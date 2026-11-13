#!/usr/bin/env python3
"""Static acceptance gate for the live Codex warning hook contract.

This deliberately does not bypass Codex hook trust or spend a model turn.  It
guards the two failures that previously made the warning path look configured
while remaining ineffective on Windows:

* commandWindows must invoke Python directly (a quoted executable command was
  rejected by Codex's Windows hook host), and
* the advisory reflex belongs on UserPromptSubmit and PreToolUse only, while
  the completion/timed-work carry guard belongs on UserPromptSubmit + Stop,
  and
* support hooks may extend those events only through exact approved scripts;
  the project-memory hot hook may use its narrow mutation matcher without
  narrowing the warning-delivery hooks, and
* one staged strong-nodes fire hook may occupy an optional PreToolUse slot
  (exact runtime location and --enabled flag path); enablement stays a
  separate owner decision - the flag file, not this gate, turns it on.

For a full delivery acceptance test, run a disposable Codex turn after this
gate and require the model to respond to a known reflex.  That test is kept
manual because it consumes model tokens and intentionally exercises user trust.
"""
from __future__ import annotations

import argparse
import copy
import json
import ntpath
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any


EVENTS = ("UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop")
# Re-baselined 2026-09-13: the owner's 09-07 and 09-09 orders added the summary, proof/math/waste sense,
# push-reminder, movie-alert, instinct/compute/danger and restart-handoff hooks to Codex. Four of them
# had been registered in Claude's {"command","args"} form, which the Codex binary does not read (it has
# no "args" key), and two carried the error-graph observer as their Windows command - so the 09-02
# baseline below was reporting the fleet's additions as 16 contract failures on every wake while the
# real defects (hooks that never ran, a doubled observer) hid inside the same list.
# UserPromptSubmit 13 -> 14 on 2026-09-15: slow_sense.py (see the ADVISORY_SCRIPTS note below).
# UserPromptSubmit 14 -> 17 and Stop 3 -> 4 on 2026-09-20: owner_prompts_hook.py and task_checklist_hook.py
# (both his 2026-09-18 orders, wired that day but never taught to this contract - two more permanent red
# lines on every wake) plus prompt_archive.py, which had never been wired for Codex at all: the ledger's last
# Codex prompt was a 07-06 backfill, so eleven weeks of his Codex asks were missing from every hourly build.
# UserPromptSubmit 17 -> 18 on 2026-09-20 18:5x: owner_board_hook.py, his board for the scheduled tasks (the
# 18:37 order: "a whole tasks thing in the brain that the schedules keep reading, that agents like you keep reading").
EXPECTED_HANDLER_COUNTS = {"UserPromptSubmit": 18, "PreToolUse": 6, "PostToolUse": 1, "Stop": 4}
REFLEX_EVENTS = frozenset(("UserPromptSubmit", "PreToolUse"))
STOP_GUARD_EVENTS = frozenset(("UserPromptSubmit", "Stop"))
REFLEX_NAME = "codex_reflex_hook.py"
STOP_GUARD_NAME = "codex_stop_guard.py"
GRAPH_NAME = "error_graph\\hook.py"
HELP_GUARD_NAME = "codex_help_guard_hook.py"
PROJECT_MEMORY_NAME = "project_memory_hook.py"
PROJECT_MEMORY_HOT_NAME = "project_memory_hot_hook.py"
# THE OBSERVER MAY BE WIRED THROUGH ITS GATE. error_graph_gate.py runs error_graph/hook.py with the same arguments and
# stdin only while error_graph_runtime/ENABLED exists, so a machine that never reads the graph pays one interpreter start
# per event instead of the engine import and its SQLite writes. Either form counts as the one observer per event; the
# gate form is accepted only while its source still gates on that flag (checked below), so a gate that stopped gating
# reads as a failure.
GATE_NAME = "error_graph_gate.py"
GATE_FLAG_MARKERS = ("error_graph_runtime", "ENABLED", "sys.exit(0)")


_RAW_SCRIPT_PATHS: dict[str, str] = {}


def _norm_script(path: str) -> str:
    raw = path.strip().strip('"\'')
    norm = ntpath.normcase(ntpath.normpath(raw))
    # 2026-10-29 (Mac port): ntpath turns /Users/x/... into \users\x\..., which no POSIX open() can read, so every
    # expected script read as missing and the selftest's rejections all came from that one cause. Compare in the
    # normalized form, read from the first raw spelling seen (the module's own Path.home() tables).
    _RAW_SCRIPT_PATHS.setdefault(norm, raw)
    return norm


EXPECTED_SCRIPTS = {
    REFLEX_NAME: _norm_script(str(Path.home() / ".claude" / "dreaming" / REFLEX_NAME)),
    STOP_GUARD_NAME: _norm_script(str(Path.home() / ".claude" / "dreaming" / STOP_GUARD_NAME)),
    GRAPH_NAME: _norm_script(str(Path.home() / ".claude" / "dreaming" / "error_graph" / "hook.py")),
    GATE_NAME: _norm_script(str(Path.home() / ".claude" / "dreaming" / GATE_NAME)),
    HELP_GUARD_NAME: _norm_script(str(Path.home() / ".claude" / "dreaming" / HELP_GUARD_NAME)),
    PROJECT_MEMORY_NAME: _norm_script(
        str(Path.home() / ".brain-framework" / "runtime" / PROJECT_MEMORY_NAME)
    ),
    PROJECT_MEMORY_HOT_NAME: _norm_script(
        str(Path.home() / ".brain-framework" / "runtime" / PROJECT_MEMORY_HOT_NAME)
    ),
}
SUPPORT_SCRIPTS = tuple(_norm_script(str(Path.home() / ".claude" / "dreaming" / name)) for name in (
    "codex_hook_telemetry.py", "owner_report_contract.py",
))
# Owner-ordered advisory hooks shared with the Claude side (09-07 summary/waste, 09-09 movie alert and the
# reflex trio, restart handoff). Plain `python -B <script>` with no extra arguments; any other shape is
# still refused. A scoped (non-wildcard) matcher is allowed only for the hot hook and the push reminder.
ADVISORY_SCRIPTS = frozenset(_norm_script(str(p)) for p in (
    Path.home() / ".claude" / "dreaming" / "proof_sense.py",
    Path.home() / ".claude" / "dreaming" / "math_sense.py",
    Path.home() / ".claude" / "dreaming" / "waste_sense.py",
    Path.home() / ".claude" / "dreaming" / "push_reminder.py",
    Path.home() / ".claude" / "dreaming" / "instinct_prime.py",
    Path.home() / ".claude" / "dreaming" / "compute_sense.py",
    Path.home() / ".claude" / "dreaming" / "danger_mode.py",
    # slow_sense.py joined the family on 2026-09-15 (the laptop-slowness probe: it is the source of the
    # [slow-sense] advisory lines, sibling to compute_sense and waste_sense above). It is a tracked brain
    # hook - committed to the repo twice that day and present in Claude's own dispatcher list - but the
    # allowlist here never learned it, so hook_liveness reported "commandWindows does not target an
    # approved hook script" plus a 13-vs-14 count mismatch on EVERY wake. Same re-baselining as the
    # 09-13 note above: a legitimate addition the contract had not caught up with, and a permanent red
    # verdict is how a safety checker stops being read.
    Path.home() / ".claude" / "dreaming" / "slow_sense.py",
    Path.home() / ".claude" / "dreaming" / "summary_hook.py",
    Path.home() / ".claude" / "dreaming" / "voice_guard_hook.py",
    Path.home() / ".claude" / "movie_sentinel" / "movie_alert_hook.py",
    Path.home() / ".codex" / "restart_handoff_hook.py",
    # 2026-09-20: the three hooks that carry a runtime argument (see ADVISORY_EXTRA_ARGS)
    Path.home() / ".claude" / "dreaming" / "owner_prompts_hook.py",
    Path.home() / ".claude" / "dreaming" / "task_checklist_hook.py",
    Path.home() / ".claude" / "dreaming" / "prompt_archive.py",
    # 2026-09-20 18:5x: the owner board for the scheduled tasks (runtime argument, JSON output on Codex)
    Path.home() / ".claude" / "dreaming" / "owner_board_hook.py",
))
# The only extra arguments an advisory hook may carry, by script: these three need to know they run under
# Codex (the payload's session key and the ledger's source column differ by runtime). Any other extra
# argument on any advisory hook is still refused.
ADVISORY_EXTRA_ARGS = {
    _norm_script(str(Path.home() / ".claude" / "dreaming" / "owner_prompts_hook.py")): ["--hook", "--runtime", "codex"],
    _norm_script(str(Path.home() / ".claude" / "dreaming" / "task_checklist_hook.py")): ["--hook", "--runtime", "codex"],
    _norm_script(str(Path.home() / ".claude" / "dreaming" / "prompt_archive.py")): ["--runtime", "codex"],
    _norm_script(str(Path.home() / ".claude" / "dreaming" / "owner_board_hook.py")): ["--hook", "--runtime", "codex"],
}
SCOPED_MATCHER_SCRIPTS = frozenset((
    "project_memory_hot_hook.py", "push_reminder.py",
))
EXPECTED_PYTHON = _norm_script(sys.executable)
SUPPORT_EVENT_COUNTS = {
    "UserPromptSubmit": {PROJECT_MEMORY_NAME: 1},
    "PreToolUse": {HELP_GUARD_NAME: 1, PROJECT_MEMORY_HOT_NAME: 1},
    "PostToolUse": {},
    "Stop": {},
}

# One OPTIONAL staged slot: the strong-nodes fire hook is wired and host-trusted
# ahead of activation but stays inert until the owner's review creates the
# ENABLED flag file. This gate accepts its exact wiring shape so a staged
# campaign does not hold the liveness verdict red for weeks (alarm fatigue);
# what may EXECUTE is still controlled by the Codex trust hashes and the flag
# file, never by this gate. Anything off-shape or off-event is still rejected.
STRONG_NODES_EVENT = "PreToolUse"
_STRONG_NODES_RUNTIME = _norm_script(
    str(Path.home() / ".claude" / "dreaming" / "strong_nodes" / "runtime"))
_STRONG_NODES_ENABLED = _norm_script(
    str(Path.home() / ".claude" / "dreaming" / "strong_nodes" / "ENABLED"))
_STRONG_NODES_HASH = re.compile(r"[0-9a-f]{64}\Z")


def _is_strong_nodes_script(script: str) -> bool:
    head, base = ntpath.split(script)
    if base != "hook.py":
        return False
    parent, generation = ntpath.split(head)
    return parent == _STRONG_NODES_RUNTIME and bool(_STRONG_NODES_HASH.match(generation))


def _script_tokens(command: str) -> set[str]:
    # Live Windows commands deliberately use space-free absolute paths. Exact .py tokens prevent
    # same-basename decoys and ``hook.py.disabled`` from satisfying the contract.
    return {_norm_script(token) for token in command.split() if token.strip('"\'').lower().endswith(".py")}


def _script_basenames(command: str) -> set[str]:
    return {ntpath.basename(script) for script in _script_tokens(command)}


def _command_shape_problem(command: str) -> str | None:
    tokens = command.split()
    if not tokens:
        return None  # reported as missing by the caller
    if _norm_script(tokens[0]) != EXPECTED_PYTHON:
        return "commandWindows must use the active trusted Python executable"
    if len(tokens) >= 3 and tokens[1] == "-B":
        mode = "normal"
        script = _norm_script(tokens[2])
        extra = tokens[3:]
    elif len(tokens) >= 4 and tokens[1:3] == ["-S", "-B"]:
        mode = "isolated"
        script = _norm_script(tokens[3])
        extra = tokens[4:]
    else:
        return "commandWindows must use Python -B followed by one exact hook script"
    if script in ADVISORY_SCRIPTS:
        if mode != "normal":
            return "advisory hooks may not use Python -S -B"
        allowed_extra = ADVISORY_EXTRA_ARGS.get(script, [])
        if extra != allowed_extra:
            return ("advisory hook has unexpected extra arguments" if not allowed_extra
                    else "advisory hook must end with " + " ".join(allowed_extra))
        return None
    if script not in EXPECTED_SCRIPTS.values():
        if not _is_strong_nodes_script(script):
            return "commandWindows does not target an approved hook script"
        if mode != "normal":
            return "strong-nodes fire hook may not use Python -S -B"
        if (len(extra) != 4 or extra[:3] != ["--hook-runtime", "codex", "--enabled"]
                or _norm_script(extra[3]) != _STRONG_NODES_ENABLED):
            return ("strong-nodes fire hook must end with --hook-runtime codex "
                    "--enabled <strong_nodes ENABLED flag>")
        return None
    if script == EXPECTED_SCRIPTS[PROJECT_MEMORY_HOT_NAME]:
        if mode != "isolated":
            return "project-memory hot hook must use Python -S -B"
        if extra:
            return "project-memory hot hook has unexpected extra arguments"
        return None
    if mode != "normal":
        return "only the project-memory hot hook may use Python -S -B"
    if script in (EXPECTED_SCRIPTS[GRAPH_NAME], EXPECTED_SCRIPTS[GATE_NAME]):
        if extra != ["--hook-runtime", "codex"]:
            return "error-graph command must end with --hook-runtime codex"
    elif script in (
        EXPECTED_SCRIPTS[REFLEX_NAME],
        EXPECTED_SCRIPTS[STOP_GUARD_NAME],
        EXPECTED_SCRIPTS[HELP_GUARD_NAME],
        EXPECTED_SCRIPTS[PROJECT_MEMORY_NAME],
    ):
        if extra:
            return "warning wrapper command has unexpected extra arguments"
    return None


GATE_SOURCE = EXPECTED_SCRIPTS[GATE_NAME]   # the selftest points this at a decoy to prove the check can fail


def _gate_problem() -> str | None:
    """The gate form counts as the observer only while it still gates on error_graph_runtime/ENABLED."""
    try:
        source = Path(_RAW_SCRIPT_PATHS.get(GATE_SOURCE, GATE_SOURCE)).read_text(encoding="utf-8-sig")
    except Exception as exc:
        return f"error-graph gate unreadable ({type(exc).__name__})"
    missing = [m for m in GATE_FLAG_MARKERS if m not in source]
    if missing:
        return "error-graph gate does not gate on error_graph_runtime/ENABLED (missing %s)" % ", ".join(missing)
    return None


def _handlers(config: dict[str, Any], event: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    groups = config.get("hooks", {}).get(event, [])
    if not isinstance(groups, list):
        return out
    for group in groups:
        if not isinstance(group, dict):
            continue
        hooks = group.get("hooks", [])
        if isinstance(hooks, list):
            out.extend(h for h in hooks if isinstance(h, dict))
    return out


# 2026-10-30 (Mac): everything above is the Windows hook-host contract (commandWindows, C:\Python314, the
# ~/.brain-framework/runtime layout, one error-graph observer per event). On macOS Codex runs `command`, the
# project-memory hooks live in ~/.claude/dreaming and the error graph is not enabled, so the Windows contract
# reported 50 failures against a correct install. Off Windows the contract is: every event has handlers, each
# handler is a positive-timeout command whose scripts exist and compile, and the core hooks sit on their events.
POSIX_CORE = {
    "UserPromptSubmit": (REFLEX_NAME, STOP_GUARD_NAME, PROJECT_MEMORY_NAME),
    "PreToolUse": (REFLEX_NAME, HELP_GUARD_NAME, PROJECT_MEMORY_HOT_NAME),
    "PostToolUse": (),
    "Stop": (STOP_GUARD_NAME,),
}


def validate_posix(config: Any) -> list[str]:
    problems: list[str] = []
    if not isinstance(config, dict) or not isinstance(config.get("hooks"), dict):
        return ["top-level hooks object missing or invalid"]
    for event, core in POSIX_CORE.items():
        handlers = _handlers(config, event)
        if not handlers:
            problems.append(f"{event}: no handlers")
            continue
        seen: set[str] = set()
        for index, handler in enumerate(handlers):
            command = str(handler.get("command") or "")
            if handler.get("type") != "command":
                problems.append(f"{event}[{index}]: handler type must be command")
            timeout = handler.get("timeout")
            if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
                problems.append(f"{event}[{index}]: timeout must be positive")
            if not command:
                problems.append(f"{event}[{index}]: command missing")
                continue
            try:
                argv = shlex.split(command)
            except ValueError:
                problems.append(f"{event}[{index}]: command does not parse")
                continue
            for arg in argv:
                if not arg.endswith(".py"):
                    continue
                seen.add(os.path.basename(arg))
                try:
                    compile(Path(arg).read_text(encoding="utf-8-sig"), arg, "exec")
                except Exception as exc:  # noqa: BLE001
                    problems.append(f"{event}[{index}]: script missing or invalid: {arg} ({type(exc).__name__})")
        for name in core:
            if name not in seen:
                problems.append(f"{event}: core hook {name} missing")
    return problems


def _selftest_posix() -> int:
    d = Path.home() / ".claude" / "dreaming"
    py = sys.executable

    def h(name: str) -> dict[str, Any]:
        return {"type": "command", "timeout": 30, "command": "%s -B %s" % (py, d / name)}

    good = {"hooks": {"UserPromptSubmit": [{"hooks": [h(REFLEX_NAME), h(STOP_GUARD_NAME), h(PROJECT_MEMORY_NAME)]}],
                      "PreToolUse": [{"matcher": "*", "hooks": [h(REFLEX_NAME), h(HELP_GUARD_NAME), h(PROJECT_MEMORY_HOT_NAME)]}],
                      "PostToolUse": [{"matcher": "*", "hooks": [h("live_inbox_hook.py")]}],
                      "Stop": [{"hooks": [h(STOP_GUARD_NAME)]}]}}
    failures = []
    if validate_posix(good):
        failures.append("known-good POSIX contract rejected: %s" % validate_posix(good))
    no_stop = copy.deepcopy(good)
    no_stop["hooks"]["Stop"] = []
    gone = copy.deepcopy(good)
    gone["hooks"]["PostToolUse"][0]["hooks"][0]["command"] = "%s -B %s" % (py, d / "no_such_hook_xyz.py")
    zero = copy.deepcopy(good)
    zero["hooks"]["Stop"][0]["hooks"][0]["timeout"] = 0
    no_reflex = copy.deepcopy(good)
    no_reflex["hooks"]["PreToolUse"][0]["hooks"].pop(0)
    for label, cfg, want in (("missing Stop", no_stop, "Stop: no handlers"),
                             ("missing script", gone, "script missing or invalid"),
                             ("zero timeout", zero, "timeout must be positive"),
                             ("missing core reflex", no_reflex, "core hook %s missing" % REFLEX_NAME)):
        if not any(want in p for p in validate_posix(cfg)):
            failures.append("%s not rejected" % label)
    for f in failures:
        print("FAIL", f)
    print("POSIX selftest: %d failure(s)" % len(failures))
    return 1 if failures else 0


def validate(config: Any) -> list[str]:
    if os.name != "nt":
        return validate_posix(config)
    problems: list[str] = []
    if not isinstance(config, dict) or not isinstance(config.get("hooks"), dict):
        return ["top-level hooks object missing or invalid"]
    for script in tuple(EXPECTED_SCRIPTS.values()) + SUPPORT_SCRIPTS:
        try:
            source = Path(_RAW_SCRIPT_PATHS.get(script, script)).read_text(encoding="utf-8-sig")
            compile(source, script, "exec")
        except Exception as exc:
            problems.append(f"expected hook script missing or invalid: {ntpath.basename(script)} ({type(exc).__name__})")
    for event in EVENTS:
        groups = config.get("hooks", {}).get(event, [])
        if isinstance(groups, list):
            for group_index, group in enumerate(groups):
                if not isinstance(group, dict):
                    continue
                matcher = group.get("matcher")
                if matcher in (None, "*"):
                    continue
                group_hooks = group.get("hooks", [])
                scoped_hot = (
                    event == "PreToolUse"
                    and isinstance(group_hooks, list)
                    and len(group_hooks) == 1
                    and isinstance(group_hooks[0], dict)
                    and any(name in SCOPED_MATCHER_SCRIPTS for name in
                            _script_basenames(str(group_hooks[0].get("commandWindows") or "")))
                )
                if not scoped_hot:
                    problems.append(
                        f"{event}[group {group_index}]: non-wildcard matcher may scope only "
                        "the project-memory hot hook or the push reminder"
                    )
        handlers = _handlers(config, event)
        if not handlers:
            problems.append(f"{event}: no handlers")
            continue
        commands = [str(h.get("commandWindows") or "") for h in handlers]
        strong_count = sum(
            any(_is_strong_nodes_script(s) for s in _script_tokens(command))
            for command in commands)
        if strong_count > (1 if event == STRONG_NODES_EVENT else 0):
            problems.append(f"{event}: unexpected strong-nodes fire handler")
        core_count = len(handlers) - strong_count
        if core_count != EXPECTED_HANDLER_COUNTS[event]:
            problems.append(
                f"{event}: expected {EXPECTED_HANDLER_COUNTS[event]} core handlers, found {core_count}"
            )
        for index, (handler, command) in enumerate(zip(handlers, commands)):
            if handler.get("type") != "command":
                problems.append(f"{event}[{index}]: handler type must be command")
            timeout = handler.get("timeout")
            if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
                problems.append(f"{event}[{index}]: timeout must be positive")
            if not command:
                problems.append(f"{event}[{index}]: commandWindows missing")
            elif command.lstrip().startswith('"'):
                problems.append(f"{event}[{index}]: quoted executable is rejected by the Windows hook host")
            elif not ntpath.isabs(command.split(" ", 1)[0]):
                problems.append(f"{event}[{index}]: commandWindows does not start with an absolute executable")
            elif not os.path.isfile(command.split(" ", 1)[0]):
                problems.append(f"{event}[{index}]: commandWindows executable does not exist")
            else:
                shape_problem = _command_shape_problem(command)
                if shape_problem:
                    problems.append(f"{event}[{index}]: {shape_problem}")
            for script in _script_tokens(command):
                if script in EXPECTED_SCRIPTS.values() and not os.path.isfile(script):
                    problems.append(f"{event}[{index}]: expected hook script does not exist")
                elif _is_strong_nodes_script(script) and not os.path.isfile(script):
                    problems.append(f"{event}[{index}]: strong-nodes fire hook script does not exist")
        command_scripts = [_script_tokens(command) for command in commands]
        reflex_count = sum(EXPECTED_SCRIPTS[REFLEX_NAME] in scripts for scripts in command_scripts)
        expected = 1 if event in REFLEX_EVENTS else 0
        if reflex_count != expected:
            problems.append(f"{event}: expected {expected} advisory reflex handler, found {reflex_count}")
        stop_guard_count = sum(EXPECTED_SCRIPTS[STOP_GUARD_NAME] in scripts for scripts in command_scripts)
        expected_guard = 1 if event in STOP_GUARD_EVENTS else 0
        if stop_guard_count != expected_guard:
            problems.append(
                f"{event}: expected {expected_guard} completion/timed-work guard, found {stop_guard_count}"
            )
        graph_count = sum((EXPECTED_SCRIPTS[GRAPH_NAME] in scripts) or (EXPECTED_SCRIPTS[GATE_NAME] in scripts)
                          for scripts in command_scripts)
        if graph_count != 1:
            problems.append(f"{event}: expected 1 shadow error-graph observer, found {graph_count}")
        if any(EXPECTED_SCRIPTS[GATE_NAME] in scripts for scripts in command_scripts):
            gate_problem = _gate_problem()
            if gate_problem:
                problems.append(f"{event}: {gate_problem}")
        for support_name in (HELP_GUARD_NAME, PROJECT_MEMORY_NAME, PROJECT_MEMORY_HOT_NAME):
            actual_support = sum(
                support_name in _script_basenames(command) for command in commands
            )
            expected_support = SUPPORT_EVENT_COUNTS[event].get(support_name, 0)
            if actual_support != expected_support:
                problems.append(
                    f"{event}: expected {expected_support} {support_name} handler, "
                    f"found {actual_support}"
                )
    return problems


def _sample_config() -> dict[str, Any]:
    """Small, platform-realistic contract fixture; commands never execute."""
    python = r"C:\Python314\python.exe"
    # The check compares exact absolute script paths and never expands "~", so the fixture spells the home out the
    # way a real hooks.json does; a literal "~\" here made every core hook read as unapproved.
    home = os.path.expanduser("~")
    graph = python + " -B " + home + r"\.claude\dreaming\error_graph\hook.py --hook-runtime codex"
    reflex = python + " -B " + home + r"\.claude\dreaming\codex_reflex_hook.py"
    stop = python + " -B " + home + r"\.claude\dreaming\codex_stop_guard.py"
    help_guard = python + " -B " + home + r"\.claude\dreaming\codex_help_guard_hook.py"
    project_memory = python + " -B " + home + r"\.brain-framework\runtime\project_memory_hook.py"
    project_memory_hot = (
        python + " -S -B " + home + r"\.brain-framework\runtime\project_memory_hot_hook.py"
    )

    dreaming = os.path.expanduser(r"~/.claude/dreaming")
    advisory = {name: python + " -B " + path for name, path in (
        ("proof", dreaming + r"\proof_sense.py"),
        ("math", dreaming + r"\math_sense.py"),
        ("waste", dreaming + r"\waste_sense.py"),
        ("push", dreaming + r"\push_reminder.py"),
        ("movie", os.path.expanduser(r"~/.claude/movie_sentinel/movie_alert_hook.py")),
        ("prime", dreaming + r"\instinct_prime.py"),
        ("compute", dreaming + r"\compute_sense.py"),
        ("danger", dreaming + r"\danger_mode.py"),
        ("slow", dreaming + r"\slow_sense.py"),
        ("handoff", os.path.expanduser(r"~/.codex/restart_handoff_hook.py")),
        ("summary", dreaming + r"\summary_hook.py"),
        ("voice", dreaming + r"\voice_guard_hook.py"),
    )}
    advisory["ownerprompts"] = python + " -B " + dreaming + r"\owner_prompts_hook.py --hook --runtime codex"
    advisory["checklist"] = python + " -B " + dreaming + r"\task_checklist_hook.py --hook --runtime codex"
    advisory["archive"] = python + " -B " + dreaming + r"\prompt_archive.py --runtime codex"
    # The board hook is counted in EXPECTED_HANDLER_COUNTS (18), so the fixture carries it too. Appended last on
    # purpose: the known-bad cases below index into this list by position.
    advisory["board"] = python + " -B " + dreaming + r"\owner_board_hook.py --hook --runtime codex"

    def handler(command: str) -> dict[str, Any]:
        return {"type": "command", "commandWindows": command, "timeout": 2}

    return {"hooks": {
        "UserPromptSubmit": [{
            "hooks": [handler(graph), handler(reflex), handler(project_memory), handler(stop)]
            + [handler(advisory[k]) for k in ("proof", "math", "waste", "push", "movie", "prime",
                                              "compute", "danger", "slow", "handoff",
                                              "ownerprompts", "checklist", "archive", "board")]
        }],
        "PreToolUse": [
            {"matcher": "*", "hooks": [handler(help_guard), handler(graph), handler(reflex)]},
            {"matcher": "Write|Edit", "hooks": [handler(project_memory_hot)]},
            {"matcher": "Bash|PowerShell|shell_command|exec_command", "hooks": [handler(advisory["push"])]},
            {"matcher": "*", "hooks": [handler(advisory["voice"])]},
        ],
        "PostToolUse": [{"hooks": [handler(graph)]}],
        "Stop": [{"hooks": [handler(graph), handler(stop), handler(advisory["summary"]), handler(advisory["checklist"])]}],
    }}


def _selftest() -> int:
    if os.name != "nt":
        return _selftest_posix()
    failures: list[str] = []
    good = _sample_config()
    if validate(good):
        failures.append("known-good contract rejected")

    cases: list[tuple[str, dict[str, Any], str]] = []
    quoted = copy.deepcopy(good)
    quoted["hooks"]["PostToolUse"][0]["hooks"][0]["commandWindows"] = '"C:\\Python314\\python.exe" -B x.py'
    cases.append(("quoted executable", quoted, "quoted executable"))
    relative = copy.deepcopy(good)
    relative["hooks"]["PostToolUse"][0]["hooks"][0]["commandWindows"] = "python.exe -B x.py"
    cases.append(("relative executable", relative, "absolute executable"))
    misplaced = copy.deepcopy(good)
    del misplaced["hooks"]["PreToolUse"][0]["hooks"][2]
    cases.append(("missing PreToolUse reflex", misplaced, "expected 1 advisory reflex handler, found 0"))
    duplicate = copy.deepcopy(good)
    duplicate["hooks"]["Stop"][0]["hooks"].append(copy.deepcopy(duplicate["hooks"]["Stop"][0]["hooks"][1]))
    cases.append(("duplicate Stop guard", duplicate, "expected 1 completion/timed-work guard, found 2"))
    no_graph = copy.deepcopy(good)
    no_graph["hooks"]["PostToolUse"][0]["hooks"].clear()
    cases.append(("missing observer", no_graph, "PostToolUse: no handlers"))
    wrong_path = copy.deepcopy(good)
    wrong_path["hooks"]["PreToolUse"][0]["hooks"][2]["commandWindows"] = r"C:\Python314\python.exe -B C:\tmp\codex_reflex_hook.py"
    cases.append(("same basename wrong path", wrong_path, "expected 1 advisory reflex handler, found 0"))
    disabled = copy.deepcopy(good)
    disabled["hooks"]["PreToolUse"][0]["hooks"][2]["commandWindows"] += ".disabled"
    cases.append(("disabled suffix decoy", disabled, "expected 1 advisory reflex handler, found 0"))
    wrong_type = copy.deepcopy(good)
    wrong_type["hooks"]["PostToolUse"][0]["hooks"][0]["type"] = "prompt"
    cases.append(("wrong handler type", wrong_type, "handler type must be command"))
    zero_timeout = copy.deepcopy(good)
    zero_timeout["hooks"]["PostToolUse"][0]["hooks"][0]["timeout"] = 0
    cases.append(("zero timeout", zero_timeout, "timeout must be positive"))
    narrow_matcher = copy.deepcopy(good)
    narrow_matcher["hooks"]["PreToolUse"][0]["matcher"] = "Write"
    cases.append(("narrow warning matcher", narrow_matcher, "non-wildcard matcher may scope only"))
    advisory_extra = copy.deepcopy(good)
    advisory_extra["hooks"]["Stop"][0]["hooks"][2]["commandWindows"] += " --quiet"
    cases.append(("advisory hook with an argument", advisory_extra, "advisory hook has unexpected extra arguments"))
    args_form = copy.deepcopy(good)
    args_form["hooks"]["Stop"][0]["hooks"][2] = {"type": "command", "command": r"C:\Python314\python.exe",
                                                 "args": ["-B", "x.py"], "timeout": 2}
    cases.append(("Claude args form never runs under Codex", args_form, "commandWindows missing"))
    doubled_observer = copy.deepcopy(good)
    doubled_observer["hooks"]["UserPromptSubmit"][0]["hooks"][7]["commandWindows"] = (
        doubled_observer["hooks"]["UserPromptSubmit"][0]["hooks"][0]["commandWindows"])
    cases.append(("push reminder carrying the observer's command", doubled_observer,
                  "expected 1 shadow error-graph observer, found 2"))
    missing_exe = copy.deepcopy(good)
    missing_exe["hooks"]["PostToolUse"][0]["hooks"][0]["commandWindows"] = missing_exe["hooks"]["PostToolUse"][0]["hooks"][0]["commandWindows"].replace(
        r"C:\Python314\python.exe", r"C:\definitely-missing\python.exe")
    cases.append(("missing executable", missing_exe, "commandWindows executable does not exist"))
    wrong_exe = copy.deepcopy(good)
    wrong_exe["hooks"]["PostToolUse"][0]["hooks"][0]["commandWindows"] = wrong_exe["hooks"]["PostToolUse"][0]["hooks"][0]["commandWindows"].replace(
        r"C:\Python314\python.exe", r"C:\Windows\System32\cmd.exe")
    cases.append(("wrong executable", wrong_exe, "active trusted Python executable"))
    extra_arg = copy.deepcopy(good)
    extra_arg["hooks"]["PreToolUse"][0]["hooks"][2]["commandWindows"] += " --ignored"
    cases.append(("unexpected wrapper argument", extra_arg, "unexpected extra arguments"))
    missing_project_memory = copy.deepcopy(good)
    del missing_project_memory["hooks"]["UserPromptSubmit"][0]["hooks"][2]
    cases.append((
        "missing project-memory submit hook",
        missing_project_memory,
        "expected 1 project_memory_hook.py handler, found 0",
    ))
    duplicate_help = copy.deepcopy(good)
    duplicate_help["hooks"]["PreToolUse"][0]["hooks"].append(
        copy.deepcopy(duplicate_help["hooks"]["PreToolUse"][0]["hooks"][0])
    )
    cases.append((
        "duplicate help guard",
        duplicate_help,
        "expected 1 codex_help_guard_hook.py handler, found 2",
    ))
    hot_without_isolation = copy.deepcopy(good)
    hot_command = hot_without_isolation["hooks"]["PreToolUse"][1]["hooks"][0]["commandWindows"]
    hot_without_isolation["hooks"]["PreToolUse"][1]["hooks"][0]["commandWindows"] = (
        hot_command.replace(" -S -B ", " -B ")
    )
    cases.append((
        "hot hook without isolated imports",
        hot_without_isolation,
        "project-memory hot hook must use Python -S -B",
    ))
    live_strong = sorted(
        p for p in (Path.home() / ".claude" / "dreaming" / "strong_nodes" / "runtime").glob("*/hook.py")
        if _STRONG_NODES_HASH.match(ntpath.normcase(p.parent.name)))
    if live_strong:
        strong_cmd = (r"C:\Python314\python.exe -B " + str(live_strong[-1])
                      + " --hook-runtime codex --enabled "
                      + str(Path.home() / ".claude" / "dreaming" / "strong_nodes" / "ENABLED"))
        staged = copy.deepcopy(good)
        staged["hooks"]["PreToolUse"][0]["hooks"].append(
            {"type": "command", "commandWindows": strong_cmd, "timeout": 5})
        staged_problems = validate(staged)
        if staged_problems:
            failures.append(f"staged strong-nodes handler rejected: {staged_problems!r}")
        wrong_flag = copy.deepcopy(staged)
        wrong_flag["hooks"]["PreToolUse"][0]["hooks"][-1]["commandWindows"] = strong_cmd.replace(
            " --enabled ", " --enable ")
        cases.append(("strong-nodes wrong arguments", wrong_flag, "strong-nodes fire hook must end with"))
        stray_strong = copy.deepcopy(good)
        stray_strong["hooks"]["Stop"][0]["hooks"].append(
            {"type": "command", "commandWindows": strong_cmd, "timeout": 5})
        cases.append(("strong-nodes outside PreToolUse", stray_strong, "unexpected strong-nodes fire handler"))
    # THE GATED OBSERVER: the gate in place of the raw hook on all four events must pass; the gate beside the raw hook is
    # two observers; the gate without its runtime argument is malformed; and a gate whose source no longer gates on
    # error_graph_runtime/ENABLED must fail - a decoy source proves it can.
    gate_cmd = good["hooks"]["PostToolUse"][0]["hooks"][0]["commandWindows"].replace(
        "error_graph\\hook.py", "error_graph_gate.py")
    gated = copy.deepcopy(good)
    for ev, gi, hi in (("UserPromptSubmit", 0, 0), ("PreToolUse", 0, 1), ("PostToolUse", 0, 0), ("Stop", 0, 0)):
        gated["hooks"][ev][gi]["hooks"][hi]["commandWindows"] = gate_cmd
    gated_problems = validate(gated)
    if gated_problems:
        failures.append(f"gated observer contract rejected: {gated_problems!r}")
    both = copy.deepcopy(gated)
    both["hooks"]["PostToolUse"][0]["hooks"].append(copy.deepcopy(good["hooks"]["PostToolUse"][0]["hooks"][0]))
    cases.append(("gate and raw observer on one event", both, "expected 1 shadow error-graph observer, found 2"))
    gate_args = copy.deepcopy(gated)
    gate_args["hooks"]["Stop"][0]["hooks"][0]["commandWindows"] = gate_cmd.replace(" --hook-runtime codex", "")
    cases.append(("gate without its runtime argument", gate_args, "error-graph command must end with --hook-runtime codex"))
    import tempfile
    global GATE_SOURCE
    saved_gate = GATE_SOURCE
    with tempfile.TemporaryDirectory() as td:
        decoy = Path(td) / "error_graph_gate.py"
        decoy.write_text("import runpy\nrunpy.run_path('hook.py', run_name='__main__')\n", encoding="utf-8")
        GATE_SOURCE = str(decoy)
        try:
            ungated = validate(gated)
        finally:
            GATE_SOURCE = saved_gate
    if not any("does not gate on error_graph_runtime/ENABLED" in p for p in ungated):
        failures.append(f"a gate that no longer gates was accepted: {ungated!r}")
    null_hooks: dict[str, Any] = {"hooks": None}
    cases.append(("invalid top level", null_hooks, "top-level hooks object missing or invalid"))

    for name, config, expected in cases:
        found = validate(config)
        if not any(expected in problem for problem in found):
            failures.append(f"{name} was not rejected as expected: {found!r}")
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stderr)
        return 1
    print("PASS: codex_warning_acceptance selftest")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "hooks_json",
        nargs="?",
        type=Path,
        default=Path.home() / ".codex" / "hooks.json",
        help="Codex hooks.json to validate (default: ~/.codex/hooks.json)",
    )
    parser.add_argument(
        "--skip-wrapper-selftest",
        action="store_true",
        help="Do not run the reflex and Stop wrapper selftests",
    )
    parser.add_argument(
        "--selftest",
        action="store_true",
        help="Exercise known-good and known-bad hook contracts without reading live config",
    )
    args = parser.parse_args()
    if args.selftest:
        return _selftest()
    try:
        config = json.loads(args.hooks_json.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"FAIL: cannot read {args.hooks_json}: {exc}", file=sys.stderr)
        return 1
    problems = validate(config)
    if not args.skip_wrapper_selftest:
        for name in (REFLEX_NAME, STOP_GUARD_NAME):
            wrapper = Path.home() / ".claude" / "dreaming" / name
            try:
                proc = subprocess.run(
                    [sys.executable, "-B", str(wrapper), "--selftest"],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if proc.returncode != 0 or "PASS" not in proc.stdout:
                    problems.append(
                        f"{name} selftest failed: {proc.stdout.strip()} {proc.stderr.strip()}".strip()
                    )
            except Exception as exc:
                problems.append(f"{name} selftest could not run: {exc}")
    if problems:
        for problem in problems:
            print(f"FAIL: {problem}", file=sys.stderr)
        return 1
    print(f"PASS: Codex warning hook contract ({args.hooks_json})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
