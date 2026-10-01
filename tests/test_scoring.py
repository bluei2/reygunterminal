"""Score explanation (points lost per item) and the optional scoring modes (defaults = the original behaviour)."""
import harness  # noqa: F401  (redirects settings files)

from engine import clean_scoring, score_overall

W = clean_scoring(None)["weights"]


def it(name, d):
    return dict(name=name, dir=d, reason="")


RAW = {"Structure": [it("HTF bias", 1), it("H1 aligned", 1), it("M15 aligned", 1), it("Trigger", -1)],
       "Trend": [it("EMA", 1)], "Momentum": [it("RSI", 0)], "Volume": [it("VP", 0)]}


def test_defaults_keep_the_original_formula():
    sc = clean_scoring({"passing": 65})
    assert sc["neutral"] == "against" and sc["mode"] == "group" and sc["passing"] == 65
    ov, lost = score_overall(RAW, 1, W)
    assert ov == round((75 + 100 + 0 + 0) / 4, 1)                  # Structure 75, Trend 100, Momentum 0, Volume 0
    assert abs(sum(x["points"] for x in lost) - (100 - ov)) < 0.3   # the lost points explain the whole gap
    assert lost[0]["points"] == 25.0 and lost[0]["state"] == "neutral"   # a lone neutral vote costs a full group


def test_neutral_ignore_and_indicator_mode():
    ov, _ = score_overall(RAW, 1, W, neutral="ignore")
    assert ov == round((75 + 100) / 2, 1)                           # all-neutral groups drop out
    ov, lost = score_overall(RAW, 1, W, mode="indicator")
    assert ov == round(100 * 4 / 7, 1) and all(abs(x["points"] - 100 / 7) < 0.1 for x in lost)
    ov, _ = score_overall(RAW, 1, W, neutral="ignore", mode="indicator")
    assert ov == 80.0                                               # 4 agree of 5 directional items


def test_weights_and_no_bias():
    w = dict(W, Structure=2.0, Volume=0.0)
    ov, lost = score_overall(RAW, 1, w)
    assert ov == round((2 * 75 + 100 + 0) / 4, 1) and not any(x["category"] == "Volume" for x in lost)
    assert score_overall(RAW, 0, W) == (0.0, [])
    assert clean_scoring({"neutral": "bogus", "mode": "x"})["neutral"] == "against"
