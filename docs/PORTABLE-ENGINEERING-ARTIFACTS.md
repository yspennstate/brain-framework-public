# Bounded, verified source handoffs

`dreaming/portable_artifact.py` moves explicitly allowlisted technical source and
clean fixtures between agents that have repository access and a native executor
but no shared filesystem. It supplies no network access or credentials and does
not execute the transferred files.

Build from an exact Git commit. Review and scan decoded inputs before packaging;
also scan the resulting transport. The deterministic manifest binds file sizes,
original Git identities and SHA-256 hashes. Bounded compressed bytes travel as
small lowercase-hex text files. An independently trusted manifest pin is required
to restore a new directory. Traversal, links/junctions, device aliases, collisions,
corruption, reordering, missing parts and oversized expansion are rejected.

```sh
python dreaming/portable_artifact.py build-git --repository REPO --revision EXACT_COMMIT --include ALLOWED_PATH --output NEW_BUNDLE
python dreaming/portable_artifact.py restore --bundle FETCHED_BUNDLE --manifest-sha256 TRUSTED_PIN --revision EXACT_COMMIT --destination NEW_DIRECTORY
```

Use actual observed values. A digest establishes bytes only when its expected
value has an independent trusted origin; a bundle cannot authorize itself.
Source restoration is not runtime startup, retrieval, cross-run memory or
independent acceptance. Execute those stages and record their own outcomes.

`fragment-source` derives one bundle per original file. `hydrate` then fills only
missing exact files in an isolated partial source tree, checking against the
separately pinned complete reference. Conflicting existing contents are never
overwritten. This lets a bounded invocation finish its actual missing dependency
without transferring the whole package again.

The 25 synthetic tests cover round trips, resumed transfers, malformed input,
conflicting output, genuine Git-object reads from a dirty checkout and fragment
authorization. Run:

```sh
python -B -m unittest discover -s tests -p test_portable_artifact.py -v
python scripts/check_no_private_labels.py
```

Keep private prompts, memory populations, operational identifiers and user source
bundles out of a generic framework transfer. This utility does not change any
consumer product's storage, permissions, deployment or connection contract.
