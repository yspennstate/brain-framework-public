"""Controls for the math hook's per-context dedup: a block shown before a compaction may be shown again after it.

 D1 an id never shown is not a duplicate
 D2 the same id in the same context is a duplicate
 D3 after a newer compaction the same id is not a duplicate
 D4 once recorded under that compaction it is a duplicate again
 D5 an older or equal compaction does not clear the record
 D6 no compaction index (0) keeps the old rule
 D7 a broken compaction lookup keeps the old rule
 D8 MATHBRAIN_DEDUP_PER_CONTEXT=0 keeps the old rule across a compaction

    python test_dedup_per_context.py      (a second or two; ALL PASS at the end)
Runs under a temporary home, so nothing of the user's is read or written.
"""
if __name__ == "__main__" and __import__("sys").argv[1:2] in (["-h"], ["--help"]):
    print((__doc__ or "").strip() or "run with no arguments")
    raise SystemExit(0)

import importlib
import os
import shutil
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
TMP = tempfile.mkdtemp(prefix="dedup_ctx_")
for k in ("HOME", "USERPROFILE"):
    os.environ[k] = TMP
os.environ.pop("MATHBRAIN_SHADOW", None)
os.makedirs(os.path.join(TMP, ".claude", "dreaming"), exist_ok=True)
results = []
CTX = {"t": 0.0, "broken": False}


def check(ok, what):
    results.append(bool(ok))
    print(("PASS  " if ok else "FAIL  ") + what)


def _last(sid):
    if CTX["broken"]:
        raise RuntimeError("index unreadable")
    return CTX["t"]


def load(**env):
    os.environ.pop("MATHBRAIN_DEDUP_PER_CONTEXT", None)
    os.environ.update(env)
    import math_sense
    importlib.reload(math_sense)
    fake = types.ModuleType("once_per_context")
    fake.last_compaction_at = _last
    sys.modules["once_per_context"] = fake
    return math_sense


try:
    M = load()
    sid = "0a1b2c3d-1111-4222-8333-444455556666"
    check(M._already_injected(sid, ["n1"]) == set(), "D1 a new id is not a duplicate")
    check(M._already_injected(sid, ["n1"]) == {"n1"}, "D2 the same id in the same context is a duplicate")
    CTX["t"] = 1000.0
    check(M._already_injected(sid, ["n1"]) == set(), "D3 after a newer compaction it is not a duplicate")
    check(M._already_injected(sid, ["n1"]) == {"n1"}, "D4 recorded under that compaction it is a duplicate again")
    CTX["t"] = 900.0
    check(M._already_injected(sid, ["n1"]) == {"n1"}, "D5 an older compaction does not clear the record")

    sid2 = "0a1b2c3d-2222-4222-8333-444455556666"
    CTX["t"] = 0.0
    M._already_injected(sid2, ["n2"])
    check(M._already_injected(sid2, ["n2"]) == {"n2"}, "D6 no compaction index keeps the old rule")
    CTX["broken"] = True
    check(M._already_injected(sid2, ["n2"]) == {"n2"}, "D7 a broken lookup keeps the old rule")
    CTX["broken"] = False

    M = load(MATHBRAIN_DEDUP_PER_CONTEXT="0")
    sid3 = "0a1b2c3d-3333-4222-8333-444455556666"
    M._already_injected(sid3, ["n3"])
    CTX["t"] = 5000.0
    check(M._already_injected(sid3, ["n3"]) == {"n3"}, "D8 MATHBRAIN_DEDUP_PER_CONTEXT=0 keeps the old rule")
finally:
    os.environ.pop("MATHBRAIN_DEDUP_PER_CONTEXT", None)
    shutil.rmtree(TMP, ignore_errors=True)
print("ALL PASS" if all(results) else "FAILED %d of %d" % (results.count(False), len(results)))
sys.exit(0 if all(results) else 1)
