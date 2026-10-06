"""Step 2 (decision engine / risk engine / fingerprint) tests."""
import time

from harness import make_tm

from engine import Event, Read, TFState, Tick
from decision_engine import DecisionEngine
from risk_engine import RiskEngine
from setup_fingerprint import build


class Src:
    symbol, live, name, paused = "XAUUSD", False, "TEST", False

    def bars(self, tf):
        return []


def setup(score=78.0, bias=1, trigger=True, spread=0.3, armed=True):
    hub, tm = make_tm(0)
    hub.src = Src()
    now = int(time.time())
    hub.last_tick = Tick(now * 1000, 2650.0, 2650.0 + spread, 1.0)
    hub.last_tick_wall = time.time()
    hub._last_analysis = time.time()
    hub.read = Read(bias=bias, overall=score, passing=70.0, trigger=trigger, verdict="test", groups=[])
    st = {k: TFState(tf=k, trend=bias) for k in ("H4", "H1", "M15", "M5")}
    if trigger:
        st["M5"].events.append(Event("M5", "CHoCH", bias, 2649.0, now - 60))
    st["H1"].pd = 0.3
    hub.states = st
    hub.autotrader = type("AT", (), {"enabled": True, "count_today": 0,
                                     "settings": dict(max_spread=0.6, sessions=[], max_open=1, max_per_day=3,
                                                      no_opposite=True, news_block=True)})()
    hub.risk = RiskEngine(hub)
    de = DecisionEngine.__new__(DecisionEngine)
    DecisionEngine.__init__.__wrapped__ if False else None
    de.hub, de.settings, de.timeline, de.last, de._prev, de._ob_cache = hub, dict(
        require_htf_bias=True, require_trigger=True, require_no_blockers=True, watch_margin=10.0), [], None, {}, (None, None)
    import threading
    de.lock = threading.Lock()
    hub.decision = de
    if armed:
        tm.set_armed(True)
    return hub, tm, de


def test_decision_ready():
    hub, tm, de = setup()
    d = de.decide()
    assert d["state"] == "READY" and d["side"] == "BUY", (d["state"], [b["code"] for b in d["blockers"]])


def test_decision_blocked_by_spread():
    hub, tm, de = setup(spread=0.9)
    d = de.decide()
    assert d["state"] == "BLOCKED" and any(b["code"] == "SPREAD_TOO_HIGH" for b in d["hard_blockers"]), d["state"]
    w = de.why_not_trade()
    assert "SPREAD TOO HIGH" in w["final"], w["final"]


def test_decision_wait_low_score_and_why():
    hub, tm, de = setup(score=50.0, trigger=False)
    d = de.decide()
    assert d["state"] == "WAIT", d["state"]
    w = de.why_not_trade()
    assert "SCORE BELOW PASSING" in w["final"] and "NO TRIGGER" in w["final"], w["final"]


def test_decision_watch():
    hub, tm, de = setup(score=78.0, trigger=False)
    assert de.decide()["state"] == "WATCH"


def test_blocked_when_disarmed():
    hub, tm, de = setup(armed=False)
    d = de.decide()
    assert d["state"] == "BLOCKED" and any(b["code"] == "ARM_OFF" for b in d["hard_blockers"])


def test_risk_engine_blocks_manual_open_on_stale_tick():
    hub, tm, de = setup()
    hub.last_tick_wall = time.time() - 120                 # price feed stopped 2 min ago
    r = tm.open(1, 0.01, 5, 10)
    assert not r["ok"] and "STALE_TICK" in r["error"], r


def test_max_trades_auto():
    hub, tm, de = setup()
    hub.autotrader.count_today = 3
    codes = [b["code"] for b in hub.risk.hard_blockers(1, "auto")]
    assert "MAX_TRADES_REACHED" in codes, codes


def test_fingerprint_and_journal_fields():
    hub, tm, de = setup()
    fp = build(de.setup_matrix())
    assert fp["side"] == "BUY" and fp["trigger"] == "M5 CHOCH" and fp["pd"] == "DISCOUNT" and fp["key"], fp
    r = tm.open(1, 0.01, 5, 10)
    assert r["ok"], r
    e = tm.journal.entries[r["gid"]]
    assert e["decision_state"] == "READY" and e["setup_fingerprint"]["key"] == fp["key"] and e["session"], e.keys()
