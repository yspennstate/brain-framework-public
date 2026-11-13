"""Controls for the math hook's repeat skip: a prompt that already went silent in this session and context is not
ranked again.

 R1 a silence with a recorded reason (incoherent_weak_block, all_dedup_suppressed) is remembered for that prompt
 R2 any other silence reason is not remembered
 R3 a different prompt in the same session is not skipped
 R4 a session id that is not a real session id (a harness run) records nothing and skips nothing
 R5 a compaction since the record (a new context) runs the prompt again
 R6 a record older than the two-hour life runs the prompt again
 R7 an unreadable compaction index runs the prompt again
 R8 MATHBRAIN_REPEAT_SKIP=0 turns the skip off

    python test_repeat_skip.py      (under a few seconds; ALL PASS at the end)
"""
if "-h" in __import__("sys").argv[1:] or "--help" in __import__("sys").argv[1:]:
    print((__doc__ or "").strip() or "usage: python %s" % __import__("os").path.basename(__file__))
    raise SystemExit(0)
import importlib
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
results = []
TMP = tempfile.mkdtemp(prefix="repeat_skip_")
os.environ["HOME"] = os.environ["USERPROFILE"] = TMP
os.makedirs(os.path.join(TMP, ".claude", "dreaming"), exist_ok=True)


def check(ok, what):
    results.append(ok)
    print(("PASS  " if ok else "FAIL  ") + what)


def fresh(env):
    for k in [k for k in os.environ if k.startswith("MATHBRAIN_")]:
        del os.environ[k]
    os.environ.update(env)
    sys.modules.pop("math_sense", None)
    ms = importlib.import_module("math_sense")
    ms.SESSION_DIR = os.path.join(TMP, "sessions")
    return ms


ms = fresh({})
SID = "0123abcd-0000-4000-8000-000000000001"
ctx = {"v": 100.0}
ms._repeat_ctx = lambda sid: ctx["v"]
p1 = {"session_id": SID, "prompt": "is the spectral gap of this operator bounded below"}
p2 = {"session_id": SID, "prompt": "a different question about the same operator"}

check(ms.REPEAT_SKIP is True, "R0 the skip is on by default")
ms._silence_note(p1, "incoherent_weak_block:lead0.210_pair0.180")
check(ms._repeat_known_silence(p1) == "incoherent_weak_block", "R1 a recorded silence is remembered with its reason")
ms._silence_note(p2, "gate_refused")
check(ms._repeat_known_silence(p2) is None, "R2 another silence reason is not remembered")
check(ms._repeat_known_silence({"session_id": SID, "prompt": "something new"}) is None, "R3 a different prompt is not skipped")

h = {"session_id": "harness-run-7", "prompt": p1["prompt"]}
ms._silence_note(h, "all_dedup_suppressed:4")
check(ms._repeat_known_silence(h) is None and not os.path.exists(ms._repeat_path("harness-run-7")),
      "R4 a harness session records and skips nothing")

ctx["v"] = 200.0
check(ms._repeat_known_silence(p1) is None, "R5 a compaction since the record runs the prompt again")
ctx["v"] = 100.0
check(ms._repeat_known_silence(p1) == "incoherent_weak_block", "R5b back in the recorded context it is skipped")

path = ms._repeat_path(SID)
rec = json.load(open(path, encoding="utf-8"))
for v in rec.values():
    v["ts"] = float(v["ts"]) - ms.REPEAT_SKIP_TTL_S - 1
json.dump(rec, open(path, "w", encoding="utf-8"))
check(ms._repeat_known_silence(p1) is None, "R6 a record past its two-hour life runs the prompt again")

ms._silence_note(p1, "all_dedup_suppressed:3")
ctx["v"] = None
check(ms._repeat_known_silence(p1) is None, "R7 an unreadable compaction index runs the prompt again")

ms = fresh({"MATHBRAIN_REPEAT_SKIP": "0"})
check(ms.REPEAT_SKIP is False, "R8 MATHBRAIN_REPEAT_SKIP=0 turns the skip off")

print("ALL PASS" if all(results) else "FAILED %d of %d" % (results.count(False), len(results)))
sys.exit(0 if all(results) else 1)
