"""
Walk-forward testing: optimize on a TRAIN window, then test the chosen setup on the NEXT window it has never seen,
roll forward, repeat.

   window 1:  TRAIN [day 0 .. train)          TEST [train .. train+test)
   window 2:  TRAIN [test .. train+test)      TEST [train+test .. train+2*test)
   ...
For each window: the optimizer (same grid / objective / min trades as the Backtest page, with your costs) picks the
best in-sample setup on TRAIN (robust rows first); that setup is then simulated on TEST.
Result per window: period, IS trades / R / PF, OOS trades / R / PF / DD, parameter set.
Summary: total OOS R, % profitable windows, how often the chosen parameters changed.
Uses one Prepared (per-bar analyser reads computed once), so every window is causal by construction.
"""
from __future__ import annotations

import time

import backtest


def run(P, train_days=20, test_days=5, objective="total_r", min_trades=8, spread=0.3, lot=0.01, costs=None,
        opt_weights=False, max_combos=600, progress=None):
    bars = P.bars
    sec = backtest.TF_SECONDS[P.tf]
    per_day = 86400 / sec
    lo0, end = P.start, len(bars) - 1
    tr_n, te_n = int(train_days * per_day), int(test_days * per_day)
    if tr_n < 50 or te_n < 10:
        raise RuntimeError("train / test windows too short")
    starts = list(range(lo0, end - tr_n - te_n + 1, te_n))
    if not starts:
        raise RuntimeError(f"not enough history: need {train_days + test_days} days, have "
                           f"{(end - lo0) / per_day:.0f}")
    rows = []
    t0 = time.time()
    prev_setup = None
    changes = 0
    for w, a in enumerate(starts):
        tr_lo, tr_hi, te_hi = a, a + tr_n, min(end, a + tr_n + te_n)

        def prog(f, m, w=w):
            if progress:
                progress((w + f * 0.9) / len(starts), f"window {w + 1}/{len(starts)} · {m}")

        opt = backtest.optimize(P, objective, min_trades, 0.7, spread, lot, opt_weights, prog, max_combos, 5,
                                lo=tr_lo, hi=tr_hi, costs=costs, with_sensitivity=False)
        if not opt["rows"]:
            rows.append(dict(window=w + 1, train=[bars[tr_lo].t, bars[tr_hi - 1].t], test=[bars[tr_hi].t, bars[te_hi - 1].t],
                             setup=None, is_=None, oos=None, note="no setup with enough trades"))
            continue
        best = opt["rows"][0]
        c = best["setup"]
        is_st = backtest.stats(backtest.simulate(P, c, tr_lo, tr_hi, None, spread, False, costs), lot)
        oos_st = backtest.stats(backtest.simulate(P, c, tr_hi, te_hi, None, spread, False, costs), lot)
        key = tuple(sorted((k, str(v)) for k, v in c.items()))
        if prev_setup is not None and key != prev_setup:
            changes += 1
        prev_setup = key
        rows.append(dict(window=w + 1, train=[bars[tr_lo].t, bars[tr_hi - 1].t], test=[bars[tr_hi].t, bars[te_hi - 1].t],
                         setup=c, is_=is_st, oos=oos_st, robust_in_train=best["robust"]))
        if progress:
            progress((w + 1) / len(starts), f"window {w + 1}/{len(starts)} done ({time.time() - t0:.0f}s)")
    done = [r for r in rows if r.get("oos")]
    tot = round(sum(r["oos"]["total_r"] for r in done), 2)
    return dict(rows=rows, windows=len(rows), tested=len(done), total_oos_r=tot,
                oos_trades=sum(r["oos"]["trades"] for r in done),
                profitable_windows=sum(1 for r in done if r["oos"]["total_r"] > 0),
                pct_profitable=round(100 * sum(1 for r in done if r["oos"]["total_r"] > 0) / len(done), 1) if done else None,
                parameter_changes=changes, train_days=train_days, test_days=test_days, objective=objective)
