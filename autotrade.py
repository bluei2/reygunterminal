"""
Auto trade: takes the signals the chart already shows (Analyser Signals, optionally Conso Breakout Signals) and
sends them through the normal trade path (master + every enabled follower, each with its own lot, journaled as
kind "auto").

Safety - the order is only sent when ALL of these hold:
  * AUTO switch ON (always OFF when the app starts; turning it on needs a confirm) and trading ARMED
  * the signal is on the bar that JUST closed (never an old arrow) and was not traded before
  * filters: allowed direction, session, not inside the news window, max cautions, min score,
    max auto trades per day (server day), max open SuperChart trades, spread limit
  * the normal checks of TradeManager.open(): master connected, not locked by daily DD/TP, Algo Trading ON ...
Every decision (taken or skipped, with the reason) is logged and shown on the AUTO TRADE card.
SL / TP = the signal's own levels, sent as $ distances from each account's fill (same as a manual entry).
"""
from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from datetime import datetime, timezone

from engine import TF_SECONDS
from error_log import report

HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(HERE, "auto_trade_settings.json")

DEFAULTS = dict(
    tf="M15",                      # v2.8.0 MASTER: M15 (the MT5 Master EA timeframe)
    auto_on_start=True,            # (Eugene, 2026-10-01: "i-ON mo na as default") trading + AUTO switch on by themselves
                                   # when the app starts; untick "AUTO ON pagbukas ng app" to start in MANUAL
    news_on_start=True,            # with auto_on_start: NEWS TRADE switches on too (Eugene, 2026-10-02: "i-ON mo lahat")
    strategies_only=True,          # (Eugene, 2026-10-01) "per strategy na tayo": AUTO trades ONLY the STRATEGIES set to
                                   # AUTO (TREND / PULLBACK / CONSOLIDATION); the sources below are ignored while True
    sources=["analyser"],          # "analyser" (Analyser Signals), "breakout" (Conso Breakout), "trendline" (TL Breakout),
                                   # "reversal" (Reversal arrows R? / R✓)
    reversal_confirmed_only=True,  # reversal arrows: only R✓ (a close through the reversal bar) - on the confirming bar
    reversal_confirm="bias",       # reversal arrows: "bias" = HTF bias must agree (end of a pullback) |
                                   #                  "score" = bias AND the analyser passing score
    reversal_min_score=60,         # reversal arrows: minimum reversal score (the number after R? / R✓)
    # REGIME ROUTER: the market regime (regime.py, your Regime settings) decides WHICH signal may trade and its RR
    router=False,                  # OFF = every selected source trades as before
    router_trend_labels=["TREND UP", "TREND DOWN"],   # trend mode: trend signals (analyser / breakout / trend line)
    router_range_labels=["RANGE"],                    # range mode: reversal arrows only
    router_trend_rr=2.0,           # TP = RR x SL distance in trend mode
    router_range_rr=1.0,           # TP = RR x SL distance in range mode
    router_range_against_htf=False,  # range mode: also fade AGAINST a clear HTF bias (off = only neutral / with it)
    lot=0.01,                      # master lot; followers use their own fixed lot
    direction="both",              # both | buy | sell
    max_cautions=0,                # skip signals with more ⚠ than this (-1 = ignore cautions)
    min_score=0.0,                 # 0 = the analyser's own passing score
    sessions=["Asia", "Frankfurt", "London", "New York"],   # v2.7.6: TREND did best in Asia (6-month test)
    news_block=True,               # no entry inside the news warning window (News settings)
    max_per_day=0,                 # 0 = unlimited (v2.8.0, Eugene: "unli na natin"); the daily loss / profit target
                                   # of the master account (Accounts) stops the day
    max_open=3,                    # max open SuperChart trades at the same time (MASTER: 3, one per strategy)
    max_spread=0.60,               # $; 0 = no limit
    regimes=[],                    # allowed market regimes (regime.py); empty = any
    no_countertrend=False,         # in TREND UP skip SELLs, in TREND DOWN skip BUYs
    score_confirm=True,            # breakout / trend line signals must be confirmed by the analyser score
    no_opposite=True,              # never open a trade against an open one (no BUY + SELL at the same time);
                                   # applies to manual entries too
    reentry_minutes=30,            # an extra trade in the SAME direction (while one is open) only after this many
                                   # minutes since the last entry, and only with a fresh confirmation (still in trend
                                   # and the analyser score still passes). 0 = no waiting
    # ---- MASTER (v2.8.0, Eugene 2026-10-04: "ilagay mo na ang master sa terminal") - the MT5 Master EA behaviour
    master_mode=True,              # each STRATEGY its own trade: TREND, PULLBACK and CONSO may be open together
                                   # (also in opposite directions); a strategy never opens a 2nd trade of its own
    reversal=True,                 # a fresh TREND signal closes the older trades AGAINST it (and the CONSO stop
    pullback_reversal=True,
    tl_auto=True,                  # v2.8.27 (Eugene): TL BREAK strategy - trend line breakout (close) + re-entry on retest
    align_mult=2.0,
    mode_mult=1.0,                 # v2.8.38: the mode's lot multiplier (AGGRESSIVE x2); the larger of this and align wins
    tl_max_sl=20.0,                # TL BREAK max SL ($) - SCALP 8
    tl_rr=5.0,                     # TL BREAK RR - AGGRESSIVE / SCALP 3                # v2.8.22 (Eugene): TREND / PULLBACK lot x2 when H4 + H1 + M15 all agree (1 = off)
    rev_cancel_conso=False,        # v2.8.19: a reversal does NOT cancel one side of a pending CONSO grid (keep the pair)        # v2.8.15: a fresh PULLBACK signal reverses too (closes the trades against it)
    rev_close=["trend", "pullback", "conso"],   # orders on that side) of these strategies
    trend_early_exit="m15_2bos",   # off | m15_bos | m15_2bos: close a TREND trade before its TP when the entry-TF
                                   # structure breaks against it (the 2nd break in the last 32 bars)
    conso_early_exit="off",
    trend_reentry_win=30,          # after a TREND WIN, a new TREND entry in the same H4 trend is allowed while that
                                   # H4 break is up to N H4 bars old (normally h4_window = 12). 0 = off
    trend_secure_r=1.0,            # TREND: close trend_secure_pct % at +R (then break-even). 0 = off.
    trend_secure_pct=50.0,         #   0.01 lot cannot be split: the trade then simply stays whole
    conso_protect=True,            # CONSO trades: break-even / profit lock / trailing of the position manager (v2.8.28 ON)
    # v2.8.48 (Eugene, range 4113-4133: the CONSO SELL STOP and BUY STOP both filled at the edges and lost ~$20 each)
    conso_fail_exit=True,          # a filled CONSO trade is closed when an AUTO-TF candle CLOSES back inside the box
    conso_fail_atr=0.2,            #   ... by at least this x ATR (a failed breakout: cut it small, not at the full SL)
    conso_one_loss=True,           # after a CONSO trade of a box LOSES, no new grid on that box (wait for a new box)
)
EXIT_MODES = ("off", "m15_bos", "m15_2bos")
import market_hours  # noqa: E402

SESSION_HOURS = market_hours.SESSIONS          # UTC, shared table (market_hours.SESSIONS / session_settings.json)


SETTINGS_VERSION = 5       # v2: auto_on_start ON by default; v3: Asia + Frankfurt added to the sessions (once)
                           # v4 (v2.8.0): MASTER once - M15, max 3 open, unlimited per day, no_opposite off


def load_settings():
    s = json.loads(json.dumps(DEFAULTS))
    try:
        with open(SETTINGS_FILE) as f:
            raw = json.load(f)
        ver = int(raw.get("settings_version", 1) or 1)
        if ver < 2:
            raw.pop("auto_on_start", None)
        if ver < 3 and isinstance(raw.get("sessions"), list) and raw["sessions"]:
            raw["sessions"] = list(dict.fromkeys(raw["sessions"] + ["Asia", "Frankfurt"]))   # v3 (once)
        if ver < 4:
            # v2.8.0 MASTER (once): the MT5 Master EA setup. Opposite trades of DIFFERENT strategies are allowed
            # (a reversal closes the older side), so the old "no opposite" rule is switched off.
            raw.update(tf="M15", max_open=3, max_per_day=0, no_opposite=False, master_mode=True)
            s["_migrate_master"] = True
        if ver < 5:
            # v2.8.28 (Eugene: CONSO 4170 -> 4180+ then back to SL): the CONSO trades also get the position manager
            # (break-even / profit lock / trailing), once
            raw["conso_protect"] = True
        for k, v in raw.items():
            if k in s:
                s[k] = v
    except (OSError, ValueError):
        pass
    return s


class AutoTrader:
    def __init__(self, tm):
        self.tm = tm
        self.hub = tm.hub
        self.enabled = False                       # ALWAYS off at start
        self.settings = load_settings()
        self.log = deque(maxlen=60)
        if self.settings.pop("_migrate_master", False):
            self._migrate_master()
        self.done = set()                          # (source, signal time) already handled
        self.last_bar = None
        self.today = None
        self.count_today = 0
        self.running = True
        threading.Thread(target=self._loop, daemon=True).start()

    def _migrate_master(self):
        """v2.8.0 once: STRATEGIES -> MASTER (when they were NORMAL) and this file saved as v4."""
        try:
            import strategies as _st
            changed = _st.migrate_to_master()
            with open(SETTINGS_FILE, "w") as f:
                json.dump(dict(self.settings, settings_version=SETTINGS_VERSION), f, indent=2)
            self._note("warn", "MASTER setup (v2.8.0): M15 · max 3 open (1 bawat strategy) · walang limit kada araw · "
                               "reversal close · early exit · secure 50% sa 1R"
                       + (" · STRATEGIES = MASTER (TREND + M5 + CVD, PULLBACK 30/70, CONSO AUTO)" if changed else ""))
        except Exception as e:
            report("CONFIG_ERROR", "autotrade", "MASTER_MIGRATION_FAILED", e)

    # ------------------------------------------------------------------ control
    def set_enabled(self, on):
        on = bool(on)
        if on and not self.tm.armed:
            return dict(ok=False, enabled=False, error="ARM trading first")
        self.enabled = on
        if on:
            # never trade arrows that are already on the chart: only bars that close from now on
            self._mark_existing()
        what = ", ".join(src for src, _ in self._plugins()) or "walang strategy na naka-AUTO"
        self._note("warn" if on else "info", f"AUTO TRADE {'ON' if on else 'OFF'}"
                   + (f" · {self.settings['tf']} · {what} · max {self.settings['max_per_day'] or 'walang limit'}/day" if on else ""))
        return dict(ok=True, enabled=self.enabled)

    def start(self, reason="AUTO ON"):
        """Trading ON + AUTO ON in one step (the confirm in the UI, or auto_on_start). A HALT is never cleared here."""
        tm = self.tm
        if getattr(tm, "halted", ""):
            return dict(ok=False, enabled=False, armed=tm.armed, error=f"HALTED: {tm.halted}", halted=True)
        if not tm.armed:
            tm.set_armed(True)
        if not tm.armed:
            return dict(ok=False, enabled=False, armed=False, error="hindi ma-ON ang trading")
        r = self.set_enabled(True)
        ok, why = tm.can_trade()
        r.update(armed=tm.armed, can_trade=ok, warning="" if ok else why)
        if r.get("ok") and not ok:
            self._note("warn", f"{reason}: AUTO ON pero hindi pa makakapag-order: {why}")
        return r

    def update_settings(self, new):
        s = self.settings
        for k in ("lot", "min_score", "max_spread", "align_mult", "mode_mult", "tl_max_sl", "tl_rr"):
            if k in new:
                try:
                    s[k] = max(0.0, float(new[k]))
                except (TypeError, ValueError):
                    pass
        for k in ("max_cautions", "max_per_day", "max_open"):
            if k in new:
                try:
                    s[k] = max(-1 if k == "max_cautions" else 0, int(float(new[k])))
                except (TypeError, ValueError):
                    pass
        if new.get("tf") in ("M1", "M5", "M15", "M30", "H1"):
            s["tf"] = new["tf"]
        if new.get("direction") in ("both", "buy", "sell"):
            s["direction"] = new["direction"]
        if isinstance(new.get("sources"), list):
            s["sources"] = [x for x in new["sources"] if x in ("analyser", "breakout", "trendline", "reversal")] \
                or ["analyser"]
        if "router" in new:
            s["router"] = bool(new["router"])
        if "router_range_against_htf" in new:
            s["router_range_against_htf"] = bool(new["router_range_against_htf"])
        for k in ("router_trend_rr", "router_range_rr"):
            if k in new:
                try:
                    s[k] = max(0.3, min(10.0, float(new[k])))
                except (TypeError, ValueError):
                    pass
        import regime as _rgm
        for k in ("router_trend_labels", "router_range_labels"):
            if isinstance(new.get(k), list):
                s[k] = [x for x in new[k] if x in _rgm.ALL]
        if "reversal_confirmed_only" in new:
            s["reversal_confirmed_only"] = bool(new["reversal_confirmed_only"])
        if new.get("reversal_confirm") in ("bias", "score"):
            s["reversal_confirm"] = new["reversal_confirm"]
        if "reversal_min_score" in new:
            try:
                s["reversal_min_score"] = max(0, min(100, int(float(new["reversal_min_score"]))))
            except (TypeError, ValueError):
                pass
        if isinstance(new.get("sessions"), list):
            s["sessions"] = [x for x in new["sessions"] if x in SESSION_HOURS]
        if "news_block" in new:
            s["news_block"] = bool(new["news_block"])
        if "no_countertrend" in new:
            s["no_countertrend"] = bool(new["no_countertrend"])
        if "news_on_start" in new:
            s["news_on_start"] = bool(new["news_on_start"])
        if "auto_on_start" in new:
            s["auto_on_start"] = bool(new["auto_on_start"])
        if "strategies_only" in new:
            s["strategies_only"] = bool(new["strategies_only"])
        if "score_confirm" in new:
            s["score_confirm"] = bool(new["score_confirm"])
        if "no_opposite" in new:
            s["no_opposite"] = bool(new["no_opposite"])
        if "reentry_minutes" in new:
            try:
                s["reentry_minutes"] = max(0, int(float(new["reentry_minutes"])))
            except (TypeError, ValueError):
                pass
        for k in ("master_mode", "reversal", "conso_protect", "pullback_reversal", "rev_cancel_conso", "tl_auto",
                  "conso_fail_exit", "conso_one_loss"):
            if k in new:
                s[k] = bool(new[k])
        if isinstance(new.get("rev_close"), list):
            s["rev_close"] = [x for x in new["rev_close"] if x in ("trend", "pullback", "conso")]
        for k in ("trend_early_exit", "conso_early_exit"):
            if new.get(k) in EXIT_MODES:
                s[k] = new[k]
        for k, lo, hi in (("trend_reentry_win", 0, 200), ("trend_secure_r", 0.0, 10.0), ("trend_secure_pct", 0.0, 100.0),
                          ("conso_fail_atr", 0.0, 3.0)):
            if k in new:
                try:
                    v = float(new[k])
                    s[k] = max(lo, min(hi, int(v) if isinstance(lo, int) else v))
                except (TypeError, ValueError):
                    pass
        if isinstance(new.get("regimes"), list):
            import regime as _rg
            s["regimes"] = [x for x in new["regimes"] if x in _rg.ALL]
        try:
            with open(SETTINGS_FILE, "w") as f:
                json.dump(dict(s, settings_version=SETTINGS_VERSION), f, indent=2)
        except OSError:
            pass
        self._mark_existing()
        return dict(ok=True, settings=s)

    # ------------------------------------------------------------------ helpers
    def _note(self, kind, text, **kw):
        self.log.appendleft(dict(t=time.time(), kind=kind, text=text, **kw))
        if kind == "signal":
            self.hub.note("signal", text, plugin="Auto trade", dir=kw.get("side", 0), tf=self.settings["tf"],
                          bar=time.strftime("%m-%d %H:%M"))
        else:
            self.hub.note(kind, text)

    def _plugins(self):
        want = {"analyser": "analyser_signals.AnalyserSignals", "breakout": "conso_breakout.ConsoBreakoutSignals",
                "trendline": "trendline_breakout.TrendlineBreakout", "reversal": "reversal_signals.ReversalSignals"}
        out = []
        srcs = [] if self.settings.get("strategies_only", True) else list(self.settings["sources"])
        if self.settings.get("router") and srcs:                # the router needs both a trend source and the arrows
            srcs += [x for x in ("analyser", "reversal") if x not in srcs]
        for src in srcs:
            p = next((x for x in self.hub.plugins if x.key == want.get(src)), None)
            if p is not None and not p.error:
                out.append((src, p))
        if self.settings.get("tl_auto", True):
            # v2.8.27 TL BREAK: the Trend Line Breakout plugin as a strategy - "tl_break" enters on the CLOSE through
            # the line, "tl_retest" re-enters when the broken line is retested and holds (each its own trade)
            p = next((x for x in self.hub.plugins if x.key == want["trendline"]), None)
            if p is not None and not p.error:
                out += [("tl_break", p), ("tl_retest", p)]
        # the market-condition STRATEGIES switched to AUTO on the Trading page (strategies.py)
        try:
            import strategies as _st
            cfg = _st.load_settings()
            for n in _st.NAMES:
                if cfg[n].get("auto"):
                    out.append((f"st_{n}", None))
        except Exception as e:
            report("SIGNAL_ERROR", "autotrade", "STRATEGIES_UNAVAILABLE", e)
        return out

    def _signals(self, src, p, bars, tf):
        """The same signals the chart draws (details incl. score and cautions)."""
        if src.startswith("st_"):
            import strategies as _st
            name = src[3:]
            cfg = _st.load_settings()[name]
            sec = TF_SECONDS[tf]
            hub = self.hub

            def bias_fn(i):
                try:
                    return hub.read_at(bars[i].t + sec).bias or 0
                except Exception:
                    return 0
            S = _st.series(bars, _st.hub_mtf(hub))
            if name == "trend":
                cfg = self._trend_cfg(cfg, S, len(bars) - 2)
            return _st.signals(name, bars, cfg, bias_fn if name != "conso" else None, 60, S=S)
        with self.hub.lock:
            ctx = self.hub.ctx()
            if src in ("tl_break", "tl_retest"):
                old = p.obj.params
                try:
                    p.obj.params = dict(old, entry="break" if src == "tl_break" else "retest", confirmed_only=False,
                                        max_sl=float(self.settings.get("tl_max_sl", 20.0)),
                                        rr=float(self.settings.get("tl_rr", 5.0)), sl_mode="swing")
                    return p.obj.signals(bars, ctx.utc_offset if ctx else 0) or []
                finally:
                    p.obj.params = old
            if src == "reversal":                  # AUTO TRADE's own R✓ / R? choice, not the chart setting
                return p.obj.signals(bars, ctx.utc_offset if ctx else 0,
                                     confirmed_only=bool(self.settings.get("reversal_confirmed_only", True)))
            if src == "analyser" and self.settings.get("min_score"):
                # AUTO "Min score %" is the READY threshold of the arrows AUTO trades (bias + trigger + score >= it);
                # computed with a temporary copy of the params under the hub lock (the chart keeps its own setting)
                old = p.obj.params
                try:
                    p.obj.params = dict(old, min_score=float(self.settings["min_score"]))
                    return p.obj.signals(bars, tf, ctx) or []
                finally:
                    p.obj.params = old
            ov = p.obj.compute(bars, tf, ctx)
        return getattr(ov, "signals", []) or []

    def _mark_existing(self):
        tf = self.settings["tf"]
        bars = self.hub.src.bars(tf)
        if len(bars) < 30:
            return
        for src, p in self._plugins():
            if src.startswith("st_"):
                continue                       # strategies: a READY that is still all green is taken (see _strategies)
            try:
                for sg in self._signals(src, p, bars, tf):
                    self.done.add((src, sg["t"]))
            except Exception as e:
                report("SIGNAL_ERROR", "autotrade", "MARK_EXISTING_FAILED", e)
        self.last_bar = bars[-1].t

    def _session_ok(self):
        now = datetime.now(timezone.utc)
        if now.weekday() >= 5:
            return False, "weekend"
        allowed = self.settings["sessions"]
        if not allowed:
            return True, ""
        h = now.hour
        for name in allowed:
            if market_hours.in_session(name, h):
                return True, name
        return False, f"outside {' / '.join(allowed)} (UTC {h:02d}h)"

    # ------------------------------------------------------------------ loop
    def _loop(self):
        while self.running:
            try:
                if self.enabled:
                    self._cycle()
            except Exception as e:
                from error_log import where
                import traceback
                self._note("error", f"auto trade: {where(e)}")
                traceback.print_exc()
            time.sleep(1.0)

    def _cycle(self):
        tm, s = self.tm, self.settings
        if not tm.armed:
            self.enabled = False
            self._note("warn", "AUTO TRADE OFF: trading was disarmed")
            return
        tf = s["tf"]
        bars = self.hub.src.bars(tf)
        if len(bars) < 30:
            return
        new_bar = bars[-1].t != self.last_bar
        if new_bar or time.time() >= getattr(self, "_st_next", 0):
            self._st_next = time.time() + 15
            self._strategies(bars, tf)                 # STRATEGIES: all green -> trade (checked often, not only per bar)
        if not new_bar:
            return                                     # wait for a new bar -> the previous one just closed
        self.last_bar = bars[-1].t
        closed_t = bars[-2].t
        try:
            self._early_exit(bars)                     # MASTER: the structure turned against an open trade
        except Exception as e:
            report("SIGNAL_ERROR", "autotrade", "EARLY_EXIT_FAILED", e)
        try:
            self._conso_fail_exit(bars)                # v2.8.48: a CONSO breakout that went back inside the box
        except Exception as e:
            report("SIGNAL_ERROR", "autotrade", "CONSO_FAIL_EXIT_FAILED", e)
        day = closed_t // 86400
        if day != self.today:
            self.today, self.count_today = day, 0
        for src, p in self._plugins():
            if src.startswith("st_"):
                continue
            for sg in self._signals(src, p, bars, tf):
                key = (src, sg["t"])
                if key in self.done:
                    continue
                self.done.add(key)
                if sg["t"] != closed_t:
                    continue                           # only the bar that just closed
                self._consider(src, sg)
                if src in ("tl_break", "tl_retest") and getattr(self, "_last_res", None) == "trade" \
                        and s.get("master_mode", True) and s.get("pullback_reversal", True):
                    self._reversal(sg["side"], key, who="TL BREAK")     # close the open trades AGAINST it

    def _strategies(self, bars, tf):
        """STRATEGIES set to AUTO: when EVERY checked condition is green on the last closed bar, trade it - once per
        READY spell (a spell = the signal bar where it turned READY; it stays one trade until it turns off and on
        again). Also a READY that was already green when AUTO was switched on. Entry / SL / TP / pending plan are
        taken from the LAST closed bar (current prices), not from the bar where the spell began."""
        import strategies as _st
        try:
            cfg = _st.load_settings()
        except Exception as e:
            report("SIGNAL_ERROR", "autotrade", "STRATEGIES_UNAVAILABLE", e)
            return
        names = [n for n in _st.NAMES if cfg[n].get("auto")]
        if not names or len(bars) < 82:
            return
        sec = TF_SECONDS[tf]
        hub = self.hub
        i = len(bars) - 2                              # the last CLOSED bar
        try:
            S = _st.series(bars, _st.hub_mtf(hub))
        except Exception as e:
            report("SIGNAL_ERROR", "autotrade", "STRATEGY_SERIES_FAILED", e)
            return
        for name in names:
            src = f"st_{name}"
            try:
                sigs = self._signals(src, None, bars, tf)
                if not sigs:
                    continue
                spell = sigs[-1]
                key = (src, spell["t"])
                if key in self.done:
                    continue
                tries = getattr(self, "_st_tries", {})
                self._st_tries = tries
                if time.time() - tries.get(key, 0) < 60:
                    continue                           # skipped < 60 s ago: try again later while it stays green
                tries[key] = time.time()
                try:
                    bias = (hub.read_at(bars[i].t + sec).bias or 0) if name != "conso" else 0
                except Exception:
                    bias = 0
                ncfg = self._trend_cfg(cfg[name], S, i) if name == "trend" else cfg[name]
                e = _st.evaluate(name, bars, S, i, ncfg, bias)
                if not e.get("ok") or (name != "conso" and e.get("side") != spell["side"]):
                    continue                           # not (or no longer) all green
                # v2.8.20 (Eugene: "pag may entry lng sna ang pag close"): the REVERSAL (close the open TREND /
                # PULLBACK / CONSO trades against the new signal) runs only when the new TREND / PULLBACK trade
                # actually ENTERS - see after _consider below. A blocked signal closes nothing.
                rev_who = "TREND" if name == "trend" else \
                    "PULLBACK" if name == "pullback" and self.settings.get("pullback_reversal", True) else None
                sg = dict(spell, score=e.get("score", spell.get("score")))
                if name == "conso":
                    # v2.8.6 (Eugene: "ok lng mag open prin ng pending order ang conso, basta pag na hit ang isa close
                    # ang opposite"): while the box stays READY, a NEW grid (BUY STOP + SELL STOP, OCO) is placed once the
                    # previous grid of this box is finished (expired / cancelled / filled and closed). One grid at a
                    # time; never while a CONSO trade is still open.
                    rounds = getattr(self, "_conso_round", {})
                    self._conso_round = rounds
                    rnd = rounds.get(key, 0)
                    gate0 = getattr(hub, "gate", None)
                    if gate0 is not None:
                        ev0 = f"conso_r{rnd}" if rnd else (spell.get("event") or spell.get("kind") or "")
                        sid0 = gate0.make_signal(src, self.settings["tf"], spell["t"], spell["side"] or 1, None, None,
                                                 None, None, None, ev0, src)["signal_id"]
                        if gate0.journal.seen.get(sid0):
                            nt0 = getattr(hub, "newstrader", None)
                            live = nt0 is not None and hasattr(nt0, "strategy_box_live") and nt0.strategy_box_live()
                            own = [g for g in self.tm.groups.values() if getattr(g, "source", "") == src]
                            if live or own:
                                self._note_once(key, f"CONSO: naka-place na ang grid ng box na ito "
                                                     f"({'may bukas na CONSO trade' if own else 'naghihintay ma-hit / mag-expire, OCO'})")
                                continue
                            if spell["t"] < getattr(self, "_conso_live_t", 0):
                                continue               # an older box, superseded by a newer one: no new round
                            if self.settings.get("conso_one_loss", True) and self._conso_box_lost(spell["t"]):
                                self._note_once(key, "CONSO: natalo na ang isang trade ng box na ito → walang bagong grid "
                                                     "hanggang may bagong box (range ito, hindi siksik)")
                                continue
                            rnd += 1
                            rounds[key] = rnd
                            self._note("info", f"CONSO: tapos na ang dating grid, READY pa rin ang box → bagong BUY STOP / "
                                               f"SELL STOP (OCO), round {rnd + 1}")
                    if rnd:
                        sg["event"] = f"conso_r{rnd}"
                    sg["plan"] = e
                    sg.update(entry=e["buy_stop"], sl=e["buy_sl"], tp=e["buy_tp"])
                else:
                    sg.update(entry=e["entry"], sl=e["sl"], tp=e["tp"])
                self._consider(src, sg)
                if rev_who and self.settings.get("master_mode", True):
                    res0 = getattr(self, "_last_res", None)
                    if res0 in ("BLOCKED_MAX_OPEN_POSITIONS", "BLOCKED_MAX_OPEN") and \
                            any(g.side == -spell["side"] for g in self.tm.groups.values()):
                        # the slots are taken by trades AGAINST the new signal: close them, then try the entry again
                        self._reversal(spell["side"], key, who=rev_who)
                        self._consider(src, sg)
                    elif res0 == "trade":
                        self._reversal(spell["side"], key, who=rev_who)
                gate = getattr(hub, "gate", None)
                sid = None
                if gate is not None:
                    sid = gate.make_signal(src, self.settings["tf"], sg["t"], sg["side"] or 1, sg.get("entry"),
                                           sg.get("sl"), sg.get("tp"), sg.get("score"), sg.get("cautions"),
                                           sg.get("event") or sg.get("kind") or "", src)["signal_id"]
                if sid and gate.journal.seen.get(sid) and name != "conso":
                    self.done.add(key)                 # it became a trade: this spell is finished (CONSO: next grid)
            except Exception as ex:
                from error_log import where
                self._note("error", f"auto trade: strategy {name}: {where(ex)}")

    # ------------------------------------------------------------------ MASTER (the MT5 Master EA behaviour)
    def _trend_cfg(self, cfg, S, i):
        """RE-ENTRY after a WIN (Master EA v1.40): the last closed TREND trade won and the H4 trend that paid is still
        the latest H4 break -> that break may be up to trend_reentry_win H4 bars old (instead of h4_window)."""
        win = int(self.settings.get("trend_reentry_win", 0) or 0)
        if not self.settings.get("master_mode", True) or win <= int(cfg.get("h4_window", 12)) or i < 0:
            return cfg
        import strategies as _st
        try:
            h4 = _st.mtf_state(S, i, cfg.get("bos_htf", "H4"), win)
        except Exception:
            return cfg
        if not h4 or not h4["last"] or h4["age"] is None:
            return cfg
        if not (cfg.get("h4_window", 12) <= h4["age"] < win):
            return cfg                                 # still fresh (normal rule) or too old
        if self._last_trend_win() != h4["last"]:
            return cfg
        return dict(cfg, h4_window=win, reentry=True)

    def _last_trend_win(self):
        """Direction of the last CLOSED TREND trade if it was a win (0 = lost / none / a TREND trade still open)."""
        tm = self.tm
        if any(getattr(g, "source", "") == "st_trend" for g in tm.groups.values()):
            return 0
        last = None
        for e in list(tm.journal.entries.values()):
            if e.get("strategy") != "st_trend" or e.get("status") != "closed" or e.get("pnl") is None:
                continue
            if last is None or (e.get("close_time") or 0) > (last.get("close_time") or 0):
                last = e
        return (last.get("side") or 0) if last and (last.get("pnl") or 0) > 0 else 0

    def _reversal(self, d, key, who="TREND"):
        """A fresh TREND signal in direction d closes the older trades of rev_close strategies that are AGAINST it,
        and cancels the unfilled CONSO stop orders on that side. Once per TREND signal."""
        s, tm = self.settings, self.tm
        if not s.get("master_mode", True) or not s.get("reversal", True) or not d:
            return
        done = getattr(self, "_rev_done", None)
        if done is None:
            done = self._rev_done = set()
        if key in done:
            return
        done.add(key)
        srcs = {f"st_{n}" for n in s.get("rev_close", [])} | {"tl_break", "tl_retest"}   # TL BREAK trades too
        hit = [g for g in list(tm.groups.values()) if getattr(g, "source", "") in srcs and g.side == -d]
        word = "UP" if d == 1 else "DOWN"
        for g in hit:
            r = tm.close_group(g.gid, 1.0, source=f"reversal: {who} {word}")
            self._note("warn", f"REVERSAL {who} {word}: isinara ang {g.source.split('_', 1)[1].upper()} "
                               f"{'BUY' if g.side == 1 else 'SELL'} {g.gid}" + ("" if r.get("ok") else f" (FAILED: {r})"),
                       decision="exit", side=g.side)
        n = 0
        nt = getattr(self.hub, "newstrader", None)
        # v2.8.19 (Eugene: "nag cancel ung pending order na buy kahit wala pang na hit"): the CONSO grid stays a PAIR
        # (BUY STOP + SELL STOP, OCO) on a reversal; one side is cancelled only when rev_cancel_conso is ON
        if "st_conso" in srcs and nt is not None and hasattr(nt, "cancel_strategy_side") \
                and self.settings.get("rev_cancel_conso", False):
            n = nt.cancel_strategy_side(-d, f"REVERSAL {word}")
            if n:
                self._note("warn", f"REVERSAL {who} {word}: {n} CONSO {'SELL' if d == 1 else 'BUY'} STOP cancelled")
        self.last_reversal = dict(t=time.time(), dir=d, closed=[g.gid for g in hit], cancelled=n)

    def _early_exit(self, bars):
        """EARLY EXIT (Master EA v1.50): on the bar that just closed, a break of structure on the AUTO TF AGAINST an
        open TREND (or CONSO) trade closes it before its TP (m15_2bos: only the 2nd break in the last 32 bars)."""
        s, tm = self.settings, self.tm
        if not s.get("master_mode", True):
            return
        modes = {"st_trend": s.get("trend_early_exit", "off"), "st_conso": s.get("conso_early_exit", "off")}
        if all(m == "off" for m in modes.values()) or len(bars) < 40:
            return
        import strategies as _st
        closed = bars[:-1]
        bos = _st._bos(closed)
        b = bos[-1]
        if not b:
            return
        n_dir = sum(1 for x in bos[-32:] if x == b)
        for g in list(tm.groups.values()):
            m = modes.get(getattr(g, "source", ""), "off")
            if m == "off" or g.side != -b or (m == "m15_2bos" and n_dir < 2):
                continue
            r = tm.close_group(g.gid, 1.0, source=f"early exit: {s['tf']} BOS {'UP' if b == 1 else 'DOWN'}")
            self._note("warn", f"EARLY EXIT {g.source[3:].upper()} {'BUY' if g.side == 1 else 'SELL'} {g.gid}: "
                               f"{'2nd ' if m == 'm15_2bos' else ''}{s['tf']} BOS {'UP' if b == 1 else 'DOWN'} kontra sa trade"
                       + ("" if r.get("ok") else f" (FAILED: {r.get('error')})"), decision="exit", side=g.side)

    def _conso_fail_exit(self, bars):
        """v2.8.48 FAILED BREAKOUT: a filled CONSO trade whose AUTO-TF candle (closed after the fill) CLOSES back inside
        its box by >= conso_fail_atr x ATR is closed at once - in a range the stop orders fill at the edges and the price
        turns back; this takes a small loss instead of the full stop on the other side of the box."""
        s, tm = self.settings, self.tm
        nt = getattr(self.hub, "newstrader", None)
        if not s.get("conso_fail_exit", True) or nt is None or len(bars) < 20:
            return
        import exits
        closed = bars[:-1]
        sec = TF_SECONDS.get(s["tf"], 900)
        c, t_close = closed[-1].c, closed[-1].t + sec
        a = exits.atr(closed, 14)[-1] or 0.0
        inside = float(s.get("conso_fail_atr", 0.2)) * a
        for st in list(getattr(nt, "events", {}).values()):
            box = st.get("box")
            if st.get("kind") != "strategy" or not box:
                continue
            for k, gid in list((st.get("groups") or {}).items()):
                g = tm.groups.get(gid) if isinstance(gid, str) else None
                if g is None or getattr(g, "source", "") != "st_conso" or t_close <= (g.created or 0):
                    continue
                back = c < box["high"] - inside if g.side == 1 else c > box["low"] + inside
                if not back:
                    continue
                r = tm.close_group(gid, 1.0, source="conso failed breakout")
                self._note("warn", f"CONSO FAILED BREAKOUT {'BUY' if g.side == 1 else 'SELL'} {gid}: {s['tf']} close {c:.2f} "
                                   f"bumalik sa loob ng box {box['low']:.2f}–{box['high']:.2f} → isinara (maliit na talo, "
                                   f"hindi buong SL)" + ("" if r.get("ok") else f" (FAILED: {r.get('error')})"),
                           decision="exit", side=g.side)

    def _conso_box_lost(self, since_t):
        """v2.8.48: a CONSO trade opened since this box became READY has closed with a loss."""
        j = getattr(self.tm, "journal", None)
        if j is None:
            return False
        return any(e.get("strategy") == "st_conso" and e.get("status") == "closed" and (e.get("pnl") or 0) < 0
                   and (e.get("open_time") or 0) >= since_t for e in list(j.entries.values()))

    def _note_once(self, key, text):
        """Log a CONSO waiting note once per spell (the strategy is re-checked every minute)."""
        seen = getattr(self, "_once", {})
        self._once = seen
        if seen.get(key) != text:
            seen[key] = text
            self._note("info", f"AUTO skip st_conso: [BLOCKED_REENTRY] {text}", decision="skip", side=0, code="BLOCKED_REENTRY")

    def _align_mult(self, side):
        """v2.8.22 (Eugene: "0.01 pag H1/M15, pero pag H4/H1/M15 ang align 0.02 ... x2 pag align sa htf"): the lot
        (master AND followers) is multiplied by align_mult when the H4, H1 and M15 structure all point the trade's way."""
        m = float(self.settings.get("align_mult", 2.0) or 1.0)
        if m <= 1.0 or not side:
            return 1.0
        st = getattr(self.hub, "states", None) or {}
        ok = all(getattr(st.get(tf), "trend", 0) == side for tf in ("H4", "H1", "M15"))
        return m if ok else 1.0

    def _cap_sl(self, sg, name):
        """TREND / PULLBACK stop: the strategy's own stop (TREND last swing), but never further than
        `max_sl` (v2.8.23 Eugene: $20 price = 200 gold pips) and, when `max_risk_pct` > 0, never more than that % of the
        master equity at the AUTO lot (v2.8.17). The TP keeps the same RR."""
        try:
            import strategies as _st
            c = _st.load_settings().get(name, {})
            cap_pct, max_sl = float(c.get("max_risk_pct", 0.0)), float(c.get("max_sl", 20.0))
            risk = getattr(self.hub, "risk", None)
            eq = float(self.tm.master.info.get("equity") or 0)
            lot = float(self.settings["lot"])
            cs = risk.contract_size()[0] if risk is not None else 100.0
        except Exception:
            return sg
        side = sg["side"] or 1
        sl_d = abs(sg["entry"] - sg["sl"])
        lim = [max_sl] if max_sl > 0 else []
        why = [f"max ${max_sl:g}"] if max_sl > 0 else []
        if cap_pct > 0 and eq > 0 and lot > 0 and cs > 0:
            lim.append(int(cap_pct / 100 * eq / (lot * cs) * 100) / 100)      # $ distance, rounded down to the cent
            why.append(f"{cap_pct:g}% ng equity ${eq:.2f}")
        max_d = min(lim) if lim else 0.0
        if max_d <= 0 or sl_d <= max_d:
            return sg
        rr = abs(sg["tp"] - sg["entry"]) / sl_d if sg.get("tp") else 2.0
        out = dict(sg, sl=round(sg["entry"] - side * max_d, 2), tp=round(sg["entry"] + side * max_d * rr, 2))
        noted = getattr(self, "_slcap_noted", set())
        self._slcap_noted = noted
        if (name, sg.get("t")) not in noted:
            noted.add((name, sg.get("t")))
            self._note("info", f"{name.upper()} SL: ${sl_d:.2f} → ${max_d:.2f} ({' / '.join(why)}), "
                               f"TP ${max_d * rr:.2f} (1:{rr:.1f})")
        return out

    def _cap_trend_sl(self, sg):                   # kept for older callers / tests
        return self._cap_sl(sg, "trend")

    def _consider(self, src, sg):
        """SIGNAL -> auto-trade filters -> MASTER GATE (decision, risk, execution, duplicates, AUTO, ARM) -> ORDER.
        Every outcome has a machine-readable code and is written to the signal journal (trade_gate.py)."""
        tm, s = self.tm, self.settings
        if src in ("st_trend", "st_pullback") and sg.get("entry") and sg.get("sl"):
            sg = self._cap_sl(sg, src[3:])
        side = sg["side"] or 1                     # a pending straddle (side 0) is checked as its BUY STOP leg
        word = "BUY/SELL STOP" if sg.get("pending") else "BUY" if side == 1 else "SELL"
        tag = f"{word} {sg['entry']:.2f} ({src}{', score ' + str(round(sg['score'])) + '%' if sg.get('score') is not None else ''})"
        gate = getattr(self.hub, "gate", None)
        sig = gate.make_signal(src, s["tf"], sg["t"], side, sg.get("entry"), sg.get("sl"), sg.get("tp"),
                               sg.get("score"), sg.get("cautions"), sg.get("event") or sg.get("kind") or "",
                               src) if gate else None

        strat_src = src.startswith("st_")
        self._last_res = None                      # v2.8.20: "trade" or the block code, read by _strategies

        def skip(why, code="BLOCKED_SCORE"):
            self._last_res = code
            # a strategy spell is retried while it stays green: journal / log a reason only when it changes
            last = getattr(self, "_skip_last", {})
            self._skip_last = last
            k = sig["signal_id"] if sig else None
            if strat_src and k is not None and last.get(k) == (code, why):
                return
            if k is not None:
                last[k] = (code, why)
            if gate is not None and sig is not None and not gate.is_duplicate(sig["signal_id"], traded_only=strat_src):
                gate.record_skip(sig, code, why)
            self._note("info", f"AUTO skip {tag}: [{code}] {why}", decision="skip", side=side, code=code)

        if gate is None:
            return skip("master gate not available: no automated order without it", "BLOCKED_MASTER_DISABLED")
        if gate.is_duplicate(sig["signal_id"], traded_only=strat_src):
            return self._note("info", f"AUTO skip {tag}: [BLOCKED_DUPLICATE_SIGNAL] signal {sig['signal_id']} "
                                      f"already processed", decision="skip", side=side, code="BLOCKED_DUPLICATE_SIGNAL")
        # ---- auto-trade signal filters (your AUTO TRADE settings)
        if not sg.get("pending") and (s["direction"] == "buy" and side != 1 or s["direction"] == "sell" and side != -1):
            return skip(f"direction set to {s['direction']} only", "BLOCKED_DIRECTION")
        # v2.8.11 (Eugene: "basta may conso wag nlng pipigilan"): the CONSO grid is placed in ANY session; risk,
        # spread, news window, ARM / daily lock and one-grid-at-a-time still apply
        ok, why = (True, "") if src == "st_conso" else self._session_ok()
        if not ok:
            return skip(why, "BLOCKED_SESSION")
        waive = ()
        route = None
        strat = src.startswith("st_") or src in ("tl_break", "tl_retest")   # TL BREAK behaves like a strategy
        if s.get("router") and not strat:          # strategies route themselves (their own market condition)
            route, why = self._route(src, side)
            if route is None:
                return skip(why, "BLOCKED_REGIME")
        if strat:
            # the strategy's own conditions + passing % replace the analyser trigger / score; the HTF bias is still
            # required for TREND / PULLBACK (gate), a CONSOLIDATION straddle has no direction
            # (100% of the strategy's own conditions). STRATEGIES "Analyser HTF bias filter" OFF (aggressive / scalp):
            # the strategy's own trend (e.g. the H4 / H1 BOS) is the direction, the analyser's HTF bias is not asked
            import strategies as _st
            own_dir = not _st.load_settings().get("htf_filter", True)
            # a CONSOLIDATION straddle has no direction: neither the HTF bias nor a structure conflict applies
            # v2.8.18 (Eugene: "kahit against sa htf"): PULLBACK in both directions ignores the HTF bias and the
            # structure conflict on every profile
            pb_free = (src == "st_pullback" and _st.load_settings().get("pullback", {}).get("any_dir", False)) \
                or src in ("tl_break", "tl_retest")    # a trend line break is often AGAINST the old trend
            waive = ("trigger", "score", "min_confidence") + (("htf:range",) if sg.get("pending") or own_dir or pb_free else ()) \
                + (("htf:own",) if own_dir or sg.get("pending") or pb_free else ())
        if src == "reversal" and route and route["mode"] == "RANGE":
            # range mode: fade the edge with the arrow; both directions, but not against a clear HTF bias
            if s.get("reversal_confirmed_only", True) and not sg.get("confirmed"):
                return skip("reversal not confirmed (R?): only R✓ is traded", "BLOCKED_STRUCTURE")
            if (sg.get("score") or 0) < s.get("reversal_min_score", 60):
                return skip(f"reversal score {sg.get('score') or 0:.0f} < {s.get('reversal_min_score', 60)}",
                            "BLOCKED_SCORE")
            try:
                r = self.hub.read_at(sg["t"] + TF_SECONDS[s["tf"]])
            except Exception as e:
                self.hub.note("error", f"auto trade: read_at failed: {e}", category="SIGNAL_ERROR")
                r = None
            bias = getattr(r, "bias", 0) or 0
            if bias == -side and not s.get("router_range_against_htf"):
                return skip(f"router RANGE: {word} arrow against the HTF bias {'BULL' if bias == 1 else 'BEAR'}",
                            "BLOCKED_STRUCTURE")
            # the arrow's own score + R✓ stand in for the trend-based score / setup confidence in a range
            waive = ("trigger", "score", "min_confidence") + (("htf:range",) if bias != side else ())
        elif src == "reversal":
            # a reversal arrow is its own trigger; with "bias" it also stands in for the analyser score (its own
            # reversal score is checked instead), the HTF bias must still agree - never a counter-trend arrow
            if s.get("reversal_confirmed_only", True) and not sg.get("confirmed"):
                return skip("reversal not confirmed (R?): only R✓ is traded", "BLOCKED_STRUCTURE")
            if (sg.get("score") or 0) < s.get("reversal_min_score", 60):
                return skip(f"reversal score {sg.get('score') or 0:.0f} < {s.get('reversal_min_score', 60)}",
                            "BLOCKED_SCORE")
            try:
                r = self.hub.read_at(sg["t"] + TF_SECONDS[s["tf"]])
            except Exception as e:
                self.hub.note("error", f"auto trade: read_at failed: {e}", category="SIGNAL_ERROR")
                r = None
            if r is None or not r.bias:
                return skip("no HTF bias to confirm the reversal", "BLOCKED_STRUCTURE")
            if r.bias != side:
                return skip(f"reversal {word} against the HTF bias {'BULL' if r.bias == 1 else 'BEAR'} "
                            f"(counter-trend arrows are not traded)", "BLOCKED_STRUCTURE")
            if s.get("reversal_confirm", "bias") == "score":
                if r.overall is None or r.overall < r.passing:
                    return skip(f"analyser score {r.overall or 0:.0f}% < passing {r.passing:.0f}% (no confirmation)",
                                "BLOCKED_SCORE")
                waive = ("trigger",)
            else:
                waive = ("trigger", "score")
        elif src != "analyser" and not strat and s.get("score_confirm", True):
            # the analyser read as it was when this signal's bar closed (no look-ahead)
            try:
                r = self.hub.read_at(sg["t"] + TF_SECONDS[s["tf"]])
            except Exception as e:
                self.hub.note("error", f"auto trade: read_at failed: {e}", category="SIGNAL_ERROR")
                r = None
            if r is None or r.overall is None:
                return skip("no analyser score to confirm it", "BLOCKED_SCORE")
            if r.bias != side:
                return skip(f"analyser bias {'BULL' if r.bias == 1 else 'BEAR' if r.bias == -1 else 'none'} "
                            f"does not confirm this {word}", "BLOCKED_STRUCTURE")
            if r.overall < r.passing:
                return skip(f"analyser score {r.overall:.0f}% < passing {r.passing:.0f}% (no confirmation)",
                            "BLOCKED_SCORE")
        rg = getattr(self.hub, "regime", None)
        if rg is not None and not route and not strat and (s.get("regimes") or s.get("no_countertrend")):
            try:
                st = rg.state()
            except Exception as e:
                self.hub.note("error", f"auto trade: regime read failed: {e}", category="SIGNAL_ERROR")
                return skip("regime not available (filter on)", "BLOCKED_REGIME")
            if s.get("regimes") and st["label"] not in s["regimes"]:
                return skip(f"regime {st['label']} not in {', '.join(s['regimes'])}", "BLOCKED_REGIME")
            if s.get("no_countertrend") and st["dir"] and st["dir"] != side:
                return skip(f"counter-trend ({st['label']})", "BLOCKED_REGIME")
        n_c = len(sg.get("cautions") or [])
        if s["max_cautions"] >= 0 and n_c > s["max_cautions"]:
            return skip(f"{n_c} caution(s): {'; '.join(sg['cautions'][:2])}", "BLOCKED_CAUTIONS")
        if not strat and s["min_score"] > 0 and sg.get("score") is not None and sg["score"] < s["min_score"]:
            return skip(f"score {sg['score']:.0f}% < {s['min_score']:.0f}%", "BLOCKED_SCORE")
        if s["max_per_day"] and self.count_today >= s["max_per_day"]:
            return skip(f"max {s['max_per_day']} auto trades today reached", "BLOCKED_MAX_TRADES")
        master = strat and s.get("master_mode", True)
        if master:
            # MASTER: each strategy its own trade - only its OWN open trade (or live CONSO grid) blocks it
            own = [g for g in tm.groups.values() if getattr(g, "source", "") == src]
            if own:
                return skip(f"may bukas pang sariling trade ang {src[3:].upper()} ({own[0].gid})", "BLOCKED_REENTRY")
            nt = getattr(self.hub, "newstrader", None)
            live = bool(sg.get("pending") and nt is not None and hasattr(nt, "strategy_box_live")
                        and nt.strategy_box_live())
            if live and sg["t"] <= getattr(self, "_conso_live_t", 0):
                return skip("may naka-pending pang CONSO grid", "BLOCKED_REENTRY")
            replace_grid = live                       # a NEWER box than the live grid's: it replaces it
        else:
            replace_grid = False
        opp = [g for g in tm.groups.values() if g.side == -side]
        if not master and s.get("no_opposite", True) and opp:
            return skip(f"may bukas na {'SELL' if side == 1 else 'BUY'} trade ({len(opp)}): bawal ang kontrang trade",
                        "BLOCKED_OPPOSITE_POSITION")
        same = [g for g in tm.groups.values() if g.side == side]
        if same and not master:
            ok, why = self._reentry_ok(same, side, sg)
            if not ok:
                return skip(why, "BLOCKED_REENTRY")
        if s["max_open"] and len(tm.groups) >= s["max_open"]:
            return skip(f"{len(tm.groups)} SuperChart trade(s) already open (max {s['max_open']})",
                        "BLOCKED_MAX_OPEN_POSITIONS")
        sl_d = abs(sg["entry"] - sg["sl"])
        tp_d = abs(sg["tp"] - sg["entry"])
        if sl_d <= 0:
            return skip("signal has no stop loss", "BLOCKED_INVALID_SL")
        if route:                                  # the router sets the target: RR x the signal's own stop
            tp_d = round(route["rr"] * sl_d, 2)
            sig["tp"] = round(sg["entry"] + side * tp_d, 2)
            tag += f" · router {route['mode']} {route['label']} 1:{route['rr']:g}"
        # ---- the master gate: every remaining gate, in order, for this exact order
        res = gate.evaluate(sig, "auto", float(s["lot"]), waive=waive)
        if not res["allowed"]:
            self._last_res = res["block_code"]
            return self._note("info", f"AUTO skip {tag}: [{res['block_code']}] {res['block_reason']}",
                              decision="skip", side=side, code=res["block_code"], signal_id=sig["signal_id"])
        lot = res["lot"]
        if sg.get("pending"):                      # CONSOLIDATION: BUY STOP + SELL STOP around the box (OCO)
            nt = getattr(self.hub, "newstrader", None)
            if nt is None:
                return skip("pending orders need the News Trader engine (not available)", "BLOCKED_EXECUTION")
            import strategies as _st
            ccfg = _st.load_settings()["conso"]
            # v2.8.7 (Eugene: "walang expiration ang conso"): the grid stays until it is hit (OCO) or cancelled
            exp = 0 if ccfg.get("no_expiry", True) else time.time() + int(ccfg["expiry_bars"]) * TF_SECONDS[s["tf"]]
            if replace_grid and hasattr(nt, "cancel_strategy_boxes"):
                # v2.8.20 (Eugene: "mapapalitan ang lumang conso box"): a NEW CONSO box replaces the old pending
                # grid (no CONSO trade is open here: that case was skipped above)
                k = nt.cancel_strategy_boxes("pinalitan ng bagong CONSO box")
                if k:
                    self._note("warn", f"CONSO: bagong box → binura ang lumang grid ({k} pending order)")
            r = nt.place_box(sg["plan"], float(lot), exp,
                             label=f"CONSO {s['tf']}" + (f" #{sg['event'][6:]}" if str(sg.get("event", "")).startswith("conso_r") else ""))
            if r.get("ok"):
                self._last_res = "trade"
                self._conso_live_t = sg["t"]          # the box whose grid is live now (older boxes are superseded)
                self.count_today += 1
                gate.journal.mark_trade(sig["signal_id"], r.get("eid"), res["setup_id"])
                self._note("signal", f"AUTO CONSO BOX {sg['plan']['box_low']:.2f}-{sg['plan']['box_high']:.2f} → "
                                     f"BUY STOP {sg['plan']['buy_stop']:.2f} / SELL STOP {sg['plan']['sell_stop']:.2f} "
                                     f"· SL ${sl_d:.2f} TP ${tp_d:.2f} · lot {lot} · {r.get('note', '')}",
                           decision="trade", side=0, signal_id=sig["signal_id"])
            else:
                self._note("error", f"AUTO CONSO BOX FAILED: {r.get('error')}", decision="error", code="EXECUTION_ERROR")
            return
        mult = self._align_mult(side) if strat else 1.0
        if strat:                                 # the mode's lot (AGGRESSIVE x2) - never stacked with the align x2
            mult = max(mult, float(s.get("mode_mult", 1.0) or 1.0))
        if mult != 1.0:
            lot = round(float(lot) * mult, 2)
            tag += f" · x{mult:g} lot"
        cap = None
        if src in ("st_trend", "st_pullback"):    # the strategy's own risk % (0 = off), x the aligned lot
            import strategies as _st
            cap = float(_st.load_settings().get(src[3:], {}).get("max_risk_pct", 0.0)) * mult
        elif src in ("tl_break", "tl_retest"):
            cap = 0.0                             # TL BREAK: no % block, its SL is capped at tl_max_sl ($20)
        r = tm.open(side, float(lot), sl_d, tp_d, kind="auto",
                    note=f"{src} signal, SL ${sl_d:.2f} TP ${tp_d:.2f}" + (f" · lot {', '.join(res['lot_notes'])}"
                                                                          if res["lot_notes"] else ""),
                    request_id=sig["signal_id"],                   # the same signal never trades twice
                    meta=dict(signal_id=sig["signal_id"], setup_id=res["setup_id"], gate_confidence=res["confidence"],
                              gate_stages=res["stages"], strategy=src if strat else None,
                              lot_mult=mult if mult != 1.0 else None, risk_pct_cap=cap))
        if r.get("ok"):
            self._last_res = "trade"
            self.count_today += 1
            gate.journal.mark_trade(sig["signal_id"], r.get("gid"), res["setup_id"])
            self._note("signal", f"AUTO {tag} → sent · SL ${sl_d:.2f} TP ${tp_d:.2f} · lot {lot} · "
                                 f"{len(r.get('results', []))} account(s) · {self.count_today}/{s['max_per_day']} today",
                       decision="trade", side=side, gid=r.get("gid"), signal_id=sig["signal_id"])
        else:
            self._note("error", f"AUTO {tag} FAILED: {r.get('error')}", decision="error", side=side,
                       code="EXECUTION_ERROR")

    def _route(self, src, side):
        """REGIME ROUTER -> (dict(mode, label, dir, rr), "") or (None, why).
        TREND labels: trend sources only, in the trend direction, RR router_trend_rr.
        RANGE labels: reversal arrows only (both directions), RR router_range_rr.
        Any other regime (VOLATILE, QUIET, TRANSITION, unknown): no trade."""
        s = self.settings
        rg = getattr(self.hub, "regime", None)
        if rg is None:
            return None, "router ON but the regime is not available"
        try:
            st = rg.state()
        except Exception as e:
            return None, f"router: regime read failed ({e.__class__.__name__})"
        label, d = st.get("label"), st.get("dir") or 0
        if label in s.get("router_trend_labels", []):
            if src == "reversal":
                return None, f"router {label}: trend signals lang (reversal arrows sa RANGE lang)"
            if d and side != d:
                return None, f"router {label}: {'SELL' if side == -1 else 'BUY'} is against the trend"
            return dict(mode="TREND", label=label, dir=d, rr=float(s.get("router_trend_rr", 2.0))), ""
        if label in s.get("router_range_labels", []):
            if src != "reversal":
                return None, f"router {label}: reversal arrows lang (walang trend)"
            return dict(mode="RANGE", label=label, dir=0, rr=float(s.get("router_range_rr", 1.0))), ""
        return None, f"router: regime {label} -> walang trade (hindi trend, hindi range)"

    def _reentry_ok(self, same, side, sg):
        """Re-entry in the same direction while a trade is still open: wait + fresh confirmation."""
        s, tm = self.settings, self.tm
        sec = TF_SECONDS[s["tf"]]
        now_srv = sg["t"] + sec                                       # this signal's bar close (server time)
        last_open = 0
        for g in same:
            e = tm.journal.entries.get(g.gid) or {}
            last_open = max(last_open, e.get("t_srv_open") or int(g.created + (self.hub.utc_offset or 0) * 3600))
        wait = s.get("reentry_minutes", 30) * 60
        mins = max(0.0, (now_srv - last_open) / 60)
        if wait and now_srv - last_open < wait:
            return False, (f"re-entry: {mins:.0f} min pa lang mula sa huling {'BUY' if side == 1 else 'SELL'} "
                           f"(kailangan {wait // 60} min)")
        try:
            r = self.hub.read_at(now_srv)
        except Exception:
            r = self.hub.read
        if r is None or r.bias != side:
            return False, "re-entry: wala na sa trend (HTF bias hindi na kasabay)"
        if r.overall is None or r.overall < r.passing:
            return False, f"re-entry: score {0 if r.overall is None else r.overall:.0f}% < passing {r.passing:.0f}%"
        rg = getattr(self.hub, "regime", None)
        if rg is not None:
            try:
                st = rg.state()
                if st["dir"] == -side:
                    return False, f"re-entry: kontra ang regime ({st['label']})"
            except Exception as e:
                report("SIGNAL_ERROR", "autotrade", "REGIME_UNAVAILABLE", e)
                return False, "re-entry: regime not available (fail safe: no re-entry)"
        return True, ""

    # ------------------------------------------------------------------ view
    def diagnose(self):
        """'Bakit wala pang trade?' - every condition AUTO TRADE needs RIGHT NOW, in order, with ok / not ok, plus the
        last signal of every source on the AUTO timeframe and the last skip reason. Read-only, cached 10 s."""
        now = time.time()
        if getattr(self, "_diag", None) and now - self._diag[0] < 10:
            return self._diag[1]
        s, tm, hub = self.settings, self.tm, self.hub
        checks = []

        def add(name, ok, text):
            checks.append(dict(name=name, ok=None if ok is None else bool(ok), text=text))

        add("Trading", tm.armed, "ON" if tm.armed else "OFF (i-ON ang AUTO: kasama na ang pag-ON ng trading)")
        add("AUTO", self.enabled, "ON" if self.enabled else "OFF")
        risk = getattr(hub, "risk", None)
        if risk is not None:
            add("MASTER", risk.master_enabled, "MASTER TRADING enabled" if risk.master_enabled else "MASTER: DISABLED")
        ok, sess = self._session_ok()
        add("Session", ok, sess or "ok")
        nf = getattr(hub, "news", None)
        if s.get("news_block") and nf is not None and hasattr(nf, "news_state"):
            try:
                ns = nf.news_state()
                add("News", ns["state"] == "NEWS_CLEAR" or (ns["state"] == "NEWS_UNKNOWN" and risk is not None
                                                          and risk.settings.get("news_unknown") == "allow"),
                    f"{ns['state']}: {ns['text']}")
            except Exception as e:
                add("News", None, f"n/a ({e.__class__.__name__})")
        add("Trades today", not s["max_per_day"] or self.count_today < s["max_per_day"],
            f"{self.count_today} / {s['max_per_day'] or '∞'}")
        add("Open trades", not s["max_open"] or len(tm.groups) < s["max_open"], f"{len(tm.groups)} / {s['max_open'] or '∞'}")
        r = getattr(hub, "read", None)
        thr = None
        if s.get("strategies_only", True):
            # per strategy: the strategy's own conditions (100%) decide, not the analyser score
            try:
                import strategies as _st
                cfg = _st.load_settings()
                on = [_st.TITLE[n] for n in _st.NAMES if cfg[n].get("auto")]
                add("Strategies", bool(on), ("naka-AUTO: " + ", ".join(on)) if on else
                    "walang strategy na naka-AUTO: i-click ang MANUAL sa tabi ng strategy (sa kanan ng chart)")
            except Exception as e:
                add("Strategies", None, f"n/a ({e.__class__.__name__})")
            r = None
        if r is not None and r.overall is not None:
            thr = s["min_score"] if s.get("min_score") else r.passing
            add("HTF bias", bool(r.bias), {1: "BULL", -1: "BEAR"}.get(r.bias, "walang bias (H4/H1 hindi magkasundo)"))
            add("Trigger", r.trigger, "may M5/M15 CHoCH/BOS kasabay ng bias" if r.trigger else
                "wala pang M5/M15 CHoCH/BOS sa direksyon ng bias")
            add("Score", r.overall >= thr, f"{r.overall:.0f}% · kailangan {thr:.0f}%"
                + (" (AUTO min score)" if s.get("min_score") else " (passing)"))
        ap = next((x for x in getattr(hub, "plugins", []) if x.key == "analyser_signals.AnalyserSignals"), None)
        if ap is not None and "analyser" in s["sources"] and not s.get("strategies_only", True):
            arrow_thr = float(s.get("min_score") or 0) or float(ap.obj.params.get("min_score") or 0) or \
                (r.passing if r is not None else None)
            if not ap.enabled:
                add("Analyser arrows", False, "naka-OFF ang Analyser Signals sa LAYERS: walang arrow = walang AUTO")
            elif arrow_thr is not None:
                src_txt = "AUTO min score" if s.get("min_score") else "⚙ ng Analyser Signals / passing"
                add("Analyser arrows", r is None or r.overall is None or r.overall >= arrow_thr,
                    f"arrow para sa AUTO: bias + trigger + score ≥ {arrow_thr:.0f}% ({src_txt})")
        if s.get("router") and not s.get("strategies_only", True):
            rg = getattr(hub, "regime", None)
            try:
                st = rg.state() if rg else None
                lab = st["label"] if st else "n/a"
                mode = "TREND" if lab in s.get("router_trend_labels", []) else \
                    "RANGE" if lab in s.get("router_range_labels", []) else None
                add("Router", mode is not None, f"regime {lab} → " + (
                    "trend signals, 1:" + str(s.get("router_trend_rr")) if mode == "TREND" else
                    "reversal arrows, 1:" + str(s.get("router_range_rr")) if mode == "RANGE" else "walang trade"))
            except Exception as e:
                add("Router", None, f"n/a ({e.__class__.__name__})")
        last = []
        try:
            bars = self.hub.src.bars(s["tf"])
            if len(bars) >= 30:
                for src, p in self._plugins():
                    sg = self._signals(src, p, bars, s["tf"])
                    g = max(sg, key=lambda x: x["t"]) if sg else None
                    last.append(dict(source=src, t=g["t"] if g else None, side=g["side"] if g else None,
                                     score=g.get("score") if g else None,
                                     bars_ago=int((bars[-1].t - g["t"]) // TF_SECONDS[s["tf"]]) if g else None))
        except Exception as e:
            last.append(dict(source="error", t=None, side=None, score=None, bars_ago=None, error=str(e)))
        skip = next((x for x in self.log if x.get("decision") == "skip"), None)
        blocking = [c for c in checks if c["ok"] is False]
        out = dict(t=int(now), tf=s["tf"], checks=checks, last_signals=last, last_skip=skip,
                   ready=not blocking, threshold=thr,
                   summary=("Handa: papasok sa susunod na signal na pasado sa lahat" if not blocking else
                            "Hinihintay: " + ", ".join(c["name"] for c in blocking)))
        self._diag = (now, out)
        return out

    def state(self):
        ok, sess = self._session_ok()
        try:
            diag = self.diagnose()
        except Exception as e:
            diag = dict(error=f"{e.__class__.__name__}: {e}")
        return dict(enabled=self.enabled, armed=self.tm.armed, settings=self.settings, today=self.count_today,
                    session_ok=ok, session=sess, log=list(self.log)[:25], diag=diag)

    def stop(self):
        self.running = False
