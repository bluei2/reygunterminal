"""SMC structural engine: structure (HH/HL/LH/LL, BOS, CHoCH), FVG, OB, liquidity sweep, premium/discount, SMC setup."""
import harness  # noqa: F401  (paths + temp folder)

import smc_engine as S
from engine import Bar

T0 = 1_700_000_000 - 1_700_000_000 % 900


def bars_from_closes(closes, wick=0.1, t0=T0, sec=900):
    out, prev = [], closes[0]
    for i, c in enumerate(closes):
        o = prev
        out.append(Bar(t0 + i * sec, o, max(o, c) + wick, min(o, c) - wick, c, 100))
        prev = c
    return out


def zigzag_up():
    seq = [100.0]
    for leg in range(6):                           # up 5, down 3 -> higher highs and higher lows
        for _ in range(5):
            seq.append(seq[-1] + 1.0)
        for _ in range(3):
            seq.append(seq[-1] - 1.0)
    return seq


CFG = S.clean_cfg(dict(swing_n=2))


def test_structure_labels_and_bos():
    b = bars_from_closes(zigzag_up())
    st = S.structure(b, "M15", 2)
    labels = [s["label"] for s in st["swings"]]
    assert "HH" in labels and "HL" in labels and "LL" not in labels and "LH" not in labels, labels
    assert st["direction"] == 1 and st["strength"] == 1.0, (st["direction"], st["strength"])
    kinds = [e["event_type"] for e in st["events"]]
    assert "BOS" in kinds, kinds
    e = st["events"][-1]
    for k in ("timestamp", "price", "direction", "timeframe", "event_type", "strength", "source"):
        assert k in e, k
    assert 0.0 <= e["strength"] <= 1.0


def test_choch_on_reversal():
    seq = zigzag_up()
    for _ in range(12):                            # a deep drop below the last higher low
        seq.append(seq[-1] - 1.5)
    st = S.structure(bars_from_closes(seq), "M15", 2)
    ev = st["events"]
    assert ev[-1]["direction"] == -1 and ev[-1]["event_type"] == "CHoCH", [(e["event_type"], e["direction"]) for e in ev]
    assert st["direction"] == -1
    # continue down in a zigzag: lower highs and lower lows
    for leg in range(4):
        for _ in range(3):
            seq.append(seq[-1] + 1.0)
        for _ in range(5):
            seq.append(seq[-1] - 1.0)
    st2 = S.structure(bars_from_closes(seq), "M15", 2)
    labels = [s["label"] for s in st2["swings"][-6:]]
    assert "LH" in labels and "LL" in labels and st2["events"][-1]["event_type"] == "BOS", labels


def test_swings_known_only_after_confirmation():
    b = bars_from_closes(zigzag_up())
    st = S.structure(b, "M15", 2)
    for s in st["swings"]:
        assert s["confirmed_at"] >= s["t"] + 2 * 900, s          # n bars to the right must have closed


def _fvg_bars():
    b = [Bar(T0 + i * 900, 99.5, 100.0, 99.0, 99.5, 100) for i in range(20)]
    t = T0 + 20 * 900
    b.append(Bar(t, 100.0, 106.2, 99.9, 106.0, 100))            # displacement candle
    b.append(Bar(t + 900, 106.0, 107.0, 101.0, 106.5, 100))     # gap: 100.0 (a.high) .. 101.0 (c.low)
    return b


def test_fvg_states():
    b = _fvg_bars()
    z = [x for x in S.fvgs(b, "M15", CFG) if x["direction"] == 1]
    assert len(z) == 1 and z[0]["status"] == "FRESH" and z[0]["lower_price"] == 100.0 and z[0]["upper_price"] == 101.0, z
    for k in ("timeframe", "direction", "upper_price", "lower_price", "created_at", "status", "fill_percentage", "invalidated"):
        assert k in z[0], k
    t = b[-1].t
    b1 = b + [Bar(t + 900, 106.5, 106.6, 100.8, 101.5, 100)]      # 20 % into the gap
    z = [x for x in S.fvgs(b1, "M15", CFG) if x["direction"] == 1][0]
    assert z["status"] == "PARTIALLY_FILLED" and abs(z["fill_percentage"] - 20.0) < 0.5, z
    b2 = b1 + [Bar(t + 1800, 101.5, 101.6, 100.4, 101.2, 100)]    # 60 % -> MITIGATED (threshold 50 %)
    z = [x for x in S.fvgs(b2, "M15", CFG) if x["direction"] == 1][0]
    assert z["status"] == "MITIGATED", z
    b3 = b2 + [Bar(t + 2700, 101.2, 101.3, 99.3, 99.5, 100)]      # close below the far edge
    z = [x for x in S.fvgs(b3, "M15", CFG) if x["direction"] == 1][0]
    assert z["status"] == "INVALIDATED" and z["invalidated"], z


def test_fvg_no_duplicates():
    b = _fvg_bars()
    t = b[-1].t
    # a second displacement whose gap overlaps the first zone -> not a new FVG
    b += [Bar(t + 900, 100.3, 100.5, 100.2, 100.4, 100), Bar(t + 1800, 100.4, 107.1, 100.35, 107.0, 100),
          Bar(t + 2700, 107.0, 108.0, 101.5, 107.5, 100)]          # gap 100.5 .. 101.5 overlaps 100 .. 101
    z = [x for x in S.fvgs(b, "M15", CFG) if x["direction"] == 1 and x["lower_price"] < 101.6]
    assert len(z) == 1, [(x["lower_price"], x["upper_price"]) for x in z]
    # the same gap WITHOUT the older zone is detected -> the rule above really removed a duplicate
    lone = [Bar(T0 + i * 900, 100.3, 100.5, 100.2, 100.4, 100) for i in range(20)]
    lone += [Bar(T0 + 20 * 900, 100.4, 107.1, 100.35, 107.0, 100), Bar(T0 + 21 * 900, 107.0, 108.0, 101.5, 107.5, 100)]
    assert [x for x in S.fvgs(lone, "M15", CFG) if x["direction"] == 1 and x["lower_price"] == 100.5]


def test_order_block_one_per_event_and_mitigation():
    seq = zigzag_up()
    b = bars_from_closes(seq)
    st = S.structure(b, "M15", 2)
    obs = S.order_blocks(b, "M15", st["events"], CFG)
    ids = [o["source_structure_event"] for o in obs]
    assert len(ids) == len(set(ids)) and len(obs) >= 1, ids
    for o in obs:
        for k in ("direction", "timeframe", "zone_high", "zone_low", "created_at", "source_structure_event", "strength",
                  "mitigation_status", "invalidated"):
            assert k in o, k
        assert o["mitigation_status"] in ("FRESH", "MITIGATED", "INVALIDATED", "EXPIRED")
    # a crash far below every bullish OB invalidates them (close rule)
    b2 = bars_from_closes(seq + [seq[-1] - 3 * k for k in range(1, 15)])
    st2 = S.structure(b2, "M15", 2)
    obs2 = [o for o in S.order_blocks(b2, "M15", st2["events"], CFG) if o["direction"] == 1]
    assert obs2 and all(o["mitigation_status"] == "INVALIDATED" for o in obs2), [o["mitigation_status"] for o in obs2]


def _swing_high_then(last_bar):
    closes = [100, 101, 102, 103, 104, 105, 104, 103, 102, 101, 100, 101, 102, 103]
    b = bars_from_closes(closes, wick=0.05)
    t = b[-1].t + 900
    return b + [last_bar(t)]


def test_sweep_vs_breakout():
    swept = _swing_high_then(lambda t: Bar(t, 103.0, 105.5, 102.9, 104.6, 100))   # wick above 105.05, close back below
    st = S.structure(swept, "M15", 2)
    atr = S.atr_series(swept)
    liq = S.liquidity(swept, "M15", st, dict(CFG, sweep_require_reaction=False), atr)
    sw = [x for x in liq["sweeps"] if x["type"] == "SWING_H"]
    assert sw and sw[0]["direction"] == -1 and sw[0]["rejection"], liq["sweeps"]
    broke = _swing_high_then(lambda t: Bar(t, 103.0, 105.9, 102.9, 105.8, 100))    # close above = breakout
    st = S.structure(broke, "M15", 2)
    liq = S.liquidity(broke, "M15", st, dict(CFG, sweep_require_reaction=False), S.atr_series(broke))
    assert not [x for x in liq["sweeps"] if x["type"] == "SWING_H"], liq["sweeps"]
    assert any(lv.get("broken_at") for lv in liq["levels"] if lv["type"] == "SWING_H")


def test_sweep_needs_reaction_when_configured():
    swept = _swing_high_then(lambda t: Bar(t, 103.0, 105.5, 102.9, 104.6, 100))
    st = S.structure(swept, "M15", 2)
    liq = S.liquidity(swept, "M15", st, CFG, S.atr_series(swept))
    sw = [x for x in liq["sweeps"] if x["type"] == "SWING_H"]
    assert sw and not sw[0]["confirmed"], sw          # no structure reaction yet -> not confirmed


def test_equal_highs():
    closes = [100, 102, 104, 102, 100, 102, 104.05, 102, 100, 99, 98]
    b = bars_from_closes(closes, wick=0.05)
    st = S.structure(b, "M15", 2)
    liq = S.liquidity(b, "M15", st, dict(CFG, eq_tol_atr=0.2), S.atr_series(b))
    assert any(lv["type"] == "EQH" for lv in liq["levels"]), [lv["type"] for lv in liq["levels"]]


def test_premium_discount_explicit_range():
    c = S.clean_cfg(dict(range_source="custom", custom_high=110.0, custom_low=100.0))
    z = lambda px: S.dealing_range(c, px)["zone"]
    assert z(102) == "DISCOUNT" and z(105) == "EQUILIBRIUM" and z(108) == "PREMIUM"
    r = S.dealing_range(c, 102)
    assert r["high"] == 110 and r["low"] == 100 and r["equilibrium"] == 105 and r["source"] == "custom"
    bad = S.dealing_range(S.clean_cfg(dict(range_source="custom", custom_high=90, custom_low=100)), 95)
    assert bad["zone"] == "UNKNOWN"                   # invalid range -> unknown, never guessed
    pdr = S.dealing_range(S.clean_cfg(dict(range_source="prev_day")), 105, d1=[Bar(T0, 100, 110, 100, 105)])
    assert pdr["zone"] == "EQUILIBRIUM" and pdr["source"] == "prev_day"


def test_smc_setup_is_one_chain():
    import backtest
    d = backtest.synth_data(15)
    m15, h1, d1 = d["M15"], d["H1"], d["D1"]
    found = invalid = 0
    for i in range(300, len(m15) - 1, 3):
        t = m15[i].t + 900
        r = S.analyze({"M15": m15[:i + 1], "H1": [b for b in h1 if b.t + 3600 <= t],
                       "D1": [b for b in d1 if b.t + 86400 <= t]})
        for su in r["setups"].values():
            if su["status"] in ("WAITING_RETEST", "RETESTED"):
                found += 1
                assert su["stage"] >= 3 and su["components"]["break_"] and (su["components"]["fvg"] or su["components"]["ob"])
                assert su["components"]["sweep"]                        # require_sweep default
                assert su["sweep"]["i"] < su["structure_event"]["i"]    # the order of the chain
                assert 0 < su["confidence"] <= 1 and su["quality"] in ("LOW", "MEDIUM", "HIGH")
                assert su["entry_zone"][0] <= su["entry_zone"][1] and su["invalidation"] is not None
                assert su["setup_id"]
            if su["status"] == "INVALIDATED":
                invalid += 1
                assert su["confidence"] == 0.0 and su["quality"] == "NONE"
    assert found > 0, "no SMC setup in 15 days of demo data"


def test_smc_no_lookahead():
    import backtest
    d = backtest.synth_data(10)
    m15 = d["M15"]
    k = len(m15) - 200
    r1 = S.analyze({"M15": m15[:k], "H1": [], "D1": []})
    S._CACHE.clear()
    r2 = S.analyze({"M15": m15[:k], "H1": [], "D1": []})           # recomputed from scratch
    assert r1["setups"] == r2["setups"] and r1["structure"]["events"] == r2["structure"]["events"]
    # events known at bar k are a prefix of what is known later (history is never rewritten)
    r3 = S.analyze({"M15": m15[:k + 150], "H1": [], "D1": []}, dict(window=400))
    early = [e["id"] for e in S.structure(m15[:k][-400:], "M15", 2)["events"]]
    late = {e["id"] for e in S.structure(m15[:k + 150][-400 - 150:], "M15", 2)["events"]}
    overlap = [e for e in early[-5:]]
    assert all(e in late for e in overlap), "structure events were redrawn"
    assert r3["ok"]
