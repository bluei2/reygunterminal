"""
Trade analytics from the journal (descriptive only: nothing here ranks a setup as "best" or changes any setting).

  by exit     TP, SL, BE, TRAIL, EXIT_GUARD, RISK_LOCK, MANUAL, RECONCILIATION, SYSTEM, UNKNOWN
  by session  LONDON, NEW_YORK, OVERLAP, ASIA, OFF, WEEKEND (the shared market_hours classification)
  by regime   TREND UP / TREND DOWN / RANGE / VOLATILE / QUIET / TRANSITION (regime at entry, regime.py)
  by setup    setup fingerprint key (decision engine at entry), e.g. "M5 CHOCH + DISCOUNT + FVG"
  MFE / MAE   per trade + averages: how far it went for you / against you, and R left on the table

Each group: trades, wins, losses, win rate, avg R, total R, max drawdown (R), avg hold (min), avg MFE / MAE (R).
Older journal entries without the new fields are still counted (their session is derived from the entry time,
their exit class from the saved reason).
"""
from __future__ import annotations

from market_hours import session_code, utc_from_server


def _closed(entries):
    return [e for e in entries if e.get("status") == "closed"]


def exit_class(e):
    if e.get("exit_reason"):
        return e["exit_reason"]
    rs = str(e.get("reason") or "")
    if rs.startswith("Exit guard"):
        return "EXIT_GUARD"
    if rs.startswith("Risk lock"):
        return "RISK_LOCK"
    if rs.startswith("Manual"):
        return "MANUAL"
    if rs == "TP":
        return "TP"
    if rs == "SL":
        r = e.get("r")
        return "BE" if r is not None and abs(r) < 0.15 else "TRAIL" if r is not None and r > 0.15 else "SL"
    return "UNKNOWN"


def session_of(e, utc_offset=0):
    if e.get("session"):
        return e["session"]
    t = e.get("t_srv_open")
    if t:
        return session_code(utc_from_server(t, utc_offset))
    return session_code(utc_from_server(int(e.get("open_time", 0)), 0))


def block(rows):
    n = len(rows)
    rs = [e.get("r") for e in rows if e.get("r") is not None]
    wins = sum(1 for e in rows if e.get("result") == "WIN")
    eq = peak = dd = 0.0
    for e in sorted(rows, key=lambda x: x.get("close_time", 0)):
        eq += e.get("r") or 0.0
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    mfe = [e["mfe_r"] for e in rows if e.get("mfe_r") is not None]
    mae = [e["mae_r"] for e in rows if e.get("mae_r") is not None]
    hold = [e["duration_min"] for e in rows if e.get("duration_min") is not None]
    return dict(trades=n, wins=wins, losses=sum(1 for e in rows if e.get("result") == "LOSS"),
                win_rate=round(100 * wins / n, 1) if n else None,
                avg_r=round(sum(rs) / len(rs), 3) if rs else None, total_r=round(sum(rs), 2) if rs else 0.0,
                pnl=round(sum(e.get("pnl") or 0 for e in rows), 2), max_dd_r=round(dd, 2),
                avg_hold_min=round(sum(hold) / len(hold), 1) if hold else None,
                avg_mfe_r=round(sum(mfe) / len(mfe), 2) if mfe else None,
                avg_mae_r=round(sum(mae) / len(mae), 2) if mae else None)


def group_by(entries, keyfn, order=None):
    out = {}
    for e in _closed(entries):
        out.setdefault(keyfn(e) or "NA", []).append(e)
    keys = sorted(out, key=lambda k: (order.index(k) if order and k in order else 99, k))
    return [dict(key=k, **block(out[k])) for k in keys]


EXITS = ["TP", "SL", "BE", "TRAIL", "EXIT_GUARD", "RISK_LOCK", "MANUAL", "RECONCILIATION", "SYSTEM", "UNKNOWN"]
SESSIONS = ["LONDON", "OVERLAP", "NEW_YORK", "ASIA", "OFF", "WEEKEND"]
REGIMES = ["TREND UP", "TREND DOWN", "RANGE", "VOLATILE", "QUIET", "TRANSITION"]


def by_exit(entries):
    return group_by(entries, exit_class, EXITS)


def by_session(entries, utc_offset=0):
    return group_by(entries, lambda e: session_of(e, utc_offset), SESSIONS)


def by_regime(entries):
    return group_by(entries, lambda e: (e.get("regime") or {}).get("label"), REGIMES)


def by_setup(entries):
    rows = group_by(entries, lambda e: (e.get("setup_fingerprint") or {}).get("key"))
    return sorted(rows, key=lambda r: -r["trades"])              # most traded first (descriptive, not "best")


def mfe_mae(entries):
    rows = []
    for e in _closed(entries):
        if e.get("mfe_r") is None and e.get("mfe_price") is None:
            continue
        left = None
        if e.get("mfe_r") is not None and e.get("r") is not None:
            left = round(e["mfe_r"] - e["r"], 2)
        rows.append(dict(gid=e["gid"], side=e.get("side"), entry=e.get("entry"), exit=e.get("exit"), r=e.get("r"),
                         mfe_price=e.get("mfe_price"), mfe_r=e.get("mfe_r"), mae_price=e.get("mae_price"),
                         mae_r=e.get("mae_r"), time_to_mfe=e.get("time_to_mfe"), time_to_mae=e.get("time_to_mae"),
                         left_on_table_r=left, exit_reason=exit_class(e)))
    lefts = [x["left_on_table_r"] for x in rows if x["left_on_table_r"] is not None]
    winners_mae = [x["mae_r"] for x in rows if x["mae_r"] is not None and (x["r"] or 0) > 0]
    summary = dict(trades=len(rows),
                   avg_mfe_r=round(sum(x["mfe_r"] for x in rows if x["mfe_r"] is not None) / max(1, sum(1 for x in rows if x["mfe_r"] is not None)), 2) if rows else None,
                   avg_mae_r=round(sum(x["mae_r"] for x in rows if x["mae_r"] is not None) / max(1, sum(1 for x in rows if x["mae_r"] is not None)), 2) if rows else None,
                   avg_left_on_table_r=round(sum(lefts) / len(lefts), 2) if lefts else None,
                   worst_mae_of_winners_r=round(min(winners_mae), 2) if winners_mae else None)
    return dict(summary=summary, trades=rows[-300:])


def all_analytics(entries, utc_offset=0):
    return dict(exits=by_exit(entries), sessions=by_session(entries, utc_offset), regimes=by_regime(entries),
                setups=by_setup(entries), mfe_mae=mfe_mae(entries), overall=block(_closed(entries)))
