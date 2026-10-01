"""FORECAST REGIME: read-only prediction layer (creation, regimes, lifecycle, ambiguity, replay, safety)."""
import copy
import json
import os
import time

from harness import TMP, cycle, make_tm

import forecast as F
from engine import Bar, Event, Read, Swing, TFState

T0 = 1_790_000_000 - 1_790_000_000 % 300


def states(d=1, m15_trend=None, trig=True, atr=2.0, price=2650.0):
    st = {k: TFState(tf=k, trend=d) for k in ("H4", "H1", "M15", "M5")}
    st["M15"].trend = d if m15_trend is None else m15_trend
    for s in st.values():
        s.atr, s.price = atr, price
    st["M15"].sl = Swing(price - 4.0, T0 - 3600) if d == 1 else None
    st["M15"].sh = Swing(price + 4.0, T0 - 3600) if d == -1 else None
    if trig:
        st["M5"].events.append(Event("M5", "BOS", d, price - d * 1.0, T0 - 600))
    return st


def view(d=1, bias=None, score=80.0, trig=True, regime="TREND UP", htf="TREND UP", t=T0, price=2650.0, **kw):
    b = d if bias is None else bias
    rd = Read(bias=b, overall=score, passing=70.0, trigger=trig, groups=[dict(category="Structure", items=[])],
              passed=score >= 70)
    return dict(t=t, read=rd, states=states(d, trig=trig, price=price, **kw), smc=dict(ok=False),
                regime=dict(label=regime, adx=30), htf_regime=dict(label=htf), session="LONDON", news=None,
                spread=None, symbol="XAUUSD", utc_offset=0)


def cfg(**kw):
    return F.clean_settings(dict(dict(min_confidence=0, min_quality="C"), **kw))


def rec(d=1, entry_type="MARKET", entry=100.0, sl=95.0, zone=None, trigger=None, status="WAITING"):
    risk = abs(entry - sl)
    return dict(id="FC-T", symbol="XAUUSD", dir=d, direction=F.DIRW[d], tf="M5", entry_type=entry_type,
                entry=entry, sl=sl, tp1=entry + d * risk, tp2=entry + d * 2 * risk, tp3=entry + d * 3 * risk,
                risk_distance=risk, entry_zone_low=zone[0] if zone else None, entry_zone_high=zone[1] if zone else None,
                trigger=trigger, status=status, created_at=T0, regime="TREND_BULL" if d == 1 else "TREND_BEAR",
                confidence=70, analyser_score=80, setup_quality="A", smc_setup_id=None, setup_tf="M15")


def bar(i, o, h, l, c):
    return Bar(T0 + i * 300, o, h, l, c)


# ---------------------------------------------------------------- regimes / creation
def test_forecast_creation_bullish():
    fc, why = F.build(view(1), F.get_regime(view(1), cfg()), cfg())
    assert fc and fc["direction"] == "LONG" and fc["regime"] == "TREND_BULL", why
    assert fc["sl"] < fc["entry"] < fc["tp1"] < fc["tp2"] < fc["tp3"] and fc["rr"] == 2.0
    assert fc["sl_reason"] and fc["tp_reason"] and fc["why"] and 0 <= fc["confidence"] <= 100
    assert abs((fc["tp2"] - fc["entry"]) / (fc["entry"] - fc["sl"]) - 2.0) < 0.01
    assert fc["timeframes"]["H4"] == "BULL" and fc["timeframes"]["M1"] == "REFINEMENT"


def test_bearish_forecast():
    v = view(-1, regime="TREND DOWN", htf="TREND DOWN")
    fc, why = F.build(v, F.get_regime(v, cfg()), cfg())
    assert fc and fc["direction"] == "SHORT" and fc["sl"] > fc["entry"] > fc["tp2"], why


def test_no_bias_rejection():
    v = view(1, bias=0)
    reg = F.get_regime(v, cfg())
    assert reg["regime"] == "NO_BIAS" and reg["direction"] == 0
    assert F.build(v, reg, cfg())[0] is None


def test_no_data():
    v = view(1)
    v["states"] = {}
    assert F.get_regime(v, cfg())["regime"] == "NO_DATA"


def test_range_detection():
    v = view(1, regime="RANGE", htf="RANGE", trig=False)
    reg = F.get_regime(v, cfg())
    assert reg["regime"] == "RANGING" and F.build(v, reg, cfg())[0] is None


def test_pullback_and_reversal_regimes():
    v = view(1, m15_trend=-1)
    assert F.get_regime(v, cfg())["regime"] == "PULLBACK_BULL"
    r = view(-1, regime="TREND DOWN", htf="TREND DOWN")
    r["states"]["H4"].trend = 1                     # major TF still bull, H1 + M15 CHoCH down = reversal
    r["states"]["M15"].events.append(Event("M15", "CHoCH", -1, 2651.0, T0 - 900))
    r["read"].bias = 1
    assert F.get_regime(r, cfg())["regime"] == "REVERSAL_BEAR"


def test_confidence_separate_from_score():
    v = view(1, score=95.0)
    fc, _ = F.build(v, F.get_regime(v, cfg()), cfg())
    assert fc["analyser_score"] == 95.0 and v["read"].overall == 95.0          # score read, never changed
    assert fc["confidence"] == sum(x["points"] for x in fc["confidence_factors"]) or fc["risks"]


def test_low_evidence_no_forecast():
    v = view(1, score=40.0, trig=False)
    fc, why = F.build(v, F.get_regime(v, cfg()), F.clean_settings(None))
    assert fc is None and why


# ---------------------------------------------------------------- lifecycle
def test_market_entry_active_immediately():
    eng = F.ForecastEngine(cfg())
    fc, why = eng.create_forecast(view(1), price=2650.0)
    assert fc and fc["status"] == "TRIGGERED" and fc["trigger_price"] == fc["entry"], why


def test_limit_zone_trigger():
    c = cfg()
    fc = rec(1, "LIMIT", 100.0, 95.0, zone=(99.0, 100.0))
    assert not F.check_trigger(fc, bar(1, 103, 104, 101, 102), c) and fc["status"] == "WAITING"
    assert F.check_trigger(fc, bar(2, 102, 102.5, 99.5, 101), c) and fc["status"] == "TRIGGERED"
    assert fc["trigger_price"] == 100.0 and fc["sl_at_trigger"] == 95.0


def test_breakout_trigger_close_and_touch():
    fc = rec(1, "BREAKOUT", 105.0, 100.0, trigger=105.0)
    assert not F.check_trigger(fc, bar(1, 104, 105.5, 103.8, 104.9), cfg(breakout_confirm="close"))
    assert F.check_trigger(fc, bar(2, 104.9, 106, 104.5, 105.6), cfg(breakout_confirm="close"))
    assert fc["trigger_price"] == 105.6
    fc2 = rec(1, "BREAKOUT", 105.0, 100.0, trigger=105.0)
    assert F.check_trigger(fc2, bar(1, 104, 105.5, 103.8, 104.9), cfg(breakout_confirm="touch"))


def _running(d=1):
    fc = rec(d, "MARKET", 100.0, 95.0 if d == 1 else 105.0)
    F._trigger(fc, T0, 100.0)
    return fc


def test_tp_hit_and_sl_hit():
    c = cfg()
    fc = _running()
    assert F.check_result(fc, bar(1, 100, 106, 99, 105), c) is None and fc["tp1_hit"]
    r = F.check_result(fc, bar(2, 105, 110.5, 104, 110), c)
    assert r and r["status"] == "TP_HIT" and r["r"] == 2.0 and r["result"] == "TP2"
    fc2 = _running()
    r2 = F.check_result(fc2, bar(1, 100, 101, 94.5, 95), c)
    assert r2["status"] == "SL_HIT" and r2["r"] == -1.0 and r2["result_reason"].startswith("Forecast reached structural")
    assert fc["mfe_r"] >= 2.0 and fc2["mae_r"] <= -1.0


def test_ambiguous_same_bar():
    both = bar(1, 100, 111, 94, 100)
    a = F.check_result(_running(), both, cfg(ambiguous_rule="sl_first"))
    assert a["status"] == "SL_HIT" and a["ambiguous"] and "AMBIGUOUS_BAR" in a["result_reason"]
    b = F.check_result(_running(), both, cfg(ambiguous_rule="tp_first"))
    assert b["status"] == "TP_HIT" and b["ambiguous"]
    x = F.check_result(_running(), both, cfg(ambiguous_rule="exclude"))
    assert x["status"] == "AMBIGUOUS" and x["r"] is None
    st = F.statistics([a, b, x])["overall"]
    assert st["ambiguous"] == 3 and st["tp"] == 1 and st["sl"] == 1


def test_live_tick_order_is_used():
    c = cfg()
    fc = _running()
    assert not F.check_tick(fc, T0 + 10, 108.0, 108.2, c)
    assert F.check_tick(fc, T0 + 20, 110.1, 110.3, c) and fc["status"] == "TP_HIT"   # the real order decides


def test_expiration_not_triggered():
    eng = F.ForecastEngine(cfg(expiry_bars=3))
    fc = rec(1, "LIMIT", 100.0, 95.0, zone=(99.0, 100.0))
    eng.active[fc["id"]] = fc
    v = view(1)
    for i in range(1, 4):
        eng.update_forecast(fc, v, bar(i, 103, 104, 102, 103))
    assert fc["status"] == "EXPIRED" and fc["result"] == "NOT_TRIGGERED" and not fc.get("trigger_time")
    assert fc["r"] is None                                     # an expired forecast is never counted as a loss


def test_invalidation_before_trigger_is_not_sl():
    eng = F.ForecastEngine(cfg())
    fc = rec(1, "LIMIT", 100.0, 95.0, zone=(99.0, 100.0))
    assert eng.update_forecast(fc, view(1, bias=-1), bar(1, 103, 104, 102, 103)) == "INVALIDATED"
    assert fc["status"] == "INVALIDATED" and fc["invalidation_reason"] == "HTF_BIAS_FLIPPED" and fc["r"] is None
    fc2 = rec(1, "LIMIT", 100.0, 95.0, zone=(99.0, 100.0))
    fc2["entry_zone_high"] = 90.0                              # zone never touched but price closes below the SL
    fc2["entry_zone_low"] = 89.0
    eng.update_forecast(fc2, view(1), bar(1, 96, 96.5, 93, 94))
    assert fc2["status"] == "INVALIDATED" and fc2["invalidation_reason"] == "ENTRY_ZONE_INVALIDATED"
    assert F.statistics([fc, fc2])["overall"]["sl"] == 0


def test_duplicate_prevention():
    eng = F.ForecastEngine(cfg(entry_mode="market"))
    v = view(1)
    a, _ = eng.create_forecast(v, price=2650.0)
    b, why = eng.create_forecast(v, price=2650.0)
    assert a and b is None and ("duplicate" in why or "KEEP" in why), why
    assert len(eng.journal.all()) == 1


def test_reforecasting_keeps_old_record():
    eng = F.ForecastEngine(cfg())
    old = rec(1, "LIMIT", 2600.0, 2595.0, zone=(2599.0, 2600.0))
    old["id"] = eng.journal.new_id()
    old["fingerprint"] = "x"
    old["atr"] = 2.0
    eng.active[old["id"]] = old
    eng.journal.save(old, "CREATED")
    snap = copy.deepcopy(old)
    new, why = eng.create_forecast(view(1), price=2650.0)                   # entry ~50 away: material change
    assert new and new["id"] != old["id"], why
    assert old["status"] == "INVALIDATED" and old["invalidation_reason"] == "SETUP_REPLACED"
    assert old["entry"] == snap["entry"] and old["created_at"] == snap["created_at"]   # never rewritten
    assert {r["id"] for r in eng.journal.all()} == {old["id"], new["id"]}


def test_snapshot_preserved_after_trigger():
    eng = F.ForecastEngine(cfg())
    fc, _ = eng.create_forecast(view(1), price=2650.0)
    s = dict(fc["snapshot"])
    fc["status"] = "WAITING"
    fc["entry_type"] = "LIMIT"
    fc["entry_zone_low"], fc["entry_zone_high"] = fc["entry"] - 1, fc["entry"]
    F.check_trigger(fc, Bar(fc["created_at"], fc["entry"] - 0.5, fc["entry"], fc["entry"] - 3, fc["entry"] - 2), eng.cfg)
    assert fc["snapshot"] == s and s["score_at_creation"] == 80.0 and s["confidence_at_creation"] == fc["confidence"]
    for k in ("score_at_creation", "bias_at_creation", "regime_at_creation", "structure_at_creation",
              "entry_at_creation", "sl_at_creation", "tp_at_creation", "confidence_at_creation"):
        assert k in s


def test_journal_is_persistent_and_append_only():
    path = os.path.join(TMP, "fc_test.jsonl")
    if os.path.exists(path):
        os.remove(path)
    j = F.ForecastJournal(path)
    eng = F.ForecastEngine(cfg(entry_mode="market"), j)
    fc, _ = eng.create_forecast(view(1), price=2650.0)
    first = open(path).readline()
    F.check_result(fc, bar(1, 2650, 2651, 2600, 2600), eng.cfg)
    j.save(fc, fc["status"])
    lines = open(path).read().splitlines()
    assert lines[0] == first.rstrip("\n") and len(lines) >= 3
    again = F.ForecastJournal(path)
    assert again.records[fc["id"]]["status"] == "SL_HIT" and again.new_id() != fc["id"]


def test_statistics_and_calibration():
    rs = []
    for i in range(6):
        r = _running()
        r.update(id=f"FC-{i}", confidence=55 + i * 8, analyser_score=72 + i * 4, session="LONDON")
        F.check_result(r, bar(1, 100, 111 if i % 2 else 101, 99 if i % 2 else 94, 100), cfg())
        rs.append(r)
    st = F.statistics(rs, min_samples=30)
    assert st["overall"]["forecasts"] == 6 and st["overall"]["tp"] == 3 and st["overall"]["sl"] == 3
    assert st["calibration"] and all(not row["enough_data"] for row in st["calibration"])
    assert st["by_score"] and st["by_regime"] and st["by_quality"] and st["by_session"] and st["by_month"]


# ---------------------------------------------------------------- replay / no look-ahead
_REPLAY = {}


_DATA = {}


def _replay(cut_days=None):
    import backtest
    from superchart import Hub
    if "d" not in _DATA:                                       # one data set: synth_data is aligned to "now"
        _DATA["d"] = backtest.synth_data(6, seed=11)
    d = _DATA["d"]
    if cut_days:
        cut_t = d["M5"][-1].t - cut_days * 86400
        d = {k: ([b for b in v if b.t < cut_t] if isinstance(v, list) else v) for k, v in d.items()}
    h = Hub(backtest.HistSource("XAUUSD", d))
    start = _DATA["d"]["M5"][-1].t - 4 * 86400
    return F.replay(h, "M5", start, None, cfg(min_confidence=40))


def test_historical_replay_reproducible():
    a = _replay()
    b = _replay()
    strip = lambda rs: [{k: v for k, v in r.items() if k not in ("journal_t",)} for r in rs]
    assert a["records"] and strip(a["records"]) == strip(b["records"])
    assert a["stats"]["overall"]["forecasts"] + a["stats"]["active"] == len(a["records"])
    _REPLAY["full"] = a


def test_replay_has_no_lookahead():
    full = _REPLAY.get("full") or _replay()
    part = _replay(cut_days=1)                                 # the same history without its last day
    last_t = max(r["created_at"] for r in part["records"]) if part["records"] else 0
    keys = ("id", "created_at", "direction", "regime", "entry", "sl", "tp2", "confidence", "analyser_score", "snapshot")
    early_full = [{k: r[k] for k in keys} for r in full["records"] if r["created_at"] <= last_t]
    early_part = [{k: r[k] for k in keys} for r in part["records"]]
    assert early_full == early_part                            # creation never depended on later bars


# ---------------------------------------------------------------- safety
def test_forecast_module_cannot_trade():
    import ast
    tree = ast.parse(open(F.__file__).read())
    imported = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            imported |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            imported.add((n.module or "").split(".")[0])
        elif isinstance(n, ast.Attribute):
            assert n.attr not in ("trader", "gate", "execution", "order_send", "open", "confirmations"), n.attr
        elif isinstance(n, ast.Constant) and isinstance(n.value, str):
            assert "/api/trade" not in n.value
    assert not imported & {"trading", "mt5_ops", "trade_gate", "execution_guard", "trade_confirmation",
                           "account_worker", "autotrade", "position_manager", "MetaTrader5"}, imported
    hub, tm = make_tm(0)
    tm.set_armed(True)
    svc = F.ForecastService.__new__(F.ForecastService)
    svc.hub, svc.settings = hub, cfg()
    svc.engine = F.ForecastEngine(svc.settings)
    fc = _running()
    svc.engine.active[fc["id"]] = fc
    n = len(tm.master.backend.calls)
    svc.engine.on_tick(T0 + 5, 111.0, 111.2)                   # a forecast "wins"
    cycle(tm)
    assert fc["status"] == "TP_HIT" and not tm.groups
    assert "open_market" not in tm.master.backend.calls[n:] and "modify" not in tm.master.backend.calls[n:]


def test_forecast_api_never_calls_trade_api():
    src = open(os.path.join(os.path.dirname(F.__file__), "superchart.py")).read()
    i = src.index('elif u.path.startswith("/api/forecast/")')
    j = src.index("elif", i + 10)
    block = src[i:j]
    assert "trade_api" not in block and "trader" not in block and "gate" not in block


def test_autotrade_gate_unaffected_by_forecast():
    import test_gate as G
    hub, tm, g = G.setup()
    r1 = g.evaluate(G.sig(g, t=int(time.time()) // 300 * 300 - 3000), "auto", 0.01, record=False)
    svc = F.ForecastService.__new__(F.ForecastService)
    svc.hub, svc.settings = hub, cfg()
    svc.engine = F.ForecastEngine(svc.settings)
    svc.engine.active["FC-X"] = _running()
    hub.forecast = svc
    r2 = g.evaluate(G.sig(g, t=int(time.time()) // 300 * 300 - 3000), "auto", 0.01, record=False)
    assert (r1["allowed"], r1["block_code"], r1["lot"]) == (r2["allowed"], r2["block_code"], r2["lot"])
    assert json.dumps([x["codes"] for x in r1["gates"]]) == json.dumps([x["codes"] for x in r2["gates"]])


def test_chart_draws_forecast_distinctly():
    import test_plugins as TP
    h = TP.hub()
    svc = F.ForecastService.__new__(F.ForecastService)
    svc.hub, svc.settings = h, cfg()
    svc.engine = F.ForecastEngine(svc.settings)
    fc = _running()
    fc["id"] = "FC-000017"
    svc.engine.active[fc["id"]] = fc
    h.forecast = svc
    try:
        lv = [x for x in h.chart("M15")["levels"] if x.get("owner") == "forecast"]
        labels = {x["label"] for x in lv}
        assert {"FC17 E", "FC17 SL", "FC17 TP2"} <= labels and all(x["type"] == "forecast" for x in lv)
        assert all(x["color"].startswith("#") and x["color"] in ("#c084fc", "#a855f7", "#e9d5ff", "#d8b4fe") for x in lv)
        svc.settings["draw"] = False
        assert not [x for x in h.chart("M15")["levels"] if x.get("owner") == "forecast"]
    finally:
        h.forecast = None


# ---------------------------------------------------------------- forecast profile (own "demo" indicator setup)
class _Up:
    pass


def test_forecast_profile_is_isolated_from_live():
    import test_plugins as TP
    h = TP.hub()
    live_scoring = json.dumps(h.scoring, sort_keys=True)
    live_state = {p.key: (p.scoring, p.enabled, list(p.chart_tfs), json.dumps(p.obj.params, sort_keys=True))
                  for p in h.plugins}
    ema = next(p for p in h.plugins if p.key == "ema.EMAPair")
    prof = dict(use_live=False, scoring=dict(passing=55.0, weights=h.scoring["weights"]),
                plugins={"ema.EMAPair": dict(score=True, chart=True, chart_tfs=["H1"], vote_tf=None, params={"fast": 9})})
    sh = F.profile_hub(h.src, prof, h.utc_offset)
    sp = next(p for p in sh.plugins if p.key == "ema.EMAPair")
    assert sp is not ema and sp.scoring and sp.enabled and sp.chart_tfs == ["H1"] and sp.obj.params["fast"] == 9
    assert sh.scoring["passing"] == 55.0
    assert not any(p.scoring for p in sh.plugins if p.key != "ema.EMAPair")            # missing = OFF
    t_end = h.src.bars("M5")[-3].t + 300
    r = sh.read_at(t_end)
    assert any(it["name"].startswith("EMA") for g in r.groups for it in g["items"]) and r.passing == 55.0
    # the live analyser is untouched
    assert json.dumps(h.scoring, sort_keys=True) == live_scoring
    assert {p.key: (p.scoring, p.enabled, list(p.chart_tfs), json.dumps(p.obj.params, sort_keys=True))
            for p in h.plugins} == live_state


def test_forecast_service_uses_profile_and_chart():
    import test_plugins as TP
    h = TP.hub()
    svc = F.ForecastService.__new__(F.ForecastService)
    svc.hub, svc.settings = h, cfg()
    svc.engine = F.ForecastEngine(svc.settings)
    svc.profile = dict(use_live=True, plugins={}, scoring={})
    svc._shadow = None
    assert svc.view_hub is h
    old = F.PROFILE_FILE
    F.PROFILE_FILE = os.path.join(TMP, "fc_profile_test.json")
    try:
        res = svc.set_profile(None, copy_live=True)                # "Copy from live" -> own setup
        assert res["ok"] and not svc.profile["use_live"] and svc.view_hub is not h
        assert F.load_profile(F.PROFILE_FILE)["use_live"] is False
        v = svc._view(h.src.bars("M5")[-3].t + 300)
        assert v["read"] is not None and v["states"]
        fc = _running()
        fc.update(id="FC-000005", created_at=h.src.bars("M15")[-5].t + 300, tf="M5")
        svc.engine.journal.save(fc, "CREATED")
        svc.engine.active[fc["id"]] = fc
        ch = svc.chart("M15")
        assert ch["bars"] and ch["profile"] == "FORECAST"
        assert any(lv.get("owner") == "forecast" and lv["label"].startswith("FC5") for lv in ch["levels"])
        assert any(m.get("owner") == "forecast" and m["text"].startswith("FC5") for m in ch["markers"])
    finally:
        F.PROFILE_FILE = old


# ---------------------------------------------------------------- volume-profile setups + comparison
VP = dict(poc=100.0, vah=110.0, val=90.0, high=115.0, low=85.0, day=0)


def vp_view(bars, bias=1, vp=VP):
    v = view(1 if bias >= 0 else -1, bias=bias)
    v.update(vp=vp, recent=bars)
    v["states"]["M15"].atr = 4.0
    return v


def test_vp_poc_rejection_long_targets_vah():
    bars = [bar(0, 104, 105, 102, 103), bar(1, 103, 103.5, 99.8, 103.2)]        # came from above, wick into POC, closed up
    fc, why = F.build_vp(vp_view(bars, 1), F.get_regime(vp_view(bars, 1), cfg()), cfg(), "vp_poc")
    assert fc and fc["direction"] == "LONG" and fc["tp2"] == 110.0 and fc["sl"] < 99.8, why
    assert fc["setup_type"] == "vp_poc" and fc["entry_type"] == "MARKET" and fc["rr"] >= 1.5


def test_vp_poc_rejection_short_targets_val():
    bars = [bar(0, 96, 97, 95, 96), bar(1, 96.5, 100.3, 96.2, 96.6)]           # came from below, rejected at POC
    v = vp_view(bars, -1)
    fc, why = F.build_vp(v, F.get_regime(v, cfg()), cfg(), "vp_poc")
    assert fc and fc["direction"] == "SHORT" and fc["tp2"] == 90.0 and fc["sl"] > 100.3, why


def test_vp_edge_fade_targets_poc():
    bars = [bar(0, 107, 109, 106, 108), bar(1, 108, 112, 107.5, 108.2)]         # poked above VAH, closed back inside
    v = vp_view(bars, -1)
    fc, why = F.build_vp(v, F.get_regime(v, cfg()), cfg(), "vp_edge")
    assert fc and fc["direction"] == "SHORT" and fc["tp2"] == 100.0 and fc["sl"] > 112, why


def test_vp_filters_rr_and_bias():
    bars = [bar(0, 104, 105, 102, 103), bar(1, 103, 103.5, 99.8, 103.2)]
    v = vp_view(bars, -1)                                                      # bias BEAR vs a LONG rejection
    assert F.build_vp(v, F.get_regime(v, cfg()), cfg(), "vp_poc")[0] is None
    near = dict(VP, vah=104.0)                                                 # target too close -> RR too small
    v2 = vp_view(bars, 1, near)
    fc, why = F.build_vp(v2, F.get_regime(v2, cfg()), cfg(), "vp_poc")
    assert fc is None and "RR" in why, why
    assert F.build_vp(vp_view(bars, 1, None), F.get_regime(vp_view(bars, 1, None), cfg()), cfg(), "vp_poc")[0] is None


def test_setups_are_compared_independently():
    eng = F.ForecastEngine(cfg(entry_mode="market", setups=["structure", "vp_poc"]))
    bars = [bar(0, 2654, 2655, 2652, 2653), bar(1, 2653, 2653.5, 2649.8, 2653.2)]
    v = view(1)
    v.update(vp=dict(poc=2650.0, vah=2662.0, val=2640.0, high=2665.0, low=2635.0, day=0), recent=bars)
    made = eng.create_all(v, price=2653.2)
    assert sorted(f["setup_type"] for f in made) == ["structure", "vp_poc"], eng.last_why_not
    st = F.statistics([F._finish(dict(f), "TP_HIT", T0, f["tp2"], "TP2", "x", 1.0) for f in made])
    assert {r["key"] for r in st["comparison"]} == {"structure", "vp_poc"} and all(r["name"] for r in st["comparison"])


def test_vp_view_uses_previous_day_only():
    import backtest
    from superchart import Hub
    d = backtest.synth_data(4, seed=3)
    h = Hub(backtest.HistSource("XAUUSD", d))
    m5 = d["M5"]
    t_end = m5[-40].t + 300
    a = F._vp_at(h, t_end)
    d2 = dict(d)
    d2["M5"] = [b if b.t // 86400 < t_end // 86400 else type(b)(b.t, b.o + 40, b.h + 60, b.l + 30, b.c + 40, b.v * 5)
                for b in m5]                                                    # change TODAY only
    F._VP_CACHE.clear()
    b2 = F._vp_at(Hub(backtest.HistSource("XAUUSD", d2)), t_end)
    assert a and b2 and (a["poc"], a["vah"], a["val"]) == (b2["poc"], b2["vah"], b2["val"])


# ---------------------------------------------------------------- structure + POC pullback (combined)
def sp_view(d=1, poc=2646.0, price=2650.0, **kw):
    v = view(d, price=price, regime="TREND UP" if d == 1 else "TREND DOWN",
             htf="TREND UP" if d == 1 else "TREND DOWN", **kw)
    v["states"]["M15"].sh = Swing(price + 12.0, T0 - 1800) if d == 1 else v["states"]["M15"].sh
    v["states"]["M15"].sl = Swing(price - 12.0, T0 - 1800) if d == -1 else v["states"]["M15"].sl
    if d == 1:
        v["states"]["M15"].sl = Swing(price - 7.0, T0 - 3600)                  # the HL below the POC
    else:
        v["states"]["M15"].sh = Swing(price + 7.0, T0 - 3600)
    v.update(vp=dict(poc=poc, vah=poc + 15, val=poc - 10, high=poc + 20, low=poc - 15, day=0),
             recent=[bar(0, price, price + 1, price - 1, price)])
    return v


def test_struct_poc_long_limit_at_poc_structure_target():
    v = sp_view(1)
    fc, why = F.build_struct_poc(v, F.get_regime(v, cfg()), cfg())
    assert fc and fc["direction"] == "LONG" and fc["entry_type"] == "LIMIT", why
    assert fc["entry_zone_low"] <= 2646.0 <= fc["entry_zone_high"] and fc["setup_type"] == "struct_poc"
    assert fc["sl"] < 2643.0 and "structure" in fc["sl_reason"]                 # beyond the M15 HL
    assert fc["tp2"] == 2662.0 and "structure target" in fc["tp_reason"][1]      # the M15 swing high


def test_struct_poc_short_mirror():
    v = sp_view(-1, poc=2654.0)
    fc, why = F.build_struct_poc(v, F.get_regime(v, cfg()), cfg())
    assert fc and fc["direction"] == "SHORT" and fc["entry_zone_low"] <= 2654.0 <= fc["entry_zone_high"], why
    assert fc["sl"] > 2657.0 and fc["tp2"] == 2638.0


def test_struct_poc_rules():
    c = cfg()
    v = sp_view(1, poc=2640.0)                    # POC below the M15 HL (2643): a return there breaks structure
    assert F.build_struct_poc(v, F.get_regime(v, c), c)[0] is None
    v = sp_view(1, poc=2652.0)                    # price already below the POC: not a pullback from above
    assert F.build_struct_poc(v, F.get_regime(v, c), c)[0] is None
    v = sp_view(1, poc=2646.0)
    v["states"]["M15"].sl = Swing(2630.0, T0 - 3600)
    v["vp"]["poc"] = 2632.0                        # 18 away with ATR 2 -> too far
    assert "too far" in F.build_struct_poc(v, F.get_regime(v, c), c)[1]
    r = view(1, regime="RANGE", htf="RANGE", trig=False)
    r.update(vp=sp_view(1)["vp"])                 # no trending structure -> no setup
    assert F.build_struct_poc(r, F.get_regime(r, c), c)[0] is None


def test_struct_poc_waits_for_pullback_and_misses():
    eng = F.ForecastEngine(cfg(setups=["struct_poc"]))
    v = sp_view(1)
    fc, why = eng.create_forecast(v, setup="struct_poc")
    assert fc and fc["status"] == "WAITING", why
    assert eng.update_forecast(fc, v, bar(1, 2650, 2651, 2648, 2650.5)) is None      # no pullback yet
    assert eng.update_forecast(fc, v, bar(2, 2650, 2650.2, 2645.9, 2647)) == "TRIGGERED"
    fc2, _ = F.ForecastEngine(cfg(setups=["struct_poc"])).create_forecast(sp_view(1), setup="struct_poc")
    assert F.check_invalidation(fc2, sp_view(1), bar(1, 2650, fc2["tp2"] + 0.5, 2649, 2651)) == "MISSED_NO_PULLBACK"
