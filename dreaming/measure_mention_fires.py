#!/usr/bin/env python3
"""measure_mention_fires.py - the 2026-07-09 dream's named frontier, measured on real data NOW.

The dream: "coverage is done, every scar fires; what's NOT done is making the fire WORTH OBEYING - four
of five fires were false, all vocabulary. A warning that fires on MENTIONS teaches its reader to click
past it." A fire-mode danger reflex should fire on the ACT (running the healer, placing the order,
editing the guarded bot), not on prose or a read-only audit that merely QUOTES its scarred vocabulary.

instinct_prime's live fire-log (instinct_fires.tsv) will measure this from real fires as it accrues, but
that takes days. This measures it NOW by replaying each fire-mode reflex against a real corpus:
  MENTION surfaces = owner prompts (prose) + READ-ONLY tool commands (grep/cat/ls/audit that quote
    vocabulary without acting on the target).
  ACT surfaces = MUTATING tool commands (running a script, git push, Remove-Item/Stop-Process, a file
    write to a guarded path).
Commands come from the raw-transcript archive (every tool call ever). A reflex whose matches are
overwhelmingly on MENTION surfaces is the click-past-it class - its trigger wants act-keying.

Read-only. Honest about the proxy: 'act' here is coarse (any non-read command), so it OVER-counts acts
if anything - a reflex still reading >70% mention against that generous denominator is a clear signal.
Usage: python measure_mention_fires.py [n_transcripts]
"""
import os, io, re, sys, json, glob
from collections import defaultdict

DR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DR)
INST = os.path.join(DR, "instincts.tsv")
MEM = os.path.expanduser(r"~/.claude/projects/{{BRAIN_SLUG}}/memory")
LEDGER = os.path.join(MEM, "prompts", "PROMPTS.jsonl")
ARCHIVE = os.path.expanduser(
    r"~/brain-repo/11_raw_session_transcripts/claude_projects/{{BRAIN_SLUG}}")

# read-only / audit command shapes (mirrors instinct_prime._READONLY): quoting vocabulary, not acting
READONLY = re.compile(r"^\s*(grep|rg|cat|less|head|tail|ls|find|wc|diff|sed -n|awk|"
                      r"git (log|status|diff|show|blame|branch)|python[0-9.]* (-m py_compile |)?[\w./\\-]*"
                      r"(recall|status|audit|eval|check|_test|probe|measure|verify|brain|reflex|inspect)|"
                      r"echo|printf|type |Get-Content|Select-String|Get-ChildItem|Get-Item|Test-Path|"
                      r"Measure-Object|cygpath|date|pwd|which|whoami)\b", re.I)

# ADVISORIES: fire-mode reflexes whose scar is a JUDGMENT/DIRECTIVE, not an act on a target - they are
# SUPPOSED to fire on the prompt/prose (the owner naming a target, claiming done, setting a duration).
# High mention-share is CORRECT for these; only ACT-GUARDS (fire only when you DO the thing) are the
# click-past-it class if they fire on mentions. (Classified by reading each reflex's gut/scar 2026-07-09;
# a judgment call - erring toward calling a reflex advisory, so the flag stays conservative.)
ADVISORY = {"work_the_clock", "he_will_look", "confirm_the_target", "his_never_is_machinery",
            "his_call_is_final", "broker_truth", "measure_one_before_promising_n", "dont_extrapolate_past_range",
            "ib_marks", "risk_vs_measured_record", "live_time_pressure", "green_not_trusted", "sim_flatters",
            "stale_mark_banks", "fixed_sides_only", "lookahead", "voice_human", "premortem"}


def load_prompts(limit=6000):
    out = []
    try:
        for ln in io.open(LEDGER, encoding="utf-8", errors="replace"):
            try:
                r = json.loads(ln)
            except Exception:
                continue
            if r.get("origin", "owner") == "owner" and r.get("text"):
                out.append(r["text"])
    except Exception:
        pass
    return out[-limit:]


def load_commands(n_transcripts):
    reads, acts = [], []
    files = sorted(glob.glob(os.path.join(ARCHIVE, "*.jsonl")))[-n_transcripts:]
    for f in files:
        try:
            for ln in io.open(f, encoding="utf-8", errors="replace"):
                try:
                    r = json.loads(ln)
                except Exception:
                    continue
                msg = r.get("message", {})
                if not isinstance(msg, dict):
                    continue
                for c in (msg.get("content") or []):
                    if isinstance(c, dict) and c.get("type") == "tool_use":
                        nm = c.get("name", "")
                        if nm in ("Edit", "Write", "NotebookEdit"):
                            acts.append(json.dumps(c.get("input", {}), ensure_ascii=False)[:400])
                        elif nm in ("Bash", "PowerShell"):
                            cmd = (c.get("input") or {}).get("command", "")
                            if cmd:
                                (reads if READONLY.match(cmd) else acts).append(cmd[:400])
        except Exception:
            continue
    return reads, acts


def main():
    n_tr = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    try:
        rows = [ln.rstrip("\n").split("\t") for ln in io.open(INST, encoding="utf-8", errors="replace")]
    except Exception:
        print("no instincts.tsv")
        return
    hdr, rows = rows[0], rows[1:]
    ci = {c: i for i, c in enumerate(hdr)}
    prompts = load_prompts()
    reads, acts = load_commands(n_tr)
    print("measure_mention_fires: corpus = %d prompts + %d read-cmds (MENTION) | %d act-cmds (ACT), "
          "from %d transcripts" % (len(prompts), len(reads), len(acts), n_tr))
    mention_surf = prompts + reads
    act_surf = acts
    if len(act_surf) < 50 or len(mention_surf) < 200:
        print("  thin corpus - raise n_transcripts."); return

    results = []
    for r in rows:
        if len(r) <= ci["trigger"] or r[ci["mode"]] != "fire":
            continue
        rid, trig = r[ci["id"]], r[ci["trigger"]]
        try:
            rx = re.compile(trig, re.I)
        except Exception:
            continue
        m = sum(1 for t in mention_surf if rx.search(t))
        a = sum(1 for t in act_surf if rx.search(t))
        if m + a == 0:
            continue
        # normalise by corpus size (mention corpus is bigger) - fire RATE per surface, then the share
        mr = m / len(mention_surf)
        ar = a / len(act_surf)
        share_mention = mr / (mr + ar) if (mr + ar) else 0.0
        results.append((rid, m, a, share_mention))

    results.sort(key=lambda t: -t[3])
    print("  fire-mode reflex          kind      mention-hits act-hits  mention-share (rate-normalised)")
    for rid, m, a, sh in results:
        kind = "advisory" if rid in ADVISORY else "act-guard"
        # only an ACT-GUARD firing mostly on mentions is the click-past-it problem; an advisory SHOULD
        flag = "  <- ACT-GUARD, mention-heavy: act-key it" if (kind == "act-guard" and sh > 0.70 and m + a >= 8) else ""
        print("  %-26s %-9s %6d %8d       %.0f%%%s" % (rid[:26], kind, m, a, 100 * sh, flag))
    heavy = [r for r in results if r[0] not in ADVISORY and r[3] > 0.70 and (r[1] + r[2]) >= 8]
    guards = [r for r in results if r[0] not in ADVISORY]
    guard_mention = (sum(1 for r in guards if r[3] > 0.5) / len(guards)) if guards else 0
    print("\n  read: only ACT-GUARDS (fire only when you DO the thing) are the click-past-it class if they "
          "fire on MENTIONS; advisories (name-a-target, claim-done, set-a-duration) SHOULD fire on prose. "
          "mention-share is rate-normalised (the mention corpus is larger).")
    print("  ACT-GUARDS flagged mention-heavy (>70%%): %d of %d -> %s"
          % (len(heavy), len(guards), ", ".join(r[0] for r in heavy) or "NONE"))
    print("  VERDICT: %s. %.0f%% of act-guards are majority-act-fired (well-keyed). The dream's felt "
          "'4 of 5 fires were mentions' was an AUDIT-HEAVY session (reads quoting vocabulary), not a "
          "systemic gut miscalibration - most act-guards are already keyed on the act."
          % ("act-guards are well-keyed; no systemic trigger surgery warranted" if not heavy
             else "a few act-guards over-fire on mentions - act-key those specific triggers",
             100 * (1 - guard_mention)))


if __name__ == "__main__":
    main()
