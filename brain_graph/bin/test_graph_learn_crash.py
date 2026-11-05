"""test_graph_learn_crash.py - a learner run that dies after its database commit and before its state is saved does not
count the same evidence twice (28 Sep 2026, claude-825b1305; interactive audit IA-20260928-06).

    python test_graph_learn_crash.py [--module GRAPH_LEARN.py] [--old OLD_GRAPH_LEARN.py]

graph_learn's paths are pointed at a throwaway folder; nothing live is read or written (transcript_index and the kind
lookup are stubbed: every session here is his). One membrane evaluation served a memory line that the session then read,
and thirty logged searches whose first opened memory ranks higher with more graph weight. The state file is written last
(step 8); the crash is injected there, after the learned counts are committed and the dial saved.
 C1 crash, then a clean run: the learned edge counts the read once (pos 1)
 C2 the dial moved one step across the crash and the rerun, not two
 C3 a clean run, then another with nothing new: nothing moves (the edge stays at 1, the dial where it was)
With --old, graph_learn before this change must count the read twice and move the dial twice (the check can fail).
"""
import importlib.machinery
import importlib.util
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
MOD = os.path.abspath(ARGS[ARGS.index("--module") + 1]) if "--module" in ARGS else os.path.join(HERE, "graph_learn.py")
OLD = os.path.abspath(ARGS[ARGS.index("--old") + 1]) if "--old" in ARGS else None
fails, ran = [], []
SID = "aaaaaaaa-1111-4000-8000-000000000000"
# the searches come from ten sessions: the learner moves the dial only on ten or more, each one vote (30 Sep, the
# 1000-page report's D03-T02); SID is the first of them and the one the membrane evaluation belongs to
SESS = [SID] + ["%08x-1111-4000-8000-000000000000" % (0xbbbbbbb0 + k) for k in range(9)]


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


class Crash(Exception):
    pass


def setup(m, td):
    live = os.path.join(td, "live")
    os.makedirs(live)
    m.HOME, m.LIVE = td, live
    m.EVENTS = os.path.join(td, "events_touch.jsonl")
    for name in ("FIRES", "RECALL_LOG", "LEARNED", "POLICY", "PRECISION", "STATUS", "STATE", "LOGMD"):
        setattr(m, name, os.path.join(live, os.path.basename(getattr(m, name))))
    m.DIAL = os.path.join(td, "brain_graph_dial.json")
    m.transcript_index = lambda max_age_days=3.0: {}
    m.session_kind = lambda sid, idx, cache: "his"
    now = time.time()
    t0 = now - 7200
    with open(m.STATE, "w", encoding="utf-8") as fh:
        json.dump({"events_offset": 0, "fires_offset": 0, "recall_offset": 0, "pending": [], "pending_recalls": [],
                   "recent": [], "judged": [], "recall_judged": [], "served_log": [], "started": t0 - 60}, fh)
    with open(m.DIAL, "w", encoding="utf-8") as fh:
        json.dump({"lambda": 0.1}, fh)
    events = [{"ts": t0 + 60, "session": SID[:8], "kind": "mem", "id": "memory_x"}]
    recs = []
    for i in range(30):                     # thirty searches; the opened memory o ranks first only at lambda >= 0.2
        t = now - 3 * 3600 + i * 120        # all judged: in the six-hour event window and closed well before now
        sid = SESS[i % len(SESS)]
        recs.append({"t": t, "session": sid, "entry": "cli", "shown": ["x_mem", "o_mem", "z_mem"],
                     "body": [["x_mem", 1.0], ["o_mem", 0.8], ["z_mem", 0.0]],
                     "graph": [["o_mem", 1.0], ["x_mem", 0.0], ["z_mem", 0.5]]})
        events.append({"ts": t + 5, "session": sid[:8], "kind": "mem", "id": "o_mem"})
    events.append({"ts": now - 5, "session": SID[:8], "kind": "file", "id": "closer"})   # closes every window
    with open(m.EVENTS, "w", encoding="utf-8") as fh:
        fh.write("".join(json.dumps(e) + "\n" for e in sorted(events, key=lambda e: e["ts"])))
    with open(m.RECALL_LOG, "w", encoding="utf-8") as fh:
        fh.write("".join(json.dumps(r) + "\n" for r in recs))
    fire = {"t": t0, "sid": SID, "ev": "UserPromptSubmit", "ep": "cli", "mem": [["memory_x", 1.0]],
            "served": [["mem", "memory_x", ["src_node"]]]}
    with open(m.FIRES, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(fire) + "\n")


def run(m, crash_on_state=False):
    real = m.jsave

    def jsave(p, obj):
        if crash_on_state and p == m.STATE:
            raise Crash()
        return real(p, obj)
    m.jsave = jsave
    argv = sys.argv
    sys.argv = [argv[0]]
    try:
        m.main()
        return "ok"
    except Crash:
        return "crashed"
    finally:
        sys.argv = argv
        m.jsave = real


def pos(m):
    db = sqlite3.connect(m.LEARNED)
    r = db.execute("SELECT pos, neg FROM learned WHERE src = 'src_node' AND dst = 'mem:memory_x'").fetchone()
    db.close()
    return r


def dial(m):
    with open(m.DIAL, encoding="utf-8") as fh:
        return float(json.load(fh)["lambda"])


def scenario(path, tag):
    out = {}
    import io
    import contextlib
    for name, crash in (("crash", True), ("clean", False)):
        td = tempfile.mkdtemp(prefix="gl_crash_%s_%s_" % (tag, name))
        try:
            m = load(path, "gl_%s_%s" % (tag, name))
            setup(m, td)
            with contextlib.redirect_stdout(io.StringIO()):
                first = run(m, crash_on_state=crash)
                d1 = dial(m)
                second = run(m)
            out[name] = {"first": first, "second": second, "pos": pos(m), "dial_after_first": d1, "dial": dial(m)}
        finally:
            shutil.rmtree(td, ignore_errors=True)
    return out


def main():
    r = scenario(MOD, "new")
    c, k = r["crash"], r["clean"]
    check(c["first"] == "crashed" and c["pos"] == (1.0, 0.0),
          "C1 crash after the commit, then a clean run: the read counted %s (want 1)" % (c["pos"],))
    check(abs(c["dial_after_first"] - 0.15) < 1e-9 and abs(c["dial"] - 0.15) < 1e-9,
          "C2 the dial across the crash and the rerun: 0.1 -> %.2f -> %.2f (want one step, 0.15)" % (
              c["dial_after_first"], c["dial"]))
    check(k["first"] == "ok" and k["pos"] == (1.0, 0.0) and abs(k["dial"] - 0.15) < 1e-9,
          "C3 a clean run, then one with nothing new: the read counted %s, the dial %.2f" % (k["pos"], k["dial"]))
    if OLD:
        o = scenario(OLD, "old")["crash"]
        check(o["pos"] == (2.0, 0.0) and abs(o["dial"] - 0.2) < 1e-9,
              "N1 graph_learn before this change counts the read %s and moves the dial to %.2f across the crash (the "
              "check can fail)" % (o["pos"], o["dial"]))
    print("FAIL" if fails else "PASS", "- %d controls, %d failed" % (len(ran), len(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
