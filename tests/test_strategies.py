"""STRATEGIES: trend / pullback / consolidation - own indicator sets, no look-ahead, AUTO (market + pending OCO),
forecast straddle with OCO, backtest entry modes."""
import math
import time
import types

from harness import TMP, cycle, make_tm

import backtest
import strategies as ST
from engine import Bar

T0 = 1_790_000_000 - 1_790_000_000 % 900


def trend_bars(n=300, d=1, step=900):
    out, p = [], 2600.0
    for i in range(n):
        wave = 3.0 * math.sin(i / 6.0)
        c = p + d * i * 0.35 + wave
        o = c - d * 0.4
        out.append(Bar(T0 + i * step, o, max(o, c) + 0.6, min(o, c) - 0.6, c, 100))
    return out


def stair_bars(n=1200, d=1, step=900):
    """M15 trend with real structure: a slow wave (H4 swings) + a fast wave (M15 swings) on a drift."""
    t0 = T0 - T0 % 14400
    out, p = [], 2600.0
    for i in range(n):
        c = p + d * i * 0.12 + 9.0 * math.sin(2 * math.pi * i / 130.0) + 2.0 * math.sin(2 * math.pi * i / 22.0)
        o = c - d * 0.3
        out.append(Bar(t0 + i * step, o, max(o, c) + 0.6, min(o, c) - 0.6, c, 100))
    return out


def legacy_conso(cfg=None):
    """The v2.1 consolidation rule (volume + ADX + box, 4 quiet candles) as a CUSTOM setting."""
    c = dict(cfg or ST.load_settings()["conso"])
    c.update(quiet_bars=4, max_range=10.0, conditions=dict(c["conditions"], vol_low=True, adx_low=True, box=True, of_balanced=False),
             required=["vol_low", "adx_low", "box"])
    return c


def legacy():
    """The pre-v2.7.8 NORMAL rules (TREND without the not-extreme KONTRA, H4 BOS within 30; PULLBACK with MFI +
    doji, 1:1.5), for the tests written for them."""
    s = ST.load_settings()
    t = s["trend"]
    t["h4_window"] = 30
    t["required"] = [k for k in t["required"] if k != "not_extreme"]
    t["conditions"]["not_extreme"] = False
    p = s["pullback"]
    p["required"] = ["rsi", "mfi", "doji", "ob", "of_turn"]
    p["conditions"].update(mfi=True, doji=True)
    p["rr"] = 1.5
    return s


def box_bars(n=200, step=300):
    out = trend_bars(120, 1, step)
    base = out[-1].c
    for k in range(n):
        i = 120 + k
        c = base + 1.6 * math.sin(k / 2.2)                      # oscillates inside a ~3.5 box
        o = base + 1.6 * math.sin((k - 1) / 2.2)
        out.append(Bar(T0 + i * step, o, max(o, c) + 0.15, min(o, c) - 0.15, c, 100))
    return out


def test_settings_required_always_on_and_clean():
    s = ST.clean_settings({"schema": ST.SCHEMA, "trend": {"conditions": {"bos_h4": False, "macd": True}, "rr": "3", "bogus": 1},
                           "pullback": {"block_volatile": False, "os": 35}})
    assert s["trend"]["conditions"]["bos_h4"] is True and s["trend"]["conditions"]["macd"] is True
    assert s["trend"]["rr"] == 3.0 and "bogus" not in s["trend"]
    assert s["pullback"]["block_volatile"] is False and s["pullback"]["os"] == 35.0
    assert s["trend"]["auto"] and s["pullback"]["auto"] and not s["conso"]["auto"]   # v2.7.6: CONSO MANUAL


def test_no_lookahead_all_strategies():
    bars = backtest.synth_data(10, seed=8)["M15"]
    cfg = ST.load_settings()
    S = ST.series(bars)
    for i in range(200, len(bars) - 5, 97):
        S2 = ST.series(bars[:i + 1])
        for n in ST.NAMES:
            a = ST.evaluate(n, bars, S, i, cfg[n], 0)
            b = ST.evaluate(n, bars[:i + 1], S2, i, cfg[n], 0)
            assert (a["ok"], a["score"], a["met"]) == (b["ok"], b["score"], b["met"]), (n, i)


def test_trend_signals_follow_the_trend_with_own_sl_tp():
    cfg = legacy()
    for d in (1, -1):
        bars = stair_bars(1200, d)
        sig = ST.signals("trend", bars, cfg["trend"], None, 1100)
        assert sig and all(g["side"] == d for g in sig), (d, len(sig))
        g = sig[-1]
        risk = abs(g["entry"] - g["sl"])
        assert (g["entry"] - g["sl"]) * d > 0 and abs(abs(g["tp"] - g["entry"]) - 2 * risk) < 0.05


def test_pullback_blocked_when_volatile_and_can_be_off():
    bars = trend_bars(300, 1)
    cfg = ST.load_settings()["pullback"]
    S = ST.series(bars)
    assert cfg["block_volatile"] is False                         # v2.8.18 default: off (spikes)
    e = ST.evaluate("pullback", bars, S, 250, dict(cfg, vol_pct=0.0, block_volatile=True), 0)
    assert "volatile" in e["missing"] and not e["ok"] and "VOLATILE" in e["why"]
    e2 = ST.evaluate("pullback", bars, S, 250, dict(cfg, vol_pct=0.0, block_volatile=False), 0)
    assert "volatile" not in e2["missing"]


def test_low_volume_grid_plan():
    bars = box_bars()
    bars = [Bar(b.t, b.o, b.h, b.l, b.c, 300 if k < len(bars) - 25 else 40) for k, b in enumerate(bars)]   # volume dries up
    cfg = legacy_conso()
    S = ST.series(bars)
    e = ST._eval_conso(bars, S, len(bars) - 2, cfg)
    assert e["met"]["vol_low"] and e["met"]["adx_low"], e["met"]
    assert e["ok"], (e["missing"], e["score"], e["why"])
    g = e["grid"]
    assert len(g) == 2 * cfg["levels"] and {x["side"] for x in g} == {1, -1}
    buys = sorted(x["entry"] for x in g if x["side"] == 1)
    assert buys[0] > e["box_high"] and abs(buys[1] - buys[0] - cfg["step"]) < 0.02
    sells = sorted((x["entry"] for x in g if x["side"] == -1), reverse=True)
    assert sells[0] < e["box_low"] and abs(sells[0] - sells[1] - cfg["step"]) < 0.02
    for x in g:
        assert abs(abs(x["entry"] - x["sl"]) - e["sl_dist"]) < 0.02 and abs(abs(x["tp"] - x["entry"]) - e["tp_dist"]) < 0.02
    loud = [Bar(b.t, b.o, b.h, b.l, b.c, 300) for b in bars]                 # same prices, normal volume
    e2 = ST._eval_conso(loud, ST.series(loud), len(loud) - 2, cfg)
    assert not e2["met"]["vol_low"] and not e2["ok"]
    sig = ST.signals("conso", bars, cfg, None, 300)
    assert sig and sig[-1]["pending"] and sig[-1]["plan"]["grid"]


def test_trend_needs_fresh_alignment():
    bars = stair_bars(1200, 1)
    cfg = ST.load_settings()["trend"]
    sig = ST.signals("trend", bars, cfg, None, 1100)
    S = ST.series(bars)
    for g in sig:
        prev = ST.evaluate("trend", bars, S, g["i"] - 1, cfg, 0)
        assert not (prev["ok"] and prev["side"] == g["side"]), g["i"]          # it TURNED ok on that bar


def test_trend_is_2_m15_bos_plus_h4_bos():
    """TREND (Eugene): every signal has >= 2 M15 BOS in its direction (the last one too) and the latest H4 BOS
    in that direction; the real M15 / H4 bars give the same answer as resampling."""
    assert ST.load_settings()["trend"]["required"] == ["bos_m15", "bos_h4", "of_buyers", "not_extreme"]
    cfg = legacy()["trend"]
    assert cfg["required"] == ["bos_m15", "bos_h4", "of_buyers"] and cfg["bos_m15_min"] == 2
    bars = stair_bars(1200, 1)
    S = ST.series(bars)
    sig = ST.signals("trend", bars, cfg, None, 1100, S=S)
    assert sig
    for g in sig:
        m15 = ST.mtf_state(S, g["i"], "M15", cfg["bos_window"])
        h4 = ST.mtf_state(S, g["i"], "H4", cfg["h4_window"])
        assert m15["up"] >= 2 and m15["last"] == 1 and h4["last"] == 1, (m15, h4)
    real = {"M15": bars, "H4": ST._resample(bars, 14400)}
    assert [g["t"] for g in ST.signals("trend", bars, cfg, None, 1100, mtf=real)] == [g["t"] for g in sig]
    # an M5 chart: the BOS still comes from M15 / H4
    m5 = [Bar(b.t + k * 300, b.o, b.h, b.l, b.c, 30) for b in bars[-400:] for k in range(3)]
    e = ST.evaluate("trend", m5, ST.series(m5, real), len(m5) - 2, cfg, 0)
    assert e.get("h4_bos") == 1 and "m15_up" in e


def test_trend_no_h4_bos_no_trade():
    cfg = ST.load_settings()["trend"]
    bars = stair_bars(1200, 1)
    flat = {"M15": bars, "H4": [Bar(b.t, 2600, 2601, 2599, 2600, 1) for b in ST._resample(bars, 14400)]}
    assert ST.signals("trend", bars, cfg, None, 1100, mtf=flat) == []
    e = ST.evaluate("trend", bars, ST.series(bars, flat), 1100, cfg, 1)
    assert not e["ok"] and "bos_h4" in e["missing"]


def test_old_settings_get_the_new_trend_and_conso_rules():
    old = {"trend": {"conditions": {"ema_align": True, "macd": True}, "required": ["ema_align", "adx"], "rr": 2.5,
                     "auto": True},
           "conso": {"conditions": {"box": False, "squeeze": True}, "required": ["adx_low", "vol_low"], "levels": 3}}
    s = ST.clean_settings(old)
    assert s["trend"]["required"] == ["bos_m15", "bos_h4", "of_buyers", "not_extreme"] and not s["trend"]["conditions"]["macd"]
    assert s["trend"]["rr"] == 2.5 and s["trend"]["auto"] is True                    # your other values stay
    assert s["conso"]["required"] == ["adx_low", "of_balanced"] and not s["conso"]["conditions"]["box"]          # v2.4: ADX < 20 alone
    assert s["conso"]["levels"] == 3 and s["schema"] == ST.SCHEMA
    assert ST.clean_settings(s) == s                                              # migrates only once


def test_conso_counts_volume_adx_box_and_needs_the_box():
    bars = box_bars()
    bars = [Bar(b.t, b.o, b.h, b.l, b.c, 300 if k < len(bars) - 25 else 40) for k, b in enumerate(bars)]
    cfg = legacy_conso()
    S = ST.series(bars)
    e = ST._eval_conso(bars, S, len(bars) - 2, cfg)
    assert e["count_of"] == 3 and e["count"] == 3 and e["ok"] and e["range_src"] == "box"
    nobox = ST._eval_conso(bars, S, len(bars) - 2, cfg, box={"ok": False})
    assert nobox["count"] == 2 and not nobox["ok"] and "box" in nobox["missing"]


def test_one_chart_indicator_per_strategy():
    import importlib.util
    import os
    spec = importlib.util.spec_from_file_location("ss", os.path.join(os.path.dirname(ST.__file__), "plugins", "strategy_signals.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    names = {c.name for c in (mod.TrendStrategy, mod.PullbackStrategy, mod.ConsolidationStrategy)}
    assert names == {"Strategy · TREND", "Strategy · PULLBACK", "Strategy · CONSOLIDATION"}
    bars = stair_bars(1200, 1)
    ctx = types.SimpleNamespace(bars=lambda tf: bars if tf == "M15" else ST._resample(bars, 14400), history=None)
    ST.save_settings(legacy())
    try:
        ov = mod.TrendStrategy().compute(bars, "M15", ctx)
    finally:
        ST.save_settings(ST.default_settings())
    assert any(m.char.startswith("TR▲") for m in ov.markers) and any(m.char.startswith("H4") for m in ov.markers)
    b2 = [Bar(b.t, b.o, b.h, b.l, b.c, 300 if k < 295 else 40) for k, b in enumerate(box_bars())]
    ov2 = mod.ConsolidationStrategy().compute(b2, "M5", None)
    assert ov2.zones and "OCO" in ov2.zones[-1].label and "2/2" in ov2.zones[-1].label         # ADX < 20 + order flow (no ticks: not judged)


# ---------------------------------------------------------------- AUTO TRADE
def _auto(score=40.0, bias=1, strategies=("trend",)):
    from test_gate import setup
    hub, tm, g = setup(score=score, bias=bias)
    from autotrade import AutoTrader
    at = AutoTrader(tm)
    at.running = False
    at.enabled = True
    at.settings.update(sources=["analyser"], sessions=[], max_cautions=-1, news_block=False, max_open=1, min_score=0)
    at._session_ok = lambda: (True, "test")
    hub.autotrader = at
    cfg = legacy()
    for n in ST.NAMES:
        cfg[n]["auto"] = n in strategies
    ST.save_settings(cfg)
    return hub, tm, g, at


def test_auto_takes_strategy_trade_with_bias_even_if_analyser_score_low():
    hub, tm, g, at = _auto(score=40.0, bias=1)
    assert ("st_trend", None) in at._plugins()
    t = int(time.time()) // 300 * 300
    at._consider("st_trend", dict(side=1, entry=2650.0, sl=2646.0, tp=2658.0, score=75.0, cautions=[], t=t,
                                  strategy="trend", kind="TREND"))
    assert len(tm.groups) == 1, [x["text"] for x in at.log][:3]
    hub2, tm2, g2, at2 = _auto(score=90.0, bias=-1)                  # SELL bias, BUY strategy signal
    at2._consider("st_trend", dict(side=1, entry=2650.0, sl=2646.0, tp=2658.0, score=75.0, cautions=[], t=t,
                                   strategy="trend", kind="TREND"))
    assert not tm2.groups and "BLOCKED_STRUCTURE" in at2.log[0]["text"], at2.log[0]["text"]
    ST.save_settings(ST.default_settings())


def test_auto_conso_places_oco_stops():
    _conso_places(0)


def test_auto_conso_straddle_not_blocked_by_bearish_bias():
    _conso_places(-1)                       # Eugene 2026-10-01: READY 2/2, HTF Bearish, no pending orders appeared


def _conso_places(bias):
    import news_trader as NTM
    hub, tm, g, at = _auto(score=40.0, bias=bias, strategies=("conso",))
    hub.src.bars = lambda tf: []
    nt = NTM.NewsTrader(tm)
    nt.running = False
    hub.newstrader = nt
    plan = dict(box_high=2652.0, box_low=2648.0, buy_stop=2652.5, sell_stop=2647.5, sl_dist=5.0, tp_dist=10.0,
                buy_sl=2647.5, buy_tp=2662.5, sell_sl=2652.5, sell_tp=2637.5, box_from=0, levels=2, step=1.5)
    t = int(time.time()) // 300 * 300
    at._consider("st_conso", dict(side=0, pending=True, entry=2652.5, sl=2647.5, tp=2662.5, score=100.0,
                                  cautions=[], t=t, plan=plan, strategy="conso", kind="CONSO BOX"))
    orders = [o for o in tm.master.backend.call("orders", None) if o["comment"].startswith(NTM.TAG)]
    assert len(orders) == 4, [x["text"] for x in at.log][:3]
    prices = sorted(o["price"] for o in orders)
    assert [round(p, 2) for p in prices] == [2646.0, 2647.5, 2652.5, 2654.0], prices
    st = next(iter(nt.events.values()))
    assert st["kind"] == "strategy" and not st["expire_at"]          # v2.8.7: CONSO grid has no expiry
    tm.set_armed(False)
    nt._cycle()
    assert not [o for o in tm.master.backend.call("orders", None) if o["comment"].startswith(NTM.TAG)]
    ST.save_settings(ST.default_settings())


# ---------------------------------------------------------------- backtest + forecast
def test_backtest_strategy_modes():
    from test_backtest import prepared
    P = prepared("M15", 12)
    base = dict(backtest.DEFAULT_SETUP, sessions=[], max_cautions=-1)
    n_tr = {}
    for mode in ("st_trend", "st_pullback", "st_conso"):
        tr = backtest.simulate(P, dict(base, entry=mode))
        n_tr[mode] = len(tr)
        if mode == "st_conso":
            for t in tr:
                if "i" in t:
                    assert t["i"] in P.entry_over
    assert n_tr["st_trend"] > 0, n_tr


def test_forecast_conso_straddle_oco():
    import forecast as F
    from test_forecast import cfg, view
    eng = F.ForecastEngine(cfg(setups=["st_conso"]))
    bb = box_bars()
    bars = [Bar(b.t, b.o, b.h, b.l, b.c, 300 if k < len(bb) - 40 else 40) for k, b in enumerate(bb)]
    k = next(i for i in range(130, len(bars)) if ST._eval_conso(bars, ST.series(bars[:i + 1]), i, dict(ST.load_settings()["conso"], min_pct=0))["ok"])
    ST.save_settings(dict(ST.default_settings(), conso=dict(ST.default_settings()["conso"], min_pct=0)))
    v = view(1)
    v["_bars_fn"] = lambda tf: bars[:k + 1]
    v["t"] = bars[k].t + 300
    made = eng.create_all(v)
    legs = [f for f in eng.active.values() if f["setup_type"] == "st_conso"]
    assert len(legs) == 2 and {f["dir"] for f in legs} == {1, -1}, eng.last_why_not
    buy = next(f for f in legs if f["dir"] == 1)
    hit = Bar(bars[k].t + 300, buy["entry"] - 0.2, buy["entry"] + 0.3, buy["entry"] - 0.3, buy["entry"] + 0.1, 100)
    eng.on_bar(v, hit)
    left = [f for f in eng.active.values() if f["setup_type"] == "st_conso"]
    assert len(left) == 1 and left[0]["dir"] == 1 and left[0]["status"] in ("TRIGGERED", "RUNNING")
    assert made
    ST.save_settings(ST.default_settings())


def _dip_bars(doji=True):
    """Uptrend, then a sharp high-volume dip (RSI + MFI oversold), ending with a doji (or a full red bar)."""
    out = trend_bars(220, 1)
    p = out[-1].c
    t = out[-1].t
    for k in range(9):                                            # the dip: falling bars with heavy volume
        o = p
        p -= 2.2
        out.append(Bar(t + (k + 1) * 900, o, o + 0.2, p - 0.3, p, 400))
    t = out[-1].t + 900
    if doji:
        out.append(Bar(t, p, p + 1.3, p - 1.4, p + 0.05, 150))   # doji: tiny body, long wicks
    else:
        out.append(Bar(t, p, p + 0.1, p - 2.4, p - 2.2, 400))   # another full red bar
    return out


def test_pullback_uptrend_oversold_mfi_doji_buys():
    cfg = dict(legacy()["pullback"], block_volatile=False)
    assert {"rsi", "mfi", "doji"} <= set(cfg["required"])
    bars = _dip_bars(True)
    i = len(bars) - 1
    zone = dict(side=1, lo=bars[-1].l - 0.5, hi=bars[-1].l + 0.5, i=i - 30, t=bars[i - 30].t, created=i - 20, broken=None)
    S = dict(ST.series(bars), obs=[zone])                        # the dip ends on a bullish order block
    e = ST.evaluate("pullback", bars, S, i, cfg, 1)               # HTF bias BULL = uptrend
    assert e["met"]["rsi"] and e["met"]["mfi"] and e["met"]["doji"] and e["met"]["ob"], e["met"]
    assert e["ok"] and e["side"] == 1, (e["missing"], e["why"])
    no = _dip_bars(False)
    e2 = ST.evaluate("pullback", no, ST.series(no), len(no) - 1, cfg, 1)
    assert not e2["ok"] and "doji" in e2["missing"], e2["missing"]
    e3 = ST.evaluate("pullback", bars, S, i, dict(cfg, any_dir=False), -1)   # trend-only mode: no BUY vs bias
    assert not (e3["ok"] and e3["side"] == 1)
    e4 = ST.evaluate("pullback", bars, S, i, dict(cfg, any_dir=True), -1)    # v2.8.18: against the HTF -> BUY
    assert e4["ok"] and e4["side"] == 1, (e4["missing"], e4["why"])


def test_pullback_needs_an_order_block_or_a_spike():
    """v2.8.21: OB OR a sudden spike (Eugene: "khit walang OB, pero kung may OB mas ok")."""
    cfg = dict(ST.load_settings()["pullback"], block_volatile=False)
    assert "ob_spike" in cfg["required"] and cfg["conditions"]["ob_spike"] and not cfg["conditions"]["ob"]
    bars = _dip_bars(True)
    S = ST.series(bars)
    i = len(bars) - 1
    S2 = dict(S, obs=[])                                         # no order block anywhere
    big = ST.evaluate("pullback", bars, S2, i, dict(cfg, spike_atr=0.5), 1)
    assert big["met"]["spike"] and big["met"]["ob_spike"] and not big["met"]["ob"]      # the dip counts as a spike
    calm = ST.evaluate("pullback", bars, S2, i, dict(cfg, spike_atr=99.0), 1)
    assert not calm["met"]["ob_spike"] and "ob_spike" in calm["missing"] and not calm["ok"]   # no OB, no spike
    zone = dict(side=1, lo=bars[-1].l - 0.5, hi=bars[-1].l + 0.5, i=i - 30, t=bars[i - 30].t, created=i - 20, broken=None)
    S3 = dict(S, obs=[zone])                                     # an OB, even without a spike: OK
    e3 = ST.evaluate("pullback", bars, S3, i, dict(cfg, spike_atr=99.0), 1)
    assert e3["met"]["ob"] and e3["met"]["ob_spike"] and "ob_spike" not in e3["missing"], e3["missing"]


def test_conso_needs_quiet_candles_in_a_row():
    bars = box_bars()
    n = len(bars)
    cfg = legacy_conso()
    assert cfg["quiet_bars"] == 4 and cfg["adx_max"] == 20
    quiet = [Bar(b.t, b.o, b.h, b.l, b.c, 300 if k < n - 25 else 40) for k, b in enumerate(bars)]
    e = ST._eval_conso(quiet, ST.series(quiet), n - 2, cfg)
    assert e["ok"] and e["quiet_n"] >= 4, (e["missing"], e["quiet_n"])
    # the volume dried up only 2 candles ago: not yet (needs 4 quiet candles in a row)
    late = [Bar(b.t, b.o, b.h, b.l, b.c, 300 if k < n - 3 else 40) for k, b in enumerate(bars)]
    e2 = ST._eval_conso(late, ST.series(late), n - 2, cfg)
    assert not e2["met"]["vol_low"] and not e2["ok"] and e2["quiet_n"] < 4, (e2["met"], e2["quiet_n"])
    e3 = ST._eval_conso(quiet, ST.series(quiet), n - 2, dict(cfg, quiet_bars=1))
    assert e3["met"]["vol_low"]


def test_side_panel_news_row():
    import threading
    now = time.time()
    nf = types.SimpleNamespace(settings={"currencies": ["USD"], "warn_impacts": ["High"]}, lock=threading.Lock(),
                               calendar=[dict(title="old", currency="USD", impact="High", t=now - 3600),
                                         dict(title="EUR thing", currency="EUR", impact="High", t=now + 60),
                                         dict(title="NFP", currency="USD", impact="High", t=now + 1800)])
    nt = types.SimpleNamespace(enabled=False, settings={"place_before_sec": 120, "close_before_sec": 180,
                                                        "close_before_news": True}, plan=None)
    r = ST.news_status(types.SimpleNamespace(news=nf, newstrader=nt))
    assert r["next"]["title"] == "NFP" and r["enabled"] is False and r["place_before_sec"] == 120
    r2 = ST.news_status(types.SimpleNamespace(news=None, newstrader=None))
    assert r2["next"] is None and not r2["available"]


def test_side_panel_near_hit():
    """How close each strategy is: progress %, the price that completes it and the $ distance (read-only)."""
    cfg = ST.load_settings()
    bars = stair_bars(1200, 1)
    S = ST.series(bars)
    seen_level = False
    for i in range(700, 1150, 7):
        e = ST.evaluate("trend", bars, S, i, cfg["trend"], 0)
        nr = ST._near("trend", e, S, i, cfg["trend"], bars[i].c)
        assert 0 <= nr["progress"] <= 100
        if e["ok"]:
            assert nr["progress"] == 100
        elif nr["level"] is not None and e.get("h4_bos") == 1:
            seen_level = True
            assert nr["level"] == round(ST.mtf_state(S, i, "M15", cfg["trend"]["bos_window"])["swing_high"], 2)
            assert abs(nr["dist"] - (nr["level"] - bars[i].c)) < 0.01
    assert seen_level
    b2 = [Bar(b.t, b.o, b.h, b.l, b.c, 300 if k < 295 else 40) for k, b in enumerate(box_bars())]
    S2 = ST.series(b2)
    e = ST._eval_conso(b2, S2, len(b2) - 2, cfg["conso"])
    nr = ST._near("conso", e, S2, len(b2) - 2, cfg["conso"], b2[-2].c)
    assert nr["progress"] == 100 and nr["level"] in (e["buy_stop"], e["sell_stop"]) and nr["dist"] > 0


# ---------------------------------------------------------------- 100% + NORMAL / AGGRESSIVE / SCALP
def test_every_checked_condition_must_pass():
    s = ST.clean_settings({"schema": ST.SCHEMA, "pullback": {"min_pct": 40, "conditions": {"stoch": True}}})
    assert all(s[n]["min_pct"] == 100.0 for n in ST.NAMES)
    bars = _dip_bars(True)
    i = len(bars) - 1
    zone = dict(side=1, lo=bars[-1].l - 0.5, hi=bars[-1].l + 0.5, i=i - 30, t=bars[i - 30].t, created=i - 20, broken=None)
    S = dict(ST.series(bars), obs=[zone])
    base = dict(ST.load_settings()["pullback"], block_volatile=False)
    assert ST.evaluate("pullback", bars, S, i, base, 1)["ok"]                 # the 4 conditions: all met
    cfg = dict(base, min_pct=s["pullback"]["min_pct"], conditions=dict(base["conditions"], near_ema=True))
    e = ST.evaluate("pullback", bars, S, i, cfg, 1)
    assert not e["met"]["near_ema"] and not e["ok"]          # one extra checked condition missing = no entry


def test_profiles_change_settings_but_never_switch_auto_on():
    cur = ST.default_settings()
    cur["pullback"]["auto"] = cur["conso"]["auto"] = False
    ag = ST.apply_profile(cur, "aggressive")
    assert ag["profile"] == "aggressive" and ag["htf_filter"] is False
    assert ag["trend"]["auto"] is True and ag["pullback"]["auto"] is False and ag["conso"]["auto"] is False
    assert ag["trend"]["bos_m15_min"] == 1 and ag["pullback"]["required"] == ["rsi", "mfi", "ob", "of_turn"]
    assert not ag["pullback"]["conditions"]["doji"] and ag["conso"]["quiet_bars"] == 1
    sc = ST.apply_profile(ag, "scalp")
    assert (sc["trend"]["bos_ltf"], sc["trend"]["bos_htf"], sc["trend"]["rr"]) == ("M5", "H1", 1.0)
    assert sc["conso"]["levels"] == 1 and ST.PROFILES["scalp"]["auto_tf"] == "M5"
    nm = ST.apply_profile(sc, "normal")
    assert (nm["trend"]["bos_ltf"], nm["trend"]["bos_htf"], nm["trend"]["bos_m15_min"]) == ("M15", "H4", 2)
    assert nm["pullback"]["required"] == ["rsi", "ob", "of_turn"] and nm["htf_filter"] is True     # v2.7.8
    assert ST.clean_settings(nm) == nm
    bad = ST.clean_settings({"schema": ST.SCHEMA, "trend": {"bos_ltf": "H4", "bos_htf": "M5"}, "profile": "zzz"})
    assert ST.MTF_TFS[bad["trend"]["bos_htf"]] > ST.MTF_TFS[bad["trend"]["bos_ltf"]] and bad["profile"] == "normal"


def test_scalp_trend_uses_m5_and_h1_structure():
    cfg = ST.apply_profile(ST.default_settings(), "scalp")["trend"]
    m5 = stair_bars(1200, 1, step=300)                                # the same structure, 3x faster
    S = ST.series(m5)
    sig = ST.signals("trend", m5, cfg, None, 1100, S=S)
    assert sig and all(g["side"] == 1 for g in sig)
    for g in sig:
        assert ST.mtf_state(S, g["i"], "M5", cfg["bos_window"])["up"] >= 2
        assert ST.mtf_state(S, g["i"], "H1", cfg["h4_window"])["last"] == 1
    assert abs(abs(sig[-1]["tp"] - sig[-1]["entry"]) - abs(sig[-1]["entry"] - sig[-1]["sl"])) < 0.05   # 1:1


def test_schema2_pullback_gets_only_the_4_conditions():
    s = ST.clean_settings({"schema": 2, "pullback": {"conditions": {"stoch": True, "turn": True}, "rr": 2.5}})
    assert not s["pullback"]["conditions"]["stoch"] and not s["pullback"]["conditions"]["turn"]
    assert [k for k, v in s["pullback"]["conditions"].items() if v] == ["rsi", "ob_spike", "of_turn"]   # + v2.8.21
    assert s["pullback"]["rr"] == 2.5


def test_htf_filter_off_follows_the_strategy_own_trend():
    t = int(time.time()) // 300 * 300
    sig = dict(side=1, entry=2650.0, sl=2646.0, tp=2658.0, score=100.0, cautions=[], t=t, strategy="trend", kind="TREND")
    hub, tm, g, at = _auto(score=90.0, bias=-1)                      # analyser says SELL, H4 BOS strategy says BUY
    at._consider("st_trend", dict(sig))
    assert not tm.groups and "BLOCKED_STRUCTURE" in at.log[0]["text"]  # NORMAL: the HTF bias must agree
    cfg = ST.load_settings()
    cfg["htf_filter"] = False
    ST.save_settings(cfg)
    hub2, tm2, g2, at2 = _auto(score=90.0, bias=-1)
    c2 = ST.load_settings()
    c2["htf_filter"] = False
    ST.save_settings(c2)
    at2._consider("st_trend", dict(sig))
    assert len(tm2.groups) == 1, [x["text"] for x in at2.log][:3]
    ST.save_settings(ST.default_settings())


def test_status_tells_if_ready_is_a_new_signal():
    """The side panel: a READY that has been on for bars was already signalled (AUTO enters only on the bar where
    it turns READY); /api/strategies says which bar that was."""
    bars = stair_bars(1200, 1)
    hub = types.SimpleNamespace(src=types.SimpleNamespace(bars=lambda tf: bars), read=None, autotrader=None,
                                trader=None, news=None, newstrader=None)
    st = ST.status(hub, "M15")
    t = st["strategies"]["trend"]
    sig = ST.signals("trend", bars, ST.load_settings()["trend"], None, 60)
    if sig:
        assert t["last_signal"]["t"] == sig[-1]["t"]
        assert t["last_signal"]["fresh"] == (sig[-1]["i"] == len(bars) - 2)
    else:
        assert t["last_signal"] is None
    assert st["auto"]["trading"] is False and "price" in st



# ---------------------------------------------------------------- v2.4: ADX < 20 alone, all green -> trade
def test_adx_below_20_alone_places_the_pending_orders():
    cfg = ST.load_settings()["conso"]
    assert cfg["required"] == ["adx_low", "of_balanced"] and cfg["quiet_bars"] == 1
    assert [k for k, v in cfg["conditions"].items() if v] == ["adx_low", "of_balanced"]
    loud = box_bars()                                            # normal volume everywhere: volume does not matter now
    S = ST.series(loud)
    i = next(k for k in range(len(loud) - 2, 80, -1) if S["adx"][k] is not None and S["adx"][k] < 20)
    e = ST._eval_conso(loud, S, i, cfg)
    assert e["ok"] and e["met"]["adx_low"] and not e["met"]["vol_low"], (e["missing"], e["why"])
    assert e["buy_stop"] > e["box_high"] and e["sell_stop"] < e["box_low"] and e["count"] == e["count_of"] == 2


def test_auto_takes_a_strategy_that_is_still_all_green_once():
    """'pag lahat ng na-set sa strategy nag green, trade na agad': a READY that was already green when AUTO came on
    is taken (at the current bar's prices), once per READY spell."""
    hub, tm, g, at = _auto(score=40.0, bias=1)
    assert at.settings.get("strategies_only", True) and [s for s, _ in at._plugins()] == ["st_trend"]
    bars = stair_bars(1200, 1)
    cfg = ST.load_settings()["trend"]                            # = legacy() saved by _auto
    S = ST.series(bars)
    i = next(k for k in range(len(bars) - 2, 300, -1)
             if ST.evaluate("trend", bars, S, k, cfg, 0)["ok"] and ST.evaluate("trend", bars, S, k - 1, cfg, 0)["ok"])
    cut = bars[:i + 2]                                           # the last CLOSED bar (i) is all green
    hub.src.bars = lambda tf: cut
    hub.read_at = lambda t: types.SimpleNamespace(bias=1, overall=40.0, passing=70.0)
    seen = []
    at._consider = lambda src, sg: seen.append((src, sg))
    at._mark_existing()                                          # switching AUTO on does not hide it
    at._strategies(cut, at.settings["tf"])
    at._strategies(cut, at.settings["tf"])                       # same spell: only once
    assert len(seen) == 1 and seen[0][0] == "st_trend", seen
    e = ST.evaluate("trend", cut, ST.series(cut), len(cut) - 2, cfg, 1)
    assert abs(seen[0][1]["entry"] - e["entry"]) < 0.01            # current prices, not the spell's first bar
    ST.save_settings(ST.default_settings())


def test_pullback_sell_needs_the_m15_order_block_above_to_hold():
    """Eugene: downtrend, price rallies UP, overbought -> there must be a bearish order block ABOVE that the rally
    tags, and it must NOT be broken on M15 (no M15 close above it). Then SELL."""
    t0 = T0 - T0 % 14400
    m5 = []
    p = 2700.0
    for k in range(300):                                          # M5 chart: drift down, then a rally into ~2690
        p += -0.15 if k < 260 else 0.35
        m5.append(Bar(t0 + k * 300, p, p + 0.4, p - 0.4, p, 100))
    cfg = dict(ST.load_settings()["pullback"], ob_tf="M15")
    S = ST.series(m5)
    i = len(m5) - 2
    top = max(b.h for b in m5[i - 5:i + 1])
    zone = dict(side=-1, lo=top - 0.3, hi=top + 2.0, i=10, t=m5[0].t, created=20, broken=None)
    S["_ob_cache"] = {"M15": [zone]}                              # a bearish M15 OB just above the rally high
    assert ST.mtf_state(S, i, "M15", 30) is not None and S["sec"] == 300
    o = ST.ob_hold(S, m5, i, -1, cfg)
    assert o is zone                                              # tagged and holding -> OB condition OK
    m15_idx = __import__("bisect").bisect_right(S["mtf"]["M15"]["closes"], S["t_close"][i]) - 1
    S["_ob_cache"] = {"M15": [dict(zone, broken=m15_idx)]}         # an M15 close went above it -> broken
    assert ST.ob_hold(S, m5, i, -1, cfg) is None
    S["_ob_cache"] = {"M15": [dict(zone, lo=top + 5, hi=top + 7)]}  # too far above: the rally never reached it
    assert ST.ob_hold(S, m5, i, -1, cfg) is None
    S["_ob_cache"] = {"M15": [dict(zone, side=1)]}                 # a bullish OB is not supply for a SELL
    assert ST.ob_hold(S, m5, i, -1, cfg) is None
    S["_ob_cache"] = {"M15": [zone]}
    e = ST.evaluate("pullback", m5, S, i, dict(cfg, block_volatile=False), -1)
    assert e["met"]["ob"] and e["side"] == -1 and e["sl"] > zone["hi"]   # SL beyond the order block


def test_strategy_panel_extra_indicators_and_schema5():
    """Strategy panel: the new indicators can be added to a setup (and are part of the 100% rule)."""
    bars = stair_bars(1200, 1)
    S = ST.series(bars)
    cfg = ST.load_settings()
    for n, keys in (("trend", ("rsi_side", "ema200")), ("pullback", ("bb_touch", "engulf")),
                    ("conso", ("rsi_mid", "inside_bb"))):
        assert all(k in cfg[n]["conditions"] and not cfg[n]["conditions"][k] for k in keys)
        c = dict(cfg[n], conditions=dict(cfg[n]["conditions"], **{k: True for k in keys}),
                 required=list(cfg[n]["required"]) + list(keys))
        c2 = ST.clean_settings({"schema": ST.SCHEMA, n: c})[n]
        assert all(c2["conditions"][k] for k in keys) and set(keys) <= set(c2["required"])
        e = ST._eval_conso(bars, S, 1100, c2) if n == "conso" else ST.evaluate(n, bars, S, 1100, c2, 1)
        if e.get("met"):
            assert all(k in e["met"] for k in keys)
            assert not e["ok"] or all(e["met"][k] for k in keys)
    # a schema-4 file with the old aggressive consolidation numbers (ADX < 25, 3 quiet candles) is reset
    old = ST.clean_settings({"schema": 4, "conso": {"adx_max": 25, "quiet_bars": 3, "rr": 1.5}})
    assert old["conso"]["adx_max"] == 20 and old["conso"]["quiet_bars"] == 1 and old["conso"]["rr"] == 1.5


def test_trend_kontra_ob_in_the_way_and_overbought():
    """Eugene: BUY trend, but overbought or just under an order block -> no BUY; once an M15 close breaks the OB,
    the BUY may go. Both are optional (＋ idagdag) so they never hold back the default setup."""
    cfg = ST.load_settings()["trend"]
    assert not cfg["conditions"]["no_ob_block"] and cfg["conditions"]["not_extreme"]   # v2.7.8: not_extreme ON
    t0 = T0 - T0 % 14400
    m5, p = [], 2700.0
    for k in range(300):
        p += 0.2
        m5.append(Bar(t0 + k * 300, p, p + 0.4, p - 0.4, p, 100))
    S = ST.series(m5)
    i = len(m5) - 2
    c = m5[i].c
    zone = dict(side=-1, lo=c + 0.3, hi=c + 2.0, i=10, t=m5[0].t, created=20, broken=None)
    S["_ob_cache"] = {"M15": [zone]}
    kc = dict(cfg, ob_tf="M15")
    assert ST.ob_block(S, m5, i, 1, kc) is zone                     # supply right above: no BUY
    assert ST.ob_block(S, m5, i, -1, kc) is None                    # it does not block a SELL
    idx = __import__("bisect").bisect_right(S["mtf"]["M15"]["closes"], S["t_close"][i]) - 1
    S["_ob_cache"] = {"M15": [dict(zone, broken=idx)]}               # an M15 close went through it
    assert ST.ob_block(S, m5, i, 1, kc) is None
    S["_ob_cache"] = {"M15": [dict(zone, lo=c + 50, hi=c + 52)]}     # far away: not in the way
    assert ST.ob_block(S, m5, i, 1, kc) is None
    on = dict(kc, conditions=dict(cfg["conditions"], bos_m15=False, bos_h4=False, not_extreme=True, no_ob_block=False),
              required=["not_extreme"])
    e = ST.evaluate("trend", m5, S, i, on, 1)
    assert e["met"]["not_extreme"] == (S["rsi"][i] < 70) and e["ok"] == e["met"]["not_extreme"]


def test_each_profile_protects_profit_before_its_own_tp():
    """BE and every profit lock must trigger BEFORE the profile's TP (scalp TP is 1R: protect at 0.6R / 0.8R)."""
    for name, p in ST.PROFILES.items():
        rr = min(p[n]["rr"] for n in ("trend", "pullback", "conso"))
        pr = p["protect"]
        assert 0 < pr["be_r"] < rr, name
        for at, lock in pr["profit_lock"]:
            assert pr["be_r"] < at < rr + 1e-9 or at < max(p[n]["rr"] for n in ("trend", "pullback", "conso")), name
            assert 0 < lock < at, name
    assert ST.PROFILES["scalp"]["protect"]["be_r"] < 1.0


def test_skipped_strategy_spell_is_retried_and_straddle_ignores_htf():
    """A READY spell blocked by something temporary (session) is tried again while it stays green; once it became
    a trade it is a duplicate. A CONSOLIDATION straddle is not blocked by the HTF bias / structure conflict."""
    hub, tm, g, at = _auto(score=40.0, bias=-1, strategies=("trend",))
    t = int(time.time()) // 300 * 300
    sig = dict(side=1, entry=2650.0, sl=2646.0, tp=2658.0, score=100.0, cautions=[], t=t, strategy="trend", kind="TREND")
    at._session_ok = lambda: (False, "outside London")
    at._consider("st_trend", dict(sig))
    at._consider("st_trend", dict(sig))                           # same reason: logged once
    assert not tm.groups and sum("outside London" in x["text"] for x in at.log) == 1
    at._session_ok = lambda: (True, "test")
    cfg = ST.load_settings()
    cfg["htf_filter"] = False
    ST.save_settings(cfg)
    at._consider("st_trend", dict(sig))                           # session open now: the same spell trades
    assert len(tm.groups) == 1, [x["text"] for x in at.log][:3]
    at._consider("st_trend", dict(sig))
    assert len(tm.groups) == 1 and "DUPLICATE" in at.log[0]["text"]
    ST.save_settings(ST.default_settings())


def test_order_flow_conditions_use_buyers_vs_sellers():
    """ORDER FLOW: buyers stronger -> of_buyers for a BUY trend, of_turn for a BUY pullback, balanced for conso.
    No tick data (backtest) -> simply not met."""
    import orderflow
    bars = stair_bars(400, 1)
    fake = types.SimpleNamespace(bar_stats=lambda tf: [(b.t, 70.0, 30.0) + (0,) * 7 for b in bars])
    old = orderflow.CURRENT
    try:
        orderflow.CURRENT = fake
        S = ST.series(bars)
        assert ST.of_pressure(S, 300, 3)[0] == 40.0
        cfg = ST.load_settings()
        t = ST._checks("trend", S, 300, 1, cfg["trend"], bars, 1)
        assert t["of_buyers"] and not ST._checks("trend", S, 300, -1, cfg["trend"], bars, -1)["of_buyers"]
        assert ST._checks("pullback", S, 300, 1, cfg["pullback"], bars, 1)["of_turn"]
        assert not ST._eval_conso(bars, S, 300, cfg["conso"])["met"]["of_balanced"]
        orderflow.CURRENT = None
        S2 = ST.series(bars)
        assert S2["of"] is None and ST._checks("trend", S2, 300, 1, cfg["trend"], bars, 1)["of_buyers"]   # no ticks: not judged
    finally:
        orderflow.CURRENT = old


def test_buyers_vs_sellers_chart_markers_on_side_change():
    import importlib.util
    import os
    import orderflow
    spec = importlib.util.spec_from_file_location("fs", os.path.join(os.path.dirname(ST.__file__), "plugins", "flow_strength.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    bars = stair_bars(60, 1, step=300)
    flow = [(70.0, 30.0)] * 20 + [(50.0, 50.0)] * 10 + [(25.0, 75.0)] * 30   # buyers, balance, sellers
    old = orderflow.CURRENT
    try:
        orderflow.CURRENT = types.SimpleNamespace(bar_stats=lambda tf: [(b.t, f[0], f[1]) for b, f in zip(bars, flow)])
        ov = mod.FlowStrength().compute(bars, "M5", None)
        assert [m.char for m in ov.markers] == ["BUYERS ▲", "SELLERS ▼"], [m.char for m in ov.markers]
        orderflow.CURRENT = None
        assert not mod.FlowStrength().compute(bars, "M5", None).markers
    finally:
        orderflow.CURRENT = old


def test_order_flow_is_on_in_every_strategy_and_old_settings_get_it():
    cfg = ST.load_settings()
    for n, k in ST.OF_KEY.items():
        assert cfg[n]["conditions"][k] and k in cfg[n]["required"]
        for p in ST.PROFILES:
            if p == "master":
                continue                    # MASTER = the MT5 Master EA rules: CVD (TREND) instead of live order flow
            assert k in ST.apply_profile(cfg, p)[n]["required"]
    old = ST.clean_settings({"schema": 5, "trend": {"required": ["bos_m15", "bos_h4"], "rr": 2.5},
                             "pullback": {"required": ["rsi", "mfi", "ob"]}})
    assert old["trend"]["required"][-1] == "of_buyers" and old["trend"]["rr"] == 2.5
    assert old["pullback"]["required"] == ["rsi", "ob_spike", "of_turn"]           # v2.7.8 refine + v2.8.21 OB or spike
    assert ST.clean_settings(old) == old


def test_everything_on_by_default_and_old_files_get_auto():
    import autotrade as A
    assert A.DEFAULTS["auto_on_start"] and A.DEFAULTS["news_on_start"]
    s = ST.clean_settings({"schema": 6, "trend": {"auto": False}, "conso": {"auto": False, "rr": 1.7}})
    assert s["trend"]["auto"] and s["pullback"]["auto"] and s["conso"]["rr"] == 1.7
    assert s["conso"]["auto"] is True                                      # v2.8.3: CONSO always AUTO (once)
    s4 = ST.clean_settings(dict(s, conso=dict(s["conso"], auto=False)))
    assert s4["conso"]["auto"] is False                                    # switched to MANUAL later: kept
    s3 = ST.clean_settings(dict(s, conso=dict(s["conso"], auto=True)))
    assert s3["conso"]["auto"] is True                                     # you switch it back to AUTO: kept
    s2 = dict(s, trend=dict(s["trend"], auto=False))
    assert ST.clean_settings(s2)["trend"]["auto"] is False                 # switched to MANUAL later: kept


def test_v276_sessions_get_asia_and_frankfurt_once():
    import json, os
    import autotrade as A
    with open(A.SETTINGS_FILE, "w") as f:
        json.dump({"settings_version": 2, "sessions": ["London", "New York"]}, f)
    try:
        assert A.load_settings()["sessions"] == ["London", "New York", "Asia", "Frankfurt"]
        with open(A.SETTINGS_FILE, "w") as f:
            json.dump({"settings_version": 3, "sessions": ["London"]}, f)
        assert A.load_settings()["sessions"] == ["London"]                # your later choice stays
    finally:
        os.remove(A.SETTINGS_FILE)


def test_v277_trend_sl_at_last_swing():
    from engine import Bar
    # an up-leg with a clear swing low at bar 50 (price 95), then higher; entry at the last bar
    bars = []
    for k in range(80):
        base = 100 + 0.2 * k
        lo = 95.0 if k == 50 else base - 1
        bars.append(Bar(k * 900, base, base + 1, lo, base + 0.5, 100))
    sw = ST.last_swing(bars, 79, 1)
    assert sw is not None and sw <= bars[79].c
    assert ST.last_swing(bars, 79, 1, look=5) is None or ST.last_swing(bars, 79, 1, look=5) < bars[79].c
    cfg = ST.default_settings()
    assert cfg["trend"]["sl_mode"] == "swing" and cfg["trend"]["rr"] == 2.0
    assert ST.apply_profile(cfg, "aggressive")["trend"]["sl_mode"] == "atr"
    assert ST.apply_profile(cfg, "normal")["trend"]["sl_mode"] == "swing"
    old = ST.clean_settings({"schema": 8, "profile": "normal", "trend": {"sl_mode": "atr", "rr": 2.5}})
    assert old["trend"]["sl_mode"] == "swing" and old["trend"]["rr"] == 2.5          # once; your RR stays
    later = ST.clean_settings(dict(old, trend=dict(old["trend"], sl_mode="atr")))
    assert later["trend"]["sl_mode"] == "atr"                                        # switched back later: kept
    agg = ST.clean_settings({"schema": 8, "profile": "aggressive", "trend": {"sl_mode": "atr"}})
    assert agg["trend"]["sl_mode"] == "atr"


def test_v277_trend_swing_sl_bounds():
    import random
    from engine import Bar
    rnd = random.Random(7)
    bars, p = [], 2000.0
    for k in range(600):
        p += rnd.gauss(0.05, 1.2)
        bars.append(Bar(k * 900, p, p + abs(rnd.gauss(0, 1)), p - abs(rnd.gauss(0, 1)), p + rnd.gauss(0, 0.5), 50))
    S = ST.series(bars)
    cfg = ST.default_settings()["trend"]
    cfg = dict(cfg, conditions={k: False for k in cfg["conditions"]}, required=[])
    n_checked = 0
    for i in range(100, 599):
        e = ST.evaluate("trend", bars, S, i, cfg, bias=1)
        if not e.get("side") or e.get("sl") is None:
            continue
        a = S["atr"][i]
        dist = abs(e["entry"] - e["sl"])
        assert dist <= 4.0 * a + 0.02 and dist >= 0.5 * a - 0.02, (i, dist, a)
        n_checked += 1
    assert n_checked > 10


def test_v278_refine_defaults_and_migration():
    d = ST.default_settings()
    assert d["trend"]["h4_window"] == 12 and "not_extreme" in d["trend"]["required"]
    assert d["pullback"]["required"] == ["rsi", "ob_spike", "of_turn"] and d["pullback"]["rr"] == 2.0
    assert not d["pullback"]["conditions"]["mfi"] and not d["pullback"]["conditions"]["doji"]
    old = ST.clean_settings({"schema": 9, "profile": "normal",
                             "trend": {"h4_window": 30, "required": ["bos_m15", "bos_h4", "of_buyers"]},
                             "pullback": {"rr": 1.5, "required": ["rsi", "mfi", "doji", "ob", "of_turn"],
                                          "conditions": {"mfi": True, "doji": True}}})
    assert old["trend"]["h4_window"] == 12 and "not_extreme" in old["trend"]["required"]
    assert old["pullback"]["required"] == ["rsi", "ob_spike", "of_turn"] and old["pullback"]["rr"] == 2.0
    again = ST.clean_settings(dict(old, pullback=dict(old["pullback"], required=old["pullback"]["required"] + ["mfi"])))
    assert "mfi" in again["pullback"]["required"]                                  # your later choice stays
    agg = ST.clean_settings({"schema": 9, "profile": "aggressive", "trend": {"h4_window": 30}})
    assert agg["trend"]["h4_window"] == 30                                         # other profiles untouched


def test_v283_conso_auto_once_for_schema_11():
    s = ST.clean_settings({"schema": 11, "profile": "custom", "conso": {"auto": False}})
    assert s["conso"]["auto"] is True and s["schema"] == ST.SCHEMA


def test_v286_conso_new_grid_after_the_old_one_is_done():
    """READY box: grid 1 placed -> while it is live no 2nd grid -> grid expired -> a NEW grid (OCO) for the same box."""
    import news_trader as NTM
    import strategies as _st
    hub, tm, g, at = _auto(score=40.0, bias=1, strategies=("conso",))
    nt = NTM.NewsTrader(tm)
    nt.running = False
    hub.newstrader = nt
    plan = dict(ok=True, box_high=2652.0, box_low=2648.0, buy_stop=2652.5, sell_stop=2647.5, sl_dist=5.0, tp_dist=10.0,
                buy_sl=2647.5, buy_tp=2662.5, sell_sl=2652.5, sell_tp=2637.5, box_from=0, levels=1, step=1.5, side=0)
    t = int(time.time()) // 900 * 900
    spell = dict(side=0, pending=True, entry=2652.5, sl=2647.5, tp=2662.5, score=100.0, cautions=[], t=t, i=200,
                 strategy="conso", kind="CONSO BOX")
    at._signals = lambda *a, **k: [dict(spell)]
    old_eval, old_series = _st.evaluate, _st.series
    _st.evaluate = lambda *a, **k: dict(plan)
    _st.series = lambda *a, **k: None
    bars = [type("B", (), dict(t=t - (300 - k) * 900))() for k in range(300)]
    try:
        def tags():
            return [o for o in tm.master.backend.call("orders", None) if o["comment"].startswith(NTM.TAG)]
        at._strategies(bars, "M15")
        assert len(tags()) == 2, [x["text"] for x in at.log][:4]                   # grid 1
        at._st_tries = {}
        at._strategies(bars, "M15")
        assert len(tags()) == 2, [x["text"] for x in at.log][:4]                   # still live: not again
        assert any("naka-place na ang grid" in x["text"] for x in at.log)
        for st in nt.events.values():                                              # grid 1 expires
            st["expire_at"] = time.time() - 1
        tm.refresh(); nt._cycle()
        assert not tags()
        at._st_tries = {}
        at._strategies(bars, "M15")
        assert len(tags()) == 2, [x["text"] for x in at.log][:4]                   # grid 2, same READY box
        assert any("round 2" in x["text"] for x in at.log)
    finally:
        _st.evaluate, _st.series = old_eval, old_series
        ST.save_settings(ST.default_settings())


def test_v2811_conso_ignores_the_session_filter_trend_does_not():
    import autotrade as AT
    hub, tm, g, at = _auto(score=40.0, bias=0, strategies=("conso",))
    at.settings["sessions"] = []                                   # no session allowed at all
    at._session_ok = lambda: (False, "outside London (UTC 23h)")
    _conso_like = __import__("test_strategies")._conso_places
    import news_trader as NTM
    nt = NTM.NewsTrader(tm); nt.running = False; hub.newstrader = nt
    plan = dict(box_high=2652.0, box_low=2648.0, buy_stop=2652.5, sell_stop=2647.5, sl_dist=5.0, tp_dist=10.0,
                buy_sl=2647.5, buy_tp=2662.5, sell_sl=2652.5, sell_tp=2637.5, box_from=0, levels=1, step=1.5)
    t = int(time.time()) // 300 * 300
    at._consider("st_conso", dict(side=0, pending=True, entry=2652.5, sl=2647.5, tp=2662.5, score=100.0,
                                  cautions=[], t=t, plan=plan, strategy="conso", kind="CONSO BOX"))
    assert len([o for o in tm.master.backend.call("orders", None) if o["comment"].startswith(NTM.TAG)]) == 2, \
        [x["text"] for x in at.log][:3]
    at._consider("st_trend", dict(side=1, entry=2650.0, sl=2645.0, tp=2660.0, score=100.0, cautions=[], t=t,
                                  strategy="trend", kind="TREND"))
    assert any("BLOCKED_SESSION" in x["text"] for x in at.log)


def test_v2812_conso_uses_its_own_5pct_risk_cap():
    import risk_engine as RE
    hub, tm, g, at = _auto(score=40.0, bias=0, strategies=("conso",))
    risk = hub.risk
    tm.master.info["equity"] = 164.57
    cs, _ = risk.contract_size()
    lot = 0.01
    sl = 8.0 / (lot * cs)
    codes = lambda bl: [b["code"] for b in bl]
    assert "RISK_PER_TRADE" in codes(risk.hard_blockers(1, "auto", lot, sl, sl * 2, risk_pct_cap=2.0))
    assert "RISK_PER_TRADE" not in codes(risk.hard_blockers(1, "auto", lot, sl, sl * 2, risk_pct_cap=5.0))
    assert "RISK_PER_TRADE" in codes(risk.hard_blockers(1, "auto", lot, sl * 2, sl * 4, risk_pct_cap=5.0))   # 9.7 % > 5
    assert ST.load_settings()["conso"]["max_risk_pct"] == 0.0                                          # v2.8.14: off
    assert "RISK_PER_TRADE" not in codes(risk.hard_blockers(1, "auto", lot, sl * 3, sl * 6, risk_pct_cap=0.0))
    assert ST.clean_settings({"schema": 12, "conso": {"max_risk_pct": 5.0}})["conso"]["max_risk_pct"] == 0.0


def test_v2816_trend_has_its_own_5pct_risk_cap():
    assert ST.load_settings()["trend"]["max_risk_pct"] == 0.0           # v2.8.23: off ($20 SL cap instead)
    hub, tm, g, at = _auto(score=40.0, bias=0, strategies=("trend",))
    risk = hub.risk
    tm.master.info["equity"] = 1060.0
    cs, _ = risk.contract_size()
    sl = 28.6 / (0.01 * cs)                                          # $28.6 at 0.01 lot
    codes = lambda bl: [b["code"] for b in bl]
    assert "RISK_PER_TRADE" not in codes(risk.hard_blockers(-1, "auto", 0.01, sl, sl * 2, risk_pct_cap=5.0))   # 2.7 %
    tm.master.info["equity"] = 153.19
    assert "RISK_PER_TRADE" in codes(risk.hard_blockers(-1, "auto", 0.01, sl, sl * 2, risk_pct_cap=5.0))       # 18.7 %


def test_v2817_trend_sl_is_capped_at_5pct_of_master_equity():
    hub, tm, g, at = _auto(score=40.0, bias=0, strategies=("trend",))
    c = ST.load_settings(); c["trend"]["max_risk_pct"] = 5.0; ST.save_settings(c)      # the 5 % mode, switched on
    tm.master.info["equity"] = 153.19
    cs = hub.risk.contract_size()[0]
    at.settings["lot"] = 0.01
    sg = dict(side=-1, entry=4121.0, sl=4121.0 + 28.6, tp=4121.0 - 57.2, t=0)
    out = at._cap_trend_sl(sg)
    max_d = int(5 / 100 * 153.19 / (0.01 * cs) * 100) / 100
    assert abs((out["sl"] - out["entry"]) - max_d) < 0.011, out
    assert abs((out["entry"] - out["tp"]) - 2 * max_d) < 0.03, out                 # RR kept 1:2
    small = dict(sg, sl=4121.0 + 0.05 * 153.19 / (0.01 * cs) / 2)
    assert at._cap_trend_sl(small) == small                                     # already inside 5 %: untouched



def test_v2818_pullback_settings_migrated_once():
    s = ST.clean_settings({"schema": 13, "pullback": {"any_dir": False, "block_volatile": True}})
    assert s["pullback"]["any_dir"] is True and s["pullback"]["block_volatile"] is False
    s2 = ST.clean_settings(dict(s, pullback=dict(s["pullback"], block_volatile=True)))
    assert s2["pullback"]["block_volatile"] is True                # switched back on later: kept


def test_v2820_new_conso_box_replaces_the_old_grid_without_ping_pong():
    import news_trader as NTM
    import strategies as _st
    hub, tm, g, at = _auto(score=40.0, bias=0, strategies=("conso",))
    nt = NTM.NewsTrader(tm); nt.running = False; hub.newstrader = nt
    mid = (hub.last_tick.bid + hub.last_tick.ask) / 2
    plan = lambda c: dict(ok=True, box_high=c + 2.0, box_low=c - 2.0, buy_stop=c + 2.5, sell_stop=c - 2.5, sl_dist=5.0,
                          tp_dist=10.0, buy_sl=c - 2.5, buy_tp=c + 12.5, sell_sl=c + 2.5, sell_tp=c - 12.5, box_from=0,
                          levels=1, step=1.5, side=0)
    t1 = int(time.time()) // 900 * 900 - 3600
    t2 = t1 + 1800
    mk = lambda t: dict(side=0, pending=True, entry=mid + 2.5, sl=mid - 2.5, tp=mid + 12.5, score=100.0, cautions=[],
                        t=t, i=200, strategy="conso", kind="CONSO BOX")
    old_eval, old_series = _st.evaluate, _st.series
    _st.series = lambda *a, **k: None
    bars = [type("B", (), dict(t=t2 - (300 - k) * 900))() for k in range(300)]
    tags = lambda: [o for o in tm.master.backend.call("orders", None) if o["comment"].startswith(NTM.TAG)]
    try:
        _st.evaluate = lambda *a, **k: plan(mid)
        at._signals = lambda *a, **k: [mk(t1)]
        at._strategies(bars, "M15")
        first = {o["ticket"] for o in tags()}
        assert len(first) == 2
        _st.evaluate = lambda *a, **k: plan(mid + 0.3)               # a NEW box (newer spell)
        at._signals = lambda *a, **k: [mk(t1), mk(t2)]
        at._st_tries = {}
        at._strategies(bars, "M15")
        now = {o["ticket"] for o in tags()}
        assert len(now) == 2 and not (now & first), [x["text"] for x in at.log][:5]   # old grid replaced
        at._st_tries = {}
        at._strategies(bars, "M15")                                  # the old box must not come back
        assert {o["ticket"] for o in tags()} == now, [x["text"] for x in at.log][:5]
        assert any("bagong box" in x["text"] for x in at.log)
    finally:
        _st.evaluate, _st.series = old_eval, old_series
        ST.save_settings(ST.default_settings())



def test_v2821_migration_ob_to_ob_spike_once():
    s = ST.clean_settings({"schema": 14, "pullback": {"conditions": {"ob": True}, "required": ["rsi", "ob", "of_turn"]}})
    assert s["pullback"]["conditions"]["ob_spike"] and not s["pullback"]["conditions"]["ob"]
    assert s["pullback"]["required"] == ["rsi", "ob_spike", "of_turn"]
    m = ST.apply_profile(ST.default_settings(), "master")["pullback"]
    assert m["conditions"]["ob_spike"] and not m["conditions"]["ob"] and "ob_spike" in m["required"]



def test_v2823_sl_capped_at_20_dollars_and_no_pct_block():
    hub, tm, g, at = _auto(score=40.0, bias=0, strategies=("trend", "pullback"))
    tm.master.info["equity"] = 130.0
    sg = dict(side=-1, entry=4160.0, sl=4160.0 + 28.6, tp=4160.0 - 57.2, t=1)
    out = at._cap_sl(sg, "trend")
    assert abs(out["sl"] - out["entry"] - 20.0) < 0.01 and abs(out["entry"] - out["tp"] - 40.0) < 0.02
    assert at._cap_sl(dict(sg, sl=4160.0 + 12.0), "pullback")["sl"] == 4172.0          # inside $20: untouched
    s = ST.clean_settings({"schema": 15, "trend": {"max_risk_pct": 5.0}})
    assert s["trend"]["max_risk_pct"] == 0.0 and s["trend"]["max_sl"] == 20.0 and s["pullback"]["max_sl"] == 20.0
    cs = hub.risk.contract_size()[0]
    bl = hub.risk.hard_blockers(-1, "auto", 0.01, 20.0 / (0.01 * cs) * 0.01 * cs / (0.01 * cs) if False else 20.0,
                                40.0, risk_pct_cap=0.0)
    assert "RISK_PER_TRADE" not in [b["code"] for b in bl]


def test_v2823_sl_capped_at_20_dollars_and_no_pct_block():
    d = ST.default_settings()
    for n in ("trend", "pullback"):
        assert d[n]["max_sl"] == 20.0 and d[n]["max_risk_pct"] == 0.0
    old = ST.clean_settings({"schema": 15, "trend": {"max_risk_pct": 5.0}, "pullback": {"max_sl": 50.0}})
    assert old["trend"]["max_risk_pct"] == 0.0 and old["pullback"]["max_sl"] == 20.0
    cfg = ST.load_settings()
    cfg["trend"].update(max_risk_pct=0.0, max_sl=20.0)
    cfg["pullback"].update(max_risk_pct=0.0, max_sl=20.0)
    ST.save_settings(cfg)
    hub, tm, g, at = _auto(score=40.0, bias=0, strategies=("trend",))
    tm.master.info["equity"] = 130.0
    for name in ("trend", "pullback"):
        sg = dict(side=-1, entry=4160.0, sl=4160.0 + 28.6, tp=4160.0 - 57.2, t=1)
        out = at._cap_sl(sg, name)
        assert abs(out["sl"] - out["entry"] - 20.0) < 0.011 and abs(out["entry"] - out["tp"] - 40.0) < 0.03, out
        small = dict(sg, sl=4160.0 + 12.0)
        assert at._cap_sl(small, name) == small                      # inside $20: the strategy's own stop
    ST.save_settings(ST.default_settings())
