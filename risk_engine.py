"""
Risk engine: one place that answers "may we trade now, and on which accounts?"

  accounts()        per account (master + every follower), each with ITS OWN broker day:
                    start equity, equity, daily P/L, DD / TP limits, locked + reason, open trades, open risk $ (at SL),
                    exposure (lots), trades today, status ACTIVE / LOCKED / DISCONNECTED / DISABLED / STALE
  hard_blockers()   every condition that must stop a new trade, as explicit codes:
                    ARM_OFF, AUTO_TRADE_OFF (auto only), HALTED, MASTER_DISCONNECTED, ALGO_TRADING_OFF,
                    DAILY_DD_LOCK, DAILY_TP_LOCK, OPPOSITE_POSITION, MAX_OPEN_REACHED, MAX_TRADES_REACHED,
                    SPREAD_TOO_HIGH, NEWS_BLOCK (auto), MARKET_CLOSED, STALE_TICK, STALE_ANALYSER, DRIFT_PAUSE (auto)
                    soft (shown, does not block the master): FOLLOWER_UNAVAILABLE
                    added (master improvement spec): MASTER_DISABLED (master trading switch), CONFIG_INVALID,
                    DATA_INVALID (data_validation), NEWS_UNKNOWN (auto, policy), RISK_PER_TRADE, MAX_TOTAL_DD,
                    EXPOSURE_LIMIT, INVALID_SL, INVALID_TP, TP_REQUIRED, MIN_RR, MAX_OPEN_REACHED / MAX_TRADES_REACHED
                    for every order kind (risk settings, 0 = off)
It never sends or changes an order. TradeManager.check_request() and AutoTrader call it before trading.

Fail safe: an unreadable master switch file or an invalid risk settings file BLOCKS trading (MASTER_DISABLED /
CONFIG_INVALID) until it is fixed or saved again from the UI. A missing file = defaults.
"""
from __future__ import annotations

import json
import math
import os
import threading
import time

from market_hours import gold_open

HARD = "hard"
SOFT = "soft"
STALE_TICK_S = 30.0
STALE_ANALYSER_S = 15.0
HERE = os.path.dirname(os.path.abspath(__file__))
MASTER_FILE = os.path.join(HERE, "master_switch.json")
RISK_FILE = os.path.join(HERE, "risk_settings.json")
PEAK_FILE = os.path.join(HERE, "risk_peak.json")
NEWS_POLICIES = ("block", "allow", "reduce_risk")
RISK_DEFAULTS = dict(
    max_risk_per_trade_pct=2.0,     # % of master equity lost at the SL (0 = off)
    max_risk_per_trade_usd=0.0,     # $ lost at the SL (0 = off)
    max_total_dd_pct=0.0,           # from the highest master equity seen (0 = off)
    max_exposure_lots=0.0,          # open master lots + the new lot (0 = off)
    max_trades_per_day=0,           # every kind (manual + auto), master's broker day (0 = off)
    max_open_positions=0,           # every kind (0 = off; AUTO TRADE has its own max_open too)
    require_tp=False,
    min_rr=0.0,                     # TP distance / SL distance (0 = off)
    news_unknown="block",           # AUTO when the news calendar is unavailable: block | allow | reduce_risk
    news_block_manual=False,        # also block MANUAL entries inside the news window
)


def clean_risk(raw):
    """-> (settings, error). Unknown keys ignored; a bad value is an error (fail safe), not silently replaced."""
    s = dict(RISK_DEFAULTS)
    errors = []
    for k, v in (raw or {}).items():
        if k not in RISK_DEFAULTS:
            continue
        d = RISK_DEFAULTS[k]
        try:
            if isinstance(d, bool):
                if not isinstance(v, bool):
                    raise ValueError("not true/false")
                s[k] = v
            elif isinstance(d, str):
                if v not in NEWS_POLICIES:
                    raise ValueError(f"one of {NEWS_POLICIES}")
                s[k] = v
            else:
                x = type(d)(v)
                if isinstance(x, float) and (math.isnan(x) or math.isinf(x)):
                    raise ValueError("not a number")
                if x < 0:
                    raise ValueError("negative")
                s[k] = x
        except (TypeError, ValueError) as e:
            errors.append(f"{k}={v!r}: {e}")
    return s, "; ".join(errors)


def blocker(code, text, level=HARD, **kw):
    return dict(code=code, text=text, level=level, **kw)


class RiskEngine:
    def __init__(self, hub):
        self.hub = hub
        self.lock = threading.Lock()
        self.master_enabled, self.master_error = self._load_master()
        self.settings, self.config_error = self._load_risk()
        self.peak = self._load_peak()

    # ------------------------------------------------------------------ master switch + settings (fail safe)
    def _load_master(self):
        if not os.path.exists(MASTER_FILE):
            return True, ""
        try:
            with open(MASTER_FILE) as f:
                v = json.load(f).get("enabled")
            if not isinstance(v, bool):
                return False, "master_switch.json: 'enabled' is not true/false"
            return v, ""
        except (OSError, ValueError, AttributeError) as e:
            return False, f"master_switch.json unreadable ({e.__class__.__name__})"

    def set_master(self, enabled, reason=""):
        """MASTER TRADING ENABLE: when OFF nothing trades (manual, auto, news box). Persisted."""
        with self.lock:
            self.master_enabled, self.master_error = bool(enabled), ""
            try:
                with open(MASTER_FILE, "w") as f:
                    json.dump(dict(enabled=bool(enabled), t=time.time(), reason=reason), f)
            except OSError as e:
                self.hub.note("error", f"risk settings: master switch not saved: {e}", category="CONFIG_ERROR")
        self.hub.note("warn" if not enabled else "info", f"MASTER TRADING {'ENABLED' if enabled else 'DISABLED'}"
                      + (f" ({reason})" if reason else ""))
        if not enabled:
            at = getattr(self.hub, "autotrader", None)
            if at is not None and at.enabled:
                at.enabled = False
                at._note("warn", "AUTO TRADE OFF: master trading disabled")
        return dict(ok=True, enabled=self.master_enabled)

    def _load_risk(self):
        if not os.path.exists(RISK_FILE):
            return dict(RISK_DEFAULTS), ""
        try:
            with open(RISK_FILE) as f:
                raw = json.load(f)
            if not isinstance(raw, dict):
                raise ValueError("not an object")
        except (OSError, ValueError) as e:
            return dict(RISK_DEFAULTS), f"risk_settings.json unreadable ({e.__class__.__name__}: {e})"
        return clean_risk(raw)

    def update_settings(self, new):
        merged = dict(self.settings)
        merged.update(new or {})
        s, err = clean_risk(merged)
        if err:
            return dict(ok=False, error=err, settings=self.settings)
        with self.lock:
            self.settings, self.config_error = s, ""
            try:
                with open(RISK_FILE, "w") as f:
                    json.dump(s, f, indent=2)
            except OSError as e:
                self.hub.note("error", f"risk settings not saved: {e}", category="CONFIG_ERROR")
        return dict(ok=True, settings=s)

    def _load_peak(self):
        try:
            with open(PEAK_FILE) as f:
                return {str(k): float(v) for k, v in json.load(f).items()}
        except (OSError, ValueError, AttributeError, TypeError):
            return {}

    def _track_peak(self):
        """Highest master equity seen (per login) for the total drawdown limit."""
        tm = self.tm
        if tm is None or not tm.master.connected:
            return None, None
        eq = tm.master.info.get("equity")
        if not eq:
            return None, None
        key = str(tm.master.info.get("login") or tm.master.id) + ("-paper" if tm.paper else "")
        pk = self.peak.get(key)
        if pk is None or eq > pk:
            self.peak[key] = pk = float(eq)
            try:
                with open(PEAK_FILE, "w") as f:
                    json.dump(self.peak, f)
            except OSError:
                pass
        return float(eq), pk

    def reset_peak(self):
        self.peak = {}
        try:
            os.remove(PEAK_FILE)
        except OSError:
            pass
        return dict(ok=True)

    def contract_size(self):
        tm = self.tm
        cs = ((getattr(tm.master, "broker", None) or {}).get("contract_size") if tm else None)
        return (float(cs), True) if cs else (100.0, False)          # 100 oz only when the broker gave nothing

    def news_state(self):
        nf = getattr(self.hub, "news", None)
        if nf is None:
            return dict(state="NEWS_UNKNOWN", text="news reader is off (--no-news)", warning=None)
        try:
            return nf.news_state()
        except Exception as e:
            return dict(state="NEWS_UNKNOWN", text=f"news state failed: {e.__class__.__name__}", warning=None)

    def lot_multiplier(self, kind="auto"):
        """reduce_risk policy for an unknown news state: auto lot x0.5 (never below the broker minimum)."""
        s = self._auto_settings()
        if kind == "auto" and s.get("news_block") and self.settings["news_unknown"] == "reduce_risk" \
                and self.news_state()["state"] == "NEWS_UNKNOWN":
            return 0.5
        return 1.0

    # ------------------------------------------------------------------ helpers
    @property
    def tm(self):
        return self.hub.trader

    def _auto_settings(self):
        at = getattr(self.hub, "autotrader", None)
        return at.settings if at is not None else {}

    def trades_today(self, acc_name=None):
        """Trades opened today (the master's broker day), from the journal (failed attempts not counted)."""
        tm = self.tm
        if tm is None:
            return 0
        day = tm._day_key()
        off = (self.hub.utc_offset or 0) * 3600
        n = 0
        for e in tm.journal.list(2000):
            if e.get("status") == "failed":
                continue
            t_srv = e.get("t_srv_open") or (e.get("open_time", 0) + off)
            if int(t_srv // 86400) == day and (acc_name is None or acc_name in (e.get("accounts") or [])):
                n += 1
        return n

    # ------------------------------------------------------------------ per account
    def accounts(self):
        tm = self.tm
        if tm is None:
            return []
        out = []
        for a in tm.all_accounts():
            v = a.view()
            dd_amt, tp_amt = a.limits()
            open_risk = exposure = 0.0
            for p in a.positions:
                exposure += p.get("volume", 0)
                if p.get("sl"):
                    open_risk += abs(p["open"] - p["sl"]) * p["volume"] * self.contract_size()[0]
            status = ("DISABLED" if a.role == "follower" and not a.cfg.get("enabled", True) else
                      "DISCONNECTED" if not a.connected else "LOCKED" if a.locked else
                      "STALE" if not getattr(a, "fresh", False) else "ACTIVE")
            out.append(dict(id=a.id, name=a.name, role=a.role, status=status, login=v.get("login"),
                            day=tm._acct_day(a) if a.connected else None,
                            start_equity=a.day_start, equity=v.get("equity"), daily_pl=v.get("day_pl"),
                            dd_limit=dd_amt or None, tp_limit=tp_amt or None, locked=bool(a.locked),
                            lock_reason=a.locked, open_trades=len(a.positions), open_risk=round(open_risk, 2),
                            exposure=round(exposure, 2), trades_today=self.trades_today(a.name),
                            close_scope=a.cfg.get("close_scope", "superchart")))
        return out

    # ------------------------------------------------------------------ blockers
    def hard_blockers(self, side=0, kind="manual", lot=None, sl_dist=None, tp_dist=None, risk_pct_cap=None):
        """All blockers for a new trade in `side` (0 = either). kind: manual | auto | preview.
        risk_pct_cap: a strategy's own max risk % per trade instead of the risk settings (v2.8.12: CONSO 5 %)."""
        hub, tm = self.hub, self.tm
        out = []
        auto = kind == "auto"
        s = self._auto_settings()
        if tm is None:
            return [blocker("READ_ONLY", "trading is not available (read-only mode)")]
        if not self.master_enabled:
            out.append(blocker("MASTER_DISABLED", self.master_error or "MASTER TRADING is disabled"))
        if self.config_error:
            out.append(blocker("CONFIG_INVALID", self.config_error))
        rs = self.settings
        if not tm.armed:
            out.append(blocker("ARM_OFF", "trading is not armed"))
        if tm.halted:
            out.append(blocker("HALTED", f"trading halted: {tm.halted}"))
        at = getattr(hub, "autotrader", None)
        if auto and at is not None and not at.enabled:
            out.append(blocker("AUTO_TRADE_OFF", "AUTO TRADE is OFF"))
        if not tm.master.connected:
            out.append(blocker("MASTER_DISCONNECTED", "master account not connected"))
        elif getattr(tm.src, "login_error", ""):
            out.append(blocker("MASTER_DISCONNECTED", tm.src.login_error))
        elif not tm.paper and not tm.master.info.get("trade_allowed", False):
            out.append(blocker("ALGO_TRADING_OFF", "Algo Trading is OFF in the master MT5"))
        if tm.master.locked:
            code = "DAILY_TP_LOCK" if "TP" in tm.master.locked else "DAILY_DD_LOCK"
            out.append(blocker(code, f"master locked: {tm.master.locked}"))
        for f in tm.followers:
            if f.cfg.get("enabled", True) and (not f.connected or f.locked):
                out.append(blocker("FOLLOWER_UNAVAILABLE", f"{f.name}: {f.locked or f.status}", SOFT))
        if side and (s.get("no_opposite", True) if at is not None else True):
            opp = [g for g in tm.groups.values() if g.side == -side]
            if opp:
                out.append(blocker("OPPOSITE_POSITION", f"{len(opp)} open {'SELL' if side == 1 else 'BUY'} trade(s)"))
        if auto:
            if s.get("max_open") and len(tm.groups) >= s["max_open"]:
                out.append(blocker("MAX_OPEN_REACHED", f"{len(tm.groups)} open (max {s['max_open']})"))
            if s.get("max_per_day") and at is not None and at.count_today >= s["max_per_day"]:
                out.append(blocker("MAX_TRADES_REACHED", f"{at.count_today} auto trades today (max {s['max_per_day']})"))
        if kind != "preview":
            if rs["max_open_positions"] and len(tm.groups) >= rs["max_open_positions"]:
                out.append(blocker("MAX_OPEN_REACHED", f"{len(tm.groups)} open (risk max {rs['max_open_positions']})"))
            if rs["max_trades_per_day"]:
                n_today = self.trades_today()
                if n_today >= rs["max_trades_per_day"]:
                    out.append(blocker("MAX_TRADES_REACHED", f"{n_today} trades today (risk max {rs['max_trades_per_day']})"))
        # ---- order values (SL / TP / RR / risk / exposure)
        bad = lambda x: x is None or not isinstance(x, (int, float)) or math.isnan(x) or math.isinf(x)
        if sl_dist is not None and (bad(sl_dist) or sl_dist < 0):
            out.append(blocker("INVALID_SL", f"invalid SL distance {sl_dist!r}"))
        if tp_dist is not None and (bad(tp_dist) or tp_dist < 0):
            out.append(blocker("INVALID_TP", f"invalid TP distance {tp_dist!r}"))
        if tp_dist is not None and not bad(tp_dist) and rs["require_tp"] and tp_dist == 0 and kind != "preview":
            out.append(blocker("TP_REQUIRED", "a take profit is required (risk settings)"))
        if rs["min_rr"] and sl_dist and tp_dist and not bad(sl_dist) and not bad(tp_dist) and sl_dist > 0 \
                and tp_dist / sl_dist < rs["min_rr"]:
            out.append(blocker("MIN_RR", f"RR 1:{tp_dist / sl_dist:.2f} below the minimum 1:{rs['min_rr']:.2f}"))
        if lot is not None and sl_dist and not bad(lot) and not bad(sl_dist) and sl_dist > 0 and lot > 0:
            cs, known = self.contract_size()
            risk_usd = sl_dist * lot * cs
            eq = tm.master.info.get("equity") or 0
            if rs["max_risk_per_trade_usd"] and risk_usd > rs["max_risk_per_trade_usd"] and risk_pct_cap is None:
                out.append(blocker("RISK_PER_TRADE", f"risk ${risk_usd:.2f} > max ${rs['max_risk_per_trade_usd']:.2f}"))
            cap = rs["max_risk_per_trade_pct"] if risk_pct_cap is None else float(risk_pct_cap)
            if cap and eq > 0 and 100 * risk_usd / eq > cap:
                out.append(blocker("RISK_PER_TRADE", f"risk {100 * risk_usd / eq:.2f}% of equity > max "
                                                     f"{cap:.2f}%" + (" (strategy limit)" if risk_pct_cap is not None else "")))
        if lot is not None and rs["max_exposure_lots"] and not bad(lot):
            open_lots = sum(p.get("volume", 0) for p in tm.master.positions)
            if open_lots + lot > rs["max_exposure_lots"] + 1e-9:
                out.append(blocker("EXPOSURE_LIMIT", f"{open_lots:.2f} open + {lot:.2f} > max {rs['max_exposure_lots']:.2f} lots"))
        if rs["max_total_dd_pct"]:
            eq, pk = self._track_peak()
            if eq and pk and 100 * (pk - eq) / pk >= rs["max_total_dd_pct"]:
                out.append(blocker("MAX_TOTAL_DD", f"equity {eq:.2f} is {100 * (pk - eq) / pk:.1f}% below the peak "
                                                   f"{pk:.2f} (max {rs['max_total_dd_pct']:.1f}%)"))
        tk = hub.last_tick
        if tk is not None and s.get("max_spread") and tk.ask - tk.bid > s["max_spread"]:
            out.append(blocker("SPREAD_TOO_HIGH", f"spread ${tk.ask - tk.bid:.2f} > ${s['max_spread']:.2f}",
                               current=round(tk.ask - tk.bid, 2), maximum=s["max_spread"]))
        if (auto and s.get("news_block")) or (kind == "manual" and rs["news_block_manual"]):
            ns = self.news_state()
            if ns["state"] == "NEWS_HIGH_IMPACT":
                out.append(blocker("NEWS_BLOCK", f"news window: {ns['text']}"))
            elif ns["state"] == "NEWS_UNKNOWN":
                pol = rs["news_unknown"] if auto else "allow"
                if pol == "block":
                    out.append(blocker("NEWS_UNKNOWN", f"news state unknown ({ns['text']}): not assumed clear"))
                else:
                    out.append(blocker("NEWS_UNKNOWN", f"news state unknown ({ns['text']})"
                                       + (": lot x0.5" if pol == "reduce_risk" else ""), SOFT))
        ds = getattr(hub, "data_status", None)
        if ds and not ds.get("ok") and time.time() - ds.get("t", 0) < 30:
            real = [i for i in ds.get("issues", []) if i["code"] not in ("NO_TICK",)]
            if real and not getattr(hub.src, "paused", False):
                out.append(blocker("DATA_INVALID", "; ".join(f"{i['code']} {i['text']}" for i in real[:3])))
        live = bool(getattr(hub.src, "live", False))
        if tk is None:
            out.append(blocker("STALE_TICK", "no price yet"))
        else:
            if live:
                if not gold_open():
                    out.append(blocker("MARKET_CLOSED", "gold market closed (weekend / daily break)"))
            age = time.time() - (getattr(hub, "last_tick_wall", 0) or 0)
            if getattr(hub, "last_tick_wall", 0) and age > STALE_TICK_S and not getattr(hub.src, "paused", False):
                out.append(blocker("STALE_TICK", f"last price {age:.0f}s ago"))
        an_age = time.time() - (getattr(hub, "_last_analysis", 0) or 0)
        if getattr(hub, "_last_analysis", 0) and an_age > STALE_ANALYSER_S:
            out.append(blocker("STALE_ANALYSER", f"analysis {an_age:.0f}s old"))
        dr = getattr(hub, "drift", None)
        if auto and dr is not None and dr.base and dr.result.get("status") == "bad" and dr.settings.get("auto_pause"):
            out.append(blocker("DRIFT_PAUSE", "live results worse than the backtest (drift monitor)"))
        return out

    def hard_only(self, blockers):
        return [b for b in blockers if b["level"] == HARD]

    def state(self):
        tm = self.tm
        eq, pk = self._track_peak() if tm else (None, None)
        return dict(accounts=self.accounts(), blockers=self.hard_blockers(0, "preview"),
                    armed=bool(tm and tm.armed), halted=tm.halted if tm else "",
                    safety=tm.safety if tm else {}, master_enabled=self.master_enabled,
                    master_error=self.master_error, config_error=self.config_error, settings=self.settings,
                    news=self.news_state(), peak_equity=pk, equity=eq,
                    total_dd_pct=round(100 * (pk - eq) / pk, 2) if eq and pk else None,
                    contract_size=self.contract_size()[0], contract_size_from_broker=self.contract_size()[1])
