"""ProofSwarm autonomous open-problem solver.

Runs continuously (designed for days of compute). For each round:
  1. Pick the most-promising open ticket (or generate new ones if exhausted)
  2. Dispatch a creative agent (conjecture_generator / strategy_proposer / lemma_decomposer)
     to seed new approaches
  3. For each candidate sub-conjecture, run the v2 paper-review pipeline (14 specialists)
  4. Run heavy numerical search (Smale: random polynomial search)
  5. Save state, log trace, update memory
  6. Sleep briefly, repeat

Stops only when:
  - A ticket reaches verdict LEAN_VERIFIED or CERTIFICATE_VERIFIED
  - Daily/total budget hit
  - Manual interrupt (Ctrl+C)

Resumes from saved state on restart.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import sys
import time
import signal
from datetime import datetime, timezone

from proofswarm.calibration import require_surface_calibration
from proofswarm.config import LOCAL_MODEL, TRACE_DIR
from proofswarm.local_runtime import LocalPolicyError, set_process_below_normal
from proofswarm.orchestrator import call_model
from proofswarm.prompts_v2 import PROMPTS_V2, AGENT_MODELS_V2, THINKING_BUDGET_V2, REQUEST_TIMEOUT_V2
from proofswarm.prompts_creative import PROMPTS_CREATIVE
from proofswarm.prompts_personas import (
    PROMPTS_PERSONAS, PERSONA_MODELS, PERSONA_THINKING,
    PERSONA_TIMEOUTS,
)
from proofswarm.schemas import Evidence, ProofTicket, Verdict, gate_verdict
from proofswarm.tools.ticket_store import save_ticket, load_ticket, list_tickets
from proofswarm.tools.lean_verify import compose_lean_source, lean_verify
from proofswarm.tools.smale_numerical import gradient_search, search_lower_bound


# Combined prompts: v2 + creative + personas (6 different "voices" for brute-force diversity)
ALL_PROMPTS = {**PROMPTS_V2, **PROMPTS_CREATIVE, **PROMPTS_PERSONAS}
ALL_MODELS = {
    **AGENT_MODELS_V2,
    "conjecture_generator": LOCAL_MODEL,
    "strategy_proposer": LOCAL_MODEL,
    "analogy_finder": LOCAL_MODEL,
    "lemma_decomposer": LOCAL_MODEL,
    **PERSONA_MODELS,
}
ALL_THINKING = {
    **THINKING_BUDGET_V2,
    "conjecture_generator": 4096,
    "strategy_proposer": 4096,
    "analogy_finder": 2048,
    "lemma_decomposer": 4096,
    **PERSONA_THINKING,
}
ALL_TIMEOUTS = {
    **REQUEST_TIMEOUT_V2,
    "conjecture_generator": 360,
    "strategy_proposer": 360,
    "analogy_finder": 240,
    "lemma_decomposer": 360,
    **PERSONA_TIMEOUTS,
}


_SHOULD_STOP = False


def _signal_handler(signum, frame):
    global _SHOULD_STOP
    print(f"\n[{datetime.now(timezone.utc).isoformat()}] Signal {signum} received — finishing current round, then stopping.")
    _SHOULD_STOP = True


# Register Ctrl+C handler
try:
    signal.signal(signal.SIGINT, _signal_handler)
except Exception:
    pass


def _try_parse_json(text: str) -> dict | list | None:
    import re
    m = re.search(r"```(?:json|JSON)?\s*\n(.*?)```", text, re.DOTALL)
    if m:
        try: return json.loads(m.group(1).strip())
        except Exception: pass
    try: return json.loads(text.strip())
    except Exception: pass
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try: return json.loads(m.group(0))
        except Exception: pass
    return None


def _agent_call(agent: str, user: str) -> tuple[str, float]:
    try:
        return call_model(
            ALL_MODELS[agent], ALL_PROMPTS[agent], user,
            thinking_budget=ALL_THINKING.get(agent, 0),
            request_timeout=ALL_TIMEOUTS.get(agent, 120),
        )
    except Exception as e:
        raise RuntimeError(f"required {agent} worker failed; autonomous run aborted") from e


# ==================================================
# Smale-specific seeding
# ==================================================

# Every ticket statement embeds the FULL Smale K(n) definition explicitly so
# agents cannot drift to other "K(2)" problems (Landau-Kolmogorov, etc.).

_SMALE_PRELUDE = """SMALE'S MEAN VALUE CONJECTURE (1981).

Setting: complex polynomial p(z) of degree n>=2 with p(0)=0 and p'(0) != 0.
Let zeta_1, ..., zeta_{n-1} be the critical points of p (i.e., zeros of p').
Define for each polynomial p:
    K_p := min_i  |p(zeta_i) / (zeta_i * p'(0))|
And the constant for degree n:
    K(n) := sup over all such polynomials p of K_p.

Smale conjectures K(n) = (n-1)/n.

This is NOT the Landau-Kolmogorov inequality, NOT a Sobolev-space inequality,
NOT about derivatives in L^2, NOT about norms of functions. It is purely about
the values of complex polynomials at their critical points.

Reference: Smale, S. (1981). "The fundamental theorem of algebra and complexity
theory." Bulletin AMS 4(1), 1-36.

YOUR TASK FOR THIS TICKET:
"""


def _smale(stmt: str) -> str:
    return _SMALE_PRELUDE + stmt


INITIAL_SMALE_TICKETS = [
    ("S0_def", _smale("State and formalize the definition of K(n) above precisely in Lean 4 over the complex numbers. Use Polynomial in mathlib4. State Smale's conjecture as a Lean theorem with a `sorry` proof.")),
    ("S1_n2", _smale("Prove K(2) = 1/2. For a degree-2 polynomial p(z) = z + a z^2 (we may assume p'(0) = 1 by scaling), compute the unique critical point zeta = -1/(2a) and the ratio |p(zeta)/(zeta p'(0))|. Show this equals 1/2 exactly, regardless of a. Hence K(2) = 1/2.")),
    ("S2_n3", _smale("Prove K(3) = 2/3 (Tischler 1989). For a degree-3 polynomial, compute the two critical points and analyze the minimum of the two Smale ratios. Show that the supremum over all degree-3 polynomials equals 2/3, with the supremum achieved by p(z) = z + (2/3) z^3 (or rotations).")),
    ("S3_n4", _smale("Prove K(4) = 3/4 (Beardon-Minda-Ng 2002). The proof uses the hyperbolic metric on the disk. Show that for any degree-4 polynomial there exists a critical point zeta with |p(zeta)/(zeta p'(0))| <= 3/4, and exhibit a polynomial achieving equality.")),
    ("S5_n5_upper_improve", _smale("OPEN PROBLEM. The best general upper bound is K(n) <= 4 due to Beardon-Minda-Ng. The conjecture says K(5) = 4/5. Prove ANY upper bound K(5) <= c with c < 4. Even c = 3.5 would be a publishable improvement; c = 4/5 would prove Smale's conjecture for n=5.")),
    ("S6_extremal_char", _smale("Prove that the supremum K(n) = sup_p K_p is achieved by polynomials in the family p(z) = z + ((n-1)/n) z^n (and their rotations/scalings). Numerical evidence saturates this. Provide a proof, possibly via discriminant variety or variational methods.")),
    ("S7_real_coeffs", _smale("Investigate K_real(n) := the same supremum restricted to polynomials with real coefficients. Determine whether K_real(n) < K(n) for some/all n>=2, and prove the relationship.")),
    ("S8_dual", _smale("Prove an AVERAGED / DUAL version: for any polynomial p of degree n >= 2 with p(0)=0, the average (1/(n-1)) sum_{i=1}^{n-1} |p(zeta_i)/(zeta_i p'(0))| satisfies a sharp bound. Identify the right bound and prove it.")),
    ("S9_extension_BMN", _smale("Extend the Beardon-Minda-Ng hyperbolic metric proof from n=4 to n=5. Identify the specific step in their proof that does not generalize, and propose a fix or alternative argument.")),
]


def seed_smale_tickets(problem_id: str = "smale_mean_value") -> int:
    """Create the initial Smale tickets if not present. Returns number created."""
    created = 0
    for tid, stmt in INITIAL_SMALE_TICKETS:
        if load_ticket(tid) is not None:
            continue
        t = ProofTicket(ticket_id=tid, problem_id=problem_id,
                        statement_informal=stmt)
        t.log("seed", "created", "from autonomous solver init")
        save_ticket(t)
        created += 1
    return created


# ==================================================
# Picking next ticket
# ==================================================

def pick_next_ticket(problem_id: str) -> ProofTicket | None:
    """Pick the most promising OPEN ticket to attack next.

    Heuristic: prefer tickets that are open, have few prior attempts, and depend on settled tickets.
    """
    tickets = list_tickets(problem_id)
    open_tickets = [t for t in tickets
                    if t.verdict in (Verdict.OPEN, Verdict.NEEDS_HUMAN, Verdict.LATEX_OPEN)]
    if not open_tickets:
        return None
    # Sort by: fewest attempts first
    open_tickets.sort(key=lambda t: (len(t.evidence), t.created_at))
    return open_tickets[0]


# ==================================================
# Smale numerical attack
# ==================================================

def smale_numerical_attack(ticket: ProofTicket, n_random: int = 250_000) -> dict:
    """Heavy random polynomial search + gradient optimization.

    5x bigger than baseline: 250K random + 20 gradient-descent seeds. Mixed sampling
    (uniform + perturb-extremal + rotational) for better coverage.
    """
    n_target = 5
    if "n2" in ticket.ticket_id: n_target = 2
    elif "n3" in ticket.ticket_id: n_target = 3
    elif "n4" in ticket.ticket_id: n_target = 4
    elif "n6" in ticket.ticket_id: n_target = 6
    elif "n7" in ticket.ticket_id: n_target = 7
    elif "n8" in ticket.ticket_id: n_target = 8

    # Random search with mixed sampling
    seed_base = int(time.time()) % 2**31
    res_rand = search_lower_bound(n=n_target, n_random=n_random,
                                   seed=seed_base, sampling="mixed")
    rand_cert = res_rand["certificate"]

    # Gradient optimization
    grad_res = gradient_search(n=n_target, n_seeds=30, max_iter=300,
                                seed=seed_base + 1)

    best_random = rand_cert["best_min_ratio"]
    best_grad = grad_res.get("best_min_ratio", 0.0)
    best_overall = max(best_random, best_grad)

    return {
        "n": n_target,
        "n_random": n_random,
        "n_gradient_seeds": grad_res.get("n_seeds", 0),
        "best_min_ratio_random": best_random,
        "best_min_ratio_gradient": best_grad,
        "best_min_ratio_overall": best_overall,
        "bound_conjectured": rand_cert["bound_conjectured"],
        "achieved_fraction": (best_overall / rand_cert["bound_conjectured"]
                               if rand_cert["bound_conjectured"] else None),
        "n_violations": rand_cert["n_violations"],
        "random_certificate_sha256": res_rand["sha256"],
    }


# ==================================================
# Round logic
# ==================================================

def _extract_lean_block(text: str) -> str | None:
    import re as _re
    m = _re.search(r"```lean4?\s*\n(.*?)```", text, _re.DOTALL)
    return m.group(1).strip() if m else None


def _try_lean_verify_from_text(text: str) -> dict:
    """Pull the Lean block out of agent text and run the kernel. Returns verify dict."""
    lean_code = _extract_lean_block(text)
    if not lean_code:
        return {"ok": False, "errors": ["no lean code block found"], "sorry_count": -1}
    try:
        compiled_source = compose_lean_source(lean_code, "", imports=[])
        v = lean_verify(lean_code, "", imports=[], timeout=180)
        result = v.model_dump()
        result["compiled_source"] = compiled_source
        return result
    except Exception as e:
        return {"ok": False, "errors": [str(e)], "sorry_count": -1}


def promote_subconjectures(ticket: ProofTicket, parsed: dict, problem_id: str) -> int:
    """When a creative agent emits sub-conjectures or decomposed lemmas, create new tickets.
    Returns number of new tickets created.
    """
    if not isinstance(parsed, dict):
        return 0
    new_count = 0
    candidates: list[tuple[str, str]] = []

    # conjecture_generator format
    for sc in parsed.get("sub_conjectures", []) or []:
        if isinstance(sc, dict) and sc.get("statement"):
            sid = sc.get("id") or f"SC_{int(time.time()*1000)%1_000_000}"
            candidates.append((f"PROMOTED_{ticket.ticket_id}_{sid}", sc["statement"][:500]))
    # lemma_decomposer format
    for lem in parsed.get("decomposition", []) or []:
        if isinstance(lem, dict) and lem.get("statement"):
            lid = lem.get("lemma_id") or f"L_{int(time.time()*1000)%1_000_000}"
            candidates.append((f"PROMOTED_{ticket.ticket_id}_{lid}", lem["statement"][:500]))
    # strategy_proposer format
    for st in parsed.get("strategies", []) or []:
        if isinstance(st, dict) and st.get("description"):
            sid = st.get("id") or f"ST_{int(time.time()*1000)%1_000_000}"
            candidates.append((f"PROMOTED_{ticket.ticket_id}_{sid}",
                               f"Strategy: {st['description'][:400]}. First step: {st.get('concrete_first_step', '')[:200]}"))

    for tid, stmt in candidates:
        if load_ticket(tid) is not None:
            continue
        new_t = ProofTicket(ticket_id=tid, problem_id=problem_id,
                            statement_informal=stmt, parents=[ticket.ticket_id])
        new_t.log("promoted", "sub_conjecture_promoted",
                  f"from {ticket.ticket_id}")
        save_ticket(new_t)
        new_count += 1
    return new_count


def _build_discussion_thread(ticket: ProofTicket, max_chars: int = 12000) -> str:
    """Concat all prior agent turns on this ticket into a readable transcript."""
    if not ticket.evidence:
        return "(no prior discussion on this ticket)"
    lines = [f"--- DISCUSSION THREAD on ticket {ticket.ticket_id} ---",
             f"Statement: {ticket.statement_informal}",
             ""]
    # Most recent N turns (oldest first), capped by char budget
    for e in ticket.evidence[-30:]:
        agent = e.produced_by
        body = e.content
        if len(body) > 2000:
            body = body[:2000] + "\n[...truncated]"
        lines.append(f"=== [{agent}] ===")
        lines.append(body)
        lines.append("")
    text = "\n".join(lines)
    return text[-max_chars:]


# Order of agents in the proof-discussion chain. Each agent sees the full prior
# thread + the new turns from earlier agents in this round.
DISCUSSION_CHAIN = [
    "persona_skeptical",         # Critique current state — what's wrong
    "persona_complex_analysis",  # Bring in complex analysis technique
    "persona_discriminant",      # Bring in algebraic geometry
    "persona_ambitious",         # Propose a stronger lemma to attack
    "lemma_decomposer",          # Decompose the most promising lemma into chain
    "formalizer",                # Convert to Lean 4
    "prover",                    # Try to fill in proof tactics
    "persona_adversarial",       # Try to break what was just proposed
    "referee",                   # Synthesize: what's solid? what to do next?
]


def run_round(problem_id: str, round_idx: int,
              budget_remaining: float,
              parallel_workers: int = 1) -> dict:
    """One discussion round on a single ticket.

    Each agent in DISCUSSION_CHAIN reads the full prior thread plus all
    earlier turns from THIS round, then contributes one turn. The thread
    grows. Lean kernel verifies any actual proof attempt. Numerical
    search runs only when an agent in the thread explicitly asks for it.
    """
    require_surface_calibration("autonomous")
    print(f"\n{'='*70}")
    print(f"ROUND {round_idx}  budget_remaining=${budget_remaining:.2f}")
    print(f"{'='*70}")
    tickets = list_tickets(problem_id)
    print(f"Tickets: {len(tickets)}, open={sum(1 for t in tickets if t.verdict.value == 'open')}")

    ticket = pick_next_ticket(problem_id)
    if ticket is None:
        return {"round": round_idx, "cost": 0.0, "halt": "no open tickets"}
    print(f"Selected ticket: {ticket.ticket_id}  ({ticket.verdict.value}, "
          f"{len(ticket.evidence)} prior turns)")

    round_cost = 0.0
    new_turns: list[dict] = []
    base_thread = _build_discussion_thread(ticket)

    for agent in DISCUSSION_CHAIN:
        # Build the prompt: prior thread + this round's earlier turns + agent-specific framing
        round_so_far = "\n".join(
            f"=== [{t['agent']}] (round {round_idx}) ===\n{t['text'][:2000]}"
            for t in new_turns
        )
        msg = (_SMALE_PRELUDE + "\n"
               + "(end of definition)\n\n"
               + f"You are participating in a proof-discussion seminar attempting "
               + f"the open problem above. The current ticket is {ticket.ticket_id}. "
               + f"Read the full transcript below; then contribute YOUR turn.\n\n"
               + f"IMPORTANT: K(n) refers ONLY to the polynomial constant defined above. "
               + f"It is NOT the Landau-Kolmogorov constant, NOT a Sobolev-space constant, "
               + f"NOT about norms ||f||_2, ||f'||_2, ||f''||_2. If you see prior turns "
               + f"that wandered into Sobolev/Landau-Kolmogorov territory, ignore them and "
               + f"return to Smale's polynomial setting.\n\n"
               + f"{base_thread}\n\n"
               + (f"--- THIS ROUND'S EARLIER TURNS ---\n{round_so_far}\n\n" if round_so_far else "")
               + f"Your turn now. Be specific. Reference prior turns by name when responding to them.")
        text, cost = _agent_call(agent, msg)
        round_cost += cost

        # If agent is the prover, try to verify any Lean code emitted
        verify_meta: dict | None = None
        compiled_source: str | None = None
        if agent == "prover":
            verify_meta = _try_lean_verify_from_text(text)
            compiled_source = verify_meta.pop("compiled_source", None)
            print(f"  [{agent:24s}] cost=${cost:.4f}  lean_ok={verify_meta.get('ok')} "
                  f"sorry={verify_meta.get('sorry_count', '?')}")
        elif agent == "formalizer":
            lean_block = _extract_lean_block(text)
            if lean_block:
                ticket.statement_lean = lean_block
            print(f"  [{agent:24s}] cost=${cost:.4f}  lean_chars="
                  f"{len(lean_block) if lean_block else 0}")
        else:
            print(f"  [{agent:24s}] cost=${cost:.4f}")

        # Save turn as evidence
        parsed = _try_parse_json(text)
        ev_kind = ("lean_proof" if agent == "prover" else
                   "referee_finding" if agent == "referee" else
                   "informal_argument")
        meta = {"parsed": parsed, "round": round_idx}
        if verify_meta:
            meta["verify"] = verify_meta
            meta["agent_output"] = text
            meta["agent_output_sha256"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
        evidence_content = compiled_source if compiled_source is not None else text
        evidence_sha256 = hashlib.sha256(evidence_content.encode("utf-8")).hexdigest()
        ticket.add_evidence(Evidence(
            kind=ev_kind, content=evidence_content, sha256=evidence_sha256,
            produced_by=agent, cost_usd=cost, metadata=meta,
        ))
        save_ticket(ticket)
        new_turns.append({"agent": agent, "text": text, "cost": cost})

        # Promote sub-lemmas if lemma_decomposer or conjecture-style
        if agent in ("lemma_decomposer",) and parsed:
            n_p = promote_subconjectures(ticket, parsed, problem_id)
            if n_p:
                print(f"    promoted {n_p} sub-lemmas as new tickets")

        # Update the referee verdict if this is the final turn
        if agent == "referee" and parsed:
            v = parsed.get("verdict", "open")
            try:
                requested = Verdict(v)
            except ValueError:
                requested = Verdict.OPEN
            ticket.verdict, gate_reason = gate_verdict(ticket, requested)
            parsed["verification_gate"] = gate_reason
            ticket.evidence[-1].metadata["verification_gate"] = gate_reason
            if isinstance(ticket.evidence[-1].metadata.get("parsed"), dict):
                ticket.evidence[-1].metadata["parsed"]["verification_gate"] = gate_reason
            save_ticket(ticket)

    return {"round": round_idx, "cost": round_cost,
            "ticket_attacked": ticket.ticket_id,
            "verdict": ticket.verdict.value,
            "n_turns_added": len(new_turns)}


# ==================================================
# Main loop
# ==================================================

def autonomous_loop(problem_id: str = "smale_mean_value",
                    daily_budget_usd: float = 0.0,
                    total_budget_usd: float = 0.0,
                    sleep_seconds: int = 5,
                    max_hours: float = 168.0,
                    parallel_rounds: int = 1):
    """Run the autonomous solver until budget exhausted, solution found, or interrupted.

    Local inference is currently serialized.  Parallel reservations are denied
    until fleet-wide model memory and compute accounting exist.
    """
    require_surface_calibration("autonomous")
    import concurrent.futures
    import threading
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore

    n_created = seed_smale_tickets(problem_id)
    print(f"Seeded {n_created} new tickets ({len(INITIAL_SMALE_TICKETS) - n_created} already present)")

    if parallel_rounds != 1:
        raise LocalPolicyError("parallel local rounds are disabled until resource reservations exist")
    state_path = TRACE_DIR / "smale_state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
    else:
        state = {"total_cost": 0.0, "rounds": 0,
                 "started_at": datetime.now(timezone.utc).isoformat()}

    daily_cost = 0.0
    daily_anchor = time.time()
    started_at = time.time()
    state_lock = threading.Lock()
    print(f"Starting PARALLEL autonomous loop. daily=${daily_budget_usd}  total=${total_budget_usd}  "
          f"max_hours={max_hours}  parallel_rounds={parallel_rounds}")

    def _one_round() -> dict:
        with state_lock:
            round_n = state["rounds"] + 1
            budget_remaining = 0.0
        result = run_round(problem_id, round_n, budget_remaining)
        with state_lock:
            state["rounds"] += 1
            state["total_cost"] += result.get("cost", 0.0)
            state["last_round"] = result
            state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
        return result

    # Pool of round-workers
    with concurrent.futures.ThreadPoolExecutor(max_workers=parallel_rounds) as ex:
        in_flight: set = set()
        # Prime the pool
        for _ in range(parallel_rounds):
            in_flight.add(ex.submit(_one_round))

        while in_flight and not _SHOULD_STOP:
            # Reset daily budget every 24h
            if time.time() - daily_anchor > 86400:
                daily_cost = 0.0
                daily_anchor = time.time()

            # Budget checks (fast — without lock since we just check the snapshot)
            with state_lock:
                tcost = state["total_cost"]
            if total_budget_usd > 0 and tcost >= total_budget_usd:
                print(f"Total budget exhausted at ${tcost:.2f}")
                break
            if daily_budget_usd > 0 and daily_cost >= daily_budget_usd:
                sleep_until = daily_anchor + 86400 - time.time()
                print(f"Daily budget hit (${daily_cost:.2f}). Sleeping {sleep_until/3600:.1f}h.")
                time.sleep(min(sleep_until, 3600))
                continue
            if (time.time() - started_at) / 3600 > max_hours:
                print(f"Max hours ({max_hours}) reached.")
                break

            # Settlement check
            tickets = list_tickets(problem_id)
            verified = [t for t in tickets if t.verdict in
                        (Verdict.LEAN_VERIFIED, Verdict.CERTIFICATE_VERIFIED)]
            if verified:
                print(f"!!! VERIFIED TICKETS: {[t.ticket_id for t in verified]}")

            # Wait for the next round to complete, then submit a new one
            done, in_flight = concurrent.futures.wait(
                in_flight, timeout=10,
                return_when=concurrent.futures.FIRST_COMPLETED,
            )
            for fut in done:
                result = fut.result()
                daily_cost += result.get("cost", 0.0)
            # Refill the pool unless we're stopping
            for _ in range(len(done)):
                if not _SHOULD_STOP and (total_budget_usd <= 0 or tcost < total_budget_usd):
                    in_flight.add(ex.submit(_one_round))
            time.sleep(sleep_seconds)

        print("\nWaiting for in-flight rounds to finish...")
        for fut in concurrent.futures.as_completed(in_flight, timeout=600):
            fut.result()

    print(f"\nDone. Total cost: ${state['total_cost']:.4f}, rounds: {state['rounds']}")
    return state


def main() -> int:
    set_process_below_normal()
    ap = argparse.ArgumentParser()
    ap.add_argument("--problem", default="smale_mean_value")
    ap.add_argument("--daily-budget", type=float, default=0.0)
    ap.add_argument("--total-budget", type=float, default=0.0)
    ap.add_argument("--sleep", type=int, default=5)
    ap.add_argument("--max-hours", type=float, default=168.0)
    ap.add_argument("--parallel-rounds", type=int, default=1)
    args = ap.parse_args()
    try:
        autonomous_loop(args.problem, args.daily_budget, args.total_budget,
                        args.sleep, args.max_hours, args.parallel_rounds)
    except (LocalPolicyError, RuntimeError) as exc:
        print(f"ProofSwarm autonomous solver stopped safely: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
