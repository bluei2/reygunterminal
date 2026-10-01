"""Order Blocks: the last opposite candle before a break of structure. Bullish OB (green) = support where a BUY's
stop goes under; bearish OB (red) = resistance, a BUY's target / a SELL's stop above. Active until price closes
through it; broken ones are shown faded for a while."""
import exits
from plugin_api import Indicator, Level, Overlay, Zone


class OrderBlocks(Indicator):
    name = "Order Blocks"
    description = "Last opposite candle before a BOS · active until closed through · TP targets / SL shelters"
    category = "TP/SL"
    purpose = "Target / SL mula sa order block"
    params = {"swing": 3, "use_body": False, "show_active": 6, "show_broken": 4, "max_dist": 25.0}

    def compute(self, bars, tf, ctx):
        p = self.params
        obs = exits.order_blocks(bars, swing=int(p["swing"]), use_body=bool(p["use_body"]))
        n = len(bars)
        active = [o for o in obs if o["broken"] is None]
        broken = [o for o in obs if o["broken"] is not None]
        price = bars[-1].c if bars else 0
        # the active blocks closest to price matter most
        near = lambda o: abs((o["lo"] + o["hi"]) / 2 - price)
        active = [o for o in active if p["max_dist"] <= 0 or near(o) <= p["max_dist"]]
        active = sorted(active, key=near)[:int(p["show_active"])]
        zones, levels = [], []
        for o in broken[-int(p["show_broken"]):]:
            zones.append(Zone(o["lo"], o["hi"], o["t"], "#1f2937", t_to=bars[o["broken"]].t,
                              label=("bull" if o["side"] == 1 else "bear") + " OB ✗", border="#475569"))
        for o in active:
            bull = o["side"] == 1
            zones.append(Zone(o["lo"], o["hi"], o["t"], "#0f3d2a" if bull else "#4a1420", t_to=None,
                              label="Bull OB" if bull else "Bear OB", border="#22c55e" if bull else "#f43f5e"))
        return Overlay(zones=zones, levels=levels)
