# Threat model

## Assets to protect

- all trading, financial, company, client, credential, and unrelated-project data;
- private mathematical manuscripts and unpublished ideas not approved for release;
- local paths and metadata that reveal private projects;
- benchmark answer keys and evaluation assignment;
- citation excerpts whose licenses do not permit redistribution.

## Main failure modes

1. **Over-broad discovery.** A recursive scan walks the home directory and reads
   material outside the mathematical allowlist.
2. **Path escape.** A symlink, junction, hardlink, archive member, or relative path
   escapes an approved root.
3. **Derived leakage.** A report omits raw files but includes proprietary excerpts,
   equations, identifiers, filenames, or embeddings.
4. **Promotion confusion.** “Mathematical” is mistaken for “publishable.”
5. **Model transcript leakage.** Local context appears in a saved prompt or run log.
6. **Benchmark leakage.** A solver sees answer keys, paired variants, or previous runs.
7. **Citation laundering.** Unsafely copied source text enters a public artifact.
8. **Check/open race.** An approved pathname is replaced or redirected after a
   policy decision but before the reader opens it.
9. **Diagnostic leakage.** A scanner finds a credential and then prints the
   matched value into a durable log.

## Controls

- strict per-run TOML source grants and hard exclusions;
- no home-root or implicit directory recursion;
- component, containment, hardlink, and stable-descriptor identity checks before
  and after every read;
- no automatic home-directory recursion;
- local-only output root with a repository escape check;
- content hashes and minimal metadata instead of excerpts by default;
- explicit human promotion into `data/public/`;
- provenance sidecars with an artifact hash, explicit rights, license, reviewer,
  and redistribution confirmation;
- secret, path, category, and provenance scans before commit, with matched
  values suppressed from diagnostics;
- fresh solver contexts and separated answer-key storage;
- immutable verification events rather than in-place status rewriting.

## Residual risk

No keyword filter proves that content is safe. No automated license classifier
grants publication rights. The public-release gate therefore requires manual
review of every promoted artifact and a clean-room reproducibility run from a
fresh clone containing no local audit directory.

Filesystem checks reduce accidental and cooperative path races; they are not a
security sandbox against a malicious process running as the same user. Release
review must therefore still inspect provenance and reproduce artifacts from
public inputs alone.
