"""test_sidecar_publication.py - no delta over a store the sidecar was not built from; the sidecar publish waits out a
reader (29 Sep 2026, claude-825b1305; scheduled audit account02/B11 r83, B11-R64-SIDECAR-REPLACE-FAIL-MIXED-BASE-DELTA-01).

    python test_sidecar_publication.py [--old OLD_LIVE_DELTA.py]

Throwaway folders only; run_graph's log and STATUS writes are captured, never the live ones.
 P1 base() over a sidecar built from the store the directory holds: accepted
 P2 the store rebuilt, the sidecar not: base() raises SidecarBehind, update() builds nothing and the pointer is unchanged
 P3 a store whose nodes.json carries no build record: refused
 P4 the sidecar publish while a SQLite reader holds the old file and closes it 0.6 s later: published, and the wait
    is reported
 P5 a reader that never closes: the publish raises and the old sidecar is left as it was
 F1 the capture's fast layer, sidecar current: the delta is built, the sidecar is not touched
 F2 sidecar behind, republish succeeds: republished once, then the delta
 F3 sidecar behind, republish fails: no delta, and the skip is recorded
 F4 the build's call (retry=False) after its own sidecar step failed: no republish, no delta
With --old, the live_delta given must accept P2's pair (N1: the version before 29 Sep builds a delta there).
"""
import importlib.machinery
import importlib.util
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import time

if __name__ == "__main__" and ("-h" in sys.argv[1:] or "--help" in sys.argv[1:]):
    print(__doc__)
    sys.exit(0)

HERE = os.path.dirname(os.path.abspath(__file__))
ARGS = sys.argv[1:]
OLD = os.path.abspath(ARGS[ARGS.index("--old") + 1]) if "--old" in ARGS else None
fails, ran = [], []
BUILT, UNTIL = 1790659241.6787663, 1790658906.667


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


def fixture(td, store_meta, sidecar_meta):
    store = os.path.join(td, "store")
    os.makedirs(store, exist_ok=True)
    with open(os.path.join(store, "nodes.json"), "w", encoding="utf-8") as fh:
        json.dump({"meta": store_meta, "names": []}, fh)
    live = os.path.join(td, "live")
    os.makedirs(live, exist_ok=True)
    sc = os.path.join(live, "sidecar.sqlite")
    if os.path.exists(sc):
        os.remove(sc)
    db = sqlite3.connect(sc)
    db.execute("CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT)")
    sm = dict(sidecar_meta, store=store)
    db.executemany("INSERT INTO meta VALUES (?, ?)", [(k, json.dumps(v)) for k, v in sm.items()])
    db.commit()
    db.close()
    ptr = os.path.join(live, "delta.json")
    with open(ptr, "w", encoding="utf-8") as fh:
        json.dump({"file": "delta_old.sqlite", "base_store_built": BUILT}, fh)
    return live, sc, ptr


def point(m, td, live, sc, ptr):
    m.HOME, m.LIVE, m.SIDECAR, m.POINTER = td, live, sc, ptr
    m.STATUS = os.path.join(td, "STATUS.json")
    m.EVENTS = os.path.join(td, "events_touch.jsonl")
    m.CONFIG = os.path.join(td, "build_config.json")
    m.log = lambda msg: None


def base_cases(path, tag, with_update=True):
    td = tempfile.mkdtemp(prefix="sidecar_pub_%s_" % tag)
    out = {}
    try:
        m = load(path, "ld_" + tag)
        good = {"built": BUILT, "until": UNTIL, "hubs": [], "half_life_days": 14}
        side = {"built": BUILT + 60.0, "store_built": BUILT, "store_until": UNTIL}
        live, sc, ptr = fixture(td, good, side)
        point(m, td, live, sc, ptr)
        try:
            b = m.base()
            out["P1"] = b.get("store_built") == BUILT and b.get("until") == UNTIL
        except Exception as e:
            out["P1"] = "raised %s" % type(e).__name__
        live, sc, ptr = fixture(td, dict(good, built=BUILT + 21600.0, until=UNTIL + 21000.0), side)
        point(m, td, live, sc, ptr)
        try:
            m.base()
            out["P2"] = "accepted"
        except Exception as e:
            out["P2"] = type(e).__name__
        before = open(ptr, "rb").read()
        if with_update:                  # never for the old module: past base() it reads the live kinds cache
            try:
                r = m.update()
                out["P2u"] = bool(r.get("skipped")) and open(ptr, "rb").read() == before
            except Exception as e:
                out["P2u"] = "raised %s" % type(e).__name__
        live, sc, ptr = fixture(td, {"hubs": []}, side)
        point(m, td, live, sc, ptr)
        try:
            m.base()
            out["P3"] = "accepted"
        except Exception as e:
            out["P3"] = type(e).__name__
    finally:
        shutil.rmtree(td, ignore_errors=True)
    return out


def publish_cases():
    ls = load(os.path.join(HERE, "live_sidecar.py"), "ls_new")
    ls.log = lambda msg: None
    td = tempfile.mkdtemp(prefix="sidecar_pub_rep_")
    out = {}
    readers = []
    if os.name != "nt":
        # POSIX renames over a file a reader holds, so the Windows refusal is stood in for while a reader is open
        real_replace = os.replace

        def replace(src, dst, *a, **k):
            if readers and os.path.abspath(dst) == os.path.abspath(os.path.join(td, "sidecar.sqlite")):
                raise PermissionError(13, "Access is denied")
            return real_replace(src, dst, *a, **k)
        ls.os = type(os)("os_standin")
        ls.os.__dict__.update(os.__dict__)
        ls.os.replace = replace
    try:
        old, new = os.path.join(td, "sidecar.sqlite"), os.path.join(td, "sidecar.sqlite.new")
        for p, v in ((old, "old"), (new, "new")):
            db = sqlite3.connect(p)
            db.execute("CREATE TABLE t(v TEXT)")
            db.execute("INSERT INTO t VALUES (?)", (v,))
            db.commit()
            db.close()
        reader = sqlite3.connect(old, check_same_thread=False)   # closed from the timer's thread
        reader.execute("SELECT count(*) FROM t").fetchone()
        readers.append(reader)
        threading.Timer(0.6, lambda r=reader: (readers.remove(r), r.close())).start()
        try:
            waited = ls.publish(new, old, tries=40, wait=0.1)
            db = sqlite3.connect(old)
            v = db.execute("SELECT v FROM t").fetchone()[0]
            db.close()
            out["P4"] = (v == "new" and waited >= 0.3, waited, v)
        except Exception as e:
            out["P4"] = (False, "raised %s" % type(e).__name__, None)
        if os.path.exists(new):
            os.remove(new)
        db = sqlite3.connect(new)
        db.execute("CREATE TABLE t(v TEXT)")
        db.execute("INSERT INTO t VALUES ('newer')")
        db.commit()
        db.close()
        reader = sqlite3.connect(old)
        reader.execute("SELECT count(*) FROM t").fetchone()
        readers.append(reader)
        try:
            ls.publish(new, old, tries=5, wait=0.05)
            out["P5"] = (False, "published under a reader")
        except PermissionError:
            readers.remove(reader)
            reader.close()
            db = sqlite3.connect(old)
            v = db.execute("SELECT v FROM t").fetchone()[0]
            db.close()
            out["P5"] = (v == "new", "raised; the sidecar reads %r" % v)
        finally:
            try:
                reader.close()
            except Exception:
                pass
    finally:
        shutil.rmtree(td, ignore_errors=True)
    return out


def fast_layer_cases():
    rg = load(os.path.join(HERE, "run_graph.py"), "rg_new")
    out = {}
    for tag, behind_seq, sidecar_ok, retry in (("F1", [None], True, True), ("F2", ["behind", None], True, True),
                                               ("F3", ["behind", "behind"], False, True), ("F4", ["behind"], True, False)):
        calls = {"sidecar": 0, "delta": 0, "logs": [], "status": []}
        seq = list(behind_seq)

        def behind(seq=seq):
            return seq.pop(0) if seq else None

        def sidecar(calls=calls, ok=sidecar_ok):
            calls["sidecar"] += 1
            if not ok:
                raise PermissionError("held")
            return "published"

        def delta(calls=calls):
            calls["delta"] += 1

        rg._sidecar_behind, rg._sidecar, rg._delta = behind, sidecar, delta
        rg.log = lambda line, calls=calls: calls["logs"].append(line)
        rg.status_update = lambda calls=calls, **kw: calls["status"].append(kw)
        rg._fast_layer(retry=retry)
        out[tag] = calls
    return out


def main():
    r = base_cases(os.path.join(HERE, "live_delta.py"), "new")
    check(r["P1"] is True, "P1 a sidecar built from the store the directory holds: accepted (%s)" % r["P1"])
    check(r["P2"] == "SidecarBehind", "P2 the store rebuilt, the sidecar not: refused (%s)" % r["P2"])
    check(r["P2u"] is True, "P2 update() builds no delta and leaves the pointer as it was (%s)" % r["P2u"])
    check(r["P3"] == "SidecarBehind", "P3 a store with no build record: refused (%s)" % r["P3"])
    p = publish_cases()
    check(p["P4"][0], "P4 a reader closes after 0.6 s: published after waiting %s s, the sidecar reads %r" % (
        p["P4"][1] if isinstance(p["P4"][1], str) else "%.2f" % p["P4"][1], p["P4"][2]))
    check(p["P5"][0], "P5 a reader that never closes: %s" % p["P5"][1])
    f = fast_layer_cases()
    check(f["F1"]["sidecar"] == 0 and f["F1"]["delta"] == 1, "F1 sidecar current: delta built, sidecar untouched (%s)" % (
        {k: f["F1"][k] for k in ("sidecar", "delta")}))
    check(f["F2"]["sidecar"] == 1 and f["F2"]["delta"] == 1, "F2 behind, republished: one republish, then the delta (%s)" % (
        {k: f["F2"][k] for k in ("sidecar", "delta")}))
    check(f["F3"]["sidecar"] == 1 and f["F3"]["delta"] == 0 and any("delta_skipped" in s for s in f["F3"]["status"]),
          "F3 behind, republish fails: no delta, the skip recorded (%s)" % ({k: f["F3"][k] for k in ("sidecar", "delta")}))
    check(f["F4"]["sidecar"] == 0 and f["F4"]["delta"] == 0, "F4 the build's call after its sidecar failed: nothing (%s)" % (
        {k: f["F4"][k] for k in ("sidecar", "delta")}))
    if OLD:
        o = base_cases(OLD, "old", with_update=False)
        check(o["P2"] == "accepted", "N1 the live_delta given (before 29 Sep) accepts P2's pair: %s (the check can fail)"
              % o["P2"])
    print("FAIL" if fails else "PASS", "- %d controls, %d failed" % (len(ran), len(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
