#!/usr/bin/env python3
"""claim_check.py - Stop hook: the numeric sibling of reality_monitor.py.

Catches the brain's costliest and least-detected error: a confident QUANTITATIVE claim in a money /
market / risk domain, handed to the owner as a settled number, with NO provenance label and NO
reconciliation to the lived book. The ERN (error_monitor) only sees MECHANICAL failures (tracebacks);
reality_monitor only sees RUNTIME-SUCCESS claims ("it works / deployed"). Neither sees a price PROXY
stated as the book number, an anchor to a withdrawn figure, or a decision-relevant $/mo handed over
un-reconciled. That whole class can leave ZERO trace in outcomes.jsonl - only the owner's corrections
catch it (the classic shape: a proxy promoted to a settled book number + a 'switching saves $X'
recommendation, both later withdrawn).

Rule: turn ends on a decision-relevant MONEY figure ($ amount, or a /mo|/yr money rate) with NO
provenance-or-hedge qualifier anywhere in the message (proxy / estimate / ~ / a range / measured /
pending / from the statement / roughly / should / assume ...) -> block ONCE with a targeted nudge:
"label how you know it, or reconcile to the lived book, before you hand him a hard number."

Disciplined exactly like reality_monitor - money domain + decision-figure shape only, qualifier-aware,
loop-safe (stop_hook_active), one block per session, fail-safe silent. A noisy alarm gets tuned out, so
it ERRS TOWARD QUIET (one hedge anywhere suppresses): it must fire on bare-figure hand-overs and stay
silent on correctly-labeled proxy/measured/ranged claims. The embedded --selftest enforces BOTH directions.
"""
import sys, os, io, json, re, time

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, ".claim_blocked.json")
LOG = os.path.join(HERE, "claim_log.jsonl")

# shared discussion/attribution discriminator - a money figure inside "we should NOT report $180k" or a
# test-string quote is being DISCUSSED, not handed over; without this claim_check false-alarms on the
# money-in-test-prose that this session's own guard work is full of. Fail-safe: absent -> never suppresses.
sys.path.insert(0, HERE)
try:
    from discuss_gate import is_discussion
except Exception:
    def is_discussion(_t):
        return False

# A decision-relevant MONEY figure: a $-amount (>= ~$1k or with a k/m/million suffix), OR any $-amount
# carrying a rate suffix (/mo, a month, /yr), OR a spelled "N thousand/million". Small bare "$5" won't trip.
MONEY_FIG = re.compile(
    r"\$\s?\d[\d,]*(?:\.\d+)?\s*(?:k|m|mm|bn|million|billion|thousand)\b"          # $27k, $9.11M
    r"|\$\s?\d[\d,]*(?:\.\d+)?\s*(?:/\s?(?:mo|month|yr|year)|\s?(?:a|per)\s(?:month|year)"
    r"|\s?(?:monthly|yearly|annually|annual))"                                    # $650/mo, $126,000 a month, $24,000 monthly
    r"|\$\s?\d{1,3}(?:,\d{3})+(?:\.\d+)?"                                          # $24,000  $9,113,504 (comma-grouped)
    r"|\$\s?[1-9]\d{3,}(?:\.\d+)?\b",                                             # $1000+ plain (no commas)
    re.I)
# The money / market / risk domain - a figure is "high-stakes" only in this context.
# TICKERS: populate for your deployment - the instrument symbols whose mention marks money context.
TICKERS = ["ABC", "XYZ"]
MONEY_CTX = re.compile(
    r"\b(carry|hedge\w*|basis|drawdown|sharpe|book|account|margin|position|proceeds|borrow|"
    r"futures?|options?|puts?|calls?|" + "|".join(re.escape(t) for t in TICKERS) + r"|leverage[d]?|portfolio|p&l|pnl|"
    r"premium|save[sd]?|saving|cost[s]?|costing|profit|earn[s]?|per month|/mo|/yr|expense|financing|rebalanc\w*)\b",
    re.I)
# A qualifier - provenance OR hedge - ANYWHERE in the message means the number is not being passed as a
# bare settled fact. Erring toward quiet: one of these suppresses. (A RANGE like $16-43k is handled below.)
QUALIFIER = re.compile(
    r"~|≈|\bproxy\b|\bproxies\b|\bestimat|\bapprox|\broughly\b|\bround(ed|ly)?\b|\babout \$|\baround \$|"
    r"\border of\b|\bballpark\b|\bindicative\b|\bplaceholder\b|\bpending\b|\breconcil|\bunverified\b|\bunknown\b|"
    r"\bnot (yet )?(verified|reconciled|confirmed|checked|sure)\b|\bfrom the (statement|account|ledger|actual)\b|"
    r"\bmeasured\b|\bassum\w+|\bshould\b|\bmight\b|\bmay\b|\bcould\b|\blikely\b|\bprobably\b|\bi think\b|"
    r"\bi'?d need to check\b|\bballpark\b|\bwash\b|\brange\b|\bballpark\b|\bballpark\b",
    re.I)
# A numeric RANGE reads as a hedge (I'm not committing to a point): $16-43k, 3.58-3.91%, $118-142k/mo.
RANGE = re.compile(r"\$?\s?\d[\d,]*(?:\.\d+)?\s?[-–—]\s?\$?\d[\d,]*(?:\.\d+)?\s*(?:k|m|%|/mo|/yr|million)?", re.I)


def _last_assistant(tail):
    last = ""
    for ln in tail.splitlines()[-80:]:
        try:
            obj = json.loads(ln)
        except Exception:
            continue
        msg = obj.get("message", {}) if isinstance(obj.get("message"), dict) else {}
        role = msg.get("role") or obj.get("role") or obj.get("type")
        if role != "assistant":
            continue
        content = msg.get("content") or obj.get("content") or ""
        if isinstance(content, str):
            txt = content
        elif isinstance(content, list):
            txt = " ".join(c.get("text", "") for c in content
                           if isinstance(c, dict) and c.get("type") == "text")
        else:
            txt = ""
        if txt.strip():
            last = txt
    return last


def assess(text):
    """Pure decision function (testable): return the trigger label if this text hands over a bare
    high-stakes money figure with no qualifier, else None."""
    t = text or ""
    if not MONEY_FIG.search(t):
        return None
    if not MONEY_CTX.search(t):
        return None
    if QUALIFIER.search(t):
        return None
    if RANGE.search(t):
        return None
    if is_discussion(t):        # a figure being discussed/attributed/quoted, not handed over as a fresh claim
        return None
    return "a decision-relevant money figure stated as a settled number, with no provenance label (proxy/measured/estimate) and no hedge or reconciliation to the lived book"


def _dedup_ok(sess):
    if not sess:
        return True
    now = time.time()
    try:
        st = json.load(io.open(STATE, encoding="utf-8")) if os.path.exists(STATE) else {}
    except Exception:
        st = {}
    st = {k: v for k, v in st.items() if now - v < 86400}
    if sess in st:
        return False
    st[sess] = now
    try:
        json.dump(st, io.open(STATE, "w", encoding="utf-8"))
    except Exception:
        pass
    return True


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # Discovery is not execution: this script writes, so a help request must
        # exit before any work. See script_help_flag_can_execute_work.
        print(__doc__ or os.path.basename(__file__))
        return
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except Exception:
        return
    try:
        if data.get("hook_event_name") != "Stop":
            return
        if data.get("stop_hook_active"):
            return
        tp = data.get("transcript_path") or ""
        if not tp or not os.path.exists(tp):
            return
        with open(tp, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 200000))
            tail = f.read().decode("utf-8", "replace")
        last = _last_assistant(tail)
        if not last:
            return
        label = assess(last)
        if not label:
            return
        try:
            if not data.get("_gate"):
                with io.open(LOG, "a", encoding="utf-8") as lf:
                    lf.write(json.dumps({"ts": time.time(), "session": data.get("session_id", "")}) + "\n")
        except Exception:
            pass
        if not _dedup_ok(data.get("session_id") or ""):
            return
        reason = ("[claim-check] You're ending by handing over " + label + ". The costliest mistakes "
                  "are exactly this shape - a price PROXY stated as the book number, a switch that 'saves "
                  "$X' before the cash was reconciled. Before you sign off: label how you know it "
                  "(proxy / measured / estimate), or reconcile it to the actual account / statement / "
                  "lived book - and if it IS a proxy, say so and give a range, not a point. If it's already "
                  "grounded and labeled, say that in one line and stop.")
        # SHADOW-first: log the would-block; only ACTUALLY block if .reasoning_guards_live exists (opt-in;
        # the shadow log proves fire rate/precision on real sessions before it can disrupt one).
        _live = os.path.join(HERE, ".reasoning_guards_live")
        try:
            with io.open(os.path.join(HERE, "reasoning_guard_shadow.tsv"), "a", encoding="utf-8") as _sf:
                _sf.write("%.0f\tclaim_check\t%s\t%s\t%s\n" % (time.time(),
                          "BLOCK" if os.path.exists(_live) else "would-block",
                          (data.get("session_id") or "")[:12], (label or "")[:48]))
        except Exception:
            pass
        if os.path.exists(_live):
            sys.stdout.write(json.dumps({"decision": "block", "reason": reason}))
    except Exception:
        return


# ---- selftest: SYNTHETIC cases of the real shapes. FIRE on the mistakes, QUIET on labeled claims. ----
def selftest():
    FIRE = [
        "So your ABC carry is $12,345 a month.",
        "Switching to futures will save you $23k a month.",
        "The disconnect was $45k last month on the hedge.",
        "The financing nets you a $21,000 monthly credit on the book.",
        "Your leveraged-ETF carry costs $98,000 per month right now.",
    ]
    QUIET = [
        "The proxy suggests ~$95-120k/mo, but the real number is UNKNOWN pending the statement reconciliation.",
        "ABC options carry 3.81% vs the XYZ 3.71% - measured from exchange mids, essentially a wash.",
        "Keep-ABC + short-XYZ + futures shows a proxy delta of roughly $16-43k/mo cheaper.",
        "It costs about $650/mo, but that's an estimate with no commission data in the log.",
        "I released the index claim and notified the peer that it's free.",
        "The dream cycle completed at 17:53 after 12 minutes.",
        "The ABC short is $2.34M and the account cash is about $5.67M - both measured from the CSVs.",
        "Financing is ~$27.7k/mo on borrow; the cash credit depends on the margin haircut, which a snapshot can't see.",
    ]
    ok = True
    for t in FIRE:
        if assess(t) is None:
            print("  MISS (should fire):", t); ok = False
    for t in QUIET:
        if assess(t) is not None:
            print("  FALSE ALARM (should stay quiet):", t); ok = False
    print(("PASS" if ok else "FAIL") + f": {len(FIRE)} fire-cases, {len(QUIET)} quiet-cases")
    return ok


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(0 if selftest() else 1)
    main()
