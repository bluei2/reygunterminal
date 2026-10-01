"""
Drift monitor: is the strategy still behaving like the backtest said?

1. Backtest page -> run a backtest (or Test a row) -> "Set as baseline". This stores the backtest's numbers
   (win rate, average R and its spread, longest losing streak, max drawdown, trades per day) and the setup.
2. From then on the monitor collects the LIVE results:
     signals  every Analyser Signals arrow that finished (TP / SL / trail) on the live chart, even if nothing
              was traded (works in paper / testing too)
     trades   the journal's AUTO trades (optionally manual too)
3. Every 30 s it compares:
     losing streak    now vs the backtest's longest       warn > longest, BAD > longest + 2
     drawdown (R)     from the live peak                  warn > backtest max DD, BAD > 1.5x
     average R        z-test once there are enough trades (min_trades)   warn z < -1.65, BAD z < -2.33
     win rate         warn when 15+ points below the backtest (after min_trades)
     setup changed    warn when the current setup is no longer the baseline's setup
   BAD -> Telegram "drift" alert + event log; with "auto pause" ON, AUTO TRADE is switched OFF.
Nothing is ever switched ON by this module.
"""
from __future__ import annotations

import json
import math
import os
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BASE_FILE = os.path.join(HERE, "drift_baseline.json")
LIVE_FILE = os.path.join(HERE, "drift_live.json")
SET_FILE = os.path.join(HERE, "drift_settings.json")
DEFAULTS = dict(source="signals", include_manual=False, min_trades=10, auto_pause=True)
SETUP_KEYS = ("passing", "sl_mode", "atr_sl", "tp_source", "rr", "trail", "max_cautions")


def _load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _save(path, obj):
    try:
        with open(path + ".tmp", "w") as f:
            json.dump(obj, f, indent=1)
        os.replace(path + ".tmp", path)
    except OSError:
        pass


def summarize(rs, days=None):
    """rs = list of R results in time order."""
    n = len(rs)
    if not n:
        return dict(trades=0)
    wins = [r for r in rs if r > 0]
    mean = sum(rs) / n
    sd = math.sqrt(sum((r - mean) ** 2 for r in rs) / (n - 1)) if n > 1 else 1.0
    streak = best = 0
    for r in rs:
        streak = streak + 1 if r <= 0 else 0
        best = max(best, streak)
    cur = 0
    for r in reversed(rs):
        if r <= 0:
            cur += 1
        else:
            break
    eq = peak = dd = 0.0
    for r in rs:
        eq += r
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    gl = -sum(r for r in rs if r < 0)
    return dict(trades=n, win_rate=round(100 * len(wins) / n, 1), avg_r=round(mean, 3), sd_r=round(sd, 3),
                total_r=round(sum(rs), 2), pf=round(sum(wins) / gl, 2) if gl > 0 else None,
                max_streak=best, cur_streak=cur, max_dd_r=round(dd, 2), cur_dd_r=round(eq - peak, 2),
                per_day=round(n / days, 2) if days else None)


class Drift:
    def __init__(self, hub):
        self.hub = hub
        self.settings = dict(DEFAULTS, **_load(SET_FILE, {}))
        self.base = _load(BASE_FILE, None)
        self.live = _load(LIVE_FILE, {})          # str(signal t) -> dict(t, side, r)
        self.result = dict(status="none", checks=[], live={})
        self.last_status = "none"
        self.running = True
        threading.Thread(target=self._loop, daemon=True).start()

    # ------------------------------------------------------------------ control
    def set_baseline(self):
        bt = getattr(self.hub, "backtest", None)
        res = getattr(bt, "result", None) if bt else None
        if not res or not res.get("current"):
            return dict(ok=False, error="run a backtest first")
        cur = res["current"]
        rs = [t["r"] for t in cur["trades"] if t.get("r") is not None]
        if len(rs) < 5:
            return dict(ok=False, error="the backtest has fewer than 5 trades: use a longer period")
        tk = self.hub.last_tick
        self.base = dict(set_time=time.time(), set_srv=int(tk.t_msc // 1000) if tk else int(time.time()),
                         tf=res["tf"], days=res["days"], source=res["source"], setup=cur["setup"],
                         stats=summarize(rs, res["days"]), period=res["period"])
        self.live = {}
        _save(BASE_FILE, self.base)
        _save(LIVE_FILE, self.live)
        self.hub.note("info", f"drift: baseline set from backtest ({len(rs)} trades, avg {self.base['stats']['avg_r']}R)")
        self.evaluate()
        return dict(ok=True, state=self.state())

    def clear(self):
        self.base, self.live = None, {}
        for p in (BASE_FILE, LIVE_FILE):
            try:
                os.remove(p)
            except OSError:
                pass
        self.result = dict(status="none", checks=[], live={})
        return dict(ok=True, state=self.state())

    def update_settings(self, new):
        s = self.settings
        if new.get("source") in ("signals", "trades"):
            s["source"] = new["source"]
        for k in ("include_manual", "auto_pause"):
            if k in new:
                s[k] = bool(new[k])
        if "min_trades" in new:
            try:
                s["min_trades"] = max(5, int(float(new["min_trades"])))
            except (TypeError, ValueError):
                pass
        _save(SET_FILE, s)
        self.evaluate()
        return dict(ok=True, state=self.state())

    # ------------------------------------------------------------------ collect
    def _collect_signals(self):
        b = self.base
        p = next((x for x in self.hub.plugins if x.key == "analyser_signals.AnalyserSignals"), None)
        if p is None or p.error:
            return
        bars = self.hub.src.bars(b["tf"])
        if len(bars) < 40:
            return
        with self.hub.lock:
            sigs = p.obj.signals(bars, b["tf"], self.hub.ctx())
        changed = False
        for s in sigs:
            if s["t"] < b["set_srv"] or s.get("result") == "open" or s.get("r") is None:
                continue
            k = str(s["t"])
            if k not in self.live:
                self.live[k] = dict(t=s["t"], side=s["side"], r=s["r"], result=s["result"])
                changed = True
        if changed:
            _save(LIVE_FILE, self.live)

    def _live_rs(self):
        b, s = self.base, self.settings
        if s["source"] == "signals":
            self._collect_signals()
            rows = sorted(self.live.values(), key=lambda x: x["t"])
            return [x["r"] for x in rows], rows[-1]["t"] if rows else None
        j = self.hub.trader.journal if self.hub.trader else None
        if j is None:
            return [], None
        kinds = {"auto", "manual"} if s["include_manual"] else {"auto"}
        rows = [e for e in j.list(5000) if e.get("status") == "closed" and e.get("r") is not None
                and e.get("open_time", 0) >= b["set_time"] and e.get("kind", "manual") in kinds]
        rows.sort(key=lambda e: e["close_time"])
        return [e["r"] for e in rows], None

    # ------------------------------------------------------------------ evaluate
    def evaluate(self):
        b, s = self.base, self.settings
        if not b:
            self.result = dict(status="none", checks=[], live={})
            return self.result
        rs, _ = self._live_rs()
        days = max(1e-6, (time.time() - b["set_time"]) / 86400)
        L = summarize(rs, days)
        B = b["stats"]
        checks = []

        def add(name, level, text):
            checks.append(dict(name=name, level=level, text=text))

        n = L.get("trades", 0)
        if n == 0:
            add("Data", "info", "wala pang natapos na trade/signal mula nang i-set ang baseline")
        else:
            cs, ms = L["cur_streak"], B["max_streak"]
            add("Losing streak", "bad" if cs > ms + 2 else "warn" if cs > ms else "ok",
                f"{cs} talo sunod-sunod ngayon · backtest pinakamahaba {ms}")
            dd, bdd = abs(L["cur_dd_r"]), abs(B["max_dd_r"]) or 1.0
            add("Drawdown", "bad" if dd > 1.5 * bdd else "warn" if dd > bdd else "ok",
                f"{dd:.1f}R mula sa peak · backtest max {bdd:.1f}R")
            if n >= s["min_trades"]:
                se = (B["sd_r"] or 1.0) / math.sqrt(n)
                z = (L["avg_r"] - B["avg_r"]) / se if se else 0.0
                add("Average R", "bad" if z < -2.33 else "warn" if z < -1.65 else "ok",
                    f"live {L['avg_r']:+.2f}R vs backtest {B['avg_r']:+.2f}R (z {z:+.1f}, {n} trades)")
                dw = L["win_rate"] - B["win_rate"]
                add("Win rate", "warn" if dw < -15 else "ok", f"live {L['win_rate']}% vs backtest {B['win_rate']}%")
            else:
                add("Average R", "info", f"{n}/{s['min_trades']} trades pa bago ang statistical test "
                                         f"(live {L['avg_r']:+.2f}R, backtest {B['avg_r']:+.2f}R)")
        try:
            cur = self.hub.backtest.current_setup() if self.hub.backtest else {}
            diff = [k for k in SETUP_KEYS if k in cur and k in b["setup"] and cur[k] != b["setup"][k]]
            if diff:
                add("Setup", "warn", "iba na ang setup kaysa sa baseline: " + ", ".join(diff) + " (mag-backtest ulit)")
        except Exception:
            pass
        order = {"bad": 3, "warn": 2, "ok": 1, "info": 0}
        worst = max((c["level"] for c in checks), key=lambda x: order[x], default="info")
        status = {"bad": "bad", "warn": "warn"}.get(worst, "ok" if n else "waiting")
        self.result = dict(status=status, checks=checks, live=L, updated=time.time())
        if status != self.last_status:
            if status == "bad":
                self._alarm(checks)
            elif status == "ok" and self.last_status == "bad":
                self.hub.note("info", "drift: back to normal")
            self.last_status = status
        return self.result

    def _alarm(self, checks):
        bad = [c for c in checks if c["level"] == "bad"]
        txt = "; ".join(f"{c['name']}: {c['text']}" for c in bad)
        self.hub.note("error", f"DRIFT: live results worse than the backtest · {txt}")
        paused = False
        at = getattr(self.hub, "autotrader", None)
        if self.settings["auto_pause"] and at is not None and at.enabled:
            at.set_enabled(False)
            paused = True
        n = getattr(self.hub, "notifier", None)
        if n:
            n.send("drift", "⚠️ <b>DRIFT MONITOR</b>\nMas mahina ang live kaysa sa backtest.\n"
                   + "\n".join(f"• {c['name']}: {c['text']}" for c in bad)
                   + ("\n\n⏸ AUTO TRADE naka-OFF na." if paused else ""))

    def _loop(self):
        time.sleep(10)
        while self.running:
            try:
                self.evaluate()
            except Exception as e:
                self.hub.note("error", f"drift: {e.__class__.__name__}: {e}")
            time.sleep(30)

    def state(self):
        return dict(settings=self.settings, baseline=self.base, **self.result)

    def stop(self):
        self.running = False
