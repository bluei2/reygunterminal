"""
Exit levels: where a trade could take profit, put its stop, or trail its stop.

  * VWAP bands  - daily VWAP (server day) with +/-1, 2, 3 standard-deviation bands (tick volume on MT5 gold).
                  Common use: TP at the next band in the trade direction, SL beyond VWAP / the band behind,
                  trail the stop with VWAP.
  * Order blocks - the last opposite-colour candle before a move that BREAKS STRUCTURE (close beyond the latest
                  swing high / low). Bullish OB = last bearish candle before a break up; bearish OB = last bullish
                  candle before a break down. Active until price CLOSES through it.
                  A bullish OB below price = support (stop goes under it); a bearish OB above = target / resistance.
  * ATR         - volatility distance: SL = entry -/+ atr_sl x ATR, TP = entry +/- atr_tp1 / atr_tp2 x ATR,
                  trail = chandelier (highest high since entry - atr_trail x ATR for a BUY).
  * exit_plan() - picks TP1 / TP2 / SL / trail for a BUY or SELL at a price from those levels.
    Sources: "vwap", "ob", "atr", combinations with "+" (e.g. "vwap+atr"), "both" (= vwap+ob) or "all".
"""
from __future__ import annotations


# ---------------------------------------------------------------- VWAP
def vwap_bands(bars, mults=(1.0, 2.0, 3.0)):
    """Returns (vwap, {+m: upper list, -m: lower list}) with one value per bar; None at the first bar of a day."""
    n = len(bars)
    vw = [None] * n
    bands = {s * m: [None] * n for m in mults for s in (1, -1)}
    day = None
    pv = vv = pv2 = 0.0
    for i, b in enumerate(bars):
        d = b.t // 86400
        if d != day:
            day, pv, vv, pv2 = d, 0.0, 0.0, 0.0
        tp = (b.h + b.l + b.c) / 3
        v = b.v or 1.0
        pv += tp * v
        vv += v
        pv2 += tp * tp * v
        m = pv / vv
        sd = max(0.0, pv2 / vv - m * m) ** 0.5
        vw[i] = m
        for k in bands:
            bands[k][i] = m + k * sd
    return vw, bands


def vwap_levels_at(vw, bands, i):
    """All VWAP levels at bar i as [(price, name)], e.g. (2651.2, 'VWAP'), (2653.0, '+1σ')."""
    if i < 0 or vw[i] is None:
        return []
    out = [(vw[i], "VWAP")]
    for k, arr in bands.items():
        if arr[i] is not None:
            out.append((arr[i], f"{'+' if k > 0 else '-'}{abs(k):g}σ"))
    return out


# ---------------------------------------------------------------- order blocks
def order_blocks(bars, swing=3, use_body=False, max_age=300):
    """List of dicts: side (+1 bull / -1 bear), lo, hi, i (OB candle), created (bar index of the break),
    broken (bar index where a close went through it, or None)."""
    n = len(bars)
    obs = []
    last_hi = last_lo = None          # (index, price) of the latest CONFIRMED swing
    used_hi = used_lo = None
    for i in range(n):
        j = i - swing                 # a swing at j is confirmed once `swing` bars after it exist
        if j >= swing:
            h, l = bars[j].h, bars[j].l
            if all(h >= bars[x].h for x in range(j - swing, j + swing + 1) if x != j):
                last_hi = (j, h)
            if all(l <= bars[x].l for x in range(j - swing, j + swing + 1) if x != j):
                last_lo = (j, l)
        b = bars[i]
        # break of structure up -> bullish OB = last bearish candle before the break
        if last_hi and last_hi != used_hi and b.c > last_hi[1]:
            used_hi = last_hi
            for k in range(i - 1, max(last_hi[0] - 10, 0) - 1, -1):
                if bars[k].c < bars[k].o:
                    c = bars[k]
                    lo, hi = (min(c.o, c.c), max(c.o, c.c)) if use_body else (c.l, c.h)
                    obs.append(dict(side=1, lo=lo, hi=hi, i=k, t=c.t, created=i, broken=None))
                    break
        if last_lo and last_lo != used_lo and b.c < last_lo[1]:
            used_lo = last_lo
            for k in range(i - 1, max(last_lo[0] - 10, 0) - 1, -1):
                if bars[k].c > bars[k].o:
                    c = bars[k]
                    lo, hi = (min(c.o, c.c), max(c.o, c.c)) if use_body else (c.l, c.h)
                    obs.append(dict(side=-1, lo=lo, hi=hi, i=k, t=c.t, created=i, broken=None))
                    break
        # invalidation: a close through the block
        for ob in obs:
            if ob["broken"] is None and i > ob["created"]:
                if (ob["side"] == 1 and b.c < ob["lo"]) or (ob["side"] == -1 and b.c > ob["hi"]):
                    ob["broken"] = i
                elif i - ob["created"] > max_age:
                    ob["broken"] = i                  # too old, stop using it
    return obs


def active_obs(obs, i):
    return [o for o in obs if o["created"] <= i and (o["broken"] is None or o["broken"] > i)]


# ---------------------------------------------------------------- ATR
def atr(bars, n=14):
    """Wilder ATR, one value per bar (None until n bars)."""
    out = [None] * len(bars)
    trs, a = [], None
    for i, b in enumerate(bars):
        tr = b.h - b.l if i == 0 else max(b.h - b.l, abs(b.h - bars[i - 1].c), abs(b.l - bars[i - 1].c))
        if a is None:
            trs.append(tr)
            if len(trs) == n:
                a = sum(trs) / n
        else:
            a = (a * (n - 1) + tr) / n
        out[i] = a
    return out


def chandelier(bars, atr_vals, side, i, lookback=22, mult=3.0):
    """Chandelier stop at bar i: BUY = highest high of the last `lookback` bars - mult x ATR."""
    if atr_vals[i] is None:
        return None
    seg = bars[max(0, i - lookback + 1):i + 1]
    if side == 1:
        return max(b.h for b in seg) - mult * atr_vals[i]
    return min(b.l for b in seg) + mult * atr_vals[i]


def sources(src):
    src = str(src or "none").lower()
    if src == "all":
        return {"vwap", "ob", "atr"}
    if src == "both":
        return {"vwap", "ob"}
    return set(src.replace(",", "+").split("+"))


# ---------------------------------------------------------------- plan
def exit_plan(side, entry, i, vw=None, bands=None, obs=None, tp_source="vwap", sl_source="vwap",
              buffer=0.5, min_dist=0.5, max_sl=10.0, atr_vals=None, atr_sl=1.5, atr_tp1=2.0, atr_tp2=3.0,
              bars=None, atr_trail=3.0):
    """TP1, TP2, SL (and their names) for a trade of `side` entered at `entry` on bar i.
    Anything not found is None (the caller falls back to its own rule)."""
    vlev = vwap_levels_at(vw, bands, i) if vw is not None else []
    olev_t, olev_s = [], []
    if obs is not None:
        for o in active_obs(obs, i):
            if o["side"] == -side:          # opposite block in front of us = target (its near edge)
                edge = o["lo"] if side == 1 else o["hi"]
                olev_t.append((edge, "Bear OB" if side == 1 else "Bull OB"))
            else:                           # same-side block behind us = stop goes beyond it
                edge = o["lo"] - buffer if side == 1 else o["hi"] + buffer
                olev_s.append((edge, "Bull OB" if side == 1 else "Bear OB"))

    def ahead(levels):
        xs = [(p, n) for p, n in levels if (p - entry) * side >= min_dist]
        return sorted(xs, key=lambda x: (x[0] - entry) * side)

    def behind(levels):
        xs = [(p, n) for p, n in levels if (entry - p) * side >= min_dist]
        return sorted(xs, key=lambda x: (entry - x[0]) * side)

    a = atr_vals[i] if atr_vals is not None and 0 <= i < len(atr_vals) else None
    alev_t = [(entry + side * m * a, f"{m:g}×ATR") for m in (atr_tp1, atr_tp2) if a and m > 0]
    alev_s = [(entry - side * atr_sl * a, f"{atr_sl:g}×ATR")] if a and atr_sl > 0 else []
    ts, ss = sources(tp_source), sources(sl_source)
    t_src = (vlev if "vwap" in ts else []) + (olev_t if "ob" in ts else []) + (alev_t if "atr" in ts else [])
    s_src = ([(p - side * buffer, n) for p, n in vlev] if "vwap" in ss else []) + \
            (olev_s if "ob" in ss else []) + (alev_s if "atr" in ss else [])
    tps = ahead(t_src)
    sls = [x for x in behind(s_src) if max_sl <= 0 or abs(entry - x[0]) <= max_sl]
    trail = None
    if vw is not None and vw[i] is not None and (entry - vw[i]) * side > 0:
        trail = (vw[i], "VWAP")
    if a and bars is not None:
        ch = chandelier(bars, atr_vals, side, i, mult=atr_trail)
        if ch is not None and (entry - ch) * side > 0 and (trail is None or (ch - trail[0]) * side > 0):
            trail = (ch, "ATR trail")                # the tighter of the two (still behind price)
    return dict(tp1=tps[0] if tps else None, tp2=tps[1] if len(tps) > 1 else None,
                sl=sls[0] if sls else None, trail=trail, atr=a)


# ---------------------------------------------------------------- trade replay (shared by signal plugins)
def swing_stop(bars, i, side, lookback=10, buffer=0.5):
    """Stop beyond the lowest low (BUY) / highest high (SELL) of the last `lookback` bars up to i."""
    seg = bars[max(0, i - lookback + 1):i + 1]
    return (min(b.l for b in seg) - buffer) if side == 1 else (max(b.h for b in seg) + buffer)


def _trail_level(bars, i, k, side, trail, vw, atr_vals, atr_trail, buffer):
    """Trailing stop candidate from the TF bar k that just CLOSED (uses bars i..k only: causal)."""
    if trail == "atr" and atr_vals is not None and atr_vals[k] is not None:
        seg = bars[i:k + 1]
        return (max(x.h for x in seg) - atr_trail * atr_vals[k]) if side == 1 else \
               (min(x.l for x in seg) + atr_trail * atr_vals[k])
    if trail == "vwap" and vw is not None and vw[k] is not None:
        return vw[k] - side * buffer
    return None


def replay(bars, i, side, entry, sl, tp, trail="none", vw=None, atr_vals=None, atr_trail=3.0, buffer=0.5,
           be_r=0.0, events=None):
    """Walk the bars after i: first of SL / TP (both in one bar = SL, to be safe). trail 'vwap' / 'atr' only ever
    tightens the stop, from the bar that closed (never earlier). be_r > 0: stop to entry once price reached
    entry + be_r x risk (applied from the next bar). events: optional list collecting (kind, t, price).
    Returns dict(result TP|SL|TRAIL|BE|open, t_exit, exit, cur_sl, r)."""
    sl_d = abs(entry - sl)
    cur = sl
    be_done = False
    for k in range(i + 1, len(bars)):
        b = bars[k]
        if (b.l <= cur) if side == 1 else (b.h >= cur):
            res = "BE" if be_done and abs(cur - entry) < 1e-9 else ("TRAIL" if (cur - sl) * side > 1e-9 else "SL")
            return dict(result=res, t_exit=b.t, exit=cur, cur_sl=cur,
                        r=round((cur - entry) * side / sl_d, 2) if sl_d else None)
        if (b.h >= tp) if side == 1 else (b.l <= tp):
            return dict(result="TP", t_exit=b.t, exit=tp, cur_sl=cur,
                        r=round((tp - entry) * side / sl_d, 2) if sl_d else None)
        if be_r and not be_done and sl_d and (((b.h - entry) if side == 1 else (entry - b.l)) >= be_r * sl_d) \
                and (cur - entry) * side < 0:
            cur, be_done = entry, True
            if events is not None:
                events.append(("BE", b.t, entry))
        new = _trail_level(bars, i, k, side, trail, vw, atr_vals, atr_trail, buffer)
        if new is not None and (new - cur) * side > 0 and (b.c - new) * side > 0:
            cur = new
            if events is not None:
                events.append(("TRAIL_UPDATE", b.t, round(new, 2)))
    return dict(result="open", t_exit=None, exit=None, cur_sl=cur, r=None)


def replay_intrabar(m1, m1_t, bars, i, side, entry, sl, tp, t_start, sec, trail="none", vw=None, atr_vals=None,
                    atr_trail=3.0, buffer=0.5, be_r=0.0, events=None):
    """Like replay() but SL / TP / BE are checked on M1 bars from t_start (the moment of entry). Trailing and BE
    levels are still decided on CLOSED signal-TF bars (the same causal rule) and applied to the following M1 bars.
    Not tick-exact: inside one M1 bar with both SL and TP, SL is assumed first."""
    import bisect
    sl_d = abs(entry - sl)
    cur, be_done = sl, False
    j = bisect.bisect_left(m1_t, t_start)
    k = i + 1                                               # next signal-TF bar whose close updates the trail
    while j < len(m1):
        m = m1[j]
        while k < len(bars) and m.t >= bars[k].t + sec:     # TF bar k closed before this M1 bar started
            new = _trail_level(bars, i, k, side, trail, vw, atr_vals, atr_trail, buffer)
            if new is not None and (new - cur) * side > 0 and (bars[k].c - new) * side > 0:
                cur = new
                if events is not None:
                    events.append(("TRAIL_UPDATE", bars[k].t + sec, round(new, 2)))
            k += 1
        if (m.l <= cur) if side == 1 else (m.h >= cur):
            res = "BE" if be_done and abs(cur - entry) < 1e-9 else ("TRAIL" if (cur - sl) * side > 1e-9 else "SL")
            return dict(result=res, t_exit=m.t, exit=cur, cur_sl=cur,
                        r=round((cur - entry) * side / sl_d, 2) if sl_d else None)
        if (m.h >= tp) if side == 1 else (m.l <= tp):
            return dict(result="TP", t_exit=m.t, exit=tp, cur_sl=cur,
                        r=round((tp - entry) * side / sl_d, 2) if sl_d else None)
        if be_r and not be_done and sl_d and (((m.h - entry) if side == 1 else (entry - m.l)) >= be_r * sl_d) \
                and (cur - entry) * side < 0:
            cur, be_done = entry, True
            if events is not None:
                events.append(("BE", m.t, entry))
        j += 1
    return dict(result="open", t_exit=None, exit=None, cur_sl=cur, r=None)


def stats(sigs):
    done = [s for s in sigs if s["result"] != "open"]
    w = sum(1 for s in done if (s["r"] or 0) > 0)
    return dict(signals=len(sigs), closed=len(done), wins=w, losses=len(done) - w,
                win_rate=round(100 * w / len(done), 1) if done else None,
                total_r=round(sum(s["r"] or 0 for s in done), 1) if done else 0.0)
