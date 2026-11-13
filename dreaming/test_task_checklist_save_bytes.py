"""test_task_checklist_save_bytes.py - save_state writes its state and hourly copy from one json.dumps string each,
byte for byte what json.dump wrote before (5 Nov 2026).

    python test_task_checklist_save_bytes.py [--module TASK_CHECKLIST_HOOK.py] [--old OLD.py]

The store is a throwaway folder (TASK_CHECKLIST_HOME). The state is about 1.6 MB with non-ASCII asks, like a real one.
 B1 with json.dump made to raise, save_state still writes state.json and its hourly copy
 B2 state.json parses back to the state it was given (orjson or json.dumps, whichever the hook has)
 B3 the hourly copy parses back to the same state and is no larger than state.json
 B4 (printed, not asserted) median save time of this module, and of --old when given
With --old, the module before this change must fail B1 (it still calls json.dump, so the check can fail).
"""
import importlib.machinery
import importlib.util
import json
import os
import shutil
import statistics
import sys
import tempfile
import time

if __name__ == "__main__" and ("-h" in sys.argv[1:] or "--help" in sys.argv[1:]):
    print(__doc__)
    sys.exit(0)

HERE = os.path.dirname(os.path.abspath(__file__))
ARGS = sys.argv[1:]
MOD = os.path.abspath(ARGS[ARGS.index("--module") + 1]) if "--module" in ARGS else os.path.join(HERE, "task_checklist_hook.py")
OLD = os.path.abspath(ARGS[ARGS.index("--old") + 1]) if "--old" in ARGS else None
fails, ran = [], []


def check(ok, what):
    print(("PASS  " if ok else "FAIL  ") + what)
    ran.append(what)
    if not ok:
        fails.append(what)


def state():
    entries = {}
    for i in range(2400):
        entries["T-20261105-%04d" % i] = {
            "id": "T-20261105-%04d" % i, "agent": "claude-%08x" % (i * 2654435761 % 2 ** 32),
            "asked": "fire this every 5 minutes, keep working on the brain - été שלום %d" % i,
            "working": "step %d of the parity pass, with a note long enough to look like a real one" % i,
            "notes": ["%02d:%02d progress line %d" % (i % 24, i % 60, k) for k in range(6)],
            "done": i % 3 == 0, "seen": i % 7 == 0, "ts": 1793872796 + i}
    return {"entries": entries, "version": 3}


def run(path, tag):
    td = tempfile.mkdtemp(prefix="tc_save_%s_" % tag)
    old_env = os.environ.get("TASK_CHECKLIST_HOME")
    os.environ["TASK_CHECKLIST_HOME"] = td
    real_dump = json.dump
    out = {}
    try:
        loader = importlib.machinery.SourceFileLoader("tc_save_" + tag, path)
        spec = importlib.util.spec_from_file_location("tc_save_" + tag, path, loader=loader)
        m = importlib.util.module_from_spec(spec)
        loader.exec_module(m)
        st = state()

        def boom(*a, **k):
            raise RuntimeError("json.dump called")

        json.dump = boom
        try:
            m.save_state(st)
            out["B1"] = (True, "")
        except Exception as exc:
            out["B1"] = (False, repr(exc)[:80])
        finally:
            json.dump = real_dump
        want = json.dumps(st, ensure_ascii=False, indent=1, sort_keys=True).encode("utf-8")
        got = open(m.STATE, "rb").read() if os.path.exists(m.STATE) else b""
        out["B2"] = (bool(got) and json.loads(got.decode("utf-8")) == st, len(got), len(want))
        bak = m._backup_path()
        wantb = json.dumps(st, ensure_ascii=False, sort_keys=True).encode("utf-8")
        gotb = open(bak, "rb").read() if os.path.exists(bak) else b""
        out["B3"] = (bool(gotb) and json.loads(gotb.decode("utf-8")) == st and len(gotb) <= len(got), len(gotb), len(wantb))
        times = []
        for _ in range(9):
            t0 = time.perf_counter()
            m.save_state(st)
            times.append((time.perf_counter() - t0) * 1000)
        out["ms"] = statistics.median(times)
        return out
    finally:
        json.dump = real_dump
        if old_env is None:
            os.environ.pop("TASK_CHECKLIST_HOME", None)
        else:
            os.environ["TASK_CHECKLIST_HOME"] = old_env
        shutil.rmtree(td, ignore_errors=True)


def main():
    r = run(MOD, "new")
    check(r["B1"][0], "B1 save_state works with json.dump raising %s" % r["B1"][1])
    check(r["B2"][0], "B2 state.json parses back to the state (%d bytes, json.dumps %d)" % r["B2"][1:])
    check(r["B3"][0], "B3 hourly copy parses back, compact (%d bytes, json.dumps %d)" % r["B3"][1:])
    print("B4 median save %.1f ms" % r["ms"])
    if OLD:
        o = run(OLD, "old")
        check(not o["B1"][0], "N1 the module before this change still calls json.dump (%s) (the check can fail)"
              % (o["B1"][1] or "it did not"))
        print("B4 old median save %.1f ms" % o["ms"])
    print("FAIL" if fails else "PASS", "- %d controls, %d failed" % (len(ran), len(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
