#!/usr/bin/env bash
# Historical Firestore / object-storage bootstrap tombstone.
#
# ProofSwarm tickets and traces remain on the local filesystem.  This path is
# retained only to make accidental execution fail before invoking gcloud.
set -euo pipefail

printf '%s\n' \
  'DISABLED: ProofSwarm remote persistence is forbidden.' \
  'No database or bucket was created and no remote request was made.' >&2
exit 64
