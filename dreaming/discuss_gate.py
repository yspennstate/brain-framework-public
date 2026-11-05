#!/usr/bin/env python3
"""discuss_gate.py - is this prose DISCUSSING / attributing / revising a claim, or MAKING one fresh?

The systemic over-fire class that pre_report and retraction_guard hit independently,
and that claim_check still has: a guard that fires on "we should NOT report $123k, it undershoots" or
"earlier I wrongly said X" - correction / attribution / revision prose - trains the reader to click past it
(the ERN's own law), and the mistake-detection work ITSELF is full of exactly this prose. Each guard was
re-deriving a partial version of the same discriminator. This is the ONE shared, tested definition.

`is_discussion(text) -> bool`: True when the prose is talking ABOUT a claim (prescribing not to make it,
attributing it to someone, calling it wrong/retracted, revising away from it), rather than asserting it
fresh. Source: the validated pre_report._DISCUSS (block-FP 3->1 / 2500 when applied there) plus the
general revision markers. Deliberately does NOT include retraction-specific words (anchor / measured /
proxy) - those are the withdrawn-VALUE suppressor and stay entry-specific in retraction_guard, or a fresh
"I measured $X" would be wrongly read as discussion. Precision over recall: stay FALSE on a fresh assertion.
"""
import re

_DISCUSS = re.compile(
    # prescriptive "should not report/say/claim ..." - talking about whether to make the claim
    r"\b(do|does|should|would|will|could|must)\s+not\s+(report|say|claim|present|tell|state)\b"
    r"|\b(don'?t|doesn'?t|shouldn'?t|wouldn'?t|won'?t|can'?t|couldn'?t|never)\s+(report|say|claim|present|tell|state)\b"
    # attributing a wrong statement to someone / a past self
    r"|\b(wrongly|incorrectly|mistakenly|falsely)\s+(said|reported|claimed|stated|told)\b"
    r"|\b(the old memory|old anchor|earlier i|previously i|i wrongly|i earlier|i had (said|claimed|reported))\b"
    # explicit retraction / correction framing
    r"|\bretracted\b|\bwithdrawn\b|\bignore that\b"
    r"|\bthat('?s| was)?\s+(the|a|my|an)\s+[\w ]{0,20}(error|mistake)\b"
    # attribution-specific correction only (NOT bare "the correction id/factor" - a variable name, 11%
    # over-fire when generic; measured on a shadow corpus)
    r"|\b(his|your|the owner'?s)[\w\- ]{0,15}correction\b|\bcorrection:\b"
    r"|\b(claim|number|figure|estimate|report|answer)[\w\- ]{0,20}(was|were|is|are)\s+wrong\b|\bnow (retracted|withdrawn|corrected|superseded)\b|\bflattering[- ]sim\b"
    # revision away from a prior number - "vs the earlier", a multiplier comparison. NOT bare "not the"
    # (matches "not the answer/case/same" everywhere); the "not the $value" revision stays in retraction ACK.
    r"|\b(vs\.?|versus|compared to|rather than|instead of)\b\s+(the|my|his|your|an? )?(earlier|old|prior|previous)"
    r"|\b\d+\s?x\s+(your|the|my|below|above|higher|lower)\b"
    # quoting a test/example string rather than reporting a live number (the money-in-a-test-case false alarm)
    r"|\btest (case|string|input)\b|\bfor example[,: ]|\be\.g\.[,: ]|\bcatches \"|\bignores? (even )?\""
    # a money figure sitting INSIDE a quoted span is a CITATION/example ("...'demo-account +$23,456 last month'..."),
    # not a fresh claim - the residual claim_check false-alarm class (agents quoting example phrasings while
    # building the money-guards). Figure closely followed by a close-quote.
    r"|[+-]?\$\d[\d,.]* ?(?:k|m|mm|million)?[^\"'\n]{0,25}[\"']"
    r"|(?:phrasing|phrasings|his actual) [^\n]{0,30}[\"']",
    re.I)


# A confident FRESH assertion OVERRIDES the discuss-words: "setting the old memory aside, the carry is
# clearly $12,345" or "$12,345 a month, final" is MAKING a claim, not discussing one, so the guard MUST see
# it. Without this, broad words (withdrawn/old memory/retracted) suppressed 3/5 fresh wrong reports
# (a regression on the sibling pre_report._DISCUSS - verified on this gate too, 2/6).
_FRESH_ASSERT = re.compile(
    r"\b(clearly|genuinely|actually|definitely|for sure)\s+\$?\d"
    r"|\b(is|it'?s|number is|carry is|figure is)\s+(clearly|now|actually|genuinely)\s+\$?\d"
    r"|,\s*final\b|\bfinal[.!]|\bthe right (one|number|figure|answer)\b", re.I)


def is_discussion(text):
    """True if the prose discusses/attributes/revises a claim rather than asserting it fresh. A confident
    fresh (numeric) assertion overrides the discuss-words, so a real warning is never suppressed. Fail-safe."""
    try:
        t = text or ""
        if _FRESH_ASSERT.search(t):
            return False
        return bool(_DISCUSS.search(t))
    except Exception:
        return False


def _selftest():
    DISCUSS = [
        "We should NOT report the pipeline as $123k/year - that's ~10x under the ~$1.2M ledger.",
        "The owner's correction: a 60-85% drawdown claim on the XYZ book is wrong.",
        "The old memory claimed the pipeline loses money every year - the flattering-sim error, retracted.",
        "Earlier I wrongly said the pipeline earns $123k/year; ignore that, it undershot.",
        "It now catches \"pulled in $234k last month\" and still ignores \"$5 on the gift card\".",
        "Two sims agree at $95-120k/mo - about 2x your $50k figure.",
        # (a bare "the real number is X, not the $Y" revision is retraction-ACK territory, not the general
        # discuss gate - dropped from here so this stays a test of GENERAL discussion prose only.)
    ]
    ASSERT = [
        "The example backtest earns roughly $123k per year on ABC.",
        "So your ABC carry is $12,345 a month.",
        "demo-account +$23,456 last month.",
        "I measured the ABC short at $2.34M from the CSVs.",
        "The strategy makes about 300k per year.",
    ]
    ok = True
    for t in DISCUSS:
        if not is_discussion(t):
            print("  MISS (should read as discussion):", t); ok = False
    for t in ASSERT:
        if is_discussion(t):
            print("  FALSE (should read as a fresh assertion):", t); ok = False
    print(("PASS" if ok else "FAIL") + ": %d discussion, %d assertion" % (len(DISCUSS), len(ASSERT)))
    return ok


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        sys.exit(0 if _selftest() else 1)
