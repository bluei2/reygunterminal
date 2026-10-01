"""Volume histogram (tick volume) colored by candle direction, with a moving average."""
from plugin_api import Hist, Indicator, Line, Overlay, sma, volumes


class Volume(Indicator):
    name = "Volume"
    description = "Tick volume bars + moving average (no vote)"
    category = "Volume"
    purpose = "Dami ng galaw"
    params = {"ma_length": 20}

    def compute(self, bars, tf, ctx):
        v = volumes(bars)
        cols = ["#1f9e78" if b.c >= b.o else "#b8435a" for b in bars]
        n = self.params["ma_length"]
        return Overlay(hists=[Hist(v, cols, "Vol", pane="Volume")],
                       lines=[Line(sma(v, n), "#fbbf24", f"Vol SMA{n}", pane="Volume", width=1)])
