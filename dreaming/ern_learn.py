#!/usr/bin/env python3
"""ern_learn.py - close the ERN -> gut loop: learn a reflex from the failures the brain actually hit.

The error-monitor (ERN) already FEELS a failure the moment it lands and logs it to outcomes.jsonl.
But nothing learns from that log - the loop was left open (error_monitor's own comment names the debt:
"so the dream can close the loop: check whether a reflex should have warned"). A brain that catches
a failure, feels it, and then forgets it isn't learning; it's just flinching.

This reads the real failures and asks, of each: did a reflex WARN before this? It matches every logged
failing command against the compiled instinct triggers (instincts.tsv). The result is two signals the
dream can act on:

  - COVERAGE: what fraction of real failures had a reflex fire for them. This is the honest score of
    how much the gut actually guards - grounded in events, not in how many scar_ files exist.
  - UNCOVERED SHAPES: failures that recurred with NO reflex watching. A repeated failure the gut is
    blind to is the strongest possible candidate for a new instinct - the scar already happened, more
    than once, and cost real time. These are surfaced for the dream to distil into a reflex.

It also emits a coarse calibration note (the recent failure RATE), since a brain hitting more errors
than its predictions assume is overconfident about its own reliability - the exact bet PREDICTIONS.md
already carries. Read-only except its report file. Usage: python ern_learn.py
"""
import os, re, io, json, sys, time
from collections import defaultdict, Counter

DR = os.path.dirname(os.path.abspath(__file__))
OUTCOMES = os.path.join(DR, "outcomes.jsonl")
TSV = os.path.join(DR, "instincts.tsv")
REPORT = os.path.join(DR, "ERN_LEARNING.md")
WINDOW_DAYS = 21   # learn from the recent past; ancient failures are a different regime

# The ERN sniffs a tool's RESPONSE for failure signatures - so it also fires on benign output that
# merely MENTIONS the shape: an echo about a traceback, a grep FOR "leaked secret", a cat of a file
# with the word "corrupt". Counting those as failures would teach the gut to guard non-problems.
# A failure is likely a FALSE ALARM when the command that "failed" only displays/searches text...
DISPLAY_CMD = re.compile(r"^\s*(echo|printf|cat|type|head|tail|ls|dir|grep|rg|egrep|findstr|"
                         r"select-string|sed\s+-n|awk|Get-Content|Write-Output|Write-Host|less|more)\b", re.I)
# ...or when the command's OWN text names the signature it then "matched" (self-referential: a command
# that greps for or echoes the trigger word is not that failure happening).
SELF_REF = re.compile(r"(leaked secret|traceback|corrupt|secret|private key|syntaxerror|mojibake|sharpe|drawdown)", re.I)


def is_false_alarm(cmd, sigs):
    """True when this logged 'failure' is most likely the ERN matching benign/self-referential output."""
    c = (cmd or "").strip()
    if not c:
        return True                       # no command captured -> matched some incidental output; can't learn a reflex from it
    if DISPLAY_CMD.match(c):
        return True                       # a display/search command didn't "fail" - it showed text that matched
    if SELF_REF.search(c):
        return True                       # the command is ABOUT the signature (a test / a grep for it), not the event
    return False


def load_triggers():
    trigs = []
    try:
        with io.open(TSV, encoding="utf-8", errors="replace") as f:
            next(f, None)
            for ln in f:
                p = ln.rstrip("\n").split("\t")
                if len(p) >= 7 and p[1] in ("fire", "guide"):
                    try:
                        trigs.append((p[0], re.compile(p[3], re.I)))
                    except Exception:
                        pass
    except Exception:
        pass
    return trigs


def load_failures():
    fails = []
    if not os.path.exists(OUTCOMES):
        return fails
    cutoff = time.time() - WINDOW_DAYS * 86400
    try:
        for ln in io.open(OUTCOMES, encoding="utf-8", errors="replace"):
            try:
                e = json.loads(ln)
            except Exception:
                continue
            if float(e.get("ts", 0)) < cutoff:
                continue
            fails.append(e)
    except Exception:
        pass
    return fails


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # Discovery is not execution: this script writes, so a help request must
        # exit before any work. See script_help_flag_can_execute_work.
        print(__doc__ or os.path.basename(__file__))
        return
    trigs = load_triggers()
    fails = load_failures()
    if not fails:
        print("ern_learn: no recent failures logged - nothing to learn from (a quiet, clean stretch).")
        return

    covered = 0
    uncovered = []           # (sig-tuple, cmd) real failures with no reflex
    uncov_sig = Counter()    # ...which recur WITHOUT a reflex
    false_alarms = Counter() # ERN fired on benign/self-referential output
    real = 0
    for e in fails:
        cmd = (e.get("cmd") or "")
        sigs = tuple(e.get("sig") or [])
        if is_false_alarm(cmd, sigs):
            false_alarms[sigs] += 1
            continue
        real += 1
        warned = any(rx.search(cmd) for _id, rx in trigs) if cmd else False
        if warned:
            covered += 1
        else:
            uncovered.append((sigs, cmd))
            uncov_sig[sigs] += 1

    n = len(fails)
    fa = sum(false_alarms.values())
    cov_rate = covered / real if real else 1.0
    # candidate reflexes: a REAL failure shape that recurred (>=2) with no reflex watching
    candidates = [(cnt, sigs) for sigs, cnt in uncov_sig.items() if cnt >= 2]
    candidates.sort(reverse=True)

    with io.open(REPORT, "w", encoding="utf-8") as f:
        f.write("# What the failures are teaching (ERN -> gut)\n\n")
        f.write(f"Over the last {WINDOW_DAYS} days the error-monitor logged **{n}** signatures. Of those, "
                f"**{fa}** look like false alarms (the ERN matching benign or self-referential output - "
                f"an echo about a traceback, a grep FOR 'leaked secret', a cat of a file with the word "
                f"'corrupt'), leaving **{real}** likely-real failures. A reflex warned before **{covered}** "
                f"of the real ones (**{cov_rate:.0%} coverage**). Regenerated by ern_learn.py.\n\n")
        if candidates:
            f.write("## Uncovered REAL failure shapes that recurred (candidate reflexes - the scar "
                    "already happened, more than once, with nothing watching)\n\n")
            for cnt, sigs in candidates:
                label = "; ".join(sigs) if sigs else "(unlabelled)"
                f.write(f"- **x{cnt}** {label}\n")
                for s2, cmd in uncovered:
                    if s2 == sigs and cmd:
                        f.write(f"    e.g. `{cmd[:120]}`\n")
                        break
        else:
            f.write("_No uncovered real failure recurred - the gut is guarding what actually goes wrong._\n")
        if fa >= max(5, n // 3):
            f.write(f"\n## The alarm itself needs a look (truth over comfort)\n\n"
                    f"{fa} of {n} logged signatures ({fa/n:.0%}) are likely FALSE ALARMS - the ERN "
                    f"firing on output that merely mentions a failure shape, not a failure. The fix here "
                    f"is not a new reflex; it's tightening error_monitor.py to check that the signature "
                    f"describes the command's own action (skip pure display/search commands and outputs "
                    f"that quote the trigger). A noisy alarm gets tuned out - the boy who cried wolf.\n")
            for sigs, c in false_alarms.most_common(5):
                f.write(f"- x{c} false: {'; '.join(sigs) if sigs else '(unlabelled)'}\n")
        # calibration note (on the REAL rate, not the inflated one)
        f.write(f"\n## Calibration\n\nReal failure rate: {real} in {WINDOW_DAYS}d "
                f"({real / WINDOW_DAYS:.1f}/day). If this runs hotter than the brain assumes, it's "
                f"overconfident about its own reliability (brain/PREDICTIONS.md, the infra bet).\n")

    top = "; ".join("/".join(s) for _c, s in candidates[:2]) if candidates else "none recurring"
    print(f"ern_learn: {n} signatures ({fa} false alarms, {real} real); {cov_rate:.0%} reflex coverage "
          f"of real; {len(candidates)} uncovered recurring [{top}] -> {os.path.basename(REPORT)}")


if __name__ == "__main__":
    main()
