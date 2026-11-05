import pytest

from math_brain.models import EdgeKind, EdgePolarity, GraphEdge, GraphNode, NodeKind
from math_brain.retrieval import (
    AbstentionReason,
    RetrievalGuard,
    ScoreChannel,
    index_signals,
    retrieve,
    search,
    tokenize,
)


def test_unicode_tokenization() -> None:
    assert "poincaré" in tokenize("Poincaré duality")


def test_latex_and_unicode_formulae_have_the_same_alpha_normalized_signals() -> None:
    latex = index_signals(r"$\forall x \in \mathbb{R}, x^2 \geq 0$")
    unicode_notation = index_signals("$∀ y ∈ ℝ, y² ≥ 0$")
    assert latex.math_tokens == unicode_notation.math_tokens
    assert latex.formula_fingerprints == unicode_notation.formula_fingerprints


def test_formula_normalization_does_not_merge_changed_operators_or_exponents() -> None:
    square = index_signals(r"$x^2 \ge 0$").formula_fingerprints
    cube = index_signals(r"$y^3 \ge 0$").formula_fingerprints
    negative_square = index_signals(r"$z^2 \le 0$").formula_fingerprints
    assert square != cube
    assert square != negative_square


def test_latex_unicode_and_plain_variables_share_alpha_normalization() -> None:
    latex_greek = index_signals(r"$\alpha + \beta = \gamma$")
    unicode_greek = index_signals("$α + β = γ$")
    plain = index_signals("$x + y = z$")
    assert latex_greek.formula_fingerprints == unicode_greek.formula_fingerprints
    assert latex_greek.formula_fingerprints == plain.formula_fingerprints


def test_latex_and_plain_named_functions_share_formula_signals() -> None:
    latex = index_signals(r"$\sin(\theta) = 0$")
    plain = index_signals("$sin(x) = 0$")
    assert latex.formula_fingerprints == plain.formula_fingerprints


def test_formula_normalization_handles_a_very_long_numeral_without_integer_parsing() -> None:
    numeral = "0" * 5_000 + "17"
    signals = index_signals(f"$x = {numeral}$")
    assert "num:17" in signals.math_tokens


def test_formula_signal_and_trace_rank_alpha_equivalent_formula() -> None:
    formula = GraphNode(
        "formula",
        NodeKind.CLAIM,
        "Quadratic nonnegativity",
        r"For real a, $a^2 \ge 0$.",
    )
    noise = GraphNode("noise", NodeKind.CLAIM, "A qualitative lower bound")
    result = retrieve(r"$z² ≥ 0$", nodes=[noise, formula], edges=[])
    assert not result.abstained
    assert result.hits[0].node.id == "formula"
    assert result.hits[0].formula_score > 0
    assert any(item.channel is ScoreChannel.FORMULA for item in result.hits[0].trace)


def test_score_trace_exactly_explains_each_component() -> None:
    node = GraphNode("claim", NodeKind.CLAIM, "Polynomial", r"$x^2 \ge 0$")
    hit = retrieve(r"polynomial $y² ≥ 0$", nodes=[node], edges=[]).hits[0]
    channel_totals = {
        channel: sum(item.amount for item in hit.trace if item.channel is channel)
        for channel in ScoreChannel
    }
    assert channel_totals[ScoreChannel.LEXICAL] == pytest.approx(hit.lexical_score)
    assert channel_totals[ScoreChannel.SYMBOL] == pytest.approx(hit.symbol_score)
    assert channel_totals[ScoreChannel.FORMULA] == pytest.approx(hit.formula_score)
    assert channel_totals[ScoreChannel.GRAPH] == pytest.approx(hit.graph_score)
    assert channel_totals[ScoreChannel.WARNING] == pytest.approx(hit.warning_score)


def test_negative_edges_surface_as_signed_warnings() -> None:
    claim = GraphNode("claim", NodeKind.CLAIM, "Interchange limit and integral")
    warning = GraphNode(
        "warning", NodeKind.ERROR_PATTERN, "Dominated convergence assumptions fail"
    )
    edge = GraphEdge(
        "claim",
        "warning",
        EdgeKind.FAILS_UNDER,
        "domination is absent",
        id="edge_warning",
    )
    hits = search("limit integral", nodes=[claim, warning], edges=[edge])
    warning_hit = next(hit for hit in hits if hit.node.id == "warning")
    assert warning_hit.warning_score > 0
    assert warning_hit.graph_score == 0
    assert warning_hit.warnings
    assert warning_hit.warnings[0].edge_polarity is EdgePolarity.NEGATIVE
    assert warning_hit.warnings[0].signed_amount < 0


@pytest.mark.parametrize(
    ("query", "nodes", "threshold", "reason"),
    [
        ("   ", [], 0.15, AbstentionReason.NO_INDEXABLE_SIGNALS),
        (
            "topology",
            [GraphNode("algebra", NodeKind.CLAIM, "finite group")],
            0.15,
            AbstentionReason.NO_CANDIDATE_MATCH,
        ),
        (
            "group",
            [GraphNode("group", NodeKind.CLAIM, "finite group")],
            100.0,
            AbstentionReason.BELOW_SUPPORT_THRESHOLD,
        ),
    ],
)
def test_retrieval_abstains_with_an_explicit_reason(
    query: str,
    nodes: list[GraphNode],
    threshold: float,
    reason: AbstentionReason,
) -> None:
    result = retrieve(query, nodes=nodes, edges=[], min_support_score=threshold)
    assert result.abstained
    assert result.reason is reason


def test_retrieval_abstains_when_the_top_match_is_ambiguous() -> None:
    nodes = [
        GraphNode("a", NodeKind.CLAIM, "finite group"),
        GraphNode("b", NodeKind.CLAIM, "finite group"),
    ]
    result = retrieve("group", nodes=nodes, edges=[], min_margin=0.01)
    assert result.abstained
    assert result.reason is AbstentionReason.AMBIGUOUS_TOP_MATCH


def test_evaluation_guard_fails_closed_against_test_and_answer_leakage() -> None:
    safe = GraphNode(
        "safe",
        NodeKind.CLAIM,
        "Cauchy completeness reference",
        metadata={"split": "reference", "item_id": "reference-1"},
    )
    answer = GraphNode(
        "answer",
        NodeKind.EVIDENCE,
        "Cauchy completeness exact answer",
        metadata={"split": "test", "content_role": "answer_key", "item_id": "test-7"},
    )
    active_copy = GraphNode(
        "active-copy",
        NodeKind.CLAIM,
        "Cauchy completeness exact item",
        metadata={"split": "train", "item_id": "test-7"},
    )
    unlabeled = GraphNode("unlabeled", NodeKind.CLAIM, "Cauchy completeness solution")
    quarantined = GraphNode(
        "quarantined",
        NodeKind.CLAIM,
        "Cauchy completeness draft",
        metadata={"split": "train", "quarantined": "TRUE"},
    )
    malformed = GraphNode(
        "malformed",
        NodeKind.CLAIM,
        "Cauchy completeness candidate",
        metadata={"split": "train", "content_role": ["reference"]},
    )
    guard = RetrievalGuard(evaluation_split="test", active_item_id="test-7")
    result = retrieve(
        "Cauchy completeness",
        nodes=[answer, active_copy, unlabeled, quarantined, malformed, safe],
        edges=[GraphEdge("safe", "answer", EdgeKind.ANALOGOUS_TO, "possible analogy")],
        guard=guard,
    )
    assert [hit.node.id for hit in result.hits] == ["safe"]
    reasons = {item.node_id: item.reason for item in result.exclusions}
    assert reasons["answer"].startswith("blocked_content_role")
    assert reasons["active-copy"] == "active_item_leakage"
    assert reasons["unlabeled"] == "missing_split_label"
    assert reasons["quarantined"] == "quarantined_record"
    assert reasons["malformed"] == "malformed_content_role"


def test_guard_configuration_cannot_allow_the_active_split_or_answer_roles() -> None:
    with pytest.raises(ValueError, match="active evaluation split"):
        RetrievalGuard(
            evaluation_split="test",
            allowed_index_splits=("train", "test"),
        )
    with pytest.raises(ValueError, match="blocked roles must include"):
        RetrievalGuard(
            evaluation_split="test",
            blocked_roles=("answer_key",),
        )
    with pytest.raises(ValueError, match="allowed index splits"):
        RetrievalGuard(
            evaluation_split="test",
            allowed_index_splits=("train", 7),  # type: ignore[arg-type]
        )


def test_retrieval_rejects_nonfinite_abstention_thresholds() -> None:
    node = GraphNode("claim", NodeKind.CLAIM, "group")
    with pytest.raises(ValueError, match="thresholds"):
        retrieve("group", nodes=[node], edges=[], min_support_score=float("nan"))


def test_search_is_deterministic_including_scores_and_traces() -> None:
    nodes = [
        GraphNode("a", NodeKind.CLAIM, "finite group action"),
        GraphNode("b", NodeKind.CLAIM, "group representation"),
    ]
    edge = GraphEdge("a", "b", EdgeKind.ANALOGOUS_TO, "shared representation", id="e")
    first = search("group", nodes=nodes, edges=[edge])
    second = search("group", nodes=reversed(nodes), edges=reversed([edge]))
    observed_first = [
        (hit.node.id, hit.total_score, hit.trace)
        for hit in first
    ]
    observed_second = [
        (hit.node.id, hit.total_score, hit.trace)
        for hit in second
    ]
    assert observed_first == observed_second
