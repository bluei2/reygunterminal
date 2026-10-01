"""
Automatic trend lines and their breakouts (no look-ahead: a swing point is only used once it is confirmed).

LINES
  Resistance (falling)  through 2+ swing highs, each lower than the one before -> a BUY breakout when price closes above
  Support (rising)      through 2+ swing lows, each higher than the one before  -> a SELL breakout when price closes below
  A line is valid only when no bar CLOSED beyond it between its first touch and now. More touches = stronger line.
  When a new swing forms, the newest valid line replaces the old one (the line the market is respecting now).

BREAKOUT  a close beyond the line by at least `buffer` (break_atr x ATR, min buffer_pips).
CONFIRMATION (after the break, `confirm_bars` closed bars)
  Strong close      the breakout candle's body >= 50% of its range, closing in the breakout direction   +20
  Volume            breakout bar tick volume >= vol_mult x the 20-bar average                            +15
  Follow-through    the next bar closes further in the breakout direction                                +15
  Retest held       within `retest_bars`, price comes back to the line and closes on the breakout side  +20
  Touches           2 touches +20, 3 or more +30
  FAILED            a close back on the wrong side of the line before confirmation -> breakout failed
Score >= min_score and not failed -> "TL✓" (confirmed); otherwise "TL?".

TREND CHANGE (trend_change): a CONFIRMED trend line break (close outside the line) followed, within `tc_bars`, by a
structure break in the same direction: a close above the latest swing high (after breaking a falling resistance) /
below the latest swing low (after breaking a rising support) = CHoCH, while price has not closed back through the
line. A higher low (lower high) after the break is added as extra proof.
"""
from __future__ import annotations

import exits

DEFAULTS = dict(swing=4, min_gap=6, max_span=200, touch_atr=0.2, break_atr=0.1, buffer_pips=5.0, pip=0.10,
                vol_mult=1.5, confirm_bars=3, retest_bars=12, min_score=55.0, max_age=250, tc_bars=24)


def pivots(bars, k):
    hi, lo = [], []
    for j in range(k, len(bars) - k):
        seg = bars[j - k:j + k + 1]
        if bars[j].h >= max(x.h for x in seg):
            hi.append(j)
        if bars[j].l <= min(x.l for x in seg):
            lo.append(j)
    return hi, lo


class Finder:
    def __init__(self, bars, p=None):
        self.p = dict(DEFAULTS)
        self.p.update(p or {})
        self.bars = bars
        self.atr = exits.atr(bars, 14)
        n = len(bars)
        self.vavg = [None] * n
        s = 0.0
        for k in range(n):
            s += bars[k].v
            if k >= 20:
                s -= bars[k - 20].v
            if k >= 19:
                self.vavg[k] = s / 20
        self.piv_hi, self.piv_lo = pivots(bars, int(self.p["swing"]))

    # ------------------------------------------------------------------ line building
    def _line(self, side, anchors, i_now):
        """side -1 = resistance (falling highs), +1 = support (rising lows). anchors = pivot indexes known at i_now.
        Returns the best valid line through the newest anchor, or None."""
        p, bars = self.p, self.bars
        if len(anchors) < 2:
            return None
        b = anchors[-1]
        px = (lambda j: bars[j].h) if side == -1 else (lambda j: bars[j].l)
        best = None
        for a in reversed(anchors[:-1]):
            if b - a < p["min_gap"]:
                continue
            if b - a > p["max_span"]:
                break
            pa, pb = px(a), px(b)
            if (side == -1 and pb >= pa) or (side == 1 and pb <= pa):
                continue                                   # must fall (resistance) / rise (support)
            slope = (pb - pa) / (b - a)
            y = lambda x, pa=pa, a=a, slope=slope: pa + slope * (x - a)
            # no close beyond the line from a to i_now
            ok = all((bars[x].c <= y(x)) if side == -1 else (bars[x].c >= y(x)) for x in range(a, i_now + 1))
            if not ok:
                continue
            tol = p["touch_atr"] * (self.atr[b] or 1.0)
            touches = sum(1 for j in anchors if a <= j <= b and abs(px(j) - y(j)) <= tol)
            cand = dict(side=side, a=a, b=b, pa=pa, slope=slope, touches=max(2, touches), span=b - a)
            if best is None or (cand["touches"], cand["span"]) > (best["touches"], best["span"]):
                best = cand
        return best

    @staticmethod
    def y(line, x):
        return line["pa"] + line["slope"] * (x - line["a"])

    # ------------------------------------------------------------------ scan
    def scan(self, start=0):
        """Walk the closed bars; returns (events, active_lines). Each event = one breakout (dict)."""
        p, bars = self.p, self.bars
        n = len(bars)
        k = int(self.p["swing"])
        last_closed = n - 2
        active = {-1: None, 1: None}
        events = []
        hi_i = lo_i = 0                                    # pointers into the pivot lists
        known_hi, known_lo = [], []
        for i in range(max(k * 2, start), last_closed + 1):
            # pivots that became known at bar i (pivot at j is confirmed at j + k)
            new_hi = new_lo = False
            while hi_i < len(self.piv_hi) and self.piv_hi[hi_i] + k <= i:
                known_hi.append(self.piv_hi[hi_i]); hi_i += 1; new_hi = True
            while lo_i < len(self.piv_lo) and self.piv_lo[lo_i] + k <= i:
                known_lo.append(self.piv_lo[lo_i]); lo_i += 1; new_lo = True
            if new_hi:
                ln = self._line(-1, known_hi, i - 1)
                if ln:
                    active[-1] = ln
            if new_lo:
                ln = self._line(1, known_lo, i - 1)
                if ln:
                    active[1] = ln
            a_ = self.atr[i] or 1.0
            buf = max(p["break_atr"] * a_, p["buffer_pips"] * p["pip"])
            for lside in (-1, 1):
                ln = active[lside]
                if ln is None:
                    continue
                if i - ln["b"] > p["max_age"]:
                    active[lside] = None
                    continue
                yv = self.y(ln, i)
                bside = -lside                              # resistance breaks UP (+1), support breaks DOWN (-1)
                if (bars[i].c - yv) * bside > buf:
                    events.append(self._event(ln, i, bside, yv))
                    active[lside] = None
        return events, [dict(l, end=last_closed) for l in active.values() if l]

    def _event(self, ln, i, side, yv):
        p, bars = self.p, self.bars
        b = bars[i]
        n = len(bars)
        last_closed = n - 2
        pts, why, against = 0, [], []
        why.append(f"close {'above' if side == 1 else 'below'} the {'falling resistance' if side == 1 else 'rising support'} "
                   f"line ({yv:.2f})")
        t_pts = 30 if ln["touches"] >= 3 else 20
        pts += t_pts
        why.append(f"line with {ln['touches']} touches over {ln['span']} bars")
        rng = (b.h - b.l) or 1e-9
        if abs(b.c - b.o) >= 0.5 * rng and (b.c - b.o) * side > 0:
            pts += 20
            why.append("strong breakout candle (body ≥ 50%)")
        va = self.vavg[i - 1]
        if va and b.v >= p["vol_mult"] * va:
            pts += 15
            why.append(f"breakout volume {b.v / va:.1f}× average")
        failed, conf_t, retest_t = False, None, None
        if pts >= p["min_score"]:
            conf_t = b.t                                   # already enough at the breakout close
        tol = p["touch_atr"] * (self.atr[i] or 1.0)
        for k in range(i + 1, min(last_closed, i + int(p["retest_bars"])) + 1):
            c = bars[k]
            yk = self.y(ln, k)
            if (c.c - yk) * side < 0:
                failed = True
                against.append(f"FAILED: closed back {'below' if side == 1 else 'above'} the line ({c.c:.2f})")
                break
            if k == i + 1 and (c.c - b.c) * side > 0:
                pts += 15
                why.append("follow-through: next bar closed further")
            touch = (c.l <= yk + tol) if side == 1 else (c.h >= yk - tol)
            if retest_t is None and touch and (c.c - yk) * side > 0:
                retest_t = c.t
                pts += 20
                why.append(f"retest held at the line ({yk:.2f})")
            if conf_t is None and pts >= p["min_score"]:
                conf_t = c.t                               # confirmed on this bar (no look-ahead)
        pts = min(100, pts)
        confirmed = conf_t is not None and not (failed and conf_t is None)
        if conf_t is None and not failed:
            against.append("waiting for confirmation (follow-through / retest)" if i + int(p["retest_bars"]) > last_closed
                           else "never confirmed")
        elif conf_t and conf_t != b.t:
            why.append("CONFIRMED on a later bar")
        # line drawing points
        return dict(t=b.t, i=i, side=side, level=yv, line=ln, score=pts, why=why, against=against,
                    failed=failed, confirmed=confirmed, retest_t=retest_t, confirm_t=conf_t, touches=ln["touches"])

    # ------------------------------------------------------------------ trend change
    def trend_change(self, ev):
        """For one breakout event: the bar where the trend change is confirmed (CHoCH after the TL break), or None.
        Only closed bars are used; the swing used for the CHoCH must be known on that bar."""
        if not ev["confirmed"]:
            return None
        p, bars = self.p, self.bars
        k = int(p["swing"])
        side, i, ln = ev["side"], ev["i"], ev["line"]
        last_closed = len(bars) - 2
        start = next((x for x in range(i, len(bars)) if bars[x].t == ev["confirm_t"]), i)
        piv_opp = self.piv_hi if side == 1 else self.piv_lo      # swing to break (CHoCH)
        piv_same = self.piv_lo if side == 1 else self.piv_hi     # higher low / lower high
        for x in range(start, min(last_closed, i + int(p["tc_bars"])) + 1):
            c = bars[x]
            if (c.c - self.y(ln, x)) * side < 0:
                return None                                         # closed back through the line: no trend change
            known = [j for j in piv_opp if j + k <= x and j < x]
            if not known:
                continue
            j = known[-1]
            lvl = bars[j].h if side == 1 else bars[j].l
            if (c.c - lvl) * side > 0:
                why = [f"trend line break {'up' if side == 1 else 'down'} ({ev['touches']} touches) + close "
                       f"{'above the swing high' if side == 1 else 'below the swing low'} {lvl:.2f} (CHoCH)"]
                # higher low / lower high since the break
                before = [bars[q].l if side == 1 else bars[q].h for q in piv_same if q < i][-1:]
                after = [bars[q].l if side == 1 else bars[q].h for q in piv_same if i <= q and q + k <= x]
                if before and after and (after[-1] - before[0]) * side > 0:
                    why.append(f"{'higher low' if side == 1 else 'lower high'} {after[-1]:.2f} formed after the break")
                return dict(t=c.t, i=x, side=side, level=lvl, t_break=ev["t"], why=why, bars_after=x - i)
        return None
