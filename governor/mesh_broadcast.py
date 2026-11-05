#!/usr/bin/env python3
"""mesh_broadcast.py - send a warning to every live mesh agent (used by the compute governor).

The governor rate-limits upstream (one distress broadcast per 10 min), so this stays dumb:
enumerate live agents from `agent_mesh.py --json status`, send the body to each as a warning.
Registers a stable governor identity on first use. Fail-safe: any error exits 0 silently.
"""
import argparse, json, subprocess, sys

MESH = r"{{USERPROFILE}}\brain-repo\12_cognitive_architecture\agent_mesh\agent_mesh.py"
SELF = "governor:<hostname>"


def run(args, timeout=15):
    return subprocess.run([sys.executable, "-s", MESH] + args,
                          capture_output=True, text=True, timeout=timeout)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic", required=True)
    ap.add_argument("--body", required=True)
    ap.add_argument("--dry", action="store_true", help="print targets, send nothing")
    a = ap.parse_args()
    try:
        st = run(["--json", "status"])
        agents = []
        try:
            agents = json.loads(st.stdout or "{}").get("agents", [])
        except Exception:
            agents = []
        targets = [ag.get("agent_id") for ag in agents
                   if ag.get("agent_id") and ag.get("agent_id") != SELF
                   and ag.get("status") in ("active", "working")]
        if a.dry:
            print("would send to %d agents: %s" % (len(targets), ", ".join(targets)))
            return
        if not targets:
            return
        # make sure the sender identity exists (idempotent-ish; ignore failures)
        run(["hello", "--agent", SELF, "--runtime", "claude",
             "--task", "compute governor distress broadcaster"])
        for t in targets:
            try:
                run(["send", "--agent", SELF, "--to", t, "--topic", a.topic,
                     "--kind", "warning", "--body", a.body])
            except Exception:
                pass
    except Exception:
        return


if __name__ == "__main__":
    main()
