#!/usr/bin/env python3
"""summary_hook.py - the Stop hook the owner asked for on 2026-09-07 13:0x.

The design: the waste hook's process census stays with the agent; at a stop the agent instead summarizes
what it was told to do and what it has done so far.

On Stop it asks the model for one short summary: what it was told to do in this session (the standing orders, in
the owner's words where possible) and what it has done so far, with what is still open. It fires ONCE per stop:
the hook input carries stop_hook_active=true when the model is already continuing because of a Stop hook, and
then this script exits silently - that guard is what the waste hook lacked, which is why it re-fired after every
reply. Fail-safe: any error -> silent exit 0.

Register (exec form, like the other dreaming hooks):
    Stop -> {"type": "command", "command": "python", "args": ["-s", "~\\.claude\\dreaming\\summary_hook.py"], "timeout": 20}

    python summary_hook.py --selftest
"""
import json
import sys


def main():
    if "--selftest" in sys.argv:
        for inp, expect in (({"stop_hook_active": True}, ""), ({"stop_hook_active": False}, "decision")):
            out = decide(inp)
            assert (expect in out) if expect else (out == ""), (inp, out)
        print("selftest ok: silent when stop_hook_active, one summary request otherwise")
        return 0
    try:
        raw = sys.stdin.read()
        inp = json.loads(raw) if raw.strip() else {}
    except Exception:  # noqa: BLE001
        inp = {}
    out = decide(inp)
    if out:
        sys.stdout.write(out)
    return 0


def decide(inp):
    if inp.get("stop_hook_active"):
        return ""
    reason = ("Summary hook (owner order 2026-09-07): before you stop, give a summary in at most eight short lines, "
              "in two parts. WHAT I WAS TOLD TO DO: the orders you received in this session, in the owner's own words "
              "where you can, most recent last. WHAT I HAVE DONE SO FAR: what is finished and verified, what is running, "
              "what is still open or blocked and on whom. No process lists, no tool talk. Then stop.")
    return json.dumps({"decision": "block", "reason": reason})


if __name__ == "__main__":
    raise SystemExit(main())
