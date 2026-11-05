# The High method — orchestrator + worker delegation

*(Named "the High method" by the owner, 2026-07-11. `/high` broadcasts it to every agent; the
hourly `HighMethodBroadcast` task re-sends it so the whole fleet keeps deploying it.)*

A parent (orchestrator) does the hard, expensive thinking cheaply by handing well-scoped
units of work to *ephemeral, lean workers*, verifying the workers weren't silently
downgraded, checking their output, and implementing or fixing it itself. It is how we get
the capability of the top models without burning usage, and without being lied to about
which model actually ran.

## Why this exists (measured, not assumed)

On 2026-07-11 I spawned five Fable workers in one session. **Three of four explicit
`model: fable` requests were silently served on `claude-opus-4-8`.** Every one of them
*self-reported* "I am claude-fable-5" — because a subagent's system prompt is written from
the requested model, not the served one. The self-report cannot see the downgrade. Only the
per-message `model` field in the worker's transcript tells the truth. So the methodology has
two non-negotiables: **never trust a worker's self-report of its model**, and **the parent
checks the work before it lands**.

**What actually gets you Fable (measured 2026-07-11, within one minute).** Leanness does *not*
prevent the downgrade — three trivial "return 42" Fable subagents (minimal context, no brain)
were all served Opus. The downgrade is **cap-driven at the account level, not context-driven**.
The lever that worked: **subagent type**. An `Explore`-type worker was served Fable while
general-purpose workers at the same moment got Opus (Explore: 2/2 Fable; general-purpose: 5/5
Opus). Explore is read-only, so it fits research/read/analysis Fable-work; for write/code work
you currently get Opus and must decide deliberately. This is a current, cap-state observation
the ledger keeps re-measuring, not a permanent law.

## The shape

- The **parent** stays on an uncapped model (Opus) when it must run long without stalling,
  or is whatever session you're in. It routes, spawns, verifies, checks, and implements.
- **Workers** are lean and disposable: minimal context in, one tight task, a compact result
  out, then discarded (their context is cleared — nothing accumulates across tasks). This is
  what keeps usage low: many small verified workers, not one giant context that grows.

## Two worker runtimes (same contract)

1. **Claude Fable worker** — spawn with the Task/Agent tool, `model: fable`. It skips the
   brain wake, so it starts lean. After it finishes, verify + log with
   `record_worker.py --agent-id <id> --requested fable --task-type <t>`.
2. **GPT-5.6 worker** — `codex exec -m gpt-5.6-sol --json` (the account's real model; a plain
   `gpt-5.6` is rejected with HTTP 400). `delegate_codex.py` wraps spawn + verify + log in one
   call. It runs in a clean working dir so it stays lean.

## The five-step parent protocol

1. **Route** — `python route.py --recommend <task_type>` picks the worker from learned data
   (falls back to priors when data is thin).
2. **Spawn lean** — give the worker only the files/spec it needs, never the brain. One task.
3. **Verify not downgraded** — Claude: `record_worker.py --agent-id …`; GPT: `delegate_codex`
   reports it. If `DOWNGRADED`, decide deliberately: accept Opus (still capable), retry later
   when Fable resets, or re-route. Do **not** proceed believing you got Fable.
4. **Check the work** — read the worker's output adversarially. The worker proposes; the
   parent disposes. Never apply an unread proposal.
5. **Implement / fix** — the parent makes the actual change to shared state, fixing the
   worker's proposal as needed, and records `--parent-verdict accept|fix|reject`.

## Downgrade verification — the crux

- **Ground truth** = `message.model` in the Claude worker transcript, or the
  `error` / `turn.failed` / "defaulting to fallback metadata" events from `codex exec --json`.
- The **yellow notice** you see in an interactive session is the same fact; headless, it lives
  in the transcript. `verify_model.py` reads it (self-tested against the real downgrades above).
- Independent signals that *could* diverge: (1) transcript model — authoritative; (2) the
  worker's self-report — weak, and its disagreement with (1) *is* the downgrade signature;
  (3) fallback/limit phrases. Agreement of two checks that share a method is not corroboration.

## The learning loop (continuous)

Every delegation is logged (`ledger.py` → `delegation_ledger.jsonl`): requested vs served
model, downgraded, tokens, duration, and the parent's verdict. `route.py` learns the best
worker per task type from that ledger (shrinkage toward priors, so cold-start is sane and data
takes over). The **nightly dream** runs `dream_delegation_review.py`: it rebuilds routing,
harvests recurring delegation failures into `MISTAKES.md` as reflex candidates, and reflects on
which agent suits which task. Routing therefore improves on its own, and repeated mistakes
become instincts.

## Files

| file | role |
|---|---|
| `verify_model.py` | downgrade detector (Claude transcript + Codex events), `--selftest` |
| `record_worker.py` | verify + log a finished Claude subagent by agentId |
| `delegate_codex.py` | spawn + verify + log a GPT-5.6 worker |
| `ledger.py` / `delegation_ledger.jsonl` | the performance record the brain learns from |
| `route.py` / `routing_policy.json` / `ROUTING.md` | learned task→agent routing |
| `dream_delegation_review.py` | the nightly learning + mistake-harvest step |

## Orchestrator preamble (paste into a parent launch prompt)

> You are an orchestrator. Do the expensive thinking through lean, disposable workers, and
> keep only the conclusions. For a well-scoped sub-task, spawn a worker: a Fable subagent
> (Task tool, `model: fable`) for reasoning/writing/analysis, or a GPT-5.6 worker
> (`codex exec -m gpt-5.6-sol`) for code. Give it only what it needs — never the whole
> context. When it returns: (1) verify it actually ran on the model you asked for
> (`record_worker.py` / `delegate_codex` — the worker's own claim about its model is not
> evidence); if it was downgraded, decide deliberately. (2) Read its output adversarially and
> you implement the change, fixing its proposal as needed. Log the outcome so routing learns.
