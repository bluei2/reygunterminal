"""
TEMPLATE - kopyahin ito, i-rename (tanggalin ang "_" sa unahan), tapos i-edit.
Halimbawa: copy _template.py my_rsi.py

Pagkatapos, i-restart ang terminal at press "p" para i-on.
"""
from plugin_api import (Indicator, Level, Line, Marker, Overlay, Signal, Strategy, Vote, Zone,
                        closes, ema, sma)


class MyIndicator(Indicator):
    name = "My Indicator"
    description = "What it draws"
    category = "Trend"            # group (LAYERS + scoring): Trend, Momentum, Volatility, Volume, Levels, SMC, Time
    purpose = "Direksyon ng trend"  # short "para saan" tag shown next to the name
    params = {"length": 100}      # editable in SuperChart (⚙). Read with self.params["length"]
    vote_tf = "M15"               # timeframe used by vote() (also editable)

    def compute(self, bars, tf, ctx):
        # bars: list of Bar(t, o, h, l, c, v) on the chart TF. Last bar is still forming.
        n = self.params["length"]
        line = sma(closes(bars), n)                 # one value per bar
        ov = Overlay(lines=[Line(line, "#55ffff", f"SMA{n}")])
        # own panel below the chart instead:  Line(values, color, label, pane="MyOsc")
        # ov.levels.append(Level(2650.0, "#ffffff", "LVL"))
        # ov.markers.append(Marker(bars[-2].t, "below", "▲", "bold #55ff55"))
        # ov.zones.append(Zone(lo, hi, bars[-20].t, "#333300"))
        return ov

    def vote(self, ctx):
        # Optional: take part in the analyser score when SCORE is on (works even when CHART is off).
        c = closes(self.vote_bars(ctx))            # closed bars of vote_tf
        m = sma(c, self.params["length"])[-1]
        if m is None:
            return None
        return Vote(1 if c[-1] > m else -1, "above SMA" if c[-1] > m else "below SMA")


class MyStrategy(Strategy):
    name = "My Strategy"
    description = "My rules"
    default_on = False

    def evaluate(self, ctx):
        # ctx.bars("M15")    -> bars for any TF (M1 M5 M15 H1 H4 D1)
        # ctx.tick           -> latest tick (bid, ask)
        # ctx.smc["H4"]      -> built-in analyser: .trend .sh .sl .fvg_bull .fvg_bear .pd .events
        # ctx.read           -> analyser verdict: .bias .score .verdict
        # Return Signal(...) when your rules hit. Same (t, direction) is alerted only once.
        return None

    def status(self, ctx):
        return ["waiting"]
