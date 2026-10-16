"""v2.8.54 SHADOW / TEST: live vs candidate on the same bars with the same simulator, paper only."""
import time
import types

from harness import TMP  # noqa: F401

import backtest
import position_manager as PM
import shadow as SD
import strategies as ST
from engine import Bar


def _data(days=12, seed=3):
    raw = backtest.synth_data(days, seed=seed)
    d = {k: raw[k] for k in ("M5", "M15", "H1", "H4")}
    d["M1"] = raw["_m1"]
    return d


def _cfg():
    c = ST.apply_profile(ST.default_settings(), "normal")
    for n in ST.NAMES:
        c[n]["auto"] = True
    return c


def test_candidate_applies_presets_only_to_the_copy():
    sc, pr = SD.candidate(_cfg(), PM.load_settings(), ["be1r_structure", "cvd_filter", "range_guard", "rr3"])
    assert pr["be_r"] == 1.0 and pr["trail_mode"] == "structure" and pr["trail_start_r"] == 1.0
    assert "cvd" in sc["trend"]["required"] and "no_range" in sc["trend"]["required"] and sc["trend"]["rr"] == 3.0
    assert "cvd_turn" in sc["pullback"]["required"] and sc["pullback"]["conditions"]["cvd_turn"]
    live = _cfg()
    assert "cvd" not in live["trend"]["required"] and not live["trend"]["conditions"]["no_range"]   # untouched


def test_no_presets_means_identical_arms_and_sane_trades():
    d = _data()
    r = SD.compare(d, _cfg(), PM.load_settings(), [], "M15", t_from=d["M15"][-1].t - 10 * 86400)
    assert r["live"]["stats"] == r["candidate"]["stats"] and r["live"]["stats"]["trades"] > 0
    for t in r["live"]["trades"]:
        assert t["res"] in ("WIN", "LOSS", "BE", "OPEN") and t["reason"] in ("SL", "TP", "FAIL", "OPEN")
        assert abs(t["entry"] - t["sl0"]) <= 20.0 + 1e-6                                # the $20 max SL
    keys = [t["key"] for t in r["live"]["trades"]]
    assert len(keys) == len(set(keys))
    by = {}
    for t in sorted(r["live"]["trades"], key=lambda x: x["t_entry"]):                 # one trade per strategy at a time
        assert t["t_entry"] >= by.get(t["strategy"], 0)
        by[t["strategy"]] = t["t_exit"]


def test_presets_change_the_candidate():
    d = _data()
    t0 = d["M15"][-1].t - 10 * 86400
    base = SD.compare(d, _cfg(), PM.load_settings(), ["rr3"], "M15", t_from=t0)
    assert base["live"]["stats"] != base["candidate"]["stats"]
    off = SD.compare(d, _cfg(), PM.load_settings(), ["no_conso"], "M15", t_from=t0)
    assert "CONSO" not in off["candidate"]["stats"]["by_strategy"]


def test_manage_sl_first_then_tp_and_failed_breakout():
    t0 = 1_760_000_000 - 1_760_000_000 % 900
    m1 = [Bar(t0 + 60 * k, 100, 100.5, 99.5, 100, 1) for k in range(30)]
    m1[3] = Bar(m1[3].t, 100, 106, 94, 100, 1)                                   # both SL and TP in one bar
    m15 = [Bar(t0 - 900 * (200 - k), 100, 101, 99, 100, 1) for k in range(200)] + [Bar(t0 + 900 * k, 100, 101, 99, 100, 1) for k in range(2)]
    cx = SD._Ctx({"M15": m15, "M1": m1}, "M15")
    pr = PM.clean(dict(PM.load_settings(), enabled=False))
    ex, t, why = SD._manage(cx, 1, 100.0, 95.0, 105.0, t0, pr)
    assert (why, ex) == ("SL", 95.0)                                              # conservative: SL first
    m1b = [Bar(t0 + 60 * k, 100, 100.4, 99.6, 101.5 if k == 14 else 100, 1) for k in range(30)]
    cx2 = SD._Ctx({"M15": m15, "M1": m1b}, "M15")
    ex, t, why = SD._manage(cx2, -1, 99.0, 104.0, 74.0, t0, pr, box=(99.5, 101.0), fail_atr=0.2)
    assert why == "FAIL" and t == m1b[14].t                                       # a SELL whose M15 closed back in the box


def test_shadow_forward_and_history_test_in_demo():
    d = _data(10)
    src = types.SimpleNamespace(live=False, bars=lambda tf: d.get(tf, []), symbol="XAUUSD")
    hub = types.SimpleNamespace(src=src, autotrader=None, lock=None)
    ST.save_settings(_cfg())
    sh = SD.Shadow(hub, start=False)
    v = sh.update_settings(dict(enabled=True, presets=["rr3"]))
    assert v["settings"]["enabled"] and v["settings"]["presets"] == ["rr3"] and v["settings"]["started_t"] > 0
    sh.settings["started_t"] = d["M15"][-1].t - 5 * 86400                          # pretend it ran for 5 days
    sh.tick()
    v = sh.view()
    assert v["arms"]["live"]["stats"]["trades"] > 0 and not sh.error
    sh.update_settings(dict(presets=["be1r_structure"]))                          # a new candidate restarts the test
    assert sh.view()["arms"]["live"]["stats"]["trades"] == 0
    assert sh.start_test(5, ["rr3"])["ok"]
    for _ in range(300):
        if sh.job["state"] != "running":
            break
        time.sleep(0.1)
    assert sh.job["state"] == "done", sh.job["msg"]
    res = sh.job["result"]
    assert res["source"].startswith("SIMULATED") and res["live"]["trades"] > 0 and res["presets"] == ["rr3"]
    ST.save_settings(ST.default_settings())
