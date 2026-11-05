#!/usr/bin/env python3
"""Bounded, shadow-only prospective-control experiment.

The module freezes exactly three pre-work trajectories (expected, scar, and
pivot), cluster-randomizes whether the bundle is shown, resolves it later,
and reports proper forecast scores plus guarded-error recurrence by arm.

This is an engineering experiment inspired by work on policy rollouts,
compositional replay, and replay under uncertainty. It does not claim that
this software is a biological model or that its records are neural replay.
It has no hook, dream-runner, memory-write, or deployment integration.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import math
import os
import random
import re
import sys
from collections import Counter, defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


SCHEMA_VERSION = 1
GENESIS_HASH = "0" * 64
TRAJECTORY_KINDS = ("expected", "scar", "pivot")
ASSIGNMENT_ALGORITHM = "hmac-sha256-cluster-v1"
PLANNING_MECHANISM_VERSION = "fixed-three-shadow-v1"
MIN_PROBABILITY = 1e-6
BOOTSTRAP_SAMPLES = 4000

RESEARCH_BASIS = (
    {
        "citation": "Jensen, Hennequin & Mattar, Nature Neuroscience (2024)",
        "url": "https://www.nature.com/articles/s41593-024-01675-7",
        "translation": (
            "Treat planning as costly: randomize whether rollouts are shown, "
            "measure overhead and downstream recurrence, and never promote on "
            "forecast plausibility alone."
        ),
    },
    {
        "citation": "Bakermans et al., Nature Neuroscience (2025)",
        "url": "https://www.nature.com/articles/s41593-025-01908-3",
        "translation": (
            "Freeze reusable source slugs per trajectory and require replication "
            "across independent units before treating reuse as useful."
        ),
    },
    {
        "citation": "Antonov & Dayan, Nature Communications (2025)",
        "url": "https://www.nature.com/articles/s41467-025-56731-y",
        "translation": (
            "Represent uncertainty explicitly, prioritize scar replay by frozen "
            "probability times severity, and score the resulting belief before use."
        ),
    },
)

DEFAULT_GATES: dict[str, Any] = {
    "min_resolved_bundles": 60,
    "min_independent_units": 30,
    "min_experiment_days": 14.0,
    "min_eligible_units_per_arm": 12,
    "min_resolution_fraction": 0.90,
    "max_largest_unit_share": 0.10,
    "max_arm_resolution_rate_gap": 0.10,
    "max_mean_brier": 0.60,
    "max_mean_log_loss": 1.00,
    "max_visible_overhead_seconds": 180.0,
    "min_distinct_scar_sources": 5,
    "min_reused_scar_sources": 3,
    "max_critical_harms": 0,
    "max_late_resolution_fraction": 0.05,
    "recurrence_ci_upper_below": 0.0,
}

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._/-]{0,159}$")
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_SECRET_RE = re.compile(
    r"(?i)(?:ghp_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{20,}|"
    r"xox[baprs]-[A-Za-z0-9-]{16,}|AKIA[A-Z0-9]{16}|"
    r"-----BEGIN [A-Z ]+PRIVATE KEY-----|Bearer\s+\S+|"
    r"(?:password|secret|token)\s*[:=]\s*\S+)"
)


class ProspectiveControlError(RuntimeError):
    """Base error for a failed shadow experiment operation."""


class ContractError(ProspectiveControlError):
    """An input or historical event violates the experiment contract."""


class LedgerError(ProspectiveControlError):
    """The append-only ledger is unavailable or fails integrity checks."""


@dataclass
class LedgerState:
    experiment: dict[str, Any] | None = None
    scenarios: dict[str, dict[str, Any]] = field(default_factory=dict)
    resolutions: dict[str, dict[str, Any]] = field(default_factory=dict)
    records: list[dict[str, Any]] = field(default_factory=list)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _record_hash(record_without_hash: Mapping[str, Any]) -> str:
    return _digest(record_without_hash)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_datetime(value: datetime | str, name: str) -> datetime:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ContractError(f"{name} must be an ISO-8601 timestamp") from exc
    else:
        raise ContractError(f"{name} must be an ISO-8601 timestamp")
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ContractError(f"{name} must include a timezone")
    return dt.astimezone(timezone.utc)


def _iso(value: datetime | str, name: str = "timestamp") -> str:
    dt = _as_datetime(value, name)
    return dt.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _now(value: datetime | None) -> datetime:
    return _as_datetime(value or _utc_now(), "clock")


def _require_exact_keys(
    value: Mapping[str, Any], required: Iterable[str], name: str
) -> None:
    if not isinstance(value, Mapping):
        raise ContractError(f"{name} must be an object")
    expected = set(required)
    actual = set(value)
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if missing or extra:
        detail = []
        if missing:
            detail.append("missing " + ", ".join(missing))
        if extra:
            detail.append("unexpected " + ", ".join(extra))
        raise ContractError(f"{name} fields invalid: {'; '.join(detail)}")


def _safe_id(value: Any, name: str) -> str:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise ContractError(
            f"{name} must be 1-128 characters using letters, digits, dot, "
            "underscore, colon, or hyphen"
        )
    return value


def _safe_slug(value: Any, name: str) -> str:
    if (
        not isinstance(value, str)
        or not _SLUG_RE.fullmatch(value)
        or ".." in value
        or "//" in value
        or value.startswith("/")
    ):
        raise ContractError(
            f"{name} must be a lowercase source slug, not a path, URL, or prose"
        )
    return value


def _slug_list(value: Any, name: str, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list):
        raise ContractError(f"{name} must be a list of source slugs")
    slugs = sorted({_safe_slug(v, f"{name}[]") for v in value})
    if not slugs and not allow_empty:
        raise ContractError(f"{name} must contain at least one source slug")
    return slugs


def _safe_text(value: Any, name: str, *, max_length: int = 600) -> str:
    if not isinstance(value, str):
        raise ContractError(f"{name} must be text")
    text = value.strip()
    if not text or len(text) > max_length or "\n" in text or "\r" in text:
        raise ContractError(f"{name} must be one non-empty line of at most {max_length} characters")
    if _SECRET_RE.search(text):
        raise ContractError(f"{name} resembles a secret and is refused")
    return text


def _finite_number(value: Any, name: str, *, minimum: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractError(f"{name} must be a finite number")
    number = float(value)
    if not math.isfinite(number) or number < minimum:
        raise ContractError(f"{name} must be finite and at least {minimum}")
    return number


def _signed_finite_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractError(f"{name} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise ContractError(f"{name} must be a finite number")
    return number


def _integer(value: Any, name: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ContractError(f"{name} must be an integer at least {minimum}")
    return value


def _probability(value: Any, name: str) -> float:
    p = _finite_number(value, name, minimum=MIN_PROBABILITY)
    if p > 1.0:
        raise ContractError(f"{name} must be at most 1")
    return p


def _normalize_gates(overrides: Mapping[str, Any] | None) -> dict[str, Any]:
    gates = dict(DEFAULT_GATES)
    if overrides is not None:
        if not isinstance(overrides, Mapping):
            raise ContractError("promotion_gates must be an object")
        unknown = sorted(set(overrides) - set(gates))
        if unknown:
            raise ContractError("unknown promotion gates: " + ", ".join(unknown))
        gates.update(overrides)

    for key in (
        "min_resolved_bundles",
        "min_independent_units",
        "min_eligible_units_per_arm",
        "min_distinct_scar_sources",
        "min_reused_scar_sources",
        "max_critical_harms",
    ):
        minimum = 0 if key in ("min_reused_scar_sources", "max_critical_harms") else 1
        gates[key] = _integer(gates[key], key, minimum=minimum)

    for key in (
        "min_experiment_days",
        "min_resolution_fraction",
        "max_arm_resolution_rate_gap",
        "max_mean_brier",
        "max_mean_log_loss",
        "max_visible_overhead_seconds",
        "max_late_resolution_fraction",
    ):
        gates[key] = _finite_number(gates[key], key, minimum=0.0)

    share = _finite_number(gates["max_largest_unit_share"], "max_largest_unit_share")
    if share <= 0 or share > 1:
        raise ContractError("max_largest_unit_share must be in (0, 1]")
    gates["max_largest_unit_share"] = share

    fraction = gates["max_late_resolution_fraction"]
    if fraction > 1:
        raise ContractError("max_late_resolution_fraction must be at most 1")
    if gates["min_resolution_fraction"] > 1:
        raise ContractError("min_resolution_fraction must be at most 1")
    if gates["max_arm_resolution_rate_gap"] > 1:
        raise ContractError("max_arm_resolution_rate_gap must be at most 1")
    if gates["max_mean_brier"] > 2:
        raise ContractError("max_mean_brier must be at most 2 for a three-class score")
    gates["recurrence_ci_upper_below"] = _signed_finite_number(
        gates["recurrence_ci_upper_below"], "recurrence_ci_upper_below"
    )
    if not -1.0 <= gates["recurrence_ci_upper_below"] <= 1.0:
        raise ContractError("recurrence_ci_upper_below must be in [-1, 1]")
    return gates


def normalize_trajectories(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) != 3:
        raise ContractError("trajectories must contain exactly expected, scar, and pivot")
    by_kind: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(value):
        if not isinstance(raw, Mapping):
            raise ContractError(f"trajectories[{index}] must be an object")
        kind = raw.get("kind")
        if kind not in TRAJECTORY_KINDS or kind in by_kind:
            raise ContractError("trajectory kinds must be unique: expected, scar, pivot")
        base = {"kind", "probability", "prediction", "observable", "source_slugs"}
        if kind == "expected":
            expected_keys = base
        elif kind == "scar":
            expected_keys = base | {"scar_source_slugs", "severity", "guard"}
        else:
            expected_keys = base | {"trigger", "pivot_action"}
        _require_exact_keys(raw, expected_keys, f"trajectory {kind}")

        row: dict[str, Any] = {
            "kind": kind,
            "probability": _probability(raw["probability"], f"{kind}.probability"),
            "prediction": _safe_text(raw["prediction"], f"{kind}.prediction"),
            "observable": _safe_text(raw["observable"], f"{kind}.observable"),
            "source_slugs": _slug_list(raw["source_slugs"], f"{kind}.source_slugs"),
        }
        if kind == "scar":
            scars = _slug_list(raw["scar_source_slugs"], "scar.scar_source_slugs")
            if not set(scars).issubset(row["source_slugs"]):
                raise ContractError("scar_source_slugs must be a subset of scar.source_slugs")
            severity = _integer(raw["severity"], "scar.severity", minimum=1)
            if severity > 3:
                raise ContractError("scar.severity must be 1, 2, or 3")
            guard = raw["guard"]
            _require_exact_keys(guard, {"guard_id", "action", "error_signature"}, "scar.guard")
            row.update(
                {
                    "scar_source_slugs": scars,
                    "severity": severity,
                    "guard": {
                        "guard_id": _safe_id(guard["guard_id"], "scar.guard.guard_id"),
                        "action": _safe_text(guard["action"], "scar.guard.action"),
                        "error_signature": _safe_id(
                            guard["error_signature"], "scar.guard.error_signature"
                        ),
                    },
                }
            )
        elif kind == "pivot":
            row.update(
                {
                    "trigger": _safe_text(raw["trigger"], "pivot.trigger"),
                    "pivot_action": _safe_text(raw["pivot_action"], "pivot.pivot_action"),
                }
            )
        by_kind[kind] = row

    normalized = [by_kind[kind] for kind in TRAJECTORY_KINDS]
    total = math.fsum(row["probability"] for row in normalized)
    if not math.isclose(total, 1.0, rel_tol=0.0, abs_tol=1e-9):
        raise ContractError(f"trajectory probabilities must sum to 1 (got {total:.12g})")
    return normalized


def _validate_key(key: bytes) -> bytes:
    if not isinstance(key, bytes) or len(key) < 32:
        raise ContractError("assignment key must contain at least 32 bytes")
    return key


def assignment_key_id(key: bytes) -> str:
    return hashlib.sha256(_validate_key(key)).hexdigest()


def read_assignment_key(path: str | os.PathLike[str]) -> bytes:
    raw = Path(path).read_bytes()
    text_candidate = raw.strip()
    if (
        len(text_candidate) >= 64
        and len(text_candidate) % 2 == 0
        and re.fullmatch(rb"[0-9a-fA-F]+", text_candidate)
    ):
        try:
            raw = bytes.fromhex(text_candidate.decode("ascii"))
        except ValueError:
            pass
    return _validate_key(raw)


def assignment_for(
    key: bytes, experiment_id: str, unit_id: str, visible_fraction: float
) -> dict[str, str]:
    key = _validate_key(key)
    experiment_id = _safe_id(experiment_id, "experiment_id")
    unit_id = _safe_id(unit_id, "unit_id")
    if not 0.1 <= visible_fraction <= 0.9:
        raise ContractError("visible_fraction must be in [0.1, 0.9]")
    message = f"arm|{SCHEMA_VERSION}|{experiment_id}|{unit_id}".encode("utf-8")
    digest = hmac.new(key, message, hashlib.sha256).digest()
    draw = int.from_bytes(digest[:8], "big") / float(1 << 64)
    arm = "visible" if draw < visible_fraction else "hidden"
    commitment_message = (
        f"commit|{SCHEMA_VERSION}|{experiment_id}|{unit_id}|{arm}"
    ).encode("utf-8")
    commitment = hmac.new(key, commitment_message, hashlib.sha256).hexdigest()
    return {
        "algorithm": ASSIGNMENT_ALGORITHM,
        "key_id": assignment_key_id(key),
        "arm": arm,
        "commitment": commitment,
    }


@contextmanager
def _ledger_lock(path: Path):
    lock_path = Path(str(path) + ".lock")
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise LedgerError(
            f"ledger lock exists: {lock_path}; inspect the writer instead of "
            "breaking it automatically"
        ) from exc
    try:
        payload = _canonical_json({"pid": os.getpid(), "created_at": _iso(_utc_now())}) + "\n"
        os.write(fd, payload.encode("utf-8"))
        os.fsync(fd)
        yield
    finally:
        os.close(fd)
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def _load_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    previous_hash = GENESIS_HASH
    previous_time: datetime | None = None
    with path.open("r", encoding="utf-8", errors="strict") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.endswith("\n"):
                raise LedgerError(f"ledger line {line_number} is incomplete")
            if not line.strip():
                raise LedgerError(f"ledger line {line_number} is blank")
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise LedgerError(f"ledger line {line_number} is not valid JSON") from exc
            if not isinstance(record, dict):
                raise LedgerError(f"ledger line {line_number} is not an object")
            required = {
                "schema_version",
                "sequence",
                "event_type",
                "recorded_at",
                "prev_hash",
                "payload",
                "record_hash",
            }
            if set(record) != required:
                raise LedgerError(f"ledger line {line_number} has an unknown record shape")
            if record["schema_version"] != SCHEMA_VERSION:
                raise LedgerError(f"ledger line {line_number} has an unsupported schema")
            if record["sequence"] != line_number:
                raise LedgerError(f"ledger line {line_number} has a non-contiguous sequence")
            if record["prev_hash"] != previous_hash:
                raise LedgerError(f"ledger line {line_number} breaks the hash chain")
            claimed = record["record_hash"]
            if not isinstance(claimed, str) or not _HEX64_RE.fullmatch(claimed):
                raise LedgerError(f"ledger line {line_number} has an invalid record hash")
            unhashed = {k: v for k, v in record.items() if k != "record_hash"}
            if not hmac.compare_digest(claimed, _record_hash(unhashed)):
                raise LedgerError(f"ledger line {line_number} failed its record hash")
            recorded = _as_datetime(record["recorded_at"], f"line {line_number}.recorded_at")
            if previous_time is not None and recorded < previous_time:
                raise LedgerError(f"ledger line {line_number} moves backward in time")
            previous_time = recorded
            previous_hash = claimed
            records.append(record)
    return records


def _validate_experiment_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    keys = {
        "experiment_id",
        "created_at",
        "end_at",
        "max_bundles",
        "visible_fraction",
        "resolution_grace_days",
        "assignment_key_id",
        "promotion_gates",
        "shadow_only",
        "research_translation_version",
    }
    _require_exact_keys(payload, keys, "experiment_started.payload")
    experiment_id = _safe_id(payload["experiment_id"], "experiment_id")
    created = _as_datetime(payload["created_at"], "created_at")
    end = _as_datetime(payload["end_at"], "end_at")
    if end <= created:
        raise ContractError("end_at must be after created_at")
    max_bundles = _integer(payload["max_bundles"], "max_bundles", minimum=4)
    if max_bundles > 10000:
        raise ContractError("max_bundles must be at most 10000")
    visible_fraction = _finite_number(payload["visible_fraction"], "visible_fraction")
    if not 0.1 <= visible_fraction <= 0.9:
        raise ContractError("visible_fraction must be in [0.1, 0.9]")
    grace = _integer(payload["resolution_grace_days"], "resolution_grace_days")
    if grace > 365:
        raise ContractError("resolution_grace_days must be at most 365")
    key_id = payload["assignment_key_id"]
    if not isinstance(key_id, str) or not _HEX64_RE.fullmatch(key_id):
        raise ContractError("assignment_key_id must be a full SHA-256 digest")
    gates = _normalize_gates(payload["promotion_gates"])
    if gates != payload["promotion_gates"]:
        raise ContractError("promotion_gates must use canonical numeric values")
    if gates["min_resolved_bundles"] > max_bundles:
        raise ContractError("min_resolved_bundles cannot exceed max_bundles")
    if gates["min_independent_units"] > max_bundles:
        raise ContractError("min_independent_units cannot exceed max_bundles")
    if payload["shadow_only"] is not True:
        raise ContractError("experiment must remain shadow_only")
    if payload["research_translation_version"] != PLANNING_MECHANISM_VERSION:
        raise ContractError("unknown research translation version")
    return dict(payload)


def _validate_scenario_payload(
    payload: Mapping[str, Any], experiment: Mapping[str, Any], prior: Sequence[dict[str, Any]]
) -> dict[str, Any]:
    keys = {
        "experiment_id",
        "bundle_id",
        "unit_id",
        "unit_source_slug",
        "author_id",
        "created_at",
        "evidence_cutoff_at",
        "work_not_started_attestation",
        "trajectories",
        "trajectory_set_digest",
        "source_set_digest",
        "planning_policy",
        "assignment",
    }
    _require_exact_keys(payload, keys, "scenario_created.payload")
    if payload["experiment_id"] != experiment["experiment_id"]:
        raise ContractError("scenario experiment_id does not match the ledger")
    _safe_id(payload["bundle_id"], "bundle_id")
    unit_id = _safe_id(payload["unit_id"], "unit_id")
    unit_source = _safe_slug(payload["unit_source_slug"], "unit_source_slug")
    _safe_id(payload["author_id"], "author_id")
    created = _as_datetime(payload["created_at"], "created_at")
    cutoff = _as_datetime(payload["evidence_cutoff_at"], "evidence_cutoff_at")
    if created < _as_datetime(experiment["created_at"], "experiment.created_at"):
        raise ContractError("scenario predates the experiment")
    if created > _as_datetime(experiment["end_at"], "experiment.end_at"):
        raise ContractError("scenario was created after the bounded experiment window")
    if cutoff > created:
        raise ContractError("evidence_cutoff_at cannot be after created_at")
    if payload["work_not_started_attestation"] is not True:
        raise ContractError("work_not_started_attestation must be true")
    trajectories = normalize_trajectories(payload["trajectories"])
    if trajectories != payload["trajectories"]:
        raise ContractError("trajectories are not in canonical frozen form")
    if payload["trajectory_set_digest"] != _digest(trajectories):
        raise ContractError("trajectory_set_digest does not match the frozen trajectories")
    all_sources = sorted({slug for row in trajectories for slug in row["source_slugs"]})
    if payload["source_set_digest"] != _digest(all_sources):
        raise ContractError("source_set_digest does not match the frozen source slugs")

    scar = trajectories[1]
    policy = payload["planning_policy"]
    _require_exact_keys(
        policy,
        {"rollout_budget", "scar_replay_priority", "priority_rule", "mechanism_version"},
        "planning_policy",
    )
    if policy["rollout_budget"] != 3:
        raise ContractError("rollout_budget must remain exactly three")
    priority = round(scar["probability"] * scar["severity"], 9)
    if policy["scar_replay_priority"] != priority:
        raise ContractError("scar_replay_priority must equal probability times severity")
    if policy["priority_rule"] != "probability_x_severity":
        raise ContractError("unknown priority_rule")
    if policy["mechanism_version"] != PLANNING_MECHANISM_VERSION:
        raise ContractError("unknown planning mechanism version")

    assignment = payload["assignment"]
    _require_exact_keys(
        assignment, {"algorithm", "key_id", "arm", "commitment"}, "assignment"
    )
    if assignment["algorithm"] != ASSIGNMENT_ALGORITHM:
        raise ContractError("unknown assignment algorithm")
    if assignment["key_id"] != experiment["assignment_key_id"]:
        raise ContractError("assignment key id does not match the experiment")
    if assignment["arm"] not in ("visible", "hidden"):
        raise ContractError("assignment arm must be visible or hidden")
    if not isinstance(assignment["commitment"], str) or not _HEX64_RE.fullmatch(
        assignment["commitment"]
    ):
        raise ContractError("assignment commitment must be a SHA-256 HMAC")
    for old in prior:
        if old["unit_id"] == unit_id:
            if old["unit_source_slug"] != unit_source:
                raise ContractError("unit_source_slug changed within an assignment cluster")
            if old["assignment"] != assignment:
                raise ContractError("assignment changed within an assignment cluster")
    return dict(payload)


def _validate_resolution_payload(
    payload: Mapping[str, Any], experiment: Mapping[str, Any], scenario: Mapping[str, Any]
) -> dict[str, Any]:
    keys = {
        "experiment_id",
        "bundle_id",
        "resolved_at",
        "work_started_at",
        "observed_kind",
        "adjudicator_id",
        "adjudicator_blinded_to_arm",
        "guarded_error_eligible",
        "guarded_error_recurred",
        "guard_followed",
        "guard_exposed",
        "planning_overhead_seconds",
        "critical_harm",
        "outcome_evidence_slugs",
        "resolution_lag_seconds",
        "adjudication_late",
    }
    _require_exact_keys(payload, keys, "scenario_resolved.payload")
    if payload["experiment_id"] != experiment["experiment_id"]:
        raise ContractError("resolution experiment_id does not match the ledger")
    if payload["bundle_id"] != scenario["bundle_id"]:
        raise ContractError("resolution bundle_id does not match its scenario")
    resolved = _as_datetime(payload["resolved_at"], "resolved_at")
    started = _as_datetime(payload["work_started_at"], "work_started_at")
    created = _as_datetime(scenario["created_at"], "scenario.created_at")
    if started <= created:
        raise ContractError("work_started_at must be after the immutable scenario creation")
    if started > _as_datetime(experiment["end_at"], "experiment.end_at"):
        raise ContractError("work_started_at must be inside the bounded experiment window")
    if resolved <= started:
        raise ContractError("resolved_at must be after work_started_at")
    if payload["observed_kind"] not in TRAJECTORY_KINDS:
        raise ContractError("observed_kind must be expected, scar, or pivot")
    _safe_id(payload["adjudicator_id"], "adjudicator_id")
    if payload["adjudicator_blinded_to_arm"] is not True:
        raise ContractError("adjudicator_blinded_to_arm must be true")
    eligible = payload["guarded_error_eligible"]
    if not isinstance(eligible, bool):
        raise ContractError("guarded_error_eligible must be boolean")
    recurred = payload["guarded_error_recurred"]
    followed = payload["guard_followed"]
    arm = scenario["assignment"]["arm"]
    if eligible:
        if not isinstance(recurred, bool):
            raise ContractError("eligible outcomes require guarded_error_recurred")
        if arm == "visible" and not isinstance(followed, bool):
            raise ContractError("eligible visible outcomes require guard_followed")
        if arm == "hidden" and followed is not None:
            raise ContractError("hidden controls cannot report following an unseen guard")
    elif recurred is not None or followed is not None:
        raise ContractError("ineligible outcomes require null recurrence and adherence")
    expected_exposure = arm == "visible"
    if payload["guard_exposed"] is not expected_exposure:
        raise ContractError("guard_exposed must be derived from randomized assignment")
    overhead = _finite_number(
        payload["planning_overhead_seconds"], "planning_overhead_seconds"
    )
    if overhead > 86400:
        raise ContractError("planning_overhead_seconds must be at most one day")
    if arm == "hidden" and overhead != 0.0:
        raise ContractError("hidden controls must have zero planning exposure overhead")
    if not isinstance(payload["critical_harm"], bool):
        raise ContractError("critical_harm must be boolean")
    evidence_slugs = _slug_list(
        payload["outcome_evidence_slugs"], "outcome_evidence_slugs"
    )
    if evidence_slugs != payload["outcome_evidence_slugs"]:
        raise ContractError("outcome_evidence_slugs must be in canonical frozen form")
    lag = _finite_number(payload["resolution_lag_seconds"], "resolution_lag_seconds")
    if not math.isclose(lag, (resolved - created).total_seconds(), abs_tol=1e-6):
        raise ContractError("resolution_lag_seconds does not match ledger times")
    deadline = _as_datetime(experiment["end_at"], "experiment.end_at") + timedelta(
        days=experiment["resolution_grace_days"]
    )
    if payload["adjudication_late"] is not (resolved > deadline):
        raise ContractError("adjudication_late does not match the bounded deadline")
    return dict(payload)


def _build_state(records: Sequence[dict[str, Any]]) -> LedgerState:
    state = LedgerState(records=list(records))
    scenarios_in_order: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        event_type = record["event_type"]
        payload = record["payload"]
        if not isinstance(payload, Mapping):
            raise ContractError(f"record {index + 1} payload must be an object")
        if event_type == "experiment_started":
            if index != 0 or state.experiment is not None:
                raise ContractError("experiment_started must be the first and only init event")
            validated = _validate_experiment_payload(payload)
            if record["recorded_at"] != validated["created_at"]:
                raise ContractError("experiment created_at must equal its append time")
            state.experiment = validated
        elif event_type == "scenario_created":
            if state.experiment is None:
                raise ContractError("scenario_created precedes experiment_started")
            validated = _validate_scenario_payload(payload, state.experiment, scenarios_in_order)
            bundle_id = validated["bundle_id"]
            if bundle_id in state.scenarios:
                raise ContractError(f"duplicate scenario bundle_id: {bundle_id}")
            if len(state.scenarios) >= state.experiment["max_bundles"]:
                raise ContractError("scenario count exceeds the bounded max_bundles")
            if record["recorded_at"] != validated["created_at"]:
                raise ContractError("scenario created_at must equal its append time")
            state.scenarios[bundle_id] = validated
            scenarios_in_order.append(validated)
        elif event_type == "scenario_resolved":
            if state.experiment is None:
                raise ContractError("scenario_resolved precedes experiment_started")
            bundle_id = payload.get("bundle_id")
            if bundle_id not in state.scenarios:
                raise ContractError(f"resolution references unknown bundle_id: {bundle_id}")
            if bundle_id in state.resolutions:
                raise ContractError(f"duplicate resolution for bundle_id: {bundle_id}")
            validated = _validate_resolution_payload(
                payload, state.experiment, state.scenarios[bundle_id]
            )
            if record["recorded_at"] != validated["resolved_at"]:
                raise ContractError("resolved_at must equal its append time")
            state.resolutions[bundle_id] = validated
        else:
            raise ContractError(f"unknown event_type: {event_type}")
    return state


def load_state(path: str | os.PathLike[str]) -> LedgerState:
    records = _load_records(Path(path))
    return _build_state(records)


def _append_event(
    path: Path, event_type: str, payload: dict[str, Any], recorded_at: datetime
) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with _ledger_lock(path):
        records = _load_records(path)
        if records:
            last_time = _as_datetime(records[-1]["recorded_at"], "last recorded_at")
            if recorded_at < last_time:
                raise ContractError(
                    "append clock moved behind the current ledger head; retry with a "
                    "monotonic clock"
                )
        sequence = len(records) + 1
        unhashed = {
            "schema_version": SCHEMA_VERSION,
            "sequence": sequence,
            "event_type": event_type,
            "recorded_at": _iso(recorded_at, "recorded_at"),
            "prev_hash": records[-1]["record_hash"] if records else GENESIS_HASH,
            "payload": payload,
        }
        record = dict(unhashed)
        record["record_hash"] = _record_hash(unhashed)
        _build_state([*records, record])
        encoded = (_canonical_json(record) + "\n").encode("utf-8")
        with path.open("ab") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        return record


def start_experiment(
    ledger_path: str | os.PathLike[str],
    *,
    experiment_id: str,
    end_at: datetime | str,
    assignment_key: bytes,
    max_bundles: int = 120,
    visible_fraction: float = 0.5,
    resolution_grace_days: int = 14,
    promotion_gates: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    clock = _now(now)
    path = Path(ledger_path)
    if path.exists() and path.stat().st_size:
        raise LedgerError("start_experiment requires an empty ledger path")
    gates = _normalize_gates(promotion_gates)
    max_bundles = _integer(max_bundles, "max_bundles", minimum=4)
    if gates["min_resolved_bundles"] > max_bundles:
        raise ContractError("min_resolved_bundles cannot exceed max_bundles")
    payload = {
        "experiment_id": _safe_id(experiment_id, "experiment_id"),
        "created_at": _iso(clock),
        "end_at": _iso(end_at, "end_at"),
        "max_bundles": max_bundles,
        "visible_fraction": _finite_number(visible_fraction, "visible_fraction"),
        "resolution_grace_days": _integer(
            resolution_grace_days, "resolution_grace_days"
        ),
        "assignment_key_id": assignment_key_id(assignment_key),
        "promotion_gates": gates,
        "shadow_only": True,
        "research_translation_version": PLANNING_MECHANISM_VERSION,
    }
    record = _append_event(path, "experiment_started", payload, clock)
    return record["payload"]


def create_scenario(
    ledger_path: str | os.PathLike[str],
    data: Mapping[str, Any],
    *,
    assignment_key: bytes,
    now: datetime | None = None,
) -> dict[str, Any]:
    required = {
        "bundle_id",
        "unit_id",
        "unit_source_slug",
        "author_id",
        "evidence_cutoff_at",
        "work_not_started_attestation",
        "trajectories",
    }
    _require_exact_keys(data, required, "scenario input")
    state = load_state(ledger_path)
    if state.experiment is None:
        raise LedgerError("experiment has not been initialized")
    experiment = state.experiment
    clock = _now(now)
    if clock > _as_datetime(experiment["end_at"], "experiment.end_at"):
        raise ContractError("bounded experiment window is closed to new scenarios")
    if len(state.scenarios) >= experiment["max_bundles"]:
        raise ContractError("bounded experiment has reached max_bundles")
    if assignment_key_id(assignment_key) != experiment["assignment_key_id"]:
        raise ContractError("assignment key does not match experiment key_id")
    bundle_id = _safe_id(data["bundle_id"], "bundle_id")
    if bundle_id in state.scenarios:
        raise ContractError(f"duplicate scenario bundle_id: {bundle_id}")
    unit_id = _safe_id(data["unit_id"], "unit_id")
    trajectories = normalize_trajectories(data["trajectories"])
    all_sources = sorted({slug for row in trajectories for slug in row["source_slugs"]})
    scar = trajectories[1]
    assignment = assignment_for(
        assignment_key, experiment["experiment_id"], unit_id, experiment["visible_fraction"]
    )
    payload = {
        "experiment_id": experiment["experiment_id"],
        "bundle_id": bundle_id,
        "unit_id": unit_id,
        "unit_source_slug": _safe_slug(data["unit_source_slug"], "unit_source_slug"),
        "author_id": _safe_id(data["author_id"], "author_id"),
        "created_at": _iso(clock),
        "evidence_cutoff_at": _iso(data["evidence_cutoff_at"], "evidence_cutoff_at"),
        "work_not_started_attestation": data["work_not_started_attestation"],
        "trajectories": trajectories,
        "trajectory_set_digest": _digest(trajectories),
        "source_set_digest": _digest(all_sources),
        "planning_policy": {
            "rollout_budget": 3,
            "scar_replay_priority": round(
                scar["probability"] * scar["severity"], 9
            ),
            "priority_rule": "probability_x_severity",
            "mechanism_version": PLANNING_MECHANISM_VERSION,
        },
        "assignment": assignment,
    }
    record = _append_event(Path(ledger_path), "scenario_created", payload, clock)
    return actor_receipt(record["payload"])


def actor_receipt(scenario: Mapping[str, Any]) -> dict[str, Any]:
    """Return the only actor-facing view; hidden content and arm labels stay sealed."""
    common: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "bundle_id": scenario["bundle_id"],
        "created_at": scenario["created_at"],
        "evidence_cutoff_at": scenario["evidence_cutoff_at"],
        "assignment_commitment": scenario["assignment"]["commitment"],
        "shadow_only": True,
        "scenario_bundle": None,
    }
    if scenario["assignment"]["arm"] == "visible":
        common["scenario_bundle"] = {
            "trajectory_set_digest": scenario["trajectory_set_digest"],
            "source_set_digest": scenario["source_set_digest"],
            "planning_policy": scenario["planning_policy"],
            "trajectories": scenario["trajectories"],
        }
    return common


def adjudication_template(
    ledger_path: str | os.PathLike[str], bundle_id: str
) -> dict[str, Any]:
    """Reveal frozen outcomes after work without revealing randomized arm."""
    state = load_state(ledger_path)
    bundle_id = _safe_id(bundle_id, "bundle_id")
    if bundle_id not in state.scenarios:
        raise ContractError(f"unknown bundle_id: {bundle_id}")
    scenario = state.scenarios[bundle_id]
    return {
        "schema_version": SCHEMA_VERSION,
        "bundle_id": bundle_id,
        "created_at": scenario["created_at"],
        "evidence_cutoff_at": scenario["evidence_cutoff_at"],
        "trajectory_set_digest": scenario["trajectory_set_digest"],
        "trajectories": scenario["trajectories"],
        "required_blinded_outcome_fields": [
            "work_started_at",
            "observed_kind",
            "adjudicator_id",
            "adjudicator_blinded_to_arm",
            "guarded_error_eligible",
            "guarded_error_recurred",
            "critical_harm",
            "outcome_evidence_slugs",
        ],
        "allocator_exposure_fields": [
            "guard_followed",
            "planning_overhead_seconds",
        ],
    }


def resolve_scenario(
    ledger_path: str | os.PathLike[str],
    data: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    required = {
        "bundle_id",
        "work_started_at",
        "observed_kind",
        "adjudicator_id",
        "adjudicator_blinded_to_arm",
        "guarded_error_eligible",
        "guarded_error_recurred",
        "guard_followed",
        "planning_overhead_seconds",
        "critical_harm",
        "outcome_evidence_slugs",
    }
    _require_exact_keys(data, required, "resolution input")
    state = load_state(ledger_path)
    if state.experiment is None:
        raise LedgerError("experiment has not been initialized")
    bundle_id = _safe_id(data["bundle_id"], "bundle_id")
    if bundle_id not in state.scenarios:
        raise ContractError(f"unknown bundle_id: {bundle_id}")
    if bundle_id in state.resolutions:
        raise ContractError(f"bundle_id is already resolved: {bundle_id}")
    scenario = state.scenarios[bundle_id]
    experiment = state.experiment
    clock = _now(now)
    created = _as_datetime(scenario["created_at"], "scenario.created_at")
    deadline = _as_datetime(experiment["end_at"], "experiment.end_at") + timedelta(
        days=experiment["resolution_grace_days"]
    )
    arm = scenario["assignment"]["arm"]
    overhead = _finite_number(
        data["planning_overhead_seconds"], "planning_overhead_seconds"
    )
    payload = {
        "experiment_id": experiment["experiment_id"],
        "bundle_id": bundle_id,
        "resolved_at": _iso(clock),
        "work_started_at": _iso(data["work_started_at"], "work_started_at"),
        "observed_kind": data["observed_kind"],
        "adjudicator_id": _safe_id(data["adjudicator_id"], "adjudicator_id"),
        "adjudicator_blinded_to_arm": data["adjudicator_blinded_to_arm"],
        "guarded_error_eligible": data["guarded_error_eligible"],
        "guarded_error_recurred": data["guarded_error_recurred"],
        "guard_followed": data["guard_followed"],
        "guard_exposed": arm == "visible",
        "planning_overhead_seconds": overhead,
        "critical_harm": data["critical_harm"],
        "outcome_evidence_slugs": _slug_list(
            data["outcome_evidence_slugs"], "outcome_evidence_slugs"
        ),
        "resolution_lag_seconds": (clock - created).total_seconds(),
        "adjudication_late": clock > deadline,
    }
    record = _append_event(Path(ledger_path), "scenario_resolved", payload, clock)
    return {
        "schema_version": SCHEMA_VERSION,
        "bundle_id": bundle_id,
        "resolved_at": record["payload"]["resolved_at"],
        "record_hash": record["record_hash"],
        "shadow_only": True,
    }


def score_resolution(
    scenario: Mapping[str, Any], resolution: Mapping[str, Any]
) -> dict[str, Any]:
    probabilities = {row["kind"]: row["probability"] for row in scenario["trajectories"]}
    observed = resolution["observed_kind"]
    brier = math.fsum(
        (probability - (1.0 if kind == observed else 0.0)) ** 2
        for kind, probability in probabilities.items()
    )
    log_loss = -math.log(max(probabilities[observed], MIN_PROBABILITY))
    return {
        "bundle_id": scenario["bundle_id"],
        "observed_kind": observed,
        "brier": brier,
        "log_loss_nats": log_loss,
    }


def _mean(values: Sequence[float]) -> float | None:
    return math.fsum(values) / len(values) if values else None


def _cluster_bootstrap_difference(
    visible: Sequence[float], hidden: Sequence[float], seed_text: str
) -> tuple[float | None, list[float] | None]:
    if not visible or not hidden:
        return None, None
    point = float(_mean(visible) - _mean(hidden))
    if len(visible) < 2 or len(hidden) < 2:
        return point, None
    seed = int(hashlib.sha256(seed_text.encode("utf-8")).hexdigest()[:16], 16)
    rng = random.Random(seed)
    samples: list[float] = []
    for _ in range(BOOTSTRAP_SAMPLES):
        v = math.fsum(rng.choice(visible) for _ in visible) / len(visible)
        h = math.fsum(rng.choice(hidden) for _ in hidden) / len(hidden)
        samples.append(v - h)
    samples.sort()
    lo = samples[int(0.025 * (len(samples) - 1))]
    hi = samples[int(0.975 * (len(samples) - 1))]
    return point, [lo, hi]


def _gate(name: str, passed: bool, actual: Any, relation: str, required: Any) -> dict[str, Any]:
    return {
        "name": name,
        "passed": bool(passed),
        "actual": actual,
        "relation": relation,
        "required": required,
    }


def experiment_report(
    ledger_path: str | os.PathLike[str], *, now: datetime | None = None
) -> dict[str, Any]:
    state = load_state(ledger_path)
    if state.experiment is None:
        raise LedgerError("experiment has not been initialized")
    experiment = state.experiment
    clock = _now(now)
    created_at = _as_datetime(experiment["created_at"], "experiment.created_at")
    if clock < created_at:
        raise ContractError("report clock predates experiment creation")
    if state.records and clock < _as_datetime(
        state.records[-1]["recorded_at"], "ledger head recorded_at"
    ):
        raise ContractError("report clock predates the current ledger head")
    elapsed_days = (clock - created_at).total_seconds() / 86400.0
    enrollment_closed = (
        clock >= _as_datetime(experiment["end_at"], "experiment.end_at")
        or len(state.scenarios) >= experiment["max_bundles"]
    )

    scores: list[dict[str, Any]] = []
    resolved_rows: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for bundle_id, resolution in state.resolutions.items():
        scenario = state.scenarios[bundle_id]
        scores.append(score_resolution(scenario, resolution))
        resolved_rows.append((scenario, resolution))
    briers = [row["brier"] for row in scores]
    logs = [row["log_loss_nats"] for row in scores]
    mean_brier = _mean(briers)
    mean_log = _mean(logs)
    score_by_unit: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for score in scores:
        score_by_unit[state.scenarios[score["bundle_id"]]["unit_id"]].append(score)
    unit_briers = [
        float(_mean([score["brier"] for score in unit_scores]))
        for unit_scores in score_by_unit.values()
    ]
    unit_logs = [
        float(_mean([score["log_loss_nats"] for score in unit_scores]))
        for unit_scores in score_by_unit.values()
    ]
    unit_mean_brier = _mean(unit_briers)
    unit_mean_log = _mean(unit_logs)

    created_by_arm = Counter(
        scenario["assignment"]["arm"] for scenario in state.scenarios.values()
    )
    resolved_by_arm = Counter(scenario["assignment"]["arm"] for scenario, _ in resolved_rows)
    eligible_events: dict[str, list[tuple[str, bool]]] = {"visible": [], "hidden": []}
    unit_counts = Counter()
    scar_source_units: dict[str, set[str]] = defaultdict(set)
    visible_overheads: list[float] = []
    harms = 0
    late = 0
    for scenario, resolution in resolved_rows:
        arm = scenario["assignment"]["arm"]
        unit_id = scenario["unit_id"]
        unit_counts[unit_id] += 1
        for slug in scenario["trajectories"][1]["scar_source_slugs"]:
            scar_source_units[slug].add(unit_id)
        if arm == "visible":
            visible_overheads.append(resolution["planning_overhead_seconds"])
        harms += int(resolution["critical_harm"])
        late += int(resolution["adjudication_late"])
        if resolution["guarded_error_eligible"]:
            eligible_events[arm].append(
                (unit_id, bool(resolution["guarded_error_recurred"]))
            )

    unit_rates: dict[str, list[float]] = {"visible": [], "hidden": []}
    arm_metrics: dict[str, Any] = {}
    for arm in ("visible", "hidden"):
        grouped: dict[str, list[bool]] = defaultdict(list)
        for unit_id, recurred in eligible_events[arm]:
            grouped[unit_id].append(recurred)
        rates = [sum(values) / len(values) for values in grouped.values()]
        unit_rates[arm] = rates
        event_n = len(eligible_events[arm])
        event_recurred = sum(int(value) for _, value in eligible_events[arm])
        arm_metrics[arm] = {
            "created_bundles": created_by_arm[arm],
            "resolved_bundles": resolved_by_arm[arm],
            "eligible_events": event_n,
            "recurrent_events": event_recurred,
            "event_recurrence_rate": event_recurred / event_n if event_n else None,
            "eligible_units": len(grouped),
            "unit_balanced_recurrence_rate": _mean(rates),
        }
    risk_difference, risk_ci = _cluster_bootstrap_difference(
        unit_rates["visible"], unit_rates["hidden"], experiment["experiment_id"]
    )

    n_resolved = len(resolved_rows)
    n_created = len(state.scenarios)
    resolution_deadline = _as_datetime(
        experiment["end_at"], "experiment.end_at"
    ) + timedelta(days=experiment["resolution_grace_days"])
    outcome_collection_finalized = (
        clock >= resolution_deadline
        or (enrollment_closed and n_created > 0 and n_resolved == n_created)
    )
    resolution_fraction = n_resolved / n_created if n_created else 0.0
    arm_resolution_rates = {
        arm: (
            resolved_by_arm[arm] / created_by_arm[arm]
            if created_by_arm[arm]
            else None
        )
        for arm in ("visible", "hidden")
    }
    if all(rate is not None for rate in arm_resolution_rates.values()):
        arm_resolution_rate_gap = abs(
            arm_resolution_rates["visible"] - arm_resolution_rates["hidden"]
        )
    else:
        arm_resolution_rate_gap = None
    independent_units = len(unit_counts)
    largest_share = max(unit_counts.values(), default=0) / n_resolved if n_resolved else 0.0
    distinct_scar_sources = len(scar_source_units)
    reused_scar_sources = sum(1 for units in scar_source_units.values() if len(units) >= 2)
    mean_visible_overhead = _mean(visible_overheads)
    late_fraction = late / n_resolved if n_resolved else 0.0
    gates = experiment["promotion_gates"]

    gate_rows = [
        _gate(
            "enrollment_closed",
            enrollment_closed,
            enrollment_closed,
            "==",
            True,
        ),
        _gate(
            "outcome_collection_finalized",
            outcome_collection_finalized,
            outcome_collection_finalized,
            "==",
            True,
        ),
        _gate(
            "min_experiment_days",
            elapsed_days >= gates["min_experiment_days"],
            elapsed_days,
            ">=",
            gates["min_experiment_days"],
        ),
        _gate(
            "min_resolved_bundles",
            n_resolved >= gates["min_resolved_bundles"],
            n_resolved,
            ">=",
            gates["min_resolved_bundles"],
        ),
        _gate(
            "min_independent_units",
            independent_units >= gates["min_independent_units"],
            independent_units,
            ">=",
            gates["min_independent_units"],
        ),
        _gate(
            "min_resolution_fraction",
            resolution_fraction >= gates["min_resolution_fraction"],
            resolution_fraction,
            ">=",
            gates["min_resolution_fraction"],
        ),
        _gate(
            "min_visible_eligible_units",
            len(unit_rates["visible"]) >= gates["min_eligible_units_per_arm"],
            len(unit_rates["visible"]),
            ">=",
            gates["min_eligible_units_per_arm"],
        ),
        _gate(
            "min_hidden_eligible_units",
            len(unit_rates["hidden"]) >= gates["min_eligible_units_per_arm"],
            len(unit_rates["hidden"]),
            ">=",
            gates["min_eligible_units_per_arm"],
        ),
        _gate(
            "max_largest_unit_share",
            largest_share <= gates["max_largest_unit_share"],
            largest_share,
            "<=",
            gates["max_largest_unit_share"],
        ),
        _gate(
            "max_arm_resolution_rate_gap",
            arm_resolution_rate_gap is not None
            and arm_resolution_rate_gap <= gates["max_arm_resolution_rate_gap"],
            {
                "gap": arm_resolution_rate_gap,
                "rates": arm_resolution_rates,
            },
            "<=",
            gates["max_arm_resolution_rate_gap"],
        ),
        _gate(
            "max_mean_brier",
            unit_mean_brier is not None
            and unit_mean_brier <= gates["max_mean_brier"],
            unit_mean_brier,
            "<=",
            gates["max_mean_brier"],
        ),
        _gate(
            "max_mean_log_loss",
            unit_mean_log is not None
            and unit_mean_log <= gates["max_mean_log_loss"],
            unit_mean_log,
            "<=",
            gates["max_mean_log_loss"],
        ),
        _gate(
            "max_visible_overhead_seconds",
            mean_visible_overhead is not None
            and mean_visible_overhead <= gates["max_visible_overhead_seconds"],
            mean_visible_overhead,
            "<=",
            gates["max_visible_overhead_seconds"],
        ),
        _gate(
            "min_distinct_scar_sources",
            distinct_scar_sources >= gates["min_distinct_scar_sources"],
            distinct_scar_sources,
            ">=",
            gates["min_distinct_scar_sources"],
        ),
        _gate(
            "min_reused_scar_sources",
            reused_scar_sources >= gates["min_reused_scar_sources"],
            reused_scar_sources,
            ">=",
            gates["min_reused_scar_sources"],
        ),
        _gate(
            "max_critical_harms",
            harms <= gates["max_critical_harms"],
            harms,
            "<=",
            gates["max_critical_harms"],
        ),
        _gate(
            "max_late_resolution_fraction",
            late_fraction <= gates["max_late_resolution_fraction"],
            late_fraction,
            "<=",
            gates["max_late_resolution_fraction"],
        ),
        _gate(
            "guarded_error_recurrence_benefit",
            risk_ci is not None
            and risk_ci[1] < gates["recurrence_ci_upper_below"],
            {"risk_difference": risk_difference, "bootstrap_95_ci": risk_ci},
            "ci_upper<",
            gates["recurrence_ci_upper_below"],
        ),
    ]
    all_pass = all(row["passed"] for row in gate_rows)
    harm_failed = not next(
        row for row in gate_rows if row["name"] == "max_critical_harms"
    )["passed"]
    adverse_recurrence_signal = (
        risk_ci is not None
        and risk_ci[0] > 0.0
        and len(unit_rates["visible"]) >= gates["min_eligible_units_per_arm"]
        and len(unit_rates["hidden"]) >= gates["min_eligible_units_per_arm"]
    )
    if all_pass:
        verdict = "PROMOTION_CANDIDATE"
    elif harm_failed or adverse_recurrence_signal or outcome_collection_finalized:
        verdict = "HOLD"
    else:
        verdict = "DATA_GATED"

    return {
        "schema_version": SCHEMA_VERSION,
        "experiment_id": experiment["experiment_id"],
        "shadow_only": True,
        "created_bundles": n_created,
        "resolved_bundles": n_resolved,
        "unresolved_bundles": len(state.scenarios) - n_resolved,
        "independent_units": independent_units,
        "elapsed_days": elapsed_days,
        "experiment_closed": enrollment_closed,
        "outcome_collection_finalized": outcome_collection_finalized,
        "resolution_deadline_at": _iso(resolution_deadline),
        "forecast": {
            "mean_brier": mean_brier,
            "mean_log_loss_nats": mean_log,
            "unit_balanced_mean_brier": unit_mean_brier,
            "unit_balanced_mean_log_loss_nats": unit_mean_log,
            "uniform_three_class_brier": 2.0 / 3.0,
            "uniform_three_class_log_loss_nats": math.log(3.0),
            "scores": scores,
        },
        "guarded_error": {
            "arms": arm_metrics,
            "visible_minus_hidden_unit_balanced_risk_difference": risk_difference,
            "cluster_bootstrap_95_ci": risk_ci,
        },
        "planning_cost": {"mean_visible_overhead_seconds": mean_visible_overhead},
        "provenance_reuse": {
            "distinct_scar_sources": distinct_scar_sources,
            "reused_across_units": reused_scar_sources,
        },
        "quality": {
            "resolution_fraction": resolution_fraction,
            "arm_resolution_rates": arm_resolution_rates,
            "arm_resolution_rate_gap": arm_resolution_rate_gap,
            "largest_unit_share": largest_share,
            "critical_harms": harms,
            "late_resolutions": late,
            "late_resolution_fraction": late_fraction,
            "adverse_recurrence_signal": adverse_recurrence_signal,
        },
        "promotion": {
            "verdict": verdict,
            "manual_review_required": True,
            "automatic_integration_permitted": False,
            "gates": gate_rows,
        },
    }


def verify_ledger(
    ledger_path: str | os.PathLike[str], *, assignment_key: bytes | None = None
) -> dict[str, Any]:
    path = Path(ledger_path)
    lock_path = Path(str(path) + ".lock")
    if lock_path.exists():
        raise LedgerError("ledger is currently locked by a writer; verify after it exits")
    state = load_state(path)
    if lock_path.exists():
        raise LedgerError("ledger changed under verification; retry after the writer exits")
    if state.experiment is None:
        raise LedgerError("ledger has no experiment to verify")
    assignment_status = "not_checked"
    if assignment_key is not None:
        experiment = state.experiment
        if assignment_key_id(assignment_key) != experiment["assignment_key_id"]:
            raise ContractError("assignment key does not match experiment key_id")
        for scenario in state.scenarios.values():
            expected = assignment_for(
                assignment_key,
                experiment["experiment_id"],
                scenario["unit_id"],
                experiment["visible_fraction"],
            )
            if scenario["assignment"] != expected:
                raise LedgerError(
                    f"assignment verification failed for {scenario['bundle_id']}"
                )
        assignment_status = "verified"
    return {
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "records": len(state.records),
        "head_hash": state.records[-1]["record_hash"] if state.records else GENESIS_HASH,
        "experiment_id": state.experiment["experiment_id"],
        "scenarios": len(state.scenarios),
        "resolutions": len(state.resolutions),
        "assignment_status": assignment_status,
        "integrity_scope": (
            "hash-chain and semantic-contract; archive head_hash to detect tail truncation"
        ),
    }


def _read_json(path: str | os.PathLike[str]) -> dict[str, Any]:
    source = Path(path)
    if source.stat().st_size > 1_000_000:
        raise ContractError("input JSON is larger than 1 MB")
    value = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ContractError("input JSON must be an object")
    return value


def _print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="start one bounded shadow experiment")
    init.add_argument("--ledger", required=True)
    init.add_argument("--experiment-id", required=True)
    init.add_argument("--end-at", required=True, help="timezone-aware ISO-8601 deadline")
    init.add_argument("--assignment-key-file", required=True)
    init.add_argument("--max-bundles", type=int, default=120)
    init.add_argument("--visible-fraction", type=float, default=0.5)
    init.add_argument("--resolution-grace-days", type=int, default=14)
    init.add_argument("--gates-json", help="optional JSON object overriding promotion gates")

    create = sub.add_parser("create", help="freeze and randomize a pre-work scenario bundle")
    create.add_argument("--ledger", required=True)
    create.add_argument("--input", required=True)
    create.add_argument("--assignment-key-file", required=True)

    template = sub.add_parser(
        "adjudication-template", help="show frozen trajectories without assignment arm"
    )
    template.add_argument("--ledger", required=True)
    template.add_argument("--bundle-id", required=True)

    resolve = sub.add_parser("resolve", help="append a later blinded adjudication")
    resolve.add_argument("--ledger", required=True)
    resolve.add_argument("--input", required=True)

    report = sub.add_parser("report", help="score forecasts, recurrence, and promotion gates")
    report.add_argument("--ledger", required=True)

    verify = sub.add_parser("verify", help="verify ledger chain and semantic contracts")
    verify.add_argument("--ledger", required=True)
    verify.add_argument("--assignment-key-file")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "init":
            gate_overrides = _read_json(args.gates_json) if args.gates_json else None
            value = start_experiment(
                args.ledger,
                experiment_id=args.experiment_id,
                end_at=args.end_at,
                assignment_key=read_assignment_key(args.assignment_key_file),
                max_bundles=args.max_bundles,
                visible_fraction=args.visible_fraction,
                resolution_grace_days=args.resolution_grace_days,
                promotion_gates=gate_overrides,
            )
        elif args.command == "create":
            value = create_scenario(
                args.ledger,
                _read_json(args.input),
                assignment_key=read_assignment_key(args.assignment_key_file),
            )
        elif args.command == "adjudication-template":
            value = adjudication_template(args.ledger, args.bundle_id)
        elif args.command == "resolve":
            value = resolve_scenario(args.ledger, _read_json(args.input))
        elif args.command == "report":
            value = experiment_report(args.ledger)
        elif args.command == "verify":
            key = (
                read_assignment_key(args.assignment_key_file)
                if args.assignment_key_file
                else None
            )
            value = verify_ledger(args.ledger, assignment_key=key)
        else:  # pragma: no cover - argparse makes this unreachable
            raise ContractError(f"unknown command: {args.command}")
        _print_json(value)
        return 0
    except (ProspectiveControlError, OSError, json.JSONDecodeError) as exc:
        print(f"prospective_control: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
