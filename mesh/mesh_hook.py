#!/usr/bin/env python3
"""Lifecycle adapter that wakes Claude/Codex sessions into the agent mesh.

Both runtimes send a JSON object on stdin.  The common hook schema includes
session_id, cwd, hook_event_name, and (for turn hooks) prompt.  This adapter is
fail-open: coordination being unavailable must never prevent the owner's work.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from agent_mesh import Mesh, bounded, duration_seconds


def session_agent(runtime: str, data: dict) -> str:
    explicit = os.environ.get("AGENT_MESH_ID", "").strip()
    if explicit:
        return explicit
    sid = str(data.get("session_id") or data.get("thread_id") or data.get("conversation_id") or "").strip()
    if sid:
        return f"{runtime}:{sid[:16]}"
    return f"{runtime}:{os.getppid()}"


def emit(event: str, context: str) -> None:
    if not context:
        return
    # ASCII JSON survives Windows cp1252 pipes while Claude reconstructs the
    # original Unicode from escapes.  A peer's Hebrew/emoji must not disable
    # coordination for the whole session.
    sys.stdout.write(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": event,
            "additionalContext": context,
        }
    }, ensure_ascii=True))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", required=True, choices=("claude", "codex"))
    parser.add_argument("--db", default=os.environ.get("AGENT_MESH_DB", ""))
    args = parser.parse_args()
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except Exception:
        return 0
    if not isinstance(data, dict):
        return 0
    event = str(data.get("hook_event_name") or "")
    if not event:
        return 0
    db = Path(args.db) if args.db else Path.home() / ".agents" / "coordination" / "agent_mesh.sqlite3"
    mesh = None
    try:
        # Prompt hooks must never feel like a frozen terminal.  CLI workers keep
        # the longer default for contention-heavy writes; a hook degrades visibly
        # after 750 ms and tells the agent to stay read-only.
        mesh = Mesh(db, busy_timeout_ms=750)
        agent = session_agent(args.runtime, data)
        cwd = str(data.get("cwd") or os.getcwd())
        session_key = os.environ.get("AGENT_MESH_SESSION", "").strip() or str(data.get("session_id") or "")
        if event == "SessionStart":
            mesh.hello(agent, args.runtime, "session started; task not published yet", cwd,
                       duration_seconds("45m"), "active", session=session_key)
            emit(event, mesh.digest(agent, mark_delivered=True))
        elif event == "UserPromptSubmit":
            # Never copy the raw user prompt into the plaintext coordination DB:
            # it can contain private data or credentials.  Agents publish a safe
            # task summary explicitly when coordination detail is useful.
            # hello is idempotent and covers a hook installed mid-session.
            mesh.hello(agent, args.runtime, "interactive session active; task summary not auto-copied",
                       cwd, duration_seconds("45m"), "working",
                       session=session_key)
            emit(event, mesh.digest(agent, mark_delivered=True))
        elif event == "Stop":
            try:
                mesh.pulse(agent, status="waiting")
            except Exception:
                pass
        elif event == "SubagentStart":
            child_key = str(data.get("agent_id") or data.get("agent_type") or "subagent")
            child = f"{args.runtime}:sub:{child_key[:40]}"
            parent = session_agent(args.runtime, data)
            mesh.hello(child, args.runtime, f"subagent {data.get('agent_type') or ''}".strip(), cwd,
                       duration_seconds("45m"), "working", parent=parent)
            emit(event, mesh.digest(child, mark_delivered=True))
        elif event == "SubagentStop":
            child_key = str(data.get("agent_id") or data.get("agent_type") or "subagent")
            child = f"{args.runtime}:sub:{child_key[:40]}"
            try:
                mesh.bye(child, "done", "subagent stopped; result not auto-copied", release=True)
            except Exception:
                pass
        return 0
    except Exception as exc:
        if event in ("SessionStart", "UserPromptSubmit", "SubagentStart"):
            emit(event, "[agent-mesh DEGRADED] Coordination is unavailable (%s). Read-only work may continue, "
                 "but do not mutate shared files, git, memories, deployments, or external state until a manual "
                 "hello/digest/claim succeeds." % exc.__class__.__name__)
        return 0
    finally:
        if mesh is not None:
            try:
                mesh.close()
            except Exception:
                pass


if __name__ == "__main__":
    try:
        import os as _o, sys as _s
        _s.path.insert(0, _o.path.join(_o.path.expanduser("~"), ".claude", "dreaming"))
        import brain_switch as _bsw
        _bsw.gate()
    except SystemExit:
        raise
    except Exception:
        pass
    raise SystemExit(main())
