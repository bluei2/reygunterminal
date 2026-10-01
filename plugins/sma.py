"""Simple moving averages (3)."""
from plugin_api import Indicator, Line, Overlay, Vote, closes, sma

COLORS = ["#38bdf8", "#a78bfa", "#f8fafc"]


class SMASet(Indicator):
    name = "SMA 20/50/200"
    description = "3 simple moving averages · vote: price & fast SMA vs middle SMA"
    category = "Trend"
    purpose = "Direksyon ng trend"
    params = {"sma1": 20, "sma2": 50, "sma3": 200}

    def compute(self, bars, tf, ctx):
        c = closes(bars)
        ns = [self.params[k] for k in ("sma1", "sma2", "sma3")]
        return Overlay(lines=[Line(sma(c, n), col, f"SMA{n}") for n, col in zip(ns, COLORS)])

    def vote(self, ctx):
        c, p = closes(self.vote_bars(ctx)), self.params
        a, b = sma(c, p["sma1"])[-1], sma(c, p["sma2"])[-1]
        if a is None or b is None:
            return None
        if c[-1] > b and a > b:
            return Vote(1, f"price & SMA{p['sma1']} above SMA{p['sma2']}")
        if c[-1] < b and a < b:
            return Vote(-1, f"price & SMA{p['sma1']} below SMA{p['sma2']}")
        return Vote(0, f"mixed vs SMA{p['sma2']}")
