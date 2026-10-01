"""
Setup quality: ONE object per side that the decision engine consumes (read-only, deterministic).

Why: ten similar indicators must not outvote market structure, and BOS + FVG + OB + sweep from the same price
event must not count as four confirmations.

  1. CATEGORIES (information types), one normalized result each:
       STRUCTURE   HTF bias + M15 structure (smc_engine) + M5/M15 trigger
       SMC         the SMC setup chain (sweep -> BOS/CHoCH -> FVG/OB -> retest) as ONE value: its confidence
       LOCATION    dealing-range zone (smc_engine, explicit source): DISCOUNT for BUY / PREMIUM for SELL
       TREND, MOMENTUM, VOLATILITY, VOLUME   the plugin votes of that category (engine groups), majority -> one value
       LIQUIDITY   liquidity sweep (already part of SMC -> weight 0 by default, shown only)
       CONTEXT     intermarket (weight 0 by default)
     Each category: available, direction BUY / SELL / NEUTRAL, support 0..1 (for the side being evaluated),
     confidence 0..1 (how one-sided the category is), items.
     ONE representation everywhere: 0.0 .. 1.0.
  2. CONTRIBUTION MAP  configurable weights (default TREND 20, MOMENTUM 15, STRUCTURE 25, SMC 25, LOCATION 10,
     VOLUME 5). confidence = sum(weight x support) / sum(weight) over AVAILABLE categories. A category with 10
     indicators still contributes at most its weight.
  3. CONFLICTS  a category whose direction is opposite to the side (confidence >= threshold) is reported as
     <CATEGORY>_CONFLICT. Policy (configurable):
        structure_priority  (default) structure / SMC conflicts block, others (momentum, trend...) only warn
        block_high_conflict           also block when >= high_conflict_count categories conflict
        warn_only                     never block, only report
  4. REGIME MODIFIERS  (regime.py; never an entry signal by itself) change the requirements:
        TRENDING         counter-trend side needs +0.15 confidence
        RANGING          without a liquidity sweep in the side's direction: +0.10 confidence
        BREAKOUT         requires displacement (structure break strength >= 0.6)
        HIGH_VOLATILITY  +0.10 confidence, risk x0.5 (auto lot)
        TRANSITION       +0.05 confidence
  5. CONDITIONS  every condition has a class REQUIRED / CONFIRMATION / OPTIONAL; which ones are REQUIRED is the
     user's choice (decision settings "required"); default = score, htf, trigger (= the existing READY rule).
The existing analyser score is not changed and stays one of the conditions ("score").
"""
from __future__ import annotations

CATEGORY_OF = {"Structure": None, "SMC": None, "Trend": "TREND", "Momentum": "MOMENTUM", "Reversal": "MOMENTUM",
               "Volatility": "VOLATILITY", "Volume": "VOLUME", "Order Flow": "VOLUME", "Levels": "LIQUIDITY",
               "Intermarket": "CONTEXT", "Other": "OTHER"}
CATEGORIES = ["STRUCTURE", "SMC", "LOCATION", "TREND", "MOMENTUM", "VOLATILITY", "VOLUME", "LIQUIDITY", "SESSION",
              "CONTEXT", "OTHER"]
DEFAULT_WEIGHTS = dict(STRUCTURE=25.0, SMC=25.0, TREND=20.0, MOMENTUM=15.0, LOCATION=10.0, VOLUME=5.0,
                       VOLATILITY=0.0, LIQUIDITY=0.0, SESSION=0.0, CONTEXT=0.0, OTHER=0.0)
CONDITIONS = {
    # key: (class, label)
    "score": ("REQUIRED", "Existing analyser score >= passing"),
    "htf": ("REQUIRED", "HTF bias in the trade direction"),
    "trigger": ("REQUIRED", "Valid market structure: M5/M15 BOS/CHoCH trigger"),
    "structure": ("CONFIRMATION", "M15 structure (SMC engine) in the trade direction"),
    "smc_setup": ("CONFIRMATION", "Valid SMC setup (sweep -> BOS/CHoCH -> FVG/OB)"),
    "retest": ("CONFIRMATION", "SMC zone retested"),
    "sweep": ("CONFIRMATION", "Liquidity sweep in the trade direction"),
    "fvg": ("CONFIRMATION", "FVG in the trade direction (fresh / partly filled)"),
    "ob": ("CONFIRMATION", "Order block in the trade direction (not invalidated)"),
    "location": ("CONFIRMATION", "Location: DISCOUNT for BUY / PREMIUM for SELL"),
    "session": ("CONFIRMATION", "Allowed session"),
    "volume": ("CONFIRMATION", "Volume / order flow agrees"),
    "vwap": ("CONFIRMATION", "VWAP side agrees"),
    "momentum": ("OPTIONAL", "Momentum indicators agree"),
    "trend": ("OPTIONAL", "Trend indicators agree"),
    "min_confidence": ("OPTIONAL", "Setup confidence >= minimum"),
    "no_conflict": ("OPTIONAL", "No category conflicts at all"),
}
DEFAULT_REQUIRED = ["score", "htf", "trigger"]
POLICIES = ("structure_priority", "block_high_conflict", "warn_only")
DEFAULTS = dict(weights=DEFAULT_WEIGHTS, required=DEFAULT_REQUIRED, min_confidence=0.0,
                conflict_policy="structure_priority", conflict_threshold=0.5, high_conflict_count=2,
                regime_rules=True)
WORD = {1: "BUY", -1: "SELL", 0: "NEUTRAL"}


def clean(cfg):
    """Validated settings. Invalid values fall back to the defaults (never to 'allow everything')."""
    c = dict(DEFAULTS)
    cfg = cfg or {}
    w = dict(DEFAULT_WEIGHTS)
    for k, v in (cfg.get("weights") or {}).items():
        if k in w:
            try:
                w[k] = max(0.0, min(100.0, float(v)))
            except (TypeError, ValueError):
                pass
    c["weights"] = w
    req = cfg.get("required", DEFAULT_REQUIRED)
    if isinstance(req, (list, tuple)):
        c["required"] = [k for k in req if k in CONDITIONS]
    try:
        c["min_confidence"] = max(0.0, min(1.0, float(cfg.get("min_confidence", 0.0))))
    except (TypeError, ValueError):
        pass
    if cfg.get("conflict_policy") in POLICIES:
        c["conflict_policy"] = cfg["conflict_policy"]
    for k, lo, hi in (("conflict_threshold", 0.1, 1.0), ("high_conflict_count", 1, 10)):
        try:
            c[k] = type(DEFAULTS[k])(max(lo, min(hi, float(cfg.get(k, DEFAULTS[k])))))
        except (TypeError, ValueError):
            pass
    c["regime_rules"] = bool(cfg.get("regime_rules", True))
    return c


def _cat(direction_votes):
    """[(dir)] -> (direction, confidence, n, agree_fn)."""
    n = len(direction_votes)
    up = sum(1 for d in direction_votes if d == 1)
    dn = sum(1 for d in direction_votes if d == -1)
    direction = 1 if up > dn else -1 if dn > up else 0
    return direction, (abs(up - dn) / n if n else 0.0), n, up, dn


def regime_class(rg):
    """regime.py state -> TRENDING / RANGING / BREAKOUT / HIGH_VOLATILITY / LOW_VOLATILITY / TRANSITION."""
    if not rg:
        return None, 0
    lab = rg.get("label")
    htf = (rg.get("htf_read") or {}).get("label")
    d = rg.get("dir") or 0
    if lab in ("VOLATILE", "TREND UP", "TREND DOWN") and htf in ("RANGE", "QUIET"):
        return "BREAKOUT", d
    return {"TREND UP": "TRENDING", "TREND DOWN": "TRENDING", "RANGE": "RANGING", "VOLATILE": "HIGH_VOLATILITY",
            "QUIET": "LOW_VOLATILITY", "TRANSITION": "TRANSITION"}.get(lab), d


def evaluate(side, read=None, smc=None, matrix=None, regime=None, session_ok=None, cfg=None):
    """side +1/-1. read = engine.Read, smc = smc_engine.analyze() result, matrix = decision setup_matrix rows
    (for VWAP / volume / session rows), regime = regime.py state. Returns the setup-quality object."""
    c = clean(cfg)
    side = side if side in (1, -1) else 0
    rows = {r["key"]: r for r in (matrix or {}).get("rows", [])}
    cats = {}

    def put(name, available, direction, confidence, support, items, source):
        cats[name] = dict(available=bool(available), direction=WORD.get(direction, "NEUTRAL"), dir=direction,
                          confidence=round(float(confidence), 2), support=round(float(support), 2), items=items,
                          source=source, weight=c["weights"].get(name, 0.0))

    # ---- STRUCTURE: HTF bias + M15 structure + trigger (one value)
    bias = read.bias if read else 0
    smc_ok = bool(smc and smc.get("ok"))
    s_dir = smc["structure"]["direction"] if smc_ok else 0
    s_str = smc["structure"]["strength"] if smc_ok else 0.0
    trig = bool(rows.get("trigger", {}).get("ok")) if rows else bool(read and read.trigger)
    parts = [1.0 if bias == side and side else 0.0,
             (0.5 + 0.5 * s_str) if s_dir == side and side else 0.0,
             1.0 if trig else 0.0]
    against = [1.0 if bias == -side and side else 0.0, (0.5 + 0.5 * s_str) if s_dir == -side and side else 0.0]
    sup = sum(parts) / 3
    opp = sum(against) / 3                   # same scale as support (3 parts)
    st_dir = side if sup > opp else -side if opp > sup else 0
    put("STRUCTURE", read is not None or smc_ok, st_dir, abs(sup - opp), sup, 3, "engine HTF bias + smc_engine M15 + trigger")

    # ---- SMC setup as ONE value
    my = opp_s = None
    if smc_ok and side:
        my = (smc.get("setups") or {}).get(WORD[side])
        opp_s = (smc.get("setups") or {}).get(WORD[-side])
    valid = lambda s_: bool(s_) and s_.get("status") in ("WAITING_RETEST", "RETESTED")
    if smc_ok:
        mine = my["confidence"] if valid(my) else 0.0
        theirs = opp_s["confidence"] if valid(opp_s) else 0.0
        d_ = side if mine > theirs else -side if theirs > mine else 0
        put("SMC", True, d_, max(mine, theirs), mine, 1, "smc_engine setup chain")
    else:
        put("SMC", False, 0, 0.0, 0.0, 0, "smc_engine not available")

    # ---- LOCATION from the dealing range
    rng = smc.get("dealing_range") if smc_ok else None
    zone = (rng or {}).get("zone")
    if zone in ("DISCOUNT", "PREMIUM", "EQUILIBRIUM") and side:
        good = "DISCOUNT" if side == 1 else "PREMIUM"
        supp = 1.0 if zone == good else 0.5 if zone == "EQUILIBRIUM" else 0.0
        d_ = side if supp > 0.5 else -side if supp < 0.5 else 0
        put("LOCATION", True, d_, abs(supp - 0.5) * 2, supp, 1, f"dealing range ({rng.get('source')})")
    else:
        put("LOCATION", False, 0, 0.0, 0.0, 0, "dealing range not available")

    # ---- indicator categories from the existing plugin votes (engine groups): one value per category
    buckets = {}
    for g in (read.groups if read else []):
        cat = CATEGORY_OF.get(g["category"], "OTHER")
        if cat is None:
            continue
        for it in g["items"]:
            buckets.setdefault(cat, []).append(it["dir"])
    for cat in ("TREND", "MOMENTUM", "VOLATILITY", "VOLUME", "CONTEXT", "OTHER"):
        dirs = buckets.get(cat, [])
        if cat == "VOLUME" and not dirs and rows.get("volume") is not None:
            pass
        if not dirs:
            put(cat, False, 0, 0.0, 0.0, 0, "no indicator of this category on SCORE")
            continue
        d_, conf, n, up, dn = _cat(dirs)
        agree = (up if side == 1 else dn) if side else 0
        put(cat, True, d_, conf, agree / n if n else 0.0, n, "plugin votes (majority)")

    # ---- LIQUIDITY (part of the SMC chain: weight 0 by default, so it is not counted twice)
    sw = None
    if smc_ok and side:
        sw = next((x for x in reversed(smc["liquidity"]["sweeps"]) if x["direction"] == side and x["confirmed"]), None)
    lq_dirs = buckets.get("LIQUIDITY", [])
    if sw is not None or lq_dirs:
        put("LIQUIDITY", True, side if sw else _cat(lq_dirs)[0], 1.0 if sw else _cat(lq_dirs)[1],
            1.0 if sw else 0.0, len(lq_dirs) + (1 if sw else 0), "smc_engine sweeps / Levels votes")
    else:
        put("LIQUIDITY", bool(smc_ok), 0, 0.0, 0.0, 0, "no confirmed sweep")
    put("SESSION", session_ok is not None, side if session_ok else 0, 1.0 if session_ok is not None else 0.0,
        1.0 if session_ok else 0.0, 1, "allowed sessions (AUTO TRADE settings)")

    # ---- confidence (contribution map)
    wsum = sum(v["weight"] for v in cats.values() if v["available"] and v["weight"] > 0)
    contrib = {}
    conf = 0.0
    if wsum > 0 and side:
        for k, v in cats.items():
            if v["available"] and v["weight"] > 0:
                contrib[k] = round(v["weight"] * v["support"] / wsum, 3)
                conf += v["weight"] * v["support"]
        conf /= wsum
    conf = round(conf, 3)

    # ---- conflicts
    th = c["conflict_threshold"]
    conflicts = []
    for k, v in cats.items():
        if not v["available"] or not side or k in ("SESSION",):
            continue
        if v["dir"] == -side and v["confidence"] >= th:
            conflicts.append(dict(category=k, code=f"{k}_CONFLICT", confidence=v["confidence"],
                                  text=f"{k} says {v['direction']} ({v['confidence']:.2f})"))
    # structure conflict = against the HTF bias, or against BOTH the M15 and the H1 structure
    htf_s = (smc or {}).get("htf_structure") or {}
    st_conf = bool(side) and (bias == -side or (s_dir == -side and htf_s.get("direction") == -side))
    conflicts = [x for x in conflicts if x["category"] != "STRUCTURE"]
    if st_conf:
        conflicts.insert(0, dict(category="STRUCTURE", code="STRUCTURE_CONFLICT", confidence=1.0,
                                 text="structure is against this side (HTF bias, or both M15 and H1)"))
    smc_conf = bool(side) and valid(opp_s) and opp_s.get("quality") == "HIGH" and opp_s.get("status") == "RETESTED" \
        and not valid(my)
    conflicts = [x for x in conflicts if x["category"] != "SMC"]
    if smc_conf:
        conflicts.append(dict(category="SMC", code="SMC_CONFLICT", confidence=opp_s["confidence"],
                              text=f"a HIGH quality retested {WORD[-side]} setup is active"))
    high = len(conflicts) >= c["high_conflict_count"]
    block = None
    pol = c["conflict_policy"]
    structural = [x for x in conflicts if x["category"] in ("STRUCTURE", "SMC")]
    if pol != "warn_only" and structural:
        block = dict(code=structural[0]["code"], text=structural[0]["text"])
    elif pol == "block_high_conflict" and high:
        block = dict(code="HIGH_CONFLICT", text=f"{len(conflicts)} categories conflict: "
                                                + ", ".join(x["category"] for x in conflicts))

    # ---- regime modifiers (never an entry signal)
    rcls, rdir = regime_class(regime) if c["regime_rules"] else (None, 0)
    extra, risk_mult, extra_req, notes = 0.0, 1.0, [], []
    if rcls == "TRENDING" and rdir and side and rdir != side:
        extra += 0.15
        notes.append("counter-trend in a TRENDING regime: +0.15 confidence needed")
    elif rcls == "RANGING" and not (sw is not None):
        extra += 0.10
        notes.append("RANGING without a liquidity sweep: +0.10 confidence needed")
    elif rcls == "BREAKOUT":
        extra_req.append("displacement")
        notes.append("BREAKOUT: displacement (break strength >= 0.6) required")
    elif rcls == "HIGH_VOLATILITY":
        extra += 0.10
        risk_mult = 0.5
        notes.append("HIGH VOLATILITY: +0.10 confidence, auto lot x0.5")
    elif rcls == "TRANSITION":
        extra += 0.05
        notes.append("TRANSITION: +0.05 confidence")
    min_conf = round(min(1.0, c["min_confidence"] + extra), 3) if (c["min_confidence"] > 0 or extra) else 0.0

    # ---- conditions
    le = smc["structure"]["last_event"] if smc_ok else None
    disp_ok = bool(le and le["direction"] == side and le["strength"] >= 0.6)
    fvg_ok = ob_ok = False
    if smc_ok and side:
        fvg_ok = any(z["direction"] == side and z["status"] in ("FRESH", "PARTIALLY_FILLED") for z in smc["fvgs"])
        ob_ok = any(o["direction"] == side and o["mitigation_status"] in ("FRESH", "MITIGATED") for o in smc["order_blocks"])
    cond_val = {
        "score": rows.get("score", {}).get("ok") if rows else (read.passed if read else None),
        "htf": rows.get("htf", {}).get("ok") if rows else (bool(bias) and bias == side),
        "trigger": trig,
        "structure": (s_dir == side) if smc_ok and side else None,
        "smc_setup": valid(my) if smc_ok else None,
        "retest": (valid(my) and my.get("status") == "RETESTED") if smc_ok else None,
        "sweep": (sw is not None) if smc_ok else None,
        "fvg": fvg_ok if smc_ok else None,
        "ob": ob_ok if smc_ok else None,
        "location": (cats["LOCATION"]["support"] == 1.0) if cats["LOCATION"]["available"] else None,
        "session": session_ok,
        "volume": (cats["VOLUME"]["dir"] == side) if cats["VOLUME"]["available"] else rows.get("orderflow", {}).get("ok"),
        "vwap": rows.get("vwap", {}).get("ok"),
        "momentum": (cats["MOMENTUM"]["dir"] == side) if cats["MOMENTUM"]["available"] else None,
        "trend": (cats["TREND"]["dir"] == side) if cats["TREND"]["available"] else None,
        "min_confidence": conf >= min_conf if side else None,
        "no_conflict": not conflicts,
        "displacement": disp_ok,
    }
    required = list(c["required"]) + [k for k in extra_req if k not in c["required"]]
    if min_conf > 0 and "min_confidence" not in required:
        required.append("min_confidence")
    conditions = {}
    for k, v in cond_val.items():
        cls, label = CONDITIONS.get(k, ("REQUIRED", "Displacement (strong structure break)"))
        conditions[k] = dict(ok=v, required=k in required, cls="REQUIRED" if k in required else cls, label=label)
    missing = [k for k in required if conditions.get(k, {}).get("ok") is not True]

    # ---- the unified quality object (spec: what the decision engine consumes)
    mom = cats["MOMENTUM"]
    vol = cats["VOLUME"]
    quality = dict(
        direction=WORD.get(side, "NONE"),
        bias={1: "BULLISH", -1: "BEARISH"}.get(bias, "NONE"),
        structure={1: "BULLISH", -1: "BEARISH"}.get(s_dir, "NONE") if smc_ok else "UNAVAILABLE",
        smc_quality=(my["quality"] if valid(my) else "NONE") if smc_ok else "UNAVAILABLE",
        location=zone or "UNAVAILABLE",
        momentum=("POSITIVE" if mom["dir"] == side else "NEGATIVE" if mom["dir"] == -side else "NEUTRAL")
        if mom["available"] else "UNAVAILABLE",
        volume=("CONFIRMING" if vol["dir"] == side else "OPPOSING" if vol["dir"] == -side else "NEUTRAL")
        if vol["available"] else "UNAVAILABLE",
        session="ALLOWED" if session_ok else "OUTSIDE" if session_ok is False else "UNKNOWN",
        confidence=conf)
    return dict(side=side, categories=cats, confidence=conf, min_confidence=min_conf, contributions=contrib,
                conflicts=conflicts, high_conflict=high, conflict_block=block, conflict_policy=pol,
                structure_alignment=WORD.get(st_dir, "NEUTRAL"),
                regime=dict(cls=rcls, dir=rdir, notes=notes, risk_multiplier=risk_mult, extra_required=extra_req),
                conditions=conditions, required=required, missing_required=missing,
                smc_setup=(dict((k, my.get(k)) for k in ("direction", "status", "quality", "confidence", "stage",
                                                     "entry_zone", "invalidation", "setup_id", "components"))
                           if my else None),
                quality=quality, settings=c)


def explain(side, q, blockers=(), state=None):
    """Human-readable decision text (spec 32): never just 'BUY'."""
    w = WORD.get(side, "NONE")
    qq = q["quality"]
    good = [f"HTF bias: {qq['bias'].lower()}", f"Structure: {qq['structure'].lower()}",
            f"SMC: {qq['smc_quality'].lower()}", f"Location: {qq['location'].lower()}",
            f"Momentum: {qq['momentum'].lower()}", f"Volume: {qq['volume'].lower()}",
            f"Session: {qq['session'].lower()}", f"Confidence: {q['confidence']:.2f}"
            + (f" (min {q['min_confidence']:.2f})" if q["min_confidence"] else "")]
    but = [f"required '{k}' not met" for k in q["missing_required"]]
    but += [x["text"] for x in q["conflicts"]]
    but += [f"{b['code']}: {b['text']}" for b in blockers if b.get("level", "hard") == "hard"]
    head = (f"{w} APPROVED" if state == "READY" else f"{w} BLOCKED" if state == "BLOCKED" else f"{w} {state or 'WAIT'}")
    return dict(head=head, reasons=good, but=but, regime=q["regime"]["notes"])
