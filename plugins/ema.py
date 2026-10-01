"""EMA fast / slow pair."""
from plugin_api import Indicator, Line, Overlay, Vote, closes, ema


class EMAPair(Indicator):
    name = "EMA 20/50"
    description = "Fast / slow EMA · vote: fast above slow"
    category = "Trend"
    purpose = "Direksyon ng trend"
    params = {"fast": 20, "slow": 50}

    def compute(self, bars, tf, ctx):
        c, p = closes(bars), self.params
        return Overlay(lines=[Line(ema(c, p["fast"]), "#ffff55", f"EMA{p['fast']}"),
                              Line(ema(c, p["slow"]), "#ff9955", f"EMA{p['slow']}")])

    def vote(self, ctx):
        c, p = closes(self.vote_bars(ctx)), self.params
        f, s = ema(c, p["fast"])[-1], ema(c, p["slow"])[-1]
        if f is None or s is None:
            return None
        return Vote(1 if f > s else -1, f"EMA{p['fast']} {'>' if f > s else '<'} EMA{p['slow']}")
