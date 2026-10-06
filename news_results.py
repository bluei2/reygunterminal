"""
NEWS RESULTS (v2.8.9, Eugene: "makita ntin gaano ka accurate ang forecast sa news").

For every warn-level news event (e.g. USD High) that was released while the app was running it records:
  * forecast / previous / actual from the calendar (actual is filled in when the calendar provides it),
    and how close the forecast was: surprise = actual - forecast, TUGMA when within 5 % (min 0.05);
  * the gold reaction: price at the release, +5 / +10 / +30 min, and the high / low of the first 10 minutes;
  * what the NEWS trades did (journal kind "news", opened -5 .. +30 min around the release): side, result, P/L.
Read-only: it never sends an order. Saved to news_results.json (last 300 events).
"""
from __future__ import annotations

import json
import os
import re
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
FILE = os.path.join(HERE, "news_results.json")
MARKS = (5, 10, 30)                 # minutes after the release
KEEP = 300


def num(text):
    """'0.3%' -> 0.3 · '187K' -> 187000 · '-1.2B' -> -1.2e9 · '' / 'n/a' -> None"""
    m = re.match(r"^\s*([<>]?)\s*(-?\d+(?:\.\d+)?)\s*([%KMBT]?)", str(text or ""), re.I)
    if not m:
        return None
    v = float(m.group(2))
    return v * {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}.get(m.group(3).upper(), 1.0)


def compare(forecast, actual):
    """-> dict(surprise, pct, verdict) or None when either value is missing."""
    f, a = num(forecast), num(actual)
    if f is None or a is None:
        return None
    d = a - f
    tol = max(abs(f) * 0.05, 0.05 if abs(f) < 100 else 0.0)
    verdict = "TUGMA" if abs(d) <= tol else ("MAS MATAAS" if d > 0 else "MAS MABABA")
    return dict(surprise=round(d, 4), pct=round(d / abs(f) * 100, 1) if f else None, verdict=verdict)


class NewsResults:
    def __init__(self, hub, path=None, start=True):
        self.hub = hub
        self.path = path or FILE
        self.lock = threading.Lock()
        self.events = {}
        try:
            with open(self.path) as f:
                for e in json.load(f):
                    self.events[e["key"]] = e
        except (OSError, ValueError, KeyError, TypeError):
            pass
        self.running = start
        if start:
            threading.Thread(target=self._loop, daemon=True, name="news-results").start()

    def _loop(self):
        while self.running:
            try:
                self.update()
            except Exception as e:                                  # never stop the recorder
                try:
                    self.hub.note("error", f"news results: {e.__class__.__name__}: {e}")
                except Exception:
                    pass
            time.sleep(5)

    def _mid(self):
        tk = getattr(self.hub, "last_tick", None)
        return round((tk.bid + tk.ask) / 2, 2) if tk else None

    def update(self, now=None):
        nf = getattr(self.hub, "news", None)
        if nf is None:
            return
        now = now or time.time()
        s = nf.settings
        cur, imp = set(s.get("currencies") or []), set(s.get("warn_impacts") or [])
        with nf.lock:
            cal = [dict(e) for e in nf.calendar if e.get("currency") in cur and e.get("impact") in imp]
        px = self._mid()
        changed = False
        with self.lock:
            for c in cal:
                key = f"{c['t']}|{c['currency']}|{c['title']}"
                e = self.events.get(key)
                if e is None:
                    # only events released while the app runs (first 2 minutes): their reaction can be measured
                    if not (0 <= now - c["t"] <= 120) or px is None:
                        continue
                    e = self.events[key] = dict(key=key, t=c["t"], title=c["title"], currency=c["currency"],
                                                impact=c["impact"], forecast=c.get("forecast", ""),
                                                previous=c.get("previous", ""), actual=c.get("actual", ""),
                                                px0=px, hi=px, lo=px, marks={}, trades=[])
                    changed = True
                if c.get("actual") and c["actual"] != e.get("actual"):
                    e["actual"] = c["actual"]
                    changed = True
            for e in self.events.values():
                age = (now - e["t"]) / 60
                if px is not None and 0 <= age <= 10.5:
                    if px > e["hi"] or px < e["lo"]:
                        e["hi"], e["lo"] = max(e["hi"], px), min(e["lo"], px)
                        changed = True
                for m in MARKS:
                    if str(m) not in e["marks"] and px is not None and m <= age <= m + 5:
                        e["marks"][str(m)] = px
                        changed = True
                if age <= 120:
                    tr = self._trades(e)
                    if tr != e.get("trades"):
                        e["trades"] = tr
                        changed = True
            if changed:
                self._save()

    def _trades(self, e):
        tm = getattr(self.hub, "trader", None)
        j = getattr(tm, "journal", None) if tm else None
        if j is None:
            return e.get("trades") or []
        out = []
        for x in list(j.entries.values()):
            if x.get("kind") != "news" or not (e["t"] - 300 <= (x.get("open_time") or 0) <= e["t"] + 1800):
                continue
            out.append(dict(gid=x.get("gid"), side=x.get("side"), what="TREND" if x.get("strategy") == "news_trend" else "CONSO",
                            result=x.get("result") or "OPEN", pnl=x.get("pnl")))
        return sorted(out, key=lambda r: r["gid"] or "")

    def _save(self):
        rows = sorted(self.events.values(), key=lambda x: x["t"])[-KEEP:]
        self.events = {r["key"]: r for r in rows}
        try:
            tmp = self.path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(rows, f)
            os.replace(tmp, self.path)
        except OSError:
            pass

    def view(self):
        with self.lock:
            rows = sorted((dict(e) for e in self.events.values()), key=lambda x: -x["t"])
        n_cmp = tugma = 0
        moves, n_tr, wins, pnl = [], 0, 0, 0.0
        for r in rows:
            r["cmp"] = compare(r.get("forecast"), r.get("actual"))
            if r["cmp"]:
                n_cmp += 1
                tugma += r["cmp"]["verdict"] == "TUGMA"
            p10 = r["marks"].get("10")
            if p10 is not None and r.get("px0"):
                r["move10"] = round(p10 - r["px0"], 2)
                moves.append(abs(r["move10"]))
            r["range10"] = round(r["hi"] - r["lo"], 2) if r.get("hi") is not None else None
            for t in r.get("trades") or []:
                if t["result"] in ("WIN", "LOSS", "BE"):
                    n_tr += 1
                    wins += t["result"] == "WIN"
                    pnl += t.get("pnl") or 0.0
        summary = dict(events=len(rows), compared=n_cmp, tugma=tugma,
                       forecast_acc=round(tugma / n_cmp * 100) if n_cmp else None,
                       avg_move10=round(sum(moves) / len(moves), 2) if moves else None,
                       trades=n_tr, wins=wins, win_rate=round(wins / n_tr * 100) if n_tr else None, pnl=round(pnl, 2))
        return dict(summary=summary, rows=rows[:100])

    def stop(self):
        self.running = False
