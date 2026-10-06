"""Buyers vs Sellers (order flow strength) on the price chart.

From the MT5 tick order flow (orderflow.py, tick rule): delta % of volume over the last `bars` candles.
  BUYERS ▲  (green, under the candle)  buyers took over: pressure crossed above +min_pct
  SELLERS ▼ (red, above the candle)    sellers took over: pressure crossed below -min_pct
A marker only when the side CHANGES (no clutter). The live % is on the STRATEGIES card ("ORDER FLOW").
Tick-rule order flow on a CFD shows pressure of price changes, not exchange volume. Read-only."""
import orderflow
from plugin_api import Indicator, Marker, Overlay


class FlowStrength(Indicator):
    name = "Buyers vs Sellers"
    description = ("Order flow sa chart: BUYERS ▲ kapag mas malakas ang buyers, SELLERS ▼ kapag sellers "
                   "(delta % ng volume sa huling N kandila, MT5 ticks)")
    category = "Order Flow"
    purpose = "Sino ang mas malakas"
    default_on = True
    params = {"bars": 3, "min_pct": 15, "history": 300}

    def compute(self, bars, tf, ctx):
        of = orderflow.CURRENT
        if of is None or tf not in orderflow.TFS or not bars:
            return Overlay()
        st = {x[0]: (x[1], x[2]) for x in of.bar_stats(tf)}
        n, thr = max(1, int(self.params["bars"])), float(self.params["min_pct"])
        markers, side = [], 0
        start = max(0, len(bars) - int(self.params["history"]))
        for i in range(start, len(bars)):
            b = s = 0.0
            for j in range(max(0, i - n + 1), i + 1):
                x = st.get(bars[j].t)
                if x:
                    b, s = b + x[0], s + x[1]
            if b + s <= 0:
                continue
            p = 100.0 * (b - s) / (b + s)
            now = 1 if p >= thr else -1 if p <= -thr else 0
            if now and now != side:
                markers.append(Marker(bars[i].t, "below" if now == 1 else "above",
                                      "BUYERS ▲" if now == 1 else "SELLERS ▼",
                                      "bold #22c55e" if now == 1 else "bold #f43f5e"))
            if now:
                side = now
        return Overlay(markers=markers)
