# -*- coding: utf-8 -*-
"""Stop hook: the first time a session tries to stop after a compaction, send it back to its open work.

Design goal (16 Oct 2026): after a compaction an agent keeps working on its open tasks. Seen the same
night: after a compaction an agent writes a recap of where it stands and ends the turn, although its tasks are still open
and nothing is waiting on the user. The SessionStart notes (CP17, CP39, CP40) only
add context; they cannot make the agent take another turn. A Stop hook that answers {"decision": "block"} can.

Rules: once per compaction (keyed by the compact_boundary record's uuid), never while stop_hook_active (no loops), only
when the compaction is in the transcript's last 3 MB and under 6 hours old, and only when the checklist holds open or
working entries under this session's agent id. The reason tells the agent it may stop with one line when everything is
done or waiting on the user or a running job, so a finished agent pays one short turn, nothing more. Silent on any error.
Fires are logged to ~/.claude/brain_graph/compactions/keep_working_fires.jsonl."""
import sys as _sys
if __name__ == "__main__" and any(_a in ("-h", "--help") for _a in _sys.argv[1:]):
    # Asking for help must not run the script's real work.
    print((__doc__ or "").strip() or "usage: python " + _sys.argv[0])
    raise SystemExit(0)
import io
import json
import os
import sys
import time
from datetime import datetime, timezone
try:
    from shared_read import open_shared as _open_shared_ro
except Exception:
    def _open_shared_ro(path, binary=False):
        return io.open(path, "rb") if binary else io.open(path, encoding="utf-8")

HOME = os.path.expanduser("~")
STATE_DIR = os.path.join(HOME, ".claude", "compact_hooks", "keep_working")
FIRES = os.path.join(HOME, ".claude", "brain_graph", "compactions", "keep_working_fires.jsonl")
CHECKLIST = os.path.join(os.environ.get("TASK_CHECKLIST_HOME") or os.path.join(HOME, "brain-repo", "12_cognitive_architecture", "task_checklist"), "state.json")
TAIL = 3 * 1024 * 1024
MAX_AGE_S = 6 * 3600
OPEN = ("open", "working")


def last_boundary(path):
    """(uuid, epoch seconds) of the newest compact_boundary in the transcript's tail, or None."""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - TAIL))
            blob = fh.read()
    except OSError:
        return None
    for line in reversed(blob.split(b"\n")):
        if b'"compact_boundary"' not in line:
            continue
        try:
            rec = json.loads(line.decode("utf-8", "replace"))
        except ValueError:
            continue
        if rec.get("subtype") != "compact_boundary":
            continue
        ts = rec.get("timestamp") or ""
        try:
            at = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
        except ValueError:
            at = os.path.getmtime(path)
        return rec.get("uuid") or ts, at
    return None


def open_entries(sid):
    try:
        with _open_shared_ro(CHECKLIST) as fh:
            entries = json.load(fh).get("entries") or {}
    except (OSError, ValueError):
        return []
    agent = "claude-" + sid[:8]
    rows = [e for e in (entries.values() if isinstance(entries, dict) else entries)
            if isinstance(e, dict) and e.get("agent") == agent and e.get("status") in OPEN
            and sid.startswith(e.get("session") or sid[:8])]   # the 8-char agent id alone can match a twin session
    rows.sort(key=lambda e: e.get("asked_at") or "", reverse=True)
    return rows


def main():
    raw = sys.stdin.buffer.read().decode("utf-8", "replace")
    data = json.loads(raw) if raw.strip() else {}
    if data.get("stop_hook_active"):
        return
    sid = data.get("session_id") or ""
    tpath = data.get("transcript_path") or ""
    if not sid or not tpath:
        return
    b = last_boundary(tpath)
    if not b:
        return
    key, at = b
    if time.time() - at > MAX_AGE_S:
        return
    os.makedirs(STATE_DIR, exist_ok=True)
    sp = os.path.join(STATE_DIR, sid + ".json")
    try:
        done = json.load(io.open(sp, encoding="utf-8")).get("nudged") or []
    except (OSError, ValueError):
        done = []
    if key in done:
        return
    rows = open_entries(sid)
    # mark first: whatever happens below, this compaction is never nudged twice
    with io.open(sp, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"nudged": (done + [key])[-50:]}))
    if not rows:
        return
    listed = "; ".join("%s (%s) %s" % (e.get("id"), e.get("asked_at", "")[11:16],
                                      (e.get("working_on") or e.get("asked_words") or "")[:110]) for e in rows[:6])
    more = "" if len(rows) <= 6 else " and %d more" % (len(rows) - 6)
    reason = ("[keep-working] You compacted at %s and are about to stop. A recap is not the end of the job: these are still "
              "open under your id: %s%s. Pick up your NEXT line and keep working on them now. Stop only if every one is "
              "done (close it with checklist done), or is waiting on his answer or on a job that is already running and "
              "will notify you; then say which in one line." % (time.strftime("%H:%M", time.localtime(at)), listed, more))
    try:
        os.makedirs(os.path.dirname(FIRES), exist_ok=True)
        with io.open(FIRES, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "session_id": sid, "boundary": key,
                                 "open": [e.get("id") for e in rows]}) + "\n")
    except OSError:
        pass
    sys.stdout.write(json.dumps({"decision": "block", "reason": reason}))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
