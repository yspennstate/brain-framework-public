"""test_graph_learn_prefix_binding.py - the learner's log cursor is bound to every byte it consumed (29 Sep 2026, claude-825b1305;
scheduled audit account04/B15 r8, B15-R73-MIDDLE-REWRITE-ESCAPES-CURSOR-BINDING-20260929-01, test T-R73-FULL-PREFIX-GENERATION-BINDING).

    python test_graph_learn_prefix_binding.py [--module GRAPH_LEARN.py] [--old OLD_GRAPH_LEARN.py]

Everything runs in a throwaway folder; nothing live is read or written. A log of about 300 KB (more than the 192 KiB the
finding asks for) is consumed to its end, then changed:
 P1 a same-size rewrite of the middle, the first and last 64 KB untouched: reset, and the changed rows come back
 P2 a same-size change inside the first 64 KB: reset
 P3 a same-size change inside the last 64 KB before the offset: reset
 P4 a pure append: no reset, exactly the appended row
 P5 a larger replacement: reset, the reason recorded ("changed")
 P6 a truncation: reset, the reason recorded ("shorter"), the remaining rows come back
 P7 a rotation (the file moved away, a new two-row file in its place): reset, the new rows
 P8 a torn last line: left pending, then returned once, whole, when it is completed, with no reset
 P9 a cursor written in the old sampled form: carried across once without a reset, and stored in the new form
 P10 the learner killed after learning and before its cursor was saved: run again, nothing is learned twice
With --old, the learner given must miss P1 (no reset, no rows: N1) and not report P6's truncation (N2).
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


def arg(name, default=None):
    return os.path.abspath(ARGS[ARGS.index(name) + 1]) if name in ARGS else default


MOD = arg("--module", os.path.join(HERE, "graph_learn.py"))
OLD = arg("--old")
fails, ran = [], []


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


def row(i, v="a"):
    return json.dumps({"i": "r%05d" % i, "v": v * 60}) + "\n"


def write_rows(p, rows):
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("".join(rows))


def big_log(p):
    rows = [row(i) for i in range(3600)]          # about 300 KB
    write_rows(p, rows)
    return rows


def edit_at(p, lo, hi, v):
    """Rewrite, at the same size, the rows lying wholly between byte lo and byte hi; returns their ids."""
    data = open(p, "rb").read()
    out, pos, changed = [], 0, []
    for ln in data.split(b"\n")[:-1]:
        start, end = pos, pos + len(ln) + 1
        pos = end
        if start >= lo and end <= hi:
            j = json.loads(ln)
            changed.append(j["i"])
            ln = json.dumps({"i": j["i"], "v": v * 60}).encode()
        out.append(ln + b"\n")
    new = b"".join(out)
    assert len(new) == len(data)
    open(p, "wb").write(new)
    return changed


def bound(m, st, p):
    return m.read_bound(st, "fires", p)


def cases(path, tag):
    m = load(path, "gl_prefix_%s" % tag)
    td = tempfile.mkdtemp(prefix="gl_prefix_%s_" % tag)
    out = {}
    try:
        p = os.path.join(td, "fires.jsonl")
        size = len("".join(big_log(p)).encode())

        def consumed():
            big_log(p)
            st = {}
            bound(m, st, p)
            return st

        st = consumed()
        changed = edit_at(p, 100 * 1024, 200 * 1024, "b")
        rows, reset = bound(m, st, p)
        out["P1"] = (reset, len(changed), sum(1 for r in rows if r["i"] in set(changed) and r["v"].startswith("b")),
                     st.get("reset_reasons", {}).get("fires"))
        st = consumed()
        edit_at(p, 1000, 20 * 1024, "c")
        out["P2"] = bound(m, st, p)[1]
        st = consumed()
        edit_at(p, size - 30 * 1024, size - 1000, "d")
        out["P3"] = bound(m, st, p)[1]
        st = consumed()
        with open(p, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(row(99999, "z"))
        rows, reset = bound(m, st, p)
        out["P4"] = (reset, [r["i"] for r in rows])
        st = consumed()
        write_rows(p, [row(i, "e") for i in range(4000)])
        rows, reset = bound(m, st, p)
        out["P5"] = (reset, len(rows), (st.get("reset_reasons", {}).get("fires") or {}).get("why", ""))
        st = consumed()
        write_rows(p, [row(i) for i in range(100)])
        rows, reset = bound(m, st, p)
        out["P6"] = (reset, len(rows), (st.get("reset_reasons", {}).get("fires") or {}).get("why", ""))
        st = consumed()
        os.replace(p, p + ".1")
        write_rows(p, [row(1, "f"), row(2, "f")])
        rows, reset = bound(m, st, p)
        out["P7"] = (reset, [r["v"][0] for r in rows])
        os.remove(p + ".1")
        write_rows(p, [row(1), row(2)])
        with open(p, "a", encoding="utf-8", newline="\n") as fh:
            fh.write('{"i": "r00003", "v": "g')
        st = {}
        first = [r["i"] for r in bound(m, st, p)[0]]
        with open(p, "a", encoding="utf-8", newline="\n") as fh:
            fh.write('gg"}\n')
        rows, reset = bound(m, st, p)
        out["P8"] = (first, [r["i"] for r in rows], reset)
        if hasattr(m, "_sampled_mark"):
            big_log(p)
            st = {}
            bound(m, st, p)
            st["fires_mark"] = m._sampled_mark(p, st["fires_offset"])
            rows, reset = bound(m, st, p)
            out["P9"] = (reset, rows, isinstance(st.get("fires_mark"), dict))
    finally:
        shutil.rmtree(td, ignore_errors=True)
    return out


def crash_case(path):
    """P10: the learner learns, then dies before its state (the cursor) is written; the next run reads the same rows."""
    sys.path.insert(0, HERE)
    import test_graph_learn_exactly_once as T
    m = load(path, "gl_crash")
    td = tempfile.mkdtemp(prefix="gl_crash_")
    try:
        t = round(time.time() - 3 * 3600, 1)
        T.setup(m, td, [T.fire(t, T.SID, "ma", "src_a", "e1")], [(t + 60, T.SID, "ma")], ("e1",))
        before = open(m.STATE, encoding="utf-8").read()
        T.run(m)
        first = T.learned(m)
        open(m.STATE, "w", encoding="utf-8").write(before)   # the cursor as it was before the run
        T.run(m)
        return first, T.learned(m)
    finally:
        shutil.rmtree(td, ignore_errors=True)


def main():
    c = cases(MOD, "new")
    check(c["P1"][0] is True and c["P1"][1] > 100 and c["P1"][2] == c["P1"][1],
          "P1 middle rewrite, head and tail untouched: reset %s, %d rows changed, %d of them returned changed (%s)" % (
              c["P1"][0], c["P1"][1], c["P1"][2], c["P1"][3]))
    check(c["P2"] is True, "P2 a change in the first 64 KB: reset %s" % c["P2"])
    check(c["P3"] is True, "P3 a change in the last 64 KB before the offset: reset %s" % c["P3"])
    check(c["P4"] == (False, ["r99999"]), "P4 an append: %s" % (c["P4"],))
    check(c["P5"][0] is True and c["P5"][1] == 4000 and "changed" in c["P5"][2], "P5 a larger replacement: %s" % (c["P5"],))
    check(c["P6"][0] is True and c["P6"][1] == 100 and "shorter" in c["P6"][2], "P6 a truncation: %s" % (c["P6"],))
    check(c["P7"] == (True, ["f", "f"]), "P7 a rotation: %s" % (c["P7"],))
    check(c["P8"] == (["r00001", "r00002"], ["r00003"], False), "P8 a torn last line, then completed: %s" % (c["P8"],))
    check(c.get("P9") is not None and c["P9"][0] is False and c["P9"][1] == [] and c["P9"][2] is True,
          "P9 an old sampled cursor carried across once: %s" % (c.get("P9"),))
    first, second = crash_case(MOD)
    check(first.get(("src_a", "mem:ma")) == (1.0, 0.0) and second == first,
          "P10 killed before the cursor was saved: learned %s, after the rerun %s" % (first, second))
    if OLD:
        o = cases(OLD, "old")
        check(o["P1"][0] is False and o["P1"][2] == 0,
              "N1 the learner given misses P1: reset %s, %d changed rows returned (the check can fail)" % (o["P1"][0], o["P1"][2]))
        check(o["P6"][0] is False, "N2 the learner given does not report P6's truncation: reset %s (the check can fail)" % (
            o["P6"][0],))
    print("%s - %d controls, %d failed" % ("FAIL" if fails else "PASS", len(ran), len(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
