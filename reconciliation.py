"""
Copier reconciliation: EXPECTED (master) vs ACTUAL (followers), every 5 s.

For every open SuperChart group, on every follower leg (only with FRESH reads of both master and follower):
  side      must equal the master's                                  -> mismatch = CRITICAL: alert + pause follower
  volume    follower lot x (master volume now / master volume at entry), within half a lot step
            follower bigger -> repair: partial close the extra;  smaller -> report only (never add risk)
  SL / TP   same $ distance from its own entry as the master's       -> repair: modify to the master's distances
  leg gone  closed by its own SL / TP (normal with different prices)  -> info only
Orphans: a REYGUN position (magic + comment RGSC-<gid>) on any account whose group no longer exists
  -> seen on 2 fresh checks >= 30 s apart: closed (normal trades) / alert only (News Trader orders)
Every problem: event log + Telegram (risk) once; a follower whose repair fails 3 times is PAUSED
(it gets no new trades until you press Resume). Stale data is never acted on.
"""
from __future__ import annotations

import threading
import time
from collections import deque

import mt5_ops

SL_TOL = 0.06
ORPHAN_AGE_S = 30.0


class Reconciler:
    def __init__(self, hub):
        self.hub = hub
        self.auto_repair = True
        self.rows = []
        self.orphans = {}                 # (acc_id, ticket) -> first seen
        self.fail = {}                    # acc_id -> consecutive repair failures
        self.alerted = set()
        self.log = deque(maxlen=60)
        self.last = 0.0
        self.running = True
        threading.Thread(target=self._loop, daemon=True).start()

    @property
    def tm(self):
        return self.hub.trader

    def _note(self, text, kind="warn", alert_key=None):
        self.log.appendleft(dict(t=time.time(), text=text, kind=kind))
        self.hub.note(kind, f"RECONCILE: {text}")
        if alert_key and alert_key not in self.alerted:
            self.alerted.add(alert_key)
            n = getattr(self.hub, "notifier", None)
            if n:
                n.send("risk", f"🔄 <b>COPIER</b>\n{text}")

    def pause(self, a, why):
        if not getattr(a, "paused", ""):
            a.paused = why
            self._note(f"{a.name} PAUSED: {why}", "error", alert_key=("pause", a.id))

    def resume(self, acc_id):
        tm = self.tm
        a = tm._acct(acc_id) if tm else None
        if not a:
            return dict(ok=False, error="account not found")
        a.paused = ""
        self.fail[acc_id] = 0
        self.alerted = {k for k in self.alerted if k[1] != acc_id}
        self._note(f"{a.name} resumed by you", "info")
        return dict(ok=True)

    # ------------------------------------------------------------------ check
    def check(self):
        tm = self.tm
        if tm is None:
            return
        rows = []
        with tm.lock:
            fmap = {f.id: f for f in tm.followers}
            accts = {a.id: a for a in tm.all_accounts()}
            master_ok = tm._fresh(tm.master)
            for gid, g in list(tm.groups.items()):
                if g.closing or time.time() - g.created < 8:
                    continue
                mp = tm.master.pos(g.legs.get("master")) if master_ok else None
                e = tm.journal.entries.get(gid) or {}
                m_lot0 = e.get("lot") or g.m_vol
                for fid, t in g.legs.items():
                    if fid == "master":
                        continue
                    f = fmap.get(fid)
                    row = dict(gid=gid, account=f.name if f else fid, status="SYNC", details=[])
                    if f is None:
                        row.update(status="UNKNOWN", details=["follower removed from Accounts"])
                        rows.append(row)
                        continue
                    if not master_ok or not tm._fresh(f):
                        row.update(status="STALE", details=["waiting for fresh data (no action)"])
                        rows.append(row)
                        continue
                    if mp is None:
                        continue
                    fp = f.pos(t)
                    if fp is None:
                        row.update(status="CLOSED", details=["leg closed by its own SL/TP"])
                        rows.append(row)
                        continue
                    # side
                    if fp["side"] != g.side:
                        row.update(status="DRIFT", details=[f"SIDE mismatch (follower {fp['side']}, master {g.side})"])
                        self.pause(f, f"side mismatch on trade {gid}")
                        rows.append(row)
                        continue
                    # volume
                    step = (getattr(f, "broker", None) or {}).get("lot_step", 0.01) or 0.01
                    exp = round((f.cfg.get("lot") or 0) * (mp["volume"] / m_lot0 if m_lot0 else 1), 8)
                    diff = fp["volume"] - exp
                    if abs(diff) > step / 2 + 1e-9:
                        row["status"] = "DRIFT"
                        row["details"].append(f"volume {fp['volume']} vs expected {exp:.2f}")
                        if diff > 0 and self.auto_repair:
                            r = f.backend.call("close", t, round(diff / step) * step)
                            self._repair_result(f, r, f"trade {gid}: closed the extra {diff:.2f} lot")
                        else:
                            self._note(f"{f.name} trade {gid}: volume {fp['volume']} < expected {exp:.2f} "
                                       f"(not topped up: that would add risk)", alert_key=("vol", fid, gid))
                    # SL / TP distances
                    m_sl = (mp["sl"] - mp["open"]) if mp["sl"] else None
                    m_tp = (mp["tp"] - mp["open"]) if mp["tp"] else None
                    f_sl = (fp["sl"] - fp["open"]) if fp["sl"] else None
                    f_tp = (fp["tp"] - fp["open"]) if fp["tp"] else None
                    bad = []
                    for nm, a_, b_ in (("SL", m_sl, f_sl), ("TP", m_tp, f_tp)):
                        if (a_ is None) != (b_ is None) or (a_ is not None and abs(a_ - b_) > SL_TOL):
                            bad.append(nm)
                    if bad:
                        row["status"] = "DRIFT"
                        row["details"].append(f"{'/'.join(bad)} differ from the master")
                        if self.auto_repair:
                            sl = round(fp["open"] + m_sl, 2) if m_sl is not None else 0.0
                            tp = round(fp["open"] + m_tp, 2) if m_tp is not None else 0.0
                            r = f.backend.call("modify", t, sl, tp)
                            self._repair_result(f, r, f"trade {gid}: {'/'.join(bad)} set like the master")
                    if row["status"] == "SYNC":
                        row["details"].append(f"{fp['volume']} lot · SL/TP like the master ✓")
                    rows.append(row)
            # orphans
            live_gids = set(tm.groups)
            now = time.time()
            seen = set()
            for a in accts.values():
                if not tm._fresh(a):
                    continue
                for p in a.positions:
                    c = str(p.get("comment") or "")
                    if p.get("magic") != mt5_ops.MAGIC or not c.startswith(mt5_ops.COMMENT_PREFIX):
                        continue                   # other EAs / manual: never touched
                    gid = c[len(mt5_ops.COMMENT_PREFIX):]
                    if gid in live_gids or gid == "check":
                        continue
                    key = (a.id, p["ticket"])
                    seen.add(key)
                    first = self.orphans.setdefault(key, now)
                    news = gid.startswith("N")
                    rows.append(dict(gid=gid, account=a.name, status="ORPHAN",
                                     details=[f"REYGUN position #{p['ticket']} without a trade group"
                                              + (" (News Trader: alert only)" if news else "")]))
                    if now - first < ORPHAN_AGE_S:
                        continue
                    if news or not self.auto_repair:
                        self._note(f"{a.name}: orphan REYGUN position #{p['ticket']} ({gid}), check it in MT5",
                                   alert_key=("orphan", a.id, p["ticket"]))
                    else:
                        r = a.backend.call("close", p["ticket"], None)
                        self._repair_result(a, r, f"orphan position #{p['ticket']} ({gid}) closed")
            self.orphans = {k: v for k, v in self.orphans.items() if k in seen}
        self.rows = rows
        self.last = time.time()

    def _repair_result(self, a, r, text):
        if r.get("ok"):
            self.fail[a.id] = 0
            self._note(f"{a.name}: repaired · {text}", "info")
        else:
            self.fail[a.id] = self.fail.get(a.id, 0) + 1
            self._note(f"{a.name}: repair FAILED ({r.get('error')}) · {text}", "error", alert_key=("fail", a.id, text))
            if self.fail[a.id] >= 3:
                self.pause(a, "repairs keep failing")

    def _loop(self):
        time.sleep(10)
        while self.running:
            try:
                self.check()
            except Exception as e:
                self.hub.note("error", f"reconciliation: {e.__class__.__name__}: {e}")
            time.sleep(5)

    def state(self):
        tm = self.tm
        paused = [dict(id=f.id, name=f.name, why=f.paused) for f in (tm.followers if tm else []) if getattr(f, "paused", "")]
        status = ("PAUSED" if paused else "DRIFT" if any(r["status"] in ("DRIFT", "ORPHAN") for r in self.rows)
                  else "SYNC")
        return dict(status=status, rows=self.rows, paused=paused, auto_repair=self.auto_repair, updated=self.last,
                    log=list(self.log)[:25])

    def stop(self):
        self.running = False
