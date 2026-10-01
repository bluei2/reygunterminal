"""Donchian Channel: highest high / lowest low / middle."""
from plugin_api import Indicator, Line, Overlay, Vote, highest, highs, lowest, lows


class DonchianChannel(Indicator):
    name = "Donchian Channel"
    description = "N-bar high / low / mid · vote: upper or lower part of the channel"
    category = "Volatility"
    purpose = "Breakout range"
    params = {"length": 20}

    def compute(self, bars, tf, ctx):
        n = self.params["length"]
        hh, ll = highest(highs(bars), n), lowest(lows(bars), n)
        m = [(a + b) / 2 if a is not None else None for a, b in zip(hh, ll)]
        return Overlay(lines=[Line(hh, "#34d399", "DC up", width=1, style="step"),
                              Line(m, "#94a3b8", "DC mid", width=1),
                              Line(ll, "#f87171", "DC lo", width=1, style="step")])

    def vote(self, ctx):
        b, n = self.vote_bars(ctx), self.params["length"]
        hh, ll = highest(highs(b), n)[-1], lowest(lows(b), n)[-1]
        if hh is None or hh == ll:
            return None
        pos = (b[-1].c - ll) / (hh - ll)
        if pos > 0.6:
            return Vote(1, f"upper channel {pos:.0%}")
        if pos < 0.4:
            return Vote(-1, f"lower channel {pos:.0%}")
        return Vote(0, f"mid channel {pos:.0%}")
