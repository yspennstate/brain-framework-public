# Formal verification

The repository pins Lean in `formal/lean-toolchain` and keeps the initial trusted
surface independent of external theorem libraries. The toolchain is managed by
Elan; the official Lean manual describes project-local toolchain selection and
the `ELAN_HOME` isolation mechanism:
<https://lean-lang.org/doc/reference/latest/Build-Tools-and-Distribution/Managing-Toolchains-with-Elan/>.

Run the formal smoke project with:

```powershell
cd formal
lake build
```

The Python adapter can invoke either `lean` directly or a pinned project command
such as `lake env lean`. It records the source hash, exact command, toolchain
version output, return code, and diagnostics. Source containing `sorry`, `admit`,
or `sorryAx` is rejected even if Lean exits successfully.

## What a kernel check proves

A successful run proves that the formal theorem was accepted by the selected
Lean kernel under its recorded imports and axioms. It does not prove that the
formal statement faithfully represents the prose theorem. It also does not
validate a paper citation or a numerical experiment that motivated the theorem.

Promotion therefore requires two different evidence records:

1. `lean_kernel` — the pinned source compiles without placeholders; and
2. `formalization_fidelity` — a reviewer compares definitions, domains,
   assumptions, quantifiers, conclusion, and boundary cases against hashed prose.

The review record fails unless every dimension passes. Notes cannot override a
failed checkbox; the formal statement must be repaired or the prose claim narrowed.

## Reproducibility gate

A release certificate includes the Lean source-tree hash, `lean-toolchain`, Lake
manifest when dependencies exist, operating-system and architecture label, and
complete build output. A second clean environment reruns the project. Only the
kernel result is portable; machine hostnames and local absolute paths are removed
from public certificates.
