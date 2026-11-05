#!/usr/bin/env python3
"""The delegation ledger - the memory the routing brain learns from.

One JSONL record per delegated task. The parent appends a row when it spawns a worker
(with the verified served model from verify_model), then updates parent_verdict after it
checks the worker's output. The dream reads this nightly to learn which agent is best for
which task type (route.py) and to harvest delegation mistakes (recurring downgrades that
slipped through, workers whose output kept getting rejected).

Schema (one JSON object per line):
  ts               ISO8601 UTC
  task_id          short id for the delegated unit of work
  task_type        one of TASK_TYPES (see taxonomy below)
  worker           logical worker: claude-fable | claude-opus | gpt-5.6 | ...
  requested_model  what we asked for (alias ok)
  served_model     GROUND TRUTH from verify_model (message.model), or null if unverifiable
  downgraded       bool - served a lower tier than requested
  verdict          verify_model verdict: OK | DOWNGRADED | DIFFERENT_MODEL | UNVERIFIABLE
  tokens_in/out    usage (0 if unknown)
  duration_ms      wallclock of the worker
  parent_verdict   accept | fix | reject | null  (filled after the parent checks the work)
  quality          optional float 0..1 the parent assigns
  notes            free text
"""
from __future__ import annotations
import argparse, json, os
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
LEDGER = os.path.join(HERE, "delegation_ledger.jsonl")

# task taxonomy - deliberately small; extend as real usage demands
TASK_TYPES = [
    "reasoning_proof",    # math/logic, proof search, hard derivation
    "code_write",         # implement a feature/script from a spec
    "code_review",        # find bugs / audit a diff
    "refactor",           # mechanical-but-careful code restructuring
    "literary_writing",   # the novel, prose, human-voice writing
    "summarize_extract",  # read a lot, return a compact structured digest
    "search_research",    # find things across files / the web
    "data_analysis",      # numbers, tables, small analyses
    "mechanical_edit",    # rote edits, formatting, renames
    "orchestration",      # long-running coordination (usually the parent itself)
    "other",
]
WORKERS = ["claude-fable", "claude-opus", "gpt-5.6"]
PARENT_VERDICTS = ["accept", "fix", "reject"]

def _now():
    return datetime.now(timezone.utc).isoformat()

def log_delegation(task_type, worker, requested_model, served_model=None,
                   downgraded=False, verdict="UNVERIFIABLE", tokens_in=0, tokens_out=0,
                   duration_ms=0, parent_verdict=None, quality=None, notes="",
                   task_id=None, path=LEDGER):
    rec = {
        "ts": _now(),
        "task_id": task_id or f"t{int(datetime.now(timezone.utc).timestamp())}",
        "task_type": task_type,
        "worker": worker,
        "requested_model": requested_model,
        "served_model": served_model,
        "downgraded": bool(downgraded),
        "verdict": verdict,
        "tokens_in": int(tokens_in or 0),
        "tokens_out": int(tokens_out or 0),
        "duration_ms": int(duration_ms or 0),
        "parent_verdict": parent_verdict,
        "quality": quality,
        "notes": notes,
    }
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    return rec

def set_parent_verdict(task_id, parent_verdict, quality=None, path=LEDGER):
    """Rewrite the row for task_id with the parent's post-check verdict."""
    if not os.path.exists(path):
        return False
    rows, hit = [], False
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        if r.get("task_id") == task_id:
            r["parent_verdict"] = parent_verdict
            if quality is not None:
                r["quality"] = quality
            hit = True
        rows.append(r)
    if hit:
        with open(path, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
    return hit

def read_records(path=LEDGER):
    if not os.path.exists(path):
        return []
    out = []
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out

def main():
    ap = argparse.ArgumentParser(description="Delegation ledger.")
    sub = ap.add_subparsers(dest="cmd")
    lg = sub.add_parser("log")
    lg.add_argument("--task-type", required=True, choices=TASK_TYPES)
    lg.add_argument("--worker", required=True)
    lg.add_argument("--requested-model", required=True)
    lg.add_argument("--served-model")
    lg.add_argument("--downgraded", action="store_true")
    lg.add_argument("--verdict", default="UNVERIFIABLE")
    lg.add_argument("--tokens-in", type=int, default=0)
    lg.add_argument("--tokens-out", type=int, default=0)
    lg.add_argument("--duration-ms", type=int, default=0)
    lg.add_argument("--parent-verdict", choices=PARENT_VERDICTS)
    lg.add_argument("--quality", type=float)
    lg.add_argument("--notes", default="")
    lg.add_argument("--task-id")
    vp = sub.add_parser("verdict")
    vp.add_argument("--task-id", required=True)
    vp.add_argument("--parent-verdict", required=True, choices=PARENT_VERDICTS)
    vp.add_argument("--quality", type=float)
    sub.add_parser("stats")
    a = ap.parse_args()
    if a.cmd == "log":
        print(json.dumps(log_delegation(
            a.task_type, a.worker, a.requested_model, a.served_model, a.downgraded,
            a.verdict, a.tokens_in, a.tokens_out, a.duration_ms, a.parent_verdict,
            a.quality, a.notes, a.task_id), indent=2))
    elif a.cmd == "verdict":
        print("updated" if set_parent_verdict(a.task_id, a.parent_verdict, a.quality) else "task_id not found")
    elif a.cmd == "stats":
        recs = read_records()
        print(f"{len(recs)} delegations logged")
        by = {}
        for r in recs:
            by.setdefault(r["worker"], []).append(r)
        for w, rs in sorted(by.items()):
            dg = sum(1 for r in rs if r.get("downgraded"))
            print(f"  {w}: {len(rs)} tasks, {dg} downgraded")
    else:
        ap.print_help()

if __name__ == "__main__":
    main()
