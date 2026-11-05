#!/usr/bin/env python3
"""The nightly delegation-learning step, called by the dream.

Rebuilds routing from the ledger, surfaces downgrade alerts and delegation-mistake
candidates, and writes DELEGATION_AGENDA.md - a short block the dream folds into its agenda
so "which agent is best for which task" stays a continuously-dreamt question. The mechanical
part (rebuild routing, compute stats) runs every dream; the reflective part (deciding a
recurring downgrade deserves a reflex, updating priors, writing a memory) is the dream's job,
prompted by the agenda this writes. That split is deliberate: mechanics are cheap and certain,
judgement is the model's.
"""
from __future__ import annotations
import os, sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import route            # noqa: E402
import ledger           # noqa: E402

AGENDA = os.path.join(HERE, "DELEGATION_AGENDA.md")

def _stats(recs):
    dg = defaultdict(lambda: [0, 0])          # worker -> [downgraded, n]
    dg_task = defaultdict(lambda: [0, 0])      # (task,worker) -> [downgraded, n]
    verdicts = defaultdict(lambda: defaultdict(int))
    for r in recs:
        w = r.get("worker", "?")
        dg[w][1] += 1
        dg_task[(r.get("task_type", "other"), w)][1] += 1
        if r.get("downgraded"):
            dg[w][0] += 1
            dg_task[(r.get("task_type", "other"), w)][0] += 1
        pv = r.get("parent_verdict")
        if pv:
            verdicts[w][pv] += 1
    return dg, dg_task, verdicts

def review(write=True):
    recs = ledger.read_records()
    route.rebuild(recs)                        # always keep routing_policy.json fresh
    agg = route.aggregate(recs)
    dg, dg_task, verdicts = _stats(recs)

    alerts = []
    for w, (d, n) in sorted(dg.items()):
        if d > 0:
            alerts.append(f"`{w}` served a lower model on {d}/{n} requests "
                          f"({100 * d // n}%) - requesting it does not guarantee getting it.")
    picks = []
    for tt in ledger.TASK_TYPES:
        rows = route.rank(tt, agg)
        if rows and (rows[0]["n"] > 0 or tt in ("reasoning_proof", "code_write", "literary_writing")):
            r0 = rows[0]
            picks.append(f"{tt}: {r0['worker']} (q={r0['q_eff']}, {r0['basis']})")
    candidates = []
    for (tt, w), (d, n) in sorted(dg_task.items()):
        if n >= 3 and d / n > 0.5:
            candidates.append(f"requesting {w} for {tt} was downgraded {d}/{n} times - route to a "
                              f"model you can actually get, or accept the downgrade knowingly (reflex candidate).")
    for w, vs in verdicts.items():
        tot = sum(vs.values())
        if tot >= 3 and vs.get("reject", 0) / tot > 0.4:
            candidates.append(f"{w} was rejected on {vs.get('reject',0)}/{tot} checked tasks - "
                              f"it may be the wrong worker for what it's being sent (reflex candidate).")

    if write:
        _write_agenda(alerts, picks, candidates, len(recs))
    return {"n": len(recs), "alerts": alerts, "picks": picks, "candidates": candidates}

def _write_agenda(alerts, picks, candidates, n):
    L = ["# Delegation review (for the dream)",
         f"_{n} delegations logged. Rebuilt routing_policy.json. Reflect on the below and, if a",
         "pattern recurs, distil it into a MISTAKES.md line or an instinct, and update priors._", ""]
    if alerts:
        L += ["## Downgrade alerts (requested != served)"] + [f"- {a}" for a in alerts] + [""]
    if picks:
        L += ["## Current best worker per task (learned/prior)"] + [f"- {p}" for p in picks] + [""]
    if candidates:
        L += ["## Delegation-mistake candidates (decide if each deserves a reflex)"] + \
             [f"- {c}" for c in candidates] + [""]
    L += ["## Standing question to dream on",
          "- Which agent is genuinely best for which task type, on the evidence so far? Where is the",
          "  ledger too thin to say, and what small experiment would settle it? Are we over-requesting",
          "  Fable (paying the verify+retry cost) where Opus or GPT-5.6 would do as well?"]
    with open(AGENDA, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")

def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # Discovery is not execution: this script writes, so a help request must
        # exit before any work. See script_help_flag_can_execute_work.
        print(__doc__ or os.path.basename(__file__))
        return
    r = review()
    print(f"[delegation review] {r['n']} delegations | {len(r['alerts'])} downgrade alerts | "
          f"{len(r['candidates'])} mistake candidates -> {AGENDA}")
    for a in r["alerts"]:
        print("  ALERT:", a)
    for c in r["candidates"]:
        print("  CANDIDATE:", c)

if __name__ == "__main__":
    main()
