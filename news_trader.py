"""
News Trader: consolidation breakout with pending stop orders around high-impact news.

When NEWS TRADE is ON **and** trading is ARMED:
  1. A few minutes before a high-impact event (currencies / impacts from the News settings) it looks for a
     CONSOLIDATION box on the chosen timeframe (conso.py). No box -> no trade for that event (logged).
  2. It places, on the master and on every enabled follower (each with its own lot, shifted by that broker's
     price difference):   BUY STOP  above the box high + buffer
                           SELL STOP below the box low  - buffer
     (grid: `levels` > 1 adds more stops every `step` further out).
     SL = the other side of the box (or a fixed $), capped at max_sl.   TP = SL distance x RR (or fixed $).
  3. OCO: as soon as one side fills on an account, every opposite-side pending on THAT account is cancelled.
  4. Pending orders still unfilled `cancel_after_min` minutes after the release are cancelled.
  5. A filled master order becomes a normal SuperChart trade group: journaled with its score + the news, and
     the followers follow the master's closes / SL-TP moves like any other SuperChart trade.

Safety: NEWS TRADE is always OFF when the app starts. Turning it OFF or DISARMING cancels every news pending
order. News pendings left over from a previous run are cancelled at start. Locked accounts are skipped.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid

import conso
import mt5_ops

HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(HERE, "news_trade_settings.json")
# Auto trading around news: enabled on Eugene's request (2026-09-29). The switch is still OFF at every start,
# needs ARM + a confirm, respects MASTER TRADING and the risk engine, and turns off on DISARM.
ALLOW_AUTO = True
TAG = mt5_ops.COMMENT_PREFIX + "N"          # comment of every news order: RGSC-N<event id><B|S><level>

DEFAULTS = dict(
    master_lot=0.01,            # followers use their own fixed lot from the Accounts page
    place_before_sec=120,       # place the pendings this many seconds before the release
    cancel_after_min=10,        # cancel what did not fill this many minutes after the release
    buffer_pips=5.0,            # entry this many pips beyond the box edge
    levels=1,                   # 1 = one buy stop + one sell stop; 2+ = grid of stops
    step=1.50,                  # $ between grid levels
    sl_mode="box",              # "box" = other side of the box, "fixed" = sl_fixed
    sl_fixed=5.0,
    max_sl=10.0,                # never risk more than this distance (box SL is capped)
    tp_mode="rr",               # "rr" = SL distance x tp_rr, "fixed" = tp_fixed
    tp_rr=2.0,
    tp_fixed=10.0,
    conso_tf="M5",
    # before the news: close every OPEN SuperChart trade (never other EAs / manual MT5 trades)
    close_before_news=True,
    close_before_sec=180,       # close this many seconds before the release (>= place_before_sec)
    # no consolidation box: "distance" = BUY STOP / SELL STOP at price +/- nobox_offset | "skip" = no trade
    nobox_mode="distance",
    nobox_offset=3.0,           # $ from the mid price to each stop when there is no box
    nobox_sl=5.0,               # $ SL of the no-box stops (TP from tp_mode)
    # consolidation box (conso.py): min 2 tops + 2 bottoms at the same level within tol_pips
    lookback=48, swing=2, min_touches=2, tol_pips=5.0, pip=0.10, min_bars=8, max_range=10.0, min_range=0.5,
)
CONSO_KEYS = ("lookback", "swing", "min_touches", "tol_pips", "pip", "min_bars", "max_range", "min_range")
NUM_KEYS = [k for k, v in DEFAULTS.items() if isinstance(v, (int, float)) and not isinstance(v, bool)]


def load_settings():
    s = dict(DEFAULTS)
    try:
        with open(SETTINGS_FILE) as f:
            for k, v in json.load(f).items():
                if k in s:
                    s[k] = v
    except (OSError, ValueError):
        pass
    return s


def clean_settings(new, old):
    s = dict(old)
    for k in NUM_KEYS:
        if k in new:
            try:
                v = float(new[k])
            except (TypeError, ValueError):
                continue
            s[k] = int(v) if isinstance(DEFAULTS[k], int) else v
    for k, opts in (("sl_mode", ("box", "fixed")), ("tp_mode", ("rr", "fixed")),
                    ("conso_tf", ("M1", "M5", "M15")), ("nobox_mode", ("distance", "skip"))):
        if new.get(k) in opts:
            s[k] = new[k]
    if "close_before_news" in new:
        s["close_before_news"] = new["close_before_news"] in (True, "true", "1", 1, "on", "yes")
    s["levels"] = max(1, min(5, int(s["levels"])))
    s["lookback"] = max(10, min(300, int(s["lookback"])))
    s["swing"] = max(1, min(5, int(s["swing"])))
    s["min_touches"] = max(2, min(6, int(s["min_touches"])))
    s["min_bars"] = max(4, min(200, int(s["min_bars"])))
    s["pip"] = max(0.00001, float(s["pip"]))
    s["place_before_sec"] = max(10, min(1800, int(s["place_before_sec"])))
    s["cancel_after_min"] = max(1, min(240, int(s["cancel_after_min"])))
    s["close_before_sec"] = max(s["place_before_sec"], min(3600, int(s["close_before_sec"])))
    s["nobox_offset"] = max(0.1, float(s["nobox_offset"]))
    s["nobox_sl"] = max(0.5, float(s["nobox_sl"]))
    for k in ("buffer_pips", "tol_pips", "step", "sl_fixed", "max_sl", "tp_rr", "tp_fixed", "master_lot", "max_range", "min_range"):
        s[k] = max(0.0, float(s[k]))
    return s


class NewsTrader:
    def __init__(self, tm):
        self.tm = tm
        self.hub = tm.hub
        self.enabled = False                 # ALWAYS off at start
        self.settings = load_settings()
        self.box = None                      # latest detection on the master chart
        self.plan = None                     # levels that would be placed now
        self.events = {}                     # event key -> state dict
        self.closed_for = set()              # events whose "close open trades" step already ran
        self.lock = threading.RLock()
        self._cleaned = False
        self.running = True
        threading.Thread(target=self._loop, daemon=True).start()

    # ------------------------------------------------------------------ public
    def set_enabled(self, on):
        on = bool(on)
        if on and not ALLOW_AUTO:
            return dict(ok=False, enabled=False, error="auto news trading is switched off in this version")
        with self.lock:
            if on and not self.tm.armed:
                return dict(ok=False, enabled=False, error="ARM trading first")
            self.enabled = on
            if not on:
                n = self.cancel_all("NEWS TRADE switched off")
                self.hub.note("info", f"NEWS TRADE OFF" + (f" · cancelled {n} pending order(s)" if n else ""))
            else:
                st = self.settings
                self.hub.note("warn", "NEWS TRADE ON · before high-impact news: "
                              + (f"close open SuperChart trades {st['close_before_sec']}s before, " if st["close_before_news"] else "")
                              + f"BUY STOP / SELL STOP {st['place_before_sec']}s before "
                              + ("(box, or price +/- $" + str(st["nobox_offset"]) + " when there is no box)"
                                 if st["nobox_mode"] == "distance" else "(only when there is a consolidation box)"))
        return dict(ok=True, enabled=self.enabled)

    def update_settings(self, new):
        with self.lock:
            self.settings = clean_settings(new or {}, self.settings)
            try:
                with open(SETTINGS_FILE, "w") as f:
                    json.dump(self.settings, f, indent=2)
            except OSError:
                pass
        return dict(ok=True, settings=self.settings)

    def cancel_all(self, why=""):
        """Cancel every news pending order on every account."""
        n = 0
        for a in self.tm.all_accounts():
            if not a.connected:
                continue
            os_ = a.backend.call("orders", None)
            if not isinstance(os_, list):
                continue
            for o in os_:
                if o["comment"].startswith(TAG):
                    r = a.backend.call("cancel_order", o["ticket"])
                    n += 1 if r.get("ok") else 0
        for st in self.events.values():
            if st["stage"] == "placed":
                st["stage"], st["note"] = "cancelled", why
        return n

    # ------------------------------------------------------------------ loop
    def _loop(self):
        while self.running:
            try:
                self._cycle()
            except Exception as e:
                from error_log import where
                import traceback
                self.hub.note("error", f"news trader: {where(e)}")
                traceback.print_exc()
            time.sleep(1.0)

    def _next_event(self):
        nf = getattr(self.hub, "news", None)
        if not nf:
            return None
        s = nf.settings
        cur, imp = set(s["currencies"]), set(s["warn_impacts"])
        now = time.time()
        with nf.lock:
            for e in nf.calendar:
                if e["currency"] in cur and e["impact"] in imp and e["t"] > now - self.settings["cancel_after_min"] * 60:
                    return e
        return None

    def _detect(self):
        s = self.settings
        with self.hub.lock:
            bars = list(self.hub.src.bars(s["conso_tf"]))
        if len(bars) < 2:
            return None
        # the forming bar is included: a box must still hold right now
        return conso.detect(bars, **{k: s[k] for k in CONSO_KEYS})

    def _nobox(self):
        """A pseudo box around the current mid price for the no-consolidation mode."""
        tk = self.hub.last_tick
        if not tk:
            return None
        mid = (tk.bid + tk.ask) / 2
        off = float(self.settings["nobox_offset"])
        return dict(ok=True, nobox=True, high=round(mid + off, 2), low=round(mid - off, 2), range=round(2 * off, 2),
                    tops=[], bottoms=[], why="no consolidation: stops at price +/- offset")

    def _levels(self, box, over=None):
        """Master prices for every order: list of (side, level, entry, sl, tp)."""
        s = dict(self.settings, **(over or {}))
        if box and box.get("nobox"):               # no-box mode: the offset IS the distance, fixed SL
            s = dict(s, buffer_pips=0.0, sl_mode="fixed", sl_fixed=s["nobox_sl"])
        out = []
        for side in (1, -1):
            for lv in range(int(s["levels"])):
                edge = box["high"] if side == 1 else box["low"]
                buf = s["buffer_pips"] * s["pip"]
                entry = edge + side * (buf + lv * s["step"])
                if s["sl_mode"] == "box":
                    other = box["low"] if side == 1 else box["high"]
                    sl_d = abs(entry - (other - side * buf))
                else:
                    sl_d = s["sl_fixed"]
                if s["max_sl"] > 0:
                    sl_d = min(sl_d, s["max_sl"])
                tp_d = sl_d * s["tp_rr"] if s["tp_mode"] == "rr" else s["tp_fixed"]
                out.append(dict(side=side, level=lv, entry=round(entry, 2), sl_d=round(sl_d, 2), tp_d=round(tp_d, 2),
                                sl=round(entry - side * sl_d, 2) if sl_d else 0.0,
                                tp=round(entry + side * tp_d, 2) if tp_d else 0.0))
        return out

    def _cycle(self):
        tm = self.tm
        if not self._cleaned and tm.master.connected:
            self._cleaned = True
            n = self.cancel_all("left over from a previous run")
            if n:
                self.hub.note("warn", f"NEWS TRADE: cancelled {n} news pending order(s) left from a previous run")
        box = self._detect()
        with self.lock:
            self.box = box
            if box and box.get("ok"):
                self.plan = self._levels(box)
            elif self.settings["nobox_mode"] == "distance":
                nb = self._nobox()
                self.plan = self._levels(nb) if nb else None
            else:
                self.plan = None
            ev = self._next_event()
            armed_ok, why = tm.can_trade()
            if self.enabled and not tm.armed:            # DISARM switches NEWS TRADE off (like AUTO TRADE)
                self.enabled = False
                n = self.cancel_all("DISARMED")
                self.hub.note("warn", "NEWS TRADE OFF: trading disarmed" + (f" · cancelled {n} pending" if n else ""))
            if not armed_ok and any(st["stage"] == "placed" for st in self.events.values()):
                n = self.cancel_all(f"cancelled: {why}")
                self.hub.note("warn", f"NEWS TRADE: {why} -> cancelled {n} pending order(s)")
            if ev:
                key = f"{ev['t']}|{ev['title']}"
                st = self.events.get(key)
                secs = ev["t"] - time.time()
                # 1) close every open SuperChart trade before the release (once per event)
                if self.enabled and armed_ok and self.settings["close_before_news"] and \
                        0 < secs <= self.settings["close_before_sec"] and key not in self.closed_for:
                    self.closed_for.add(key)
                    n = len(tm.groups)
                    if n:
                        tm.close_everything(source="news")
                        self.hub.note("warn", f"NEWS {ev['currency']} {ev['title']} in {int(secs)}s: closed {n} open "
                                              f"SuperChart trade(s) before the news")
                # 2) the pending orders
                if st is None and self.enabled and 0 < secs <= self.settings["place_before_sec"]:
                    st = self.events[key] = dict(key=key, eid=uuid.uuid4().hex[:4], title=ev["title"],
                                                 currency=ev["currency"], t=ev["t"], stage="new", note="",
                                                 orders={}, triggered={}, groups={})
                    use = box if (box and box.get("ok")) else \
                        (self._nobox() if self.settings["nobox_mode"] == "distance" else None)
                    risk_why = self._risk_block(use)
                    if not armed_ok:
                        st["stage"], st["note"] = "skipped", f"not placed: {why}"
                        self.hub.note("warn", f"NEWS TRADE {ev['currency']} {ev['title']}: {st['note']}")
                    elif risk_why:
                        st["stage"], st["note"] = "skipped", f"not placed: {risk_why}"
                        self.hub.note("warn", f"NEWS TRADE {ev['currency']} {ev['title']}: {st['note']}")
                    elif use is None:
                        st["stage"] = "skipped"
                        st["note"] = "no consolidation: " + (box or {}).get("why", "no data")
                        self.hub.note("info", f"NEWS TRADE {ev['currency']} {ev['title']}: {st['note']} -> no trade")
                    else:
                        self._place(st, use)
            for st in self.events.values():
                if st["stage"] == "placed":
                    self._watch(st)

    def _risk_block(self, box):
        """MASTER TRADING + the risk engine's hard limits (daily DD / target, total DD, config) before any order."""
        risk = getattr(self.hub, "risk", None)
        if risk is None:
            return ""
        if not getattr(risk, "master_enabled", True):
            return "MASTER TRADING is disabled"
        if box is None:
            return ""
        try:
            lv = self._levels(box)
            L = lv[0] if lv else None
            bl = risk.hard_blockers(L["side"] if L else 0, "news", float(self.settings["master_lot"]),
                                    L["sl_d"] if L else None, L["tp_d"] if L else None) if L else []
        except Exception as e:
            return f"risk check failed ({e.__class__.__name__})"
        # a straddle is both sides by design, and open trades were closed just before (close_before_news)
        skip = ("MAX_OPEN_REACHED", "OPPOSITE_POSITION", "EXPOSURE_LIMIT")
        hard = [b for b in bl if b.get("level", "hard") == "hard" and b.get("code") not in skip]
        return "; ".join(f"{b['code']}: {b.get('text', '')}" for b in hard[:2])

    def place_box(self, plan, master_lot, expire_at, label="CONSO"):
        """A CONSOLIDATION strategy straddle (AUTO TRADE, strategies.py): BUY STOP above / SELL STOP below the box
        on the master + followers, SL = the strategy's stop, TP = its RR; OCO and expiry like the news orders."""
        tm = self.tm
        ok, why = tm.can_trade()
        if not ok:
            return dict(ok=False, error=why)
        box = dict(ok=True, high=plan["box_high"], low=plan["box_low"], range=round(plan["box_high"] - plan["box_low"], 2))
        buf = plan["buy_stop"] - plan["box_high"]
        over = dict(buffer_pips=buf / (self.settings["pip"] or 0.1), levels=max(1, int(plan.get("levels", 1))),
                    step=float(plan.get("step", 1.5)), sl_mode="fixed", sl_fixed=plan["sl_dist"],
                    max_sl=0.0, tp_mode="fixed", tp_fixed=plan["tp_dist"])
        with self.lock:
            key = f"BOX|{plan['box_low']}|{plan['box_high']}|{int(expire_at)}"
            if key in self.events:
                return dict(ok=False, error="this box was already placed")
            st = self.events[key] = dict(key=key, eid=uuid.uuid4().hex[:4], title=label, currency="", t=time.time(),
                                         expire_at=float(expire_at), stage="new", note="", orders={}, triggered={},
                                         groups={}, kind="strategy")
            self._place(st, box, over, master_lot)
        return dict(ok=st["stage"] == "placed", eid=st["eid"], note=st["note"], error="; ".join(st.get("errors") or []))

    # ------------------------------------------------------------------ placing
    def _accounts(self, master_lot=None):
        tm = self.tm
        out = []
        if tm.master.connected and not tm.master.locked:
            out.append((tm.master, float(master_lot if master_lot is not None else self.settings["master_lot"])))
        for f in tm.followers:
            if f.connected and f.cfg.get("enabled") and not f.locked and f.cfg.get("lot") and \
                    (tm.paper or f.info.get("trade_allowed", True)):
                out.append((f, float(f.cfg["lot"])))
        return out

    def _place(self, st, box, over=None, master_lot=None):
        tm = self.tm
        levels = self._levels(box, over)
        tk = self.hub.last_tick
        m_mid = (tk.bid + tk.ask) / 2 if tk else None
        st["box"] = dict(high=box["high"], low=box["low"], range=box["range"], nobox=bool(box.get("nobox")))
        st["levels"] = levels
        placed, errors = 0, []

        def one(item):
            a, lot = item
            shift = 0.0
            if a is not tm.master and m_mid is not None:
                px = a.backend.call("price", a.symbol)
                if px.get("ok"):
                    shift = (px["bid"] + px["ask"]) / 2 - m_mid          # this broker's quote difference
            res = []
            for L in levels:
                c = f"{TAG}{st['eid']}{'B' if L['side'] == 1 else 'S'}{L['level']}"
                r = a.backend.call("place_pending", a.symbol, L["side"], lot, L["entry"] + shift,
                                   (L["sl"] + shift) if L["sl"] else 0.0, (L["tp"] + shift) if L["tp"] else 0.0, c)
                res.append((L, r))
            return a, res

        for a, res in tm.pool.map(one, self._accounts(master_lot)):
            lst = st["orders"].setdefault(a.id, [])
            for L, r in res:
                if r.get("ok"):
                    lst.append(dict(ticket=r["ticket"], side=L["side"], level=L["level"], price=r["price"],
                                    sl=r.get("sl"), tp=r.get("tp")))
                    placed += 1
                else:
                    errors.append(f"{a.name} {'BUY' if L['side'] == 1 else 'SELL'} STOP: {r.get('error')}")
        st["placed_at"] = time.time()
        st["stage"] = "placed" if placed else "failed"
        st["note"] = f"{placed} pending order(s) placed" + (f", {len(errors)} failed" if errors else "")
        st["errors"] = errors
        self.hub.note("warn", f"{'STRATEGY BOX' if st.get('kind') == 'strategy' else 'NEWS GRID'} {st['currency']} {st['title']}: "
                              f"{'no box, price +/- ' + str(self.settings['nobox_offset']) + ' ·' if box.get('nobox') else 'box'} "
                              f"{box['low']:.2f}-{box['high']:.2f} "
                              f"· {st['note']}")
        for e in errors:
            self.hub.note("error", f"NEWS GRID: {e}")

    # ------------------------------------------------------------------ OCO + expiry
    def _watch(self, st):
        tm = self.tm
        accts = {a.id: a for a in tm.all_accounts()}
        for acc_id, lst in st["orders"].items():
            a = accts.get(acc_id)
            if not a or not a.connected or not a.fresh or not a.orders_fresh:
                continue
            if a.orders_read_at <= st.get("placed_at", 0):
                continue                         # this account's lists were read before the orders existed
            pend = {o["ticket"] for o in (a.orders or [])}
            pos = {p["ticket"]: p for p in a.positions}
            for o in lst:
                if o.get("state"):
                    continue
                if o["ticket"] in pend:
                    continue
                # gone from pending: filled (a position with the same ticket or comment) or removed
                p = pos.get(o["ticket"]) or next((q for q in a.positions if q["comment"] ==
                                                  f"{TAG}{st['eid']}{'B' if o['side'] == 1 else 'S'}{o['level']}"), None)
                if p:
                    o["state"], o["pos"] = "filled", p["ticket"]
                    self._on_fill(st, a, o, p)
                else:
                    o["state"] = "gone"
        # expiry (news: minutes after the release; a strategy box: its own expire_at)
        if time.time() > (st.get("expire_at") or st["t"] + self.settings["cancel_after_min"] * 60):
            n = self._cancel_event(st, None)
            st["stage"] = "done"
            st["note"] += f" · expired, {n} unfilled cancelled"
            self.hub.note("info", f"NEWS TRADE {st['title']}: window over, cancelled {n} unfilled pending order(s)")
        elif all(o.get("state") for lst in st["orders"].values() for o in lst):
            st["stage"] = "done"

    def _cancel_event(self, st, only_acc=None, side=None):
        n = 0
        accts = {a.id: a for a in self.tm.all_accounts()}
        for acc_id, lst in st["orders"].items():
            if only_acc and acc_id != only_acc:
                continue
            a = accts.get(acc_id)
            if not a or not a.connected:
                continue
            for o in lst:
                if o.get("state") or (side is not None and o["side"] != side):
                    continue
                r = a.backend.call("cancel_order", o["ticket"])
                if r.get("ok"):
                    o["state"] = "cancelled"
                    n += 1
        return n

    def _on_fill(self, st, a, o, p):
        tm = self.tm
        word = "BUY" if o["side"] == 1 else "SELL"
        st["triggered"][a.id] = o["side"]
        n = self._cancel_event(st, a.id, side=-o["side"])            # OCO on this account
        self.hub.note("signal", f"NEWS {word} STOP filled on {a.name} @ {p['open']:.2f} · OCO cancelled {n} "
                                f"opposite order(s)", plugin="News Trader", dir=o["side"], tf="",
                      bar=time.strftime("%m-%d %H:%M"))
        key = f"{o['side']}:{o['level']}"
        with tm.lock:
            if a is tm.master:
                from trading import Group
                gid = f"N{st['eid']}{'B' if o['side'] == 1 else 'S'}{o['level']}"
                g = Group(gid, o["side"], time.time(), {"master": p["ticket"]}, p["volume"], p["sl"], p["tp"], p["open"],
                          sl_dist=abs(p["open"] - p["sl"]) if p["sl"] else 0.0,
                          tp_dist=abs(p["tp"] - p["open"]) if p["tp"] else 0.0)
                for fid, ft in st["groups"].get(key + ":early", {}).items():
                    g.legs[fid] = ft                                   # followers that filled before the master
                tm.groups[gid] = g
                st["groups"][key] = gid
                tm._save_groups()
                try:
                    from journal import score_snapshot
                    names = [tm._acct(x).name for x in g.legs if tm._acct(x)]
                    tm.journal.opened(gid, tm.master.symbol, o["side"], p["volume"], p["open"], p["sl"], p["tp"],
                                      abs(p["open"] - p["sl"]) if p["sl"] else 0.0,
                                      abs(p["tp"] - p["open"]) if p["tp"] else 0.0, names,
                                      score_snapshot(self.hub.read), tm.paper, news=tm._news_context(),
                                      kind="strategy" if st.get("kind") == "strategy" else "news")
                except Exception as e:
                    self.hub.note("error", f"journal: {e}")
            else:
                gid = st["groups"].get(key)
                g = tm.groups.get(gid) if gid else None
                if g:
                    g.legs[a.id] = p["ticket"]
                    tm._save_groups()
                else:
                    st["groups"].setdefault(key + ":early", {})[a.id] = p["ticket"]

    # ------------------------------------------------------------------ view
    def state(self):
        ev = self._next_event()
        with self.lock:
            evs = sorted(self.events.values(), key=lambda x: -x["t"])[:6]
            return dict(enabled=self.enabled, allow_auto=ALLOW_AUTO, settings=self.settings, box=self.box, plan=self.plan,
                        next=dict(title=ev["title"], currency=ev["currency"], impact=ev["impact"], t=ev["t"])
                        if ev else None,
                        events=[{k: v for k, v in e.items() if k != "groups"} for e in evs])

    def stop(self):
        self.running = False
