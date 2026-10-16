"""v2.8.51 realistic CVD: real MT5 tick delta (tick rule) where the tick recorder covers a bar, M1 estimate inside
the bar where it does not, the candle estimate last. No look-ahead."""
import types

from harness import TMP  # noqa: F401

import cvd_engine as CE
from engine import Bar

T0 = 1_760_000_000 - 1_760_000_000 % 86400


def _bars(n, sec, start=T0, up=True):
    out, p = [], 4000.0
    for k in range(n):
        o = p
        p += 1.0 if up else -1.0
        out.append(Bar(start + k * sec, o, max(o, p) + 0.5, min(o, p) - 0.5, p, 100))
    return out


def _of(stats_tf, deltas, first, synthetic=False):
    """A fake tick recorder: bar_stats rows (bar_t, buy, sell, delta, vol, ...)."""
    rows = [(t, 0, 0, d, 0, None, 0, 0, 0, 0) for t, d in deltas.items()]
    return types.SimpleNamespace(bar_stats=lambda tf: rows if tf == stats_tf else [],
                                 first_tick_t=lambda: first, synthetic=synthetic)


def test_tick_delta_wins_where_ticks_cover_the_bar():
    m15 = _bars(6, 900)
    of = _of("M15", {m15[3].t: -42.0, m15[4].t: 17.0, m15[1].t + 0: 99.0}, first=m15[2].t + 10)
    d, src = CE.bar_deltas(m15, "M15", of, None)
    assert src == ["bar", "bar", "bar", "tick", "tick", "bar"]          # bar 1 starts before the first tick
    assert d[3] == -42.0 and d[4] == 17.0 and d[1] == CE.est(m15[1])
    assert CE.coverage(src)["tick"] == round(2 / 6, 3)


def test_simulated_ticks_are_never_used():
    m15 = _bars(4, 900)
    of = _of("M15", {b.t: 500.0 for b in m15}, first=0, synthetic=True)
    _d, src = CE.bar_deltas(m15, "M15", of, None)
    assert set(src) == {"bar"}


def test_m1_estimate_inside_the_candle():
    m15 = [Bar(T0, 4000, 4010, 3990, 4000, 300)]                          # a doji: candle estimate = 0
    m1 = [Bar(T0 + 60 * k, 4000 + k, 4001.5 + k, 3999.5 + k, 4001 + k, 20) for k in range(15)]   # 15 up minutes
    d, src = CE.bar_deltas(m15, "M15", None, m1)
    assert src == ["m1"] and d[0] > 0 and CE.est(m15[0]) == 0.0
    d2, src2 = CE.bar_deltas(m15, "M15", None, m1[:5])                   # too few M1 bars: the candle itself
    assert src2 == ["bar"] and d2[0] == 0.0


def test_cumulate_no_reset_and_daily_reset():
    bars = _bars(4, 43200)                                                # 2 bars per day
    c, o, h, l = CE.cumulate(bars, [1, 2, 3, 4], "M15", reset_daily=False)
    assert c == [1, 3, 6, 10] and o == [0, 1, 3, 6] and h[1] == 3 and l[1] == 1
    c2, *_ = CE.cumulate(bars, [1, 2, 3, 4], "M15", reset_daily=True)
    assert c2 == [1, 3, 3, 7]


def test_divergence_bearish_and_bullish():
    hs = [10, 11, 12, 13, 12, 11, 10, 11, 12, 14, 12, 11, 10]            # higher high at 9 vs 3
    bars = [Bar(T0 + k * 900, h - 1, h, h - 2, h - 1, 10) for k, h in enumerate(hs)]
    cvd = [0, 1, 2, 9, 2, 1, 0, 1, 2, 5, 2, 1, 0]                       # ... but CVD a lower high
    div = CE.divergences(bars, cvd)
    assert (12, -1, 9) in div
    lows = [-x for x in hs]
    bars2 = [Bar(T0 + k * 900, x + 1, x + 2, x, x + 1, 10) for k, x in enumerate(lows)]
    assert (12, 1, 9) in CE.divergences(bars2, [-x for x in cvd])
    assert all(i - j == 3 for i, _s, j in div)                           # known 3 bars after the pivot


def test_strategy_cvd_uses_real_ticks_when_covered():
    import orderflow
    import strategies as ST
    m15 = _bars(120, 900)
    old = orderflow.CURRENT
    try:
        orderflow.CURRENT = _of("M15", {b.t: -50.0 for b in m15}, first=0)   # sellers on every bar (rising price)
        S = ST.series(m15)
        i = len(m15) - 2
        assert S["cvd"]["ticks"] == 1.0 and ST.cvd_change(S, i, 8) == -400.0
        orderflow.CURRENT = None
        S2 = ST.series(m15)
        assert S2["cvd"]["ticks"] == 0.0 and ST.cvd_change(S2, i, 8) > 0     # estimate: rising candles
    finally:
        orderflow.CURRENT = old


def test_chart_plugin_line_candles_and_label():
    import importlib.util
    import os
    import strategies as ST
    spec = importlib.util.spec_from_file_location("cvdp", os.path.join(os.path.dirname(ST.__file__), "plugins", "cvd.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    m15 = _bars(60, 900)
    m1 = _bars(60 * 15, 60)
    ctx = types.SimpleNamespace(bars=lambda tf: m1 if tf == "M1" else m15)
    ov = mod.CVD().compute(m15, "M15", ctx)
    assert ov.lines and "M1 100%" in ov.lines[0].label and ov.lines[0].pane == "CVD"
    assert any(h.pane == "CVD" for h in ov.hists) and any(h.pane == "Delta" for h in ov.hists)
    assert mod.CVD.params["reset_daily"] is False                         # "No Reset" like the MT5 CVD
