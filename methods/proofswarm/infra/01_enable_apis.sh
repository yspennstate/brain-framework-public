#!/usr/bin/env bash
# Historical cloud bootstrap tombstone.
#
# ProofSwarm is owner-directed local-only infrastructure.  This path used to
# enable billable Google Cloud APIs and is intentionally non-operational.
set -euo pipefail

printf '%s\n' \
  'DISABLED: ProofSwarm is local-only; cloud API provisioning is forbidden.' \
  'No services were enabled and no remote request was made.' >&2
exit 64
