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
        raw = sys.stdin.buffer.read().decode("utf-8", "replace")
        i = raw.find("{")
        p = json.loads(raw[i:]) if i >= 0 else {}
    except Exception:
        p = {}
    if p.get("agent_id") or p.get("agent_type"):   # a subagent: its parent gets the message
        return
    sid = str(p.get("session_id") or "")
    if not sid:
        return
    import fleet_board as F
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
