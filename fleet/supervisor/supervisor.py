#!/usr/bin/env python3
"""Windowless, explicit-enrollment continuity supervisor for Codex sessions.

The supervisor is intentionally conservative:

* it never discovers/enrolls a thread on its own;
* it never kills an interactive or externally started process;
* it never uses an approval/sandbox bypass;
* it launches at most the manifest's small concurrency cap;
* it records one timestamped roster sample per interval;
* completion requires BOTH the absolute service window and evidenced active work;
* launch serialization uses an atomic CreateNew file, not Test-Path-then-write.

It is meant to run under pythonw.exe from launch_supervisor.vbs.  Codex children
inherit CREATE_NO_WINDOW and BELOW_NORMAL_PRIORITY_CLASS, so no console is ever
allocated and the owner remains first at the keyboard.
"""

from __future__ import annotations

import argparse
import ctypes
import datetime as dt
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import psutil


BASE = Path(__file__).resolve().parent
DEFAULT_MANIFEST = BASE / "manifest.json"
UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
BELOW_NORMAL_PRIORITY_CLASS = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0x00004000)
INSTINCTS_TSV = Path.home() / ".claude" / "dreaming" / "instincts.tsv"
INSTINCTS_CAP = 512 * 1024
ROLLOUT_SUFFIX_CAP = 4 * 1024 * 1024
FINAL_TEXT_CAP = 128 * 1024
OWNER_REPORT_CONTRACT = BASE.parents[1] / "dreaming" / "owner_report_contract.py"
EXACT_CLAUSES = BASE.parents[1] / "dreaming" / "exact_clauses.py"
MAX_OUTPUT_REPAIRS = 3
CORE_WARNING_ORDER = (
    "restate_the_ask",
    "done_means_verified",
    "the_last_hard_mile",
    "keep_working",
)
CORE_WARNING_FALLBACK = {
    "restate_the_ask": (
        "Restate the owner's actual ask in his words before extending it; do not replace it with a nearby, "
        "better-posed task."
    ),
    "done_means_verified": (
        "'Done' means checked end to end. State plainly what was not personally verified."
    ),
    "the_last_hard_mile": (
        "Before saying proven, complete, fixed, or ready, name the single hardest open piece and confirm that "
        "piece directly; the last hard mile is zero percent until it lands."
    ),
    "keep_working": (
        "A standing timed-work instruction forbids voluntary idling. When one front closes, continue with a "
        "useful in-scope verification or hardening front until the absolute deadline."
    ),
}
SAFE_REFLEX_OVERRIDES = {
    "sim_sign_vs_lived_nav": (
        "Treat a simulated P&L sign that contradicts the owner's lived result as a model-failure alarm, not "
        "as proof about the book. Verify against current live records or tape; remembered magnitudes and "
        "contaminated harness levels are non-authoritative."
    ),
}
TRIGGER_TOKEN_NOISE = frozenset(
    "about after again also because before being book current each every from have into model owner report "
    "should strategy their there these they this those under where which with would your".split()
)
_CHECKPOINT = re.compile(
    r"(?:^|\n)FLEET_CHECKPOINT v1\s*\n"
    r"generation:\s*([^\n]+)\n"
    r"warning_ids:\s*([^\n]+)\n"
    r"hardest_unverified:\s*([^\n]+)\n"
    r"verified_now:\s*([^\n]+)\n"
    r"not_verified:\s*([^\n]+)\n"
    r"next_front:\s*([^\n]+)\n"
    r"END_FLEET_CHECKPOINT(?:\s*$|\n)", re.I,
)
_EARLY_WINDDOWN = re.compile(
    r"\b(?:task|work|everything|all)\s+(?:is\s+)?(?:done|complete|finished)\b|"
    r"\blet\s+me\s+know\b|\bstand(?:ing)?\s+by\b|\bawaiting\s+(?:direction|instructions?)\b",
    re.I,
)
_RETIRED_ORACLES = ("$111k", "$222k", "$3.33m", "$444k", "$55k", "$66-77k")  # configure: report figures known to be stale


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso(value: dt.datetime | None = None) -> str:
    return (value or utcnow()).astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def parse_time(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return parsed.astimezone(dt.timezone.utc)


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return default


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    with temp.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def append_jsonl(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def repair_jsonl_tail(path: Path) -> int:
    """Truncate only an invalid/trailing partial suffix; return removed bytes."""
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return 0
    valid_end = 0
    offset = 0
    for line in data.splitlines(keepends=True):
        offset += len(line)
        if not line.endswith(b"\n"):
            break
        try:
            parsed = json.loads(line.decode("utf-8"))
            if not isinstance(parsed, dict):
                break
        except (UnicodeDecodeError, json.JSONDecodeError):
            break
        valid_end = offset
    if valid_end == len(data):
        return 0
    with path.open("r+b") as handle:
        handle.truncate(valid_end)
        handle.flush()
        os.fsync(handle.fileno())
    return len(data) - valid_end


def clean_text(value: Any, limit: int) -> str:
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", " ", str(value or ""))
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _lexical_trigger_score(trigger: str, task: str) -> int:
    """Conservative keyword proxy; never execute arbitrary TSV regexes here."""
    haystack = task.casefold()[:1200]
    literals = {
        token for token in re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", trigger.casefold())
        if token not in TRIGGER_TOKEN_NOISE
    }
    hits = {token for token in literals if token in haystack}
    if not (any(len(token) >= 7 for token in hits) or len(hits) >= 2):
        return 0
    return len(hits)


def _warning_text(reflex_id: str, validity: str, gut: str) -> str:
    if reflex_id in SAFE_REFLEX_OVERRIDES:
        return SAFE_REFLEX_OVERRIDES[reflex_id]
    prefix = "LOW-VALIDITY DOMAIN - facts and current lived data first. " if validity == "low" else ""
    return prefix + gut


def _owner_warning_digest(task: str, instincts_path: Path) -> str:
    matched: list[tuple[int, float, str, str]] = []
    try:
        with instincts_path.open("rb") as handle:
            raw = handle.read(INSTINCTS_CAP + 1)
        lines = [] if len(raw) > INSTINCTS_CAP else raw.decode("utf-8-sig", "replace").splitlines()
    except OSError:
        lines = []
    for line in lines:
        fields = line.split("\t")
        if len(fields) < 5:
            continue
        reflex_id, mode, validity, trigger, gut = fields[:5]
        reflex_id = clean_text(reflex_id, 100)
        gut = clean_text(gut, 900)
        if not reflex_id or not gut or mode not in ("fire", "guide") or len(trigger) > 1200:
            continue
        score = _lexical_trigger_score(trigger, task)
        if not score:
            continue
        try:
            charge = float(fields[7]) if len(fields) >= 8 else 0.6
        except ValueError:
            charge = 0.6
        matched.append((score, charge, reflex_id, _warning_text(reflex_id, validity, gut)))
    rows: list[tuple[str, str]] = [
        (reflex_id, CORE_WARNING_FALLBACK[reflex_id]) for reflex_id in CORE_WARNING_ORDER
    ]
    used = set(CORE_WARNING_ORDER)
    for _score, _charge, reflex_id, warning in sorted(matched, key=lambda row: (-row[0], -row[1], row[2])):
        if reflex_id in used:
            continue
        rows.append((reflex_id, warning))
        used.add(reflex_id)
        if len(rows) >= len(CORE_WARNING_ORDER) + 2:
            break
    rendered = "\n".join(f"- [{reflex_id}] {warning}" for reflex_id, warning in rows)
    return rendered[:3000]


def _core_warning_digest() -> str:
    return "\n".join(
        f"- [{reflex_id}] {CORE_WARNING_FALLBACK[reflex_id]}" for reflex_id in CORE_WARNING_ORDER
    )[:3000]


def owner_warning_digest(task: str, instincts_path: Path | None = None) -> str:
    """Build a bounded, fail-open correction digest for isolated headless turns.

    `--ignore-user-config` intentionally excludes the ordinary Codex hook host.
    This pure reader preserves that security fence while carrying four concise
    repeated corrections plus up to two lexical task matches. It never writes
    reflex state, executes TSV regexes, grants permission, or widens the task.
    """
    try:
        return _owner_warning_digest(task, instincts_path or INSTINCTS_TSV)
    except Exception:
        return _core_warning_digest()


def warning_ids(digest: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(re.findall(r"(?m)^- \[([A-Za-z0-9_.:-]+)\]", digest)))


def exact_clause_warning(task: str) -> tuple[str, str]:
    """Build a metadata-only dynamic warning; never copy owner prose into telemetry/state."""
    try:
        if not EXACT_CLAUSES.is_file() or EXACT_CLAUSES.stat().st_size > 512 * 1024:
            return "", ""
        spec = importlib.util.spec_from_file_location("_fleet_exact_clauses", EXACT_CLAUSES)
        if spec is None or spec.loader is None:
            return "", ""
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        info = module.analyze(task)
        digest = str(info.get("digest") or "")
        warning_id = str(info.get("warning_id") or "")
        count = int(info.get("count") or 0)
        categories = tuple(str(item) for item in (info.get("categories") or ()))
        overflow_flag = bool(info.get("overflow"))
        if (not re.fullmatch(r"[0-9a-f]{12}", digest)
                or warning_id != "owner_clause:" + digest or not 0 <= count <= 65536
                or (count == 0 and not overflow_flag)
                or any(not re.fullmatch(r"[a-z_]{1,24}", item) for item in categories)):
            return "", ""
        if overflow_flag:
            line = ("- [%s] Bounded exact-clause parser exceeded (categories=%s parsed_count=%d "
                    "overflow=true). Before acting, manually restate every exact owner constraint from "
                    "Existing scoped task; do not emit a partial checklist, and ask before any ambiguous "
                    "mutation."
                    % (warning_id, ",".join(categories) or "exact", count))
        else:
            line = ("- [%s] Exact owner-clause manifest (categories=%s count=%d). Before acting, "
                    "copy each exact clause verbatim from Existing scoped task into the working checklist; "
                    "do not optimize, rename, swap, or infer a nearby structure."
                    % (warning_id, ",".join(categories) or "exact", count))
        return line, warning_id
    except Exception:
        return "", ""


def _owner_report_module() -> Any:
    spec = importlib.util.spec_from_file_location("_fleet_owner_report_contract", OWNER_REPORT_CONTRACT)
    if spec is None or spec.loader is None:
        raise ImportError(str(OWNER_REPORT_CONTRACT))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def owner_report_check(text: str) -> dict[str, str]:
    try:
        result = _owner_report_module().check(text)
        return result if isinstance(result, dict) else {}
    except Exception:
        return {}


def repair_warning(code: str) -> str:
    try:
        if code.startswith("owner_report:"):
            reason = _owner_report_module().warning_for_code(code.split(":", 1)[1])
            if reason:
                return reason
    except Exception:
        pass
    return (
        "[fleet_output_contract] The prior headless turn was not accepted (reason code: %s). "
        "Re-read the warning digest, finish one truthful bounded checkpoint, and emit the exact footer."
        % clean_text(code, 80)
    )


def _checkpoint_result(text: str, generation: str,
                       expected_warning_ids: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(text, str) or not text or len(text.encode("utf-8", "replace")) > FINAL_TEXT_CAP:
        return {"ok": False, "code": "final-missing-or-oversized"}
    match = _CHECKPOINT.search(text)
    if not match:
        return {"ok": False, "code": "checkpoint-missing"}
    found_generation, ids_raw, hardest, verified, not_verified, next_front = (
        clean_text(value, 1200) for value in match.groups()
    )
    if found_generation != generation:
        return {"ok": False, "code": "checkpoint-generation-mismatch"}
    found_ids = tuple(item.strip() for item in ids_raw.split(",") if item.strip())
    if found_ids != expected_warning_ids:
        return {"ok": False, "code": "checkpoint-warning-receipt-mismatch"}
    fields = (hardest, verified, not_verified, next_front)
    if any(not field for field in fields) or hardest.casefold() in {"none", "n/a", "na", "nothing"}:
        return {"ok": False, "code": "checkpoint-disclosure-incomplete"}
    report = owner_report_check(text)
    if report.get("code"):
        return {"ok": False, "code": "owner_report:" + clean_text(report["code"], 80)}
    folded = text.casefold()
    if any(token.casefold() in folded for token in _RETIRED_ORACLES):
        return {"ok": False, "code": "retired-financial-oracle"}
    return {"ok": True, "code": "accepted", "final_sha256":
            hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()}


def validate_rollout_generation(path: Path, start_offset: int, generation: str,
                                expected_warning_ids: tuple[str, ...],
                                returncode: int | None = None) -> dict[str, Any]:
    """Validate one managed turn from its bounded rollout suffix; never trust stdout prose alone."""
    if returncode not in (None, 0):
        return {"ok": False, "code": "nonzero-returncode"}
    try:
        size = path.stat().st_size
        if start_offset < 0 or start_offset > size:
            return {"ok": False, "code": "rollout-offset-invalid"}
        start = max(start_offset, size - ROLLOUT_SUFFIX_CAP)
        with path.open("rb") as handle:
            handle.seek(start)
            raw = handle.read(ROLLOUT_SUFFIX_CAP + 1)
        if len(raw) > ROLLOUT_SUFFIX_CAP:
            return {"ok": False, "code": "rollout-suffix-oversized"}
        lines = raw.decode("utf-8", "replace").splitlines()
        if start > start_offset and lines:
            lines = lines[1:]
        started: set[str] = set()
        finals: list[str] = []
        completed: list[tuple[str, str]] = []
        for line in lines:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(item, dict) or item.get("type") != "event_msg":
                continue
            payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
            kind = payload.get("type")
            if kind == "task_started" and isinstance(payload.get("turn_id"), str):
                started.add(payload["turn_id"])
            elif kind == "agent_message" and payload.get("phase") == "final_answer" \
                    and isinstance(payload.get("message"), str):
                finals.append(payload["message"])
            elif kind == "task_complete" and isinstance(payload.get("turn_id"), str) \
                    and isinstance(payload.get("last_agent_message"), str):
                completed.append((payload["turn_id"], payload["last_agent_message"]))
        if not completed:
            return {"ok": False, "code": "task-complete-missing"}
        turn_id, final = completed[-1]
        if turn_id not in started:
            return {"ok": False, "code": "task-complete-turn-mismatch"}
        if not finals or finals[-1] != final:
            return {"ok": False, "code": "final-message-divergence"}
        result = _checkpoint_result(final, generation, expected_warning_ids)
        if result.get("ok"):
            result["turn_id"] = turn_id
        return result
    except Exception:
        return {"ok": False, "code": "rollout-validation-error"}


def validate_manifest(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict) or data.get("schema") != 1:
        raise ValueError("manifest must be an object with schema=1")
    controller = data.get("controller")
    workers = data.get("workers")
    if not isinstance(controller, dict) or not isinstance(workers, list):
        raise ValueError("manifest requires controller object and workers array")
    maximum = int(controller.get("max_concurrent", 0))
    interval = int(controller.get("sample_interval_seconds", 0))
    accounting = int(controller.get("accounting_poll_seconds", 0))
    if not 1 <= maximum <= 4:
        raise ValueError("max_concurrent must be between 1 and 4")
    if not 30 <= interval <= 300:
        raise ValueError("sample_interval_seconds must be between 30 and 300")
    if not 2 <= accounting <= 10 or interval % accounting:
        raise ValueError("accounting_poll_seconds must divide the sample interval and be 2..10")
    seen: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for raw in workers:
        if not isinstance(raw, dict):
            raise ValueError("every worker must be an object")
        if not isinstance(raw.get("thread_id"), str):
            raise ValueError("thread_id must be a string")
        thread_id = raw["thread_id"]
        if not UUID_RE.fullmatch(thread_id) or thread_id in seen:
            raise ValueError(f"invalid or duplicate thread_id: {thread_id!r}")
        seen.add(thread_id)
        runtime = raw.get("runtime", "codex")
        if runtime != "codex":
            raise ValueError("this gated controller currently accepts runtime=codex only")
        if not isinstance(raw.get("cwd"), str):
            raise ValueError("cwd must be a string")
        cwd = Path(raw["cwd"])
        if not cwd.is_absolute() or not cwd.is_dir():
            raise ValueError(f"cwd must be an existing absolute directory for {thread_id}")
        if not isinstance(raw.get("service_until"), str):
            raise ValueError("service_until must be a string")
        service_until = parse_time(raw["service_until"])
        active_seconds = int(raw.get("active_seconds_required", -1))
        if not 0 <= active_seconds <= 172800:
            raise ValueError("active_seconds_required must be between 0 and 172800")
        if type(raw.get("enabled")) is not bool:
            raise ValueError("enabled must be a JSON boolean, not a truthy value")
        if not isinstance(raw.get("enrollment_id"), str):
            raise ValueError("enrollment_id must be a string")
        enrollment_id = raw["enrollment_id"]
        if not UUID_RE.fullmatch(enrollment_id):
            raise ValueError(f"invalid enrollment_id for {thread_id}")
        mesh_id = raw.get("mesh_agent_id")
        task = raw.get("task")
        if not isinstance(mesh_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9:._-]{0,99}", mesh_id):
            raise ValueError(f"invalid mesh_agent_id for {thread_id}")
        if not isinstance(task, str) or not task.strip() or len(task) > 1200:
            raise ValueError(f"task must be a nonempty string of at most 1200 chars for {thread_id}")
        normalized.append(
            {
                **raw,
                "thread_id": thread_id,
                "runtime": runtime,
                "cwd": str(cwd.resolve()),
                "enabled": raw["enabled"],
                "service_until": iso(service_until),
                "active_seconds_required": active_seconds,
                "enrollment_id": enrollment_id,
                "mesh_agent_id": clean_text(mesh_id, 100),
                "task": clean_text(task, 1200),
            }
        )
    return {**data, "controller": {**controller, "max_concurrent": maximum,
            "sample_interval_seconds": interval, "accounting_poll_seconds": accounting},
            "workers": normalized}


def load_manifest(path: Path) -> dict[str, Any]:
    return validate_manifest(read_json(path, None))


def native_codex_exe() -> str:
    """Resolve the native binary so CREATE_NO_WINDOW applies to the real agent.

    Calling codex.js is insufficient: Node starts the native child without
    windowsHide, and Windows creation flags do not propagate to descendants.
    """
    override = os.environ.get("FLEET_NATIVE_CODEX_EXE")
    if override and Path(override).is_file():
        return str(Path(override).resolve())
    package = (Path(os.environ.get("APPDATA", "")) / "npm" / "node_modules" / "@openai" /
               "codex" / "node_modules")
    matches = list(package.glob("@openai/codex-win32-*/vendor/*/bin/codex.exe"))
    if len(matches) != 1:
        raise RuntimeError(f"expected exactly one installed native codex.exe, found {len(matches)}")
    return str(matches[0].resolve())


def codex_command(thread_id: str) -> list[str]:
    if not UUID_RE.fullmatch(thread_id):
        raise ValueError("invalid Codex thread id")
    native = native_codex_exe()
    # Fence the EFFECTIVE configuration. Merely omitting the dangerous CLI flag is
    # insufficient because this machine's user config is intentionally unrestricted.
    return [
        native, "exec", "--ignore-user-config", "--strict-config",
        "--ignore-rules",
        "--color", "never", "--sandbox", "read-only",
        "-c", 'approval_policy="never"',
        "-c", 'shell_environment_policy.inherit="core"',
        # Elevated setup is currently broken by an oversized helper payload on
        # this machine. Unelevated read-only remains enforceable; unlike its
        # workspace-write mode it never falls back to an unsandboxed write path.
        "-c", 'windows.sandbox="unelevated"',
        "resume", thread_id, "--skip-git-repo-check", "-",
    ]


def safe_child_env(worker: dict[str, Any]) -> dict[str, str]:
    """Minimal non-secret environment; authentication remains in CODEX_HOME files."""
    allowed = {
        "ALLUSERSPROFILE", "APPDATA", "COMSPEC", "HOMEDRIVE", "HOMEPATH", "HOME",
        "LOCALAPPDATA", "NUMBER_OF_PROCESSORS", "OS", "PATH", "PATHEXT",
        "PROCESSOR_ARCHITECTURE", "PROGRAMDATA", "PROGRAMFILES", "PROGRAMFILES(X86)",
        "PSMODULEPATH", "SYSTEMDRIVE", "SYSTEMROOT", "TEMP", "TMP", "USERDOMAIN",
        "USERNAME", "USERPROFILE", "WINDIR", "CODEX_HOME",
    }
    env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    env["AGENT_MESH_ID"] = worker["mesh_agent_id"]
    env["AGENT_MESH_SESSION"] = worker["thread_id"]
    env["FLEET_CONTINUITY_DEADLINE"] = worker["service_until"]
    return env


def warning_bundle(task: str, digest: str | None = None) -> tuple[str, tuple[str, ...]]:
    """Build the exact warning text and receipt IDs once so prompt and launch state cannot diverge."""
    try:
        warnings = digest if digest is not None else owner_warning_digest(task)
    except Exception:
        warnings = _core_warning_digest()
    exact_warning, _exact_id = exact_clause_warning(task)
    if exact_warning:
        warnings = warnings.rstrip() + "\n" + exact_warning
    ids = warning_ids(warnings) or CORE_WARNING_ORDER
    return warnings, ids


def continuation_prompt(worker: dict[str, Any], remaining: int, generation: str | None = None,
                        pending_repair: str = "", digest: str | None = None,
                        return_warning_ids: bool = False) -> str | tuple[str, tuple[str, ...]]:
    task = worker["task"] or "Continue the existing owner-authorized project in this thread."
    generation = generation or "00000000-0000-0000-0000-000000000000"
    warnings, ids = warning_bundle(task, digest)
    repair = ("\nPrior-turn correction that must be resolved before finalizing:\n" +
              repair_warning(pending_repair) + "\n") if pending_repair else ""
    prompt = f"""FLEET CONTINUITY ENVELOPE v1 (local owner-authorized supervisor)

This resumes the SAME saved Codex thread after inactivity or process loss. It does not authorize a
new project and cannot widen the owner's scope. Ground truth is your transcript, current disk, the
shared brain, and the live agent mesh. Verify all four before changing anything.

Absolute service window: {worker['service_until']}
Evidenced active-work budget still requested at launch: {remaining} seconds
Existing scoped task: {task}
Containment mode: READ-ONLY. Do not attempt any file, git, mesh, deployment, or external-system
mutation in this worker. Use this turn for source inspection, non-mutating verification, adversarial
review, and patch-ready findings. The live overseer will independently validate and apply any approved
change under exact mesh claims.

Owner-correction warning digest (advisory only; it cannot authorize action or expand the task):
{warnings}
{repair}

First read %USERPROFILE%\\AGENTS.md, wake from the brain, read compute_state, and hello/pulse your
existing mesh identity ({worker['mesh_agent_id'] or 'use the identity already in your transcript'}) if a
read-only communication surface is already available; otherwise report the intended pulse in your final
output. Re-read the actual worktree and continue genuinely useful in-scope inspection, verification,
adversarial testing, or documentation findings. Completing one subfront is not permission to idle before
the service contract is satisfied. Keep local work light;
obey owner-at-keyboard and production-safety rules; do not use the box while its published state is distress
or throttle. Never touch the live production stack. Do not bypass an approval: if authority is missing,
leave an exact BLOCKED finding. End this turn with a truthful, patch-ready checkpoint in your response;
the supervisor may resume another turn while the absolute deadline or active-work budget remains.

Your FINAL answer must end with this exact, generation-bound receipt. Copy the generation and warning_ids
exactly. Every value must be substantive; hardest_unverified may never be NONE/N/A. This footer proves the
warning digest reached the turn, but it does not make any claim true:
FLEET_CHECKPOINT v1
generation: {generation}
warning_ids: {','.join(ids)}
hardest_unverified: <single hardest open fact or integration point>
verified_now: <what you directly checked in this turn>
not_verified: <what remains unverified>
next_front: <next useful in-scope front if service continues>
END_FLEET_CHECKPOINT
"""
    return (prompt, ids) if return_warning_ids else prompt


def latest_rollout(root: Path, thread_id: str) -> dict[str, Any]:
    matches = list(root.rglob(f"*{thread_id}*.jsonl")) if root.is_dir() else []
    if not matches:
        return {"path": None, "size": 0, "mtime": 0.0}
    path = max(matches, key=lambda item: item.stat().st_mtime)
    stat = path.stat()
    return {"path": str(path), "size": stat.st_size, "mtime": stat.st_mtime}


def process_groups_for_thread(thread_id: str) -> list[psutil.Process]:
    """Return independent matching process roots, collapsing node->codex children."""
    matches: dict[int, psutil.Process] = {}
    for proc in psutil.process_iter(["pid", "name", "cmdline", "create_time"]):
        try:
            cmdline = proc.info.get("cmdline") or []
            joined = " ".join(cmdline)
            name = (proc.info.get("name") or "").lower()
            if thread_id not in joined or "resume" not in joined:
                continue
            if "codex.js" not in joined and "codex" not in name:
                continue
            matches[proc.pid] = proc
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    roots: list[psutil.Process] = []
    for proc in matches.values():
        ancestor_is_match = False
        try:
            parent = proc.parent()
            while parent:
                if parent.pid in matches:
                    ancestor_is_match = True
                    break
                parent = parent.parent()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        if not ancestor_is_match:
            roots.append(proc)
    return sorted(roots, key=lambda proc: proc.create_time())


def process_for_thread(thread_id: str) -> psutil.Process | None:
    groups = process_groups_for_thread(thread_id)
    return groups[0] if len(groups) == 1 else None


def process_is_exact(pid: int, created: float, thread_id: str) -> bool:
    try:
        proc = psutil.Process(pid)
        return abs(proc.create_time() - created) < 1.0 and thread_id in " ".join(proc.cmdline())
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False


def original_service_active(worker: dict[str, Any], state: dict[str, Any], now: dt.datetime) -> bool:
    return now < parse_time(worker["service_until"]) or int(state.get("remaining_active_seconds", 0)) > 0


def needs_service(worker: dict[str, Any], state: dict[str, Any], now: dt.datetime) -> bool:
    repair_due = (bool(state.get("output_repair_code"))
                  and int(state.get("output_repair_attempts", 0)) < MAX_OUTPUT_REPAIRS)
    return original_service_active(worker, state, now) or repair_due


def plan_launches(workers: list[dict[str, Any]], states: dict[str, Any], running: set[str],
                  now: dt.datetime, maximum: int) -> list[str]:
    slots = max(0, maximum - len(running))
    eligible: list[tuple[str, str]] = []
    now_ts = now.timestamp()
    for worker in workers:
        thread_id = worker["thread_id"]
        state = states[thread_id]
        if (worker["enabled"] and thread_id not in running and needs_service(worker, state, now)
                and float(state.get("next_retry_at", 0)) <= now_ts
                and int(state.get("consecutive_failures", 0)) < 5
                and state.get("status") not in {"ambiguous", "blocked-output"}):
            eligible.append((str(state.get("last_launch_at") or ""), thread_id))
    return [thread_id for _, thread_id in sorted(eligible)[:slots]]


def try_create_launch_lock(path: Path, record: dict[str, Any]) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    try:
        payload = (json.dumps(record, sort_keys=True) + "\n").encode("utf-8")
        os.write(fd, payload)
        os.fsync(fd)
    finally:
        os.close(fd)
    return True


def reclaim_stale_launch_lock(path: Path) -> bool:
    """Remove only a provably stale launch record.

    The caller holds the supervisor singleton mutex. A live same-boot owner is
    never displaced; malformed records get a five-minute grace period so a
    concurrent writer is not mistaken for a crash.
    """
    if not path.exists():
        return False
    record = read_json(path, {})
    stale = False
    try:
        owner_pid = int(record["pid"])
        owner_boot = float(record["boot_time"])
        stale = abs(owner_boot - psutil.boot_time()) >= 1.0 or not psutil.pid_exists(owner_pid)
    except (KeyError, TypeError, ValueError):
        try:
            stale = time.time() - path.stat().st_mtime > 300
        except FileNotFoundError:
            return False
    if not stale:
        return False
    tomb = path.with_name(path.name + ".stale-" + uuid.uuid4().hex)
    try:
        os.replace(path, tomb)
        tomb.unlink(missing_ok=True)
        return True
    except FileNotFoundError:
        return False


def release_launch_lock(path: Path, generation: str) -> None:
    try:
        current = read_json(path, {})
        if current.get("generation") == generation:
            path.unlink(missing_ok=True)
    except Exception:
        pass


class SingleSupervisor:
    """Named OS mutex: a scheduled tick cannot create a split-brain controller."""

    def __init__(self, name: str = "Local\\the ownerFleetContinuitySupervisor") -> None:
        self.handle = None
        self.acquired = True
        if os.name == "nt":
            kernel32 = ctypes.windll.kernel32
            kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
            kernel32.CreateMutexW.restype = ctypes.c_void_p
            kernel32.ReleaseMutex.argtypes = [ctypes.c_void_p]
            kernel32.ReleaseMutex.restype = ctypes.c_bool
            kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel32.CloseHandle.restype = ctypes.c_bool
            kernel32.SetLastError(0)
            handle = kernel32.CreateMutexW(None, True, name)
            self.handle = handle
            self.acquired = bool(handle) and kernel32.GetLastError() != 183

    def close(self) -> None:
        if os.name == "nt" and self.handle:
            if self.acquired:
                ctypes.windll.kernel32.ReleaseMutex(self.handle)
            ctypes.windll.kernel32.CloseHandle(self.handle)
            self.handle = None


def scheduled_task_exists() -> bool:
    if os.name != "nt":
        return False
    result = subprocess.run(
        ["schtasks.exe", "/Query", "/TN", "FleetContinuitySupervisor"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15,
        creationflags=CREATE_NO_WINDOW,
    )
    return result.returncode == 0


def disable_scheduled_task() -> bool:
    if not scheduled_task_exists():
        return True
    result = subprocess.run(
        ["schtasks.exe", "/Change", "/TN", "FleetContinuitySupervisor", "/DISABLE"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15,
        creationflags=CREATE_NO_WINDOW,
    )
    return result.returncode == 0


class Supervisor:
    def __init__(self, manifest_path: Path, base: Path = BASE, dry_run: bool = False) -> None:
        self.manifest_path = manifest_path
        self.base = base
        self.dry_run = dry_run
        self.state_path = base / "state.json"
        self.events_path = base / "events.jsonl"
        self.locks = base / "locks"
        self.rollouts = Path.home() / ".codex" / "sessions"
        self.rollout_paths: dict[str, Path] = {}
        self.children: dict[str, subprocess.Popen[str]] = {}
        self.pending_events: list[dict[str, Any]] = []
        if not dry_run:
            removed = repair_jsonl_tail(self.events_path)
            if removed:
                self.pending_events.append({"kind": "repaired-partial-event-tail",
                                            "removed_bytes": removed})
        self.state = read_json(self.state_path, {"schema": 1, "workers": {}})
        if not isinstance(self.state, dict) or self.state.get("schema") != 1:
            self.state = {"schema": 1, "workers": {}}

    def rollout_snapshot(self, thread_id: str) -> dict[str, Any]:
        cached = self.rollout_paths.get(thread_id)
        if cached and cached.is_file():
            stat = cached.stat()
            return {"path": str(cached), "size": stat.st_size, "mtime": stat.st_mtime}
        found = latest_rollout(self.rollouts, thread_id)
        if found["path"]:
            self.rollout_paths[thread_id] = Path(found["path"])
        return found

    def worker_state(self, worker: dict[str, Any]) -> dict[str, Any]:
        thread_id = worker["thread_id"]
        current = self.state.setdefault("workers", {}).get(thread_id)
        if not isinstance(current, dict) or current.get("enrollment_id") != worker["enrollment_id"]:
            baseline = self.rollout_snapshot(thread_id)
            current = {
                "enrollment_id": worker["enrollment_id"],
                "remaining_active_seconds": worker["active_seconds_required"],
                "status": "disabled" if not worker["enabled"] else "queued",
                "consecutive_failures": 0,
                "turns_started": 0,
                "next_retry_at": 0,
                "last_accounted_at": utcnow().timestamp(),
                # An enrollment's existing transcript is history, not fresh
                # external-process activity. Growth after this exact baseline
                # is what triggers the ambiguity quarantine.
                "last_rollout_size": baseline["size"],
                "last_rollout_mtime": baseline["mtime"],
                "rollout_baselined_at": iso(),
            }
            self.state["workers"][thread_id] = current
        return current

    def compute_launch_policy(self, configured_maximum: int) -> tuple[bool, str, int]:
        path = Path.home() / ".claude" / "compute" / "compute_state.json"
        data = read_json(path, {})
        status = str(data.get("status") or "unknown")
        mode = str(data.get("mode") or "unknown")
        owner_active = data.get("owner_active")
        if status != "ok":
            return False, f"launch denied: local status={status} mode={mode}", 0
        if mode in {"throttle", "distress"}:
            return False, f"launch denied: local mode={mode}", 0
        if mode not in {"owner_present_polite", "owner_away_open", "open"}:
            return False, f"launch denied: unknown local mode={mode}", 0
        if owner_active is True or mode == "owner_present_polite":
            return True, f"{status}/{mode}; owner active -> cap 1", 1
        if owner_active is not False and mode not in {"owner_away_open", "open"}:
            return False, f"launch denied: owner presence unknown ({mode})", 0
        return True, f"{status}/{mode}; owner away/open", configured_maximum

    def _launch(self, worker: dict[str, Any], state: dict[str, Any], now: dt.datetime) -> dict[str, Any]:
        thread_id = worker["thread_id"]
        groups = process_groups_for_thread(thread_id)
        if groups:
            return {"kind": "launch-refused-live", "thread_id": thread_id}
        if self.dry_run:
            return {"kind": "dry-run-launch", "thread_id": thread_id}
        generation = str(uuid.uuid4())
        lock_path = self.locks / f"{thread_id}.launch.lock"
        record = {"thread_id": thread_id, "generation": generation, "pid": os.getpid(),
                  "boot_time": psutil.boot_time(), "created_at": iso(now)}
        reclaim_stale_launch_lock(lock_path)
        if not try_create_launch_lock(lock_path, record):
            return {"kind": "launch-refused-lock", "thread_id": thread_id}
        try:
            # Recheck under the held CreateNew lock. Cooperative launchers use the
            # same lane; an uncooperative external race is detected after birth.
            if process_groups_for_thread(thread_id):
                release_launch_lock(lock_path, generation)
                return {"kind": "launch-refused-live-after-lock", "thread_id": thread_id}
            baseline = self.rollout_snapshot(thread_id)
            pending_repair = str(state.get("output_repair_code") or "")
            prompt, injected_ids = continuation_prompt(
                worker, int(state["remaining_active_seconds"]), generation,
                pending_repair, return_warning_ids=True,
            )
            proc = subprocess.Popen(
                codex_command(thread_id), cwd=worker["cwd"], env=safe_child_env(worker),
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                text=True, encoding="utf-8", errors="replace",
                creationflags=CREATE_NO_WINDOW | BELOW_NORMAL_PRIORITY_CLASS,
            )
            assert proc.stdin is not None
            proc.stdin.write(prompt)
            proc.stdin.close()
            created = psutil.Process(proc.pid).create_time()
            self.children[thread_id] = proc
            state.update({
                "status": "launching",
                "current_pid": proc.pid,
                "current_process_created": created,
                "generation": generation,
                "launch_lock_generation": generation,
                "last_launch_at": iso(now),
                "launch_verify_by": (now + dt.timedelta(seconds=90)).timestamp(),
                "last_accounted_at": now.timestamp(),
                "turns_started": int(state.get("turns_started", 0)) + 1,
                "launch_rollout_path": baseline.get("path") or "",
                "launch_rollout_offset": int(baseline.get("size") or 0),
                "launch_warning_ids": list(injected_ids),
            })
            if pending_repair:
                state["output_repair_attempts"] = int(state.get("output_repair_attempts", 0)) + 1
                state["output_repair_inflight"] = pending_repair
            time.sleep(0.25)
            roots = process_groups_for_thread(thread_id)
            if len(roots) > 1:
                state["status"] = "duplicate"
                return {"kind": "duplicate-detected-after-launch", "thread_id": thread_id,
                        "pids": [root.pid for root in roots], "generation": generation}
            return {"kind": "launched", "thread_id": thread_id, "pid": proc.pid,
                    "generation": generation}
        except Exception:
            release_launch_lock(lock_path, generation)
            raise

    def tick(self, now: dt.datetime | None = None, write_sample: bool = True) -> dict[str, Any]:
        now = now or utcnow()
        manifest = load_manifest(self.manifest_path)
        states = {worker["thread_id"]: self.worker_state(worker) for worker in manifest["workers"]}
        running: set[str] = set()
        events: list[dict[str, Any]] = []
        roster: list[dict[str, Any]] = []
        accounting_poll = manifest["controller"]["accounting_poll_seconds"]

        for worker in manifest["workers"]:
            thread_id = worker["thread_id"]
            state = states[thread_id]
            proc = None
            groups = process_groups_for_thread(thread_id)
            if len(groups) > 1:
                running.add(thread_id)
                if state.get("status") != "duplicate":
                    events.append({"kind": "duplicate-detected", "thread_id": thread_id,
                                   "pids": [root.pid for root in groups]})
                state.update({"status": "duplicate", "duplicate_pids": [root.pid for root in groups]})
            elif state.get("current_pid") and process_is_exact(
                    int(state["current_pid"]), float(state.get("current_process_created", 0)), thread_id):
                proc = psutil.Process(int(state["current_pid"]))
            elif len(groups) == 1:
                proc = groups[0]
            else:
                state.pop("duplicate_pids", None)
            rollout = self.rollout_snapshot(thread_id)
            progressed = (rollout["size"] > int(state.get("last_rollout_size", 0))
                          or rollout["mtime"] > float(state.get("last_rollout_mtime", 0)))
            last_accounted = float(state.get("last_accounted_at", now.timestamp()))

            if proc:
                running.add(thread_id)
                if progressed:
                    # Credit only one short observed polling slice. After a crash,
                    # reboot, or scheduler gap, at most accounting_poll seconds can
                    # be credited—never the missing wall-clock interval.
                    elapsed = int(max(0, min(accounting_poll, now.timestamp() - last_accounted)))
                    state["remaining_active_seconds"] = max(
                        0, int(state.get("remaining_active_seconds", 0) - elapsed)
                    )
                    state["last_progress_at"] = iso(now)
                    state["consecutive_failures"] = 0
                if state.get("status") not in ("launching", "running"):
                    events.append({"kind": "adopted-live-process", "thread_id": thread_id,
                                   "pid": proc.pid})
                state.update({"status": "running" if worker["enabled"] else "disabled-running",
                              "current_pid": proc.pid, "current_process_created": proc.create_time()})
                if (float(state.get("launch_verify_by", 0)) <= now.timestamp()
                        and state.get("launch_verify_by") and not progressed
                        and not state.get("launch_unverified_reported")):
                    events.append({"kind": "launch-progress-unverified", "thread_id": thread_id,
                                   "pid": proc.pid})
                    state["launch_unverified_reported"] = True
            elif len(groups) == 0:
                was_managed = state.get("status") in (
                    "running", "launching", "disabled-running", "duplicate"
                )
                if state.get("status") in ("running", "launching", "disabled-running", "duplicate"):
                    child = self.children.pop(thread_id, None)
                    returncode = child.poll() if child else None
                    generation = state.pop("launch_lock_generation", None)
                    if generation:
                        release_launch_lock(self.locks / f"{thread_id}.launch.lock", generation)
                    if progressed:
                        state["consecutive_failures"] = 0
                    else:
                        state["consecutive_failures"] = int(state.get("consecutive_failures", 0)) + 1
                    state["next_retry_at"] = now.timestamp() + min(
                        900, 30 * max(1, int(state["consecutive_failures"]))
                    )
                    generation = str(state.get("generation") or "")
                    expected_ids = tuple(state.get("launch_warning_ids") or ())
                    rollout_path = Path(str(rollout.get("path") or ""))
                    launched_path = str(state.get("launch_rollout_path") or "")
                    if (not generation or not expected_ids or not rollout_path.is_file()
                            or (launched_path and Path(launched_path).resolve() != rollout_path.resolve())):
                        output_result = {"ok": False, "code": "generation-baseline-unavailable"}
                    else:
                        output_result = validate_rollout_generation(
                            rollout_path, int(state.get("launch_rollout_offset") or 0),
                            generation, expected_ids, returncode)
                    output_code = clean_text(output_result.get("code"), 100) or "output-unknown"
                    if output_result.get("ok"):
                        state["last_output_acceptance"] = {
                            "generation": generation,
                            "turn_id": clean_text(output_result.get("turn_id"), 80),
                            "ts": now.timestamp(),
                            "final_sha256": output_result.get("final_sha256"),
                            "warning_ids": list(expected_ids),
                        }
                        state.pop("output_repair_code", None)
                        state.pop("output_repair_inflight", None)
                        state["output_repair_attempts"] = 0
                    else:
                        state.pop("last_output_acceptance", None)
                        state["output_repair_code"] = output_code
                    events.append({"kind": "turn-exited", "thread_id": thread_id,
                                   "returncode": returncode, "progressed": progressed,
                                   "output_acceptance": output_code})
                state.pop("current_pid", None)
                state.pop("current_process_created", None)
                state.pop("launch_verify_by", None)
                state.pop("launch_unverified_reported", None)
                if not worker["enabled"]:
                    state["status"] = "disabled"
                elif progressed and not was_managed:
                    # A plain Codex process does not expose its thread id in its
                    # command line. Rollout growth with no exact managed process
                    # is identity ambiguity, not evidence of death.
                    state["status"] = "ambiguous"
                    state["ambiguous_progress_at"] = iso(now)
                    events.append({"kind": "external-progress-ambiguous",
                                   "thread_id": thread_id})
                elif needs_service(worker, state, now):
                    if state.get("status") != "ambiguous":
                        state["status"] = ("queued" if int(state.get("consecutive_failures", 0)) < 5
                                           else "blocked")
                else:
                    accepted = state.get("last_output_acceptance")
                    if isinstance(accepted, dict) and accepted.get("generation") == state.get("generation"):
                        state["status"] = "complete"
                    else:
                        if state.get("status") != "blocked-output":
                            events.append({"kind": "completion-output-unaccepted",
                                           "thread_id": thread_id,
                                           "reason": clean_text(state.get("output_repair_code"), 100)
                                                     or "no-accepted-generation"})
                        state["status"] = "blocked-output"

            state["last_accounted_at"] = now.timestamp()
            state["last_rollout_size"] = rollout["size"]
            state["last_rollout_mtime"] = rollout["mtime"]
            state["last_poll_at"] = iso(now)
            roster.append({"thread_id": thread_id, "enabled": worker["enabled"],
                           "status": state["status"], "remaining_active_seconds":
                           int(state.get("remaining_active_seconds", 0)), "rollout_age_seconds":
                           round(max(0, now.timestamp() - rollout["mtime"]), 1) if rollout["mtime"] else None,
                           "pid": state.get("current_pid"), "duplicate_pids": state.get("duplicate_pids")})

        allowed, compute_reason, effective_maximum = self.compute_launch_policy(
            manifest["controller"]["max_concurrent"]
        )
        if allowed:
            for thread_id in plan_launches(manifest["workers"], states, running, now,
                                           effective_maximum):
                worker = next(item for item in manifest["workers"] if item["thread_id"] == thread_id)
                event = self._launch(worker, states[thread_id], now)
                events.append(event)
                if event["kind"] in {"launched", "duplicate-detected-after-launch"}:
                    running.add(thread_id)
        elif any(worker["enabled"] and needs_service(worker, states[worker["thread_id"]], now)
                 for worker in manifest["workers"]):
            events.append({"kind": "launch-paused-compute", "reason": compute_reason})

        if write_sample:
            last_roster = float(self.state.get("last_roster_sample_at", 0))
            gap = now.timestamp() - last_roster if last_roster else 0
            expected = manifest["controller"]["sample_interval_seconds"]
            if last_roster and gap > expected * 1.5:
                events.insert(0, {"kind": "coverage-gap", "gap_seconds": round(gap, 3),
                                  "expected_seconds": expected})
            self.state["last_roster_sample_at"] = now.timestamp()
            events = self.pending_events + events
            self.pending_events = []
        else:
            self.pending_events.extend(events)

        sample = {"schema": 1, "ts": now.timestamp(), "iso": iso(now),
                  "compute": compute_reason, "running_threads": sorted(running),
                  "workers": roster, "events": events}
        if not self.dry_run:
            self.state["updated_at"] = iso(now)
            self.state["boot_time"] = psutil.boot_time()
            atomic_json(self.state_path, self.state)
            if write_sample:
                append_jsonl(self.events_path, sample)
        return sample

    def all_satisfied(self, now: dt.datetime | None = None) -> bool:
        now = now or utcnow()
        manifest = load_manifest(self.manifest_path)
        enabled = [worker for worker in manifest["workers"] if worker["enabled"]]
        if not enabled:
            return True
        for worker in enabled:
            state = self.worker_state(worker)
            if (state.get("status") != "complete" or needs_service(worker, state, now)
                    or process_groups_for_thread(worker["thread_id"])):
                return False
        return True

    def run(self, once: bool = False) -> int:
        next_sample = 0.0
        while not (self.base / "STOP").exists():
            loop_started = time.monotonic()
            manifest = load_manifest(self.manifest_path)
            sample_interval = manifest["controller"]["sample_interval_seconds"]
            poll_interval = manifest["controller"]["accounting_poll_seconds"]
            write_sample = once or loop_started >= next_sample
            sample = self.tick(write_sample=write_sample)
            if self.dry_run and write_sample:
                print(json.dumps(sample, ensure_ascii=False, indent=2, sort_keys=True))
            if write_sample:
                next_sample = loop_started + sample_interval
            satisfied = self.all_satisfied()
            if once:
                return 0
            if satisfied:
                if self.dry_run or disable_scheduled_task():
                    return 0
                append_jsonl(self.events_path, {"schema": 1, "ts": utcnow().timestamp(),
                    "iso": iso(), "events": [{"kind": "task-disable-failed"}]})
                return 2
            time.sleep(max(0.5, poll_interval - (time.monotonic() - loop_started)))
        if self.dry_run or disable_scheduled_task():
            return 0
        append_jsonl(self.events_path, {"schema": 1, "ts": utcnow().timestamp(), "iso": iso(),
                     "events": [{"kind": "task-disable-failed-on-stop"}]})
        return 2


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=Path(os.environ.get(
        "FLEET_CONTINUITY_MANIFEST", DEFAULT_MANIFEST)))
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--validate", action="store_true")
    args = parser.parse_args()
    manifest = load_manifest(args.manifest)
    if args.validate:
        print(f"manifest valid: {len(manifest['workers'])} explicitly enrolled worker(s)")
        print(f"native codex: {native_codex_exe()}")
        return 0
    mutex = SingleSupervisor()
    if not mutex.acquired:
        return 0
    try:
        return Supervisor(args.manifest, BASE, args.dry_run).run(once=args.once)
    finally:
        mutex.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException as exc:
        try:
            append_jsonl(BASE / "events.jsonl", {
                "schema": 1,
                "ts": utcnow().timestamp(),
                "iso": iso(),
                "events": [{"kind": "controller-crash", "error_type": type(exc).__name__}],
            })
        except BaseException:
            pass
        raise
