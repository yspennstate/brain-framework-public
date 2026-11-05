#!/usr/bin/env python3
"""Extract load-bearing exact clauses from an owner task without retaining owner prose.

The recurring failure this closes is substitution: an agent silently changes ONLY/NOT, an exact
count, an entity role, or the sign/tenor pairing of two option legs.  Association graphs are useful
for recall but unsafe for these operators, so this module is deliberately small and deterministic.

Public output contains only a SHA-256 digest, category names, a count, and an overflow bit.  It never
returns or persists snippets, entity names, counts, or prompt text.  The caller already has the owner
message and can be instructed to copy the matched clauses verbatim into its working checklist.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from typing import Any

TEXT_CAP = 65536
CLAUSE_CAP = 320
MANIFEST_CAP = 12

_GOAL_ENVELOPE = re.compile(r"<(?:codex_internal_context|goal_context)\b[^>]*>(.*?)"
                            r"</(?:codex_internal_context|goal_context)>", re.I | re.S)
_OBJECTIVE = re.compile(r"<objective\b[^>]*>(.*?)</objective>", re.I | re.S)
_FLEET_TASK = re.compile(
    r"(?ms)^\s*Existing scoped task:\s*(.*?)\s*(?=^\s*(?:Containment mode|"
    r"Owner-correction warning digest|Prior-turn correction|Your FINAL answer)\b)"
)
_IGNORED_TAG_BLOCK = re.compile(
    r"(?is)<(?:system-reminder|task-notification|local-command(?:-stdout|-stderr)?|"
    r"tool-result|tool-output)\b[^>]*>.*?</(?:system-reminder|task-notification|"
    r"local-command(?:-stdout|-stderr)?|tool-result|tool-output)>"
)
_FENCE = re.compile(r"(?s)```.*?```")
_INLINE_CODE = re.compile(r"`[^`\n]{0,1000}`")
_QUOTED = re.compile(r'"[^"\n]{0,1000}"|“[^”\n]{0,1000}”|\'[^\'\n]{0,1000}\'|‘[^’\n]{0,1000}’')
_BLOCKQUOTE = re.compile(r"(?m)^\s*>[^\n]*(?:\n|$)")
_ROLE_OR_LOG = re.compile(r"(?im)^\s*(?:assistant|system|tool|task-notification|local-command)\s*:.*$")
_JSON_LINE = re.compile(r"(?m)^\s*[\[{].*[\]}]\s*,?\s*$")
_TAG = re.compile(r"</?[A-Za-z][^>]{0,200}>")
_SEGMENT = re.compile(r"(?m)(?:^|(?<=[.!?])\s+)([^.!?\n]{1,640}(?:[.!?]|$))")

_ACTION = (
    r"(?:add|assign|build|buy|call|change|choose|commit|create|deploy|edit|exclude|give|host|"
    r"delete|implement|include|keep|leave|make|move|name|open|push|put|read|remove|return|run|sell|set|"
    r"show|spawn|switch|touch|update|use|work|write)"
)
_COMMAND = re.compile(
    rf"^\s*(?:(?:please|also|then)\s+)*(?:i\s+(?:want|need|asked)\s+(?:you\s+)?"
    rf"(?:to\s+)?|you\s+(?:must|should|need\s+to|have\s+to)\s+|"
    rf"make\s+sure\s+(?:to\s+)?)?(?P<verb>{_ACTION})\b",
    re.I,
)
_COMMAND_ANYWHERE = re.compile(
    rf"\b(?:please\s+|i\s+(?:want|need|asked)\s+(?:you\s+)?(?:to\s+)?|"
    rf"you\s+(?:must|should|need\s+to|have\s+to)\s+|make\s+sure\s+(?:to\s+)?)"
    rf"(?P<verb>{_ACTION})\b",
    re.I,
)
_DIRECT_ONLY = re.compile(
    rf"(?:^|[;:]\s*)(?:(?:please|you\s+(?:must|should))\s+)?(?:only|just)\s+{_ACTION}\b|"
    rf"(?:^|[;:]\s*)(?:please\s+)?{_ACTION}\b[^.;!?]{{0,100}}\b(?:only|exclusively|nothing\s+else|"
    r"nothing\s+but|except)\b",
    re.I,
)
_NOT_ONLY = re.compile(r"\bnot\s+only\b", re.I)
_DIRECT_NEGATION = re.compile(
    rf"(?:^|[;:]\s*)(?:please\s+)?(?:you\s+)?(?:do\s+not|don['’]?t|never|"
    rf"must\s+not|should\s+not)\s+(?:ever\s+)?{_ACTION}\b|"
    rf"(?:^|[;:]\s*)under\s+no\s+circumstances\s+(?:ever\s+)?{_ACTION}\b|"
    r"(?:^|[;:]\s*)no\s+(?:buying|selling|changing|using|deleting|adding|removing)\b",
    re.I,
)
_CONTRAST = re.compile(
    r"\bnot\s+[^,;.!?]{1,80}\s*,?\s+(?:but|rather|use)\b|"
    r"\b(?:instead\s+of|rather\s+than)\b",
    re.I,
)
_COMMAND_ALTERNATIVE = re.compile(r"(?:^|[,;:]\s*)not\s+[^.;!?]{1,100}$", re.I)

_COUNT_WORD = (r"(?:\d{1,3}|zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|"
               r"twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty)")
_COUNT_NOUN = (
    r"(?:agents?|subagents?|workers?|songs?|items?|files?|graphs?|charts?|accounts?|tests?|"
    r"pages?|slides?|options?|examples?|variants?|sections?|columns?|rows?|hours?|days?|"
    r"chapters?|folders?|teams?)"
)
_COUNT_PHRASE = re.compile(
    rf"\b(?:exactly\s+)?{_COUNT_WORD}(?:\s*[-]\s*|\s+){_COUNT_NOUN}\b",
    re.I,
)
_COUNT_DIRECTIVE = re.compile(
    rf"^\s*(?:(?:there\s+)?(?:must|should)\s+be\s+|i\s+(?:want|need|asked\s+for)\s+|"
    rf"exactly\s+)(?:exactly\s+)?{_COUNT_WORD}(?:\s*[-]\s*|\s+){_COUNT_NOUN}\b",
    re.I,
)
_COUNT_NEGATION = re.compile(rf"[,;]\s*not\s+(?:exactly\s+)?{_COUNT_WORD}\b", re.I)

_TARGET_NOUN = (
    r"(?:desktop\s+app|api|website|dashboard|prod\s+stack|aws|azure|local(?:ly)?|"
    r"box|observers?|portfolio\s+margin|reg(?:ulation)?\s+[a-z])"
)
_TARGET_CONTRAST = re.compile(
    rf"\b{_TARGET_NOUN}\b[^.;!?]{{0,100}}\b(?:not|instead\s+of|rather\s+than|no)\b"
    rf"[^.;!?]{{0,80}}(?:\b{_TARGET_NOUN}\b)?",
    re.I,
)
_ROLE = r"(?:manager|master|sub(?:account)?|parent|child)"
_ENTITY = r"(?:[A-Za-z0-9][A-Za-z0-9_.-]{0,30})"
_ROLE_ASSIGN = re.compile(
    rf"\b{_ROLE}\b\s*(?:=|:|\bto\b|\bmust\s+be\b)\s*{_ENTITY}\b|"
    rf"\b(?:set|assign|make)\s+(?:the\s+)?{_ROLE}\b\s*(?:=|:|\bto\b|\bas\b)?\s*{_ENTITY}\b|"
    rf"\b{_ENTITY}\s+(?:must\s+be|is)\s+(?:the\s+)?{_ROLE}\b|"
    rf"\buse\s+{_ENTITY}\s+as\s+(?:the\s+)?{_ROLE}\b",
    re.I,
)
_NO_FIELD = re.compile(r"\bno\s+(?:account\s+)?(?:number|id|identifier)\b", re.I)

_INSTRUMENT = re.compile(r"\b(?:puts?|calls?|options?|leaps?|legs?)\b", re.I)
_LONG = re.compile(r"\blong\b", re.I)
_SHORT = re.compile(r"\bshort\b", re.I)
_SHORTER_TENOR = re.compile(
    r"\b(?:shorter(?:[ -]dated)?|short[ -]dated|near(?:er)?(?:\s+expiry)?|earlier|front(?:[ -]month)?)\b",
    re.I,
)
_LONGER_TENOR = re.compile(
    r"\b(?:longer(?:[ -]dated)?|long[ -]dated|far(?:ther)?(?:\s+expiry)?|later|back(?:[ -]month)?)\b",
    re.I,
)
_LONG_DIRECTION = re.compile(r"\b(?:long|buy)\b", re.I)
_SHORT_DIRECTION = re.compile(r"\b(?:short|sell)\b", re.I)
_OPTION_ACTION = re.compile(r"\b(?:buy|sell|buying|selling|long|short)\b", re.I)
_EXPIRY = re.compile(r"\b(?:expiry|expiration|tenor)\b", re.I)
_LEG_ENTITY = re.compile(r"\b[A-Z][A-Z0-9]{0,5}\b")
_LEG_ENTITY_NOISE = frozenset(
    "A AN AND BUY CALL CALLS FOR I LEAP LEAPS LONG ONLY OPTION OPTIONS OR PUT PUTS SELL SHORT THE USE".split()
)
_CODE_CONTEXT = re.compile(
    r"\b(?:parser|cli|command[- ]line|source\s+code|function|class|argument|flag|regex|fixture|unit\s+test)\b",
    re.I,
)

_CATEGORY_ORDER = ("only", "not", "count", "target", "role", "leg", "tenor", "sign")


def _owner_task(value: Any) -> str:
    text = value if isinstance(value, str) else "" if value is None else str(value)
    text = text[:TEXT_CAP]
    # A whole JSON task object is an owner task, not a pasted log line.  Parse only recognized
    # string keys and otherwise leave JSON to the conservative line suppressor below.
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            for key in ("objective", "task", "prompt"):
                if isinstance(parsed.get(key), str) and parsed[key].strip():
                    text = parsed[key]
                    break
    except Exception:
        pass
    # Redact untrusted examples BEFORE looking for an objective tag, so a fenced/quoted/reminder
    # `<objective>` cannot hijack selection.  Only recognized goal envelopes may unwrap objective.
    text = _IGNORED_TAG_BLOCK.sub(" ", text)
    text = _FENCE.sub(" ", text)
    text = _INLINE_CODE.sub(" ", text)
    text = _QUOTED.sub(" ", text)
    text = _BLOCKQUOTE.sub(" ", text)
    text = _ROLE_OR_LOG.sub(" ", text)
    selected = ""
    for envelope in _GOAL_ENVELOPE.findall(text):
        objective = _OBJECTIVE.search(envelope)
        if objective:
            selected = objective.group(1)
            break
    if selected:
        text = selected
    else:
        fleet = _FLEET_TASK.search(text)
        if fleet:
            text = fleet.group(1)
    text = _JSON_LINE.sub(" ", text)
    return _TAG.sub(" ", text)


def _overflow_risk(value: Any) -> bool:
    original = value if isinstance(value, str) else "" if value is None else str(value)
    if len(original) > TEXT_CAP:
        return True
    task = _owner_task(original)
    for raw in re.split(r"(?<=[.!?])\s+|\n", task):
        if len(raw) <= CLAUSE_CAP:
            continue
        # `_segments` intentionally refuses an oversized clause. Search the full bounded clause here,
        # including its tail, so that refusal becomes an explicit overflow advisory rather than silence.
        if (_COMMAND.search(raw) or _COMMAND_ANYWHERE.search(raw) or _DIRECT_ONLY.search(raw)
                or _DIRECT_NEGATION.search(raw) or _COUNT_DIRECTIVE.search(raw)
                or _ROLE_ASSIGN.search(raw)):
            return True
    return False


def _segments(text: str) -> list[tuple[int, int, str]]:
    out = []
    for match in _SEGMENT.finditer(text):
        raw = match.group(1).strip()
        if not raw or len(raw) > CLAUSE_CAP:
            continue
        cleaned = re.sub(r"^\s*(?:[-*+]\s+|\d{1,3}[.)]\s+|#{1,6}\s+)", "", raw)
        if cleaned:
            out.append((match.start(1), match.end(1), cleaned))
    return out


def _near_each_other(left: re.Pattern[str], right: re.Pattern[str], text: str, distance: int = 96) -> bool:
    return any(abs(a.start() - b.start()) <= distance for a in left.finditer(text) for b in right.finditer(text))


def _paired_leg(clause: str) -> bool:
    """Require both bound sides inside one clause and reject two-entity/scenario composites."""
    if _CODE_CONTEXT.search(clause) or not (_INSTRUMENT.search(clause) or _EXPIRY.search(clause)):
        return False
    entities = {m.group(0) for m in _LEG_ENTITY.finditer(clause)
                if m.group(0) not in _LEG_ENTITY_NOISE}
    if len(entities) > 1:
        return False
    return (_near_each_other(_LONG_DIRECTION, _SHORTER_TENOR, clause)
            and _near_each_other(_SHORT_DIRECTION, _LONGER_TENOR, clause))


def _detect(value: Any) -> list[tuple[int, int, str, tuple[str, ...]]]:
    text = _owner_task(value)
    if not text.strip():
        return []
    found = []
    for start, end, clause in _segments(text):
        categories = set()
        command = bool(_COMMAND.search(clause) or _COMMAND_ANYWHERE.search(clause))
        if not _NOT_ONLY.search(clause) and _DIRECT_ONLY.search(clause):
            categories.update(("only", "target"))
        if _DIRECT_NEGATION.search(clause):
            categories.add("not")
        if command and not _NOT_ONLY.search(clause) and _CONTRAST.search(clause):
            categories.update(("not", "target"))
        if _COUNT_PHRASE.search(clause) and (command or _COUNT_DIRECTIVE.search(clause)):
            categories.add("count")
        if "count" in categories and _COUNT_NEGATION.search(clause):
            categories.add("not")
        role_assignments = list(_ROLE_ASSIGN.finditer(clause))
        if role_assignments:
            categories.add("role")
        targets = list(re.finditer(rf"\b{_TARGET_NOUN}\b", clause, re.I))
        if _TARGET_CONTRAST.search(clause) and (command or len(targets) >= 2 or role_assignments):
            categories.update(("not", "target"))
        if _OPTION_ACTION.search(clause) and _INSTRUMENT.search(clause) and categories.intersection({"only", "not"}):
            categories.add("sign")
        if _paired_leg(clause):
            categories.update(("leg", "sign", "tenor"))
        if categories:
            found.append((start, end, clause, tuple(sorted(categories))))

    # One warning per exact clause.  Merge overlapping detectors and discard duplicate normalized prose.
    merged: dict[tuple[int, int], tuple[str, set[str]]] = {}
    for start, end, clause, categories in found:
        key = (start, end)
        if key not in merged:
            merged[key] = (clause, set())
        merged[key][1].update(categories)
    out, seen = [], set()
    for (start, end), (clause, categories) in sorted(merged.items()):
        normalized = re.sub(r"\s+", " ", clause).strip().casefold()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        out.append((start, end, normalized, tuple(c for c in _CATEGORY_ORDER if c in categories)))
    return out


def analyze(value: Any) -> dict[str, Any]:
    """Return privacy-safe metadata only; never return the matched clauses."""
    clauses = _detect(value)
    bounded_overflow = _overflow_risk(value)
    if not clauses and not bounded_overflow:
        return {"digest": "", "categories": (), "count": 0, "overflow": False, "warning_id": ""}
    material = "\x1e".join(clause for _start, _end, clause, _categories in clauses)
    if bounded_overflow:
        task_digest = hashlib.sha256(_owner_task(value).encode("utf-8", "replace")).hexdigest()
        material += "\x1e<bounded-overflow>\x1e" + task_digest
    digest = hashlib.sha256(material.encode("utf-8", "replace")).hexdigest()[:12]
    categories = tuple(c for c in _CATEGORY_ORDER
                       if any(c in item[3] for item in clauses))
    overflow = bounded_overflow or len(clauses) > MANIFEST_CAP
    return {"digest": digest, "categories": categories, "count": len(clauses),
            "overflow": overflow, "warning_id": "owner_clause:" + digest}


_MATH_TOKEN = re.compile(r"\$[^$]{1,400}\$|\\\[|\\begin\{|\\(?:frac|sum|int|mathbb|mathcal|leq|geq|infty|lim|sup|inf|alpha|beta|lambda|sigma|varepsilon|epsilon)\b")
_NUMBERED_STEP = re.compile(r"(?m)^\s*(?:\[step\s+)?\d{1,2}[.)\]]\s+\S")


def _is_math_argument(value: Any) -> bool:
    """A numbered mathematical argument (a proof under audit) is not an owner instruction list.

    2026-09-07: on the probability proof bank this hook fired on nearly every item - a proof reads as a
    numbered list of clauses - and told the solver to copy the steps verbatim and never 'swap or infer',
    the opposite of auditing them; the brain arm called flawed proofs valid more often than the sterile
    arm. Math density plus numbered steps means: stay silent, the proof-audit lane speaks instead."""
    text = _owner_task(value)
    if len(text) < 200:
        return False
    return len(_MATH_TOKEN.findall(text)) >= 6 and len(_NUMBERED_STEP.findall(text)) >= 3


def render(value: Any) -> str:
    if _is_math_argument(value):
        return ""
    info = analyze(value)
    if not info["count"] and not info["overflow"]:
        return ""
    categories = ",".join(info["categories"]) or "exact"
    header = "[exact-owner-clauses digest=%s categories=%s n=%d]" % (
        info["digest"], categories, info["count"]
    )
    if info["overflow"]:
        return (header + " This prompt exceeds the bounded exact-clause parser. Before acting, "
                "manually restate every exact owner constraint; do not emit a partial checklist, and "
                "ask before any ambiguous mutation.")
    return (header + " This prompt contains load-bearing exact clauses. Before acting, copy each one "
            "verbatim from the owner message into your working checklist. Do not optimize, rename, swap, "
            "or infer a nearby structure; ambiguity requires a question before mutation.")


def _selftest() -> int:
    positives = [
        ("Only buy puts; do not sell options.", {"only", "not", "sign"}),
        ("Use 7 subagents, not 3.", {"count", "not"}),
        ("Only change observers.", {"only", "target"}),
        ("Build the desktop app, not an API.", {"target", "not"}),
        ("Create the website dashboard, not the prod stack.", {"target", "not"}),
        ("Set manager AAA; no id number.", {"role"}),
        ("Set master=AAA and sub=BBB; use portfolio margin, not Reg D.", {"role", "target", "not"}),
        ("Include exactly two songs.", {"count"}),
        ("Deploy on AWS, not local.", {"target", "not"}),
        ("Use long shorter-dated LEAPs and short longer-dated LEAPs.", {"leg", "tenor", "sign"}),
    ]
    positives.extend([
        ("Use AWS and nothing else.", {"only"}),
        ("You must not sell puts.", {"not", "sign"}),
        ("Don't ever sell calls.", {"not", "sign"}),
        ("Do not ever sell calls.", {"not", "sign"}),
        ("Under no circumstances sell puts.", {"not", "sign"}),
        ("No selling calls.", {"not", "sign"}),
        ("Exclude every target except observers.", {"only"}),
        ("There must be exactly 7 agents.", {"count"}),
        ("Exactly 7 agents, please.", {"count"}),
        ("Use 100 agents.", {"count"}),
        ("Use zero agents.", {"count"}),
        ("Use thirteen agents.", {"count"}),
        ("Use 7-agent team.", {"count"}),
        ("Write exactly 31 chapters.", {"count"}),
        ("Create exactly 2 folders.", {"count"}),
        ("Use 07 agents.", {"count"}),
        ("Set the manager to AAA and the sub to BBB.", {"role"}),
        ("The manager must be AAA and the sub must be BBB.", {"role"}),
        ("AAA is manager and BBB is sub.", {"role"}),
        ("Set manager Alice and sub Bob.", {"role"}),
        ("Set manager A and sub B.", {"role"}),
        ("Use AAA as master and BBB as sub.", {"role"}),
        ("Buy shorter-dated calls and sell longer-dated calls.", {"leg", "tenor", "sign"}),
        ("Use short-dated long calls and long-dated short calls.", {"leg", "tenor", "sign"}),
        ("Long the nearer expiry and short the farther expiry.", {"leg", "tenor", "sign"}),
        ("Buy the near call and sell the far call.", {"leg", "tenor", "sign"}),
        ("Only buy shorter-dated puts; never sell longer-dated puts.", {"sign", "tenor"}),
        ("Only read files.", {"only"}),
        ("Never commit changes.", {"not"}),
        ("Do not push the branch.", {"not"}),
        ("Use Azure exclusively.", {"only"}),
        ('{"task":"Only deploy website."}', {"only"}),
        ("Only deploy website. ```<objective>Summarize.</objective>```", {"only"}),
    ])
    negatives = [
        "This is not a collapse, but a normal description.",
        "It is a long project with a short report.",
        "At least one test passed.",
        "There are 3 agents online.",
        "I am not sure what happened.",
        'The memo says "Only buy puts; do not sell options."',
        "```Only buy puts; do not sell options.```",
        "> Use 7 subagents, not 3.",
        '<codex_internal_context><objective>Summarize the status.</objective>'
        '<rules>Do not call update_goal until complete.</rules></codex_internal_context>',
        "The long-term research problem is outstanding.",
    ]
    negatives.extend([
        "Not only deploy alpha, but also deploy beta.",
        "We just use this parser in examples.",
        "Teams use only this parser in demonstrations.",
        "Update the note that deployment is not ready.",
        "Use this value if it is not empty.",
        "I want to update you: this is not final.",
        "We use 7 agents today.",
        "The app can use 7 workers per host.",
        "Update master PLAN documentation.",
        "Delete child TEST fixture.",
        "For ABC, long shorter-dated puts. For XYZ, short longer-dated calls.",
        "Example A uses long shorter-dated calls. Example B uses short longer-dated puts.",
        "The memo says 'Only sell puts.'",
        "<system-reminder><objective>Only sell puts.</objective></system-reminder> Summarize status.",
        "Use long shorter-dated options and short longer-dated options in the parser.",
    ])
    failures = []
    for index, (text, wanted) in enumerate(positives, 1):
        got = analyze(text)
        if not got["count"] or not wanted.issubset(set(got["categories"])):
            failures.append("P%02d" % index)
    for index, text in enumerate(negatives, 1):
        if analyze(text)["count"]:
            failures.append("N%02d" % index)
    wrapped = analyze("<goal_context><objective>Use 7 subagents, not 3.</objective>"
                      "<rules>Never finish early.</rules></goal_context>")
    if not wrapped["count"] or "count" not in wrapped["categories"]:
        failures.append("objective-isolation")
    fleet = render("FLEET CONTINUITY ENVELOPE v1\nExisting scoped task: Only change observers.\n"
                   "Containment mode: READ-ONLY. Do not mutate files.")
    if "exact-owner-clauses" not in fleet:
        failures.append("fleet-isolation")
    stable_a = analyze("Use   7 subagents, not 3.")
    stable_b = analyze("Use 7 subagents, not 3.")
    if stable_a["digest"] != stable_b["digest"]:
        failures.append("digest-stability")
    private = analyze("Set master=AAA and sub=BBB.")
    if set(private) != {"digest", "categories", "count", "overflow", "warning_id"}:
        failures.append("privacy-schema")
    if any(token in render("Set master=AAA and sub=BBB.") for token in ("AAA", "BBB")):
        failures.append("render-leak")
    overflow = analyze("\n".join("Do not delete file%d." % i for i in range(13)))
    if not overflow["overflow"] or overflow["count"] != 13:
        failures.append("overflow")
    if not analyze("Only deploy website " + "x" * 400 + ".")["overflow"]:
        failures.append("long-clause-overflow")
    if not analyze("x" * 400 + "; Only deploy website.")["overflow"]:
        failures.append("long-clause-tail-overflow")
    if not analyze("Summarize. " + "x" * TEXT_CAP + " Only deploy website.")["overflow"]:
        failures.append("tail-overflow")
    for junk in (None, 123, {}, []):
        try:
            analyze(junk)
            render(junk)
        except Exception:
            failures.append("failsafe")
            break
    print("exact_clauses selftest:", "PASS" if not failures else "FAIL " + ",".join(failures))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(_selftest())
