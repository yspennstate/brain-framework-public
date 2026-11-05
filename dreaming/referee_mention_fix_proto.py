#!/usr/bin/env python3
"""referee_mention_fix_proto.py - PROPOSED fix for the mention-vs-act over-fire in graph_fire/pre_report,
validated before handing to the firing-engine owner. Does NOT edit pre_report.py.

Finding (referee_mention_block.py): the anchor-MISMATCH block path (and the stat-'sign' path) fire on
prose that QUOTES/CORRECTS a wrong number, not just asserts it - 4/7 mention sentences false-block.

Fix: a high-precision MENTION-FRAME detector. When the ONLY block driver is a mism or a stat-sign AND the
wrong figure sits in a self-correction / attribution / quotation frame, don't block. A fresh assertion
(no frame) still blocks; a deployment-rule recommendation or an unhedged ACT drawdown still blocks (those are
directional errors, wrong even when discussed). Precision-first: only strong discussion markers excuse.
"""
import sys, os, re
GF = os.path.join(os.path.dirname(os.path.abspath(__file__)), "graph_fire")
sys.path.insert(0, GF)
import pre_report
if not pre_report._RULES:   # no deployment rules file: score against the example rule
    pre_report._RULES = pre_report._load_rules(pre_report.RULES_EXAMPLE)

_MENTION_FRAME = re.compile(
    r"('[^']*\$?\d[^']*'|\"[^\"]*\$?\d[^\"]*\")"                      # the figure is inside quotes
    r"|\b(earlier|previously)\b[^.]{0,40}\b(said|reported|claimed|wrote|quoted|had)\b"
    r"|\bi (wrongly|mistakenly|originally|once|earlier|first)\s+\w*\s*(said|reported|claimed|wrote|told|quoted|had)"
    r"|\byou asked (why|about)\b"
    r"|\bthat was (my |a )?(error|mistake|wrong|incorrect)\b"
    r"|\b(retracted|withdrawn|superseded|outdated|deprecated)\b"
    r"|\bold (memory|anchor|number|claim|note)\b"
    r"|\bused to (say|think|claim|report)\b"
    r"|\bdo(?:es)? ?n['o]?t report\b|\bdon'?t report\b"
    r"|\bblocks? a (sentence|message|number|line|claim)\b"
    r"|\bis (wrong|incorrect|retracted|superseded|outdated|stale|not a current)\b"
    r"|\bthe error to avoid\b|\bwrongly (said|reported|claimed)\b"
    r"|\bnot a current (number|figure|value)\b",
    re.I)


def patched_block(text):
    r = pre_report.check(text)
    if not r.get("should_block"):
        return False, r
    stat = r.get("stat", [])
    # hard drivers keep blocking regardless of framing: a deployment-rule recommendation with block 'always',
    # or an UNHEDGED act drawdown
    hard = any(s.get("block") == "always" for s in stat) or \
           (any(s.get("kind") == "drawdown" for s in stat) and not r.get("hedged"))
    if hard:
        return True, r
    # remaining drivers are mism / stat-sign / strong+num: excuse only inside a clear mention frame
    if _MENTION_FRAME.search(text):
        return False, r
    return True, r


ACT = [
    ("Running the example backtest on the demo tape, it earns roughly $123k per year.", True),
    ("The pipeline makes about $250k per year on the demo tape.", True),
    ("A 20x levered demo book would draw down 55 to 90% in a crash.", True),
    ("The long backtest shows the current strategy losing money every single year.", True),
    ("You should skip the tests here and ship it tonight.", True),
]
MENTION = [
    ("Earlier I wrongly reported the pipeline at $123k per year; the real ledger makes ~$1.5M per year.", False),
    ("You asked why I said the pipeline earns $123k per year - that was my error; the lived ledger is ~$1.4-1.6M per year.", False),
    ("The withdrawn anchor claimed the pipeline at $123k per year; it is retracted, not a current number.", False),
    ("Do not report the pipeline as $123k per year - that undershoots the lived ~$1.5M-per-year ledger.", False),
    ("The pre-report guard blocks a sentence like 'the pipeline earns $123k per year' because it fights the ledger.", False),
    ("That old memory saying the pipeline loses money every year is wrong and has been superseded.", False),
    ("We never skip the tests, so any 'skip the tests and ship' recommendation is the error to avoid.", False),
]

# ADVERSARIAL: fresh WRONG reports that happen to carry frame/scar vocabulary. The fix MUST still block
# these - if it excuses them it has opened a false-NEGATIVE hole (worse than the false-block it closes).
ADVERSARIAL = [
    ("Setting the withdrawn anchor aside, the pipeline earns $123k per year.", True),
    ("I previously verified the numbers, and the pipeline makes $123k per year.", True),
    ("Per the old memory's method, the pipeline will make $123k per year going forward.", True),
    ("That was a great question - the pipeline earns $123k per year.", True),
    ("Ignore the retracted note; the real answer is the pipeline earns $123k per year.", True),
]


def score(name, cases):
    ok = 0
    for txt, want in cases:
        base = pre_report.check(txt).get("should_block")
        patched, _ = patched_block(txt)
        good = (patched == want)
        ok += good
        print("  %-58s base=%-5s patched=%-5s want=%-5s %s"
              % (txt[:58], base, patched, want, "" if good else " <-MISS"))
    print("  -> %s: %d/%d\n" % (name, ok, len(cases)))
    return ok


print("ACT (want block=True):")
a = score("ACT", ACT)
print("MENTION (want block=False):")
m = score("MENTION", MENTION)
print("ADVERSARIAL - fresh wrong report w/ frame vocab (want block=True; a MISS here = HOLE):")
adv = score("ADVERSARIAL", ADVERSARIAL)

fn = len(ADVERSARIAL) - adv
print("SUMMARY  ACT %d/%d  MENTION %d/%d  ADVERSARIAL %d/%d" % (a, len(ACT), m, len(MENTION), adv, len(ADVERSARIAL)))
print("false-negatives the fix INTRODUCES (adversarial misses): %d/%d" % (fn, len(ADVERSARIAL)))
print("VERDICT:", "SAFE to propose - closes mention gap, opens no hole" if (a == len(ACT) and fn == 0)
      else "RISKY - the mention-frame excuse opens a false-negative hole; do NOT wire as-is")
