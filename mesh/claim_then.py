"""Run a command ONLY if a mesh claim is actually granted.

The failure this exists to prevent: writing

    mesh claim --resource memory:brain/index ... ; python edit_memory.py

in one shell line. The ';' runs the edit unconditionally, so a claim_conflict is
printed and then ignored - the claim becomes decoration and the mesh becomes
advisory theatre. That happened on 2026-08-04 06:0x; nothing was lost only
because the two writes did not interleave, and MEMORY.md is a read-modify-write
of the whole file, so interleaved writers lose an entry silently.

usage:
    python claim_then.py --agent <id> --resource <res> [--mode write]
                         [--purpose "..."] -- <command> [args...]

exit codes:
    0   claim granted, command ran, command's own exit code is returned
    3   claim CONFLICT - the command was NOT run; the holder is printed
    4   claim failed for another reason - the command was NOT run

The claim is released on the way out whether the command succeeded or not, so a
failing command cannot strand a lease.
"""
import os
import subprocess
import sys

# Derived, so this tree carries no single machine's username.
MESH = os.path.expanduser(
    "~/brain-repo/12_cognitive_architecture/agent_mesh/agent_mesh.py")


def main(argv):
    if any(a in ("-h", "--help") for a in (argv or [])):
        # Discovery is not execution: this script writes, so a help request must
        # exit before any work. See script_help_flag_can_execute_work.
        print(__doc__ or os.path.basename(__file__))
        return 0
    if "--" not in argv:
        print(__doc__)
        return 4
    split = argv.index("--")
    opts, cmd = argv[:split], argv[split + 1:]
    if not cmd:
        print("no command after --")
        return 4

    def opt(name, default=None):
        return opts[opts.index(name) + 1] if name in opts else default

    agent = opt("--agent")
    resource = opt("--resource")
    if not agent or not resource:
        print("--agent and --resource are required")
        return 4
    mode = opt("--mode", "write")
    purpose = opt("--purpose", "claim_then wrapper")

    claim = subprocess.run(
        [sys.executable, MESH, "claim", "--agent", agent,
         "--resource", resource, "--mode", mode, "--purpose", purpose],
        capture_output=True, text=True)
    out = (claim.stdout or "") + (claim.stderr or "")
    if claim.returncode == 3 or "conflict" in out.lower():
        print("CLAIM CONFLICT on %s - command NOT run." % resource)
        print(out.strip()[:800])
        print("Message the holder, choose a disjoint scope, or wait. "
              "Do not bypass silently.")
        return 3
    if claim.returncode != 0:
        print("CLAIM FAILED (rc=%d) on %s - command NOT run." % (claim.returncode, resource))
        print(out.strip()[:800])
        return 4

    try:
        rc = subprocess.run(cmd).returncode
    finally:
        subprocess.run(
            [sys.executable, MESH, "release", "--agent", agent,
             "--resource", resource],
            capture_output=True, text=True)
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
