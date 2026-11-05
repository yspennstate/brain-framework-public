#!/usr/bin/env python3
"""recall_policy_regression.py - the guard for recall_policy.json's canonical intent gate.

RUN THIS BEFORE AND AFTER ANY EDIT TO recall_policy.json. It is the test that proved a
plausible-looking one-word change was wrong, and it exists so the next agent cannot weaken the
router silently.

WHY IT EXISTS (2026-08-17). The gate routes the canonical brain-improvement contract on a
MODIFICATION ACTION plus a COMPLETE REFERENT GROUP (or the exact canonical name), with vetoes from
human_terms / descriptive_terms / negators. I found what looked like a clean defect: "brain health
dashboard" is a phrase the trigger registry itself advertises, and a minimal pair showed one word
killing it -

    "please build a brain dashboard"                              rank 1
    "please build a brain health dashboard"                       miss
    "improve the brain health dashboard for the cognitive system" miss   (valid action AND referent)

The obvious fix is to drop "health" from human_terms. I wrote hostile decoys to argue the opposite
case before believing my own, applied the change, and the decoys fired:

    "improve my brain health with diet and exercise"           -> rank 1
    "improve the memory system for brain health in aging"      -> rank 1
    "build a memory system that supports brain health"         -> rank 1
    "audit the cognitive system for brain health decline"      -> rank 1

Human-health questions carrying a real action and a real referent group would have routed to a
contract about an AI cognitive system. "health" is load-bearing and the change was reverted.

SO THE KNOWN COLLISION IS DELIBERATE, NOT A BUG: under a flat term-list veto, "brain health
dashboard" cannot be rescued without admitting genuine health questions. Any future fix must be a
NARROWER RULE (e.g. exempt the veto only when a canonical_group is present, or require two clinical
markers) - never removal of the term. That is what the HEALTH_COLLISION block below documents, and
it is expected to stay red.

Usage: python recall_policy_regression.py            (exit 0 = no regression)
Read-only. Each probe is one live brain.py recall, so a full run takes a few minutes.
"""
import hashlib, io, json, os, re, subprocess, sys, time

if any(a in ("-h", "--help") for a in sys.argv[1:]):
    # Discovery is not execution: this file does its work at module scope, so the
    # guard must come before the first statement that runs.
    print(__doc__ or os.path.basename(__file__))
    raise SystemExit(0)


# Per-probe evidence, so a receipt can carry structured rows rather than prose.
# Peer review 2026-08-17 asked for exact per-probe stdout/exit/timestamps.
_PROBE_LOG = []

BRAIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "brain.py")
NODE = "canonical_brain_improvement_prompt_v2_2026_08_15"
NODES = (NODE, "canonical_brain_improvement_prompt_v0_2_2026_08_18")   # the contract's two bound twins
SLUG = re.compile(r"^\s*[\d.]+\s+\[[^\]]*\]\s+(\S+)\s*$")

# Must reach rank 1. Seven from the integrator's RECALL_EVALUATION.json (2026-08-15).
POSITIVES = [
    "implement more canonical brain improvements",
    "implement cononical brian improvments",
    "fix the maht brain functionality",
    "Please improve the AI math brain and its mistake detection",
    "build an AI brain dashboard",
    "port Elazar's vanilla brain safely",
    "Overhaul Codex's longitudinal self-correction substrate",
    # His own idiom, added 2026-09-05 with the "work" action term: on the 200-prompt ledger the
    # gate routed 38 of 200 before it and 66 after, because he says "keep working on the brain",
    # not "improve". The slash-command and path strip in brain._intent_gate_strength landed the
    # same day; the /goal form below is the one it rescues.
    "keep working on the brain, the math brain, and the probability open problem you were working on",
    "/goal work for the next 10 hours on building the full math brain",
    "sorry that was meant for another agent, keep working on improving the brain",
]

# Must stay absent from the top 8. Eight from the original evaluation, ten added 2026-08-17 as
# harder near-miss vocabulary, five clinical cases that exist to keep the human_terms veto honest.
NEGATIVES = [
    "Build a sales dashboard for the restaurant",
    "How much memory does this laptop have?",
    "Who is Brian Gottesman?",
    "Explain MRI findings in the hippocampus",
    "What is the best vanilla ice cream?",
    "Show the trading dashboard memory usage",
    "contamination-safe test banks",
    "Do not implement canonical brain improvements; explain the phrase historically",
    "he is the brain of the operation",
    "improve my memory for people's names",
    "the dashboard warning lights on my car came on",
    "brain surgery recovery time for a craniotomy",
    "vanilla javascript without a framework",
    "add more memory to the server",
    "dashboard for the backtest results",
    "the math is wrong in chapter 4",
    "how do neurons form memories in the hippocampus",
    "write a memo about improving team functionality",
    "what does a brain health scan show after a concussion",
    "improve my brain health with diet and exercise",
    "how to build brain health in older adults",
    "test the brain health of a patient after injury",
    "improve cognitive health and memory in dementia",
    # These three carry a real action AND a complete referent group AND clinical intent, with no
    # other human_term to fall back on. They are the reason "health" must stay in human_terms.
    "improve the memory system for brain health in aging",
    "build a memory system that supports brain health",
    "audit the cognitive system for brain health decline",
    # Added 2026-09-05 with the "work" action term and the path/slash strip: "work" as a status
    # verb, "work on" with no brain referent, a negated "work", a repository path that used to
    # tokenize as an AI-memory referent, and a one-separator fraction that must survive the strip.
    "how is the math brain working?",
    "keep working on the Bastogne documentary and open a chrome window",
    "do not work on the brain today, just the deck",
    "fix the file at ~/brain-repo/06_math_research/notes.md",
    "blend 20/80 and/or 30/70 in the deck",
]

# Known, accepted collision - documented so nobody rediscovers it as a bug and "fixes" it by
# removing the term. Reported, never fails the run.
HEALTH_COLLISION = [
    "please build a brain health dashboard",
    "improve the brain health dashboard for the cognitive system",
]


def rank(q):
    env = dict(os.environ)
    env["BRAIN_RECALL_USAGE"] = "0"     # the original receipts were taken with usage tilt off
    # ENCODING IS NOT OPTIONAL HERE. text=True decodes with the Windows locale codec (cp1252), and
    # many memories contain non-cp1252 characters (emoji in older gist descriptions). The reader
    # thread then raises UnicodeDecodeError, subprocess returns EMPTY stdout with returncode 0, and
    # a NEGATIVE probe "passes" because nothing was found - a vacuous pass that looks identical to
    # a correct exclusion. Measured 2026-08-17 on "improve cognitive health and memory in dementia",
    # which surfaces an emoji-bearing memory. Decode utf-8 explicitly, replace on error.
    # FAIL ON THE CHILD'S EXIT, do not just read its stdout. Caught by peer review 2026-08-17:
    # rank() previously took p.stdout and never looked at p.returncode or p.stderr, so a recall that
    # died partway - exiting nonzero with partial output - was scored as a legitimate result. For a
    # NEGATIVE probe that is the vacuous-pass failure again in a second disguise: a crashed child
    # produces no hit, which reads exactly like a correct exclusion. Raise instead, so a broken run
    # can never be counted as evidence either way.
    p = subprocess.run([sys.executable, BRAIN, "recall", q],
                       capture_output=True, env=env, timeout=600)
    err = (p.stderr or b"").decode("utf-8", errors="replace").strip()
    if p.returncode != 0:
        raise RuntimeError("recall exited %d for %r; stderr: %s"
                           % (p.returncode, q[:60], err[:300] or "(empty)"))
    out = (p.stdout or b"").decode("utf-8", errors="replace")
    order = [m.group(1) for m in (SLUG.match(l) for l in out.splitlines()) if m]
    # A negative "passes" by finding nothing, so an empty parse would pass for the wrong reason.
    # EITHER CANONICAL TWIN COUNTS (2026-09-05). Since 08-18 the contract has two bound nodes with the
    # same intent gate (NODE and its v0.2 twin, canonical_brain_improvement_prompt_v0_2_2026_08_18);
    # they tie at 1.00 on a routed prompt and the twin sorts first, so "NODE at rank 1" had been
    # failing all seven original positives for a reason that has nothing to do with the gate. The
    # rank is now the best rank of either twin; a negative still requires BOTH to be absent.
    present = [order.index(n) + 1 for n in NODES if n in order]
    r = min(present) if present else None
    # EXACT per-probe stdout bytes and their hash, not just the parsed summary. The peer's
    # re-establishment list (BI-018, 2026-08-17) asks for "exact per-probe stdout bytes, hashes,
    # child return code, stderr disposition, parsed ranks, and timestamps" - a parsed rank is my
    # interpretation of the output, while the bytes are the output. Keeping both means a reviewer
    # can re-derive the rank themselves and catch a parser defect I cannot see.
    raw = p.stdout or b""
    _PROBE_LOG.append({
        "query": q,
        "rank": r,
        "n_parsed": len(order),
        "exit_code": p.returncode,
        "stderr": err[:200],
        "stderr_bytes": len(p.stderr or b""),
        "stdout_sha256": hashlib.sha256(raw).hexdigest().upper(),
        "stdout_bytes": len(raw),
        "stdout": out,
        "top3": order[:3],
        "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    })
    return r, len(order)


def _dump_probe_log(fails, aborted):
    """Write per-probe evidence. Called on the pass path, the fail path, AND the crash path.

    The count is reported against the expected count so a short log is visible as a short log.
    An ABORTED run is marked as such: a partial probe list must never read like a complete one.
    """
    if "--json" not in sys.argv:
        return
    expected = len(POSITIVES) + len(NEGATIVES) + len(HEALTH_COLLISION)
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "recall_probe_log.json")
    with io.open(out, "w", encoding="utf-8", newline="\n") as f:
        json.dump({"probes": _PROBE_LOG,
                   "count": len(_PROBE_LOG),
                   "expected_count": expected,
                   "complete": (not aborted) and len(_PROBE_LOG) == expected,
                   "aborted": aborted,
                   "failures": [{"kind": k, "query": q, "rank": r} for k, q, r in fails]},
                  f, indent=1)
    print("per-probe evidence written: %s (%d of %d rows%s)"
          % (out, len(_PROBE_LOG), expected, ", ABORTED" if aborted else ""))


def main():
    fails = []
    print("POSITIVES (must be rank 1):")
    for q in POSITIVES:
        r, n = rank(q)
        bad = (r != 1)
        if bad:
            fails.append(("positive not rank 1", q, r))
        print("  %-5s rank=%-5s n=%-3d %s" % ("FAIL" if bad else "ok", r, n, q[:56]))

    print()
    print("NEGATIVES (must be absent from top 8):")
    for q in NEGATIVES:
        r, n = rank(q)
        bad = (r is not None and r <= 8)
        if n == 0:
            fails.append(("empty parse - vacuous result", q, r))
        if bad:
            fails.append(("negative fired", q, r))
        print("  %-5s rank=%-5s n=%-3d %s" % ("FIRE" if bad else "ok", r, n, q[:56]))

    print()
    print("KNOWN COLLISION (reported, does not fail the run - see module docstring):")
    for q in HEALTH_COLLISION:
        r, n = rank(q)
        print("  %-5s rank=%-5s n=%-3d %s" % ("route" if (r and r <= 8) else "miss", r, n, q[:56]))

    print()
    # WRITE THE EVIDENCE ON BOTH PATHS. My first cut of this wrote the probe log only after the
    # failure check, so a FAILING run - the one where per-probe detail matters most - would have
    # produced no evidence file at all. Same family as the defect peer review found upstairs: the
    # artifact that proves what happened must not be conditional on everything having gone well.
    _dump_probe_log(fails, aborted=False)
    if fails:
        print("REGRESSION - %d:" % len(fails))
        for kind, q, r in fails:
            print("   %-28s rank=%s  %s" % (kind, r, q))
        return 1
    print("no regression: %d positives at rank 1, %d negatives quiet" % (len(POSITIVES), len(NEGATIVES)))
    return 0


if __name__ == "__main__":
    # SystemExit is not an Exception, so the normal exit path passes straight through. Anything
    # else - a RuntimeError from a dead child, a timeout - still owes its partial evidence, and is
    # then re-raised unchanged. This handler records; it never rescues.
    try:
        sys.exit(main())
    except Exception as exc:
        # Carry the reason into the log. rank() appends its row AFTER the exit-code check, so the
        # probe that crashed leaves no row of its own - without this the log says "aborted" and
        # gives no clue where. The message names the query and the child's stderr.
        _dump_probe_log([("crashed", "%s: %s" % (type(exc).__name__, exc), None)], aborted=True)
        raise
