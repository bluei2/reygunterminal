"""Keltner Channel (EMA ± ATR)."""
from plugin_api import Indicator, Line, Overlay, Vote, atr, closes, ema


class KeltnerChannel(Indicator):
    name = "Keltner Channel"
    description = "EMA ± ATR multiple · vote: price vs middle"
    category = "Volatility"
    purpose = "Channel at extremes"
    params = {"length": 20, "atr_length": 10, "multiplier": 2.0}

    def compute(self, bars, tf, ctx):
        p = self.params
        m, a = ema(closes(bars), p["length"]), atr(bars, p["atr_length"])
        k = p["multiplier"]
        up = [x + k * y if x is not None and y is not None else None for x, y in zip(m, a)]
        lo = [x - k * y if x is not None and y is not None else None for x, y in zip(m, a)]
        return Overlay(lines=[Line(up, "#c084fc", "KC up", width=1), Line(m, "#a1a1aa", "KC mid", width=1),
                              Line(lo, "#c084fc", "KC lo", width=1)])

    def vote(self, ctx):
        c = closes(self.vote_bars(ctx))
        m = ema(c, self.params["length"])[-1]
        if m is None:
            return None
        return Vote(1 if c[-1] > m else -1, "above KC mid" if c[-1] > m else "below KC mid")
