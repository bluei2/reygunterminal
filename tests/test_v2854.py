"""v2.8.54: optional range guard / CVD filter conditions (OFF by default) and the exit-tuning / session reports."""
import math

from harness import TMP  # noqa: F401

import strategies as ST
import trade_analytics as TA
from engine import Bar

T0 = 1_760_000_000 - 1_760_000_000 % 900


def test_range_guard_detects_back_and_forth_not_a_trend():
    chop = [Bar(T0 + k * 900, 4000 + 3 * math.sin(k / 1.5), 4001.5 + 3 * math.sin(k / 1.5),
                3998.5 + 3 * math.sin(k / 1.5), 4000 + 3 * math.sin((k + 1) / 1.5), 100) for k in range(120)]
    trend = [Bar(T0 + k * 900, 4000 + k * 2, 4001.5 + k * 2, 3999 + k * 2, 4001 + k * 2, 100) for k in range(120)]
    cfg = ST.default_settings()["trend"]
    assert ST.in_range(chop, ST.series(chop), 110, cfg)
    assert not ST.in_range(trend, ST.series(trend), 110, cfg)
    assert not ST.default_settings()["trend"]["conditions"]["no_range"]          # OFF by default
    assert not ST.default_settings()["pullback"]["conditions"]["cvd_turn"]
    met = ST._checks("trend", ST.series(chop), 110, 1, cfg, chop, 0)
    assert met["no_range"] is False


def test_exit_tuning_numbers():
    E = [dict(gid=f"g{k}", status="closed", result=res, r=r, mfe_r=mfe, mae_r=mae, sl_dist=10, tp_dist=50,
              kind="auto", strategy="st_trend", close_time=1, open_time=0)
         for k, (res, r, mfe, mae) in enumerate([("WIN", 2.0, 2.5, -0.3), ("LOSS", -1.0, 0.6, -1.0),
                                                 ("BE", 0.0, 1.1, -0.2), ("WIN", 5.0, 5.0, -0.8)])]
    t = TA.exit_tuning(E)
    assert t["trades"] == 4 and t["reach"][0.5] == 100 and t["reach"][1.0] == 75
    assert t["gave_back"][0.5] == 50                                               # 2 of the 4 closed at <= 0 R
    assert t["tp_reach"] == 25 and t["winners_mae_median"] in (0.3, 0.8) and t["lines"]
    assert TA.exit_tuning([])["trades"] == 0


def test_strategy_by_session_keys():
    E = [dict(gid="a", status="closed", strategy="st_conso", kind="strategy", session="ASIA", result="LOSS", r=-1, pnl=-20),
         dict(gid="b", status="closed", strategy="st_conso", kind="strategy", session="LONDON", result="WIN", r=2, pnl=40),
         dict(gid="c", status="closed", strategy="st_trend", kind="auto", session="ASIA", result="WIN", r=1, pnl=20)]
    keys = [r["key"] for r in TA.by_strategy_session(E)]
    assert set(keys) == {"CONSO · ASIA", "CONSO · LONDON", "TREND · ASIA"}
    assert {"strategy_sessions", "exit_tuning"} <= set(TA.all_analytics(E))
