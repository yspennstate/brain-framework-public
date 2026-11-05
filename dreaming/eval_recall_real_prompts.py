#!/usr/bin/env python3
"""eval_recall_real_prompts.py - the SHIPPED recall path, on real owner prompts.

Why this file exists (2026-09-14). eval_prompt_recall_fullstack.py answers a different question: it
reimplements the blend from the seeder, the spread and the semantic lane so it can sweep the blend
weights. That makes it blind to anything inside `brain.recall()` itself - a rerank added there changes
nothing in its output, and an unchanged number from a blind instrument reads exactly like "no harm
done". It is not evidence on that axis (gate_incomplete_is_not_a_pass_2026_07_16).

This one calls `brain.recall()`, the function the CLI and the hooks call, over the same real
prompt-to-reads episodes: what the owner actually typed, scored against what that session actually
went on to read. It is independent of the 56-question owner-voice bank, so a rerank tuned on that
bank can be falsified here.

    python eval_recall_real_prompts.py [--n N] [--k 8]

Scores hit@k and MRR over the episodes' read sets. Set BRAIN_RECALL_* dials to compare arms; hold the
index generation with `bm25.py freeze` so both arms see one corpus.
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from eval_prompt_recall_fullstack import episodes   # noqa: E402
from eval_brain_recall_fresh import recall as cli_recall  # the CLI path, exactly as the bank scores it


def _inproc_recall():
    """brain.recall() called in this one process, the call brain.py's CLI makes after parsing (2026-10-03).

    The CLI path starts a fresh `brain.py recall` per episode. On this box a process start stalls for 20-35 s in a
    busy window, so the 66 episodes passed the gate's 900 s cap on 6 of the 8 nights to 3 Oct (rc 124, no number at
    all); on calm nights they took 259-403 s. Same function, same arguments, same output parsing; `--cli` keeps the
    old path, and `--check-cli N` compares the two on the first N episodes, slug for slug."""
    import contextlib
    import io
    import re
    import brain  # noqa: E402  (the module the CLI runs)

    def rec(q, k=8):
        q = re.sub(r"^[\-\*>\s]+", "", q)      # what brain.main() does to a query before recall()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            brain.recall(q, 8)
        out = buf.getvalue()
        slugs = re.findall(r"^\s*\d\.\d\d\s+\[[^\]]*\]\s+(\S+)", out, re.M)
        return slugs[:k], out
    return rec


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        print(__doc__)
        return 0
    k = int(sys.argv[sys.argv.index("--k") + 1]) if "--k" in sys.argv else 8
    eps = episodes()
    if "--n" in sys.argv:
        eps = eps[: int(sys.argv[sys.argv.index("--n") + 1])]
    if len(eps) < 20:
        print("too few episodes (%d) - re-run as the archive grows" % len(eps))
        return 2
    if "--check-cli" in sys.argv:
        m = int(sys.argv[sys.argv.index("--check-cli") + 1])
        rec = _inproc_recall()
        same = 0
        for _sess, prompt, _reads in eps[:m]:
            a, _ = rec(prompt, k)
            b, _ = cli_recall(prompt, k)
            same += a == b
            if a != b:
                print("DIFFERS: %r\n  inproc %s\n  cli    %s" % (prompt[:80], a, b))
        print("check-cli: %d of %d episodes identical" % (same, m))
        return 0 if same == m else 1
    recall_fn = cli_recall if "--cli" in sys.argv else _inproc_recall()
    hit = 0
    rr = 0.0
    t0 = time.time()
    for _sess, prompt, reads in eps:
        try:
            slugs, _raw = recall_fn(prompt, k)
        except Exception:
            slugs = []
        rank = next((i + 1 for i, s in enumerate(slugs) if s in reads), None)
        if rank:
            hit += 1
            rr += 1.0 / rank
    n = len(eps)
    print("eval_recall_real_prompts: %d episodes through brain.recall(n=%d) %s, dials "
          "QCOVER=%s NAME_COVERAGE=%s NOTE_DEMOTE=%s"
          % (n, k, "via the CLI, one process each" if "--cli" in sys.argv else "in one process",
             os.environ.get("BRAIN_RECALL_QCOVER", "0"),
             os.environ.get("BRAIN_RECALL_NAME_COVERAGE", "1"),
             os.environ.get("BRAIN_RECALL_NOTE_DEMOTE", "1")))
    print("  hit@%d %d/%d = %.3f     MRR %.4f     (%.0fs)" % (k, hit, n, hit / float(n), rr / n, time.time() - t0))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
