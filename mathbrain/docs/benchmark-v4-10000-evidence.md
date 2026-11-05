# Retained 10,000-item benchmark evidence

The v4 bank was materialized from commit `bc8d54f` with seed
`public-10000-regression` and coordinate window `[0, 10000)`. The raw canonical records remain in
ignored private storage at `local/benchmark/v4-10000/items.jsonl`; they are not a public export.

The atomic artifact contains exactly 10,000 JSONL records and 41,771,350 bytes. An independent
post-publication recount found 10,000 lines and reproduced the file digest:

- items SHA-256: `be4e6dd9904c8deac5f703e6f269b65c1e2ff5d258e03506a35102b4e33e3942`
- manifest digest: `9a8041719330c7f5b68cdf6e322844751600ac62bd10d0d8781eae10da2cfbb8`
- RFC 6962 record root: `17a2fc295a7539bc2d03a189724e7bd5360ecdb438fe3cbedb0ddddfb09dd7f6`
- RFC 6962 semantic root: `be216e2c6ba9851bb8fd470b60c707c8277521f2c20e8a6a83eed2acf58bd0b6`
- generator source hash: `c81c5eb532f9e11d11fdb2605e5786d59929c37e5a813cc13ba5385442a7fc39`
- generator bundle hash: `d676086553a762946678ce3d2c89c0cdce8258dc3142965605a93dd9f8ccd27c`

All 18 families contain 555 or 556 items. Each of the four structural mutation mechanisms contains
2,500 items, and every family/fault cell is populated. Certification checks both the independent
verifier and semantic uniqueness. Only the 2,500 `off_by_one` cases satisfy the solver-valid
shortcut audit; the other 7,500 records are retained as engineering fuzz cases and cannot enter a
scored evaluation plan.

The complete repository run that generated and independently regenerated this manifest passed 327
tests; three Windows symlink tests were skipped because the platform could not create symlinks.
Any later public export still requires the explicit human review and provenance sidecars described
in `docs/privacy-boundary.md`.
