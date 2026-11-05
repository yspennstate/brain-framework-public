#!/usr/bin/env python3
"""Close the loop on a finished Claude subagent worker: verify it wasn't downgraded and
log the delegation. The parent spawns a worker with the Task tool (model: fable), notes the
returned agentId, then calls this with that id. It ground-truths the served model from the
worker's transcript (message.model) - NOT the worker's self-report, which lies when the
API silently served Opus for a Fable request.

  python record_worker.py --agent-id <id> --requested fable --task-type code_write \
         [--self-report "..."] [--parent-verdict accept] [--task-id t123]

Exit code 2 if the worker was downgraded (so a parent script can branch on it).
"""
from __future__ import annotations
import argparse, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import verify_model as vm            # noqa: E402
from ledger import log_delegation, TASK_TYPES    # noqa: E402

def transcript_usage(path):
    """Rough usage from a Claude subagent transcript: summed output, peak input."""
    tin = tout = 0
    if not path or not os.path.exists(path):
        return 0, 0
    for line in open(path, encoding="utf-8", errors="replace"):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        msg = rec.get("message")
        u = msg.get("usage") if isinstance(msg, dict) else None
        if isinstance(u, dict):
            tout += u.get("output_tokens", 0) or 0
            tin = max(tin, (u.get("input_tokens", 0) or 0) +
                      (u.get("cache_read_input_tokens", 0) or 0))
    return tin, tout

def record(agent_id, requested, task_type, self_report=None, parent_verdict=None,
           quality=None, task_id=None, notes=""):
    transcript = vm.locate_transcript(agent_id)
    v = vm.verify(requested, transcript=transcript, self_report=self_report)
    tin, tout = transcript_usage(transcript)
    rec = log_delegation(
        task_type=task_type, worker=_worker_label(requested, v),
        requested_model=requested, served_model=(v["served"][0] if v["served"] else None),
        downgraded=v["downgraded"], verdict=v["verdict"],
        tokens_in=tin, tokens_out=tout, parent_verdict=parent_verdict, quality=quality,
        notes=notes or f"claude worker agent {agent_id}", task_id=task_id or agent_id)
    return v, rec

def _worker_label(requested, v):
    # label by what we REQUESTED (the routing decision); the downgrade is recorded
    # separately via served_model/downgraded, so routing learns "requesting fable for
    # task X yields a downgrade N% of the time" rather than mislabeling it an Opus choice.
    fam, canon, tier = vm.normalize(requested)
    if canon == "claude-fable-5":
        return "claude-fable"
    if canon == "claude-opus-4":
        return "claude-opus"
    if fam == "openai":
        return "gpt-5.6"
    return requested

def main():
    ap = argparse.ArgumentParser(description="Verify + log a finished Claude subagent worker.")
    ap.add_argument("--agent-id", required=True)
    ap.add_argument("--requested", required=True)
    ap.add_argument("--task-type", required=True, choices=TASK_TYPES)
    ap.add_argument("--self-report")
    ap.add_argument("--parent-verdict", choices=["accept", "fix", "reject"])
    ap.add_argument("--quality", type=float)
    ap.add_argument("--task-id")
    ap.add_argument("--notes", default="")
    a = ap.parse_args()
    v, rec = record(a.agent_id, a.requested, a.task_type, a.self_report,
                    a.parent_verdict, a.quality, a.task_id, a.notes)
    banner = "  <<< DOWNGRADED - the worker did NOT run on the requested model" if v["downgraded"] else ""
    print(f"verdict: {v['verdict']} | requested {a.requested} -> served {v['served']}{banner}")
    for e in v["evidence"]:
        print("  -", e)
    print("logged:", rec["task_id"], "| tokens", rec["tokens_in"], "in /", rec["tokens_out"], "out")
    sys.exit(2 if v["downgraded"] else 0)

if __name__ == "__main__":
    main()
