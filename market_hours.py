"""Shared session / market-hours helpers (one place, used by the chart, decision engine, watchdog, analytics)."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
SESSIONS_FILE = os.path.join(HERE, "session_settings.json")
# Session hours in UTC (never broker time, never local PC time). Broker bar times are converted with the
# detected server offset (utc_from_server) before they are compared with these hours.
DEFAULT_SESSIONS = {"Asia": [0, 9], "London": [7, 16], "New York": [12, 21]}


def _clean_sessions(raw):
    out = {}
    for k, default in DEFAULT_SESSIONS.items():
        v = (raw or {}).get(k, default)
        try:
            a, b = int(v[0]), int(v[1])
            if not (0 <= a <= 23 and 1 <= b <= 24 and a != b):
                raise ValueError
            out[k] = [a, b]
        except (TypeError, ValueError, IndexError):
            out[k] = list(default)
    return out


def load_sessions():
    try:
        with open(SESSIONS_FILE) as f:
            return _clean_sessions(json.load(f))
    except (OSError, ValueError):
        return _clean_sessions(None)


SESSIONS = load_sessions()        # one table for the chart header, decision engine, auto trade and backtest


def save_sessions(new):
    global SESSIONS
    SESSIONS = _clean_sessions(dict(SESSIONS, **(new or {})))
    try:
        with open(SESSIONS_FILE, "w") as f:
            json.dump(SESSIONS, f, indent=2)
    except OSError:
        pass
    return SESSIONS


def in_session(name, hour_utc):
    a, b = SESSIONS.get(name, DEFAULT_SESSIONS.get(name, [0, 0]))
    return a <= hour_utc < b if a < b else (hour_utc >= a or hour_utc < b)


def session_name(now_utc: datetime) -> str:
    h = now_utc.hour
    if now_utc.weekday() >= 5:
        return "Weekend"
    london = in_session("London", h)
    ny = in_session("New York", h)
    asia = in_session("Asia", h)
    if london and ny:
        return "London / New York overlap"
    if london:
        return "London session"
    if ny:
        return "New York session"
    if asia:
        return "Asia session"
    return "Off-session"


def session_code(now_utc: datetime) -> str:
    """Short code for analytics / fingerprints: LONDON, NEW_YORK, OVERLAP, ASIA, OFF, WEEKEND."""
    n = session_name(now_utc)
    return {"London / New York overlap": "OVERLAP", "London session": "LONDON", "New York session": "NEW_YORK",
            "Asia session": "ASIA", "Off-session": "OFF", "Weekend": "WEEKEND"}[n]


def utc_from_server(t_server: int, utc_offset_h: int) -> datetime:
    return datetime.fromtimestamp(int(t_server) - int(utc_offset_h or 0) * 3600, timezone.utc)


def gold_open(now=None):
    """Rough XAUUSD trading hours in UTC: Sun 22:00 -> Fri 21:00, daily break ~21:00-22:05."""
    d = now or datetime.now(timezone.utc)
    wd, hm = d.weekday(), d.hour * 60 + d.minute
    if wd == 5 or (wd == 4 and hm >= 21 * 60) or (wd == 6 and hm < 22 * 60 + 5):
        return False
    if 21 * 60 <= hm < 22 * 60 + 5:
        return False
    return True
