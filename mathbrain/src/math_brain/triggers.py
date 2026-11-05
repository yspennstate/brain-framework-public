"""Specific mathematical review actions activated by text or graph context.

Two trigger schemas share this module.

Schema 1 (``config/triggers.toml``): a trigger fires when any of its keywords occurs in the text as a
substring, case-insensitively. The behaviour is unchanged from the first version of this module.

Schema 2 (``config/review_triggers_probability.toml``, front G02 of the 12 Oct 2026 brain-improvement run):
a trigger is one recurring failure pattern written as features of the text. Anchors are whole words or
phrases (a trailing ``*`` makes a prefix) or regular expressions, matched on a normalized copy of the text
(Greek letters and LaTeX macros spelled out, accents dropped, hyphens between letters read as spaces,
case folded). A phrase that carries an operator (``sigma > 0``) is matched with every space removed.

- ``context``: groups that must each have a member present, or the trigger stays silent.
- ``unless``: phrases whose presence silences the trigger.
- ``mode = "present"``: the trigger fires whenever an anchor (and the context) is present.
- ``mode = "missing"``: the trigger fires only when an anchor is present and some group in ``requires``
  has no member in the text - the named result is used, but a hypothesis it needs is never stated.
  The match carries the labels of the missing groups, so the caution can say what to look for.

A fired trigger is a question for the reader, not a verdict: the text may state the hypothesis in words
the lists do not hold, or carry it from an earlier line the matcher never saw.
"""

from __future__ import annotations

import re
import tomllib
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable

MODES = ("present", "missing")
DEFAULT_REVIEW_TRIGGERS = Path(__file__).resolve().parents[2] / "config" / "review_triggers_probability.toml"


@dataclass(frozen=True, slots=True)
class ReviewTrigger:
    id: str
    keywords: tuple[str, ...]
    action: str
    patterns: tuple[str, ...] = ()
    context: tuple[tuple[str, ...], ...] = ()
    requires: tuple[tuple[str, ...], ...] = ()
    requires_labels: tuple[str, ...] = ()
    unless: tuple[str, ...] = ()
    mode: str = "present"
    whole_words: bool = False
    reflex: str = ""
    title: str = ""
    hint: str = ""

    def __post_init__(self) -> None:
        if not self.id or not (self.keywords or self.patterns) or not self.action:
            raise ValueError("trigger id, keywords, and action are required")
        if self.mode not in MODES:
            raise ValueError(f"trigger {self.id}: unknown mode {self.mode!r}")
        if self.mode == "missing" and not self.requires:
            raise ValueError(f"trigger {self.id}: mode 'missing' needs at least one requires group")
        if any(not group for group in (*self.context, *self.requires)):
            raise ValueError(f"trigger {self.id}: empty context or requires group")
        if self.requires_labels and len(self.requires_labels) != len(self.requires):
            raise ValueError(f"trigger {self.id}: one label per requires group")
        for pattern in self.patterns:
            re.compile(pattern)
        if self.patterns and not self.whole_words:
            raise ValueError(f"trigger {self.id}: patterns need the schema-2 matcher (whole_words)")


@dataclass(frozen=True, slots=True)
class TriggerMatch:
    trigger: ReviewTrigger
    matched_keywords: tuple[str, ...]
    missing: tuple[str, ...] = ()


# ---------------------------------------------------------------------------------------------- normalization

_CHAR_MAP = {
    "σ": " sigma ", "μ": " mu ", "λ": " lambda ", "ε": " epsilon ", "ϵ": " epsilon ", "δ": " delta ",
    "θ": " theta ", "ρ": " rho ", "τ": " tau ", "π": " pi ", "φ": " phi ", "ϕ": " phi ", "ψ": " psi ",
    "α": " alpha ", "β": " beta ", "γ": " gamma ", "κ": " kappa ", "ν": " nu ", "ω": " omega ", "η": " eta ",
    "ξ": " xi ", "ζ": " zeta ", "χ": " chi ", "Σ": " sigma ", "Δ": " delta ", "Λ": " lambda ", "Φ": " phi ",
    "Ω": " omega ", "Γ": " gamma ", "Θ": " theta ", "Ψ": " psi ",
    "≤": "<=", "⩽": "<=", "≥": ">=", "⩾": ">=", "≠": "!=", "→": "->", "⟶": "->", "↦": "->", "⇒": "=>",
    "∞": " infinity ", "∈": " in ", "×": "x", "·": "*", "−": "-", "–": "-", "—": "-", "‐": "-", "‑": "-",
    "’": "'", "‘": "'", "“": '"', "”": '"', "≪": "<<", "≫": ">>", "∑": " sum ", "∏": " prod ", "√": "sqrt",
    "≈": "~", "∼": "~", "ℝ": "R", "ℕ": "N", "ℤ": "Z", "𝔼": "E", "ℙ": "P", "\u00a0": " ",
}
_CHAR_MAP.update({  # superscripts and subscripts, before NFKD folds them into bare digits ("k2" for k squared)
    "¹": "^1", "²": "^2", "³": "^3", "⁴": "^4", "ⁿ": "^n", "⁻": "^-",
    "₀": "_0", "₁": "_1", "₂": "_2", "₃": "_3", "ᵢ": "_i", "ₙ": "_n", "ₖ": "_k",
})
_LATEX = (
    (re.compile(r"\\(?:leqslant|leq|le)(?![A-Za-z])"), "<="),
    (re.compile(r"\\(?:geqslant|geq|ge)(?![A-Za-z])"), ">="),
    (re.compile(r"\\(?:neq|ne)(?![A-Za-z])"), "!="),
    (re.compile(r"\\infty(?![A-Za-z])"), " infinity "),
    (re.compile(r"\\(?:to|rightarrow|longrightarrow)(?![A-Za-z])"), "->"),
    (re.compile(r"\\ll(?![A-Za-z])"), "<<"),
    (re.compile(r"\\(?:mathbb|mathbf|mathrm|operatorname|textrm|text|mathcal)\s*\{\s*([^{}]*)\}"), r" \1 "),
    (re.compile(r"\\(?:left|right|big|Big|bigg|Bigg)(?![A-Za-z])"), ""),
    (re.compile(r"\\[,;:! ]"), " "),
    (re.compile(r"\\([A-Za-z]+)"), r" \1 "),
)
_HYPHEN_BETWEEN_LETTERS = re.compile(r"(?<=[a-z])-(?=[a-z])")
_SPACE = re.compile(r"\s+")
_OPERATOR = re.compile(r"[<>=!|^_/(){}\[\]+*]")
_WORDCHAR = "a-z0-9"


def normalize_text(text: str) -> str:
    """The text the schema-2 matcher reads: spelled-out symbols, no accents, case folded, single spaces."""

    out = "".join(_CHAR_MAP.get(ch, ch) for ch in text or "")
    for pattern, repl in _LATEX:
        out = pattern.sub(repl, out)
    out = unicodedata.normalize("NFKD", out)
    out = "".join(ch for ch in out if not unicodedata.combining(ch))
    out = out.casefold().replace("$", " ")
    out = _HYPHEN_BETWEEN_LETTERS.sub(" ", out)
    return _SPACE.sub(" ", out).strip()


def _compact(text: str) -> str:
    return re.sub(r"[\s{}]+", "", text)


@lru_cache(maxsize=8192)
def _phrase_matcher(phrase: str) -> tuple[bool, re.Pattern[str]]:
    """(operator phrase?, compiled matcher) for one list entry."""

    prefix = phrase.endswith("*")
    norm = normalize_text(phrase[:-1] if prefix else phrase)
    if _OPERATOR.search(norm):
        return True, re.compile(re.escape(_compact(norm)))
    tail = "" if prefix else f"(?![{_WORDCHAR}])"
    return False, re.compile(f"(?<![{_WORDCHAR}]){re.escape(norm)}{tail}")


@lru_cache(maxsize=1024)
def _regex(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


class _Text:
    __slots__ = ("norm", "compact")

    def __init__(self, text: str) -> None:
        self.norm = normalize_text(text)
        self.compact = _compact(self.norm)

    def has(self, phrase: str) -> bool:
        operator, matcher = _phrase_matcher(phrase)
        return bool(matcher.search(self.compact if operator else self.norm))


# ---------------------------------------------------------------------------------------------- loading


def _groups(record: dict, key: str) -> tuple[tuple[str, ...], ...]:
    value = record.get(key, [])
    if value and all(isinstance(item, str) for item in value):
        value = [value]
    return tuple(tuple(group) for group in value)


def load_triggers(path: str | Path) -> tuple[ReviewTrigger, ...]:
    with Path(path).open("rb") as handle:
        payload = tomllib.load(handle)
    schema = payload.get("schema_version")
    if schema not in (1, 2):
        raise ValueError("unsupported trigger schema")
    triggers = []
    seen = set()
    for record in payload.get("trigger", []):
        if schema == 1:
            trigger = ReviewTrigger(
                id=record["id"],
                keywords=tuple(record["keywords"]),
                action=record["action"],
            )
        else:
            trigger = ReviewTrigger(
                id=record["id"],
                keywords=tuple(record.get("keywords", ())),
                action=record["action"],
                patterns=tuple(record.get("patterns", ())),
                context=_groups(record, "context"),
                requires=_groups(record, "requires"),
                requires_labels=tuple(record.get("requires_labels", ())),
                unless=tuple(record.get("unless", ())),
                mode=record.get("mode", "present"),
                whole_words=True,
                reflex=record.get("reflex", ""),
                title=record.get("title", ""),
                hint=record.get("hint", ""),
            )
        if trigger.id in seen:
            raise ValueError(f"duplicate trigger id: {trigger.id}")
        seen.add(trigger.id)
        triggers.append(trigger)
    return tuple(triggers)


@lru_cache(maxsize=4)
def _default_triggers(path: str) -> tuple[ReviewTrigger, ...]:
    return load_triggers(path)


def default_review_triggers() -> tuple[ReviewTrigger, ...]:
    """The probability review triggers shipped with the store (empty if the file is absent)."""

    if not DEFAULT_REVIEW_TRIGGERS.exists():
        return ()
    return _default_triggers(str(DEFAULT_REVIEW_TRIGGERS))


# ---------------------------------------------------------------------------------------------- matching


def _match_schema2(trigger: ReviewTrigger, text: _Text) -> TriggerMatch | None:
    anchors = tuple(keyword for keyword in trigger.keywords if text.has(keyword))
    anchors += tuple(pattern for pattern in trigger.patterns if _regex(pattern).search(text.norm))
    if not anchors:
        return None
    if any(text.has(phrase) for phrase in trigger.unless):
        return None
    if any(not any(text.has(phrase) for phrase in group) for group in trigger.context):
        return None
    if trigger.mode == "missing":
        labels = trigger.requires_labels or tuple(group[0] for group in trigger.requires)
        missing = tuple(
            label
            for label, group in zip(labels, trigger.requires)
            if not any(text.has(phrase) for phrase in group)
        )
        if not missing:
            return None
        return TriggerMatch(trigger, anchors, missing)
    return TriggerMatch(trigger, anchors)


def match_triggers(text: str, triggers: Iterable[ReviewTrigger]) -> list[TriggerMatch]:
    lowered = text.casefold()
    prepared: _Text | None = None
    matches = []
    for trigger in triggers:
        if not trigger.whole_words:
            keywords = tuple(keyword for keyword in trigger.keywords if keyword.casefold() in lowered)
            if keywords:
                matches.append(TriggerMatch(trigger, keywords))
            continue
        if prepared is None:
            prepared = _Text(text)
        match = _match_schema2(trigger, prepared)
        if match is not None:
            matches.append(match)
    return matches


def review_cautions(
    text: str, triggers: Iterable[ReviewTrigger] | None = None, *, limit: int = 2
) -> list[str]:
    """At most ``limit`` one-line cautions for a statement or proof step, one per failure pattern.

    A 'missing' match (a named result whose stated hypotheses are incomplete) ranks before a 'present'
    one; ties go to the trigger with more anchors in the text, then to file order.
    """

    pool = tuple(default_review_triggers() if triggers is None else triggers)
    order = {trigger.id: index for index, trigger in enumerate(pool)}
    found = match_triggers(text, pool)
    found.sort(
        key=lambda m: (m.trigger.mode != "missing", -len(m.matched_keywords), order.get(m.trigger.id, 0))
    )
    lines: list[str] = []
    seen_patterns: set[str] = set()
    for match in found:
        pattern = match.trigger.reflex or match.trigger.id
        if pattern in seen_patterns:
            continue
        seen_patterns.add(pattern)
        line = f"review trigger {match.trigger.id}: {match.trigger.action}"
        if match.missing:
            line += f" Not stated in this text: {'; '.join(match.missing)}."
        if match.trigger.hint:
            line += f" ({match.trigger.hint})"
        lines.append(line)
        if len(lines) >= limit:
            break
    return lines
