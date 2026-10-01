"""VWAP bands for exits: daily VWAP with +/-1, 2, 3 sigma bands. TP = next band in your direction, SL beyond
VWAP (or the band behind you), trail the stop with VWAP. Use the TP/SL Map to get the exact prices picked."""
import exits
from plugin_api import Indicator, Line, Overlay


class VWAPBandsTPSL(Indicator):
    name = "VWAP Bands (TP/SL)"
    description = "Daily VWAP + ±1/2/3σ bands · TP = next band ahead · SL beyond VWAP / band behind · trail with VWAP"
    category = "TP/SL"
    purpose = "Target at stop mula sa VWAP"
    params = {"band1": 1.0, "band2": 2.0, "band3": 3.0}

    def compute(self, bars, tf, ctx):
        if tf in ("H4", "D1"):
            return Overlay()
        p = self.params
        mults = tuple(m for m in (p["band1"], p["band2"], p["band3"]) if m > 0)
        vw, bands = exits.vwap_bands(bars, mults)
        for i in range(1, len(bars)):                     # gap at each daily reset
            if bars[i].t // 86400 != bars[i - 1].t // 86400:
                vw[i - 1] = None
                for arr in bands.values():
                    arr[i - 1] = None
        cols = {1: "#fde68a", 2: "#f59e0b", 3: "#b45309"}
        lines = [Line(vw, "#fbbf24", "VWAP", width=2)]
        for n, m in enumerate(mults, 1):
            c = cols.get(n, "#f59e0b")
            far = n >= 2                                  # outer bands don't squash the candles
            lines += [Line(bands[m], c, f"+{m:g}σ", width=1, style="dashed" if n == 3 else "line", autoscale=not far),
                      Line(bands[-m], c, f"-{m:g}σ", width=1, style="dashed" if n == 3 else "line", autoscale=not far)]
        return Overlay(lines=lines)
