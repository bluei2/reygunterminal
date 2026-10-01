"""Draws the built-in SMC analyser on the chart: swings, BOS/CHoCH, sweeps, FVG."""
from engine import analyze
from plugin_api import Indicator, Level, Marker, Overlay, Zone

BULL, BEAR = "#55ff55", "#ff5555"


class SMCOverlay(Indicator):
    name = "SMC Structure"
    description = "SH/SL, B/C/x markers, FVG zones · adds liquidity sweeps + FVG to the analyser"
    category = "SMC"
    purpose = "Structure at liquidity"
    analyser_module = "liquidity"

    def compute(self, bars, tf, ctx):
        st = analyze(tf, bars, n=3 if tf in ("H4", "H1") else 2)
        ov = Overlay()
        if st.sh:
            ov.levels.append(Level(st.sh.level, BEAR, "SH"))
        if st.sl:
            ov.levels.append(Level(st.sl.level, BULL, "SL"))
        if st.fvg_bull:
            ov.zones.append(Zone(st.fvg_bull.lo, st.fvg_bull.hi, st.fvg_bull.t, "#004400"))
        if st.fvg_bear:
            ov.zones.append(Zone(st.fvg_bear.lo, st.fvg_bear.hi, st.fvg_bear.t, "#440000"))
        for e in st.events[-80:]:
            if e.kind == "SWEEP":
                ov.markers.append(Marker(e.t, "above" if e.direction == -1 else "below", "x",
                                         "bold black on #ffff55"))
            elif e.kind in ("BOS", "CHoCH"):
                ch = "B" if e.kind == "BOS" else "C"
                if e.direction == 1:
                    ov.markers.append(Marker(e.t, "below", ch, f"bold black on {BULL}"))
                else:
                    ov.markers.append(Marker(e.t, "above", ch, f"bold white on {BEAR}"))
        return ov
