# Ten more Brain tests of investigation and continuity

Evidence review, 6 November 2026. Companion to [the earlier Brain evidence review](https://github.com/yspennstate/math-brain-public/blob/c30a1258466eeda05abd5d99963603d2e47e9610/docs/brain-evidence-review-2026-11-06.md).

The Brain supplies persistent records and retrieval tools to a model-driven agent. These ten additional evaluations show where that helps: discovering difficult evidence under a fixed budget, resuming work across sessions, finding available facts with less search effort, and completing a chain that depends on recorded revisions. They also show failures. Larger embeddings did not improve the registered primary outcomes, a reranker made retrieval worse, and adding transcripts initially degraded ordinary retrieval.

**Date scope:** these are completed evaluations reported on 3–6 November, including substantial older tests from 3 November. Several reports dated 4 November locally have 3 November UTC records. This is not a strict last-48-hours list or an exhaustive ranking of the largest experiments. It combines eight recent retrieval, engineering and workflow evaluations with two older investigation and continuity tests. None of the ten was summarized as a test in the earlier review.

These are **source-checked producer reports, not fresh independent reruns**. Counts and caveats were checked against retained reports, protocols and available aggregate scoring outputs. Paired-test arithmetic was independently recalculated where disagreement counts were available. Private questions, answer keys, transcripts and row-level records are not published here. The review did not independently audit every raw event or subjective grade, and some run artifacts remain available only to their producers. Public readers cannot reproduce the scores from this document alone.

The ten entries are not ten independent confirmations of one claim. Several use related banks, the same retrieval implementation or shared graders. Scores measured in points, retrieved records and completed tasks must not be pooled. Retrieval-component tests are distinguished from agents executing work.

## 1 Same information available through the Brain or file search

**Run:** 5–6 November. **Size:** 42 work-memory questions, three arms. **Source:** S1.

Codex with the recall tool answered **40/42** correctly; an arm using grep and file reads over a frozen copy of approximately 53,000 memory notes answered **42/42**; the model-alone arm answered **1/42**. The files arm had the facts, so this is a more useful comparison than withholding the records from it.

The Brain arm's median wall time was **358 seconds**, against **436 seconds** for files; median uncached input was approximately **9.6k tokens**, against **17.7k**. These are about 18% less elapsed time and 46% less uncached input at the reported medians. They are not monetary savings.

Both Brain misses were reported recall/tool timeouts under severe memory pressure, after which the agent guessed. The final accuracy comparison has an important budget qualification: the usual cap was 600 seconds, but three late reruns used 1,200 seconds, including two files-arm successes after initial timeouts. One of those reruns needed 659 seconds. The final 40-versus-42 score therefore does not establish an equal-cap accuracy advantage for file search, and the study does not establish a Brain accuracy advantage either.

The grader was also corrected to accept specified equivalent numeric formats; its reported checks were 87/87 positives and 0/87 negative accepts. These checks do not replace independent adjudication of the task answers.

**What it teaches:** organized retrieval can reduce the work of finding an available fact. Reliability and honest handling of a failed tool remain part of the system's performance. This bank did not establish an advantage on multi-note reasoning.

## 2 Older work history and local investigation

**Run and report:** 5 November. **Size:** 29 questions before exclusion, 28 valid pairs. **Source:** S2.

A preregistered pilot used one Codex session per question, the same model and a 300-second budget. The Brain arm could retrieve prior records as well as inspect live files and transcripts; the comparison arm had files and transcripts.

The headline report gives **29/29 points with the Brain versus 19.5/29 without**, allowing partial credit. One comparison row read a disallowed memory copy and was excluded. The valid paired result is therefore **28/28 versus 19.5/28**, with 11 questions favoring the Brain and none favoring the comparison. The corresponding two-sided sign-test value is approximately **0.00098**. The headline 12-to-0 comparison and p = 0.0005 include the excluded row and should not be substituted for the valid paired result.

The benefit was concentrated in older history that the local transcripts did not contain. On nine local-era/investigation questions, the original scores were **9 versus 8**. Reported median execution time per question was **20 versus 75 seconds**, but waiting for memory availability before a session began was excluded from those timings.

The first two passes were discarded after answer-key exposure through transcript search. The scored pass used corrected search exclusions and an output-based access audit. One grader, who also wrote the bank, graded answers with arm labels hidden. Questions were written from memory cards, and the Brain arm reached the ceiling.

**What it teaches:** persistent history helps an agent use work that is absent from its current files. This is partly an information-access comparison, not a clean test of superior reasoning over the same records.

## 3 Difficult investigation across computers on 113 questions

**Date: 3 November, outside the recent two-day window. Source: S3.**

Agents had to discover recorded results described in language unlike their source records. Both arms could access the underlying information: one through Brain retrieval, the other through ordinary search over the repository. The registered six-command, 600-second comparison produced:

- **Brain: 30/113 correct**, nine wrong answers, 74 abstentions.
- **Search: 8/113 correct**, two wrong answers, 103 abstentions.
- **24 Brain-only successes versus two search-only successes**, exact paired p = .00001049.
- Mean time: **109 versus 105 seconds per question**.

This is a meaningful improvement in discovering and completing answers under the measured budget. It is also important that the Brain gave more wrong answers. Conditional on answering, accuracy was **30/39, about 77%, versus 8/10, 80%**. The gain was reach, not demonstrated precision.

An earlier two-command run on the same bank scored only **6 versus 4**, with p approximately .73. It is a budget variation on the same questions, not another independent bank. Later hand adjudication changed the six-command counts to **32 versus 9**; the registered **30 versus 8** remains the primary result here. The runtime snapshot and launch condition changed from the registration, with the departures disclosed before grading.

The comparison concerns investigation and evidence discovery. It does not show that the Brain independently plans better research or verifies the records it finds more accurately.

## 4 A two session workflow test on 14 paired tasks

**Date: 3 November, outside the recent two-day window. Source: S4.**

This is the clearest additional continuity test. A first session produced artifacts and decisions. A fresh session then had to complete the workflow after the original inputs were removed. Final outputs were graded.

- Overall: **Brain 12/14 versus control 4/14**.
- History-dependent tasks: **8/10 versus 0/10**.
- Local-policy controls: **4/4 in both arms**.
- Eight Brain-only successes and none for the control give **p = .0078125**.

The result demonstrates that persistent history can be recovered and used to complete work across a session boundary. It also shows that the memory-enabled arm passed the controls rather than indiscriminately applying old decisions.

However, the history tasks deliberately withheld required prior decisions from the control packet. This is an **unequal-information memory-use comparison**, not a test against ordinary retrieval over the same historical evidence. The Brain was enabled in both stages, so the result does not isolate better first-stage work from better second-stage recovery. It involved one model, one generated bank, and one execution per arm. A planned hardening requirement was relaxed after calibration, so the statistical result should be treated as exploratory. Both registered Brain failures were retained.

## 5 Blind comparison with a strong indexed search baseline

**Executed:** 3 November, 21:35–21:40 UTC. **Final grading and analysis:** 4 November, approximately 00:47–01:25 UTC. **Size:** 50 natural-language questions. **Source:** S5.

A SQLite FTS5 index and served Brain retrieval searched the same memory collection. A grader who built neither arm scored shuffled candidate file bodies, without seeing the arm labels or answer key: 2 for an answer, 1 for partial relevance and 0 for no answer.

For the first result alone, the Brain scored **43/100 points**, the index **44/100**; the head-to-head comparison was 10 wins each and 30 ties. The index was faster under the measured load: median **1.5 seconds**, against **2.5 seconds** for the Brain.

The result changed when the unit was the best answer anywhere in the first five files. The Brain scored **77/100**, the index **66/100**; a fully answering file appeared for **33/50 versus 27/50** questions. The Brain led on nine questions, the index on two, with 39 ties.

An index-first hybrid assembled from the already graded candidates scored **73/100**, below the Brain's 77. It won on no question and lost on three. This was a reuse of the same grades, not a fresh evaluation, and the hybrid was not pursued.

**What it teaches:** the Brain's benefit can lie below the first hit. Search is a strong baseline, and combining two tools does not automatically improve either. This is a retrieval-quality test with subjective grading, not completed agent work.

## 6 A larger embedding model

**Reports dated:** 4 November locally; result commits **3 November, 22:33–22:34 UTC**. **Size:** 138 design questions, 350 held-out questions and 126 confirmatory questions. **Source:** S6.

The test compared a 384-dimensional embedding model with a 768-dimensional model over the same approximately 45,000 notes. It measured the position of a designated relevant note, without a solving agent.

The 138-question design set suggested better top-20 retrieval. On the 350-question held-out set, however, the registered top-20 outcome was **27 versus 28**, with nine wins and eight losses for the larger model: **p = 1**. It failed its primary test.

A secondary top-five gain, **8 versus 20**, motivated a new preregistered confirmation on 126 unused questions. That top-five primary result was **13 versus 11**, favoring the smaller model numerically: **p = 0.75**. The apparent top-five gain did not replicate.

The larger model did improve broad candidate reach: top-200 hits were **63→84/350** and **45→63/126**. It also took substantially longer to embed the notes in the reported environment: **1,512 versus 6,098 seconds**, with heavy swapping during the larger run.

**What it teaches:** better candidate coverage is useful for a possible later ranking stage, but it is not the same as a better answer near the top. The larger model was not adopted as a retrieval improvement on this evidence.

## 7 Cross-encoder reranking on real prompts

**Registration recorded:** 3 November, 23:35 UTC. **Initial result committed:** 23:50 UTC; follow-up audit committed **4 November, 00:09 UTC**. **Size:** 115 real-prompt episodes. **Source:** S7.

A preregistered hybrid combined a lexical candidate list with a larger embedding pool, then used a cross-encoder to reorder the first 20 positions. The primary outcome was whether a designated relevant memory appeared in the first five.

Plain recall achieved **31/115**, the hybrid **20/115**. There were **16 losses and five gains**, exact paired **p = 0.027**. Top-20 hits also fell, **42→31**. Plain recall took approximately **1.27 seconds per prompt**, and the hybrid added **4.46 seconds on CPU**.

Earlier design results on written questions had looked more promising. They were not treated as confirmation. The real-prompt primary failed, and the change was not adopted.

The relevance labels were memories that the original sessions subsequently read. That is a behavioral proxy, not an independent relevance judgment, and it can favor the existing system. A later audit removed reads previously surfaced by a hook; top-five counts still favored plain recall, **28 versus 17**. This narrows one source of bias without proving the labels are unbiased.

**What it teaches:** an apparently stronger ranking model can be worse on the prompts agents actually receive. The measured loss matters more than the sophistication of the proposed component.

## 8 Transcript coverage with a regression gate and a fresh repair test

**Experiments and reports:** 5 November. **Size:** 37 transcript questions, 50 ordinary questions and 15 unanswerable questions, followed by 20 fresh ordinary and 20 fresh past-session questions. **Source:** S8.

A shadow index added **15,861 transcript segments**. Bank holders graded candidate results with arm labels hidden. The original expansion improved the transcript bank's best-in-five score from **3 to 42 out of 74 possible points**. Removing repeated-segment credit left **41/74**.

On the 50 ordinary questions, the best-in-five score fell by **four points on the 100-point bank scale**. Grading the 17 questions whose candidate sets differed established that change; the other 33 had the same candidate sets and therefore no difference on this metric. The registered tolerance allowed a loss of at most three points, so the four-point loss blocked adoption. A same-base control reproduced identical ordinary top-five lists without expansion, separating the observed regression from a suspected indexing-time difference.

A later two-index merge still failed the ordinary-question requirement. The next design inserted a transcript result only after a frozen score/cue gate. On fresh questions, ordinary performance was **40=40/40 points**, while past-session performance rose **1→20/40**, with 13 wins and no losses.

The fresh ordinary control had a ceiling: every answer was already in the original first four positions. It therefore could not reveal the earlier failure caused by displacing a useful fifth result. The gate opened on 15/20 ordinary questions, and every inserted segment on those questions was irrelevant. The 15 unanswerable questions scored zero in both arms, but those relevance grades did not measure whether an agent would actually abstain.

A related six-stage transcript hunt initially had zero completed stages in both repetitions of both arms. After the new route, the Brain repetitions reached six and two stages, with **one of two complete**. No answer appeared in recall output itself: recall exposed the index location, then the agent read the answers directly. This is a linked application check, not an independent large study.

**What it teaches:** expanding memory can recover otherwise missing experience while degrading ordinary retrieval. A useful learning loop detects the regression, blocks the first design, tests a narrower repair and retains the repair's remaining weakness.

## 9 Locating code with a symbol map

**Run and report:** 5 November. **Size:** 10 development and 10 held-out questions. **Source:** S9.

A purpose-built code map based on source-code symbols was compared with Graphify and ordinary Brain memory recall. All three received the same code-location questions. The first ten were written with file names known and were treated as biased development data.

On the ten held-out questions, which avoided file-name words, the correct file appeared in the first three results for **9/10** with the code map, **4/10** with Graphify and **3/10** with memory recall. Reported query times were approximately **5.5, 20.8 and 13.5 seconds**, respectively. The code-map output was approximately 2,000 characters, versus 5,000 for Graphify.

A later Brain command routed directly to the map and reproduced **9/10** at about **1.2 seconds**. That is integration/parity evidence on the same questions, not another held-out success.

The sample is small, and file location is only a first step toward a code change or explanation. The map missed a paraphrase with no matching code vocabulary. Its stored view also became stale, leading to a separate refresh check.

**What it teaches:** useful agency includes selecting the right representation and tool. A code-oriented index can beat a general memory retriever for finding implementation locations. This result supports targeted tool choice, not a global claim that graphs improve all tasks.

## 10 Superseded-evidence hunt

**Completed:** 5 November, 16:51 UTC; reported at 17:13 UTC. **Size:** six stages, two repetitions per arm. **Source:** S10.

This hunt required navigating from outdated evidence to the current value under a slow-key scheme designed to prevent guessing from answer-bearing file names.

The Brain arm completed **6/6 stages in both runs**, taking **2,808 and 1,242 seconds**, with six and five recall calls. The documents arm reached only the readable first stage in both runs, taking **426 and 266 seconds**, with no recall calls or candidate-search loop.

The control did not have the memory information needed beyond the starting stage. Its stopping behavior should not be described as lack of motivation or defective agency. This is an information-access and workflow demonstration, not an equal-information comparison. The small sample and changing hunt harness also limit generalization.

**What it teaches:** the Brain can support a multi-step workflow that depends on recorded revisions. A fairer next test must provide strong search with the same old and current records, then compare correct resolution, completion, cost and constraint adherence.

## What the combined evidence supports

**Better completion on some bounded tasks.** The 113-question investigation test found 30 correct answers with the Brain versus eight through ordinary search over the underlying records. This is a substantial measured gain in evidence discovery under the tested budget, accompanied by more wrong answers. The 42-question matched-information comparison showed lower median search time and input with the Brain, but no accuracy advantage; unequal rerun caps limit the final accuracy comparison.

**Usable continuity across a session boundary.** The 14-task workflow test produced 12 completions with the Brain versus four without. The later session used persistent decisions to finish work. Required history was withheld from the control, and the Brain was enabled in both stages, so the test does not isolate recovery quality against equally informed search. The six-stage hunt adds a small example of sustained execution with revised records, under the same information-access limitation.

**A concrete feedback loop with unresolved weaknesses.** The transcript program detected a regression, blocked the initial expansion, used a control to separate causes, and tested a narrower repair on fresh questions. The repair recovered more past-session information, but its ordinary-question bank could not expose the failure that mattered most. The embedding and reranking results reinforce the value of rejecting changes that fail their registered tests.

This is evidence for useful agentic behavior in investigation, memory use, tool selection and continuity. It does not establish ten agency wins, multi-day autonomous reliability or generalized planning superiority. None of the ten establishes that a named open mathematical problem was solved. Completing a workflow and independently verifying a new theorem require different evidence.

## Source register and reproducibility limits

The following producer artifacts were read and cross-checked. Their surrounding material contains private work history or evaluation instruments, so only bounded aggregate findings are presented here.

- **S1:** three-arm 42-question work-memory recall report, 6 November, with timeout, rerun-budget and grader-correction notes.
- **S2:** 29-question older-history/local-investigation pilot result and exclusion log, 5 November.
- **S3:** 113-question difficult-investigation protocol, six-command result tables and disclosed registration departures, 3 November; same-bank two-command run and later adjudication used only as qualifications.
- **S4:** two-session workflow protocol, 14-task paired scoring outputs and calibration notes, 3 November.
- **S5:** indexed-search comparison report, including independent 50-question body grades and the same-grade hybrid analysis, finalized 4 November.
- **S6:** embedding comparison result, including the 350-question held-out test and 126-question registered confirmation.
- **S7:** real-prompt reranker protocol, result and subsequent exposure-bias audit.
- **S8:** transcript-coverage result ledger and detailed grading report for the original expansion, same-base control, two-index merge and fresh gated evaluation; related transcript-hunt ledger.
- **S9:** held-out code-location comparison and later routing/parity report.
- **S10:** superseded-evidence hunt's final four-run ledger entry.

The source checks establish what producers reported and identify inconsistencies; they do not independently validate every run or grade. Some banks were reused, some scoring was amended, and the statistical tests do not adjust for every experiment or subgroup explored. Those limits apply even when an individual p-value is small.

Public reproducibility would require separately reviewed synthetic or appropriately licensed fixtures and complete executable protocols. Future comparisons should give strong baselines the same underlying evidence, freeze scoring and exclusions before execution, independently check final artifacts, and report correct completion, wrong answers, abstentions, time, resource use, constraint adherence and recovery together.
