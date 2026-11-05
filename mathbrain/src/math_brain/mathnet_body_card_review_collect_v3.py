"""Phase-separated collector for the frozen dual body/card review.

``prepare`` creates private recovery state and durably emits the exact public
precommitment while both attempt slots are pending. ``anchor`` publishes that
digest as an annotated remote Git tag and records a live-verifiable receipt.
Only a later ``execute`` invocation may consume the externally supplied digest,
and ``verify`` cold-checks the private signed pair in another process without
authentication, signing keys, ledger state, or raw Codex run artifacts.

The frozen projection, event-family bindings, signed pair, and exact selection
remain private.  The command output contains counts and commitments only.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import secrets
import stat
import subprocess
import sys
from collections.abc import Mapping, Sequence
from contextlib import suppress
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from . import mathnet_body_card_review_execution_v3 as execution
from .attempt_ledger import AttemptLedger, SlotState
from .codex_runner import capture_codex_executable_provenance
from .mathnet_body_card_review_signed_pair_v3 import (
    serialize_signed_body_card_review_pair_v3,
    verify_and_select_signed_body_card_review_pair_bytes_v3,
)
from .mathnet_body_card_review_v3 import (
    BODY_CARD_EVENT_BINDINGS_SCHEMA_V3,
    BODY_CARD_FROZEN_CARD_COUNT_V3,
    BODY_CARD_FROZEN_ITEM_COUNT_V3,
    BODY_CARD_REVIEW_VERSION_V3,
    BodyCardReviewRunPolicyV3,
    FrozenBodyCardEventFamilyBindingsV3,
    FrozenBodyCardProjectionV3,
    body_card_review_public_precommitment_digest_v3,
    load_body_card_event_family_bindings_v3,
    load_frozen_body_card_projection_v3,
    parse_body_card_review_public_precommitment_v3,
)
from .models import canonical_json

BODY_CARD_EVALUATOR_PRIVATE_RAW_DIGEST_V3 = (
    "sha256:6a7b02451cfa0120b11855c979811a91cd65d7aa064d595dcb26573b332d3278"
)
BODY_CARD_COLLECTION_ANCHOR_SCHEMA_V3 = (
    "org.math-research-brain/engineering-pilot-body-card-remote-anchor"
)
BODY_CARD_COLLECTION_ANCHOR_ACK_SCHEMA_V3 = (
    "org.math-research-brain/engineering-pilot-body-card-remote-anchor-ack"
)
BODY_CARD_COLLECTION_SUMMARY_SCHEMA_V3 = (
    "org.math-research-brain/engineering-pilot-body-card-collection-summary"
)
BODY_CARD_EVALUATOR_MAX_BYTES_V3 = 1_000_000
BODY_CARD_PRIVATE_OUTPUT_MAX_BYTES_V3 = 10_000_000

PROJECTION_FILE = "projection.json"
EVALUATOR_FILE = "evaluator-private.json"
EVENT_BINDINGS_FILE = "event-family-bindings.json"
PRIVATE_STATE_FILE = "private-state.json"
LEDGER_DIRECTORY = "attempt-ledger"
PUBLICATION_RECEIPT_FILE = "publication-receipt.json"
PUBLIC_PRECOMMITMENT_FILE = "public-precommitment.json"
RUNTIME_DIRECTORY = "runtime"
ANCHOR_ACK_FILE = "external-anchor-ack.json"
SIGNED_PAIR_FILE = "signed-review-pair.json"
SELECTION_REPORT_FILE = "selection-report-private.json"
PREPARE_SUMMARY_FILE = "prepare-summary.json"

_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_GIT_OID_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_SAFE_REMOTE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_TAG_RE = re.compile(r"body-card-review-v3-[0-9a-f]{12}\Z")
_ANCHOR_TAGGER_RE = re.compile(
    r"tagger Math Research Brain Anchor <anchor@math-research-brain\.invalid> "
    r"[1-9][0-9]{8,} \+0000\Z"
)


def _sha256(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _ascii_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("private collection JSON contains a duplicate key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON value is forbidden: {value}")


def _canonical_json_lf(raw: bytes, *, label: str, maximum: int) -> object:
    if type(raw) is not bytes or not raw.endswith(b"\n") or not 1 < len(raw) <= maximum:
        raise ValueError(f"{label} framing or byte length is invalid")
    try:
        value = json.loads(
            raw[:-1].decode("utf-8", errors="strict"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise ValueError(f"{label} is not strict JSON") from exc
    if canonical_json(value).encode("utf-8") + b"\n" != raw:
        raise ValueError(f"{label} is not canonical JSON with one LF")
    return value


def _exact_mapping(value: object, keys: set[str], label: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != keys:
        raise ValueError(f"{label} fields differ from the frozen schema")
    return value


def _require_digest(value: object, label: str) -> str:
    if type(value) is not str or _DIGEST_RE.fullmatch(value) is None:
        raise ValueError(f"{label} is not a canonical SHA-256 digest")
    return value


def _require_git_oid(value: object, label: str) -> str:
    if type(value) is not str or _GIT_OID_RE.fullmatch(value) is None:
        raise ValueError(f"{label} is not a full lowercase Git object id")
    return value


def _private_root(path: str | Path, *, must_exist: bool) -> Path:
    return execution._private_root_path(path, must_exist=must_exist)


def _private_file(root: Path, name: str, *, must_exist: bool) -> Path:
    if type(name) is not str or not name or Path(name).name != name:
        raise ValueError("private collection filename is invalid")
    candidate = root / name
    if must_exist:
        try:
            status = candidate.lstat()
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise ValueError(f"required private collection file is missing: {name}") from exc
        if (
            not candidate.is_symlink()
            and stat.S_ISREG(status.st_mode)
            and resolved == candidate
            and status.st_nlink > 1
        ):
            staging_name = re.compile(rf"\.{re.escape(name)}\.[0-9a-f]{{32}}\.tmp\Z")
            linked_staging: list[Path] = []
            for entry in root.iterdir():
                if staging_name.fullmatch(entry.name) is None:
                    continue
                try:
                    entry_status = entry.lstat()
                except OSError as exc:
                    raise ValueError("private staging link cannot be inspected") from exc
                if (
                    not entry.is_symlink()
                    and stat.S_ISREG(entry_status.st_mode)
                    and (entry_status.st_dev, entry_status.st_ino)
                    == (status.st_dev, status.st_ino)
                ):
                    linked_staging.append(entry)
            if len(linked_staging) != status.st_nlink - 1:
                raise ValueError(f"private collection file has unknown hard links: {name}")
            for entry in linked_staging:
                entry.unlink()
            execution._fsync_directory(root)
            status = candidate.lstat()
        if (
            candidate.is_symlink()
            or not stat.S_ISREG(status.st_mode)
            or resolved != candidate
            or status.st_nlink != 1
        ):
            raise ValueError(f"private collection file is unsafe: {name}")
        return resolved
    if candidate.exists() or candidate.is_symlink():
        raise FileExistsError(f"private collection file already exists: {name}")
    return candidate


def _write_private_once(path: Path, raw: bytes, *, maximum: int) -> Path:
    if type(raw) is not bytes or not 0 < len(raw) <= maximum:
        raise ValueError("private collection output exceeds its byte budget")
    if path.exists() or path.is_symlink():
        existing = _private_file(path.parent, path.name, must_exist=True).read_bytes()
        if not hmac.compare_digest(existing, raw):
            raise FileExistsError(f"private collection output already differs: {path.name}")
        return path
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(16)}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    for optional in ("O_BINARY", "O_NOINHERIT", "O_NOFOLLOW"):
        flags |= int(getattr(os, optional, 0))
    try:
        descriptor = os.open(temporary, flags, 0o600)
        try:
            written = 0
            while written < len(raw):
                count = os.write(descriptor, raw[written:])
                if count <= 0:  # pragma: no cover
                    raise OSError("private collection write made no progress")
                written += count
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        try:
            os.link(temporary, path)
        except FileExistsError:
            existing = _private_file(path.parent, path.name, must_exist=True).read_bytes()
            if not hmac.compare_digest(existing, raw):
                raise FileExistsError(
                    f"private collection output already differs: {path.name}"
                ) from None
        execution._fsync_directory(path.parent)
    finally:
        with suppress(FileNotFoundError):
            temporary.unlink()
        execution._fsync_directory(path.parent)
    return path


def _read_regular_source(path: str | Path, *, label: str, maximum: int) -> bytes:
    raw_path = Path(path)
    if not raw_path.is_absolute():
        raise ValueError(f"{label} path must be absolute")
    try:
        status = raw_path.lstat()
        resolved = raw_path.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"{label} cannot be inspected") from exc
    if raw_path.is_symlink() or not stat.S_ISREG(status.st_mode) or resolved != raw_path:
        raise ValueError(f"{label} must be a regular non-symlink file")
    if not 0 < status.st_size <= maximum:
        raise ValueError(f"{label} byte length is invalid")
    try:
        raw = resolved.read_bytes()
    except OSError as exc:
        raise ValueError(f"{label} cannot be read") from exc
    if len(raw) != status.st_size:
        raise ValueError(f"{label} changed while it was read")
    return raw


def _load_evaluator_and_build_bindings(
    raw: bytes,
    projection: FrozenBodyCardProjectionV3,
) -> bytes:
    if not hmac.compare_digest(_sha256(raw), BODY_CARD_EVALUATOR_PRIVATE_RAW_DIGEST_V3):
        raise ValueError("private evaluator differs from the frozen SHA-256")
    root = _exact_mapping(
        _canonical_json_lf(
            raw,
            label="private evaluator",
            maximum=BODY_CARD_EVALUATOR_MAX_BYTES_V3,
        ),
        {
            "card_map",
            "items",
            "projection_content_digest",
            "source_manifest_digest",
            "universe_content_digest",
        },
        "private evaluator",
    )
    if root["projection_content_digest"] != projection.content_digest:
        raise ValueError("private evaluator projection digest differs")
    _require_digest(root["source_manifest_digest"], "private evaluator source manifest")
    _require_digest(root["universe_content_digest"], "private evaluator universe")
    raw_cards = root["card_map"]
    raw_items = root["items"]
    if (
        type(raw_cards) is not list
        or len(raw_cards) != BODY_CARD_FROZEN_CARD_COUNT_V3
        or type(raw_items) is not list
        or len(raw_items) != BODY_CARD_FROZEN_ITEM_COUNT_V3
    ):
        raise ValueError("private evaluator frozen universe is incomplete")
    card_ids: list[str] = []
    for index, raw_card in enumerate(raw_cards):
        card = _exact_mapping(
            raw_card,
            {"card_id", "family", "record_id", "semantic_digest"},
            f"private evaluator card {index}",
        )
        if any(
            type(card[key]) is not str or not card[key]
            for key in ("card_id", "family", "record_id")
        ):
            raise ValueError("private evaluator card metadata is invalid")
        _require_digest(card["semantic_digest"], "private evaluator card semantic digest")
        card_ids.append(card["card_id"])
    if set(card_ids) != set(projection.card_by_id) or len(set(card_ids)) != len(card_ids):
        raise ValueError("private evaluator card universe differs from the projection")

    projection_items = projection.item_by_id
    bindings: list[dict[str, str]] = []
    source_ordinals: list[int] = []
    for index, raw_item in enumerate(raw_items):
        item = _exact_mapping(
            raw_item,
            {
                "event_family_digest",
                "gold",
                "problem_text_digest",
                "review_item_id",
                "selection_coordinate_digest",
                "source_ordinal",
            },
            f"private evaluator item {index}",
        )
        item_id = item["review_item_id"]
        if type(item_id) is not str or item_id not in projection_items:
            raise ValueError("private evaluator contains an unknown review item")
        event_family_digest = _require_digest(
            item["event_family_digest"],
            "private evaluator event family",
        )
        problem_digest = _require_digest(
            item["problem_text_digest"],
            "private evaluator problem text",
        )
        _require_digest(
            item["selection_coordinate_digest"],
            "private evaluator selection coordinate",
        )
        if problem_digest != _sha256(projection_items[item_id].problem_text.encode("utf-8")):
            raise ValueError("private evaluator problem digest is stale")
        if type(item["source_ordinal"]) is not int or item["source_ordinal"] < 0:
            raise ValueError("private evaluator source ordinal is invalid")
        source_ordinals.append(item["source_ordinal"])
        gold = _exact_mapping(
            item["gold"],
            {
                "answer",
                "confirmation_rule",
                "confirming_solution_indices",
                "content_digest",
                "final_answer_text_digest",
                "solution_text_digests",
            },
            "private evaluator gold record",
        )
        for key in ("content_digest", "final_answer_text_digest"):
            _require_digest(gold[key], f"private evaluator gold {key}")
        if type(gold["solution_text_digests"]) is not list or any(
            type(value) is not str or _DIGEST_RE.fullmatch(value) is None
            for value in gold["solution_text_digests"]
        ):
            raise ValueError("private evaluator solution digest vector is invalid")
        if type(gold["confirmation_rule"]) is not str or not gold["confirmation_rule"]:
            raise ValueError("private evaluator confirmation rule is invalid")
        if type(gold["confirming_solution_indices"]) is not list or any(
            type(value) is not int or value < 0 for value in gold["confirming_solution_indices"]
        ):
            raise ValueError("private evaluator confirming-solution vector is invalid")
        bindings.append(
            {
                "event_family_digest": event_family_digest,
                "problem_text_digest": problem_digest,
                "review_item_id": item_id,
            }
        )
    item_ids = [item["review_item_id"] for item in bindings]
    if (
        set(item_ids) != set(projection_items)
        or len(set(item_ids)) != len(item_ids)
        or len(set(source_ordinals)) != len(source_ordinals)
    ):
        raise ValueError("private evaluator item universe is duplicated or incomplete")
    value = {
        "frozen_projection_content_digest": projection.content_digest,
        "frozen_projection_raw_file_digest": projection.raw_file_digest,
        "items": sorted(bindings, key=lambda item: item["review_item_id"]),
        "schema": BODY_CARD_EVENT_BINDINGS_SCHEMA_V3,
        "schema_version": BODY_CARD_REVIEW_VERSION_V3,
    }
    return canonical_json(value).encode("utf-8") + b"\n"


def _load_projection(root: Path) -> tuple[bytes, FrozenBodyCardProjectionV3]:
    raw = _private_file(root, PROJECTION_FILE, must_exist=True).read_bytes()
    return raw, load_frozen_body_card_projection_v3(raw)


def _load_bindings(
    root: Path,
    projection: FrozenBodyCardProjectionV3,
) -> tuple[bytes, FrozenBodyCardEventFamilyBindingsV3]:
    raw = _private_file(root, EVENT_BINDINGS_FILE, must_exist=True).read_bytes()
    return raw, load_body_card_event_family_bindings_v3(raw, projection)


def _load_bound_collection(
    root: Path,
    *,
    expected_public_digest: str,
) -> tuple[
    FrozenBodyCardProjectionV3,
    FrozenBodyCardEventFamilyBindingsV3,
    execution.PreparedBodyCardReviewsV3,
    AttemptLedger,
    execution.BoundBodyCardReviewsV3,
    bytes,
]:
    _projection_raw, projection = _load_projection(root)
    _bindings_raw, bindings = _load_bindings(root, projection)
    prepared = execution.load_body_card_review_private_state_v3(
        _private_file(root, PRIVATE_STATE_FILE, must_exist=True),
        projection,
    )
    if prepared.event_family_bindings_digest != bindings.raw_file_digest:
        raise ValueError("durable preparation differs from event-family bindings")
    ledger = execution.open_body_card_review_ledger_v3(
        prepared,
        root / LEDGER_DIRECTORY,
    )
    public_bytes = _private_file(root, PUBLIC_PRECOMMITMENT_FILE, must_exist=True).read_bytes()
    bound = execution.restore_bound_body_card_review_ledger_v3(
        prepared,
        ledger,
        private_state_path=_private_file(root, PRIVATE_STATE_FILE, must_exist=True),
        public_precommitment_bytes=public_bytes,
        expected_public_precommitment_digest=expected_public_digest,
    )
    return projection, bindings, prepared, ledger, bound, public_bytes


def _git(
    repository: Path,
    *arguments: str,
    check: bool = True,
    timeout: int = 120,
    anchor_identity: bool = False,
) -> str | None:
    creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0
    environment = dict(os.environ)
    environment["GIT_TERMINAL_PROMPT"] = "0"
    if anchor_identity:
        for inherited in (
            "GIT_AUTHOR_DATE",
            "GIT_AUTHOR_EMAIL",
            "GIT_AUTHOR_NAME",
            "GIT_COMMITTER_DATE",
        ):
            environment.pop(inherited, None)
        environment.update(
            {
                "GIT_COMMITTER_EMAIL": "anchor@math-research-brain.invalid",
                "GIT_COMMITTER_NAME": "Math Research Brain Anchor",
                "TZ": "UTC0",
            }
        )
    result = subprocess.run(
        ["git", *arguments],
        cwd=repository,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        timeout=timeout,
        creationflags=creationflags,
    )
    if result.returncode != 0:
        if check:
            raise RuntimeError("Git anchor operation failed")
        return None
    return result.stdout.rstrip("\n")


def _tag_name(public_digest: str) -> str:
    digest = _require_digest(public_digest, "public precommitment digest")
    tag = "body-card-review-v3-" + digest.removeprefix("sha256:")[:12]
    if _TAG_RE.fullmatch(tag) is None:  # pragma: no cover - defensive
        raise ValueError("derived body/card anchor tag is invalid")
    return tag


def _remote_locator(repository: Path, remote: str) -> str:
    if type(remote) is not str or _SAFE_REMOTE_RE.fullmatch(remote) is None:
        raise ValueError("body/card anchor remote name is unsafe")
    output = _git(repository, "remote", "get-url", "--all", remote)
    assert output is not None
    locators = output.splitlines()
    if len(locators) != 1:
        raise ValueError("body/card anchor remote must have exactly one locator")
    locator = locators[0]
    if (
        not locator
        or len(locator) > 2_048
        or locator != locator.strip()
        or any(ord(character) < 0x20 or ord(character) > 0x7E for character in locator)
    ):
        raise ValueError("body/card anchor remote locator is unsafe")
    raw_path = Path(locator)
    if raw_path.is_absolute():
        try:
            resolved = raw_path.resolve(strict=True)
        except OSError as exc:
            raise ValueError("body/card anchor local remote cannot be resolved") from exc
        if not resolved.is_dir():
            raise ValueError("body/card anchor local remote is not a directory")
        canonical = str(resolved)
        if any(ord(character) < 0x20 or ord(character) > 0x7E for character in canonical):
            raise ValueError("body/card anchor local remote path is not ASCII")
        return canonical
    parsed = urlsplit(locator)
    if parsed.scheme:
        if (
            parsed.scheme.lower() != "https"
            or parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("body/card anchor remote URL is not canonical HTTPS")
        return locator
    raise ValueError("body/card anchor remote locator is neither HTTPS nor absolute")


def _anchor_message(
    *,
    implementation_revision: str,
    public_digest: str,
    public_raw_digest: str,
    event_family_bindings_digest: str,
    core_digest: str,
    remote: str,
    remote_locator: str,
) -> str:
    if type(remote) is not str or _SAFE_REMOTE_RE.fullmatch(remote) is None:
        raise ValueError("body/card anchor remote name is unsafe")
    if type(remote_locator) is not str or not remote_locator:
        raise ValueError("body/card anchor remote locator is missing")
    return _ascii_json(
        {
            "core_precommitment_digest": _require_digest(core_digest, "core digest"),
            "event_family_bindings_digest": _require_digest(
                event_family_bindings_digest,
                "event-family bindings digest",
            ),
            "implementation_revision": _require_git_oid(
                implementation_revision,
                "implementation revision",
            ),
            "public_precommitment_digest": _require_digest(public_digest, "public digest"),
            "public_precommitment_raw_digest": _require_digest(
                public_raw_digest,
                "public raw digest",
            ),
            "remote": remote,
            "remote_locator": remote_locator,
            "schema": BODY_CARD_COLLECTION_ANCHOR_SCHEMA_V3,
            "schema_version": BODY_CARD_REVIEW_VERSION_V3,
        }
    )


def _local_tag_details(
    repository: Path,
    *,
    tag_name: str,
    expected_revision: str,
    expected_message: str,
) -> tuple[str, str]:
    tag_oid = _git(repository, "rev-parse", "--verify", f"refs/tags/{tag_name}")
    assert tag_oid is not None
    tag_oid = _require_git_oid(tag_oid, "local anchor tag object")
    if _git(repository, "cat-file", "-t", tag_oid) != "tag":
        raise ValueError("body/card anchor must be an annotated tag")
    target = _git(repository, "rev-parse", "--verify", f"{tag_name}^{{}}")
    if target != expected_revision:
        raise ValueError("body/card anchor tag points at the wrong implementation revision")
    raw_tag = _git(repository, "cat-file", "-p", tag_oid)
    assert raw_tag is not None
    parts = raw_tag.split("\n\n", 1)
    headers = parts[0].splitlines() if parts else []
    if (
        len(parts) != 2
        or headers[:3]
        != [
            f"object {expected_revision}",
            "type commit",
            f"tag {tag_name}",
        ]
        or len(headers) != 4
        or _ANCHOR_TAGGER_RE.fullmatch(headers[3]) is None
        or parts[1].rstrip("\n") != expected_message
    ):
        raise ValueError("body/card anchor tag message differs from the frozen record")
    return tag_oid, target


def _remote_tag_details(
    repository: Path,
    *,
    remote: str,
    tag_name: str,
) -> tuple[str, str] | None:
    output = _git(
        repository,
        "ls-remote",
        "--tags",
        remote,
        f"refs/tags/{tag_name}",
        f"refs/tags/{tag_name}^{{}}",
    )
    assert output is not None
    if not output:
        return None
    refs: dict[str, str] = {}
    for line in output.splitlines():
        fields = line.split("\t")
        if len(fields) != 2:
            raise ValueError("remote body/card anchor response is malformed")
        refs[fields[1]] = _require_git_oid(fields[0], "remote anchor object")
    expected_refs = {
        f"refs/tags/{tag_name}",
        f"refs/tags/{tag_name}^{{}}",
    }
    if set(refs) != expected_refs:
        raise ValueError("remote body/card anchor is not one annotated tag")
    return refs[f"refs/tags/{tag_name}"], refs[f"refs/tags/{tag_name}^{{}}"]


def _verify_anchor_ack(
    root: Path,
    repository: Path,
    *,
    expected_public_digest: str,
    expected_revision: str,
    expected_message: str,
    expected_remote: str,
    expected_remote_locator: str,
) -> dict[str, Any]:
    raw = _private_file(root, ANCHOR_ACK_FILE, must_exist=True).read_bytes()
    ack = _exact_mapping(
        _canonical_json_lf(
            raw,
            label="external anchor acknowledgment",
            maximum=100_000,
        ),
        {
            "anchor_message_digest",
            "implementation_revision",
            "public_precommitment_digest",
            "remote",
            "remote_locator",
            "remote_ref",
            "remote_tag_object_oid",
            "schema",
            "schema_version",
            "tag_name",
        },
        "external anchor acknowledgment",
    )
    remote = ack["remote"]
    remote_locator = ack["remote_locator"]
    tag_name = ack["tag_name"]
    if (
        ack["schema"] != BODY_CARD_COLLECTION_ANCHOR_ACK_SCHEMA_V3
        or type(ack["schema"]) is not str
        or ack["schema_version"] != BODY_CARD_REVIEW_VERSION_V3
        or type(ack["schema_version"]) is not int
        or type(remote) is not str
        or _SAFE_REMOTE_RE.fullmatch(remote) is None
        or remote != expected_remote
        or type(remote_locator) is not str
        or remote_locator != expected_remote_locator
        or _remote_locator(repository, remote) != expected_remote_locator
        or type(tag_name) is not str
        or tag_name != _tag_name(expected_public_digest)
        or ack["remote_ref"] != f"refs/tags/{tag_name}"
        or ack["implementation_revision"] != expected_revision
        or ack["public_precommitment_digest"] != expected_public_digest
        or ack["anchor_message_digest"] != _sha256(expected_message.encode("ascii"))
    ):
        raise ValueError("external body/card anchor acknowledgment differs")
    local_tag_oid, local_target = _local_tag_details(
        repository,
        tag_name=tag_name,
        expected_revision=expected_revision,
        expected_message=expected_message,
    )
    remote_details = _remote_tag_details(
        repository,
        remote=remote,
        tag_name=tag_name,
    )
    if remote_details != (local_tag_oid, local_target) or ack["remote_tag_object_oid"] != (
        local_tag_oid
    ):
        raise ValueError("live remote body/card anchor differs from its acknowledgment")
    return ack


def _prepare(args: argparse.Namespace) -> dict[str, object]:
    target = _private_root(args.private_root, must_exist=False)
    target.mkdir(mode=0o700)
    execution._fsync_directory(target.parent)
    root = _private_root(target, must_exist=True)
    projection_source = _read_regular_source(
        args.projection_source,
        label="frozen projection source",
        maximum=1_000_000,
    )
    evaluator_source = _read_regular_source(
        args.evaluator_source,
        label="private evaluator source",
        maximum=BODY_CARD_EVALUATOR_MAX_BYTES_V3,
    )
    _write_private_once(
        _private_file(root, PROJECTION_FILE, must_exist=False),
        projection_source,
        maximum=1_000_000,
    )
    _write_private_once(
        _private_file(root, EVALUATOR_FILE, must_exist=False),
        evaluator_source,
        maximum=BODY_CARD_EVALUATOR_MAX_BYTES_V3,
    )
    projection = load_frozen_body_card_projection_v3(projection_source)
    bindings_raw = _load_evaluator_and_build_bindings(evaluator_source, projection)
    bindings_path = _write_private_once(
        _private_file(root, EVENT_BINDINGS_FILE, must_exist=False),
        bindings_raw,
        maximum=1_000_000,
    )
    bindings = load_body_card_event_family_bindings_v3(bindings_path.read_bytes(), projection)
    policy = BodyCardReviewRunPolicyV3(
        executable_provenance=capture_codex_executable_provenance(args.executable),
    )
    prepared = execution.prepare_body_card_reviews_v3(
        projection,
        policy,
        repository_root=args.repository_root,
        auth_file=args.auth_file,
        event_family_bindings_digest=bindings.raw_file_digest,
    )
    private_state_path = execution.save_body_card_review_private_state_v3(
        root / PRIVATE_STATE_FILE,
        prepared,
    )
    ledger = execution.create_body_card_review_ledger_v3(
        prepared,
        root / LEDGER_DIRECTORY,
    )
    bound = execution.bind_body_card_review_ledger_v3(
        prepared,
        ledger,
        private_state_path=private_state_path,
    )
    public_bytes = execution.finalize_body_card_review_publication_v3(
        bound,
        ledger=ledger,
        repository_root=args.repository_root,
        auth_file=args.auth_file,
        publication_receipt_path=root / PUBLICATION_RECEIPT_FILE,
    )
    _write_private_once(
        _private_file(root, PUBLIC_PRECOMMITMENT_FILE, must_exist=False),
        public_bytes,
        maximum=1_000_000,
    )
    (root / RUNTIME_DIRECTORY).mkdir(mode=0o700)
    execution._fsync_directory(root)
    if any(ledger.state(slot.slot_id) is not SlotState.PENDING for slot in prepared.slots):
        raise ValueError("body/card prepare phase consumed an attempt slot")
    summary = {
        "core_precommitment_digest": prepared.core_precommitment_digest,
        "event_family_bindings_digest": bindings.raw_file_digest,
        "implementation_revision": prepared.implementation_revision,
        "public_precommitment_digest": bound.public_precommitment_digest,
        "public_precommitment_raw_digest": _sha256(public_bytes),
        "schema": BODY_CARD_COLLECTION_SUMMARY_SCHEMA_V3,
        "schema_version": BODY_CARD_REVIEW_VERSION_V3,
        "slot_count": len(prepared.slots),
        "state": "prepared_unanchored",
    }
    _write_private_once(
        _private_file(root, PREPARE_SUMMARY_FILE, must_exist=False),
        canonical_json(summary).encode("utf-8") + b"\n",
        maximum=100_000,
    )
    return summary


def _anchor(args: argparse.Namespace) -> dict[str, object]:
    root = _private_root(args.private_root, must_exist=True)
    repository = Path(args.repository_root).resolve(strict=True)
    expected_digest = _require_digest(args.expected_public_digest, "expected public digest")
    _projection, bindings, prepared, ledger, bound, public_bytes = _load_bound_collection(
        root,
        expected_public_digest=expected_digest,
    )
    if any(ledger.state(slot.slot_id) is not SlotState.PENDING for slot in prepared.slots):
        raise ValueError("body/card anchor must precede every STARTED transition")
    observed_root, observed_revision = (
        execution.capture_body_card_review_implementation_revision_v3(repository)
    )
    if (
        observed_root != prepared.repository_root
        or observed_revision != prepared.implementation_revision
    ):
        raise ValueError("body/card anchor implementation differs from preparation")
    remote = args.remote
    if type(remote) is not str or _SAFE_REMOTE_RE.fullmatch(remote) is None:
        raise ValueError("body/card anchor remote name is unsafe")
    remote_locator = _remote_locator(repository, remote)
    message = _anchor_message(
        implementation_revision=prepared.implementation_revision,
        public_digest=bound.public_precommitment_digest,
        public_raw_digest=_sha256(public_bytes),
        event_family_bindings_digest=bindings.raw_file_digest,
        core_digest=prepared.core_precommitment_digest,
        remote=remote,
        remote_locator=remote_locator,
    )
    tag_name = _tag_name(expected_digest)
    local_ref = _git(
        repository,
        "rev-parse",
        "--verify",
        f"refs/tags/{tag_name}",
        check=False,
    )
    if local_ref is None:
        _git(
            repository,
            "tag",
            "--annotate",
            "--no-sign",
            "--message",
            message,
            tag_name,
            prepared.implementation_revision,
            anchor_identity=True,
        )
    local_tag_oid, local_target = _local_tag_details(
        repository,
        tag_name=tag_name,
        expected_revision=prepared.implementation_revision,
        expected_message=message,
    )
    remote_details = _remote_tag_details(
        repository,
        remote=remote,
        tag_name=tag_name,
    )
    if remote_details is None:
        _git(
            repository,
            "push",
            remote,
            f"refs/tags/{tag_name}:refs/tags/{tag_name}",
            timeout=300,
        )
        remote_details = _remote_tag_details(
            repository,
            remote=remote,
            tag_name=tag_name,
        )
    if remote_details != (local_tag_oid, local_target):
        raise ValueError("remote body/card anchor differs from the local annotated tag")
    ack = {
        "anchor_message_digest": _sha256(message.encode("ascii")),
        "implementation_revision": prepared.implementation_revision,
        "public_precommitment_digest": expected_digest,
        "remote": remote,
        "remote_locator": remote_locator,
        "remote_ref": f"refs/tags/{tag_name}",
        "remote_tag_object_oid": local_tag_oid,
        "schema": BODY_CARD_COLLECTION_ANCHOR_ACK_SCHEMA_V3,
        "schema_version": BODY_CARD_REVIEW_VERSION_V3,
        "tag_name": tag_name,
    }
    _write_private_once(
        root / ANCHOR_ACK_FILE,
        canonical_json(ack).encode("utf-8") + b"\n",
        maximum=100_000,
    )
    _verify_anchor_ack(
        root,
        repository,
        expected_public_digest=expected_digest,
        expected_revision=prepared.implementation_revision,
        expected_message=message,
        expected_remote=remote,
        expected_remote_locator=remote_locator,
    )
    return {
        "implementation_revision": prepared.implementation_revision,
        "public_precommitment_digest": expected_digest,
        "remote_ref": f"refs/tags/{tag_name}",
        "remote_tag_object_oid": local_tag_oid,
        "state": "anchored_pending",
    }


def _execute(args: argparse.Namespace) -> dict[str, object]:
    root = _private_root(args.private_root, must_exist=True)
    repository = Path(args.repository_root).resolve(strict=True)
    expected_digest = _require_digest(args.expected_public_digest, "expected public digest")
    _projection, bindings, prepared, ledger, bound, public_bytes = _load_bound_collection(
        root,
        expected_public_digest=expected_digest,
    )
    remote = args.remote
    remote_locator = _remote_locator(repository, remote)
    message = _anchor_message(
        implementation_revision=prepared.implementation_revision,
        public_digest=bound.public_precommitment_digest,
        public_raw_digest=_sha256(public_bytes),
        event_family_bindings_digest=bindings.raw_file_digest,
        core_digest=prepared.core_precommitment_digest,
        remote=remote,
        remote_locator=remote_locator,
    )
    _verify_anchor_ack(
        root,
        repository,
        expected_public_digest=expected_digest,
        expected_revision=prepared.implementation_revision,
        expected_message=message,
        expected_remote=remote,
        expected_remote_locator=remote_locator,
    )
    manifests = execution.execute_body_card_reviews_v3(
        bound,
        ledger=ledger,
        public_precommitment_bytes=public_bytes,
        expected_public_precommitment_digest=expected_digest,
        publication_receipt_path=root / PUBLICATION_RECEIPT_FILE,
        runtime_parent=root / RUNTIME_DIRECTORY,
        repository_root=repository,
        auth_file=args.auth_file,
    )
    pair_bytes = serialize_signed_body_card_review_pair_v3(
        manifests[0],
        manifests[1],
        bindings=bindings,
        review_precommitment_bytes=public_bytes,
        expected_review_precommitment_digest=expected_digest,
    )
    _write_private_once(
        root / SIGNED_PAIR_FILE,
        pair_bytes,
        maximum=BODY_CARD_PRIVATE_OUTPUT_MAX_BYTES_V3,
    )
    report = verify_and_select_signed_body_card_review_pair_bytes_v3(
        pair_bytes,
        bindings=bindings,
        review_precommitment_bytes=public_bytes,
        expected_review_precommitment_digest=expected_digest,
    )
    return {
        "candidate_count": report.candidate_count,
        "pair_digest": _sha256(pair_bytes),
        "public_precommitment_digest": expected_digest,
        "selected_count": len(report.selected),
        "selection_vector_digest": report.selection_vector_digest,
        "state": "executed_pair_persisted",
        "status": report.status.value,
    }


def _verify(args: argparse.Namespace) -> dict[str, object]:
    root = _private_root(args.private_root, must_exist=True)
    repository = Path(args.repository_root).resolve(strict=True)
    expected_digest = _require_digest(args.expected_public_digest, "expected public digest")
    _projection_raw, projection = _load_projection(root)
    _bindings_raw, bindings = _load_bindings(root, projection)
    public_bytes = _private_file(root, PUBLIC_PRECOMMITMENT_FILE, must_exist=True).read_bytes()
    public = parse_body_card_review_public_precommitment_v3(
        public_bytes,
        expected_digest=expected_digest,
    )
    if body_card_review_public_precommitment_digest_v3(public) != expected_digest:
        raise ValueError("cold verifier public precommitment digest differs")
    core = public.get("core")
    if not isinstance(core, Mapping):
        raise ValueError("cold verifier public core is missing")
    implementation = core.get("implementation_revision")
    if not isinstance(implementation, Mapping):
        raise ValueError("cold verifier implementation revision is missing")
    revision = _require_git_oid(implementation.get("oid"), "cold verifier revision")
    core_digest = _require_digest(public.get("core_digest"), "cold verifier core digest")
    observed_root, observed_revision = (
        execution.capture_body_card_review_implementation_revision_v3(repository)
    )
    if observed_root != str(repository) or observed_revision != revision:
        raise ValueError("cold verifier implementation differs from the anchored revision")
    remote = args.remote
    remote_locator = _remote_locator(repository, remote)
    message = _anchor_message(
        implementation_revision=revision,
        public_digest=expected_digest,
        public_raw_digest=_sha256(public_bytes),
        event_family_bindings_digest=bindings.raw_file_digest,
        core_digest=core_digest,
        remote=remote,
        remote_locator=remote_locator,
    )
    _verify_anchor_ack(
        root,
        repository,
        expected_public_digest=expected_digest,
        expected_revision=revision,
        expected_message=message,
        expected_remote=remote,
        expected_remote_locator=remote_locator,
    )
    pair_bytes = _private_file(root, SIGNED_PAIR_FILE, must_exist=True).read_bytes()
    report = verify_and_select_signed_body_card_review_pair_bytes_v3(
        pair_bytes,
        bindings=bindings,
        review_precommitment_bytes=public_bytes,
        expected_review_precommitment_digest=expected_digest,
    )
    report_bytes = canonical_json(report.to_private_dict()).encode("utf-8") + b"\n"
    _write_private_once(
        root / SELECTION_REPORT_FILE,
        report_bytes,
        maximum=BODY_CARD_PRIVATE_OUTPUT_MAX_BYTES_V3,
    )
    return {
        "candidate_count": report.candidate_count,
        "pair_digest": _sha256(pair_bytes),
        "public_precommitment_digest": expected_digest,
        "selected_count": len(report.selected),
        "selection_vector_digest": report.selection_vector_digest,
        "state": "cold_verified",
        "status": report.status.value,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Frozen body/card dual-review collector")
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--private-root", required=True)
    prepare.add_argument("--projection-source", required=True)
    prepare.add_argument("--evaluator-source", required=True)
    prepare.add_argument("--repository-root", required=True)
    prepare.add_argument("--auth-file", required=True)
    prepare.add_argument("--executable", required=True)

    anchor = subparsers.add_parser("anchor")
    anchor.add_argument("--private-root", required=True)
    anchor.add_argument("--repository-root", required=True)
    anchor.add_argument("--expected-public-digest", required=True)
    anchor.add_argument("--remote", default="origin")

    execute = subparsers.add_parser("execute")
    execute.add_argument("--private-root", required=True)
    execute.add_argument("--repository-root", required=True)
    execute.add_argument("--auth-file", required=True)
    execute.add_argument("--expected-public-digest", required=True)
    execute.add_argument("--remote", default="origin")

    verify = subparsers.add_parser("verify")
    verify.add_argument("--private-root", required=True)
    verify.add_argument("--repository-root", required=True)
    verify.add_argument("--expected-public-digest", required=True)
    verify.add_argument("--remote", default="origin")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "prepare":
        result = _prepare(args)
    elif args.command == "anchor":
        result = _anchor(args)
    elif args.command == "execute":
        result = _execute(args)
    elif args.command == "verify":
        result = _verify(args)
    else:  # pragma: no cover - argparse owns this boundary
        raise AssertionError("unreachable body/card collection command")
    sys.stdout.write(_ascii_json(result) + "\n")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by subprocess integration
    raise SystemExit(main())
