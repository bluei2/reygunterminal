"""
REYGUN SUPERCHART - desktop dashboard with TradingView-style chart.
Same data sources, analyser and plugins as REYGUN TERM. Orders only on an explicit click (ARM + BUY/SELL + confirm).

Run:
  python superchart.py --mode live --symbol XAUUSD_ph
  python superchart.py --mode demo
  python superchart.py --mode replay --file recordings\\XAUUSD_ph_....csv --speed 20

Opens its own app window (Microsoft Edge / Chrome app mode). The server only listens on
127.0.0.1, so nothing is reachable from outside the PC/VPS.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
import bisect
import hashlib
from collections import OrderedDict, deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from engine import TF_SECONDS, analyze, estimate_utc_offset, market_read
from plugin_api import Context, Overlay
import data_validation
import smc_engine
import trade_gate
from error_log import ERRORS, attach_file as attach_error_file
from plugin_loader import VOTE_TFS, load_plugins, load_scoring, save_scoring, save_state, short_tb
from sources import DemoSource, MT5Source, Recorder, ReplaySource

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(HERE, "web")
VERSION = "2.8.49"        # REYGUN SuperChart version (see CHANGELOG.md)
BUILD = "2026-10-09.49"          # must match BUILD in web/app.js; the UI warns when the running server is older
STRUCT_TFS = ["H4", "H1", "M15", "M5"]
HIST_WINDOW = 600          # bars per TF the history / backtest analyser sees (live MT5 loads ~500)
CHART_TFS = ["M1", "M5", "M15", "M30", "H1", "H4", "D1"]
SPEEDS = [1, 2, 5, 10, 20, 50, 100, 250, 500]
HEX = re.compile(r"#[0-9a-fA-F]{6}")


# --------------------------------------------------------------------------
def web_color(style: str, default="#e8e8ff") -> str:
    """Take a terminal style string ('bold black on #55ff55') and return a usable hex color."""
    if not style:
        return default
    if " on " in style:
        m = HEX.search(style.split(" on ", 1)[1])
        if m:
            return m.group(0)
    m = HEX.search(style)
    return m.group(0) if m else default


def zone_color(hex_color: str) -> str:
    """Terminal zone backgrounds are very dark; brighten and make translucent for the web chart."""
    h = web_color(hex_color, "#335533").lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    mx = max(r, g, b, 1)
    if mx < 0xB0:
        k = 0xD0 / mx
        r, g, b = (min(255, int(c * k)) for c in (r, g, b))
    return f"rgba({r},{g},{b},0.16)"


DEFAULT_SESSIONS = [("ASIA", 0, 8, "#60a5fa"), ("LONDON", 7, 16, "#fbbf24"), ("NEW YORK", 12, 21, "#f472b6"),
                    ("FRANKFURT", 6, 14, "#34d399")]


def session_table(plugins):
    """Use the session hours from plugins/sessions.py (incl. edited settings) so header and chart agree."""
    for p in plugins:
        if hasattr(p.obj, "table"):
            try:
                return p.obj.table()
            except Exception:
                pass
        mod = sys.modules.get(type(p.obj).__module__)
        tbl = getattr(mod, "SESSIONS", None)
        if tbl:
            return tbl
    return DEFAULT_SESSIONS


from market_hours import session_name  # noqa: E402  (moved to market_hours.py, re-exported here)


def fmt_t(sec: int) -> str:
    return datetime.fromtimestamp(sec, tz=timezone.utc).strftime("%m-%d %H:%M")


# --------------------------------------------------------------------------
class PluginContractError(Exception):
    """A plugin returned something outside the plugin output schema (PLUGIN_MALFORMED)."""


def _check_vote(p, v):
    d = getattr(v, "direction", None)
    if isinstance(d, bool) or not isinstance(d, (int, float)) or d not in (-1, 0, 1):
        raise PluginContractError(f"PLUGIN_MALFORMED: vote direction {d!r} (must be -1, 0 or +1)")
    reason = getattr(v, "reason", "")
    if not isinstance(reason, str):
        raise PluginContractError(f"PLUGIN_MALFORMED: vote reason must be text, got {type(reason).__name__}")
    name = getattr(v, "name", "") or p.name
    return (str(name), int(d), reason)


def vote_items(p, res):
    """vote() may return one Vote, a list of Votes (one score item each), or None. Anything else is rejected
    (PluginContractError): a malformed vote is never turned into a neutral one."""
    if res is None:
        return []
    cat = getattr(p.obj, "category", "Other")
    if isinstance(res, (list, tuple)):
        out = []
        for v in res:
            if v is None:
                continue
            n, d, r = _check_vote(p, v)
            out.append((n, cat, d, r))
        return out
    n, d, r = _check_vote(p, res)
    return [(n, cat, d, r)]


def vote_on_tfs(p, ctx, tfs):
    """The indicator's vote on each of `tfs` (its chart timeframes), combined into ONE item per vote name:
    majority direction (a tie = 0). One indicator = one vote, however many timeframes it is shown on."""
    per = []
    with p.vote_lock:                       # vote_tf is switched temporarily: never two threads at once
        orig = getattr(p.obj, "vote_tf", "M15")
        try:
            for tf in tfs:
                p.obj.vote_tf = tf
                per.append((tf, vote_items(p, p.obj.vote(ctx))))
        finally:
            p.obj.vote_tf = orig
    if len(per) == 1:
        return per[0][1]
    order, acc = [], {}
    for tf, items in per:
        for name, cat, d, reason in items:
            if name not in acc:
                order.append(name)
                acc[name] = [cat, [], []]
            acc[name][1].append(d)
            acc[name][2].append(f"{tf} {'↑' if d == 1 else '↓' if d == -1 else '·'}")
    out = []
    for name in order:
        cat, dirs, txt = acc[name]
        s = sum(dirs)
        out.append((name, cat, 1 if s > 0 else -1 if s < 0 else 0, " · ".join(txt)))
    return out


PLUGIN_SLOW_S = {"vote": 1.0, "compute": 3.0, "evaluate": 1.0, "status": 1.0}
PLUGIN_STRIKES = 3


def plugin_timer(hub, p, what, t0):
    """Plugin timeout contract: a call slower than PLUGIN_SLOW_S[what] is a strike; PLUGIN_STRIKES consecutive
    strikes mark the plugin UNAVAILABLE (PLUGIN_TIMEOUT). Python cannot interrupt a running call, so a plugin that
    never returns still blocks its caller - the strike rule catches the slow ones."""
    dt = time.perf_counter() - t0
    if dt > PLUGIN_SLOW_S.get(what, 1.0):
        p.strikes = getattr(p, "strikes", 0) + 1
        if p.strikes >= PLUGIN_STRIKES:
            hub._plugin_failed(p, TimeoutError(f"PLUGIN_TIMEOUT: {what}() took {dt:.1f}s ({p.strikes}x)"),
                               code="PLUGIN_TIMEOUT")
    else:
        p.strikes = 0


class Hub:
    """Owns the data source. One lock serialises every MT5 / plugin call."""

    def __init__(self, src, record=False, utc_offset=None):
        self.src = src
        self.trader = None
        self.news = None
        self.newstrader = None
        self.news_results = None
        self.intermarket = None
        self.orderflow = None
        self.autotrader = None
        self.backtest = None
        self.notifier = None
        self.alerts = None
        self.watchdog = None
        self.regime = None
        self.drift = None
        self.exitguard = None
        self.confirmations = None
        self.risk = None
        self.decision = None
        self.execution = None
        self.reconciler = None
        self.gate = None
        self.positions_mgr = None
        self.forecast = None
        self.ai = None
        self.fixed_offset = utc_offset is not None
        self.utc_offset = utc_offset or 0
        self.lock = threading.RLock()
        self.plugins, load_errors = load_plugins()
        self.scoring = load_scoring()
        self.states = {}
        self.read = None
        self.events = deque(maxlen=400)       # dicts, newest last
        self.signals = deque(maxlen=200)
        self.tape = deque(maxlen=80)
        self.seen_events: set = set()
        self.seen_signals: set = set()
        self.last_signal = {}
        self.first = True
        self.last_tick = None
        self.tick_times = deque(maxlen=5000)
        self.total_ticks = 0
        self.recorder = None
        self.started = time.time()
        self.last_step = time.time()
        self.last_tick_wall = 0.0             # wall time of the last received tick (watchdog)
        self.spreads = deque(maxlen=300)      # recent spreads (data validation)
        self.smc_cfg = smc_engine.load_settings()
        self.smc = None
        self.data_status = dict(ok=False, issues=[dict(code="NO_TICK", text="starting", tf=None)], t=0)
        self.running = True
        self._last_analysis = 0.0
        self.speed_i = SPEEDS.index(int(src.speed)) if int(src.speed) in SPEEDS else 0
        self.note("info", f"source: {src.name} {src.symbol}  {src.status}")
        self.note("info", f"plugins: {len(self.plugins)} loaded, {sum(p.enabled for p in self.plugins)} on")
        for e in load_errors:
            self.note("error", f"plugin load error: {e}")
        if record:
            self.toggle_record()

    # ------------------------------------------------------------ logging
    def note(self, kind, text, **extra):
        self.events.append(dict(kind=kind, text=text, wall=time.time(), **extra))
        if kind == "error":                         # every error also goes to the categorized error log
            try:
                ERRORS.from_note(text, **{k: v for k, v in extra.items() if isinstance(v, (str, int, float))})
            except Exception as e:                  # the log itself must never break the caller
                print(f"error log failed: {e}")

    # ------------------------------------------------------------ loop
    def run(self):
        while self.running:
            t0 = time.perf_counter()
            self.last_step = time.time()
            try:
                self.step()
            except Exception as e:
                from error_log import where
                self.note("error", f"loop error: {where(e)}")
                traceback.print_exc()
            time.sleep(max(0.05, 0.25 - (time.perf_counter() - t0)))

    def step(self):
        with self.lock:
            ticks = self.src.poll()
            if ticks:
                if self.recorder:
                    self.recorder.write(ticks)
                self.total_ticks += len(ticks)
                now = time.monotonic()
                self.tick_times.extend([now] * min(len(ticks), 500))
                prev = self.last_tick.bid if self.last_tick else None
                for tk in ticks[-40:]:
                    d = 0 if prev is None or tk.bid == prev else (1 if tk.bid > prev else -1)
                    prev = tk.bid
                    self.tape.append(dict(t=tk.t_msc, bid=tk.bid, ask=tk.ask, d=d))
                self.last_tick = ticks[-1]
                self.last_tick_wall = time.time()
                for tk in ticks[-20:]:
                    if tk.ask >= tk.bid > 0:
                        self.spreads.append(tk.ask - tk.bid)
                if self.orderflow is not None:
                    self.orderflow.add(ticks)
                if self.src.live and not self.fixed_offset:
                    new = estimate_utc_offset(self.last_tick.t_msc, time.time(), self.utc_offset)
                    if new != self.utc_offset:
                        self.utc_offset = new
                        self.note("info", f"broker server time = UTC{new:+d}")
            fast = ticks and not self.src.live and self.src.speed >= 20
            if time.time() - self._last_analysis >= 1.0 or fast:
                self._last_analysis = time.time()
                self.analyze()

    def ctx(self):
        """What a plugin may READ. No trader, account, gate or hub reference: a plugin cannot reach order code
        through its context. bars() hands out copies of the bar lists."""
        src = self.src
        return Context(src.symbol, lambda tf: list(src.bars(tf)), self.last_tick, self.states, self.read,
                       self.utc_offset, history=self.read_at)

    # ------------------------------------------------------------------ analyser history (no look-ahead)
    def _score_sig(self):
        """Everything that changes the score: scoring settings + which indicators score, with their params."""
        parts = [json.dumps(self.scoring, sort_keys=True)]
        for p in self.plugins:
            if p.kind == "indicator" and p.scoring and not p.error:
                parts.append(f"{p.key}|{getattr(p.obj, 'vote_tf', '')}|{','.join(p.chart_tfs)}|"
                             f"{json.dumps(p.obj.params, sort_keys=True)}")
        return hashlib.md5("\n".join(parts).encode()).hexdigest()

    def read_at(self, t_end):
        """The analyser Read as of time t_end, built only from bars that had CLOSED by t_end (a bar counts
        when bar.t + tf seconds <= t_end). Results are cached per (score settings, t_end)."""
        if not hasattr(self, "_hist"):
            self._hist = OrderedDict()           # (sig, t_end) -> Read
            self._hist_an = OrderedDict()        # (tf, last bar t) -> TFState
            self._hist_vote = OrderedDict()      # (plugin key, tf, last bar t, sig) -> vote tuple / None
        sig = self._score_sig()
        key = (sig, int(t_end))
        r = self._hist.get(key)
        if r is not None:
            return r
        cut = {}

        tc = self.__dict__.setdefault("_times_cache", {})

        def bars_at(tf):
            if tf in cut:
                return cut[tf]
            full = self.src.bars(tf)
            sec = TF_SECONDS[tf]
            key = (id(full), len(full), full[-1].t if full else 0)
            times = tc.get(tf)
            if times is None or times[0] != key:
                times = tc[tf] = (key, [b.t for b in full])
            k = bisect.bisect_right(times[1], t_end - sec)       # bars with b.t <= t_end - sec are closed
            cut[tf] = full[max(0, k - HIST_WINDOW):k]             # same depth the live analyser sees
            return cut[tf]

        states = {}
        for tf in STRUCT_TFS:
            b = bars_at(tf)
            ak = (tf, b[-1].t if b else 0, len(b) > 0)
            st = self._hist_an.get(ak)
            if st is None:
                st = analyze(tf, b, n=3 if tf in ("H4", "H1") else 2)
                self._hist_an[ak] = st
                if len(self._hist_an) > 4000:
                    self._hist_an.popitem(last=False)
            states[tf] = st
        m1 = []
        for ftf in ("M1", "M5", "M15", "H1"):        # finest TF that still has bars up to t_end (M1 history is short)
            fb = bars_at(ftf)
            if fb and fb[-1].t + 2 * TF_SECONDS[ftf] >= t_end:
                m1 = fb
                break
        ctx = Context(self.src.symbol, bars_at, None, states, None, self.utc_offset)
        votes = []
        for p in self.plugins:
            if not p.votes or not p.scoring or p.error:
                continue
            vtfs = p.score_tfs()
            vk = (p.key, tuple(vtfs), tuple((bars_at(t)[-1].t if bars_at(t) else 0) if t in TF_SECONDS else 0
                                            for t in vtfs), sig)
            if vk in self._hist_vote:
                v = self._hist_vote[vk]
            else:
                try:
                    v = vote_on_tfs(p, ctx, vtfs)
                except Exception as e:
                    v = []                          # excluded from this historical read (never a fake neutral)
                    p.hist_errors = getattr(p, "hist_errors", 0) + 1
                    if p.hist_errors == 1:
                        self.note("error", f"plugin '{p.name}' failed on history (vote excluded): "
                                           f"{e.__class__.__name__}: {e}", category="PLUGIN_ERROR")
                self._hist_vote[vk] = v
                if len(self._hist_vote) > 20000:
                    self._hist_vote.popitem(last=False)
            votes.extend(v or [])
        r = market_read(states, bars_at("D1"), m1, self.modules(), votes, self.scoring, self.module_tfs())
        r.tf_states = states                     # read-only reference for the forecast layer (score untouched)
        self._hist[key] = r
        if len(self._hist) > 6000:
            self._hist.popitem(last=False)
        return r

    def validate_data(self):
        """data_validation.validate for the current feed (symbol, tick, bars). Stored in self.data_status."""
        tm = self.trader
        try:
            res = data_validation.validate(self.src, self.last_tick, tm.master.symbol if tm else None,
                                           list(self.spreads)[-100:])
        except Exception as e:
            res = dict(ok=False, issues=[dict(code="DATA_CHECK_FAILED", text=f"{e.__class__.__name__}: {e}", tf=None)])
            ERRORS.add("DATA_ERROR", "data_validation", "DATA_CHECK_FAILED", str(e))
        prev = self.data_status
        res["t"] = time.time()
        self.data_status = res
        if prev.get("t") and bool(prev.get("ok")) != bool(res["ok"]):
            if res["ok"]:
                self.note("info", "data validation: OK again")
            else:
                self.note("warn", "data validation: " + "; ".join(i["text"] for i in res["issues"][:3]))
        return res

    # ------------------------------------------------------------------ SMC structural engine (read-only)
    def _smc_bars(self, bars_fn):
        c = self.smc_cfg
        return {tf: bars_fn(tf) for tf in {c["tf"], c["htf"], "D1"}}

    def update_smc(self):
        """Live SMC view from CLOSED bars only (the forming bar is dropped), same function as smc_at()."""
        try:
            self.smc = smc_engine.analyze(self._smc_bars(lambda tf: self.src.bars(tf)[:-1]), self.smc_cfg,
                                          self.utc_offset)
        except Exception as e:
            self.smc = dict(ok=False, reason=f"{e.__class__.__name__}: {e}", setup=None, setups={})
            self.note("error", f"SMC engine: {e.__class__.__name__}: {e}", category="DECISION_ERROR")
        return self.smc

    def smc_at(self, t_end):
        """The SMC view as it was at t_end (only bars closed by then). Used by backtests / history."""
        cut = {}
        for tf in {self.smc_cfg["tf"], self.smc_cfg["htf"], "D1"}:
            full = self.src.bars(tf)
            sec = TF_SECONDS[tf]
            k = bisect.bisect_right([b.t for b in full], t_end - sec)
            cut[tf] = full[max(0, k - 400):k]
        return smc_engine.analyze(cut, self.smc_cfg, self.utc_offset)

    def set_smc_settings(self, new):
        self.smc_cfg = smc_engine.save_settings(new)
        self.update_smc()
        return self.smc_cfg

    def analyze(self):
        self.validate_data()
        for tf in STRUCT_TFS:
            self.states[tf] = analyze(tf, self.src.bars(tf), n=3 if tf in ("H4", "H1") else 2)
        self.update_smc()
        self.read = market_read(self.states, self.src.bars("D1"), self.src.bars("M1"), self.modules(),
                                self.collect_votes(), self.scoring, self.module_tfs())
        self.read.tf_states = self.states
        self.live_cautions = self._live_cautions()
        self._events()
        self._strategies()

    def _live_cautions(self):
        """Reversal-risk warnings for the current bias on M5 and M15 (see caution.py)."""
        r = self.read
        if not r or not r.bias:
            return []
        import caution
        out = []
        for tf in ("M5", "M15"):
            bars = self.src.bars(tf)
            if len(bars) < 60:
                continue
            try:
                for w in caution.cautions(bars, len(bars) - 1, r.bias):
                    out.append(f"{tf}: {w}")
            except Exception as e:
                out.append(f"{tf}: caution check failed ({e.__class__.__name__})")
        return out

    def collect_votes(self):
        """Votes from every enabled indicator that implements vote()."""
        out = []
        by_plugin = {}                              # plugin key -> its vote items (read-only, for the companion view)
        ctx = self.ctx()
        for p in self.plugins:
            if not p.votes or not p.scoring or p.error:
                continue
            t0 = time.perf_counter()
            try:
                items = vote_on_tfs(p, ctx, p.score_tfs())
            except PluginContractError as e:
                self._plugin_failed(p, e, code="PLUGIN_MALFORMED")
                continue
            except Exception as e:
                self._plugin_failed(p, e)
                continue
            plugin_timer(self, p, "vote", t0)
            out.extend(items)
            by_plugin[p.key] = [dict(name=n, dir=d, reason=r) for n, _c, d, r in items]
        self.votes_by_plugin = by_plugin
        return out

    def module_tfs(self) -> dict:
        """Analyser modules (premium/discount, liquidity) follow their indicator's chart timeframes too.
        Only structure timeframes (H4 H1 M15 M5) have the data these checks need; others are ignored."""
        out = {}
        for p in self.plugins:
            m = getattr(p.obj, "analyser_module", None) if p.kind == "indicator" else None
            if m and p.scoring and not p.error and p.chart_tfs:
                tfs = [t for t in p.chart_tfs if t in STRUCT_TFS]
                if tfs:
                    out[m] = tfs
        return out

    def modules(self) -> set:
        """Analyser modules switched on by enabled indicator plugins."""
        return {p.obj.analyser_module for p in self.plugins
                if p.kind == "indicator" and p.scoring and not p.error and getattr(p.obj, "analyser_module", None)}

    def _events(self):
        fresh = []
        for tf in STRUCT_TFS:
            st = self.states.get(tf)
            bars = self.src.bars(tf)
            if not st or not bars:
                continue
            recent_t = bars[-1].t - 3 * TF_SECONDS[tf]
            for e in st.events:
                if e.key in self.seen_events:
                    continue
                self.seen_events.add(e.key)
                if e.t >= recent_t or self.first:
                    fresh.append(e)
        if self.first:
            self.first = False
            for tf in STRUCT_TFS:
                st = self.states.get(tf)
                e = st.last() if st else None
                if e:
                    self._push_event(e, hist=True)
            return
        order = {"H4": 0, "H1": 1, "M15": 2, "M5": 3}
        liq = "liquidity" in self.modules()
        for e in sorted(fresh, key=lambda e: (e.t, order[e.tf])):
            if e.kind in ("SWEEP", "FVG") and not liq:
                continue            # sweeps/FVG only when the liquidity module is on
            if e.kind == "FVG" and e.tf == "M5":
                continue
            self._push_event(e)

    def _push_event(self, e, hist=False):
        detail = e.note if e.kind == "FVG" else f"{e.level:.2f} {e.note}".strip()
        self.note("structure", detail, tf=e.tf, ev=e.kind, dir=e.direction, bar=fmt_t(e.t), hist=hist)

    def _strategies(self):
        ctx = self.ctx()
        for p in self.plugins:
            if p.kind != "strategy" or not p.enabled or p.error:
                continue
            t0 = time.perf_counter()
            try:
                res = p.obj.evaluate(ctx)
            except Exception as e:
                self._plugin_failed(p, e)
                continue
            plugin_timer(self, p, "evaluate", t0)
            if not res:
                continue
            sigs = res if isinstance(res, list) else [res]
            bad = [x for x in sigs if getattr(x, "direction", None) not in (1, -1) or not isinstance(getattr(x, "t", None), int)]
            if bad:
                self._plugin_failed(p, PluginContractError(f"PLUGIN_MALFORMED: signal {bad[0]!r}"), code="PLUGIN_MALFORMED")
                continue
            for sig in sigs:
                key = (p.key, sig.t, sig.direction)
                if key in self.seen_signals:
                    continue
                self.seen_signals.add(key)
                self.last_signal[p.key] = sig
                s = dict(plugin=p.name, dir=sig.direction, reason=sig.reason, tf=sig.tf, bar=fmt_t(sig.t),
                         entry=sig.entry, sl=sig.sl, tp=sig.tp, wall=time.time())
                self.signals.append(s)
                self.note("signal", sig.reason, **{k: v for k, v in s.items() if k not in ("reason", "wall")})

    def _plugin_failed(self, p, e, code="PLUGIN_ERROR"):
        """PLUGIN_FAILED: this plugin only is marked UNAVAILABLE (its votes / drawings / signals are excluded, never
        replaced by a neutral vote); the terminal and every other plugin keep running."""
        p.error = short_tb(e)
        p.error_code = code
        self.note("error", f"plugin '{p.name}' {code}, UNAVAILABLE: {p.error}", category="PLUGIN_ERROR",
                  code=code, component=f"plugin:{p.key}")

    # ------------------------------------------------------------ actions
    def set_plugin(self, key, chart=None, score=None, params=None, vote_tf=None, reset=False, chart_tfs=None):
        """Update one plugin: chart on/off, score on/off, parameters, vote timeframe, or reset to defaults."""
        with self.lock:
            for p in self.plugins:
                if p.key != key:
                    continue
                if chart is not None:
                    p.enabled = bool(chart)
                    if chart:
                        p.error = None
                    word = "CHART" if p.kind == "indicator" else "ON"
                    self.note("info", f"{p.name}: {word} {'on' if chart else 'off'}")
                if score is not None and p.can_score:
                    p.scoring = bool(score)
                    if score:
                        p.error = None
                    self.note("info", f"{p.name}: SCORE {'on' if score else 'off'}")
                if reset:
                    p.reset_params()
                    self.note("info", f"{p.name}: settings reset to default")
                if params:
                    applied = p.set_params(params)
                    p.error = None
                    self.note("info", f"{p.name}: " + ", ".join(f"{k}={v}" for k, v in applied.items()))
                if vote_tf in VOTE_TFS:
                    p.obj.vote_tf = vote_tf
                    self.note("info", f"{p.name}: votes on {vote_tf}")
                if chart_tfs is not None and p.kind == "indicator":
                    shown = p.set_chart_tfs(chart_tfs)
                    self.note("info", f"{p.name}: drawn on {', '.join(shown) if shown else 'every timeframe'}")
            save_state(self.plugins)
            self.analyze()

    def reconnect_master(self, path, login, symbol):
        """Point the chart + master trading to another MT5 terminal. Returns an error/warning text or None."""
        if not self.src.live:
            return None
        with self.lock:
            try:
                warn = self.src.reconnect(path, login, symbol)
            except RuntimeError as e:
                self.note("error", f"master reconnect failed: {e}")
                return f"FAILED: {e}"
            self.last_tick = None
            self.states = {}
            self.first = True
            self.note("info", f"master MT5 -> {path or 'default terminal'} ({self.src.status}, {self.src.symbol})")
            if warn:
                self.note("error", warn)
            self.analyze()
            return warn

    def set_scoring(self, sc):
        with self.lock:
            self.scoring = save_scoring(None if (sc or {}).get("reset") else sc)
            w = ", ".join(f"{k} x{v:g}" for k, v in self.scoring["weights"].items() if k != "Other")
            self.note("info", f"scoring: pass {self.scoring['passing']:g}% · {w}")
            self.analyze()
        return dict(ok=True, scoring=self.scoring)

    def toggle_record(self):
        with self.lock:
            if self.recorder:
                path, n = self.recorder.path, self.recorder.count
                self.recorder.close()
                self.recorder = None
                self.note("info", f"recording saved: {path} ({n:,} ticks)")
            else:
                self.recorder = Recorder(self.src.symbol)
                self.note("info", f"recording -> {self.recorder.path}")

    def pause(self):
        with self.lock:
            self.src.paused = not self.src.paused

    def speed(self, direction):
        with self.lock:
            if self.src.live:
                return
            self.speed_i = max(0, min(len(SPEEDS) - 1, self.speed_i + (1 if direction > 0 else -1)))
            self.src.speed = SPEEDS[self.speed_i]

    # ------------------------------------------------------------ views
    def state(self):
        with self.lock:
            src = self.src
            tk = self.last_tick
            now = time.monotonic()
            while self.tick_times and now - self.tick_times[0] > 5:
                self.tick_times.popleft()
            m1 = src.bars("M1")
            chg = None
            if len(m1) >= 2 and m1[-2].c:
                chg = (m1[-1].c - m1[-2].c) / m1[-2].c * 100
            tfs = {}
            for tf, st in self.states.items():
                e = st.last()
                tfs[tf] = dict(
                    trend=st.trend, pd=st.pd, atr=st.atr, bars=st.bars,
                    last=dict(kind=e.kind, dir=e.direction, level=e.level, bar=fmt_t(e.t)) if e else None,
                    sh=dict(level=st.sh.level, broken=st.sh.broken, swept=st.sh.swept) if st.sh else None,
                    sl=dict(level=st.sl.level, broken=st.sl.broken, swept=st.sl.swept) if st.sl else None,
                    fvg_bull=[st.fvg_bull.lo, st.fvg_bull.hi] if st.fvg_bull else None,
                    fvg_bear=[st.fvg_bear.lo, st.fvg_bear.hi] if st.fvg_bear else None,
                )
            r = self.read
            ctx = self.ctx()
            strategies = []
            for p in self.plugins:
                if p.kind != "strategy" or not p.enabled:
                    continue
                status = []
                if not p.error:
                    try:
                        status = p.obj.status(ctx) or []
                    except Exception as e:
                        self._plugin_failed(p, e)
                sig = self.last_signal.get(p.key)
                strategies.append(dict(name=p.name, status=status, error=p.error,
                                       last=dict(dir=sig.direction, bar=fmt_t(sig.t)) if sig else None))
            try:
                acc = src.account()
                pos = src.positions()
            except Exception:
                acc, pos = None, []
            now_utc = datetime.now(timezone.utc)
            return dict(
                version=VERSION, build=BUILD, symbol=src.symbol, digits=src.digits, source=src.name, live=src.live, status=src.status,
                paused=src.paused, speed=src.speed, finished=getattr(src, "finished", False),
                progress=getattr(src, "progress", None),
                tick=dict(bid=tk.bid, ask=tk.ask, t=tk.t_msc) if tk else None,
                change_pct=chg, tps=len(self.tick_times) / 5, poll_ms=src.poll_ms,
                total_ticks=self.total_ticks, uptime=int(time.time() - self.started),
                recording=dict(path=self.recorder.path, count=self.recorder.count) if self.recorder else None,
                session=session_name(now_utc), utc_offset=self.utc_offset,
                trading=None if not self.trader else dict(paper=self.trader.paper, armed=self.trader.armed),
                sessions=[dict(name=n, color=c, start=a, end=b,
                               active=now_utc.weekday() < 5 and (a <= now_utc.hour < b if a < b
                                                                 else (now_utc.hour >= a or now_utc.hour < b)))
                          for n, a, b, c in session_table(self.plugins)],
                read=dict(bias=r.bias, score=r.score, max_score=r.max_score, verdict=r.verdict, lines=r.lines,
                          pdh=r.pdh, pdl=r.pdl, style=r.verdict_style, groups=r.groups, overall=r.overall,
                          passing=r.passing, passed=r.passed, trigger=r.trigger,
                          lost=getattr(r, "lost", []), alt=getattr(r, "alt", {}),
                          cautions=getattr(self, "live_cautions", [])) if r else None,
                scoring=self.scoring,
                plugin_votes=dict(getattr(self, "votes_by_plugin", {}) or {}),
                modules=sorted(self.modules()),
                tfs=tfs, strategies=strategies, account=acc, positions=pos,
                plugins=[dict(key=p.key, name=p.name, kind=p.kind, enabled=p.enabled, error=p.error,
                              status="UNAVAILABLE" if p.error else "OK", error_code=getattr(p, "error_code", None),
                              score=p.scoring, can_score=p.can_score, votes=p.votes,
                              module=getattr(p.obj, "analyser_module", None) if p.kind == "indicator" else None,
                              params=dict(getattr(p.obj, "params", {}) or {}), defaults=p.defaults,
                              vote_tf=getattr(p.obj, "vote_tf", None) if p.votes else None,
                              chart_tfs=list(getattr(p, "chart_tfs", [])),
                              description=getattr(p.obj, "description", ""),
                              purpose=getattr(p.obj, "purpose", "") if p.kind == "indicator" else "alerts only",
                              category=getattr(p.obj, "category", "Other") if p.kind == "indicator" else "Strategy")
                         for p in self.plugins],
                events=list(self.events)[-150:], signals=list(self.signals)[-50:], tape=list(self.tape)[-40:],
            )

    def chart(self, tf, n=600):
        if tf not in CHART_TFS:
            tf = "M15"
        with self.lock:
            all_bars = self.src.bars(tf)
            bars = all_bars[-n:]
            off = len(all_bars) - len(bars)
            out = dict(tf=tf, bars=[[b.t, b.o, b.h, b.l, b.c] for b in bars], lines=[], levels=[],
                       markers=[], zones=[], vlines=[], hists=[], profiles=[], layers=[], signals=[])
            if not bars:
                return out
            first_t = bars[0].t
            times = {b.t for b in bars}
            ctx = self.ctx()
            for p in self.plugins:
                if p.kind != "indicator" or not p.enabled or p.error or not p.shows_on(tf):
                    continue
                t0 = time.perf_counter()
                try:
                    ov = p.obj.compute(list(all_bars), tf, ctx)      # a copy: a plugin cannot reorder our bars
                except Exception as e:
                    self._plugin_failed(p, e)
                    continue
                plugin_timer(self, p, "compute", t0)
                if not ov:
                    continue
                if not isinstance(ov, Overlay):
                    self._plugin_failed(p, PluginContractError(f"PLUGIN_MALFORMED: compute() returned "
                                                               f"{type(ov).__name__}, not Overlay"), code="PLUGIN_MALFORMED")
                    continue
                before = {k: len(v) for k, v in out.items() if isinstance(v, list)}
                out["layers"].append(p.name)
                def align(vals):
                    if len(vals) == len(all_bars):
                        return [b.t for b in bars], vals[off:]
                    vals = vals[-len(bars):]
                    return ([b.t for b in bars[-len(vals):]] if vals else []), vals

                for i, ln in enumerate(ov.lines):
                    tl, vals = align(ln.values)
                    data, started = [], False
                    for t, v in zip(tl, vals):
                        if v is None:
                            if started:
                                data.append([t, None])      # whitespace = visible gap in the line
                            continue
                        started = True
                        data.append([t, round(v, 6)])
                    while data and data[-1][1] is None:
                        data.pop()
                    out["lines"].append(dict(id=f"{p.key}:L{i}", label=ln.label, color=web_color(ln.color), data=data,
                                             pane=getattr(ln, "pane", ""), style=getattr(ln, "style", "line"),
                                             width=getattr(ln, "width", 2),
                                             autoscale=getattr(ln, "autoscale", True),
                                             axis=getattr(ln, "axis", True)))
                for i, hs in enumerate(getattr(ov, "hists", [])):
                    tl, vals = align(hs.values)
                    cols = hs.colors if isinstance(hs.colors, (list, tuple)) else None
                    if cols is not None:
                        _, cols = align(list(cols))
                    one = web_color(hs.colors) if cols is None else None
                    data = [[t, round(v, 6), web_color(cols[k]) if cols else one]
                            for k, (t, v) in enumerate(zip(tl, vals)) if v is not None]
                    out["hists"].append(dict(id=f"{p.key}:H{i}", label=hs.label, pane=hs.pane or "Volume", data=data))
                for pf in getattr(ov, "profiles", []):
                    out["profiles"].append(dict(rows=[[lo, hi, v] for lo, hi, v in pf.rows], color=web_color(pf.color),
                                                highlight=list(pf.highlight), hcolor=web_color(pf.highlight_color),
                                                width=pf.width_pct, t_from=getattr(pf, "t_from", None),
                                                t_to=getattr(pf, "t_to", None),
                                                buy=[round(x, 3) for x in pf.buy] if getattr(pf, "buy", None) else None,
                                                scolor=web_color(getattr(pf, "sell_color", "#ef4444")),
                                                title=getattr(pf, "title", "") or ""))
                for sg in getattr(ov, "signals", []) or []:
                    if sg.get("t", 0) >= first_t:
                        out["signals"].append(dict(sg, source=p.name))
                for lv in ov.levels:
                    out["levels"].append(dict(price=lv.price, color=web_color(lv.color), label=lv.label,
                                              pane=getattr(lv, "pane", "")))
                for m in ov.markers:
                    if m.t not in times:
                        continue
                    ch = (m.char or "•")[:16]
                    shape = "arrowUp" if ch in ("▲", "↑") else "arrowDown" if ch in ("▼", "↓") else \
                        ("arrowUp" if m.pos == "below" else "arrowDown")
                    out["markers"].append(dict(time=m.t, position="aboveBar" if m.pos == "above" else "belowBar",
                                               color=web_color(m.style, "#ffd54a"), shape=shape,
                                               text="" if ch in ("▲", "▼", "↑", "↓") else ch))
                for z in ov.zones:
                    t_to = getattr(z, "t_to", None)
                    if t_to is not None and t_to < first_t:
                        continue
                    border = getattr(z, "border", "")
                    out["zones"].append(dict(lo=z.lo, hi=z.hi, t_from=max(z.t_from, first_t), t_to=t_to,
                                             color=zone_color(z.bg), label=getattr(z, "label", ""),
                                             border=web_color(border) if border else ""))
                for v in getattr(ov, "vlines", []):
                    if v.t >= first_t:
                        out["vlines"].append(dict(t=v.t, color=web_color(v.color, "#94a3b8"), label=v.label,
                                                  dashed=v.dashed))
                # chart object ownership: every object this plugin produced is tagged with its owner; the chart
                # is rebuilt from each plugin's own Overlay, so no plugin can update or delete another's objects
                now_ = int(time.time())
                for k, n0 in before.items():
                    if k == "layers":
                        continue
                    for obj in out[k][n0:]:
                        if isinstance(obj, dict):
                            obj.update(owner=p.key, type=k[:-1] if k.endswith("s") else k, timeframe=tf,
                                       created_at=now_)
                            if k == "signals":
                                obj["signal_id"] = trade_gate.signal_id(self.src.symbol, tf, obj.get("t", 0),
                                                                        obj.get("side", 0), "", p.key)
            nt = getattr(self, "newstrader", None)
            if nt is not None:
                self._news_overlay(nt, out, first_t, tf)
            fs = getattr(self, "forecast", None)
            if fs is not None:                       # FORECAST levels (violet, "FC" labels): virtual, never orders
                try:
                    for lv in fs.overlay():
                        out["levels"].append(dict(lv, owner="forecast", type="forecast", timeframe=tf,
                                                  created_at=int(time.time())))
                except Exception as e:
                    self.note("error", f"forecast overlay: {e}", category="DECISION_ERROR")
            out["markers"].sort(key=lambda m: m["time"])
            return out

    def _news_overlay(self, nt, out, first_t, tf):
        """NEWS TRADE on: show the consolidation box it sees and the pending orders (planned or placed)."""
        box = nt.box
        if box and box.get("ok"):
            sec = TF_SECONDS.get(tf, 60)
            t0 = box["t_from"] - box["t_from"] % sec                 # snap to this chart's bar
            out["zones"].append(dict(lo=box["low"], hi=box["high"], t_from=max(t0, first_t), t_to=None,
                                     color="rgba(167,139,250,0.14)",
                                     label=f"NEWS BOX {box['range']:.2f} · {len(box['tops'])}T/{len(box['bottoms'])}B",
                                     border="#a78bfa"))
        placed = [st for st in nt.events.values() if st["stage"] == "placed"]
        if placed:
            for o in placed[-1]["orders"].get("master", []):
                if not o.get("state"):
                    out["levels"].append(dict(price=o["price"], color="#22c55e" if o["side"] == 1 else "#f43f5e",
                                              label="BS" if o["side"] == 1 else "SS", pane=""))
        elif nt.plan and nt.enabled:
            for L in nt.plan:
                out["levels"].append(dict(price=L["entry"], color="rgba(74,222,128,0.55)" if L["side"] == 1 else "rgba(251,113,133,0.55)",
                                          label="bs?" if L["side"] == 1 else "ss?", pane=""))

    def close(self):
        self.running = False
        if self.trader:
            self.trader.stop()
        with self.lock:
            if self.recorder:
                self.recorder.close()
            self.src.close()


# --------------------------------------------------------------------------
def make_handler(hub: Hub):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def _send(self, code, body: bytes, ctype="application/json"):
            try:
                self.send_response(code)
            except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                return
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            try:
                self.end_headers()
                self.wfile.write(body)
            except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                pass        # the page / window closed the request first (reload, tab switch): harmless, no log spam

        def _json(self, obj, code=200):
            self._send(code, json.dumps(obj, separators=(",", ":")).encode())

        def do_GET(self):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            try:
                if u.path == "/api/state":
                    return self._json(hub.state())
                if u.path == "/api/trading":
                    return self._json(hub.trader.state() if hub.trader else {"disabled": True})
                if u.path == "/api/journal":
                    if not hub.trader:
                        return self._json({"disabled": True})
                    j = hub.trader.journal
                    return self._json(dict(entries=j.list(), stats=j.stats(), series=j.series(), paper=hub.trader.paper))
                if u.path == "/api/journal.csv":
                    body = hub.trader.journal.csv().encode() if hub.trader else b""
                    self.send_response(200)
                    self.send_header("Content-Type", "text/csv; charset=utf-8")
                    self.send_header("Content-Disposition", "attachment; filename=reygun_journal.csv")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if u.path == "/api/newstrade":
                    return self._json(hub.newstrader.state() if hub.newstrader else {"disabled": True})
                if u.path == "/api/backtest":
                    if not hub.backtest:
                        return self._json({"disabled": True})
                    st = hub.backtest.status()
                    if q.get("light"):
                        st["result"] = None
                    st["current_setup"] = hub.backtest.current_setup()
                    return self._json(st)
                if u.path == "/api/auto":
                    return self._json(hub.autotrader.state() if hub.autotrader else {"disabled": True})
                if u.path == "/api/orderflow":
                    if not hub.orderflow:
                        return self._json({"disabled": True})
                    return self._json(hub.orderflow.footprint(q.get("tf", ["M5"])[0] if q.get("tf", ["M5"])[0] in ("M1", "M5", "M15", "M30", "H1") else "M5",
                                                              max(5, min(60, int(q.get("n", ["20"])[0])))))
                if u.path == "/api/intermarket":
                    return self._json(hub.intermarket.view() if hub.intermarket else {"disabled": True})
                if u.path == "/api/news/results":
                    return self._json(hub.news_results.view() if hub.news_results else {"disabled": True})
                if u.path == "/api/news":
                    return self._json(hub.news.view() if hub.news else {"disabled": True})
                if u.path in ("/api/decision", "/api/decision/why", "/api/setup", "/api/tradeplan"):
                    de = hub.decision
                    if de is None:
                        return self._json({"disabled": True})
                    side = int(q.get("side", ["0"])[0] or 0) or None
                    if u.path == "/api/decision":
                        return self._json(de.state())
                    if u.path == "/api/decision/why":
                        return self._json(de.why_not_trade(side, q.get("kind", ["preview"])[0]))
                    if u.path == "/api/setup":
                        from setup_fingerprint import build
                        m = de.setup_matrix(side)
                        return self._json(dict(m, fingerprint=build(m)))
                    lot = float(q.get("lot", ["0.01"])[0] or 0.01)
                    sl = float(q.get("sl", ["0"])[0] or 0)
                    tp = float(q.get("tp", ["0"])[0] or 0)
                    return self._json(trade_plan(hub, side, lot, sl, tp))
                if u.path == "/api/execution":
                    if hub.execution is None:
                        return self._json({"disabled": True})
                    lot = q.get("lot", [None])[0]
                    return self._json(hub.execution.monitor(float(lot) if lot else None,
                                                            float(q.get("sl", ["5"])[0]), float(q.get("tp", ["10"])[0])))
                if u.path.startswith("/api/analytics"):
                    if not hub.trader:
                        return self._json({"disabled": True})
                    import trade_analytics as ta
                    ents = hub.trader.journal.list(10000)
                    part = u.path[len("/api/analytics"):].strip("/")
                    fn = {"": lambda: ta.all_analytics(ents, hub.utc_offset), "mfe": lambda: ta.mfe_mae(ents),
                          "mae": lambda: ta.mfe_mae(ents), "setup": lambda: dict(rows=ta.by_setup(ents)),
                          "session": lambda: dict(rows=ta.by_session(ents, hub.utc_offset)),
                          "regime": lambda: dict(rows=ta.by_regime(ents)), "exit": lambda: dict(rows=ta.by_exit(ents))}
                    if part not in fn:
                        return self._json({"error": "unknown"}, 404)
                    return self._json(fn[part]())
                if u.path == "/api/walkforward":
                    bt = hub.backtest
                    return self._json(dict(result=bt.last_wf if bt else None))
                if u.path == "/api/montecarlo":
                    import monte_carlo
                    src_ = q.get("source", ["backtest"])[0]
                    if src_ == "journal" and hub.trader:
                        rs = [e["r"] for e in hub.trader.journal.list(10000) if e.get("status") == "closed"
                              and e.get("r") is not None]
                    else:
                        res_ = (hub.backtest.result or {}) if hub.backtest else {}
                        rs = ((res_.get("current") or {}).get("r_list")) or []
                    try:
                        return self._json(dict(ok=True, source=src_, **monte_carlo.run(
                            rs, min(5000, int(q.get("n", ["1000"])[0])), float(q.get("slip_r", ["0"])[0]),
                            float(q.get("skip_pct", ["0"])[0]))))
                    except RuntimeError as e:
                        return self._json(dict(ok=False, error=str(e)))
                if u.path == "/api/mt5/detect":
                    # every OPEN MT5 terminal and its account (read-only; probed in separate processes)
                    import mt5_detect
                    sym = q.get("symbol", [None])[0] or getattr(hub.src, "symbol", None)
                    found = mt5_detect.detect(sym)
                    tm = hub.trader
                    cur_m = (tm.cfg["master"].get("path") if tm else "") or ""
                    cur_f = {(f.get("path") or "").lower(): f.get("name") for f in (tm.cfg["followers"] if tm else [])}
                    for f in found:
                        f["role"] = "master" if cur_m and f["path"].lower() == cur_m.lower() else \
                            ("follower: " + cur_f[f["path"].lower()]) if f["path"].lower() in cur_f else ""
                    return self._json(dict(ok=True, symbol=sym, terminals=found,
                                           windows=sys.platform == "win32"))
                if u.path == "/api/strategies":
                    import strategies as stm
                    with hub.lock:
                        return self._json(stm.status(hub, q.get("tf", [None])[0]))
                if u.path == "/api/ai":
                    ai = getattr(hub, "ai", None)
                    return self._json(ai.state() if ai else {"disabled": True})
                if u.path.startswith("/api/forecast"):
                    fs = hub.forecast
                    if fs is None:
                        return self._json({"disabled": True})
                    import forecast as fcm
                    part = u.path[len("/api/forecast"):].strip("/")
                    if part == "":
                        return self._json(fs.state())
                    if part == "history":
                        n = int(q.get("n", ["200"])[0])
                        return self._json(dict(records=list(reversed(fs.engine.journal.all()))[:n]))
                    if part == "stats":
                        return self._json(fcm.statistics(fs.engine.journal.all(), fs.settings["calibration_min_samples"]))
                    if part == "settings":
                        return self._json(dict(settings=fs.settings, defaults=fcm.DEFAULTS))
                    if part == "replay":
                        return self._json(fs.replay_job)
                    if part == "profile":
                        return self._json(fs.profile_view())
                    if part == "chart":
                        tf_ = q.get("tf", ["M15"])[0]
                        return self._json(fs.chart(tf_ if tf_ in CHART_TFS else "M15", int(q.get("n", ["400"])[0])))
                    return self._json({"error": "unknown"}, 404)
                if u.path == "/api/smc":
                    sm = hub.smc or {}
                    return self._json(dict(summary=smc_engine.summary(sm), smc=sm, settings=hub.smc_cfg))
                if u.path in ("/api/gate", "/api/signals/journal"):
                    return self._json(hub.gate.state(int(q.get("n", ["60"])[0])) if hub.gate else {"disabled": True})
                if u.path == "/api/errors":
                    return self._json(ERRORS.view(int(q.get("n", ["100"])[0])))
                if u.path == "/api/positions/manager":
                    return self._json(hub.positions_mgr.state() if hub.positions_mgr else {"disabled": True})
                if u.path == "/api/decision/settings":
                    import setup_quality as sq
                    return self._json(dict(settings=hub.decision.settings if hub.decision else None,
                                           conditions={k: dict(cls=v[0], label=v[1]) for k, v in sq.CONDITIONS.items()},
                                           policies=sq.POLICIES, weights=sq.DEFAULT_WEIGHTS))
                if u.path == "/api/sessions":
                    import market_hours as mh
                    now_utc = datetime.now(timezone.utc)
                    return self._json(dict(sessions=mh.SESSIONS, active=mh.session_code(now_utc),
                                           name=mh.session_name(now_utc), utc_offset=hub.utc_offset))
                if u.path == "/api/reconciliation":
                    return self._json(hub.reconciler.state() if hub.reconciler else {"disabled": True})
                if u.path in ("/api/risk", "/api/risk/accounts"):
                    if hub.risk is None:
                        return self._json({"disabled": True})
                    return self._json(hub.risk.state() if u.path == "/api/risk" else dict(accounts=hub.risk.accounts()))
                if u.path == "/api/exitguard":
                    return self._json(hub.exitguard.state() if hub.exitguard else {"disabled": True})
                if u.path == "/api/regime":
                    return self._json(hub.regime.state() if hub.regime else {"disabled": True})
                if u.path == "/api/system":
                    return self._json(dict(
                        telegram=hub.notifier.state() if hub.notifier else None,
                        health=hub.watchdog.state() if hub.watchdog else None,
                        drift=hub.drift.state() if hub.drift else None,
                        report=hub.alerts.last_report if hub.alerts else None,
                        regimes=__import__("regime").ALL))
                if u.path.startswith("/snap/") or u.path.startswith("/report/"):
                    import snapshot as _snap
                    name = os.path.basename(u.path)
                    data = None
                    if u.path.startswith("/snap/"):
                        data = _snap.load(name)
                    else:
                        try:
                            with open(os.path.join(HERE, "reports", name), "rb") as f:
                                data = f.read()
                        except OSError:
                            pass
                    if not data or not name.endswith(".png"):
                        return self._send(404, b"not found", "text/plain")
                    return self._send(200, data, "image/png")
                if u.path == "/api/chart":
                    return self._json(hub.chart(q.get("tf", ["M15"])[0], int(q.get("n", ["600"])[0])))
                if u.path == "/api/companion":              # read-only snapshot for the mobile companion page
                    import companion
                    with hub.lock:
                        return self._json(dict(companion.snapshot(hub), version=VERSION, build=BUILD))
                path = "index.html" if u.path in ("/", "") else "m.html" if u.path in ("/m", "/m/") \
                    else u.path.lstrip("/")
                full = os.path.normpath(os.path.join(WEB, path))
                if not full.startswith(WEB) or not os.path.isfile(full):
                    return self._send(404, b"not found", "text/plain")
                ctype = {"html": "text/html; charset=utf-8", "js": "application/javascript",
                         "css": "text/css", "svg": "image/svg+xml", "png": "image/png"}.get(
                    full.rsplit(".", 1)[-1], "application/octet-stream")
                with open(full, "rb") as f:
                    return self._send(200, f.read(), ctype)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as e:
                traceback.print_exc()
                try:                                   # also in System & Alerts -> error log, with file:line
                    from error_log import report
                    report("OTHER_ERROR", f"api {u.path}", "API_GET_FAILED", e)
                except Exception:
                    pass
                try:
                    self._json({"error": str(e)}, 500)
                except Exception:
                    pass

        def do_POST(self):
            u = urlparse(self.path)
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                body = {}
            if u.path == "/api/plugin":
                chart = body.get("chart", body.get("enabled"))
                hub.set_plugin(body.get("key"), chart=chart, score=body.get("score"), params=body.get("params"),
                               vote_tf=body.get("vote_tf"), reset=bool(body.get("reset")),
                               chart_tfs=body.get("chart_tfs") if isinstance(body.get("chart_tfs"), list) else None)
            elif u.path == "/api/record":
                hub.toggle_record()
            elif u.path == "/api/pause":
                hub.pause()
            elif u.path == "/api/speed":
                hub.speed(int(body.get("dir", 1)))
            elif u.path == "/api/scoring":
                return self._json(hub.set_scoring(body))
            elif u.path == "/api/news/settings":
                return self._json(hub.news.update_settings(body) if hub.news else {"ok": False})
            elif u.path.startswith("/api/newstrade/"):
                nt = hub.newstrader
                if not nt:
                    return self._json({"ok": False, "error": "news trading not available"})
                act = u.path.rsplit("/", 1)[-1]
                if act == "enable":
                    return self._json(nt.set_enabled(body.get("enabled")))
                if act == "settings":
                    return self._json(nt.update_settings(body))
                if act == "cancel":
                    return self._json(dict(ok=True, cancelled=nt.cancel_all("cancelled by you")))
                return self._json({"ok": False, "error": "unknown"}, 404)
            elif u.path in ("/api/backtest/run", "/api/backtest/apply"):
                if not hub.backtest:
                    return self._json({"ok": False, "error": "not available"})
                if u.path.endswith("run"):
                    return self._json(hub.backtest.start(body))
                return self._json(hub.backtest.apply(body.get("setup") or {}))
            elif u.path == "/api/auto/start":
                at = hub.autotrader                      # the user's confirm: trading ON + AUTO ON together
                return self._json(at.start() if at else {"ok": False, "error": "trading not available"})
            elif u.path in ("/api/auto/enable", "/api/auto/settings"):
                at = hub.autotrader
                if not at:
                    return self._json({"ok": False, "error": "trading not available"})
                return self._json(at.set_enabled(body.get("enabled")) if u.path.endswith("enable")
                                  else at.update_settings(body))
            elif u.path == "/api/orderflow/row":
                return self._json(dict(ok=True, rows=hub.orderflow.set_row(body.get("tf"), float(body.get("row") or 0)))
                                  if hub.orderflow else {"ok": False})
            elif u.path == "/api/intermarket":
                return self._json(hub.intermarket.update_settings(body) if hub.intermarket else {"ok": False})
            elif u.path.startswith("/api/telegram/"):
                nt = hub.notifier
                if not nt:
                    return self._json({"ok": False, "error": "not available"})
                act = u.path.rsplit("/", 1)[-1]
                if act == "settings":
                    return self._json(nt.update_settings(body))
                if act == "test":
                    return self._json(nt.test())
                if act == "findchat":
                    r = nt.find_chat()
                    if r.get("ok") and body.get("use_first") and r["chats"]:
                        nt.update_settings(dict(chat_id=r["chats"][0]["id"]))
                    return self._json(r)
                return self._json({"ok": False, "error": "unknown"}, 404)
            elif u.path == "/api/report/send":
                if not hub.alerts:
                    return self._json({"ok": False})
                if body.get("preview"):
                    text, png = hub.alerts.build_report()
                    return self._json(dict(ok=True, text=text))
                return self._json(hub.alerts.send_report(force=True))
            elif u.path == "/api/exitguard/settings":
                return self._json(hub.exitguard.update_settings(body) if hub.exitguard else {"ok": False})
            elif u.path in ("/api/reconciliation/resume", "/api/reconciliation/settings"):
                rc = hub.reconciler
                if not rc:
                    return self._json({"ok": False})
                if u.path.endswith("resume"):
                    return self._json(rc.resume(body.get("id")))
                rc.auto_repair = bool(body.get("auto_repair", rc.auto_repair))
                return self._json(dict(ok=True, auto_repair=rc.auto_repair))
            elif u.path == "/api/strategies/settings":
                import strategies as stm
                cur = stm.load_settings()
                before = json.loads(json.dumps(cur))
                for n in stm.NAMES:                      # merge: only what was sent changes
                    if isinstance(body.get(n), dict):
                        cur[n].update({k: v for k, v in body[n].items() if k != "conditions"})
                        if isinstance(body[n].get("conditions"), dict):
                            cur[n]["conditions"].update(body[n]["conditions"])
                if "htf_filter" in body:
                    cur["htf_filter"] = bool(body["htf_filter"])
                strip = lambda x: {n: {k: v for k, v in x[n].items() if k != "auto"} for n in stm.NAMES} | \
                    {"htf_filter": x.get("htf_filter")}                               # noqa: E731
                if strip(stm.clean_settings(cur)) != strip(before):
                    cur["profile"] = "custom"                # you changed the numbers / conditions yourself
                saved = stm.save_settings(cur)
                on = [stm.TITLE[n] for n in stm.NAMES if saved[n]["auto"]]
                hub.note("warn" if on else "info", "STRATEGIES: AUTO " + (", ".join(on) if on else "off"))
                return self._json(dict(ok=True, settings=saved))
            elif u.path == "/api/strategies/profile":
                # NORMAL / AGGRESSIVE / SCALP preset: the strategy settings + the AUTO TF. Never switches anything ON.
                import strategies as stm
                name = str(body.get("name", ""))
                if name == "master":
                    name = "normal"                    # v2.8.37: MASTER is NORMAL now
                if name not in stm.PROFILES:
                    return self._json(dict(ok=False, error=f"unknown profile {name!r}"))
                saved = stm.save_settings(stm.apply_profile(stm.load_settings(), name))
                at = getattr(hub, "autotrader", None)
                tf = stm.PROFILES[name]["auto_tf"]
                if at is not None:
                    at.update_settings(dict(stm.PROFILES[name].get("autotrade") or {}, tf=tf))
                pm = getattr(hub, "positions_mgr", None)
                if pm is not None:                       # profit protection that fits this profile's TP (RR)
                    pm.update_settings(dict(stm.PROFILES[name]["protect"]))
                hub.note("info", f"STRATEGIES profile: {stm.PROFILES[name]['title']} (AUTO TF {tf}) · protect "
                                 + str(stm.PROFILES[name]["protect"]))
                return self._json(dict(ok=True, settings=saved, auto_tf=tf if at is not None else None))
            elif u.path.startswith("/api/ai/"):
                ai = getattr(hub, "ai", None)            # AI analyst: read-only text, never reaches the trade API
                if ai is None:
                    return self._json({"ok": False, "error": "AI analyst not available"})
                act = u.path.rsplit("/", 1)[-1]
                if act == "settings":
                    return self._json(ai.update_settings(body))
                if act == "ask":
                    return self._json(ai.ask(str(body.get("mode", "analysis")), str(body.get("question", ""))))
                return self._json({"ok": False, "error": "unknown"}, 404)
            elif u.path.startswith("/api/forecast/"):
                fs = hub.forecast                        # forecast endpoints never reach the trade API
                if fs is None:
                    return self._json({"ok": False, "error": "forecast not available"})
                act = u.path.rsplit("/", 1)[-1]
                if act == "settings":
                    return self._json(fs.update_settings(body))
                if act == "create":
                    return self._json(fs.create_now())
                if act == "cancel":
                    return self._json(fs.engine.cancel(str(body.get("id", ""))))
                if act == "replay":
                    return self._json(fs.run_replay(body.get("days", 10), body.get("date_from"), body.get("date_to")))
                if act == "profile":
                    return self._json(fs.set_profile(body))
                if act == "profile_copy":
                    return self._json(fs.set_profile(None, copy_live=True))
                return self._json({"error": "unknown"}, 404)
            elif u.path == "/api/master":
                if not hub.risk:
                    return self._json({"ok": False, "error": "risk engine not available"})
                return self._json(hub.risk.set_master(bool(body.get("enabled")), str(body.get("reason", ""))[:80]))
            elif u.path == "/api/risk/settings":
                return self._json(hub.risk.update_settings(body) if hub.risk else {"ok": False})
            elif u.path == "/api/risk/reset_peak":
                return self._json(hub.risk.reset_peak() if hub.risk else {"ok": False})
            elif u.path == "/api/decision/settings":
                return self._json(dict(ok=True, settings=hub.decision.update_settings(body)) if hub.decision
                                  else {"ok": False})
            elif u.path == "/api/smc/settings":
                return self._json(dict(ok=True, settings=hub.set_smc_settings(body)))
            elif u.path == "/api/positions/settings":
                return self._json(hub.positions_mgr.update_settings(body) if hub.positions_mgr else {"ok": False})
            elif u.path == "/api/sessions":
                import market_hours as mh
                return self._json(dict(ok=True, sessions=mh.save_sessions(body)))
            elif u.path == "/api/execution/settings":
                return self._json(hub.execution.update_settings(body) if hub.execution else {"ok": False})
            elif u.path == "/api/regime/settings":
                return self._json(hub.regime.update_settings(body) if hub.regime else {"ok": False})
            elif u.path.startswith("/api/drift/"):
                d = hub.drift
                if not d:
                    return self._json({"ok": False})
                act = u.path.rsplit("/", 1)[-1]
                return self._json(d.set_baseline() if act == "baseline" else d.clear() if act == "clear"
                                  else d.update_settings(body))
            elif u.path == "/api/news/refresh":
                if hub.news:
                    threading.Thread(target=hub.news.refresh, kwargs={"force": True}, daemon=True).start()
            elif u.path.startswith("/api/trade/") or u.path == "/api/accounts":
                return self._json(trade_api(hub, u.path, body))
            else:
                return self._json({"error": "unknown"}, 404)
            return self._json({"ok": True})

    return Handler


def trade_plan(hub, side, lot, sl_dist, tp_dist):
    """Everything shown before an execution: why (setup matrix), existing score, risk / target $, RR, blockers,
    execution per account. Read-only."""
    de = hub.decision
    d = de.decide(side, "manual")
    tk = hub.last_tick
    s = d["side_i"]
    price = (tk.ask if s == 1 else tk.bid) if tk and s else None
    tm = hub.trader
    plan = tm.entry_plan(lot, s or 1, sl_dist, tp_dist) if tm else []
    risk_usd = round(sl_dist * lot * 100, 2) if sl_dist else None
    return dict(side=d["side"], state=d["state"], score=d["score"], passing=d["passing_score"],
                rows=d["matrix"]["rows"], blockers=d["blockers"], hard_blockers=d["hard_blockers"],
                entry=None if price is None else round(price, 2),
                sl=None if price is None or not sl_dist else round(price - s * sl_dist, 2),
                tp=None if price is None or not tp_dist else round(price + s * tp_dist, 2),
                rr=round(tp_dist / sl_dist, 2) if sl_dist and tp_dist else None, lot=lot,
                risk_usd=risk_usd, target_usd=round(tp_dist * lot * 100, 2) if tp_dist else None,
                execution=plan, regime=d["regime"], session=d["session"], news=d["news"])


def trade_api(hub, path, b):
    """All order actions. Every one of them is a direct user click in the UI (nothing is automatic)."""
    t = hub.trader
    if t is None:
        return dict(ok=False, error="trading not available")
    try:
        if path == "/api/accounts":
            return t.update_config(b)
        act = path.rsplit("/", 1)[-1]
        if act == "arm":
            return t.set_armed(b.get("armed"))
        book = getattr(hub, "confirmations", None)
        if act == "prepare":
            return book.prepare(b.get("side"), b.get("lot"), b.get("sl_dist"), b.get("tp_dist"), b.get("request_id"))
        if act in ("confirm", "open"):
            # "open" is kept for compatibility but now also needs the one-time server token from /prepare
            if not b.get("token"):
                return dict(ok=False, error="server confirmation required: call /api/trade/prepare first")
            return book.confirm(b.get("token"), b.get("request_id"), b.get("side"), b.get("lot"),
                                b.get("sl_dist"), b.get("tp_dist"))
        if act == "stopnew":
            return t.stop_new_trades()
        if act == "emergency":
            return t.emergency_stop(close_positions=bool(b.get("close_positions")))
        if act == "safety":
            t.safety.update({k: type(t.safety[k])(v) for k, v in (b or {}).items() if k in t.safety})
            cfg = dict(t.cfg, safety={k: t.safety[k] for k in t.safety})
            from trading import save_config
            save_config(cfg)
            t.cfg = cfg
            return dict(ok=True, safety=t.safety)
        if act == "close":
            return t.close_group(b["gid"], float(b.get("fraction", 1.0)))
        if act == "modify":
            return t.modify_group(b["gid"], float(b.get("sl") or 0), float(b.get("tp") or 0))
        if act == "be":
            return t.breakeven(b["gid"])
        if act == "closeall":
            return t.close_everything()
        if act == "unlock":
            return t.unlock(b["id"])
        if act == "reconnect":
            return t.reconnect(b["id"])
        if act == "symbols":
            return t.find_symbols(b.get("id", "master"), b.get("base") or None)
        if act == "test":
            return t.test_connections(float(b.get("lot") or 0.01))
        if act == "signaltest":
            return t.signal_test(float(b.get("hold") if b.get("hold") is not None else 5.0))
        if act == "plan":
            return dict(ok=True, plan=t.entry_plan(float(b.get("lot") or 0)))
        return dict(ok=False, error="unknown action")
    except KeyError as e:
        return dict(ok=False, error=str(e))
    except (TypeError, ValueError) as e:
        return dict(ok=False, error=f"bad input: {e}")


def open_window(url: str):
    """Open as an app window (no address bar). Edge first, then Chrome, then default browser."""
    cands = [
        shutil.which("msedge"),
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        shutil.which("chrome"),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]
    profile = os.path.join(HERE, ".appwindow")
    for exe in cands:
        if exe and os.path.isfile(exe):
            try:
                subprocess.Popen([exe, f"--app={url}", "--start-maximized",
                                  f"--user-data-dir={profile}", "--no-first-run"])
                return
            except OSError:
                continue
    webbrowser.open(url)


def main(argv=None):
    ap = argparse.ArgumentParser(description="REYGUN SUPERCHART")
    ap.add_argument("--mode", choices=["live", "demo", "replay"], default="live")
    ap.add_argument("--symbol", default="XAUUSD")
    ap.add_argument("--file", help="recorded CSV for --mode replay")
    ap.add_argument("--speed", type=int, default=1)
    ap.add_argument("--warmup-min", type=int, default=240)
    ap.add_argument("--record", action="store_true")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--utc-offset", type=int, default=None,
                    help="broker server time minus UTC in hours (auto-detected in live mode)")
    ap.add_argument("--no-window", action="store_true", help="do not open the app window")
    ap.add_argument("--resume", action="store_true",
                    help="(used by the .bat after a crash) restore trading ON / AUTO / NEWS TRADE if it stopped < 10 min ago")
    ap.add_argument("--paper", action="store_true", help="live chart data, but trades are simulated (paper)")
    ap.add_argument("--no-trading", action="store_true", help="hide the trade panel completely (read-only)")
    ap.add_argument("--no-news", action="store_true", help="do not fetch the news calendar / headlines")
    ap.add_argument("--login")
    ap.add_argument("--password")
    ap.add_argument("--server")
    ap.add_argument("--mt5-path")
    a = ap.parse_args(argv)
    attach_error_file(os.path.join(HERE, "errors.jsonl"))
    import runstate
    runstate.install_crash_logging()
    prev_state = runstate.read()

    master_login = None
    if a.mode == "live" and not a.mt5_path:
        try:                                    # master terminal chosen on the Accounts page
            from trading import load_config
            mc = load_config()["master"]
            a.mt5_path = mc.get("path") or None
            master_login = mc.get("login") or None
            if mc.get("symbol"):
                a.symbol = mc["symbol"]
            if a.mt5_path:
                print(f"Master MT5 (from Accounts page): {a.mt5_path}")
        except Exception as e:
            print(f"accounts.json not read: {e}")
        if not a.mt5_path and not a.login:
            # no terminal chosen yet: attach to the OPEN MT5 that has this account / symbol instead of letting
            # MetaTrader5 start its "default" terminal (often another broker on a PC / VPS with several MT5)
            try:
                import mt5_detect
                found = mt5_detect.detect(a.symbol)
                for f in found:
                    print(f"  open MT5: {f['path']} · " + (f"#{f.get('login')} {f.get('server')}"
                          + ("" if f.get("has_symbol") else f" · no {a.symbol}") if f.get("ok") else f.get("error", "")))
                pick = mt5_detect.pick_master(found, master_login, a.symbol)
                if pick:
                    a.mt5_path = pick["path"]
                    print(f"Master MT5 (auto-detected, open terminal): {pick['path']} #{pick.get('login')}")
                    from trading import load_config, save_config
                    cfg = load_config()
                    cfg["master"]["path"] = pick["path"]                       # next start: the same terminal
                    if not cfg["master"].get("login"):
                        cfg["master"]["login"] = int(pick.get("login") or 0)
                    save_config(cfg)
                    master_login = master_login or pick.get("login")
                elif len(found) > 1:
                    print("WARNING: several MT5 terminals are open - choose the master on the Accounts page "
                          "(🔍 Hanapin ang bukas na MT5)")
            except Exception as e:
                print(f"MT5 auto-detect skipped: {e}")
    try:
        if a.mode == "live":
            src = MT5Source(a.symbol, a.login, a.password, a.server, a.mt5_path)
            if src.check_login(master_login):
                print(f"WARNING: {src.login_error}  (trading is blocked until this is fixed)")
        elif a.mode == "replay":
            if not a.file:
                ap.error("--file is required for replay")
            src = ReplaySource(a.file, a.warmup_min, a.symbol if a.symbol != "XAUUSD" else None)
        else:
            src = DemoSource(a.symbol)
    except RuntimeError as e:
        print(f"\n[REYGUN SUPERCHART] {e}\n")
        sys.exit(1)
    src.speed = a.speed if a.mode != "live" else 1

    hub = Hub(src, record=a.record, utc_offset=a.utc_offset)
    hub.analyze()
    threading.Thread(target=hub.run, daemon=True).start()
    if not a.no_trading:
        from trading import TradeManager
        hub.trader = TradeManager(hub, paper=a.paper)
        from reconciliation import Reconciler
        hub.reconciler = Reconciler(hub)
        from execution_guard import ExecutionGuard
        hub.execution = ExecutionGuard(hub)
        from trade_confirmation import ConfirmationBook
        hub.confirmations = ConfirmationBook(hub.trader, hub.execution.settings["confirmation_expiry"])
        from autotrade import AutoTrader
        hub.autotrader = AutoTrader(hub.trader)
        from position_manager import PositionController
        hub.positions_mgr = PositionController(hub.trader)
        mode = "PAPER trading (simulated)" if hub.trader.paper else "LIVE trading (real orders, needs ARM)"
        print(f"Trade panel: {mode}")

    from regime import Regime
    hub.regime = Regime(hub)
    from risk_engine import RiskEngine
    hub.risk = RiskEngine(hub)
    from decision_engine import DecisionEngine
    hub.decision = DecisionEngine(hub)
    if hub.trader:
        from trade_gate import MasterGate
        hub.gate = MasterGate(hub, paper=hub.trader.paper)
    from forecast import ForecastService
    hub.forecast = ForecastService(hub)          # READ-ONLY prediction layer (never trades)
    from ai_analyst import AIAnalyst
    hub.ai = AIAnalyst(hub)                      # Claude Fable analyst: READ-ONLY, only on a button press
    from notify import Notifier
    hub.notifier = Notifier(hub)
    from alerts import Alerts
    hub.alerts = Alerts(hub)
    from watchdog import Watchdog
    hub.watchdog = Watchdog(hub)
    from drift import Drift
    hub.drift = Drift(hub)
    if hub.trader:
        from exitguard import ExitGuard
        hub.exitguard = ExitGuard(hub)
    from backtest import BacktestJob
    hub.backtest = BacktestJob(hub)
    from orderflow import OrderFlow
    hub.orderflow = OrderFlow(hub)
    from intermarket import Intermarket
    hub.intermarket = Intermarket(hub)
    if not a.no_news:
        from news import NewsFeed
        hub.news = NewsFeed()
        print("News reader: ForexFactory calendar + RSS headlines (background)")
        from news_results import NewsResults
        hub.news_results = NewsResults(hub)
        if hub.trader:
            from news_trader import NewsTrader
            hub.newstrader = NewsTrader(hub.trader)
            print("News Trader: loaded (OFF - switch it on in the NEWS TRADE card; needs ARM)")

    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(hub))
    except OSError as e:
        print(f"\n[REYGUN SUPERCHART] port {a.port} busy ({e}). Is it already running? Try --port 8766\n")
        hub.close()
        sys.exit(1)
    httpd.daemon_threads = True
    if a.resume and hub.trader:
        back = runstate.resume(hub, prev_state)
        if back:
            hub.note("warn", "RESUMED after an unexpected stop: " + ", ".join(back))
            print("RESUMED after an unexpected stop: " + ", ".join(back))
    if hub.trader and hub.autotrader and hub.autotrader.settings.get("auto_on_start") and \
            not hub.autotrader.enabled and not getattr(a, "no_trading", False):
        r = hub.autotrader.start("AUTO ON pagbukas")
        msg = "AUTO ON pagbukas ng app (setting)" if r.get("ok") else f"AUTO ON pagbukas FAILED: {r.get('error')}"
        nt = getattr(hub, "newstrader", None)
        if r.get("ok") and nt is not None and hub.autotrader.settings.get("news_on_start", True) and not nt.enabled:
            rn = nt.set_enabled(True)
            msg += " · NEWS TRADE ON" if rn.get("ok") else f" · NEWS TRADE hindi na-ON: {rn.get('error')}"
        hub.note("warn", msg)
        print(msg)
    runstate.start_writer(hub)
    url = f"http://127.0.0.1:{a.port}/"
    print(f"REYGUN SUPERCHART v{VERSION} (build {BUILD})  |  {src.name} {src.symbol}  |  {url}")
    print("Orders are only sent when YOU click BUY/SELL (after ARM + confirm). Close this window (or Ctrl+C) to stop.")
    if not a.no_window:
        threading.Timer(0.8, open_window, args=(url,)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        runstate.mark_clean(hub)
        httpd.server_close()
        hub.close()


if __name__ == "__main__":
    main()
