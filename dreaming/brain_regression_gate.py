#!/usr/bin/env python3
"""Property gate for the live strong-node, acknowledgement, and topic-recall layers.

This gate deliberately attacks the failures that status checks missed:

* enforcement markers present but no real deny reaches the consumer;
* an unrelated acknowledgement token bypassing the applicable scar;
* corrupt ledgers being recast as empty and overwritten;
* two processes both winning one deny/claim;
* a long session spending a lifetime deny cap and becoming permanently advisory;
* keyword membership that does not contain the word;
* shape-only aliases rewriting intentional words;
* hash-seed-dependent truncation and unbounded prompt fanout;
* a crash publishing a mixed three-file keyword snapshot;
* a graph-loader signature change breaking ``brain.py recall``.

Exit states: 0 PASS, 1 FAIL, 2 INCOMPLETE. Tests use temporary state and non-UUID replay
sessions, so the gate never pollutes live deny, dedup, surfacing, or activation ledgers.
"""
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from collections import defaultdict


HERE = os.path.dirname(os.path.abspath(__file__))
GF = os.path.join(HERE, "graph_fire")
sys.path.insert(0, HERE)
sys.path.insert(0, GF)

TOKEN = re.compile(r"[a-zA-Z][a-zA-Z0-9_]{2,}")
# Floors are TRIPWIRES for a dead faculty, not targets. Measured on the source corpus 2026-09-02
# (eval_hook_fire_reasons.py, 60 harvested prompts): the live hook fires on 47-53% of prompts and
# on a mistake-class memory for 12-13%; with every gate relaxed at once it reaches 82% and 17%,
# and the mistake-class ceiling on a given prompt mix is set by the prompts, not the gates. The
# earlier floors (60 / 40) came from a different prompt mix and had been red on every nightly run
# without anyone acting. A stale mistake-weight table (nothing rebuilt it) read 3%, which fails the
# new floor; a rebuilt one reads 12-13%, which passes. If more proactive fires are wanted, the
# binding gates are SAL_FLOOR and MARGIN; ACT_FLOOR and CAND_FLOOR do nothing.
MIN_FIRE_RATE = 0.40
MIN_MISTAKE_RATE = 0.08
MIN_TOPICS = 1500
RACE_WORKERS = 8
RACE_ROUNDS = 3

results = []


def record(section, name, state, detail=""):
    results.append((section, name, state, detail))


def _read_json(path):
    with io.open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _write_bytes(path, data):
    with open(path, "wb") as handle:
        handle.write(data)


def _creationflags():
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


_RACE_WORKER = r"""
import os, sys, time
root, state, gate, start = sys.argv[1:5]
sys.path.insert(0, root)
sys.path.insert(0, os.path.join(root, "graph_fire"))
start = float(start)
while time.time() < start:
    time.sleep(0.001)
if gate == "ack":
    import ack_gate as mod
    mod.STATE = state
    mod.LOG = state + ".log"
    fired = [{"id": "secrets_in_git", "charge": 1.0, "gut": "scan", "source": "MISTAKES"}]
    data = {"session_id": "12345678-1111-2222-3333-444444444444",
            "tool_name": "PowerShell",
            "tool_input": {"command": "Write-Output race-check-action"}}
    result = mod.evaluate("PreToolUse", data, fired)
    won = bool(result and result.get("decision") == "deny")
else:
    import heed_gate as mod
    mod.STATE = state
    won = mod._claim_budget("12345678-1111-2222-3333-444444444444",
                            "secrets_in_git", now=2000000000.0)
sys.stdout.write("1" if won else "0")
"""


def _race_counts(gate, temp_root):
    counts = []
    for round_no in range(RACE_ROUNDS):
        state = os.path.join(temp_root, "%s-%d.json" % (gate, round_no))
        start = time.time() + 1.5
        procs = [
            subprocess.Popen(
                [sys.executable, "-c", _RACE_WORKER, HERE, state, gate, str(start)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                creationflags=_creationflags(),
            )
            for _ in range(RACE_WORKERS)
        ]
        winners = 0
        for proc in procs:
            out, err = proc.communicate(timeout=12)
            if proc.returncode != 0:
                raise RuntimeError("%s race worker failed: %s" % (gate, err[-240:]))
            winners += 1 if out.strip() == "1" else 0
        counts.append(winners)
    return counts


_BUDGET_RACE_WORKER = r"""
import os, sys, time
root, state, start, reflex, logical_now = sys.argv[1:6]
sys.path.insert(0, root)
sys.path.insert(0, os.path.join(root, "graph_fire"))
start = float(start)
while time.time() < start:
    time.sleep(0.001)
import ack_gate as mod
mod.STATE = state
mod.LOG = state + ".log"
fired = [{"id": reflex, "charge": 1.0, "gut": "budget race", "source": "MISTAKES"}]
data = {"session_id": "87654321-1111-2222-3333-444444444444",
        "tool_name": "PowerShell",
        "tool_input": {"command": "Write-Output budget-race-" + reflex}}
result = mod.evaluate("PreToolUse", data, fired, now=float(logical_now))
sys.stdout.write("1" if result and result.get("decision") == "deny" else "0")
"""


def _budget_race_counts(temp_root):
    reflexes = (
        "secrets_in_git",
        "preserve_before_destroy",
        "concurrent_races",
        "pinned_file_two_tripwires",
        "windowless",
        "gate_tool_needs_env",
        "healer_first_do_no_harm",
        "no_local_privileged_stack",
    )
    session = "87654321-1111-2222-3333-444444444444"
    counts = []
    for round_no in range(RACE_ROUNDS):
        state = os.path.join(temp_root, "ack-budget-race-%d.json" % round_no)
        logical_now = 2000000000.0 + round_no * 10000.0
        payload = {
            session + "|@deny_budget": [logical_now, "budget", 1.0],
        }
        _write_bytes(
            state,
            (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"),
        )
        start = time.time() + 1.5
        procs = [
            subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    _BUDGET_RACE_WORKER,
                    HERE,
                    state,
                    str(start),
                    reflex,
                    str(logical_now),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                creationflags=_creationflags(),
            )
            for reflex in reflexes
        ]
        winners = 0
        for proc in procs:
            out, err = proc.communicate(timeout=12)
            if proc.returncode != 0:
                raise RuntimeError("ACK budget race worker failed: %s" % err[-240:])
            winners += 1 if out.strip() == "1" else 0
        counts.append(winners)
    return counts


def check_enforcement():
    try:
        import ack_gate
        import enforcement
        import heed_gate
        import codex_reflex_hook as crh
    except Exception as exc:
        record("enforcement", "import live gate stack", "INCOMPLETE", repr(exc)[:160])
        return

    actual_live = enforcement.is_live(audit=False)
    record(
        "enforcement",
        "default-live control",
        "PASS" if actual_live else "FAIL",
        "" if actual_live else "an owner-disable token currently authorizes enforcement off",
    )
    for label, mod in (("heed", heed_gate), ("ack", ack_gate)):
        live = mod._enforcement_live()
        record(
            "enforcement",
            "%s gate live" % label,
            "PASS" if live else "FAIL",
            "" if live else "default-live control reported an authorized disable",
        )
        marker = os.path.exists(mod.ENABLED)
        record(
            "enforcement",
            "%s informational marker present" % label,
            "PASS" if marker else "FAIL",
            "" if marker else "marker missing (enforcement remains live, but deployment drifted)",
        )

    try:
        reason = heed_gate._reason(
            {"id": "regression_probe", "_sess": "s", "gut": "probe", "source": "MISTAKES"},
            0.9,
            (0.0, "s", "cmd", ["e"]),
            3,
        )
        hso = {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
        real_ok = crh._heed_deny(hso) is not None
        forged_ok = crh._heed_deny(
            dict(hso, permissionDecisionReason="[ack-gate]forged")
        ) is None
        record("enforcement", "real deny reaches Codex", "PASS" if real_ok else "FAIL")
        record("enforcement", "forged marker rejected", "PASS" if forged_ok else "FAIL")
    except Exception as exc:
        record("enforcement", "deny provenance path", "INCOMPLETE", repr(exc)[:160])

    real_capture = getattr(crh, "_capture", None)
    try:
        seen = {"n": 0}

        def fake(module, payload):
            if getattr(module, "__name__", "") == "instinct_prime":
                return None
            seen["n"] += 1
            return None

        crh._capture = fake
        crh.process({
            "hook_event_name": "UserPromptSubmit",
            "prompt": "the grid and leverage",
            "session_id": "11111111-2222-3333-4444-555555555555",
        })
        record(
            "enforcement",
            "silent reflex still recalls",
            "PASS" if seen["n"] else "FAIL",
            "" if seen["n"] else "association lane was skipped",
        )
    except Exception as exc:
        record("enforcement", "silent reflex still recalls", "INCOMPLETE", repr(exc)[:160])
    finally:
        if real_capture is not None:
            crh._capture = real_capture

    old_ack_state, old_ack_log = ack_gate.STATE, ack_gate.LOG
    old_heed_state = heed_gate.STATE
    old_disable, old_tamper = enforcement.DISABLE, enforcement.TAMPER_LOG
    try:
        with tempfile.TemporaryDirectory(prefix="brain-gate-regression-") as temp_root:
            ack_gate.STATE = os.path.join(temp_root, "ack.json")
            ack_gate.LOG = os.path.join(temp_root, "ack.tsv")
            heed_gate.STATE = os.path.join(temp_root, "heed.json")
            enforcement.DISABLE = os.path.join(temp_root, "disable")
            enforcement.TAMPER_LOG = os.path.join(temp_root, "enforcement.tsv")

            _write_bytes(enforcement.DISABLE, b"disable without owner authorization\n")
            unsigned_ok = enforcement.is_live(audit=False)
            _write_bytes(
                enforcement.DISABLE,
                (enforcement.AUTHORIZATION + "\nreason: isolated positive control\n").encode("utf-8"),
            )
            signed_ok = not enforcement.is_live(audit=False)
            os.remove(enforcement.DISABLE)
            record(
                "enforcement",
                "unsigned disable cannot turn gates off",
                "PASS" if unsigned_ok else "FAIL",
            )
            record(
                "enforcement",
                "explicit disable contract is testable",
                "PASS" if signed_ok else "FAIL",
            )

            session = "12345678-1111-2222-3333-444444444444"
            fired = [{
                "id": "secrets_in_git",
                "charge": 1.0,
                "gut": "scan exact staged bytes",
                "source": "MISTAKES",
            }]

            unrelated = {
                "session_id": session,
                "tool_name": "PowerShell",
                "tool_input": {
                    "command": "Write-Output first-action\n"
                               "# heeded:windowless this token is for a different scar"
                },
            }
            denied = ack_gate.evaluate("PreToolUse", unrelated, fired)
            state = _read_json(ack_gate.STATE)
            unrelated_ok = (
                denied
                and denied.get("decision") == "deny"
                and session + "|secrets_in_git" in state
                and session + "|windowless" not in state
            )
            record(
                "enforcement",
                "ACK binds applicable token",
                "PASS" if unrelated_ok else "FAIL",
                "" if unrelated_ok else "an unrelated heeded token bypassed or was recorded",
            )

            correct = dict(unrelated)
            correct["tool_input"] = {
                "command": "Write-Output retry\n"
                           "# heeded:secrets_in_git I will scan the exact staged bytes first"
            }
            retry = ack_gate.evaluate("PreToolUse", correct, fired)
            state = _read_json(ack_gate.STATE)
            ack_ok = retry is None and state[session + "|secrets_in_git"][1] == "acked"
            record("enforcement", "ACK correct retry passes", "PASS" if ack_ok else "FAIL")

            empty_session = "87654321-1111-2222-3333-444444444444"
            empty_first = dict(unrelated)
            empty_first["session_id"] = empty_session
            empty_denied = ack_gate.evaluate("PreToolUse", empty_first, fired)
            empty_retry = dict(correct)
            empty_retry["session_id"] = empty_session
            empty_result = ack_gate.evaluate("PreToolUse", empty_retry, [])
            state = _read_json(ack_gate.STATE)
            empty_ok = (
                empty_denied
                and empty_denied.get("decision") == "deny"
                and empty_result is None
                and state[empty_session + "|secrets_in_git"][1] == "acked"
            )
            record(
                "enforcement",
                "ACK signed retry registers when reflex dedup leaves fired empty",
                "PASS" if empty_ok else "FAIL",
            )

            for path in (ack_gate.STATE, ack_gate.STATE + ".lock"):
                try:
                    os.remove(path)
                except OSError:
                    pass
            ordered = [
                {"id": "outside_view", "charge": 1.0, "gut": "reason", "source": "MISTAKES"},
                fired[0],
            ]
            selected = ack_gate.evaluate(
                "PreToolUse",
                {
                    "session_id": session,
                    "tool_name": "PowerShell",
                    "tool_input": {"command": "Write-Output applicable-second-reflex"},
                },
                ordered,
            )
            record(
                "enforcement",
                "ACK selects top applicable reflex",
                "PASS" if selected and selected.get("decision") == "deny" else "FAIL",
            )

            nonuuid_state = os.path.join(temp_root, "nonuuid.json")
            ack_gate.STATE = nonuuid_state
            nonuuid = ack_gate.evaluate(
                "PreToolUse",
                {
                    "session_id": "fixture",
                    "tool_name": "PowerShell",
                    "tool_input": {"command": "Write-Output fixture"},
                },
                fired,
            )
            record(
                "enforcement",
                "ACK non-UUID is pure",
                "PASS" if nonuuid is None and not os.path.exists(nonuuid_state) else "FAIL",
            )

            ack_gate.STATE = os.path.join(temp_root, "ack-corrupt.json")
            corrupt = b"{corrupt"
            _write_bytes(ack_gate.STATE, corrupt)
            allowed = ack_gate.evaluate("PreToolUse", unrelated, fired)
            ack_corrupt_ok = allowed is None and open(ack_gate.STATE, "rb").read() == corrupt
            record(
                "enforcement",
                "ACK corruption fails open + preserves",
                "PASS" if ack_corrupt_ok else "FAIL",
            )

            heed_gate.STATE = os.path.join(temp_root, "heed-corrupt.json")
            _write_bytes(heed_gate.STATE, corrupt)
            claimed = heed_gate._claim_budget(session, "secrets_in_git", now=2000000000.0)
            heed_corrupt_ok = not claimed and open(heed_gate.STATE, "rb").read() == corrupt
            record(
                "enforcement",
                "HEED corruption fails open + preserves",
                "PASS" if heed_corrupt_ok else "FAIL",
            )

            def ack_call(target_session, reflex, clock, rationale=None):
                command = "Write-Output budget-check-%s" % reflex
                if rationale is not None:
                    command += "\n# heeded:%s %s" % (reflex, rationale)
                return ack_gate.evaluate(
                    "PreToolUse",
                    {
                        "session_id": target_session,
                        "tool_name": "PowerShell",
                        "tool_input": {"command": command},
                    },
                    [{
                        "id": reflex,
                        "charge": 1.0,
                        "gut": "bounded long-session enforcement",
                        "source": "MISTAKES",
                    }],
                    now=clock,
                )

            base = 2000000000.0
            bucket_session = "aaaaaaaa-1111-2222-3333-444444444444"
            ack_gate.STATE = os.path.join(temp_root, "ack-token-bucket.json")
            first = ack_call(bucket_session, "secrets_in_git", base)
            second = ack_call(bucket_session, "windowless", base)
            third = ack_call(bucket_session, "concurrent_races", base)
            initial_burst_ok = (
                first and first.get("decision") == "deny"
                and second and second.get("decision") == "deny"
                and third is None
            )
            record(
                "enforcement",
                "ACK fresh session has bounded two-stop burst",
                "PASS" if initial_burst_ok else "FAIL",
            )

            half_hour = ack_call(bucket_session, "concurrent_races", base + 1800.0)
            one_hour = ack_call(bucket_session, "concurrent_races", base + 3600.0)
            immediate = ack_call(bucket_session, "preserve_before_destroy", base + 3600.0)
            refill_ok = (
                half_hour is None
                and one_hour and one_hour.get("decision") == "deny"
                and immediate is None
            )
            record(
                "enforcement",
                "ACK replenishes one stop per hour",
                "PASS" if refill_ok else "FAIL",
            )

            two_hours = ack_call(
                bucket_session, "preserve_before_destroy", base + 7200.0
            )
            record(
                "enforcement",
                "ACK long session has no lifetime shutoff",
                "PASS"
                if two_hours and two_hours.get("decision") == "deny"
                else "FAIL",
            )

            idle_first = ack_call(
                bucket_session, "pinned_file_two_tripwires", base + 10 * 3600.0
            )
            idle_second = ack_call(
                bucket_session, "no_local_privileged_stack", base + 10 * 3600.0
            )
            idle_third = ack_call(
                bucket_session, "healer_first_do_no_harm", base + 10 * 3600.0
            )
            idle_cap_ok = (
                idle_first and idle_first.get("decision") == "deny"
                and idle_second and idle_second.get("decision") == "deny"
                and idle_third is None
            )
            record(
                "enforcement",
                "ACK idle time cannot accumulate a larger burst",
                "PASS" if idle_cap_ok else "FAIL",
            )

            ack_session = "bbbbbbbb-1111-2222-3333-444444444444"
            refund_first = ack_call(ack_session, "secrets_in_git", base)
            refund_ack = ack_call(
                ack_session,
                "secrets_in_git",
                base,
                rationale="I will scan the exact staged bytes before committing",
            )
            refund_second = ack_call(ack_session, "windowless", base)
            refund_third = ack_call(ack_session, "concurrent_races", base)
            refund_state = _read_json(ack_gate.STATE)
            no_refund_ok = (
                refund_first and refund_first.get("decision") == "deny"
                and refund_ack is None
                and refund_second and refund_second.get("decision") == "deny"
                and refund_third is None
                and refund_state[ack_session + "|secrets_in_git"][1] == "acked"
                and float(refund_state[ack_session + "|@deny_budget"][2]) == 0.0
            )
            record(
                "enforcement",
                "ACK rationale does not refund a deny token",
                "PASS" if no_refund_ok else "FAIL",
            )

            other_session = "cccccccc-1111-2222-3333-444444444444"
            other_first = ack_call(other_session, "secrets_in_git", base)
            other_second = ack_call(other_session, "windowless", base)
            sessions_independent = (
                other_first and other_first.get("decision") == "deny"
                and other_second and other_second.get("decision") == "deny"
            )
            record(
                "enforcement",
                "ACK deny budgets are session-local",
                "PASS" if sessions_independent else "FAIL",
            )

            malformed_session = "dddddddd-1111-2222-3333-444444444444"
            ack_gate.STATE = os.path.join(temp_root, "ack-budget-corrupt.json")
            malformed = (
                json.dumps(
                    {
                        malformed_session + "|@deny_budget": [
                            base,
                            "budget",
                            -1.0,
                        ]
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            ).encode("utf-8")
            _write_bytes(ack_gate.STATE, malformed)
            malformed_allowed = ack_call(
                malformed_session, "secrets_in_git", base
            )
            malformed_ok = (
                malformed_allowed is None
                and open(ack_gate.STATE, "rb").read() == malformed
            )
            record(
                "enforcement",
                "ACK malformed budget fails open + preserves",
                "PASS" if malformed_ok else "FAIL",
            )

            legacy_session = "eeeeeeee-1111-2222-3333-444444444444"
            ack_gate.STATE = os.path.join(temp_root, "ack-budget-legacy.json")
            legacy = {
                legacy_session + "|secrets_in_git": [base, "denied", ""],
                legacy_session + "|windowless": [base, "denied", ""],
            }
            _write_bytes(
                ack_gate.STATE,
                (json.dumps(legacy, sort_keys=True, separators=(",", ":")) + "\n").encode(
                    "utf-8"
                ),
            )
            migrated_first = ack_call(
                legacy_session, "concurrent_races", base + 3600.0
            )
            migrated_immediate = ack_call(
                legacy_session, "preserve_before_destroy", base + 3600.0
            )
            migrated_later = ack_call(
                legacy_session, "preserve_before_destroy", base + 7200.0
            )
            migrated_state = _read_json(ack_gate.STATE)
            migration_ok = (
                migrated_first and migrated_first.get("decision") == "deny"
                and migrated_immediate is None
                and migrated_later and migrated_later.get("decision") == "deny"
                and migrated_state[legacy_session + "|@deny_budget"][1] == "budget"
            )
            record(
                "enforcement",
                "ACK legacy deny history migrates conservatively",
                "PASS" if migration_ok else "FAIL",
            )

            ack_counts = _race_counts("ack", temp_root)
            heed_counts = _race_counts("heed", temp_root)
            budget_counts = _budget_race_counts(temp_root)
            record(
                "enforcement",
                "ACK cross-process claim exactly once",
                "PASS" if ack_counts == [1] * RACE_ROUNDS else "FAIL",
                "winner counts %s" % ack_counts,
            )
            record(
                "enforcement",
                "HEED cross-process claim exactly once",
                "PASS" if heed_counts == [1] * RACE_ROUNDS else "FAIL",
                "winner counts %s" % heed_counts,
            )
            record(
                "enforcement",
                "ACK cross-reflex token debit exactly once",
                "PASS" if budget_counts == [1] * RACE_ROUNDS else "FAIL",
                "winner counts %s" % budget_counts,
            )
    except Exception as exc:
        record("enforcement", "isolated state-machine suite", "INCOMPLETE", repr(exc)[:200])
    finally:
        ack_gate.STATE, ack_gate.LOG = old_ack_state, old_ack_log
        heed_gate.STATE = old_heed_state
        enforcement.DISABLE, enforcement.TAMPER_LOG = old_disable, old_tamper


def _parse_nodes(path):
    members, aliases, weights = {}, {}, {}
    with io.open(path, encoding="utf-8", errors="strict") as handle:
        header = next(handle, "").rstrip("\n")
        if header != "keyword\tkind\tweight\tmembers":
            raise ValueError("unexpected keyword_nodes.tsv header")
        for line in handle:
            parts = line.rstrip("\n").split("\t")
            if len(parts) != 4:
                raise ValueError("malformed keyword row")
            term, kind, weight, payload = parts
            weight = float(weight)
            if kind == "member":
                members[term] = payload.split(",") if payload else []
                weights[term] = weight
            elif kind == "alias":
                aliases[term] = payload
            else:
                raise ValueError("unknown keyword row kind")
    return members, aliases, weights


def _small_artifacts(label):
    return {
        "keyword_nodes.tsv": "keyword\tkind\tweight\tmembers\n%s\tmember\t1.0\tm\n" % label,
        "keyword_graph_edges.tsv": "a\tb\tweight\nkw:%s\tm\t1.0\n" % label,
        "keyword_related.tsv": "a\tb\tweight\n",
    }


def _check_publication_transaction():
    import keyword_artifacts as ka

    try:
        with tempfile.TemporaryDirectory(prefix="keyword-publication-regression-") as temp_root:
            first = ka.publish_generation(temp_root, _small_artifacts("old"), source={"v": "old"})
            old_id = first["generation_id"]
            observations = []
            cases = (
                ("after:keyword_nodes.tsv", "stage", old_id),
                ("after_generation", "promoted", old_id),
                ("after_pointer", "selected", None),
            )
            for fault, label, expected in cases:
                artifacts = _small_artifacts("new-" + label)
                try:
                    ka.publish_generation(
                        temp_root, artifacts, source={"v": label}, fault_at=fault
                    )
                except RuntimeError:
                    pass
                selected = ka.load_generation(temp_root)
                observations.append(selected["generation_id"])
                if expected is not None and selected["generation_id"] != expected:
                    raise AssertionError("%s exposed a partial/new generation" % fault)
                old_id = selected["generation_id"]

            selected = ka.load_generation(temp_root)
            corrupt_path = selected["paths"]["keyword_related.tsv"]
            with open(corrupt_path, "ab") as handle:
                handle.write(b"corruption")
            try:
                ka.load_generation(temp_root)
                hash_guard = False
            except ka.ArtifactError:
                hash_guard = True
            record(
                "artifact",
                "transaction crash is old-or-new",
                "PASS",
                "selected generations %s" % [item[:12] for item in observations],
            )
            record(
                "artifact",
                "reader rejects selected-byte drift",
                "PASS" if hash_guard else "FAIL",
            )
    except Exception as exc:
        record("artifact", "transaction crash suite", "FAIL", repr(exc)[:200])


def _check_hash_seed_determinism():
    code = (
        "import hashlib,sys;"
        "sys.path.insert(0,sys.argv[1]);"
        "import spread_recall as s;"
        "members={'topic':set('slug%03d'%i for i in range(80))};"
        "out=s._topic_expand({'topic'},members,{});"
        "print(hashlib.sha256(('\\n'.join(sorted(out))).encode()).hexdigest())"
    )
    hashes = []
    try:
        for seed in ("1", "2", "3"):
            env = os.environ.copy()
            env["PYTHONHASHSEED"] = seed
            proc = subprocess.run(
                [sys.executable, "-c", code, HERE],
                capture_output=True,
                text=True,
                env=env,
                timeout=20,
                creationflags=_creationflags(),
            )
            if proc.returncode != 0:
                raise RuntimeError(proc.stderr[-240:])
            hashes.append(proc.stdout.strip())
        record(
            "artifact",
            "hash-seed-stable truncation",
            "PASS" if len(set(hashes)) == 1 else "FAIL",
            "%d distinct selections" % len(set(hashes)),
        )
    except Exception as exc:
        record("artifact", "hash-seed-stable truncation", "INCOMPLETE", repr(exc)[:180])


JUDGE_AGENDA_BUDGET_S = 90   # the dream builds this section inline; 2026-09-02 it took 2.8 s when healthy


def check_judge_agenda():
    """The nightly judge's agenda (J1 target, J2 firehose, J3 suppressed) must build within a budget and
    carry the fields the precision table reads. 2026-09-02: a suppression reason the hook had gained that
    day was missing from the sheet builder's fixed reason list, its rows were never placed, and the
    builder's loop never ended - the dream would have hung on it all night with nothing red anywhere.
    Built in a subprocess with a hard timeout so a hang here is a FAIL, never a hang of this gate."""
    code = ("import sys, os; sys.path.insert(0, %r); import fire_judge_nightly as fj; s = fj.agenda_section(); "
            "print(len(s)); print(s.count('\"path\": \"')); print('\"reason\": \"' in s); print('suppressed' in s.lower())" % HERE)
    t0 = time.time()
    try:
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                              timeout=JUDGE_AGENDA_BUDGET_S, cwd=HERE, creationflags=_creationflags())
    except subprocess.TimeoutExpired:
        record("judge", "judge agenda builds within budget", "FAIL",
               "did not finish in %d s - the dream would hang here (a new suppression reason? see MISTAKES 215)" % JUDGE_AGENDA_BUDGET_S)
        return
    dt = time.time() - t0
    if proc.returncode != 0:
        record("judge", "judge agenda builds within budget", "FAIL", "exit %d: %s" % (proc.returncode, (proc.stderr or "").strip()[-200:]))
        return
    lines = (proc.stdout or "").strip().split("\n")
    try:
        length, n_path, has_reason, has_supp = int(lines[0]), int(lines[1]), lines[2] == "True", lines[3] == "True"
    except Exception:
        record("judge", "judge agenda builds within budget", "FAIL", "unreadable output: %r" % (proc.stdout or "")[:120])
        return
    record("judge", "judge agenda builds within budget", "PASS" if length > 1000 else "FAIL", "%.1f s, %d chars" % (dt, length))
    record("judge", "judge cases carry path and reason", "PASS" if (n_path > 0 and has_reason and has_supp) else "INCOMPLETE",
           "path on %d cases, reason %s, suppressed section %s (INCOMPLETE when the day logged no suppressed fire)" % (n_path, has_reason, has_supp))


def check_keyword_artifact():
    try:
        import build_keyword_nodes as builder
        import keyword_artifacts as ka
        import spread_recall as sr
        selected = ka.load_generation(HERE, allow_legacy=False)
        members, aliases, weights = _parse_nodes(selected["paths"]["keyword_nodes.tsv"])
    except Exception as exc:
        record("artifact", "verified generation loads", "INCOMPLETE", repr(exc)[:200])
        _check_publication_transaction()
        _check_hash_seed_determinism()
        return

    record(
        "artifact",
        "verified generation loads",
        "PASS",
        "%s / %s" % (selected["generation_id"][:12], selected["manifest_sha256"][:12]),
    )
    record(
        "artifact",
        "topic count",
        "PASS" if len(members) >= MIN_TOPICS else "FAIL",
        "%d topics (floor %d)" % (len(members), MIN_TOPICS),
    )

    try:
        prompt_counts = builder._prompt_terms()
        texts, vocab = builder._corpus()
        source = selected["manifest"].get("source", {})
        expected_source = {
            "prompt_terms_sha256": builder._mapping_digest(prompt_counts),
            "corpus_search_sha256": builder._mapping_digest(texts),
            "corpus_vocab_sha256": builder._mapping_digest(vocab),
            "memory_count": len(texts),
            "prompt_term_count": len(prompt_counts),
            "reviewed_aliases": dict(sorted(builder.REVIEWED_ALIASES.items())),
        }
        stale = [key for key, value in expected_source.items() if source.get(key) != value]
        record(
            "artifact",
            "source receipt is current",
            "PASS" if not stale else "FAIL",
            "" if not stale else "drifted fields: %s" % stale,
        )

        tokens_by_slug = {slug: set(TOKEN.findall(text)) for slug, text in texts.items()}
        bad = []
        total = 0
        for term, slugs in members.items():
            for slug in slugs:
                total += 1
                if term not in tokens_by_slug.get(slug, set()):
                    bad.append((term, slug))
        record(
            "artifact",
            "all memberships are token-exact",
            "PASS" if not bad else "FAIL",
            "%d rows checked%s" % (
                total,
                "" if not bad else "; %d bad, e.g. %s" % (len(bad), bad[:3]),
            ),
        )

        unexpected = sorted(set(aliases) - set(builder.REVIEWED_ALIASES))
        expected_aliases = {
            raw: canonical
            for raw, canonical in builder.REVIEWED_ALIASES.items()
            if canonical in members
        }
        missing = sorted(set(expected_aliases) - set(aliases))
        wrong = sorted(
            raw for raw, canonical in aliases.items()
            if builder.REVIEWED_ALIASES.get(raw) != canonical
        )
        alias_ok = not unexpected and not missing and not wrong
        record(
            "artifact",
            "aliases are owner-reviewed only",
            "PASS" if alias_ok else "FAIL",
            "%d aliases; unexpected=%s missing=%s wrong=%s"
            % (len(aliases), unexpected[:4], missing[:4], wrong[:4]),
        )
        dangerous = {"addrs", "appleid", "arise", "basics", "beasts", "beets",
                     "bland", "brands", "bride", "canton"}
        record(
            "artifact",
            "intentional-word alias negatives",
            "PASS" if dangerous.isdisjoint(aliases) else "FAIL",
            "" if dangerous.isdisjoint(aliases) else str(sorted(dangerous & set(aliases))),
        )

        synthetic_members = {
            "topic%02d" % topic: set(
                "t%02d-slug%03d" % (topic, member) for member in range(50)
            )
            for topic in range(sr.TOPIC_MAX_HITS)
        }
        synthetic_hit = set(synthetic_members)
        synthetic_weights = {term: 1.0 for term in synthetic_members}
        synthetic_related = defaultdict(list)
        for term in list(synthetic_members):
            for related_no in range(8):
                other = "related%02d" % related_no
                synthetic_members.setdefault(
                    other,
                    set("r%02d-slug%03d" % (related_no, member) for member in range(50)),
                )
                synthetic_weights[other] = 0.5
                synthetic_related[term].append((other, 1.0 - related_no / 20.0))
        overlay = sr._topic_overlay(
            synthetic_hit, {}, synthetic_members, {}, synthetic_hit,
            weights=synthetic_weights, related=synthetic_related,
        )
        directed = sum(len(edges) for edges in overlay.values())
        max_directed = 2 * sr.TOPIC_OVERLAY_MAX_EDGES + 120
        overlay_ok = directed <= max_directed and len(overlay) <= 140
        record(
            "artifact",
            "overlay fanout is structurally bounded",
            "PASS" if overlay_ok else "FAIL",
            "%d directed entries / %d nodes (bounds %d / 140)"
            % (directed, len(overlay), max_directed),
        )
    except Exception as exc:
        record("artifact", "exact corpus + overlay audit", "INCOMPLETE", repr(exc)[:200])

    _check_publication_transaction()
    _check_hash_seed_determinism()


def _percentile(values, q):
    if not values:
        return 0
    values = sorted(values)
    return values[int(round((len(values) - 1) * q))]


def check_recall(n_prompts):
    try:
        import eval_mistake_surfacing as em
        import spread_recall as sr
        adj, deg = sr._load_graph()
        if not adj or not deg:
            raise RuntimeError("memory graph is empty")
        record("recall", "brain.py graph-loader compatibility", "PASS", "%d nodes" % len(adj))
    except Exception as exc:
        record("recall", "load replay + graph", "INCOMPLETE", repr(exc)[:180])
        return

    try:
        prompts = em.harvest(n_files=8)[:n_prompts]
    except Exception as exc:
        record("recall", "harvest prompts", "INCOMPLETE", repr(exc)[:180])
        return
    if len(prompts) < 20:
        record("recall", "harvest prompts", "INCOMPLETE", "only %d prompts" % len(prompts))
        return

    def run(prompt, tag, index, overlay_on):
        real_overlay = sr._topic_overlay
        if not overlay_on:
            sr._topic_overlay = lambda *args, **kwargs: defaultdict(list)
        old_in, old_out = sys.stdin, sys.stdout
        sys.stdin = io.StringIO(json.dumps({
            "hook_event_name": "UserPromptSubmit",
            "prompt": prompt,
            "session_id": "regress-%s-%d" % (tag, index),
        }))
        sys.stdout = capture = io.StringIO()
        try:
            sr.main()
        except Exception:
            return None
        finally:
            sys.stdin, sys.stdout = old_in, old_out
            sr._topic_overlay = real_overlay
        output = capture.getvalue().strip()
        if not output:
            return None
        try:
            message = json.loads(output)["hookSpecificOutput"]["additionalContext"]
            match = re.search(r"this reminds me of `([^`]+)`", message)
            return match.group(1) if match else None
        except Exception:
            return None

    try:
        mw = sr._load_mistake_weights()
        off, on = [], []
        edge_counts, node_counts, hit_counts = [], [], []
        sal = sr._load_salience()
        for index, prompt in enumerate(prompts):
            off.append(run(prompt, "off", index, False))
            on.append(run(prompt, "on", index, True))
            words = sr._words(prompt)
            members, aliases, hit, weights, related, _generation = sr._topic_context(words)
            overlay = sr._topic_overlay(
                words, sal, members, aliases, hit, weights=weights, related=related
            )
            hit_counts.append(len(hit))
            edge_counts.append(sum(len(value) for value in overlay.values()))
            node_counts.append(len(overlay))

        fires = sum(1 for value in on if value)
        mistakes = sum(1 for value in on if value and mw.get(value, 0) >= 0.5)
        rate = fires / float(len(on))
        mistake_rate = mistakes / float(len(on))
        record(
            "recall",
            "fire rate",
            "PASS" if rate >= MIN_FIRE_RATE else "FAIL",
            "%.0f%% of %d (floor %.0f%%)" % (rate * 100, len(on), MIN_FIRE_RATE * 100),
        )
        record(
            "recall",
            "mistake-class fire rate",
            "PASS" if mistake_rate >= MIN_MISTAKE_RATE else "FAIL",
            "%.0f%% (floor %.0f%%)" % (mistake_rate * 100, MIN_MISTAKE_RATE * 100),
        )
        lost = [a for a, b in zip(off, on) if a and not b]
        record(
            "recall",
            "keyword arm silences nothing",
            "PASS" if not lost else "FAIL",
            "" if not lost else "%d baseline fires lost, e.g. %s" % (len(lost), lost[:3]),
        )
        max_edges = 2 * sr.TOPIC_OVERLAY_MAX_EDGES + 120
        fanout_ok = max(edge_counts) <= max_edges and max(node_counts) <= 140
        record(
            "recall",
            "real-prompt fanout bound",
            "PASS" if fanout_ok else "FAIL",
            "hits p50/p95/max=%d/%d/%d; edges=%d/%d/%d; nodes max=%d"
            % (
                _percentile(hit_counts, 0.5), _percentile(hit_counts, 0.95), max(hit_counts),
                _percentile(edge_counts, 0.5), _percentile(edge_counts, 0.95), max(edge_counts),
                max(node_counts),
            ),
        )
    except Exception as exc:
        record("recall", "real-prompt replay", "INCOMPLETE", repr(exc)[:200])


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # Discovery is not execution: this script writes, so a help request must
        # exit before any work. See script_help_flag_can_execute_work.
        print(__doc__ or os.path.basename(__file__))
        return
    global results
    results = []
    n = 60
    if "--prompts" in sys.argv:
        try:
            n = int(sys.argv[sys.argv.index("--prompts") + 1])
        except Exception:
            pass
    check_enforcement()
    check_judge_agenda()
    check_keyword_artifact()
    check_recall(n)

    width = max((len(item[1]) for item in results), default=1) + 2
    section = None
    for sec, name, state, detail in results:
        if sec != section:
            print("\n[%s]" % sec)
            section = sec
        label = "ok" if state == "PASS" else state
        print("  %-10s %-*s %s" % (label, width, name, detail))

    fails = [item for item in results if item[2] == "FAIL"]
    incomplete = [item for item in results if item[2] == "INCOMPLETE"]
    print(
        "\n%d checks: %d pass, %d FAIL, %d INCOMPLETE"
        % (len(results), len(results) - len(fails) - len(incomplete), len(fails), len(incomplete))
    )
    if fails:
        print("VERDICT: FAIL")
        return 1
    if incomplete:
        print("VERDICT: INCOMPLETE - an uninspected property is not a pass")
        return 2
    print("VERDICT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
