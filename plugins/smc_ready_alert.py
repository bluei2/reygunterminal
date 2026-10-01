"""Alert when the built-in analyser turns READY (confluence >= passing score, trigger, right side of range)."""
from plugin_api import Signal, Strategy


class SMCReadyAlert(Strategy):
    name = "SMC READY alert"
    description = "Signal when the analyser verdict becomes LONG/SHORT READY"

    def evaluate(self, ctx):
        r = ctx.read
        if not r or "READY" not in r.verdict:
            return None
        m5 = ctx.bars("M5")
        if not m5:
            return None
        m15 = ctx.smc.get("M15")
        sl = None
        if m15:
            sw = m15.sl if r.bias == 1 else m15.sh
            sl = sw.level if sw else None
        return Signal(m5[-1].t, r.bias, f"confluence {r.overall:.0f}% (pass {r.passing:.0f}%)", tf="M5",
                      entry=ctx.tick.bid if ctx.tick else None, sl=sl)

    def status(self, ctx):
        r = ctx.read
        return [f"{r.overall:.0f}% / pass {r.passing:.0f}%" if r and r.overall is not None else "no data"]
