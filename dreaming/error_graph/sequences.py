#!/usr/bin/env python3
"""Explicit temporal motif matching and warning follow-through."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

try:
    from .events import Event
except ImportError:
    from events import Event


@dataclass(frozen=True)
class Step:
    all_features: Tuple[str, ...] = ()
    any_features: Tuple[str, ...] = ()
    none_features: Tuple[str, ...] = ()
    same_action_as: Optional[int] = None

    @classmethod
    def from_mapping(cls, row: Mapping[str, Any]) -> "Step":
        return cls(tuple(row.get("all") or ()), tuple(row.get("any") or ()),
                   tuple(row.get("none") or ()), row.get("same_action_as"))


@dataclass(frozen=True)
class Pattern:
    pattern_id: str
    family: str
    steps: Tuple[Step, ...]
    within_seconds: float
    forbid_between: Tuple[str, ...]
    score: float
    reason_code: str
    remediation_code: str
    severity: float = 0.5
    min_independent_sessions: int = 0
    status: str = "curated"

    @classmethod
    def from_mapping(cls, row: Mapping[str, Any]) -> "Pattern":
        return cls(
            pattern_id=str(row["id"]), family=str(row["family"]),
            steps=tuple(Step.from_mapping(step) for step in row.get("steps", ())),
            within_seconds=float(row.get("within_seconds", 1800)),
            forbid_between=tuple(row.get("forbid_between") or ()), score=float(row.get("score", 1.0)),
            reason_code=str(row.get("reason_code", row["id"])),
            remediation_code=str(row.get("remediation_code", "verify_before_continuing")),
            severity=float(row.get("severity", 0.5)),
            min_independent_sessions=int(row.get("min_independent_sessions", 0)),
            status=str(row.get("status", "curated")),
        )


@dataclass(frozen=True)
class SequenceMatch:
    pattern_id: str
    family: str
    score: float
    reason_code: str
    remediation_code: str
    severity: float
    event_ids: Tuple[str, ...]
    evidence_path: Tuple[Tuple[str, str, str], ...]


@dataclass(frozen=True)
class WarningResolution:
    trace_id: str
    status: str
    reason_code: str


def _view(event: Event | Mapping[str, Any]) -> Dict[str, Any]:
    if isinstance(event, Event):
        return {"event_id": event.event_id, "event_ts": event.event_ts,
                "features": set(event.features),
                "action_fingerprint": str(event.attrs.get("shape_fingerprint") or event.action_fingerprint),
                "surface": event.surface, "kind": event.kind}
    return {"event_id": str(event.get("event_id", "")), "event_ts": float(event.get("event_ts", 0.0)),
            "features": set(event.get("features") or ()),
            "action_fingerprint": str((event.get("attrs") or {}).get("shape_fingerprint")
                                      or event.get("action_fingerprint", "")),
            "surface": str(event.get("surface", "")), "kind": str(event.get("kind", ""))}


def _step_matches(step: Step, candidate: Mapping[str, Any], selected: Sequence[Mapping[str, Any]]) -> bool:
    features = candidate["features"]
    if step.all_features and not set(step.all_features).issubset(features):
        return False
    if step.any_features and not set(step.any_features).intersection(features):
        return False
    if step.none_features and set(step.none_features).intersection(features):
        return False
    if step.same_action_as is not None:
        index = int(step.same_action_as)
        if index < 0 or index >= len(selected):
            return False
        if not candidate.get("action_fingerprint") or candidate.get("action_fingerprint") != selected[index].get("action_fingerprint"):
            return False
    return True


def match_pattern(pattern: Pattern, history: Sequence[Event | Mapping[str, Any]],
                  current: Event | Mapping[str, Any]) -> Optional[SequenceMatch]:
    """Find an ordered occurrence ending at current; no future events are visible."""
    if not pattern.steps:
        return None
    cur = _view(current)
    if pattern.steps[-1].same_action_as is None and not _step_matches(pattern.steps[-1], cur, ()):
        return None
    candidates = [_view(item) for item in history if _view(item)["event_ts"] <= cur["event_ts"]]
    candidates.append(cur)
    final_index = len(candidates) - 1
    chosen: Dict[int, Tuple[int, Mapping[str, Any]]] = {len(pattern.steps) - 1: (final_index, cur)}
    forbidden = set(pattern.forbid_between)
    states = [0]

    def complete_valid() -> bool:
        ordered_rows = [chosen[i][1] for i in range(len(pattern.steps))]
        for index, step in enumerate(pattern.steps):
            if step.same_action_as is None:
                continue
            ref = int(step.same_action_as)
            if ref < 0 or ref >= len(ordered_rows) \
                    or ordered_rows[index].get("action_fingerprint") != ordered_rows[ref].get("action_fingerprint"):
                return False
        lo = min(item[0] for item in chosen.values())
        hi = max(item[0] for item in chosen.values())
        return not any(forbidden.intersection(candidate["features"]) for candidate in candidates[lo + 1:hi])

    def search(step_index: int, search_before: int) -> bool:
        # Hard bound protects the hook even if future pattern files grow.
        if states[0] >= 4096:
            return False
        if step_index < 0:
            return complete_valid()
        step = pattern.steps[step_index]
        for candidate_index in range(search_before - 1, -1, -1):
            states[0] += 1
            candidate = candidates[candidate_index]
            if cur["event_ts"] - candidate["event_ts"] > pattern.within_seconds:
                break
            if not _step_matches(Step(step.all_features, step.any_features, step.none_features, None),
                                 candidate, ()):
                continue
            chosen[step_index] = (candidate_index, candidate)
            if search(step_index - 1, candidate_index):
                return True
            chosen.pop(step_index, None)
        return False

    if not search(len(pattern.steps) - 2, final_index):
        return None
    ordered = [chosen[i][1] for i in range(len(pattern.steps))]

    event_ids = tuple(item["event_id"] for item in ordered)
    path = []
    for left, right in zip(event_ids, event_ids[1:]):
        path.append(("event:" + left, "PRECEDES_WITHIN", "event:" + right))
    if event_ids:
        path.append(("event:" + event_ids[-1], "MATCHES_SEQUENCE", "motif:" + pattern.pattern_id))
        path.append(("motif:" + pattern.pattern_id, "INDICATES", "family:" + pattern.family))
    return SequenceMatch(pattern.pattern_id, pattern.family, pattern.score, pattern.reason_code,
                         pattern.remediation_code, pattern.severity, event_ids, tuple(path))


class SequenceMatcher:
    def __init__(self, patterns: Iterable[Pattern]):
        self.patterns = tuple(patterns)

    @classmethod
    def from_json(cls, path: str) -> "SequenceMatcher":
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
        return cls(Pattern.from_mapping(row) for row in payload.get("patterns", ()))

    def match(self, history: Sequence[Event | Mapping[str, Any]], current: Event | Mapping[str, Any]) -> Tuple[SequenceMatch, ...]:
        matches = []
        for pattern in self.patterns:
            result = match_pattern(pattern, history, current)
            if result is not None:
                matches.append(result)
        return tuple(sorted(matches, key=lambda item: (-item.score, item.pattern_id)))


def load_default_matcher() -> SequenceMatcher:
    return SequenceMatcher.from_json(os.path.join(os.path.dirname(os.path.abspath(__file__)), "patterns.json"))


_VERIFYING = {"op:verify", "op:test", "op:brain_recall", "op:read"}
_ACTING = {"op:execute", "op:mutate", "op:external", "op:delete", "act:assert", "act:complete", "act:recommend"}


def _targets(features: Iterable[str]) -> set[str]:
    return {feature for feature in features if str(feature).startswith("targeth:")}


def resolve_follow_through(pending: Sequence[Mapping[str, Any]], current: Event) -> Tuple[WarningResolution, ...]:
    """Classify only clear evidence.  Silence/changed action remains pending."""
    resolutions = []
    features = set(current.features)
    for warning in pending:
        same_episode = warning.get("warning_episode_id") == current.episode_id
        warning_targets = _targets(warning.get("warning_features") or ())
        current_targets = _targets(features)
        relevant_target = bool(warning_targets and warning_targets.intersection(current_targets))
        repeated = warning.get("event_id") != current.event_id and warning.get("action_fingerprint") \
            and warning.get("action_fingerprint") == current.action_fingerprint
        corresponding_result = (repeated and warning.get("warning_surface") == "pre_action"
                                and current.surface == "post_action")
        if repeated and _ACTING.intersection(features) and not corresponding_result:
            resolutions.append(WarningResolution(str(warning["trace_id"]), "ignored", "same_action_after_warning"))
        elif current.surface == "post_action" and "outcome:success" in features \
                and _VERIFYING.intersection(features) and same_episode and relevant_target:
            resolutions.append(WarningResolution(str(warning["trace_id"]), "heeded", "successful_relevant_verification"))
    return tuple(resolutions)
