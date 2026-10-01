"""MACD: MACD line, signal line, histogram."""
from plugin_api import Hist, Indicator, Level, Line, Overlay, Vote, closes, ema


def macd(c, fast=12, slow=26, sig=9):
    f, s = ema(c, fast), ema(c, slow)
    m = [a - b if a is not None and b is not None else None for a, b in zip(f, s)]
    sg = ema(m, sig)
    h = [a - b if a is not None and b is not None else None for a, b in zip(m, sg)]
    return m, sg, h


class MACD(Indicator):
    name = "MACD"
    description = "MACD, signal and histogram · vote: MACD above / below signal"
    category = "Momentum"
    purpose = "Momentum shift"
    params = {"fast": 12, "slow": 26, "signal": 9}

    def _m(self, bars):
        p = self.params
        return macd(closes(bars), p["fast"], p["slow"], p["signal"])

    def compute(self, bars, tf, ctx):
        m, sig, hist = self._m(bars)
        cols = []
        for i, h in enumerate(hist):
            prev = hist[i - 1] if i else None
            if h is None:
                cols.append("#64748b")
            elif h >= 0:
                cols.append("#26d7a0" if prev is None or h >= prev else "#1f7a61")
            else:
                cols.append("#ff5a78" if prev is None or h <= prev else "#8c3446")
        return Overlay(hists=[Hist(hist, cols, "Hist", pane="MACD")],
                       lines=[Line(m, "#38bdf8", "MACD", pane="MACD", width=1),
                              Line(sig, "#fb923c", "Signal", pane="MACD", width=1)],
                       levels=[Level(0, "#64748b", "0", pane="MACD")])

    def vote(self, ctx):
        m, sig, _ = self._m(self.vote_bars(ctx))
        if not m or m[-1] is None or sig[-1] is None:
            return None
        return Vote(1 if m[-1] > sig[-1] else -1, "MACD > signal" if m[-1] > sig[-1] else "MACD < signal")
