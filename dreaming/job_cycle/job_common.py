#!/usr/bin/env python3
"""job_common.py - shared bones of the job cycle (readme -> brain -> clear -> reseed).

Owner's ask, 2026-07-26: "when AI has too much context it starts making too many mistakes. I want the
brain to start clearing context after every job done, so that every single time a task is done the AI
should write a readme for what it did, put it in the brain, clear everything, and then tell me what it
did."

The one honest constraint: in Claude Code 2.1.220 NO hook can clear the context. The hook JSON schema is
a closed union (hookEventName -> {additionalContext} for Stop; permissionDecision for PreToolUse; ...),
and the only real clear paths are the user typing /clear, the ExitPlanMode "Yes, clear context" option
(which calls clearConversation internally), and auto-compact at threshold. So this module automates the
three steps that CAN be automated - write the readme, file it in the brain, hand the next context the
readme - and reduces the clear itself to one keypress that loses nothing.

Everything here is fail-safe silent: a hook that raises must never cost the owner a turn.
"""
import io
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import job_proc as P  # noqa: E402  structural identity: which process, which terminal
# JOB_CYCLE_STATE: test seam (mirrors WORK_TIMER_STATE / RESUME_STATE_DIR). A fixture run without it
# overwrites the live LAST_JOB.md - the exact class of accident that cost the fleet its RESUME.md on 07-09.
STATE = os.environ.get("JOB_CYCLE_STATE", os.path.join(HERE, "state"))
LOGDIR = os.path.join(STATE, "log")
# LAST_JOB is the newest readme from ANY session. PERSESSION holds one per session id.
# Both, because this machine runs many agents at once: within minutes of arming the cycle, a second live
# session filed its readme and took over LAST_JOB.md, so a /clear here would have been handed its handover
# instead of mine. The reseed prefers the caller's own session file and only falls back to the global one,
# saying so out loud when it does.
LAST_JOB = os.path.join(STATE, "LAST_JOB.md")
PERSESSION = os.path.join(STATE, "last")
CURRENT = os.path.join(STATE, "current.json")
CURRENTDIR = os.path.join(STATE, "current")
BLOCKS = os.path.join(STATE, "blocks.json")
# PENDING holds one handover pointer per session that has filed a readme and not yet been cleared.
# It exists because of what /clear actually does, measured 2026-07-26 from the transcripts: it does NOT
# empty a running session, it ENDS the session and starts a new one with a new id and a new file
# (c52f5946 stops 16:07:59Z after 819 lines, 5a003764 opens 16:08:27Z in its own). So the id the reseed
# hook is handed has never filed anything, session_file() can never hit on a clear, and the old code fell
# through to the machine-wide LAST_JOB.md every single time - back to the shared file the per-session key
# was written to replace. It looked fine only on timing: the predecessor filed 16s before it died and the
# nearest parallel agent 3.5 minutes earlier.
PENDING = os.path.join(STATE, "pending")
# No TTL, deliberately. Owner, 2026-07-26: "can you make it that this does not work with timing, it works
# with how the terminal are built?" A pointer is collected when the claude.exe that wrote it is gone, not
# when a clock runs out - see job_proc.process_alive(). A clear happens inside a live process, so a dead
# process's pointer is unclaimable by anyone, which is both tighter than six hours and unbounded in time.
# Absent -> the whole cycle is silent. Lets the owner kill it without a settings.json edit (house
# convention, same as graph_fire/REPORT_GUARDS_LIVE).
LIVE = os.path.join(HERE, "JOB_CYCLE_LIVE")

MAX_SCAN = 80 * 1024 * 1024

# A job is work worth a readme. Editing anything counts on its own; otherwise it takes a few real tool
# calls. Reading three files and answering a question is a conversation, not a job.
MUTATORS = {"Edit", "Write", "NotebookEdit", "MultiEdit"}
DELEGATORS = {"Task", "Agent", "Workflow"}
COUNTED = {"Bash", "PowerShell", "Read", "Grep", "Glob", "WebFetch", "WebSearch",
           "BashOutput", "TaskOutput", "Skill", "Artifact"}
BUSY_TOOLS = 4


def live():
    # Two off switches on purpose. The file kills the cycle machine-wide with an rm and no settings edit;
    # JOB_CYCLE_OFF=1 lets one long autonomous runner opt out of per-turn readmes without disarming it
    # for the owner's interactive sessions.
    if os.environ.get("JOB_CYCLE_OFF", "").strip() not in ("", "0", "false", "False"):
        return False
    return os.path.exists(LIVE)


def read_payload(stream=None):
    """Parse the hook payload from stdin, tolerating a UTF-8 BOM and trailing junk.

    Caught 2026-07-26, the hard way: a BOM-prefixed payload made json.load raise, the fail-safe except
    swallowed it, and the hook produced nothing at all. Registered, armed, and silently dead - with no
    error anywhere to notice. A hook whose only failure mode is invisible is worse than one that is off,
    so parsing is deliberately forgiving here while everything downstream stays strict.
    """
    st = stream or sys.stdin
    raw = b""
    try:
        raw = st.buffer.read()
    except Exception:
        try:
            raw = (st.read() or "").encode("utf-8", "replace")
        except Exception:
            return None
    if not raw:
        return None
    try:
        txt = raw.decode("utf-8-sig", "replace").strip()
    except Exception:
        return None
    if not txt:
        return None
    try:
        obj = json.loads(txt)
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


def _ensure(d):
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass


def _epoch(ts):
    if not ts:
        return 0.0
    try:
        s = str(ts).replace("Z", "+00:00")
        import datetime
        return datetime.datetime.fromisoformat(s).timestamp()
    except Exception:
        return 0.0


_SYSREM = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)
_LOCALCMD = re.compile(r"<local-command-[^>]*>.*?</local-command-[^>]*>", re.S)


def _user_text(msg):
    """Text of a genuine user turn, or "" if this is a tool result / pure system noise.

    Tool results arrive as type=user too; counting them as a new ask would reset the work tally on every
    single tool call and the hook would never see a job at all.
    """
    content = msg.get("content")
    if isinstance(content, str):
        txt = content
    elif isinstance(content, list):
        for b in content:
            if isinstance(b, dict) and b.get("type") == "tool_result":
                return ""
        txt = " ".join(b.get("text", "") for b in content
                       if isinstance(b, dict) and b.get("type") == "text")
    else:
        return ""
    txt = _SYSREM.sub(" ", txt)
    txt = _LOCALCMD.sub(" ", txt)
    return txt.strip()


def scan(transcript):
    """One streaming pass: the last genuine ask, when it landed, and the tools run since.

    Returns dict(ask, ask_ts, tools{name:n}, n_tools, is_job).
    """
    out = {"ask": "", "ask_ts": 0.0, "tools": {}, "n_tools": 0, "is_job": False}
    if not transcript or not os.path.exists(transcript):
        return out
    try:
        with open(transcript, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            start = max(0, size - MAX_SCAN)
            f.seek(start)
            buf = f.read()
        if start > 0:
            nl = buf.find(b"\n")
            if nl != -1:
                buf = buf[nl + 1:]
    except Exception:
        return out

    ask, ask_ts, tools = "", 0.0, {}
    for ln in buf.split(b"\n"):
        if not ln.strip():
            continue
        try:
            obj = json.loads(ln.decode("utf-8", "replace"))
        except Exception:
            continue
        if not isinstance(obj, dict):
            continue
        typ = obj.get("type")
        msg = obj.get("message") if isinstance(obj.get("message"), dict) else {}
        role = msg.get("role") or typ
        if role == "user":
            txt = _user_text(msg if msg else obj)
            if txt:
                # A new ask: the previous job's tool tally is closed out.
                ask, ask_ts, tools = txt, _epoch(obj.get("timestamp")), {}
        elif role == "assistant":
            content = msg.get("content") or []
            if isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_use":
                        n = b.get("name") or "?"
                        tools[n] = tools.get(n, 0) + 1

    n = sum(tools.values())
    counted = sum(v for k, v in tools.items() if k in COUNTED or k in MUTATORS or k in DELEGATORS)
    out.update(ask=ask, ask_ts=ask_ts, tools=tools, n_tools=n)
    out["is_job"] = bool(set(tools) & MUTATORS) or bool(set(tools) & DELEGATORS) or counted >= BUSY_TOOLS
    return out


def session_file(session):
    """Where this session's own readme lives. Sanitised: a session id reaches us from a payload."""
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", str(session or "unknown"))[:120]
    return os.path.join(PERSESSION, safe + ".md")


def _safe(s, n=120):
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(s or ""))[:n]


def session_id():
    """The session id of the process actually running this, from its own environment.

    Caught the hard way 2026-07-26, by the pid field added an hour earlier: job_readme.py resolved
    identity from the SHARED current.json, and between this session's Stop hook writing it and the CLI
    call reading it, a neighbouring session's Stop hook overwrote it. The readme filed under 05bac1b4 -
    the novel session - clobbering ITS handover and leaving a pointer that claimed this session's pid for
    someone else's work. A cleared novel session would have been handed this job and told, with the
    confident pid wording, that it was its own predecessor.

    So identity comes from the environment, which is per-process and cannot be raced. CLAUDE_CODE_SESSION_ID
    is what claude actually exports; the old code looked for CLAUDE_SESSION_ID, which never existed, so the
    fallback it was counting on had never once fired.
    """
    for k in ("CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID"):
        v = (os.environ.get(k) or "").strip()
        if v:
            return v
    return ""


def claude_pid():
    """PID of the claude.exe that owns this session - the one id that survives a /clear.

    Claude Code exports CLAUDE_PID to every child process (observed in 2.1.220; walking the parent chain
    from a hook child lands on that exact pid as claude.exe). A clear resets React state in-process, so
    CLAUDE_CODE_SESSION_ID changes and CLAUDE_PID does not. That is what lets a freshly cleared session
    tell its OWN predecessor from the eleven other agents running on this machine.

    0 when absent, which is a real case worth degrading for rather than trusting: hooks are spawned by
    claude but nothing in the documented payload promises this variable.
    """
    v = (os.environ.get("CLAUDE_PID") or "").strip()
    return int(v) if v.isdigit() else 0


def project_key(transcript="", cwd=""):
    """Which project this session belongs to - the folder its transcript lives in.

    A clear keeps you in the same project; a parallel agent on the novel or the protected production stack is usually
    in another one. Weaker than the pid but it survives a missing CLAUDE_PID.
    """
    if transcript:
        d = os.path.basename(os.path.dirname(str(transcript)))
        if d:
            return _safe(d)
    return _safe(cwd) if cwd else ""


def pointer_file(session):
    return os.path.join(PENDING, _safe(session or "unknown") + ".json")


def _pointers():
    """[(path, record)] for every pointer on disk that still names a readme that exists."""
    out = []
    try:
        names = sorted(os.listdir(PENDING))
    except Exception:
        return out
    for fn in names:
        if not fn.endswith(".json"):
            continue
        p = os.path.join(PENDING, fn)
        d = _json_load(p, None)
        if isinstance(d, dict):
            out.append((p, d))
    return out


def prune_pointers(now=None):
    """Collect pointers whose claude.exe is gone. Returns how many went.

    Liveness, not age. The old rule dropped anything older than six hours, which is the wrong question
    twice: a pointer whose process is still running is still claimable however old it is, and a pointer
    whose process died two minutes ago is already garbage. `now` is accepted and ignored so existing
    callers and tests keep working.
    """
    gone = 0
    tbl = P.table()
    for p, d in _pointers():
        if not P.process_alive(d, tbl):
            try:
                os.remove(p)
                gone += 1
            except Exception:
                pass
    return gone


def write_pointer(session, readme_path, transcript="", cwd="", title=""):
    """Leave the next context a note saying where this session's readme is, and which lane left it.

    The record carries the structural fingerprint from job_proc.identity() - the claude.exe pid AND its
    creation time, plus the host shell that owns the terminal tab. filed_at is written for the human
    reading the file; nothing matches on it.

    Any other pointer belonging to this same process is removed first. One live claude.exe can only have
    one predecessor to hand over, and enforcing that here means take_pointer() never needs a tie-break -
    which is where the old code reached for `newest()` and let a clock back in.
    """
    if not session or session == "unknown" or not readme_path:
        return None
    _ensure(PENDING)
    ident = P.identity()
    rec = {"session": session, "project": project_key(transcript, cwd), "readme": readme_path,
           "filed_at": time.time(), "title": (title or "")[:200]}
    rec.update(ident)
    p = pointer_file(session)
    for other, d in _pointers():
        if os.path.abspath(other) != os.path.abspath(p) and P.same_process(ident, d):
            try:
                os.remove(other)
            except Exception:
                pass
    _json_dump(p, rec)
    prune_pointers()
    return p if os.path.exists(p) else None


def take_pointer(session, transcript="", cwd="", consume=True, now=None):
    """Take the readme left by the session this one replaced. Returns (path, info) or (None, info).

    STRUCTURAL ONLY, per the owner 2026-07-26: "can you make it that this does not work with timing, it
    works with how the terminal are built?" Both rungs are exact identities read off the process tree, and
    there is no third rung:

      process   - the pointer's claude.exe IS my claude.exe: same pid AND same creation FILETIME, still
                  live. A /clear resets state inside one process, so this is the predecessor by definition.
      terminal  - same host shell. One powershell.exe per terminal tab (six live claude.exe on this box,
                  six distinct hosts), so this is still my lane after claude itself was relaunched in it.

    Anything else returns no handover with rule="absent". The two rungs that used to sit below - `recency`
    (newest readme anyone filed) and the machine-wide LAST_JOB.md - are gone. They turned "I cannot tell"
    into a confident answer, and the live test proved how thin that was: the nearest rival pointer landed
    in the same MINUTE as ours. A gate has three states and INCOMPLETE is not a pass; an empty handover the
    owner can see beats a plausible one belonging to another agent.

    `now` is accepted and ignored - kept so old callers do not break.
    """
    mine = str(session or "")
    tbl = P.table()
    ident = P.identity(tbl)
    prune_pointers()
    cands = []
    for p, d in _pointers():
        if str(d.get("session") or "") == mine:
            continue
        rp = d.get("readme") or ""
        if not rp or not os.path.exists(rp):
            continue
        d["_pointer"] = p
        cands.append(d)

    base = {"candidates": len(cands), "identity": ident}
    if not cands:
        return None, dict(base, rule="absent", why="no handover pointer is waiting")

    hits = [d for d in cands if P.same_process(ident, d)]
    rule = "process"
    if not hits:
        hits = [d for d in cands if P.same_terminal(ident, d)]
        rule = "terminal"
    if not hits:
        return None, dict(base, rule="absent",
                          why=("%d pointer(s) waiting, none from this claude.exe (pid %s) or this "
                               "terminal (host %s)" % (len(cands), ident.get("claude_pid") or "?",
                                                       ident.get("host_pid") or "?")))
    if len(hits) > 1:
        # write_pointer() clears same-process duplicates, so this should be unreachable. If it happens,
        # say so rather than invent a preference - a tie-break is exactly where a clock would sneak back in.
        return None, dict(base, rule="ambiguous",
                          why=("%d pointers claim this same lane (%s) - refusing to guess between them"
                               % (len(hits), ", ".join(str(d.get("session"))[:8] for d in hits))))

    pick = hits[0]
    info = dict(base, rule=rule, session=pick.get("session"), filed_at=pick.get("filed_at"),
                title=pick.get("title") or "", matched_pid=pick.get("claude_pid"),
                matched_host=pick.get("host_pid"))
    if consume:
        try:
            os.remove(pick["_pointer"])
        except Exception:
            pass
    return pick.get("readme"), info


def filed_state(session=None):
    """What the readme on file says about itself: which session filed it and when.

    Reads THIS session's own copy when it exists. Reading the shared LAST_JOB.md instead would let any
    other agent's readme decide whether this session is done - and with a dozen sessions running here,
    the shared file changes hands every few minutes.
    """
    path = None
    if session:
        p = session_file(session)
        if os.path.exists(p):
            path = p
    if path is None:
        path = LAST_JOB
    if not os.path.exists(path):
        return {}
    try:
        head = io.open(path, encoding="utf-8").read(2048)
    except Exception:
        return {}
    st = {"mtime": os.path.getmtime(path), "path": path}
    m = re.search(r"^session:\s*(\S+)", head, re.M)
    if m:
        st["session"] = m.group(1)
    m = re.search(r"^filed_at:\s*([0-9.]+)", head, re.M)
    if m:
        try:
            st["filed_at"] = float(m.group(1))
        except Exception:
            pass
    m = re.search(r"^slug:\s*(\S+)", head, re.M)
    if m:
        st["slug"] = m.group(1)
    return st


def covered(session, ask_ts):
    """True if the readme on disk was filed by THIS session AFTER the current ask landed.

    Timestamp beats a key handshake here: it works whether the model files proactively or only after the
    hook asks, and a readme from the previous job can never be mistaken for this one's.
    """
    st = filed_state(session)
    if not st or st.get("session") != session:
        return False
    when = st.get("filed_at") or st.get("mtime") or 0.0
    return when >= (ask_ts or 0.0) - 1.0


def _json_load(p, default):
    try:
        return json.load(io.open(p, encoding="utf-8"))
    except Exception:
        return default


def _json_dump(p, obj):
    _ensure(os.path.dirname(p))
    try:
        tmp = p + ".tmp"
        with io.open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f)
        os.replace(tmp, p)
    except Exception:
        pass


def block_count(key):
    st = _json_load(BLOCKS, {})
    return int(st.get(key, 0))


def bump_block(key):
    now = time.time()
    st = _json_load(BLOCKS, {})
    st = {k: v for k, v in st.items() if isinstance(v, (int, float))}
    st[key] = int(st.get(key, 0)) + 1
    st["_ts"] = now
    _json_dump(BLOCKS, st)


def current_file(session):
    return os.path.join(CURRENTDIR, _safe(session or "unknown") + ".json")


def write_current(payload):
    """Per session first, shared second. Same lesson as LAST_JOB.md, one level down: a shared file that
    answers 'what am I working on' gets overwritten by whichever agent stopped most recently."""
    s = (payload or {}).get("session") or ""
    if s:
        _ensure(CURRENTDIR)
        _json_dump(current_file(s), payload)
    _json_dump(CURRENT, payload)


def read_current(session=None):
    """This session's own record. The shared file is a fallback and is checked for identity by callers."""
    s = session or session_id()
    if s:
        d = _json_load(current_file(s), None)
        if isinstance(d, dict) and d:
            return d
    return _json_load(CURRENT, {})


def slugify(s, maxlen=60):
    s = re.sub(r"[^a-zA-Z0-9]+", "_", (s or "job").strip().lower()).strip("_")
    return (s[:maxlen].rstrip("_") or "job")
