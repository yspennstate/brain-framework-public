# Compaction: how sessions compact and what is kept

The agents that build and test the brain run for hours or days in one session. Every model call
re-reads the whole context, so the cost of a turn is the size of the context, not the size of the
new message. In one measured session on 9 October 2026 that was about 663,000 tokens a turn.
Compaction replaces the conversation so far with a written summary and the session continues from
it. This document describes how compaction is configured, when it is triggered, what the summary is
told to keep, how every summary becomes a node in the brain, and what went wrong on the way. The code
is in `framework/dreaming/`; the aggregate measurements are in Appendix I of
`papers/math-brain-2026-10-24` (`sections/I_compaction.tex`).

## The harness

What follows was read from the Claude Code binary and checked against session transcripts.

- Automatic compaction summarizes the conversation when context use approaches `autoCompactWindow`,
  a settings key between 100,000 and 1,000,000 tokens. The threshold is the smaller of that key and
  the model's window. The same knob exists as the environment variable
  `CLAUDE_CODE_AUTO_COMPACT_WINDOW`; `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` sets a percentage instead.
- The window is read when a session starts. A session that was running when the setting changed keeps
  compacting near the old limit: one session that started on 1 October compacted seven times on
  9 October at 966,643 to 969,909 tokens, two hours after the window was set to 300,000.
- A `PreCompact` hook receives the trigger (manual or automatic). Whatever it prints is handed to the
  summarizer as instructions. A `PostCompact` hook receives the trigger and the summary text.
- A hook cannot compact a session. No hook output field does it. An agent cannot type `/compact` to
  itself in an interactive session either; that is the user's command.
- Codex has no compaction hook. Its rollout files carry a `compacted` record, and it compacts at
  `model_auto_compact_token_limit` in `~/.codex/config.toml`. In the Codex build used here the record's
  message is empty and the summary is encrypted, so the text cannot be read back from the rollout.

## Settings

Both runtimes compact at 300,000 tokens: `autoCompactWindow` 300000 in `~/.claude/settings.json` and
`model_auto_compact_token_limit` 300000 in `~/.codex/config.toml` (it had been 800,000). The window
has to sit above the context a session carries right after a compaction. Here that baseline is
140,000 to 160,000 tokens: the summary, the kept tail of the conversation, the system prompt, tool
definitions and hook output. A 200,000-token window tried for headless workers sat close enough to
that baseline that sessions compacted every few minutes and the harness reported that auto-compact
was thrashing. Measure the baseline before choosing a window: it is the input of the first model call
after a compaction (input plus cache creation plus cache read in that call's usage).

A window too close to the baseline also costs more per message. Headless sessions at 200,000 compacted
every 15 messages or so (184 compactions in 2,843 messages) and read about 50,000 input-equivalent tokens a
message; interactive sessions at 300,000 compacted every 56 messages (552 in 31,113) and read about 43,000,
before counting the compaction calls themselves.

## Compacting by task, not only by size

A summary written when a subject is finished loses less than one written in the middle of a subject.
So 53 hooks (`compact_hooks.jsonl`, fired by `compact_hooks.py` on Claude through the hook dispatcher
and on Codex through `hooks.json` with JSON output) watch for the end of a piece of work and tell the
session to bring it to a stopping point. Their signals:

- a task closed on the checklist, a final report, a push, a commit, a released claim, a handoff sent,
  a report or gate decision written, tests passing, a checkpoint written;
- the user switching subject or correcting the agent (the correction goes on disk first);
- size: context bands from 200,000 to 500,000 tokens, a large tool result that now rides in every
  turn, many whole-file reads, long runs of small steps;
- an idle gap on a large context, repeated failures, a background job without a recorded handle;
- right after a compaction: start from the NEXT line, check claims and inbox, treat the user's newest
  words as current, answer from the records rather than the old context.

Each hook has a cooldown, at most one fires per action, small routine commands stay silent, and fires
are logged to `~/.claude/brain_graph/compactions/hook_fires.jsonl`. Mathematical work has one more path:
a solver run that wants its result kept supplies a math-only digest (in the node's extra fields, or in the
file named by `COMPACT_MATH_STAGE_FILE`), and `compact_nodes.py` hands it to `compact_math.py`, which checks
it and stages it under `math_drafts` with a name ending in `.json.pending`, so nothing imports it into the
math store before it is reviewed. The session summary itself is never used as mathematical input.

A hook can only tell the session; it cannot compact it. For interactive sessions a `Stop` hook therefore
starts a small windowless helper when the turn closed a task and the context is past 150,000 tokens. The
helper waits until the session is idle and types `/compact` into that session's own console input buffer,
never into the focused window. Idle means the last assistant turn has ended, no queued prompt or scheduled
loop firing comes after it (a new turn shows first as a queue record; the user record reaches the transcript
only with the first reply), and there has been no keyboard or mouse input for 15 seconds. It gives up if a
new prompt arrives while it waits and fires at most once per 20 minutes per session. A `/compact` typed into
a busy session is queued by the harness, not lost. Headless workers compact best by ending: one task per
session, a NEXT line, and the next session seeded from it.

Since no hook can compact a session, `compact_on_task_end.py` does it from outside. On `Stop`, when the
turn closed a task (a checklist `done` command outside a heredoc, or the end-of-task line) and the
context is 150,000 tokens or more, it starts a windowless helper that waits until the session is idle
and types `/compact` into it. Idle needs three things: the last turn ended, no queued prompt or loop
fire after it, and 15 seconds without keyboard or mouse input, so the keys never land inside text the
user is typing. The first version judged idle by the last assistant record alone and was wrongly
recorded as a failure: the keys had arrived, Claude Code queued `/compact`, and ran it at the end of a
turn that had started 16 seconds earlier. A `/compact` typed into a busy session is queued, not lost.

A headless worker compacts best by ending: one task per session, the next seeded from its NEXT line.
For Codex, `codex_manual_compact.py` saves a summary the agent writes itself (it must contain a NEXT
line) as a compaction node, because the harness's own summary is unreadable.

## What the summary is told to keep

On `PreCompact`, `compact_nodes.py` tells the summarizer to keep the open requests in the user's
words, task ids, commit hashes, file paths, held claims and the session's NEXT line, and never to copy
test questions or answer keys. Two scars shaped this list:

- A summary keeps what was written down, not the reasoning or the work that was done. On 24 September
  an agent told a peer that an experiment had never been run; two complete runs from before its
  compaction were on disk. After a compaction, search the artifacts, not the summary.
- A long `/goal` condition judged on the visible transcript can never read as met once the transcript
  has been compacted. Recognize that and do not loop on it.

## Every compaction is a node

On `PostCompact`, `compact_nodes.py` saves the summary as a node under
`~/.claude/brain_graph/compactions/<date>/` and an anchor memory of the same name in the memory folder.
The anchor holds the request, the pending work, the current work and the next step, and links to:

- a hub memory that describes compaction;
- the same session's previous compaction;
- the checklist tasks open under the session's agent, and other sessions' compactions on those tasks;
- the five memories recall finds closest to the summary.

The graph rebuild turns those links into edges, so recall can reach a session's compactions by
question. `INDEX.jsonl` holds one row per node (time, runtime, session, trigger, length, sha256, task
ids, previous node, related memories). An anchor whose summary mentions test material carries no
excerpt and is marked `test_adjacent`; the full text stays local. Compactions never enter the math
store: copying them in would carry test material and private review text, so the store grows from
memories instead.

`compact_nodes.py --sweep` backfills from Claude transcripts (the `compact_boundary` record and its
summary) and from Codex rollouts; the hook dispatcher starts it windowless at most every 15 minutes,
under a lock. `--list` counts compactions read-only, `--relink` adds related links later, and
`--paths-file` runs a disjoint shard in parallel. `compact_index.py` maintains the index. By 26 October
the index held 11,581 nodes: 10,158 from Claude sessions and 1,423 from Codex.

Two hooks close the loop with the session itself. Right after a compaction a session is told whether
its summary became a node, and how to rebuild it if not. `keep_working_after_compact.py` handles an
agent that writes a recap after compacting and stops while its tasks are still open: on the first
`Stop` after a compaction, and only when the checklist holds open entries under that agent, it answers
`{"decision": "block"}` and lists the open work. It fires once per compaction and never while a stop
hook is already active. An agent with nothing left says so in one line and stops. `wake_cached.py`
answers `SessionStart` with source `compact` by handing back the wake text the session already
received. Re-running the full wake after every compaction had stalled agents for 160 to 390 seconds
each time.

Compaction nodes also compete in recall. A summary repeats the user's words exactly, so when a user's
sentence is quoted back, the summaries that quote it can fill the first eight results above the memory
that records the request. Capping summaries at one or two a session (28 October) and multiplying every
summary's word score by 0.6 (2 November) were both rejected: a summary is often the only record of its
session, and the second change cut the first eight on 1,976 questions about summaries from 482 to 129.
What was kept moves a single row. The best record that is not a summary goes up to eighth place when it
scores at least 0.7 of the top word score; on 187 quoted sentences the first eight rose from 168 to 170,
with none worse. The numbers are in TESTS.md.

## What went wrong, and the fixes

- Lost to encoding (9 October). The hook read its input in the console code page, and a lone surrogate
  made the UTF-8 write fail. It now reads bytes as UTF-8, and a lone surrogate becomes U+FFFD.
- Only one node, in a place nobody looked (9 October). The early nodes lived only under
  `brain_graph`, where neither recall nor the graph reads, and only one compaction had ever been saved.
  The anchor memories fixed both.
- Busy sessions dropped (14 October). The backfill matched a transcript compaction to a hook row
  within 600 seconds by time alone, so a worker that compacted two or three times in ten minutes lost
  the later ones: 1,012 of 1,764 between 2 and 11 October. A match now needs 80 percent text overlap
  within 120 seconds, and targeted saves restored the missing nodes. The cut sits in a measured gap: a
  node's own compaction scored at least 0.811 (median 1.00), and the 3,070 other compactions of the same
  sessions within 30 minutes scored at most 0.730 (99th percentile 0.53).
- Wrong session (12 October). A subagent's compaction reaches the hook with the parent's session id
  and no agent id, and the agent's own compact record is appended only after `PostCompact` returns. A
  late pass and the sweep now file it under the right agent (84 repaired). Workflow stream agents had
  also been filed under their coordinator.
- Codex (10 October). No Codex compaction had ever become a node: the record's message was empty, the
  summary encrypted, and the sweeper cut session ids to 35 of 36 UUID characters. Codex now gets
  metadata-only nodes, kept away from test working directories, plus the manual summary above.
- Off-topic links (14 October). Recall's top five were 55 to 62 percent on topic in a blind check. A
  rare-word overlap gate (above 0.03 by memory document frequency) raised that to about 83 percent, and
  the linker now reads the summary without session plumbing (identity blocks, standing constraints,
  launcher prompts). In a blind check of 20 nodes, all 8 that had no links gained some; of the 12 that
  had links, 5 got better, 3 got worse and 4 were unchanged.
- False notes (14 October). The hook said a claim had been released after a `--help` call. Its
  claim-released and gate-written notes now read the command and its result. Replaying 111,785 tool
  calls removed 66 false notes and added none. The hook also moved inside the dispatcher process, which
  cut the median `PostToolUse` time from 0.28 to 0.08 seconds.

## Measuring a compaction

The `postTokens` field of a compaction record (median about 19,000 tokens) is not what the next call
reads. The next call carries the summary plus the kept tail of the conversation and the hook output.
Measure both sides from the usage of the assistant messages around the boundary (input plus cache read
plus cache write), counting each API call once by its message id, since a call is logged once per
content block, and skipping sidechain records. Over 1,256 transcripts and 8,617 compactions (8,536
measurable) the medians were:

| | tokens |
|---|---|
| last model call before the compaction | 264,360 |
| `postTokens` recorded by the harness | 18,980 |
| first model call after the compaction | 166,019 (cache read 33,224, cache write 133,525) |

The median ratio of the call before to the call after was 1.52 (1.63 for automatic compactions, 1.25
for manual ones). A ratio built on `postTokens` comes out about nine times larger. Appendix I of
`papers/math-brain-2026-10-24` tabulates the recorded fields, so its before-over-after median of 13.8
describes the summary side only. Most of what is re-read after a compaction is fixed context, so
shrinking the fixed context is the other lever, and unlike compacting more often it loses no working
detail. Removed context is not billed tokens saved: a cost estimate prices the re-read tokens as cache
reads and writes at their own rates, and adds the cost of writing the summary.

## How the Codex path was tested (9 October)

The Codex path was built on copies of the live helpers, and no candidate went live before it passed.
The fifth candidate passed all of the following:

- 66 focused unit tests covering the manual save, the metadata-only boundary nodes and the sweeper's
  session identity (`framework/dreaming/test_codex_*.py`).
- 61 controls from four older compaction suites, rerun against the candidate, and the 20 tests of the
  math solver's compaction export.
- A save through the real command line and the real mesh API on a synthetic file tree. It checked
  interactive and worker saves, task links, that a second save changes nothing, that a private summary
  stays out of the index, that a refused claim leaves no partial files, and that a save for the wrong
  worker is refused.
- Four real encrypted compaction records from one Codex thread. They became four metadata nodes with
  full session ids and task and previous links, and no retained message was copied.
- The three patches, applied with fixed line-ending settings, reproduce the candidate byte for byte.
  The first check ran with git's default `autocrlf`, which turned LF into CRLF.

Two earlier runs of the legacy suites failed and were kept. One wrapper passed a keyword argument
before a positional one. Paths inside nested temporary folders ran past Windows' 260-character limit;
a short fixture root with a depth guard under 250 fixed that. The check for flashing console windows
was not conclusive. Three probes saw no new visible console window, but the longest gap between their
samples was 131 to 535 ms, longer than a 100 ms flash, so they do not rule one out. A separate reviewer
kept the candidate on function. The helper records compactions; it does not shrink the native context.

## Checks

- Coverage: compactions in the transcripts against nodes in the index, per runtime.
- Reach: the share of anchors in the recall index and the share with graph edges. Both lag the nodes by
  one index refresh and one graph build; if the graph build stops (on a starved disk, for example), new
  anchors are still saved but get no edges.
- Links: the share of new anchors with at least one related link, by day. A fall means the linker is
  failing or starved.
- The month-old test: ask recall, in ordinary words, about ten pieces of work from a month ago. The
  compaction that did the work should come back with its request, its task and its files.
