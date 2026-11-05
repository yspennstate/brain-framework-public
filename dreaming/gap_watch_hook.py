#!/usr/bin/env python3
"""gap_watch_hook.py - PreToolUse hook: two coverage-gap CAUTIONS the deployed gut misses entirely.

GAP A (stale_assert): a session asserts a definitive present-state claim ("X is dead / broken / missing /
never fires / writes unconditionally") about a checkable artifact INTO a durable or broadcast pipe (a mesh
send/handoff, a brain memory, a memory-dir write) with NO fresh observation of that artifact in the recent
tool window. Demonstrated three times on 2026-07-12 (I broadcast 'the error-graph hook is dead' from a
stale/reconstructed read; it was live) - and no reflex fired at the assert/broadcast moment.
GAP B (peer_broadcast_wrong): a mesh send/handoff carrying a claim the owner-guard (pre_report) would
BLOCK reaches PEERS from the command surface, unguarded. Reuses the exact ~0.04%-false-block machinery.

SURFACE-ONLY: emits additionalContext, NEVER a deny/block - it cannot cause a healer-is-the-outage. Gated
by graph_fire/GAP_WATCH_LIVE (absent -> silent no-op; delete that one file to revert instantly, no settings
edit). Fail-safe by construction: any error -> exit 0, no output (this is the every-tool-call path).
Verified 2026-07-12 (smoke 9/9, fires-on-real / quiet-on-observed, fail-safe held).
"""
import sys, os, io, json

HERE = os.path.dirname(os.path.abspath(__file__))
GF = os.path.join(HERE, "graph_fire")
LIVE = os.path.join(GF, "GAP_WATCH_LIVE")


def main():
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except Exception:
        return
    try:
        if not os.path.exists(LIVE):
            return  # not promoted / reverted -> silent
        sys.path.insert(0, GF)
        import gap_watch
        fires = gap_watch.watch(data) or []
        if not fires:
            return
        lines = ["[unconscious] coverage-gap caution - a place the gut usually can't watch:"]
        for f in fires[:2]:
            g = str(f.get("gut", "")).replace("\r", " ").replace("\n", " ")
            if g:
                lines.append("  - " + g[:640])
        ctx = "\n".join(lines)
        if len(lines) < 2:
            return
        out = {"hookSpecificOutput": {"hookEventName": "PreToolUse",   # a note, not an approval (29 Sep)
                                      "additionalContext": ctx}}
        sys.stdout.write(json.dumps(out))
    except Exception:
        return


if __name__ == "__main__":
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import brain_switch as _bsw
        _bsw.gate()
    except SystemExit:
        raise
    except Exception:
        pass
    main()
