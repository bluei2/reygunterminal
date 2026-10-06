"""v2.8.24: daily DD / TP in pips (1 gold pip = $0.10 price move), per account base lot."""
import sys
import time

sys.path.insert(0, "..")
from harness import make_tm, cycle   # noqa: E402
import trading as T                  # noqa: E402


def test_pips_mode_cleaned_and_converted():
    f = T.clean_follower({"name": "F", "lot": 0.02, "daily_dd": 400, "dd_mode": "pips", "daily_tp": 1200, "tp_mode": "pips"})
    assert f["dd_mode"] == "pips" and f["tp_mode"] == "pips"
    hub, tm = make_tm(1)
    fo = tm.followers[0]
    fo.cfg.update(lot=0.02, daily_dd=400, dd_mode="pips", daily_tp=1200, tp_mode="pips")
    fo.broker = dict(fo.broker or {}, contract_size=100.0)
    assert abs(fo.limits()[0] - 80.0) < 1e-9 and abs(fo.limits()[1] - 240.0) < 1e-9     # 0.02 lot: $80 / $240
    m = tm.master
    m.cfg.update(daily_dd=400, dd_mode="pips")
    m.broker = dict(m.broker or {}, contract_size=100.0)
    m.base_lot = 0.01
    assert abs(m.limits()[0] - 40.0) < 1e-9                                               # 0.01 lot: $40
    m.cfg["dd_mode"] = "$"
    assert m.limits()[0] == 400                                                           # $ mode unchanged


def test_pips_lock_triggers():
    hub, tm = make_tm(0)
    m = tm.master
    m.cfg.update(daily_dd=200, dd_mode="pips")
    m.broker = dict(m.broker or {}, contract_size=100.0)
    tm.refresh()
    m.day_start = float(m.info["equity"]) + 25.0          # down $25 today > 200 pips at 0.01 ($20)
    tm.refresh()
    cycle(tm, 1)
    assert m.locked, m.view()


def test_master_dd_200_pips_once():
    import json
    import os
    old = T.CONFIG_FILE
    path = os.path.join(os.path.dirname(old) if os.path.dirname(old) else ".", "_cfg_test_pips.json")
    T.CONFIG_FILE = path
    try:
        with open(path, "w") as f:
            json.dump({"master": {"daily_dd": 20.0, "dd_mode": "$", "daily_tp": 60.0, "tp_mode": "$"},
                       "followers": [], "master_preset": 1, "risk_version": T.RISK_VERSION}, f)
        cfg = T.load_config()
        assert cfg["master"]["daily_dd"] == 400.0 and cfg["master"]["dd_mode"] == "pips"
        assert cfg["master"]["daily_tp"] == 60.0 and cfg["master"]["tp_mode"] == "$"      # TP untouched
        cfg["master"].update(daily_dd=300.0)                                             # your later change
        T.save_config(cfg)
        again = T.load_config()
        assert again["master"]["daily_dd"] == 300.0 and again["master"]["dd_mode"] == "pips"
    finally:
        T.CONFIG_FILE = old
        if os.path.exists(path):
            os.remove(path)



def test_master_200_pips_from_v2825_becomes_400():
    import json
    import os
    old = T.CONFIG_FILE
    path = "_cfg_test_pips2.json"
    T.CONFIG_FILE = path
    try:
        with open(path, "w") as f:
            json.dump({"master": {"daily_dd": 200.0, "dd_mode": "pips"}, "followers": [], "master_preset": 1,
                       "master_dd_pips": 1, "risk_version": T.RISK_VERSION}, f)
        assert T.load_config()["master"]["daily_dd"] == 400.0
        with open(path, "w") as f:
            json.dump({"master": {"daily_dd": 250.0, "dd_mode": "pips"}, "followers": [], "master_preset": 1,
                       "master_dd_pips": 1, "risk_version": T.RISK_VERSION}, f)
        assert T.load_config()["master"]["daily_dd"] == 250.0          # your own value is kept
    finally:
        T.CONFIG_FILE = old
        if os.path.exists(path):
            os.remove(path)
