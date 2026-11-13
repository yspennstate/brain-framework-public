"""Every fast recall copy in this repository pins the files that ship beside it.

 R  PIN_RETRIEVAL in each fastrecall.py is the sha256 of framework/mathbrain/src/math_brain/retrieval.py
 Q  PIN_QUERY_EMBED in each fastrecall.py is the sha256 of the query_embed.py the copy loads (the one beside it)

The fast path and the encoder-ahead lever both refuse to run when a pin disagrees with the file, and they refuse
quietly: recall still answers, only slower. A source change landed without its re-pin turns the speedup off on every
install with nothing failing, so this test is the place it fails.

    python test_fastrecall_pins.py      (under a second; ALL PASS at the end)
    python test_fastrecall_pins.py ROOT (check another checkout of the repository)
"""
import hashlib
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.dirname(os.path.dirname(HERE))
RETRIEVAL = os.path.join(ROOT, "framework", "mathbrain", "src", "math_brain", "retrieval.py")
COPIES = [os.path.join(ROOT, "framework", "dreaming"), os.path.join(ROOT, "agent_tools")]
res = []


def sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def pin(src, name):
    m = re.search(r'^%s = "([0-9a-f]{64})"$' % name, src, re.M)
    return m.group(1) if m else None


def ok(name, cond, extra=""):
    res.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name + (" " + extra if extra else ""), flush=True)


if not os.path.isfile(RETRIEVAL):
    print("SKIP: %s not found" % RETRIEVAL)
    sys.exit(0)
want_r = sha(RETRIEVAL)
for folder in COPIES:
    fr = os.path.join(folder, "fastrecall.py")
    if not os.path.isfile(fr):
        continue
    with open(fr, encoding="utf-8") as fh:
        src = fh.read()
    rel = os.path.relpath(fr, ROOT).replace(os.sep, "/")
    got = pin(src, "PIN_RETRIEVAL")
    ok("R %s pins retrieval.py" % rel, got == want_r, "" if got == want_r else "(pin %s, file %s)" % (
        (got or "none")[:12], want_r[:12]))
    qe = os.path.join(folder, "query_embed.py")
    got = pin(src, "PIN_QUERY_EMBED")
    if os.path.isfile(qe):
        want_q = sha(qe)
        ok("Q %s pins the query_embed.py beside it" % rel, got == want_q,
           "" if got == want_q else "(pin %s, file %s)" % ((got or "none")[:12], want_q[:12]))
ok("found at least one fast recall copy", len(res) > 0)
print("ALL PASS" if all(res) else "RESULT %d/%d" % (sum(res), len(res)))
sys.exit(0 if all(res) else 1)
