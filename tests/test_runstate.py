"""Keep-alive: a crash restart (--resume) restores trading ON / AUTO / NEWS; a normal start or a clean stop never."""
import time
import types

import runstate as R
from harness import TMP


def _hub(armed=False, halted=""):
    calls = []
    tm = types.SimpleNamespace(armed=armed, halted=halted, set_armed=lambda on: calls.append(("arm", on)))
    at = types.SimpleNamespace(enabled=False, set_enabled=lambda on: (calls.append(("auto", on)), dict(ok=True))[1])
    nt = types.SimpleNamespace(enabled=False, set_enabled=lambda on: (calls.append(("news", on)), dict(ok=True))[1])
    return types.SimpleNamespace(trader=tm, autotrader=at, newstrader=nt, running=True), calls


def test_resume_only_after_a_recent_unclean_stop():
    R.STATE_FILE = TMP + "/runtime_state.json"
    hub, calls = _hub()
    now = time.time()
    prev = dict(t=now - 30, clean=False, armed=True, auto=True, news=False)
    assert R.resume(hub, prev, now) == ["trading ON", "AUTO"] and ("auto", True) in calls
    hub, calls = _hub()
    assert R.resume(hub, dict(prev, clean=True), now) == [] and not calls            # closed on purpose
    assert R.resume(hub, dict(prev, t=now - 3600), now) == [] and not calls         # too old
    assert R.resume(hub, dict(prev, armed=False), now) == []                         # was OFF: stays OFF
    hub, calls = _hub(halted="daily DD")
    assert R.resume(hub, prev, now) == [] and not calls                              # a HALT is never cleared
    assert R.resume(hub, None, now) == []


def test_state_file_and_clean_mark():
    R.STATE_FILE = TMP + "/runtime_state.json"
    hub, _ = _hub(armed=True)
    hub.autotrader.enabled = True
    R.write(R.snapshot(hub))
    s = R.read()
    assert s["armed"] and s["auto"] and not s["clean"]
    R.mark_clean(hub)
    assert R.read()["clean"] is True
    R._stopped = False
