---
description: Broadcast the High method (verified Fable/GPT-5.6 worker delegation) to all mesh agents and show how to deploy it.
---

The **High method** is our delegation methodology: do the expensive thinking through lean,
disposable Fable/GPT-5.6 workers, verify they were not silently downgraded, then check and
implement their work as the parent. Full guide:
`<HOME>\brain-repo\12_cognitive_architecture\delegation\DELEGATION.md`.

When the owner runs `/high`:

1. **Broadcast to every agent** — run
   `python <HOME>\brain-repo\12_cognitive_architecture\delegation\high_broadcast.py`
   and report the fanout count (how many mailboxes got it). This is the same reminder the
   hourly `HighMethodBroadcast` task sends.

2. **Print the deploy guide** to the owner:
   - **Route** — `route.py --recommend <task_type>` picks the worker from learned data.
   - **Spawn lean** — a Fable subagent (Task tool, `model: fable`) for reasoning/writing/analysis,
     or a GPT-5.6 worker (`codex exec -m gpt-5.6-sol`) for code. Give it only what it needs,
     never the whole brain. Workers are ephemeral — their context is discarded on return.
   - **Verify not downgraded** — the worker's own claim about its model LIES (a capped Fable
     worker still says `claude-fable-5` while served `claude-opus-4-8`). Ground-truth from the
     transcript: `record_worker.py --agent-id <id> --requested fable` (Claude) or
     `delegate_codex.py` (GPT). Measured 2026-07-11: while Fable is capped, general-purpose
     subagents are served Opus regardless of how lean they are; **Explore-type** subagents still
     get Fable. So for read/analysis Fable work, use an Explore worker; for write/code work,
     expect Opus and decide deliberately (accept Opus, retry when Fable resets, or reroute).
   - **Check + implement** — read the worker's output adversarially (worker proposes, parent
     disposes), implement/fix it yourself, and log the outcome (`ledger.py`) so routing learns.

3. Offer to open `ROUTING.md` (current best worker per task) or `DELEGATION.md` (full method).
