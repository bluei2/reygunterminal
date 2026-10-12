"""
FORECAST REGIME: a READ-ONLY prediction layer on top of the existing analyser.

A forecast is NOT a trade. It is a virtual, testable prediction record:

   READ MARKET -> DETECT REGIME -> BUILD FORECAST -> SAVE SNAPSHOT -> WAIT -> TRIGGER -> MONITOR
   -> TP / SL / EXPIRED / INVALIDATED -> SAVE RESULT -> STATISTICS

Safety (by construction):
  * this module imports nothing that can trade (no trading / mt5_ops / trade_gate / execution modules) and never
    touches Hub.trader; it cannot send, modify or close an order, and never talks to the execution gate
  * it only READS: the existing analyser Read (score, bias, trigger, groups - the score is never recalculated),
    the structure states, the SMC engine, regime.py, sessions / news / spread when available
  * every read is made with Hub.read_at(t) / Hub.smc_at(t) = only bars CLOSED at the forecast time (no look-ahead);
    live and historical replay go through exactly the same code
  * its records live in their own journal (forecast_journal.jsonl), never in the trade journal

Regime states: NO_DATA, NO_BIAS, RANGING, TREND_BULL/BEAR, PULLBACK_BULL/BEAR, BREAKOUT_BULL/BEAR,
REVERSAL_BULL/BEAR (market) and WAITING_LONG/SHORT, TRIGGERED_*, RUNNING_*, TP_HIT, SL_HIT, EXPIRED, INVALIDATED
(forecast lifecycle).

Outcome rules (documented, conservative):
  * a forecast only counts after its ENTRY triggered (LIMIT: price enters the zone; MARKET: at creation;
    BREAKOUT: a bar CLOSES beyond the trigger level, or touches it with confirm="touch")
  * after the trigger, CLOSED bars (and live ticks, in their real order) decide: SL first vs the final target
    (TP2 by default = the configured RR). TP1 / TP3 touches are recorded as milestones.
  * on the trigger bar only the stop is checked (a target on the same bar cannot be ordered reliably)
  * SL and target touched in the SAME bar without tick order = AMBIGUOUS_BAR -> rule "sl_first" (default),
    "tp_first", or "exclude" (not counted in win rate)
  * not triggered + setup destroyed = INVALIDATED (never SL); not triggered in time = EXPIRED
  * triggered but neither SL nor target within running_timeout bars = EXPIRED (triggered), R marked at the close
"""
from __future__ import annotations

import bisect
import hashlib
import json
import os
import threading
import time
from collections import deque
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
JOURNAL_FILE = os.path.join(HERE, "forecast_journal.jsonl")
SETTINGS_FILE = os.path.join(HERE, "forecast_settings.json")
PROFILE_FILE = os.path.join(HERE, "forecast_profile.json")
TFS = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400, "D1": 86400}
REGIME_KINDS = ("TREND", "PULLBACK", "BREAKOUT", "REVERSAL", "RANGE")
FINAL = ("TP_HIT", "SL_HIT", "EXPIRED", "INVALIDATED", "AMBIGUOUS")
ACTIVE = ("WAITING", "TRIGGERED", "RUNNING")
QUALITY_ORDER = ["NO SETUP", "C", "B", "A", "A+"]
INVALIDATION_CODES = ("HTF_BIAS_FLIPPED", "STRUCTURE_BROKEN", "ENTRY_ZONE_INVALIDATED", "OPPOSITE_CHOCH",
                      "NEWS_INVALIDATION", "TIME_EXPIRY", "MANUAL_CANCEL", "SETUP_REPLACED", "MISSED_NO_PULLBACK",
                      "OCO_CANCELLED")
RESULT_TEXT = {"TP_HIT": "Forecast reached target.", "SL_HIT": "Forecast reached structural invalidation level.",
               "EXPIRED": "Entry was never triggered before expiry.",
               "EXPIRED_RUNNING": "Triggered, but neither SL nor target within the running timeout.",
               "INVALIDATED": "Setup structure changed before entry.",
               "AMBIGUOUS": "SL and target touched in the same bar; excluded (ambiguous rule = exclude)."}

DEFAULTS = dict(
    enabled=True,
    tf="M5",                    # forecast bar timeframe: created on each new CLOSED bar of this TF, monitored on it
    htf="H4", bias_tf="H1", setup_tf="M15", trigger_tf="M5", refine_tf="M1",
    min_score=0.0,              # 0 = the analyser's own passing score
    min_confidence=50.0,
    min_quality="C",
    allowed_regimes=["TREND", "PULLBACK", "BREAKOUT", "REVERSAL"],
    entry_mode="auto",          # auto | market | limit | breakout
    breakout_confirm="close",   # close | touch
    rr=2.0,                     # TP2 = rr x risk (independent from trading SL/TP settings)
    final_target="tp2",         # tp1 | tp2 | tp3: which target finishes a forecast as TP_HIT
    expiry_bars=50,             # WAITING -> EXPIRED after this many forecast bars
    running_timeout_bars=100,   # TRIGGERED -> EXPIRED (R at close) after this many bars
    ambiguous_rule="sl_first",  # sl_first | tp_first | exclude
    one_per_direction=True,     # one active forecast per symbol + direction (False: one per symbol)
    max_active=1,               # when one_per_direction is False: max active forecasts per symbol
    draw=True,
    sessions=[],                # optional forecast-only session filter (Asia / London / New York), [] = any
    news_filter=False,          # optional: no new forecast inside the news window
    max_spread=0.0,             # optional forecast-only spread filter (0 = off)
    min_atr=0.0, min_adx=0.0,   # optional
    invalidate_on_news=False,
    sl_buffer_atr=0.10, min_sl_atr=0.40, max_sl_atr=3.0, atr_sl=1.5,
    calibration_min_samples=30,
    # setup types compared side by side (each with its own active forecast per direction)
    setups=["structure", "struct_poc", "vp_poc", "vp_edge", "rev_ok", "rev_any", "router",
            "st_trend", "st_pullback", "st_conso"],
    vp_min_rr=1.5,              # volume-profile setups: skip when the target is too close
    vp_bias_filter=True,        # volume-profile setups only in the HTF bias direction
    vp_tol_atr=0.10,            # "touched the level" tolerance (x ATR)
    sp_max_dist_atr=3.0,        # structure + POC pullback: pPOC at most this many ATR away from price
    rev_bias_filter=True,       # reversal arrows: only in the HTF bias direction (end of a pullback)
    rev_min_score=60,           # reversal arrows: minimum reversal score
    router_trend_rr=2.0,        # REGIME ROUTER: trend regime -> structure forecast at this RR
    router_range_rr=1.0,        #                range regime -> reversal arrow (R✓) at this RR
)
SETUP_TYPES = {"structure": "Structure (trend / pullback / breakout / reversal)",
               "struct_poc": "Structure bias, entry on the pullback to pPOC (LIMIT) -> structure swing",
               "vp_poc": "VP: pPOC rejection -> pVAH / pVAL",
               "rev_ok": "Reversal arrow R✓ (confirmed), with the HTF bias",
               "rev_any": "Reversal arrow R? or R✓ (on the arrow bar), with the HTF bias",
               "router": "Regime router: TREND -> structure 1:2 · RANGE -> reversal arrow R✓ 1:1 · else none",
               "st_trend": "Strategy TREND: its own indicator set, market entry with the trend (Trading page)",
               "st_pullback": "Strategy PULLBACK: dip against the trend that turns back (Trading page)",
               "st_conso": "Strategy CONSOLIDATION: BUY STOP / SELL STOP around the box (Trading page)",
               "vp_edge": "VP: pVAH / pVAL rejection -> pPOC"}


def clean_settings(raw):
    s = json.loads(json.dumps(DEFAULTS))
    for k, v in (raw or {}).items():
        if k not in DEFAULTS:
            continue
        d = DEFAULTS[k]
        try:
            if isinstance(d, bool):
                s[k] = bool(v)
            elif isinstance(d, (int, float)) and not isinstance(d, bool):
                x = type(d)(float(v))
                if x < 0:
                    continue
                s[k] = x
            elif isinstance(d, list):
                s[k] = [str(x) for x in v] if isinstance(v, list) else d
            else:
                s[k] = str(v)
        except (TypeError, ValueError):
            continue
    for k, ok in (("entry_mode", ("auto", "market", "limit", "breakout")), ("breakout_confirm", ("close", "touch")),
                  ("final_target", ("tp1", "tp2", "tp3")), ("ambiguous_rule", ("sl_first", "tp_first", "exclude")),
                  ("min_quality", tuple(QUALITY_ORDER[1:]))):
        if s[k] not in ok:
            s[k] = DEFAULTS[k]
    for k in ("tf", "htf", "bias_tf", "setup_tf", "trigger_tf", "refine_tf"):
        if s[k] not in TFS:
            s[k] = DEFAULTS[k]
    s["allowed_regimes"] = [x for x in s["allowed_regimes"] if x in REGIME_KINDS]
    s["setups"] = [x for x in s["setups"] if x in SETUP_TYPES]
    s["rr"] = max(0.5, s["rr"])
    s["expiry_bars"] = max(1, int(s["expiry_bars"]))
    s["running_timeout_bars"] = max(1, int(s["running_timeout_bars"]))
    return s


def load_settings(path=None):
    try:
        with open(path or SETTINGS_FILE) as f:
            return clean_settings(json.load(f))
    except (OSError, ValueError):
        return clean_settings(None)


W = {1: "BULL", -1: "BEAR", 0: "FLAT"}
DIRW = {1: "LONG", -1: "SHORT", 0: "NEUTRAL"}


# ============================================================================ market view (read-only)
def cut(bars, t_end, sec):
    """Bars CLOSED by t_end (bar.t + sec <= t_end)."""
    k = bisect.bisect_right([b.t for b in bars], t_end - sec)
    return bars[:k]


def _vp_at(hub, t_end):
    """The PREVIOUS day's volume profile as of t_end, from the Volume Profile plugin of this hub (its own settings;
    no second implementation). None when the plugin is missing or there is not enough history."""
    p = next((x for x in getattr(hub, "plugins", []) if x.key == "volume_profile.VolumeProfile"), None)
    if p is None:
        return None
    g = type(p.obj).vote.__globals__                  # the plugin module's own functions (loaded by path)
    pdp = g.get("prev_day_profile")
    if pdp is None:
        return None
    day = (t_end - 1) // 86400
    # cached ON the hub (not by id(hub): a new hub can get the id of a freed one and read its stale profile)
    cache = hub.__dict__.setdefault("_vp_cache", {}) if hasattr(hub, "__dict__") else _VP_CACHE
    key = (day, json.dumps(p.obj.params, sort_keys=True))
    if key in cache:                                   # yesterday's profile does not change during today
        return cache[key]
    out = None
    for tf in ("M5", "M15", "M30", "H1"):
        b = cut(hub.src.bars(tf), t_end, TFS[tf])
        if len(b) > 50 and b[0].t <= (day - 1) * 86400:
            rows, va, mb = p.obj._cfg(b)
            vp = pdp(b, t_end - 1, rows, va, mb, 1)
            if vp:
                out = dict(poc=vp["poc"], vah=vp["vah"], val=vp["val"], high=vp["high"], low=vp["low"],
                           day=vp["day"])
                break
    if out is not None:
        cache[key] = out
        if len(cache) > 500:
            cache.pop(next(iter(cache)))
    return out


_VP_CACHE = {}


def _reversal_at(hub, t_end, tf="M5", window=320):
    """Reversal arrows on the forecast bar that just CLOSED at t_end, with the Reversal plugin's own settings
    (the same arrows as the chart). Only bars closed by t_end are used.
      any : a reversal (R? or R✓) whose reversal bar is the bar that just closed
      ok  : a reversal CONFIRMED by the bar that just closed (R✓ appears on the confirming bar)"""
    p = next((x for x in getattr(hub, "plugins", []) if x.key == "reversal_signals.ReversalSignals"), None)
    if p is None:
        return None
    try:
        import reversal
        bars = cut(hub.src.bars(tf), t_end, TFS[tf])[-window:]
        if len(bars) < 60:
            return None
        det = reversal.Detector(bars + [bars[-1]], p.obj._det_params(),
                                utc_offset=getattr(hub, "utc_offset", 0) or 0)       # last = "forming" (never read)
        n = len(bars)
        a = det.atr[n - 1] or (bars[-1].h - bars[-1].l) or 1.0
        out = dict(any=None, ok=None, atr=a, close=bars[-1].c, t=bars[-1].t)
        best = max((x for x in (det.evaluate(n - 1, -1), det.evaluate(n - 1, 1)) if x),
                   key=lambda x: x["score"], default=None)
        if best:
            out["any"] = dict(best, entry=bars[-1].c)
        cb = int(det.p.get("confirm_bars", 3))
        for i in range(n - 2, max(0, n - 2 - cb), -1):
            for side in (-1, 1):
                ev = det.evaluate(i, side)
                if ev and ev["confirmed"] and ev["confirm_t"] == bars[-1].t:
                    if out["ok"] is None or ev["score"] > out["ok"]["score"]:
                        out["ok"] = dict(ev, entry=bars[-1].c)
        return out
    except Exception:
        return None


def market_view(hub, t_end, live_extras=None, tf="M5"):
    """Everything the forecast may read at t_end, ONLY from bars closed by then. Reuses the existing analyser
    (Hub.read_at), its structure states, the SMC engine (Hub.smc_at) and regime.py - nothing is recalculated."""
    read = hub.read_at(t_end)
    states = getattr(read, "tf_states", None) or {}
    try:
        smc = hub.smc_at(t_end)
    except Exception as e:                        # an unavailable input is reported, never invented
        smc = dict(ok=False, reason=f"{e.__class__.__name__}: {e}")
    import regime as rg
    rs = rg.load_settings()
    reg = htf_reg = None
    try:
        b1 = cut(hub.src.bars(rs["tf"]), t_end, TFS[rs["tf"]])[-400:]
        b2 = cut(hub.src.bars(rs["htf"]), t_end, TFS[rs["htf"]])[-400:]
        reg = rg.classify(b1, rs) if len(b1) > 60 else None
        htf_reg = rg.classify(b2, rs) if len(b2) > 60 else None
    except Exception as e:
        reg = htf_reg = None
        try:
            from error_log import report
            report("DATA_ERROR", "forecast.market_view", "REGIME_UNAVAILABLE", e)
        except Exception:
            pass
    from market_hours import session_code, utc_from_server
    try:
        vp = _vp_at(hub, t_end)
    except Exception as e:
        vp = None
        try:
            from error_log import report
            report("DATA_ERROR", "forecast.market_view", "VP_UNAVAILABLE", e)
        except Exception:
            pass
    recent = cut(hub.src.bars(tf), t_end, TFS.get(tf, 300))[-3:] if tf in TFS else []
    view = dict(t=int(t_end), read=read, states=states, smc=smc, regime=reg, htf_regime=htf_reg, vp=vp,
                recent=recent, tf=tf,
                utc_offset=getattr(hub, "utc_offset", 0),
                session=session_code(utc_from_server(t_end, getattr(hub, "utc_offset", 0))),
                news=None, spread=None, symbol=getattr(hub.src, "symbol", ""))
    # reversal arrows are computed only when a reversal setup asks for them (lazy: replay stays fast without them)
    view["_rev_fn"] = (lambda: _reversal_at(hub, t_end, tf)) if tf in TFS else (lambda: None)
    view["_bars_fn"] = lambda tf_: cut(hub.src.bars(tf_), t_end, TFS[tf_]) if tf_ in TFS else []
    if live_extras:
        view.update(live_extras)
    return view


def view_reversal(view):
    if "rev" not in view:
        fn = view.get("_rev_fn")
        view["rev"] = fn() if fn else None
    return view["rev"]


# ============================================================================ regime
def _recent(e, tf, t_end, nbars):
    return e is not None and e.t >= t_end - nbars * TFS.get(tf, 300)


def get_regime(view, cfg=None):
    """Market regime + direction + which timeframe said what. Never forces a direction."""
    c = cfg or DEFAULTS
    read, st = view.get("read"), view.get("states") or {}
    t = view["t"]
    h4, h1, m15, m5 = (st.get(k) for k in (c["htf"], c["bias_tf"], c["setup_tf"], c["trigger_tf"]))
    tfr = {}
    if read is None or not (h4 and h1 and m15 and m5) or not getattr(read, "groups", None) and read.overall is None:
        return dict(regime="NO_DATA", kind=None, direction=0, tfs=tfr, reasons=["not enough market data"], risks=[])
    e15, e5 = m15.last(), m5.last()
    tfr[c["htf"]] = W.get(h4.trend, "FLAT")
    tfr[c["bias_tf"]] = W.get(h1.trend, "FLAT")
    tfr[c["setup_tf"]] = W.get(m15.trend, "FLAT") + (f" / {e15.kind}" if e15 and _recent(e15, m15.tf, t, 8) else "")
    tfr[c["trigger_tf"]] = (f"{e5.kind} {'UP' if e5.direction == 1 else 'DN'}" if e5 and _recent(e5, m5.tf, t, 24)
                            else W.get(m5.trend, "FLAT"))
    tfr[c["refine_tf"]] = "REFINEMENT"
    bias = read.bias
    reasons, risks = [], []
    rl = (view.get("regime") or {}).get("label")
    hl = (view.get("htf_regime") or {}).get("label")
    if not bias or (h4.trend and h1.trend and h4.trend != h1.trend and not (e15 and e15.kind == "CHoCH")):
        return dict(regime="NO_BIAS", kind=None, direction=0, tfs=tfr,
                    reasons=[f"{c['htf']} {W.get(h4.trend)} vs {c['bias_tf']} {W.get(h1.trend)}: no clear bias"],
                    risks=[])
    word = "BULL" if bias == 1 else "BEAR"
    smc = view.get("smc") or {}
    sl_ev = smc.get("structure", {}).get("last_event") if smc.get("ok") else None
    # REVERSAL: the setup TF broke against the major TF with a CHoCH, confirmed by the bias TF
    if e15 and e15.kind == "CHoCH" and _recent(e15, m15.tf, t, 8) and h4.trend and e15.direction == -h4.trend \
            and h1.trend == e15.direction:
        d = e15.direction
        return dict(regime=f"REVERSAL_{W[d]}", kind="REVERSAL", direction=d, tfs=tfr,
                    reasons=[f"{m15.tf} CHoCH {'UP' if d == 1 else 'DN'} against {c['htf']} {W[h4.trend]}",
                             f"{c['bias_tf']} already {W[d]}"], risks=[f"{c['htf']} still {W[h4.trend]} (counter-trend)"])
    # RANGE
    if rl in ("RANGE", "QUIET") and not (e15 and _recent(e15, m15.tf, t, 4) and e15.direction == bias):
        return dict(regime="RANGING", kind="RANGE", direction=0, tfs=tfr,
                    reasons=[f"regime {rl}: no valid breakout"], risks=[])
    # BREAKOUT: expansion out of a higher-TF range, with a fresh strong break in the bias direction
    if hl in ("RANGE", "QUIET") and rl in ("VOLATILE", "TREND UP", "TREND DOWN") and e15 and \
            _recent(e15, m15.tf, t, 4) and e15.direction == bias and (sl_ev is None or sl_ev.get("strength", 1) >= 0.5):
        return dict(regime=f"BREAKOUT_{word}", kind="BREAKOUT", direction=bias, tfs=tfr,
                    reasons=[f"{rl} out of a {hl} {c['bias_tf']} range", f"{m15.tf} {e15.kind} {word}"], risks=[])
    trig = (e5 and e5.direction == bias and _recent(e5, m5.tf, t, 24)) or \
           (e15 and e15.direction == bias and _recent(e15, m15.tf, t, 8))
    htf_ok = (h4.trend == bias or not h4.trend) and (h1.trend == bias or not h1.trend)
    if htf_ok and m15.trend == bias:
        reasons.append(f"{c['htf']}/{c['bias_tf']}/{m15.tf} all {word}")
        return dict(regime=f"TREND_{word}", kind="TREND", direction=bias, tfs=tfr, reasons=reasons, risks=risks,
                    trigger=bool(trig))
    if htf_ok and m15.trend == -bias:
        pd = m15.pd if m15.pd is not None else h1.pd
        in_zone = pd is not None and ((bias == 1 and pd < 0.5) or (bias == -1 and pd > 0.5))
        reasons.append(f"{m15.tf} pulling back against {word} HTF")
        if in_zone:
            reasons.append("price in " + ("discount" if bias == 1 else "premium"))
        return dict(regime=f"PULLBACK_{word}", kind="PULLBACK", direction=bias, tfs=tfr, reasons=reasons,
                    risks=risks, trigger=bool(trig))
    return dict(regime="NO_BIAS", kind=None, direction=0, tfs=tfr, reasons=["structure unclear"], risks=[])


# ============================================================================ building a forecast
def _level_below(cands, entry, d, lo, hi):
    """Structural SL: the nearest candidate beyond the entry whose distance is within [lo, hi]."""
    ok = [(abs(entry - p), p, why) for p, why in cands if p is not None and (entry - p) * d > 0
          and lo <= abs(entry - p) <= hi]
    return min(ok) if ok else None


def build(view, reg, cfg, price=None):
    """-> (forecast dict | None, why-not text). Pure: uses only the view (closed bars at view['t'])."""
    c = cfg
    read, st, smc = view["read"], view.get("states") or {}, view.get("smc") or {}
    d = reg["direction"]
    if reg["regime"] in ("NO_DATA", "NO_BIAS", "RANGING") or not d:
        return None, reg["regime"]
    if reg["kind"] not in c["allowed_regimes"]:
        return None, f"regime {reg['kind']} not allowed"
    score = read.overall
    passing = read.passing
    min_score = c["min_score"] or passing
    if score is None:
        return None, "no analyser score"
    m15, m5, h1, h4 = st.get(c["setup_tf"]), st.get(c["trigger_tf"]), st.get(c["bias_tf"]), st.get(c["htf"])
    atr = (m15.atr if m15 and m15.atr else None) or (m5.atr if m5 else None)
    if not atr:
        return None, "no ATR"
    if c["min_atr"] and atr < c["min_atr"]:
        return None, f"ATR {atr:.2f} < {c['min_atr']}"
    rg = view.get("regime") or {}
    if c["min_adx"] and (rg.get("adx") or 0) < c["min_adx"]:
        return None, f"ADX {rg.get('adx')} < {c['min_adx']}"
    if c["sessions"]:
        from market_hours import in_session, utc_from_server
        h = utc_from_server(view["t"], view.get("utc_offset") or 0).hour
        if not any(in_session(s_, h) for s_ in c["sessions"]):
            return None, "outside forecast sessions"
    if c["news_filter"] and view.get("news") == "HIGH":
        return None, "news risk HIGH"
    if c["max_spread"] and view.get("spread") and view["spread"] > c["max_spread"]:
        return None, f"spread {view['spread']} > {c['max_spread']}"
    px = price if price is not None else (m5.price if m5 and m5.price else None)
    if not px:
        return None, "no price"
    # ---------------- entry model
    su = (smc.get("setups") or {}).get("BUY" if d == 1 else "SELL") if smc.get("ok") else None
    su_valid = bool(su) and su.get("status") in ("WAITING_RETEST", "RETESTED")
    fvg = (m15.fvg_bull if d == 1 else m15.fvg_bear) if m15 else None
    mode = c["entry_mode"]
    entry_type = zone = trigger_level = None
    why_entry = ""
    if mode in ("auto", "limit"):
        if su_valid and su.get("entry_zone") and su.get("status") == "WAITING_RETEST":
            z = su["entry_zone"]
            if (d == 1 and z[1] < px) or (d == -1 and z[0] > px):
                entry_type, zone, why_entry = "LIMIT", (z[0], z[1]), f"SMC {su['quality']} entry zone"
        if entry_type is None and fvg and ((d == 1 and fvg.hi < px) or (d == -1 and fvg.lo > px)) \
                and abs(px - (fvg.hi if d == 1 else fvg.lo)) <= 3 * atr:
            entry_type, zone, why_entry = "LIMIT", (fvg.lo, fvg.hi), f"{m15.tf} FVG"
    if entry_type is None and (mode == "breakout" or (mode == "auto" and reg["kind"] == "BREAKOUT")):
        sw = (m5.sh if d == 1 else m5.sl) if m5 else None
        if sw and not sw.broken and (sw.level - px) * d > 0 and abs(sw.level - px) <= 2 * atr:
            entry_type, trigger_level, why_entry = "BREAKOUT", sw.level, f"{m5.tf} swing {'high' if d == 1 else 'low'}"
    if entry_type is None and mode in ("auto", "market"):
        if not reg.get("trigger") and reg["kind"] in ("TREND", "PULLBACK"):
            return None, "no trigger yet (no valid continuation entry)"
        entry_type, why_entry = "MARKET", "trigger printed"
    if entry_type is None:
        return None, f"no {mode} entry available"
    if entry_type == "LIMIT":
        entry = zone[1] if d == 1 else zone[0]         # first touch of the zone
        trigger_desc = f"price enters {zone[0]:.2f}-{zone[1]:.2f}"
    elif entry_type == "BREAKOUT":
        entry = trigger_level
        trigger_desc = f"{c['tf']} {'close' if c['breakout_confirm'] == 'close' else 'touch'} beyond {trigger_level:.2f}"
    else:
        entry = px
        trigger_desc = "active at creation"
    # ---------------- SL (structural first, ATR fallback)
    buf = c["sl_buffer_atr"] * atr
    cands = []
    if su_valid:
        cands.append(((su.get("invalidation") or 0) - d * buf if su.get("invalidation") else None, "liquidity sweep extreme"))
    if zone:
        cands.append(((zone[0] if d == 1 else zone[1]) - d * buf, "entry zone invalidation (FVG/OB)"))
    for s_, nm in ((m15, "swing"), (m5, "swing")):
        if s_:
            sw = s_.sl if d == 1 else s_.sh
            if sw:
                cands.append((sw.level - d * buf, f"{s_.tf} {nm} {'low' if d == 1 else 'high'}"))
    e_ev = m15.last() if m15 else None
    if e_ev and e_ev.direction == d:
        cands.append((e_ev.level - d * buf, f"{m15.tf} {e_ev.kind} level"))
    pick = _level_below(cands, entry, d, c["min_sl_atr"] * atr, c["max_sl_atr"] * atr)
    if pick:
        sl, sl_reason = pick[1], pick[2]
    else:
        sl, sl_reason = entry - d * c["atr_sl"] * atr, f"ATR fallback ({c['atr_sl']}x)"
    risk = abs(entry - sl)
    if risk <= 0:
        return None, "invalid SL"
    # ---------------- TP (R multiples + opposing liquidity)
    tp1 = entry + d * risk
    tp2 = entry + d * risk * c["rr"]
    tp3 = entry + d * risk * (c["rr"] + 1)
    tp_reason = [f"TP1 1R", f"TP2 {c['rr']}R (configured RR)"]
    if smc.get("ok"):
        opp = sorted([lv["price"] for lv in smc["liquidity"]["levels"] if lv["side"] == d and (lv["price"] - tp2) * d > 0],
                     key=lambda p: abs(p - entry))
        if opp and abs(opp[0] - entry) <= risk * (c["rr"] + 3):
            tp3, tp_reason = opp[0], tp_reason + ["TP3 opposing liquidity"]
        else:
            tp_reason.append(f"TP3 {c['rr'] + 1}R")
    else:
        tp_reason.append(f"TP3 {c['rr'] + 1}R")
    # ---------------- confidence (documented factors, 0-100) + quality
    f = []                                                      # (factor, points, max)
    htf_pts = (10 if h4 and h4.trend == d else 0) + (10 if h1 and h1.trend == d else 0)
    f.append(("HTF alignment", htf_pts, 20))
    f.append(("structure", 15 if m15 and m15.trend == d else 7 if reg["kind"] == "PULLBACK" else 0, 15))
    trig_pts = 15 if (reg.get("trigger") or read.trigger) else 0
    le = smc.get("structure", {}).get("last_event") if smc.get("ok") else None
    if trig_pts and le and le.get("direction") == d and le.get("strength", 0) >= 0.6:
        trig_pts += 5
    if entry_type == "BREAKOUT":
        trig_pts = max(trig_pts, 10)
    f.append(("trigger quality", trig_pts, 20))
    smc_pts = ({"HIGH": 15, "MEDIUM": 10, "LOW": 5}.get(su.get("quality"), 0) if su_valid else 0) or (5 if (fvg or zone) else 0)
    f.append(("SMC confluence", smc_pts, 15))
    zone_ = (smc.get("dealing_range") or {}).get("zone") if smc.get("ok") else None
    good = "DISCOUNT" if d == 1 else "PREMIUM"
    f.append(("location", 10 if zone_ == good else 5 if zone_ == "EQUILIBRIUM" else 0, 10))
    sc_pts = 15 if score >= passing else round(15 * 0.5 * max(0.0, score) / passing) if passing else 0
    f.append(("analyser score", sc_pts, 15))
    f.append(("volatility suitability", 5 if rg.get("label") not in ("VOLATILE", "QUIET") else 0, 5))
    conf = sum(p for _, p, _ in f)
    risks = list(reg.get("risks") or [])
    if rg.get("label") == "VOLATILE":
        risks.append("high volatility")
    if view.get("news") == "HIGH":
        conf -= 10
        risks.append("news risk HIGH")
    if view.get("spread") and view.get("spread_median") and view["spread"] > 2 * view["spread_median"]:
        conf -= 5
        risks.append(f"spread {view['spread']:.2f} unusually high")
    if zone_ and zone_ != good and zone_ != "EQUILIBRIUM":
        risks.append(f"price in {zone_.lower()} for a {DIRW[d]}")
    conf = max(0, min(100, conf))
    majors = dict(htf=htf_pts == 20, structure=bool(m15 and m15.trend == d), trigger=trig_pts >= 15,
                  location=zone_ == good, smc=smc_pts >= 10)
    missing = sum(1 for v in majors.values() if not v)
    if conf >= 85 and missing == 0:
        quality = "A+"
    elif conf >= 70 and missing <= 1:
        quality = "A"
    elif conf >= 55 and missing <= 2:
        quality = "B"
    elif conf >= 40:
        quality = "C"
    else:
        quality = "NO SETUP"
    if quality == "NO SETUP":
        return None, f"no setup (confidence {conf})"
    if QUALITY_ORDER.index(quality) < QUALITY_ORDER.index(c["min_quality"]):
        return None, f"quality {quality} < {c['min_quality']}"
    if score < min_score:
        return None, f"analyser score {score:.0f}% < {min_score:.0f}%"
    if conf < c["min_confidence"]:
        return None, f"confidence {conf} < {c['min_confidence']:.0f}"
    why = [f"{k} {v}" for k, v in reg["tfs"].items() if v != "REFINEMENT"]
    why += reg["reasons"] + [f"entry: {why_entry}", f"analyser score {score:.0f}%"]
    if su_valid:
        why.append(f"SMC setup {su['quality']} ({su['status'].replace('_', ' ').lower()})")
    if not risks:
        why.append("no major conflicting signal")
    fc = dict(symbol=view.get("symbol", ""), direction=DIRW[d], dir=d, regime=reg["regime"], regime_kind=reg["kind"],
              timeframes=reg["tfs"], tf=c["tf"], setup_quality=quality, confidence=conf,
              confidence_factors=[dict(factor=n, points=p, max=m) for n, p, m in f],
              analyser_score=round(score, 1), passing_score=passing, bias=W.get(read.bias),
              entry_type=entry_type, entry=round(entry, 2),
              entry_zone_low=round(zone[0], 2) if zone else None, entry_zone_high=round(zone[1], 2) if zone else None,
              trigger=round(trigger_level, 2) if trigger_level is not None else None, trigger_desc=trigger_desc,
              sl=round(sl, 2), sl_reason=sl_reason, sl_distance=round(risk, 2),
              tp1=round(tp1, 2), tp2=round(tp2, 2), tp3=round(tp3, 2), tp_reason=tp_reason,
              risk_distance=round(risk, 2), reward_distance=round(abs(tp2 - entry), 2), rr=c["rr"],
              price_at_creation=round(px, 2), atr=round(atr, 3), session=view.get("session"),
              news_risk=view.get("news") or "n/a", spread_at_creation=view.get("spread"),
              why=why, risks=risks, smc_setup_id=su.get("setup_id") if su_valid else None,
              profile=view.get("profile", "LIVE"), setup_type="structure")
    return fc, ""


def build_vp(view, reg, cfg, kind):
    """Volume-profile setups on the bar that just CLOSED, against the PREVIOUS day's profile (never today's):
      vp_poc   price came to pPOC and was REJECTED (wick through / at the POC, close back on the side it came from)
               -> MARKET entry in the rejection direction, target pVAH (long) / pVAL (short)
      vp_edge  price poked beyond pVAH / pVAL and closed back INSIDE the value area -> fade, target pPOC
    SL beyond the rejection wick (+ buffer), TP2 = the VP target, TP1 halfway, TP3 the far edge / day extreme.
    Skipped when RR to TP2 < vp_min_rr, or (vp_bias_filter) against the HTF bias."""
    c = cfg
    vp, read, st = view.get("vp"), view.get("read"), view.get("states") or {}
    bars = view.get("recent") or []
    if not vp:
        return None, "no previous-day volume profile (Volume Profile plugin / history)"
    if read is None or len(bars) < 2:
        return None, "not enough data"
    b, prev = bars[-1], bars[-2]
    m15 = st.get(c["setup_tf"]) or st.get("M15")
    atr = (m15.atr if m15 and m15.atr else None) or max(0.5, b.h - b.l)
    tol = c["vp_tol_atr"] * atr
    poc, vah, val = vp["poc"], vp["vah"], vp["val"]
    rng = max(1e-9, b.h - b.l)
    d = 0
    why_setup = ""
    wick = 0.0
    if kind == "vp_poc":
        if b.l <= poc + tol and b.c > poc and prev.c > poc and (b.c - b.l) / rng >= 0.5:
            d, why_setup, wick = 1, f"pPOC {poc:.2f} held as support (rejection from above)", (min(b.o, b.c) - b.l) / rng
        elif b.h >= poc - tol and b.c < poc and prev.c < poc and (b.h - b.c) / rng >= 0.5:
            d, why_setup, wick = -1, f"pPOC {poc:.2f} held as resistance (rejection from below)", (b.h - max(b.o, b.c)) / rng
        target = vah if d == 1 else val
        far = max(vp["high"], vah) if d == 1 else min(vp["low"], val)
    else:
        if b.h > vah + tol and b.c < vah:
            d, why_setup, wick = -1, f"poked above pVAH {vah:.2f}, closed back inside value", (b.h - max(b.o, b.c)) / rng
        elif b.l < val - tol and b.c > val:
            d, why_setup, wick = 1, f"poked below pVAL {val:.2f}, closed back inside value", (min(b.o, b.c) - b.l) / rng
        target = poc
        far = val if d == -1 else vah
    if not d:
        return None, f"{kind}: no rejection on this bar"
    bias = read.bias
    if c["vp_bias_filter"] and bias != d:
        return None, f"{kind}: against / without HTF bias"
    entry = b.c
    buf = c["sl_buffer_atr"] * atr
    sl = (b.l - buf) if d == 1 else (b.h + buf)
    if abs(entry - sl) < c["min_sl_atr"] * atr:
        sl = entry - d * c["min_sl_atr"] * atr
    risk = abs(entry - sl)
    if (target - entry) * d <= 0:
        return None, f"{kind}: price already beyond the target"
    rr = abs(target - entry) / risk
    if rr < c["vp_min_rr"]:
        return None, f"{kind}: RR {rr:.2f} < {c['vp_min_rr']}"
    tp1 = entry + d * abs(target - entry) / 2
    tp3 = far if (far - target) * d > 0 else target + d * risk
    h4, h1 = st.get(c["htf"]), st.get(c["bias_tf"])
    f = []
    f.append(("HTF alignment", (13 if h4 and h4.trend == d else 0) + (12 if h1 and h1.trend == d else 0), 25))
    f.append(("rejection quality", 20 if wick >= 0.5 else 12 if wick >= 0.3 else 5, 20))
    f.append(("RR to target", 20 if rr >= 2.5 else 15 if rr >= 2 else 10, 20))
    inside = val <= entry <= vah
    f.append(("location", 15 if inside else 5, 15))
    score, passing = read.overall or 0.0, read.passing or 70.0
    f.append(("analyser score", 10 if score >= passing else 5 if score >= passing * 0.75 else 0, 10))
    smc = view.get("smc") or {}
    sw = smc.get("ok") and any(x["direction"] == d and x["confirmed"] and abs(x["level"] - entry) <= 3 * atr
                               for x in smc["liquidity"]["sweeps"][-3:])
    f.append(("SMC sweep confluence", 10 if sw else 0, 10))
    conf = max(0, min(100, sum(p for _, p, _ in f)))
    quality = "A+" if conf >= 85 else "A" if conf >= 70 else "B" if conf >= 55 else "C" if conf >= 40 else "NO SETUP"
    if quality == "NO SETUP" or QUALITY_ORDER.index(quality) < QUALITY_ORDER.index(c["min_quality"]):
        return None, f"{kind}: quality {quality}"
    if conf < c["min_confidence"]:
        return None, f"{kind}: confidence {conf} < {c['min_confidence']:.0f}"
    fc = dict(symbol=view.get("symbol", ""), direction=DIRW[d], dir=d, regime=reg.get("regime") or "NO_BIAS",
              regime_kind="VP", timeframes=reg.get("tfs") or {}, tf=c["tf"], setup_quality=quality, confidence=conf,
              confidence_factors=[dict(factor=n, points=p, max=m) for n, p, m in f],
              analyser_score=round(score, 1), passing_score=passing, bias=W.get(bias),
              entry_type="MARKET", entry=round(entry, 2), entry_zone_low=None, entry_zone_high=None, trigger=None,
              trigger_desc="rejection bar closed", sl=round(sl, 2), sl_reason="beyond the rejection wick",
              sl_distance=round(risk, 2), tp1=round(tp1, 2), tp2=round(target, 2), tp3=round(tp3, 2),
              tp_reason=["TP1 halfway", "TP2 " + ("pPOC" if kind == "vp_edge" else "pVAH" if d == 1 else "pVAL"),
                         "TP3 far edge / previous-day extreme"],
              risk_distance=round(risk, 2), reward_distance=round(abs(target - entry), 2), rr=round(rr, 2),
              price_at_creation=round(entry, 2), atr=round(atr, 3), session=view.get("session"),
              news_risk=view.get("news") or "n/a", spread_at_creation=view.get("spread"),
              why=[why_setup, f"previous day: POC {poc:.2f} · VAH {vah:.2f} · VAL {val:.2f}",
                   f"RR to target 1:{rr:.2f}", f"HTF bias {W.get(bias)}"] + (["SMC sweep nearby"] if sw else []),
              risks=[] if inside else ["entry outside the previous value area"], smc_setup_id=None,
              profile=view.get("profile", "LIVE"), setup_type=kind,
              vp_levels=dict(poc=round(poc, 2), vah=round(vah, 2), val=round(val, 2)))
    return fc, ""


def build_struct_poc(view, reg, cfg):
    """STRUCTURE + POC pullback: market structure is the main entry indicator, the previous day's POC is the place.
      1. structure gives the direction: regime TREND or PULLBACK (H4 / H1 bias, M15 structure), never RANGE / NO_BIAS
      2. price is on the trend side of pPOC (long: above it) and within sp_max_dist_atr x ATR -> a pullback can reach it
      3. the POC must sit INSIDE the structure: a long needs pPOC above the M15 swing low (the HL), a short below the
         M15 swing high (the LH); a return to a POC beyond it would already break the structure
      -> LIMIT at pPOC (+/- tolerance). SL beyond the structural swing (or pVAL / pVAH), TP2 = the M15 swing
         high / low the pullback started from (structure target) when RR >= vp_min_rr, else rr x risk.
    WAITING until price comes back to the POC; invalidated by an opposite CHoCH, a bias flip, a close beyond the SL,
    or when price reaches the TP2 structure target before ever pulling back (missed move)."""
    c = cfg
    kind = "struct_poc"
    vp, read, st, smc = view.get("vp"), view.get("read"), view.get("states") or {}, view.get("smc") or {}
    d = reg.get("direction") or 0
    if not vp:
        return None, f"{kind}: no previous-day volume profile"
    if read is None or read.overall is None:
        return None, f"{kind}: no analyser score"
    if reg.get("kind") not in ("TREND", "PULLBACK") or not d:
        return None, f"{kind}: structure not trending ({reg.get('regime')})"
    if reg["kind"] not in c["allowed_regimes"]:
        return None, f"{kind}: regime {reg['kind']} not allowed"
    m15, m5 = st.get(c["setup_tf"]), st.get(c["trigger_tf"])
    atr = (m15.atr if m15 and m15.atr else None) or (m5.atr if m5 else None)
    if not atr:
        return None, f"{kind}: no ATR"
    bars = view.get("recent") or []
    px = (m5.price if m5 and m5.price else None) or (bars[-1].c if bars else None)
    if not px:
        return None, f"{kind}: no price"
    poc, vah, val = vp["poc"], vp["vah"], vp["val"]
    tol = c["vp_tol_atr"] * atr
    dist = (px - poc) * d
    if dist < -tol:
        return None, f"{kind}: price already on the wrong side of pPOC {poc:.2f}"
    if dist > c["sp_max_dist_atr"] * atr:
        return None, f"{kind}: pPOC {poc:.2f} too far ({dist / atr:.1f} ATR > {c['sp_max_dist_atr']})"
    sw_in = (m15.sl if d == 1 else m15.sh) if m15 else None          # the structure point that must hold
    sw_out = (m15.sh if d == 1 else m15.sl) if m15 else None         # where the pullback started (structure target)
    if sw_in and (poc - sw_in.level) * d <= 0:
        return None, (f"{kind}: pPOC {poc:.2f} is beyond the {m15.tf} swing {'low' if d == 1 else 'high'} "
                      f"{sw_in.level:.2f} (a return there breaks structure)")
    zone = (poc - tol, poc + tol)
    entry = zone[1] if d == 1 else zone[0]
    buf = c["sl_buffer_atr"] * atr
    cands = []
    if sw_in:
        cands.append((sw_in.level - d * buf, f"{m15.tf} swing {'low' if d == 1 else 'high'} (structure)"))
    edge = val if d == 1 else vah
    if (entry - edge) * d > 0:
        cands.append((edge - d * buf, "pVAL" if d == 1 else "pVAH"))
    pick = _level_below(cands, entry, d, c["min_sl_atr"] * atr, c["max_sl_atr"] * atr)
    if pick:
        sl, sl_reason = pick[1], pick[2]
    else:
        sl, sl_reason = entry - d * c["atr_sl"] * atr, f"ATR fallback ({c['atr_sl']}x)"
    risk = abs(entry - sl)
    if risk <= 0:
        return None, f"{kind}: invalid SL"
    tp1 = entry + d * risk
    if sw_out and not sw_out.broken and (sw_out.level - entry) * d / risk >= c["vp_min_rr"]:
        tp2, tp2_why = sw_out.level, f"TP2 {m15.tf} swing {'high' if d == 1 else 'low'} (structure target)"
    else:
        tp2, tp2_why = entry + d * risk * c["rr"], f"TP2 {c['rr']}R (configured RR)"
    far = vah if d == 1 else val
    tp3, tp3_why = (far, "TP3 pVAH" if d == 1 else "TP3 pVAL") if (far - tp2) * d > 0 else \
        (tp2 + d * risk, "TP3 +1R")
    rr = abs(tp2 - entry) / risk
    # ---------------- confidence (0-100)
    h4, h1 = st.get(c["htf"]), st.get(c["bias_tf"])
    f = []
    f.append(("HTF alignment", (10 if h4 and h4.trend == d else 0) + (10 if h1 and h1.trend == d else 0), 20))
    f.append(("structure", 20 if m15 and m15.trend == d else 12 if reg["kind"] == "PULLBACK" else 0, 20))
    su = (smc.get("setups") or {}).get("BUY" if d == 1 else "SELL") if smc.get("ok") else None
    fvg = (m15.fvg_bull if d == 1 else m15.fvg_bear) if m15 else None
    near = lambda lo, hi: lo - atr <= poc <= hi + atr                                     # noqa: E731
    conf_pts, conf_why = 8, "POC alone"
    if su and su.get("status") in ("WAITING_RETEST", "RETESTED") and su.get("entry_zone") and near(*su["entry_zone"]):
        conf_pts, conf_why = 20, "POC inside the SMC entry zone"
    elif fvg and near(fvg.lo, fvg.hi):
        conf_pts, conf_why = 15, f"POC at a {m15.tf} FVG"
    f.append(("POC confluence", conf_pts, 20))
    score, passing = read.overall, read.passing or 70.0
    f.append(("analyser score", 15 if score >= passing else round(15 * 0.5 * max(0.0, score) / passing), 15))
    f.append(("RR to structure target", 15 if rr >= 3 else 10 if rr >= 2 else 5, 15))
    zone_ = (smc.get("dealing_range") or {}).get("zone") if smc.get("ok") else None
    good = "DISCOUNT" if d == 1 else "PREMIUM"
    f.append(("location", 10 if zone_ == good else 5 if zone_ in (None, "EQUILIBRIUM") else 0, 10))
    conf = max(0, min(100, sum(p for _, p, _ in f)))
    quality = "A+" if conf >= 85 else "A" if conf >= 70 else "B" if conf >= 55 else "C" if conf >= 40 else "NO SETUP"
    if quality == "NO SETUP" or QUALITY_ORDER.index(quality) < QUALITY_ORDER.index(c["min_quality"]):
        return None, f"{kind}: quality {quality}"
    min_score = c["min_score"] or passing
    if score < min_score:
        return None, f"{kind}: analyser score {score:.0f}% < {min_score:.0f}%"
    if conf < c["min_confidence"]:
        return None, f"{kind}: confidence {conf} < {c['min_confidence']:.0f}"
    risks = list(reg.get("risks") or [])
    if zone_ and zone_ not in (good, "EQUILIBRIUM"):
        risks.append(f"POC in {zone_.lower()} for a {DIRW[d]}")
    why = [f"{k} {v}" for k, v in reg["tfs"].items() if v != "REFINEMENT"] + reg["reasons"]
    why += [f"entry: pullback to pPOC {poc:.2f} ({conf_why})",
            f"previous day: POC {poc:.2f} · VAH {vah:.2f} · VAL {val:.2f}",
            f"structure holds: {m15.tf} swing {'low' if d == 1 else 'high'} {sw_in.level:.2f}" if sw_in else
            "no structural swing found (ATR / VA stop)", f"RR to TP2 1:{rr:.2f}"]
    fc = dict(symbol=view.get("symbol", ""), direction=DIRW[d], dir=d, regime=reg["regime"], regime_kind=reg["kind"],
              timeframes=reg["tfs"], tf=c["tf"], setup_quality=quality, confidence=conf,
              confidence_factors=[dict(factor=n, points=p, max=m) for n, p, m in f],
              analyser_score=round(score, 1), passing_score=passing, bias=W.get(read.bias),
              entry_type="LIMIT", entry=round(entry, 2), entry_zone_low=round(zone[0], 2),
              entry_zone_high=round(zone[1], 2), trigger=None, trigger_desc=f"pullback touches pPOC {poc:.2f}",
              sl=round(sl, 2), sl_reason=sl_reason, sl_distance=round(risk, 2),
              tp1=round(tp1, 2), tp2=round(tp2, 2), tp3=round(tp3, 2), tp_reason=["TP1 1R", tp2_why, tp3_why],
              risk_distance=round(risk, 2), reward_distance=round(abs(tp2 - entry), 2), rr=round(rr, 2),
              price_at_creation=round(px, 2), atr=round(atr, 3), session=view.get("session"),
              news_risk=view.get("news") or "n/a", spread_at_creation=view.get("spread"),
              why=why, risks=risks, smc_setup_id=None, profile=view.get("profile", "LIVE"), setup_type=kind,
              vp_levels=dict(poc=round(poc, 2), vah=round(vah, 2), val=round(val, 2)))
    return fc, ""


def build_reversal(view, reg, cfg, kind):
    """Reversal arrows as a forecast (the same arrows as the Reversal plugin on the chart):
      rev_ok  : R✓ - entry at the close of the bar that CONFIRMED the reversal
      rev_any : R? / R✓ - entry at the close of the reversal bar itself (earlier, less certain)
    Only in the HTF bias direction when rev_bias_filter (a top in a bearish market = end of a pullback).
    SL beyond the reversal extreme (+ buffer, min_sl_atr), TP1 1R, TP2 rr x risk, TP3 rr + 1."""
    c = cfg
    rv = view_reversal(view)
    read = view.get("read")
    if rv is None:
        return None, f"{kind}: reversal detector not available (Reversal plugin / history)"
    ev = rv.get("ok" if kind == "rev_ok" else "any")
    if not ev:
        return None, f"{kind}: no {'confirmed ' if kind == 'rev_ok' else ''}reversal on this bar"
    d = ev["side"]
    if ev["score"] < c["rev_min_score"]:
        return None, f"{kind}: reversal score {ev['score']} < {c['rev_min_score']}"
    bias = getattr(read, "bias", 0) if read is not None else 0
    if c["rev_bias_filter"] and bias != d:
        return None, f"{kind}: against / without the HTF bias (counter-trend arrow)"
    atr = rv["atr"]
    entry = ev["entry"]
    buf = c["sl_buffer_atr"] * atr
    sl = ev["level"] - d * buf
    if (entry - sl) * d < c["min_sl_atr"] * atr:
        sl = entry - d * c["min_sl_atr"] * atr
    risk = abs(entry - sl)
    if risk <= 0 or risk > c["max_sl_atr"] * atr * 2:
        return None, f"{kind}: SL distance {risk:.2f} not usable"
    tp1, tp2, tp3 = entry + d * risk, entry + d * risk * c["rr"], entry + d * risk * (c["rr"] + 1)
    st = view.get("states") or {}
    h4, h1 = st.get(c["htf"]), st.get(c["bias_tf"])
    f = [("reversal score", round(40 * ev["score"] / 100), 40),
         ("confirmed (R✓)", 15 if ev["confirmed"] else 0, 15),
         ("HTF alignment", (10 if h4 and h4.trend == d else 0) + (10 if h1 and h1.trend == d else 0), 20)]
    score, passing = (read.overall or 0.0) if read is not None else 0.0, (read.passing or 70.0) if read is not None else 70.0
    f.append(("analyser score", 15 if score >= passing else 8 if score >= passing * 0.75 else 0, 15))
    f.append(("sweep / divergence", 10 if any(t in ("DIV",) or t.endswith(("H", "L")) for t in ev["tags"]) else 0, 10))
    conf = max(0, min(100, sum(p for _, p, _ in f)))
    quality = "A+" if conf >= 85 else "A" if conf >= 70 else "B" if conf >= 55 else "C" if conf >= 40 else "NO SETUP"
    if quality == "NO SETUP" or QUALITY_ORDER.index(quality) < QUALITY_ORDER.index(c["min_quality"]):
        return None, f"{kind}: quality {quality}"
    if conf < c["min_confidence"]:
        return None, f"{kind}: confidence {conf} < {c['min_confidence']:.0f}"
    word = "top" if d == -1 else "bottom"
    fc = dict(symbol=view.get("symbol", ""), direction=DIRW[d], dir=d, regime=reg.get("regime") or "NO_BIAS",
              regime_kind="REVERSAL_ARROW", timeframes=reg.get("tfs") or {}, tf=c["tf"], setup_quality=quality,
              confidence=conf, confidence_factors=[dict(factor=n, points=p, max=m) for n, p, m in f],
              analyser_score=round(score, 1), passing_score=passing, bias=W.get(bias),
              entry_type="MARKET", entry=round(entry, 2), entry_zone_low=None, entry_zone_high=None, trigger=None,
              trigger_desc=("reversal confirmed (close through the reversal bar)" if kind == "rev_ok"
                            else "reversal bar closed"),
              sl=round(sl, 2), sl_reason="beyond the reversal extreme", sl_distance=round(risk, 2),
              tp1=round(tp1, 2), tp2=round(tp2, 2), tp3=round(tp3, 2),
              tp_reason=["TP1 1R", f"TP2 {c['rr']}R", f"TP3 {c['rr'] + 1}R"],
              risk_distance=round(risk, 2), reward_distance=round(abs(tp2 - entry), 2), rr=c["rr"],
              price_at_creation=round(entry, 2), atr=round(atr, 3), session=view.get("session"),
              news_risk=view.get("news") or "n/a", spread_at_creation=view.get("spread"),
              why=[f"R{'✓' if ev['confirmed'] else '?'}{ev['score']} {word}: " + " · ".join(ev["tags"][:3]),
                   *ev["why"][:4], f"HTF bias {W.get(bias)}"],
              risks=[] if ev["confirmed"] else ["not confirmed yet (R?)"], smc_setup_id=None,
              profile=view.get("profile", "LIVE"), setup_type=kind,
              reversal=dict(score=ev["score"], tags=ev["tags"], confirmed=ev["confirmed"], bar_t=ev["t"]))
    return fc, ""


def strategy_snapshot(stm, S, i, e):
    """v2.8.45 (audit): the indicator VALUES behind a strategy forecast at its bar (not only met / missed labels), so
    \"why did the system make this forecast?\" can be answered in numbers later. Only data of bar i (closed)."""
    def v(key, nd=2):
        arr = S.get(key)
        x = arr[i] if arr and i < len(arr) else None
        return round(x, nd) if isinstance(x, (int, float)) else None
    out = dict(mmi=v("mmi", 1), cmi=v("cmi", 1), adx=v("adx", 1), rsi=v("rsi", 1), mfi=v("mfi", 1), atr=v("atr", 3),
               ema20=v("ema20"), ema50=v("ema50"), close=v("c"), route=stm.route_zone(S, i, {}))
    try:
        for tf, w in (("H4", 30), ("M15", 32), ("M5", 32)):
            m = stm.mtf_state(S, i, tf, w)
            if m:
                out[f"bos_{tf.lower()}"] = dict(last=m.get("last"), age=m.get("age"), up=m.get("up"), dn=m.get("dn"))
    except Exception:
        pass
    try:
        cv = stm.cvd_change(S, i, 8)
        out["cvd_change8"] = round(cv, 1) if cv is not None else None
    except Exception:
        out["cvd_change8"] = None
    out["cvd_source"] = "estimated (OHLC + tick volume, " + ("M5" if (S.get("cvd") or {}).get("m5") else "entry TF") + ")"
    for k in ("box_high", "box_low", "ob_lo", "ob_hi", "spike"):
        if e.get(k) is not None:
            out[k] = e[k]
    out["met"] = {k: bool(x) for k, x in (e.get("met") or {}).items()}
    return out


def build_strategy(view, reg, cfg, setup):
    """The Trading-page STRATEGIES (strategies.py) as forecasts on the forecast timeframe, on the bar where the
    strategy turns OK (fresh). TREND / PULLBACK: one MARKET forecast. CONSOLIDATION: TWO BREAKOUT forecasts
    (BUY STOP above / SELL STOP below the box, touch = fill) - the side that never fills simply expires."""
    import strategies as stm
    name = setup[3:]
    c = cfg
    fn = view.get("_bars_fn")
    bars = fn(c["tf"])[-400:] if fn else []
    if len(bars) < 90:
        return None, f"{setup}: not enough bars"
    scfg = stm.load_settings()[name]
    S = stm.series(bars, {k: fn(k) for k in stm.MTF_TFS} if fn else None)
    i = len(bars) - 1
    read = view.get("read")
    bias = getattr(read, "bias", 0) or 0
    e = stm.evaluate(name, bars, S, i, scfg, bias)
    if not e["ok"]:
        miss = ", ".join(e.get("missing") or []) or f"score {e['score']:.0f}% < {scfg['min_pct']:.0f}%"
        return None, f"{setup}: {e.get('why') or miss}"
    prev = stm.evaluate(name, bars, S, i - 1, scfg, bias)
    if prev["ok"] and (name != "conso" or (prev.get("box_high"), prev.get("box_low")) == (e.get("box_high"), e.get("box_low"))):
        return None, f"{setup}: already OK on the previous bar (not fresh)"
    atr = S["atr"][i] or 1.0
    score = (read.overall or 0.0) if read is not None else 0.0
    passing = (read.passing or 70.0) if read is not None else 70.0
    conf = int(round(e["score"]))
    quality = "A+" if conf >= 85 else "A" if conf >= 70 else "B" if conf >= 55 else "C" if conf >= 40 else "C"
    met = [stm.label(name, k) for k in e["on"] if e["met"].get(k)]
    miss = [stm.label(name, k) for k in e["on"] if not e["met"].get(k)]

    snap = strategy_snapshot(stm, S, i, e)

    def rec(d, entry_type, entry, sl, tp, trigger=None, confirm=None):
        risk = abs(entry - sl)
        return dict(symbol=view.get("symbol", ""), direction=DIRW[d], dir=d, regime=reg.get("regime") or "NO_BIAS",
                    regime_kind="STRATEGY", timeframes=reg.get("tfs") or {}, tf=c["tf"], setup_quality=quality,
                    confidence=conf, confidence_factors=[dict(factor=f"{stm.TITLE[name]} conditions met", points=conf, max=100)],
                    analyser_score=round(score, 1), passing_score=passing, bias=W.get(bias),
                    entry_type=entry_type, entry=round(entry, 2), entry_zone_low=None, entry_zone_high=None,
                    trigger=round(trigger, 2) if trigger is not None else None, confirm=confirm,
                    trigger_desc="strategy OK on this bar" if entry_type == "MARKET" else f"stop order at {trigger:.2f}",
                    sl=round(sl, 2), sl_reason="strategy stop", sl_distance=round(risk, 2),
                    tp1=round(entry + d * risk, 2), tp2=round(tp, 2), tp3=round(tp + d * risk, 2),
                    tp_reason=["TP1 1R", f"TP2 {scfg['rr']:g}R (strategy RR)", "TP3 +1R"],
                    risk_distance=round(risk, 2), reward_distance=round(abs(tp - entry), 2), rr=scfg["rr"],
                    price_at_creation=round(bars[-1].c, 2), atr=round(atr, 3), session=view.get("session"),
                    news_risk=view.get("news") or "n/a", spread_at_creation=view.get("spread"),
                    why=[f"STRATEGY {stm.TITLE[name]} {e['score']:.0f}% ≥ {scfg['min_pct']:.0f}%"] + met,
                    risks=[f"not met: {x}" for x in miss], smc_setup_id=None, profile=view.get("profile", "LIVE"),
                    setup_type=setup, values=snap)
    if name == "conso":
        p = e
        legs = [rec(1, "BREAKOUT", p["buy_stop"], p["buy_sl"], p["buy_tp"], p["buy_stop"], "touch"),
                rec(-1, "BREAKOUT", p["sell_stop"], p["sell_sl"], p["sell_tp"], p["sell_stop"], "touch")]
        for leg in legs:
            leg["expiry_bars"] = int(scfg["expiry_bars"])
        return legs, ""
    return rec(e["side"], "MARKET", e["entry"], e["sl"], e["tp"]), ""


ROUTER_TREND = ("TREND UP", "TREND DOWN")
ROUTER_RANGE = ("RANGE",)


def build_router(view, reg, cfg):
    """REGIME ROUTER (same idea as AUTO TRADE's router), on the regime of the forecast view (regime.py):
      TREND UP / DOWN -> the structure forecast, only in the trend direction, RR router_trend_rr
      RANGE           -> a confirmed reversal arrow (R✓), both directions but not against a clear HTF bias,
                         RR router_range_rr
      anything else   -> no forecast"""
    c = cfg
    rg = view.get("regime") or {}
    label, rdir = rg.get("label"), rg.get("dir") or 0
    if label in ROUTER_TREND:
        fc, why = build(view, reg, dict(c, rr=c["router_trend_rr"]))
        if fc is None:
            return None, f"router {label}: {why}"
        if rdir and fc["dir"] != rdir:
            return None, f"router {label}: {fc['direction']} is against the trend"
    elif label in ROUTER_RANGE:
        read = view.get("read")
        bias = getattr(read, "bias", 0) if read is not None else 0
        fc, why = build_reversal(view, reg, dict(c, rr=c["router_range_rr"], rev_bias_filter=False), "rev_ok")
        if fc is None:
            return None, f"router {label}: {why}"
        if bias == -fc["dir"]:
            return None, f"router {label}: arrow against the HTF bias"
    else:
        return None, f"router: regime {label or 'n/a'} -> no trade"
    fc.update(setup_type="router", router_mode="TREND" if label in ROUTER_TREND else "RANGE", router_regime=label)
    fc["why"] = [f"ROUTER {fc['router_mode']} ({label}) · 1:{fc['rr']}"] + list(fc["why"])
    return fc, ""


def fingerprint(fc):
    raw = "|".join(str(x) for x in (fc.get("setup_type", "structure"), fc["symbol"], fc["direction"], fc["regime"],
                                    fc.get("entry_zone_low"),
                                    fc.get("entry_zone_high"), fc.get("trigger"), fc["tf"], fc.get("smc_setup_id"),
                                    fc["entry_type"], round(fc["sl"], 1)))
    return hashlib.sha1(raw.encode()).hexdigest()[:14]


# ============================================================================ lifecycle (pure, bar / tick driven)
def _r(fc, price):
    return round((price - fc["entry"]) * fc["dir"] / fc["risk_distance"], 3) if fc["risk_distance"] else 0.0


def _finish(fc, status, t, price, result, reason, r=None, **extra):
    fc.update(status=status, closed_at=int(t), exit_price=round(price, 2) if price is not None else None,
              result=result, result_reason=reason, r=round(r, 2) if r is not None else None, **extra)
    if fc.get("trigger_time"):
        fc["duration_min"] = round((t - fc["trigger_time"]) / 60, 1)
    fc.setdefault("events", []).append(dict(t=int(t), event=status, result=result))
    return fc


def check_trigger(fc, bar, cfg):
    """WAITING -> TRIGGERED on a CLOSED bar that comes after the creation bar. Returns True when triggered."""
    if fc["status"] != "WAITING":
        return False
    d = fc["dir"]
    hit, price = False, None
    if fc["entry_type"] == "MARKET":
        hit, price = True, fc["entry"]
    elif fc["entry_type"] == "LIMIT":
        edge = fc["entry_zone_high"] if d == 1 else fc["entry_zone_low"]
        if (d == 1 and bar.l <= edge) or (d == -1 and bar.h >= edge):
            hit = True
            price = min(bar.o, edge) if d == 1 else max(bar.o, edge)     # a gap through the zone fills at the open
    elif fc["entry_type"] == "BREAKOUT":
        lvl = fc["trigger"]
        if (fc.get("confirm") or cfg["breakout_confirm"]) == "close":
            if (d == 1 and bar.c > lvl) or (d == -1 and bar.c < lvl):
                hit, price = True, bar.c
        elif (d == 1 and bar.h > lvl) or (d == -1 and bar.l < lvl):
            hit, price = True, max(bar.o, lvl) if d == 1 else min(bar.o, lvl)
    if not hit:
        return False
    _trigger(fc, bar.t + TFS.get(fc["tf"], 300) if fc["entry_type"] == "BREAKOUT" else bar.t, price)
    return True


def _trigger(fc, t, price):
    """Records the trigger. For LIMIT / BREAKOUT the SL / TP keep their $ distances from the actual fill."""
    d = fc["dir"]
    if fc["entry_type"] != "MARKET" and abs(price - fc["entry"]) > 1e-9:
        shift = price - fc["entry"]
        for k in ("sl", "tp1", "tp2", "tp3"):
            fc[k] = round(fc[k] + shift, 2)
        fc["entry"] = round(price, 2)
    fc.update(status="TRIGGERED", trigger_time=int(t), trigger_price=round(price, 2), sl_at_trigger=fc["sl"],
              tp_at_trigger=fc["tp2"], mfe_r=0.0, mae_r=0.0, bars_running=0, tp1_hit=False, tp2_hit=False,
              tp3_hit=False, regime_state=f"TRIGGERED_{fc['direction']}")
    fc.setdefault("events", []).append(dict(t=int(t), event="TRIGGERED", price=round(price, 2)))
    _ = d


def _target(fc, cfg):
    return fc[{"tp1": "tp1", "tp2": "tp2", "tp3": "tp3"}[cfg["final_target"]]]


def check_result(fc, bar, cfg, trigger_bar=False):
    """TRIGGERED/RUNNING on a CLOSED bar: MFE/MAE, milestones, SL vs final target, ambiguity, timeout."""
    if fc["status"] not in ("TRIGGERED", "RUNNING"):
        return None
    d = fc["dir"]
    t_close = bar.t + TFS.get(fc["tf"], 300)
    fav = bar.h if d == 1 else bar.l
    adv = bar.l if d == 1 else bar.h
    fc["mfe_r"] = max(fc.get("mfe_r", 0.0), _r(fc, fav)) if not trigger_bar else fc.get("mfe_r", 0.0)
    fc["mae_r"] = min(fc.get("mae_r", 0.0), _r(fc, adv))
    sl_hit = (adv <= fc["sl"]) if d == 1 else (adv >= fc["sl"])
    tgt = _target(fc, cfg)
    if trigger_bar:                                  # only the stop is checked on the trigger bar (conservative)
        if sl_hit:
            return _finish(fc, "SL_HIT", t_close, fc["sl"], "SL", RESULT_TEXT["SL_HIT"], -1.0, ambiguous=False)
        fc["status"] = "RUNNING"
        fc["regime_state"] = f"RUNNING_{fc['direction']}"
        return None
    fc["status"] = "RUNNING"
    fc["regime_state"] = f"RUNNING_{fc['direction']}"
    fc["bars_running"] = fc.get("bars_running", 0) + 1
    tp_hit = {k: ((fav >= fc[k]) if d == 1 else (fav <= fc[k])) for k in ("tp1", "tp2", "tp3")}
    final_hit = (fav >= tgt) if d == 1 else (fav <= tgt)
    if sl_hit and final_hit:
        fc["ambiguous"] = True
        rule = cfg["ambiguous_rule"]
        if rule == "exclude":
            return _finish(fc, "AMBIGUOUS", t_close, None, "AMBIGUOUS_BAR", RESULT_TEXT["AMBIGUOUS"], None)
        if rule == "tp_first":
            return _finish(fc, "TP_HIT", t_close, tgt, cfg["final_target"].upper(),
                           RESULT_TEXT["TP_HIT"] + " (AMBIGUOUS_BAR, rule tp_first)", _r(fc, tgt))
        return _finish(fc, "SL_HIT", t_close, fc["sl"], "SL",
                       RESULT_TEXT["SL_HIT"] + " (AMBIGUOUS_BAR, rule sl_first)", -1.0)
    for k in ("tp1", "tp2", "tp3"):
        if tp_hit[k] and not fc.get(f"{k}_hit"):
            fc[f"{k}_hit"] = True
            fc.setdefault("events", []).append(dict(t=int(t_close), event=f"{k.upper()}_TOUCHED"))
    if sl_hit:
        note = " (after TP1 was touched)" if fc.get("tp1_hit") else ""
        return _finish(fc, "SL_HIT", t_close, fc["sl"], "SL", RESULT_TEXT["SL_HIT"] + note, -1.0, ambiguous=False)
    if final_hit:
        return _finish(fc, "TP_HIT", t_close, tgt, cfg["final_target"].upper(), RESULT_TEXT["TP_HIT"], _r(fc, tgt),
                       ambiguous=False)
    if fc["bars_running"] >= cfg["running_timeout_bars"]:
        return _finish(fc, "EXPIRED", t_close, bar.c, "TIMEOUT", RESULT_TEXT["EXPIRED_RUNNING"], _r(fc, bar.c))
    return None


def check_tick(fc, t, bid, ask, cfg):
    """Live ticks in their real order: trigger (LIMIT / touch-breakout) and SL vs target. Returns True on change."""
    d = fc["dir"]
    px = bid if d == 1 else ask                       # exit side for monitoring
    ent = ask if d == 1 else bid                      # entry side
    if fc["status"] == "WAITING":
        if fc["entry_type"] == "LIMIT":
            edge = fc["entry_zone_high"] if d == 1 else fc["entry_zone_low"]
            if (d == 1 and ent <= edge) or (d == -1 and ent >= edge):
                _trigger(fc, t, edge)
                fc["status"] = "RUNNING"
                fc["trigger_source"] = "tick"
                return True
        elif fc["entry_type"] == "BREAKOUT" and (fc.get("confirm") or cfg["breakout_confirm"]) == "touch":
            if (d == 1 and ent > fc["trigger"]) or (d == -1 and ent < fc["trigger"]):
                _trigger(fc, t, fc["trigger"])
                fc["status"] = "RUNNING"
                fc["trigger_source"] = "tick"
                return True
        return False
    if fc["status"] not in ("TRIGGERED", "RUNNING"):
        return False
    r = _r(fc, px)
    fc["mfe_r"] = max(fc.get("mfe_r", 0.0), r)
    fc["mae_r"] = min(fc.get("mae_r", 0.0), r)
    if (d == 1 and px <= fc["sl"]) or (d == -1 and px >= fc["sl"]):
        _finish(fc, "SL_HIT", t, fc["sl"], "SL", RESULT_TEXT["SL_HIT"] + " (tick)", -1.0, ambiguous=False)
        return True
    tgt = _target(fc, cfg)
    for k in ("tp1", "tp2", "tp3"):
        if ((d == 1 and px >= fc[k]) or (d == -1 and px <= fc[k])) and not fc.get(f"{k}_hit"):
            fc[f"{k}_hit"] = True
    if (d == 1 and px >= tgt) or (d == -1 and px <= tgt):
        _finish(fc, "TP_HIT", t, tgt, cfg["final_target"].upper(), RESULT_TEXT["TP_HIT"] + " (tick)", _r(fc, tgt),
                ambiguous=False)
        return True
    return False


def check_invalidation(fc, view, bar):
    """WAITING only: the setup is destroyed before entry -> reason code (never an SL)."""
    if fc["status"] != "WAITING":
        return None
    d = fc["dir"]
    read, st = view.get("read"), view.get("states") or {}
    straddle = fc.get("setup_type") == "st_conso"                 # a box straddle has no direction of its own
    if read is not None and read.bias and read.bias != d and not straddle:
        return "HTF_BIAS_FLIPPED"
    if (d == 1 and bar.c < fc["sl"]) or (d == -1 and bar.c > fc["sl"]):
        return "ENTRY_ZONE_INVALIDATED"
    if fc.get("setup_type") == "struct_poc" and ((d == 1 and bar.h >= fc["tp2"]) or (d == -1 and bar.l <= fc["tp2"])):
        return "MISSED_NO_PULLBACK"               # price reached the structure target without coming back to the POC
    m15 = st.get(fc.get("setup_tf") or "M15")
    e = m15.last() if m15 else None
    if e and e.kind == "CHoCH" and e.direction == -d and e.t > fc["created_at"] - TFS.get(fc["tf"], 300) and not straddle:
        return "OPPOSITE_CHOCH"
    if fc.get("smc_setup_id"):
        smc = view.get("smc") or {}
        su = (smc.get("setups") or {}).get("BUY" if d == 1 else "SELL") if smc.get("ok") else None
        if su and su.get("setup_id") == fc["smc_setup_id"] and su.get("status") == "INVALIDATED":
            return "STRUCTURE_BROKEN"
    return None


def invalidate_forecast(fc, t, code, text=None):
    return _finish(fc, "INVALIDATED", t, None, code, text or RESULT_TEXT["INVALIDATED"], None,
                   invalidation_reason=code)


def expire_forecast(fc, t):
    return _finish(fc, "EXPIRED", t, None, "NOT_TRIGGERED", RESULT_TEXT["EXPIRED"], None,
                   invalidation_reason="TIME_EXPIRY")


# ============================================================================ journal
class ForecastJournal:
    """Append-only JSON lines: every event of a forecast is a new line with the full record (the original
    creation snapshot is never rewritten; the latest line per id is the current state)."""

    def __init__(self, path=None, persist=True):
        self.path = path or JOURNAL_FILE
        self.persist = persist
        self.records = {}
        self.order = []
        self.lock = threading.Lock()
        self.seq = 0
        if persist:
            self._load()

    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                for ln in f:
                    try:
                        r = json.loads(ln)
                    except ValueError:
                        continue
                    if "id" not in r:
                        continue
                    if r["id"] not in self.records:
                        self.order.append(r["id"])
                    self.records[r["id"]] = r
                    try:
                        self.seq = max(self.seq, int(str(r["id"]).split("-")[-1]))
                    except ValueError:
                        pass
        except OSError:
            pass

    def new_id(self):
        with self.lock:
            self.seq += 1
            return f"FC-{self.seq:06d}"

    def save(self, fc, event):
        rec = json.loads(json.dumps(fc, default=str))
        rec["journal_event"] = event
        rec["journal_t"] = time.time()
        with self.lock:
            if fc["id"] not in self.records:
                self.order.append(fc["id"])
            self.records[fc["id"]] = rec
            if self.persist:
                try:
                    with open(self.path, "a", encoding="utf-8") as f:
                        f.write(json.dumps(rec) + "\n")
                except OSError as e:
                    try:
                        from error_log import ERRORS
                        ERRORS.add("STATE_ERROR", "forecast_journal", "WRITE_FAILED", str(e))
                    except Exception:
                        pass

    def all(self):
        return [self.records[i] for i in self.order]


# ============================================================================ engine
class ForecastEngine:
    """Pure core shared by LIVE and REPLAY: on_bar(view, bar) once per CLOSED forecast bar; on_tick live."""

    def __init__(self, cfg=None, journal=None):
        self.cfg = clean_settings(cfg)
        self.journal = journal or ForecastJournal(persist=False)
        self.active = {r["id"]: r for r in self.journal.all() if r.get("status") in ACTIVE}
        self.regime_hist = deque(maxlen=50)
        self.last_regime = None
        self.last_view = None
        self.last_why_not = ""

    # ---------------- regime
    def get_regime(self, view):
        reg = get_regime(view, self.cfg)
        if self.last_regime is None or reg["regime"] != self.last_regime["regime"]:
            self.regime_hist.appendleft(dict(t=view["t"], regime=reg["regime"],
                                             prev=self.last_regime["regime"] if self.last_regime else None))
        self.last_regime = reg
        return reg

    # ---------------- lifecycle per closed bar
    def update_forecast(self, fc, view, bar):
        """One closed bar for one active forecast. Returns the event name or None."""
        if bar.t < fc["created_at"]:                       # never the creation bar or anything before it
            return None
        if fc["status"] == "WAITING":
            fc["bars_waiting"] = fc.get("bars_waiting", 0) + 1
            code = check_invalidation(fc, view, bar)
            if code:
                invalidate_forecast(fc, bar.t + TFS.get(fc["tf"], 300), code)
                return "INVALIDATED"
            if check_trigger(fc, bar, self.cfg):
                res = check_result(fc, bar, self.cfg, trigger_bar=True)
                return "SL_HIT" if res else "TRIGGERED"
            if fc["bars_waiting"] >= (fc.get("expiry_bars") or self.cfg["expiry_bars"]):
                expire_forecast(fc, bar.t + TFS.get(fc["tf"], 300))
                return "EXPIRED"
            return None
        res = check_result(fc, bar, self.cfg)
        return res["status"] if res else None

    def create_forecast(self, view, price=None, force=False, setup="structure"):
        """A new forecast of one setup type from this view if every rule allows it (no duplicates, one active per
        symbol / direction / setup type, re-forecasting rules)."""
        c = self.cfg
        if not c["enabled"] and not force:
            return None, "forecast OFF"
        reg = self.get_regime(view) if self.last_view is not view else self.last_regime
        if setup == "structure":
            fc, why = build(view, reg, c, price)
        elif setup == "struct_poc":
            fc, why = build_struct_poc(view, reg, c)
        elif setup in ("rev_ok", "rev_any"):
            fc, why = build_reversal(view, reg, c, setup)
        elif setup == "router":
            fc, why = build_router(view, reg, c)
        elif setup.startswith("st_"):
            fc, why = build_strategy(view, reg, c, setup)
        else:
            fc, why = build_vp(view, reg, c, setup)
        if fc is None:
            self.last_why_not = why
            return None, why
        if isinstance(fc, list):                           # a straddle: register every leg on its own
            made = [self._register(f, view)[0] for f in fc]
            made = [f for f in made if f]
            return (made[0] if made else None), ("" if made else self.last_why_not)
        return self._register(fc, view)

    def _register(self, fc, view):
        c = self.cfg
        fp = fingerprint(fc)
        if any(r.get("fingerprint") == fp for r in self.journal.all()[-300:]):
            self.last_why_not = "duplicate (same fingerprint)"
            return None, self.last_why_not
        same = [r for r in self.active.values() if r["symbol"] == fc["symbol"]
                and r.get("setup_type", "structure") == fc["setup_type"]
                and (r["dir"] == fc["dir"] or not c["one_per_direction"])]
        running = [r for r in same if r["status"] in ("TRIGGERED", "RUNNING")]
        if running:
            self.last_why_not = f"KEEP: {running[0]['id']} is running"
            return None, self.last_why_not
        waiting = [r for r in same if r["status"] == "WAITING"]
        limit = 1 if c["one_per_direction"] else max(1, int(c["max_active"]))
        if len(waiting) >= limit:
            old = waiting[0]
            # material change = another direction, another entry type, or the entry moved more than 1 ATR
            # (TREND <-> PULLBACK in the same direction alone is not a new setup)
            material = (old["dir"] != fc["dir"] or old["entry_type"] != fc["entry_type"]
                        or abs(old["entry"] - fc["entry"]) > 1.0 * (fc.get("atr") or 1))
            if not material:
                self.last_why_not = f"KEEP: {old['id']} still valid"
                return None, self.last_why_not
            invalidate_forecast(old, view["t"], "SETUP_REPLACED", "Setup changed materially; replaced by a new forecast.")
            self.journal.save(old, "INVALIDATED")
            self.active.pop(old["id"], None)
        fc.update(id=self.journal.new_id(), created_at=int(view["t"]), status="WAITING",
                  regime_state=f"WAITING_{fc['direction']}", fingerprint=fp, setup_tf=c["setup_tf"],
                  events=[dict(t=int(view["t"]), event="CREATED")])
        fc["snapshot"] = dict(score_at_creation=fc["analyser_score"], bias_at_creation=fc["bias"],
                              regime_at_creation=fc["regime"], structure_at_creation=dict(fc["timeframes"]),
                              entry_at_creation=fc["entry"], sl_at_creation=fc["sl"],
                              tp_at_creation=[fc["tp1"], fc["tp2"], fc["tp3"]],
                              confidence_at_creation=fc["confidence"], quality_at_creation=fc["setup_quality"])
        self.active[fc["id"]] = fc
        self.journal.save(fc, "CREATED")
        if fc["entry_type"] == "MARKET":                  # active immediately at the creation price
            _trigger(fc, view["t"], fc["entry"])
            self.journal.save(fc, "TRIGGERED")
        return fc, ""

    def on_bar(self, view, bar):
        """One CLOSED forecast bar: update actives first (with this bar), then maybe create (from this view)."""
        events = []
        self.last_view = None
        self.get_regime(view)
        self.last_view = view
        for fc in list(self.active.values()):
            ev = self.update_forecast(fc, view, bar)
            if ev:
                self.journal.save(fc, ev)
                events.append((fc["id"], ev))
                if fc.get("setup_type") == "st_conso" and fc.get("trigger_time"):
                    for o in list(self.active.values()):              # OCO: the other stop of the same box
                        if o is not fc and o.get("setup_type") == "st_conso" and o["status"] == "WAITING" \
                                and o["created_at"] == fc["created_at"]:
                            invalidate_forecast(o, bar.t + TFS.get(o["tf"], 300), "OCO_CANCELLED",
                                                "The other stop of the box filled first (OCO).")
                            self.journal.save(o, "INVALIDATED")
                            self.active.pop(o["id"], None)
                            events.append((o["id"], "INVALIDATED"))
            if fc["status"] not in ACTIVE:
                self.active.pop(fc["id"], None)
        for fc in self.create_all(view, price=bar.c):
            events.append((fc["id"], "CREATED"))
        return events

    def create_all(self, view, price=None, force=False):
        """Every enabled setup type is evaluated on its own (for the side-by-side comparison)."""
        made, why = [], []
        for st_ in self.cfg["setups"] or ["structure"]:
            fc, w = self.create_forecast(view, price, force, st_)
            if fc:
                made.append(fc)
            elif w:
                why.append(f"{st_}: {w}" if not w.startswith(st_) else w)
        self.last_why_not = "" if made else " · ".join(why)
        return made

    def on_tick(self, t, bid, ask):
        changed = []
        for fc in list(self.active.values()):
            if check_tick(fc, t, bid, ask, self.cfg):
                self.journal.save(fc, fc["status"] if fc["status"] not in ("TRIGGERED", "RUNNING") else "TRIGGERED")
                changed.append((fc["id"], fc["status"]))
            if fc["status"] not in ACTIVE:
                self.active.pop(fc["id"], None)
        return changed

    def cancel(self, fid, t=None):
        fc = self.active.get(fid)
        if not fc:
            return dict(ok=False, error="no active forecast with that id")
        invalidate_forecast(fc, t or time.time(), "MANUAL_CANCEL", "Cancelled by the user.")
        self.journal.save(fc, "INVALIDATED")
        self.active.pop(fid, None)
        return dict(ok=True, forecast=fc)


# ============================================================================ statistics
def _bucket(v, edges, fmt):
    if v is None:
        return "n/a"
    for lo, hi in edges:
        if lo <= v < hi:
            return fmt(lo, hi)
    return "other"


CONF_B = [(0, 50), (50, 60), (60, 70), (70, 80), (80, 90), (90, 101)]
SCORE_B = [(0, 70), (70, 75), (75, 80), (80, 85), (85, 90), (90, 101)]


def _block(rs):
    n = len(rs)
    trig = [r for r in rs if r.get("trigger_time")]
    tp = [r for r in rs if r.get("status") == "TP_HIT"]
    sl = [r for r in rs if r.get("status") == "SL_HIT"]
    amb = [r for r in rs if r.get("ambiguous")]
    rr = [r["r"] for r in rs if r.get("r") is not None and r.get("trigger_time")]
    gw = sum(x for x in rr if x > 0)
    gl = -sum(x for x in rr if x < 0)
    dur = [r["duration_min"] for r in rs if r.get("duration_min") is not None and r.get("status") in ("TP_HIT", "SL_HIT")]
    decided = len(tp) + len(sl)
    return dict(forecasts=n, triggered=len(trig), not_triggered=n - len(trig),
                expired=sum(1 for r in rs if r.get("status") == "EXPIRED"),
                invalidated=sum(1 for r in rs if r.get("status") == "INVALIDATED"),
                tp=len(tp), sl=len(sl), ambiguous=len(amb),
                win_rate=round(100 * len(tp) / decided, 1) if decided else None,
                trigger_rate=round(100 * len(trig) / n, 1) if n else None,
                avg_r=round(sum(rr) / len(rr), 3) if rr else None, expectancy=round(sum(rr) / len(rr), 3) if rr else None,
                total_r=round(sum(rr), 2), profit_factor=round(gw / gl, 2) if gl > 0 else None,
                avg_confidence=round(sum(r["confidence"] for r in rs) / n, 1) if n else None,
                avg_score=round(sum(r["analyser_score"] for r in rs) / n, 1) if n else None,
                avg_mfe_r=round(sum(r.get("mfe_r", 0) for r in trig) / len(trig), 2) if trig else None,
                avg_mae_r=round(sum(r.get("mae_r", 0) for r in trig) / len(trig), 2) if trig else None,
                avg_duration_min=round(sum(dur) / len(dur), 1) if dur else None,
                **_streaks_and_times(rs))


def _streaks_and_times(rs):
    """v2.8.45 (audit): max consecutive wins / losses (resolved TP / SL in time order), average minutes from creation
    to trigger, and average minutes to TP and to SL separately."""
    seq = sorted((r for r in rs if r.get("status") in ("TP_HIT", "SL_HIT")), key=lambda r: r.get("created_at") or 0)
    best = {"TP_HIT": 0, "SL_HIT": 0}
    run, last = 0, None
    for r in seq:
        run = run + 1 if r["status"] == last else 1
        last = r["status"]
        best[last] = max(best[last], run)

    def avg(xs):
        xs = [x for x in xs if x is not None]
        return round(sum(xs) / len(xs), 1) if xs else None
    trig = avg([(r["trigger_time"] - r["created_at"]) / 60 for r in rs if r.get("trigger_time") and r.get("created_at")])
    return dict(max_win_streak=best["TP_HIT"], max_loss_streak=best["SL_HIT"], avg_min_to_trigger=trig,
                avg_min_to_tp=avg([r.get("duration_min") for r in rs if r.get("status") == "TP_HIT"]),
                avg_min_to_sl=avg([r.get("duration_min") for r in rs if r.get("status") == "SL_HIT"]))


def statistics(records, min_samples=30):
    done = [r for r in records if r.get("status") in FINAL]

    def by(fn):
        g = {}
        for r in done:
            g.setdefault(fn(r), []).append(r)
        return [dict(key=k, **_block(v)) for k, v in sorted(g.items(), key=lambda kv: str(kv[0]))]

    wday = lambda r: datetime.fromtimestamp(r["created_at"], timezone.utc).strftime("%a")
    month = lambda r: datetime.fromtimestamp(r["created_at"], timezone.utc).strftime("%Y-%m")
    calib = by(lambda r: _bucket(r["confidence"], CONF_B, lambda a, b: f"{a}-{min(b, 100) - 1 if b <= 100 else 100}"))
    for row in calib:
        row["enough_data"] = row["triggered"] >= min_samples
    return dict(overall=_block(done), active=sum(1 for r in records if r.get("status") in ACTIVE),
                by_regime=by(lambda r: r["regime"]), by_direction=by(lambda r: r["direction"]),
                by_quality=by(lambda r: r["setup_quality"]), by_symbol=by(lambda r: r["symbol"]),
                by_timeframe=by(lambda r: r["tf"]), by_session=by(lambda r: r.get("session") or "n/a"),
                by_entry_type=by(lambda r: r["entry_type"]), by_day=by(wday), by_month=by(month),
                by_setup=by(lambda r: r.get("setup_type", "structure")),
                comparison=[dict(row, name=SETUP_TYPES.get(row["key"], row["key"]))
                            for row in by(lambda r: r.get("setup_type", "structure"))],
                calibration=calib, min_samples=min_samples,
                by_score=by(lambda r: _bucket(r["analyser_score"], SCORE_B,
                                              lambda a, b: f"<{b}" if a == 0 else f"{a}-{min(b, 100) - 1 if b <= 100 else 100}")),
                note=f"Calibration is only meaningful with at least {min_samples} triggered forecasts per bucket.")


# ============================================================================ replay (historical, no look-ahead)
def replay(hub, tf="M5", t_from=None, t_to=None, cfg=None, progress=None):
    """Walk CLOSED bars of `tf`; at each one: build the view with read_at / smc_at (as of that bar's close),
    update actives with that bar, maybe create. Deterministic: the same data gives the same records."""
    c = clean_settings(dict(cfg or {}, tf=tf))
    eng = ForecastEngine(c, ForecastJournal(persist=False))
    bars = hub.src.bars(tf)
    sec = TFS[tf]
    start = next((i for i, b in enumerate(bars) if b.t >= (t_from or 0)), len(bars))
    start = max(start, 60)
    end = len(bars) - 1
    if t_to:
        end = min(end, next((i for i, b in enumerate(bars) if b.t >= t_to), end))
    n = max(1, end - start)
    for k, i in enumerate(range(start, end)):
        bar = bars[i]
        view = market_view(hub, bar.t + sec, None, tf)
        eng.on_bar(view, bar)
        if progress and k % 100 == 0:
            progress(k / n, f"forecast replay bar {k:,}/{n:,}")
    recs = eng.journal.all()
    return dict(records=recs, stats=statistics(recs, c["calibration_min_samples"]), bars=n, tf=tf,
                period=[bars[start].t if start < len(bars) else None, bars[end].t if end < len(bars) else None],
                settings=c, regimes=list(eng.regime_hist)[:30])


# ============================================================================ forecast profile ("demo" indicator setup)
# The forecast can read the LIVE setup (default: exactly the terminal's score) or its OWN profile: a separate set of
# indicators (CHART / SCORE / timeframes / vote TF / parameters) + passing score + weights, like a demo account.
# The profile runs in its own analyser instance (a second Hub over the SAME price data, with its own plugin objects),
# so the live score, the live plugin settings and AUTO TRADE are never touched.
def load_profile(path=None):
    try:
        with open(path or PROFILE_FILE) as f:
            p = json.load(f)
        if not isinstance(p, dict):
            raise ValueError
    except (OSError, ValueError):
        p = {}
    return dict(use_live=bool(p.get("use_live", True)), plugins=dict(p.get("plugins") or {}),
                scoring=dict(p.get("scoring") or {}))


def save_profile(p, path=None):
    clean = dict(use_live=bool(p.get("use_live", True)), plugins={}, scoring=dict(p.get("scoring") or {}))
    for k, v in (p.get("plugins") or {}).items():
        if isinstance(v, dict):
            clean["plugins"][str(k)] = dict(score=bool(v.get("score")), chart=bool(v.get("chart")),
                                            chart_tfs=[t for t in (v.get("chart_tfs") or []) if t in TFS],
                                            vote_tf=v.get("vote_tf") if v.get("vote_tf") in TFS else None,
                                            params=dict(v.get("params") or {}))
    try:
        with open(path or PROFILE_FILE, "w") as f:
            json.dump(clean, f, indent=2)
    except OSError:
        pass
    return clean


def profile_from_hub(hub):
    """A profile that equals the live setup right now (the 'copy from live' starting point)."""
    return dict(use_live=False, scoring=dict(hub.scoring),
                plugins={p.key: dict(score=p.scoring, chart=p.enabled, chart_tfs=list(p.chart_tfs),
                                     vote_tf=getattr(p.obj, "vote_tf", None),
                                     params={k: v for k, v in p.obj.params.items() if p.defaults.get(k) != v})
                         for p in hub.plugins if p.kind == "indicator"})


def apply_profile(h, profile):
    """Configure a (separate) Hub's plugins + scoring from a profile. Plugins missing from the profile are OFF."""
    from engine import clean_scoring
    pl = profile.get("plugins") or {}
    for p in h.plugins:
        if p.kind != "indicator":
            p.enabled = False                         # strategies never run in the forecast profile
            continue
        s = pl.get(p.key) or {}
        p.reset_params()
        if s.get("params"):
            p.set_params(s["params"])
        if s.get("vote_tf") in TFS:
            p.obj.vote_tf = s["vote_tf"]
        p.set_chart_tfs(s.get("chart_tfs") or [])
        p.enabled = bool(s.get("chart"))
        p.scoring = bool(s.get("score")) and p.can_score
    h.scoring = clean_scoring(profile.get("scoring") or None)
    for attr in ("_hist", "_hist_an", "_hist_vote"):
        if hasattr(h, attr):
            getattr(h, attr).clear()
    return h


def profile_hub(src, profile, utc_offset=0):
    from superchart import Hub
    h = Hub(src, utc_offset=utc_offset)
    h.note = lambda *a, **k: None                     # the shadow analyser keeps no event log of its own
    return apply_profile(h, profile)


# ============================================================================ live service
class ForecastService:
    """Live wrapper: a closed-bar loop (1 s) + tick checks. Read-only: no trader, no gate, no orders."""

    def __init__(self, hub, journal_path=None):
        self.hub = hub
        self.settings = load_settings()
        self.engine = ForecastEngine(self.settings, ForecastJournal(journal_path or JOURNAL_FILE))
        self.last_bar_t = None
        self.last_tick_t = None
        self.status = dict(regime=None, why_not="starting")
        self.replay_job = dict(state="idle", progress=0.0, msg="", result=None)
        self.profile = load_profile()
        self._shadow = None
        self.running = True
        threading.Thread(target=self._loop, daemon=True).start()

    # ---------------- profile (own indicator setup, like a demo account)
    @property
    def view_hub(self):
        if self.profile["use_live"]:
            return self.hub
        if self._shadow is None:
            self._shadow = profile_hub(self.hub.src, self.profile, self.hub.utc_offset)
        self._shadow.utc_offset = self.hub.utc_offset
        return self._shadow

    def set_profile(self, new, copy_live=False):
        p = profile_from_hub(self.hub) if copy_live else dict(self.profile, **{k: v for k, v in (new or {}).items()
                                                                                 if k in ("use_live", "plugins", "scoring")})
        self.profile = save_profile(p)
        self._shadow = None                            # rebuilt on the next read with the new setup
        return dict(ok=True, profile=self.profile_view())

    def profile_view(self):
        h = self.view_hub
        return dict(use_live=self.profile["use_live"], scoring=h.scoring,
                    plugins=[dict(key=p.key, name=p.name, category=getattr(p.obj, "category", "Other"),
                                  can_score=p.can_score, votes=p.votes, module=getattr(p.obj, "analyser_module", None),
                                  score=p.scoring, chart=p.enabled, chart_tfs=list(p.chart_tfs),
                                  vote_tf=getattr(p.obj, "vote_tf", None), params=dict(p.obj.params),
                                  defaults=p.defaults, error=p.error)
                             for p in h.plugins if p.kind == "indicator"])

    def _view(self, t_end, extras=None):
        h = self.view_hub
        tf = self.settings["tf"]
        if h is self.hub:
            return market_view(h, t_end, extras, tf)
        with self.hub.lock:                            # the shadow analyser reads the same bars the live feed writes
            return market_view(h, t_end, extras, tf)

    def chart(self, tf="M15", n=400):
        """The forecast chart: candles + the forecast profile's indicators (CHART) + forecast levels / markers."""
        h = self.view_hub
        with self.hub.lock:
            if h is not self.hub and time.time() - getattr(h, "_fc_analysed", 0) > 2:
                h.analyze()                            # the profile's own analyser state for its drawings
                h._fc_analysed = time.time()
            out = h.chart(tf, n)
        if h is not self.hub:
            out["levels"] += [dict(lv, owner="forecast", type="forecast") for lv in self.overlay()]
        bars = out.get("bars") or []
        if bars:
            sec = TFS.get(tf, 900)
            t0 = bars[0][0]
            times = {b[0] for b in bars}
            mk = []
            for r in self.engine.journal.all()[-200:]:
                ft = TFS.get(r.get("tf"), 300)
                n_ = r["id"].split("-")[-1].lstrip("0") or "0"
                for t_, txt, col in ((r["created_at"] - ft, f"FC{n_}", "#c084fc"),
                                     (r.get("trigger_time"), f"FC{n_} IN", "#d8b4fe"),
                                     (r.get("closed_at") and r["closed_at"] - 1, f"FC{n_} {r.get('result') or ''}".strip(),
                                      "#26d7a0" if r.get("status") == "TP_HIT" else "#ff5a78" if r.get("status") == "SL_HIT"
                                      else "#94a3b8")):
                    if not t_:
                        continue
                    bt = int(t_) - int(t_) % sec
                    if bt < t0 or bt not in times:
                        continue
                    up = r["dir"] == 1
                    mk.append(dict(time=bt, position="belowBar" if up else "aboveBar", color=col,
                                   shape="arrowUp" if up else "arrowDown", text=txt, owner="forecast"))
            out["markers"] = sorted(out.get("markers", []) + mk, key=lambda m: m["time"])
        out["profile"] = "LIVE" if h is self.hub else "FORECAST"
        return out

    def update_settings(self, new):
        self.settings = clean_settings(dict(self.settings, **(new or {})))
        self.engine.cfg = self.settings
        try:
            with open(SETTINGS_FILE, "w") as f:
                json.dump(self.settings, f, indent=2)
        except OSError:
            pass
        return dict(ok=True, settings=self.settings)

    def _extras(self):
        hub = self.hub
        tk = hub.last_tick
        sp = round(tk.ask - tk.bid, 2) if tk else None
        spreads = list(getattr(hub, "spreads", []) or [])[-100:]
        med = sorted(spreads)[len(spreads) // 2] if spreads else None
        news = None
        risk = getattr(hub, "risk", None)
        if risk is not None:
            try:
                ns = risk.news_state()
                w = ns.get("warning") or {}
                news = ("HIGH" if ns["state"] == "NEWS_HIGH_IMPACT" else "UNKNOWN" if ns["state"] == "NEWS_UNKNOWN"
                        else "MEDIUM" if w and 0 <= w.get("minutes", 999) <= 120 else "LOW")
            except Exception:
                news = None
        return dict(spread=sp, spread_median=med, news=news, utc_offset=getattr(hub, "utc_offset", 0))

    def step(self):
        hub = self.hub
        tf = self.settings["tf"]
        bars = hub.src.bars(tf)
        if len(bars) < 70:
            return
        closed = bars[-2]
        if self.last_bar_t is None:
            self.last_bar_t = closed.t                # start from the next closed bar (no old history is forecast)
        if closed.t != self.last_bar_t:
            self.last_bar_t = closed.t
            view = self._view(closed.t + TFS[tf], self._extras())
            view["profile"] = "LIVE" if self.profile["use_live"] else "FORECAST"
            if self.settings["enabled"] or self.engine.active:
                cfg = self.engine.cfg
                if not self.settings["enabled"]:          # OFF: keep monitoring what exists, create nothing
                    cfg_enabled = cfg["enabled"]
                    cfg["enabled"] = False
                    self.engine.on_bar(view, closed)
                    cfg["enabled"] = cfg_enabled
                else:
                    self.engine.on_bar(view, closed)
            else:
                self.engine.get_regime(view)
            self.status = dict(regime=self.engine.last_regime, why_not=self.engine.last_why_not, t=time.time())
        tk = hub.last_tick
        if tk is not None and self.engine.active and tk.t_msc != self.last_tick_t:
            self.last_tick_t = tk.t_msc
            self.engine.on_tick(tk.t_msc / 1000, tk.bid, tk.ask)

    def _loop(self):
        time.sleep(5)
        while self.running:
            try:
                self.step()
            except Exception as e:
                self.hub.note("error", f"forecast: {e.__class__.__name__}: {e}", category="DECISION_ERROR")
            time.sleep(1.0)

    def create_now(self):
        """Evaluate the last closed bar right now (the same rules; no forced forecast when there is no setup)."""
        tf = self.settings["tf"]
        bars = self.hub.src.bars(tf)
        if len(bars) < 70:
            return dict(ok=False, error="not enough bars")
        view = self._view(bars[-2].t + TFS[tf], self._extras())
        view["profile"] = "LIVE" if self.profile["use_live"] else "FORECAST"
        self.engine.last_view = None
        made = self.engine.create_all(view, price=bars[-2].c, force=True)
        return dict(ok=bool(made), forecast=made[0] if made else None, created=[f["id"] for f in made],
                    why_not=self.engine.last_why_not)

    def run_replay(self, days=10, date_from=None, date_to=None):
        if self.replay_job["state"] == "running":
            return dict(ok=False, error="a replay is already running")
        self.replay_job = dict(state="running", progress=0.0, msg="loading history…", result=None)

        def work():
            try:
                import backtest
                from superchart import Hub
                src = self.hub.src
                t_from = t_to = None
                if date_from and date_to:
                    import calendar
                    p = lambda s_: calendar.timegm(time.strptime(str(s_)[:10], "%Y-%m-%d"))
                    t_from, t_to = p(date_from), p(date_to) + 86400
                if getattr(src, "live", False) and getattr(src, "mt5", None) is not None:
                    with self.hub.lock:
                        data = (backtest.load_mt5_range(src, t_from, t_to) if t_from
                                else backtest.load_mt5(src, int(days)))
                else:
                    data = backtest.synth_data(int(days) + 2)
                    if t_from:
                        data = {k: ([b for b in v if b.t < t_to] if isinstance(v, list) else v) for k, v in data.items()}
                h = Hub(backtest.HistSource(getattr(src, "symbol", "XAUUSD"), data), utc_offset=self.hub.utc_offset)
                if not self.profile["use_live"]:
                    apply_profile(h, self.profile)     # replay with the forecast's own indicator setup
                tf = self.settings["tf"]
                first = t_from or (data[tf][-1].t - int(days) * 86400)
                res = replay(h, tf, first, t_to, self.settings,
                             lambda f, m: self.replay_job.update(progress=f, msg=m))
                res["records"] = res["records"][-500:]
                self.replay_job.update(state="done", progress=1.0, msg="done", result=res)
            except Exception as e:
                self.replay_job.update(state="error", msg=f"{e.__class__.__name__}: {e}")

        threading.Thread(target=work, daemon=True).start()
        return dict(ok=True)

    def state(self):
        recs = self.engine.journal.all()
        tk = self.hub.last_tick
        act = []
        for fc in self.engine.active.values():
            a = dict(fc)
            if tk:
                px = tk.bid if fc["dir"] == 1 else tk.ask
                a.update(age_min=round((time.time() - fc["created_at"]) / 60, 1) if fc["created_at"] > 1e9 else None,
                         dist_entry=round(fc["entry"] - px, 2), dist_sl=round(px - fc["sl"], 2),
                         dist_tp=round(fc["tp2"] - px, 2), r_now=_r(fc, px) if fc.get("trigger_time") else None)
            act.append(a)
        return dict(settings=self.settings, regime=self.engine.last_regime, why_not=self.engine.last_why_not,
                    profile="LIVE" if self.profile["use_live"] else "FORECAST",
                    regime_history=list(self.engine.regime_hist)[:15], active=act,
                    recent=[r for r in reversed(recs) if r.get("status") not in ACTIVE][:12],
                    total=len(recs), read_only=True)

    def overlay(self):
        """Chart objects for active forecasts (drawn in violet, labelled FC: never mistaken for real orders)."""
        if not self.settings["draw"]:
            return []
        out = []
        for fc in self.engine.active.values():
            n = fc["id"].split("-")[-1].lstrip("0") or "0"
            out.append(dict(price=fc["entry"], color="#c084fc", label=f"FC{n} E", pane=""))
            out.append(dict(price=fc["sl"], color="#a855f7", label=f"FC{n} SL", pane=""))
            for k in ("tp1", "tp2", "tp3"):
                out.append(dict(price=fc[k], color="#e9d5ff", label=f"FC{n} {k.upper()}", pane=""))
            if fc.get("trigger") is not None:
                out.append(dict(price=fc["trigger"], color="#d8b4fe", label=f"FC{n} TRG", pane=""))
        return out

    def stop(self):
        self.running = False
