"""
Session VWAP, resets every day (server day, same as MT5), with two standard-deviation bands.
MT5 gold uses TICK volume, not real exchange volume.
"""
from plugin_api import Indicator, Line, Overlay, Vote


def daily_vwap(bars, band1=1.0, band2=2.0):
    n = len(bars)
    vw, u1, l1, u2, l2 = ([None] * n for _ in range(5))
    day = None
    pv = vv = pv2 = 0.0
    for i, b in enumerate(bars):
        d = b.t // 86400
        if d != day:
            day, pv, vv, pv2 = d, 0.0, 0.0, 0.0
        tp = (b.h + b.l + b.c) / 3
        v = b.v or 1.0
        pv += tp * v
        vv += v
        pv2 += tp * tp * v
        m = pv / vv
        sd = max(0.0, pv2 / vv - m * m) ** 0.5
        vw[i], u1[i], l1[i], u2[i], l2[i] = m, m + band1 * sd, m - band1 * sd, m + band2 * sd, m - band2 * sd
    for i in range(1, n):                       # gap at each daily reset
        if bars[i].t // 86400 != bars[i - 1].t // 86400:
            for arr in (vw, u1, l1, u2, l2):
                arr[i - 1] = None
    return vw, u1, l1, u2, l2


class DailyVWAP(Indicator):
    name = "VWAP (daily)"
    description = "Daily VWAP with σ bands · tick volume · vote: price above / below VWAP"
    category = "Volume"
    purpose = "Fair price ng araw"
    params = {"band1": 1.0, "band2": 2.0}

    def compute(self, bars, tf, ctx):
        if tf == "D1":
            return Overlay()
        p = self.params
        vw, u1, l1, u2, l2 = daily_vwap(bars, p["band1"], p["band2"])
        return Overlay(lines=[
            Line(vw, "#fbbf24", "VWAP", width=2),
            Line(u1, "#fde68a", f"+{p['band1']:g}σ", width=1), Line(l1, "#fde68a", f"-{p['band1']:g}σ", width=1),
            Line(u2, "#f59e0b", f"+{p['band2']:g}σ", width=1), Line(l2, "#f59e0b", f"-{p['band2']:g}σ", width=1),
        ])

    def vote(self, ctx):
        b = self.vote_bars(ctx)
        vw = [v for v in daily_vwap(b)[0] if v is not None]
        if not b or not vw:
            return None
        return Vote(1 if b[-1].c > vw[-1] else -1, "above VWAP" if b[-1].c > vw[-1] else "below VWAP")
