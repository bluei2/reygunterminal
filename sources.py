"""
Data sources for REYGUN TERM.

  MT5Source    - live, via the MetaTrader5 Python package (Windows VPS)
  ReplaySource - plays back a CSV recorded by this app (time_msc,bid,ask,volume)
  DemoSource   - synthetic gold-like ticks, para ma-test ang UI kahit walang MT5
"""
from __future__ import annotations

import csv
import os
import random
import time
from datetime import datetime

from engine import ALL_TFS, Aggregator, Bar, Tick


class BaseSource:
    name = "BASE"
    symbol = "XAUUSD"
    digits = 2
    live = False

    def __init__(self):
        self.speed = 1.0
        self.paused = False
        self.poll_ms = 0.0
        self.finished = False
        self.status = ""

    def poll(self) -> list[Tick]:
        return []

    def bars(self, tf: str) -> list[Bar]:
        return []

    def positions(self) -> list[dict]:
        return []

    def account(self) -> dict | None:
        return None

    def close(self):
        pass


# --------------------------------------------------------------------------
class _SimClockSource(BaseSource):
    """Shared by Demo and Replay: simulated clock with speed + pause."""

    def __init__(self):
        super().__init__()
        self.agg = Aggregator(ALL_TFS)
        self._wall = time.perf_counter()
        self.sim_msc = 0

    def _advance(self) -> int:
        now = time.perf_counter()
        dt = now - self._wall
        self._wall = now
        if self.paused or self.finished:
            return self.sim_msc
        self.sim_msc += int(dt * 1000 * self.speed)
        return self.sim_msc

    def bars(self, tf):
        return self.agg.bars[tf]

    def _feed(self, tk: Tick):
        mid = tk.bid
        self.agg.add(tk.t_msc // 1000, mid, tk.vol or 1.0)


class DemoSource(_SimClockSource):
    name = "DEMO"

    def __init__(self, symbol="XAUUSD", start_price=2650.0, days=30, seed=None):
        super().__init__()
        self.symbol = symbol
        self.rng = random.Random(seed)
        self.price = start_price
        self.drift = 0.0
        self.regime_left = 0
        now_s = int(time.time())
        t = now_s - days * 86400
        t -= t % 60
        # History: M1 OHLC random walk with trending regimes
        while t < now_s - 60:
            self._regime()
            o = self.price
            path = [o]
            for _ in range(6):
                self.price += self.drift / 6 + self.rng.gauss(0, 0.16)
                path.append(self.price)
            hi, lo, c = max(path), min(path), path[-1]
            for p, off in ((o, 0), (hi if c < o else lo, 15), (lo if c < o else hi, 35), (c, 59)):
                self.agg.add(t + off, round(p, 2))
            t += 60
        self.sim_msc = now_s * 1000
        self._last_tick_msc = self.sim_msc
        self.status = f"{days}d synthetic history"

    def _regime(self):
        if self.regime_left <= 0:
            self.drift = self.rng.choice([-1, 1]) * self.rng.uniform(0.02, 0.12)
            if self.rng.random() < 0.3:
                self.drift = 0.0
            self.regime_left = self.rng.randint(120, 900)   # minutes
        self.regime_left -= 1

    def poll(self):
        now = self._advance()
        out = []
        step_ms = 250
        n = 0
        while self._last_tick_msc + step_ms <= now and n < 2000:
            self._last_tick_msc += step_ms
            n += 1
            if self._last_tick_msc % 60000 < step_ms:
                self._regime()
            self.price += self.drift / 240 + self.rng.gauss(0, 0.045)
            spr = round(self.rng.uniform(0.12, 0.35), 2)
            bid = round(self.price, 2)
            tk = Tick(self._last_tick_msc, bid, round(bid + spr, 2), self.rng.choice([1, 1, 2, 3, 5]))
            self._feed(tk)
            out.append(tk)
        if n >= 2000:   # too far behind at high speed -> jump
            self._last_tick_msc = now
        return out


class ReplaySource(_SimClockSource):
    name = "REPLAY"

    def __init__(self, path: str, warmup_min: int = 240, symbol: str | None = None):
        super().__init__()
        self.path = path
        base = os.path.basename(path)
        self.symbol = symbol or base.split("_")[0]
        self.ticks: list[Tick] = []
        with open(path, newline="") as f:
            for row in csv.DictReader(f):
                try:
                    self.ticks.append(Tick(int(row["time_msc"]), float(row["bid"]),
                                           float(row["ask"]), float(row.get("volume") or 0)))
                except (KeyError, ValueError):
                    continue
        if not self.ticks:
            raise RuntimeError(f"No ticks in {path}")
        self.i = 0
        t0 = self.ticks[0].t_msc
        warm_end = t0 + warmup_min * 60_000
        while self.i < len(self.ticks) and self.ticks[self.i].t_msc < warm_end:
            self._feed(self.ticks[self.i])
            self.i += 1
        self.sim_msc = self.ticks[min(self.i, len(self.ticks) - 1)].t_msc
        self.start_msc = t0
        self.end_msc = self.ticks[-1].t_msc
        self.status = f"{len(self.ticks):,} ticks, warmup {warmup_min}m"

    def poll(self):
        now = self._advance()
        out = []
        while self.i < len(self.ticks) and self.ticks[self.i].t_msc <= now:
            tk = self.ticks[self.i]
            self._feed(tk)
            out.append(tk)
            self.i += 1
        if self.i >= len(self.ticks):
            self.finished = True
            self.status = "replay finished"
        return out

    @property
    def progress(self) -> float:
        span = max(1, self.end_msc - self.start_msc)
        return (self.sim_msc - self.start_msc) / span


# --------------------------------------------------------------------------
class MT5Source(BaseSource):
    name = "MT5 LIVE"
    live = True

    def __init__(self, symbol="XAUUSD", login=None, password=None, server=None, path=None,
                 n_bars=500):
        super().__init__()
        try:
            import MetaTrader5 as mt5
        except ImportError as e:
            raise RuntimeError("MetaTrader5 package not installed. Run: pip install MetaTrader5 "
                               "(Windows only)") from e
        self.mt5 = mt5
        self._init_kwargs = {}
        if path:
            self._init_kwargs["path"] = path
        if login:
            self._init_kwargs.update(login=int(login), password=password or "", server=server or "")
        self._connect()
        if not mt5.symbol_select(symbol, True):
            raise RuntimeError(f"Symbol {symbol} not found in Market Watch: {mt5.last_error()}")
        info = mt5.symbol_info(symbol)
        self.symbol = symbol
        self.digits = info.digits
        tick = mt5.symbol_info_tick(symbol)
        self._last_msc = int(tick.time_msc) if tick else 0
        self.n_bars = n_bars
        self.tfmap = {
            "M1": mt5.TIMEFRAME_M1, "M5": mt5.TIMEFRAME_M5, "M15": mt5.TIMEFRAME_M15, "M30": mt5.TIMEFRAME_M30,
            "H1": mt5.TIMEFRAME_H1, "H4": mt5.TIMEFRAME_H4, "D1": mt5.TIMEFRAME_D1,
        }
        self._bar_cache: dict[str, tuple[float, list[Bar]]] = {}
        self.path = path or ""
        self.login_error = ""
        acc = mt5.account_info()
        self.status = f"{acc.server} #{acc.login}" if acc else "connected"

    def check_login(self, expected_login) -> str:
        """If an account number is expected, make sure the terminal is logged in to exactly that account."""
        self.login_error = ""
        if expected_login:
            acc = self.mt5.account_info()
            if acc and int(acc.login) != int(expected_login):
                self.login_error = f"master MT5 is logged in as #{acc.login}, expected #{expected_login}"
        return self.login_error

    def reconnect(self, path=None, expected_login=None, symbol=None):
        """Switch the master to another MT5 terminal (folder) without restarting the app."""
        mt5 = self.mt5
        old_kwargs, old_symbol = dict(self._init_kwargs), self.symbol
        try:
            mt5.shutdown()
        except Exception:
            pass
        self._init_kwargs = {"path": path} if path else {}
        try:
            self._connect()
            sym = symbol or self.symbol
            if not mt5.symbol_select(sym, True):
                raise RuntimeError(f"symbol {sym} not found in this terminal's Market Watch")
        except RuntimeError:
            self._init_kwargs = old_kwargs          # go back to the terminal that worked
            try:
                mt5.shutdown()
                self._connect()
                mt5.symbol_select(old_symbol, True)
            except Exception:
                pass
            raise
        self.symbol = sym
        info = mt5.symbol_info(sym)
        self.digits = info.digits
        tick = mt5.symbol_info_tick(sym)
        self._last_msc = int(tick.time_msc) if tick else 0
        self._bar_cache.clear()
        self.path = path or ""
        acc = mt5.account_info()
        self.status = f"{acc.server} #{acc.login}" if acc else "connected"
        return self.check_login(expected_login)

    def _connect(self):
        if not self.mt5.initialize(**self._init_kwargs):
            raise RuntimeError(f"MT5 initialize failed: {self.mt5.last_error()}")

    def poll(self):
        if self.paused:
            return []
        mt5 = self.mt5
        t0 = time.perf_counter()
        arr = mt5.copy_ticks_from(self.symbol, max(0, self._last_msc // 1000), 5000,
                                  mt5.COPY_TICKS_INFO)
        self.poll_ms = (time.perf_counter() - t0) * 1000
        if arr is None:
            err = mt5.last_error()
            self.status = f"tick error {err}, reconnecting"
            try:
                mt5.shutdown()
                self._connect()
                mt5.symbol_select(self.symbol, True)
            except Exception:
                pass
            return []
        out = []
        for r in arr:
            msc = int(r["time_msc"])
            if msc <= self._last_msc:
                continue
            out.append(Tick(msc, float(r["bid"]), float(r["ask"]), float(r["volume"])))
        if out:
            self._last_msc = out[-1].t_msc
        return out

    def bars(self, tf):
        now = time.monotonic()
        cached = self._bar_cache.get(tf)
        if cached and now - cached[0] < 0.9:
            return cached[1]
        rates = self.mt5.copy_rates_from_pos(self.symbol, self.tfmap[tf], 0, self.n_bars)
        if rates is None:
            return cached[1] if cached else []
        bars = [Bar(int(r["time"]), float(r["open"]), float(r["high"]), float(r["low"]),
                    float(r["close"]), float(r["tick_volume"])) for r in rates]
        self._bar_cache[tf] = (now, bars)
        return bars

    def positions(self):
        ps = self.mt5.positions_get(symbol=self.symbol) or []
        out = []
        for p in ps:
            out.append(dict(side="BUY" if p.type == 0 else "SELL", vol=p.volume,
                            open=p.price_open, sl=p.sl, tp=p.tp, pnl=p.profit,
                            magic=p.magic, comment=p.comment))
        return out

    def account(self):
        a = self.mt5.account_info()
        if not a:
            return None
        return dict(balance=a.balance, equity=a.equity, margin_free=a.margin_free,
                    currency=a.currency)

    def close(self):
        try:
            self.mt5.shutdown()
        except Exception:
            pass


# --------------------------------------------------------------------------
class Recorder:
    """Writes live ticks to recordings/SYMBOL_YYYYmmdd_HHMMSS.csv for later replay."""

    def __init__(self, symbol: str, folder: str = "recordings"):
        os.makedirs(folder, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.path = os.path.join(folder, f"{symbol}_{stamp}.csv")
        self.f = open(self.path, "w", newline="")
        self.w = csv.writer(self.f)
        self.w.writerow(["time_msc", "bid", "ask", "volume"])
        self.count = 0
        self._last_flush = time.monotonic()

    def write(self, ticks: list[Tick]):
        for t in ticks:
            self.w.writerow([t.t_msc, t.bid, t.ask, t.vol])
        self.count += len(ticks)
        if time.monotonic() - self._last_flush > 2:
            self.f.flush()
            self._last_flush = time.monotonic()

    def close(self):
        self.f.flush()
        self.f.close()
