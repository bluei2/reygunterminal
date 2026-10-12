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


def _fill_buy_on_master(tm, nt):
    """Simulate: the master's news BUY STOP is hit (pending gone, a position with the same comment)."""
    b = tm.master.backend
    o = next(x for x in news_orders(tm.master) if x["comment"].endswith("B0"))
    b.call("cancel_order", o["ticket"])
    r = b.c_open_market(tm.master.symbol, 1, 0.01, 5.0, 10.0, o["comment"])
    b.positions_[r["ticket"]]["magic"] = __import__("mt5_ops").MAGIC
    time.sleep(0.01)
    tm.refresh()
    nt._cycle()
    return r["ticket"]


def test_v287_news_fill_follow_trend_and_time_exit():
    hub, tm, nt = setup(follow_trend="true", time_exit_min=10, close_before_news="false")
    tm.set_armed(True)
    nt.set_enabled(True)
    nt._cycle()
    assert len(news_orders(tm.master)) == 2
    _fill_buy_on_master(tm, nt)
    src = sorted(getattr(g, "source", "") for g in tm.groups.values())
    assert src == ["news", "news_trend"], (src, [t for _, t in hub.notes][-5:])
    trend = next(g for g in tm.groups.values() if g.source == "news_trend")
    assert trend.side == 1 and "f1" in trend.legs                     # followers get the TREND too
    nt._cycle()
    assert len([g for g in tm.groups.values() if g.source == "news_trend"]) == 1   # only once
    for g in tm.groups.values():                                     # 10 minutes later
        g.created -= 601
    nt._cycle()
    cycle(tm, 3)
    assert not [g for g in tm.groups.values() if g.source in ("news", "news_trend")], [t for _, t in hub.notes][-4:]
    assert any("10 min na" in t for _, t in hub.notes)


def test_v287_follow_trend_can_be_off():
    hub, tm, nt = setup(close_before_news="false", follow_trend="false")
    tm.set_armed(True)
    nt.set_enabled(True)
    nt._cycle()
    _fill_buy_on_master(tm, nt)
    assert not [g for g in tm.groups.values() if g.source == "news_trend"]
    assert nt.settings["time_exit_min"] == 0                          # v2.8.36: no time exit (trailing)


def test_v287_conso_grid_has_no_expiry():
    hub, tm, nt = setup()
    tm.set_armed(True)
    plan = dict(box_high=2652.0, box_low=2648.0, buy_stop=2652.5, sell_stop=2647.5, sl_dist=5.0, tp_dist=10.0, levels=1)
    r = nt.place_box(plan, 0.01, 0, label="CONSO M15")
    assert r["ok"], r
    st = next(s for s in nt.events.values() if s.get("kind") == "strategy")
    st["t"] -= 10 * 3600                                             # hours later: still there
    tm.refresh(); nt._cycle()
    assert st["stage"] == "placed" and len([o for o in news_orders(tm.master) if "B" in o["comment"]]) >= 1



def test_v288_follow_trend_on_by_default_and_ordinary_conso_grid_cancelled_before_news():
    hub, tm, nt = setup(secs_to_news=100)
    assert nt.settings["follow_trend"] is True
    tm.set_armed(True)
    plan = dict(box_high=2652.0, box_low=2648.0, buy_stop=2652.5, sell_stop=2647.5, sl_dist=5.0, tp_dist=10.0, levels=1)
    assert nt.place_box(plan, 0.01, 0, label="CONSO M15")["ok"]
    grid = next(s for s in nt.events.values() if s.get("kind") == "strategy")
    ordinary = [o for o in news_orders(tm.master) if grid["eid"] in o["comment"]]
    assert len(ordinary) == 2
    nt.set_enabled(True)
    nt._cycle()                                                     # 100 s before the news
    assert not [o for o in news_orders(tm.master) if grid["eid"] in o["comment"]], "ordinary CONSO grid still there"
    assert grid["stage"] == "done"
    news = [o for o in news_orders(tm.master) if grid["eid"] not in o["comment"]]
    assert len(news) == 2                                           # the NEWS conso is placed
    assert any("ordinary CONSO" in t for _, t in hub.notes)


def test_v2810_follower_enters_at_market_when_the_master_fills():
    """Master BUY STOP filled, follower's own BUY STOP not (its broker price did not reach it) -> follower MARKET BUY,
    its pending orders of that grid cancelled (no double entry)."""
    hub, tm, nt = setup(close_before_news="false", follow_trend="false")
    tm.set_armed(True)
    nt.set_enabled(True)
    nt._cycle()
    f = tm.followers[0]
    assert len(news_orders(f)) == 2
    _fill_buy_on_master(tm, nt)
    g = next(g for g in tm.groups.values() if g.source == "news")
    assert "f1" in g.legs, [t for _, t in hub.notes][-5:]
    fp = f.backend.positions_[g.legs["f1"]]
    assert fp["side"] == 1 and abs((fp["open"] - fp["sl"]) - (g.m_open - g.m_sl if hasattr(g, "m_open") else fp["open"] - fp["sl"])) < 0.5
    assert not news_orders(f), news_orders(f)                        # BUY STOP replaced + SELL STOP (OCO) cancelled
    nt._cycle()
    assert len(f.backend.positions_) == 1                            # never twice


def test_v2810_copy_check_reports_a_missing_follower():
    from harness import make_tm
    hub, tm = make_tm(1)
    tm.set_armed(True)
    tm.followers[0].paused = "test"
    r = tm.open(1, 0.01, 5.0, 10.0)
    assert r.get("ok"), r
    g = tm.groups[r["gid"]]
    assert "f1" not in g.legs
    g.created -= 30
    tm.copy_audit(); tm.copy_audit()
    msgs = [t for _, t in hub.notes if "COPY CHECK" in t]
    assert len(msgs) == 1 and "PAUSED" in msgs[0], msgs


def test_v2813_no_half_grid_when_the_price_already_left_the_box():
    import types
    hub, tm, nt = setup()
    tm.set_armed(True)
    mid = (hub.last_tick.bid + hub.last_tick.ask) / 2
    plan = dict(box_high=mid + 6.0, box_low=mid + 2.0, buy_stop=mid + 6.5, sell_stop=mid + 1.5, sl_dist=5.0, tp_dist=10.0,
                levels=2, step=1.5)                                   # the price is already BELOW the box
    r = nt.place_box(plan, 0.01, 0, label="CONSO M15")
    assert not r["ok"] and "nabasag na pababa" in r["error"], r
    assert not news_orders(tm.master)
    plan = dict(box_high=mid + 2.0, box_low=mid - 2.0, buy_stop=mid + 2.5, sell_stop=mid - 2.5, sl_dist=5.0, tp_dist=10.0,
                levels=2, step=1.5)                                   # price inside the box: full grid
    r = nt.place_box(plan, 0.01, 0, label="CONSO M15 b")
    assert r["ok"], r
    assert len(news_orders(tm.master)) == 4


def test_v2835_news_rr5_and_trailed():
    import json
    import os
    import news_trader as NTM
    import position_manager as PM
    assert NTM.DEFAULTS["tp_rr"] == 5.0
    old = NTM.SETTINGS_FILE
    NTM.SETTINGS_FILE = "_nt_test.json"
    try:
        with open(NTM.SETTINGS_FILE, "w") as f:
            json.dump(dict(tp_rr=2.0, follow_trend=True), f)
        assert NTM.load_settings()["tp_rr"] == 5.0                      # once
        assert NTM.load_settings()["time_exit_min"] == 0                # v2.8.36: no 10-min exit
        with open(NTM.SETTINGS_FILE, "w") as f:
            json.dump(dict(tp_rr=3.0, time_exit_min=15, settings_version=3), f)
        st = NTM.load_settings()
        assert st["tp_rr"] == 3.0 and st["time_exit_min"] == 15          # your later choices stay
    finally:
        if os.path.exists(NTM.SETTINGS_FILE):
            os.remove(NTM.SETTINGS_FILE)
        NTM.SETTINGS_FILE = old
    hub, tm, nt = setup(follow_trend="false", time_exit_min=0, close_before_news="false")
    tm.set_armed(True)
    nt.set_enabled(True)
    nt._cycle()
    _fill_buy_on_master(tm, nt)
    g = next(x for x in tm.groups.values() if x.source == "news")
    pc = PM.PositionController(tm)
    pc.running = False
    pc.settings = PM.clean(None)                                        # the default 0.5R ladder
    hub.set_price(g.m_open + 1.1 * g.sl_dist)                           # +1R and a bit
    cycle(tm); pc.step(); cycle(tm)
    mp = tm.master.pos(g.legs["master"])
    assert mp["sl"] > mp["open"], mp                                    # the news trade is trailed too
