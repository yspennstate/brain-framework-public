# Everything is a node

This is the one rule a brain is built on. Every thing the brain's people and agents produce, say, read or touch becomes a node, and every relation between two things becomes an edge. Prompts, answers, context compactions, memories, mistakes, files, folders, scripts, functions, keywords, papers, proofs, failed attempts, audits, tasks, datasets, tests, dreams: all of them. A thing that is not a node cannot be recalled, cannot fire as a warning, cannot be linked to the next thing that resembles it, and is lost the first time the context that held it is cleared.

The other build notes say what the parts of a brain are ([build-your-own-brain.md](https://github.com/yspennstate/math-brain-public/blob/main/docs/build-your-own-brain.md)) and how to stand one up in a day ([brain-in-an-afternoon.md](https://github.com/yspennstate/math-brain-public/blob/main/docs/brain-in-an-afternoon.md)). This note says what goes into it and how it is wired. Read it before writing the first import script, because the most common way a brain fails is not bad search. It is a store that holds the facts someone remembered to write down, while the prompts, the compactions, the mistakes and the files that produced them stay outside, in logs nobody searches.

## 1. Why everything

An agent working on a problem needs three things from its past: what is known, what was tried, and what went wrong. Only the first of these is usually written down as a fact. What was tried lives in old transcripts and compaction summaries. What went wrong lives in corrections a person typed in the middle of a session, in a commit that reverted another commit, in a test that failed once. If those stay outside the store, the brain answers "what is true" and is silent on "we have been here before, and it did not work", which is the question that saves the most time.

There is a second reason. A link is only as good as the two things it joins. A mistake card that says "this happened while editing the upload script" is worth much more when the upload script is itself a node, so that the next agent who opens that file is shown the mistake before it edits. Recall by meaning finds similar text; recall by edges finds the things that were actually involved. The second kind needs the files, the prompts and the sessions to be nodes too.

The test of a brain built this way is simple: pick any piece of work done last month and ask the brain about it in the words a person would use. It should return the request that started it, the session or compaction that did it, the files it changed, what came out of it, and any mistake made along the way, each with its links to the others.

## 2. The kinds of node

Each row is a kind of thing that should become a node. The right-hand column is where it usually comes from, so that nothing is left for a person to remember to file by hand.

| Kind | What one node is | Where it comes from |
|---|---|---|
| request | one thing a person asked, in their own words, with the time | every prompt, captured by a hook as it is typed; old prompts from transcripts |
| answer | what the agent reported back for one request | the agent's final message of the turn |
| compaction | the summary written when an agent's context was compacted | the compaction event, captured as it happens; old ones from transcripts |
| session | one working session of one agent | the session start and stop events |
| memory | one durable fact, rule, method or result | written by the agent at the moment it learns it |
| rule | an instruction from a person that holds from now on | a request that sets a standing order, filed as its own node |
| mistake | one thing that went wrong: what happened, the evidence, the rule that would have prevented it | corrections from a person, failed tests, reverts, audits |
| reflex | a compiled trigger that fires a mistake or a rule when its pattern appears | compiled from mistakes and rules, nightly |
| mechanism | the shared cause behind several mistakes | written when a mistake repeats; it is the hub the mistakes hang from |
| claim | a theorem, lemma, definition, conjecture, counterexample or open problem | papers, proofs, the agents' own results |
| proof | the argument for a claim, with its verification state | the agent or the paper that produced it |
| attempt | a route that was tried on a problem, including the ones that failed | the agent's working notes and compactions |
| paper | a cited or written work | citations, downloads, the papers the work produced |
| file | one file, by path, with its content hash | every file an agent reads or writes |
| folder | one directory | the file nodes beneath it |
| code | a script, module or function, and what it does | the code tree, at the function level for the important parts |
| term | a keyword, symbol, exact name or identifier | extracted from every other node's text |
| task | one entry on the task list: who took it, when, its state | the task list, as entries are opened and closed |
| audit | one audit or review and its findings | the audit reports |
| test | one test question, one run, one result | the test harness |
| dataset | one body of data and where it lives now | every upload, move or backup |
| dream | one entry of the nightly consolidation pass | the dream's journal |
| forecast | a prediction made before a result was known, and whether it held | the dream and the agents |

Two kinds deserve a word. **Requests** are the most valuable and the most often forgotten. They are the only record of why any of the other nodes exist, and a person's later words overrule earlier ones, so the brain must know the order in which things were asked ([prompts-as-nodes.md](https://github.com/yspennstate/math-brain-public/blob/main/docs/prompts-as-nodes.md) gives the ledger). **Compactions** are the second. When a long session's context is compacted, the summary is the session's own account of what it was doing, what it finished and what it still owed. Kept as a node, it lets the next session, or another agent entirely, pick up the work instead of redoing it. Every compaction becomes a node the moment it is written, and the compactions already sitting in old transcripts are backfilled once.

## 3. What a node carries

A node is a card that a reader can understand on its own, with the fields that let a machine rank it, link it and know how far to trust it.

```json
{
  "id": "mistake_7f3a9c21",
  "kind": "mistake",
  "title": "A retry rebuilt a shared file on a newer tip and overwrote a colleague's edit",
  "body": "What happened, the evidence, and the rule: on a retry, refuse if a named path changed between the first tip and the new one.",
  "fire_when": "pushing a file that other agents also edit",
  "status": "verified",
  "created_at": "2026-10-24T22:48:39+03:00",
  "source": "session 4c1e..., request req_9b2e..., commit f680d9e",
  "hint": "verified on a control repository, both directions",
  "links": [
    {"to": "file_push_paths",          "rel": "about"},
    {"to": "mechanism_stale_base_write", "rel": "instance_of"},
    {"to": "req_9b2e41c07d1a",         "rel": "produced_by"}
  ]
}
```

- **id** is stable and never reused. For text that can arrive twice (a prompt, a compaction), derive it from a hash of the normalized text, so the same thing seen twice is one node with a count of two.
- **kind** is one of the kinds above. A store that refuses unknown kinds keeps itself clean; add a kind on purpose, not by accident.
- **title and body** are written for a reader who has never seen the work. A card that only makes sense next to its neighbours will be misread when it is shown alone, and it will be shown alone.
- **fire_when** says in plain words when the node should be put in front of an agent. It is what turns a stored fact into a warning.
- **status** is one of verified, provisional, superseded, or set by a person. Every node a hook serves carries its status, because a note is a hint to weigh, not an order to obey.
- **source** says where the node came from, so any claim in it can be checked against the thing it summarizes.

## 4. The kinds of edge

Edges are typed. A plain "related to" link is better than none, but a typed edge tells recall why two things are joined, and lets a walk follow only the relations that matter for the question.

| Edge | From and to | Made by |
|---|---|---|
| asks_about | request to any node | the agent, when it starts on a request |
| produced | request or session to anything written during it | the stop hook, automatically |
| answered_by | request to the node that answers it | the agent or the dream, as a judgement |
| follows | request to the request it continues | the prompt hook, for short follow-ups |
| in_session | any node to the session it came from | automatically |
| previous | compaction to the session's previous compaction | automatically |
| about | mistake, memory or audit to the file, code or term it concerns | at write time |
| instance_of | mistake to its mechanism | the writer, or the dream when a pattern repeats |
| triggers | reflex to the mistake or rule it fires | the nightly compile |
| supersedes | new node to the node it replaces | the writer; the old node is kept and marked |
| cites, proves, refutes, depends_on, uses | between claims, proofs, attempts and papers | the agent, from the mathematics |
| defines, mentions | node to term | automatic term extraction |
| contains | folder to file, file to function | automatically, from the tree |
| located_at | dataset to its storage place | every upload or move |
| co_used | any two nodes used in the same stretch of work | learned from use (section 5) |

**Nothing is deleted.** A memory that turns out to be wrong gets a `supersedes` edge from its replacement and keeps its place. The next agent needs to know that the old answer was believed once and why it was dropped; a deleted wrong answer is a wrong answer someone will rediscover.

## 5. How edges get made

Edges come from four places, and a brain needs all four.

1. **At write time.** The agent that writes a node links it to what it knows is involved: the request it was working on, the file it changed, the mechanism a mistake belongs to. This is the most precise source and the easiest to skip, so the writing tool should ask for it.
2. **From structure, automatically.** Some relations need no judgement. A node written during a session is in that session. A compaction follows the session's previous compaction. A file sits in a folder. A node whose text names a file path, a function or an exact identifier gets a `mentions` edge to that term's node. These edges are added by the hooks and the import scripts, never by hand.
3. **From use, by learning.** When an agent opens a folder, then reads a file in it, then thinks a certain keyword, the nodes for all three light up together. Each time two nodes are active in the same stretch of work, the `co_used` edge between them gets a little stronger; edges that are never used again decay. This is the Hebbian rule, and it is what lets the brain learn associations nobody wrote down: that a certain error message tends to come with a certain config file, or that two lemmas are usually needed together. Log every activation (a node recalled, opened, fired or written) with the session and the time, and update the weights from that log in a scheduled pass rather than inside the hook, so that a busy session is not slowed down.
4. **By the dream.** The nightly pass links the day's new nodes to the hubs they belong to, merges near-duplicates into one node with both sources, writes a mechanism node when the same kind of mistake appears a second time, compiles reflexes from mistakes and rules, and grades a sample of yesterday's fires so a reflex that keeps firing in the wrong place gets quieter.

**Hubs** are ordinary nodes with many edges: one per mechanism of failure, one per topic, one for all compactions, one for where the data lives. They are what a graph walk passes through to get from a new question to an old answer in two steps. A node with no edges at all is an orphan. Orphans cannot be reached by a walk and are usually a sign that an import skipped its linking step, so the dream counts them every night and reports the count.

## 6. The pipelines

Each source is captured live, as it happens, and backfilled once from its history. Live capture is done by hooks, so that no one has to remember it.

| Event | Hook | Nodes and edges written |
|---|---|---|
| a person types a prompt | prompt hook | a request node (or a count on the existing one); `follows` for a short follow-up; terms extracted |
| the agent stops | stop hook | an answer node; `produced` from the request to every node written in the turn |
| the context is compacted | compaction hook | a compaction node with `in_session` and `previous` edges; an anchor in the memory index linking it to the compaction hub and to the open tasks it names |
| a file is read or written | tool hook | an activation record; a file node if it is new; the co-use log |
| a correction or a failed test | the agent, prompted by a hook | a mistake node with `about` and `instance_of` edges |
| a task is opened or closed | task hook | a task node with links to its request and its result |
| data leaves the machine | the upload tool | a dataset node with `located_at` and a hash |
| the nightly pass | the dream | merges, hubs, reflexes, decay of unused co-use edges, the orphan count |

For backfill, the order matters, because the later steps link to what the earlier ones made:

1. **Files and code first.** Walk the trees that matter and write a node per file and per folder, with the content hash, and per important function. These are the anchors everything else will point at.
2. **Requests.** Pull every prompt from every transcript the agents ever wrote, oldest first, with timestamps. Dedupe by hash. Mark the ones that set standing rules as rule nodes, and link each later rule to the earlier one it changes.
3. **Compactions.** Pull every compaction summary from the same transcripts, write each as a node, and chain each session's compactions with `previous`.
4. **Memories.** Import the existing notes, one node per fact, and link each to the request, session and files named in it.
5. **Mistakes.** Read the corrections in the requests ("that is wrong", "I never said that", "stop doing this") and the reverts in the version history. Each becomes a mistake node; group them into mechanisms.
6. **Papers, results and claims.** Import the domain records last, linked to the attempts and sessions that produced them.
7. **Terms.** Run the term extraction over everything, then the first co-use pass over whatever activation history exists.
8. **Check.** Count nodes per kind and orphans per kind, and run a recall test (section 8). Fix the import that left orphans before going on.

Two rules for every import. Keep anything used to grade the brain (answer keys, held-out questions) out of every corpus it can search. And keep private material out of any edition you publish: names, home paths, account details and anything a person marked private stay in the private store; the public copy is built from it by a scrubbing step and scanned before release ([privacy-boundary.md](https://github.com/yspennstate/math-brain-public/blob/main/docs/privacy-boundary.md)).

## 7. Recall over the graph

With everything a node, recall has three signals to combine: shared words (BM25), shared meaning (a sentence encoder), and the edges. Search by words and meaning first, then walk one or two steps along typed edges from the best cards: from a mistake to its mechanism and the other mistakes of that mechanism, from a request to what it produced, from a file to the mistakes made in it. Print whole cards with their ids and their status, and say plainly when nothing fits well, so the agent rewords instead of answering from a poor match. In our tests the whole-card format and several wordings in one call made the largest gains ([build-your-own-brain.md](https://github.com/yspennstate/math-brain-public/blob/main/docs/build-your-own-brain.md), section 3); a deeper walk mostly re-ordered cards the words had already found, so one or two steps is enough.

The hooks use the same graph in the other direction. Before an agent acts on a file, the hook looks up the file's node and serves the one or two mistakes most strongly linked to it. Before it answers a request, the hook checks whether the same request was asked before and what it produced. At most two notes per action, each with its status, and a note that keeps firing in the wrong place is graded down by the dream.

## 8. How to know it is working

- **Coverage.** Every kind in section 2 has nodes, and the count grows each day work is done. A kind whose count is flat while work goes on has a broken pipeline.
- **Orphans.** The orphan count per kind is small and falling. A spike after an import means the import skipped its links.
- **The month-old test.** Take ten pieces of work from a month ago. For each, ask the brain in ordinary words what was done and why. Score whether it returns the request, the session or compaction, the files and the outcome. This catches missing pipelines faster than any count.
- **Fire controls.** For each reflex, one case where it should fire and one where it should not, both from real work. A reflex that fails either is fixed or retired.
- **A controlled test.** Agents with the brain, agents with the same documents and no brain, and the model alone, on the same questions, with timeouts counted as wrong ([TESTS.md](https://github.com/yspennstate/math-brain-public/blob/main/TESTS.md)).

## 9. A starting checklist

1. Choose the node kinds for your field from section 2, and the edge kinds from section 4. Start with requests, compactions, memories, mistakes and files even if your domain records are elsewhere.
2. Give every node the fields in section 3, including `fire_when` and `status`.
3. Install the hooks in section 6 so that capture is automatic from today.
4. Backfill the history in the order of section 6.
5. Schedule the nightly pass: merges, hubs, reflexes, co-use update and decay, orphan count.
6. Run the checks in section 8 after the backfill and once a week after that.

The field starter in [examples/field_brain_starter/](https://github.com/yspennstate/math-brain-public/blob/main/examples/field_brain_starter/) already writes requests as nodes and links what each turn produced; it is the quickest place to see the rule at work before applying it to everything else.

In this framework three documents carry parts of the method in more detail: [keyword-topic-nodes.md](keyword-topic-nodes.md) for terms as nodes, [mistake-first-recall.md](mistake-first-recall.md) for mistakes and the reflexes they compile into, and [MEMORY_CONVENTIONS.md](MEMORY_CONVENTIONS.md) for the memory card format.
