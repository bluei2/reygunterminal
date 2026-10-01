"""Step 3 (execution guard) tests."""
from harness import cycle, make_tm

from execution_guard import ExecutionGuard


def _setup(n=1):
    hub, tm = make_tm(n)
    hub.execution = ExecutionGuard(hub)
    hub.execution.settings.update(require_protective_sl=True, max_spread=0.6)
    cycle(tm)                                     # reads broker info
    tm.set_armed(True)
    return hub, tm


def test_invalid_lot_step():
    hub, tm = _setup(0)
    tm.master.broker["lot_step"] = 0.1
    tm.master.broker["min_lot"] = 0.1
    r = tm.open(1, 0.15, 5, 10)
    assert not r["ok"] and "INVALID_LOT" in r["error"], r


def test_missing_sl_blocked():
    hub, tm = _setup(0)
    r = tm.open(1, 0.01, 0, 10)
    assert not r["ok"] and "NO_PROTECTIVE_SL" in r["error"], r


def test_stops_level():
    hub, tm = _setup(0)
    tm.master.broker.update(stops_level=400, point=0.01)          # 4.00 minimum distance
    r = tm.open(1, 0.01, 3, 10)
    assert not r["ok"] and "BROKER_STOPS_LEVEL" in r["error"], r


def test_freeze_level():
    hub, tm = _setup(0)
    tm.master.broker.update(freeze_level=600, point=0.01)
    r = tm.open(1, 0.01, 5, 10)
    assert not r["ok"] and "BROKER_FREEZE_LEVEL" in r["error"], r


def test_follower_skipped_master_trades():
    hub, tm = _setup(1)
    f = tm.followers[0]
    f.broker["lot_step"] = 0.1                                    # follower lot 0.01 is invalid on its broker
    r = tm.open(1, 0.01, 5, 10)
    assert r["ok"] and "f1" not in tm.groups[r["gid"]].legs, r
    life = [x["state"] for x in tm.journal.entries[r["gid"]]["lifecycle"]]
    assert "FOLLOWER_SKIPPED" in life, life


def test_monitor_rows():
    hub, tm = _setup(1)
    tm.followers[0].broker["spread"] = 0.95
    m = hub.execution.monitor(0.01)
    st = {x["name"]: x["status"] for x in m["accounts"]}
    assert st["Master"] == "READY" and st["F1"] == "BLOCKED", st
