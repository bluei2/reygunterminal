"""Ichimoku. Spans are shifted forward inside the chart (the future part is not drawn)."""
from plugin_api import Indicator, Line, Overlay, Vote, highest, highs, lowest, lows


def mid(h, l, n):
    hh, ll = highest(h, n), lowest(l, n)
    return [(a + b) / 2 if a is not None else None for a, b in zip(hh, ll)]


def shift(values, k):
    if k == 0:
        return list(values)
    return [None] * k + values[:-k] if k > 0 else values[-k:] + [None] * (-k)


class Ichimoku(Indicator):
    name = "Ichimoku"
    description = "Tenkan · Kijun · Senkou A/B · Chikou · vote: above/below cloud + TK cross"
    category = "Trend"
    purpose = "Trend at cloud S/R"
    params = {"tenkan": 9, "kijun": 26, "senkou_b": 52, "displacement": 26}

    def _calc(self, bars):
        p = self.params
        h, l = highs(bars), lows(bars)
        tenkan, kijun = mid(h, l, p["tenkan"]), mid(h, l, p["kijun"])
        span_a = [(a + b) / 2 if a is not None and b is not None else None for a, b in zip(tenkan, kijun)]
        return tenkan, kijun, shift(span_a, p["displacement"]), shift(mid(h, l, p["senkou_b"]), p["displacement"])

    def compute(self, bars, tf, ctx):
        tenkan, kijun, a, b = self._calc(bars)
        chikou = shift([x.c for x in bars], -self.params["displacement"])
        return Overlay(lines=[
            Line(tenkan, "#38bdf8", "Tenkan", width=1), Line(kijun, "#f43f5e", "Kijun", width=1),
            Line(a, "#4ade80", "Senkou A", width=1), Line(b, "#f87171", "Senkou B", width=1),
            Line(chikou, "#a3a3a3", "Chikou", width=1),
        ])

    def vote(self, ctx):
        bars = self.vote_bars(ctx)
        tenkan, kijun, a, b = self._calc(bars)
        if not bars or None in (a[-1], b[-1], tenkan[-1], kijun[-1]):
            return None
        c = bars[-1].c
        if c > max(a[-1], b[-1]) and tenkan[-1] > kijun[-1]:
            return Vote(1, "above cloud, TK up")
        if c < min(a[-1], b[-1]) and tenkan[-1] < kijun[-1]:
            return Vote(-1, "below cloud, TK down")
        return Vote(0, "in/near cloud")
