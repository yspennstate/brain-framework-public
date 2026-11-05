"""Local-only ProofSwarm orchestrator.

Mock mode exercises the lifecycle without inference.  A real run talks only to
the configured loopback model server and stops on every worker or policy error.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import re
import sys
import time

from proofswarm.calibration import (
    CALIBRATION_SURFACES,
    require_surface_calibration,
    run_surface_calibration,
)
from proofswarm.config import (
    AGENT_MODELS,
    LOCAL_BASE_URL,
    LOCAL_MODEL,
    PER_PROBLEM_BUDGET_USD,
    TRACE_DIR,
)
from proofswarm.local_runtime import (
    LocalPolicyError,
    exclusive_local_model_slot,
    require_local_compute,
    set_process_below_normal,
)
from proofswarm.prompts import PROMPTS
from proofswarm.schemas import Evidence, ProofTicket, Verdict, gate_verdict
from proofswarm.tools.ticket_store import save_ticket, list_tickets, load_ticket
from proofswarm.tools.numerical import run_numerical_search
from proofswarm.tools.lean_verify import compose_lean_source, lean_verify
from proofswarm.schemas import NumericalSearchRequest


# --- Local model call ---
import urllib.request


MAX_LOCAL_REQUEST_BYTES = 256 * 1024
MAX_LOCAL_RESPONSE_BYTES = 4 * 1024 * 1024


def _call_local(model: str, system: str, user: str, max_output_tokens: int,
                request_timeout: int, max_retries: int) -> tuple[str, float]:
    """Chat completion against a loopback OpenAI-compatible server."""

    if not isinstance(system, str) or not isinstance(user, str):
        raise LocalPolicyError("local model prompts must be text")
    if not 1 <= int(max_output_tokens) <= 32768:
        raise LocalPolicyError("local max_output_tokens must be between 1 and 32768")
    if not 1 <= int(request_timeout) <= 600:
        raise LocalPolicyError("local request timeout must be between 1 and 600 seconds")
    if not 0 <= int(max_retries) <= 3:
        raise LocalPolicyError("local retries must be between 0 and 3")
    url = LOCAL_BASE_URL.rstrip("/") + "/chat/completions"
    body = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "temperature": 0.2,
        "max_tokens": max_output_tokens,
        "stream": False,
    }).encode()
    if len(body) > MAX_LOCAL_REQUEST_BYTES:
        raise LocalPolicyError(
            "local model request exceeds the fixed memory-safe input bound; split the task"
        )
    last_err: Exception | None = None
    # Refuse before the lock file is opened or created, then recheck on every
    # attempt so a governor transition while waiting/retrying also stops work.
    require_local_compute()
    with exclusive_local_model_slot():
        for attempt in range(1, max_retries + 2):
            require_local_compute()
            req = urllib.request.Request(
                url, data=body, method="POST",
                headers={"Content-Type": "application/json", "Connection": "close"})
            try:
                with urllib.request.urlopen(req, timeout=request_timeout) as resp:
                    response_bytes = resp.read(MAX_LOCAL_RESPONSE_BYTES + 1)
                if len(response_bytes) > MAX_LOCAL_RESPONSE_BYTES:
                    raise RuntimeError("local model response exceeded the fixed memory-safe bound")
                payload = json.loads(response_bytes)
                choices = payload.get("choices", [])
                text = choices[0].get("message", {}).get("content", "") if choices else ""
                if not isinstance(text, str) or not text.strip():
                    raise RuntimeError("local model returned no assistant text")
                return text, 0.0
            except Exception as e:                 # noqa: BLE001 - retry then surface
                last_err = e
                if attempt <= max_retries:
                    time.sleep(2 * attempt)
                    continue
                raise RuntimeError(
                    "ProofSwarm could not use the loopback model server at %s (%s); "
                    "the run stopped without a remote fallback" % (url, e)
                ) from e
    raise last_err if last_err else RuntimeError("_call_local failed unexpectedly")


def call_model(model: str, system: str, user: str,
               max_output_tokens: int = 16384,
               thinking_budget: int = 0,
               request_timeout: int = 120,
               max_retries: int = 2) -> tuple[str, float]:
    """Call one local model.  ``thinking_budget`` is kept for caller compatibility."""

    del thinking_budget
    configured_model = model if model == LOCAL_MODEL else LOCAL_MODEL
    return _call_local(
        configured_model, system, user, max_output_tokens, request_timeout, max_retries
    )


# Per-role reasoning hints are retained for caller compatibility. The current
# local OpenAI-compatible request does not send provider-specific thinking fields.
_THINKING_BUDGET = {
    "navigator": 0,
    "formalizer": 256,
    "prover": 4096,
    "counterexample": 0,
    "referee": 512,
}


# --- Mock LLM (no cost, used for local smoke tests) ---
MOCK_RESPONSES = {
    "navigator": '{"target": "formalizer", "ticket_id": "L1", "instruction": "formalize informal statement", "budget_usd": 0.05}',
    "formalizer": '''```lean4
import Mathlib.Topology.Basic
namespace Furstenberg23
theorem closed_invariant_sets_are_trivial
    (S : Set ℝ) (hS_closed : IsClosed S)
    (hT2 : ∀ x ∈ S, (2 * x) % 1 ∈ S)
    (hT3 : ∀ x ∈ S, (3 * x) % 1 ∈ S) :
    S = ∅ ∨ Set.Finite S ∨ S = Set.Ico 0 1 := by sorry
end Furstenberg23
```
```json
{"depends_on": ["IsClosed"], "ambiguities": [], "todos": ["close mod-1 wrapping"]}
```''',
    "prover": '''```lean4
theorem dummy : 1 + 1 = 2 := by rfl
```
```json
{"verified": true, "sorry_count": 0, "lean_errors": [], "duration_seconds": 0.5,
 "remaining_subgoals": []}
```''',
    "counterexample": '''```json
{"family": "rational_atomic", "candidates_checked": 5,
 "tolerance": 0, "counterexamples_found": [],
 "max_sup_norm_violation": 0, "certificate_sha256": "stub"}
```''',
    "referee": '''{"verdict": "open", "rationale": "mock pass — no real evidence",
 "issues": [], "next_action": "dispatch prover with concrete subgoal"}''',
}


_REQUEST_TIMEOUT = {
    "navigator": 60, "formalizer": 150, "prover": 180,
    "counterexample": 60, "referee": 150,
}


def _agent_call(agent: str, user_msg: str, mock: bool) -> tuple[str, float]:
    if mock:
        return MOCK_RESPONSES[agent], 0.0
    try:
        return call_model(AGENT_MODELS[agent], PROMPTS[agent], user_msg,
                          thinking_budget=_THINKING_BUDGET.get(agent, 0),
                          request_timeout=_REQUEST_TIMEOUT.get(agent, 120))
    except Exception as e:
        raise RuntimeError(f"required {agent} worker failed; run aborted") from e


# --- Output parsing ---
_LEAN_FENCE_RE = re.compile(r"```(?:lean4?|Lean4?)\s*\n(.*?)```", re.DOTALL)
_JSON_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*\n?(\{.*?\}|\[.*?\])\s*\n?```", re.DOTALL)
_BARE_JSON_RE = re.compile(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", re.DOTALL)


def _extract_lean_block(text: str) -> str | None:
    """Pick the LARGEST lean block (Formalizer may emit multiple candidates)."""
    matches = _LEAN_FENCE_RE.findall(text)
    if not matches:
        return None
    return max(matches, key=len).strip()


def _extract_json_block(text: str) -> dict | None:
    """Find the first valid JSON object in the text. Try fenced, then bare braces, then whole text."""
    for m in _JSON_FENCE_RE.findall(text):
        try:
            obj = json.loads(m.strip())
            if isinstance(obj, dict):
                return obj
        except Exception:
            continue
    # Greedy: try every {...} candidate, pick the first that parses.
    for m in _BARE_JSON_RE.findall(text):
        try:
            obj = json.loads(m)
            if isinstance(obj, dict):
                return obj
        except Exception:
            continue
    try:
        obj = json.loads(text.strip())
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


# --- Initial ticket seeding ---
SEEDS_BY_PROBLEM: dict[str, list[tuple[str, str]]] = {
    "furstenberg_x2x3": [
        ("L1", "Formalize the Furstenberg ×2 ×3 conjecture statement in Lean 4."),
        ("L2", "Prove the closed-set version: only closed sets in [0,1) jointly invariant under T_2 and T_3 are finite or all of [0,1)."),
        ("L3", "Formalize Rudolph's positive-entropy theorem statement: positive-entropy joint T_2,T_3-invariant ergodic measure is Lebesgue."),
        ("L4", "Lemma: joint T_2,T_3-invariance + ergodicity implies measure is in extreme convex hull of jointly invariant measures."),
        ("L5", "Numerical certificate: Bernoulli convolutions ν_β for β ∈ Q ∩ (0,1), denom ≤ 100, contain no jointly T_2,T_3-invariant member."),
        ("L6", "Lemma: h_μ(T_2) > 0 ⟺ h_μ(T_3) > 0."),
    ],
    "poincare_birkhoff": [
        ("L1", "Formalize the Poincaré–Birkhoff theorem in Lean 4: an area-preserving homeomorphism of the closed annulus S^1 × [0,1] that fixes both boundary circles setwise and twists them in opposite directions has at least two distinct fixed points in the interior."),
        ("L2", "Weaker statement: an area-preserving homeomorphism of the annulus with the twist condition has at least one fixed point."),
        ("L3", "Formalize the standard twist map family with generating function: f(θ,y) = (θ + α(y) + ε ∂V/∂θ, y - ε ∂V/∂y) for V smooth periodic in θ. State area preservation for small ε."),
        ("L4", "Numerical certificate: for the explicit twist map f(θ,y) = (θ + (1 − 2y) + ε sin(2π θ), y) with ε ∈ [−0.1, 0.1], find at least 2 fixed points via Newton's method on a fine grid and certify the count."),
        ("L5", "Lemma: fixed points of an annulus self-map are intersections of the graph of θ ↦ F(θ,y)−θ (mod 1) with zero, treated topologically."),
        ("L6", "Counterexample search: drop the area-preservation hypothesis and exhibit a twist map with 0 or 1 fixed points, confirming area-preservation is essential."),
        ("L7", "Lemma: for orientation-preserving circle homeomorphisms, the rotation number is well-defined and continuous in the C^0 topology."),
    ],
}


def seed_tickets(problem_id: str = "furstenberg_x2x3") -> list[ProofTicket]:
    """Create the L1..Ln tickets for the given problem if not already present."""
    seeds = SEEDS_BY_PROBLEM.get(problem_id)
    if seeds is None:
        raise ValueError(f"No seed tickets defined for problem '{problem_id}'. "
                         f"Available: {list(SEEDS_BY_PROBLEM)}")
    out = []
    for tid, stmt in seeds:
        existing = load_ticket(tid)
        if existing is not None and existing.problem_id == problem_id:
            out.append(existing)
            continue
        t = ProofTicket(ticket_id=tid, problem_id=problem_id, statement_informal=stmt)
        t.log("seed", "created", "from problem card")
        save_ticket(t)
        out.append(t)
    return out


# --- Main loop ---
def run(problem_id: str, max_rounds: int, mock: bool) -> dict:
    """Run the orchestrator until settlement or budget exhaustion."""
    if not mock:
        require_surface_calibration("orchestrator")
    seed_tickets(problem_id)
    summary = {"rounds": 0, "total_cost_usd": 0.0, "events": []}

    for round_idx in range(1, max_rounds + 1):
        summary["rounds"] = round_idx
        if PER_PROBLEM_BUDGET_USD > 0 and summary["total_cost_usd"] >= PER_PROBLEM_BUDGET_USD:
            summary["events"].append({"round": round_idx, "halt": "budget exhausted"})
            break

        # Build navigator context — explicit booleans so the Navigator stops re-dispatching the same agent.
        ticket_summary = []
        for t in sorted(list_tickets(problem_id), key=lambda x: x.ticket_id):
            last_kind = t.evidence[-1].kind if t.evidence else None
            referee_blockers: list[str] = []
            if t.verdict == Verdict.REFUTED_BY_REFEREE:
                # Pull blockers from the most recent referee_finding so Formalizer can repair.
                for e in reversed(t.evidence):
                    if e.kind == "referee_finding":
                        for iss in (e.metadata or {}).get("issues", []):
                            if iss.get("severity") == "blocker":
                                referee_blockers.append(iss.get("description", ""))
                        break
            ticket_summary.append({
                "id": t.ticket_id,
                "verdict": t.verdict.value,
                "has_lean_statement": bool(t.statement_lean),
                "has_lean_proof_evidence": any(e.kind == "lean_proof" and
                                                e.metadata.get("verify", {}).get("ok") is not None
                                                for e in t.evidence),
                "has_numerical_certificate": any(e.kind == "numerical_certificate" for e in t.evidence),
                "has_referee_finding": any(e.kind == "referee_finding" for e in t.evidence),
                "needs_formalizer_repair": t.verdict == Verdict.REFUTED_BY_REFEREE,
                "referee_blockers": referee_blockers[:3],
                "evidence_count": len(t.evidence),
                "last_evidence_kind": last_kind,
                "informal_preview": t.statement_informal[:160],
            })
        nav_user = json.dumps({
            "problem_id": problem_id, "tickets": ticket_summary, "round": round_idx,
            "cost_policy": "local-only; zero marginal API cost; caller bounds rounds",
            "dispatch_rules": {
                "formalizer_initial": "when has_lean_statement is false",
                "formalizer_repair":  "when needs_formalizer_repair is true; pass referee_blockers as instruction",
                "prover":     "when has_lean_statement is true AND has_lean_proof_evidence is false AND verdict != refuted_by_referee",
                "counterexample": "for tickets with claims of the form 'no X exists in family F' or 'forall X in F, P(X)'",
                "referee":   "after prover or counterexample evidence is in, BEFORE marking settled",
                "stop":      "if all tickets have verdict in {lean_verified, certificate_verified, disproven, needs_human} OR budget exhausted",
            }
        }, indent=2)
        nav_text, nav_cost = _agent_call("navigator", nav_user, mock)
        summary["total_cost_usd"] += nav_cost
        dispatch = _extract_json_block(nav_text)
        if not dispatch:
            summary["events"].append({"round": round_idx, "warn": "navigator returned no JSON",
                                      "text": nav_text[:300]})
            break
        target = (dispatch.get("target") or "").strip().lower()
        # Normalize common variants the LLM emits.
        target_aliases = {
            "counterexample hunter": "counterexample", "counterexample_hunter": "counterexample",
            "ce_hunter": "counterexample", "lemma_miner": "counterexample",
        }
        target = target_aliases.get(target, target)
        ticket_id = dispatch.get("ticket_id")
        instruction = dispatch.get("instruction", "")
        ticket = load_ticket(ticket_id) if ticket_id else None
        summary["events"].append({"round": round_idx, "dispatch": dispatch,
                                  "nav_cost_usd": round(nav_cost, 4)})
        # Live progress output (otherwise stdout is buffered until end).
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        print(f"[r{round_idx:02d}] target={target:14s} ticket={ticket_id or '-':4s} "
              f"cost=${summary['total_cost_usd']:.4f} | {instruction[:80]}",
              flush=True)
        if target == "human":
            summary["events"].append({"round": round_idx, "halt": "navigator routed to human"})
            break
        if not ticket:
            summary["events"].append({"round": round_idx, "warn": f"ticket {ticket_id} not found"})
            continue

        # Dispatch
        if target == "formalizer":
            # If repairing, expose the Referee's prior blockers and the prior Lean statement.
            extra = ""
            if ticket.verdict == Verdict.REFUTED_BY_REFEREE and ticket.statement_lean:
                last_referee = next((e for e in reversed(ticket.evidence)
                                     if e.kind == "referee_finding"), None)
                blockers = []
                if last_referee:
                    for iss in (last_referee.metadata or {}).get("issues", []):
                        blockers.append(f"[{iss.get('severity')}] {iss.get('description')}")
                extra = ("\n\n--- PRIOR LEAN STATEMENT (REFUTED) ---\n"
                         + ticket.statement_lean
                         + "\n\n--- REFEREE BLOCKERS (must address) ---\n"
                         + "\n".join(blockers))
            user = (f"Ticket {ticket.ticket_id}\nInformal: {ticket.statement_informal}\n"
                    f"Instruction: {instruction}{extra}")
            text, cost = _agent_call("formalizer", user, mock)
            summary["total_cost_usd"] += cost
            lean_block = _extract_lean_block(text)
            meta = _extract_json_block(text) or {}
            if lean_block:
                ticket.statement_lean = lean_block
                # On repair, reset verdict to OPEN so Prover gets another chance.
                if ticket.verdict == Verdict.REFUTED_BY_REFEREE:
                    ticket.verdict = Verdict.OPEN
                ticket.add_evidence(Evidence(
                    kind="informal_argument", content=lean_block, produced_by="formalizer",
                    cost_usd=cost, metadata=meta,
                ))
            ticket.log("formalizer", "produced_lean_statement",
                       f"chars={len(lean_block) if lean_block else 0}, repair={bool(extra)}")
            save_ticket(ticket)

        elif target == "prover":
            user = f"Ticket {ticket.ticket_id}\nLean statement:\n{ticket.statement_lean or 'NONE'}\nInstruction: {instruction}"
            text, cost = _agent_call("prover", user, mock)
            summary["total_cost_usd"] += cost
            lean_block = _extract_lean_block(text) or ""
            if not lean_block:
                raise RuntimeError("required prover worker returned no Lean source")
            verify = lean_verify(lean_block, "", imports=[]) if not mock else None
            verify_dict = verify.model_dump() if verify else {"ok": False, "mock": True}
            compiled_source = compose_lean_source(lean_block, "", imports=[])
            verify_dict["formal_statement_sha256"] = hashlib.sha256(
                (ticket.statement_lean or "").encode("utf-8")
            ).hexdigest()
            ticket.add_evidence(Evidence(
                kind="lean_proof", content=compiled_source, produced_by="prover",
                sha256=verify_dict.get("source_sha256"),
                cost_usd=cost, metadata={"verify": verify_dict},
            ))
            ticket.log("prover", "drafted_proof",
                       f"verified={verify_dict.get('ok')}, sorry={verify_dict.get('sorry_count')}")
            save_ticket(ticket)

        elif target == "counterexample":
            # Run a real numerical sweep — this is local Python, no LLM cost beyond the agent's
            # parameter-design call.
            user = f"Ticket {ticket.ticket_id}\nClaim: {ticket.statement_informal}\nInstruction: {instruction}"
            text, cost = _agent_call("counterexample", user, mock)
            summary["total_cost_usd"] += cost
            # Pick the family by problem.
            sweeps: list[tuple[str, dict]]
            if problem_id == "poincare_birkhoff":
                sweeps = [("twist_map_fixed_points", {
                    "epsilon": [0.02, 0.05, 0.1, 0.15, 0.2, -0.1],
                    "grid_n": [120],
                })]
            else:  # furstenberg_x2x3 default
                sweeps = [
                    ("rational_atomic", {"denominators": [q for q in range(2, 101)]}),
                    ("bernoulli_convolution", {"beta": [k / 20 for k in range(1, 20)]}),
                ]
            results = []
            for family, params in sweeps:
                req = NumericalSearchRequest(family=family, parameter_grid=params,
                                             invariance_tolerance=1e-3)
                try:
                    res = run_numerical_search(req)
                    results.append({"family": family, **res.model_dump()})
                except Exception as e:
                    results.append({"family": family, "error": str(e)})
            ticket.add_evidence(Evidence(
                kind="numerical_certificate", content=json.dumps(results, indent=2),
                produced_by="counterexample", cost_usd=cost, metadata={"agent_text": text[:500]},
            ))
            ticket.log("counterexample", "ran_sweep", f"families={[r['family'] for r in results]}")
            save_ticket(ticket)

        elif target == "referee":
            user = f"Ticket {ticket.ticket_id}\nInformal: {ticket.statement_informal}\nLean: {ticket.statement_lean}\nEvidence count: {len(ticket.evidence)}\nLast evidence kind: {ticket.evidence[-1].kind if ticket.evidence else 'none'}"
            text, cost = _agent_call("referee", user, mock)
            summary["total_cost_usd"] += cost
            verdict_dict = _extract_json_block(text) or {}
            v = verdict_dict.get("verdict", "open")
            try:
                requested = Verdict(v)
            except ValueError:
                requested = Verdict.OPEN
            ticket.verdict, gate_reason = gate_verdict(ticket, requested)
            verdict_dict["requested_verdict"] = requested.value
            verdict_dict["effective_verdict"] = ticket.verdict.value
            verdict_dict["verification_gate"] = gate_reason
            ticket.add_evidence(Evidence(
                kind="referee_finding", content=text, produced_by="referee",
                cost_usd=cost, metadata=verdict_dict,
            ))
            ticket.log("referee", "verdict", v)
            save_ticket(ticket)

        else:
            summary["events"].append({"round": round_idx, "warn": f"unknown target {target}"})

        # Settlement check
        all_settled = all(t.verdict not in (Verdict.OPEN,) for t in list_tickets(problem_id))
        if all_settled:
            summary["events"].append({"round": round_idx, "halt": "all tickets settled"})
            break

    return summary


def main() -> int:
    set_process_below_normal()
    ap = argparse.ArgumentParser()
    ap.add_argument("--problem", default="furstenberg_x2x3")
    ap.add_argument("--rounds", type=int, default=12)
    ap.add_argument("--mock", action="store_true", help="No LLM calls; deterministic stubs.")
    ap.add_argument(
        "--calibrate",
        choices=(*CALIBRATION_SURFACES, "all"),
        help="Run synthetic controls against the configured local model, then exit.",
    )
    args = ap.parse_args()
    try:
        if args.calibrate:
            surfaces = CALIBRATION_SURFACES if args.calibrate == "all" else (args.calibrate,)

            def invoke(_role: str, system: str, user: str) -> tuple[str, float]:
                return call_model(
                    LOCAL_MODEL,
                    system,
                    user,
                    max_output_tokens=256,
                    request_timeout=120,
                    max_retries=0,
                )

            for surface in surfaces:
                _receipt, path = run_surface_calibration(surface, invoke)
                require_surface_calibration(surface)
                print(f"Calibration passed for {surface}: {path}")
            return 0
        summary = run(args.problem, args.rounds, args.mock)
    except (LocalPolicyError, RuntimeError) as exc:
        print(f"ProofSwarm stopped safely: {exc}", file=sys.stderr)
        return 2
    out_path = TRACE_DIR / f"run_{int(time.time())}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    print(json.dumps(summary, indent=2))
    print(f"\nTrace written to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
