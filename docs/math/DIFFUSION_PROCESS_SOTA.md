# A diffusion process for this brain

Mathematical and engineering specification, 23–24 July 2026. This document extends
`DIFFUSION_THEORY.md`, which develops the consequences of the
path-convergence paper. The question here is narrower and more operational:

> What should “diffusion” mean in a persistent, heterogeneous memory system, and
> which parts of modern diffusion, flow, consistency, and retrieval research survive
> contact with this brain's real data?

The answer is not “train an image denoiser on Markdown.” The brain has a finite
external memory, an association graph, lexical and semantic retrieval, a replay
process, live world state, and acts that can be right or wrong. Its useful analogue is
a **risk-conditioned, multi-channel reverse process over evidence paths**, with early
stopping and a terminal reality corrector. Some components are already measured and
implemented; some remain design propositions. The status of each claim is explicit.

## 1. Epistemic status

Four labels are used:

- **Paper theorem**: a result proved in a cited paper, with its original assumptions.
- **Local measurement**: a result on this brain's real corpus or traces.
- **Design proposition**: a mechanism justified by the mathematics but not itself a
  theorem.
- **Open conjecture**: a plausible direction that has not cleared a local gate.

The mapping from a theorem about Gaussian diffusion to a memory system is never
silently promoted into a theorem about the brain. It is an analogy until measured.

The strongest local facts available at the start of this ten-hour program are:

1. **Local measurement.** A strength-weighted semantic seed centroid improved
   semantic-component MRR from 0.0464 to 0.0692 overall and from 0.0112 to 0.0163
   on the hard subset, positive in every one of five folds. The BM25-first
   end-to-end pipeline did not move.
2. **Local measurement.** Deliberate retrieval demand has a fast age head with
   fitted time constant about 1.3 days and a nearly flat old-age plateau roughly
   110 times lower per memory-day. Beyond 30 days, feedback memories were retrieved
   about thirteen times as often as project memories.
3. **Local measurement.** A sharp low-temperature semantic free energy won the
   easy topical regime but lost the hard semantic-tail regime in every fold.
   BM25 already owns the sharp topical head; the warm semantic centroid is useful
   in the tail.
4. **Local measurement.** Text-pattern reflexes miss failures whose necessary world
   state was absent from the conditioning set. Grounding language in a report is not
   grounding reality.
5. **Local refutation.** A hard threshold on a single evidence-mass product
   `B_hat = mass × coherence` confounded narrow coherent queries with vague
   incoherent ones. Mass and coherence may be useful diagnostics, but the product
   is not a green/red truth gate.

These measurements constrain the theory. A beautiful mechanism that contradicts
them is not the mechanism for this brain.

## 2. State space

At clock time \(t\), let the active memory store be

\[
\mathcal M_t=\{m_i\}_{i=1}^{N_t}.
\]

Each memory is represented by

\[
m_i=(x_i,\ell_i,g_i,a_i,c_i,\pi_i,\sigma_i),
\]

where:

- \(x_i\in \mathbb S^{d-1}\) is a normalized semantic embedding;
- \(\ell_i\) is lexical content and sparse term statistics;
- \(g_i\) is its graph neighborhood;
- \(a_i\ge 0\) is age;
- \(c_i\) is a memory type such as feedback, reference, project, or transient;
- \(\pi_i\) is provenance and verification state;
- \(\sigma_i\) is lifecycle state: live, superseded, correction endpoint, or
  substrate-only.

A query is not merely a string. The conditioning state is

\[
C=(q,\;u,\;w,\;r,\;F),
\]

where \(q\) is the text, \(u\) is the user/session context, \(w\) is sampled world
state, \(r\) is act risk, and \(F\) contains freshness times and receipts for
required input channels. A system conditioned only on \(q\) is permitted to retrieve
ideas. It is not permitted to certify a current external fact.

The clean latent object is an **evidence path**

\[
Z_0=(I,E,\Gamma,A),
\]

where \(I\) is an ordered set of memories, \(E\) is the supporting evidence,
\(\Gamma\) is provenance/lineage, and \(A\) is the intended act or answer. The
reverse process should recover a useful path, not generate a fluent memory-shaped
string.

## 3. The forward corruption process

Modern diffusion models separate a forward corruption process from its reverse.
For this brain, scalar Gaussian noise is the wrong corruption. Define instead a
product Markov operator

\[
K_s =
K^{\rm prov}_s
K^{\rm time}_s
K^{\rm graph}_s
K^{\rm sem}_s
K^{\rm lex}_s,\qquad s\in[0,1],
\]

applied to evidence paths. The factors need not commute.

### 3.1 Lexical corruption

\(K^{\rm lex}_s\) masks or perturbs query terms with rate \(\rho_{\rm lex}(s)\).
Typos, paraphrases, and missing index phrases are real lexical noise. BM25 with
query normalization is the practical inverse for this component.

### 3.2 Semantic corruption

\(K^{\rm sem}_s\) applies heat diffusion on the embedding sphere, or equivalently a
small von Mises–Fisher/Brownian perturbation. This formalizes loss of semantic
precision without inventing Euclidean geometry off the unit sphere.

### 3.3 Graph corruption

\(K^{\rm graph}_s\) drops edges and applies a lazy random walk. This models missing
links, stale coactivation, and the fact that an association may require more than one
hop. The reverse is not graph generation; it is spreading activation over verified,
weighted edges.

### 3.4 Temporal corruption

Age is a second noise axis. The measured hazard for type \(c\) is represented by

\[
h_c(a)=A_f e^{-a/\tau_f}+\lambda_c,
\qquad \tau_f\approx1.3\ {\rm days},
\]

with \(A_f\approx0.67\) on the observed retrieval-rate scale and old-age plateaus

\[
\lambda_{\rm feedback}\approx0.0267,\quad
\lambda_{\rm reference}\approx0.00462,\quad
\lambda_{\rm project}\approx0.00207
\]

per memory-day. The numerical amplitude is a measured local estimator, not a
universal human-memory constant.

The old \(45\)-day exponential tried to make one scale do two jobs. It gave stale
project memories an extended episodic glow and did not express the persistent
feedback plateau cleanly.

### 3.5 Provenance corruption

\(K^{\rm prov}_s\) drops or stales a fraction of receipts, world observations, and
lineage. This is the corruption most text-only systems omit. It represents a real
failure mode: the answer can be linguistically grounded while its necessary external
state was never sampled.

Hard owner rules and correction endpoints are not randomly erased during real
operation. Provenance dropout belongs in evaluation and training/replay, where it
tests robustness, not in the live store.

## 4. Reverse dynamics as operator splitting

Karras et al.'s EDM work is useful here less for a particular image sampler than for
its separation of schedule, parameterization, solver, preconditioning, and loss.
This brain should preserve that separation.

Let \(\mu_s\) be a distribution over candidate memories/evidence paths at noise level
\(s\). One reverse step is split into a predictor and correctors:

\[
\begin{aligned}
\mu_{s-\Delta/2} &= P^{\rm lex+graph}_{s\to s-\Delta/2}(\mu_s;C),\\
\mu_{s-\Delta} &= C^{\rm sem}_{s-\Delta/2\to s-\Delta}
                  (\mu_{s-\Delta/2};C),\\
\widehat\mu_{s-\Delta} &= C^{\rm prov+act}(\mu_{s-\Delta};C).
\end{aligned}
\]

### 4.1 Support-expanding predictor

The path-convergence readout in the source model is a convex combination of its
patterns. The implementation here is **not** literally hull-bound: the unnormalized
mean \(\bar x_p\) lies in the seed hull, but normalization can leave that hull, and
ranking \(\widehat x_p\) against the full embedding matrix can surface an outside-hull
memory. The valid weaker statement is that the centroid retains only the first
weighted moment of the seed cloud. It erases multimodal structure and cannot point
independently along a direction that cancels in that moment.

Lexical retrieval and graph diffusion therefore run first or in parallel to supply
independent, support-expanding directions:

\[
\mathcal C =
\mathcal C_{\rm BM25}(q)
\cup \mathcal C_{\rm graph}(q)
\cup \mathcal C_{\rm sem}(q).
\]

BM25 supplies sharp topical evidence; the graph supplies relational transport.
Cross-topic discovery is not merely a temperature-tuning problem for the semantic
first-moment channel.

### 4.2 Warm semantic corrector

For live seed embeddings \(x_j\) with evidence strengths \(p_j>0\), use

\[
\bar x_p=\frac{\sum_j p_jx_j}{\sum_j p_j},\qquad
\widehat x_p=\frac{\bar x_p}{\|\bar x_p\|}.
\]

Candidates are corrected by cosine to \(\widehat x_p\). Equal seed weights are the
rollback, not the default, because the real held-out link task favored the weighted
centroid in every fold.

The finite-evidence diagnostics are

\[
M=\sum_jp_j,\qquad
C=\|\bar x_p\|,\qquad
\widehat B=MC,\qquad
n_{\rm eff}=\frac{(\sum_jp_j)^2}{\sum_jp_j^2}.
\]

The exact quotient identity in the path-convergence paper motivates inspecting a normalizing
denominator. It does **not** license a universal threshold for this different
retrieval model. Locally, \(\widehat B\) failed as a one-number gate. The correct
interface exposes \(M\) and \(C\) separately:

- low mass, high coherence can be a narrow answerable query;
- high mass, low coherence can be a broad or conflicted query;
- low values of both indicate genuine thin evidence;
- none of these certifies external truth.

### 4.3 Terminal provenance and act corrector

The reverse process can propose a memory path. Before a high-stakes act or a claim
that something is live, current, filled, deleted, connected, or deployed, it must
condition on the required world channels:

\[
F_k = t_{\rm now}-t_k \le \Delta_k(r),
\]

for each required channel \(k\). The tolerance \(\Delta_k\) decreases with act risk.
Examples include mesh inbox/claims, compute state, target identity, before-state,
after-state, and an authoritative external receipt.

This corrector has three outcomes:

1. **ALLOW**: required channels are fresh and the after-state supports the act.
2. **VERIFY**: evidence is incomplete or channels disagree; sample again.
3. **STOP**: a required claim or permission failed.

It is not a word list. It is a typed conditioning contract.

## 5. Multi-path consistency

Flow Matching replaces simulation-heavy score learning with vector-field regression
on probability paths. Consistency models learn maps that agree across noise levels,
and recent discrete work replaces a nonexistent unique probability-flow ODE with
consistency across many valid posterior bridges. The useful import is path agreement,
not image-generation speed.

Let \(\Phi_{t\to s}\) map a noisy evidence state at level \(t\) to level \(s<t\).
The consistency condition is

\[
\Phi_{u\to s}\!\left(\Phi_{t\to u}(z_t)\right)
\approx \Phi_{t\to s}(z_t),
\qquad 0\le s<u<t\le1.
\]

For a heterogeneous discrete brain there are several valid routes:

\[
\begin{aligned}
\gamma_1 &: {\rm lexical}\to{\rm graph}\to{\rm semantic},\\
\gamma_2 &: {\rm graph}\to{\rm lexical}\to{\rm semantic},\\
\gamma_3 &: {\rm semantic\ seed}\to{\rm lexical\ expansion}\to{\rm graph},\\
\gamma_4 &: {\rm correction}\to{\rm supersession}\to{\rm current\ evidence}.
\end{aligned}
\]

Let \(p_\gamma(i\mid C)\) be the candidate distribution from route \(\gamma\). Define
the path-disagreement statistic

\[
D_{\rm path}(C)=
\frac1{|\Gamma|}\sum_{\gamma\in\Gamma}
D_{\rm JS}\!\left(p_\gamma\,\middle\|\,\bar p\right),
\qquad
\bar p=\frac1{|\Gamma|}\sum_\gamma p_\gamma.
\]

Low disagreement supports one-step execution. High disagreement is useful
information: it invokes another retrieval or a reality check. Averaging the routes
until the disagreement disappears would manufacture confidence and is forbidden.

## 6. One-step reflexes as consistency students

A compiled reflex is a one-step student of a slower verified teacher path. If
\(\Psi\) is the deliberate multi-step procedure and \(f_\theta\) the reflex,

\[
\mathcal L_{\rm cons} =
\mathbb E_{t>u>s}
d\!\left(
f_\theta(z_t,t,s),
f_{\bar\theta}(z_u,u,s)
\right),
\quad z_u=\Phi_{t\to u}(z_t).
\]

For this brain the student output is intentionally narrow:

- trigger deliberation;
- require a missing condition;
- block a forbidden act;
- point to the relevant correction.

The student does not synthesize the resolution. A one-step reflex trained on finite
wordings inherits the off-training-path obstruction in the path-convergence paper.
It must be tested on paraphrases, agent voice, tool outputs, and act-state envelopes.

## 7. Replay distribution

Dream replay should sample useful evidence paths without becoming either “the newest
thirty files again” or uniform archaeology. For candidate memory \(i\), define

\[
u_i =
h_{c_i}(a_i)
\bigl[1+\log(1+o_i)\bigr]
\times g_{\rm sal}(i)
\times g_{\rm affect}(i)
\times g_{\rm orphan}(i).
\]

Here \(o_i\) is token overlap with current concerns.

The factors are bounded and auditable. Unknown birth time enters the persistent
plateau; it is never fabricated as recent.

The initial design proposed a Min-SNR analogue. Partition candidates into episodic,
transition, and persistent bands, let \(Z_b=\sum_{i\in b}u_i\), and cap each band at
the median positive mass:

\[
\widetilde u_i =
u_i \min\!\left(1,\frac{\operatorname{median}_{b'}Z_{b'}}{Z_{b(i)}}\right).
\]

**Local refutation.** On the real retrieval trace, after excluding memories younger
than eight days because the dream's recent section already owns them, this cap was
dominated by the raw measured schedule:

| Arm | hit@13 | recall@13 | MRR |
|---|---:|---:|---:|
| old 45-day scale | 0.297 | 0.0510 | 0.1221 |
| two-scale raw | 0.432 | 0.0823 | 0.1900 |
| two-scale with band cap | 0.270 | 0.0563 | 0.1396 |

These are the authoritative frozen-input numbers: the 2,433-memory versioned
mirror at commit `2fd03c9` and one byte-copied retrieval trace. Relative to the old
scale, the raw schedule improved hit rate 45%, recall 62%, and MRR 56% in the central
retrospective cell. The cap hurt every metric relative to the raw schedule. It remains
implemented as an ablation and is not the default.

The pre-registered sensitivity grid varied minimum age over 8, 15, and 30 days and
prediction horizon over 12, 24, and 48 hours. Hit rate and recall improved in all
nine cells. MRR improved in seven of nine, with a median relative change of only
6.9%; it fell 3.7% and 1.1% in the 15-day/24-hour and 15-day/48-hour cells. Thus the
directional robustness rule passes at its exact 7/9 boundary, but “uniform” or
“drastic” would be false descriptions.

An adversarial audit found an age-provenance sensitivity and a parser bug. Fifty-five
compact `YYYYMMDD` slug dates were initially misclassified as unknown; after adding
that parser and its regression test, 210 of 2,433 memories remain genuinely
undated. The frozen Git checkout gives those files current mtimes, making them
unavailable at historical cutoffs. An explicit opposite-bound sensitivity treated
all 210 as already persistent. The central relative gains remained positive:
45.5% hit rate, 37.7% recall, and 49.0% MRR. Thus direction survives both extreme
age policies, but magnitude and the eligible event pool change; prospective data
with real birth provenance remains the proper confirmation.

Frozen negative controls were mixed:

- permuting memory-specific creation times reduced the central MRR advantage from
  +0.0680 to +0.0063;
- permuting types reduced the absolute MRR advantage to +0.0338 and made recall
  12% worse relative to the old scale;
- rotating future target identity by five episodes did **not** destroy the result;
  its MRR advantage increased to +0.1416, consistent with slow recurrence or a
  generic ranking-shape advantage.

The creation-time control supports a timing-specific component, while the failed
target-rotation control blocks a clean mechanism-identification claim.

Episode-paired uncertainty is also wide in the central cell. The deterministic
20,000-resample bootstrap 95% intervals for absolute candidate-minus-old deltas were
\([0.000,0.270]\) for hit rate, \([-0.003,0.067]\) for recall, and
\([-0.025,0.170]\) for MRR. One-sided 50,000-draw sign-flip p-values were 0.062,
0.048, and 0.100 respectively. These descriptive tests share the calibration trace;
even the nominal recall result is not an independent confirmatory test.

The deployed dream samples rather than taking a deterministic top-\(K\), so a second
frozen check used the exact Plackett-Luce distribution. Each old/new pair shared
512 exponential-race noise vectors over each of 37 episodes. Average hit@13 changed
from 0.1391 to 0.2291, recall@13 from 0.0194 to 0.0330, and MRR from 0.0364 to
0.0628. Episode-paired bootstrap intervals for the absolute deltas were
\([0.0628,0.1199]\), \([0.0085,0.0190]\), and \([0.0178,0.0361]\). This removes the
rank-versus-sampling surrogate concern.

Those iid episode intervals are descriptive because 12-hour checkpoints overlap
24-hour target windows; lag-one delta correlations were 0.47--0.60. A post-hoc
circular moving-block sensitivity remained positive at both four- and eight-episode
blocks. The respective 95% intervals were \([0.0504,0.1328]\) and
\([0.0476,0.1339]\) for hit rate, \([0.0062,0.0208]\) and
\([0.0047,0.0226]\) for recall, and \([0.0139,0.0403]\) and
\([0.0129,0.0409]\) for MRR. Because block lengths were selected after the first
result, this is a robustness sensitivity, not confirmatory inference. It does not
remove same-trace calibration or the unavailable historical-consciousness-pool
limitation.

This is exactly why state-of-the-art mechanisms are imported as hypotheses rather
than decorations. Min-SNR addresses conflicting diffusion-training gradients. A
finite replay-selection budget is not automatically the same problem.

The result is not a clean holdout: the temporal constants were estimated from the
same 26-day trace, historical consciousness tiers cannot be reconstructed, and the
retrieval log is downstream of the current brain. It is strong enough to reject the
cap and justify a rollback-safe candidate for prospective measurement; it is not
enough to claim a causal or drastic improvement in the live brain.

## 8. Objective and constraints

The brain has no useful FID analogue. Its objective is multi-part:

\[
\begin{aligned}
\mathcal L ={}&
w_R\mathcal L_{\rm rank}
+w_C D_{\rm path}
+w_K\mathcal L_{\rm calibration}\\
&+w_A(s,r)\mathcal L_{\rm act}
+w_F\mathcal L_{\rm false\ alarm}
+w_T\mathcal C_{\rm latency}.
\end{aligned}
\]

The terms are:

- ranking loss on held-out human links and actual future reads;
- path-consistency divergence;
- Brier/log loss for stated confidence;
- missed unsafe act or false completion;
- false-alarm cost on ordinary actions;
- latency and compute cost.

One candidate act-weight schedule increases toward the terminal end:

\[
w_A(s,r)\propto \frac{r}{s+\varepsilon}.
\]

This schedule is an **implementation hypothesis, not a theorem of the
path-convergence paper**. That paper supplies three narrower cautions: drift error
accumulates along a same-noise path through an inhomogeneous Grönwall bound;
finite-pattern and far-tail limits need not commute; and tail exponents can vary
with bridge time. None of those results derives \(r/(s+\varepsilon)\), proves
terminal monotonicity, or identifies an optimal act weight. The schedule and its
constant therefore require prospective ablation against constant, decreasing, and
data-learned alternatives.

Optimization is constrained:

\[
\begin{aligned}
\Pr(\text{false alarm}\mid\text{ordinary act})&\le \alpha,\\
\Pr(\text{miss}\mid\text{high-risk failure})&\le \beta,\\
\text{freshness}_k(C)&\le\Delta_k(r),\\
\text{provenance coverage}(C)&\ge p_{\min},\\
\text{rollback test}&=\text{green}.
\end{aligned}
\]

An unconstrained weighted average can trade one catastrophic miss for many tiny
ranking gains. The constraints prevent that.

## 9. Stopping before collapse or memorization

Diffusion dynamics can pass through qualitatively different regimes: early
speciation, useful denoising, and late collapse/memorization. The brain should stop
when another step no longer adds evidence.

Track:

\[
H_s=-\sum_i p_s(i)\log p_s(i),\quad
S_s=\left|\{i:p_s(i)>\epsilon\}\right|,\quad
D_{\rm path}(s),\quad
P_{\rm cov}(s).
\]

Let \(G_s\) be the expected marginal utility of taking one more retrieval/corrector
step. Stop with a candidate answer only if:

\[
G_s \le c_{\rm step},\quad
D_{\rm path}(s)\le\delta,\quad
P_{\rm cov}(s)\ge p_{\min},
\]

and support has not collapsed without new provenance. If \(G_s>c_{\rm step}\), keep
working. Otherwise:

- high entropy: retrieve more;
- low entropy with poor provenance: verify rather than answer;
- high path disagreement: show alternatives or ask;
- stale required channel: resample world state;
- supersession conflict: route to the current correction;
- endpoint pressure toward a stored anecdote: stop early and report uncertainty.

The endpoint lesson from the path-convergence paper is literal here: reaching a
stored memory is not the same as reaching truth.

## 10. Guidance

Classifier-free guidance strengthens a conditional direction but can reduce
diversity or overdrive the sampler. The brain's analogue is narrative guidance from
the current goal, owner preferences, and salient memories.

Let \(v_{\rm base}\) be evidence transport and \(v_C\) conditional transport:

\[
v_{\rm guided}=v_{\rm base}+\gamma(r,Q)(v_C-v_{\rm base}).
\]

The guidance scale must fall when evidence quality \(Q\) is weak or risk \(r\) is
high. High-risk work is **less** free to follow a fluent goal narrative; it requires
more external evidence. This is the opposite of motivational overguidance.

Corrections and hard owner rules may increase retrieval priority. They may not
override live observations about the target. “The owner usually wants X” is not a
receipt that X happened.

## 11. Same-noise evaluation

The path-convergence paper compares finite and population paths under shared
Brownian noise. The engineering counterpart is paired evaluation:

- identical query;
- identical corpus snapshot;
- identical graph fold;
- identical random seed/replay draw;
- identical world-state envelope;
- one mechanism changed at a time.

Report:

- hit@\(k\), recall@\(k\), and MRR;
- per-fold deltas, not only a pooled mean;
- false-negative and false-positive rates for guards;
- calibration and path disagreement;
- latency and failure behavior;
- the exact rollback result.

The “drastic improvement” rule for this program was frozen before new candidate
runs. A result needs a large primary-endpoint gain on held-out or frozen real data,
positive behavior across folds, and no applicable safety regression. Synthetic
self-tests cannot establish it.

## 12. Implementation map

The isolated branch implements:

| Theory component | Code | Rollback/status |
|---|---|---|
| strength-weighted semantic corrector | `dreaming/brain.py::_semantic_scores` | `BRAIN_SEM_CENTROID=1` |
| separate mass/coherence/effective-count diagnostics | same function, optional `diag` dict | observational only |
| two-scale salience head and type plateau | `dreaming/salience.py` | `BRAIN_RECENCY_V1=1` |
| two-scale replay schedule | `dreaming/diffusion_replay.py` | pure module |
| dream replay integration | `dreaming/dream.py` | `BRAIN_DIFFUSION_REPLAY_V1=1` |
| Min-SNR-style cap | `score_candidates` ablation | measured, not promoted |
| scheduler/integration tests | `dreaming/test_diffusion_replay.py`, `test_diffusion_integration.py` | twenty-three deterministic tests |
| retrospective mechanism evaluator | `dreaming/eval_diffusion_replay.py` | explicit unknown-age policies; writes only with `--json-out` |
| frozen negative controls | `dreaming/eval_diffusion_controls.py` | target/type/time controls |
| same-noise selector evaluator | `dreaming/eval_diffusion_sampling.py` | shared exponential clocks |

The live main tree is not silently overwritten. Promotion requires review,
re-running the frozen gates, committing the isolated branch, and integrating under a
fresh mesh claim.

## 13. What is not yet solved

1. A prospective temporal holdout long enough to validate the replay gain
   independently.
2. A typed act-envelope corpus with both true and false completions, so the
   conditioning-completeness guard can be measured at a bounded false-alarm rate.
3. A multi-path candidate-disagreement implementation and calibration set.
4. A clean measure of whether replay changes what the next dream actually
   consolidates, rather than merely predicting retrieval.
5. A proof or empirical law for choosing the semantic/graph/lexical solver step
   sizes. EDM tells us to separate these knobs, not their values.
6. A principled growing-dimension law for embedding readout error. The source paper
   explicitly leaves the relevant interior regime open.
7. Continuous monitoring for memorization/collapse as the corpus and retrieval
   policy co-evolve.

## 14. Source map

Primary sources used for the design:

- *Path Convergence in Diffusion Models* (expanded 200-page
  pedagogical volume). Source for shared-noise path comparison,
  finite-sample normalizers, finite-pattern convex-hull readout, accumulated
  same-noise drift-error bounds, endpoint atomicity/quantization, and heavy-tail
  noncommuting-limit cautions.
- Yang Song et al., *Score-Based Generative Modeling through Stochastic
  Differential Equations*, ICLR 2021 oral,
  <https://arxiv.org/abs/2011.13456>. Predictor-corrector sampling and
  probability-flow ODE.
- Tero Karras et al., *Elucidating the Design Space of Diffusion-Based Generative
  Models*, NeurIPS 2022, <https://arxiv.org/abs/2206.00364>. Separation of schedule,
  solver, parameterization, preconditioning, and loss.
- Yaron Lipman et al., *Flow Matching for Generative Modeling*,
  <https://arxiv.org/abs/2210.02747>. Simulation-free vector-field learning and
  alternative probability paths, including optimal-transport paths.
- Yang Song et al., *Consistency Models*,
  <https://arxiv.org/abs/2303.01469>. One/few-step maps consistent across noise
  levels.
- Tiankai Hang et al., *Efficient Diffusion Training via Min-SNR Weighting
  Strategy*, <https://arxiv.org/abs/2303.09556>. Timestep-gradient conflict and
  SNR-clamped loss weighting; tested here as an analogy and not promoted.
- Andreas Blattmann et al., *Semi-Parametric Neural Image Synthesis*,
  <https://arxiv.org/abs/2204.11824>. Retrieval-augmented diffusion: external
  nonparametric memory conditions a smaller parametric generator.
- Giulio Biroli et al., *Dynamical Regimes of Diffusion Models*,
  <https://arxiv.org/abs/2402.18491>. Speciation and collapse/memorization regimes.
- Peng Sun et al., *Unified Continuous Generative Models for Denoising-based
  Diffusion*, submitted to ICLR 2026,
  <https://openreview.net/forum?id=xFdT63wm5e>. A unified view of diffusion, flow,
  and consistency objectives. Submission status is stated, not upgraded to
  acceptance.
- Amirmojtaba Sabour, Sanja Fidler, and Karsten Kreis, *Align Your Flow: Scaling
  Continuous-Time Flow Map Distillation*, NeurIPS 2025 poster,
  <https://openreview.net/forum?id=pzHuesCvcO>. Flow maps between arbitrary noise
  levels and few-step distillation.
- Hasan Amin et al., *Consistent Diffusion Language Models*, submitted to ICLR
  2026, <https://openreview.net/forum?id=inXScPz1gT>. Exact discrete posterior
  bridges and multi-path consistency. Submission status is stated, not upgraded to
  acceptance.

A paper archive carries local PDFs, hashes, URLs, and the same status
boundaries.
