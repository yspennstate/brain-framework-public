"""test_graph_learn_exactly_once.py - every membrane evaluation is learned from once, and only when its text was printed; a
log replaced under the learner's offset is read again, not skipped.

    python test_graph_learn_exactly_once.py [--module GRAPH_LEARN.py] [--membrane LIVE_MEMBRANE.py] [--old OLD_GRAPH_LEARN.py]

graph_learn's paths are pointed at a throwaway folder; nothing live is read or written. Every session here is interactive
(entry point cli). Each fire serves one memory line lit by its own source node, and the session reads that memory.
 X1 two evaluations of one session in the same tenth of a second, same event and entry point, other lines: both learned
 X2 one evaluation written twice (a retry): learned once
 X3 two sessions whose ids share the first 8 characters, same tenth, event and entry point: both learned
 X4 a record from before the eid keeps its old identity
 E1 a shown line whose evaluation has no receipt from the hook: it teaches nothing, and the status counts it
 E2 the same line with its receipt: it teaches
 E3 mark_emitted writes one receipt for the evaluation that returned text, and none again
 G1 the fire log replaced by one of the same size: its rows are read, and the reset is reported
 G2 replaced by a larger one: its first rows are read too
 G3 an append: only the new row
 G4 a replaced log whose evaluations were already learned: not learned again, a new one learned once
With --old, the learner given must merge X1 (N1), learn E1 without a receipt (N2), and read nothing from G1 (N3).
"""
import importlib.machinery
import importlib.util
import io
import contextlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time

if __name__ == "__main__" and ("-h" in sys.argv[1:] or "--help" in sys.argv[1:]):
    print(__doc__)
    sys.exit(0)

HERE = os.path.dirname(os.path.abspath(__file__))
ARGS = sys.argv[1:]


def arg(name, default=None):
    return os.path.abspath(ARGS[ARGS.index(name) + 1]) if name in ARGS else default


MOD = arg("--module", os.path.join(HERE, "graph_learn.py"))
MEMB = arg("--membrane", os.path.join(HERE, "live_membrane.py"))
OLD = arg("--old")
fails, ran = [], []
SID = "bbbbbbbb-2222-4000-8000-000000000001"
SID2 = "bbbbbbbb-3333-4000-8000-000000000002"


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


def fire(t, sid, mem, src, eid=None):
    f = {"t": t, "sid": sid, "ev": "post", "ep": "cli", "mem": [[mem, 1.0]], "served": [["mem", mem, [src]]]}
    if eid:
        f["eid"] = eid
    return f


def setup(m, td, fires, reads, receipts=()):
    live = os.path.join(td, "live")
    os.makedirs(live, exist_ok=True)
    m.HOME, m.LIVE = td, live
    m.EVENTS = os.path.join(td, "events_touch.jsonl")
    for name in ("FIRES", "RECALL_LOG", "LEARNED", "POLICY", "PRECISION", "STATUS", "STATE", "LOGMD"):
        setattr(m, name, os.path.join(live, os.path.basename(getattr(m, name))))
    m.EMITTED = os.path.join(live, "emitted.jsonl")
    m.DIAL = os.path.join(td, "brain_graph_dial.json")
    now = time.time()
    with open(m.STATE, "w", encoding="utf-8") as fh:
        json.dump({"events_offset": 0, "fires_offset": 0, "recall_offset": 0, "pending": [], "pending_recalls": [],
                   "recent": [], "judged": [], "recall_judged": [], "served_log": [], "started": now - 86400}, fh)
    with open(m.DIAL, "w", encoding="utf-8") as fh:
        json.dump({"lambda": 0.1}, fh)
    events = [{"ts": t, "session": s[:8], "kind": "mem", "id": mem} for t, s, mem in reads]
    events.append({"ts": now - 5, "session": SID[:8], "kind": "file", "id": "closer"})
    with open(m.EVENTS, "w", encoding="utf-8") as fh:
        fh.write("".join(json.dumps(e) + "\n" for e in sorted(events, key=lambda e: e["ts"])))
    open(m.RECALL_LOG, "w").close()
    with open(m.FIRES, "w", encoding="utf-8") as fh:
        fh.write("".join(json.dumps(f) + "\n" for f in fires))
    with open(m.EMITTED, "w", encoding="utf-8") as fh:
        fh.write("".join(json.dumps({"eid": e, "t": now}) + "\n" for e in receipts))
    return now


def run(m):
    argv = sys.argv
    sys.argv = [argv[0]]
    try:
        with contextlib.redirect_stdout(io.StringIO()) as out:
            m.main()
        return json.loads(out.getvalue() or "{}")
    finally:
        sys.argv = argv


def learned(m):
    if not os.path.exists(m.LEARNED):
        return {}
    db = sqlite3.connect(m.LEARNED)
    rows = {(s, d): (p, n) for s, d, p, n in db.execute("SELECT src, dst, pos, neg FROM learned")}
    db.close()
    return rows


def learner_cases(path, tag):
    out = {}
    t0 = time.time() - 3 * 3600
    t = round(t0, 1)
    cases = {
        "X1": ([fire(t, SID, "ma", "src_a", "e1"), fire(t, SID, "mb", "src_b", "e2")],
               [(t + 60, SID, "ma"), (t + 61, SID, "mb")], ("e1", "e2")),
        "X2": ([fire(t, SID, "ma", "src_a", "e1"), fire(t, SID, "ma", "src_a", "e1")], [(t + 60, SID, "ma")], ("e1",)),
        "X3": ([fire(t, SID, "ma", "src_a", "e1"), fire(t, SID2, "mb", "src_b", "e2")],
               [(t + 60, SID, "ma"), (t + 61, SID2, "mb")], ("e1", "e2")),
        "E1": ([fire(t, SID, "ma", "src_a", "e1")], [(t + 60, SID, "ma")], ()),
        "E2": ([fire(t, SID, "ma", "src_a", "e1")], [(t + 60, SID, "ma")], ("e1",)),
    }
    for name, (fires, reads, receipts) in cases.items():
        td = tempfile.mkdtemp(prefix="gl_once_%s_%s_" % (tag, name))
        try:
            m = load(path, "gl_%s_%s" % (tag, name))
            setup(m, td, fires, reads, receipts)
            st = run(m)
            out[name] = {"learned": learned(m), "status": st}
        finally:
            shutil.rmtree(td, ignore_errors=True)
    return out


def bound_cases(path, tag):
    out = {}
    m = load(path, "gl_bound_%s" % tag)
    td = tempfile.mkdtemp(prefix="gl_bound_%s_" % tag)
    try:
        p = os.path.join(td, "fires.jsonl")

        def write(rows):
            with open(p, "w", encoding="utf-8") as fh:
                fh.write("".join(json.dumps(r) + "\n" for r in rows))

        def read(st):
            if hasattr(m, "read_bound"):
                return m.read_bound(st, "fires", p)
            rows, st["fires_offset"] = m.read_new(p, st.get("fires_offset", 0))
            return rows, None
        write([{"a": 1}, {"a": 2}])
        st = {}
        read(st)
        write([{"b": 3}, {"b": 4}])                      # same size, other rows
        rows, reset = read(st)
        out["G1"] = (rows, reset)
        write([{"a": 1}, {"a": 2}])
        st = {}
        read(st)
        write([{"c": 5}, {"c": 6}, {"c": 7}, {"c": 8}])  # larger, other rows
        rows, reset = read(st)
        out["G2"] = (rows, reset)
        write([{"a": 1}, {"a": 2}])
        st = {}
        read(st)
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"a": 3}) + "\n")
        out["G3"] = read(st)
    finally:
        shutil.rmtree(td, ignore_errors=True)
    return out


def replaced_log_case(path):
    m = load(path, "gl_g4")
    td = tempfile.mkdtemp(prefix="gl_g4_")
    try:
        t = round(time.time() - 3 * 3600, 1)
        first = [fire(t, SID, "ma", "src_a", "e1")]
        setup(m, td, first, [(t + 60, SID, "ma"), (t + 70, SID, "mb")], ("e1", "e2"))
        run(m)
        # the log rewritten (the same evaluation, its keys in another order) with one new evaluation after it
        rewritten = [dict(sorted(first[0].items(), reverse=True)), fire(t + 5, SID, "mb", "src_b", "e2")]
        with open(m.FIRES, "w", encoding="utf-8") as fh:
            fh.write("".join(json.dumps(f) + "\n" for f in rewritten))
        st = run(m)
        return learned(m), st
    finally:
        shutil.rmtree(td, ignore_errors=True)


def membrane_case():
    lm = load(MEMB, "lm_once")
    td = tempfile.mkdtemp(prefix="lm_once_")
    try:
        lm.EMITTED = os.path.join(td, "emitted.jsonl")
        lm.LAST_EID[0] = "abc123"
        lm.mark_emitted()
        lm.mark_emitted()
        rows = [json.loads(l) for l in open(lm.EMITTED, encoding="utf-8")]
        return rows
    finally:
        shutil.rmtree(td, ignore_errors=True)


def main():
    r = learner_cases(MOD, "new")
    x1 = r["X1"]["learned"]
    check(x1.get(("src_a", "mem:ma")) == (1.0, 0.0) and x1.get(("src_b", "mem:mb")) == (1.0, 0.0),
          "X1 two evaluations sharing tenth, session, event and entry point: both learned %s" % x1)
    check(r["X2"]["learned"].get(("src_a", "mem:ma")) == (1.0, 0.0),
          "X2 one evaluation written twice: learned once %s" % r["X2"]["learned"])
    x3 = r["X3"]["learned"]
    check(x3.get(("src_a", "mem:ma")) == (1.0, 0.0) and x3.get(("src_b", "mem:mb")) == (1.0, 0.0),
          "X3 two sessions sharing their first 8 characters: both learned %s" % x3)
    m = load(MOD, "gl_x4")
    legacy = {"t": 1000.0, "sid": "abcdefgh", "ev": "post", "ep": "cli"}
    import hashlib
    old_id = hashlib.sha1(json.dumps([1000.0, "abcdefgh", "post", "cli"]).encode("utf-8")).hexdigest()
    check(m.obs_id(legacy) == old_id, "X4 a record without an eid keeps its old identity")
    check(r["E1"]["learned"] == {} and r["E1"]["status"].get("shown_without_receipt") == 1,
          "E1 no receipt: nothing learned %s, counted %s" % (r["E1"]["learned"], r["E1"]["status"].get(
              "shown_without_receipt")))
    check(r["E2"]["learned"].get(("src_a", "mem:ma")) == (1.0, 0.0), "E2 with its receipt: learned %s" % r["E2"]["learned"])
    mc = membrane_case()
    check(len(mc) == 1 and mc[0]["eid"] == "abc123", "E3 one receipt for the evaluation, none again: %s" % mc)
    b = bound_cases(MOD, "new")
    check(b["G1"][0] == [{"b": 3}, {"b": 4}] and b["G1"][1] is True,
          "G1 same-size replacement: read %s, reset %s" % b["G1"])
    check(b["G2"][0] == [{"c": 5}, {"c": 6}, {"c": 7}, {"c": 8}] and b["G2"][1] is True,
          "G2 larger replacement: read %s, reset %s" % b["G2"])
    check(b["G3"][0] == [{"a": 3}] and b["G3"][1] is False, "G3 an append: read %s, reset %s" % b["G3"])
    g4, st4 = replaced_log_case(MOD)
    check(g4.get(("src_a", "mem:ma")) == (1.0, 0.0) and g4.get(("src_b", "mem:mb")) == (1.0, 0.0)
          and "fires" in (st4.get("logs_read_again") or []),
          "G4 a replaced log: the learned evaluation not learned again, the new one once %s, reset %s" % (
              g4, st4.get("logs_read_again")))
    if OLD:
        o = learner_cases(OLD, "old")
        ob = bound_cases(OLD, "old")
        check(len(o["X1"]["learned"]) < 2, "N1 the learner given merges X1: %s (the check can fail)" % o["X1"]["learned"])
        check(o["E1"]["learned"] != {}, "N2 the learner given learns E1 with no receipt: %s (the check can fail)" % (
            o["E1"]["learned"]))
        check(ob["G1"][0] == [], "N3 the learner given reads nothing from G1's replacement: %s (the check can fail)" % (
            ob["G1"][0],))
    print("FAIL" if fails else "PASS", "- %d controls, %d failed" % (len(ran), len(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
