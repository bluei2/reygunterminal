"""v2.8.28: trailing (step 0.5R from +1R) + CONSO protected (Eugene: 4170 -> 4180+ then back)."""
import json
import os
import sys

sys.path.insert(0, "..")
import position_manager as PM   # noqa: E402
import autotrade as AT          # noqa: E402


def test_step_trailing_example_conso_buy():
    c = PM.clean(dict(trail_mode="step", step_r=0.5, trail_start_r=1.0))
    # CONSO BUY 4170, SL $8 (4162): price reached 4182 (+1.5R) -> SL +1R = 4178 (profit locked, not a loss)
    p = PM.plan(1, 4170.0, 4162.0, 4182.0, 8.0, c)
    assert p["new_sl"] == 4178.0 and p["rule"] == "TRAIL_STEP", p
    p2 = PM.plan(1, 4170.0, 4178.0, 4179.0, 8.0, c)               # price came back: the stop is never loosened
    assert p2["new_sl"] is None


def test_defaults_and_migration_once(tmp_path=None):
    d = PM.clean(None)
    assert d["trail_mode"] == "step" and d["step_r"] == 0.5 and d["trail_start_r"] == 0.5     # v2.8.33
    old = PM.SETTINGS_FILE
    path = "_pm_test.json"
    PM.SETTINGS_FILE = path
    try:
        with open(path, "w") as f:
            json.dump(dict(enabled=True, be_r=1.0, profit_lock=[[1.5, 0.5]], trail_mode="none", step_r=1.0, version=2), f)
        s = PM.load_settings()
        assert s["trail_mode"] == "step" and s["step_r"] == 0.5 and s["profit_lock"] == [[1.5, 0.5]]
        with open(path, "w") as f:
            json.dump(dict(s, trail_mode="none", version=4), f)       # switched off later: kept
        assert PM.load_settings()["trail_mode"] == "none"
    finally:
        PM.SETTINGS_FILE = old
        if os.path.exists(path):
            os.remove(path)


def test_conso_protect_on_once():
    assert AT.DEFAULTS["conso_protect"] is True
    old = AT.SETTINGS_FILE
    path = "_at_test.json"
    AT.SETTINGS_FILE = path
    try:
        with open(path, "w") as f:
            json.dump(dict(conso_protect=False, settings_version=4), f)
        assert AT.load_settings()["conso_protect"] is True
        with open(path, "w") as f:
            json.dump(dict(conso_protect=False, settings_version=5), f)
        assert AT.load_settings()["conso_protect"] is False
    finally:
        AT.SETTINGS_FILE = old
        if os.path.exists(path):
            os.remove(path)


def test_manual_and_ea_trades_are_trailed_too():
    """v2.8.31 (Eugene: 'kahit anong trade, manual, EA man o SuperChart'): copied manual / EA trades get BE + trailing."""
    from harness import cycle
    from test_copy_manual import _setup, _manual
    for magic in (0, 26200001):
        hub, tm = _setup()
        t = _manual(tm, side=-1, sl_d=10.0, tp_d=20.0, magic=magic)   # SELL, SL $10
        tm.refresh(); tm.copy_manual()
        g = next(x for x in tm.groups.values() if x.kind == "copy")
        pc = PM.PositionController(tm)
        pc.running = False
        pc.settings = PM.clean(dict(enabled=True, trail_mode="step", step_r=0.5, trail_start_r=1.0))
        hub.set_price(g.m_open - 16.0)                                # +1.5R (after the spread)
        cycle(tm)
        pc.step()
        cycle(tm)
        mp = tm.master.pos(t)
        assert abs(mp["sl"] - (mp["open"] - 10.0)) < 0.3, (magic, mp)  # SL locked at +1R
        fp = tm.followers[0].pos(g.legs["f1"])
        assert fp["sl"] < fp["open"], fp                               # the follower too


def test_manual_trades_left_alone_when_switched_off():
    from harness import cycle
    from test_copy_manual import _setup, _manual
    hub, tm = _setup()
    t = _manual(tm, side=1, sl_d=10.0, tp_d=20.0)
    tm.refresh(); tm.copy_manual()
    sl0 = tm.master.pos(t)["sl"]
    pc = PM.PositionController(tm)
    pc.running = False
    pc.settings = PM.clean(dict(enabled=True, manage_manual=False))
    hub.set_price(tm.master.pos(t)["open"] + 15.0)
    cycle(tm); pc.step(); cycle(tm)
    assert tm.master.pos(t)["sl"] == sl0


def test_one_to_one_trade_is_protected_halfway_to_tp():
    """v2.8.32 (Eugene: '1:1 lng kc ung trade'): SL $10, TP $10 -> 1R = $5 (half the TP): at +$7.5 the SL is +$5."""
    from harness import cycle
    from test_copy_manual import _setup, _manual
    hub, tm = _setup()
    t = _manual(tm, side=-1, sl_d=10.0, tp_d=10.0)
    tm.refresh(); tm.copy_manual()
    pc = PM.PositionController(tm)
    pc.running = False
    pc.settings = PM.clean(dict(enabled=True, trail_mode="step", step_r=0.5, trail_start_r=1.0, be_r=1.0, tp_scale=True))
    o = tm.master.pos(t)["open"]
    hub.set_price(o - 7.8)                                         # +0.78 real R = +1.5 "TP-R" (after the spread)
    cycle(tm); pc.step(); cycle(tm)
    mp = tm.master.pos(t)
    assert abs(mp["sl"] - (o - 5.0)) < 0.3, mp                     # +$5 locked (half the TP)
    pc.settings = PM.clean(dict(enabled=True, trail_mode="step", step_r=0.5, trail_start_r=1.0, be_r=1.0, tp_scale=False))
    hub2, tm2 = _setup()
    t2 = _manual(tm2, side=-1, sl_d=10.0, tp_d=10.0)
    tm2.refresh(); tm2.copy_manual()
    pc2 = PM.PositionController(tm2)
    pc2.running = False
    pc2.settings = pc.settings
    o2 = tm2.master.pos(t2)["open"]
    sl2 = tm2.master.pos(t2)["sl"]
    hub2.set_price(o2 - 7.8)
    cycle(tm2); pc2.step(); cycle(tm2)
    assert tm2.master.pos(t2)["sl"] == sl2                          # tp_scale OFF: nothing before +1R


def test_one_to_two_trade_unchanged():
    c = PM.clean(dict(trail_mode="step", step_r=0.5, trail_start_r=1.0))
    p = PM.plan(1, 4170.0, 4162.0, 4182.0, 8.0, c)                  # the 1:2 rules are untouched
    assert p["new_sl"] == 4178.0


def test_v2833_half_r_ladder():
    """0.5R = BE, 1R -> lock 0.5R, 1.5R -> lock 1R ... (Eugene)."""
    c = PM.clean(None)
    assert c["be_r"] == 0.5 and c["trail_start_r"] == 0.5 and c["step_r"] == 0.5 and c["tp_scale"] is False
    e, R = 4100.0, 10.0
    assert PM.plan(1, e, e - R, e + 4.0, R, c)["new_sl"] is None                    # +0.4R: nothing yet
    assert PM.plan(1, e, e - R, e + 5.0, R, c)["new_sl"] == round(e + 0.2, 2)       # +0.5R: BE (+$0.20)
    assert PM.plan(1, e, e - R, e + 10.0, R, c)["new_sl"] == e + 5.0                # +1R: lock 0.5R
    assert PM.plan(1, e, e - R, e + 15.0, R, c)["new_sl"] == e + 10.0               # +1.5R: lock 1R
    assert PM.plan(-1, e, e + R, e - 10.0, R, c)["new_sl"] == e - 5.0               # SELL the same
    old = PM.SETTINGS_FILE
    path = "_pm_test4.json"
    PM.SETTINGS_FILE = path
    try:
        with open(path, "w") as f:
            json.dump(dict(enabled=True, be_r=1.0, profit_lock=[[1.5, 0.5], [1.8, 1.0]], trail_mode="step",
                           step_r=0.5, trail_start_r=1.0, version=3), f)
        s = PM.load_settings()
        assert s["be_r"] == 0.5 and s["trail_start_r"] == 0.5 and s["profit_lock"] == [[1.5, 0.5], [1.8, 1.0]]
    finally:
        PM.SETTINGS_FILE = old
        if os.path.exists(path):
            os.remove(path)
