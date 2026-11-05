"""ProofSwarm v2 — paper review orchestrator.

Reads a LaTeX paper, seeds one ticket per claim (Theorem/Lemma/Proposition/Corollary),
and dispatches a structured review workflow with 14 specialist agents.

Workflow per claim (deterministic order, not navigator-driven):
  Pass 1 (broad audit, cheap, parallelizable):
    statement_auditor, notation_checker, quantifier_checker, constant_calculator
  Pass 2 (deep audit, expensive):
    definition_consistency, cross_reference_validator, proof_auditor, boundary_case_tester
  Pass 3 (synthesis):
    referee

Optional Pass 4 (formal): formalizer + prover for tractable claims.

Each claim's findings are merged into a single ProofTicket. Final report aggregates
verdicts across all claims.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

from proofswarm.calibration import require_surface_calibration
from proofswarm.config import PER_PROBLEM_BUDGET_USD, TRACE_DIR
from proofswarm.local_runtime import LocalPolicyError, set_process_below_normal
from proofswarm.orchestrator import call_model
from proofswarm.prompts_v2 import (PROMPTS_V2, AGENT_MODELS_V2, THINKING_BUDGET_V2,
                                    REQUEST_TIMEOUT_V2)
from proofswarm.schemas import Evidence, ProofTicket, Verdict, gate_verdict
from proofswarm.tools.latex_parser import parse_file, PaperClaim
from proofswarm.tools.symbolic import check_claim
from proofswarm.tools.ticket_store import load_ticket, save_ticket
from proofswarm.tools.lean_verify import compose_lean_source, lean_verify


# ============================================================================
# Helpers
# ============================================================================

def _agent_call_v2(agent: str, user_msg: str) -> tuple[str, float]:
    """Call one required specialist through the local model provider."""
    try:
        return call_model(
            AGENT_MODELS_V2[agent], PROMPTS_V2[agent], user_msg,
            thinking_budget=THINKING_BUDGET_V2.get(agent, 0),
            request_timeout=REQUEST_TIMEOUT_V2.get(agent, 120),
        )
    except Exception as e:
        raise RuntimeError(f"required {agent} worker failed; review aborted") from e


def _try_parse_json(text: str) -> dict | list | None:
    import re
    m = re.search(r"```(?:json|JSON)?\s*\n(.*?)```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except Exception:
            pass
    # try whole text
    try:
        return json.loads(text.strip())
    except Exception:
        pass
    # try first {...}
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    return None


def _prover_evidence(text: str, timeout: int = 120) -> tuple[dict, str, str]:
    """Preserve exact model output while binding evidence to exact compiled source."""

    import re

    agent_digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    lean_match = re.search(r"```lean4?\s*\n(.*?)```", text, re.DOTALL)
    if not lean_match:
        return (
            {
                "verify": {"ok": False, "errors": ["no lean code block found"]},
                "agent_output": text,
                "agent_output_sha256": agent_digest,
            },
            text,
            agent_digest,
        )
    lean_code = lean_match.group(1).strip()
    try:
        compiled_source = compose_lean_source(lean_code, "", imports=[])
        verify = lean_verify(lean_code, "", imports=[], timeout=timeout)
        verify_dict = verify.model_dump()
    except Exception as exc:
        compiled_source = lean_code + "\n"
        verify_dict = {
            "ok": False,
            "errors": [f"verify call failed: {type(exc).__name__}"],
            "source_sha256": hashlib.sha256(compiled_source.encode("utf-8")).hexdigest(),
        }
    return (
        {
            "verify": verify_dict,
            "agent_output": text,
            "agent_output_sha256": agent_digest,
        },
        compiled_source,
        hashlib.sha256(compiled_source.encode("utf-8")).hexdigest(),
    )


def _claim_user_message(claim: PaperClaim, definitions_block: str = "",
                        references_block: str = "", full_paper: str = "") -> str:
    """Construct the per-agent user message.

    Includes the full paper as context, then the specific claim and optional
    helpers. The configured local model's context limit remains a hard input gate.
    """
    parts = []
    if full_paper:
        parts.append("=" * 70)
        parts.append("FULL PAPER (entire LaTeX source) — your global context")
        parts.append("=" * 70)
        parts.append(full_paper)
        parts.append("=" * 70)
        parts.append("END OF FULL PAPER")
        parts.append("=" * 70)
    parts.append(f"\n>>> CLAIM YOU ARE REVIEWING <<<")
    parts.append(f"Kind: {claim.kind}")
    parts.append(f"Label: {claim.label or '<unlabeled>'}")
    if claim.title:
        parts.append(f"Title: {claim.title}")
    parts.append(f"\n--- STATEMENT (verbatim from paper) ---\n{claim.statement}")
    if claim.proof:
        parts.append(f"\n--- PROOF (verbatim from paper) ---\n{claim.proof}")
    parts.append(f"\nReview this claim against the full paper above. All definitions, "
                 f"notation, and other lemmas/theorems referenced are in the full paper.")
    return "\n".join(parts)


def _build_definitions_block(claims: list[PaperClaim]) -> str:
    """Bundle all definitions from the paper into one text block."""
    defs = [c for c in claims if c.kind == "definition"]
    if not defs:
        return ""
    lines = []
    for d in defs:
        lines.append(f"[{d.label or '?'}] {d.title or ''}")
        lines.append(d.statement)
        lines.append("")
    return "\n".join(lines)


def _build_references_block(target: PaperClaim, all_claims: list[PaperClaim]) -> str:
    """Find all \\ref{X}-style references in target's proof and provide their statements."""
    import re
    if not target.proof:
        return ""
    refs = set(re.findall(r"\\ref\{([^}]+)\}|Lemma~?\\?ref\{([^}]+)\}", target.proof))
    refs = {r for tup in refs for r in tup if r}
    by_label = {c.label: c for c in all_claims if c.label}
    blocks = []
    for ref in refs:
        c = by_label.get(ref)
        if c:
            blocks.append(f"[{ref}] {c.kind}: {c.statement[:300]}...")
    return "\n".join(blocks)


# ============================================================================
# Constant Calculator — deterministic tool, not LLM
# ============================================================================

def run_constant_calculator(agent_text: str) -> list[dict]:
    """Parse the agent's claims_to_verify list, run sympy on each, return results."""
    parsed = _try_parse_json(agent_text)
    if not isinstance(parsed, dict):
        return []
    claims = parsed.get("claims_to_verify", [])
    out = []
    for c in claims:
        if not isinstance(c, dict):
            continue
        claim_str = c.get("claim", "")
        ctx = c.get("context", "")
        result = check_claim(claim_str, ctx)
        out.append({
            "claim": claim_str, "context": ctx,
            "ok": result.ok, "actual": result.actual, "error": result.error,
        })
    return out


def _load_or_seed_review_ticket(
    ticket_id: str, problem_id: str, statement: str, *, detail: str
) -> ProofTicket:
    """Resume an exact open ticket; never overwrite a collision or settled review."""

    existing = load_ticket(ticket_id)
    if existing is not None:
        if existing.problem_id != problem_id or existing.statement_informal != statement:
            raise LocalPolicyError(
                f"ticket ID {ticket_id!r} already belongs to a different claim"
            )
        if existing.verdict is not Verdict.OPEN:
            raise LocalPolicyError(
                f"ticket {ticket_id!r} is already settled as {existing.verdict.value}"
            )
        existing.log("seed", "resumed", detail)
        save_ticket(existing)
        return existing
    ticket = ProofTicket(
        ticket_id=ticket_id,
        problem_id=problem_id,
        statement_informal=statement,
    )
    ticket.log("seed", "created", detail)
    save_ticket(ticket)
    return ticket


# ============================================================================
# Per-claim workflow
# ============================================================================

# 3x deeper review: each claim hit by 13 specialists across 4 passes, then Lean attempts,
# then a second proof_auditor pass for redundancy, then Referee.
PASS1 = ["statement_auditor", "notation_checker", "quantifier_checker", "constant_calculator"]
PASS2 = ["definition_consistency", "cross_reference_validator", "proof_auditor", "boundary_case_tester"]
PASS3_LEAN = ["formalizer", "prover"]      # Lean attempt on every claim — accept failure on hard ones
PASS4_CROSS = ["lemma_miner", "counterexample_hunter"]
PASS5_REVERIFY = ["proof_auditor"]          # second independent pass — catches misses
PASS6_REFEREE = ["referee"]


def review_claim(claim: PaperClaim, problem_id: str,
                 definitions_block: str, references_block: str,
                 print_cb=print, attempt_lean: bool = True) -> ProofTicket:
    """Run the deep multi-pass review workflow on a single claim.

    14+ specialist calls per claim:
      Pass 1: statement_auditor, notation_checker, quantifier_checker, constant_calculator
      Pass 2: definition_consistency, cross_reference_validator, proof_auditor, boundary_case_tester
      Pass 3: formalizer + prover (Lean attempt — accept failure on hard claims)
      Pass 4: lemma_miner + counterexample_hunter
      Pass 5: proof_auditor (second independent pass — catches misses)
      Pass 6: referee (synthesizes everything)
    """
    require_surface_calibration("paper_review")
    tid = claim.label or f"unlabeled_{int(time.time()*1000)%1_000_000}"
    t = _load_or_seed_review_ticket(
        tid,
        problem_id,
        claim.statement,
        detail=f"kind={claim.kind} label={claim.label}",
    )
    user_msg = _claim_user_message(claim, definitions_block, references_block)

    total_cost = 0.0
    findings: dict[str, dict] = {}

    def _run(agents: list[str], phase: str, msg: str | None = None):
        nonlocal total_cost
        for agent in agents:
            text, cost = _agent_call_v2(agent, msg or user_msg)
            total_cost += cost
            parsed = _try_parse_json(text)
            extra: dict = {}
            evidence_content = text
            evidence_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
            if agent == "constant_calculator":
                extra = {"sympy_results": run_constant_calculator(text)}
            elif agent == "prover":
                extra, evidence_content, evidence_sha256 = _prover_evidence(text)
            ev_kind = "referee_finding" if agent == "referee" else \
                       "lean_proof" if agent == "prover" else \
                       "numerical_certificate" if agent == "counterexample_hunter" else \
                       "informal_argument"
            t.add_evidence(Evidence(
                kind=ev_kind, content=evidence_content, sha256=evidence_sha256,
                produced_by=agent, cost_usd=cost, metadata={"parsed": parsed, **extra},
            ))
            # Append-or-overwrite per agent (last wins; both kept in evidence)
            key = f"{phase}.{agent}"
            findings[key] = {"parsed": parsed, **extra}
            t.log(agent, phase, f"chars={len(text)} cost=${cost:.4f}")
            save_ticket(t)
            extra_note = ""
            if agent == "prover" and "verify" in extra:
                lv = extra["verify"]
                extra_note = f" lean_ok={lv.get('ok')} sorry={lv.get('sorry_count', '?')}"
            print_cb(f"  [{tid}] {phase:10s}{agent:28s} cost=${cost:.4f} cum=${total_cost:.4f}{extra_note}")

    _run(PASS1, "pass1")
    _run(PASS2, "pass2")
    if attempt_lean:
        _run(PASS3_LEAN, "pass3")
    _run(PASS4_CROSS, "pass4")
    _run(PASS5_REVERIFY, "pass5")

    # Pass 6: Referee — synthesizes findings
    referee_msg = (user_msg + "\n\n--- ALL FINDINGS SO FAR ---\n"
                   + json.dumps({k: (v.get("parsed") if v.get("parsed") else "no parse")
                                 for k, v in findings.items()}, indent=2)[:12000])
    text, cost = _agent_call_v2("referee", referee_msg)
    total_cost += cost
    parsed = _try_parse_json(text) or {}
    verdict_str = parsed.get("verdict", "open")
    try:
        v = Verdict(verdict_str)
    except ValueError:
        v = Verdict.OPEN
    t.add_evidence(Evidence(
        kind="referee_finding", content=text[:6000], produced_by="referee",
        cost_usd=cost, metadata={"parsed": parsed},
    ))
    t.verdict, gate_reason = gate_verdict(t, v)
    parsed["verification_gate"] = gate_reason
    t.evidence[-1].metadata["verification_gate"] = gate_reason
    if isinstance(t.evidence[-1].metadata.get("parsed"), dict):
        t.evidence[-1].metadata["parsed"]["verification_gate"] = gate_reason
    t.log("referee", "pass6", f"verdict={verdict_str}")
    save_ticket(t)
    print_cb(f"  [{tid}] referee verdict={verdict_str:22s} TOTAL=${total_cost:.4f}")
    return t


# ============================================================================
# Main
# ============================================================================

def run(paper_path: str, problem_id: str, max_claims: int | None = None,
        skip_kinds: tuple[str, ...] = ("definition",),
        skip_completed: bool = True,
        parallel_workers: int = 1) -> dict:
    require_surface_calibration("paper_review")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore
    claims, bib = parse_file(paper_path)
    full_paper = Path(paper_path).read_text(encoding="utf-8")
    print(f"Parsed {len(claims)} claims, {len(bib)} bib entries from {paper_path}")
    print(f"Full paper: {len(full_paper)} chars (~{len(full_paper)//4} tokens) — passed as global context to every agent call")
    if parallel_workers != 1:
        raise LocalPolicyError("parallel local review workers are disabled until reservations exist")
    require_surface_calibration("paper_review")
    print(f"parallel_workers={parallel_workers}  skip_completed={skip_completed}")

    summary = {"paper": paper_path, "claims_reviewed": [],
               "total_cost_usd": 0.0, "verdicts": {}}
    reviewed = 0
    for c in claims:
        if c.kind in skip_kinds:
            continue
        if max_claims is not None and reviewed >= max_claims:
            break
        if PER_PROBLEM_BUDGET_USD > 0 and summary["total_cost_usd"] >= PER_PROBLEM_BUDGET_USD:
            print(f"Budget exhausted at ${summary['total_cost_usd']:.2f}")
            break

        # Skip if already completed
        if skip_completed and c.label:
            from proofswarm.tools.ticket_store import load_ticket
            existing = load_ticket(c.label)
            if existing is not None and existing.verdict.value not in ("open",):
                print(f"\n=== SKIP {c.kind} {c.label} — already has verdict={existing.verdict.value} ===")
                summary["claims_reviewed"].append(existing.ticket_id)
                summary["verdicts"][existing.ticket_id] = existing.verdict.value
                summary["total_cost_usd"] += existing.cost_usd
                reviewed += 1
                continue

        print(f"\n=== Reviewing {c.kind} {c.label or '?'} (statement {len(c.statement)} chars,"
              f" proof {len(c.proof or '')} chars) ===")
        ticket = review_claim_with_full_paper(c, problem_id, full_paper,
                                               parallel_workers=parallel_workers)
        summary["claims_reviewed"].append(ticket.ticket_id)
        summary["verdicts"][ticket.ticket_id] = ticket.verdict.value
        summary["total_cost_usd"] += ticket.cost_usd
        reviewed += 1
    return summary


def review_claim_with_full_paper(claim: PaperClaim, problem_id: str,
                                   full_paper: str, print_cb=print,
                                   attempt_lean: bool = True,
                                   parallel_workers: int = 1) -> ProofTicket:
    """Wrapper around review_claim that injects the full paper as context.

    Specialists run serially until local model-memory reservations exist.  A
    required worker failure aborts immediately; the referee runs only after
    every required specialist completed successfully.
    """
    if parallel_workers != 1:
        raise LocalPolicyError("parallel local review workers are disabled until reservations exist")
    tid = (claim.label or f"unlabeled_{int(time.time()*1000)%1_000_000}")
    t = _load_or_seed_review_ticket(
        tid,
        problem_id,
        claim.statement,
        detail=f"kind={claim.kind} label={claim.label}",
    )
    user_msg = _claim_user_message(claim, full_paper=full_paper)

    total_cost = 0.0
    findings: dict[str, dict] = {}

    def _run_one(agent_phase: tuple[str, str]):
        agent, phase = agent_phase
        text, cost = _agent_call_v2(agent, user_msg)
        parsed = _try_parse_json(text)
        extra: dict = {}
        evidence_content = text
        evidence_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if agent == "constant_calculator":
            extra = {"sympy_results": run_constant_calculator(text)}
        elif agent == "prover":
            extra, evidence_content, evidence_sha256 = _prover_evidence(text)
        ev_kind = ("lean_proof" if agent == "prover" else
                   "numerical_certificate" if agent == "counterexample_hunter" else
                   "informal_argument")
        t.add_evidence(Evidence(
            kind=ev_kind, content=evidence_content, sha256=evidence_sha256,
            produced_by=agent, cost_usd=cost, metadata={"parsed": parsed, **extra},
        ))
        findings[f"{phase}.{agent}"] = {"parsed": parsed, **extra}
        t.log(agent, phase, f"chars={len(text)} cost=${cost:.4f}")
        save_ticket(t)
        extra_note = ""
        if agent == "prover" and "verify" in extra:
            lv = extra["verify"]
            extra_note = f" lean_ok={lv.get('ok')} sorry={lv.get('sorry_count','?')}"
        return agent, phase, cost, extra_note

    # Build the parallel-eligible work list (passes 1-5).
    work: list[tuple[str, str]] = []
    for a in PASS1: work.append((a, "pass1"))
    for a in PASS2: work.append((a, "pass2"))
    if attempt_lean:
        for a in PASS3_LEAN: work.append((a, "pass3"))
    for a in PASS4_CROSS: work.append((a, "pass4"))
    for a in PASS5_REVERIFY: work.append((a, "pass5"))

    for agent_phase in work:
        agent, phase, cost, extra_note = _run_one(agent_phase)
        total_cost += cost
        print_cb(
            f"  [{tid}] {phase:10s}{agent:28s} cost=${cost:.4f} "
            f"cum=${total_cost:.4f}{extra_note}"
        )

    referee_msg = (user_msg + "\n\n--- ALL FINDINGS SO FAR ---\n"
                   + json.dumps({k: (v.get("parsed") if v.get("parsed") else "no parse")
                                 for k, v in findings.items()}, indent=2)[:12000])
    text, cost = _agent_call_v2("referee", referee_msg)
    total_cost += cost
    parsed = _try_parse_json(text) or {}
    verdict_str = parsed.get("verdict", "open")
    try:
        v = Verdict(verdict_str)
    except ValueError:
        v = Verdict.OPEN
    t.add_evidence(Evidence(
        kind="referee_finding", content=text[:6000], produced_by="referee",
        cost_usd=cost, metadata={"parsed": parsed},
    ))
    t.verdict, gate_reason = gate_verdict(t, v)
    parsed["verification_gate"] = gate_reason
    t.evidence[-1].metadata["verification_gate"] = gate_reason
    if isinstance(t.evidence[-1].metadata.get("parsed"), dict):
        t.evidence[-1].metadata["parsed"]["verification_gate"] = gate_reason
    t.log("referee", "pass6", f"verdict={verdict_str}")
    save_ticket(t)
    print_cb(f"  [{tid}] referee verdict={verdict_str:22s} TOTAL=${total_cost:.4f}")
    return t


def main() -> int:
    set_process_below_normal()
    ap = argparse.ArgumentParser()
    ap.add_argument("--paper", required=True, help="Path to .tex file")
    ap.add_argument("--problem", default="paper_review")
    ap.add_argument("--max-claims", type=int, default=None)
    args = ap.parse_args()
    try:
        summary = run(args.paper, args.problem, max_claims=args.max_claims)
    except (LocalPolicyError, RuntimeError) as exc:
        print(f"ProofSwarm paper review stopped safely: {exc}", file=sys.stderr)
        return 2
    out = TRACE_DIR / f"paper_review_{int(time.time())}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print("\n" + "=" * 70)
    print("FINAL SUMMARY")
    print("=" * 70)
    print(f"Total cost: ${summary['total_cost_usd']:.4f}")
    print(f"Claims reviewed: {len(summary['claims_reviewed'])}")
    print(f"Verdicts:")
    for tid, v in summary["verdicts"].items():
        print(f"  {tid:30s} {v}")
    print(f"\nFull trace: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
