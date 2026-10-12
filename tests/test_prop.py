"""v2.8.30 PROP FIRM follower: no news trades / news window, no weekend positions, no x2; the master keeps trading."""
import calendar
import sys
import threading
import time
import types

sys.path.insert(0, "..")
from harness import make_tm, cycle   # noqa: E402


def _prop(tm, **kw):
    f = tm.followers[0]
    f.cfg.update(dict(prop=True, prop_no_news=True, prop_news_pad=5, prop_close_news=True, prop_no_weekend=True,
                      prop_fri_close=20.0, prop_no_x2=True), **kw)
    return f


def _news(hub, t):
    hub.news = types.SimpleNamespace(settings=dict(currencies=["USD"], warn_impacts=["High"]), lock=threading.Lock(),
                                     calendar=[dict(t=t, title="CPI m/m", currency="USD", impact="High")])


def _ts(y, mo, d, h, mi=0):
    return calendar.timegm((y, mo, d, h, mi, 0))


def test_weekend_window():
    hub, tm = make_tm(1)
    f = _prop(tm)
    fri = _ts(2026, 10, 9, 20, 30)          # Friday 20:30 UTC
    assert tm.prop_state(f, _ts(2026, 10, 9, 19, 0)) == (None, None)
    b, c = tm.prop_state(f, fri)
    assert b and c and "weekend" in b
    assert tm.prop_state(f, _ts(2026, 10, 10, 12))[0]                      # Saturday
    assert tm.prop_state(f, _ts(2026, 10, 11, 22))[0]                      # Sunday 22:00
    assert tm.prop_state(f, _ts(2026, 10, 11, 23, 30)) == (None, None)     # Sunday 23:30: open again
    f.cfg["prop_no_weekend"] = False
    assert tm.prop_state(f, fri) == (None, None)


def test_news_window_blocks_and_closes_prop_only():
    hub, tm = make_tm(1)
    tm.set_armed(True)
    f = _prop(tm, prop_no_weekend=False)
    r = tm.open(1, 0.01, 5.0, 10.0)
    assert r["ok"] and "f1" in tm.groups[r["gid"]].legs
    _news(hub, time.time() + 120)                                          # news in 2 min (inside ±5)
    assert "news window" in tm.follower_skip_reason(f)
    tm.prop_guard()
    cycle(tm, 1)
    g = tm.groups[r["gid"]]
    assert "f1" not in g.legs and "master" in g.legs                       # PROP leg closed, the master keeps it
    r2 = tm.open(1, 0.01, 5.0, 10.0)
    assert r2["ok"] and "f1" not in tm.groups[r2["gid"]].legs              # no new PROP trade in the window
    tm.groups[r2["gid"]].created -= 30
    tm.copy_audit()
    _news(hub, time.time() + 3600)                                         # news in 1 h: outside the window
    assert tm.follower_skip_reason(f) is None


def test_news_trades_never_go_to_prop_and_no_x2():
    hub, tm = make_tm(1)
    tm.set_armed(True)
    _prop(tm, prop_no_weekend=False)
    tm.followers[0].cfg["lot"] = 0.02
    r = tm.open(1, 0.01, 5.0, 10.0, kind="news")
    assert r["ok"] and "f1" not in tm.groups[r["gid"]].legs
    r2 = tm.open(1, 0.02, 5.0, 10.0, kind="auto", meta=dict(lot_mult=2.0))
    leg = tm.groups[r2["gid"]].legs["f1"]
    cycle(tm, 1)
    assert tm.followers[0].pos(leg)["volume"] == 0.02                      # PROP: no x2
