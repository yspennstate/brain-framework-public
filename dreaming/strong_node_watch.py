#!/usr/bin/env python3
"""strong_node_watch.py - the >10 recurrence watch, running OBSERVE-ONLY.

WHY (2026-09-05). The strong-node generation (campaign agent-mistakes-strong-node-10h) was
released 2026-07-26 and parked: READY.json says enabled=false, its semantic review is
PENDING_HUMAN_REVIEW, and the ENABLED marker was never placed - a governance gate only the owner
can open (memories strong_node_growth_is_parked_not_broken,
strong_node_activation_stalled_at_human_gates_2026_08_14,
strong_node_generator_is_chosen_dark_2026_08_27). brain.strong_node_proposals() returned DISABLED
before it ever scanned, so for six weeks nobody - not the dream, not the dashboard, not the
owner - could see which mechanisms had actually crossed the >10 line. Every dream since 08-13
carried "the >10 recurrence watch is not running" as a worry.

WHAT THIS DOES. It runs the reviewed scanner - the hash-pinned runtime generation that brain.py
verifies byte for byte before importing - READ-ONLY, and writes one report:

    strong_nodes/WATCH_LATEST.json     the latest observe-only report (atomic replace)
    strong_nodes/WATCH_HISTORY.jsonl   one row per run, append-only

brain.strong_node_proposals() reads WATCH_LATEST.json when ENABLED is absent and reports
OBSERVE_ONLY (fresh = at most 48 h old) instead of a bare DISABLED; the dream agenda's M1
section renders the queue. run_dream.ps1 runs this nightly after the instincts compile.

WHAT THIS NEVER DOES. It never creates ENABLED, never writes under releases/, runtime/ or the
reviewer keys, never signs anything, never promotes. Promotion and access warnings stay dark
until the release's own activation contract is completed. A report from this file is not
strong-node coverage, and the M1 section says so in its first line.

    python strong_node_watch.py              run the scan and write the report (exit 0; 2 = DEGRADED)
    python strong_node_watch.py --status     print the latest report's freshness and summary
    python strong_node_watch.py --selftest   both-sided controls on a temporary live root
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import sys
import tempfile
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

DR = os.path.dirname(os.path.abspath(__file__))
if DR not in sys.path:
    sys.path.insert(0, DR)

SCHEMA = "strong-node-observe-watch-v1"
STRICT_MIN = 11        # the scanner's own rule: promotion needs at least 11 exact current occurrences
NEAR_MIN = 6           # rows reported as "near threshold" from this many occurrences up
LATEST_NAME = "WATCH_LATEST.json"
HISTORY_NAME = "WATCH_HISTORY.jsonl"
IDENTITY_RE_TEXT = "^atomic identity kind=(\\S+) mechanism=(\\S+) reflex=(\\S+)$"


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _below_normal() -> None:
    """Background AI compute runs BelowNormal on this machine (compute doctrine)."""
    if os.name != "nt":
        try:
            os.nice(10)
        except Exception:
            pass
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), 0x00004000)
    except Exception:
        pass


def _sha256_file(path: Path) -> str | None:
    try:
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except Exception:
        return None


def release_state(live_root: Path) -> dict:
    """Read the parked release's READY.json and its strong nodes. Never raises."""
    import re

    identity_re = re.compile(IDENTITY_RE_TEXT)
    out = {
        "ready_path": str(live_root / "READY.json"),
        "state": "UNKNOWN",
        "enabled": None,
        "release_timestamp": None,
        "publication_generation_id": None,
        "strong_nodes": [],
        "error": None,
    }
    try:
        ready = json.loads((live_root / "READY.json").read_bytes().decode("utf-8"))
        out["enabled"] = bool(ready.get("enabled"))
        out["release_timestamp"] = ready.get("release_timestamp")
        out["publication_generation_id"] = ready.get("publication_generation_id")
        out["state"] = str((ready.get("semantic_review") or {}).get("state") or "UNKNOWN")
        registry_path = ready.get("registry_path")
        if registry_path and os.path.isfile(registry_path):
            registry = json.loads(Path(registry_path).read_bytes().decode("utf-8"))
            payload = registry.get("payload", registry)
            for node in payload.get("nodes") or []:
                if not isinstance(node, dict) or node.get("node_class") != "strong_recurrence":
                    continue
                identity = None
                for evidence in node.get("evidence") or []:
                    match = identity_re.fullmatch(str(evidence))
                    if match:
                        identity = [
                            match.group(1),
                            match.group(2),
                            "" if match.group(3) == "-" else match.group(3),
                        ]
                        break
                out["strong_nodes"].append(
                    {
                        "id": node.get("id"),
                        "identity": identity,
                        "occurrences": len(node.get("occurrence_manifest") or []),
                    }
                )
    except Exception as exc:
        out["error"] = " ".join(str(exc).split())[:300]
    return out


def _occ(row: dict) -> int:
    return len(row.get("occurrence_ids") or [])


def _identity(row: dict) -> tuple:
    return (
        str(row.get("source_kind") or ""),
        str(row.get("mechanism_id") or ""),
        str(row.get("reflex_id") or ""),
    )


def _dates(row: dict) -> tuple:
    values = [str(value) for value in (row.get("dates") or [])]
    return (min(values) if values else None, max(values) if values else None)


def summarize(
    proposals: dict,
    release: dict,
    enabled_present: bool,
    as_of: str,
    scan_seconds: float,
    runtime_generation: str | None,
    inputs: dict,
) -> dict:
    """Pure: turn the scanner's proposals into the observe-only report."""
    candidates = list(proposals.get("candidates") or [])
    not_promoted = list(proposals.get("not_promoted") or [])
    covered = {}
    for node in release.get("strong_nodes") or []:
        if node.get("identity"):
            covered[tuple(node["identity"])] = node
    eligible = []
    for row in sorted(candidates, key=_occ, reverse=True):
        key = _identity(row)
        first, last = _dates(row)
        pending = covered.get(key)
        if pending is None:
            coverage = "NOT_IN_PENDING_RELEASE"
        elif int(pending.get("occurrences") or 0) == _occ(row):
            coverage = "COVERED_SAME_EVIDENCE"
        else:
            coverage = "COVERED_EVIDENCE_CHANGED"
        eligible.append(
            {
                "source_kind": key[0],
                "mechanism_id": key[1],
                "reflex_id": key[2] or None,
                "occurrences": _occ(row),
                "first_date": first,
                "last_date": last,
                "pending_release": coverage,
                "pending_release_occurrences": (
                    pending.get("occurrences") if pending else None
                ),
            }
        )
    near = []
    blocked = []
    for row in sorted(not_promoted, key=_occ, reverse=True):
        count = _occ(row)
        if count < NEAR_MIN:
            continue
        key = _identity(row)
        first, last = _dates(row)
        entry = {
            "source_kind": key[0],
            "mechanism_id": key[1],
            "reflex_id": key[2] or None,
            "occurrences": count,
            "first_date": first,
            "last_date": last,
            "ineligible_reasons": [
                str(reason)[:200] for reason in (row.get("ineligible_reasons") or [])
            ],
        }
        (blocked if count >= STRICT_MIN else near).append(entry)
    eligible_keys = {
        (row["source_kind"], row["mechanism_id"], row["reflex_id"] or "")
        for row in eligible
    }
    stale_release = [
        node["id"] for key, node in covered.items() if key not in eligible_keys
    ]
    histogram = Counter(_occ(row) for row in not_promoted)
    return {
        "schema": SCHEMA,
        "as_of": as_of,
        "status": "OBSERVED",
        "scan_seconds": scan_seconds,
        "runtime_generation_id": runtime_generation,
        "promotion": {
            "enabled_present": bool(enabled_present),
            "state": "DISABLED" if not enabled_present else "ENABLED_PRESENT_SEE_BRAIN_PROPOSALS",
            "release_state": release.get("state"),
            "release_timestamp": release.get("release_timestamp"),
            "message": "observe-only: this watch never places ENABLED, never signs, never promotes",
        },
        "strict_rule": proposals.get("strict_rule"),
        "dedup_rule": proposals.get("dedup_rule"),
        "inputs": inputs,
        "eligible": eligible,
        "blocked_over_threshold": blocked,
        "near_threshold": near,
        "totals": {
            "eligible": len(eligible),
            "blocked_over_threshold": len(blocked),
            "near_threshold": len(near),
            "not_promoted": len(not_promoted),
            "occurrence_histogram_not_promoted": {
                str(key): value for key, value in sorted(histogram.items())
            },
        },
        "pending_release": {
            "publication_generation_id": release.get("publication_generation_id"),
            "strong_nodes": release.get("strong_nodes") or [],
            "stale_strong_nodes": stale_release,
            "error": release.get("error"),
        },
    }


def degraded_report(as_of: str, scan_seconds: float, enabled_present: bool, release: dict, reason: str) -> dict:
    return {
        "schema": SCHEMA,
        "as_of": as_of,
        "status": "DEGRADED",
        "scan_seconds": scan_seconds,
        "runtime_generation_id": None,
        "promotion": {
            "enabled_present": bool(enabled_present),
            "state": "DISABLED" if not enabled_present else "ENABLED_PRESENT_SEE_BRAIN_PROPOSALS",
            "release_state": release.get("state"),
            "release_timestamp": release.get("release_timestamp"),
            "message": "observe-only: this watch never places ENABLED, never signs, never promotes",
        },
        "reason": " ".join(str(reason).split())[:400] or "unknown failure",
        "eligible": [],
        "blocked_over_threshold": [],
        "near_threshold": [],
        "totals": {},
        "pending_release": {"strong_nodes": release.get("strong_nodes") or []},
    }


def write_latest(live_root: Path, report: dict) -> str:
    """Atomically replace WATCH_LATEST.json and append one history row. Returns the report hash."""
    live_root.mkdir(parents=True, exist_ok=True)
    data = (
        json.dumps(report, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    ).encode("utf-8")
    fd, tmp = tempfile.mkstemp(prefix=".WATCH_LATEST.", suffix=".tmp", dir=str(live_root))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, str(live_root / LATEST_NAME))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    report_sha = hashlib.sha256(data).hexdigest()
    row = {
        "as_of": report.get("as_of"),
        "status": report.get("status"),
        "scan_seconds": report.get("scan_seconds"),
        "eligible": len(report.get("eligible") or []),
        "blocked_over_threshold": len(report.get("blocked_over_threshold") or []),
        "near_threshold": len(report.get("near_threshold") or []),
        "report_sha256": report_sha,
        "reason": report.get("reason"),
    }
    with open(live_root / HISTORY_NAME, "ab") as handle:
        handle.write((json.dumps(row, sort_keys=True) + "\n").encode("utf-8"))
    return report_sha


def run_watch(live_root: Path) -> tuple[dict, int]:
    """Run the reviewed scanner read-only and write the report. Returns (report, exit code)."""
    as_of = _utc_now()
    started = time.time()
    # Do not leave bytecode caches inside the reviewed runtime directory.
    sys.dont_write_bytecode = True
    import brain

    enabled_present = (live_root / brain._strong_node_enabled_name()).exists()
    release = release_state(live_root)
    try:
        runtime_dir, generation = brain._strong_node_runtime_pin()
        package = brain._strong_node_package(runtime_dir, generation)
        recurrence = importlib.import_module(package.__name__ + ".recurrence")
        memory_dir = Path(brain.MEM)
        mistakes = memory_dir / "MISTAKES.md"
        instincts = Path(brain.DR) / "instincts.tsv"
        audits = (
            Path.home()
            / "brain-repo"
            / "12_cognitive_architecture"
            / "audits"
        )
        inputs = {
            "memory_dir": str(memory_dir),
            "memory_files": sum(1 for _ in memory_dir.glob("*.md")),
            "mistakes_path": str(mistakes),
            "mistakes_sha256": _sha256_file(mistakes),
            "instincts_path": str(instincts),
            "instincts_sha256": _sha256_file(instincts),
            "audit_root": str(audits),
        }
        proposals = recurrence.proposals(
            memory_dir=memory_dir,
            mistakes_path=mistakes,
            instincts_path=instincts,
            audit_root=audits,
        )
        report = summarize(
            proposals,
            release,
            enabled_present,
            as_of,
            round(time.time() - started, 1),
            generation,
            inputs,
        )
        code = 0
    except Exception as exc:
        report = degraded_report(
            as_of, round(time.time() - started, 1), enabled_present, release, str(exc)
        )
        code = 2
    write_latest(live_root, report)
    return report, code


SCAN_TIMEOUT_SECONDS = 900   # a hung scan must not stall the nightly dream runner (RunPy has no timeout)


def run_watch_with_timeout(live_root: Path) -> tuple[dict, int]:
    """Run the scan in a child process under a hard cap; DEGRADED on timeout, never a hang."""
    import subprocess

    started = time.time()
    command = [sys.executable, "-B", os.path.abspath(__file__), "--child", "--live-root", str(live_root)]
    flags = 0x08000000 if os.name == "nt" else 0   # CREATE_NO_WINDOW: never a console flash
    try:
        proc = subprocess.run(command, capture_output=True, timeout=SCAN_TIMEOUT_SECONDS, creationflags=flags)
    except subprocess.TimeoutExpired:
        import brain

        enabled_present = (live_root / brain._strong_node_enabled_name()).exists()
        report = degraded_report(
            _utc_now(), round(time.time() - started, 1), enabled_present,
            release_state(live_root), "scan timed out after %d s" % SCAN_TIMEOUT_SECONDS,
        )
        write_latest(live_root, report)
        return report, 2
    text = proc.stdout.decode("utf-8", "replace").rstrip()
    if text:
        print(text)
    err = proc.stderr.decode("utf-8", "replace").strip()
    if err:
        print(err[-2000:], file=sys.stderr)
    try:
        report = json.loads((live_root / LATEST_NAME).read_bytes().decode("utf-8"))
    except Exception as exc:
        report = degraded_report(
            _utc_now(), round(time.time() - started, 1), False, release_state(live_root),
            "child exited %s but no readable report: %s" % (proc.returncode, exc),
        )
        write_latest(live_root, report)
        return report, 2
    return report, proc.returncode


def _live_root() -> Path:
    import brain

    return Path(brain._strong_node_live_root()).resolve()


def _print_summary(report: dict, freshness: str | None = None) -> None:
    head = "strong-node observe-only watch: status=%s as_of=%s scan=%ss" % (
        report.get("status"), report.get("as_of"), report.get("scan_seconds"))
    if freshness:
        head += " freshness=%s" % freshness
    print(head)
    promotion = report.get("promotion") or {}
    print("  promotion: %s (release %s since %s) - %s" % (
        promotion.get("state"), promotion.get("release_state"),
        promotion.get("release_timestamp"), promotion.get("message")))
    if report.get("status") == "DEGRADED":
        print("  reason:", report.get("reason"))
        return
    for row in report.get("eligible") or []:
        print("  ELIGIBLE  %s/%s reflex=%s occ=%d %s..%s pending_release=%s" % (
            row["source_kind"], row["mechanism_id"], row.get("reflex_id"), row["occurrences"],
            row.get("first_date"), row.get("last_date"), row.get("pending_release")))
    for row in report.get("blocked_over_threshold") or []:
        print("  BLOCKED   %s/%s occ=%d reasons=%s" % (
            row["source_kind"], row["mechanism_id"], row["occurrences"],
            "; ".join(row.get("ineligible_reasons") or [])))
    for row in report.get("near_threshold") or []:
        print("  NEAR      %s/%s reflex=%s occ=%d reasons=%s" % (
            row["source_kind"], row["mechanism_id"], row.get("reflex_id"), row["occurrences"],
            "; ".join(row.get("ineligible_reasons") or [])))
    totals = report.get("totals") or {}
    print("  totals:", json.dumps(totals, sort_keys=True))
    pending = report.get("pending_release") or {}
    print("  pending release strong nodes: %d, stale against today: %s" % (
        len(pending.get("strong_nodes") or []), pending.get("stale_strong_nodes")))


def _selftest() -> int:
    """Both-sided controls on a temporary live root. Exit 0 on pass, 3 on any failure."""
    failures = []

    def check(condition: bool, label: str) -> None:
        print(("  ok   " if condition else "  FAIL ") + label)
        if not condition:
            failures.append(label)

    # 1. summarize() classifies by the scanner's own rule and compares with the pending release.
    proposals = {
        "strict_rule": "promotion requires at least 11 reviewed current occurrences",
        "candidates": [
            {"source_kind": "mistakes_reflex", "mechanism_id": "m_eleven", "reflex_id": "r_eleven",
             "occurrence_ids": ["c%d" % i for i in range(11)], "dates": ["2026-06-0%d" % (i % 9 + 1) for i in range(11)],
             "eligible": True, "ineligible_reasons": []},
        ],
        "not_promoted": [
            {"source_kind": "audit_mechanism", "mechanism_id": "m_blocked", "reflex_id": None,
             "occurrence_ids": ["a%d" % i for i in range(23)], "dates": ["20260701"] * 23,
             "eligible": False, "ineligible_reasons": ["2 matching sidecars have stale/missing bindings"]},
            {"source_kind": "mistakes_reflex", "mechanism_id": "m_ten", "reflex_id": "r_ten",
             "occurrence_ids": ["c%d" % i for i in range(10)], "dates": ["2026-07-01"] * 10,
             "eligible": False, "ineligible_reasons": ["only 10 exact-reflex corrections with canonical occurrence dates"]},
            {"source_kind": "mistakes_reflex", "mechanism_id": "m_three", "reflex_id": "r_three",
             "occurrence_ids": ["c1", "c2", "c3"], "dates": ["2026-07-01"] * 3,
             "eligible": False, "ineligible_reasons": ["only 3 exact-reflex corrections with canonical occurrence dates"]},
        ],
    }
    release = {
        "state": "PENDING_HUMAN_REVIEW", "release_timestamp": "2026-07-26T19:16:54+03:00",
        "publication_generation_id": "gen", "error": None,
        "strong_nodes": [
            {"id": "strong_eleven", "identity": ["mistakes_reflex", "m_eleven", "r_eleven"], "occurrences": 11},
            {"id": "strong_blocked", "identity": ["audit_mechanism", "m_blocked", ""], "occurrences": 20},
        ],
    }
    report = summarize(proposals, release, False, "2026-09-05T12:00:00Z", 1.5, "g" * 64, {"x": 1})
    check(report["status"] == "OBSERVED" and report["schema"] == SCHEMA, "summarize: OBSERVED report with schema")
    check([r["mechanism_id"] for r in report["eligible"]] == ["m_eleven"], "summarize: exactly the 11-occurrence row is eligible")
    check(report["eligible"][0]["pending_release"] == "COVERED_SAME_EVIDENCE", "summarize: eligible row matched to the pending release")
    check([r["mechanism_id"] for r in report["blocked_over_threshold"]] == ["m_blocked"], "summarize: the 23-occurrence ineligible row is BLOCKED, not eligible")
    check([r["mechanism_id"] for r in report["near_threshold"]] == ["m_ten"], "summarize: the 10-occurrence row is NEAR, the 3-occurrence row is not listed")
    check(report["pending_release"]["stale_strong_nodes"] == ["strong_blocked"], "summarize: the pending release's stale strong node is named")
    check(report["promotion"]["state"] == "DISABLED", "summarize: promotion reads DISABLED when ENABLED is absent")

    # 2. Reader in brain.py: fresh / stale / missing / unreadable, through the live-root seam.
    import brain

    with tempfile.TemporaryDirectory(prefix="strong_watch_selftest_") as tmp:
        root = Path(tmp)
        previous = os.environ.get("BRAIN_STRONG_NODE_LIVE_ROOT")
        os.environ["BRAIN_STRONG_NODE_LIVE_ROOT"] = str(root)
        try:
            check(brain._strong_node_watch_latest() == (None, "missing"), "reader: missing report reads missing")
            queue = brain.strong_node_proposals()
            check(queue["status"] == "DISABLED" and "missing" in queue["message"], "proposals: no report -> DISABLED, message names the missing watch")
            write_latest(root, report)
            fresh = dict(report, as_of=_utc_now())
            write_latest(root, fresh)
            got, freshness = brain._strong_node_watch_latest()
            check(freshness == "fresh" and got["totals"]["eligible"] == 1, "reader: a report stamped now reads fresh")
            queue = brain.strong_node_proposals()
            check(queue["status"] == "OBSERVE_ONLY", "proposals: fresh report -> OBSERVE_ONLY")
            check(queue["watch"]["eligible"][0]["mechanism_id"] == "m_eleven", "proposals: carries the watch's eligible row")
            check("promotion DISABLED" in queue["message"] and "read-only" in queue["message"], "proposals: message says promotion is disabled and the scan was read-only")
            section = brain.strong_node_agenda_section()
            check("PROMOTION DISABLED" in section and "OBSERVED ELIGIBLE" in section and "m_eleven" in section, "agenda: OBSERVE_ONLY section names promotion disabled and the eligible row")
            check("not coverage" in section.lower() or "not strong-node coverage" in section.lower(), "agenda: section says a watch report is not coverage")
            old = dict(report, as_of="2026-08-01T00:00:00Z")
            write_latest(root, old)
            queue = brain.strong_node_proposals()
            check(queue["status"] == "DISABLED" and "stale" in queue["message"], "proposals: stale report -> DISABLED, message says stale")
            (root / LATEST_NAME).write_bytes(b"{not json")
            check(brain._strong_node_watch_latest() == (None, "unreadable"), "reader: garbage reads unreadable")
            degraded = degraded_report(_utc_now(), 0.1, False, release, "pin failed")
            write_latest(root, degraded)
            queue = brain.strong_node_proposals()
            check(queue["status"] == "DISABLED" and "DEGRADED" in queue["message"], "proposals: a DEGRADED watch report never reads as OBSERVE_ONLY")
            history = (root / HISTORY_NAME).read_text(encoding="utf-8").splitlines()
            check(len(history) == 4 and all(json.loads(line)["report_sha256"] for line in history), "history: one appended row per write (four writes so far) with a report hash")

            # 3. DEGRADED path through a broken runtime pin: report written, exit 2, nothing else touched.
            os.environ["BRAIN_STRONG_NODE_TEST_MODE"] = "1"
            os.environ["BRAIN_STRONG_NODE_TEST_RUNTIME_DIR"] = str(root / "no_such_runtime")
            os.environ["BRAIN_STRONG_NODE_TEST_RUNTIME_GENERATION"] = ""
            before = sorted(str(p.relative_to(root)) for p in root.rglob("*"))
            try:
                report2, code = run_watch(root)
            finally:
                for key in ("BRAIN_STRONG_NODE_TEST_MODE", "BRAIN_STRONG_NODE_TEST_RUNTIME_DIR",
                            "BRAIN_STRONG_NODE_TEST_RUNTIME_GENERATION"):
                    os.environ.pop(key, None)
            after = sorted(str(p.relative_to(root)) for p in root.rglob("*"))
            check(code == 2 and report2["status"] == "DEGRADED" and "pin" in report2["reason"], "run_watch: a broken pin writes a DEGRADED report and exits 2 without raising")
            check(set(after) - set(before) == set() and set(before) - set(after) == set(),
                  "run_watch: the DEGRADED path changed no file names under the live root (only WATCH_* contents)")
            check(not (root / "ENABLED").exists(), "invariant: no ENABLED marker was created")
        finally:
            if previous is None:
                os.environ.pop("BRAIN_STRONG_NODE_LIVE_ROOT", None)
            else:
                os.environ["BRAIN_STRONG_NODE_LIVE_ROOT"] = previous

    print("selftest: %d failure(s)" % len(failures))
    return 3 if failures else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--status", action="store_true", help="print the latest report and its freshness")
    parser.add_argument("--selftest", action="store_true", help="run the both-sided controls")
    parser.add_argument("--live-root", default=None, help="override the strong_nodes directory (tests)")
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    _below_normal()
    if args.selftest:
        return _selftest()
    if args.child:
        live_root = Path(args.live_root).resolve() if args.live_root else _live_root()
        report, code = run_watch(live_root)
        _print_summary(report)
        return code
    if args.status:
        import brain

        report, freshness = brain._strong_node_watch_latest()
        if report is None:
            print("strong-node observe-only watch: no readable report (%s)" % freshness)
            return 1
        _print_summary(report, freshness)
        return 0 if freshness == "fresh" and report.get("status") == "OBSERVED" else 1
    live_root = Path(args.live_root).resolve() if args.live_root else _live_root()
    report, code = run_watch_with_timeout(live_root)
    print("  wrote %s (exit %d)" % (live_root / LATEST_NAME, code))
    return code


if __name__ == "__main__":
    sys.exit(main())
