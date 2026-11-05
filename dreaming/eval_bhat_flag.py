#!/usr/bin/env python3
"""eval_bhat_flag.py - calibrate and gate the B_hat thin-evidence flag (diffusion iteration 5).

THE OBJECT. a finite-sample ratio decomposition proves the exact finite-p identity
m_p - m_inf = n_hat / B_hat: retrieval error is a centered fluctuation over the empirical
evidence-mass denominator. The principled thin-evidence flag is therefore "B_hat small at the
query" - not the sqrt(d_eff/n_eff) initial-slope heuristic (that survives only as a secondary
diagnostic; the growing-dimensional interior law is open, so no retrieval-error
law is claimed for it). Implementation (brain._semantic_scores diag):
    B_hat = (sum of live seed strengths) * ||strength-weighted mean of unit seed vectors||
          = evidence mass x seed-cloud coherence
computed through the SAME embedding loader and row-freshness gate the live readout uses (the
2026-07-23 probe's guessed row plumbing returned d_eff = 0 on multi-seed queries; wiring through
_semantic_scores closes that).

PRE-REGISTERED PROCEDURE AND SHIP RULE (written before the first calibration run):
  1. Query corpus: real recall-CLI queries. SOURCE AMENDMENT (2026-07-23 20:40, before any
     threshold was computed): the first registration mined the transcript-archive mirror by regex
     and found ZERO recall calls (the mirror does not carry them); the typed access ledger
     memory_access.tsv holds 2,416 recall_query events, each pinned to an exact artifact file and
     line, so the corpus now comes from those pinned lines (read-only, line-addressed - no
     whole-transcript substring hunts). The distribution is computed over queries that produce at
     least one live embedded seed; no-seed queries already get recall's loud no-foothold message.
  2. Threshold: the 10th percentile (p10) of that empirical distribution. No other constant.
  3. Demonstration sets, fixed here before measuring:
     confident (the six standard positive-control queries) and vague (the primary session's four
     probes plus four registered tonight: "make it better please", "the thing we discussed
     yesterday", "why is it broken again", "sourdough hydration baker percentages").
  4. SHIP RULE: all 6/6 confident queries must sit ABOVE the threshold (unflagged), and at least
     6/8 vague probes must be flagged (B_hat < threshold, or no seeds, or no live embedded
     seeds). The flag is a purely additive marker line; BRAIN_THIN_FLAG=0 disables it.
If the rule fails, the flag does not ship and the failure is reported with the numbers.
Usage: python eval_bhat_flag.py
"""
import glob
import io
import os
import re
import sys

HOME = os.path.expanduser("~")
DR = os.path.join(HOME, ".claude", "dreaming")
sys.path.insert(0, DR)
ARCHIVE = os.path.join(HOME, "brain-repo", "11_raw_session_transcripts",
                       "claude_projects", "{{BRAIN_SLUG}}")

try:
    import ctypes
    ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)
except Exception:
    try:  # no windll off Windows: nice 10 carries the same BelowNormal intent
        import os as _os
        if _os.name != "nt":
            _os.nice(10)
    except Exception:
        pass

import brain as B  # noqa: E402
import spread_recall as sr  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

CONFIDENT = [
    "flashing terminal windows scheduled task",
    "never start the local protected production stack",
    "grid harvest one second data only",
    "roll cadence gamma replenishment puts",
    "research writing human voice no AI tells",
    "check the brain before you simulate",
]
VAGUE = [
    "what should I do next",
    "improve the thing from before",
    "the problem with the number",
    "how does the water flow in the garden",
    "make it better please",
    "the thing we discussed yesterday",
    "why is it broken again",
    "sourdough hydration baker percentages",
]
RX = [re.compile(r'brain\.py\s+recall\s+\\{0,3}"([^"\\]{8,120})'),
     re.compile(r"brain\.py\s+recall\s+'([^'\n]{8,120})'")]


def mine_queries(cap=300):
    """Queries from memory_access.tsv recall_query rows via their pinned (artifact, line)."""
    rows = []
    try:
        with io.open(os.path.join(DR, "memory_access.tsv"), encoding="utf-8",
                     errors="replace") as f:
            header = next(f).rstrip("\n").split("\t")
            ai, ki, li, ei = (header.index(c) for c in ("artifact", "access_kind", "line", "epoch"))
            for ln in f:
                p = ln.rstrip("\n").split("\t")
                if len(p) > max(ai, ki, li) and p[ki] == "recall_query":
                    try:
                        rows.append((float(p[ei]), p[ai], int(p[li])))
                    except Exception:
                        pass
    except Exception:
        return []
    rows.sort(reverse=True)
    roots = [os.path.join(HOME, ".claude", "projects", "{{BRAIN_SLUG}}"), ARCHIVE,
             os.path.join(HOME, ".codex", "sessions")]
    by_artifact = {}
    for _, art, line_no in rows:
        by_artifact.setdefault(art, []).append(line_no)
    seen, out = set(), []
    for art, line_nos in by_artifact.items():
        if len(out) >= cap:
            break
        fp = next((os.path.join(r, art) for r in roots if os.path.isfile(os.path.join(r, art))),
                  None)
        if fp is None:
            hits = glob.glob(os.path.join(roots[2], "**", art), recursive=True)
            fp = hits[0] if hits else None
        if fp is None:
            continue
        want = set(line_nos)
        try:
            with io.open(fp, encoding="utf-8", errors="replace") as f:
                for i, ln in enumerate(f, 1):
                    if i not in want:
                        continue
                    for rx in RX:
                        for m in rx.finditer(ln):
                            q = m.group(1).strip().strip('\\"')
                            k = q.lower()
                            if len(q) >= 8 and k not in seen:
                                seen.add(k)
                                out.append(q)
        except Exception:
            continue
    return out[:cap]


def bhat_of(query, idx):
    words = sr._words(query)
    seeds, _lex = sr._seeds_from_prompt(words, idx)
    if not seeds:
        return dict(status="no-seed")
    diag = {}
    B._semantic_scores(seeds, diag)
    if not diag.get("ready"):
        return dict(status="no-sidecar")
    if not diag.get("n_live"):
        return dict(status="no-live-seed", n_seeds=diag.get("n_seeds", 0))
    return dict(status="ok", b_hat=diag["b_hat"], n_eff=diag["n_eff"],
                n_seeds=diag["n_seeds"], n_live=diag["n_live"])


def main():
    idx = B._archived_index_lines()
    idx.update(sr._index_lines())

    queries = mine_queries()
    print("mined %d distinct recall-CLI queries from memory_access.tsv ledger-pinned lines" % len(queries))
    vals = []
    statuses = {}
    for q in queries:
        r = bhat_of(q, idx)
        statuses[r["status"]] = statuses.get(r["status"], 0) + 1
        if r["status"] == "ok":
            vals.append(r["b_hat"])
    vals.sort()
    print("status counts: %s" % statuses)
    if len(vals) < 20:
        print("FAIL: too few seeded queries (%d) to calibrate honestly - not shipping" % len(vals))
        return 1
    pct = lambda p: vals[max(0, min(len(vals) - 1, int(p * len(vals))))]
    thr = pct(0.10)
    print("B_hat over %d seeded queries: p5=%.2f p10=%.2f p25=%.2f median=%.2f p90=%.2f"
          % (len(vals), pct(0.05), thr, pct(0.25), pct(0.50), pct(0.90)))
    print("pre-registered threshold = p10 = %.2f" % thr)

    ok = True
    print("\nconfident controls (must be unflagged, 6/6):")
    for q in CONFIDENT:
        r = bhat_of(q, idx)
        good = r["status"] == "ok" and r["b_hat"] >= thr
        ok &= good
        print("  %s B_hat=%-8s n_eff=%-6s %s"
              % ("PASS" if good else "FAIL",
                 ("%.2f" % r["b_hat"]) if r.get("b_hat") is not None else r["status"],
                 ("%.1f" % r["n_eff"]) if r.get("n_eff") is not None else "-", q))

    print("\nvague probes (>= 6/8 must be flagged):")
    flagged = 0
    for q in VAGUE:
        r = bhat_of(q, idx)
        f = r["status"] in ("no-seed", "no-live-seed") or (r["status"] == "ok" and r["b_hat"] < thr)
        flagged += f
        print("  %s B_hat=%-8s n_eff=%-6s %s"
              % ("flag" if f else "----",
                 ("%.2f" % r["b_hat"]) if r.get("b_hat") is not None else r["status"],
                 ("%.1f" % r["n_eff"]) if r.get("n_eff") is not None else "-", q))
    ok &= flagged >= 6
    print("  flagged %d/8" % flagged)

    print("\nVERDICT: %s (threshold %.2f)"
          % ("SHIP the flag" if ok else "DO NOT SHIP - rule failed", thr))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
