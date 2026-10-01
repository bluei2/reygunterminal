"""
Reversal-risk checks ("⚠ caution") for a trade idea. They do NOT change the score; they warn that even though the
score passed, the entry is late / stretched / right into an obstacle:

  * RSI overbought (BUY) / oversold (SELL)                       RSI(14) >= 70 / <= 30
  * Stochastic overbought / oversold                             %K(14,3) >= 80 / <= 20
  * RSI divergence                                               price higher high but RSI lower high (BUY), etc.
  * Into an order block                                          BUY with a bearish OB just above (<= near_atr x ATR)
  * Stretched from VWAP                                          BUY above VWAP +2σ / SELL below -2σ
  * Into yesterday's high / low                                  BUY just under PDH / SELL just above PDL
  * Big move already done                                        the last `run_bars` bars moved > run_atr x ATR
  * Fresh reversal against the trade                             reversal.py top (BUY) / bottom (SELL) in the last 3 bars

cautions(bars, i, side, ...) -> list of short strings (empty = clean).
"""
from __future__ import annotations

import exits

DEFAULTS = dict(rsi_len=14, rsi_hi=70.0, rsi_lo=30.0, stoch_len=14, stoch_smooth=3, stoch_hi=80.0, stoch_lo=20.0,
                near_atr=0.5, vwap_sigma=2.0, run_bars=6, run_atr=3.0, div_lookback=40, div_swing=3)


def rsi(closes, n=14):
    out = [None] * len(closes)
    if len(closes) <= n:
        return out
    gains = losses = 0.0
    for k in range(1, n + 1):
        d = closes[k] - closes[k - 1]
        gains += max(d, 0)
        losses += max(-d, 0)
    ag, al = gains / n, losses / n
    out[n] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    for k in range(n + 1, len(closes)):
        d = closes[k] - closes[k - 1]
        ag = (ag * (n - 1) + max(d, 0)) / n
        al = (al * (n - 1) + max(-d, 0)) / n
        out[k] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def stoch_k(bars, n=14, smooth=3):
    raw = [None] * len(bars)
    for k in range(n - 1, len(bars)):
        seg = bars[k - n + 1:k + 1]
        hi, lo = max(b.h for b in seg), min(b.l for b in seg)
        raw[k] = 50.0 if hi == lo else 100 * (bars[k].c - lo) / (hi - lo)
    out = [None] * len(bars)
    for k in range(len(bars)):
        seg = [x for x in raw[max(0, k - smooth + 1):k + 1] if x is not None]
        if len(seg) == smooth:
            out[k] = sum(seg) / smooth
    return out


class Context:
    """Precomputed series for one list of bars (compute once, check many bars)."""

    def __init__(self, bars, p=None):
        self.p = dict(DEFAULTS)
        self.p.update(p or {})
        self.bars = bars
        closes = [b.c for b in bars]
        self.rsi = rsi(closes, int(self.p["rsi_len"]))
        self.stoch = stoch_k(bars, int(self.p["stoch_len"]), int(self.p["stoch_smooth"]))
        self.atr = exits.atr(bars, 14)
        self.vw, self.bands = exits.vwap_bands(bars, (self.p["vwap_sigma"],))
        self.obs = exits.order_blocks(bars)
        # previous server-day high / low for every bar
        self.pdh, self.pdl = [None] * len(bars), [None] * len(bars)
        day, prev, cur = None, None, None
        for k, b in enumerate(bars):
            d = b.t // 86400
            if d != day:
                prev, day = cur, d
                cur = [b.h, b.l]
            else:
                cur[0], cur[1] = max(cur[0], b.h), min(cur[1], b.l)
            if prev:
                self.pdh[k], self.pdl[k] = prev

    def _div(self, i, side):
        """Regular divergence against the trade: BUY -> bearish div (HH price, LH RSI); SELL -> bullish div."""
        k, lb = int(self.p["div_swing"]), int(self.p["div_lookback"])
        bars, r = self.bars, self.rsi
        piv = []
        for j in range(max(k, i - lb), i - k + 1):
            if side == 1 and all(bars[j].h >= bars[x].h for x in range(j - k, j + k + 1) if x != j):
                piv.append(j)
            if side == -1 and all(bars[j].l <= bars[x].l for x in range(j - k, j + k + 1) if x != j):
                piv.append(j)
        if len(piv) < 2:
            return None
        a, b = piv[-2], piv[-1]
        if r[a] is None or r[b] is None:
            return None
        if side == 1 and bars[b].h > bars[a].h and r[b] < r[a] - 2:
            return f"bearish RSI divergence (HH price, RSI {r[a]:.0f}→{r[b]:.0f})"
        if side == -1 and bars[b].l < bars[a].l and r[b] > r[a] + 2:
            return f"bullish RSI divergence (LL price, RSI {r[a]:.0f}→{r[b]:.0f})"
        return None

    def _reversal(self, i, side, within=3):
        """A fresh reversal AGAINST this side in the last `within` bars up to i (only bars <= i are used)."""
        if not self.p.get("reversal", True):
            return None
        if getattr(self, "_rdet", None) is None:
            import reversal
            self._rdet = reversal.Detector(self.bars, None, self)
        for k in range(i, max(0, i - within), -1):
            r = self._rdet.evaluate(k, -side)
            if r:
                return (f"reversal {'top' if side == 1 else 'bottom'} {r['score']:.0f}: {r['why'][0]}"
                        + ("" if k == i else f" ({i - k} bar ago)"))
        return None

    def check(self, i, side):
        p, bars = self.p, self.bars
        out = []
        price = bars[i].c
        a = self.atr[i] or 0.0
        rv, sv = self.rsi[i], self.stoch[i]
        if rv is not None:
            if side == 1 and rv >= p["rsi_hi"]:
                out.append(f"RSI {rv:.0f} overbought")
            if side == -1 and rv <= p["rsi_lo"]:
                out.append(f"RSI {rv:.0f} oversold")
        if sv is not None:
            if side == 1 and sv >= p["stoch_hi"]:
                out.append(f"Stoch {sv:.0f} overbought")
            if side == -1 and sv <= p["stoch_lo"]:
                out.append(f"Stoch {sv:.0f} oversold")
        d = self._div(i, side)
        if d:
            out.append(d)
        if a:
            for o in exits.active_obs(self.obs, i):
                if o["side"] != -side:
                    continue
                inside = o["lo"] <= price <= o["hi"]
                dist = (o["lo"] - price) if side == 1 else (price - o["hi"])
                if inside or 0 <= dist <= p["near_atr"] * a:
                    out.append(f"{'inside' if inside else 'near'} {'bearish' if side == 1 else 'bullish'} OB "
                               f"{o['lo']:.2f}-{o['hi']:.2f}")
                    break
            pd_lvl = self.pdh[i] if side == 1 else self.pdl[i]
            if pd_lvl is not None:
                dist = (pd_lvl - price) * side
                if 0 <= dist <= p["near_atr"] * a:
                    out.append(f"just {'under PDH' if side == 1 else 'above PDL'} {pd_lvl:.2f}")
            n = int(p["run_bars"])
            if i >= n:
                move = (price - bars[i - n].c) * side
                if move > p["run_atr"] * a:
                    out.append(f"already ran {move:.2f} ({move / a:.1f}×ATR) in {n} bars")
        rv_hit = self._reversal(i, side)
        if rv_hit:
            out.append(rv_hit)
        band = self.bands.get(side * p["vwap_sigma"])
        if band is not None and band[i] is not None and (price - band[i]) * side > 0:
            out.append(f"stretched: {'above' if side == 1 else 'below'} VWAP {side * p['vwap_sigma']:+g}σ")
        return out


def cautions(bars, i, side, p=None):
    return Context(bars, p).check(i, side)
