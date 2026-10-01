"""
SMC structural engine (read-only, deterministic, causal).

The existing analyser (engine.py) and its score are NOT changed. This module adds a structural view on top of the
same bars that answers "is there ONE coherent SMC setup?" instead of counting BOS, FVG, OB and sweep as separate
points:

    LIQUIDITY SWEEP  ->  BOS / CHoCH  ->  FVG / ORDER BLOCK (from that displacement)  ->  RETEST  =  SMC SETUP

Everything is computed from CLOSED bars only, in one forward pass, and every object carries the time it became
known (a swing is known n bars after its extreme; an FVG when its third candle closes; an OB when the structure
break that created it closes). Replaying the same bars up to any time t gives exactly what was visible live at t,
so the same function serves LIVE (Hub.smc) and history / backtest (Hub.read_at -> Read.smc).

Output of analyze(bars_by_tf, cfg):
  structure       swings labelled HH / HL / LH / LL, BOS / CHoCH events {timestamp, price, direction, timeframe,
                  event_type, strength 0..1, source}, direction, strength 0..1
  fvgs            {timeframe, direction, upper_price, lower_price, created_at, status FRESH | PARTIALLY_FILLED |
                  MITIGATED | INVALIDATED, fill_percentage, invalidated}; one per zone (overlaps are not duplicated)
  order_blocks    {direction, timeframe, zone_high, zone_low, created_at, source_structure_event, strength,
                  mitigation_status FRESH | MITIGATED | INVALIDATED, invalidated}; at most one per structure event
  liquidity       levels (EQH / EQL, PDH / PDL, previous-session high / low, swing highs / lows, each internal or
                  external to the dealing range) and sweeps {level, type, direction, rejection, reaction, confirmed}.
                  A sweep = wick beyond the level + CLOSE back inside (rejection); a close beyond is a BREAKOUT.
  dealing_range   {source htf_swing | prev_day | session | custom, high, low, equilibrium, position, zone
                  DISCOUNT | EQUILIBRIUM | PREMIUM}
  setups          BUY / SELL: {direction, stage 0-4, status, quality NONE | LOW | MEDIUM | HIGH, confidence 0..1,
                  invalidation, entry_zone, components, setup_id}; `setup` = the better valid one (or None)
Direction convention: +1 = bullish (BUY), -1 = bearish (SELL). A sweep of sell-side liquidity (below lows) is +1.
"""
from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from datetime import datetime, timezone

from engine import TF_SECONDS

DEFAULTS = dict(
    tf="M15",                   # setup timeframe
    htf="H1",                   # dealing range timeframe (htf_swing)
    window=300,                 # bars analysed (the most recent closed ones)
    swing_n=2,                  # fractal swing: n bars left and right
    atr_len=14,
    # FVG
    fvg_min_atr=0.15,           # minimum gap size (x ATR)
    fvg_min_displacement=0.8,   # middle candle body >= x ATR (displacement)
    fvg_max_age=96,             # bars; older FVGs expire
    fvg_partial=0.5,            # fill >= this fraction -> MITIGATED (below: PARTIALLY_FILLED)
    fvg_invalidation="close",   # close | wick beyond the far edge -> INVALIDATED
    # Order blocks
    ob_lookback=12,             # bars before the break searched for the last opposite candle
    ob_max_age=96,
    ob_invalidation="close",    # close | wick
    # Liquidity
    eq_tol_atr=0.10,            # equal highs / lows tolerance (x ATR)
    sweep_reaction_bars=12,     # structure break in the sweep direction within this many bars = reaction
    sweep_require_reaction=True,
    # Premium / discount
    range_source="htf_swing",   # htf_swing | prev_day | session | custom
    custom_high=0.0,
    custom_low=0.0,
    eq_band=0.05,               # +-5 % around the middle = EQUILIBRIUM
    # Setup
    require_sweep=True,         # the chain must start with a liquidity sweep
    setup_max_age=48,           # bars from the sweep / break to now
    zone_after_break=3,         # FVG may form up to n bars after the break (same displacement leg)
)
SESSIONS_UTC = (("ASIA", 0, 9), ("LONDON", 7, 16), ("NEW_YORK", 12, 21))
_CACHE: OrderedDict = OrderedDict()
import os  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(HERE, "smc_settings.json")


def load_settings():
    try:
        with open(SETTINGS_FILE) as f:
            return clean_cfg(json.load(f))
    except (OSError, ValueError):
        return clean_cfg(None)


def save_settings(new):
    cfg = clean_cfg(dict(load_settings(), **(new or {})))
    try:
        with open(SETTINGS_FILE, "w") as f:
            json.dump(cfg, f, indent=2)
    except OSError:
        pass
    return cfg


def clean_cfg(cfg):
    out = dict(DEFAULTS)
    for k, v in (cfg or {}).items():
        if k not in DEFAULTS:
            continue
        d = DEFAULTS[k]
        try:
            if isinstance(d, bool):
                v = bool(v)
            elif isinstance(d, int):
                v = int(v)
            elif isinstance(d, float):
                v = float(v)
            else:
                v = str(v)
        except (TypeError, ValueError):
            continue
        out[k] = v
    if out["tf"] not in TF_SECONDS:
        out["tf"] = DEFAULTS["tf"]
    if out["htf"] not in TF_SECONDS:
        out["htf"] = DEFAULTS["htf"]
    if out["range_source"] not in ("htf_swing", "prev_day", "session", "custom"):
        out["range_source"] = DEFAULTS["range_source"]
    out["swing_n"] = max(1, min(6, out["swing_n"]))
    return out


# ---------------------------------------------------------------------------------------------- helpers
def atr_series(bars, n=14):
    """Causal ATR: value at i uses bars <= i."""
    out, trs = [], []
    for i, b in enumerate(bars):
        tr = b.h - b.l if i == 0 else max(b.h - b.l, abs(b.h - bars[i - 1].c), abs(b.l - bars[i - 1].c))
        trs.append(tr)
        k = min(n, len(trs))
        out.append(sum(trs[-k:]) / k)
    return out


def _id(*parts):
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:12]


def _clip(x, a=0.0, b=1.0):
    return max(a, min(b, x))


# ---------------------------------------------------------------------------------------------- structure
def structure(bars, tf, n=2, atr=None):
    """Swings (HH/HL/LH/LL) and BOS / CHoCH on candle CLOSE, in one causal pass."""
    atr = atr or atr_series(bars)
    swings, events = [], []
    trend = 0
    act_h = act_l = None                      # active (unbroken) swing high / low
    last_h = last_l = None                    # previous swing (for labels)
    L = len(bars)
    for i in range(L):
        j = i - n
        if j >= n:
            hj, lj = bars[j].h, bars[j].l
            left, right = bars[j - n:j], bars[j + 1:j + n + 1]
            if hj > max(b.h for b in left) and hj >= max(b.h for b in right):
                lab = "H" if last_h is None else ("HH" if hj > last_h["price"] else "LH")
                sw = dict(kind="high", label=lab, price=hj, t=bars[j].t, confirmed_at=bars[i].t, i=j,
                          broken=False, swept=False)
                swings.append(sw)
                last_h = act_h = sw
            if lj < min(b.l for b in left) and lj <= min(b.l for b in right):
                lab = "L" if last_l is None else ("HL" if lj > last_l["price"] else "LL")
                sw = dict(kind="low", label=lab, price=lj, t=bars[j].t, confirmed_at=bars[i].t, i=j,
                          broken=False, swept=False)
                swings.append(sw)
                last_l = act_l = sw
        b = bars[i]
        a = atr[i] or 1e-9
        for sw, d in ((act_h, 1), (act_l, -1)):
            if sw is None or sw["broken"] or b.t <= sw["t"]:
                continue
            if (d == 1 and b.c > sw["price"]) or (d == -1 and b.c < sw["price"]):
                kind = "BOS" if trend == d else "CHoCH"
                trend = d
                sw["broken"] = True
                body = abs(b.c - b.o)
                strength = _clip(0.5 * min(1.0, body / a) + 0.5 * min(1.0, abs(b.c - sw["price"]) / (0.5 * a)))
                events.append(dict(id=_id(tf, kind, d, b.t, round(sw["price"], 5)), timestamp=b.t,
                                   price=round(sw["price"], 5), direction=d, timeframe=tf, event_type=kind,
                                   strength=round(strength, 2), source="smc_engine", i=i, swing_t=sw["t"]))
    labels = [s["label"] for s in swings[-4:]]
    if trend == 1:
        agree = sum(1 for x in labels if x in ("HH", "HL"))
    elif trend == -1:
        agree = sum(1 for x in labels if x in ("LH", "LL"))
    else:
        agree = 0
    strength = round(agree / len(labels), 2) if labels and trend else 0.0
    return dict(direction=trend, strength=strength, swings=swings, events=events,
                last_event=events[-1] if events else None)


# ---------------------------------------------------------------------------------------------- FVG
def fvgs(bars, tf, cfg, atr=None):
    """3-candle imbalances with fill tracking. created_at = close of the third candle."""
    atr = atr or atr_series(bars, cfg["atr_len"])
    out = []
    for i in range(len(bars)):
        b = bars[i]
        # update existing zones with this bar (only bars AFTER creation)
        for z in out:
            if z["status"] in ("INVALIDATED", "EXPIRED") or i <= z["i"]:
                continue
            if i - z["i"] > cfg["fvg_max_age"]:
                z["status"] = "EXPIRED"
                continue
            size = z["upper_price"] - z["lower_price"]
            if z["direction"] == 1:
                pen = z["upper_price"] - b.l
                beyond = (b.c < z["lower_price"]) if cfg["fvg_invalidation"] == "close" else (b.l < z["lower_price"])
            else:
                pen = b.h - z["lower_price"]
                beyond = (b.c > z["upper_price"]) if cfg["fvg_invalidation"] == "close" else (b.h > z["upper_price"])
            fill = _clip(pen / size) if size > 0 else 1.0
            z["fill_percentage"] = round(max(z["fill_percentage"], fill * 100), 1)
            if beyond:
                z["status"], z["invalidated"], z["invalidated_at"] = "INVALIDATED", True, b.t
            elif z["fill_percentage"] >= cfg["fvg_partial"] * 100:
                if z["status"] != "MITIGATED":
                    z["mitigated_at"] = b.t
                z["status"] = "MITIGATED"
            elif z["fill_percentage"] > 0:
                z["status"] = "PARTIALLY_FILLED"
        if i < 2:
            continue
        a0, mid, c = bars[i - 2], bars[i - 1], bars[i]
        at = atr[i] or 1e-9
        if abs(mid.c - mid.o) < cfg["fvg_min_displacement"] * at:
            continue
        for d, lo, hi in ((1, a0.h, c.l), (-1, c.h, a0.l)):
            if hi - lo <= cfg["fvg_min_atr"] * at or hi <= lo:
                continue
            dup = any(z["direction"] == d and z["status"] not in ("INVALIDATED", "EXPIRED")
                      and z["lower_price"] < hi and lo < z["upper_price"] for z in out)
            if dup:
                continue                          # same zone already tracked: no duplicate signal
            out.append(dict(id=_id(tf, "FVG", d, c.t), timeframe=tf, direction=d, upper_price=round(hi, 5),
                            lower_price=round(lo, 5), created_at=c.t, i=i, status="FRESH", fill_percentage=0.0,
                            invalidated=False, displacement=round(abs(mid.c - mid.o) / at, 2)))
    return out


# ---------------------------------------------------------------------------------------------- order blocks
def order_blocks(bars, tf, events, cfg):
    """One OB per structure event: the last opposite candle before the breaking bar."""
    obs = []
    for e in events:
        i, d = e["i"], e["direction"]
        cand = None
        for k in range(i - 1, max(-1, i - 1 - cfg["ob_lookback"]), -1):
            bk = bars[k]
            if (d == 1 and bk.c < bk.o) or (d == -1 and bk.c > bk.o):
                cand = k
                break
        if cand is None:
            continue
        bk = bars[cand]
        obs.append(dict(id=_id(tf, "OB", e["id"]), direction=d, timeframe=tf, zone_high=round(bk.h, 5),
                        zone_low=round(bk.l, 5), created_at=e["timestamp"], i=i, candle_t=bk.t,
                        source_structure_event=e["id"], source_event_type=e["event_type"], strength=e["strength"],
                        mitigation_status="FRESH", invalidated=False))
    for ob in obs:
        for k in range(ob["i"] + 1, len(bars)):
            b = bars[k]
            if k - ob["i"] > cfg["ob_max_age"]:
                if ob["mitigation_status"] == "FRESH":
                    ob["mitigation_status"] = "EXPIRED"
                break
            if ob["direction"] == 1:
                beyond = (b.c < ob["zone_low"]) if cfg["ob_invalidation"] == "close" else (b.l < ob["zone_low"])
                touch = b.l <= ob["zone_high"]
            else:
                beyond = (b.c > ob["zone_high"]) if cfg["ob_invalidation"] == "close" else (b.h > ob["zone_high"])
                touch = b.h >= ob["zone_low"]
            if beyond:
                ob["mitigation_status"], ob["invalidated"], ob["invalidated_at"] = "INVALIDATED", True, b.t
                break
            if touch and ob["mitigation_status"] == "FRESH":
                ob["mitigation_status"], ob["mitigated_at"] = "MITIGATED", b.t
    return obs


# ---------------------------------------------------------------------------------------------- dealing range
def _prev_session(bars, utc_offset):
    """High / low of the most recently COMPLETED session window (UTC hours, SESSIONS_UTC)."""
    if not bars:
        return None
    sec = bars[-1].t - bars[-2].t if len(bars) > 1 else 900
    end_utc = bars[-1].t + sec - utc_offset * 3600
    best = None
    for name, a, b in SESSIONS_UTC:
        day0 = end_utc - end_utc % 86400
        for back in range(0, 3):
            s = day0 - back * 86400 + a * 3600
            e = day0 - back * 86400 + b * 3600
            if e <= end_utc:
                if best is None or e > best[2]:
                    best = (name, s, e)
                break
    if best is None:
        return None
    name, s, e = best
    sel = [x for x in bars if s <= x.t - utc_offset * 3600 < e]
    if not sel:
        return None
    return dict(name=name, high=max(x.h for x in sel), low=min(x.l for x in sel), end=e + utc_offset * 3600)


def dealing_range(cfg, price, htf_struct=None, d1=None, tf_bars=None, utc_offset=0):
    src = cfg["range_source"]
    hi = lo = None
    note = ""
    if src == "htf_swing" and htf_struct:
        hs = [s for s in htf_struct["swings"] if s["kind"] == "high"]
        ls = [s for s in htf_struct["swings"] if s["kind"] == "low"]
        if hs and ls:
            hi, lo = hs[-1]["price"], ls[-1]["price"]
            note = f"last confirmed H1 swings ({hs[-1]['label']} / {ls[-1]['label']})"
    elif src == "prev_day" and d1:
        hi, lo = d1[-1].h, d1[-1].l          # d1 = closed D1 bars -> the last one is the previous day
        note = "previous day"
    elif src == "session":
        ps = _prev_session(tf_bars or [], utc_offset)
        if ps:
            hi, lo, note = ps["high"], ps["low"], f"previous {ps['name']} session"
    elif src == "custom":
        hi, lo, note = cfg["custom_high"] or None, cfg["custom_low"] or None, "custom"
    if hi is None or lo is None or hi <= lo:
        return dict(source=src, high=None, low=None, equilibrium=None, position=None, zone="UNKNOWN",
                    note=note or "range not available")
    eq = (hi + lo) / 2
    pos = (price - lo) / (hi - lo) if price is not None else None
    band = cfg["eq_band"]
    zone = ("UNKNOWN" if pos is None else "DISCOUNT" if pos < 0.5 - band else "PREMIUM" if pos > 0.5 + band
            else "EQUILIBRIUM")
    return dict(source=src, high=round(hi, 5), low=round(lo, 5), equilibrium=round(eq, 5),
                position=None if pos is None else round(pos, 3), zone=zone, note=note)


# ---------------------------------------------------------------------------------------------- liquidity
def liquidity(bars, tf, st, cfg, atr, d1=None, utc_offset=0, rng=None):
    levels = []
    tol_i = lambda i: cfg["eq_tol_atr"] * (atr[i] if i < len(atr) else atr[-1])
    highs = [s for s in st["swings"] if s["kind"] == "high"]
    lows = [s for s in st["swings"] if s["kind"] == "low"]
    for group, kind, typ in ((highs, 1, "EQH"), (lows, -1, "EQL")):
        for a_, b_ in zip(group, group[1:]):
            idx = next((k for k in range(len(bars)) if bars[k].t == b_["confirmed_at"]), len(bars) - 1)
            if abs(a_["price"] - b_["price"]) <= tol_i(idx):
                lvl = max(a_["price"], b_["price"]) if kind == 1 else min(a_["price"], b_["price"])
                levels.append(dict(type=typ, price=round(lvl, 5), side=kind, known_at=b_["confirmed_at"]))
    for s in highs[-6:]:
        levels.append(dict(type="SWING_H", price=s["price"], side=1, known_at=s["confirmed_at"]))
    for s in lows[-6:]:
        levels.append(dict(type="SWING_L", price=s["price"], side=-1, known_at=s["confirmed_at"]))
    if d1:
        pd_ = d1[-1]
        known = pd_.t + 86400
        levels.append(dict(type="PDH", price=pd_.h, side=1, known_at=known))
        levels.append(dict(type="PDL", price=pd_.l, side=-1, known_at=known))
    ps = _prev_session(bars, utc_offset)
    if ps:
        levels.append(dict(type=f"{ps['name']}_H", price=ps["high"], side=1, known_at=ps["end"]))
        levels.append(dict(type=f"{ps['name']}_L", price=ps["low"], side=-1, known_at=ps["end"]))
    if rng and rng.get("high") is not None:
        for lv in levels:
            lv["scope"] = ("EXTERNAL" if lv["price"] >= rng["high"] - 1e-9 or lv["price"] <= rng["low"] + 1e-9
                           else "INTERNAL")
    # sweeps: first interaction of each level after it became known
    sweeps = []
    ev = st["events"]
    for lv in levels:
        start = next((k for k in range(len(bars)) if bars[k].t >= lv["known_at"]), None)
        if start is None:
            continue
        for k in range(start, len(bars)):
            b = bars[k]
            if lv["side"] == 1 and b.h > lv["price"]:
                if b.c < lv["price"]:
                    sweeps.append(_sweep(lv, b, k, -1, ev, cfg))
                else:
                    lv["broken_at"] = b.t                      # close beyond = breakout, not a sweep
                break
            if lv["side"] == -1 and b.l < lv["price"]:
                if b.c > lv["price"]:
                    sweeps.append(_sweep(lv, b, k, 1, ev, cfg))
                else:
                    lv["broken_at"] = b.t
                break
    # the same bar sweeping several levels = one sweep event (keep the most significant level)
    rank = {"PDH": 0, "PDL": 0, "EQH": 1, "EQL": 1}
    uniq = {}
    for s_ in sweeps:
        key = (s_["i"], s_["direction"])
        if key not in uniq or rank.get(s_["type"], 5) < rank.get(uniq[key]["type"], 5):
            uniq[key] = s_
    sweeps = sorted(uniq.values(), key=lambda x: x["i"])
    return dict(levels=levels, sweeps=sweeps)


def _sweep(lv, b, k, d, events, cfg):
    react = next((e for e in events if e["direction"] == d and k < e["i"] <= k + cfg["sweep_reaction_bars"]), None)
    return dict(id=_id(lv["type"], lv["price"], b.t), type=lv["type"], level=lv["price"], direction=d, t=b.t, i=k,
                extreme=b.l if d == 1 else b.h, rejection=True, reaction=react is not None,
                reaction_event=react["id"] if react else None,
                confirmed=react is not None or not cfg["sweep_require_reaction"])


# ---------------------------------------------------------------------------------------------- setup
def _setup(d, bars, tf, st, fv, obs, liq, rng, cfg, atr):
    n = len(bars)
    none = dict(direction=d, stage=0, status="NONE", quality="NONE", confidence=0.0, invalidation=None,
                entry_zone=None, components={}, setup_id=None)
    last_i = n - 1
    sw = None
    if cfg["require_sweep"]:
        cands = [s for s in liq["sweeps"] if s["direction"] == d and s["confirmed"]
                 and last_i - s["i"] <= cfg["setup_max_age"]]
        if not cands:
            return none
        sw = cands[-1]
        brk = next((e for e in st["events"] if e["direction"] == d and sw["i"] < e["i"]
                    <= sw["i"] + cfg["sweep_reaction_bars"]), None)
        start_i = sw["i"]
    else:
        brk = next((e for e in reversed(st["events"]) if e["direction"] == d
                    and last_i - e["i"] <= cfg["setup_max_age"]), None)
        cands = [s for s in liq["sweeps"] if s["direction"] == d and brk and s["i"] < brk["i"]
                 and brk["i"] - s["i"] <= cfg["sweep_reaction_bars"]]
        sw = cands[-1] if cands else None
        start_i = sw["i"] if sw else (brk["i"] - cfg["ob_lookback"] if brk else 0)
    comp = dict(sweep=sw is not None, break_=brk is not None, fvg=False, ob=False, retest=False, location=None)
    stage = 1 if sw else 0
    inv = sw["extreme"] if sw else None
    zone = None
    fz = oz = None
    if brk:
        stage = 2
        fz_c = [z for z in fv if z["direction"] == d and start_i <= z["i"] <= brk["i"] + cfg["zone_after_break"]
                and z["status"] != "EXPIRED"]
        fz = fz_c[-1] if fz_c else None
        oz = next((o for o in obs if o["source_structure_event"] == brk["id"]), None)
        comp["fvg"], comp["ob"] = fz is not None, oz is not None
        if inv is None:
            inv = oz["zone_low"] if (oz and d == 1) else oz["zone_high"] if oz else None
        if fz or oz:
            stage = 3
            if fz:
                zone = [fz["lower_price"], fz["upper_price"]]
            if oz:
                zone = [oz["zone_low"], oz["zone_high"]] if zone is None else [min(zone[0], oz["zone_low"]),
                                                                                 max(zone[1], oz["zone_high"])]
            retest = (fz and fz["status"] in ("PARTIALLY_FILLED", "MITIGATED")) or \
                     (oz and oz["mitigation_status"] == "MITIGATED")
            if retest:
                stage = 4
                comp["retest"] = True
    # invalidation: a close beyond the sweep extreme (or the OB far edge) after the chain started
    invalid = False
    if inv is not None:
        for k in range(start_i + 1, n):
            if (d == 1 and bars[k].c < inv) or (d == -1 and bars[k].c > inv):
                invalid = True
                break
    if fz and fz["status"] == "INVALIDATED" and not (oz and oz["mitigation_status"] != "INVALIDATED"):
        invalid = True
    if rng and rng.get("zone") not in (None, "UNKNOWN"):
        comp["location"] = (rng["zone"] == "DISCOUNT") if d == 1 else (rng["zone"] == "PREMIUM")
    if stage < 3:
        return dict(none, stage=stage, status="FORMING" if stage else "NONE", components=comp,
                    invalidation=inv, sweep=sw, structure_event=brk)
    conf = 0.25 * bool(sw) + 0.25 + 0.20 + 0.15 * comp["retest"] + 0.10 * bool(comp["location"]) + \
        0.05 * (comp["fvg"] and comp["ob"])
    conf *= 0.7 + 0.3 * (brk["strength"] if brk else 0)
    conf = round(_clip(conf), 2)
    status = "INVALIDATED" if invalid else "RETESTED" if stage == 4 else "WAITING_RETEST"
    if invalid:
        conf = 0.0
    quality = "NONE" if invalid else "HIGH" if conf >= 0.75 else "MEDIUM" if conf >= 0.55 else "LOW"
    sid = _id(tf, d, sw["t"] if sw else "-", brk["timestamp"], zone)
    return dict(direction=d, stage=stage, status=status, quality=quality, confidence=conf,
                invalidation=None if inv is None else round(inv, 5),
                entry_zone=None if zone is None else [round(zone[0], 5), round(zone[1], 5)],
                components=comp, setup_id=sid, sweep=sw, structure_event=brk,
                fvg=fz["id"] if fz else None, ob=oz["id"] if oz else None,
                created_at=brk["timestamp"])


# ---------------------------------------------------------------------------------------------- main
def analyze(bars_by_tf, cfg=None, utc_offset=0, price=None):
    """bars_by_tf: {tf: CLOSED bars}. Returns the structural view as of the last closed bar of cfg['tf']."""
    c = clean_cfg(cfg)
    tf = c["tf"]
    all_bars = bars_by_tf.get(tf) or []
    bars = all_bars[-c["window"]:]
    if len(bars) < 4 * c["swing_n"] + 10:
        return dict(tf=tf, ok=False, reason=f"not enough {tf} bars", setup=None, setups={},
                    structure=dict(direction=0, strength=0.0, swings=[], events=[], last_event=None),
                    fvgs=[], order_blocks=[], liquidity=dict(levels=[], sweeps=[]),
                    dealing_range=dict(zone="UNKNOWN", source=c["range_source"]))
    htf = bars_by_tf.get(c["htf"]) or []
    d1 = bars_by_tf.get("D1") or []
    key = (tf, bars[-1].t, len(bars), htf[-1].t if htf else 0, d1[-1].t if d1 else 0,
           json.dumps(c, sort_keys=True), utc_offset, price)
    hit = _CACHE.get(key)
    if hit is not None:
        return hit
    atr = atr_series(bars, c["atr_len"])
    st = structure(bars, tf, c["swing_n"], atr)
    htf_st = structure(htf[-c["window"]:], c["htf"], max(2, c["swing_n"] + 1)) if len(htf) > 20 else None
    px = price if price is not None else bars[-1].c
    rng = dealing_range(c, px, htf_st, d1, bars, utc_offset)
    fv = fvgs(bars, tf, c, atr)
    obs = order_blocks(bars, tf, st["events"], c)
    liq = liquidity(bars, tf, st, c, atr, d1, utc_offset, rng)
    setups = {"BUY": _setup(1, bars, tf, st, fv, obs, liq, rng, c, atr),
              "SELL": _setup(-1, bars, tf, st, fv, obs, liq, rng, c, atr)}
    valid = [s for s in setups.values() if s["status"] in ("WAITING_RETEST", "RETESTED")]
    best = max(valid, key=lambda s: (s["confidence"], s.get("created_at") or 0)) if valid else None
    active_f = [z for z in fv if z["status"] not in ("EXPIRED",)][-12:]
    active_o = [o for o in obs if o["mitigation_status"] != "EXPIRED"][-10:]
    out = dict(tf=tf, ok=True, t=bars[-1].t, price=px, atr=round(atr[-1], 5),
               structure=dict(direction=st["direction"], strength=st["strength"], swings=st["swings"][-10:],
                              events=st["events"][-10:], last_event=st["last_event"]),
               htf_structure=dict(direction=htf_st["direction"], strength=htf_st["strength"]) if htf_st else None,
               fvgs=active_f, order_blocks=active_o,
               liquidity=dict(levels=liq["levels"][-20:], sweeps=liq["sweeps"][-10:]),
               dealing_range=rng, setups=setups, setup=best, config=c)
    _CACHE[key] = out
    if len(_CACHE) > 3000:
        _CACHE.popitem(last=False)
    return out


def summary(res):
    """Short dict for the UI / journal."""
    if not res or not res.get("ok"):
        return dict(ok=False, reason=(res or {}).get("reason", "n/a"))
    s = res.get("setup")
    rng = res["dealing_range"]
    st = res["structure"]
    le = st.get("last_event")
    return dict(ok=True, tf=res["tf"], structure_dir=st["direction"], structure_strength=st["strength"],
                last_event=f"{le['event_type']} {'UP' if le['direction'] == 1 else 'DN'} @ {le['price']:.2f}" if le else None,
                zone=rng["zone"], range_source=rng["source"], range_position=rng.get("position"),
                setup=None if not s else dict(direction="BUY" if s["direction"] == 1 else "SELL", status=s["status"],
                                              quality=s["quality"], confidence=s["confidence"], stage=s["stage"],
                                              entry_zone=s["entry_zone"], invalidation=s["invalidation"],
                                              setup_id=s["setup_id"], components=s["components"]),
                fvgs_active=sum(1 for z in res["fvgs"] if z["status"] in ("FRESH", "PARTIALLY_FILLED")),
                obs_active=sum(1 for o in res["order_blocks"] if o["mitigation_status"] == "FRESH"),
                sweeps=len(res["liquidity"]["sweeps"]))


def utc_str(t):
    return datetime.fromtimestamp(t, tz=timezone.utc).strftime("%m-%d %H:%M")
