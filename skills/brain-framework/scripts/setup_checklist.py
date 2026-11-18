"""Source-backed setup obligations and current observations; unchecked is never silently complete."""
from __future__ import annotations

import json
from pathlib import Path

import connector as c


# Qualification runs keep these original requirements under the same IDs.
# They describe a release, second installation, migration or state transfer.
QUALIFICATION_ONLY = {
    "S02": {1: "Verify offline archive, real network acquisition, cache tamper/missing/untracked controls",
            2: "Review upstream changes as inactive candidates; never silently repin"},
    "S03": {1: "Exclude lived state and credentials from the transferable package",
            2: "Install the actual public package into a second ignorant profile"},
    "S04": {2: "Preserve existing user-authored identities, memories and domain boundaries during migration"},
    "S20": {2: "Check propagation and transport do not expose credentials or private histories"},
    "S25": {0: "Transfer only the public locked source/skill and operator-authorized state",
            1: "Prove second empty profile install from that exact package",
            2: "Verify target-native activation, root/worker behavior and remaining input checklist"},
}


def selected_capabilities(deployment):
    """Include faculties actually wired into core hooks, without selecting domain engines from cues."""
    selected = {name: ["deployment.enabled"] for name in deployment.get("enabled", [])}
    hook_faculties = {
        "reflex": {"codex_reflex_hook.py", "instinct_prime.py", "danger_mode.py"},
        "checklist": {"task_checklist_hook.py", "owner_prompts_hook.py", "prompt_archive.py"},
        "continuity": {"compact_hooks.py", "restart_handoff_hook.py"},
        "governor": {"compute_sense.py"},
    }
    for spec in deployment.get("handlers", {}).values():
        names = {Path(str(arg)).name for arg in spec.get("argv", [])}
        for capability, scripts in hook_faculties.items():
            for script in sorted(names & scripts):
                reasons = selected.setdefault(capability, [])
                if script not in reasons:
                    reasons.append(script)
    return selected


def inspect(profile, refresh=False, *, observation=None, persist=True):
    catalog = c.read_json(c.SKILL / "references/mechanisms.json")
    deployment = c.active(profile, required=False)
    report = {"schema": 1, "at": c.now(), "source_revision": catalog["revision"], "profile": str(profile),
              "scope": "Setup obligations from the pinned public repository; native qualification is limited to the current computer.",
              "items": [], "complete": False}
    report["selected_capabilities"] = selected_capabilities(deployment) if deployment else {}
    verified = set()
    receiver = False
    receiver_result = {}
    supplied_observation = observation
    observation = {}
    if deployment:
        if supplied_observation is not None:
            observation = supplied_observation
        elif refresh:
            observation = c.verify(deployment, integrated=True, hooks=False, smoke=False)
        # A changed source/code/config makes previous observations insufficient even without a full refresh.
        try:
            c.verify_source(c.state_dir(profile) / "sources" / deployment["source_revision"], strict=True)
            c.verify_runtime_code(deployment)
            current = c.read_json(deployment["hook_config"]).get("hooks", {})
            if any(g not in current.get(e, []) for e, groups in deployment.get("managed_hook_groups", {}).items() for g in groups):
                raise c.ConnectorError("Runtime hook registration changed")
            if (c.state_dir(profile) / "DISCONNECTED").exists():
                raise c.ConnectorError("Profile is disconnected")
            from receiver_probe import validate_receivers
            receiver_result = validate_receivers(deployment)
            receiver = receiver_result.get("passed", False)
            if observation and (observation.get("deployment") != deployment["id"]
                                or observation.get("revision") != deployment["source_revision"]):
                raise c.ConnectorError("Observed checks belong to a different deployment or source revision")
            verified = {row["name"] for row in observation.get("checks", []) if row.get("passed") is True}
        except (c.ConnectorError, OSError, ValueError, KeyError) as exc:
            report["observation_error"] = str(exc)
    rules = {
        "S01": ("locked upstream source", "Source/code integrity is current"),
        "S02": ("locked upstream source", "Exact public source is available locally"),
        "S04": ("installation provenance node/recall readback", "Native corpus provenance is read back"),
        "S06": ("effective startup and hook registrations", "Effective marked startup and owned hooks were checked"),
        "S09": ("installation provenance node/recall readback", "Current private corpus recall succeeds"),
        "S10": ("semantic artifacts and actual query encoder", "Production encoder and corpus coverage are current"),
    }
    for row in catalog["mechanisms"]:
        status, detail = "unchecked", "Required source-backed setup/admission evidence has not been established for this profile"
        if row["id"] in rules:
            name, detail = rules[row["id"]]
            if name in verified:
                status = "observed"
        if row["id"] in {"S05", "S07", "S17", "S31", "S34"} and receiver:
            status, detail = "observed", "Current native root + worker source/recall/decision and automatic delivery receipt validates"
        if row.get("external_inputs"):
            status, detail = "awaiting-input-or-admission", "Operator inputs and native admission must be checked; these are not silently marked optional-complete"
        # A top-level mechanism carries more than one obligation. Only the specific observed subcheck
        # is established by these checks; every unmeasured subcheck remains visibly open.
        checks = row.get("setup_checks", [row["setup"], row["admission"]])
        qualification = QUALIFICATION_ONLY.get(row["id"], {})
        if any(i >= len(checks) or checks[i] != requirement for i, requirement in qualification.items()):
            raise c.ConnectorError("Review qualification-only scope for changed obligation " + row["id"])
        report["items"].append({"id": row["id"], "mechanism": row["mechanism"], "capability": row["capability"], "status": status,
                                "observation": detail, "source_paths": row["source_paths"],
                                "checks": [{"requirement": task, "status": "unchecked",
                                            "scope": "qualification" if i in qualification else "admission"}
                                           for i, task in enumerate(checks)],
                                "admission": row["admission"], "missing_inputs": row.get("external_inputs", [])})
    observations = {row["id"]: row for row in report["items"] if row["status"] == "observed"}
    # These individually named obligations have exactly the corresponding runtime observation.
    direct = {"S01": [0], "S02": [0], "S04": [0], "S06": [0], "S09": [0], "S10": [0],
              "S05": [0], "S07": [0], "S17": [0], "S31": [0], "S34": [0]}
    for key, indices in direct.items():
        if key in observations:
            for index in indices:
                if index < len(observations[key]["checks"]):
                    observations[key]["checks"][index]["status"] = "checked-current-observation"
    # Manual/extended admissions are bound to code, exact evidence bytes and this deployment.
    evidence_file = c.state_dir(profile) / "setup-evidence.json"
    if deployment and evidence_file.exists():
        from receiver_probe import deployment_fingerprint
        entries = c.read_json(evidence_file)
        for item in report["items"]:
            for index, task in enumerate(item["checks"]):
                key = item["id"] + ":" + str(index)
                entry = entries.get(key, {})
                if (entry.get("deployment") == deployment["id"] and entry.get("fingerprint") == deployment_fingerprint(deployment)
                        and entry.get("verdict") == "pass" and entry.get("evidence")
                        and all(Path(p).is_file() and c.sha(p) == digest for p, digest in entry["evidence"].items())):
                        task.update({"status": "checked-evidence", "evidence": entry["evidence"],
                                     "evidence_scope": entry.get("scope")})
    all_checks = [check for item in report["items"] for check in item["checks"]]
    unchecked = [item for item in report["items"] if any(task["status"] == "unchecked" for task in item["checks"])]
    from brain_binding import applicable_setup
    applicable = applicable_setup(report, project_selected=False)
    report.update({"checked": sum(task["status"] != "unchecked" for task in all_checks), "total": len(all_checks),
                   "unchecked_mechanisms": [row["id"] for row in unchecked], "complete": not unchecked,
                   "automatic_delivery_verified": receiver,
                   "receiver_reason": receiver_result.get("reason"),
                   "admission": applicable,
                   "warning": "Main admission: " + str(len(applicable["open_required_checks"]))
                   + " applicable checks open. " + ("Automatic root/worker delivery verified." if receiver
                                                    else "Automatic root/worker delivery unverified.")})
    if deployment and persist:
        c.atomic(c.state_dir(profile) / "setup-checklist.json", report)
    return report
