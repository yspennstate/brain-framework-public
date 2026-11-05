"""Controls for graph_learn.py's recall weight (27 Sep, claude-825b1305): it learns only from agents at work, with the first
memory opened after a search as the target. Runs graph_learn.main() against a temporary live folder and a temporary
projects folder, so nothing live is read or written. Counts only.

    python test_graph_learn_kinds.py

1. session_kind: an interactive transcript reads 'his', a '[headless-worker]' transcript 'test', another headless one
   'script', a session with no transcript 'unknown'
2. forty searches by an interactive session whose first opened memory ranks first only at a graph weight of 0.3 or
   more move the weight up one step, 0.20 -> 0.25
3. the same forty searches by a test worker leave the weight where it is and are counted as 'test'
4. the same forty by an interactive session whose first open ranks first only at weight 0 move it down, 0.20 -> 0.15
5. the target is the first memory opened: a later open of a memory that would favour the other direction changes nothing
6. a search's window of opens ends at the same session's next search, and a logged session joins without 'recalled' events
7. the membrane: a served line taken up in an interactive session teaches its edge; in a test worker's session nothing
8. a real sample: the live recall log and the extracted events of the last six hours, run dry against the live projects
   folder, count test workers and leave the weight unchanged (skipped when the extract is missing)
"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
results = []


def check(name, ok, detail=""):
    results.append(ok)
    print("%s %s %s" % ("PASS" if ok else "FAIL", name, detail), flush=True)


def load():
    spec = importlib.util.spec_from_file_location("graph_learn_t", os.path.join(HERE, "graph_learn.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def transcript(folder, sid, entry, first_prompt):
    rows = [{"type": "queue-operation"}, {"type": "attachment", "entrypoint": entry},
            {"type": "user", "entrypoint": entry, "message": {"role": "user", "content": first_prompt}}]
    with open(os.path.join(folder, sid + ".jsonl"), "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def run(gl, tmp, recs, events, now, dry=False, lam=0.2, fires=()):
    live = os.path.join(tmp, "live")
    if os.path.isdir(live):
        shutil.rmtree(live)
    os.makedirs(live)
    ev_path = os.path.join(tmp, "events.jsonl")
    open(ev_path, "w").close()
    with open(os.path.join(live, "recall_log.jsonl"), "w", encoding="utf-8") as fh:
        for r in recs:
            fh.write(json.dumps(r) + "\n")
    with open(os.path.join(live, "fires.jsonl"), "w", encoding="utf-8") as fh:
        for f in fires:
            fh.write(json.dumps(f) + "\n")
    dial = os.path.join(tmp, "dial.json")
    json.dump({"lambda": lam}, open(dial, "w"))
    st = {"events_offset": 0, "fires_offset": 0, "recall_offset": 0, "pending": [], "pending_recalls": [],
          "recent": events, "judged": [], "recall_judged": [], "served_log": [], "started": now - 86400,
          "last_event_ts": max(e[0] for e in events)}
    json.dump(st, open(os.path.join(live, "learn_state.json"), "w"))
    json.dump({}, open(os.path.join(live, "policy.json"), "w"))
    gl.LIVE, gl.EVENTS, gl.FIRES = live, ev_path, os.path.join(live, "fires.jsonl")
    gl.RECALL_LOG, gl.LEARNED = os.path.join(live, "recall_log.jsonl"), os.path.join(live, "learned.sqlite")
    gl.POLICY, gl.PRECISION = os.path.join(live, "policy.json"), os.path.join(live, "precision.json")
    gl.STATUS, gl.STATE, gl.LOGMD = (os.path.join(live, "LIVE_STATUS.json"), os.path.join(live, "learn_state.json"),
                                     os.path.join(live, "LEARNING_LOG.md"))
    gl.DIAL = dial
    real_time = gl.time.time
    gl.time.time = lambda: now
    argv = sys.argv
    sys.argv = ["graph_learn.py"] + (["--dry"] if dry else [])
    import contextlib
    import io
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            gl.main()
    finally:
        gl.time.time = real_time
        sys.argv = argv
    status = json.loads(buf.getvalue())
    return status, json.load(open(dial))["lambda"]


def synthetic(sid, n, now, entry=None, target="c", later=None, x=0.667):
    """n searches whose body ranks a first and c second, each followed by an open of `target` (and of `later` a minute
    after it). The graph puts c first once the weight passes (1 - x) / (2 - x): 0.25 at x = 0.667, 0.125 at x = 0.857."""
    recs, events = [], []
    for i in range(n):
        t = now - 7200 + i * 60
        body = [["a", 1.0], ["c", x], ["b", 0.3], ["d", 0.0]]
        graph = [["c", 1.0], ["a", 0.0]]
        r = {"t": t, "q": "q%d" % i, "lam": 0.2, "body": body, "graph": graph, "shown": ["a", "c", "b", "d"],
             "session": sid}
        if entry:
            r["entry"] = entry
        recs.append(r)
        events.append([t + 30, sid[:8], "mem", target])
        if later:
            events.append([t + 90, sid[:8], "mem", later])
    events.append([now - 5, sid[:8], "dir", "x"])      # the capture has seen past every search's ten-minute window
    return recs, events


def synthetic_sessions(plan, now, x=0.667, later=None):
    """Searches by several sessions, one minute apart in plan order, each an interactive session (entry cli) opening its
    target half a minute after the search (and `later` a minute after that): plan is [(session id, n, target)]. The
    body and graph are synthetic's, so the graph puts c first once the weight passes (1 - x) / (2 - x)."""
    recs, events, i = [], [], 0
    for sid, n, target in plan:
        for _ in range(n):
            t = now - 7200 + i * 60
            body = [["a", 1.0], ["c", x], ["b", 0.3], ["d", 0.0]]
            graph = [["c", 1.0], ["a", 0.0]]
            recs.append({"t": t, "q": "q%d" % i, "lam": 0.2, "body": body, "graph": graph, "shown": ["a", "c", "b", "d"],
                         "session": sid, "entry": "cli"})
            events.append([t + 30, sid[:8], "mem", target])
            if later:
                events.append([t + 45, sid[:8], "mem", later])
            i += 1
    events.append([now - 5, plan[0][0][:8], "dir", "x"])
    return recs, events


def main():
    t0 = time.time()
    gl = load()
    tmp = tempfile.mkdtemp(prefix="gltest_")
    try:
        proj = os.path.join(tmp, "projects", (os.environ.get("BRAIN_SLUG") or os.path.expanduser("~").replace(":", "-").replace(chr(92), "-").replace("/", "-")))
        os.makedirs(proj)
        transcript(proj, "11111111-0000-4000-8000-000000000001", "cli", "work on the brain")
        transcript(proj, "22222222-0000-4000-8000-000000000002", "sdk-cli", "[headless-worker] QUESTION rec_01_02\n\nWhere did ...")
        transcript(proj, "33333333-0000-4000-8000-000000000003", "sdk-cli", "You are the nightly dream.")
        gl.PROJECTS = os.path.join(tmp, "projects")
        # since 29 Sep the learner asks session_kinds, the resolver the store and the fast layer share (account03/B01
        # auto73), so the fixture's transcripts are that module's folder; the real sample below points it back
        sk = gl.session_kinds
        live_sk = (sk.PROJECTS, sk.CODEX, sk.CACHE)
        sk.PROJECTS, sk.CODEX = os.path.join(tmp, "projects"), os.path.join(tmp, "codex")
        sk.CACHE = os.path.join(tmp, "session_kinds.json")
        os.makedirs(sk.CODEX, exist_ok=True)
        sk.reset()
        idx = gl.transcript_index()
        cache = {}
        got = [gl.session_kind(s, idx, cache) for s in ("11111111", "22222222", "33333333", "44444444")]
        check("session_kind reads his / test / script / unknown", got == ["his", "test", "script", "unknown"], str(got))

        now = time.time()
        # each session one vote, ten sessions needed (30 Sep, the 1000-page report's D03-T02); until then this control
        # moved the weight on forty searches from the one session below
        recs, ev = synthetic("11111111-0000-4000-8000-000000000001", 40, now)
        status, lam = run(gl, tmp, recs, ev, now)
        check("one interactive session's forty searches leave the weight", lam == 0.2 and status["recall_sessions"] == 1,
              "%s | %s" % (lam, status["recall"][:140]))
        ten = ["%08x-0000-4000-8000-000000000000" % (0xa0000000 + k) for k in range(10)]
        recs, ev = synthetic_sessions([(sid, 4, "c") for sid in ten], now)
        status, lam = run(gl, tmp, recs, ev, now)
        check("forty searches from ten interactive sessions move the weight toward the graph", lam == 0.25,
              "%s | %s" % (lam, status["recall"][:140]))
        recs, ev = synthetic_sessions([(sid, 4, "c") for sid in ten[:9]], now)
        status, lam = run(gl, tmp, recs, ev, now)
        check("thirty-six searches from nine sessions leave the weight", lam == 0.2 and status["recall_sessions"] == 9,
              "%s | %s" % (lam, status["recall"][:140]))
        recs, ev = synthetic_sessions([(ten[0], 31, "c")] + [(sid, 1, "a") for sid in ten[1:]], now)
        status, lam = run(gl, tmp, recs, ev, now)
        check("a busy session cannot outvote nine others (the mean over searches would have moved it up)",
              lam == 0.2 and status["recall_largest_session_share"] > 0.7, "%s | %s" % (lam, status["recall"][:140]))
        recs, ev = synthetic("22222222-0000-4000-8000-000000000002", 40, now)
        status, lam = run(gl, tmp, recs, ev, now)
        check("a test worker's searches are counted and not learned from",
              lam == 0.2 and status["recall_judged_by_kind"].get("test") == 40, "%s %s" % (lam, status["recall_judged_by_kind"]))
        recs, ev = synthetic_sessions([(sid, 4, "a") for sid in ten], now, x=0.857)
        status, lam = run(gl, tmp, recs, ev, now)
        check("interactive sessions opening what word search ranked first move the weight down", lam == 0.15,
              "%s | %s" % (lam, status["recall"][:120]))
        recs, ev = synthetic_sessions([(sid, 4, "a") for sid in ten], now, x=0.857, later="c")
        status, lam = run(gl, tmp, recs, ev, now)
        check("the target is the first memory opened, not the best-ranked of several (that rule ties and stays)",
              lam == 0.15, str(lam))
        r1 = {"t": now - 3000, "q": "first", "lam": 0.2, "body": [["a", 1.0], ["b", 0.5]], "graph": [], "shown": ["a", "b"],
              "session": "11111111-0000-4000-8000-000000000001", "entry": "cli"}
        r2 = dict(r1, t=now - 2820, q="second")
        ev = [[now - 2700, "11111111", "mem", "b"], [now - 5, "11111111", "dir", "x"]]
        status, lam = run(gl, tmp, [r1, r2], ev, now, dry=True)
        check("a search's window ends at the same session's next search (one of two searches judged)",
              status["recall_judged_by_kind"].get("his") == 1, str(status["recall_judged_by_kind"]))
        recs, ev = synthetic("11111111-0000-4000-8000-000000000001", 20, now)
        status, lam = run(gl, tmp, recs, ev, now)
        check("fewer than 30 searches by agents at work leave the weight", lam == 0.2 and "30 needed" in status["recall"],
              status["recall"][:100])

        # the membrane: a served memory line taken up by an interactive session teaches its edge; the same line in a
        # test worker's session teaches nothing and is reported apart
        fires = [{"t": now - 4000, "sid": "11111111", "ev": "post", "ep": "cli", "mem": [["m1", 9.0]],
                  "served": [["mem", "m1", ["kw:alpha"]]]},
                 {"t": now - 3990, "sid": "22222222", "ev": "post", "ep": "sdk-cli", "mem": [["m2", 9.0]],
                  "served": [["mem", "m2", ["kw:beta"]]]}]
        ev = [[now - 3900, "11111111", "mem", "m1"], [now - 3890, "22222222", "mem", "m2"],
              [now - 5, "11111111", "dir", "x"]]
        status, lam = run(gl, tmp, [], ev, now, fires=fires)
        import sqlite3
        db = sqlite3.connect(os.path.join(tmp, "live", "learned.sqlite"))
        rows = db.execute("SELECT src, dst, pos, neg FROM learned ORDER BY src").fetchall()
        db.close()
        tw = status["precision"].get("test_workers", {})
        check("the membrane learns from an interactive session's uptake, not a test worker's",
              rows == [("kw:alpha", "mem:m1", 1.0, 0.0)] and tw.get("evaluations") == 1 and tw.get("mem_taken") == 1
              and status["precision"]["mem"]["judged"] == 1, "%s | tests %s" % (rows, tw))

        extract = os.path.join(os.environ.get("TEMP", tmp), "claude", (os.environ.get("BRAIN_SLUG") or os.path.expanduser("~").replace(":", "-").replace(chr(92), "-").replace("/", "-")),
                               "825b1305-b798-4b86-9592-6dee5d977809", "scratchpad", "ev", "mem_events.jsonl")
        if os.path.exists(extract):
            evs = [json.loads(l) for l in open(extract, encoding="utf-8")]
            last = max(e["ts"] for e in evs)
            ev = [[e["ts"], e["session"], e["kind"], e["id"]] for e in evs if e["ts"] > last - 6 * 3600]
            live_recs = [json.loads(l) for l in open(os.path.join(os.path.expanduser("~"), ".claude", "brain_graph", "live",
                                                                  "recall_log.jsonl"), encoding="utf-8") if l.strip()]
            live_recs = [r for r in live_recs if last - 6 * 3600 < r["t"] < last - 700]
            gl.PROJECTS = os.path.join(os.path.expanduser("~"), ".claude", "projects")
            sk.PROJECTS, sk.CODEX, sk.CACHE = live_sk      # read only: the run is dry, and a dry run saves nothing
            sk.reset()
            status, lam = run(gl, tmp, live_recs, ev, last + 120, dry=True)
            k = status["recall_judged_by_kind"]
            check("real sample: test workers dominate, the weight stays", lam == 0.2 and k.get("test", 0) > k.get("his", 0),
                  "%d searches, judged by kind %s" % (len(live_recs), k))
        else:
            print("SKIP real sample (no extract at %s)" % extract)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("RESULT %d/%d (%.0f s)" % (sum(results), len(results), time.time() - t0))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
