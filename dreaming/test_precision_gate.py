"""test_precision_gate.py - the reflex hook stops steering by a precision table it cannot date or that is a week old
(29 Sep 2026, claude-825b1305; scheduled audit account04/B04 r7, B04-R43-FRESHNESS-WATCH-NOT-GATE).

    python test_precision_gate.py [--module INSTINCT_PRIME.py] [--old OLD_INSTINCT_PRIME.py]

A throwaway table and gate file.
 P1 built now: its reflexes steer
 P2 built three days ago (past the 36-hour liveness warning, inside seven days): still steers
 P3 built eight days ago: ignored, every reflex unjudged, and the reason written beside it
 P4 no build stamp: ignored
 P5 stamped in the future: ignored
With --old, the hook given must still read P3's table (N1; the check can fail).
"""
import importlib.machinery
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ARGS = sys.argv[1:]
MOD = os.path.abspath(ARGS[ARGS.index("--module") + 1]) if "--module" in ARGS else os.path.join(HERE, "instinct_prime.py")
OLD = os.path.abspath(ARGS[ARGS.index("--old") + 1]) if "--old" in ARGS else None
fails, ran = [], []
TABLE = {"x": {"n": 5, "on_domain": 4, "p": 0.8, "judged": True}}


def check(ok, what):
    print(("PASS  " if ok else "FAIL  ") + what)
    ran.append(what)
    if not ok:
        fails.append(what)


def load(path, name):
    sys.path.insert(0, os.path.dirname(path))
    loader = importlib.machinery.SourceFileLoader(name, path)
    spec = importlib.util.spec_from_file_location(name, path, loader=loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


def stamp(days):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - days * 86400))


def read(path, tag, built):
    td = tempfile.mkdtemp(prefix="precgate_%s_" % tag)
    try:
        m = load(path, "ip_%s" % tag)
        m.PRECISION_PATH = os.path.join(td, "reflex_precision.json")
        if hasattr(m, "PRECISION_GATE"):
            m.PRECISION_GATE = os.path.join(td, "reflex_precision.gate.json")
        d = {"reflexes": TABLE}
        if built is not None:
            d["built"] = built
        with open(m.PRECISION_PATH, "w", encoding="utf-8") as fh:
            json.dump(d, fh)
        got = m._load_precision()
        gate = None
        gp = getattr(m, "PRECISION_GATE", None)
        if gp and os.path.exists(gp):
            gate = json.load(open(gp, encoding="utf-8"))
        return got, gate
    finally:
        shutil.rmtree(td, ignore_errors=True)


def main():
    p1, _ = read(MOD, "p1", stamp(0))
    check(p1 == TABLE, "P1 built now: steers")
    p2, _ = read(MOD, "p2", stamp(3))
    check(p2 == TABLE, "P2 built three days ago: still steers")
    p3, g3 = read(MOD, "p3", stamp(8))
    check(p3 == {} and g3 and g3.get("ignored") and "days ago" in g3.get("reason", ""),
          "P3 built eight days ago: ignored, reason %s" % (g3 or {}).get("reason"))
    p4, _ = read(MOD, "p4", None)
    check(p4 == {}, "P4 no build stamp: ignored")
    p5, _ = read(MOD, "p5", stamp(-2))
    check(p5 == {}, "P5 stamped in the future: ignored")
    if OLD:
        o3, _ = read(OLD, "old3", stamp(8))
        check(o3 == TABLE, "N1 the hook given reads an eight-day-old table (the check can fail)")
    print("FAIL" if fails else "PASS", "- %d controls, %d failed" % (len(ran), len(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
