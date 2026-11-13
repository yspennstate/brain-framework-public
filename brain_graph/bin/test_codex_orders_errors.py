"""test_codex_orders_errors.py - the Codex orders hook logs every failure it swallows (an audit
found that main() caught every exception and returned success with no trace, so a broken hook
read as "no relevant order"), and it keeps a session's receiver kind only once the rollout says it. Log, state, error file and transcripts in a temp dir.

    python test_codex_orders_errors.py [PATH_TO_codex_orders_hook.py] [--old OLD_HOOK.py]

Controls:
 1. the membrane raises inside run(): main() returns 0, writes nothing to stdout, logs one "run" line naming the error
 2. a corrupt state file: one "state_reset" line, and the state file is rewritten as valid JSON
 3. a normal prompt: no error line
 4. past 2 MB the error file is set aside whole under a dated name and a fresh one begins: three failures after the
    cap are all in the fresh file, and the old file is kept byte for byte
 5. no transcript yet: the first evaluation stores "not known" (None), not False; once the rollout exists with a cli
    session_meta the next evaluation says True
 6. a transcript without a session_meta in its first lines: not known; after the session_meta is written, True
 7. session_meta source "exec": False, and it stays False when the file later changes (a session_meta answer is kept)
 8. source "cli" with originator codex_exec: False
 9. a state file from before this change holding a bare "interactive": false, with a cli rollout: asked again, True
10. serving on inside the test (policy stubbed, one order that clears the bar): a session first evaluated before its
    rollout existed is served exactly once over three prompts
Reading the rollout the way Codex does:
11. eight event rows, then a cli session_meta on line 9: True at the first evaluation, found on line 9
12. every kind Codex allows before the header, a blank line, an unreadable line and an unknown kind, then the header: True
13. a response before the header: None, "response_before_meta", and kept without reading the file again after more
    lines (a cli header among them) are written; an inter-agent message before the header gives the same
14. a long file with no header, the scan capped at 64 KiB: the first evaluation stops at "scan_limit" having read at
    most the cap plus one line, later ones read on from there, every byte is read once, and a header written after
    that is found by reading only the new bytes, on its line
15. a file with no header left unchanged for five evaluations is opened once (ia_read: its size, then 0 four times)
16. Codex's interactive sources: vscode, custom atlas and custom chatgpt True; another custom source, mcp, a spawned
    thread and exec False; a header with no source True (Codex reads it as vscode)
17. a header still being written (no newline yet): None; once the line is finished: True, on line 4
18. an answer stored by the version before 29 Sep (session_meta, false, for a vscode rollout) is read again: True;
    and a changed transcript path is read again (a cli answer, then an exec rollout: False)
19. serving on: a header on line 9 is served exactly once over three prompts; an exec header on line 9, never
With --old, the hook before the 29 Sep change must fail 11, 15, 16 and 19; with --old-r70, the hook before the 28 Sep
change (codex_orders_hook.py.bak_b05r70_20260928) must fail 5 and 9 (the checks can fail).
"""
import importlib.util
import io
import json
import os
import sys
import tempfile

if __name__ == "__main__" and ("-h" in sys.argv[1:] or "--help" in sys.argv[1:]):
    print(__doc__)
    sys.exit(0)

H = os.path.expanduser("~")
OLD = sys.argv[sys.argv.index("--old") + 1] if "--old" in sys.argv else None
OLD_R70 = sys.argv[sys.argv.index("--old-r70") + 1] if "--old-r70" in sys.argv else None
_pos = [a for i, a in enumerate(sys.argv[1:], 1) if not a.startswith("--") and sys.argv[i - 1] not in ("--old", "--old-r70")]
HOOK = _pos[0] if _pos else os.path.join(H, ".claude", "dreaming", "codex_orders_hook.py")
tmp = tempfile.mkdtemp(prefix="codex_orders_err_test_")
spec = importlib.util.spec_from_file_location("coh", HOOK)
Hk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(Hk)
Hk.LIVE = tmp
Hk.STATE_DIR, Hk.LOG, Hk.ERRLOG = os.path.join(tmp, "state"), os.path.join(tmp, "log.jsonl"), os.path.join(tmp, "err.jsonl")
fails = []


def errors():
    if not os.path.exists(Hk.ERRLOG):
        return []
    return [json.loads(x) for x in open(Hk.ERRLOG, encoding="utf-8") if x.strip()]


def call_main(payload):
    """Run main() with payload on stdin; return (exit code, stdout text)."""
    old_in, old_out = sys.stdin, sys.stdout
    sys.stdin = io.TextIOWrapper(io.BytesIO(json.dumps(payload).encode("utf-8")), encoding="utf-8")
    sys.stdout = io.StringIO()
    try:
        rc = Hk.main()
        out = sys.stdout.getvalue()
    finally:
        sys.stdin, sys.stdout = old_in, old_out
    return rc, out


# 1. the membrane raises
sys.path.insert(0, Hk.BIN)
import live_membrane as LM
if not os.path.exists(LM.SIDECAR):
    print("SKIP - no graph sidecar yet (a fresh install builds it on the first graph build)")
    sys.exit(0)
real_policy = LM.policy


def boom():
    raise RuntimeError("policy unreadable (test)")


LM.policy = boom
rc, out = call_main({"hook_event_name": "UserPromptSubmit", "session_id": "err-test-1", "prompt": "render the film faster"})
LM.policy = real_policy
e = errors()
if not (rc == 0 and out == "" and len(e) == 1 and e[0]["kind"] == "run" and "policy unreadable" in e[0]["err"]
        and e[0]["sid"] == "err-test-1" and e[0]["ev"] == "UserPromptSubmit" and e[0]["tb"]):
    fails.append("1 run error not logged as expected: rc=%r out=%r errors=%r" % (rc, out, e))

# 2. a corrupt state file
os.makedirs(Hk.STATE_DIR, exist_ok=True)
sp = Hk._state_path("err-test-2")
with open(sp, "w", encoding="utf-8") as fh:
    fh.write("{not json")
rc, out = call_main({"hook_event_name": "UserPromptSubmit", "session_id": "err-test-2", "prompt": "push the memories"})
e = errors()
resets = [x for x in e if x["kind"] == "state_reset" and x["sid"] == "err-test-2"]
try:
    json.load(open(sp, encoding="utf-8"))
    rewritten = True
except Exception:
    rewritten = False
if not (rc == 0 and len(resets) == 1 and rewritten):
    fails.append("2 state reset: rc=%r resets=%d rewritten=%r" % (rc, len(resets), rewritten))

# 3. a normal prompt adds no error line
n0 = len(errors())
rc, out = call_main({"hook_event_name": "UserPromptSubmit", "session_id": "err-test-3", "prompt": "keep working on the brain"})
if not (rc == 0 and len(errors()) == n0):
    fails.append("3 normal prompt logged an error: %r" % errors()[n0:])

# 4. past the cap: set aside whole, a fresh file, nothing dropped
with open(Hk.ERRLOG, "a", encoding="utf-8") as fh:
    fh.write("x" * (2 * 1024 * 1024 + 10) + "\n")
size0 = os.path.getsize(Hk.ERRLOG)
LM.policy = boom
for i in range(3):
    call_main({"hook_event_name": "UserPromptSubmit", "session_id": "err-test-4-%d" % i, "prompt": "x"})
LM.policy = real_policy
d, b = os.path.split(Hk.ERRLOG)
stem = os.path.splitext(b)[0] + "."
arch = [f for f in os.listdir(d) if f.startswith(stem) and f.endswith(".jsonl") and f != b]
fresh = []
for ln in open(Hk.ERRLOG, encoding="utf-8"):
    try:
        fresh.append(json.loads(ln))
    except ValueError:
        pass
n4 = len([e for e in fresh if str(e.get("sid", "")).startswith("err-test-4-")])
kept = sum(os.path.getsize(os.path.join(d, f)) for f in arch)
if not (len(arch) == 1 and kept == size0 and n4 == 3 and os.path.getsize(Hk.ERRLOG) < 1024 * 1024):
    fails.append("4 past the cap: %d set-aside file(s) holding %d of %d bytes, %d of 3 new failures in the fresh file" % (
        len(arch), kept, size0, n4))


# 5-10: the receiver kind


def load_hook(path, root):
    import importlib.machinery
    name = "coh_%d" % abs(hash((path, root)))
    loader = importlib.machinery.SourceFileLoader(name, path)   # a backup's suffix is not .py
    sp_ = importlib.util.spec_from_file_location(name, path, loader=loader)
    m = importlib.util.module_from_spec(sp_)
    loader.exec_module(m)
    m.LIVE = root
    m.STATE_DIR, m.LOG, m.ERRLOG = os.path.join(root, "state"), os.path.join(root, "log.jsonl"), os.path.join(root, "err.jsonl")
    return m


class _Live:
    def __init__(self, *a, **k):
        self.db = None


ORDER = {"slug": "owner_test_order_for_the_receiver_kind", "score": 99.0}
real = {k: getattr(LM, k) for k in ("policy", "Live", "order_candidates", "order_line", "held_back")}


def stub(serve):
    LM.policy = lambda: {"serve_orders_codex": serve, "orders_min": 30.0, "orders_stable": 2}
    LM.Live = _Live
    LM.order_candidates = (lambda q, seen, db, **k: [dict(ORDER)]) if serve else (lambda q, seen, db, **k: [])
    LM.order_line = lambda oc: "an order line"
    LM.held_back = lambda *a, **k: False


def unstub():
    for k, v in real.items():
        setattr(LM, k, v)


def rollout(path, meta=None, lines_before=0):
    with open(path, "w", encoding="utf-8") as fh:
        for i in range(lines_before):
            fh.write(json.dumps({"type": "event_msg", "payload": {"n": i}}) + "\n")
        if meta is not None:
            fh.write(json.dumps({"type": "session_meta", "payload": meta}) + "\n")


CLI = {"source": "cli", "originator": "codex_tui"}


def prompt(m, sid, tpath, now):
    return m.run({"hook_event_name": "UserPromptSubmit", "session_id": sid, "prompt": "check the receiver kind now",
                  "transcript_path": tpath}, now=now)


def state(m, sid):
    return json.load(open(m._state_path(sid), encoding="utf-8"))


def receiver_controls(m, tag):
    """{control: (ok, text)} for 5-10 on hook module m."""
    root = tempfile.mkdtemp(prefix="codex_ia_%s_" % tag)
    m.LIVE = root
    m.STATE_DIR, m.LOG, m.ERRLOG = os.path.join(root, "state"), os.path.join(root, "log.jsonl"), os.path.join(root, "err.jsonl")
    r = {}
    t0 = 1_800_000_000.0
    try:
        stub(False)
        tp = os.path.join(root, "rollout5.jsonl")
        prompt(m, "ia-5", tp, t0)
        first = state(m, "ia-5").get("interactive")
        rollout(tp, CLI)
        prompt(m, "ia-5", tp, t0 + 10)
        second = state(m, "ia-5").get("interactive")
        r[5] = (first is None and second is True,
                "5 no transcript, then a cli rollout: first %r, second %r" % (first, second))
        tp = os.path.join(root, "rollout6.jsonl")
        rollout(tp, None, lines_before=3)
        prompt(m, "ia-6", tp, t0)
        first = state(m, "ia-6").get("interactive")
        rollout(tp, CLI)
        prompt(m, "ia-6", tp, t0 + 10)
        second = state(m, "ia-6").get("interactive")
        r[6] = (first is None and second is True,
                "6 no session_meta yet, then written: first %r, second %r" % (first, second))
        tp = os.path.join(root, "rollout7.jsonl")
        rollout(tp, {"source": "exec", "originator": "codex_exec"})
        prompt(m, "ia-7", tp, t0)
        first = state(m, "ia-7").get("interactive")
        rollout(tp, CLI)
        prompt(m, "ia-7", tp, t0 + 10)
        second = state(m, "ia-7").get("interactive")
        r[7] = (first is False and second is False,
                "7 exec session_meta, then the file changes: first %r, second %r" % (first, second))
        tp = os.path.join(root, "rollout8.jsonl")
        rollout(tp, {"source": "cli", "originator": "codex_exec"})
        prompt(m, "ia-8", tp, t0)
        v = state(m, "ia-8").get("interactive")
        r[8] = (v is False, "8 cli source, codex_exec originator: %r" % (v,))
        tp = os.path.join(root, "rollout9.jsonl")
        rollout(tp, CLI)
        os.makedirs(m.STATE_DIR, exist_ok=True)
        with open(m._state_path("ia-9"), "w", encoding="utf-8") as fh:
            json.dump({"ask": [], "recent": [], "calls": 0, "calls_at_eval": 0, "last_eval": 0.0, "run": [None, 0],
                       "served": {}, "interactive": False}, fh)
        prompt(m, "ia-9", tp, t0)
        v = state(m, "ia-9").get("interactive")
        r[9] = (v is True, "9 a state from before 28 Sep with a bare false, cli rollout: %r" % (v,))
        stub(True)
        tp = os.path.join(root, "rollout10.jsonl")
        outs = [prompt(m, "ia-10", tp, t0)]
        rollout(tp, CLI)
        outs += [prompt(m, "ia-10", tp, t0 + 10), prompt(m, "ia-10", tp, t0 + 20)]
        served = [i for i, o in enumerate(outs) if o]
        r[10] = (served == [1], "10 serving on, first prompt before the rollout existed: served on prompts %r of 0..2" % (served,))
    finally:
        unstub()
    return r


BEFORE = ["inter_agent_communication_metadata", "compacted", "turn_context", "token_usage_record", "world_state",
          "realtime_item", "retained_context", "security_risk_score", "event_msg"]


def ev(i, pad=0):
    return json.dumps({"type": "event_msg", "payload": {"n": i, "pad": "x" * pad}}) + "\n"


def header(**payload):
    return json.dumps({"type": "session_meta", "payload": dict({"id": "x", "originator": "codex-tui"}, **payload)}) + "\n"


def write(path, text, mode="w"):
    with open(path, mode, encoding="utf-8", newline="") as fh:
        fh.write(text)


def opens(m, target):
    """Count the hook's opens of the transcript (its global `open` shadows the builtin inside the module)."""
    import builtins
    c = {"n": 0}

    def o(file, *a, **k):
        if isinstance(file, str) and os.path.abspath(file) == os.path.abspath(target):
            c["n"] += 1
        return builtins.open(file, *a, **k)
    m.open = o
    return c


def logged(m, sid):
    if not os.path.exists(m.LOG):
        return []
    return [r for r in (json.loads(x) for x in open(m.LOG, encoding="utf-8") if x.strip()) if r.get("sid") == sid[:40]]


def scan_controls(m, tag):
    """{control: (ok, text)} for 11-19 on hook module m."""
    root = tempfile.mkdtemp(prefix="codex_scan_%s_" % tag)
    m.LIVE = root
    m.STATE_DIR, m.LOG, m.ERRLOG = os.path.join(root, "state"), os.path.join(root, "log.jsonl"), os.path.join(root, "err.jsonl")
    r, t0 = {}, 1_800_000_000.0
    try:
        stub(False)
        tp = os.path.join(root, "r11.jsonl")
        write(tp, "".join(ev(i) for i in range(8)) + header(source="cli"))
        prompt(m, "sc-11", tp, t0)
        s = state(m, "sc-11")
        r[11] = (s.get("interactive") is True and s.get("ia_scan", {}).get("meta_line") == 9,
                 "11 header on line 9: %r, found on line %r" % (s.get("interactive"), s.get("ia_scan", {}).get("meta_line")))
        tp = os.path.join(root, "r12.jsonl")
        rows = [json.dumps({"type": k, "payload": {}}) + "\n" for k in BEFORE]
        write(tp, "".join(rows) + "\n" + "{not json\n" + json.dumps({"type": "some_future_kind"}) + "\n" + header(source="cli"))
        prompt(m, "sc-12", tp, t0)
        s = state(m, "sc-12")
        r[12] = (s.get("interactive") is True, "12 every allowed kind before the header: %r (%s)" % (
            s.get("interactive"), s.get("ia_status")))
        tp = os.path.join(root, "r13.jsonl")
        write(tp, ev(0) + json.dumps({"type": "response_item", "payload": {"role": "user"}}) + "\n" + header(source="cli"))
        c = opens(m, tp)
        prompt(m, "sc-13", tp, t0)
        a = state(m, "sc-13")
        write(tp, ev(5) + header(source="cli"), "a")
        prompt(m, "sc-13", tp, t0 + 10)
        b = state(m, "sc-13")
        reads = [x.get("ia_read") for x in logged(m, "sc-13")]
        tp2 = os.path.join(root, "r13b.jsonl")
        write(tp2, json.dumps({"type": "inter_agent_communication", "payload": {}}) + "\n" + header(source="cli"))
        prompt(m, "sc-13b", tp2, t0)
        d = state(m, "sc-13b")
        r[13] = (a.get("interactive") is None and a.get("ia_status") == "response_before_meta" and b.get("ia_status") ==
                 "response_before_meta" and b.get("interactive") is None and c["n"] == 1 and reads[-1:] == [0] and
                 d.get("ia_status") == "response_before_meta",
                 "13 a response first: %r/%s, after more lines %r/%s, opened %d time(s), bytes read %r; an inter-agent "
                 "message first: %s" % (a.get("interactive"), a.get("ia_status"), b.get("interactive"), b.get("ia_status"),
                                         c["n"], reads, d.get("ia_status")))
        m.__dict__.pop("open", None)
        tp = os.path.join(root, "r14.jsonl")
        body = "".join(ev(i, pad=90) for i in range(3000))
        write(tp, body)
        size = os.path.getsize(tp)
        saved = getattr(m, "SCAN_BYTES", None)
        m.SCAN_BYTES = 64 * 1024
        try:
            statuses, reads = [], []
            for i in range(12):
                prompt(m, "sc-14", tp, t0 + i)
                s = state(m, "sc-14")
                statuses.append(s.get("ia_status"))
                reads.append(logged(m, "sc-14")[-1].get("ia_read"))
                if s.get("ia_status") == "no_session_meta":
                    break
            line = max(len(x) for x in body.splitlines(True))
            first_ok = statuses[0] == "scan_limit" and reads[0] <= 64 * 1024 + line
            whole = sum(x or 0 for x in reads) == size and statuses[-1] == "no_session_meta"
            hdr = header(source="cli")
            write(tp, hdr, "a")
            prompt(m, "sc-14", tp, t0 + 100)
            s = state(m, "sc-14")
            last = logged(m, "sc-14")[-1].get("ia_read")
        finally:
            if saved is None:
                m.__dict__.pop("SCAN_BYTES", None)
            else:
                m.SCAN_BYTES = saved
        r[14] = (first_ok and whole and s.get("interactive") is True and last == len(hdr) and
                 s.get("ia_scan", {}).get("meta_line") == 3001,
                 "14 a long file, the scan capped at 64 KiB: statuses %s, bytes read %r of %d, then the header found "
                 "reading %r of %d bytes, on line %r" % ("/".join(map(str, statuses)), reads, size, last, len(hdr),
                                                        s.get("ia_scan", {}).get("meta_line")))
        tp = os.path.join(root, "r15.jsonl")
        write(tp, ev(0) + ev(1) + ev(2))
        c = opens(m, tp)
        for i in range(5):
            prompt(m, "sc-15", tp, t0 + i)
        m.__dict__.pop("open", None)
        reads = [x.get("ia_read") for x in logged(m, "sc-15")]
        r[15] = (c["n"] == 1 and reads == [os.path.getsize(tp), 0, 0, 0, 0],
                 "15 an unchanged file with no header, five evaluations: opened %d time(s), bytes read %r" % (c["n"], reads))
        cases = [("vscode", "vscode", True), ("custom atlas", {"custom": "atlas"}, True),
                 ("custom chatgpt", {"custom": "chatgpt"}, True), ("custom other", {"custom": "other"}, False),
                 ("mcp", "mcp", False), ("subagent", {"subagent": {"thread_spawn": {"parent_thread_id": "p"}}}, False),
                 ("exec", "exec", False), ("no source", None, True)]
        got = []
        for i, (name, src, want) in enumerate(cases):
            tp = os.path.join(root, "r16_%d.jsonl" % i)
            write(tp, header() if src is None else header(source=src))
            prompt(m, "sc-16-%d" % i, tp, t0)
            v = state(m, "sc-16-%d" % i).get("interactive")
            got.append((name, v, v is want))
        r[16] = (all(g[2] for g in got), "16 sources: %s" % ", ".join("%s %r" % (n, v) for n, v, _ in got))
        tp = os.path.join(root, "r17.jsonl")
        hdr = header(source="cli")
        write(tp, ev(0) + ev(1) + ev(2) + hdr[:40])
        prompt(m, "sc-17", tp, t0)
        a = state(m, "sc-17")
        write(tp, hdr[40:], "a")
        prompt(m, "sc-17", tp, t0 + 10)
        b = state(m, "sc-17")
        r[17] = (a.get("interactive") is None and b.get("interactive") is True and
                 b.get("ia_scan", {}).get("meta_line") == 4,
                 "17 a header half written: %r; finished: %r on line %r" % (
                     a.get("interactive"), b.get("interactive"), b.get("ia_scan", {}).get("meta_line")))
        tp = os.path.join(root, "r18.jsonl")
        write(tp, header(source="vscode"))
        os.makedirs(m.STATE_DIR, exist_ok=True)
        with open(m._state_path("sc-18"), "w", encoding="utf-8") as fh:
            json.dump({"ask": [], "recent": [], "calls": 0, "calls_at_eval": 0, "last_eval": 0.0, "run": [None, 0],
                       "served": {}, "interactive": False, "ia_status": "session_meta"}, fh)
        prompt(m, "sc-18", tp, t0)
        a = state(m, "sc-18").get("interactive")
        ta, tb = os.path.join(root, "r18a.jsonl"), os.path.join(root, "r18b.jsonl")
        write(ta, header(source="cli"))
        write(tb, header(source="exec", originator="codex_exec"))
        prompt(m, "sc-18b", ta, t0)
        b1 = state(m, "sc-18b").get("interactive")
        prompt(m, "sc-18b", tb, t0 + 10)
        b2 = state(m, "sc-18b").get("interactive")
        r[18] = (a is True and b1 is True and b2 is False,
                 "18 a version-1 answer for a vscode rollout: %r; a cli answer, then an exec rollout: %r, %r" % (a, b1, b2))
        stub(True)
        served = {}
        for name, src, orig in (("cli", "cli", "codex-tui"), ("exec", "exec", "codex_exec")):
            tp = os.path.join(root, "r19_%s.jsonl" % name)
            write(tp, "".join(ev(i) for i in range(8)) + header(source=src, originator=orig))
            outs = [prompt(m, "sc-19-%s" % name, tp, t0 + 10 * i) for i in range(3)]
            served[name] = [i for i, o in enumerate(outs) if o]
        r[19] = (served["cli"] == [1] and served["exec"] == [],
                 "19 serving on, header on line 9: cli served on prompts %r, exec on %r" % (served["cli"], served["exec"]))
    finally:
        m.__dict__.pop("open", None)
        unstub()
    return r


results = dict(receiver_controls(Hk, "new"))
results.update(scan_controls(Hk, "new"))
for k, (ok, text) in sorted(results.items()):
    if not ok:
        fails.append(text)
    print(("PASS  " if ok else "FAIL  ") + text)
n_controls = 4 + len(results)
for old_path, which, tag in ((OLD, (11, 15, 16, 19), "29 Sep"), (OLD_R70, (5, 9), "28 Sep")):
    if not old_path:
        continue
    om = load_hook(old_path, tempfile.mkdtemp(prefix="codex_ia_old_"))
    old = dict(receiver_controls(om, "old"))
    old.update(scan_controls(om, "old"))
    for k in which:
        ok = not old[k][0]
        n_controls += 1
        if not ok:
            fails.append("N%d the hook before the %s change passes %s" % (k, tag, old[k][1]))
        print(("PASS  " if ok else "FAIL  ") + "N%d the hook before the %s change fails it (the check can fail): %s" % (
            k, tag, old[k][1]))

print("FAIL" if fails else "PASS", "- %d controls;" % n_controls, "; ".join(fails) if fails else "errors logged, state "
      "reset logged, a normal prompt silent, past the cap nothing dropped, the receiver kind kept only from the rollout")
sys.exit(1 if fails else 0)
