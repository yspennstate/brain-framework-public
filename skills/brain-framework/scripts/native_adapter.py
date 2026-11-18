"""Profile/OS bindings only; original algorithms, receipts and gate predicates remain unchanged."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import runpy
import shlex
import subprocess
import sys


def wake_command(python, brainctl, profile, session=None):
    argv = [str(python), str(brainctl), "--profile", str(profile), "--maintenance", "repair"]
    if session:
        argv += ["--session", str(session)]
    argv += ["native", "script", "dreaming/codex_wake.py"]
    return subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--profile", required=True)
    p.add_argument("--brainctl", required=True)
    modes = p.add_mutually_exclusive_group(required=True)
    modes.add_argument("--guard")
    modes.add_argument("--task-checklist")
    modes.add_argument("--codex-capture")
    args, remaining = p.parse_known_args()
    if args.task_checklist:
        target = Path(args.task_checklist)
        sys.path.insert(0, str(target.parent))
        import task_checklist_hook
        argv = [sys.executable, args.brainctl, "--profile", args.profile, "native", "checklist"]
        task_checklist_hook.CLI = subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)
        sys.argv = [str(target), *remaining]
        return task_checklist_hook.main()
    if args.codex_capture:
        target = Path(args.codex_capture)
        sys.path.insert(0, str(target.parent))
        import extract_codex_events
        extract_codex_events.ROOT = str(Path(args.profile) / "sessions")
        sys.argv = [str(target), *remaining]
        return extract_codex_events.main()
    guard = Path(args.guard)
    sys.path.insert(0, str(guard.parent))
    import codex_brain_wake_guard
    codex_brain_wake_guard._wake_command = lambda: wake_command(sys.executable, args.brainctl, args.profile, os.environ.get("CODEX_THREAD_ID"))
    sys.argv = [str(guard), *remaining]
    runpy.run_path(str(guard), run_name="__main__")


if __name__ == "__main__":
    raise SystemExit(main() or 0)
