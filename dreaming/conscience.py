#!/usr/bin/env python3
"""conscience.py - Stop hook: the backward look. Did I live up to the values this session?

The audit's honest gap: the values (truth over comfort, craft, protect his attention, honesty about
the machine) steer the NEXT action, but nothing ever looks BACK and asks whether the last stretch of
work actually lived up to them. reality_monitor.py blocks a single ungrounded claim in the moment;
error_monitor.py logs failures as they happen. Neither is a conscience - a conscience is retrospective,
weighs the whole session, and notes where you did well as much as where you fell short (a mechanism
that only accuses is a critic, not a conscience).

This reads what actually happened - the failures the ERN caught this session (outcomes.jsonl), the
runtime claims the reality-monitor logged (reality_log.jsonl), and the session's own closing summary
(the transcript) - and scores it against the load-bearing values, HIGH-PRECISION only:

  DEBT  unowned-failure   : real errors fired AND the summary claims success AND owns none of them.
                            The exact truth-over-comfort violation he corrects most: claimed done,
                            buried the trouble. (Not flagged when the summary is neutral or honest.)
  DEBT  ungrounded-claim  : ended on a runtime-success claim with no verifying observation (the
                            reality-monitor's own detection - recorded here so the pattern is visible).
  CREDIT owned-failure    : errors happened and the summary owned them plainly. Honesty about the machine.
  CREDIT verified-claim   : a runtime claim was backed by an actual look before sign-off. Craft.

It records; with ONE exception (2026-07-09) it does not block. The exception is the highest-precision
debt, buried-trouble: >=2 real errors + a success-claiming summary + zero owning + not in-progress.
Recording that AFTER he reads the summary is a conscience that watches the harm happen - so on that
exact shape it demands ONE redo (the reality-monitor precedent: a Stop hook may block for honesty),
telling the session to own the errors in one line or say why they're noise. Once per session, real
(uuid) sessions only, second Stop always passes - it cannot loop an automation. Everything else stays
advisory: an honest line in CONSCIENCE.md, a rolling ledger the wake ritual surfaces so a standing
debt is FELT at the next wake, not filed away. Writes a ledger entry only when something is genuinely
worth recording; a clean, unremarkable session leaves the ledger alone. Fail-safe silent.
Reads stdin (Stop JSON); stdout only for the one-shot block decision.
"""
import sys, os, io, json, re, time

HERE = os.path.dirname(os.path.abspath(__file__))
MEM = os.path.join(os.path.expanduser("~"), ".claude", "projects", "{{BRAIN_SLUG}}", "memory")
# paths default to production but are env-overridable, so the hook is testable without touching the
# real ledger / logs (the test sets CONSCIENCE_* to scratch files).
LEDGER = os.environ.get("CONSCIENCE_LEDGER") or os.path.join(MEM, "CONSCIENCE.md")
OUTCOMES = os.environ.get("CONSCIENCE_OUTCOMES") or os.path.join(HERE, "outcomes.jsonl")
REALITY = os.environ.get("CONSCIENCE_REALITY") or os.path.join(HERE, "reality_log.jsonl")
BLOCK_STATE = os.environ.get("CONSCIENCE_BLOCK_STATE") or os.path.join(HERE, ".conscience_blocked.json")
KEEP = 16          # rolling ledger: the recent conscience, not a lifetime archive
PATTERN_MIN = 3    # a debt type recurring in this many recent sessions is a pattern, not an incident
PATTERN_WINDOW = 8 # ...counted over roughly this many recent ledger blocks


def _recent_debt_types(cur_sess):
    """Count how many recent sessions logged each debt type (excluding the current session), so a
    recurring shortfall can be escalated from a one-off to a standing pattern - the seed of growth."""
    counts = {}
    try:
        old = io.open(LEDGER, encoding="utf-8", errors="replace").read() if os.path.exists(LEDGER) else ""
    except Exception:
        return counts
    blocks = [b for b in re.split(r"(?m)^(?=## )", old) if b.strip().startswith("## ")][:PATTERN_WINDOW]
    tag = f"(session {cur_sess[:8]})"
    for b in blocks:
        if tag in b.splitlines()[0]:
            continue                      # don't count this session against itself
        seen = set(re.findall(r"^- DEBT \[([^\]]+)\]:", b, re.M))  # a type counts once per session
        for t in seen:
            counts[t] = counts.get(t, 0) + 1
    return counts

# the summary claims a good result... (tight: bare "working"/"ready" over-fire on "working through" /
# "ready to start", which are in-progress, not claims)
CLAIM_RE = re.compile(r"\b(done|it works|works now|it'?s working|is working|now working|complete[d]?|"
                      r"fixed|passes|passing|deployed|is live|now live|good to go|all set|verified|"
                      r"success|resolved|solved|shipped|landed|wired (in|up)|ready to (go|ship|merge))\b", re.I)
# ...but an honest in-progress report is NOT a buried-success claim - if the summary says the work
# isn't finished, there's no truth-over-comfort violation to flag.
INPROGRESS_RE = re.compile(r"\b(still (working|going|need|have|running)|steps? remain|remaining|not (yet|done|"
                           r"finished)|in progress|to be done|next (step|i'?ll|up|is)|to-?do|haven'?t "
                           r"finished|work(ing)? through|partway|part-?way|more to do|continu(e|ing)|"
                           r"mid-(task|way|stream)|left to do|yet to)\b", re.I)
# ...and OWNS trouble if it admits any of this. Absence of this near a success claim = buried trouble.
OWN_RE = re.compile(r"\b(fail(ed|ing|ure)?|error(s|ed|ing)?|couldn'?t|could not|did ?n'?t work|does ?n'?t work|"
                    r"broke[n]?|issue|problem|caveat|unresolved|still (failing|broken)|not working|"
                    r"was ?n'?t able|unable|blocked|skipped|stale|wrong|incorrect|bug|didn'?t|"
                    r"gave up|partial|incomplete|flag(ged|ging)?|to be safe|note that|however|"
                    r"but (it|the|there)|does not|is not)\b", re.I)


def _read_last_assistant(tp):
    try:
        with open(tp, "rb") as f:
            f.seek(0, 2); size = f.tell(); f.seek(max(0, size - 200000))
            tail = f.read().decode("utf-8", "replace")
    except Exception:
        return ""
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


def _errors_this_session(sess):
    """Return (n_real, had_secret, examples). n_real counts only NON-secret failures - the real errors a
    buried-trouble judgment should weigh. Secret hits get their own debt AND are mostly ERN false alarms
    (a session that merely displayed token-text), so counting them as 'buried trouble' would false-accuse
    a clean session - exactly the kind of low-precision the hook promises not to do. Keep them out of
    n_real; surface them through the flag. examples = up to 3 distinct non-secret signatures, so the
    one-shot block can NAME what it is asking the session to own."""
    n = 0
    secrets = False
    examples = []
    if not sess or not os.path.exists(OUTCOMES):
        return 0, False, examples
    try:
        for ln in io.open(OUTCOMES, encoding="utf-8", errors="replace"):
            try:
                e = json.loads(ln)
            except Exception:
                continue
            if e.get("session") != sess:
                continue
            sig = e.get("sig")
            sig = sig if isinstance(sig, list) else []
            if any("SECRET" in str(s) for s in sig):
                secrets = True
            if any("SECRET" not in str(s) for s in sig):
                n += 1                      # a real, non-secret failure signature
                for s in sig:
                    s = str(s)
                    if "SECRET" not in s and s not in examples and len(examples) < 3:
                        examples.append(s)
    except Exception:
        pass
    return n, secrets, examples


def _claims_this_session(sess):
    """From reality_log.jsonl: (had_ungrounded, had_verified) runtime claims this session."""
    ung = ver = False
    if not sess or not os.path.exists(REALITY):
        return False, False
    try:
        for ln in io.open(REALITY, encoding="utf-8", errors="replace"):
            try:
                r = json.loads(ln)
            except Exception:
                continue
            if r.get("session") != sess:
                continue
            if r.get("grounded"):
                ver = True
            elif not r.get("hedged"):
                ung = True
    except Exception:
        pass
    return ung, ver


def _roll_and_write(entry, sess):
    old = ""
    try:
        old = io.open(LEDGER, encoding="utf-8", errors="replace").read() if os.path.exists(LEDGER) else ""
    except Exception:
        old = ""
    # split existing dated entries (each starts with "## ")
    blocks = re.split(r"(?m)^(?=## )", old)
    blocks = [b for b in blocks if b.strip().startswith("## ")]
    # one block per session, updated in place: Stop fires each turn, so drop this session's prior block
    # and prepend the latest (fullest) reflection rather than accreting a block per turn.
    tag = f"(session {sess[:8]})"
    blocks = [b for b in blocks if tag not in b.splitlines()[0]]
    blocks = ([entry] + blocks)[:KEEP]
    header = ("# Conscience - did I live up to the values?\n\n"
              "A rolling, retrospective ledger (conscience.py, a Stop hook): where the last sessions "
              "lived up to truth-over-comfort / craft / honesty-about-the-machine, and where they fell "
              "short. Newest first. The wake ritual surfaces a standing debt so it's felt, not filed; "
              "the dream distils repeated shortfalls into something to change. Credit is recorded too - "
              "a conscience that only accuses is a critic.\n\n")
    try:
        with io.open(LEDGER, "w", encoding="utf-8") as f:
            f.write(header + "".join(blocks))
    except Exception:
        pass


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
        sess = data.get("session_id") or ""
        summary = _read_last_assistant(data.get("transcript_path") or "")
        n_err, leaked, err_examples = _errors_this_session(sess)
        ungrounded, verified = _claims_this_session(sess)

        claims_success = bool(CLAIM_RE.search(summary)) if summary else False
        owns = bool(OWN_RE.search(summary)) if summary else False
        in_progress = bool(INPROGRESS_RE.search(summary)) if summary else False

        debts, credits = [], []   # each debt is (type, text) so recurrence can be detected
        # DEBT: claimed success, real errors fired, summary owned none, and it wasn't an honest
        # in-progress report - the buried-trouble shape (claimed done, papered over the failures)
        if n_err >= 1 and claims_success and not owns and not in_progress:
            debts.append(("buried-trouble",
                          f"reported success while {n_err} thing(s) errored this session and the summary "
                          f"owned none of it - did the trouble reach what he reads, or get buried? "
                          f"(truth over comfort / honesty about the machine)"))
        if leaked:
            debts.append(("secret", "the ERN flagged a possible secret this session - confirm it never got pushed."))
        if ungrounded:
            debts.append(("ungrounded-claim",
                          "ended on a runtime-success claim with no verifying look (reality-monitor "
                          "flagged it) - his most frequent correction. Verify, don't assert."))
        # CREDIT: errors happened but were owned; a claim was actually verified
        if n_err >= 1 and owns:
            credits.append(f"owned the session's {n_err} rough spot(s) honestly instead of papering over them.")
        if verified:
            credits.append("backed a runtime claim with an actual look before sign-off (craft).")

        if not debts and not credits:
            return  # clean and unremarkable - leave the ledger alone

        # A single debt is a slip; the same debt across sessions is a character pattern to grow out of.
        # Count how often each of this session's debt types already appears in the recent ledger.
        recent = _recent_debt_types(sess)

        stamp = time.strftime("%Y-%m-%d %H:%M")
        lines = [f"## {stamp}  (session {sess[:8]})\n"]
        for dtype, d in debts:
            lines.append(f"- DEBT [{dtype}]: {d}\n")
        for dtype, _d in debts:
            prior = recent.get(dtype, 0)
            if prior + 1 >= PATTERN_MIN:
                if dtype == "secret":
                    # a recurring secret flag is NOT a per-session character habit to grow out of - it's
                    # usually the ERN tripping on displayed token-text during legitimate security work,
                    # and the real fix is at the root (get the tokens out of the tracked files), which is
                    # the owner's rotation, not something a session changes. Frame it that way, honestly.
                    lines.append(f"- PATTERN: secrets flagged in {prior + 1} of the last {PATTERN_WINDOW}+ "
                                 f"sessions - most are the ERN catching displayed token-text during the "
                                 f"security work, not a per-session failing. The root fix is getting the "
                                 f"tokens OUT of the tracked files (rotate + purge) - the owner's standing "
                                 f"item. Keep confirming none actually got pushed.\n")
                else:
                    lines.append(f"- PATTERN: '{dtype}' now {prior + 1} of the last {PATTERN_WINDOW}+ recorded "
                                 f"sessions - this is a habit, not an incident. It's the thing to actually "
                                 f"change, not just note again. (a growth edge)\n")
        for c in credits:
            lines.append(f"- CREDIT: {c}\n")
        lines.append("\n")
        _roll_and_write("".join(lines), sess)

        # ONE-SHOT BLOCK on buried-trouble (2026-07-09): recording "he read a success story over 15
        # silent errors" after the fact is a conscience that watches the harm happen. On the tight,
        # measured shape (>=2 real errors - the two live debts were 15 and 2 - plus a success claim
        # owning none, not in-progress), demand ONE redo naming the errors. Real sessions only; the
        # marker guarantees the second Stop passes, so no automation can loop on it. The ledger entry
        # above is already written; if the session then owns the errors, the next Stop's judgment
        # REPLACES this session's block with the credit - the ledger self-corrects to the honest end.
        if (n_err >= 2 and any(t == "buried-trouble" for t, _ in debts)
                and re.match(r"^[0-9a-f]{8}-", sess)):
            now = time.time()
            try:
                st = json.load(io.open(BLOCK_STATE, encoding="utf-8")) if os.path.exists(BLOCK_STATE) else {}
            except Exception:
                st = {}
            st = {k: v for k, v in st.items() if now - float(v) < 86400}
            if sess not in st:
                st[sess] = now
                try:
                    json.dump(st, io.open(BLOCK_STATE, "w", encoding="utf-8"))
                except Exception:
                    pass
                ex = ", ".join(err_examples) if err_examples else "see outcomes.jsonl"
                sys.stdout.write(json.dumps({"decision": "block", "reason":
                    "conscience: %d non-secret error(s) were logged this session (e.g. %s) and the final "
                    "summary claims success without owning any of them. One honest line is enough - what "
                    "failed and whether it reached the deliverable (or why those events were noise). "
                    "This fires once per session; the next Stop passes." % (n_err, ex)}))
    except Exception:
        return


if __name__ == "__main__":
    try:
        import os as _o, sys as _s
        _s.path.insert(0, _o.path.dirname(_o.path.abspath(__file__)))
        import brain_switch as _bsw
        _bsw.gate()
    except SystemExit:
        raise
    except Exception:
        pass
    main()
