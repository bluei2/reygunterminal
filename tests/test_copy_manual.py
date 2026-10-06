"""COPY MANUAL TRADES: a trade opened by hand on the master MT5 (magic 0, master symbol) is copied to followers."""
import mt5_ops
from harness import cycle, make_tm


def _manual(tm, side=1, sl_d=5.0, tp_d=10.0, magic=0, symbol=None, vol=0.05):
    b = tm.master.backend
    sym = symbol or tm.master.symbol
    r = b.c_open_market(sym, side, vol, sl_d, tp_d, "")
    b.positions_[r["ticket"]]["magic"] = magic
    return r["ticket"]


def _tm():
    hub, tm = make_tm(1)
    tm._manual_seen = None               # the harness loop ran once before it was stopped: start like a fresh app
    return hub, tm


def _setup():
    hub, tm = _tm()
    tm.set_armed(True)
    tm.refresh()
    tm.copy_manual()                     # first read: marks what is already open
    return hub, tm


def test_manual_trade_copied_and_close_mirrored():
    hub, tm = _setup()
    t = _manual(tm, side=-1)
    tm.refresh()
    tm.copy_manual()
    gs = [g for g in tm.groups.values() if g.kind == "copy"]
    assert len(gs) == 1, hub.notes
    g = gs[0]
    assert g.legs["master"] == t and "f1" in g.legs and g.side == -1
    fp = tm.followers[0].backend.positions_[g.legs["f1"]]
    assert fp["volume"] == 0.01 and fp["side"] == -1
    assert abs((fp["sl"] - fp["open"]) - 5) < 0.02 and abs((fp["open"] - fp["tp"]) - 10) < 0.02, fp
    tm.copy_manual()                      # never copied twice
    assert len([g for g in tm.groups.values() if g.kind == "copy"]) == 1
    g.created -= 20                       # past the 5 s settle time
    tm.master.backend.c_close(t)          # you close it by hand on the master
    cycle(tm, 3)
    assert g.legs["f1"] not in tm.followers[0].backend.positions_


def test_partial_and_sl_move_mirrored():
    hub, tm = _setup()
    t = _manual(tm, side=1, vol=0.04)
    tm.refresh(); tm.copy_manual()
    g = [g for g in tm.groups.values() if g.kind == "copy"][0]
    g.created -= 20
    mp = tm.master.backend.positions_[t]
    mp["sl"] = mp["open"] - 2.0                            # you move the SL on the master
    cycle(tm, 2)
    fp = tm.followers[0].backend.positions_[g.legs["f1"]]
    assert abs((fp["open"] - fp["sl"]) - 2.0) < 0.05, fp
    tm.master.backend.c_close(t, 0.02)                     # half closed on the master -> half on the follower
    cycle(tm, 2)
    fp = tm.followers[0].backend.positions_.get(g.legs["f1"])
    assert fp is None or fp["volume"] <= 0.01, fp


def test_ignored_ea_other_symbol_and_preexisting():
    hub, tm = _tm()
    tm.set_armed(True)
    old = _manual(tm)                                      # already open when the app started
    tm.refresh(); tm.copy_manual()
    tm.cfg["copy_magics"] = "26200001-26200004"            # only the REYGUN Master EA chosen:
    _manual(tm, magic=123456)                              # another EA is then not copied
    _manual(tm, symbol="GBPJPY")                           # another symbol
    tm.refresh(); tm.copy_manual()
    assert not [g for g in tm.groups.values() if g.kind == "copy"], hub.notes
    assert old not in [g.legs.get("master") for g in tm.groups.values()]


def test_switch_off_and_superchart_trade_not_double_copied():
    hub, tm = _setup()
    r = tm.open(1, 0.01, 5, 10)                            # a normal SuperChart trade (magic = ours)
    assert r.get("ok"), r
    tm.refresh(); tm.copy_manual()
    assert not [g for g in tm.groups.values() if g.kind == "copy"]
    tm.cfg["copy_manual"] = False
    _manual(tm)
    tm.refresh(); tm.copy_manual()
    assert not [g for g in tm.groups.values() if g.kind == "copy"]


def test_copied_even_when_not_armed():
    hub, tm = _setup()
    tm.set_armed(False)                                    # you never touched the terminal
    _manual(tm)
    tm.refresh(); tm.copy_manual()
    assert [g for g in tm.groups.values() if g.kind == "copy"], hub.notes


def test_locked_master_not_copied():
    hub, tm = _setup()
    tm.cfg["copy_when_locked"] = False
    tm.master.locked = "daily DD"
    _manual(tm)
    tm.refresh(); tm.copy_manual()
    assert not [g for g in tm.groups.values() if g.kind == "copy"]
    assert any("hindi kinopya" in n[1] for n in hub.notes)


def test_magic_constant():
    assert mt5_ops.MAGIC != 0


def test_every_ea_trade_on_the_master_is_copied():
    hub, tm = _setup()
    t = _manual(tm, side=1, magic=26200003)                       # Master EA CONSO fill on the master
    tm.refresh(); tm.copy_manual()
    gs = [g for g in tm.groups.values() if g.kind == "copy"]
    assert len(gs) == 1 and gs[0].legs["master"] == t and "f1" in gs[0].legs, hub.notes[-3:]
    assert any("COPY EA 26200003" in n[1] for n in hub.notes)
    e = tm.journal.entries[gs[0].gid]
    assert e["kind"] == "ea_mt5" and e["source_magic"] == 26200003
    _manual(tm, side=-1, magic=555)                               # any other EA too (default: all)
    tm.refresh(); tm.copy_manual()
    assert len([g for g in tm.groups.values() if g.kind == "copy"]) == 2


def test_copy_magics_limits_and_copy_ea_off():
    hub, tm = _setup()
    tm.cfg["copy_magics"] = "26200001-26200004"
    _manual(tm, side=1, magic=555)
    tm.refresh(); tm.copy_manual()
    assert not [g for g in tm.groups.values() if g.kind == "copy"]
    tm.cfg["copy_ea"] = False
    _manual(tm, side=1, magic=26200001)
    tm.refresh(); tm.copy_manual()
    assert not [g for g in tm.groups.values() if g.kind == "copy"]
    _manual(tm, side=1, magic=0)                                  # manual still copied
    tm.refresh(); tm.copy_manual()
    assert len([g for g in tm.groups.values() if g.kind == "copy"]) == 1


def test_our_own_terminal_trades_are_not_copied_twice():
    hub, tm = _setup()
    r = tm.open(1, 0.01, 5.0, 10.0, kind="manual")                # a terminal trade: already master + followers
    assert r.get("ok"), r
    tm.refresh(); tm.copy_manual()
    assert not [g for g in tm.groups.values() if g.kind == "copy"]
    assert len(tm.followers[0].backend.positions_) == 1


def test_parse_magics():
    import trading
    assert trading.parse_magics("26200001-26200004, 777;x, 9-3") == [(26200001, 26200004), (777, 777)]
    assert trading.parse_magics("") == []


def test_locked_master_still_copied_by_default():
    """v2.8.2: the EA keeps trading on the master after the app's daily lock -> the followers still follow it."""
    hub, tm = _setup()
    tm.master.locked = "DAILY TP hit (+61.00)"
    t = _manual(tm, side=1, magic=26200001)
    tm.refresh(); tm.copy_manual()
    gs = [g for g in tm.groups.values() if g.kind == "copy"]
    assert len(gs) == 1 and gs[0].legs["master"] == t and "f1" in gs[0].legs, hub.notes[-3:]


def test_not_copied_trade_is_explained_once():
    hub, tm = _setup()
    tm.cfg["copy_magics"] = "26200001-26200004"
    _manual(tm, magic=555)
    tm.refresh(); tm.copy_manual(); tm.refresh(); tm.copy_manual()
    msgs = [n[1] for n in hub.notes if "HINDI kinopya" in n[1]]
    assert len(msgs) == 1 and "magic 555" in msgs[0], msgs


def test_preexisting_trade_is_reported():
    hub, tm = _tm()
    _manual(tm, magic=26200002)
    tm.refresh(); tm.copy_manual()
    assert any("BAGO nag-start" in n[1] for n in hub.notes), hub.notes[-3:]
