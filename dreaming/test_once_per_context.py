"""Controls for once_per_context.py, which sends a hook's standing text whole once per context and then only what changed.

 O1 the first call for a session sends every line and says it went whole
 O2 the next call sends only lines not seen before; a line that differs only in its digits counts as seen
 O3 a line matched by `keep` always goes
 O4 a compaction of the session after the whole send makes the next call whole again
 O5 a send older than `resend` seconds goes whole again
 O6 no session id, or ONCE_PER_CONTEXT_OFF=1, sends everything whole
 O7 two hooks on one session, and two sessions on one hook, keep separate state

    python test_once_per_context.py      (under a second; ALL PASS at the end; writes only to a temp folder)
"""
if "-h" in __import__("sys").argv[1:] or "--help" in __import__("sys").argv[1:]:
    print((__doc__ or "").strip() or "usage: python %s" % __import__("os").path.basename(__file__))
    raise SystemExit(0)
import datetime
import json
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.pop("ONCE_PER_CONTEXT_OFF", None)
import once_per_context as O

results = []


def check(ok, what):
    results.append(bool(ok))
    print(("PASS  " if ok else "FAIL  ") + what)


def compaction(path, sess, at):
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"session_id": sess, "at": datetime.datetime.fromtimestamp(at).isoformat()}) + "\n")


tmp = tempfile.mkdtemp(prefix="opc_")
try:
    O.STATE = os.path.join(tmp, "state", "once_per_context.json")
    O.COMPACTION_INDEX = os.path.join(tmp, "INDEX.jsonl")
    open(O.COMPACTION_INDEX, "w").close()
    s = "sess-a"
    std = ["[hook] 12 processes live", "  rule one: judge, do not shed", "  rule two: report in one line"]
    out, whole, at = O.filter_lines(s, "h", std)
    check(whole and out == std, "O1 first call sends all %d lines whole" % len(std))
    nxt = ["[hook] 15 processes live", "  rule one: judge, do not shed", "  rule two: report in one line", "  a new line"]
    out, whole, at2 = O.filter_lines(s, "h", nxt)
    check(not whole and out == ["  a new line"] and at2 == at,
          "O2 only the unseen line goes; '15 processes' matches '12 processes' with digits masked")
    out, whole, _ = O.filter_lines(s, "h", nxt, keep=lambda l: l.startswith("[hook]"))
    check(not whole and out == ["[hook] 15 processes live"], "O3 a kept line goes even though it was seen")
    compaction(O.COMPACTION_INDEX, s, time.time() + 1)
    out, whole, _ = O.filter_lines(s, "h", nxt)
    check(whole and out == nxt, "O4 after a compaction the next call is whole again")
    out, whole, _ = O.filter_lines(s, "h", nxt, resend=-1)
    check(whole and out == nxt, "O5 a whole send older than resend goes whole again")
    out, whole, _ = O.filter_lines("", "h", nxt)
    check(whole and out == nxt, "O6 no session id sends everything whole")
    os.environ["ONCE_PER_CONTEXT_OFF"] = "1"
    out, whole, _ = O.filter_lines(s, "h", nxt)
    check(whole and out == nxt, "O6 ONCE_PER_CONTEXT_OFF=1 sends everything whole")
    os.environ.pop("ONCE_PER_CONTEXT_OFF", None)
    o1, w1, _ = O.filter_lines(s, "other_hook", std)
    o2, w2, _ = O.filter_lines("sess-b", "h", std)
    check(w1 and w2 and o1 == std and o2 == std, "O7 a second hook and a second session each start whole")
    check(O.last_compaction_at("sess-none") == 0 and O.last_compaction_at(s) > 0,
          "O4 last_compaction_at is 0 for a session with no compaction and set for one with")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("ALL PASS" if all(results) else "SOME FAILED")
sys.exit(0 if all(results) else 1)
