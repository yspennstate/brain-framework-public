# Shadow prospective-control design

## Decision this experiment is allowed to make

The experiment may produce only three verdicts:

- **DATA_GATED**: enrollment or the resolution grace window is still open and
  evidence is immature;
- **HOLD**: outcome collection is finalized without every gate passing, or a
  qualified adverse signal has appeared;
- **PROMOTION_CANDIDATE**: every preregistered gate passes after closure.

The last verdict is not deployment authority. It is an invitation to independent
review and a separately authorized integration experiment. The report always sets
automatic_integration_permitted to false.

The primary causal hypothesis is that showing the frozen three-trajectory bundle
reduces recurrence of the scar-named error among eligible tasks. Secondary
hypotheses are that forecasts beat a uniform three-class baseline, planning cost is
bounded, and scar primitives can be reused across independent units. These are
tested together because a planning ritual that sounds insightful but does not
improve guarded outcomes is not useful.

## Research translation, with a hard boundary

Three recent primary studies motivate engineering choices here. None validates
this software as a model of a brain, and none supplies evidence that this exact
intervention will help an AI coding workflow.

1. [A recurrent network model of planning explains hippocampal replay and human behavior](https://www.nature.com/articles/s41593-024-01675-7)
   (Jensen, Hennequin, and Mattar, Nature Neuroscience, 2024) gives a meta-RL agent
   policy rollouts and reports that it learns when their behavioral value justifies
   their time cost. Translation: allocate planning against a no-payload control,
   cap the bundle at three trajectories, measure visible-arm overhead, and require
   downstream recurrence benefit. We do not implement their recurrent network,
   policy, maze, or neural interpretation.

2. [Constructing future behavior in the hippocampal formation through composition and replay](https://www.nature.com/articles/s41593-025-01908-3)
   (Bakermans et al., Nature Neuroscience, 2025) reports a model and recordings in
   which replay helps construct compositional memories from reusable building
   blocks and supports generalization to new configurations. Translation: each
   trajectory binds stable source slugs, scar sources are explicit, and promotion
   requires multiple distinct scar sources with reuse across independent units.
   A slug is provenance, not a neuron, place field, or proof of compositionality.

3. [Exploring replay](https://www.nature.com/articles/s41467-025-56731-y)
   (Antonov and Dayan, Nature Communications, 2025) extends DYNA-like replay into
   belief space under uncertainty and studies prioritization for future choice.
   Translation: freeze probabilities before evidence arrives; include an explicit
   pivot for uncertainty; compute scar priority as frozen probability times a
   bounded severity; score the realized class later. We do not implement their
   Bayesian belief-state planner, value updates, or biological replay claims.

The translation is intentionally modest: these papers suggest variables worth
measuring and controls worth having. Causal evidence for this system must come from
this system's randomized outcomes.

## Experimental unit and allocation

Assignment is clustered by unit_id rather than bundle_id. A unit should be an
already-existing session or task container obtained from unit_source_slug before
the allocator sees the trajectories. HMAC-SHA256 maps experiment ID and unit ID to
one stable arm at the preregistered visible fraction. Trying IDs until one yields a
desired arm invalidates the experiment; software cannot prove the operator did not
do this, so the external unit ledger is part of the audit evidence.

The assignment key is at least 32 bytes and remains outside the ledger. The ledger
stores a key ID and full assignment commitment. verify can recompute every arm and
commitment when given the key.

Hidden controls are content holdouts, not deception. The control trajectories are
frozen in the operator ledger but not returned in the actor receipt. The receipt
contains no arm label. The actor can still observe that scenario_bundle is null;
therefore treatment receipt is not blinded. Outcome adjudication is arm-blind.

## Event state machine and immutability

One ledger contains one experiment and these event types:

1. experiment_started fixes start, end, maximum bundle count, visible fraction,
   resolution grace, key ID, and promotion gates.
2. scenario_created fixes append-clock created_at, evidence cutoff, unit and source
   provenance, author, exactly three normalized trajectories, probabilities,
   guard, pivot, digests, planning policy, and assignment.
3. scenario_resolved fixes append-clock resolved_at, work start, observed class,
   blinded adjudicator attestation, guarded-error outcome, exposure metadata,
   harm, outcome evidence slugs, lag, and whether adjudication missed the deadline.

There is no update or delete event. Duplicate bundle IDs and second resolutions are
refused. Input cannot set created_at or resolved_at; those timestamps are generated
inside the append operation. work_started_at must be strictly later than creation,
must remain inside the experiment window, and resolution must be strictly later
than work start. New scenarios stop at end_at or max_bundles. Late outcomes are
retained rather than erased, but the late-resolution gate makes them adverse
evidence.

Every JSONL record has exact fields, a contiguous sequence, previous hash, and
SHA-256 record hash over canonical JSON. The writer uses an exclusive side lock,
appends one line, flushes, and fsyncs. It never breaks a stale lock automatically.
This provides operational tamper evidence, not authenticated logging: a filesystem
administrator could rewrite all records and hashes. Archive verify's head hash in
an independent system if tail-truncation detection matters.

## Blinded resolution protocol

The actor and adjudicator must not have operator-ledger access.

1. After work, the operator exports adjudication-template. It reveals the frozen
   trajectories and source provenance but not assignment or exposure.
2. The adjudicator reviews outcome evidence and returns only work_started_at,
   observed_kind, adjudicator identity and blind attestation, eligibility,
   recurrence, harm, and outcome evidence slugs.
3. The allocator separately obtains guard adherence and planning overhead from the
   exposure record. It merges those two fields into the adjudicator result and
   calls resolve.

The code enforces arm invariants: hidden units have no guard adherence value and
zero planning overhead; visible eligible units have boolean adherence. This flat
event is intentionally simple, but procedural role separation remains necessary.
A future production design should use separately signed adjudication and exposure
events if the shadow lane demonstrates value.

## Scores

For three frozen probabilities p and one-hot realized outcome y:

    Brier = sum_k (p_k - y_k)^2
    log loss = -ln(p_observed)

The Brier score is the unnormalized multiclass convention, ranging from zero to
two. Both scores are proper: changing a probability after work is impossible
without breaking the record hash and trajectory digest. Reports show event means
for transparency and unit-balanced means for gates so prolific units do not get
extra calibration weight.

Guarded-error recurrence is evaluated only where the scar error was genuinely
eligible. Within each randomized unit, recurrence is averaged across eligible
bundles; unit rates are then averaged within arm. The effect is visible minus
hidden, so negative is favorable. A deterministic nonparametric cluster bootstrap
resamples units independently within arms for a 95% percentile interval. This is a
small, auditable estimator, not a claim of asymptotic optimality or a substitute
for design review.

## Default promotion gates

All gates must pass simultaneously after experiment closure:

| Gate | Default | Purpose |
| --- | ---: | --- |
| Enrollment closed | true | Prevent optional early stopping |
| Outcome collection finalized | true | Wait for all outcomes or the grace deadline |
| Elapsed duration | at least 14 days | Cover temporal variation |
| Resolved bundles | at least 60 | Basic outcome maturity |
| Independent units | at least 30 | Avoid pseudo-replication |
| Eligible units per arm | at least 12 | Make arm comparison meaningful |
| Resolution fraction | at least 0.90 | Limit outcome attrition |
| Arm resolution-rate gap | at most 0.10 | Limit differential attrition |
| Largest resolved-unit share | at most 0.10 | Limit unit domination |
| Unit-balanced mean Brier | at most 0.60 | Beat uniform Brier 0.667 |
| Unit-balanced mean log loss | at most 1.00 nats | Beat uniform ln(3), about 1.099 |
| Visible planning overhead | at most 180 seconds | Price the intervention |
| Distinct scar sources | at least 5 | Avoid one-anecdote promotion |
| Scar sources reused across units | at least 3 | Demand cross-unit reuse |
| Critical harms | zero | Immediate adverse gate |
| Late resolution fraction | at most 0.05 | Limit hindsight and stale labels |
| Recurrence effect | bootstrap CI upper endpoint below 0 | Require directionally robust benefit |

Thresholds are serialized in experiment_started and cannot change mid-run. A new
threshold set requires a new experiment and key namespace.

Two adverse signals override the normal wait-until-closure rule: any critical harm,
or, after both arms reach the configured eligible-unit floor, a recurrence
bootstrap interval whose lower endpoint is above zero, produces an immediate HOLD
report. The module reports this signal but does not mutate, stop, or deploy
anything automatically.

## Failure modes kept visible

- The work-not-started attestation and source slugs are audit claims, not proof of
  an honest planner or a complete evidence set.
- The schema enforces one expected, scar, and pivot label, but cannot prove that
  free-text trajectory descriptions are semantically exhaustive or exclusive.
- Operator access to the ledger reveals assignments and trajectories. Filesystem
  permissions and role separation are outside this module.
- An actor knows whether a bundle was received, so behavior may include treatment
  awareness. The hidden arm controls content exposure, not placebo effects.
- Blinding is an attestation in this version. There is no adjudicator signature.
- Guard eligibility and recurrence can be misclassified. Outcome evidence slugs
  make later audit possible but do not adjudicate themselves.
- HMAC assignment is random-looking only if unit IDs are fixed first and the key
  remains secret.
- The bootstrap interval can be coarse with few units; minimum-arm gates are a
  floor, not a guarantee of power.
- Proper forecast scores test calibration and sharpness, not whether a trajectory
  caused an outcome.
- A passing shadow run on one workflow does not establish generalization to other
  tasks, models, agents, or users.

These limitations are reasons for the hidden control and promotion gates, not
footnotes to waive them.

## Integration boundary

The directory owns no default runtime path and has no automatic consumer. It must
not be added to SessionStart, UserPromptSubmit, Stop, the dream scheduler, wake
digests, reconsolidation, or memory writing during this experiment. Shadow ledgers
stay outside the repository. Tests use temporary directories only.

If a completed run reaches PROMOTION_CANDIDATE, the next artifact should be an
independent audit of assignment integrity, missing outcomes, eligibility labels,
arm balance, and cost. Any live integration must be a separate change with its own
claims, rollback plan, and owner authorization.
