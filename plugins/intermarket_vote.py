"""Intermarket: votes the bias of the markets that move with / against gold (DXY, EURUSD, silver, USDJPY, yields...)
into the score as its own group. The panel on the Overview shows every symbol; this plugin only votes."""
import intermarket
from engine import TF_SECONDS
from plugin_api import Indicator, Overlay, Vote


class IntermarketVote(Indicator):
    name = "Intermarket (DXY, EURUSD, XAG...)"
    description = ("Majority of DXY / EURUSD / XAGUSD / USDJPY / yields / indices read for gold "
                   "(dollar down = gold up) · symbols & relations: ⚙ on the Intermarket card")
    category = "Intermarket"
    purpose = "Salungat at kasabay na market"
    vote_tf = "M1"
    params = {"min_agree": 2}

    def compute(self, bars, tf, ctx):
        return Overlay()

    def vote(self, ctx):
        im = intermarket.CURRENT
        if im is None:
            return None
        m1 = ctx.bars("M1")
        if not m1:
            return None
        sec = TF_SECONDS.get(im.settings["tf"], 900)
        r = im.read(t_end=m1[-1].t - sec)          # only intermarket bars that had closed by then
        if not r["counted"]:
            return None
        diff = r["bull"] - r["bear"]
        why = f"{r['bull']} bull / {r['bear']} bear of {r['counted']}"
        if abs(diff) >= int(self.params["min_agree"]) or (r["counted"] <= 2 and diff):
            return Vote(1 if diff > 0 else -1, why)
        return Vote(0, why + " (no clear lead)")
