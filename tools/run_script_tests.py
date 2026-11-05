"""Run the script-style tests: files named test_*.py that do their work at import and end in sys.exit(0|1).

pytest cannot collect these (one sys.exit at import ends the whole collection), so conftest.py files in dreaming/
and brain_graph/bin/ keep pytest off them and this runner runs each one as its own process instead.

    python tools/run_script_tests.py [--timeout 120] [paths...]

Prints one line per file (PASS, FAIL rc, SKIP when it printed SKIP and exited 0, TIMEOUT) and a total; exits 1 if
any failed. With no paths it runs every script-style test the two conftest files name.
"""
import argparse, os, subprocess, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def is_script_test(path):
    """Ends the process itself: a module-level sys.exit / raise SystemExit, and no unittest or pytest test function."""
    try:
        src = open(path, encoding="utf-8", errors="replace").read()
    except OSError:
        return False
    top_exit = any(l.startswith(("sys.exit(", "raise SystemExit")) for l in src.splitlines())
    has_cases = "unittest.TestCase" in src or "\ndef test_" in src
    return top_exit and not has_cases


def discover():
    out = []
    for d in ("dreaming", os.path.join("brain_graph", "bin")):
        full = os.path.join(ROOT, d)
        for f in sorted(os.listdir(full)):
            if f.startswith("test_") and f.endswith(".py") and is_script_test(os.path.join(full, f)):
                out.append(os.path.join(full, f))
    return out


def main():
    ap = argparse.ArgumentParser(description="Run the script-style tests one process each.")
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--timeout", type=int, default=120)
    a = ap.parse_args()
    paths = [os.path.abspath(p) for p in a.paths] or discover()
    res = {"PASS": 0, "FAIL": 0, "SKIP": 0, "TIMEOUT": 0}
    for p in paths:
        t = time.time()
        try:
            r = subprocess.run([sys.executable, "-s", p], cwd=os.path.dirname(p), capture_output=True, text=True,
                               timeout=a.timeout)
            if r.returncode == 0:
                st = "SKIP" if r.stdout.lstrip().startswith("SKIP") else "PASS"
            else:
                st = "FAIL"
            tail = (r.stdout + r.stderr).strip().splitlines()[-1:] if st == "FAIL" else []
        except subprocess.TimeoutExpired:
            st, tail = "TIMEOUT", []
        res[st] += 1
        print("%-7s %5.1fs %s%s" % (st, time.time() - t, os.path.relpath(p, ROOT), ("  | " + tail[0][:160]) if tail else ""),
              flush=True)
    print("total: " + ", ".join("%s %d" % kv for kv in res.items()))
    return 1 if res["FAIL"] or res["TIMEOUT"] else 0


if __name__ == "__main__":
    sys.exit(main())
