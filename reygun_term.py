"""
REYGUN TERM - terminal chart + analyser for MT5 (XAUUSD and any symbol).
READ-ONLY: this app never sends orders.

Run:
  python reygun_term.py --mode live   --symbol XAUUSD
  python reygun_term.py --mode demo
  python reygun_term.py --mode replay --file recordings/XAUUSD_20260925_101500.csv --speed 20

Keys:
  SPACE pause  +/- speed  1-5 chart TF  p plugins  r record  c clear log  q quit
"""
from __future__ import annotations

import argparse
import sys
import time
from collections import deque
from datetime import datetime, timezone

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import RichLog, SelectionList, Static

from engine import TF_SECONDS, Bar, TFState, analyze, estimate_utc_offset, market_read
from plugin_api import Context, Overlay, Signal
from plugin_loader import load_plugins, load_scoring, save_state, short_tb
from sources import DemoSource, MT5Source, Recorder, ReplaySource

STRUCT_TFS = ["H4", "H1", "M15", "M5"]
CHART_KEYS = {"1": "M1", "2": "M5", "3": "M15", "4": "H1", "5": "H4"}
SPEEDS = [1, 2, 5, 10, 20, 50, 100, 250, 500]

BULL = "#55ff55"
BEAR = "#ff5555"
DIM = "#8888cc"


def fmt_t(sec: int, with_date=False) -> str:
    d = datetime.fromtimestamp(sec, tz=timezone.utc)
    return d.strftime("%m-%d %H:%M" if with_date else "%H:%M:%S")


def term_bg(hex_color: str) -> str:
    """Bright plugin colors make unreadable cell backgrounds; darken them for the terminal."""
    h = (hex_color or "").lstrip("#")
    if len(h) != 6:
        return hex_color
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    if max(r, g, b) <= 0x60:
        return hex_color
    k = 0x48 / max(r, g, b)
    return "#%02x%02x%02x" % (int(r * k), int(g * k), int(b * k))


def trend_txt(x: int) -> Text:
    if x == 1:
        return Text("BULL", style=f"bold {BULL}")
    if x == -1:
        return Text("BEAR", style=f"bold {BEAR}")
    return Text("----", style=DIM)


# --------------------------------------------------------------------------
class CandleChart(Static):
    """Plain ASCII candlestick chart. Indicator plugins add lines/levels/markers/zones."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.bars: list[Bar] = []
        self.overlays: list[Overlay] = []
        self.bid = None

    def set_data(self, bars, overlays, bid):
        self.bars, self.overlays, self.bid = bars, overlays, bid
        self.refresh()

    def render(self):
        W, H = self.content_size.width, self.content_size.height
        axis_w = 11
        cols = max(5, W - axis_w)
        rows = max(4, H)
        all_bars = self.bars
        bars = all_bars[-cols:]
        if not bars:
            return Text("waiting for data...", style=DIM)
        offset = len(all_bars) - len(bars)   # to align plugin line values

        hi = max(b.h for b in bars)
        lo = min(b.l for b in bars)
        span = hi - lo or 1.0
        pad = span * 0.04
        hi += pad
        lo -= pad

        levels, lines, markers, zones, vlines = [], [], [], [], []
        for ov in self.overlays:
            vlines += getattr(ov, "vlines", [])
            levels += [lv for lv in ov.levels if not getattr(lv, "pane", "")]
            lines += [ln for ln in ov.lines if not getattr(ln, "pane", "")]   # pane lines: SuperChart only
            markers += ov.markers
            zones += ov.zones
        # only pull levels into view if they are reasonably close
        levels = [lv for lv in levels if lo - span * 0.5 < lv.price < hi + span * 0.5]
        for lv in levels:
            hi, lo = max(hi, lv.price), min(lo, lv.price)
        step = (hi - lo) / rows

        def row_of(price):
            return min(rows - 1, max(0, int((hi - price) / step)))

        def in_view(price):
            return lo <= price <= hi

        level_rows = {}
        for lv in levels:
            level_rows.setdefault(row_of(lv.price), lv)
        cur_price = self.bid if self.bid else bars[-1].c
        cur_row = row_of(cur_price)

        grid = [[[" ", "", ""] for _ in range(cols)] for _ in range(rows)]   # char, fg, bg

        # zones (background)
        for z in zones:
            for r in range(rows):
                rhi = hi - r * step
                rlo = rhi - step
                if z.hi >= rlo and z.lo <= rhi:
                    t_to = getattr(z, "t_to", None)
                    for ci, b in enumerate(bars):
                        if b.t >= z.t_from and (t_to is None or b.t <= t_to):
                            grid[r][ci][2] = term_bg(z.bg)
        # period separators
        vcols = {v.t: v for v in vlines}
        for ci, b in enumerate(bars):
            v = vcols.get(b.t)
            if v:
                for r in range(rows):
                    if grid[r][ci][0] == " ":
                        grid[r][ci][0], grid[r][ci][1] = "┆", "#5b6b88"
        # level lines
        for r, lv in level_rows.items():
            for ci in range(len(bars)):
                grid[r][ci][0], grid[r][ci][1] = "┄", lv.color
        # candles
        for ci, b in enumerate(bars):
            col = BULL if b.c >= b.o else BEAR
            bhi, blo = max(b.o, b.c), min(b.o, b.c)
            for r in range(rows):
                rhi = hi - r * step
                rlo = rhi - step
                if bhi >= rlo and blo <= rhi:
                    grid[r][ci][0], grid[r][ci][1] = "█", col
                elif b.h >= rlo and b.l <= rhi:
                    grid[r][ci][0], grid[r][ci][1] = "│", col
        # indicator lines (drawn only on empty cells, candles stay on top)
        for ln in lines:
            vals = ln.values[offset:offset + len(bars)] if len(ln.values) == len(all_bars) \
                else ln.values[-len(bars):]
            start = len(bars) - len(vals)
            for i, v in enumerate(vals):
                if v is None or not in_view(v):
                    continue
                cell = grid[row_of(v)][start + i]
                if cell[0] in (" ", "┄"):
                    cell[0], cell[1] = ln.char, ln.color
        # markers
        idx = {b.t: ci for ci, b in enumerate(bars)}
        for m in markers:
            ci = idx.get(m.t)
            if ci is None:
                continue
            b = bars[ci]
            r = row_of(b.h) - 1 if m.pos == "above" else row_of(b.l) + 1
            if 0 <= r < rows:
                grid[r][ci] = [m.char[:1], m.style, ""]

        out = Text(no_wrap=True, overflow="crop")
        for r in range(rows):
            for ch, fg, bg in grid[r]:
                sty = (fg + (f" on {bg}" if bg else "")).strip()
                out.append(ch, style=sty or None)
            mid = hi - (r + 0.5) * step
            if r == cur_row:
                out.append(f" ◀{cur_price:>{axis_w - 2}.2f}", style="bold black on #ffff55")
            elif r in level_rows:
                lv = level_rows[r]
                out.append(f" {lv.label[:3]:<3}{lv.price:>{axis_w - 4}.2f}"[:axis_w], style=f"bold {lv.color}")
            elif r % 4 == 0:
                out.append(f" {mid:>{axis_w - 1}.2f}", style=DIM)
            if r < rows - 1:
                out.append("\n")
        return out


# --------------------------------------------------------------------------
class PluginMenu(ModalScreen):
    """Toggle plugins on/off."""
    DEFAULT_CSS = """
    PluginMenu { align: center middle; background: #00000080; }
    #menu { width: 78; height: auto; max-height: 80%; background: #0000a8;
            border: double #ffff55; padding: 0 1; }
    #menu-title { color: #ffff55; text-style: bold; }
    #menu-help { color: #8888cc; }
    SelectionList { background: #0000a8; height: auto; max-height: 24; border: none; }
    """
    BINDINGS = [("escape", "close", "Close"), ("p", "close", "Close")]

    def __init__(self, plugins):
        super().__init__()
        self.plugins = plugins

    def compose(self) -> ComposeResult:
        items = []
        for i, p in enumerate(self.plugins):
            tag = (getattr(p.obj, "category", "IND") or "IND").upper()[:6] if p.kind == "indicator" else "STRAT"
            desc = getattr(p.obj, "description", "")
            err = "  [ERROR]" if p.error else ""
            sc = " +SCORE" if getattr(p, "scoring", False) else ""
            items.append((f"[{tag}{sc}] {p.name} - {desc}{err}", i, p.enabled))
        with Vertical(id="menu"):
            yield Static("PLUGINS", id="menu-title")
            yield Static("↑↓ move · SPACE chart on/off · ESC close   (SCORE & settings: set them in SuperChart)",
                         id="menu-help")
            if items:
                yield SelectionList(*items)
            else:
                yield Static("No plugins found in plugins/ folder.")

    def action_close(self):
        try:
            sel = self.query_one(SelectionList).selected
        except Exception:
            sel = None
        self.dismiss(sel)


# --------------------------------------------------------------------------
class ReygunTerm(App):
    TITLE = "REYGUN TERM"
    CSS = """
    Screen { background: #0000a8; color: #e8e8ff; }
    #topbar { height: 1; background: #00a8a8; color: #000000; text-style: bold; }
    #row1 { height: 3fr; }
    #row2 { height: 2fr; }
    #chart { width: 3fr; height: 100%; border: round #55ffff; border-title-color: #ffff55;
             border-subtitle-color: #e8e8ff; }
    #structure { width: 2fr; height: 100%; border: round #55ffff; border-title-color: #ffff55; padding: 0 1; }
    #tape { width: 32; border: round #55ffff; border-title-color: #ffff55; background: #0000a8; scrollbar-size: 0 0; }
    #events { width: 2fr; border: round #55ffff; border-title-color: #ffff55; background: #0000a8; scrollbar-size-vertical: 1; scrollbar-size-horizontal: 0; }
    #read { width: 2fr; height: 100%; border: round #55ffff; border-title-color: #ffff55; padding: 0 1; }
    #statusbar { height: 1; background: #a8a8a8; color: #000000; }
    """
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("space", "pause", "Pause"),
        ("plus", "faster", "Faster"),
        ("equals_sign", "faster", "Faster"),
        ("minus", "slower", "Slower"),
        ("r", "record", "Record"),
        ("c", "clear", "Clear"),
        ("p", "plugins", "Plugins"),
    ] + [(k, f"chart_tf('{v}')", v) for k, v in CHART_KEYS.items()]

    def __init__(self, src, chart_tf="M5", record=False, bell=True):
        super().__init__()
        self.src = src
        self.chart_tf = chart_tf
        self.recorder: Recorder | None = None
        self._record_on_start = record
        self.use_bell = bell
        self.states: dict[str, TFState] = {}
        self.read = None
        self.seen_events: set = set()
        self.seen_signals: set = set()
        self.last_signal: dict[str, Signal] = {}
        self.first_analysis = True
        self.last_bid = None
        self.last_tick = None
        self.tick_times: deque = deque(maxlen=4000)
        self.total_ticks = 0
        self.started = time.monotonic()
        self._n = 0
        self.speed_i = SPEEDS.index(int(src.speed)) if int(src.speed) in SPEEDS else 0
        self.plugins, self.plugin_errors = load_plugins()
        self.utc_offset = 0

    # ---------------------------------------------------------------- layout
    def compose(self) -> ComposeResult:
        yield Static(id="topbar")
        with Horizontal(id="row1"):
            yield CandleChart(id="chart")
            yield Static(id="structure")
        with Horizontal(id="row2"):
            yield RichLog(id="tape", max_lines=300, markup=False, wrap=False)
            yield RichLog(id="events", max_lines=500, markup=False, wrap=False)
            yield Static(id="read")
        yield Static(id="statusbar")

    def on_mount(self):
        self.query_one("#chart").border_title = f"1 CHART {self.chart_tf}"
        self.query_one("#structure").border_title = "2 ANALYSER"
        self.query_one("#tape").border_title = "3 TAPE"
        self.query_one("#events").border_title = "4 EVENT LOG"
        self.query_one("#read").border_title = "5 READ"
        self.log_event(Text(f"source: {self.src.name} {self.src.symbol}  {self.src.status}", style="bold #ffff55"))
        n_on = sum(p.enabled for p in self.plugins)
        self.log_event(Text(f"plugins: {len(self.plugins)} loaded, {n_on} on  (press p)", style="#ffff55"))
        for e in self.plugin_errors:
            self.log_event(Text(f"plugin load error: {e}", style=f"bold {BEAR}"))
        if self._record_on_start:
            self.action_record()
        self.update_chart_subtitle()
        self.analyze_all()
        self.set_interval(0.25, self.loop)

    # ------------------------------------------------------------------ loop
    def loop(self):
        self._n += 1
        try:
            ticks = self.src.poll()
        except Exception as e:  # keep the terminal alive
            self.log_event(Text(f"poll error: {e}", style=f"bold {BEAR}"))
            ticks = []
        if ticks:
            if self.recorder:
                self.recorder.write(ticks)
            self.total_ticks += len(ticks)
            now = time.monotonic()
            self.tick_times.extend([now] * min(len(ticks), 500))
            self.push_tape(ticks)
            self.last_tick = ticks[-1]
            if self.src.live:
                self.utc_offset = estimate_utc_offset(self.last_tick.t_msc, time.time(), self.utc_offset)
        if self._n % 4 == 0 or (ticks and not self.src.live and self.src.speed >= 20):
            self.analyze_all()
        self.update_chart()
        self.update_bars()

    def push_tape(self, ticks):
        tape = self.query_one("#tape", RichLog)
        for tk in ticks[-40:]:
            if self.last_bid is None or tk.bid == self.last_bid:
                sty, arrow = "#e8e8ff", " "
            elif tk.bid > self.last_bid:
                sty, arrow = BULL, "▲"
            else:
                sty, arrow = BEAR, "▼"
            self.last_bid = tk.bid
            ts = datetime.fromtimestamp(tk.t_msc / 1000, tz=timezone.utc).strftime("%H:%M:%S.%f")[:-3]
            spr = tk.ask - tk.bid
            tape.write(Text.assemble((f"{ts} ", DIM), (f"{arrow}{tk.bid:.2f}", sty),
                                     (f" {spr:4.2f}", "#ffff55" if spr > 0.4 else DIM)))

    # -------------------------------------------------------------- analysis
    def context(self) -> Context:
        return Context(self.src.symbol, self.src.bars, self.last_tick, self.states, self.read, self.utc_offset)

    def analyze_all(self):
        for tf in STRUCT_TFS:
            bars = self.src.bars(tf)
            self.states[tf] = analyze(tf, bars, n=3 if tf in ("H4", "H1") else 2)
        m1 = self.src.bars("M1")
        self.read = market_read(self.states, self.src.bars("D1"), m1, self.modules(), self.collect_votes(),
                                load_scoring())
        self.log_new_events()
        self.run_strategies()
        self.update_structure()
        self.update_read()

    def run_strategies(self):
        ctx = self.context()
        for p in self.plugins:
            if p.kind != "strategy" or not p.enabled or p.error:
                continue
            try:
                res = p.obj.evaluate(ctx)
            except Exception as e:
                self.plugin_failed(p, e)
                continue
            if not res:
                continue
            for sig in (res if isinstance(res, list) else [res]):
                key = (p.key, sig.t, sig.direction)
                if key in self.seen_signals:
                    continue
                self.seen_signals.add(key)
                self.last_signal[p.key] = sig
                self.log_signal(p.name, sig)

    def plugin_failed(self, p, e):
        p.error = short_tb(e)
        self.log_event(Text(f"plugin '{p.name}' error, disabled: {p.error}", style=f"bold {BEAR}"))

    def log_signal(self, name, sig: Signal):
        up = sig.direction == 1
        parts = [(fmt_t(sig.t, with_date=True) + " ", DIM),
                 (f" {'BUY ' if up else 'SELL'} ", "bold black on #55ff55" if up else "bold white on #ff5555"),
                 (f" {name}", "bold #ffff55"),
                 (f"  {sig.tf} {sig.reason}", "#e8e8ff")]
        lv = []
        if sig.entry is not None:
            lv.append(f"@{sig.entry:.2f}")
        if sig.sl is not None:
            lv.append(f"SL {sig.sl:.2f}")
        if sig.tp is not None:
            lv.append(f"TP {sig.tp:.2f}")
        if lv:
            parts.append(("  " + " ".join(lv), "#55ffff"))
        self.log_event(Text.assemble(*parts))
        if self.use_bell:
            self.bell()

    def collect_votes(self):
        out, ctx = [], self.context()
        for p in self.plugins:
            if not p.votes or not p.scoring or p.error:
                continue
            try:
                v = p.obj.vote(ctx)
            except Exception as e:
                self.plugin_failed(p, e)
                continue
            for x in (v if isinstance(v, (list, tuple)) else [v] if v is not None else []):
                out.append((getattr(x, "name", "") or p.name, getattr(p.obj, "category", "Other"), int(x.direction), x.reason))
        return out

    def modules(self) -> set:
        return {p.obj.analyser_module for p in self.plugins
                if p.kind == "indicator" and p.scoring and not p.error and getattr(p.obj, "analyser_module", None)}

    def log_new_events(self):
        fresh = []
        for tf in STRUCT_TFS:
            st = self.states.get(tf)
            bars = self.src.bars(tf)
            if not st or not bars:
                continue
            recent_t = bars[-1].t - 3 * TF_SECONDS[tf]   # only events from the last few bars are "new"
            for e in st.events:
                if e.t < recent_t and not self.first_analysis:
                    self.seen_events.add(e.key)
                    continue
                if e.key not in self.seen_events:
                    self.seen_events.add(e.key)
                    fresh.append(e)
        if self.first_analysis:
            self.first_analysis = False
            for tf in STRUCT_TFS:
                st = self.states.get(tf)
                e = st.last() if st else None
                if e:
                    self.log_event(self.event_text(e, hist=True))
            return
        order = {"H4": 0, "H1": 1, "M15": 2, "M5": 3}
        liq = "liquidity" in self.modules()
        for e in sorted(fresh, key=lambda e: (e.t, order[e.tf])):
            if e.kind in ("SWEEP", "FVG") and not liq:
                continue   # only with the liquidity module on
            if e.kind == "FVG" and e.tf == "M5":
                continue   # too noisy
            self.log_event(self.event_text(e))

    def event_text(self, e, hist=False):
        up = e.direction == 1
        col = "#ffff55" if e.kind == "SWEEP" else (BULL if up else BEAR)
        label = {"BOS": "BOS  ", "CHoCH": "CHoCH", "SWEEP": "SWEEP", "FVG": "FVG  "}[e.kind]
        detail = e.note if e.kind == "FVG" else f"{e.level:.2f} {e.note}"
        big = e.tf in ("H4", "H1") and e.kind in ("BOS", "CHoCH")
        return Text.assemble(
            (fmt_t(e.t, with_date=True) + " ", DIM),
            (f"{e.tf:<3} ", "bold #55ffff"),
            (f"{'▲' if up else '▼'} {label} ", f"bold {col}" + (" reverse" if big else "")),
            (detail, "#e8e8ff"),
            ("  (hist)" if hist else "", DIM),
        )

    def log_event(self, text: Text):
        self.query_one("#events", RichLog).write(text)

    # ------------------------------------------------------------------ views
    def update_structure(self):
        t = Text(no_wrap=True, overflow="ellipsis")
        mods = self.modules()
        show_pd = "premium_discount" in mods
        t.append(f"{'TF':<4}{'TREND':<6}{'LAST BREAK':<18}{'P/D' if show_pd else '':>5}\n", style="bold #ffff55")
        for tf in STRUCT_TFS:
            st = self.states.get(tf)
            if not st:
                continue
            t.append(f"{tf:<4}", style="bold #55ffff")
            t.append_text(trend_txt(st.trend))
            t.append("  ")
            e = st.last()
            if e:
                col = BULL if e.direction == 1 else BEAR
                t.append(f"{e.kind:<5}{'▲' if e.direction == 1 else '▼'}{e.level:>10.2f} ", style=col)
            else:
                t.append(f"{'-':<18}", style=DIM)
            if show_pd and st.pd is not None:
                if st.pd < 0:
                    t.append(" <0% ", style="#55aaff")
                elif st.pd > 1:
                    t.append(">100%", style="#ff9955")
                else:
                    t.append(f"{st.pd * 100:>4.0f}%", style="#ff9955" if st.pd > 0.5 else "#55aaff")
            t.append("\n")
        t.append("\n")
        if "liquidity" in mods or "prev_day" in mods:
            t.append("LIQUIDITY / ZONES\n", style="bold #ffff55")
        for tf in (("H1", "M15") if "liquidity" in mods else ()):
            st = self.states.get(tf)
            if not st:
                continue
            if st.sh:
                tag = "broken" if st.sh.broken else ("swept" if st.sh.swept else "resting")
                t.append(f"{tf:<4}BSL {st.sh.level:>9.2f} {tag}\n", style=BEAR)
            if st.sl:
                tag = "broken" if st.sl.broken else ("swept" if st.sl.swept else "resting")
                t.append(f"{tf:<4}SSL {st.sl.level:>9.2f} {tag}\n", style=BULL)
            if st.fvg_bull:
                t.append(f"{tf:<4}FVG+ {st.fvg_bull.lo:.2f}-{st.fvg_bull.hi:.2f}\n", style="#88dd88")
            if st.fvg_bear:
                t.append(f"{tf:<4}FVG- {st.fvg_bear.lo:.2f}-{st.fvg_bear.hi:.2f}\n", style="#dd8888")
        r = self.read
        if r and r.pdh is not None:
            t.append(f"D1  PDH {r.pdh:>9.2f}   PDL {r.pdl:.2f}\n", style="#ff55ff")
        if not mods:
            t.append("Price action + TF bias only.\n", style=DIM)
            t.append("Press p to add zones/liquidity/prev-day.\n", style=DIM)
        m15 = self.states.get("M15")
        if m15 and m15.atr:
            t.append(f"M15 ATR {m15.atr:.2f}\n", style=DIM)
        self.query_one("#structure", Static).update(t)

    def update_read(self):
        r = self.read
        t = Text()
        if r:
            t.append(f" {r.verdict} ", style=r.verdict_style)
            t.append("\n")
            col = BULL if r.bias == 1 else BEAR if r.bias == -1 else DIM
            ov = r.overall or 0
            filled = int(round(ov / 10))
            t.append(f"confluence {'█' * filled}{'░' * (10 - filled)} {ov:.0f}% ", style=col)
            t.append(f"(pass {r.passing:.0f}%)\n", style=BULL if r.passed else DIM)
            for ln in r.lines:
                t.append(f"· {ln}\n")
            for g in r.groups:
                if "agree" not in g:
                    continue
                t.append(f"· {g['category'][:10]:<10} {g['agree']}/{g['total']} ", style="#e8e8ff")
                t.append(f"{g['pct']:>3.0f}% x{g['weight']:g} ", style=BULL if g["pct"] >= r.passing else DIM)
                for it in g["items"]:
                    ch = "▲" if it["dir"] == 1 else "▼" if it["dir"] == -1 else "•"
                    col = BULL if it["dir"] == 1 else BEAR if it["dir"] == -1 else DIM
                    t.append(ch, style=col)
                t.append("\n")
        strats = [p for p in self.plugins if p.kind == "strategy" and p.enabled]
        if strats:
            t.append("\nSTRATEGIES\n", style="bold #ffff55")
            ctx = self.context()
            for p in strats:
                if p.error:
                    t.append(f"✗ {p.name}: error\n", style=BEAR)
                    continue
                try:
                    st = " · ".join(p.obj.status(ctx) or [])
                except Exception as e:
                    self.plugin_failed(p, e)
                    continue
                sig = self.last_signal.get(p.key)
                t.append(f"{p.name}", style="#55ffff")
                if st:
                    t.append(f"  {st}", style="#e8e8ff")
                if sig:
                    t.append(f"  last {'BUY' if sig.direction == 1 else 'SELL'} {fmt_t(sig.t, True)}",
                             style=BULL if sig.direction == 1 else BEAR)
                t.append("\n")
        acc = self.src.account()
        if acc:
            t.append(f"\nBAL {acc['balance']:.2f}  EQ {acc['equity']:.2f} {acc['currency']}\n", style="bold #ffff55")
        for p in self.src.positions()[:4]:
            pc = BULL if p["pnl"] >= 0 else BEAR
            t.append(f"{p['side']} {p['vol']:.2f} @{p['open']:.2f} SL {p['sl']:.2f} ", style="#e8e8ff")
            t.append(f"{p['pnl']:+.2f}\n", style=f"bold {pc}")
        self.query_one("#read", Static).update(t)

    def update_chart(self):
        tf = self.chart_tf
        bars = self.src.bars(tf)
        overlays = []
        ctx = None
        for p in self.plugins:
            if p.kind != "indicator" or not p.enabled or p.error:
                continue
            ctx = ctx or self.context()
            try:
                ov = p.obj.compute(bars, tf, ctx)
                if ov:
                    overlays.append(ov)
            except Exception as e:
                self.plugin_failed(p, e)
                self.update_chart_subtitle()
        bid = self.last_tick.bid if self.last_tick else None
        self.query_one("#chart", CandleChart).set_data(bars, overlays, bid)

    def update_chart_subtitle(self):
        on = [p.name for p in self.plugins if p.kind == "indicator" and p.enabled and not p.error]
        self.query_one("#chart").border_subtitle = " + ".join(on) if on else "plain  (p = add indicators)"

    def update_bars(self):
        src = self.src
        tk = self.last_tick
        top = Text(no_wrap=True, overflow="crop")
        top.append(" REYGUN TERM ", style="bold #ffff55 on #000000")
        top.append(f" {src.symbol} ", style="bold")
        top.append(f"│ {src.name} ")
        if tk:
            top.append(f"│ BID {tk.bid:.2f}  ASK {tk.ask:.2f}  SPR {tk.ask - tk.bid:.2f} ")
            top.append(f"│ {datetime.fromtimestamp(tk.t_msc / 1000, tz=timezone.utc):%Y-%m-%d %H:%M:%S} ")
        top.append("│ READ-ONLY ", style="bold")
        if src.paused:
            top.append(" PAUSED ", style="bold white on #aa0000")
        if self.recorder:
            top.append(f" ● REC {self.recorder.count:,} ", style="bold white on #aa0000")
        self.query_one("#topbar", Static).update(top)

        now = time.monotonic()
        while self.tick_times and now - self.tick_times[0] > 5:
            self.tick_times.popleft()
        tps = len(self.tick_times) / 5
        el = int(now - self.started)
        sb = f" x{src.speed:g} │ {tps:5.1f} ticks/s │ poll {src.poll_ms:5.1f}ms │ {self.total_ticks:,} ticks │ " \
             f"{el // 60}:{el % 60:02d} │ {src.status}"
        if isinstance(src, ReplaySource):
            sb += f" │ {src.progress * 100:5.1f}%"
        sb += " │ SPACE pause  +/- speed  1-5 TF  p plugins  r rec  q quit"
        self.query_one("#statusbar", Static).update(sb)

    # --------------------------------------------------------------- actions
    def action_pause(self):
        self.src.paused = not self.src.paused

    def action_faster(self):
        if self.src.live:
            return
        self.speed_i = min(len(SPEEDS) - 1, self.speed_i + 1)
        self.src.speed = SPEEDS[self.speed_i]

    def action_slower(self):
        if self.src.live:
            return
        self.speed_i = max(0, self.speed_i - 1)
        self.src.speed = SPEEDS[self.speed_i]

    def action_chart_tf(self, tf: str):
        self.chart_tf = tf
        self.query_one("#chart").border_title = f"1 CHART {tf}"
        self.update_chart()

    def action_plugins(self):
        def apply(selected):
            if selected is None:
                return
            sel = set(selected)
            for i, p in enumerate(self.plugins):
                on = i in sel
                if on != p.enabled:
                    p.enabled = on
                    p.error = None if on else p.error
                    self.log_event(Text(f"plugin {'ON ' if on else 'OFF'} {p.name}",
                                        style="#55ffff" if on else DIM))
            save_state(self.plugins)
            self.analyze_all()
            self.update_chart_subtitle()
            self.update_chart()
            self.update_read()
        self.push_screen(PluginMenu(self.plugins), apply)

    def action_record(self):
        if self.recorder:
            path, n = self.recorder.path, self.recorder.count
            self.recorder.close()
            self.recorder = None
            self.log_event(Text(f"recording saved: {path} ({n:,} ticks)", style="bold #ffff55"))
        else:
            self.recorder = Recorder(self.src.symbol)
            self.log_event(Text(f"recording -> {self.recorder.path}", style="bold #ffff55"))

    def action_clear(self):
        self.query_one("#events", RichLog).clear()

    def on_unmount(self):
        if self.recorder:
            self.recorder.close()
        self.src.close()


# --------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(description="REYGUN TERM - MT5 chart + analyser (read-only)")
    ap.add_argument("--mode", choices=["live", "demo", "replay"], default="live")
    ap.add_argument("--symbol", default="XAUUSD")
    ap.add_argument("--file", help="recorded CSV for --mode replay")
    ap.add_argument("--speed", type=int, default=1, help="replay/demo speed multiplier")
    ap.add_argument("--warmup-min", type=int, default=240, help="replay: minutes fed instantly before playback")
    ap.add_argument("--chart-tf", default="M5", choices=list(CHART_KEYS.values()))
    ap.add_argument("--record", action="store_true", help="start recording ticks immediately")
    ap.add_argument("--no-bell", action="store_true", help="no beep on strategy signals")
    ap.add_argument("--login", help="MT5 account (optional, uses logged-in terminal if omitted)")
    ap.add_argument("--password")
    ap.add_argument("--server")
    ap.add_argument("--mt5-path", help="path to terminal64.exe if you run several MT5 installs")
    a = ap.parse_args(argv)

    if a.mode == "live" and not a.mt5_path:
        try:                                    # same master terminal as set on SuperChart's Accounts page
            from trading import load_config
            mc = load_config()["master"]
            a.mt5_path = mc.get("path") or None
            if mc.get("symbol"):
                a.symbol = mc["symbol"]
        except Exception:
            pass
    try:
        if a.mode == "live":
            src = MT5Source(a.symbol, a.login, a.password, a.server, a.mt5_path)
        elif a.mode == "replay":
            if not a.file:
                ap.error("--file is required for replay")
            src = ReplaySource(a.file, a.warmup_min, a.symbol if a.symbol != "XAUUSD" else None)
        else:
            src = DemoSource(a.symbol)
    except RuntimeError as e:
        print(f"\n[REYGUN TERM] {e}\n")
        sys.exit(1)
    src.speed = a.speed if a.mode != "live" else 1
    ReygunTerm(src, chart_tf=a.chart_tf, record=a.record, bell=not a.no_bell).run()


if __name__ == "__main__":
    main()
