"""Position management controller: priority, tighten-only, BE / profit lock / trailing / partial, broker safety."""
import time

from harness import cycle, make_tm

import position_manager as PM


def cfg(**kw):
    # these rule tests run without the v2.8.28 default trailing unless they ask for one
    return PM.clean(dict(dict(enabled=True, require_armed=True, trail_mode="none"), **kw))


def test_break_even_then_profit_lock_most_protective_wins():
    c = cfg(be_r=1.0, be_offset=0.1, profit_lock=[[2.0, 1.0]])
    p = PM.plan(1, 100.0, 95.0, 105.5, 5.0, c)                  # +1.1R -> BE
    assert p["rule"] == "BREAK_EVEN" and p["new_sl"] == 100.1 and p["mode"] == "BE_ACTIVE", p
    p = PM.plan(1, 100.0, 100.1, 110.5, 5.0, c)                 # +2.1R -> lock +1R beats BE
    assert p["rule"] == "PROFIT_LOCK" and p["new_sl"] == 105.0 and p["mode"] == "PROFIT_LOCK_ACTIVE", p


def test_never_loosens():
    c = cfg(be_r=1.0, trail_mode="atr", trail_start_r=1.0, trail_atr_mult=3.0)
    # SL already at +1.6R; the ATR trail would put it lower (price - 3 x 2 = 104) -> no change
    p = PM.plan(1, 100.0, 108.0, 110.0, 5.0, c, atr=2.0)
    assert p["new_sl"] is None, p
    s = PM.plan(-1, 100.0, 92.0, 90.0, 5.0, c, atr=2.0)          # SELL mirror
    assert s["new_sl"] is None, s


def test_step_and_structure_trailing():
    c = cfg(trail_mode="step", step_r=1.0, trail_start_r=1.0)
    p = PM.plan(1, 100.0, 95.0, 111.0, 5.0, c)                  # +2.2R -> SL one step behind = +1R
    assert p["rule"] == "TRAIL_STEP" and p["new_sl"] == 105.0, p
    c2 = cfg(trail_mode="structure", structure_buffer=0.3, trail_start_r=1.0)
    p2 = PM.plan(1, 100.0, 95.0, 107.0, 5.0, c2, swing=104.0)
    assert p2["rule"] == "TRAIL_STRUCTURE" and p2["new_sl"] == 103.7 and p2["mode"] == "TRAIL_ACTIVE", p2


def test_broker_stops_and_freeze_level():
    c = cfg(be_r=1.0, be_offset=0.1)
    p = PM.plan(1, 100.0, 95.0, 105.0, 5.0, c, broker=dict(point=0.01, stops_level=600))   # min 6.00 from price
    assert p["new_sl"] is not None and 105.0 - p["new_sl"] >= 6.0, p
    f = PM.plan(1, 100.0, 104.5, 105.0, 5.0, cfg(profit_lock=[[0.5, 0.95]]), broker=dict(point=0.01, freeze_level=100))
    assert f["new_sl"] is None and "freeze" in (f["blocked"] or ""), f


def test_partial_close_once():
    c = cfg(partial_close=[[1.0, 0.5]])
    p = PM.plan(1, 100.0, 95.0, 105.5, 5.0, c)
    assert p["partials"] and p["partials"][0]["fraction"] == 0.5
    p2 = PM.plan(1, 100.0, 95.0, 106.0, 5.0, c, done_partials={p["partials"][0]["key"]})
    assert not p2["partials"]


def test_controller_moves_sl_on_all_accounts_and_journals():
    hub, tm = make_tm(1)
    tm.set_armed(True)
    assert tm.open(1, 0.01, 5.0, 20.0)["ok"]
    cycle(tm, 2)
    pc = PM.PositionController(tm)
    pc.running = False
    pc.settings = cfg(be_r=1.0, be_offset=0.1)
    gid = next(iter(tm.groups))
    g = tm.groups[gid]
    hub.set_price(g.m_open + 6.0)                                  # +1.2R
    cycle(tm)
    pc.step()
    cycle(tm)
    for acc_id, t in g.legs.items():
        a = tm._acct(acc_id)
        pos = a.pos(t)
        assert pos["sl"] >= pos["open"], (acc_id, pos)
    e = tm.journal.entries[gid]
    assert any(str(x["source"]).startswith("position_manager:BREAK_EVEN") for x in e["sl_changes"]), e["sl_changes"]
    assert g.pm_mode == "BE_ACTIVE"


def test_controller_skips_unprotected_and_disarmed():
    hub, tm = make_tm(0)
    tm.set_armed(True)
    tm.master.backend.drop_sl = True
    tm.safety["close_unprotected"] = False
    tm.safety["protective_sl_retry_count"] = 0
    assert tm.open(1, 0.01, 5.0, 20.0)["ok"]
    cycle(tm)
    pc = PM.PositionController(tm)
    pc.running = False
    pc.settings = cfg(be_r=0.5)
    gid = next(iter(tm.groups))
    hub.set_price(tm.groups[gid].m_open + 5.0)
    cycle(tm)
    pc.step()
    assert gid not in pc.status                                   # emergency protection has priority
    tm.set_armed(False)
    pc.step()
    assert gid not in pc.status


def test_breakeven_button_never_loosens():
    hub, tm = make_tm(0)
    tm.set_armed(True)
    assert tm.open(1, 0.01, 5.0, 20.0)["ok"]
    cycle(tm)
    gid = next(iter(tm.groups))
    mp = tm.master.pos(tm.groups[gid].legs["master"])
    hub.set_price(mp["open"] + 6.0)
    cycle(tm)
    tm.modify_group(gid, mp["open"] + 3.0, mp["tp"])             # SL already locks +3
    cycle(tm)
    tm.breakeven(gid)
    cycle(tm)
    assert tm.master.pos(tm.groups[gid].legs["master"])["sl"] == round(mp["open"] + 3.0, 2)


def test_profit_protection_is_on_by_default_and_old_off_files_upgrade():
    """Eugene: 'dapat ON na agad': break-even at 1R + lock 0.5R at 1.25R from the start; an old file that only had the
    old OFF defaults gets them, a file where the user chose values keeps them."""
    import json
    import position_manager as PM
    d = PM.clean(None)
    assert d["enabled"] and d["be_r"] == 0.5 and d["profit_lock"] == [[1.25, 0.5]] and d["trail_mode"] == "step"
    with open(PM.SETTINGS_FILE, "w") as f:
        json.dump(dict(enabled=False, be_r=0.0, profit_lock=[], trail_mode="none"), f)
    s = PM.load_settings()
    assert s["enabled"] and s["be_r"] == 0.5
    with open(PM.SETTINGS_FILE, "w") as f:
        json.dump(dict(enabled=True, be_r=2.0, profit_lock=[], version=4), f)
    s = PM.load_settings()
    assert s["be_r"] == 2.0 and s["profit_lock"] == []                 # your own choice stays
    with open(PM.SETTINGS_FILE, "w") as f:
        json.dump(dict(version=2, enabled=False, be_r=0.0, profit_lock=[]), f)
    assert PM.load_settings()["enabled"] is False                        # switched OFF on purpose (saved by v2.6.1)
    import os
    os.remove(PM.SETTINGS_FILE)
