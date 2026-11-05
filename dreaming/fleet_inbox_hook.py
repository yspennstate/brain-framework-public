"""fleet_inbox_hook.py - put the user's fleet-wide messages in front of every Claude session, on every computer.

The messages live in one place: FLEET_INBOX.md in the board repository (FLEET_BOARD_REPO), posted with
`fleet_board.py broadcast`, so a message to the whole fleet reaches every agent quickly and nobody wonders where to
look. This hook reads that file over the GitHub API at most once a minute per computer (a shared cache), and shows each
session every message it has not yet seen, once. SessionStart and UserPromptSubmit (headless sessions too); a new session sees the last 48 hours. Silent inside subagents.
Fail-open: no network, no gh token or any error prints nothing.
"""
import json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main():
    try:
        # in the dispatcher's process stdin is a StringIO with no .buffer (3 Nov)
        raw = sys.stdin.buffer.read().decode("utf-8", "replace") if hasattr(sys.stdin, "buffer") else sys.stdin.read()
        i = raw.find("{")
        p = json.loads(raw[i:]) if i >= 0 else {}
    except Exception:
        p = {}
    if p.get("agent_id") or p.get("agent_type"):   # a subagent: its parent gets the message
        return
    sid = str(p.get("session_id") or "")
    if not sid:
        return
    if "--midturn" in sys.argv:          # PostToolUse: answer messages sooner, so look within a
        import time                      # turn too, but at most once a minute per session
        # the stamp is checked before fleet_board is imported (urllib, subprocess, socket): 59 of 60 tool calls stop
        # here, and on 3 Nov they paid that import in a fresh process each time, 304 calls over 1 s, 20 at the 15 s cap
        state = os.path.join(os.path.expanduser("~"), ".claude", "fleet_board")   # = fleet_board.STATE
        stamp = os.path.join(state, "midturn_" + "".join(ch for ch in sid if ch.isalnum() or ch == "-"))
        try:
            if time.time() - os.path.getmtime(stamp) < 60:
                return
        except OSError:
            pass
        os.makedirs(state, exist_ok=True)
        open(stamp, "w").close()
    import fleet_board as F
    import time
    # Never fetch inline on a stale cache: gh auth token took 18 s under swap on 3 Nov, past the 15 s PostToolUse
    # budget mid-turn, and held the prompt up to 57 s (13 of 120 UserPromptSubmit runs over 5 s, 2-3 Nov). Refresh in
    # a detached process. Mid-turn, read the copy we have; on a prompt, give the refresh up to 5 s first (3 Nov:
    # a normal refresh took about 3 s here, so a 3 s cap mostly read the old copy).
    cp = os.path.join(F.STATE, "inbox_cache.json")
    try:
        stale = time.time() - json.load(open(cp, encoding="utf-8"))["t"] >= F.CACHE_S
        m0 = os.path.getmtime(cp)
    except Exception:
        stale, m0 = True, None           # no copy yet, or a broken one: the detached refresh makes it
    if stale:
        import subprocess
        p = subprocess.Popen([sys.executable, "-s", "-c", "import sys; sys.path.insert(0, %r); import fleet_board as F; "
                              "F.inbox_cached()" % os.path.dirname(os.path.abspath(F.__file__))],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
        if "--midturn" not in sys.argv:
            end = time.time() + 5
            while time.time() < end and p.poll() is None:   # a failed fetch exits without writing: stop waiting then
                time.sleep(0.1)
                try:
                    if os.path.getmtime(cp) != m0:   # the refresh replaced the copy (os.replace, so never half-written)
                        break
                except OSError:
                    pass

    # Read the copy only, never the network, even when it went stale a moment ago: the dispatcher runs this hook inside
    # its own process (3 Nov: 0.01 s there against 0.1-0.8 s as a subprocess, which also timed out at 15 s under swap),
    # and there no 15 s cap would stop an inline token + credential + API fetch (up to ~48 s).
    def cached_only():
        try:
            return json.load(open(cp, encoding="utf-8"))["text"]
        except Exception:
            return None
    F.inbox_cached = lambda *a, **k: cached_only()
    agent = "claude-" + sid[:8]
    msgs, sp, seen = F.unseen(agent)
    if not msgs:
        return
    fb = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fleet_board.py").replace(chr(92), "/")
    lines = ["[FLEET INBOX - the user's message to every agent on every computer; act on it, then ack: "
             "%s %s ack <id> --agent %s]" % (F.PY, fb, agent)]
    for m in msgs[:5]:
        lines.append("%s  %s  (%s)\n%s" % (m["id"], m["when"], m["sender"], m["text"][:2500]))
    if len(msgs) > 5:
        lines.append("... %d more: %s %s check --agent %s --all" % (len(msgs) - 5, F.PY, fb, agent))
    print("\n\n".join(lines))
    F.mark_seen(sp, seen, msgs)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
