"""
Setup fingerprint: a normalized description of the market at entry, built ONLY from the decision engine's setup
matrix (which itself reads the existing engine / plugin results). Stored with every trade in the journal so the
analytics can group trades by setup. It is descriptive data, not an indicator and not a score.
"""
from __future__ import annotations

TREND = {1: "BULL", -1: "BEAR", 0: "FLAT", None: "NA"}


def yn(v):
    return "YES" if v is True else "NO" if v is False else "NA"


def build(matrix: dict) -> dict:
    """matrix = DecisionEngine.setup_matrix(side) -> fingerprint dict (flat, JSON-safe)."""
    r = {row["key"]: row for row in matrix.get("rows", [])}
    g = lambda k, f="raw": (r.get(k) or {}).get(f)
    score = matrix.get("score")
    fp = dict(
        side="BUY" if matrix.get("side") == 1 else "SELL" if matrix.get("side") == -1 else "NONE",
        h4=TREND.get(g("htf", "h4"), "NA"), h1=TREND.get(g("htf", "h1"), "NA"),
        m15=TREND.get(g("structure", "m15"), "NA"),
        trigger=g("trigger") or "NONE",
        pd=g("pd") or "NA",
        fvg=yn(g("fvg", "ok")), ob=yn(g("ob", "ok")), vwap=yn(g("vwap", "ok")), vp=yn(g("vp", "ok")),
        orderflow=yn(g("orderflow", "ok")), intermarket=yn(g("intermarket", "ok")),
        regime=(g("regime") or "NA").replace(" ", "_"), session=g("session") or "NA", news=g("news") or "NA",
        score=None if score is None else int(round(score)),
        score_bucket=None if score is None else int(score // 5 * 5),
    )
    fp["key"] = setup_key(fp)
    return fp


def setup_key(fp: dict) -> str:
    """Grouping key for setup performance: the structural part only (not score / session / news)."""
    parts = [fp.get("trigger", "NONE"), fp.get("pd", "NA")]
    if fp.get("fvg") == "YES":
        parts.append("FVG")
    if fp.get("ob") == "YES":
        parts.append("OB")
    return " + ".join(p for p in parts if p and p != "NA")
