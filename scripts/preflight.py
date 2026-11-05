#!/usr/bin/env python3
"""Run everything this repository can check about itself, before you push.

There is no CI here. That is a deliberate cost decision - the math brain's suite alone is
25 minutes and Actions minutes on a private repo are billed - but it means the only thing
standing between a broken commit and main is whether a person remembered to run the
checks. On 2026-08-10 a person did not: exporting dreaming/job_cycle introduced two
help-flag hazards, the tree's own ratchet went red, and it stayed red on main for two
hours because every check run that day was one the author chose about the change they had
just made, rather than the one the REPO runs about everything.

The README already documents these commands. This exists because three commands you must
remember is a checklist, and one command is a habit.

    python scripts/preflight.py            # the fast gates: seconds
    python scripts/preflight.py --full     # adds the math brain suite (~25 min)

Exits non-zero if anything fails, and prints what it did NOT run so a green here is never
mistaken for a green everywhere.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# (label, argv, cwd relative to ROOT). Ordered cheapest-first so a typo fails in seconds.
FAST = [
    ("private-label guard", [sys.executable, "scripts/check_no_private_labels.py"], "."),
    ("guard's own controls", [sys.executable, "-m", "pytest",
                              "scripts/test_check_no_private_labels.py", "-q"], "."),
    ("core suites", [sys.executable, "-m", "pytest", "dreaming", "mesh", "governor",
                     "methods", "-q"], "."),
    ("network", [sys.executable, "-m", "pytest", "network", "-q"], "."),
    ("resilience", [sys.executable, "-m", "pytest", "resilience", "-q"], "."),
    ("viewer", [sys.executable, "-m", "pytest", "tests", "-q"], "viewer"),
    ("smoke (end to end)", [sys.executable, "scripts/smoke_framework.py"], "."),
]
FULL_EXTRA = [
    ("math brain", [sys.executable, "-m", "pytest", ".", "-q"], "mathbrain"),
]
# Named so a green is honest about its own edges.
NOT_COVERED = [
    "a whole-tree pytest run - two test_network.py basenames collide, see the README",
    "the math brain suite unless --full is given",
    "anything requiring a real desktop (scripts/verify_no_console_flash.py)",
]


def run(label, argv, cwd):
    started = time.time()
    proc = subprocess.run(argv, cwd=os.path.join(ROOT, cwd), capture_output=True,
                          text=True, encoding="utf-8", errors="replace")
    elapsed = time.time() - started
    tail = [l for l in (proc.stdout or "").splitlines() if l.strip()][-1:]
    detail = tail[0][:72] if tail else ""
    status = "ok  " if proc.returncode == 0 else "FAIL"
    print("  %s %-22s %5.1fs  %s" % (status, label, elapsed, detail))
    if proc.returncode != 0:
        for line in (proc.stdout or "").splitlines()[-12:]:
            if line.strip():
                print("        %s" % line[:110])
        for line in (proc.stderr or "").splitlines()[-4:]:
            if line.strip():
                print("        %s" % line[:110])
    return proc.returncode == 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--full", action="store_true",
                        help="also run the math brain suite (~25 minutes)")
    args = parser.parse_args(argv)

    checks = FAST + (FULL_EXTRA if args.full else [])
    print("preflight: %d checks%s\n" % (len(checks), " (--full)" if args.full else ""))
    failed = [label for label, cmd, cwd in checks if not run(label, cmd, cwd)]

    print("\nnot covered by this run:")
    for item in NOT_COVERED:
        if args.full and item.startswith("the math brain"):
            continue
        print("  - %s" % item)

    if failed:
        print("\n%d check(s) FAILED: %s" % (len(failed), ", ".join(failed)))
        return 1
    print("\nall %d passed - safe to push" % len(checks))
    return 0


if __name__ == "__main__":
    sys.exit(main())
