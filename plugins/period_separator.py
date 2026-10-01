"""
Period separator (gaya ng sa MT5): patayong linya sa simula ng bawat period.
  M1-H1 -> bawat araw   |   H4 -> bawat linggo   |   D1 -> bawat buwan
Naka-base sa server time ng broker, pareho sa MT5.
"""
from datetime import datetime, timezone

from plugin_api import Indicator, Overlay, VLine

DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


class PeriodSeparator(Indicator):
    name = "Period separator"
    description = "Vertical line at each new day (M1-H1), week (H4) or month (D1)"
    category = "Time"
    purpose = "Hati ng araw o linggo"

    def compute(self, bars, tf, ctx):
        ov = Overlay()
        prev = None
        for b in bars:
            d = datetime.fromtimestamp(b.t, tz=timezone.utc)   # server time, stored as-is
            if tf == "D1":
                key, label = (d.year, d.month), d.strftime("%b %Y")
            elif tf == "H4":
                iso = d.isocalendar()
                key, label = (iso[0], iso[1]), "Week " + d.strftime("%d %b")
            else:
                key, label = (d.year, d.month, d.day), f"{DAY_NAMES[d.weekday()]} {d.day:02d}"
            if prev is not None and key != prev:
                ov.vlines.append(VLine(b.t, "#94a3b8", label))
            prev = key
        return ov
