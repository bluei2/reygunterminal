"""Loads plugins/*.py and remembers each plugin's settings (chart on/off, score on/off, parameters)."""
from __future__ import annotations

import importlib.util
import inspect
import json
import os
import re
import traceback

from plugin_api import Indicator, Strategy

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_DIR = os.path.join(HERE, "plugins")
SETTINGS_FILE = os.path.join(HERE, "plugin_settings.json")
OLD_STATE_FILE = os.path.join(HERE, "plugins_enabled.json")     # older versions: {key: bool}
VOTE_TFS = ["M1", "M5", "M15", "M30", "H1", "H4", "D1"]


class LoadedPlugin:
    def __init__(self, obj, key: str, kind: str):
        self.obj = obj
        self.key = key          # "file.ClassName"
        self.kind = kind        # "indicator" | "strategy"
        self.enabled = bool(getattr(obj, "default_on", False))    # CHART (indicators) / ON (strategies)
        self.scoring = False                                        # SCORE (indicators only)
        self.error: str | None = None
        # every instance gets its own copy of the defaults
        self.defaults = dict(getattr(type(obj), "params", {}) or {})
        obj.params = dict(self.defaults)
        self.default_vote_tf = getattr(type(obj), "vote_tf", "M15")
        self.chart_tfs: list[str] = []  # timeframes where it is DRAWN ([] = every timeframe). When set, the SCORE
                                        # uses the same timeframes ("where the indicator is, there it scores")
        import threading
        self.vote_lock = threading.Lock()

    @property
    def name(self):
        return getattr(self.obj, "name", self.key)

    @property
    def can_score(self) -> bool:
        """Indicators that vote or switch on an analyser module can take part in the score."""
        if self.kind != "indicator":
            return False
        votes = type(self.obj).vote is not Indicator.vote
        return votes or bool(getattr(self.obj, "analyser_module", None))

    @property
    def votes(self) -> bool:
        return self.kind == "indicator" and type(self.obj).vote is not Indicator.vote

    def set_params(self, new: dict) -> dict:
        """Apply user values, cast to the default's type. Unknown keys are ignored. Returns applied params."""
        out = dict(self.obj.params)
        for k, v in (new or {}).items():
            if k not in self.defaults:
                continue
            d = self.defaults[k]
            try:
                if isinstance(d, bool):
                    v = v if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes", "on")
                elif isinstance(d, int):
                    v = int(round(float(v)))
                elif isinstance(d, float):
                    v = float(v)
                else:
                    v = str(v)
            except (TypeError, ValueError):
                continue
            out[k] = v
        self.obj.params = out
        return out

    def reset_params(self):
        self.obj.params = dict(self.defaults)
        self.obj.vote_tf = self.default_vote_tf
        self.chart_tfs = []

    def set_chart_tfs(self, tfs) -> list:
        """Timeframes where this indicator is drawn. Unknown values are ignored; [] or all = every timeframe."""
        clean = [t for t in VOTE_TFS if t in set(tfs or [])]
        self.chart_tfs = [] if len(clean) == len(VOTE_TFS) else clean
        return self.chart_tfs

    def shows_on(self, tf) -> bool:
        return not self.chart_tfs or tf in self.chart_tfs

    def score_tfs(self) -> list:
        """Timeframes this indicator is scored on: its chart timeframes when chosen, else its vote timeframe."""
        return list(self.chart_tfs) if self.chart_tfs else [getattr(self.obj, "vote_tf", self.default_vote_tf)]

    def settings(self) -> dict:
        return dict(chart=self.enabled, score=self.scoring,
                    params={k: v for k, v in self.obj.params.items() if self.defaults.get(k) != v},
                    vote_tf=getattr(self.obj, "vote_tf", self.default_vote_tf), chart_tfs=list(self.chart_tfs))


# Plugin safety contract: a plugin may READ market data / indicators, CALCULATE, DRAW and RETURN votes / signals.
# It must not send or modify orders, change risk, or bypass the gate. Python cannot fully sandbox code, so the
# loader refuses plugin files that import the trading / broker / gate modules or use dynamic-import tricks, and
# the plugin Context carries no reference to the trader, the accounts or the gate.
FORBIDDEN_MODULES = ("mt5_ops", "trading", "account_worker", "MetaTrader5", "trade_gate", "trade_confirmation",
                     "risk_engine", "execution_guard", "position_manager", "reconciliation", "autotrade", "superchart",
                     "news_trader", "exitguard", "notify", "subprocess", "socket", "ctypes", "multiprocessing")
FORBIDDEN_TOKENS = ("order_send", "order_check", "__import__", "importlib", "sys.modules", "positions_get",
                    "globals()", "__builtins__")
_IMPORT_RX = re.compile(r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w., ]+))", re.M)


def contract_violations(source: str) -> list[str]:
    """Static check of a plugin file against the safety contract. -> list of violations (empty = OK)."""
    out = []
    for m in _IMPORT_RX.finditer(source):
        names = [m.group(1)] if m.group(1) else [x.strip().split(" as ")[0] for x in m.group(2).split(",")]
        for n in names:
            root = (n or "").split(".")[0]
            if root in FORBIDDEN_MODULES:
                out.append(f"imports '{root}'")
    for tok in FORBIDDEN_TOKENS:
        if tok in source:
            out.append(f"uses '{tok}'")
    return out


def load_plugins() -> tuple[list[LoadedPlugin], list[str]]:
    plugins: list[LoadedPlugin] = []
    errors: list[str] = []
    if not os.path.isdir(PLUGIN_DIR):
        return plugins, errors
    for fn in sorted(os.listdir(PLUGIN_DIR)):
        if not fn.endswith(".py") or fn.startswith("_"):
            continue
        path = os.path.join(PLUGIN_DIR, fn)
        mod_name = f"reygun_plugin_{fn[:-3]}"
        try:
            with open(path, encoding="utf-8") as f:
                bad = contract_violations(f.read())
        except OSError as e:
            bad = [f"unreadable: {e}"]
        if bad:
            errors.append(f"{fn}: PLUGIN_FORBIDDEN (not loaded): {'; '.join(bad)}")
            continue
        try:
            spec = importlib.util.spec_from_file_location(mod_name, path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        except Exception as e:
            errors.append(f"{fn}: {e.__class__.__name__}: {e}")
            continue
        for _, cls in inspect.getmembers(mod, inspect.isclass):
            if cls.__module__ != mod_name:
                continue
            kind = "indicator" if issubclass(cls, Indicator) and cls is not Indicator else \
                   "strategy" if issubclass(cls, Strategy) and cls is not Strategy else None
            if not kind:
                continue
            try:
                plugins.append(LoadedPlugin(cls(), f"{fn[:-3]}.{cls.__name__}", kind))
            except Exception as e:
                errors.append(f"{fn}.{cls.__name__}: {e}")
    _restore(plugins)
    return plugins, errors


def _restore(plugins):
    saved = {}
    try:
        with open(SETTINGS_FILE) as f:
            saved = json.load(f)
    except (OSError, ValueError):
        try:                                    # migrate the old on/off file: ON meant chart + score
            with open(OLD_STATE_FILE) as f:
                saved = {k: {"chart": bool(v), "score": bool(v)} for k, v in json.load(f).items()}
        except (OSError, ValueError):
            saved = {}
    for p in plugins:
        s = saved.get(p.key)
        if not isinstance(s, dict):
            continue
        p.enabled = bool(s.get("chart", p.enabled))
        p.scoring = bool(s.get("score", False)) and p.can_score
        p.set_params(s.get("params") or {})
        tf = s.get("vote_tf")
        if tf in VOTE_TFS:
            p.obj.vote_tf = tf
        if isinstance(s.get("chart_tfs"), list):
            p.set_chart_tfs(s["chart_tfs"])


def save_state(plugins: list[LoadedPlugin]) -> None:
    try:
        with open(SETTINGS_FILE, "w") as f:
            json.dump({p.key: p.settings() for p in plugins}, f, indent=2)
    except OSError:
        pass


SCORING_FILE = os.path.join(HERE, "scoring_settings.json")


def load_scoring() -> dict:
    from engine import clean_scoring
    try:
        with open(SCORING_FILE) as f:
            return clean_scoring(json.load(f))
    except (OSError, ValueError):
        return clean_scoring(None)


def save_scoring(sc: dict) -> dict:
    from engine import clean_scoring
    sc = clean_scoring(sc)
    try:
        with open(SCORING_FILE, "w") as f:
            json.dump(sc, f, indent=2)
    except OSError:
        pass
    return sc


def short_tb(e: Exception) -> str:
    tb = traceback.extract_tb(e.__traceback__)
    where = f"{os.path.basename(tb[-1].filename)}:{tb[-1].lineno}" if tb else ""
    return f"{e.__class__.__name__}: {e} ({where})"
