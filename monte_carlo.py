"""
Monte Carlo STRESS TEST of a list of trade results (R multiples). It is not a prediction.

Each run: the same trades in a random order (so a different losing streak / drawdown path), optionally with extra
slippage per trade (uniform 0..slip_r R) and with a random share of trades skipped (skip_pct, e.g. missed entries).
Returns percentiles of total R, max drawdown (R) and the longest losing streak.
"""
from __future__ import annotations

import random


def _path(rs):
    eq = peak = dd = 0.0
    streak = best = 0
    for r in rs:
        eq += r
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
        streak = streak + 1 if r <= 0 else 0
        best = max(best, streak)
    return eq, dd, best


def _pct(vals, p):
    if not vals:
        return None
    v = sorted(vals)
    k = min(len(v) - 1, max(0, int(round(p / 100 * (len(v) - 1)))))
    return round(v[k], 2)


def run(rs, n=1000, slip_r=0.0, skip_pct=0.0, seed=7):
    rs = [float(r) for r in rs if r is not None]
    if len(rs) < 5:
        raise RuntimeError("need at least 5 trades")
    rnd = random.Random(seed)
    tot, dds, streaks = [], [], []
    for _ in range(int(n)):
        x = rs[:]
        rnd.shuffle(x)
        if skip_pct:
            x = [r for r in x if rnd.random() >= skip_pct / 100]
        if slip_r:
            x = [r - rnd.uniform(0, slip_r) for r in x]
        e, d, s = _path(x)
        tot.append(e)
        dds.append(d)
        streaks.append(s)
    base = _path(rs)
    return dict(runs=int(n), trades=len(rs), slip_r=slip_r, skip_pct=skip_pct,
                actual=dict(total_r=round(base[0], 2), max_dd_r=round(base[1], 2), max_losing_streak=base[2]),
                total_r=dict(p5=_pct(tot, 5), median=_pct(tot, 50), p95=_pct(tot, 95)),
                max_dd_r=dict(median=_pct(dds, 50), p95=_pct(dds, 5), worst=round(min(dds), 2)),
                losing_streak=dict(median=_pct(streaks, 50), p95=_pct(streaks, 95), max=max(streaks)),
                prob_loss=round(100 * sum(1 for t in tot if t < 0) / len(tot), 1),
                note="stress test, not a prediction")
