"""Unique live GitHub acknowledgement for one v4 execution ledger.

Creating a local ledger is not authorization to use it.  Before any solver reaches
``STARTED``, one immutable issue comment must name the exact prepared execution,
ledger digest, and bound public record.  Every execution and closure consumer refetches
the target comment and the issue comment list; zero, edited, or multiple acknowledgement
comments fail closed.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime

from .models import canonical_json

RANDOM_EXTERNAL_EXECUTION_ACK_SCHEMA_V4 = (
    "org.math-research-brain/random-external-execution-acknowledgement/v4"
)
RANDOM_EXTERNAL_EXECUTION_ACK_EVIDENCE_SCHEMA_V4 = (
    "org.math-research-brain/random-external-execution-ack-evidence/v4"
)
RANDOM_EXTERNAL_EXECUTION_ACK_PREFIX_V4 = "random-external-execution-ack-v4 "

_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z", re.ASCII)
_IDENTIFIER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}\Z", re.ASCII)
_NODE_ID_RE = re.compile(r"[A-Za-z0-9_=-]{4,256}\Z", re.ASCII)
_UTC_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z")
_MAX_RESPONSE_BYTES = 2_000_000
_EVIDENCE_DOMAIN = b"math-brain/random-external-execution-ack-evidence-v4\x00"


def _sha256(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _digest_json(value: object) -> str:
    return _sha256(_EVIDENCE_DOMAIN + canonical_json(value).encode("utf-8"))


def _digest(value: object, label: str) -> str:
    if type(value) is not str or _DIGEST_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be a canonical SHA-256 digest")
    return value


def _identifier(value: object, label: str) -> str:
    if type(value) is not str or _IDENTIFIER_RE.fullmatch(value) is None:
        raise ValueError(f"{label} is invalid")
    return value


def _positive_int(value: object, label: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{label} must be a positive exact integer")
    return value


def _strict_utc(value: object, label: str) -> str:
    if type(value) is not str or _UTC_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be whole-second UTC text")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError as error:
        raise ValueError(f"{label} is not a real UTC timestamp") from error
    if parsed.year < 2020:
        raise ValueError(f"{label} predates the permitted GitHub chronology")
    return value


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("GitHub execution acknowledgement contains a duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"GitHub execution acknowledgement contains forbidden constant {value}")


def _strict_json(raw: bytes, *, root_type: type[object]) -> object:
    if type(raw) is not bytes or not 0 < len(raw) <= _MAX_RESPONSE_BYTES:
        raise ValueError("GitHub execution acknowledgement response is not bounded bytes")
    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError("GitHub execution acknowledgement is not strict JSON") from error
    if not isinstance(value, root_type) or type(value) is not root_type:
        raise ValueError("GitHub execution acknowledgement has the wrong JSON root")
    return value


def _ack_record(
    *,
    execution_authorization_digest: str,
    execution_precommitment_digest: str,
    ledger_digest: str,
    execution_public_record_digest: str,
    plan_digest: str,
    task_map_public_precommitment_digest: str,
    execution_policy_digest: str,
    implementation_manifest_digest: str,
) -> dict[str, object]:
    return {
        "execution_authorization_digest": _digest(
            execution_authorization_digest,
            "execution acknowledgement authorization",
        ),
        "execution_policy_digest": _digest(
            execution_policy_digest, "execution acknowledgement policy"
        ),
        "execution_precommitment_digest": _digest(
            execution_precommitment_digest,
            "execution acknowledgement precommitment",
        ),
        "execution_public_record_digest": _digest(
            execution_public_record_digest,
            "execution acknowledgement public record",
        ),
        "implementation_manifest_digest": _digest(
            implementation_manifest_digest,
            "execution acknowledgement implementation manifest",
        ),
        "ledger_digest": _digest(ledger_digest, "execution acknowledgement ledger"),
        "plan_digest": _digest(plan_digest, "execution acknowledgement plan"),
        "schema": RANDOM_EXTERNAL_EXECUTION_ACK_SCHEMA_V4,
        "schema_version": 4,
        "task_map_public_precommitment_digest": _digest(
            task_map_public_precommitment_digest,
            "execution acknowledgement task map",
        ),
    }


def render_github_execution_ack_body_v4(
    *,
    execution_authorization_digest: str,
    execution_precommitment_digest: str,
    ledger_digest: str,
    execution_public_record_digest: str,
    plan_digest: str,
    task_map_public_precommitment_digest: str,
    execution_policy_digest: str,
    implementation_manifest_digest: str,
) -> str:
    record = _ack_record(
        execution_authorization_digest=execution_authorization_digest,
        execution_precommitment_digest=execution_precommitment_digest,
        ledger_digest=ledger_digest,
        execution_public_record_digest=execution_public_record_digest,
        plan_digest=plan_digest,
        task_map_public_precommitment_digest=task_map_public_precommitment_digest,
        execution_policy_digest=execution_policy_digest,
        implementation_manifest_digest=implementation_manifest_digest,
    )
    body = RANDOM_EXTERNAL_EXECUTION_ACK_PREFIX_V4 + canonical_json(record)
    if "\n" in body or len(body.encode("utf-8")) > 4_096:
        raise ValueError("GitHub execution acknowledgement body exceeds its frozen bound")
    return body


@dataclass(frozen=True, slots=True)
class GitHubExecutionAcknowledgementV4:
    repository_owner: str
    repository_name: str
    issue_number: int
    comment_id: int
    comment_node_id: str
    actor_login: str
    actor_id: int
    created_at: str
    updated_at: str
    body_sha256: str
    execution_authorization_digest: str
    execution_precommitment_digest: str
    ledger_digest: str
    execution_public_record_digest: str
    plan_digest: str
    task_map_public_precommitment_digest: str
    execution_policy_digest: str
    implementation_manifest_digest: str
    content_digest: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self) is not GitHubExecutionAcknowledgementV4:
            raise ValueError("GitHub execution-acknowledgement subclasses are forbidden")
        _identifier(self.repository_owner, "GitHub repository owner")
        _identifier(self.repository_name, "GitHub repository name")
        _positive_int(self.issue_number, "GitHub issue number")
        _positive_int(self.comment_id, "GitHub comment id")
        if (
            type(self.comment_node_id) is not str
            or _NODE_ID_RE.fullmatch(self.comment_node_id) is None
        ):
            raise ValueError("GitHub comment node id is invalid")
        _identifier(self.actor_login, "GitHub acknowledgement actor")
        _positive_int(self.actor_id, "GitHub acknowledgement actor id")
        if self.actor_login.casefold() != self.repository_owner.casefold():
            raise ValueError("GitHub acknowledgement actor must own the repository")
        created = _strict_utc(self.created_at, "GitHub acknowledgement creation time")
        updated = _strict_utc(self.updated_at, "GitHub acknowledgement update time")
        if created != updated:
            raise ValueError("GitHub execution acknowledgement was edited after creation")
        for value, label in (
            (self.body_sha256, "GitHub acknowledgement body"),
            (self.execution_authorization_digest, "execution authorization"),
            (self.execution_precommitment_digest, "execution precommitment"),
            (self.ledger_digest, "execution ledger"),
            (self.execution_public_record_digest, "execution public record"),
            (self.plan_digest, "execution plan"),
            (self.task_map_public_precommitment_digest, "execution task map"),
            (self.execution_policy_digest, "execution policy"),
            (self.implementation_manifest_digest, "execution implementation manifest"),
        ):
            _digest(value, label)
        object.__setattr__(self, "content_digest", _digest_json(self._payload()))

    def _payload(self) -> dict[str, object]:
        return {
            "actor_id": self.actor_id,
            "actor_login": self.actor_login,
            "body_sha256": self.body_sha256,
            "comment_id": self.comment_id,
            "comment_node_id": self.comment_node_id,
            "created_at": self.created_at,
            "execution_authorization_digest": self.execution_authorization_digest,
            "execution_policy_digest": self.execution_policy_digest,
            "execution_precommitment_digest": self.execution_precommitment_digest,
            "execution_public_record_digest": self.execution_public_record_digest,
            "implementation_manifest_digest": self.implementation_manifest_digest,
            "issue_number": self.issue_number,
            "ledger_digest": self.ledger_digest,
            "plan_digest": self.plan_digest,
            "repository_name": self.repository_name,
            "repository_owner": self.repository_owner,
            "schema": RANDOM_EXTERNAL_EXECUTION_ACK_EVIDENCE_SCHEMA_V4,
            "schema_version": 4,
            "task_map_public_precommitment_digest": (self.task_map_public_precommitment_digest),
            "updated_at": self.updated_at,
        }

    def validate_current(self) -> None:
        self.__post_init__()
        expected_body = render_github_execution_ack_body_v4(
            execution_authorization_digest=self.execution_authorization_digest,
            execution_precommitment_digest=self.execution_precommitment_digest,
            ledger_digest=self.ledger_digest,
            execution_public_record_digest=self.execution_public_record_digest,
            plan_digest=self.plan_digest,
            task_map_public_precommitment_digest=self.task_map_public_precommitment_digest,
            execution_policy_digest=self.execution_policy_digest,
            implementation_manifest_digest=self.implementation_manifest_digest,
        )
        if not hmac.compare_digest(
            self.body_sha256,
            _sha256(expected_body.encode("utf-8")),
        ):
            raise ValueError("GitHub acknowledgement body digest is stale")
        if not hmac.compare_digest(self.content_digest, _digest_json(self._payload())):
            raise ValueError("GitHub acknowledgement evidence digest is stale")

    def commitment_record(self) -> dict[str, object]:
        self.validate_current()
        return self._payload() | {"content_digest": self.content_digest}


def _comment_url(owner: str, repository: str, comment_id: int) -> str:
    return f"https://api.github.com/repos/{owner}/{repository}/issues/comments/{comment_id}"


def _issue_comments_url(owner: str, repository: str, issue_number: int) -> str:
    return (
        f"https://api.github.com/repos/{owner}/{repository}/issues/"
        f"{issue_number}/comments?per_page=100&page=1"
    )


def _fetch_github_json_bytes_v4(url: str, *, timeout_seconds: int = 30) -> bytes:
    if type(url) is not str or not url.startswith("https://api.github.com/repos/"):
        raise ValueError("GitHub execution acknowledgement URL is invalid")
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 60:
        raise ValueError("GitHub execution acknowledgement timeout is invalid")
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "math-research-brain-execution-ack-v4",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            if response.status != 200:
                raise ValueError("GitHub execution acknowledgement returned non-200 status")
            content_type = response.headers.get_content_type()
            if content_type != "application/json":
                raise ValueError("GitHub execution acknowledgement returned non-JSON content")
            raw = response.read(_MAX_RESPONSE_BYTES + 1)
    except (OSError, urllib.error.URLError) as error:
        raise ValueError("GitHub execution acknowledgement live read failed") from error
    if len(raw) > _MAX_RESPONSE_BYTES:
        raise ValueError("GitHub execution acknowledgement live response is too large")
    return raw


def _parse_target_comment(
    raw: bytes,
    *,
    repository_owner: str,
    repository_name: str,
    issue_number: int,
    comment_id: int,
    actor_login: str,
    actor_id: int,
    expected_body: str,
) -> GitHubExecutionAcknowledgementV4:
    value = _strict_json(raw, root_type=dict)
    assert type(value) is dict
    user = value.get("user")
    if type(user) is not dict:
        raise ValueError("GitHub execution acknowledgement lacks an exact actor")
    observed = {
        "actor_id": user.get("id"),
        "actor_login": user.get("login"),
        "body": value.get("body"),
        "comment_id": value.get("id"),
        "comment_node_id": value.get("node_id"),
        "created_at": value.get("created_at"),
        "issue_url": value.get("issue_url"),
        "updated_at": value.get("updated_at"),
    }
    expected_issue_url = (
        f"https://api.github.com/repos/{repository_owner}/{repository_name}/issues/{issue_number}"
    )
    if (
        observed["actor_id"] != actor_id
        or observed["actor_login"] != actor_login
        or observed["body"] != expected_body
        or observed["comment_id"] != comment_id
        or observed["issue_url"] != expected_issue_url
    ):
        raise ValueError("GitHub execution acknowledgement identity or body differs")
    record = json.loads(expected_body.removeprefix(RANDOM_EXTERNAL_EXECUTION_ACK_PREFIX_V4))
    return GitHubExecutionAcknowledgementV4(
        repository_owner=repository_owner,
        repository_name=repository_name,
        issue_number=issue_number,
        comment_id=comment_id,
        comment_node_id=observed["comment_node_id"],
        actor_login=actor_login,
        actor_id=actor_id,
        created_at=observed["created_at"],
        updated_at=observed["updated_at"],
        body_sha256=_sha256(expected_body.encode("utf-8")),
        execution_authorization_digest=record["execution_authorization_digest"],
        execution_precommitment_digest=record["execution_precommitment_digest"],
        ledger_digest=record["ledger_digest"],
        execution_public_record_digest=record["execution_public_record_digest"],
        plan_digest=record["plan_digest"],
        task_map_public_precommitment_digest=record["task_map_public_precommitment_digest"],
        execution_policy_digest=record["execution_policy_digest"],
        implementation_manifest_digest=record["implementation_manifest_digest"],
    )


def fetch_github_execution_ack_v4(
    *,
    execution_authorization_digest: str,
    execution_precommitment_digest: str,
    ledger_digest: str,
    execution_public_record_digest: str,
    plan_digest: str,
    task_map_public_precommitment_digest: str,
    execution_policy_digest: str,
    implementation_manifest_digest: str,
    repository_owner: str,
    repository_name: str,
    issue_number: int,
    comment_id: int,
    actor_login: str,
    actor_id: int,
) -> GitHubExecutionAcknowledgementV4:
    """Fetch one exact comment and prove it is the issue's only v4 ack."""

    owner = _identifier(repository_owner, "GitHub repository owner")
    repository = _identifier(repository_name, "GitHub repository name")
    issue = _positive_int(issue_number, "GitHub issue number")
    comment = _positive_int(comment_id, "GitHub comment id")
    actor = _identifier(actor_login, "GitHub acknowledgement actor")
    actor_number = _positive_int(actor_id, "GitHub acknowledgement actor id")
    body = render_github_execution_ack_body_v4(
        execution_authorization_digest=execution_authorization_digest,
        execution_precommitment_digest=execution_precommitment_digest,
        ledger_digest=ledger_digest,
        execution_public_record_digest=execution_public_record_digest,
        plan_digest=plan_digest,
        task_map_public_precommitment_digest=task_map_public_precommitment_digest,
        execution_policy_digest=execution_policy_digest,
        implementation_manifest_digest=implementation_manifest_digest,
    )
    target = _parse_target_comment(
        _fetch_github_json_bytes_v4(_comment_url(owner, repository, comment)),
        repository_owner=owner,
        repository_name=repository,
        issue_number=issue,
        comment_id=comment,
        actor_login=actor,
        actor_id=actor_number,
        expected_body=body,
    )
    issue_comments = _strict_json(
        _fetch_github_json_bytes_v4(_issue_comments_url(owner, repository, issue)),
        root_type=list,
    )
    assert type(issue_comments) is list
    if len(issue_comments) >= 100:
        raise ValueError("GitHub issue has too many comments to prove ack uniqueness")
    candidates = tuple(
        item
        for item in issue_comments
        if type(item) is dict
        and type(item.get("body")) is str
        and item["body"].startswith(RANDOM_EXTERNAL_EXECUTION_ACK_PREFIX_V4)
    )
    if len(candidates) != 1 or candidates[0].get("id") != comment:
        raise ValueError("GitHub issue must contain exactly one execution acknowledgement")
    target.validate_current()
    return target


def verify_live_github_execution_ack_v4(
    *,
    execution_authorization_digest: str,
    execution_precommitment_digest: str,
    ledger_digest: str,
    execution_public_record_digest: str,
    plan_digest: str,
    task_map_public_precommitment_digest: str,
    execution_policy_digest: str,
    implementation_manifest_digest: str,
    expected_ack_evidence_digest: str,
    repository_owner: str,
    repository_name: str,
    issue_number: int,
    comment_id: int,
    actor_login: str,
    actor_id: int,
) -> GitHubExecutionAcknowledgementV4:
    """Live-refetch the unique acknowledgement and match sealed evidence."""

    expected_digest = _digest(expected_ack_evidence_digest, "expected GitHub ack evidence")
    target = fetch_github_execution_ack_v4(
        execution_authorization_digest=execution_authorization_digest,
        execution_precommitment_digest=execution_precommitment_digest,
        ledger_digest=ledger_digest,
        execution_public_record_digest=execution_public_record_digest,
        plan_digest=plan_digest,
        task_map_public_precommitment_digest=task_map_public_precommitment_digest,
        execution_policy_digest=execution_policy_digest,
        implementation_manifest_digest=implementation_manifest_digest,
        repository_owner=repository_owner,
        repository_name=repository_name,
        issue_number=issue_number,
        comment_id=comment_id,
        actor_login=actor_login,
        actor_id=actor_id,
    )
    if not hmac.compare_digest(target.content_digest, expected_digest):
        raise ValueError("live GitHub execution acknowledgement differs from sealed evidence")
    return target


__all__ = [
    "RANDOM_EXTERNAL_EXECUTION_ACK_EVIDENCE_SCHEMA_V4",
    "RANDOM_EXTERNAL_EXECUTION_ACK_PREFIX_V4",
    "RANDOM_EXTERNAL_EXECUTION_ACK_SCHEMA_V4",
    "GitHubExecutionAcknowledgementV4",
    "fetch_github_execution_ack_v4",
    "render_github_execution_ack_body_v4",
    "verify_live_github_execution_ack_v4",
]
