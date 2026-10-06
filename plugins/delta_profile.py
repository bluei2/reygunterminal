"""
DELTA PROFILE - buy vs sell volume at every price (Eugene, 2026-10-04: "parang volume profile sa main chart pero may
value kung sino ang mas maraming volume o mas agresibo sa dalawa").

Same estimate as the CVD and the MT5 Master EA (gold CFD has no real aggressor data):
    delta = tick volume x (close - open) / (high - low)      (per lower-timeframe bar: M1 / M5 when loaded)
    buy = (volume + delta) / 2 ,  sell = (volume - delta) / 2
Every row: green = buyers, red = sellers, the number = the row's delta. Gold row = POC (most traded price).
Top text: total BUY % vs SELL %, who is more aggressive, and who is pushing in the last quarter of the window.
Vote: buyers clearly stronger (>= vote_edge %) -> +1, sellers -> -1.
"""
from plugin_api import Indicator, Level, Overlay, Profile, Vote

TF_SEC = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400, "D1": 86400}


def _delta(b):
    rg = b.h - b.l
    return (b.v or 0.0) * (b.c - b.o) / rg if rg > 0 else 0.0


def num(x):
    a = abs(x)
    s = f"{a / 1e6:.1f}M" if a >= 1e6 else f"{a / 1e3:.1f}K" if a >= 1e3 else f"{a:.0f}"
    return ("+" if x > 0 else "-" if x < 0 else "") + s


def delta_profile(src, rows=40):
    """src = the bars of the window (finest available). -> dict(rows=[(lo, hi, total)], buy=[...], poc_i, totals)."""
    if not src:
        return None
    hi, lo = max(b.h for b in src), min(b.l for b in src)
    if hi <= lo:
        return None
    step = (hi - lo) / rows
    buy, sell = [0.0] * rows, [0.0] * rows
    tb = ts = recent = 0.0
    q0 = int(len(src) * 0.75)
    for k, b in enumerate(src):
        v = float(b.v or 0.0)
        d = _delta(b)
        bb, ss = (v + d) / 2, (v - d) / 2
        tb, ts = tb + bb, ts + ss
        if k >= q0:
            recent += d
        r1 = max(0, min(rows - 1, int((b.l - lo) / step)))
        r2 = max(0, min(rows - 1, int((b.h - lo) / step)))
        share = 1.0 / (r2 - r1 + 1)
        for r in range(r1, r2 + 1):
            buy[r] += bb * share
            sell[r] += ss * share
    tot = [buy[r] + sell[r] for r in range(rows)]
    poc = max(range(rows), key=lambda r: tot[r])
    return dict(rows=[(lo + r * step, lo + (r + 1) * step, tot[r]) for r in range(rows)], buy=buy, sell=sell,
                poc_i=poc, poc=lo + (poc + 0.5) * step, total_buy=tb, total_sell=ts, recent=recent)


def verdict(dp, edge=52.0):
    t = dp["total_buy"] + dp["total_sell"]
    bp = 100.0 * dp["total_buy"] / t if t > 0 else 50.0
    who = "BUYERS mas agresibo" if bp >= edge else "SELLERS mas agresibo" if bp <= 100 - edge else "BALANSE"
    return bp, who


class DeltaProfile(Indicator):
    name = "Delta Profile (buy vs sell)"
    description = ("Volume profile na hati sa BUY (berde) at SELL (pula) bawat presyo, may delta number bawat row, "
                   "POC, at kung sino ang mas agresibo · pareho ng formula ng CVD at ng MT5 Master EA")
    category = "Volume"
    purpose = "Sino ang mas malakas sa bawat presyo"
    default_on = True
    vote_tf = "M15"
    params = {"bars": 200, "rows": 40, "width": 0.25, "lower_tf": "M1", "vote_edge": 55.0}

    def _src(self, bars, tf, ctx):
        """The bars of the window from the finest loaded timeframe below the chart (M1 -> M5), else the chart bars."""
        n = max(20, int(self.params["bars"]))
        win = bars[-n:]
        if not win or ctx is None or not hasattr(ctx, "bars"):
            return win
        want = [self.params.get("lower_tf") or "M1", "M1", "M5"]
        for ltf in dict.fromkeys(want):
            if TF_SEC.get(ltf, 10 ** 9) >= TF_SEC.get(tf, 300):
                continue
            try:
                lb = ctx.bars(ltf)
            except Exception:
                continue
            if lb and lb[0].t <= win[0].t:
                return [b for b in lb if b.t >= win[0].t]
        return win

    def compute(self, bars, tf, ctx):
        src = self._src(bars, tf, ctx)
        dp = delta_profile(src, max(8, min(200, int(self.params["rows"]))))
        if dp is None:
            return Overlay()
        bp, who = verdict(dp, 52.0)
        tot = dp["total_buy"] - dp["total_sell"]
        title = (f"BUY {bp:.0f}% · SELL {100 - bp:.0f}% · Δ {num(tot)} → {who}"
                 f" · huling 25%: {num(dp['recent'])} ({'buyers' if dp['recent'] > 0 else 'sellers'} tumutulak)")
        pf = Profile(rows=dp["rows"], color="#22c55e", highlight=[dp["poc_i"]], highlight_color="#fbbf24",
                     width_pct=float(self.params["width"]), buy=dp["buy"], sell_color="#ef4444", title=title)
        return Overlay(profiles=[pf], levels=[Level(round(dp["poc"], 2), "#fbbf24", "ΔPOC")])

    def vote(self, ctx):
        b = self.vote_bars(ctx)
        dp = delta_profile(b[-int(self.params["bars"]):], 20)
        if dp is None:
            return None
        bp, who = verdict(dp, float(self.params["vote_edge"]))
        return Vote(1 if bp >= self.params["vote_edge"] else -1 if bp <= 100 - self.params["vote_edge"] else 0,
                    f"delta profile: {who} ({bp:.0f}% buy)")
