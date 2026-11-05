# Mathematical Research Brain

This repository is a privacy-isolated memory, verification, and evaluation
system for mathematical research. It is intentionally **math only**. It must
not ingest trading material, financial or options theory, company information,
credentials, personal archives, client work, or unrelated projects.

The project is private while it is being hardened for eventual public release.
No local source is publishable merely because a scanner can read it.

## What it is for

- Store mathematical claims together with definitions, dependencies, evidence,
  counterexamples, citations, and verification state.
- Retrieve relevant prior work through lexical search and a typed association
  graph rather than a flat notes folder. Formulae are notation-normalized and
  alpha-renamed, every score has a trace, and weak or ambiguous recall abstains.
- Turn recurring mathematical failure patterns into explicit review triggers.
- Check computations independently and route formalizable statements to Lean.
- Verify that citations support the precise claims attributed to them.
- Consolidate accepted results while surfacing contradictions, orphaned claims,
  stale evidence, and missing proof obligations. SWS/REM-style replay produces
  quarantined review proposals only; it cannot create evidence or promote truth.
- Measure whether the system actually improves error detection using paired,
  blinded brain-on/brain-off evaluations.

## Non-negotiable privacy boundary

The default ingestion policy is deny. A source is admissible only when all of
the following are true:

1. it is explicitly allowlisted for a particular local audit;
2. it is mathematical rather than financial, commercial, personal, or client work;
3. its provenance and publication rights are recorded;
4. the audit output remains under the gitignored `local/` tree; and
5. a human explicitly promotes a sanitized artifact into `data/public/`.

Path names and keywords are only early warning signals. They can block a source;
they can never make a source safe to publish.

## Verification philosophy

Every important claim carries a status:

`unverified -> provisional -> verified` or `refuted`.

Verification is evidence-specific. A passing numerical example does not verify
a universal theorem; a citation match does not verify a proof; a Lean theorem
does not verify that the formal statement faithfully represents the prose.
Results are promoted only when the evidence covers the scope of the claim.

## Repository map

- `src/math_brain/` — privacy gate, schemas, graph memory, retrieval, triggers,
  verification, consolidation, benchmark generation, and evaluation.
- `config/` — public-safe policy and trigger definitions.
- `docs/` — architecture, threat model, audit protocol, and corrected design brief.
- `tests/` — deterministic unit, property-style, adversarial, and regression tests.
- `data/public/` — reviewed redistributable benchmark artifacts only.
- `local/` — gitignored local audits and transcripts.

## Quick start

```powershell
python -m pip install -e .
python -m pytest -q
$SOURCE_FILE = "<explicitly-allowlisted-source-file>"
$AUDIT_MANIFEST = "local/audits/<audit-id>/allowlist.toml"
math-brain policy check $SOURCE_FILE --manifest $AUDIT_MANIFEST `
  --source-id source-001 --repository-root .
```

The command above checks one file against one source grant; it does not discover,
upload, copy, or publish anything. See `docs/local-audit-protocol.md` and
`docs/privacy-boundary.md` before auditing any local source.
Evaluation recall must also use a `RetrievalGuard`; it rejects test/answer roles,
the active or paired item, quarantined records, and unlabeled corpus splits.
See `docs/retrieval-and-consolidation.md` for the trusted interfaces.

## Release status

Private research preview. The source and generated public benchmark will need a
final privacy, provenance, license, citation, and reproducibility audit before
the repository can be made public.

Hard challenge v2 is retained only as a falsified development artifact. Two
solver-view-only decoders recover its fault labels—and one recovers the complete
expected answer—on 320/320 cases without doing the mathematics. Its selected
pilot was stopped before the first selected attempt and is disabled in code.
The leakage-resistant successor contract and its coefficient-aware, label-isolated
relational gate, paired-dispatch plan, exact three-source claim core, typed
forced-recurrence binding, pre-axis binding-bank manifest, and independently
committed answer-axis plan are recorded in
`docs/hard-challenge-v3-design.md`; no v3 selected collection is authorized.
