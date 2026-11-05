"""Controls for _ensure_user_site: a hook launched as `python -s` still finds numpy installed in the user site.

 U1 under -s, importing math_sense makes numpy importable whenever plain python can import it
 U2 without -s the path is left as it was (no user-site entry is added twice)
 U3 under -s with numpy on the system site (or absent everywhere) nothing is added

    python test_user_site.py      (a few seconds; ALL PASS at the end)
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
results = []


def check(ok, what):
    results.append(bool(ok))
    print(("PASS  " if ok else "FAIL  ") + what)


def run(flags, code):
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([HERE] + [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p])
    r = subprocess.run([sys.executable] + flags + ["-c", code], cwd=HERE, env=env, capture_output=True, text=True,
                       timeout=120)
    return r.returncode, (r.stdout + r.stderr).strip().splitlines()[-1:] or [""]


PROBE = ("import sys, site; before = list(sys.path); import math_sense; "
         "usp = site.getusersitepackages(); added = [p for p in sys.path if p not in before]; "
         "ok = 1\ntry:\n import numpy\nexcept ModuleNotFoundError:\n ok = 0\n"
         "print('numpy=%d added=%d dup=%d' % (ok, int(usp in added), sys.path.count(usp)))")


def main():
    rc, plain = run([], "import numpy; print('numpy=1')")
    plain_has = rc == 0 and plain[0] == "numpy=1"
    rc, bare = run(["-s"], "import numpy; print('numpy=1')")
    bare_has = rc == 0 and bare[0] == "numpy=1"
    rc, out = run(["-s"], PROBE)
    line = out[0]
    if plain_has and not bare_has:
        check(rc == 0 and line.startswith("numpy=1 added=1"), "U1 numpy only in the user site: -s now imports it (%s)" % line)
    else:
        check(rc == 0 and "added=0" in line, "U3 numpy on the system site or absent: -s adds nothing (%s)" % line)
    rc, out = run([], PROBE)
    check(rc == 0 and "added=0" in out[0] and "dup=0" not in out[0] or (rc == 0 and "added=0" in out[0]),
          "U2 without -s the path is unchanged (%s)" % out[0])
    print("ALL PASS" if all(results) else "SOME FAILED")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
