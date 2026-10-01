"""Companion view (/m): active indicators with their votes, read-only, no secrets."""
import ast
import json

from harness import cycle, make_tm

import companion as C


def _hub():
    import test_plugins as TP
    h = TP.hub()
    ind = [p for p in h.plugins if p.kind == "indicator"]
    for p in ind:
        h.set_plugin(p.key, chart=False, score=False)
    voters = [p for p in ind if p.votes and not getattr(p.obj, "analyser_module", None)][:3]
    for p in voters:
        h.set_plugin(p.key, chart=True, score=True)
    chart_only = next(p for p in ind if p not in voters)
    h.set_plugin(chart_only.key, chart=True, score=False)
    h.analyze()
    return h, voters, chart_only


def test_lists_only_active_indicators_with_votes():
    h, voters, chart_only = _hub()
    I = C.indicators(h)
    keys = {x["key"] for x in I["items"]}
    assert keys == {p.key for p in voters} | {chart_only.key}, keys
    assert I["counts"]["score"] == 3 and I["counts"]["active"] == 4
    co = next(x for x in I["items"] if x["key"] == chart_only.key)
    assert co["chart"] and not co["score"] and co["state"] == "off" and not co["votes"]
    for x in I["items"]:
        if x["score"]:
            assert x["tfs"] and x["votes"], x                       # its vote right now, on its timeframes
            assert all(v["state"] in ("agree", "against", "neutral", "up", "down") for v in x["votes"])
    assert [x["score"] for x in I["items"]] == sorted([x["score"] for x in I["items"]], reverse=True)


def test_snapshot_is_complete_and_json():
    h, _, _ = _hub()
    s = C.snapshot(h)
    json.dumps(s)
    assert s["read_only"] and s["score"] and "overall" in s["score"] and s["indicators"]["items"]
    assert set(s["safety"]) >= {"master", "armed", "paper", "auto"}


def test_no_secrets_and_no_trading():
    hub, tm = make_tm(0)
    tm.set_armed(True)
    hub.events = getattr(hub, "events", [])
    hub.events.append(dict(kind="info", text="token=123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcd sk-ant-api-secretsecret",
                           wall=0))
    n = len(tm.master.backend.calls)
    s = json.dumps(C.snapshot(hub))
    cycle(tm)
    assert "sk-ant-api" not in s and "ABCDEFGHIJKLMNOP" not in s and "terminal_path" not in s and '"path"' not in s
    assert "open_market" not in tm.master.backend.calls[n:]
    tree = ast.parse(open(C.__file__).read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            assert node.attr not in ("open", "close_group", "modify_group", "set_armed", "set_master",
                                     "set_plugin", "set_scoring", "update_settings", "run_replay"), node.attr
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names] + [getattr(node, "module", "") or ""]
            assert not {"trading", "autotrade", "trade_gate", "mt5_ops"} & {n.split(".")[0] for n in names}
