from math_brain.citations import CitationCheck, CitationVerdict


def test_citation_needs_all_entailment_dimensions() -> None:
    check = CitationCheck(
        source_id="source-1",
        source_hash="sha256:abc",
        locator="Theorem 2.1",
        attributed_claim="A precise claim",
        verdict=CitationVerdict.SUPPORTS,
        assumptions_match=True,
        statement_match=True,
        authorship_match=False,
        checked_by="reviewer",
    )
    assert not check.fully_verified
