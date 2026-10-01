"""Auto Fibonacci retracement of the current H1 dealing range (last H1 swing high / swing low)."""
from plugin_api import Indicator, Level, Overlay

RATIOS = [0.0, 0.236, 0.382, 0.5, 0.618, 0.705, 0.786, 1.0]


class AutoFib(Indicator):
    name = "Auto Fibonacci (H1)"
    description = "Retracement of the last H1 swing · measured in the H1 trend direction"
    category = "Levels"
    purpose = "Retracement levels"

    def compute(self, bars, tf, ctx):
        st = ctx.smc.get("H1")
        if not st or not st.sh or not st.sl or st.sh.level <= st.sl.level:
            return Overlay()
        hi, lo = st.sh.level, st.sl.level
        ov = Overlay()
        for r in RATIOS:
            # bullish leg: 0 at the high, retrace down toward the low; bearish leg: the reverse
            price = hi - (hi - lo) * r if st.trend >= 0 else lo + (hi - lo) * r
            col = "#fbbf24" if r in (0.618, 0.705) else "#94a3b8"
            ov.levels.append(Level(price, col, f"{r:g}"))
        return ov
