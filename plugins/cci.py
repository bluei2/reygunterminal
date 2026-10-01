"""Commodity Channel Index."""
from plugin_api import Indicator, Level, Line, Overlay, Vote, hlc3, sma


def cci(bars, n=20):
    tp = hlc3(bars)
    m = sma(tp, n)
    out = [None] * len(bars)
    for i in range(n - 1, len(bars)):
        md = sum(abs(x - m[i]) for x in tp[i - n + 1: i + 1]) / n
        out[i] = 0.0 if md == 0 else (tp[i] - m[i]) / (0.015 * md)
    return out


class CCI(Indicator):
    name = "CCI"
    description = "Commodity Channel Index · vote: above / below zero"
    category = "Momentum"
    purpose = "Momentum at extremes"
    params = {"length": 20, "level": 100.0}

    def compute(self, bars, tf, ctx):
        p = self.params
        return Overlay(lines=[Line(cci(bars, p["length"]), "#facc15", f"CCI {p['length']}", pane="CCI")],
                       levels=[Level(p["level"], "#f87171", "+", pane="CCI"), Level(0, "#64748b", "0", pane="CCI"),
                               Level(-p["level"], "#4ade80", "-", pane="CCI")])

    def vote(self, ctx):
        v = cci(self.vote_bars(ctx), self.params["length"])
        if not v or v[-1] is None:
            return None
        return Vote(1 if v[-1] > 0 else -1, f"CCI {v[-1]:.0f}")
