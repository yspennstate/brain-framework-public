#!/usr/bin/env python3
"""claim_gate.py - run a claim-dependent command ONLY if the mesh claim succeeds,
gated on the claim tool's EXIT CODE - never on grepping its output.

Why this exists (2026-07-23): an agent piped `agent_mesh.py claim` through
`grep | head` inside a `&&` chain. The claim was DENIED (a live holder), but grep
matched the error line and exited 0, so the chain continued and the write to the
claimed resource proceeded anyway - a silent bypass of a live claim. The index
survived only because the writer had its own cross-process lock. The protocol line
"if it returns exit code 3, STOP" is a spec for machinery, not a reminder to be
careful; this is the machinery. Pipe-friendly inspection of claim output is fine -
but the DECISION must ride the exit code, and with this wrapper it always does.

Usage:
  python claim_gate.py --agent A --resource R --purpose P [--mode write|exclusive]
                       [--release] -- <command> [args...]

Behavior: acquires the claim; on ANY nonzero exit the claim tool's output is shown,
the command is NOT run, and the claim exit code propagates (3 = conflict: message
the holder, choose a disjoint scope, or wait). On success the command runs with its
own stdio; with --release the claim is released afterward (the command's exit code
still propagates). Stdlib only.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MESH = os.path.join(HERE, "agent_mesh.py")


def main(argv):
    args = argv[1:]
    if "--" not in args:
        print(__doc__.strip().split("Usage:")[1])
        return 2
    split = args.index("--")
    opts, cmd = args[:split], args[split + 1:]
    if not cmd:
        print("claim_gate: no command after --")
        return 2
    val = {"--agent": "", "--resource": "", "--purpose": "", "--mode": "write"}
    release = "--release" in opts
    i = 0
    while i < len(opts):
        if opts[i] in val and i + 1 < len(opts):
            val[opts[i]] = opts[i + 1]
            i += 2
        else:
            i += 1
    for req in ("--agent", "--resource", "--purpose"):
        if not val[req]:
            print("claim_gate: %s is required" % req)
            return 2

    claim = [sys.executable, MESH, "claim", "--agent", val["--agent"],
             "--resource", val["--resource"], "--mode", val["--mode"],
             "--purpose", val["--purpose"]]
    rc = subprocess.call(claim)
    if rc != 0:
        print("claim_gate: claim FAILED with exit %d - the gated command was NOT run. "
              "(3 = live conflict: message the holder, pick a disjoint scope, or wait.)" % rc,
              file=sys.stderr)
        return rc

    try:
        cmd_rc = subprocess.call(cmd)
    finally:
        if release:
            subprocess.call([sys.executable, MESH, "release", "--agent", val["--agent"],
                             "--resource", val["--resource"]])
    return cmd_rc


if __name__ == "__main__":
    sys.exit(main(sys.argv))
