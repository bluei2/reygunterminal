"""Trend Line Breakout: draws automatic trend lines (falling resistance through lower swing highs, rising support
through higher swing lows) and marks the breakout when a candle CLOSES through the line.

Confirmation: strong breakout candle, volume, follow-through, retest that holds, number of touches.
TL✓ = confirmed, TL? = not yet, TL✗ = failed (closed back through the line).

Score group "Structure": a fresh confirmed breakout (last vote_within bars of vote_tf) votes in its direction, so a
trend line break WITH your HTF bias raises the score and one against it lowers it. No fresh break = no vote.
Each breakout also gets an entry / SL / TP plan and its result (TP / SL) so you can see how well it works.
SIGNALS ONLY: nothing is traded unless you add "trendline" to AUTO TRADE sources."""
import caution
import exits
import trendline
from plugin_api import Indicator, Level, Line, Marker, Overlay, Vote


class TrendlineBreakout(Indicator):
    name = "Trend Line Breakout"
    description = ("Auto trend lines (falling resistance / rising support) + breakout on a CLOSE through the line · "
                   "confirmation: strong candle, volume, follow-through, retest, touches · TL✓ confirmed, TL? "
                   "waiting, TL✗ failed · entry = breakout close or retest (entry) · SL swing|line · vote: fresh "
                   "confirmed break")
    category = "Structure"
    purpose = "Trend line breakout + retest"
    vote_tf = "M15"
    params = dict(trendline.DEFAULTS, lookback=400, history=5, entry="break", sl_mode="swing", sl_atr=0.5,
                  max_sl=10.0, rr=2.0, confirmed_only=False, vote_within=6)

    def _fp(self):
        return {k: self.params[k] for k in trendline.DEFAULTS}

    def _run(self, bars):
        p = self.params
        f = trendline.Finder(bars, self._fp())
        ev, active = f.scan(max(0, len(bars) - 1 - int(p["lookback"])))
        return f, ev, active

    def signals(self, bars, utc_offset=0):
        p = self.params
        if len(bars) < 60:
            return []
        f, evs, _ = self._run(bars)
        cz = caution.Context(bars)
        idx = {b.t: k for k, b in enumerate(bars)}
        out, open_until = [], -1
        for ev in evs:
            if p["confirmed_only"] and not ev["confirmed"]:
                continue
            side = ev["side"]
            i = ev["i"]
            if p["entry"] == "retest":
                if not ev["retest_t"]:
                    continue
                i = idx[ev["retest_t"]]
            elif p["confirmed_only"]:
                i = idx[ev["confirm_t"]]                   # enter when it is confirmed, not before
            if i <= open_until:
                continue
            entry = bars[i].c
            a = f.atr[i] or 1.0
            if p["sl_mode"] == "line":
                sl = f.y(ev["line"], i) - side * p["sl_atr"] * a
                sl_name = f"line -{p['sl_atr']:g}×ATR"
            else:
                sl = exits.swing_stop(bars, i, side, 10, p["buffer_pips"] * p["pip"])
                sl_name = "swing"
            if p["max_sl"] > 0 and abs(entry - sl) > p["max_sl"]:
                sl = entry - side * p["max_sl"]
                sl_name += " (capped)"
            risk = (entry - sl) * side
            if risk <= 0:
                continue
            tp = entry + side * risk * p["rr"]
            res = exits.replay(bars, i, side, entry, sl, tp, "none", None, f.atr, 3.0, 0.0)
            open_until = next((k for k in range(i, len(bars)) if bars[k].t == res["t_exit"]), len(bars)) \
                if res["t_exit"] else len(bars)
            word = "BUY" if side == 1 else "SELL"
            tc = f.trend_change(ev)
            why = list(ev["why"]) + ([f"🔄 TREND CHANGE confirmed {tc['bars_after']} bar(s) after the break: "
                                      + "; ".join(tc["why"])] if tc else [])
            out.append(dict(t=bars[i].t, t_break=ev["t"], side=side, entry=round(entry, 2), sl=round(sl, 2),
                            tp=round(tp, 2), sl_name=sl_name, tp_name=f"{p['rr']:g}R", rr=p["rr"],
                            score=ev["score"], passing=p["min_score"], confirmed=ev["confirmed"], failed=ev["failed"],
                            title=f"TL BREAK {word} {entry:.2f}", groups=[], why_for=why, trend_change=tc,
                            why_against=ev["against"], neutral=[], line=ev["line"], level=round(ev["level"], 2),
                            cautions=cz.check(i, side), **res))
        return out

    def compute(self, bars, tf, ctx):
        p = self.params
        if len(bars) < 60:
            return Overlay()
        f, evs, active = self._run(bars)
        sigs = self.signals(bars)
        n = len(bars)
        lines, markers, levels = [], [], []

        def draw(ln, end, color, style, label, axis):
            vals = [None] * n
            for x in range(ln["a"], min(n, end + 1)):
                vals[x] = round(f.y(ln, x), 2)
            lines.append(Line(vals, color, label, style=style, width=2 if axis else 1, autoscale=False, axis=axis))

        for ev in evs[-int(p["history"]):]:
            col = "#64748b" if ev["failed"] else ("#22c55e" if ev["side"] == 1 else "#f43f5e")
            draw(ev["line"], ev["i"], col, "dashed", "TL broken", False)
            tag = "✗" if ev["failed"] else "✓" if ev["confirmed"] else "?"
            markers.append(Marker(ev["t"], "below" if ev["side"] == 1 else "above",
                                  f"TL{tag}{ev['score']:.0f}" + (" RT" if ev["retest_t"] else ""),
                                  f"bold {col}"))
        for ln in active:
            col = "#f472b6" if ln["side"] == -1 else "#34d399"
            draw(ln, n - 1, col, "line", "TL res" if ln["side"] == -1 else "TL sup", True)
        seen_tc = set()
        for ev in evs[-int(p["history"]):]:
            tc = f.trend_change(ev)
            if tc and (tc["side"], round(tc["level"], 2)) in seen_tc:
                continue
            if tc:
                seen_tc.add((tc["side"], round(tc["level"], 2)))
                markers.append(Marker(tc["t"], "below" if tc["side"] == 1 else "above",
                                      "TREND▲" if tc["side"] == 1 else "TREND▼", "bold #fbbf24"))
        for s in sigs[-int(p["history"]):]:
            if s["result"] in ("TP", "SL") and s["t_exit"]:
                markers.append(Marker(s["t_exit"], "above" if s["side"] == 1 else "below", s["result"],
                                      "#22c55e" if s["result"] == "TP" else "#f43f5e"))
        return Overlay(lines=lines, markers=markers, levels=levels, signals=sigs[-int(p["history"]):])

    def vote(self, ctx):
        bars = self.vote_bars(ctx)
        if len(bars) < 60:
            return None
        f = trendline.Finder(bars + [bars[-1]], self._fp())      # last element = "forming" placeholder
        evs, _ = f.scan(max(0, len(bars) - 1 - int(self.params["lookback"])))
        n = len(bars)
        w = int(self.params["vote_within"])
        for ev in reversed(evs[-6:]):
            tc = f.trend_change(ev)
            if tc and tc["i"] >= n - w:
                return Vote(tc["side"], f"TREND CHANGE {'UP' if tc['side'] == 1 else 'DOWN'} (TL break + CHoCH)")
        for ev in reversed(evs):
            if ev["i"] < n - w:
                break
            if ev["failed"]:
                return Vote(-ev["side"], f"TL break {'up' if ev['side'] == 1 else 'down'} FAILED")
            if ev["confirmed"]:
                return Vote(ev["side"], f"TL break {'up' if ev['side'] == 1 else 'down'} ✓ ({ev['touches']} touches"
                            + (", retest" if ev["retest_t"] else "") + ")")
            return None                                         # fresh but not confirmed yet: no vote
        return None

    def trend_changes(self, bars):
        """Trend change events as signal-like dicts (Telegram / other modules)."""
        if len(bars) < 60:
            return []
        f, evs, _ = self._run(bars)
        out = []
        last = None
        for ev in evs:
            tc = f.trend_change(ev)
            if tc and last and tc["side"] == last["side"] and abs(tc["level"] - last["level"]) < 1e-6:
                continue                                   # two lines broken into the same CHoCH: one trend change
            if tc:
                last = tc
                c = bars[tc["i"]]
                out.append(dict(t=tc["t"], side=tc["side"], tc=True, entry=round(c.c, 2), level=round(tc["level"], 2),
                                why_for=tc["why"], why_against=[], cautions=[], score=ev["score"],
                                passing=self.params["min_score"], sl=None, tp=None))
        return out

    def confirmed_breaks(self, bars):
        """Confirmed trend line breaks, timed on the bar where they were confirmed (no look-ahead)."""
        if len(bars) < 60:
            return []
        f, evs, _ = self._run(bars)
        return [dict(t=e["confirm_t"], side=e["side"], why=e["why"]) for e in evs if e["confirmed"] and e["confirm_t"]]
