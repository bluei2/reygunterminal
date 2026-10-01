"""
Server-side trade confirmation (one-time tokens) for MANUAL entries.

  1. POST /api/trade/prepare  {side, lot, sl_dist, tp_dist, request_id}
       -> all pre-trade checks run on the server (ARM, halted, master, lock, opposite trade, ...);
          when they pass, a random one-time token is created, bound to: account (master login), symbol, side,
          lot, SL distance, TP distance and request_id; it expires after `expiry_s` (15 s).
  2. The UI shows the plan and asks you to confirm.
  3. POST /api/trade/confirm  {token, request_id, side, lot, sl_dist, tp_dist}
       -> the token must exist, not be expired, not used, and EVERY bound value must match
          (changing any trade parameter invalidates it); it is deleted before the order is sent (one time only).
       -> TradeManager.open(request_id=...) never runs the same request_id twice (ALREADY_EXECUTED).
The browser dialog alone is not trusted: without a valid token no manual order is sent.
AUTO TRADE does not use tokens (it is server-side and gated by ARM + AUTO ON); it passes its own request_id.
"""
from __future__ import annotations

import secrets
import threading
import time

EXPIRY_S = 15.0


class ConfirmationBook:
    def __init__(self, tm, expiry_s=EXPIRY_S):
        self.tm = tm
        self.expiry_s = expiry_s
        self.tokens = {}              # token -> dict(binding, expires)
        self.lock = threading.Lock()

    def _binding(self, side, lot, sl_dist, tp_dist, request_id):
        tm = self.tm
        acct = tm.master.info.get("login") or tm.master.id
        return (str(acct), str(tm.master.symbol), int(side), round(float(lot), 2), round(float(sl_dist), 2),
                round(float(tp_dist), 2), str(request_id))

    def _purge(self):
        now = time.time()
        for k in [k for k, v in self.tokens.items() if v["expires"] < now]:
            self.tokens.pop(k, None)

    def prepare(self, side, lot, sl_dist, tp_dist, request_id):
        if not request_id:
            return dict(ok=False, error="request_id is required")
        try:
            side, lot, sl_dist, tp_dist = int(side), float(lot), float(sl_dist or 0), float(tp_dist or 0)
        except (TypeError, ValueError):
            return dict(ok=False, error="invalid order values")
        ok, why = self.tm.check_request(side, lot, sl_dist, tp_dist)
        if not ok:
            return dict(ok=False, error=why, blocked=True)
        if request_id in self.tm.processed:
            return dict(ok=False, error="REQUEST_ALREADY_PROCESSED")
        with self.lock:
            self._purge()
            token = secrets.token_urlsafe(24)
            self.tokens[token] = dict(binding=self._binding(side, lot, sl_dist, tp_dist, request_id),
                                      expires=time.time() + self.expiry_s, created=time.time())
        tk = self.tm.hub.last_tick
        price = (tk.ask if side == 1 else tk.bid) if tk else None
        preview = None
        if price:
            preview = dict(price=round(price, 2),
                           sl=round(price - side * sl_dist, 2) if sl_dist else None,
                           tp=round(price + side * tp_dist, 2) if tp_dist else None,
                           rr=round(tp_dist / sl_dist, 2) if sl_dist and tp_dist else None)
        return dict(ok=True, token=token, expires_in=self.expiry_s, request_id=request_id,
                    plan=self.tm.entry_plan(lot, side, sl_dist, tp_dist), preview=preview, paper=self.tm.paper)

    def confirm(self, token, request_id, side, lot, sl_dist, tp_dist, kind="manual"):
        if request_id and request_id in self.tm.processed:
            prev = self.tm.processed[request_id]
            return dict(prev, ok=False, error="ALREADY_EXECUTED", duplicate=True)
        with self.lock:
            self._purge()
            t = self.tokens.pop(token, None) if token else None         # one time only: removed before use
        if t is None:
            return dict(ok=False, error="confirmation expired or invalid: press BUY/SELL again")
        try:
            b = self._binding(int(side), float(lot), float(sl_dist or 0), float(tp_dist or 0), request_id)
        except (TypeError, ValueError):
            return dict(ok=False, error="invalid order values")
        if b != t["binding"]:
            return dict(ok=False, error="trade parameters changed after confirmation: press BUY/SELL again")
        life = [dict(t=t["created"], state="REQUESTED", note=kind),
                dict(t=time.time(), state="CONFIRMED", note="server token")]
        return self.tm.open(int(side), float(lot), float(sl_dist or 0), float(tp_dist or 0), kind=kind,
                            request_id=request_id, lifecycle=life)
