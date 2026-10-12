"""
MASTER AUTO-TRADE GATE: the single, ordered path from a SIGNAL to an ORDER for automated trades.

A signal is not a trade. Every automated order passes, in this order (spec "final decision hierarchy"):

   1 DATA        data valid (data_validation), fresh tick / analysis        -> BLOCKED_STALE_DATA
   2 MASTER      MASTER TRADING enabled, config valid, not halted            -> BLOCKED_MASTER_DISABLED
   3 SESSION     allowed session, market open                                -> BLOCKED_SESSION
   4 NEWS        news window / unknown news state (policy)                  -> BLOCKED_NEWS
   5 BIAS        HTF bias in the trade direction                            -> BLOCKED_STRUCTURE
   6 STRUCTURE   valid market structure (trigger), no structure conflict    -> BLOCKED_STRUCTURE
   7 SMC         SMC conditions the user made REQUIRED (setup, sweep, ...)   -> BLOCKED_SMC
   8 LOCATION    location, when REQUIRED                                    -> BLOCKED_LOCATION
   9 CONFIRM     existing score >= passing + other REQUIRED confirmations  -> BLOCKED_SCORE
  10 CONFLICT    conflict policy (setup_quality)                            -> BLOCKED_CONFLICT
  11 RISK        DD / daily target / max trades / max open / opposite /     -> BLOCKED_DD_LIMIT, BLOCKED_DAILY_TARGET,
                 SL / TP / RR / risk per trade / exposure / spread             BLOCKED_MAX_TRADES, BLOCKED_MAX_OPEN_POSITIONS,
                                                                               BLOCKED_OPPOSITE_POSITION, BLOCKED_INVALID_SL,
                                                                               BLOCKED_INVALID_TP, BLOCKED_RISK, BLOCKED_SPREAD
  12 EXECUTION   broker / execution guard                                   -> BLOCKED_EXECUTION
  13 DUPLICATE   new signal_id, setup not traded again too soon             -> BLOCKED_DUPLICATE_SIGNAL / _SETUP
  14 AUTO        AUTO TRADE enabled                                         -> BLOCKED_AUTO_DISABLED
  15 ARM         trading ARMED                                              -> BLOCKED_NOT_ARMED
  -> EXECUTE     only then TradeManager.open() (which runs check_request() once more)

Stages recorded for every signal: SIGNAL (BUY_SETUP_DETECTED) -> DECISION (BUY_ALLOWED / BUY_REJECTED) -> RISK
(RISK_ALLOWED / RISK_REJECTED) -> EXECUTION (EXECUTION_ALLOWED / EXECUTION_REJECTED). Nothing is rejected silently:
every evaluated signal is written to the signal journal with its gates, block code, and explanation.
Every unknown blocker code still blocks (as BLOCKED_RISK): an unmapped reason never lets an order through.
This module never sends an order itself.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from collections import OrderedDict, deque

HERE = os.path.dirname(os.path.abspath(__file__))
WORD = {1: "BUY", -1: "SELL"}
GATES = ["DATA", "MASTER", "SESSION", "NEWS", "BIAS", "STRUCTURE", "SMC", "LOCATION", "CONFIRM", "CONFLICT",
         "RISK", "EXECUTION", "DUPLICATE", "AUTO", "ARM"]
STAGE_OF = {g: ("DECISION" if g in ("BIAS", "STRUCTURE", "SMC", "LOCATION", "CONFIRM", "CONFLICT", "SESSION", "NEWS",
                                     "DATA", "MASTER", "DUPLICATE", "AUTO", "ARM")
                else "RISK" if g == "RISK" else "EXECUTION") for g in GATES}

# risk_engine / execution_guard / decision code -> (gate, BLOCKED_* code)
CODE_MAP = {
    "DATA_INVALID": ("DATA", "BLOCKED_STALE_DATA"), "STALE_TICK": ("DATA", "BLOCKED_STALE_DATA"),
    "STALE_ANALYSER": ("DATA", "BLOCKED_STALE_DATA"), "BROKER_DATA_STALE": ("DATA", "BLOCKED_STALE_DATA"),
    "MASTER_DISABLED": ("MASTER", "BLOCKED_MASTER_DISABLED"), "CONFIG_INVALID": ("MASTER", "BLOCKED_MASTER_DISABLED"),
    "HALTED": ("MASTER", "BLOCKED_MASTER_DISABLED"), "READ_ONLY": ("MASTER", "BLOCKED_MASTER_DISABLED"),
    "MARKET_CLOSED": ("SESSION", "BLOCKED_SESSION"), "SESSION": ("SESSION", "BLOCKED_SESSION"),
    "NEWS_BLOCK": ("NEWS", "BLOCKED_NEWS"), "NEWS_UNKNOWN": ("NEWS", "BLOCKED_NEWS"),
    "STRUCTURE_CONFLICT": ("STRUCTURE", "BLOCKED_STRUCTURE"), "SMC_CONFLICT": ("SMC", "BLOCKED_SMC"),
    "HIGH_CONFLICT": ("CONFLICT", "BLOCKED_CONFLICT"),
    "DAILY_DD_LOCK": ("RISK", "BLOCKED_DD_LIMIT"), "MAX_TOTAL_DD": ("RISK", "BLOCKED_DD_LIMIT"),
    "DAILY_TP_LOCK": ("RISK", "BLOCKED_DAILY_TARGET"), "MAX_TRADES_REACHED": ("RISK", "BLOCKED_MAX_TRADES"),
    "MAX_OPEN_REACHED": ("RISK", "BLOCKED_MAX_OPEN_POSITIONS"),
    "OPPOSITE_POSITION": ("RISK", "BLOCKED_OPPOSITE_POSITION"),
    "INVALID_SL": ("RISK", "BLOCKED_INVALID_SL"), "NO_PROTECTIVE_SL": ("RISK", "BLOCKED_INVALID_SL"),
    "INVALID_TP": ("RISK", "BLOCKED_INVALID_TP"), "TP_REQUIRED": ("RISK", "BLOCKED_INVALID_TP"),
    "RISK_PER_TRADE": ("RISK", "BLOCKED_RISK"), "EXPOSURE_LIMIT": ("RISK", "BLOCKED_RISK"),
    "MIN_RR": ("RISK", "BLOCKED_RISK"), "DRIFT_PAUSE": ("RISK", "BLOCKED_RISK"),
    "SPREAD_TOO_HIGH": ("RISK", "BLOCKED_SPREAD"),
    "MASTER_DISCONNECTED": ("EXECUTION", "BLOCKED_EXECUTION"), "ALGO_TRADING_OFF": ("EXECUTION", "BLOCKED_EXECUTION"),
    "AUTO_TRADE_OFF": ("AUTO", "BLOCKED_AUTO_DISABLED"), "ARM_OFF": ("ARM", "BLOCKED_NOT_ARMED"),
    "DUPLICATE_SIGNAL": ("DUPLICATE", "BLOCKED_DUPLICATE_SIGNAL"),
    "DUPLICATE_SETUP": ("DUPLICATE", "BLOCKED_DUPLICATE_SETUP"), "REENTRY": ("DUPLICATE", "BLOCKED_DUPLICATE_SETUP"),
}
# required condition -> gate
COND_GATE = {"htf": ("BIAS", "BLOCKED_STRUCTURE"), "trigger": ("STRUCTURE", "BLOCKED_STRUCTURE"),
             "structure": ("STRUCTURE", "BLOCKED_STRUCTURE"), "displacement": ("STRUCTURE", "BLOCKED_STRUCTURE"),
             "smc_setup": ("SMC", "BLOCKED_SMC"), "retest": ("SMC", "BLOCKED_SMC"), "sweep": ("SMC", "BLOCKED_SMC"),
             "fvg": ("SMC", "BLOCKED_SMC"), "ob": ("SMC", "BLOCKED_SMC"), "location": ("LOCATION", "BLOCKED_LOCATION"),
             "session": ("SESSION", "BLOCKED_SESSION"), "score": ("CONFIRM", "BLOCKED_SCORE"),
             "min_confidence": ("CONFIRM", "BLOCKED_SCORE"), "volume": ("CONFIRM", "BLOCKED_SCORE"),
             "vwap": ("CONFIRM", "BLOCKED_SCORE"), "momentum": ("CONFIRM", "BLOCKED_SCORE"),
             "trend": ("CONFIRM", "BLOCKED_SCORE"), "no_conflict": ("CONFLICT", "BLOCKED_CONFLICT")}


def signal_id(symbol, tf, t, direction, structure_event="", setup_type=""):
    """Deterministic id: the same signal (symbol, timeframe, bar time, direction, structure event, setup type)
    always hashes to the same id, so it can never be processed twice."""
    raw = "|".join(str(x) for x in (symbol, tf, int(t), int(direction), structure_event or "", setup_type or ""))
    return "S" + hashlib.sha1(raw.encode()).hexdigest()[:15]


class SignalJournal:
    """Every evaluated signal (allowed or blocked) as JSON lines, with its trade_id when it traded."""

    def __init__(self, paper=True, path=None):
        self.path = path or os.path.join(HERE, "signals_paper.jsonl" if paper else "signals.jsonl")
        self.lock = threading.Lock()
        self.recent = deque(maxlen=400)
        self.seen = OrderedDict()                 # signal_id -> trade_id or ""
        self.setups = {}                          # setup_id -> dict(trade_id, t)
        self._load()

    def _load(self):
        try:
            with open(self.path) as f:
                lines = f.readlines()[-5000:]
        except OSError:
            return
        for ln in lines:
            try:
                e = json.loads(ln)
            except ValueError:
                continue
            self.recent.appendleft(e)
            self.seen[e.get("signal_id")] = e.get("trade_id") or ""
            if e.get("setup_id") and e.get("trade_id"):
                self.setups[e["setup_id"]] = dict(trade_id=e["trade_id"], t=e.get("t", 0))

    def record(self, e):
        with self.lock:
            self.recent.appendleft(e)
            self.seen[e["signal_id"]] = e.get("trade_id") or ""
            while len(self.seen) > 20000:
                self.seen.popitem(last=False)
            if e.get("setup_id") and e.get("trade_id"):
                self.setups[e["setup_id"]] = dict(trade_id=e["trade_id"], t=e.get("t", time.time()))
            try:
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(e, default=str) + "\n")
            except OSError as ex:
                from error_log import ERRORS
                ERRORS.add("STATE_ERROR", "signal_journal", "WRITE_FAILED", str(ex))

    def mark_trade(self, sid, trade_id, setup_id=None):
        with self.lock:
            self.seen[sid] = trade_id
            if setup_id:
                self.setups[setup_id] = dict(trade_id=trade_id, t=time.time())
            e = next((x for x in self.recent if x.get("signal_id") == sid), None)
            if e is not None:
                e["trade_id"] = trade_id
                e["order_result"] = "SENT"
            try:
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(dict(signal_id=sid, trade_id=trade_id, setup_id=setup_id, t=time.time(),
                                            event="TRADE_LINK")) + "\n")
            except OSError:
                pass

    def stats(self):
        ev = [e for e in self.recent if e.get("event") != "TRADE_LINK"]
        blocked = {}
        for e in ev:
            if e.get("decision") == "BLOCKED":
                blocked[e.get("block_code")] = blocked.get(e.get("block_code"), 0) + 1
        return dict(generated=len(ev), blocked=sum(blocked.values()),
                    executed=sum(1 for e in ev if e.get("trade_id")), by_code=blocked)


class MasterGate:
    def __init__(self, hub, paper=True, journal=None):
        self.hub = hub
        self.journal = journal or SignalJournal(paper)
        self.last = None

    # ------------------------------------------------------------------ helpers
    def make_signal(self, source, tf, t, side, entry=None, sl=None, tp=None, score=None, cautions=None,
                    structure_event="", setup_type=None):
        sym = getattr(self.hub.src, "symbol", "")
        sid = signal_id(sym, tf, t, side, structure_event, setup_type or source)
        return dict(signal_id=sid, source=source, symbol=sym, tf=tf, t=int(t), side=side, entry=entry, sl=sl, tp=tp,
                    score=score, cautions=list(cautions or []), structure_event=structure_event,
                    setup_type=setup_type or source, signal=f"{WORD.get(side, '?')}_SETUP_DETECTED")

    def is_duplicate(self, sid, traded_only=False):
        """traded_only (STRATEGIES): a READY spell that was SKIPPED (session, max open, spread...) may be tried again
        while it stays all green; only a spell that already became a trade is a duplicate."""
        if traded_only:
            return bool(self.journal.seen.get(sid))
        return sid in self.journal.seen

    def _lot(self, lot, q):
        """Regime / news risk multipliers applied to the auto lot, rounded DOWN to the broker lot step."""
        mult = 1.0
        notes = []
        rm = ((q or {}).get("regime") or {}).get("risk_multiplier", 1.0) or 1.0
        if rm != 1.0:
            mult *= rm
            notes.append(f"regime x{rm}")
        risk = getattr(self.hub, "risk", None)
        if risk is not None:
            nm = risk.lot_multiplier("auto")
            if nm != 1.0:
                mult *= nm
                notes.append(f"news unknown x{nm}")
        if mult == 1.0:
            return lot, notes
        tm = self.hub.trader
        b = (getattr(tm.master, "broker", None) or {}) if tm else {}
        step = b.get("lot_step") or 0.01
        mn = b.get("min_lot") or step
        new = max(mn, int((lot * mult) / step + 1e-9) * step)
        return round(new, 8), notes

    # ------------------------------------------------------------------ evaluation
    def evaluate(self, sig, kind="auto", lot=0.01, record=True, prefilter=None, waive=()):
        """sig: make_signal() dict. prefilter: (code, text) from the auto-trade signal filters (direction, session
        hours, cautions, ...) - recorded and blocks like any gate. Returns the gate result (never sends)."""
        hub = self.hub
        side = sig["side"]
        sl_d = abs(sig["entry"] - sig["sl"]) if sig.get("sl") is not None and sig.get("entry") is not None else None
        tp_d = abs(sig["tp"] - sig["entry"]) if sig.get("tp") is not None and sig.get("entry") is not None else None
        fails = {g: [] for g in GATES}

        def fail(gate, code, text, source):
            fails[gate].append(dict(code=code, text=text, source=source))

        # decision (setup quality, required conditions, conflicts)
        de = getattr(hub, "decision", None)
        d = None
        if de is not None:
            try:
                d = de.decide(side, kind)
            except Exception as e:
                fail("DATA", "BLOCKED_STALE_DATA", f"decision engine failed: {e.__class__.__name__}: {e}", "decision")
                hub.note("error", f"gate: decision failed: {e}", category="DECISION_ERROR")
        q = (d or {}).get("quality") or {}
        if d is None and de is None:
            fail("CONFIRM", "BLOCKED_SCORE", "no decision engine: cannot confirm the signal", "decision")
        if d is not None:
            for k in d.get("missing_required") or []:
                if k == "trigger" and sig["source"] != "analyser":
                    continue                     # a breakout / trend-line signal is itself the structure trigger
                stratlike = str(sig["source"]).startswith("st_") or sig["source"] in ("tl_break", "tl_retest")
                own = sig["source"] == "reversal" or stratlike
                if stratlike and k != "htf":
                    # STRATEGIES (Eugene: "pag lahat ng na-set sa strategy nag-green, trade na agad"): the strategy's
                    # own conditions are the setup. The analyser's required list (and regime extras such as
                    # BREAKOUT -> displacement) does not apply; the HTF bias still does on NORMAL (HTF filter ON).
                    # Risk, news, session, execution and the structure conflict stay hard blockers.
                    continue
                if k in waive and k != "htf" and own:
                    continue                     # reversal arrow / strategy: its own trigger / score
                if k == "score" and kind == "auto":
                    # AUTO TRADE "Min score %" (0 = passing) is YOUR score threshold for automatic entries
                    at_s = getattr(getattr(hub, "autotrader", None), "settings", None) or {}
                    thr = float(at_s.get("min_score") or 0)
                    sc = getattr(getattr(hub, "read", None), "overall", None)     # the same score the rule checks
                    if sc is None:
                        sc = sig.get("score")
                    if thr > 0 and sc is not None and sc >= thr:
                        continue
                if k == "htf" and "htf:range" in waive and own:
                    continue                     # router RANGE mode: no trend to follow (a clear opposite bias is
                                                 # already blocked by the router before the gate)
                gate, code = COND_GATE.get(k, ("CONFIRM", "BLOCKED_SCORE"))
                cnd = (q.get("conditions") or {}).get(k, {})
                fail(gate, code, f"required: {cnd.get('label', k)}", "decision")
        # risk + execution + conflicts (all hard blockers)
        risk = getattr(hub, "risk", None)
        blockers = []
        if risk is not None:
            try:
                cap = None
                if sig["source"] in ("tl_break", "tl_retest"):
                    cap = 0.0                                  # v2.8.27 TL BREAK: SL capped at $20, no % block
                elif sig["source"] in ("st_conso", "st_trend", "st_pullback"):   # own risk % per strategy (0 = off)
                    import strategies as _st
                    nm = sig["source"][3:]
                    cap = float(_st.load_settings().get(nm, {}).get("max_risk_pct", 0.0))
                blockers += risk.hard_blockers(side, kind, lot, sl_d, tp_d, risk_pct_cap=cap)
            except Exception as e:
                fail("RISK", "BLOCKED_RISK", f"risk engine failed: {e.__class__.__name__}: {e}", "risk")
                hub.note("error", f"gate: risk engine failed: {e}", category="RISK_ERROR")
        else:
            fail("RISK", "BLOCKED_RISK", "no risk engine", "risk")
        ex = getattr(hub, "execution", None)
        ex_res = None
        if ex is not None:
            try:
                ex_res = ex.check(side, lot, sl_d, tp_d)
                blockers += [dict(b, level=b.get("level", "hard"), _src="execution") for b in ex_res.get("reasons", [])]
            except Exception as e:
                fail("EXECUTION", "BLOCKED_EXECUTION", f"execution guard failed: {e.__class__.__name__}: {e}", "execution")
                hub.note("error", f"gate: execution guard failed: {e}", category="EXECUTION_ERROR")
        cb = q.get("conflict_block")
        if cb and cb.get("code") == "STRUCTURE_CONFLICT" and "htf:own" in waive and \
                (str(sig["source"]).startswith("st_") or sig["source"] in ("tl_break", "tl_retest")):
            cb = None       # STRATEGIES with the analyser HTF filter OFF: the strategy's own trend (its BOS) decides
        if cb:
            blockers.append(dict(code=q["conflict_block"]["code"], text=q["conflict_block"]["text"], level="hard"))
        seen_codes = set()
        for b in blockers:
            if b.get("level", "hard") != "hard":
                continue
            key = (b["code"], b.get("_src"))
            if key in seen_codes:
                continue
            seen_codes.add(key)
            gate, code = CODE_MAP.get(b["code"], ("EXECUTION" if b.get("_src") == "execution" else "RISK",
                                                  "BLOCKED_EXECUTION" if b.get("_src") == "execution" else "BLOCKED_RISK"))
            if b.get("_src") == "execution" and b["code"] not in ("SPREAD_TOO_HIGH", "NO_PROTECTIVE_SL",
                                                                 "BROKER_DATA_STALE"):
                gate, code = "EXECUTION", "BLOCKED_EXECUTION"
            fail(gate, code, f"{b['code']}: {b['text']}", b.get("_src") or "risk")
        if sl_d is not None and sl_d <= 0:
            fail("RISK", "BLOCKED_INVALID_SL", "signal has no stop loss", "signal")
        if prefilter:
            code, text = prefilter
            gate, bcode = CODE_MAP.get(code, ("CONFIRM", code if code.startswith("BLOCKED_") else "BLOCKED_SCORE"))
            fail(gate, bcode, text, "auto filter")
        # duplicates
        if self.is_duplicate(sig["signal_id"], traded_only=str(sig.get("source", "")).startswith("st_")):
            fail("DUPLICATE", "BLOCKED_DUPLICATE_SIGNAL", f"signal {sig['signal_id']} was already processed", "gate")
        setup_id = (q.get("smc_setup") or {}).get("setup_id") if (q.get("smc_setup") or {}).get("status") in (
            "WAITING_RETEST", "RETESTED") else None
        if setup_id and setup_id in self.journal.setups:
            prev = self.journal.setups[setup_id]
            tm = hub.trader
            still_open = tm is not None and prev["trade_id"] in tm.groups
            at = getattr(hub, "autotrader", None)
            wait = (at.settings.get("reentry_minutes", 30) if at else 30) * 60
            if still_open or time.time() - prev["t"] < wait:
                fail("DUPLICATE", "BLOCKED_DUPLICATE_SETUP",
                     f"setup {setup_id} already traded ({prev['trade_id']}) {(time.time() - prev['t']) / 60:.0f} min ago", "gate")
        gates = []
        first = None
        for g in GATES:
            f = fails[g]
            ok = not f
            gates.append(dict(gate=g, stage=STAGE_OF[g], ok=ok, codes=[x["code"] for x in f],
                              reasons=[x["text"] for x in f]))
            if not ok and first is None:
                first = (g, f[0])
        w = WORD.get(side, "?")
        dec_ok = all(x["ok"] for x in gates if x["stage"] == "DECISION")
        risk_ok = all(x["ok"] for x in gates if x["stage"] == "RISK")
        exe_ok = all(x["ok"] for x in gates if x["stage"] == "EXECUTION")
        allowed = first is None
        new_lot, lot_notes = self._lot(lot, q) if allowed else (lot, [])
        stages = [dict(stage="SIGNAL", result=sig["signal"]),
                  dict(stage="DECISION", result=f"{w}_ALLOWED" if dec_ok else f"{w}_REJECTED"),
                  dict(stage="RISK", result="RISK_ALLOWED" if risk_ok else "RISK_REJECTED"),
                  dict(stage="EXECUTION", result="EXECUTION_ALLOWED" if exe_ok else "EXECUTION_REJECTED")]
        expl = (d or {}).get("explanation") or {}
        if not allowed:
            expl = dict(expl, head=f"{w} BLOCKED", but=[x for g in gates for x in g["reasons"]])
        else:
            expl = dict(expl, head=f"{w} APPROVED")
        res = dict(allowed=allowed, block_code=first[1]["code"] if first else None,
                   block_gate=first[0] if first else None, block_reason=first[1]["text"] if first else None,
                   gates=gates, stages=stages, signal_id=sig["signal_id"], setup_id=setup_id, lot=new_lot,
                   lot_notes=lot_notes, explanation=expl, confidence=q.get("confidence"), t=time.time())
        if record:
            self.record(sig, res, d, lot)
        self.last = res
        return res

    def record(self, sig, res, d=None, lot=None):
        q = (d or {}).get("quality") or {}
        tk = self.hub.last_tick
        e = dict(t=time.time(), signal_id=sig["signal_id"], setup_id=res.get("setup_id"), trade_id=None,
                 symbol=sig["symbol"], timeframe=sig["tf"], bar_t=sig["t"], source=sig["source"],
                 direction=WORD.get(sig["side"]), signal=sig["signal"], entry=sig.get("entry"), sl=sig.get("sl"),
                 tp=sig.get("tp"), lot=res.get("lot", lot),
                 htf_bias=(q.get("quality") or {}).get("bias"), structure=(q.get("quality") or {}).get("structure"),
                 smc_state=(d or {}).get("smc"), indicator_groups={k: dict(direction=v["direction"], support=v["support"])
                                                                  for k, v in (q.get("categories") or {}).items()
                                                                  if v.get("available")},
                 score=(d or {}).get("score"), confidence=q.get("confidence"),
                 risk=dict(ok=all(g["ok"] for g in res["gates"] if g["stage"] == "RISK")),
                 spread=round(tk.ask - tk.bid, 2) if tk else None, session=(d or {}).get("session"),
                 news_state=(d or {}).get("news"), decision="ALLOWED" if res["allowed"] else "BLOCKED",
                 block_code=res.get("block_code"), block_reason=res.get("block_reason"),
                 gates=[dict(gate=g["gate"], ok=g["ok"], codes=g["codes"]) for g in res["gates"]],
                 stages=res["stages"], explanation=res["explanation"], order_result=None)
        self.journal.record(e)

    def record_skip(self, sig, code, text):
        """A signal stopped by an auto-trade filter before the full evaluation (still journaled)."""
        gate, bcode = CODE_MAP.get(code, ("CONFIRM", code if str(code).startswith("BLOCKED_") else "BLOCKED_SCORE"))
        res = dict(allowed=False, block_code=bcode, block_gate=gate, block_reason=text, setup_id=None,
                   gates=[dict(gate=g, stage=STAGE_OF[g], ok=g != gate, codes=[bcode] if g == gate else [],
                               reasons=[text] if g == gate else []) for g in GATES],
                   stages=[dict(stage="SIGNAL", result=sig["signal"]),
                           dict(stage=STAGE_OF[gate], result=f"{WORD.get(sig['side'])}_REJECTED")],
                   explanation=dict(head=f"{WORD.get(sig['side'])} BLOCKED", but=[text]))
        self.record(sig, res)
        return res

    def state(self, n=60):
        return dict(last=self.last, recent=[e for e in list(self.journal.recent)[:n] if e.get("event") != "TRADE_LINK"],
                    stats=self.journal.stats(), gates=GATES)
