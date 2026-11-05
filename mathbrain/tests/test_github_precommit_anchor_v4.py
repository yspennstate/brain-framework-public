from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

import math_brain.github_precommit_anchor_v4 as anchor
import math_brain.mathnet_random_external_lane_v4 as lane
from math_brain.models import canonical_json


def _digest(label: str) -> str:
    import hashlib

    return "sha256:" + hashlib.sha256(label.encode("utf-8")).hexdigest()


def _precommitment() -> lane.MathNetRandomExternalPrecommitmentV4:
    code = lane.freeze_current_mathnet_random_selector_code_v4(protocol_git_commit="a" * 40)
    return lane.MathNetRandomExternalPrecommitmentV4._derive(
        claimed_created_utc="2026-07-15T00:00:00Z",
        source_manifest_digest=_digest("source-manifest"),
        source_runtime_evidence_digest=_digest("source-runtime"),
        selector_code_manifest=code,
        beacon_round_spec=lane.DrandQuicknetRoundSpecV4(round_number=100_000_000),
    )


def _created_before(
    precommitment: lane.MathNetRandomExternalPrecommitmentV4,
    *,
    seconds: int = 120,
) -> str:
    scheduled = datetime.strptime(
        precommitment.beacon_round_spec.scheduled_utc,
        "%Y-%m-%dT%H:%M:%SZ",
    ).replace(tzinfo=UTC)
    return (scheduled - timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _response(
    precommitment: lane.MathNetRandomExternalPrecommitmentV4,
    *,
    created_at: str | None = None,
) -> bytes:
    body = anchor.render_github_precommit_anchor_body_v4(
        precommitment.canonical_record(),
        expected_precommitment_record_sha256=precommitment.record_sha256,
    )
    created = created_at or _created_before(precommitment)
    return canonical_json(
        {
            "author_association": "OWNER",
            "body": body,
            "created_at": created,
            "html_url": (
                "https://github.com/yspennstate/math-research-brain/issues/17#issuecomment-456"
            ),
            "id": 456,
            "issue_url": ("https://api.github.com/repos/yspennstate/math-research-brain/issues/17"),
            "node_id": "IC_kwDOAnchorV4",
            "updated_at": created,
            "url": (
                "https://api.github.com/repos/yspennstate/math-research-brain/issues/comments/456"
            ),
            "user": {
                "id": 42,
                "login": "yspennstate",
                "type": "User",
            },
        }
    ).encode("utf-8")


def _parse(
    response: bytes,
    precommitment: lane.MathNetRandomExternalPrecommitmentV4,
) -> anchor.GitHubPrecommitAnchorEvidenceV4:
    return anchor.parse_github_precommit_anchor_response_v4(
        response,
        precommitment.canonical_record(),
        expected_precommitment_record_sha256=precommitment.record_sha256,
        repository_owner="yspennstate",
        repository_name="math-research-brain",
        issue_number=17,
        comment_id=456,
        expected_actor_login="yspennstate",
        expected_actor_id=42,
    )


def test_anchor_body_binds_exact_precommitment_code_and_future_round() -> None:
    precommitment = _precommitment()
    body = anchor.render_github_precommit_anchor_body_v4(
        precommitment.canonical_record(),
        expected_precommitment_record_sha256=precommitment.record_sha256,
    )
    parsed = json.loads(body)

    assert body == canonical_json(parsed)
    assert body.encode("ascii").decode("ascii") == body
    assert parsed["precommitment_record_sha256"] == precommitment.record_sha256
    assert parsed["precommitment_content_digest"] == precommitment.content_digest
    assert parsed["beacon_round_number"] == 100_000_000
    assert parsed["beacon_round_spec_digest"] == (precommitment.beacon_round_spec.content_digest)
    assert parsed["minimum_lead_seconds"] == 60
    assert parsed["implementation_revision"] == "a" * 40


def test_normalized_evidence_binds_server_time_repository_actor_and_comment() -> None:
    precommitment = _precommitment()
    evidence = _parse(_response(precommitment), precommitment)
    public = evidence.to_dict()

    assert evidence.lead_seconds == 120
    assert evidence.comment_id == 456
    assert evidence.actor_id == 42
    assert evidence.precommitment_record_sha256 == precommitment.record_sha256
    assert public["authority"] == {
        "cached_json_is_chronology_proof": False,
        "external_chronology_authority_verified": False,
        "in_process_execution_identity_attested": False,
        "nominal_python_record_is_chronology_proof": False,
        "requires_fresh_live_https_verification": True,
    }
    serialized = canonical_json(public)
    assert "problem_text" not in serialized
    assert "source_manifest" not in serialized
    assert "C:\\" not in serialized


def test_edited_comment_is_not_prebeacon_evidence() -> None:
    precommitment = _precommitment()
    parsed = json.loads(_response(precommitment))
    parsed["updated_at"] = (
        datetime.strptime(parsed["created_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        + timedelta(seconds=1)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    with pytest.raises(ValueError, match="edited after creation"):
        _parse(canonical_json(parsed).encode("utf-8"), precommitment)


@pytest.mark.parametrize("lead_seconds", [59, 0, -1])
def test_comment_must_precede_the_future_round_by_the_frozen_margin(
    lead_seconds: int,
) -> None:
    precommitment = _precommitment()
    response = _response(
        precommitment,
        created_at=_created_before(precommitment, seconds=lead_seconds),
    )

    with pytest.raises(ValueError, match="did not precede"):
        _parse(response, precommitment)


@pytest.mark.parametrize(
    ("field", "replacement", "match"),
    [
        ("body", "{}", "identity or body differs"),
        ("author_association", "COLLABORATOR", "identity or body differs"),
        (
            "url",
            "https://api.github.com/repos/yspennstate/other/issues/comments/456",
            "URLs differ",
        ),
        (
            "html_url",
            "https://example.invalid/yspennstate/math-research-brain/issues/17#issuecomment-456",
            "HTML URL",
        ),
    ],
)
def test_body_actor_and_repository_substitution_fail_closed(
    field: str,
    replacement: str,
    match: str,
) -> None:
    precommitment = _precommitment()
    parsed = json.loads(_response(precommitment))
    parsed[field] = replacement

    with pytest.raises(ValueError, match=match):
        _parse(canonical_json(parsed).encode("utf-8"), precommitment)


def test_wrong_precommitment_record_digest_fails_before_comment_parsing() -> None:
    precommitment = _precommitment()

    with pytest.raises(ValueError, match="precommitment bytes differ"):
        anchor.parse_github_precommit_anchor_response_v4(
            _response(precommitment),
            precommitment.canonical_record(),
            expected_precommitment_record_sha256=_digest("substituted-record"),
            repository_owner="yspennstate",
            repository_name="math-research-brain",
            issue_number=17,
            comment_id=456,
            expected_actor_login="yspennstate",
            expected_actor_id=42,
        )


def test_authority_consumer_refetches_live_and_compares_external_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    precommitment = _precommitment()
    raw = _response(precommitment)
    expected = _parse(raw, precommitment)
    calls = {"count": 0}

    def fetch(
        repository_owner: str,
        repository_name: str,
        comment_id: int,
        *,
        timeout_seconds: int,
    ) -> bytes:
        calls["count"] += 1
        assert (repository_owner, repository_name, comment_id) == (
            "yspennstate",
            "math-research-brain",
            456,
        )
        assert timeout_seconds == 20
        return raw

    monkeypatch.setattr(anchor, "_fetch_github_comment_json_v4", fetch)
    verified = anchor.verify_live_github_precommit_anchor_v4(
        precommitment.canonical_record(),
        expected_precommitment_record_sha256=precommitment.record_sha256,
        expected_anchor_evidence_digest=expected.content_digest,
        repository_owner="yspennstate",
        repository_name="math-research-brain",
        issue_number=17,
        comment_id=456,
        expected_actor_login="yspennstate",
        expected_actor_id=42,
    )

    assert calls == {"count": 1}
    assert verified == expected

    with pytest.raises(ValueError, match="differs from the sealed evidence digest"):
        anchor.verify_live_github_precommit_anchor_v4(
            precommitment.canonical_record(),
            expected_precommitment_record_sha256=precommitment.record_sha256,
            expected_anchor_evidence_digest=_digest("substituted-anchor-evidence"),
            repository_owner="yspennstate",
            repository_name="math-research-brain",
            issue_number=17,
            comment_id=456,
            expected_actor_login="yspennstate",
            expected_actor_id=42,
        )
    assert calls == {"count": 2}


def test_duplicate_json_keys_are_rejected() -> None:
    precommitment = _precommitment()
    valid = _response(precommitment).decode("utf-8")
    duplicate = valid.replace('{"author_association":', '{"id":456,"author_association":', 1)

    with pytest.raises(ValueError, match="duplicate JSON key"):
        _parse(duplicate.encode("utf-8"), precommitment)
