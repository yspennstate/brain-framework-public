#!/usr/bin/env python3
"""Lifecycle adapter: inject the global-workspace stream into a waking session.

Same stdin/stdout contract as agent_mesh/mesh_hook.py, and fail-open the same
way: the shared present tense being unavailable must never block the owner's
work. Unlike the mesh hook this adapter is READ-ONLY - it renders the stream
and never writes workspace state. Posting is a deliberate act by the agent.

Output is ASCII-safe JSON (ensure_ascii=True) written to the raw byte stream:
on a cp1252 Windows console a Hebrew or emoji character in one post must not
become a UnicodeEncodeError that silently eats the whole wake.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _emit(event: str, context: str) -> None:
    payload = json.dumps({
        "hookSpecificOutput": {
            "hookEventName": event,
            "additionalContext": context,
        }
    }, ensure_ascii=True)
    try:
        sys.stdout.buffer.write(payload.encode("ascii"))
    except Exception:
        sys.stdout.write(payload)


def main() -> int:
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except Exception:
        return 0
    if not isinstance(data, dict):
        return 0
    event = str(data.get("hook_event_name") or "")
    if event not in ("SessionStart", "UserPromptSubmit", "SubagentStart"):
        return 0

    text = ""
    try:
        from workspace import connect_ro, render_stream, DEFAULT_DB

        db = Path(os.environ.get("GLOBAL_WORKSPACE_DB", "") or DEFAULT_DB)
        con = connect_ro(db)
        if con is not None:
            try:
                text = render_stream(con)
            finally:
                con.close()
    except Exception:
        text = ""  # fail-open: a broken workspace must not block the session

    try:
        prompt = str(data.get("prompt") or "")
        if event == "UserPromptSubmit" and prompt.lstrip().startswith("/goal"):
            # the nudge matters most on the very first goal, before any DB exists
            nudge = ("New goal detected: register it so siblings see it and duplicates "
                     "get flagged: python %s intent-add --agent <mesh-id> --origin owner "
                     "--goal \"...\"" % (Path(__file__).parent / "workspace.py"))
            text = (text + "\n" + nudge) if text else nudge
        if text:
            _emit(event, text)
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
