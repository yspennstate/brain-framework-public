# Project-memory authority and reclassification

Project memory treats a prompt as owner-authoritative only when all of these are
true:

- its provenance is `owner`;
- it has an exact non-hook transcript or rollout locator;
- its source-content HMAC equals its observed content HMAC; and
- it uses the current owner-facing projection.

`UserPromptSubmit` is an observation lane, not an authority lane. Hook rows are
stored as `unknown` and receive no project assignment. A later exact source row
may reconcile the same occurrence and promote it to owner authority. Source-less
hook rows remain preserved but inert.

Classification policy or classifier-version changes require an explicit
two-phase rebuild:

```text
python project_memory_cli.py reclassify-prepare \
  --config C:\private\projects.json \
  --prior-config C:\private\projects.prior.json

python project_memory_cli.py reclassify-apply \
  C:\private\classification-rebuilds\candidate-....json \
  --config C:\private\projects.json
```

If the only policy change was adding one or more projects and the exact prior
file is unavailable, preparation can reconstruct the prior semantic view:

```text
python project_memory_cli.py reclassify-prepare \
  --config C:\private\projects.json \
  --prior-without-project newly-added-project
```

The reconstructed legacy digest must exactly equal the digest already bound in
the live database; naming the wrong project therefore fails before source
reconciliation or candidate creation.

Both commands report bounded progress without printing source identifiers or
prompt text. Reapplying an already committed artifact verifies the receipt,
assignment digest, active project set, cache HMAC, cache hash, freshness, and
event manifest before returning without rebuilding the cache. Any post-apply
assignment or classification drift fails closed.

Preparation may append newly discovered source occurrences, reconcile exact hook
observations, and advance source manifests. It does not change live assignments
or the live classification digest. It rereads every authoritative occurrence,
verifies source HMACs and the complete span/clause projection, computes
session-prior labels from the previous four authoritative events, and writes a
private candidate SQLite file plus an HMAC-signed envelope. Neither artifact
contains raw prompt text.

Application rechecks the envelope signature, candidate hash, exact config bytes,
live classification digest, complete classification-input digest, assignment
digest, clause coverage, project IDs, and quarantine count. It then replaces all
assignments, demotes source-less legacy owner-hook rows, updates classifier
metadata, records a rebuild receipt, and marks every review for republication in
one `BEGIN IMMEDIATE` transaction. The signed hot cache is made stale before the
swap and rebuilt only after the committed state is current.

The candidate and envelope must remain under the deployment-private state root.
Never commit or copy them into the framework repository.
