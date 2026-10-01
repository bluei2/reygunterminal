"""
CVD - Cumulative Volume Delta (ESTIMATED).

Walang buy/sell (aggressor) data ang MT5 sa gold CFD, kaya tinatantya ang delta ng bawat candle:
    delta = tick_volume x (close - open) / (high - low)
Nagre-reset kada araw sa M1-H1 (kapag reset_daily = true), tuloy-tuloy sa H4/D1.
Gamitin para sa divergence: bagong high ng presyo pero mas mababang high ng CVD = humihina ang buyers.
"""
from plugin_api import Hist, Indicator, Level, Line, Overlay, Vote

INTRADAY = ("M1", "M5", "M15", "M30", "H1")


def cvd(bars, tf, reset_daily=True):
    n = len(bars)
    out, delta, cols = [None] * n, [None] * n, ["#64748b"] * n
    total, day = 0.0, None
    reset = reset_daily and tf in INTRADAY
    for i, b in enumerate(bars):
        d = b.t // 86400
        if reset and d != day:
            total, day = 0.0, d
            if i:
                out[i - 1] = None
        rng = b.h - b.l
        dl = (b.v or 0.0) * ((b.c - b.o) / rng if rng > 0 else 0.0)
        total += dl
        out[i], delta[i] = total, dl
        cols[i] = "#26d7a0" if dl >= 0 else "#ff5a78"
    return out, delta, cols


class CVD(Indicator):
    name = "CVD (estimated)"
    description = "Cumulative volume delta from candle body vs range · vote: CVD rising / falling"
    category = "Volume"
    purpose = "Buy vs sell pressure (tantya)"
    params = {"reset_daily": True, "vote_lookback": 10}

    def compute(self, bars, tf, ctx):
        c, delta, cols = cvd(bars, tf, self.params["reset_daily"])
        return Overlay(lines=[Line(c, "#f8fafc", "CVD", pane="CVD", width=2)],
                       hists=[Hist(delta, cols, "Delta", pane="Delta")],
                       levels=[Level(0, "#64748b", "0", pane="CVD")])

    def vote(self, ctx):
        c, _, _ = cvd(self.vote_bars(ctx), self.vote_tf, self.params["reset_daily"])
        vals = [v for v in c[-max(3, self.params["vote_lookback"]):] if v is not None]
        if len(vals) < 3:
            return None
        slope = vals[-1] - vals[0]
        return Vote(1 if slope > 0 else -1 if slope < 0 else 0, "CVD rising" if slope > 0 else "CVD falling")
