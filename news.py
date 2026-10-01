"""
News reader for SuperChart.

  * Economic calendar: ForexFactory weekly JSON (title, currency, time, impact, forecast, previous).
  * Headlines: RSS feeds (default FXStreet news; more feeds can be added on the News page). Headlines that contain
    gold-moving keywords (gold, XAU, Fed, CPI, NFP, ...) are flagged.
  * Everything is fetched in a background thread and cached to news_cache.json, so the last data
    shows immediately on the next start, even offline.
Settings in news_settings.json (currencies, impacts, warning window, feeds, keywords).
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime

HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(HERE, "news_settings.json")
CACHE_FILE = os.path.join(HERE, "news_cache.json")

DEFAULTS = {
    "calendar_url": "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
    "rss": [
        {"name": "FXStreet", "url": "https://www.fxstreet.com/rss/news"},
    ],
    "currencies": ["USD"],                    # events that move gold the most
    "impacts": ["High", "Medium"],            # shown in the calendar ("Low", "Holiday" also possible)
    "warn_impacts": ["High"],                 # these trigger the header countdown / trade warning / voice
    "warn_minutes": 30,                       # warn this many minutes before ...
    "after_minutes": 10,                      # ... and until this many minutes after the release
    "keywords": ["gold", "xau", "bullion", "fed", "fomc", "powell", "cpi", "pce", "inflation", "nfp",
                 "payroll", "jobs", "unemployment", "rate cut", "rate hike", "yields", "treasury", "dollar",
                 "dxy", "gdp", "ppi", "retail sales", "tariff", "war", "geopolit", "safe haven"],
    "calendar_refresh_min": 15,
    "rss_refresh_min": 3,
}

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) REYGUN-SuperChart/1.0"


def load_settings() -> dict:
    s = json.loads(json.dumps(DEFAULTS))
    try:
        with open(SETTINGS_FILE) as f:
            user = json.load(f)
        for k, v in user.items():
            if k in s:
                s[k] = v
    except (OSError, ValueError):
        pass
    return s


def save_settings(new: dict) -> dict:
    s = load_settings()
    for k in ("currencies", "impacts", "warn_impacts", "keywords"):
        if isinstance(new.get(k), list):
            s[k] = [str(x).strip() for x in new[k] if str(x).strip()]
    for k in ("warn_minutes", "after_minutes"):
        if k in new:
            try:
                s[k] = max(0, min(240, int(new[k])))
            except (TypeError, ValueError):
                pass
    if isinstance(new.get("rss"), list):
        s["rss"] = [dict(name=str(x.get("name", "feed")), url=str(x.get("url", ""))) for x in new["rss"] if x.get("url")]
    with open(SETTINGS_FILE, "w") as f:
        json.dump(s, f, indent=2)
    return s


def _get(url, timeout=12) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def parse_calendar(raw: bytes) -> list[dict]:
    out = []
    for e in json.loads(raw.decode("utf-8", "replace")):
        try:
            t = datetime.fromisoformat(e["date"]).timestamp()
        except (KeyError, ValueError):
            continue
        out.append(dict(t=int(t), title=e.get("title", ""), currency=e.get("country", ""),
                        impact=e.get("impact", ""), forecast=e.get("forecast", ""), previous=e.get("previous", ""),
                        actual=e.get("actual", "")))
    out.sort(key=lambda x: x["t"])
    return out


def _text(el, tag):
    x = el.find(tag)
    return (x.text or "").strip() if x is not None and x.text else ""


def parse_rss(raw: bytes, source: str) -> list[dict]:
    root = ET.fromstring(raw)
    items = root.findall(".//item")
    if not items:                                           # Atom
        ns = {"a": "http://www.w3.org/2005/Atom"}
        items = root.findall(".//a:entry", ns)
        out = []
        for it in items:
            link = it.find("a:link", ns)
            t = _text(it, "{http://www.w3.org/2005/Atom}updated")
            try:
                ts = datetime.fromisoformat(t.replace("Z", "+00:00")).timestamp()
            except ValueError:
                ts = time.time()
            out.append(dict(title=_text(it, "{http://www.w3.org/2005/Atom}title"),
                            link=link.get("href") if link is not None else "", t=int(ts), source=source))
        return out
    out = []
    for it in items:
        pd = _text(it, "pubDate")
        try:
            ts = parsedate_to_datetime(pd).timestamp()
        except (TypeError, ValueError):
            ts = time.time()
        title = re.sub(r"\s+", " ", _text(it, "title"))
        if title:
            out.append(dict(title=title, link=_text(it, "link"), t=int(ts), source=source))
    return out


class NewsFeed:
    def __init__(self, note=None):
        self.note = note or (lambda *a, **k: None)
        self.settings = load_settings()
        self.lock = threading.Lock()
        self.calendar: list[dict] = []
        self.headlines: list[dict] = []
        self.errors: dict[str, str] = {}
        self.updated = {"calendar": 0, "rss": 0}
        self._next = {"calendar": 0, "rss": 0}
        self._load_cache()
        self.running = True
        threading.Thread(target=self._loop, daemon=True).start()

    # ------------------------------------------------------------------ cache
    def _load_cache(self):
        try:
            with open(CACHE_FILE) as f:
                c = json.load(f)
            self.calendar = c.get("calendar", [])
            self.headlines = c.get("headlines", [])
            self.updated = c.get("updated", self.updated)
        except (OSError, ValueError):
            pass

    def _save_cache(self):
        try:
            with open(CACHE_FILE, "w") as f:
                json.dump(dict(calendar=self.calendar, headlines=self.headlines[:200], updated=self.updated), f)
        except OSError:
            pass

    # ------------------------------------------------------------------ fetching
    def refresh(self, force=False):
        now = time.time()
        s = self.settings
        if force or now >= self._next["calendar"]:
            self._next["calendar"] = now + s["calendar_refresh_min"] * 60
            try:
                cal = parse_calendar(_get(s["calendar_url"]))
                with self.lock:
                    self.calendar = cal
                    self.updated["calendar"] = int(now)
                    self.errors.pop("calendar", None)
            except Exception as e:
                self.errors["calendar"] = f"{e.__class__.__name__}: {e}"
                self._next["calendar"] = now + 120          # retry sooner after an error
        if force or now >= self._next["rss"]:
            self._next["rss"] = now + s["rss_refresh_min"] * 60
            got = []
            for feed in s["rss"]:
                try:
                    got += parse_rss(_get(feed["url"]), feed.get("name", "feed"))
                    self.errors.pop(feed["url"], None)
                except Exception as e:
                    self.errors[feed["url"]] = f"{feed.get('name')}: {e.__class__.__name__}: {e}"
            if got:
                kws = [k.lower() for k in s["keywords"]]
                with self.lock:
                    seen = {h["link"] or h["title"] for h in self.headlines}
                    new = []
                    for h in got:
                        key = h["link"] or h["title"]
                        if key in seen:
                            continue
                        seen.add(key)
                        low = h["title"].lower()
                        h["hits"] = [k for k in kws if re.search(r"\b" + re.escape(k), low)]
                        h["first_seen"] = int(now)
                        new.append(h)
                    self.headlines = sorted(self.headlines + new, key=lambda x: x["t"], reverse=True)[:200]
                    self.updated["rss"] = int(now)
        self._save_cache()

    def _loop(self):
        while self.running:
            try:
                self.refresh()
            except Exception as e:
                self.errors["loop"] = str(e)
            time.sleep(20)

    # ------------------------------------------------------------------ views
    def upcoming_warning(self, now=None):
        """The next/ongoing warn-level event for the chosen currencies, with minutes until it."""
        now = now or time.time()
        s = self.settings
        cur, imp = set(s["currencies"]), set(s["warn_impacts"])
        with self.lock:
            for e in self.calendar:
                if e["currency"] in cur and e["impact"] in imp and e["t"] >= now - s["after_minutes"] * 60:
                    mins = (e["t"] - now) / 60
                    return dict(e, minutes=round(mins, 1),
                                active=-s["after_minutes"] <= mins <= s["warn_minutes"])
        return None

    def news_state(self, now=None, max_age_h=12.0):
        """NEWS_UNKNOWN (no usable calendar: never loaded, or older than max_age_h), NEWS_HIGH_IMPACT (inside the
        warning window) or NEWS_CLEAR. Unavailable data is never reported as CLEAR."""
        now = now or time.time()
        upd = (self.updated or {}).get("calendar") or 0
        if not upd or now - upd > max_age_h * 3600:
            why = self.errors.get("calendar") or ("calendar never loaded" if not upd else
                                                  f"calendar is {(now - upd) / 3600:.0f} h old")
            return dict(state="NEWS_UNKNOWN", text=why, warning=None)
        w = self.upcoming_warning(now)
        if w and w.get("active"):
            return dict(state="NEWS_HIGH_IMPACT", text=f"{w['currency']} {w['title']} ({w['minutes']:+.0f} min)", warning=w)
        return dict(state="NEWS_CLEAR", text="no high-impact event in the window", warning=w)

    def view(self):
        s = self.settings
        now = time.time()
        with self.lock:
            cal = [e for e in self.calendar if e["t"] >= now - 36 * 3600]     # from yesterday on
            heads = self.headlines[:80]
        return dict(settings=s, calendar=cal, headlines=heads, updated=self.updated, errors=self.errors,
                    warning=self.upcoming_warning(now), now=int(now), state=self.news_state(now))

    def update_settings(self, new):
        self.settings = save_settings(new)
        self._next = {"calendar": 0, "rss": 0}
        return dict(ok=True, settings=self.settings)

    def stop(self):
        self.running = False
