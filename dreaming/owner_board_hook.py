"""owner_board_hook.py - hand every agent the owner's board for the scheduled tasks (his 2026-09-20 18:37 order:
"have a whole tasks thing in the brain that the schedules keep reading, that agents like you keep reading, and that
I can update").

Prints, once per session and then whenever the board changes or an hour passes, the STANDING ORDERS and the newest
message from 12_cognitive_architecture/scheduled_tasks/OWNER_MESSAGES.md. Claude gets plain text through the dispatcher;
Codex gets JSON (the only form it injects). Never blocks, never raises out; under BOARD_MAX chars.
"""
import io, json, os, re, sys, time

if any(a in ("-h", "--help") for a in sys.argv[1:]):
    # Discovery is not execution: this file does its work at module scope, so the
    # guard must come before the first statement that runs.
    print(__doc__ or os.path.basename(__file__))
    raise SystemExit(0)


BOARD = os.path.join(os.path.expanduser("~"), "brain-repo", "12_cognitive_architecture", "scheduled_tasks", "BOARD.md")
README = "~/brain-repo/12_cognitive_architecture/scheduled_tasks/README.md"
STATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".owner_board_state")
EVERY_S = 60 * 60
BOARD_MAX = 3500


def read_payload():
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", "replace")
    except Exception:
        return {}
    i = raw.find("{")
    try:
        return json.loads(raw[i:]) if i >= 0 else {}
    except Exception:
        return {}


def board_text():
    with io.open(BOARD, encoding="utf-8", errors="replace") as f:
        t = f.read()
    # BOARD.md is rendered by board_sync.py from the control README the tasks read: a "control:" line and the message
    control = ""
    m = re.search(r"^control: (.*)$", t, re.M)
    if m:
        control = m.group(1).strip()
    message = ""
    m2 = re.search(r"## OWNER MESSAGE\n(.*?)(?=\n## |\Z)", t, re.S)
    if m2:
        message = m2.group(1).strip()
    out = ["[owner-board] the owner message to the scheduled tasks and every agent (the control README the tasks read; his newest words win over an earlier task). Roster, protocol and how to answer: %s" % README]
    if control:
        out.append("  control: " + control)
    if message:
        out.append("  his message: " + " ".join(message.split()))
    text = "\n".join(out)
    return text if len(text) <= BOARD_MAX else text[:BOARD_MAX - 40] + " ... (cut; read the file)"


def main():
    if "--hook" not in sys.argv[1:]:
        print(board_text())
        return 0
    runtime = "codex" if ("codex" in sys.argv[1:] or os.environ.get("CODEX_THREAD_ID")) else "claude"
    try:
        payload = read_payload()
        sess = str(payload.get("session_id") or payload.get("thread_id") or "")[:16] or "nosession"
        event = str(payload.get("hook_event_name") or "UserPromptSubmit")
        if event not in ("UserPromptSubmit", "SessionStart"):
            return 0
        if not os.path.exists(BOARD):
            return 0
        mtime = os.path.getmtime(BOARD)
        os.makedirs(STATE_DIR, exist_ok=True)
        sp = os.path.join(STATE_DIR, re.sub(r"[^A-Za-z0-9_.-]", "_", sess) + ".json")
        st = {}
        try:
            with io.open(sp, encoding="utf-8") as f:
                st = json.load(f)
        except Exception:
            st = {}
        now = time.time()
        if st.get("board_mtime") == mtime and now - float(st.get("at") or 0) < EVERY_S:
            return 0
        text = board_text()
        with io.open(sp, "w", encoding="utf-8") as f:
            json.dump({"board_mtime": mtime, "at": now}, f)
        if runtime == "codex":
            sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}))
        else:
            sys.stdout.buffer.write((text + "\n").encode("utf-8", "replace"))
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
