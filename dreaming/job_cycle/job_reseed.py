#!/usr/bin/env python3
"""job_reseed.py - SessionStart hook: hand a freshly cleared context the readme of the job it just forgot.

This is what makes /clear cheap. SessionStart fires with source in (startup, resume, clear, compact, fork);
on clear and compact the conversation is gone, so we inject the last job readme as additionalContext. The
owner presses one key, the context empties, and the next turn still knows what was just done and what was
left open - without carrying the transcript that made it stupid.

CLEAR AND COMPACT ARE NOT THE SAME EVENT, measured 2026-07-26 from the transcripts on disk:

  compact keeps the session. One id, one file, five compacts in transcript 1d40fc49 with a SessionStart
          injection immediately after each compact_boundary. So the session's own readme is the right one.
  clear   ENDS the session and starts a new one. c52f5946 stops at 16:07:59Z after 819 lines; 5a003764
          opens at 16:08:27Z in its own file with a new id. The id handed to this hook has therefore
          NEVER filed a readme, so looking one up by session id cannot hit, and the old code fell through
          to the machine-wide LAST_JOB.md every time - the shared file the per-session key exists to
          avoid. It only looked right because the predecessor filed 16 seconds before it died while the
          nearest parallel agent had filed 3.5 minutes earlier.

So a clear is a handover between two different sessions, and it needs a key that survives it. CLAUDE_PID
does: the clear resets React state inside one claude.exe, so the pid is identical on both sides while the
session id is not. take_pointer() matches on it and says out loud which rule fired, because the failure
that matters is being handed the novel session's handover and reporting it to the owner as continuity.

Capped, because a readme large enough to refill the context defeats the point. startup/resume are left
alone: the wake ritual and RESUME.md already cover those, and loading it twice is the same disease.
"""
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import job_common as J  # noqa: E402

CAP = 6000
RESEED_ON = ("clear", "compact")

# Both surviving rungs are exact structural identities, so neither needs a hedge - they only need to say
# WHICH structure matched, because the failure that matters here is silent: being handed another agent's
# handover and presenting it to the owner as continuity.
CAVEATS = {
    "own": "",
    "process": ("Handover from session %(session)s, matched on the claude.exe process itself - pid "
                "%(matched_pid)s and its creation time, still live. A /clear resets state inside one "
                "process, so this IS your own predecessor, the session you were just in.\n\n"),
    "terminal": ("Handover from session %(session)s, matched on the terminal that hosts this session - the "
                 "same shell (host pid %(matched_host)s) that launched claude - because the claude process "
                 "itself could not be confirmed (no creation time was recorded on one side). Almost "
                 "certainly your own predecessor, but it is the lane that was proved, not the process.\n\n"),
}

# There is no rung below those two on purpose. Nothing gets borrowed on time.
ABSENT = ("The context was just cleared. No handover was available for this session, so there is nothing "
          "here about what came before - do not go looking for continuity that was not handed to you, and "
          "ask rather than guess.\n\nWhy: %(why)s.\nThis is deliberate. The handover matches on the "
          "claude.exe process and the terminal that hosts it, never on which readme was filed most "
          "recently, because this machine runs many agents at once and the newest readme is routinely "
          "someone else's work.\n")


def clip(doc, path):
    """Cap the readme without throwing away the part a successor actually needs.

    Plain head-truncation keeps 'What you asked' and 'What I did' - the narrative of a job that is
    already over - and cuts 'Next / open', which is the only section that tells the next context what
    to DO. That is exactly backwards, and it is not hypothetical: measured 2026-07-26 over the 20
    readmes filed since this went live, 4 exceed CAP and the longest one - 73462600, the Keeper cuts -
    lost its Next/open in a real clear, so the successor was handed the history and not the handle.

    So reserve room for the tail and splice it back on. The head still leads, because it is what
    orients; the tail survives, because it is what continues.
    """
    if len(doc) <= CAP:
        return doc
    tail = ""
    i = doc.rfind("\n## Next / open")
    if i >= 0:
        tail = doc[i:].rstrip()
        if len(tail) > CAP // 2:
            # A runaway tail must not evict the head it is supposed to accompany.
            tail = tail[:CAP // 2].rsplit("\n", 1)[0] + "\n[...]"
    note = "\n\n[middle truncated - full copy: %s]\n" % path
    head = doc[:max(CAP - len(tail) - len(note), 0)].rsplit("\n", 1)[0]
    return head + note + (tail + "\n" if tail else "")


def pick(session, transcript, cwd, source):
    """(path, info) for the readme this context should be handed, or (None, info explaining why not)."""
    # A compact keeps the session id, so the session's own copy is exactly right and no handover is
    # involved - and must not be consumed, or a compact would eat the pointer a later clear needs.
    own = J.session_file(session)
    if os.path.exists(own):
        return own, {"rule": "own", "session": session}

    return J.take_pointer(session, transcript, cwd, consume=(source == "clear"))


def main():
    if not J.live():
        return 0
    payload = J.read_payload()
    if payload is None:
        return 0
    source = payload.get("source") or ""
    if source not in RESEED_ON:
        return 0

    session = payload.get("session_id") or ""
    transcript = payload.get("transcript_path") or ""
    cwd = payload.get("cwd") or ""

    path, info = pick(session, transcript, cwd, source)
    fields = {"session": info.get("session") or "?",
              "candidates": int(info.get("candidates") or 0),
              "matched_pid": info.get("matched_pid") or "?",
              "matched_host": info.get("matched_host") or "?",
              "why": info.get("why") or "no structural match"}

    doc = ""
    if path and os.path.exists(path):
        try:
            doc = io.open(path, encoding="utf-8", errors="replace").read()
        except Exception:
            doc = ""

    if not doc.strip():
        # No handover, and that is said out loud. Staying silent here would be indistinguishable from a
        # dead hook, and the old code's answer - inject the newest readme from any session on the machine -
        # is what the owner asked to remove: an unresolved match is not a licence to serve someone else's
        # work. Only on a clear; a compact with nothing filed is genuinely nothing to report.
        if source != "clear":
            return 0
        try:
            note = ABSENT % fields
        except Exception:
            note = ABSENT.split("Why:")[0]
        sys.stdout.write(json.dumps({"hookSpecificOutput": {
            "hookEventName": "SessionStart", "additionalContext": note}}))
        return 0

    doc = clip(doc, path)

    try:
        caveat = CAVEATS.get(info.get("rule") or "", "") % fields
    except Exception:
        caveat = ""

    ctx = ("The context was just cleared on purpose. This is the readme of the job that finished right "
           "before the clear - it is the whole handover, so do not go re-reading the old transcript.\n\n"
           "%s%s\n"
           "Job journal: %s   (python \"%s\" recall \"<query>\")\n"
           % (caveat, doc.rstrip(), J.LOGDIR, os.path.join(J.HERE, "job_readme.py")))

    sys.stdout.write(json.dumps({"hookSpecificOutput": {
        "hookEventName": "SessionStart", "additionalContext": ctx}}))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
