"""Stochastic oscillator."""
from plugin_api import Indicator, Level, Line, Overlay, Vote, highest, highs, lowest, lows, sma


class Stochastic(Indicator):
    name = "Stochastic"
    description = "%K / %D with overbought / oversold · vote: %K above / below %D"
    category = "Momentum"
    purpose = "Overbought at oversold"
    params = {"k_length": 14, "k_smooth": 3, "d_length": 3, "overbought": 80.0, "oversold": 20.0}

    def _kd(self, bars):
        p = self.params
        hh, ll = highest(highs(bars), p["k_length"]), lowest(lows(bars), p["k_length"])
        raw = [None if h is None else (50.0 if h == l else (b.c - l) / (h - l) * 100)
               for b, h, l in zip(bars, hh, ll)]
        k = sma(raw, p["k_smooth"])
        return k, sma(k, p["d_length"])

    def compute(self, bars, tf, ctx):
        k, d = self._kd(bars)
        p = self.params
        return Overlay(lines=[Line(k, "#38bdf8", "%K", pane="Stoch", width=1),
                              Line(d, "#fb923c", "%D", pane="Stoch", width=1)],
                       levels=[Level(p["overbought"], "#f87171", "OB", pane="Stoch"),
                               Level(p["oversold"], "#4ade80", "OS", pane="Stoch")])

    def vote(self, ctx):
        k, d = self._kd(self.vote_bars(ctx))
        if not k or k[-1] is None or d[-1] is None:
            return None
        return Vote(1 if k[-1] > d[-1] else -1, f"%K {k[-1]:.0f} {'>' if k[-1] > d[-1] else '<'} %D")
