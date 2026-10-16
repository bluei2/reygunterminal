"""v2.8.53: settings history + one-click rollback, and the AUTO loss-streak pause."""
import os
import time
import types

from harness import TMP

import settings_history as SH
import strategies as ST


def test_history_keeps_changes_and_restores():
    path = os.path.join(TMP, "sh_test.jsonl")
    if os.path.exists(path):
        os.remove(path)
    ST.save_settings(ST.apply_profile(ST.default_settings(), "normal"))
    h = SH.SettingsHistory("9.9.9", path=path, start=False)
    assert len(h.rows) == 1 and h.snapshot("same") is None                 # identical content is not stored twice
    first = h.rows[-1]["id"]
    s = ST.load_settings()
    s["trend"]["rr"] = 3.0
    s["htf_filter"] = False
    ST.save_settings(s)
    row = h.snapshot("binago")
    assert row and any("trend.rr 5.0 → 3.0" in c for c in row["changes"]) and any("htf_filter" in c for c in row["changes"])
    lst = h.list()
    assert lst[0]["current"] and lst[0]["reason"] == "binago" and not lst[1]["current"]
    at = types.SimpleNamespace(settings=None)
    hub = types.SimpleNamespace(autotrader=at, positions_mgr=None, newstrader=None)
    r = h.restore(first, hub)
    assert r["ok"] and ST.load_settings()["trend"]["rr"] == 5.0 and ST.load_settings()["htf_filter"] is True
    assert at.settings is not None                                          # running AUTO TRADE reloaded
    reasons = [x["reason"] for x in h.list()]
    assert reasons[0].startswith("ibinalik") and reasons[1] == "binago"     # the state before is kept: undoable
    h2 = SH.SettingsHistory("9.9.9", path=path, start=False)                # survives a restart
    assert len(h2.rows) == len(h.rows)
    assert h.restore(123, hub)["ok"] is False


def _at_with_journal(results, ago_min=10):
    from test_strategies import _auto
    hub, tm, g, at = _auto(score=40.0, bias=1)
    now = time.time()
    tm.journal.entries.clear()
    for k, res in enumerate(results):                                       # oldest first
        t = now - ago_min * 60 - (len(results) - k) * 60
        tm.journal.entries[f"L{k}"] = dict(gid=f"L{k}", status="closed", kind="auto", result=res, close_time=t,
                                           open_time=t - 300)
    return hub, tm, at


def test_loss_streak_pauses_new_auto_entries():
    hub, tm, at = _at_with_journal(["WIN", "LOSS", "BE", "LOSS", "LOSS"])
    at.settings.update(loss_pause_n=3, loss_pause_h=2.0, loss_pause_reset_t=0.0)
    lp = at.loss_pause()
    assert lp["streak"] == 3 and lp["active"] and lp["until_txt"].endswith("Manila")
    t = int(time.time()) // 900 * 900
    at._consider("st_trend", dict(side=1, entry=2650.0, sl=2642.0, tp=2690.0, score=100.0, cautions=[], t=t,
                                  strategy="trend", kind="TREND"))
    assert at._last_res == "BLOCKED_LOSS_STREAK" and not tm.groups
    assert at.state()["loss_pause"]["active"]
    r = at.resume_after_losses()
    assert r["ok"] and not r["loss_pause"]["active"] and r["loss_pause"]["streak"] == 0


def test_loss_streak_ends_with_a_win_or_time_or_off():
    hub, tm, at = _at_with_journal(["LOSS", "LOSS", "LOSS", "WIN"])
    at.settings.update(loss_pause_n=3, loss_pause_h=2.0, loss_pause_reset_t=0.0)
    assert at.loss_pause()["streak"] == 0 and not at.loss_pause()["active"]
    hub, tm, at = _at_with_journal(["LOSS", "LOSS", "LOSS"], ago_min=180)    # the pause (2 h) is over
    at.settings.update(loss_pause_n=3, loss_pause_h=2.0, loss_pause_reset_t=0.0)
    assert at.loss_pause()["streak"] == 3 and not at.loss_pause()["active"]
    hub, tm, at = _at_with_journal(["LOSS", "LOSS", "LOSS"])
    at.settings.update(loss_pause_n=0, loss_pause_reset_t=0.0)
    assert not at.loss_pause()["active"]
    import autotrade
    assert autotrade.DEFAULTS["loss_pause_n"] == 3 and autotrade.DEFAULTS["loss_pause_h"] == 2.0
