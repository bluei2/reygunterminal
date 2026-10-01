"""TP/SL Map: for the current price, picks the likely TP1 / TP2 / SL / trail for a BUY and/or a SELL from the
VWAP bands, the order blocks and ATR (TP must be at least min_rr x the stop away). Levels only - nothing is traded."""
import exits
from plugin_api import Indicator, Level, Overlay, Zone


class TPSLMap(Indicator):
    name = "TP/SL Map"
    description = ("Likely TP1 / TP2 / SL / trail for the current price · source vwap | ob | atr | all "
                   "(or e.g. vwap+atr) · "
                   "side auto (analyser bias, else price vs VWAP) | buy | sell | both")
    category = "TP/SL"
    purpose = "Posibleng TP at SL ngayon"
    params = {"side": "auto", "tp_source": "all", "sl_source": "all", "min_rr": 1.0, "buffer_pips": 5.0,
              "pip": 0.10, "max_sl": 10.0, "atr_len": 14, "atr_sl": 1.5, "atr_tp1": 2.0, "atr_tp2": 3.0,
              "atr_trail": 3.0}

    def compute(self, bars, tf, ctx):
        if len(bars) < 30 or tf in ("H4", "D1"):
            return Overlay()
        p = self.params
        i = len(bars) - 1
        price = bars[-1].c
        vw, bands = exits.vwap_bands(bars)
        obs = exits.order_blocks(bars)
        av = exits.atr(bars, int(p["atr_len"]))
        akw = dict(atr_vals=av, atr_sl=p["atr_sl"], atr_tp1=p["atr_tp1"], atr_tp2=p["atr_tp2"],
                   atr_trail=p["atr_trail"], bars=bars)
        buf = p["buffer_pips"] * p["pip"]
        side = str(p["side"]).lower()
        if side == "auto":
            bias = getattr(ctx.read, "bias", 0) if ctx.read is not None else 0
            sides = [bias] if bias else [1 if vw[i] is not None and price >= vw[i] else -1]
        elif side == "both":
            sides = [1, -1]
        else:
            sides = [1 if side == "buy" else -1]
        levels, zones = [], []
        for sd in sides:
            first = exits.exit_plan(sd, price, i, vw, bands, obs, tp_source="none", sl_source=p["sl_source"],
                                    buffer=buf, min_dist=buf, max_sl=p["max_sl"], **akw)
            sl = first["sl"]
            sl_d = abs(price - sl[0]) if sl else 0.0
            plan = exits.exit_plan(sd, price, i, vw, bands, obs, tp_source=p["tp_source"], sl_source="none",
                                   buffer=buf, min_dist=max(buf, p["min_rr"] * sl_d), max_sl=p["max_sl"], **akw)
            tag = "B" if sd == 1 else "S"
            col_tp = "#22c55e" if sd == 1 else "#f43f5e"
            if sl:
                levels.append(Level(sl[0], "#fb7185" if sd == 1 else "#4ade80", f"{tag}SL"))
            for k, name in (("tp1", "TP1"), ("tp2", "TP2")):
                if plan[k]:
                    levels.append(Level(plan[k][0], col_tp, f"{tag}{name}"))
            if plan["trail"]:
                levels.append(Level(plan["trail"][0], "#fbbf24", f"{tag}TRL"))
                parts_trail = f"trail {plan['trail'][0]:.2f} ({plan['trail'][1]})"
            else:
                parts_trail = ""
            # summary box on the last bar
            parts = []
            if plan["tp1"]:
                r = f" {abs(plan['tp1'][0] - price) / sl_d:.1f}R" if sl_d else ""
                parts.append(f"TP1 {plan['tp1'][0]:.2f} ({plan['tp1'][1]}{r})")
            if plan["tp2"]:
                parts.append(f"TP2 {plan['tp2'][0]:.2f} ({plan['tp2'][1]})")
            if sl:
                parts.append(f"SL {sl[0]:.2f} ({sl[1]})")
            else:
                parts.append(f"SL: walang level sa loob ng ${p['max_sl']:g}")
            if parts_trail:
                parts.append(parts_trail)
            if plan.get("atr"):
                parts.append(f"ATR {plan['atr']:.2f}")
            if parts:
                top = max([price] + [x[0] for x in (plan["tp1"], plan["tp2"], sl) if x])
                zones.append(Zone(top, top, bars[max(0, i - 60)].t, "#000000", t_to=None,
                                  label=f"{'BUY' if sd == 1 else 'SELL'} map: " + " · ".join(parts),
                                  border=""))
        return Overlay(levels=levels, zones=zones)
