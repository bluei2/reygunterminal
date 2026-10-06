"""v2.8.9 NEWS RESULTS: forecast vs actual, gold reaction, news trade outcome."""
import os
import tempfile
import threading
import time
import types

import news_results as NR


def _hub(t_release, price):
    tick = types.SimpleNamespace(bid=price - 0.1, ask=price + 0.1)
    news = types.SimpleNamespace(settings=dict(currencies=["USD"], warn_impacts=["High"]), lock=threading.Lock(),
                                 calendar=[dict(t=t_release, title="CPI m/m", currency="USD", impact="High",
                                                forecast="0.3%", previous="0.4%", actual="")])
    j = types.SimpleNamespace(entries={})
    return types.SimpleNamespace(news=news, last_tick=tick, trader=types.SimpleNamespace(journal=j), note=lambda *a, **k: None)


def test_num_and_compare():
    assert NR.num("0.3%") == 0.3 and NR.num("187K") == 187000 and NR.num("-1.2B") == -1.2e9 and NR.num("") is None
    assert NR.compare("0.3%", "0.3%")["verdict"] == "TUGMA"
    assert NR.compare("0.3%", "0.5%")["verdict"] == "MAS MATAAS"
    assert NR.compare("187K", "150K")["verdict"] == "MAS MABABA"
    assert NR.compare("0.3%", "") is None


def test_records_release_reaction_actual_and_trades():
    t0 = 1_800_000_000
    hub = _hub(t0, 4000.0)
    path = os.path.join(tempfile.mkdtemp(), "nr.json")
    r = NR.NewsResults(hub, path=path, start=False)
    r.update(now=t0 - 600)
    assert not r.events                                             # not released yet
    r.update(now=t0 + 30)
    e = next(iter(r.events.values()))
    assert e["px0"] == 4000.0
    hub.last_tick = types.SimpleNamespace(bid=3991.9, ask=3992.1)
    r.update(now=t0 + 5 * 60 + 10)
    hub.news.calendar[0]["actual"] = "0.5%"
    hub.trader.journal.entries["N1"] = dict(gid="N1", kind="news", open_time=t0 + 20, side=-1, result="WIN", pnl=12.5)
    hub.trader.journal.entries["X"] = dict(gid="X", kind="auto", open_time=t0 + 20, side=1, result="LOSS", pnl=-5)
    hub.last_tick = types.SimpleNamespace(bid=3985.9, ask=3986.1)
    r.update(now=t0 + 10 * 60 + 5)
    v = r.view()
    row = v["rows"][0]
    assert row["actual"] == "0.5%" and row["cmp"]["verdict"] == "MAS MATAAS"
    assert row["marks"]["5"] == 3992.0 and row["move10"] == -14.0 and row["range10"] == 14.0
    assert [t["gid"] for t in row["trades"]] == ["N1"]
    s = v["summary"]
    assert s["events"] == 1 and s["forecast_acc"] == 0 and s["win_rate"] == 100 and s["pnl"] == 12.5
    r2 = NR.NewsResults(hub, path=path, start=False)                # saved and loaded back
    assert len(r2.events) == 1


def test_event_released_before_the_app_started_is_not_recorded():
    t0 = 1_800_000_000
    hub = _hub(t0, 4000.0)
    r = NR.NewsResults(hub, path=os.path.join(tempfile.mkdtemp(), "nr.json"), start=False)
    r.update(now=t0 + 600)
    assert not r.events
