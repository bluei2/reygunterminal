"""
Money Flow Index with overbought / oversold levels.
MT5 gold/forex: gumagamit ng TICK volume (walang totoong exchange volume sa CFD).
"""
from plugin_api import Indicator, Level, Line, Overlay, Vote, hlc3


def mfi(bars, n=14):
    tp = hlc3(bars)
    out = [None] * len(bars)
    for i in range(n, len(bars)):
        pos = neg = 0.0
        for j in range(i - n + 1, i + 1):
            flow = tp[j] * (bars[j].v or 1.0)
            if tp[j] > tp[j - 1]:
                pos += flow
            elif tp[j] < tp[j - 1]:
                neg += flow
        out[i] = 100.0 if neg == 0 else 100 - 100 / (1 + pos / neg)
    return out


class MFI(Indicator):
    name = "MFI"
    description = "Money Flow Index · tick volume · vote: above bull level / below bear level"
    category = "Volume"
    purpose = "Volume-weighted momentum"
    params = {"length": 14, "overbought": 80.0, "oversold": 20.0, "bull_above": 55.0, "bear_below": 45.0}

    def compute(self, bars, tf, ctx):
        p = self.params
        return Overlay(lines=[Line(mfi(bars, p["length"]), "#34d399", f"MFI {p['length']}", pane="MFI")],
                       levels=[Level(p["overbought"], "#f87171", "OB", pane="MFI"), Level(50, "#64748b", "50", pane="MFI"),
                               Level(p["oversold"], "#4ade80", "OS", pane="MFI")])

    def vote(self, ctx):
        p = self.params
        v = mfi(self.vote_bars(ctx), p["length"])[-1]
        if v is None:
            return None
        return Vote(1 if v > p["bull_above"] else -1 if v < p["bear_below"] else 0, f"MFI {v:.0f}")
