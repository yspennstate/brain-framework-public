"""Deterministic, explainable lexical/formula plus signed-graph retrieval."""

from __future__ import annotations

import hashlib
import math
import os
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from enum import StrEnum
from itertools import chain
from typing import Iterable

from . import semantic as _semantic
from .models import ClaimKind, EdgeKind, EdgePolarity, GraphEdge, GraphNode
from .background_hubs import BackgroundCandidate, CommunityIndex, PreparedCommunityIndex


# 2026-09-11: a minus attached to digits is part of the number. The store holds enumerated families whose members
# differ only in signed coordinates ((-2,2) against (2,2)); with the sign dropped their lexical scores tied and the
# mirror row was served for the one asked. "-\d+" is tried first so "(-2,2)" yields "-2","2" while "(2,2)" yields
# "2","2"; a hyphenated word ("self-avoiding") still matches the second alternative unchanged.
TOKEN_RE = re.compile(r"-\d+|[^\W_]+(?:['’-][^\W_]+)?", re.UNICODE)
FORMULA_RE = re.compile(
    r"\$\$(.*?)\$\$|(?<!\$)\$(?!\$)(.*?)(?<!\$)\$(?!\$)|"
    r"\\\[(.*?)\\\]|\\\((.*?)\\\)",
    re.DOTALL,
)
MATH_HINT_RE = re.compile(
    r"\\[A-Za-z]+|[=<>≤≥≠≈≡∈∉∀∃∑∏∫√∂∇∞⊂⊆⊃⊇∪∩^_]|(?:->|=>|<->|:=)"
)
MATH_TOKEN_RE = re.compile(
    r"domain_[a-z]+|\\[a-z]+|[a-z][a-z0-9]*|\d+(?:\.\d+)?|"
    r"<->|->|<=|>=|!=|:=|[+\-*/^_=<>(){}\[\],:|]"
)

UNICODE_MATH_ALIASES = str.maketrans(
    {
        "∀": r" \forall ",
        "∃": r" \exists ",
        "∈": r" \in ",
        "∉": r" \notin ",
        "≤": r" \le ",
        "≥": r" \ge ",
        "≠": r" \ne ",
        "≈": r" \approx ",
        "≡": r" \equiv ",
        "→": r" \to ",
        "⇒": r" \implies ",
        "⇔": r" \iff ",
        "∑": r" \sum ",
        "∏": r" \prod ",
        "∫": r" \int ",
        "√": r" \sqrt ",
        "∂": r" \partial ",
        "∇": r" \nabla ",
        "∞": r" \infty ",
        "⊂": r" \subset ",
        "⊆": r" \subseteq ",
        "⊃": r" \supset ",
        "⊇": r" \supseteq ",
        "∪": r" \cup ",
        "∩": r" \cap ",
        "×": " * ",
        "·": " * ",
        "−": "-",
        "ℝ": " domain_real ",
        "ℤ": " domain_integer ",
        "ℚ": " domain_rational ",
        "ℕ": " domain_natural ",
        "ℂ": " domain_complex ",
        "α": r" \alpha ",
        "β": r" \beta ",
        "γ": r" \gamma ",
        "δ": r" \delta ",
        "ε": r" \epsilon ",
        "θ": r" \theta ",
        "λ": r" \lambda ",
        "μ": r" \mu ",
        "ν": r" \nu ",
        "ρ": r" \rho ",
        "σ": r" \sigma ",
        "τ": r" \tau ",
        "φ": r" \phi ",
        "ψ": r" \psi ",
        "ω": r" \omega ",
        "⁰": "^0",
        "¹": "^1",
        "²": "^2",
        "³": "^3",
        "⁴": "^4",
        "⁵": "^5",
        "⁶": "^6",
        "⁷": "^7",
        "⁸": "^8",
        "⁹": "^9",
    }
)

COMMAND_ALIASES = {
    r"\geq": r"\ge",
    r"\leq": r"\le",
    r"\neq": r"\ne",
    r"\longrightarrow": r"\to",
    r"\rightarrow": r"\to",
    r"\Rightarrow": r"\implies",
    r"\Leftrightarrow": r"\iff",
    r"\cdot": "*",
    r"\times": "*",
}

OPERATOR_COMMANDS = frozenset(
    {
        r"\forall",
        r"\exists",
        r"\in",
        r"\notin",
        r"\le",
        r"\ge",
        r"\ne",
        r"\approx",
        r"\equiv",
        r"\to",
        r"\implies",
        r"\iff",
        r"\sum",
        r"\prod",
        r"\int",
        r"\sqrt",
        r"\partial",
        r"\nabla",
        r"\subset",
        r"\subseteq",
        r"\supset",
        r"\supseteq",
        r"\cup",
        r"\cap",
    }
)
GREEK_VARIABLES = frozenset(
    {
        r"\alpha",
        r"\beta",
        r"\gamma",
        r"\delta",
        r"\epsilon",
        r"\varepsilon",
        r"\theta",
        r"\lambda",
        r"\mu",
        r"\nu",
        r"\rho",
        r"\sigma",
        r"\tau",
        r"\phi",
        r"\varphi",
        r"\psi",
        r"\omega",
    }
)
FUNCTION_NAMES = frozenset(
    {"sin", "cos", "tan", "log", "ln", "exp", "lim", "det", "ker", "im", "rank"}
)
FUNCTION_COMMANDS = {f"\\{name}": name for name in FUNCTION_NAMES}
CONSTANT_COMMANDS = {r"\pi": "pi", r"\infty": "infinity"}
OPERATOR_ALIASES = {
    "^": "pow",
    "=": "eq",
    "<": "lt",
    ">": "gt",
    "<=": "le",
    ">=": "ge",
    "!=": "ne",
    "+": "add",
    "-": "sub",
    "*": "mul",
    "/": "div",
    "_": "subscript",
    "->": "to",
    "<->": "iff",
    ":=": "define",
    "|": "such_that",
}
PUNCTUATION = frozenset("(){}[],: ")

# ---------------------------------------------------------------------------
# Indexable-term hygiene.
#
# normalize_formula() alpha-renames every identifier it meets to v0, v1, v2 IN ORDER
# OF APPEARANCE, and _formula_segments() falls back to treating a whole untagged text
# as one formula whenever it contains so much as an "=" or an "_". Together those two
# turn an ordinary paragraph into a stream of positional placeholders, and `v3` in a
# query then "matches" `v3` in a claim purely because each is the third distinct
# identifier of its own text. That is word ORDER scored as though it were vocabulary.
# Measured on 120 real gate-passing prompts, those placeholders and the operator and
# punctuation tokens beside them were 95.9% of the symbol channel's mass and content
# words 4.1% of everything matched; the head of every result slate was owned by four
# short campaign log entries. Variable slots are therefore not indexable terms.
#
# The lexical channel has the milder version of the same problem: 66.4% of its mass
# came from one- and two-character tokens and a further 18.1% from stopwords, leaving
# 15.5% for content words. A stoplist and a minimum length are the standard answer.
# ---------------------------------------------------------------------------
VARIABLE_SLOT_RE = re.compile(r"^v\d+$")
MIN_CONTENT_TOKEN_CHARS = 3
LEXICAL_STOPWORDS = frozenset(
    """the of a an and or to in on for is are be was were it its this that these those
    with by as at from we our you your they their he she not no if then than so such
    all any each other which what when where how why can could would should may might
    must do does did have has had been being there here more most some many much very
    just also into over under about between within one two three four five six seven
    eight nine ten""".split()
)
# A claim whose body carries no statement cannot inform anybody, but it can still
# collect graph mass and occupy a slot. One node in the live store has a completely
# empty body and was measured propagating score into the claim that took slot 1.
MIN_STATEMENT_WORDS = 8


def _symbol_terms(tokens: Iterable[str]) -> tuple[str, ...]:
    return tuple(token for token in tokens if not VARIABLE_SLOT_RE.match(token))


def _content_terms(tokens: Iterable[str]) -> tuple[str, ...]:
    return tuple(
        token
        for token in tokens
        if len(token) >= MIN_CONTENT_TOKEN_CHARS and token not in LEXICAL_STOPWORDS
    )


#: PER-PROCESS MEMO OF EACH CARD'S FILTERED TERMS (F01, 12 Oct 2026; suggested by F03). retrieve() re-filtered every card's
#: tokens on every call - _content_terms and _symbol_terms over ~10,700 cards, about half of a 3-6 s whole-store call - though
#: they depend only on the card's signals. Keyed by the IndexSignals object, which _SIGNAL_MEMO keeps alive for the process,
#: and checked by identity, so a new signals object (a changed text) recomputes. Results are unchanged; a batch, an
#: evaluator or a recall with several wordings pays once. Title tokens likewise, keyed by the title text.
_TERMS_MEMO: dict[int, tuple] = {}
_TITLE_MEMO: dict[str, tuple[str, ...]] = {}


def _card_terms(signals: "IndexSignals") -> tuple[tuple[str, ...], tuple[str, ...]]:
    entry = _TERMS_MEMO.get(id(signals))
    if entry is not None and entry[0] is signals:
        return entry[1], entry[2]
    content, symbols = _content_terms(signals.lexical_tokens), _symbol_terms(signals.math_tokens)
    _TERMS_MEMO[id(signals)] = (signals, content, symbols)
    return content, symbols


def _title_tokens(title: str) -> tuple[str, ...]:
    hit = _TITLE_MEMO.get(title)
    if hit is None:
        hit = _TITLE_MEMO[title] = tokenize(title)
    return hit


# Claim kinds whose body is a statement however short: "There are infinitely many prime numbers." says something. A
# proof-attempt record under MIN_STATEMENT_WORDS words is a fragment of an attempt, whatever it contains.
_STATEMENT_KINDS = frozenset(kind.value for kind in ClaimKind if kind is not ClaimKind.PROOF_ATTEMPT)
# A stated relation between expressions, for a node that carries no claim kind: "x² ≥ 0 for every real x." is a
# statement; "author-checked, no independent read" is a note.
_RELATION_RE = re.compile(
    r"[=<>≤≥≠≈≡∈∉⊂⊆⊃⊇→⇒⇔↔≅]"
    r"|\\(?:le|leq|ge|geq|lt|gt|neq|ne|in|notin|subset|subseteq|supset|to|implies|iff|approx|equiv|sim|cong)(?![A-Za-z])"
)


def _is_degenerate(node: GraphNode) -> bool:
    """A node whose body carries no statement. An empty body always counts. A short one counts unless its claim kind
    says it is a statement or, when it has no claim kind, it states a relation between expressions (interactive audit
    B100-02, 30 Sep 2026: the word count alone put every compact statement, the two infinitude-of-primes theorems in the
    live store among them, below any wordy candidate)."""
    body = node.body or ""
    words = len(body.split())
    if words == 0:
        return True
    if words >= MIN_STATEMENT_WORDS:
        return False
    kind = (node.metadata or {}).get("claim_kind")
    if isinstance(kind, str) and kind in _STATEMENT_KINDS:
        return False
    return not (kind is None and _RELATION_RE.search(body))

EDGE_MULTIPLIER = {
    EdgeKind.DEPENDS_ON: 1.0,
    EdgeKind.DEFINES: 0.9,
    EdgeKind.PROVES: 1.0,
    EdgeKind.REFUTES: 1.25,
    EdgeKind.GENERALIZES: 0.8,
    EdgeKind.SPECIALIZES: 0.9,
    EdgeKind.ANALOGOUS_TO: 0.6,
    EdgeKind.CITES: 0.65,
    EdgeKind.VERIFIED_BY: 1.0,
    EdgeKind.FAILS_UNDER: 1.25,
    EdgeKind.TRIGGERS: 1.1,
    EdgeKind.SUPERSEDES: 0.45,
    EdgeKind.SEMANTIC_ASSOCIATION: 0.55,
    EdgeKind.BRIDGES_TO: 0.45,
}
PROOF_EDGE_KINDS = frozenset(
    {
        EdgeKind.DEPENDS_ON,
        EdgeKind.DEFINES,
        EdgeKind.PROVES,
        EdgeKind.REFUTES,
        EdgeKind.VERIFIED_BY,
        EdgeKind.FAILS_UNDER,
    }
)
MANDATORY_BLOCKED_ROLES = frozenset(
    {"answer_key", "solution", "fault_annotation", "evaluation_output"}
)


class ScoreChannel(StrEnum):
    LEXICAL = "lexical"
    SYMBOL = "symbol"
    FORMULA = "formula"
    SEMANTIC = "semantic"
    GRAPH = "graph"
    WARNING = "warning"


class RetrievalMode(StrEnum):
    """Discovery diffuses broadly; proof mode follows exact directed edges only."""

    DISCOVERY = "discovery"
    PROOF = "proof"


class AbstentionReason(StrEnum):
    NO_INDEXABLE_SIGNALS = "no_indexable_signals"
    NO_CANDIDATE_MATCH = "no_candidate_match"
    BELOW_SUPPORT_THRESHOLD = "below_support_threshold"
    AMBIGUOUS_TOP_MATCH = "ambiguous_top_match"


@dataclass(frozen=True, slots=True)
class IndexSignals:
    lexical_tokens: tuple[str, ...]
    math_tokens: tuple[str, ...]
    formula_fingerprints: tuple[str, ...]

    @property
    def empty(self) -> bool:
        return not (self.lexical_tokens or self.math_tokens or self.formula_fingerprints)


@dataclass(frozen=True, slots=True)
class ScoreTrace:
    channel: ScoreChannel
    amount: float
    detail: str
    source_node_id: str | None = None
    edge_id: str | None = None
    edge_kind: EdgeKind | None = None
    edge_polarity: EdgePolarity | None = None
    path: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.amount < 0 or not math.isfinite(self.amount):
            raise ValueError("score trace amount must be finite and nonnegative")
        if not self.detail.strip():
            raise ValueError("score trace detail is required")

    @property
    def signed_amount(self) -> float:
        if self.edge_polarity is EdgePolarity.NEGATIVE:
            return -self.amount
        return self.amount


@dataclass(frozen=True, slots=True)
class SearchHit:
    node: GraphNode
    lexical_score: float
    symbol_score: float
    formula_score: float
    semantic_score: float
    graph_score: float
    warning_score: float
    trace: tuple[ScoreTrace, ...] = ()

    @property
    def support_score(self) -> float:
        return (
            self.lexical_score
            + self.symbol_score
            + self.formula_score
            + self.semantic_score
            + self.graph_score
        )

    @property
    def total_score(self) -> float:
        return self.support_score + self.warning_score

    @property
    def warnings(self) -> tuple[ScoreTrace, ...]:
        return tuple(item for item in self.trace if item.channel is ScoreChannel.WARNING)


@dataclass(frozen=True, slots=True)
class GuardExclusion:
    node_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class RetrievalGuard:
    """Fail-closed evaluation guard against answer and test-split contamination."""

    evaluation_split: str
    allowed_index_splits: tuple[str, ...] = ("train", "reference")
    active_item_id: str | None = None
    active_pair_id: str | None = None
    active_source_digest: str | None = None
    require_split_label: bool = True
    blocked_roles: tuple[str, ...] = (
        "answer_key",
        "solution",
        "fault_annotation",
        "evaluation_output",
    )

    def __post_init__(self) -> None:
        if not isinstance(self.evaluation_split, str) or not self.evaluation_split.strip():
            raise ValueError("evaluation_split is required")
        if self.require_split_label and not self.allowed_index_splits:
            raise ValueError("a split-requiring guard needs at least one allowed index split")
        if any(
            not isinstance(item, str) or not item.strip()
            for item in self.allowed_index_splits
        ):
            raise ValueError("allowed index splits must be nonempty")
        allowed = {item.strip().casefold() for item in self.allowed_index_splits}
        if self.evaluation_split.strip().casefold() in allowed:
            raise ValueError("the active evaluation split cannot be indexed")
        if any(not isinstance(item, str) or not item.strip() for item in self.blocked_roles):
            raise ValueError("blocked roles must be nonempty strings")
        blocked = {item.strip().casefold() for item in self.blocked_roles}
        missing_roles = MANDATORY_BLOCKED_ROLES.difference(blocked)
        if missing_roles:
            raise ValueError(
                "blocked roles must include " + ", ".join(sorted(missing_roles))
            )

    def exclusion_reason(self, node: GraphNode) -> str | None:
        metadata = node.metadata
        for marker, reason in (
            ("quarantined", "quarantined_record"),
            ("is_answer_key", "answer_key_marker"),
        ):
            if marker not in metadata:
                continue
            marker_value = _metadata_flag(metadata[marker])
            if marker_value is None:
                return f"malformed_boolean_marker:{marker}"
            if marker_value:
                return reason
        privacy_value = metadata.get("privacy_status", "")
        if not isinstance(privacy_value, str):
            return "malformed_privacy_status"
        privacy_status = privacy_value.strip().casefold()
        if privacy_status in {"denied", "quarantined"}:
            return "privacy_status_not_eligible"
        role_value = metadata.get("content_role", "")
        if not isinstance(role_value, str):
            return "malformed_content_role"
        role = role_value.strip().casefold()
        if role in {item.strip().casefold() for item in self.blocked_roles}:
            return f"blocked_content_role:{role}"
        split_value = metadata.get("split")
        if split_value is None and self.require_split_label:
            return "missing_split_label"
        if split_value is not None:
            if not isinstance(split_value, str) or not split_value.strip():
                return "malformed_split_label"
            split = split_value.strip().casefold()
            allowed = {item.strip().casefold() for item in self.allowed_index_splits}
            if split not in allowed:
                return f"split_not_allowed:{split}"
        if self.active_item_id is not None:
            if str(metadata.get("item_id", "")) == self.active_item_id:
                return "active_item_leakage"
        if self.active_pair_id is not None:
            if str(metadata.get("pair_id", "")) == self.active_pair_id:
                return "paired_item_leakage"
        if self.active_source_digest is not None:
            if str(metadata.get("source_digest", "")) == self.active_source_digest:
                return "source_digest_leakage"
        return None


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    query_signals: IndexSignals
    hits: tuple[SearchHit, ...]
    abstained: bool
    reason: AbstentionReason | None
    exclusions: tuple[GuardExclusion, ...] = ()
    # Background is advisory ID-only state, never part of hits or their scores.
    background: tuple[BackgroundCandidate, ...] = ()


@dataclass(frozen=True, slots=True)
class ExactDependencyClosure:
    root_id: str
    ordered_node_ids: tuple[str, ...]
    edge_ids: tuple[str, ...]


def _metadata_flag(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"true", "yes", "1"}:
            return True
        if normalized in {"false", "no", "0"}:
            return False
    return None


# Every dash a person might type between two names, folded to the ASCII hyphen TOKEN_RE admits inside a word,
# plus the curly apostrophe.
_DASH_FOLD = {ord(c): "-" for c in "‐‑‒–—―−－"}
_DASH_FOLD[ord("’")] = "'"


def tokenize(text: str) -> tuple[str, ...]:
    # Accents and dashes are FOLDED (2026-09-12). With NFKC the accent stays composed, so "Caratheodory" written
    # with its accent and the ASCII spelling were different tokens; and a two-author name written with an en-dash
    # ("Vitali-Caratheodory") split in two where the hyphen form stayed one token. Measured on the live ranker
    # with an identical candidate set: the query "Does the Vitali-Caratheodory theorem need the measure to be
    # regular?" ranked the Vitali-Caratheodory node 1st in ASCII and 5th with the accent and en-dash - the hook
    # served the Vitali covering lemma and a non-measurable set instead. NFKD, drop the combining marks, fold
    # the dashes: both spellings now yield one token. The prefilter (math_prefilter.tokens) folds the same way;
    # the two tokenizers must agree or candidates found by one are dropped by the other. The node signal cache
    # stores tokens, so it needs rebuilding after this change (build_signal_cache.py).
    normalized = unicodedata.normalize("NFKD", text).casefold()
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch)).translate(_DASH_FOLD)
    return tuple(match.group(0) for match in TOKEN_RE.finditer(normalized))


def _formula_segments(text: str) -> tuple[str, ...]:
    delimited = tuple(
        next(group for group in match.groups() if group is not None)
        for match in FORMULA_RE.finditer(text)
    )
    if delimited:
        return delimited
    if MATH_HINT_RE.search(text):
        return (text,)
    return ()


def _normalize_number(token: str) -> str:
    if "." not in token:
        return token.lstrip("0") or "0"
    integer, fractional = token.split(".", 1)
    integer = integer.lstrip("0") or "0"
    fractional = fractional.rstrip("0")
    return integer if not fractional else f"{integer}.{fractional}"


def normalize_formula(expression: str) -> str:
    """Canonicalize notation and alpha-rename variables without evaluating it."""

    value = expression.translate(UNICODE_MATH_ALIASES)
    value = unicodedata.normalize("NFKC", value).casefold()
    value = re.sub(r"\\mathbb\s*\{\s*r\s*\}", " domain_real ", value)
    value = re.sub(r"\\mathbb\s*\{\s*z\s*\}", " domain_integer ", value)
    value = re.sub(r"\\mathbb\s*\{\s*q\s*\}", " domain_rational ", value)
    value = re.sub(r"\\mathbb\s*\{\s*n\s*\}", " domain_natural ", value)
    value = re.sub(r"\\mathbb\s*\{\s*c\s*\}", " domain_complex ", value)
    value = re.sub(r"\\(?:mathrm|mathbf|mathit)\s*\{([^{}]*)\}", r"\1", value)
    value = re.sub(r"\\operatorname\s*\{([^{}]*)\}", r"\1", value)
    value = value.replace(r"\left", "").replace(r"\right", "")
    for source, target in COMMAND_ALIASES.items():
        value = value.replace(source.casefold(), target)
    for spacing in (r"\,", r"\;", r"\!", r"\quad", r"\qquad"):
        value = value.replace(spacing, " ")

    variable_names: dict[str, str] = {}
    normalized_tokens: list[str] = []
    for raw_token in MATH_TOKEN_RE.findall(value):
        if raw_token in OPERATOR_COMMANDS:
            normalized_tokens.append(f"op:{raw_token[1:]}")
        elif raw_token in FUNCTION_COMMANDS:
            normalized_tokens.append(f"fn:{FUNCTION_COMMANDS[raw_token]}")
        elif raw_token in CONSTANT_COMMANDS:
            normalized_tokens.append(f"const:{CONSTANT_COMMANDS[raw_token]}")
        elif raw_token in GREEK_VARIABLES:
            variable_names.setdefault(raw_token, f"v{len(variable_names)}")
            normalized_tokens.append(variable_names[raw_token])
        elif raw_token.startswith("domain_"):
            normalized_tokens.append(f"const:{raw_token.removeprefix('domain_')}")
        elif raw_token.startswith("\\"):
            normalized_tokens.append(f"cmd:{raw_token[1:]}")
        elif raw_token in FUNCTION_NAMES:
            normalized_tokens.append(f"fn:{raw_token}")
        elif raw_token[0].isalpha():
            variable_names.setdefault(raw_token, f"v{len(variable_names)}")
            normalized_tokens.append(variable_names[raw_token])
        elif raw_token[0].isdigit():
            normalized_tokens.append(f"num:{_normalize_number(raw_token)}")
        elif raw_token in OPERATOR_ALIASES:
            normalized_tokens.append(f"op:{OPERATOR_ALIASES[raw_token]}")
        elif raw_token in PUNCTUATION:
            normalized_tokens.append(f"punct:{raw_token}")
    return " ".join(normalized_tokens)


#: Signals depend only on the text, so they are cacheable. WHY THIS EXISTS: retrieve()
#: indexes EVERY node on EVERY query, and profiling on 2026-08-30 put index_signals at 4.07s
#: of a 6.7s call over a 3191-node store - normalize_formula alone was 1.63s across 457,718
#: regex matches. That took the injection hook to 2.6-3.7s per prompt against its own 2.5s
#: budget, so every agent on the machine paid for it. The work is identical every time.
#:
#: Two layers, deliberately:
#:   - an in-process memo, which is what makes a BATCH (one query after another over the same
#:     store) pay the indexing cost once instead of once per query;
#:   - an optional on-disk cache, loaded READ-ONLY, for the hook, which is a fresh process per
#:     prompt and so cannot benefit from the memo. It is built by an explicit offline script,
#:     never written from here, so two sessions running at once cannot race on it.
#:
#: Keyed by a hash of the text, so a node whose body changes simply misses and recomputes.
#: A stale cache cannot return the wrong signals; it can only fail to help.
_SIGNAL_MEMO: dict[str, "IndexSignals"] = {}
_DISK_SIGNALS: dict[str, "IndexSignals"] = {}


def _signal_key(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_signal_cache(path) -> int:
    """Load a prebuilt signal cache. Returns how many entries were loaded."""
    import pickle

    try:
        with open(path, "rb") as fh:
            data = pickle.load(fh)
    except (OSError, EOFError, pickle.UnpicklingError, AttributeError):
        return 0
    if not isinstance(data, dict):
        return 0
    _DISK_SIGNALS.update(data)
    return len(data)


def _index_signals_uncached(text: str) -> IndexSignals:
    formulas = tuple(
        normalized
        for normalized in (normalize_formula(segment) for segment in _formula_segments(text))
        if normalized
    )
    math_tokens = tuple(chain.from_iterable(formula.split() for formula in formulas))
    fingerprints = tuple(
        "sha256:" + hashlib.sha256(formula.encode("utf-8")).hexdigest()
        for formula in formulas
    )
    return IndexSignals(tokenize(text), math_tokens, fingerprints)


#: DOCUMENT EXPANSION (mb10d campaign, 2026-09-24). Words a card is FOUND by without being shown: node id -> text, read
#: from <store>.expansion.pkl (written by math_brain.expansion.build at every prefilter build: the mathematical words of a
#: house card's source memory that the card itself lacks, filtered to the store's title vocabulary and to words his own
#: prompts rarely use). A
#: researcher mid-proof names the objects ("a halfspace", "its squared Walsh coefficients") where the card carries the
#: program's own names ("FEI for linear threshold functions"). Index-only: never part of a node's body, digest or
#: rendering, so the integrity check and the block are unchanged. Empty unless load_expansion() is called.
EXPANSION: dict[str, str] = {}

#: What the last load_expansion() decided: the sidecar's generation, the store's, the lag between them and whether the
#: terms were admitted, with the reason when they were not.
EXPANSION_STATUS: dict = {}

#: A sidecar lagging its store by more than this was not rebuilt with it (the prefilter and its sidecar are rebuilt within
#: 15 minutes of a store change), so its terms are refused. MATHBRAIN_EXPANSION_MAX_LAG_H sets it in hours.
try:
    EXPANSION_MAX_LAG_S = float(os.environ.get("MATHBRAIN_EXPANSION_MAX_LAG_H", "24")) * 3600.0
except ValueError:
    EXPANSION_MAX_LAG_S = 24 * 3600.0

_SIDECAR_SUFFIX = ".expansion.pkl"


def _store_generation(sidecar_path) -> str | None:
    """The newest creation stamp in the store the sidecar sits beside (<store>.expansion.pkl), or None when there is no
    such store or it cannot be read."""
    import sqlite3

    path = str(sidecar_path)
    if not path.endswith(_SIDECAR_SUFFIX):
        return None
    store = path[: -len(_SIDECAR_SUFFIX)]
    if not os.path.isfile(store):
        return None
    try:
        db = sqlite3.connect("file:%s?mode=ro" % store.replace("\\", "/"), uri=True)
        try:
            row = db.execute("SELECT max(created_at) FROM nodes").fetchone()
        finally:
            db.close()
    except sqlite3.Error:
        return None
    return str(row[0]) if row and row[0] else None


def _stamp_seconds(stamp: str) -> float | None:
    from datetime import datetime, timezone

    try:
        moment = datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.timestamp()


def load_expansion(path, store_generation: str | None = None) -> int:
    """Load a document-expansion sidecar. Returns how many cards it expands (0 when absent, unreadable or refused).

    The sidecar records the store generation it was built from (the newest creation stamp among the current nodes), and
    its terms reach ranking only while that generation belongs to the store in use. A sidecar newer than the store's
    newest node was built from a store this one never was (a rollback, or another store's file), and one older than the
    store by more than EXPANSION_MAX_LAG_S was not rebuilt with it; both are refused, any terms an earlier load admitted
    are dropped, and EXPANSION_STATUS says why (1000-page report D10-T05, 30 Sep 2026: nothing compared the two, so a
    stale sidecar's terms reached ranking). store_generation defaults to the newest created_at in the store beside the
    sidecar; a sidecar written before generations were recorded is admitted as unchecked."""
    import pickle

    try:
        with open(path, "rb") as fh:
            data = pickle.load(fh)
    except (OSError, EOFError, pickle.UnpicklingError, AttributeError):
        return 0
    expansion = data.get("expansion") if isinstance(data, dict) else None
    if not isinstance(expansion, dict):
        return 0
    generation = str(data.get("generation") or "") or None
    if store_generation is None:
        store_generation = _store_generation(path)
    status = {"path": str(path), "generation": generation, "store_generation": store_generation,
              "lag_s": None, "admitted": True, "reason": None}
    if generation is None or store_generation is None:
        status["reason"] = "unchecked: the sidecar records no generation" if generation is None else \
            "unchecked: no readable store beside the sidecar"
    else:
        built, current = _stamp_seconds(generation), _stamp_seconds(store_generation)
        lag = None if built is None or current is None else current - built
        status["lag_s"] = lag
        if lag is None:
            if generation != store_generation:
                status["admitted"], status["reason"] = False, "unreadable generation stamp"
        elif lag < 0:
            status["admitted"], status["reason"] = False, "the sidecar is newer than the store"
        elif lag > EXPANSION_MAX_LAG_S:
            status["admitted"], status["reason"] = False, "the sidecar lags the store by more than the limit"
    EXPANSION_STATUS.clear()
    EXPANSION_STATUS.update(status)
    EXPANSION.clear()
    if not status["admitted"]:
        return 0
    EXPANSION.update({str(k): str(v) for k, v in expansion.items() if v})
    return len(EXPANSION)


#: EXPANSION FIELD (F01, 12 Oct 2026; for F02's validated rewordings). Phrasings a mathematician would type for a card,
#: indexed as a SEPARATE lexical field with its own document frequencies and its own weight (retrieve(expansion_weight=)),
#: never folded into the card's own words: measured by F02 on 12 Oct, appending rewordings to the cards' text lifted their
#: own questions but pushed cards without rewordings down (br50 top 8: 36 -> 33 of 42). Empty, and inert, unless
#: load_expansion_field() is called; the weight defaults to 0. Index-only, like EXPANSION: never shown, never in a digest.
EXPANSION_FIELD: dict[str, str] = {}
_EXPANSION_FIELD_TERMS: dict[str, tuple[str, ...]] = {}


def load_expansion_field(path) -> int:
    """Load {"card_id": ..., "text": ...} JSON lines (several lines per card are joined). Returns the cards loaded; 0 when
    the file is absent or unreadable, which leaves the field empty."""
    import json

    loaded: dict[str, list[str]] = {}
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(row, dict):
                    continue
                card, text = row.get("card_id"), row.get("text")
                if isinstance(card, str) and isinstance(text, str) and text.strip():
                    loaded.setdefault(card, []).append(" ".join(text.split()))
    except OSError:
        return 0
    EXPANSION_FIELD.clear()
    _EXPANSION_FIELD_TERMS.clear()
    EXPANSION_FIELD.update({card: " ".join(texts) for card, texts in loaded.items()})
    return len(EXPANSION_FIELD)


def _expansion_field_terms(node_id: str) -> tuple[str, ...]:
    hit = _EXPANSION_FIELD_TERMS.get(node_id)
    if hit is None:
        text = EXPANSION_FIELD.get(node_id, "")
        hit = _EXPANSION_FIELD_TERMS[node_id] = _content_terms(tokenize(text)) if text else ()
    return hit


def index_text(node) -> str:
    """The text a card is indexed and matched by: title, body and its expansion words when a sidecar is loaded."""
    extra = EXPANSION.get(node.id)
    return (node.title or "") + " " + (node.body or "") + (" " + extra if extra else "")


def index_signals(text: str) -> IndexSignals:
    key = _signal_key(text)
    hit = _SIGNAL_MEMO.get(key)
    if hit is not None:
        return hit
    hit = _DISK_SIGNALS.get(key)
    if hit is None:
        hit = _index_signals_uncached(text)
    _SIGNAL_MEMO[key] = hit
    return hit


#: KEYED SIGNAL SIDECAR (mb10d campaign, 2026-09-24). The 18 MB brain_signal_cache.pkl is loaded only when the hook has no
#: prefilter, so on the ordinary path every prompt recomputed the signals of its ~300 pooled candidates: 0.74 s of 2.16 s
#: CPU on a profiled research prompt, and a BelowNormal hook behind a hundred agent processes pays that several times
#: over in wall time (the 24 Sep field test saw 89-151 s calls). <store>.signals.sqlite holds the same signals keyed by
#: FINGERPRINT:sha256(text), and retrieve() reads only its candidates' rows, in one query. The fingerprint is a hash of
#: this file, because the entries are TOKENS: a tokenizer change must miss, never hand back another tokenizer's tokens.
#: math_prefilter.build fills it incrementally at every index build (rows under an old fingerprint are dropped there);
#: readers open it read-only. Absent, unreadable or stale, it can only fail to help. MATHBRAIN_SIGNAL_DB=0 switches it off.
_SIGNAL_DB = None
_SIGNAL_FP = None


def signal_fingerprint() -> str:
    global _SIGNAL_FP
    if _SIGNAL_FP is None:
        try:
            with open(__file__, "rb") as fh:
                _SIGNAL_FP = hashlib.sha256(fh.read()).hexdigest()[:16]
        except OSError:
            _SIGNAL_FP = "nofile"
    return _SIGNAL_FP


def signal_db_path(store_path) -> str:
    return str(store_path) + ".signals.sqlite"


def load_signal_db(path) -> bool:
    """Open a keyed signal sidecar read-only for prefetch_signals. False when absent, unreadable or switched off."""
    global _SIGNAL_DB
    if os.environ.get("MATHBRAIN_SIGNAL_DB", "1").strip() in ("0", "off", "false"):
        return False
    try:
        if not os.path.exists(path):
            return False
        import sqlite3
        _SIGNAL_DB = sqlite3.connect("file:%s?mode=ro" % str(path).replace(os.sep, "/"), uri=True)
        return True
    except Exception:
        _SIGNAL_DB = None
        return False


def prefetch_signals(texts) -> int:
    """Fill the in-process memo from the sidecar for the given texts, in one query per 500 keys. Returns rows read."""
    if _SIGNAL_DB is None:
        return 0
    import pickle
    fp = signal_fingerprint()
    want = {}
    for text in texts:
        key = _signal_key(text)
        if key not in _SIGNAL_MEMO and key not in _DISK_SIGNALS:
            want[fp + ":" + key] = key
    got = 0
    keys = list(want)
    try:
        for i in range(0, len(keys), 500):
            chunk = keys[i:i + 500]
            rows = _SIGNAL_DB.execute("select k, v from sig where k in (%s)" % ",".join("?" * len(chunk)), chunk)
            for k, v in rows:
                try:
                    sig = pickle.loads(v)
                except Exception:
                    continue
                if isinstance(sig, IndexSignals):
                    _SIGNAL_MEMO[want[k]] = sig
                    got += 1
    except Exception:
        return got
    return got


def update_signal_db(path, texts) -> dict:
    """Add the signals of every text the sidecar lacks under this file's fingerprint; drop rows of other fingerprints.
    Incremental (a rebuild after one ingest computes one card), WAL so a reading hook never blocks it."""
    import pickle
    import sqlite3
    import time as _time
    t0 = _time.time()
    fp = signal_fingerprint()
    con = sqlite3.connect(str(path), timeout=30)
    try:
        con.execute("pragma journal_mode=wal")
        con.execute("create table if not exists sig (k text primary key, v blob not null)")
        dropped = con.execute("delete from sig where substr(k, 1, %d) != ?" % (len(fp) + 1), (fp + ":",)).rowcount
        have = {r[0] for r in con.execute("select k from sig")}
        add, seen = [], set()
        for text in texts:
            if not text or not text.strip():
                continue
            k = fp + ":" + _signal_key(text)
            if k in have or k in seen:
                continue
            seen.add(k)
            # index_signals, not the uncached form: the prefilter build has just computed these for its IDF table, so
            # the memo already holds them (a builder process never loads the on-disk pickle, whose tokenizer may differ)
            add.append((k, pickle.dumps(index_signals(text), protocol=pickle.HIGHEST_PROTOCOL)))
        con.executemany("insert or ignore into sig (k, v) values (?, ?)", add)
        con.commit()
        total = con.execute("select count(*) from sig").fetchone()[0]
    finally:
        con.close()
    return {"path": str(path), "fingerprint": fp, "added": len(add), "dropped": dropped, "rows": total,
            "seconds": round(_time.time() - t0, 1)}


#: How a query term's own repetition counts. The document side has always been saturated
#: (1 + log1p(tf)) and length-normalised; the query side was linear, so a long audit brief that
#: says "set" fifteen times paid any node that says "set" once fifteen times over. Measured
#: 2026-09-03 on the probe bank through the hook path: with linear weighting the labelled
#: statement of Hall's theorem ranked 53rd-104th behind "Orthonormal basis of a Hilbert space"
#: (lexical 113 vs 22) on an argument about systems of distinct representatives. The knob
#: exists so the arms can be measured on the held-out tier before the default moves:
#:   linear  - production as of 2026-09-03 (the default until measured)
#:   log     - 1 + log1p(count), the document side's own saturation
#:   binary  - a term counts once however often the query repeats it
QUERY_TF_MODE = os.environ.get("MATHBRAIN_QUERY_TF", "linear").strip().lower()


#: LEXICAL SCHEME (MATHBRAIN_LEXICAL, default "base"; F01 of the 12 Oct 2026 brain-improvement run). "base" is the formula
#: in _channel_scores: idf x (1 + log1p(tf)) / sqrt(length). Dividing by the square root of the card's length favours
#: short cards so strongly that a question worded away from its card's words almost never reaches a long card: on the dev
#: recall questions written for this (Codex, paraphrased and applied, split by card), the answering card was first for 36
#: of 130 questions in the check half. "bm25" is Okapi BM25 over the same content terms, k1 MATHBRAIN_BM25_K1 (1.2), b
#: MATHBRAIN_BM25_B (0.3; the recall profile uses 0.4), idf log1p((N - df + 0.5) / (df + 0.5)) on the same document
#: frequencies (the pool's, or the
#: store-wide table when GLOBAL_LEXICAL_DF is installed). Measured on the same 130: first 55, MRR .347 -> .455; on br50
#: MRR .597 -> .583. Only the lexical channel changes; symbol, formula, phrase and graph are as before. The math hook
#: keeps "base" (its gates are calibrated on base scores); retrieve_for_recall() below uses "bm25".
LEXICAL_SCHEME = os.environ.get("MATHBRAIN_LEXICAL", "base").strip().lower() or "base"
LEXICAL_SCHEMES = ("base", "bm25")
try:
    BM25_K1 = float(os.environ.get("MATHBRAIN_BM25_K1", "1.2"))
    BM25_B = float(os.environ.get("MATHBRAIN_BM25_B", "0.3"))
except ValueError:
    BM25_K1, BM25_B = 1.2, 0.3


def _query_term_weight(count: int) -> float:
    if QUERY_TF_MODE == "log":
        return 1.0 + math.log1p(max(0, count - 1))
    if QUERY_TF_MODE == "binary":
        return 1.0
    return float(count)


#: GLOBAL INVERSE DOCUMENT FREQUENCY for the lexical channel (campaign 2026-09-24). Since the hook's prefilter landed
#: (2026-09-07) retrieve() is handed ~300 lexically pre-selected nodes, so the document frequencies below were being
#: computed over that POOL: a word common in the pool but rare in the store (the question's own subject words) was
#: weighted as if it were common, and the ranker put concept cards above the theorem that connects them. Measured
#: on the fresh bank (rank_compare2): the target is first on 45 of 89 unnamed questions with pool IDF and 57 with
#: store-wide IDF (12 gained, 0 lost); named 76 -> 82 (7 gained, 1 lost). The caller installs the store-wide table
#: (term -> document frequency, and the number of documents); None keeps the pool statistics (the old behaviour).
#: GLOBAL_LEXICAL_MODE 1 rescales the store-wide IDF of each query so its total over the query's matched terms equals
#: the pool IDF total - term weights are redistributed, the channel's scale (which the absolute abstention gates were
#: calibrated on) is kept. Mode 2 uses the raw store-wide IDF.
GRAPH_NORMALIZE: int = int(os.environ.get("MATHBRAIN_GRAPH_NORMALIZE", "0") or 0)
GLOBAL_LEXICAL_DF: dict[str, int] | None = None
GLOBAL_LEXICAL_N: int = 0
GLOBAL_LEXICAL_MODE: int = 2


def _bm25_idf(documents: int, frequency: int) -> float:
    """Okapi BM25's idf, kept positive by log1p: log(1 + (N - df + 0.5) / (df + 0.5))."""
    return math.log1p(max(0.0, documents - frequency + 0.5) / (frequency + 0.5))


def _notation_idf_mode(signals: IndexSignals) -> bool:
    # Prose gates depend on absolute symbol scores. Use the rare-symbol mode
    # only where notation dominates the query and carries several content terms.
    symbols = _symbol_terms(signals.math_tokens)
    content = {term for term in symbols if not term.startswith("punct:") and term != "op:subscript"}
    return (len(symbols) >= 2 * max(1, len(_content_terms(signals.lexical_tokens)))
            and len(content) >= 5)


def _channel_scores(
    query_terms: Iterable[str],
    document_terms: dict[str, tuple[str, ...]],
    *,
    channel: ScoreChannel,
    weight: float,
    lengths: dict[str, int] | None = None,
    notation_mode: bool = False,
    scheme: str = "base",
    bm25_k1: float | None = None,
    bm25_b: float | None = None,
) -> tuple[dict[str, float], dict[str, list[ScoreTrace]]]:
    """lengths: a node's length for the divisor when it is not the length of its terms - an expanded card's words
    match, but only its own title and body count toward its length (see EXPANSION).
    scheme: "base" or "bm25" (see LEXICAL_SCHEME); it applies to the LEXICAL channel only."""
    bm25 = channel is ScoreChannel.LEXICAL and scheme == "bm25"
    k1 = BM25_K1 if bm25_k1 is None else bm25_k1
    b = BM25_B if bm25_b is None else bm25_b
    if channel is ScoreChannel.SYMBOL and notation_mode:
        # Layout terms have zero discriminating IDF in this mode; exclude them
        # consistently from matches and from the document-length divisor.
        def content_term(term: str) -> bool:
            return not term.startswith("punct:") and term != "op:subscript"
        query_terms = tuple(term for term in query_terms if content_term(term))
        document_terms = {node_id: tuple(term for term in terms if content_term(term))
                          for node_id, terms in document_terms.items()}
    query_counts = Counter(query_terms)
    if not query_counts:
        return {}, {}
    document_counts = {node_id: Counter(terms) for node_id, terms in document_terms.items()}
    document_frequency: Counter[str] = Counter()
    for counts in document_counts.values():
        document_frequency.update(counts.keys())
    total_documents = max(1, len(document_counts))
    idf_override: dict[str, float] = {}
    if (channel is ScoreChannel.LEXICAL and GLOBAL_LEXICAL_DF is not None and GLOBAL_LEXICAL_N > 0
            and GLOBAL_LEXICAL_MODE in (1, 2)):
        matched = [term for term in query_counts if document_frequency[term] > 0]
        if bm25:
            g = {t: _bm25_idf(GLOBAL_LEXICAL_N, GLOBAL_LEXICAL_DF.get(t, 0)) for t in matched}
        else:
            g = {t: math.log1p(GLOBAL_LEXICAL_N / (1 + GLOBAL_LEXICAL_DF.get(t, 0))) for t in matched}
        if GLOBAL_LEXICAL_MODE == 1 and g:
            if bm25:
                p_total = sum(_bm25_idf(total_documents, document_frequency[t]) for t in matched)
            else:
                p_total = sum(math.log1p(total_documents / (1 + document_frequency[t])) for t in matched)
            g_total = sum(g.values())
            factor = (p_total / g_total) if g_total > 0 else 1.0
            idf_override = {t: v * factor for t, v in g.items()}
        else:
            idf_override = g
    scores: dict[str, float] = {}
    traces: dict[str, list[ScoreTrace]] = defaultdict(list)
    average_length = 1.0
    if bm25:
        # the mean of the lengths the divisor would use (before the floor of 1), over every document of the pool
        average_length = max(1e-9, sum(
            (lengths[node_id] if lengths is not None and node_id in lengths else sum(counts.values()))
            for node_id, counts in document_counts.items()
        ) / total_documents)
    for node_id, counts in document_counts.items():
        if lengths is not None and node_id in lengths:
            length = max(1, lengths[node_id])
        else:
            length = max(1, sum(counts.values()))
        for term, query_weight in sorted(query_counts.items()):
            if term not in counts:
                continue
            inverse_frequency = idf_override.get(term)
            if inverse_frequency is None:
                if channel is ScoreChannel.SYMBOL and notation_mode:
                    inverse_frequency = math.log((total_documents + 1) / (document_frequency[term] + 0.5))
                elif bm25:
                    inverse_frequency = _bm25_idf(total_documents, document_frequency[term])
                else:
                    inverse_frequency = math.log1p(total_documents / (1 + document_frequency[term]))
            if bm25:
                tf = counts[term]
                amount = (
                    weight
                    * _query_term_weight(query_weight)
                    * inverse_frequency
                    * tf * (k1 + 1)
                    / (tf + k1 * (1 - b + b * length / average_length))
                )
            else:
                amount = (
                    weight
                    * _query_term_weight(query_weight)
                    * inverse_frequency
                    * (1 + math.log1p(counts[term]))
                    / math.sqrt(length)
                )
            scores[node_id] = scores.get(node_id, 0.0) + amount
            traces[node_id].append(
                ScoreTrace(channel, amount, f"matched normalized term {term!r}")
            )
    return scores, traces


TITLE_PHRASE_WEIGHT = 1.1


def _strip_possessive(token: str) -> str:
    if token.endswith("'s") or token.endswith("’s"):
        return token[:-2]
    return token


#: TITLE PHRASES ACROSS PLURALS AND NUMBER WORDS (MATHBRAIN_PHRASE_NORM, default on; campaign mb10d, 25 Sep, arm cand48). His
#: "the 5 singular value version of the quadratic form result" names the card titled "... quadratic forms holds when the fifth
#: singular value is large ...", but only "singular value" matched verbatim: "5" is not "fifth" and "form" is not "forms". So the
#: channel also compares n-grams after folding a plural and a number word or ordinal to its digits, on both sides. A match only
#: the folding creates counts when it carries a content word ("the 5" never meets "the fifth" by itself); every n-gram that
#: matched verbatim counts exactly as before. MATHBRAIN_PHRASE_NORM=0 restores verbatim matching.
PHRASE_NORM: bool = os.environ.get("MATHBRAIN_PHRASE_NORM", "1").strip() not in ("0", "", "off", "false")
#: THE PLURAL HALF IS OFF (MATHBRAIN_PHRASE_NORM_PLURAL, default 0; arm cand53). Measured with both halves on (arm cand48), the
#: answerable banks moved the wrong way against the landed hook: hook-path 71 -> 70 served, 55 -> 54 first; fresh2 lost Levy's
#: continuity theorem and Strassen's law of the iterated logarithm; the house bank lost one answer and demoted one. A plural fold
#: adds a title-phrase match to every card whose title differs from the question by an s. The number half stays on.
PHRASE_NORM_PLURAL: bool = os.environ.get("MATHBRAIN_PHRASE_NORM_PLURAL", "0").strip() not in ("0", "", "off", "false")
_NUMBER_WORDS = {
    "one": "1", "first": "1", "two": "2", "second": "2", "three": "3", "third": "3", "four": "4", "fourth": "4",
    "five": "5", "fifth": "5", "six": "6", "sixth": "6", "seven": "7", "seventh": "7", "eight": "8", "eighth": "8",
    "nine": "9", "ninth": "9", "ten": "10", "tenth": "10",
}
_ORDINAL_RE = re.compile(r"(\d+)(?:st|nd|rd|th)")
_PHRASE_STOP = frozenset("the a an of for and or in on at to by with from as is are be its this that".split())
#: NO ARTICLE-LED TITLE PHRASES (MATHBRAIN_PHRASE_NO_ARTICLE; 19 Oct 2026, claude-5693aca3). A long sentence title such as
#: "Aldous-Fill Open Problem 6.13 ... the maximal Kemeny constant of an n-vertex graph is ..." met the healthcheck's Turan
#: question ("Maximum number of edges in an n-vertex graph with no complete subgraph ...") on "an n-vertex", "n-vertex graph"
#: and the trigram "an n-vertex graph": three phrase units, +2.36, which lifted it over Turan's Theorem and out of the block.
#: An n-gram whose first word is an article names nothing; under this dial it does not count. Other stop words stay
#: (inner "of" carries names such as "law of large numbers").
#: Default on since 19 Oct 2026 06:0x: hook-path bank (math_probe_fresh_20260903) q_content 60 -> 64 of 89, q_hinted 85 -> 85,
#: strict absent-target fires 17 -> 19 of 32 (ceiling 0.75); healthcheck 11 speak / 6 silent. Set it to 0 to compare.
PHRASE_NO_ARTICLE: bool = os.environ.get("MATHBRAIN_PHRASE_NO_ARTICLE", "1").strip() not in ("0", "", "off", "false")
_PHRASE_ARTICLES = frozenset(("a", "an", "the"))
#: NO TITLE PHRASES THAT END ON A STOP WORD (MATHBRAIN_PHRASE_NO_TRAILING_STOP; 19 Oct 2026, claude-5693aca3). The bank's
#: Gaussian-concentration question ("... a tail bound for ...") met the harvested title "Theorem 3.8
#: (Master Tail Bound for Independent Sum of Random Tensors)" on "bound for" and the trigram "tail bound for": +2.41, which
#: put that card 0.095 under Gaussian Concentration for Lipschitz Functions and silenced the hook on the margin test. A
#: phrase that ends on "for", "of" or "the" names nothing its inner words do not; under this dial it does not count.
#: A phrase of stop words only ("is the") is left as it was: dropping those too (first form, 07:1x) cost the healthcheck's
#: wall-reduction item, whose two cards for the one chi = 1/3 finding then tied within 0.017 and silenced the margin test.
#: First form, measured: hook-path bank (math_probe_fresh_20260903) q_content 64 -> 68 of 89 (rank-1 49 -> 56,
#: silent 12 -> 10), q_hinted 85 -> 88 (rank-1 81 -> 84, silent 2 -> 0), no answerable item lost; strict absent-target fires
#: 19/32 -> 18/31 (Lee-Yang gains an off-target fire, Loomis-Whitney and Maschke go silent).
#: Default on since 19 Oct 2026 07:2x, this form: q_content 64 -> 68 of 89 (rank-1 49 -> 55, silent 12 -> 10), q_hinted 85 -> 88
#: (rank-1 81 -> 84, silent 2 -> 0), no answerable item lost; strict absent-target fires 19/32 -> 19/31 (Frankl-Wilson now finds
#: its own card); healthcheck 11 speak / 6 silent. Set it to 0 to compare.
PHRASE_NO_TRAILING_STOP: bool = os.environ.get("MATHBRAIN_PHRASE_NO_TRAILING_STOP", "1").strip() not in ("0", "", "off", "false")


def _phrase_fold(token: str) -> str:
    folded = _NUMBER_WORDS.get(token)
    if folded is not None:
        return folded
    m = _ORDINAL_RE.fullmatch(token)
    if m:
        return m.group(1)
    if PHRASE_NORM_PLURAL and len(token) >= 4 and token.endswith("s") and not token.endswith(("ss", "us", "is")) and token.isalpha():
        return token[:-1]
    return token


def _phrase_content(gram) -> bool:
    return any(tok not in _PHRASE_STOP and not tok.lstrip("-").isdigit() for tok in gram)


def _title_phrase_scores(
    query_tokens: tuple[str, ...],
    titles: dict[str, tuple[str, ...]],
    *,
    weight: float,
) -> tuple[dict[str, float], dict[str, list[ScoreTrace]]]:
    """Reward nodes whose title n-grams appear verbatim in the query.

    Bag-of-words channels cannot tell "the first isomorphism theorem" from
    text that merely mentions groups and homomorphisms; a query that speaks a
    node's name should rank the named node first. Possessives are stripped on
    both sides so "lagrange theorem" still matches "Lagrange's Theorem";
    coverage damping keeps a single shared bigram from dominating long titles.
    Under PHRASE_NORM a plural and a number word also match (see above).
    """
    query_norm = tuple(_strip_possessive(token) for token in query_tokens)
    if len(query_norm) < 2:
        return {}, {}
    query_bigrams = set(zip(query_norm, query_norm[1:]))
    query_trigrams = set(zip(query_norm, query_norm[1:], query_norm[2:]))
    if PHRASE_NORM:
        query_fold = tuple(_phrase_fold(token) for token in query_norm)
        query_fold_bigrams = set(zip(query_fold, query_fold[1:]))
        query_fold_trigrams = set(zip(query_fold, query_fold[1:], query_fold[2:]))
    scores: dict[str, float] = {}
    traces: dict[str, list[ScoreTrace]] = defaultdict(list)
    for node_id, title_tokens in titles.items():
        title_norm = tuple(_strip_possessive(token) for token in title_tokens)
        if len(title_norm) < 2:
            continue
        title_bigrams = set(zip(title_norm, title_norm[1:]))
        matched_bigrams = query_bigrams & title_bigrams
        title_trigrams = None
        if PHRASE_NORM:
            title_fold = tuple(_phrase_fold(token) for token in title_norm)
            seen = {tuple(_phrase_fold(tok) for tok in bg) for bg in matched_bigrams}
            extra = {bg for bg in (query_fold_bigrams & set(zip(title_fold, title_fold[1:])))
                     if bg not in seen and _phrase_content(bg)}
            if extra:
                matched_bigrams = matched_bigrams | extra
                title_trigrams = set(zip(title_norm, title_norm[1:], title_norm[2:]))
                raw_tri = query_trigrams & title_trigrams
                seen3 = {tuple(_phrase_fold(tok) for tok in tg) for tg in raw_tri}
                extra3 = {tg for tg in (query_fold_trigrams & set(zip(title_fold, title_fold[1:], title_fold[2:])))
                          if tg not in seen3 and _phrase_content(tg)}
        if not matched_bigrams:
            continue
        if title_trigrams is None:
            title_trigrams = set(zip(title_norm, title_norm[1:], title_norm[2:]))
            matched_trigrams = query_trigrams & title_trigrams
        else:
            matched_trigrams = raw_tri | extra3
        if PHRASE_NO_ARTICLE:
            matched_bigrams = {g for g in matched_bigrams if g[0] not in _PHRASE_ARTICLES}
            matched_trigrams = {g for g in matched_trigrams if g[0] not in _PHRASE_ARTICLES}
            if not matched_bigrams:
                continue
        if PHRASE_NO_TRAILING_STOP:
            matched_bigrams = {g for g in matched_bigrams if g[-1] not in _PHRASE_STOP or not _phrase_content(g)}
            matched_trigrams = {g for g in matched_trigrams if g[-1] not in _PHRASE_STOP or not _phrase_content(g)}
            if not matched_bigrams:
                continue
        coverage = min(1.0, len(matched_bigrams) / len(title_bigrams))
        amount = (
            weight
            * (len(matched_bigrams) + 2.0 * len(matched_trigrams))
            * (0.5 + 0.5 * coverage)
        )
        detail = "matched title phrase " + " ".join(
            sorted("_".join(bigram) for bigram in matched_bigrams)
        )
        scores[node_id] = amount
        traces[node_id].append(ScoreTrace(ScoreChannel.LEXICAL, amount, detail))
    return scores, traces


def lexical_scores(query: str, nodes: Iterable[GraphNode]) -> dict[str, float]:
    materialized = list(nodes)
    scores, _ = _channel_scores(
        tokenize(query),
        {node.id: tokenize(node.title + " " + node.body) for node in materialized},
        channel=ScoreChannel.LEXICAL,
        weight=1.0,
    )
    return scores


def guard_nodes(
    nodes: Iterable[GraphNode], guard: RetrievalGuard | None
) -> tuple[tuple[GraphNode, ...], tuple[GuardExclusion, ...]]:
    accepted: list[GraphNode] = []
    excluded: list[GuardExclusion] = []
    for node in sorted(nodes, key=lambda item: item.id):
        reason = guard.exclusion_reason(node) if guard is not None else None
        if reason is None:
            accepted.append(node)
        else:
            excluded.append(GuardExclusion(node.id, reason))
    return tuple(accepted), tuple(excluded)


def current_revision_nodes(
    nodes: Iterable[GraphNode],
    edges: Iterable[GraphEdge],
    *,
    known_ids: Iterable[str] | None = None,
) -> tuple[tuple[GraphNode, ...], tuple[GuardExclusion, ...]]:
    """Hide every superseded revision, and every head of a family that has more than one.

    ``known_ids`` names every node the caller holds, eligible or not (retrieve() passes its whole input);
    by default it is the ids of ``nodes``. Three cases for a SUPERSEDES edge:
      - its source is among ``nodes``: the target is history, ``superseded_revision`` (as before);
      - its source is known but not among ``nodes`` (the evaluation guard excluded it): the edge has no
        effect, like everything else about an ineligible node (test_research_status_ablation's
        test_ineligible_correction_cannot_hide_eligible_history);
      - its source is not known at all (a missing row, a correction not loaded): the target is withheld as
        ``superseded_by_unavailable_revision``. Until 30 Sep 2026 such an edge was ignored and the statement
        it replaced came back as current content (the 1000-page report's D06-T05); the note reader and the
        math hook's loader already failed closed on a missing endpoint.
    A family with two or more heads (successors of one claim, no reviewed merge) serves none of them, as
    ``competing_revision_heads``: which head is current is a review decision, and ranking would otherwise
    pick one by relevance (D06-T02; read_current_note refuses such a family as ambiguous). A head that is
    not known counts; an ineligible one does not.
    """

    materialized = tuple(nodes)
    node_ids = {node.id for node in materialized}
    known = node_ids if known_ids is None else node_ids | set(known_ids)
    ineligible = known - node_ids
    revision_edges = [
        edge
        for edge in edges
        if edge.kind is EdgeKind.SUPERSEDES
        and edge.source_id not in ineligible
        and edge.target_id not in ineligible
    ]
    superseded: dict[str, str] = {}
    for edge in revision_edges:
        if edge.target_id not in node_ids:
            continue
        if superseded.get(edge.target_id) != "superseded_revision":
            superseded[edge.target_id] = (
                "superseded_revision"
                if edge.source_id in node_ids
                else "superseded_by_unavailable_revision"
            )
    family: dict[str, str] = {}

    def root(member: str) -> str:
        while family.setdefault(member, member) != member:
            family[member] = family[family[member]]
            member = family[member]
        return member

    for edge in revision_edges:
        left, right = root(edge.source_id), root(edge.target_id)
        if left != right:
            family[left] = right
    retired = {edge.target_id for edge in revision_edges}
    heads: dict[str, int] = defaultdict(int)
    for member in list(family):
        if member not in retired:
            heads[root(member)] += 1
    competing = {
        node_id
        for node_id in node_ids
        if node_id in family and node_id not in retired and heads[root(node_id)] > 1
    }
    accepted = tuple(
        node for node in materialized if node.id not in superseded and node.id not in competing
    )
    exclusions = tuple(
        GuardExclusion(node_id, superseded[node_id]) for node_id in sorted(superseded)
    ) + tuple(GuardExclusion(node_id, "competing_revision_heads") for node_id in sorted(competing))
    return accepted, exclusions


def _graph_spread(
    seeds: list[str],
    base_scores: dict[str, float],
    edges: Iterable[GraphEdge],
    node_ids: set[str],
    *,
    graph_depth: int,
    decay: float,
    mode: RetrievalMode,
) -> tuple[dict[str, float], dict[str, float], dict[str, list[ScoreTrace]]]:
    adjacency: dict[str, list[tuple[str, GraphEdge]]] = defaultdict(list)
    for edge in edges:
        if edge.source_id not in node_ids or edge.target_id not in node_ids:
            continue
        if mode is RetrievalMode.PROOF and edge.kind not in PROOF_EDGE_KINDS:
            continue
        adjacency[edge.source_id].append((edge.target_id, edge))
        if mode is RetrievalMode.DISCOVERY:
            adjacency[edge.target_id].append((edge.source_id, edge))
    for neighbors in adjacency.values():
        neighbors.sort(key=lambda item: (item[0], item[1].kind.value, item[1].id))
    # FAN-OUT NORMALISATION (GRAPH_NORMALIZE, campaign 2026-09-24, default 0 = off). Without it a node hands
    # decay x its score to EVERY neighbour, so a concept adjacent to many cards (a hub) raises all of them at once;
    # the spreading-activation model the paper states divides a node's outflow among its neighbours in proportion
    # to edge weight (P = W / d), which is what bounds the mass and keeps the walk local. Mode 1 divides by the
    # node's total outgoing weight; mode 2 by its square root (a softer penalty between the two). MEASURED the same
    # day over 381 labelled items (rank_eval v2): either mode moves rank-1 by one item; the whole graph channel is
    # worth at most two. Kept off; the switch stays so the result can be re-measured on a denser graph.
    fanout: dict[str, float] = {}
    if GRAPH_NORMALIZE:
        for node_id, neighbors in adjacency.items():
            fanout[node_id] = sum(abs(e.signed_weight) * EDGE_MULTIPLIER[e.kind] for _, e in neighbors) or 1.0

    graph_scores: dict[str, float] = defaultdict(float)
    warning_scores: dict[str, float] = defaultdict(float)
    traces: dict[str, list[ScoreTrace]] = defaultdict(list)
    for seed in seeds:
        frontier: list[
            tuple[str, float, int, tuple[str, ...], tuple[GraphEdge, ...], GraphEdge | None]
        ] = [(seed, base_scores[seed], 0, (seed,), (), None)]
        best: dict[
            tuple[str, bool], tuple[float, tuple[str, ...], tuple[GraphEdge, ...], GraphEdge | None]
        ] = {}
        while frontier:
            current, score, depth, path, path_edges, warning_edge = frontier.pop(0)
            if depth >= graph_depth:
                continue
            for neighbor, edge in adjacency.get(current, []):
                if neighbor in path:
                    continue
                propagated = score * decay * abs(edge.signed_weight) * EDGE_MULTIPLIER[edge.kind]
                if GRAPH_NORMALIZE == 1:
                    propagated /= max(1.0, fanout.get(current, 1.0))
                elif GRAPH_NORMALIZE == 2:
                    propagated /= max(1.0, math.sqrt(fanout.get(current, 1.0)))
                next_warning = warning_edge or (edge if edge.is_warning else None)
                key = (neighbor, next_warning is not None)
                previous = best.get(key)
                if previous is not None and previous[0] >= propagated:
                    continue
                next_path = (*path, neighbor)
                next_edges = (*path_edges, edge)
                best[key] = (propagated, next_path, next_edges, next_warning)
                frontier.append(
                    (neighbor, propagated, depth + 1, next_path, next_edges, next_warning)
                )

        for (node_id, warning_path), (score, path, path_edges, warning_edge) in sorted(
            best.items(), key=lambda item: (item[0][0], item[0][1])
        ):
            if warning_path:
                warning_scores[node_id] += score
                assert warning_edge is not None
                traces[node_id].append(
                    ScoreTrace(
                        ScoreChannel.WARNING,
                        score,
                        f"surfaced by negative {warning_edge.kind.value} edge",
                        source_node_id=seed,
                        edge_id=warning_edge.id,
                        edge_kind=warning_edge.kind,
                        edge_polarity=warning_edge.polarity,
                        path=path,
                    )
                )
            else:
                graph_scores[node_id] += score
                last_edge = path_edges[-1]
                traces[node_id].append(
                    ScoreTrace(
                        ScoreChannel.GRAPH,
                        score,
                        f"propagated through typed {last_edge.kind.value} edge",
                        source_node_id=seed,
                        edge_id=last_edge.id,
                        edge_kind=last_edge.kind,
                        edge_polarity=last_edge.polarity,
                        path=path,
                    )
                )

    # A seed is the one node in its own neighbourhood that receives nothing from
    # itself - `if neighbor in path: continue` bars it - while every neighbour
    # collects decay x the seed's base score with no division by degree. On a query
    # that names a claim, the claim keeps only its direct match and its neighbour
    # gets that plus 55% of it, which is how the ranker came to answer with a
    # claim's neighbour instead of the claim on 47.4% of the cases where it missed
    # (chance 0.10%). Retention removes the asymmetry rather than the channel: the
    # seed keeps exactly the share it hands out, and no neighbour loses anything.
    for seed in seeds:
        retained = base_scores[seed] * decay
        if retained <= 0:
            continue
        graph_scores[seed] += retained
        traces[seed].append(
            ScoreTrace(
                ScoreChannel.GRAPH,
                retained,
                "seed retains the share it propagates to its neighbours",
                source_node_id=seed,
                path=(seed,),
            )
        )

    # The spread runs once per seed and accumulates, so this channel is a SUM over
    # seeds while every other channel is not. Its scale therefore grows with how many
    # seeds a query raises - which is mostly how much text was sent - and it outgrows
    # direct evidence on long prompts. Average over the seeds instead, so the channel
    # answers "how strongly is this node connected to what the query is about" rather
    # than "how much text did you send".
    if seeds:
        divisor = float(len(seeds))
        graph_scores = defaultdict(
            float, {node_id: score / divisor for node_id, score in graph_scores.items()}
        )
        warning_scores = defaultdict(
            float, {node_id: score / divisor for node_id, score in warning_scores.items()}
        )
        scaled: dict[str, list[ScoreTrace]] = defaultdict(list)
        for node_id, items in traces.items():
            for item in items:
                scaled[node_id].append(
                    ScoreTrace(
                        item.channel,
                        item.amount / divisor,
                        item.detail,
                        source_node_id=item.source_node_id,
                        edge_id=item.edge_id,
                        edge_kind=item.edge_kind,
                        edge_polarity=item.edge_polarity,
                        path=item.path,
                    )
                )
        traces = scaled
    return dict(graph_scores), dict(warning_scores), traces


def retrieve(
    query: str,
    *,
    nodes: Iterable[GraphNode],
    edges: Iterable[GraphEdge],
    limit: int = 10,
    seed_limit: int = 8,
    graph_depth: int = 2,
    decay: float = 0.55,
    min_support_score: float = 0.15,
    min_margin: float = 0.0,
    guard: RetrievalGuard | None = None,
    mode: RetrievalMode = RetrievalMode.DISCOVERY,
    current_only: bool = True,
    background_hubs: CommunityIndex | PreparedCommunityIndex | None = None,
    background_limit: int = 5,
    lexical_scheme: str | None = None,
    title_weight: float = 0.0,
    semantic_weight: float | None = None,
    bm25_k1: float | None = None,
    bm25_b: float | None = None,
    expansion_weight: float = 0.0,
) -> RetrievalResult:
    """lexical_scheme: "base" or "bm25" (None: LEXICAL_SCHEME, from MATHBRAIN_LEXICAL). title_weight: a card's title
    words count 1 + title_weight times in the lexical channel (0: as before). semantic_weight: None leaves the meaning
    channel as configured (on only under MATH_BRAIN_SEMANTIC, at MATH_BRAIN_SEMANTIC_WEIGHT); a number turns it on for
    this call at that weight per standard deviation (0 turns it off). See retrieve_for_recall()."""
    mode = RetrievalMode(mode)
    scheme = (lexical_scheme or LEXICAL_SCHEME or "base").strip().lower()
    if scheme not in LEXICAL_SCHEMES:
        raise ValueError("lexical_scheme must be one of " + ", ".join(LEXICAL_SCHEMES))
    if not math.isfinite(title_weight) or title_weight < 0 or title_weight != int(title_weight):
        raise ValueError("title_weight must be a whole number, zero or more")
    if semantic_weight is not None and (not math.isfinite(semantic_weight) or semantic_weight < 0):
        raise ValueError("semantic_weight must be finite and nonnegative")
    if limit <= 0 or seed_limit <= 0:
        raise ValueError("limit and seed_limit must be positive")
    if graph_depth < 0:
        raise ValueError("graph_depth must be nonnegative")
    if not 0 <= decay <= 1:
        raise ValueError("decay must be in [0, 1]")
    if (
        not math.isfinite(min_support_score)
        or not math.isfinite(min_margin)
        or min_support_score < 0
        or min_margin < 0
    ):
        raise ValueError("abstention thresholds must be nonnegative")

    signals = index_signals(query)
    node_list = tuple(nodes)
    edge_list = tuple(edges)
    accepted, exclusions = guard_nodes(node_list, guard)
    if current_only:
        accepted, revision_exclusions = current_revision_nodes(
            accepted, edge_list, known_ids=(node.id for node in node_list)
        )
        exclusions = (*exclusions, *revision_exclusions)
    if len({node.id for node in accepted}) != len(accepted):
        raise ValueError("node identifiers must be unique")
    if signals.empty:
        return RetrievalResult(
            signals, (), True, AbstentionReason.NO_INDEXABLE_SIGNALS, exclusions
        )

    node_map = {node.id: node for node in accepted}
    if _SIGNAL_DB is not None:
        prefetch_signals([index_text(node) if node.id in EXPANSION else node.title + " " + node.body for node in accepted]
                         + [node.title + " " + node.body for node in accepted if node.id in EXPANSION])
    node_signals = {
        node.id: index_signals(index_text(node) if node.id in EXPANSION else node.title + " " + node.body)
        for node in accepted
    }
    # Both sides of a channel are filtered with the same rule, so document length -
    # the divisor in _channel_scores - counts only terms that channel can match.
    # An expanded card is matched by its expansion words but measured by its own title and body: counted in the
    # length, twenty added words cut every one of its matches by about a fifth, and near-floor house cards fell out
    # (measured 24 Sep on the house bank: three losses, all this).
    expanded_lengths = {
        node.id: len(_content_terms(index_signals(node.title + " " + node.body).lexical_tokens))
        for node in accepted
        if node.id in EXPANSION
    }
    title_tokens = {node.id: _title_tokens(node.title) for node in accepted}
    lexical_documents = {node_id: _card_terms(item)[0] for node_id, item in node_signals.items()}
    if title_weight > 0:
        # A title word counts 1 + title_weight times: in its term frequency and in the card's length (an expanded card's
        # length is its title and body, so its title words are added there too).
        whole = int(title_weight)
        for node_id in lexical_documents:
            title_terms = _content_terms(title_tokens[node_id])
            if not title_terms:
                continue
            lexical_documents[node_id] = lexical_documents[node_id] + title_terms * whole
            if node_id in expanded_lengths:
                expanded_lengths[node_id] += len(title_terms) * whole
    lexical, lexical_traces = _channel_scores(
        _content_terms(signals.lexical_tokens),
        lexical_documents,
        channel=ScoreChannel.LEXICAL,
        weight=1.0,
        lengths=expanded_lengths or None,
        scheme=scheme,
        bm25_k1=bm25_k1,
        bm25_b=bm25_b,
    )
    if expansion_weight > 0 and EXPANSION_FIELD:
        # the rewordings field: its own document frequencies and lengths, its weight, the same lexical scheme
        field_documents = {
            node_id: _expansion_field_terms(node_id) for node_id in node_signals if node_id in EXPANSION_FIELD
        }
        field_scores, field_traces = _channel_scores(
            _content_terms(signals.lexical_tokens),
            {node_id: terms for node_id, terms in field_documents.items() if terms},
            channel=ScoreChannel.LEXICAL,
            weight=expansion_weight,
            scheme=scheme,
            bm25_k1=bm25_k1,
            bm25_b=bm25_b,
        )
        for node_id, amount in field_scores.items():
            lexical[node_id] = lexical.get(node_id, 0.0) + amount
            lexical_traces[node_id].extend(
                ScoreTrace(item.channel, item.amount, item.detail.replace("matched normalized term", "matched rewording term"))
                for item in field_traces[node_id]
            )
    symbols, symbol_traces = _channel_scores(
        _symbol_terms(signals.math_tokens),
        {node_id: _card_terms(item)[1] for node_id, item in node_signals.items()},
        channel=ScoreChannel.SYMBOL,
        weight=1.35,
        notation_mode=_notation_idf_mode(signals),
    )
    formulas, formula_traces = _channel_scores(
        signals.formula_fingerprints,
        {node_id: item.formula_fingerprints for node_id, item in node_signals.items()},
        channel=ScoreChannel.FORMULA,
        weight=2.0,
    )
    # The three channels above all match surface form, so a claim that answers the
    # question in different words scores zero on every one of them and is dropped by
    # the filter below before seeds are picked. The semantic channel is what lets such
    # a claim be a candidate at all; it must therefore land before that filter.
    semantic: dict[str, float] = {}
    semantic_traces: dict[str, list[ScoreTrace]] = defaultdict(list)
    # A question that carries parameters - two or more separate numbers, the shape of the enumerated families ("walks
    # of length 10 in the strip 0 <= y < 3 avoiding (4,2)") - gets no semantic scores: a dense embedding cannot tell a
    # family's members apart, and measured 2026-09-24 it moved the stored answer off first place on 8 of 40 such
    # locate questions that the lexical and tuple channels had right. MATH_BRAIN_SEMANTIC_MAX_NUMBERS (default 1)
    # is the most separate numbers a question may carry and still get the channel.
    _max_numbers = int(os.environ.get("MATH_BRAIN_SEMANTIC_MAX_NUMBERS", "1") or 1)
    _semantic_on = _semantic.enabled() if semantic_weight is None else semantic_weight > 0
    if _semantic_on and len(re.findall(r"\d+", query or "")) <= _max_numbers:
        try:
            if _semantic.available():
                details = _semantic.standout_details(query, node_map.keys())
                if semantic_weight is not None:
                    # standout_details pays WEIGHT per standard deviation; this call asked for semantic_weight
                    _per_sd = float(_semantic.WEIGHT) if _semantic.WEIGHT else 0.0
                    details = {
                        node_id: (amount / _per_sd * semantic_weight, cosine)
                        for node_id, (amount, cosine) in details.items()
                        if _per_sd > 0 and amount > 0
                    }
                semantic = {node_id: amount for node_id, (amount, _cos) in details.items()}
                for node_id, (amount, cosine) in details.items():
                    _sd = amount if semantic_weight is None else amount / semantic_weight
                    semantic_traces[node_id].append(
                        ScoreTrace(
                            ScoreChannel.SEMANTIC,
                            amount,
                            f"{_sd:.2f} sd above the corpus mean similarity "
                            f"(cosine {cosine:.3f})",
                        )
                    )
        except Exception:
            # A missing or unreadable artifact leaves the surface channels exactly as
            # they were; retrieval never fails because the vectors are absent.
            semantic, semantic_traces = {}, defaultdict(list)
    base_scores = {
        node_id: lexical.get(node_id, 0.0)
        + symbols.get(node_id, 0.0)
        + formulas.get(node_id, 0.0)
        + semantic.get(node_id, 0.0)
        for node_id in node_map
    }
    base_scores = {node_id: score for node_id, score in base_scores.items() if score > 0}
    seeds = sorted(base_scores, key=lambda item: (-base_scores[item], item))[:seed_limit]
    graph, warnings, graph_traces = _graph_spread(
        seeds,
        base_scores,
        edge_list,
        set(node_map),
        graph_depth=graph_depth,
        decay=decay,
        mode=mode,
    )
    # Title-phrase bonus lands AFTER graph spread on purpose: naming a node is
    # evidence about that node, not its neighborhood. Folding it into the seed
    # scores lets well-connected hub nodes collect a propagated share of every
    # neighbor's bonus and outrank the very node the query named.
    phrase_scores, phrase_traces = _title_phrase_scores(
        signals.lexical_tokens,
        title_tokens,
        weight=TITLE_PHRASE_WEIGHT,
    )
    for node_id, amount in phrase_scores.items():
        lexical[node_id] = lexical.get(node_id, 0.0) + amount
        lexical_traces.setdefault(node_id, []).extend(phrase_traces[node_id])

    hits: list[SearchHit] = []
    for node_id, node in node_map.items():
        components = (
            lexical.get(node_id, 0.0),
            symbols.get(node_id, 0.0),
            formulas.get(node_id, 0.0),
            semantic.get(node_id, 0.0),
            graph.get(node_id, 0.0),
            warnings.get(node_id, 0.0),
        )
        if not any(components):
            continue
        trace = tuple(
            sorted(
                chain(
                    lexical_traces.get(node_id, []),
                    symbol_traces.get(node_id, []),
                    formula_traces.get(node_id, []),
                    semantic_traces.get(node_id, []),
                    graph_traces.get(node_id, []),
                ),
                key=lambda item: (
                    item.channel.value,
                    item.detail,
                    item.source_node_id or "",
                    item.edge_id or "",
                    item.path,
                ),
            )
        )
        hits.append(SearchHit(node, *components, trace))
    # Cap and demote rather than exclude: a claim with no statement in it still
    # appears if there is room, but it can never take a slot from a claim that says
    # something. Under the pre-repair ranker one of the four such nodes in the store
    # held slot 1 of the injected block on 60.4% of real prompts.
    pool = sorted(
        hits,
        key=lambda hit: (_is_degenerate(hit.node), -hit.total_score, hit.node.id),
    )
    ranked = tuple(pool[:limit])
    if not ranked:
        return RetrievalResult(
            signals, (), True, AbstentionReason.NO_CANDIDATE_MATCH, exclusions
        )

    # Both tests judge the candidates that can be served first. A claim with no statement in it
    # sits below every claim that says something, yet its title alone can carry the highest
    # support in the pool: a two-word stub titled like the query outscored the theorem it names.
    # Compared in, it let weak statements through the support floor on its score, forced an
    # abstention by standing close to the one real answer, or hid a real tie between two
    # statements behind its own margin (account03/B03 r88, B03-R72-DEGENERATE-CONTENDER-BREAKS-
    # MARGIN-SEMANTICS-001). It counts only when nothing with a statement is left to compare.
    def servable(hits_in: Iterable[SearchHit]) -> list[SearchHit]:
        hits_in = list(hits_in)
        return [hit for hit in hits_in if not _is_degenerate(hit.node)] or hits_in

    # The support floor, like the ambiguity test below, judges the servable candidates of the whole pool: the pool is
    # ordered by total score, which adds warning mass to support, so a caution card could fill a one-hit output and
    # abstain for weak support while the same query asked for two hits served (account04/B02 r72,
    # B02-R72-SUPPORT-FLOOR-REMAINS-LIMIT-DEPENDENT-20260929). The verdict is now the one an unlimited request gets.
    by_support = sorted(servable(pool), key=lambda hit: (-hit.support_score, hit.node.id))
    if by_support[0].support_score < min_support_score:
        return RetrievalResult(
            signals,
            ranked,
            True,
            AbstentionReason.BELOW_SUPPORT_THRESHOLD,
            exclusions,
        )
    # The ambiguity test compares the two best-supported candidates of the whole pool, not of
    # the output, so its verdict is the one an unlimited request gets. Asking for one hit used to
    # remove the runner-up before the comparison and with it the doubt (IA-20260929-MB-01).
    contenders = sorted(servable(pool), key=lambda hit: (-hit.support_score, hit.node.id))
    if len(contenders) > 1:
        margin = contenders[0].support_score - contenders[1].support_score
        if margin < min_margin:
            # A link exempts a near tie only when the mode's walk follows it, and the linked pair is then served
            # together whatever the limit: the exemption's reason is that showing both resolves the subject
            # (interactive audit B100-01, 30 Sep 2026: a semantic association that proof mode never walks lifted
            # proof-mode abstention, and a request for one hit served one of the pair). The verdict stays the one an
            # unlimited request gets (IA-20260929-MB-01).
            if not _directly_linked(contenders[0].node.id, contenders[1].node.id, edge_list, mode):
                return RetrievalResult(
                    signals,
                    ranked,
                    True,
                    AbstentionReason.AMBIGUOUS_TOP_MATCH,
                    exclusions,
                )
            served_ids = {hit.node.id for hit in ranked}
            ranked = ranked + tuple(
                hit for hit in contenders[:2] if hit.node.id not in served_ids
            )
    background = ()
    if background_hubs is not None:
        # Activate matching normalized title n-grams after all foreground gates.
        # Whole-title coverage is not required by the phrase-score channel.
        # Even an explicit historical foreground view cannot revive background hubs.
        background_ids = set(node_map)
        if not current_only:
            background_nodes, _ = current_revision_nodes(
                accepted, edge_list, known_ids=(node.id for node in node_list)
            )
            background_ids = {node.id for node in background_nodes}
        background = background_hubs.activate(
            phrase_scores, eligible_ids=background_ids, edges=edge_list, limit=background_limit
        )
    return RetrievalResult(signals, ranked, False, None, exclusions, background)


#: THE RECALL PROFILE (F01, 12 Oct 2026). Deliberate recall - an agent searching the store with its own words
#: (math_recall.py) - is a different job from the hook's passive selection: the agent reads every hit whole, so what
#: matters is that the answering card is among the first eight, whatever its length. Tuned on the tune half of the dev
#: recall questions (card-id hash split), checked on the other half and on br50 (12 Oct, 216 dev questions):
#:   base retrieve (depth 0)           check: first 37/142, top 10 69, top 50 88, MRR .331 | br50 MRR .597, top 10 42
#:   bm25 b .4 + title x2 + meaning 2  check: first 54/142, top 10 77, top 50 100, MRR .442 | br50 MRR .613, top 10 42
#: Each part is env-overridable: MATHBRAIN_RECALL_LEXICAL (bm25 | base), MATHBRAIN_RECALL_BM25_B, MATHBRAIN_RECALL_TITLE
#: (whole number), MATHBRAIN_RECALL_SEM_WEIGHT (0 = no meaning channel), MATHBRAIN_RECALL_EXP_WEIGHT (the rewordings field,
#: 0 = off, the default until F02's sidecar is measured; file MATHBRAIN_EXPANSION_FIELD or <store>.rewordings.jsonl);
#: MATHBRAIN_RECALL_PROFILE=0 makes this exactly retrieve(). The meaning channel needs the claim vectors and the ONNX query
#: encoder; without them it is silently absent.
def _recall_env(name: str, default: str) -> str:
    return (os.environ.get(name, default) or default).strip()


def recall_profile() -> dict:
    """The keyword arguments retrieve_for_recall() adds, as configured now ({} when the profile is switched off)."""
    if _recall_env("MATHBRAIN_RECALL_PROFILE", "1").lower() in ("0", "off", "false", "no"):
        return {}
    try:
        b = float(_recall_env("MATHBRAIN_RECALL_BM25_B", "0.4"))
        title = float(_recall_env("MATHBRAIN_RECALL_TITLE", "1"))
        sem = float(_recall_env("MATHBRAIN_RECALL_SEM_WEIGHT", "2.0"))
        exp = float(_recall_env("MATHBRAIN_RECALL_EXP_WEIGHT", "0"))
    except ValueError:
        b, title, sem, exp = 0.4, 1.0, 2.0, 0.0
    return {"lexical_scheme": _recall_env("MATHBRAIN_RECALL_LEXICAL", "bm25").lower(), "bm25_b": b,
            "title_weight": title, "semantic_weight": sem, "expansion_weight": exp}


def retrieve_for_recall(query: str, **kwargs) -> RetrievalResult:
    """retrieve() with the recall profile: BM25 lexical scoring, title words counted twice and the meaning channel on.
    Every retrieve() keyword passes through; one given explicitly wins over the profile."""
    for key, value in recall_profile().items():
        kwargs.setdefault(key, value)
    if kwargs.get("expansion_weight") and not EXPANSION_FIELD:
        # the rewordings file: MATHBRAIN_EXPANSION_FIELD, else <store>.rewordings.jsonl beside the store in use
        path = os.environ.get("MATHBRAIN_EXPANSION_FIELD") or (
            (os.environ.get("MATHBRAIN_STORE") or "") + ".rewordings.jsonl")
        if path and os.path.isfile(path):
            load_expansion_field(path)
    return retrieve(query, **kwargs)


def _directly_linked(
    first_id: str,
    second_id: str,
    edges: Iterable[GraphEdge],
    mode: RetrievalMode = RetrievalMode.DISCOVERY,
) -> bool:
    """Ambiguity abstention targets queries whose subject the index cannot
    resolve. Two near-tied top hits that are directly connected in the graph
    (a theorem and the conjecture it proves, a claim and its refutation) mean
    the subject IS resolved - serving both beats staying silent. In proof mode
    only a relation the proof walk follows counts; an association it refuses
    to walk cannot settle which statement a proof question is about."""
    pair = {first_id, second_id}
    return any(
        {edge.source_id, edge.target_id} == pair
        and (mode is not RetrievalMode.PROOF or edge.kind in PROOF_EDGE_KINDS)
        for edge in edges
    )


def search(
    query: str,
    *,
    nodes: Iterable[GraphNode],
    edges: Iterable[GraphEdge],
    limit: int = 10,
    seed_limit: int = 8,
    graph_depth: int = 2,
    decay: float = 0.55,
    mode: RetrievalMode = RetrievalMode.DISCOVERY,
    current_only: bool = True,
) -> list[SearchHit]:
    """Backward-compatible hit list; use :func:`retrieve` for abstention details."""

    result = retrieve(
        query,
        nodes=nodes,
        edges=edges,
        limit=limit,
        seed_limit=seed_limit,
        graph_depth=graph_depth,
        decay=decay,
        min_support_score=0.0,
        mode=mode,
        current_only=current_only,
    )
    return list(result.hits)


def exact_dependency_closure(
    root_id: str,
    *,
    nodes: Iterable[GraphNode],
    edges: Iterable[GraphEdge],
) -> ExactDependencyClosure:
    """Return a dependency-first closure using directed ``depends_on`` edges only.

    Missing reachable targets and cycles fail closed.  Semantic, bridge, citation,
    and similarity edges are ignored even when they connect the same nodes.
    """

    node_list = tuple(nodes)
    node_map = {node.id: node for node in node_list}
    if len(node_map) != len(node_list):
        raise ValueError("node identifiers must be unique")
    if root_id not in node_map:
        raise KeyError(root_id)

    adjacency: dict[str, list[tuple[str, GraphEdge]]] = defaultdict(list)
    for edge in edges:
        if edge.kind is EdgeKind.DEPENDS_ON:
            adjacency[edge.source_id].append((edge.target_id, edge))
    for neighbors in adjacency.values():
        neighbors.sort(key=lambda item: (item[0], item[1].id))

    visited: set[str] = set()
    active: list[str] = []
    active_set: set[str] = set()
    ordered: list[str] = []
    used_edge_ids: list[str] = []

    def visit(node_id: str) -> None:
        if node_id in visited:
            return
        if node_id in active_set:
            start = active.index(node_id)
            cycle = " -> ".join((*active[start:], node_id))
            raise ValueError(f"dependency cycle: {cycle}")
        if node_id not in node_map:
            raise ValueError(f"missing dependency node: {node_id}")
        active.append(node_id)
        active_set.add(node_id)
        for target_id, edge in adjacency.get(node_id, []):
            visit(target_id)
            if edge.id not in used_edge_ids:
                used_edge_ids.append(edge.id)
        active.pop()
        active_set.remove(node_id)
        visited.add(node_id)
        ordered.append(node_id)

    visit(root_id)
    return ExactDependencyClosure(root_id, tuple(ordered), tuple(used_edge_ids))
