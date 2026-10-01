"""NEWS TRADE: close open SuperChart trades before the news, BUY STOP / SELL STOP (box or price +/- offset), OCO,
MASTER / ARM safety."""
import threading
import time
import types

from harness import cycle, make_tm

import news_trader as NTM


def setup(secs_to_news=100, **settings):
    hub, tm = make_tm(1)
    hub.src.bars = lambda tf: []                                    # no bars -> no consolidation box
    hub.news = types.SimpleNamespace(settings=dict(currencies=["USD"], warn_impacts=["High"]), lock=threading.Lock(),
                                     calendar=[dict(t=time.time() + secs_to_news, title="Core PCE", currency="USD",
                                                    impact="High")])
    nt = NTM.NewsTrader(tm)
    nt.running = False
    time.sleep(0.05)
    nt.settings = NTM.clean_settings(dict(dict(place_before_sec=120, close_before_sec=180), **settings), nt.settings)
    hub.newstrader = nt
    return hub, tm, nt


def news_orders(a):
    os_ = a.backend.call("orders", None)
    return [o for o in os_ if o["comment"].startswith(NTM.TAG)] if isinstance(os_, list) else []


def test_off_until_armed_and_switched_on():
    hub, tm, nt = setup()
    assert not nt.set_enabled(True)["ok"]                           # not armed
    nt._cycle()
    assert not news_orders(tm.master)
    tm.set_armed(True)
    assert nt.set_enabled(True)["ok"] and nt.enabled


def test_closes_open_trades_then_places_both_stops_without_box():
    hub, tm, nt = setup()
    tm.set_armed(True)
    r = tm.open(1, 0.01, 5.0, 10.0)
    assert r.get("ok"), r
    cycle(tm)
    assert len(tm.groups) == 1
    nt.set_enabled(True)
    nt._cycle()
    cycle(tm)
    assert not tm.groups, "open SuperChart trade was not closed before the news"
    assert any("closed 1 open" in t for _, t in hub.notes)
    for a in (tm.master, tm.followers[0]):
        o = news_orders(a)
        assert len(o) == 2, (a.name, o)
        mid = (hub.last_tick.bid + hub.last_tick.ask) / 2
        buy = next(x for x in o if x["comment"].endswith("B0"))
        sell = next(x for x in o if x["comment"].endswith("S0"))
        assert abs(buy["price"] - (mid + 3.0)) < 0.3 and abs(sell["price"] - (mid - 3.0)) < 0.3
    st = next(iter(nt.events.values()))
    assert st["stage"] == "placed" and st["box"]["nobox"]


def test_skip_mode_master_off_and_disarm():
    hub, tm, nt = setup(nobox_mode="skip")
    tm.set_armed(True)
    nt.set_enabled(True)
    nt._cycle()
    assert not news_orders(tm.master) and next(iter(nt.events.values()))["stage"] == "skipped"
    hub2, tm2, nt2 = setup()
    tm2.set_armed(True)
    nt2.set_enabled(True)
    hub2.risk = types.SimpleNamespace(master_enabled=False)
    nt2._cycle()
    assert not news_orders(tm2.master) and "MASTER" in next(iter(nt2.events.values()))["note"]
    hub3, tm3, nt3 = setup()
    tm3.set_armed(True)
    nt3.set_enabled(True)
    nt3._cycle()
    assert len(news_orders(tm3.master)) == 2
    tm3.set_armed(False)
    nt3._cycle()
    assert not nt3.enabled and not news_orders(tm3.master)          # DISARM: off + every news pending cancelled


def test_too_early_does_nothing_and_close_can_be_off():
    hub, tm, nt = setup(secs_to_news=600)
    tm.set_armed(True)
    tm.open(1, 0.01, 5.0, 10.0)
    cycle(tm)
    nt.set_enabled(True)
    nt._cycle()
    cycle(tm)
    assert len(tm.groups) == 1 and not news_orders(tm.master)       # 10 min before: nothing yet
    hub2, tm2, nt2 = setup(close_before_news="false")
    tm2.set_armed(True)
    tm2.open(-1, 0.01, 5.0, 10.0)
    cycle(tm2)
    nt2.set_enabled(True)
    nt2._cycle()
    cycle(tm2)
    assert len(tm2.groups) == 1 and len(news_orders(tm2.master)) == 2
