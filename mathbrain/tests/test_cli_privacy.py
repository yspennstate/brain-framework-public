import json
from pathlib import Path

import pytest

from math_brain.cli import main


def _write_manifest(repository: Path, approved: Path) -> Path:
    audit = repository / "local" / "audits" / "audit-001"
    audit.mkdir(parents=True)
    manifest = audit / "allowlist.toml"
    manifest.write_text(
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
    return manifest


def test_policy_check_cli_requires_manifest_source_grant(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    approved = tmp_path / "approved"
    approved.mkdir()
    source = approved / "lemma.tex"
    source.write_text("lemma", encoding="utf-8")
    manifest = _write_manifest(tmp_path, approved)

    result = main(
        [
            "policy",
            "check",
            str(source),
            "--manifest",
            str(manifest),
            "--source-id",
            "source-001",
            "--repository-root",
            str(tmp_path),
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert result == 0
    assert payload["allowed"] is True


def test_policy_check_cli_has_no_ad_hoc_root_bypass(tmp_path: Path) -> None:
    approved = tmp_path / "approved"
    approved.mkdir()
    source = approved / "lemma.tex"
    source.write_text("lemma", encoding="utf-8")

    with pytest.raises(SystemExit) as error:
        main(["policy", "check", str(source), "--allow-root", str(approved)])

    assert error.value.code == 2


def test_policy_scan_cli_suppresses_detected_value(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = tmp_path / "config"
    config.mkdir()
    (config / "privacy.toml").write_text(
        "\n".join(
            [
                "[hard_exclusions]",
                'path_tokens = ["forbidden-marker"]',
                "content_tokens = []",
                "[scanner]",
                "max_file_bytes = 4096",
                'policy_category_contexts = ["docs/policy.md"]',
            ]
        ),
        encoding="utf-8",
    )
    key = "".join(["api", "_key"])
    value = "".join(["synthetic", "-canary", "-123456"])
    canary = f"{key} = {value}"
    artifact = tmp_path / "artifact.txt"
    artifact.write_text(canary, encoding="utf-8")

    result = main(
        [
            "policy",
            "scan",
            "artifact.txt",
            "--repository-root",
            str(tmp_path),
        ]
    )
    output = capsys.readouterr().out
    payload = json.loads(output)

    assert result == 2
    assert payload["allowed"] is False
    assert canary not in output
