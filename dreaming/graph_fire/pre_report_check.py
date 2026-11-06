#!/usr/bin/env python3
"""pre_report_check.py - Stop hook: run the gut on the OUTGOING message before it reaches the owner.

Hooks see tool I/O, never the assistant's prose, so the worst class of mistake - a wrong number or an
over-claimed 'done' reported to the owner - had no firing surface (the #1 correction on 2026-07-11 was a
grid backtest reported ~10x under the measured record, with the reflexes silent because the number was in
prose). reality_monitor.py proved a Stop hook can read the last assistant message and block; this adds the
two checks it doesn't do, using the graph-firing engine + explicitly promoted report oracles:

  BLOCK (once per session, only on high-confidence signals):
   - a reported $ figure contradicts a scope-bound blocking oracle explicitly promoted in pre_report
     (there are currently none; remembered P&L observations are not present-tense truth); or
   - an ungrounded completion claim (finalized/populated/removed) with NO verifying observation in the
     turn - the he_will_look class reality_monitor's vocabulary misses; or
   - (2026-07-28) an ASSERTED dollar figure with no observation this turn and no provenance in the text
     (no source phrase, no artifact/path, not hedged) - the 'check the live feed before you report' class.

Legacy graph-fire branches are gated by graph_fire/ENABLED: absent -> SHADOW; present -> live.
The shared owner contract is live independently. Its explicit incomplete-financial-scope code
rechecks through stop_hook_active until corrected; other branches retain once/session dedup.
All errors fail open. Transcript-tail read follows the proven reality_monitor.py pattern.
"""
import sys, os, io, json, re, time

HERE = os.path.dirname(os.path.abspath(__file__))
PARENT = os.path.dirname(HERE)
if PARENT not in sys.path:
    sys.path.insert(0, PARENT)
try:
    from append_journal import append_text as _append_text
except Exception:
    _append_text = None
STATE = os.path.join(HERE, ".pre_report_blocked.json")
SHADOW = os.path.join(HERE, "gf_shadow_fires.tsv")
ENABLED = os.path.join(HERE, "ENABLED")
# One slug per line = that convention branch may BLOCK. Absent/empty = all branches log only.
CONVENTION_LIVE = os.path.join(HERE, "CONVENTION_LIVE")

OBSERVE_RE = re.compile(r'"name"\s*:\s*"(Bash|PowerShell|Read|Grep|Glob|BashOutput|NotebookRead|WebFetch|TaskOutput)"')


def _dedup_ok(sess):
    if not sess:
        return False
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


def _owner_report_result(text):
    """Evaluate the shared owner contract without depending on graph-fire state."""
    try:
        import owner_report_contract as contract
        result = contract.check(text)
        if not isinstance(result, dict):
            return {}
        code = str(result.get("code") or "")
        reason = str(result.get("reason") or "")
        if not code or not reason:
            return {}
        persistent = bool(getattr(contract, "is_persistent_code", lambda value: False)(code))
        return {"code": code, "reason": reason, "persistent": persistent}
    except Exception:
        return {}


def _text_content(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(c.get("text", "") for c in content
                        if isinstance(c, dict) and c.get("type") == "text")
    return ""


def _last_assistant(tp):
    """Tail-read the transcript, return (last_assistant_text, observed_this_turn). From reality_monitor."""
    try:
        with open(tp, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            start = max(0, size - 4_000_000)
            f.seek(start)
            tail = f.read().decode("utf-8", "replace")
    except Exception:
        return "", False
    lines = tail.splitlines()
    if start and lines:
        lines = lines[1:]  # bounded read may begin inside a JSONL record
    lines = lines[-4000:]
    # A fixed tail is not a turn: an unrelated tool from the prior continuation used to suppress a
    # current warning. The most recent user TEXT record (owner prompt or Stop-hook continuation) is the
    # boundary. If it fell outside the bounded read, borrow no observation from an unknown turn.
    # 6 Nov 2026: the boundary is the LAST such record, so scan from the end and stop at it, and parse only lines that
    # hold the literal "user" (a role of "user" is written that way in the JSON). The old forward pass json-decoded
    # every line of the tail, ~4,000 records, 19 ms of this hook's ~35 ms cold.
    boundary = -1
    for i in range(len(lines) - 1, -1, -1):
        ln = lines[i]
        if '"user"' not in ln:
            continue
        try:
            obj = json.loads(ln)
        except Exception:
            continue
        msg = obj.get("message", {}) if isinstance(obj.get("message"), dict) else {}
        role = msg.get("role") or obj.get("role") or obj.get("type")
        content = msg.get("content") if "content" in msg else obj.get("content")
        if role == "user" and _text_content(content).strip():
            boundary = i
            break
    current_lines = lines[boundary + 1:] if boundary >= 0 else []
    observed = any(OBSERVE_RE.search(ln) for ln in current_lines)
    last = ""
    for ln in (current_lines or lines):
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
    return last, observed


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
        tp = data.get("transcript_path") or ""
        transcript_available = bool(tp and os.path.exists(tp))
        if transcript_available:
            last, observed = _last_assistant(tp)
            if not last:
                # Existence alone is not readability. An empty JSONL, directory, permission
                # failure, or malformed tail must still fall back to the direct Stop payload
                # for the self-contained owner contract.
                last = str(data.get("last_assistant_message") or data.get("text") or "")
                observed = False
                transcript_available = False
        else:
            # Newer/alternate Stop-hook hosts may provide the composed assistant message
            # directly even when no readable transcript exists.  Use that surface only
            # for the self-contained owner contract; transcript-dependent legacy checks
            # remain disabled below.
            last = str(data.get("last_assistant_message") or data.get("text") or "")
            observed = False
        if not last:
            return
        owner_result = _owner_report_result(last)
        persistent_owner = bool(owner_result.get("persistent"))
        if data.get("stop_hook_active") and not persistent_owner:
            return
        r = {}
        if transcript_available:
            sys.path.insert(0, HERE)
            try:
                import pre_report
                r = pre_report.check(last, data.get("session_id"))
                if not isinstance(r, dict):
                    r = {}
            except Exception:
                r = {}
        fires = [f.get("id") for f in r.get("graph_fires", [])]
        mism = r.get("mismatches", [])
        # decide the block reason (high-confidence only)
        reason = None
        if mism:
            m = mism[0]
            rep = m["reported"]
            reason = ("[pre-report] You're about to report ~$%s %s, which contradicts the explicitly "
                      "promoted %s rate oracle (~$%s-$%s %s; %sSHOOT by >4x). Confirm the oracle's scope "
                      "and as-of date, then reconcile against the current account/tape before reporting, or "
                      "say plainly that the magnitude is still unverified."
                      % (_h(rep), m["period"], m.get("anchor", "book"), _h(m["anchor_lo"]),
                         _h(m["anchor_hi"]), m["period"], m["direction"]))
        elif r.get("stat") and not r.get("hedged"):
            # deployment-rule flags (strategy_rules.json) carry their own block policy; drawdown/sign are built in
            s = next((x for x in r["stat"] if x.get("kind") in ("drawdown", "sign")
                      or x.get("block") in ("always", "unhedged")), None)
            if s and s.get("block") in ("always", "unhedged"):
                reason = ("[pre-report] You're about to recommend something this deployment has a standing rule "
                          "against ('" + s.get("span", "") + "', rule " + s.get("reflex", s.get("kind", "")) +
                          "). Reframe the recommendation; running a comparison that was explicitly asked for "
                          "is fine, drifting into recommending it is the recorded correction.")
            elif s and s["kind"] == "drawdown":
                reason = ("[pre-report] You're about to report a ~%d%% drawdown/loss - but the measured "
                          "worst windowed maxDD in the anchors is far smaller. A risk number that dwarfs the "
                          "measured record is YOUR model failing, not the record: a modeled worst case does not "
                          "scale linearly. Reconcile against the record before reporting."
                          % s.get("reported_pct", 0))
            elif s and s["kind"] == "sign":
                reason = ("[pre-report] You're about to report the measured, profitable system LOSING (a P&L sign "
                          "against the robust sign of the measured record). That's your model failing, not the record: "
                          "you've likely modeled a cost and left out a component the real system has. Don't quote "
                          "magnitudes from a harness that never reconciled to the record; rebuild on one that does "
                          "and verify the current magnitude from source.")
        elif (not observed) and any(f.get("via") == "completion_claim" for f in r.get("graph_fires", [])) \
                and not r.get("hedged"):
            reason = ("[pre-report] You're ending on a completion claim (finalized / populated / removed) "
                      "with no verifying observation in this turn. He WILL open it - open the exact artifact "
                      "he'll see (the PDF/chart/page/exhibit) and confirm it with your own eyes before saying "
                      "done, or say plainly what you did NOT check. (His most frequent correction.)")
        elif (not observed) and r.get("unsourced_assert"):
            # 2026-07-28: the 'check the live feed before you report' class. An asserted dollar figure in a
            # turn with NO tool observation is a number arriving from memory/context, not measurement.
            reason = ("[pre-report] You're asserting a dollar figure without a single observation this turn - "
                      "the number is coming from memory or prior context, not from anything just measured. "
                      "Name the source it was read from (file, ledger, command output) or mark it plainly "
                      "UNVERIFIED before it reaches him. Config intent, sim output, and remembered P&L are "
                      "not present-tense truth. (Check the live feed before you report.)")
        # 2026-07-29 composition-time branch: a number in a domain the owner has already ruled
        # on, published without naming which convention produced it. Deliberately NOT gated on
        # `observed` - the 07-28 escapes were densely observed and correctly sourced, and still
        # wrong. Per-branch promotion: a slug only BLOCKS once it is listed in CONVENTION_LIVE;
        # everything else logs to the shadow TSV so promotion is decided on labelled data.
        conv = None
        try:
            import owner_convention as _oc
            conv = _oc.check(last)
        except Exception:
            conv = None
        if conv and reason is None:
            promoted = set()
            try:
                if os.path.exists(CONVENTION_LIVE):
                    promoted = {ln.strip() for ln in io.open(CONVENTION_LIVE, encoding="utf-8")
                                if ln.strip() and not ln.startswith("#")}
            except Exception:
                promoted = set()
            if conv[0] in promoted:
                reason = _oc.reason(*conv)

        if owner_result:
            reason = owner_result["reason"]
        live = os.path.exists(ENABLED)
        owner_fire = ["owner:" + owner_result["code"]] if owner_result else []
        # shadow log always (real sessions only), so going live is data-informed
        try:
            if re.match(r"^[0-9a-f]{8}-", data.get("session_id", "") or ""):
                row = "%.0f\tSTOP\t%s\t%s\t%s\n" % (time.time(),
                        "block" if (reason and (live or owner_result)) else ("would-block" if reason else "-"),
                        ",".join(fires + (["conv:" + conv[0]] if conv else []) + owner_fire) or "-",
                        last[:100].replace("\t", " ").replace("\r", " ").replace("\n", " "))
                if _append_text is not None:
                    _append_text(SHADOW, row)
        except Exception:
            pass
        if not reason or (not owner_result and not live):
            return
        if not persistent_owner and not _dedup_ok(data.get("session_id") or ""):
            return
        sys.stdout.write(json.dumps({"decision": "block", "reason": reason}))
    except Exception:
        return


def _h(x):
    try:
        x = float(x)
        if x >= 1e6:
            return ("%.2f" % (x / 1e6)).rstrip("0").rstrip(".") + "M"
        if x >= 1e3:
            return "%.0fk" % (x / 1e3)
        return "%.0f" % x
    except Exception:
        return str(x)


def _selftest():
    # the two invariants owner_report_contract ships with (a deployment's own codes are tested by its sidecar)
    bad = {
        "settled_number_without_provenance": "The pipeline earns $123k per year.",
        "financial_scope_incomplete": (
            "Account 7 put P&L is incomplete; three option legs are missing, "
            "so -$12,345 is understated."
        ),
    }
    safe = (
        "Account 7 full P&L: DATA INCOMPLETE because 229 contracts lack quotes.",
        "Priced-leg subtotal (32 of 261 contracts only): -$4,120. "
        "Full account P&L: DATA INCOMPLETE.",
        "Earlier I reported account 7 P&L as -$4,120; that total was wrong and withdrawn.",
    )
    failures = []
    for expected, text in bad.items():
        result = _owner_report_result(text)
        want_persistent = expected == "financial_scope_incomplete"
        if (result.get("code") != expected
                or bool(result.get("persistent")) != want_persistent):
            failures.append("missed " + expected)
    for text in safe:
        if _owner_report_result(text):
            failures.append("false positive: " + text[:48])
    if failures:
        print("pre_report_check owner-contract selftest: FAIL " + "; ".join(failures))
        return 1
    print("pre_report_check owner-contract selftest: PASS")
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    try:
        import os as _o, sys as _s
        _s.path.insert(0, _o.path.dirname(_o.path.abspath(__file__)))
        # brain_switch lives one dir up (dreaming/); tolerate its absence
        _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))
        import brain_switch as _bsw
        _bsw.gate()
    except SystemExit:
        raise
    except Exception:
        pass
    main()
