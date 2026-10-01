"""
Exit guard: protects an OPEN SuperChart trade when the trend turns against it before TP / SL.

Trigger (settings.trigger)
  trend_change  a TREND CHANGE against the trade (trend line broken with a CLOSE outside + CHoCH)   <- default
  tl_break      a confirmed trend line break (TL✓) against the trade
Only events on bars AFTER the trade was opened count, and only fresh ones (last 3 closed bars of `tf`), so an old
event is never acted on when the guard is switched on later.

Action (settings.action)
  close      close the whole trade on the master and every follower                                <- default
  partial    close `partial` (e.g. 50%) everywhere, the rest runs to SL / TP
  breakeven  move SL to the entry everywhere (only when the trade is in profit, otherwise alert only)
  alert      message only (event log + Telegram)

Safety: acts ONLY while trading is ARMED (ARM is always OFF when the app starts). When disarmed it only alerts.
Every action is written to the journal ("Exit guard: TREND CHANGE DOWN") and sent to Telegram (trades).
"""
from __future__ import annotations

import json
import os
import threading
import time
from collections import deque

from engine import TF_SECONDS

HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(HERE, "exit_guard_settings.json")
DEFAULTS = dict(enabled=True, trigger="trend_change", action="close", tf="M15", scope="all", partial=0.5, fresh_bars=3)


def load_settings():
    s = dict(DEFAULTS)
    try:
        with open(SETTINGS_FILE) as f:
            for k, v in json.load(f).items():
                if k in s:
                    s[k] = v
    except (OSError, ValueError):
        pass
    return s


class ExitGuard:
    def __init__(self, hub):
        self.hub = hub
        self.settings = load_settings()
        self.done = set()                  # (gid, event time)
        self.log = deque(maxlen=30)
        self.last_bar = None
        self.running = True
        threading.Thread(target=self._loop, daemon=True).start()

    def update_settings(self, new):
        s = self.settings
        if "enabled" in new:
            s["enabled"] = bool(new["enabled"])
        if new.get("trigger") in ("trend_change", "tl_break"):
            s["trigger"] = new["trigger"]
        if new.get("action") in ("close", "partial", "breakeven", "alert"):
            s["action"] = new["action"]
        if new.get("tf") in ("M5", "M15", "M30", "H1"):
            s["tf"] = new["tf"]
        if new.get("scope") in ("all", "auto", "manual"):
            s["scope"] = new["scope"]
        if "partial" in new:
            try:
                s["partial"] = max(0.1, min(0.9, float(new["partial"])))
            except (TypeError, ValueError):
                pass
        try:
            with open(SETTINGS_FILE, "w") as f:
                json.dump(s, f, indent=2)
        except OSError:
            pass
        self.last_bar = None
        self.hub.note("info", f"exit guard {'ON' if s['enabled'] else 'OFF'}: {s['trigger']} -> {s['action']} ({s['tf']})")
        return dict(ok=True, state=self.state())

    def _note(self, text, kind="info", **kw):
        self.log.appendleft(dict(t=time.time(), text=text, kind=kind, **kw))
        self.hub.note(kind, text)

    # ------------------------------------------------------------------ events
    def _events(self, bars):
        p = next((x for x in self.hub.plugins if x.key == "trendline_breakout.TrendlineBreakout" and not x.error), None)
        if p is None:
            return []
        with self.hub.lock:
            if self.settings["trigger"] == "trend_change":
                return [dict(t=e["t"], side=e["side"], what=f"TREND CHANGE {'UP' if e['side'] == 1 else 'DOWN'}",
                             why=e["why_for"]) for e in p.obj.trend_changes(bars)]
            return [dict(t=e["t"], side=e["side"], what=f"TL break {'UP' if e['side'] == 1 else 'DOWN'}",
                         why=e["why"]) for e in p.obj.confirmed_breaks(bars)]

    def _loop(self):
        time.sleep(8)
        while self.running:
            try:
                self._cycle()
            except Exception as e:
                self.hub.note("error", f"exit guard: {e.__class__.__name__}: {e}")
            time.sleep(2)

    def _cycle(self):
        s, tm = self.settings, self.hub.trader
        if not s["enabled"] or tm is None or not tm.groups:
            return
        tf = s["tf"]
        bars = self.hub.src.bars(tf)
        if len(bars) < 80 or bars[-1].t == self.last_bar:
            return
        self.last_bar = bars[-1].t
        evs = self._events(bars)
        if not evs:
            return
        sec = TF_SECONDS[tf]
        fresh_from = bars[-1 - int(s["fresh_bars"])].t
        for gid, g in list(tm.groups.items()):
            e = tm.journal.entries.get(gid) or {}
            kind = e.get("kind", "manual")
            if s["scope"] == "auto" and kind != "auto" or s["scope"] == "manual" and kind == "auto":
                continue
            t_open = e.get("t_srv_open") or int(g.created + (self.hub.utc_offset or 0) * 3600)
            open_bar = t_open - t_open % sec
            for ev in evs:
                if ev["side"] != -g.side or ev["t"] <= open_bar or ev["t"] < fresh_from:
                    continue
                key = (gid, ev["t"])
                if key in self.done:
                    continue
                self.done.add(key)
                self._act(tm, gid, g, ev)
                break

    def _act(self, tm, gid, g, ev):
        s = self.settings
        word = "BUY" if g.side == 1 else "SELL"
        reason = f"Exit guard: {ev['what']}"
        act = s["action"]
        mp = tm.master.pos(g.legs.get("master"))
        pnl = mp["pnl"] if mp else None
        head = f"{word} trade {gid}: {ev['what']} ({s['tf']}) against it"
        if act != "alert" and not tm.armed:
            act = "alert"
            head += " · DISARMED, walang ginawa"
        result = ""
        if act == "close":
            r = tm.close_group(gid, 1.0, source=f"guard:{reason}")
            result = "CLOSED" if r.get("ok") else f"close failed: {r.get('results')}"
        elif act == "partial":
            r = tm.close_group(gid, s["partial"], source=f"guard:{reason}")
            result = f"closed {s['partial']:.0%}" if r.get("ok") else "partial close failed"
        elif act == "breakeven":
            if pnl is not None and pnl > 0:
                r = tm.breakeven(gid)
                result = "SL -> breakeven" if r.get("ok") else "breakeven failed"
            else:
                result = "talo pa, kaya alert lang (hindi ma-breakeven)"
        else:
            result = "alert lang"
        text = f"🛡 EXIT GUARD · {head} → {result}" + (f" · P/L {pnl:+.2f}" if pnl is not None else "")
        self._note(text, "warn" if act != "alert" else "info", gid=gid, action=act)
        n = getattr(self.hub, "notifier", None)
        if n:
            n.send("trades", f"🛡 <b>EXIT GUARD</b>\n{word} trade {gid}: <b>{ev['what']}</b> ({s['tf']}) kontra sa trade\n"
                             + (f"P/L {pnl:+.2f}\n" if pnl is not None else "") + f"→ <b>{result}</b>")

    def state(self):
        tm = self.hub.trader
        return dict(settings=self.settings, armed=bool(tm and tm.armed), open=len(tm.groups) if tm else 0,
                    log=list(self.log)[:15],
                    plugin=any(x.key == "trendline_breakout.TrendlineBreakout" for x in self.hub.plugins))

    def stop(self):
        self.running = False
