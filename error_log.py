"""
Categorized error log (one place for every failure). Nothing is swallowed silently: components call
ERRORS.add(...) (or Hub.note("error", ...), which is routed here) and the error is kept in memory, written to
errors.jsonl and shown on the System page.

Categories: DATA_ERROR, PLUGIN_ERROR, SIGNAL_ERROR, DECISION_ERROR, RISK_ERROR, EXECUTION_ERROR, BROKER_ERROR,
STATE_ERROR (+ CONFIG_ERROR, ALERT_ERROR, OTHER_ERROR).
Every entry: timestamp, component, category, error_code, message, context. Secrets (Telegram bot tokens,
passwords, api keys, tokens in URLs) are redacted before anything is stored.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from collections import deque

HERE = os.path.dirname(os.path.abspath(__file__))
CATEGORIES = ("DATA_ERROR", "PLUGIN_ERROR", "SIGNAL_ERROR", "DECISION_ERROR", "RISK_ERROR", "EXECUTION_ERROR",
              "BROKER_ERROR", "STATE_ERROR", "CONFIG_ERROR", "ALERT_ERROR", "OTHER_ERROR")

_SECRET_PATTERNS = [
    (re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{25,}\b"), "<telegram-token>"),                 # bot123:ABC...
    (re.compile(r"(?i)(password|passwd|pwd|api[_-]?key|secret|token)(\s*[=:]\s*|\"\s*:\s*\")([^\s\"',&]+)"),
     r"\1\2<redacted>"),
    (re.compile(r"(?i)(/bot)[^/\s]+"), r"\1<redacted>"),
    (re.compile(r"sk-ant-[A-Za-z0-9_-]{8,}"), "<anthropic-key>"),                          # Anthropic API key                                    # api.telegram.org/bot<token>
]

# first matching keyword -> category (for errors reported through Hub.note("error", text))
_KEYWORDS = [
    ("plugin", "PLUGIN_ERROR"), ("decision", "DECISION_ERROR"), ("gate", "DECISION_ERROR"),
    ("risk", "RISK_ERROR"), ("lock", "RISK_ERROR"), ("order", "EXECUTION_ERROR"), ("close failed", "EXECUTION_ERROR"),
    ("sl/tp", "EXECUTION_ERROR"), ("modify", "EXECUTION_ERROR"), ("auto trade", "EXECUTION_ERROR"),
    ("reconcil", "STATE_ERROR"), ("journal", "STATE_ERROR"), ("groups", "STATE_ERROR"),
    ("mt5", "BROKER_ERROR"), ("master", "BROKER_ERROR"), ("follower", "BROKER_ERROR"), ("connect", "BROKER_ERROR"),
    ("data", "DATA_ERROR"), ("bars", "DATA_ERROR"), ("tick", "DATA_ERROR"), ("history", "DATA_ERROR"),
    ("signal", "SIGNAL_ERROR"), ("telegram", "ALERT_ERROR"), ("alert", "ALERT_ERROR"), ("settings", "CONFIG_ERROR"),
]


def redact(text):
    s = str(text)
    for rx, rep in _SECRET_PATTERNS:
        s = rx.sub(rep, s)
    return s


def _clean_ctx(ctx):
    if not ctx:
        return {}
    try:
        return json.loads(redact(json.dumps(ctx, default=str)))
    except Exception:
        return {"context": redact(str(ctx))}


def categorize(text):
    low = str(text).lower()
    for k, cat in _KEYWORDS:
        if k in low:
            return cat
    return "OTHER_ERROR"


class ErrorLog:
    def __init__(self, path=None, maxlen=300):
        self.path = path
        self.items = deque(maxlen=maxlen)
        self.counts = {}
        self.lock = threading.Lock()

    def add(self, category, component, code, message, context=None):
        category = category if category in CATEGORIES else "OTHER_ERROR"
        e = dict(t=time.time(), category=category, component=str(component), code=str(code),
                 message=redact(message), context=_clean_ctx(context))
        with self.lock:
            self.items.appendleft(e)
            self.counts[category] = self.counts.get(category, 0) + 1
            if self.path:
                try:
                    with open(self.path, "a", encoding="utf-8") as f:
                        f.write(json.dumps(e) + "\n")
                except OSError:
                    pass                    # the in-memory log still has it
        return e

    def from_note(self, text, **extra):
        comp = extra.get("component") or str(text).split(":")[0][:40]
        return self.add(extra.get("category") or categorize(text), comp, extra.get("code") or "ERROR", text,
                        {k: v for k, v in extra.items() if k not in ("component", "category", "code")})

    def view(self, n=100):
        with self.lock:
            return dict(items=list(self.items)[:n], counts=dict(self.counts), categories=CATEGORIES)


ERRORS = ErrorLog()
_LAST = {}


def where(e):
    """'IndexError: list index out of range (news_trader.py:231 in _cycle)' - the deepest frame of the exception,
    so an error in the log says WHERE it happened, not only what."""
    import traceback as _tb
    frames = _tb.extract_tb(e.__traceback__) if isinstance(e, BaseException) else []
    loc = f" ({os.path.basename(frames[-1].filename)}:{frames[-1].lineno} in {frames[-1].name})" if frames else ""
    return f"{e.__class__.__name__}: {e}{loc}" if isinstance(e, BaseException) else str(e)


def report(category, component, code, exc, every_s=60.0, context=None):
    """Record a handled exception without flooding the log (same component+code at most once per every_s).
    Use instead of a bare `except: pass` so no failure is silent."""
    key = (component, code)
    now = time.time()
    if now - _LAST.get(key, 0) < every_s:
        return None
    _LAST[key] = now
    msg = where(exc)
    return ERRORS.add(category, component, code, msg, context)


def attach_file(path):
    ERRORS.path = path
