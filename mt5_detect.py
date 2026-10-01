"""Find the MT5 terminals that are OPEN on this PC (Windows) and what account each one is logged in to.

Why: MetaTrader5.initialize() without a path attaches to the "default" terminal, which on a PC / VPS with several MT5
installations (LiteFinance, SwitchMarkets, ...) may be a different one than the terminal you have open - it can even
start another terminal. The Accounts page uses this to show every open terminal with one-click "use as MASTER" /
"add as FOLLOWER", and the app uses it at start when the master has no terminal path yet.

Every probe runs in its OWN process (one MT5 connection per Python process), so the master connection of the running
app is never touched. Read-only: no order, no setting change in MT5.
"""
from __future__ import annotations

import multiprocessing as mp
import os
import subprocess
import sys


def running_terminals() -> list[str]:
    """Full paths of every running terminal64.exe (unique, in start order). Empty outside Windows."""
    if sys.platform != "win32":
        return []
    cmds = [
        ["powershell", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process -Filter \"Name='terminal64.exe'\" | ForEach-Object { $_.ExecutablePath }"],
        ["wmic", "process", "where", "name='terminal64.exe'", "get", "ExecutablePath"],
    ]
    for cmd in cmds:
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=15,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        paths = []
        for line in out.splitlines():
            p = line.strip().strip('"')
            if p.lower().endswith("terminal64.exe") and p not in paths and os.path.exists(p):
                paths.append(p)
        if paths:
            return paths
    return []


def _probe_main(path, symbol, q):
    try:
        import MetaTrader5 as mt5
    except ImportError:
        q.put(dict(ok=False, error="MetaTrader5 package not installed"))
        return
    try:
        if not mt5.initialize(path=path, timeout=15000):
            q.put(dict(ok=False, error=f"initialize failed: {mt5.last_error()}"))
            return
        acc = mt5.account_info()
        ti = mt5.terminal_info()
        out = dict(ok=acc is not None)
        if acc is not None:
            out.update(login=int(acc.login), server=acc.server, name=acc.name, company=acc.company,
                       balance=float(acc.balance), equity=float(acc.equity), currency=acc.currency,
                       trade_allowed=bool(ti.trade_allowed) if ti else None)
        else:
            out["error"] = "terminal open but not logged in"
        if symbol:
            out["has_symbol"] = mt5.symbol_info(symbol) is not None
            if not out["has_symbol"]:                       # suggest the broker's gold symbol
                base = "XAU" if symbol.upper().startswith(("XAU", "GOLD")) else symbol[:6]
                syms = [s.name for s in (mt5.symbols_get(f"*{base}*") or [])][:5]
                out["symbols"] = syms
        mt5.shutdown()
        q.put(out)
    except Exception as e:                                  # never crash the caller
        q.put(dict(ok=False, error=f"{e.__class__.__name__}: {e}"))


def probe(path: str, symbol: str | None = None, timeout: float = 25.0) -> dict:
    ctx = mp.get_context("spawn")
    q = ctx.Queue()
    p = ctx.Process(target=_probe_main, args=(path, symbol, q), daemon=True)
    p.start()
    try:
        res = q.get(timeout=timeout)
    except Exception:
        res = dict(ok=False, error="no answer from this terminal (busy or not logged in)")
    p.join(2)
    if p.is_alive():
        p.terminate()
    res["path"] = path
    res["folder"] = os.path.basename(os.path.dirname(path))
    return res


def detect(symbol: str | None = None) -> list[dict]:
    """Every open MT5 terminal with its account (login, server, balance) and whether it has `symbol`."""
    return [probe(p, symbol) for p in running_terminals()]


def pick_master(found: list[dict], login=None, symbol=None) -> dict | None:
    """The terminal to use as master: the expected login > the only one with the symbol > the only one open."""
    ok = [f for f in found if f.get("ok")]
    if login:
        m = [f for f in ok if int(f.get("login") or 0) == int(login)]
        if m:
            return m[0]
    if symbol:
        m = [f for f in ok if f.get("has_symbol")]
        if len(m) == 1:
            return m[0]
    return ok[0] if len(ok) == 1 else None
