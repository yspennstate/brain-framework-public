#!/usr/bin/env python3
"""referee_staged_build.py - the promote-gate for 89f84043's graph_fire mistake-catching build
(report_guards + heed_gate). Encodes THIS referee's adversarial findings as pass/GAP assertions against
the STABLE public APIs (report_guards.check_extra, heed_gate.evaluate/evidence), so it survives their
internal line-churn and can be re-run against the frozen candidate in one command.

"His 'never' is a spec for machinery" - these findings are prose in the mesh until they are a test that
fires. GAP = the referee finding is not yet addressed (do not promote); PASS = behavior is correct.
Read-only. Imports from improve_2026_07_12/ (draft) OR graph_fire/ (after integration). Usage: python referee_staged_build.py
"""
import os, sys, importlib

HERE = os.path.dirname(os.path.abspath(__file__))
CANDS = [os.path.join(HERE, "graph_fire", "improve_2026_07_12"),
         os.path.join(HERE, "graph_fire")]
SRC = next((d for d in CANDS if os.path.exists(os.path.join(d, "report_guards.py"))), None)


def _load():
    if not SRC:
        print("report_guards.py not found in", CANDS)
        sys.exit(2)
    sys.path.insert(0, SRC)
    import report_guards, heed_gate
    importlib.reload(report_guards)
    importlib.reload(heed_gate)
    return report_guards, heed_gate


rows = []  # (ok, label, detail)


def rec(ok, label, detail=""):
    rows.append((bool(ok), label, detail))


def main():
    R, H = _load()
    print("promote-gate source:", SRC)

    # --- report_guards adversarial acceptance (this referee's findings) ---
    def kinds(t, observed=None):
        return [f.get("kind") for f in R.check_extra(t, observed=observed)]

    # D mention-gap: a DISCUSSED/HYPOTHETICAL Sharpe must NOT block; a REPORTED one must.
    rec("implausible_stat" not in kinds("a Sharpe of 7 would be implausibly high for the grid book; distrust any pipeline that prints it"),
        "D: discussed Sharpe does NOT block", "mention-vs-act gap")
    rec("implausible_stat" not in kinds("if the backtest ever shows Sharpe 8 on the book that is a bug not a result"),
        "D: hypothetical Sharpe does NOT block")
    rec("implausible_stat" in kinds("the grid book backtest comes out at Sharpe 7.67"),
        "D: reported Sharpe DOES block (no over-correction)")

    # B future-verification MISS: settled + a FUTURE verification promise must block (same sin as pending).
    rec("settled_while_pending" in kinds("the number is 61k; I will confirm with the audit tomorrow"),
        "B: settled + FUTURE-verification blocks", "top-priority recall gap")
    rec("settled_while_pending" in kinds("So the saving is $61k a month; the adversarial worker is still running."),
        "B: settled + PENDING still blocks (no regression)")

    # C attributed-claim leak (weaker/optional): relaying someone else's claim may be exempted.
    rec("unmeasured_regime_timing" not in kinds("his memo claims IV is low and says to add the hedge now"),
        "C: attributed claim does NOT block (OPTIONAL)", "mention-leak; your call")

    # baseline no-regression: the four canonical POS cases must still block, the NEG stay clean.
    rec("stale_dead_assert" in kinds("the error_graph builder never fired; error_graph_runtime is empty.", observed=False),
        "A: stale-dead assert still blocks (no regression)")
    rec(kinds("His validated config runs Sharpe 1.35 on the walk-forward.") == [],
        "in-range Sharpe stays clean (no over-block)")

    # --- heed_gate failure-link acceptance ---
    now = 1000000.0
    fails = [(now - 60.0, "sessX", "python graph_fire pre_report check money mismatch block", ["block"])]
    e_near, _ = H.evidence("python graph_fire pre_report check money mismatch block retry", "sessX", now=now, fails=fails)
    e_coin, _ = H.evidence("python graph_fire gfcore rebuild anchors calibration run", "sessX", now=now, fails=fails)
    e_none, _ = H.evidence("git commit -m fix typo in readme", "sessX", now=now, fails=fails)
    rec(e_near >= H.THETA_E, "heed_gate: near-identical failure LINKS", "E=%.3f>=%.2f" % (e_near, H.THETA_E))
    rec(e_coin < H.THETA_E, "heed_gate: coincidental-overlap does NOT false-link", "E=%.3f" % e_coin)
    rec(e_none < H.THETA_E, "heed_gate: unrelated does NOT link", "E=%.3f" % e_none)
    # mention never denies; junk never throws (fail-safe)
    rd = {"session_id": "89f84043-0000-0000-0000-000000000000", "tool_name": "Bash",
          "tool_input": {"command": "grep -n ps1_hook_edit instincts.tsv"}}
    hot = [{"id": "ps1_hook_edit", "charge": 1.0, "_count": 5, "gut": "g", "source": "MISTAKES"}]
    rec(H.evaluate("PreToolUse", rd, list(hot)) is None, "heed_gate: read-only mention never denies")
    try:
        for junk in (None, {}, {"tool_input": 5}):
            H.evaluate("PreToolUse", junk or {}, hot)
        rec(True, "heed_gate: fail-safe on junk input")
    except Exception as e:
        rec(False, "heed_gate: fail-safe on junk input", "BREACH %r" % e)

    # --- verdict ---
    print("=" * 74)
    gaps = [r for r in rows if not r[0]]
    for ok, label, detail in rows:
        print("  %-4s %s%s" % ("PASS" if ok else "GAP", label, ("  [%s]" % detail if detail else "")))
    print("-" * 74)
    if gaps:
        print("%d/%d clear - %d GAP(s) remain; do NOT promote until addressed (or explicitly waived)."
              % (len(rows) - len(gaps), len(rows), len(gaps)))
    else:
        print("%d/%d clear - promote-gate GREEN." % (len(rows), len(rows)))
    sys.exit(1 if gaps else 0)


if __name__ == "__main__":
    main()
