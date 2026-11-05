# Privacy and publication boundary

The source gate and the publication gate are separate. Passing the source gate
means a file may be read for one local audit. It does not mean its content,
metadata, summary, embedding, or any other derivation may be committed.

## Run-local source manifest

Each audit creates exactly one manifest at
`local/audits/<audit-id>/allowlist.toml`. The parser rejects missing and unknown
fields, unsupported schema versions, relative source roots, duplicate source
identifiers or roots, unrecognized rights states, link components, and broad
roots that would include the home directory. A valid template is:

```toml
schema_version = 1
audit_id = "audit-001"
derivation_policy = "local_only"
no_home_recursion = true
local_output_root = "local/audits/audit-001"

[[sources]]
id = "source-001"
label = "Owner-approved mathematical source"
root = "<absolute-approved-math-root>"
rights = "private"
content_class = "pure_mathematics"
max_file_bytes = 5000000
```

The angle-bracket value must be replaced locally with the exact approved root.
It is deliberately not a working committed path. Accepted rights states are
`unknown`, `private`, `local_analysis_only`, `original_public_release`,
`permissive_license`, and `public_domain`. Regardless of that field, every
derivation remains local until it independently passes the publication gate.

The command-line check requires the manifest and one source identifier:

```powershell
math-brain policy check $SOURCE_FILE --manifest $AUDIT_MANIFEST `
  --source-id source-001 --repository-root .
```

There is no command that walks the home directory. The publication scanner also
refuses directory arguments; its caller must enumerate repository-relative
files explicitly.

## Link and race defense

Eligibility checks inspect every existing path component without following
reparse points. Symlinks and Windows junctions are denied even when they point
back inside an approved root. Multiply linked files are denied because their
other names cannot be established from one pathname. Alternate data-stream
syntax, drive-relative paths, trailing-dot aliases, and reserved device names
are also denied before resolution. Network shares and device-namespace paths are
not accepted as source or output roots.

An approved read then opens the file once and reads only through that descriptor.
Before reading any byte, the gate compares the descriptor's device, file
identity, mode, size, and modification time with the checked pathname. After the
read, it repeats descriptor, containment, and component checks. Replacement,
rename, link insertion, and ordinary concurrent modification therefore fail
closed. This is defense against accidental and cooperative local races, not a
sandbox against a malicious process running with the same operating-system
identity.

## Local derivations

Derived findings, excerpts, prompts, transcripts, and reports may be created
only below the manifest's `local_output_root`. The writer uses exclusive create,
private permissions, and no-follow flags where the platform supports them. It
never overwrites an existing result and never creates parent directories on the
caller's behalf. `local/` is gitignored, but the path gate remains mandatory;
ignore rules alone are not a security boundary.

## Public export sidecar

Every artifact below `data/public/` needs a sibling named
`<artifact>.provenance.toml`. The sidecar has this exact schema:

```toml
schema_version = 1
artifact = "data/public/example.jsonl"
artifact_sha256 = "0000000000000000000000000000000000000000000000000000000000000000"
rights = "public_domain"
license_id = "CC0-1.0"
provenance_refs = ["generated:documented-generator-v1"]
source_ids = ["source-public-001"]
reviewer_id = "reviewer-001"
reviewed_at = "2026-07-13T12:00:00+00:00"
redistribution_confirmed = true
local_material_excluded = true
```

The displayed digest is a placeholder; the sidecar must contain the artifact's
actual lowercase SHA-256. Rights must be explicitly publishable, the license
must be a concrete redistributable identifier, provenance references must use
`https`, `doi`, `urn`, or `generated` identifiers, and a human review timestamp
with an offset is mandatory. Public-domain material additionally requires
`CC0-1.0` or `PDM-1.0`. Accepted license identifiers are an explicit allowlist in
`config/privacy.toml`; a syntactically plausible unknown identifier is denied.

The gate re-hashes the artifact, scans both files, and rejects local paths,
credential patterns, and forbidden subject categories. A clean scan is only a
necessary condition: it cannot grant rights or prove that a mathematical source
was faithfully paraphrased.

## Context-aware scanning

Policy documents must name the categories they forbid, so category matching has
an explicit, narrow context list in `config/privacy.toml`. That exemption applies
only to category words. Absolute paths and credential patterns are still errors
inside policy documents. Public data artifacts receive no category exemption.

Scanner diagnostics contain the repository-relative file, line, column, and
finding class, but never the matched value. This prevents a detected credential
from being copied into a build log. Binary and non-UTF-8 publication candidates
are denied rather than guessed at.

Before staging a public artifact:

```powershell
math-brain policy export data/public/example.jsonl `
  --manifest data/public/example.jsonl.provenance.toml --repository-root .
math-brain policy scan README.md docs/privacy-boundary.md `
  --repository-root . --config config/privacy.toml
```

The final release process must also run the repository secret scanner and repeat
the export check from a fresh clone that contains no `local/` directory.
