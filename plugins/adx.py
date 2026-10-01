"""ADX / DMI: trend strength (ADX above threshold = trending) and direction (+DI vs -DI)."""
from plugin_api import Indicator, Level, Line, Overlay, Vote, rma, true_range


def dmi(bars, n=14):
    size = len(bars)
    pdm, mdm = [0.0] * size, [0.0] * size
    for i in range(1, size):
        up = bars[i].h - bars[i - 1].h
        dn = bars[i - 1].l - bars[i].l
        pdm[i] = up if up > dn and up > 0 else 0.0
        mdm[i] = dn if dn > up and dn > 0 else 0.0
    tr = rma(true_range(bars), n)
    p, m = rma(pdm, n), rma(mdm, n)
    pdi = [100 * a / t if a is not None and t else None for a, t in zip(p, tr)]
    mdi = [100 * a / t if a is not None and t else None for a, t in zip(m, tr)]
    dx = [100 * abs(a - b) / (a + b) if a is not None and b is not None and (a + b) else None
          for a, b in zip(pdi, mdi)]
    return rma(dx, n), pdi, mdi


class ADX(Indicator):
    name = "ADX / DMI"
    description = "ADX with +DI / -DI · vote: +DI vs -DI when ADX is above the no-trend level"
    category = "Trend"
    purpose = "Lakas ng trend"
    params = {"length": 14, "trend_level": 25.0, "no_trend_below": 20.0}

    def compute(self, bars, tf, ctx):
        adx, pdi, mdi = dmi(bars, self.params["length"])
        return Overlay(lines=[Line(adx, "#f8fafc", "ADX", pane="ADX"),
                              Line(pdi, "#26d7a0", "+DI", pane="ADX", width=1),
                              Line(mdi, "#ff5a78", "-DI", pane="ADX", width=1)],
                       levels=[Level(self.params["trend_level"], "#64748b", "TR", pane="ADX")])

    def vote(self, ctx):
        adx, pdi, mdi = dmi(self.vote_bars(ctx), self.params["length"])
        if not adx or None in (adx[-1], pdi[-1], mdi[-1]):
            return None
        if adx[-1] < self.params["no_trend_below"]:
            return Vote(0, f"ADX {adx[-1]:.0f} no trend")
        return Vote(1 if pdi[-1] > mdi[-1] else -1, f"ADX {adx[-1]:.0f}, {'+DI' if pdi[-1] > mdi[-1] else '-DI'} leads")
