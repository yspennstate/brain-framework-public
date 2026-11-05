# Context compaction as part of the memory

A long agent session eventually fills its context window, and the harness replaces the conversation with a summary.
That summary is the session's own account of what it was asked, what it finished, what it still owes and what it was
about to do next. Thrown away, it is the most expensive record a brain can lose, because the next session has to
rebuild it from files and guesswork. Kept and linked, it lets the same agent, or a different one, resume the work.

This note covers four things: when to compact, what the summary has to keep, how each compaction becomes a node of the
graph, and how to measure what compaction saves. [everything-is-a-node.md](EVERYTHING_IS_A_NODE.md) explains why every
compaction is a node at all; this note is the procedure.

## 1. Why the window setting matters

Every model call re-reads the whole context, so the cost of a turn grows with the size of the context. A session that
carries 600k tokens pays for 600k tokens on every turn, mostly as cache reads. Compacting earlier makes turns cheaper,
but only up to a point.

After a compaction a session does not start empty. It carries its instruction files, the memory index, whatever its
start-up hooks print, the tool list and the summary itself. Measure that baseline before choosing a window: it is the
input of the first model call after a compaction (input plus cache creation plus cache read in that call's usage). On
the machine this brain runs on it was 140k to 160k tokens in October 2026.

A window inside or just above the baseline makes a session compact every few turns. With a 200k window, headless
sessions compacted every 15 messages or so (184 compactions in 2,843 messages) and paid more per message than
interactive sessions at 300k, which compacted every 56 messages (552 in 31,113): about 50k input-equivalent tokens per
message against 43k, before counting the compaction calls themselves. In the worst case the harness gives up with
"Autocompact is thrashing". The rule: the window must sit well above the baseline. The working setting here is 300k
for both Claude Code (`autoCompactWindow` in settings, or `CLAUDE_CODE_AUTO_COMPACT_WINDOW`) and Codex
(`model_auto_compact_token_limit`).

A running session reads the window when it starts. Changing the setting does not move a session that is already
running; it keeps compacting at its old threshold until it is restarted or compacted by hand.

## 2. Compact at the end of a task, not at the edge of the window

A compaction at a task boundary loses less than one in the middle of a task, because the finished task can be
summarized in a line and its working detail is already in files and commits. Signals that a task has ended: a task
entry closed, a final report written, a push or a commit, a lease released, a handoff sent, tests passing, a
checkpoint written, a change of subject.

An agent in an interactive session cannot run `/compact` on itself, and no hook output can trigger a compaction. What
works is a Stop hook that, when the turn closed a task and the context is past a size threshold (150k here), starts a
small windowless helper. The helper waits until the session is idle and then types `/compact` into that session's own
console input buffer, never into the focused window. Idle is stricter than it looks:

- the last assistant turn has ended;
- no queued prompt or scheduled loop firing comes after it (a new turn shows first as a queue record, and the user
  record reaches the transcript only with the first reply);
- there has been no keyboard or mouse input for 15 seconds, so the keys never land in text a person is typing.

A `/compact` typed into a busy session is not lost; the harness queues it and runs it at the next turn boundary. The
helper gives up if a new prompt arrives while it waits, and fires at most once per 20 minutes per session.

Headless workers compact best by ending. Give each session one task, have it write a NEXT line, and seed the next
session from that line.

## 3. What the summary must keep

Claude Code passes the output of a PreCompact hook to the summarizer as instructions. The instruction used here:

> Keep in the summary, word for word where they appear: the person's open requests with their times and task ids;
> every id, commit hash, file path and card or item id the work depends on; what is done and verified versus still
> open; the NEXT line if one was written; leases held and peer messages owed an answer; the agent's own id. Drop
> finished subjects' tool output and anything already saved to a file, a memory or a commit, and name where it was
> saved instead. Never copy test questions, answer keys or item text into the summary. Restrictions keep their scope
> words ("for the night", "until he says", "this session").

Two points in it come from failures. Scope words matter because a restriction that loses "for the night" becomes
permanent. Test material is excluded because a summary can end up in a pushed file; a summary that mentions the test
bank keeps its full text only in local storage, and its anchor carries no excerpt.

The summary is still a bounded probe of the session's past. Before an agent says it did not do or produce something
that predates its last compaction, it searches the files the work would have left (output names, scratch folders,
the transcript). The summary drops runs the files remember.

## 4. Every compaction becomes a node

The PostCompact hook receives the summary text. It writes the compaction twice.

- **The full node**: the whole summary under `compactions/<date>/<name>.md` in the graph store, with one row per node in
  `INDEX.jsonl`. This is brain data and is never deleted.
- **The anchor**: a short memory of the same name in the memory folder, holding a bounded excerpt (the request, the
  pending tasks, the current work, the next step, about 3,600 characters) and a pointer to the full node. The anchor
  is what makes the compaction reachable: lexical recall indexes the memory folder, and the graph builder turns the
  anchor's links into edges. Anchors get no line in the always-loaded memory index.

Each anchor links to:

- the compaction hub, one node that every compaction hangs from;
- the session's previous compaction (`previous`), so a session's compactions form a chain;
- the open tasks of the session's agent on the task checklist, and other sessions' compactions on the same tasks;
- related memories, found by recall and kept only when they pass a rare-word gate (below).

The hook then tells the session the name of its node, so the agent can read back what the summary dropped.

**Related links need a gate.** Taking recall's top five results as "related" produced many off-topic links. Two
changes fixed most of it. First, the query and the gate read the summary with the session plumbing removed: identity
blocks, standing constraints, launcher text and agent ids appear in every summary and say nothing about its subject.
Second, a candidate memory is kept only when the node holds enough of the memory's distinctive words: the score is
the share of the memory's rare words (weighted by log inverse document frequency over the memory folder, with the
word table rebuilt daily) that also appear in the node, and the cut is 0.03. On twelve nodes judged blind, 43 of the
52 links kept were on topic and 6 off, against about 33 of 60 on topic for recall's top five alone. The gate also
drops about two in five on-topic links, so a refill pass asks recall a second way and fills the remaining places
through the same gate. A shared rare word can still be a false friend.

**Robustness.** The hook reads its input as UTF-8 bytes and replaces a lone surrogate with U+FFFD; one compaction was
lost to a console code page before that change. Every error is swallowed and logged, because a memory hook must never
break a compaction. The hook runs in process inside the hook dispatcher; a hook that reads `sys.stdin.buffer` must
fall back to `sys.stdin.read()` there, or it fails silently.

## 5. Backfilling the compactions the hook missed

A sweep reads the transcripts and turns every compaction without a node into one. In Claude Code transcripts a
compaction is a `compact_boundary` record, and the summary is the next user record. The sweep keeps a byte cursor
per file, so repeat sweeps are cheap, and runs in the background at most every 15 minutes, one sweep at a time.

Deciding whether a transcript compaction is already saved has to be done by content, not by time. Matching on "any
saved node of the same session within ten minutes" drops the second and third compaction of a busy session; one
backfill found 1,012 compactions missing out of 1,764 that way. The content rule: measure the overlap between the
saved summary and the stretch of transcript it spans. A node's own compaction scored at least 0.811 (median 1.00);
the 3,070 other compactions of the same sessions within 30 minutes scored at most 0.730 (99th percentile 0.53). The
cut is 0.8, inside that gap.

Subagent compactions can arrive with the parent's session and no agent id, so the sweep also looks for each
unattributed summary in the session's agent transcripts and refiles it under the agent that wrote it.

## 6. Runtimes without a compaction hook

Codex has no compaction hook, and in the build used here its compaction record (`"type": "compacted"`) has an empty
message: the model's summary is stored encrypted. Two things still work. The sweep saves a metadata node for each
Codex compaction (event hash, window number, token counts, session and task links). And an agent can save a
summary it writes itself as a checkpoint node: open requests, task ids, commits, paths, leases and a line beginning
`NEXT:`, which the tool requires. Node names are content-addressed, so a retry after a crash writes nothing new.

Thread ids in Codex are UUIDv7, whose first eight hex digits are the top of a millisecond clock and change only every
65.5 seconds. Threads started within a minute share them, so a node must carry the full id.

## 7. After the compaction

Two hooks keep the work moving.

- After a compaction, agents tend to write a recap and stop although their tasks are still open. A Stop hook that
  returns `{"decision": "block", "reason": ...}` once per compaction sends them back: it lists the session's open
  task entries and lets the agent stop with one line if everything is done or waiting. It never fires while
  `stop_hook_active` is set, and only for a compaction near the end of the transcript and under six hours old.
- Start-up hooks that run again after every compaction add their full cost each time. A slow orientation step (it took
  up to 6.5 minutes here) should serve its last output again after a compaction instead of being re-run.

## 8. Measuring what compaction saves

Claude Code writes `preTokens` and `postTokens` on each `compact_boundary` record. `preTokens` agrees with what the last
model call before the boundary read. `postTokens` does not describe what the next call reads: it counts roughly the
summary and the fixed part, about 19k, while the first call after a compaction reads far more. A before-over-after
ratio built from these two fields overstates the saving by about a factor of nine.

Measure both sides from the assistant messages' usage instead (input plus cache read plus cache write), deduplicated
by message id, because each API call is logged once per content block, and with sidechain records skipped. Over
1,256 transcripts and 8,617 compactions (8,536 measurable), the medians were:

| | tokens |
|---|---|
| last model call before the compaction | 264,360 |
| `postTokens` recorded by the harness | 18,980 |
| first model call after the compaction | 166,019 (cache read 33,224, cache write 133,525) |

The median ratio of the call before to the call after was 1.52 (1.63 for automatic compactions, 1.25 for manual ones).
Most of what is re-read after a compaction is fixed context, so shrinking the fixed context is the other lever, and
unlike compacting more often it loses no working detail. A cost estimate has to price the re-read tokens as
cache reads and writes, at their own rates.

## 9. Checks

- **Coverage**: compactions in the transcripts against nodes in the index, per runtime. Every compaction should have a
  node, and a node should exist before the agent's next turn.
- **Reach**: the share of anchors that appear in the recall index and the share that have graph edges. These lag the
  nodes by one index refresh and one graph build. If the graph build stops (on a starved disk, for example), new
  anchors keep being saved but get no edges, and nothing else reports it.
- **Links**: the share of new anchors with at least one related link, tracked by day. A fall means the linker is
  failing or starved, not that the sessions changed.
- **The month-old test**: take ten pieces of work from a month ago and ask recall about each in ordinary words. The
  compaction that did the work should come back with its request, its task and its files.
