"""
Trading sessions as boxes (high-low of each session) with a label.

Oras ay UTC (hindi Manila). Baguhin sa ⚙ settings ng "Sessions" sa SuperChart.
  Asia      00:00-08:00 UTC  = 08:00-16:00 Manila
  Frankfurt 06:00-14:00 UTC  = 14:00-22:00 Manila (1 oras bago ang London; kasabay ng NY open sa dulo)
  London    07:00-16:00 UTC  = 15:00-00:00 Manila
  New York  12:00-21:00 UTC  = 20:00-05:00 Manila
Awtomatikong ina-adjust sa server time ng broker (ctx.utc_offset).
"""
from plugin_api import Indicator, Overlay, Zone

# name, start hour UTC, end hour UTC, color
SESSIONS = [
    ("ASIA", 0, 8, "#60a5fa"),
    ("LONDON", 7, 16, "#fbbf24"),
    ("NEW YORK", 12, 21, "#f472b6"),
    ("FRANKFURT", 6, 14, "#34d399"),
]
MAX_DAYS = 6                      # only the last few days, para hindi magulo
SHOW_ON = ("M1", "M5", "M15", "M30", "H1")


def in_session(hour: int, start: int, end: int) -> bool:
    """Works for sessions that cross midnight too (e.g. 23 -> 7)."""
    return start <= hour < end if start < end else (hour >= start or hour < end)


def active_sessions(utc_hour: int):
    return [name for name, a, b, _ in SESSIONS if in_session(utc_hour, a, b)]


class TradingSessions(Indicator):
    name = "Sessions"
    description = "Asia / Frankfurt / London / New York boxes (high-low of each session) · hours in UTC"
    category = "Time"
    purpose = "Asia, Frankfurt, London, New York"
    params = {"asia_start": 0, "asia_end": 8, "london_start": 7, "london_end": 16,
              "newyork_start": 12, "newyork_end": 21, "frankfurt_start": 6, "frankfurt_end": 14,
              "max_days": MAX_DAYS}

    def table(self):
        p = self.params
        return [("ASIA", p["asia_start"], p["asia_end"], SESSIONS[0][3]),
                ("LONDON", p["london_start"], p["london_end"], SESSIONS[1][3]),
                ("NEW YORK", p["newyork_start"], p["newyork_end"], SESSIONS[2][3]),
                ("FRANKFURT", p.get("frankfurt_start", 6), p.get("frankfurt_end", 14), SESSIONS[3][3])]

    def compute(self, bars, tf, ctx):
        ov = Overlay()
        if tf not in SHOW_ON or not bars:
            return ov
        off = ctx.utc_offset * 3600
        last_day = (bars[-1].t - off) // 86400
        boxes = {}                          # (session, utc_day) -> [t_from, t_to, hi, lo]
        for b in bars:
            ut = b.t - off                  # bar time in UTC
            day = ut // 86400
            if last_day - day >= self.params["max_days"]:
                continue
            hour = (ut % 86400) // 3600
            for name, a, e, _ in self.table():
                if in_session(hour, a, e):
                    # a session that crosses midnight belongs to the day it ends on
                    k = (name, day + 1 if a > e and hour >= a else day)
                    box = boxes.get(k)
                    if box is None:
                        boxes[k] = [b.t, b.t, b.h, b.l]
                    else:
                        box[1] = b.t
                        box[2] = max(box[2], b.h)
                        box[3] = min(box[3], b.l)
        colors = {n: c for n, _, _, c in self.table()}
        for (name, _day), (t0, t1, hi, lo) in sorted(boxes.items(), key=lambda kv: kv[1][0]):
            ov.zones.append(Zone(lo, hi, t0, colors[name], t_to=t1, label=name, border=colors[name]))
        return ov
