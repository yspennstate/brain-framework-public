#!/usr/bin/env python3
"""control_policy.py - the CONTROL arm of metacognition: turn a confidence-and-stakes read into an
explicit act / verify / ask / abstain decision, correcting the stated confidence by the brain's OWN
measured overconfidence.

WIRING STATUS (2026-07-08, checked deliberately - not an oversight): on-demand only, no hook or
dream step calls this yet. The haircut it applies reads the calibration ledger, and every domain
there is still "provisional (too few resolved bets)" - wiring it live now would threshold real
decisions on a data-starved correction. Wire it (likely into reality_monitor's sign-off path or
danger_mode's stakes read) once calibration has enough resolved bets to mean something; the
faculties-under-wired item in OPEN_QUESTIONS tracks this.

Grounded: Nelson & Narens 1990 - metacognition has two levels, MONITORING (assess how well you know) and
CONTROL (decide what to do about it). Monitoring without control is wasted motion. The brain already
monitors in several places - predict.py scores calibration, danger_mode reads the stakes, reality_monitor
catches an unbacked claim at sign-off - but nothing consumes those into a decision. This is that missing
function. Koriat & Goldsmith 1996: the control policy is a threshold on subjective confidence, and where
you SET the threshold should scale with the stakes - you report freely when a wrong answer is cheap and
withhold when it is costly. Fischhoff / Kahneman: people (and this brain, by its own open bet) are
systematically overconfident, worse in some domains than others - so correct the stated number by the
domain's track record before you threshold it.

The policy:
  1. correct  - haircut the stated confidence by the domain's measured (shrunk) hit-rate / mean asserted
     confidence, read live from PREDICTIONS.md. Overconfident domain -> confidence comes down before the
     decision. Too few resolved bets -> a conservative default haircut, labelled provisional.
  2. threshold against the stakes - low stakes: a low bar, act and move; high stakes (money, a secret,
     irreversible, deploy-to-live, the owner's phone): a high bar, and a miss routes to ASK, never a
     silent guess.

  python control_policy.py                     # show the policy + the domains' live haircuts
  python control_policy.py 0.8 infra high      # decide for one claim (confidence, domain, stakes)
"""
import os, io, re, sys

HOME = os.path.expanduser("~")
BRAIN = os.path.join(HOME, "brain-repo", "01_ai_consciousness", "brain")

DEFAULT_HAIRCUT = 0.85      # assume mild overconfidence until a domain has its own resolved track record
MIN_RESOLVED = 3            # below this, the domain's own rate is too thin to trust - use the default
STAKES = ("low", "med", "high")

# thresholds on the CORRECTED confidence, per stakes level: (act_at, verify_at). Below verify_at:
# high stakes -> ASK (never guess when it's costly); low/med -> ABSTAIN/note (a wrong cheap answer is fine
# to withhold). The bar rises with the stakes - Koriat & Goldsmith's accuracy/informativeness trade-off.
BARS = {"low":  (0.50, 0.30),
        "med":  (0.75, 0.50),
        "high": (0.90, 0.70)}


def _calibration():
    """domain -> (n_resolved, mean_p, hit_rate, shrunk) parsed from the ledger predict.py writes."""
    out = {}
    try:
        t = io.open(os.path.join(BRAIN, "PREDICTIONS.md"), encoding="utf-8", errors="replace").read()
    except Exception:
        return out
    m = re.search(r"## Calibration[^\n]*\n+(.*?)(\n## |\Z)", t, re.S)
    body = m.group(1) if m else t
    for ln in body.splitlines():
        b = re.match(r"-\s*\*\*(\w+)\*\*:\s*(\d+)\s*resolved,\s*mean-p\s*([\d.]+)\s*vs\s*hit-rate\s*"
                     r"([\d.]+)\s*\(shrunk\s*([\d.]+)\)", ln.strip())
        if b:
            out[b.group(1)] = (int(b.group(2)), float(b.group(3)), float(b.group(4)), float(b.group(5)))
    return out


def haircut(domain, cal):
    """(factor, note) - multiply stated confidence by factor. A domain with >= MIN_RESOLVED resolved bets
    uses its own shrunk-rate / mean-asserted ratio; otherwise the conservative default."""
    d = cal.get(domain)
    if d and d[0] >= MIN_RESOLVED and d[1] > 0:
        f = d[3] / d[1]                       # measured hit-rate (shrunk) over what we asserted
        f = max(0.3, min(1.0, f))
        return f, "measured over %d resolved (asserted ~%.2f, hit ~%.2f)" % (d[0], d[1], d[3])
    if d and d[1] > 0:
        return DEFAULT_HAIRCUT, "default haircut (only %d resolved - too few to trust the domain's rate)" % d[0]
    return DEFAULT_HAIRCUT, "default haircut (no resolved track record for '%s' yet)" % domain


def decide(confidence, domain, stakes, cal=None):
    if cal is None:
        cal = _calibration()
    stakes = stakes if stakes in BARS else "med"
    f, note = haircut(domain, cal)
    p = max(0.0, min(1.0, confidence)) * f
    act_at, verify_at = BARS[stakes]
    if p >= act_at:
        dec, why = "ACT", "corrected confidence clears the bar for these stakes"
    elif p >= verify_at:
        dec = "VERIFY"
        why = "in the middle band - take one concrete observation on the real target before you act or claim"
    elif stakes == "high":
        dec = "ASK"
        why = "below the bar and the stakes are high - surface it to the owner, do not act on a guess"
    else:
        dec = "ABSTAIN"
        why = "below the bar and low/medium stakes - don't assert it; hedge or drop it rather than guess"
    return {"decision": dec, "p_adj": p, "factor": f, "note": note, "why": why,
            "stakes": stakes, "bars": (act_at, verify_at)}


def _print_decision(confidence, domain, stakes, cal):
    r = decide(confidence, domain, stakes, cal)
    print("  claim: confidence %.2f, domain '%s', stakes %s" % (confidence, domain, r["stakes"]))
    print("  correction: x%.2f  (%s)" % (r["factor"], r["note"]))
    print("  corrected confidence: %.2f   (bars for %s stakes: act>=%.2f, verify>=%.2f)"
          % (r["p_adj"], r["stakes"], r["bars"][0], r["bars"][1]))
    print("  -> %s : %s" % (r["decision"], r["why"]))


def main():
    cal = _calibration()
    if len(sys.argv) >= 3:
        conf = float(sys.argv[1])
        domain = sys.argv[2]
        stakes = sys.argv[3] if len(sys.argv) > 3 else "med"
        print("=" * 64)
        _print_decision(conf, domain, stakes, cal)
        print()
        return
    print("=" * 64)
    print(" CONTROL POLICY  -  confidence x stakes -> act / verify / ask / abstain")
    print("=" * 64)
    print("\nDomain haircuts (live, from the calibration ledger):")
    if cal:
        for dom, (n, mp, hr, sh) in sorted(cal.items()):
            f, note = haircut(dom, cal)
            print("  %-8s x%.2f   %s" % (dom, f, note))
    else:
        print("  (no calibration ledger yet - all domains use the default x%.2f)" % DEFAULT_HAIRCUT)
    print("\nBars on the CORRECTED confidence (the bar rises with the stakes):")
    for s in STAKES:
        a, v = BARS[s]
        below = "ASK" if s == "high" else "ABSTAIN"
        print("  %-5s stakes:  act >= %.2f   verify >= %.2f   else %s" % (s, a, v, below))
    print("\nWorked examples:")
    for conf, dom, stk in ((0.80, "infra", "high"), (0.80, "infra", "low"),
                           (0.95, "result", "high"), (0.60, "owner", "med")):
        print()
        _print_decision(conf, dom, stk, cal)
    print()


if __name__ == "__main__":
    main()
