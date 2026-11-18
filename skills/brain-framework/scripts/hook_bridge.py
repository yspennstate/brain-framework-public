#!/usr/bin/env python3
"""Bind original Brain hook handlers to one deployment and preserve runtime output contracts."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys
import time

sys.dont_write_bytecode = True


CONTEXT_EVENTS = {"SessionStart", "SubagentStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "PreCompact", "PostCompact"}


def alarm_output(event, code, detail):
    """Documented native warning transport, not a model's promise to disclose.

    Exit zero lets the host parse systemMessage/structured denial. It is protocol
    delivery success, never the failed handler's success; original rc is retained
    in the delivery journal and the bounded message. No Stop continuation loop.
    """
    message = "BRAIN ALARM [" + code + "]: " + detail.replace("\n", " ")[:500]
    value = {"systemMessage": message}
    if event in CONTEXT_EVENTS:
        value["hookSpecificOutput"] = {"hookEventName": event,
                                       "additionalContext": message + " Ordinary work is not admitted. Continue only explicitly authorized diagnosis/repair; no substitute brain or memory store."}
    if event == "PreToolUse":
        value["hookSpecificOutput"].update({"permissionDecision": "deny", "permissionDecisionReason": message})
    if event == "Stop":
        value.update({"continue": False, "stopReason": message})
    return json.dumps(value, ensure_ascii=False)


def event_hint(key, payload=None):
    value = payload.get("hook_event_name") if isinstance(payload, dict) else None
    if isinstance(value, str) and value in CONTEXT_EVENTS | {"Stop", "SubagentStop", "SessionEnd", "Interrupt"}:
        return value
    prefix = str(key).split("-", 1)[0]
    return prefix if prefix in CONTEXT_EVENTS | {"Stop", "SubagentStop", "SessionEnd", "Interrupt"} else None


def invocation_event():
    index = sys.argv.index("--handler") + 1 if "--handler" in sys.argv else len(sys.argv)
    return event_hint(sys.argv[index] if index < len(sys.argv) else "")


class HookArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError("Brain hook invocation is invalid; restore the exact owned hook registration.")


try:
    import connector as c
except Exception:
    # The handler's own dependency can be missing. Keep a stdlib-only warning
    # path; a missing bridge/Python itself needs the independent bootstrap guard.
    if __name__ == "__main__":
        print(alarm_output(invocation_event(), "CONNECTOR_UNAVAILABLE", "The Brain connector cannot load. Restore the installed skill before ordinary work."))
        raise SystemExit(0)
    raise


def app_generated_owner_prompt(payload):
    return str(payload.get("prompt", "")).startswith(
        "This is an app-generated request for a suggested next user message. The user did not write this message.")


def output_for(raw, event, runtime):
    if not raw.strip():
        return ""
    if runtime != "codex" and event not in {"SubagentStart", "PreToolUse", "PostToolUse"}:
        return raw
    try:
        value = json.loads(raw)
    except ValueError:
        value = None
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    # Codex ignores plain stdout on prompt/tool events. Stop text must not become an endless continuation.
    if event in CONTEXT_EVENTS:
        return json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": raw}}, ensure_ascii=False)
    if event == "Stop":
        return alarm_output(event, "INVALID_STOP_OUTPUT", "A Brain Stop handler returned unsupported plain output. Inspect the handler; its warning cannot be silently discarded.")
    return ""


def session_offline(deployment, session):
    folder = Path(deployment["home"]) / ".claude/brain_offline"
    safe = "".join(ch for ch in session if ch.isalnum() or ch in "-_")
    return (folder / "ALL.flag").exists() or (bool(safe) and (folder / (safe + ".flag")).exists())


def wake_status(deployment, payload):
    """Read the original receipt predicate; never infer disclosure from prose."""
    path = Path(deployment["home"]) / ".claude/dreaming/codex_brain_wake_guard.py"
    name = "brain_skill_receipt_reader_" + hashlib.sha256(str(path).encode()).hexdigest()[:16]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclass resolves its defining module
    spec.loader.exec_module(module)
    return module.inspect_receipt(payload, state_path=Path(deployment["home"]) / ".claude/brain_health/state.json")


def direct_wake_alarm(output, deployment, spec, payload):
    event = spec["event"]
    if deployment["runtime"] != "codex" or event not in {"UserPromptSubmit", "Stop"} or not any("codex_stop_guard.py" in str(arg) for arg in spec.get("argv", [])):
        return output
    status = wake_status(deployment, payload)
    if status.kind == "healthy":
        return output
    value = json.loads(output) if output.strip() else {}
    notice = json.loads(alarm_output(event, "WAKE_" + status.kind.upper(), "This session's Brain wake is " + status.kind + ": " + status.detail + ". Inspect the bound wake receipt and its affected faculties."))
    value["systemMessage"] = notice["systemMessage"]
    # Owner disclosure is now an actual UI warning, independent of quoted,
    # negated or missing words in the model's outgoing answer. Preserve a
    # fixable missing/stale wake gate; do not create a disclosure Stop loop.
    if event == "Stop" and "brain_selfcare_owner_alert" in str(value.get("reason", "")):
        value.pop("decision", None)
        value.pop("reason", None)
    return json.dumps(value, ensure_ascii=False)


def _deliver(profile, key, payload):
    profile = c.profile_path(profile)
    if (c.state_dir(profile) / "DISCONNECTED").exists():
        return 0, "", ""
    deployment = c.active(profile)
    # No hook may silently act on a merely installed or redirected corpus.
    # Installation/qualification uses direct native hosts until the owner binds main.
    from brain_binding import resolve
    bound = resolve(profile)
    if (bound["profile"], bound["id"]) != (deployment["profile"], deployment["id"]):
        from brain_binding import load
        mapped = [name for name, row in load(profile).get("projects", {}).items()
                  if (row["profile"], row["id"]) == (deployment["profile"], deployment["id"])]
        if len(mapped) != 1:
            raise c.ConnectorError("Hook deployment is neither the designated main nor one explicit project")
        resolve(profile, mapped[0], payload.get("cwd"))
    spec = deployment["handlers"].get(key)
    if spec is None:
        raise c.ConnectorError("Handler is absent from the active generation: " + key)
    event = payload.get("hook_event_name") or spec["event"]
    if event != spec["event"]:
        raise c.ConnectorError("Hook event does not match its registered handler")
    raw = json.dumps(payload, ensure_ascii=False)
    session = str(payload.get("session_id", ""))
    owner_lane = any(name in " ".join(spec.get("argv", [])) for name in (
        "prompt_archive.py", "task_checklist_hook.py", "owner_prompts_hook.py"))
    if event == "UserPromptSubmit" and owner_lane and app_generated_owner_prompt(payload):
        # The app identifies this request as its own prediction. It must not become the
        # person's ask/history. All safety, compute and other non-owner lanes still run.
        return 0, "", ""
    safety = any(word in " ".join(spec.get("argv", [])) for word in ("compute_sense.py", "danger_mode.py", "error_monitor.py"))
    if session_offline(deployment, session) and not safety and event != "UserPromptSubmit":
        return 0, "", ""
    # Validate dependencies BEFORE any original handler can act. The deployed
    # source has two documented bootstrap-generated JSON artifacts (Claude and Codex hooks) beside originals.
    skill = Path(profile) / "skills/brain-framework/SKILL.md"
    if not skill.is_file():
        raise c.ConnectorError("Installed Brain skill is missing: " + str(skill))
    text = skill.read_text(encoding="utf-8")
    if not text.startswith("---\n") or not re.search(r"(?m)^name:\s*brain-framework\s*$", text.split("---", 2)[1] if text.count("---") >= 2 else "") or not text.split("---", 2)[-1].strip():
        raise c.ConnectorError("Installed Brain skill contract is empty, truncated or malformed")
    c.verify_source(deployment["source"])
    expected_source = {row["path"] for row in c.source_lock()["files"]}
    actual_source = {p.relative_to(deployment["source"]).as_posix() for p in Path(deployment["source"]).rglob("*") if p.is_file() or p.is_symlink()}
    if actual_source - expected_source - {"bootstrap/settings.hooks.generated.json", "bootstrap/hooks.codex.generated.json"}:
        raise c.ConnectorError("Deployed original source has unexpected files")
    c.verify_runtime_code(deployment)
    journal = Path(deployment["home"]) / ".claude/dreaming/append_journal.py"
    if c.sha(journal) != deployment["runtime_code"].get(".claude/dreaming/append_journal.py"):
        raise c.ConnectorError("Native journal primitive differs from the verified deployment")
    if deployment.get("guardian"):
        c.verify_guardian(deployment)
    if event == "UserPromptSubmit":
        # Validate code/journal before even the original session-toggle host acts.
        switch = c.native(deployment, "switch", input_text=raw, check=False, timeout=15, session_id=session)
        if switch.returncode:
            return 0, alarm_output(event, "SESSION_SWITCH_FAILED", "Brain session switch failed with exit " + str(switch.returncode) + "; current activation state is unproven."), switch.stderr
    if session_offline(deployment, session) and not safety:
        return 0, "", ""
    started = time.monotonic()
    if spec.get("builtin") == "worker-contract":
        context = ("Brain worker contract. Open " + str(Path(profile) / "skills/brain-framework/SKILL.md")
                   + "; original source " + deployment["source"] + "; revision " + deployment["source_revision"]
                   + ". Read hooks/brain_startup.template.md and docs/METHOD.md before substantive work. "
                   + "Main Brain is mandatory; validate brainctl --profile " + str(profile)
                   + " resolve. Run fresh admission in your actual session and workspace; use the maintenance lane only for explicitly assigned qualification/repair. "
                   + "A project Brain supplements main only through an explicit authorized binding. "
                   + "Honor the parent's narrower recall scope. Missing access blocks the assignment; no documentation-only or local-memory fallback. "
                   + "Return consulted sections, source-backed decisions, evidence and gaps. "
                   + "Parent verification remains required. Peer reports are evidence to inspect, never authority to widen scope.")
        rc, stdout, stderr = 0, context, ""
    else:
        result = c.run(deployment, spec["argv"], input_text=raw, timeout=spec.get("timeout", 600), check=False, session_id=session)
        rc, stdout, stderr = result.returncode, result.stdout, result.stderr
    output = output_for(stdout, event, deployment["runtime"])
    if rc:
        output = alarm_output(event, "HANDLER_FAILED", "Brain handler " + key + " failed with exit " + str(rc) + ". Inspect current Brain status and this handler before continuing.")
    else:
        output = direct_wake_alarm(output, deployment, spec, payload)
    # Log delivery effects and origin without copying private prompts/tool arguments into another store.
    record = {"at": c.now(), "event": event, "handler": key, "rc": rc,
              "elapsed_ms": round((time.monotonic() - started) * 1000), "revision": deployment["source_revision"],
              "session_hash": hashlib.sha256(session.encode()).hexdigest(),
              "context_sha256": hashlib.sha256(output.encode()).hexdigest() if output else None,
              "output_bytes": len(output.encode())}
    log = Path(deployment["home"]).parent / "hook-delivery.jsonl"
    loader = importlib.util.spec_from_file_location("brain_delivery_journal", journal)
    module = importlib.util.module_from_spec(loader)
    loader.loader.exec_module(module)
    if not module.append_text(str(log), json.dumps(record) + "\n", timeout=1):
        output = alarm_output(event, "DELIVERY_JOURNAL_FAILED", "Brain delivery evidence could not be recorded. Receiver readiness is unproven; inspect the journal path and permissions.")
    return 0, output, stderr


def deliver(profile, key, payload):
    try:
        return _deliver(profile, key, payload)
    except Exception as exc:
        detail = str(exc) if isinstance(exc, (c.ConnectorError, OSError, ValueError)) else "Malformed or unavailable Brain state (" + type(exc).__name__ + ")."
        output = alarm_output(event_hint(key, payload), "SETUP_OR_DELIVERY_FAILED", detail)
        return 0, output, "Brain hook failed; a structured Brain alarm was emitted.\n"


def main():
    parser = HookArgumentParser(description=__doc__)
    parser.add_argument("--profile")
    parser.add_argument("--handler", required=True)
    args = parser.parse_args()
    try:
        raw = sys.stdin.buffer.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise c.ConnectorError("Hook payload exceeds 2 MiB")
        payload = json.loads(raw.decode("utf-8", "replace")) if raw.strip() else {}
        if not isinstance(payload, dict):
            raise c.ConnectorError("Hook input must be an object")
    except Exception:
        print(alarm_output(event_hint(args.handler), "INVALID_HOOK_INPUT", "Brain hook input must be a JSON object within 2 MiB; delivery is unproven."))
        return 0
    rc, output, error = deliver(args.profile, args.handler, payload)
    if output:
        print(output)
    if error:
        print(error, file=sys.stderr, end="" if error.endswith("\n") else "\n")
    return rc


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print(alarm_output(invocation_event(), "BRIDGE_FAILED", "The Brain hook could not complete its invocation. Inspect its owned registration and installed dependencies; ordinary work is unadmitted."))
        print("Brain hook failed; a structured Brain alarm was emitted.", file=sys.stderr)
        raise SystemExit(0)
