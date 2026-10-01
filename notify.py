"""
Telegram alerts for REYGUN SuperChart.

Setup (once):
  1. In Telegram open @BotFather -> /newbot -> copy the BOT TOKEN.
  2. Open your new bot and press START (or add it to a group).
  3. Paste the token in the terminal (System page -> Telegram), press "Find my chat id", then "Send test".

What it sends (each one can be switched off):
  signals  new Analyser Signals / Conso Breakout arrow on a bar that just closed (entry, SL, TP, score, cautions)
  trades   trade opened (with a chart picture) and closed (reason, P/L per account, R, picture)
  risk     daily DD / TP lock hit on an account
  news     high-impact news entering the warning window
  health   watchdog: MT5 disconnected, Algo Trading off, no data, follower down ... and when it recovers
  drift    live results worse than the backtest said (drift monitor)
  eod      end-of-day report at the hour you choose

Nothing here places or changes orders. Sending runs in a background queue, so a slow / blocked internet never
freezes the terminal. The bot token is saved in telegram_settings.json on this PC / VPS only: keep it private.
"""
from __future__ import annotations

import json
import os
import queue
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import deque

HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(HERE, "telegram_settings.json")
API_BASE = os.environ.get("REYGUN_TG_API", "https://api.telegram.org")      # tests point this at a fake server

EVENTS = ("signals", "ready", "blocked", "trades", "risk", "news", "health", "drift", "eod")
DEFAULTS = dict(
    enabled=False,
    token="",
    chat_id="",
    events={k: True for k in EVENTS},
    pictures=True,                    # chart picture with trade open / close
    signal_tf="M5",
    signal_sources=["analyser"],      # analyser | breakout
    signal_min_score=0.0,
    eod_hour=23,                      # local hour (tz_offset) for the end-of-day report; -1 = off
    tz_offset=8,                      # Manila
    silent_hours=[],                  # e.g. [1,2,3,4,5]: sent without sound
)


def load_settings():
    s = json.loads(json.dumps(DEFAULTS))
    try:
        with open(SETTINGS_FILE) as f:
            u = json.load(f)
        for k, v in u.items():
            if k == "events" and isinstance(v, dict):
                s["events"].update({e: bool(v.get(e, s["events"].get(e, True))) for e in EVENTS})
            elif k in s:
                s[k] = v
    except (OSError, ValueError):
        pass
    return s


def _mask(tok):
    return (tok[:6] + "…" + tok[-4:]) if len(tok) > 12 else ("set" if tok else "")


class Notifier:
    def __init__(self, hub):
        self.hub = hub
        self.settings = load_settings()
        self.q = queue.Queue(maxsize=200)
        self.log = deque(maxlen=40)
        self.last_error = ""
        self.sent = 0
        self.running = True
        self.search_msg = ""
        threading.Thread(target=self._sender, daemon=True).start()
        threading.Thread(target=self._auto_chat, daemon=True).start()

    # ------------------------------------------------------------------ settings
    def update_settings(self, new):
        s = self.settings
        new_token = False
        if "token" in new and new["token"] and "…" not in str(new["token"]):
            tok = str(new["token"]).strip()
            new_token = tok != s["token"]
            s["token"] = tok
        if "chat_id" in new:
            s["chat_id"] = str(new["chat_id"]).strip()
        if "enabled" in new:
            s["enabled"] = bool(new["enabled"])
        if new_token:
            s["enabled"] = True                   # a fresh token switches the alerts on (you can still turn them off)
            self.search_msg = ""
        if "pictures" in new:
            s["pictures"] = bool(new["pictures"])
        if isinstance(new.get("events"), dict):
            for e in EVENTS:
                if e in new["events"]:
                    s["events"][e] = bool(new["events"][e])
        if new.get("signal_tf") in ("M1", "M5", "M15", "M30", "H1", "H4"):
            s["signal_tf"] = new["signal_tf"]
        if isinstance(new.get("signal_sources"), list):
            s["signal_sources"] = [x for x in new["signal_sources"] if x in ("analyser", "breakout", "trendline", "reversal")]
        for k, lo, hi in (("eod_hour", -1, 23), ("tz_offset", -12, 14)):
            if k in new:
                try:
                    s[k] = max(lo, min(hi, int(float(new[k]))))
                except (TypeError, ValueError):
                    pass
        if "signal_min_score" in new:
            try:
                s["signal_min_score"] = max(0.0, min(100.0, float(new["signal_min_score"])))
            except (TypeError, ValueError):
                pass
        if isinstance(new.get("silent_hours"), list):
            s["silent_hours"] = sorted({int(h) for h in new["silent_hours"] if str(h).isdigit() and 0 <= int(h) <= 23})
        try:
            with open(SETTINGS_FILE, "w") as f:
                json.dump(s, f, indent=2)
        except OSError:
            pass
        return dict(ok=True, state=self.state())

    def ready(self):
        s = self.settings
        return bool(s["enabled"] and s["token"] and s["chat_id"])

    def local_hour(self):
        return time.gmtime(time.time() + self.settings["tz_offset"] * 3600).tm_hour

    # ------------------------------------------------------------------ public
    def send(self, event, text, image=None, force=False):
        """Queue a message. event: one of EVENTS (or 'test'). image: PNG bytes (optional)."""
        s = self.settings
        if not force:
            if not self.ready() or not s["events"].get(event, False):
                return False
        elif not (s["token"] and s["chat_id"]):
            return False
        if image is not None and not s["pictures"] and event != "test":
            image = None
        try:
            self.q.put_nowait(dict(event=event, text=text, image=image, t=time.time()))
            return True
        except queue.Full:
            self.last_error = "queue full (internet down?)"
            return False

    def test(self):
        if not self.settings["token"]:
            return dict(ok=False, error="walang bot token")
        if not self.settings["chat_id"]:
            return dict(ok=False, error="walang chat id: pindutin ang 'Find my chat id' pagkatapos mag-START sa bot")
        r = self._call("sendMessage", dict(chat_id=self.settings["chat_id"],
                                           text="✅ REYGUN SuperChart: konektado ang Telegram alerts.",
                                           parse_mode="HTML"))
        return dict(ok=r.get("ok", False), error=r.get("description") or r.get("error", ""))

    def find_chat(self):
        """Newest chat that messaged the bot (press START in Telegram first)."""
        if not self.settings["token"]:
            return dict(ok=False, error="walang bot token")
        r = self._call("getUpdates", dict(limit=50, timeout=0))
        if not r.get("ok"):
            return dict(ok=False, error=r.get("description") or r.get("error", "failed"))
        chats = []
        for u in r.get("result", []):
            m = u.get("message") or u.get("channel_post") or u.get("my_chat_member") or {}
            c = m.get("chat") or {}
            if c.get("id") is not None:
                name = c.get("title") or " ".join(x for x in (c.get("first_name"), c.get("last_name")) if x) or c.get("username", "")
                chats.append(dict(id=str(c["id"]), name=name, type=c.get("type", "")))
        uniq = list({c["id"]: c for c in chats}.values())
        if not uniq:
            return dict(ok=False, error="walang nakita: buksan ang bot sa Telegram, pindutin ang START o mag-type ng kahit ano, tapos subukan ulit")
        return dict(ok=True, chats=uniq[::-1])

    def _auto_chat(self):
        """Token saved but no chat id yet: keep asking the bot who wrote to it; the first chat found is used and a
        welcome message is sent. So the only setup is: paste the token, then say hi to your bot."""
        while self.running:
            s = self.settings
            if s["token"] and not s["chat_id"]:
                r = self.find_chat()
                if r.get("ok") and r["chats"]:
                    c = r["chats"][0]
                    self.update_settings(dict(chat_id=c["id"]))
                    self.search_msg = f"nakita: {c['name']} ({c['id']})"
                    self.hub.note("info", f"Telegram: chat found ({c['name']}), alerts ON")
                    self.send("test", "✅ <b>REYGUN SuperChart</b> konektado na!\nDito darating ang mga alert: "
                                      "signals, trades, DD lock, news, health at daily report.", force=True)
                else:
                    err = r.get("error", "")
                    self.search_msg = ("i-message ang bot mo sa Telegram (START o 'hi')" if "walang nakita" in err
                                       else err)
            time.sleep(8)

    def state(self):
        s = dict(self.settings)
        s["token"] = _mask(s["token"])
        return dict(settings=s, ready=self.ready(), sent=self.sent, last_error=self.last_error,
                    searching=bool(self.settings["token"] and not self.settings["chat_id"]), search_msg=self.search_msg,
                    queued=self.q.qsize(), log=list(self.log)[:20])

    # ------------------------------------------------------------------ transport
    def _call(self, method, fields, image=None, timeout=20):
        url = f"{API_BASE}/bot{self.settings['token']}/{method}"
        try:
            if image is None:
                data = urllib.parse.urlencode(fields).encode()
                req = urllib.request.Request(url, data=data)
            else:
                boundary = uuid.uuid4().hex
                body = b""
                for k, v in fields.items():
                    body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n").encode()
                body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"chart.png\"\r\n"
                         f"Content-Type: image/png\r\n\r\n").encode() + image + f"\r\n--{boundary}--\r\n".encode()
                req = urllib.request.Request(url, data=body,
                                             headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            try:
                return json.loads(e.read().decode())
            except Exception:
                return dict(ok=False, error=f"HTTP {e.code}")
        except Exception as e:
            return dict(ok=False, error=f"{e.__class__.__name__}: {e}")

    def _sender(self):
        while self.running:
            try:
                m = self.q.get(timeout=1)
            except queue.Empty:
                continue
            s = self.settings
            silent = self.local_hour() in (s.get("silent_hours") or [])
            text = m["text"]
            ok, err = False, ""
            for attempt in range(3):
                if m["image"] is not None:
                    r = self._call("sendPhoto", dict(chat_id=s["chat_id"], caption=text[:1020], parse_mode="HTML",
                                                     disable_notification=str(silent).lower()), m["image"])
                    if not r.get("ok") and len(text) > 1020:
                        r = self._call("sendMessage", dict(chat_id=s["chat_id"], text=text, parse_mode="HTML",
                                                           disable_notification=str(silent).lower()))
                else:
                    r = self._call("sendMessage", dict(chat_id=s["chat_id"], text=text[:4000], parse_mode="HTML",
                                                       disable_notification=str(silent).lower()))
                ok = bool(r.get("ok"))
                err = r.get("description") or r.get("error", "")
                if ok:
                    break
                ra = ((r.get("parameters") or {}).get("retry_after"))
                time.sleep(float(ra) if ra else 2 + attempt * 3)
            self.sent += ok
            self.last_error = "" if ok else err
            first = text.split("\n", 1)[0]
            import re
            self.log.appendleft(dict(t=time.time(), event=m["event"], ok=ok, text=re.sub(r"<[^>]+>", "", first)[:120],
                                     error=err if not ok else "", pic=m["image"] is not None))
            if not ok:
                self.hub.note("error", f"Telegram: {err}")
            time.sleep(0.4)                           # stay far below Telegram's rate limit

    def stop(self):
        self.running = False
