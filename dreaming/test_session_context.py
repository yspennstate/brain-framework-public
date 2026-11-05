"""Controls for the session-context route (MATHBRAIN_SESSION_CONTEXT, off by default).

 S1 unset, the route is off (SESSION_CONTEXT is False)
 S2 a remembered prompt comes back for a continuation that carries a proof cue and no admin cue
 S3 an admin cue, a missing cue, another session, or an expired record gives nothing
 S4 the wiring in _main_body: the context is remembered on a gate pass and joined to the prompt on a refusal, before
    the gate's own silence; the header names the route when it was used

    python test_session_context.py      (under a second; ALL PASS at the end)
"""
import ast
import importlib
import json
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
results = []


def check(ok, what):
    results.append(bool(ok))
    print(("PASS  " if ok else "FAIL  ") + what)


def fresh(env):
    for k in [k for k in os.environ if k.startswith("MATHBRAIN_")]:
        del os.environ[k]
    os.environ.update(env)
    sys.modules.pop("math_sense", None)
    return importlib.import_module("math_sense")


def main():
    check(fresh({}).SESSION_CONTEXT is False, "S1 off by default")

    d = tempfile.mkdtemp()
    m = fresh({"MATHBRAIN_SESSION_CONTEXT": "1", "MATHBRAIN_SESSION_CTX_DIR": d})
    m._remember_math_context("sess-a", "Prove the Tutte condition characterises graphs with a perfect matching")
    got = m._session_math_context("sess-a", "keep working until you have a full proof")
    check(m.SESSION_CONTEXT and got and "Tutte" in got, "S2 continuation gets the remembered prompt %r" % (got or "")[:40])

    admin = m._session_math_context("sess-a", "push the proof to the repo")
    nocue = m._session_math_context("sess-a", "keep going please")
    other = m._session_math_context("sess-b", "keep working until you have a full proof")
    path = m._ctx_file("sess-a")
    with open(path, encoding="utf-8") as f:
        rec = json.load(f)
    rec["ts"] = time.time() - m.SESSION_CTX_TTL_S - 5
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rec, f)
    old = m._session_math_context("sess-a", "keep working until you have a full proof")
    check(admin is None and nocue is None and other is None and old is None,
          "S3 admin %r no-cue %r other %r expired %r" % (admin, nocue, other, old))

    src = open(os.path.join(HERE, "math_sense.py"), encoding="utf-8").read()
    body = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "_main_body")
    seg = lambda n: ast.get_source_segment(src, n) or ""
    calls = {seg(n.func): n.lineno for n in ast.walk(body) if isinstance(n, ast.Call)}
    refuse = [n for n in ast.walk(body) if isinstance(n, ast.If) and seg(n.test) == "not gate_ok and ctx_used is None"]
    head = [n for n in ast.walk(body) if isinstance(n, ast.If) and seg(n.test) == "ctx_used is not None"
            and "header" in seg(n)]
    ok = ("_remember_math_context" in calls and "_session_math_context" in calls and len(refuse) == 1 and head
          and calls["_session_math_context"] < refuse[0].lineno < head[0].lineno)
    check(ok, "S4 wiring remember/recall before the refusal %d, header %d" % (len(refuse), len(head)))
    print("ALL PASS" if all(results) else "SOME FAILED")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
