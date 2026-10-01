"""Run every tests/test_*.py without extra packages:   python tests/run_tests.py  [name-filter]"""
import importlib
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def main():
    flt = sys.argv[1] if len(sys.argv) > 1 else ""
    mods = sorted(f[:-3] for f in os.listdir(HERE) if f.startswith("test_") and f.endswith(".py"))
    passed = failed = 0
    for m in mods:
        mod = importlib.import_module(m)
        for name in sorted(n for n in dir(mod) if n.startswith("test_")):
            if flt and flt not in f"{m}.{name}":
                continue
            try:
                getattr(mod, name)()
                passed += 1
                print(f"  PASS  {m}.{name}")
            except Exception:
                failed += 1
                print(f"  FAIL  {m}.{name}")
                traceback.print_exc(limit=4)
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
