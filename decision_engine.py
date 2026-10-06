"""
Decision engine: reads what already exists and decides WAIT / WATCH / READY / BLOCKED (never sends an order).

  SCORE          the existing analyser score (engine.market_read + your plugins + weights) - unchanged, authoritative
  CONFIRMATIONS  context evidence shown next to the score, NOT added to it:
                   HTF bias, M15 structure, M5/M15 trigger, premium/discount, FVG, order block, VWAP, volume profile,
                   order flow, intermarket, regime, session
                 read from: Hub.read / Hub.states (engine), plugin votes in Read.groups, exits.py (order blocks /
                 VWAP helpers already used by caution.py), regime.py, intermarket.py, news.py
  BLOCKERS       risk_engine.hard_blockers() (+ execution_guard in step 3)

  Decision rule (default):
    READY    score >= passing  AND  HTF bias  AND  trigger  AND  no hard blocker
    BLOCKED  the setup itself is READY but a hard blocker stops it
    WATCH    bias present and (score passes but no trigger yet, or trigger present and score within 10 of passing)
    WAIT     anything else
    OPEN / MANAGING   a SuperChart trade in that direction is open

why_not_trade() explains the current decision line by line. A timeline keeps what changed and when.
"""
from __future__ import annotations

import json
import os
import threading
import time
from collections import deque

import exits
import setup_quality
from error_log import report
from engine import _recent
from market_hours import session_code, utc_from_server

HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(HERE, "decision_settings.json")
DEFAULTS = dict(require_htf_bias=True, require_trigger=True, require_no_blockers=True, watch_margin=10.0)
T = {1: "BULL", -1: "BEAR", 0: "FLAT"}


def clean_settings(raw):
    """Decision settings = the classic flags + the setup-quality settings (setup_quality.clean validates them).
    Missing or invalid values fall back to safe defaults."""
    raw = raw or {}
    s = dict(DEFAULTS)
    for k in DEFAULTS:
        if k in raw:
            try:
                s[k] = type(DEFAULTS[k])(raw[k])
            except (TypeError, ValueError):
                pass
    q = setup_quality.clean(raw)
    req = list(q["required"])
    if not s["require_htf_bias"] and "htf" in req:           # older flags still honoured
        req.remove("htf")
    if not s["require_trigger"] and "trigger" in req:
        req.remove("trigger")
    q["required"] = req
    s.update(q)
    return s


def load_settings():
    try:
        with open(SETTINGS_FILE) as f:
            return clean_settings(json.load(f))
    except (OSError, ValueError):
        return clean_settings(None)


def save_settings(new, current=None):
    merged = dict(current or load_settings())
    merged.update(new or {})
    if "required" in (new or {}):          # the list is the source of truth once the user edits it
        merged["require_htf_bias"] = "htf" in new["required"]
        merged["require_trigger"] = "trigger" in new["required"]
    s = clean_settings(merged)
    try:
        with open(SETTINGS_FILE, "w") as f:
            json.dump(s, f, indent=2)
    except OSError as e:
        from error_log import ERRORS
        ERRORS.add("CONFIG_ERROR", "decision_engine", "SETTINGS_NOT_SAVED", str(e))
    return s


class DecisionEngine:
    def __init__(self, hub):
        self.hub = hub
        self.settings = load_settings()
        self.timeline = deque(maxlen=80)
        self.last = None
        self._prev = {}
        self._ob_cache = (None, None)
        self.lock = threading.Lock()
        self.running = True
        threading.Thread(target=self._loop, daemon=True).start()

    # ------------------------------------------------------------------ reading existing results
    def _vote(self, read, name_part=None, category=None):
        """Majority direction of existing plugin votes (Read.groups) by item name or category; None if absent."""
        if read is None:
            return None
        dirs = []
        for g in read.groups:
            for it in g["items"]:
                if (category and g["category"] == category) or (name_part and name_part.lower() in it["name"].lower()):
                    dirs.append(it["dir"])
        if not dirs:
            return None
        s = sum(dirs)
        return 1 if s > 0 else -1 if s < 0 else 0

    def _obs(self, bars):
        key = (len(bars), bars[-1].t if bars else 0)
        if self._ob_cache[0] != key:
            self._ob_cache = (key, exits.order_blocks(bars) if len(bars) > 30 else [])
        return self._ob_cache[1]

    def setup_matrix(self, side=None):
        """Read-only matrix for `side` (default: the HTF bias). Every row: key, label, value, ok (True/False/None),
        kind (required / confirmation / info), raw fields for the fingerprint."""
        hub = self.hub
        r = hub.read
        st = hub.states or {}
        h4, h1, m15, m5 = (st.get(k) for k in ("H4", "H1", "M15", "M5"))
        bias = r.bias if r else 0
        side = side if side in (1, -1) else bias
        tk = hub.last_tick
        rows = []

        def row(key, label, value, ok, kind="confirmation", **raw):
            rows.append(dict(key=key, label=label, value=value, ok=ok, kind=kind, **raw))

        # required
        score = r.overall if r else None
        row("score", "Existing score", "--" if score is None else f"{score:.0f}% / {r.passing:.0f}%",
            None if score is None else score >= r.passing, "required")
        row("htf", "HTF bias", f"{T.get(bias, '?')} (H4 {T.get(h4.trend if h4 else 0)} · H1 {T.get(h1.trend if h1 else 0)})",
            bool(bias) and bias == side, "required", h4=h4.trend if h4 else None, h1=h1.trend if h1 else None)
        trig, trig_txt = None, "NONE"
        now_t = tk.t_msc // 1000 if tk else 0
        for s_, nb in ((m5, 24), (m15, 8)):
            e = s_.last() if s_ else None
            if e and side and e.direction == side and _recent(e, s_.tf, now_t, nb):
                trig, trig_txt = e, f"{s_.tf} {e.kind.upper()}"
                break
        row("trigger", "Trigger", f"{trig.tf} {trig.kind} @ {trig.level:.2f}" if trig else "none yet",
            trig is not None, "required", raw=trig_txt)
        # confirmations
        row("structure", "M15 structure", T.get(m15.trend if m15 else 0), bool(m15 and side and m15.trend == side),
            m15=m15.trend if m15 else None)
        smc = getattr(hub, "smc", None) or {}
        rng = smc.get("dealing_range") if smc.get("ok") else None
        if rng and rng.get("zone") not in (None, "UNKNOWN"):
            pd = rng["position"]
            pd_txt = rng["zone"]
            pd_where = ("above the range" if pd > 1 else "below the range" if pd < 0 else f"{pd * 100:.0f}%")
            good = "DISCOUNT" if side == 1 else "PREMIUM"
            row("pd", "Premium / Discount", f"{pd_txt} ({pd_where} · {rng['source']})",
                None if not side else (None if pd_txt == "EQUILIBRIUM" else pd_txt == good),
                raw="DISCOUNT" if pd < 0.5 else "PREMIUM", range=dict(high=rng["high"], low=rng["low"],
                                                                         eq=rng["equilibrium"], source=rng["source"]))
        else:
            pd = h1.pd if h1 else None
            pd_txt = None if pd is None else ("DISCOUNT" if pd < 0.5 else "PREMIUM")
            pd_where = None if pd is None else ("above the range" if pd > 1 else "below the range" if pd < 0 else f"{pd * 100:.0f}%")
            row("pd", "Premium / Discount", "--" if pd is None else f"{pd_txt} ({pd_where})",
                None if pd is None or not side else (pd < 0.5) == (side == 1), raw=pd_txt)
        fvg = (m15.fvg_bull if side == 1 else m15.fvg_bear) if m15 and side else None
        row("fvg", "FVG (M15)", f"{fvg.lo:.2f}-{fvg.hi:.2f}" if fvg else "none", fvg is not None if side else None)
        ob_ok, ob_txt = None, "--"
        bars = hub.src.bars("M15")[:-1] if hasattr(hub.src, "bars") else []
        if side and len(bars) > 30:
            obs = exits.active_obs(self._obs(bars), len(bars) - 1)
            a = (m15.atr if m15 else 0) or 1.0
            px = bars[-1].c
            near = [o for o in obs if o["side"] == side and o["lo"] - a <= px <= o["hi"] + a]
            ob_ok = bool(near)
            ob_txt = f"{near[-1]['lo']:.2f}-{near[-1]['hi']:.2f}" if near else "none near price"
        row("ob", "Order block", ob_txt, ob_ok)
        if smc.get("ok") and side:
            su = (smc.get("setups") or {}).get("BUY" if side == 1 else "SELL") or {}
            valid = su.get("status") in ("WAITING_RETEST", "RETESTED")
            row("smc_setup", "SMC setup", (f"{su['quality']} {su['confidence']:.2f} · {su['status'].replace('_', ' ')}"
                                           if valid else (su.get("status") or "NONE").replace("_", " ")
                                           + (f" (stage {su.get('stage')}/4)" if su.get("stage") else "")),
                valid, raw=su.get("quality") if valid else "NONE")
            swp = next((x for x in reversed(smc["liquidity"]["sweeps"]) if x["direction"] == side and x["confirmed"]), None)
            row("sweep", "Liquidity sweep", f"{swp['type']} @ {swp['level']:.2f}" if swp else "none confirmed",
                swp is not None)
        else:
            row("smc_setup", "SMC setup", "n/a", None)
        vw = self._vote(r, "VWAP")
        vw_src = "plugin"
        if vw is None and len(bars) > 20:
            v, _ = exits.vwap_bands(bars)
            vw = 1 if v[-1] and bars[-1].c > v[-1] else -1 if v[-1] else None
            vw_src = "calc"
        row("vwap", "VWAP", "--" if vw is None else f"{'above' if vw == 1 else 'below' if vw == -1 else 'at'} ({vw_src})",
            None if vw is None or not side else vw == side)
        vp = self._vote(r, "Volume Profile")
        row("vp", "Volume profile", "n/a (plugin off)" if vp is None else T.get(vp), None if vp is None or not side else vp == side)
        of = self._vote(r, category="Order Flow")
        row("orderflow", "Order flow", "n/a" if of is None else ("POSITIVE" if of == 1 else "NEGATIVE" if of == -1 else "MIXED"),
            None if of is None or not side else of == side)
        im = None
        try:
            import intermarket
            if intermarket.CURRENT is not None:
                im = intermarket.CURRENT.view().get("bias")
        except Exception as e:
            im = None
            report("DECISION_ERROR", "decision_engine", "INTERMARKET_UNAVAILABLE", e)
        row("intermarket", "Intermarket", "n/a" if im is None else T.get(im), None if not im or not side else im == side)
        rg = getattr(hub, "regime", None)
        rg_label, rg_dir = None, 0
        if rg is not None:
            try:
                rs = rg.state()
                rg_label, rg_dir = rs["label"], rs["dir"]
            except Exception as e:
                report("DECISION_ERROR", "decision_engine", "REGIME_UNAVAILABLE", e)
        row("regime", "Regime", rg_label or "n/a", None if rg_label is None or not side else rg_dir != -side,
            raw=rg_label)
        sess = session_code(utc_from_server(now_t, hub.utc_offset)) if tk else None
        at = getattr(hub, "autotrader", None)
        allowed = [x.upper().replace(" ", "_") for x in (at.settings.get("sessions") if at else ["London", "New York"])]
        sess_ok = None if sess is None else (not allowed or sess in allowed or (sess == "OVERLAP" and allowed))
        row("session", "Session", sess or "--", sess_ok, raw=sess)
        risk = getattr(hub, "risk", None)
        if risk is not None:
            ns = risk.news_state()
        else:
            nf = getattr(hub, "news", None)
            ns = nf.news_state() if nf is not None else dict(state="NEWS_UNKNOWN", text="news reader off")
        st_ = ns["state"]
        news_txt = {"NEWS_CLEAR": "CLEAR", "NEWS_HIGH_IMPACT": f"HIGH IMPACT {ns['text']}",
                    "NEWS_UNKNOWN": f"UNKNOWN ({ns['text']})"}[st_]
        news_ok = True if st_ == "NEWS_CLEAR" else False if st_ == "NEWS_HIGH_IMPACT" else None
        row("news", "News", news_txt, news_ok, "info",
            raw={"NEWS_CLEAR": "CLEAR", "NEWS_HIGH_IMPACT": "WARNING", "NEWS_UNKNOWN": "UNKNOWN"}[st_])
        spread = round(tk.ask - tk.bid, 2) if tk else None
        mx = (at.settings.get("max_spread") if at else 0) or 0
        row("spread", "Spread", "--" if spread is None else f"${spread:.2f}" + (f" (max ${mx:.2f})" if mx else ""),
            None if spread is None else (not mx or spread <= mx), "info", raw=spread)
        return dict(side=side, bias=bias, score=score, passing=r.passing if r else None, rows=rows,
                    confirmations=sum(1 for x in rows if x["kind"] == "confirmation" and x["ok"]),
                    confirmations_total=sum(1 for x in rows if x["kind"] == "confirmation" and x["ok"] is not None))

    # ------------------------------------------------------------------ decision
    def decide(self, side=None, kind="preview"):
        hub, s = self.hub, self.settings
        m = self.setup_matrix(side)
        side = m["side"]
        req = {x["key"]: x for x in m["rows"] if x["kind"] == "required"}
        risk = getattr(hub, "risk", None)
        blockers = risk.hard_blockers(side, kind) if risk is not None else []
        ex = getattr(hub, "execution", None)
        exec_res = ex.check(side, None, None, None) if ex is not None and side else dict(ok=True, reasons=[])
        blockers = blockers + [dict(b, level=b.get("level", "hard")) for b in exec_res.get("reasons", [])]
        # setup quality: grouped categories, confidence 0..1, conflicts, regime modifiers, required conditions
        rg = getattr(hub, "regime", None)
        rg_state = None
        if rg is not None:
            try:
                rg_state = rg.state()
            except Exception as e:
                hub.note("error", f"decision engine: regime read failed: {e}", category="DECISION_ERROR")
        sess_row = next((x for x in m["rows"] if x["key"] == "session"), {})
        q = setup_quality.evaluate(side, hub.read, getattr(hub, "smc", None), m, rg_state, sess_row.get("ok"), s)
        if q["conflict_block"]:
            blockers = blockers + [dict(code=q["conflict_block"]["code"], text=q["conflict_block"]["text"], level="hard")]
        hard = [b for b in blockers if b["level"] == "hard"]
        score_ok = bool(req["score"]["ok"])
        bias_ok = bool(req["htf"]["ok"]) or not s["require_htf_bias"]
        trig_ok = bool(req["trigger"]["ok"]) or not s["require_trigger"]
        setup_ready = side in (1, -1) and not q["missing_required"]
        tm = hub.trader
        open_same = [g for g in (tm.groups.values() if tm else []) if g.side == side]
        if open_same:
            state = "MANAGING" if any(time.time() - g.created > 60 for g in open_same) else "OPEN"
        elif setup_ready and (hard and s["require_no_blockers"]):
            state = "BLOCKED"
        elif setup_ready:
            state = "READY"
        elif side and (score_ok and not trig_ok or trig_ok and m["score"] is not None
                       and m["score"] >= m["passing"] - s["watch_margin"]):
            state = "WATCH"
        else:
            state = "WAIT"
        r = hub.read
        st = hub.states or {}
        trig_row = req["trigger"]
        out = dict(symbol=getattr(hub.src, "symbol", ""), side={1: "BUY", -1: "SELL"}.get(side, "NONE"),
                   side_i=side, state=state, score=m["score"], passing_score=m["passing"],
                   bias={k: T.get(st[k].trend if st.get(k) else 0) for k in ("H4", "H1")},
                   trigger=trig_row["raw"], structure={k: T.get(st[k].trend if st.get(k) else 0) for k in ("M15", "M5")},
                   confirmations=[x for x in m["rows"] if x["kind"] == "confirmation" and x["ok"]],
                   against=[x for x in m["rows"] if x["kind"] == "confirmation" and x["ok"] is False],
                   blockers=blockers, hard_blockers=hard,
                   regime=next((x["raw"] for x in m["rows"] if x["key"] == "regime"), None),
                   session=next((x["raw"] for x in m["rows"] if x["key"] == "session"), None),
                   news=next((x["raw"] for x in m["rows"] if x["key"] == "news"), None),
                   risk_ok=not [b for b in hard if b["code"] not in [x["code"] for x in exec_res.get("reasons", [])]],
                   execution_ok=bool(exec_res.get("ok", True)), matrix=m, verdict=r.verdict if r else "",
                   t=time.time(), quality=q, confidence=q["confidence"], conflicts=q["conflicts"],
                   missing_required=q["missing_required"], required=q["required"],
                   smc=smc_summary(getattr(hub, "smc", None)),
                   data=getattr(hub, "data_status", None))
        out["explanation"] = setup_quality.explain(side, q, blockers, state)
        return out

    def why_not_trade(self, side=None, kind="preview"):
        d = self.decide(side, kind)
        lines = []
        for x in d["matrix"]["rows"]:
            mark = "✓" if x["ok"] else "✗" if x["ok"] is False else "–"
            lines.append(dict(label=x["label"], value=x["value"], mark=mark, kind=x["kind"]))
        for b in d["blockers"]:
            lines.append(dict(label=b["code"], value=b["text"], mark="✗" if b["level"] == "hard" else "!", kind="blocker"))
        reasons = []
        req = {x["key"]: x for x in d["matrix"]["rows"] if x["kind"] == "required"}
        missing = d.get("missing_required") or []
        if "score" in missing:
            reasons.append("SCORE BELOW PASSING")
        if "htf" in missing:
            reasons.append("NO HTF BIAS" if not d["matrix"]["bias"] else "AGAINST HTF BIAS")
        if "trigger" in missing:
            reasons.append("NO TRIGGER")
        for k in missing:
            if k not in ("score", "htf", "trigger"):
                reasons.append(f"REQUIRED {k.upper()} NOT MET")
        for k, cnd in (d.get("quality") or {}).get("conditions", {}).items():
            lines.append(dict(label=cnd["label"], value="yes" if cnd["ok"] else "no" if cnd["ok"] is False else "n/a",
                              mark="✓" if cnd["ok"] else "✗" if cnd["ok"] is False else "–",
                              kind=cnd["cls"].lower()))
        reasons += [b["code"].replace("_", " ") for b in d["hard_blockers"]]
        final = d["state"] if d["state"] in ("READY", "OPEN", "MANAGING") else f"{d['state']} — " + " + ".join(reasons or ["waiting"])
        return dict(decision=d["state"], side=d["side"], final=final, lines=lines, reasons=reasons,
                    explanation=d.get("explanation"), confidence=d.get("confidence"), conflicts=d.get("conflicts"))

    def snapshot(self, side, kind):
        """Stored in the journal at entry: decision state, fingerprint, confirmations, blockers, spread, session."""
        from setup_fingerprint import build
        try:
            d = self.decide(side, kind)
            return dict(decision_state=d["state"], setup_fingerprint=build(d["matrix"]),
                        confirmations=[x["label"] for x in d["confirmations"]],
                        against=[x["label"] for x in d["against"]],
                        blockers=[b["code"] for b in d["blockers"]],
                        spread=next((x["raw"] for x in d["matrix"]["rows"] if x["key"] == "spread"), None),
                        session=d["session"], confidence=d.get("confidence"),
                        conflicts=[c["code"] for c in d.get("conflicts") or []],
                        smc_state=d.get("smc"), setup_quality=(d.get("quality") or {}).get("quality"),
                        indicator_groups={k: dict(direction=v["direction"], support=v["support"])
                                          for k, v in ((d.get("quality") or {}).get("categories") or {}).items()
                                          if v["available"]},
                        setup_id=((d.get("quality") or {}).get("smc_setup") or {}).get("setup_id"))
        except Exception as e:
            self.hub.note("error", f"decision snapshot: {e}")
            return {}

    # ------------------------------------------------------------------ timeline
    def event(self, text, kind="info"):
        self.timeline.appendleft(dict(t=time.time(), text=text, kind=kind))

    def _track(self, d):
        p = self._prev
        tk = self.hub.last_tick
        srv = tk.t_msc // 1000 if tk else None
        b = f"{d['bias']['H4']}/{d['bias']['H1']}"
        if p.get("bias") != b:
            self.event(f"HTF {b}", "bias")
        if d["trigger"] != "NONE" and p.get("trigger") != d["trigger"]:
            self.event(f"Trigger {d['trigger']} ({d['side']})", "trigger")
        sc = d["score"]
        if sc is not None and d["passing_score"] is not None:
            passed = sc >= d["passing_score"]
            if p.get("passed") is not None and passed != p.get("passed"):
                self.event(f"Score {sc:.0f}% {'≥' if passed else '<'} passing {d['passing_score']:.0f}%", "score")
            p["passed"] = passed
        if p.get("state") != d["state"]:
            extra = ""
            if d["state"] == "BLOCKED":
                extra = ": " + ", ".join(x["code"] for x in d["hard_blockers"])
            self.event(f"{d['state']} {d['side']}{extra}", "state")
            self._on_state(d, p.get("state"))
        p.update(bias=b, trigger=d["trigger"], state=d["state"], srv=srv)

    def _on_state(self, d, prev):
        al = getattr(self.hub, "alerts", None)
        if al is not None and hasattr(al, "on_decision"):
            try:
                al.on_decision(d, prev)
            except Exception as e:
                report("ALERT_ERROR", "alerts.on_decision", "ALERT_FAILED", e)

    def _loop(self):
        time.sleep(4)
        while self.running:
            try:
                d = self.decide()
                with self.lock:
                    self.last = d
                self._track(d)
            except Exception as e:
                self.hub.note("error", f"decision engine: {e.__class__.__name__}: {e}")
            time.sleep(1.0)

    def state(self):
        with self.lock:
            d = self.last
        if d is None:
            d = self.decide()
        return dict(d, timeline=list(self.timeline)[:40], settings=self.settings)

    def update_settings(self, new):
        with self.lock:
            self.settings = save_settings(new, self.settings)
        return self.settings

    def stop(self):
        self.running = False


def smc_summary(smc):
    try:
        import smc_engine
        return smc_engine.summary(smc)
    except Exception as e:
        return dict(ok=False, reason=f"{e.__class__.__name__}: {e}")
