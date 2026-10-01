"""Keep-alive support (Eugene, 2026-10-01: "na-di-disconnect, baka biglang may news, ang panalo biglang matalo").

* The run_superchart_*.bat files restart the server by themselves when it stops (crash, MT5 hiccup), 5 s later.
* While running, the app writes runtime_state.json every few seconds: trading ON (armed), AUTO, NEWS TRADE.
  A clean shutdown marks it clean.
* A restart by the .bat (--resume) within RESUME_WINDOW seconds of an UNCLEAN stop restores those switches, so a
  crash does not leave open trades without AUTO / profit protection. A normal start (double-click) never resumes:
  AUTO is OFF at start, as always.
* Every uncaught error (any thread) and a hard crash (faulthandler) go to crash_log.txt with the traceback.
Broker-side SL / TP are on every order, so they protect the trades even while the app is down.
"""
from __future__ import annotations

import faulthandler
import json
import os
import sys
import threading
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(HERE, "runtime_state.json")
CRASH_LOG = os.path.join(HERE, "crash_log.txt")
RESUME_WINDOW = 600            # s: only resume a stop that happened within the last 10 minutes
_fault_fh = None
_stopped = False


def log_crash(text):
    try:
        with open(CRASH_LOG, "a", encoding="utf-8") as f:
            f.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n{text}\n")
    except OSError:
        pass


def install_crash_logging():
    """Uncaught exceptions of the main thread and of every other thread -> crash_log.txt (and the console)."""
    global _fault_fh
    old = sys.excepthook

    def hook(t, v, tb):
        log_crash("".join(traceback.format_exception(t, v, tb)))
        old(t, v, tb)
    sys.excepthook = hook

    def thook(args):
        if args.exc_type is SystemExit:
            return
        log_crash(f"thread {getattr(args.thread, 'name', '?')}:\n"
                  + "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)))
    threading.excepthook = thook
    try:
        _fault_fh = open(CRASH_LOG, "a")
        faulthandler.enable(_fault_fh)          # hard crashes (segfault in a DLL) too
    except (OSError, RuntimeError, ValueError):
        pass


def snapshot(hub) -> dict:
    tm = getattr(hub, "trader", None)
    at = getattr(hub, "autotrader", None)
    nt = getattr(hub, "newstrader", None)
    return dict(t=time.time(), pid=os.getpid(), clean=False,
                armed=bool(tm is not None and tm.armed and not getattr(tm, "halted", "")),
                auto=bool(at is not None and at.enabled), news=bool(nt is not None and nt.enabled))


def write(state: dict):
    try:
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(state, f)
        os.replace(tmp, STATE_FILE)
    except OSError:
        pass


def read() -> dict | None:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def start_writer(hub, every=3.0):
    def loop():
        while getattr(hub, "running", True) and not _stopped:
            write(snapshot(hub))
            time.sleep(every)
    threading.Thread(target=loop, daemon=True, name="runstate").start()


def mark_clean(hub):
    global _stopped
    _stopped = True                                 # the writer must not overwrite the clean mark
    s = snapshot(hub)
    s["clean"] = True
    write(s)


def resume(hub, prev: dict | None, now=None) -> list[str]:
    """Restore trading ON / AUTO / NEWS TRADE after an UNCLEAN stop within RESUME_WINDOW. Returns what was restored."""
    now = now or time.time()
    if not prev or prev.get("clean") or now - float(prev.get("t") or 0) > RESUME_WINDOW:
        return []
    done = []
    tm, at, nt = getattr(hub, "trader", None), getattr(hub, "autotrader", None), getattr(hub, "newstrader", None)
    if prev.get("armed") and tm is not None and not tm.halted:
        tm.set_armed(True)
        done.append("trading ON")
        if prev.get("auto") and at is not None and at.set_enabled(True).get("ok"):
            done.append("AUTO")
        if prev.get("news") and nt is not None and nt.set_enabled(True).get("ok"):
            done.append("NEWS TRADE")
    return done
