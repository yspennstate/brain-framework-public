"""Explicit owner-designated main/project bindings and fresh activation admission.

This is a connector overlay. It neither creates a corpus nor substitutes for the
upstream native readers/writers. Unknown identity and unmeasured setup fail closed.
"""
from __future__ import annotations

import hashlib
import json
import datetime as dt
import os
from pathlib import Path
import re

import connector as c


SCHEMA = 1
WAKE_NODES = ("identity_who_you_are", "relationship_our_work_together",
              "method_write_like_a_human", "shared_language",
              "method_think_creatively", "values_what_we_stand_for")


def registry_path(profile):
    return c.state_dir(c.profile_path(profile)) / "brain-bindings.json"


def load(profile):
    if (c.state_dir(c.profile_path(profile)) / "DISCONNECTED").exists():
        raise c.ConnectorError("This profile is disconnected")
    path = registry_path(profile)
    if not path.is_file() or path.is_symlink():
        raise c.ConnectorError("No explicit main-brain binding. Obtain the owner's designation, then use bind-main. No substitute corpus is permitted.")
    data = c.read_json(path)
    if not isinstance(data, dict) or data.get("schema") != SCHEMA or data.get("profile") != str(c.profile_path(profile)):
        raise c.ConnectorError("Brain binding schema/profile mismatch")
    if not isinstance(data.get("main"), dict) or not isinstance(data.get("projects", {}), dict):
        raise c.ConnectorError("Malformed main/project binding registry")
    return data


def descriptor(profile, role, authorization, roots=()):
    if not authorization.strip():
        raise c.ConnectorError("Binding requires the actual owner authorization evidence pointer")
    profile = c.profile_path(profile)
    d = c.active(profile)
    if (c.state_dir(profile) / "DISCONNECTED").exists():
        raise c.ConnectorError("Cannot bind a disconnected deployment")
    c.runtime_env(d)  # validates canonical HOME/slug; never selects an arbitrary BRAIN_MEM_DIR
    if not d.get("slug"):
        raise c.ConnectorError("Deployment has no canonical corpus slug; repair before binding")
    fields = {k: d[k] for k in ("id", "home", "memory", "source", "source_revision", "slug", "runtime", "python")}
    fields.update({"profile": str(profile), "role": role, "authorization": authorization,
                   "bound_at": c.now(), "resolved_home": str(Path(d["home"]).resolve(strict=True)),
                   "resolved_memory": str(Path(d["memory"]).resolve(strict=True)),
                   "workspace_roots": [str(c.absolute(p).resolve(strict=True)) for p in roots]})
    if not (Path(d["memory"]) / "MEMORY.md").is_file():
        raise c.ConnectorError("Designated corpus has no MEMORY.md")
    if d["source_revision"] != c.source_lock()["revision"]:
        raise c.ConnectorError("Designated deployment uses a different source revision; review the migration")
    return fields


def bind_main(profile, authorization, apply=False, target_profile=None):
    profile = c.profile_path(profile)
    main = descriptor(target_profile or profile, "main", authorization)
    path = registry_path(profile)
    if path.exists():
        existing = load(profile)
        # Re-designation is explicit; preserve separately authorized project mappings.
        projects = existing.get("projects", {})
    else:
        projects = {}
    result = {"schema": SCHEMA, "profile": str(profile), "main": main, "projects": projects}
    if apply:
        c.atomic(path, result)
    return {"applied": apply, "binding": str(path), "registry": result,
            "note": "Owner designation establishes identity, not readiness. Run admit for the current session."}


def bind_project(profile, name, target_profile, roots, authorization, apply=False):
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", name) or name == "main":
        raise c.ConnectorError("Project id must be a distinct lowercase identifier")
    data = load(profile)
    resolve(profile)  # every project requires a valid main
    if not roots:
        raise c.ConnectorError("A project binding requires explicit workspace roots")
    project = descriptor(target_profile, "project", authorization, roots)
    if project["resolved_memory"] == data["main"]["resolved_memory"]:
        raise c.ConnectorError("A project brain must have a separate corpus; do not relabel the main")
    for key, other in data.get("projects", {}).items():
        if key != name and other["resolved_memory"] == project["resolved_memory"]:
            raise c.ConnectorError("Project corpus already has a different binding")
    data.setdefault("projects", {})[name] = project
    if apply:
        c.atomic(registry_path(profile), data)
    return {"applied": apply, "project": name, "descriptor": project}


def validate(entry):
    profile = c.profile_path(entry["profile"])
    d = c.active(profile)
    if (c.state_dir(profile) / "DISCONNECTED").exists():
        raise c.ConnectorError("Designated Brain is disconnected")
    for key in ("id", "home", "memory", "source", "source_revision", "slug", "runtime", "python"):
        if d.get(key) != entry.get(key):
            raise c.ConnectorError("Designated Brain changed at " + key + "; inspect migration and explicitly rebind")
    if d["source_revision"] != c.source_lock()["revision"]:
        raise c.ConnectorError("Bound Brain source revision differs from this skill")
    if (str(Path(d["home"]).resolve(strict=True)) != entry["resolved_home"]
            or str(Path(d["memory"]).resolve(strict=True)) != entry["resolved_memory"]):
        raise c.ConnectorError("Bound Brain path resolves to a different location")
    c.runtime_env(d)
    if not (Path(d["memory"]) / "MEMORY.md").is_file():
        raise c.ConnectorError("Bound Brain corpus/index is inaccessible")
    return d


def resolve(profile, brain="main", workspace=None):
    data = load(profile)
    main = validate(data["main"])
    if brain == "main":
        return main
    entry = data.get("projects", {}).get(brain)
    if entry is None:
        raise c.ConnectorError("No authorized project-brain binding: " + brain)
    if not workspace:
        raise c.ConnectorError("Project access requires the actual --workspace")
    current = c.absolute(workspace).resolve(strict=True)
    if not any(current.is_relative_to(Path(root)) for root in entry["workspace_roots"]):
        raise c.ConnectorError("Workspace is outside this project's authorized scope")
    project = validate(entry)
    if Path(project["memory"]).resolve() == Path(main["memory"]).resolve():
        raise c.ConnectorError("Project and main corpus alias one another")
    return project


def selection(profile, brain="main", workspace=None):
    """No implicit project selection: disclose applicable mappings to the operator."""
    data = load(profile)
    selected = resolve(profile, brain, workspace)
    current = c.absolute(workspace).resolve(strict=True) if workspace else None
    applicable = [key for key, row in data.get("projects", {}).items()
                  if current and any(current.is_relative_to(Path(p)) for p in row["workspace_roots"])]
    if brain == "main" and applicable:
        raise c.ConnectorError("Workspace has mapped project brains; select one explicitly with --brain: " + ", ".join(applicable))
    return {"main": data["main"], "selected": brain, "project": data.get("projects", {}).get(brain),
            "deployment": selected["id"], "applicable_projects": applicable}


def activation_path(profile, session, workspace, brain="main"):
    key = json.dumps([session, str(c.absolute(workspace).resolve()), brain], ensure_ascii=False).encode()
    return c.state_dir(profile) / "activations" / (hashlib.sha256(key).hexdigest() + ".json")


def admission_fingerprint(deployment):
    # Keep the manual evidence ledger out of its OWN deployment fingerprint:
    # checklist entries carry that fingerprint, so hashing the ledger there
    # would create an impossible self-referential certification.
    from receiver_probe import deployment_fingerprint
    evidence = c.state_dir(deployment["profile"]) / "setup-evidence.json"
    value = [deployment_fingerprint(deployment), c.sha(evidence) if evidence.is_file() else None]
    return hashlib.sha256(json.dumps(value).encode()).hexdigest()


def receiver_for(profile, targets):
    """An external healthy brain does not qualify an empty/different receiver."""
    receiver = c.active(c.profile_path(profile))
    if not any((receiver["profile"], receiver["id"]) == (d["profile"], d["id"]) for _, d in targets):
        raise c.ConnectorError("Invoking receiver is not the designated main or selected project host; install/connect and qualify this actual receiver")
    return receiver


def native_session_context(receiver, session):
    """Match the invoking runtime signal, not a parent/caller-named receipt."""
    key = {"codex": "CODEX_THREAD_ID", "claude": "CLAUDE_CODE_SESSION_ID"}.get(receiver["runtime"])
    actual = os.environ.get(key, "").strip() if key else ""
    if actual and actual != session:
        raise c.ConnectorError("Current native session differs from --session; a parent or other context cannot authorize this operation")
    context = {"runtime": receiver["runtime"], "source": key, "session": actual or None, "verified": bool(actual)}
    if not actual:
        context["error"] = "Native session identity is unavailable; run inside the qualified runtime before ordinary admission"
    return context


def applicable_setup(checklist, project_selected):
    # Main is mandatory. Actual enabled configuration/core hooks select its
    # faculties; a selected project additionally requires specialist admission.
    # Keep every unselected engine visible without inventing its operation.
    items = checklist.get("items", [])
    selected = checklist.get("selected_capabilities")
    if selected is not None:
        if any(not row.get("capability") for row in items):
            raise c.ConnectorError("Setup inventory lacks source-backed capability selection")
        required_capabilities = set(selected) | {"core"}
        if project_selected:
            required_capabilities.add("specialists")
        excluded = [row["id"] for row in items if row.get("capability") not in required_capabilities]
    else:
        # Older/isolated inventories have no selection proof: stay conservative.
        excluded = [] if project_selected else ["S23"]
    qualification = [{"id": row["id"], "requirement": check["requirement"], "status": check["status"]}
                     for row in items for check in row.get("checks", []) if check.get("scope") == "qualification"]
    open_checks = [{"id": row["id"], "requirement": check["requirement"]}
                   for row in items if row["id"] not in excluded
                   for check in row.get("checks", []) if check.get("scope", "admission") != "qualification"
                   and check.get("status") not in {"checked-current-observation", "checked-evidence"}]
    complete = bool(items) and not open_checks
    return {"complete": complete, "open_required_checks": open_checks,
            "qualification_only": qualification,
            "selection_basis": selected,
            "not_required_for_task": [{"id": key, "reason": "Faculty is not selected by explicit deployment configuration, actual registered core hooks, or this task's project selection. Inventory remains unverified, not passed."} for key in excluded]}


def admit(profile, session, workspace, brain="main"):
    """Recompute every check; no last-ready receipt is an admission shortcut."""
    if not session.strip() or not workspace:
        raise c.ConnectorError("Admission requires the actual session id and workspace")
    chosen = selection(profile, brain, workspace)  # fail before native effects if identity is unknown
    targets = [("main", resolve(profile))]
    if brain != "main":
        targets.append((brain, resolve(profile, brain, workspace)))
    receiver = receiver_for(profile, targets)
    context = native_session_context(receiver, session)
    # Integrity must pass BEFORE running any of the installed hosts. The general
    # diagnostic verifier collects multiple errors; admission cannot execute a
    # tainted host just to collect another diagnostic.
    for _, d in targets:
        c.verify_source(c.state_dir(d["profile"]) / "sources" / d["source_revision"], strict=True)
        c.verify_runtime_code(d)
    reports = []
    from setup_checklist import inspect
    for name, d in targets:
        upkeep = {"state": "native-identity-unverified", "attempted": False}
        if context["verified"]:
            from recall_upkeep import ensure_current
            try:
                upkeep = ensure_current(d, session)
            except (c.ConnectorError, OSError, ValueError, KeyError) as exc:
                # Replace any earlier successful receipt with the fresh verdict.
                upkeep = {"state": "unverified", "error": str(exc)}
        verification = c.verify(d, integrated=True, hooks=True, smoke=True)
        checklist = inspect(c.profile_path(d["profile"]), observation=verification, persist=False)
        applicable = applicable_setup(checklist, project_selected=brain != "main")
        # Prove wake nodes are actually readable through the selected native reader.
        nodes = []
        for slug in WAKE_NODES if name == "main" else ():
            result = c.native(d, "brain", ["node", slug], check=False)
            ok = result.returncode == 0 and "BRAIN NODE: " + slug in result.stdout
            nodes.append({"slug": slug, "passed": ok,
                          "output_sha256": hashlib.sha256(result.stdout.encode()).hexdigest()})
        reports.append({"brain": name, "profile": d["profile"], "deployment": d["id"],
                        "fingerprint": admission_fingerprint(d), "fingerprint_kind": "admission",
                        "recall_upkeep": upkeep,
                        "verification": verification, "setup": checklist, "applicable_setup": applicable, "wake_nodes": nodes,
                        "passed": bool(context["verified"] and upkeep.get("state") in {"current", "repaired", "not-selected"}
                                       and verification.get("ready") and applicable["complete"]
                                       and all(n["passed"] for n in nodes))})
    report = {"schema": SCHEMA, "at": c.now(), "session": session, "workspace": str(c.absolute(workspace).resolve()),
              "revision": c.source_lock()["revision"], "selection": chosen, "native_context": context,
              "receiver": {"profile": str(c.profile_path(profile)), "deployment": receiver["id"],
                           "fingerprint": admission_fingerprint(receiver), "fingerprint_kind": "admission"},
              "passed": all(r["passed"] for r in reports), "brains": reports,
              "next": "Consult the returned wake nodes and task evidence, then work under the skill" if all(r["passed"] for r in reports)
              else "Repair/requalify the listed gates. Only diagnosis, authorized repair and this skill's maintenance may continue; no substitute memory writes."}
    # This is connector evidence in the designated main profile, never an alternate memory store.
    c.atomic(activation_path(profile, session, workspace, brain), report)
    return report


def require_admission(profile, session, workspace, brain="main"):
    """Guard native memory mutations against failed, stale or mismatched admission."""
    return _require_admission(profile, session, workspace, brain, hook_event=False)


def require_hook_admission(profile, payload, brain="main"):
    """Called only by the native observer with the host-delivered event object.

    Hook subprocesses can inherit a parent's environment. Their actual session
    comes from the native event, whereas CLI writers use their tool environment.
    Neither feed admits a receipt for another session, workspace or receiver.
    """
    session = (payload.get("session_id") or payload.get("thread_id")) if isinstance(payload, dict) else None
    if (not isinstance(payload, dict) or not isinstance(session, str)
            or not session.strip() or not isinstance(payload.get("cwd"), str)
            or not payload["cwd"].strip()):
        raise c.ConnectorError("Native hook admission requires the host event's actual session and workspace")
    return _require_admission(profile, session, payload["cwd"], brain, hook_event=True)


def _require_admission(profile, session, workspace, brain, *, hook_event):
    chosen = selection(profile, brain, workspace)
    path = activation_path(profile, session, workspace, brain)
    if not session or not workspace or not path.is_file() or path.is_symlink():
        raise c.ConnectorError("Memory mutation requires current --session, --workspace and successful admit; no fallback write")
    report = c.read_json(path)
    age = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(report["at"])).total_seconds()
    if (not report.get("passed") or report.get("session") != session or report.get("selection") != chosen
            or report.get("workspace") != str(c.absolute(workspace).resolve()) or not 0 <= age <= 1800):
        raise c.ConnectorError("Admission is failed, stale or belongs to another session/workspace/brain; rerun admit")
    expected = {"main": resolve(profile)}
    if brain != "main":
        expected[brain] = resolve(profile, brain, workspace)
    receiver = receiver_for(profile, list(expected.items()))
    context = ({"runtime": receiver["runtime"], "session": session, "verified": True}
               if hook_event else native_session_context(receiver, session))
    stored = report.get("native_context", {})
    source = {"codex": "CODEX_THREAD_ID", "claude": "CLAUDE_CODE_SESSION_ID"}.get(receiver["runtime"])
    if (context.get("verified") is not True or stored.get("verified") is not True
            or source is None or stored.get("source") != source
            or stored.get("runtime") != context["runtime"] or stored.get("session") != context["session"]):
        raise c.ConnectorError("Admission lacks the current trusted native context; rerun inside the qualified runtime, no fallback write")
    if report.get("receiver") != {"profile": str(c.profile_path(profile)), "deployment": receiver["id"],
                                  "fingerprint": admission_fingerprint(receiver), "fingerprint_kind": "admission"}:
        raise c.ConnectorError("Invoking receiver changed or lacks its own qualification")
    rows = {r["brain"]: r for r in report.get("brains", [])}
    if set(rows) != set(expected):
        raise c.ConnectorError("Admission lacks a required brain")
    for name, d in expected.items():
        if rows[name].get("fingerprint_kind") != "admission" or rows[name].get("fingerprint") != admission_fingerprint(d):
            raise c.ConnectorError("Brain code/configuration/binding changed since admission")
        c.verify_runtime_code(d)
    return report
