"""
Order flow from MT5 ticks (footprint, delta, imbalance, absorption).

IMPORTANT: MT5 gold is a CFD - there is no exchange volume and no real "who hit the ask". Every tick is classified
with the TICK RULE (the standard for FX/CFD order flow): bid up = BUY, bid down = SELL, unchanged = same as the
previous tick. Volume = the tick's volume if the broker sends one, else 1 per tick (tick count). It shows the
aggression / pressure of price changes, not real contracts like GC futures.

Per bar (M1 / M5 / M15 / M30 / H1) and per price row (row size in $):
  buy, sell, delta = buy - sell, POC (row with most volume)
  imbalance    BUY  row p: buy[p] >= ratio x sell[p - row]  (diagonal, like ATAS / Sierra)
               SELL row p: sell[p] >= ratio x buy[p + row]
  stacked      >= 3 imbalances of the same side in a row -> strong zone (support for buy, resistance for sell)
  absorption   big volume (>= 1.8 x avg) but small range (<= 0.7 x avg) - one side is being absorbed
               delta very negative but bar closes up -> sellers absorbed (bullish); the opposite -> bearish
  delta divergence  5-bar high with strong selling (delta <= -30% of volume) = bearish; 5-bar low with strong buying = bullish
Cumulative delta resets every server day.
"""
from __future__ import annotations

import bisect
import random
import threading
import time
from collections import deque

from engine import TF_SECONDS

CURRENT = None
TFS = ("M1", "M5", "M15", "M30", "H1")
AUTO_ROW = {"M1": 0.10, "M5": 0.25, "M15": 0.50, "M30": 1.00, "H1": 1.00}


class OrderFlow:
    def __init__(self, hub, backfill_hours=12, max_ticks=600_000):
        global CURRENT
        self.hub = hub
        self.ticks = deque(maxlen=max_ticks)      # (t_sec float, price, side +1/-1, vol)
        self.last_bid = None
        self.last_side = 1
        self.rows = dict(AUTO_ROW)
        self.params = dict(ratio=3.0, min_imb_vol=5, imb_min_share=0.3, stacked=3, absorb_vol=1.8, absorb_range=0.7)
        self.synthetic = False
        self.backfill_hours = backfill_hours
        self.status = "backfilling…"
        self.lock = threading.Lock()
        self._fp = {tf: {} for tf in TFS}          # tf -> {bar_t: {"lv": {bucket: [buy, sell]}, o,h,l,c}}
        CURRENT = self
        threading.Thread(target=self._backfill, daemon=True).start()

    # ------------------------------------------------------------------ input
    def _classify(self, bid):
        if self.last_bid is None or bid == self.last_bid:
            side = self.last_side
        else:
            side = 1 if bid > self.last_bid else -1
        self.last_bid, self.last_side = bid, side
        return side

    def add(self, ticks):
        """Called by the hub with every new batch of ticks."""
        with self.lock:
            for tk in ticks:
                side = self._classify(tk.bid)
                vol = tk.vol if tk.vol and tk.vol > 0 else 1.0
                rec = (tk.t_msc / 1000.0, tk.bid, side, vol)
                self.ticks.append(rec)
                self._add_fp(rec)

    def _add_fp(self, rec):
        t, px, side, vol = rec
        for tf in TFS:
            sec = TF_SECONDS[tf]
            bt = int(t) - int(t) % sec
            bars = self._fp[tf]
            b = bars.get(bt)
            if b is None:
                b = bars[bt] = dict(lv={}, o=px, h=px, l=px, c=px)
                if len(bars) > 700:
                    for k in sorted(bars)[:len(bars) - 600]:
                        del bars[k]
            b["h"], b["l"], b["c"] = max(b["h"], px), min(b["l"], px), px
            row = self.rows[tf]
            key = round(int(px / row + 1e-9) * row, 6)
            cell = b["lv"].setdefault(key, [0.0, 0.0])
            cell[0 if side > 0 else 1] += vol

    def _rebuild(self, tf):
        self._fp[tf] = {}
        for rec in self.ticks:
            t, px, side, vol = rec
            sec = TF_SECONDS[tf]
            bt = int(t) - int(t) % sec
            b = self._fp[tf].get(bt)
            if b is None:
                b = self._fp[tf][bt] = dict(lv={}, o=px, h=px, l=px, c=px)
            b["h"], b["l"], b["c"] = max(b["h"], px), min(b["l"], px), px
            row = self.rows[tf]
            key = round(int(px / row + 1e-9) * row, 6)
            cell = b["lv"].setdefault(key, [0.0, 0.0])
            cell[0 if side > 0 else 1] += vol

    def set_row(self, tf, row):
        if tf in TFS and row and row > 0:
            with self.lock:
                self.rows[tf] = round(float(row), 4)
                self._rebuild(tf)
        return self.rows

    # ------------------------------------------------------------------ backfill
    def _backfill(self):
        src = self.hub.src
        try:
            if getattr(src, "live", False) and getattr(src, "mt5", None) is not None:
                mt5 = src.mt5
                with self.hub.lock:
                    tk = mt5.symbol_info_tick(src.symbol)
                    end = int(tk.time_msc // 1000) if tk else int(time.time())
                    arr = mt5.copy_ticks_from(src.symbol, max(0, end - self.backfill_hours * 3600), 2_000_000,
                                              mt5.COPY_TICKS_INFO)
                if arr is None or len(arr) == 0:
                    self.status = "no tick history from MT5 (only live ticks from now on)"
                    return
                from engine import Tick
                hist = [Tick(int(r["time_msc"]), float(r["bid"]), float(r["ask"]), float(r["volume"])) for r in arr]
                self._merge_history(hist)
                self.status = f"{len(hist):,} ticks from MT5 ({self.backfill_hours}h) + live"
            else:
                self._simulate_history()
        except Exception as e:
            self.status = f"backfill failed: {e.__class__.__name__}: {e}"

    def _merge_history(self, hist):
        with self.lock:
            live = list(self.ticks)
            first_live = live[0][0] if live else float("inf")
            self.ticks.clear()
            self.last_bid, self.last_side = None, 1
            for tf in TFS:
                self._fp[tf] = {}
            for tk in hist:
                if tk.t_msc / 1000.0 >= first_live:
                    break
                side = self._classify(tk.bid)
                rec = (tk.t_msc / 1000.0, tk.bid, side, tk.vol if tk.vol and tk.vol > 0 else 1.0)
                self.ticks.append(rec)
                self._add_fp(rec)
            for rec in live:
                self.ticks.append(rec)
                self._add_fp(rec)
            if live:
                self.last_bid = live[-1][1]

    def _simulate_history(self):
        """Demo / replay: build ticks inside the recent M1 bars (so the screens have something to show)."""
        m1 = self.hub.src.bars("M1")[-self.backfill_hours * 60:]
        rnd = random.Random(11)
        from engine import Tick
        hist = []
        for b in m1[:-1]:
            n = rnd.randint(25, 70)
            path = [b.o]
            first, second = (b.h, b.l) if rnd.random() < 0.5 else (b.l, b.h)
            for k in range(1, n):
                f = k / n
                target = first if f < 0.35 else second if f < 0.7 else b.c
                path.append(path[-1] + (target - path[-1]) * rnd.uniform(0.2, 0.6) + rnd.gauss(0, 0.03))
            path[-1] = b.c
            for k, px in enumerate(path):
                hist.append(Tick(int((b.t + 60 * k / n) * 1000), round(px, 2), round(px + 0.25, 2),
                                 rnd.choice([1, 1, 2, 3, 5])))
        self.synthetic = True
        self._merge_history(hist)
        self.status = f"SIMULATED ticks ({len(hist):,}) + demo live"

    # ------------------------------------------------------------------ reading
    def bar_stats(self, tf, t_end=None):
        """Per-bar summary for the chart: [(bar_t, buy, sell, delta, vol, poc, hi, lo, o, c)] sorted by time."""
        with self.lock:
            items = sorted(self._fp[tf].items())
        out = []
        sec = TF_SECONDS[tf]
        for bt, b in items:
            if t_end is not None and bt + sec > t_end:
                break
            buy = sum(v[0] for v in b["lv"].values())
            sell = sum(v[1] for v in b["lv"].values())
            poc = max(b["lv"].items(), key=lambda kv: kv[1][0] + kv[1][1])[0] if b["lv"] else None
            out.append((bt, buy, sell, buy - sell, buy + sell, poc, b["h"], b["l"], b["o"], b["c"]))
        return out

    def footprint(self, tf, n=20, t_end=None):
        """Full footprint of the last n bars + events, for the Order Flow page.
        t_end: only bars that had closed by then (bar.t + tf seconds <= t_end) - used by the score history."""
        p = self.params
        row = self.rows[tf]
        sec = TF_SECONDS[tf]
        with self.lock:
            items = sorted(self._fp[tf].items())
            if t_end is not None:
                items = [x for x in items if x[0] + sec <= t_end]
            items = items[-(n + 25):]
            items = [(bt, dict(lv=dict(b["lv"]), o=b["o"], h=b["h"], l=b["l"], c=b["c"])) for bt, b in items]
        stats = []
        for bt, b in items:
            buy = sum(v[0] for v in b["lv"].values())
            sell = sum(v[1] for v in b["lv"].values())
            stats.append((bt, buy, sell, b["h"] - b["l"]))
        bars, events = [], []
        cum, day = 0.0, None
        with self.lock:
            all_items = sorted(self._fp[tf].items())
        if t_end is not None:
            all_items = [x for x in all_items if x[0] + sec <= t_end]
        cum_by_t = {}
        for bt, b in all_items:                      # cumulative delta, reset each server day
            if bt // 86400 != day:
                day, cum = bt // 86400, 0.0
            cum += sum(v[0] - v[1] for v in b["lv"].values())
            cum_by_t[bt] = cum
        for idx, (bt, b) in enumerate(items):
            lv = b["lv"]
            keys = sorted(lv)
            buy = sum(v[0] for v in lv.values())
            sell = sum(v[1] for v in lv.values())
            vol, delta = buy + sell, buy - sell
            poc = max(lv.items(), key=lambda kv: kv[1][0] + kv[1][1])[0] if lv else None
            rows = []
            imb_b, imb_s = set(), set()
            big = max((max(v) for v in lv.values()), default=0)
            min_v = max(p["min_imb_vol"], p["imb_min_share"] * big)     # tiny cells never count as imbalance
            for k in keys:
                bv, sv = lv[k]
                below = lv.get(round(k - row, 6), [0, 0])[1]
                above = lv.get(round(k + row, 6), [0, 0])[0]
                if bv >= min_v and bv >= p["ratio"] * max(below, 1.0):
                    imb_b.add(k)
                if sv >= min_v and sv >= p["ratio"] * max(above, 1.0):
                    imb_s.add(k)
                rows.append([k, round(bv, 1), round(sv, 1), k in imb_b, k in imb_s])
            ev = []
            for side, sset in ((1, imb_b), (-1, imb_s)):
                run = []
                for k in keys + [None]:
                    if k is not None and k in sset and (not run or abs(k - run[-1] - row) < 1e-6):
                        run.append(k)
                        continue
                    if len(run) >= p["stacked"]:
                        ev.append(dict(kind="stacked", side=side, lo=run[0], hi=run[-1] + row,
                                       text=f"stacked {'BUY' if side > 0 else 'SELL'} imbalance x{len(run)} "
                                            f"{run[0]:.2f}-{run[-1] + row:.2f}"))
                    run = [k] if k is not None and k in sset else []
            prev = stats[max(0, idx - 20):idx]
            if len(prev) >= 10 and vol > 0:
                av = sum(x[1] + x[2] for x in prev) / len(prev)
                ar = sum(x[3] for x in prev) / len(prev)
                rng = b["h"] - b["l"]
                if av > 0 and ar > 0 and vol >= p["absorb_vol"] * av and rng <= p["absorb_range"] * ar:
                    if delta < -0.2 * vol and b["c"] >= b["o"]:
                        ev.append(dict(kind="absorption", side=1, text=f"sellers absorbed (Δ {delta:+.0f}, "
                                                                        f"vol {vol / av:.1f}×) → bullish"))
                    elif delta > 0.2 * vol and b["c"] <= b["o"]:
                        ev.append(dict(kind="absorption", side=-1, text=f"buyers absorbed (Δ {delta:+.0f}, "
                                                                         f"vol {vol / av:.1f}×) → bearish"))
                    else:
                        ev.append(dict(kind="absorption", side=0, text=f"high volume, small range ({vol / av:.1f}×)"))
            look = items[max(0, idx - 5):idx]
            if len(look) == 5 and vol > 0:
                hh = max(x[1]["h"] for x in look)
                ll = min(x[1]["l"] for x in look)
                if b["h"] > hh and delta <= -0.3 * vol:
                    ev.append(dict(kind="divergence", side=-1, text=f"5-bar high with strong selling (Δ {delta:+.0f}) → bearish"))
                if b["l"] < ll and delta >= 0.3 * vol:
                    ev.append(dict(kind="divergence", side=1, text=f"5-bar low with strong buying (Δ {delta:+.0f}) → bullish"))
            bars.append(dict(t=bt, o=b["o"], h=b["h"], l=b["l"], c=b["c"], buy=round(buy, 1), sell=round(sell, 1),
                             delta=round(delta, 1), vol=round(vol, 1), poc=poc, rows=rows, events=ev,
                             cum=round(cum_by_t.get(bt, 0.0), 1)))
            for e in ev:
                events.append(dict(e, t=bt))
        bars = bars[-n:]
        first = bars[0]["t"] if bars else 0
        with self.lock:
            cover = self.ticks[0][0] if self.ticks else None
        return dict(tf=tf, row=row, bars=bars, events=[e for e in events if e["t"] >= first][::-1],
                    status=self.status, synthetic=self.synthetic, coverage_from=cover, params=self.params,
                    rows=self.rows)

    def events_for_chart(self, tf, n=300):
        """Absorption / stacked / divergence events of the last n bars (for chart markers)."""
        fp = self.footprint(tf, n)
        return fp["bars"], fp["events"]
