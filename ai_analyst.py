"""
AI ANALYST (Claude Fable) - a READ-ONLY second opinion on the chart and on the score.

What it does
  * builds a compact snapshot of what the terminal already knows (price, HTF bias, score + every group / item,
    the points each item costs, SMC, regime, previous-day volume profile, active forecasts, news state, recent
    H4 / H1 / M15 / M5 candles)
  * sends it to the Anthropic Messages API (model claude-fable-5-1 by default) ONLY when you press a button
  * shows the answer and keeps a local history (ai_journal.jsonl)

What it never does
  * it never sends, modifies or closes an order, never arms trading, never changes the score, the weights, the
    plugins, AUTO TRADE or any setting: the answer is text for you to read, nothing in it is executed
  * it never calls the API by itself (no timer, no auto mode): one button press = one request
  * the API key is stored only in ai_settings.json on this PC, is never sent anywhere except api.anthropic.com,
    is never returned by the API endpoints (only "set" + last 4 characters) and is redacted from the error log
"""
import json
import os
import threading
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(HERE, "ai_settings.json")
JOURNAL_FILE = os.path.join(HERE, "ai_journal.jsonl")
API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
MODELS = ("claude-fable-5-1", "claude-opus-5-5", "claude-sonnet-5", "claude-haiku-4-5-20251001")
MODES = ("analysis", "score", "question")

DEFAULTS = dict(
    api_key="",
    model="claude-fable-5-1",
    language="Taglish",          # Taglish | English | Filipino
    max_tokens=1800,
    min_interval_s=15,           # cost guard: at most one request every N seconds
    bars_h4=30, bars_h1=40, bars_m15=48, bars_m5=36,
)

SYSTEM = """You are the AI analyst inside the REYGUN SuperChart trading terminal (XAUUSD, MT5).
You are READ-ONLY: you cannot place, modify or close trades and nothing you write is executed. The trader decides.
Use ONLY the JSON snapshot you are given (closed candles, the terminal's own analyser and score). If something is
missing or stale, say so instead of guessing. Never invent prices, news or levels that are not in the snapshot.
Be concrete and short: numbers, levels and conditions, not generic advice. This is not financial advice.
Reply in {language}."""

TASKS = {
    "analysis": """Give a market read for {symbol} right now:
1. Bias (H4 / H1 / M15) in one line, and whether they agree.
2. Key levels from the snapshot (structure swings, SMC zones, previous-day POC / VAH / VAL, PDH / PDL).
3. Best scenario LONG or SHORT (or WAIT): where the entry would make sense, invalidation (SL idea), first target,
   and what must happen first (trigger).
4. The opposite scenario and what would switch you to it.
5. Your confidence (low / medium / high) and the main risk (news, range, conflicting TFs).""",
    "score": """Explain the terminal's score ({overall}% vs passing {passing}%):
1. Which groups / indicators pull it down (use the "lost" list: points each item costs) and whether that is a
   real market conflict (e.g. a pullback against the HTF) or a scoring artefact (neutral votes, one-indicator groups,
   an indicator that measures volatility and not direction, the same thing counted twice).
2. Compare the alternative scores in "alt" (neutral = ignore, per-indicator) and say which setting would describe
   THIS market better, and the risk of each.
3. Concrete suggestions for the indicator set / weights / timeframes. Do NOT tell the trader to lower the passing
   score just to get more trades.""",
    "question": "{question}",
}


def clean_settings(raw):
    s = dict(DEFAULTS)
    for k, v in (raw or {}).items():
        if k not in DEFAULTS:
            continue
        d = DEFAULTS[k]
        try:
            s[k] = type(d)(v) if not isinstance(d, str) else str(v).strip()
        except (TypeError, ValueError):
            continue
    if s["model"] not in MODELS and not s["model"].startswith("claude-"):
        s["model"] = DEFAULTS["model"]
    if s["language"] not in ("Taglish", "English", "Filipino"):
        s["language"] = "Taglish"
    s["max_tokens"] = max(300, min(8000, int(s["max_tokens"])))
    s["min_interval_s"] = max(5, int(s["min_interval_s"]))
    for k in ("bars_h4", "bars_h1", "bars_m15", "bars_m5"):
        s[k] = max(0, min(200, int(s[k])))
    return s


def load_settings(path=None):
    try:
        with open(path or SETTINGS_FILE) as f:
            return clean_settings(json.load(f))
    except (OSError, ValueError):
        return clean_settings(None)


def public_settings(s):
    """Settings for the UI: never the key itself."""
    out = {k: v for k, v in s.items() if k != "api_key"}
    out["key_set"] = bool(s.get("api_key"))
    out["key_hint"] = ("…" + s["api_key"][-4:]) if s.get("api_key") else ""
    return out


# ============================================================================ snapshot (read-only)
def _bars(src, tf, n):
    if n <= 0:
        return []
    try:
        b = src.bars(tf)[:-1][-n:]                     # closed bars only
    except Exception:
        return []
    return [[int(x.t), round(x.o, 2), round(x.h, 2), round(x.l, 2), round(x.c, 2)] for x in b]


def _read_dict(r):
    if r is None:
        return None
    return dict(bias={1: "BULL", -1: "BEAR"}.get(r.bias, "NONE"), overall=r.overall, passing=r.passing,
                passed=r.passed, trigger=r.trigger, verdict=r.verdict, lines=r.lines,
                groups=[dict(category=g["category"], pct=g["pct"], weight=g["weight"], agree=g["agree"],
                             total=g["total"], items=[dict(name=i["name"], vote=i["dir"], reason=i.get("reason", ""))
                                                      for i in g["items"]]) for g in r.groups],
                lost=getattr(r, "lost", []), alt=getattr(r, "alt", {}))


def snapshot(hub, cfg=None):
    """Everything the AI gets. Pure read: copies of values, nothing is changed on the hub."""
    c = cfg or DEFAULTS
    src = hub.src
    tk = getattr(hub, "last_tick", None)
    snap = dict(symbol=getattr(src, "symbol", "XAUUSD"), server_time=int(time.time()),
                price=dict(bid=getattr(tk, "bid", None), ask=getattr(tk, "ask", None)) if tk else None,
                utc_offset_h=getattr(hub, "utc_offset", 0),
                read=_read_dict(getattr(hub, "read", None)),
                scoring=getattr(hub, "scoring", None),
                regime=getattr(hub, "regime", None))
    st = getattr(hub, "states", None) or {}
    snap["timeframes"] = {tf: dict(trend={1: "BULL", -1: "BEAR"}.get(s.trend, "FLAT"),
                                   swing_high=round(s.sh.level, 2) if s.sh else None,
                                   swing_low=round(s.sl.level, 2) if s.sl else None,
                                   atr=round(s.atr, 3) if s.atr else None,
                                   last_event=(f"{s.last().kind} {'UP' if s.last().direction == 1 else 'DN'} @ "
                                               f"{s.last().level:.2f}") if s.last() else None)
                          for tf, s in st.items() if s is not None}
    try:
        import smc_engine
        snap["smc"] = smc_engine.summary(getattr(hub, "smc", None))
    except Exception as e:
        snap["smc"] = dict(ok=False, reason=str(e))
    try:
        import forecast
        t_end = (src.bars("M5")[-1].t if src.bars("M5") else int(time.time()))
        snap["prev_day_volume_profile"] = forecast._vp_at(hub, t_end)
        fs = getattr(hub, "forecast", None)
        if fs is not None:
            snap["forecasts_active"] = [
                {k: f.get(k) for k in ("id", "setup_type", "direction", "status", "entry_type", "entry", "sl", "tp2",
                                       "confidence", "setup_quality")} for f in list(fs.engine.active.values())]
    except Exception as e:
        snap["prev_day_volume_profile"] = None
        snap["forecast_error"] = str(e)
    try:
        nw = getattr(hub, "news", None)
        snap["news"] = nw.news_state() if nw and hasattr(nw, "news_state") else "n/a"
    except Exception:
        snap["news"] = "n/a"
    snap["candles"] = {"H4": _bars(src, "H4", c["bars_h4"]), "H1": _bars(src, "H1", c["bars_h1"]),
                       "M15": _bars(src, "M15", c["bars_m15"]), "M5": _bars(src, "M5", c["bars_m5"])}
    snap["candles_format"] = "[time (server, unix), open, high, low, close], oldest first, closed bars only"
    return snap


def build_prompt(snap, mode, question, cfg):
    r = snap.get("read") or {}
    task = TASKS[mode].format(symbol=snap.get("symbol"), overall=r.get("overall"), passing=r.get("passing"),
                              question=(question or "").strip()[:2000])
    body = f"{task}\n\nSNAPSHOT (JSON):\n{json.dumps(snap, separators=(',', ':'), default=str)}"
    return SYSTEM.format(language=cfg["language"]), body


# ============================================================================ API call
def call_api(cfg, system, user, timeout=120, opener=None):
    """One Messages API request -> (text, usage). Raises RuntimeError with a readable message."""
    if not cfg.get("api_key"):
        raise RuntimeError("Walang API key. Ilagay sa AI Analyst -> Settings.")
    data = json.dumps(dict(model=cfg["model"], max_tokens=cfg["max_tokens"], system=system,
                           messages=[dict(role="user", content=user)])).encode()
    req = urllib.request.Request(API_URL, data=data, method="POST", headers={
        "content-type": "application/json", "x-api-key": cfg["api_key"], "anthropic-version": API_VERSION})
    try:
        with (opener or urllib.request.urlopen)(req, timeout=timeout) as resp:
            out = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode()).get("error", {}).get("message", "")
        except Exception:
            msg = ""
        raise RuntimeError(f"API error {e.code}: {msg or e.reason}") from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"Hindi maabot ang api.anthropic.com: {e.reason}") from None
    text = "".join(b.get("text", "") for b in out.get("content", []) if b.get("type") == "text").strip()
    if not text:
        raise RuntimeError(f"Walang sagot (stop_reason {out.get('stop_reason')})")
    return text, out.get("usage", {})


# ============================================================================ service
class AIAnalyst:
    def __init__(self, hub, settings_path=None, journal_path=None, opener=None):
        self.hub = hub
        self.settings_path = settings_path or SETTINGS_FILE
        self.journal_path = journal_path or JOURNAL_FILE
        self.settings = load_settings(self.settings_path)
        self.opener = opener                             # tests inject a fake HTTP opener
        self.lock = threading.Lock()
        self.running = False
        self.last = None
        self.last_call = 0.0
        self.history = self._load()

    def _load(self):
        out = []
        try:
            with open(self.journal_path) as f:
                for line in f:
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        continue
        except OSError:
            pass
        return out[-50:]

    def update_settings(self, new):
        new = dict(new or {})
        if not new.get("api_key"):                       # empty = keep the stored key
            new.pop("api_key", None)
        if new.pop("clear_key", False):
            new["api_key"] = ""
        with self.lock:
            self.settings = clean_settings(dict(self.settings, **new))
            try:
                with open(self.settings_path, "w") as f:
                    json.dump(self.settings, f, indent=2)
                try:
                    os.chmod(self.settings_path, 0o600)
                except OSError:
                    pass
            except OSError as e:
                return dict(ok=False, error=str(e))
        return dict(ok=True, settings=public_settings(self.settings))

    def ask(self, mode="analysis", question="", wait=False):
        """Start one request in the background (or run it now when wait=True, for tests)."""
        if mode not in MODES:
            return dict(ok=False, error="unknown mode")
        if mode == "question" and not (question or "").strip():
            return dict(ok=False, error="Walang tanong.")
        with self.lock:
            if self.running:
                return dict(ok=False, error="May tumatakbo pang request.")
            wait_s = self.settings["min_interval_s"] - (time.time() - self.last_call)
            if wait_s > 0:
                return dict(ok=False, error=f"Maghintay ng {wait_s:.0f}s (cost guard).")
            if not self.settings.get("api_key"):
                return dict(ok=False, error="Walang API key. Ilagay sa Settings.")
            self.running = True
            self.last_call = time.time()
        try:
            with self.hub.lock:
                snap = snapshot(self.hub, self.settings)
        except Exception as e:
            with self.lock:
                self.running = False
            return dict(ok=False, error=f"snapshot: {e}")
        if wait:
            self._run(mode, question, snap)
            return dict(ok=True, result=self.last)
        threading.Thread(target=self._run, args=(mode, question, snap), daemon=True).start()
        return dict(ok=True, started=True)

    def _run(self, mode, question, snap):
        cfg = dict(self.settings)
        t0 = time.time()
        rec = dict(t=int(t0), mode=mode, question=(question or "")[:500], model=cfg["model"],
                   symbol=snap.get("symbol"), score=(snap.get("read") or {}).get("overall"),
                   bias=(snap.get("read") or {}).get("bias"))
        try:
            system, user = build_prompt(snap, mode, question, cfg)
            text, usage = call_api(cfg, system, user, opener=self.opener)
            rec.update(ok=True, answer=text, usage=usage, seconds=round(time.time() - t0, 1))
        except Exception as e:
            rec.update(ok=False, error=str(e), seconds=round(time.time() - t0, 1))
            try:
                from error_log import report
                report("ALERT_ERROR", "ai_analyst", "AI_REQUEST_FAILED", e)
            except Exception:
                pass
        with self.lock:
            self.last = rec
            self.history = (self.history + [rec])[-50:]
            self.running = False
            try:
                with open(self.journal_path, "a") as f:
                    f.write(json.dumps(rec) + "\n")
            except OSError:
                pass

    def state(self):
        with self.lock:
            return dict(settings=public_settings(self.settings), running=self.running, last=self.last,
                        history=list(reversed(self.history))[:15], models=list(MODELS), read_only=True)
