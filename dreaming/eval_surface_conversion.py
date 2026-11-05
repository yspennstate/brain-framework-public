#!/usr/bin/env python3
"""Chronological shadow evaluation of surface-to-read conversion.

When the recall CLI or a live hook surfaces a memory slug, does attention convert into an actual
content read of that same slug in the same session? This is an attentional-gating / retrieval-success
measurement, not evidence that surfacing caused a later answer to be correct.

The typed ``memory_access.tsv`` is authoritative about access semantics:

* treatments: ``recall_surface`` and ``hook_surface``, reported separately;
* outcomes: only ``read_explicit`` and ``read_command``;
* ``search_command``, ``recall_query``, and every surface class are never counted as reads.

The primary window is pre-registered at 30 minutes. Repeated same-slug surfaces inside five minutes
without an intervening read collapse into one burst. Each surface is matched, using history strictly
before its timestamp, to three corpus-available non-surfaced controls with similar prior read
popularity, availability age, and read recency. Control selection never sees future reads or future
surface labels. Recent rows without a complete outcome window are right-censored.

Reports include surface/control conversion, median conversion lag, matched-control coverage,
session-balanced absolute/relative lift, a deterministic session bootstrap interval, chronological
half stability, and explicit maturity gates. This evaluator writes nothing and has no consumer.

Usage: python eval_surface_conversion.py [path/to/memory_access.tsv]
"""

from __future__ import print_function

import bisect
import csv
import hashlib
import io
import math
import os
import random
import statistics
import sys
from collections import Counter, defaultdict

import build_memory_access as access


LEDGER = sys.argv[1] if len(sys.argv) > 1 else access.OUT
READ_KINDS = frozenset(("read_explicit", "read_command"))
SURFACE_KINDS = frozenset(("recall_surface", "hook_surface"))
KNOWN_KINDS = frozenset((
    "read_explicit", "read_command", "search_command", "recall_query",
    "recall_surface", "hook_surface",
))
WINDOW_SECONDS = 30 * 60.0
BURST_SECONDS = 5 * 60.0
CONTROL_K = 3
MAX_POP_BIN_GAP = 1
BOOTSTRAPS = 3000
SEED = 20260709

# A structural signal is not a consumer gate. These floors only say the observer is mature enough
# to interpret; recall or salience still cannot change without a separate full-stack intervention.
MIN_SPAN_DAYS = 14.0
MIN_MATCHED_SURFACES = 50
MIN_SESSIONS = 20
MIN_CONVERSIONS = 10
MIN_CONTROL_COVERAGE = 0.80
MAX_SESSION_SHARE = 0.15
MIN_EXACT_POP_MATCH = 0.80
MAX_MEDIAN_AVAILABILITY_GAP_H = 24.0

SUBSTRATE_SLUGS = frozenset((
    "MEMORY", "INDEX_ARCHIVE", "PREDICTIONS", "STATE_OF_MIND", "OPEN_QUESTIONS",
    "AUTOBIOGRAPHY", "MODEL_OF_PARTNER", "FASCINATIONS", "THE_OTHER_MINDS",
    "CONSCIENCE", "MISTAKES", "SALIENCE", "GROWTH", "STATS", "CURIOSITY",
    "RECONSOLIDATION_QUEUE", "RECENT_BRAIN",
))


def _session(value):
    """Hooks retain the 12-character prefix; canonicalize every actor to that same join key."""
    return str(value or "")[:12]


def load_access(path):
    rows = []
    stats = Counter()
    try:
        fh = io.open(path, encoding="utf-8", errors="replace")
    except Exception:
        stats["missing_file"] += 1
        return rows, stats
    with fh:
        reader = csv.DictReader(fh, delimiter="\t")
        if not reader.fieldnames or not set(access.FIELDS).issubset(set(reader.fieldnames)):
            stats["bad_schema"] += 1
            return rows, stats
        for order, raw in enumerate(reader):
            stats["rows"] += 1
            try:
                ts = float(raw.get("epoch"))
            except Exception:
                stats["bad_ts"] += 1
                continue
            if not math.isfinite(ts):
                stats["bad_ts"] += 1
                continue
            kind = str(raw.get("access_kind") or "")
            if kind not in KNOWN_KINDS:
                stats["unknown_kind"] += 1
                continue
            session = _session(raw.get("session"))
            if not session:
                stats["bad_session"] += 1
                continue
            slug = str(raw.get("slug") or "").strip()
            if kind in SURFACE_KINDS and not slug:
                stats["surface_without_slug"] += 1
                continue
            rows.append({
                "ts": ts,
                "session": session,
                "actor": str(raw.get("actor") or "")[:24],
                "source": str(raw.get("source") or "")[:48],
                "kind": kind,
                "slug": slug[:181],
                "event_id": str(raw.get("event_id") or "")[:32],
                "artifact": str(raw.get("artifact") or "")[:240],
                "line": str(raw.get("line") or "")[:24],
                "order": order,
            })
    rows.sort(key=lambda row: (row["ts"], row["order"], row["kind"], row["slug"]))
    stats["usable"] = len(rows)
    return rows, stats


def _read_index(rows):
    reads = defaultdict(list)
    for row in rows:
        if row["kind"] in READ_KINDS and row["slug"] and row["slug"] not in SUBSTRATE_SLUGS:
            reads[(row["session"], row["slug"])].append(row["ts"])
    for key in reads:
        reads[key].sort()
    return reads


def _first_read_after(reads, session, slug, ts):
    times = reads.get((session, slug), ())
    index = bisect.bisect_right(times, ts)  # strictly subsequent; equal-time order is ambiguous
    return times[index] if index < len(times) else None


def _converted(reads, session, slug, ts, window=WINDOW_SECONDS):
    found = _first_read_after(reads, session, slug, ts)
    if found is None or found - ts > window:
        return False, None
    return True, found - ts


def _surface_cohort(row):
    """Every slug surfaced to one session at the same instant is contemporaneously treated.

    Recall output normally shares artifact/line as well, but hooks can emit FOK and spread rows on
    adjacent ledger lines at one timestamp. Timestamp grouping keeps either route out of controls.
    """
    return (row["session"], row["kind"], round(row["ts"], 3))


def dedupe_surface_bursts(rows, reads, burst_seconds=BURST_SECONDS):
    surfaces = [
        row for row in rows
        if row["kind"] in SURFACE_KINDS and row["slug"] not in SUBSTRATE_SLUGS
    ]
    surfaces.sort(key=lambda row: (row["ts"], row["order"], row["kind"], row["slug"]))
    kept = []
    last = {}
    collapsed = 0
    for row in surfaces:
        key = (row["session"], row["kind"], row["slug"])
        prior = last.get(key)
        if prior is not None and row["ts"] - prior["ts"] <= burst_seconds:
            intervening = _first_read_after(reads, row["session"], row["slug"], prior["ts"])
            if intervening is None or intervening >= row["ts"]:
                collapsed += 1
                continue
        kept.append(row)
        last[key] = row
    return kept, collapsed


def _pop_bin(count):
    if count <= 0:
        return 0
    if count == 1:
        return 1
    if count <= 3:
        return 2
    if count <= 7:
        return 3
    return 4


def _stable_tie(event_id, slug):
    return hashlib.sha256((str(event_id) + "|" + slug).encode("utf-8", "replace")).hexdigest()


def _recency_gap(target_last, control_last, ts):
    if target_last is None and control_last is None:
        return 0.0
    if target_last is None or control_last is None:
        return 2.0
    target_age = max(0.0, (ts - target_last) / 60.0)
    control_age = max(0.0, (ts - control_last) / 60.0)
    return abs(math.log1p(target_age) - math.log1p(control_age))


def select_controls(surface, cohort_slugs, known_since, read_count, last_read, last_surface,
                    k=CONTROL_K):
    """Match from strictly prior state; no future read/surface outcome is inspected."""
    ts, session, target = surface["ts"], surface["session"], surface["slug"]
    target_count = read_count.get(target, 0)
    target_bin = _pop_bin(target_count)
    target_since = known_since.get(target, ts)
    target_age = max(0.0, ts - target_since)
    target_last = last_read.get(target)
    ranked = []
    for slug, first_seen in known_since.items():
        if not slug or slug == target or slug in cohort_slugs or slug in SUBSTRATE_SLUGS:
            continue
        prior_surface = last_surface.get((session, slug))
        if prior_surface is not None and 0 <= ts - prior_surface <= WINDOW_SECONDS:
            continue  # already treated in this session; not an untreated availability control
        count = read_count.get(slug, 0)
        bin_gap = abs(_pop_bin(count) - target_bin)
        if bin_gap > MAX_POP_BIN_GAP:
            continue
        age = max(0.0, ts - first_seen)
        pop_gap = abs(math.log1p(count) - math.log1p(target_count))
        availability_gap = abs(math.log1p(age / 60.0) - math.log1p(target_age / 60.0))
        recency_gap = _recency_gap(target_last, last_read.get(slug), ts)
        score = 3.0 * pop_gap + 0.75 * availability_gap + recency_gap
        ranked.append((score, bin_gap, _stable_tie(surface["event_id"], slug), slug, age, count))
    ranked.sort()
    return [{
        "slug": slug,
        "score": score,
        "pop_bin_gap": bin_gap,
        "availability_gap_s": abs(age - target_age),
        "prior_reads": count,
    } for score, bin_gap, _tie, slug, age, count in ranked[:k]]


def build_conversion_rows(rows, observation_end, window=WINDOW_SECONDS, control_k=CONTROL_K):
    reads = _read_index(rows)
    surfaces, collapsed = dedupe_surface_bursts(rows, reads)
    cohort_slugs = defaultdict(set)
    for row in rows:
        if row["kind"] in SURFACE_KINDS and row["slug"]:
            cohort_slugs[_surface_cohort(row)].add(row["slug"])

    timeline = sorted(rows, key=lambda row: (row["ts"], row["order"], row["kind"], row["slug"]))
    pointer = 0
    known_since = {}
    read_count = Counter()
    last_read = {}
    last_surface = {}
    evaluations = []
    diagnostics = Counter()
    diagnostics["raw_surfaces"] = sum(1 for row in rows if row["kind"] in SURFACE_KINDS)
    diagnostics["burst_collapsed"] = collapsed
    diagnostics["dedup_surfaces"] = len(surfaces)

    for surface in surfaces:
        ts = surface["ts"]
        while pointer < len(timeline) and timeline[pointer]["ts"] < ts:
            event = timeline[pointer]
            slug = event["slug"]
            if slug and slug not in SUBSTRATE_SLUGS:
                known_since.setdefault(slug, event["ts"])
                if event["kind"] in READ_KINDS:
                    read_count[slug] += 1
                    last_read[slug] = event["ts"]
                if event["kind"] in SURFACE_KINDS:
                    last_surface[(event["session"], slug)] = event["ts"]
            pointer += 1

        if ts + window > observation_end:
            diagnostics["right_censored"] += 1
            continue
        diagnostics["eligible_surfaces"] += 1
        controls = select_controls(
            surface, cohort_slugs[_surface_cohort(surface)], known_since,
            read_count, last_read, last_surface, k=control_k,
        )
        target_hit, lag = _converted(reads, surface["session"], surface["slug"], ts, window)
        control_hits = []
        for control in controls:
            hit, _control_lag = _converted(
                reads, surface["session"], control["slug"], ts, window
            )
            control_hits.append(1.0 if hit else 0.0)
        if len(controls) < control_k:
            diagnostics["insufficient_controls"] += 1
            continue
        diagnostics["matched_surfaces"] += 1
        evaluations.append({
            "ts": ts,
            "session": surface["session"],
            "surface_kind": surface["kind"],
            "surface_source": surface["source"],
            "event_id": surface["event_id"],
            "slug": surface["slug"],
            "converted": 1.0 if target_hit else 0.0,
            "lag_s": lag,
            "controls": tuple(control["slug"] for control in controls),
            "control_rate": sum(control_hits) / len(control_hits),
            "difference": (1.0 if target_hit else 0.0) - sum(control_hits) / len(control_hits),
            "exact_pop_bin": 1.0 if all(control["pop_bin_gap"] == 0 for control in controls) else 0.0,
            "availability_gap_s": sum(control["availability_gap_s"] for control in controls) / len(controls),
        })
    return evaluations, diagnostics, surfaces


def _session_balanced(rows, key):
    by = defaultdict(list)
    for row in rows:
        by[row["session"]].append(float(row[key]))
    if not by:
        return 0.0
    return sum(sum(values) / len(values) for values in by.values()) / len(by)


def session_bootstrap_ci(rows, key="difference", bootstraps=BOOTSTRAPS, seed=SEED):
    by = defaultdict(list)
    for row in rows:
        by[row["session"]].append(float(row[key]))
    sessions = sorted(by)
    if len(sessions) < 2:
        return (float("nan"), float("nan"))
    means = {session: sum(values) / len(values) for session, values in by.items()}
    rng = random.Random(seed)
    draws = []
    for _ in range(bootstraps):
        sample = [sessions[rng.randrange(len(sessions))] for _i in sessions]
        draws.append(sum(means[session] for session in sample) / len(sample))
    draws.sort()
    return (
        draws[int(0.025 * (len(draws) - 1))],
        draws[int(0.975 * (len(draws) - 1))],
    )


def _half_lifts(rows):
    if len(rows) < 2:
        return (float("nan"), float("nan"))
    ordered = sorted(rows, key=lambda row: (row["ts"], row["session"], row["event_id"]))
    cut = max(1, len(ordered) // 2)
    return (
        _session_balanced(ordered[:cut], "difference"),
        _session_balanced(ordered[cut:], "difference"),
    )


def summarize_kind(kind, evaluations, surfaces, diagnostics, observation_end):
    dedup = [row for row in surfaces if row["kind"] == kind]
    eligible = [row for row in dedup if row["ts"] + WINDOW_SECONDS <= observation_end]
    matched = [row for row in evaluations if row["surface_kind"] == kind]
    sessions = sorted(set(row["session"] for row in matched))
    counts = Counter(row["session"] for row in matched)
    target_rate = _session_balanced(matched, "converted")
    control_rate = _session_balanced(matched, "control_rate")
    lift = _session_balanced(matched, "difference")
    ci = session_bootstrap_ci(matched, seed=SEED + (1 if kind == "hook_surface" else 0))
    relative = target_rate / control_rate if control_rate > 0 else float("inf") if target_rate > 0 else 1.0
    lags = [row["lag_s"] for row in matched if row["lag_s"] is not None]
    span = (
        (max(row["ts"] for row in eligible) - min(row["ts"] for row in eligible)) / 86400.0
        if len(eligible) > 1 else 0.0
    )
    coverage = len(matched) / len(eligible) if eligible else 0.0
    share = max(counts.values()) / len(matched) if matched else 0.0
    early, late = _half_lifts(matched)
    exact_pop = sum(row["exact_pop_bin"] for row in matched) / len(matched) if matched else 0.0
    availability_gaps = [row["availability_gap_s"] / 3600.0 for row in matched]
    gates = []
    if span < MIN_SPAN_DAYS:
        gates.append("%.2f/%g days" % (span, MIN_SPAN_DAYS))
    if len(matched) < MIN_MATCHED_SURFACES:
        gates.append("%d/%d matched surfaces" % (len(matched), MIN_MATCHED_SURFACES))
    if len(sessions) < MIN_SESSIONS:
        gates.append("%d/%d sessions" % (len(sessions), MIN_SESSIONS))
    conversions = sum(int(row["converted"]) for row in matched)
    if conversions < MIN_CONVERSIONS:
        gates.append("%d/%d conversions" % (conversions, MIN_CONVERSIONS))
    if coverage < MIN_CONTROL_COVERAGE:
        gates.append("%.0f%%/%d%% control coverage" % (100 * coverage, 100 * MIN_CONTROL_COVERAGE))
    if share > MAX_SESSION_SHARE:
        gates.append("largest session %.1f%% > %.0f%%" % (100 * share, 100 * MAX_SESSION_SHARE))
    if exact_pop < MIN_EXACT_POP_MATCH:
        gates.append("exact popularity matching %.0f%% < %.0f%%" % (
            100 * exact_pop, 100 * MIN_EXACT_POP_MATCH
        ))
    median_availability_gap_h = (
        statistics.median(availability_gaps) if availability_gaps else None
    )
    if (median_availability_gap_h is None
            or median_availability_gap_h > MAX_MEDIAN_AVAILABILITY_GAP_H):
        gates.append("median availability gap %s > %.0fh" % (
            "n/a" if median_availability_gap_h is None else "%.1fh" % median_availability_gap_h,
            MAX_MEDIAN_AVAILABILITY_GAP_H,
        ))
    if gates:
        verdict = "DATA-GATED/HOLD (%s)" % "; ".join(gates)
    elif ci[0] > 0 and early > 0 and late > 0:
        verdict = (
            "ATTENTIONAL-CONVERSION SIGNAL / LIVE HOLD - association is predictive, not causal or "
            "proof of answer quality"
        )
    else:
        verdict = "HOLD/NULL - conversion does not beat matched controls robustly"
    return {
        "kind": kind,
        "dedup": len(dedup),
        "eligible": len(eligible),
        "matched": len(matched),
        "sessions": len(sessions),
        "conversions": conversions,
        "span_days": span,
        "coverage": coverage,
        "largest_session_share": share,
        "target_rate": target_rate,
        "control_rate": control_rate,
        "lift": lift,
        "relative_lift": relative,
        "ci": ci,
        "median_lag_s": statistics.median(lags) if lags else None,
        "early_lift": early,
        "late_lift": late,
        "exact_pop_match": exact_pop,
        "median_availability_gap_h": median_availability_gap_h,
        "verdict": verdict,
    }


def _fixture_row(ts, kind, slug, session="11111111-222", source="fixture", event_id=None, order=0,
                 artifact="fixture.jsonl", line="1"):
    return {
        "ts": float(ts), "session": session, "actor": "fixture", "source": source,
        "kind": kind, "slug": slug, "event_id": event_id or ("%s-%s-%s" % (kind, slug, ts)),
        "artifact": artifact, "line": line, "order": order,
    }


def self_test():
    session = "11111111-222"
    base = [
        _fixture_row(10, "read_explicit", "target", session, order=0),
        _fixture_row(11, "read_explicit", "control_a", session, order=1),
        _fixture_row(12, "read_command", "control_b", session, order=2),
        _fixture_row(13, "read_explicit", "control_c", session, order=3),
        _fixture_row(100, "recall_surface", "target", session, source="brain_cli", event_id="surface-target", order=4, line="5"),
        _fixture_row(100, "recall_surface", "cohort_decoy", session, source="brain_cli", event_id="surface-decoy", order=5, line="5"),
        _fixture_row(110, "search_command", "target", session, order=6),
        _fixture_row(120, "recall_surface", "target", session, source="brain_cli", event_id="surface-target-repeat", order=7, line="6"),
        _fixture_row(130, "hook_surface", "hook_target", session, source="hook_spread", order=8),
        _fixture_row(140, "search_command", "hook_target", session, order=9),
        _fixture_row(150, "read_explicit", "target", session, order=10),
        _fixture_row(160, "read_command", "control_a", session, order=11),
        _fixture_row(2500, "recall_surface", "right_censored", session, order=12),
    ]
    rows_a, diag_a, surfaces_a = build_conversion_rows(base, observation_end=3000, control_k=3)
    recall_target = [row for row in rows_a if row["event_id"] == "surface-target"]
    assert len(recall_target) == 1 and recall_target[0]["converted"] == 1.0
    assert diag_a["burst_collapsed"] == 1
    assert not [row for row in rows_a if row["slug"] == "right_censored"]
    hook = [row for row in rows_a if row["surface_kind"] == "hook_surface"]
    assert hook and hook[0]["converted"] == 0.0  # its search is not a read
    assert set(recall_target[0]["controls"]) == {"control_a", "control_b", "control_c"}
    assert "cohort_decoy" not in recall_target[0]["controls"]
    assert _surface_cohort(_fixture_row(
        130, "hook_surface", "one", session, source="hook_fok", line="8"
    )) == _surface_cohort(_fixture_row(
        130, "hook_surface", "two", session, source="hook_spread", line="9"
    ))

    # Changing only a future outcome may change conversion, never the control assignment.
    changed = list(base) + [_fixture_row(170, "read_explicit", "control_b", session, order=20)]
    rows_b, _diag_b, _surfaces_b = build_conversion_rows(changed, observation_end=3000, control_k=3)
    recall_changed = [row for row in rows_b if row["event_id"] == "surface-target"][0]
    assert recall_changed["controls"] == recall_target[0]["controls"]
    assert recall_changed["control_rate"] > recall_target[0]["control_rate"]

    # Strict temporal semantics: reads before/equal/after-window do not convert.
    read_map = _read_index([
        _fixture_row(99, "read_explicit", "x", session),
        _fixture_row(100, "read_explicit", "x", session),
        _fixture_row(100 + WINDOW_SECONDS + 1, "read_explicit", "x", session),
    ])
    assert _converted(read_map, session, "x", 100)[0] is False

    fixture_boot = [
        {"session": "a", "difference": 1.0},
        {"session": "a", "difference": 0.0},
        {"session": "b", "difference": -1.0},
    ]
    assert session_bootstrap_ci(fixture_boot, bootstraps=100) == session_bootstrap_ci(
        fixture_boot, bootstraps=100
    )
    return 11


def _print_summary(summary):
    label = "recall CLI" if summary["kind"] == "recall_surface" else "live hook"
    lag = "n/a" if summary["median_lag_s"] is None else "%.0fs" % summary["median_lag_s"]
    relative = (
        "undefined: control=0" if math.isinf(summary["relative_lift"])
        else "%.2fx" % summary["relative_lift"]
    )
    lo, hi = summary["ci"]
    print("\n%s surfaces:" % label)
    print(
        "  dedup=%d eligible=%d matched=%d sessions=%d span=%.2fd coverage=%.1f%%"
        % (
            summary["dedup"], summary["eligible"], summary["matched"], summary["sessions"],
            summary["span_days"], 100 * summary["coverage"],
        )
    )
    print(
        "  session-balanced conversion: surface %.3f vs matched controls %.3f -> lift %+.3f "
        "(95%% CI %+.3f,%+.3f; %s); conversions=%d; median lag=%s"
        % (
            summary["target_rate"], summary["control_rate"], summary["lift"], lo, hi,
            relative, summary["conversions"], lag,
        )
    )
    print(
        "  match quality: exact popularity-bin %.1f%%; median availability-age gap %s; "
        "chronological-half lift %+.3f / %+.3f; largest session %.1f%%"
        % (
            100 * summary["exact_pop_match"],
            "n/a" if summary["median_availability_gap_h"] is None
            else "%.2fh" % summary["median_availability_gap_h"],
            summary["early_lift"], summary["late_lift"],
            100 * summary["largest_session_share"],
        )
    )
    print("  VERDICT: " + summary["verdict"])


def main():
    checks = self_test()
    rows, stats = load_access(LEDGER)
    if not rows:
        print("eval_surface_conversion: no usable memory_access.tsv rows (%s)" % dict(stats))
        return 0
    try:
        observation_end = os.path.getmtime(LEDGER)
    except Exception:
        observation_end = max(row["ts"] for row in rows)
    evaluations, diagnostics, surfaces = build_conversion_rows(rows, observation_end)
    print(
        "eval_surface_conversion: %d typed events; %d raw surfaces -> %d dedup "
        "(%d burst repeats collapsed, %d right-censored); window=30m controls=%d"
        % (
            len(rows), diagnostics.get("raw_surfaces", 0), diagnostics.get("dedup_surfaces", 0),
            diagnostics.get("burst_collapsed", 0), diagnostics.get("right_censored", 0), CONTROL_K,
        )
    )
    print(
        "  semantics: only explicit/command content reads are outcomes; search/query/surface events "
        "remain non-reads"
    )
    print(
        "  framing: conversion measures attentional gating/retrieval success, never causal usefulness "
        "or answer quality"
    )
    summaries = [
        summarize_kind("recall_surface", evaluations, surfaces, diagnostics, observation_end),
        summarize_kind("hook_surface", evaluations, surfaces, diagnostics, observation_end),
    ]
    for summary in summaries:
        _print_summary(summary)
    print("\n  self-test: PASS (%d checks); evaluator writes nothing and has no consumer" % checks)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
