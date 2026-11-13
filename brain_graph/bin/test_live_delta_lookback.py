"""test_live_delta_lookback.py - the fast layer's context read covers what each new event's count depends on, however much
of the log lies between.

    python test_live_delta_lookback.py [--module LIVE_DELTA.py] [--old OLD_LIVE_DELTA.py]

Fixtures are throwaway logs in the event log's own line format. The reference for every case is a read of the whole log
(the module before this change with its look-back set past the start of the file): the candidate must flag the same new
events and produce the same edges, while reading less.
 L1 the auditor's case at four paddings (0, 4, 12, 24 MB of another session between one session's context and its new
    touch, 101 s apart): the same new events and edges as the whole read at every padding, and the read starts after
    the log's older history
 L2 a bank touch 1,000 s before the new touch, before 12 MB of padding: the new touch is left out, as the whole read
    leaves it out
 L3 a session captured five days late: 12 MB of old-stamped lines appended between the context and the new touch - a
    read that stops at the first block older than it needs would miss the context; the edge is there
 L4 the 256 MiB cap, here 1 MiB: the touch whose context lies beyond reach is withheld (kept as context, counted), a later
    touch in another session is still counted with its edge
 L5 a last line still being written is neither new nor context, and the index does not read it
 L6 the index cache: reused when the prefix is the same (no block is read), extended over an append (only the new bytes
    are read, same maximum as a fresh scan), recomputed when a byte of the prefix changes (equal to a fresh scan)
 L7 a line stamped ten days in the future does not hide the log behind it (the edge is there, nothing withheld)
 L8 marks every line: the needed context line is exactly the first line of the read and is read
 L9 the live path (STATUS, the verified prefix hash, the cache): the same result as the explicit start, twice in a row
With --old, the module before this change must miss L1's edge at 12 MB, admit L2's touch and miss L3's edge (the checks
can fail).
"""
import hashlib
import importlib.machinery
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time

if __name__ == "__main__" and ("-h" in sys.argv[1:] or "--help" in sys.argv[1:]):
    print(__doc__)
    sys.exit(0)

HERE = os.path.dirname(os.path.abspath(__file__))
ARGS = sys.argv[1:]
MOD = os.path.abspath(ARGS[ARGS.index("--module") + 1]) if "--module" in ARGS else os.path.join(HERE, "live_delta.py")
OLD = os.path.abspath(ARGS[ARGS.index("--old") + 1]) if "--old" in ARGS else None
REF = OLD or MOD
fails, ran = [], []
T0 = 1790000000.0
PAD = "x" * 1000


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


def line(ts, s, kind, nid):
    return (json.dumps({"ts": round(ts, 3), "session": s, "kind": kind, "id": nid}) + "\n").encode("utf-8")


def history(nbytes, t):
    """Older lines of an unrelated session, before everything a case needs."""
    out, n = [], 0
    while n < nbytes:
        ln = line(t + len(out) * 0.01, "old00000", "kw", PAD)
        out.append(ln)
        n += len(ln)
    return out


def padding(nbytes, t, s="pad00000", span=80.0):
    out, n = [], 0
    k = max(1, nbytes // 1070)
    while n < nbytes:
        ln = line(t + span * len(out) / k, s, "kw", PAD)
        out.append(ln)
        n += len(ln)
    return out


def write(td, name, prefix, new, tail=b""):
    p = os.path.join(td, name)
    with open(p, "wb") as fh:
        for ln in prefix:
            fh.write(ln)
        start = fh.tell()
        for ln in new:
            fh.write(ln)
        fh.write(tail)
    return p, start


def run(m, p, start, most=None):
    kc = os.path.join(os.path.dirname(p), "no_kinds.json")
    kw = {"most": most} if most is not None else {}
    ev, rd = m.read_events({"until": 0.0, "hubs": set()}, path=p, kinds_cache=kc, start=start, mode="bytes", **kw)
    edges = m.pairs_of(ev, set(), T0 + 10 ** 6, 0.0)[0]
    return {"new": sorted((s, t, x) for s, t, x, n in ev if n), "edges": {k: round(v[0], 6) for k, v in edges.items()},
            "rd": rd}


def full(p, start):
    """The whole log read: the module before this change reading back past the start of the file."""
    r = load(REF, "ld_ref_%d" % len(ran))
    if hasattr(r, "LOOKBACK"):
        r.LOOKBACK = 1 << 40
        return run(r, p, start)
    return run(r, p, start, most=1 << 40)


def case_l1(m, td, pad_mb):
    hist = history(10 << 20, T0 - 86400.0)
    prefix = hist + [line(T0, "s1aaaaaa", "file", "/work/a.py"), line(T0 + 50.0, "s5ended0", "mem", "note_of_s5")]
    prefix += padding(int(pad_mb * 1e6), T0 + 1.0)
    new = [line(T0 + 101.0, "s1aaaaaa", "mem", "lesson_x")]
    p, start = write(td, "l1_%s.jsonl" % pad_mb, prefix, new)
    return p, start, len(b"".join(hist))


EDGE1 = ("file:/work/a.py", "mem:lesson_x")


def main():
    m = load(MOD, "ld_new")
    td = tempfile.mkdtemp(prefix="live_delta_lookback_")
    try:
        # L1: invariance across paddings
        ok_all, lines = True, []
        for mb in (0, 4, 12, 24):
            p, start, hist_b = case_l1(m, td, mb)
            got, ref = run(m, p, start), full(p, start)
            same = got["new"] == ref["new"] and got["edges"] == ref["edges"]
            skipped = got["rd"]["context_skipped"]
            ok_all = ok_all and same and EDGE1 in got["edges"] and got["rd"]["ctx_start"] >= hist_b - m.MARK_STEP
            ok_all = ok_all and skipped > (mb * 1e6 / 1100 if mb else 0)
            lines.append("%d MB: edge %s, same as whole read %s, read from %.1f MB of %.1f, %d lines of other sessions "
                         "not parsed" % (mb, EDGE1 in got["edges"], same, got["rd"]["ctx_start"] / 1e6, start / 1e6, skipped))
        check(ok_all, "L1 one session's edge and new events stay those of the whole read at every padding (%s)" % "; ".join(lines))
        if OLD:
            o = load(OLD, "ld_old_l1")
            p, start, _ = case_l1(m, td, 12)
            got = run(o, p, start)
            check(EDGE1 not in got["edges"], "N1 the module before this change misses L1's edge at 12 MB (%s)" % (
                EDGE1 in got["edges"]))

        # L2: a bank touch before the padding
        prefix = history(1 << 20, T0 - 86400.0) + [line(T0 - 1000.0, "s1aaaaaa", "file", "/data/bank100/q.json"),
                                                  line(T0, "s1aaaaaa", "file", "/work/a.py")]
        prefix += padding(12_000_000, T0 + 1.0)
        p, start = write(td, "l2.jsonl", prefix, [line(T0 + 101.0, "s1aaaaaa", "mem", "lesson_x")])
        got, ref = run(m, p, start), full(p, start)
        tgt = ("s1aaaaaa", T0 + 101.0, "mem:lesson_x")
        check(tgt not in got["new"] and got["new"] == ref["new"] and got["edges"] == ref["edges"],
              "L2 a bank touch 1,000 s before, behind 12 MB: the touch is left out (%s), as by the whole read (%s)" % (
                  tgt not in got["new"], tgt not in ref["new"]))
        if OLD:
            got = run(load(OLD, "ld_old_l2"), p, start)
            check(tgt in got["new"], "N2 the module before this change admits it (%s)" % (tgt in got["new"]))

        # L3: a late-captured session between the context and the new touch
        prefix = history(1 << 20, T0 - 86400.0) + [line(T0, "s1aaaaaa", "file", "/work/a.py")]
        prefix += padding(12_000_000, T0 - 5 * 86400.0, s="late0000", span=3600.0)
        p, start = write(td, "l3.jsonl", prefix, [line(T0 + 101.0, "s1aaaaaa", "mem", "lesson_x")])
        got, ref = run(m, p, start), full(p, start)
        check(EDGE1 in got["edges"] and got["new"] == ref["new"] and got["edges"] == ref["edges"],
              "L3 12 MB of lines stamped five days earlier between context and touch: edge %s, same as whole read %s" % (
                  EDGE1 in got["edges"], got["edges"] == ref["edges"]))
        if OLD:
            got = run(load(OLD, "ld_old_l3"), p, start)
            check(EDGE1 not in got["edges"], "N3 the module before this change misses it (%s)" % (EDGE1 in got["edges"]))

        # L4: the cap binds
        prefix = [line(T0, "s1aaaaaa", "file", "/work/a.py")] + padding(12_000_000, T0 + 1.0)
        new = [line(T0 + 101.0, "s1aaaaaa", "mem", "lesson_x"), line(T0 + 100000.0, "s4bbbbbb", "file", "/work/b.py"),
               line(T0 + 100010.0, "s4bbbbbb", "mem", "lesson_y")]
        p, start = write(td, "l4.jsonl", prefix, new)
        got = run(m, p, start, most=1 << 20)
        e4 = ("file:/work/b.py", "mem:lesson_y")
        held = ("s1aaaaaa", T0 + 101.0, "mem:lesson_x")
        check(got["rd"]["withheld_new"] == 1 and held not in got["new"] and EDGE1 not in got["edges"] and e4 in got["edges"]
              and ("s4bbbbbb", T0 + 100010.0, "mem:lesson_y") in got["new"],
              "L4 cap 1 MiB, context 12 MB back: withheld %d (the touch is not counted, its edge absent: %s), the later "
              "touch counted with its edge (%s)" % (got["rd"]["withheld_new"], EDGE1 not in got["edges"], e4 in got["edges"]))

        # L5: a torn last line
        prefix = history(1 << 20, T0 - 86400.0) + [line(T0, "s1aaaaaa", "file", "/work/a.py")]
        torn = line(T0 + 102.0, "s1aaaaaa", "mem", "torn_one")[:-9]
        p, start = write(td, "l5.jsonl", prefix, [line(T0 + 101.0, "s1aaaaaa", "mem", "lesson_x")], tail=torn)
        got = run(m, p, start)
        mk = m.ts_marks(p, os.path.getsize(p))
        check(all(x[2] != "mem:torn_one" for x in got["new"]) and got["rd"]["end"] == os.path.getsize(p) - len(torn)
              and mk[-1][0] == os.path.getsize(p) - len(torn) and mk[-1][1] == T0 + 101.0,
              "L5 a torn last line: not new (%s), the read ends before it (%s), the index stops before it with maximum %s" % (
                  all(x[2] != "mem:torn_one" for x in got["new"]), got["rd"]["end"] == os.path.getsize(p) - len(torn),
                  mk[-1][1] == T0 + 101.0))

        # L6: the index cache
        p, start, _ = case_l1(m, td, 12)
        cache = os.path.join(td, "marks.json")
        read_bytes = []
        real = m._block_max

        def counting(body, ceiling):
            read_bytes.append(len(body))
            return real(body, ceiling)
        m._block_max = counting
        sha = hashlib.sha256(open(p, "rb").read()[:start]).hexdigest()
        a = m.ts_marks(p, start, sha=sha, cache=cache)
        n_a = sum(read_bytes)
        read_bytes.clear()
        b_ = m.ts_marks(p, start, sha=sha, cache=cache)
        n_b = sum(read_bytes)
        with open(p, "ab") as fh:
            for ln in padding(3_000_000, T0 + 200.0, s="more0000"):
                fh.write(ln)
        size2 = os.path.getsize(p)
        read_bytes.clear()
        c = m.ts_marks(p, size2, cache=cache)
        n_c = sum(read_bytes)
        m._block_max = real
        fresh = m.ts_marks(p, size2)
        with open(p, "r+b") as fh:                # one byte of the prefix changes, the length does not
            fh.seek(start // 2)
            ch = fh.read(1)
            fh.seek(start // 2)
            fh.write(b"y" if ch != b"y" else b"z")
        d = m.ts_marks(p, size2, cache=cache)
        fresh2 = m.ts_marks(p, size2)
        check(a == b_ and n_b == 0 and 0 < n_c < n_a and c[-1][1] == fresh[-1][1] and d == fresh2,
              "L6 index cache: reused with no block read (%s, %d bytes), extended over 3 MB reading %.1f MB (a fresh scan "
              "%.1f MB) to the same maximum (%s), recomputed after a changed byte (%s)" % (
                  a == b_, n_b, n_c / 1e6, n_a / 1e6, c[-1][1] == fresh[-1][1], d == fresh2))

        # L7: a line stamped in the future, 3 MB before the context, a 1 MiB cap, marks every 64 KiB
        future = time.time() + 10 * 86400.0
        prefix = history(500_000, T0 - 86400.0) + [line(future, "bad00000", "kw", "from_the_future")]
        prefix += history(3_000_000, T0 - 80000.0) + [line(T0, "s1aaaaaa", "file", "/work/a.py")]
        p, start = write(td, "l7.jsonl", prefix, [line(T0 + 101.0, "s1aaaaaa", "mem", "lesson_x")])
        step = m.MARK_STEP
        m.MARK_STEP = 64 << 10
        try:
            got = run(m, p, start, most=1 << 20)
            real = m._block_max
            m._block_max = lambda body, ceiling: real(body, float("inf"))
            try:
                blind = run(m, p, start, most=1 << 20)
            finally:
                m._block_max = real
        finally:
            m.MARK_STEP = step
        check(EDGE1 in got["edges"] and got["rd"]["withheld_new"] == 0 and EDGE1 not in blind["edges"]
              and blind["rd"]["withheld_new"] == 1,
              "L7 a line stamped ten days ahead, 3 MB back, 1 MiB cap: edge %s, withheld %d; the same index letting that "
              "line set its maximum withholds %d and loses the edge (%s)" % (
                  EDGE1 in got["edges"], got["rd"]["withheld_new"], blind["rd"]["withheld_new"], EDGE1 not in blind["edges"]))

        # L8: a mark at every line, the needed line first in the read
        step = m.MARK_STEP
        m.MARK_STEP = 50
        try:
            prefix = [line(T0 - 86400.0 + i, "old00000", "kw", "h%d" % i) for i in range(40)]
            at_ctx = len(b"".join(prefix))
            prefix += [line(T0, "s1aaaaaa", "file", "/work/a.py")]
            prefix += [line(T0 + 10.0 + i, "pad00000", "kw", "p%d" % i) for i in range(10)]
            p, start = write(td, "l8.jsonl", prefix, [line(T0 + 101.0, "s1aaaaaa", "mem", "lesson_x")])
            got = run(m, p, start)
        finally:
            m.MARK_STEP = step
        check(EDGE1 in got["edges"] and got["rd"]["ctx_start"] == at_ctx,
              "L8 marks at every line: the read starts exactly at the context line (%s) and reads it (edge %s)" % (
                  got["rd"]["ctx_start"] == at_ctx, EDGE1 in got["edges"]))

        # L9: the live path
        p, start, _ = case_l1(m, td, 12)
        sha = hashlib.sha256(open(p, "rb").read()[:start]).hexdigest()
        built = time.time()
        live = os.path.join(td, "live")
        os.makedirs(live, exist_ok=True)
        with open(os.path.join(td, "STATUS.json"), "w") as fh:
            json.dump({"build_events_bytes": start, "build_store_built": built, "build_events_sha": sha}, fh)
        m.EVENTS, m.STATUS, m.HOME, m.LIVE = p, os.path.join(td, "STATUS.json"), td, live
        m.MARKS = os.path.join(live, "ts_marks.json")
        b = {"until": T0, "store_built": built, "hubs": set()}
        kc = os.path.join(td, "no_kinds.json")
        real = m._block_max
        reads = []
        m._block_max = lambda body, ceiling: reads.append(len(body)) or real(body, ceiling)
        try:
            r1 = m.read_events(b, path=p, kinds_cache=kc)
            n1 = sum(reads)
            reads.clear()
            r2 = m.read_events(b, path=p, kinds_cache=kc)
            n2 = sum(reads)
            with open(p, "ab") as fh:             # the capture appends; the store has not rebuilt
                fh.write(line(T0 + 300.0, "s1aaaaaa", "mem", "lesson_z"))
            r3 = m.read_events(b, path=p, kinds_cache=kc)
        finally:
            m._block_max = real
        ref, ref3 = run(m, p, start), None
        ref3 = full(p, start)
        new_of = lambda r: sorted((s, t, x) for s, t, x, n in r[0] if n)
        cached = json.load(open(m.MARKS)) if os.path.exists(m.MARKS) else {}
        check(r1[1]["mode"] == "bytes" and new_of(r1) == new_of(r2) == ref["new"][:1] and new_of(r3) == ref3["new"]
              and n1 > 0 and n2 == 0 and cached.get("sha") == sha and cached.get("upto") == start,
              "L9 the live path: bytes mode (%s), two runs give the explicit start's new events (%s) - the second reading no "
              "block of the index (%d bytes, the first %.1f MB) - a third after an append gives the whole read's (%s), the "
              "cache keyed to the verified prefix (%s)" % (r1[1]["mode"], new_of(r1) == new_of(r2) == ref["new"][:1], n2,
                                                           n1 / 1e6, new_of(r3) == ref3["new"], cached.get("sha") == sha))
    finally:
        shutil.rmtree(td, ignore_errors=True)
    print("FAIL" if fails else "PASS", "- %d controls, %d failed" % (len(ran), len(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
