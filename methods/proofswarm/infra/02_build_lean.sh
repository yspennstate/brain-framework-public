#!/usr/bin/env bash
# Historical Cloud Build / Cloud Run deployment tombstone.
#
# The local ProofSwarm may use only a preprovisioned local Lean toolchain.  It
# must never build, push, or deploy a verifier through a cloud fallback.
set -euo pipefail

printf '%s\n' \
  'DISABLED: ProofSwarm cloud build and deployment are forbidden.' \
  'Use the preprovisioned local Lean project; no remote request was made.' >&2
exit 64
