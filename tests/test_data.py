"""Data validation, categorized error log (secrets redacted), news states, backtest integrity report."""
import os
import tempfile
import time
import types

import harness  # noqa: F401

import backtest
import data_validation as DV
from engine import Bar, Tick
from error_log import ErrorLog, categorize, redact

T0 = 1_700_000_000 - 1_700_000_000 % 300


def good_bars(n=60, sec=300):
    return [Bar(T0 + i * sec, 100 + i * 0.1, 100.5 + i * 0.1, 99.5 + i * 0.1, 100.2 + i * 0.1) for i in range(n)]


def test_valid_bars_pass():
    assert DV.validate_bars("M5", good_bars(), T0 + 59 * 300 + 10) == []


def test_invalid_ohlc_time_order_future():
    b = good_bars()
    b[-5] = Bar(b[-5].t, 100, 99, 101, 100)                       # high < low
    assert DV.validate_bars("M5", b)[0]["code"] == "BAR_INVALID"
    b = good_bars()
    b[-3], b[-4] = b[-4], b[-3]
    assert DV.validate_bars("M5", b)[0]["code"] == "BAR_TIME_ORDER"
    b = good_bars()
    assert any(i["code"] == "BAR_FROM_FUTURE" for i in DV.validate_bars("M5", b, b[-1].t - 3600))
    assert DV.validate_bars("M5", good_bars(5))[0]["code"] == "BARS_MISSING"
    nan = good_bars()
    nan[-1] = Bar(nan[-1].t, float("nan"), 1, 1, 1)
    assert DV.validate_bars("M5", nan)[0]["code"] == "BAR_INVALID"


def test_tick_and_spread():
    assert DV.validate_tick(None)[0]["code"] == "NO_TICK"
    assert DV.validate_tick(Tick(1, 0.0, 1.0))[0]["code"] == "TICK_INVALID"
    assert DV.validate_tick(Tick(1, 2650.5, 2650.0))[0]["code"] == "TICK_INVALID"
    assert DV.validate_tick(Tick(1, 2650.0, 2660.0), [0.3] * 50)[0]["code"] == "SPREAD_INVALID"
    assert DV.validate_tick(Tick(1, 2650.0, 2650.3), [0.3] * 50) == []


def test_symbol_mismatch():
    src = types.SimpleNamespace(symbol="XAUUSD", bars=lambda tf: good_bars(60, 300))
    r = DV.validate(src, Tick((T0 + 59 * 300) * 1000, 100, 100.2), "XAUUSD_ph", tfs=("M5",))
    assert not r["ok"] and r["issues"][0]["code"] == "SYMBOL_MISMATCH"


def test_error_log_categories_and_redaction():
    assert "123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA" not in redact("sending with 123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")
    assert "hunter2" not in redact("login failed password=hunter2 user=1")
    assert "SECRETTOKEN" not in redact("https://api.telegram.org/botSECRETTOKEN/sendMessage")
    assert categorize("plugin 'x' error") == "PLUGIN_ERROR" and categorize("master order failed: 10030") == "EXECUTION_ERROR"
    path = os.path.join(tempfile.mkdtemp(), "errors.jsonl")
    log = ErrorLog(path)
    e = log.add("BROKER_ERROR", "mt5", "10030", "invalid fill", dict(token="abc123secretvalue"))
    assert e["category"] == "BROKER_ERROR" and "abc123secretvalue" not in open(path).read()
    for k in ("t", "component", "code", "message", "context"):
        assert k in e
    assert log.view()["counts"]["BROKER_ERROR"] == 1


def test_hub_errors_go_to_error_log():
    from error_log import ERRORS
    from superchart import Hub
    h = Hub(backtest.HistSource("XAUUSD", {}))
    n = len(ERRORS.items)
    h.note("error", "journal: disk full", code="X")
    assert len(ERRORS.items) == n + 1 and ERRORS.items[0]["category"] == "STATE_ERROR"


def test_news_states():
    import news
    nf = news.NewsFeed.__new__(news.NewsFeed)
    nf.settings = dict(news.DEFAULTS) if hasattr(news, "DEFAULTS") else news.load_settings()
    import threading
    nf.lock = threading.Lock()
    nf.calendar, nf.errors, nf.updated = [], {"calendar": "URLError: offline"}, {"calendar": 0, "rss": 0}
    assert nf.news_state()["state"] == "NEWS_UNKNOWN"               # never loaded -> NOT clear
    now = time.time()
    nf.updated["calendar"] = now - 60
    assert nf.news_state(now)["state"] == "NEWS_CLEAR"
    cur = nf.settings["currencies"][0]
    imp = nf.settings["warn_impacts"][0]
    nf.calendar = [dict(t=now + 300, currency=cur, impact=imp, title="CPI")]
    assert nf.news_state(now)["state"] == "NEWS_HIGH_IMPACT"
    nf.updated["calendar"] = now - 20 * 3600
    assert nf.news_state(now)["state"] == "NEWS_UNKNOWN"            # stale calendar -> unknown


def test_backtest_signals_generated_blocked_executed():
    d = backtest.synth_data(10)
    P = backtest.Prepared(None, d, "XAUUSD", "M5", 8)
    cnt = {}
    tr = backtest.simulate(P, dict(backtest.DEFAULT_SETUP, sessions=["London"], smc_filter="setup"), counters=cnt)
    assert cnt["generated"] == cnt["executed"] + sum(cnt["blocked"].values()), cnt
    assert cnt["executed"] == len(tr) and cnt["blocked"], cnt
    st = backtest.stats(tr)
    st.update(backtest.integrity(tr, 2.0))
    for k in ("trades", "win_rate", "loss_rate", "pf", "expectancy_r", "max_dd_r", "avg_rr", "avg_win_r", "avg_loss_r",
              "max_consec_losses", "worst_day_r", "daily_target_hit_rate"):
        assert k in st, k
    if st["trades"]:
        assert abs(st["win_rate"] + st["loss_rate"] - 100) < 0.2
