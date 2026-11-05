"""Controls for _UNSERVED_KINDS: evidence and source nodes are never served; MATHBRAIN_SERVE_SOURCE=1 serves source nodes.

 K1 by default both evidence and source are unserved
 K2 with MATHBRAIN_SERVE_SOURCE=1 only evidence is unserved
 K3 every place that used to skip evidence alone now reads _UNSERVED_KINDS (no bare "evidence" test is left)

    python test_unserved_kinds.py      (a few seconds; ALL PASS at the end)
"""

import sys
if __name__ == "__main__" and any(arg in ("-h", "--help") for arg in sys.argv[1:]):
    print(__doc__ or "No command-line options.")
    raise SystemExit(0)
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
results = []


def check(ok, what):
    results.append(bool(ok))
    print(("PASS  " if ok else "FAIL  ") + what)


PROBE = "import math_sense as m; print(','.join(m._UNSERVED_KINDS))"


def run(val):
    env = dict(os.environ)
    env.pop("MATHBRAIN_SERVE_SOURCE", None)
    if val is not None:
        env["MATHBRAIN_SERVE_SOURCE"] = val
    env["PYTHONPATH"] = os.pathsep.join([HERE] + [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p])
    r = subprocess.run([sys.executable, "-c", PROBE], cwd=HERE, env=env, capture_output=True, text=True, timeout=120)
    return ((r.stdout + r.stderr).strip().splitlines() or [""])[-1]


def main():
    d, on = run(None), run("1")
    check(d == "evidence,source", "K1 default unserved kinds = %r" % d)
    check(on == "evidence", "K2 MATHBRAIN_SERVE_SOURCE=1 -> %r" % on)
    src = open(os.path.join(HERE, "math_sense.py"), encoding="utf-8").read()
    bare = re.findall(r'\.lower\(\) (?:!=|==) "evidence"', src)
    uses = src.count("in _UNSERVED_KINDS")
    check(not bare and uses >= 3, "K3 no bare evidence test left (%d bare, %d uses)" % (len(bare), uses))
    print("ALL PASS" if all(results) else "SOME FAILED")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
