"""Step 1 (safety layer) tests."""
import json
import os
import time

from harness import TMP, cycle, make_tm, trading

import journal
from trade_confirmation import ConfirmationBook


def test_arm_off_blocks():
    hub, tm = make_tm(0)
    r = tm.open(1, 0.01, 5, 10)
    assert not r["ok"] and "armed" in r["error"], r


def test_token_expiry():
    hub, tm = make_tm(0)
    tm.set_armed(True)
    book = ConfirmationBook(tm, expiry_s=0.3)
    p = book.prepare(1, 0.01, 5, 10, "req-exp")
    assert p["ok"], p
    time.sleep(0.5)
    r = book.confirm(p["token"], "req-exp", 1, 0.01, 5, 10)
    assert not r["ok"] and "expired" in r["error"], r
    assert not tm.groups


def test_token_reuse_and_binding():
    hub, tm = make_tm(0)
    tm.set_armed(True)
    book = ConfirmationBook(tm)
    p = book.prepare(1, 0.01, 5, 10, "req-a")
    bad = book.confirm(p["token"], "req-a", 1, 0.02, 5, 10)            # lot changed -> invalid
    assert not bad["ok"] and "changed" in bad["error"], bad
    p = book.prepare(1, 0.01, 5, 10, "req-b")
    r1 = book.confirm(p["token"], "req-b", 1, 0.01, 5, 10)
    assert r1["ok"], r1
    r2 = book.confirm(p["token"], "req-b", 1, 0.01, 5, 10)             # same token + id again
    assert not r2["ok"] and r2["error"] == "ALREADY_EXECUTED", r2
    assert len(tm.groups) == 1


def test_duplicate_request_id():
    hub, tm = make_tm(0)
    tm.set_armed(True)
    a = tm.open(1, 0.01, 5, 10, request_id="dup-1")
    b = tm.open(1, 0.01, 5, 10, request_id="dup-1")
    assert a["ok"] and not b["ok"] and b["error"] == "ALREADY_EXECUTED", (a, b)
    assert len(tm.groups) == 1


def test_opposite_blocked():
    hub, tm = make_tm(0)
    tm.set_armed(True)
    assert tm.open(1, 0.01, 5, 10)["ok"]
    r = tm.open(-1, 0.01, 5, 10)
    assert not r["ok"] and "kontrang" in r["error"], r


def test_missing_sl_attached():
    hub, tm = make_tm(1)
    tm.set_armed(True)
    tm.followers[0].backend.drop_sl = True
    r = tm.open(1, 0.01, 5, 10)
    gid = r["gid"]
    tm.safety["protective_sl_retry_delay"] = 0
    cycle(tm, 2)
    fp = tm.followers[0].backend.positions_[tm.groups[gid].legs["f1"]]
    assert fp["sl"] > 0, fp
    states = [x["state"] for x in tm.journal.entries[gid]["lifecycle"]]
    assert "UNPROTECTED" in states and "SL_ATTACHED" in states, states
    cycle(tm)
    assert tm.groups[gid].state == "PROTECTED", tm.groups[gid].state


def test_missing_sl_closed_when_it_cannot_be_attached():
    hub, tm = make_tm(1)
    tm.set_armed(True)
    fb = tm.followers[0].backend
    fb.drop_sl, fb.modify_fails = True, True
    r = tm.open(1, 0.01, 5, 10)
    t = tm.groups[r["gid"]].legs["f1"]
    tm.safety["protective_sl_retry_delay"] = 0
    cycle(tm, tm.safety["protective_sl_retry_count"] + 2)
    assert t not in fb.positions_, "unprotected follower leg should have been closed"
    assert any("UNPROTECTED" in n[1] for n in hub.notes)


def test_stale_follower_is_not_touched_then_closed_when_fresh():
    hub, tm = make_tm(1)
    tm.set_armed(True)
    r = tm.open(1, 0.01, 5, 10)
    g = tm.groups[r["gid"]]
    g.created -= 10                                        # past the settle time
    f = tm.followers[0]
    ft = g.legs["f1"]
    # master closes (e.g. hit TP in MT5) while the follower cannot be read
    tm.master.backend.c_close(g.legs["master"])
    f.backend.fail_reads = True
    cycle(tm, 3)
    assert ft in f.backend.positions_, "must not act on a stale follower"
    assert r["gid"] in tm.groups and tm.groups[r["gid"]].closing, "group must wait, not be dropped"
    assert any("STALE" in n[1] for n in hub.notes)
    f.backend.fail_reads = False
    cycle(tm, 2)
    assert ft not in f.backend.positions_, "follower leg must be closed once data is fresh"
    assert r["gid"] not in tm.groups


def test_per_account_risk_day():
    hub, tm = make_tm(1)
    tm.master.backend.server_time = 20000 * 86400 + 100
    tm.followers[0].backend.server_time = 20001 * 86400 + 100      # follower's broker is already on the next day
    cycle(tm)
    assert tm.master.day_key == 20000 and tm.followers[0].day_key == 20001, (tm.master.day_key, tm.followers[0].day_key)


def test_daily_dd_lock_per_account():
    hub, tm = make_tm(1)
    f = tm.followers[0]
    f.cfg.update(daily_dd=5, dd_mode="$")
    cycle(tm)
    f.backend.balance -= 10                                 # this account alone loses $10
    cycle(tm)
    assert f.locked and "DD" in f.locked and not tm.master.locked, (f.locked, tm.master.locked)


def test_close_scope_default_and_migration():
    assert trading.DEFAULT_RISK["close_scope"] == "superchart"
    with open(trading.CONFIG_FILE, "w") as fh:
        json.dump({"master": {"close_scope": "all"}, "followers": [{"id": "x", "close_scope": "all"}]}, fh)
    cfg = trading.load_config()
    assert cfg["master"]["close_scope"] == "superchart" and cfg["followers"][0]["close_scope"] == "superchart"
    trading.save_config(cfg)
    cfg["master"]["close_scope"] = "all"                    # an explicit choice after migration is kept
    trading.save_config(cfg)
    assert trading.load_config()["master"]["close_scope"] == "all"
    os.remove(trading.CONFIG_FILE)


def test_emergency_stop():
    hub, tm = make_tm(1)
    tm.set_armed(True)
    assert tm.open(1, 0.01, 5, 10)["ok"]
    tm.master.backend.c_place_pending("XAUUSD", 1, 0.01, 2700, 2690, 2720, "RGSC-N1")
    at = type("AT", (), {"enabled": True, "settings": {}, "set_enabled": lambda self, v: setattr(self, "enabled", v)})()
    hub.autotrader = at
    r = tm.stop_new_trades()
    assert r["ok"] and r["cancelled"] == 1 and not tm.armed and not at.enabled and tm.halted
    assert tm.master.backend.positions_, "STOP NEW TRADES must not close positions"
    tm.armed = True                                          # even if something re-arms internally...
    assert not tm.open(1, 0.01, 5, 10)["ok"], "halted must reject new trades until ARM is pressed again"
    r = tm.emergency_stop(close_positions=True)
    assert not tm.master.backend.positions_ and not tm.groups
    tm.set_armed(True)
    assert not tm.halted


def test_failed_order_journaled_and_stats_ok():
    hub, tm = make_tm(0)
    tm.set_armed(True)
    hub.last_tick = None                                     # no price -> paper order fails
    r = tm.open(1, 0.01, 5, 10)
    assert not r["ok"]
    failed = [e for e in tm.journal.entries.values() if e["status"] == "failed"]
    assert failed and failed[0]["result"] == "FAILED"
    st = tm.journal.stats()
    assert st["all"]["trades"] == 0                           # failed attempts never count as trades
    tm.journal.series()


def test_old_journal_compatible():
    path = os.path.join(TMP, "journal_paper.jsonl")
    old = dict(gid="old1", open_time=1, close_time=2, symbol="XAUUSD", side=1, lot=0.01, entry=2650, sl=2645, tp=2660,
               sl_dist=5, tp_dist=10, accounts=["Master"], score={}, paper=True, status="closed", partials=[],
               sl_changes=[], exit=2660, reason="TP", pnl=10.0, pnl_by_account={"Master": 10.0}, r=2.0,
               result="WIN", duration_min=1)
    with open(path, "w") as fh:
        fh.write(json.dumps(old) + "\n")
    j = journal.Journal(True)
    assert j.stats()["all"]["trades"] == 1 and j.series()[0]["pnl"] == 10.0
    j.lifecycle("old1", "NOTE", "added later")
    assert journal.Journal(True).entries["old1"]["lifecycle"][0]["state"] == "NOTE"


def test_close_group_waits_for_stale_follower():
    hub, tm = make_tm(1)
    tm.set_armed(True)
    r = tm.open(1, 0.01, 5, 10)
    g = tm.groups[r["gid"]]
    f = tm.followers[0]
    ft = g.legs["f1"]
    f.backend.fail_reads = True
    cycle(tm)                                               # follower becomes stale
    res = tm.close_group(r["gid"], 1.0, source="guard:Exit guard: TEST")
    assert r["gid"] in tm.groups and tm.groups[r["gid"]].closing, "must wait for the stale follower"
    assert ft in f.backend.positions_
    f.backend.fail_reads = False
    cycle(tm, 2)
    assert ft not in f.backend.positions_ and r["gid"] not in tm.groups
    time.sleep(2.5)
    e = tm.journal.entries[r["gid"]]
    assert e["status"] == "closed" and e["reason"] == "Exit guard: TEST", (e["status"], e.get("reason"))
