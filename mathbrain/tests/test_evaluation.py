from __future__ import annotations

import copy
import hashlib
import json
import os
from dataclasses import replace
from fractions import Fraction
from pathlib import Path

import pytest

import math_brain.evaluation as ev
from math_brain import codex_runner as codex_runner_module
from math_brain.benchmark import (
    SOLVER_VALID_FAULTS,
    BenchmarkItem,
    evaluate_expression,
    generate_item,
)
from math_brain.challenge_bank import (
    ReferenceCorpus,
    TraceVariant,
    assignment_variant,
    load_challenge_bank,
)
from math_brain.codex_artifact_io import (
    deserialize_codex_run_result,
    serialize_codex_run_result,
)
from math_brain.codex_runner import (
    DEFAULT_BASE_INSTRUCTIONS,
    DEFAULT_DEVELOPER_INSTRUCTIONS,
    AppServerProtocolError,
    CodexRunResult,
    ExecutableProvenance,
    app_server_command_tail_policy_digest,
    audit_app_server_messages,
)

ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC_EXECUTABLE_BYTES = b"MZ\x00\x00evaluation-test-native"
SYNTHETIC_EXECUTABLE_PATH = str(
    Path("C:/math-brain-tests/codex-evaluation.exe")
    if os.name == "nt"
    else Path("/math-brain-tests/codex-evaluation")
)
SYNTHETIC_EXECUTABLE_PROVENANCE = ExecutableProvenance(
    canonical_path=SYNTHETIC_EXECUTABLE_PATH,
    size_bytes=len(SYNTHETIC_EXECUTABLE_BYTES),
    sha256_digest="sha256:" + hashlib.sha256(SYNTHETIC_EXECUTABLE_BYTES).hexdigest(),
)


def digest(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode()).hexdigest()


def bytes_digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


_TEST_DESKTOP_PERMISSIONS = (
    "<synthetic_permissions>read-only fixture</synthetic_permissions>"
)
_TEST_DESKTOP_SKILLS_TEMPLATE = "\n".join(
    (
        "<synthetic_capabilities>",
        "synthetic capability one: <CODEX_HOME>/skills/one",
        "synthetic capability two: <CODEX_HOME>/skills/two",
        "synthetic capability three: <CODEX_HOME>/skills/three",
        "synthetic capability four: <CODEX_HOME>/skills/four",
        "synthetic capability five: <CODEX_HOME>/skills/five",
        "</synthetic_capabilities>",
    )
)
_TEST_DESKTOP_PRIMARY_AGENT = "synthetic primary-agent fixture"
_TEST_DESKTOP_MULTI_AGENT = "<multi_agent_mode>synthetic fixture</multi_agent_mode>"
_TEST_DESKTOP_ENVIRONMENT_TEMPLATE = "\n".join(
    (
        "<environment_context>",
        "  <current_date><CURRENT_DATE></current_date>",
        "  <timezone>Fixture/Zone</timezone>",
        "  <filesystem>synthetic restricted fixture</filesystem>",
        "</environment_context>",
    )
)
_TEST_DESKTOP_USER_AGENT_TEMPLATE = (
    "synthetic-evaluator/1 (TestOS 1.<WINDOWS_BUILD>; test_arch) fixture"
)
_TEST_DESKTOP_VERIFICATIONS = ["synthetic-verification-marker"]


@pytest.fixture(autouse=True)
def _use_synthetic_desktop_commitments(monkeypatch: pytest.MonkeyPatch) -> None:
    canonical_verifications = json.dumps(
        _TEST_DESKTOP_VERIFICATIONS,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    synthetic = {
        "_DESKTOP_PERMISSIONS_INPUT_DIGEST_V1": bytes_digest(
            _TEST_DESKTOP_PERMISSIONS.encode("utf-8")
        ),
        "_DESKTOP_SKILLS_NORMALIZED_DIGEST_V1": bytes_digest(
            _TEST_DESKTOP_SKILLS_TEMPLATE.encode("utf-8")
        ),
        "_DESKTOP_PRIMARY_AGENT_INPUT_DIGEST_V1": bytes_digest(
            _TEST_DESKTOP_PRIMARY_AGENT.encode("utf-8")
        ),
        "_DESKTOP_MULTI_AGENT_INPUT_DIGEST_V1": bytes_digest(
            _TEST_DESKTOP_MULTI_AGENT.encode("utf-8")
        ),
        "_DESKTOP_ENVIRONMENT_NORMALIZED_DIGEST_V1": bytes_digest(
            _TEST_DESKTOP_ENVIRONMENT_TEMPLATE.encode("utf-8")
        ),
        "_DESKTOP_USER_AGENT_NORMALIZED_DIGEST_V1": bytes_digest(
            _TEST_DESKTOP_USER_AGENT_TEMPLATE.encode("utf-8")
        ),
        "_DESKTOP_MODEL_VERIFICATIONS_DIGEST_V1": bytes_digest(
            canonical_verifications
        ),
    }
    for name, value in synthetic.items():
        monkeypatch.setattr(codex_runner_module, name, value)


def uuid7(label: str) -> str:
    value = hashlib.sha256(label.encode("utf-8")).hexdigest()
    return f"{value[:8]}-{value[8:12]}-7{value[13:16]}-8{value[17:20]}-{value[20:32]}"


def canonical_request_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def solver_valid_items(seed: str, count: int) -> tuple[BenchmarkItem, ...]:
    items: list[BenchmarkItem] = []
    for index in range(10_000):
        item = generate_item(seed, index)
        if item.fault.kind in SOLVER_VALID_FAULTS:
            items.append(item)
            if len(items) == count:
                return tuple(items)
    raise AssertionError("generator did not yield enough solver-valid benchmark items")


@pytest.fixture(scope="module")
def config() -> ev.EvaluationConfig:
    return ev.EvaluationConfig.from_toml(ROOT / "config" / "evaluation.toml")


def build_test_plan(
    config: ev.EvaluationConfig,
    *,
    manifest_label: str = "benchmark-manifest",
) -> ev.EvaluationPlan:
    items = solver_valid_items("sealed-evaluation-tests", 4)
    cases = tuple(
        ev.EvaluationCase(
            item=item,
            domain="synthetic-algebra",
            difficulty="controlled",
        )
        for item in items
    )
    packets = {
        case.case_id: (
            f"Frozen public checking packet for {case.case_id}" if index % 2 == 0 else None
        )
        for index, case in enumerate(cases)
    }
    reasons = {
        case_id: "retriever abstained below the frozen relevance threshold"
        for case_id, packet in packets.items()
        if packet is None
    }
    return ev.build_full_plan(
        cases,
        config=config,
        seeds=ev.DesignSeeds(
            order="order-seed-v1",
            variant="variant-seed-v1",
            pair="pair-seed-v1",
            condition="condition-seed-v1",
            bootstrap="bootstrap-seed-v1",
        ),
        benchmark_manifest_digest=digest(manifest_label),
        solver_prompt="Classify each supplied derivation and return only the required JSON.",
        brain_on_packets=packets,
        abstention_reasons=reasons,
    )


@pytest.fixture(scope="module")
def plan(config: ev.EvaluationConfig) -> ev.EvaluationPlan:
    return build_test_plan(config)


def _fraction_text(value: Fraction) -> str:
    numerator = value.numerator
    denominator = value.denominator
    return str(numerator) if denominator == 1 else f"{numerator}/{denominator}"


def _answer_for(
    plan: ev.EvaluationPlan,
    assignment: ev.Assignment,
    *,
    correct: bool,
) -> dict[str, object]:
    case = plan.cases[assignment.case_index]
    item = case.item
    truth_invalid = assignment.variant is ev.Variant.FAULTY
    predict_invalid = truth_invalid if correct else not truth_invalid
    if not predict_invalid:
        return {
            "case_id": assignment.case_id,
            "error_family": None,
            "first_invalid_line": None,
            "probability_invalid": 0.01,
            "repair": None,
            "view_digest": assignment.view_digest,
            "witness": None,
        }
    if truth_invalid:
        line = item.fault.step_number
        relation = item.faulty_solution.steps[line - 1].assertion
        left = evaluate_expression(relation.left, item.problem.witness_map)
        right = evaluate_expression(relation.right, item.problem.witness_map)
        return {
            "case_id": assignment.case_id,
            "error_family": item.fault.kind.value,
            "first_invalid_line": line,
            "probability_invalid": 0.99,
            "repair": {
                "replacement_line": item.clean_solution.steps[line - 1].text,
            },
            "view_digest": assignment.view_digest,
            "witness": {
                "left_value": _fraction_text(left),
                "right_value": _fraction_text(right),
            },
        }
    return {
        "case_id": assignment.case_id,
        "error_family": "false_positive",
        "first_invalid_line": 1,
        "probability_invalid": 0.99,
        "repair": None,
        "view_digest": assignment.view_digest,
        "witness": None,
    }


def response_payload(
    plan: ev.EvaluationPlan,
    pair_id: int,
    condition: ev.Condition,
    *,
    correct: bool,
) -> dict[str, object]:
    return {
        "answers": [
            _answer_for(plan, assignment, correct=correct)
            for assignment in plan.assignments_for(pair_id, condition)
        ],
        "schema": ev.RESPONSE_SCHEMA,
        "schema_version": ev.RESPONSE_SCHEMA_VERSION,
    }


def batch_results(
    plan: ev.EvaluationPlan,
    *,
    brain_on_correct: bool = True,
) -> tuple[ev.BatchResult, ...]:
    results = []
    for pair_id in plan.spec.required_pair_ids:
        for condition in ev.Condition:
            correct = (
                brain_on_correct if condition is ev.Condition.BRAIN_ON else not brain_on_correct
            )
            results.append(
                ev.parse_batch_response(
                    plan,
                    pair_id=pair_id,
                    condition=condition,
                    raw=json.dumps(response_payload(plan, pair_id, condition, correct=correct)),
                    latency_seconds=1.0,
                    input_tokens=100,
                    output_tokens=100,
                )
            )
    return tuple(results)


def runtime_evidence(
    plan: ev.EvaluationPlan,
    *,
    served: bool = True,
) -> tuple[ev.RuntimeEvidence, ...]:
    result = []
    for pair_id in plan.spec.required_pair_ids:
        for condition in ev.Condition:
            slot = f"{pair_id}-{condition.value}"
            result.append(
                ev.RuntimeEvidence(
                    agent_id=f"independent-agent-{slot}",
                    pair_id=pair_id,
                    condition=condition,
                    requested_model=plan.spec.requested_model,
                    requested_provider=plan.spec.requested_provider,
                    requested_reasoning_effort=plan.spec.requested_reasoning_effort,
                    accepted_model=plan.spec.requested_model,
                    accepted_provider=plan.spec.requested_provider,
                    accepted_reasoning_effort=plan.spec.requested_reasoning_effort,
                    accepted_config_digest=ev.accepted_runtime_config_digest(
                        model=plan.spec.requested_model,
                        provider=plan.spec.requested_provider,
                        reasoning_effort=plan.spec.requested_reasoning_effort,
                        allow_provider_model_fallback=False,
                    ),
                    allow_provider_model_fallback=False,
                    served_model=plan.spec.requested_model if served else None,
                    served_reasoning_effort=(
                        plan.spec.requested_reasoning_effort if served else None
                    ),
                    response_attestation_digest=(
                        digest(f"response-attestation-{slot}") if served else None
                    ),
                    response_attestation_source=(
                        "app_server_response_model_verification" if served else None
                    ),
                    final_assistant_end_turn=served,
                )
            )
    return tuple(result)


def codex_artifact_for_batch(
    plan: ev.EvaluationPlan,
    pair_id: int,
    condition: ev.Condition,
    output_text: str,
) -> CodexRunResult:
    thread_id = uuid7(f"thread-{pair_id}-{condition.value}")
    turn_id = uuid7(f"turn-{pair_id}-{condition.value}")
    batch = plan.batch(pair_id, condition)
    prompt = ev.codex_prompt_for_batch(batch)
    schema_json = ev.codex_output_schema_json(batch)
    runtime_cwd = str(
        (ROOT.parent / "evaluation-test-runtime" / f"{pair_id}-{condition.value}").resolve()
    )
    codex_home = str(
        Path(runtime_cwd).parent
        / f".codex-home-{hashlib.sha256(thread_id.encode()).hexdigest()[:32]}"
    )
    thread = {
        "id": thread_id,
        "extra": None,
        "sessionId": thread_id,
        "forkedFromId": None,
        "parentThreadId": None,
        "preview": "",
        "ephemeral": True,
        "historyMode": "legacy",
        "modelProvider": plan.spec.requested_provider,
        "createdAt": 1_784_082_727,
        "updatedAt": 1_784_082_727,
        "recencyAt": 1_784_082_727,
        "status": {"type": "idle"},
        "path": None,
        "cwd": runtime_cwd,
        "cliVersion": "0.144.0-alpha.4",
        "source": "vscode",
        "threadSource": None,
        "agentNickname": None,
        "agentRole": None,
        "gitInfo": None,
        "name": None,
        "turns": [],
    }
    thread_start = {
        "thread": thread,
        "model": plan.spec.requested_model,
        "modelProvider": plan.spec.requested_provider,
        "serviceTier": None,
        "cwd": runtime_cwd,
        "runtimeWorkspaceRoots": [],
        "instructionSources": [],
        "approvalPolicy": "never",
        "approvalsReviewer": "user",
        "sandbox": {"type": "readOnly", "networkAccess": False},
        "activePermissionProfile": None,
        "reasoningEffort": plan.spec.requested_reasoning_effort,
        "multiAgentMode": "explicitRequestOnly",
    }
    thread_request = {
        "id": 2,
        "method": "thread/start",
        "params": {
            "allowProviderModelFallback": False,
            "approvalPolicy": "never",
            "baseInstructions": DEFAULT_BASE_INSTRUCTIONS,
            "cwd": runtime_cwd,
            "developerInstructions": DEFAULT_DEVELOPER_INSTRUCTIONS,
            "dynamicTools": [],
            "environments": [],
            "ephemeral": True,
            "experimentalRawEvents": True,
            "model": plan.spec.requested_model,
            "modelProvider": plan.spec.requested_provider,
            "personality": "none",
            "runtimeWorkspaceRoots": [],
            "sandbox": "read-only",
            "selectedCapabilityRoots": [],
        },
    }
    turn_request = {
        "id": 3,
        "method": "turn/start",
        "params": {
            "approvalPolicy": "never",
            "effort": plan.spec.requested_reasoning_effort,
            "environments": [],
            "input": [{"text": prompt, "type": "text"}],
            "model": plan.spec.requested_model,
            "outputSchema": json.loads(schema_json),
            "personality": "none",
            "threadId": thread_id,
        },
    }
    message_id = f"message-{pair_id}-{condition.value}"
    response_turn = {
        "id": turn_id,
        "items": [],
        "itemsView": "notLoaded",
        "status": "inProgress",
        "error": None,
        "startedAt": None,
        "completedAt": None,
        "durationMs": None,
    }
    user_item = {
        "type": "userMessage",
        "id": f"user-{pair_id}-{condition.value}",
        "clientId": None,
        "content": [{"type": "text", "text": prompt, "text_elements": []}],
    }
    started_agent_item = {
        "type": "agentMessage",
        "id": message_id,
        "text": "",
        "phase": "final_answer",
        "memoryCitation": None,
    }
    bound = {"threadId": thread_id, "turnId": turn_id}

    def raw_input(role: str, *texts: str) -> dict[str, object]:
        return {
            "method": "rawResponseItem/completed",
            "params": {
                **bound,
                "item": {
                    "type": "message",
                    "role": role,
                    "content": [{"type": "input_text", "text": text} for text in texts],
                    "internal_chat_message_metadata_passthrough": {"turn_id": turn_id},
                },
            },
        }

    skills = _TEST_DESKTOP_SKILLS_TEMPLATE.replace(
        "<CODEX_HOME>", codex_home.replace("\\", "/")
    )
    environment = _TEST_DESKTOP_ENVIRONMENT_TEMPLATE.replace(
        "<CURRENT_DATE>", "2026-07-15"
    )
    messages = [
        {
            "id": 1,
            "result": {
                "codexHome": codex_home,
                "platformFamily": "windows",
                "platformOs": "windows",
                "userAgent": _TEST_DESKTOP_USER_AGENT_TEMPLATE.replace(
                    "<WINDOWS_BUILD>", "26200"
                ),
            },
        },
        {"id": 2, "result": thread_start},
        {"method": "thread/started", "params": {"thread": copy.deepcopy(thread)}},
        {"id": 3, "result": {"turn": response_turn}},
        {
            "method": "thread/status/changed",
            "params": {
                "threadId": thread_id,
                "status": {"type": "active", "activeFlags": []},
            },
        },
        {
            "method": "turn/started",
            "params": {
                "threadId": thread_id,
                "turn": {**response_turn, "startedAt": 1_784_082_728},
            },
        },
        raw_input(
            "developer",
            _TEST_DESKTOP_PERMISSIONS,
            DEFAULT_DEVELOPER_INSTRUCTIONS,
            skills,
        ),
        raw_input("developer", _TEST_DESKTOP_PRIMARY_AGENT),
        raw_input("developer", _TEST_DESKTOP_MULTI_AGENT),
        raw_input("user", environment),
        raw_input("user", prompt),
        {
            "method": "item/started",
            "params": {
                **bound,
                "item": user_item,
                "startedAtMs": 1_784_082_728_235,
            },
        },
        {
            "method": "item/completed",
            "params": {
                **bound,
                "item": copy.deepcopy(user_item),
                "completedAtMs": 1_784_082_728_236,
            },
        },
        {
            "method": "model/verification",
            "params": {**bound, "verifications": list(_TEST_DESKTOP_VERIFICATIONS)},
        },
        {
            "method": "item/started",
            "params": {
                **bound,
                "item": started_agent_item,
                "startedAtMs": 1_784_082_729_327,
            },
        },
        {
            "method": "item/agentMessage/delta",
            "params": {**bound, "itemId": message_id, "delta": output_text},
        },
        {
            "method": "item/completed",
            "params": {
                **bound,
                "item": {**started_agent_item, "text": output_text},
                "completedAtMs": 1_784_082_729_335,
            },
        },
        {
            "method": "rawResponseItem/completed",
            "params": {
                **bound,
                "item": {
                    "type": "message",
                    "id": message_id,
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": output_text}],
                    "phase": "final_answer",
                    "internal_chat_message_metadata_passthrough": {"turn_id": turn_id},
                },
            },
        },
        {
            "method": "thread/tokenUsage/updated",
            "params": {**bound, "tokenUsage": {}},
        },
        {
            "method": "thread/status/changed",
            "params": {"threadId": thread_id, "status": {"type": "idle"}},
        },
        {
            "method": "turn/completed",
            "params": {
                "threadId": thread_id,
                "turn": {
                    "id": turn_id,
                    "items": [],
                    "itemsView": "notLoaded",
                    "status": "completed",
                    "error": None,
                    "startedAt": 1_784_082_728,
                    "completedAt": 1_784_082_730,
                    "durationMs": 2_000,
                },
            },
        },
    ]
    transcript = b"".join(
        json.dumps(message, ensure_ascii=True, separators=(",", ":")).encode("utf-8") + b"\n"
        for message in messages
    )
    audit = audit_app_server_messages(
        messages,
        requested_model=plan.spec.requested_model,
        requested_effort=plan.spec.requested_reasoning_effort,
        requested_provider=plan.spec.requested_provider,
        expected_thread_id=thread_id,
        expected_turn_id=turn_id,
        thread_start_result=thread_start,
        process_exit_code=0,
        forced_termination=False,
        expected_model_inputs=(
            ("developer", DEFAULT_BASE_INSTRUCTIONS),
            ("developer", DEFAULT_DEVELOPER_INSTRUCTIONS),
            ("user", prompt),
        ),
        expected_model_input_profile=(codex_runner_module.MODEL_INPUT_PROFILE_DESKTOP_V1),
        expected_runtime_cwd=runtime_cwd,
        expected_codex_home_path_digest=bytes_digest(codex_home.encode("utf-8")),
    )
    return CodexRunResult(
        requested_model=plan.spec.requested_model,
        requested_effort=plan.spec.requested_reasoning_effort,
        requested_provider=plan.spec.requested_provider,
        prompt_text=prompt,
        output_schema_json=schema_json,
        launch_command_json=canonical_request_bytes(
            [
                SYNTHETIC_EXECUTABLE_PATH,
                *codex_runner_module._app_server_command_tail(
                    plan.spec.requested_model,
                    plan.spec.requested_reasoning_effort,
                ),
            ]
        ),
        executable_path=SYNTHETIC_EXECUTABLE_PATH,
        executable_size_bytes=SYNTHETIC_EXECUTABLE_PROVENANCE.size_bytes,
        executable_sha256_digest=SYNTHETIC_EXECUTABLE_PROVENANCE.sha256_digest,
        executable_lock_enforced=True,
        standard_popen_used=True,
        solver_process_isolation_policy_digest=(
            codex_runner_module.solver_process_isolation_policy_digest()
        ),
        codex_home_directory_device=2,
        codex_home_directory_inode=pair_id * 10 + list(ev.Condition).index(condition) + 1,
        codex_home_path_digest=bytes_digest(codex_home.encode("utf-8")),
        codex_home_initial_auth_only=True,
        codex_home_cleanup_verified=True,
        credential_leak_scan_passed=True,
        command_tail_policy_digest=app_server_command_tail_policy_digest(
            plan.spec.requested_model,
            plan.spec.requested_reasoning_effort,
        ),
        initialize_request_json=codex_runner_module.locked_initialize_request_json(),
        initialized_notification_json=canonical_request_bytes(
            {"method": "initialized", "params": {}}
        ),
        thread_start_request_json=canonical_request_bytes(thread_request),
        turn_start_request_json=canonical_request_bytes(turn_request),
        execution_nonce=hashlib.sha256(
            f"execution-{pair_id}-{condition.value}".encode()
        ).hexdigest(),
        timeout_seconds=plan.config.timeout_seconds,
        runtime_directory_device=1,
        runtime_directory_inode=pair_id * len(ev.Condition)
        + list(ev.Condition).index(condition)
        + 1,
        thread_id=thread_id,
        turn_id=turn_id,
        output_text=output_text,
        messages=tuple(messages),
        transcript_jsonl=transcript,
        transcript_hash=bytes_digest(transcript),
        stderr_bytes=b"",
        stderr_hash=bytes_digest(b""),
        stderr_excerpt="",
        process_exit_code=0,
        forced_termination=False,
        elapsed_seconds=1.0,
        audit=audit,
    )


def nonstandard_codex_artifact_for_batch(
    plan: ev.EvaluationPlan,
    pair_id: int,
    condition: ev.Condition,
    output_text: str,
) -> CodexRunResult:
    """Build a coherent legacy-profile artifact for process-gate tests only."""
    artifact = codex_artifact_for_batch(plan, pair_id, condition, output_text)
    thread_id = artifact.thread_id
    turn_id = artifact.turn_id
    bound = {"threadId": thread_id, "turnId": turn_id}
    thread_result = {
        "model": plan.spec.requested_model,
        "reasoningEffort": plan.spec.requested_reasoning_effort,
        "modelProvider": plan.spec.requested_provider,
        "thread": {"id": thread_id},
    }
    messages = [
        {"id": 1, "result": {"userAgent": "evaluation-test"}},
        {
            "method": "thread/started",
            "params": {"thread": {"id": thread_id}},
        },
        {"id": 2, "result": thread_result},
        {"id": 3, "result": {"turn": {"id": turn_id}}},
        {
            "method": "turn/started",
            "params": {
                "threadId": thread_id,
                "turn": {"id": turn_id, "items": [], "status": "inProgress"},
            },
        },
        {
            "method": "rawResponseItem/completed",
            "params": {
                **bound,
                "item": {
                    "type": "message",
                    "role": "developer",
                    "content": [
                        {"type": "input_text", "text": DEFAULT_BASE_INSTRUCTIONS},
                        {
                            "type": "input_text",
                            "text": DEFAULT_DEVELOPER_INSTRUCTIONS,
                        },
                    ],
                },
            },
        },
        {
            "method": "rawResponseItem/completed",
            "params": {
                **bound,
                "item": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": artifact.prompt_text}],
                },
            },
        },
        {
            "method": "item/started",
            "params": {
                **bound,
                "item": {"id": "message-1", "type": "agentMessage"},
            },
        },
        {
            "method": "item/completed",
            "params": {
                **bound,
                "item": {
                    "id": "message-1",
                    "type": "agentMessage",
                    "phase": "final_answer",
                    "text": output_text,
                },
            },
        },
        {
            "method": "turn/completed",
            "params": {
                "threadId": thread_id,
                "turn": {"id": turn_id, "status": "completed", "items": []},
            },
        },
    ]
    transcript = b"".join(
        json.dumps(message, ensure_ascii=True, separators=(",", ":")).encode("utf-8") + b"\n"
        for message in messages
    )
    audit = audit_app_server_messages(
        messages,
        requested_model=plan.spec.requested_model,
        requested_effort=plan.spec.requested_reasoning_effort,
        requested_provider=plan.spec.requested_provider,
        expected_thread_id=thread_id,
        expected_turn_id=turn_id,
        thread_start_result=thread_result,
        process_exit_code=0,
        forced_termination=False,
        expected_model_inputs=(
            ("developer", DEFAULT_BASE_INSTRUCTIONS),
            ("developer", DEFAULT_DEVELOPER_INSTRUCTIONS),
            ("user", artifact.prompt_text),
        ),
    )
    assert audit.eligible
    return replace(
        artifact,
        standard_popen_used=False,
        codex_home_path_digest=None,
        codex_home_initial_auth_only=False,
        messages=tuple(messages),
        transcript_jsonl=transcript,
        transcript_hash=bytes_digest(transcript),
        audit=audit,
    )


def isolation_evidence(
    plan: ev.EvaluationPlan,
    runtimes: tuple[ev.RuntimeEvidence, ...],
) -> tuple[ev.IsolationEvidence, ...]:
    return tuple(
        ev.IsolationEvidence(
            agent_id=runtime.agent_id,
            pair_id=runtime.pair_id,
            condition=runtime.condition,
            classification=ev.IsolationClassification.INSTRUCTION_BLINDED_EVENT_AUDITED,
            event_stream_digest=digest(f"event-stream-{runtime.pair_id}-{runtime.condition.value}"),
            audit_complete=True,
            tool_call_count=0,
            followup_count=0,
            policy_violation_count=0,
        )
        for runtime in runtimes
    )


def retrieval_evidence(plan: ev.EvaluationPlan) -> tuple[ev.RetrievalEvidence, ...]:
    return ev.retrieval_evidence_for_plan(plan)


@pytest.fixture(scope="module")
def complete_run(plan: ev.EvaluationPlan) -> ev.EvaluationRun:
    runtimes = runtime_evidence(plan)
    return ev.finalize_run(
        plan,
        batch_results=batch_results(plan),
        runtime_evidence=runtimes,
        isolation_evidence=isolation_evidence(plan, runtimes),
        retrieval_evidence=retrieval_evidence(plan),
    )


def assert_malformed(result: ev.BatchResult, code: str) -> None:
    assert {attempt.status for attempt in result.attempts} == {ev.AttemptStatus.MALFORMED}
    assert {attempt.failure_code for attempt in result.attempts} == {code}


def test_config_is_executable_strict_and_hashed(config: ev.EvaluationConfig) -> None:
    assert config.digest.startswith("sha256:") and len(config.digest) == 71
    assert config.runtime.requested_model == "gpt-5.6-sol"
    assert config.runtime.requested_provider == "openai"
    assert config.runtime.executable_provenance is None
    with pytest.raises(ValueError, match="at least 10"):
        replace(config, pair_count=8)


def test_optional_executable_provenance_is_config_and_plan_precommitted(
    tmp_path: Path,
    config: ev.EvaluationConfig,
) -> None:
    source = (ROOT / "config" / "evaluation.toml").read_text(encoding="utf-8")
    source += (
        "\n[runtime.executable_provenance]\n"
        f"canonical_path = {json.dumps(SYNTHETIC_EXECUTABLE_PATH)}\n"
        f"size_bytes = {SYNTHETIC_EXECUTABLE_PROVENANCE.size_bytes}\n"
        f"sha256_digest = {json.dumps(SYNTHETIC_EXECUTABLE_PROVENANCE.sha256_digest)}\n"
    )
    path = tmp_path / "evaluation-with-executable.toml"
    path.write_text(source, encoding="utf-8")
    committed = ev.EvaluationConfig.from_toml(path)
    assert committed.runtime.executable_provenance == SYNTHETIC_EXECUTABLE_PROVENANCE
    assert committed.digest != config.digest

    uncommitted_plan = build_test_plan(config, manifest_label="uncommitted-runtime")
    committed_plan = build_test_plan(committed, manifest_label="committed-runtime")
    assert committed_plan.spec.runtime_policy_digest != uncommitted_plan.spec.runtime_policy_digest


def test_partial_executable_provenance_config_fails_closed(tmp_path: Path) -> None:
    source = (ROOT / "config" / "evaluation.toml").read_text(encoding="utf-8")
    source += (
        "\n[runtime.executable_provenance]\n"
        f"canonical_path = {json.dumps(SYNTHETIC_EXECUTABLE_PATH)}\n"
    )
    path = tmp_path / "evaluation-partial-executable.toml"
    path.write_text(source, encoding="utf-8")
    with pytest.raises(ValueError, match="runtime executable provenance keys differ"):
        ev.EvaluationConfig.from_toml(path)


def test_full_plan_is_exact_matched_balanced_and_seed_sealed(plan: ev.EvaluationPlan) -> None:
    assert len(plan.assignments) == len(plan.cases) * 10 * 2
    assert len({assignment.digest for assignment in plan.assignments}) == len(plan.assignments)
    for case in plan.cases:
        on = [
            assignment
            for assignment in plan.assignments
            if assignment.case_id == case.case_id and assignment.condition is ev.Condition.BRAIN_ON
        ]
        assert sum(item.variant is ev.Variant.CLEAN for item in on) == 5
        assert sum(item.variant is ev.Variant.FAULTY for item in on) == 5
    for pair_id in plan.spec.required_pair_ids:
        on = plan.batch(pair_id, ev.Condition.BRAIN_ON)
        off = plan.batch(pair_id, ev.Condition.BRAIN_OFF)
        assert on.pack_digest == off.pack_digest
        assert [case.view_digest for case in on.cases] == [case.view_digest for case in off.cases]
        assert on.treatment_digest != off.treatment_digest
    commitments = plan.spec.seed_commitments.to_dict()
    assert len(set(commitments.values())) == 5


def test_plan_rejects_missing_assignment_and_stale_config(plan: ev.EvaluationPlan) -> None:
    with pytest.raises(ValueError, match="cardinality"):
        replace(plan, assignments=plan.assignments[:-1])
    changed_gate = replace(
        plan.config.benefit_gate,
        minimum_primary_delta=plan.config.benefit_gate.minimum_primary_delta + 0.01,
    )
    changed_config = replace(plan.config, benefit_gate=changed_gate)
    with pytest.raises(ValueError, match="config digest"):
        replace(plan, config=changed_config)


def test_plan_seals_executable_lease_and_standard_popen_requirements(
    plan: ev.EvaluationPlan,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launch_policy = ev._sealed_runtime_policy(plan.config)["codex_launch_policy"]
    assert launch_policy["executable_image_binding_policy"] == "windows_deny_write_delete_lease_v1"
    assert launch_policy["require_standard_popen"] is True
    assert launch_policy["solver_process_isolation_policy_digest"] == (
        codex_runner_module.solver_process_isolation_policy_digest()
    )

    with monkeypatch.context() as patch:
        patch.setattr(ev, "EXECUTABLE_IMAGE_BINDING_POLICY", "weakened")
        with pytest.raises(ValueError, match="runtime-policy seal"):
            replace(plan)
    with monkeypatch.context() as patch:
        patch.setattr(ev, "REQUIRE_STANDARD_POPEN", False)
        with pytest.raises(ValueError, match="runtime-policy seal"):
            replace(plan)


def test_old_eight_pair_faulty_only_helped_canary_cannot_form_a_run(
    config: ev.EvaluationConfig,
    plan: ev.EvaluationPlan,
) -> None:
    with pytest.raises(ValueError, match="at least 10"):
        replace(config, pair_count=8)
    faulty_only = tuple(
        assignment for assignment in plan.assignments if assignment.variant is ev.Variant.FAULTY
    )
    with pytest.raises(ValueError, match="cardinality"):
        replace(plan, assignments=faulty_only)


def test_output_schema_binds_exact_order_and_digests(plan: ev.EvaluationPlan) -> None:
    batch = plan.batch(0, ev.Condition.BRAIN_ON)
    schema = ev.batch_output_schema(batch)
    answers = schema["properties"]["answers"]
    assert answers["minItems"] == answers["maxItems"] == len(plan.cases)
    assert answers["items"]["type"] == "object"
    assert answers["items"]["additionalProperties"] is False
    assert [item["properties"]["case_id"]["const"] for item in answers["prefixItems"]] == [
        case.case_id for case in batch.cases
    ]
    assert all(
        item["properties"]["case_id"]["type"] == "string"
        and item["properties"]["view_digest"]["type"] == "string"
        for item in answers["prefixItems"]
    )
    typed_payload = answers["prefixItems"][0]["properties"]["witness"]["anyOf"][1]["properties"][
        "typed_payload"
    ]
    assert {variant["type"] for variant in typed_payload["anyOf"]} == {
        "array",
        "boolean",
        "integer",
        "object",
        "string",
    }
    assert schema["properties"]["schema"]["type"] == "string"
    assert schema["properties"]["schema_version"]["type"] == "integer"
    assert batch.to_solver_payload()["response_schema"] == schema
    changed_contexts = ("post-seal packet swap", *batch.case_contexts[1:])
    with pytest.raises(ValueError, match="context digest"):
        replace(batch, case_contexts=changed_contexts)
    with pytest.raises(ValueError, match="treatment digest"):
        replace(batch, treatment_digest=digest("post-seal-treatment-swap"))


def test_parser_accepts_exact_complete_batch_and_derives_verdict(
    plan: ev.EvaluationPlan,
) -> None:
    payload = response_payload(plan, 0, ev.Condition.BRAIN_ON, correct=True)
    result = ev.parse_batch_response(
        plan,
        pair_id=0,
        condition=ev.Condition.BRAIN_ON,
        raw=json.dumps(payload),
    )
    assert {attempt.status for attempt in result.attempts} == {ev.AttemptStatus.SUCCESS}
    assert all(attempt.answer is not None for attempt in result.attempts)
    faulty = next(
        attempt
        for assignment, attempt in zip(
            plan.assignments_for(0, ev.Condition.BRAIN_ON), result.attempts, strict=True
        )
        if assignment.variant is ev.Variant.FAULTY
    )
    assert faulty.answer is not None and faulty.answer.verdict is ev.Verdict.INVALID


def test_probability_tie_rule_is_predeclared_and_valid_nulls_are_enforced(
    plan: ev.EvaluationPlan,
) -> None:
    assignments = plan.assignments_for(0, ev.Condition.BRAIN_ON)
    faulty_index = next(
        index
        for index, assignment in enumerate(assignments)
        if assignment.variant is ev.Variant.FAULTY
    )
    payload = response_payload(plan, 0, ev.Condition.BRAIN_ON, correct=True)
    payload["answers"][faulty_index]["probability_invalid"] = 0.5  # type: ignore[index]
    result = ev.parse_batch_response(
        plan,
        pair_id=0,
        condition=ev.Condition.BRAIN_ON,
        raw=json.dumps(payload),
    )
    answer = result.attempts[faulty_index].answer
    assert answer is not None and answer.verdict is ev.Verdict.INVALID
    clean_index = next(
        index
        for index, assignment in enumerate(assignments)
        if assignment.variant is ev.Variant.CLEAN
    )
    bad = response_payload(plan, 0, ev.Condition.BRAIN_ON, correct=True)
    bad["answers"][clean_index]["first_invalid_line"] = 1  # type: ignore[index]
    assert_malformed(
        ev.parse_batch_response(
            plan,
            pair_id=0,
            condition=ev.Condition.BRAIN_ON,
            raw=json.dumps(bad),
        ),
        "answer_schema",
    )


def test_parser_rejects_duplicate_json_keys(plan: ev.EvaluationPlan) -> None:
    raw = (
        '{"schema":"org.math-research-brain/evaluation-batch-response",'
        '"schema":"org.math-research-brain/evaluation-batch-response",'
        '"schema_version":1,"answers":[]}'
    )
    assert_malformed(
        ev.parse_batch_response(
            plan,
            pair_id=0,
            condition=ev.Condition.BRAIN_ON,
            raw=raw,
        ),
        "duplicate_key",
    )


def test_parser_classifies_root_and_answer_shape_errors_as_malformed(
    plan: ev.EvaluationPlan,
) -> None:
    assert_malformed(
        ev.parse_batch_response(
            plan,
            pair_id=0,
            condition=ev.Condition.BRAIN_ON,
            raw="{}",
        ),
        "root_schema",
    )
    assert_malformed(
        ev.parse_batch_response(
            plan,
            pair_id=0,
            condition=ev.Condition.BRAIN_ON,
            raw=json.dumps(
                {
                    "answers": [None],
                    "schema": ev.RESPONSE_SCHEMA,
                    "schema_version": ev.RESPONSE_SCHEMA_VERSION,
                }
            ),
        ),
        "answer_schema",
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("probability_invalid", True),
        ("probability_invalid", "0.9"),
        ("first_invalid_line", True),
        ("first_invalid_line", 3.0),
    ],
)
def test_parser_rejects_bool_coercions_and_numeric_strings(
    plan: ev.EvaluationPlan,
    field: str,
    value: object,
) -> None:
    assignments = plan.assignments_for(0, ev.Condition.BRAIN_ON)
    index = next(
        index
        for index, assignment in enumerate(assignments)
        if assignment.variant is ev.Variant.FAULTY
    )
    payload = response_payload(plan, 0, ev.Condition.BRAIN_ON, correct=True)
    payload["answers"][index][field] = value  # type: ignore[index]
    assert_malformed(
        ev.parse_batch_response(
            plan,
            pair_id=0,
            condition=ev.Condition.BRAIN_ON,
            raw=json.dumps(payload),
        ),
        "answer_schema",
    )


def test_parser_rejects_bool_schema_version(plan: ev.EvaluationPlan) -> None:
    payload = response_payload(plan, 0, ev.Condition.BRAIN_ON, correct=True)
    payload["schema_version"] = True
    assert_malformed(
        ev.parse_batch_response(
            plan,
            pair_id=0,
            condition=ev.Condition.BRAIN_ON,
            raw=json.dumps(payload),
        ),
        "root_schema",
    )


def test_parser_rejects_nan_anywhere(plan: ev.EvaluationPlan) -> None:
    payload = response_payload(plan, 0, ev.Condition.BRAIN_ON, correct=True)
    payload["answers"][0]["probability_invalid"] = float("nan")  # type: ignore[index]
    assert_malformed(
        ev.parse_batch_response(
            plan,
            pair_id=0,
            condition=ev.Condition.BRAIN_ON,
            raw=json.dumps(payload),
        ),
        "nonfinite_number",
    )


@pytest.mark.parametrize("poison", ["bad\x00text", "bad\u202etext", "e\u0301", "\ud800"])
def test_parser_rejects_control_bidi_non_nfc_and_surrogate(
    plan: ev.EvaluationPlan,
    poison: str,
) -> None:
    assignments = plan.assignments_for(0, ev.Condition.BRAIN_ON)
    index = next(
        index
        for index, assignment in enumerate(assignments)
        if assignment.variant is ev.Variant.FAULTY
    )
    payload = response_payload(plan, 0, ev.Condition.BRAIN_ON, correct=True)
    payload["answers"][index]["error_family"] = poison  # type: ignore[index]
    assert_malformed(
        ev.parse_batch_response(
            plan,
            pair_id=0,
            condition=ev.Condition.BRAIN_ON,
            raw=json.dumps(payload),
        ),
        "unicode_policy",
    )


def test_parser_rejects_extreme_depth_and_bytes(plan: ev.EvaluationPlan) -> None:
    nested: object = "x"
    for _ in range(plan.config.response_limits.maximum_depth + 2):
        nested = [nested]
    payload = response_payload(plan, 0, ev.Condition.BRAIN_ON, correct=True)
    payload["answers"] = nested
    assert_malformed(
        ev.parse_batch_response(
            plan,
            pair_id=0,
            condition=ev.Condition.BRAIN_ON,
            raw=json.dumps(payload),
        ),
        "depth_budget",
    )
    oversized = "x" * (plan.config.response_limits.maximum_bytes + 1)
    assert_malformed(
        ev.parse_batch_response(
            plan,
            pair_id=0,
            condition=ev.Condition.BRAIN_ON,
            raw=oversized,
        ),
        "byte_budget",
    )


def test_parser_records_missing_answer_in_sealed_denominator(plan: ev.EvaluationPlan) -> None:
    payload = response_payload(plan, 0, ev.Condition.BRAIN_ON, correct=True)
    payload["answers"].pop(1)  # type: ignore[union-attr]
    result = ev.parse_batch_response(
        plan,
        pair_id=0,
        condition=ev.Condition.BRAIN_ON,
        raw=json.dumps(payload),
    )
    assert sum(attempt.status is ev.AttemptStatus.MISSING for attempt in result.attempts) == 1
    run = ev.finalize_run(plan, batch_results=[result])
    assert len(run.observations) == len(plan.assignments)
    assert sum(item.status is ev.AttemptStatus.MISSING for item in run.observations) == (
        len(plan.assignments) - len(plan.cases) + 1
    )


def test_parser_rejects_case_order_and_view_digest_swaps(plan: ev.EvaluationPlan) -> None:
    payload = response_payload(plan, 0, ev.Condition.BRAIN_ON, correct=True)
    payload["answers"][0], payload["answers"][1] = (  # type: ignore[index]
        payload["answers"][1],
        payload["answers"][0],
    )
    assert_malformed(
        ev.parse_batch_response(
            plan,
            pair_id=0,
            condition=ev.Condition.BRAIN_ON,
            raw=json.dumps(payload),
        ),
        "answer_order",
    )
    swapped = response_payload(plan, 0, ev.Condition.BRAIN_ON, correct=True)
    first = swapped["answers"][0]["view_digest"]  # type: ignore[index]
    second = swapped["answers"][1]["view_digest"]  # type: ignore[index]
    swapped["answers"][0]["view_digest"] = second  # type: ignore[index]
    swapped["answers"][1]["view_digest"] = first  # type: ignore[index]
    assert_malformed(
        ev.parse_batch_response(
            plan,
            pair_id=0,
            condition=ev.Condition.BRAIN_ON,
            raw=json.dumps(swapped),
        ),
        "view_digest",
    )


def test_finalize_rejects_duplicated_batches_and_attempt_reordering(
    plan: ev.EvaluationPlan,
) -> None:
    result = batch_results(plan)[0]
    with pytest.raises(ValueError, match="duplicate batch"):
        ev.finalize_run(plan, batch_results=[result, result])
    reordered = replace(result, attempts=tuple(reversed(result.attempts)))
    with pytest.raises(ValueError, match="attempts differ"):
        ev.finalize_run(plan, batch_results=[reordered])
    assignments = plan.assignments_for(result.pair_id, result.condition)
    forged_attempt = replace(
        result.attempts[0],
        case_id=assignments[1].case_id,
        view_digest=assignments[1].view_digest,
    )
    forged = replace(result, attempts=(forged_attempt, *result.attempts[1:]))
    with pytest.raises(ValueError, match="attempt identity"):
        ev.finalize_run(plan, batch_results=[forged])


def test_exact_repair_and_witness_receipts_replay_hidden_math(plan: ev.EvaluationPlan) -> None:
    pair_id = next(
        pair
        for pair in plan.spec.required_pair_ids
        if any(
            assignment.variant is ev.Variant.FAULTY
            for assignment in plan.assignments_for(pair, ev.Condition.BRAIN_ON)
        )
    )
    result = ev.parse_batch_response(
        plan,
        pair_id=pair_id,
        condition=ev.Condition.BRAIN_ON,
        raw=json.dumps(response_payload(plan, pair_id, ev.Condition.BRAIN_ON, correct=True)),
    )
    run = ev.finalize_run(plan, batch_results=[result])
    faulty = next(
        item
        for item in run.observations
        if item.assignment.pair_id == pair_id
        and item.assignment.condition is ev.Condition.BRAIN_ON
        and item.assignment.variant is ev.Variant.FAULTY
    )
    assert faulty.repair_receipt is not None and faulty.repair_receipt.passed
    assert faulty.witness_receipt is not None and faulty.witness_receipt.passed

    payload = response_payload(plan, pair_id, ev.Condition.BRAIN_ON, correct=True)
    assignments = plan.assignments_for(pair_id, ev.Condition.BRAIN_ON)
    index = next(
        index
        for index, assignment in enumerate(assignments)
        if assignment.variant is ev.Variant.FAULTY
    )
    payload["answers"][index]["repair"] = {  # type: ignore[index]
        "replacement_line": "1. forged = line."
    }
    bad_result = ev.parse_batch_response(
        plan,
        pair_id=pair_id,
        condition=ev.Condition.BRAIN_ON,
        raw=json.dumps(payload),
    )
    bad_run = ev.finalize_run(plan, batch_results=[bad_result])
    bad_faulty = next(
        item
        for item in bad_run.observations
        if item.assignment.pair_id == pair_id
        and item.assignment.condition is ev.Condition.BRAIN_ON
        and item.assignment.case_id == assignments[index].case_id
    )
    assert bad_faulty.repair_receipt is not None
    assert not bad_faulty.repair_receipt.passed


def test_caller_created_receipt_cannot_authorize_verifier_credit(
    complete_run: ev.EvaluationRun,
) -> None:
    observation = next(
        item
        for item in complete_run.observations
        if item.repair_receipt is not None and item.repair_receipt.passed
    )
    assert observation.answer is not None
    forged = ev.VerifierReceipt.create(
        kind=ev.VerifierKind.REPAIR,
        assignment=observation.assignment,
        answer=observation.answer,
        scorer_digest=complete_run.plan.config.scorer_digest,
        validator_digest=digest("caller-asserted-validator"),
        evidence_digest=digest("caller-asserted-evidence"),
        passed=True,
    )
    forged_observation = replace(observation, repair_receipt=forged)
    assert (
        ev.score_observation(
            forged_observation,
            complete_run.plan.config,
        ).repair
        == 0.0
    )
    with pytest.raises(ValueError, match="fresh sealed-plan replay"):
        ev.score_observation(
            forged_observation,
            complete_run.plan.config,
            plan=complete_run.plan,
        )


def test_valid_answer_cannot_receive_forged_repair_boolean_or_receipt(
    complete_run: ev.EvaluationRun,
) -> None:
    clean = next(
        item
        for item in complete_run.observations
        if item.assignment.condition is ev.Condition.BRAIN_ON
        and item.assignment.variant is ev.Variant.CLEAN
    )
    faulty = next(
        item
        for item in complete_run.observations
        if item.repair_receipt is not None and item.repair_receipt.passed
    )
    with pytest.raises(ValueError, match="VALID answers cannot"):
        replace(clean, repair_receipt=faulty.repair_receipt)
    assert "repair_verified" not in ev.Observation.__dataclass_fields__
    assert "witness_verified" not in ev.Observation.__dataclass_fields__


def test_aggregate_rejects_duplicated_favorable_rows(
    complete_run: ev.EvaluationRun,
) -> None:
    on = [
        item
        for item in complete_run.observations
        if item.assignment.condition is ev.Condition.BRAIN_ON
    ]
    with pytest.raises(ValueError, match="duplicated"):
        ev.aggregate([*on, on[0]], complete_run.plan.config)
    clean = next(
        item
        for item in on
        if item.assignment.variant is ev.Variant.CLEAN
        and item.answer is not None
        and item.answer.verdict is ev.Verdict.VALID
    )
    same_logical_row = replace(
        clean,
        assignment=replace(clean.assignment, order_index=clean.assignment.order_index + 999),
    )
    with pytest.raises(ValueError, match="duplicated"):
        ev.aggregate([clean, same_logical_row], complete_run.plan.config)


def test_primary_delta_and_inference_use_the_same_mean_pair_estimand(
    config: ev.EvaluationConfig,
) -> None:
    weighted = replace(
        config,
        weights=ev.ScoreWeights(
            balanced_validity=1.0,
            localization=0.0,
            repair=0.0,
            witness=0.0,
            error_family=0.0,
        ),
    )
    pool = solver_valid_items("estimand-regression", 100)
    by_family = {}
    for item in pool:
        by_family.setdefault(item.generation.family, []).append(item)
    repeated_family, repeated_items = next(
        (family, items) for family, items in by_family.items() if len(items) >= 3
    )
    different_item = next(item for item in pool if item.generation.family != repeated_family)
    cases = tuple(
        ev.EvaluationCase(
            item=item,
            domain="estimand",
            difficulty="controlled",
        )
        for item in (*repeated_items[:3], different_item)
    )
    assert cases[0].item.generation.family == cases[1].item.generation.family
    assert cases[0].item.generation.family == cases[2].item.generation.family
    assert cases[0].item.generation.family != cases[3].item.generation.family
    packets = {case.case_id: "frozen generic context" for case in cases}
    local_plan = ev.build_full_plan(
        cases,
        config=weighted,
        seeds=ev.DesignSeeds("eo", "ev", "ep", "ec", "eb"),
        benchmark_manifest_digest=digest("estimand-manifest"),
        solver_prompt="Return only the sealed response schema.",
        brain_on_packets=packets,
    )
    favored_case_id = cases[0].case_id
    results = []
    for pair_id in local_plan.spec.required_pair_ids:
        for condition in ev.Condition:
            answers = []
            for assignment in local_plan.assignments_for(pair_id, condition):
                correct = (
                    condition is ev.Condition.BRAIN_ON
                    and assignment.variant is ev.Variant.FAULTY
                    and assignment.case_id == favored_case_id
                )
                answers.append(_answer_for(local_plan, assignment, correct=correct))
            results.append(
                ev.parse_batch_response(
                    local_plan,
                    pair_id=pair_id,
                    condition=condition,
                    raw=json.dumps(
                        {
                            "answers": answers,
                            "schema": ev.RESPONSE_SCHEMA,
                            "schema_version": ev.RESPONSE_SCHEMA_VERSION,
                        }
                    ),
                )
            )
    run = ev.finalize_run(local_plan, batch_results=results)
    on = [row for row in run.observations if row.assignment.condition is ev.Condition.BRAIN_ON]
    off = [row for row in run.observations if row.assignment.condition is ev.Condition.BRAIN_OFF]
    pooled = (
        ev.aggregate(on, weighted, plan=local_plan).primary
        - ev.aggregate(off, weighted, plan=local_plan).primary
    )
    inference = ev.paired_inference(run)
    assert pooled != pytest.approx(inference.observed_mean_delta)
    decision = ev.decide_benefit(run)
    assert decision.primary_delta == pytest.approx(inference.observed_mean_delta)
    assert decision.pooled_primary_delta == pytest.approx(pooled)


def test_asserted_digest_evidence_cannot_authorize_benefit(
    complete_run: ev.EvaluationRun,
) -> None:
    decision = ev.decide_benefit(complete_run)
    assert not decision.helped
    assert {
        "command_tail_policy",
        "executable_image_binding_unenforced",
        "executable_provenance_unsealed",
        "nonstandard_process_factory",
        "runtime_artifact_binding",
        "served_identity",
        "isolation_policy",
    } <= set(decision.failed_gates)
    assert decision.inference.method == ("exact_paired_sign_flip_plus_fixed_case_paired_bootstrap")
    assert "fixed sealed case universe" in decision.inference.estimand
    assert decision.inference.randomization_p == pytest.approx(1 / 1024)
    assert decision.inference.bootstrap_interval[0] > 0


def test_sign_flip_direction_is_one_sided_and_exact(plan: ev.EvaluationPlan) -> None:
    runtimes = runtime_evidence(plan)
    harmed = ev.finalize_run(
        plan,
        batch_results=batch_results(plan, brain_on_correct=False),
        runtime_evidence=runtimes,
        isolation_evidence=isolation_evidence(plan, runtimes),
        retrieval_evidence=retrieval_evidence(plan),
    )
    inference = ev.paired_inference(harmed)
    assert inference.observed_mean_delta < 0
    assert inference.randomization_p == 1.0


def test_unverified_or_rerouted_served_identity_fails_benefit_gate(
    plan: ev.EvaluationPlan,
) -> None:
    unverified = runtime_evidence(plan, served=False)
    run = ev.finalize_run(
        plan,
        batch_results=batch_results(plan),
        runtime_evidence=unverified,
        isolation_evidence=isolation_evidence(plan, unverified),
        retrieval_evidence=retrieval_evidence(plan),
    )
    assert "served_identity" in ev.decide_benefit(run).failed_gates
    rerouted = list(runtime_evidence(plan))
    rerouted[0] = replace(rerouted[0], reroute_notice_model="fallback-model")
    rerouted_tuple = tuple(rerouted)
    run = ev.finalize_run(
        plan,
        batch_results=batch_results(plan),
        runtime_evidence=rerouted_tuple,
        isolation_evidence=isolation_evidence(plan, rerouted_tuple),
        retrieval_evidence=retrieval_evidence(plan),
    )
    assert rerouted_tuple[0].identity_status is ev.IdentityStatus.CONTRADICTED
    assert "served_identity" in ev.decide_benefit(run).failed_gates


def test_request_context_or_self_report_cannot_claim_served_model(
    plan: ev.EvaluationPlan,
) -> None:
    base = runtime_evidence(plan, served=False)[0]
    with pytest.raises(ValueError, match="complete response-side attestation"):
        replace(base, served_model=plan.spec.requested_model)


def test_accepted_config_mismatch_is_separate_from_served_identity(
    plan: ev.EvaluationPlan,
) -> None:
    runtimes = list(runtime_evidence(plan))
    runtimes[0] = replace(
        runtimes[0],
        accepted_provider="different-provider",
        accepted_config_digest=ev.accepted_runtime_config_digest(
            model=plan.spec.requested_model,
            provider="different-provider",
            reasoning_effort=plan.spec.requested_reasoning_effort,
            allow_provider_model_fallback=False,
        ),
    )
    values = tuple(runtimes)
    run = ev.finalize_run(
        plan,
        batch_results=batch_results(plan),
        runtime_evidence=values,
        isolation_evidence=isolation_evidence(plan, values),
        retrieval_evidence=retrieval_evidence(plan),
    )
    assert values[0].identity_status is ev.IdentityStatus.UNVERIFIED
    assert {
        "accepted_runtime_config",
        "served_identity",
    } <= set(ev.decide_benefit(run).failed_gates)
    with pytest.raises(ValueError, match="not bound"):
        replace(runtimes[0], accepted_config_digest=digest("stale-accepted-config"))


def test_codex_artifact_is_bound_to_sealed_batch_and_scored_response(
    plan: ev.EvaluationPlan,
) -> None:
    pair_id = 0
    condition = ev.Condition.BRAIN_ON
    raw = json.dumps(response_payload(plan, pair_id, condition, correct=True))
    artifact = codex_artifact_for_batch(plan, pair_id, condition, raw)
    result, runtime, isolation = ev.parse_codex_artifact_response(
        plan,
        pair_id=pair_id,
        condition=condition,
        artifact=artifact,
    )
    assert runtime.artifact_derived
    assert isolation.artifact_derived
    assert isolation.passes(plan.config.isolation)
    assert runtime.identity_status is ev.IdentityStatus.UNVERIFIED

    run = ev.finalize_run(
        plan,
        batch_results=(result,),
        runtime_evidence=(runtime,),
        isolation_evidence=(isolation,),
    )
    assert run.runtime_evidence == (runtime,)

    wrong_prompt = "different sealed prompt"
    wrong_turn_request = json.loads(artifact.turn_start_request_json)
    wrong_turn_request["params"]["input"] = [{"text": wrong_prompt, "type": "text"}]
    with pytest.raises(AppServerProtocolError, match="audit does not match"):
        replace(
            artifact,
            prompt_text=wrong_prompt,
            turn_start_request_json=canonical_request_bytes(wrong_turn_request),
        )

    stale_runtime = replace(runtime, batch_digest=digest("wrong-batch"))
    with pytest.raises(ValueError, match="not bound to the sealed batch"):
        ev.finalize_run(
            plan,
            batch_results=(result,),
            runtime_evidence=(stale_runtime,),
        )


def test_executable_command_zero_and_digest_must_match_plan_precommit(
    config: ev.EvaluationConfig,
) -> None:
    sealed_config = replace(
        config,
        runtime=replace(
            config.runtime,
            executable_provenance=SYNTHETIC_EXECUTABLE_PROVENANCE,
        ),
    )
    sealed_plan = build_test_plan(
        sealed_config,
        manifest_label="executable-provenance-adversary",
    )
    pair_id = 0
    condition = ev.Condition.BRAIN_ON
    raw = json.dumps(response_payload(sealed_plan, pair_id, condition, correct=True))
    artifact = codex_artifact_for_batch(sealed_plan, pair_id, condition, raw)
    ev.codex_evidence_for_batch(
        sealed_plan,
        pair_id=pair_id,
        condition=condition,
        artifact=artifact,
    )

    fake_path = (
        str(Path("C:/math-brain-tests/fake-codex.exe"))
        if os.name == "nt"
        else str(Path("/math-brain-tests/fake-codex"))
    )
    fake_launch = json.loads(artifact.launch_command_json)
    fake_launch[0] = fake_path
    fake_command_zero = replace(
        artifact,
        executable_path=fake_path,
        launch_command_json=canonical_request_bytes(fake_launch),
    )
    with pytest.raises(ValueError, match="executable provenance differs"):
        ev.codex_evidence_for_batch(
            sealed_plan,
            pair_id=pair_id,
            condition=condition,
            artifact=fake_command_zero,
        )

    fake_digest = replace(
        artifact,
        executable_sha256_digest=digest("fake-native-executable"),
    )
    with pytest.raises(ValueError, match="executable provenance differs"):
        ev.codex_evidence_for_batch(
            sealed_plan,
            pair_id=pair_id,
            condition=condition,
            artifact=fake_digest,
        )


def test_strict_gate_rejects_unlocked_or_injected_process_artifacts(
    config: ev.EvaluationConfig,
) -> None:
    sealed_config = replace(
        config,
        runtime=replace(
            config.runtime,
            executable_provenance=SYNTHETIC_EXECUTABLE_PROVENANCE,
        ),
    )
    sealed_plan = build_test_plan(
        sealed_config,
        manifest_label="local-launch-trust-gates",
    )
    results = []
    runtimes = []
    isolations = []
    for pair_id in sealed_plan.spec.required_pair_ids:
        for condition in ev.Condition:
            raw = json.dumps(response_payload(sealed_plan, pair_id, condition, correct=True))
            artifact = replace(
                nonstandard_codex_artifact_for_batch(sealed_plan, pair_id, condition, raw),
                executable_lock_enforced=False,
            )
            result, runtime, isolation = ev.parse_codex_artifact_response(
                sealed_plan,
                pair_id=pair_id,
                condition=condition,
                artifact=artifact,
            )
            results.append(result)
            runtimes.append(runtime)
            isolations.append(isolation)
    run = ev.finalize_run(
        sealed_plan,
        batch_results=results,
        runtime_evidence=runtimes,
        isolation_evidence=isolations,
        retrieval_evidence=retrieval_evidence(sealed_plan),
    )
    assert {
        "executable_image_binding_unenforced",
        "nonstandard_process_factory",
    } <= set(ev.decide_benefit(run).failed_gates)


def test_standard_desktop_artifact_round_trips_with_bound_codex_home(
    plan: ev.EvaluationPlan,
) -> None:
    pair_id = 0
    condition = ev.Condition.BRAIN_ON
    raw = json.dumps(response_payload(plan, pair_id, condition, correct=True))
    artifact = codex_artifact_for_batch(plan, pair_id, condition, raw)
    encoded = serialize_codex_run_result(artifact)
    restored = deserialize_codex_run_result(encoded)
    assert restored.standard_popen_used
    assert restored.codex_home_initial_auth_only
    assert restored.codex_home_path_digest is not None
    assert restored.audit.eligible
    assert serialize_codex_run_result(restored) == encoded


def test_codex_artifact_rejects_cross_plan_policy_schema_and_response_replay(
    plan: ev.EvaluationPlan,
) -> None:
    pair_id = 0
    condition = ev.Condition.BRAIN_ON
    raw = json.dumps(response_payload(plan, pair_id, condition, correct=True))
    artifact = codex_artifact_for_batch(plan, pair_id, condition, raw)

    packets = {packet.case_id: packet.packet for packet in plan.retrieval_packets}
    reasons = {
        packet.case_id: packet.abstention_reason
        for packet in plan.retrieval_packets
        if packet.abstention_reason is not None
    }
    other_plan = ev.build_full_plan(
        plan.cases,
        config=plan.config,
        seeds=plan.seeds,
        benchmark_manifest_digest=digest("different-benchmark-manifest"),
        solver_prompt=plan.solver_prompt,
        brain_on_packets=packets,
        abstention_reasons=reasons,
    )
    assert other_plan.spec.digest != plan.spec.digest
    assert ev.codex_prompt_for_batch(other_plan.batch(pair_id, condition)) != artifact.prompt_text
    with pytest.raises(ValueError, match="prompt artifact differs"):
        ev.codex_evidence_for_batch(
            other_plan,
            pair_id=pair_id,
            condition=condition,
            artifact=artifact,
        )

    leaky_thread_request = json.loads(artifact.thread_start_request_json)
    leaky_thread_request["params"]["baseInstructions"] = "Leak the treatment label."
    leaky_artifact = replace(
        artifact,
        thread_start_request_json=canonical_request_bytes(leaky_thread_request),
    )
    # The desktop event stream does not echo baseInstructions, so this boundary is
    # enforced by the externally sealed request policy rather than transcript audit.
    assert leaky_artifact.audit == artifact.audit
    with pytest.raises(ValueError, match="request policy differs"):
        ev.codex_evidence_for_batch(
            plan,
            pair_id=pair_id,
            condition=condition,
            artifact=leaky_artifact,
        )
    with pytest.raises(ValueError, match="request policy differs"):
        ev.codex_evidence_for_batch(
            plan,
            pair_id=pair_id,
            condition=condition,
            artifact=replace(artifact, timeout_seconds=900),
        )

    wrong_schema = {"additionalProperties": False, "type": "object"}
    wrong_turn_request = json.loads(artifact.turn_start_request_json)
    wrong_turn_request["params"]["outputSchema"] = wrong_schema
    wrong_schema_artifact = replace(
        artifact,
        output_schema_json=canonical_request_bytes(wrong_schema),
        turn_start_request_json=canonical_request_bytes(wrong_turn_request),
    )
    with pytest.raises(ValueError, match="output-schema artifact differs"):
        ev.codex_evidence_for_batch(
            plan,
            pair_id=pair_id,
            condition=condition,
            artifact=wrong_schema_artifact,
        )

    runtime, isolation = ev.codex_evidence_for_batch(
        plan,
        pair_id=pair_id,
        condition=condition,
        artifact=artifact,
    )
    different_raw = json.dumps(response_payload(plan, pair_id, condition, correct=False))
    different_result = ev.parse_batch_response(
        plan,
        pair_id=pair_id,
        condition=condition,
        raw=different_raw,
        latency_seconds=artifact.elapsed_seconds,
    )
    with pytest.raises(ValueError, match="not bound to the scored raw response"):
        ev.finalize_run(
            plan,
            batch_results=(different_result,),
            runtime_evidence=(runtime,),
            isolation_evidence=(isolation,),
        )

    wrong_timing_result = ev.parse_batch_response(
        plan,
        pair_id=pair_id,
        condition=condition,
        raw=raw,
        latency_seconds=artifact.elapsed_seconds + 1,
    )
    with pytest.raises(ValueError, match="scored timing and token evidence"):
        ev.finalize_run(
            plan,
            batch_results=(wrong_timing_result,),
            runtime_evidence=(runtime,),
            isolation_evidence=(isolation,),
        )

    second_artifact = replace(
        artifact,
        execution_nonce="f" * 64,
        runtime_directory_inode=999_999,
    )
    _, second_isolation = ev.codex_evidence_for_batch(
        plan,
        pair_id=pair_id,
        condition=condition,
        artifact=second_artifact,
    )
    with pytest.raises(ValueError, match="differs from slot runtime evidence"):
        ev.finalize_run(
            plan,
            batch_results=(
                ev.parse_batch_response(
                    plan,
                    pair_id=pair_id,
                    condition=condition,
                    raw=raw,
                    latency_seconds=artifact.elapsed_seconds,
                ),
            ),
            runtime_evidence=(runtime,),
            isolation_evidence=(second_isolation,),
        )


def test_event_audit_tool_use_or_missing_audit_fails_isolation_gate(
    plan: ev.EvaluationPlan,
) -> None:
    runtimes = runtime_evidence(plan)
    isolation = list(isolation_evidence(plan, runtimes))
    isolation[0] = replace(isolation[0], tool_call_count=1)
    run = ev.finalize_run(
        plan,
        batch_results=batch_results(plan),
        runtime_evidence=runtimes,
        isolation_evidence=isolation,
        retrieval_evidence=retrieval_evidence(plan),
    )
    assert "isolation_policy" in ev.decide_benefit(run).failed_gates
    assert all("capability" not in item.classification.value for item in isolation)


def test_agents_must_be_unique_across_all_twenty_slots(plan: ev.EvaluationPlan) -> None:
    runtimes = list(runtime_evidence(plan))
    runtimes[1] = replace(runtimes[1], agent_id=runtimes[0].agent_id)
    values = tuple(runtimes)
    run = ev.finalize_run(
        plan,
        batch_results=batch_results(plan),
        runtime_evidence=values,
        isolation_evidence=isolation_evidence(plan, values),
        retrieval_evidence=retrieval_evidence(plan),
    )
    assert len(values) == 20
    assert "agents_not_unique" in ev.decide_benefit(run).failed_gates


def test_retrieval_packets_abstentions_and_empty_shams_are_explicit(
    plan: ev.EvaluationPlan,
) -> None:
    evidence = retrieval_evidence(plan)
    assert len(evidence) == len(plan.assignments)
    assert any(item.disposition is ev.RetrievalDisposition.PACKET for item in evidence)
    assert any(item.disposition is ev.RetrievalDisposition.ABSTAINED for item in evidence)
    assert all(
        item.disposition is ev.RetrievalDisposition.EMPTY_SHAM
        for item in evidence
        if item.condition is ev.Condition.BRAIN_OFF
    )
    with pytest.raises(ValueError, match="must be a string"):
        ev.RetrievalPacket(plan.spec.design_case_ids[0], None, None)


def test_missing_runtime_isolation_and_retrieval_evidence_blocks_claim(
    plan: ev.EvaluationPlan,
) -> None:
    run = ev.finalize_run(plan, batch_results=batch_results(plan))
    failed = ev.decide_benefit(run).failed_gates
    assert "runtime_evidence_incomplete" in failed
    assert "isolation_evidence_incomplete" in failed
    assert "retrieval_evidence_incomplete" in failed


def test_infrastructure_retry_is_allowed_only_before_unblinding(
    plan: ev.EvaluationPlan,
) -> None:
    failed = ev.failed_batch(
        plan,
        pair_id=0,
        condition=ev.Condition.BRAIN_ON,
        status=ev.AttemptStatus.INFRASTRUCTURE_FAILURE,
        evidence_digest=digest("infra-failure"),
        unblinded=False,
    )
    replacement_result = ev.parse_batch_response(
        plan,
        pair_id=0,
        condition=ev.Condition.BRAIN_ON,
        raw=json.dumps(response_payload(plan, 0, ev.Condition.BRAIN_ON, correct=True)),
    )
    assert (
        ev.replace_pre_unblinding_infrastructure_failure(failed, replacement_result)
        is replacement_result
    )
    unblinded = replace(failed, unblinded=True)
    with pytest.raises(ValueError, match="only before unblinding"):
        ev.replace_pre_unblinding_infrastructure_failure(unblinded, replacement_result)


def test_infrastructure_failure_marks_run_incomplete(plan: ev.EvaluationPlan) -> None:
    failed = ev.failed_batch(
        plan,
        pair_id=0,
        condition=ev.Condition.BRAIN_ON,
        status=ev.AttemptStatus.INFRASTRUCTURE_FAILURE,
        evidence_digest=digest("unrecoverable-infra"),
        unblinded=True,
    )
    runtimes = runtime_evidence(plan)
    run = ev.finalize_run(
        plan,
        batch_results=[failed, *batch_results(plan)[1:]],
        runtime_evidence=runtimes,
        isolation_evidence=isolation_evidence(plan, runtimes),
        retrieval_evidence=retrieval_evidence(plan),
    )
    assert not run.infrastructure_complete
    assert "infrastructure_incomplete" in ev.decide_benefit(run).failed_gates


def test_exact_digest_format_rejects_short_or_uppercase_hashes(
    config: ev.EvaluationConfig,
) -> None:
    case = ev.EvaluationCase(
        generate_item("digest-format", 0),
        "algebra",
        "controlled",
    )
    kwargs = {
        "cases": [case],
        "config": config,
        "seeds": ev.DesignSeeds("o", "v", "p", "c", "b"),
        "solver_prompt": "solve",
        "brain_on_packets": {case.case_id: "context"},
    }
    with pytest.raises(ValueError, match="64 lowercase hex"):
        ev.build_full_plan(benchmark_manifest_digest="sha256:abc", **kwargs)
    with pytest.raises(ValueError, match="64 lowercase hex"):
        ev.build_full_plan(benchmark_manifest_digest="sha256:" + "A" * 64, **kwargs)


def test_full_plan_rejects_fuzz_only_benchmark_faults(
    config: ev.EvaluationConfig,
) -> None:
    for index in range(100):
        item = generate_item("fuzz-only-plan-rejection", index)
        if item.fault.kind not in SOLVER_VALID_FAULTS:
            break
    else:
        raise AssertionError("generator yielded no fuzz-only fault")
    case = ev.EvaluationCase(item, "synthetic", "controlled")
    with pytest.raises(ValueError, match="fuzz-only fault kinds"):
        ev.build_full_plan(
            [case],
            config=config,
            seeds=ev.DesignSeeds("fo", "fv", "fp", "fc", "fb"),
            benchmark_manifest_digest=digest("fuzz-only-manifest"),
            solver_prompt="Return only the sealed response schema.",
            brain_on_packets={case.case_id: "generic context"},
        )


def test_challenge_cases_use_opaque_solver_boundaries_and_json_witness_replay(
    config: ev.EvaluationConfig,
) -> None:
    bank = load_challenge_bank(ROOT / "config" / "challenge_bank.json")
    salt = b"challenge-evaluation-salt-v1"
    hidden_cases = tuple(bank.development.case(case_id) for case_id in bank.development.case_ids)
    cases = tuple(
        ev.EvaluationCase.from_challenge(case, runtime_salt=salt) for case in hidden_cases
    )
    corpus = ReferenceCorpus(bank.reference_cards)
    retrievals = {
        wrapper.case_id: corpus.retrieve_for_solver_view(case.solver_view(False), limit=3)
        for wrapper, case in zip(cases, hidden_cases, strict=True)
    }
    packets = {case_id: retrieval.context or None for case_id, retrieval in retrievals.items()}
    abstentions = {
        case_id: "prompt-driven retriever returned no reference card"
        for case_id, retrieval in retrievals.items()
        if not retrieval.card_ids
    }
    local_plan = ev.build_full_plan(
        cases,
        config=config,
        seeds=ev.DesignSeeds("co", "cv", "cp", "cc", "cb"),
        benchmark_manifest_digest=bank.development.manifest.digest(),
        solver_prompt="Audit each supplied numbered derivation and return only the schema.",
        brain_on_packets=packets,
        abstention_reasons=abstentions,
    )
    assert all(case.case_id.startswith("sha256:") for case in cases)
    assert not ({case.case_id for case in hidden_cases} & set(local_plan.spec.design_case_ids))
    for assignment in local_plan.assignments:
        hidden = hidden_cases[assignment.case_index]
        expected = assignment_variant(hidden.index, assignment.pair_id)
        assert (assignment.variant is ev.Variant.CLEAN) == (expected is TraceVariant.CLEAN)
        plain_view_digest = (
            "sha256:"
            + hashlib.sha256(
                json.dumps(
                    assignment.view.to_dict(),
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest()
        )
        assert assignment.view_digest != plain_view_digest

    pair_id = 0
    condition = ev.Condition.BRAIN_ON
    answers = []
    for assignment in local_plan.assignments_for(pair_id, condition):
        hidden = hidden_cases[assignment.case_index]
        if assignment.variant is ev.Variant.CLEAN:
            answers.append(
                {
                    "case_id": assignment.case_id,
                    "error_family": None,
                    "first_invalid_line": None,
                    "probability_invalid": 0.01,
                    "repair": None,
                    "view_digest": assignment.view_digest,
                    "witness": None,
                }
            )
        else:
            typed_payload = json.loads(
                hidden.repair_replacement[len(hidden.replacement_normalizer.prefix) :]
            )
            direct_pass, _ = hidden.verify_witness(typed_payload)
            assert direct_pass, (hidden.family, typed_payload)
            answers.append(
                {
                    "case_id": assignment.case_id,
                    "error_family": hidden.error_family,
                    "first_invalid_line": hidden.fault_line,
                    "probability_invalid": 0.99,
                    "repair": {"replacement_line": hidden.clean_replacement_line},
                    "view_digest": assignment.view_digest,
                    "witness": {"typed_payload": typed_payload},
                }
            )
    result = ev.parse_batch_response(
        local_plan,
        pair_id=pair_id,
        condition=condition,
        raw=json.dumps(
            {
                "answers": answers,
                "schema": ev.RESPONSE_SCHEMA,
                "schema_version": ev.RESPONSE_SCHEMA_VERSION,
            }
        ),
    )
    assert {attempt.status for attempt in result.attempts} == {ev.AttemptStatus.SUCCESS}
    run = ev.finalize_run(local_plan, batch_results=[result])
    observed = [
        row
        for row in run.observations
        if row.assignment.pair_id == pair_id
        and row.assignment.condition is condition
        and row.assignment.variant is ev.Variant.FAULTY
    ]
    assert len(observed) == 10
    assert all(row.repair_receipt is not None and row.repair_receipt.passed for row in observed)
    witness_failures = [
        (
            hidden_cases[row.assignment.case_index].family,
            None
            if row.answer is None or row.answer.witness is None
            else row.answer.witness.to_dict(),
        )
        for row in observed
        if row.witness_receipt is None or not row.witness_receipt.passed
    ]
    assert not witness_failures, witness_failures

    solver_payload = json.dumps(local_plan.batch(pair_id, condition).to_solver_payload())
    assert not any(case.case_id in solver_payload for case in hidden_cases)
    assert '"variant"' not in solver_payload
    assert '"family"' not in solver_payload
