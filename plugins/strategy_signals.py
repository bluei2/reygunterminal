"""One chart INDICATOR per market-condition STRATEGY (strategies.py, set on the Trading page), each its own
switch in LAYERS:
  Strategy · TREND          TR ▲/▼  = 2 BOS on M15 + 1 BOS on H4 in the same direction -> market entry, 1:RR
                            (optional B15 / H4 marks on the bars where the M15 / H4 structure broke)
  Strategy · PULLBACK       PB ▲/▼  = trend + oversold/overbought + MFI + doji + order block
  Strategy · CONSOLIDATION  BOX + pending BUY STOP / SELL STOP grid when volume low (1) + ADX < 20 (2) + box (3);
                            one stop filled = the opposite is cancelled (OCO)
Each arrow has its SL / TP and the W/L result, like Analyser Signals. SIGNALS ONLY: AUTO TRADE takes them only
for the strategies you switched to AUTO on the Trading page."""
import os

import exits
import strategies as ST
from engine import TF_SECONDS
from plugin_api import Indicator, Level, Marker, Overlay, Zone

_CACHE = {"mtime": None, "cfg": None}
COLOR = {"trend": "#22d3ee", "pullback": "#a78bfa", "conso": "#fbbf24"}


def current_settings():
    """strategy_settings.json, re-read only when it changes."""
    try:
        m = os.path.getmtime(ST.SETTINGS_FILE)
    except OSError:
        m = None
    if _CACHE["cfg"] is None or m != _CACHE["mtime"]:
        _CACHE["cfg"], _CACHE["mtime"] = ST.load_settings(), m
    return _CACHE["cfg"]


def _mtf(ctx):
    if ctx is None:
        return None
    try:
        return {tf: ctx.bars(tf) for tf, _ in ST.MTF}
    except Exception:
        return None


def _signals(name, bars, tf, ctx, lookback):
    if tf not in TF_SECONDS or len(bars) < 80:
        return []
    cfg = current_settings()
    sec = TF_SECONDS[tf]
    hist = getattr(ctx, "history", None) if ctx is not None else None

    def bias_fn(i):
        if hist is None:
            return 0
        try:
            return hist(bars[i].t + sec).bias or 0
        except Exception:
            return 0
    return ST.signals(name, bars, cfg[name], bias_fn if name != "conso" else None, int(lookback), mtf=_mtf(ctx))


def _arrows(name, sigs, bars, history):
    """TR / PB arrows + live entry / SL / TP levels + the signal-panel details."""
    atr = exits.atr(bars, 14)
    idx = {b.t: k for k, b in enumerate(bars)}
    markers, levels, details = [], [], []
    tag = "TR" if name == "trend" else "PB"
    for s in sigs[-int(history):]:
        i = idx.get(s["t"])
        res = exits.replay(bars, i, s["side"], s["entry"], s["sl"], s["tp"], "none", None, atr, 3.0, 0.0) \
            if i is not None else dict(result="open", r=None, t_exit=None)
        up = s["side"] == 1
        rtxt = {"TP": " ✓", "SL": " ✗"}.get(res["result"], "")
        markers.append(Marker(s["t"], "below" if up else "above", f"{tag}{'▲' if up else '▼'}{rtxt}", f"bold {COLOR[name]}"))
        if res["result"] == "open":
            levels += [Level(s["entry"], "#e2e8f0", f"{tag}E"), Level(s["sl"], "#f43f5e", f"{tag}SL"),
                       Level(s["tp"], "#22c55e", f"{tag}TP")]
        details.append(dict(t=s["t"], side=s["side"], title=f"{ST.TITLE[name]} {'BUY' if up else 'SELL'} {s['entry']:.2f}",
                            score=s["score"], passing=current_settings()[name]["min_pct"], result=res["result"],
                            r=res.get("r"), entry=s["entry"], sl=s["sl"], tp=s["tp"], sl_name="strategy",
                            tp_name=f"{s['rr']:g}R", groups=[],
                            why_for=[ST.label(name, k) for k in s["why_for"]],
                            why_against=[ST.label(name, k) for k in s["why_against"]], neutral=[], cautions=[]))
    return markers, levels, details


class TrendStrategy(Indicator):
    name = "Strategy · TREND"
    description = ("TR ▲/▼ = 2 break of structure sa M15 + 1 BOS sa H4 sa parehong direksyon → entry kasabay ng "
                   "trend (SL / RR sa Trading → STRATEGIES). B15 / H4 = kung saan nag-BOS ang M15 / H4.")
    category = "Signals"
    purpose = "Trend entry (BOS entry TF + trend TF)"
    params = {"show_bos": True, "lookback": 300, "history": 20}

    def compute(self, bars, tf, ctx):
        sigs = _signals("trend", bars, tf, ctx, self.params["lookback"])
        markers, levels, details = _arrows("trend", sigs, bars, self.params["history"])
        if self.params.get("show_bos") and tf in TF_SECONDS and len(bars) > 10:
            sec, t0 = TF_SECONDS[tf], bars[max(0, len(bars) - int(self.params["lookback"]))].t
            m = _mtf(ctx) or {}
            tcfg = current_settings()["trend"]
            ltf_name = tcfg.get("bos_ltf", "M15")
            for htf, hsec in ST.MTF:
                if htf not in (ltf_name, tcfg.get("bos_htf", "H4")):
                    continue
                src = m.get(htf) or []
                if hsec < sec or len(src) < 10:
                    continue
                for b, v in zip(src, ST._bos(src)):
                    close = b.t + hsec
                    if v and close - sec >= t0:
                        # the chart bar in which the higher-TF bar closed (the BOS became known)
                        t_mark = (close - sec) // sec * sec
                        markers.append(Marker(t_mark, "below" if v == 1 else "above",
                                              f"{'B' + htf[1:] if htf == ltf_name else htf}{'↑' if v == 1 else '↓'}",
                                              "#67e8f9" if htf == ltf_name else "bold #0ea5e9"))
        details.sort(key=lambda x: x["t"])
        return Overlay(markers=markers, levels=levels, signals=details)


class PullbackStrategy(Indicator):
    name = "Strategy · PULLBACK"
    description = ("PB ▲/▼ = uptrend + oversold (RSI) + MFI + doji + order block → BUY (salamin para sa SELL). "
                   "Mga setting nasa Trading → STRATEGIES.")
    category = "Signals"
    purpose = "Pullback entry"
    params = {"lookback": 300, "history": 20}

    def compute(self, bars, tf, ctx):
        sigs = _signals("pullback", bars, tf, ctx, self.params["lookback"])
        markers, levels, details = _arrows("pullback", sigs, bars, self.params["history"])
        details.sort(key=lambda x: x["t"])
        return Overlay(markers=markers, levels=levels, signals=details)


class ConsolidationStrategy(Indicator):
    name = "Strategy · CONSOLIDATION"
    description = ("Score 1-2-3: mahinang volume (1) + ADX < 20 (2) + box (3) → BUY STOP sa itaas / SELL STOP sa "
                   "ibaba ng box, nandoon na bago mag-break. Na-hit ang isa = cancel ang kabila (OCO).")
    category = "Signals"
    purpose = "Box breakout pending (OCO)"
    params = {"lookback": 300, "history": 20}

    def compute(self, bars, tf, ctx):
        sigs = _signals("conso", bars, tf, ctx, self.params["lookback"])
        if not sigs:
            return Overlay()
        markers, zones, levels = [], [], []
        last_t = bars[-1].t
        exp = TF_SECONDS.get(tf, 300) * int(current_settings()["conso"]["expiry_bars"])
        for s in sigs[-int(self.params["history"]):]:
            p = s["plan"]
            cnt = f"{p.get('count', '?')}/{p.get('count_of', 3)}"
            zones.append(Zone(p["box_low"], p["box_high"], p.get("box_from") or s["t"], "#3b2f0b",
                              t_to=min(last_t, s["t"] + exp),
                              label=f"CONSO {cnt} · BUY STOP {p['buy_stop']:.2f} / SELL STOP {p['sell_stop']:.2f} "
                                    f"(OCO) · SL ${p['sl_dist']:.2f} · 1:{p['rr']:g}", border="#fbbf24"))
            markers.append(Marker(s["t"], "above", f"BOX {cnt}", "bold #fbbf24"))
        s = sigs[-1]
        if last_t - s["t"] <= exp:
            for g in s["plan"].get("grid") or []:
                levels.append(Level(g["entry"], "#22c55e" if g["side"] == 1 else "#f43f5e",
                                    f"{'BS' if g['side'] == 1 else 'SS'}{g['level'] + 1}"))
        return Overlay(markers=markers, zones=zones, levels=levels)
