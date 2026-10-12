"""v2.8.44: the release zip never carries a runtime file, and the tests never write into the program folder."""
import os
import sys
import tempfile
import zipfile

sys.path.insert(0, "..")
import harness          # noqa: E402,F401  (redirects every runtime file)
import make_release     # noqa: E402
import strategies as ST  # noqa: E402


def test_tests_write_settings_to_temp_only():
    assert os.path.dirname(ST.SETTINGS_FILE) == harness.TMP
    ST.save_settings(ST.default_settings())
    assert not os.path.exists(os.path.join(harness.ROOT, "strategy_settings.json"))


def test_release_has_no_runtime_files():
    out = os.path.join(tempfile.mkdtemp(), "r.zip")
    make_release.build(out)
    names = zipfile.ZipFile(out).namelist()
    assert not [n for n in names if n.endswith((".json", ".jsonl", ".pyc"))], names
    assert any(n.endswith("superchart.py") for n in names) and any(n.endswith("web/app.js") for n in names)


def test_check_blocks_a_runtime_file():
    try:
        make_release.check(["superchart.py", "strategy_settings.json"])
    except SystemExit as e:
        assert "strategy_settings.json" in str(e)
    else:
        raise AssertionError("not blocked")
