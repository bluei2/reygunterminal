"""
Strategy tester + optimizer for the Analyser Signals setup, on the master MT5's own history.

How it works
  1. LOAD     M5 / M15 / M30 / H1 / H4 / D1 bars of the last N days from the master MT5 (demo mode: synthetic data).
  2. SCORE    for every bar of the test timeframe, rebuild the analyser read as it was when that bar closed
              (only bars that had closed by then - no look-ahead), with YOUR current setup: indicators on SCORE,
              their parameters, SMC checks. Stored per bar: bias, trigger, range side, % of every group.
              This is the slow part and is done ONCE.
  3. REPLAY   any combination of passing score, group weights, SL / TP / trail, cautions, sessions, direction is
              then just arithmetic on the stored reads + the price bars -> thousands of combinations per minute.
  4. OPTIMIZE search on the first 70 % of the period (in-sample) and report the best setups also on the last 30 %
              (out-of-sample, never used for choosing) so over-fitted setups are visible.

Costs: every trade pays the spread (entry worse by the spread). One trade at a time. A bar that touches both SL and
TP counts as SL. $ P/L assumes 100 oz per lot (gold); R numbers do not depend on the lot.
Order flow / intermarket votes only exist where their data exists (recent hours / days), not in older history.
"""
from __future__ import annotations

import itertools
import json
import math
import random
import threading
import time
from datetime import datetime, timezone

import caution
import exits
from engine import TF_SECONDS, Bar
from sources import BaseSource

LOAD_TFS = ("M5", "M15", "M30", "H1", "H4", "D1")
import market_hours  # noqa: E402

SESSION_HOURS = market_hours.SESSIONS          # the same UTC table as live (market_hours)


# ============================================================================ data
class HistSource(BaseSource):
    name = "BACKTEST"

    def __init__(self, symbol, data, digits=2):
        super().__init__()
        self.symbol, self.digits, self.data = symbol, digits, data

    def bars(self, tf):
        return self.data.get(tf, [])


def _spreads(rates, digits):
    """Per-bar spread in price from MT5 rates ('spread' is in points), or None when the field is missing."""
    try:
        pt = 10 ** -int(digits or 2)
        return [float(r["spread"]) * pt for r in rates]
    except (KeyError, ValueError, TypeError, IndexError):
        return None


def load_mt5(src, days, skip_days=0, warm=620, with_m1=False):
    """Last `days` days (ending `skip_days` ago) of every TF from the master MT5, plus warm-up bars."""
    mt5 = src.mt5
    out = {"_spread": {}}
    for tf in (("M1",) if with_m1 else ()) + LOAD_TFS:
        key = "_m1" if tf == "M1" else tf
        per_day = 23 * 3600 // TF_SECONDS[tf] if tf != "D1" else 1
        start = skip_days * per_day
        count = days * per_day + warm
        rates = mt5.copy_rates_from_pos(src.symbol, src.tfmap[tf], start, count)
        if rates is None or len(rates) == 0:
            raise RuntimeError(f"MT5 gave no {tf} history ({mt5.last_error()}). Scroll the {tf} chart back in MT5 "
                               f"or raise 'Max bars in chart' (Tools > Options > Charts).")
        out[key] = [Bar(int(r["time"]), float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]),
                        float(r["tick_volume"])) for r in rates]
        out["_spread"][tf] = _spreads(rates, getattr(src, "digits", 2))
    return out


def load_mt5_range(src, t_from, t_to, warm=620, with_m1=False):
    """Every TF from t_from to t_to (broker SERVER time, like the MT5 Strategy Tester dates) plus warm-up bars
    before t_from. Fetched by position and sliced by bar time, so there is no UTC / server-time confusion."""
    mt5 = src.mt5
    last = src.bars("M1")[-1].t if src.bars("M1") else int(time.time())
    out, first_seen = {"_spread": {}}, None
    for tf in (("M1",) if with_m1 else ()) + LOAD_TFS:
        sec = TF_SECONDS[tf]
        count = min(99000, int((last - t_from) / sec) + warm + 50)
        rates = mt5.copy_rates_from_pos(src.symbol, src.tfmap[tf], 0, count)
        if rates is None or len(rates) == 0:
            raise RuntimeError(f"MT5 gave no {tf} history ({mt5.last_error()}).")
        keep = [r for r in rates if int(r["time"]) < t_to]
        bars = [Bar(int(r["time"]), float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]),
                    float(r["tick_volume"])) for r in keep]
        out["_m1" if tf == "M1" else tf] = bars
        out["_spread"][tf] = _spreads(keep, getattr(src, "digits", 2))
        if tf in ("M5", "M15", "M30", "H1") and bars:
            first_seen = max(first_seen or 0, bars[0].t)
    if first_seen and first_seen > t_from:
        raise RuntimeError(f"MT5 history only starts {time.strftime('%Y-%m-%d', time.gmtime(first_seen))} for "
                           f"the smaller timeframes. Raise 'Max bars in chart' (Tools > Options > Charts), scroll the "
                           f"M5 chart back to load older data, or pick a later From date.")
    return out


def synth_data(days, seed=5, price=2650.0):
    """Demo mode: gold-like M5 random walk with trending / ranging regimes, aggregated to the other TFs."""
    rnd = random.Random(seed)
    n = (days + 12) * 288
    t0 = int(time.time()) // 300 * 300 - n * 300
    m5, m1, drift, vol = [], [], 0.0, 0.9
    for i in range(n):
        if i % 96 == 0:
            drift = rnd.choice([-0.35, -0.15, 0, 0, 0.15, 0.35])
            vol = rnd.uniform(0.6, 1.4)
        o = price
        path = [o]
        for _ in range(5):
            price += drift * 0.2 + rnd.gauss(0, vol * 0.45)
            path.append(price)
        b5 = Bar(t0 + i * 300, o, max(path) + rnd.uniform(0, 0.3), min(path) - rnd.uniform(0, 0.3), price,
                 rnd.randint(40, 400))
        m5.append(b5)
        # five M1 bars along the same path; the M5 wick extremes go to the M1 bar that holds them
        sub = [[t0 + i * 300 + k * 60, path[k], max(path[k], path[k + 1]), min(path[k], path[k + 1]), path[k + 1]]
               for k in range(5)]
        max(sub, key=lambda x: x[2])[2] = b5.h
        min(sub, key=lambda x: x[3])[3] = b5.l
        m1.extend(Bar(t, o_, h_, l_, c_, b5.v / 5) for t, o_, h_, l_, c_ in sub)
    data = {"M5": m5, "_m1": m1}          # "_m1": intrabar replay only, never shown to the analyser
    for tf in LOAD_TFS[1:]:
        sec = TF_SECONDS[tf]
        agg = []
        for b in m5:
            bt = b.t - b.t % sec
            if agg and agg[-1].t == bt:
                a = agg[-1]
                agg[-1] = Bar(bt, a.o, max(a.h, b.h), min(a.l, b.l), b.c, a.v + b.v)
            else:
                agg.append(Bar(bt, b.o, b.h, b.l, b.c, b.v))
        data[tf] = agg
    return data


# ============================================================================ precompute
class Prepared:
    """Per-bar analyser reads + exit helpers for one test timeframe (step 2)."""

    def __init__(self, hub_factory, data, symbol, tf, days, progress=None, utc_offset=0, first_t=None):
        from superchart import Hub
        self.tf, self.sec, self.utc_offset = tf, TF_SECONDS[tf], utc_offset
        src = HistSource(symbol, data)
        self.hub = hub_factory(src) if hub_factory else Hub(src)
        bars = data[tf]
        self.bars = bars
        self.mtf = {k: data.get(k) for k in ("M5", "M15", "H1", "H4")}   # trend strategy BOS rules
        self.m1 = data.get("_m1") or []                    # intrabar replay (optional)
        self.m1_t = [b.t for b in self.m1]
        self.spread = (data.get("_spread") or {}).get(tf)  # per-bar broker spread in price (MT5 history) or None
        n = len(bars)
        first_t = first_t or bars[-1].t - days * 86400
        self.start = max(60, next((i for i, b in enumerate(bars) if b.t >= first_t), 60))
        self.reads = [None] * n
        self.groups = set()
        t0 = time.time()
        for k, i in enumerate(range(self.start, n - 1)):
            r = self.hub.read_at(bars[i].t + self.sec)
            loc_ok = "wrong side of range" not in (r.verdict or "")
            g = {x["category"]: x["pct"] for x in r.groups}
            self.groups.update(g)
            self.reads[i] = (r.bias, bool(r.trigger), loc_ok, g)
            if progress and k % 50 == 0:
                done = (k + 1) / max(1, n - 1 - self.start)
                progress(done, f"scoring bar {k + 1:,} / {n - 1 - self.start:,} "
                               f"({time.time() - t0:.0f}s)")
        self.scoring = self.hub.scoring
        # exit helpers (all causal: value at bar i only uses bars <= i)
        self.atr = exits.atr(bars, 14)
        self.vw, self.bands = exits.vwap_bands(bars)
        self.obs = exits.order_blocks(bars)
        self.cz = caution.Context(bars)
        self._caut = {}

    def smc_ok(self, i, side, mode):
        """SMC filter for bar i, side: the smc_engine view as it was when bar i closed (no look-ahead).
        Cached per bar; computed only when a setup uses the filter."""
        if not hasattr(self, "_smc"):
            self._smc = {}
        if i not in self._smc:
            try:
                self._smc[i] = self.hub.smc_at(self.bars[i].t + self.sec)
            except Exception:
                self._smc[i] = None
        r = self._smc[i]
        if not r or not r.get("ok"):
            return False
        su = (r.get("setups") or {}).get("BUY" if side == 1 else "SELL") or {}
        if su.get("status") not in ("WAITING_RETEST", "RETESTED"):
            return False
        if mode == "retested":
            return su["status"] == "RETESTED"
        if mode == "high":
            return su.get("quality") == "HIGH"
        return True

    def cautions(self, i, side):
        k = (i, side)
        if k not in self._caut:
            self._caut[k] = self.cz.check(i, side)
        return self._caut[k]

    def utc_hour(self, i):
        return ((self.bars[i].t // 3600) - self.utc_offset) % 24

    def utc_weekday(self, i):
        return datetime.fromtimestamp(self.bars[i].t - self.utc_offset * 3600, tz=timezone.utc).weekday()


# ============================================================================ replay one setup
DEFAULT_SETUP = dict(passing=70.0, weights=None, sl_mode="atr", atr_sl=1.5, swing_bars=10, sl_fixed=5.0, max_sl=10.0,
                     tp_source="rr", rr=2.0, min_rr=1.0, atr_tp1=2.0, trail="none", atr_trail=3.0,
                     max_cautions=-1, sessions=[], direction="both", buffer=0.5, be_r=0.0,
                     smc_filter="off",      # off | setup | retested | high  (the SAME smc_engine as live)
                     entry="analyser",      # analyser (READY) | reversal_ok (R✓ arrows) | reversal_any (R? + R✓)
                     rev_min_score=60,      # reversal arrows: minimum reversal score
                     rev_bias=True,         # reversal arrows: only with the HTF bias (end of a pullback)
                     rev_sl="wick",         # reversal arrows: "wick" = beyond the reversal extreme | "setup" = sl_mode
                     # REGIME ROUTER (entry="router"): trend regime -> analyser READY with the trend at router_trend_rr,
                     # range regime -> reversal arrows at router_range_rr, any other regime -> no trade
                     router_trend_rr=2.0, router_range_rr=1.0, router_rev="reversal_ok",
                     router_trend_labels=("TREND UP", "TREND DOWN"), router_range_labels=("RANGE",),
                     router_range_against_htf=False)
SMC_FILTERS = ("off", "setup", "retested", "high")
ENTRY_MODES = ("analyser", "reversal_ok", "reversal_any", "router", "st_trend", "st_pullback", "st_conso")


def strategy_array(P, setup, lo, hi):
    """entry='st_trend' | 'st_pullback' | 'st_conso': the Trading-page STRATEGIES (strategies.py, your settings).
    Trend / pullback: entry at the close of the signal bar, the strategy's own SL and RR.
    Conso: the pending BUY STOP / SELL STOP of the box fill on the first bar that touches one within expiry_bars
    (both touched in the same bar = skipped as ambiguous); the fill price is the stop (or the open on a gap)."""
    import strategies as stm
    name = setup["entry"][3:]
    scfg = (setup.get("strategy_cfg") or stm.load_settings())[name]
    key = ("strategy", name, json.dumps(scfg, sort_keys=True), lo, hi)
    cache = P.__dict__.setdefault("_rev_cache", {})
    if key in cache:
        out, P.sl_over, P.rr_over, P.entry_over = cache[key]
        return out
    bars = P.bars

    def bias_fn(i):
        rd = P.reads[i] if i < len(P.reads) else None
        return rd[0] if rd else 0
    sigs = stm.signals(name, bars, scfg, bias_fn if name != "conso" else None, lookback=len(bars),
                       mtf=getattr(P, "mtf", None))
    out, sl_o, rr_o, en_o = [0] * len(bars), {}, {}, {}
    for g in sigs:
        i = g["i"]
        if name != "conso":
            if lo <= i < hi:
                out[i], sl_o[i], rr_o[i] = g["side"], g["sl"], scfg["rr"]
            continue
        p = g["plan"]
        for k in range(i + 1, min(hi, i + 1 + int(scfg["expiry_bars"]))):
            b = bars[k]
            up, dn = b.h >= p["buy_stop"], b.l <= p["sell_stop"]
            if up and dn:
                break                                          # ambiguous: both stops in one bar
            if up or dn:
                side = 1 if up else -1
                stop = p["buy_stop"] if up else p["sell_stop"]
                fill = max(b.o, stop) if up else min(b.o, stop)
                if k >= lo:
                    out[k], en_o[k], rr_o[k] = side, fill, scfg["rr"]
                    sl_o[k] = fill - side * p["sl_dist"]
                break
    P.sl_over, P.rr_over, P.entry_over = sl_o, rr_o, en_o
    cache[key] = (out, sl_o, rr_o, en_o)
    return out


def regime_labels(P, lo, hi, rs=None):
    """(label, dir) of the regime (regime.py, your Regime settings) as of every bar's close - closed regime-TF bars
    only. One classification per regime-TF bar (cached)."""
    import bisect
    import regime as rg
    rs = rs or rg.load_settings()
    key = ("regime", json.dumps(rs, sort_keys=True), lo, hi)
    cache = P.__dict__.setdefault("_rev_cache", {})
    if key in cache:
        return cache[key]
    rtf = rs["tf"]
    rb = P.hub.src.bars(rtf) if getattr(P, "hub", None) is not None else []
    rsec = TF_SECONDS[rtf]
    times = [b.t for b in rb]
    per_k, out = {}, [("TRANSITION", 0)] * len(P.bars)
    for i in range(lo, hi):
        t_close = P.bars[i].t + P.sec
        k = bisect.bisect_right(times, t_close - rsec)          # regime-TF bars closed by this bar's close
        if k not in per_k:
            seg = rb[max(0, k - 400):k]
            c = rg.classify(seg, rs) if len(seg) > 60 else dict(label="TRANSITION", dir=0)
            per_k[k] = (c["label"], c.get("dir", 0))
        out[i] = per_k[k]
    cache[key] = out
    return out


def router_array(P, setup, lo, hi):
    """entry='router': trend regime -> analyser READY in the trend direction; range regime -> reversal arrows.
    Sets P.rr_over[i] (the RR of that entry) and P.rev_level[i] (wick stop of the arrows)."""
    labels = regime_labels(P, lo, hi)
    ready = ready_array(P, setup, lo, hi)
    rev = reversal_array(P, dict(setup, entry=setup.get("router_rev", "reversal_ok"),
                                 rev_bias=False), lo, hi)             # both directions; the bias rule is below
    lvl = dict(getattr(P, "rev_level", {}))
    out, rr, keep = [0] * len(P.bars), {}, {}
    prev = 0
    for i in range(lo, hi):
        label, d = labels[i]
        if label in setup.get("router_trend_labels", ()):
            v = ready[i] if ready[i] and (not d or ready[i] == d) else 0
            if v and v != prev:
                out[i], rr[i] = v, float(setup.get("router_trend_rr", 2.0))
            prev = ready[i]
        elif label in setup.get("router_range_labels", ()):
            prev = ready[i]
            v = rev[i]
            if not v:
                continue
            rd = P.reads[i]
            bias = rd[0] if rd else 0
            if bias == -v and not setup.get("router_range_against_htf", False):
                continue
            out[i], rr[i] = v, float(setup.get("router_range_rr", 1.0))
            if i in lvl:
                keep[i] = lvl[i]
        else:
            prev = ready[i]
    P.rr_over, P.rev_level = rr, keep
    return out


def reversal_array(P, setup, lo, hi):
    """+1 / -1 / 0 per bar: a Reversal arrow (the same detector and settings as the Reversal plugin).
      reversal_any : on the reversal bar itself (R? or later R✓)
      reversal_ok  : on the bar that CONFIRMED it (close through the reversal bar) - what R✓ auto trading uses
    Causal: the arrow on bar i only uses bars <= i (the confirmation bar for reversal_ok).
    The reversal extreme of every arrow is kept in P.rev_level[mode][i] for the 'wick' stop."""
    import reversal
    mode = setup.get("entry", "analyser")
    min_sc = float(setup.get("rev_min_score", 60))
    key = (mode, min_sc, bool(setup.get("rev_bias", True)), json.dumps(setup.get("rev_params") or {}, sort_keys=True), lo, hi)
    cache = P.__dict__.setdefault("_rev_cache", {})
    if key in cache:
        P.rev_level = cache[key][1]
        return cache[key][0]
    bars = P.bars
    det = P.__dict__.get("_rev_det")
    if det is None or getattr(det, "_pkey", None) != key[3]:
        det = reversal.Detector(bars, setup.get("rev_params") or None, cz=P.cz, utc_offset=P.utc_offset)
        det._pkey = key[3]
        P._rev_det = det
    idx = {b.t: k for k, b in enumerate(bars)}
    out, lvl = [0] * len(bars), {}
    for i in range(max(lo - 5, 0), hi):
        best = max((x for x in (det.evaluate(i, -1), det.evaluate(i, 1)) if x), key=lambda x: x["score"], default=None)
        if not best or best["score"] < min_sc:
            continue
        if mode == "reversal_ok":
            if not best["confirmed"]:
                continue
            k = idx.get(best["confirm_t"])
        else:
            k = i
        if k is None or not (lo <= k < hi):
            continue
        rd = P.reads[k]
        if setup.get("rev_bias", True) and (rd is None or rd[0] != best["side"]):
            continue
        out[k] = best["side"]
        lvl[k] = best["level"]
    P.rev_level = lvl
    cache[key] = (out, lvl)
    return out
COST_DEFAULTS = dict(spread_mode="fixed", commission_per_lot=0.0, slippage=0.0, swap_per_lot=0.0,
                     execution_delay_ms=0, intrabar=False)


def ready_array(P, setup, lo, hi):
    """+1 / -1 / 0 per bar: analyser READY with this passing score and these weights."""
    w = setup.get("weights") or P.scoring["weights"]
    passing = setup["passing"]
    out = [0] * len(P.bars)
    for i in range(lo, hi):
        rd = P.reads[i]
        if rd is None:
            continue
        bias, trig, loc, g = rd
        if not bias or not trig or not loc:
            continue
        ws = sum(w.get(c, 1.0) for c in g if w.get(c, 1.0) > 0)
        if ws <= 0:
            continue
        overall = sum(g[c] * w.get(c, 1.0) for c in g if w.get(c, 1.0) > 0) / ws
        if overall >= passing:
            out[i] = bias
    return out


def simulate(P, setup, lo=None, hi=None, ready=None, spread=0.3, keep_trades=True, costs=None, counters=None):
    """Trades for one setup on bars [lo, hi). Returns list of dicts.
    costs (optional, see COST_DEFAULTS): spread_mode fixed | variable (the broker's per-bar spread when the history
    has it), commission_per_lot ($ round trip), slippage ($ against you on the entry and on stop exits), swap_per_lot
    ($ per night held, positive = you pay), execution_delay_ms (> 0: fill at the NEXT bar's open instead of this
    bar's close), intrabar (SL / TP / BE checked on M1 bars; trailing still decided on closed bars).
    Without costs the result is exactly the classic model (spread paid at entry, bar-by-bar exits)."""
    s = dict(DEFAULT_SETUP)
    s.update(setup)
    c = dict(COST_DEFAULTS, **(costs or {}))
    bars = P.bars
    lo = P.start if lo is None else lo
    hi = len(bars) - 1 if hi is None else hi
    rev_mode = s.get("entry") in ("reversal_ok", "reversal_any", "router")
    router = s.get("entry") == "router"
    strat = str(s.get("entry", "")).startswith("st_")
    if ready is None:
        ready = strategy_array(P, s, lo, hi) if strat else router_array(P, s, lo, hi) if router else \
            reversal_array(P, s, lo, hi) if rev_mode else ready_array(P, s, lo, hi)
    rev_level = getattr(P, "rev_level", {}) if rev_mode else {}
    rr_over = getattr(P, "rr_over", {}) if (router or strat) else {}
    sl_over = getattr(P, "sl_over", {}) if strat else {}
    entry_over = getattr(P, "entry_over", {}) if strat else {}
    buf = s["buffer"]
    sess = s["sessions"]
    slip = float(c["slippage"] or 0)
    delay = float(c["execution_delay_ms"] or 0) > 0
    intrabar = bool(c["intrabar"]) and bool(getattr(P, "m1", None))
    var_sp = c["spread_mode"] == "variable" and getattr(P, "spread", None)
    sec = TF_SECONDS[P.tf]
    trades, prev, open_until = [], 0, -1
    cnt = counters if counters is not None else {}
    cnt.setdefault("generated", 0)
    cnt.setdefault("executed", 0)
    blk = cnt.setdefault("blocked", {})

    def block(code):
        blk[code] = blk.get(code, 0) + 1

    smc_mode = s.get("smc_filter", "off") if s.get("smc_filter", "off") in SMC_FILTERS else "off"
    for i in range(lo, hi):
        rd = ready[i]
        fresh = rd and (rd != prev or router or strat)       # router / strategy arrays hold single-bar entries
        prev = rd
        if not fresh:
            continue
        cnt["generated"] += 1                                   # a new READY signal on this bar
        if i <= open_until:
            block("ONE_TRADE_AT_A_TIME")
            continue
        side = rd
        if s["direction"] == "buy" and side != 1 or s["direction"] == "sell" and side != -1:
            block("BLOCKED_DIRECTION")
            continue
        if sess:
            h = P.utc_hour(i)
            if not any(market_hours.in_session(x, h) for x in sess):
                block("BLOCKED_SESSION")
                continue
        if s["max_cautions"] >= 0 and len(P.cautions(i, side)) > s["max_cautions"]:
            block("BLOCKED_CAUTIONS")
            continue
        if smc_mode != "off" and not P.smc_ok(i, side, smc_mode):
            block("BLOCKED_SMC")
            continue
        if delay and i + 1 >= len(bars):
            break
        sp = max(spread, P.spread[i]) if var_sp and P.spread[i] is not None else spread
        raw = entry_over[i] if i in entry_over else (bars[i + 1].o if delay else bars[i].c)
        entry = raw + side * (sp + slip)                    # pay the spread (+ slippage)
        a = P.atr[i] or 1.0
        # ---- stop
        sl = None
        if s["sl_mode"] == "atr":
            sl = entry - side * s["atr_sl"] * a
        elif s["sl_mode"] == "swing":
            sl = exits.swing_stop(bars, i, side, int(s["swing_bars"]), buf)
        elif s["sl_mode"] == "fixed":
            sl = entry - side * s["sl_fixed"]
        elif s["sl_mode"] in ("vwap", "ob"):
            pl = exits.exit_plan(side, entry, i, P.vw, P.bands, P.obs, tp_source="none", sl_source=s["sl_mode"],
                                 buffer=buf, min_dist=buf, max_sl=s["max_sl"], atr_vals=P.atr, bars=bars)
            sl = pl["sl"][0] if pl["sl"] else None
        if i in sl_over:                                         # the strategy's own stop
            sl = sl_over[i] - side * (sp + slip) if i in entry_over else sl_over[i]
        if rev_mode and s.get("rev_sl", "wick") == "wick" and i in rev_level:
            sl = rev_level[i] - side * max(buf, 0.1 * a)             # beyond the reversal extreme
            if (entry - sl) * side < 0.4 * a:
                sl = entry - side * 0.4 * a
        if sl is None or (entry - sl) * side <= 0:
            sl = entry - side * 1.5 * a
        if s["max_sl"] > 0 and abs(entry - sl) > s["max_sl"]:
            sl = entry - side * s["max_sl"]
        sl_d = abs(entry - sl)
        # ---- target
        tp = entry + side * sl_d * rr_over.get(i, s["rr"])
        if s["tp_source"] != "rr" and i not in rr_over:
            pl = exits.exit_plan(side, entry, i, P.vw, P.bands, P.obs, tp_source=s["tp_source"], sl_source="none",
                                 buffer=buf, min_dist=max(buf, s["min_rr"] * sl_d), max_sl=s["max_sl"],
                                 atr_vals=P.atr, atr_tp1=s["atr_tp1"], atr_tp2=s["atr_tp1"] + 1, bars=bars)
            if pl["tp1"]:
                tp = pl["tp1"][0]
        ev = [] if keep_trades else None
        t_fill = bars[i + 1].t if delay else bars[i].t + sec
        mode = "bar"
        if intrabar and P.m1_t and P.m1_t[0] <= t_fill <= P.m1_t[-1]:
            res = exits.replay_intrabar(P.m1, P.m1_t, bars, i, side, entry, sl, tp, t_fill, sec, s["trail"], P.vw,
                                        P.atr, s["atr_trail"], buf, s["be_r"], ev)
            mode = "M1"
        else:
            res = exits.replay(bars, i, side, entry, sl, tp, s["trail"], P.vw, P.atr, s["atr_trail"], buf,
                               s["be_r"], ev)
        if res["result"] == "open":
            block("END_OF_DATA")
            break                                           # end of data: stop (not counted)
        cnt["executed"] += 1
        k_exit = next((k for k in range(i + 1, len(bars)) if bars[k].t >= res["t_exit"] - sec + 1), len(bars))
        open_until = k_exit
        exit_px = res["exit"]
        if res["result"] in ("SL", "TRAIL", "BE"):
            exit_px -= side * slip                          # stop orders fill worse
        nights = max(0, int(res["t_exit"] // 86400) - int(t_fill // 86400))
        cost_pts = float(c["commission_per_lot"] or 0) / 100 + float(c["swap_per_lot"] or 0) * nights / 100
        pts = (exit_px - entry) * side - cost_pts
        r = round(pts / sl_d, 2) if sl_d else None
        if keep_trades:
            events = [dict(kind="ENTRY", t=t_fill, price=round(entry, 2), r=0.0, usd_per_lot=0.0)]
            for kind, t_e, px in ev:
                events.append(dict(kind=kind, t=t_e, price=px, r=round((px - entry) * side / sl_d, 2) if sl_d else None,
                                   usd_per_lot=round((px - entry) * side * 100, 2)))
            events.append(dict(kind=res["result"], t=res["t_exit"], price=round(exit_px, 2), r=r,
                               usd_per_lot=round(pts * 100, 2)))
            trades.append(dict(i=i, t=bars[i].t, side=side, entry=round(entry, 2), sl=round(sl, 2), tp=round(tp, 2),
                               exit=round(exit_px, 2), t_exit=res["t_exit"], result=res["result"], r=r,
                               pts=round(pts, 2), costs=round(cost_pts, 3), nights=nights, mode=mode, events=events))
        else:
            trades.append(dict(r=r, pts=pts, t=bars[i].t))
    return trades


def integrity(trades, daily_target_r=2.0):
    """Extra backtest-integrity figures (spec 39): loss rate, expectancy, average win / loss / planned RR,
    consecutive losses, worst day (daily drawdown in R) and the share of trading days reaching the daily target."""
    n = len(trades)
    if not n:
        return dict(loss_rate=None, expectancy_r=None, avg_win_r=None, avg_loss_r=None, avg_rr=None,
                    max_consec_losses=0, worst_day_r=None, trading_days=0, daily_target_r=daily_target_r,
                    daily_target_hit_rate=None)
    rs = [t.get("r") or 0.0 for t in trades]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    streak = best = 0
    for r in rs:
        streak = streak + 1 if r <= 0 else 0
        best = max(best, streak)
    days = {}
    for t in trades:
        d = int((t.get("t_exit") or t.get("t") or 0) // 86400)
        days.setdefault(d, []).append(t.get("r") or 0.0)
    day_dd = []
    for rl in days.values():
        cum = peak = dd = 0.0
        for r in rl:
            cum += r
            peak = max(peak, cum)
            dd = min(dd, cum - peak)
        day_dd.append(min(dd, cum))
    rr = [abs(t["tp"] - t["entry"]) / abs(t["entry"] - t["sl"]) for t in trades
          if t.get("tp") is not None and t.get("sl") is not None and t.get("entry") is not None and t["entry"] != t["sl"]]
    return dict(loss_rate=round(100 * len(losses) / n, 1), expectancy_r=round(sum(rs) / n, 3),
                avg_win_r=round(sum(wins) / len(wins), 3) if wins else None,
                avg_loss_r=round(sum(losses) / len(losses), 3) if losses else None,
                avg_rr=round(sum(rr) / len(rr), 2) if rr else None, max_consec_losses=best,
                worst_day_r=round(min(day_dd), 2) if day_dd else None, trading_days=len(days),
                daily_target_r=daily_target_r,
                daily_target_hit_rate=round(100 * sum(1 for rl in days.values() if sum(rl) >= daily_target_r) / len(days), 1)
                if daily_target_r else None)


def stats(trades, lot=0.01):
    n = len(trades)
    if not n:
        return dict(trades=0, wins=0, losses=0, win_rate=None, total_r=0.0, avg_r=None, pf=None, max_dd_r=0.0,
                    usd=0.0, calmar=None)
    rs = [t["r"] or 0.0 for t in trades]
    wins = [r for r in rs if r > 0]
    gl = -sum(r for r in rs if r < 0)
    cum = peak = dd = 0.0
    for r in rs:
        cum += r
        peak = max(peak, cum)
        dd = min(dd, cum - peak)
    tot = sum(rs)
    return dict(trades=n, wins=len(wins), losses=n - len(wins), win_rate=round(100 * len(wins) / n, 1),
                total_r=round(tot, 2), avg_r=round(tot / n, 3), pf=round(sum(wins) / gl, 2) if gl > 0 else None,
                max_dd_r=round(dd, 2), usd=round(sum(t["pts"] for t in trades) * 100 * lot, 2),
                calmar=round(tot / -dd, 2) if dd < 0 else None)


def monthly(trades):
    out = {}
    for t in trades:
        m = datetime.fromtimestamp(t["t"], tz=timezone.utc).strftime("%Y-%m")
        o = out.setdefault(m, dict(month=m, trades=0, wins=0, r=0.0))
        o["trades"] += 1
        o["wins"] += (t["r"] or 0) > 0
        o["r"] = round(o["r"] + (t["r"] or 0), 2)
    return list(out.values())


# ============================================================================ optimizer
OBJECTIVES = {
    "total_r": lambda st: st["total_r"],
    "profit_factor": lambda st: (st["pf"] or 0) if st["total_r"] > 0 else st["total_r"],
    "expectancy": lambda st: st["avg_r"] or -9,
    "calmar": lambda st: st["calmar"] if st["calmar"] is not None else (st["total_r"] * 10 if st["total_r"] > 0 else st["total_r"]),
}


def grid(opt_weights=False, groups=(), max_combos=2500, seed=1):
    combos = []
    for passing, (sl_mode, atr_sl), (tp_source, rr), trail, caut, sess in itertools.product(
            [55, 60, 65, 70, 75, 80, 85, 90],
            [("atr", 1.0), ("atr", 1.5), ("atr", 2.0), ("swing", 1.5)],
            [("rr", 1.0), ("rr", 1.5), ("rr", 2.0), ("rr", 2.5), ("rr", 3.0), ("atr", 2.0), ("vwap", 2.0)],
            ["none", "atr"], [-1, 0, 1], [[], ["London", "New York"]]):
        combos.append(dict(passing=float(passing), sl_mode=sl_mode, atr_sl=atr_sl, tp_source=tp_source, rr=rr,
                           trail=trail, max_cautions=caut, sessions=sess))
    rnd = random.Random(seed)
    if opt_weights and groups:
        for c in combos:
            c["weights"] = {g: rnd.choice([0.0, 1.0, 1.0, 2.0]) if g != "Structure" else rnd.choice([1.0, 2.0])
                            for g in groups}
    if len(combos) > max_combos:
        combos = rnd.sample(combos, max_combos)
    return combos


def neighbors(c):
    """Small changes of one parameter at a time (parameter sensitivity)."""
    out = []
    for k, deltas in (("passing", (-5, 5)), ("atr_sl", (-0.25, 0.25)), ("rr", (-0.5, 0.5)), ("atr_trail", (-0.5, 0.5))):
        if k == "atr_sl" and c.get("sl_mode") != "atr" or k == "rr" and c.get("tp_source") != "rr" \
                or k == "atr_trail" and c.get("trail") != "atr":
            continue
        for d in deltas:
            v = round(c.get(k, DEFAULT_SETUP[k]) + d, 2)
            if v <= 0 or (k == "passing" and not 30 <= v <= 95):
                continue
            out.append((f"{k} {'+' if d > 0 else ''}{d:g}", dict(c, **{k: v})))
    return out


def sensitivity(P, c, lo, hi, spread, lot, costs, objective):
    """Objective of the setup and of its neighbours on [lo, hi): stable = small changes give similar results."""
    score = OBJECTIVES.get(objective, OBJECTIVES["total_r"])
    base = stats(simulate(P, c, lo, hi, None, spread, False, costs), lot)
    b = score(base)
    rows = []
    for name, cc in neighbors(c):
        st = stats(simulate(P, cc, lo, hi, None, spread, False, costs), lot)
        rows.append(dict(change=name, total_r=st["total_r"], pf=st["pf"], trades=st["trades"], score=round(score(st), 3)))
    if not rows:
        return dict(base=round(b, 3), rows=[], stable=None, flag="")
    vals = sorted(x["score"] for x in rows)
    med = vals[len(vals) // 2]
    flip = b > 0 and any(x["total_r"] < 0 for x in rows)
    weak = b > 0 and med < 0.5 * b
    return dict(base=round(b, 3), rows=rows, median=round(med, 3), stable=not (flip or weak),
                flag="POSSIBLE OVERFIT" if flip or weak else "")


def optimize(P, objective="total_r", min_trades=15, is_frac=0.7, spread=0.3, lot=0.01, opt_weights=False,
             progress=None, max_combos=2500, top=20, lo=None, hi=None, costs=None, with_sensitivity=True):
    lo = P.start if lo is None else lo
    hi = len(P.bars) - 1 if hi is None else hi
    split = lo + int((hi - lo) * is_frac)
    combos = grid(opt_weights, sorted(P.groups), max_combos)
    score = OBJECTIVES.get(objective, OBJECTIVES["total_r"])
    ready_cache = {}
    results = []
    t0 = time.time()
    for k, c in enumerate(combos):
        rk = (c["passing"], tuple(sorted((c.get("weights") or {}).items())))
        if rk not in ready_cache:
            ready_cache[rk] = ready_array(P, dict(DEFAULT_SETUP, **c), lo, hi)
        tr = simulate(P, c, lo, split, ready_cache[rk], spread, keep_trades=False, costs=costs)
        st = stats(tr, lot)
        if st["trades"] >= min_trades:
            results.append((score(st), c, st, rk))
        if progress and k % 40 == 0:
            progress((k + 1) / len(combos), f"optimizing {k + 1:,} / {len(combos):,} setups ({time.time() - t0:.0f}s)")
    results.sort(key=lambda x: x[0], reverse=True)
    uniq, seen = [], set()
    for row in results:                                   # same trades = same result: keep the first only
        fp = (row[2]["trades"], row[2]["wins"], row[2]["total_r"], row[2]["max_dd_r"])
        if fp in seen:
            continue
        seen.add(fp)
        uniq.append(row)
    out = []
    for sc, c, st, rk in uniq[:top * 3]:
        oos = stats(simulate(P, c, split, hi, ready_cache[rk], spread, keep_trades=False, costs=costs), lot)
        full = stats(simulate(P, c, lo, hi, ready_cache[rk], spread, keep_trades=False, costs=costs), lot)
        robust = oos["trades"] >= max(3, min_trades // 4) and oos["total_r"] > 0 and (oos["pf"] or 0) >= 1.1
        out.append(dict(setup=c, is_=st, oos=oos, full=full, score=round(sc, 3), robust=robust))
    # robust ones first (still ranked by in-sample), then the rest
    out = ([r for r in out if r["robust"]] + [r for r in out if not r["robust"]])[:top]
    if with_sensitivity:
        for k, row in enumerate(out[:8]):                  # the top rows: does a small change break it?
            if progress:
                progress(0.99, f"parameter sensitivity {k + 1}/{min(8, len(out))}")
            row["sensitivity"] = sensitivity(P, row["setup"], lo, hi, spread, lot, costs, objective)
    split_t = P.bars[split].t
    return dict(rows=out, tested=len(combos), valid=len(results), unique=len(uniq), split_t=split_t, objective=objective,
                min_trades=min_trades)


# ============================================================================ job (runs in the background)
class BacktestJob:
    def __init__(self, hub):
        self.hub = hub
        self.state = "idle"
        self.progress = 0.0
        self.msg = ""
        self.result = None
        self.prepared = None
        self.last_wf = None
        self.prep_key = None
        self.lock = threading.Lock()

    def status(self):
        return dict(state=self.state, progress=round(self.progress, 3), msg=self.msg, result=self.result)

    def start(self, req):
        if self.state == "running":
            return dict(ok=False, error="a test is already running")
        self.state, self.progress, self.msg, self.result = "running", 0.0, "starting…", None
        threading.Thread(target=self._run, args=(req,), daemon=True).start()
        return dict(ok=True)

    def _p(self, frac, msg):
        self.progress, self.msg = frac, msg

    def current_setup(self):
        """The live setup: Analyser Signals params + scoring + auto-trade filters."""
        hub = self.hub
        p = next((x for x in hub.plugins if x.key == "analyser_signals.AnalyserSignals"), None)
        ap = dict(p.obj.params) if p else {}
        at = getattr(hub, "autotrader", None)
        ats = at.settings if at else {}
        return dict(passing=float(ap.get("min_score") or hub.scoring["passing"]), weights=dict(hub.scoring["weights"]),
                    sl_mode=ap.get("sl_mode", "atr"), atr_sl=ap.get("atr_sl", 1.5), swing_bars=ap.get("swing_bars", 10),
                    sl_fixed=ap.get("sl_fixed", 5.0), max_sl=ap.get("max_sl", 10.0),
                    tp_source=ap.get("tp_source", "rr") if ap.get("tp_source") in ("rr", "atr", "vwap", "ob", "all") else "rr",
                    rr=ap.get("rr", 2.0), min_rr=ap.get("min_rr", 1.0), atr_tp1=ap.get("atr_tp1", 2.0),
                    trail=ap.get("trail", "none"), atr_trail=ap.get("atr_trail", 3.0),
                    max_cautions=ats.get("max_cautions", ap.get("max_cautions", -1)),
                    sessions=list(ats.get("sessions", [])), direction=ats.get("direction", "both"),
                    # the router / arrow settings of AUTO TRADE, so "router" backtests what AUTO would do
                    router_trend_rr=float(ats.get("router_trend_rr", 2.0)),
                    router_range_rr=float(ats.get("router_range_rr", 1.0)),
                    router_trend_labels=tuple(ats.get("router_trend_labels", ("TREND UP", "TREND DOWN"))),
                    router_range_labels=tuple(ats.get("router_range_labels", ("RANGE",))),
                    router_range_against_htf=bool(ats.get("router_range_against_htf", False)),
                    router_rev="reversal_ok" if ats.get("reversal_confirmed_only", True) else "reversal_any",
                    rev_min_score=float(ats.get("reversal_min_score", 60)))

    def _run(self, req):
        try:
            hub = self.hub
            days = max(3, min(365, int(req.get("days", 30))))
            skip = max(0, min(365, int(req.get("skip_days", 0))))
            t_from = t_to = None
            if req.get("date_from") and req.get("date_to"):
                import calendar
                parse = lambda d: calendar.timegm(time.strptime(str(d)[:10], "%Y-%m-%d"))
                t_from, t_to = parse(req["date_from"]), parse(req["date_to"]) + 86400      # "to" day included
                if t_to <= t_from:
                    raise RuntimeError("To date must be after From date")
                if t_to - t_from > 366 * 86400:
                    raise RuntimeError("date range too long (max 1 year)")
                days = max(1, round((t_to - t_from) / 86400))
            tf = req.get("tf", "M5") if req.get("tf") in ("M5", "M15", "M30", "H1") else "M5"
            spread = max(0.0, float(req.get("spread", 0.30)))
            lot = max(0.01, float(req.get("lot", 0.01)))
            costs = dict(COST_DEFAULTS)
            for k in COST_DEFAULTS:
                if k in req and req[k] is not None:
                    costs[k] = (req[k] in (True, "true", "1", 1)) if k == "intrabar" else \
                               (str(req[k]) if k == "spread_mode" else max(0.0, float(req[k])))
            if costs["spread_mode"] not in ("fixed", "variable"):
                costs["spread_mode"] = "fixed"
            with_m1 = bool(costs["intrabar"])
            key = (days, skip, t_from, t_to, tf, with_m1, hub._score_sig() if hasattr(hub, "_score_sig") else "")
            if self.prepared is None or self.prep_key != key:
                self._p(0.01, "loading history…")
                if t_from and getattr(hub.src, "live", False) and getattr(hub.src, "mt5", None) is not None:
                    with hub.lock:
                        data = load_mt5_range(hub.src, t_from, t_to, with_m1=with_m1)
                    source = f"MT5 {hub.src.symbol}"
                elif t_from:
                    back = int((time.time() - t_from) / 86400) + 2
                    if back > 400:
                        raise RuntimeError("demo mode only simulates the last ~400 days")
                    data = synth_data(back)
                    data = {k: ([b for b in v if b.t < t_to] if isinstance(v, list) else v) for k, v in data.items()}
                    if not data["M5"] or data["M5"][-1].t < t_from:
                        raise RuntimeError("no simulated data in that date range (demo data ends now)")
                    source = "SIMULATED data (demo mode)"
                elif getattr(hub.src, "live", False) and getattr(hub.src, "mt5", None) is not None:
                    with hub.lock:
                        data = load_mt5(hub.src, days, skip, with_m1=with_m1)
                    source = f"MT5 {hub.src.symbol}"
                else:
                    data = synth_data(days + skip)
                    if skip:
                        cut = data["M5"][-1].t - skip * 86400
                        data = {k: ([b for b in v if b.t <= cut] if isinstance(v, list) else v) for k, v in data.items()}
                    source = "SIMULATED data (demo mode)"
                self._p(0.03, f"scoring {tf} bars with your current setup…")
                heavy = req.get("mode") in ("optimize", "walkforward")
                self.prepared = Prepared(None, data, hub.src.symbol, tf, days,
                                         lambda f, m: self._p(0.03 + 0.42 * f if heavy else 0.03 + 0.9 * f, m),
                                         utc_offset=hub.utc_offset, first_t=t_from)
                self.prepared.source = source
                self.prep_key = key
            P = self.prepared
            setup = dict(DEFAULT_SETUP, **self.current_setup())
            setup.update({k: v for k, v in (req.get("setup") or {}).items() if k in DEFAULT_SETUP})
            if setup.get("entry") not in ENTRY_MODES:
                setup["entry"] = "analyser"
            if setup["entry"].startswith("st_"):
                import strategies as stm
                setup["strategy_cfg"] = stm.load_settings()             # your Trading-page strategy settings
            rp = next((x for x in hub.plugins if x.key == "reversal_signals.ReversalSignals"), None)
            if rp is not None and setup["entry"] != "analyser":
                setup["rev_params"] = rp.obj._det_params()           # the SAME arrows as on your chart
            cnt = {}
            trades = simulate(P, setup, spread=spread, costs=costs, counters=cnt)
            try:
                dt_r = max(0.0, float(req.get("daily_target_r", 2.0)))
            except (TypeError, ValueError):
                dt_r = 2.0
            st_ = stats(trades, lot)
            st_.update(integrity(trades, dt_r))
            base = dict(setup=setup, stats=st_, monthly=monthly(trades), trades=trades[-400:],
                        signals=dict(generated=cnt.get("generated", 0), executed=cnt.get("executed", 0),
                                     blocked=sum(cnt.get("blocked", {}).values()), by_reason=cnt.get("blocked", {})),
                        curve=_curve(trades), r_list=[t["r"] for t in trades if t.get("r") is not None],
                        intrabar_trades=sum(1 for t in trades if t.get("mode") == "M1"),
                        m1_available=bool(P.m1), variable_spread_available=bool(P.spread))
            res = dict(mode=req.get("mode", "backtest"), source=P.source, tf=tf, days=days, skip_days=skip,
                       date_from=req.get("date_from") if t_from else None, date_to=req.get("date_to") if t_from else None,
                       spread=spread, lot=lot, costs=costs, bars=len(P.bars) - P.start,
                       period=[P.bars[P.start].t, P.bars[-1].t], groups=sorted(P.groups), current=base)
            if req.get("mode") == "optimize":
                self._p(0.46, "optimizing…")
                res["opt"] = optimize(P, req.get("objective", "total_r"), int(req.get("min_trades", 15)),
                                      spread=spread, lot=lot, opt_weights=bool(req.get("opt_weights")),
                                      progress=lambda f, m: self._p(0.46 + 0.53 * f, m), costs=costs)
            elif req.get("mode") == "walkforward":
                import walkforward
                self._p(0.46, "walk-forward…")
                res["wf"] = walkforward.run(P, float(req.get("train_days", 20)), float(req.get("test_days", 5)),
                                            req.get("objective", "total_r"), int(req.get("min_trades", 8)), spread, lot,
                                            costs, bool(req.get("opt_weights")), int(req.get("max_combos", 600)),
                                            lambda f, m: self._p(0.46 + 0.53 * f, m))
                self.last_wf = res["wf"]
            self.result = res
            self.state, self.progress, self.msg = "done", 1.0, "done"
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.state, self.msg = "error", f"{e.__class__.__name__}: {e}"

    def apply(self, setup):
        """Write a setup into the live terminal: Analyser Signals params, scoring, auto-trade filters."""
        hub = self.hub
        p = next((x for x in hub.plugins if x.key == "analyser_signals.AnalyserSignals"), None)
        if p:
            hub.set_plugin(p.key, params={k: setup[k] for k in ("sl_mode", "atr_sl", "tp_source", "rr", "trail",
                                                                "atr_trail", "swing_bars", "max_sl") if k in setup}
                           | {"min_score": 0.0})
        sc = dict(passing=setup.get("passing", hub.scoring["passing"]),
                  weights=setup.get("weights") or hub.scoring["weights"])
        hub.set_scoring(sc)
        at = getattr(hub, "autotrader", None)
        if at:
            at.update_settings(dict(max_cautions=setup.get("max_cautions", -1), sessions=setup.get("sessions", []),
                                    direction=setup.get("direction", "both")))
        hub.note("info", f"backtest setup applied: pass {sc['passing']:.0f}% · SL {setup.get('sl_mode')} · "
                         f"TP {setup.get('tp_source')} {setup.get('rr')} · trail {setup.get('trail')} · "
                         f"max ⚠ {setup.get('max_cautions')} · sessions {setup.get('sessions') or 'all'}")
        self.prepared = None                      # scoring changed -> next test re-scores
        return dict(ok=True)


def _curve(trades):
    cum, out = 0.0, []
    for t in trades:
        cum += t["r"] or 0
        out.append([t["t_exit"] or t["t"], round(cum, 2)])
    return out
