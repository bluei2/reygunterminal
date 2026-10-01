"""
One follower account = one process connected to its own MT5 terminal (MetaTrader5 allows one terminal
per Python process). The main SuperChart process talks to it through two queues.
"""
from __future__ import annotations

import multiprocessing as mp
import queue
import threading
import time
import traceback

import mt5_ops


def worker_main(path, expected_login, cmd_q, res_q):
    try:
        import MetaTrader5 as mt5
    except ImportError:
        res_q.put(("__init__", dict(ok=False, error="MetaTrader5 package not installed")))
        return
    kw = {"path": path} if path else {}
    if not mt5.initialize(**kw):
        res_q.put(("__init__", dict(ok=False, error=f"MT5 initialize failed for {path}: {mt5.last_error()}")))
        return
    acc = mt5_ops.account(mt5)
    if expected_login and acc.get("ok") and int(acc["login"]) != int(expected_login):
        res_q.put(("__init__", dict(ok=False, error=f"terminal is logged in as #{acc['login']}, "
                                                     f"expected #{expected_login}")))
        mt5.shutdown()
        return
    res_q.put(("__init__", acc))
    while True:
        try:
            rid, cmd, args = cmd_q.get()
        except (EOFError, KeyboardInterrupt):
            break
        if cmd == "stop":
            break
        try:
            fn = getattr(mt5_ops, cmd)
            out = fn(mt5, *args)
        except Exception as e:
            out = dict(ok=False, error=f"{e.__class__.__name__}: {e}", tb=traceback.format_exc(limit=3))
        res_q.put((rid, out))
    mt5.shutdown()


class RemoteMT5:
    """Main-process handle to a follower worker. call() is thread safe and blocking."""

    def __init__(self, path, expected_login=None, start_timeout=40.0):
        ctx = mp.get_context("spawn")
        self.cmd_q, self.res_q = ctx.Queue(), ctx.Queue()
        self.proc = ctx.Process(target=worker_main, args=(path, expected_login, self.cmd_q, self.res_q), daemon=True)
        self.proc.start()
        self._lock = threading.Lock()
        self._rid = 0
        self.info = None
        try:
            tag, info = self.res_q.get(timeout=start_timeout)
            self.info = info
        except queue.Empty:
            self.info = dict(ok=False, error="worker did not start (timeout)")
        self.ok = bool(self.info and self.info.get("ok"))

    def call(self, cmd, *args, timeout=20.0):
        if not self.ok or not self.proc.is_alive():
            return dict(ok=False, error="account not connected")
        with self._lock:
            self._rid += 1
            rid = self._rid
            self.cmd_q.put((rid, cmd, args))
            deadline = time.time() + timeout
            while True:
                left = deadline - time.time()
                if left <= 0:
                    return dict(ok=False, error=f"timeout waiting for {cmd}")
                try:
                    got_rid, out = self.res_q.get(timeout=left)
                except queue.Empty:
                    return dict(ok=False, error=f"timeout waiting for {cmd}")
                if got_rid == rid:
                    return out

    def stop(self):
        try:
            self.cmd_q.put((0, "stop", ()))
            self.proc.join(timeout=3)
        except Exception:
            pass
        if self.proc.is_alive():
            self.proc.terminate()
