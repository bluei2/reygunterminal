"""
POSITION MANAGEMENT CONTROLLER: the ONE place that moves stop losses of open SuperChart trades automatically.

Deterministic priority (highest first):
   1 EMERGENCY PROTECTION   TradeManager.protect(): a leg without SL gets one (retry) or is closed. The controller
                            does not touch a trade until every leg is PROTECTED.
   2 BROKER SAFETY          the new SL must respect the broker's stops level (distance from price) and freeze level
                            (no modification while price is inside the freeze distance of the current SL)
   3 BREAK-EVEN             at +be_r R (or +be_usd $ on the master): SL -> entry +/- be_offset
   4 PROFIT LOCK            steps [[at_r, lock_r], ...]: at +at_r R lock +lock_r R
   5 TRAILING               none | atr (price -/+ atr_mult x ATR) | step (every step_r R: SL one step behind) |
                            structure (last confirmed swing low / high on trail_tf -/+ buffer)
   (+ partial close [[at_r, fraction], ...], each step once)
The candidate SLs of every active rule are combined into ONE decision: the most protective one. The result is
applied only when it is TIGHTER than the current stop (tighten-only): no rule can ever loosen an SL that is
already protecting the trade. Every change is journaled (sl_changes with source position_manager:<rule>).

Default (Eugene, 2026-10-01, "dapat ON na agad"): ON with break-even at +1R (SL -> entry + $0.20) and a profit lock
at +1.25R -> +0.5R. Trailing and partial close stay off (0.01 lots cannot be split). Runs only while trading is ON
(armed) unless require_armed is false. Older saved files that still had the old OFF-and-empty defaults get these.
The same rules exist in the backtest (be_r, trail); this controller is the live counterpart.
"""
from __future__ import annotations

import json
import math
import os
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(HERE, "position_settings.json")
DEFAULTS = dict(enabled=True, require_armed=True, interval_s=2.0,
                be_r=1.0, be_usd=0.0, be_offset=0.20,
                profit_lock=[[1.25, 0.5]],         # [[at_r, lock_r], ...]
                trail_mode="none", trail_start_r=1.0, trail_atr_mult=3.0, trail_tf="M5", step_r=1.0,
                structure_buffer=0.30, swing_n=2,
                partial_close=[],                  # [[at_r, fraction 0..1], ...]
                min_step=0.05)                     # $: ignore SL moves smaller than this
TRAIL_MODES = ("none", "atr", "step", "structure")
PRIORITY = ["EMERGENCY", "BROKER_SAFETY", "BREAK_EVEN", "PROFIT_LOCK", "TRAILING"]
MODE_OF = {"BREAK_EVEN": "BE_ACTIVE", "BREAK_EVEN_USD": "BE_ACTIVE", "PROFIT_LOCK": "PROFIT_LOCK_ACTIVE",
           "TRAIL_ATR": "TRAIL_ACTIVE", "TRAIL_STEP": "TRAIL_ACTIVE", "TRAIL_STRUCTURE": "TRAIL_ACTIVE"}


def _pairs(v, lo2=0.0, hi2=100.0):
    out = []
    for x in v or []:
        try:
            a, b = float(x[0]), float(x[1])
        except (TypeError, ValueError, IndexError):
            continue
        if a > 0 and lo2 <= b <= hi2 and not (math.isnan(a) or math.isnan(b)):
            out.append([a, b])
    return sorted(out)


def clean(raw):
    s = dict(DEFAULTS)
    for k, d in DEFAULTS.items():
        if k not in (raw or {}):
            continue
        v = raw[k]
        try:
            if isinstance(d, bool):
                s[k] = bool(v)
            elif isinstance(d, float):
                x = float(v)
                if math.isnan(x) or x < 0:
                    continue
                s[k] = x
            elif isinstance(d, int):
                s[k] = max(1, int(v))
            elif isinstance(d, list):
                s[k] = _pairs(v, 0.0, 1.0 if k == "partial_close" else 100.0)
            else:
                s[k] = str(v)
        except (TypeError, ValueError):
            continue
    if s["trail_mode"] not in TRAIL_MODES:
        s["trail_mode"] = "none"
    s["interval_s"] = max(1.0, s["interval_s"])
    s["step_r"] = max(0.1, s["step_r"])
    return s


def more_protective(side, a, b):
    """The tighter of two stops for this side (None-safe)."""
    if a is None:
        return b
    if b is None:
        return a
    return max(a, b) if side == 1 else min(a, b)


def plan(side, entry, cur_sl, price, risk_dist, cfg, atr=None, swing=None, broker=None, lot=None,
         contract=100.0, done_partials=()):
    """Pure decision for one trade. -> dict(r, new_sl, rule, mode, candidates, partials, blocked)."""
    c = cfg
    out = dict(r=None, new_sl=None, rule=None, mode="NONE", candidates=[], partials=[], blocked=None)
    if not risk_dist or risk_dist <= 0 or price is None or entry is None:
        out["blocked"] = "no initial risk (SL distance) known"
        return out
    r = (price - entry) * side / risk_dist
    out["r"] = round(r, 3)
    cands = []
    if c["be_r"] > 0 and r >= c["be_r"]:
        cands.append(("BREAK_EVEN", entry + side * c["be_offset"]))
    if c["be_usd"] > 0 and lot and (price - entry) * side * lot * contract >= c["be_usd"]:
        cands.append(("BREAK_EVEN_USD", entry + side * c["be_offset"]))
    for at_r, lock_r in c["profit_lock"]:
        if r >= at_r:
            cands.append(("PROFIT_LOCK", entry + side * lock_r * risk_dist))
    if c["trail_mode"] != "none" and r >= c["trail_start_r"]:
        if c["trail_mode"] == "atr" and atr:
            cands.append(("TRAIL_ATR", price - side * c["trail_atr_mult"] * atr))
        elif c["trail_mode"] == "step":
            k = math.floor(r / c["step_r"] + 1e-9)
            if k >= 1:
                cands.append(("TRAIL_STEP", entry + side * (k - 1) * c["step_r"] * risk_dist))
        elif c["trail_mode"] == "structure" and swing is not None:
            cands.append(("TRAIL_STRUCTURE", swing - side * c["structure_buffer"]))
    out["candidates"] = [dict(rule=n, sl=round(v, 2)) for n, v in cands]
    for at_r, frac in c["partial_close"]:
        key = f"{at_r}:{frac}"
        if r >= at_r and key not in done_partials:
            out["partials"].append(dict(key=key, at_r=at_r, fraction=frac))
    if not cands:
        return out
    best_rule, best = cands[0]
    for n, v in cands[1:]:
        if more_protective(side, best, v) != best:
            best_rule, best = n, v
    # BROKER SAFETY: stops level (min distance from the current price) and freeze level
    b = broker or {}
    pt = b.get("point") or 0.01
    min_dist = (b.get("stops_level") or 0) * pt
    freeze = (b.get("freeze_level") or 0) * pt
    if (price - best) * side <= min_dist:
        best = price - side * (min_dist + pt)            # as close as the broker allows
        if (price - best) * side <= 0:
            out["blocked"] = "price too close to place a stop (stops level)"
            return out
    if cur_sl and freeze and abs(price - cur_sl) <= freeze:
        out["blocked"] = "inside the broker freeze level: SL cannot be modified now"
        return out
    best = round(best, 2)
    # TIGHTEN ONLY: never loosen an existing stop
    if cur_sl and (best - cur_sl) * side <= c["min_step"]:
        out["mode"] = MODE_OF.get(best_rule, "NONE")
        out["rule"] = best_rule
        return out
    out.update(new_sl=best, rule=best_rule, mode=MODE_OF.get(best_rule, "NONE"))
    return out


SETTINGS_VERSION = 2


def load_settings():
    try:
        with open(SETTINGS_FILE) as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return clean(None)
    if int(raw.get("version", 1) or 1) < SETTINGS_VERSION and not raw.get("enabled") \
            and not float(raw.get("be_r") or 0) and not float(raw.get("be_usd") or 0) and not raw.get("profit_lock"):
        # the old file only held the old OFF defaults -> the new protective defaults (choices you made are kept)
        for k in ("enabled", "be_r", "be_offset", "profit_lock"):
            raw.pop(k, None)
    return clean(raw)


class PositionController:
    def __init__(self, tm):
        self.tm = tm
        self.hub = tm.hub
        self.settings = load_settings()
        self.log = []
        self.status = {}                  # gid -> last plan (for the UI)
        self.running = True
        threading.Thread(target=self._loop, daemon=True).start()

    def update_settings(self, new):
        self.settings = clean(dict(self.settings, **(new or {})))
        try:
            with open(SETTINGS_FILE, "w") as f:
                json.dump(dict(self.settings, version=SETTINGS_VERSION), f, indent=2)
        except OSError as e:
            self.hub.note("error", f"position manager settings not saved: {e}", category="CONFIG_ERROR")
        self._note(f"settings saved ({'ON' if self.settings['enabled'] else 'OFF'})")
        return dict(ok=True, settings=self.settings)

    def _note(self, text, kind="info"):
        self.log.insert(0, dict(t=time.time(), text=text, kind=kind))
        del self.log[60:]
        if kind != "debug":
            self.hub.note(kind, f"POSITION MANAGER: {text}")

    def _atr_swing(self, side):
        tf = self.settings["trail_tf"]
        try:
            bars = self.hub.src.bars(tf)[:-1]
        except Exception as e:
            self.hub.note("error", f"position manager: bars failed: {e}", category="DATA_ERROR")
            return None, None
        if len(bars) < 20:
            return None, None
        import smc_engine
        atr = smc_engine.atr_series(bars[-100:], 14)[-1]
        swing = None
        if self.settings["trail_mode"] == "structure":
            st = smc_engine.structure(bars[-200:], tf, self.settings["swing_n"])
            kind = "low" if side == 1 else "high"
            sw = [s for s in st["swings"] if s["kind"] == kind]
            swing = sw[-1]["price"] if sw else None
        return atr, swing

    def step(self):
        s, tm = self.settings, self.tm
        if not s["enabled"]:
            return
        if s["require_armed"] and not tm.armed:
            return
        tk = self.hub.last_tick
        if tk is None:
            return
        with tm.lock:
            groups = list(tm.groups.values())
        for g in groups:
            if g.closing or g.state in ("PROTECTION_PENDING", "CLOSING") or (g.sl_dist and g.state != "PROTECTED"
                                                                             and not all(g.protected.get(x) for x in g.legs)):
                continue                           # EMERGENCY PROTECTION first (protect()), not us
            if not tm._fresh(tm.master):
                continue
            mp = tm.master.pos(g.legs.get("master"))
            if not mp:
                continue
            price = tk.bid if g.side == 1 else tk.ask
            risk_dist = g.sl_dist or (abs(mp["open"] - mp["sl"]) if mp["sl"] else 0)
            atr, swing = self._atr_swing(g.side) if s["trail_mode"] in ("atr", "structure") else (None, None)
            broker = getattr(tm.master, "broker", None) or {}
            done = set(getattr(g, "pm_done", []) or [])
            p = plan(g.side, mp["open"], mp["sl"] or None, price, risk_dist, s, atr, swing, broker, mp["volume"],
                     broker.get("contract_size") or 100.0, done)
            self.status[g.gid] = dict(p, t=time.time(), sl=mp["sl"], entry=mp["open"])
            g.pm_mode = p["mode"]
            if p["new_sl"] is not None:
                r = tm.modify_group(g.gid, p["new_sl"], mp["tp"], source=f"position_manager:{p['rule']}")
                if r.get("ok"):
                    self._note(f"{g.gid}: SL {mp['sl'] or '-'} → {p['new_sl']} ({p['rule']}, {p['r']:+.2f}R)")
                else:
                    self._note(f"{g.gid}: SL move failed: {r.get('error')}", "error")
            for part in p["partials"]:
                r = tm.close_group(g.gid, part["fraction"], source=f"position_manager:partial {part['at_r']}R")
                done.add(part["key"])
                g.pm_done = sorted(done)
                self._note(f"{g.gid}: partial close {part['fraction']:.0%} at {part['at_r']}R"
                           + ("" if r.get("ok") else f" FAILED: {r.get('error')}"), "info" if r.get("ok") else "error")

    def _loop(self):
        time.sleep(5)
        while self.running:
            try:
                self.step()
            except Exception as e:
                self.hub.note("error", f"position manager: {e.__class__.__name__}: {e}", category="EXECUTION_ERROR")
            time.sleep(self.settings["interval_s"])

    def state(self):
        return dict(settings=self.settings, status=self.status, log=self.log[:25], priority=PRIORITY,
                    trail_modes=TRAIL_MODES)

    def stop(self):
        self.running = False
