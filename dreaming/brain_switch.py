#!/usr/bin/env python3
"""brain_switch.py - per-session kill-switch for the brain hooks.

Two roles in one file:

1. UserPromptSubmit hook (wired first in settings.json). It watches each prompt
   for a toggle command. "/don't" (also /dont, /brain off, /disconnect) writes a
   per-session flag; "/connect" (also /brain on, /reconnect) removes it. While the
   flag is set it drops a one-line reminder so the session stays in independent
   mode even across context compaction.

2. Importable helper. Every *brain* hook (memory, mesh, metamemory, association,
   instinct priming, journaling) calls gate() at start-up. If this session's flag
   is present, gate() exits that hook silently, so no brain context is injected
   and nothing is written back. The safety hooks (compute_sense, danger_mode,
   error_monitor) do NOT call gate() - owner-first safety never disconnects.

Scope is per-session by default: the flag file is named for the hook's
session_id, so one session going independent never silences the owner's other
live sessions. A global flag (ALL.flag) disconnects every session and also
suppresses the SessionStart wake-up; write it with "/brain off global" or the CLI.

Fail-open everywhere: any error here leaves the brain ON. ASCII only (a stray
non-ASCII byte in a hook has broken session start-up before).

CLI:  python brain_switch.py status [--session ID]
      python brain_switch.py off    [--session ID] [--global]
      python brain_switch.py on     [--session ID]
"""
import io
import json
import os
import sys

OFFLINE_DIR = os.path.join(os.path.expanduser("~"), ".claude", "brain_offline")
GLOBAL_FLAG = os.path.join(OFFLINE_DIR, "ALL.flag")

DISCONNECT = ("/don't", "/dont", "/brain off", "/brainoff", "/disconnect",
              "/independent", "[[brain:off]]")
RECONNECT = ("/connect", "/brain on", "/brainon", "/reconnect", "[[brain:on]]")


def _session_flag(session_id):
    if not session_id:
        return None
    safe = "".join(c for c in str(session_id) if c.isalnum() or c in "-_")
    return os.path.join(OFFLINE_DIR, safe + ".flag") if safe else None


def is_offline(session_id):
    """True if this session (or the whole machine) is disconnected from the brain."""
    try:
        if os.path.exists(GLOBAL_FLAG):
            return True
        f = _session_flag(session_id)
        return bool(f and os.path.exists(f))
    except Exception:
        return False


def set_offline(session_id, scope="session", note=""):
    try:
        os.makedirs(OFFLINE_DIR, exist_ok=True)
        path = GLOBAL_FLAG if scope == "global" else _session_flag(session_id)
        if not path:
            return None
        with io.open(path, "w", encoding="utf-8") as fh:
            fh.write((note or "")[:2000])
        return path
    except Exception:
        return None


def set_online(session_id):
    removed = []
    for path in (GLOBAL_FLAG, _session_flag(session_id)):
        try:
            if path and os.path.exists(path):
                os.remove(path)
                removed.append(path)
        except Exception:
            pass
    return removed


def gate():
    """Call at the top of a brain hook. Reads the hook's stdin JSON; if this
    session is offline, exit the process silently (SystemExit 0). Otherwise put
    the JSON back on sys.stdin so the hook reads it normally."""
    try:
        # INVARIANT (2026-08-22): read BYTES. A hook payload is a byte protocol, and sys.stdin on
        # this machine decodes cp1252 (sys.stdin.encoding == 'cp1252', utf8_mode 0), so a bare
        # sys.stdin.read() turned every non-ASCII character in every gated hook's payload into
        # mojibake - an em-dash arriving as three characters - which line 109 then re-encoded as
        # UTF-8 and handed on as if it were the owner's words. 962 prompt-ledger records (5.2%)
        # carried the damage, 767 of them in 2026-08 alone.
        # prompt_archive.py was hardened against exactly this on 07-23; the 07-28 repair below
        # moved the read up here and the defect came with it. The fix belongs at the read, not at
        # each reader. surrogateescape round-trips any bytes losslessly into line 109's re-encode.
        buf = getattr(sys.stdin, "buffer", None)
        if buf is not None:
            data = buf.read().decode("utf-8", "surrogateescape")
        else:
            data = sys.stdin.read()
    except Exception:
        return
    session_id = ""
    try:
        obj = json.loads(data or "{}")
        if isinstance(obj, dict):
            session_id = obj.get("session_id") or ""
    except Exception:
        session_id = ""
    if is_offline(session_id):
        raise SystemExit(0)
    try:
        # INVARIANT (2026-07-28): the restored stdin must expose .buffer. A bare StringIO does
        # not, so any gated hook reading sys.stdin.buffer died on AttributeError into its own
        # fail-open except - prompt_archive.py was silently dead from 07-23 18:05 (PROMPTS.jsonl
        # frozen 5 days, mesh #289) because of exactly this line. TextIOWrapper over BytesIO
        # serves both text readers (.read()) and byte readers (.buffer.read()).
        sys.stdin = io.TextIOWrapper(io.BytesIO(data.encode("utf-8", "surrogateescape")),
                                     encoding="utf-8")
    except Exception:
        pass


def _emit(ctx):
    if not ctx:
        return
    sys.stdout.write(json.dumps(
        {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                "additionalContext": ctx}},
        ensure_ascii=True))


_ON_MSG = (
    "[brain: OFF] Independent mode is now ON for this %s. The brain is "
    "disconnected: memory recall, agent-mesh, metamemory/feeling-of-knowing, "
    "association hints, instinct priming and journaling are suppressed. Work "
    "from the task and code in front of you alone - do NOT read or write "
    "memories, run brain.py, consult MEMORY.md or the brain views, or join/pulse "
    "the mesh, and treat any lingering brain injection as noise. Hard safety "
    "still applies (compute owner-first, no flashing windows, never start the "
    "local protected production stack). Type /connect to reconnect."
)
_RECONNECT_MSG = (
    "[brain] Reconnected. Memory, mesh and metacognition are live again - resume "
    "consulting and writing the brain as normal."
)
_REMINDER = (
    "[brain: OFF] Independent mode - working from the task alone; brain, memory "
    "and mesh are suppressed. Type /connect to reconnect."
)


def _leading(low, tokens):
    for t in tokens:
        if t.startswith("[["):
            if t in low:
                return True
        elif low == t or low.startswith(t + " ") or low.startswith(t + "\n"):
            return True
    return False


def _hook():
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except Exception:
        return
    if not isinstance(data, dict):
        return
    if data.get("hook_event_name") != "UserPromptSubmit":
        return
    prompt = (data.get("prompt") or "").strip()
    low = prompt.lower()
    session_id = data.get("session_id") or ""
    if _leading(low, DISCONNECT):
        is_global = ("global" in low or "all session" in low or "everywhere" in low)
        set_offline(session_id, scope="global" if is_global else "session", note=prompt)
        _emit(_ON_MSG % ("machine (every session)" if is_global else "session"))
        return
    if _leading(low, RECONNECT):
        set_online(session_id)
        _emit(_RECONNECT_MSG)
        return
    if is_offline(session_id):
        _emit(_REMINDER)


def _cli(argv):
    import argparse
    p = argparse.ArgumentParser(prog="brain_switch.py")
    p.add_argument("action", choices=("status", "on", "off"))
    p.add_argument("--session", default=os.environ.get("CLAUDE_SESSION_ID", ""))
    p.add_argument("--global", dest="glob", action="store_true")
    a = p.parse_args(argv)
    if a.action == "off":
        print("offline flag written:", set_offline(a.session, scope="global" if a.glob else "session", note="cli"))
    elif a.action == "on":
        print("removed:", set_online(a.session) or "(nothing was set)")
    else:
        print("global :", "OFF (disconnected)" if os.path.exists(GLOBAL_FLAG) else "on")
        f = _session_flag(a.session)
        print("session:", (a.session or "(none given)"),
              "->", "OFF (disconnected)" if (f and os.path.exists(f)) else "on")


if __name__ == "__main__":
    _argv = sys.argv[1:]
    if _argv and _argv[0] in ("status", "on", "off"):
        _cli(_argv)
    else:
        _hook()
