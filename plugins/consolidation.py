"""Consolidation Box: finds tight ranges (boxes) where price is coiling. A break of the box is often the start of
the next move, and before high-impact news the box edges are where the News Trader puts its pending orders."""
import conso
from plugin_api import Indicator, Level, Overlay, Zone


class ConsolidationBox(Indicator):
    name = "Consolidation Box"
    description = "Box = min 2 tops + 2 bottoms at the same level (within N pips) · live box edges as levels"
    category = "Volatility"
    purpose = "Consolidation / range box"
    params = {"lookback": 48, "swing": 2, "min_touches": 2, "tol_pips": 5.0, "pip": 0.10, "min_bars": 8,
              "max_range": 10.0, "min_range": 0.5, "history": 12}

    def compute(self, bars, tf, ctx):
        p = self.params
        boxes = conso.scan(bars, **p)[-int(p["history"]):]
        zones, levels = [], []
        for b in boxes:
            live = b.get("live")
            zones.append(Zone(b["low"], b["high"], b["t_from"], "#3b2a6b" if live else "#262045",
                              t_to=None if live else b["t_to"],
                              label=f"BOX {b['range']:.2f} · {len(b['tops'])}T/{len(b['bottoms'])}B" + (" LIVE" if live else ""),
                              border="#a78bfa" if live else "#5b4b8a"))
            if live:
                levels += [Level(b["high"], "#a78bfa", "BXH"), Level(b["low"], "#a78bfa", "BXL")]
        return Overlay(zones=zones, levels=levels)
