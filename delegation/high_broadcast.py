#!/usr/bin/env python3
"""Broadcast the High method to every agent on the mesh.

Run once by the /high command (owner-triggered), and hourly by the windowless scheduled
task HighMethodBroadcast. Posts a short, safe reminder to every agent's mailbox so the whole
fleet keeps deploying delegation the verified way. Plaintext mesh - no secrets, just the method.
"""
from __future__ import annotations
import os, subprocess, sys
from datetime import datetime

MESH = r"{{USERPROFILE}}\brain-repo\12_cognitive_architecture\agent_mesh\agent_mesh.py"
HERE = os.path.dirname(os.path.abspath(__file__))
SENDER = "brain-high"
# Kept tight on purpose (hourly x many agents): a pointer + the one rule that matters, not a wall
# of text. The full method lives in DELEGATION.md and the /high command.
BODY = (
    "THE HIGH METHOD: delegate expensive work to lean, disposable Fable/GPT-5.6 workers, then as the "
    "parent VERIFY the served model, CHECK the output, and implement/fix. The one rule: a worker's "
    "self-report of its model LIES about downgrades - ground-truth it from the transcript message.model "
    "(delegation/record_worker.py or delegate_codex.py); a capped Fable worker says 'claude-fable-5' "
    "while served Opus. Full method + deploy steps: run /high, or read "
    "12_cognitive_architecture/delegation/DELEGATION.md. Reflex: verify_worker_model."
)

def broadcast():
    # register the sender (idempotent) then post to every agent
    subprocess.run(["python", MESH, "hello", "--agent", SENDER, "--runtime", "brain",
                    "--task", "High-method reminder broadcaster"],
                   capture_output=True, text=True, timeout=25)
    r = subprocess.run(["python", MESH, "send", "--agent", SENDER, "--to", "all",
                        "--topic", "high-method", "--kind", "message", "--body", BODY],
                       capture_output=True, text=True, timeout=25)
    with open(os.path.join(HERE, "high_broadcast.log"), "a", encoding="utf-8") as f:
        _out = " ".join((r.stdout or "").split())[:160]
        _err = " ".join((r.stderr or "").split())[:120]
        f.write(f"{datetime.now().isoformat()} rc={r.returncode} out={_out} err={_err}\n")
    return r

if __name__ == "__main__":
    import os
    import sys
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # Discovery is not execution: this script writes, so a help request must
        # exit before any work. See script_help_flag_can_execute_work.
        print(__doc__ or os.path.basename(__file__))
        raise SystemExit(0)
    r = broadcast()
    print("broadcast OK" if r.returncode == 0 else "broadcast FAILED")
    if r.stdout:
        print(r.stdout[:400])
    if r.returncode != 0 and r.stderr:
        print("ERR:", r.stderr[:400])
    sys.exit(r.returncode)
