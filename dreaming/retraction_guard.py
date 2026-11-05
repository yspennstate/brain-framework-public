#!/usr/bin/env python3
"""retraction_guard.py - Stop hook: catch the brain RE-ASSERTING a value/claim it has already RETRACTED.

The failure mode this closes is ANCHORING: a brain keeps returning to a withdrawn figure even after a
VERIFIED retraction memory said "do not use this as a settled number." Hedging it ("~$50k") makes it
look careful while the claim is still wrong. claim_check (bare-figure gate) and reality_monitor
(runtime-success gate) both miss this, because a re-asserted-withdrawn value is neither bare nor a
success claim - it's a CONTENT contradiction with the brain's own corrected knowledge.

This guard keeps a small, high-precision registry of RETRACTED claims (seeded with SYNTHETIC examples
below - replace them with your deployment's real retractions; growable via withdrawn_claims.jsonl /
the dream). At Stop, if the outgoing message re-states a withdrawn value in its domain context WITHOUT
acknowledging it's withdrawn (no 'withdrawn/retracted/superseded/old anchor/no longer'), it blocks once
and points at the retraction memory. Precision over recall: curated patterns only (no parsing arbitrary
memories), so it fires on the real anchor and stays silent on everything else, including correctly
citing the retraction.

Disciplined like its siblings: loop-safe (stop_hook_active), one block per session, fail-safe silent.
"""
import sys, os, io, json, re, time

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, ".retraction_blocked.json")
REGISTRY = os.path.join(HERE, "withdrawn_claims.jsonl")

# shared discussion/attribution discriminator (the general half of what the ACK below does entry-specific):
# citing/attributing/revising the withdrawn value, not re-asserting it, must stay quiet. Fail-safe import.
sys.path.insert(0, HERE)
try:
    from discuss_gate import is_discussion
except Exception:
    def is_discussion(_t):
        return False

# Seed registry - SYNTHETIC EXAMPLES demonstrating the entry schema; replace them with your deployment's
# actually-retracted claims. Each: value (regex for the withdrawn figure/claim), ctx (regex the domain
# must match), optional ack (entry-specific correct-framing words), memory (the retraction slug), note.
# ACK words near the value mean it's being cited as retracted -> quiet.
SEED = [
    {"value": r"\$?\s?50\s*(?:k|,000)\b.{0,40}(?:/mo|a month|per month|month)|\bfifty\b.{0,20}\bthousand\b.{0,20}\bmonth\b",
     "ctx": r"\bABC\b|carry|disconnect|hedge|income|/mo|per month|month",
     "memory": "example_retraction_2026_01_01",
     "note": "the $50k/month ABC-carry estimate is WITHDRAWN; the current number is UNKNOWN until the ledger reconciliation"},
    {"value": r"-\s?(?:6\d|7\d|8\d|9\d)\s?%",
     "ctx": r"(?:drawdown|max ?dd|draw ?down).{0,60}(?:lever|20\s?x|\bXYZ\b|book)|"
            r"(?:lever|20\s?x|\bXYZ\b|the book).{0,60}(?:drawdown|max ?dd|draw ?down)",
     "ack": r"crash.?tail|worst.?case|\brare\b|\bmedian\b|unlevered|sublinear|not (?:a )?(?:typical|expected)",
     "memory": "example_retraction_2026_01_02",
     "note": "a -60 to -85% drawdown for the levered XYZ book is WITHDRAWN - that was a crash-tail naively scaled by leverage; the real windowed number is far smaller. When your risk number contradicts the owner's lived book, YOUR number is wrong"},
]
# ACK = the value is being CITED as retracted, or COMPARED/REVISED away from - not asserted as current.
# Both forms must suppress (a disconfirming corpus test caught a false alarm on "$95k, above the
# $50k I gave you, haven't reconciled" - honest revision, wrongly flagged). Err toward quiet.
ACK = re.compile(r"\bwithdraw|\bretract|\bsupersed|\bno longer\b|\bold (anchor|estimate|number|figure)|"
                 r"\bformer\b|\bwas wrong\b|\bdisavow|\bstale\b|\bdon'?t use\b|\bnot .{0,15}settled\b|\bproxy\b|"
                 r"\bUNKNOWN\b|\breconcil|\bgave you\b|\bgave (?:you )?earlier\b|\babove the\b|\bbelow the\b|"
                 r"\bvs\.?\b|\bversus\b|\bcompared? to\b|\brevis|\bearlier (?:estimate|number|figure)\b|"
                 r"\bprevious\b|\bprior\b|"
                 # revision/comparison phrasings that false-alarmed in a shadow eval: calling the
                 # value an "anchor", rejecting it ("not the ..."), grounding a NEW measured number against it,
                 # or comparing by a multiplier ("2x your", "5x below"). A multiplier is ACK only before a
                 # reference word - so the drawdown entry's "20x levered" (leverage) is NOT suppressed.
                 r"\banchor\b|\bnot the\b|\bmeasured\b|\bagrees? (?:at|on)\b|"
                 r"\b\d+\s?x\s+(?:your|the|my|below|above|higher|lower)\b", re.I)


def _load_registry():
    reg = list(SEED)
    try:
        if os.path.exists(REGISTRY):
            for ln in io.open(REGISTRY, encoding="utf-8"):
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    e = json.loads(ln)
                    if e.get("value") and e.get("ctx"):
                        reg.append(e)
                except Exception:
                    continue
    except Exception:
        pass
    return reg


def assess(text, reg=None):
    """Return (note, memory) if the text re-asserts a withdrawn claim without acknowledging retraction."""
    t = text or ""
    if not t:
        return None
    if ACK.search(t) or is_discussion(t):
        return None                      # citing the retraction, discussing/attributing it, or hedged - fine
    for e in (reg if reg is not None else _load_registry()):
        try:
            if re.search(e["value"], t, re.I) and re.search(e["ctx"], t, re.I):
                ea = e.get("ack")                 # entry-specific "correct framing" words
                if ea and re.search(ea, t, re.I):
                    continue                       # cited with its correct nuance - not a naive re-assertion
                return (e.get("note", "a value the brain has retracted"), e.get("memory", ""))
        except Exception:
            continue
    return None


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


def _last_assistant(tail):
    last = ""
    for ln in tail.splitlines()[-80:]:
        try:
            obj = json.loads(ln)
        except Exception:
            continue
        msg = obj.get("message", {}) if isinstance(obj.get("message"), dict) else {}
        if (msg.get("role") or obj.get("role") or obj.get("type")) != "assistant":
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
        if data.get("hook_event_name") != "Stop" or data.get("stop_hook_active"):
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
        hit = assess(last)
        if not hit:
            return
        if not _dedup_ok(data.get("session_id") or ""):
            return
        note, mem = hit
        reason = ("[retraction-guard] You're re-asserting something the brain already RETRACTED: " + note +
                  (" (see " + mem + ")." if mem else ".") + " This is anchoring - the exact root of a "
                  "correction. Do not restate the withdrawn value as current. If you must mention it, say "
                  "plainly that it's withdrawn and give the corrected status; otherwise drop it and use the "
                  "reconciled number or say it's unknown.")
        # SHADOW-first (like pre_report_check's ENABLED gate): log the would-block; only ACTUALLY block if
        # .reasoning_guards_live exists. A rare precise block on a shared machine with the owner asleep must
        # be opt-in - the shadow log proves the fire rate/precision before it can disrupt any session.
        _live = os.path.join(HERE, ".reasoning_guards_live")
        try:
            with io.open(os.path.join(HERE, "reasoning_guard_shadow.tsv"), "a", encoding="utf-8") as _sf:
                _sf.write("%.0f\tretraction_guard\t%s\t%s\t%s\n" % (time.time(),
                          "BLOCK" if os.path.exists(_live) else "would-block",
                          (data.get("session_id") or "")[:12], (mem or "")[:48]))
        except Exception:
            pass
        if os.path.exists(_live):
            sys.stdout.write(json.dumps({"decision": "block", "reason": reason}))
    except Exception:
        return


def selftest():
    reg = list(SEED)
    FIRE = [
        "So the ABC carry is about $50k a month on the hedge.",
        "The hedge income runs 50,000 per month against ABC.",
        "Roughly $50k monthly is the ABC carry.",
        "The 20x levered XYZ book courts ruin - maxDD -60 to -85%.",
    ]
    QUIET = [
        "The old $50k anchor is withdrawn; the current carry is UNKNOWN pending reconciliation.",
        "That $50k/month figure was superseded - the real number needs the ledger.",
        "The proxy shows $95-120k/mo, a different measurement from the retracted 50k.",
        "The XYZ short is $2.34M and cash is $5.67M, measured from the CSVs.",
        "Switching instruments could save $23k/mo per the proxy, pending the ledger.",
        "The sim shows the ABC carry around $95k/month, above the $50k I gave you earlier; I haven't reconciled the two.",
        "The levered XYZ book's worst-case crash-tail drawdown was -62%, a rare tail, not a typical outcome.",
        # synthetic false-alarm shapes - honest revision/comparison of the withdrawn value,
        # NOT a naive re-assertion; the ACK must suppress these:
        "Two independent sims now agree at ~$95k/mo - about 2x your $50k anchor.",
        "Dropping the memory debate; the real number is my measured ~$110k, not the $50k.",
        "The audit shows ~$13k/mo, 5x below your $50k anchor - I can't see the notional yet.",
    ]
    ok = True
    for t in FIRE:
        if assess(t, reg) is None:
            print("  MISS (should fire):", t); ok = False
    for t in QUIET:
        if assess(t, reg) is not None:
            print("  FALSE ALARM (should stay quiet):", t); ok = False
    print(("PASS" if ok else "FAIL") + f": {len(FIRE)} fire, {len(QUIET)} quiet")
    return ok


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(0 if selftest() else 1)
    main()
