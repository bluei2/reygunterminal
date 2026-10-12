"""v2.8.29: SuperChart logs the follower MT5 terminals in (login / password / server); passwords stay on the VPS."""
import queue
import sys
import types

sys.path.insert(0, "..")
import trading as T          # noqa: E402
import account_worker as W   # noqa: E402
from harness import make_tm  # noqa: E402


def test_password_never_reaches_the_browser_and_is_kept():
    f = T.clean_follower(dict(name="Prop", path="C:/MT5_P/terminal64.exe", login=123, server="Broker-Live", password="s3cret"))
    assert f["password"] == "s3cret" and f["server"] == "Broker-Live"
    pub = T.public_config(dict(master={}, followers=[f]))
    assert "password" not in pub["followers"][0] and pub["followers"][0]["has_password"] is True
    hub, tm = make_tm(0)
    tm.cfg["followers"] = [f]
    kept = tm._keep_password(dict(f, password=""))
    assert kept["password"] == "s3cret"                                   # empty field = keep
    assert tm._keep_password(dict(f, password="new"))["password"] == "new"
    assert tm._keep_password(dict(f, clear_password=True))["password"] == ""
    assert "s3cret" not in str(tm.state()["config"])                       # the full state the browser gets


def test_worker_logs_in_with_the_credentials():
    seen = {}
    fake = types.SimpleNamespace(initialize=lambda **kw: seen.update(kw) or False, last_error=lambda: (-6, "auth"),
                                 shutdown=lambda: None)
    sys.modules["MetaTrader5"] = fake
    try:
        q = queue.Queue()
        W.worker_main("C:/MT5_P/terminal64.exe", 123, queue.Queue(), q, "s3cret", "Broker-Live")
        tag, info = q.get_nowait()
        assert seen == dict(path="C:/MT5_P/terminal64.exe", login=123, password="s3cret", server="Broker-Live"), seen
        assert not info["ok"] and "password" in info["error"]                # a clear hint when the login fails
        seen.clear()
        W.worker_main("C:/MT5_P/terminal64.exe", 123, queue.Queue(), q, None, None)
        assert seen == dict(path="C:/MT5_P/terminal64.exe")                  # no password: the terminal's own login
    finally:
        sys.modules.pop("MetaTrader5", None)
