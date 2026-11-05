"""Clause-complete prompt projection.

The projection partitions the entire trusted prompt payload.  Every character is
covered by a content, separator, or explicit unparsed span; semantic clauses are an
overlay and may never erase a base span.  Persisted features are keyed digests, not
prompt words.
"""
from __future__ import annotations

import re
from typing import Callable, Iterable

MAX_PROMPT_CHARS = 200_000
MAX_ACCEPTED_CHARS = 1_000_000
MAX_SEMANTIC_SPANS = 256
MAX_SPAN_CHARS = 2_048

_CUT = re.compile(
    r"(?=<system-reminder>)|(?<=</system-reminder>)|(?<=[.!?;])\s+|[\r\n]+|,\s+(?=(?:and\s+also|but\s+also|also|then|"
    r"make\s+sure|do\s+not|don['’]?t|never|please|use|keep|work|implement|"
    r"add|remove|read|review|audit|check|verify)\b)|"
    r"\s+(?=(?:and\s+also|but\s+also|also|then|make\s+sure)\b)",
    re.I,
)
_SYSTEM_REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.I | re.S)
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_.:/-]{1,63}")
_STOP = frozenset(
    "the a an and or of to in on for by is are was were be been it its this that these "
    "those with from as at we you they them our your my me do did if then so but can could "
    "may might would will what which when where why how who just also here there".split()
)

_PROHIBITION = re.compile(r"(?i)\b(?:do\s+not|don['’]?t|never|must\s+not|should\s+not|without)\b")
_CADENCE = re.compile(r"(?i)\b(?:every|each|hourly|daily|weekly|for\s+(?:the\s+next\s+)?\d+\s*(?:minutes?|hours?|days?)|until|through)\b")
_CONDITION = re.compile(r"(?i)^\s*(?:if|when|whenever|unless|after|before|while)\b")
_QUESTION = re.compile(r"(?i)(?:\?|^\s*(?:what|why|how|when|where|who|can|could|should|is|are|do|does)\b)")
_DELIVERABLE = re.compile(
    r"(?i)\b(?:build|implement|create|write|make|add|remove|change|update|push|deploy|"
    r"read|review|audit|check|verify|classify|record|keep|work|use|include|exclude|"
    r"test|run|monitor|surface|fire)\b"
)
_CONTEXT = re.compile(r"(?i)^\s*(?:because|given|background|context|for\s+reference|i\s+am|i['’]m)\b")
_PRIORITY = re.compile(r"(?i)\b(?:must|every|each|exactly|all|never|do\s+not|don['’]?t|make\s+sure|critical|before)\b")

_OP_PATTERNS = (
    ("delete", re.compile(r"(?i)\b(?:delete|remove|erase)\b")),
    ("write", re.compile(r"(?i)\b(?:write|edit|change|modify|overwrite)\b")),
    ("push", re.compile(r"(?i)\b(?:push|publish|release)\b")),
    ("deploy", re.compile(r"(?i)\b(?:deploy|launch|start)\b")),
    ("execute", re.compile(r"(?i)\b(?:run|execute|invoke)\b")),
    ("network", re.compile(r"(?i)\b(?:send|message|email|upload|download)\b")),
)


def _kind(raw: str) -> str:
    if not raw:
        return "separator"
    if raw.isspace() or not any(ch.isalnum() for ch in raw):
        return "separator"
    return "content"


def _append_span(out: list[dict], start: int, end: int, raw: str, kind: str | None = None) -> None:
    if end <= start:
        return
    out.append({"start": start, "end": end, "raw": raw, "kind": kind or _kind(raw)})


def partition_spans(value: object) -> tuple[list[dict], bool]:
    """Return a no-gap partition and an overflow flag.

    Oversized content is represented as bounded ``unparsed`` chunks instead of being
    dropped.  Text beyond the hard cap becomes one final ``unparsed_overflow`` span.
    """
    text = value if isinstance(value, str) else "" if value is None else str(value)
    if len(text) > MAX_ACCEPTED_CHARS:
        raise ValueError("prompt_too_large_for_clause_complete_projection")
    overflow = len(text) > MAX_PROMPT_CHARS
    bounded = text[:MAX_PROMPT_CHARS]
    spans: list[dict] = []
    cursor = 0
    semantic_count = 0
    for match in _CUT.finditer(bounded):
        if match.start() > cursor:
            raw = bounded[cursor:match.start()]
            if len(raw) <= MAX_SPAN_CHARS and semantic_count < MAX_SEMANTIC_SPANS:
                _append_span(spans, cursor, match.start(), raw)
                semantic_count += 1
            else:
                for pos in range(cursor, match.start(), MAX_SPAN_CHARS):
                    end = min(match.start(), pos + MAX_SPAN_CHARS)
                    _append_span(spans, pos, end, bounded[pos:end], "unparsed")
                overflow = True
        _append_span(spans, match.start(), match.end(), bounded[match.start():match.end()], "separator")
        cursor = match.end()
    if cursor < len(bounded):
        raw = bounded[cursor:]
        if len(raw) <= MAX_SPAN_CHARS and semantic_count < MAX_SEMANTIC_SPANS:
            _append_span(spans, cursor, len(bounded), raw)
        else:
            for pos in range(cursor, len(bounded), MAX_SPAN_CHARS):
                end = min(len(bounded), pos + MAX_SPAN_CHARS)
                _append_span(spans, pos, end, bounded[pos:end], "unparsed")
            overflow = True
    if len(text) > len(bounded):
        for pos in range(len(bounded), len(text), MAX_SPAN_CHARS):
            end = min(len(text), pos + MAX_SPAN_CHARS)
            _append_span(spans, pos, end, text[pos:end], "unparsed_overflow")
    if not spans and text:
        _append_span(spans, 0, len(text), bounded, "content")
    if not text:
        spans = []
    reminder_ranges = [(match.start(), match.end()) for match in _SYSTEM_REMINDER.finditer(text)]
    for span in spans:
        if any(span["start"] >= start and span["end"] <= end for start, end in reminder_ranges):
            span["kind"] = "system_inert"
    validate_partition(spans, len(text))
    return spans, overflow


def validate_partition(spans: Iterable[dict], text_length: int) -> None:
    cursor = 0
    for span in spans:
        if int(span["start"]) != cursor or int(span["end"]) < cursor:
            raise ValueError("coverage spans must form one ordered, gap-free partition")
        cursor = int(span["end"])
    if cursor != text_length:
        raise ValueError("coverage spans do not cover the complete prompt")


def role_for(raw: str, kind: str) -> tuple[str, bool, float]:
    if kind == "separator":
        return "separator", False, 0.0
    if kind == "system_inert":
        return "system_context", False, 0.0
    if kind.startswith("unparsed"):
        return "unknown", True, 3.0
    prohibition_matches = list(_PROHIBITION.finditer(raw))
    def quoted(match) -> bool:
        before = raw[:match.start()]
        after = raw[match.end():]
        return ((before.count('"') % 2 == 1 and '"' in after)
                or (before.count("'") % 2 == 1 and "'" in after)
                or (before.count("“") > before.count("”") and "”" in after))
    if prohibition_matches and not all(quoted(match) for match in prohibition_matches):
        return "prohibition", True, 3.5
    if _CADENCE.search(raw):
        return "duration_cadence", True, 3.25
    if _CONDITION.search(raw):
        return "condition", True, 2.25
    if _QUESTION.search(raw):
        return "question", True, 1.5
    if _DELIVERABLE.search(raw):
        return "obligation", True, 2.5 if _PRIORITY.search(raw) else 2.0
    if _CONTEXT.search(raw):
        return "context", False, 0.35
    return "unknown", True, 1.0


def feature_digests(raw: str, keyed: Callable[[str, str], str]) -> tuple[str, ...]:
    """Keyed exact-token and boundary-trigram features for private-text alignment."""
    out = set()
    for word in _WORD.findall(raw)[:256]:
        whole = word.lower().strip("._/: -")
        tokens = [whole] + [part for part in re.split(r"[._/:\\-]+", whole) if part != whole]
        for token in tokens:
            if len(token) < 2 or token in _STOP:
                continue
            out.add("lexh:" + keyed("lexeme", token)[:24])
            bounded = "<" + token + ">"
            if len(bounded) >= 3:
                out.add("subh:" + keyed("char3", bounded[:3])[:16])
                out.add("subh:" + keyed("char3", bounded[-3:])[:16])
    return tuple(sorted(out))


def forbidden_ops(raw: str, role: str) -> tuple[str, ...]:
    if role != "prohibition":
        return ()
    matched = tuple(name for name, pattern in _OP_PATTERNS if pattern.search(raw))
    return matched or ("read", "write", "delete", "push", "deploy", "execute", "network")


def exact_categories(raw: str) -> tuple[str, ...]:
    """Independently authored structural categories, never a learned rule pack."""
    out = []
    if _PROHIBITION.search(raw):
        out.append("prohibition")
    if _CADENCE.search(raw):
        out.append("cadence_or_duration")
    if _CONDITION.search(raw):
        out.append("condition")
    if _QUESTION.search(raw):
        out.append("question")
    if _DELIVERABLE.search(raw):
        out.append("deliverable")
    if _PRIORITY.search(raw):
        out.append("priority")
    return tuple(out)


def project_prompt(value: object, keyed: Callable[[str, str], str]) -> tuple[list[dict], list[dict], bool]:
    """Return persisted-safe span and clause projections plus overflow state."""
    spans, overflow = partition_spans(value)
    safe_spans: list[dict] = []
    clauses: list[dict] = []
    for ordinal, span in enumerate(spans):
        raw = span["raw"]
        span_id = "span:" + keyed("span", "%d:%d:%s" % (span["start"], span["end"], raw))[:32]
        safe_spans.append({
            "span_id": span_id,
            "ordinal": ordinal,
            "start": span["start"],
            "end": span["end"],
            "kind": span["kind"],
            "span_hmac": keyed("span-bytes", raw),
        })
        if span["kind"] == "separator":
            continue
        role, actionable, weight = role_for(raw, span["kind"])
        clauses.append({
            "clause_id": "clause:" + keyed("clause", "%d:%d:%s" % (span["start"], span["end"], raw))[:32],
            "span_id": span_id,
            "ordinal": len(clauses),
            "role": role,
            "actionable": actionable,
            "weight": weight,
            "features": feature_digests(raw, keyed),
            "exact_categories": exact_categories(raw),
            "forbidden_ops": forbidden_ops(raw, role),
            "raw": raw,  # transient only; store.py removes it before persistence
        })
    return safe_spans, clauses, overflow
