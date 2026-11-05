"""graph_membrane_hook.py - the brain graph's subconscious feeding the context, on prompts and tool calls (26 Sep 2026).

His voice note of 26 Sep 00:26 and his 15:5x order: the graph grown from everything agents touch runs live, with a weight
that is not zero, so it can be studied and made better. The work is in ~/.claude/brain_graph/bin/live_membrane.py; this
file only reads the hook payload and prints what reached the context. It never blocks and never fails a tool call.

Wired through hook_dispatch_list.json (UserPromptSubmit and PostToolUse), enabled while
~/.claude/brain_graph/live/HOOK_ENABLED exists. ~/.claude/brain_graph/live/STOP silences it at once.
"""
import json
import os
import sys


def main():
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:
        return
    sys.path.insert(0, os.path.join(os.path.expanduser("~"), ".claude", "brain_graph", "bin"))
    try:
        import live_membrane
        text = live_membrane.run(payload)
    except Exception:
        return
    if text:
        ev = payload.get("hook_event_name") or "UserPromptSubmit"
        sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": ev, "additionalContext": text}}))
        try:                                 # the receipt comes after the text is out (see live_membrane.mark_emitted)
            sys.stdout.flush()
            live_membrane.mark_emitted()
        except Exception:
            pass


main()
