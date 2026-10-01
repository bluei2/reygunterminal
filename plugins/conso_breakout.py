"""Conso Breakout: possible BUY / SELL arrows when price breaks out of a consolidation box, with the entry, SL and
target profit drawn like TradingView's long/short position tool, and whether it reached TP or SL.

SIGNALS ONLY: nothing is traded. The indicator draws the history; the strategy (same file) alerts live in
Signal Center / event log when a new breakout happens.
"""
import caution
import conso
from plugin_api import Indicator, Level, Marker, Overlay, Signal, Strategy, Zone

PARAMS = {"confirm": "close", "buffer_pips": 5.0, "rr": 2.0, "max_sl": 10.0, "sl_mode": "box", "sl_fixed": 5.0,
          "tp_source": "rr", "min_rr": 1.0, "trail": "none",
          "atr_len": 14, "atr_sl": 1.5, "atr_tp1": 2.0, "atr_tp2": 3.0, "atr_trail": 3.0,
          "ema_len": 0, "confirm_bars": 3,
          # box rule (same as the Consolidation Box indicator)
          "lookback": 48, "swing": 2, "min_touches": 2, "tol_pips": 5.0, "pip": 0.10, "min_bars": 8,
          "max_range": 10.0, "min_range": 0.5}


def _signals(bars, p):
    return conso.breakout_signals(bars, **p)


class ConsoBreakoutSignals(Indicator):
    name = "Conso Breakout Signals"
    description = ("▲ BUY / ▼ SELL when price breaks a box (2 tops + 2 bottoms) · entry, SL and TARGET drawn · "
                   "✓ TP / ✗ SL / ⇢ TRAIL result · tp_source rr|vwap|ob|atr|all (or vwap+atr) · "
                   "sl_mode box|fixed|vwap|ob|atr · trail none|vwap|atr · ema_len > 0 = trend filter")
    category = "Signals"
    purpose = "Possible buy / sell + target"
    params = dict(PARAMS, history=15)

    def compute(self, bars, tf, ctx):
        p = dict(self.params)
        hist = int(p.pop("history"))
        sigs = _signals(bars, p)
        st = conso.signal_stats(sigs)
        markers, zones, levels = [], [], []
        last_t = bars[-1].t if bars else 0
        for s in sigs[-hist:]:
            up = s["side"] == 1
            word = "BUY" if up else "SELL"
            res = {"TP": "✓ TP", "SL": "✗ SL", "open": "RUNNING"}.get(s["result"]) or f"⇢ TRAIL {s['r'] or 0:+g}R"
            markers.append(Marker(s["t"], "below" if up else "above", "▲" if up else "▼",
                                  "bold #22c55e" if up else "bold #f43f5e"))
            t_to = s["t_exit"] if s["t_exit"] else None
            zones.append(Zone(min(s["entry"], s["tp"]), max(s["entry"], s["tp"]), s["t"], "#0f3d2a", t_to=t_to,
                              label=f"{word} {s['entry']:.2f} · SL {s['sl']:.2f} ({s['sl_name']}) → TP {s['tp']:.2f} "
                                    f"({s['tp_name'] if s['tp_name'].endswith('R') else s['tp_name'] + ', ' + format(s['rr'], 'g') + 'R'}) {res}",
                              border="#22c55e"))
            zones.append(Zone(min(s["entry"], s["sl"]), max(s["entry"], s["sl"]), s["t"], "#4a1420", t_to=t_to,
                              border="#f43f5e"))
            if s["result"] == "open" or (s["t_exit"] and s["t_exit"] == last_t):
                levels += [Level(s["entry"], "#e2e8f0", "ENT"), Level(s["tp"], "#22c55e", "TP"),
                           Level(s["sl"], "#f43f5e", "SL")]
                if s["result"] == "open" and abs(s["cur_sl"] - s["sl"]) > 1e-6:
                    levels.append(Level(s["cur_sl"], "#fbbf24", "TRL"))
        if sigs and st["closed"]:
            # stats on the latest signal's label
            z = zones[-2]
            z.label += f"  ·  history W{st['wins']} L{st['losses']} ({st['win_rate']:.0f}%) {st['total_r']:+g}R"
        # details for the signal panel (why / against / caution)
        cz = caution.Context(bars)
        idx = {b.t: i for i, b in enumerate(bars)}
        details = []
        for s in sigs[-hist:]:
            i = idx.get(s["t"])
            bx = s["box"]
            why = [f"Box {bx['low']:.2f}-{bx['high']:.2f} ({len(bx['tops'])} tops / {len(bx['bottoms'])} bottoms, "
                   f"{bx['bars']} bars)",
                   f"{'Closed above' if s['side'] == 1 else 'Closed below'} the box + {p['buffer_pips']:g} pips"
                   if p.get("confirm", "close") == "close" else
                   f"Touched {'above' if s['side'] == 1 else 'below'} the box + {p['buffer_pips']:g} pips"]
            against = []
            if ctx.read is not None and getattr(ctx.read, "bias", 0):
                (why if ctx.read.bias == s["side"] else against).append(
                    f"Analyser bias NOW {'BULL' if ctx.read.bias == 1 else 'BEAR'} (current, not at signal time)")
            details.append(dict(t=s["t"], side=s["side"], score=None, passing=None, result=s["result"], r=s["r"],
                                entry=s["entry"], sl=s["sl"], tp=s["tp"], sl_name=s["sl_name"], tp_name=s["tp_name"],
                                groups=[], why_for=why, why_against=against, neutral=[],
                                cautions=cz.check(i, s["side"]) if i is not None else []))
        return Overlay(markers=markers, zones=zones, levels=levels, signals=details)


class ConsoBreakoutAlert(Strategy):
    name = "Conso Breakout alert"
    description = "Alert (no trade) when price breaks out of a consolidation box, with entry / SL / TP"
    tf = "M5"
    params = dict(PARAMS)

    def _sigs(self, ctx):
        bars = ctx.bars(self.tf)
        return _signals(bars[:-1], self.params) if len(bars) > 60 else []   # closed bars only

    def evaluate(self, ctx):
        sigs = self._sigs(ctx)
        bars = ctx.bars(self.tf)
        if not sigs or len(bars) < 3:
            return None
        s = sigs[-1]
        if s["t"] != bars[-2].t:          # only a breakout on the bar that just closed is new
            return None
        word = "BUY" if s["side"] == 1 else "SELL"
        agree = ""
        if ctx.read is not None and getattr(ctx.read, "bias", 0):
            agree = " · analyser bias AGREES" if ctx.read.bias == s["side"] else " · analyser bias AGAINST"
        return Signal(s["t"], s["side"], f"{word} box break {s['box']['low']:.2f}-{s['box']['high']:.2f} · "
                                         f"SL {s['sl']:.2f} TP {s['tp']:.2f} ({s['rr']:g}R){agree}",
                      tf=self.tf, entry=s["entry"], sl=s["sl"], tp=s["tp"])

    def status(self, ctx):
        bars = ctx.bars(self.tf)
        if len(bars) < 60:
            return ["warming up"]
        live = conso.detect(bars, **self.params)
        sigs = self._sigs(ctx)
        st = conso.signal_stats(sigs)
        out = [f"{self.tf} box {live['low']:.2f}-{live['high']:.2f} LIVE" if live["ok"] else f"{self.tf}: no box now"]
        if sigs:
            s = sigs[-1]
            out.append(f"last {'BUY' if s['side'] == 1 else 'SELL'} {s['entry']:.2f} → {s['result']}")
        if st["closed"]:
            out.append(f"history W{st['wins']} L{st['losses']} ({st['win_rate']:.0f}%) {st['total_r']:+g}R")
        return out
