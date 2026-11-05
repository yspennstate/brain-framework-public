# -*- coding: utf-8 -*-
"""SessionStart hook for source "compact": hand back the wake this session already received, without re-running it.

Design goal (16 Oct 2026): an agent must not stall after a compaction. Measured that night:
wake_brain.ps1 parts 1 and 2 re-ran on every compaction and took 390 s and 393 s for one session, 224 s / 162 s /
163 s for three others - PowerShell alone took 35 s to start on the loaded machine.
The agent sat silent the whole time, and on a 300k window with a ~160k floor it compacts every 15-25 minutes.

Startup, resume and clear still run the full wake_brain.ps1 (settings.json matcher "startup|resume|clear"). After a
compaction this prints the same session's own last emitted wake part (brain_health/wake_full/claude_<sha256(sid)>_partN
_emitted.txt, written by wake_brain.ps1), or, when the session has none, the newest one of any session from the last
12 hours. Silent on any error: a missing wake never blocks the session.
usage: wake_cached.py --part 1|2   (stdin: the hook JSON)"""
import glob
import hashlib
import json
import os
import sys
import time

DIR = os.path.join(os.path.expanduser("~"), ".claude", "brain_health", "wake_full")
MAX_AGE = 12 * 3600
CAP = 9600          # the wake's own per-part budget; the harness inlines at most 10,000 chars of one hook


def main():
    part = sys.argv[sys.argv.index("--part") + 1] if "--part" in sys.argv else "1"
    try:
        sid = json.loads(sys.stdin.read() or "{}").get("session_id", "")
    except ValueError:
        sid = ""
    path = None
    if sid:
        own = os.path.join(DIR, "claude_%s_part%s_emitted.txt" % (hashlib.sha256(sid.encode("utf-8")).hexdigest(), part))
        if os.path.exists(own):
            path = own
    if path is None:
        now = time.time()
        best = None
        for f in glob.glob(os.path.join(DIR, "claude_*_part%s_emitted.txt" % part)):
            try:
                m = os.path.getmtime(f)
            except OSError:
                continue
            if now - m < MAX_AGE and (best is None or m > best[0]):
                best = (m, f)
        path = best[1] if best else None
    if path is None:
        return
    body = open(path, encoding="utf-8", errors="replace").read()
    stamp = time.strftime("%m-%d %H:%M", time.localtime(os.path.getmtime(path)))
    head = "[wake, part %s: the copy this session received at %s, served again after the compaction instead of re-running " \
           "the wake (it took up to 6.5 min); the next startup or resume refreshes it]\n" % (part, stamp)
    text = (head + body)[:CAP]
    sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
