"""v2.8.48 CONSO in a RANGE (Eugene, 2026-10-08, 4113-4133: the SELL STOP filled at the bottom and the BUY STOP at the
top, both lost ~$20): a failed breakout is cut small, one loss per box."""
import time
import types


from harness import TMP  # noqa: F401

import strategies as ST
from engine import Bar
from test_strategies import _auto


def _grid_setup():
    import news_trader as NTM
    hub, tm, g, at = _auto(score=40.0, bias=1, strategies=("conso",))
    nt = NTM.NewsTrader(tm)
    nt.running = False
    hub.newstrader = nt
    return hub, tm, at, nt, NTM


def test_one_loss_per_box_then_no_new_grid():
    import strategies as _st
    hub, tm, at, nt, NTM = _grid_setup()
    plan = dict(ok=True, box_high=2652.0, box_low=2648.0, buy_stop=2652.5, sell_stop=2647.5, sl_dist=5.0, tp_dist=10.0,
                buy_sl=2647.5, buy_tp=2662.5, sell_sl=2652.5, sell_tp=2637.5, box_from=0, levels=1, step=1.5, side=0)
    t = int(time.time()) // 900 * 900
    spell = dict(side=0, pending=True, entry=2652.5, sl=2647.5, tp=2662.5, score=100.0, cautions=[], t=t, i=200,
                 strategy="conso", kind="CONSO BOX")
    at._signals = lambda *a, **k: [dict(spell)]
    old_eval, old_series = _st.evaluate, _st.series
    _st.evaluate = lambda *a, **k: dict(plan)
    _st.series = lambda *a, **k: None
    bars = [types.SimpleNamespace(t=t - (300 - k) * 900) for k in range(300)]
    try:
        def tags():
            return [o for o in tm.master.backend.call("orders", None) if o["comment"].startswith(NTM.TAG)]
        at._strategies(bars, "M15")
        assert len(tags()) == 2                                                     # grid 1
        for st in nt.events.values():
            st["expire_at"] = time.time() - 1
        tm.refresh(); nt._cycle()
        assert not tags()
        tm.journal.entries["NxxxxS0"] = dict(gid="NxxxxS0", strategy="st_conso", status="closed", pnl=-19.83,
                                             open_time=t + 60, side=-1)
        at._st_tries = {}
        at._strategies(bars, "M15")
        assert not tags(), "a losing box must not get a new grid"
        assert any("natalo na ang isang trade ng box" in x["text"] for x in at.log)
        at.settings["conso_one_loss"] = False                                      # switch off -> round 2 again
        at._st_tries, at._once = {}, {}
        at._strategies(bars, "M15")
        assert len(tags()) == 2
    finally:
        _st.evaluate, _st.series = old_eval, old_series
        ST.save_settings(ST.default_settings())


def _fail_case(close, side=-1, created_ago=3600):
    hub, tm, at, nt, NTM = _grid_setup()
    from trading import Group
    now = int(time.time()) // 900 * 900
    bars = []
    p = 2650.0
    for k in range(40):
        bars.append(Bar(now - (40 - k) * 900, p, p + 1.0, p - 1.0, p, 100))
    last = bars[-2]
    bars[-2] = Bar(last.t, last.o, max(last.o, close) + 0.3, min(last.o, close) - 0.3, close, 100)
    gid = "NabcdS0" if side == -1 else "NabcdB0"
    tm.groups[gid] = Group(gid, side, time.time() - created_ago, {"master": 1}, 0.01, 0, 0, 2647.0, source="st_conso")
    nt.events["BOX|x"] = dict(key="BOX|x", eid="abcd", kind="strategy", box=dict(high=2652.0, low=2648.0, range=4.0),
                              groups={f"{side}:0": gid}, orders={}, triggered={})
    closed = []
    tm.close_group = lambda g, frac, source="": (closed.append((g, source)), {"ok": True})[1]
    at.settings["tf"] = "M15"
    at._conso_fail_exit(bars)
    return closed, at


def test_failed_breakout_sell_back_inside_the_box_is_closed():
    closed, at = _fail_case(2649.5)                         # SELL filled below 2648, M15 closed back at 2649.5
    assert closed == [("NabcdS0", "conso failed breakout")]
    assert any("CONSO FAILED BREAKOUT" in x["text"] for x in at.log)


def test_breakout_that_holds_is_kept():
    closed, _ = _fail_case(2646.0)                          # still below the box: a real breakout
    assert closed == []
    closed, _ = _fail_case(2648.1)                          # just at the edge (< 0.2 ATR inside): kept
    assert closed == []


def test_fill_bar_not_judged_before_it_closes_and_switch_off():
    closed, _ = _fail_case(2649.5, created_ago=0)           # filled after that candle closed: not judged yet
    assert closed == []
    closed, at = _fail_case(2655.0, side=1)                 # BUY above 2652 that is still above: kept
    assert closed == []
    closed, at = _fail_case(2650.0, side=1)                 # BUY back inside: closed
    assert closed == [("NabcdB0", "conso failed breakout")]
