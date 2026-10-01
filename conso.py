"""
Consolidation (range / box) detector. Used by:
  * the "Consolidation Box" indicator plugin (draws the boxes on the chart), and
  * the News Trader (places the buy stop above and the sell stop below the box before high-impact news).

Definition (Eugene's rule): a box is a range where price has made
  - at least `min_touches` (default 2) swing TOPS at the same level, and
  - at least `min_touches` (default 2) swing BOTTOMS at the same level,
where "same level" = within `tol_pips` (default 5 pips) of the box high / box low, and the touches
alternate (price went top -> bottom -> top ..., not all bottoms first and then all tops = a trend leg).
The box runs from its first touch up to NOW and every bar since then stayed inside it
(a bar that pokes out by more than the tolerance breaks the box).

Swing top = a bar whose high is the highest of `swing` bars on each side (swing bottom likewise with lows).
1 pip = `pip` in price (gold: 0.10 by default, so 5 pips = $0.50).
"""
from __future__ import annotations

DEFAULTS = dict(tf="M5", lookback=48, swing=2, min_touches=2, tol_pips=5.0, pip=0.10,
                min_bars=8, max_range=10.0, min_range=0.5)


def _pivots(bars, k):
    """Indexes of swing tops and swing bottoms (needs k bars on both sides, so the last k bars never qualify)."""
    tops, bots = [], []
    for i in range(k, len(bars) - k):
        h, l = bars[i].h, bars[i].l
        if all(h >= bars[j].h for j in range(i - k, i + k + 1) if j != i):
            tops.append(i)
        if all(l <= bars[j].l for j in range(i - k, i + k + 1) if j != i):
            bots.append(i)
    return tops, bots


def detect(bars, lookback=48, swing=2, min_touches=2, tol_pips=5.0, pip=0.10, min_bars=8,
           max_range=10.0, min_range=0.5, end=None, **_):
    """Is there a box that is still alive at bar index end-1 (default: the latest bar)?
    Returns dict(ok, high, low, range, t_from, t_to, tops, bottoms, why)."""
    if end is None:
        end = len(bars)
    lookback, swing, min_touches, min_bars = int(lookback), int(swing), int(min_touches), int(min_bars)
    base = max(0, end - lookback)
    win = bars[base:end]
    if len(win) < max(min_bars, 2 * swing + 3):
        return dict(ok=False, why="not enough bars")
    tol = tol_pips * pip
    tops, bots = _pivots(win, swing)
    best = None
    fail = "need 2 tops + 2 bottoms at the same level"
    # try every start (a swing point) from the oldest: the longest valid box wins
    starts = sorted(set(tops + bots))
    for s in starts:
        if len(win) - s < min_bars:
            break
        seg = win[s:]
        hi = max(b.h for b in seg)
        lo = min(b.l for b in seg)
        # the extremes must themselves be touched twice: count swing points near them inside the segment
        t_hits = [i for i in tops if i >= s and win[i].h >= hi - tol]
        b_hits = [i for i in bots if i >= s and win[i].l <= lo + tol]
        if len(t_hits) < min_touches or len(b_hits) < min_touches:
            continue
        # a real range bounces between the two sides: the touches must interleave (not all bottoms first and
        # all tops later = that is a move up, not a box)
        if not (max(t_hits) > min(b_hits) and max(b_hits) > min(t_hits)):
            fail = "tops and bottoms do not alternate (trend leg, not a range)"
            continue
        rng = hi - lo
        if rng > max_range:
            fail = f"box {rng:.2f} bigger than max {max_range:g}"
            continue
        if rng < min_range:
            fail = f"box {rng:.2f} smaller than min {min_range:g}"
            continue
        best = dict(ok=True, high=round(hi, 5), low=round(lo, 5), range=round(rng, 2),
                    t_from=win[s].t, t_to=win[-1].t, bars=len(seg),
                    tops=[round(win[i].h, 2) for i in t_hits], bottoms=[round(win[i].l, 2) for i in b_hits],
                    why=f"box {rng:.2f}: {len(t_hits)} tops / {len(b_hits)} bottoms within {tol_pips:g} pips "
                        f"over {len(seg)} bars")
        break
    if best:
        return best
    hi = max(b.h for b in win)
    lo = min(b.l for b in win)
    return dict(ok=False, high=hi, low=lo, range=round(hi - lo, 2), t_from=win[0].t, t_to=win[-1].t,
                why=f"{fail} (tops {len(tops)}, bottoms {len(bots)} in last {len(win)} bars)")


def scan(bars, step=None, **p):
    """Boxes through the history (for drawing): detect at each point, keep a box while price stays inside it."""
    q = dict(DEFAULTS)
    q.update(p)
    tol = q["tol_pips"] * q["pip"]
    step = step or 3
    boxes = []
    n = len(bars)
    i = int(q["min_bars"]) + 2 * int(q["swing"]) + 1
    while i <= n:
        r = detect(bars, end=i, **q)
        if r["ok"]:
            j = i
            while j < n and bars[j].h <= r["high"] + tol and bars[j].l >= r["low"] - tol:
                j += 1                                   # still inside the box
            r["t_to"] = bars[j - 1].t
            r["live"] = j >= n
            boxes.append(r)
            i = j + int(q["min_bars"])                   # next box must start after this one broke
        else:
            i += step
    return boxes


def breakout_signals(bars, confirm="close", buffer_pips=5.0, sl_mode="box", sl_fixed=5.0, max_sl=10.0, rr=2.0,
                     ema_len=0, confirm_bars=3, tp_source="rr", trail="none", min_rr=1.0,
                     atr_len=14, atr_sl=1.5, atr_tp1=2.0, atr_tp2=3.0, atr_trail=3.0, **p):
    """Possible BUY / SELL from every box: the first break out of the box after it formed.
      confirm = "close": a bar CLOSES beyond box edge +/- buffer (entry = that close)
      confirm = "touch": price TOUCHES box edge +/- buffer (entry = that level, like a stop order)
    SL = other side of the box -/+ buffer (capped at max_sl) or fixed $; TP = SL distance x rr.
    ema_len > 0: only BUY above the EMA and SELL below it (trend filter).
    tp_source: "rr" (SL x rr) | "vwap" (next VWAP band ahead) | "ob" (next opposite order block) | "atr"
               (atr_tp1 / atr_tp2 x ATR) | "both" (vwap+ob) | "all" | combos like "vwap+atr"
               - only levels at least min_rr x the SL distance away count; falls back to rr when none is found.
    sl_mode:   "box" | "fixed" | "vwap" (beyond the VWAP level behind entry) | "ob" (beyond the order block behind)
               | "atr" (atr_sl x ATR)
               - falls back to the box when no level is found.
    trail:     "none" | "vwap" (the stop follows VWAP once VWAP is on the profit side of the stop)
               | "atr" (chandelier: highest high since entry - atr_trail x ATR for a BUY).
    Then the following bars are replayed: TP hit / SL hit (both in one bar = SL, to be safe) / still open.
    Returns a list of dicts (oldest first)."""
    q = dict(DEFAULTS)
    q.update(p)
    tol = q["tol_pips"] * q["pip"]
    buf = buffer_pips * q["pip"]
    ema_vals = None
    if ema_len and ema_len > 1 and len(bars) > ema_len:
        k, e, ema_vals = 2 / (ema_len + 1), None, []
        for b in bars:
            e = b.c if e is None else b.c * k + e * (1 - k)
            ema_vals.append(e)
    index = {b.t: i for i, b in enumerate(bars)}
    import exits
    vw = bands = obs = atr_vals = None
    ts = exits.sources(tp_source) | {sl_mode, trail}
    if "vwap" in ts:
        vw, bands = exits.vwap_bands(bars)
    if "ob" in ts:
        obs = exits.order_blocks(bars)
    if "atr" in ts:
        atr_vals = exits.atr(bars, int(atr_len))
    akw = dict(atr_vals=atr_vals, atr_sl=atr_sl, atr_tp1=atr_tp1, atr_tp2=atr_tp2, atr_trail=atr_trail, bars=bars)
    out = []
    for box in scan(bars, **q):
        if box.get("live"):
            continue
        j = index.get(box["t_to"], -1) + 1              # first bar that left the box
        sig = None
        for i in range(j, min(len(bars), j + max(1, int(confirm_bars)))):
            b = bars[i]
            up_lvl, dn_lvl = box["high"] + buf, box["low"] - buf
            if confirm == "touch":
                up, dn = b.h >= up_lvl, b.l <= dn_lvl
                if up and dn:
                    break                                # both sides in one bar: no clean signal
                if up or dn:
                    sig = (i, 1 if up else -1, up_lvl if up else dn_lvl)
            else:
                if b.c > up_lvl:
                    sig = (i, 1, b.c)
                elif b.c < dn_lvl:
                    sig = (i, -1, b.c)
            if sig:
                break
        if not sig:
            continue
        i, side, entry = sig
        if ema_vals is not None and (entry - ema_vals[i]) * side < 0:
            continue                                     # against the trend filter
        other = box["low"] - buf if side == 1 else box["high"] + buf
        sl_name = "box"
        plan = None
        if vw is not None or obs is not None or atr_vals is not None:
            plan = exits.exit_plan(side, entry, i, vw, bands, obs, tp_source=tp_source, **akw,
                                   sl_source=sl_mode if sl_mode in ("vwap", "ob", "atr") else "none",
                                   buffer=buf, min_dist=max(tol, 0.3), max_sl=max_sl)
        if sl_mode in ("vwap", "ob", "atr") and plan and plan["sl"]:
            sl_d, sl_name = abs(entry - plan["sl"][0]), plan["sl"][1]
        elif sl_mode == "fixed":
            sl_d, sl_name = sl_fixed, "fixed"
        else:
            sl_d = abs(entry - other)
        if max_sl > 0:
            sl_d = min(sl_d, max_sl)
        if sl_d <= 0:
            continue
        sl = entry - side * sl_d
        tp_name = f"{rr:g}R"
        if tp_source != "rr" and plan is not None:
            plan = exits.exit_plan(side, entry, i, vw, bands, obs, tp_source=tp_source, sl_source="none", **akw,
                                   buffer=buf, min_dist=max(tol, 0.3, min_rr * sl_d), max_sl=max_sl)
        if tp_source != "rr" and plan and plan["tp1"]:
            tp, tp_name = plan["tp1"]
        else:
            tp = entry + side * sl_d * rr
        result, t_exit, exit_px = "open", None, None
        start = i + 1                                    # replay from the NEXT bar (no look-inside-the-bar guess)
        cur_sl = sl
        for k2 in range(start, len(bars)):
            b = bars[k2]
            hit_sl = (b.l <= cur_sl) if side == 1 else (b.h >= cur_sl)
            hit_tp = (b.h >= tp) if side == 1 else (b.l <= tp)
            if hit_sl:
                trailed = (cur_sl - sl) * side > 1e-9
                result, t_exit, exit_px = ("TRAIL" if trailed else "SL"), b.t, cur_sl
                break
            if hit_tp:
                result, t_exit, exit_px = "TP", b.t, tp
                break
            new_sl = None
            if trail == "atr" and atr_vals is not None and atr_vals[k2] is not None:
                seg = bars[i:k2 + 1]                     # chandelier since the entry
                new_sl = (max(x.h for x in seg) - atr_trail * atr_vals[k2]) if side == 1 else \
                         (min(x.l for x in seg) + atr_trail * atr_vals[k2])
            elif trail == "vwap" and vw is not None and vw[k2] is not None:
                new_sl = vw[k2] - side * buf
            if new_sl is not None:
                if (new_sl - cur_sl) * side > 0 and (b.c - new_sl) * side > 0:
                    cur_sl = new_sl                      # only ever tightens, and only behind price
        r = round((exit_px - entry) * side / sl_d, 2) if exit_px is not None else None
        out.append(dict(t=bars[i].t, side=side, entry=round(entry, 2), sl=round(sl, 2), tp=round(tp, 2),
                        sl_d=round(sl_d, 2), rr=round(abs(tp - entry) / sl_d, 2), result=result, t_exit=t_exit,
                        exit=round(exit_px, 2) if exit_px is not None else None, cur_sl=round(cur_sl, 2),
                        box=box, r=r, tp_name=tp_name, sl_name=sl_name))
    return out


def signal_stats(sigs):
    done = [s for s in sigs if s["result"] != "open"]
    w = sum(1 for s in done if (s["r"] or 0) > 0)
    return dict(signals=len(sigs), closed=len(done), wins=w, losses=len(done) - w,
                win_rate=round(100 * w / len(done), 1) if done else None,
                total_r=round(sum(s["r"] for s in done), 1) if done else 0.0)
