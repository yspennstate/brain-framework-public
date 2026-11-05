# graph_fire - the mistake-firing layer

Built 2026-07-11 (crash-recovery session 89f84043), hardened over a 10-hour math pass the same day.
This is the FIRING layer of the gut - it decides when a reflex fires. It does not own reflex CONTENT
(instincts.tsv / INSTINCTS.md - the dream and the reflex-compilation agents own those).

## The problem it solves

The brain's reflexes are good; they just weren't firing at the moment of the mistake. The day's worst
correction - a grid backtest reported ~10x under the measured record - happened with `sim_flatters` and
`risk_vs_measured_record` both defined and both silent, because the number lived in assistant prose, a surface
the old regex hook never watched. Coverage on paper is not firing in practice. Three gaps:

1. Surface: the old `instinct_prime` fired only on tool calls (Bash/Edit), never on the outgoing message.
2. Match: pure per-reflex regex - `heredoc` never woke the `CRLF` scar.
3. No graph, no accumulation: reflexes matched independently; being near one scar didn't raise a related.

## Architecture (two surfaces, different economics)

- **Command surface** (`instinct_prime.py` PreToolUse, fires per tool call - nagging is the cost): the
  regex path UNION the graph path, with per-reflex CALIBRATED thresholds so it doesn't over-fire.
- **Report surface** (`pre_report_check.py` Stop hook, fires once a turn on outgoing prose - a wrong
  number reaching the owner is the worst case): high-precision structural checks that BLOCK, plus a more
  sensitive graph pass. This is where the real value is.

All gated by the file `graph_fire/ENABLED` - delete it to revert everything to shadow (log-only), no code
change. Every entry point is fail-safe: any error -> silent no-op, the regex path is never affected.

## The math

**Graph-derived token model** (`build_anchors.py` -> `reflex_anchors.json`). Offline: each reflex is
anchored to memory nodes (its `source` slug if it's a graph node, else nodes whose slug-vocabulary
overlaps the reflex's own trigger/gut text - this rescues the 20/56 reflexes whose source is the
un-embedded ledger MISTAKES/PREDICTIONS). Activation diffuses from the anchors over `spread_edges.tsv`
(2-hop BFS, decay 0.5 - the same diffusion `spread_recall` does at recall time) and is projected onto
vocabulary, giving a reflex -> {token: weight} vector. At runtime firing is a cheap token dot-product, so
it generalises past the literal regex. The heavy graph work is precomputed; the hot path stays fast (+3ms).

**Per-reflex calibration** (`calibrate.py` -> `reflex_anchors_cal.json`). A single threshold is wrong:
`leveraged_etf_is_not_linear` (tokens etf/carry/reset) is specific and safe to fire readily, while
`confirm_the_target` (grid/fleet/machine) is so broad it fires on anything. Each reflex gets its own
threshold, the smallest that keeps its false-alarm rate on real typical actions under a 2% budget (an
FDR-style control). This cut the union's nag from 0.84 back to the regex baseline while keeping recall.

**Conscious<->subconscious bridge** (`activation_buffer.py`, per-session files). One channel joins the two
layers: recall surfacing a scar (`spread_recall.py`) deposits it, which LOWERS the bar for the reflex
anchored on it; the gut firing a reflex deposits its scar, which recall can then surface. A `danger`
scalar ramps on real failures and decays over ~20 min (the measured self-excitation timescale), lowering
thresholds session-wide - caution that builds after real trouble, not after every caution.

**Audit caveat (07-12): the danger leg is effectively vestigial in practice.** It ramps only when a command
matches a recent failure's exact 20-40 char prefix (`recent_repeat` in forward_feed), which misses modified
retries (you usually FIX the command when you retry, so the prefix changes) - it has fired ~once ever, so
`danger=0.00` across all 15 live-session buffers (mine included, despite a failure-heavy session). And even
when it ramps, the command-surface `score>=2.6` gate nullifies it: verified that `danger=2.0` let a
2.14-scoring reflex into `graph_fire` (threshold 2.30->1.98) but `_augment_graph`'s gate then filtered it,
so the only live effect is on the report-surface graph pass, which is marginal. So the bridge's WORKING leg
is the priming below, not the danger accumulator. Activating danger meaningfully would need a broader trigger
(over-fire risk, same wall as the grind-detector) AND a danger-aware gate (over-nag on the marginal graph) -
not worth it for this corpus. The nodes/priming channel below is the leg that carries the real value.

Verified
end-to-end (07-11, beyond the buffer roundtrip): a reflex scoring 2.14 against its 2.30 threshold does NOT
fire until the conscious layer deposits its scar, which drops the bar to 1.50 (primed 0.80) and it fires -
the cross-layer priming demonstrably changes firing, the thing three dreams flagged as unverifiable from
inside a dream.

**Report-surface structural checks** (`pre_report.py`) - where precision actually lives, because the
number/claim is present. Each compares a reported statistic to the measured record (`lived_anchors.json`) and
BLOCKS once/session at 0-0.3% false-fire:
- a $ figure contradicting an anchor by >2x either way (undershoot is as damning as flatter);
- a drawdown far worse than the measured worst in the anchors;
- a P&L sign contradicting the measured, profitable record;
- a recommendation that breaks a stated strategy rule (for example, selling what the rule says is buy-only);
- an ungrounded completion claim (finalized/populated with no verifying observation this turn).

**Forward-feed** (`forward_feed.py`): a Bash command with a real `C:\...` drive path (Git Bash mangles it -
hit 3 sessions in one day) fires before it runs; a command resembling a just-failed one surfaces the error.

## Honest evaluation (the methodology matters more than any number)

Measure against REAL history, never authored cases (`eval_real.py`): 52 labelled corrections in MISTAKES.md,
700 ordinary tool calls sampled from transcripts as the benign population, outcome failures for
generalization. The first draft claimed "9/10, 0 false-fire" on 10 cases I wrote myself - it was lying the
way a flattering sim lies. Real data showed the broad model firing on 80% of typical actions.

Findings, all measured, several against my own hopes:
- **The command-surface graph layer is MARGINAL** (re-measured on 2500 real actions - the first 700-sample
  under-counted). The pre-existing regex already fires on ~63% of typical actions; the calibrated graph adds
  ~0 recall and +1.6% nag over it, and as a precision filter it stays on the same recall-nag frontier - it
  has no discriminative signal beyond the regex, because mistakes and typical actions aren't separable by
  token content. So the graph is gated to nag-neutral (fires on the command surface only when a recalled
  scar primes it - the bridge). The value is the STRUCTURAL report-surface checks, not the graph.
- **The report-surface blocker had to be hardened against REAL prose.** On 2500 actual assistant messages
  it first false-blocked 0.75% (domain-analysis prose mentions its numbers constantly) - the
  healer-first-do-no-harm outage. Tightened every branch to require an ASSERTION, not a mention (a number
  needs the anchor subject NEAR it + an attached rate + >4x gap; drawdown/sign/sell-vol/levetf need their
  trigger words adjacent; a peer referee's mention-vs-act gate suppresses correction/retraction prose).
  Result: 0.040% false-block, and it blocks the REAL 07-11 grid-undershoot message end-to-end (verified
  against the actual transcript, not a paraphrase - the paraphrase passed while the real thing first didn't).
- **Personalized PageRank is WORSE than 2-hop BFS** here: global spread washes out each reflex's
  specificity; local structure wins for discrimination. Kept BFS.
- **Learning the weights does NOT help** and would hurt: the only labelled data is prose (MISTAKES.md)
  while the runtime surface is actions, so a logistic trained on it learns prose-vs-command surface
  (top features: lesson/wrong/please) at a bogus recall of 1.00. A surface-invariant graph prior is
  NECESSARY, not just preferable. (`learn_probe.py` carries the demonstration.)
- **Failures cluster** (outcomes.jsonl): median consecutive gap ~21 min vs ~43 under a Poisson null -
  which is why the danger half-life is ~20 min.

The lesson under all of it: a mistake-catcher must be measured against real actions, or it over-fires
while looking perfect. Every surprising positive here got broken before it was believed.

## Rebuild / operate

    python build_anchors.py                              # reflex_anchors.json (BFS diffusion)
    python calibrate.py --budget=0.02                    # reflex_anchors_cal.json (per-reflex thresholds)
    python sample_actions.py                             # refresh the typical-action negatives
    python eval_real.py --cal      # held-out regex vs calibrated vs union   (--sweep, --nag also)
    python shadow_eval.py          # named-case check     |  *.py --selftest  # per-module fail-safe
    python monitor.py              # count the deployed watcher's REAL interventions (healer-first)
    python recalibrate.py          # adapt on fresh negatives; refresh anchors when reflexes change
    rm ENABLED                     # revert everything to shadow (log-only), instantly, no code change

The block-FP eval negatives are `prose_negatives.jsonl` (real assistant messages); regenerate with the
sampler in `monitor`/`eval_real`. Test session ids MUST be non-hex-prefixed (e.g. `zztest-...`) or they
pollute the durable ledgers. `monitor.py` is the healer-first gate: if it ever shows a real-session block
that is a correct message, tighten that branch or `rm ENABLED`.

Rebuilding anchors requires re-running calibrate (it reads reflex_anchors.json). gfcore loads the
calibrated model if present, else the uniform one. Knobs live at the top of each module.

## Known limits

- A lookahead written as bare code (`df[df.vix<15]`) with no trigger words is still missed (needs AST).
- The report-surface checks can't tell REPORTING a wrong number from DISCUSSING one (mention-vs-act at Stop).
- The command-surface "outcome recall" (~0.4) understates the real job: of the failures it misses, ~80% are
  generic one-off bugs (tracebacks, file-not-found, 65% of them a `cd dir && python x.py` that threw) - NOT
  a recurring mistake CLASS a reflex should pre-empt, and the ERN (error_monitor) catches them post-hoc
  anyway. The pre-execution firing exists to catch RECURRING classes (a flattering number, a backslash
  path, a lookahead); it is not, and should not be, a generic bug predictor.
- Precision on typical actions is measured against a sample that is itself scar-dense (the fleet works in
  guarded domains), so the nag numbers are an upper bound - some "nag" is a legitimate caution.
- The shadow log `gf_shadow_fires.tsv` and `instinct_fires.tsv` only skip test fixtures by a uuid-shape
  check (`^[0-9a-f]{8}-`). Test session ids that happen to be hex-shaped (e.g. `deadbeef-...`) SLIP THROUGH
  and pollute the durable ledgers - I did exactly this while validating (the recurring fixtures-pollute-the-
  ledger scar). Use a non-hex prefix (e.g. `zztest-...`) for test session ids, and treat the current shadow
  counts as inflated until a few days of clean live logging accumulate. As of the build, ALL 8 Stop-hook
  blocks in the log were my own test strings - zero real fleet messages were blocked.
