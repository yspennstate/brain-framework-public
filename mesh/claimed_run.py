#!/usr/bin/env python3
"""Run a command ONLY if a mesh claim is actually held. Release it afterwards.

WHY THIS EXISTS (2026-08-07): twice in one session I wrote

    python agent_mesh.py claim --resource R ... ; python -c "<mutate shared state>"

and the second command ran even though the claim had FAILED - once on exit code 3,
once because the agent's lease had silently expired ("agent is not live"). Shell
chaining does not gate anything: the claim's failure is invisible to the next command.
Owning the slip twice did not prevent the third, so this is the machinery instead of
the promise. The protocol rule is claim-then-mutate; this makes not-claiming impossible.

USAGE
    python claimed_run.py --agent A --resource R --purpose P -- <command> [args...]

EXIT CODES
    0   claim held, command ran, command exited 0
    3   CLAIM REFUSED or agent not live - COMMAND NOT RUN (this is the point)
    n   command's own non-zero exit (claim was held; still released)
"""
import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MESH = os.path.join(HERE, "agent_mesh.py")


def _mesh(args):
    """Run an agent_mesh subcommand; return (returncode, stdout+stderr)."""
    p = subprocess.run([sys.executable, MESH] + args,
                       capture_output=True, text=True)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def claim_is_live(out, rc, resource):
    """A claim counts as HELD only on positive evidence, never on absence of error.

    The 'not live' failure printed a human sentence and still exited 0, so an exit
    code alone is not proof.

    BUG FIXED 2026-08-07, found in live use: the first version required the literal
    resource string to appear in the response. The mesh NORMALISES resources - it
    stores `file:C:\\path` as bare `C:\\path` - so every file: claim produced a FALSE
    REFUSAL on a claim that had actually succeeded. Worse, the refusal path returned
    before the release, so each false refusal LEAKED a live claim and deadlocked the
    resource against other agents. Positive evidence is now structural: a non-empty
    `acquired` list in the parsed JSON, which cannot be faked by a conflict response.
    """
    bad = ("not live", "would resolve against", "exit code 3",
           "refused", "held by", "conflict")
    low = out.lower()
    for b in bad:
        if b in low:
            return False, "mesh said: %s" % b
    if rc != 0:
        return False, "claim exited %d" % rc
    # positive evidence: a non-empty "acquired" list in the response
    try:
        start = out.index("{")
        data = json.loads(out[start:])
        if data.get("acquired"):
            return True, "held"
        return False, "claim response has no acquired entry"
    except Exception:
        pass
    # fallback for a non-JSON response: compare on a normalised resource
    norm = resource.split(":", 1)[-1].lower().replace("\\", "/")
    if norm and norm in low.replace("\\\\", "/").replace("\\", "/"):
        return True, "held (normalised match)"
    return False, "no positive evidence of a held claim"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", required=True)
    ap.add_argument("--resource", required=True)
    ap.add_argument("--purpose", required=True)
    ap.add_argument("--mode", default="write")
    ap.add_argument("cmd", nargs=argparse.REMAINDER,
                    help="command to run, after a bare --")
    a = ap.parse_args()

    cmd = a.cmd[1:] if a.cmd and a.cmd[0] == "--" else a.cmd
    if not cmd:
        print("claimed_run: no command given after --", file=sys.stderr)
        return 2

    rc, out = _mesh(["claim", "--agent", a.agent, "--resource", a.resource,
                     "--purpose", a.purpose, "--mode", a.mode])
    ok, why = claim_is_live(out, rc, a.resource)
    if not ok:
        print("claimed_run: CLAIM NOT HELD (%s) - COMMAND NOT RUN" % why,
              file=sys.stderr)
        print(out.strip()[-400:], file=sys.stderr)
        return 3

    print("claimed_run: claim held on %s - running" % a.resource, file=sys.stderr)
    try:
        return subprocess.run(cmd).returncode
    finally:
        rrc, rout = _mesh(["release", "--agent", a.agent, "--resource", a.resource])
        print("claimed_run: released (%s)" % ("ok" if rrc == 0 else "rc=%d" % rrc),
              file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
