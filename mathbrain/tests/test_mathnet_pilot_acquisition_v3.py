from __future__ import annotations

import hashlib
import hmac
import inspect
import json
import pickle
from collections.abc import Iterator
from dataclasses import asdict, replace
from pathlib import Path

import pytest

import math_brain.mathnet_acquisition_v3 as acquisition
import math_brain.mathnet_bank_v3 as bank_module
import math_brain.mathnet_language_audit_projection_v3 as projection_module
import math_brain.mathnet_language_review_manifest_v3 as review_module
import math_brain.mathnet_pilot_material_v3 as material_module
from math_brain.mathnet_acquisition_v3 import (
    EXPECTED_MATHNET_PILOT_CANDIDATE_COUNT_V3,
    EXPECTED_MATHNET_PILOT_SCREEN_COUNTS_V3,
    EXPECTED_MATHNET_PILOT_UNIVERSE_COMMITMENTS_V3,
    EXPECTED_MATHNET_SHARDS_V3,
    MATHNET_ARROW_SCHEMA_DIGEST_V3,
    MATHNET_CONFIG_V3,
    MATHNET_PHYSICAL_LEAVES_V3,
    MATHNET_REPOSITORY_V3,
    MATHNET_REVISION_V3,
    MATHNET_ROW_ADAPTER_DIGEST_V3,
    MATHNET_SHARD_COUNT_V3,
    MATHNET_SHARD_ROWS_V3,
    MATHNET_SPLIT_V3,
    MATHNET_TOTAL_BYTES_V3,
    MATHNET_TOTAL_ROWS_V3,
    FooterAuditV3,
    MathNetAcquisitionRuntimeEvidenceV3,
    MathNetCacheModeV3,
    MathNetSourceArtifactManifestV3,
    PreparedMathNetPilotEvaluatorVaultV3,
    PreparedMathNetPilotUniverseV3,
    PreparedPinnedMathNetSourceV3,
    build_pinned_mathnet_pilot_artifacts_v3,
    verify_pinned_mathnet_pilot_artifacts_v3,
)
from math_brain.mathnet_language_audit_projection_v3 import (
    MathNetLanguageAuditItemV3,
    UnsignedMathNetAuditProjectionV3,
    build_unsigned_mathnet_language_audit_projection_v3,
    verify_unsigned_mathnet_language_audit_projection_v3,
)
from math_brain.mathnet_language_review_manifest_v3 import (
    MATHNET_LANGUAGE_REVIEW_OUTPUT_SCHEMA_V3,
    MathNetLanguageResolutionCodeV3,
    MathNetLanguageReviewerRoleV3,
    MathNetLanguageReviewResolutionReportV3,
    MathNetLanguageReviewSubmissionV3,
    MathNetLanguageReviewVerdictV3,
    MathNetReviewerASignerV3,
    MathNetReviewerBSignerV3,
    SignedMathNetLanguageReviewManifestV3,
    mathnet_language_review_output_schema_v3,
    mathnet_language_review_stdin_v3,
    parse_mathnet_language_review_stdout_v3,
    sign_replayed_mathnet_language_review_manifest_v3,
    verify_and_resolve_mathnet_language_reviews_v3,
)
from math_brain.mathnet_pilot_material_v3 import (
    MathNetPilotLanguageStateV3,
    build_synthetic_mathnet_pilot_material_v3,
)
from math_brain.models import canonical_json

VAULT_KEY = b"test vault key " * 3
WRONG_KEY = b"wrong vault key " * 3
PSEUDONYM_KEY = b"audit pseudonym material " * 2
SECOND_PSEUDONYM_KEY = b"rotated pseudonym material " * 2
PROMPT_SENTINEL = "OFFICIAL-PILOT-PROMPT-SENTINEL: compute two."
NULL_PROMPT_SENTINEL = "OFFICIAL-PILOT-NULL-PROMPT-SENTINEL: compute three."
SECOND_NULL_PROMPT_SENTINEL = "  SECOND-NULL-PROMPT-SENTINEL π: compute five.  "
GOLD_SENTINEL = "2/1"
SOLUTION_SENTINEL = r"OFFICIAL-PILOT-SOLUTION-SENTINEL \boxed{2}."
SECOND_SOLUTION_SENTINEL = r"SECOND-NULL-SOLUTION-SENTINEL \boxed{5}."
REVIEWER_A_SEED = bytes(range(32))
REVIEWER_B_SEED = bytes(reversed(range(32)))
REVIEW_RUNTIME_A = "sha256:" + hashlib.sha256(b"review-runtime-a").hexdigest()
REVIEW_RUNTIME_B = "sha256:" + hashlib.sha256(b"review-runtime-b").hexdigest()


def _manifest() -> MathNetSourceArtifactManifestV3:
    footers = tuple(
        FooterAuditV3(
            ordinal=ordinal,
            row_count=MATHNET_SHARD_ROWS_V3[ordinal],
            row_group_count=1,
            physical_leaves=MATHNET_PHYSICAL_LEAVES_V3,
            arrow_schema_digest=MATHNET_ARROW_SCHEMA_DIGEST_V3,
            created_by="parquet-cpp-arrow version 23.0.1",
        )
        for ordinal in range(MATHNET_SHARD_COUNT_V3)
    )
    return MathNetSourceArtifactManifestV3(
        repository_id=MATHNET_REPOSITORY_V3,
        revision=MATHNET_REVISION_V3,
        config_name=MATHNET_CONFIG_V3,
        split=MATHNET_SPLIT_V3,
        shards=EXPECTED_MATHNET_SHARDS_V3,
        footer_audits=footers,
        total_bytes=MATHNET_TOTAL_BYTES_V3,
        total_rows=MATHNET_TOTAL_ROWS_V3,
        unique_id_count=MATHNET_TOTAL_ROWS_V3,
        arrow_schema_digest=MATHNET_ARROW_SCHEMA_DIGEST_V3,
        row_adapter_digest=MATHNET_ROW_ADAPTER_DIGEST_V3,
    )


def _runtime(*, strong_flags: bool = False) -> MathNetAcquisitionRuntimeEvidenceV3:
    return MathNetAcquisitionRuntimeEvidenceV3(
        cache_mode=MathNetCacheModeV3.REQUIRE_PRESENT,
        remote_metadata_verified=strong_flags,
        immutable_snapshot_enforced=strong_flags,
        huggingface_hub_version="1.8.0",
        pyarrow_version="23.0.1",
    )


def _prepared(
    *,
    runtime: MathNetAcquisitionRuntimeEvidenceV3 | None = None,
) -> PreparedPinnedMathNetSourceV3:
    fingerprints = tuple(
        acquisition._LocalShardFingerprintV3(
            size_bytes=item.size_bytes,
            modified_ns=0,
            device=0,
            inode=item.ordinal,
            sha256=item.lfs_sha256,
        )
        for item in EXPECTED_MATHNET_SHARDS_V3
    )
    paths = tuple(
        Path(f"C:/private-mathnet-cache/shard-{item.ordinal:05d}.parquet")
        for item in EXPECTED_MATHNET_SHARDS_V3
    )
    return PreparedPinnedMathNetSourceV3._create(
        manifest=_manifest(),
        runtime_evidence=runtime if runtime is not None else _runtime(),
        local_paths=paths,
        fingerprints=fingerprints,
    )


def _row(
    index: int,
    *,
    language: str | None,
    problem: str,
    solutions: list[str],
    final_answer: str | None,
) -> dict[str, object]:
    return {
        "id": f"official-pilot-id-{index}",
        "problem_markdown": problem,
        "solutions_markdown": solutions,
        "images": [],
        "country": f"Country-{index}",
        "competition": f"Competition-{index}",
        "topics_flat": [],
        "language": language,
        "problem_type": "final answer only",
        "final_answer": final_answer,
    }


def _tiny_rows() -> tuple[dict[str, object], ...]:
    return (
        _row(
            0,
            language="English",
            problem=PROMPT_SENTINEL,
            solutions=[SOLUTION_SENTINEL],
            final_answer=GOLD_SENTINEL,
        ),
        _row(
            1,
            language=None,
            problem=NULL_PROMPT_SENTINEL,
            solutions=[r"Hence \boxed{3}."],
            final_answer="3",
        ),
        _row(
            2,
            language="French",
            problem="Calculez quatre.",
            solutions=[],
            final_answer=None,
        ),
    )


def _projection_rows() -> tuple[dict[str, object], ...]:
    base = _tiny_rows()
    return (
        base[0],
        base[1],
        _row(
            3,
            language=None,
            problem=SECOND_NULL_PROMPT_SENTINEL,
            solutions=[SECOND_SOLUTION_SENTINEL],
            final_answer="5",
        ),
        base[2],
    )


def _install_tiny_source(
    monkeypatch,
    *,
    rows: tuple[dict[str, object], ...],
    events: list[str],
) -> None:
    monkeypatch.setattr(bank_module, "MATHNET_HF_ROW_COUNT_V3", len(rows))
    monkeypatch.setattr(material_module, "MATHNET_HF_ROW_COUNT_V3", len(rows))

    def verify_bytes(_source: PreparedPinnedMathNetSourceV3) -> None:
        events.append("bytes")

    def iter_rows(
        _source: PreparedPinnedMathNetSourceV3,
        *,
        batch_size: int,
    ) -> Iterator[dict[str, object]]:
        assert batch_size == 17
        events.append("stream")
        yield from rows

    monkeypatch.setattr(acquisition, "_verify_prepared_bytes_v3", verify_bytes)
    monkeypatch.setattr(acquisition, "_iter_prepared_rows_v3", iter_rows)
    monkeypatch.setattr(
        acquisition,
        "_verify_expected_pinned_pilot_universe_v3",
        lambda _universe: None,
    )


def _build_tiny(
    monkeypatch,
    *,
    source: PreparedPinnedMathNetSourceV3 | None = None,
    rows: tuple[dict[str, object], ...] | None = None,
) -> tuple[
    PreparedPinnedMathNetSourceV3,
    PreparedMathNetPilotUniverseV3,
    PreparedMathNetPilotEvaluatorVaultV3,
    list[str],
]:
    events: list[str] = []
    selected_rows = rows if rows is not None else _tiny_rows()
    _install_tiny_source(monkeypatch, rows=selected_rows, events=events)
    prepared = source if source is not None else _prepared()
    universe, vault = build_pinned_mathnet_pilot_artifacts_v3(
        prepared,
        vault_key=VAULT_KEY,
        batch_size=17,
    )
    return prepared, universe, vault, events


def _build_projection(
    monkeypatch,
    *,
    source: PreparedPinnedMathNetSourceV3 | None = None,
    rows: tuple[dict[str, object], ...] | None = None,
    pseudonym_key: bytes = PSEUDONYM_KEY,
) -> tuple[
    PreparedPinnedMathNetSourceV3,
    PreparedMathNetPilotUniverseV3,
    PreparedMathNetPilotEvaluatorVaultV3,
    UnsignedMathNetAuditProjectionV3,
    list[str],
]:
    selected_rows = rows if rows is not None else _projection_rows()
    prepared, universe, vault, events = _build_tiny(
        monkeypatch,
        source=source,
        rows=selected_rows,
    )
    events.clear()
    projection = build_unsigned_mathnet_language_audit_projection_v3(
        prepared,
        universe,
        vault,
        vault_key=VAULT_KEY,
        pseudonym_key=pseudonym_key,
        batch_size=17,
    )
    return prepared, universe, vault, projection, events


def _walk(value: object) -> Iterator[tuple[str | None, object]]:
    if type(value) is dict:
        for key, nested in value.items():
            assert type(key) is str
            yield key, nested
            yield from _walk(nested)
    elif type(value) is list:
        for nested in value:
            yield None, nested
            yield from _walk(nested)
    else:
        assert type(value) in (str, int, bool, type(None))


def test_official_pilot_build_and_replay_have_exact_byte_stream_byte_order(
    monkeypatch,
) -> None:
    source, universe, vault, events = _build_tiny(monkeypatch)
    assert events == ["bytes", "stream", "bytes"]
    assert type(universe) is PreparedMathNetPilotUniverseV3
    assert type(vault) is PreparedMathNetPilotEvaluatorVaultV3
    assert universe.candidate_count == vault.entry_count == 2
    assert dict(universe.screen_counts)["source_english_eligible"] == 1
    assert dict(universe.screen_counts)["null_language_audit_queue"] == 1
    assert universe.confirmatory_source_gate is False
    assert vault.confirmatory_source_gate is False
    assert vault.bound_universe_content_digest == universe.universe_content_digest
    assert vault.bound_universe_wrapper_digest == universe.content_digest

    events.clear()
    verify_pinned_mathnet_pilot_artifacts_v3(
        source,
        universe,
        vault,
        vault_key=VAULT_KEY,
        batch_size=17,
    )
    assert events == ["bytes", "stream", "bytes"]


def test_official_wrappers_are_narrow_non_pickleable_and_publicly_gold_free(
    monkeypatch,
) -> None:
    _source, universe, vault, _events = _build_tiny(monkeypatch)
    assert not hasattr(universe, "universe")
    assert not hasattr(universe, "rows")
    assert not hasattr(vault, "vault")
    assert not hasattr(vault, "entries")
    for artifact in (universe, vault):
        with pytest.raises(TypeError):
            asdict(artifact)
        with pytest.raises(TypeError, match="non-pickleable"):
            pickle.dumps(artifact)

    universe_public = universe.public_commitment_payload()
    vault_public = vault.public_commitment_payload()
    assert universe_public["engineering_pilot_only"] is True
    assert universe_public["confirmatory_source_gate"] is False
    assert universe_public["audited_projection_handoff_authorized"] is False
    assert universe_public["selector_handoff_authorized"] is False
    assert universe_public["in_process_creation_identity_attested"] is False
    assert universe_public["preparation_code_identity_attested"] is False
    assert universe_public["external_signature_attested"] is False
    assert universe_public["wrapper_self_authenticating"] is False
    assert universe_public["local_source_replay_required_for_use"] is True
    assert universe_public["source_binding_claim"] == "official_byte_verified"
    assert vault_public["engineering_pilot_only"] is True
    assert vault_public["evaluator_only"] is True
    assert vault_public["confirmatory_source_gate"] is False
    assert vault_public["selector_handoff_authorized"] is False
    assert vault_public["in_process_creation_identity_attested"] is False
    assert vault_public["preparation_code_identity_attested"] is False
    assert vault_public["external_signature_attested"] is False
    assert vault_public["wrapper_self_authenticating"] is False
    assert vault_public["local_source_replay_required_for_use"] is True
    assert vault_public["source_binding_claim"] == "official_byte_verified"

    serialized = canonical_json(
        {"universe": universe_public, "vault": vault_public}
    )
    forbidden_keys = {
        "answer",
        "entries",
        "final_answer_text_digest",
        "gold",
        "private_content_digest",
        "private_ordered_source_rows_digest",
        "problem_text",
        "rows",
        "solution_text_digests",
        "source_row_commitment",
        "vault_key",
    }
    walked = tuple(_walk({"universe": universe_public, "vault": vault_public}))
    assert forbidden_keys.isdisjoint(key for key, _value in walked if key is not None)
    assert PROMPT_SENTINEL not in serialized
    assert NULL_PROMPT_SENTINEL not in serialized
    assert GOLD_SENTINEL not in serialized
    assert SOLUTION_SENTINEL not in serialized
    assert universe._universe.content_digest in serialized
    assert vault._vault.private_content_digest not in serialized
    assert all(entry.content_digest not in serialized for entry in vault._vault.entries)
    assert PROMPT_SENTINEL not in repr(universe)
    assert SOLUTION_SENTINEL not in repr(vault)


def test_direct_bare_synthetic_and_invalid_inputs_fail_before_byte_work(
    monkeypatch,
) -> None:
    events: list[str] = []
    rows = _tiny_rows()
    _install_tiny_source(monkeypatch, rows=rows, events=events)
    source = _prepared()
    with pytest.raises(TypeError):
        PreparedMathNetPilotUniverseV3()
    with pytest.raises(TypeError):
        PreparedMathNetPilotEvaluatorVaultV3()
    with pytest.raises(ValueError, match="acquisition replay token"):
        PreparedMathNetPilotUniverseV3._create(
            manifest=object(),  # type: ignore[arg-type]
            runtime_evidence=object(),  # type: ignore[arg-type]
            universe=object(),
            creation_guard=object(),
        )
    with pytest.raises(ValueError, match="acquisition replay token"):
        PreparedMathNetPilotEvaluatorVaultV3._create(
            manifest=object(),  # type: ignore[arg-type]
            runtime_evidence=object(),  # type: ignore[arg-type]
            vault=object(),
            bound_universe=object(),  # type: ignore[arg-type]
            creation_guard=object(),
        )
    with pytest.raises(ValueError, match="prepared source"):
        build_pinned_mathnet_pilot_artifacts_v3(
            object(),
            vault_key=VAULT_KEY,
            batch_size=17,
        )
    for bad_key in (b"x" * 31, b"x" * 65, bytearray(b"x" * 32)):
        with pytest.raises(ValueError, match="32 to 64 immutable bytes"):
            build_pinned_mathnet_pilot_artifacts_v3(
                source,
                vault_key=bad_key,  # type: ignore[arg-type]
                batch_size=17,
            )
    for bad_batch in (0, 65_537, True, 1.5):
        with pytest.raises(ValueError, match="batch size"):
            build_pinned_mathnet_pilot_artifacts_v3(
                source,
                vault_key=VAULT_KEY,
                batch_size=bad_batch,  # type: ignore[arg-type]
            )
    assert events == []

    synthetic = build_synthetic_mathnet_pilot_material_v3(rows, vault_key=VAULT_KEY)
    with pytest.raises(ValueError, match="bare universe"):
        verify_pinned_mathnet_pilot_artifacts_v3(
            source,
            synthetic.universe,  # type: ignore[arg-type]
            synthetic.vault,  # type: ignore[arg-type]
            vault_key=VAULT_KEY,
            batch_size=17,
        )
    assert events == []


def test_wrong_key_and_pair_or_runtime_mismatch_fail_before_source_work(
    monkeypatch,
) -> None:
    source, universe, vault, events = _build_tiny(monkeypatch)
    events.clear()
    with pytest.raises(ValueError, match="key differs"):
        verify_pinned_mathnet_pilot_artifacts_v3(
            source,
            universe,
            vault,
            vault_key=WRONG_KEY,
            batch_size=17,
        )
    assert events == []

    original_pair_digest = vault._bound_universe_wrapper_digest
    original_content_digest = vault._content_digest
    try:
        object.__setattr__(
            vault,
            "_bound_universe_wrapper_digest",
            "sha256:" + "0" * 64,
        )
        object.__setattr__(
            vault,
            "_content_digest",
            acquisition._digest(
                vault._payload(),
                domain=acquisition._OFFICIAL_PILOT_EVALUATOR_VAULT_DOMAIN,
            ),
        )
        vault.validate_current()
        with pytest.raises(ValueError, match="wrapper-pair binding"):
            verify_pinned_mathnet_pilot_artifacts_v3(
                source,
                universe,
                vault,
                vault_key=VAULT_KEY,
                batch_size=17,
            )
    finally:
        object.__setattr__(vault, "_bound_universe_wrapper_digest", original_pair_digest)
        object.__setattr__(vault, "_content_digest", original_content_digest)
    assert events == []
    vault.validate_current()

    changed_source = _prepared(
        runtime=replace(source.runtime_evidence, pyarrow_version="23.0.2")
    )
    with pytest.raises(ValueError, match="runtime evidence differs"):
        verify_pinned_mathnet_pilot_artifacts_v3(
            changed_source,
            universe,
            vault,
            vault_key=VAULT_KEY,
            batch_size=17,
        )
    assert events == []


def test_pre_and_post_byte_failures_never_emit_a_prepared_result(monkeypatch) -> None:
    rows = _tiny_rows()
    monkeypatch.setattr(bank_module, "MATHNET_HF_ROW_COUNT_V3", len(rows))
    monkeypatch.setattr(material_module, "MATHNET_HF_ROW_COUNT_V3", len(rows))
    source = _prepared()

    events: list[str] = []

    def fail_pre(_source: PreparedPinnedMathNetSourceV3) -> None:
        events.append("bytes")
        raise ValueError("pre-byte failure")

    def stream(_source, *, batch_size):
        del _source, batch_size
        events.append("stream")
        yield from rows

    monkeypatch.setattr(acquisition, "_verify_prepared_bytes_v3", fail_pre)
    monkeypatch.setattr(acquisition, "_iter_prepared_rows_v3", stream)
    monkeypatch.setattr(
        acquisition,
        "_verify_expected_pinned_pilot_universe_v3",
        lambda _universe: None,
    )
    with pytest.raises(ValueError, match="pre-byte failure"):
        build_pinned_mathnet_pilot_artifacts_v3(
            source,
            vault_key=VAULT_KEY,
            batch_size=17,
        )
    assert events == ["bytes"]

    events.clear()

    def fail_post(_source: PreparedPinnedMathNetSourceV3) -> None:
        events.append("bytes")
        if events.count("bytes") == 2:
            raise ValueError("post-byte failure")

    monkeypatch.setattr(acquisition, "_verify_prepared_bytes_v3", fail_post)
    with pytest.raises(ValueError, match="post-byte failure"):
        build_pinned_mathnet_pilot_artifacts_v3(
            source,
            vault_key=VAULT_KEY,
            batch_size=17,
        )
    assert events == ["bytes", "stream", "bytes"]


def test_prompt_or_gold_drift_is_rejected_by_official_replay(monkeypatch) -> None:
    source, universe, vault, events = _build_tiny(monkeypatch)
    changed_rows = list(_tiny_rows())
    changed_rows[0] = changed_rows[0] | {
        "final_answer": "5",
        "solutions_markdown": [r"Changed source says \boxed{5}."],
    }

    def changed_stream(_source, *, batch_size):
        del _source
        assert batch_size == 17
        events.append("stream")
        yield from changed_rows

    monkeypatch.setattr(acquisition, "_iter_prepared_rows_v3", changed_stream)
    events.clear()
    with pytest.raises(ValueError, match="differs from the supplied source rows"):
        verify_pinned_mathnet_pilot_artifacts_v3(
            source,
            universe,
            vault,
            vault_key=VAULT_KEY,
            batch_size=17,
        )
    assert events == ["bytes", "stream"]

    changed_rows[0] = _tiny_rows()[0] | {"problem_markdown": "changed prompt"}
    events.clear()
    with pytest.raises(ValueError, match="differs from the supplied source rows"):
        verify_pinned_mathnet_pilot_artifacts_v3(
            source,
            universe,
            vault,
            vault_key=VAULT_KEY,
            batch_size=17,
        )
    assert events == ["bytes", "stream"]


def test_protocol_v3_gate_stays_false_even_with_stronger_runtime_flags(
    monkeypatch,
) -> None:
    source = _prepared(runtime=_runtime(strong_flags=True))
    _source, universe, vault, events = _build_tiny(monkeypatch, source=source)
    assert events == ["bytes", "stream", "bytes"]
    assert source.runtime_evidence.remote_metadata_verified is True
    assert source.runtime_evidence.immutable_snapshot_enforced is True
    assert universe.confirmatory_source_gate is False
    assert vault.confirmatory_source_gate is False
    assert universe.public_commitment_payload()["confirmatory_source_gate"] is False
    assert vault.public_commitment_payload()["confirmatory_source_gate"] is False


def test_wrapper_content_digests_are_tamper_evident(monkeypatch) -> None:
    _source, universe, vault, _events = _build_tiny(monkeypatch)
    for artifact in (universe, vault):
        original = artifact._content_digest
        try:
            object.__setattr__(artifact, "_content_digest", "sha256:" + "f" * 64)
            with pytest.raises(ValueError, match="stale"):
                artifact.validate_current()
        finally:
            object.__setattr__(artifact, "_content_digest", original)
        artifact.validate_current()

    assert any(
        row.raw_language_state is MathNetPilotLanguageStateV3.NULL_AUDIT_REQUIRED
        for row in universe._universe.rows
    )


def test_runtime_evidence_tampering_never_repairs_itself() -> None:
    evidence = _runtime()
    original_digest = evidence.content_digest
    original_version = evidence.pyarrow_version
    try:
        object.__setattr__(evidence, "pyarrow_version", "tampered-version")
        for _attempt in range(2):
            with pytest.raises(ValueError, match="stale"):
                evidence.validate_current()
            assert evidence.content_digest == original_digest
    finally:
        object.__setattr__(evidence, "pyarrow_version", original_version)
    evidence.validate_current()
    assert evidence.content_digest == original_digest


def test_real_full_source_pilot_pins_cover_the_exact_v3_universe() -> None:
    assert sum(count for _code, count in EXPECTED_MATHNET_PILOT_SCREEN_COUNTS_V3) == (
        MATHNET_TOTAL_ROWS_V3
    )
    counts = dict(EXPECTED_MATHNET_PILOT_SCREEN_COUNTS_V3)
    assert counts["source_english_eligible"] == 87
    assert counts["null_language_audit_queue"] == 942
    assert (
        counts["source_english_eligible"] + counts["null_language_audit_queue"]
        == EXPECTED_MATHNET_PILOT_CANDIDATE_COUNT_V3
        == 1_029
    )
    assert tuple(key for key, _digest in EXPECTED_MATHNET_PILOT_UNIVERSE_COMMITMENTS_V3) == (
        "coordinate_universe_digest",
        "gold_free_ordered_rows_digest",
        "public_ordered_source_rows_digest",
        "universe_content_digest",
        "public_content_digest",
    )
    for _key, digest in EXPECTED_MATHNET_PILOT_UNIVERSE_COMMITMENTS_V3:
        assert digest.startswith("sha256:")
        assert len(digest) == 71


def test_language_audit_projection_is_replay_final_canonical_and_ineligible(
    monkeypatch,
) -> None:
    signature = inspect.signature(build_unsigned_mathnet_language_audit_projection_v3)
    assert tuple(signature.parameters) == (
        "source",
        "prepared_universe",
        "evaluator_vault",
        "vault_key",
        "pseudonym_key",
        "batch_size",
    )
    assert signature.parameters["vault_key"].kind is inspect.Parameter.KEYWORD_ONLY
    assert signature.parameters["pseudonym_key"].kind is inspect.Parameter.KEYWORD_ONLY

    source = _prepared(runtime=_runtime(strong_flags=True))
    _source, _universe, _vault, projection, events = _build_projection(
        monkeypatch,
        source=source,
    )
    assert events == ["bytes", "stream", "bytes"]
    assert type(projection) is UnsignedMathNetAuditProjectionV3
    assert len(projection.items) == 2
    assert {item.problem_text for item in projection.items} == {
        NULL_PROMPT_SENTINEL,
        SECOND_NULL_PROMPT_SENTINEL,
    }
    assert tuple(item.audit_item_id for item in projection.items) == tuple(
        sorted(item.audit_item_id for item in projection.items)
    )
    assert all(type(item) is MathNetLanguageAuditItemV3 for item in projection.items)
    assert all(
        set(item.to_dict()) == {"audit_item_id", "problem_text"}
        for item in projection.items
    )

    payload = projection.to_dict()
    assert set(payload) == {
        "confirmatory_source_gate",
        "content_digest",
        "engineering_pilot_only",
        "external_signature_attested",
        "in_process_creation_identity_attested",
        "items",
        "language_audit_handoff_authorized",
        "local_source_replay_required_for_use",
        "lookup_blinding_attested",
        "preparation_code_identity_attested",
        "preparation_runtime_identity_attested",
        "private_gold_present",
        "pseudonym_key_commitment",
        "pseudonym_key_freshness_attested",
        "schema",
        "schema_version",
        "selector_handoff_authorized",
        "structured_source_metadata_present",
        "wrapper_self_authenticating",
    }
    assert payload["engineering_pilot_only"] is True
    for field_name in (
        "confirmatory_source_gate",
        "external_signature_attested",
        "in_process_creation_identity_attested",
        "language_audit_handoff_authorized",
        "lookup_blinding_attested",
        "preparation_code_identity_attested",
        "preparation_runtime_identity_attested",
        "private_gold_present",
        "pseudonym_key_freshness_attested",
        "selector_handoff_authorized",
        "structured_source_metadata_present",
        "wrapper_self_authenticating",
    ):
        assert payload[field_name] is False
    assert payload["local_source_replay_required_for_use"] is True
    assert source.runtime_evidence.remote_metadata_verified is True
    assert source.runtime_evidence.immutable_snapshot_enforced is True

    encoded = projection.canonical_json()
    assert canonical_json(json.loads(encoded)) == encoded
    assert UnsignedMathNetAuditProjectionV3.from_json(encoded) == projection
    with pytest.raises(TypeError, match="canonical JSON"):
        pickle.dumps(projection)
    assert NULL_PROMPT_SENTINEL not in repr(projection)
    assert SECOND_NULL_PROMPT_SENTINEL not in repr(projection)
    public = projection.public_commitment_payload()
    assert public["item_count"] == 2
    assert "items" not in public
    assert NULL_PROMPT_SENTINEL not in canonical_json(public)


def test_language_audit_ids_match_independent_hmac_and_payload_has_no_private_fields(
    monkeypatch,
) -> None:
    _source, universe, _vault, projection, _events = _build_projection(monkeypatch)
    items_by_prompt = {item.problem_text: item.audit_item_id for item in projection.items}
    private_null_rows = tuple(
        row
        for row in universe._universe.rows
        if row.raw_language_state is MathNetPilotLanguageStateV3.NULL_AUDIT_REQUIRED
    )
    assert len(private_null_rows) == 2
    for row in private_null_rows:
        material = (
            b"math-brain/mathnet-null-language-audit/v3/item-id\x00"
            + universe.universe_content_digest.encode("ascii")
            + b"\x00"
            + row.selection_coordinate_digest.encode("ascii")
        )
        expected = "mathnet-language-audit:" + hmac.new(
            PSEUDONYM_KEY,
            material,
            hashlib.sha256,
        ).hexdigest()
        assert items_by_prompt[row.problem_text] == expected

    serialized = projection.canonical_json()
    forbidden_keys = {
        "answer",
        "case_id",
        "entries",
        "event_family_digest",
        "gold",
        "language",
        "manifest_content_digest",
        "opaque_source_digest",
        "raw_language_state",
        "runtime_evidence_digest",
        "selection_coordinate_digest",
        "solution",
        "source_binding_digest",
        "source_id",
        "source_ordinal",
        "vault",
        "vault_key",
    }
    walked = tuple(_walk(projection.to_dict()))
    assert forbidden_keys.isdisjoint(key for key, _value in walked if key is not None)
    for row in universe._universe.rows:
        assert row.case_id not in serialized
        assert row.selection_coordinate_digest not in serialized
        assert row.event_family_digest not in serialized
    for forbidden_value in (
        GOLD_SENTINEL,
        SOLUTION_SENTINEL,
        SECOND_SOLUTION_SENTINEL,
        "Country-1",
        "Competition-1",
        VAULT_KEY.decode("ascii"),
        PSEUDONYM_KEY.decode("ascii"),
    ):
        assert forbidden_value not in serialized


def test_language_audit_key_rotation_and_invalid_inputs_are_fail_closed(
    monkeypatch,
) -> None:
    source, universe, vault, projection, events = _build_projection(monkeypatch)
    events.clear()
    rotated = build_unsigned_mathnet_language_audit_projection_v3(
        source,
        universe,
        vault,
        vault_key=VAULT_KEY,
        pseudonym_key=SECOND_PSEUDONYM_KEY,
        batch_size=17,
    )
    assert events == ["bytes", "stream", "bytes"]
    assert {item.problem_text for item in rotated.items} == {
        item.problem_text for item in projection.items
    }
    assert {item.audit_item_id for item in rotated.items}.isdisjoint(
        item.audit_item_id for item in projection.items
    )
    assert rotated.pseudonym_key_commitment != projection.pseudonym_key_commitment

    invalid_calls = (
        {"pseudonym_key": VAULT_KEY},
        {"pseudonym_key": b"short"},
        {"pseudonym_key": bytearray(b"x" * 32)},
        {"vault_key": WRONG_KEY},
        {"batch_size": 0},
        {"batch_size": True},
    )
    for overrides in invalid_calls:
        events.clear()
        arguments = {
            "vault_key": VAULT_KEY,
            "pseudonym_key": PSEUDONYM_KEY,
            "batch_size": 17,
        }
        arguments.update(overrides)
        with pytest.raises(ValueError):
            build_unsigned_mathnet_language_audit_projection_v3(
                source,
                universe,
                vault,
                **arguments,
            )
        assert events == []

    events.clear()
    with pytest.raises(ValueError, match="prepared source"):
        build_unsigned_mathnet_language_audit_projection_v3(
            object(),
            universe,
            vault,
            vault_key=VAULT_KEY,
            pseudonym_key=PSEUDONYM_KEY,
            batch_size=17,
        )
    assert events == []


def test_language_audit_replay_failure_never_extracts_or_emits_prompts(monkeypatch) -> None:
    source, universe, vault, events = _build_tiny(
        monkeypatch,
        rows=_projection_rows(),
    )
    events.clear()

    def reject_replay(*args, **kwargs):
        del args, kwargs
        events.append("replay")
        raise ValueError("source replay rejected")

    def forbidden_extraction(*args, **kwargs):
        del args, kwargs
        events.append("extract")
        raise AssertionError("prompt extraction ran after failed replay")

    monkeypatch.setattr(
        projection_module,
        "verify_pinned_mathnet_pilot_artifacts_v3",
        reject_replay,
    )
    monkeypatch.setattr(
        projection_module,
        "_prepared_pilot_universe_value_v3",
        forbidden_extraction,
    )
    with pytest.raises(ValueError, match="source replay rejected"):
        build_unsigned_mathnet_language_audit_projection_v3(
            source,
            universe,
            vault,
            vault_key=VAULT_KEY,
            pseudonym_key=PSEUDONYM_KEY,
            batch_size=17,
        )
    assert events == ["replay"]


def test_language_audit_prompt_or_gold_drift_is_rejected_before_projection(
    monkeypatch,
) -> None:
    rows = _projection_rows()
    source, universe, vault, events = _build_tiny(monkeypatch, rows=rows)
    changed = list(rows)

    def changed_stream(_source, *, batch_size):
        del _source
        assert batch_size == 17
        events.append("stream")
        yield from changed

    monkeypatch.setattr(acquisition, "_iter_prepared_rows_v3", changed_stream)
    changed[1] = changed[1] | {
        "final_answer": "7",
        "solutions_markdown": [r"Changed private gold says \boxed{7}."],
    }
    events.clear()
    with pytest.raises(ValueError, match="differs from the supplied source rows"):
        build_unsigned_mathnet_language_audit_projection_v3(
            source,
            universe,
            vault,
            vault_key=VAULT_KEY,
            pseudonym_key=PSEUDONYM_KEY,
            batch_size=17,
        )
    assert events == ["bytes", "stream"]

    changed[1] = rows[1] | {"problem_markdown": "changed null prompt"}
    events.clear()
    with pytest.raises(ValueError, match="differs from the supplied source rows"):
        build_unsigned_mathnet_language_audit_projection_v3(
            source,
            universe,
            vault,
            vault_key=VAULT_KEY,
            pseudonym_key=PSEUDONYM_KEY,
            batch_size=17,
        )
    assert events == ["bytes", "stream"]


def test_language_audit_pseudonym_collision_fails_after_replay(monkeypatch) -> None:
    source, universe, vault, events = _build_tiny(
        monkeypatch,
        rows=_projection_rows(),
    )
    events.clear()
    monkeypatch.setattr(
        projection_module,
        "_audit_item_id_v3",
        lambda **_kwargs: "mathnet-language-audit:" + "a" * 64,
    )
    with pytest.raises(ValueError, match="collision"):
        build_unsigned_mathnet_language_audit_projection_v3(
            source,
            universe,
            vault,
            vault_key=VAULT_KEY,
            pseudonym_key=PSEUDONYM_KEY,
            batch_size=17,
        )
    assert events == ["bytes", "stream", "bytes"]


def test_language_audit_projection_tamper_and_noncanonical_json_are_rejected(
    monkeypatch,
) -> None:
    _source, _universe, _vault, projection, _events = _build_projection(monkeypatch)
    first = projection.items[0]
    original_prompt = first.problem_text
    try:
        object.__setattr__(first, "problem_text", "tampered prompt")
        with pytest.raises(ValueError, match="stale"):
            projection.validate_current()
    finally:
        object.__setattr__(first, "problem_text", original_prompt)
    projection.validate_current()

    original_digest = projection.content_digest
    try:
        object.__setattr__(projection, "content_digest", "sha256:" + "f" * 64)
        with pytest.raises(ValueError, match="stale"):
            projection.validate_current()
    finally:
        object.__setattr__(projection, "content_digest", original_digest)
    projection.validate_current()

    extra = json.loads(projection.canonical_json())
    extra["unexpected"] = False
    with pytest.raises(ValueError, match="unexpected fields"):
        UnsignedMathNetAuditProjectionV3.from_dict(extra)

    changed_flag = json.loads(projection.canonical_json())
    changed_flag["lookup_blinding_attested"] = True
    with pytest.raises(ValueError, match="fixed field"):
        UnsignedMathNetAuditProjectionV3.from_dict(changed_flag)

    reversed_items = json.loads(projection.canonical_json())
    reversed_items["items"].reverse()
    with pytest.raises(ValueError, match="unique and sorted"):
        UnsignedMathNetAuditProjectionV3.from_dict(reversed_items)

    changed_digest = json.loads(projection.canonical_json())
    changed_digest["content_digest"] = "sha256:" + "e" * 64
    with pytest.raises(ValueError, match="digest differs"):
        UnsignedMathNetAuditProjectionV3.from_dict(changed_digest)

    noncanonical = json.dumps(
        json.loads(projection.canonical_json()),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    with pytest.raises(ValueError, match="not canonical"):
        UnsignedMathNetAuditProjectionV3.from_json(noncanonical)

    canonical = projection.canonical_json()
    duplicate = canonical.replace(
        '{"confirmatory_source_gate":false,',
        '{"confirmatory_source_gate":false,"confirmatory_source_gate":false,',
        1,
    )
    with pytest.raises(ValueError, match="invalid"):
        UnsignedMathNetAuditProjectionV3.from_json(duplicate)

    in_memory_payload = projection.to_dict()
    exact_size = len(canonical_json(in_memory_payload).encode("utf-8"))
    monkeypatch.setattr(
        projection_module,
        "MATHNET_LANGUAGE_AUDIT_MAX_JSON_BYTES_V3",
        exact_size,
    )
    assert (
        UnsignedMathNetAuditProjectionV3.from_dict(in_memory_payload)
        == projection
    )
    monkeypatch.setattr(
        projection_module,
        "MATHNET_LANGUAGE_AUDIT_MAX_JSON_BYTES_V3",
        exact_size - 1,
    )
    with pytest.raises(ValueError, match="exceeds its byte budget"):
        UnsignedMathNetAuditProjectionV3.from_dict(in_memory_payload)


def test_language_audit_projection_is_gold_invariant_but_replay_still_binds_gold(
    monkeypatch,
) -> None:
    rows = _projection_rows()
    events: list[str] = []
    _install_tiny_source(monkeypatch, rows=rows, events=events)
    source = _prepared()
    universe_a, vault_a = build_pinned_mathnet_pilot_artifacts_v3(
        source,
        vault_key=VAULT_KEY,
        batch_size=17,
    )
    events.clear()
    projection_a = build_unsigned_mathnet_language_audit_projection_v3(
        source,
        universe_a,
        vault_a,
        vault_key=VAULT_KEY,
        pseudonym_key=PSEUDONYM_KEY,
        batch_size=17,
    )

    changed = list(rows)
    changed[1] = changed[1] | {
        "final_answer": "7",
        "solutions_markdown": [r"Changed exact gold says \boxed{7}."],
    }
    _install_tiny_source(monkeypatch, rows=tuple(changed), events=events)
    universe_b, vault_b = build_pinned_mathnet_pilot_artifacts_v3(
        source,
        vault_key=VAULT_KEY,
        batch_size=17,
    )
    events.clear()
    projection_b = build_unsigned_mathnet_language_audit_projection_v3(
        source,
        universe_b,
        vault_b,
        vault_key=VAULT_KEY,
        pseudonym_key=PSEUDONYM_KEY,
        batch_size=17,
    )
    assert events == ["bytes", "stream", "bytes"]
    assert universe_a.universe_content_digest == universe_b.universe_content_digest
    assert vault_a.content_digest != vault_b.content_digest
    assert projection_a == projection_b


def test_unsigned_language_audit_projection_verifier_replays_exactly(monkeypatch) -> None:
    source, universe, vault, projection, events = _build_projection(monkeypatch)
    events.clear()
    verify_unsigned_mathnet_language_audit_projection_v3(
        source,
        universe,
        vault,
        projection,
        vault_key=VAULT_KEY,
        pseudonym_key=PSEUDONYM_KEY,
        batch_size=17,
    )
    assert events == ["bytes", "stream", "bytes"]

    events.clear()
    with pytest.raises(ValueError, match="exact unsigned projection"):
        verify_unsigned_mathnet_language_audit_projection_v3(
            source,
            universe,
            vault,
            object(),
            vault_key=VAULT_KEY,
            pseudonym_key=PSEUDONYM_KEY,
            batch_size=17,
        )
    assert events == []

    events.clear()
    with pytest.raises(ValueError, match="pseudonym key commitment differs"):
        verify_unsigned_mathnet_language_audit_projection_v3(
            source,
            universe,
            vault,
            projection,
            vault_key=VAULT_KEY,
            pseudonym_key=SECOND_PSEUDONYM_KEY,
            batch_size=17,
        )
    assert events == []


def test_recomputed_unsigned_projection_digest_parses_but_never_confers_authority(
    monkeypatch,
) -> None:
    source, universe, vault, projection, events = _build_projection(monkeypatch)
    forged = projection.to_dict()
    forged["items"][0]["problem_text"] = "FORGED WITHOUT REPLAY"
    forged_without_digest = dict(forged)
    del forged_without_digest["content_digest"]
    forged["content_digest"] = projection_module._digest(
        forged_without_digest,
        domain=projection_module._PROJECTION_DOMAIN,
    )

    reconstructed = UnsignedMathNetAuditProjectionV3.from_json(
        canonical_json(forged)
    )
    assert type(reconstructed) is UnsignedMathNetAuditProjectionV3
    assert reconstructed != projection
    reconstructed_payload = reconstructed.to_dict()
    for field_name in (
        "confirmatory_source_gate",
        "external_signature_attested",
        "in_process_creation_identity_attested",
        "language_audit_handoff_authorized",
        "lookup_blinding_attested",
        "preparation_code_identity_attested",
        "preparation_runtime_identity_attested",
        "pseudonym_key_freshness_attested",
        "selector_handoff_authorized",
        "wrapper_self_authenticating",
    ):
        assert reconstructed_payload[field_name] is False
    assert reconstructed_payload["local_source_replay_required_for_use"] is True

    events.clear()
    with pytest.raises(ValueError, match="differs from deterministic replay"):
        verify_unsigned_mathnet_language_audit_projection_v3(
            source,
            universe,
            vault,
            reconstructed,
            vault_key=VAULT_KEY,
            pseudonym_key=PSEUDONYM_KEY,
            batch_size=17,
        )
    assert events == ["bytes", "stream", "bytes"]


def _review_submission(
    projection: UnsignedMathNetAuditProjectionV3,
    *,
    role: MathNetLanguageReviewerRoleV3,
    runtime_digest: str,
    verdicts: tuple[MathNetLanguageReviewVerdictV3, ...] | None = None,
) -> MathNetLanguageReviewSubmissionV3:
    selected = verdicts or (MathNetLanguageReviewVerdictV3.ENGLISH,) * len(
        projection.items
    )
    assert len(selected) == len(projection.items)
    raw = canonical_json(
        {
            "decisions": [
                {
                    "audit_item_id": item.audit_item_id,
                    "verdict": verdict.value,
                }
                for item, verdict in zip(projection.items, selected, strict=True)
            ],
            "schema": MATHNET_LANGUAGE_REVIEW_OUTPUT_SCHEMA_V3,
            "schema_version": 3,
        }
    ).encode("utf-8") + b"\n"
    return parse_mathnet_language_review_stdout_v3(
        raw,
        projection,
        reviewer_role=role,
        review_runtime_evidence_digest=runtime_digest,
    )


def _signed_language_reviews(monkeypatch):
    source, universe, vault, projection, events = _build_projection(monkeypatch)
    signer_a = MathNetReviewerASignerV3(REVIEWER_A_SEED)
    signer_b = MathNetReviewerBSignerV3(REVIEWER_B_SEED)
    submission_a = _review_submission(
        projection,
        role=MathNetLanguageReviewerRoleV3.REVIEWER_A,
        runtime_digest=REVIEW_RUNTIME_A,
    )
    submission_b = _review_submission(
        projection,
        role=MathNetLanguageReviewerRoleV3.REVIEWER_B,
        runtime_digest=REVIEW_RUNTIME_B,
        verdicts=(
            MathNetLanguageReviewVerdictV3.NON_ENGLISH,
            MathNetLanguageReviewVerdictV3.ENGLISH,
        ),
    )
    events.clear()
    manifest_a = sign_replayed_mathnet_language_review_manifest_v3(
        source,
        universe,
        vault,
        submission_a,
        vault_key=VAULT_KEY,
        pseudonym_key=PSEUDONYM_KEY,
        signer=signer_a,
        batch_size=17,
    )
    assert events == ["bytes", "stream", "bytes"]
    events.clear()
    manifest_b = sign_replayed_mathnet_language_review_manifest_v3(
        source,
        universe,
        vault,
        submission_b,
        vault_key=VAULT_KEY,
        pseudonym_key=PSEUDONYM_KEY,
        signer=signer_b,
        batch_size=17,
    )
    assert events == ["bytes", "stream", "bytes"]
    return (
        source,
        universe,
        vault,
        projection,
        events,
        signer_a,
        signer_b,
        manifest_a,
        manifest_b,
    )


def test_language_review_child_contract_is_minimal_exact_and_prompt_preserving(
    monkeypatch,
) -> None:
    _source, _universe, _vault, projection, _events = _build_projection(monkeypatch)
    stdin = mathnet_language_review_stdin_v3(projection)
    assert stdin.endswith(b"\n") and not stdin.endswith(b"\n\n")
    parsed_input = json.loads(stdin[:-1])
    assert set(parsed_input) == {"items", "schema", "schema_version"}
    assert parsed_input["items"] == [item.to_dict() for item in projection.items]
    assert all(set(item) == {"audit_item_id", "problem_text"} for item in parsed_input["items"])
    assert {item["problem_text"] for item in parsed_input["items"]} == {
        NULL_PROMPT_SENTINEL,
        SECOND_NULL_PROMPT_SENTINEL,
    }
    assert "reviewer_a" not in stdin.decode("utf-8")
    assert "reviewer_b" not in stdin.decode("utf-8")

    schema = mathnet_language_review_output_schema_v3(projection)
    serialized_schema = canonical_json(schema)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert NULL_PROMPT_SENTINEL not in serialized_schema
    assert SECOND_NULL_PROMPT_SENTINEL not in serialized_schema
    assert schema["properties"]["decisions"]["minItems"] == 2
    assert schema["properties"]["decisions"]["maxItems"] == 2
    assert schema["properties"]["decisions"]["items"] is False
    assert schema["properties"]["decisions"]["uniqueItems"] is True
    assert [
        item["properties"]["audit_item_id"]["const"]
        for item in schema["properties"]["decisions"]["prefixItems"]
    ] == [item.audit_item_id for item in projection.items]

    decisions = [
        {
            "audit_item_id": item.audit_item_id,
            "verdict": MathNetLanguageReviewVerdictV3.ENGLISH.value,
        }
        for item in projection.items
    ]
    stdout = (
        canonical_json(
            {
                "decisions": decisions,
                "schema": MATHNET_LANGUAGE_REVIEW_OUTPUT_SCHEMA_V3,
                "schema_version": 3,
            }
        ).encode("utf-8")
        + b"\n"
    )
    submission = parse_mathnet_language_review_stdout_v3(
        stdout,
        projection,
        reviewer_role=MathNetLanguageReviewerRoleV3.REVIEWER_A,
        review_runtime_evidence_digest=REVIEW_RUNTIME_A,
    )
    assert tuple(item.audit_item_id for item in submission.decisions) == tuple(
        item.audit_item_id for item in projection.items
    )
    assert submission.reviewer_role is MathNetLanguageReviewerRoleV3.REVIEWER_A


def test_language_review_stdout_rejects_partial_duplicate_and_noncanonical_data(
    monkeypatch,
) -> None:
    _source, _universe, _vault, projection, _events = _build_projection(monkeypatch)
    decisions = [
        {"audit_item_id": item.audit_item_id, "verdict": "english"}
        for item in projection.items
    ]

    def encoded(selected):
        return (
            canonical_json(
                {
                    "decisions": selected,
                    "schema": MATHNET_LANGUAGE_REVIEW_OUTPUT_SCHEMA_V3,
                    "schema_version": 3,
                }
            ).encode("utf-8")
            + b"\n"
        )

    invalid = (
        encoded(decisions[:1]),
        encoded(tuple(reversed(decisions))),
        encoded([decisions[0], decisions[0]]),
        encoded([decisions[0], decisions[1] | {"verdict": "probably"}]),
        encoded(decisions) + b"\n",
        encoded(decisions)[:-1] + b" \n",
    )
    for raw in invalid:
        with pytest.raises(ValueError):
            parse_mathnet_language_review_stdout_v3(
                raw,
                projection,
                reviewer_role=MathNetLanguageReviewerRoleV3.REVIEWER_A,
                review_runtime_evidence_digest=REVIEW_RUNTIME_A,
            )

    canonical = encoded(decisions)
    duplicate = canonical.replace(
        b'{"decisions":',
        b'{"decisions":[],"decisions":',
        1,
    )
    with pytest.raises(ValueError, match="invalid"):
        parse_mathnet_language_review_stdout_v3(
            duplicate,
            projection,
            reviewer_role=MathNetLanguageReviewerRoleV3.REVIEWER_A,
            review_runtime_evidence_digest=REVIEW_RUNTIME_A,
        )


def test_replayed_language_review_signing_has_no_projection_blessing_api(
    monkeypatch,
) -> None:
    assert "projection" not in inspect.signature(
        sign_replayed_mathnet_language_review_manifest_v3
    ).parameters
    source, universe, vault, projection, events = _build_projection(monkeypatch)
    submission = _review_submission(
        projection,
        role=MathNetLanguageReviewerRoleV3.REVIEWER_A,
        runtime_digest=REVIEW_RUNTIME_A,
    )
    signer = MathNetReviewerASignerV3(REVIEWER_A_SEED)
    events.clear()
    manifest = sign_replayed_mathnet_language_review_manifest_v3(
        source,
        universe,
        vault,
        submission,
        vault_key=VAULT_KEY,
        pseudonym_key=PSEUDONYM_KEY,
        signer=signer,
        batch_size=17,
    )
    assert events == ["bytes", "stream", "bytes"]
    assert type(manifest) is SignedMathNetLanguageReviewManifestV3
    assert manifest.reviewer_authority_commitment == signer.authority_commitment
    assert manifest.reviewer_key_fingerprint == signer.key_fingerprint
    assert SignedMathNetLanguageReviewManifestV3.from_json(manifest.canonical_json()) == manifest
    serialized = manifest.canonical_json()
    for forbidden in (
        NULL_PROMPT_SENTINEL,
        SECOND_NULL_PROMPT_SENTINEL,
        GOLD_SENTINEL,
        SOLUTION_SENTINEL,
        "selection_coordinate_digest",
        "source_ordinal",
    ):
        assert forbidden not in serialized
    payload = manifest.to_dict()
    for fixed_false in (
        "external_anchor_verified_in_artifact",
        "independent_peer_review_verified",
        "private_gold_present",
        "runtime_isolation_verified_in_artifact",
        "selector_handoff_authorized",
        "structured_source_metadata_present",
        "wrapper_self_authenticating",
    ):
        assert payload[fixed_false] is False
    assert payload["local_source_replay_required_for_use"] is True
    with pytest.raises(TypeError, match="canonical JSON"):
        pickle.dumps(manifest)

    wrong_role_submission = _review_submission(
        projection,
        role=MathNetLanguageReviewerRoleV3.REVIEWER_B,
        runtime_digest=REVIEW_RUNTIME_A,
    )
    events.clear()
    with pytest.raises(ValueError, match="reviewer-B signer"):
        sign_replayed_mathnet_language_review_manifest_v3(
            source,
            universe,
            vault,
            wrong_role_submission,
            vault_key=VAULT_KEY,
            pseudonym_key=PSEUDONYM_KEY,
            signer=signer,
            batch_size=17,
        )
    assert events == []


def test_dual_review_resolution_replays_verifies_and_remains_ineligible(monkeypatch) -> None:
    (
        source,
        universe,
        vault,
        projection,
        events,
        signer_a,
        signer_b,
        manifest_a,
        manifest_b,
    ) = _signed_language_reviews(monkeypatch)
    events.clear()
    report = verify_and_resolve_mathnet_language_reviews_v3(
        source,
        universe,
        vault,
        manifest_a,
        manifest_b,
        vault_key=VAULT_KEY,
        pseudonym_key=PSEUDONYM_KEY,
        reviewer_a_verifier=signer_a.verifier(),
        reviewer_b_verifier=signer_b.verifier(),
        expected_a_authority_commitment=signer_a.authority_commitment,
        expected_b_authority_commitment=signer_b.authority_commitment,
        expected_a_key_fingerprint=signer_a.key_fingerprint,
        expected_b_key_fingerprint=signer_b.key_fingerprint,
        batch_size=17,
    )
    assert events == ["bytes", "stream", "bytes"]
    assert report.item_count == len(projection.items) == 2
    assert report.agreed_english_count == 1
    assert report.agreed_non_english_count == 0
    assert report.uncertain_count == 0
    assert report.disagreement_count == 1
    assert report.unresolved_count == 1
    assert {item.resolution for item in report.items} == {
        MathNetLanguageResolutionCodeV3.AGREED_ENGLISH,
        MathNetLanguageResolutionCodeV3.DISAGREEMENT,
    }
    report_payload = report.to_dict()
    assert report_payload["selector_handoff_authorized"] is False
    assert report_payload["candidate_projection_authorized"] is False
    assert report_payload["independent_peer_review_verified"] is False
    assert report_payload["wrapper_self_authenticating"] is False
    serialized = report.canonical_json()
    assert NULL_PROMPT_SENTINEL not in serialized
    assert SECOND_NULL_PROMPT_SENTINEL not in serialized
    assert MathNetLanguageReviewResolutionReportV3.from_json(serialized) == report
    with pytest.raises(TypeError, match="canonical JSON"):
        pickle.dumps(report)


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    tuple(
        (left, right, expected)
        for left, right, expected in (
            (
                MathNetLanguageReviewVerdictV3.ENGLISH,
                MathNetLanguageReviewVerdictV3.ENGLISH,
                MathNetLanguageResolutionCodeV3.AGREED_ENGLISH,
            ),
            (
                MathNetLanguageReviewVerdictV3.NON_ENGLISH,
                MathNetLanguageReviewVerdictV3.NON_ENGLISH,
                MathNetLanguageResolutionCodeV3.AGREED_NON_ENGLISH,
            ),
            (
                MathNetLanguageReviewVerdictV3.ENGLISH,
                MathNetLanguageReviewVerdictV3.NON_ENGLISH,
                MathNetLanguageResolutionCodeV3.DISAGREEMENT,
            ),
            (
                MathNetLanguageReviewVerdictV3.NON_ENGLISH,
                MathNetLanguageReviewVerdictV3.ENGLISH,
                MathNetLanguageResolutionCodeV3.DISAGREEMENT,
            ),
            *(
                (left, right, MathNetLanguageResolutionCodeV3.UNCERTAIN)
                for left in MathNetLanguageReviewVerdictV3
                for right in MathNetLanguageReviewVerdictV3
                if MathNetLanguageReviewVerdictV3.UNCERTAIN in {left, right}
            ),
        )
    ),
)
def test_all_nine_language_review_verdict_pairs_have_frozen_resolution(
    left,
    right,
    expected,
) -> None:
    assert review_module._resolution_code_v3(left, right) is expected


def test_language_review_key_and_runtime_reuse_are_rejected(monkeypatch) -> None:
    (
        source,
        universe,
        vault,
        projection,
        events,
        signer_a,
        signer_b,
        manifest_a,
        manifest_b,
    ) = _signed_language_reviews(monkeypatch)
    same_seed_b = MathNetReviewerBSignerV3(REVIEWER_A_SEED)
    events.clear()
    with pytest.raises(ValueError, match="same underlying"):
        verify_and_resolve_mathnet_language_reviews_v3(
            source,
            universe,
            vault,
            manifest_a,
            manifest_b,
            vault_key=VAULT_KEY,
            pseudonym_key=PSEUDONYM_KEY,
            reviewer_a_verifier=signer_a.verifier(),
            reviewer_b_verifier=same_seed_b.verifier(),
            expected_a_authority_commitment=signer_a.authority_commitment,
            expected_b_authority_commitment=same_seed_b.authority_commitment,
            expected_a_key_fingerprint=signer_a.key_fingerprint,
            expected_b_key_fingerprint=same_seed_b.key_fingerprint,
            batch_size=17,
        )
    assert events == []

    reused_runtime_submission = _review_submission(
        projection,
        role=MathNetLanguageReviewerRoleV3.REVIEWER_B,
        runtime_digest=manifest_a.review_runtime_evidence_digest,
        verdicts=tuple(item.verdict for item in manifest_b.decisions),
    )
    reused_runtime_b = sign_replayed_mathnet_language_review_manifest_v3(
        source,
        universe,
        vault,
        reused_runtime_submission,
        vault_key=VAULT_KEY,
        pseudonym_key=PSEUDONYM_KEY,
        signer=signer_b,
        batch_size=17,
    )
    events.clear()
    with pytest.raises(ValueError, match="same runtime evidence"):
        verify_and_resolve_mathnet_language_reviews_v3(
            source,
            universe,
            vault,
            manifest_a,
            reused_runtime_b,
            vault_key=VAULT_KEY,
            pseudonym_key=PSEUDONYM_KEY,
            reviewer_a_verifier=signer_a.verifier(),
            reviewer_b_verifier=signer_b.verifier(),
            expected_a_authority_commitment=signer_a.authority_commitment,
            expected_b_authority_commitment=manifest_b.reviewer_authority_commitment,
            expected_a_key_fingerprint=signer_a.key_fingerprint,
            expected_b_key_fingerprint=manifest_b.reviewer_key_fingerprint,
            batch_size=17,
        )
    assert events == []


def test_attacker_self_signatures_cannot_replace_expected_review_keys(monkeypatch) -> None:
    source, universe, vault, projection, events = _build_projection(monkeypatch)
    attacker_a = MathNetReviewerASignerV3(hashlib.sha256(b"attacker-a").digest())
    attacker_b = MathNetReviewerBSignerV3(hashlib.sha256(b"attacker-b").digest())
    honest_a = MathNetReviewerASignerV3(REVIEWER_A_SEED)
    honest_b = MathNetReviewerBSignerV3(REVIEWER_B_SEED)
    submission_a = _review_submission(
        projection,
        role=MathNetLanguageReviewerRoleV3.REVIEWER_A,
        runtime_digest=REVIEW_RUNTIME_A,
    )
    submission_b = _review_submission(
        projection,
        role=MathNetLanguageReviewerRoleV3.REVIEWER_B,
        runtime_digest=REVIEW_RUNTIME_B,
    )
    forged_a = sign_replayed_mathnet_language_review_manifest_v3(
        source,
        universe,
        vault,
        submission_a,
        vault_key=VAULT_KEY,
        pseudonym_key=PSEUDONYM_KEY,
        signer=attacker_a,
        batch_size=17,
    )
    forged_b = sign_replayed_mathnet_language_review_manifest_v3(
        source,
        universe,
        vault,
        submission_b,
        vault_key=VAULT_KEY,
        pseudonym_key=PSEUDONYM_KEY,
        signer=attacker_b,
        batch_size=17,
    )
    events.clear()
    with pytest.raises(ValueError, match="commitment differs"):
        verify_and_resolve_mathnet_language_reviews_v3(
            source,
            universe,
            vault,
            forged_a,
            forged_b,
            vault_key=VAULT_KEY,
            pseudonym_key=PSEUDONYM_KEY,
            reviewer_a_verifier=attacker_a.verifier(),
            reviewer_b_verifier=attacker_b.verifier(),
            expected_a_authority_commitment=honest_a.authority_commitment,
            expected_b_authority_commitment=honest_b.authority_commitment,
            expected_a_key_fingerprint=honest_a.key_fingerprint,
            expected_b_key_fingerprint=honest_b.key_fingerprint,
            batch_size=17,
        )
    assert events == []


def test_language_review_signature_role_and_serialization_tampering_fail_closed(
    monkeypatch,
) -> None:
    (
        source,
        universe,
        vault,
        _projection,
        events,
        signer_a,
        signer_b,
        manifest_a,
        manifest_b,
    ) = _signed_language_reviews(monkeypatch)

    replacement = "0" if manifest_a.authority_signature[-1] != "0" else "1"
    bad_signature_a = replace(
        manifest_a,
        authority_signature=manifest_a.authority_signature[:-1] + replacement,
    )
    events.clear()
    with pytest.raises(ValueError, match="signature is invalid"):
        verify_and_resolve_mathnet_language_reviews_v3(
            source,
            universe,
            vault,
            bad_signature_a,
            manifest_b,
            vault_key=VAULT_KEY,
            pseudonym_key=PSEUDONYM_KEY,
            reviewer_a_verifier=signer_a.verifier(),
            reviewer_b_verifier=signer_b.verifier(),
            expected_a_authority_commitment=signer_a.authority_commitment,
            expected_b_authority_commitment=signer_b.authority_commitment,
            expected_a_key_fingerprint=signer_a.key_fingerprint,
            expected_b_key_fingerprint=signer_b.key_fingerprint,
            batch_size=17,
        )
    assert events == []

    with pytest.raises(ValueError, match="wrong role"):
        verify_and_resolve_mathnet_language_reviews_v3(
            source,
            universe,
            vault,
            manifest_b,
            manifest_a,
            vault_key=VAULT_KEY,
            pseudonym_key=PSEUDONYM_KEY,
            reviewer_a_verifier=signer_a.verifier(),
            reviewer_b_verifier=signer_b.verifier(),
            expected_a_authority_commitment=signer_a.authority_commitment,
            expected_b_authority_commitment=signer_b.authority_commitment,
            expected_a_key_fingerprint=signer_a.key_fingerprint,
            expected_b_key_fingerprint=signer_b.key_fingerprint,
            batch_size=17,
        )
    assert events == []

    serialized = manifest_a.canonical_json()
    with pytest.raises(ValueError, match="not canonical"):
        SignedMathNetLanguageReviewManifestV3.from_json(serialized + "\n")
    duplicated = serialized.replace(
        '"schema":',
        '"schema":"duplicate","schema":',
        1,
    )
    with pytest.raises(ValueError, match="invalid"):
        SignedMathNetLanguageReviewManifestV3.from_json(duplicated)


def test_partial_language_review_replays_but_never_emits_a_signed_manifest(
    monkeypatch,
) -> None:
    source, universe, vault, projection, events = _build_projection(monkeypatch)
    partial = _review_submission(
        projection,
        role=MathNetLanguageReviewerRoleV3.REVIEWER_A,
        runtime_digest=REVIEW_RUNTIME_A,
    )
    object.__setattr__(partial, "decisions", partial.decisions[:1])
    object.__setattr__(
        partial,
        "decision_universe_digest",
        review_module._decision_universe_digest_v3(partial.decisions),
    )
    events.clear()
    with pytest.raises(ValueError, match="exactly cover"):
        sign_replayed_mathnet_language_review_manifest_v3(
            source,
            universe,
            vault,
            partial,
            vault_key=VAULT_KEY,
            pseudonym_key=PSEUDONYM_KEY,
            signer=MathNetReviewerASignerV3(REVIEWER_A_SEED),
            batch_size=17,
        )
    assert events == ["bytes", "stream", "bytes"]


def test_language_review_submission_is_parser_minted_and_tamper_evident(
    monkeypatch,
) -> None:
    _source, _universe, _vault, projection, _events = _build_projection(monkeypatch)
    with pytest.raises(TypeError, match="strict parser"):
        MathNetLanguageReviewSubmissionV3()

    changed_digest = _review_submission(
        projection,
        role=MathNetLanguageReviewerRoleV3.REVIEWER_A,
        runtime_digest=REVIEW_RUNTIME_A,
    )
    object.__setattr__(changed_digest, "decision_universe_digest", "sha256:" + "0" * 64)
    with pytest.raises(ValueError, match="decision digest is stale"):
        changed_digest.validate_current()

    changed_verdict = _review_submission(
        projection,
        role=MathNetLanguageReviewerRoleV3.REVIEWER_A,
        runtime_digest=REVIEW_RUNTIME_A,
    )
    object.__setattr__(
        changed_verdict.decisions[0],
        "verdict",
        MathNetLanguageReviewVerdictV3.NON_ENGLISH,
    )
    with pytest.raises(ValueError, match="decision digest is stale"):
        changed_verdict.validate_current()

    genuine = _review_submission(
        projection,
        role=MathNetLanguageReviewerRoleV3.REVIEWER_A,
        runtime_digest=REVIEW_RUNTIME_A,
    )
    with pytest.raises(TypeError, match="process-local"):
        pickle.dumps(genuine)


def test_substituted_prompt_verdicts_cannot_be_laundered_by_honest_replay(
    monkeypatch,
) -> None:
    source, universe, vault, projection, events = _build_projection(monkeypatch)
    forged = projection.to_dict()
    forged["items"][0]["problem_text"] = "ATTACKER-SUBSTITUTED-PROMPT"
    forged_without_digest = dict(forged)
    del forged_without_digest["content_digest"]
    forged["content_digest"] = projection_module._digest(
        forged_without_digest,
        domain=projection_module._PROJECTION_DOMAIN,
    )
    altered_projection = UnsignedMathNetAuditProjectionV3.from_json(canonical_json(forged))
    assert tuple(item.audit_item_id for item in altered_projection.items) == tuple(
        item.audit_item_id for item in projection.items
    )
    assert altered_projection.content_digest != projection.content_digest
    assert (
        altered_projection.public_commitment_payload()["reviewer_payload_digest"]
        != projection.public_commitment_payload()["reviewer_payload_digest"]
    )
    altered_submission = _review_submission(
        altered_projection,
        role=MathNetLanguageReviewerRoleV3.REVIEWER_A,
        runtime_digest=REVIEW_RUNTIME_A,
    )

    events.clear()
    with pytest.raises(ValueError, match="projection content digest differs"):
        sign_replayed_mathnet_language_review_manifest_v3(
            source,
            universe,
            vault,
            altered_submission,
            vault_key=VAULT_KEY,
            pseudonym_key=PSEUDONYM_KEY,
            signer=MathNetReviewerASignerV3(REVIEWER_A_SEED),
            batch_size=17,
        )
    assert events == ["bytes", "stream", "bytes"]


def test_wrong_review_pseudonym_key_rejects_before_source_replay(monkeypatch) -> None:
    (
        source,
        universe,
        vault,
        _projection,
        events,
        signer_a,
        signer_b,
        manifest_a,
        manifest_b,
    ) = _signed_language_reviews(monkeypatch)
    events.clear()
    with pytest.raises(ValueError, match="pseudonym key commitment differs"):
        verify_and_resolve_mathnet_language_reviews_v3(
            source,
            universe,
            vault,
            manifest_a,
            manifest_b,
            vault_key=VAULT_KEY,
            pseudonym_key=SECOND_PSEUDONYM_KEY,
            reviewer_a_verifier=signer_a.verifier(),
            reviewer_b_verifier=signer_b.verifier(),
            expected_a_authority_commitment=signer_a.authority_commitment,
            expected_b_authority_commitment=signer_b.authority_commitment,
            expected_a_key_fingerprint=signer_a.key_fingerprint,
            expected_b_key_fingerprint=signer_b.key_fingerprint,
            batch_size=17,
        )
    assert events == []


def test_review_stdout_budget_covers_the_declared_maximum_queue() -> None:
    decisions = [
        {
            "audit_item_id": "mathnet-language-audit:" + f"{index:064x}",
            "verdict": MathNetLanguageReviewVerdictV3.NON_ENGLISH.value,
        }
        for index in range(projection_module.MATHNET_LANGUAGE_AUDIT_MAX_ITEMS_V3)
    ]
    encoded = canonical_json(
        {
            "decisions": decisions,
            "schema": MATHNET_LANGUAGE_REVIEW_OUTPUT_SCHEMA_V3,
            "schema_version": 3,
        }
    ).encode("utf-8") + b"\n"
    assert len(encoded) > 1024 * 1024
    assert len(encoded) <= review_module.MATHNET_LANGUAGE_REVIEW_MAX_STDOUT_BYTES_V3
