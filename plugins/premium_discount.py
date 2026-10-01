"""
Premium / Discount zones of the H1 dealing range (huling H1 swing high hanggang swing low).
Kapag naka-on ito, binibilang ng analyser kung nasa tamang side ang presyo:
discount para sa long, premium para sa short.
"""
from plugin_api import Indicator, Level, Overlay, Zone

RANGE_TF = "H1"


class PremiumDiscount(Indicator):
    name = "Premium / Discount"
    description = "H1 dealing range zones + equilibrium · adds location to the analyser score"
    category = "SMC"
    purpose = "Location sa range"
    analyser_module = "premium_discount"

    def compute(self, bars, tf, ctx):
        ov = Overlay()
        st = ctx.smc.get(RANGE_TF)
        if not st or not st.sh or not st.sl or st.sh.level <= st.sl.level or not bars:
            return ov
        hi, lo = st.sh.level, st.sl.level
        eq = (hi + lo) / 2
        t0 = max(min(st.sh.t, st.sl.t), bars[0].t)
        ov.zones.append(Zone(eq, hi, t0, "#fb923c", label="PREMIUM", border="#fb923c"))
        ov.zones.append(Zone(lo, eq, t0, "#60a5fa", label="DISCOUNT", border="#60a5fa"))
        ov.levels.append(Level(eq, "#cbd5e1", "EQ"))
        return ov
