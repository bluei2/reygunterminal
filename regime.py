"""
Market regime: what kind of market is it right now, and which setups fit it.

Per timeframe (closed bars only):
  ADX(14)          trend strength            >= 25 strong, < 20 weak
  Choppiness(14)   0-100, high = sideways    > 61.8 choppy, < 38.2 trending
  EMA20 vs EMA50   direction + slope
  ATR percentile   today's ATR(14) vs its last 200 values   >= 85 = volatile spike, <= 15 = very quiet

Regimes:
  TREND UP / TREND DOWN  ADX >= trend_adx, EMAs aligned, not choppy       -> follow the trend, pullback entries, trail
  RANGE                  ADX < range_adx or choppy                         -> box / mean reversion, fixed TP, no breakout chasing
  VOLATILE               ATR percentile >= 85                              -> smaller lot / wider SL, or stand aside
  QUIET                  ATR percentile <= 15 and not trending             -> compression: wait for the box breakout
  TRANSITION             none of the above                                 -> be selective

The main TF gives the regime; the higher TF is shown next to it (does the big picture agree?).
Auto trade can be limited to chosen regimes (AUTO TRADE ⚙ -> regimes). Every journal entry stores the regime
at entry, so the journal can show which regime makes you money.
"""
from __future__ import annotations

import json
import os
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(HERE, "regime_settings.json")
CURRENT = None

DEFAULTS = dict(tf="M15", htf="H1", adx_len=14, trend_adx=25.0, range_adx=20.0, chop_len=14, chop_hi=61.8,
                atr_len=14, atr_lookback=200, vol_hi=85.0, vol_lo=15.0)

ADVICE = {
    "TREND UP": "Sumabay sa trend: BUY sa pullback (EMA20 / order block), trailing stop. Iwasang mag-SELL.",
    "TREND DOWN": "Sumabay sa trend: SELL sa pullback (EMA20 / order block), trailing stop. Iwasang mag-BUY.",
    "RANGE": "Sideways: bumili sa ibaba / magbenta sa taas ng range, fixed TP. Huwag habulin ang breakout.",
    "VOLATILE": "Malikot: liitan ang lot o luwagan ang SL, o maghintay na kumalma. Mag-ingat sa news.",
    "QUIET": "Tahimik o nagsisiksik: abangan ang consolidation box at ang breakout nito.",
    "TRANSITION": "Walang malinaw na regime: maging mapili, mataas na score lang.",
}
COLORS = {"TREND UP": "#26d7a0", "TREND DOWN": "#ff5a78", "RANGE": "#fbbf24", "VOLATILE": "#e879f9",
          "QUIET": "#60a5fa", "TRANSITION": "#94a3b8"}
ALL = list(ADVICE)


def load_settings():
    s = dict(DEFAULTS)
    try:
        with open(SETTINGS_FILE) as f:
            for k, v in json.load(f).items():
                if k in s:
                    s[k] = v
    except (OSError, ValueError):
        pass
    return s


def _rma(vals, n):
    out, a = [], None
    for i, v in enumerate(vals):
        if i < n - 1:
            out.append(None)
            continue
        if a is None:
            a = sum(vals[i - n + 1:i + 1]) / n
        else:
            a = (a * (n - 1) + v) / n
        out.append(a)
    return out


def _ema(vals, n):
    out, k, e = [], 2 / (n + 1), None
    for v in vals:
        e = v if e is None else v * k + e * (1 - k)
        out.append(e)
    return out


def indicators(bars, s=DEFAULTS):
    n = len(bars)
    if n < max(60, s["adx_len"] * 3):
        return None
    tr, pdm, mdm = [0.0], [0.0], [0.0]
    for i in range(1, n):
        h, l, pc = bars[i].h, bars[i].l, bars[i - 1].c
        tr.append(max(h - l, abs(h - pc), abs(l - pc)))
        up, dn = h - bars[i - 1].h, bars[i - 1].l - l
        pdm.append(up if up > dn and up > 0 else 0.0)
        mdm.append(dn if dn > up and dn > 0 else 0.0)
    L = int(s["adx_len"])
    atr_w = _rma(tr, L)
    pdi = [None if a is None or a == 0 else 100 * p / a for p, a in zip(_rma(pdm, L), atr_w)]
    mdi = [None if a is None or a == 0 else 100 * m / a for m, a in zip(_rma(mdm, L), atr_w)]
    dx = [0.0 if p is None or m is None or p + m == 0 else 100 * abs(p - m) / (p + m) for p, m in zip(pdi, mdi)]
    adx = _rma(dx[L:], L)
    adx_now = adx[-1] if adx and adx[-1] is not None else 0.0
    # choppiness
    C = int(s["chop_len"])
    win = bars[-C:]
    rng = max(b.h for b in win) - min(b.l for b in win)
    import math
    chop = 100 * math.log10(sum(tr[-C:]) / rng) / math.log10(C) if rng > 0 else 50.0
    # ATR percentile
    A = int(s["atr_len"])
    atr = _rma(tr, A)
    hist = [a for a in atr[-int(s["atr_lookback"]):] if a is not None]
    atr_now = hist[-1] if hist else 0.0
    pct = 100 * sum(1 for a in hist if a <= atr_now) / len(hist) if hist else 50.0
    c = [b.c for b in bars]
    e20, e50 = _ema(c, 20), _ema(c, 50)
    slope = (e20[-1] - e20[-6]) / (atr_now or 1)
    ema_dir = 1 if e20[-1] > e50[-1] and c[-1] > e50[-1] else -1 if e20[-1] < e50[-1] and c[-1] < e50[-1] else 0
    return dict(adx=adx_now, pdi=pdi[-1] or 0, mdi=mdi[-1] or 0, chop=chop, atr=atr_now, atr_pct=pct,
                ema_dir=ema_dir, slope=slope)


def classify(bars, s=DEFAULTS):
    x = indicators(bars, s)
    if x is None:
        return dict(label="TRANSITION", dir=0, ok=False, why=["not enough bars"], advice=ADVICE["TRANSITION"],
                    color=COLORS["TRANSITION"])
    why = [f"ADX {x['adx']:.0f}", f"Chop {x['chop']:.0f}", f"ATR pct {x['atr_pct']:.0f}",
           f"EMA20/50 {'up' if x['ema_dir'] == 1 else 'down' if x['ema_dir'] == -1 else 'flat'}"]
    trending = x["adx"] >= s["trend_adx"] and x["chop"] < s["chop_hi"] and x["ema_dir"] != 0
    if x["atr_pct"] >= s["vol_hi"]:
        label = "VOLATILE"
    elif trending:
        label = "TREND UP" if x["ema_dir"] == 1 else "TREND DOWN"
    elif x["atr_pct"] <= s["vol_lo"]:
        label = "QUIET"
    elif x["adx"] < s["range_adx"] or x["chop"] >= s["chop_hi"]:
        label = "RANGE"
    else:
        label = "TRANSITION"
    d = 1 if label == "TREND UP" else -1 if label == "TREND DOWN" else 0
    return dict(label=label, dir=d, ok=True, why=why, advice=ADVICE[label], color=COLORS[label],
                **{k: round(v, 2) for k, v in x.items()})


class Regime:
    def __init__(self, hub):
        global CURRENT
        self.hub = hub
        self.settings = load_settings()
        self.cache = (0.0, None)
        self.last_label = None
        CURRENT = self

    def update_settings(self, new):
        s = self.settings
        for k in ("tf", "htf"):
            if new.get(k) in ("M5", "M15", "M30", "H1", "H4", "D1"):
                s[k] = new[k]
        for k in ("trend_adx", "range_adx", "chop_hi", "vol_hi", "vol_lo"):
            if k in new:
                try:
                    s[k] = float(new[k])
                except (TypeError, ValueError):
                    pass
        try:
            with open(SETTINGS_FILE, "w") as f:
                json.dump(s, f, indent=2)
        except OSError:
            pass
        self.cache = (0.0, None)
        return dict(ok=True, state=self.state())

    def state(self):
        if self.cache[1] is not None and time.time() - self.cache[0] < 5:
            return self.cache[1]
        s = self.settings
        out = dict(settings=s, all=ALL, colors=COLORS)
        for key in ("tf", "htf"):
            bars = self.hub.src.bars(s[key])[:-1]            # closed bars only
            out[key + "_read"] = dict(classify(bars, s), tf=s[key])
        m, h = out["tf_read"], out["htf_read"]
        out.update(label=m["label"], dir=m["dir"], color=m["color"], advice=m["advice"],
                   agree=(h["dir"] == m["dir"]) if m["dir"] else None)
        if self.last_label and m["label"] != self.last_label:
            self.hub.note("info", f"regime {s['tf']}: {self.last_label} → {m['label']}")
        self.last_label = m["label"]
        self.cache = (time.time(), out)
        return out

    def snapshot(self):
        """Small dict stored in the journal at entry."""
        try:
            st = self.state()
            return dict(label=st["label"], tf=st["settings"]["tf"], htf=st["htf_read"]["label"],
                        adx=st["tf_read"].get("adx"), atr_pct=st["tf_read"].get("atr_pct"))
        except Exception as e:
            from error_log import report
            report("DECISION_ERROR", "regime.snapshot", "REGIME_SNAPSHOT_FAILED", e)
            return None
