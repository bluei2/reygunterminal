"""Classic daily pivot points from the previous D1 candle: P, R1-R3, S1-S3."""
from plugin_api import Vote, Indicator, Level, Overlay


class PivotPoints(Indicator):
    name = "Pivot Points (daily)"
    description = "Classic floor pivots P · R1-R3 · S1-S3 from yesterday"
    category = "Levels"
    purpose = "Daily support at resistance"

    def compute(self, bars, tf, ctx):
        d1 = ctx.bars("D1")
        if len(d1) < 2:
            return Overlay()
        y = d1[-2]
        p = (y.h + y.l + y.c) / 3
        r1, s1 = 2 * p - y.l, 2 * p - y.h
        r2, s2 = p + (y.h - y.l), p - (y.h - y.l)
        r3, s3 = y.h + 2 * (p - y.l), y.l - 2 * (y.h - p)
        lv = [Level(p, "#e2e8f0", "P")]
        lv += [Level(v, "#f87171", f"R{i}") for i, v in enumerate((r1, r2, r3), 1)]
        lv += [Level(v, "#4ade80", f"S{i}") for i, v in enumerate((s1, s2, s3), 1)]
        return Overlay(levels=lv)

    def vote(self, ctx):
        d1, b = ctx.bars("D1"), self.vote_bars(ctx)
        if len(d1) < 2 or not b:
            return None
        y = d1[-2]
        p = (y.h + y.l + y.c) / 3
        return Vote(1 if b[-1].c > p else -1, "above daily P" if b[-1].c > p else "below daily P")
