# Prospective control, shadow lane

This directory implements a bounded experiment for one question: does seeing a
small, pre-work set of possible futures reduce recurrence of a known error enough
to justify the time and context it costs?

It is deliberately shadow-only. Nothing here is imported by a hook, the dream
runner, wake-up code, or the memory system. It never promotes itself. Every command
requires an explicit ledger path, and even a fully passing report says only
PROMOTION_CANDIDATE with manual review required.

## What the contract guarantees

Before work begins, one scenario event freezes exactly three mutually exclusive
trajectories:

- **expected**: the current best forecast;
- **scar**: a failure derived from at least one named scar source, with a concrete
  guard and severity;
- **pivot**: a trigger and bounded recovery action if the expected route stops
  being sensible.

The CLI, not the input JSON, stamps created_at from its append clock. The input
must supply an evidence_cutoff_at no later than that stamp, stable source slugs,
and a true work-not-started attestation. Probabilities must be positive and sum to
one. The event stores digests of both the trajectory set and source set.

The ledger is append-only JSONL with a sequence number, previous-record hash, and
record hash on every line. It fails closed on a concurrent lock, partial line,
unknown field, duplicate creation or resolution, time reversal, hash mismatch, or
semantic-contract violation. Run verify after every collection batch and archive
the reported head hash somewhere outside the ledger. The hash chain detects edits,
reordering, and insertion; an externally archived head is needed to detect removal
of a valid tail.

## Hidden control and role separation

Assignment is deterministic HMAC-SHA256 by experiment and stable unit ID. All
bundles for the same unit stay in the same arm, preventing within-session leakage.
The secret key is never stored; the ledger keeps only its SHA-256 fingerprint,
assignment, and a commitment. The fingerprint identifies a random key; it is not
the key itself.

The operator ledger contains both arms and all trajectories, so it must be private
to the allocator. The actor receives only create command output:

- a visible unit receives the frozen bundle;
- a hidden unit receives the same neutral receipt with scenario_bundle set to
  null; no arm label, trajectory prose, source slug, or guard is emitted.

This is not double-blind: an actor can tell whether a payload was supplied. Here
“hidden control” means that control content is frozen but withheld during work,
and that the later outcome adjudicator is not shown the assignment arm.

Resolution uses two roles. First, give adjudication-template output to an
adjudicator only after the work is over. It contains the frozen trajectories but
no assignment. The adjudicator supplies the blinded outcome fields. Separately,
the allocator adds the exposure-only fields guard_followed and
planning_overhead_seconds, then appends the combined resolution. Do not show those
arm-dependent exposure fields to the adjudicator.

## Safe operating procedure

Keep the assignment key and operator ledger in an access-restricted runtime
directory outside this repository. Mint unit IDs from an existing session or task
ledger before assignment; never try candidate IDs until a desired arm appears.
Use source slugs rather than raw prompts or transcripts, and keep trajectory text
short and task-specific; the writer refuses common secret shapes but is not a data
loss prevention system.

Set convenience variables for the examples:

    $PC = 'C:\path\to\prospective_control.py'
    $LEDGER = 'C:\private-runtime\prospective-control\experiment.jsonl'
    $KEY = 'C:\private-runtime\prospective-control\assignment.key'

Generate a 32-byte key once. Do not commit or print it:

    python -c "import secrets,pathlib; pathlib.Path(r'C:\private-runtime\prospective-control\assignment.key').write_bytes(secrets.token_bytes(32))"

Start a finite experiment. end-at must be timezone-aware and in the future:

    python $PC init --ledger $LEDGER --experiment-id pc-shadow-01 `
      --end-at 2026-08-01T20:00:00Z --assignment-key-file $KEY `
      --max-bundles 120 --visible-fraction 0.5 --resolution-grace-days 14

Prepare scenario.json before doing the task:

    {
      "bundle_id": "case-0001",
      "unit_id": "session-0001",
      "unit_source_slug": "session/session-0001",
      "author_id": "planner-01",
      "evidence_cutoff_at": "2026-07-10T08:00:00Z",
      "work_not_started_attestation": true,
      "trajectories": [
        {
          "kind": "expected",
          "probability": 0.65,
          "prediction": "The scoped implementation passes its declared checks",
          "observable": "All declared verification commands exit successfully",
          "source_slugs": ["project_current_state"]
        },
        {
          "kind": "scar",
          "probability": 0.25,
          "prediction": "An old worktree failure contaminates the scoped change",
          "observable": "The scoped diff contains unrelated changes",
          "source_slugs": ["project_current_state", "scar_worktree_contamination"],
          "scar_source_slugs": ["scar_worktree_contamination"],
          "severity": 3,
          "guard": {
            "guard_id": "verify-clean-scope",
            "action": "Require a clean scoped diff before editing",
            "error_signature": "dirty_worktree"
          }
        },
        {
          "kind": "pivot",
          "probability": 0.10,
          "prediction": "A sparse checkout becomes the bounded recovery path",
          "observable": "The full checkout exceeds its declared time budget",
          "source_slugs": ["repo_scale_observation"],
          "trigger": "The full isolated checkout misses its time budget",
          "pivot_action": "Use a sparse worktree at the same pinned commit"
        }
      ]
    }

Freeze and allocate it, saving only the returned actor receipt for the actor:

    python $PC create --ledger $LEDGER --input .\scenario.json `
      --assignment-key-file $KEY

After work, export the arm-blind adjudication view:

    python $PC adjudication-template --ledger $LEDGER --bundle-id case-0001

The final resolution JSON is flat, but its provenance is two-part as described
above. A hidden unit must use null for guard_followed and zero for planning
overhead; a visible eligible unit must use a boolean adherence value.

    {
      "bundle_id": "case-0001",
      "work_started_at": "2026-07-10T08:05:00Z",
      "observed_kind": "expected",
      "adjudicator_id": "adjudicator-02",
      "adjudicator_blinded_to_arm": true,
      "guarded_error_eligible": true,
      "guarded_error_recurred": false,
      "guard_followed": true,
      "planning_overhead_seconds": 42.0,
      "critical_harm": false,
      "outcome_evidence_slugs": ["outcome/case-0001"]
    }

Append, verify, and report:

    python $PC resolve --ledger $LEDGER --input .\resolution.json
    python $PC verify --ledger $LEDGER --assignment-key-file $KEY
    python $PC report --ledger $LEDGER

The assignment key is optional for verify. Without it, the command checks the
complete hash chain and semantic contract but labels assignment_status as
not_checked.

## Metrics and gates

For observed class o and frozen class probabilities p, this module reports the
unnormalized multiclass Brier score

    sum_k (p_k - 1[k=o])^2

and natural-log loss

    -ln(p_o).

Both are proper scoring rules; lower is better. Reports include event means and
unit-balanced means. The default forecast gates are slightly stricter than the
uniform three-class baselines of 2/3 Brier and ln(3) log loss.

Guarded-error recurrence is intention-to-treat. Eligible outcomes are grouped by
stable unit first, then averaged so one high-volume session cannot dominate. The
visible-minus-hidden difference receives a deterministic cluster bootstrap 95%
interval. Promotion requires its upper endpoint below zero, not merely a favorable
point estimate.

Other default gates require closed enrollment, finalized outcome collection, at
least 14 elapsed days, 60 resolved bundles, 30 independent units, 12 eligible
units per arm, at least 90% resolution, low arm-differential attrition, bounded
unit concentration, forecast quality, bounded visible overhead, scar-source
diversity and reuse, zero critical harms, and few late resolutions. A critical
harm, or a bootstrap interval entirely above zero after arm-size maturity, forces
HOLD immediately. DESIGN.md defines every gate and limitation.

## Tests

From this directory:

    python -B -m unittest discover -s tests -v

The suite is hermetic: temporary ledgers only, no model, network, hooks, live
memory, background process, or scheduled task.
