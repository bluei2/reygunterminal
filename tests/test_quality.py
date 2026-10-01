"""Setup quality: grouped scoring (no double counting), conflicts + policy, regime modifiers, required conditions."""
import harness  # noqa: F401

import setup_quality as Q
from engine import Read


def read_with(groups, bias=1, trigger=True, overall=80.0):
    gs = []
    for cat, dirs in groups.items():
        gs.append(dict(category=cat, items=[dict(name=f"{cat}{i}", dir=d, reason="") for i, d in enumerate(dirs)],
                       agree=sum(1 for d in dirs if d == bias), against=sum(1 for d in dirs if d == -bias),
                       total=len(dirs), pct=0, weight=1))
    return Read(bias=bias, overall=overall, passing=70.0, trigger=trigger, groups=gs, passed=overall >= 70)


def smc(struct_dir=1, strength=1.0, zone="DISCOUNT", setup=None, opp=None, htf_dir=None):
    su = {"BUY": setup or dict(status="NONE", quality="NONE", confidence=0.0),
          "SELL": opp or dict(status="NONE", quality="NONE", confidence=0.0)}
    return dict(ok=True, structure=dict(direction=struct_dir, strength=strength, last_event=None),
                htf_structure=dict(direction=htf_dir if htf_dir is not None else struct_dir, strength=1.0),
                dealing_range=dict(zone=zone, source="htf_swing"), setups=su, fvgs=[], order_blocks=[],
                liquidity=dict(sweeps=[], levels=[]))


def test_many_similar_indicators_do_not_outvote_structure():
    one = Q.evaluate(1, read_with({"Trend": [1]}), smc())
    ten = Q.evaluate(1, read_with({"Trend": [1] * 10}), smc())
    assert one["confidence"] == ten["confidence"], (one["confidence"], ten["confidence"])
    wsum = sum(v["weight"] for v in ten["categories"].values() if v["available"] and v["weight"] > 0)
    assert ten["contributions"]["TREND"] <= ten["categories"]["TREND"]["weight"] / wsum + 1e-9   # capped by its weight
    # ten bearish trend indicators cannot flip a bullish structure: structure keeps its full contribution
    against = Q.evaluate(1, read_with({"Trend": [-1] * 10}), smc())
    assert against["categories"]["STRUCTURE"]["support"] == 1.0
    assert against["contributions"]["STRUCTURE"] > against["contributions"].get("TREND", 0)


def test_smc_counts_once():
    setup = dict(status="RETESTED", quality="HIGH", confidence=0.9, direction=1, stage=4, entry_zone=[1, 2],
                 invalidation=0.5, setup_id="x", components=dict(sweep=True, break_=True, fvg=True, ob=True))
    q = Q.evaluate(1, read_with({}), smc(setup=setup))
    assert q["categories"]["SMC"]["items"] == 1 and q["categories"]["SMC"]["support"] == 0.9
    assert q["categories"]["LIQUIDITY"]["weight"] == 0.0          # the sweep is inside the SMC chain


def test_confidence_is_0_to_1():
    q = Q.evaluate(1, read_with({"Trend": [1, 1], "Momentum": [1]}), smc())
    assert 0.0 <= q["confidence"] <= 1.0
    for v in q["categories"].values():
        assert 0.0 <= v["support"] <= 1.0 and 0.0 <= v["confidence"] <= 1.0


def test_momentum_conflict_is_reported_not_averaged():
    q = Q.evaluate(1, read_with({"Momentum": [-1, -1, -1]}), smc())
    codes = [c["code"] for c in q["conflicts"]]
    assert "MOMENTUM_CONFLICT" in codes and q["structure_alignment"] == "BUY", codes
    assert q["conflict_block"] is None                             # structure_priority: momentum only warns


def test_high_conflict_policy_blocks():
    cfg = dict(conflict_policy="block_high_conflict", high_conflict_count=2)
    q = Q.evaluate(1, read_with({"Momentum": [-1, -1], "Trend": [-1, -1]}), smc(), cfg=cfg)
    assert q["high_conflict"] and q["conflict_block"]["code"] == "HIGH_CONFLICT", q["conflicts"]


def test_structure_conflict_blocks_unless_warn_only():
    q = Q.evaluate(1, read_with({}, bias=-1), smc(struct_dir=-1, htf_dir=-1))
    assert q["conflict_block"] and q["conflict_block"]["code"] == "STRUCTURE_CONFLICT"
    q2 = Q.evaluate(1, read_with({}, bias=-1), smc(struct_dir=-1), cfg=dict(conflict_policy="warn_only"))
    assert q2["conflict_block"] is None and q2["conflicts"]


def test_regime_modifies_requirements_not_entries():
    hv = Q.evaluate(1, read_with({}), smc(), regime=dict(label="VOLATILE", dir=0, htf_read=dict(label="VOLATILE")))
    assert hv["regime"]["cls"] == "HIGH_VOLATILITY" and hv["regime"]["risk_multiplier"] == 0.5
    assert hv["min_confidence"] >= 0.10 and "min_confidence" in hv["required"]
    ct = Q.evaluate(-1, read_with({}, bias=-1), smc(struct_dir=-1), regime=dict(label="TREND UP", dir=1,
                                                                                htf_read=dict(label="TREND UP")))
    assert ct["min_confidence"] >= 0.15, ct["regime"]
    bo = Q.evaluate(1, read_with({}), smc(), regime=dict(label="VOLATILE", dir=1, htf_read=dict(label="RANGE")))
    assert bo["regime"]["cls"] == "BREAKOUT" and "displacement" in bo["required"]
    off = Q.evaluate(1, read_with({}), smc(), regime=dict(label="VOLATILE", dir=0, htf_read=dict(label="VOLATILE")),
                     cfg=dict(regime_rules=False))
    assert off["min_confidence"] == 0.0 and off["regime"]["cls"] is None


def test_required_conditions_are_configurable():
    q = Q.evaluate(1, read_with({}), smc(), cfg=dict(required=["score", "htf", "trigger", "smc_setup"]))
    assert "smc_setup" in q["missing_required"] and q["conditions"]["smc_setup"]["cls"] == "REQUIRED"
    q2 = Q.evaluate(1, read_with({}), smc())
    assert not q2["missing_required"] and q2["conditions"]["smc_setup"]["cls"] == "CONFIRMATION"
    assert q2["conditions"]["momentum"]["cls"] == "OPTIONAL"


def test_invalid_config_falls_back_to_safe_defaults():
    c = Q.clean(dict(weights=dict(TREND="abc", SMC=-5), required="everything", min_confidence="x",
                     conflict_policy="yolo"))
    assert c["weights"]["TREND"] == Q.DEFAULT_WEIGHTS["TREND"] and c["weights"]["SMC"] == 0.0
    assert c["required"] == Q.DEFAULT_REQUIRED and c["min_confidence"] == 0.0
    assert c["conflict_policy"] == "structure_priority"


def test_explanation_never_bare():
    q = Q.evaluate(1, read_with({"Momentum": [-1]}), smc())
    e = Q.explain(1, q, [dict(code="SPREAD_TOO_HIGH", text="spread 0.9", level="hard")], "BLOCKED")
    assert e["head"] == "BUY BLOCKED" and e["reasons"] and any("SPREAD" in x for x in e["but"]), e
