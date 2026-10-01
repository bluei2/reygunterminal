"""
Intermarket: markets that move WITH or AGAINST gold.

  DXY / USDX (US dollar index)  inverse   - dollar up = gold down (strongest link)
  EURUSD                        with      - mirror of the dollar
  XAGUSD (silver)               with      - sister metal
  USDJPY                        inverse   - dollar strength / yields
  US10Y (yields)                inverse   - real yields up = gold down (few brokers have it)
  US500 / NAS100, BTCUSD        auto      - risk sentiment; the sign comes from the measured correlation

For every symbol (read from the MASTER MT5 every 30 s, names found automatically for the broker's prefix/suffix):
  trend on the chosen TF (EMA20 vs EMA50 + price vs EMA20), change today, and the correlation of its bar returns
  with gold. "Implication for gold" = its trend x the relation (dollar DOWN -> gold BULLISH).
The majority of implications is the intermarket bias; the "Intermarket" indicator plugin can vote it into the score.
In demo / replay mode the symbols are simulated from the gold chart (for testing the screen only).
"""
from __future__ import annotations

import bisect
import json
import math
import os
import random
import threading
import time

import mt5_ops
from engine import Bar

HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(HERE, "intermarket_settings.json")
CURRENT = None            # the running Intermarket (plugins read it)

DEFAULT_ITEMS = [
    dict(base="DXY", relation=-1, symbol=""),
    dict(base="EURUSD", relation=1, symbol=""),
    dict(base="XAGUSD", relation=1, symbol=""),
    dict(base="USDJPY", relation=-1, symbol=""),
    dict(base="US10Y", relation=-1, symbol=""),
    dict(base="US500", relation=0, symbol=""),
    dict(base="BTCUSD", relation=0, symbol=""),
]
EXTRA_ALIASES = {"DXY": ["USDX", "USDIDX", "USDINDEX", "DOLLARINDEX"], "US10Y": ["UST10Y", "TNX", "USTNOTE", "US10YR", "TNOTE"],
                 "US500": ["SPX500", "SP500", "SPX", "USA500"], "USDJPY": [], "EURUSD": [], "XAGUSD": ["SILVER"],
                 "BTCUSD": ["BITCOIN"]}
for k, v in EXTRA_ALIASES.items():                     # let the symbol finder know these names too
    mt5_ops.ALIASES[k] = list(dict.fromkeys(mt5_ops.ALIASES.get(k, []) + v))


def load_settings():
    s = dict(tf="M15", corr_bars=96, items=[dict(x) for x in DEFAULT_ITEMS])
    try:
        with open(SETTINGS_FILE) as f:
            u = json.load(f)
        if u.get("tf") in ("M5", "M15", "M30", "H1", "H4"):
            s["tf"] = u["tf"]
        if isinstance(u.get("corr_bars"), (int, float)):
            s["corr_bars"] = max(20, min(280, int(u["corr_bars"])))
        if isinstance(u.get("items"), list):
            s["items"] = [dict(base=str(x.get("base", "")).upper().strip(), relation=int(x.get("relation", 0)),
                               symbol=str(x.get("symbol", "")).strip()) for x in u["items"] if x.get("base")]
    except (OSError, ValueError, TypeError):
        pass
    return s


def ema(vals, n):
    out, k, e = [], 2 / (n + 1), None
    for v in vals:
        e = v if e is None else v * k + e * (1 - k)
        out.append(e)
    return out


def trend(bars):
    """+1 / -1 / 0 from EMA20 vs EMA50 and price vs EMA20."""
    if len(bars) < 55:
        return 0, "not enough bars"
    c = [b.c for b in bars]
    e20, e50 = ema(c, 20), ema(c, 50)
    if e20[-1] > e50[-1] and c[-1] > e20[-1]:
        return 1, "EMA20 > EMA50, price above"
    if e20[-1] < e50[-1] and c[-1] < e20[-1]:
        return -1, "EMA20 < EMA50, price below"
    return 0, "mixed / ranging"


def correlation(a_bars, b_bars, n):
    """Pearson correlation of bar-to-bar returns on the bar times both series share (last n)."""
    bm = {b.t: b.c for b in b_bars}
    pairs = [(a.c, bm[a.t]) for a in a_bars if a.t in bm][-(n + 1):]
    if len(pairs) < 20:
        return None
    ra = [pairs[i][0] / pairs[i - 1][0] - 1 for i in range(1, len(pairs))]
    rb = [pairs[i][1] / pairs[i - 1][1] - 1 for i in range(1, len(pairs))]
    ma, mb = sum(ra) / len(ra), sum(rb) / len(rb)
    va = sum((x - ma) ** 2 for x in ra)
    vb = sum((y - mb) ** 2 for y in rb)
    if va <= 0 or vb <= 0:
        return None
    return sum((x - ma) * (y - mb) for x, y in zip(ra, rb)) / math.sqrt(va * vb)


class Intermarket:
    def __init__(self, hub):
        global CURRENT
        self.hub = hub
        self.settings = load_settings()
        self.lock = threading.Lock()
        self.data = {}            # base -> dict(symbol, bars, error)
        self.resolved = {}        # base -> broker symbol
        self.view_cache = None
        self.updated = 0
        self.running = True
        CURRENT = self
        threading.Thread(target=self._loop, daemon=True).start()

    # ------------------------------------------------------------------ settings
    def update_settings(self, new):
        s = self.settings
        if new.get("tf") in ("M5", "M15", "M30", "H1", "H4"):
            s["tf"] = new["tf"]
        if isinstance(new.get("items"), list):
            s["items"] = [dict(base=str(x.get("base", "")).upper().strip(), relation=int(x.get("relation", 0) or 0),
                               symbol=str(x.get("symbol", "")).strip()) for x in new["items"] if x.get("base")]
        try:
            with open(SETTINGS_FILE, "w") as f:
                json.dump(s, f, indent=2)
        except OSError:
            pass
        self.resolved = {}
        self.refresh()
        return dict(ok=True, settings=s)

    # ------------------------------------------------------------------ data
    def _loop(self):
        while self.running:
            try:
                self.refresh()
            except Exception as e:
                self.hub.note("error", f"intermarket: {e.__class__.__name__}: {e}")
            time.sleep(30)

    def refresh(self):
        src = self.hub.src
        tf = self.settings["tf"]
        out = {}
        if getattr(src, "live", False) and getattr(src, "mt5", None) is not None:
            mt5 = src.mt5
            for it in self.settings["items"]:
                base = it["base"]
                with self.hub.lock:
                    sym = it["symbol"] or self.resolved.get(base)
                    if not sym:
                        r = mt5_ops.find_symbols(mt5, base, 5)
                        sym = r.get("best") if r.get("ok") else None
                        if sym:
                            self.resolved[base] = sym
                    if not sym:
                        out[base] = dict(symbol=None, bars=[], error="not on this broker")
                        continue
                    mt5.symbol_select(sym, True)
                    rates = mt5.copy_rates_from_pos(sym, src.tfmap[tf], 0, 300)
                if rates is None or len(rates) == 0:
                    out[base] = dict(symbol=sym, bars=[], error=f"no {tf} data")
                    continue
                bars = [Bar(int(r["time"]), float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]),
                            float(r["tick_volume"])) for r in rates]
                out[base] = dict(symbol=sym, bars=bars, error="")
        else:
            out = self._simulate(tf)
        with self.lock:
            self.data = out
            self.updated = time.time()
            self.view_cache = None

    def _simulate(self, tf):
        """Demo / replay: build look-alike series from the gold chart so the screen can be tested."""
        gold = self.hub.src.bars(tf)[-300:]
        if len(gold) < 60:
            return {}
        rnd = random.Random(42)
        base_px = {"DXY": 104.0, "EURUSD": 1.08, "XAGUSD": 31.0, "USDJPY": 148.0, "US10Y": 4.2, "US500": 5600.0,
                   "BTCUSD": 64000.0}
        beta = {"DXY": -0.35, "EURUSD": 0.3, "XAGUSD": 1.3, "USDJPY": -0.25, "US10Y": -0.5, "US500": 0.1, "BTCUSD": 0.2}
        out = {}
        for it in self.settings["items"]:
            b = it["base"]
            p0, k = base_px.get(b, 100.0), beta.get(b, 0.0)
            px, bars = p0, []
            for i, g in enumerate(gold):
                ret = (g.c / gold[i - 1].c - 1) if i else 0.0
                px *= 1 + k * ret + rnd.gauss(0, 0.0006)
                o = bars[-1].c if bars else px
                bars.append(Bar(g.t, o, max(o, px) * 1.0002, min(o, px) * 0.9998, px, 1.0))
            out[b] = dict(symbol=f"{b} (sim)", bars=bars, error="")
        return out

    # ------------------------------------------------------------------ reading
    def _gold(self, tf, t_end=None):
        g = self.hub.src.bars(tf)
        if t_end is not None:
            k = bisect.bisect_right([b.t for b in g], t_end)
            g = g[:k]
        return g

    def read(self, t_end=None):
        """Rows + bias as of t_end (None = now). Only bars that started before t_end are used."""
        tf = self.settings["tf"]
        with self.lock:
            data = dict(self.data)
        gold = self._gold(tf, t_end)
        rows, bull, bear = [], 0, 0
        for it in self.settings["items"]:
            d = data.get(it["base"])
            row = dict(base=it["base"], relation=it["relation"], symbol=(d or {}).get("symbol"),
                       error=(d or {}).get("error", "loading…"))
            bars = (d or {}).get("bars") or []
            if t_end is not None:
                bars = bars[:bisect.bisect_right([b.t for b in bars], t_end)]
            if len(bars) >= 55:
                tdir, why = trend(bars)
                corr = correlation(gold, bars, self.settings["corr_bars"])
                rel = it["relation"]
                if rel == 0:                                    # auto: follow the measured correlation
                    rel = (1 if corr > 0 else -1) if corr is not None and abs(corr) >= 0.3 else 0
                imp = tdir * rel
                day0 = next((b.o for b in bars if b.t // 86400 == bars[-1].t // 86400), bars[0].o)
                row.update(price=bars[-1].c, chg=(bars[-1].c / day0 - 1) * 100 if day0 else 0.0, trend=tdir,
                           why=why, corr=None if corr is None else round(corr, 2), used_rel=rel, implication=imp,
                           error="")
                bull += imp > 0
                bear += imp < 0
            rows.append(row)
        n = sum(1 for r in rows if "implication" in r)
        bias = 1 if bull > bear else -1 if bear > bull else 0
        return dict(rows=rows, bull=bull, bear=bear, counted=n, bias=bias, tf=tf, updated=self.updated,
                    live=bool(getattr(self.hub.src, "live", False)), settings=self.settings)

    def view(self):
        if self.view_cache is None or time.time() - self.view_cache[0] > 5:
            self.view_cache = (time.time(), self.read())
        return self.view_cache[1]

    def stop(self):
        self.running = False
