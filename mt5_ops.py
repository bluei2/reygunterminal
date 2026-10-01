"""
Order functions on top of the MetaTrader5 package. Used for the master account (in the main process)
and for every follower account (each in its own process, because one Python process = one MT5 terminal).

Every function takes the imported `mt5` module as its first argument and returns plain dicts.
"""
from __future__ import annotations

import math
import time

MAGIC = 20260925                 # every REYGUN SuperChart order carries this magic number
COMMENT_PREFIX = "RGSC-"         # + group id, e.g. RGSC-k3f9a

RETCODE_OK = {10008, 10009, 10010}      # placed / done / done partially
RET_INVALID_STOPS = 10016
RET_INVALID_FILL = 10030


def _err(mt5, msg):
    return dict(ok=False, error=f"{msg} {mt5.last_error()}")


def normalize_volume(info, lot: float) -> float:
    step = info.volume_step or 0.01
    v = math.floor(lot / step + 1e-9) * step
    v = max(info.volume_min, min(info.volume_max, v))
    return round(v, 8)


def _fillings(mt5, info):
    """Filling modes to try, most common first. symbol_info.filling_mode: bit 1 = FOK, bit 2 = IOC."""
    out = []
    fm = getattr(info, "filling_mode", 0) or 0
    if fm & 1:
        out.append(mt5.ORDER_FILLING_FOK)
    if fm & 2:
        out.append(mt5.ORDER_FILLING_IOC)
    out.append(mt5.ORDER_FILLING_RETURN)
    for f in (mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_IOC):
        if f not in out:
            out.append(f)
    return out


def _send(mt5, info, req):
    last = None
    for fill in _fillings(mt5, info):
        req["type_filling"] = fill
        res = mt5.order_send(req)
        last = res
        if res is None:
            continue
        if res.retcode != RET_INVALID_FILL:
            return res
    return last


def account(mt5, symbol=None) -> dict:
    a = mt5.account_info()
    t = mt5.terminal_info()
    if not a:
        return _err(mt5, "account_info failed")
    server_time = None
    if symbol:
        tk = mt5.symbol_info_tick(symbol)
        server_time = int(tk.time) if tk else None      # this account's broker clock (per-account risk day)
    return dict(ok=True, login=a.login, server=a.server, name=a.name, currency=a.currency,
                balance=a.balance, equity=a.equity, margin_free=a.margin_free,
                trade_allowed=bool(t.trade_allowed) if t else False,
                terminal_connected=bool(getattr(t, "connected", True)) if t else False,
                server_time=server_time,
                trade_expert=bool(getattr(a, "trade_expert", True)))


def broker_info(mt5, symbol) -> dict:
    """Broker / symbol execution facts for the execution guard and the broker monitor (read-only)."""
    t0 = time.perf_counter()
    info = mt5.symbol_info(symbol)
    tick = mt5.symbol_info_tick(symbol)
    term = mt5.terminal_info()
    acc = mt5.account_info()
    if not info:
        return _err(mt5, f"symbol {symbol} not found")
    fm = getattr(info, "filling_mode", 0) or 0
    return dict(ok=True, broker=getattr(acc, "company", "") if acc else "", server=getattr(acc, "server", "") if acc else "",
                symbol=symbol, selected=bool(getattr(info, "select", True)), digits=info.digits, point=info.point,
                min_lot=info.volume_min, max_lot=info.volume_max, lot_step=info.volume_step or 0.01,
                stops_level=int(getattr(info, "trade_stops_level", 0) or 0),
                freeze_level=int(getattr(info, "trade_freeze_level", 0) or 0),
                filling_mode=("FOK " if fm & 1 else "") + ("IOC " if fm & 2 else "") + "RETURN",
                trade_mode=int(getattr(info, "trade_mode", 4)),
                contract_size=float(getattr(info, "trade_contract_size", 0) or 0) or None,
                bid=tick.bid if tick else None, ask=tick.ask if tick else None,
                spread=round(tick.ask - tick.bid, info.digits) if tick else None,
                tick_time=int(tick.time) if tick else None,
                connected=bool(getattr(term, "connected", True)) if term else False,
                trade_allowed=bool(term.trade_allowed) if term else False,
                ping_ms=round((getattr(term, "ping_last", 0) or 0) / 1000, 1) if term else None,
                margin_free=float(acc.margin_free) if acc else None,
                call_ms=round((time.perf_counter() - t0) * 1000, 1))


def positions(mt5, symbol=None, magic_only=True):
    """List of our positions. On an MT5 error returns an error dict (NOT an empty list), so callers never
    mistake a failed read for 'all positions closed'."""
    ps = mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()
    if ps is None:
        return _err(mt5, "positions_get failed")
    out = []
    for p in ps:
        if magic_only and p.magic != MAGIC:
            continue
        out.append(dict(ticket=int(p.ticket), symbol=p.symbol, side=1 if p.type == 0 else -1,
                        volume=float(p.volume), open=float(p.price_open), sl=float(p.sl), tp=float(p.tp),
                        price=float(p.price_current), pnl=float(p.profit) + float(getattr(p, "swap", 0.0)),
                        magic=int(p.magic), comment=str(p.comment or "")))
    return out


def open_market(mt5, symbol, side, lot, sl_dist=0.0, tp_dist=0.0, comment="") -> dict:
    """Market order. SL/TP are DISTANCES in price from this account's own fill price (0 = none)."""
    if not mt5.symbol_select(symbol, True):
        return _err(mt5, f"symbol {symbol} not available")
    info = mt5.symbol_info(symbol)
    tick = mt5.symbol_info_tick(symbol)
    if not info or not tick:
        return _err(mt5, f"no price for {symbol}")
    vol = normalize_volume(info, lot)
    price = tick.ask if side == 1 else tick.bid
    digits = info.digits
    min_stop = (getattr(info, "trade_stops_level", 0) or 0) * info.point

    def stops(p):
        sl = tp = 0.0
        if sl_dist > 0:
            sl = round(p - side * max(sl_dist, min_stop), digits)
        if tp_dist > 0:
            tp = round(p + side * max(tp_dist, min_stop), digits)
        return sl, tp

    sl, tp = stops(price)
    req = dict(action=mt5.TRADE_ACTION_DEAL, symbol=symbol, volume=vol,
               type=mt5.ORDER_TYPE_BUY if side == 1 else mt5.ORDER_TYPE_SELL,
               price=price, sl=sl, tp=tp, deviation=50, magic=MAGIC, comment=comment[:31],
               type_time=mt5.ORDER_TIME_GTC)
    res = _send(mt5, info, dict(req))
    if res is not None and res.retcode == RET_INVALID_STOPS and (sl or tp):
        # some brokers refuse stops on market orders: open naked, then attach SL/TP
        req.update(sl=0.0, tp=0.0)
        res = _send(mt5, info, dict(req))
        if res is not None and res.retcode in RETCODE_OK:
            fill = res.price or price
            sl, tp = stops(fill)
            ticket = _ticket_from_result(mt5, res, symbol, comment)
            if ticket:
                modify(mt5, ticket, sl, tp)
    if res is None:
        return _err(mt5, "order_send returned None")
    if res.retcode not in RETCODE_OK:
        return dict(ok=False, error=f"retcode {res.retcode} {res.comment}")
    ticket = _ticket_from_result(mt5, res, symbol, comment)
    return dict(ok=True, ticket=ticket, volume=vol, price=res.price or price, sl=sl, tp=tp)


def _ticket_from_result(mt5, res, symbol, comment):
    """res.order is usually the position ticket for a market deal; confirm via positions_get."""
    for p in mt5.positions_get(symbol=symbol) or []:
        if p.ticket == res.order or (p.magic == MAGIC and p.comment == comment[:31]):
            return int(p.ticket)
    return int(res.order) if res.order else None


def _position(mt5, ticket):
    ps = mt5.positions_get(ticket=ticket)
    return ps[0] if ps else None


def close(mt5, ticket, volume=None) -> dict:
    p = _position(mt5, ticket)
    if p is None:
        return dict(ok=True, closed=0.0, note="already closed")
    info = mt5.symbol_info(p.symbol)
    tick = mt5.symbol_info_tick(p.symbol)
    vol = p.volume if volume is None else min(p.volume, normalize_volume(info, volume))
    if vol <= 0:
        return dict(ok=False, error="volume too small")
    long_ = p.type == 0
    req = dict(action=mt5.TRADE_ACTION_DEAL, symbol=p.symbol, volume=vol, position=int(ticket),
               type=mt5.ORDER_TYPE_SELL if long_ else mt5.ORDER_TYPE_BUY,
               price=tick.bid if long_ else tick.ask, deviation=50, magic=MAGIC,
               comment=str(p.comment or "")[:31], type_time=mt5.ORDER_TIME_GTC)
    res = _send(mt5, info, req)
    if res is None:
        return _err(mt5, "close failed")
    if res.retcode not in RETCODE_OK:
        return dict(ok=False, error=f"retcode {res.retcode} {res.comment}")
    return dict(ok=True, closed=vol, price=res.price)


def modify(mt5, ticket, sl, tp) -> dict:
    p = _position(mt5, ticket)
    if p is None:
        return dict(ok=False, error="position not found")
    info = mt5.symbol_info(p.symbol)
    d = info.digits if info else 2
    req = dict(action=mt5.TRADE_ACTION_SLTP, symbol=p.symbol, position=int(ticket),
               sl=round(sl or 0.0, d), tp=round(tp or 0.0, d), magic=MAGIC)
    res = mt5.order_send(req)
    if res is None:
        return _err(mt5, "modify failed")
    if res.retcode not in RETCODE_OK and res.retcode != 10025:       # 10025 = no changes
        return dict(ok=False, error=f"retcode {res.retcode} {res.comment}")
    return dict(ok=True)


def close_all(mt5, scope="superchart") -> dict:
    """scope 'superchart' = only positions with our magic number, 'all' = every position on the account."""
    ps = mt5.positions_get() or []
    done, errors = 0, []
    for p in ps:
        if scope != "all" and p.magic != MAGIC:
            continue
        r = close(mt5, p.ticket)
        if r.get("ok"):
            done += 1
        else:
            errors.append(r.get("error"))
    return dict(ok=not errors, closed=done, errors=errors)


DEAL_REASONS = {0: "Manual", 1: "Manual (mobile)", 2: "Manual (web)", 3: "Closed by app/EA", 4: "SL", 5: "TP",
                6: "Stop out", 7: "Rollover", 8: "Variation margin", 9: "Split"}


def closed_info(mt5, ticket) -> dict:
    """How a position ended, from the deal history: exit price, reason (SL / TP / Manual / ...), total P/L."""
    deals = mt5.history_deals_get(position=int(ticket))
    if deals is None:
        return _err(mt5, "history_deals_get failed")
    if not deals:
        return dict(ok=False, error="no deals yet")
    exits = [d for d in deals if getattr(d, "entry", 0) in (1, 3)]          # DEAL_ENTRY_OUT / OUT_BY
    if not exits:
        return dict(ok=False, error="position still open")
    last = sorted(exits, key=lambda d: d.time_msc if hasattr(d, "time_msc") else d.time)[-1]
    pnl = sum(float(d.profit) + float(getattr(d, "commission", 0)) + float(getattr(d, "swap", 0)) +
              float(getattr(d, "fee", 0)) for d in deals)
    return dict(ok=True, exit=float(last.price), reason=DEAL_REASONS.get(int(getattr(last, "reason", 0)), "Other"),
                pnl=round(pnl, 2), time=int(last.time), partial_exits=len(exits))


def price(mt5, symbol) -> dict:
    t = mt5.symbol_info_tick(symbol)
    if not t:
        return _err(mt5, f"no tick for {symbol}")
    return dict(ok=True, bid=t.bid, ask=t.ask, time_msc=int(t.time_msc))


# ---------------------------------------------------------------- pending orders (News Trader)
def place_pending(mt5, symbol, side, lot, price, sl=0.0, tp=0.0, comment="") -> dict:
    """BUY STOP (side 1) above the market or SELL STOP (side -1) below it. Prices are absolute."""
    if not mt5.symbol_select(symbol, True):
        return _err(mt5, f"symbol {symbol} not available")
    info = mt5.symbol_info(symbol)
    tick = mt5.symbol_info_tick(symbol)
    if not info or not tick:
        return _err(mt5, f"no price for {symbol}")
    d = info.digits
    min_stop = (getattr(info, "trade_stops_level", 0) or 0) * info.point
    price = round(price, d)
    if side == 1 and price <= tick.ask + min_stop:
        return dict(ok=False, error=f"buy stop {price} is not above ask {tick.ask} (+stops level)")
    if side == -1 and price >= tick.bid - min_stop:
        return dict(ok=False, error=f"sell stop {price} is not below bid {tick.bid} (+stops level)")
    req = dict(action=mt5.TRADE_ACTION_PENDING, symbol=symbol, volume=normalize_volume(info, lot),
               type=mt5.ORDER_TYPE_BUY_STOP if side == 1 else mt5.ORDER_TYPE_SELL_STOP,
               price=price, sl=round(sl or 0.0, d), tp=round(tp or 0.0, d), deviation=50, magic=MAGIC,
               comment=comment[:31], type_time=mt5.ORDER_TIME_GTC)
    res = _send(mt5, info, req)
    if res is None:
        return _err(mt5, "pending order_send returned None")
    if res.retcode not in RETCODE_OK:
        return dict(ok=False, error=f"retcode {res.retcode} {res.comment}")
    return dict(ok=True, ticket=int(res.order), price=price, volume=req["volume"], sl=req["sl"], tp=req["tp"])


def orders(mt5, symbol=None):
    """Our pending orders (magic only). Error dict on MT5 failure, like positions()."""
    os_ = mt5.orders_get(symbol=symbol) if symbol else mt5.orders_get()
    if os_ is None:
        return _err(mt5, "orders_get failed")
    out = []
    for o in os_:
        if o.magic != MAGIC:
            continue
        side = 1 if o.type in (mt5.ORDER_TYPE_BUY_STOP, getattr(mt5, "ORDER_TYPE_BUY_LIMIT", 2)) else -1
        out.append(dict(ticket=int(o.ticket), symbol=o.symbol, side=side, price=float(o.price_open),
                        sl=float(o.sl), tp=float(o.tp), volume=float(o.volume_current), comment=str(o.comment or "")))
    return out


def cancel_order(mt5, ticket) -> dict:
    res = mt5.order_send(dict(action=mt5.TRADE_ACTION_REMOVE, order=int(ticket), magic=MAGIC))
    if res is None:
        return _err(mt5, "cancel failed")
    if res.retcode not in RETCODE_OK:
        return dict(ok=False, error=f"retcode {res.retcode} {res.comment}")
    return dict(ok=True)


# ---------------------------------------------------------------- connection test (sends NO order)
def connection_check(mt5, symbol, lot=0.01) -> dict:
    """Everything needed to trade, checked without trading: account, Algo Trading, symbol, price, and MT5's own
    order_check() for a BUY of `lot` (validates margin / volume / trade permissions but does NOT send an order)."""
    out = dict(ok=False, checks=[])

    def add(name, ok, detail):
        out["checks"].append(dict(name=name, ok=bool(ok), detail=str(detail)))
        return ok

    a = mt5.account_info()
    t = mt5.terminal_info()
    if not add("Account", a is not None, f"#{a.login} {a.server} · {a.name}" if a else f"no account {mt5.last_error()}"):
        return out
    out.update(login=a.login, server=a.server, balance=a.balance, equity=a.equity, currency=a.currency)
    add("Balance", True, f"{a.balance:,.2f} {a.currency} (equity {a.equity:,.2f})")
    add("Algo Trading (terminal)", t is not None and t.trade_allowed,
        "ON" if t is not None and t.trade_allowed else "OFF - press the Algo Trading button in this MT5")
    add("Trading allowed (account)", getattr(a, "trade_expert", True),
        "yes" if getattr(a, "trade_expert", True) else "broker does not allow EA / API trading on this account")
    if not mt5.symbol_select(symbol, True):
        sug = find_symbols(mt5, symbol, 5)
        names = ", ".join(x["name"] for x in sug.get("symbols", [])) if sug.get("ok") else ""
        add("Symbol", False, f"{symbol} not found on this broker" +
            (f" - did you mean: {names} (use 🔍 next to Symbol)" if names else " - check the symbol name"))
        out["suggest"] = sug.get("best") if sug.get("ok") else None
        return out
    add("Symbol", True, symbol)
    info = mt5.symbol_info(symbol)
    tick = mt5.symbol_info_tick(symbol)
    if not add("Price", tick is not None and tick.bid > 0, f"bid {tick.bid} ask {tick.ask} · spread "
               f"{round((tick.ask - tick.bid), info.digits if info else 2)}" if tick else "no tick (market closed?)"):
        return out
    mode = getattr(info, "trade_mode", 4)
    add("Symbol trade mode", mode == 4 or mode is None, "full trading" if mode == 4 else
        {0: "DISABLED", 1: "long only", 2: "short only", 3: "close only"}.get(mode, str(mode)))
    vol = normalize_volume(info, lot)
    add("Lot size", abs(vol - lot) < 1e-9, f"{lot} → {vol} (min {info.volume_min}, step {info.volume_step})")
    check = getattr(mt5, "order_check", None)
    if check is not None:
        req = dict(action=mt5.TRADE_ACTION_DEAL, symbol=symbol, volume=vol, type=mt5.ORDER_TYPE_BUY,
                   price=tick.ask, deviation=50, magic=MAGIC, comment="RGSC-check", type_time=mt5.ORDER_TIME_GTC)
        res = None
        for fill in _fillings(mt5, info):
            req["type_filling"] = fill
            res = check(req)
            if res is not None and res.retcode != RET_INVALID_FILL:
                break
        ok = res is not None and res.retcode in (0, 10009)
        margin = getattr(res, "margin", None)
        add("Order check (not sent)", ok, (f"OK · margin needed {margin:.2f}" if margin is not None else "OK") if ok
            else f"retcode {getattr(res, 'retcode', '?')} {getattr(res, 'comment', mt5.last_error())}")
    out["ok"] = all(c["ok"] for c in out["checks"])
    return out


# ---------------------------------------------------------------- symbol finder (prefix / suffix per broker)
import re as _re

ALIASES = {"XAUUSD": ["GOLD"], "GOLD": ["XAUUSD"], "XAGUSD": ["SILVER"], "SILVER": ["XAGUSD"],
           "US30": ["DJ30", "DJI", "WS30", "DOWJONES"], "NAS100": ["USTEC", "NDX", "US100"],
           "SPX500": ["US500", "SP500"], "BTCUSD": ["BITCOIN", "BTC"]}


def base_of(symbol: str) -> str:
    """The core of a broker symbol: 'XAUUSD_ph' -> 'XAUUSD', '#XAUUSD.m' -> 'XAUUSD', 'GOLDmicro' -> 'GOLD'."""
    m = _re.search(r"[A-Z0-9]{3,}", symbol or "")
    return m.group(0) if m else (symbol or "").upper()


def find_symbols(mt5, base="XAUUSD", limit=15) -> dict:
    """Every symbol on this terminal whose name contains the base (or an alias like GOLD), best match first."""
    base = base_of(base) if base else "XAUUSD"
    keys = [base] + ALIASES.get(base, [])
    allsyms = mt5.symbols_get()
    if allsyms is None:
        return _err(mt5, "symbols_get failed")
    out = []
    for s in allsyms:
        name = s.name
        norm = _re.sub(r"[^A-Z0-9]", "", name.upper())
        hit = next((k for k in keys if k in norm), None)
        if not hit:
            continue
        extra = len(norm) - len(hit)
        mode = getattr(s, "trade_mode", 4)
        score = (0 if hit == base else 1, 0 if mode == 4 else 1, extra, 0 if getattr(s, "visible", True) else 1, len(name))
        out.append(dict(name=name, description=getattr(s, "description", ""), trade_mode=mode,
                        tradable=mode == 4, visible=bool(getattr(s, "visible", True)), score=score))
    out.sort(key=lambda x: x["score"])
    for x in out:
        x.pop("score")
    return dict(ok=True, base=base, symbols=out[:limit], best=out[0]["name"] if out else None)
