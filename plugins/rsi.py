"""RSI with overbought / oversold levels."""
from plugin_api import Indicator, Level, Line, Overlay, Vote, closes, rma


def rsi(c, n=14):
    up = [max(0.0, c[i] - c[i - 1]) for i in range(1, len(c))]
    dn = [max(0.0, c[i - 1] - c[i]) for i in range(1, len(c))]
    au, ad = rma(up, n), rma(dn, n)
    out = [None]
    for a, d in zip(au, ad):
        out.append(None if a is None else (100.0 if d == 0 else 100 - 100 / (1 + a / d)))
    return out


class RSI(Indicator):
    name = "RSI"
    description = "Relative Strength Index · vote: above bull level / below bear level"
    category = "Momentum"
    purpose = "Overbought at oversold"
    params = {"length": 14, "overbought": 70.0, "oversold": 30.0, "bull_above": 55.0, "bear_below": 45.0}

    def compute(self, bars, tf, ctx):
        p = self.params
        return Overlay(lines=[Line(rsi(closes(bars), p["length"]), "#a78bfa", f"RSI {p['length']}", pane="RSI")],
                       levels=[Level(p["overbought"], "#f87171", "OB", pane="RSI"), Level(50, "#64748b", "50", pane="RSI"),
                               Level(p["oversold"], "#4ade80", "OS", pane="RSI")])

    def vote(self, ctx):
        p = self.params
        r = rsi(closes(self.vote_bars(ctx)), p["length"])[-1]
        if r is None:
            return None
        return Vote(1 if r > p["bull_above"] else -1 if r < p["bear_below"] else 0, f"RSI {r:.0f}")
