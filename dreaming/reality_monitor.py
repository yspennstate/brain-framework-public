#!/usr/bin/env python3
"""reality_monitor.py - source/reality monitoring at the end of a turn (Johnson & Raye 1981; Johnson,
Hashtroudi & Lindsay 1993).

The owner's single most frequent correction is "you said it's done / it works / it's deployed - and it
wasn't." At its root that is a source-monitoring error: mistaking an INFERRED or INTENDED state for an
OBSERVED one. Reality monitoring is the faculty that tells "I actually saw this happen" from "I assume
it did." The gut's `he_will_look` reflex fires BEFORE acting; this is the end-of-turn half - the only
place the brain can inspect its own outgoing CLAIM, because hooks see tool I/O, not the assistant's prose
(the Stop hook gets the transcript, so it can).

What it does: when the turn ends on a strong RUNTIME-SUCCESS claim (works / deployed / tests pass / live)
but no verifying OBSERVATION happened in the turn (no run, no read of what the owner will actually see),
it blocks exactly once - "take one concrete look before you sign off." Everything else passes. It also
logs every runtime claim (grounded or not) to reality_log.jsonl so the dream can learn whether
claimed-done matched reality over time.

Loop-safe by construction: honours `stop_hook_active` (never blocks inside its own continuation) and
dedups to at most ONE block per session. Errs toward NOT blocking - grounding is assumed if any
observation tool ran recently, so a real verification is never second-guessed. Fail-safe silent on error.
"""
import sys, os, io, json, re, time

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, ".reality_blocked.json")
LOG = os.path.join(HERE, "reality_log.jsonl")

# strong claims about RUNTIME / deployment reality - the kind that need an observation to back them.
# Deliberately NOT "done/fixed" alone (those can be a pure edit); only claims about a live/running result.
CLAIM_RE = re.compile(
    r"\b(it works|works now|working now|it'?s working|(is |now |just )?deployed|all (tests?|checks?) "
    r"(pass|passed|passing|are green)|tests? (are |now )?pass|verified (it|that|the)|confirmed (it |that )?"
    r"work|is live|now live|it'?s live|up and running|good to go|is running now|ran (clean|green|fine))\b",
    re.I)
# hedged language nearby means it's NOT an unqualified claim - leave it alone.
HEDGE_RE = re.compile(r"\b(should|might|may|probably|likely|i think|i believe|appears?|seems?|expect|"
                      r"in theory|presumably|assuming|if .* works|hopefully|not (yet )?(sure|verified|tested)|"
                      r"haven'?t (verified|tested|checked|run)|untested|can'?t verify)\b", re.I)
# any of these in the recent tail => a genuine OBSERVATION happened => treat the claim as grounded.
# Only real look-at-reality tools count. NOT a bare tool_result: every tool (Edit/Write included) emits
# one, so grounding on it let an EDIT stand in for a verification - a turn that only edited and then said
# "it works" passed unblocked, which is the exact "no run, no read" over-claim this guard exists to catch
# (verified: the edit-only claim slipped through before this). The name markers match the real transcript
# format exactly (checked), so dropping the generic tool_result keeps Bash/Read/etc. grounding intact.
OBSERVE_RE = re.compile(r'"name"\s*:\s*"(Bash|PowerShell|Read|Grep|Glob|BashOutput|NotebookRead|WebFetch|TaskOutput)"')


def _dedup_ok(sess):
    """At most one block per session, ever (86400s window)."""
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


_URL = re.compile(r"https?://[^\s\"'<>)\]]+", re.I)
_WINPATH = re.compile(r"[A-Za-z]:\\[^\s\"'<>|)\]]+\.[A-Za-z0-9]{1,5}")
_NIXPATH = re.compile(r"(?:~|/[\w.\-]+)(?:/[\w.\-]+)+\.[A-Za-z0-9]{1,5}")
_BOX = re.compile(r"\b(the box|on the box|azure|the server|ssh|deployed to|systemctl|the endpoint|the daemon|the observer)\b", re.I)


def _name_the_look(text):
    """Name the exact artifact the claim points at + the one look that would confirm it, so verifying
    is cheaper than the confident guess. Falls back to the generic nudge when nothing concrete is named."""
    t = text or ""
    m = _URL.search(t)
    if m:
        return "Take ONE concrete look - load " + m.group(0)[:90] + " and read what he'll actually see -"
    m = _WINPATH.search(t) or _NIXPATH.search(t)
    if m:
        return "Take ONE concrete look - open " + m.group(0)[:90] + " and read what he'll actually get -"
    if _BOX.search(t):
        return ("Take ONE concrete look - check the live target itself (ssh / the process / the endpoint), "
                "that it's really running and not just that you deployed it -")
    return "Take ONE concrete look - run it, or read the artifact he'll see -"


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
        # Continuation stops (stop_hook_active) must never BLOCK twice, but they
        # must still LOG: inside a timed work session every Stop is a continuation
        # (the work clock re-blocks each turn), and the early return here silenced
        # the claimed-vs-reality stream for whole 10-hour sessions (found 2026-07-14:
        # 30 log lines total while a work session produced dozens of claim-bearing
        # turns; the dream's reality review was reading a hole, not a record).
        continuation = bool(data.get("stop_hook_active"))
        tp = data.get("transcript_path") or ""
        if not tp or not os.path.exists(tp):
            return
        # read only the tail - transcripts get large
        with open(tp, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 200000))
            tail = f.read().decode("utf-8", "replace")
        lines = tail.splitlines()[-60:]
        observed = any(OBSERVE_RE.search(ln) for ln in lines)
        last_asst = ""
        for ln in lines:
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
                last_asst = txt  # keep the last assistant text block - the message ending the turn
        if not last_asst:
            return
        m = CLAIM_RE.search(last_asst)
        if not m:
            return
        hedged = bool(HEDGE_RE.search(last_asst))
        # log the claim for the dream's reality-vs-claim review - but never the humility_check gate's
        # fixtures ("_gate"): 60 of the log's first 72 entries were the gate's rm1/rm2 replays, and the
        # dream would have "learned" claimed-vs-reality statistics from fiction (caught 2026-07-08).
        try:
            if not data.get("_gate"):
                with io.open(LOG, "a", encoding="utf-8") as lf:
                    lf.write(json.dumps({"ts": time.time(), "claim": m.group(0)[:60],
                                         "grounded": bool(observed), "hedged": hedged,
                                         "session": data.get("session_id", "")}) + "\n")
        except Exception:
            pass
        if continuation:
            return  # logged above; never block a continuation stop (loop guard)
        if observed or hedged:
            return  # backed by an observation, or explicitly hedged - fine
        if not _dedup_ok(data.get("session_id") or ""):
            return
        # Make the reality cheaper to reach than the representation (the deepest mistake-harvest
        # finding): a warning only adds friction; naming the EXACT thing to look at and the command
        # to do it flips the economics, so checking is easier than the confident guess. Pull the most
        # concrete artifact the claim points at, and hand back the one look that would confirm it.
        look = _name_the_look(last_asst)
        reason = ("[reality-monitor] You're ending on a runtime-success claim - \"" + m.group(0) +
                  "\" - but I don't see a verifying observation in this turn (nothing was run or read to "
                  "see what he'll actually get). His most frequent correction is exactly this: 'you said "
                  "it worked and it didn't.' " + look + " and report what you actually observed. If you "
                  "already verified and it's genuinely done, say so plainly in one line and stop.")
        sys.stdout.write(json.dumps({"decision": "block", "reason": reason}))
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
