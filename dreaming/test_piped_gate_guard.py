# -*- coding: utf-8 -*-
"""Controls both ways for danger_mode._piped_gate (claude-42778e9b, 5 Nov 2026).
    python -s test_piped_gate_guard.py"""

import sys
if __name__ == "__main__" and any(arg in ("-h", "--help") for arg in sys.argv[1:]):
    print(__doc__ or "No command-line options.")
    raise SystemExit(0)

import json
import os
import subprocess

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import danger_mode as dm  # noqa: E402

FIRE = [
    # the 5 Nov shape: a wrong scanner path, tail's 0, and the push went out
    "python3 scripts/scan_secrets.py --range a..b 2>&1 | tail -1 && python3 mesh.py assert x && git push -q origin HEAD:main",
    "python scan_secrets.py --staged | tail -3 && git commit -m x -- f",
    "python3 -m pytest -q 2>&1 | tail -1 && git add f && git commit -q -m x -- f",
    "python3 scripts/check_no_private_labels.py | head -1 && git -C repo push origin main",
    "python export_public.py src out | tee log && safe_push.py --paths a",
    "cd r && python3 dreaming/scan_secrets.py --range x..y | sed -n 1p && gh release create v1",
    'python3 scan_secrets.py --range "$(git rev-parse a)..$(git rev-parse b)" 2>&1 | tail -1 && git push',
    # an assignment's exit is its substitution's, which is tail's
    'out="$(python3 scan_secrets.py --staged 2>&1 | tail -1)" && git push',
    "python3 scan_secrets.py $(git ls-files | grep -v x) | tail -1 && git push",
    # a test later in the chain does not stand in for the gate before it (three real 5 Nov commits)
    'python3 scripts/check_no_private_labels.py 2>&1 | tail -1 && git fetch -q origin && '
    '[ "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" ] && git commit -q -m x -- f',
]
QUIET = [
    # the gate's own exit decides
    "python3 dreaming/scan_secrets.py --range a..b && git push -q origin HEAD:main",
    # the pipeline ends in grep: grep's exit is the test, by design
    "python3 scripts/check_no_private_labels.py 2>&1 | tail -1 | grep -q '^clean' && git commit -m x -- f",
    "set -o pipefail; python3 scan_secrets.py --staged | tail -1 && git push",
    # a ; or || ends the chain: the push does not depend on the gate at all
    "python3 -m pytest -q | tail -1; git status",
    "python3 -m pytest -q | tail -3 || echo failed",
    # no commit or push after it
    "python3 -m pytest -q -p no:cacheprovider tests/x.py 2>&1 | tail -15",
    # a reader pipe that is not a gate
    "git log --oneline | head -3 && git push origin HEAD:main",
    "grep -n scan_secrets dreaming/*.py | head && echo done",
    # the shape quoted in a message or a fixture is text, not a pipeline (the three false fires of the replay)
    'python3 mesh.py send --to x --topic t --body "your push ran scan_secrets.py --range a..b | tail -1 && git push"',
    "python3 -c 'for c in [\"python3 scan_secrets.py | tail -1 && git push\"]: print(c)' && git commit -m x -- f",
    # a test of the scan's last line is itself the gate
    '[ "$(python3 scan_secrets.py --staged 2>&1 | tail -1)" = "secret scan clean" ] && git push',
]


def _hook(command):
    env = dict(os.environ, PIPEGATE_GUARD="1")
    payload = json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": command}})
    r = subprocess.run([sys.executable, "-s", os.path.join(os.path.dirname(os.path.abspath(__file__)), "danger_mode.py")],
                       input=payload, capture_output=True, text=True, env=env, timeout=60)
    return r.stdout


def main():
    bad = 0
    for c in FIRE:
        if not dm._piped_gate(c):
            print("MISSED", c)
            bad += 1
    for c in QUIET:
        if dm._piped_gate(c):
            print("FALSE FIRE", c)
            bad += 1
    out = _hook(FIRE[0])
    if "REFUSED (piped gate)" not in out or '"deny"' not in out:
        print("HOOK did not refuse:", out[:300])
        bad += 1
    if "piped gate" in _hook(QUIET[0]):
        print("HOOK refused a quiet command")
        bad += 1
    print("piped gate: %d fire, %d quiet, %d wrong" % (len(FIRE), len(QUIET), bad))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
