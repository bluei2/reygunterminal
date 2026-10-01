"""
COMPANION VIEW - one read-only snapshot for the mobile page (/m) and, later, for a cloud companion app.

It shows at a glance:
  * safety: MASTER, ARMED, paper / live, AUTO TRADE on / off
  * the score: bias, OVERALL vs passing, trigger, groups, and what pulls it down
  * the ACTIVE indicators: every indicator with CHART and / or SCORE on, its timeframes and its vote right now
    (agrees / against / neutral with the bias), analyser modules (P/D, liquidity, prev-day) and strategies that are ON
  * open trades (master side), active forecasts, the last AI answer and recent events

Read-only by construction: it copies values out of the hub and never calls anything that trades or changes
settings. Secrets are redacted and account paths / logins are left out.
"""
import time

MODULE_ITEMS = {"premium_discount": ["Premium/Discount"], "liquidity": ["Liquidity sweep"],
                "prev_day": ["Prev-day sweep"]}
MODULE_DEFAULT_TFS = {"premium_discount": ["H1"], "liquidity": ["M15", "M5"], "prev_day": ["D1"]}


def _redact(text):
    try:
        from error_log import redact
        return redact(str(text))
    except Exception:
        return str(text)


def _items_by_name(read):
    out = {}
    for g in (getattr(read, "groups", None) or []):
        for it in g["items"]:
            out[it["name"]] = dict(it, category=g["category"])
    return out


def _state_of(d, bias):
    if d is None:
        return "none"
    if not bias:
        return "neutral" if d == 0 else ("up" if d == 1 else "down")
    return "agree" if d == bias else "against" if d == -bias else "neutral"


def indicators(hub, read=None):
    """Active indicators (CHART or SCORE on) and strategies that are ON, with their votes right now."""
    read = read if read is not None else getattr(hub, "read", None)
    bias = getattr(read, "bias", 0) or 0
    names = _items_by_name(read)
    by_plugin = getattr(hub, "votes_by_plugin", {}) or {}
    try:
        mod_tfs = hub.module_tfs() if hasattr(hub, "module_tfs") else {}
    except Exception:
        mod_tfs = {}
    out, strat = [], []
    total = chart_n = score_n = 0
    for p in getattr(hub, "plugins", []):
        if p.kind != "indicator":
            if p.enabled:
                strat.append(dict(name=p.name, error=bool(p.error)))
            continue
        total += 1
        chart_n += bool(p.enabled)
        score_n += bool(p.scoring)
        if not (p.enabled or p.scoring):
            continue
        module = getattr(p.obj, "analyser_module", None)
        votes = []
        if p.scoring and not p.error:
            if module:
                votes = [dict(name=n, dir=names[n]["dir"], reason=names[n].get("reason", ""))
                         for n in MODULE_ITEMS.get(module, []) if n in names]
            else:
                votes = list(by_plugin.get(p.key, []))
        for v in votes:
            v["state"] = _state_of(v["dir"], bias)
            v["reason"] = _redact(v.get("reason", ""))[:160]
        tfs = list(getattr(p, "chart_tfs", []) or [])
        if module:                                   # analyser modules score on their own structure timeframes
            tfs = (mod_tfs.get(module) or MODULE_DEFAULT_TFS.get(module, [])) if p.scoring else tfs
        elif not tfs and p.votes:
            tfs = [getattr(p.obj, "vote_tf", "M15")]
        states = {v["state"] for v in votes}
        overall = ("error" if p.error else "off" if not p.scoring else "agree" if states == {"agree"}
                   else "against" if "against" in states and "agree" not in states
                   else "mixed" if len(states) > 1 else (next(iter(states)) if states else "none"))
        out.append(dict(key=p.key, name=p.name, category=getattr(p.obj, "category", "Other"),
                        chart=bool(p.enabled), score=bool(p.scoring), tfs=tfs, module=module,
                        error=_redact(p.error)[:120] if p.error else None, votes=votes, state=overall))
    order = {"agree": 0, "mixed": 1, "neutral": 2, "against": 3, "none": 4, "off": 5, "error": 6}
    out.sort(key=lambda x: (not x["score"], x["category"], order.get(x["state"], 9), x["name"]))
    return dict(items=out, strategies=strat, counts=dict(total=total, chart=chart_n, score=score_n,
                                                         active=len(out)))


def snapshot(hub):
    """Everything the companion page shows. Pure read."""
    src = hub.src
    tk = getattr(hub, "last_tick", None)
    r = getattr(hub, "read", None)
    snap = dict(t=int(time.time()), symbol=src.symbol, source=getattr(src, "name", ""), live=bool(src.live),
                status=getattr(src, "status", ""),
                price=dict(bid=tk.bid, ask=tk.ask, spread=round(tk.ask - tk.bid, 2)) if tk else None,
                read_only=True)
    # ---- safety
    tr = getattr(hub, "trader", None)
    risk = getattr(hub, "risk", None)
    at = getattr(hub, "autotrader", None)
    snap["safety"] = dict(master=bool(getattr(risk, "master_enabled", True)) if risk else None,
                          armed=bool(tr.armed) if tr else False, paper=bool(tr.paper) if tr else True,
                          halted=bool(getattr(tr, "halted", False)) if tr else False,
                          auto=bool(at.enabled) if at else False, trading=tr is not None)
    # ---- score
    sc = getattr(hub, "scoring", None) or {}
    if r is not None:
        snap["score"] = dict(bias={1: "BULL", -1: "BEAR"}.get(r.bias, "NONE"), overall=r.overall, passing=r.passing,
                             passed=r.passed, trigger=r.trigger, verdict=r.verdict,
                             groups=[dict(category=g["category"], pct=g["pct"], agree=g["agree"], total=g["total"],
                                          weight=g["weight"]) for g in r.groups],
                             lost=list(getattr(r, "lost", []))[:6], alt=getattr(r, "alt", {}),
                             neutral=sc.get("neutral", "against"), mode=sc.get("mode", "group"))
        st = getattr(hub, "states", {}) or {}
        snap["tfs"] = {tf: {1: "BULL", -1: "BEAR"}.get(s.trend, "FLAT") for tf, s in st.items() if s is not None}
    else:
        snap["score"], snap["tfs"] = None, {}
    snap["indicators"] = indicators(hub, r)
    # ---- trades (master side only; no account paths / logins)
    if tr is not None:
        try:
            ts = tr.state()
            m = ts.get("master") or {}
            snap["account"] = dict(balance=m.get("balance"), equity=m.get("equity"), day_pl=m.get("day_pl"),
                                   connected=m.get("connected"), followers=len(ts.get("followers") or []))
            snap["trades"] = [dict(side=g["side"], volume=g["volume"], open=g["open"], sl=g["sl"], tp=g["tp"],
                                   pnl=g["pnl"], state=g["state"], legs=len(g["legs"])) for g in ts.get("groups", [])]
        except Exception as e:
            snap["account"], snap["trades"] = None, []
            snap["trades_error"] = _redact(e)[:120]
    else:
        snap["account"], snap["trades"] = None, []
    # ---- forecasts
    fs = getattr(hub, "forecast", None)
    try:
        snap["forecasts"] = [dict(id=f["id"], setup=f.get("setup_type", "structure"), direction=f["direction"],
                                  status=f["status"], entry=f["entry"], sl=f["sl"], tp=f["tp2"],
                                  confidence=f.get("confidence")) for f in list(fs.engine.active.values())] if fs else []
    except Exception:
        snap["forecasts"] = []
    # ---- last AI answer (text only)
    ai = getattr(hub, "ai", None)
    last = getattr(ai, "last", None) if ai else None
    snap["ai"] = dict(mode=last.get("mode"), t=last.get("t"), ok=last.get("ok"), model=last.get("model"),
                      text=_redact(last.get("answer") or last.get("error") or "")[:1500]) if last else None
    # ---- recent events
    ev = list(getattr(hub, "events", []))[-15:]
    snap["events"] = [dict(kind=e.get("kind"), text=_redact(e.get("text", ""))[:200], wall=e.get("wall"))
                      for e in reversed(ev)]
    return snap
