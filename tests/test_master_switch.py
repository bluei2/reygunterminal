"""v2.8.59: changing the master account - a follower can become the master, it is then never also a follower,
and the block while trades are open says what is open."""
import time
import types

from harness import make_tm


def _cfg(tm, master=None, followers=None):
    c = {"master": dict(tm.cfg["master"], **(master or {})), "followers": followers if followers is not None else
         [dict(name="LiteFinance", path="C:\\\\MT5_LF\\\\terminal64.exe", login=89135371, symbol="XAUUSD_ph", lot=0.02)],
         "copy_manual": True}
    return c


def test_new_master_is_removed_from_the_followers():
    hub, tm = make_tm(0)
    r = tm.update_config(_cfg(tm, master=dict(path="C:\\\\MT5_LF\\\\terminal64.exe", login=89135371)))
    assert r["ok"], r
    assert tm.cfg["followers"] == [] and "inalis bilang follower" in r.get("warning", "")
    r2 = tm.update_config(_cfg(tm, master=dict(path="", login=89135371)))     # same account by login
    assert r2["ok"] and tm.cfg["followers"] == []


def test_other_followers_are_kept():
    hub, tm = make_tm(0)
    fl = [dict(name="A", path="C:\\\\A\\\\terminal64.exe", login=1, lot=0.01),
          dict(name="B", path="C:\\\\B\\\\terminal64.exe", login=2, lot=0.02)]
    r = tm.update_config(_cfg(tm, master=dict(path="C:\\\\b\\\\TERMINAL64.exe", login=2), followers=fl))
    assert r["ok"] and [f["name"] for f in tm.cfg["followers"]] == ["A"]


def test_block_while_trades_open_says_what_is_open():
    hub, tm = make_tm(0)
    tm.src = types.SimpleNamespace(**dict(vars(tm.src), live=True))
    tm.set_armed(True)
    assert tm.open(1, 0.01, 5.0, 10.0).get("ok")
    r = tm.update_config(_cfg(tm, master=dict(path="C:\\\\Other\\\\terminal64.exe", login=5)))
    assert not r["ok"] and "1 SuperChart trade" in r["error"] and "Save accounts" in r["error"]


def test_save_switches_when_the_chart_is_still_on_another_mt5():
    hub, tm = make_tm(0)
    calls = []
    tm.src = types.SimpleNamespace(**dict(vars(tm.src), live=True, path="C:\\\\Switch\\\\terminal64.exe", symbol="XAUUSDs"))
    hub.reconnect_master = lambda p, l, s: calls.append((p, l, s)) or None
    tm.cfg["master"]["path"] = "C:\\\\LF\\\\terminal64.exe"            # saved before, but the chart never moved
    r = tm.update_config(_cfg(tm, master=dict(path="C:\\\\LF\\\\terminal64.exe"), followers=[]))
    assert r["ok"] and calls and calls[0][0] == "C:\\\\LF\\\\terminal64.exe", (r, calls)


def test_fallback_master_picks_the_open_terminal_with_the_symbol():
    import mt5_detect as D
    lf = dict(ok=True, path="C:\\LF\\terminal64.exe", login=89135371, has_symbol=True)
    sw = dict(ok=True, path="C:\\SW\\terminal64.exe", login=4009140, has_symbol=False)
    f2 = dict(ok=True, path="C:\\LF2\\terminal64.exe", login=777, has_symbol=True)
    assert D.fallback_master([sw, lf], 4009140, "XAUUSD_ph") is lf          # old login closed / no symbol: the open one
    assert D.fallback_master([lf, f2], 89135371, "XAUUSD_ph") is lf         # the saved login first
    assert D.fallback_master([lf, f2], None, "XAUUSD_ph", ["C:\\LF2\\terminal64.exe"]) is lf   # not a follower
    assert D.fallback_master([lf, f2], None, "XAUUSD_ph") is None           # not clear: none
    assert D.fallback_master([sw], None, "XAUUSD_ph") is None               # nothing with the symbol
