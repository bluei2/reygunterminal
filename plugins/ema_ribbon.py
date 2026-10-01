"""EMA ribbon (4 EMAs)."""
from plugin_api import Indicator, Line, Overlay, Vote, closes, ema

COLORS = ["#22d3ee", "#facc15", "#fb923c", "#e2e8f0"]


class EMARibbon(Indicator):
    name = "EMA Ribbon"
    description = "4 EMAs · vote: first three stacked in order"
    category = "Trend"
    purpose = "Trend alignment"
    params = {"ema1": 9, "ema2": 21, "ema3": 50, "ema4": 200}

    def _lengths(self):
        return [self.params[k] for k in ("ema1", "ema2", "ema3", "ema4")]

    def compute(self, bars, tf, ctx):
        c = closes(bars)
        return Overlay(lines=[Line(ema(c, n), col, f"EMA{n}", width=1 if i < 2 else 2)
                              for i, (n, col) in enumerate(zip(self._lengths(), COLORS))])

    def vote(self, ctx):
        c = closes(self.vote_bars(ctx))
        a, b, d = (ema(c, n)[-1] for n in self._lengths()[:3])
        if None in (a, b, d):
            return None
        if a > b > d:
            return Vote(1, "stacked up")
        if a < b < d:
            return Vote(-1, "stacked down")
        return Vote(0, "ribbon mixed")
