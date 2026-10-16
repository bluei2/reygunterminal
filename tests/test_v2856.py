"""v2.8.56: CONSO stays out of the news window (no new grid, the unfilled grid is cancelled) and NEWS TRADE places
its pendings 1 min before the release."""
import json
import threading
import time
import types

from harness import TMP  # noqa: F401

import strategies as ST
from test_strategies import _auto


def _news(events):
    return types.SimpleNamespace(calendar=events, lock=threading.Lock(),
                                 settings=dict(currencies=["USD"], warn_impacts=["High"], warn_minutes=30, after_minutes=10))


def test_conso_news_window_high_medium_and_switches():
    hub, tm, g, at = _auto(strategies=("conso",))
    now = time.time()
    hub.news = _news([dict(currency="USD", impact="Medium", title="Retail Sales", t=now + 90)])
    assert at.conso_news(now)["title"] == "Retail Sales"                    # Medium counts for CONSO
    at.settings["conso_news_medium"] = False
    assert at.conso_news(now) is None                                       # High only
    hub.news = _news([dict(currency="USD", impact="High", title="CPI", t=now - 9 * 60),
                      dict(currency="EUR", impact="High", title="ECB", t=now + 60),
                      dict(currency="USD", impact="Low", title="x", t=now + 60)])
    assert at.conso_news(now)["title"] == "CPI"                             # 9 min after: still inside
    assert at.conso_news(now + 2 * 60) is None                              # 11 min after: over
    assert at.conso_news(now - 40 * 60) is None                             # 31 min before: not yet
    at.settings["conso_news_guard"] = False
    assert at.conso_news(now) is None
    hub.news = None
    at.settings["conso_news_guard"] = True
    assert at.conso_news(now) is None                                       # no calendar: nothing to do here
    ST.save_settings(ST.default_settings())


def test_conso_grid_cancelled_and_not_placed_inside_the_news_window():
    import news_trader as NTM
    hub, tm, g, at = _auto(score=40.0, bias=1, strategies=("conso",))
    nt = NTM.NewsTrader(tm)
    nt.running = False
    hub.newstrader = nt
    hub.news = _news([])
    plan = dict(ok=True, box_high=2652.0, box_low=2648.0, buy_stop=2652.5, sell_stop=2647.5, sl_dist=5.0, tp_dist=10.0,
                buy_sl=2647.5, buy_tp=2662.5, sell_sl=2652.5, sell_tp=2637.5, box_from=0, levels=1, step=1.5, side=0)
    t = int(time.time()) // 900 * 900
    spell = dict(side=0, pending=True, entry=2652.5, sl=2647.5, tp=2662.5, score=100.0, cautions=[], t=t, i=200,
                 strategy="conso", kind="CONSO BOX")
    at._signals = lambda *a, **k: [dict(spell)]
    old_eval, old_series = ST.evaluate, ST.series
    ST.evaluate = lambda *a, **k: dict(plan)
    ST.series = lambda *a, **k: None
    bars = [type("B", (), dict(t=t - (300 - k) * 900))() for k in range(300)]

    def tags():
        return [o for o in tm.master.backend.call("orders", None) if o["comment"].startswith(NTM.TAG)]
    try:
        at._strategies(bars, "M15")
        assert len(tags()) == 2, [x["text"] for x in at.log][:4]                   # no news: the grid is placed
        assert at._conso_news_cancel() == 0 and len(tags()) == 2                  # still no news: kept
        hub.news.calendar.append(dict(currency="USD", impact="High", title="CPI", t=time.time() + 90))
        assert at._conso_news_cancel() == 2 and not tags()                        # 1.5 min before CPI: both gone
        assert any("binura bago ang news" in x["text"] for x in at.log)
        at._st_tries = {}
        at._strategies(bars, "M15")
        assert not tags(), [x["text"] for x in at.log][:4]                        # and no new grid in the window
        assert any("news window" in x["text"] for x in at.log)
        hub.news.calendar[:] = []                                                 # the window is over
        at._st_tries = {}
        at._strategies(bars, "M15")
        assert len(tags()) == 2, [x["text"] for x in at.log][:6]
    finally:
        ST.evaluate, ST.series = old_eval, old_series
        ST.save_settings(ST.default_settings())


def test_news_trade_places_one_minute_before_once():
    import news_trader as NTM
    assert NTM.DEFAULTS["place_before_sec"] == 60
    with open(NTM.SETTINGS_FILE, "w") as f:
        json.dump(dict(place_before_sec=120, settings_version=3), f)
    assert NTM.load_settings()["place_before_sec"] == 60                          # old 2 min -> 1 min, once
    with open(NTM.SETTINGS_FILE, "w") as f:
        json.dump(dict(place_before_sec=90, settings_version=4), f)
    assert NTM.load_settings()["place_before_sec"] == 90                          # a later choice is kept
    import os
    os.remove(NTM.SETTINGS_FILE)


def test_news_trade_buffer_ten_pips():
    import news_trader as NTM
    assert NTM.DEFAULTS["buffer_pips"] == 10.0 and NTM.DEFAULTS["pip"] == 0.10
    with open(NTM.SETTINGS_FILE, "w") as f:
        json.dump(dict(buffer_pips=5.0, settings_version=4), f)
    s = NTM.load_settings()
    assert s["buffer_pips"] == 10.0                                                # old 5 pips -> 10, once
    with open(NTM.SETTINGS_FILE, "w") as f:
        json.dump(dict(buffer_pips=8.0, settings_version=5), f)
    assert NTM.load_settings()["buffer_pips"] == 8.0
    import os
    os.remove(NTM.SETTINGS_FILE)
    nt = NTM.NewsTrader.__new__(NTM.NewsTrader)
    nt.settings = NTM.load_settings()
    lv = nt._levels(dict(ok=True, high=4153.26, low=4149.26, range=4.0))
    buy = [x for x in lv if x["side"] == 1][0]
    sell = [x for x in lv if x["side"] == -1][0]
    assert buy["entry"] == 4154.26 and sell["entry"] == 4148.26            # $1.00 beyond each box edge
