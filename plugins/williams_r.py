"""Williams %R."""
from plugin_api import Indicator, Level, Line, Overlay, Vote, highest, highs, lowest, lows


def wr(bars, n=14):
    hh, ll = highest(highs(bars), n), lowest(lows(bars), n)
    return [None if h is None else (-50.0 if h == l else (h - b.c) / (h - l) * -100)
            for b, h, l in zip(bars, hh, ll)]


class WilliamsR(Indicator):
    name = "Williams %R"
    description = "Williams %R · vote: above / below -50"
    category = "Momentum"
    purpose = "Overbought at oversold"
    params = {"length": 14, "upper": -20.0, "lower": -80.0}

    def compute(self, bars, tf, ctx):
        p = self.params
        return Overlay(lines=[Line(wr(bars, p["length"]), "#22d3ee", f"%R {p['length']}", pane="%R")],
                       levels=[Level(p["upper"], "#f87171", "OB", pane="%R"), Level(p["lower"], "#4ade80", "OS", pane="%R")])

    def vote(self, ctx):
        v = wr(self.vote_bars(ctx), self.params["length"])
        if not v or v[-1] is None:
            return None
        return Vote(1 if v[-1] > -50 else -1, f"%R {v[-1]:.0f}")
