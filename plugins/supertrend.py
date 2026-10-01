"""Supertrend. Green line under price in uptrend, red above in downtrend. Arrows on flips."""
from plugin_api import Indicator, Line, Marker, Overlay, Vote, atr


def supertrend(bars, period=10, mult=3.0):
    a = atr(bars, period)
    n = len(bars)
    up_line, dn_line, direction = [None] * n, [None] * n, [0] * n
    fub = flb = None
    d = 1
    for i, b in enumerate(bars):
        if a[i] is None:
            continue
        mid = (b.h + b.l) / 2
        ub, lb = mid + mult * a[i], mid - mult * a[i]
        pc = bars[i - 1].c if i else b.c
        fub = ub if fub is None or ub < fub or pc > fub else fub
        flb = lb if flb is None or lb > flb or pc < flb else flb
        if d == 1 and b.c < flb:
            d = -1
        elif d == -1 and b.c > fub:
            d = 1
        direction[i] = d
        if d == 1:
            up_line[i] = flb
        else:
            dn_line[i] = fub
    return up_line, dn_line, direction


class Supertrend(Indicator):
    name = "Supertrend"
    description = "ATR trailing trend line · arrows on flips · vote: trend direction"
    category = "Trend"
    purpose = "Trend at trailing stop"
    params = {"atr_length": 10, "multiplier": 3.0}

    def _st(self, bars):
        return supertrend(bars, self.params["atr_length"], self.params["multiplier"])

    def compute(self, bars, tf, ctx):
        up, dn, dr = self._st(bars)
        ov = Overlay(lines=[Line(up, "#26d7a0", "ST up"), Line(dn, "#ff5a78", "ST dn")])
        for i in range(1, len(bars)):
            if dr[i] and dr[i - 1] and dr[i] != dr[i - 1]:
                if dr[i] == 1:
                    ov.markers.append(Marker(bars[i].t, "below", "▲", "bold #26d7a0"))
                else:
                    ov.markers.append(Marker(bars[i].t, "above", "▼", "bold #ff5a78"))
        return ov

    def vote(self, ctx):
        _, _, dr = self._st(self.vote_bars(ctx))
        d = dr[-1] if dr else 0
        return Vote(d, "Supertrend up" if d == 1 else "Supertrend down" if d == -1 else "warming up")
