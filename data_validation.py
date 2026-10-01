"""
Data validation: checked before every decision (decision engine, risk engine, master gate).

Nothing here repairs or substitutes data. A problem is reported with a code and the caller BLOCKS:
  SYMBOL_MISMATCH      chart symbol differs from the master account's symbol
  NO_TICK              no price received yet
  TICK_INVALID         bid/ask <= 0, NaN, or ask < bid
  SPREAD_INVALID       spread absurdly large compared with the recent median spread
  BARS_MISSING         a structure timeframe has too few bars to analyse
  BAR_INVALID          an OHLC bar is inconsistent (high < max(open, close), low > min(open, close), NaN, <= 0)
  BAR_TIME_ORDER       bar times not strictly increasing
  BAR_FROM_FUTURE      the forming bar starts after the latest tick (clock / feed problem)
  BAR_INCOMPLETE       the last CLOSED bar is older than 3 bar lengths while ticks are arriving (feed stalled)
Market status (open / closed) and broker connection are checked by risk_engine (MARKET_CLOSED,
MASTER_DISCONNECTED) and execution_guard (NOT_CONNECTED, BROKER_DATA_STALE).
"""
from __future__ import annotations

import math

from engine import TF_SECONDS

CHECK_TFS = ("M1", "M5", "M15", "H1", "H4")
MIN_BARS = {"M1": 10, "M5": 30, "M15": 30, "H1": 20, "H4": 10}
LOOKBACK = 60


def _bad(x):
    return x is None or not isinstance(x, (int, float)) or math.isnan(x) or math.isinf(x) or x <= 0


def issue(code, text, tf=None):
    return dict(code=code, text=text, tf=tf)


def validate_tick(tk, spreads=None):
    """tk: engine.Tick. spreads: recent spreads (for the sanity ratio)."""
    if tk is None:
        return [issue("NO_TICK", "no price received yet")]
    out = []
    if _bad(tk.bid) or _bad(tk.ask):
        return [issue("TICK_INVALID", f"invalid price bid={tk.bid} ask={tk.ask}")]
    if tk.ask < tk.bid:
        out.append(issue("TICK_INVALID", f"ask {tk.ask} below bid {tk.bid}"))
    sp = tk.ask - tk.bid
    if spreads:
        med = sorted(spreads)[len(spreads) // 2]
        if med > 0 and sp > 20 * med and sp > 1.0:
            out.append(issue("SPREAD_INVALID", f"spread {sp:.2f} is {sp / med:.0f}x the recent median {med:.2f}"))
    return out


def validate_bars(tf, bars, last_tick_t=None, min_bars=None):
    """Checks the last LOOKBACK bars of one timeframe (cheap: called about once per second)."""
    need = MIN_BARS.get(tf, 10) if min_bars is None else min_bars
    if not bars or len(bars) < need:
        return [issue("BARS_MISSING", f"{tf}: {len(bars or [])} bars (need {need})", tf)]
    out = []
    tail = bars[-LOOKBACK:]
    prev_t = None
    for b in tail:
        if any(_bad(v) for v in (b.o, b.h, b.l, b.c)):
            out.append(issue("BAR_INVALID", f"{tf} bar {b.t}: invalid price", tf))
            break
        if b.h + 1e-9 < max(b.o, b.c) or b.l - 1e-9 > min(b.o, b.c) or b.h + 1e-9 < b.l:
            out.append(issue("BAR_INVALID", f"{tf} bar {b.t}: OHLC inconsistent (o={b.o} h={b.h} l={b.l} c={b.c})", tf))
            break
        if prev_t is not None and b.t <= prev_t:
            out.append(issue("BAR_TIME_ORDER", f"{tf}: bar times not increasing at {b.t}", tf))
            break
        prev_t = b.t
    sec = TF_SECONDS.get(tf, 60)
    if last_tick_t:
        if bars[-1].t > last_tick_t + sec:
            out.append(issue("BAR_FROM_FUTURE", f"{tf}: forming bar {bars[-1].t} is after the last tick {last_tick_t}", tf))
        if len(bars) >= 2 and last_tick_t - bars[-2].t > 3 * sec + sec and tf in ("M1", "M5"):
            out.append(issue("BAR_INCOMPLETE", f"{tf}: last closed bar is {(last_tick_t - bars[-2].t) // 60} min old", tf))
    return out


def validate(src, tick, master_symbol=None, spreads=None, tfs=CHECK_TFS):
    """Everything for one decision. -> dict(ok, issues)."""
    issues = []
    if master_symbol and getattr(src, "symbol", None) and str(master_symbol) != str(src.symbol):
        issues.append(issue("SYMBOL_MISMATCH", f"chart {src.symbol} vs master {master_symbol}"))
    issues += validate_tick(tick, spreads)
    t_tick = tick.t_msc // 1000 if tick is not None and not _bad(getattr(tick, "t_msc", None)) else None
    for tf in tfs:
        try:
            bars = src.bars(tf)
        except Exception as e:                            # a data error is reported, never hidden
            issues.append(issue("BARS_MISSING", f"{tf}: {e.__class__.__name__}: {e}", tf))
            continue
        # bar times are broker server time; tick times too, so they compare directly
        issues += validate_bars(tf, bars, t_tick)
    return dict(ok=not issues, issues=issues)
