import hashlib
import json
import tomllib
from pathlib import Path

import pytest

from math_brain.privacy import PrivacyViolation
from math_brain.publication import (
    REDISTRIBUTABLE_LICENSE_IDS,
    check_public_export,
    load_public_export_manifest,
)

_PATH_SEPARATOR = chr(92)


def _write_export(
    repository: Path,
    content: str = "deterministic algebra benchmark",
    **overrides,
) -> tuple[Path, Path]:
    public = repository / "data" / "public"
    public.mkdir(parents=True, exist_ok=True)
    artifact = public / "sample.jsonl"
    artifact.write_text(content, encoding="utf-8")
    fields = {
        "schema_version": 1,
        "artifact": "data/public/sample.jsonl",
        "artifact_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "rights": "public_domain",
        "license_id": "CC0-1.0",
        "provenance_refs": ["generated:deterministic-algebra-v1"],
        "source_ids": ["source-public-001"],
        "reviewer_id": "reviewer-001",
        "reviewed_at": "2026-07-13T12:00:00+00:00",
        "redistribution_confirmed": True,
        "local_material_excluded": True,
    }
    fields.update(overrides)
    manifest = public / "sample.jsonl.provenance.toml"
    manifest.write_text(
        "\n".join(
            [
                f'schema_version = {fields["schema_version"]}',
                f'artifact = {json.dumps(fields["artifact"])}',
                f'artifact_sha256 = {json.dumps(fields["artifact_sha256"])}',
                f'rights = {json.dumps(fields["rights"])}',
                f'license_id = {json.dumps(fields["license_id"])}',
                f'provenance_refs = {json.dumps(fields["provenance_refs"])}',
                f'source_ids = {json.dumps(fields["source_ids"])}',
                f'reviewer_id = {json.dumps(fields["reviewer_id"])}',
                f'reviewed_at = {json.dumps(fields["reviewed_at"])}',
                "redistribution_confirmed = "
                + str(fields["redistribution_confirmed"]).lower(),
                "local_material_excluded = " + str(fields["local_material_excluded"]).lower(),
                "",
            ]
        ),
        encoding="utf-8",
    )
    return artifact, manifest


def test_public_export_requires_matching_provenance_license_hash_and_review(tmp_path: Path) -> None:
    artifact, manifest = _write_export(tmp_path)

    decision = check_public_export(
        artifact,
        manifest_path=manifest,
        repository_root=tmp_path,
    )

    assert decision.allowed
    assert decision.code == "allowed"


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"rights": "private"}, "export-rights"),
        ({"license_id": "NOASSERTION"}, "export-license"),
        ({"license_id": "MadeUp-1.0"}, "export-license"),
        ({"redistribution_confirmed": False}, "export-rights"),
        ({"local_material_excluded": False}, "export-provenance"),
        ({"reviewed_at": "2026-07-13"}, "export-schema"),
        ({"provenance_refs": ["https://127.0.0.1/source"]}, "export-provenance"),
        ({"artifact": _PATH_SEPARATOR.join(["data", "public", "sample.jsonl"])}, "export-schema"),
        ({"artifact": "data/public/./sample.jsonl"}, "export-schema"),
        ({"artifact": "data/public/sample.jsonl:stream"}, "export-schema"),
        ({"artifact": "data/public/NUL.jsonl"}, "export-schema"),
    ],
)
def test_public_export_fails_closed_on_missing_release_authority(
    tmp_path: Path,
    overrides: dict[str, object],
    code: str,
) -> None:
    _artifact, manifest = _write_export(tmp_path, **overrides)

    with pytest.raises(PrivacyViolation) as error:
        load_public_export_manifest(manifest, repository_root=tmp_path)

    assert error.value.code == code


def test_public_export_rejects_local_path_in_provenance(tmp_path: Path) -> None:
    separator = chr(92)
    local_path = "".join(
        ["C", ":", separator, "Users", separator, "sample", separator, "proof.tex"]
    )
    _artifact, manifest = _write_export(
        tmp_path,
        provenance_refs=["https://example.org/source?local=" + local_path],
    )

    with pytest.raises(PrivacyViolation) as error:
        load_public_export_manifest(manifest, repository_root=tmp_path)

    assert error.value.code == "export-metadata-privacy"


def test_public_export_rejects_secret_canary_content(tmp_path: Path) -> None:
    key = "".join(["api", "_key"])
    value = "".join(["synthetic", "-canary", "-123456"])
    artifact, manifest = _write_export(tmp_path, content=f"{key} = {value}")

    decision = check_public_export(
        artifact,
        manifest_path=manifest,
        repository_root=tmp_path,
    )

    assert not decision.allowed
    assert decision.code == "privacy-scan-failed"


def _slack_webhook_canary() -> str:
    return "".join(["https://hooks", ".slack", ".com/services/T0", "ABCDEFGH", "/B0", "12345678", "/", "x" * 24])


def test_public_export_rejects_a_slack_webhook_in_the_artifact(tmp_path: Path) -> None:
    artifact, manifest = _write_export(tmp_path, content="post results to " + _slack_webhook_canary())

    decision = check_public_export(artifact, manifest_path=manifest, repository_root=tmp_path)

    assert not decision.allowed
    assert decision.code == "privacy-scan-failed"


def test_public_export_rejects_a_slack_webhook_as_provenance(tmp_path: Path) -> None:
    artifact, manifest = _write_export(tmp_path, provenance_refs=[_slack_webhook_canary()])

    try:
        decision = check_public_export(artifact, manifest_path=manifest, repository_root=tmp_path)
    except PrivacyViolation:
        return
    assert not decision.allowed


def _fine_grained_github_canary() -> str:
    return "".join(["github", "_pat_", "1" * 22, "_", "A" * 59])


def test_public_export_rejects_fine_grained_github_token_in_the_artifact(tmp_path: Path) -> None:
    artifact, manifest = _write_export(tmp_path, content=_fine_grained_github_canary())

    decision = check_public_export(
        artifact,
        manifest_path=manifest,
        repository_root=tmp_path,
    )

    assert not decision.allowed
    assert decision.code == "privacy-scan-failed"


def test_public_export_rejects_fine_grained_github_token_in_the_provenance(
    tmp_path: Path,
) -> None:
    artifact, manifest = _write_export(
        tmp_path,
        provenance_refs=["generated:deterministic-algebra-v1", _fine_grained_github_canary()],
    )

    try:
        decision = check_public_export(
            artifact,
            manifest_path=manifest,
            repository_root=tmp_path,
        )
    except PrivacyViolation:
        return
    assert not decision.allowed


def _temporary_aws_key_id() -> str:
    return "".join(["AS", "IA", "IOSF", "ODNN", "7EXA", "MPLE"])


def test_public_export_rejects_a_temporary_aws_credential_set_in_the_artifact(tmp_path: Path) -> None:
    fields = {
        "".join(["aws", "_access", "_key", "_id"]): _temporary_aws_key_id(),
        "".join(["aws", "_secret", "_access", "_key"]): "".join(["synthetic", "/", "S" * 31]),
        "".join(["aws", "_session", "_token"]): "".join(["session", "-", "T" * 60]),
    }
    # one line: _write_export writes text mode, so a newline would change the bytes it hashed
    artifact, manifest = _write_export(tmp_path, content=json.dumps(fields))

    decision = check_public_export(
        artifact,
        manifest_path=manifest,
        repository_root=tmp_path,
    )

    assert not decision.allowed
    assert decision.code == "privacy-scan-failed"
    assert all(value not in decision.reason for value in fields.values())


@pytest.mark.parametrize(
    "wrap",
    ["{k}", "generated:{k}", "generated:run-{k}-v1"],
    ids=["bare", "generated", "embedded"],
)
def test_a_key_id_in_a_provenance_ref_is_refused_by_the_ref_format(tmp_path: Path, wrap: str) -> None:
    # the provenance path closes before the scanner runs: a generated: id is a lowercase slug of at
    # most 64 characters and every other scheme must be a public URL, a DOI or a URN
    artifact, manifest = _write_export(
        tmp_path,
        provenance_refs=["generated:deterministic-algebra-v1", wrap.format(k=_temporary_aws_key_id())],
    )

    decision = check_public_export(
        artifact,
        manifest_path=manifest,
        repository_root=tmp_path,
    )

    assert not decision.allowed
    assert decision.code == "export-provenance"


def test_public_export_rejects_hash_mismatch(tmp_path: Path) -> None:
    artifact, manifest = _write_export(tmp_path, artifact_sha256="0" * 64)

    decision = check_public_export(
        artifact,
        manifest_path=manifest,
        repository_root=tmp_path,
    )

    assert not decision.allowed
    assert decision.code == "hash-mismatch"


def test_public_export_rejects_linked_artifact(tmp_path: Path) -> None:
    artifact, manifest = _write_export(tmp_path)
    target = artifact.with_name("target.jsonl")
    artifact.replace(target)
    try:
        artifact.symlink_to(target)
    except OSError:
        pytest.skip("symlinks unavailable on this platform")

    decision = check_public_export(
        artifact,
        manifest_path=manifest,
        repository_root=tmp_path,
    )

    assert not decision.allowed
    assert decision.code == "link-component"


def test_license_allowlist_matches_committed_policy() -> None:
    repository = Path(__file__).resolve().parents[1]
    with (repository / "config" / "privacy.toml").open("rb") as handle:
        policy = tomllib.load(handle)

    configured = frozenset(policy["public_export"]["redistributable_license_ids"])

    assert configured == REDISTRIBUTABLE_LICENSE_IDS
