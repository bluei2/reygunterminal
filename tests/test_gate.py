"""MASTER AUTO-TRADE GATE: every gate, machine-readable codes, order of gates, duplicates, signal journal, AutoTrader."""
import json
import os
import threading
import time
import types

from harness import TMP, cycle, make_tm

import decision_engine
from engine import Event, Read, TFState, Tick
from execution_guard import ExecutionGuard
from risk_engine import RiskEngine
from trade_gate import GATES, MasterGate, signal_id


class Src:
    symbol, live, name, paused = "XAUUSD", False, "TEST", False

    def bars(self, tf):
        return []


def setup(score=80.0, bias=1, trigger=True, spread=0.3, armed=True, auto_on=True, news_block=False, n_followers=0,
          risk=None):
    hub, tm = make_tm(n_followers)
    hub.src = Src()
    now = int(time.time())
    hub.last_tick = Tick(now * 1000, 2650.0, 2650.0 + spread, 1.0)
    hub.last_tick_wall = time.time()
    hub._last_analysis = time.time()
    hub.data_status = dict(ok=True, issues=[], t=time.time())
    hub.read = Read(bias=bias, overall=score, passing=70.0, trigger=trigger, verdict="test", groups=[],
                    passed=score >= 70)
    st = {k: TFState(tf=k, trend=bias) for k in ("H4", "H1", "M15", "M5")}
    if trigger:
        st["M5"].events.append(Event("M5", "CHoCH", bias, 2649.0, now - 60))
    hub.states = st
    hub.smc = None
    hub.autotrader = types.SimpleNamespace(enabled=auto_on, count_today=0, _note=lambda *a, **k: None,
                                           settings=dict(max_spread=0.6, sessions=[], max_open=1, max_per_day=3,
                                                         no_opposite=True, news_block=news_block, reentry_minutes=30))
    if risk:
        with open(os.path.join(TMP, "risk_settings.json"), "w") as f:
            json.dump(risk, f)
    hub.risk = RiskEngine(hub)
    hub.execution = ExecutionGuard(hub)
    hub.execution.settings.update(require_protective_sl=True, max_spread=0.6)
    de = decision_engine.DecisionEngine.__new__(decision_engine.DecisionEngine)
    de.hub, de.settings, de.timeline, de.last, de._prev, de._ob_cache = hub, decision_engine.clean_settings(None), \
        __import__("collections").deque(maxlen=80), None, {}, (None, None)
    de.lock = threading.Lock()
    hub.decision = de
    cycle(tm)
    if armed:
        tm.set_armed(True)
    hub.gate = MasterGate(hub, paper=True)
    return hub, tm, hub.gate


def sig(gate, side=1, t=None, sl=5.0, tp=10.0, src="analyser"):
    t = t or int(time.time()) // 300 * 300
    e = 2650.0
    return gate.make_signal(src, "M5", t, side, e, e - side * sl if sl is not None else None,
                            e + side * tp if tp is not None else None, 80, [])


def test_all_gates_pass():
    hub, tm, g = setup()
    r = g.evaluate(sig(g), "auto", 0.01)
    assert r["allowed"], (r["block_code"], r["block_reason"])
    assert [s["result"] for s in r["stages"]] == ["BUY_SETUP_DETECTED", "BUY_ALLOWED", "RISK_ALLOWED", "EXECUTION_ALLOWED"]
    assert r["explanation"]["head"] == "BUY APPROVED" and r["explanation"]["reasons"]


def test_master_disabled():
    hub, tm, g = setup()
    hub.risk.set_master(False, "test")
    r = g.evaluate(sig(g), "auto", 0.01)
    assert r["block_code"] == "BLOCKED_MASTER_DISABLED", r["block_code"]
    assert not tm.open(1, 0.01, 5, 10)["ok"]                        # manual entries too
    hub.risk.set_master(True)
    assert RiskEngine(hub).master_enabled                            # persisted


def test_corrupt_master_file_fails_safe():
    hub, tm, g = setup()
    with open(os.path.join(TMP, "master_switch.json"), "w") as f:
        f.write("{not json")
    hub.risk = RiskEngine(hub)
    assert not hub.risk.master_enabled
    assert g.evaluate(sig(g), "auto", 0.01)["block_code"] == "BLOCKED_MASTER_DISABLED"


def test_invalid_risk_config_fails_safe():
    hub, tm, g = setup(risk=dict(max_risk_per_trade_pct="lots"))
    assert hub.risk.config_error
    assert g.evaluate(sig(g), "auto", 0.01)["block_code"] == "BLOCKED_MASTER_DISABLED"


def test_auto_disabled():
    hub, tm, g = setup(auto_on=False)
    assert g.evaluate(sig(g), "auto", 0.01)["block_code"] == "BLOCKED_AUTO_DISABLED"


def test_not_armed():
    hub, tm, g = setup(armed=False)
    assert g.evaluate(sig(g), "auto", 0.01)["block_code"] == "BLOCKED_NOT_ARMED"


def test_gate_order_master_before_arm():
    hub, tm, g = setup(armed=False, auto_on=False)
    hub.risk.set_master(False)
    r = g.evaluate(sig(g), "auto", 0.01)
    assert r["block_gate"] == "MASTER" and r["block_code"] == "BLOCKED_MASTER_DISABLED"
    failing = [x["gate"] for x in r["gates"] if not x["ok"]]
    assert failing == [x for x in GATES if x in failing] and {"MASTER", "AUTO", "ARM"} <= set(failing), failing


def test_stale_data():
    hub, tm, g = setup()
    hub.last_tick_wall = time.time() - 120
    assert g.evaluate(sig(g), "auto", 0.01)["block_code"] == "BLOCKED_STALE_DATA"
    hub2, tm2, g2 = setup()
    hub2.data_status = dict(ok=False, issues=[dict(code="BAR_INVALID", text="M5 OHLC inconsistent", tf="M5")],
                            t=time.time())
    r = g2.evaluate(sig(g2), "auto", 0.01)
    assert r["block_code"] == "BLOCKED_STALE_DATA" and "BAR_INVALID" in r["block_reason"], r["block_reason"]


def test_invalid_sl_and_tp():
    hub, tm, g = setup()
    assert g.evaluate(sig(g, sl=0.0), "auto", 0.01)["block_code"] == "BLOCKED_INVALID_SL"
    hub2, tm2, g2 = setup(risk=dict(require_tp=True))
    assert g2.evaluate(sig(g2, tp=0.0), "auto", 0.01)["block_code"] == "BLOCKED_INVALID_TP"
    hub3, tm3, g3 = setup(risk=dict(min_rr=3.0))
    assert g3.evaluate(sig(g3, sl=5, tp=5), "auto", 0.01)["block_code"] == "BLOCKED_RISK"


def test_invalid_lot():
    hub, tm, g = setup()
    tm.master.broker.update(lot_step=0.1, min_lot=0.1)
    r = g.evaluate(sig(g), "auto", 0.15)
    assert r["block_code"] == "BLOCKED_EXECUTION" and "INVALID_LOT" in r["block_reason"], r


def test_risk_per_trade():
    hub, tm, g = setup(risk=dict(max_risk_per_trade_usd=3.0))
    r = g.evaluate(sig(g, sl=5), "auto", 0.01)                     # $5 at the SL > $3
    assert r["block_code"] == "BLOCKED_RISK" and "RISK_PER_TRADE" in r["block_reason"]


def test_dd_limit_and_daily_target():
    hub, tm, g = setup()
    tm.master.locked = "daily DD -2.0% hit"
    assert g.evaluate(sig(g), "auto", 0.01)["block_code"] == "BLOCKED_DD_LIMIT"
    hub2, tm2, g2 = setup()
    tm2.master.locked = "daily TP +3.0% hit"
    assert g2.evaluate(sig(g2), "auto", 0.01)["block_code"] == "BLOCKED_DAILY_TARGET"


def test_total_dd_limit():
    hub, tm, g = setup(risk=dict(max_total_dd_pct=5.0))
    hub.risk.peak = {str(tm.master.info.get("login") or tm.master.id) + "-paper": tm.master.info["equity"] * 1.2}
    r = g.evaluate(sig(g), "auto", 0.01)
    assert r["block_code"] == "BLOCKED_DD_LIMIT" and "MAX_TOTAL_DD" in r["block_reason"], r["block_reason"]


def test_max_trades_and_max_open():
    hub, tm, g = setup(risk=dict(max_trades_per_day=1))
    assert tm.open(1, 0.01, 5, 10)["ok"]
    cycle(tm)
    r = g.evaluate(sig(g), "auto", 0.01)
    assert "BLOCKED_MAX_TRADES" in [c for x in r["gates"] for c in x["codes"]], r["gates"]
    hub2, tm2, g2 = setup(risk=dict(max_open_positions=1))
    hub2.autotrader.settings["max_open"] = 0
    assert tm2.open(1, 0.01, 5, 10)["ok"]
    cycle(tm2)
    r2 = g2.evaluate(sig(g2), "auto", 0.01)
    assert "BLOCKED_MAX_OPEN_POSITIONS" in [c for x in r2["gates"] for c in x["codes"]]


def test_opposite_position():
    hub, tm, g = setup()
    assert tm.open(-1, 0.01, 5, 10)["ok"]
    cycle(tm)
    r = g.evaluate(sig(g, side=1), "auto", 0.01)
    assert "BLOCKED_OPPOSITE_POSITION" in [c for x in r["gates"] for c in x["codes"]]


def test_duplicate_signal():
    hub, tm, g = setup()
    s = sig(g)
    assert g.evaluate(s, "auto", 0.01)["allowed"]
    r = g.evaluate(s, "auto", 0.01)
    assert r["block_code"] == "BLOCKED_DUPLICATE_SIGNAL"
    assert signal_id("XAUUSD", "M5", 1, 1, "x", "a") == signal_id("XAUUSD", "M5", 1, 1, "x", "a")
    assert signal_id("XAUUSD", "M5", 1, 1, "x", "a") != signal_id("XAUUSD", "M5", 1, -1, "x", "a")
    # the journal survives a restart: a new gate still knows the id
    assert MasterGate(hub, paper=True).is_duplicate(s["signal_id"])


def test_score_and_structure_required():
    hub, tm, g = setup(score=50.0)
    assert g.evaluate(sig(g), "auto", 0.01)["block_code"] == "BLOCKED_SCORE"
    hub2, tm2, g2 = setup(bias=-1)
    r = g2.evaluate(sig(g2, side=1), "auto", 0.01)
    assert r["block_code"] == "BLOCKED_STRUCTURE", r["block_code"]


def test_news_unknown_policy():
    hub, tm, g = setup(news_block=True)                              # no news reader -> NEWS_UNKNOWN
    r = g.evaluate(sig(g), "auto", 0.01)
    assert r["block_code"] == "BLOCKED_NEWS" and "NEWS_UNKNOWN" in r["block_reason"]
    hub2, tm2, g2 = setup(news_block=True, risk=dict(news_unknown="allow"))
    assert g2.evaluate(sig(g2), "auto", 0.01)["allowed"]
    hub3, tm3, g3 = setup(news_block=True, risk=dict(news_unknown="reduce_risk"))
    r3 = g3.evaluate(sig(g3), "auto", 0.04)
    assert r3["allowed"] and r3["lot"] == 0.02 and r3["lot_notes"], r3


def test_every_signal_is_journaled():
    hub, tm, g = setup(score=50.0)
    s = sig(g)
    g.evaluate(s, "auto", 0.01)
    e = g.journal.recent[0]
    for k in ("t", "symbol", "timeframe", "signal_id", "setup_id", "direction", "htf_bias", "structure", "smc_state",
              "indicator_groups", "score", "confidence", "risk", "spread", "session", "news_state", "decision",
              "block_code", "order_result", "sl", "tp"):
        assert k in e, k
    assert e["decision"] == "BLOCKED" and e["block_code"] == "BLOCKED_SCORE"
    st = g.journal.stats()
    assert st["generated"] >= 1 and st["blocked"] >= 1


def test_autotrade_goes_through_the_gate():
    from autotrade import AutoTrader
    hub, tm, g = setup()
    at = AutoTrader(tm)
    at.running = False
    at.enabled = True
    at.settings.update(sessions=[], max_cautions=-1, news_block=False, max_open=1)
    at._session_ok = lambda: (True, "test")                         # tests also run on weekends
    hub.autotrader = at
    t = int(time.time()) // 300 * 300
    good = dict(side=1, entry=2650.0, sl=2645.0, tp=2660.0, score=80, cautions=[], t=t)
    at._consider("analyser", good)
    assert len(tm.groups) == 1, [x["text"] for x in at.log]
    e = next(iter(tm.journal.entries.values()))
    assert e.get("signal_id") and e["request_id"] == e["signal_id"]
    linked = [x for x in g.journal.recent if x.get("signal_id") == e["signal_id"] and x.get("trade_id")]
    assert linked, "signal journal has no trade_id"
    at._consider("analyser", good)                                   # the same signal again -> nothing
    assert len(tm.groups) == 1
    # without the gate there is no automated order at all
    hub.gate = None
    tm.close_everything()
    cycle(tm, 2)
    at._consider("analyser", dict(good, t=t + 300))
    assert "BLOCKED_MASTER_DISABLED" in at.log[0]["text"], at.log[0]["text"]


def test_auto_min_score_is_the_auto_threshold():
    hub, tm, g = setup(score=55.0)                                    # passing 70, score 55
    assert g.evaluate(sig(g), "auto", 0.01)["block_code"] == "BLOCKED_SCORE"
    hub.autotrader.settings["min_score"] = 50                         # AUTO "Min score %" = 50
    assert g.evaluate(sig(g, t=int(time.time()) // 300 * 300 + 300), "auto", 0.01)["allowed"]
    hub.autotrader.settings["min_score"] = 60                         # 55 < 60 -> still blocked
    r = g.evaluate(sig(g, t=int(time.time()) // 300 * 300 + 600), "auto", 0.01)
    assert r["block_code"] == "BLOCKED_SCORE"


def test_autotrade_diagnosis_lists_every_condition():
    from autotrade import AutoTrader
    hub, tm, g = setup(score=40.0, trigger=False)
    at = AutoTrader(tm)
    at.running = False
    at.enabled = True
    at._session_ok = lambda: (True, "London")
    hub.autotrader = at
    d0 = at.diagnose()                                       # default: STRATEGIES only -> no analyser score check
    assert "Strategies" in [c["name"] for c in d0["checks"]] and "Score" not in [c["name"] for c in d0["checks"]]
    at._diag = None
    at.settings["strategies_only"] = False                   # the analyser sources (old mode)
    d = at.diagnose()
    names = [c["name"] for c in d["checks"]]
    for n in ("Trading", "AUTO", "Session", "Trades today", "Open trades", "HTF bias", "Trigger", "Score"):
        assert n in names, names
    sc = next(c for c in d["checks"] if c["name"] == "Score")
    assert sc["ok"] is False and "40%" in sc["text"] and "70%" in sc["text"]
    assert not d["ready"] and "Trigger" in d["summary"] and "Score" in d["summary"]
    at._diag = None
    at.settings["min_score"] = 35
    sc = next(c for c in at.diagnose()["checks"] if c["name"] == "Score")
    assert sc["ok"] is True and "AUTO min score" in sc["text"]
    assert "diag" in at.state()


def test_auto_min_score_drives_the_analyser_arrows_it_trades():
    import types as _t
    from autotrade import AutoTrader
    hub, tm, g = setup()
    at = AutoTrader(tm)
    at.running = False
    seen = {}

    class P:
        params = {"min_score": 0.0, "x": 1}

        def signals(self, bars, tf, ctx):
            seen["min"] = self.params["min_score"]
            return []
    p = _t.SimpleNamespace(obj=P())
    hub.ctx = lambda: None
    at.settings["min_score"] = 50
    at._signals("analyser", p, [], "M5")
    assert seen["min"] == 50.0 and p.obj.params == {"min_score": 0.0, "x": 1}    # chart setting untouched


def test_auto_start_arms_and_enables_in_one_step_but_never_clears_a_halt():
    from autotrade import AutoTrader
    hub, tm, g = setup(score=40.0)
    at = AutoTrader(tm)
    at.running = False
    hub.autotrader = at
    tm.armed = False
    r = at.start()
    assert r["ok"] and at.enabled and tm.armed, r
    at.set_enabled(False)
    tm.armed = False
    tm.halted = "DAILY DD 12:00:00"
    r = at.start()
    assert not r["ok"] and r.get("halted") and not tm.armed and not at.enabled
    tm.halted = ""
    assert at.update_settings({"auto_on_start": True})["settings"]["auto_on_start"] is True


def test_auto_on_start_is_on_by_default_old_files_too():
    import json
    import autotrade as A
    assert A.DEFAULTS["auto_on_start"] is True
    with open(A.SETTINGS_FILE, "w") as f:
        json.dump(dict(auto_on_start=False, lot=0.02), f)                 # an older file (no settings_version)
    s = A.load_settings()
    assert s["auto_on_start"] is True and s["lot"] == 0.02
    with open(A.SETTINGS_FILE, "w") as f:
        json.dump(dict(auto_on_start=False, settings_version=2), f)       # switched OFF on purpose later
    assert A.load_settings()["auto_on_start"] is False
    import os
    os.remove(A.SETTINGS_FILE)


def test_strategy_trade_not_blocked_by_analyser_required_list():
    """STRATEGIES: all strategy conditions green = trade. The analyser's own required list (score, trigger, regime
    extras like BREAKOUT -> displacement) does not block it; the HTF bias (NORMAL) and hard blockers still do."""
    hub, tm, g = setup(score=50.0, trigger=False)
    hub.decision.settings["required"] = list(hub.decision.settings["required"]) + ["displacement"]
    r = g.evaluate(sig(g, src="st_trend"), "auto", 0.01)
    assert r["allowed"], (r["block_code"], r["block_reason"])
    r2 = g.evaluate(sig(g, src="analyser", t=int(time.time()) // 300 * 300 - 300), "auto", 0.01)
    assert not r2["allowed"]                                       # the analyser signal still needs them
    hub3, tm3, g3 = setup(bias=-1)
    r3 = g3.evaluate(sig(g3, side=1, src="st_trend"), "auto", 0.01)
    assert r3["block_code"] == "BLOCKED_STRUCTURE", r3["block_code"]   # against the HTF bias: still blocked
