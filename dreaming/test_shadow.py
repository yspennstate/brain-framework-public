"""Controls for MATHBRAIN_SHADOW: the hook may print its block, but a shadow run records nothing as served.

 S1 MATHBRAIN_SHADOW=1 sets SHADOW; unset or 0 leaves it off
 S2 under shadow a real session's silence note goes to the harness file, not the production file
 S3 without shadow the same note goes to the production file
 S4 every serve-recording site in the hook body is guarded by SHADOW

    python test_shadow.py      (a few seconds; ALL PASS at the end)
"""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
results = []
SID = "0123abcd-0000-4000-8000-000000000000"


def check(ok, what):
    results.append(bool(ok))
    print(("PASS  " if ok else "FAIL  ") + what)


PROBE = r"""
import os, sys
import math_sense as m
home = sys.argv[1]
m.HOME = home
os.makedirs(os.path.join(home, ".claude", "dreaming"), exist_ok=True)
m._silence_note({"session_id": %r}, "probe_reason")
d = os.path.join(home, ".claude", "dreaming")
print("shadow=%%d prod=%%d harness=%%d" %% (int(m.SHADOW), int(os.path.exists(os.path.join(d, "mathbrain_silence.tsv"))),
      int(os.path.exists(os.path.join(d, "mathbrain_silence_harness.tsv")))))
""" % SID


def run(shadow):
    env = dict(os.environ)
    env.pop("MATHBRAIN_SHADOW", None)
    if shadow is not None:
        env["MATHBRAIN_SHADOW"] = shadow
    env["PYTHONPATH"] = os.pathsep.join([HERE] + [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p])
    with tempfile.TemporaryDirectory() as home:
        r = subprocess.run([sys.executable, "-c", PROBE, home], cwd=HERE, env=env, capture_output=True, text=True,
                           timeout=120)
    lines = (r.stdout + r.stderr).strip().splitlines()
    return r.returncode, (lines[-1] if lines else "")


def main():
    rc1, on = run("1")
    rc0, off = run("0")
    rcu, unset = run(None)
    check(rc1 == 0 and on.startswith("shadow=1") and off.startswith("shadow=0") and unset.startswith("shadow=0"),
          "S1 the dial: on=%r off=%r unset=%r" % (on, off, unset))
    check(rc1 == 0 and on.endswith("prod=0 harness=1"), "S2 shadow note goes to the harness file (%s)" % on)
    check(rc0 == 0 and off.endswith("prod=1 harness=0"), "S3 normal note goes to the production file (%s)" % off)
    src = open(os.path.join(HERE, "math_sense.py"), encoding="utf-8").read()
    guards = ["if not SHADOW:\n                _remember_math_context(",
              "if emitted_ids and not SHADOW:\n            _already_injected(",
              'if emitted_ids and not SHADOW and re.match(r"^[0-9a-f]{8}-", sess):',
              'real = bool(re.match(r"^[0-9a-f]{8}-", sess)) and not SHADOW']
    missing = [g.split("\n")[0] for g in guards if g not in src]
    check(not missing and "_already_injected(sess_for_guard" in src, "S4 serve-recording sites guarded (missing %s)" % missing)
    print("ALL PASS" if all(results) else "SOME FAILED")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
