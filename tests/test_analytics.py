"""Step 5 (analytics, MFE/MAE, exit classes) tests."""
import time

from harness import cycle, make_tm

import trade_analytics as ta


def test_mfe_mae_recorded_and_exit_class():
    hub, tm = make_tm(0)
    tm.set_armed(True)
    r = tm.open(1, 0.01, 5, 10)
    g = tm.groups[r["gid"]]
    base = g.m_open
    for px in (base + 3.0, base - 2.0, base + 1.0):               # +3 then -2 against, then back
        hub.set_price(px)
        cycle(tm)
    tm.close_group(r["gid"], 1.0, source="guard:Exit guard: TREND CHANGE DOWN")
    time.sleep(2.5)
    e = tm.journal.entries[r["gid"]]
    assert e["exit_reason"] == "EXIT_GUARD", e.get("exit_reason")
    assert e["mfe_r"] is not None and e["mfe_r"] >= 0.55 and e["mae_r"] <= -0.35, (e["mfe_r"], e["mae_r"])
    assert e["time_to_mfe"] is not None


def test_classify_exit():
    c = tm_cls = __import__("trading").TradeManager.classify_exit
    assert c("TP", "master", 2.0) == "TP"
    assert c("SL", "master", -1.0) == "SL"
    assert c("SL", "master", 0.02) == "BE"
    assert c("SL", "master", 0.8) == "TRAIL"
    assert c("x", "risk", -0.5) == "RISK_LOCK"
    assert c("x", "emergency", 0.0) == "SYSTEM"
    assert c("x", "superchart", 0.0) == "MANUAL"


def test_analytics_groups_and_old_entries():
    old = dict(gid="o", status="closed", result="WIN", r=2.0, pnl=10, open_time=1790000000, close_time=1790000600,
               reason="TP", duration_min=10)
    new = dict(gid="n", status="closed", result="LOSS", r=-1.0, pnl=-5, open_time=1790001000, close_time=1790001300,
               exit_reason="SL", session="LONDON", regime={"label": "RANGE"},
               setup_fingerprint={"key": "M5 CHOCH + DISCOUNT"}, mfe_r=0.4, mae_r=-1.0, duration_min=5)
    failed = dict(gid="f", status="failed", result="FAILED")
    a = ta.all_analytics([old, new, failed])
    ex = {x["key"]: x for x in a["exits"]}
    assert ex["TP"]["trades"] == 1 and ex["SL"]["trades"] == 1
    assert a["overall"]["trades"] == 2 and a["overall"]["total_r"] == 1.0 and a["overall"]["max_dd_r"] == -1.0
    assert {x["key"] for x in a["sessions"]} >= {"LONDON"}
    assert any(x["key"] == "M5 CHOCH + DISCOUNT" for x in a["setups"])
    assert a["mfe_mae"]["summary"]["trades"] == 1 and a["mfe_mae"]["trades"][0]["left_on_table_r"] == 1.4
