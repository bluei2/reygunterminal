"""Step 4 (copier reconciliation) tests."""
import time

from harness import cycle, make_tm

import reconciliation
from reconciliation import Reconciler


def _setup():
    hub, tm = make_tm(1)
    rc = Reconciler.__new__(Reconciler)
    rc.hub, rc.auto_repair, rc.rows, rc.orphans, rc.fail, rc.alerted, rc.last, rc.running = hub, True, [], {}, {}, set(), 0, False
    from collections import deque
    rc.log = deque(maxlen=60)
    hub.reconciler = rc
    tm.set_armed(True)
    r = tm.open(1, 0.01, 5, 10)
    g = tm.groups[r["gid"]]
    g.created -= 20
    cycle(tm)
    return hub, tm, rc, g


def test_sync_ok():
    hub, tm, rc, g = _setup()
    rc.check()
    assert rc.rows and rc.rows[0]["status"] == "SYNC", rc.rows


def test_sl_mismatch_repaired():
    hub, tm, rc, g = _setup()
    f = tm.followers[0]
    ft = g.legs["f1"]
    f.backend.positions_[ft]["sl"] = 0.0                      # someone removed the follower SL in MT5
    cycle(tm)
    rc.check()
    assert rc.rows[0]["status"] == "DRIFT"
    cycle(tm)
    fp = f.backend.positions_[ft]
    assert abs((fp["open"] - fp["sl"]) - 5) < 0.02, fp


def test_extra_volume_partially_closed():
    hub, tm, rc, g = _setup()
    f = tm.followers[0]
    ft = g.legs["f1"]
    f.backend.positions_[ft]["volume"] = 0.03
    cycle(tm)
    rc.check()
    assert abs(f.backend.positions_[ft]["volume"] - 0.01) < 1e-9, f.backend.positions_[ft]


def test_side_mismatch_pauses_follower_and_resume():
    hub, tm, rc, g = _setup()
    f = tm.followers[0]
    fp = f.backend.positions_[g.legs["f1"]]
    fp.update(side=-1, sl=fp["open"] + 5, tp=fp["open"] - 10)   # a SELL where a BUY was expected
    cycle(tm)
    rc.check()
    assert f.paused, "follower must be paused"
    tm.close_everything()
    r = tm.open(1, 0.01, 5, 10)
    assert "f1" not in tm.groups[r["gid"]].legs, "paused follower must not get new trades"
    rc.resume("f1")
    assert not f.paused


def test_orphan_closed_after_age():
    hub, tm, rc, g = _setup()
    f = tm.followers[0]
    t = f.backend.c_open_market("XAUUSD", 1, 0.01, 5, 10, "RGSC-zzzzzz")["ticket"]     # no group for it
    cycle(tm)
    rc.check()
    assert any(r["status"] == "ORPHAN" for r in rc.rows)
    assert t in f.backend.positions_, "not closed on the first sighting"
    old = reconciliation.ORPHAN_AGE_S
    reconciliation.ORPHAN_AGE_S = 0
    try:
        rc.check()
    finally:
        reconciliation.ORPHAN_AGE_S = old
    assert t not in f.backend.positions_


def test_other_ea_untouched():
    hub, tm, rc, g = _setup()
    f = tm.followers[0]
    t = f.backend.c_open_market("XAUUSD", 1, 0.01, 5, 10, "other EA")["ticket"]      # comment without RGSC-
    reconciliation.ORPHAN_AGE_S, old = 0, reconciliation.ORPHAN_AGE_S
    try:
        cycle(tm)
        rc.check()
        rc.check()
    finally:
        reconciliation.ORPHAN_AGE_S = old
    assert t in f.backend.positions_, "a position that is not a SuperChart trade must never be closed"


def test_stale_follower_no_action():
    hub, tm, rc, g = _setup()
    f = tm.followers[0]
    ft = g.legs["f1"]
    f.backend.positions_[ft]["sl"] = 0.0
    f.backend.fail_reads = True
    cycle(tm)
    rc.check()
    assert rc.rows[0]["status"] == "STALE" and f.backend.positions_[ft]["sl"] == 0.0


def test_partial_fill_reported_never_topped_up():
    hub, tm, rc, g = _setup()
    f = tm.followers[0]
    f.cfg["lot"] = 0.05                                       # asked 0.05 ...
    ft = g.legs["f1"]
    f.backend.positions_[ft]["volume"] = 0.01                 # ... broker filled only 0.01 (partial fill)
    cycle(tm)
    n_calls = len(f.backend.calls)
    rc.check()
    assert rc.rows[0]["status"] == "DRIFT" and "volume" in " ".join(rc.rows[0]["details"]), rc.rows
    assert "open_market" not in f.backend.calls[n_calls:]    # never adds risk to "fix" it
    assert f.backend.positions_[ft]["volume"] == 0.01
