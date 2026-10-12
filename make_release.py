"""
Build the release zip WITHOUT any runtime file (v2.8.44).

Runtime files = your settings, accounts, journals, caches. They live next to the program on the VPS and must never be
inside an update: extracting a zip that contains one replaces yours (that is how strategy_settings.json from the
tests overwrote the strategy settings in v2.8.30 - v2.8.43).

    python make_release.py OUT.zip
"""
import fnmatch
import os
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
EXCLUDE = ("*.json", "*.jsonl", "*.pyc", "*.log", "crash_log.txt", "*.tmp")
EXCLUDE_DIRS = ("__pycache__", "recordings", ".git")
KEEP = ()                                 # a json that IS part of the program would be listed here (none today)


def files(root=HERE):
    for d, dirs, names in os.walk(root):
        dirs[:] = [x for x in dirs if x not in EXCLUDE_DIRS]
        for n in names:
            rel = os.path.relpath(os.path.join(d, n), root)
            if rel in KEEP or not any(fnmatch.fnmatch(n, p) for p in EXCLUDE):
                yield rel


def check(paths):
    """The build fails when a runtime file would be shipped."""
    bad = [p for p in paths if p not in KEEP and any(fnmatch.fnmatch(os.path.basename(p), x) for x in EXCLUDE)]
    if bad:
        raise SystemExit("RELEASE BLOCKED - runtime files in the package: " + ", ".join(bad))


def build(out):
    paths = sorted(files())
    check(paths)
    top = os.path.basename(HERE)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for p in paths:
            z.write(os.path.join(HERE, p), os.path.join(top, p))
    with zipfile.ZipFile(out) as z:                       # verify what was written, not what was meant
        check([n.split("/", 1)[1] for n in z.namelist() if "/" in n])
    return len(paths)


if __name__ == "__main__":
    n = build(sys.argv[1] if len(sys.argv) > 1 else "REYGUN_SuperChart.zip")
    print(f"OK: {n} files, no runtime files")
