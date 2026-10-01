"""Previous day high / low / close as horizontal levels."""
from plugin_api import Indicator, Level, Overlay


class PrevDayLevels(Indicator):
    name = "Prev Day H/L/C"
    description = "PDH, PDL, PDC levels from D1 · adds PDH/PDL sweep to the analyser score"
    category = "Levels"
    purpose = "High, low, close kahapon"
    analyser_module = "prev_day"

    def compute(self, bars, tf, ctx):
        d1 = ctx.bars("D1")
        if len(d1) < 2:
            return Overlay()
        p = d1[-2]
        return Overlay(levels=[
            Level(p.h, "#ff55ff", "PDH"),
            Level(p.l, "#ff55ff", "PDL"),
            Level(p.c, "#aa88ff", "PDC"),
        ])
