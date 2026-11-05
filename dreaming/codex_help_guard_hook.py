#!/usr/bin/env python3
"""Fast, deny-only Codex PreToolUse entry point for local Python help discovery.

This hook intentionally does not initialize the brain, retrieval, telemetry, or
advisory stack.  It runs before the general reflex hook so a proven local
``script.py --help`` execution hazard is decided inside the host's two-second
budget even when the advisory path is slow.  Classification remains centralized
in ``codex_reflex_hook._help_guard``; this file is only the low-latency boundary.

Fail open on malformed input or any internal error.  A deny is printed only when
the shared classifier returns an explicit deny object.
"""
import json
import sys
import importlib.util
from pathlib import Path


def main() -> None:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        if payload.get("hook_event_name") != "PreToolUse":
            return
        # Owner's 2026-09-19 standing order. Keep the no-subagents decision in
        # this already-installed synchronous gate, before advisory brain work.
        policy = Path.home() / '.codex' / 'no_subagents_hook.py'
        spec = importlib.util.spec_from_file_location('codex_no_subagents', policy)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        blocked = module.decide(payload)
        if blocked:
            sys.stdout.write(json.dumps(blocked, ensure_ascii=False))
            return
        visual_policy = Path.home() / '.codex' / 'film_visual_policy_hook.py'
        visual_spec = importlib.util.spec_from_file_location('codex_film_visual_policy', visual_policy)
        visual_module = importlib.util.module_from_spec(visual_spec)
        visual_spec.loader.exec_module(visual_module)
        visual_block = visual_module.decide(payload)
        if visual_block:
            sys.stdout.write(json.dumps(visual_block, ensure_ascii=False))
            return
        import codex_reflex_hook
        decision = codex_reflex_hook._help_guard(payload)
        if decision:
            output = decision.get("hookSpecificOutput", {})
            reason = output.get("permissionDecisionReason", "")
            output["permissionDecisionReason"] = "[fast-help-guard] " + reason
            sys.stdout.write(json.dumps(decision, ensure_ascii=False))
    except Exception:
        return


if __name__ == "__main__":
    main()
