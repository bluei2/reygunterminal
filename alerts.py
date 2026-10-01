"""
Alert producers: turn what happens in the terminal into Telegram messages (notify.Notifier) and journal pictures.

  signals   watches the chart's Analyser Signals / Conso Breakout on the chosen TF: a new arrow on the bar
            that just closed -> message with entry / SL / TP, score, reasons for / against, cautions, regime
  trades    TradeManager calls on_open / on_close -> chart picture saved in the journal + message
  risk      TradeManager calls on_risk when an account hits its daily DD / TP lock
  news      a high-impact event entering the warning window (News settings)
  eod       end-of-day report at settings.eod_hour (local time), also saved in reports/
Signals are ONLY messages. Nothing here sends an order.
"""
from __future__ import annotations

import html
import os
import threading
import time

import caution
import snapshot

HERE = os.path.dirname(os.path.abspath(__file__))
REPORT_DIR = os.path.join(HERE, "reports")
esc = html.escape


def _px(v):
    return "-" if v in (None, 0) else f"{v:.2f}"


class Alerts:
    def __init__(self, hub):
        self.hub = hub
        self.seen_sig = set()
        self.last_bar = {}
        self.news_sent = set()
        self.eod_done = None
        self.day_sigs = {}            # local date -> list of (source, t, side)
        self.last_report = None
        self.running = True
        threading.Thread(target=self._loop, daemon=True).start()

    @property
    def n(self):
        return getattr(self.hub, "notifier", None)

    def _local(self, t=None):
        tz = self.n.settings["tz_offset"] if self.n else 8
        return time.gmtime((t or time.time()) + tz * 3600)

    def _regime_txt(self):
        rg = getattr(self.hub, "regime", None)
        if not rg:
            return ""
        try:
            st = rg.state()
            return f"{st['label']} ({st['settings']['tf']}) · {st['htf_read']['label']} ({st['settings']['htf']})"
        except Exception:
            return ""

    # ------------------------------------------------------------------ signals
    def _plugin(self, key):
        return next((x for x in self.hub.plugins if x.key == key and not x.error), None)

    def _signals(self, src, tf, bars):
        if src == "reversal":
            p = self._plugin("reversal_signals.ReversalSignals")
            return p.obj.signals(bars, self.hub.utc_offset) if p else []
        if src == "trendline":
            p = self._plugin("trendline_breakout.TrendlineBreakout")
            return (p.obj.signals(bars) + p.obj.trend_changes(bars)) if p else []
        if src == "analyser":
            p = self._plugin("analyser_signals.AnalyserSignals")
            if p is None:
                return []
            with self.hub.lock:
                return p.obj.signals(bars, tf, self.hub.ctx())
        p = self._plugin("conso_breakout.ConsoBreakoutSignals")
        if p is None:
            return []
        import conso
        prm = dict(p.obj.params)
        prm.pop("history", None)
        sigs = conso.breakout_signals(bars, **prm)
        cz = caution.Context(bars)
        idx = {b.t: i for i, b in enumerate(bars)}
        for s in sigs:
            i = idx.get(s["t"])
            s["cautions"] = cz.check(i, s["side"]) if i is not None else []
        return sigs

    def _watch_signals(self):
        n = self.n
        if not n or not n.ready() or not n.settings["events"].get("signals"):
            return
        s = n.settings
        tf = s["signal_tf"]
        bars = self.hub.src.bars(tf)
        if len(bars) < 40:
            return
        if self.last_bar.get(tf) == bars[-1].t:
            return
        first = tf not in self.last_bar
        self.last_bar[tf] = bars[-1].t
        closed_t = bars[-2].t
        for src in s["signal_sources"]:
            try:
                sigs = self._signals(src, tf, bars)
            except Exception as e:
                self.hub.note("error", f"alerts: {src} signals: {e}")
                continue
            for sg in sigs:
                key = (src, tf, sg["t"], bool(sg.get("tc")))
                if key in self.seen_sig:
                    continue
                self.seen_sig.add(key)
                if first or sg["t"] != closed_t:
                    continue                                   # only the bar that just closed
                if s["signal_min_score"] and sg.get("score") is not None and sg["score"] < s["signal_min_score"]:
                    continue
                day = time.strftime("%Y-%m-%d", self._local())
                self.day_sigs.setdefault(day, []).append((src, sg["t"], sg["side"]))
                n.send("signals", self._signal_text(src, tf, sg))

    def _signal_text(self, src, tf, sg):
        up = sg["side"] == 1
        head = f"{'🟢' if up else '🔴'} <b>{'BUY' if up else 'SELL'} signal</b> · {esc(self.hub.src.symbol)} {tf}"
        src_name = {"analyser": "Analyser", "breakout": "Conso breakout", "reversal": "Reversal",
                    "trendline": "Trend line breakout"}.get(src, src)
        if sg.get("tc"):
            lines = [f"🔄 <b>TREND CHANGE {'UP ↑' if up else 'DOWN ↓'}</b> · {esc(self.hub.src.symbol)} {tf}",
                     f"Price {_px(sg['entry'])} · {'swing high' if up else 'swing low'} {_px(sg['level'])} nabasag"]
            lines += ["✅ " + esc(w) for w in sg.get("why_for", [])]
            rg = self._regime_txt()
            if rg:
                lines.append(f"Regime: {esc(rg)}")
            lines.append("<i>trend line break (close sa labas) + CHoCH · senyales lang</i>")
            return "\n".join(lines)
        if src == "reversal":
            head = (f"⟲ <b>POSSIBLE {'BOTTOM (bullish)' if up else 'TOP (bearish)'} reversal</b> · "
                    f"{esc(self.hub.src.symbol)} {tf}")
        lines = [head,
                 f"Entry <b>{_px(sg['entry'])}</b> · SL {_px(sg['sl'])} ({esc(str(sg.get('sl_name', '')))}) · "
                 f"TP {_px(sg['tp'])} ({esc(str(sg.get('tp_name', '')))})"]
        if sg.get("score") is not None:
            lines.append(f"Score <b>{sg['score']:.0f}%</b> (pass {sg.get('passing', 0):.0f}%) · {src_name}")
        else:
            lines.append(src_name)
        rg = self._regime_txt()
        if rg:
            lines.append(f"Regime: {esc(rg)}")
        wf, wa = sg.get("why_for") or [], sg.get("why_against") or []
        if wf:
            lines.append("✅ " + esc("; ".join(wf[:4])) + (f" +{len(wf) - 4}" if len(wf) > 4 else ""))
        if wa:
            lines.append("❌ " + esc("; ".join(wa[:3])) + (f" +{len(wa) - 3}" if len(wa) > 3 else ""))
        for c in (sg.get("cautions") or [])[:3]:
            lines.append("⚠️ " + esc(c))
        at = getattr(self.hub, "autotrader", None)
        lines.append(f"<i>signal lang · auto trade {'ON' if at and at.enabled else 'OFF'}</i>")
        return "\n".join(lines)

    # ------------------------------------------------------------------ trades (called by TradeManager)
    def on_open(self, gid):
        threading.Thread(target=self._open_work, args=(gid,), daemon=True).start()

    def _open_work(self, gid):
        tm = self.hub.trader
        e = tm.journal.entries.get(gid) if tm else None
        if not e:
            return
        png = None
        try:
            png = snapshot.trade_picture(self.hub, e, "entry")
            name = snapshot.save(f"{gid}_entry.png", png)
            if name:
                tm.journal.add_snap(gid, "entry", name)
        except Exception as ex:
            self.hub.note("error", f"snapshot: {ex}")
        n = self.n
        if not n:
            return
        up = e["side"] == 1
        sc = e.get("score") or {}
        acc = e.get("accounts") or []
        txt = [f"{'🟢' if up else '🔴'} <b>{e.get('kind', 'manual').upper()} {'BUY' if up else 'SELL'}</b> "
               f"{esc(e.get('symbol', ''))} {e.get('lot')} @ <b>{_px(e.get('entry'))}</b>",
               f"SL {_px(e.get('sl'))} · TP {_px(e.get('tp'))}",
               f"{len(acc)} account(s): {esc(', '.join(acc))}"]
        if sc.get("overall") is not None:
            txt.append(f"Score {sc['overall']:.0f}% (pass {sc.get('passing', 0):.0f}%) · {esc(sc.get('verdict', ''))}")
        if e.get("regime"):
            txt.append(f"Regime: {esc(e['regime'].get('label', ''))}")
        if e.get("paper"):
            txt.append("<i>PAPER</i>")
        n.send("trades", "\n".join(txt), image=png)

    def on_close(self, gid):
        threading.Thread(target=self._close_work, args=(gid,), daemon=True).start()

    def _close_work(self, gid):
        tm = self.hub.trader
        e = tm.journal.entries.get(gid) if tm else None
        if not e:
            return
        png = None
        try:
            png = snapshot.trade_picture(self.hub, e, "exit")
            name = snapshot.save(f"{gid}_exit.png", png)
            if name:
                tm.journal.add_snap(gid, "exit", name)
        except Exception as ex:
            self.hub.note("error", f"snapshot: {ex}")
        n = self.n
        if not n:
            return
        res = e.get("result", "")
        icon = {"WIN": "✅", "LOSS": "❌"}.get(res, "➖")
        by = e.get("pnl_by_account") or {}
        txt = [f"{icon} <b>CLOSED {res}</b> {esc(e.get('symbol', ''))} {'BUY' if e['side'] == 1 else 'SELL'} "
               f"{_px(e.get('entry'))} → {_px(e.get('exit'))}",
               f"P/L <b>{e.get('pnl', 0):+.2f}</b>" + (f" · {e['r']:+g}R" if e.get("r") is not None else "")
               + f" · {e.get('duration_min', 0):.0f} min · {esc(e.get('reason', ''))}"]
        if len(by) > 1:
            txt.append(" · ".join(f"{esc(k)} {v:+.2f}" for k, v in by.items() if v is not None))
        n.send("trades", "\n".join(txt), image=png)

    def on_risk(self, account, reason, closed):
        n = self.n
        if n:
            n.send("risk", f"🛑 <b>{esc(account)}</b>: {esc(reason)}\nNagsara ng {closed} trade(s). "
                           f"Naka-lock hanggang sa susunod na araw.")

    # ------------------------------------------------------------------ decision (READY / BLOCKED)
    def on_decision(self, d, prev):
        """Called by the decision engine when its state changes. READY / BLOCKED alerts, at most one per
        state + side every 10 minutes."""
        n = self.n
        if not n or d["state"] not in ("READY", "BLOCKED"):
            return
        if d["state"] == "BLOCKED" and all(b["code"] in ("ARM_OFF", "HALTED", "READ_ONLY", "MASTER_DISABLED") for b in d["hard_blockers"]):
            return                                         # you are simply not trading: not worth a message
        key = (d["state"], d["side"])
        last = getattr(self, "_dec_sent", {})
        self._dec_sent = last
        if time.time() - last.get(key, 0) < 600:
            return
        last[key] = time.time()
        sym = esc(self.hub.src.symbol)
        if d["state"] == "READY":
            conf = ", ".join(x["label"] for x in d["confirmations"][:6])
            n.send("ready", f"🟢 <b>REYGUN READY</b>\n{sym} <b>{d['side']}</b>\n"
                            f"Score: <b>{d['score']:.0f}%</b> (pass {d['passing_score']:.0f}%)\n"
                            f"Regime: {esc(str(d.get('regime') or '-'))} · Session: {esc(str(d.get('session') or '-'))}\n"
                            f"HTF: {d['bias']['H4']}/{d['bias']['H1']} · Trigger: {esc(d['trigger'])}\n"
                            f"Confirmations: {esc(conf) or '-'}\nBlockers: NONE")
        else:
            rs = d["hard_blockers"]
            lines = []
            for b in rs[:4]:
                extra = f" (current {b['current']}, max {b['maximum']})" if "current" in b else ""
                lines.append(f"• {b['code']}: {esc(b['text'])}{extra}")
            n.send("blocked", f"🔴 <b>REYGUN BLOCKED</b>\n{sym} {d['side']} · Score: {d['score']:.0f}%\n"
                              + "\n".join(lines))

    # ------------------------------------------------------------------ news
    def _watch_news(self):
        n, news = self.n, getattr(self.hub, "news", None)
        if not n or not news or not n.ready() or not n.settings["events"].get("news"):
            return
        w = news.upcoming_warning()
        if not w or not w.get("active") or w["minutes"] < 0:
            return
        key = (w.get("currency"), w.get("title"), w.get("t"))
        if key in self.news_sent:
            return
        self.news_sent.add(key)
        at = getattr(self.hub, "autotrader", None)
        blk = at and at.settings.get("news_block")
        when = time.strftime("%H:%M", self._local(w["t"]))
        n.send("news", f"📰 <b>{esc(w.get('impact', ''))} impact news</b> in {w['minutes']:.0f} min ({when})\n"
                       f"{esc(w.get('currency', ''))} · {esc(w.get('title', ''))}"
                       + (f"\nForecast {esc(str(w.get('forecast')))} · Previous {esc(str(w.get('previous')))}"
                          if w.get("forecast") or w.get("previous") else "")
                       + ("\n<i>Auto trade: walang bagong entry sa news window.</i>" if blk else ""))

    # ------------------------------------------------------------------ end of day
    def build_report(self, day=None):
        hub, tm = self.hub, self.hub.trader
        tz = self.n.settings["tz_offset"] if self.n else 8
        day = day or time.strftime("%Y-%m-%d", self._local())
        loc_day = lambda t: time.strftime("%Y-%m-%d", time.gmtime(t + tz * 3600))
        entries = [e for e in (tm.journal.list(5000) if tm else []) if loc_day(e.get("open_time", 0)) == day
                   or (e.get("close_time") and loc_day(e["close_time"]) == day)]
        closed = [e for e in entries if e.get("status") == "closed" and loc_day(e["close_time"]) == day]
        closed.sort(key=lambda e: e["close_time"])
        pnl = sum(e.get("pnl") or 0 for e in closed)
        rsum = sum(e.get("r") or 0 for e in closed)
        w = sum(1 for e in closed if e.get("result") == "WIN")
        lo = sum(1 for e in closed if e.get("result") == "LOSS")
        L = [f"📊 <b>REYGUN daily report</b> · {day}",
             f"Trades: <b>{len(closed)}</b> ({w}W / {lo}L)" + (f" · win {100 * w / len(closed):.0f}%" if closed else ""),
             f"P/L: <b>{pnl:+.2f}</b> · {rsum:+.2f}R"]
        for e in closed[-8:]:
            L.append(f"  {time.strftime('%H:%M', time.gmtime(e['open_time'] + tz * 3600))} "
                     f"{'BUY' if e['side'] == 1 else 'SELL'} {e.get('kind', 'manual')[:4]} {e.get('result', '')} "
                     + (f"{e['r']:+g}R " if e.get("r") is not None else "") + f"{e.get('pnl', 0):+.2f}")
        if closed:
            best = max(closed, key=lambda e: e.get("pnl") or 0)
            worst = min(closed, key=lambda e: e.get("pnl") or 0)
            L.append(f"Best {best.get('pnl', 0):+.2f} · Worst {worst.get('pnl', 0):+.2f}")
        if tm:
            opn = [g for g in tm.groups.values()]
            if opn:
                L.append(f"Open ngayon: {len(opn)} trade(s)")
            L.append("")
            L.append("<b>Accounts</b>")
            for a in tm.all_accounts():
                v = a.view()
                if v.get("equity") is None:
                    L.append(f"  {esc(v['name'])}: {v['status']}")
                    continue
                L.append(f"  {esc(v['name'])}: equity {v['equity']:,.2f} · day "
                         f"{(v.get('day_pl') or 0):+.2f}" + (f" · 🔒 {esc(v['locked'])}" if v.get("locked") else ""))
        sigs = self.day_sigs.get(day, [])
        if sigs:
            L.append("")
            L.append(f"Signals na na-alert: {len(sigs)}")
        rg = self._regime_txt()
        if rg:
            L.append(f"Regime ngayon: {esc(rg)}")
        dr = getattr(hub, "drift", None)
        if dr and dr.base:
            st = dr.result.get("status", "")
            lv = dr.result.get("live") or {}
            L.append(f"Drift: {st.upper()}" + (f" · live {lv.get('trades', 0)} tr avg {lv.get('avg_r', 0):+.2f}R"
                                               if lv.get("trades") else ""))
        wd = getattr(hub, "watchdog", None)
        if wd:
            ws = wd.state()
            bad = [c["name"] for c in ws["checks"] if c["level"] in ("bad", "warn")]
            L.append("Health: " + ("✅ OK" if not bad else "⚠️ " + esc(", ".join(bad))))
        news = getattr(hub, "news", None)
        if news:
            now = time.time()
            with news.lock:
                cal = [e for e in news.calendar if now <= e["t"] <= now + 24 * 3600 and e.get("impact") == "High"
                       and e.get("currency") in news.settings.get("currencies", ["USD"])]
            if cal:
                L.append("")
                L.append("<b>High impact, susunod na 24 oras</b>")
                for e in cal[:6]:
                    L.append(f"  {time.strftime('%a %H:%M', time.gmtime(e['t'] + tz * 3600))} {esc(e['currency'])} "
                             f"{esc(e['title'])}")
        text = "\n".join(L)
        png = None
        try:
            png = snapshot.day_picture(hub, [e for e in entries if e.get("open_time")], tz_offset=tz)
        except Exception:
            pass
        return text, png

    def send_report(self, force=False):
        text, png = self.build_report()
        day = time.strftime("%Y-%m-%d", self._local())
        try:
            os.makedirs(REPORT_DIR, exist_ok=True)
            import re
            with open(os.path.join(REPORT_DIR, f"{day}.txt"), "w", encoding="utf-8") as f:
                f.write(re.sub(r"<[^>]+>", "", html.unescape(text)))
            if png:
                with open(os.path.join(REPORT_DIR, f"{day}.png"), "wb") as f:
                    f.write(png)
        except OSError:
            pass
        self.last_report = dict(t=time.time(), day=day, text=text, png=bool(png))
        sent = self.n.send("eod", text, image=png, force=force) if self.n else False
        return dict(ok=True, sent=sent, text=text, day=day)

    def _watch_eod(self):
        n = self.n
        if not n or n.settings["eod_hour"] < 0:
            return
        lt = self._local()
        day = time.strftime("%Y-%m-%d", lt)
        if lt.tm_hour == n.settings["eod_hour"] and self.eod_done != day:
            self.eod_done = day
            if n.ready() and n.settings["events"].get("eod"):
                self.send_report()

    # ------------------------------------------------------------------ loop
    def _loop(self):
        time.sleep(5)
        while self.running:
            for fn in (self._watch_signals, self._watch_news, self._watch_eod):
                try:
                    fn()
                except Exception as e:
                    self.hub.note("error", f"alerts {fn.__name__}: {e.__class__.__name__}: {e}")
            time.sleep(2)

    def stop(self):
        self.running = False
