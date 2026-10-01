"""
Execution guard: broker / execution safety before ANY order, per account.

For every account (master + enabled followers) it checks, using the broker facts that TradeManager.refresh()
already read through mt5_ops.broker_info (this module never calls MetaTrader5 itself):
  connected, symbol exists + selected, trading allowed (Algo Trading + symbol trade mode), lot >= min / <= max /
  on the lot step, stop loss present (require_protective_sl), SL / TP distance vs broker stops level, freeze level,
  spread, broker data freshness, execution latency (ping), free margin, account unlocked.
Master problems are HARD (no order); follower problems make that follower be skipped (the others still trade).

check(side, lot, sl_dist, tp_dist) -> {ok, reasons:[{code, text, level, account}], accounts:{id: {...}}}
monitor() -> per-account dashboard rows (READY / BLOCKED + reasons + broker facts).
"""
from __future__ import annotations

import json
import os
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(HERE, "execution_settings.json")
DEFAULTS = dict(require_protective_sl=True, max_spread=0.0, max_latency_ms=0.0, broker_stale_s=60.0,
                confirmation_expiry=15.0, min_margin=0.0)


def load_settings():
    s = dict(DEFAULTS)
    try:
        with open(SETTINGS_FILE) as f:
            s.update({k: v for k, v in json.load(f).items() if k in DEFAULTS})
    except (OSError, ValueError):
        pass
    return s


def _r(code, text, level, account):
    return dict(code=code, text=text, level=level, account=account)


class ExecutionGuard:
    def __init__(self, hub):
        self.hub = hub
        self.settings = load_settings()

    def update_settings(self, new):
        s = self.settings
        for k, v in (new or {}).items():
            if k in DEFAULTS:
                try:
                    s[k] = bool(v) if isinstance(DEFAULTS[k], bool) else float(v)
                except (TypeError, ValueError):
                    pass
        try:
            with open(SETTINGS_FILE, "w") as f:
                json.dump(s, f, indent=2)
        except OSError:
            pass
        book = getattr(self.hub, "confirmations", None)
        if book is not None:
            book.expiry_s = s["confirmation_expiry"]
        return dict(ok=True, settings=s)

    def _max_spread(self):
        if self.settings["max_spread"]:
            return self.settings["max_spread"]
        at = getattr(self.hub, "autotrader", None)
        return (at.settings.get("max_spread") if at else 0) or 0

    def check_account(self, a, side, lot, sl_dist, tp_dist):
        """Reasons this account cannot take the order (empty = ok)."""
        s = self.settings
        tm = self.hub.trader
        name = a.name
        out = []
        if not a.connected:
            return [_r("NOT_CONNECTED", f"{name}: not connected", "", name)]
        if a.locked:
            out.append(_r("ACCOUNT_LOCKED", f"{name}: {a.locked}", "", name))
        bi = getattr(a, "broker", None)
        if not bi:
            out.append(_r("EXECUTION_NOT_READY", f"{name}: broker data not read yet", "", name))
            return out
        if time.time() - getattr(a, "broker_at", 0) > s["broker_stale_s"]:
            out.append(_r("BROKER_DATA_STALE", f"{name}: broker data {time.time() - a.broker_at:.0f}s old", "", name))
        if not bi.get("connected", True):
            out.append(_r("NOT_CONNECTED", f"{name}: MT5 not connected to the broker", "", name))
        if not tm.paper and not bi.get("trade_allowed", True):
            out.append(_r("TRADING_NOT_ALLOWED", f"{name}: Algo Trading is OFF", "", name))
        if bi.get("trade_mode", 4) != 4:
            out.append(_r("SYMBOL_TRADE_DISABLED", f"{name}: {bi['symbol']} trade mode {bi.get('trade_mode')} "
                                                   f"(0 disabled / 3 close only)", "", name))
        if not bi.get("selected", True):
            out.append(_r("SYMBOL_NOT_SELECTED", f"{name}: {bi['symbol']} not in Market Watch", "", name))
        if lot is not None:
            step = bi.get("lot_step") or 0.01
            if lot < bi["min_lot"] - 1e-9 or lot > bi["max_lot"] + 1e-9:
                out.append(_r("INVALID_LOT", f"{name}: lot {lot} outside {bi['min_lot']}-{bi['max_lot']}", "", name))
            elif abs(round(lot / step) * step - lot) > 1e-6:
                out.append(_r("INVALID_LOT", f"{name}: lot {lot} not a multiple of the step {step}", "", name))
        if sl_dist is not None:
            spread = bi.get("spread") or 0.0
            pt = bi.get("point") or 0.01
            stops = bi.get("stops_level", 0) * pt
            freeze = bi.get("freeze_level", 0) * pt
            if s["require_protective_sl"] and not sl_dist:
                out.append(_r("NO_PROTECTIVE_SL", f"{name}: no stop loss (required)", "", name))
            if sl_dist and stops and sl_dist - spread <= stops:
                out.append(_r("BROKER_STOPS_LEVEL", f"{name}: SL ${sl_dist} too close (stops level {stops:.2f} "
                                                    f"+ spread {spread:.2f})", "", name))
            if tp_dist and stops and tp_dist <= stops:
                out.append(_r("BROKER_STOPS_LEVEL", f"{name}: TP ${tp_dist} too close (stops level {stops:.2f})", "", name))
            if freeze and sl_dist and sl_dist <= freeze:
                out.append(_r("BROKER_FREEZE_LEVEL", f"{name}: SL inside the freeze level {freeze:.2f}", "", name))
        mx = self._max_spread()
        if mx and bi.get("spread") is not None and bi["spread"] > mx:
            out.append(_r("SPREAD_TOO_HIGH", f"{name}: spread ${bi['spread']:.2f} > ${mx:.2f}", "", name))
        if s["max_latency_ms"] and bi.get("ping_ms") and bi["ping_ms"] > s["max_latency_ms"]:
            out.append(_r("HIGH_LATENCY", f"{name}: latency {bi['ping_ms']:.0f} ms > {s['max_latency_ms']:.0f}", "", name))
        if s["min_margin"] and bi.get("margin_free") is not None and bi["margin_free"] < s["min_margin"]:
            out.append(_r("NO_MARGIN", f"{name}: free margin {bi['margin_free']:.2f} < {s['min_margin']:.2f}", "", name))
        return out

    def check(self, side, lot, sl_dist, tp_dist):
        tm = self.hub.trader
        if tm is None:
            return dict(ok=False, reasons=[_r("EXECUTION_NOT_READY", "trading not available", "hard", "")], accounts={})
        accounts, reasons = {}, []
        for a in tm.all_accounts():
            if a.role == "follower" and not a.cfg.get("enabled", True):
                continue
            a_lot = lot if a.role == "master" else a.cfg.get("lot")
            rs = self.check_account(a, side, a_lot, sl_dist, tp_dist)
            level = "hard" if a.role == "master" else "soft"
            for x in rs:
                x["level"] = level
            accounts[a.id] = dict(name=a.name, role=a.role, ok=not rs, reasons=rs)
            reasons += rs
        return dict(ok=not [x for x in reasons if x["level"] == "hard"], reasons=reasons, accounts=accounts)

    def follower_ok(self, f, side, sl_dist, tp_dist):
        """Used by TradeManager.open(): skip a follower that would be rejected."""
        rs = self.check_account(f, side, f.cfg.get("lot"), sl_dist, tp_dist)
        return not rs, "; ".join(x["code"] for x in rs)

    def monitor(self, lot=None, sl_dist=5.0, tp_dist=10.0):
        tm = self.hub.trader
        if tm is None:
            return dict(accounts=[], settings=self.settings)
        rows = []
        for a in tm.all_accounts():
            bi = getattr(a, "broker", None) or {}
            a_lot = (lot if lot is not None else 0.01) if a.role == "master" else a.cfg.get("lot")
            rs = self.check_account(a, 1, a_lot, sl_dist, tp_dist) if (a.role == "master" or a.cfg.get("enabled", True)) else []
            rows.append(dict(id=a.id, name=a.name, role=a.role, enabled=a.role == "master" or a.cfg.get("enabled", True),
                             status="READY" if not rs else "BLOCKED", reasons=rs,
                             broker=bi.get("broker"), server=bi.get("server"), symbol=bi.get("symbol") or a.symbol,
                             digits=bi.get("digits"), point=bi.get("point"), min_lot=bi.get("min_lot"),
                             max_lot=bi.get("max_lot"), lot_step=bi.get("lot_step"), stops_level=bi.get("stops_level"),
                             freeze_level=bi.get("freeze_level"), filling_mode=bi.get("filling_mode"),
                             spread=bi.get("spread"), latency_ms=bi.get("ping_ms"), last_tick=bi.get("tick_time"),
                             connection=a.connected, trade_allowed=bi.get("trade_allowed"),
                             data_age_s=round(time.time() - getattr(a, "broker_at", 0), 1) if bi else None))
        return dict(accounts=rows, settings=self.settings, max_spread=self._max_spread())
