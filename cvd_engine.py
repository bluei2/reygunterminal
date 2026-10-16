"""
CVD ENGINE (v2.8.51, Eugene: "gumawa ng mas realistic na CVD").

Gold on MT5 is a CFD: there is no exchange aggressor data (who hit the bid / ask). The most realistic delta MT5 can
give is the TICK RULE on the real MT5 ticks (bid up = buy tick, bid down = sell tick) - that is what orderflow.py
records (live + a tick backfill from MT5 at start). So every bar's delta comes from the best source available:

  1. "tick"  the bar is fully covered by real MT5 ticks  -> delta = buy ticks - sell ticks (tick rule)
  2. "m1"    no ticks, but M1 bars exist inside the bar   -> sum of the M1 estimates (finer than one candle)
  3. "bar"   only the bar itself                          -> tick_volume x (close - open) / (high - low)

Units are the same in all three: ticks (MT5 tick volume = number of ticks for gold), so the line is continuous.
Only data of bars that are CLOSED (or the forming bar itself, for the chart) is used - nothing looks ahead.
"""
from __future__ import annotations

TF_SEC = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400, "D1": 86400}
INTRADAY = ("M1", "M5", "M15", "M30", "H1")


def est(b):
    """Candle estimate (same formula as before / the MT5 Master EA): tick volume x body / range."""
    rg = b.h - b.l
    return float(getattr(b, "v", 0) or 0) * ((b.c - b.o) / rg if rg > 0 else 0.0)


def tick_map(of, tf):
    """{bar_t: delta} for bars FULLY covered by real ticks of the order-flow recorder (None when there are none)."""
    if of is None or tf not in TF_SEC or getattr(of, "synthetic", False):
        return {}                                      # demo / replay ticks are simulated: never "real"
    try:
        stats = of.bar_stats(tf)
        first = of.first_tick_t()
    except Exception:
        return {}
    if first is None:
        return {}
    return {x[0]: float(x[3]) for x in stats if x[0] >= first}


def bar_deltas(bars, tf, of=None, m1=None):
    """-> (deltas, sources) per bar. of: orderflow.OrderFlow (or None), m1: M1 bars (or None)."""
    sec = TF_SEC.get(tf) or ((bars[1].t - bars[0].t) if len(bars) > 1 else 60)
    ticks = tick_map(of, tf) if tf in ("M1", "M5", "M15", "M30", "H1") else {}
    m1_sum, m1_cnt = {}, {}
    if m1 and sec > 60:
        for b in m1:
            k = b.t - b.t % sec
            m1_sum[k] = m1_sum.get(k, 0.0) + est(b)
            m1_cnt[k] = m1_cnt.get(k, 0) + 1
    out, src = [], []
    need = max(1, sec // 60)
    for b in bars:
        if b.t in ticks:
            out.append(ticks[b.t])
            src.append("tick")
        elif m1_cnt.get(b.t, 0) >= max(1, int(need * 0.8)):
            out.append(m1_sum[b.t])                     # nearly every M1 bar of it is there
            src.append("m1")
        else:
            out.append(est(b))
            src.append("bar")
    return out, src


def cumulate(bars, deltas, tf, reset_daily=False):
    """Cumulative delta (optionally restarting every server day on intraday charts) -> (cvd, open, high, low)."""
    n = len(bars)
    cvd, op, hi, lo = [None] * n, [None] * n, [None] * n, [None] * n
    total, day = 0.0, None
    reset = reset_daily and tf in INTRADAY
    for i, (b, d) in enumerate(zip(bars, deltas)):
        k = b.t // 86400
        if reset and k != day:
            total, day = 0.0, k
        o = total
        total += d
        cvd[i], op[i], hi[i], lo[i] = total, o, max(o, total), min(o, total)
    return cvd, op, hi, lo


def divergences(bars, cvd, k=3, look=40):
    """Confirmed swing divergences between price and CVD (pivot k bars each side, known k bars later):
    price makes a HIGHER high but CVD a LOWER high -> bearish (-1); price a LOWER low but CVD a HIGHER low ->
    bullish (+1). -> list of (confirm_bar_index, side, pivot_index)."""
    n = len(bars)
    out = []
    last_h = last_l = None
    for i in range(2 * k, n):
        j = i - k                                     # the pivot bar, known now
        if cvd[j] is None:
            continue
        win = range(j - k, j + k + 1)
        if all(bars[j].h >= bars[x].h for x in win if x != j):
            if last_h is not None and j - last_h <= look and bars[j].h > bars[last_h].h and cvd[j] < cvd[last_h]:
                out.append((i, -1, j))
            last_h = j
        if all(bars[j].l <= bars[x].l for x in win if x != j):
            if last_l is not None and j - last_l <= look and bars[j].l < bars[last_l].l and cvd[j] > cvd[last_l]:
                out.append((i, 1, j))
            last_l = j
    return out


def coverage(sources):
    """Share of bars per source, e.g. {'tick': 0.4, 'm1': 0.5, 'bar': 0.1}."""
    n = len(sources) or 1
    return {s: round(sources.count(s) / n, 3) for s in ("tick", "m1", "bar")}
