"""Render a graph node for injection or notes: label, text - assumptions intact.

A retrieval hit is only as honest as its rendering. Two claim-node shapes exist
in live stores. The legacy projection (appends before 2026-08-29) put the whole
statement in the TITLE and only the assumptions in the BODY, so any renderer
reading title-as-label and body-as-content served a conditional claim without
its conditions - observed live when the transfer-principle rows went out as
unconditional while their "(NOT available)" uniformity assumption sat in the
unrendered field. The current projection (store._default_claim_node) writes a
short title and a body of statement plus an "Assumes:" clause.

render_parts() treats both the same way: the label comes from the statement
head, and the text is the statement plus a capped Assumes tail that budget
truncation can never drop. Non-claim nodes pass through with plain truncation.

A third shape carries no marker at all: an overlay revision's node
(overlay.MathNodeRevision.to_graph_node) writes the statement and then each assumption
on a line of its own. Rendered as it stands, the assumptions are the tail of the
statement and the first thing the budget cuts. claim_view() gives such a node
the "Assumes:" form from the claim's own statement and assumptions, so it
renders like the current projection.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Sequence

from .models import GraphNode

#: a claim node whose title runs this long carries the legacy projection
LEGACY_CLAIM_TITLE_CHARS = 200

_STOP = set(
    "the a an of and or to in is are for with that this it its be by on as at from "
    "which where when then so we let can not no any all each per than".split()
)


def _is_claim(node: GraphNode) -> bool:
    metadata = node.metadata or {}
    return bool(metadata.get("claim_kind"))


#: The record fields the probability ledger and intake harvesters store in a claim's
#: assumptions: where a problem is posed, what the record refers to, its review and its status.
#: They are provenance, not hypotheses of the statement. In the store on 30 Sep 1,145 overlay
#: records carried only such lines, 2,051 carried none, and 2 carried both.
_RECORD_LINE = re.compile(
    r"^(?:Posed (?:in|as)|Refers to the|No independent review|Status as recorded"
    r"|Current status|Resolution citation|Exclusion status|Supersedes the)\b"
)


def claim_hypotheses(assumptions: Sequence[str]) -> tuple[str, ...]:
    """The hypotheses among `assumptions`: every non-empty one but the ledger's record lines."""
    return tuple(
        " ".join(a.split())
        for a in assumptions
        if a and a.strip() and not _RECORD_LINE.match(a.strip())
    )


def claim_view(node: GraphNode, statement: str, assumptions: Sequence[str]) -> GraphNode:
    """The node to render for a claim whose body lists its assumptions as bare lines.

    `statement` and `assumptions` are the claim's own fields (the claims table keeps them
    apart). When the node's body is exactly the overlay projection of that claim, the
    statement and then one assumption per line (and then the revision's conditions, which
    its node metadata lists), the view carries the body "statement Assumes: a; b" instead,
    which render_parts serves as the statement plus an Assumes tail that no budget cut
    drops. Anything else comes back unchanged: a node with neither assumptions nor
    conditions, a node in either marked shape, or a claim that does not match the body.

    Why (30 Sep 2026, 1000-page report test D10-T01): 5,690 of the store's 8,243 claim
    nodes are overlay nodes. Of the 4,163 claims with hypotheses, 3,125 delivered every one
    at the live 480-character budget with no query, and 3,763 through this view; the rest
    overflow the Assumes clause's 220-character cap, which ends in "...". On the regression
    gate's 218 bank prompts, 90 of 466 served cards lost a hypothesis whose words appear
    nowhere else on the card ("the corollary centres at a MEDIAN, not the mean", "the case
    p = 1 is false"), and 29 through the view, with the same cards chosen for every prompt.
    Those cards were labelled excerpts, but the renderer's contract is that a conditional
    claim stays conditional.

    Only hypotheses are protected. A ledger record's lines ("Posed in ...", "Status as
    recorded ...") stay after the statement, where the budget cuts first, as they always
    were: protecting them cost the open-problem pages the value their questions ask for
    (value in block 40 of 40 without the view, 6 of 40 with every line protected), and a
    record with nothing but such lines comes back unchanged.
    """
    conditions = (node.metadata or {}).get("conditions") or ()
    if isinstance(conditions, str) or not all(isinstance(c, str) for c in conditions):
        return node
    parts = [*assumptions, *conditions]
    items = [a for a in parts if a and a.strip()]
    if not items or not _is_claim(node) or len(parts) != len(items):
        return node
    body = node.body or ""
    if " Assumes: " in " ".join(body.split()) or body != "\n".join([statement, *parts]):
        return node
    hypotheses = claim_hypotheses(items)
    if not hypotheses:
        return node
    records = [" ".join(a.split()) for a in items if _RECORD_LINE.match(a.strip())]
    head = " ".join([statement, *records])
    return dataclasses.replace(node, body=head + " Assumes: " + "; ".join(hypotheses))


def _num_boost() -> float:
    """Weight of a numeric query term in the window scorer (see _window_for_query); read per call so an evaluator
    can set it in the hook's environment. Any unreadable value means the old equal weighting."""
    try:
        import os
        # default 3.0 since 2026-09-12: on the 100-item locate bank (a value only the store holds) the number weight 1 -> 2
        # -> 3 put the value in the block for 12 -> 13 -> 14 of 20 campaign questions, the other 80 unchanged at 80/80;
        # nothing else in the hook reads it, so abstention is untouched.
        return max(0.0, float(os.environ.get("MATHBRAIN_WINDOW_NUMBOOST", "3.0")))
    except Exception:
        return 3.0


#: a question that binds a parameter to a value ("at M = 4", "n = 13", "chi = 1/3"); fractions stay whole so
#: "chi = 1/3" does not read as "chi = 1" and match a different sentence
_BIND_RE = re.compile(r"\b([a-z][a-z0-9_]{0,7})\s*(?:=|equals)\s*(-?\d+(?:\.\d+)?(?:/\d+)?)\b")
#: at most this many bindings are scored, rarest in the node first (a 2,000-character proof prompt can carry dozens)
_BIND_MAX = 6
#: the interrogative cue that opens the part of a question naming what is SOUGHT; a binding after it pins where the
#: answer lives ("what value of beta ... at M = 4"), a binding before it names the note ("the m = 8 spectrum, ...")
_BIND_CUE_RE = re.compile(r"\b(what|which|how many|how much|give|find|compute|determine|state)\b")


def _bind_role() -> int:
    """How the question's interrogative cue is used (MATHBRAIN_WINDOW_BINDROLE): 0 plain, 1 tail-only, 2 tail-gated.

    Why (2026-09-12, coordinator, measured on the locate bank): the plain boost (0) fixed the item it was written for
    and broke a neighbour - one fixed, one lost, aggregate identical. In the fixed item the binding sits in the
    interrogative tail and marks the answering sentence; in the broken one it sits in the subject noun phrase and
    names the family, so the boost dragged the window to wherever the note repeats it.
    Mode 1 scored only the bindings after the cue and moved NOTHING: the fixed item had needed all three of its
    bindings, which its answering sentence restates together, and the tail binding alone is not enough weight.
    Mode 2 is the conjunction: a window is eligible for the boost only if it contains a tail binding, and an eligible
    window scores every binding the question states. The tail binding decides where, the full set decides how much.
    """
    try:
        import os
        v = os.environ.get("MATHBRAIN_WINDOW_BINDROLE", "2").strip().lower()
        if v in ("0", "", "off", "false", "plain"):
            return 0
        return 1 if v in ("1", "tail") else 2
    except Exception:
        return 2


def _bind_boost() -> float:
    """Weight of a parameter BINDING from the question ("at M = 4") in the window scorer; 0 = the old scorer.

    Landed defaulting OFF on 2026-09-12 and flipped to 3.0 in this second edit after the paired measurement held
    (mode 2: campaign value-in-block 15 -> 16 of 20, one item up and none down; fresh bank, 100 long prompts,
    absent-target fires, reach and the health check identical to the off arm on every item). The first form of
    this feature had landed defaulting on and ran unmeasured on the live path for four hours - the two-edit rule
    exists because of that.

    Why (2026-09-12, lane M2): the number weight added on 09-12 only reaches numbers the term regex keeps -
    decimals and runs of two or more digits. A locate question whose discriminating number is a single digit
    ("what value of beta makes the inequality tight at M = 4") has NO numeric term at all, so the window is
    chosen on topic words, and the head sentence - which names the campaign, the engine and the inequality -
    beats the sentence that carries the answer ("1.587 at M=2, 1.260 at M=4, EXACTLY 1.000 at M=8"). Measured
    on the L1 campaign bank: the answering window scored 0.711 against the head's 2.711. A binding matches a
    var = value pair as one unit, which is rare inside a note, so it moves the window only where the question
    actually pins a parameter. Read per call so an evaluator can set it in the hook's environment.
    """
    try:
        import os
        return max(0.0, float(os.environ.get("MATHBRAIN_WINDOW_BINDBOOST", "3.0")))
    except Exception:
        return 3.0


def _query_bindings(query: str, low: str):
    """[(regex, weight)] for the query's parameter bindings that occur in this node at all.

    The weight divides by the number of occurrences exactly as the term weights do, so a binding the node
    repeats everywhere cannot decide the window. A binding the node never states is dropped, which keeps the
    scorer's cost proportional to what can actually match.
    """
    boost = _bind_boost()
    if boost <= 0.0:
        return []
    found, seen = [], set()
    qlow = query.lower()
    role = _bind_role()
    m = _BIND_CUE_RE.search(qlow)
    # the bindings after the interrogative cue: the ones that pin WHERE the answer lives
    tail_keys = set(v + "=" + n for v, n in _BIND_RE.findall(qlow[m.start():] if m else ""))
    if role == 1:
        # no cue at all: nothing in the question names what is sought, so no binding can pin the answer
        qlow = qlow[m.start():] if m else ""
    for var, num in _BIND_RE.findall(qlow):
        key = var + "=" + num
        if var in _STOP or key in seen:
            continue
        seen.add(key)
        try:
            rx = re.compile(re.escape(var) + r"\s*=\s*" + re.escape(num) + r"(?![0-9.])")
        except re.error:
            continue
        hits = len(rx.findall(low))
        if hits:
            found.append((rx, boost / (1 + hits), key in tail_keys))
    found.sort(key=lambda item: -item[1])
    return found[:_BIND_MAX]


def _window_for_query(text: str, query: str, width: int) -> str:
    """The passage of `text` that best matches `query`, not its first `width` chars.

    Head truncation throws away the part that answers the question whenever the node
    is longer than the budget, and at the injection budget of 320 characters most
    nodes are. Measured 2026-08-29 on three retrieval-dependent banks at the LIVE
    budgets: the stored fact reached the block on 9 of 40 items under head
    truncation and 27 of 40 under this window, with the selection policy held fixed.
    Ordering was measured separately and left alone - it was worth one item in forty
    and cost campaign deposits a larger share of ordinary theorem queries.

    Scoring reads the query only, so it cannot prefer any particular answer. Terms
    are weighted by rarity within the node: counting distinct terms equally ties
    many windows and the earliest then wins, which is the head again.
    """
    if len(text) <= width:
        return text
    # Callers rely on this to TRUNCATE as well as to window. The first version returned
    # `text` untouched whenever the query was empty, which conflates "no windowing needed"
    # with "no truncation needed": render_parts(node, query="") then emitted 3142
    # characters against a 320-character budget, the whole body plus an ellipsis. Caught
    # 2026-08-30 only because a provenance hash showed my pre-change backup was taken
    # AFTER the edit, which made the control that should have caught it compare the new
    # file against itself. With no query the correct behaviour is the old one: head
    # truncation.
    if not query:
        return text[:width]
    terms = {
        w
        for w in re.findall(r"[a-z]+|\d+(?:\.\d+)+|\d{2,}", query.lower())
        # decimals stay whole: a [a-z0-9]+ split turns 10.08 into two dropped tokens,
        # and a number in the question is the most discriminating term available
        if (len(w) > 2 or any(c.isdigit() for c in w)) and w not in _STOP
    }
    if not terms:
        return text[:width]
    low = text.lower()
    # 2026-09-12: a NUMBER in the question weighs more than a word (MATHBRAIN_WINDOW_NUMBOOST, 1.0 = the old equal
    # weighting). A locate question ("at which M is the inequality tight with beta exactly 1.000") shares its words with
    # the node's opening sentence and its number only with the passage that answers it; at equal weights the head won
    # the tie and the value at character 615 never reached the block.
    _nb = _num_boost()
    weight = {t: (_nb if any(c.isdigit() for c in t) else 1.0) / (1 + low.count(t)) for t in terms}
    # 2026-09-12 (lane M2): a parameter BINDING the question states ("at M = 4") scores as one unit, see _bind_boost.
    binds = _query_bindings(query, low)
    step = max(1, width // 16)
    best, best_score = 0, -1.0
    for start in range(0, max(1, len(text) - width + 1), step):
        window = low[start : start + width]
        score = sum(weight[t] for t in terms if t in window)
        if binds:
            # mode 2: the window must contain a TAIL binding to be eligible; an eligible window scores every binding
            if _bind_role() != 2 or any(rx.search(window) for rx, w, tail in binds if tail):
                score += sum(w for rx, w, tail in binds if rx.search(window))
        # An early-match bias was tried here on 2026-08-30, on the theory that matching terms
        # are where a node restates the question and the answer follows just past them. It
        # changed nothing: 0 of 8 packets carried the stored value before and after. The cause
        # was upstream - the Assumes tail takes most of the 320-character budget and leaves the
        # statement about 100, so no window over a body that states its value at character 240
        # can reach it. Windowing cannot recover a value the budget never reaches; writing the
        # value into the first clause of the node can, and that is where the fix went.
        if score > best_score:
            best_score, best = score, start
    return ("..." if best else "") + text[best : best + width]


#: a statement that opens with one of these is stating its hypotheses first
_LEAD_HYPOTHESIS = re.compile(r"^(?:Let|Suppose|Assume|If|For|Given|Consider|Fix|Take|Whenever)\b", re.I)
_LEAD_MAX_CHARS = 140


def _lead_clause(statement: str) -> str:
    """The opening hypothesis sentence of a statement, or "" when it has none.

    Why (2026-09-11): the query window served the Rayleigh-quotient node as "...its range over all nonzero
    x is contained in the interval between the smallest and largest eigenvalues of A" and a blind judge flagged
    it as false for a general matrix - the store's statement begins "Let A be a Hermitian n by n matrix"; the
    window had cut the hypothesis off. A windowed excerpt keeps its opening hypothesis clause, capped, in front
    of the matched passage, so a conditional statement stays conditional the way the Assumes tail already does.
    """
    if not _LEAD_HYPOTHESIS.match(statement):
        return ""
    m = re.search(r"[.:;]\s", statement)
    clause = statement[: m.end()].strip() if m else statement.strip()
    if len(clause) > _LEAD_MAX_CHARS:
        cut = clause[: _LEAD_MAX_CHARS - 3]
        clause = (cut[: cut.rfind(" ")] if " " in cut else cut) + "..."
    return clause


def _window_keeping_lead(statement: str, query: str, width: int) -> str:
    """_window_for_query, but a window that starts past the head carries the lead hypothesis clause first."""
    window = _window_for_query(statement, query, width)
    if not window.startswith("..."):
        return window
    lead = _lead_clause(statement)
    if not lead or len(lead) + 8 >= width:
        return window
    inner = _window_for_query(statement, query, width - len(lead) - 5)
    if not inner.startswith("..."):
        return inner
    # a window that begins inside the lead clause would repeat it ("Let A be Hermitian. ... ian n by n
    # matrix. For any nonzero x ..."); the head already carries the lead, so serve the head instead
    start = statement.find(inner[3:])
    if 0 <= start < len(lead) + 1:
        return statement[:width]
    return lead + " ... " + inner[3:]


def render_parts(
    node: GraphNode,
    *,
    max_text_chars: int = 320,
    max_assumes_chars: int = 220,
    max_label_chars: int = 120,
    query: str = "",
) -> tuple[str, str]:
    """Return (label, text) for one node, keeping assumptions visible.

    The Assumes tail is capped at max_assumes_chars and the statement head gets
    the remaining budget (never less than 80 characters), so a conditional claim
    stays conditional at every truncation level.

    `query` selects WHICH passage of an over-long statement survives; without it
    the statement is cut from the head exactly as before. The Assumes clause is
    never a candidate for windowing - it is appended whole after the cut, so a
    conditional claim cannot lose its conditions to a well-matching window.
    """

    title = " ".join((node.title or "").split())
    body = " ".join((node.body or "").split())
    if _is_claim(node) and len(title) >= LEGACY_CLAIM_TITLE_CHARS:
        statement = title
        assumes = "; ".join(
            part.strip() for part in (node.body or "").splitlines() if part.strip()
        )
        label = statement[:max_label_chars]
        if " " in label:
            label = label[: label.rfind(" ")]
        label += "..."
    else:
        label = title
        statement, assumes = body, ""
        if _is_claim(node) and " Assumes: " in body:
            statement, assumes = body.rsplit(" Assumes: ", 1)
    text = statement
    if assumes:
        clause = "Assumes: " + assumes
        if len(clause) > max_assumes_chars:
            clause = clause[: max_assumes_chars - 3] + "..."
        budget = max(max_text_chars - len(clause) - 4, 80)
        if len(text) > budget:
            text = _window_keeping_lead(text, query, budget - 3) + "..."
        text = text + " || " + clause
    elif len(text) > max_text_chars:
        text = _window_keeping_lead(text, query, max_text_chars - 3) + "..."
    return label, text
