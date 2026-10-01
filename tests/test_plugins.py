"""Plugin safety contract: isolation, timeout, malformed output, forbidden imports, no order access, chart ownership."""
import os
import tempfile
import time

import harness  # noqa: F401

import plugin_loader
import superchart
from plugin_api import Context, Indicator, Overlay, Vote
from plugin_loader import LoadedPlugin, contract_violations
from sources import DemoSource

_HUB = None


def hub():
    global _HUB
    if _HUB is None:
        _HUB = superchart.Hub(DemoSource("XAUUSD"))
        for _ in range(4):
            _HUB.step()
        _HUB.analyze()
    for p in list(_HUB.plugins):
        if p.key.startswith("test_"):
            _HUB.plugins.remove(p)
    return _HUB


def add(h, cls, key):
    lp = LoadedPlugin(cls(), key, "indicator")
    lp.scoring = True
    lp.enabled = True
    h.plugins.append(lp)
    return lp


class Good(Indicator):
    name, category = "Good", "Trend"

    def vote(self, ctx):
        return Vote(1, "up")

    def compute(self, bars, tf, ctx):
        ov = Overlay()
        from plugin_api import Level
        ov.levels.append(Level(bars[-1].c, "#fff", "good"))
        return ov


class Crash(Indicator):
    name, category = "Crash", "Trend"

    def vote(self, ctx):
        raise ValueError("boom")


class Malformed(Indicator):
    name, category = "Malformed", "Momentum"

    def vote(self, ctx):
        return Vote(5, "five")


class Slow(Indicator):
    name, category = "Slow", "Momentum"

    def vote(self, ctx):
        time.sleep(0.02)
        return Vote(1, "slow")


class BadCompute(Indicator):
    name, category = "BadCompute", "Trend"

    def compute(self, bars, tf, ctx):
        return {"lines": []}


def test_failed_plugin_isolated_and_not_neutral():
    h = hub()
    g = add(h, Good, "test_good.Good")
    c = add(h, Crash, "test_crash.Crash")
    votes = h.collect_votes()
    names = [v[0] for v in votes]
    assert "Good" in names and "Crash" not in names, names       # failed plugin gives NO vote (not a neutral one)
    assert c.error and getattr(c, "error_code", "") == "PLUGIN_ERROR" and not g.error
    st = [p for p in h.state()["plugins"] if p["key"] == "test_crash.Crash"][0]
    assert st["status"] == "UNAVAILABLE"
    assert h.collect_votes()                                       # terminal keeps running, others still vote


def test_malformed_vote_rejected():
    h = hub()
    m = add(h, Malformed, "test_mal.Malformed")
    votes = h.collect_votes()
    assert not [v for v in votes if v[0] == "Malformed"] and m.error_code == "PLUGIN_MALFORMED", m.error


def test_malformed_compute_rejected():
    h = hub()
    b = add(h, BadCompute, "test_badc.BadCompute")
    h.chart("M15")
    assert b.error_code == "PLUGIN_MALFORMED", b.error


def test_plugin_timeout_strikes():
    h = hub()
    s = add(h, Slow, "test_slow.Slow")
    old = dict(superchart.PLUGIN_SLOW_S)
    superchart.PLUGIN_SLOW_S["vote"] = 0.001
    try:
        for _ in range(superchart.PLUGIN_STRIKES):
            h.collect_votes()
    finally:
        superchart.PLUGIN_SLOW_S.update(old)
    assert s.error_code == "PLUGIN_TIMEOUT", (s.error, getattr(s, "strikes", None))


def test_forbidden_imports_detected_and_not_loaded():
    assert contract_violations("import trading\n") and contract_violations("from mt5_ops import open_market\n")
    assert contract_violations("x = __import__('trading')\n") and contract_violations("mt5.order_send(r)\n")
    assert contract_violations("import os, subprocess\n")
    assert not contract_violations("import math\nfrom plugin_api import Indicator\nimport exits\n")
    d = tempfile.mkdtemp()
    with open(os.path.join(d, "evil.py"), "w") as f:
        f.write("import trading\nfrom plugin_api import Indicator\nclass Evil(Indicator):\n    name='Evil'\n")
    with open(os.path.join(d, "fine.py"), "w") as f:
        f.write("from plugin_api import Indicator\nclass Fine(Indicator):\n    name='Fine'\n")
    old = plugin_loader.PLUGIN_DIR
    plugin_loader.PLUGIN_DIR = d
    try:
        ps, errs = plugin_loader.load_plugins()
    finally:
        plugin_loader.PLUGIN_DIR = old
    assert [p.key for p in ps] == ["fine.Fine"] and any("PLUGIN_FORBIDDEN" in e for e in errs), (ps, errs)


def test_shipped_plugins_follow_the_contract():
    ps, errs = plugin_loader.load_plugins()
    assert not [e for e in errs if "PLUGIN_FORBIDDEN" in e], errs


def test_plugin_context_has_no_order_access():
    h = hub()
    ctx = h.ctx()
    fields = set(Context.__dataclass_fields__)
    assert fields == {"symbol", "bars", "tick", "smc", "read", "utc_offset", "history"}, fields
    for bad in ("trader", "hub", "gate", "risk", "execution", "positions_mgr", "autotrader"):
        assert not hasattr(ctx, bad), bad
    b = ctx.bars("M5")
    n = len(h.src.bars("M5"))
    b.clear()                                                      # a plugin mutating its copy
    assert len(h.src.bars("M5")) == n


def test_chart_objects_have_owners():
    h = hub()
    add(h, Good, "test_good2.Good")
    ch = h.chart("M15")
    mine = [x for x in ch["levels"] if x.get("owner") == "test_good2.Good"]
    assert mine and all(x["type"] == "level" and x["timeframe"] == "M15" and x["created_at"] for x in mine)
    assert all("owner" in x for x in ch["levels"] + ch["lines"] + ch["zones"] + ch["markers"])


def test_indicator_drawn_only_on_chosen_timeframes():
    h = hub()
    g = add(h, Good, "test_tfs.Good")
    h.set_plugin("test_tfs.Good", chart_tfs=["H4", "bogus"])
    assert g.chart_tfs == ["H4"]
    own = lambda tf: [x for x in h.chart(tf)["levels"] if x.get("owner") == "test_tfs.Good"]
    assert own("H4") and not own("M15")                           # drawn on H4 only
    assert "Good" in [v[0] for v in h.collect_votes()]             # the SCORE vote is not affected
    saved = g.settings()
    g2 = LoadedPlugin(Good(), "test_tfs.Good", "indicator")
    g2.set_chart_tfs(saved["chart_tfs"])
    assert g2.shows_on("H4") and not g2.shows_on("M15")
    h.set_plugin("test_tfs.Good", chart_tfs=[])                   # empty = every timeframe again
    assert own("M15") and own("H4")


class TfVote(Indicator):
    """Votes UP on H4, DOWN on every other timeframe (to see which timeframes are scored)."""
    name, category = "TfVote", "Trend"

    def vote(self, ctx):
        return Vote(1 if self.vote_tf == "H4" else -1, self.vote_tf)


def test_score_follows_chart_timeframes():
    h = hub()
    p = add(h, TfVote, "test_tfv.TfVote")
    mine = lambda: [v for v in h.collect_votes() if v[0] == "TfVote"]
    assert mine()[0][2] == -1                                     # no chart TF chosen -> vote timeframe (M15)
    h.set_plugin("test_tfv.TfVote", chart_tfs=["H4"])
    assert mine()[0][2] == 1                                      # shown on H4 only -> scored on H4 only
    h.set_plugin("test_tfv.TfVote", chart_tfs=["H4", "H1", "M15"])
    v = mine()
    assert len(v) == 1 and v[0][2] == -1 and "H4" in v[0][3]      # 3 TFs -> ONE vote, majority (1 up, 2 down)
    h.set_plugin("test_tfv.TfVote", chart_tfs=["H4", "M15"])
    assert mine()[0][2] == 0                                      # tie -> neutral
    assert p.obj.vote_tf == "M15"                                 # the temporary switch is restored
    # history (backtest) uses the same timeframes
    t_end = h.src.bars("M15")[-3].t + 900
    h.set_plugin("test_tfv.TfVote", chart_tfs=["H4"])
    r = h.read_at(t_end)
    items = [it for g in r.groups for it in g["items"] if it["name"] == "TfVote"]
    assert items and items[0]["dir"] == 1, items
    h.set_plugin("test_tfv.TfVote", chart_tfs=[])


def test_module_checks_follow_chart_timeframes():
    from engine import MOD_PD, market_read
    h = hub()
    pd = next(p for p in h.plugins if getattr(p.obj, "analyser_module", None) == MOD_PD)
    old = (pd.scoring, list(pd.chart_tfs))
    try:
        pd.scoring = True
        pd.set_chart_tfs(["H4"])
        assert h.module_tfs() == {MOD_PD: ["H4"]}
        r = market_read(h.states, h.src.bars("D1"), h.src.bars("M1"), {MOD_PD}, (), h.scoring, h.module_tfs())
        it = [i for g in r.groups for i in g["items"] if i["name"] == "Premium/Discount"]
        if h.states["H4"].pd is not None:
            assert it and it[0]["reason"].startswith("H4"), it
        pd.set_chart_tfs(["M1"])                                  # no structure data on M1 -> default H1 check
        assert h.module_tfs() == {}
    finally:
        pd.scoring = old[0]
        pd.set_chart_tfs(old[1])


def test_prev_day_volume_profile_uses_only_the_completed_day():
    import importlib.util
    spec = importlib.util.spec_from_file_location("vppd", os.path.join(os.path.dirname(plugin_loader.__file__),
                                                                        "plugins", "volume_profile.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    from engine import Bar
    day0 = 20000 * 86400
    bars = []
    for d, base in ((0, 100.0), (1, 200.0), (2, 300.0)):          # three server days, 288 M5 bars each
        for i in range(288):
            px = base + (i % 20) * 0.5
            bars.append(Bar(day0 + d * 86400 + i * 300, px, px + 0.4, px - 0.4, px + 0.1, 100))
    now = bars[-1].t
    vp = m.prev_day_profile(bars, now)
    assert 199 <= vp["val"] <= vp["poc"] <= vp["vah"] <= 211 and vp["day"] == day0 + 86400, vp["poc"]
    # changing TODAY (still forming) never moves yesterday's levels
    bars2 = bars[:-100] + [Bar(b.t, b.o + 50, b.h + 60, b.l + 40, b.c + 50, b.v * 9) for b in bars[-100:]]
    vp2 = m.prev_day_profile(bars2, now)
    assert (vp2["poc"], vp2["vah"], vp2["val"]) == (vp["poc"], vp["vah"], vp["val"])
    # a short day (Sunday open) is skipped -> the previous FULL day is used
    short = bars[:576] + [Bar(day0 + 2 * 86400 + i * 300, 500, 500.4, 499.6, 500.1, 100) for i in range(10)]
    short += [Bar(day0 + 3 * 86400 + i * 300, 300, 300.4, 299.6, 300.1, 100) for i in range(5)]
    vp3 = m.prev_day_profile(short, short[-1].t, min_bars=40)
    assert vp3["day"] == day0 + 86400, vp3["day"]
    # votes: above VAH +1, below VAL -1, inside 0
    ind = m.VolumeProfile()
    ind.params = dict(ind.params)

    class Ctx:
        def __init__(self, bs):
            self._b = bs

        def bars(self, tf):
            return self._b

    def with_close(c):
        last = bars[-1]
        return bars[:-1] + [Bar(last.t, c, c + 0.1, c - 0.1, c, 1), Bar(last.t + 300, c, c, c, c, 1)]
    assert ind.vote(Ctx(with_close(vp["vah"] + 5))).direction == 1
    assert ind.vote(Ctx(with_close(vp["val"] - 5))).direction == -1
    assert ind.vote(Ctx(with_close((vp["val"] + vp["vah"]) / 2))).direction == 0



def test_daily_profiles_max_five_days():
    import importlib.util
    spec = importlib.util.spec_from_file_location("vpd", os.path.join(os.path.dirname(plugin_loader.__file__),
                                                                       "plugins", "volume_profile.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    from engine import Bar
    day0 = 20000 * 86400
    bars = [Bar(day0 + d * 86400 + i * 300, 100 + d * 10 + (i % 20) * 0.5, 100.4 + d * 10 + (i % 20) * 0.5,
                99.6 + d * 10 + (i % 20) * 0.5, 100.1 + d * 10 + (i % 20) * 0.5, 100)
            for d in range(9) for i in range(288)]
    days = m.daily_profiles(bars, bars[-1].t, days=8)
    assert len(days) == 8 and all(d["complete"] for d in days)
    ind = m.VolumeProfile()
    ind.params = dict(ind.params, days_shown=9)

    class Ctx:
        def bars(self, tf):
            return bars
    ov = ind.compute(bars, "M5", Ctx())
    assert len(ov.profiles) == 5                                   # capped at 5 so the chart stays light
    assert all(pf.t_from is not None and pf.t_to is not None and pf.t_from < pf.t_to for pf in ov.profiles)
    assert {lv.label for lv in ov.levels} == {"pPOC", "pVAH", "pVAL"}
    ind.params["show_today"] = True
    ind._cache = (None, None)
    ov2 = ind.compute(bars, "M5", Ctx())
    assert len(ov2.profiles) == 6 and ov2.profiles[-1].color == "#94a3b8"      # + today's faint developing one
