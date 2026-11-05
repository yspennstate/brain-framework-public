import os
import subprocess
from pathlib import Path

import pytest

import math_brain.privacy as privacy_module
from math_brain.privacy import (
    PrivacyViolation,
    check_local_derivation_output,
    check_manifest_source_path,
    check_public_output,
    check_source_path,
    load_allowlist_manifest,
    read_allowed_source,
    write_local_derivation,
)


def _manifest(
    repository: Path,
    source_root: Path,
    *,
    audit_id: str = "audit-001",
    rights: str = "private",
    home: Path | None = None,
):
    audit_root = repository / "local" / "audits" / audit_id
    audit_root.mkdir(parents=True)
    manifest_path = audit_root / "allowlist.toml"
    manifest_path.write_text(
        "\n".join(
            [
                "schema_version = 1",
                f'audit_id = "{audit_id}"',
                'derivation_policy = "local_only"',
                "no_home_recursion = true",
                f'local_output_root = "local/audits/{audit_id}"',
                "",
                "[[sources]]",
                'id = "source-001"',
                'label = "Synthetic mathematics"',
                f'root = "{source_root.as_posix()}"',
                f'rights = "{rights}"',
                'content_class = "pure_mathematics"',
                "max_file_bytes = 1024",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return load_allowlist_manifest(manifest_path, repository_root=repository, home=home)


def test_source_is_denied_without_allow_root(tmp_path: Path) -> None:
    source = tmp_path / "lemma.tex"
    source.write_text("lemma", encoding="utf-8")
    result = check_source_path(source, allow_roots=[])
    assert not result.allowed
    assert result.code == "no-valid-allow-root"


def test_source_outside_allowlist_is_denied(tmp_path: Path) -> None:
    allowed = tmp_path / "approved"
    outside = tmp_path / "other" / "lemma.tex"
    allowed.mkdir()
    outside.parent.mkdir()
    outside.write_text("lemma", encoding="utf-8")
    result = check_source_path(outside, allow_roots=[allowed])
    assert not result.allowed
    assert result.code == "outside-allowlist"


def test_denied_category_wins_inside_allowlist(tmp_path: Path) -> None:
    allowed = tmp_path / "approved"
    source = allowed / "financial-notes" / "lemma.tex"
    source.parent.mkdir(parents=True)
    source.write_text("lemma", encoding="utf-8")
    result = check_source_path(source, allow_roots=[allowed])
    assert not result.allowed
    assert result.code == "denied-path-token"


def test_ordinary_math_file_inside_allowlist_is_eligible(tmp_path: Path) -> None:
    allowed = tmp_path / "approved"
    source = allowed / "algebra" / "lemma.tex"
    source.parent.mkdir(parents=True)
    source.write_text("lemma", encoding="utf-8")
    result = check_source_path(source, allow_roots=[allowed])
    assert result.allowed
    assert result.resolved_path == source.resolve()


def test_hidden_relative_path_is_denied(tmp_path: Path) -> None:
    allowed = tmp_path / "approved"
    source = allowed / ".private" / "lemma.tex"
    source.parent.mkdir(parents=True)
    source.write_text("lemma", encoding="utf-8")
    result = check_source_path(source, allow_roots=[allowed])
    assert not result.allowed
    assert result.code == "hidden-path"


def test_alternate_stream_and_device_alias_syntax_is_denied(tmp_path: Path) -> None:
    approved = tmp_path / "approved"
    approved.mkdir()
    source_stream = approved / "lemma.tex:canary"
    source_result = check_source_path(source_stream, allow_roots=[approved])

    public = tmp_path / "data" / "public"
    public.mkdir(parents=True)
    output_result = check_public_output(public / "NUL.txt", repository_root=tmp_path)
    separator = chr(92)
    network_path = Path(separator * 2 + "server" + separator + "share" + separator + "lemma.tex")
    network_result = check_source_path(network_path, allow_roots=[approved])

    assert source_result.code == "unsafe-path-syntax"
    assert output_result.code == "unsafe-path-syntax"
    assert network_result.code == "unsafe-path-syntax"


def test_public_output_must_use_public_prefix(tmp_path: Path) -> None:
    (tmp_path / "data" / "public").mkdir(parents=True)
    allowed = check_public_output(
        tmp_path / "data" / "public" / "bank.jsonl", repository_root=tmp_path
    )
    denied = check_public_output(tmp_path / "local" / "audit.json", repository_root=tmp_path)
    assert allowed.allowed
    assert not denied.allowed
    assert denied.code == "nonpublic-prefix"


def test_allowlist_manifest_is_strict_and_source_scoped(tmp_path: Path) -> None:
    approved = tmp_path / "approved"
    approved.mkdir()
    source = approved / "lemma.tex"
    source.write_text("lemma", encoding="utf-8")
    manifest = _manifest(tmp_path, approved)

    accepted = check_manifest_source_path(source, manifest=manifest, source_id="source-001")
    unknown = check_manifest_source_path(source, manifest=manifest, source_id="source-999")

    assert accepted.allowed
    assert not unknown.allowed
    assert unknown.code == "unknown-source"
    assert manifest.derivation_policy == "local_only"


def test_allowlist_manifest_rejects_unknown_fields(tmp_path: Path) -> None:
    approved = tmp_path / "approved"
    approved.mkdir()
    audit_root = tmp_path / "local" / "audits" / "audit-001"
    audit_root.mkdir(parents=True)
    manifest_path = audit_root / "allowlist.toml"
    manifest_path.write_text(
        "\n".join(
            [
                "schema_version = 1",
                'audit_id = "audit-001"',
                'derivation_policy = "local_only"',
                "no_home_recursion = true",
                'local_output_root = "local/audits/audit-001"',
                'unexpected = "typo"',
                "[[sources]]",
                'id = "source-001"',
                'label = "Synthetic mathematics"',
                f'root = "{approved.as_posix()}"',
                'rights = "private"',
                'content_class = "pure_mathematics"',
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(PrivacyViolation, match="unknown fields") as error:
        load_allowlist_manifest(manifest_path, repository_root=tmp_path)
    assert error.value.code == "manifest-schema"


def test_manifest_must_live_in_its_exact_local_audit_root(tmp_path: Path) -> None:
    approved = tmp_path / "approved"
    approved.mkdir()
    wrong_root = tmp_path / "local" / "audits" / "wrong-place"
    wrong_root.mkdir(parents=True)
    manifest_path = wrong_root / "allowlist.toml"
    manifest_path.write_text(
        "\n".join(
            [
                "schema_version = 1",
                'audit_id = "audit-001"',
                'derivation_policy = "local_only"',
                "no_home_recursion = true",
                'local_output_root = "local/audits/audit-001"',
                "[[sources]]",
                'id = "source-001"',
                'label = "Synthetic mathematics"',
                f'root = "{approved.as_posix()}"',
                'rights = "private"',
                'content_class = "pure_mathematics"',
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(PrivacyViolation) as error:
        load_allowlist_manifest(manifest_path, repository_root=tmp_path)
    assert error.value.code == "manifest-location"


def test_home_root_and_ancestor_are_never_discovery_roots(tmp_path: Path) -> None:
    pretend_home = tmp_path / "pretend-home"
    pretend_home.mkdir()
    source = pretend_home / "lemma.tex"
    source.write_text("lemma", encoding="utf-8")

    home_result = check_source_path(source, allow_roots=[pretend_home], home=pretend_home)
    ancestor_result = check_source_path(source, allow_roots=[tmp_path], home=pretend_home)

    assert home_result.code == "home-recursion"
    assert ancestor_result.code == "home-recursion"


def test_directory_and_hardlink_sources_fail_closed(tmp_path: Path) -> None:
    approved = tmp_path / "approved"
    approved.mkdir()
    directory_result = check_source_path(approved, allow_roots=[approved])
    assert directory_result.code == "not-regular-file"

    source = approved / "lemma.tex"
    alias = approved / "alias.tex"
    source.write_text("lemma", encoding="utf-8")
    try:
        os.link(source, alias)
    except OSError:
        pytest.skip("hardlinks unavailable on this filesystem")
    hardlink_result = check_source_path(source, allow_roots=[approved])
    assert hardlink_result.code == "hardlink"


def test_symlink_component_is_denied_even_when_target_is_inside_root(tmp_path: Path) -> None:
    approved = tmp_path / "approved"
    real = approved / "real"
    real.mkdir(parents=True)
    source = real / "lemma.tex"
    source.write_text("lemma", encoding="utf-8")
    linked = approved / "linked"
    try:
        linked.symlink_to(real, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable on this platform")

    result = check_source_path(linked / "lemma.tex", allow_roots=[approved])

    assert result.code == "link-component"


@pytest.mark.skipif(os.name != "nt", reason="junctions are a Windows path primitive")
def test_windows_junction_component_is_denied(tmp_path: Path) -> None:
    approved = tmp_path / "approved"
    target = tmp_path / "junction-target"
    approved.mkdir()
    target.mkdir()
    (target / "lemma.tex").write_text("lemma", encoding="utf-8")
    junction = approved / "junction"
    completed = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(target)],
        capture_output=True,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        text=True,
    )
    if completed.returncode != 0:
        pytest.skip("junction creation unavailable")
    try:
        result = check_source_path(junction / "lemma.tex", allow_roots=[approved])
        assert result.code == "link-component"
    finally:
        os.rmdir(junction)


def test_stable_descriptor_rejects_check_open_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    approved = tmp_path / "approved"
    approved.mkdir()
    source = approved / "lemma.tex"
    replacement = approved / "replacement.tex"
    backup = approved / "original.tex"
    source.write_text("approved", encoding="utf-8")
    replacement.write_text("replacement", encoding="utf-8")
    manifest = _manifest(tmp_path, approved)
    real_open = privacy_module.os.open
    swapped = False

    def swapping_open(path, flags, *args):
        nonlocal swapped
        if Path(path) == source and not swapped:
            swapped = True
            source.replace(backup)
            replacement.replace(source)
        return real_open(path, flags, *args)

    monkeypatch.setattr(privacy_module.os, "open", swapping_open)
    with pytest.raises(PrivacyViolation) as error:
        read_allowed_source(source, manifest=manifest, source_id="source-001")
    assert error.value.code == "path-race"


def test_stable_descriptor_rejects_hardlink_created_during_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    approved = tmp_path / "approved"
    approved.mkdir()
    source = approved / "lemma.tex"
    alias = approved / "alias.tex"
    source.write_text("approved", encoding="utf-8")
    manifest = _manifest(tmp_path, approved)
    real_open = privacy_module.os.open
    linked = False

    def linking_open(path, flags, *args):
        nonlocal linked
        if Path(path) == source and not linked:
            try:
                os.link(source, alias)
            except OSError:
                pytest.skip("hardlinks unavailable on this filesystem")
            linked = True
        return real_open(path, flags, *args)

    monkeypatch.setattr(privacy_module.os, "open", linking_open)
    with pytest.raises(PrivacyViolation) as error:
        read_allowed_source(source, manifest=manifest, source_id="source-001")
    assert error.value.code == "path-race"


def test_local_derivations_cannot_escape_or_overwrite(tmp_path: Path) -> None:
    approved = tmp_path / "approved"
    approved.mkdir()
    manifest = _manifest(tmp_path, approved)
    derived_root = manifest.local_output_root / "derived"
    derived_root.mkdir()
    destination = derived_root / "finding.json"

    created = write_local_derivation(destination, b"{}", manifest=manifest)
    outside = check_local_derivation_output(tmp_path / "docs" / "finding.json", manifest=manifest)

    assert created.read_bytes() == b"{}"
    assert not outside.allowed
    assert outside.code in {"outside-local-audit", "unresolvable"}
    with pytest.raises(PrivacyViolation) as error:
        write_local_derivation(destination, b"changed", manifest=manifest)
    assert error.value.code == "output-exists"


def test_local_derivation_requires_immutable_bytes(tmp_path: Path) -> None:
    approved = tmp_path / "approved"
    approved.mkdir()
    manifest = _manifest(tmp_path, approved)
    destination = manifest.local_output_root / "finding.json"

    with pytest.raises(PrivacyViolation) as error:
        write_local_derivation(
            destination,
            "not bytes",  # type: ignore[arg-type]
            manifest=manifest,
        )

    assert error.value.code == "invalid-output-data"
    assert not destination.exists()
