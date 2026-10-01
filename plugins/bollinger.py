"""Bollinger Bands."""
from plugin_api import Indicator, Line, Overlay, Vote, closes, sma, stdev


class BollingerBands(Indicator):
    name = "Bollinger Bands"
    description = "SMA ± standard deviations · vote: price vs middle band"
    category = "Volatility"
    purpose = "Extremes at squeeze"
    params = {"length": 20, "std_mult": 2.0}

    def compute(self, bars, tf, ctx):
        c, p = closes(bars), self.params
        m, sd = sma(c, p["length"]), stdev(c, p["length"])
        k = p["std_mult"]
        up = [a + k * b if a is not None else None for a, b in zip(m, sd)]
        lo = [a - k * b if a is not None else None for a, b in zip(m, sd)]
        return Overlay(lines=[Line(up, "#60a5fa", "BB up", width=1), Line(m, "#94a3b8", "BB mid", width=1),
                              Line(lo, "#60a5fa", "BB lo", width=1)])

    def vote(self, ctx):
        c = closes(self.vote_bars(ctx))
        m = sma(c, self.params["length"])[-1]
        if m is None:
            return None
        return Vote(1 if c[-1] > m else -1, "above BB mid" if c[-1] > m else "below BB mid")
