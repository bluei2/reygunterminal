"""Parabolic SAR as dots."""
from plugin_api import Indicator, Line, Overlay, Vote


def psar(bars, start=0.02, step=0.02, maximum=0.2):
    n = len(bars)
    out = [None] * n
    if n < 3:
        return out
    up = bars[1].c >= bars[0].c
    af = start
    ep = bars[0].h if up else bars[0].l
    sar = bars[0].l if up else bars[0].h
    for i in range(1, n):
        b = bars[i]
        sar = sar + af * (ep - sar)
        if up:
            sar = min(sar, bars[i - 1].l, bars[i - 2].l if i > 1 else bars[i - 1].l)
            if b.l < sar:
                up, sar, ep, af = False, ep, b.l, start
            elif b.h > ep:
                ep, af = b.h, min(maximum, af + step)
        else:
            sar = max(sar, bars[i - 1].h, bars[i - 2].h if i > 1 else bars[i - 1].h)
            if b.h > sar:
                up, sar, ep, af = True, ep, b.h, start
            elif b.l < ep:
                ep, af = b.l, min(maximum, af + step)
        out[i] = sar
    return out


class ParabolicSAR(Indicator):
    name = "Parabolic SAR"
    description = "Stop-and-reverse dots · vote: price above / below SAR"
    category = "Trend"
    purpose = "Trailing stop at reversal"
    params = {"start": 0.02, "step": 0.02, "maximum": 0.2}

    def _sar(self, bars):
        p = self.params
        return psar(bars, p["start"], p["step"], p["maximum"])

    def compute(self, bars, tf, ctx):
        return Overlay(lines=[Line(self._sar(bars), "#e2e8f0", "PSAR", style="dots")])

    def vote(self, ctx):
        b = self.vote_bars(ctx)
        s = self._sar(b)
        if not b or s[-1] is None:
            return None
        return Vote(1 if b[-1].c > s[-1] else -1, "price above SAR" if b[-1].c > s[-1] else "price below SAR")
