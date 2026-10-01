"""
REYGUN SuperChart trading: manual entries on a master account, mirrored to follower accounts.

  * Only trades entered from SuperChart are handled (magic number mt5_ops.MAGIC, comment RGSC-<group id>).
  * Each follower uses its own FIXED lot. SL/TP are sent as price DISTANCES, so every account gets the same
    risk distance from its own fill price even if brokers quote slightly different prices.
  * Sync: when the master position is closed, partially closed, or its SL/TP moved (from SuperChart OR
    directly in MT5), the followers do the same.
  * Daily risk per account: DD limit and TP target ($ or % of the day's starting balance). When hit, all
    trades on that account are closed and it takes no new entries until the next (server) day.
  * Nothing trades automatically. Entries need ARM + a click + a confirm in the UI.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from error_log import report
import mt5_ops
from journal import Journal, score_snapshot

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(HERE, "accounts.json")
CONTRACT_SIZE = 100.0            # paper trading: XAUUSD 1 lot = 100 oz

DEFAULT_RISK = {"daily_dd": 0.0, "dd_mode": "%", "daily_tp": 0.0, "tp_mode": "%", "close_scope": "superchart"}
RISK_VERSION = 2          # v2: DD/TP lock closes only SuperChart trades unless the user picks "all" again
SAFETY_DEFAULTS = dict(close_unprotected=True, protective_sl_retry_count=3, protective_sl_retry_delay=2.0,
                       stale_follower_s=6.0, closing_timeout_s=600)
DEFAULT_MASTER = dict(name="Master", path="", login=0, symbol="", paper_balance=10000.0, **DEFAULT_RISK)
DEFAULT_FOLLOWER = dict(name="Follower", path="", login=0, symbol="", lot=0.01, enabled=True,
                        paper_balance=10000.0, **DEFAULT_RISK)


# ============================================================================ config
def load_config() -> dict:
    cfg = {"master": dict(DEFAULT_MASTER), "followers": [], "safety": {}}
    try:
        with open(CONFIG_FILE) as f:
            raw = json.load(f)
        cfg["safety"] = {k: v for k, v in (raw.get("safety") or {}).items() if k in SAFETY_DEFAULTS}
        cfg["master"].update({k: v for k, v in (raw.get("master") or {}).items() if k in DEFAULT_MASTER})
        for fl in raw.get("followers") or []:
            cfg["followers"].append(clean_follower(fl))
        if int(raw.get("risk_version", 1)) < RISK_VERSION:
            # older files had "all" as the silent default: switch to SuperChart-only; "all" must be chosen again
            for a in [cfg["master"]] + cfg["followers"]:
                a["close_scope"] = "superchart"
    except (OSError, ValueError):
        pass
    return cfg


def clean_follower(fl: dict) -> dict:
    out = dict(DEFAULT_FOLLOWER)
    out.update({k: v for k, v in (fl or {}).items() if k in DEFAULT_FOLLOWER})
    out["id"] = str(fl.get("id") or uuid.uuid4().hex[:8])
    try:
        out["lot"] = max(0.0, float(out["lot"]))
        out["login"] = int(out["login"] or 0)
        for k in ("daily_dd", "daily_tp", "paper_balance"):
            out[k] = max(0.0, float(out[k] or 0))
    except (TypeError, ValueError):
        pass
    out["dd_mode"] = "$" if out["dd_mode"] == "$" else "%"
    out["tp_mode"] = "$" if out["tp_mode"] == "$" else "%"
    out["close_scope"] = "all" if out["close_scope"] == "all" else "superchart"
    out["enabled"] = bool(out["enabled"])
    return out


def clean_master(m: dict) -> dict:
    out = dict(DEFAULT_MASTER)
    out.update({k: v for k, v in (m or {}).items() if k in DEFAULT_MASTER})
    try:
        out["login"] = int(out["login"] or 0)
    except (TypeError, ValueError):
        out["login"] = 0
    out["path"] = str(out["path"] or "").strip().strip('"')
    out["symbol"] = str(out["symbol"] or "").strip()
    for k in ("daily_dd", "daily_tp", "paper_balance"):
        try:
            out[k] = max(0.0, float(out[k] or 0))
        except (TypeError, ValueError):
            out[k] = 0.0
    out["dd_mode"] = "$" if out["dd_mode"] == "$" else "%"
    out["tp_mode"] = "$" if out["tp_mode"] == "$" else "%"
    out["close_scope"] = "all" if out["close_scope"] == "all" else "superchart"
    return out


def save_config(cfg: dict) -> None:
    cfg["risk_version"] = RISK_VERSION
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)


# ============================================================================ backends
class LocalMT5Backend:
    """Master account: the same MT5 terminal the chart data comes from (shared module, shared lock)."""

    def __init__(self, mt5, lock):
        self.mt5, self.lock = mt5, lock
        self.ok = True
        self.info = None

    def call(self, cmd, *args):
        with self.lock:
            try:
                return getattr(mt5_ops, cmd)(self.mt5, *args)
            except Exception as e:
                return dict(ok=False, error=f"{e.__class__.__name__}: {e}")

    def stop(self):
        pass


class PaperBackend:
    """Simulated account on live (or demo) prices. Positions live in memory only."""

    def __init__(self, price_fn, balance=10000.0, name="paper"):
        self.price_fn = price_fn            # () -> (bid, ask, t_msc) or None
        self.balance = float(balance)
        self.positions_ = {}
        self.closed_ = {}                   # ticket -> {exit, reason, pnl, time}
        self.pending_ = {}                  # ticket -> buy stop / sell stop
        self.next_ticket = 1000
        self.ok = True
        self.info = dict(ok=True, login=0, server="PAPER", name=name)
        self.lock = threading.Lock()

    # --- helpers
    def _px(self):
        p = self.price_fn()
        return p if p else (None, None, 0)

    def _pnl(self, p, bid, ask):
        exit_ = bid if p["side"] == 1 else ask
        return (exit_ - p["open"]) * p["side"] * p["volume"] * CONTRACT_SIZE

    def tick(self):
        """Close positions whose SL or TP was touched."""
        bid, ask, _ = self._px()
        if bid is None:
            return
        with self.lock:
            for t, o in list(self.pending_.items()):            # stop orders: trigger into a position
                if (o["side"] == 1 and ask >= o["price"]) or (o["side"] == -1 and bid <= o["price"]):
                    fill = ask if o["side"] == 1 else bid          # fills at the market (slippage on a spike)
                    self.positions_[t] = dict(symbol=o["symbol"], side=o["side"], volume=o["volume"], open=fill,
                                              sl=o["sl"], tp=o["tp"], comment=o["comment"])
                    del self.pending_[t]
            for t, p in list(self.positions_.items()):
                exit_ = bid if p["side"] == 1 else ask
                hit_sl = p["sl"] and ((p["side"] == 1 and exit_ <= p["sl"]) or (p["side"] == -1 and exit_ >= p["sl"]))
                hit_tp = p["tp"] and ((p["side"] == 1 and exit_ >= p["tp"]) or (p["side"] == -1 and exit_ <= p["tp"]))
                if hit_sl or hit_tp:
                    pnl = self._pnl(p, bid, ask)
                    self.balance += pnl
                    self._record_close(t, exit_, "SL" if hit_sl else "TP", pnl)
                    del self.positions_[t]

    def _record_close(self, t, exit_, reason, pnl):
        c = self.closed_.setdefault(t, dict(pnl=0.0))
        c.update(exit=exit_, reason=reason, time=int(time.time()))
        c["pnl"] = round(c["pnl"] + pnl, 2)

    # --- mt5_ops-compatible commands
    def call(self, cmd, *args):
        fn = getattr(self, "c_" + cmd, None)
        if fn is None:
            return dict(ok=False, error=f"paper: {cmd} not supported")
        with self.lock:
            return fn(*args)

    def c_account(self, symbol=None):
        bid, ask, t_msc = self._px()
        fl = sum(self._pnl(p, bid, ask) for p in self.positions_.values()) if bid is not None else 0.0
        return dict(ok=True, login=0, server="PAPER", name="paper", currency="USD", balance=round(self.balance, 2),
                    equity=round(self.balance + fl, 2), margin_free=0.0, trade_allowed=True, trade_expert=True,
                    terminal_connected=True, server_time=int(t_msc // 1000) if t_msc else None)

    def c_positions(self, symbol=None, magic_only=True):
        bid, ask, _ = self._px()
        out = []
        for t, p in self.positions_.items():
            out.append(dict(ticket=t, symbol=p["symbol"], side=p["side"], volume=p["volume"], open=p["open"],
                            sl=p["sl"], tp=p["tp"], price=(bid if p["side"] == 1 else ask) or p["open"],
                            pnl=round(self._pnl(p, bid, ask), 2) if bid is not None else 0.0,
                            magic=mt5_ops.MAGIC, comment=p["comment"]))
        return out

    def c_open_market(self, symbol, side, lot, sl_dist=0.0, tp_dist=0.0, comment=""):
        bid, ask, _ = self._px()
        if bid is None:
            return dict(ok=False, error="no price")
        vol = round(max(0.01, round(lot / 0.01) * 0.01), 2)
        price = ask if side == 1 else bid
        sl = round(price - side * sl_dist, 2) if sl_dist > 0 else 0.0
        tp = round(price + side * tp_dist, 2) if tp_dist > 0 else 0.0
        self.next_ticket += 1
        t = self.next_ticket
        self.positions_[t] = dict(symbol=symbol, side=side, volume=vol, open=price, sl=sl, tp=tp, comment=comment[:31])
        return dict(ok=True, ticket=t, volume=vol, price=price, sl=sl, tp=tp)

    def c_close(self, ticket, volume=None):
        p = self.positions_.get(ticket)
        if not p:
            return dict(ok=True, closed=0.0, note="already closed")
        bid, ask, _ = self._px()
        vol = p["volume"] if volume is None else min(p["volume"], round(round(volume / 0.01) * 0.01, 2))
        if vol <= 0:
            return dict(ok=False, error="volume too small")
        part = dict(p, volume=vol)
        pnl = self._pnl(part, bid, ask)
        self.balance += pnl
        p["volume"] = round(p["volume"] - vol, 2)
        exit_ = bid if p["side"] == 1 else ask
        c = self.closed_.setdefault(ticket, dict(pnl=0.0))
        c["pnl"] = round(c["pnl"] + pnl, 2)
        if p["volume"] <= 1e-9:
            self._record_close(ticket, exit_, "Closed by app/EA", 0.0)
            del self.positions_[ticket]
        return dict(ok=True, closed=vol, price=bid if p["side"] == 1 else ask)

    def c_closed_info(self, ticket):
        c = self.closed_.get(ticket)
        if not c or "exit" not in c:
            return dict(ok=False, error="position still open" if ticket in self.positions_ else "unknown ticket")
        return dict(ok=True, exit=c["exit"], reason=c["reason"], pnl=c["pnl"], time=c["time"])

    def c_modify(self, ticket, sl, tp):
        p = self.positions_.get(ticket)
        if not p:
            return dict(ok=False, error="position not found")
        p["sl"], p["tp"] = round(sl or 0.0, 2), round(tp or 0.0, 2)
        return dict(ok=True)

    def c_place_pending(self, symbol, side, lot, price, sl=0.0, tp=0.0, comment=""):
        bid, ask, _ = self._px()
        if bid is None:
            return dict(ok=False, error="no price")
        if (side == 1 and price <= ask) or (side == -1 and price >= bid):
            return dict(ok=False, error="stop price is on the wrong side of the market")
        self.next_ticket += 1
        t = self.next_ticket
        vol = round(max(0.01, round(lot / 0.01) * 0.01), 2)
        self.pending_[t] = dict(symbol=symbol, side=side, volume=vol, price=round(price, 2), sl=round(sl or 0, 2),
                                tp=round(tp or 0, 2), comment=comment[:31])
        return dict(ok=True, ticket=t, price=round(price, 2), volume=vol, sl=round(sl or 0, 2), tp=round(tp or 0, 2))

    def c_price(self, symbol=None):
        bid, ask, t = self._px()
        return dict(ok=bid is not None, bid=bid, ask=ask, time_msc=t)

    def c_orders(self, symbol=None):
        return [dict(ticket=t, **o) for t, o in self.pending_.items()]

    def c_cancel_order(self, ticket):
        self.pending_.pop(ticket, None)
        return dict(ok=True)

    def c_broker_info(self, symbol):
        bid, ask, t = self._px()
        return dict(ok=True, broker="PAPER", server="PAPER", symbol=symbol, selected=True, digits=2, point=0.01,
                    min_lot=0.01, max_lot=100.0, lot_step=0.01, stops_level=0, freeze_level=0, filling_mode="PAPER",
                    trade_mode=4, contract_size=CONTRACT_SIZE, bid=bid, ask=ask, spread=round(ask - bid, 2) if bid is not None else None,
                    tick_time=int(t // 1000) if t else None, connected=True, trade_allowed=True, ping_ms=0.0,
                    margin_free=self.balance, call_ms=0.0)

    def c_find_symbols(self, base="XAUUSD", limit=15):
        return dict(ok=True, base=base, symbols=[dict(name=base, description="paper (simulated)", trade_mode=4,
                                                      tradable=True, visible=True)], best=base)

    def c_connection_check(self, symbol, lot=0.01):
        bid, ask, _ = self._px()
        ok = bid is not None
        checks = [dict(name="Account", ok=True, detail="PAPER (simulated)"),
                  dict(name="Price", ok=ok, detail=f"bid {bid} ask {ask}" if ok else "no price yet")]
        return dict(ok=ok, checks=checks, login=0, server="PAPER", balance=round(self.balance, 2))

    def c_close_all(self, scope="superchart"):
        n = 0
        for t in list(self.positions_):
            self.c_close(t)
            n += 1
        return dict(ok=True, closed=n, errors=[])

    def stop(self):
        pass


# ============================================================================ accounts
class Account:
    def __init__(self, acc_id, role, cfg, symbol):
        self.id, self.role, self.cfg = acc_id, role, cfg
        self.symbol = symbol
        self.backend = None
        self.status = "connecting"
        self.error = ""
        self.info = {}
        self.positions = []
        self.day_key = None
        self.day_start = None
        self.locked = ""            # reason when locked
        self.locked_day = None
        self.fresh = False          # positions refreshed successfully in the last cycle
        self.orders = []            # our pending orders (News Trader)
        self.orders_fresh = False
        self.orders_read_at = 0.0
        self.last_ok = 0.0          # last successful account read (watchdog)
        self.paused = ""            # set by the copier reconciliation (no new trades until you resume)

    @property
    def name(self):
        return self.cfg.get("name") or self.id

    @property
    def connected(self):
        return self.backend is not None and self.status == "connected"

    def pos(self, ticket):
        for p in self.positions:
            if p["ticket"] == ticket:
                return p
        return None

    def limits(self):
        base = self.day_start or 0.0
        dd = self.cfg.get("daily_dd", 0.0) or 0.0
        tp = self.cfg.get("daily_tp", 0.0) or 0.0
        dd_amt = dd if self.cfg.get("dd_mode") == "$" else base * dd / 100
        tp_amt = tp if self.cfg.get("tp_mode") == "$" else base * tp / 100
        return dd_amt, tp_amt

    def view(self):
        eq = self.info.get("equity")
        day_pl = (eq - self.day_start) if eq is not None and self.day_start is not None else None
        dd_amt, tp_amt = self.limits()
        return dict(id=self.id, role=self.role, name=self.name, symbol=self.symbol, status=self.status,
                    path=self.cfg.get("path", ""),
                    error=self.error, connected=self.connected, login=self.info.get("login"),
                    server=self.info.get("server"), balance=self.info.get("balance"), equity=eq,
                    trade_allowed=self.info.get("trade_allowed", False), day_start=self.day_start, day_pl=day_pl,
                    dd_amount=dd_amt, tp_amount=tp_amt, locked=self.locked,
                    lot=self.cfg.get("lot"), enabled=self.cfg.get("enabled", True), paused=self.paused,
                    open_trades=len(self.positions))


class Group:
    def __init__(self, gid, side, created, legs, m_vol, m_sl, m_tp, m_open, sl_dist=0.0, tp_dist=0.0,
                 state="MANAGING", protected=None, closing=None, mfe_px=None, mae_px=None, mfe_t=None, mae_t=None,
                 pm_mode="NONE", pm_done=None, **_):
        self.gid, self.side, self.created = gid, side, created
        self.legs = legs                    # account id -> ticket
        self.m_vol, self.m_sl, self.m_tp, self.m_open = m_vol, m_sl, m_tp, m_open
        self.sl_dist, self.tp_dist = sl_dist, tp_dist     # what was asked for (protective SL watchdog)
        self.state = state                  # lifecycle: PROTECTION_PENDING / PROTECTED / MANAGING / CLOSING
        self.protected = protected or {}    # account id -> True once its leg was seen WITH a stop loss
        self.closing = closing              # dict(since, left=[follower ids still to close]) after the master closed
        self.mfe_px, self.mae_px = mfe_px, mae_px      # best / worst price seen while open (master prices)
        self.mfe_t, self.mae_t = mfe_t, mae_t          # when (wall time)
        self.missing = 0                    # consecutive checks where the master position was not found
        self.pm_mode = pm_mode              # position manager: NONE / BE_ACTIVE / PROFIT_LOCK_ACTIVE / TRAIL_ACTIVE
        self.pm_done = list(pm_done or [])  # partial-close steps already done

    def to_json(self):
        d = self.__dict__.copy()
        for k in ("missing", "sl_tries", "stale_noted"):
            d.pop(k, None)
        return d


# ============================================================================ manager
class TradeManager:
    def __init__(self, hub, paper: bool):
        self.hub = hub
        self.src = hub.src
        self.paper = paper or not self.src.live
        self.armed = False
        self.lock = threading.RLock()
        self.cfg = load_config()
        self.groups: dict[str, Group] = {}
        self.pool = ThreadPoolExecutor(max_workers=8)
        suffix = "_paper" if self.paper else ""
        self.groups_file = os.path.join(HERE, f"trade_groups{suffix}.json")
        self.risk_file = os.path.join(HERE, f"risk_state{suffix}.json")
        self.journal = Journal(self.paper)
        self.master = Account("master", "master", self.cfg["master"], self.src.symbol)
        self.followers: list[Account] = []
        self._connect_master()
        self._load_groups()
        self._load_risk()
        self._build_followers()
        self.safety = dict(SAFETY_DEFAULTS, **{k: v for k, v in self.cfg.get("safety", {}).items() if k in SAFETY_DEFAULTS})
        self.processed = {}                 # request_id -> result (duplicate order protection)
        self.halted = ""                    # set by STOP NEW TRADES / EMERGENCY STOP (cleared when you ARM again)
        self.running = True
        self.last_cycle = time.time()
        threading.Thread(target=self._loop, daemon=True).start()

    # ------------------------------------------------------------------ setup
    def _price_fn(self):
        tk = self.hub.last_tick
        return (tk.bid, tk.ask, tk.t_msc) if tk else None

    def _connect_master(self):
        if self.paper:
            self.master.backend = PaperBackend(self._price_fn, self.cfg["master"].get("paper_balance", 10000), "master")
        else:
            self.master.backend = LocalMT5Backend(self.src.mt5, self.hub.lock)
        self.master.status = "connected"

    def _build_followers(self):
        old = {f.id: f for f in self.followers}
        new = []
        for fc in self.cfg["followers"]:
            prev = old.pop(fc["id"], None)
            same_conn = prev and prev.cfg.get("path") == fc.get("path") and prev.cfg.get("login") == fc.get("login")
            if prev and same_conn:
                prev.cfg = fc
                prev.symbol = fc.get("symbol") or self.src.symbol
                new.append(prev)
                continue
            if prev and prev.backend:
                prev.backend.stop()
            a = Account(fc["id"], "follower", fc, fc.get("symbol") or self.src.symbol)
            self._restore_risk(a)
            new.append(a)
            threading.Thread(target=self._connect_follower, args=(a,), daemon=True).start()
        for gone in old.values():
            if gone.backend:
                gone.backend.stop()
        self.followers = new

    def _connect_follower(self, a: Account):
        if self.paper:
            a.backend = PaperBackend(self._price_fn, a.cfg.get("paper_balance", 10000), a.name)
            a.status = "connected"
            return
        if not a.cfg.get("path"):
            a.status, a.error = "error", "no MT5 terminal path set"
            return
        a.status = "connecting"
        try:
            from account_worker import RemoteMT5
            be = RemoteMT5(a.cfg["path"], a.cfg.get("login") or None)
        except Exception as e:
            a.status, a.error = "error", f"{e.__class__.__name__}: {e}"
            return
        if not be.ok:
            a.status, a.error = "error", (be.info or {}).get("error", "could not connect")
            be.stop()
            return
        a.backend, a.status, a.error = be, "connected", ""
        self.hub.note("info", f"account {a.name} connected (#{be.info.get('login')} {be.info.get('server')})")

    # ------------------------------------------------------------------ persistence
    def _load_groups(self):
        try:
            with open(self.groups_file) as f:
                for d in json.load(f):
                    g = Group(**d)
                    self.groups[g.gid] = g
        except (OSError, ValueError, TypeError):
            pass

    def _save_groups(self):
        try:
            with open(self.groups_file, "w") as f:
                json.dump([g.to_json() for g in self.groups.values()], f, indent=1)
        except OSError:
            pass

    def _load_risk(self):
        try:
            with open(self.risk_file) as f:
                self._risk_saved = json.load(f)
        except (OSError, ValueError):
            self._risk_saved = {}
        self._restore_risk(self.master)

    def _restore_risk(self, a):
        s = getattr(self, "_risk_saved", {}).get(a.id)
        if s:
            a.day_key, a.day_start = s.get("day_key"), s.get("day_start")
            a.locked, a.locked_day = s.get("locked", ""), s.get("locked_day")

    def _save_risk(self):
        data = {a.id: dict(day_key=a.day_key, day_start=a.day_start, locked=a.locked, locked_day=a.locked_day)
                for a in [self.master] + self.followers}
        try:
            with open(self.risk_file, "w") as f:
                json.dump(data, f)
        except OSError:
            pass

    # ------------------------------------------------------------------ loop
    def _day_key(self):
        tk = self.hub.last_tick
        if tk:
            return int(tk.t_msc // 1000 // 86400)        # broker server day
        return int(time.time() // 86400)

    def _loop(self):
        while self.running:
            t0 = time.time()
            self.last_cycle = t0
            try:
                self.refresh()
                self.risk_check()
                self.sync()
                self.protect()
                self.track_excursion()
            except Exception as e:
                self.hub.note("error", f"trading loop: {e}")
            time.sleep(max(0.2, 1.0 - (time.time() - t0)))

    def all_accounts(self):
        return [self.master] + list(self.followers)

    def refresh(self):
        def one(a):
            started = time.time()
            a.fresh = False
            if not a.connected:
                return
            if isinstance(a.backend, PaperBackend):
                a.backend.tick()
            info = a.backend.call("account", a.symbol)
            if info.get("ok"):
                a.info = info
                a.last_ok = time.time()
            a.orders_fresh = False
            ords = a.backend.call("orders", None)
            if isinstance(ords, list):
                a.orders, a.orders_fresh = ords, True
                a.orders_read_at = started          # this read began at `started` (News Trader: stale-read guard)
            pos = a.backend.call("positions", None, True)
            if isinstance(pos, list):
                a.positions = pos
                a.fresh = True             # this cycle's positions are trustworthy
            if time.time() - getattr(a, "broker_at", 0) > 10:
                bi = a.backend.call("broker_info", a.symbol)
                if bi.get("ok"):
                    a.broker, a.broker_at = bi, time.time()
        list(self.pool.map(one, self.all_accounts()))

    def _acct_day(self, a):
        """Each account's own broker day (its server clock); falls back to the master's day."""
        st = a.info.get("server_time")
        return int(st // 86400) if st else self._day_key()

    def risk_check(self):
        changed = False
        for a in self.all_accounts():
            if not a.connected or "balance" not in a.info:
                continue
            day = self._acct_day(a)
            if a.day_key != day:
                a.day_key, a.day_start = day, a.info["balance"]
                if a.locked and a.locked_day != day:
                    self.hub.note("info", f"{a.name}: new day, unlocked ({a.locked})")
                    a.locked, a.locked_day = "", None
                changed = True
            if a.locked or a.day_start is None:
                continue
            pl = a.info["equity"] - a.day_start
            dd_amt, tp_amt = a.limits()
            reason = ""
            if dd_amt > 0 and pl <= -dd_amt:
                reason = f"DAILY DD hit ({pl:+.2f})"
            elif tp_amt > 0 and pl >= tp_amt:
                reason = f"DAILY TP hit ({pl:+.2f})"
            if reason:
                a.locked, a.locked_day = reason, day
                changed = True
                scope = a.cfg.get("close_scope", "superchart")
                if a.role == "master":
                    for g in self.groups.values():
                        g.pending_reason = "risk"          # journal: closed by the daily DD/TP lock
                r = a.backend.call("close_all", scope)
                self.hub.note("error" if "DD" in reason else "info",
                              f"{a.name}: {reason} -> closed {r.get('closed', 0)} trade(s), locked until next day")
                if getattr(self.hub, "alerts", None):
                    self.hub.alerts.on_risk(a.name, reason, r.get("closed", 0))
        if changed:
            self._save_risk()

    def _fresh(self, a):
        """Positions of this account were read successfully in the last cycle (never act on stale data)."""
        return a is not None and a.connected and getattr(a, "fresh", False)

    def _stale_note(self, g, names):
        now = time.time()
        if now - getattr(g, "stale_noted", 0) > 30:
            g.stale_noted = now
            self.hub.note("warn", f"group {g.gid}: FOLLOWER DATA STALE ({', '.join(names)}), waiting for a fresh read")

    def sync(self):
        """Mirror master changes (close / partial / SL-TP) onto follower legs.
        Safety: nothing is done to a follower unless its positions were freshly read in this cycle; when one of
        the involved followers is stale the whole group waits for the next cycle (so nothing is done twice)."""
        with self.lock:
            if not self.master.connected or not self.groups:
                return
            fmap = {f.id: f for f in self.followers}
            dirty = False
            if not self._fresh(self.master):
                return                                  # master read failed this cycle: do nothing
            now = time.time()
            for gid, g in list(self.groups.items()):
                if now - g.created < 5 and not g.closing:
                    continue                            # brand-new trade: let the positions settle first
                f_legs = {fid: t for fid, t in g.legs.items() if fid != "master"}
                # ---- master already closed: close the remaining follower legs (retry until done / timeout)
                if g.closing:
                    left = []
                    for fid in g.closing["left"]:
                        f, t = fmap.get(fid), f_legs.get(fid)
                        if t is None or f is None:
                            continue                    # follower removed: nothing we can do (reconciliation reports)
                        if not self._fresh(f):
                            left.append(fid)
                            continue
                        if f.pos(t):
                            r = f.backend.call("close", t, None)
                            if not r.get("ok"):
                                self.hub.note("error", f"{f.name}: close failed: {r.get('error')}")
                                left.append(fid)
                    g.closing["left"] = left
                    if left and now - g.closing["since"] < self.safety["closing_timeout_s"]:
                        self._stale_note(g, [fmap[x].name for x in left if x in fmap])
                        dirty = True
                        continue
                    if left:
                        self.hub.note("error", f"group {gid}: could NOT close follower leg(s) "
                                               f"{', '.join(fmap[x].name for x in left if x in fmap)} - check them in MT5")
                        self._alert("⚠ <b>COPIER</b>", f"Trade {gid}: hindi naisara ang follower leg(s) "
                                    f"{', '.join(fmap[x].name for x in left if x in fmap)}. Tingnan sa MT5.")
                    self._finalize(g, getattr(g, "pending_reason", "master"))
                    del self.groups[gid]
                    dirty = True
                    continue
                mp = self.master.pos(g.legs.get("master"))
                if mp is None:
                    g.missing += 1
                    if g.missing < 2:
                        continue                        # must be missing on two reads in a row
                    g.state = "CLOSING"
                    g.closing = dict(since=now, left=list(f_legs))
                    self.journal.lifecycle(gid, "CLOSING", "master position closed -> closing followers")
                    self.hub.note("info", f"group {gid}: master closed -> closing followers")
                    dirty = True
                    continue                            # handled from the next cycle with fresh follower reads
                g.missing = 0
                vol_changed = mp["volume"] < g.m_vol - 1e-9 and g.m_vol > 0
                sltp_changed = abs(mp["sl"] - g.m_sl) > 1e-6 or abs(mp["tp"] - g.m_tp) > 1e-6
                if vol_changed or sltp_changed:
                    stale = [fmap[fid].name for fid in f_legs if fid in fmap and fmap[fid].connected
                             and not self._fresh(fmap[fid])]
                    if stale:
                        self._stale_note(g, stale)
                        continue                        # try again next cycle, all at once
                if vol_changed:
                    frac = 1 - mp["volume"] / g.m_vol
                    for fid, t in f_legs.items():
                        f = fmap.get(fid)
                        fp = f.pos(t) if self._fresh(f) else None
                        if fp:
                            f.backend.call("close", t, fp["volume"] * frac)
                    self.hub.note("info", f"group {gid}: master partial close {frac:.0%} -> followers")
                    self.journal.partial(gid, frac, "master")
                    g.m_vol = mp["volume"]
                    dirty = True
                if sltp_changed:
                    self.hub.note("info", f"group {gid}: master SL {mp['sl'] or '-'} TP {mp['tp'] or '-'} -> followers")
                    self.journal.sl_changed(gid, mp["sl"], mp["tp"], "master")
                    self._mirror_sltp(g, mp, fmap)
                    g.m_sl, g.m_tp = mp["sl"], mp["tp"]
                    dirty = True
                # drop legs that closed on their own (their own SL/TP)
                for fid, t in list(f_legs.items()):
                    f = fmap.get(fid)
                    if f and self._fresh(f) and not f.pos(t):
                        g.legs.pop(fid)
                        dirty = True
            if dirty:
                self._save_groups()

    def protect(self):
        """Protective SL watchdog: every leg of a new trade must show a stop loss. If one does not, attach it
        (retry), alert, and when that keeps failing close that REYGUN leg (safety.close_unprotected).
        Once a leg was seen WITH a stop, later changes are the user's (e.g. SL removed on purpose) and are left alone."""
        with self.lock:
            if not self.groups:
                return
            accts = {a.id: a for a in self.all_accounts()}
            now = time.time()
            dirty = False
            for gid, g in list(self.groups.items()):
                if not g.sl_dist or g.closing:
                    continue
                for acc_id, t in list(g.legs.items()):
                    if g.protected.get(acc_id):
                        continue
                    a = accts.get(acc_id)
                    if not self._fresh(a):
                        continue
                    p = a.pos(t)
                    if p is None:
                        continue
                    if p["sl"]:
                        g.protected[acc_id] = True
                        dirty = True
                        if all(g.protected.get(x) for x in g.legs):
                            g.state = "PROTECTED"
                            self.journal.lifecycle(gid, "PROTECTED", "every leg has a stop loss")
                        continue
                    tries = getattr(g, "sl_tries", {})
                    g.sl_tries = tries
                    n, last = tries.get(acc_id, (0, 0.0))
                    if now - last < self.safety["protective_sl_retry_delay"]:
                        continue
                    if n == 0:
                        g.state = "PROTECTION_PENDING"
                        self.journal.lifecycle(gid, "UNPROTECTED", f"{a.name}: position has NO stop loss")
                        self.hub.note("error", f"⚠ UNPROTECTED POSITION {gid} on {a.name}: attaching SL…")
                        self._alert("⚠ <b>UNPROTECTED POSITION</b>", f"Trade {gid} sa {a.name}: walang SL. Ikinakabit…")
                    if n < self.safety["protective_sl_retry_count"]:
                        sl = round(p["open"] - g.side * g.sl_dist, 2)
                        tp = p["tp"] or (round(p["open"] + g.side * g.tp_dist, 2) if g.tp_dist else 0.0)
                        r = a.backend.call("modify", t, sl, tp)
                        tries[acc_id] = (n + 1, now)
                        if r.get("ok"):
                            self.hub.note("info", f"{a.name}: SL {sl} attached to trade {gid}")
                            self.journal.lifecycle(gid, "SL_ATTACHED", f"{a.name}: SL {sl}")
                        continue
                    if n == self.safety["protective_sl_retry_count"]:
                        tries[acc_id] = (n + 1, now)
                        if self.safety["close_unprotected"]:
                            r = a.backend.call("close", t, None)
                            msg = "CLOSED (walang SL na maikabit)" if r.get("ok") else f"close failed: {r.get('error')}"
                        else:
                            msg = "NAKABUKAS PA RIN NANG WALANG SL: isara o lagyan ng SL sa MT5"
                        self.hub.note("error", f"UNPROTECTED {gid} on {a.name}: {msg}")
                        self.journal.lifecycle(gid, "UNPROTECTED_FINAL", f"{a.name}: {msg}")
                        self._alert("🛑 <b>UNPROTECTED POSITION</b>", f"Trade {gid} sa {a.name}: {msg}")
            if dirty:
                self._save_groups()

    def track_excursion(self):
        """MFE / MAE: the best and the worst price each open trade has seen (master exit-side price, sampled every
        loop, about once a second - not tick-exact)."""
        tk = self.hub.last_tick
        if tk is None or not self.groups:
            return
        now = time.time()
        for g in list(self.groups.values()):
            if g.closing or not g.m_open:
                continue
            px = tk.bid if g.side == 1 else tk.ask
            fav = (px - g.m_open) * g.side
            if g.mfe_px is None or fav > (g.mfe_px - g.m_open) * g.side:
                g.mfe_px, g.mfe_t = px, now
            if g.mae_px is None or fav < (g.mae_px - g.m_open) * g.side:
                g.mae_px, g.mae_t = px, now

    @staticmethod
    def classify_exit(reason, source, r):
        src = str(source or "")
        if src.startswith("guard:"):
            return "EXIT_GUARD"
        if src == "risk":
            return "RISK_LOCK"
        if src == "emergency":
            return "SYSTEM"
        if src == "reconciliation":
            return "RECONCILIATION"
        if src == "superchart":
            return "MANUAL"
        rs = str(reason or "")
        if rs == "TP":
            return "TP"
        if rs == "SL":
            if r is not None and abs(r) < 0.15:
                return "BE"
            if r is not None and r > 0.15:
                return "TRAIL"
            return "SL"
        if rs.startswith("Manual"):
            return "MANUAL"
        if rs in ("Stop out",):
            return "SYSTEM"
        if rs.startswith("Closed by app"):
            return "SYSTEM"
        return "UNKNOWN"

    def _alert(self, head, text):
        n = getattr(self.hub, "notifier", None)
        if n:
            n.send("risk", f"{head}\n{text}")

    def _finalize(self, g, source):
        """Group is fully closed: ask every account how its leg ended and write the journal. Runs in background
        because the broker's deal history can take a moment to show the closing deal."""
        legs = dict(g.legs)

        def work():
            accts = {a.id: a for a in self.all_accounts()}
            results = {}
            for attempt in range(6):
                for acc_id, t in legs.items():
                    if acc_id in results:
                        continue
                    a = accts.get(acc_id)
                    if not a or not a.connected:
                        results[acc_id] = None
                        continue
                    r = a.backend.call("closed_info", t)
                    if r.get("ok"):
                        results[acc_id] = r
                if len(results) == len(legs):
                    break
                time.sleep(1.0 + attempt)
            m = results.get("master") or {}
            reason = m.get("reason", "?")
            if source == "superchart":
                reason = "Manual (SuperChart)"
            elif source == "risk":
                reason = "Risk lock (DD/TP)"
            elif str(source).startswith("guard:"):
                reason = str(source)[6:]
            elif source == "emergency":
                reason = "Emergency stop"
            pnl = {(accts[k].name if k in accts else k): (v or {}).get("pnl") for k, v in results.items()}
            try:
                tk = self.hub.last_tick
                ex = m.get("exit")
                r_mult = round((ex - g.m_open) * g.side / g.sl_dist, 2) if ex and g.sl_dist else None
                extra = dict(exit_reason=self.classify_exit(m.get("reason"), source, r_mult))
                if g.mfe_px is not None:
                    fav, adv = (g.mfe_px - g.m_open) * g.side, (g.mae_px - g.m_open) * g.side
                    extra.update(mfe_price=round(g.mfe_px, 2), mae_price=round(g.mae_px, 2),
                                 mfe_usd_per_lot=round(fav * 100, 2), mae_usd_per_lot=round(adv * 100, 2),
                                 mfe_r=round(fav / g.sl_dist, 2) if g.sl_dist else None,
                                 mae_r=round(adv / g.sl_dist, 2) if g.sl_dist else None,
                                 time_to_mfe=round(g.mfe_t - g.created) if g.mfe_t else None,
                                 time_to_mae=round(g.mae_t - g.created) if g.mae_t else None)
                self.journal.closed(g.gid, m.get("exit"), reason, pnl, source,
                                    t_srv=int(tk.t_msc // 1000) if tk else None, **extra)
                self.hub.note("info", f"journal: {g.gid} closed · {reason} · P/L {sum(x for x in pnl.values() if x):+.2f}")
                if getattr(self.hub, "alerts", None):
                    self.hub.alerts.on_close(g.gid)
            except Exception as e:
                self.hub.note("error", f"journal: {e}")

        threading.Thread(target=work, daemon=True).start()

    def _mirror_sltp(self, g, mp, fmap):
        sl_d = (mp["sl"] - mp["open"]) if mp["sl"] else None
        tp_d = (mp["tp"] - mp["open"]) if mp["tp"] else None
        for fid, t in g.legs.items():
            f = fmap.get(fid)
            fp = f.pos(t) if f and f.connected else None
            if fp:
                sl = fp["open"] + sl_d if sl_d is not None else 0.0
                tp = fp["open"] + tp_d if tp_d is not None else 0.0
                r = f.backend.call("modify", t, sl, tp)
                if not r.get("ok"):
                    self.hub.note("error", f"{f.name}: SL/TP sync failed: {r.get('error')}")

    # ------------------------------------------------------------------ actions
    def can_trade(self):
        if not self.armed:
            return False, "trading is not armed"
        if not self.master.connected:
            return False, "master account not connected"
        if getattr(self.src, "login_error", ""):
            return False, self.src.login_error
        if self.master.locked:
            return False, f"master locked: {self.master.locked}"
        if not self.paper and not self.master.info.get("trade_allowed", False):
            return False, "Algo Trading is OFF in the master MT5 terminal"
        return True, ""

    def entry_plan(self, lot, side=1, sl_dist=None, tp_dist=None):
        ex = getattr(self.hub, "execution", None)
        m_why = ""
        if ex is not None:
            rs = ex.check_account(self.master, side, lot, sl_dist, tp_dist)
            m_why = "; ".join(x["code"] for x in rs)
        plan = [dict(id="master", name=self.master.name, lot=lot, ok=not m_why, why=m_why)]
        for f in self.followers:
            why = ("disabled" if not f.cfg.get("enabled") else "not connected" if not f.connected else
                   f"locked: {f.locked}" if f.locked else f"PAUSED: {f.paused}" if getattr(f, "paused", "") else
                   "lot is 0" if not f.cfg.get("lot") else
                   "Algo Trading OFF" if not self.paper and not f.info.get("trade_allowed", True) else "")
            if not why and ex is not None:
                ok_f, why_f = ex.follower_ok(f, side, sl_dist, tp_dist)
                why = "" if ok_f else why_f
            plan.append(dict(id=f.id, name=f.name, lot=f.cfg.get("lot"), ok=not why, why=why))
        return plan

    def _news_context(self):
        """The nearest high-impact event at entry time (for the journal)."""
        nf = getattr(self.hub, "news", None)
        if not nf:
            return None
        try:
            w = nf.upcoming_warning()
        except Exception as e:
            report("DATA_ERROR", "trading.news_context", "NEWS_READ_FAILED", e)
            return None
        if not w:
            return None
        return dict(title=w["title"], currency=w["currency"], impact=w["impact"], minutes=w["minutes"],
                    active=w["active"])

    def check_request(self, side, lot, sl_dist, tp_dist, kind="manual"):
        """Everything that must hold before an order may be sent (no side effects). -> (ok, reason)"""
        ok, why = self.can_trade()
        if not ok:
            return False, why
        if self.halted:
            return False, f"trading halted: {self.halted}"
        if side not in (1, -1) or lot <= 0 or sl_dist < 0 or tp_dist < 0:
            return False, "invalid order"
        at = getattr(self.hub, "autotrader", None)
        if (at is None or at.settings.get("no_opposite", True)) and any(g.side == -side for g in self.groups.values()):
            n_opp = sum(1 for g in self.groups.values() if g.side == -side)
            return False, (f"bawal ang kontrang trade: may {n_opp} bukas na {'SELL' if side == 1 else 'BUY'}. "
                           f"Isara muna, o i-OFF ang 'no opposite' sa AUTO TRADE ⚙.")
        risk = getattr(self.hub, "risk", None)                 # central blockers (stale price, spread, news, ...)
        if risk is not None:
            hard = risk.hard_only(risk.hard_blockers(side, kind, lot, sl_dist, tp_dist))
            if hard:
                return False, "BLOCKED: " + "; ".join(f"{b['code']} ({b['text']})" for b in hard)
        ex = getattr(self.hub, "execution", None)              # broker / execution checks (execution_guard.py)
        if ex is not None:
            r = ex.check(side, lot, sl_dist, tp_dist)
            if not r["ok"]:
                return False, "EXECUTION: " + "; ".join(f"{x['code']} ({x['text']})" for x in r["reasons"])
        return True, ""

    def open(self, side, lot, sl_dist, tp_dist, kind="manual", note="", request_id=None, lifecycle=None, meta=None):
        """Send one trade to the master and every enabled follower.
        request_id: the same id is never executed twice (a retry gets the first result back, ALREADY_EXECUTED)."""
        if request_id:
            with self.lock:
                prev = self.processed.get(request_id)
                if prev is not None:
                    return dict(prev, ok=False, error="ALREADY_EXECUTED", duplicate=True)
                self.processed[request_id] = dict(ok=False, error="in progress")
                if len(self.processed) > 2000:
                    for k in list(self.processed)[:500]:
                        self.processed.pop(k, None)
        life = list(lifecycle or [])
        stamp = lambda st, note_="": life.append(dict(t=time.time(), state=st, note=note_))
        if not life:
            stamp("REQUESTED", kind)
        ok, why = self.check_request(side, lot, sl_dist, tp_dist, kind)
        if not ok:
            res = dict(ok=False, error=why)
            if request_id:
                self.processed[request_id] = res
            return res
        stamp("RISK_APPROVED")
        de = getattr(self.hub, "decision", None)
        extra = de.snapshot(side, kind) if de is not None else {}        # the decision BEFORE the order
        if meta:
            extra.update({k: v for k, v in meta.items() if k not in ("gid",) and v is not None})
        with self.lock:
            gid = uuid.uuid4().hex[:6]
            comment = f"{mt5_ops.COMMENT_PREFIX}{gid}"
            stamp("SENT", "master")
            rm = self.master.backend.call("open_market", self.master.symbol, side, lot, sl_dist, tp_dist, comment)
            results = [dict(account=self.master.name, **rm)]
            if not rm.get("ok"):
                self.hub.note("error", f"master order failed: {rm.get('error')}")
                stamp("FAILED", str(rm.get("error")))
                try:
                    self.journal.failed(gid, self.master.symbol, side, lot, sl_dist, tp_dist, kind, str(rm.get("error")),
                                        life, self.paper, request_id)
                except Exception as e:
                    self.hub.note("error", f"journal: {e}")
                res = dict(ok=False, error=f"master: {rm.get('error')}", results=results)
                if request_id:
                    self.processed[request_id] = res
                return res
            stamp("FILLED", f"master {rm['volume']} @ {rm['price']}")
            g = Group(gid, side, time.time(), {"master": rm["ticket"]}, rm["volume"], rm["sl"], rm["tp"], rm["price"],
                      sl_dist=sl_dist, tp_dist=tp_dist, state="PROTECTION_PENDING" if sl_dist else "MANAGING")
            todo = [f for f in self.followers if f.connected and f.cfg.get("enabled") and not f.locked
                    and not getattr(f, "paused", "")
                    and f.cfg.get("lot") and (self.paper or f.info.get("trade_allowed", True))]
            ex = getattr(self.hub, "execution", None)
            if ex is not None:                          # skip followers the broker would reject (lot step, stops...)
                keep = []
                for f in todo:
                    ok_f, why_f = ex.follower_ok(f, side, sl_dist, tp_dist)
                    if ok_f:
                        keep.append(f)
                    else:
                        stamp("FOLLOWER_SKIPPED", f"{f.name}: {why_f}")
                        self.hub.note("warn", f"{f.name}: skipped by the execution guard ({why_f})")
                todo = keep

            def send(f):
                return f, f.backend.call("open_market", f.symbol, side, f.cfg["lot"], sl_dist, tp_dist, comment)

            for f, r in self.pool.map(send, todo):
                results.append(dict(account=f.name, **r))
                if r.get("ok"):
                    g.legs[f.id] = r["ticket"]
                    stamp("FILLED", f"{f.name} {r.get('volume')} @ {r.get('price')}")
                else:
                    self.hub.note("error", f"{f.name}: order failed: {r.get('error')}")
                    stamp("FOLLOWER_FAILED", f"{f.name}: {r.get('error')}")
            if sl_dist and not rm.get("sl"):
                stamp("PROTECTION_PENDING", "master filled without SL")
            self.refresh()
            self.groups[gid] = g
            self._save_groups()
            try:
                names = [self.master.name] + [f.name for f in self.followers if f.id in g.legs]
                rg = getattr(self.hub, "regime", None)
                tk = self.hub.last_tick
                self.journal.opened(gid, self.master.symbol, side, rm["volume"], rm["price"], rm["sl"], rm["tp"],
                                    sl_dist, tp_dist, names, score_snapshot(self.hub.read), self.paper,
                                    news=self._news_context(), kind=kind, regime=rg.snapshot() if rg else None,
                                    t_srv=int(tk.t_msc // 1000) if tk else None, lifecycle=life,
                                    request_id=request_id, **extra)
                if de is not None:
                    de.event(f"{'BUY' if side == 1 else 'SELL'} executed ({kind}) @ {rm['price']:.2f} · {gid}", "trade")
                if getattr(self.hub, "alerts", None):
                    self.hub.alerts.on_open(gid)
            except Exception as e:
                self.hub.note("error", f"journal: {e}")
        word = "BUY" if side == 1 else "SELL"
        self.hub.note("signal", f"{kind.upper()} {word} {lot} @ {rm['price']:.2f} on {len(g.legs)} account(s)"
                      + (f" · {note}" if note else ""),
                      plugin="Auto trade" if kind == "auto" else "Manual entry", dir=side, tf="", bar=time.strftime("%m-%d %H:%M"))
        res = dict(ok=True, gid=gid, results=results)
        if request_id:
            self.processed[request_id] = res
        return res

    # ------------------------------------------------------------------ emergency
    def cancel_pending(self):
        """Cancel every REYGUN pending order (magic) on every connected account. -> count"""
        n = 0
        for a in self.all_accounts():
            if not a.connected:
                continue
            ords = a.backend.call("orders", None)
            if isinstance(ords, list):
                for o in ords:
                    r = a.backend.call("cancel_order", o["ticket"])
                    n += bool(r.get("ok"))
        nt = getattr(self.hub, "newstrader", None)
        if nt is not None:
            try:
                nt.set_enabled(False)
            except Exception as e:
                report("EXECUTION_ERROR", "trading.cancel_pending", "NEWS_TRADER_OFF_FAILED", e)
        return n

    def stop_new_trades(self, reason="STOP NEW TRADES"):
        """AUTO OFF, ARM OFF, new requests rejected, REYGUN pending orders cancelled. Open positions stay."""
        at = getattr(self.hub, "autotrader", None)
        if at is not None and at.enabled:
            at.set_enabled(False)
        self.armed = False
        self.halted = f"{reason} {time.strftime('%H:%M:%S')}"
        n = self.cancel_pending()
        self.hub.note("error", f"{reason}: AUTO OFF, DISARMED, {n} pending order(s) cancelled")
        self._alert(f"🛑 <b>{reason}</b>", f"AUTO OFF · DISARMED · {n} pending order(s) cancelled")
        return dict(ok=True, cancelled=n)

    def emergency_stop(self, close_positions=False):
        """STOP NEW TRADES + (only when explicitly asked) close REYGUN positions. Never touches other EAs / manual."""
        r = self.stop_new_trades("EMERGENCY STOP")
        closed = None
        if close_positions:
            closed = self.close_everything(source="emergency")
            self.hub.note("error", "EMERGENCY STOP: every SuperChart position closed")
        return dict(ok=True, cancelled=r["cancelled"], closed=closed)

    def _group(self, gid):
        g = self.groups.get(gid)
        if not g:
            raise KeyError("trade group not found")
        return g

    def _acct(self, acc_id):
        if acc_id == "master":
            return self.master
        return next((f for f in self.followers if f.id == acc_id), None)

    def close_group(self, gid, fraction=1.0, source="superchart"):
        with self.lock:
            g = self._group(gid)
            fraction = max(0.01, min(1.0, float(fraction)))
            jobs = []
            for acc_id, t in g.legs.items():
                a = self._acct(acc_id)
                ok_read = a is not None and a.connected and (acc_id == "master" or self._fresh(a))
                p = a.pos(t) if ok_read else None          # followers: never act on a stale read
                if p:
                    jobs.append((a, t, None if fraction >= 0.999 else p["volume"] * fraction))
            res = list(self.pool.map(lambda j: (j[0].name, j[0].backend.call("close", j[1], j[2])), jobs))
            if fraction >= 0.999:
                done = {a.id for a, _, _ in jobs}
                left = [fid for fid in g.legs if fid != "master" and fid not in done
                        and (lambda f: f is not None and (not self._fresh(f) or f.pos(g.legs[fid])))(self._acct(fid))]
                failed = [a.id for (a, _, _), (_, r) in zip(jobs, res) if not r.get("ok") and a.id != "master"]
                left = sorted(set(left) | set(failed))
                self.journal.lifecycle(gid, "CLOSING", source)
                if left:
                    g.state, g.pending_reason = "CLOSING", source
                    g.closing = dict(since=time.time(), left=left)
                    self.hub.note("warn", f"group {gid}: {len(left)} follower leg(s) will be closed on the next fresh read")
                else:
                    self.groups.pop(gid, None)
                    self._finalize(g, source)
            else:
                self.journal.partial(gid, fraction, source)
                mres = next((r for n, r in res if n == self.master.name), None)
                if mres and mres.get("ok"):
                    g.m_vol = max(0.0, g.m_vol - mres.get("closed", 0))
            self._save_groups()
        for n, r in res:
            if not r.get("ok"):
                self.hub.note("error", f"{n}: close failed: {r.get('error')}")
        self.hub.note("info", f"group {gid}: closed {fraction:.0%} on {len(res)} account(s)")
        self.refresh()
        return dict(ok=all(r.get("ok") for _, r in res), results=[dict(account=n, **r) for n, r in res])

    def modify_group(self, gid, sl, tp, source="superchart"):
        """sl/tp = master PRICES (0 = remove). Followers get the same distance from their own entry."""
        with self.lock:
            g = self._group(gid)
            mp = self.master.pos(g.legs.get("master"))
            if not mp:
                return dict(ok=False, error="master position not found")
            r = self.master.backend.call("modify", mp["ticket"], sl, tp)
            if not r.get("ok"):
                return dict(ok=False, error=f"master: {r.get('error')}")
            mp = dict(mp, sl=sl or 0.0, tp=tp or 0.0)
            self._mirror_sltp(g, mp, {f.id: f for f in self.followers})
            g.m_sl, g.m_tp = mp["sl"], mp["tp"]
            self._save_groups()
            self.journal.sl_changed(gid, mp["sl"], mp["tp"], source)
        self.hub.note("info", f"group {gid}: SL {sl or '-'} TP {tp or '-'} on all accounts")
        self.refresh()
        return dict(ok=True)

    def breakeven(self, gid):
        with self.lock:
            g = self._group(gid)
            out = []
            for acc_id, t in g.legs.items():
                a = self._acct(acc_id)
                p = a.pos(t) if a and a.connected else None
                if p:
                    if p["sl"] and (p["sl"] - p["open"]) * g.side >= 0:
                        out.append((a.name, dict(ok=True, note="SL already at or beyond break-even (not loosened)")))
                        continue                          # tighten-only: never move a protecting SL back
                    out.append((a.name, a.backend.call("modify", t, p["open"], p["tp"])))
                    if acc_id == "master":
                        g.m_sl = p["open"]
                        self.journal.sl_changed(gid, p["open"], p["tp"], "breakeven")
            self._save_groups()
        self.hub.note("info", f"group {gid}: SL moved to break-even")
        self.refresh()
        return dict(ok=all(r.get("ok") for _, r in out), results=[dict(account=n, **r) for n, r in out])

    def close_everything(self, source="superchart"):
        with self.lock:
            res = list(self.pool.map(lambda a: (a.name, a.backend.call("close_all", "superchart")),
                                     [a for a in self.all_accounts() if a.connected]))
            for g in list(self.groups.values()):
                self._finalize(g, source)
            self.groups.clear()
            self._save_groups()
        self.hub.note("info", "CLOSE ALL: every SuperChart trade closed on all accounts")
        self.refresh()
        return dict(ok=all(r.get("ok") for _, r in res), results=[dict(account=n, **r) for n, r in res])

    def unlock(self, acc_id):
        a = self._acct(acc_id)
        if not a:
            return dict(ok=False, error="account not found")
        a.locked, a.locked_day = "", None
        a.day_start = a.info.get("equity", a.day_start)       # restart the day's count from now
        self._save_risk()
        self.hub.note("info", f"{a.name}: unlocked manually (day P/L restarts from now)")
        return dict(ok=True)

    def set_armed(self, armed):
        self.armed = bool(armed)
        if self.armed:
            self.halted = ""                        # arming again = the explicit decision to resume
        self.hub.note("warn" if self.armed else "info",
                      f"TRADING {'ARMED' if self.armed else 'DISARMED'}" + (" (paper)" if self.paper else " (LIVE)"))
        return dict(ok=True, armed=self.armed)

    def update_config(self, new):
        with self.lock:
            cfg = {"master": clean_master(new.get("master") or self.cfg["master"]),
                   "followers": [clean_follower(f) for f in (new.get("followers") or [])],
                   "safety": {k: v for k, v in (new.get("safety") or self.cfg.get("safety") or {}).items()
                              if k in SAFETY_DEFAULTS}}
            old = self.cfg["master"]
            m = cfg["master"]
            master_moved = any(str(old.get(k) or "") != str(m.get(k) or "") for k in ("path", "login", "symbol"))
            if master_moved and self.src.live:
                if self.groups:
                    return dict(ok=False, error="close all SuperChart trades before changing the master MT5 "
                                                "(open trades are tied to the current master)")
                err = self.hub.reconnect_master(m["path"] or None, m["login"] or None, m["symbol"] or None)
                if err is not None and err.startswith("FAILED"):
                    return dict(ok=False, error=err)
                self.master.symbol = self.src.symbol
                self._master_warning = err
            self.cfg = cfg
            save_config(cfg)
            self.safety = dict(SAFETY_DEFAULTS, **cfg["safety"])
            self.master.cfg = cfg["master"]
            self._build_followers()
        self.hub.note("info", f"accounts saved: master + {len(cfg['followers'])} follower(s)")
        warn = getattr(self, "_master_warning", None)
        self._master_warning = None
        return dict(ok=True, warning=warn) if warn else dict(ok=True)

    def test_connections(self, lot=0.01):
        """Check every account without trading (see mt5_ops.connection_check)."""
        def one(a):
            if a.role == "follower" and not a.cfg.get("enabled", True):
                return dict(id=a.id, name=a.name, role=a.role, ok=False, skipped=True,
                            checks=[dict(name="Copy", ok=False, detail="follower is switched OFF")])
            if not a.connected:
                return dict(id=a.id, name=a.name, role=a.role, ok=False,
                            checks=[dict(name="Connection", ok=False, detail=a.error or a.status)])
            use_lot = lot if a.role == "master" else float(a.cfg.get("lot") or lot)
            r = a.backend.call("connection_check", a.symbol, use_lot)
            if not isinstance(r, dict) or "checks" not in r:
                r = dict(ok=False, checks=[dict(name="Check", ok=False, detail=(r or {}).get("error", "failed"))])
            expected = (a.cfg.get("login") or None)
            if expected and r.get("login") and int(r["login"]) != int(expected):
                r["checks"].append(dict(name="Login #", ok=False, detail=f"logged in as #{r['login']}, expected #{expected}"))
                r["ok"] = False
            return dict(r, id=a.id, name=a.name, role=a.role, symbol=a.symbol, lot=use_lot)
        res = list(self.pool.map(one, self.all_accounts()))
        good = sum(1 for x in res if x.get("ok"))
        self.hub.note("info", f"connection test: {good}/{len(res)} account(s) ready")
        return dict(ok=all(x.get("ok") or x.get("skipped") for x in res), results=res, paper=self.paper)

    def find_symbols(self, acc_id, base=None):
        """Symbols on that account's own MT5 that match the base (from the master symbol by default)."""
        a = self._acct(acc_id)
        if not a:
            return dict(ok=False, error="account not found (Save the accounts first)")
        if not a.connected:
            return dict(ok=False, error=f"{a.name} is not connected ({a.error or a.status})")
        base = base or mt5_ops.base_of(self.master.symbol or self.src.symbol)
        r = a.backend.call("find_symbols", base, 15)
        if isinstance(r, dict):
            r["account"] = a.name
            r["current"] = a.symbol
            syms = r.get("symbols") or []
            cur = next((x for x in syms if x["name"] == a.symbol and x.get("tradable")), None)
            if cur:                                       # the symbol already in use stays first
                syms.remove(cur)
                syms.insert(0, dict(cur, current=True))
                r["best"] = cur["name"]
        return r

    def reconnect(self, acc_id):
        a = self._acct(acc_id)
        if not a or a.role != "follower":
            return dict(ok=False, error="follower not found")
        if a.backend:
            a.backend.stop()
            a.backend = None
        threading.Thread(target=self._connect_follower, args=(a,), daemon=True).start()
        return dict(ok=True)

    # ------------------------------------------------------------------ view
    def state(self):
        can, why = self.can_trade()
        groups = []
        accts = {a.id: a for a in self.all_accounts()}
        for g in sorted(self.groups.values(), key=lambda x: x.created):
            legs, total = [], 0.0
            for acc_id, t in g.legs.items():
                a = accts.get(acc_id)
                p = a.pos(t) if a else None
                if p:
                    total += p["pnl"]
                legs.append(dict(account=a.name if a else acc_id, id=acc_id, ticket=t,
                                 volume=p["volume"] if p else None, open=p["open"] if p else None,
                                 sl=p["sl"] if p else None, tp=p["tp"] if p else None, pnl=p["pnl"] if p else None))
            mp = self.master.pos(g.legs.get("master"))
            groups.append(dict(gid=g.gid, side=g.side, created=g.created, volume=mp["volume"] if mp else g.m_vol,
                               open=mp["open"] if mp else g.m_open, sl=mp["sl"] if mp else g.m_sl,
                               tp=mp["tp"] if mp else g.m_tp, pnl=round(total, 2), legs=legs, state=g.state,
                               unprotected=[a_id for a_id in g.legs if g.sl_dist and not g.protected.get(a_id)
                                            and time.time() - g.created > 5],
                               closing=bool(g.closing), pm_mode=getattr(g, "pm_mode", "NONE")))
        mv = self.master.view()
        mv["terminal_path"] = getattr(self.src, "path", "")
        mv["login_error"] = getattr(self.src, "login_error", "")
        if not mv.get("login") and getattr(self.src, "live", False):
            mv["data_status"] = self.src.status
        return dict(paper=self.paper, armed=self.armed, can_trade=can, reason=why, halted=self.halted,
                    safety=self.safety, master=mv, followers=[f.view() for f in self.followers],
                    groups=groups, config=self.cfg)

    def stop(self):
        self.running = False
        for f in self.followers:
            if f.backend:
                f.backend.stop()
