# What the Brain helps with, and what its tests do not yet establish

*Evidence review, 6 November 2026.*

The Brain is a shared memory and workflow system for AI agents. Its strongest evidence is about **using work already recorded**: finding relevant facts, joining several records, recovering earlier decisions, and carrying useful state into another session. Some tests show large improvements over searching files under a deadline. Others show a smaller accuracy gain with substantial extra latency, no clear gain, or an advantage for ordinary file search.

There is also positive evidence for **more efficient multi-step execution**. In two small six-stage scavenger hunts, both arms could find the needed information and completed every repetition, while the Brain arm finished faster with substantially less input. A separate blind comparison favored memory-informed research plans in seven of eight judgments. Longer hunts and a session handoff demonstrate additional continuity. These are meaningful agentic results, with limits: the samples are small, plan quality is not completed research, and several other hunts gave only the Brain arm the information needed to finish.

## What the system is

The Brain combines persistent records, source references, links between records, retrieval tools, and agent hooks. Its documented workflow asks an agent to retrieve earlier work, keep a next-step note, save intermediate results, coordinate changes to shared state, check important claims, and write corrections back with provenance. Old records can be superseded rather than silently erased. [1]

These are useful mechanisms for continuity. A model still has to decide what to do, use the tools, check the evidence, and complete the task. Having a memory tool available is not the same as the agent successfully using it. Nor does a stored correction guarantee that every later session will retrieve and follow it.

The evaluations below concern particular deployed configurations. They are not a certification that every installation or the public starter has the same corpus, integration, or performance.

## Evidence status

This is a synthesis of selected producer-reported results, not an independent rerun or an inventory of every Brain experiment. Public reports support the benchmark figures and limitations below. The newer hunt figures are sanitized development aggregates whose detailed instruments and run records are not publicly released. No question contents, answers, private source locations, or access details are included here.

The public harder-task report has a 2 November cutoff. The hunt observations are from 5–6 November. A later experiment may change the picture. Counts of prepared questions, launched jobs, repeated runs, and completed distinct tests are different quantities and should not be added together as though they were interchangeable.

## 1. The clearest positive result: finding recorded information before time runs out

In a three-arm evaluation, Claude Sonnet agents answered the same 988 items with the Brain, with searchable research repositories, or without tools. With a 600-second limit per item:

- Brain: **95.4% correct**.
- Repository search: **82.0% correct**.
- Model alone: **18.9% correct**.
- Mean time was **120 seconds with the Brain versus 211 seconds with repository search**.
- Timeouts were **3.7% versus 17.5%**.

The repository arm could access the exported store and relevant research records, making it a much stronger control than the model alone. On disagreements, the Brain was correct alone on 146 items and the repository arm on 13. The reported paired test was p = 1.2 × 10⁻²⁹. [2]

The key qualification is that, on the **806 items both arms finished within the deadline, accuracy was essentially tied: 99.3% and 99.4%**. That is a selected subset, not a separate randomized test, but it helps explain the overall result. The Brain mainly helped agents reach and combine existing information within the time budget. This is not evidence of a comparable improvement in mathematical reasoning.

The underlying bank was also heavily weighted toward retrieval: 923 of its 1,000 items were store-record lookups and 77 were proof audits. The arms had different interfaces, and a later access audit found that the isolation rules needed tightening, although it did not identify answer-key access in the inspected records. This is useful evidence for the evaluated retrieval workflow, not a perfectly isolated universal comparison. [2]

## 2. The important counterexample: another model did better with the files

On 394 items from the same bank, Codex agents with a 900-second limit scored **94.7% with the Brain and 97.0% with repository access**. There were 10 Brain-only successes and 19 repository-only successes; p = .14. The experiment did not establish a reliable difference, but its observed direction favored repository access. [2]

The Brain arm had recall without a shell; the repository arm had a read-only shell. Of the 19 Brain losses, 16 were abstentions and three were timeouts. Four occurred without a recall call. The report identifies tool-startup and retrieval problems among the practical failure modes.

The lesson is not that memory always helps or always hurts. Its value depends on the model, available tools, time budget, retrieval quality, and integration reliability. A tool that fails to become available, returns the wrong material, or takes too long can remove the expected benefit.

## 3. Broader repository tests: some accuracy gains, often slower

### A new bank of repository and history questions

A larger evaluation generated 2,000 new questions from repository contents and history. Both arms had files; one also had Brain recall. There were **1,726 completed paired items**, on which the Brain scored **93.7% versus 91.4%** for git/files alone. The reported paired result was 83 Brain-only versus 43 git-only successes, p = .00046. [3]

This gain came with a substantial time cost: median time was **67 seconds versus 13 seconds**, and mean time was **154 seconds versus 25 seconds**. The report attributes much of the accuracy difference to fewer unsuccessful searches ending in an abstention, rather than more accurate answers once an agent committed to one.

There are material limitations:

- Grading rules were revised after answers were known, then applied to both arms. Under the original rules, the figures were **90.2% versus 88.6%**, with p = .044.
- The run stopped early for quota reasons. The Brain arm's 274 missing answers were excluded from paired scoring, not counted as wrong.
- Overloaded runs were excluded in both directions. The reported figures therefore do not measure completion reliability across every attempted run.

### Newer and harder recall

A later report gives **690/809 correct with the Brain versus 656/809 with direct search**, or **85.3% versus 81.1%**, with p = .00187. Mean time was **68 seconds versus 23 seconds**. The remaining 80 planned questions belonged to worker pairs that were not launched. Configuration changed during execution, limiting attribution of the overall effect. [4]

The harder pilot was inconclusive: **61/80 versus 59/80**, p = .727, with mean time **33 seconds versus 15 seconds**. One entire 20-question pair was excluded for prohibited access, and the report retains an unresolved discrepancy about the configured reasoning effort and command allowance. These limitations should remain visible rather than being cleaned out of the headline.

Together, these tests support a bounded claim: memory sometimes helps an agent find an answer it otherwise abandons, but the extra retrieval can be expensive in elapsed time. They do not support a general speedup.

## 4. Why the biggest memory-versus-no-memory gaps need care

In a 1,000-item comparison, agents with recall scored **998/1,000**, versus **374/1,000** without tools. Almost the entire improvement was on facts held in the store that the unaided agent could not know. On the 77 proof audits, the scores were **75 versus 74**. [5]

That is a useful demonstration that the system can supply otherwise unavailable information. It is not a fair test of whether its retrieval method beats ordinary search over the same evidence, and it is not a 62-percentage-point reasoning improvement.

The distinction applies to the scavenger hunts too: **access to the necessary information** and **the ability to use the same information better** are separate experimental questions.

## 5. Scavenger hunts: better execution efficiency and sustained work

The hunts require an agent to complete successive stages, with later stages dependent on earlier answers. They test whether an agent can continue a sequence of retrieval and execution steps instead of merely answering an isolated question. The following are producer-reported development aggregates, not independently reproduced benchmark results. [6]

### The strongest comparative agency evidence: information accessible to both arms

Two six-stage hunt banks used the same model and reasoning effort, a 1,800-second cap, and two repetitions per arm. The direct-search arm could find the underlying facts in accessible records, including copies of memory content. In each bank, **both arms completed all six stages in both repetitions**.

- On the first bank, median completion time was **104 seconds with the Brain versus 184 seconds with direct search**. Median command counts were **19.5 versus 30.5**, and reported median input was **0.49 million versus 2.38 million tokens**.
- On an older-history bank, median completion time was **85 seconds versus 307 seconds**, command counts **18 versus 55**, and input **0.53 million versus 5.0 million tokens**.

These are completed multi-step tasks, not just successful recall calls. The Brain arm delivered the same completion outcome using roughly **43–72% less elapsed time and 79–89% less input** in these observations. That is positive evidence of superior execution efficiency on the measured tasks. It should not be dismissed merely because both arms eventually succeeded.

This comparison is stronger than denying the control necessary facts. However, the records were reachable through different interfaces and copies, not a verified identical source bundle. There were only two banks and two repetitions per arm, no demonstrated completion-rate advantage, and no independent reproduction. The producer reports reviewing flagged tool events and correcting false-positive audit flags; this report has not independently inspected every raw event. Input-token reductions are not automatically equivalent to dollar savings.

### A fourteen-stage run

In two repetitions, the Brain arm completed **all 14 stages**, taking **2,861 and 3,319 seconds**, approximately **48 and 55 minutes**. Each run made 11 recall calls. The documents arm completed neither repetition and reached only the first stage in each.

This demonstrates a memory-supported agent maintaining progress through a bounded chain for close to an hour. But the documents arm lacked notes needed after the initial stage. Its failure is therefore not evidence that a comparably informed agent cannot plan or sustain the task. Two repetitions also give little basis for estimating reliability.

### An eight-stage handoff

In the eight-stage relay test selected for this review, both Brain repetitions completed the task. In one, the first session reached its time limit after maintaining a handoff record; a fresh session read that record and completed the task in another **423 seconds**. In the other, the first session had already finished, and the second mainly read and reported the result.

The first is a concrete example of unfinished work surviving a session boundary. The second is not another independent demonstration of recovering an unfinished task. Neither establishes robust multi-day autonomy or general recovery from arbitrary failures.

### Failures and controls matter

- In one eight-stage run, one Brain repetition completed and the other timed out. Completion was not universal.
- In a six-stage operational hunt, one Brain repetition completed and another timed out. The successful repetition made no recall call, so completion alone cannot be attributed to recall.
- In a six-stage math-memory hunt, both Brain repetitions initially timed out. Both completed on a later rerun with a larger allowed budget, but their actual completion times were below the original limit. Run-to-run variability, not simply the larger cap, must remain part of the explanation.
- In a small code-location hunt where direct search could reach the needed information, **both arms completed both repetitions**. Direct search took **270 and 281 seconds**; the Brain arm took **498 and 373 seconds**. One Brain run used no recall. This is a useful counterexample to claims that adding memory always improves navigation.

The harness also changed during development. Some early runs used unintended shortcuts and are excluded from the claims above. The two information-accessible six-stage banks were reported clean after event review; they should not be discarded solely because other runs exploited the early harness. Some apparent control failures in other runs arose from access-scope instructions, not demonstrated inability to solve the task. Respecting a restriction should not be scored as a failure of agency.

These hunts are promising demonstrations of continuity and tool use. The next evidential step is a frozen, independently checked comparison with equally available underlying information, matched resources, and enough distinct tasks and repetitions to estimate failure rates.

## 6. Math reasoning and new-problem solving are not yet established benefits

There are positive proof-audit results, but their controls matter. On 160 planted-fault audits, relevant notes beat scrambled notes **128 to 117**, with p = .043. However, relevant notes did not show a reliable advantage over no notes in the corresponding comparisons: **80.0% versus 77.4%** on 150 items, and **301 versus 299** on 358 items. Content quality matters; this does not establish that supplying notes generally improves proof reasoning. [7]

A pooled 61-item solving/auditing exercise scored **52 correct with a full assistance package versus 41 with tools alone**. The package combined documents, retrieval, hooks, additional notes and hints, and statements of named results. Against documents plus recall, it scored **52 versus 46**, p = .18. The stronger result therefore cannot be isolated to the Brain, and much of the benefit concerned access to known results. [8]

On a small blind-graded test involving 13 open problems, the Brain arm scored **24/39 versus 26/39** without it. A referee preferred the no-Brain attempt eight times and the Brain attempt three times, with two ties; p = .23. This is inconclusive and does not prove harm, but it provides **no demonstrated advantage in solving new open problems**. [9]

A useful memory can support research without independently creating new mathematics. Claims that a particular open problem has been solved require a correct proof and suitable independent scrutiny; retrieval scores and hunt completion cannot substitute for that.

## 7. What “agentic” and “long-horizon” mean here

There is evidence about planning as well as execution. A public report describes a preregistered, blind-judged comparison at **eight cutoffs on two open problems**. Plans written with a digest of the store's earlier work were preferred **seven of eight times**, with **eight redundant or dead-end steps versus seventeen** in the comparison plans. [10] This supports better plan selection in that small experiment. The intervention supplied a digest, the cutoffs concern only two problems, and the outcome was judged plans rather than executed plans or solved problems. It is not evidence of a broad, independently replicated planning advantage.

The Brain supports several ingredients of agentic work:

- **Continuity:** preserving results, decisions, failed routes, and the next action across sessions.
- **Coordination:** explicit handoffs and mechanisms for avoiding conflicting changes to shared state.
- **Correction:** recording why an earlier claim was wrong and making the replacement discoverable.
- **Execution support:** returning relevant evidence during a task and prompting an agent to save what it learns.

The public starter includes integration tests for hooks, concurrent claims and writes, and background maintenance. These check mechanisms such as whether a hook fires or concurrent writes are retained. They are software checks, not measurements of real-world research productivity or autonomous judgment. [1]

The public harder-task report also distinguishes prepared agency fixtures from completed evaluations: at its 2 November cutoff, the proposed controlled agency suite had not run, and an observational session audit could not establish a causal benefit. [4] The newer hunts add actual execution examples, but do not erase the control and sample-size limitations described above.

The defensible agency claim is specific: **the measured six-stage tasks show more efficient completed execution with information accessible to both arms; a small blind comparison favors memory-informed plans; and the selected longer demonstrations include sustained work and a reported continuation of unfinished work across a session boundary**. These are real positive results. The evidence summarized here does not establish reliable autonomous research over days or weeks, continual improvement without supervision, or a general planning advantage across tasks and models.

## Overall assessment

The Brain is most convincingly useful when relevant earlier work exists, an agent would otherwise spend too long finding it, and the retrieval and writeback tools are functioning properly. It can make that work easier to locate and reuse, and it can help preserve progress that would otherwise disappear with a session.

It is less clearly beneficial when ordinary search already finds the answer quickly, when retrieval adds latency without useful evidence, or when the task requires reasoning beyond what the stored work supplies. The current record contains all of these cases.

The next evaluation should keep the underlying information equal, freeze access rules and scoring before execution, use genuinely held-out tasks, distinguish repeated attempts from distinct tasks, and report final-artifact correctness, completion, time, resource use, constraint adherence, and recovery failures together. A convincing system should earn its claim on those measures, not on a single favorable percentage.

## Sources and traceability

Public sources are linked below. Figures and statistical tests are reported as given by the producers, not recomputed from private raw records. Reported p-values do not correct for every experiment or subgroup explored and should not be treated as guarantees of generalization.

1. [Public system overview](https://github.com/yspennstate/math-brain-public/blob/05510d706aead8ade313626705282a77dddaeba8/README.md) and [documented agent workflow and starter integration tests](https://github.com/yspennstate/math-brain-public/blob/05510d706aead8ade313626705282a77dddaeba8/docs/agency.md).
2. [Three-arm comparison, controls, timeouts, and model-dependent results](https://github.com/yspennstate/math-brain-public/blob/bc8ca81461ba416bde7c3a6ded6274ad14c52505/TESTS.md#L129-L153).
3. [Repository/history evaluation, revised grading, timing, and missing pairs](https://github.com/yspennstate/math-brain-public/blob/bc8ca81461ba416bde7c3a6ded6274ad14c52505/TESTS.md#L31-L66).
4. [Harder recall and agency results, evidence cutoff 2 November 2026](https://github.com/yspennstate/math-brain-public/blob/e4f4d0a43b96223265ffa3db3d049dcad2f2c3cb/results/new-hard-recall-2026-11-02/README.md).
5. [Store-access versus no-tool comparison](https://github.com/yspennstate/math-brain-public/blob/bc8ca81461ba416bde7c3a6ded6274ad14c52505/TESTS.md#L174-L184).
6. Producer-reported scavenger-hunt development summaries, 5–6 November 2026. Sanitized aggregate results only; no public reproducibility package is available for these runs.
7. [Relevant, scrambled, and absent notes in proof audits](https://github.com/yspennstate/math-brain-public/blob/bc8ca81461ba416bde7c3a6ded6274ad14c52505/TESTS.md#L74-L76).
8. [Solving/auditing comparisons and assistance-package limitations](https://github.com/yspennstate/math-brain-public/blob/bc8ca81461ba416bde7c3a6ded6274ad14c52505/TESTS.md#L232-L244).
9. [Blind-graded open-problem comparison](https://github.com/yspennstate/math-brain-public/blob/bc8ca81461ba416bde7c3a6ded6274ad14c52505/TESTS.md#L278).
10. [Preregistered blind comparison of memory-informed research plans](https://github.com/yspennstate/math-brain-public/blob/bc8ca81461ba416bde7c3a6ded6274ad14c52505/TESTS.md#L78-L82).
