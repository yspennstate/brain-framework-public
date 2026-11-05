#!/usr/bin/env python3
"""Learned task->agent routing. Reads the delegation ledger, learns which worker is best
for each task type, writes routing_policy.json + ROUTING.md. The dream calls rebuild()
nightly so the policy is a continuously-learned thing, not a fixed table.

Cold start: with little data, recommendations lean on PRIORS (honest guesses, clearly
labelled as priors in the output). As real outcomes accumulate the data takes over
(Bayesian shrinkage toward the prior with strength K).

Scoring per (task_type, worker):
  quality   parent_verdict mapped accept=1.0 / fix=0.5 / reject=0.0 (None rows ignored)
  q_eff     (sum_quality + K*prior_q) / (n_scored + K)     # shrink to prior
  downgrade fraction of attempts served below the requested tier (reported, not hidden)
  tokens    mean total tokens (efficiency tie-break)
Recommendation = workers sorted by q_eff desc, then tokens asc.
"""
from __future__ import annotations
import argparse, json, os
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
import sys
sys.path.insert(0, HERE)
from ledger import read_records, TASK_TYPES, WORKERS  # noqa: E402
import verify_model as _vm  # noqa: E402


def _canon_worker(w):
    """Unify worker labels so 'fable' / 'claude-fable' / 'claude-fable-5' don't become three
    separate ranked entities (review finding #12) - data must attach to one logical worker."""
    fam, canon, _ = _vm.normalize(w or "")
    if canon == "claude-fable-5":
        return "claude-fable"
    if canon == "claude-opus-4":
        return "claude-opus"
    if fam == "openai":
        return "gpt-5.6"
    return w or "?"

POLICY_JSON = os.path.join(HERE, "routing_policy.json")
ROUTING_MD = os.path.join(HERE, "ROUTING.md")

# PRIORS: ordered best-guess worker per task type. These are guesses to be corrected by
# data - the whole point of the ledger is to learn the real answer. Fable is the most
# capable Claude model (when not capped); Opus is the uncapped fallback good for long
# orchestration; gpt-5.6 (Codex) is strong at writing/refactoring code.
PRIORS = {
    "reasoning_proof":   ["claude-fable", "gpt-5.6", "claude-opus"],
    "code_write":        ["gpt-5.6", "claude-fable", "claude-opus"],
    "code_review":       ["claude-fable", "gpt-5.6", "claude-opus"],
    "refactor":          ["gpt-5.6", "claude-fable"],
    "literary_writing":  ["claude-fable", "claude-opus"],
    "summarize_extract": ["claude-fable", "gpt-5.6"],
    "search_research":   ["claude-fable", "gpt-5.6"],
    "data_analysis":     ["claude-fable", "gpt-5.6"],
    "mechanical_edit":   ["gpt-5.6", "claude-opus"],
    "orchestration":     ["claude-opus"],
    "other":             ["claude-fable", "gpt-5.6", "claude-opus"],
}
K = 3.0  # shrinkage strength (samples of "virtual prior evidence")
_Q = {"accept": 1.0, "fix": 0.5, "reject": 0.0}

def _prior_q(task_type, worker):
    order = PRIORS.get(task_type, [])
    if worker in order:
        return max(0.5, 0.7 - 0.05 * order.index(worker))  # 0.70, 0.65, 0.60, ...
    return 0.45

def aggregate(records):
    agg = defaultdict(lambda: {"n": 0, "scored": 0, "sum_q": 0.0,
                               "downgraded": 0, "tokens": 0, "dur": 0})
    for r in records:
        tt = r.get("task_type") or "other"          # explicit None -> "other" (finding #11)
        w = _canon_worker(r.get("worker", "?"))      # unify worker aliases (finding #12)
        a = agg[(tt, w)]
        a["n"] += 1
        if r.get("downgraded"):
            a["downgraded"] += 1
        a["tokens"] += (r.get("tokens_in", 0) or 0) + (r.get("tokens_out", 0) or 0)
        a["dur"] += r.get("duration_ms", 0) or 0
        pv = r.get("parent_verdict")
        if pv in _Q:
            a["scored"] += 1
            a["sum_q"] += _Q[pv]
    return agg

def rank(task_type, agg):
    workers = set(WORKERS) | {w for (tt, w) in agg if tt == task_type}
    rows = []
    for w in workers:
        a = agg.get((task_type, w), {"n": 0, "scored": 0, "sum_q": 0.0,
                                     "downgraded": 0, "tokens": 0, "dur": 0})
        pq = _prior_q(task_type, w)
        q_eff = (a["sum_q"] + K * pq) / (a["scored"] + K)
        dr = (a["downgraded"] / a["n"]) if a["n"] else 0.0
        avg_tok = (a["tokens"] / a["n"]) if a["n"] else 0
        rows.append({
            "worker": w, "n": a["n"], "scored": a["scored"],
            "q_eff": round(q_eff, 3), "downgrade_rate": round(dr, 2),
            "avg_tokens": int(avg_tok),
            "basis": "data" if a["scored"] >= K else "prior-led",
        })
    rows.sort(key=lambda r: (-r["q_eff"], r["avg_tokens"]))
    return rows

def rebuild(records=None):
    records = read_records() if records is None else records
    agg = aggregate(records)
    policy = {}
    for tt in TASK_TYPES:
        policy[tt] = rank(tt, agg)
    with open(POLICY_JSON, "w", encoding="utf-8") as f:
        json.dump({"n_records": len(records), "policy": policy}, f, indent=2)
    _write_md(policy, len(records))
    return policy

def _write_md(policy, n):
    lines = ["# Task -> agent routing (learned)", "",
             f"Rebuilt from {n} logged delegations. `basis: prior-led` means not enough real",
             "outcomes yet - the recommendation is still leaning on the cold-start prior.",
             "`downgrade_rate` = how often the requested model was silently served as a lower one",
             "(verify before trusting a Fable request actually ran on Fable).", ""]
    for tt in TASK_TYPES:
        rows = policy[tt]
        best = rows[0]["worker"] if rows else "-"
        lines.append(f"## {tt}  ->  **{best}**")
        for r in rows:
            lines.append(f"- {r['worker']}: q={r['q_eff']} n={r['n']} "
                         f"downgrade={r['downgrade_rate']} tok~{r['avg_tokens']} ({r['basis']})")
        lines.append("")
    with open(ROUTING_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

def recommend(task_type):
    records = read_records()
    rows = rank(task_type, aggregate(records))
    return rows[0]["worker"] if rows else None

def main():
    ap = argparse.ArgumentParser(description="Learned delegation routing.")
    ap.add_argument("--recommend", choices=TASK_TYPES, help="print best worker for a task type")
    ap.add_argument("--rebuild", action="store_true", help="rebuild routing_policy.json + ROUTING.md")
    a = ap.parse_args()
    if a.recommend:
        print(recommend(a.recommend))
    else:
        pol = rebuild()
        print(f"routing rebuilt for {len(pol)} task types -> {ROUTING_MD}")

if __name__ == "__main__":
    main()
