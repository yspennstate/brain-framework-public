"""Run the script-style tests: files named test_*.py that do their work at import and end in sys.exit(0|1).

pytest cannot collect these (one sys.exit at import ends the whole collection), so conftest.py files in dreaming/
and brain_graph/bin/ keep pytest off them and this runner runs each one as its own process instead.

    python tools/run_script_tests.py [--timeout 120] [paths...]

Prints one line per file (PASS, FAIL rc, SKIP when it printed SKIP and exited 0, TIMEOUT) and a total; exits 1 if
any failed. A test that dies only because numpy is not installed is a SKIP: nothing else in the framework needs
it. Tests run without the user site-packages unless --user-site is given.
With no paths it runs every script-style test the two conftest files name, and every test file with no
test case that does its work in a __main__ block (pytest collects nothing from those, so nothing else runs them).
"""
import argparse, os, subprocess, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OPTIONAL = ("numpy",)
sys.path.insert(0, ROOT)


def is_script_test(path):
    """Ends the process itself and has no unittest or pytest test function: a module-level sys.exit / raise
    SystemExit, or one inside a module-level if/try/for/with block (not in a def, a class or the __main__ block),
    which runs at import."""
    try:
        src = open(path, encoding="utf-8", errors="replace").read()
    except OSError:
        return False
    if "unittest.TestCase" in src or "\ndef test_" in src:
        return False
    if any(l.startswith(("sys.exit(", "raise SystemExit")) for l in src.splitlines()):
        return True
    import ast
    try:
        stack = list(ast.parse(src).body)
    except SyntaxError:
        return False
    while stack:
        n = stack.pop()
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        if isinstance(n, ast.If) and "__name__" in ast.unparse(n.test):
            continue
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "exit" \
                and isinstance(n.func.value, ast.Name) and n.func.value.id == "sys":
            return True
        if isinstance(n, ast.Raise) and n.exc is not None:
            e = n.exc.func if isinstance(n.exc, ast.Call) else n.exc
            if isinstance(e, ast.Name) and e.id == "SystemExit":
                return True
        stack.extend(ast.iter_child_nodes(n))
    return False


def is_main_test(path):
    """No unittest or pytest test case, and a module-level `if __name__ == "__main__":` block: the file is a script
    that runs its checks when executed, and pytest collects nothing from it."""
    try:
        src = open(path, encoding="utf-8", errors="replace").read()
    except OSError:
        return False
    if "unittest.TestCase" in src or "\ndef test_" in src:
        return False
    import ast
    try:
        body = ast.parse(src).body
    except SyntaxError:
        return False
    return any(isinstance(n, ast.If) and "__main__" in ast.unparse(n.test) for n in body)


def discover():
    out = []
    for d in ("dreaming", os.path.join("brain_graph", "bin")):
        full = os.path.join(ROOT, d)
        for f in sorted(os.listdir(full)):
            p = os.path.join(full, f)
            if f.startswith("test_") and f.endswith(".py") and (is_script_test(p) or is_main_test(p)):
                out.append(p)
    return out


def main():
    ap = argparse.ArgumentParser(description="Run the script-style tests one process each.")
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--user-site", action="store_true",
                    help="let the tests see the user site-packages (pip install --user); off by default")
    a = ap.parse_args()
    flags = [] if a.user_site else ["-s"]
    paths = [os.path.abspath(p) for p in a.paths] or discover()
    res = {"PASS": 0, "FAIL": 0, "SKIP": 0, "TIMEOUT": 0}
    for p in paths:
        t = time.time()
        try:
            r = subprocess.run([sys.executable] + flags + [p], cwd=os.path.dirname(p), capture_output=True, text=True,
                               timeout=a.timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            last = (r.stdout + r.stderr).strip().splitlines()[-1:]
            if r.returncode == 0:
                st = "SKIP" if r.stdout.lstrip().startswith("SKIP") else "PASS"
            elif last and any(last[0].endswith("No module named '%s'" % m) for m in OPTIONAL):
                st = "SKIP"   # numpy is optional: the store and recall run without it, only these tests need it
            else:
                st = "FAIL"
            tail = last if st == "FAIL" or (st == "SKIP" and r.returncode) else []
        except subprocess.TimeoutExpired:
            st, tail = "TIMEOUT", []
        res[st] += 1
        print("%-7s %5.1fs %s%s" % (st, time.time() - t, os.path.relpath(p, ROOT), ("  | " + tail[0][:160]) if tail else ""),
              flush=True)
    print("total: " + ", ".join("%s %d" % kv for kv in res.items()))
    return 1 if res["FAIL"] or res["TIMEOUT"] else 0


if __name__ == "__main__":
    sys.exit(main())
