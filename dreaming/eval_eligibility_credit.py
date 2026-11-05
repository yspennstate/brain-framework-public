#!/usr/bin/env python3
"""Regression gate for the event-bounded eligibility-credit shadow lane.

This tests the contract the live data cannot yet prove: no future-read leakage, no prompt-to-prompt
or outcome-to-outcome leakage, exact half-life math, per-event normalization, typed low-reliability
ERN association mass, quarantine boundaries, adjudication override, secret-safe serialization,
precise timestamp ordering, default no-write,
and atomic explicit writes. It then reports the live data-maturity verdict without treating that report
as evidence that eligibility predicts causality or usefulness.

No model, network, live ledger mutation, or background process is used.
Usage: python eval_eligibility_credit.py
"""

from __future__ import print_function

import contextlib
import io
import json
import os
import tempfile

import eligibility_credit as ec
import sys


SESSION = "11111111-2222-3333-4444-555555555555"
PREFIX = SESSION[:12]


def _event(ts, key, quality="unknown", candidate=True,
           reliability=ec.RAW_ERN_RELIABILITY_WEIGHT):
    return {
        "ts": float(ts),
        "session": SESSION,
        "sig": ["fixture-signal"],
        "tool": "Bash",
        "cmd": "fixture command never serialized",
        "event_key": key,
        "quality": quality,
        "shadow_candidate": bool(candidate),
        "reliability_weight": float(reliability),
        "adjudicated": reliability >= 0.90,
        "severity": "unknown",
    }


def _prompt(ts, event_id="prompt-event", origin="owner"):
    return {
        "ts": float(ts),
        "ts_text": "fixture",
        "session": PREFIX,
        "class": "improve",
        "sha1": "abc123def456",
        "origin": origin,
        "event_id": event_id,
        "index": 0,
    }


def _read(ts, slug):
    return {"ts": float(ts), "session": SESSION, "slug": slug}


def _assert(condition, message):
    if not condition:
        raise AssertionError(message)


def isolated_contract():
    checks = []

    # Prompt floor + a quarantined outcome both close the trace. The read at 900 belongs to an older
    # prompt; the read at 1090 is consumed by the boundary-only event at 1100; the read at 1185 is future.
    outcomes = [
        _event(1080, "aaaaaaaaaaaaaaaa"),
        _event(1100, "bbbbbbbbbbbbbbbb", quality="synthetic", candidate=False),
        _event(1180, "cccccccccccccccc"),
    ]
    reads = [
        _read(900, "old_prompt"),
        _read(1010, "first_event"),
        _read(1090, "consumed_before_boundary"),
        _read(1120, "second_event"),
        _read(1185, "future_read"),
    ]
    records, stats = ec.build_credit_records(outcomes, reads, [_prompt(1000)])
    by_event = {}
    for row in records:
        by_event.setdefault(row["event_key"], []).append(row)
    _assert({r["memory"] for r in by_event["aaaaaaaaaaaaaaaa"]} == {"first_event"},
            "prompt boundary or future-read exclusion failed")
    _assert({r["memory"] for r in by_event["cccccccccccccccc"]} == {"second_event"},
            "prior-outcome boundary failed")
    _assert("bbbbbbbbbbbbbbbb" not in by_event and stats["boundary_only_outcome"] == 1,
            "quarantined outcome did not remain a boundary-only event")
    checks.append("prompt/outcome/future boundaries")

    # Exact fixed half-life, duplicate collapse, normalization, and unsigned association mass.
    half_reads = [_read(1400, "half"), _read(1700, "dup"), _read(1900, "dup"), _read(2000, "now")]
    half_records, _ = ec.build_credit_records(
        [_event(2000, "dddddddddddddddd")], half_reads, [_prompt(1000)],
        half_life_seconds=600, max_lag_seconds=1800,
    )
    by_slug = {r["memory"]: r for r in half_records}
    _assert(abs(by_slug["half"]["eligibility_raw"] - 0.5) < 1e-6,
            "ten-minute half-life is not exact")
    _assert(by_slug["dup"]["read_ts"] == 1900 and len([r for r in half_records if r["memory"] == "dup"]) == 1,
            "latest-read duplicate collapse failed")
    _assert(abs(sum(r["eligibility"] for r in half_records) - 1.0) < 2e-6,
            "per-event eligibility mass is not normalized")
    _assert(abs(sum(r["association_mass"] for r in half_records)
                - ec.RAW_ERN_RELIABILITY_WEIGHT) < 2e-6,
            "per-event association mass does not equal the source reliability weight")
    _assert(all(r["outcome_valence"] == -1.0 and r["memory_role"] == "unknown"
                and r["route"] == "replay-suspect" for r in half_records),
            "outcome-valence/memory-role separation failed")
    _assert(all("credit" not in r and "signal_sign" not in r for r in half_records),
            "a signed credit field survived in the unsigned shadow schema")
    checks.extend(["half-life", "duplicate collapse", "event normalization",
                   "unsigned association mass", "valence/role separation"])

    no_prompt, no_prompt_stats = ec.build_credit_records(
        [_event(2000, "eeeeeeeeeeeeeeee")], [_read(1900, "orphan")], []
    )
    _assert(not no_prompt and no_prompt_stats["no_prompt_boundary"] == 1,
            "an unbounded event emitted an association")
    checks.append("no unbounded association")

    with tempfile.TemporaryDirectory() as td:
        outcomes_path = os.path.join(td, "outcomes.jsonl")
        prompts_path = os.path.join(td, "prompts.jsonl")
        adjudications_path = os.path.join(td, "adjudications.jsonl")
        raw = [
            {"ts": 1000, "session": SESSION, "sig": ["hard failure"], "tool": "Bash", "cmd": "python job.py"},
            {"ts": 1100, "session": SESSION, "sig": ["possible LEAKED SECRET"], "tool": "Bash", "cmd": "deploy job"},
            {"ts": 1200, "session": SESSION, "sig": ["hard failure"], "tool": "Bash", "cmd": "fixture probe should fail"},
        ]
        with io.open(outcomes_path, "w", encoding="utf-8") as fh:
            for row in raw:
                fh.write(json.dumps(row) + "\n")
        first_key = ec._event_key({
            "ts": 1000.0, "session": SESSION, "sig": ["hard failure"], "tool": "Bash"
        })
        with io.open(adjudications_path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"event_key": first_key, "label": "real_bug",
                                 "severity": "medium", "source": "manual"}) + "\n")
        judgments, judgment_stats = ec.load_adjudications(adjudications_path)
        loaded, load_stats = ec.load_outcomes(
            outcomes_path, now=5000, min_event_age=0, adjudications=judgments
        )
        by_ts = {row["ts"]: row for row in loaded}
        _assert(by_ts[1000]["quality"] == "real_bug"
                and by_ts[1000]["reliability_weight"] == 0.90,
                "manual adjudication did not override low-confidence heuristic")
        _assert(by_ts[1100]["quality"] == "security" and by_ts[1100]["shadow_candidate"],
                "real security signal was not retained asymmetrically")
        _assert(by_ts[1200]["quality"] in ("synthetic", "expected_negative")
                and not by_ts[1200]["shadow_candidate"],
                "expected-negative fixture was not quarantined")
        _assert(judgment_stats["usable"] == 1 and load_stats["boundary_only"] == 1,
                "quality-gate accounting is inconsistent")
        checks.extend(["quality quarantine", "adjudication override"])

        # All prompt origins define boundaries; origin is a covariate, not a filter.
        with io.open(prompts_path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": "1970-01-01T00:10:00+00:00", "session": PREFIX,
                                 "source": "automation", "class_provisional": "run",
                                 "text": "private fixture prompt"}) + "\n")
        prompt_rows, _ = ec.load_prompts(prompts_path)
        _assert(len(prompt_rows) == 1 and prompt_rows[0]["origin"] == "automation",
                "non-owner prompt boundary was discarded")
        checks.append("all prompt origins bound events")

        retrievals_path = os.path.join(td, "retrievals.tsv")
        with io.open(retrievals_path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("slug\tepoch\tiso\tsession\n")
            # Rounded epochs tie and alphabetical fallback would reverse the real order.
            fh.write("z_earlier\t1000\t1970-01-01T00:16:40.100+00:00\t%s\n" % SESSION)
            fh.write("a_later\t1000\t1970-01-01T00:16:40.900+00:00\t%s\n" % SESSION)
        precise_reads, precise_stats = ec.load_retrievals(retrievals_path)
        _assert([r["slug"] for r in precise_reads] == ["z_earlier", "a_later"]
                and abs(precise_reads[1]["ts"] - precise_reads[0]["ts"] - 0.8) < 1e-6
                and precise_stats["precise_iso"] == 2,
                "retrieval loader ignored precise ISO order")
        checks.append("precise retrieval order")

        private_marker = "raw secret detector prose must stay private"
        adversarial = _event(2100, "ffffffffffffffff")
        adversarial["sig"] = [private_marker]
        adversarial["tool"] = private_marker
        adversarial_prompt = _prompt(2000, event_id="adversarial-prompt", origin=private_marker)
        adversarial_prompt["class"] = private_marker
        safe_records, _ = ec.build_credit_records(
            [adversarial], [_read(2050, "safe_memory")], [adversarial_prompt]
        )
        safe_payload = ec.serialize_records(safe_records)
        _assert(private_marker not in safe_payload and safe_records[0]["signal_classes"] == ["secret"]
                and safe_records[0]["tool_class"] == "Other"
                and safe_records[0]["prompt_class"] == "other"
                and safe_records[0]["prompt_origin"] == "unknown",
                "derived sidecar copied an arbitrary signal/category string")
        checks.append("adversarial category redaction")

        sidecar = os.path.join(td, "credit.jsonl")
        digest1 = ec.write_sidecar(half_records, sidecar)
        digest2 = ec.write_sidecar(half_records, sidecar)
        payload = io.open(sidecar, encoding="utf-8").read()
        _assert(digest1 == digest2 and "fixture command never serialized" not in payload
                and "private fixture prompt" not in payload,
                "atomic sidecar is nondeterministic or leaked raw prose")
        _assert(not [name for name in os.listdir(td) if ".tmp." in name],
                "atomic writer stranded a temporary file")
        checks.extend(["secret-safe deterministic serialization", "atomic replace"])

        default_out = os.path.join(td, "must_not_exist.jsonl")
        with contextlib.redirect_stdout(io.StringIO()):
            rc = ec.main([
                "--outcomes", os.path.join(td, "missing-outcomes"),
                "--retrievals", os.path.join(td, "missing-retrievals"),
                "--prompts", os.path.join(td, "missing-prompts"),
                "--adjudications", os.path.join(td, "missing-adjudications"),
                "--out", default_out,
            ])
        _assert(rc == 0 and not os.path.exists(default_out), "default report mode wrote a sidecar")
        checks.append("default no-write")

    return checks


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # Discovery is not execution: this script writes, so a help request must
        # exit before any work. See script_help_flag_can_execute_work.
        print(__doc__ or os.path.basename(__file__))
        return
    checks = isolated_contract()
    live = ec.analyze()
    report = ec.render_report(live, top_n=3)
    matched = len(set(r["event_key"] for r in live["records"]))
    sessions = len(set(r["session"] for r in live["records"]))
    print("eval_eligibility_credit: PASS (%d isolated checks; no model/network/live writes)" % len(checks))
    print("  live shadow: %d matched outcome events, %d associations, %d sessions" %
          (matched, len(live["records"]), sessions))
    verdict = "DATA-GATED" if "DATA-GATED" in report else "REVIEW-REQUIRED"
    print("  verdict: %s; evaluator proves containment/math, not causal usefulness" % verdict)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
