# Keeping what a compaction throws away

A long agent session runs out of context and is compacted: the harness replaces the conversation with a summary
and the agent carries on from that. The summary is written once and is usually lost when the session ends. It is
also the densest record the session will ever produce: what the agent was asked, what it did, what failed and how
it was fixed, and what comes next.

We now keep every compaction summary as a node in the brain.

## What is stored

Each compaction becomes one file under a `compactions/` folder in the brain's graph, plus a line in an append-only
index (`INDEX.jsonl`) with the session id, the agent, the time, and the task ids the summary names. An anchor note
in the memory folder links the node to:

- a hub node for all compactions, so recall can reach the set as a whole
- the same session's previous compaction, so a long session reads as a chain
- other sessions' compactions on the same open tasks
- the memories whose text is closest to the summary

Because the anchor sits in the memory folder, ordinary recall finds compactions the same way it finds memories.
A question like "what did we try on the render cache last week" can return the compaction that recorded it, even
when nobody wrote a memory at the time.

## What it fixes

- A summary can drop something the agent needed. The agent is told, right after compacting, where its node is and
  that the full summary is there, so it reads the node instead of redoing the work.
- A new agent on an old task can read the chain of compactions for that task, in order, instead of starting from
  the code.
- Several agents on one task can see each other's summaries without sending messages.

## Hooks around a compaction

Three small hooks make compactions cheaper and safer:

1. **Before.** At a large context size, the agent is told to finish its current step and write its next-step line
   to disk, so the automatic compaction lands at a boundary and not in the middle of an edit.
2. **After.** The first message after a compaction tells the agent to read its next-step line, its open checklist
   entries and its inbox before acting, and to trust what those record as done.
3. **At stop.** If an agent compacted and is about to stop with tasks still open under its id, a stop hook names
   them and asks it to continue, or to say in one line what each one is waiting on.

The third one matters most in practice. An agent that has just compacted often reads its own summary as a final
report and stops, with work still open.

## Measured over one week (to 28 October 2026)

By the morning of 28 October the brain held 12,613 compactions, the first saved on 9 October. Every one had its
node file, 12,612 had an anchor note, and 12,515 were already in the recall index (the rest were the last two
hours, waiting for the next index refresh). The anchors carried 57,110 links between them, about 4.5 each, and 94
percent were linked to the same session's previous compaction.

When compactions happen, over the last seven days:

| | Count |
|---|---|
| Claude, compacted right after a task closed | 2,732 |
| Claude, forced at the context limit | 1,680 |
| Codex, forced (no compaction hook) | 488 |

The task-end case is a helper that waits until the session has closed a task, has been idle for 8 seconds, and
the user has not typed for 15 seconds, then runs the compact command itself. It started 3,239 times that week,
compacted 2,623 times, queued 246 behind a running turn, stood aside 339 times because a new prompt had arrived,
and gave up 3 times. The median context at that moment was 215,000 tokens. Two signs it was not thrashing: the
median gap between one session's compactions was 27 minutes after a task-end compaction and 52 minutes before a
forced one, and only 5 forced compactions came within 15 minutes of a task-end one.

The weak spot was sessions on a ten-minute loop. They close a task every tick at over 150,000 tokens, so 453
task-end compactions came less than 15 minutes after the previous one, and the busiest loop sessions compacted 230
to 260 times in the week. Each of those saves little and costs a summary pass. A higher token floor for loop
sessions, or compacting a loop only every third closed tick, removes most of them.

The link that fails first under load is the one to related memories. At save time the anchor asks recall for the
closest memories under a 25-second limit. With the disk nearly full and file opens taking 40 to 350 ms, recall
stopped answering in time and the share of new anchors with a topic link fell from 40 to 75 percent a day to under
10 percent. A sweep fills these in later. It has to run at normal priority: a pass at low priority with a short
limit linked nothing, because its recall never got the processor, while a pass at normal priority with 240 seconds
a recall brought one day's anchors from 8 to 82 percent linked.

## Building it for your own brain

Most harnesses let a hook run on the compaction event or on the session start that follows it. Copy the summary
out at that moment, write the node and the index line, and link the anchor with your brain's ordinary edge-writing
code. Never delete the nodes: they are raw material for the nightly consolidation, and the summary of a session
that has since ended exists nowhere else.
