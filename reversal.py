"""
Reversal detector: signs that the move is ending and price may turn.

For every closed bar it adds up evidence for a BEARISH reversal (top) and a BULLISH reversal (bottom).
Only bars up to that bar are used (no look-ahead), except the CONFIRMATION, which by nature comes later.

  LIQUIDITY SWEEP (wick takes the liquidity, the bar closes back inside)       strongest type + 5 per extra, max 40
    Equal highs / lows      2+ swing highs (lows) within eq_tol x ATR: resting stops              35   EQH / EQL
    Previous day high / low yesterday's high (low)                                               30   PDH / PDL
    Asia session high / low today's Asian range (00-07 UTC), swept in London / New York          30   ASH / ASL
    Swing high / low        the latest swing high (low) that was still intact                    25   SWH / SWL
    Range high / low        highest high (lowest low) of the last `sweep_lookback` bars          25   RGH / RGL
    Failed breakout         the previous bar CLOSED beyond the range, this bar closes back in    25   FB
  CANDLE PATTERN                                                                strongest + 5 per extra, max 30
    Evening / Morning star  3 bars: big body, small body, close past the middle of bar 1         25   ES / MS
    Three outside down / up engulfing + a close beyond it                                        25   3O
    Engulfing               body swallows the previous opposite body                             20   ENG
    Shooting star / Hammer  wick >= pin_ratio x body and >= 55% of the bar (pin bar)             20   PIN
    Dark cloud / Piercing   opens beyond, closes past the middle of the previous body           15   DC / PL
    Tweezer top / bottom    equal highs (lows) with opposite colors                              15   TWZ
    Harami                  small opposite body inside a big body                                10   HAR
    Doji                    body <= 10% of the bar (indecision)                                  5    DOJ
  RSI DIVERGENCE            higher high + lower RSI (top) / lower low + higher RSI (bottom)       20   DIV
  CONTEXT (+10 each)        RSI overbought / oversold · >= ext_atr x ATR from EMA20 · climax volume (>= vol_mult x
                            average) · at an opposite order block
A trigger (sweep, candle pattern or divergence) is required. Reported when score >= min_score (max 100).

  CONFIRMATION within `confirm_bars` closed bars:
    CHoCH close             a close through the other end of the reversal bar
    Confirmation candle     the next bar is a strong candle in the new direction (body >= 50% of its range)
                            closing past the reversal bar's body
  -> "R✓" (confirmed). Without it: "R?" (possible only).
"""
from __future__ import annotations

import caution
import exits

DEFAULTS = dict(sweep_lookback=20, pin_ratio=2.0, ext_atr=2.5, vol_mult=2.0, min_score=60.0, confirm_bars=3,
                ema_len=20, near_atr=0.5, swing=3, eq_tol=0.15, eq_lookback=60, asia_end_utc=7)
SWEEP_PTS = {"EQ": 35, "PD": 30, "AS": 30, "SW": 25, "RG": 25, "FB": 25}
CANDLE_PTS = {"ES": 25, "3O": 25, "ENG": 20, "PIN": 20, "DC": 15, "TWZ": 15, "HAR": 10, "DOJ": 5}


def _ema(vals, n):
    out, k, e = [], 2 / (n + 1), None
    for v in vals:
        e = v if e is None else v * k + e * (1 - k)
        out.append(e)
    return out


class Detector:
    """Precompute once for a list of bars, then evaluate(i, side) for any closed bar i."""

    def __init__(self, bars, p=None, cz=None, utc_offset=0):
        self.p = dict(DEFAULTS)
        self.p.update(p or {})
        self.bars = bars
        self.cz = cz or caution.Context(bars)
        self.ema = _ema([b.c for b in bars], int(self.p["ema_len"]))
        self.atr = self.cz.atr
        n = len(bars)
        # volume average (previous 20 bars)
        self.vavg = [None] * n
        s = 0.0
        for k in range(n):
            s += bars[k].v
            if k >= 20:
                s -= bars[k - 20].v
            if k >= 19:
                self.vavg[k] = s / 20
        # average body (20)
        self.bavg = [None] * n
        s = 0.0
        for k in range(n):
            s += abs(bars[k].c - bars[k].o)
            if k >= 20:
                s -= abs(bars[k - 20].c - bars[k - 20].o)
            if k >= 19:
                self.bavg[k] = s / 20
        # swing pivots: (index, price); a pivot at j is known only at j + swing
        k = int(self.p["swing"])
        self.piv_hi, self.piv_lo = [], []
        for j in range(k, n - k):
            seg = bars[j - k:j + k + 1]
            if bars[j].h >= max(x.h for x in seg):
                self.piv_hi.append((j, bars[j].h))
            if bars[j].l <= min(x.l for x in seg):
                self.piv_lo.append((j, bars[j].l))
        # today's completed Asian range (UTC 00:00 - asia_end_utc) for every bar after it
        off = int(utc_offset or 0) * 3600
        self.asia_hi, self.asia_lo = [None] * n, [None] * n
        day, rng, done = None, None, False
        end_h = int(self.p["asia_end_utc"])
        for k2, b in enumerate(bars):
            tu = b.t - off
            d, h = tu // 86400, (tu % 86400) // 3600
            if d != day:
                day, rng, done = d, None, False
            if h < end_h:
                rng = [max(rng[0], b.h), min(rng[1], b.l)] if rng else [b.h, b.l]
            else:
                done = rng is not None
                if done:
                    self.asia_hi[k2], self.asia_lo[k2] = rng

    # ------------------------------------------------------------------ helpers
    def _pivots_before(self, piv, i):
        k = int(self.p["swing"])
        lb = int(self.p["eq_lookback"])
        return [(j, px) for j, px in piv if j + k < i and j >= i - lb]

    def _sweeps(self, i, side):
        """List of (code, text) liquidity sweeps on bar i for a reversal in `side` (-1 top, +1 bottom)."""
        p, bars = self.p, self.bars
        b = bars[i]
        a = self.atr[i] or (b.h - b.l) or 1.0
        out = []
        took = (lambda lvl: b.h > lvl and b.c < lvl) if side == -1 else (lambda lvl: b.l < lvl and b.c > lvl)
        hi_word, name = ("high", "H") if side == -1 else ("low", "L")
        piv = self._pivots_before(self.piv_hi if side == -1 else self.piv_lo, i)
        # equal highs / lows
        best_eq = None
        for x in range(len(piv)):
            for y in range(x + 1, len(piv)):
                if abs(piv[x][1] - piv[y][1]) <= p["eq_tol"] * a:
                    lvl = max(piv[x][1], piv[y][1]) if side == -1 else min(piv[x][1], piv[y][1])
                    between = bars[piv[y][0] + 1:i]
                    intact = all((z.h <= lvl) if side == -1 else (z.l >= lvl) for z in between)
                    if intact and took(lvl):
                        best_eq = lvl
        if best_eq is not None:
            out.append(("EQ", f"swept equal {hi_word}s {best_eq:.2f} (resting stops)"))
        # previous day
        pd = self.cz.pdh[i] if side == -1 else self.cz.pdl[i]
        if pd is not None and took(pd):
            out.append(("PD", f"swept yesterday's {hi_word} {pd:.2f}"))
        # Asian range
        asr = self.asia_hi[i] if side == -1 else self.asia_lo[i]
        if asr is not None and took(asr):
            out.append(("AS", f"swept the Asia session {hi_word} {asr:.2f}"))
        # latest intact swing
        if piv:
            j, lvl = piv[-1]
            between = bars[j + 1:i]
            if all((z.h <= lvl) if side == -1 else (z.l >= lvl) for z in between) and took(lvl):
                out.append(("SW", f"swept the swing {hi_word} {lvl:.2f}"))
        # range high / low
        L = int(p["sweep_lookback"])
        prior = bars[i - L:i]
        lvl = max(x.h for x in prior) if side == -1 else min(x.l for x in prior)
        if took(lvl):
            out.append(("RG", f"swept the {L}-bar {hi_word} {lvl:.2f}"))
        # failed breakout: previous bar CLOSED beyond the range, this one closes back inside
        if i - 1 - L >= 0:
            prior2 = bars[i - 1 - L:i - 1]
            lvl2 = max(x.h for x in prior2) if side == -1 else min(x.l for x in prior2)
            pb = bars[i - 1]
            if (side == -1 and pb.c > lvl2 and b.c < lvl2) or (side == 1 and pb.c < lvl2 and b.c > lvl2):
                out.append(("FB", f"failed breakout {'above' if side == -1 else 'below'} {lvl2:.2f} (closed back inside)"))
        return out

    def _candles(self, i, side):
        """List of (code, text) reversal candle patterns ending at bar i."""
        bars, p = self.bars, self.p
        b, b1, b2 = bars[i], bars[i - 1], bars[i - 2]
        body = lambda x: abs(x.c - x.o)
        rng = lambda x: (x.h - x.l) or 1e-9
        bull = lambda x: x.c > x.o
        bear = lambda x: x.c < x.o
        avg = self.bavg[i - 1] or body(b1) or 1e-9
        a = self.atr[i] or rng(b)
        out = []
        if side == -1:
            # evening star
            if bull(b2) and body(b2) >= avg and body(b1) <= 0.35 * body(b2) and bear(b) and b.c < (b2.o + b2.c) / 2:
                out.append(("ES", "evening star"))
            # three outside down: b1 engulfed b2 (bearish), b closes lower
            if bull(b2) and bear(b1) and b1.o >= b2.c and b1.c <= b2.o and body(b1) > body(b2) and b.c < b1.c:
                out.append(("3O", "three outside down"))
            if bear(b) and bull(b1) and b.o >= b1.c and b.c <= b1.o and body(b) > body(b1):
                out.append(("ENG", "bearish engulfing"))
            uw = b.h - max(b.o, b.c)
            if uw >= p["pin_ratio"] * max(body(b), 1e-9) and uw >= 0.55 * rng(b):
                out.append(("PIN", "shooting star / bearish pin bar"))
            if bull(b1) and body(b1) >= 0.6 * avg and bear(b) and b.o >= b1.c - 0.05 * a \
                    and (b1.o + b1.c) / 2 > b.c > b1.o:
                out.append(("DC", "dark cloud cover"))
            if bull(b1) and bear(b) and abs(b.h - b1.h) <= 0.1 * a:
                out.append(("TWZ", "tweezer top"))
            if bull(b1) and body(b1) >= avg and bear(b) and max(b.o, b.c) <= b1.c and min(b.o, b.c) >= b1.o \
                    and body(b) <= 0.5 * body(b1):
                out.append(("HAR", "bearish harami"))
        else:
            if bear(b2) and body(b2) >= avg and body(b1) <= 0.35 * body(b2) and bull(b) and b.c > (b2.o + b2.c) / 2:
                out.append(("ES", "morning star"))
            if bear(b2) and bull(b1) and b1.o <= b2.c and b1.c >= b2.o and body(b1) > body(b2) and b.c > b1.c:
                out.append(("3O", "three outside up"))
            if bull(b) and bear(b1) and b.o <= b1.c and b.c >= b1.o and body(b) > body(b1):
                out.append(("ENG", "bullish engulfing"))
            lw = min(b.o, b.c) - b.l
            if lw >= p["pin_ratio"] * max(body(b), 1e-9) and lw >= 0.55 * rng(b):
                out.append(("PIN", "hammer / bullish pin bar"))
            if bear(b1) and body(b1) >= 0.6 * avg and bull(b) and b.o <= b1.c + 0.05 * a \
                    and (b1.o + b1.c) / 2 < b.c < b1.o:
                out.append(("DC", "piercing line"))
            if bear(b1) and bull(b) and abs(b.l - b1.l) <= 0.1 * a:
                out.append(("TWZ", "tweezer bottom"))
            if bear(b1) and body(b1) >= avg and bull(b) and max(b.o, b.c) <= b1.o and min(b.o, b.c) >= b1.c \
                    and body(b) <= 0.5 * body(b1):
                out.append(("HAR", "bullish harami"))
        if body(b) <= 0.1 * rng(b) and not out:
            out.append(("DOJ", "doji (indecision)"))
        return out

    # ------------------------------------------------------------------ main
    def evaluate(self, i, side):
        """side = -1 bearish reversal (top) / +1 bullish reversal (bottom). -> dict or None"""
        p, bars = self.p, self.bars
        if i < max(30, int(p["sweep_lookback"]) + 3) or i >= len(bars):
            return None
        b = bars[i]
        a = self.atr[i] or (b.h - b.l) or 1.0
        pts, why, tags = 0, [], []
        sw = self._sweeps(i, side)
        if sw:
            best = max(SWEEP_PTS[c] for c, _ in sw)
            pts += min(40, best + 5 * (len(sw) - 1))
            why += [t for _, t in sw]
            c0 = max(sw, key=lambda x: SWEEP_PTS[x[0]])[0]
            tags.append(c0 if c0 == "FB" else c0 + ("H" if side == -1 else "L"))
        cd = self._candles(i, side)
        if cd:
            best = max(CANDLE_PTS[c] for c, _ in cd)
            pts += min(30, best + 5 * (len(cd) - 1))
            why += [t for _, t in cd]
            c0 = max(cd, key=lambda x: CANDLE_PTS[x[0]])[0]
            tags.append({"ES": "ES" if side == -1 else "MS", "DC": "DC" if side == -1 else "PL"}.get(c0, c0))
        trig = bool(sw) or any(c != "DOJ" for c, _ in cd)
        d = self.cz._div(i, -side)                 # divergence against the current move
        if d:
            pts += 20
            trig = True
            why.append(d)
            tags.append("DIV")
        r = self.cz.rsi[i]
        if r is not None and ((side == -1 and r >= self.cz.p["rsi_hi"]) or (side == 1 and r <= self.cz.p["rsi_lo"])):
            pts += 10
            why.append(f"RSI {r:.0f} {'overbought' if side == -1 else 'oversold'}")
        if a and (b.c - self.ema[i]) * -side >= p["ext_atr"] * a:
            pts += 10
            why.append(f"stretched {abs(b.c - self.ema[i]) / a:.1f}×ATR from EMA{int(p['ema_len'])}")
        va = self.vavg[i - 1]
        if va and b.v >= p["vol_mult"] * va:
            pts += 10
            why.append(f"climax volume {b.v / va:.1f}× average")
        near = p["near_atr"] * a
        ext = b.h if side == -1 else b.l
        for o in exits.active_obs(self.cz.obs, i):
            if o["side"] == side and o["lo"] - near <= ext <= o["hi"] + near:
                pts += 10
                why.append(f"at a {'bearish' if side == -1 else 'bullish'} order block {o['lo']:.2f}-{o['hi']:.2f}")
                break
        pts = min(100, pts)
        if not trig or pts < p["min_score"]:
            return None
        # ---- confirmation (later closed bars)
        conf, conf_t, conf_how = False, None, ""
        last_closed = len(bars) - 2
        for k in range(i + 1, min(last_closed, i + int(p["confirm_bars"])) + 1):
            c = bars[k]
            if (side == -1 and c.c < b.l) or (side == 1 and c.c > b.h):
                conf, conf_t, conf_how = True, c.t, "close through the reversal bar (CHoCH)"
                break
            strong = abs(c.c - c.o) >= 0.5 * ((c.h - c.l) or 1e-9)
            if k == i + 1 and strong and (c.c - c.o) * side > 0 and \
                    ((side == -1 and c.c < min(b.o, b.c)) or (side == 1 and c.c > max(b.o, b.c))):
                conf, conf_t, conf_how = True, c.t, "confirmation candle"
                break
        return dict(t=b.t, i=i, side=side, score=pts, why=why, tags=tags, level=ext, confirmed=conf,
                    confirm_t=conf_t, confirm_how=conf_how,
                    pending=(not conf and i + int(p["confirm_bars"]) > last_closed))

    def scan(self, start, end=None):
        """All reversals in bars[start:end] (closed bars only), oldest first. A top and a bottom on the same bar:
        keep the stronger."""
        end = len(self.bars) - 1 if end is None else end
        out = []
        for i in range(max(0, start), end):
            dn, up = self.evaluate(i, -1), self.evaluate(i, 1)
            best = max((x for x in (dn, up) if x), key=lambda x: x["score"], default=None)
            if best:
                out.append(best)
        return out


def recent(bars, side, within=3, p=None, cz=None, utc_offset=0):
    """Fresh reversal AGAINST a trade in `side` in the last `within` closed bars, or None."""
    det = Detector(bars, p, cz, utc_offset)
    n = len(bars)
    for i in range(n - 2, max(0, n - 2 - within), -1):
        r = det.evaluate(i, -side)
        if r:
            return r
    return None
