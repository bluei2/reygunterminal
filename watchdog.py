"""
Health watchdog: checks every 5 s that everything the terminal needs is alive, tries to fix what it can,
and warns you (event log + Telegram "health") when something goes wrong and again when it recovers.

  Data feed        ticks still arriving from the master MT5 (skipped when gold is closed: weekend and the daily
                   break around 21:00-22:05 UTC)
  Master MT5       terminal reachable, connected to the broker, Algo Trading button ON (needed to send orders)
  Master account   account info still readable
  Followers        each follower process alive, connected to its broker, Algo Trading ON
                   -> a dead / disconnected follower is reconnected automatically (at most every 2 min)
  App loops        the chart/analyser loop and the trading loop are still running (not frozen)

A problem must last 2 checks in a row (10 s) before it alerts, so short hiccups do not spam you.
The watchdog never opens, closes or changes a trade.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

LEVELS = {"ok": 0, "na": 0, "warn": 1, "bad": 2}


from market_hours import gold_open  # noqa: E402  (shared helper)


class Watchdog:
    def __init__(self, hub, stale_minutes=3.0):
        self.hub = hub
        self.stale = stale_minutes * 60
        self.checks = {}           # name -> dict(level, text, since, bad_count, alerted)
        self.retry = {}            # follower id -> last reconnect try
        self.running = True
        self.started = time.time()
        threading.Thread(target=self._loop, daemon=True).start()

    # ------------------------------------------------------------------ helpers
    def _set(self, name, level, text):
        c = self.checks.get(name)
        now = time.time()
        if c is None:
            c = self.checks[name] = dict(name=name, level=level, text=text, since=now, bad_count=0, alerted=False)
        if level != c["level"]:
            c["since"] = now
        c["level"], c["text"] = level, text
        if level == "bad":
            c["bad_count"] += 1
            if c["bad_count"] == 2 and not c["alerted"]:
                c["alerted"] = True
                self.hub.note("error", f"HEALTH: {name}: {text}")
                self._tg(f"🔴 <b>{name}</b>\n{text}")
        else:
            if c["alerted"] and level in ("ok", "na"):
                self.hub.note("info", f"HEALTH: {name} recovered")
                self._tg(f"🟢 <b>{name}</b> OK na ulit\n{text}")
            c["bad_count"], c["alerted"] = 0, False

    def _tg(self, text):
        n = getattr(self.hub, "notifier", None)
        if n:
            n.send("health", "🩺 HEALTH · " + text)

    # ------------------------------------------------------------------ checks
    def check(self):
        hub, now = self.hub, time.time()
        src = hub.src
        live = bool(getattr(src, "live", False))
        warm = now - self.started > 20                       # give everything time to start

        # app loops
        self._set("Chart loop", "bad" if now - getattr(hub, "last_step", now) > 30 else "ok",
                  "analyser/chart loop is running" if now - getattr(hub, "last_step", now) <= 30
                  else f"frozen for {now - hub.last_step:.0f}s")
        tm = hub.trader
        if tm is not None:
            lag = now - getattr(tm, "last_cycle", now)
            self._set("Trading loop", "bad" if lag > 30 else "ok",
                      "positions/sync loop is running" if lag <= 30 else f"frozen for {lag:.0f}s")

        # data feed
        if live:
            if not gold_open():
                self._set("Data feed", "na", "market closed (weekend / daily break)")
            else:
                last = getattr(hub, "last_tick_wall", 0) or self.started
                age = now - last
                lvl = "bad" if age > self.stale and warm else "warn" if age > 60 and warm else "ok"
                self._set("Data feed", lvl, f"last tick {age:.0f}s ago" + ("" if lvl == "ok" else " · walang dumarating na presyo"))
        else:
            self._set("Data feed", "na", f"{src.name}: simulated data")

        # master MT5 terminal
        if live:
            try:
                with hub.lock:
                    t = src.mt5.terminal_info()
                if t is None:
                    self._set("Master MT5", "bad", "MT5 terminal not reachable, reconnecting…")
                    self._reconnect_master()
                elif not getattr(t, "connected", True):
                    self._set("Master MT5", "bad", "MT5 is not connected to the broker server (internet / login)")
                elif not getattr(t, "trade_allowed", True):
                    self._set("Master MT5", "warn" if tm is None else "bad",
                              "Algo Trading button is OFF in MT5: orders will be rejected")
                else:
                    self._set("Master MT5", "ok", f"connected · Algo Trading ON · ping {getattr(t, 'ping_last', 0) / 1000:.0f} ms")
            except Exception as e:
                self._set("Master MT5", "bad", f"{e.__class__.__name__}: {e}")
        else:
            self._set("Master MT5", "na", "not used (demo / replay / paper)")

        # accounts
        if tm is not None:
            accts = [tm.master] + [f for f in tm.followers if f.cfg.get("enabled", True)]
            for a in accts:
                name = self._label(a)
                if tm.paper:
                    self._set(name, "na" if a.role == "master" else "ok", "paper account")
                    continue
                if a.role == "follower" and not a.connected:
                    self._set(name, "bad", f"not connected ({a.error or a.status})")
                    self._reconnect_follower(a)
                    continue
                be = a.backend
                if a.role == "follower" and hasattr(be, "proc") and not be.proc.is_alive():
                    self._set(name, "bad", "worker process died, reconnecting…")
                    a.status = "error"
                    self._reconnect_follower(a)
                    continue
                age = now - (a.last_ok or 0)
                if a.last_ok and age > 20:
                    self._set(name, "bad", f"account not readable for {age:.0f}s")
                    if a.role == "follower" and age > 60:
                        self._reconnect_follower(a)
                    continue
                if not a.last_ok:
                    if warm:
                        self._set(name, "warn", "waiting for the first account read")
                    continue
                info = a.info
                if not info.get("terminal_connected", True):
                    self._set(name, "bad", "MT5 not connected to the broker server")
                elif not info.get("trade_allowed", True):
                    self._set(name, "bad", "Algo Trading is OFF in this MT5: orders will be rejected")
                elif a.locked:
                    self._set(name, "warn", f"locked: {a.locked}")
                else:
                    self._set(name, "ok", f"#{info.get('login')} · equity {info.get('equity', 0):,.2f}")
            # forget followers that were removed
            names = {self._label(a) for a in accts}
            for k in [k for k in self.checks if k.startswith(("Follower ", "Master account")) and k not in names]:
                self.checks.pop(k, None)

    @staticmethod
    def _label(a):
        if a.role == "master":
            return "Master account" + ("" if a.name.lower() == "master" else f" ({a.name})")
        return f"Follower {a.name}"

    def _reconnect_master(self):
        src = self.hub.src
        try:
            with self.hub.lock:
                try:
                    src.mt5.shutdown()
                except Exception:
                    pass
                src._connect()
                src.mt5.symbol_select(src.symbol, True)
            self.hub.note("info", "watchdog: master MT5 reconnected")
        except Exception as e:
            self.hub.note("error", f"watchdog: master reconnect failed: {e}")

    def _reconnect_follower(self, a):
        now = time.time()
        if now - self.retry.get(a.id, 0) < 120 or a.status == "connecting":
            return
        self.retry[a.id] = now
        self.hub.note("info", f"watchdog: reconnecting {a.name}…")
        try:
            self.hub.trader.reconnect(a.id)
        except Exception as e:
            self.hub.note("error", f"watchdog: reconnect {a.name} failed: {e}")

    # ------------------------------------------------------------------ loop / view
    def _loop(self):
        time.sleep(3)
        while self.running:
            try:
                self.check()
            except Exception as e:
                self.hub.note("error", f"watchdog: {e.__class__.__name__}: {e}")
            time.sleep(5)

    def state(self):
        rows = sorted(self.checks.values(), key=lambda c: (-LEVELS[c["level"]], c["name"]))
        worst = max((LEVELS[c["level"]] for c in rows), default=0)
        return dict(status=["ok", "warn", "bad"][worst], checks=[{k: c[k] for k in ("name", "level", "text", "since")}
                                                                   for c in rows], market_open=gold_open())

    def stop(self):
        self.running = False
