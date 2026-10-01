"""Trading the Reversal arrows: AUTO TRADE source, forecast setups, backtest entry mode, no look-ahead."""
import time

import backtest
import forecast as F
import reversal
from test_forecast import T0, cfg, view
from test_gate import setup


def _at(hub, tm, **kw):
    from autotrade import AutoTrader
    at = AutoTrader(tm)
    at.running = False
    at.enabled = True
    at.settings.update(dict(sources=["reversal"], sessions=[], max_cautions=0, news_block=False, max_open=1,
                            reversal_confirmed_only=True, reversal_confirm="bias", reversal_min_score=60), **kw)
    at._session_ok = lambda: (True, "test")
    hub.autotrader = at
    hub.read_at = lambda t: hub.read                                   # the analyser as of the arrow's bar
    return at


def arrow(side=-1, score=70, confirmed=True, t=None):
    t = t or int(time.time()) // 300 * 300
    e = 2650.0
    return dict(side=side, entry=e, sl=e - side * 4.0, tp=e + side * 8.0, score=score, confirmed=confirmed,
                cautions=[], t=t, kind="TOP (bearish)" if side == -1 else "BOTTOM (bullish)")


def test_arrow_with_the_bias_trades_even_below_passing_score():
    hub, tm, g = setup(score=50.0, bias=-1)                            # bearish, analyser score only 50%
    at = _at(hub, tm)
    at._consider("reversal", arrow(-1))
    assert len(tm.groups) == 1, [x["text"] for x in at.log]
    grp = next(iter(tm.groups.values()))
    assert grp.side == -1


def test_counter_trend_arrow_is_never_traded():
    hub, tm, g = setup(score=90.0, bias=1)                             # bullish HTF, a SELL arrow
    at = _at(hub, tm)
    at._consider("reversal", arrow(-1))
    assert not tm.groups and "BLOCKED_STRUCTURE" in at.log[0]["text"], at.log[0]["text"]


def test_unconfirmed_low_score_and_score_mode():
    hub, tm, g = setup(score=50.0, bias=-1)
    at = _at(hub, tm)
    at._consider("reversal", arrow(-1, confirmed=False))
    assert not tm.groups and "R?" in at.log[0]["text"]
    at._consider("reversal", arrow(-1, score=45, t=int(time.time()) // 300 * 300 + 300))
    assert not tm.groups and "reversal score 45" in at.log[0]["text"]
    at.settings["reversal_confirm"] = "score"                           # bias AND the passing score
    at._consider("reversal", arrow(-1, t=int(time.time()) // 300 * 300 + 600))
    assert not tm.groups and "BLOCKED_SCORE" in at.log[0]["text"], at.log[0]["text"]
    at.settings.update(reversal_confirmed_only=False, reversal_confirm="bias")
    at._consider("reversal", arrow(-1, confirmed=False, t=int(time.time()) // 300 * 300 + 900))
    assert len(tm.groups) == 1, [x["text"] for x in at.log][:3]


def test_analyser_signals_still_need_the_score():
    hub, tm, g = setup(score=50.0, bias=-1)
    s = g.make_signal("analyser", "M5", int(time.time()) // 300 * 300, -1, 2650.0, 2654.0, 2642.0, 50, [])
    assert g.evaluate(s, "auto", 0.01, waive=("trigger", "score"))["block_code"] == "BLOCKED_SCORE"


def test_settings_accept_reversal_source():
    hub, tm, g = setup()
    at = _at(hub, tm)
    at.update_settings(dict(sources=["analyser", "reversal"], reversal_confirm="score", reversal_min_score=75,
                            reversal_confirmed_only=False))
    s = at.settings
    assert s["sources"] == ["analyser", "reversal"] and s["reversal_confirm"] == "score"
    assert s["reversal_min_score"] == 75 and s["reversal_confirmed_only"] is False
    at.update_settings(dict(reversal_confirm="off"))                    # not an option: unchanged
    assert s["reversal_confirm"] == "score"


# ---------------------------------------------------------------- detector: no look-ahead
def test_reversal_detector_has_no_lookahead():
    bars = backtest.synth_data(6, seed=4)["M5"]
    full = reversal.Detector(bars)
    checked = 0
    for i in range(200, len(bars) - 10, 37):
        part = reversal.Detector(bars[:i + 1] + [bars[i]])              # bars up to i (+ a dummy forming bar)
        for side in (-1, 1):
            a, b = full.evaluate(i, side), part.evaluate(i, side)
            assert (a is None) == (b is None), (i, side)
            if a:
                assert (a["score"], a["level"], a["tags"]) == (b["score"], b["level"], b["tags"]), (i, side)
                checked += 1
    assert checked > 0


# ---------------------------------------------------------------- forecast setups
def rev_view(d=-1, bias=-1, confirmed=True, score=72):
    v = view(d, bias=bias, regime="TREND DOWN" if d == -1 else "TREND UP", htf="TREND DOWN" if d == -1 else "TREND UP")
    ev = dict(side=d, score=score, level=2653.0 if d == -1 else 2647.0, tags=["SWH", "DIV"], why=["swept swing high"],
              confirmed=confirmed, t=T0, entry=2650.0)
    v["rev"] = dict(any=ev, ok=ev if confirmed else None, atr=2.0, close=2650.0, t=T0)
    return v


def test_forecast_reversal_setups():
    fc, why = F.build_reversal(rev_view(), F.get_regime(rev_view(), cfg()), cfg(), "rev_ok")
    assert fc and fc["direction"] == "SHORT" and fc["setup_type"] == "rev_ok", why
    assert fc["sl"] > 2653.0 and fc["tp2"] < fc["entry"] and fc["entry_type"] == "MARKET"
    v = rev_view(confirmed=False)
    assert F.build_reversal(v, F.get_regime(v, cfg()), cfg(), "rev_ok")[0] is None      # R? only -> not rev_ok
    assert F.build_reversal(v, F.get_regime(v, cfg()), cfg(), "rev_any")[0] is not None
    v = rev_view(d=-1, bias=1)                                          # counter-trend arrow
    assert "HTF bias" in F.build_reversal(v, F.get_regime(v, cfg()), cfg(), "rev_any")[1]
    assert F.build_reversal(v, F.get_regime(v, cfg()), cfg(rev_bias_filter=False), "rev_any")[0] is not None
    v = rev_view(score=50)
    assert "score" in F.build_reversal(v, F.get_regime(v, cfg()), cfg(), "rev_any")[1]


def test_forecast_reversal_from_real_detector():
    from superchart import Hub
    d = backtest.synth_data(5, seed=6)
    h = Hub(backtest.HistSource("XAUUSD", d))
    m5 = d["M5"]
    hits = 0
    for k in range(len(m5) - 400, len(m5) - 2, 3):
        rv = F._reversal_at(h, m5[k].t + 300, "M5")
        assert rv is not None and rv["t"] == m5[k].t
        for x in (rv["any"], rv["ok"]):
            if x:
                hits += 1
                assert x["entry"] == m5[k].c
        if rv["ok"]:
            assert rv["ok"]["confirm_t"] == m5[k].t and rv["ok"]["t"] < m5[k].t
    assert hits > 0


# ---------------------------------------------------------------- backtest entry mode
def test_backtest_reversal_entry():
    from test_backtest import prepared
    P = prepared("M15", 12)
    base = dict(backtest.DEFAULT_SETUP, sessions=[], max_cautions=-1)
    for mode in ("reversal_ok", "reversal_any"):
        s = dict(base, entry=mode, rev_min_score=50, rev_bias=True)
        arr = backtest.reversal_array(P, s, P.start, len(P.bars) - 1)
        assert any(arr), mode
        for i, v in enumerate(arr):
            if v:
                assert P.reads[i] is not None and P.reads[i][0] == v            # with the HTF bias only
        trades = backtest.simulate(P, s)
        assert trades and all(t["side"] in (1, -1) for t in trades)
    a = backtest.simulate(P, dict(base))                                # analyser mode unchanged
    b = backtest.simulate(P, dict(base, entry="analyser"))
    assert [t["t"] for t in a] == [t["t"] for t in b]


# ---------------------------------------------------------------- REGIME ROUTER
import types  # noqa: E402


def _router(label, d, score=80.0, bias=None):
    hub, tm, g = setup(score=score, bias=d if bias is None else bias)
    at = _at(hub, tm, sources=["analyser"], router=True, router_trend_rr=2.0, router_range_rr=1.0,
             router_trend_labels=["TREND UP", "TREND DOWN"], router_range_labels=["RANGE"], max_cautions=-1)
    hub.regime = types.SimpleNamespace(state=lambda: dict(label=label, dir=d))
    return hub, tm, at


def _tp_ratio(tm):
    grp = next(iter(tm.groups.values()))
    return round(abs(grp.m_tp - grp.m_open) / abs(grp.m_open - grp.m_sl), 2) if grp.m_tp and grp.m_sl else None


def test_router_trend_takes_trend_signal_at_1_to_2():
    hub, tm, at = _router("TREND DOWN", -1)
    t = int(time.time()) // 300 * 300
    at._consider("reversal", arrow(-1, t=t))                                  # arrows are not for trend mode
    assert not tm.groups and "trend signals lang" in at.log[0]["text"], at.log[0]["text"]
    at._consider("analyser", dict(side=1, entry=2650.0, sl=2646.0, tp=2656.0, score=80, cautions=[], t=t + 300))
    assert not tm.groups and "against the trend" in at.log[0]["text"]
    at._consider("analyser", dict(side=-1, entry=2650.0, sl=2654.0, tp=2645.0, score=80, cautions=[], t=t + 600))
    assert len(tm.groups) == 1, [x["text"] for x in at.log][:3]
    grp = next(iter(tm.groups.values()))
    assert abs(grp.tp_dist - 2 * grp.sl_dist) < 0.02, (grp.tp_dist, grp.sl_dist)       # TP = 2 x SL


def test_router_range_takes_arrows_at_1_to_1_both_ways():
    hub, tm, at = _router("RANGE", 0, score=40.0, bias=0)
    t = int(time.time()) // 300 * 300
    at._consider("analyser", dict(side=1, entry=2650.0, sl=2646.0, tp=2658.0, score=80, cautions=[], t=t))
    assert not tm.groups and "reversal arrows lang" in at.log[0]["text"]
    at._consider("reversal", arrow(1, t=t + 300))                             # BUY at a bottom, no HTF bias
    assert len(tm.groups) == 1, [x["text"] for x in at.log][:3]
    grp = next(iter(tm.groups.values()))
    assert abs(grp.tp_dist - grp.sl_dist) < 0.02                             # TP = 1 x SL


def test_router_range_blocks_arrow_against_clear_htf_and_other_regimes():
    hub, tm, at = _router("RANGE", 0, score=40.0, bias=1)                     # range, but HTF clearly bullish
    t = int(time.time()) // 300 * 300
    at._consider("reversal", arrow(-1, t=t))
    assert not tm.groups and "against the HTF bias" in at.log[0]["text"]
    at._consider("reversal", arrow(1, t=t + 300))                             # with the bias: ok
    assert len(tm.groups) == 1, [x["text"] for x in at.log][:3]
    for label in ("VOLATILE", "QUIET", "TRANSITION"):
        hub2, tm2, at2 = _router(label, 0)
        at2._consider("analyser", dict(side=1, entry=2650.0, sl=2646.0, tp=2658.0, score=80, cautions=[], t=t))
        at2._consider("reversal", arrow(1, t=t + 300))
        assert not tm2.groups and "walang trade" in at2.log[0]["text"], label


def test_router_off_keeps_old_behaviour():
    hub, tm, g = setup(score=80.0, bias=-1)
    at = _at(hub, tm, sources=["analyser"], router=False, max_cautions=-1)
    hub.regime = types.SimpleNamespace(state=lambda: dict(label="VOLATILE", dir=0))
    t = int(time.time()) // 300 * 300
    at._consider("analyser", dict(side=-1, entry=2650.0, sl=2654.0, tp=2638.0, score=80, cautions=[], t=t))
    assert len(tm.groups) == 1
    grp = next(iter(tm.groups.values()))
    assert abs(grp.tp_dist - 12.0) < 0.02                                    # the signal's own TP, untouched


def test_backtest_router_rr_per_regime():
    from test_backtest import prepared
    P = prepared("M15", 12)
    s = dict(backtest.DEFAULT_SETUP, sessions=[], max_cautions=-1, entry="router", rev_min_score=50,
             router_range_labels=("RANGE", "TRANSITION"))
    trades = backtest.simulate(P, s)
    labels = backtest.regime_labels(P, P.start, len(P.bars) - 1)
    assert trades
    seen = set()
    for tr in trades:
        if "i" not in tr:
            continue
        lab = labels[tr["i"]][0]
        ratio = abs(tr["tp"] - tr["entry"]) / abs(tr["entry"] - tr["sl"])
        want = 2.0 if lab in s["router_trend_labels"] else 1.0
        assert abs(ratio - want) < 0.02, (lab, ratio)
        seen.add(want)
    assert seen


def test_forecast_router():
    v = rev_view(d=-1, bias=0)
    v["regime"] = dict(label="RANGE", dir=0)
    fc, why = F.build_router(v, F.get_regime(v, cfg()), cfg())
    assert fc and fc["setup_type"] == "router" and fc["router_mode"] == "RANGE", why
    assert abs(abs(fc["tp2"] - fc["entry"]) - abs(fc["entry"] - fc["sl"])) < 0.02             # 1:1
    v = view(1)
    v["regime"] = dict(label="TREND UP", dir=1)
    fc, why = F.build_router(v, F.get_regime(v, cfg()), cfg())
    assert fc and fc["router_mode"] == "TREND" and fc["rr"] == 2.0, why
    v["regime"] = dict(label="VOLATILE", dir=0)
    assert "no trade" in F.build_router(v, F.get_regime(v, cfg()), cfg())[1]
