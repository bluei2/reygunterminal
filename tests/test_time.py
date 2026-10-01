"""Time integrity: closed HTF candles only, read_at / smc_at use no future data, session boundaries (UTC)."""
from datetime import datetime, timezone

import harness  # noqa: F401

import backtest
import market_hours as MH
import smc_engine as S
from engine import TF_SECONDS

_DATA = backtest.synth_data(14)


def _hub(cut_t=None):
    data = _DATA if cut_t is None else {k: ([b for b in v if b.t < cut_t] if isinstance(v, list) else v)
                                        for k, v in _DATA.items()}
    from superchart import Hub
    return Hub(backtest.HistSource("XAUUSD", data))


def test_read_at_ignores_future_bars():
    m5 = _DATA["M5"]
    t_end = m5[-700].t + 300
    full = _hub()
    past = _hub(cut_t=t_end + 7 * 86400)          # identical history, nothing after a week later
    only = _hub(cut_t=t_end)                      # history that ENDS at t_end (no future at all)
    a, b, c = full.read_at(t_end), past.read_at(t_end), only.read_at(t_end)
    for r in (b, c):
        assert (a.bias, a.overall, a.trigger, a.verdict) == (r.bias, r.overall, r.trigger, r.verdict)


def test_htf_uses_closed_candles_only():
    h = _hub()
    h1 = _DATA["H1"]
    bar = h1[-50]
    mid = bar.t + 1800                             # halfway through this H1 candle
    import bisect
    times = [b.t for b in h1]
    k = bisect.bisect_right(times, mid - TF_SECONDS["H1"])
    assert h1[k - 1].t + 3600 <= mid and h1[k - 1].t < bar.t    # the forming H1 candle is NOT visible
    # a big change inside the forming H1 bar cannot change the read made before it closed
    r1 = h.read_at(mid)
    data2 = dict(_DATA)
    data2["H1"] = list(h1)
    data2["H1"][-50] = type(bar)(bar.t, bar.o, bar.h + 50, bar.l - 50, bar.c + 30, bar.v)
    from superchart import Hub
    h2 = Hub(backtest.HistSource("XAUUSD", data2))
    r2 = h2.read_at(mid)
    assert (r1.bias, r1.overall, r1.trigger) == (r2.bias, r2.overall, r2.trigger)


def test_smc_at_equals_truncated_history():
    h = _hub()
    m15 = _DATA["M15"]
    t_end = m15[-300].t + 900
    live_like = S.analyze({tf: [b for b in _DATA[tf] if b.t + TF_SECONDS[tf] <= t_end][-400:]
                           for tf in ("M15", "H1", "D1")}, h.smc_cfg, h.utc_offset)
    hist = h.smc_at(t_end)
    assert live_like["setups"] == hist["setups"] and live_like["dealing_range"] == hist["dealing_range"]
    assert hist["t"] + 900 <= t_end                 # last bar used had closed


def test_session_boundaries_utc():
    d = lambda h, wd=2: datetime(2026, 9, 21 + wd, h, 0, tzinfo=timezone.utc)   # 2026-09-21 is a Monday
    assert MH.session_code(d(6)) == "ASIA" and MH.session_code(d(7)) == "LONDON"
    assert MH.session_code(d(12)) == "OVERLAP" and MH.session_code(d(16)) == "NEW_YORK"
    assert MH.session_code(d(21)) == "OFF" and MH.session_code(d(10, wd=5)) == "WEEKEND"
    assert MH.in_session("London", 7) and not MH.in_session("London", 16)


def test_broker_time_is_not_utc():
    # broker server time UTC+3: a bar stamped 10:00 server time is 07:00 UTC -> London, not "10:00 London"
    t_server = int(datetime(2026, 9, 22, 10, 0, tzinfo=timezone.utc).timestamp())
    u = MH.utc_from_server(t_server, 3)
    assert u.hour == 7 and MH.session_code(u) == "LONDON"


def test_sessions_configurable_and_validated():
    old = dict(MH.SESSIONS)
    try:
        s = MH.save_sessions({"Asia": [22, 6], "London": ["x", 5]})
        assert s["Asia"] == [22, 6] and s["London"] == old["London"]          # invalid -> default kept
        assert MH.in_session("Asia", 23) and MH.in_session("Asia", 3) and not MH.in_session("Asia", 7)
    finally:
        MH.save_sessions(old)
