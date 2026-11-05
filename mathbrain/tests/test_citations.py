from dataclasses import replace

import pytest

from math_brain.citations import CitationCheck, CitationVerdict

DIGEST = "sha256:" + "ab" * 32


def test_citation_needs_all_entailment_dimensions() -> None:
    check = CitationCheck(
        source_id="source-1",
        source_hash=DIGEST,
        locator="Theorem 2.1",
        attributed_claim="A precise claim",
        verdict=CitationVerdict.SUPPORTS,
        assumptions_match=True,
        statement_match=True,
        authorship_match=False,
        scope_match=True,
        checked_by="reviewer",
    )
    assert not check.fully_verified


def _supported() -> CitationCheck:
    return CitationCheck(
        source_id="source-1",
        source_hash=DIGEST,
        locator="Theorem 2.1",
        attributed_claim="A precise claim",
        verdict=CitationVerdict.SUPPORTS,
        assumptions_match=True,
        statement_match=True,
        authorship_match=True,
        scope_match=True,
        checked_by="reviewer",
    )


def test_exact_booleans_decide_full_verification() -> None:
    assert _supported().fully_verified
    assert not replace(_supported(), assumptions_match=False).fully_verified


# B100-06 (interactive audit, 30 Sep 2026): the string "false" is truthy, so a flag read from text made
# fully_verified true; a record is typed when it is built.
@pytest.mark.parametrize(
    "flag", ["assumptions_match", "statement_match", "authorship_match", "scope_match"]
)
@pytest.mark.parametrize("value", ["false", "true", 0, 1, None])
def test_a_match_flag_must_be_an_exact_boolean(flag: str, value: object) -> None:
    with pytest.raises(TypeError, match="exact boolean"):
        replace(_supported(), **{flag: value})


@pytest.mark.parametrize("verdict", ["supports", "maybe", None])
def test_the_verdict_must_be_a_citation_verdict(verdict: object) -> None:
    with pytest.raises(TypeError, match="CitationVerdict"):
        replace(_supported(), verdict=verdict)


@pytest.mark.parametrize("source_hash", ["sha256:abc", "SHA256:" + "ab" * 32, "ab" * 32, "sha256:" + "AB" * 32])
def test_the_source_hash_must_be_a_sha256_digest(source_hash: str) -> None:
    with pytest.raises(ValueError, match="source hash"):
        replace(_supported(), source_hash=source_hash)


def test_a_missing_field_is_still_refused_by_name() -> None:
    with pytest.raises(ValueError, match="locator"):
        replace(_supported(), locator="  ")
    with pytest.raises(TypeError, match="checked_by"):
        replace(_supported(), checked_by=None)


# 1000-page report D09-T04: a source proving a finite-dimensional case, cited for a
# dimension-free theorem.
def _finite_dimensional_source() -> CitationCheck:
    return replace(
        _supported(),
        attributed_claim="the bound holds in every separable Hilbert space",
        locator="Theorem 3 (for R^n, constants depending on n)",
        verdict=CitationVerdict.PARTIAL,
        scope_match=False,
        notes="the source proves the finite-dimensional case only",
    )


def test_a_narrower_source_is_partial_support_and_never_fully_verified() -> None:
    check = _finite_dimensional_source()
    assert check.verdict is CitationVerdict.PARTIAL
    assert not check.fully_verified


def test_supports_is_refused_when_the_source_scope_does_not_cover_the_claim() -> None:
    with pytest.raises(ValueError, match="partial support"):
        replace(_finite_dimensional_source(), verdict=CitationVerdict.SUPPORTS)


def test_every_match_including_scope_is_needed_for_full_verification() -> None:
    assert _supported().fully_verified
    for flag in ("assumptions_match", "statement_match", "authorship_match"):
        assert not replace(_supported(), **{flag: False}).fully_verified
