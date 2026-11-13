"""Controls for push_reminder.py's once-per-context routing text (it uses once_per_context.py).

 R1 the first reminder to a session carries the whole routing paragraph
 R2 the next reminder to the same context carries the lead line and a one-line note, not the paragraph again
 R3 a second session, and an event with no session id, get the whole paragraph
 R4 after a compaction of the session the paragraph goes whole again
 R5 the hook's own selftest still passes (silent 30 minutes, one fire per half hour, never on Stop)

    python test_push_reminder_once.py      (under a second; ALL PASS at the end; writes only to a temp folder)
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
import push_reminder as P

results = []


def check(ok, what):
    results.append(bool(ok))
    print(("PASS  " if ok else "FAIL  ") + what)


def ctx(out):
    return out.get("hookSpecificOutput", {}).get("additionalContext", "")


tmp = tempfile.mkdtemp(prefix="push_once_")
try:
    O.STATE = os.path.join(tmp, "opc.json")
    O.COMPACTION_INDEX = os.path.join(tmp, "INDEX.jsonl")
    open(O.COMPACTION_INDEX, "w", encoding="utf-8").close()
    sd = os.path.join(tmp, "state")
    full = P.ROUTING % P.ROUTING_DOC
    lead = full.split("\n")[0]
    t0 = time.time() - 4000
    p = {"hook_event_name": "UserPromptSubmit", "session_id": "sess-1"}
    P.decide(p, now=t0, state_dir=sd)
    a = ctx(P.decide(p, now=t0 + 31 * 60, state_dir=sd))
    check(a == full, "R1 the first reminder carries the whole paragraph (%d chars)" % len(full))
    b = ctx(P.decide(p, now=t0 + 62 * 60, state_dir=sd))
    check(b.startswith(lead) and "reached this context already" in b and len(b) < len(full) // 2,
          "R2 the next reminder is the lead line plus a note (%d chars against %d)" % (len(b), len(full)))
    q = {"hook_event_name": "UserPromptSubmit", "session_id": "sess-2"}
    P.decide(q, now=t0, state_dir=sd)
    c = ctx(P.decide(q, now=t0 + 31 * 60, state_dir=sd))
    check(c == full and P._once("", full) == full, "R3 another session and a missing session id get the whole text")
    with open(O.COMPACTION_INDEX, "a", encoding="utf-8") as f:
        f.write(json.dumps({"session_id": "sess-1",
                            "at": datetime.datetime.fromtimestamp(time.time() + 1).isoformat()}) + "\n")
    d = ctx(P.decide(p, now=t0 + 93 * 60, state_dir=sd))
    check(d == full, "R4 after a compaction the paragraph goes whole again")
    try:
        P.selftest()
        ok = True
    except AssertionError as e:
        ok = False
        print("   ", e)
    check(ok, "R5 push_reminder --selftest passes")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("ALL PASS" if all(results) else "SOME FAILED")
sys.exit(0 if all(results) else 1)
