"""v2.8.45: forecast statistics (streaks, time to trigger / TP / SL) and the indicator snapshot of strategy forecasts."""
import sys

sys.path.insert(0, "..")
import harness  # noqa: E402,F401
import forecast as F  # noqa: E402
import strategies as ST  # noqa: E402
from test_strategies import stair_bars  # noqa: E402


def _r(t, status, dur, trig=60):
    return dict(created_at=t, trigger_time=t + trig, status=status, duration_min=dur, r=2.0 if status == "TP_HIT" else -1.0,
                confidence=50, analyser_score=60)


def test_streaks_and_times():
    rs = [_r(1, "TP_HIT", 30), _r(2, "TP_HIT", 50), _r(3, "SL_HIT", 10), _r(4, "SL_HIT", 20), _r(5, "SL_HIT", 30),
          _r(6, "TP_HIT", 40), dict(created_at=7, status="EXPIRED", confidence=50, analyser_score=60)]
    b = F._block(rs)
    assert b["max_win_streak"] == 2 and b["max_loss_streak"] == 3
    assert b["avg_min_to_trigger"] == 1.0 and b["avg_min_to_tp"] == 40.0 and b["avg_min_to_sl"] == 20.0
    assert b["win_rate"] == 50.0                                     # resolved only: 3 TP / 6 decided


def test_strategy_snapshot_has_values():
    bars = stair_bars(400, 1)
    S = ST.series(bars)
    i = len(bars) - 1
    e = ST.evaluate("trend", bars, S, i, ST.load_settings()["trend"], 1)
    snap = F.strategy_snapshot(ST, S, i, e)
    for k in ("mmi", "cmi", "adx", "rsi", "atr", "close", "route", "met", "cvd_source"):
        assert k in snap, k
    assert snap["cmi"] is not None and snap["route"] == "trend"
