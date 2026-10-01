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
    tf="M5",
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
    sessions=["London", "New York"],
    news_block=True,               # no entry inside the news warning window (News settings)
    max_per_day=3,
    max_open=1,                    # max open SuperChart trades at the same time
    max_spread=0.60,               # $; 0 = no limit
    regimes=[],                    # allowed market regimes (regime.py); empty = any
    no_countertrend=False,         # in TREND UP skip SELLs, in TREND DOWN skip BUYs
    score_confirm=True,            # breakout / trend line signals must be confirmed by the analyser score
    no_opposite=True,              # never open a trade against an open one (no BUY + SELL at the same time);
                                   # applies to manual entries too
    reentry_minutes=30,            # an extra trade in the SAME direction (while one is open) only after this many
                                   # minutes since the last entry, and only with a fresh confirmation (still in trend
                                   # and the analyser score still passes). 0 = no waiting
)
import market_hours  # noqa: E402

SESSION_HOURS = market_hours.SESSIONS          # UTC, shared table (market_hours.SESSIONS / session_settings.json)


def load_settings():
    s = json.loads(json.dumps(DEFAULTS))
    try:
        with open(SETTINGS_FILE) as f:
            for k, v in json.load(f).items():
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
        self.done = set()                          # (source, signal time) already handled
        self.last_bar = None
        self.today = None
        self.count_today = 0
        self.running = True
        threading.Thread(target=self._loop, daemon=True).start()

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
                   + (f" · {self.settings['tf']} · {what} · max {self.settings['max_per_day']}/day" if on else ""))
        return dict(ok=True, enabled=self.enabled)

    def update_settings(self, new):
        s = self.settings
        for k in ("lot", "min_score", "max_spread"):
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
        if isinstance(new.get("regimes"), list):
            import regime as _rg
            s["regimes"] = [x for x in new["regimes"] if x in _rg.ALL]
        try:
            with open(SETTINGS_FILE, "w") as f:
                json.dump(s, f, indent=2)
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
            return _st.signals(name, bars, cfg, bias_fn if name != "conso" else None, 60, mtf=_st.hub_mtf(hub))
        with self.hub.lock:
            ctx = self.hub.ctx()
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
                e = _st.evaluate(name, bars, S, i, cfg[name], bias)
                if not e.get("ok") or (name != "conso" and e.get("side") != spell["side"]):
                    continue                           # not (or no longer) all green
                sg = dict(spell, score=e.get("score", spell.get("score")))
                if name == "conso":
                    sg["plan"] = e
                    sg.update(entry=e["buy_stop"], sl=e["buy_sl"], tp=e["buy_tp"])
                else:
                    sg.update(entry=e["entry"], sl=e["sl"], tp=e["tp"])
                self._consider(src, sg)
                gate = getattr(hub, "gate", None)
                sid = None
                if gate is not None:
                    sid = gate.make_signal(src, self.settings["tf"], sg["t"], sg["side"] or 1, sg.get("entry"),
                                           sg.get("sl"), sg.get("tp"), sg.get("score"), sg.get("cautions"),
                                           sg.get("event") or sg.get("kind") or "", src)["signal_id"]
                if sid and gate.journal.seen.get(sid):
                    self.done.add(key)                 # it became a trade: this spell is finished
            except Exception as ex:
                from error_log import where
                self._note("error", f"auto trade: strategy {name}: {where(ex)}")

    def _consider(self, src, sg):
        """SIGNAL -> auto-trade filters -> MASTER GATE (decision, risk, execution, duplicates, AUTO, ARM) -> ORDER.
        Every outcome has a machine-readable code and is written to the signal journal (trade_gate.py)."""
        tm, s = self.tm, self.settings
        side = sg["side"] or 1                     # a pending straddle (side 0) is checked as its BUY STOP leg
        word = "BUY/SELL STOP" if sg.get("pending") else "BUY" if side == 1 else "SELL"
        tag = f"{word} {sg['entry']:.2f} ({src}{', score ' + str(round(sg['score'])) + '%' if sg.get('score') is not None else ''})"
        gate = getattr(self.hub, "gate", None)
        sig = gate.make_signal(src, s["tf"], sg["t"], side, sg.get("entry"), sg.get("sl"), sg.get("tp"),
                               sg.get("score"), sg.get("cautions"), sg.get("event") or sg.get("kind") or "",
                               src) if gate else None

        strat_src = src.startswith("st_")

        def skip(why, code="BLOCKED_SCORE"):
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
        ok, why = self._session_ok()
        if not ok:
            return skip(why, "BLOCKED_SESSION")
        waive = ()
        route = None
        strat = src.startswith("st_")
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
            waive = ("trigger", "score", "min_confidence") + (("htf:range",) if sg.get("pending") or own_dir else ()) \
                + (("htf:own",) if own_dir or sg.get("pending") else ())
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
        opp = [g for g in tm.groups.values() if g.side == -side]
        if s.get("no_opposite", True) and opp:
            return skip(f"may bukas na {'SELL' if side == 1 else 'BUY'} trade ({len(opp)}): bawal ang kontrang trade",
                        "BLOCKED_OPPOSITE_POSITION")
        same = [g for g in tm.groups.values() if g.side == side]
        if same:
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
            return self._note("info", f"AUTO skip {tag}: [{res['block_code']}] {res['block_reason']}",
                              decision="skip", side=side, code=res["block_code"], signal_id=sig["signal_id"])
        lot = res["lot"]
        if sg.get("pending"):                      # CONSOLIDATION: BUY STOP + SELL STOP around the box (OCO)
            nt = getattr(self.hub, "newstrader", None)
            if nt is None:
                return skip("pending orders need the News Trader engine (not available)", "BLOCKED_EXECUTION")
            import strategies as _st
            ccfg = _st.load_settings()["conso"]
            r = nt.place_box(sg["plan"], float(lot), time.time() + int(ccfg["expiry_bars"]) * TF_SECONDS[s["tf"]],
                             label=f"CONSO {s['tf']}")
            if r.get("ok"):
                self.count_today += 1
                gate.journal.mark_trade(sig["signal_id"], r.get("eid"), res["setup_id"])
                self._note("signal", f"AUTO CONSO BOX {sg['plan']['box_low']:.2f}-{sg['plan']['box_high']:.2f} → "
                                     f"BUY STOP {sg['plan']['buy_stop']:.2f} / SELL STOP {sg['plan']['sell_stop']:.2f} "
                                     f"· SL ${sl_d:.2f} TP ${tp_d:.2f} · lot {lot} · {r.get('note', '')}",
                           decision="trade", side=0, signal_id=sig["signal_id"])
            else:
                self._note("error", f"AUTO CONSO BOX FAILED: {r.get('error')}", decision="error", code="EXECUTION_ERROR")
            return
        r = tm.open(side, float(lot), sl_d, tp_d, kind="auto",
                    note=f"{src} signal, SL ${sl_d:.2f} TP ${tp_d:.2f}" + (f" · lot {', '.join(res['lot_notes'])}"
                                                                          if res["lot_notes"] else ""),
                    request_id=sig["signal_id"],                   # the same signal never trades twice
                    meta=dict(signal_id=sig["signal_id"], setup_id=res["setup_id"], gate_confidence=res["confidence"],
                              gate_stages=res["stages"]))
        if r.get("ok"):
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
