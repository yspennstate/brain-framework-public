"""test_hook_liveness_judge_hint.py - a stale judged_nightly.jsonl says why off Windows: run_dream.py has no REM pass,
so nothing judges fires unless an awake agent does (5 Nov 2026).

    python test_hook_liveness_judge_hint.py [--module HOOK_LIVENESS.py] [--old OLD_HOOK_LIVENESS.py]

The module is copied into a throwaway folder beside a judged_nightly.jsonl aged three days and run there; its
judged_nightly line is read from the output.
 J1 _stale_hint names the awake-agent judging for judged_nightly off Windows, and adds nothing on Windows or for
    another stream
 J2 the real run prints STALE with that hint on the judged_nightly line (on this platform, when it is not Windows)
With --old, the module before this change must print the STALE line without the hint (the check can fail).
"""
import importlib.machinery
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import time

if __name__ == "__main__" and ("-h" in sys.argv[1:] or "--help" in sys.argv[1:]):
    print(__doc__)
    sys.exit(0)

HERE = os.path.dirname(os.path.abspath(__file__))
ARGS = sys.argv[1:]
MOD = os.path.abspath(ARGS[ARGS.index("--module") + 1]) if "--module" in ARGS else os.path.join(HERE, "hook_liveness.py")
OLD = os.path.abspath(ARGS[ARGS.index("--old") + 1]) if "--old" in ARGS else None
fails, ran = [], []


def check(ok, what):
    print(("PASS  " if ok else "FAIL  ") + what)
    ran.append(what)
    if not ok:
        fails.append(what)


def judged_line(path, tag):
    td = tempfile.mkdtemp(prefix="hl_judge_%s_" % tag)
    try:
        copy = os.path.join(td, "hook_liveness.py")
        shutil.copyfile(path, copy)
        j = os.path.join(td, "judged_nightly.jsonl")
        with open(j, "w", encoding="utf-8") as fh:
            fh.write('{"case_id": "x", "instinct": "y", "domain_at_play": "no"}\n')
        t = time.time() - 3 * 86400
        os.utime(j, (t, t))
        env = dict(os.environ, HOME=td, USERPROFILE=td)
        p = subprocess.run([sys.executable, "-s", copy], cwd=td, env=env, capture_output=True, text=True,
                           timeout=240)
        for line in p.stdout.splitlines():
            if line.strip().startswith("judged_nightly.jsonl"):
                return line
        return "(no judged_nightly line; rc %s; %s)" % (p.returncode, (p.stderr or "").strip()[-200:])
    finally:
        shutil.rmtree(td, ignore_errors=True)


def main():
    loader = importlib.machinery.SourceFileLoader("hl_judge_mod", MOD)
    spec = importlib.util.spec_from_file_location("hl_judge_mod", MOD, loader=loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    hint = getattr(m, "_stale_hint", None)
    posix = hint("judged_nightly.jsonl", "darwin") if hint else ""
    check(bool(hint) and "awake agent" in posix and "DREAM_AGENDA.md" in posix
          and hint("judged_nightly.jsonl", "win32") == "" and hint("reflex_precision.json", "darwin") == "",
          "J1 the hint off Windows: %r; on Windows and for another stream it adds nothing" % posix[:70])
    line = judged_line(MOD, "new")
    if sys.platform != "win32":
        check("STALE" in line and "awake agent" in line, "J2 the run prints: %s" % line.strip()[:160])
    if OLD:
        o = judged_line(OLD, "old")
        check("STALE" in o and "awake agent" not in o,
              "N1 the module before this change prints STALE with no hint (the check can fail): %s" % o.strip()[:120])
    print("FAIL" if fails else "PASS", "- %d controls, %d failed" % (len(ran), len(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
