import Lake
open Lake DSL

package proofswarm where
  -- minimal mathlib pull; full index too heavy for POC cold-starts
  leanOptions := #[⟨`autoImplicit, false⟩]

require mathlib from git "https://github.com/leanprover-community/mathlib4.git" @ "v4.13.0"

@[default_target]
lean_lib ProofSwarm where
  roots := #[`ProofSwarm]
