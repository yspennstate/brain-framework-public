"""Live GitHub issue-comment chronology evidence for the v4 random lane.

The random selector's own ``claimed_created_utc`` is not chronology authority.  This
module instead renders the exact selector precommitment as an ASCII GitHub comment and
requires GitHub's live REST representation to report an unchanged server-side creation
time at least sixty seconds before the committed future drand round.

The normalized record is still only integrity evidence.  An authority-bearing consumer
must call :func:`verify_live_github_precommit_anchor_v4` itself from pinned code; accepting
a caller-supplied Python object or cached JSON does not establish that the live HTTPS read
occurred.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import ssl
import urllib.request
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from .mathnet_random_external_lane_v4 import (
    MathNetRandomExternalPrecommitmentV4,
)
from .models import canonical_json

GITHUB_PRECOMMIT_ANCHOR_SCHEMA_V4 = (
    "org.math-research-brain/github-issue-comment-precommit-anchor/v4"
)
GITHUB_PRECOMMIT_ANCHOR_EVIDENCE_SCHEMA_V4 = (
    "org.math-research-brain/github-issue-comment-precommit-anchor-evidence/v4"
)
GITHUB_REST_API_VERSION_V4 = "2022-11-28"
GITHUB_PRECOMMIT_MINIMUM_LEAD_SECONDS_V4 = 60

_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_GIT_OID_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_IDENTIFIER_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,99})\Z")
_NODE_ID_RE = re.compile(r"[A-Za-z0-9_=-]{1,256}\Z")
_UTC_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z")
_MAX_COMMENT_BYTES_V4 = 64_000
_MAX_RESPONSE_BYTES_V4 = 1_000_000
_EVIDENCE_DOMAIN_V4 = "org.math-research-brain/github-precommit-anchor-evidence/v4"


def _sha256(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _digest(value: object, label: str) -> str:
    if type(value) is not str or _DIGEST_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be an exact SHA-256 digest")
    return value


def _identifier(value: object, label: str) -> str:
    if type(value) is not str or _IDENTIFIER_RE.fullmatch(value) is None:
        raise ValueError(f"{label} is invalid")
    return value


def _positive_int(value: object, label: str) -> int:
    if type(value) is not int or value < 1:
        raise ValueError(f"{label} must be a positive exact integer")
    return value


def _strict_utc(value: object, label: str) -> datetime:
    if type(value) is not str or _UTC_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be canonical UTC text")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError as error:  # pragma: no cover - regex excludes most malformed values
        raise ValueError(f"{label} must be canonical UTC text") from error
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise ValueError(f"{label} must be canonical UTC text")
    return parsed


def _reject_duplicate_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("GitHub anchor response contains a duplicate JSON key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"GitHub anchor response contains forbidden JSON constant {value}")


def _strict_json(raw: bytes) -> dict[str, Any]:
    if type(raw) is not bytes or not 1 <= len(raw) <= _MAX_RESPONSE_BYTES_V4:
        raise ValueError("GitHub anchor response must be bounded immutable bytes")
    try:
        text = raw.decode("utf-8", errors="strict")
        parsed = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("GitHub anchor response is not strict UTF-8 JSON") from error
    if type(parsed) is not dict:
        raise ValueError("GitHub anchor response must be one exact object")
    return parsed


def _precommitment_from_raw(
    precommitment_bytes: bytes,
    expected_precommitment_record_sha256: str,
) -> MathNetRandomExternalPrecommitmentV4:
    if type(precommitment_bytes) is not bytes or not precommitment_bytes:
        raise ValueError("GitHub anchor requires immutable precommitment bytes")
    expected = _digest(
        expected_precommitment_record_sha256,
        "expected random precommitment record",
    )
    if not hmac.compare_digest(_sha256(precommitment_bytes), expected):
        raise ValueError("random precommitment bytes differ from the external commitment")
    parsed = MathNetRandomExternalPrecommitmentV4.from_canonical_record(precommitment_bytes)
    parsed.validate_current()
    if not hmac.compare_digest(parsed.record_sha256, expected):
        raise ValueError("parsed random precommitment differs from its record digest")
    return parsed


def render_github_precommit_anchor_body_v4(
    precommitment_bytes: bytes,
    *,
    expected_precommitment_record_sha256: str,
) -> str:
    """Render the one-line ASCII body that must be posted before the beacon round."""

    precommitment = _precommitment_from_raw(
        precommitment_bytes,
        expected_precommitment_record_sha256,
    )
    spec = precommitment.beacon_round_spec
    body = canonical_json(
        {
            "beacon_round_number": spec.round_number,
            "beacon_round_scheduled_utc": spec.scheduled_utc,
            "beacon_round_spec_digest": spec.content_digest,
            "implementation_revision": (precommitment.selector_code_manifest.protocol_git_commit),
            "minimum_lead_seconds": GITHUB_PRECOMMIT_MINIMUM_LEAD_SECONDS_V4,
            "precommitment_content_digest": precommitment.content_digest,
            "precommitment_record_sha256": precommitment.record_sha256,
            "schema": GITHUB_PRECOMMIT_ANCHOR_SCHEMA_V4,
            "schema_version": 4,
        }
    )
    encoded = body.encode("ascii", errors="strict")
    if not 1 <= len(encoded) <= _MAX_COMMENT_BYTES_V4:
        raise ValueError("GitHub precommit anchor body exceeds its frozen bound")
    return body


@dataclass(frozen=True, slots=True)
class GitHubPrecommitAnchorEvidenceV4:
    """Normalized live-comment fields; nominal construction is not HTTPS proof."""

    repository_owner: str
    repository_name: str
    issue_number: int
    comment_id: int
    comment_node_id: str
    actor_login: str
    actor_id: int
    author_association: str
    api_url: str
    issue_url: str
    html_url: str
    created_at: str
    updated_at: str
    lead_seconds: int
    anchor_body_sha256: str
    precommitment_record_sha256: str
    precommitment_content_digest: str
    beacon_round_spec_digest: str
    beacon_round_number: int
    beacon_round_scheduled_utc: str
    implementation_revision: str
    content_digest: str

    def _payload(self) -> dict[str, object]:
        return {
            "actor_id": self.actor_id,
            "actor_login": self.actor_login,
            "anchor_body_sha256": self.anchor_body_sha256,
            "api_url": self.api_url,
            "author_association": self.author_association,
            "beacon_round_number": self.beacon_round_number,
            "beacon_round_scheduled_utc": self.beacon_round_scheduled_utc,
            "beacon_round_spec_digest": self.beacon_round_spec_digest,
            "comment_id": self.comment_id,
            "comment_node_id": self.comment_node_id,
            "created_at": self.created_at,
            "html_url": self.html_url,
            "implementation_revision": self.implementation_revision,
            "issue_number": self.issue_number,
            "issue_url": self.issue_url,
            "lead_seconds": self.lead_seconds,
            "minimum_lead_seconds": GITHUB_PRECOMMIT_MINIMUM_LEAD_SECONDS_V4,
            "precommitment_content_digest": self.precommitment_content_digest,
            "precommitment_record_sha256": self.precommitment_record_sha256,
            "repository_name": self.repository_name,
            "repository_owner": self.repository_owner,
            "schema": GITHUB_PRECOMMIT_ANCHOR_EVIDENCE_SCHEMA_V4,
            "schema_version": 4,
            "updated_at": self.updated_at,
            "authority": {
                "cached_json_is_chronology_proof": False,
                "external_chronology_authority_verified": False,
                "in_process_execution_identity_attested": False,
                "nominal_python_record_is_chronology_proof": False,
                "requires_fresh_live_https_verification": True,
            },
        }

    def validate_current(self) -> None:
        if type(self) is not GitHubPrecommitAnchorEvidenceV4:
            raise ValueError("GitHub precommit anchor subclasses are forbidden")
        owner = _identifier(self.repository_owner, "GitHub repository owner")
        repository = _identifier(self.repository_name, "GitHub repository name")
        issue_number = _positive_int(self.issue_number, "GitHub issue number")
        comment_id = _positive_int(self.comment_id, "GitHub comment id")
        _identifier(self.actor_login, "GitHub anchor actor login")
        _positive_int(self.actor_id, "GitHub anchor actor id")
        if self.author_association != "OWNER":
            raise ValueError("GitHub anchor comment must be authored by the repository owner")
        if (
            type(self.comment_node_id) is not str
            or _NODE_ID_RE.fullmatch(self.comment_node_id) is None
        ):
            raise ValueError("GitHub anchor comment node id is invalid")
        expected_api = (
            f"https://api.github.com/repos/{owner}/{repository}/issues/comments/{comment_id}"
        )
        expected_issue = f"https://api.github.com/repos/{owner}/{repository}/issues/{issue_number}"
        if self.api_url != expected_api or self.issue_url != expected_issue:
            raise ValueError("GitHub anchor URLs differ from the exact repository comment")
        parsed_html = urlsplit(self.html_url)
        expected_prefix = f"/{owner}/{repository}/"
        if (
            parsed_html.scheme != "https"
            or parsed_html.hostname != "github.com"
            or parsed_html.username is not None
            or parsed_html.password is not None
            or parsed_html.query
            or not parsed_html.path.startswith(expected_prefix)
            or parsed_html.fragment != f"issuecomment-{comment_id}"
        ):
            raise ValueError("GitHub anchor HTML URL is not canonical")
        created = _strict_utc(self.created_at, "GitHub anchor creation time")
        updated = _strict_utc(self.updated_at, "GitHub anchor update time")
        scheduled = _strict_utc(
            self.beacon_round_scheduled_utc,
            "GitHub anchor beacon schedule",
        )
        if created != updated:
            raise ValueError("GitHub anchor comment was edited after creation")
        expected_lead = int((scheduled - created).total_seconds())
        if (
            type(self.lead_seconds) is not int
            or self.lead_seconds != expected_lead
            or self.lead_seconds < GITHUB_PRECOMMIT_MINIMUM_LEAD_SECONDS_V4
        ):
            raise ValueError("GitHub anchor did not precede the future beacon by enough time")
        for value, label in (
            (self.anchor_body_sha256, "GitHub anchor body"),
            (self.precommitment_record_sha256, "GitHub anchor precommitment record"),
            (self.precommitment_content_digest, "GitHub anchor precommitment content"),
            (self.beacon_round_spec_digest, "GitHub anchor beacon specification"),
            (self.content_digest, "GitHub anchor evidence content"),
        ):
            _digest(value, label)
        _positive_int(self.beacon_round_number, "GitHub anchor beacon round")
        if (
            type(self.implementation_revision) is not str
            or _GIT_OID_RE.fullmatch(self.implementation_revision) is None
        ):
            raise ValueError("GitHub anchor implementation revision is invalid")
        expected = _sha256(
            (_EVIDENCE_DOMAIN_V4 + "\x00" + canonical_json(self._payload())).encode("utf-8")
        )
        if not hmac.compare_digest(self.content_digest, expected):
            raise ValueError("GitHub precommit anchor evidence digest is stale")

    def to_dict(self) -> dict[str, object]:
        self.validate_current()
        return self._payload() | {"content_digest": self.content_digest}


def parse_github_precommit_anchor_response_v4(
    response_bytes: bytes,
    precommitment_bytes: bytes,
    *,
    expected_precommitment_record_sha256: str,
    repository_owner: str,
    repository_name: str,
    issue_number: int,
    comment_id: int,
    expected_actor_login: str,
    expected_actor_id: int,
) -> GitHubPrecommitAnchorEvidenceV4:
    """Normalize cached API bytes for inspection; this alone is not live authority."""

    owner = _identifier(repository_owner, "GitHub repository owner")
    repository = _identifier(repository_name, "GitHub repository name")
    issue = _positive_int(issue_number, "GitHub issue number")
    expected_comment = _positive_int(comment_id, "GitHub comment id")
    actor_login = _identifier(expected_actor_login, "GitHub anchor actor login")
    actor_id = _positive_int(expected_actor_id, "GitHub anchor actor id")
    precommitment = _precommitment_from_raw(
        precommitment_bytes,
        expected_precommitment_record_sha256,
    )
    expected_body = render_github_precommit_anchor_body_v4(
        precommitment_bytes,
        expected_precommitment_record_sha256=expected_precommitment_record_sha256,
    )
    parsed = _strict_json(response_bytes)
    user = parsed.get("user")
    if type(user) is not dict:
        raise ValueError("GitHub anchor response lacks an exact user object")
    observed_comment = parsed.get("id")
    observed_actor_id = user.get("id")
    if (
        type(observed_comment) is not int
        or observed_comment != expected_comment
        or type(observed_actor_id) is not int
        or observed_actor_id != actor_id
        or user.get("login") != actor_login
        or user.get("type") != "User"
        or parsed.get("author_association") != "OWNER"
        or parsed.get("body") != expected_body
    ):
        raise ValueError("GitHub anchor comment identity or body differs")
    node_id = parsed.get("node_id")
    api_url = parsed.get("url")
    issue_url = parsed.get("issue_url")
    html_url = parsed.get("html_url")
    created_at = parsed.get("created_at")
    updated_at = parsed.get("updated_at")
    if not all(
        type(value) is str
        for value in (node_id, api_url, issue_url, html_url, created_at, updated_at)
    ):
        raise ValueError("GitHub anchor response fields have inexact types")
    spec = precommitment.beacon_round_spec
    created = _strict_utc(created_at, "GitHub anchor creation time")
    scheduled = _strict_utc(spec.scheduled_utc, "GitHub anchor beacon schedule")
    lead_seconds = int((scheduled - created).total_seconds())
    pending = "sha256:" + "0" * 64
    draft = GitHubPrecommitAnchorEvidenceV4(
        repository_owner=owner,
        repository_name=repository,
        issue_number=issue,
        comment_id=expected_comment,
        comment_node_id=node_id,
        actor_login=actor_login,
        actor_id=actor_id,
        author_association="OWNER",
        api_url=api_url,
        issue_url=issue_url,
        html_url=html_url,
        created_at=created_at,
        updated_at=updated_at,
        lead_seconds=lead_seconds,
        anchor_body_sha256=_sha256(expected_body.encode("ascii")),
        precommitment_record_sha256=precommitment.record_sha256,
        precommitment_content_digest=precommitment.content_digest,
        beacon_round_spec_digest=spec.content_digest,
        beacon_round_number=spec.round_number,
        beacon_round_scheduled_utc=spec.scheduled_utc,
        implementation_revision=(precommitment.selector_code_manifest.protocol_git_commit),
        content_digest=pending,
    )
    digest = _sha256(
        (_EVIDENCE_DOMAIN_V4 + "\x00" + canonical_json(draft._payload())).encode("utf-8")
    )
    result = replace(draft, content_digest=digest)
    result.validate_current()
    return result


def _fetch_github_comment_json_v4(
    repository_owner: str,
    repository_name: str,
    comment_id: int,
    *,
    timeout_seconds: int,
) -> bytes:
    owner = _identifier(repository_owner, "GitHub repository owner")
    repository = _identifier(repository_name, "GitHub repository name")
    comment = _positive_int(comment_id, "GitHub comment id")
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 60:
        raise ValueError("GitHub anchor timeout must be an exact bounded integer")
    url = f"https://api.github.com/repos/{owner}/{repository}/issues/comments/{comment}"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "math-research-brain-precommit-anchor-v4",
            "X-GitHub-Api-Version": GITHUB_REST_API_VERSION_V4,
        },
        method="GET",
    )
    context = ssl.create_default_context()
    with urllib.request.urlopen(
        request,
        timeout=timeout_seconds,
        context=context,
    ) as response:
        if response.status != 200 or response.geturl() != url:
            raise ValueError("GitHub anchor live read returned an unexpected response")
        content_type = response.headers.get_content_type()
        if content_type not in {"application/json", "application/vnd.github+json"}:
            raise ValueError("GitHub anchor live read returned an unexpected content type")
        raw = response.read(_MAX_RESPONSE_BYTES_V4 + 1)
    if len(raw) > _MAX_RESPONSE_BYTES_V4:
        raise ValueError("GitHub anchor live response exceeds its byte bound")
    return raw


def fetch_github_precommit_anchor_v4(
    precommitment_bytes: bytes,
    *,
    expected_precommitment_record_sha256: str,
    repository_owner: str,
    repository_name: str,
    issue_number: int,
    comment_id: int,
    expected_actor_login: str,
    expected_actor_id: int,
    timeout_seconds: int = 20,
) -> GitHubPrecommitAnchorEvidenceV4:
    """Fetch the exact public comment over live TLS and normalize its server time."""

    response = _fetch_github_comment_json_v4(
        repository_owner,
        repository_name,
        comment_id,
        timeout_seconds=timeout_seconds,
    )
    return parse_github_precommit_anchor_response_v4(
        response,
        precommitment_bytes,
        expected_precommitment_record_sha256=expected_precommitment_record_sha256,
        repository_owner=repository_owner,
        repository_name=repository_name,
        issue_number=issue_number,
        comment_id=comment_id,
        expected_actor_login=expected_actor_login,
        expected_actor_id=expected_actor_id,
    )


def verify_live_github_precommit_anchor_v4(
    precommitment_bytes: bytes,
    *,
    expected_precommitment_record_sha256: str,
    expected_anchor_evidence_digest: str,
    repository_owner: str,
    repository_name: str,
    issue_number: int,
    comment_id: int,
    expected_actor_login: str,
    expected_actor_id: int,
    timeout_seconds: int = 20,
) -> GitHubPrecommitAnchorEvidenceV4:
    """Refetch the public comment and compare it to the independently expected digest."""

    expected = _digest(
        expected_anchor_evidence_digest,
        "expected GitHub precommit anchor evidence",
    )
    fresh = fetch_github_precommit_anchor_v4(
        precommitment_bytes,
        expected_precommitment_record_sha256=expected_precommitment_record_sha256,
        repository_owner=repository_owner,
        repository_name=repository_name,
        issue_number=issue_number,
        comment_id=comment_id,
        expected_actor_login=expected_actor_login,
        expected_actor_id=expected_actor_id,
        timeout_seconds=timeout_seconds,
    )
    if not hmac.compare_digest(fresh.content_digest, expected):
        raise ValueError("live GitHub precommit anchor differs from the sealed evidence digest")
    return fresh


__all__ = [
    "GITHUB_PRECOMMIT_ANCHOR_EVIDENCE_SCHEMA_V4",
    "GITHUB_PRECOMMIT_ANCHOR_SCHEMA_V4",
    "GITHUB_PRECOMMIT_MINIMUM_LEAD_SECONDS_V4",
    "GITHUB_REST_API_VERSION_V4",
    "GitHubPrecommitAnchorEvidenceV4",
    "fetch_github_precommit_anchor_v4",
    "parse_github_precommit_anchor_response_v4",
    "render_github_precommit_anchor_body_v4",
    "verify_live_github_precommit_anchor_v4",
]
