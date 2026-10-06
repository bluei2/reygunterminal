"""SIGNAL TEST (v2.7.9): a real order that can never fill, on every account, then deleted."""
from types import SimpleNamespace as NS

import mt5_ops
from harness import make_tm


class FakeMT5:
    TRADE_ACTION_PENDING, TRADE_ACTION_REMOVE = 5, 8
    ORDER_TYPE_BUY_LIMIT, ORDER_TIME_GTC = 2, 0
    ORDER_FILLING_FOK, ORDER_FILLING_IOC, ORDER_FILLING_RETURN = 0, 1, 2

    def __init__(self, closed=False, stuck=False):
        self.closed, self.stuck = closed, stuck
        self.book, self.sent = {}, []

    def account_info(self):
        return NS(login=89135371, server="LiteFinance-MT5")

    def last_error(self):
        return (0, "ok")

    def symbol_select(self, s, on):
        return True

    def symbol_info(self, s):
        return NS(digits=2, volume_min=0.01, volume_max=100, volume_step=0.01, filling_mode=2)

    def symbol_info_tick(self, s):
        return NS(bid=4000.0, ask=4000.3)

    def order_send(self, req):
        self.sent.append(dict(req))
        if req["action"] == self.TRADE_ACTION_REMOVE:
            if self.stuck:
                return NS(retcode=10006, comment="rejected")
            self.book.pop(req["order"], None)
            return NS(retcode=10009, comment="done", order=req["order"])
        if self.closed:
            return NS(retcode=10018, comment="Market closed", order=0)
        t = 555 + len(self.book)
        self.book[t] = req
        return NS(retcode=10009, comment="done", order=t)

    def orders_get(self, ticket=None):
        return (NS(ticket=ticket),) if ticket in self.book else ()


def test_send_never_fills_and_remove_deletes():
    m = FakeMT5()
    r = mt5_ops.signal_test_send(m, "XAUUSD_ph")
    assert r["ok"], r
    req = m.sent[0]
    assert req["type"] == m.ORDER_TYPE_BUY_LIMIT and req["volume"] == 0.01
    assert req["price"] == 3200.0 and req["comment"] == mt5_ops.TEST_COMMENT and req["magic"] == mt5_ops.MAGIC
    rm = mt5_ops.signal_test_remove(m, r["ticket"])
    assert rm["ok"] and not m.book


def test_market_closed_is_reported():
    r = mt5_ops.signal_test_send(FakeMT5(closed=True), "XAUUSD_ph")
    assert not r["ok"] and r["market_closed"]
    assert "MARKET CLOSED" in r["checks"][-1]["detail"]


def test_failed_delete_tells_user_to_remove_it():
    m = FakeMT5(stuck=True)
    r = mt5_ops.signal_test_send(m, "XAUUSD_ph")
    rm = mt5_ops.signal_test_remove(m, r["ticket"])
    assert not rm["ok"] and "MANO-MANO" in rm["check"]["detail"]


def test_manager_runs_on_every_account_and_leaves_nothing():
    hub, tm = make_tm(1)
    r = tm.signal_test(hold=0)
    assert r["ok"], r
    assert len(r["results"]) == 2 and all(x["ok"] for x in r["results"])
    for a in tm.all_accounts():
        assert not a.backend.pending_, a.name
        names = [c["name"] for c in next(x for x in r["results"] if x["id"] == a.id)["checks"]]
        assert names[-1] == "Delete test order"


def test_refused_while_halted():
    hub, tm = make_tm(1)
    tm.halted = "test halt"
    r = tm.signal_test(hold=0)
    assert not r["ok"] and "HALT" in r["error"]
    assert tm.halted == "test halt"                       # a halt is never cleared by a test


def test_leftover_test_order_is_swept():
    hub, tm = make_tm(1)
    f = tm.followers[0]
    r = f.backend.call("signal_test_send", f.symbol)          # a test order whose delete never happened
    t = r["ticket"]
    tm.refresh()
    assert t in f.backend.pending_                            # younger than 30 s: left alone
    f.test_seen[t] -= 31
    tm.refresh()
    assert t not in f.backend.pending_ and any("naiwang test order" in n[1] for n in hub.notes)
    other = f.backend.call("place_pending", f.symbol, 1, 0.01, 99999.0, 0.0, 0.0, "RGSC-Nabc")["ticket"]
    f.test_seen = {other: 0}
    tm.refresh()
    assert other in f.backend.pending_                        # any other order is never touched
