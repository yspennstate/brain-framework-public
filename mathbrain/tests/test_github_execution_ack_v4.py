from __future__ import annotations

import hashlib
from typing import Any

import pytest

import math_brain.github_execution_ack_v4 as execution_ack
from math_brain.models import canonical_json


def digest(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode("utf-8")).hexdigest()


ACK_INPUTS = {
    "execution_authorization_digest": digest("execution-authorization"),
    "execution_precommitment_digest": digest("execution-precommitment"),
    "ledger_digest": digest("ledger"),
    "execution_public_record_digest": digest("public-record"),
    "plan_digest": digest("plan"),
    "task_map_public_precommitment_digest": digest("task-map"),
    "execution_policy_digest": digest("policy"),
    "implementation_manifest_digest": digest("manifest"),
}


def comment(body: str, *, comment_id: int = 9001, updated_at: str | None = None) -> dict[str, Any]:
    return {
        "body": body,
        "created_at": "2026-07-15T16:00:00Z",
        "id": comment_id,
        "issue_url": ("https://api.github.com/repos/yspennstate/math-research-brain/issues/77"),
        "node_id": "IC_kwDOABC123",
        "updated_at": updated_at or "2026-07-15T16:00:00Z",
        "user": {"id": 123456, "login": "yspennstate"},
    }


def install_live_responses(
    monkeypatch: pytest.MonkeyPatch,
    *,
    target: dict[str, Any],
    issue_comments: list[dict[str, Any]],
) -> None:
    def fetch(url: str, *, timeout_seconds: int = 30) -> bytes:
        assert timeout_seconds == 30
        payload: object = issue_comments if "?per_page=" in url else target
        return canonical_json(payload).encode("utf-8")

    monkeypatch.setattr(execution_ack, "_fetch_github_json_bytes_v4", fetch)


def fetch(monkeypatch: pytest.MonkeyPatch) -> execution_ack.GitHubExecutionAcknowledgementV4:
    body = execution_ack.render_github_execution_ack_body_v4(**ACK_INPUTS)
    target = comment(body)
    install_live_responses(monkeypatch, target=target, issue_comments=[target])
    return execution_ack.fetch_github_execution_ack_v4(
        **ACK_INPUTS,
        repository_owner="yspennstate",
        repository_name="math-research-brain",
        issue_number=77,
        comment_id=9001,
        actor_login="yspennstate",
        actor_id=123456,
    )


def test_unique_live_ack_round_trip_binds_every_execution_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence = fetch(monkeypatch)

    assert evidence.execution_authorization_digest == ACK_INPUTS["execution_authorization_digest"]
    assert evidence.execution_precommitment_digest == ACK_INPUTS["execution_precommitment_digest"]
    assert evidence.ledger_digest == ACK_INPUTS["ledger_digest"]
    assert evidence.execution_public_record_digest == ACK_INPUTS["execution_public_record_digest"]
    assert (
        execution_ack.verify_live_github_execution_ack_v4(
            **ACK_INPUTS,
            expected_ack_evidence_digest=evidence.content_digest,
            repository_owner="yspennstate",
            repository_name="math-research-brain",
            issue_number=77,
            comment_id=9001,
            actor_login="yspennstate",
            actor_id=123456,
        )
        == evidence
    )


def test_edited_ack_is_never_execution_authority(monkeypatch: pytest.MonkeyPatch) -> None:
    body = execution_ack.render_github_execution_ack_body_v4(**ACK_INPUTS)
    edited = comment(body, updated_at="2026-07-15T16:00:01Z")
    install_live_responses(monkeypatch, target=edited, issue_comments=[edited])

    with pytest.raises(ValueError, match="edited after creation"):
        execution_ack.fetch_github_execution_ack_v4(
            **ACK_INPUTS,
            repository_owner="yspennstate",
            repository_name="math-research-brain",
            issue_number=77,
            comment_id=9001,
            actor_login="yspennstate",
            actor_id=123456,
        )


def test_second_execution_ack_makes_campaign_infeasible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = execution_ack.render_github_execution_ack_body_v4(**ACK_INPUTS)
    target = comment(body)
    second = comment(body, comment_id=9002)
    install_live_responses(
        monkeypatch,
        target=target,
        issue_comments=[target, second],
    )

    with pytest.raises(ValueError, match="exactly one execution acknowledgement"):
        execution_ack.fetch_github_execution_ack_v4(
            **ACK_INPUTS,
            repository_owner="yspennstate",
            repository_name="math-research-brain",
            issue_number=77,
            comment_id=9001,
            actor_login="yspennstate",
            actor_id=123456,
        )


def test_live_ack_cannot_be_rebound_to_an_unexpected_evidence_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetch(monkeypatch)

    with pytest.raises(ValueError, match="differs from sealed evidence"):
        execution_ack.verify_live_github_execution_ack_v4(
            **ACK_INPUTS,
            expected_ack_evidence_digest=digest("attacker-evidence"),
            repository_owner="yspennstate",
            repository_name="math-research-brain",
            issue_number=77,
            comment_id=9001,
            actor_login="yspennstate",
            actor_id=123456,
        )


def test_ack_body_has_one_line_and_no_private_execution_material() -> None:
    body = execution_ack.render_github_execution_ack_body_v4(**ACK_INPUTS)

    assert "\n" not in body
    assert "prompt" not in body
    assert "response" not in body
    assert "ledger_key" not in body
    assert body.startswith(execution_ack.RANDOM_EXTERNAL_EXECUTION_ACK_PREFIX_V4)
