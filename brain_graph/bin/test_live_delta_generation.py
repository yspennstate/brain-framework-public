"""test_live_delta_generation.py - the fast layer is merged only onto the base generation it was counted on (28 Sep 2026,
claude-825b1305; interactive audit IA-20260928-02).

    python test_live_delta_generation.py [--module LIVE_DELTA.py] [--old OLD_LIVE_DELTA.py]

live_delta's LIVE and POINTER are pointed at a throwaway folder holding a pointer and a delta file with a meta table; the
sidecar's meta is passed as the membrane passes it. Base 100 is the delta's; the sidecar varies:
 D1 the same last event, another store build (200): refused
 D2 the same last event and builds: accepted
 D3 the pointer and the sidecar agree, but the file the pointer names was counted on another store build: refused
 D4 the last event 2 s apart: refused
 D5 a pointer from before the sidecar's build time was added to it, the file recording it and matching: accepted
Sub-second generations (29 Sep, account02/B12 r90 and account01/B19; the base identity is exact since then):
 D6 the sidecar rebuilt 0.5 s later, all else equal: refused
 D7 the sidecar's last event 0.5 s later: refused
 D8 the store rebuilt 0.5 s later: refused
 D9 all three 0.5 s later: refused
 D10 the live system's own full-precision values (29 Sep 08:29) through JSON in the pointer, the file and the sidecar:
     accepted
The dual legacy omission (29 Sep, account04/B12 r67, T-R67-DUAL-LEGACY-BASE-CLOSURE E, F and D):
 D11 the pointer and the file both omit the sidecar build: refused
 D12 both omit the store build: refused
 D13 both omit the last event: refused
 D14 the pointer omits the sidecar build and the file records another one: refused
 D15 the sidecar records no build of its own: refused
 D16 a refusal says why (open_delta.refused names the missing value)
With --old, the live_delta given must accept D1 (the version before 28 Sep), D6-D9 (the version before 29 Sep 08:xx) or
D11 (the version before 29 Sep 15:xx); the check says which it tested.
"""
import importlib.machinery
import importlib.util
import json
import os
import shutil
import sqlite3
import sys
import tempfile

if __name__ == "__main__" and ("-h" in sys.argv[1:] or "--help" in sys.argv[1:]):
    print(__doc__)
    sys.exit(0)

HERE = os.path.dirname(os.path.abspath(__file__))
ARGS = sys.argv[1:]
MOD = os.path.abspath(ARGS[ARGS.index("--module") + 1]) if "--module" in ARGS else os.path.join(HERE, "live_delta.py")
OLD = os.path.abspath(ARGS[ARGS.index("--old") + 1]) if "--old" in ARGS else None
fails, ran = [], []
UNTIL, SBUILT, CBUILT = 1790000000.0, 1790000100.0, 1790000150.0


def check(ok, what):
    print(("PASS  " if ok else "FAIL  ") + what)
    ran.append(what)
    if not ok:
        fails.append(what)


def load(path, name):
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    loader = importlib.machinery.SourceFileLoader(name, path)
    spec = importlib.util.spec_from_file_location(name, path, loader=loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


def opened(m, td, ptr_extra=None, file_meta=None, sidecar=None, drop_ptr=(), drop_file=(), drop_sidecar=()):
    m.LIVE = td
    m.POINTER = os.path.join(td, "delta.json")
    name = "delta_fixture.sqlite"
    p = os.path.join(td, name)
    if os.path.exists(p):
        os.remove(p)
    db = sqlite3.connect(p)
    db.execute("CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT)")
    fm = {"base_store_until": UNTIL, "base_store_built": SBUILT, "base_sidecar_built": CBUILT, "total_w": 1.0}
    fm.update(file_meta or {})
    for k in drop_file:
        fm.pop(k, None)
    db.executemany("INSERT INTO meta VALUES (?, ?)", [(k, json.dumps(v)) for k, v in fm.items()])
    db.commit()
    db.close()
    ptr = {"file": name, "built": CBUILT + 10, "base_store_until": UNTIL, "base_store_built": SBUILT,
           "base_sidecar_built": CBUILT}
    ptr.update(ptr_extra or {})
    for k in drop_ptr:
        ptr.pop(k, None)
    with open(m.POINTER, "w", encoding="utf-8") as fh:
        json.dump(ptr, fh)
    sc = {"store_until": UNTIL, "store_built": SBUILT, "built": CBUILT}
    sc.update(sidecar or {})
    for k in drop_sidecar:
        sc.pop(k, None)
    conn, meta = m.open_delta(sc)
    if conn is not None:
        conn.close()
    return conn is not None


def cases(path, tag):
    td = tempfile.mkdtemp(prefix="live_delta_gen_%s_" % tag)
    try:
        m = load(path, "ld_" + tag)
        live = {"base_store_until": 1790658906.667, "base_store_built": 1790659241.6787663,
                "base_sidecar_built": 1790659435.3909745}
        return {"D1": opened(m, td, sidecar={"store_built": 200.0 + SBUILT}),
                "D2": opened(m, td),
                "D3": opened(m, td, file_meta={"base_store_built": SBUILT + 200.0}),
                "D4": opened(m, td, sidecar={"store_until": UNTIL + 2.0}),
                "D5": opened(m, td, drop_ptr=("base_sidecar_built",)),
                "D6": opened(m, td, sidecar={"built": CBUILT + 0.5}),
                "D7": opened(m, td, sidecar={"store_until": UNTIL + 0.5}),
                "D8": opened(m, td, sidecar={"store_built": SBUILT + 0.5}),
                "D9": opened(m, td, sidecar={"store_until": UNTIL + 0.5, "store_built": SBUILT + 0.5, "built": CBUILT + 0.5}),
                "D10": opened(m, td, ptr_extra=live, file_meta=live,
                              sidecar={"store_until": live["base_store_until"], "store_built": live["base_store_built"],
                                       "built": live["base_sidecar_built"]}),
                "D11": opened(m, td, drop_ptr=("base_sidecar_built",), drop_file=("base_sidecar_built",)),
                "D12": opened(m, td, drop_ptr=("base_store_built",), drop_file=("base_store_built",)),
                "D13": opened(m, td, drop_ptr=("base_store_until",), drop_file=("base_store_until",)),
                "D14": opened(m, td, drop_ptr=("base_sidecar_built",), file_meta={"base_sidecar_built": CBUILT + 60.0}),
                "D15": opened(m, td, drop_sidecar=("built",)),
                "D16": (opened(m, td, drop_ptr=("base_sidecar_built",), drop_file=("base_sidecar_built",)),
                        getattr(m.open_delta, "refused", None))}
    finally:
        shutil.rmtree(td, ignore_errors=True)


def main():
    r = cases(MOD, "new")
    check(r["D1"] is False, "D1 the same last event, another store build: refused (%s)" % (not r["D1"]))
    check(r["D2"] is True, "D2 the same base: accepted (%s)" % r["D2"])
    check(r["D3"] is False, "D3 the named file was counted on another store build: refused (%s)" % (not r["D3"]))
    check(r["D4"] is False, "D4 the last event 2 s apart: refused (%s)" % (not r["D4"]))
    check(r["D5"] is True, "D5 a pointer without the sidecar build, all else matching: accepted (%s)" % r["D5"])
    sub = [k for k in ("D6", "D7", "D8", "D9") if r[k] is not False]
    check(not sub, "D6-D9 a generation 0.5 s apart in the sidecar build, last event, store build or all three: refused "
                   "(accepted: %s)" % (", ".join(sub) or "none"))
    check(r["D10"] is True, "D10 the live system's full-precision values through JSON: accepted (%s)" % r["D10"])
    dual = [k for k in ("D11", "D12", "D13", "D14", "D15") if r[k] is not False]
    check(not dual, "D11-D15 a base value on record nowhere (pointer and file both omit it: sidecar build, store build, "
                    "last event), a mismatched file behind a legacy pointer, a sidecar without its build: refused "
                    "(accepted: %s)" % (", ".join(dual) or "none"))
    check(r["D16"][0] is False and "base_sidecar_built" in str(r["D16"][1]),
          "D16 the refusal names the missing value: %r" % (r["D16"][1],))
    if OLD:
        o = cases(OLD, "old")
        if o["D1"] is True:
            check(True, "N1 the live_delta given accepts D1's other base (the version before 28 Sep; the check can fail)")
        elif not [k for k in ("D6", "D7", "D8", "D9") if o[k] is True]:
            check(o["D11"] is True, "N3 the live_delta given (before 29 Sep 15:xx) accepts D11, a base value recorded "
                                    "nowhere: %s (the check can fail)" % o["D11"])
        else:
            took = [k for k in ("D6", "D7", "D8", "D9") if o[k] is True]
            check(len(took) == 4, "N2 the live_delta given (before 29 Sep) accepts the sub-second generations D6-D9: %s "
                                  "(the check can fail)" % (", ".join(took) or "none"))
    print("FAIL" if fails else "PASS", "- %d controls, %d failed" % (len(ran), len(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
