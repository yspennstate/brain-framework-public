# Provenance and publication scope

This directory is a newly authored, self-contained reference implementation.
Its code, documentation and synthetic tests are covered by this repository's
root MIT license (copyright 2026 Yitzchak Shmalo). No third-party code was vendored.
Dependencies are Python's standard library and an operator-installed OpenSSH
client/server, which retain their own licenses and are not redistributed here.

Design context reviewed from the public repository at commit
`15ecca9e7486588c3c4b97a956ea91d42a8f8e79`:

- `framework/mesh/agent_mesh.py`: existing local SQLite leases and mailbox design.
- `framework/mesh/README.md`: existing single-computer mesh contract.
- Root `README.md` and `LICENSE`: repository purpose and license scope.

No source functions were copied from those files. No private repository code,
private tasks, private messages, real accounts, credentials, hostnames, runtime
databases, user transcripts or private research records were inputs to these
new files. All examples use synthetic identities, invented task text, reserved
`.invalid` email domains and generic illustrative paths. No service setup was
performed and no real email was sent for verification.

`MANIFEST.json` enumerates exact distributable payload paths and SHA-256 hashes.
It intentionally excludes its own bytes to avoid a circular hash; a release
review should separately hash the manifest and permit only these payloads plus
that exact manifest. Do not recursively publish the staging workspace, test
scratch directories, runtime databases, reference downloads or caches.

This is a narrow additive publication inventory, not a privacy audit or license
review of the repository's pre-existing contents. A clean candidate does not
establish that the rest of the repository or its history contains no private
information. Independently review these exact bytes before public publication.
