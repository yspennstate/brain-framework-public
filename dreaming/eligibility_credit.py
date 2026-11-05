#!/usr/bin/env python3
"""eligibility_credit.py - shadow eligibility associations over the brain's existing logs.

This is an isolated, read-only-by-default bridge between three streams the brain already owns:

  * retrievals.tsv: a memory became active because a session read it;
  * outcomes.jsonl: an error-related-negativity (ERN) event arrived later;
  * prompts/PROMPTS.jsonl: the owner task in which both happened.

The neuroscience analogy is a three-factor eligibility trace: recent activation leaves a decaying
tag, and a later error/surprise signal makes that tag eligible for learning. Owner prompts and prior
outcomes are hard event boundaries: a later task/error cannot spend an earlier task's trace. Each
outcome's total trace mass is normalized to one so an error-heavy or read-heavy event cannot dominate
by construction. The output is deliberately an unsigned association, never blame or causal credit. A memory read before an
error may have caused it, helped catch it, or merely co-occurred in a hard session. Nothing here changes
salience, recall, reflexes, hooks, rankings, or any existing ledger. It only measures the candidate edge
so a later, held-out evaluation can decide whether the edge deserves a consumer.

Default execution reports to stdout and writes nothing:

    python eligibility_credit.py

The only write path is explicit, atomic, and limited to this mechanism's own sidecar:

    python eligibility_credit.py --write
    python eligibility_credit.py --write --out C:\\path\\eligibility_credit.jsonl

Input and output flags are test seams; no live fixture should ever be necessary. The sidecar contains
only timestamps, ids, memory slugs, signal labels, prompt class/hash, and weights. It never copies raw
commands, responses, or prompt text, which keeps secrets and private prose out of the derived stream.
"""

from __future__ import print_function

import argparse
import bisect
import csv
import hashlib
import io
import json
import math
import os
import re
import statistics
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover - Python 3.9+ has it; fallback keeps the tool portable
    ZoneInfo = None


HOME = os.path.expanduser("~")
HERE = os.path.dirname(os.path.abspath(__file__))
MEM = os.path.join(HOME, ".claude", "projects", "{{BRAIN_SLUG}}", "memory")
DEFAULT_OUTCOMES = os.environ.get("ELIGIBILITY_OUTCOMES", os.path.join(HERE, "outcomes.jsonl"))
DEFAULT_RETRIEVALS = os.environ.get("ELIGIBILITY_RETRIEVALS", os.path.join(HERE, "retrievals.tsv"))
DEFAULT_PROMPTS = os.environ.get(
    "ELIGIBILITY_PROMPTS", os.path.join(MEM, "prompts", "PROMPTS.jsonl")
)
DEFAULT_OUT = os.environ.get("ELIGIBILITY_OUT", os.path.join(HERE, "eligibility_credit.jsonl"))
DEFAULT_ADJUDICATIONS = os.environ.get(
    "ELIGIBILITY_ADJUDICATIONS", os.path.join(HERE, "outcome_adjudications.jsonl")
)

SCHEMA = "eligibility-credit/v3"
UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I
)
SESSION_PREFIX_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{3}$", re.I)
DISPLAY_CMD = re.compile(
    r"^\s*(?:sudo\s+)?(?:echo|printf|cat|type|head|tail|ls|dir|grep|rg|egrep|fgrep|"
    r"findstr|select-string|awk|sed\s+-n|Get-Content|Write-Output|Write-Host|less|more)\b",
    re.I,
)
SELF_REF = re.compile(
    r"leaked secret|traceback|syntaxerror|indentationerror|mojibake|parsererror|"
    r"non-zero exit|implausible magnitude",
    re.I,
)
SECRET = re.compile(
    r"(?:sk-|gh[pousr]_|github_pat_|rpa_|rpk_|hf_|gsk_)[A-Za-z0-9_-]{16,}|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|\b\d{8,10}:[A-Za-z0-9_-]{35}\b"
)
EXPECTED_NEGATIVE = re.compile(
    r"expected[-_ ]?negative|deliberate(?:ly)?[^\n]{0,20}fail|should[^\n]{0,12}fail|"
    r"self[-_ ]?test|\bfixture\b|\bperfprobe\b|\bprobe\b|humility_check\.py|"
    r"python(?:\.exe)?\s+-c\s+[\"']?\s*import\s|import\s+sentence_transformers",
    re.I,
)
ADJUDICATION_LABELS = {
    "real_bug", "security", "expected_negative", "display_echo", "synthetic", "unknown"
}

# Pre-registered engineering hypothesis, not a claim that biological wall time transfers literally:
# ten-minute half-life, hard reset after thirty minutes or an earlier prompt/outcome boundary.
DEFAULT_HALF_LIFE_SECONDS = 10 * 60.0
DEFAULT_MAX_LAG_SECONDS = 30 * 60.0
DEFAULT_MIN_EVENT_AGE_SECONDS = 60.0
DEFAULT_MAX_MEMORIES_PER_ERROR = 8
# An engineering prior for an UNADJUDICATED ERN source, not measured classifier precision. It only
# scales unsigned association mass; it never assigns blame or changes a memory.
RAW_ERN_RELIABILITY_WEIGHT = 0.25
SAFE_SIGNAL_CLASSES = {
    "traceback", "hard_failure", "mojibake", "git_conflict", "secret", "powershell_parse",
    "syntax", "implausible_magnitude", "other",
}
SAFE_TOOLS = {"Bash", "PowerShell", "Read", "Write", "Edit", "Grep", "Glob", "Other"}
SAFE_PROMPT_CLASSES = {
    "mistake", "security", "prove", "improve", "think", "run", "remember", "admin", "other"
}
SAFE_PROMPT_ORIGINS = {"owner", "hook", "automation", "backfill", "import", "unknown"}
SAFE_SEVERITIES = {"low", "medium", "high", "critical", "unknown"}


def _prompt_timezone():
    """PROMPTS.jsonl hook timestamps are local and naive; transcripts/outcomes are UTC epochs."""
    name = os.environ.get("ELIGIBILITY_PROMPT_TZ", "UTC")
    if ZoneInfo is not None:
        try:
            return ZoneInfo(name)
        except Exception:
            pass
    return datetime.now().astimezone().tzinfo or timezone.utc


def _parse_prompt_ts(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_prompt_timezone())
        return dt.timestamp()
    except Exception:
        return None


def _parse_utc_ts(value):
    """Millisecond-resolved transcript ISO timestamp; unlike prompt timestamps, naive means UTC."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return None


def _safe_category(value, allowed, fallback):
    value = str(value or "")
    return value if value in allowed else fallback


def _signal_classes(signals):
    """Map arbitrary detector prose to a fixed vocabulary; raw strings stay only in the source ledger."""
    out = set()
    for value in signals or []:
        low = str(value).lower()
        if "secret" in low or "private key" in low:
            out.add("secret")
        elif "traceback" in low or "exception" in low or "panic" in low:
            out.add("traceback")
        elif "mojibake" in low or "encoding" in low:
            out.add("mojibake")
        elif "git" in low or "conflict" in low or "rejected push" in low:
            out.add("git_conflict")
        elif "powershell" in low or "parser" in low:
            out.add("powershell_parse")
        elif "syntax" in low or "indentation" in low:
            out.add("syntax")
        elif "magnitude" in low or "sharpe" in low:
            out.add("implausible_magnitude")
        elif "failure" in low or "non-zero" in low or "fatal" in low:
            out.add("hard_failure")
        else:
            out.add("other")
    return sorted(out or {"other"})


def _event_key(event):
    raw = "%s|%.6f|%s|%s" % (
        event.get("session", ""),
        event.get("ts", 0.0),
        "|".join(event.get("sig") or []),
        event.get("tool", ""),
    )
    return hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()[:16]


def _quality_label(event):
    """Heuristic quarantine label; an append-only manual adjudication may override it.

    Security is asymmetric at alarm time, but a synthetic secret-scanner canary is still not evidence
    that a memory caused a real leak. Unknown raw ERN events remain low-reliability shadow candidates.
    """
    sigs = [str(x) for x in (event.get("sig") or [])]
    cmd = str(event.get("cmd") or "").strip()
    if not cmd:
        return "unknown"
    if EXPECTED_NEGATIVE.search(cmd):
        return "synthetic" if ("fixture" in cmd.lower() or "selftest" in cmd.lower()) else "expected_negative"
    if any("SECRET" in s.upper() for s in sigs):
        return "security"
    # A common shell wrapper should not hide that the actual command is only a read/search.
    inner = re.sub(r"^\s*cd\s+[^;&|]+\s*(?:&&|;)\s*", "", cmd, flags=re.I)
    if DISPLAY_CMD.match(inner) or SELF_REF.search(cmd):
        return "display_echo"
    return "unknown"


def load_adjudications(path):
    """Latest append-only judgment per event key; raw notes never enter the derived sidecar."""
    out = {}
    stats = Counter()
    try:
        fh = io.open(path, encoding="utf-8", errors="replace")
    except Exception:
        stats["missing_file"] += 1
        return out, stats
    with fh:
        for line in fh:
            stats["lines"] += 1
            try:
                row = json.loads(line)
            except Exception:
                stats["bad_json"] += 1
                continue
            key = str(row.get("event_key") or "")
            label = str(row.get("label") or "unknown")
            if len(key) != 16 or label not in ADJUDICATION_LABELS:
                stats["bad_shape"] += 1
                continue
            out[key] = {
                "label": label,
                "severity": str(row.get("severity") or "unknown")[:24],
                "source": str(row.get("source") or "manual")[:24],
            }
    stats["usable"] = len(out)
    return out, stats


def load_outcomes(path, now=None, min_event_age=DEFAULT_MIN_EVENT_AGE_SECONDS, adjudications=None):
    """Load every UUID outcome as a boundary; mark only non-quarantined rows as candidates."""
    now = time.time() if now is None else float(now)
    adjudications = adjudications or {}
    stats = Counter()
    events = []
    seen = set()
    try:
        fh = io.open(path, encoding="utf-8", errors="replace")
    except Exception:
        stats["missing_file"] += 1
        return events, stats
    with fh:
        for line in fh:
            stats["lines"] += 1
            try:
                row = json.loads(line)
            except Exception:
                stats["bad_json"] += 1
                continue
            if not isinstance(row, dict):
                stats["bad_shape"] += 1
                continue
            try:
                ts = float(row.get("ts"))
            except Exception:
                stats["bad_ts"] += 1
                continue
            if not math.isfinite(ts):
                stats["bad_ts"] += 1
                continue
            if now - ts < min_event_age:
                stats["too_new"] += 1
                continue
            session = str(row.get("session") or "")
            sig = row.get("sig") or []
            if isinstance(sig, str):
                sig = [sig]
            event = {
                "ts": ts,
                "session": session,
                "sig": [str(x)[:160] for x in sig],
                "tool": str(row.get("tool") or "")[:80],
                "cmd": str(row.get("cmd") or ""),
            }
            key = _event_key(event)
            if key in seen:
                stats["duplicate"] += 1
                continue
            seen.add(key)
            event["event_key"] = key
            if not UUID_RE.fullmatch(session):
                stats["non_uuid_session"] += 1
                continue
            heuristic = _quality_label(event)
            judgment = adjudications.get(key)
            quality = judgment["label"] if judgment else heuristic
            event["quality"] = quality
            event["adjudicated"] = bool(judgment)
            event["severity"] = judgment["severity"] if judgment else "unknown"
            event["shadow_candidate"] = quality in ("unknown", "real_bug", "security")
            # Raw ERN is a replay suspect, never trusted blame. A manual real-event judgment lifts
            # reliability only to the verified-observation tier; it still cannot mutate memory.
            event["reliability_weight"] = (
                0.90 if judgment and quality in ("real_bug", "security")
                else RAW_ERN_RELIABILITY_WEIGHT
            )
            stats["quality_" + quality] += 1
            if event["shadow_candidate"]:
                stats["shadow_candidate"] += 1
            else:
                stats["boundary_only"] += 1
            events.append(event)
    events.sort(key=lambda x: (x["ts"], x["session"], x["event_key"]))
    stats["uuid_boundaries"] = len(events)
    return events, stats


def load_retrievals(path):
    rows = []
    stats = Counter()
    try:
        fh = io.open(path, encoding="utf-8", errors="replace")
    except Exception:
        stats["missing_file"] += 1
        return rows, stats
    with fh:
        reader = csv.DictReader(fh, delimiter="\t")
        for row in reader:
            stats["rows"] += 1
            ts = _parse_utc_ts(row.get("iso"))
            if ts is None:
                try:
                    ts = float(row.get("epoch"))
                except Exception:
                    stats["bad_ts"] += 1
                    continue
            else:
                stats["precise_iso"] += 1
            if not math.isfinite(ts):
                stats["bad_ts"] += 1
                continue
            session = str(row.get("session") or "")
            slug = str(row.get("slug") or "").strip()
            if not UUID_RE.fullmatch(session) or not slug:
                stats["bad_shape"] += 1
                continue
            rows.append({"ts": ts, "session": session, "slug": slug[:180]})
    rows.sort(key=lambda x: (x["session"], x["ts"], x["slug"]))
    stats["usable"] = len(rows)
    return rows, stats


def load_prompts(path):
    rows = []
    stats = Counter()
    try:
        fh = io.open(path, encoding="utf-8", errors="replace")
    except Exception:
        stats["missing_file"] += 1
        return rows, stats
    with fh:
        for index, line in enumerate(fh):
            stats["lines"] += 1
            try:
                row = json.loads(line)
            except Exception:
                stats["bad_json"] += 1
                continue
            if not isinstance(row, dict):
                stats["bad_shape"] += 1
                continue
            session = str(row.get("session") or "")[:12]
            if not SESSION_PREFIX_RE.fullmatch(session):
                stats["bad_session"] += 1
                continue
            epoch = _parse_prompt_ts(row.get("ts"))
            if epoch is None:
                stats["bad_ts"] += 1
                continue
            text = str(row.get("text") or "")
            # The archive already redacts, but the derived stream stores only a hash regardless.
            prompt_hash = hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:12]
            origin = str(row.get("origin") or row.get("source") or "unknown")[:24]
            event_id = hashlib.sha1(
                ("%s|%.6f|%s" % (session, epoch, prompt_hash)).encode("utf-8")
            ).hexdigest()[:16]
            rows.append({
                "ts": epoch,
                "ts_text": str(row.get("ts") or "")[:40],
                "session": session,
                "class": str(row.get("class_model") or row.get("class_provisional") or "other")[:40],
                "sha1": prompt_hash,
                "origin": origin,
                "event_id": event_id,
                "index": index,
            })
    rows.sort(key=lambda x: (x["session"], x["ts"], x["index"]))
    stats["usable"] = len(rows)
    return rows, stats


def _group_by_session(rows):
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["session"]].append(row)
    for session in grouped:
        grouped[session].sort(key=lambda x: (x["ts"], x.get("slug", ""), x.get("index", 0)))
    return grouped


def _latest_prompt(prompts, error_ts):
    if not prompts:
        return None
    times = [p["ts"] for p in prompts]
    index = bisect.bisect_right(times, error_ts) - 1
    return prompts[index] if index >= 0 else None


def build_credit_records(
    outcomes,
    retrievals,
    prompts,
    half_life_seconds=DEFAULT_HALF_LIFE_SECONDS,
    max_lag_seconds=DEFAULT_MAX_LAG_SECONDS,
    max_memories_per_error=DEFAULT_MAX_MEMORIES_PER_ERROR,
):
    """Return deterministic association rows plus matching diagnostics.

    A prompt or any previous outcome closes the prior trace. Only reads inside the resulting event
    segment and before the current outcome are eligible. Repeated reads use the most recent activation.
    Raw traces decay by half-life; their per-event normalized mass sums to one. Every UUID outcome,
    including a quarantined one, closes the trace, but only shadow candidates emit associations.
    """
    half_life_seconds = max(float(half_life_seconds), 1e-9)
    max_lag_seconds = max(float(max_lag_seconds), 0.0)
    max_memories_per_error = max(int(max_memories_per_error), 1)
    reads_by_session = _group_by_session(retrievals)
    prompts_by_session = _group_by_session(prompts)
    stats = Counter()
    records = []
    previous_outcome = {}

    for event in outcomes:
        previous_ts = previous_outcome.get(event["session"])
        previous_outcome[event["session"]] = event["ts"]
        if not event.get("shadow_candidate"):
            stats["boundary_only_outcome"] += 1
            continue

        prompt = _latest_prompt(prompts_by_session.get(event["session"][:12], []), event["ts"])
        if prompt is None:
            stats["no_prompt_boundary"] += 1
            continue
        boundary_ts = prompt["ts"]
        boundary_kind = "prompt"
        if previous_ts is not None and previous_ts > boundary_ts:
            boundary_ts = previous_ts
            boundary_kind = "previous-outcome"

        session_reads = reads_by_session.get(event["session"], [])
        times = [r["ts"] for r in session_reads]
        end = bisect.bisect_right(times, event["ts"])
        unbounded_start = bisect.bisect_left(times, event["ts"] - max_lag_seconds)
        start = bisect.bisect_left(times, max(boundary_ts, event["ts"] - max_lag_seconds))
        stats["reads_cut_by_boundary"] += max(0, start - unbounded_start)
        latest = {}
        for row in reversed(session_reads[start:end]):
            lag = event["ts"] - row["ts"]
            if lag < 0:
                continue
            if lag > max_lag_seconds:
                break
            if row["slug"] not in latest:
                latest[row["slug"]] = (row, lag)

        if not latest:
            stats["unmatched_error"] += 1
            continue
        stats["matched_error"] += 1
        ranked = sorted(latest.values(), key=lambda pair: (pair[1], pair[0]["slug"]))
        ranked = ranked[:max_memories_per_error]
        weighted = [
            (read, lag, math.exp(-math.log(2.0) * lag / half_life_seconds))
            for read, lag in ranked
        ]
        raw_mass = sum(weight for _read, _lag, weight in weighted) or 1.0
        for read, lag, raw_eligibility in weighted:
            eligibility = raw_eligibility / raw_mass
            reliability = float(event.get("reliability_weight", RAW_ERN_RELIABILITY_WEIGHT))
            signal_classes = _signal_classes(event.get("sig"))
            signal_digest = hashlib.sha256(
                "\n".join(str(x) for x in event.get("sig") or []).encode("utf-8", "replace")
            ).hexdigest()[:16]
            records.append({
                "schema": SCHEMA,
                "event_key": event["event_key"],
                "event_id": prompt["event_id"],
                "error_ts": round(event["ts"], 3),
                "session": event["session"],
                "signal_classes": signal_classes,
                "signal_sha256": signal_digest,
                "signal_source": "ern",
                "outcome_valence": -1.0,
                "source_reliability_weight": reliability,
                "provenance": "outcomes.jsonl:ern",
                "quality": _safe_category(event.get("quality"), ADJUDICATION_LABELS, "unknown"),
                "adjudicated": bool(event.get("adjudicated")),
                "severity": _safe_category(event.get("severity"), SAFE_SEVERITIES, "unknown"),
                "memory_role": "unknown",
                "route": "role-adjudication" if reliability >= 0.90 else "replay-suspect",
                "tool_class": _safe_category(event.get("tool"), SAFE_TOOLS, "Other"),
                "memory": read["slug"],
                "read_ts": round(read["ts"], 3),
                "lag_s": round(lag, 3),
                "eligibility_raw": round(raw_eligibility, 6),
                "eligibility": round(eligibility, 6),
                "association_mass": round(eligibility * reliability, 6),
                "boundary": boundary_kind,
                "boundary_ts": round(boundary_ts, 3),
                "previous_outcome_ts": round(previous_ts, 3) if previous_ts is not None else None,
                "prompt_ts": prompt["ts_text"],
                "prompt_class": _safe_category(prompt["class"], SAFE_PROMPT_CLASSES, "other"),
                "prompt_origin": _safe_category(prompt["origin"], SAFE_PROMPT_ORIGINS, "unknown"),
                "prompt_sha1": prompt["sha1"],
            })
            stats["associations"] += 1
        stats["normalized_events"] += 1

    records.sort(key=lambda r: (r["error_ts"], r["session"], -r["eligibility"], r["memory"]))
    return records, stats


def analyze(
    outcomes_path=DEFAULT_OUTCOMES,
    retrievals_path=DEFAULT_RETRIEVALS,
    prompts_path=DEFAULT_PROMPTS,
    adjudications_path=DEFAULT_ADJUDICATIONS,
    now=None,
    min_event_age=DEFAULT_MIN_EVENT_AGE_SECONDS,
    half_life_seconds=DEFAULT_HALF_LIFE_SECONDS,
    max_lag_seconds=DEFAULT_MAX_LAG_SECONDS,
    max_memories_per_error=DEFAULT_MAX_MEMORIES_PER_ERROR,
):
    adjudications, adjudication_stats = load_adjudications(adjudications_path)
    outcomes, outcome_stats = load_outcomes(
        outcomes_path, now=now, min_event_age=min_event_age, adjudications=adjudications
    )
    retrievals, retrieval_stats = load_retrievals(retrievals_path)
    prompts, prompt_stats = load_prompts(prompts_path)
    records, credit_stats = build_credit_records(
        outcomes,
        retrievals,
        prompts,
        half_life_seconds=half_life_seconds,
        max_lag_seconds=max_lag_seconds,
        max_memories_per_error=max_memories_per_error,
    )
    return {
        "outcomes": outcomes,
        "retrievals": retrievals,
        "prompts": prompts,
        "records": records,
        "outcome_stats": outcome_stats,
        "retrieval_stats": retrieval_stats,
        "prompt_stats": prompt_stats,
        "adjudication_stats": adjudication_stats,
        "credit_stats": credit_stats,
        "half_life_seconds": float(half_life_seconds),
        "max_lag_seconds": float(max_lag_seconds),
    }


def _aggregate(analysis):
    per = defaultdict(lambda: {
        "mass": 0.0, "events": set(), "sessions": set(), "lags": []
    })
    for row in analysis["records"]:
        item = per[row["memory"]]
        item["mass"] += row["association_mass"]
        item["events"].add(row["event_key"])
        item["sessions"].add(row["session"])
        item["lags"].append(row["lag_s"])
    read_sessions = defaultdict(set)
    for row in analysis["retrievals"]:
        read_sessions[row["slug"]].add(row["session"])
    rows = []
    for slug, item in per.items():
        rows.append({
            "memory": slug,
            "mass": item["mass"],
            "errors": len(item["events"]),
            "error_sessions": len(item["sessions"]),
            "read_sessions": len(read_sessions.get(slug, ())),
            "median_lag": statistics.median(item["lags"]) if item["lags"] else 0.0,
        })
    rows.sort(key=lambda r: (-r["mass"], -r["error_sessions"], r["memory"]))
    return rows


def render_report(analysis, top_n=10):
    os_ = analysis["outcome_stats"]
    cs = analysis["credit_stats"]
    events = [e for e in analysis["outcomes"] if e.get("shadow_candidate")]
    rows = _aggregate(analysis)
    matched_keys = set(r["event_key"] for r in analysis["records"])
    reliable_records = [
        r for r in analysis["records"] if r["source_reliability_weight"] >= 0.90
    ]
    reliable_keys = set(r["event_key"] for r in reliable_records)
    reliable_event_times = [e["ts"] for e in events if e["event_key"] in reliable_keys]
    reliable_span_days = 0.0
    if len(reliable_event_times) > 1:
        reliable_span_days = (max(reliable_event_times) - min(reliable_event_times)) / 86400.0
    matched_sessions = set(r["session"] for r in analysis["records"])
    reliable_sessions = set(r["session"] for r in reliable_records)
    raw_session_mass = Counter()
    reliable_session_mass = Counter()
    for record in analysis["records"]:
        raw_session_mass[record["session"]] += record["association_mass"]
    for record in reliable_records:
        reliable_session_mass[record["session"]] += record["association_mass"]
    raw_total_mass = sum(raw_session_mass.values())
    reliable_total_mass = sum(reliable_session_mass.values())
    raw_max_share = (max(raw_session_mass.values()) / raw_total_mass) if raw_total_mass else 0.0
    reliable_max_share = (
        max(reliable_session_mass.values()) / reliable_total_mass if reliable_total_mass else 0.0
    )
    lines = [
        "=" * 76,
        " ELIGIBILITY ASSOCIATIONS - SHADOW ONLY (report mode; no consumer, no writes)",
        "=" * 76,
        "  inputs: %d outcome lines -> %d UUID boundaries / %d shadow candidates; %d retrievals; %d prompts" % (
            os_.get("lines", 0),
            os_.get("uuid_boundaries", 0),
            len(events),
            len(analysis["retrievals"]),
            len(analysis["prompts"]),
        ),
        "  quarantined: %d non-UUID/legacy, %d display, %d expected/synthetic, %d too-new/malformed" % (
            os_.get("non_uuid_session", 0),
            os_.get("quality_display_echo", 0),
            os_.get("quality_expected_negative", 0) + os_.get("quality_synthetic", 0),
            os_.get("too_new", 0)
            + os_.get("bad_json", 0) + os_.get("bad_shape", 0) + os_.get("bad_ts", 0),
        ),
        "  quality: %d unknown raw ERN, %d security, %d adjudications (%d reliable matched outcomes)" % (
            os_.get("quality_unknown", 0),
            os_.get("quality_security", 0),
            analysis["adjudication_stats"].get("usable", 0),
            len(reliable_keys),
        ),
        "  boundaries: %d read rows cut; %d candidate errors lacked a prompt boundary" % (
            cs.get("reads_cut_by_boundary", 0),
            cs.get("no_prompt_boundary", 0),
        ),
        "  trace: half-life %.0fs, max lag %.0fs; %d/%d candidates matched -> %d normalized associations" % (
            analysis["half_life_seconds"],
            analysis["max_lag_seconds"],
            len(matched_keys),
            len(events),
            len(analysis["records"]),
        ),
        "  raw independence: %d matched sessions; largest session %.1f%% of association mass" % (
            len(matched_sessions), 100.0 * raw_max_share
        ),
        "  maturity subset: %d reliable outcomes across %d sessions / %.1f days; largest session %.1f%%" % (
            len(reliable_keys), len(reliable_sessions), reliable_span_days,
            100.0 * reliable_max_share,
        ),
    ]
    if rows:
        lines.append("")
        lines.append("  %-42s %7s %7s %9s %9s" % ("memory", "mass", "errors", "sessions", "med-lag"))
        for row in rows[:top_n]:
            lines.append("  %-42s %7.2f %7d %4d/%-4d %8.0fs" % (
                row["memory"][:42],
                row["mass"],
                row["errors"],
                row["error_sessions"],
                row["read_sessions"],
                row["median_lag"],
            ))
    else:
        lines.append("  no recent memory-read/error overlap; the trace is empty, not broken")

    gates = []
    if reliable_span_days < 14.0:
        gates.append("%.1f/14 reliable-subset days" % reliable_span_days)
    if len(reliable_keys) < 50:
        gates.append("%d/50 reliable matched outcomes" % len(reliable_keys))
    if len(reliable_sessions) < 20:
        gates.append("%d/20 reliable independent sessions" % len(reliable_sessions))
    if reliable_total_mass and reliable_max_share > 0.10:
        gates.append("reliable largest-session share %.1f%% > 10%%" % (100.0 * reliable_max_share))
    verdict = (
        "DATA-GATED (%s). Keep shadow-only; do not change salience, recall, reflexes, or graph edges."
        % "; ".join(gates)
        if gates else
        "MATURE ENOUGH FOR A HELD-OUT EVALUATION, not for a consumer."
    )
    lines.extend([
        "",
        "  VERDICT: " + verdict,
        "  Interpretation: association means 'active when an outcome arrived,' never causal credit or blame.",
        "  Outcome valence is stored separately; every memory role remains unknown until adjudicated.",
    ])
    return "\n".join(lines)


def serialize_records(records):
    return "".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in records)


def write_sidecar(records, path=DEFAULT_OUT):
    """Atomically replace only the explicit eligibility sidecar; callers must opt in with --write."""
    parent = os.path.dirname(os.path.abspath(path))
    if not os.path.isdir(parent):
        raise OSError("output directory does not exist: %s" % parent)
    tmp = "%s.tmp.%d" % (path, os.getpid())
    payload = serialize_records(records)
    try:
        with io.open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _parser():
    p = argparse.ArgumentParser(description="Shadow eligibility associations; report-only unless --write.")
    p.add_argument("--outcomes", default=DEFAULT_OUTCOMES)
    p.add_argument("--retrievals", default=DEFAULT_RETRIEVALS)
    p.add_argument("--prompts", default=DEFAULT_PROMPTS)
    p.add_argument("--adjudications", default=DEFAULT_ADJUDICATIONS)
    p.add_argument("--half-life-seconds", "--tau-seconds", dest="half_life_seconds",
                   type=float, default=DEFAULT_HALF_LIFE_SECONDS)
    p.add_argument("--max-lag-seconds", type=float, default=DEFAULT_MAX_LAG_SECONDS)
    p.add_argument("--min-event-age-seconds", type=float, default=DEFAULT_MIN_EVENT_AGE_SECONDS)
    p.add_argument("--max-memories-per-error", type=int, default=DEFAULT_MAX_MEMORIES_PER_ERROR)
    p.add_argument("--top", type=int, default=10)
    p.add_argument("--write", action="store_true", help="atomically write this mechanism's sidecar")
    p.add_argument("--out", default=DEFAULT_OUT, help="sidecar path (ignored unless --write)")
    return p


def main(argv=None):
    args = _parser().parse_args(argv)
    analysis = analyze(
        outcomes_path=args.outcomes,
        retrievals_path=args.retrievals,
        prompts_path=args.prompts,
        adjudications_path=args.adjudications,
        min_event_age=args.min_event_age_seconds,
        half_life_seconds=args.half_life_seconds,
        max_lag_seconds=args.max_lag_seconds,
        max_memories_per_error=args.max_memories_per_error,
    )
    print(render_report(analysis, top_n=max(1, args.top)))
    if args.write:
        digest = write_sidecar(analysis["records"], args.out)
        print("  wrote explicit shadow sidecar: %s (%d rows, sha256 %s)" % (
            args.out, len(analysis["records"]), digest[:12]
        ))
    return 0


if __name__ == "__main__":
    sys.exit(main())
