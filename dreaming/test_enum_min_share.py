"""Controls for the enum abstain's share floor (MATHBRAIN_ENUM_MIN_SHARE): the hook goes silent on three absent
quoted titles only when the quotes are what the prompt is about.

 E1 a question that is mostly three absent quoted titles is silenced
 E2 a long prompt whose three quoted phrases are a few percent of its words still serves
 E3 MATHBRAIN_ENUM_MIN_SHARE=0 restores the old rule (the long prompt is silenced)
 E4 a question with one quoted title in the pool still serves
 E5 a bad MATHBRAIN_ENUM_MIN_SHARE value falls back to 0.15

    python test_enum_min_share.py      (under a second; ALL PASS at the end)
Runs the hook's own lines, cut from _main_body, against stubs; nothing of the user's is read or written.
"""
if __name__ == "__main__" and __import__("sys").argv[1:2] in (["-h"], ["--help"]):
    print((__doc__ or "").strip() or "run with no arguments")
    raise SystemExit(0)

import ast
import io
import os
import re
import sys
import textwrap

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = io.open(os.path.join(HERE, "math_sense.py"), encoding="utf-8").read().replace("\r\n", "\n")
LINES = SRC.split("\n")
results = []


def check(ok, what):
    results.append(bool(ok))
    print(("PASS  " if ok else "FAIL  ") + what)


def snippet():
    """From the share floor's first statement (if any) through the abstain test, as the body of a function."""
    tree = ast.parse(SRC)
    for fn in tree.body:
        if isinstance(fn, ast.FunctionDef) and fn.name == "_main_body":
            stmts = [n for n in ast.walk(fn) if hasattr(n, "body") and isinstance(getattr(n, "body"), list)]
            for parent in stmts:
                body = parent.body
                for i, st in enumerate(body):
                    if isinstance(st, ast.If) and "MATHBRAIN_ENUM_ABSTAIN" in ast.dump(st.test):
                        j = i
                        while j > 0 and "_enum_share" in ast.dump(body[j - 1]):
                            j -= 1
                        code = textwrap.dedent("\n".join(LINES[body[j].lineno - 1:st.end_lineno]))
                        return "def gate(prompt, _quoted_n, _named_entries, pinned_ids, data):\n" + \
                            textwrap.indent(code, "    ") + "\n    return 'served'\n"
    return None


CODE = snippet()
quoted_re = re.compile(r"[\"“”]([^\"“”]{8,160})[\"“”]")


def run(prompt, named=0, pinned=None, **env):
    os.environ.pop("MATHBRAIN_ENUM_MIN_SHARE", None)
    os.environ.pop("MATHBRAIN_ENUM_ABSTAIN", None)
    os.environ.update(env)
    notes = []
    g = {"os": os, "_QUOTED_RE": quoted_re, "_silence_note": lambda d, why: notes.append(why)}
    exec(CODE, g)
    n = len(quoted_re.findall(prompt))
    out = g["gate"](prompt, n, named, pinned or [], {})
    return "silent" if out is None else out


ENUM = ('Which of these does the store hold: "bounded gaps for ordered sieve weights", '
        '"the hitting estimate for planar reflected walks", "a second moment bound on cyclic sums"?')
FILLER = " ".join(["the argument continues with an estimate of the remainder term in each case"] * 30)
LONG = ('Read the proof below. "State the gap if there is one", "name the lemma that closes it", '
        '"say whether the bound is sharp". ' + FILLER)

try:
    check(CODE is not None, "E0 the abstain block is found in _main_body")
    check(run(ENUM) == "silent", "E1 a question made of three absent quoted titles is silenced")
    check(run(LONG) == "served", "E2 three quoted instruction phrases in a long prompt still serve")
    check(run(LONG, MATHBRAIN_ENUM_MIN_SHARE="0") == "silent", "E3 MATHBRAIN_ENUM_MIN_SHARE=0 keeps the old rule")
    check(run(ENUM, named=1) == "served", "E4 one quoted title in the pool still serves")
    check(run(LONG, MATHBRAIN_ENUM_MIN_SHARE="x") == "served" and run(ENUM, MATHBRAIN_ENUM_MIN_SHARE="x") == "silent",
          "E5 a bad share value falls back to the default")
except Exception as e:
    check(False, "raised %s: %s" % (type(e).__name__, e))
finally:
    os.environ.pop("MATHBRAIN_ENUM_MIN_SHARE", None)
print("ALL PASS" if all(results) else "FAILED %d of %d" % (results.count(False), len(results)))
sys.exit(0 if all(results) else 1)
