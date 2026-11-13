"""test_codex_capture_zst.py - the Codex capture reads finished .jsonl.zst rollouts exactly once.

    python test_codex_capture_zst.py [DIR_WITH_THE_CAPTURE] [OLD_CAPTURE_DIR]

Codex compresses a rollout to .jsonl.zst once the session ends. Three synthetic rollouts (commands, agent messages and
a hook message, written into a temp ROOT/yyyy/mm/dd; nothing outside the temp folder is read or written):
 R  plain capture of the three: the reference
 Z  the same three as .zst only, fresh state: the same events
 C  the third captured half-way as plain, then replaced by its full .zst: the events equal R (continued at the
    decompressed byte, nothing twice); a further run adds nothing
 B  a plain file beside its own .zst: read once, as the plain file
 N  (with OLD_CAPTURE_DIR) a capture that globs *.jsonl only, on Z's folder: no events (the check can fail)
Needs the zstandard module; without it the test says so and exits 0.
"""
if __name__ == "__main__" and __import__("sys").argv[1:2] in (["-h"], ["--help"]):
    print((__doc__ or "").strip() or "run with no arguments")
    raise SystemExit(0)

import collections
import json
import os
import shutil
import subprocess
import sys
import tempfile

try:
    import zstandard
except ImportError:
    print("SKIP - zstandard is not installed (pip install zstandard)")
    sys.exit(0)

BIN = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.abspath(__file__))
OLD = sys.argv[2] if len(sys.argv) > 2 else None
NO_WINDOW = 0x08000000 if os.name == "nt" else 0
CODE = r"""
import sys
sys.path.insert(0, sys.argv[1])
import extract_codex_events as cx
cx.ROOT = sys.argv[2]
sys.argv = ["extract_codex_events.py", "--days", "4000", "--out", sys.argv[3], "--state", sys.argv[4]]
cx.main()
"""
fails = []


def check(ok, what):
    print(("PASS  " if ok else "FAIL  ") + what, flush=True)
    if not ok:
        fails.append(what)


def run(impl, root, d):
    os.makedirs(d, exist_ok=True)
    r = subprocess.run([sys.executable, "-c", CODE, impl, root, os.path.join(d, "events.jsonl"),
                        os.path.join(d, "state.json")], capture_output=True, text=True, creationflags=NO_WINDOW,
                       env=dict(os.environ, CODEX_CAPTURE_BUDGET_S="0", CODEX_EXTRA_ROOTS=""))
    return r.returncode, (r.stdout + r.stderr)[-300:]


def lines(p):
    c = collections.Counter()
    if os.path.exists(p):
        with open(p, "rb") as fh:
            for ln in fh:
                ln = ln.rstrip(b"\r\n")
                if ln:
                    c[ln] += 1
    return c


def rollout(n, k=40):
    """one synthetic rollout: a session header, then k turns of a command and an agent message, a hook every tenth"""
    sid = "0199aaaa-bbbb-7ccc-8ddd-%012d" % (n + 1)
    recs = [{"timestamp": "2026-09-01T10:00:00.000Z", "type": "session_meta",
             "payload": {"id": sid, "cwd": "/work/proj%d" % n, "originator": "codex_exec", "source": "exec"}}]
    for i in range(k):
        ts = "2026-09-01T10:%02d:%02d.000Z" % (i // 60 + n * 3, i % 60)
        recs.append({"timestamp": ts, "type": "event_msg", "payload": {"type": "item_completed", "item": {
            "type": "CommandExecution", "command": "python ~/proj%d/tool_%d.py --step %d" % (n, i % 7, i),
            "cwd": "/work/proj%d" % n, "exit_code": 0 if i % 9 else 2, "stdout": "", "stderr": "step failed"}}})
        recs.append({"timestamp": ts, "type": "event_msg", "payload": {"type": "item_completed", "item": {
            "type": "AgentMessage", "content": [{"type": "text",
                                                  "text": "checked the boundary lemma for case %d and the cover bound" % i}]}}})
        if i % 10 == 0:
            recs.append({"timestamp": ts, "type": "response_item", "payload": {
                "type": "message", "role": "developer", "content": [{"type": "input_text",
                                                                     "text": "[prompts] the recent asks, part %d" % i}]}})
    return ("\n".join(json.dumps(r) for r in recs) + "\n").encode()


def zst(data, dst):
    with open(dst, "wb") as b:
        b.write(zstandard.ZstdCompressor(level=3).compress(data))


def main():
    names = ["rollout-2026-09-01T10-0%d-00-synthetic%d.jsonl" % (n, n) for n in range(3)]
    data = [rollout(n) for n in range(3)]
    tmp = tempfile.mkdtemp(prefix="codexzst_")
    try:
        def root(name):
            d = os.path.join(tmp, name, "root", "2026", "09", "01")
            os.makedirs(d)
            return os.path.join(tmp, name, "root"), d

        def plain(dst, b):
            with open(dst, "wb") as fh:
                fh.write(b)

        rr, rd = root("R")
        for nm, b in zip(names, data):
            plain(os.path.join(rd, nm), b)
        rc, msg = run(BIN, rr, os.path.join(tmp, "R"))
        R = lines(os.path.join(tmp, "R", "events.jsonl"))
        check(rc == 0 and sum(R.values()) > 100, "R plain capture: %d events (rc %d %s)" % (
            sum(R.values()), rc, "" if rc == 0 else msg))

        zr, zd = root("Z")
        for nm, b in zip(names, data):
            zst(b, os.path.join(zd, nm + ".zst"))
        rc, msg = run(BIN, zr, os.path.join(tmp, "Z"))
        Z = lines(os.path.join(tmp, "Z", "events.jsonl"))
        check(rc == 0 and Z == R, "Z .zst only: %d events, equal to R: %s %s" % (sum(Z.values()), Z == R,
                                                                                 "" if rc == 0 else msg))

        cr, cd = root("C")
        for nm, b in zip(names[:2], data[:2]):
            plain(os.path.join(cd, nm), b)
        half = data[2].rfind(b"\n", 0, len(data[2]) // 2) + 1
        third = os.path.join(cd, names[2])
        plain(third, data[2][:half])
        rc1, _ = run(BIN, cr, os.path.join(tmp, "C"))
        os.remove(third)
        zst(data[2], third + ".zst")
        rc2, msg = run(BIN, cr, os.path.join(tmp, "C"))
        C = lines(os.path.join(tmp, "C", "events.jsonl"))
        check(rc1 == 0 and rc2 == 0 and C == R, "C half plain then full .zst: %d events, equal to R: %s %s" % (
            sum(C.values()), C == R, "" if rc2 == 0 else msg))
        rc3, _ = run(BIN, cr, os.path.join(tmp, "C"))
        C2 = lines(os.path.join(tmp, "C", "events.jsonl"))
        check(rc3 == 0 and C2 == C, "C again: adds nothing (%d events)" % sum(C2.values()))

        br, bd = root("B")
        for nm, b in zip(names, data):
            plain(os.path.join(bd, nm), b)
            zst(b, os.path.join(bd, nm + ".zst"))
        rc, _ = run(BIN, br, os.path.join(tmp, "B"))
        B = lines(os.path.join(tmp, "B", "events.jsonl"))
        check(rc == 0 and B == R, "B plain beside its .zst: read once, equal to R: %s" % (B == R))

        if OLD:
            rc, _ = run(OLD, zr, os.path.join(tmp, "N"))
            N = lines(os.path.join(tmp, "N", "events.jsonl"))
            check(sum(N.values()) == 0, "N a *.jsonl-only capture on the .zst folder: %d events (the check can fail)"
                  % sum(N.values()))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("ALL PASS" if not fails else "FAILED: %d" % len(fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
