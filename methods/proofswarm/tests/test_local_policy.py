from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from proofswarm import calibration
from proofswarm.local_runtime import (
    LocalPolicyError,
    require_local_compute,
    validate_loopback_base_url,
)
from proofswarm.schemas import Evidence, ProofTicket, Verdict, gate_verdict
from proofswarm.tools import ticket_store


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "script_name",
    ["01_enable_apis.sh", "02_build_lean.sh", "03_init_firestore.sh"],
)
def test_legacy_cloud_bootstraps_are_fail_closed_tombstones(script_name: str) -> None:
    source = (ROOT / "infra" / script_name).read_text(encoding="utf-8")
    executable = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("#")
    )
    assert "exit 64" in executable
    assert "gcloud" not in executable
    assert "curl " not in executable
    assert "docker " not in executable


def test_historical_grounded_probe_is_neutralized() -> None:
    probe = json.loads((ROOT / "_probe_grounded.json").read_text(encoding="utf-8"))
    assert probe == {
        "disabled": True,
        "policy": "local-only-zero-cloud-fallback",
        "inference_backend": "local",
        "remote_requests_allowed": False,
        "reason": "Historical remote probe neutralized; use the loopback ProofSwarm runtime.",
    }


@pytest.mark.parametrize(
    "url",
    [
        "https://aiplatform.googleapis.com/v1",
        "http://192.168.1.10:11434/v1",
        "http://example.com/v1",
        "file:///tmp/model",
    ],
)
def test_model_url_must_be_loopback(url: str) -> None:
    with pytest.raises(LocalPolicyError, match="loopback|http"):
        validate_loopback_base_url(url)


def test_ipv4_ipv6_and_localhost_loopback_are_allowed() -> None:
    assert validate_loopback_base_url("http://127.0.0.1:11434/v1").startswith("http://")
    assert validate_loopback_base_url("http://[::1]:11434/v1").startswith("http://")
    assert validate_loopback_base_url("http://localhost:11434/v1").startswith("http://")


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("PROOFSWARM_BACKEND", "firestore"),
        ("PROOFSWARM_BACKEND", "vertex"),
        ("PROOFSWARM_INFERENCE_BACKEND", "vertex"),
        ("PROOFSWARM_STORAGE_BACKEND", "firestore"),
    ],
)
def test_cloud_and_legacy_selectors_fail_at_import(name: str, value: str) -> None:
    env = os.environ.copy()
    for key in (
        "PROOFSWARM_BACKEND",
        "PROOFSWARM_INFERENCE_BACKEND",
        "PROOFSWARM_STORAGE_BACKEND",
    ):
        env.pop(key, None)
    env[name] = value
    result = subprocess.run(
        [sys.executable, "-B", "-c", "import proofswarm.config"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
        timeout=30,
    )
    assert result.returncode != 0
    assert "local-only" in result.stderr


@pytest.mark.parametrize("value", [r"\\server\share\tickets", "relative/tickets", "https://host/path"])
def test_local_storage_paths_reject_remote_or_relative_locations(value: str) -> None:
    from proofswarm.config import _local_absolute_path

    with pytest.raises(LocalPolicyError, match="network|absolute"):
        _local_absolute_path("TEST_PATH", value)


def test_compute_gate_requires_fresh_owner_away_open_state(tmp_path: Path) -> None:
    state_path = tmp_path / "compute_state.json"
    state_path.write_text(
        json.dumps(
            {
                "ts": datetime.now().isoformat(),
                "status": "ok",
                "mode": "owner_away_open",
                "owner_active": False,
                "owner_away": True,
                "commit_avail_mb": 20000,
                "mem_avail_mb": 12000,
            }
        ),
        encoding="utf-8",
    )
    assert require_local_compute(state_path)["mode"] == "owner_away_open"
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    payload["owner_active"] = True
    payload["owner_away"] = False
    state_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(LocalPolicyError, match="owner"):
        require_local_compute(state_path)

    payload["owner_active"] = False
    payload["owner_away"] = True
    payload["ts"] = (datetime.now() - timedelta(minutes=5)).isoformat()
    state_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(LocalPolicyError, match="payload is stale"):
        require_local_compute(state_path)


def test_local_model_request_has_zero_cost_and_no_remote_fallback(monkeypatch) -> None:
    import proofswarm.orchestrator as orchestrator

    observed: dict[str, object] = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _size: int = -1) -> bytes:
            return json.dumps(
                {"choices": [{"message": {"content": "local answer"}}]}
            ).encode("utf-8")

    def fake_urlopen(request, timeout):
        observed["url"] = request.full_url
        observed["body"] = json.loads(request.data)
        observed["timeout"] = timeout
        return Response()

    monkeypatch.setattr(orchestrator, "require_local_compute", lambda: {})
    monkeypatch.setattr(orchestrator, "exclusive_local_model_slot", lambda: nullcontext())
    monkeypatch.setattr(orchestrator.urllib.request, "urlopen", fake_urlopen)
    text, cost = orchestrator.call_model(
        "legacy-nonlocal-label", "system", "user", max_retries=0
    )
    assert text == "local answer"
    assert cost == 0.0
    assert str(observed["url"]).startswith(("http://127.0.0.1", "http://localhost"))
    assert observed["body"]["model"] == orchestrator.LOCAL_MODEL


def test_local_model_request_size_is_bounded_before_launch(monkeypatch) -> None:
    import proofswarm.orchestrator as orchestrator

    launched = False

    def fake_urlopen(*_args, **_kwargs):
        nonlocal launched
        launched = True
        raise AssertionError("oversized request must not launch")

    monkeypatch.setattr(orchestrator.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(LocalPolicyError, match="memory-safe input bound"):
        orchestrator.call_model(
            orchestrator.LOCAL_MODEL,
            "system",
            "x" * orchestrator.MAX_LOCAL_REQUEST_BYTES,
            max_retries=0,
        )
    assert not launched


def test_compute_denial_happens_before_local_model_slot(monkeypatch) -> None:
    import proofswarm.orchestrator as orchestrator

    slot_entered = False

    def deny_compute() -> None:
        raise LocalPolicyError("governor is throttled")

    def forbidden_slot():
        nonlocal slot_entered
        slot_entered = True
        return nullcontext()

    monkeypatch.setattr(orchestrator, "require_local_compute", deny_compute)
    monkeypatch.setattr(orchestrator, "exclusive_local_model_slot", forbidden_slot)
    with pytest.raises(LocalPolicyError, match="throttled"):
        orchestrator.call_model(
            orchestrator.LOCAL_MODEL, "system", "user", max_retries=0
        )
    assert not slot_entered


def test_positive_verdicts_require_mechanical_evidence() -> None:
    ticket = ProofTicket(ticket_id="L1", problem_id="p", statement_informal="claim")
    effective, reason = gate_verdict(ticket, Verdict.LEAN_VERIFIED)
    assert effective is Verdict.OPEN
    assert "denied" in reason

    compiled_source = "theorem exact : True := by trivial"
    source_digest = hashlib.sha256(compiled_source.encode("utf-8")).hexdigest()
    ticket.add_evidence(
        Evidence(
            kind="lean_proof",
            content=compiled_source,
            sha256=source_digest,
            produced_by="prover",
            metadata={
                "verify": {
                    "ok": True,
                    "elaborated": True,
                    "sorry_count": 0,
                    "source_sha256": source_digest,
                }
            },
        )
    )
    effective, reason = gate_verdict(ticket, Verdict.LEAN_VERIFIED)
    assert effective is Verdict.NEEDS_HUMAN
    assert "fidelity review pending" in reason

    review = "I compared the complete informal claim to the exact compiled Lean source."
    review_digest = hashlib.sha256(review.encode("utf-8")).hexdigest()
    ticket.add_evidence(
        Evidence(
            kind="referee_finding",
            content=review,
            sha256=review_digest,
            produced_by="human",
            metadata={
                "statement_fidelity": {
                    "verified": True,
                    "reviewer_kind": "human",
                    "informal_sha256": hashlib.sha256(b"claim").hexdigest(),
                    "lean_source_sha256": source_digest,
                    "review_sha256": review_digest,
                }
            },
        )
    )
    effective, _reason = gate_verdict(ticket, Verdict.LEAN_VERIFIED)
    assert effective is Verdict.LEAN_VERIFIED


def test_kernel_digest_must_be_canonical_lowercase_hex() -> None:
    ticket = ProofTicket(ticket_id="L2", problem_id="p", statement_informal="claim")
    ticket.add_evidence(
        Evidence(
            kind="lean_proof",
            content="compiled",
            produced_by="prover",
            metadata={
                "verify": {
                    "ok": True,
                    "elaborated": True,
                    "sorry_count": 0,
                    "source_sha256": "G" * 64,
                }
            },
        )
    )
    effective, reason = gate_verdict(ticket, Verdict.LEAN_VERIFIED)
    assert effective is Verdict.OPEN
    assert "exact-source kernel evidence" in reason


@pytest.mark.parametrize(
    ("requested", "subject_kind", "metadata_key"),
    [
        (Verdict.CERTIFICATE_VERIFIED, "numerical_certificate", "certificate_verified"),
        (Verdict.DISPROVEN, "counterexample", "counterexample_verified"),
    ],
)
def test_unbound_positive_metadata_cannot_promote(
    requested: Verdict, subject_kind: str, metadata_key: str
) -> None:
    ticket = ProofTicket(ticket_id="L3", problem_id="p", statement_informal="claim")
    ticket.add_evidence(
        Evidence(
            kind=subject_kind,
            content="model-authored claim of verification",
            produced_by="model",
            metadata={metadata_key: True},
        )
    )
    effective, reason = gate_verdict(ticket, requested)
    assert effective is Verdict.OPEN
    assert "independently bound" in reason


@pytest.mark.parametrize(
    (
        "requested",
        "subject_kind",
        "verification_key",
        "subject_digest_key",
        "mechanical_producer",
    ),
    [
        (
            Verdict.CERTIFICATE_VERIFIED,
            "numerical_certificate",
            "certificate_verification",
            "certificate_evidence_sha256",
            "local_certificate_verifier",
        ),
        (
            Verdict.DISPROVEN,
            "counterexample",
            "counterexample_verification",
            "counterexample_evidence_sha256",
            "local_counterexample_verifier",
        ),
    ],
)
def test_positive_nonlean_verdict_requires_exact_subject_and_independent_record(
    requested: Verdict,
    subject_kind: str,
    verification_key: str,
    subject_digest_key: str,
    mechanical_producer: str,
) -> None:
    ticket = ProofTicket(ticket_id="L4", problem_id="p", statement_informal="claim")
    subject = "exact deterministic witness"
    subject_digest = hashlib.sha256(subject.encode("utf-8")).hexdigest()
    ticket.add_evidence(
        Evidence(
            kind=subject_kind,
            content=subject,
            sha256=subject_digest,
            produced_by="search_worker",
        )
    )
    verification_record = "independent deterministic replay matched the witness"
    record_digest = hashlib.sha256(verification_record.encode("utf-8")).hexdigest()
    ticket.add_evidence(
        Evidence(
            kind="referee_finding",
            content=verification_record,
            sha256=record_digest,
            produced_by=mechanical_producer,
            metadata={
                verification_key: {
                    "verified": True,
                    "verifier_kind": "mechanical",
                    "verification_method": "deterministic_replay",
                    subject_digest_key: subject_digest,
                    "verification_record_sha256": record_digest,
                    "statement_sha256": hashlib.sha256(b"claim").hexdigest(),
                    "ticket_id": ticket.ticket_id,
                    "problem_id": ticket.problem_id,
                }
            },
        )
    )
    effective, _reason = gate_verdict(ticket, requested)
    assert effective is requested

    ticket.evidence[0].content += " tampered"
    effective, reason = gate_verdict(ticket, requested)
    assert effective is Verdict.OPEN
    assert "independently bound" in reason


def _passing_calibration_invoke(_role: str, _system: str, user: str) -> tuple[str, float]:
    control_id = json.loads(user)["control_id"]
    expected = calibration.CONTROL_CASES[control_id]["expected"]
    return json.dumps(expected, sort_keys=True), 0.0


def test_calibration_receipt_binds_prompts_outputs_and_freshness(
    monkeypatch, tmp_path: Path
) -> None:
    now = datetime(2026, 8, 2, 6, 0, tzinfo=timezone.utc)
    prompts = {"prover": "prove exactly", "referee": "audit exactly"}
    roles = ("prover", "referee")
    receipt = calibration.build_calibration_receipt(
        "test_surface", prompts, roles, _passing_calibration_invoke, now=now
    )
    calibration.validate_calibration_receipt(
        receipt, "test_surface", prompts, roles, now=now + timedelta(hours=1)
    )

    monkeypatch.setattr(calibration, "CALIBRATION_DIR", tmp_path)
    calibration.write_calibration_receipt("test_surface", receipt)
    loaded = calibration.require_calibration_receipt(
        "test_surface", prompts, roles, now=now + timedelta(hours=1)
    )
    assert loaded["run_sha256"] == receipt["run_sha256"]

    with pytest.raises(LocalPolicyError, match="stale"):
        calibration.validate_calibration_receipt(
            receipt, "test_surface", prompts, roles, now=now + timedelta(hours=25)
        )


def test_calibration_fails_closed_on_contradictory_role_output() -> None:
    prompts = {"referee": "audit exactly"}

    def contradictory(_role: str, _system: str, user: str) -> tuple[str, float]:
        control_id = json.loads(user)["control_id"]
        answer = dict(calibration.CONTROL_CASES[control_id]["expected"])
        if control_id == "known_false":
            answer["classification"] = "sound"
        return json.dumps(answer), 0.0

    receipt = calibration.build_calibration_receipt(
        "test_surface", prompts, ("referee",), contradictory
    )
    assert receipt["passed"] is False
    with pytest.raises(LocalPolicyError, match="cover|failed"):
        calibration.validate_calibration_receipt(
            receipt, "test_surface", prompts, ("referee",)
        )


def test_ticket_ids_that_sanitize_the_same_do_not_overwrite(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(ticket_store, "_LOCAL_DIR", tmp_path)
    first = ProofTicket(ticket_id="lemma:a", problem_id="p", statement_informal="first")
    second = ProofTicket(ticket_id="lemma_a", problem_id="p", statement_informal="second")
    ticket_store.save_ticket(first)
    ticket_store.save_ticket(second)
    assert ticket_store.load_ticket(first.ticket_id) == first
    assert ticket_store.load_ticket(second.ticket_id) == second
    assert {ticket.ticket_id for ticket in ticket_store.list_tickets("p")} == {
        "lemma:a",
        "lemma_a",
    }


def test_stale_ticket_writer_cannot_overwrite_new_evidence(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(ticket_store, "_LOCAL_DIR", tmp_path)
    original = ProofTicket(ticket_id="shared", problem_id="p", statement_informal="claim")
    ticket_store.save_ticket(original)
    first_writer = ticket_store.load_ticket("shared")
    stale_writer = ticket_store.load_ticket("shared")
    assert first_writer is not None and stale_writer is not None

    first_writer.statement_informal = "new exact evidence"
    ticket_store.save_ticket(first_writer)
    stale_writer.statement_informal = "lost update"
    with pytest.raises(ticket_store.TicketStoreConflict, match="stale ticket revision"):
        ticket_store.save_ticket(stale_writer)
    assert ticket_store.load_ticket("shared") == first_writer


def test_oversized_ticket_fails_before_replacing_current_file(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(ticket_store, "_LOCAL_DIR", tmp_path)
    original = ProofTicket(ticket_id="bounded", problem_id="p", statement_informal="small")
    ticket_store.save_ticket(original)
    current_size = ticket_store._ticket_path("bounded").stat().st_size
    monkeypatch.setattr(ticket_store, "MAX_TICKET_BYTES", current_size + 128)
    original.statement_informal = "x" * 1024
    with pytest.raises(ticket_store.TicketStoreConflict, match="persistence bound"):
        ticket_store.save_ticket(original)
    monkeypatch.setattr(ticket_store, "MAX_TICKET_BYTES", 8 * 1024 * 1024)
    persisted = ticket_store.load_ticket("bounded")
    assert persisted is not None and persisted.statement_informal == "small"


def test_paper_review_resumes_only_the_same_open_claim(monkeypatch, tmp_path: Path) -> None:
    from proofswarm import paper_review

    monkeypatch.setattr(ticket_store, "_LOCAL_DIR", tmp_path)
    first = paper_review._load_or_seed_review_ticket(
        "claim-1", "paper", "exact statement", detail="first pass"
    )
    resumed = paper_review._load_or_seed_review_ticket(
        "claim-1", "paper", "exact statement", detail="resume"
    )
    assert resumed.store_revision == first.store_revision + 1
    assert resumed.history[-1]["action"] == "resumed"

    with pytest.raises(LocalPolicyError, match="different claim"):
        paper_review._load_or_seed_review_ticket(
            "claim-1", "paper", "different statement", detail="collision"
        )
