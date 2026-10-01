"""ATR Exits: volatility-based stop, targets and trailing stop.
  * Chandelier trailing stop (long line under price / short line over price): highest high of `lookback` bars
    minus mult x ATR (long), lowest low plus mult x ATR (short). Where price closes through it the trend flips.
  * For the current price: SL at atr_sl x ATR, TP1 / TP2 at atr_tp1 / atr_tp2 x ATR, for BUY and SELL."""
import exits
from plugin_api import Indicator, Level, Line, Overlay


class ATRExits(Indicator):
    name = "ATR Exits (chandelier)"
    description = ("Chandelier trailing stop (HH - 3×ATR / LL + 3×ATR) · ATR SL / TP1 / TP2 for the current price · "
                   "side auto (analyser bias, else trail trend) | trend | buy | sell | both")
    category = "TP/SL"
    purpose = "SL, TP at trailing mula sa ATR"
    params = {"atr_len": 14, "lookback": 22, "trail_mult": 3.0, "atr_sl": 1.5, "atr_tp1": 2.0, "atr_tp2": 3.0,
              "side": "auto", "show_levels": True}

    def compute(self, bars, tf, ctx):
        p = self.params
        n = len(bars)
        if n < int(p["atr_len"]) + 5:
            return Overlay()
        a = exits.atr(bars, int(p["atr_len"]))
        lb, m = int(p["lookback"]), p["trail_mult"]
        long_s = [exits.chandelier(bars, a, 1, i, lb, m) for i in range(n)]
        short_s = [exits.chandelier(bars, a, -1, i, lb, m) for i in range(n)]
        # which side is "in force": flips when price closes through the active stop
        trend, up, dn = 1, [None] * n, [None] * n
        for i in range(n):
            if long_s[i] is None:
                continue
            if trend == 1 and bars[i].c < long_s[i]:
                trend = -1
            elif trend == -1 and bars[i].c > short_s[i]:
                trend = 1
            if trend == 1:
                up[i] = long_s[i]
            else:
                dn[i] = short_s[i]
        levels = []
        price, atr_now = bars[-1].c, a[-1]
        side = str(p["side"]).lower()
        bias = getattr(ctx.read, "bias", 0) if ctx.read is not None else 0
        auto = bias or trend                           # analyser bias first, else the chandelier trend
        sides = [1, -1] if side == "both" else [1] if side == "buy" else [-1] if side == "sell" else \
            [trend] if side == "trend" else [auto]
        for sd in (sides if p["show_levels"] else []):
            tag = "B" if sd == 1 else "S"
            levels += [Level(price - sd * p["atr_sl"] * atr_now, "#fb7185" if sd == 1 else "#4ade80", f"{tag}SL"),
                       Level(price + sd * p["atr_tp1"] * atr_now, "#22c55e" if sd == 1 else "#f43f5e", f"{tag}TP1"),
                       Level(price + sd * p["atr_tp2"] * atr_now, "#22c55e" if sd == 1 else "#f43f5e", f"{tag}TP2")]
        return Overlay(lines=[Line(up, "#34d399", f"ATR trail L {m:g}×", width=2),
                              Line(dn, "#f87171", f"ATR trail S {m:g}×", width=2, style="step")],
                       levels=levels)
