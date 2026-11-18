"""Native fresh-root acceptance, with actual command/output evidence and an allowed/defect pair."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tomllib

sys.dont_write_bytecode = True
import connector as c


def deployment_fingerprint(deployment):
    profile = Path(deployment["profile"])
    skill = profile / "skills/brain-framework"
    data = {"revision": deployment["source_revision"], "runtime_code": deployment["runtime_code"],
            "enabled": deployment.get("enabled", []), "handlers": deployment.get("handlers", {}),
            "hooks": deployment.get("managed_hook_groups", {}),
            "skill_code": {str(p.relative_to(skill)): c.sha(p) for p in sorted((skill / "scripts").glob("*.py"))},
            "skill_instructions": c.sha(skill / "SKILL.md")}
    binding = c.state_dir(profile) / "brain-bindings.json"
    data["brain_binding"] = c.sha(binding) if binding.is_file() else None
    data["effective_hooks"] = c.sha(deployment["hook_config"]) if Path(deployment.get("hook_config", "")).is_file() else None
    data["references"] = {str(p.relative_to(skill)): c.sha(p) for p in sorted((skill / "references").glob("*")) if p.is_file()}
    data["effective_startup"] = {str(p): c.sha(p) for p in (profile / "AGENTS.md", profile / "AGENTS.override.md") if p.is_file()}
    config = profile / "config.toml"
    data["native_hook_trust"] = tomllib.loads(config.read_text()).get("hooks", {}) if config.exists() else {}
    data["native_brain_write_scope"] = tomllib.loads(config.read_text()).get("sandbox_workspace_write", {}).get("writable_roots", []) if config.exists() else []
    observer = deployment.get("guardian", {})
    data["guardian"] = observer
    data["guardian_bytes"] = {p: c.sha(p) if Path(p).is_file() else None
                              for p in [*observer.get("files", {}), *([observer["manifest"]] if observer.get("manifest") else [])]}
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def completed_commands(events):
    result = []
    for line in Path(events).read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        item = row.get("item", {})
        if row.get("type") == "item.completed" and item.get("type") == "command_execution":
            result.append(item)
    return result


def worker_dispatch_observation(rows):
    raw = [r.get("payload", {}) for r in rows if r.get("payload", {}).get("type") == "function_call"
           and r.get("payload", {}).get("name") == "spawn_agent"]
    started = {r.get("payload", {}).get("item", {}).get("agent_thread_id") for r in rows
               if r.get("type") == "event_msg" and r.get("payload", {}).get("type") == "item_completed"
               and r.get("payload", {}).get("item", {}).get("type") == "SubAgentActivity"
               and r.get("payload", {}).get("item", {}).get("kind") == "started"}
    started.discard(None)
    if (raw and len(raw) != 1) or (started and len(started) != 1) or (not raw and not started):
        raise c.ConnectorError("Expected exactly one native worker dispatch/start observation")
    return {"raw_dispatches": len(raw), "started_ids": sorted(started)}


def inspect_root(deployment, folder, require_worker=False):
    folder = Path(folder)
    result = c.read_json(folder / "decision.json")
    request = c.read_json(folder / "request.json")
    records = c.read_json(request["records_file"])
    if not isinstance(records, list) or not all(isinstance(x, dict) and x.get("id") for x in records):
        raise c.ConnectorError("Probe records changed shape")
    by_id = {row["id"]: row for row in result.get("cases", [])}
    for case in request["cases"]:
        row = by_id.get(case["id"], {})
        if row.get("actual_count") != len(records) or row.get("supported") is not (case["claimed_count"] == len(records)):
            raise c.ConnectorError("Fresh root failed the actual-state allowed/defect pair")
    if result.get("source_revision") != deployment["source_revision"]:
        raise c.ConnectorError("Fresh root reported a different original revision")
    commands = completed_commands(folder / "events.jsonl")
    successful = [row for row in commands if row.get("exit_code") == 0]
    texts = "\n".join(str(row.get("command", "")) + "\n" + str(row.get("aggregated_output", "")) for row in successful)
    for required in ("brain_startup.template.md", "METHOD.md", "NOTES_ARE_HINTS.md"):
        if required not in texts:
            raise c.ConnectorError("Native trace lacks successful original-source consultation: " + required)
    recall = [row for row in successful if "brainctl.py" in str(row.get("command", "")) and "recall" in str(row.get("command", ""))]
    if not recall:
        raise c.ConnectorError("Native trace lacks a successful bound recall invocation")
    if not any(deployment["canary"] in str(row.get("aggregated_output", "")) for row in recall):
        raise c.ConnectorError("Native recall did not demonstrate retrieval from this installed corpus")
    final = (folder / "final.txt").read_text()
    if not final.strip():
        raise c.ConnectorError("Native session did not finish with a report")
    rows = [json.loads(line) for line in (folder / "events.jsonl").read_text().splitlines() if line.strip()]
    if not any(row.get("type") == "turn.completed" for row in rows):
        raise c.ConnectorError("Native turn is not complete")
    thread = next((row.get("thread_id") for row in rows if row.get("type") == "thread.started"), None)
    log = Path(deployment["home"]).parent / "hook-delivery.jsonl"
    deliveries = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    session_hash = hashlib.sha256(str(thread).encode()).hexdigest()
    matching = [row for row in deliveries if row.get("session_hash") == session_hash]
    observed = {row["event"] for row in matching if row.get("rc") == 0}
    if not {"SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop"} <= observed:
        raise c.ConnectorError("Actual receiver lacks the required native event chain")
    if not any(row.get("event") == "SessionStart" and row.get("output_bytes", 0) for row in matching):
        raise c.ConnectorError("No receiver wake context was delivered")
    if len([row for row in rows if row.get("item", {}).get("type") == "agent_message"]) > 12:
        raise c.ConnectorError("Native receiver produced excessive continuation replies; inspect Stop gates")
    worker = {"verified": False}
    worker_hashes = {}
    if require_worker:
        if not any(row.get("event") == "SubagentStart" and row.get("output_bytes", 0) for row in matching):
            raise c.ConnectorError("No native worker received the source/recall contract")
        sessions = Path(deployment["profile"]) / "sessions"
        parent_rollouts = list(sessions.rglob("*" + str(thread) + "*.jsonl"))
        if not parent_rollouts:
            raise c.ConnectorError("Native parent rollout is unavailable for worker verification")
        parent_rows = []
        for rollout in parent_rollouts:
            native_rows = [json.loads(line) for line in rollout.read_text().splitlines() if line.strip()]
            parent_rows.extend(native_rows)
            worker_hashes[str(rollout)] = c.sha(rollout)
        dispatch = worker_dispatch_observation(parent_rows)
        worker_field = result.get("worker", {})
        child_id = str(worker_field.get("worker_id") or "")
        if not child_id:
            raise c.ConnectorError("Parent did not return the native worker id")
        # Codex returns a canonical task path, while rollout filenames use a UUID.
        # Bind both through native session metadata, never through a model-supplied filename.
        child_rollouts = []
        for path in sessions.rglob("*.jsonl"):
            with path.open(encoding="utf-8") as stream:
                first = json.loads(stream.readline())
            meta = first.get("payload", {})
            if meta.get("parent_thread_id") == thread and child_id in {meta.get("id"), meta.get("agent_path")}:
                if dispatch["started_ids"] and meta.get("id") not in dispatch["started_ids"]:
                    raise c.ConnectorError("Worker start event differs from native child metadata")
                child_rollouts.append(path)
        if not child_rollouts:
            raise c.ConnectorError("Native worker rollout is unavailable: " + child_id)
        child_rows = [json.loads(line) for path in child_rollouts for line in path.read_text().splitlines() if line.strip()]
        child_commands = [row.get("payload", {}).get("item", {}) for row in child_rows
                          if row.get("type") == "event_msg" and row.get("payload", {}).get("type") == "item_completed"]
        child_commands = [row for row in child_commands if row.get("type") == "CommandExecution" and row.get("exit_code") == 0]
        child_text = "\n".join(str(row.get("command", "")) + "\n" + str(row.get("aggregated_output", "")) for row in child_commands)
        for required in ("brain_startup.template.md", "METHOD.md", "brainctl.py", "recall"):
            if required not in child_text:
                raise c.ConnectorError("Worker trace lacks original consultation or scoped recall: " + required)
        if not worker_field.get("verified_by_parent"):
            raise c.ConnectorError("Parent did not independently verify the worker's case")
        expected = by_id["claim-a"]
        decision = worker_field.get("decision", {})
        if any(decision.get(k) != expected.get(k) for k in ("id", "supported", "actual_count")):
            raise c.ConnectorError("Worker's consequential decision disagrees with current raw artifacts")
        if not any(row.get("payload", {}).get("type") == "task_complete" for row in child_rows):
            raise c.ConnectorError("Native worker did not complete")
        # Raw IDs must appear in each receiver's independently executed evidence inspection.
        if not all(str(record["id"]) in child_text and str(record["id"]) in texts for record in records):
            raise c.ConnectorError("Parent/worker trace lacks independent inspection of the actual records")
        for rollout in child_rollouts:
            worker_hashes[str(rollout)] = c.sha(rollout)
        worker = {"verified": True, "worker_id": child_id, "rollouts": [str(p) for p in child_rollouts],
                  "dispatch_observation": dispatch,
                  "source_contract": "Observed native SubagentStart context plus original-source/recall trace"}
    return {"thread_id": thread, "cases": len(request["cases"]), "actual_count": len(records), "worker": worker,
            "source_consulted": True, "recall_commands": len(recall), "native_events": sorted(observed),
            "served_model": "unknown unless independently established from authoritative response metadata",
            "fallback_events": [row for row in rows if row.get("type") in {"error", "turn.failed"}],
            "artifact_hashes": {**worker_hashes, **{str(path): c.sha(path) for path in (folder / "request.json", folder / "records.json", folder / "decision.json", folder / "events.jsonl", folder / "final.txt")}}}


def run_root(profile, *, cli=None, reviewed_automation=False, with_worker=False, timeout=900):
    if not 1 <= timeout <= 3600:
        raise c.ConnectorError("Receiver deadline must be 1..3600 seconds")
    deployment = c.active(profile)
    cli = cli or shutil.which("codex")
    if not cli:
        raise c.ConnectorError("Codex CLI is unavailable; qualify the actual target runtime instead")
    generation = Path(deployment["home"]).parent
    folder = generation / "receiver-probes" / secrets.token_hex(6)
    folder.mkdir(parents=True)
    count = 2 + secrets.randbelow(3)
    records = [{"id": secrets.token_hex(5), "value": secrets.randbelow(100)} for _ in range(count)]
    c.atomic(folder / "records.json", records)
    cases = [{"id": "claim-a", "claimed_count": count + 1}, {"id": "claim-b", "claimed_count": count}]
    c.atomic(folder / "request.json", {"records_file": str(folder / "records.json"), "cases": cases})
    prompt = ("$brain-framework This is an authorized connector qualification task in the skill's maintenance lane, not ordinary admitted work. "
              "Run fresh admission, retain its actual failed/open gates, then perform only this bounded receiver verification without certifying overall readiness. "
              "Read the pinned hooks/brain_startup.template.md, docs/METHOD.md and docs/NOTES_ARE_HINTS.md. "
              "Assess both import-count claims in " + str(folder / "request.json") + ". Check current artifacts. "
              "Also identify this Brain installation's source provenance from its actual memory. Write " + str(folder / "decision.json")
              + " with cases [{id,supported:boolean,actual_count:integer}], source_revision, source_sections, recalled_nodes, evidence_paths. "
              "Use the installed Brain Framework and report briefly. You may use this profile's normal scoped recall/cache/telemetry "
              "and mesh/checklist/verification-receipt writes. Include the current setup-checklist warning in your user-facing update. "
              "Read only this fixture, the installed skill/original source and this profile's Brain. "
              "Write task artifacts only inside " + str(folder) + ". Do not preserve transient probe findings as durable memories. "
              "Do not change configuration, install software, start services, publish, access other profiles or message others.")
    if with_worker:
        prompt += (" For claim-a, dispatch exactly one fresh read-only worker with the raw task artifacts, this installed skill, "
                   "the full original revision/path, required original startup/method, and this profile's permitted recall scope. "
                   "Withhold the intended answer and your suspected finding. Require source acknowledgment, actual evidence and "
                   "a source-backed decision. Inspect its work and independently check its consequential answer. "
                   "Include worker: {worker_id, source_sections, decision, evidence_paths, verified_by_parent:boolean} in decision.json. "
                   "No nested workers. Give the worker only this task's authorized scope.")
    c.atomic(folder / "prompt.txt", prompt.encode())
    c.atomic(folder / "invocation.json", {"profile": str(profile), "deployment": deployment["id"],
                                         "revision": deployment["source_revision"], "reviewed_automation": bool(reviewed_automation),
                                         "deployment_fingerprint": deployment_fingerprint(deployment),
                                         "fixture_hashes": {name: c.sha(folder / name)
                                                            for name in ("prompt.txt", "request.json", "records.json")},
                                         "sandbox_mode": "workspace-write", "brain_write_root": str(c.state_dir(profile))})
    # Normal config discovery is essential: --ignore-user-config also discards persisted hook trust.
    command = [str(cli), "--no-daemon", "exec", "--skip-git-repo-check", "--json", "--sandbox", "workspace-write",
               "-c", 'approval_policy="never"', "--add-dir", str(c.state_dir(profile)), "-C", str(folder), "-o", str(folder / "final.txt")]
    if reviewed_automation:
        command.append("--dangerously-bypass-hook-trust")
    command.append(prompt)
    with (folder / "events.jsonl").open("w") as output, (folder / "stderr.log").open("w") as error:
        process = subprocess.Popen(command, env={**os.environ, "CODEX_HOME": str(profile)}, stdin=subprocess.DEVNULL,
                                   stdout=output, stderr=error, start_new_session=(os.name != "nt"),
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        c.wait_owned(process, timeout=timeout)
    if process.returncode:
        raise c.ConnectorError("Native receiver failed; inspect " + str(folder))
    return accept_root(deployment, folder, with_worker=with_worker, reviewed_automation=reviewed_automation)


def resume_root(profile, folder, *, cli=None, with_worker=False, timeout=900):
    if not 1 <= timeout <= 3600:
        raise c.ConnectorError("Receiver deadline must be 1..3600 seconds")
    deployment = c.active(profile)
    folder = c.absolute(folder).resolve(strict=True)
    permitted = (Path(deployment["home"]).parent / "receiver-probes").resolve(strict=True)
    if folder == permitted or not folder.is_relative_to(permitted):
        raise c.ConnectorError("Resume fixture is outside this deployment's receiver probes")
    origin = c.read_json(folder / "invocation.json")
    if (origin.get("profile") != str(profile) or origin.get("deployment") != deployment["id"]
            or origin.get("revision") != deployment["source_revision"] or not isinstance(origin.get("reviewed_automation"), bool)
            or origin.get("deployment_fingerprint") != deployment_fingerprint(deployment)
            or origin.get("sandbox_mode") != "workspace-write" or origin.get("brain_write_root") != str(c.state_dir(profile))):
        raise c.ConnectorError("Resume origin is missing/mismatched; retain the original session without claiming receiver acceptance")
    rows = [json.loads(line) for line in (folder / "events.jsonl").read_text().splitlines() if line.strip()]
    threads = {row["thread_id"] for row in rows if row.get("type") == "thread.started"}
    if len(threads) != 1:
        raise c.ConnectorError("Resume requires exactly one recorded native session id")
    thread = next(iter(threads))
    cli = cli or shutil.which("codex")
    if not cli:
        raise c.ConnectorError("Codex CLI is unavailable")
    prompt = ("Continue this exact interrupted connector qualification session and existing fixture " + str(folder)
              + ". Maintenance scope only; preserve failed ordinary admission. Reopen the current installed Brain skill, "
              "pinned startup/METHOD and changed admission/selection references. Main is still the explicitly designated "
              "profile " + str(profile) + "; no project. Recheck current main resolution/admission for this session and workspace. "
              "Complete the existing parent/worker artifact verification; do not start a new root or replacement worker. "
              "You may locate only this root's and its existing child's saved rollouts using session metadata under this profile's sessions directory, and inspect only those two rollouts for source/evidence verification. No other chat history is authorized. "
              "Resume or follow up the existing worker with the same original revision/main scope if necessary. "
              "Independently inspect its actual source/recall/evidence and return the original decision.json schema. "
              "Do not mutate installed configuration, durable memory, services or external state. Finish your mesh/checklist ownership and report briefly.")
    command = [str(cli), "--no-daemon", "exec", "resume", "--skip-git-repo-check", "--json",
               "-c", 'sandbox_mode="workspace-write"', "-c", 'approval_policy="never"',
               "-o", str(folder / "final.txt"), thread, prompt]
    with (folder / "events.jsonl").open("a") as output, (folder / "stderr.log").open("a") as error:
        process = subprocess.Popen(command, env={**os.environ, "CODEX_HOME": str(profile)}, stdin=subprocess.DEVNULL,
                                   stdout=output, stderr=error, start_new_session=(os.name != "nt"),
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        c.wait_owned(process, timeout=timeout)
    if process.returncode:
        raise c.ConnectorError("Resumed native receiver failed; retain session " + thread + " and inspect " + str(folder))
    return accept_root(deployment, folder, with_worker=with_worker, reviewed_automation=origin["reviewed_automation"])


def verify_probe_origin(deployment, folder, reviewed_automation, fingerprint):
    origin = c.read_json(folder / "invocation.json")
    if (origin.get("profile") != deployment["profile"] or origin.get("deployment") != deployment["id"]
            or origin.get("revision") != deployment["source_revision"]
            or origin.get("deployment_fingerprint") != fingerprint
            or origin.get("reviewed_automation") is not reviewed_automation):
        raise c.ConnectorError("Receiver qualification must start on the exact current deployment and trust scope")
    fixture_hashes = {name: c.sha(folder / name) for name in ("prompt.txt", "request.json", "records.json")}
    if origin.get("fixture_hashes") != fixture_hashes:
        raise c.ConnectorError("Receiver input fixture changed during qualification")
    return fixture_hashes


def accept_root(deployment, folder, *, with_worker=False, reviewed_automation=False):
    folder = Path(folder)
    generation = Path(deployment["home"]).parent
    profile = c.profile_path(deployment["profile"])
    fingerprint = deployment_fingerprint(deployment)
    fixture_hashes = verify_probe_origin(deployment, folder, reviewed_automation, fingerprint)
    observed = inspect_root(deployment, folder, require_worker=with_worker)
    if deployment_fingerprint(c.active(profile)) != fingerprint:
        raise c.ConnectorError("Receiver deployment changed while accepting its observations")
    observed["artifact_hashes"].update({str(folder / name): digest for name, digest in fixture_hashes.items()})
    observed["artifact_hashes"][str(folder / "invocation.json")] = c.sha(folder / "invocation.json")
    report = {"schema": 1, "at": c.now(), "passed": True, "root_verified": True, "worker_verified": observed["worker"]["verified"],
              "profile": str(profile), "deployment": deployment["id"], "revision": deployment["source_revision"],
              "deployment_fingerprint": fingerprint, "root": observed,
              "trust_scope": "reviewed-automation, one invocation only" if reviewed_automation else "native persisted review/policy",
              "folder": str(folder), "remaining": [] if observed["worker"]["verified"] and not reviewed_automation else ["Independent worker acceptance and actual target trust scope must be proven."]}
    c.atomic(generation / "root-receiver-verification.json", report)
    if observed["worker"]["verified"]:
        c.atomic(generation / "receiver-verification.json", {**report, "artifact_hashes": observed["artifact_hashes"]})
        c.native(deployment, "script", ["delegation/ledger.py", "log", "--task-type", "data_analysis", "--worker", "codex-native-worker",
                                       "--requested-model", "runtime-default", "--verdict", "UNVERIFIABLE", "--parent-verdict", "accept",
                                       "--task-id", "receiver-" + observed["thread_id"], "--notes", "Actual fresh root/worker source and evidence rechecked; served-model response metadata unavailable. Receipt: " + str(generation / "receiver-verification.json")])
    return report


def validate_receivers(deployment):
    path = Path(deployment["home"]).parent / "receiver-verification.json"
    if not path.exists():
        return {"passed": False, "reason": "Fresh root and worker acceptance not recorded"}
    report = c.read_json(path)
    if report.get("deployment_fingerprint") != deployment_fingerprint(deployment) or report.get("deployment") != deployment["id"]:
        return {"passed": False, "reason": "Receiver evidence belongs to different code/configuration"}
    hashes = report.get("artifact_hashes", {})
    if not hashes or len(hashes) < 7 or not report.get("folder"):
        return {"passed": False, "reason": "Receiver receipt lacks native parent/worker artifacts"}
    for name, digest in hashes.items():
        if not Path(name).is_file() or c.sha(name) != digest:
            return {"passed": False, "reason": "Receiver evidence changed: " + name}
    if not report.get("root_verified") or not report.get("worker_verified") or report.get("trust_scope") != "native persisted review/policy":
        return {"passed": False, "reason": "Required root, worker or persisted runtime trust gate is missing"}
    try:
        folder = Path(report["folder"])
        if str(folder / "invocation.json") not in hashes:
            raise c.ConnectorError("Receiver receipt has no hashed invocation evidence")
        verify_probe_origin(deployment, folder, False, report["deployment_fingerprint"])
        inspect_root(deployment, folder, require_worker=True)
    except (OSError, ValueError, KeyError, c.ConnectorError) as exc:
        return {"passed": False, "reason": "Receiver evidence no longer passes: " + str(exc)}
    return {"passed": True, "receipt": str(path)}
