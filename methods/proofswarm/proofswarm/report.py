"""Generate a human-readable report from a paper-review run.

Reads all tickets in traces/tickets/ and produces a structured findings
document grouped by claim, listing:
  - per-specialist findings (verdicts, issues, sympy results, lean status)
  - referee final verdict
  - top blockers/majors aggregated across the paper
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path
from collections import defaultdict


def load_tickets(tickets_dir: Path) -> list[dict]:
    out = []
    for p in sorted(tickets_dir.glob("*.json")):
        out.append(json.loads(p.read_text(encoding="utf-8")))
    return out


def severity_rank(s: str) -> int:
    return {"blocker": 3, "major": 2, "minor": 1}.get((s or "").lower(), 0)


def build_report(tickets: list[dict]) -> str:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore
    lines: list[str] = []
    lines.append("=" * 78)
    lines.append("PROOFSWARM v2 — PAPER REVIEW REPORT")
    lines.append("=" * 78)
    lines.append("")
    total_cost = sum(t.get("cost_usd", 0.0) for t in tickets)
    verdict_counts: dict[str, int] = defaultdict(int)
    all_blockers: list[tuple[str, str]] = []  # (ticket_id, description)
    all_majors: list[tuple[str, str]] = []
    all_sympy_failures: list[tuple[str, str, str]] = []  # (ticket_id, claim, actual)
    all_sympy_passes: list[tuple[str, str]] = []        # (ticket_id, claim)
    lean_results: list[tuple[str, bool, int]] = []      # (ticket_id, ok, sorry_count)

    for t in tickets:
        verdict_counts[t.get("verdict", "unknown")] += 1
        # Walk evidence
        for e in t.get("evidence", []):
            md = e.get("metadata", {}) or {}
            parsed = md.get("parsed") or {}
            # Issues from any agent
            issues = parsed.get("issues") or []
            for iss in issues:
                if not isinstance(iss, dict):
                    continue
                desc = iss.get("description", "")
                sev = (iss.get("severity") or "").lower()
                if sev == "blocker":
                    all_blockers.append((t["ticket_id"], desc))
                elif sev == "major":
                    all_majors.append((t["ticket_id"], desc))
            # Sympy
            for sr in md.get("sympy_results", []) or []:
                if sr.get("ok"):
                    all_sympy_passes.append((t["ticket_id"], sr.get("claim", "")))
                else:
                    all_sympy_failures.append(
                        (t["ticket_id"], sr.get("claim", ""), sr.get("actual", ""))
                    )
            # Lean
            if e.get("kind") == "lean_proof":
                lv = md.get("lean_verify") or {}
                if "ok" in lv:
                    lean_results.append((t["ticket_id"], lv.get("ok", False),
                                          lv.get("sorry_count", -1)))

    lines.append(f"Total cost: ${total_cost:.4f}")
    lines.append(f"Claims reviewed: {len(tickets)}")
    lines.append("")
    lines.append("VERDICT DISTRIBUTION:")
    for v, n in sorted(verdict_counts.items(), key=lambda kv: -kv[1]):
        lines.append(f"  {v:30s} {n}")
    lines.append("")
    lines.append("=" * 78)
    lines.append("PER-CLAIM SUMMARY")
    lines.append("=" * 78)
    for t in tickets:
        lines.append("")
        lines.append(f"--- {t['ticket_id']} ---")
        lines.append(f"  verdict: {t.get('verdict')}")
        lines.append(f"  cost: ${t.get('cost_usd', 0.0):.4f}")
        lines.append(f"  evidence items: {len(t.get('evidence', []))}")
        # Find referee finding
        for e in reversed(t.get("evidence", [])):
            if e.get("produced_by") == "referee":
                md = e.get("metadata", {}) or {}
                parsed = md.get("parsed") or {}
                rationale = (parsed.get("rationale") or "")[:400]
                lines.append(f"  referee rationale: {rationale}")
                issues = parsed.get("issues") or []
                if issues:
                    lines.append(f"  referee issues:")
                    for iss in issues[:5]:
                        sev = iss.get("severity", "?")
                        d = (iss.get("description") or "")[:200]
                        lines.append(f"    [{sev}] {d}")
                break
        # Lean
        for e in t.get("evidence", []):
            if e.get("kind") == "lean_proof":
                lv = (e.get("metadata") or {}).get("lean_verify") or {}
                if "ok" in lv:
                    lines.append(f"  lean: ok={lv.get('ok')} sorry={lv.get('sorry_count', '?')}")
                break

    lines.append("")
    lines.append("=" * 78)
    lines.append("AGGREGATED BLOCKERS ACROSS PAPER")
    lines.append("=" * 78)
    if all_blockers:
        for tid, desc in all_blockers[:30]:
            lines.append(f"  [{tid}] {desc[:300]}")
    else:
        lines.append("  (none)")
    lines.append("")
    lines.append("=" * 78)
    lines.append("AGGREGATED MAJOR ISSUES")
    lines.append("=" * 78)
    if all_majors:
        for tid, desc in all_majors[:30]:
            lines.append(f"  [{tid}] {desc[:300]}")
    else:
        lines.append("  (none)")
    lines.append("")
    lines.append("=" * 78)
    lines.append("SYMPY ARITHMETIC VERIFICATIONS")
    lines.append("=" * 78)
    lines.append(f"PASSED ({len(all_sympy_passes)}):")
    for tid, claim in all_sympy_passes[:20]:
        lines.append(f"  [{tid}] {claim[:200]}")
    lines.append("")
    lines.append(f"FAILED ({len(all_sympy_failures)}):")
    for tid, claim, actual in all_sympy_failures[:20]:
        lines.append(f"  [{tid}] {claim[:150]} -> {actual[:150]}")
    lines.append("")
    lines.append("=" * 78)
    lines.append("LEAN KERNEL RESULTS")
    lines.append("=" * 78)
    for tid, ok, sc in lean_results:
        lines.append(f"  [{tid}] ok={ok} sorry_count={sc}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickets-dir",
                    default=os.path.expanduser("~/proofswarm/traces/tickets"))
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore
    tickets = load_tickets(Path(args.tickets_dir))
    report = build_report(tickets)
    if args.out:
        Path(args.out).write_text(report, encoding="utf-8")
        print(f"Report written to {args.out}")
    print(report)


if __name__ == "__main__":
    main()
