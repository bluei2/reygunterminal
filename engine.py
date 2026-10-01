"""
REYGUN TERM - deterministic market-structure engine.

Walang AI/ML dito. Pure rules:
  - Fractal swings (N bars left / N bars right)
  - BOS / CHoCH on candle CLOSE beyond the last swing
  - Liquidity sweep = wick beyond swing, close back inside
  - FVG (3-candle imbalance), tracked until fully filled
  - Premium / discount inside the current dealing range
  - Multi-timeframe read + 0-6 confluence score
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

TF_SECONDS = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400, "D1": 86400}
ALL_TFS = ["M1", "M5", "M15", "M30", "H1", "H4", "D1"]


@dataclass
class Bar:
    t: int          # bucket start, epoch seconds
    o: float
    h: float
    l: float
    c: float
    v: float = 0.0


@dataclass
class Tick:
    t_msc: int
    bid: float
    ask: float
    vol: float = 0.0


class Aggregator:
    """Builds bars for several timeframes from a tick stream."""

    def __init__(self, tfs=ALL_TFS, maxlen: int = 800):
        self.bars: dict[str, list[Bar]] = {tf: [] for tf in tfs}
        self.maxlen = maxlen

    def add(self, t: int, price: float, vol: float = 1.0) -> None:
        for tf, lst in self.bars.items():
            s = TF_SECONDS[tf]
            b = t - (t % s)
            if lst and lst[-1].t == b:
                bar = lst[-1]
                if price > bar.h:
                    bar.h = price
                if price < bar.l:
                    bar.l = price
                bar.c = price
                bar.v += vol
            elif not lst or b > lst[-1].t:
                lst.append(Bar(b, price, price, price, price, vol))
                if len(lst) > self.maxlen + 50:
                    del lst[: len(lst) - self.maxlen]
            # older tick than current bar -> ignore


# --------------------------------------------------------------------------
# Structure
# --------------------------------------------------------------------------
@dataclass
class Event:
    tf: str
    kind: str           # BOS | CHoCH | SWEEP | FVG
    direction: int      # +1 bullish, -1 bearish
    level: float
    t: int
    note: str = ""

    @property
    def key(self):
        return (self.tf, self.kind, self.direction, self.t, round(self.level, 5))


@dataclass
class Swing:
    level: float
    t: int
    broken: bool = False
    swept: bool = False


@dataclass
class Gap:
    lo: float
    hi: float
    t: int
    direction: int


@dataclass
class TFState:
    tf: str
    trend: int = 0                      # +1 bull / -1 bear / 0 undefined
    sh: Optional[Swing] = None          # latest swing high
    sl: Optional[Swing] = None          # latest swing low
    events: list[Event] = field(default_factory=list)
    fvg_bull: Optional[Gap] = None      # latest unfilled bullish FVG
    fvg_bear: Optional[Gap] = None
    atr: float = 0.0
    pd: Optional[float] = None          # 0 = range low, 1 = range high
    price: float = 0.0
    bars: int = 0

    def last(self, kinds=("BOS", "CHoCH")) -> Optional[Event]:
        for e in reversed(self.events):
            if e.kind in kinds:
                return e
        return None


def analyze(tf: str, bars: list[Bar], n: int = 3, atr_len: int = 14) -> TFState:
    """Full recompute on the given bars. Last bar is treated as still forming."""
    st = TFState(tf=tf, bars=len(bars))
    if len(bars) < 2 * n + 3:
        if bars:
            st.price = bars[-1].c
        return st

    closed = bars[:-1]
    L = len(closed)
    st.price = bars[-1].c

    # ATR
    trs = []
    for i in range(1, L):
        b, p = closed[i], closed[i - 1]
        trs.append(max(b.h - b.l, abs(b.h - p.c), abs(b.l - p.c)))
    if trs:
        k = min(atr_len, len(trs))
        st.atr = sum(trs[-k:]) / k

    trend = 0
    sh: Optional[Swing] = None
    sl: Optional[Swing] = None
    events: list[Event] = []
    open_gaps: list[Gap] = []

    for i in range(L):
        # 1) confirm swing at j = i - n (needs n bars on the right)
        j = i - n
        if j >= n:
            hj, lj = closed[j].h, closed[j].l
            left = closed[j - n: j]
            right = closed[j + 1: j + n + 1]
            if hj > max(b.h for b in left) and hj >= max(b.h for b in right):
                sh = Swing(hj, closed[j].t)
            if lj < min(b.l for b in left) and lj <= min(b.l for b in right):
                sl = Swing(lj, closed[j].t)

        bar = closed[i]

        # 2) breaks & sweeps of the active swings
        if sh and not sh.broken and bar.t > sh.t:
            if bar.c > sh.level:
                kind = "BOS" if trend == 1 else "CHoCH"
                trend = 1
                sh.broken = True
                events.append(Event(tf, kind, 1, sh.level, bar.t))
            elif bar.h > sh.level and not sh.swept:
                sh.swept = True
                events.append(Event(tf, "SWEEP", -1, sh.level, bar.t, "BSL"))
        if sl and not sl.broken and bar.t > sl.t:
            if bar.c < sl.level:
                kind = "BOS" if trend == -1 else "CHoCH"
                trend = -1
                sl.broken = True
                events.append(Event(tf, kind, -1, sl.level, bar.t))
            elif bar.l < sl.level and not sl.swept:
                sl.swept = True
                events.append(Event(tf, "SWEEP", 1, sl.level, bar.t, "SSL"))

        # 3) FVG mitigation, then new FVGs
        if open_gaps:
            keep = []
            for g in open_gaps:
                if g.direction == 1 and bar.l <= g.lo and bar.t > g.t:
                    continue
                if g.direction == -1 and bar.h >= g.hi and bar.t > g.t:
                    continue
                keep.append(g)
            open_gaps = keep[-12:]
        if i >= 2:
            a, c = closed[i - 2], closed[i]
            min_gap = 0.15 * st.atr if st.atr else 0.0
            if c.l > a.h and (c.l - a.h) > min_gap:
                open_gaps.append(Gap(a.h, c.l, c.t, 1))
                events.append(Event(tf, "FVG", 1, (a.h + c.l) / 2, c.t,
                                    f"{a.h:.2f}-{c.l:.2f}"))
            elif c.h < a.l and (a.l - c.h) > min_gap:
                open_gaps.append(Gap(c.h, a.l, c.t, -1))
                events.append(Event(tf, "FVG", -1, (c.h + a.l) / 2, c.t,
                                    f"{c.h:.2f}-{a.l:.2f}"))

    st.trend, st.sh, st.sl, st.events = trend, sh, sl, events
    for g in reversed(open_gaps):
        if g.direction == 1 and st.fvg_bull is None:
            st.fvg_bull = g
        if g.direction == -1 and st.fvg_bear is None:
            st.fvg_bear = g
    if sh and sl and sh.level > sl.level:
        st.pd = (st.price - sl.level) / (sh.level - sl.level)
    return st


# --------------------------------------------------------------------------
# Multi-timeframe read
# --------------------------------------------------------------------------
@dataclass
class Read:
    bias: int = 0
    score: int = 0
    verdict: str = "NO DATA"
    verdict_style: str = "white"
    lines: list[str] = field(default_factory=list)
    pdh: Optional[float] = None
    pdl: Optional[float] = None
    max_score: int = 100
    modules: list = field(default_factory=list)
    groups: list = field(default_factory=list)   # every scoring group with its items (see market_read)
    overall: Optional[float] = None               # weighted confluence %, 0-100
    passing: float = 70.0                         # user's passing score %
    passed: bool = False
    trigger: bool = False
    lost: list = field(default_factory=list)      # items not agreeing + the points each one costs the OVERALL
    alt: dict = field(default_factory=dict)       # what the OVERALL would be with the other scoring options


def _recent(e: Optional[Event], tf: str, now_t: int, nbars: int) -> bool:
    return e is not None and e.t >= now_t - nbars * TF_SECONDS[tf]


# Analyser modules. Core (always on) = price action structure + timeframe bias.
# Extra modules are switched on by indicator plugins that declare `analyser_module = "..."`.
MOD_PD = "premium_discount"      # plugins/premium_discount.py
MOD_LIQ = "liquidity"            # plugins/smc_overlay.py  (sweeps + FVG)
MOD_PREVDAY = "prev_day"         # plugins/prev_day_levels.py


GROUP_ORDER = ["Structure", "SMC", "Trend", "Momentum", "Volatility", "Volume", "Order Flow", "Levels", "Reversal",
               "Intermarket", "Other"]
CATEGORY_ORDER = GROUP_ORDER                       # kept for older code
DEFAULT_SCORING = {"passing": 70.0,
                   "weights": {"Structure": 1.0, "SMC": 1.0, "Trend": 1.0, "Momentum": 1.0,
                               "Volatility": 1.0, "Volume": 1.0, "Order Flow": 1.0, "Levels": 1.0, "Reversal": 1.0,
                               "Intermarket": 1.0, "Other": 1.0}}


SCORE_NEUTRAL = ("against", "ignore")    # a neutral (0) vote: counts as not agreeing (default) | left out of the group
SCORE_MODES = ("group", "indicator")     # group: every group is one average (default) | indicator: every item counts once


def clean_scoring(sc) -> dict:
    out = {"passing": DEFAULT_SCORING["passing"], "weights": dict(DEFAULT_SCORING["weights"]),
           "neutral": "against", "mode": "group"}
    sc = sc or {}
    if sc.get("neutral") in SCORE_NEUTRAL:
        out["neutral"] = sc["neutral"]
    if sc.get("mode") in SCORE_MODES:
        out["mode"] = sc["mode"]
    try:
        out["passing"] = max(0.0, min(100.0, float(sc.get("passing", out["passing"]))))
    except (TypeError, ValueError):
        pass
    for k, v in (sc.get("weights") or {}).items():
        if k in out["weights"]:
            try:
                out["weights"][k] = max(0.0, min(10.0, float(v)))
            except (TypeError, ValueError):
                pass
    return out


def group_votes(votes):
    """votes: [(indicator name, category, direction, reason)] -> {category: [items]}."""
    groups = {}
    for name, cat, d, reason in votes:
        groups.setdefault(cat or "Other", []).append(dict(name=name, dir=d, reason=reason))
    return groups


SWEEP_LOOKBACK = {"M5": 36, "M15": 16, "H1": 8, "H4": 4}


def market_read(states: dict[str, TFState], d1: list[Bar], m1: list[Bar], modules=frozenset(),
                votes=(), scoring=None, module_tfs=None) -> Read:
    """
    Weighted group scoring.
      * Every group gets a % = items agreeing with the HTF bias / items in the group
        (a neutral vote counts as "not agreeing"): e.g. Trend 3 of 5 bullish = 60%.
      * Groups: Structure (always: HTF bias, H1 aligned, M15 aligned, M5/M15 trigger),
        SMC (Premium/Discount location + liquidity sweep, when those are ON for scoring),
        and one group per indicator category (Trend, Momentum, Volatility, Volume, Levels).
        Prev-day sweep counts inside Levels.
      * OVERALL % = weighted average of the groups that have at least one item, using YOUR weights
        (weight 0 = ignore that group).
      * READY = HTF bias + trigger + OVERALL >= passing score (+ right side of range if P/D is ON).
    """
    sc = clean_scoring(scoring)
    r = Read(passing=sc["passing"])
    modules = set(modules or ())
    r.modules = sorted(modules)
    h4, h1, m15, m5 = (states.get(k) for k in ("H4", "H1", "M15", "M5"))
    if not (h4 and h1 and m15 and m5) or not m1:
        return r
    price = m1[-1].c
    now_t = m1[-1].t
    bias = h4.trend or h1.trend
    r.bias = bias
    side = "BULL" if bias == 1 else "BEAR" if bias == -1 else "----"
    arrow = "UP" if bias == 1 else "DN"
    want = bias if bias else 1

    def item(name, ok, reason):
        return dict(name=name, dir=(want if ok else -want) if bias else 0, reason=reason, check=True)

    # ---- Structure (price action + timeframe bias)
    trig = None
    for st, nb in ((m5, 24), (m15, 8)):
        e = st.last()
        if e and bias and e.direction == bias and _recent(e, st.tf, now_t, nb):
            trig = e
            break
    r.trigger = trig is not None
    structure = [
        item("HTF bias", bias != 0, f"H4 {_t(h4.trend)} / H1 {_t(h1.trend)}"),
        item("H1 aligned", bias != 0 and h1.trend == bias, f"H1 {_t(h1.trend)}"),
        item("M15 aligned", bias != 0 and m15.trend == bias, f"M15 {_t(m15.trend)}"),
        item("Trigger", trig is not None,
             f"{trig.tf} {trig.kind} {arrow} @ {trig.level:.2f}" if trig else f"no M5/M15 CHoCH/BOS {arrow} yet"),
    ]
    r.lines.append(f"HTF bias {side} (H4 {_t(h4.trend)} / H1 {_t(h1.trend)})")
    r.lines.append(f"M15 {_t(m15.trend)}  M5 {_t(m5.trend)}")
    if trig:
        r.lines.append(f"Trigger: {trig.tf} {trig.kind} {arrow} @ {trig.level:.2f}")

    # ---- SMC checks (only when switched on for scoring)
    smc, levels_extra = [], []
    loc_ok = True
    mt = module_tfs or {}
    pd_tfs = [t for t in mt.get(MOD_PD, ["H1"]) if states.get(t) is not None and states[t].pd is not None]
    if MOD_PD in modules and pd_tfs:
        # one item, even on several timeframes: majority of the chosen timeframes (a tie = not OK)
        oks, txt = [], []
        for t in pd_tfs:
            pdv = states[t].pd
            z = "DISCOUNT" if pdv < 0.5 else "PREMIUM"
            oks.append((bias == 1 and pdv < 0.5) or (bias == -1 and pdv > 0.5))
            txt.append(f"{t} {pdv * 100:.0f}% {z}")
        loc_ok = sum(oks) * 2 > len(oks)
        smc.append(item("Premium/Discount", loc_ok, " · ".join(txt)))
        r.lines.append("range " + " · ".join(txt))
    if MOD_LIQ in modules:
        found = None
        liq_tfs = mt.get(MOD_LIQ)
        seq = ([(states[t], SWEEP_LOOKBACK.get(t, 16)) for t in liq_tfs if states.get(t) is not None]
               if liq_tfs else [(m15, 16), (m5, 36)])
        for st, nb in seq:
            e = st.last(("SWEEP",))
            if e and bias and e.direction == bias and _recent(e, st.tf, now_t, nb):
                found = e
                break
        smc.append(item("Liquidity sweep", found is not None,
                        f"{found.tf} {found.note} swept @ {found.level:.2f}" if found else "no opposite-side sweep"))
        if found:
            r.lines.append(f"{found.tf} {found.note} swept @ {found.level:.2f}")
        fst = seq[0][0] if seq else m15
        fvg = (fst.fvg_bull if bias == 1 else fst.fvg_bear) if bias else None
        if fvg:
            r.lines.append(f"{fst.tf} FVG {fvg.lo:.2f}-{fvg.hi:.2f} (entry zone)")
    if MOD_PREVDAY in modules and len(d1) >= 2:
        r.pdh, r.pdl = d1[-2].h, d1[-2].l
        today = d1[-1]
        pdh_swept = today.h > r.pdh and price < r.pdh
        pdl_swept = today.l < r.pdl and price > r.pdl
        ok = (bias == -1 and pdh_swept) or (bias == 1 and pdl_swept)
        levels_extra.append(item("Prev-day sweep", ok, "PDH swept, back below" if pdh_swept else
                                 "PDL swept, back above" if pdl_swept else "no PDH/PDL sweep"))

    # ---- assemble groups
    raw = {"Structure": structure}
    if smc:
        raw["SMC"] = smc
    for cat, items in group_votes(votes).items():
        raw.setdefault(cat, []).extend(items)
    if levels_extra:
        raw.setdefault("Levels", []).extend(levels_extra)

    order = {g: i for i, g in enumerate(GROUP_ORDER)}
    groups = []
    for cat in sorted(raw, key=lambda c: order.get(c, 99)):
        items = raw[cat]
        agree = sum(1 for it in items if bias and it["dir"] == bias)
        against = sum(1 for it in items if bias and it["dir"] == -bias)
        neutral = len(items) - agree - against
        counted = len(items) if sc["neutral"] == "against" else agree + against
        pct = 100.0 * agree / counted if counted and bias else 0.0
        w = sc["weights"].get(cat, sc["weights"].get("Other", 1.0))
        groups.append(dict(category=cat, items=items, agree=agree, against=against, neutral=neutral,
                           total=len(items), counted=counted, pct=round(pct, 1), weight=w,
                           skipped=bool(bias) and counted == 0, point=pct >= sc["passing"]))
    r.groups = groups
    r.overall, r.lost = score_overall(raw, bias, sc["weights"], sc["neutral"], sc["mode"])
    r.alt = {f"{n}/{m}": score_overall(raw, bias, sc["weights"], n, m)[0]
             for n in SCORE_NEUTRAL for m in SCORE_MODES}
    r.score = int(round(r.overall))
    r.max_score = 100
    r.passed = bool(bias) and r.overall >= sc["passing"]

    # ---- verdict
    word = "LONG" if bias == 1 else "SHORT"
    if bias == 0:
        r.verdict, r.verdict_style = "NO BIAS - wait for H4/H1 structure", "bold yellow"
    elif trig and loc_ok and r.passed:
        r.verdict, r.verdict_style = f"{word} READY - {r.overall:.0f}% >= {sc['passing']:.0f}%, trigger printed", \
            "bold black on green" if bias == 1 else "bold white on red"
    elif not loc_ok:
        r.verdict, r.verdict_style = f"WAIT - {side} but wrong side of range, wait for pullback", "bold yellow"
    elif not trig:
        r.verdict, r.verdict_style = f"WATCH - {side} {r.overall:.0f}%, wait M5/M15 CHoCH {arrow}", "bold cyan"
    else:
        weakest = min((g for g in groups if g["weight"] > 0), key=lambda g: g["pct"], default=None)
        why = f", weakest {weakest['category']} {weakest['pct']:.0f}%" if weakest else ""
        r.verdict, r.verdict_style = f"WATCH - {side} {r.overall:.0f}% below {sc['passing']:.0f}%{why}", "bold cyan"
    return r


def score_overall(raw, bias, weights, neutral="against", mode="group"):
    """OVERALL % of the groups {category: [items]} and, for every item that does not agree, the points it costs.
      group     : OVERALL = sum(group% x weight) / sum(weight); an item's share = weight / sum(weight) / items in group
      indicator : every item counts once with its group's weight: OVERALL = sum(weight x agree) / sum(weight)
      neutral 'against' : a 0 vote counts as not agreeing;  'ignore' : a 0 vote is left out (a group of only 0 votes
                          is left out too)"""
    if not bias:
        return 0.0, []
    shares = []                                            # (item, category, share of 100 points)
    groups = []
    for cat, items in raw.items():
        w = weights.get(cat, weights.get("Other", 1.0))
        if w <= 0:
            continue
        use = [it for it in items if neutral == "against" or it["dir"] != 0]
        if use:
            groups.append((cat, w, use))
    if not groups:
        return 0.0, []
    if mode == "indicator":
        tot = sum(w * len(use) for _, w, use in groups)
        for cat, w, use in groups:
            shares += [(it, cat, 100.0 * w / tot) for it in use]
    else:
        tot = sum(w for _, w, _ in groups)
        for cat, w, use in groups:
            shares += [(it, cat, 100.0 * w / tot / len(use)) for it in use]
    overall = sum(sh for it, _, sh in shares if it["dir"] == bias)
    lost = sorted(({"name": it["name"], "category": cat, "points": round(sh, 1),
                    "state": "neutral" if it["dir"] == 0 else "against"}
                   for it, cat, sh in shares if it["dir"] != bias), key=lambda x: -x["points"])
    return round(overall, 1), lost


def _t(x: int) -> str:
    return {1: "BULL", -1: "BEAR"}.get(x, "----")


def estimate_utc_offset(tick_msc: int, now_utc: float, current: int = 0) -> int:
    """MT5 bar/tick times are broker server time. Estimate the offset (hours) from a fresh tick.
    Only trusted when the tick is fresh (within ~2 min of a whole-hour offset); otherwise keep current."""
    diff = tick_msc / 1000 - now_utc
    hours = round(diff / 3600)
    if abs(diff - hours * 3600) < 120 and -12 <= hours <= 14:
        return hours
    return current
