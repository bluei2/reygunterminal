"""v2.8.0 MASTER: the MT5 Master EA behaviour in the terminal - each strategy its own trade, reversal close,
early exit, re-entry after a win, secure partial, CVD + M5 structure in TREND, one-time migration."""
import json
import os
import time

from harness import TMP, cycle

import strategies as ST
from engine import Bar
from test_strategies import _auto, stair_bars


def _sg(side, t, entry=2650.0, sl_d=4.0):
    return dict(side=side, entry=entry, sl=entry - side * sl_d, tp=entry + side * 2 * sl_d, score=100.0, cautions=[],
                t=t, strategy="x", kind="X")


def _restore():
    """Every test leaves the default strategy settings behind (the next test modules expect NORMAL)."""
    ST.save_settings(ST.default_settings())


def _master_auto(**kw):
    hub, tm, g, at = _auto(**kw)
    at.settings.update(master_mode=True, no_opposite=False, max_open=3, max_per_day=0, reversal=True,
                       rev_close=["trend", "pullback", "conso"], trend_early_exit="m15_2bos",
                       align_mult=1.0)              # the x2-on-alignment lot is tested on its own
    return hub, tm, g, at


# ---------------------------------------------------------------- strategies: MASTER profile, CVD, M5
def test_master_profile_mirrors_the_ea():
    m = ST.master_settings(ST.clean_settings(None))
    assert m["profile"] == "normal" and m["htf_filter"] is True       # v2.8.49: NORMAL = the Oct 7 setup
    assert m["trend"]["required"] == ["bos_m15", "bos_h4", "of_buyers", "not_extreme", "mmi_cmi"]
    assert m["trend"]["sl_mode"] == "swing" and m["trend"]["rr"] == 5.0 and m["trend"]["h4_window"] == 12
    assert m["pullback"]["os"] == 40.0 and m["pullback"]["ob"] == 60.0 and m["pullback"]["required"] == ["rsi", "ob_spike", "of_turn", "mmi_cmi"]
    assert m["conso"]["required"] == ["adx_low", "of_balanced", "mmi_cmi"] and m["conso"]["levels"] == 1 and m["conso"]["max_sl"] == 20.0   # v2.8.41: same as TREND / PULLBACK
    assert all(m[n]["auto"] for n in ST.NAMES)                  # CONSO on AUTO too
    assert ST.PROFILES["master"]["autotrade"]["max_open"] == 3


def test_migrate_to_master_only_from_normal():
    p = os.path.join(TMP, "mig_strat.json")
    ST.save_settings(ST.clean_settings({"schema": 10, "profile": "normal", "conso": {"auto": False}}), p)
    assert ST.migrate_to_master(p)
    s = ST.load_settings(p)
    assert s["profile"] == "normal" and s["conso"]["auto"] and "of_buyers" in s["trend"]["required"]
    ST.save_settings(ST.apply_profile(ST.clean_settings(None), "aggressive"), p)
    assert not ST.migrate_to_master(p) and ST.load_settings(p)["profile"] == "aggressive"


def _m5_from(m15, bull=True):
    """3 M5 bars per M15 bar; bull=True: every M5 bar closes up (buyers), False: down."""
    out = []
    for b in m15:
        for k in range(3):
            o = b.o + (b.c - b.o) * k / 3
            c = b.o + (b.c - b.o) * (k + 1) / 3
            if not bull:
                o, c = max(o, c), min(o, c)
            else:
                o, c = min(o, c), max(o, c)
            out.append(Bar(b.t + k * 300, o, max(o, c) + 0.1, min(o, c) - 0.1, c, 50))
    return out
    _restore()


def test_cvd_condition_uses_m5_delta_without_look_ahead():
    m15 = stair_bars(400, 1)
    up = ST.series(m15, {"M5": _m5_from(m15, True)})
    dn = ST.series(m15, {"M5": _m5_from(m15, False)})
    i = 300
    assert up["cvd"]["m5"] and ST.cvd_change(up, i, 8) > 0 and ST.cvd_change(dn, i, 8) < 0
    # only M5 bars CLOSED by bar i's close: the next M15 bar's M5 bars never count
    m5 = _m5_from(m15, True)
    bumped = [Bar(b.t, b.o, b.h, b.l, b.c, 10 ** 6 if b.t >= m15[i + 1].t else b.v) for b in m5]
    assert abs(ST.cvd_change(ST.series(m15, {"M5": bumped}), i, 8) - ST.cvd_change(up, i, 8)) < 1e-6
    cfg = ST.master_settings()["trend"]
    assert ST._checks("trend", up, i, 1, cfg, m15, 0)["cvd"] is True
    assert ST._checks("trend", dn, i, 1, cfg, m15, 0)["cvd"] is False
    assert ST._checks("trend", dn, i, -1, cfg, m15, 0)["cvd"] is True
    # turn2 = only the last 2 bars
    assert ST._checks("trend", up, i, 1, dict(cfg, cvd_mode="turn2"), m15, 0)["cvd"] is True


def test_m5_structure_condition():
    m15 = stair_bars(400, 1)
    S = ST.series(m15, {"M5": ST._resample(stair_bars(1200, 1, step=300), 300)})
    i = 350
    st = ST.mtf_state(S, i, "M5", 32)
    assert st is not None
    met = ST._checks("trend", S, i, st["last"] or 1, ST.master_settings()["trend"], m15, 0)
    assert met["m5_struct"] is True
    met2 = ST._checks("trend", S, i, -(st["last"] or 1), ST.master_settings()["trend"], m15, 0)
    assert met2["m5_struct"] is False
    # no M5 bars (backtest on M15 only): not judged
    assert ST._checks("trend", ST.series(m15), i, 1, ST.master_settings()["trend"], m15, 0)["m5_struct"] is True


# ---------------------------------------------------------------- AUTO TRADE: each strategy its own trade
def test_each_strategy_its_own_trade_even_opposite():
    hub, tm, g, at = _master_auto(score=90.0, bias=1, strategies=("trend", "pullback"))
    ST.save_settings(dict(ST.load_settings(), htf_filter=False))
    t = int(time.time()) // 300 * 300
    at._consider("st_trend", _sg(1, t))
    assert len(tm.groups) == 1, [x["text"] for x in at.log][:3]
    gt = next(iter(tm.groups.values()))
    assert gt.source == "st_trend"
    at._consider("st_trend", _sg(1, t + 300))                    # TREND again: its own trade is open
    assert len(tm.groups) == 1 and "sariling trade" in at.log[0]["text"], at.log[0]["text"]
    at._consider("st_pullback", _sg(-1, t + 600))                # PULLBACK SELL while TREND BUY is open: allowed
    assert len(tm.groups) == 2, [x["text"] for x in at.log][:3]
    assert {x.source for x in tm.groups.values()} == {"st_trend", "st_pullback"}
    e = [x for x in tm.journal.entries.values() if x.get("strategy") == "st_pullback"]
    assert e and e[0]["side"] == -1
    _restore()


def test_reversal_closes_older_trades_against_the_new_trend():
    hub, tm, g, at = _master_auto(score=90.0, bias=1, strategies=("trend", "pullback"))
    ST.save_settings(dict(ST.load_settings(), htf_filter=False))
    t = int(time.time()) // 300 * 300
    at._consider("st_pullback", _sg(-1, t))
    assert len(tm.groups) == 1
    gid = next(iter(tm.groups))
    tm.groups[gid].created -= 20
    at._reversal(1, ("st_trend", t + 300))                       # fresh TREND BUY
    cycle(tm, 2)
    assert gid not in tm.groups, hub.notes[-3:]
    assert any("REVERSAL TREND UP" in x["text"] for x in at.log)
    n = len(at.log)
    at._reversal(1, ("st_trend", t + 300))                       # the same signal: once
    assert len(at.log) == n
    at.settings["reversal"] = False
    at._consider("st_pullback", _sg(-1, t + 900))
    gid2 = [k for k, v in tm.groups.items() if v.source == "st_pullback"][0]
    at._reversal(1, ("st_trend", t + 1200))
    assert gid2 in tm.groups                                     # reversal OFF: kept
    _restore()


def test_reversal_cancels_conso_stops_on_the_old_side():
    import types
    hub, tm, g, at = _master_auto()
    calls = []
    hub.newstrader = types.SimpleNamespace(cancel_strategy_side=lambda side, why="": calls.append(side) or 2,
                                           strategy_box_live=lambda: False)
    at._reversal(-1, ("st_trend", 1))
    assert calls == [], "v2.8.19: the CONSO pair is kept on a reversal by default"
    at.settings["rev_cancel_conso"] = True                       # the old behaviour, when switched on
    at._reversal(-1, ("st_trend", 2))
    assert calls == [1] and "CONSO BUY STOP cancelled" in at.log[0]["text"]
    _restore()


def test_early_exit_on_the_second_bos_against_the_trend_trade():
    hub, tm, g, at = _master_auto(score=90.0, bias=-1)
    ST.save_settings(dict(ST.load_settings(), htf_filter=False))
    t = int(time.time()) // 300 * 300
    at._consider("st_trend", _sg(-1, t))                         # a TREND SELL
    assert len(tm.groups) == 1, [x["text"] for x in at.log][:3]
    gid = next(iter(tm.groups))
    tm.groups[gid].created -= 20
    bars = stair_bars(500, 1)                                    # an uptrend: BOS UP again and again
    bos = ST._bos(bars[:-1])
    k = max(j for j, v in enumerate(bos) if v == 1)
    at._early_exit(bars[:k + 2])                                 # the bar that just closed has a BOS UP
    cycle(tm, 2)
    assert gid not in tm.groups and any("EARLY EXIT TREND SELL" in x["text"] for x in at.log)
    _restore()


def test_early_exit_off_and_not_for_other_strategies():
    hub, tm, g, at = _master_auto(score=90.0, bias=-1, strategies=("trend", "pullback"))
    ST.save_settings(dict(ST.load_settings(), htf_filter=False))
    t = int(time.time()) // 300 * 300
    at._consider("st_pullback", _sg(-1, t))
    gid = next(iter(tm.groups))
    bars = stair_bars(500, 1)
    bos = ST._bos(bars[:-1])
    k = max(j for j, v in enumerate(bos) if v == 1)
    at._early_exit(bars[:k + 2])
    assert gid in tm.groups                                      # PULLBACK has no early exit
    at._consider("st_trend", _sg(-1, t + 300))
    at.settings["trend_early_exit"] = "off"
    at._early_exit(bars[:k + 2])
    assert len(tm.groups) == 2
    _restore()


def test_reentry_after_a_win_extends_the_h4_window():
    hub, tm, g, at = _master_auto()
    S, i = {}, 100
    cfg = ST.master_settings()["trend"]
    real = ST.mtf_state
    ST.mtf_state = lambda S_, i_, tf, w: dict(last=1, age=20, up=1, dn=0)   # the H4 UP break is 20 H4 bars old
    try:
        _reentry_checks(at, tm, cfg, S, i)
    finally:
        ST.mtf_state = real


def _reentry_checks(at, tm, cfg, S, i):
    assert at._trend_cfg(cfg, S, i) is cfg                       # no win yet: normal rule
    tm.journal.entries["w1"] = dict(gid="w1", strategy="st_trend", status="closed", pnl=12.5, side=1, close_time=time.time())
    c2 = at._trend_cfg(cfg, S, i)
    assert c2["h4_window"] == 30 and c2.get("reentry")
    tm.journal.entries["l1"] = dict(gid="l1", strategy="st_trend", status="closed", pnl=-5.0, side=1,
                                    close_time=time.time() + 5)
    assert at._trend_cfg(cfg, S, i) is cfg                       # the LAST trend trade lost: no re-entry
    at.settings["trend_reentry_win"] = 0
    tm.journal.entries.pop("l1")
    assert at._trend_cfg(cfg, S, i) is cfg                       # off


# ---------------------------------------------------------------- partial close guard + secure
    _restore()


def test_partial_close_never_closes_a_minimum_lot():
    hub, tm, g, at = _master_auto(score=90.0, bias=1)
    ST.save_settings(dict(ST.load_settings(), htf_filter=False))
    t = int(time.time()) // 300 * 300
    at._consider("st_trend", _sg(1, t))
    gid = next(iter(tm.groups))
    tm.close_group(gid, 0.5, source="test")
    cycle(tm)
    assert gid in tm.groups and tm.master.pos(tm.groups[gid].legs["master"])["volume"] == 0.01
    assert any("skipped" in n[1] for n in hub.notes)
    _restore()


def test_secure_partial_for_trend_and_conso_left_alone():
    import position_manager as PM
    hub, tm, g, at = _master_auto(score=90.0, bias=1)
    ST.save_settings(dict(ST.load_settings(), htf_filter=False))
    at.settings["lot"] = 0.04
    t = int(time.time()) // 300 * 300
    at._consider("st_trend", _sg(1, t, sl_d=4.0))
    gid = next(iter(tm.groups))
    grp = tm.groups[gid]
    grp.state = "PROTECTED"
    grp.protected = {k: True for k in grp.legs}
    mp = tm.master.pos(grp.legs["master"])
    pm = PM.PositionController.__new__(PM.PositionController)
    pm.tm, pm.hub, pm.settings, pm.status, pm.log = tm, hub, PM.clean(None), {}, []
    hub.set_price(mp["open"] + 4.5)                              # +1.1R
    cycle(tm)
    pm.step()
    cycle(tm)
    assert tm.master.pos(grp.legs["master"])["volume"] == 0.02, tm.master.positions
    # a CONSO trade: no break-even / lock / partial from the position manager (Master EA: CSProtect off)
    at.settings["conso_protect"] = False                         # CONSO protection OFF: left alone
    grp.source = "st_conso"
    sl0 = tm.master.pos(grp.legs["master"])["sl"]
    hub.set_price(mp["open"] + 7.0)                              # +1.75R: lock 1.5R -> 0.5R would move it
    cycle(tm)
    pm.step()
    assert tm.master.pos(grp.legs["master"])["sl"] == sl0


# ---------------------------------------------------------------- migration
    _restore()


def test_v4_autotrade_migration_to_master():
    import autotrade as A
    p = A.SETTINGS_FILE
    with open(p, "w") as f:
        json.dump(dict(settings_version=3, tf="M5", max_open=1, max_per_day=3, no_opposite=True, lot=0.02), f)
    ST.save_settings(ST.clean_settings({"schema": 10, "profile": "normal"}))
    from autotrade import AutoTrader
    hub, tm, g, _a = _auto()
    at = AutoTrader(tm)
    at.running = False
    s = at.settings
    assert ST.load_settings()["profile"] == "normal"
    assert s["tf"] == "M15" and s["max_open"] == 3 and s["max_per_day"] == 0 and s["no_opposite"] is False
    assert s["master_mode"] and s["lot"] == 0.02                         # your lot is kept
    with open(p) as f:
        assert json.load(f)["settings_version"] == A.SETTINGS_VERSION
    os.remove(p)
    _restore()


def test_master_account_daily_limits_once():
    import trading
    with open(trading.CONFIG_FILE, "w") as f:
        json.dump(dict(master=dict(name="M"), followers=[], risk_version=2), f)
    c = trading.load_config()
    assert c["master"]["daily_dd"] == 400.0 and c["master"]["dd_mode"] == "pips"     # v2.8.26: 400 pips
    assert c["master"]["daily_tp"] == 60.0 and c["master"]["tp_mode"] == "$"
    with open(trading.CONFIG_FILE, "w") as f:                     # your own limits: kept
        json.dump(dict(master=dict(name="M", daily_dd=5.0, dd_mode="%"), followers=[], risk_version=2), f)
    c = trading.load_config()
    assert c["master"]["daily_dd"] == 5.0 and c["master"]["daily_tp"] == 0.0
    trading.save_config(c)
    assert trading.load_config()["master"]["daily_dd"] == 5.0           # saved once: never again
    os.remove(trading.CONFIG_FILE)


# ---------------------------------------------------------------- delta profile (chart)
def test_delta_profile_splits_buy_and_sell():
    import importlib.util as u
    spec = u.spec_from_file_location("dp", os.path.join(os.path.dirname(ST.__file__), "plugins", "delta_profile.py"))
    m = u.module_from_spec(spec)
    spec.loader.exec_module(m)
    up = [Bar(1000 + i * 900, 2600 + i * 0.1, 2601 + i * 0.1, 2599.5 + i * 0.1, 2600.8 + i * 0.1, 100) for i in range(300)]
    ov = m.DeltaProfile().compute(up, "M15", None)
    pf = ov.profiles[0]
    assert len(pf.buy) == len(pf.rows) and all(0 <= b <= r[2] + 1e-9 for b, r in zip(pf.buy, pf.rows))
    assert abs(sum(r[2] for r in pf.rows) - 200 * 100) < 1e-6          # the last `bars` bars, all their volume
    assert "BUYERS mas agresibo" in pf.title and pf.highlight and ov.levels[0].label == "ΔPOC"
    dn = [Bar(b.t, b.c, b.h, b.l, b.o, b.v) for b in up]                 # same bars, every candle down
    assert "SELLERS mas agresibo" in m.DeltaProfile().compute(dn, "M15", None).profiles[0].title
    # finer bars from the context when they cover the window (M1 below M15)
    fine = [Bar(up[-200].t + k * 60, 2600, 2601, 2599, 2599.2, 7) for k in range(3000)]
    ctx = type("C", (), {"bars": lambda self, tf: fine if tf == "M1" else []})()
    assert "SELLERS" in m.DeltaProfile().compute(up, "M15", ctx).profiles[0].title


def test_v2815_fresh_pullback_closes_the_trades_against_it():
    hub, tm, g, at = _master_auto(score=90.0, bias=1, strategies=("trend", "pullback"))
    ST.save_settings(dict(ST.load_settings(), htf_filter=False))
    t = int(time.time()) // 300 * 300
    at._consider("st_trend", _sg(1, t))                          # an open TREND BUY
    gid = next(iter(tm.groups))
    tm.groups[gid].created -= 20
    at._reversal(-1, ("st_pullback", t + 300), who="PULLBACK")  # fresh PULLBACK SELL
    cycle(tm, 2)
    assert gid not in tm.groups, hub.notes[-3:]
    assert any("REVERSAL PULLBACK DOWN" in x["text"] for x in at.log)
    assert at.settings["pullback_reversal"] is True
    _restore()


def _run_strategy(at, name, spell, bars=None):
    """Drive _strategies with one READY spell of `name` (the strategy maths stubbed)."""
    import strategies as _st
    t = spell["t"]
    bars = bars or [type("B", (), dict(t=t - (300 - k) * 900))() for k in range(301)]
    spell = dict(spell, i=len(bars) - 2, strategy=name)
    at._signals = lambda *a, **k: [dict(spell)] if a and a[0] == name or k.get("name") == name else [dict(spell)]
    old = _st.evaluate, _st.series
    _st.evaluate = lambda n, *a, **k: dict(ok=n == name, side=spell["side"], entry=spell["entry"], sl=spell["sl"],
                                          tp=spell["tp"], score=100.0) if n == name else dict(ok=False, side=0)
    _st.series = lambda *a, **k: None
    at._trend_cfg = lambda c, S, i: c
    at._st_tries = {}
    try:
        at._strategies(bars, "M15")
    finally:
        _st.evaluate, _st.series = old


def test_v2820_reversal_closes_only_when_the_new_trade_enters():
    hub, tm, g, at = _master_auto(score=90.0, bias=1, strategies=("trend",))
    ST.save_settings(dict(ST.load_settings(), htf_filter=False))
    t = int(time.time()) // 900 * 900
    at._consider("st_pullback", _sg(1, t - 3600))                 # an open BUY (another strategy)
    buy = next(iter(tm.groups))
    tm.groups[buy].created -= 20
    sell = _sg(-1, t)
    at._session_ok = lambda: (False, "outside the sessions (test)")
    _run_strategy(at, "trend", sell)                               # TREND SELL is READY but BLOCKED
    assert buy in tm.groups, "a blocked signal must not close anything"
    assert not any("REVERSAL" in x["text"] for x in at.log)
    at._session_ok = lambda: (True, "")
    _run_strategy(at, "trend", dict(sell, t=t + 900))             # now it ENTERS
    cycle(tm, 2)
    assert buy not in tm.groups, [x["text"] for x in at.log][:4]
    assert any(getattr(x, "side", 0) == -1 for x in tm.groups.values())
    assert any("REVERSAL TREND DOWN" in x["text"] for x in at.log)
    _restore()


def test_v2822_lot_x2_when_h4_h1_m15_align():
    import types
    from test_gate import setup
    from autotrade import AutoTrader
    from reconciliation import Reconciler
    hub, tm, g = setup(score=90.0, bias=-1, n_followers=1)
    at = AutoTrader(tm)
    at.running, at.enabled = False, True
    at.settings.update(sources=["analyser"], sessions=[], max_cautions=-1, news_block=False, max_open=3, min_score=0,
                       master_mode=True, no_opposite=False, lot=0.01)
    at._session_ok = lambda: (True, "test")
    hub.autotrader = at
    cfg = ST.master_settings(ST.clean_settings(None))
    cfg["trend"]["auto"] = True
    ST.save_settings(cfg)
    tm.followers[0].cfg["lot"] = 0.02
    for tf in ("H4", "H1", "M15"):
        hub.states[tf].trend = -1
    t = int(time.time()) // 300 * 300
    at._consider("st_trend", _sg(-1, t))                         # SELL, all three bearish -> x2
    assert tm.groups, [x["text"] for x in at.log][:3]
    gid = next(iter(tm.groups))
    grp = tm.groups[gid]
    cycle(tm, 1)
    assert tm.master.pos(grp.legs["master"])["volume"] == 0.02
    assert tm.followers[0].pos(grp.legs["f1"])["volume"] == 0.04
    assert tm.journal.entries[gid]["lot_mult"] == 2.0
    grp.created -= 30
    rc = Reconciler(hub)
    rc.check()
    cycle(tm, 1)
    assert tm.followers[0].pos(grp.legs["f1"])["volume"] == 0.04         # no "volume drift" repair
    hub.states["H4"].trend = 1                                     # H4 against: base lot
    assert at._align_mult(-1) == 1.0 and at._align_mult(1) == 1.0
    _restore()


def test_v2822_trend_5pct_cap_also_applies_when_the_order_is_sent():
    """The gate allowed TREND up to 5 %, but tm.open re-checked at the risk settings' 2 % -> blocked. Same cap now."""
    hub, tm, g, at = _master_auto(score=90.0, bias=1, strategies=("trend",))
    ST.save_settings(dict(ST.load_settings(), htf_filter=False))
    hub.risk.settings = dict(hub.risk.settings, max_risk_per_trade_pct=2.0) if isinstance(getattr(hub.risk, "settings", None), dict) else hub.risk.settings
    cs = hub.risk.contract_size()[0]
    eq = float(tm.master.info.get("equity") or 0)
    sl_d = round(0.04 * eq / (0.01 * cs), 2)                     # 4 % of equity at 0.01: > 2 %, < 5 %
    ok2, why2 = tm.check_request(1, 0.01, sl_d, sl_d * 2, "auto")
    ok5, why5 = tm.check_request(1, 0.01, sl_d, sl_d * 2, "auto", risk_pct_cap=5.0)
    assert ok5, why5
    _restore()


def test_v2827_tl_break_counter_trend_entry_and_retest_reentry():
    import types
    hub, tm, g, at = _master_auto(score=90.0, bias=1, strategies=("trend",))   # HTF BULL
    ST.save_settings(dict(ST.load_settings(), htf_filter=False))
    tm.master.info["equity"] = 130.0
    t = int(time.time()) // 900 * 900
    brk = dict(side=-1, entry=4150.0, sl=4170.0, tp=4110.0, score=60.0, cautions=[], t=t, kind="TL break")
    at._consider("tl_break", brk)                                   # SELL against the BULL bias, $20 SL on $130
    assert at._last_res == "trade", [x["text"] for x in at.log][:3]
    rt = dict(brk, t=t + 900, entry=4152.0, sl=4170.0, tp=4116.0)
    at._consider("tl_retest", rt)                                   # re-entry on the retest: its own trade
    assert at._last_res == "trade", [x["text"] for x in at.log][:3]
    assert sorted(getattr(x, "source", "") for x in tm.groups.values()) == ["tl_break", "tl_retest"]
    at._consider("tl_break", dict(brk, t=t + 1800))                 # a 2nd break while the 1st is open: one each
    assert at._last_res != "trade"
    # the plugin is asked with the TL BREAK params (break / retest, max SL $20, 1:2)
    seen = []
    class P:
        params = dict(entry="break", max_sl=10.0)
        def signals(self, bars, off):
            seen.append(dict(self.params))
            return []
    fake = types.SimpleNamespace(obj=P(), error=None)
    hub.ctx = lambda: None
    at._signals("tl_retest", fake, [], "M15")
    assert seen[-1]["entry"] == "retest" and seen[-1]["max_sl"] == 20.0 and seen[-1]["rr"] == 5.0
    assert fake.obj.params == dict(entry="break", max_sl=10.0)   # the chart's own settings untouched
    _restore()



def test_v2837_normal_is_the_sc_setup_and_master_is_its_alias():
    n = ST.apply_profile(ST.default_settings(), "normal")
    m = ST.apply_profile(ST.default_settings(), "master")
    assert n == m and n["profile"] == "normal"
    assert n["trend"]["rr"] == 5.0 and n["pullback"]["required"] == ["rsi", "ob_spike", "of_turn", "mmi_cmi"] and n["htf_filter"] is True
    assert ST.PROFILES["normal"]["protect"]["be_r"] == 0.5 and ST.PROFILES["normal"]["autotrade"]["conso_protect"]
    assert ST.clean_settings({"schema": ST.SCHEMA, "profile": "master"})["profile"] == "normal"
    assert set(ST.PROFILE_BUTTONS) == {"scalp", "normal", "aggressive"}


def test_v2838_aggressive_lot_x2_not_stacked_with_align():
    hub, tm, g, at = _master_auto(score=90.0, bias=1, strategies=("trend",))
    ST.save_settings(dict(ST.load_settings(), htf_filter=False))
    at.settings.update(lot=0.01, mode_mult=2.0, align_mult=2.0)
    t = int(time.time()) // 300 * 300
    at._consider("st_trend", _sg(1, t))                      # aligned AND aggressive: still x2, not x4
    gid = next(iter(tm.groups))
    cycle(tm, 1)
    assert tm.master.pos(tm.groups[gid].legs["master"])["volume"] == 0.02
    _restore()
