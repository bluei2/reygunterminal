"""
SETTINGS HISTORY + ROLLBACK (v2.8.53, Eugene: "ibalik sa dati" should be one click).

Every change of the trading settings is kept as a snapshot (strategies, AUTO TRADE, position manager / trailing,
NEWS TRADE). A background watcher compares the files every 20 s, so a change from ANY place (a button, a mode,
a profile, a hand edit) is caught. Identical content is never stored twice. The last KEEP snapshots are kept in
settings_history.jsonl (a runtime file: never in the update zip).

restore(id) writes that snapshot back (after saving the current state as "bago mag-restore", so a restore can be
undone too) and reloads the running AUTO TRADE / position manager / NEWS TRADE settings. Accounts, passwords and
journals are never part of a snapshot.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
HIST_FILE = os.path.join(HERE, "settings_history.jsonl")
KEEP = 300
LABEL = {"strategy": "Strategies", "autotrade": "AUTO TRADE", "position": "Trailing / BE", "news": "NEWS TRADE"}
# keys worth showing in the change summary (dotted, file-relative); everything else is counted as "iba pa"
WATCH = ("profile", "htf_filter", "trend.rr", "trend.max_sl", "trend.auto", "pullback.rr", "pullback.os", "pullback.ob",
         "pullback.auto", "conso.max_range", "conso.auto", "conso.levels", "tf", "lot", "max_open", "align_mult",
         "mode_mult", "loss_pause_n", "loss_pause_h", "be_r", "trail_mode", "trail_start_r", "step_r", "enabled",
         "trend.required", "pullback.required", "conso.required", "tp_rr", "follow_trend")


def _paths():
    """Read the modules' current file paths (the test harness redirects them)."""
    import autotrade
    import news_trader
    import position_manager
    import strategies
    return {"strategy": strategies.SETTINGS_FILE, "autotrade": autotrade.SETTINGS_FILE,
            "position": position_manager.SETTINGS_FILE, "news": news_trader.SETTINGS_FILE}


def _read_all():
    out = {}
    for k, p in _paths().items():
        try:
            with open(p) as f:
                out[k] = json.load(f)
        except (OSError, ValueError):
            pass
    return out


def _hash(files):
    return hashlib.sha1(json.dumps(files, sort_keys=True).encode()).hexdigest()[:12]


def _flat(d, pre=""):
    out = {}
    for k, v in (d or {}).items():
        key = f"{pre}{k}"
        if isinstance(v, dict) and k != "conditions":
            out.update(_flat(v, key + "."))
        else:
            out[key] = v
    return out


def diff(old, new):
    """Human list of what changed between two snapshots' files: ["Strategies: trend.rr 5 → 3", ...]."""
    lines, other = [], 0
    for f in sorted(set(old) | set(new)):
        a, b = _flat(old.get(f)), _flat(new.get(f))
        for k in sorted(set(a) | set(b)):
            if a.get(k) == b.get(k) or k.endswith("version") or k.endswith("schema"):
                continue
            if k in WATCH or any(k.endswith("." + w) or k == w for w in WATCH):
                lines.append(f"{LABEL.get(f, f)}: {k} {a.get(k)} → {b.get(k)}")
            else:
                other += 1
    if other:
        lines.append(f"+ {other} iba pang setting")
    return lines


class SettingsHistory:
    def __init__(self, version="", path=None, start=True, interval=20.0):
        self.version = version
        self.path = path or HIST_FILE
        self.lock = threading.Lock()
        self.rows = []
        try:
            with open(self.path) as f:
                self.rows = [json.loads(x) for x in f if x.strip()]
        except (OSError, ValueError):
            self.rows = []
        self.running = start
        self.snapshot(f"pagbukas ng app v{version}" if version else "pagbukas ng app")
        if start:
            threading.Thread(target=self._loop, args=(interval,), daemon=True, name="settings-history").start()

    def _loop(self, interval):
        while self.running:
            time.sleep(interval)
            try:
                self.snapshot("binago")
            except Exception:
                pass

    def snapshot(self, reason):
        """Store the current files when they differ from the last snapshot. -> the new row or None."""
        files = _read_all()
        if not files:
            return None
        h = _hash(files)
        with self.lock:
            last = self.rows[-1] if self.rows else None
            if last and last.get("hash") == h:
                return None
            row = dict(id=int(time.time() * 1000), t=time.time(), reason=reason, ver=self.version, hash=h, files=files,
                       changes=diff(last["files"], files) if last else ["unang snapshot"])
            if self.rows and row["id"] <= self.rows[-1]["id"]:
                row["id"] = self.rows[-1]["id"] + 1
            self.rows.append(row)
            self.rows = self.rows[-KEEP:]
            self._save()
            return row

    def _save(self):
        try:
            tmp = self.path + ".tmp"
            with open(tmp, "w") as f:
                for r in self.rows:
                    f.write(json.dumps(r) + "\n")
            os.replace(tmp, self.path)
        except OSError:
            pass

    def list(self, n=60):
        with self.lock:
            rows = list(self.rows[-n:])
        return [dict(id=r["id"], t=r["t"], reason=r["reason"], ver=r.get("ver"), changes=r.get("changes", [])[:12],
                     current=(i == len(rows) - 1)) for i, r in enumerate(rows)][::-1]

    def restore(self, sid, hub=None):
        """Write snapshot sid back and reload the running settings. -> dict(ok, changes)."""
        with self.lock:
            row = next((r for r in self.rows if r["id"] == int(sid)), None)
        if row is None:
            return dict(ok=False, error="walang ganitong snapshot")
        self.snapshot("bago mag-restore")
        cur = _read_all()
        paths = _paths()
        for k, data in row["files"].items():
            try:
                tmp = paths[k] + ".tmp"
                with open(tmp, "w") as f:
                    json.dump(data, f, indent=2)
                os.replace(tmp, paths[k])
            except OSError as e:
                return dict(ok=False, error=f"{LABEL.get(k, k)}: {e}")
        self._reload(hub)
        new = self.snapshot(f"ibinalik ang {time.strftime('%m-%d %H:%M', time.localtime(row['t']))}")
        return dict(ok=True, changes=diff(cur, row["files"]), restored=row["id"], snapshot=new and new["id"])

    @staticmethod
    def _reload(hub):
        if hub is None:
            return
        try:
            import autotrade
            if getattr(hub, "autotrader", None) is not None:
                hub.autotrader.settings = autotrade.load_settings()
        except Exception:
            pass
        try:
            import position_manager
            pm = getattr(hub, "positions_mgr", None)
            if pm is not None:
                pm.settings = position_manager.load_settings()
        except Exception:
            pass
        try:
            import news_trader
            nt = getattr(hub, "newstrader", None)
            if nt is not None:
                nt.settings = news_trader.load_settings()
        except Exception:
            pass

    def stop(self):
        self.running = False
