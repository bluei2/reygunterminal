"""On-Balance Volume (tick volume)."""
from plugin_api import Indicator, Line, Overlay, Vote, ema


def obv(bars):
    out, total = [], 0.0
    for i, b in enumerate(bars):
        if i:
            if b.c > bars[i - 1].c:
                total += b.v
            elif b.c < bars[i - 1].c:
                total -= b.v
        out.append(total)
    return out


class OBV(Indicator):
    name = "OBV"
    description = "On-balance volume + EMA · vote: OBV above / below its EMA"
    category = "Volume"
    purpose = "Volume confirmation"
    params = {"ema_length": 20}

    def compute(self, bars, tf, ctx):
        o = obv(bars)
        return Overlay(lines=[Line(o, "#38bdf8", "OBV", pane="OBV"),
                              Line(ema(o, self.params["ema_length"]), "#fb923c", "EMA", pane="OBV", width=1)])

    def vote(self, ctx):
        o = obv(self.vote_bars(ctx))
        e = ema(o, self.params["ema_length"])
        if not o or e[-1] is None:
            return None
        return Vote(1 if o[-1] > e[-1] else -1, "OBV above EMA" if o[-1] > e[-1] else "OBV below EMA")
