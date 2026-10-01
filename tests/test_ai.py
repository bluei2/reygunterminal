"""AI analyst (Claude Fable): read-only, key never exposed, one request per press, fake HTTP only."""
import ast
import io
import json
import os
import time

from harness import TMP, cycle, make_tm

import ai_analyst as A


class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeOpener:
    def __init__(self, text="**Bias:** BULL\n1. Entry sa pPOC"):
        self.calls, self.text = [], text

    def __call__(self, req, timeout=0):
        self.calls.append(dict(url=req.full_url, headers=dict(req.header_items()), body=json.loads(req.data)))
        return FakeResp(json.dumps(dict(content=[dict(type="text", text=self.text)], stop_reason="end_turn",
                                        usage=dict(input_tokens=1200, output_tokens=300))).encode())


def svc(opener=None, key="sk-ant-test-1234567890abcd"):
    import test_plugins as TP
    h = TP.hub()
    ai = A.AIAnalyst(h, os.path.join(TMP, f"ai_s_{time.time_ns()}.json"), os.path.join(TMP, f"ai_j_{time.time_ns()}.jsonl"),
                     opener=opener or FakeOpener())
    if key:
        ai.update_settings(dict(api_key=key, min_interval_s=5))
    return ai


def test_key_is_never_returned_and_file_is_local():
    ai = svc()
    st = ai.state()
    blob = json.dumps(st)
    assert "sk-ant-test" not in blob and st["settings"]["key_set"] and st["settings"]["key_hint"] == "…abcd"
    ai.update_settings(dict(api_key="", model="claude-fable-5-1"))          # blank = keep the key
    assert ai.settings["api_key"].startswith("sk-ant-test")
    ai.update_settings(dict(clear_key=True))
    assert not ai.settings["api_key"] and not ai.state()["settings"]["key_set"]
    from error_log import redact
    assert "sk-ant-test" not in redact("failed with sk-ant-test-1234567890abcd")


def test_ask_sends_snapshot_to_fable_and_keeps_history():
    op = FakeOpener()
    ai = svc(op)
    res = ai.ask("score", wait=True)
    assert res["ok"] and res["result"]["ok"] and "Bias" in res["result"]["answer"]
    c = op.calls[0]
    assert c["url"] == "https://api.anthropic.com/v1/messages" and c["body"]["model"] == "claude-fable-5-1"
    assert c["headers"].get("X-api-key") == ai.settings["api_key"]
    user = c["body"]["messages"][0]["content"]
    snap = json.loads(user.split("SNAPSHOT (JSON):\n", 1)[1])
    assert snap["read"] is not None and "lost" in snap["read"] and "alt" in snap["read"]
    assert snap["candles"]["H1"] and len(snap["candles"]["H1"][0]) == 5
    assert "READ-ONLY" in c["body"]["system"] and "Taglish" in c["body"]["system"]
    assert ai.state()["history"][0]["mode"] == "score"
    assert os.path.exists(ai.journal_path) and "sk-ant" not in open(ai.journal_path).read()


def test_cost_guard_and_no_key():
    ai = svc()
    assert ai.ask("analysis", wait=True)["ok"]
    r = ai.ask("analysis", wait=True)
    assert not r["ok"] and "Maghintay" in r["error"]                        # one request per min_interval_s
    assert not svc(key="").ask("analysis", wait=True)["ok"]
    assert not ai.ask("question", "", wait=True)["ok"]


def test_api_error_is_readable():
    def boom(req, timeout=0):
        import urllib.error
        raise urllib.error.URLError("no route")
    ai = svc(boom)
    r = ai.ask("analysis", wait=True)
    assert r["ok"] and not r["result"]["ok"] and "api.anthropic.com" in r["result"]["error"]


def test_ai_module_cannot_trade():
    tree = ast.parse(open(A.__file__).read())
    imported = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            imported |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            imported.add((n.module or "").split(".")[0])
        elif isinstance(n, ast.Attribute):
            assert n.attr not in ("trader", "gate", "execution", "order_send", "confirmations", "set_scoring",
                                  "set_plugin", "set_master", "set_armed"), n.attr
        elif isinstance(n, ast.Constant) and isinstance(n.value, str):
            assert "/api/trade" not in n.value
    assert not imported & {"trading", "mt5_ops", "trade_gate", "execution_guard", "trade_confirmation",
                           "account_worker", "autotrade", "position_manager", "MetaTrader5", "risk_engine"}, imported
    hub, tm = make_tm(0)
    tm.set_armed(True)
    ai = A.AIAnalyst(hub, os.path.join(TMP, "ai_t.json"), os.path.join(TMP, "ai_t.jsonl"),
                     opener=FakeOpener("BUY NOW 1.0 lot, SL 2600"))
    ai.update_settings(dict(api_key="sk-ant-x-000000000000"))
    n = len(tm.master.backend.calls)
    ai.ask("analysis", wait=True)                                          # even an answer that "says" buy
    cycle(tm)
    assert not tm.groups and "open_market" not in tm.master.backend.calls[n:]
