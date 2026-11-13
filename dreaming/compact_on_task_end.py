# -*- coding: utf-8 -*-
"""Compact a session when it finishes a task (9 Oct 2026).

Why a hook cannot just call /compact: Claude Code takes /compact only as typed input. Its hook outputs are decision,
reason, stopReason, terminalSequence (OSC titles and bells only), additionalContext and the like; none compacts (read
from the 9 Oct binary). So on Stop, when the turn closed a task and the context is large, this hook starts a windowless
helper. The helper waits until that session is idle, then types "/compact" and Enter into that session's own console
(AttachConsole + WriteConsoleInputW). The keys go into that console's input buffer, never to the window that has the
focus. compact_nodes.py's PreCompact/PostCompact hooks then save the compaction as a node, as for an automatic one.

Guards: interactive claude.exe only (not -p/--print); context at least MIN_TOKENS; a task closed in this turn (a checklist
'done', or the end-of-task line "This was your ... ask"); once per COOLDOWN_S per session; the helper gives up if anyone
writes to the session while it waits (he may be typing) or it is not idle within WAIT_S; off while OFF_FLAG exists.
Idle means: the transcript quiet IDLE_S, the last turn ended and no queue-operation or loop fire since (a new turn shows
as a queue-operation before its user record is written), and no keyboard or mouse input for OWNER_STILL_S.

Proven 10 Oct on one session: the keys typed at 00:56:16 entered that session's queue (queue-operation enqueue
"/compact" at 21:56:16Z), and Claude Code ran /compact when the turn that had started 16 s earlier ended (22:03:02Z).
That turn was the reason for the 7-minute delay; the idle rule above now waits for such a turn to finish.

    hook (Stop, via hook_dispatch):  python -s compact_on_task_end.py          (reads the hook JSON on stdin)
    helper (started by the hook):    pythonw compact_on_task_end.py --inject PID TRANSCRIPT SESSION
    test the decision offline:       python compact_on_task_end.py --selftest
"""
import sys
if __name__ == "__main__" and ("-h" in sys.argv[1:] or "--help" in sys.argv[1:]):
    print(__doc__)
    sys.exit(0)
import json
import os
import re
import subprocess
import sys
import time

HOME = os.path.expanduser("~").replace("\\", "/")
DREAM = HOME + "/.claude/dreaming"
LOG_DIR = HOME + "/.claude/brain_graph/compactions"
LOG = LOG_DIR + "/task_end_compact.log"
STATE = LOG_DIR + "/task_end_compact_state.json"
OFF_FLAG = DREAM + "/COMPACT_ON_TASK_END_OFF"
MIN_TOKENS = 150000          # below this a compaction saves little; the 300k auto window covers the rest
COOLDOWN_S = 1200
IDLE_S = 8                   # the transcript must be quiet this long before the keys are typed
OWNER_STILL_S = 15           # and no keyboard or mouse input this long (he may be typing into this session's box)
WAIT_S = 900
DONE_RE = re.compile(r"(checklist\.py|task_checklist_hook\.py)\S*\s+done\s+T-\d{8}-\d+")
# "This was your 21:40 ask: ..." or, as written on 10 Oct 03:33, "This was your recurring film check (8 Oct 17:4x, ...)":
# the line names his ask or carries the clock time of it
END_LINE_RE = re.compile(r"This was your [^\n]{0,40}\bask\b|This was your [^\n]{0,80}?\b\d{1,2}:\d[\dx]\b", re.I)


def log(msg):
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except OSError:
        pass


def tail_records(path, nbytes=1500000):
    try:
        with open(path, "rb") as f:
            f.seek(max(0, os.path.getsize(path) - nbytes))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return []
    out = []
    for line in lines[1:] if len(lines) > 1 else lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def is_prompt(r, notifications=True):
    """A turn starts at a typed prompt (a user record with string content) or at a prompt queued while the agent worked
    (an attachment of type queued_command; tool results come back as lists). Task notifications wake a turn too."""
    if r.get("type") == "user" and isinstance((r.get("message") or {}).get("content"), str):
        # Stop-hook feedback and scheduled loop prompts are isMeta records: a turn boundary, but not him typing (the
        # 00:02 10 Oct helper for 5e550009 gave up 4 s in because the summary hook's feedback looked like his prompt)
        return notifications or not r.get("isMeta")
    a = r.get("attachment") or {}
    if r.get("type") == "attachment" and a.get("type") == "queued_command":
        return notifications or not str(a.get("prompt") or "").lstrip().startswith("<task-notification>")
    return False


def turn_start(r):
    """Where a turn begins for the question 'was a task closed in it': his prompt, a prompt queued while the agent worked,
    or a loop prompt. A task notification or the Stop hooks' own feedback continues the turn it lands in: on 10 Oct 03:33
    background-task notifications cut an agent's turn after its checklist 'done', and the hook saw no task closed."""
    if r.get("type") == "user":
        c = (r.get("message") or {}).get("content")
        if not isinstance(c, str):
            return False
        c = c.lstrip()
        return not (c.startswith("<task-notification>") or c.startswith("Stop hook feedback"))
    a = r.get("attachment") or {}
    if r.get("type") == "attachment" and a.get("type") == "queued_command":
        return not str(a.get("prompt") or "").lstrip().startswith("<task-notification>")
    return False


def turn_facts(records):
    """(context tokens at the last assistant message, a task closed in the turn since the last prompt)."""
    tokens, closed, start = 0, False, 0
    for i, r in enumerate(records):
        if turn_start(r):
            start = i
    for r in records[start:]:
        msg = r.get("message") or {}
        if r.get("type") == "assistant":
            u = msg.get("usage") or {}
            t = (u.get("input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0)
            tokens = t or tokens
            for c in msg.get("content") or []:
                # only a shell command that runs the checklist counts: a file that merely contains the words does not
                # (the first version fired on this very file's selftest text inside a Write call)
                cmd = str((c.get("input") or {}).get("command") or "") if c.get("type") == "tool_use" else ""
                # a heredoc or here-string that only carries the words (a patch, a test fixture) is not a close
                if c.get("name") in ("Bash", "PowerShell") and DONE_RE.search(cmd) and "<<" not in cmd and "@'" not in cmd:
                    closed = True
                if c.get("type") == "text" and END_LINE_RE.search(c.get("text") or ""):
                    closed = True
    return tokens, closed


def decide(records, session_id, now=None, state=None):
    now = now or time.time()
    state = state if state is not None else {}
    if os.path.exists(OFF_FLAG):
        return False, "off flag"
    tokens, closed = turn_facts(records)
    if not closed:
        return False, "no task closed this turn"
    if tokens < MIN_TOKENS:
        return False, "context %d under %d" % (tokens, MIN_TOKENS)
    if now - float(state.get(session_id, 0)) < COOLDOWN_S:
        return False, "cooldown"
    return True, "task closed at %d tokens" % tokens


def session_process():
    """The interactive claude.exe this hook runs under, or None. Claude Code hands every child its own process id
    (CLAUDE_PID), its entry point (cli) and whether a person attends it; the first version walked the parent chain
    instead and found no claude.exe from inside the Stop hook (23:34 and 23:39, 9 Oct), so the env comes first."""
    if os.environ.get("CLAUDE_CODE_ENTRYPOINT", "cli") != "cli":
        return None
    if os.environ.get("CLAUDE_CODE_SESSION_ATTENDED", "1").lower() in ("0", "false", "no"):
        return None
    pid = os.environ.get("CLAUDE_PID", "")
    # hooks run as "python -s": the user site (psutil) is not importable here, which is why the 23:4x-23:5x versions
    # found nothing (logged parents=ModuleNotFoundError); the check below uses the Windows API alone
    return int(pid) if pid.isdigit() and is_claude(int(pid)) else None


def is_claude(pid):
    """A live Claude Code process. A long-running session's binary is often renamed by an update while it runs
    (claude.exe.old.<stamp>.<pid>; 5e55000a's was on 9 Oct), so the name only has to start with claude.exe."""
    return os.path.basename(process_image(pid)).startswith("claude.exe")


def process_image(pid):
    """The full image path of a live process, lower-cased, or '' (OpenProcess + QueryFullProcessImageNameW: no psutil,
    and never os.kill, which on Windows terminates the process)."""
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = wintypes.HANDLE
    h = k32.OpenProcess(0x1000, False, int(pid))          # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        code = wintypes.DWORD(0)
        if not k32.GetExitCodeProcess(h, ctypes.byref(code)) or code.value != 259:   # STILL_ACTIVE
            return ""
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        if not k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return ""
        return buf.value.lower()
    finally:
        k32.CloseHandle(h)


def hook():
    if sys.platform != "win32":  # the console injection is Win32 only
        return 0
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", "replace")
    except AttributeError:  # in-process under hook_dispatch the stdin is a StringIO, already text
        raw = sys.stdin.read()
    try:
        data = json.loads(raw or "{}")
    except ValueError:
        return 0
    sid, path = data.get("session_id") or "", data.get("transcript_path") or ""
    if not sid or not path or data.get("stop_hook_active"):
        return 0
    try:
        state = json.load(open(STATE, encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    ok, why = decide(tail_records(path), sid, state=state)
    if not ok:
        return 0
    pid = session_process()
    if not pid:
        cp = os.environ.get("CLAUDE_PID", "")
        log("%s: %s, but no interactive claude.exe found (CLAUDE_PID=%r image=%r ENTRYPOINT=%r ATTENDED=%r); nothing typed"
            % (sid[:8], why, cp, process_image(int(cp)) if cp.isdigit() else "", os.environ.get("CLAUDE_CODE_ENTRYPOINT"),
               os.environ.get("CLAUDE_CODE_SESSION_ATTENDED")))
        return 0
    state[sid] = time.time()
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(STATE + ".tmp", "w", encoding="utf-8") as f:
            json.dump(state, f)
        os.replace(STATE + ".tmp", STATE)
    except OSError:
        pass
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    subprocess.Popen([pyw if os.path.exists(pyw) else sys.executable, os.path.abspath(__file__), "--inject", str(pid), path,
                      sid], creationflags=(0x08000000 | 0x00000008 | 0x00000200 if os.name == "nt" else 0), stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
    log("%s: %s; helper started for claude.exe %d" % (sid[:8], why, pid))
    print("[compact-on-task-end] a task closed at a large context: this session will compact itself once it is idle "
          "(typed /compact; the PreCompact hook keeps the open asks, ids and the NEXT line).")
    return 0


def type_into_console(pid, text):
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    u32 = ctypes.WinDLL("user32", use_last_error=True)

    class KEY(ctypes.Structure):
        _fields_ = [("bKeyDown", wintypes.BOOL), ("wRepeatCount", wintypes.WORD), ("wVirtualKeyCode", wintypes.WORD),
                    ("wVirtualScanCode", wintypes.WORD), ("uChar", wintypes.WCHAR), ("dwControlKeyState", wintypes.DWORD)]

    class EV(ctypes.Union):
        _fields_ = [("KeyEvent", KEY), ("pad", ctypes.c_byte * 16)]

    class REC(ctypes.Structure):
        _fields_ = [("EventType", wintypes.WORD), ("Event", EV)]

    def records(s):
        out = []
        for ch in s:
            vk = 0x0D if ch == "\r" else (u32.VkKeyScanW(ch) & 0xFF)
            sc = u32.MapVirtualKeyW(vk, 0)
            for down in (1, 0):
                r = REC()
                r.EventType = 1
                r.Event.KeyEvent = KEY(down, 1, vk, sc, ch, 0)
                out.append(r)
        return (REC * len(out))(*out), len(out)

    k32.FreeConsole()
    if not k32.AttachConsole(int(pid)):
        return "AttachConsole failed (%d)" % ctypes.get_last_error()
    try:
        k32.CreateFileW.restype = wintypes.HANDLE
        h = k32.CreateFileW("CONIN$", 0xC0000000, 3, None, 3, 0, None)
        if not h or h == wintypes.HANDLE(-1).value:
            return "CONIN$ failed (%d)" % ctypes.get_last_error()
        n = wintypes.DWORD(0)
        for chunk in (text, "\r"):                       # the Enter goes alone, so the text is not read as a paste
            arr, count = records(chunk)
            if not k32.WriteConsoleInputW(h, arr, count, ctypes.byref(n)):
                return "WriteConsoleInputW failed (%d)" % ctypes.get_last_error()
            time.sleep(0.5)
        k32.CloseHandle(h)
        return "typed"
    finally:
        k32.FreeConsole()


def idle_records(recs):
    """The last assistant message ended its turn and nothing has started a new one since. A new turn shows first as a
    queue-operation record (or a loop's scheduled_task_fire); its user record is written later, with the first reply.
    On 10 Oct 00:56 a loop prompt was dequeued at 21:56:00Z, its user record reached the file only after 21:56:16, and
    the keys typed at 21:56:16 were queued behind that turn: /compact ran at its end, 7 minutes late."""
    for r in reversed(recs):
        t = r.get("type")
        if t == "assistant":
            return (r.get("message") or {}).get("stop_reason") == "end_turn"
        if t in ("user", "queue-operation"):
            return False
        if t == "system" and r.get("subtype") == "scheduled_task_fire":
            return False
    return False


def owner_input_idle_s():
    """Seconds since the last keyboard or mouse input in this desktop session (keys we write to a console are not input
    here). He may be typing into the very box the keys would join, so nothing is typed until he has been still a while."""
    if os.name != "nt":
        import posix_proc
        return posix_proc.idle_seconds()
    import ctypes
    from ctypes import wintypes

    class LII(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]
    li = LII(ctypes.sizeof(LII), 0)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(li)):
        return 0.0
    return ((ctypes.windll.kernel32.GetTickCount() - li.dwTime) & 0xFFFFFFFF) / 1000.0


def idle(path):
    """Quiet for IDLE_S, the turn ended and no new one started, and no keyboard or mouse input for OWNER_STILL_S."""
    try:
        if time.time() - os.path.getmtime(path) < IDLE_S:
            return False
    except OSError:
        return False
    return idle_records(tail_records(path, 400000)) and owner_input_idle_s() >= OWNER_STILL_S


def inject(pid, path, sid):
    t0 = time.time()
    start_size = os.path.getsize(path) if os.path.exists(path) else 0
    prompts0 = sum(1 for r in tail_records(path) if is_prompt(r, notifications=False))
    while time.time() - t0 < WAIT_S:
        if not is_claude(int(pid)):
            log("%s: claude.exe %s ended; nothing typed" % (sid[:8], pid))
            return 0
        recs = tail_records(path)
        if sum(1 for r in recs if is_prompt(r, notifications=False)) > prompts0:
            log("%s: a new prompt arrived while waiting; left to him (no keys typed)" % sid[:8])
            return 0
        if any(r.get("type") == "system" and r.get("subtype") == "compact_boundary" for r in recs[-50:]) and \
                os.path.getsize(path) != start_size and idle(path):
            log("%s: the session compacted by itself; nothing typed" % sid[:8])
            return 0
        if idle(path):
            time.sleep(3)
            if idle(path):
                out = type_into_console(pid, "/compact")
                log("%s: /compact into claude.exe %s: %s" % (sid[:8], pid, out))
                return 0
        time.sleep(4)
    # Still busy after WAIT_S (a loop or the goal hook keeps the session working): typed now, /compact is queued and
    # Claude Code runs it when the turn ends, as it did on 10 Oct 00:56. Only while he has kept his hands off the
    # keyboard and mouse, so the keys cannot join text he is typing.
    if not is_claude(int(pid)):
        log("%s: claude.exe %s ended; nothing typed" % (sid[:8], pid))
        return 0
    for _ in range(60):
        if owner_input_idle_s() >= OWNER_STILL_S:
            out = type_into_console(pid, "/compact")
            log("%s: still busy after %d s; /compact queued into claude.exe %s: %s" % (sid[:8], WAIT_S, pid, out))
            return 0
        time.sleep(5)
    log("%s: not idle within %d s and he kept typing; nothing typed" % (sid[:8], WAIT_S))
    return 0


def selftest():
    global OFF_FLAG
    OFF_FLAG = OFF_FLAG + ".selftest_absent"                                # the logic is tested whether or not it is paused

    def turn(done=True, end_line=False, tokens=400000):
        recs = [{"type": "user", "message": {"content": "do the thing"}}]
        content = [{"type": "text", "text": "Done." + (" (This was your 21:40 ask: build it.)" if end_line else "")}]
        if done:
            content.append({"type": "tool_use", "name": "Bash",
                            "input": {"command": "python checklist.py done T-20990101-128 \"ok\""}})
        recs.append({"type": "assistant", "message": {"content": content, "stop_reason": "end_turn",
                                                       "usage": {"input_tokens": 10, "cache_read_input_tokens": tokens}}})
        return recs
    cases = [(turn(), True), (turn(done=False), False), (turn(done=False, end_line=True), True),
             (turn(tokens=40000), False)]
    ok = all(decide(r, "s", state={})[0] == want for r, want in cases)
    ok &= decide(turn(), "s", state={"s": time.time()})[0] is False          # cooldown
    later = turn() + [{"type": "user", "message": {"content": "new ask"}}]    # a later prompt opens a new turn
    ok &= decide(later, "s", state={})[0] is False
    written = turn() ; written[1]["message"]["content"] = [{"type": "tool_use", "name": "Write",
                       "input": {"content": "python checklist.py done T-20990101-128"}}]
    ok &= decide(written, "s", state={})[0] is False                         # a file's text is not a closed task
    queued = turn() + [{"type": "attachment", "attachment": {"type": "queued_command", "prompt": "next ask"}}]
    ok &= decide(queued, "s", state={})[0] is False                          # a queued prompt opens a new turn
    ended = turn() + [{"type": "system", "subtype": "stop_hook_summary"}, {"type": "system", "subtype": "turn_duration"}]
    ok &= idle_records(ended) is True                                        # a finished turn is idle
    ok &= idle_records(ended + [{"type": "queue-operation", "operation": "dequeue"}]) is False   # 10 Oct 00:56
    ok &= idle_records(ended + [{"type": "system", "subtype": "scheduled_task_fire"}]) is False
    ok &= idle_records(ended + [{"type": "user", "isMeta": True, "message": {"content": "Stop hook feedback"}}]) is False
    ok &= isinstance(owner_input_idle_s(), float)
    note = {"type": "user", "message": {"content": "<task-notification>\n<task-id>b1</task-id>\n</task-notification>"}}
    fb = {"type": "user", "isMeta": True, "message": {"content": "Stop hook feedback:\nSummary hook ..."}}
    after = {"type": "assistant", "message": {"content": [{"type": "text", "text": "Summary."}], "stop_reason": "end_turn",
                                              "usage": {"input_tokens": 10, "cache_read_input_tokens": 400000}}}
    ok &= decide(turn() + [note, after], "s", state={})[0] is True            # 10 Oct 03:33: a notification is not a turn
    ok &= decide(turn() + [fb, after], "s", state={})[0] is True              # nor is the summary hook's feedback
    film = turn(done=False)
    film[1]["message"]["content"][0]["text"] = "Done. This was your recurring film check (8 Oct 17:4x, 50 an hour)."
    ok &= decide(film, "s", state={})[0] is True                              # the end line names the ask's clock time
    film[1]["message"]["content"][0]["text"] = "Done. This was your favourite part of the run."
    ok &= decide(film, "s", state={})[0] is False                             # no ask, no clock time: not an end line
    print("selftest", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--inject":
        sys.exit(inject(sys.argv[2], sys.argv[3], sys.argv[4]))
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        sys.exit(selftest())
    try:
        sys.exit(hook())
    except Exception as e:  # noqa: BLE001 - never break a session's Stop
        log("hook error %r" % (e,))
        sys.exit(0)
