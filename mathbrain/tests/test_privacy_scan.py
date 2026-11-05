from pathlib import Path

import pytest

from math_brain.privacy_scan import (
    ScanPolicy,
    load_scan_policy,
    scan_repository_files,
    scan_text,
)


def _windows_path_canary() -> str:
    separator = chr(92)
    return "".join(["C", ":", separator, "Users", separator, "sample", separator, "notes.txt"])


def _secret_canary() -> str:
    key = "".join(["api", "_key"])
    value = "".join(["synthetic", "-canary", "-123456"])
    return f"{key} = {value}"


def _forbidden_category(policy: ScanPolicy) -> str:
    return sorted(policy.forbidden_categories)[-1]


def test_scanner_detects_absolute_paths_without_echoing_them() -> None:
    canary = _windows_path_canary()
    findings = scan_text(canary, relative_path="data/public/result.txt")

    assert {finding.code for finding in findings} == {"absolute-path"}
    assert all(canary not in finding.message for finding in findings)


@pytest.mark.parametrize(
    "canary",
    [
        chr(47) + "workspace" + chr(47) + "approved" + chr(47) + "notes.txt",
        "file" + "://" + "localhost" + chr(47) + "approved" + chr(47) + "notes.txt",
        chr(92) * 2 + "server" + chr(92) + "share" + chr(92) + "notes.txt",
    ],
)
def test_scanner_detects_posix_file_uri_and_unc_paths(canary: str) -> None:
    findings = scan_text(canary, relative_path="data/public/result.txt")

    assert "absolute-path" in {finding.code for finding in findings}


def test_scanner_does_not_misread_latex_regex_as_unc_path() -> None:
    latex_pattern = 're.sub(r"' + chr(92) * 2 + 'mathbb' + chr(92) + 's*", "", value)'

    findings = scan_text(latex_pattern, relative_path="src/math_brain/retrieval.py")

    assert "absolute-path" not in {finding.code for finding in findings}


def test_scanner_detects_secrets_without_echoing_values() -> None:
    canary = _secret_canary()
    findings = scan_text(canary, relative_path="data/public/result.txt")

    assert "secret" in {finding.code for finding in findings}
    assert all(canary not in finding.message for finding in findings)


def test_scanner_detects_provider_token_canary() -> None:
    canary = "".join(["sk", "-", "x" * 24])
    findings = scan_text(canary, relative_path="data/public/result.txt")

    assert "secret" in {finding.code for finding in findings}
    assert all(canary not in finding.message for finding in findings)


def test_forbidden_categories_are_context_aware_but_secrets_are_not() -> None:
    policy = ScanPolicy(policy_category_contexts=frozenset({"docs/privacy-boundary.md"}))
    category = _forbidden_category(policy)

    ordinary = scan_text(category, relative_path="data/public/result.txt", policy=policy)
    policy_doc = scan_text(category, relative_path="docs/privacy-boundary.md", policy=policy)
    policy_doc_secret = scan_text(
        _secret_canary(),
        relative_path="docs/privacy-boundary.md",
        policy=policy,
    )

    assert "forbidden-category" in {finding.code for finding in ordinary}
    assert not policy_doc
    assert "secret" in {finding.code for finding in policy_doc_secret}


def test_scanner_refuses_directory_recursion_without_reading_nested_canary(tmp_path: Path) -> None:
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "canary.txt").write_text(_secret_canary(), encoding="utf-8")

    findings = scan_repository_files(repository_root=tmp_path, relative_paths=["nested"])

    assert {finding.code for finding in findings} == {"directory-input"}


def test_scanner_accepts_only_repository_relative_file_names(tmp_path: Path) -> None:
    safe = tmp_path / "safe.txt"
    safe.write_text("elementary algebra", encoding="utf-8")

    clean = scan_repository_files(repository_root=tmp_path, relative_paths=["safe.txt"])
    absolute = scan_repository_files(repository_root=tmp_path, relative_paths=[safe])
    traversal = scan_repository_files(repository_root=tmp_path, relative_paths=["../outside.txt"])

    assert not clean
    assert {finding.code for finding in absolute} == {"absolute-input-path"}
    assert {finding.code for finding in traversal} == {"absolute-input-path"}


def test_scanner_rejects_empty_and_drive_relative_input_sets(tmp_path: Path) -> None:
    empty = scan_repository_files(repository_root=tmp_path, relative_paths=[])
    drive_relative = scan_repository_files(
        repository_root=tmp_path,
        relative_paths=["C:relative.txt"],
    )

    assert {finding.code for finding in empty} == {"no-input-files"}
    assert {finding.code for finding in drive_relative} == {"absolute-input-path"}


@pytest.mark.parametrize("path", ["safe.txt:stream", "NUL.txt", "folder./safe.txt"])
def test_scanner_rejects_alternate_stream_and_device_aliases(tmp_path: Path, path: str) -> None:
    findings = scan_repository_files(repository_root=tmp_path, relative_paths=[path])

    assert {finding.code for finding in findings} == {"unsafe-path-syntax"}


def test_scanner_refuses_home_root(tmp_path: Path) -> None:
    pretend_home = tmp_path / "home"
    pretend_home.mkdir()
    (pretend_home / "safe.txt").write_text("algebra", encoding="utf-8")

    findings = scan_repository_files(
        repository_root=pretend_home,
        relative_paths=["safe.txt"],
        home=pretend_home,
    )

    assert {finding.code for finding in findings} == {"home-recursion"}


def test_scanner_rejects_symlink_input(tmp_path: Path) -> None:
    target = tmp_path / "target.txt"
    target.write_text("algebra", encoding="utf-8")
    link = tmp_path / "linked.txt"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlinks unavailable on this platform")

    findings = scan_repository_files(repository_root=tmp_path, relative_paths=["linked.txt"])

    assert {finding.code for finding in findings} == {"unsafe-path"}


def test_committed_policy_config_loads_contexts_and_content_categories(tmp_path: Path) -> None:
    config = tmp_path / "privacy.toml"
    category = "".join(["forbidden", "-canary"])
    config.write_text(
        "\n".join(
            [
                "[hard_exclusions]",
                f'path_tokens = ["{category}"]',
                'content_tokens = ["private marker"]',
                "[scanner]",
                "max_file_bytes = 2048",
                'policy_category_contexts = ["docs/policy.md"]',
            ]
        ),
        encoding="utf-8",
    )

    policy = load_scan_policy(config, repository_root=tmp_path)

    assert category in policy.forbidden_categories
    assert "private marker" in policy.forbidden_categories
    assert policy.policy_category_contexts == frozenset({"docs/policy.md"})
    assert policy.max_file_bytes == 2048
