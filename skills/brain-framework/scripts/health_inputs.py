"""Read the inputs of the installed Brain readers without publishing repairs."""
from __future__ import annotations

from collections import Counter
import datetime as dt
import gzip
import json
import math
import os
from pathlib import Path
import re
import subprocess
import time


def recurrence_findings(dreaming, memory):
    """Observe attempted recurrence watches through the installed readers."""
    dreaming = Path(dreaming)
    strong_root = dreaming / "strong_nodes"
    findings = []
    if any(os.path.lexists(strong_root / name) for name in ("WATCH_LATEST.json", "WATCH_HISTORY.jsonl", "ENABLED")):
        try:
            import brain

            if Path(brain._strong_node_live_root()).resolve() != strong_root.resolve():
                raise ValueError("reader resolved a different strong-node root")
            report, freshness = brain._strong_node_watch_latest()
            if report and report.get("status") == "DEGRADED":
                reason = " ".join(str(report.get("reason") or "unknown failure").split())[:400]
                findings.append(f"Strong-node recurrence watch DEGRADED ({freshness} report): {reason}")
            elif freshness != "fresh" or not report or report.get("status") != "OBSERVED":
                findings.append(f"Strong-node recurrence watch UNVERIFIED: {freshness} or non-observing report")
            else:
                runtime, generation = brain._strong_node_runtime_pin()
                if Path(runtime).resolve() != (strong_root / "runtime" / generation).resolve():
                    raise ValueError("reviewed runtime is outside this strong-node root")
                if report.get("runtime_generation_id") != generation:
                    raise ValueError("observed generation differs from the current reviewed pin")
                inputs = report.get("inputs")
                if not isinstance(inputs, dict) or not inputs.get("memory_dir"):
                    raise ValueError("report has no observed corpus")
                if Path(inputs["memory_dir"]).resolve() != Path(memory).resolve():
                    raise ValueError("report belongs to a different corpus")
        except (ImportError, OSError, RuntimeError, ValueError, TypeError, AttributeError) as exc:
            findings.append(f"Strong-node recurrence watch UNVERIFIED: {exc}")

    mistake_status = dreaming / ".mistake_graph_status.json"
    generation = dreaming / ".mistake_graph_snapshot.json.generation.json"
    if os.path.lexists(mistake_status) or os.path.lexists(generation):
        try:
            from mistake_graph_status import summarize_path

            summary = summarize_path(mistake_status, generation_path=generation)
            # This status version can establish a ledger comparison or an
            # alert. It carries no proof of owner-correction ingestion.
            findings.append(f"{summary.title}: {summary.detail}")
        except (ImportError, OSError, RuntimeError, ValueError, AttributeError) as exc:
            findings.append(f"Mistake recurrence watch UNVERIFIED: {exc}")
    return findings


def lexical_findings(memory, index_path, read_docs):
    """Use the original index builder's eligibility and tokenizer on this corpus."""
    with gzip.open(index_path, "rt", encoding="utf-8") as stream:
        index = json.load(stream)
    docs = read_docs(str(memory))
    lengths = {slug: len(tokens) for slug, tokens in docs.items()}
    terms = {slug: dict(Counter(tokens)) for slug, tokens in docs.items()}
    frequencies = dict(Counter(term for counts in terms.values() for term in counts))
    if (index["n"], index["dl"], index["tf"], index["df"]) == (len(docs), lengths, terms, frequencies):
        if not math.isclose(index["avgdl"], sum(lengths.values()) / max(1, len(lengths)), rel_tol=1e-9):
            return ["BM25 average document length is inconsistent with its documents"]
        return []
    indexed = set(index["tf"])
    missing = len(docs.keys() - indexed)
    obsolete = len(indexed - docs.keys())
    edited = sum(index["tf"][slug] != terms[slug] for slug in indexed & docs.keys())
    return [f"BM25 index differs from the designated corpus ({missing} missing, {obsolete} obsolete, "
            f"{edited} changed documents; scoring metadata also checked). Inspect or refresh the original index."]


def owner_window(report, ledger, builder, now):
    """Compare the built view to the original builder's rules on the real ledger."""
    with ledger.open("rb") as stream:
        rows = []
        for number, line in enumerate(stream, 1):
            if not line.endswith(b"\n"):
                raise ValueError(f"Incomplete prompt ledger row {number}; resample after the archive write")
            if line.strip():
                try:
                    row = json.loads(line)
                    if not isinstance(row, dict):
                        raise ValueError("expected an object")
                except ValueError as exc:
                    raise ValueError(f"Malformed prompt ledger row {number}") from exc
                if builder._is_owner(row) and len(str(row.get("text") or "")) >= 3:
                    rows.append(builder._slim(row))
    prompts = builder.owner_prompts(builder._fold_cache(list(rows))[-builder.CACHE_MAX_ROWS:])
    expected = sorted(prompts, key=lambda row: row["ts"])[-builder.N_KEEP:]
    built_at = report["built_epoch"]
    built = report["prompts"]
    recorded_rows = report["ledger_rows"]
    if (type(built_at) is not int or not 0 < built_at <= now or not isinstance(built, list)
            or report["kept"] != len(built) or type(recorded_rows) is not int or recorded_rows < 0):
        raise ValueError("Owner-prompt view metadata is invalid")
    findings = []
    if recorded_rows > len(rows):
        findings.append(f"Owner-prompt ledger has {len(rows)} eligible rows; the last build recorded "
                        f"{recorded_rows}. Check for rotation, truncation or a changed owner classification.")
    if report.get("topics_warning"):
        findings.append("Owner-prompt topic configuration failed to load; the builder used its generic lexicon")

    def key(row):
        return (row["ts"], row["session"], row["text"][:builder.JSON_TEXT_CAP])

    # The hook can serve newly appended prompts before the next build. An
    # already-due row missing from a fresh view is still a failure.
    built_by_key = {key(row): row for row in built}
    if len(built_by_key) != len(built):
        findings.append("Owner-prompt view holds duplicate rows after native folding")
    missed = 0
    pending = 0
    for row in expected:
        prior = built_by_key.get(key(row))
        fields = ("refires", "last_ts", "cls", "runtime")
        if prior and all(prior.get(field) == row.get(field) for field in fields):
            continue
        recorded_at = dt.datetime.fromisoformat(row.get("last_ts") or row["ts"]).timestamp()
        if recorded_at < built_at or now - recorded_at > 3 * 3600:
            missed += 1
        else:
            pending += 1
    if missed:
        findings.append(f"Owner-prompt view is missing {missed} current prompt records outside the live-tail grace period. Rebuild from the original ledger and verify the result.")
    return findings, len(built), len(expected), pending


def governor_findings(home, now=None):
    """Verify the POSIX producer's embedded time and the identity of its live PID."""
    home = Path(home)
    compute = home / ".claude/compute"
    if (compute / "STOP_GOVERNOR").exists():
        return ["Compute governor is explicitly paused by STOP_GOVERNOR; ongoing monitoring is unavailable"]
    try:
        state = json.loads((compute / "compute_state.json").read_text(encoding="utf-8"))
        epoch = state["t_epoch"]
        status = state["status"]
        now = time.time() if now is None else now
        if (type(epoch) not in (int, float) or not math.isfinite(epoch) or not 0 < epoch <= now + 1
                or status not in {"ok", "warn", "distress"} or not isinstance(state["reasons"], list)
                or any(not isinstance(reason, str) for reason in state["reasons"])):
            raise ValueError("invalid heartbeat time or status")
    except (OSError, KeyError, TypeError, ValueError) as exc:
        return [f"Compute governor heartbeat UNVERIFIED: {exc}"]
    findings = []
    if status != "ok" or state["reasons"]:
        findings.append(f"Compute governor reported {status.upper()}: " + ", ".join(state["reasons"]))
    if now - epoch > 600:
        findings.append(f"Compute governor heartbeat is {int((now - epoch) // 60)} minutes old")
    try:
        pid = int((compute / "governor.pid").read_text(encoding="ascii"))
        if pid <= 1:
            raise ValueError("invalid governor PID")
        result = subprocess.run(["/bin/ps", "-p", str(pid), "-o", "command="],
                                capture_output=True, text=True, timeout=3)
        if result.returncode and (result.returncode != 1 or result.stderr.strip()):
            detail = " ".join(result.stderr.split())[:240] or f"ps exited {result.returncode}"
            raise ValueError(detail)
        program = str(home / "brain-framework/governor/compute_governor_posix.py")
        if (result.returncode != 0 or not re.search(r"(?:^|\s)" + re.escape(program) + r"\s+--every\s+[1-9]\d*(?:\s|$)",
                                                     result.stdout)):
            findings.append("Compute governor PID has no matching live POSIX runner; inspect the native supervisor and its log")
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        findings.append(f"Compute governor process UNVERIFIED: {exc}")
    return findings
