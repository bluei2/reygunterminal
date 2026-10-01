"""ATR in its own panel (volatility, useful for SL sizing)."""
from plugin_api import Indicator, Line, Overlay, atr


class ATRPane(Indicator):
    name = "ATR"
    description = "Average True Range (no vote)"
    category = "Volatility"
    purpose = "Laki ng galaw, pang-SL"
    params = {"length": 14}

    def compute(self, bars, tf, ctx):
        n = self.params["length"]
        return Overlay(lines=[Line(atr(bars, n), "#f472b6", f"ATR {n}", pane="ATR")])
