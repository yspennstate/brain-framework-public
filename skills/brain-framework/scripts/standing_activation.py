#!/usr/bin/env python3
"""Manage the Brain Framework startup block in the current Codex profile."""

import argparse
import os
from pathlib import Path
import stat
import tempfile


BEGIN = b"<!-- brain-framework:begin -->"
END = b"<!-- brain-framework:end -->\n\n"
BOM = b"\xef\xbb\xbf"


def startup_block(skill):
    if any(character in str(skill) for character in "`\r\n"):
        raise ValueError("Use a Codex profile path without backticks or line breaks.")
    instructions = f"""
## Standing Brain Framework

For every chat in this Codex profile, open `{skill}` before substantive analysis, planning, tool use, or user-facing work. Validate the explicitly designated main brain for every task; an authorized project brain supplements it. Run fresh admission and follow continuous calibration through reasoning, actions, communication and closeout. Retain the exact source revision, main/project bindings, scope and unresolved gates when resuming or compacting. If the skill or required brain is unavailable, hold dependent work: no documentation-only mode, substitute corpus or local-memory fallback. A failed admission permits only bounded authorized diagnosis, repair, qualification and skill maintenance.

Before any authorized worker dispatch or follow-up, give the worker this skill or its full instructions, accessible original source at the same revision, designated main/project bindings, permitted recall scope, actual task and required evidence. The worker validates its required brains and performs fresh admission; missing access blocks the assignment. This applies to fresh contexts and nested workers. Verify observed source use, correct-corpus retrieval and consequential results before acceptance. Honor the user's later scope limits and disconnect requests; this contract does not override higher-priority rules or host permissions.
"""
    return BEGIN + b"\n" + instructions.encode("utf-8") + b"\n" + END


def read_instructions(path):
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError(f"Manage {path} manually because it is not a regular file.")
    return path.read_bytes() if path.exists() else b""


def without_block(data):
    start = len(BOM) if data.startswith(BOM) else 0
    if BEGIN not in data and END.rstrip() not in data:
        return data, False
    if not data.startswith(BEGIN + b"\n", start) or data.count(BEGIN) != 1 or data.count(END.rstrip()) != 1:
        raise ValueError("The Brain Framework markers were moved, duplicated, or damaged. Review them manually.")
    finish = data.find(END, start + len(BEGIN))
    if finish == -1:
        raise ValueError("The Brain Framework closing marker is incomplete. Review it manually.")
    return data[:start] + data[finish + len(END):], True


def replace_instructions(path, before, after):
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
    descriptor, name = tempfile.mkstemp(dir=path.parent, prefix=".brain-framework-")
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(after)
        temporary.chmod(mode)
        if read_instructions(path) != before:
            raise ValueError(f"{path} changed during activation. Retry against the new contents.")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def manage(action, codex_home):
    override = codex_home / "AGENTS.override.md"
    standard = codex_home / "AGENTS.md"
    files = {path: read_instructions(path) for path in (override, standard)}
    effective = override if files[override].removeprefix(BOM).strip() else standard
    unmanaged = {path: without_block(data) for path, data in files.items()}
    managed = [path for path, (_, present) in unmanaged.items() if present]
    if len(managed) > 1:
        raise ValueError("Both global instruction files contain the activation block. Review them manually.")

    skill = codex_home / "skills" / "brain-framework" / "SKILL.md"
    if action == "status":
        if not managed:
            print("Standing Brain Framework activation is disabled.")
        elif managed[0] != effective or not skill.is_file():
            raise ValueError(f"Standing activation in {managed[0]} is shadowed or its installed skill is missing.")
        elif startup_block(skill) not in files[effective]:
            raise ValueError(f"Standing activation in {effective} differs from the installed version. Run enable to refresh it.")
        else:
            print(f"Standing Brain Framework activation is enabled in {effective}.")
        return

    if action == "disable":
        if managed:
            target = managed[0]
            replace_instructions(target, files[target], unmanaged[target][0])
        print("Standing Brain Framework activation is disabled.")
        return

    if not skill.is_file():
        raise ValueError(f"Install the skill at {skill} before enabling standing activation.")
    if managed and managed[0] != effective:
        raise ValueError(f"{managed[0]} is shadowed by {effective}. Disable the old block, then enable again.")
    original = unmanaged[effective][0]
    offset = len(BOM) if original.startswith(BOM) else 0
    updated = original[:offset] + startup_block(skill) + original[offset:]
    if updated != files[effective]:
        replace_instructions(effective, files[effective], updated)
    print(f"Standing Brain Framework activation is enabled in {effective}.")


def manage_claude(action, codex_home):
    target = codex_home.parent / ".claude/CLAUDE.md"
    skill = codex_home / "skills/brain-framework/SKILL.md"
    data = read_instructions(target)
    original, present = without_block(data)
    block = startup_block(skill).replace(b"Codex profile", b"Claude profile")
    if action == "status":
        if not present:
            print("Standing Brain Framework activation is disabled.")
        elif block not in data or not skill.is_file():
            raise ValueError("Claude standing block differs from this installed skill or the skill is missing")
        else:
            print(f"Standing Brain Framework activation is enabled in {target}.")
        return
    if action == "disable":
        if present:
            replace_instructions(target, data, original)
        print("Standing Brain Framework activation is disabled.")
        return
    if not skill.is_file():
        raise ValueError(f"Install the skill at {skill} before enabling standing activation.")
    target.parent.mkdir(parents=True, exist_ok=True)
    offset = len(BOM) if original.startswith(BOM) else 0
    updated = original[:offset] + block + original[offset:]
    if updated != data:
        replace_instructions(target, data, updated)
    print(f"Standing Brain Framework activation is enabled in {target}.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("enable", "status", "disable"))
    parser.add_argument("--runtime", choices=("codex", "claude"), default="codex")
    args = parser.parse_args()
    action = args.action
    operation = manage_claude if args.runtime == "claude" else manage
    codex_home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser().absolute()
    try:
        if action == "status":
            operation(action, codex_home)
        else:
            lock = codex_home / ".brain-framework-activation.lock"
            try:
                lock.mkdir()
            except FileExistsError as error:
                raise ValueError(f"Activation is locked by {lock}. If no activation command is running, remove the empty lock directory and retry.") from error
            try:
                operation(action, codex_home)
            finally:
                lock.rmdir()
    except (OSError, ValueError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
