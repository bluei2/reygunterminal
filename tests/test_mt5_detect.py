"""MT5 finder: pick the right OPEN terminal as master (no MT5 needed for these tests)."""
import mt5_detect as D


def test_pick_master_prefers_login_then_symbol():
    found = [dict(ok=True, path="C:/A/terminal64.exe", login=40065413, has_symbol=False),
             dict(ok=True, path="C:/B/terminal64.exe", login=89135371, has_symbol=True),
             dict(ok=False, path="C:/C/terminal64.exe", error="not logged in")]
    assert D.pick_master(found, login=40065413, symbol="XAUUSD_ph")["path"] == "C:/A/terminal64.exe"
    assert D.pick_master(found, login=None, symbol="XAUUSD_ph")["login"] == 89135371      # only one has the symbol
    two = [dict(found[1]), dict(found[1], path="C:/D/terminal64.exe", login=1)]
    assert D.pick_master(two, None, "XAUUSD_ph") is None                                 # ambiguous: you choose
    assert D.pick_master([found[0]], None, None)["login"] == 40065413                    # the only open one
    assert D.pick_master([], 1, "X") is None


def test_running_terminals_is_empty_off_windows():
    import sys
    if sys.platform != "win32":
        assert D.running_terminals() == [] and D.detect("XAUUSD") == []
