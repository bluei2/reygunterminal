"""Analyser Signals: ▲ BUY / ▼ SELL on every bar where the ANALYSER turned READY - your HTF bias + a CHoCH/BOS
trigger + the weighted score >= your passing score, using exactly the indicators you switched to SCORE and your
group weights. Each signal gets an entry, SL and TP (from the TP/SL tools) drawn until it is hit, plus W/L and R.

The score for every past bar is rebuilt only from bars that had CLOSED at that time (no look-ahead). Change your
SCORE indicators / weights / passing score and the arrows are recalculated. SIGNALS ONLY - nothing is traded."""
import caution
import exits
from engine import TF_SECONDS
from plugin_api import Indicator, Level, Marker, Overlay, Zone


class AnalyserSignals(Indicator):
    name = "Analyser Signals"
    description = ("▲/▼ where the analyser turned READY (bias + trigger + score ≥ passing, with YOUR scoring "
                   "indicators) · SL/TP drawn with ✓ TP / ✗ SL result · sl_mode atr|swing|vwap|ob|fixed · "
                   "tp_source rr|vwap|ob|atr|all · trail none|vwap|atr")
    category = "Signals"
    purpose = "Posibleng BUY/SELL mula sa score"
    params = {"lookback": 300, "min_score": 0.0, "one_at_a_time": True,
              "sl_mode": "atr", "sl_fixed": 5.0, "swing_bars": 10, "max_sl": 10.0,
              "tp_source": "rr", "rr": 2.0, "min_rr": 1.0, "trail": "none",
              "atr_len": 14, "atr_sl": 1.5, "atr_tp1": 2.0, "atr_tp2": 3.0, "atr_trail": 3.0,
              "buffer_pips": 5.0, "pip": 0.10, "history": 15,
              "max_cautions": -1}   # -1 = only show ⚠ ; 0 = skip signals with any caution ; 1 = allow 1 ...

    def signals(self, bars, tf, ctx):
        p = self.params
        if ctx.history is None or tf not in TF_SECONDS or len(bars) < 30:
            return []
        sec = TF_SECONDS[tf]
        n = len(bars)
        start = max(20, n - 1 - int(p["lookback"]))
        buf = p["buffer_pips"] * p["pip"]
        atr_vals = exits.atr(bars, int(p["atr_len"]))
        srcs = exits.sources(p["tp_source"]) | {p["sl_mode"], p["trail"]}
        vw = bands = obs = None
        if "vwap" in srcs:
            vw, bands = exits.vwap_bands(bars)
        if "ob" in srcs:
            obs = exits.order_blocks(bars)
        akw = dict(atr_vals=atr_vals, atr_sl=p["atr_sl"], atr_tp1=p["atr_tp1"], atr_tp2=p["atr_tp2"],
                   atr_trail=p["atr_trail"], bars=bars)
        cz = caution.Context(bars)
        out, prev_ready, open_until = [], 0, -1
        for i in range(start, n - 1):                 # closed bars only (the last one is still forming)
            r = ctx.history(bars[i].t + sec)
            if p["min_score"] > 0:                    # your own threshold instead of the passing score
                ok = r.bias and r.trigger and r.overall is not None and r.overall >= p["min_score"]
            else:
                ok = r.bias and "READY" in r.verdict        # exactly the analyser's READY
            ready = r.bias if ok else 0
            fresh = ready and ready != prev_ready
            prev_ready = ready
            if not fresh:
                continue
            if p["one_at_a_time"] and i <= open_until:
                continue                              # previous signal still running
            side, entry = ready, bars[i].c
            warns = cz.check(i, side)
            if p["max_cautions"] >= 0 and len(warns) > p["max_cautions"]:
                continue                              # filtered out by your caution limit
            why_for, why_against, neutral = [], [], []
            for g in r.groups:
                for it in g["items"]:
                    txt = f"{g['category']}: {it['name']}" + (f" ({it['reason']})" if it.get("reason") else "")
                    (why_for if it["dir"] == side else why_against if it["dir"] == -side else neutral).append(txt)
            # ---- stop
            sl, sl_name = None, p["sl_mode"]
            if p["sl_mode"] in ("vwap", "ob", "atr"):
                pl = exits.exit_plan(side, entry, i, vw, bands, obs, tp_source="none", sl_source=p["sl_mode"],
                                     buffer=buf, min_dist=buf, max_sl=p["max_sl"], **akw)
                if pl["sl"]:
                    sl, sl_name = pl["sl"]
            elif p["sl_mode"] == "swing":
                sl, sl_name = exits.swing_stop(bars, i, side, int(p["swing_bars"]), buf), "swing"
            elif p["sl_mode"] == "fixed":
                sl, sl_name = entry - side * p["sl_fixed"], "fixed"
            if sl is None or (entry - sl) * side <= 0:
                a = atr_vals[i] or 1.0
                sl, sl_name = entry - side * p["atr_sl"] * a, f"{p['atr_sl']:g}×ATR"
            if p["max_sl"] > 0 and abs(entry - sl) > p["max_sl"]:
                sl = entry - side * p["max_sl"]
            sl_d = abs(entry - sl)
            # ---- target
            tp, tp_name = entry + side * sl_d * p["rr"], f"{p['rr']:g}R"
            if str(p["tp_source"]).lower() != "rr":
                pl = exits.exit_plan(side, entry, i, vw, bands, obs, tp_source=p["tp_source"], sl_source="none",
                                     buffer=buf, min_dist=max(buf, p["min_rr"] * sl_d), max_sl=p["max_sl"], **akw)
                if pl["tp1"]:
                    tp, tp_name = pl["tp1"]
            res = exits.replay(bars, i, side, entry, sl, tp, p["trail"], vw, atr_vals, p["atr_trail"], buf)
            if res["t_exit"]:
                open_until = next((k for k in range(i, n) if bars[k].t == res["t_exit"]), n)
            else:
                open_until = n
            out.append(dict(t=bars[i].t, side=side, entry=round(entry, 2), sl=round(sl, 2), tp=round(tp, 2),
                            sl_name=sl_name, tp_name=tp_name, rr=round(abs(tp - entry) / sl_d, 2) if sl_d else 0,
                            score=round(r.overall, 0), passing=r.passing, verdict=r.verdict,
                            groups=[dict(category=g["category"], pct=g["pct"], agree=g["agree"], total=g["total"],
                                         weight=g["weight"]) for g in r.groups],
                            why_for=why_for, why_against=why_against, neutral=neutral, cautions=warns, **res))
        return out

    def compute(self, bars, tf, ctx):
        sigs = self.signals(bars, tf, ctx)
        if not sigs:
            return Overlay()
        st = exits.stats(sigs)
        markers, zones, levels = [], [], []
        for s in sigs[-int(self.params["history"]):]:
            up = s["side"] == 1
            word = "BUY" if up else "SELL"
            res = {"TP": "✓ TP", "SL": "✗ SL", "open": "RUNNING"}.get(s["result"]) or f"⇢ TRAIL {s['r'] or 0:+g}R"
            warn = f" ⚠{len(s['cautions'])}" if s["cautions"] else ""
            markers.append(Marker(s["t"], "below" if up else "above", "▲" if up else "▼",
                                  "bold #fbbf24" if s["cautions"] else ("bold #38bdf8" if up else "bold #f472b6")))
            t_to = s["t_exit"] or None
            zones.append(Zone(min(s["entry"], s["tp"]), max(s["entry"], s["tp"]), s["t"], "#0c3553", t_to=t_to,
                              label=f"SCORE {s['score']:.0f}%{warn} {word} {s['entry']:.2f} · SL {s['sl']:.2f} "
                                    f"({s['sl_name']}) → TP {s['tp']:.2f} ({s['tp_name'] if s['tp_name'].endswith('R') else s['tp_name'] + ', ' + format(s['rr'], 'g') + 'R'}) {res}",
                              border="#38bdf8"))
            zones.append(Zone(min(s["entry"], s["sl"]), max(s["entry"], s["sl"]), s["t"], "#4a1420", t_to=t_to,
                              border="#f43f5e"))
            if s["result"] == "open":
                levels += [Level(s["entry"], "#e2e8f0", "AENT"), Level(s["tp"], "#38bdf8", "ATP"),
                           Level(s["sl"], "#f43f5e", "ASL")]
                if abs(s["cur_sl"] - s["sl"]) > 1e-6:
                    levels.append(Level(s["cur_sl"], "#fbbf24", "ATRL"))
        if st["closed"]:
            zones[-2].label += f"  ·  history W{st['wins']} L{st['losses']} ({st['win_rate']:.0f}%) {st['total_r']:+g}R"
            clean = exits.stats([x for x in sigs if not x["cautions"]])
            warned = exits.stats([x for x in sigs if x["cautions"]])
            if clean["closed"] and warned["closed"]:
                zones[-2].label += (f"  ·  clean W{clean['wins']}/L{clean['losses']} {clean['total_r']:+g}R"
                                    f" vs ⚠ W{warned['wins']}/L{warned['losses']} {warned['total_r']:+g}R")
        details = [dict(t=s["t"], side=s["side"], title=f"{'BUY' if s['side'] == 1 else 'SELL'} {s['entry']:.2f}",
                        score=s["score"], passing=s["passing"], result=s["result"], r=s["r"], entry=s["entry"],
                        sl=s["sl"], tp=s["tp"], sl_name=s["sl_name"], tp_name=s["tp_name"], groups=s["groups"],
                        why_for=s["why_for"], why_against=s["why_against"], neutral=s["neutral"],
                        cautions=s["cautions"]) for s in sigs[-int(self.params["history"]):]]
        return Overlay(markers=markers, zones=zones, levels=levels, signals=details)
