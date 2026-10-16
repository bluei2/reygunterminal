"""v2.8.58: the "BE 1R + M15 structure" candidate, the simulator trails on the chosen TF, GAMITIN SA LIVE writes only
what the last history TEST tested (and the settings history keeps the state before)."""
import os
import time
import types

from harness import TMP

import position_manager as PM
import settings_history as SH
import shadow as SD
import strategies as ST
from test_shadow import _cfg, _data


def test_be1r_m15_candidate():
    sc, pr = SD.candidate(_cfg(), PM.load_settings(), ["be1r_m15"])
    assert pr["be_r"] == 1.0 and pr["trail_mode"] == "structure" and pr["trail_tf"] == "M15" and pr["profit_lock"] == []
    assert PM.load_settings()["be_r"] == 0.5                                       # live untouched


def test_simulator_trails_on_the_chosen_tf():
    d = _data(12)
    cx = SD._Ctx(d, "M15")
    ts = [b.t for b in d["M15"][-200:]]
    s5 = [cx.swing_at(t, 1, "M5") for t in ts]
    s15 = [cx.swing_at(t, 1, "M15") for t in ts]
    assert any(x is not None for x in s5) and any(x is not None for x in s15) and s5 != s15
    t0 = d["M15"][-1].t - 10 * 86400
    a = SD.compare(d, _cfg(), PM.load_settings(), ["be1r_structure"], "M15", t_from=t0)
    b = SD.compare(d, _cfg(), PM.load_settings(), ["be1r_m15"], "M15", t_from=t0)
    assert a["live"]["stats"] == b["live"]["stats"]                                # same live arm
    assert a["candidate"]["stats"]["trades"] > 0 and b["candidate"]["stats"]["trades"] > 0


def test_apply_only_what_was_tested_and_history_keeps_before():
    path = os.path.join(TMP, "sh_apply.jsonl")
    if os.path.exists(path):
        os.remove(path)
    ST.save_settings(_cfg())
    if os.path.exists(PM.SETTINGS_FILE):
        os.remove(PM.SETTINGS_FILE)
    hist = SH.SettingsHistory("9.9.9", path=path, start=False)
    pm = types.SimpleNamespace(settings=PM.load_settings())
    pm.update_settings = lambda new: (setattr(pm, "settings", PM.clean(dict(pm.settings, **new))),
                                      open(PM.SETTINGS_FILE, "w").write(__import__("json").dumps(dict(pm.settings, version=4))))
    d = _data(8)
    src = types.SimpleNamespace(live=False, bars=lambda tf: d.get(tf, []), symbol="XAUUSD")
    hub = types.SimpleNamespace(src=src, autotrader=None, lock=None, settings_hist=hist, positions_mgr=pm)
    sh = SD.Shadow(hub, start=False)
    assert not sh.apply(["be1r_m15"])["ok"]                                         # never tested: refused
    assert sh.start_test(5, ["be1r_m15"])["ok"]
    for _ in range(300):
        if sh.job["state"] != "running":
            break
        time.sleep(0.1)
    assert sh.job["state"] == "done", sh.job["msg"]
    assert not sh.apply(["rr3"])["ok"]                                              # a different candidate: refused
    r = sh.apply(["be1r_m15"])
    assert r["ok"] and any("be_r: 0.5 → 1.0" in c for c in r["changes"]), r
    live = PM.load_settings()
    assert live["be_r"] == 1.0 and live["trail_mode"] == "structure" and live["trail_tf"] == "M15"
    assert pm.settings["be_r"] == 1.0                                               # the running manager too
    reasons = [x["reason"] for x in hist.list()]
    assert reasons[0].startswith("GAMITIN SA LIVE"), reasons                     # the state before = the row below
    before = hist.list()[1]
    assert hist.restore(before["id"], hub)["ok"] and PM.load_settings()["be_r"] == 0.5   # one-click back
    os.remove(PM.SETTINGS_FILE)
    ST.save_settings(ST.default_settings())
