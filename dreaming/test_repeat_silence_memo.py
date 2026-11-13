"""Controls for the math hook's repeat memo: a loop prompt that already went silent is not ranked again.

 R1 a prompt never seen has no memo
 R2 after a post-ranking silence (incoherent_weak_block) the same prompt in the same session is known
 R3 a different prompt in that session is not
 R4 a harness session (no uuid) never records
 R5 a silence for another reason (the gate) is not recorded
 R6 a compaction since the record clears it
 R7 a record older than two hours is ignored
 R8 _main_body returns at once on a known repeat and logs skip_repeat_known
 R9 MATHBRAIN_REPEAT_SKIP=0 turns the memo off

    python test_repeat_silence_memo.py      (a second or two; ALL PASS at the end)
Runs under a temporary home, so nothing of the user's is read or written.
"""
if __name__ == "__main__" and __import__("sys").argv[1:2] in (["-h"], ["--help"]):
    print((__doc__ or "").strip() or "run with no arguments")
    raise SystemExit(0)

import importlib
import io
import json
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
TMP = tempfile.mkdtemp(prefix="repeat_memo_")
for k in ("HOME", "USERPROFILE"):
    os.environ[k] = TMP
os.environ.pop("MATHBRAIN_SHADOW", None)
os.makedirs(os.path.join(TMP, ".claude", "dreaming"), exist_ok=True)
os.makedirs(os.path.join(TMP, ".claude", "brain_graph", "compactions"), exist_ok=True)
io.open(os.path.join(TMP, ".claude", "brain_graph", "compactions", "INDEX.jsonl"), "w", encoding="utf-8").close()
results = []


def check(ok, what):
    results.append(bool(ok))
    print(("PASS  " if ok else "FAIL  ") + what)


def load(**env):
    os.environ.pop("MATHBRAIN_REPEAT_SKIP", None)
    os.environ.update(env)
    for name in ("math_sense", "once_per_context"):
        if name in sys.modules:
            importlib.reload(sys.modules[name])
    import math_sense
    return math_sense


SID = "0a1b2c3d-1111-4222-8333-444455556666"
loop = {"session_id": SID, "prompt": "Loop tick: check the eigenvalue bound for the Laplacian on the torus again."}
try:
    M = load()
    check(M._repeat_known_silence(loop) is None, "R1 a new prompt has no memo")
    M._silence_note(loop, "incoherent_weak_block:0.31")
    check(M._repeat_known_silence(loop) == "incoherent_weak_block", "R2 the same prompt is known after its silence")
    check(M._repeat_known_silence(dict(loop, prompt="A different question about Sobolev embeddings.")) is None,
          "R3 a different prompt is not")
    harness = {"session_id": "bank-q17", "prompt": loop["prompt"]}
    M._silence_note(harness, "all_dedup_suppressed")
    check(M._repeat_known_silence(harness) is None, "R4 a harness session never records")
    other = dict(loop, prompt="Gate case: hello there, nothing mathematical in this one.")
    M._silence_note(other, "gate_refused")
    check(M._repeat_known_silence(other) is None, "R5 a gate silence is not recorded")

    real_ctx = M._repeat_ctx
    M._repeat_ctx = lambda sid: 1e9
    check(M._repeat_known_silence(loop) is None, "R6 a compaction since the record clears it")
    M._repeat_ctx = real_ctx

    p = M._repeat_path(SID)
    rec = json.load(io.open(p, encoding="utf-8"))
    fp = M._repeat_fp(loop)
    saved = rec[fp]["ts"]
    rec[fp]["ts"] = time.time() - M.REPEAT_SKIP_TTL_S - 60
    json.dump(rec, io.open(p, "w", encoding="utf-8"))
    check(M._repeat_known_silence(loop) is None, "R7 a record older than two hours is ignored")
    rec[fp]["ts"] = saved
    json.dump(rec, io.open(p, "w", encoding="utf-8"))

    t0 = time.time()
    out = M._main_body(dict(loop), [])
    log = io.open(os.path.join(TMP, ".claude", "dreaming", "mathbrain_silence.tsv"), encoding="utf-8").read()
    check(out is None and "skip_repeat_known:incoherent_weak_block" in log and time.time() - t0 < 5,
          "R8 _main_body returns at once on a known repeat (%.2f s)" % (time.time() - t0))

    M = load(MATHBRAIN_REPEAT_SKIP="0")
    check(not M.REPEAT_SKIP, "R9 MATHBRAIN_REPEAT_SKIP=0 turns the memo off")
finally:
    os.environ.pop("MATHBRAIN_REPEAT_SKIP", None)
    shutil.rmtree(TMP, ignore_errors=True)
print("ALL PASS" if all(results) else "FAILED %d of %d" % (results.count(False), len(results)))
sys.exit(0 if all(results) else 1)
