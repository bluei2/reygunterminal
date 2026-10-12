"""Test helpers: a TradeManager on synthetic prices with controllable fake accounts, in a temporary folder
(nothing in your real settings / journal is touched)."""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

TMP = tempfile.mkdtemp(prefix="reygun_test_")

import journal          # noqa: E402
import trading          # noqa: E402

journal.HERE = TMP
trading.HERE = TMP
trading.CONFIG_FILE = os.path.join(TMP, "accounts.json")

# every settings / state file of the newer modules goes to the temp folder too
import decision_engine  # noqa: E402
import market_hours  # noqa: E402
import position_manager  # noqa: E402
import risk_engine  # noqa: E402
import smc_engine  # noqa: E402
import trade_gate  # noqa: E402

risk_engine.MASTER_FILE = os.path.join(TMP, "master_switch.json")
risk_engine.RISK_FILE = os.path.join(TMP, "risk_settings.json")
risk_engine.PEAK_FILE = os.path.join(TMP, "risk_peak.json")
trade_gate.HERE = TMP
position_manager.SETTINGS_FILE = os.path.join(TMP, "position_settings.json")
decision_engine.SETTINGS_FILE = os.path.join(TMP, "decision_settings.json")
smc_engine.SETTINGS_FILE = os.path.join(TMP, "smc_settings.json")
market_hours.SESSIONS_FILE = os.path.join(TMP, "session_settings.json")

# v2.8.44: EVERY runtime file of EVERY module goes to the temp folder. Before this, strategies.py (and a few others)
# still wrote into the program folder, and that test file ended up inside the release zip (it overwrote the user's
# strategy settings on every update since v2.8.30).
import importlib  # noqa: E402
for _name in ("strategies", "autotrade", "news_trader", "news", "news_results", "forecast", "execution_guard",
              "exitguard", "intermarket", "regime", "runstate", "notify", "plugin_loader", "drift", "ai_analyst"):
    try:
        _m = importlib.import_module(_name)
    except Exception:
        continue
    for _attr, _val in list(vars(_m).items()):
        if _attr.endswith("_FILE") and isinstance(_val, str) and os.path.dirname(os.path.abspath(_val)) == ROOT:
            setattr(_m, _attr, os.path.join(TMP, os.path.basename(_val)))
import plugin_loader  # noqa: E402

plugin_loader.SETTINGS_FILE = os.path.join(TMP, "plugin_settings.json")
plugin_loader.SCORING_FILE = os.path.join(TMP, "scoring_settings.json")
import forecast  # noqa: E402

forecast.JOURNAL_FILE = os.path.join(TMP, "forecast_journal.jsonl")
forecast.SETTINGS_FILE = os.path.join(TMP, "forecast_settings.json")
forecast.PROFILE_FILE = os.path.join(TMP, "forecast_profile.json")
import autotrade  # noqa: E402
import strategies  # noqa: E402

strategies.SETTINGS_FILE = os.path.join(TMP, "strategy_settings.json")
import importlib as _il  # noqa: E402

for _m in ("execution_guard", "exitguard", "intermarket", "news", "news_trader", "notify", "regime"):
    try:                                   # every settings file of the app -> temp folder (never the real ones)
        _mod = _il.import_module(_m)
        if hasattr(_mod, "SETTINGS_FILE"):
            _mod.SETTINGS_FILE = os.path.join(TMP, os.path.basename(_mod.SETTINGS_FILE))
    except Exception:
        pass

autotrade.SETTINGS_FILE = os.path.join(TMP, "auto_trade_settings.json")
import ai_analyst  # noqa: E402

ai_analyst.SETTINGS_FILE = os.path.join(TMP, "ai_settings.json")
ai_analyst.JOURNAL_FILE = os.path.join(TMP, "ai_journal.jsonl")

from engine import Tick  # noqa: E402


class FakeHub:
    """Enough of Hub for TradeManager: a price, notes, no plugins."""

    def __init__(self, bid=2650.0):
        self.src = types.SimpleNamespace(live=False, symbol="XAUUSD", name="TEST", status="test", login_error="",
                                         path="")
        self.lock = threading.RLock()
        self.last_tick = Tick(int(time.time() * 1000), bid, bid + 0.2, 1.0)
        self.read = None
        self.utc_offset = 0
        self.notes = []
        self.autotrader = None
        self.notifier = None
        self.alerts = None
        self.regime = None
        self.newstrader = None
        self.news = None
        self.plugins = []

    def note(self, kind, text, **kw):
        self.notes.append((kind, text))

    def set_price(self, bid):
        self.last_tick = Tick(int(time.time() * 1000), bid, bid + 0.2, 1.0)


class FakeBackend(trading.PaperBackend):
    """PaperBackend whose behaviour can be broken on purpose (no SL on fill, modify fails, read fails)."""

    def __init__(self, price_fn, name="fake"):
        super().__init__(price_fn, 10000.0, name)
        self.drop_sl = False          # fill without the stop loss
        self.modify_fails = False
        self.fail_reads = False       # positions() returns an error (stale account)
        self.server_time = None
        self.calls = []

    def call(self, cmd, *args):
        self.calls.append(cmd)
        if self.fail_reads and cmd in ("positions", "account", "orders"):
            return dict(ok=False, error="simulated read failure")
        return super().call(cmd, *args)

    def c_open_market(self, symbol, side, lot, sl_dist=0.0, tp_dist=0.0, comment=""):
        r = super().c_open_market(symbol, side, lot, sl_dist, tp_dist, comment)
        if self.drop_sl and r.get("ok"):
            self.positions_[r["ticket"]]["sl"] = 0.0
            r["sl"] = 0.0
        return r

    def c_modify(self, ticket, sl, tp):
        if self.modify_fails:
            return dict(ok=False, error="simulated: invalid stops")
        return super().c_modify(ticket, sl, tp)

    def c_account(self, symbol=None):
        r = super().c_account(symbol)
        if self.server_time is not None:
            r["server_time"] = self.server_time
        return r


def make_tm(n_followers=1, loop=False):
    """TradeManager in paper mode with fake master + followers. The background loop is stopped so tests drive
    refresh() / sync() / protect() by hand (deterministic)."""
    for f in os.listdir(TMP):                              # every test starts clean
        if f.startswith(("journal", "trade_groups", "risk_state", "signals", "master_switch", "risk_settings",
                         "risk_peak", "position_settings", "decision_settings")):
            os.remove(os.path.join(TMP, f))
    hub = FakeHub()
    tm = trading.TradeManager(hub, paper=True)
    tm.running = False                                    # stop the loop thread; tests call the steps
    time.sleep(1.1)
    price = tm._price_fn
    tm.master.backend = FakeBackend(price, "master")
    tm.master.status = "connected"
    tm.followers = []
    for k in range(n_followers):
        a = trading.Account(f"f{k + 1}", "follower", dict(trading.DEFAULT_FOLLOWER, id=f"f{k + 1}", name=f"F{k + 1}",
                                                          lot=0.01, enabled=True), "XAUUSD")
        a.backend = FakeBackend(price, a.name)
        a.status = "connected"
        tm.followers.append(a)
    hub.trader = tm
    tm.refresh()
    return hub, tm


def cycle(tm, n=1):
    for _ in range(n):
        tm.refresh()
        tm.risk_check()
        tm.sync()
        tm.protect()
        tm.track_excursion()
