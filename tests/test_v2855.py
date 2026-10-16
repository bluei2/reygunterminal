"""v2.8.55: daily report per strategy + follower copies + loss streak, and the mobile page's today / strategies."""
import time

from harness import TMP  # noqa: F401

import companion


def _hub_with_trades(n_followers=1):
    from test_strategies import _auto
    from test_gate import setup
    hub, tm, g = setup(score=40.0, bias=1, n_followers=n_followers)
    from autotrade import AutoTrader
    at = AutoTrader(tm)
    at.running = False
    hub.autotrader = at
    now = time.time()
    tm.journal.entries.clear()
    names = [tm.master.name] + [f.name for f in tm.followers]
    for k, (st, res, pnl, accts) in enumerate([("st_trend", "WIN", 21.0, names), ("st_conso", "LOSS", -19.83, names[:1]),
                                               (None, "WIN", 5.0, names)]):
        tm.journal.entries[f"T{k}"] = dict(gid=f"T{k}", status="closed", strategy=st, kind="auto" if st else "manual",
                                           result=res, pnl=pnl, r=1.0 if res == "WIN" else -1.0, side=1,
                                           open_time=now - 600 - k, close_time=now - 60 - k, accounts=accts)
    return hub, tm, at


def test_companion_today_and_strategy_status():
    hub, tm, at = _hub_with_trades()
    td = companion.today(hub)
    assert td["trades"] == 3 and td["pnl"] == 6.17
    assert td["by_strategy"]["TREND"]["pnl"] == 21.0 and td["by_strategy"]["CONSO"]["losses"] == 1
    assert td["loss_pause"] is not None and td["loss_pause"]["active"] is False
    companion._ST_CACHE.update(t=0, v=None)
    st = companion.strategy_status(hub)
    assert isinstance(st, list) and st                                     # never raises (an error row at worst)


def test_eod_report_has_strategies_copies_and_streak():
    import alerts
    hub, tm, at = _hub_with_trades(n_followers=1)
    hub.notifier = None
    al = alerts.Alerts.__new__(alerts.Alerts)                             # no background thread
    al.hub, al.day_sigs, al.last_report = hub, {}, None
    text, _png = al.build_report()
    assert "Bawat strategy" in text and "TREND: 1 tr (1W / 0L) +21.00" in text and "CONSO: 1 tr (0W / 1L) -19.83" in text
    f = tm.followers[0]
    assert f"kopya sa {f.name}: 2/3 ⚠" in text
