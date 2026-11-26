"""advisor_nudge.py - reminds every session to call its advisor model often.

UserPromptSubmit (every prompt, loop fires included): one line telling the session to call its advisor before
substantive work and before it declares anything done.
PostToolUse: every CHECK_EVERY tool calls, count the advisor calls in the session transcript; if none happened in the
last window, print a nudge with the count. Advisory only, never blocks, silent on any error.
Plain stdout by default (a hook dispatcher wraps it); --json for a hook wired straight into settings (a machine without a dispatcher).
--selftest runs the controls both ways.

Opt-in: silent unless the session has an advisor tool and you turn it on, by creating the file
~/.claude/advisor_nudge/ENABLED or setting ADVISOR_NUDGE=1. A stock install has no advisor tool, so it ships off.
"""
import json, os, sys, time

CHECK_EVERY = 12  # tool calls between transcript checks
STATE_DIR = os.path.join(os.path.expanduser("~"), ".claude", "advisor_nudge")
PROMPT_LINE = ("[advisor] Call your advisor tool often - before you commit to an approach, "
               "when stuck or results do not fit, and before you report anything done. On a loop, that is at least one "
               "advisor call on every tick that does real work. Save your result to disk first, then call it.")


def count_advisor(path):
    n = 0
    try:
        with open(path, "rb") as f:
            for line in f:
                if b'"advisor"' in line and b"server_tool_use" in line:
                    n += line.count(b'"name": "advisor"') + line.count(b'"name":"advisor"')
    except OSError:
        return None
    return n


def post_tool(data, state_dir=STATE_DIR):
    sid = str(data.get("session_id") or "nosession")[:64]
    tp = data.get("transcript_path") or ""
    os.makedirs(state_dir, exist_ok=True)
    sp = os.path.join(state_dir, sid + ".json")
    try:
        st = json.load(open(sp))
    except Exception:
        st = {"calls": 0, "adv": None, "t": time.time()}
    st["calls"] = st.get("calls", 0) + 1
    msg = ""
    if st["calls"] % CHECK_EVERY == 0 and tp:
        n = count_advisor(tp)
        if n is not None:
            if st.get("adv") is not None and n <= st["adv"]:
                msg = ("[advisor] %d tool calls since your last advisor call (%d advisor calls this session). Call the "
                       "advisor often: save what you have, then call advisor() now." % (CHECK_EVERY, n))
            st["adv"] = n
    tmp = sp + ".tmp"
    json.dump(st, open(tmp, "w"))
    os.replace(tmp, sp)
    return msg


def emit(ev, msg, as_json):
    if not msg:
        return
    if as_json:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": ev, "additionalContext": msg}}))
    else:
        print(msg)


def selftest():
    import tempfile
    d = tempfile.mkdtemp()
    tp = os.path.join(d, "t.jsonl")
    open(tp, "w").write('{"type":"assistant"}\n')
    data = {"session_id": "s1", "transcript_path": tp}
    outs = [post_tool(data, d) for _ in range(CHECK_EVERY * 2)]
    ok1 = outs[CHECK_EVERY - 1] == "" and outs[2 * CHECK_EVERY - 1].startswith("[advisor]")
    with open(tp, "a") as f:
        f.write('{"message":{"content":[{"type":"server_tool_use","name":"advisor","input":{}}]}}\n')
    outs = [post_tool(data, d) for _ in range(CHECK_EVERY)]
    ok2 = outs[-1] == ""  # an advisor call happened in the window: silent
    outs = [post_tool(data, d) for _ in range(CHECK_EVERY)]
    ok3 = outs[-1].startswith("[advisor]")  # none since: nudge
    print("selftest", "PASS" if ok1 and ok2 and ok3 else "FAIL", ok1, ok2, ok3)
    return 0 if ok1 and ok2 and ok3 else 1


def main():
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    as_json = "--json" in sys.argv
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except Exception:
        data = {}
    ev = data.get("hook_event_name") or ""
    if not (os.environ.get("ADVISOR_NUDGE") == "1" or os.path.exists(os.path.join(STATE_DIR, "ENABLED"))):
        return
    try:
        if ev == "UserPromptSubmit":
            emit(ev, PROMPT_LINE, as_json)
        elif ev == "PostToolUse":
            emit(ev, post_tool(data), as_json)
    except Exception:
        pass


if __name__ == "__main__":
    main()
