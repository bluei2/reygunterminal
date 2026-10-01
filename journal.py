"""
Trade journal for SuperChart trades.

Every trade group is recorded when it opens (with the analyser score + group breakdown at that moment)
and completed when it closes (TP / SL / manual / mirror, exit price, P/L per account, R multiple).
Stored as JSON lines: journal.jsonl (live) or journal_paper.jsonl (paper). CSV export on demand.
"""
from __future__ import annotations

import csv
import io
import json
import os
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))


def score_snapshot(read) -> dict:
    """Freeze the analyser read at entry time."""
    if not read:
        return {}
    return dict(
        overall=read.overall, passing=read.passing, passed=read.passed, bias=read.bias, trigger=read.trigger,
        verdict=read.verdict,
        groups=[dict(category=g["category"], agree=g["agree"], total=g["total"], pct=g["pct"], weight=g["weight"],
                     items=[dict(name=i["name"], dir=i["dir"]) for i in g["items"]]) for g in read.groups],
    )


def _news_txt(n):
    if not n:
        return ""
    m = n.get("minutes", 0)
    when = f"in {m:.0f}m" if m >= 0 else f"{-m:.0f}m ago"
    return f"{n.get('currency')} {n.get('title')} ({n.get('impact')}) {when}" + (" [WARNING WINDOW]" if n.get("active") else "")


class Journal:
    def __init__(self, paper: bool):
        self.path = os.path.join(HERE, "journal_paper.jsonl" if paper else "journal.jsonl")
        self.lock = threading.Lock()
        self.entries: dict[str, dict] = {}
        self._load()

    # ------------------------------------------------------------------ storage
    def _load(self):
        try:
            with open(self.path) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        e = json.loads(line)
                        self.entries[e["gid"]] = e
        except (OSError, ValueError):
            pass

    def _save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            for e in sorted(self.entries.values(), key=lambda x: x.get("open_time", 0)):
                f.write(json.dumps(e) + "\n")
        os.replace(tmp, self.path)

    # ------------------------------------------------------------------ events
    def opened(self, gid, symbol, side, lot, entry, sl, tp, sl_dist, tp_dist, accounts, snapshot, paper, news=None,
               kind="manual", regime=None, t_srv=None, lifecycle=None, request_id=None, **extra):
        with self.lock:
            self.entries[gid] = dict(
                gid=gid, open_time=time.time(), symbol=symbol, side=side, lot=lot, entry=entry, sl=sl, tp=tp,
                sl_dist=sl_dist, tp_dist=tp_dist, accounts=accounts, score=snapshot, paper=paper,
                status="open", partials=[], sl_changes=[], news=news, kind=kind, regime=regime,
                t_srv_open=t_srv, snaps={}, lifecycle=list(lifecycle or []), request_id=request_id, **extra)
            self._save()

    def lifecycle(self, gid, state, note=""):
        """Append one lifecycle transition (REQUESTED ... CLOSED) to a trade."""
        with self.lock:
            e = self.entries.get(gid)
            if e is not None:
                e.setdefault("lifecycle", []).append(dict(t=time.time(), state=state, note=note))
                self._save()

    def update(self, gid, **fields):
        with self.lock:
            e = self.entries.get(gid)
            if e is not None:
                e.update(fields)
                self._save()

    def failed(self, gid, symbol, side, lot, sl_dist, tp_dist, kind, error, lifecycle, paper, request_id=None):
        """An order that was not filled on the master (kept so every attempt is visible)."""
        with self.lock:
            self.entries[gid] = dict(
                gid=gid, open_time=time.time(), close_time=time.time(), symbol=symbol, side=side, lot=lot,
                entry=None, sl=None, tp=None, sl_dist=sl_dist, tp_dist=tp_dist, accounts=[], score={}, paper=paper,
                status="failed", result="FAILED", reason=f"order failed: {error}"[:160], partials=[], sl_changes=[],
                kind=kind, lifecycle=list(lifecycle or []), request_id=request_id, pnl=None, r=None)
            self._save()

    def add_snap(self, gid, phase, name):
        with self.lock:
            e = self.entries.get(gid)
            if e:
                e.setdefault("snaps", {})[phase] = name
                self._save()

    def sl_changed(self, gid, sl, tp, source):
        with self.lock:
            e = self.entries.get(gid)
            if e:
                e["sl_changes"].append(dict(time=time.time(), sl=sl, tp=tp, source=source))
                self._save()

    def partial(self, gid, fraction, source, pnl=None):
        with self.lock:
            e = self.entries.get(gid)
            if e:
                e["partials"].append(dict(time=time.time(), fraction=round(fraction, 3), source=source, pnl=pnl))
                self._save()

    def closed(self, gid, exit_price, reason, pnl_by_account: dict, source, t_srv=None, **extra):
        with self.lock:
            e = self.entries.get(gid)
            if not e or e.get("status") == "closed":
                return
            total = round(sum(v for v in pnl_by_account.values() if v is not None), 2)
            r_mult = None
            if e.get("sl_dist") and exit_price:
                r_mult = round((exit_price - e["entry"]) * e["side"] / e["sl_dist"], 2)
            e.update(status="closed", close_time=time.time(), exit=exit_price, reason=reason, source=source,
                     pnl=total, pnl_by_account=pnl_by_account, r=r_mult,
                     result="WIN" if total > 0.005 else "LOSS" if total < -0.005 else "BE",
                     duration_min=round((time.time() - e["open_time"]) / 60, 1), t_srv_close=t_srv)
            e.update(extra)
            e.setdefault("lifecycle", []).append(dict(t=time.time(), state="CLOSED", note=reason))
            self._save()

    # ------------------------------------------------------------------ views
    def list(self, limit=300):
        with self.lock:
            return sorted(self.entries.values(), key=lambda x: x.get("open_time", 0), reverse=True)[:limit]

    def stats(self):
        with self.lock:
            closed = [e for e in self.entries.values() if e.get("status") == "closed"]

        def block(rows):
            n = len(rows)
            wins = [e for e in rows if e["result"] == "WIN"]
            losses = [e for e in rows if e["result"] == "LOSS"]
            gp = sum(e["pnl"] for e in wins)
            gl = -sum(e["pnl"] for e in losses)
            rs = [e["r"] for e in rows if e.get("r") is not None]
            return dict(trades=n, wins=len(wins), losses=len(losses),
                        win_rate=round(100 * len(wins) / n, 1) if n else None,
                        pnl=round(sum(e["pnl"] for e in rows), 2),
                        avg_r=round(sum(rs) / len(rs), 2) if rs else None,
                        profit_factor=round(gp / gl, 2) if gl > 0 else None)

        by_pass = dict(passed=block([e for e in closed if (e.get("score") or {}).get("passed")]),
                       not_passed=block([e for e in closed if not (e.get("score") or {}).get("passed")]))
        by_kind = dict(manual=block([e for e in closed if e.get("kind", "manual") == "manual"]),
                       auto=block([e for e in closed if e.get("kind") == "auto"]),
                       news=block([e for e in closed if e.get("kind") == "news"]))
        reasons = {}
        for e in closed:
            reasons[e.get("reason", "?")] = reasons.get(e.get("reason", "?"), 0) + 1
        by_regime = {}
        for lab in sorted({(e.get("regime") or {}).get("label") or "unknown" for e in closed}):
            by_regime[lab] = block([e for e in closed if ((e.get("regime") or {}).get("label") or "unknown") == lab])
        in_news = [e for e in closed if (e.get("news") or {}).get("active")]
        by_news = dict(in_window=block(in_news), outside=block([e for e in closed if e not in in_news]))
        return dict(all=block(closed), by_pass=by_pass, by_news=by_news, by_kind=by_kind, by_regime=by_regime, reasons=reasons,
                    open=sum(1 for e in self.entries.values() if e.get("status") == "open"))

    def series(self):
        """Every closed trade in time order, compact (for the equity curve and the P/L calendar)."""
        with self.lock:
            closed = [e for e in self.entries.values() if e.get("status") == "closed" and e.get("close_time")]
        closed.sort(key=lambda e: e["close_time"])
        return [dict(t=int(e["close_time"]), ot=int(e.get("open_time") or e["close_time"]), gid=e["gid"],
                     pnl=e.get("pnl") or 0.0, r=e.get("r"), regime=(e.get("regime") or {}).get("label"),
                     side=e.get("side"), result=e.get("result"), kind=e.get("kind", "manual"),
                     acc={k: v for k, v in (e.get("pnl_by_account") or {}).items() if v is not None})
                for e in closed]

    def csv(self) -> str:
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["open_time", "close_time", "symbol", "side", "lot", "entry", "sl", "tp", "exit", "result",
                    "reason", "pnl_total", "r", "duration_min", "score_overall", "passing", "passed", "trigger",
                    "verdict", "groups", "accounts", "pnl_by_account", "partials", "paper", "news_at_entry", "kind", "regime"])
        fmt = lambda t: time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t)) if t else ""
        for e in sorted(self.entries.values(), key=lambda x: x.get("open_time", 0)):
            sc = e.get("score") or {}
            groups = "; ".join(f"{g['category']} {g['agree']}/{g['total']} {g['pct']:.0f}% x{g['weight']:g}"
                               for g in sc.get("groups", []))
            w.writerow([fmt(e.get("open_time")), fmt(e.get("close_time")), e.get("symbol"),
                        "BUY" if e.get("side") == 1 else "SELL", e.get("lot"), e.get("entry"), e.get("sl"),
                        e.get("tp"), e.get("exit"), e.get("result", "OPEN"), e.get("reason", ""), e.get("pnl", ""),
                        e.get("r", ""), e.get("duration_min", ""), sc.get("overall"), sc.get("passing"),
                        sc.get("passed"), sc.get("trigger"), sc.get("verdict"), groups,
                        "|".join(e.get("accounts", [])),
                        "|".join(f"{k}:{v}" for k, v in (e.get("pnl_by_account") or {}).items()),
                        len(e.get("partials", [])), e.get("paper"), _news_txt(e.get("news")), e.get("kind", "manual"),
                        (e.get("regime") or {}).get("label", "")])
        return out.getvalue()
