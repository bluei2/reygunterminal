"""Step 6 (backtest realism, intrabar, walk-forward, Monte Carlo, sensitivity) tests."""
import sys
import time

from harness import ROOT  # noqa: F401  (sets sys.path)

import backtest
import exits
import monte_carlo
import walkforward
from engine import Bar

_P = {}


def prepared(tf="M15", days=12):
    if (tf, days) not in _P:
        data = backtest.synth_data(days)
        _P[(tf, days)] = backtest.Prepared(None, data, "XAUUSD", tf, days)
    return _P[(tf, days)]


def test_costs_reduce_results():
    P = prepared()
    s = dict(backtest.DEFAULT_SETUP)
    base_tr = backtest.simulate(P, s, spread=0.3)
    base = backtest.stats(base_tr)
    # commission + swap: the SAME trades, only dollars are taken away
    fee_tr = backtest.simulate(P, s, spread=0.3, costs=dict(commission_per_lot=7, swap_per_lot=2))
    fee = backtest.stats(fee_tr)
    assert base["trades"] and fee["trades"] == base["trades"] and fee["usd"] < base["usd"], (base, fee)
    # slippage: every fill is worse by the slippage (entry paid further away in the trade's direction)
    slip_tr = backtest.simulate(P, s, spread=0.3, costs=dict(slippage=0.2))
    b0 = {t["t"]: t for t in base_tr if "entry" in t}
    same = [(b0[t["t"]], t) for t in slip_tr if "entry" in t and t["t"] in b0 and b0[t["t"]]["side"] == t["side"]]
    assert same and all(abs((x["entry"] - y["entry"]) * x["side"] - 0.2) < 0.011 for y, x in same), same[:2]


def test_delay_fills_next_open():
    P = prepared()
    s = dict(backtest.DEFAULT_SETUP)
    tr = backtest.simulate(P, s, spread=0.0, costs=dict(execution_delay_ms=500))
    t = tr[0]
    nxt = P.bars[t["i"] + 1]
    assert abs(t["entry"] - nxt.o) < 0.006 and t["events"][0]["t"] == nxt.t, (t["entry"], nxt.o)


def test_intrabar_uses_m1_and_no_lookahead():
    P = prepared()
    assert P.m1, "synthetic data must provide M1"
    s = dict(backtest.DEFAULT_SETUP, trail="atr")
    tr = backtest.simulate(P, s, spread=0.3, costs=dict(intrabar=True))
    assert tr and all(t["mode"] == "M1" for t in tr)
    sec = backtest.TF_SECONDS[P.tf]
    for t in tr:
        for ev in t["events"]:
            assert ev["t"] >= P.bars[t["i"]].t + sec - 60 or ev["kind"] == "ENTRY", "event before the entry"
        tu = [e for e in t["events"] if e["kind"] == "TRAIL_UPDATE"]
        for e in tu:                        # a trail level is only known after a TF bar CLOSED
            assert (e["t"] - P.bars[0].t) % sec == 0, e


def test_trailing_uses_only_closed_bars():
    # price goes up for 3 bars then collapses inside bar 4: the ATR trail may only use bars 1-3
    bars = [Bar(i * 300, 100 + i, 100.5 + i, 99.5 + i, 100 + i + 0.5, 1) for i in range(6)]
    bars[4] = Bar(4 * 300, 104.5, 104.6, 90.0, 91.0, 1)
    atr = [1.0] * 6
    ev = []
    r = exits.replay(bars, 0, 1, 100.5, 95.0, 130.0, "atr", None, atr, 1.0, 0.5, 0.0, ev)
    assert r["result"] == "TRAIL" and r["t_exit"] == 4 * 300, r
    assert all(e[1] < 4 * 300 for e in ev), ev          # no update from the bar that hit the stop


def test_breakeven_event():
    bars = [Bar(i * 300, 100, 100.2, 99.8, 100, 1) for i in range(3)]
    bars[1] = Bar(300, 100, 106, 99.9, 105, 1)          # +6 with 5 risk -> 1R: BE
    bars[2] = Bar(600, 105, 105.2, 99.0, 99.5, 1)       # back down: stopped at entry
    ev = []
    r = exits.replay(bars, 0, 1, 100.0, 95.0, 120.0, "none", None, None, 3.0, 0.5, 1.0, ev)
    assert r["result"] == "BE" and r["r"] == 0.0 and ev[0][0] == "BE", (r, ev)


def test_walkforward_runs():
    P = prepared("M15", 30)
    wf = walkforward.run(P, train_days=10, test_days=4, min_trades=3, max_combos=60)
    assert wf["windows"] >= 3 and all("oos" in r for r in wf["rows"]), wf["windows"]


def test_monte_carlo():
    rs = [2, -1, -1, 2, -1, 2, -1, -1, -1, 2, 2, -1]
    mc = monte_carlo.run(rs, n=500)
    assert mc["total_r"]["median"] == 3.0 and mc["max_dd_r"]["worst"] <= mc["actual"]["max_dd_r"] <= 0
    assert mc["losing_streak"]["max"] >= mc["actual"]["max_losing_streak"]
    mc2 = monte_carlo.run(rs, n=500, slip_r=0.3)
    assert mc2["total_r"]["median"] < mc["total_r"]["median"]


def test_sensitivity_flags():
    P = prepared()
    sens = backtest.sensitivity(P, dict(backtest.DEFAULT_SETUP), P.start, len(P.bars) - 1, 0.3, 0.01, None, "total_r")
    assert sens["rows"] and "flag" in sens
