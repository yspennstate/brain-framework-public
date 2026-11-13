import json
import os
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


def _classic_github_canary() -> str:
    return "".join(["gh", "p_", "x" * 36])


def _fine_grained_github_canary(tail: int = 82) -> str:
    # github_pat_ + 22 + _ + 59, assembled so that no literal token sits in this file
    return "".join(["github", "_pat_", "1" * 22, "_", "A" * (tail - 23)])


@pytest.mark.parametrize(
    "placement",
    ["{t}", '{{"pat": "{t}"}}', 'pat = "{t}"', "pat: {t}"],
    ids=["standalone", "json", "toml", "yaml"],
)
@pytest.mark.parametrize(
    ("canary", "kind"),
    [
        (_classic_github_canary, "github-token"),
        (_fine_grained_github_canary, "github-fine-grained-token"),
    ],
    ids=["classic", "fine-grained"],
)
def test_scanner_detects_both_github_token_families(placement, canary, kind) -> None:
    token = canary()
    text = placement.format(t=token)
    findings = scan_text(text, relative_path="data/public/result.txt")

    assert any(f.code == "secret" and kind in f.message for f in findings)
    assert all(token not in finding.message for finding in findings)


def test_fine_grained_github_token_is_refused_in_a_policy_document() -> None:
    policy = ScanPolicy(policy_category_contexts=frozenset({"docs/privacy-boundary.md"}))
    findings = scan_text(
        _fine_grained_github_canary(),
        relative_path="docs/privacy-boundary.md",
        policy=policy,
    )

    assert "secret" in {finding.code for finding in findings}


@pytest.mark.parametrize(
    "near_miss",
    [
        "".join(["github", "_pat_", "A" * 10]),
        "".join(["github", "_patx_", "A" * 40]),
        "".join(["git", "hub", "pat_", "A" * 40]),
    ],
    ids=["too-short", "wrong-prefix", "no-underscore"],
)
def test_github_token_near_misses_do_not_fire(near_miss) -> None:
    findings = scan_text(near_miss, relative_path="data/public/result.txt")

    assert "secret" not in {finding.code for finding in findings}


# Temporary (ASIA) key ids and the
# conventional credential field names were not refused. Every value below is synthetic.


def _aws_key_id(prefix: str, tail: int = 16) -> str:
    return "".join([prefix[:2], prefix[2:], "IOSF", "ODNN", "7EXA", "MPLE", "Q" * (tail - 16)])[: 4 + tail]


_AWS_ID = "".join(["aws", "_access", "_key", "_id"])
_AWS_SECRET = "".join(["aws", "_secret", "_access", "_key"])
_AWS_TOKEN = "".join(["aws", "_session", "_token"])
_AWS_LEGACY_TOKEN = "".join(["aws", "_security", "_token"])
_SYNTHETIC_SECRET = "".join(["synthetic", "/", "S" * 31])
_SYNTHETIC_TOKEN = "".join(["session", "-", "T" * 60])
_AWS_FORMATS = {
    "env": lambda name, value: f"{name.upper()}={value}",
    "json": lambda name, value: f'{{"{name}": "{value}"}}',
    "yaml": lambda name, value: f"{name}: {value}",
    "toml": lambda name, value: f'{name} = "{value}"',
}


def _secret_kinds(text: str) -> set[str]:
    findings = scan_text(text, relative_path="data/public/result.txt")
    return {f.message.split("possible ")[-1].split(" detected")[0] for f in findings if f.code == "secret"}


@pytest.mark.parametrize("placement", ["{t}", '{{"id": "{t}"}}', 'id = "{t}"', "id: {t}"],
                         ids=["standalone", "json", "toml", "yaml"])
@pytest.mark.parametrize("prefix", ["AKIA", "ASIA"], ids=["long-term", "temporary"])
def test_both_aws_access_key_prefixes_are_refused(placement, prefix) -> None:
    key_id = _aws_key_id(prefix)
    findings = scan_text(placement.format(t=key_id), relative_path="data/public/result.txt")

    assert any(f.code == "secret" and "aws-access-key" in f.message for f in findings)
    assert all(key_id not in f.message for f in findings)


@pytest.mark.parametrize("form", sorted(_AWS_FORMATS))
@pytest.mark.parametrize(
    ("name", "value"),
    [
        (_AWS_ID, _aws_key_id("ASIA")),
        (_AWS_SECRET, _SYNTHETIC_SECRET),
        (_AWS_TOKEN, _SYNTHETIC_TOKEN),
        (_AWS_LEGACY_TOKEN, _SYNTHETIC_TOKEN),
    ],
    ids=["key-id", "secret-key", "session-token", "security-token"],
)
def test_each_aws_credential_field_is_refused_on_its_own(form, name, value) -> None:
    text = _AWS_FORMATS[form](name, value)
    findings = scan_text(text, relative_path="data/public/result.txt")

    assert "aws-credential-field" in _secret_kinds(text)
    assert all(value not in f.message for f in findings)


@pytest.mark.parametrize("form", sorted(_AWS_FORMATS))
def test_a_complete_temporary_aws_credential_set_is_refused(form) -> None:
    render = _AWS_FORMATS[form]
    values = (_aws_key_id("ASIA"), _SYNTHETIC_SECRET, _SYNTHETIC_TOKEN)
    text = "\n".join(render(name, value) for name, value in zip((_AWS_ID, _AWS_SECRET, _AWS_TOKEN), values))
    findings = [f for f in scan_text(text, relative_path="data/public/result.txt") if f.code == "secret"]

    assert {f.line for f in findings} == {1, 2, 3}
    assert all(value not in f.message for f in findings for value in values)


@pytest.mark.parametrize(
    "near_miss",
    [
        _aws_key_id("ASIA", tail=15),
        _aws_key_id("ASIA", tail=17),
        "asia" + "iosfodnn7example",
        "the " + _AWS_TOKEN + " field expires; Asia and ASIA are words",
        _AWS_TOKEN + ": none",
        "my" + _AWS_TOKEN + "s = " + _SYNTHETIC_TOKEN,
    ],
    ids=["short", "long", "lowercase", "prose", "short-value", "longer-name"],
)
def test_aws_near_misses_do_not_fire(near_miss) -> None:
    assert not _secret_kinds(near_miss)


@pytest.mark.parametrize(
    "text",
    [
        '{"' + "pass" + 'word": "' + "hunter2" * 3 + '"}',
        '{"' + "api" + '_key": "' + "z" * 24 + '"}',
        "{'" + "sec" + "ret': '" + "q" * 16 + "'}",
    ],
    ids=["password", "api-key", "single-quoted"],
)
def test_json_quoted_secret_keys_are_refused(text) -> None:
    assert "assigned-secret" in _secret_kinds(text)


@pytest.mark.parametrize(
    ("canary", "kind"),
    [
        ("".join(["GOC", "SPX", "-", "q" * 28]), "google-oauth-client-secret"),
        ("".join(["hf", "_", "Q" * 34]), "huggingface-token"),
        ("".join(["12345678", "9", ":", "AA", "b" * 33]), "telegram-bot-token"),
    ],
    ids=["google-oauth", "huggingface", "telegram"],
)
def test_other_provider_credentials_are_refused(canary, kind) -> None:
    findings = scan_text(canary, relative_path="data/public/result.txt")

    assert kind in _secret_kinds(canary)
    assert all(canary not in f.message for f in findings)


@pytest.mark.parametrize(
    "near_miss",
    [
        "".join(["GOC", "SPX", "-", "q" * 10]),
        "".join(["hf", "_", "Q" * 10]),
        "".join(["12345678", "9", ":", "AB", "b" * 33]),
        "".join(["12345678", "9", ":", "AA", "b" * 32]),
        "".join(["123", ":", "AA", "b" * 33]),
    ],
    ids=["oauth-short", "hf-short", "telegram-no-AA", "telegram-short", "telegram-few-digits"],
)
def test_provider_near_misses_do_not_fire(near_miss) -> None:
    assert not _secret_kinds(near_miss)


def test_the_secret_pattern_registry_names_every_expected_family() -> None:
    from math_brain.privacy_scan import _SECRET_PATTERNS

    names = {name for name, _pattern in _SECRET_PATTERNS}
    assert {
        "private-key",
        "aws-access-key",
        "aws-credential-field",
        "google-oauth-client-secret",
        "huggingface-token",
        "telegram-bot-token",
        "github-token",
        "github-fine-grained-token",
        "provider-token",
        "google-api-key",
        "slack-token",
        "slack-webhook",
        "jwt",
        "assigned-secret",
        "bearer-token",
    } <= names


def _slack_webhook(kind: str = "services") -> str:
    return "".join(["https://hooks", ".slack", ".com/", kind, "/T0", "ABCDEFGH", "/B0", "12345678", "/", "x" * 24])


@pytest.mark.parametrize("placement", ["{t}", '{{"url": "{t}"}}', 'url = "{t}"', "url: {t}"],
                         ids=["standalone", "json", "toml", "yaml"])
@pytest.mark.parametrize("kind", ["services", "workflows", "triggers"])
def test_a_slack_webhook_url_is_refused(placement, kind) -> None:
    text = placement.format(t=_slack_webhook(kind))
    findings = scan_text(text, relative_path="data/public/result.txt")
    assert "slack-webhook" in _secret_kinds(text)
    assert all("x" * 24 not in f.message for f in findings)


@pytest.mark.parametrize(
    "near_miss",
    [
        "https://docs.slack.dev/messaging/sending-messages-using-incoming-webhooks",
        "https://api.slack.com/messaging/webhooks",
        "".join(["https://hooks", ".slack", ".com/services/"]),
        "".join(["https://hooks", ".slack", ".com/services/T0ABCDEFGH"]),
    ],
    ids=["docs", "api-docs", "bare-host", "one-segment"],
)
def test_slack_webhook_near_misses_do_not_fire(near_miss) -> None:
    assert "slack-webhook" not in _secret_kinds(near_miss)


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
    # "C:relative.txt" is drive-relative only on Windows; elsewhere the colon rule refuses it first.
    expected = {"absolute-input-path"} if os.name == "nt" else {"unsafe-path-syntax"}
    assert {finding.code for finding in drive_relative} == expected


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


# The decoded pass over JSON strings. Every canary is assembled at run time so that no literal path, category word or
# credential sits in this file.

_DECODED = "(in a JSON string, once its escapes are decoded)"


def _bs(count: int = 1) -> str:
    return chr(92) * count


def _json_unc_escaped() -> str:
    # a UNC path to server/share/notes.txt as json.dumps writes it: four backslashes before the host, two before each part
    return '{"p": "' + _bs(4) + "server" + _bs(2) + "share" + _bs(2) + 'notes.txt"}'


def _json_posix_slash_escaped() -> str:
    return '{"p": "' + _bs() + "/workspace" + _bs() + "/approved" + _bs() + '/notes.txt"}'


def _json_category_u_escaped(policy: ScanPolicy) -> str:
    word = _forbidden_category(policy)
    return '{"t": "the ' + _bs() + "u%04x" % ord(word[0]) + word[1:] + ' folder"}'


def _json_secret_u_escaped() -> str:
    return '{"t": "' + _secret_canary().replace("_", _bs() + "u005f") + '"}'


@pytest.mark.parametrize(
    ("make", "code"),
    [
        (lambda policy: _json_unc_escaped(), "absolute-path"),
        (lambda policy: _json_posix_slash_escaped(), "absolute-path"),
        (_json_category_u_escaped, "forbidden-category"),
        (lambda policy: _json_secret_u_escaped(), "secret"),
    ],
    ids=["escaped-unc", "slash-escaped-posix", "u-escaped-category", "u-escaped-secret"],
)
def test_json_strings_are_also_scanned_as_decoded(make, code) -> None:
    policy = ScanPolicy()
    text = make(policy)
    assert json.loads(text)
    as_text = scan_text(text, relative_path="data/public/result.txt", policy=policy)
    as_json = scan_text(text, relative_path="data/public/result.json", policy=policy)

    assert code not in {finding.code for finding in as_text}  # the raw pass alone misses it
    found = [finding for finding in as_json if finding.code == code]
    assert found and all(finding.message.endswith(_DECODED) for finding in found)
    assert all(text not in finding.message for finding in as_json)


def test_the_decoded_pass_never_drops_a_raw_finding() -> None:
    # an under-escaped UNC path in hand-written JSON: the share name starts with b, so the file parses and the string
    # decodes to a backspace and no path. Only the raw pass sees the path, and the file must still be refused.
    text = '{"p": "' + _bs(2) + "server" + _bs() + 'backup"}'
    assert json.loads(text)
    findings = scan_text(text, relative_path="data/public/result.json")

    assert [finding.code for finding in findings] == ["absolute-path"]
    assert not findings[0].message.endswith(_DECODED)


def test_a_path_both_passes_see_is_reported_once() -> None:
    text = json.dumps({"p": _windows_path_canary()})
    findings = scan_text(text, relative_path="data/public/result.json")

    assert [finding.code for finding in findings] == ["absolute-path"]
    assert not findings[0].message.endswith(_DECODED)


def test_the_decoded_pass_adds_nothing_to_escaped_latex() -> None:
    text = json.dumps({"t": "a" + _bs() + "qquad" + chr(10) + "b"})

    def located(path: str) -> list[tuple[str, int, int, str]]:
        return [(f.code, f.line, f.column, f.message) for f in scan_text(text, relative_path=path)]

    assert located("data/public/result.json") == located("data/public/result.txt")


def test_a_json_file_that_does_not_parse_is_scanned_raw_only() -> None:
    text = _json_unc_escaped()[:-1]  # the closing brace dropped
    findings = scan_text(text, relative_path="data/public/result.json")

    assert "absolute-path" not in {finding.code for finding in findings}


def test_json_lines_are_decoded_row_by_row_and_located() -> None:
    text = json.dumps({"n": 1}) + chr(10) + _json_unc_escaped() + chr(10)
    findings = scan_text(text, relative_path="data/public/rows.jsonl")

    assert [(finding.code, finding.line, finding.column) for finding in findings] == [("absolute-path", 2, 7)]


def test_decoded_category_words_respect_the_policy_contexts() -> None:
    policy = ScanPolicy(policy_category_contexts=frozenset({"data/public/policy.json"}))
    text = _json_category_u_escaped(policy)

    def codes(path: str) -> set[str]:
        return {finding.code for finding in scan_text(text, relative_path=path, policy=policy)}

    assert "forbidden-category" in codes("data/public/other.json")
    assert "forbidden-category" not in codes("data/public/policy.json")
