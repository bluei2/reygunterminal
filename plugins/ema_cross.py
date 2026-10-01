"""Sample strategy: M5 EMA20/50 cross, filtered by H1 above/below EMA50."""
from plugin_api import Signal, Strategy, closes, ema


class EMACrossM5(Strategy):
    name = "EMA cross M5 (H1 filter)"
    description = "Sample strategy - template for your own rules"

    def evaluate(self, ctx):
        m5, h1 = ctx.bars("M5"), ctx.bars("H1")
        if len(m5) < 60 or len(h1) < 60:
            return None
        c = closes(m5[:-1])                 # closed bars only
        f, s = ema(c, 20), ema(c, 50)
        h1c = closes(h1[:-1])
        h1_e = ema(h1c, 50)[-1]
        h1_dir = 1 if h1c[-1] > h1_e else -1
        crossed_up = f[-2] <= s[-2] and f[-1] > s[-1]
        crossed_dn = f[-2] >= s[-2] and f[-1] < s[-1]
        t = m5[-2].t
        if crossed_up and h1_dir == 1:
            return Signal(t, 1, "EMA20 x above EMA50, H1 above EMA50", tf="M5", entry=c[-1])
        if crossed_dn and h1_dir == -1:
            return Signal(t, -1, "EMA20 x below EMA50, H1 below EMA50", tf="M5", entry=c[-1])
        return None

    def status(self, ctx):
        m5 = ctx.bars("M5")
        if len(m5) < 60:
            return ["warming up"]
        c = closes(m5[:-1])
        f, s = ema(c, 20)[-1], ema(c, 50)[-1]
        return [f"M5 EMA20 {'>' if f > s else '<'} EMA50"]
