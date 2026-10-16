"""
SHADOW MODE + TEST BAGO I-APPLY (v2.8.54, Eugene: "patunay muna bago live").

Two "arms" are simulated on the SAME bars with the SAME simulator:
  LIVE       your current settings (strategies + trailing / BE)
  CANDIDATE  the same + the presets you tick (e.g. BE 1R + structure trailing, CVD filter, range guard, TP 1:3)
Nothing here ever sends an order. The comparison is fair because both arms share every simplification:
  * entries: the strategies' own signals on the AUTO timeframe (TREND / PULLBACK at the signal bar's close, CONSO
    BUY STOP / SELL STOP filled on the first touch); one trade per strategy at a time
  * not simulated (same for both arms): the analyser HTF-bias gate, sessions, news window, max open, reversal close
  * exits: SL first then TP inside each M1 bar (conservative), BE / trailing of the position manager after each M1
    close, the CONSO failed-breakout exit, the strategy's max SL; a spread cost per trade
  * $ = result at 0.01 lot (1 $ per 1 $ move of gold)

SHADOW (forward): runs on the live chart bars from the moment you switch it on, every new AUTO-TF bar; closed
virtual trades are kept in shadow_trades.json, so the comparison survives a restart.
TEST (history): the same two arms on N days of MT5 history (demo: simulated data) - the "test before applying".
"""
from __future__ import annotations

import bisect
import json
import os
import threading
import time

import position_manager as PM
import strategies as ST
from engine import TF_SECONDS

HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(HERE, "shadow_settings.json")
TRADES_FILE = os.path.join(HERE, "shadow_trades.json")

PRESETS = {
    "be1r_structure": dict(title="BE 1R + structure trailing", protect=dict(be_r=1.0, trail_mode="structure",
                                                                          trail_start_r=1.0)),
    # v2.8.58 (Eugene, Oct 15 3:45 AM BUY ~4100 -> BE at +0.5R -> wick to 4100 -> +$45 without us): BE only at +1R,
    # then the stop trails under the last M15 swing (not M5), and no early +1.25R profit lock
    "be1r_m15": dict(title="BE 1R + M15 structure (walang maagang lock)",
                     protect=dict(be_r=1.0, trail_mode="structure", trail_start_r=1.0, trail_tf="M15", profit_lock=[])),
    "rr3": dict(title="TP 1:3 (imbes na 1:5)", strat=dict(trend=dict(rr=3.0), pullback=dict(rr=3.0), conso=dict(rr=3.0))),
    "cvd_filter": dict(title="CVD filter (TREND + PULLBACK)", cond=dict(trend=["cvd"], pullback=["cvd_turn"])),
    "range_guard": dict(title="Range guard (TREND)", cond=dict(trend=["no_range"])),
    "conso_box15": dict(title="CONSO box max $15", strat=dict(conso=dict(max_range=15.0))),
    "no_conso": dict(title="Walang CONSO", strat=dict(conso=dict(auto=False))),
}
NAMES = ("trend", "pullback", "conso")


# ============================================================================ arms
def candidate(scfg, protect, presets):
    """(strategy settings, protect settings) of the candidate arm = live + the ticked presets."""
    sc = json.loads(json.dumps(scfg))
    pr = dict(protect)
    for p in presets or []:
        d = PRESETS.get(p)
        if not d:
            continue
        pr.update(d.get("protect", {}))
        for n, over in d.get("strat", {}).items():
            sc[n].update(over)
        for n, keys in d.get("cond", {}).items():
            for k in keys:
                sc[n]["conditions"][k] = True
                if k not in sc[n]["required"]:
                    sc[n]["required"].append(k)
    return sc, PM.clean(pr)


# ============================================================================ simulator
def _stats(trades):
    done = [t for t in trades if t["reason"] != "OPEN"]
    n = len(done)
    wins = sum(1 for t in done if t["res"] == "WIN")
    losses = sum(1 for t in done if t["res"] == "LOSS")
    eq = peak = dd = 0.0
    for t in sorted(done, key=lambda x: x["t_exit"]):
        eq += t["usd"]
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    per = {}
    for t in done:
        p = per.setdefault(t["strategy"].upper(), dict(trades=0, usd=0.0, wins=0, losses=0))
        p["trades"] += 1
        p["usd"] = round(p["usd"] + t["usd"], 2)
        p["wins"] += t["res"] == "WIN"
        p["losses"] += t["res"] == "LOSS"
    return dict(trades=n, wins=wins, losses=losses, be=n - wins - losses,
                win_rate=round(100 * wins / n, 1) if n else None,
                usd=round(sum(t["usd"] for t in done), 2), total_r=round(sum(t["r"] for t in done), 2),
                max_dd_usd=round(dd, 2), by_strategy=per, open=sum(1 for t in trades if t["reason"] == "OPEN"))


class _Ctx:
    """Shared, cached views of the data for one run (both arms use the same one)."""

    def __init__(self, data, auto_tf):
        self.auto_tf = auto_tf
        self.sec = TF_SECONDS[auto_tf]
        self.bars = data[auto_tf]
        self.mtf = {k: data.get(k) for k in ("M5", "M15", "H1", "H4") if data.get(k)}
        self.S = ST.series(self.bars, self.mtf)
        self.times = [b.t for b in self.bars]
        self.ex = data.get("M1") or data.get("M5") or self.bars
        self.ex_sec = TF_SECONDS["M1"] if data.get("M1") else TF_SECONDS["M5"] if data.get("M5") else self.sec
        self.ex_t = [b.t for b in self.ex]
        self.m5 = data.get("M5") or self.bars
        self.m5_t = [b.t for b in self.m5]
        self.m5_sec = TF_SECONDS["M5"] if data.get("M5") else self.sec
        self.m15 = data.get("M15") or self.bars
        self.m15_t = [b.t for b in self.m15]
        self.m15_sec = TF_SECONDS["M15"] if data.get("M15") else self.sec

    def atr_at(self, t):
        k = bisect.bisect_right(self.times, t - self.sec) - 1          # the last auto-TF bar closed by t
        return (self.S["atr"][k] if 0 <= k < len(self.bars) else None) or None

    def swing_at(self, t, side, tf="M5"):
        """The last swing low (BUY) / high (SELL) of the trailing TF closed by t (M5, or M15 like the live trail_tf)."""
        bars, ts, sec = (self.m15, self.m15_t, self.m15_sec) if tf == "M15" else (self.m5, self.m5_t, self.m5_sec)
        k = bisect.bisect_right(ts, t - sec) - 1
        if k < 10:
            return None
        return ST.last_swing(bars, k, side)


def _manage(cx, side, entry, sl, tp, t0, protect, box=None, fail_atr=0.2, t_end=None):
    """Walk the exec bars from t0. -> (exit, t_exit, reason)."""
    risk = abs(entry - sl)
    k = bisect.bisect_left(cx.ex_t, t0)
    structure = protect.get("trail_mode") == "structure"
    for b in cx.ex[k:]:
        if t_end is not None and b.t >= t_end:
            break
        if side == 1:
            if b.l <= sl:
                return sl, b.t, "SL"
            if b.h >= tp:
                return tp, b.t, "TP"
        else:
            if b.h >= sl:
                return sl, b.t, "SL"
            if b.l <= tp:
                return tp, b.t, "TP"
        t_close = b.t + cx.ex_sec
        if box is not None and t_close % cx.sec == 0:                  # an auto-TF candle just closed
            a = cx.atr_at(t_close) or 0.0
            back = b.c < box[1] - fail_atr * a if side == 1 else b.c > box[0] + fail_atr * a
            if back:
                return b.c, b.t, "FAIL"
        if protect.get("enabled", True):
            pl = PM.plan(side, entry, sl, b.c, risk, protect, atr=cx.atr_at(t_close),
                         swing=cx.swing_at(t_close, side, protect.get("trail_tf", "M5")) if structure else None)
            if pl.get("new_sl") is not None and (pl["new_sl"] - sl) * side > 0:
                sl = pl["new_sl"]
    last = cx.ex[-1] if cx.ex else None
    return (last.c if last else entry), (last.t if last else t0), "OPEN"


def simulate(cx, scfg, protect, t_from=0, t_to=None, spread=0.3, fail_exit=True, fail_atr=0.2):
    """All virtual trades of one arm between t_from and t_to (signal times)."""
    out = []
    t_to = t_to or 10 ** 12
    for name in NAMES:
        cfg = scfg[name]
        if not cfg.get("auto", True):
            continue
        sigs = ST.signals(name, cx.bars, cfg, None, lookback=len(cx.bars), S=cx.S)
        busy_until = 0
        for g in sigs:
            t_sig = g["t"] + cx.sec                                    # the signal bar's close
            if not (t_from <= t_sig < t_to) or t_sig < busy_until:
                continue
            if name == "conso":
                p = g["plan"]
                k = bisect.bisect_left(cx.ex_t, t_sig)
                fill = None
                nxt = next((x["t"] + cx.sec for x in sigs if x["t"] + cx.sec > t_sig), None)
                for b in cx.ex[k:]:
                    up, dn = b.h >= p["buy_stop"], b.l <= p["sell_stop"]
                    if up and dn:
                        break                                          # both in one bar: ambiguous, skipped
                    if up or dn:
                        side = 1 if up else -1
                        stop = p["buy_stop"] if up else p["sell_stop"]
                        fill = (side, max(b.o, stop) if up else min(b.o, stop), b.t)
                        break
                    if nxt is not None and b.t >= nxt:
                        break                                          # a new box replaced this grid
                if fill is None:
                    continue
                side, entry, t_in = fill
                sl, tp = entry - side * p["sl_dist"], entry + side * p["tp_dist"]
                box = (p["box_low"], p["box_high"]) if fail_exit else None
            else:
                side, entry, t_in, box = g["side"], g["entry"], t_sig, None
                sl, tp = g["sl"], g["tp"]
                mx = float(cfg.get("max_sl", 20.0) or 0)
                if mx > 0 and abs(entry - sl) > mx:                    # AUTO TRADE's $ cap, same RR
                    rr = abs(tp - entry) / abs(entry - sl)
                    sl, tp = entry - side * mx, entry + side * mx * rr
            ex, t_ex, why = _manage(cx, side, entry, sl, tp, t_in, protect, box, fail_atr)
            risk = abs(entry - sl) or 1.0
            usd = round((ex - entry) * side - spread, 2)
            r = round((ex - entry) * side / risk, 2)
            res = "OPEN" if why == "OPEN" else "WIN" if r >= 0.15 else "LOSS" if r <= -0.15 else "BE"
            out.append(dict(key=f"{name}|{t_in}|{side}", strategy=name, side=side, t_entry=t_in, entry=round(entry, 2),
                            sl0=round(sl, 2), exit=round(ex, 2), t_exit=t_ex, reason=why, r=r, usd=usd, res=res))
            busy_until = t_ex if why != "OPEN" else 10 ** 12
    return sorted(out, key=lambda x: x["t_entry"])


def compare(data, scfg, protect, presets, auto_tf="M15", t_from=0, spread=0.3, fail_exit=True, fail_atr=0.2):
    """Both arms on the same data. -> dict(live, candidate) each with stats + trades."""
    cx = _Ctx(data, auto_tf)
    csc, cpr = candidate(scfg, protect, presets)
    a = simulate(cx, scfg, PM.clean(protect), t_from, None, spread, fail_exit, fail_atr)
    b = simulate(cx, csc, cpr, t_from, None, spread, fail_exit, fail_atr)
    return dict(live=dict(stats=_stats(a), trades=a), candidate=dict(stats=_stats(b), trades=b))


# ============================================================================ live shadow + history test
def load_settings(path=None):
    d = dict(enabled=False, presets=["be1r_structure"], started_t=0.0)
    try:
        with open(path or SETTINGS_FILE) as f:
            raw = json.load(f)
        d.update({k: raw[k] for k in d if k in raw})
    except (OSError, ValueError):
        pass
    d["presets"] = [p for p in d["presets"] if p in PRESETS]
    return d


class Shadow:
    def __init__(self, hub, start=True):
        self.hub = hub
        self.settings = load_settings()
        self.store = self._load_store()
        self.last_bar = None
        self.last = None
        self.error = ""
        self.lock = threading.Lock()
        self.job = dict(state="idle", msg="", result=None)
        self.running = start
        if start:
            threading.Thread(target=self._loop, daemon=True, name="shadow").start()

    # ---- storage
    def _load_store(self):
        try:
            with open(TRADES_FILE) as f:
                d = json.load(f)
            return {k: dict(d.get(k) or {}) for k in ("live", "candidate")}
        except (OSError, ValueError):
            return {"live": {}, "candidate": {}}

    def _save(self):
        try:
            with open(SETTINGS_FILE, "w") as f:
                json.dump(self.settings, f, indent=2)
            with open(TRADES_FILE, "w") as f:
                json.dump(self.store, f)
        except OSError:
            pass

    def _auto_tf(self):
        at = getattr(self.hub, "autotrader", None)
        return (at.settings.get("tf") if at is not None else None) or "M15"

    def _data(self, closed=True):
        src = self.hub.src
        out = {}
        for tf in ("M1", "M5", "M15", "H1", "H4"):
            try:
                b = src.bars(tf)
                out[tf] = b[:-1] if closed else b
            except Exception:
                pass
        return out

    def _fail(self):
        at = getattr(self.hub, "autotrader", None)
        s = at.settings if at is not None else {}
        return bool(s.get("conso_fail_exit", True)), float(s.get("conso_fail_atr", 0.2))

    # ---- forward shadow
    def update_settings(self, new):
        with self.lock:
            s = self.settings
            if "presets" in new and isinstance(new["presets"], list):
                ps = [p for p in new["presets"] if p in PRESETS]
                if ps != s["presets"]:
                    s["presets"] = ps
                    s["started_t"] = time.time() if s["enabled"] else 0.0       # a new candidate = a new test
                    self.store = {"live": {}, "candidate": {}}
            if "enabled" in new:
                on = bool(new["enabled"])
                if on and not s["enabled"]:
                    s["started_t"] = time.time()
                    self.store = {"live": {}, "candidate": {}}
                s["enabled"] = on
            if new.get("reset"):
                s["started_t"] = time.time() if s["enabled"] else 0.0
                self.store = {"live": {}, "candidate": {}}
            self.last_bar = None
            self._save()
        return self.view()

    def _loop(self):
        while self.running:
            try:
                self.tick()
            except Exception as e:
                self.error = f"{e.__class__.__name__}: {e}"
            time.sleep(30)

    def tick(self):
        if not self.settings["enabled"]:
            return
        tf = self._auto_tf()
        bars = self.hub.src.bars(tf)
        if len(bars) < 120 or bars[-1].t == self.last_bar:
            return
        self.last_bar = bars[-1].t
        fe, fa = self._fail()
        res = compare(self._data(), ST.load_settings(), PM.load_settings(), self.settings["presets"], tf,
                      t_from=self.settings["started_t"], fail_exit=fe, fail_atr=fa)
        with self.lock:
            for arm in ("live", "candidate"):
                for t in res[arm]["trades"]:
                    if t["reason"] != "OPEN":
                        self.store[arm][t["key"]] = t
            self.last = {arm: [t for t in res[arm]["trades"] if t["reason"] == "OPEN"] for arm in ("live", "candidate")}
            self._save()
        self.error = ""

    def view(self):
        with self.lock:
            arms = {}
            for arm in ("live", "candidate"):
                closed = sorted(self.store[arm].values(), key=lambda x: x["t_entry"])
                opn = (self.last or {}).get(arm, [])
                arms[arm] = dict(stats=_stats(closed + opn), recent=list(reversed(closed[-15:])), open=opn)
            return dict(settings=self.settings, arms=arms, error=self.error,
                        presets=[dict(key=k, title=v["title"]) for k, v in PRESETS.items()],
                        job=dict(state=self.job["state"], msg=self.job["msg"], result=self.job["result"]))

    # ---- history test ("test bago i-apply")
    def start_test(self, days=30, presets=None):
        if self.job["state"] == "running":
            return dict(ok=False, error="may tumatakbo pang test")
        days = max(3, min(120, int(days)))
        presets = [p for p in (presets if presets is not None else self.settings["presets"]) if p in PRESETS]
        self.job = dict(state="running", msg=f"kinukuha ang {days} araw ng history…", result=None)
        threading.Thread(target=self._test, args=(days, presets), daemon=True, name="shadow-test").start()
        return dict(ok=True)

    def _test(self, days, presets):
        try:
            import backtest
            src = self.hub.src
            if getattr(src, "live", False) and getattr(src, "mt5", None) is not None:
                with self.hub.lock:
                    raw = backtest.load_mt5(src, days, with_m1=True)
                source = f"MT5 {src.symbol}"
            else:
                raw = backtest.synth_data(days)
                source = "SIMULATED data (demo)"
            data = {k: raw[k] for k in ("M5", "M15", "H1", "H4") if raw.get(k)}
            if raw.get("_m1"):
                data["M1"] = raw["_m1"]
            tf = self._auto_tf()
            if tf not in data:
                tf = "M15"
            self.job["msg"] = "sinusubok ang LIVE at CANDIDATE…"
            t_from = data[tf][-1].t - days * 86400
            fe, fa = self._fail()
            res = compare(data, ST.load_settings(), PM.load_settings(), presets, tf, t_from=t_from, fail_exit=fe,
                          fail_atr=fa)
            self.job = dict(state="done", msg="tapos", result=dict(
                days=days, source=source, tf=tf, presets=presets,
                live=res["live"]["stats"], candidate=res["candidate"]["stats"],
                period=[t_from, data[tf][-1].t]))
        except Exception as e:
            self.job = dict(state="error", msg=f"{e.__class__.__name__}: {e}", result=None)

    def apply(self, presets):
        """v2.8.58 GAMITIN SA LIVE: write the candidate of the LAST finished history TEST (exactly those presets) into
        the live strategy + position-manager settings. The settings history keeps the state before (one-click
        IBALIK). Never sends an order. -> dict(ok, changes | error)."""
        presets = [p for p in (presets or []) if p in PRESETS]
        res = (self.job or {}).get("result") or {}
        if not presets:
            return dict(ok=False, error="walang napiling candidate")
        if self.job.get("state") != "done" or sorted(res.get("presets") or []) != sorted(presets):
            return dict(ok=False, error="i-▶ TEST muna ang candidate na ito (ang huling test ay ibang candidate)")
        sh = getattr(self.hub, "settings_hist", None)
        live_s, live_p = ST.load_settings(), PM.load_settings()
        if not os.path.exists(PM.SETTINGS_FILE):              # defaults only: write them so IBALIK has a file to restore
            with open(PM.SETTINGS_FILE, "w") as f:
                json.dump(dict(live_p, version=getattr(PM, "SETTINGS_VERSION", 4)), f, indent=2)
        if sh is not None:
            sh.snapshot("bago ang GAMITIN SA LIVE")
        sc, pr = candidate(live_s, live_p, presets)
        ST.save_settings(sc)
        pm = getattr(self.hub, "positions_mgr", None)
        if pm is not None and hasattr(pm, "update_settings"):
            pm.update_settings(pr)
        else:
            with open(PM.SETTINGS_FILE, "w") as f:
                json.dump(dict(pr, version=getattr(PM, "SETTINGS_VERSION", 4)), f, indent=2)
        changes = [f"position {k}: {live_p.get(k)} → {pr.get(k)}" for k in sorted(pr) if pr.get(k) != live_p.get(k)]
        for n in NAMES:
            for k in sorted(set(sc[n]) | set(live_s[n])):
                if sc[n].get(k) != live_s[n].get(k):
                    changes.append(f"{n} {k}: {live_s[n].get(k)} → {sc[n].get(k)}")
        if sh is not None:
            sh.snapshot("GAMITIN SA LIVE: " + ", ".join(PRESETS[p]["title"] for p in presets))
        return dict(ok=True, changes=changes, presets=presets)

    def stop(self):
        self.running = False
