"""Reversal: arrows where the move may be ending: liquidity sweeps (equal highs/lows, PDH/PDL, Asia high/low,
swing, range, failed breakout), reversal candle patterns (morning/evening star, three outside, engulfing, pin bar,
piercing/dark cloud, tweezer, harami, doji), RSI divergence, exhaustion, climax volume, order block. "R?" = possible, "R✓" = confirmed by a close through the reversal bar.

Score group "Reversal": a fresh reversal in the last few closed bars votes in its direction. A reversal AGAINST
your HTF bias lowers the score (warning), one WITH the bias (end of a pullback) raises it. No reversal = no vote.
Also shown as ⚠ caution on Analyser Signals that go against a fresh reversal. SIGNALS ONLY: nothing is traded."""
import exits
import reversal
from plugin_api import Indicator, Level, Marker, Overlay, Vote


class ReversalSignals(Indicator):
    name = "Reversal"
    description = ("⟲ possible top / bottom · SWEEPS: EQH/EQL, PDH/PDL, Asia high/low, swing, range, failed breakout · "
                   "CANDLES: morning/evening star, three outside, engulfing, pin bar, piercing/dark cloud, tweezer, "
                   "harami, doji · + RSI divergence, OB/OS, stretched, climax volume, order block · R✓ = confirmed "
                   "(CHoCH close or confirmation candle) · vote: fresh reversal in the last vote_within bars")
    category = "Reversal"
    purpose = "Posibleng pagbaliktad ng galaw"
    vote_tf = "M15"
    params = dict(reversal.DEFAULTS, lookback=300, history=20, confirmed_only=False, rr=2.0, sl_atr=0.3,
                  vote_within=3)

    def _det_params(self):
        return {k: self.params[k] for k in reversal.DEFAULTS}

    def signals(self, bars, utc_offset=0, confirmed_only=None):
        """Reversal events with an entry / SL / TP plan and the result (for the signal panel, stats, alerts).
        confirmed_only: None = the plugin setting; True / False = override (AUTO TRADE uses its own choice)."""
        p = dict(self.params)
        if confirmed_only is not None:
            p["confirmed_only"] = bool(confirmed_only)
        if len(bars) < 40:
            return []
        det = reversal.Detector(bars, self._det_params(), utc_offset=utc_offset)
        evs = det.scan(len(bars) - 1 - int(p["lookback"]))
        out, open_until = [], -1
        for ev in evs:
            if p["confirmed_only"] and not ev["confirmed"]:
                continue
            i = ev["i"]
            if p["confirmed_only"]:
                i = next(k for k, b in enumerate(bars) if b.t == ev["confirm_t"])
            if i <= open_until:
                continue
            side, entry = ev["side"], bars[i].c
            a = det.atr[i] or 1.0
            sl = ev["level"] - side * p["sl_atr"] * a
            risk = (entry - sl) * side
            if risk <= 0:
                continue
            tp = entry + side * risk * p["rr"]
            res = exits.replay(bars, i, side, entry, sl, tp, "none", None, det.atr, 3.0, 0.0)
            if res["t_exit"]:
                open_until = next((k for k in range(i, len(bars)) if bars[k].t == res["t_exit"]), len(bars))
            else:
                open_until = len(bars)
            kind = "TOP (bearish)" if side == -1 else "BOTTOM (bullish)"
            out.append(dict(t=bars[i].t, side=side, entry=round(entry, 2), sl=round(sl, 2), tp=round(tp, 2),
                            sl_name="beyond the wick", tp_name=f"{p['rr']:g}R", rr=p["rr"], score=ev["score"],
                            passing=p["min_score"], confirmed=ev["confirmed"], kind=kind,
                            title=f"REVERSAL {kind} {entry:.2f}",
                            tags=ev["tags"], groups=[],
                            why_for=ev["why"] + ([f"CONFIRMED: {ev['confirm_how']}"] if ev["confirmed"] else []),
                            why_against=[] if ev["confirmed"] else
                            [f"not confirmed yet: wait for a close {'below' if side == -1 else 'above'} "
                             f"{bars[ev['i']].l if side == -1 else bars[ev['i']].h:.2f}"],
                            neutral=[], cautions=[], **res))
        return out

    def compute(self, bars, tf, ctx):
        sigs = self.signals(bars, ctx.utc_offset if ctx else 0)
        markers, levels = [], []
        for s in sigs[-int(self.params["history"]):]:
            up = s["side"] == 1
            col = ("#22c55e" if up else "#f43f5e") if s["confirmed"] else ("#86efac" if up else "#fda4af")
            res = {"TP": " TP", "SL": " SL"}.get(s["result"], "")
            markers.append(Marker(s["t"], "below" if up else "above",
                                  f"R{'✓' if s['confirmed'] else '?'}{s['score']:.0f} {' '.join(s['tags'][:2])}{res}",
                                  f"bold {col}"))
        if sigs and sigs[-1]["result"] == "open":
            s = sigs[-1]
            levels += [Level(s["entry"], "#e2e8f0", "REV"), Level(s["sl"], "#f43f5e", "SL"),
                       Level(s["tp"], "#22c55e", "TP")]
        return Overlay(markers=markers, levels=levels, signals=sigs[-int(self.params["history"]):])

    def vote(self, ctx):
        bars = self.vote_bars(ctx)
        if len(bars) < 40:
            return None
        det = reversal.Detector(bars + [bars[-1]], self._det_params(), utc_offset=ctx.utc_offset)  # last = "forming"
        n = len(bars)
        for i in range(n - 1, max(0, n - 1 - int(self.params["vote_within"])), -1):
            best = max((x for x in (det.evaluate(i, -1), det.evaluate(i, 1)) if x),
                       key=lambda x: x["score"], default=None)
            if best:
                word = "top" if best["side"] == -1 else "bottom"
                return Vote(best["side"], f"{word} {best['score']:.0f} ({best['why'][0][:40]})"
                            + (" ✓" if best["confirmed"] else ""))
        return None
