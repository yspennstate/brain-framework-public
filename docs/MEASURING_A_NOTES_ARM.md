# Measuring whether a notes arm helps

A "brain arm" serves a solver the same item as the "none arm" plus a few notes retrieved from the
store. Whether the notes help is the question every brain-vs-none run claims to answer, and most of
the ways to get a null that means nothing were found the hard way. This is the order that survives.

## 1. Prove the run is sterile, item by item

The environment flag that turns hooks off is a promise about the launch. The transcript is the
receipt of what the model saw. One run served 829 of 1,200 items with hook injections (reflex text,
guards, compute notices) because hand relaunches lacked the flag; the paired null survived, but only
a third of the run met its own protocol. So: the runner passes the sterile switch by default, the
preflight probe's transcript must carry no hook marker before an arm serves, every served item's
transcript is checked, and a hooked one is rejected and re-served. A missing transcript is not
proven sterile.

## 2. Measure what the notes can speak to, before serving

For each item the key carries retrieval targets: the named results a helpful note would carry.
`b12_notes_relevance.py` reports the share of sampled items whose served notes name at least one
target, by family. A notes arm with 36 percent coverage is a 36 percent treatment, and a null on it
measures the store, not help. Grow the store in the bank's areas first, then serve.

Match on the result's NAME, not the whole target phrase. The first metric wanted "PageRank / Google
matrix" to appear as written and scored an item whose three notes were all PageRank statements as a
miss; every ranking read as 34 to 36 percent and retrieval looked inert. Read six cases the metric
calls misses before believing it.

## 3. Rank semantically, and measure the ranking separately from the store

A claim is written in the problem's vocabulary and the theorem node in the result's. Token overlap
(59 percent coverage) and BM25 with the claim plus three argument steps (61 percent) both let common
tokens outvote the rare ones that name a result; sentence-embedding cosine (68 percent, and 73 once
the store carried the bank's own subjects) put the right statements on top and an eyes-on read agreed.
Growing the store changed nothing under the overlap ranking until the store carried the bank's own
subjects (then 65 percent); measure coverage after every store change AND every ranking change.

## 4. Serve one arm at a time

Throughput peaks at three parallel calls and falls at five (measured on the same host). Two arms at
three workers each produced empty envelopes for 49 items that the runner rejected as an unknown
model. The per-item resume makes that survivable; serving one arm at a time makes it unnecessary.

## 5. Grade with the vocabulary the prompt asks for

The served prompt asks for "valid" or "flawed". A grader that counts anything but "clean" as a false
positive prints 49 of 49 clean items wrong. Read one raw verdict of each truth class before believing
a rate. Full credit is the exact fault step; clean twins report false positives only; the contrast is
McNemar exact on the discordant pairs, and the coverage from step 2 is quoted beside the p-value.

## 6. Expect notes to be a small negative until proven otherwise

Final grades on one research bank (240-item sample, receipts on every verdict): runner-ranked notes at
59 percent coverage scored 2.8 points below no notes (135 vs 140 of 179 pairs, p 0.49); embedding-ranked
notes at 73 percent coverage scored 2.9 points below (129 vs 134 of 172, p 0.46); the two note arms tied
(p 1.0); clean-item false positives 3 of 52 in every arm. A note about the wrong theorem is a
distraction the solver has to read past, and a note about the right theorem can make a wrong citation
or an over-reach look legitimate. Quote the room to fail with the null: none was wrong on 38 of 172
items, so a true +10 points would have shown and a true +5 would not. The exploratory split by fault
mechanism is where the next design comes from: notes lifted the hidden-hypothesis faults (24/43 to
30/44) and cost the citation and scope faults (21/26 to 17/25; 17/19 to 13/17).

## 7. Where the difficulty is

On a 37-family research bank, plain haiku sat near the ceiling on local faults (algebra, constants,
indices, citations) and at 55 percent on hidden-hypothesis faults. That is where a note carrying the
theorem's hypotheses could act, and where a bank's headroom lives; the per-mechanism table is part of
every grade.

## 8. Coverage first, and what fills a gap

Before serving a notes arm, measure name coverage: the share of items whose served notes carry the
name of a result the item needs. A family at zero coverage is a family the arm cannot help, whatever
the ranking. On the research bank ten of 31 families sat under half coverage on embedding-ranked notes
over a store that already held the encyclopedia harvest, and the names they needed (a correlation
inequality under its two authors' names, a theorem known by the order it concerns, a recent law named
after its authors, a first-moment bound by year) do not appear in an encyclopedia lead paragraph. A
second harvest batch would not have carried them. What did was a small hand-authored bundle of
concept nodes, each a statement with its hypothesis list and its limits, under an in-house license so
no open-license claim is made about the wording: twelve nodes took BM25 name coverage on the same
frozen sample from 0.64 to 0.84 and every weak family to full coverage.

Two things to keep from that. First, the hypothesis list is the shape the store lacked, and it is the
shape the one fault with headroom needs; harvest leads describe results, they rarely state the
conditions. Second, rankings are not interchangeable: the embedding ranking lost names the lexical
ranking had (three families went from full to zero when the ranking changed), because a name is found
lexically and a neighbourhood is found by embedding. Test a hybrid before choosing either alone, and
pre-register that arm before serving it.

## 9. Framing the notes as hints, and serving fewer of them, measured

Two cheap levers were pre-registered against the embedding-ranked arm on the same frozen sample and
served one at a time: a framing sentence as the first note of every item (a note is a claim about a
result, not evidence that the argument used it correctly) and a single note instead of three. Neither
moved the solver. Framing scored 129 against 131 of 175 paired faulted items (minus 1.1 points,
McNemar p 0.87) and 131 against 141 of 178 against no notes at all (minus 5.6 points, p 0.11); the two
pre-registered mechanism cells - the dropped-hypothesis faults where notes help and the citation, scope
and sharpness faults where they hurt - stayed where they were (30 vs 28 of 42; 33 vs 34 of 52). One note
instead of three read plus 3.4 points against three notes (p 0.38) and plus 0.6 against none. Clean
false positives stayed at two or three of 52 in every arm.

So a hint line does not repair the harm a note does when it makes a wrong use of a result look
legitimate, and fewer notes do not help either; the harm is in what the note says, not in how many or
how they are framed. Nor does the ranking: BM25 notes on the grown store, which name the needed result
for 0.84 of the items where the embedding notes managed 0.73, scored 134 against 132 of 176 paired items
(p 0.86) and 136 against 140 of 179 against no notes (p 0.59). Four arms, four nulls, all with the same
slight lean below no notes. That leaves content shape - a hypothesis list served as a checklist, on nodes that
carry one - as the lever still standing, and it is the one to pre-register next. Read a null on a
framing arm as evidence about the solver's use of notes, not as a reason to drop the hint from the
notes people read: the hint is for the reader who weighs, and the solver here did not weigh, it used.
