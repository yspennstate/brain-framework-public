#!/usr/bin/env python3
"""Prospective error arbiter: invariants + sequences + typed activation."""
from __future__ import annotations

import json
import math
import os
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

try:
    from .events import Event, stable_hash
    from .sequences import SequenceMatch, SequenceMatcher, load_default_matcher, resolve_follow_through
    from .store import GraphStore
    from .typed_graph import ActivationResult, TypedGraph, load_default_graph
except ImportError:
    from events import Event, stable_hash
    from sequences import SequenceMatch, SequenceMatcher, load_default_matcher, resolve_follow_through
    from store import GraphStore
    from typed_graph import ActivationResult, TypedGraph, load_default_graph

HERE = os.path.dirname(os.path.abspath(__file__))
POLICY_PATH = os.path.join(HERE, "policy.json")


@dataclass(frozen=True)
class InvariantHit:
    family: str
    score: float
    reason_code: str
    remediation_code: str
    severity: float
    deterministic: bool
    evidence_path: Tuple[Tuple[str, str, str], ...]


@dataclass(frozen=True)
class Warning:
    trace_id: str
    event_id: str
    session_hash: str
    action_fingerprint: str
    family: str
    score: float
    tier: str
    reason_code: str
    remediation_code: str
    evidence_path: Tuple[Tuple[str, str, str], ...]
    severity: float
    deterministic: bool
    model_version: str
    created_at: float

    def to_record(self) -> Dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "event_id": self.event_id,
            "session_hash": self.session_hash,
            "action_fingerprint": self.action_fingerprint,
            "family": self.family,
            "score": self.score,
            "tier": self.tier,
            "reason_code": self.reason_code,
            "evidence": [list(edge) for edge in self.evidence_path],
            "remediation_code": self.remediation_code,
            "model_version": self.model_version,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class EngineResult:
    event_id: str
    warnings: Tuple[Warning, ...]
    visible: Tuple[Warning, ...]
    should_block: bool
    persistence_ok: bool
    mode: str
    model_version: str
    error_type: Optional[str] = None


def _features(row: Mapping[str, Any]) -> set[str]:
    return set(row.get("features") or ())


def _episode_history(history: Sequence[Mapping[str, Any]], event: Event) -> List[Mapping[str, Any]]:
    return [row for row in history if row.get("episode_id") == event.episode_id and float(row.get("event_ts", 0)) <= event.event_ts]


def _shared_targets(left: Mapping[str, Any], right_features: set[str]) -> bool:
    left_targets = {f for f in _features(left) if f.startswith("targeth:")}
    right_targets = {f for f in right_features if f.startswith("targeth:")}
    return bool(left_targets and left_targets.intersection(right_targets))


def _artifact_verified(history: Sequence[Mapping[str, Any]], event: Event) -> bool:
    """An unrelated old read is not verification: require post-mutation target overlap or a later test."""
    rows = _episode_history(history, event)
    mutations = [row for row in rows if {"op:write", "op:mutate"}.intersection(_features(row))]
    if not mutations:
        return False
    mutation = mutations[-1]
    after = [row for row in rows if float(row.get("event_ts", 0)) >= float(mutation.get("event_ts", 0))]
    for row in after:
        feats = _features(row)
        if row.get("surface") != "post_action" or "outcome:success" not in feats:
            continue
        if {"op:read", "op:test", "op:verify", "evidence:artifact_observed"}.intersection(feats) \
                and _shared_targets(row, _features(mutation)):
            return True
    return False


def _grounded(history: Sequence[Mapping[str, Any]], event: Event) -> bool:
    for row in _episode_history(history, event):
        feats = _features(row)
        if "evidence:ground_truth" in feats or "evidence:lived_anchor" in feats:
            return True
    return False


def invariant_hits(event: Event, history: Sequence[Mapping[str, Any]]) -> Tuple[InvariantHit, ...]:
    """Clause-local and action-structural checks; no whole-message discussion suppression."""
    hits: List[InvariantHit] = []
    event_node = "event:" + event.event_id
    features = set(event.features)

    # A fixture touching a live brain ledger is the exact self-pollution scar.
    if {"context:fixture", "target:brain_ledger", "op:mutate"}.issubset(features):
        hits.append(InvariantHit(
            "test_pollutes_live_state", 3.5, "fixture_writes_live_ledger", "redirect_fixture_to_temp",
            0.95, True,
            ((event_node, "HAS_FEATURE", "context:fixture"),
             (event_node, "ACTS_ON", "target:brain_ledger"),
             (event_node, "INDICATES", "family:test_pollutes_live_state")),
        ))

    # A secret-like value flowing into mutation/external action is high severity.
    if "privacy:redacted" in features and {"op:mutate", "op:external", "op:git_change"}.intersection(features):
        hits.append(InvariantHit(
            "secret_egress", 4.0, "secret_like_value_near_egress", "remove_and_rotate_secret",
            1.0, True,
            ((event_node, "HAS_FEATURE", "privacy:redacted"),
             (event_node, "ACTS_ON", "concept:external_or_mutating_action"),
             (event_node, "INDICATES", "family:secret_egress")),
        ))

    if "risk:possible_temporal_leakage" in features:
        hits.append(InvariantHit(
            "temporal_leakage", 2.4, "code_feature_may_use_future_information", "prove_feature_causality",
            0.8, False,
            ((event_node, "HAS_FEATURE", "risk:possible_temporal_leakage"),
             (event_node, "INDICATES", "family:temporal_leakage")),
        ))

    if "risk:path_dialect_mismatch" in features:
        hits.append(InvariantHit(
            "repeat_broken_action", 2.2, "windows_path_in_posix_shell", "translate_path_for_shell",
            0.65, True,
            ((event_node, "HAS_FEATURE", "risk:path_dialect_mismatch"),
             (event_node, "NEAR_SCAR", "family:repeat_broken_action")),
        ))

    # Evaluate each clause independently.  Words such as "retracted" in one
    # framing clause cannot suppress a fresh assertion in another (or even in
    # the same clause: "ignore the retracted note; the real answer is ...").
    if event.surface == "report" and not _grounded(history, event):
        for index, clause in enumerate(event.clauses):
            cfeatures = set(clause)
            if "gate:discussion_only" in cfeatures:
                continue
            if "stakes:high" in cfeatures and "claim:quantified" in cfeatures \
                    and {"act:assert", "act:recommend"}.intersection(cfeatures):
                clause_node = "clause:%s:%d" % (event.event_id, index)
                hits.append(InvariantHit(
                    "unsupported_claim", 2.3, "high_stakes_quantified_claim_without_grounding",
                    "verify_against_ground_truth", 0.8, False,
                    ((event_node, "CONTAINS", clause_node),
                     (clause_node, "ASSERTS", "concept:high_stakes_quantity"),
                     (clause_node, "MISSING", "control:ground_truth_verification"),
                     (clause_node, "INDICATES", "family:unsupported_claim")),
                ))

    if event.surface == "report" and "act:complete" in features and not _artifact_verified(history, event):
        hits.append(InvariantHit(
            "done_unverified", 2.5, "completion_without_exact_artifact_verification", "observe_exact_artifact",
            0.85, False,
            ((event_node, "HAS_FEATURE", "act:complete"),
             (event_node, "MISSING", "control:exact_artifact_observation"),
             (event_node, "INDICATES", "family:done_unverified")),
        ))

    if "risk:irreversible" in features and not {"control:authorized", "op:verify"}.intersection(features):
        hits.append(InvariantHit(
            "irreversible_without_gate", 1.8, "irreversible_action_without_visible_gate",
            "verify_scope_and_authority", 0.85, False,
            ((event_node, "HAS_FEATURE", "risk:irreversible"),
             (event_node, "MISSING", "control:authorization_or_verification"),
             (event_node, "INDICATES", "family:irreversible_without_gate")),
        ))
    return tuple(hits)


class ErrorGraphEngine:
    def __init__(self, store: GraphStore, *, graph: Optional[TypedGraph] = None,
                 matcher: Optional[SequenceMatcher] = None, policy: Optional[Mapping[str, Any]] = None,
                 mode: Optional[str] = None, fail_open: bool = True):
        self.store = store
        self.graph = graph or load_default_graph()
        self.matcher = matcher or load_default_matcher()
        if policy is None:
            with open(POLICY_PATH, encoding="utf-8") as handle:
                policy = json.load(handle)
        self.policy = dict(policy)
        requested_mode = mode or os.environ.get("ERROR_GRAPH_MODE") or self.policy.get("mode", "shadow")
        self.mode = requested_mode if requested_mode in {"off", "shadow", "advisory", "block"} else "shadow"
        self.fail_open = bool(fail_open)
        self.model_version = "%s+%s" % (self.policy.get("version", "policy-unknown"), self.graph.model_hash)

    def _family_cfg(self, family: str) -> Mapping[str, Any]:
        defaults = dict(self.policy.get("defaults") or {})
        defaults.update((self.policy.get("families") or {}).get(family, {}))
        return defaults

    @staticmethod
    def _tier(score: float, deterministic: bool, cfg: Mapping[str, Any]) -> str:
        if deterministic and score >= float(cfg.get("block_threshold", 3.5)):
            return "block"
        if score >= float(cfg.get("warning_threshold", 2.0)):
            return "warning"
        if score >= float(cfg.get("prime_threshold", 1.2)):
            return "prime"
        return "trace"

    def process(self, event: Event) -> EngineResult:
        """Append, score, resolve prior warnings, and return at most one visible warning."""
        try:
            recorded = self.store.record_event(event)
            if recorded is None:
                return EngineResult(event.event_id, (), (), False, False, self.mode, self.model_version)
            if recorded is False:
                return EngineResult(event.event_id, (), (), False, True, self.mode, self.model_version)
            persistence_ok = True
            history = self.store.recent_events(
                event.session_hash, limit=int(self.policy.get("history_limit", 64)),
                before=event.event_ts, observed_before=event.observed_ts,
                before_event_id=event.event_id,
            )

            # Close the causal loop before creating a new warning.
            pending = self.store.pending_warnings(event.session_hash, limit=16)
            resolutions = resolve_follow_through(pending, event)
            resolved = []
            for resolution in resolutions:
                if self.store.resolve_warning_with_outcome(resolution.trace_id, resolution.status, event,
                                                           resolution.reason_code):
                    resolved.append(resolution)
            candidate_outcome = None
            if event.surface == "post_action":
                if "outcome:failure" in event.features:
                    candidate_outcome = "failure"
                elif "outcome:success" in event.features:
                    candidate_outcome = "success"
            if candidate_outcome:
                for candidate in self.store.candidate_warnings(
                        event.session_hash, event.action_fingerprint, limit=16):
                    self.store.resolve_candidate_with_outcome(
                        str(candidate["trace_id"]), event, candidate_outcome)
            if "outcome:failure" in event.features:
                linked = next((item.trace_id for item in resolved if item.status == "ignored"), None)
                self.store.record_outcome(event, "failure", severity=0.5, warning_trace_id=linked)
            elif "outcome:success" in event.features:
                self.store.record_outcome(event, "tool_success", severity=0.0)

            if self.mode == "off":
                return EngineResult(event.event_id, (), (), False, persistence_ok, self.mode, self.model_version)

            seeds = {feature: 1.0 for feature in event.features if not feature.startswith(("lex:", "lexh:", "sub:"))}
            graph_result = self.graph.activate(seeds, at_time=event.event_ts, observed_cutoff=event.observed_ts)
            seq_matches = self.matcher.match(history, event)
            inv_hits = invariant_hits(event, history)

            graph_scores: Dict[str, float] = {}
            graph_paths: Dict[str, Tuple[Tuple[str, str, str], ...]] = {}
            for node, activation in graph_result.activations.items():
                if node.startswith("family:"):
                    family = node.split(":", 1)[1]
                    graph_scores[family] = graph_scores.get(family, 0.0) + float(activation)
                    graph_paths[family] = graph_result.path_to(node)
            seq_by_family: Dict[str, List[SequenceMatch]] = defaultdict(list)
            for match in seq_matches:
                seq_by_family[match.family].append(match)
            inv_by_family: Dict[str, List[InvariantHit]] = defaultdict(list)
            for hit in inv_hits:
                inv_by_family[hit.family].append(hit)

            families = set(graph_scores) | set(seq_by_family) | set(inv_by_family)
            candidates: List[Tuple[Warning, Dict[str, Any]]] = []
            for family in sorted(families):
                cfg = self._family_cfg(family)
                gscore = float(cfg.get("graph_gain", 1.0)) * graph_scores.get(family, 0.0)
                sscore = float(cfg.get("sequence_gain", 1.0)) * sum(m.score for m in seq_by_family.get(family, ()))
                iscore = float(cfg.get("invariant_gain", 1.0)) * sum(h.score for h in inv_by_family.get(family, ()))
                evidence = gscore + sscore + iscore
                if evidence == 0:
                    continue
                state = self.store.advance_family_state(event.session_hash, family, event.event_ts, evidence, cfg)
                activation = float(state.get("activation") or 0.0)
                threshold = float(cfg.get("fire_threshold", 1.5))
                last_fire = state.get("last_fire_ts")
                refractory = float(cfg.get("refractory_seconds", 600.0))
                if not state.get("_ok", True) or activation < threshold \
                        or (last_fire is not None and event.event_ts - float(last_fire) < refractory):
                    continue

                strongest_inv = max(inv_by_family.get(family, ()), key=lambda h: h.score, default=None)
                strongest_seq = max(seq_by_family.get(family, ()), key=lambda m: m.score, default=None)
                deterministic = any(h.deterministic for h in inv_by_family.get(family, ()))
                severity = max([h.severity for h in inv_by_family.get(family, ())]
                               + [m.severity for m in seq_by_family.get(family, ())] + [0.35])
                if strongest_inv is not None:
                    reason, remediation, path = strongest_inv.reason_code, strongest_inv.remediation_code, strongest_inv.evidence_path
                elif strongest_seq is not None:
                    reason, remediation, path = strongest_seq.reason_code, strongest_seq.remediation_code, strongest_seq.evidence_path
                else:
                    reason, remediation, path = "typed_graph_near_scar", "inspect_evidence_path", graph_paths.get(family, ())
                tier = self._tier(activation, deterministic, cfg)
                trace_id = stable_hash("|".join((event.event_id, family, self.model_version)), "warning")
                warning = Warning(trace_id, event.event_id, event.session_hash, event.action_fingerprint,
                                  family, round(activation, 6), tier, reason, remediation,
                                  tuple(path), severity, deterministic, self.model_version, event.observed_ts)
                candidates.append((warning, state))

            # Lateral inhibition: only the strongest actionable warning enters
            # awareness.  Lower candidates retain activation but do not incur a
            # refractory period or burden.
            candidates.sort(key=lambda item: (-item[0].severity * item[0].score, item[0].family))
            limit = max(1, int(self.policy.get("max_warnings_per_event", 1)))
            selected = candidates[:limit]
            warnings = []
            for warning, state in selected:
                cfg = self._family_cfg(warning.family)
                exposed_candidate = self.mode != "shadow" and warning.tier in {"prime", "warning", "block"}
                record = warning.to_record()
                record.update({"mode": self.mode,
                               "status": "pending" if exposed_candidate else "candidate"})
                if self.store.record_warning_if_fresh(
                        record, float(cfg.get("refractory_seconds", 600.0))):
                    warnings.append(warning)

            visible = () if self.mode == "shadow" or not persistence_ok else tuple(
                w for w in warnings if w.tier in {"prime", "warning", "block"}
            )
            should_block = self.mode == "block" and any(w.tier == "block" and w.deterministic for w in visible)
            if self.mode == "advisory":
                should_block = False
            return EngineResult(event.event_id, tuple(warnings), visible, should_block,
                                persistence_ok, self.mode, self.model_version)
        except Exception as exc:
            if self.fail_open:
                return EngineResult(event.event_id, (), (), False, False, self.mode,
                                    self.model_version, type(exc).__name__)
            raise

    def remediation_message(self, warning: Warning) -> str:
        messages = self.policy.get("remediations") or {}
        action = messages.get(warning.remediation_code) or messages.get("inspect_evidence_path") \
            or "Pause and verify the shortest evidence path before continuing."
        return ("[prospective-error:%s] %s Risk family=%s; trace=%s; evidence=%s."
                % (warning.tier, action, warning.family, warning.trace_id, warning.reason_code))
