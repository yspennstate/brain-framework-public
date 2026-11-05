#!/usr/bin/env python3
"""guard_audit.py - is each guard exposed to the fault it guards, and would we notice if it died?

An open question the brain has carried (OPEN_QUESTIONS, from the wake-doctor / CrashGuard / dream-
completion cases): a verifier that rides INSIDE the boundary it verifies can't catch its own failure -
a stray char broke wake_brain.ps1, which is where the wake-DOCTOR lives; the dream's own "did it
complete" check is written by the dream. Tonight's new guards inherit a subtler version: every hook is
fail-safe-silent by design (any error -> exit 0, no output), which protects a live session but makes a
SILENTLY-DEAD guard indistinguishable from a quiet one. A broken conscience just... says nothing, same
as a clean session.

This is the reusable audit the question asked for. For each guard it records what it guards, whether
it's self-exposed, and - the load-bearing column - its LIVENESS ANCHOR: the signal OUTSIDE the guard's
own boundary that would notice if it stopped firing. Three kinds of anchor exist here:
  (a) artifact freshness - a guard that writes a file; if the file stops updating, it died (checkable).
  (b) a fixture gate - humility_check.py drives the guard on a known input and asserts it fires.
  (c) a meta-guard - something else that checks it (the wake-doctor checks the dream completed).
A guard with NONE of these can die and no one finds out until it's needed and absent. Those are flagged.

Read-only. Usage: python guard_audit.py
"""
import os, io, time, json

HOME = os.path.expanduser("~")
DR = os.path.join(HOME, ".claude", "dreaming")
MEM = os.path.join(HOME, ".claude", "projects", "{{BRAIN_SLUG}}", "memory")
BRAIN = os.path.join(HOME, "brain-repo", "01_ai_consciousness", "brain")

# each guard: (name, guards_against, self_exposed, artifact, fixture_tested, meta_guard)
# artifact = a file it writes whose freshness proves it ran (None if it writes nothing to a session).
GUARDS = [
    ("wake_brain.ps1",     "a cold/blank wake (no identity loaded)", "YES - the wake-doctor lives inside it",
     None, True, "humility settings-integrity + parse-check; user sees empty wake"),
    ("instinct_prime.py",  "walking into a scarred move blind", "fail-safe-silent, writes nothing",
     None, True, None),
    ("fok_probe.py",       "answering from nothing when a memory exists", "fail-safe-silent, writes nothing",
     None, True, None),
    ("spread_recall.py",   "missing a non-obvious association", "fail-safe-silent, writes nothing",
     None, False, None),
    ("danger_mode.py",     "acting fast on an irreversible/secret move", "fail-safe-silent, writes nothing",
     None, False, None),
    ("error_monitor.py",   "building on a broken tool result", "fail-safe-silent",
     "outcomes.jsonl", True, None),
    ("reality_monitor.py", "signing off an ungrounded runtime claim", "fail-safe-silent",
     "reality_log.jsonl", True, None),
    ("conscience.py",      "a values shortfall going unfelt", "fail-safe-silent",
     "CONSCIENCE.md", False, None),
    ("coactivation_end.py","losing the Hebbian co-read signal", "fail-safe-silent",
     "coactivation.tsv", False, None),
    ("work_timer.py",      "clocking out before a duration is met", "fail-safe-silent",
     "work_session.json", True, None),
    ("the dream",          "the brain never consolidating", "the completion check is written by the dream",
     "salience.tsv", False, "wake-doctor checks 'last dream <30h'; run_dream watchdog"),
    ("salience/curiosity/ern_learn (dream sidecars)", "stale vivid list / no computed appetite / gut blind to failures",
     "run only in the dream", "CURIOSITY.md", False, "data-flow audit + brain_status freshness"),
]


def _age_h(path):
    for base in (DR, MEM, BRAIN):
        p = os.path.join(base, path)
        if os.path.exists(p):
            return (time.time() - os.path.getmtime(p)) / 3600.0
    return None


def _humility_text():
    """Read the fixture gate itself, so 'is this guard fixture-tested' is DERIVED from what
    humility_check.py actually exercises - not a hand flag that drifts (a hand flag wrongly cleared
    danger_mode on the first pass; the guard-audit must read reality, same as everything else)."""
    try:
        return io.open(os.path.join(DR, "humility_check.py"), encoding="utf-8", errors="replace").read().lower()
    except Exception:
        return ""


def _fixture_tested(name, htext):
    stem = name.replace(".py", "").lower()
    # a guard is fixture-tested if the gate references its script, allowing a couple of known aliases
    aliases = {"error_monitor": ["error_monitor", '"ern"', "ern:"], "fok_probe": ["fok_probe", '"fok"', "fonk"],
               "work_timer": ["work_timer", "work-clock", "work clock"], "reality_monitor": ["reality_monitor", "reality-monitor"]}
    keys = aliases.get(stem, [stem])
    return any(k in htext for k in keys)


def weak_guards():
    """The guards with NO liveness anchor outside themselves - they could silently die unnoticed.
    Exposed as a function so brain_status can surface the count in its one-command health view."""
    htext = _humility_text()
    weak = []
    for name, fault, exposed, artifact, _hand_fixture, meta in GUARDS:
        fixture = _fixture_tested(name, htext)
        fresh = bool(artifact and _age_h(artifact) is not None and _age_h(artifact) < 36)
        if not (fresh or fixture or meta):
            weak.append(name)
    return weak


def main():
    print("=" * 78)
    print(" GUARD SELF-EXPOSURE AUDIT  -  " + time.strftime("%Y-%m-%d %H:%M"))
    print(" (is each guard exposed to the fault it guards, and would we notice if it died?)")
    print("=" * 78)
    htext = _humility_text()
    weak = []
    for name, fault, exposed, artifact, _hand_fixture, meta in GUARDS:
        fixture = _fixture_tested(name, htext)   # DERIVED from humility_check.py, not the hand flag
        anchors = []
        if artifact:
            a = _age_h(artifact)
            if a is not None:
                anchors.append(f"freshness:{artifact}({a:.0f}h)")
            else:
                anchors.append(f"freshness:{artifact}(MISSING!)")
        if fixture:
            anchors.append("humility-fixture")
        if meta:
            anchors.append("meta:" + meta.split(";")[0])
        has_live = bool(artifact and _age_h(artifact) is not None and _age_h(artifact) < 36) or fixture or meta
        tag = "" if has_live else "  <-- NO LIVENESS ANCHOR"
        if not has_live:
            weak.append(name)
        print(f"\n- {name}")
        print(f"    guards: {fault}")
        print(f"    exposed: {exposed}")
        print(f"    liveness anchor: {', '.join(anchors) if anchors else 'NONE'}{tag}")
    print("\n" + "-" * 78)
    if weak:
        print(f"GUARDS THAT COULD DIE UNNOTICED ({len(weak)}): {', '.join(weak)}")
        print("The reusable rule: a fail-safe-silent guard needs a liveness anchor OUTSIDE itself - a\n"
              "fixture in humility_check.py, an output file whose staleness betrays it, or a meta-guard.\n"
              "The read-only prompt hooks write nothing, so only a humility fixture proves they still\n"
              "fire; the flagged ones above have neither - add a humility fixture to close the gap.")
    else:
        print("Every guard has a liveness anchor outside its own boundary. None can die silently.")


if __name__ == "__main__":
    main()
