"""test_queued_prompts.py - his prompts typed while an agent works reach the capture and the verdict file. Such a prompt is written only as a queued_command attachment (and a queue-operation record), never
as a user record; until 27 Sep extract_touch_events.scan_file and backfill_history._verdicts_in read user records
alone, and missed 1,213 of his 5,269 prompts since 16 Aug and 59 of his 165 corrections.

    python test_queued_prompts.py        exit 0 all pass, 1 a failure

Controls on synthetic transcripts in a temp dir, plus one real transcript when it is present (his queued correction of
26 Sep 20:11 in session 5e550002). The pre-fix files fail the queued controls (checked at install).
"""
if "-h" in __import__("sys").argv[1:] or "--help" in __import__("sys").argv[1:]:
    print((__doc__ or "").strip() or "usage: python %s" % __import__("os").path.basename(__file__))
    raise SystemExit(0)
import collections
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import extract_touch_events as X  # noqa: E402
import backfill_history as BH  # noqa: E402

fails = 0


def check(label, ok):
    global fails
    fails += 0 if ok else 1
    print(("PASS " if ok else "FAIL ") + label)


def scan(path):
    ev, st = [], collections.Counter()
    X.scan_file(path, 0, lambda ts, s, k, i: ev.append((ts, s, k, i)), st)
    return ev, st


tmp = tempfile.mkdtemp(prefix="queued_ctl_")


def write(name, rows):
    p = os.path.join(tmp, name)
    with open(p, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, separators=(",", ":")) + "\n")
    return p


SID = "0123abcd-0000-4000-8000-000000000000"
base = {"sessionId": SID, "cwd": os.path.expanduser("~"), "userType": "external", "isSidechain": False}


def queued(ts, text, entry="cli"):
    return dict(base, type="attachment", timestamp=ts, entrypoint=entry,
                attachment={"type": "queued_command", "prompt": text})


def user(ts, text):
    return dict(base, type="user", timestamp=ts, entrypoint="cli", message={"role": "user", "content": text})


e, st = scan(write("q.jsonl", [queued("2026-09-27T09:00:00.000Z", "that is wrong, i said the other folder")]))
check("a queued prompt of his gives words and a correction", st["prompts"] == 1 and st["queued_prompts"] == 1
      and [x[3] for x in e if x[2] == "verdict"] == ["correction"] and any(x[2] == "kw" for x in e))
e, st = scan(write("dup.jsonl", [queued("2026-09-27T09:00:00.000Z", "that is wrong, i said the other folder"),
                                 user("2026-09-27T09:01:00.000Z", "that is wrong, i said the other folder")]))
check("queued and then written as a user record counts once", st["prompts"] == 1
      and sum(1 for x in e if x[2] == "verdict") == 1)
e, st = scan(write("far.jsonl", [queued("2026-09-27T09:00:00.000Z", "keep working"),
                                 user("2026-09-27T10:00:00.000Z", "keep working")]))
check("the same words an hour apart are two prompts", st["prompts"] == 2)
e, st = scan(write("tn.jsonl", [queued("2026-09-27T09:02:00.000Z",
                                       "<task-notification>\n<task-id>x</task-id> wrong</task-notification>")]))
check("a queued task notification is not his prompt", st["prompts"] == 0 and not e)
e, st = scan(write("hl.jsonl", [queued("2026-09-27T09:03:00.000Z", "this is broken, fix it", entry="sdk-cli")]))
check("a headless queued prompt gives words but no verdict, as a headless user record does",
      st["prompts"] == 1 and not any(x[2] == "verdict" for x in e))
got = []
BH._verdicts_in(write("bh.jsonl", [queued("2026-09-27T09:00:00.000Z", "that is wrong, i said the other folder"),
                                   user("2026-09-27T09:01:00.000Z", "that is wrong, i said the other folder")]),
                lambda ts, s, v: got.append(v), collections.Counter())
check("the verdict backfill reads a queued correction and counts it once", got == ["correction"])

REAL = os.path.expanduser(r"~\.claude\projects\{{BRAIN_SLUG}}\5e550002-ae81-4e52-bf60-6159a7bebd4a.jsonl")
if os.path.exists(REAL):
    t_corr = X.epoch("2026-09-26T17:11:01.000Z")
    e, st = scan(REAL)
    check("real transcript: his queued correction of 26 Sep 20:11 is a verdict (%d prompts, %d queued)"
          % (st["prompts"], st["queued_prompts"]),
          any(x[2] == "verdict" and x[3] == "correction" and abs(x[0] - t_corr) < 5 for x in e))
else:
    print("SKIP real transcript (not present)")
print("test_queued_prompts: %s" % ("all pass" if not fails else "%d FAILED" % fails))
sys.exit(1 if fails else 0)
