"""Fail-closed calibration receipts for real local ProofSwarm runs.

Each surface must pass four synthetic controls with every decision-critical
role.  Receipts bind the exact local model, loopback endpoint, full prompt
bundle, control bundle, raw role outputs, and a 24-hour validity window.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Callable, Mapping

from proofswarm.config import CALIBRATION_DIR, LOCAL_BASE_URL, LOCAL_MODEL
from proofswarm.local_runtime import LocalPolicyError


SCHEMA_VERSION = 1
MAX_AGE = timedelta(hours=24)
_SURFACE_RE = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")

CONTROL_CASES: dict[str, dict[str, object]] = {
    "known_sound": {
        "claim": "Integer equality: 17 + 17 = 34.",
        "expected": {
            "control_id": "known_sound",
            "classification": "sound",
            "reason_code": "equality_holds",
            "lhs": 34,
            "rhs": 34,
        },
    },
    "known_false": {
        "claim": "Integer equality: 2 * 3 = 5.",
        "expected": {
            "control_id": "known_false",
            "classification": "false",
            "reason_code": "equality_fails",
            "lhs": 6,
            "rhs": 5,
        },
    },
    "boundary": {
        "claim": "For every integer n with 0 <= n <= 0, n * n = 0. Check n = 0.",
        "expected": {
            "control_id": "boundary",
            "classification": "sound",
            "reason_code": "boundary_checked",
            "lhs": 0,
            "rhs": 0,
        },
    },
    "malformed": {
        "claim": "For every x, f(x) = 0. No domain for x and no definition of f are supplied.",
        "expected": {
            "control_id": "malformed",
            "classification": "malformed",
            "reason_code": "undefined_symbols",
            "lhs": None,
            "rhs": None,
        },
    },
}

SURFACE_CALIBRATION_ROLES: dict[str, tuple[str, ...]] = {
    "orchestrator": ("navigator", "formalizer", "prover", "counterexample", "referee"),
    "paper_review": (
        "statement_auditor",
        "proof_auditor",
        "boundary_case_tester",
        "formalizer",
        "prover",
        "counterexample_hunter",
        "referee",
    ),
    "autonomous": (
        "strategy_proposer",
        "lemma_decomposer",
        "formalizer",
        "prover",
        "persona_adversarial",
        "referee",
    ),
}
CALIBRATION_SURFACES = tuple(SURFACE_CALIBRATION_ROLES)

ModelInvoke = Callable[[str, str, str], tuple[str, float]]


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def _sha256(value: object) -> str:
    raw = value if isinstance(value, bytes) else _canonical_bytes(value)
    return hashlib.sha256(raw).hexdigest()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _parse_time(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise LocalPolicyError(f"calibration {field} is missing")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise LocalPolicyError(f"calibration {field} is invalid") from exc
    if parsed.tzinfo is None:
        raise LocalPolicyError(f"calibration {field} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def surface_prompts(surface: str) -> dict[str, str]:
    """Load the complete prompt bundle used by one execution surface."""

    if surface == "orchestrator":
        from proofswarm.prompts import PROMPTS

        prompts = dict(PROMPTS)
    elif surface == "paper_review":
        from proofswarm.prompts_v2 import PROMPTS_V2

        prompts = dict(PROMPTS_V2)
    elif surface == "autonomous":
        from proofswarm.prompts_creative import PROMPTS_CREATIVE
        from proofswarm.prompts_personas import PROMPTS_PERSONAS
        from proofswarm.prompts_v2 import PROMPTS_V2

        prompts = {**PROMPTS_V2, **PROMPTS_CREATIVE, **PROMPTS_PERSONAS}
    else:
        raise LocalPolicyError(f"unknown ProofSwarm calibration surface: {surface!r}")
    if any(not isinstance(role, str) or not isinstance(prompt, str) for role, prompt in prompts.items()):
        raise LocalPolicyError("calibration prompt bundle contains a non-string entry")
    return prompts


def _prompt_digest(surface: str, prompts: Mapping[str, str], roles: tuple[str, ...]) -> str:
    return _sha256(
        {
            "surface": surface,
            "local_model": LOCAL_MODEL,
            "local_base_url": LOCAL_BASE_URL,
            "roles": sorted(roles),
            "prompts": {role: prompts[role] for role in sorted(prompts)},
        }
    )


def _control_digest() -> str:
    return _sha256(CONTROL_CASES)


def _control_message(control_id: str, case: Mapping[str, object]) -> str:
    return json.dumps(
        {
            "task": "proofswarm_calibration_control",
            "control_id": control_id,
            "claim": case["claim"],
            "instructions": (
                "Return only one JSON object. Use classification sound, false, or malformed; "
                "use reason_code equality_holds, equality_fails, boundary_checked, or "
                "undefined_symbols; compute lhs and rhs as integers or null. Do not add keys."
            ),
            "response_keys": ["control_id", "classification", "reason_code", "lhs", "rhs"],
        },
        sort_keys=True,
    )


def _evaluate_output(control_id: str, output: str) -> tuple[bool, str]:
    try:
        parsed = json.loads(output)
    except (json.JSONDecodeError, TypeError):
        return False, "output is not a bare JSON object"
    expected = CONTROL_CASES[control_id]["expected"]
    if not isinstance(parsed, dict) or parsed != expected:
        return False, "structured answer disagrees with the mechanical control witness"
    return True, "mechanical control matched"


def _receipt_digest(receipt: Mapping[str, object]) -> str:
    return _sha256({key: value for key, value in receipt.items() if key != "run_sha256"})


def build_calibration_receipt(
    surface: str,
    prompts: Mapping[str, str],
    roles: tuple[str, ...],
    invoke: ModelInvoke,
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    """Run controls serially and retain exact role evidence, stopping at first failure."""

    if not _SURFACE_RE.fullmatch(surface):
        raise LocalPolicyError("invalid calibration surface name")
    if not roles or any(role not in prompts for role in roles):
        raise LocalPolicyError("calibration roles are empty or absent from the prompt bundle")
    created = (now or _utc_now()).astimezone(timezone.utc)
    results: list[dict[str, object]] = []
    failed = False
    for role in roles:
        for control_id, case in CONTROL_CASES.items():
            user = _control_message(control_id, case)
            try:
                output, cost = invoke(role, prompts[role], user)
                if not isinstance(output, str):
                    raise TypeError("model output is not text")
                passed, reason = _evaluate_output(control_id, output)
                if isinstance(cost, bool) or not isinstance(cost, (int, float)) or float(cost) != 0.0:
                    passed = False
                    reason = "calibration call did not report zero marginal API cost"
                result = {
                    "role": role,
                    "control_id": control_id,
                    "input_sha256": hashlib.sha256(user.encode("utf-8")).hexdigest(),
                    "output": output,
                    "output_sha256": hashlib.sha256(output.encode("utf-8")).hexdigest(),
                    "cost_usd": float(cost),
                    "passed": passed,
                    "reason": reason,
                }
            except Exception as exc:  # noqa: BLE001 - preserve failure evidence, then stop
                result = {
                    "role": role,
                    "control_id": control_id,
                    "input_sha256": hashlib.sha256(user.encode("utf-8")).hexdigest(),
                    "output": "",
                    "output_sha256": hashlib.sha256(b"").hexdigest(),
                    "cost_usd": None,
                    "passed": False,
                    "reason": f"required calibration worker failed: {type(exc).__name__}",
                }
            results.append(result)
            if result["passed"] is not True:
                failed = True
                break
        if failed:
            break
    expected_count = len(roles) * len(CONTROL_CASES)
    receipt: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "policy": "local-only-zero-cloud-fallback",
        "surface": surface,
        "created_at": _iso(created),
        "expires_at": _iso(created + MAX_AGE),
        "local_model": LOCAL_MODEL,
        "local_base_url": LOCAL_BASE_URL,
        "roles": sorted(roles),
        "prompt_bundle_sha256": _prompt_digest(surface, prompts, roles),
        "control_bundle_sha256": _control_digest(),
        "results": results,
        "passed": not failed and len(results) == expected_count,
    }
    receipt["run_sha256"] = _receipt_digest(receipt)
    return receipt


def validate_calibration_receipt(
    receipt: Mapping[str, object],
    surface: str,
    prompts: Mapping[str, str],
    roles: tuple[str, ...],
    *,
    now: datetime | None = None,
) -> None:
    """Recompute every binding and control result; trust no receipt booleans."""

    if receipt.get("schema_version") != SCHEMA_VERSION:
        raise LocalPolicyError("calibration schema mismatch")
    if receipt.get("policy") != "local-only-zero-cloud-fallback":
        raise LocalPolicyError("calibration policy mismatch")
    if receipt.get("surface") != surface:
        raise LocalPolicyError("calibration surface mismatch")
    if receipt.get("local_model") != LOCAL_MODEL or receipt.get("local_base_url") != LOCAL_BASE_URL:
        raise LocalPolicyError("calibration local model or endpoint changed")
    if receipt.get("roles") != sorted(roles):
        raise LocalPolicyError("calibration role set changed")
    if receipt.get("prompt_bundle_sha256") != _prompt_digest(surface, prompts, roles):
        raise LocalPolicyError("calibration prompt bundle changed")
    if receipt.get("control_bundle_sha256") != _control_digest():
        raise LocalPolicyError("calibration control bundle changed")
    if receipt.get("run_sha256") != _receipt_digest(receipt):
        raise LocalPolicyError("calibration receipt digest mismatch")

    current = (now or _utc_now()).astimezone(timezone.utc)
    created = _parse_time(receipt.get("created_at"), "created_at")
    expires = _parse_time(receipt.get("expires_at"), "expires_at")
    if created > current + timedelta(minutes=5):
        raise LocalPolicyError("calibration receipt is future-dated")
    if current - created > MAX_AGE or current >= expires or expires - created != MAX_AGE:
        raise LocalPolicyError("calibration receipt is stale or has an invalid validity window")

    results = receipt.get("results")
    if not isinstance(results, list):
        raise LocalPolicyError("calibration results are missing")
    by_key: dict[tuple[str, str], Mapping[str, object]] = {}
    for result in results:
        if not isinstance(result, dict):
            raise LocalPolicyError("calibration result is malformed")
        key = (str(result.get("role")), str(result.get("control_id")))
        if key in by_key:
            raise LocalPolicyError("calibration contains duplicate role controls")
        by_key[key] = result
    expected_keys = {(role, control_id) for role in roles for control_id in CONTROL_CASES}
    if set(by_key) != expected_keys:
        raise LocalPolicyError("calibration does not cover every required role and control")
    for role, control_id in sorted(expected_keys):
        result = by_key[(role, control_id)]
        user = _control_message(control_id, CONTROL_CASES[control_id])
        output = result.get("output")
        if not isinstance(output, str):
            raise LocalPolicyError("calibration output evidence is missing")
        if result.get("input_sha256") != hashlib.sha256(user.encode("utf-8")).hexdigest():
            raise LocalPolicyError("calibration input evidence mismatch")
        if result.get("output_sha256") != hashlib.sha256(output.encode("utf-8")).hexdigest():
            raise LocalPolicyError("calibration output evidence mismatch")
        passed, _reason = _evaluate_output(control_id, output)
        if not passed or result.get("passed") is not True or result.get("cost_usd") != 0.0:
            raise LocalPolicyError(f"calibration control failed for {role}/{control_id}")
    if receipt.get("passed") is not True:
        raise LocalPolicyError("calibration receipt is not passing")


def calibration_path(surface: str) -> Path:
    if not _SURFACE_RE.fullmatch(surface):
        raise LocalPolicyError("invalid calibration surface name")
    return CALIBRATION_DIR / f"{surface}.json"


def write_calibration_receipt(surface: str, receipt: Mapping[str, object]) -> Path:
    """Atomically replace one local receipt after retaining all exact outputs."""

    path = calibration_path(surface)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(receipt, indent=2, sort_keys=True, ensure_ascii=False).encode("utf-8")
    temp_path: Path | None = None
    try:
        with NamedTemporaryFile(mode="wb", dir=path.parent, prefix=".calibration-", delete=False) as handle:
            temp_path = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
    return path


def run_surface_calibration(surface: str, invoke: ModelInvoke) -> tuple[dict[str, object], Path]:
    prompts = surface_prompts(surface)
    roles = SURFACE_CALIBRATION_ROLES[surface]
    receipt = build_calibration_receipt(surface, prompts, roles, invoke)
    return receipt, write_calibration_receipt(surface, receipt)


def require_calibration_receipt(
    surface: str,
    prompts: Mapping[str, str],
    roles: tuple[str, ...],
    *,
    now: datetime | None = None,
) -> Mapping[str, object]:
    path = calibration_path(surface)
    if not path.is_file():
        raise LocalPolicyError(
            f"calibration HOLD: {surface} has no fresh local calibration receipt"
        )
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LocalPolicyError(f"calibration HOLD: {surface} receipt is unreadable") from exc
    if not isinstance(receipt, dict):
        raise LocalPolicyError(f"calibration HOLD: {surface} receipt is malformed")
    validate_calibration_receipt(receipt, surface, prompts, roles, now=now)
    return receipt


def require_surface_calibration(surface: str) -> Mapping[str, object]:
    return require_calibration_receipt(
        surface,
        surface_prompts(surface),
        SURFACE_CALIBRATION_ROLES[surface],
    )
