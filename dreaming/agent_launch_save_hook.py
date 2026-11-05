# -*- coding: utf-8 -*-
"""Save the work of every agent you start (9 Oct 2026).

The rule: whoever starts an agent makes sure the agent's work is saved, including when the agent is stopped early;
work that is not saved was not worth starting. That night four Codex stages were cut off for running long and one of them took a 20-minute merge with
it; two disk fills lost audit rows mid-write; a test runner died on a flush and its half-run arms had to be run again.

PreToolUse, Claude sessions (hook_dispatch_list.json): fires on the Agent, Task and Workflow tools every time, and on a
Bash or PowerShell command that starts an agent (codex exec, claude -p, claude_pool.py run, a four_arm live run, a pool,
runner or driver, launch_detached, Start-Process of codex or claude) at most once per ten minutes per session. It never
blocks; it hands the launcher four checks.

    python agent_launch_save_hook.py --selftest
"""
import sys
if __name__ == "__main__" and ("-h" in sys.argv[1:] or "--help" in sys.argv[1:]):
    print(__doc__)
    sys.exit(0)
import io
import json
import os
import re
import sys
import time

STATE = os.path.join(os.path.expanduser("~"), ".claude", "dreaming", "state", "agent_launch_save")
QUIET_S = 600
# 9 Oct 01:1x: only a launcher in COMMAND position counts - at the start of the command or after ; & | ( or a newline,
# behind optional timeout N and VAR=value prefixes, and a script only as python's (or pythonw's) own argument. The first
# version fired on a sed command that merely named drive_v2.py inside its quoted text.
_AT = r"(?:^|[;&|(]\s*|\n\s*)(?:timeout\s+\d+\s+)?(?:\w+=\S*\s+)*"
_PY = _AT + r"[\w:/\\.\"'-]*?python[w3]?(?:\.exe)?[\"']?\s+(?:-[XWm]\s+\S+\s+|-\S+\s+)*[\"']?[^\s;&|\"']*?"
LAUNCH = re.compile(
    _AT + r"[\w:/\\.\"'-]*?codex(?:\.exe|\.cmd)?[\"']?\s+(?:exec|e)\b"
    + r"|" + _AT + r"[\w:/\\.\"'-]*?claude(?:\.exe|\.cmd)?[\"']?\s(?:[^\n|;&]*\s)?(?:-p|--print)\b"
    + r"|" + _PY + r"(?:claude_pool\.py\s+run\b|four_arm\.py\b[^\n;&|]*--mode\s+live|launch_detached\.py"
    r"|brain_workers[\\/]+pool\.py|claude_workers[\\/]+runner\.py|drive_(?:shard|v\d+b?)\.py|codex_writer\.py)"
    + r"|" + _AT + r"Start-Process\b[^\n;]*\b(?:codex|claude)\b", re.I)
TEXT = ("[save-agent-work] You are starting an agent. His order, 9 Oct 00:1x: \"make sure that that agents work is saved "
        "otherwise there is no point\". Before it runs: (1) it writes AS IT GOES to a durable place you name - result rows "
        "appended per finding, a STATUS.md with a NEXT line - never only its final message and never only a scratchpad; "
        "(2) its session or thread id is recorded so a crash or a stop resumes it, not restarts it; (3) you know where its "
        "output lands and who reads it next (a merge, a landing route, your own next step) and you will read it; (4) if it "
        "can be stopped - a time cap, a usage floor, a full disk - the stop leaves a receipt of what was half done "
        "(brain_workers/park_index.py and PARKED_INDEX.md do this for the Codex pool). An agent whose work is not saved "
        "spent the week for nothing.")


def decide(payload, now=None, state_dir=STATE):
    now = time.time() if now is None else now
    tool = str(payload.get("tool_name") or "")
    event = payload.get("hook_event_name") or "PreToolUse"
    if tool in ("Agent", "Task", "Workflow"):
        return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": TEXT}}
    if tool not in ("Bash", "PowerShell"):
        return {}
    cmd = str((payload.get("tool_input") or {}).get("command") or "")
    if not LAUNCH.search(cmd):
        return {}
    session = "".join(ch for ch in str(payload.get("session_id") or "nosession") if ch.isalnum() or ch in "-_")[:80]
    os.makedirs(state_dir, exist_ok=True)
    p = os.path.join(state_dir, (session or "nosession") + ".json")
    try:
        last = json.load(io.open(p, encoding="utf-8")).get("last")
    except (OSError, ValueError):
        last = None                      # never fired in this session
    if last is not None and now - last < QUIET_S:
        return {}
    try:
        tmp = p + ".%d.tmp" % os.getpid()
        io.open(tmp, "w", encoding="utf-8").write(json.dumps({"last": now}))
        os.replace(tmp, p)
    except OSError:
        pass
    return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": TEXT}}


def selftest():
    import tempfile
    d = tempfile.mkdtemp()
    fired = lambda out: "additionalContext" in out.get("hookSpecificOutput", {})
    assert fired(decide({"tool_name": "Agent", "session_id": "t1"}, now=0, state_dir=d)), "Agent tool fires"
    assert fired(decide({"tool_name": "Workflow", "session_id": "t1"}, now=1, state_dir=d)), "Workflow fires every time"
    assert not decide({"tool_name": "Bash", "session_id": "t1", "tool_input": {"command": "ls -la"}}, now=2, state_dir=d)
    assert not decide({"tool_name": "Edit", "session_id": "t1", "tool_input": {}}, now=3, state_dir=d)
    c = {"command": "codex exec --model gpt-6.1-sol -C work -"}
    assert fired(decide({"tool_name": "Bash", "session_id": "t2", "tool_input": c}, now=10, state_dir=d)), "codex exec fires"
    assert not decide({"tool_name": "Bash", "session_id": "t2", "tool_input": c}, now=200, state_dir=d), "quiet ten minutes"
    assert fired(decide({"tool_name": "Bash", "session_id": "t2", "tool_input": c}, now=700, state_dir=d)), "fires again after"
    for cmd in ("claude -p 'audit this' --model claude-opus-5-5", "python claude_pool.py run --account claude3",
                "python four_arm.py --mode live --runtime codex", "python launch_detached.py log -- python x.py",
                "Start-Process -WindowStyle Hidden codex", "python ~/pd_work/pilot_v2/drive_v2.py p0"):
        assert fired(decide({"tool_name": "PowerShell", "session_id": cmd[:20], "tool_input": {"command": cmd}}, now=0,
                            state_dir=d)), cmd
    for cmd in ("timeout 900 python -X utf8 ~/pd_work/pilot_v2/drive_v2.py p0",
                "cd /c/x && python launch_detached.py log -- python y.py", "PYTHONIOENCODING=utf-8 python four_arm.py --mode live",
                "C:/Python314/python.exe -X utf8 ~/brain_workers/pool.py --hours 72"):
        assert fired(decide({"tool_name": "Bash", "session_id": "p" + cmd[:12], "tool_input": {"command": cmd}}, now=0,
                            state_dir=d)), cmd
    for cmd in ("git log --oneline", "python park_index.py", "cat claude_workers/README.md", "python grade_v2.py",
                "sed -i 's/drive_v2.py, grade_v2.py/x/' push_0035.py", "grep -n \"codex exec\" pool.py",
                "echo \"claude -p is how it starts\" > /dev/null", "grep -c launch_detached.py notes.md"):
        assert not decide({"tool_name": "Bash", "session_id": "s" + cmd[:9], "tool_input": {"command": cmd}}, now=0,
                          state_dir=d), cmd
    print("selftest ok: Agent/Task/Workflow fire every time; agent-launching commands fire once per ten minutes; "
          "ordinary commands stay silent")


def main():
    if "--selftest" in sys.argv:
        selftest()
        return 0
    try:
        raw = sys.stdin.read(512 * 1024)
        out = decide(json.loads(raw) if raw.strip() else {})
        if out:
            sys.stdout.write(json.dumps(out))
    except Exception:  # noqa: BLE001  (a hook never blocks a session)
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
