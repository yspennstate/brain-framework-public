# Two recall levers, measured

These tests cover deliberate recall (`framework/dreaming/brain.py recall`) over the agents' own memory notes, not the math store. Each question was written to share as few content words as possible with the note that answers it. Scores give the rank of that note.

## Rewriting the question before recall (25-27 September 2026)

For each question a small model (Haiku 4.5) with no tools wrote three search queries "in the words a note answering it would use". Recall ran on the question and on each rewrite, and the score asked whether the source note appeared in any of the four top lists. The predictions were registered before the run.

On 100 questions:

| source found within | question as asked | three rewrites alone | question + rewrites |
|---|---|---|---|
| top 1 | 24 | 23 | 36 |
| top 5 | 30 | 41 | 48 |
| top 20 | 40 | 58 | 65 |

A replication on 300 other questions gave 104 against 132 at top 5 and 144 against 186 at top 20. The gain was about the same on notes older than a month (+10.0 points at top 5) as on newer ones (+8.7). The rewrites cost under a cent a question.

This overstates the lever. "Question + rewrites, top 5" can hold up to 20 notes, against five for the question alone. Two days later the rewrites were merged into one interleaved list and compared at the same number of lines, and the gain disappeared: 105 against 109 at 5 lines (14 lost, 18 gained, p = 0.6) and 149 against 150 at 20. The rewrites do help as a second look. After a plain recall had missed, the rewrites' 20 lines held the note for 25 of the 151 misses. So a rewrite is worth running when the first search fails, not in place of it.

## Embedding the question and fusing with the word lane (19 October 2026)

When this test ran, the meaning lane of deliberate recall took its query from the word lane's seeds and folded the question's own embedding in at a low weight. A question whose words point to the wrong neighbourhood therefore kept the meaning lane there too. The test encoded the question with the store's own encoder (bge-small-en-v1.5), ranked every note by cosine, and fused that list with the BM25 word list by reciprocal rank.

Answer at rank 1, 64 questions:

| lane | rank 1 |
|---|---|
| live recall | 24 |
| word lane alone | 34 |
| word lane + question vector | 46 |

The dense lane alone was weaker than words on older notes (6 of 12 against 7), so it has to be the fusion and not dense alone. The rank-fusion constant mattered. With k = 60, a note that was first on the vector and absent from the word list fell to eleventh. With k = 10 it stayed near the top: over 84 questions, k = 10 put the answer first on 51 against 49. The form that was later staged (its own vector lane at k = 200, fused at k = 10 with the live list) put the answer first on 52 of 80 against 27 for live recall. A third set of 16 questions, worded as symptoms and written after the first two, was harder for everything: live recall rank 1 on 2, the fusion on 4.

As a positive control, twelve random notes' own descriptions ranked themselves first on 10 of 12 by the vector alone, so the encoder and the stored vectors matched.

The caveats are real. One author wrote all the questions, and the first two sets lean towards recent notes. Notes filed since the nightly vector rebuild have no vector, so the word lane has to stay in the fusion. This result is provisional until the fused form is landed in `brain.py` and the three sets are re-run on the landed code.
