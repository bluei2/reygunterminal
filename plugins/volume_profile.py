"""
Volume Profile - DAILY (session) profiles. The only volume profile of the terminal.

Trade today against the COMPLETED profile of the previous trading day: today's profile is still forming, so its
POC / value area keep moving. On the chart:
  * the last `days_shown` completed days (max 5, to keep the chart light): each day's own profile drawn inside
    that day, with its POC highlighted and its value area as a faint box
  * pPOC / pVAH / pVAL: the previous day's levels as lines, and its value area carried into today
  * today's developing profile: optional (show_today), faint, never used for the vote
Day = broker server day (bar times are server time). Days with too few bars (e.g. the short Sunday open) are skipped.
Tick volume spread over each bar's range; built from M5 bars (finer than the chart) when available.

Vote (vote timeframe / chart timeframes, closed bars only), against the PREVIOUS day:
  close ABOVE pVAH -> +1   (accepted above yesterday's value)
  close BELOW pVAL -> -1   (accepted below yesterday's value)
  inside the value area -> 0 (rotation, no directional edge)
"""
import json

from plugin_api import Indicator, Level, Overlay, Profile, Vote, Zone

DAY = 86400
MAX_DAYS = 5


def _profile(seg, rows, value_area):
    hi, lo = max(b.h for b in seg), min(b.l for b in seg)
    if hi <= lo:
        return None
    step = (hi - lo) / rows
    vol = [0.0] * rows
    for b in seg:
        z = min(rows - 1, int((b.h - lo) / step))
        a = min(int((b.l - lo) / step), z)
        share = (b.v or 1.0) / (z - a + 1)
        for r in range(a, z + 1):
            vol[r] += share
    poc = max(range(rows), key=lambda r: vol[r])
    total, inside = sum(vol), vol[poc]
    lo_i = hi_i = poc
    while inside < total * value_area / 100 and (lo_i > 0 or hi_i < rows - 1):
        up = vol[hi_i + 1] if hi_i < rows - 1 else -1
        dn = vol[lo_i - 1] if lo_i > 0 else -1
        if up >= dn:
            hi_i += 1
            inside += up
        else:
            lo_i -= 1
            inside += dn
    return dict(rows=[(lo + r * step, lo + (r + 1) * step, vol[r]) for r in range(rows)], poc_i=poc,
                poc=lo + (poc + 0.5) * step, vah=lo + (hi_i + 1) * step, val=lo + lo_i * step,
                high=hi, low=lo)


def daily_profiles(bars, now_t=None, rows=48, value_area=70.0, min_bars=40, days=MAX_DAYS, with_today=False):
    """Profiles of the last `days` COMPLETE server days before the day of `now_t` (oldest first), plus today's
    developing profile when with_today. Each: poc, vah, val, rows, day_start, day_end, complete."""
    if not bars:
        return []
    now_t = bars[-1].t if now_t is None else now_t
    today = now_t // DAY
    by_day = {}
    for b in bars:
        d = b.t // DAY
        if d < today or (with_today and d == today and b.t <= now_t):
            by_day.setdefault(d, []).append(b)
    full = [d for d in sorted(by_day) if d < today and len(by_day[d]) >= min_bars][-days:]
    out = []
    for d in full:
        vp = _profile(by_day[d], rows, value_area)
        if vp:
            vp.update(day_start=d * DAY, day_end=(d + 1) * DAY, complete=True, bars=len(by_day[d]))
            out.append(vp)
    if with_today and by_day.get(today) and len(by_day[today]) >= 3:
        vp = _profile(by_day[today], rows, value_area)
        if vp:
            vp.update(day_start=today * DAY, day_end=(today + 1) * DAY, complete=False, bars=len(by_day[today]))
            out.append(vp)
    return out


def prev_day_profile(bars, now_t=None, rows=48, value_area=70.0, min_bars=40, days_back=1):
    """The previous COMPLETE day (days_back > 1: the last N complete days merged into one profile). Never uses
    a bar of the current day."""
    if not bars:
        return None
    now_t = bars[-1].t if now_t is None else now_t
    today = now_t // DAY
    by_day = {}
    for b in bars:
        d = b.t // DAY
        if d < today:
            by_day.setdefault(d, []).append(b)
    full = [d for d in sorted(by_day) if len(by_day[d]) >= min_bars]
    if len(full) < days_back:
        return None
    use = full[-days_back:]
    vp = _profile([b for d in use for b in by_day[d]], rows, value_area)
    if vp:
        vp.update(day=use[-1] * DAY, day_start=use[0] * DAY, today_start=today * DAY)
    return vp


class VolumeProfile(Indicator):
    name = "Volume Profile"
    description = ("Daily profile ng bawat araw (hanggang 5 araw sa chart) · pPOC / pVAH / pVAL ng KAHAPON ang "
                   "basehan ng entry at ng vote (ang profile ngayong araw ay nabubuo pa, kaya faint lang at optional)")
    category = "Volume"
    purpose = "Daily POC at value area"
    vote_tf = "M15"
    params = {"days_shown": 5, "rows": 40, "value_area": 70.0, "min_bars": 40, "show_today": False,
              "show_value_box": True, "profile_width": 0.6}

    def __init__(self):
        self._cache = (None, None)

    def _src(self, ctx, fallback, n_days=1):
        """The finest timeframe whose history covers the days needed (M5 -> M15 -> M30 -> H1): MT5 keeps only a
        few hundred bars per chart, so 5 days of M5 are often not loaded while H1 always covers them."""
        for tf in ("M5", "M15", "M30", "H1"):
            b = ctx.bars(tf)
            if len(b) > 50 and b[0].t <= (b[-1].t // DAY - n_days) * DAY:
                return b
        return fallback

    def _cfg(self, src=None):
        """min_bars is counted in M5 bars; scaled to the timeframe actually used (e.g. 40 M5 bars = 4 H1 bars)."""
        p = self.params
        mb = max(5, int(p["min_bars"]))
        if src and len(src) > 2:
            sec = min(b2.t - b1.t for b1, b2 in zip(src[-50:], src[-49:])) or 300
            mb = max(3, int(mb * 300 / sec))
        return (max(8, int(p["rows"])), float(p["value_area"]), mb)

    def _days(self, src, now_t):
        p = self.params
        rows, va, mb = self._cfg(src)
        key = (len(src), src[-1].t if src else 0, now_t // DAY, json.dumps(p, sort_keys=True))
        if self._cache[0] != key:
            n = max(1, min(MAX_DAYS, int(p["days_shown"])))
            self._cache = (key, daily_profiles(src, now_t, rows, va, mb, n, bool(p["show_today"])))
        return self._cache[1]

    def compute(self, bars, tf, ctx):
        if not bars:
            return Overlay()
        src = self._src(ctx, bars, max(1, min(MAX_DAYS, int(self.params["days_shown"]))))
        days = self._days(src, bars[-1].t)
        if not days:
            return Overlay()
        p = self.params
        times = [b.t for b in bars]
        ov = Overlay()

        def snap(t0, t1):
            """First / last chart bar inside [t0, t1): the profile is drawn over that day's candles."""
            inside = [t for t in times if t0 <= t < t1]
            return (inside[0], inside[-1]) if inside else (None, None)

        for vp in days:
            a, b = snap(vp["day_start"], vp["day_end"])
            if a is None:
                continue
            today = not vp["complete"]
            ov.profiles.append(Profile(vp["rows"], "#94a3b8" if today else "#38bdf8", highlight=[vp["poc_i"]],
                                       highlight_color="#f59e0b", width_pct=float(p["profile_width"]),
                                       t_from=a, t_to=b))
            if p["show_value_box"] and not today:
                ov.zones.append(Zone(vp["val"], vp["vah"], a, "#0c4a6e", b, "", ""))
        prev = [vp for vp in days if vp["complete"]]
        if prev:
            y = prev[-1]
            ov.levels += [Level(y["poc"], "#f59e0b", "pPOC"), Level(y["vah"], "#38bdf8", "pVAH"),
                          Level(y["val"], "#38bdf8", "pVAL")]
            t_today = next((t for t in times if t >= y["day_end"]), None)
            if t_today is not None:
                ov.zones.append(Zone(y["val"], y["vah"], t_today, "#0c4a6e", None, "prev day value area", "#38bdf8"))
        return ov

    def vote(self, ctx):
        b = self.vote_bars(ctx)                              # closed bars of the vote timeframe
        if not b:
            return None
        src = self._src(ctx, b, 1)
        rows, va, mb = self._cfg(src)
        vp = prev_day_profile(src, b[-1].t, rows, va, mb, 1)
        if not vp:
            return None
        c = b[-1].c
        if c > vp["vah"]:
            return Vote(1, f"above prev-day VAH {vp['vah']:.2f}")
        if c < vp["val"]:
            return Vote(-1, f"below prev-day VAL {vp['val']:.2f}")
        return Vote(0, f"inside prev-day value {vp['val']:.2f}-{vp['vah']:.2f} (POC {vp['poc']:.2f})")
