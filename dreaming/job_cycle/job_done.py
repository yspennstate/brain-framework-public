#!/usr/bin/env python3
"""job_done.py - Stop hook: a job does not end until its readme is in the brain.

The cycle the owner asked for on 2026-07-26: job done -> write a readme -> put it in the brain -> clear
everything -> tell me what it did. This hook owns the first three and stages the fourth.

  1. Model finishes a turn that did real work.
  2. This hook BLOCKS once with the exact command to file the readme.
  3. Model files it (job_readme.py write) and reports to the owner.
  4. This hook sees the readme, lets the turn end, and tells the owner to press /clear.
  5. job_reseed.py hands the readme to the fresh context, so the clear costs nothing.

No hook in Claude Code 2.1.220 can clear the context itself - the hook output schema is a closed union and
none of its branches touch the conversation. Step 5 is what makes step 4 safe to do by hand.

Fail-safe silent, capped at 2 blocks per ask so a stubborn turn can never trap the owner in a loop, and
gated by the JOB_CYCLE_LIVE file (delete it to switch the whole cycle off without editing settings.json).
"""
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import job_common as J  # noqa: E402

MAX_BLOCKS = 2

REASON = """\
Job done - now close it out before you stop. The owner's standing rule (2026-07-26): every finished job
gets a readme, filed in the brain, so the context can be cleared without losing what happened.

1. Write the readme body to a temp file. Cover, in his voice, briefly:
   - What you asked        (his words)
   - What I did            (the actual moves, not a plan)
   - What changed on disk  (path - one line each; say "nothing" if nothing)
   - What is true now      (only what a tool actually showed you - no claimed greens)
   - Next / open           (what you would pick up first)
2. File it:
     python "%(cli)s" write --title "<short title>" --body-file "<temp file>"
   Add --remember only if this job taught something durable that belongs in the memory corpus.
3. Then write your normal final report to the owner, ending with the one-line restatement of which
   task this was (his words + the clock time), and tell him the readme is filed and he can /clear.

Do not re-do the job. Do not re-verify what you already verified. Just file it and report.
%(tools)s"""


def main():
    if not J.live():
        return 0
    payload = J.read_payload()
    if payload is None:
        return 0

    session = payload.get("session_id") or ""
    transcript = payload.get("transcript_path") or ""

    # PER-SESSION PAUSE, owner 2026-08-03: "can you stop the hook just for yourslef but not
    # the other agents so we can talk?". Honours the SAME file the trading wake hook checks,
    # so one switch quiets both for ONE session while the rest of the fleet keeps its readme
    # discipline. Deliberately NOT the JOB_CYCLE_LIVE gate, which would disable the cycle for
    # everybody. Delete ~/.claude/trading_wake.off.<session_id> to re-arm that session.
    if session and os.path.exists(os.path.join(
            os.path.expanduser("~"), ".claude", "trading_wake.off." + session)):
        return 0

    info = J.scan(transcript)
    current = {
        "session": session,
        "transcript": transcript,
        "cwd": payload.get("cwd") or "",
        "ask": info["ask"][:2000],
        "ask_ts": info["ask_ts"],
        "tools": info["tools"],
        "is_job": info["is_job"],
    }
    # The whole structural fingerprint, not just the pid: which claude.exe (pid + creation time, so a
    # recycled number cannot impersonate it) and which terminal shell owns the lane.
    current.update(J.P.identity())
    J.write_current(current)

    if not info["is_job"]:
        return 0

    key = "%s:%.0f" % (session, info["ask_ts"])

    if J.covered(session, info["ask_ts"]):
        st = J.filed_state(session)
        # "the NEXT SESSION" and not "the next context", deliberately: /clear ends this session and starts
        # a new one with a new id, so the handover crosses a session boundary. The pointer is what carries
        # it; say whether one is actually waiting rather than promising the delivery blind.
        ready = os.path.exists(J.pointer_file(session))
        # Say what will actually happen, not what would be nice. There is no longer any time-based
        # fallback: no pointer means the next session is handed nothing and is told so. Promising a
        # delivery that cannot happen is how a handover failure stays invisible until it matters.
        msg = ("job readme filed -> %s\n%s\n"
               "Context is carrying this whole job. Press /clear - it ends this session, and %s"
               % (st.get("slug", "?"), st.get("path", J.LAST_JOB),
                  "the new one is handed this readme by the handover pointer waiting for it "
                  "(matched on this claude.exe, not on timing)." if ready
                  else "NO handover pointer is on file - the new session will be told that plainly "
                       "rather than served another agent's readme. Re-file if that is wrong."))
        sys.stdout.write(json.dumps({"systemMessage": msg}))
        return 0

    if J.block_count(key) >= MAX_BLOCKS:
        return 0

    J.bump_block(key)
    tools = ", ".join("%s x%d" % (k, v) for k, v in sorted(info["tools"].items(),
                                                           key=lambda kv: -kv[1])[:8])
    reason = REASON % {"cli": os.path.join(J.HERE, "job_readme.py"),
                       "tools": ("\nThis turn ran: " + tools) if tools else ""}
    sys.stdout.write(json.dumps({"decision": "block", "reason": reason}))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
