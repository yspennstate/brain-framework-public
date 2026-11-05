# Local mathematical audit protocol

Local audit is intentionally separate from public corpus construction.

1. Create a run-specific `local/audits/<run-id>/allowlist.toml` using the exact
   schema in `privacy-boundary.md`.
2. Add only narrow roots the owner has explicitly approved as mathematical and
   in scope. The home directory and any ancestor of it are invalid roots.
3. Record a neutral source label, rights state, and content class. “Unknown”
   rights means local analysis only; publishable rights still do not authorize
   automatic export.
4. Load the manifest and run `math-brain policy check` on each candidate file.
5. Enumerate supported files without following links, opening archives, or
   recursively scanning a directory not named in the manifest.
6. Read through the stable-descriptor API, which re-checks identity,
   containment, and link components before and after the read.
7. Store findings, excerpts, prompts, and transcripts only inside that run's
   gitignored directory.
8. Report claims by local opaque identifier. Avoid absolute paths in outputs.
9. Promote nothing automatically. A human reviews a minimal export, writes its
   provenance sidecar, and confirms a concrete redistribution license.
10. Run the public-artifact and repository-file privacy gates from a fresh
    process before staging, followed by the repository secret scanner.

The phrase “all mathematical work on this computer” means all roots that pass
this protocol. It never authorizes scanning excluded or ambiguous locations.
