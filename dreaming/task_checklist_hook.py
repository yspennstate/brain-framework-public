#!/usr/bin/env python3
"""task_checklist_hook.py - the owner's checklist of what they asked every agent, kept by every agent.

Design note: an owner who runs many agents at once asks different agents different things, and two failures
follow. Agents work on one ask without knowing another agent holds a related one, and the owner forgets what was
asked of whom. The checklist is the shared answer: every ask the owner gives an agent becomes a dated entry under
that agent, the agent checks it off when it is done, and a second checkmark records that the owner has seen the
result. When the owner asks what the agents are doing, an agent answers from the list, not from memory.

One file, three faces:

  hook mode   (--hook, stdin = the harness payload; Claude through hook_dispatch, Codex through hooks.json)
      UserPromptSubmit : an ask from the owner becomes a dated entry under this session's agent, in the owner's
                         words; a short follow-up ("ok do it") is attached to the session's open entry;
                         a question about tasks ("what did i ask the agents?") prints the whole checklist
                         so the agent answers from it. Every registration also prints what OTHER agents
                         have open, so nobody works blind.
      Stop             : records the agent's last activity on its open entry; fills "working on" from the
                         agent mesh when the agent never stated it.
      SessionStart     : prints the digest - open entries across agents, what is done and not yet seen.
  cli mode    (add / working / note / done / blocked / drop / reopen / seen / list / show / render / digest)
      the commands agents and the owner use by hand.
  selftest    (--selftest) positive and negative controls on a temporary store.

The store is one folder in the brain tree, ~/brain-repo/12_cognitive_architecture/task_checklist (TASK_CHECKLIST_HOME overrides it):
      ledger.jsonl   append-only, every event with its time, agent, runtime and session
      state.json     the folded state, rewritten atomically under a lock
      CHECKLIST.md   the rendered view the owner reads: open / done-not-seen / seen, grouped by date

Two checkmarks per entry: [x] done (the agent's) and [x] seen (the owner's). Times are the owner's local clock.
Never blocks, never raises out: any error -> silent exit 0.
"""
import io
import json
import os
import re
import sys
import time

HOME = os.path.expanduser("~")
_BRAIN_TREE = os.path.join(HOME, "brain-repo", "12_cognitive_architecture", "task_checklist")
STORE = (os.environ.get("TASK_CHECKLIST_HOME")
         or (_BRAIN_TREE if os.path.isdir(os.path.dirname(_BRAIN_TREE)) else os.path.join(HOME, ".claude", "task_checklist")))
LEDGER = os.path.join(STORE, "ledger.jsonl")
STATE = os.path.join(STORE, "state.json")
VIEW = os.path.join(STORE, "CHECKLIST.md")
LOCK = os.path.join(STORE, ".lock")
LOG = os.path.join(STORE, "hook.log")
_LIVE_STORES = {os.path.normcase(os.path.abspath(p)) for p in (_BRAIN_TREE, os.path.join(HOME, ".claude", "task_checklist"))}
MESH_DB = os.path.join(HOME, ".agents", "coordination", "agent_mesh.sqlite3")

CLI = "python %s" % os.path.abspath(__file__).replace("\\", "/")
MIN_ASK_CHARS = 25
MAX_QUESTION_CHARS = 160      # longer than this and it is an ask that mentions the list, not a question about it
                              # (questions run 20-90 characters; asks 200-900)
DIGEST_MAX_OTHERS = 8
# Claude Code hands the model at most 10,000 chars of one hook command's output inline; above that it persists the
# text to a file and shows a 2 KB preview (measured 2026-09-20). The whole list is 290 KB, so the hook prints the
# top of it - open entries first - under this cap and names the file for the rest. The dispatcher merges this hook
# with the sense hooks on the same prompt, hence well under 10,000.
CONTEXT_CAP = 7000
# an automated delivery probe of a hook (this fleet's own test prompt) is never one of the owner's asks
PROBE_RX = re.compile(r"^\s*(\[probe\]|Automated delivery probe of a hook\b"
                      # a headless worker's batch prompt is the fleet's, not the owner's (one entry per picture batch)
                      # (any picture-maker brief: a worker's image brief once put seven entries on the owner's list)
                      r"|\[headless-worker\]|You are (?:a|the) picture maker\b"
                      # the harness's continuation turn after a usage-limit reset (one entry per session)
                      r"|Your claude\.ai usage limit has reset\b)", re.I)

QUESTION_RX = re.compile(
    r"(what (did|have) i (ask|tell|give)|what (are|is) (the |my |all the )?agents? (working|doing|on)"
    r"|what is everyone (working|doing)|\bchecklist\b|\btask list\b|list (of )?(my |the |open )?tasks"
    r"|what tasks|which agents|what('s| is) (still )?open|status of (the |my )?agents"
    r"|what have (i|you) been asked|open tasks|show (me )?(the )?(open )?tasks|what did i ask)", re.I)
INTERROGATIVE_RX = re.compile(
    r"^\W*(what|which|who|whom|whose|where|when|why|how|is|are|was|were|do|does|did|can|could|would|will|should|have|has"
    r"|show|list|tell me|give me|remind me|any (update|news)|status)\b", re.I)
SEEN_RX = re.compile(r"\b(mark|set|check)\b.{0,24}\bseen\b|\bi('ve| have) seen\b|\bseen (it|them|all|that|those)\b", re.I)
FOLLOWUP_RX = re.compile(
    r"^(ok(ay)?|yes|yep|ya|yeah|no|nope|sure|fine|good|great|perfect|thanks|thank you|go|go ahead|do it"
    r"|yes do it|continue|proceed|carry on|keep (working|going|auditing|at it|doing)|k|done|got it|right)\b[\s.,!]*(.{0,60})$", re.I)
_SYSREM = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)
_CMD_NAME = re.compile(r"<command-name>(.*?)</command-name>", re.S)
_CMD_ARGS = re.compile(r"<command-args>(.*?)</command-args>", re.S)
_CMD_STDOUT = re.compile(r"<local-command-stdout>.*?</local-command-stdout>", re.S)
_NOISE = ("<task-notification>", "[SYSTEM NOTIFICATION - NOT USER INPUT]", "<local-command-caveat>")
# <cross-session-message> is the form a peer's relay takes (six entries were once filed from one as the owner's
# asks under the receiving agents)
_MACHINE_TURN_RX = re.compile(r"^\s*(<agent-message\b|Another Claude session sent a message\b|<hook_prompt\b|<agent-notification\b"
                              r"|<cross-session-message\b)", re.I)


# ----------------------------------------------------------------------------- small utilities

def now_min():
    return time.strftime("%Y-%m-%d %H:%M")


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def log(msg):
    try:
        os.makedirs(STORE, exist_ok=True)
        with io.open(LOG, "a", encoding="utf-8") as fh:
            fh.write("[%s] %s\n" % (now_iso(), msg))
    except Exception:
        pass


class _Lock(object):
    """A lock file with a short wait; a lock older than 60 s is treated as abandoned."""

    def __enter__(self):
        os.makedirs(STORE, exist_ok=True)
        deadline = time.time() + 4.0
        while True:
            try:
                fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode("ascii"))
                os.close(fd)
                return self
            except FileExistsError:
                try:
                    if time.time() - os.path.getmtime(LOCK) > 60:
                        os.remove(LOCK)
                        continue
                except OSError:
                    pass
                if time.time() > deadline:
                    raise RuntimeError("checklist lock held too long")
                time.sleep(0.05)

    def __exit__(self, *a):
        try:
            os.remove(LOCK)
        except OSError:
            pass


def _backup_path():
    """The hourly copy forced to disk; see save_state. Read from STATE at call time: selftest() rebinds STATE to a temp
    store, and a path fixed at import let its 513-byte test state overwrite the live 15 MB copy (23 Oct 13:48)."""
    return STATE + ".hourly"


def _read_json(path):
    with io.open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _fallback_state():
    """state.json exists but does not parse. The 23 Oct 11:46 crash left it so; the old code went on with {} and the
    list lost 5,600 entries while the day's ids restarted at 001. Load the newest readable copy instead."""
    import glob
    cands = [p for p in [_backup_path()] + glob.glob(STATE + ".*.tmp") if os.path.exists(p) and os.path.getsize(p) > 1024]
    for p in sorted(cands, key=os.path.getmtime, reverse=True):
        try:
            st = _read_json(p)
            if isinstance(st, dict) and st.get("entries"):
                log("state.json unreadable; loaded %s (%d entries) - ledger events after %s are not in it, run "
                    "restore_after_crash.py" % (p, len(st["entries"]), time.ctime(os.path.getmtime(p))))
                return st
        except Exception:
            continue
    log("state.json unreadable and no readable copy; starting empty (ids still follow the ledger, see new_id)")
    return {}


def load_state():
    try:
        st = _read_json(STATE)
    except Exception:
        st = _fallback_state() if os.path.exists(STATE) and os.path.getsize(STATE) > 0 else {}
    st.setdefault("entries", {})
    st.setdefault("sessions", {})
    st.setdefault("seq", {})
    return st


def _replace_retrying(tmp, dest, tries=60):
    """os.replace, retried: on Windows it fails with PermissionError while any other process has dest open, and with
    the 9 Oct wave's fifty workers reading state.json on every prompt that cost 33 lost writes in an evening (hook.log)."""
    for k in range(tries):
        try:
            os.replace(tmp, dest)
            return
        except PermissionError:
            if k == tries - 1:
                raise
            time.sleep(0.05 + 0.01 * k)


def _sweep_stale_tmps(keep=2, age=3600):
    """A writer killed mid-dump (a hook timeout on a busy disk) leaves its 17 MB state.json.<pid>.tmp behind: 33 in one
    hour on 24 Oct, about half a gigabyte an hour. After a good save, remove those older than an hour, keeping the newest
    `keep` for _fallback_state."""
    import glob
    try:
        olds = sorted(glob.glob(STATE + ".*.tmp"), key=os.path.getmtime, reverse=True)[keep:]
        now = time.time()
        for p in olds:
            try:
                if now - os.path.getmtime(p) > age:
                    os.remove(p)
            except OSError:
                pass
    except Exception:
        pass


def save_state(st):
    tmp = "%s.%d.tmp" % (STATE, os.getpid())     # one temp file per writer: two writers never share a temp
    try:
        with io.open(tmp, "w", encoding="utf-8") as fh:
            json.dump(st, fh, ensure_ascii=False, indent=1, sort_keys=True)
        _replace_retrying(tmp, STATE)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    _sweep_stale_tmps()
    # Once an hour, a copy forced to disk: a power cut can leave state.json as zeros (23 Oct 11:46), and an fsync of
    # 15 MB on every prompt would hold the lock past other hooks' 4 s wait, so the hourly copy carries durability.
    try:
        bak = _backup_path()
        if not os.path.exists(bak) or time.time() - os.path.getmtime(bak) > 3600:
            old_n = os.path.getsize(bak) if os.path.exists(bak) else 0
            if old_n > 2 * os.path.getsize(STATE) + 4096:
                # never let a much smaller state replace a big copy: that is a wiped or wrong store, not the list
                log("hourly backup kept: state.json (%d bytes) is under half the copy (%d bytes)"
                    % (os.path.getsize(STATE), old_n))
                return
            btmp = bak + ".%d.tmp" % os.getpid()
            with io.open(btmp, "w", encoding="utf-8") as fh:
                json.dump(st, fh, ensure_ascii=False, sort_keys=True)
                fh.flush()
                os.fsync(fh.fileno())
            _replace_retrying(btmp, bak)
    except Exception as exc:
        log("hourly backup failed: %r" % (exc,))


def append_ledger(event):
    with io.open(LEDGER, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False) + "\n")


def _ledger_max_today(day):
    """The highest id the ledger has issued today, from its last 2 MB (a day runs ~0.5-1.5 MB). If state.json is ever
    lost again, ids still never repeat: on 23 Oct the reset restarted at 001 and reused 44 ids in two hours."""
    try:
        with open(LEDGER, "rb") as fh:
            fh.seek(0, 2)
            fh.seek(max(0, fh.tell() - 2 * 1024 * 1024))
            tail = fh.read().decode("utf-8", "replace")
        hits = re.findall(r'"kind": "add", "id": "T-%s-(\d+)"' % day, tail)
        return max(int(h) for h in hits) if hits else 0
    except Exception:
        return 0


def new_id(st):
    day = time.strftime("%Y%m%d")
    n = max(int(st["seq"].get(day, 0)), _ledger_max_today(day)) + 1
    while "T-%s-%03d" % (day, n) in st["entries"]:
        n += 1
    st["seq"][day] = n
    return "T-%s-%03d" % (day, n)


def clean_prompt(prompt):
    """The owner's own words, without harness-injected blocks. '' when there are none."""
    if not prompt:
        return ""
    p = prompt
    if "mechanical header, injected by run_dream" in p:
        return ""
    # a relayed agent message, a Codex hook's own prompt or a peer's turn text arrives as a user turn and is not the owner's
    # (two entries were once registered from such text)
    if _MACHINE_TURN_RX.match(p):
        return ""
    for mk in _NOISE:
        if mk in p:
            p = p.split("</task-notification>")[-1] if "</task-notification>" in p else ""
    if "<command-name>" in p:
        name = _CMD_NAME.search(p)
        args = _CMD_ARGS.search(p)
        n = (name.group(1).strip() if name else "")
        a = (args.group(1).strip() if args else "")
        # a /goal IS an ask, in the owner's words; every other slash command is machinery
        p = a if n.strip("/") == "goal" else (n + " " + a).strip()
    p = _SYSREM.sub("", p)
    p = _CMD_STDOUT.sub("", p)
    return p.strip()


def short(text, n=140):
    t = " ".join((text or "").split())
    return t if len(t) <= n else t[: n - 3].rstrip() + "..."


_BARE_SECRET_RX = re.compile(
    r"sk-[A-Za-z0-9]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16}|xox[baprs]-[A-Za-z0-9-]{10,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----")
_LABELLED_SECRET_RX = re.compile(r"(?i)\b(password|passwd|secret|token|api[_-]?key)(\s*[=:]\s*)[^\s\"']{6,}")


def redact(text):
    """Credentials never reach the store: the ledger and state are pushed to a repository.
    Two passes: bare tokens first, then `label=value` with the label kept."""
    t = _BARE_SECRET_RX.sub("<redacted>", text or "")
    return _LABELLED_SECRET_RX.sub(lambda m: m.group(1) + m.group(2) + "<redacted>", t)


_PASTE_RX = re.compile(r'["{}\[\]]\s*[:,]|^\s*[{\[]|"[A-Za-z_]+":\s')


def ask_and_paste(text):
    """Split a prompt into the owner's words and any pasted material (command output, JSON, a log, an email).

    Returns (ask, pasted_chars). A prompt whose first line is prose followed by a paste keeps the prose as the
    ask; a prompt that is ONLY a paste (it starts mid-token or with a brace) has no ask of its own.
    """
    lines = [l for l in (text or "").splitlines() if l.strip()]
    if not lines:
        return "", 0
    pasty = [l for l in lines if _PASTE_RX.search(l)]
    if len(lines) >= 2 and len(pasty) >= max(2, int(0.4 * len(lines))):
        head = lines[0].strip()
        if _PASTE_RX.search(head) or not head[:1].isalpha():
            return "", len(text)
        return head, len(text) - len(head)
    head = lines[0].strip()
    if not head[:1].isalpha() and not head.startswith(("/", "*", "-", "\"", "'")) and _PASTE_RX.search(head):
        return "", len(text)
    return " ".join(text.split()), 0


def last_said(transcript_path, tail_bytes=96 * 1024, full=False):
    """The agent's latest words, from the tail of its own transcript (Claude Code JSONL). '' if unavailable.

    So the owner's list shows what each agent last reported without the agent doing anything - most entries carried
    "(not yet stated by the agent)" in the first two hours because agents do not run `working` by hand.
    """
    try:
        if not transcript_path or not os.path.exists(transcript_path):
            return ""
        with open(transcript_path, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - tail_bytes))
            chunk = fh.read().decode("utf-8", "replace")
        text = ""
        for line in chunk.splitlines():
            if '"assistant"' not in line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if rec.get("type") != "assistant":
                continue
            content = (rec.get("message") or {}).get("content") or []
            parts = [c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"]
            t = " ".join(" ".join(parts).split())
            if t:
                text = t
        text = redact(text)
        return text if full else short(text, 220)
    except Exception:
        return ""


def mesh_task_for(session, agent_hint=""):
    """Best-effort, read-only: the agent mesh row for this session (agent id, task). Never raises."""
    try:
        import sqlite3
        if not os.path.exists(MESH_DB):
            return None, None
        con = sqlite3.connect("file:%s?mode=ro" % MESH_DB.replace("\\", "/"), uri=True, timeout=1)
        try:
            sid = (session or "")[:8]
            rows = []
            if sid:
                rows = con.execute(
                    "select agent_id, task from agents where session_key like ? order by last_seen desc limit 1",
                    ("%" + sid + "%",)).fetchall()
            if not rows and agent_hint:
                rows = con.execute("select agent_id, task from agents where agent_id = ? limit 1",
                                   (agent_hint,)).fetchall()
            if rows:
                return rows[0][0], rows[0][1]
        finally:
            con.close()
    except Exception:
        pass
    return None, None


# ----------------------------------------------------------------------------- the model

def _entry(st, tid):
    e = st["entries"].get(tid)
    if not e:
        raise KeyError("no entry %s" % tid)
    return e


def _event(st, kind, tid, by, note=""):
    ev = {"at": now_iso(), "kind": kind, "id": tid, "by": by, "note": redact(note)}
    append_ledger(ev)
    return ev


def remember_mesh(st, session, mesh_agent, mesh_task):
    if session:
        s = st["sessions"].setdefault(session[:12], {})
        s["mesh_agent"], s["mesh_task"] = mesh_agent, mesh_task


def older_open_for_session(st, session, except_id=None):
    """Open entries of this session other than the one being touched - the ones the agent has not ticked."""
    return sorted((e for e in st["entries"].values()
                   if e["session"] == (session or "")[:12] and e["status"] == "open" and e["id"] != except_id),
                  key=lambda e: e["id"])


def nudge(st, session, except_id=None):
    olds = older_open_for_session(st, session, except_id)
    if not olds:
        return ""
    return ("  Still open on your list: %s - if any of those is finished, `%s done <id> \"<result>\"` now (the owner reads the list)."
            % (", ".join(e["id"] for e in olds[-4:]), CLI))


def add(st, asked, by, runtime, session, working="", asked_at=None, source="hook"):
    tid = new_id(st)
    e = {
        "id": tid,
        "asked_at": asked_at or now_min(),
        "asked_words": redact(" ".join(asked.split())),
        "agent": by,
        "runtime": runtime or "",
        "session": (session or "")[:12],
        "working_on": working or "",
        "status": "open",
        "done_at": None,
        "result": "",
        "seen": False,
        "seen_at": None,
        "updates": [],
        "last_activity": now_min(),
        "source": source,
    }
    st["entries"][tid] = e
    if session:
        st["sessions"].setdefault(session[:12], {})["latest"] = tid
    _event(st, "add", tid, by, short(asked, 200))
    return e


def set_working(st, tid, text, by):
    e = _entry(st, tid)
    e["working_on"] = redact(" ".join(text.split()))
    e["last_activity"] = now_min()
    _event(st, "working", tid, by, e["working_on"])
    return e


def note(st, tid, text, by):
    e = _entry(st, tid)
    e["updates"].append({"at": now_min(), "by": by, "note": redact(" ".join(text.split()))})
    e["updates"] = e["updates"][-40:]
    e["last_activity"] = now_min()
    _event(st, "note", tid, by, short(text, 300))
    return e


def set_status(st, tid, status, by, text=""):
    e = _entry(st, tid)
    e["status"] = status
    e["last_activity"] = now_min()
    if status == "done":
        e["done_at"] = now_min()
        if text:
            text = redact(text)
            if e.get("result"):
                e["updates"].append({"at": now_min(), "by": by, "note": "round result: " + " ".join(text.split())})
                e["updates"] = e["updates"][-40:]
            e["result"] = " ".join(text.split())
    elif status == "open":
        e["done_at"] = None
        e["seen"] = False
        e["seen_at"] = None
    elif text:
        e["updates"].append({"at": now_min(), "by": by, "note": "%s: %s" % (status, " ".join(text.split()))})
    _event(st, status, tid, by, short(text, 300))
    return e


def set_seen(st, tid, by="owner", value=True):
    e = _entry(st, tid)
    e["seen"] = bool(value)
    e["seen_at"] = now_min() if value else None
    _event(st, "seen" if value else "unseen", tid, by)
    return e


def touch(st, tid, by):
    e = _entry(st, tid)
    e["last_activity"] = now_min()


def repeated_ask(st, session, text):
    """The newest entry of this session with exactly these words, whatever its status; None if there is none."""
    words = " ".join(text.split())
    cands = [x for x in st["entries"].values() if x["session"] == (session or "")[:12] and x["asked_words"] == words]
    cands.sort(key=lambda x: x["id"])
    return cands[-1] if cands else None


def newest_for_session(st, session):
    """The session's newest entry whatever its status, or None."""
    cands = [x for x in st["entries"].values() if x["session"] == (session or "")[:12]]
    cands.sort(key=lambda x: x["id"])
    return cands[-1] if cands else None


CLOSING_LINE_RX = re.compile(r"\(\s*this was your\s+\d{1,2}:\d{2}", re.I)
REPORT_LINE_RX = re.compile(r"\bREPORT\s*[-—:]", re.I)


# an interim report carries the closing line too (every report names its ask), so a report that says in words that
# the work is not finished keeps the entry open: on 3 Oct 16:59 "I'm not done: ... won't be ready to upload until
# tomorrow" closed the 15:14 ask whose three uploads were a day away
NOT_DONE_RX = re.compile(r"\b(?:i[\u2019']?m|i am|it[\u2019']?s|it is|this is|we[\u2019']?re|we are)\s+not\s+(?:yet\s+)?"
                         r"(?:done|finished|complete)\b|\bnot\s+(?:done|finished)\s+yet\b", re.I)


def closes_task(said):
    """The house convention: a final report ends by naming the task - '(This was your HH:MM ask ...)' - and the
    waste hook's convention ends it with 'REPORT -'. Either is the agent saying it is finished, unless the report
    says in words that it is not (NOT_DONE_RX)."""
    return (bool(said) and (bool(CLOSING_LINE_RX.search(said)) or bool(REPORT_LINE_RX.search(said)))
            and not NOT_DONE_RX.search(said))


_NAMED_ASK_RX = re.compile(r"\(\s*this was your\s+(\d{1,2}):(\d{2})", re.I)


def entry_to_close(st, session, said, latest):
    """Which open entry an agent's closing line closes. A line naming its ask's time ('This was your 02:44 ask')
    closes only an open entry of this session asked at that minute (one minute either way), else nothing; a bare
    'REPORT -' line closes only the session's latest entry, never the fallback. On 27 Sep 03:22 the 02:44 ask was
    already done, the fallback handed the Stop the session's older open entry - a two-day contract asked at 20:50 -
    and the report's closing line closed that."""
    m = _NAMED_ASK_RX.search(said or "")
    if m:
        want = int(m.group(1)) * 60 + int(m.group(2))
        for x in sorted(st["entries"].values(), key=lambda x: x["id"], reverse=True):
            if x["session"] != (session or "")[:12] or x["status"] != "open":
                continue
            try:
                got = int(x["asked_at"][11:13]) * 60 + int(x["asked_at"][14:16])
            except (ValueError, IndexError, TypeError):
                continue
            if min(abs(got - want), 1440 - abs(got - want)) <= 1:
                return x
        return None
    if latest is not None and latest["status"] == "open" and REPORT_LINE_RX.search(said or ""):
        return latest
    return None


def entry_named(st, session, said):
    """The open entry of this session that an agent's closing line names by its minute: the one asked then, else the
    one whose follow-up ('owner said: ...') or repeat ('asked again') came then, one minute either way. None when the
    minute belongs to no open entry of this session - an ask already done, another session's, a slip. A Stop records
    the agent's activity on this entry only: after the 27 Sep repair above, a line naming a done ask still stamped the
    session's older open entry with that report's words, a fresh activity time and a stop event (found by the
    scheduled audit account02/B14 r37)."""
    m = _NAMED_ASK_RX.search(said or "")
    if not m:
        return None
    want = int(m.group(1)) * 60 + int(m.group(2))

    def near(stamp):
        try:
            got = int(stamp[11:13]) * 60 + int(stamp[14:16])
        except (ValueError, IndexError, TypeError):
            return False
        return min(abs(got - want), 1440 - abs(got - want)) <= 1

    mine = sorted((x for x in st["entries"].values()
                   if x["session"] == (session or "")[:12] and x["status"] == "open"),
                  key=lambda x: x["id"], reverse=True)
    for x in mine:
        if near(x.get("asked_at")):
            return x
    for x in mine:
        for u in reversed(x.get("updates") or []):
            if (u.get("note") or "").startswith(("owner said:", "asked again")) and near(u.get("at")):
                return x
    return None


def latest_open_for_session(st, session):
    sess = st["sessions"].get((session or "")[:12], {})
    tid = sess.get("latest")
    e = st["entries"].get(tid) if tid else None
    if e and e["status"] == "open":
        return e
    # fall back to the newest open entry of this session
    cands = [x for x in st["entries"].values() if x["session"] == (session or "")[:12] and x["status"] == "open"]
    cands.sort(key=lambda x: x["id"])
    return cands[-1] if cands else None


# ----------------------------------------------------------------------------- views

def _sorted(entries):
    return sorted(entries, key=lambda e: e["id"], reverse=True)


def _by_day(entries):
    out = {}
    for e in entries:
        out.setdefault(e["asked_at"][:10], []).append(e)
    return sorted(out.items(), reverse=True)


def _entry_md(e):
    done = "[x] done %s" % e["done_at"][11:] if e["status"] == "done" else "[ ] done"
    if e["status"] in ("blocked", "dropped"):
        done = "[ ] %s" % e["status"]
    seen = "[x] seen %s" % (e["seen_at"] or "")[11:] if e["seen"] else "[ ] seen"
    head = "- %s  %s  **%s**  asked %s  agent %s (%s)" % (done, seen, e["id"], e["asked_at"][11:], e["agent"], e["runtime"] or "?")
    lines = [head, "  You asked: \"%s\"" % short(e["asked_words"], 400)]
    lines.append("  Working on: %s" % (e["working_on"] or "(not yet stated by the agent)"))
    if e.get("result"):
        lines.append("  Result: %s" % short(e["result"], 400))
    for u in e["updates"][-6:]:
        lines.append("  %s - %s" % (u["at"][5:], short(u["note"], 220)))
    ls = e.get("last_said")
    if ls and ls.get("text"):
        lines.append("  Agent last said (%s): %s" % (ls["at"][5:], short(ls["text"], 220)))
    lines.append("  Last activity: %s" % e["last_activity"])
    return "\n".join(lines)


def render(st):
    es = list(st["entries"].values())
    open_ = _sorted([e for e in es if e["status"] in ("open", "blocked")])
    done_unseen = _sorted([e for e in es if e["status"] in ("done", "dropped") and not e["seen"]])
    week_ago = time.strftime("%Y-%m-%d", time.localtime(time.time() - 7 * 86400))
    seen_all = [e for e in es if e["seen"]]
    seen = _sorted([e for e in seen_all if (e.get("seen_at") or "")[:10] >= week_ago])
    n_seen_older = len(seen_all) - len(seen)
    out = []
    out.append("# Task checklist - what you asked the agents, what they are doing, what is done, what you have seen")
    out.append("")
    out.append("Updated %s (owner's clock). %d open, %d done and not yet seen, %d seen." % (now_min(), len(open_), len(done_unseen), len(seen)))
    out.append("Every agent registers each ask here through the checklist hook; mark yours seen with `%s seen <id|all>`." % CLI)
    out.append("")
    for title, group in (("## Open (%d)" % len(open_), open_),
                         ("## Done, not yet seen by you (%d)" % len(done_unseen), done_unseen),
                         ("## Seen in the last 7 days (%d%s)" % (len(seen), (", %d older in the ledger" % n_seen_older) if n_seen_older else ""), seen)):
        out.append(title)
        out.append("")
        if not group:
            out.append("(none)")
            out.append("")
            continue
        for day, items in _by_day(group):
            out.append("### %s" % day)
            for e in items:
                out.append(_entry_md(e))
            out.append("")
    out.append("Store: %s (ledger.jsonl is the dated record of every change; state.json is the folded state)." % STORE.replace("\\", "/"))
    return "\n".join(out) + "\n"


def write_view(st):
    try:
        tmp = "%s.%d.tmp" % (VIEW, os.getpid())
        with io.open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(render(st))
        _replace_retrying(tmp, VIEW)
    except Exception as exc:
        log("render failed: %r" % (exc,))


def render_for_context(st, cap=None):
    """The list as the model can actually receive it: cut at CONTEXT_CAP on a line boundary, open entries first
    (render() puts them first), with the file named for the rest."""
    cap = CONTEXT_CAP if cap is None else cap
    text = render(st)
    if len(text) <= cap:
        return text
    cut = text[:cap]
    cut = cut[:cut.rfind("\n")] if "\n" in cut else cut
    rest = text[len(cut):].count("\n")
    return cut + "\n  ... %d more lines - read the whole list in %s\n" % (rest, VIEW.replace("\\", "/"))


def digest(st, session=None, exclude_session=None, max_others=DIGEST_MAX_OTHERS):
    es = list(st["entries"].values())
    open_ = _sorted([e for e in es if e["status"] in ("open", "blocked")])
    unseen = [e for e in es if e["status"] in ("done", "dropped") and not e["seen"]]
    lines = ["[checklist] %d open task(s) across all agents, %d done and not yet seen by the owner. Full list: %s" % (
        len(open_), len(unseen), VIEW.replace("\\", "/"))]
    others = [e for e in open_ if not exclude_session or e["session"] != (exclude_session or "")[:12]]
    for e in others[:max_others]:
        lines.append("  %s %s %s (%s): asked \"%s\" -> %s" % (
            e["id"], e["asked_at"], e["agent"], e["runtime"] or "?", short(e["asked_words"], 90),
            short(e["working_on"], 70) or "(working-on not stated)"))
    if len(others) > max_others:
        lines.append("  ... and %d more open; read the full list." % (len(others) - max_others))
    return "\n".join(lines)


def commands_help(tid=None):
    t = tid or "<id>"
    return ("  Update yours: %s working %s \"<one line: what you are doing>\" | note %s \"<dated progress>\" | done %s \"<result>\" | blocked %s \"<why>\"" % (CLI, t, t, t, t)
            + "\n  The owner marks seen: %s seen <id|all>. List: %s list" % (CLI, CLI))


# ----------------------------------------------------------------------------- hook mode

def hook_main(runtime_arg):
    # The harness hands the payload as UTF-8 bytes; a text read on Windows decodes it as the console
    # code page and turns every em dash in the prompt into mojibake. Read bytes, decode UTF-8.
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", "replace")
    except Exception:
        try:
            raw = sys.stdin.read()
        except Exception:
            raw = ""
    i = raw.find("{")
    try:
        payload = json.loads(raw[i:]) if i >= 0 else {}
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    event = str(payload.get("hook_event_name") or "")
    session = str(payload.get("session_id") or payload.get("thread_id") or "")
    # A HARNESS IS NOT THE OWNER. Every real Claude and Codex session carries a uuid-shaped id; the
    # regression gate, the brain studies and the dispatcher controls send test prompts through this hook with ids like
    # "regression-gate", "brainstudy-a" and "r5-env-int-C". Nine such prompts sat in the checklist as open asks from
    # agents that do not exist ("State the triangle removal lemma precisely."). On the live store, a session whose id is
    # not uuid-shaped registers nothing and prints nothing; the selftest's temp store is unaffected, and
    # TASK_CHECKLIST_ALLOW_HARNESS=1 lifts it.
    if (session and not re.match(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-", session)
            and os.path.normcase(os.path.abspath(STORE)) in _LIVE_STORES
            and os.environ.get("TASK_CHECKLIST_ALLOW_HARNESS") != "1"):
        return 0
    runtime = runtime_arg or ("codex" if payload.get("thread_id") else "claude")
    if "--runtime" not in sys.argv and os.environ.get("CODEX_THREAD_ID"):
        runtime = "codex"
    # the mesh lookup opens a 200 MB sqlite that is often write-locked; do it once per session and remember it
    st0 = load_state()
    sess0 = st0["sessions"].get(session[:12], {}) if session else {}
    if "mesh_agent" in sess0:
        mesh_agent, mesh_task = sess0.get("mesh_agent"), sess0.get("mesh_task")
    else:
        mesh_agent, mesh_task = mesh_task_for(session)
    agent = os.environ.get("TASK_CHECKLIST_AGENT") or mesh_agent or ("%s-%s" % (runtime, session[:8] or "nosession"))

    if event == "SessionStart":
        st = load_state()
        out = digest(st, session=session)
        print(out)
        print(commands_help())
        return 0

    if event == "Stop":
        said_full = last_said(payload.get("transcript_path"), full=True)
        said = short(said_full, 220)
        with _Lock():
            st = load_state()
            # a closing line that names its ask's minute decides whose activity this was; with no such line the
            # session's open entry is the best guess
            if _NAMED_ASK_RX.search(said_full or ""):
                e = entry_named(st, session, said_full)
            else:
                e = latest_open_for_session(st, session)
            if e:
                if not e["working_on"] and mesh_task:
                    e["working_on"] = short(mesh_task, 200) + " (from the agent mesh)"
                    _event(st, "working", e["id"], agent, e["working_on"])
                if said:
                    e["last_said"] = {"at": now_min(), "text": said}
                touch(st, e["id"], agent)
                _event(st, "stop", e["id"], agent, said[:200] if said else "")
                # the agent's own closing line closes the entry it names - agents rarely run `done` by hand
                if closes_task(said_full):
                    lt = st["entries"].get(st["sessions"].get((session or "")[:12], {}).get("latest") or "")
                    c = entry_to_close(st, session, said_full, lt)
                    if c is not None:
                        result = short(CLOSING_LINE_RX.split(said_full)[0], 300) or short(said_full, 300)
                        set_status(st, c["id"], "done", agent, result)
                        note(st, c["id"], "closed by the agent's closing line", agent)
                save_state(st)
                write_view(st)
        return 0

    if event != "UserPromptSubmit":
        return 0
    text = redact(clean_prompt(str(payload.get("prompt") or payload.get("user_prompt") or payload.get("text") or "")))
    if not text:
        return 0
    # a /goal is an ask in the owner's words, whichever way the harness delivers it - the XML command form (handled in
    # clean_prompt) or plain text "/goal keep watching the movie ..." (once skipped as a slash command)
    m_goal = re.match(r"^/goal\b\s*(.*)$", text, re.S)
    if m_goal:
        text = m_goal.group(1).strip()
        if not text:
            return 0
    elif text.startswith("/") and not text.startswith("/checklist"):
        return 0
    # pasted output, JSON, a log or an email is context, not the ask: keep the owner's sentence, note the paste; a prompt
    # that is only a paste is attached to the session's open entry (a pasted subscription listing was once
    # registered verbatim as an ask)
    if PROBE_RX.match(text):
        return 0
    ask, pasted = ask_and_paste(text)
    if pasted:
        with _Lock():
            st = load_state()
            latest = latest_open_for_session(st, session)
            if not ask:
                if latest:
                    note(st, latest["id"], "the owner pasted %d characters of output" % pasted, agent)
                    save_state(st)
                    write_view(st)
                return 0
        text = ask + " [+ %d pasted characters]" % pasted

    # A question about the tasks is SHORT. A long prompt that merely mentions the checklist ("...then go back and
    # audit this checklist...") is an ask and must be registered - the first version read the word and printed the
    # list instead of registering the ask (an early version did exactly that).
    # ...and a SHORT imperative that mentions the list ("audit and make sure the agents are using the checklist") is
    # an ask too. So the question path needs the FORM of a question: it
    # starts with an interrogative, or ends in "?", or is the /checklist command - and it is short.
    # "mark all seen" / "i've seen it" is the owner's checkmark, whatever its form - tell the agent to run `seen`, register nothing
    if SEEN_RX.search(text) and len(text) <= MAX_QUESTION_CHARS:
        st = load_state()
        print("[checklist] The owner is marking entries SEEN - that checkmark is theirs. Run `%s seen <id|all|today>` for the ones the owner "
              "names (all = every done entry not yet seen), then confirm which ids moved. The list:" % CLI)
        print(render_for_context(st))
        return 0
    mentions = QUESTION_RX.search(text) or text.startswith("/checklist")
    is_question = bool(INTERROGATIVE_RX.match(text)) or text.rstrip().endswith("?") or text.startswith("/checklist")
    if mentions and is_question and (len(text) <= MAX_QUESTION_CHARS or text.startswith("/checklist")):
        st = load_state()
        print("[checklist] The owner is asking about the tasks they gave agents. Answer FROM the checklist below, "
              "in the owner's words per entry, dated; then, if they say they have seen some, run `%s seen <id|all>`." % CLI)
        print(render_for_context(st))
        return 0
    print_list_too = bool(mentions)      # an ask ABOUT the checklist still gets the list, after it is registered

    with _Lock():
        st = load_state()
        latest = latest_open_for_session(st, session)
        if len(text) < MIN_ASK_CHARS or FOLLOWUP_RX.match(text):
            if not latest:
                # "keep working" after the entry was ticked done: the owner is continuing that task - reopen it
                latest = newest_for_session(st, session)
                if latest and latest["status"] != "open":
                    set_status(st, latest["id"], "open", agent, "")
                    st["sessions"].setdefault(session[:12], {})["latest"] = latest["id"]
            if latest:
                note(st, latest["id"], "owner said: " + short(text, 200), agent)
                save_state(st)
                write_view(st)
                print("[checklist] The owner's follow-up is on %s (%s): \"%s\"" % (latest["id"], latest["status"], short(text, 80)))
            return 0
        if latest and latest["asked_words"] == " ".join(text.split()):
            return 0
        # The same words again from the same session - a /loop tick, a re-ask - is ONE entry with a dated update
        # log, reopened per tick, not a new line on the owner's list each time (a 3-minute loop would add 480 a day).
        again = repeated_ask(st, session, text)
        if again:
            n = sum(1 for u in again["updates"] if u["note"].startswith("asked again")) + 2
            if again["status"] != "open":
                set_status(st, again["id"], "open", agent, "")
            note(st, again["id"], "asked again (time %d)" % n, agent)
            st["sessions"].setdefault(session[:12], {})["latest"] = again["id"]
            save_state(st)
            write_view(st)
            print("[checklist] The owner's repeated ask is %s (time %d), reopened: \"%s\"" % (again["id"], n, short(text, 120)))
            print("  Close each round with: %s done %s \"<result>\" - the rounds stay as dated updates on the one entry." % (CLI, again["id"]))
            return 0
        e = add(st, text, agent, runtime, session, working=(short(mesh_task, 200) + " (from the agent mesh)") if mesh_task else "")
        save_state(st)
        write_view(st)
        print("[checklist] Registered the owner's ask as %s under agent %s: \"%s\"" % (e["id"], agent, short(text, 160)))
        print("  Before substantive work state what you will do: %s working %s \"<one line>\"; when finished: %s done %s \"<result>\"." % (CLI, e["id"], CLI, e["id"]))
        if print_list_too:
            print(render_for_context(st))
        else:
            print(digest(st, session=session, exclude_session=session))
        n = nudge(st, session, e["id"])
        if n:
            print(n)
        return 0


# ----------------------------------------------------------------------------- cli mode

def _who(args):
    """Who made a CLI change. An agent that leaves --by off is named from its own session before anything else: the
    old fallback joined the runtime to the OS user name, which on a single-user machine is the owner's own account,
    so every agent note, working line and done without --by was recorded under the owner's name and read as theirs."""
    by = getattr(args, "by", None) or os.environ.get("TASK_CHECKLIST_AGENT")
    if by:
        return by
    sid = (os.environ.get("CLAUDE_CODE_SESSION_ID") or "").strip().lower()
    if re.match(r"[0-9a-f]{8}", sid):
        return "claude-" + sid[:8]
    tid = (os.environ.get("CODEX_THREAD_ID") or "").strip().lower()
    if re.match(r"[0-9a-f]{8}", tid):
        return "codex-" + tid[:8]
    return "%s-%s" % (getattr(args, "runtime", "") or "cli", os.environ.get("USERNAME", "owner"))


def cli_main(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="task_checklist", description="the owner's checklist of what they asked every agent")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("add", help="register an ask (the owner's words) under an agent")
    p.add_argument("--asked", required=True, help="the owner's words, verbatim")
    p.add_argument("--working", default="", help="what the agent will do, one line")
    p.add_argument("--by", default=None, help="agent id (mesh id if you have one)")
    p.add_argument("--runtime", default="", help="claude | codex | headless")
    p.add_argument("--session", default="", help="session or thread id")
    p.add_argument("--asked-at", default=None, help="YYYY-MM-DD HH:MM, the owner's clock (default now)")
    for name, hlp in (("working", "state what you are doing on an entry"), ("note", "dated progress note"),
                      ("done", "check it off, with the result in one line"), ("blocked", "why it is blocked"),
                      ("drop", "dropped, with why"), ("reopen", "reopen a done entry")):
        q = sub.add_parser(name, help=hlp)
        q.add_argument("id")
        q.add_argument("text", nargs="?", default="")
        q.add_argument("--by", default=None)
        q.add_argument("--runtime", default="")
    q = sub.add_parser("seen", help="the owner's checkmark: seen <id|all|today|T-...,T-...>")
    q.add_argument("id")
    q.add_argument("--undo", action="store_true")
    q = sub.add_parser("list", help="text table")
    q.add_argument("--all", action="store_true")
    q.add_argument("--open", action="store_true")
    q.add_argument("--unseen", action="store_true")
    q.add_argument("--agent", default="")
    q = sub.add_parser("show", help="one entry in full")
    q.add_argument("id")
    sub.add_parser("render", help="rewrite CHECKLIST.md")
    sub.add_parser("digest", help="the short block the hooks print")
    sub.add_parser("where", help="print the store path")
    args = ap.parse_args(argv)
    if not args.cmd:
        ap.print_help()
        return 0
    if args.cmd == "where":
        print(STORE)
        return 0
    if args.cmd in ("list", "show", "digest"):
        st = load_state()
        if args.cmd == "digest":
            print(digest(st))
            return 0
        if args.cmd == "show":
            e = _entry(st, args.id)
            print(json.dumps(e, ensure_ascii=False, indent=1))
            return 0
        es = _sorted(st["entries"].values())
        if args.open:
            es = [e for e in es if e["status"] in ("open", "blocked")]
        elif args.unseen:
            es = [e for e in es if not e["seen"]]
        elif not args.all:
            es = [e for e in es if e["status"] in ("open", "blocked") or not e["seen"]]
        if args.agent:
            es = [e for e in es if args.agent in e["agent"]]
        for e in es:
            print("%s  %-7s %s  %s  %-28s %s" % (e["id"], e["status"], "seen" if e["seen"] else "----", e["asked_at"], e["agent"][:28], short(e["asked_words"], 80)))
            if e["working_on"]:
                print("%s  working on: %s" % (" " * 15, short(e["working_on"], 100)))
        if not es:
            print("(nothing to show)")
        return 0
    with _Lock():
        st = load_state()
        by = _who(args)
        if args.cmd == "add":
            e = add(st, args.asked, by, args.runtime, args.session, working=args.working, asked_at=args.asked_at, source="cli")
            print(e["id"])
        elif args.cmd == "working":
            set_working(st, args.id, args.text, by)
        elif args.cmd == "note":
            note(st, args.id, args.text, by)
        elif args.cmd == "done":
            set_status(st, args.id, "done", by, args.text)
        elif args.cmd == "blocked":
            set_status(st, args.id, "blocked", by, args.text)
        elif args.cmd == "drop":
            set_status(st, args.id, "dropped", by, args.text)
        elif args.cmd == "reopen":
            set_status(st, args.id, "open", by, args.text)
        elif args.cmd == "seen":
            ids = []
            if args.id == "all":
                ids = [k for k, e in st["entries"].items() if e["status"] in ("done", "dropped") and not e["seen"]]
            elif args.id == "today":
                ids = [k for k, e in st["entries"].items() if (e["done_at"] or "")[:10] == time.strftime("%Y-%m-%d") and not e["seen"]]
            else:
                ids = [x.strip() for x in args.id.split(",") if x.strip()]
            for tid in ids:
                set_seen(st, tid, "owner", not args.undo)
            print("marked %s: %s" % ("unseen" if args.undo else "seen", ", ".join(ids) or "(none)"))
        elif args.cmd == "render":
            pass
        save_state(st)
        write_view(st)
    return 0


# ----------------------------------------------------------------------------- selftest

def selftest():
    import shutil
    import tempfile
    global STORE, LEDGER, STATE, VIEW, LOCK, LOG
    tmp = tempfile.mkdtemp(prefix="checklist_selftest_")
    STORE, LEDGER, STATE, VIEW, LOCK, LOG = (tmp, os.path.join(tmp, "ledger.jsonl"), os.path.join(tmp, "state.json"),
                                             os.path.join(tmp, "CHECKLIST.md"), os.path.join(tmp, ".lock"), os.path.join(tmp, "hook.log"))
    fails = []

    def run_hook(payload, runtime="claude"):
        so, si = sys.stdout, sys.stdin
        sys.stdin = io.StringIO(json.dumps(payload))
        sys.stdout = io.StringIO()
        try:
            hook_main(runtime)
            return sys.stdout.getvalue()
        finally:
            sys.stdout, sys.stdin = so, si

    def check(name, cond):
        print("  %s  %s" % ("PASS" if cond else "FAIL", name))
        if not cond:
            fails.append(name)

    ask = "please audit the paper in my inbox, rewrite it, and email me the new draft tonight"
    out = run_hook({"hook_event_name": "UserPromptSubmit", "session_id": "sessA0001", "prompt": ask})
    st = load_state()
    check("an ask registers one entry", len(st["entries"]) == 1 and "Registered" in out)
    tid = list(st["entries"])[0]
    check("entry carries the owner's words verbatim", st["entries"][tid]["asked_words"] == ask)
    out2 = run_hook({"hook_event_name": "UserPromptSubmit", "session_id": "sessA0001", "prompt": ask})
    check("the same ask again does not duplicate", len(load_state()["entries"]) == 1 and out2 == "")
    run_hook({"hook_event_name": "UserPromptSubmit", "session_id": "sessA0001", "prompt": "ok do it"})
    check("a short follow-up attaches as a dated update", len(load_state()["entries"]) == 1 and len(load_state()["entries"][tid]["updates"]) == 1)
    out3 = run_hook({"hook_event_name": "UserPromptSubmit", "session_id": "sessB0002", "prompt": "/model opus"})
    check("a slash command registers nothing", len(load_state()["entries"]) == 1 and out3 == "")
    relay = ('<cross-session-message from="uds:\\\\.\\pipe\\LOCAL\\cc-msg-0000" from-name="owner-49" from-mode="bypass">\n'
             "Owner's order for every film agent: narration keeps today's normal voice.\n</cross-session-message>")
    out3b = run_hook({"hook_event_name": "UserPromptSubmit", "session_id": "sessB0002", "prompt": relay})
    check("a peer's <cross-session-message> relay registers nothing", len(load_state()["entries"]) == 1 and out3b == "")
    check("the owner's own words that merely mention a cross-session-message still count",
          clean_prompt("why are <cross-session-message> relays filed as my asks? fix the checklist") != "")
    out4 = run_hook({"hook_event_name": "UserPromptSubmit", "session_id": "sessB0002", "prompt": "what did i ask the agents to do today?"})
    check("a question about tasks prints the full checklist and registers nothing", "Task checklist" in out4 and tid in out4 and len(load_state()["entries"]) == 1)
    out5 = run_hook({"hook_event_name": "UserPromptSubmit", "session_id": "sessB0002",
                     "prompt": "build me a movie about the ReLU network with lots of pictures and narration"})
    check("a second agent's ask registers and its digest names the first agent's open task", len(load_state()["entries"]) == 2 and tid in out5)
    check("the two entries have distinct dated ids", len(set(load_state()["entries"])) == 2 and all(k.startswith("T-") for k in load_state()["entries"]))
    run_hook({"hook_event_name": "Stop", "session_id": "sessA0001"})
    check("Stop records activity and prints nothing", "stop" in io.open(LEDGER, encoding="utf-8").read())
    cli_main(["working", tid, "auditing with three passes then rewriting", "--by", "agentA"])
    check("working sets the line", load_state()["entries"][tid]["working_on"].startswith("auditing"))
    cli_main(["done", tid, "audited, rewritten, emailed", "--by", "agentA"])
    e = load_state()["entries"][tid]
    check("done ticks the box with the time and the result", e["status"] == "done" and e["done_at"] and e["result"].startswith("audited"))
    check("done is not seen until the owner says so", e["seen"] is False)
    cli_main(["seen", tid])
    e = load_state()["entries"][tid]
    check("seen ticks the owner's box with the time", e["seen"] is True and bool(e["seen_at"]))
    md = io.open(VIEW, encoding="utf-8").read()
    check("the rendered view has the three sections and both checkmarks", "## Open (1)" in md and "## Seen in the last 7 days (1)" in md and "[x] done" in md and "[x] seen" in md)
    check("the rendered view quotes the owner's words", ask in md)
    out6 = run_hook({"hook_event_name": "SessionStart", "session_id": "sessC0003"})
    check("SessionStart prints the digest with the other agent's open task", "[checklist] 1 open" in out6 and "sessB" in out6 or "T-" in out6)
    cli_main(["done", "T-%s-002" % time.strftime("%Y%m%d"), "film built", "--by", "agentB"])
    out_rep = run_hook({"hook_event_name": "UserPromptSubmit", "session_id": "sessB0002",
                        "prompt": "build me a movie about the ReLU network with lots of pictures and narration"})
    st_rep = load_state()
    check("the same ask again after done REOPENS the one entry instead of adding another",
          len(st_rep["entries"]) == 2 and "reopened" in out_rep and st_rep["entries"]["T-%s-002" % time.strftime("%Y%m%d")]["status"] == "open")
    out7 = run_hook({"hook_event_name": "UserPromptSubmit", "session_id": "sessC0003",
                     "prompt": "<command-name>/goal</command-name><command-args>keep the boxes running all night and report hourly</command-args>"})
    check("a /goal counts as an ask in the owner's words", len(load_state()["entries"]) == 3 and "keep the boxes" in out7)
    out7b = run_hook({"hook_event_name": "UserPromptSubmit", "session_id": "sessH0008",
                      "prompt": "/goal keep watching the movie, get everything ready for rendering, start rendering what you can tonight"})
    check("a /goal delivered as plain text registers too (one was once skipped as a slash command)",
          len(load_state()["entries"]) == 4 and "keep watching the movie" in out7b)
    # an early defect: a long ask that mentions the checklist must REGISTER, not print the list
    long_ask = ("check box 3 and make sure the cores run legal lanes only, no more calendar spread lanes, queue 15 days of "
                "new work that was not done before, new data, proportions, holding periods, moneyness, push the library to "
                "the brain, then go back and audit this checklist and the system and make sure it is working and part of "
                "the brain and making things better")
    n_before = len(load_state()["entries"])
    out_long = run_hook({"hook_event_name": "UserPromptSubmit", "session_id": "sessD0004", "prompt": long_ask})
    check("a long ask that mentions the checklist registers (it is not a question)",
          len(load_state()["entries"]) == n_before + 1 and "Registered" in out_long)
    out_q = run_hook({"hook_event_name": "UserPromptSubmit", "session_id": "sessD0004", "prompt": "show me the checklist"})
    check("a short question about the checklist prints the list and registers nothing",
          len(load_state()["entries"]) == n_before + 1 and "Task checklist" in out_q)
    # an early case: a SHORT imperative that mentions the checklist is an ask, and it gets the list as well
    out_imp = run_hook({"hook_event_name": "UserPromptSubmit", "session_id": "sessE0005",
                        "prompt": "audit and make sure the agents are using the checklist and it is working"})
    check("a short imperative that mentions the checklist REGISTERS (and still prints the list)",
          len(load_state()["entries"]) == n_before + 2 and "Registered" in out_imp and "Task checklist" in out_imp)
    out_q2 = run_hook({"hook_event_name": "UserPromptSubmit", "session_id": "sessE0005",
                       "prompt": "what are the agents working on right now, is the checklist current?"})
    check("a question ending in ? about the agents prints the list and registers nothing",
          len(load_state()["entries"]) == n_before + 2 and "Task checklist" in out_q2)
    # Stop reads the agent's last words from its transcript
    tpath = os.path.join(tmp, "transcript.jsonl")
    with io.open(tpath, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "user", "message": {"content": "hi"}}) + "\n")
        fh.write(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "Queued the lanes; the archiver is back on."}]}}) + "\n")
    run_hook({"hook_event_name": "Stop", "session_id": "sessD0004", "transcript_path": tpath})
    e4 = latest_open_for_session(load_state(), "sessD0004")
    check("Stop records the agent's last words from its transcript",
          bool(e4) and (e4.get("last_said") or {}).get("text", "").startswith("Queued the lanes"))
    check("the rendered view shows what the agent last said", "Agent last said" in io.open(VIEW, encoding="utf-8").read())
    # pasted material: a prompt that is only JSON output attaches as a note; prose + paste keeps the prose
    nE = len(load_state()["entries"])
    run_hook({"hook_event_name": "UserPromptSubmit", "session_id": "sessE0005",
              "prompt": 'scription": null, "id": "/subscriptions/1234-5678/resourceGroups/x",\n  "location": "eastus",\n  "name": "box"\n}'})
    eE = latest_open_for_session(load_state(), "sessE0005")
    check("a prompt that is only pasted JSON becomes a note on the open entry, not an ask",
          len(load_state()["entries"]) == nE and bool(eE) and any("pasted" in u["note"] for u in eE["updates"]))
    run_hook({"hook_event_name": "UserPromptSubmit", "session_id": "sessF0006",
              "prompt": 'here is the output, fix whatever is wrong with the deployment please\n{\n  "status": "failed",\n  "code": "QuotaExceeded",\n  "id": "/subscriptions/1234"\n}'})
    eF = latest_open_for_session(load_state(), "sessF0006")
    check("prose followed by a paste keeps the prose as the ask and notes the paste size",
          bool(eF) and eF["asked_words"].startswith("here is the output") and "pasted characters" in eF["asked_words"])
    # the owner's checkmark, in any form, is never registered as an ask
    nS = len(load_state()["entries"])
    out_seen = run_hook({"hook_event_name": "UserPromptSubmit", "session_id": "sessF0006", "prompt": "mark all seen"})
    check("'mark all seen' tells the agent to run seen and registers nothing",
          len(load_state()["entries"]) == nS and "marking entries SEEN" in out_seen)
    # a follow-up after the entry was ticked done reopens it ("keep working")
    cli_main(["done", eF["id"], "deployment fixed", "--by", "agentF"])
    out_kw = run_hook({"hook_event_name": "UserPromptSubmit", "session_id": "sessF0006", "prompt": "keep working"})
    eF2 = load_state()["entries"][eF["id"]]
    check("'keep working' after done reopens the entry with the note",
          eF2["status"] == "open" and any("keep working" in u["note"] for u in eF2["updates"]) and eF["id"] in out_kw)
    # the agent's closing line closes the entry
    tpath2 = os.path.join(tmp, "transcript2.jsonl")
    with io.open(tpath2, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "assistant", "message": {"content": [{"type": "text",
                 "text": "Deployment repaired and verified; the quota was raised.\n\n(This was your %s ask: fix the deployment.)"
                 % eF["asked_at"][11:16]}]}}) + "\n")
    run_hook({"hook_event_name": "Stop", "session_id": "sessF0006", "transcript_path": tpath2})
    eF3 = load_state()["entries"][eF["id"]]
    check("a Stop whose last words carry the closing line ticks the entry done with the result",
          eF3["status"] == "done" and eF3["result"].startswith("Deployment repaired") and "(This was your" not in eF3["result"])
    # a closing line closes only the ask it names (27 Sep: a report's line closed an older two-day contract)
    def stop_with(sess, text):
        p = os.path.join(tmp, "transcript_%s.jsonl" % sess)
        with io.open(p, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}) + "\n")
        run_hook({"hook_event_name": "Stop", "session_id": sess, "transcript_path": p})
    with _Lock():
        st = load_state()
        eA = add(st, "keep working on the brain for the next 2 days", "agentH", "claude", "sessH0008", asked_at="2026-09-26 20:50")
        eB = add(st, "give me a report in chrome on the scheduled tasks", "agentH", "claude", "sessH0008", asked_at="2026-09-27 02:44")
        # sentinels no clock can produce, and a mesh task that would fill an empty working line
        eA["last_activity"] = "2025-01-01 00:00"
        eA["last_said"] = {"at": "2025-01-01 00:00", "text": "sentinel words"}
        st["sessions"].setdefault("sessH0008", {}).update({"mesh_agent": "agentH", "mesh_task": "two-day brain run"})
        save_state(st)
    cli_main(["done", eB["id"], "report opened", "--by", "agentH"])
    stop_with("sessH0008", "The report is open in Chrome.\n\n(This was your 02:44 ask: a report on the scheduled tasks.)")
    check("a closing line naming a done ask does not close the session's other open entry",
          load_state()["entries"][eA["id"]]["status"] == "open")

    def ledger_kinds(tid):
        out = []
        for line in io.open(LEDGER, encoding="utf-8"):
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if ev.get("id") == tid:
                out.append(ev.get("kind"))
        return out
    sA = load_state()["entries"][eA["id"]]
    check("...and leaves that entry as it was: no borrowed words, no fresh activity, no working line, no stop event",
          sA["last_activity"] == "2025-01-01 00:00" and sA["last_said"]["text"] == "sentinel words"
          and sA["working_on"] == "" and "stop" not in ledger_kinds(eA["id"]) and "working" not in ledger_kinds(eA["id"]))
    with _Lock():
        st = load_state()
        eK1 = add(st, "rebuild the word index", "agentK", "claude", "sessK0011", asked_at="2026-09-27 07:00")
        eK2 = add(st, "check what the fleet reported", "agentK", "claude", "sessK0011", asked_at="2026-09-27 08:30")
        eK2["last_activity"] = "2025-01-01 00:00"
        save_state(st)
    stop_with("sessK0011", "Index rebuilt and checked.\n\n(This was your 07:00 ask: rebuild the word index.)")
    sK = load_state()["entries"]
    check("a closing line naming an older open ask puts the words on that ask and closes it; the latest is untouched",
          sK[eK1["id"]]["status"] == "done" and sK[eK1["id"]].get("last_said", {}).get("text", "").startswith("Index rebuilt")
          and "last_said" not in sK[eK2["id"]] and sK[eK2["id"]]["last_activity"] == "2025-01-01 00:00"
          and sK[eK2["id"]]["status"] == "open")
    with _Lock():
        st = load_state()
        eL = add(st, "keep improving the brain for two days", "agentL", "claude", "sessL0012", asked_at="2026-09-26 20:46")
        eL["updates"].append({"at": "2026-09-27 04:34", "by": "agentL", "note": "owner said: keep working"})
        eL["last_activity"] = "2025-01-01 00:00"
        eM = add(st, "tidy the logs", "agentM", "claude", "sessM0013", asked_at="2026-09-27 06:00")
        eM["last_activity"] = "2025-01-01 00:00"
        save_state(st)
    stop_with("sessL0012", "Measured the rewrite at equal lines.\n\n(This was your 04:34 ask: keep working.)")
    sL = load_state()["entries"][eL["id"]]
    check("a closing line naming a follow-up's minute records the activity on that entry and leaves it open",
          sL["status"] == "open" and sL.get("last_said", {}).get("text", "").startswith("Measured the rewrite")
          and sL["last_activity"] != "2025-01-01 00:00")
    stop_with("sessM0013", "Logs tidied.\n\n(This was your 23:59 ask: something else.)")
    sM = load_state()["entries"][eM["id"]]
    check("a closing line naming a minute no open entry has records nothing on the session's entry",
          sM["status"] == "open" and "last_said" not in sM and sM["last_activity"] == "2025-01-01 00:00"
          and "stop" not in ledger_kinds(eM["id"]))
    stop_with("sessH0008", "Two days done.\n\n(This was your 20:50 ask: keep working on the brain.)")
    check("a closing line naming an open entry's minute closes that entry",
          load_state()["entries"][eA["id"]]["status"] == "done")
    with _Lock():
        st = load_state()
        eN = add(st, "finish the film and upload all three versions", "agentN", "claude", "sessN0014",
                 asked_at="2026-10-03 15:14")
        save_state(st)
    stop_with("sessN0014", "I\u2019m not done: the poems are covered, but the film waits on its pictures until tomorrow."
              "\n\n(This was your 15:14 ask: finish the film and upload it.)")
    sN = load_state()["entries"][eN["id"]]
    check("a report that says it is not done keeps its named entry open and records the words (3 Oct 16:59)",
          sN["status"] == "open" and sN.get("last_said", {}).get("text", "").startswith("I\u2019m not done"))
    stop_with("sessN0014", "Uploaded all three; nothing is left.\n\n(This was your 15:14 ask: finish the film and upload it.)")
    check("...and the same entry closes on the report that finishes it",
          load_state()["entries"][eN["id"]]["status"] == "done")
    with _Lock():
        st = load_state()
        eC = add(st, "check the rebalancing bots", "agentI", "claude", "sessI0009", asked_at="2026-09-27 09:10")
        eD = add(st, "audit the paper", "agentJ", "claude", "sessJ0010", asked_at="2026-09-27 08:00")
        eE = add(st, "audit the paper again", "agentJ", "claude", "sessJ0010", asked_at="2026-09-27 09:00")
        save_state(st)
    stop_with("sessI0009", "Checked.\n\nREPORT - checked the bots; nothing stopped; nothing left running.")
    check("a bare REPORT line closes the session's latest open entry", load_state()["entries"][eC["id"]]["status"] == "done")
    cli_main(["done", eE["id"], "audited", "--by", "agentJ"])
    stop_with("sessJ0010", "Done.\n\nREPORT - audited; nothing stopped.")
    check("a bare REPORT line never closes the fallback (an older open entry)",
          load_state()["entries"][eD["id"]]["status"] == "open")
    # The fixture is SYNTHETIC but it is shaped like a credential, and a file carrying that shape
    # trips every secret scanner this repository's push discipline runs - a control nobody can
    # publish is a control nobody runs. Assembled here so the file carries no token shape while
    # the control still plants a real one at runtime and still fails if the redaction regresses.
    planted = "ghp_" + "abcdefghijklmnopqrstuvwxyz" + "0123456789"
    run_hook({"hook_event_name": "UserPromptSubmit", "session_id": "sessG0007",
              "prompt": "use this key to push the repo and then tell me when it is done: token=" + planted})
    eG = latest_open_for_session(load_state(), "sessG0007")
    check("a credential in a prompt never reaches the store",
          bool(eG) and planted[:10] not in eG["asked_words"] and "<redacted>" in eG["asked_words"]
          and planted[:10] not in io.open(LEDGER, encoding="utf-8").read())
    # THE LABELLED PASS ALONE COVERED THE CASE ABOVE. Measured 2026-09-21: with _BARE_SECRET_RX
    # disabled and only the `token=` pass left, the control above still went green - so the bare
    # arm, the one that catches a key pasted with no label in front of it, had no control of its
    # own and could have regressed silently. This is that control: same shape, no label.
    bare = "AKIA" + "IOSFODNN7EXAMPLE"          # the AWS docs' own example id, assembled, never a key
    run_hook({"hook_event_name": "UserPromptSubmit", "session_id": "sessG0008",
              "prompt": "here it is " + bare + " use it to list the bucket and tell me what is there"})
    eG2 = latest_open_for_session(load_state(), "sessG0008")
    check("an UNLABELLED credential is redacted too (the bare arm has its own control)",
          bool(eG2) and bare not in eG2["asked_words"] and "<redacted>" in eG2["asked_words"]
          and bare not in io.open(LEDGER, encoding="utf-8").read())
    so = sys.stdout
    sys.stdin = io.StringIO("not json at all")
    sys.stdout = io.StringIO()
    try:
        rc = hook_main("claude")
    finally:
        sys.stdout = so
    check("garbage on stdin never raises", rc == 0)
    shutil.rmtree(tmp, ignore_errors=True)
    print("selftest: %d control(s) failed" % len(fails))
    return 1 if fails else 0


# ----------------------------------------------------------------------------- entry

def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    argv = sys.argv[1:]
    if "--selftest" in argv:
        return selftest()
    if "--hook" in argv:
        runtime = ""
        if "--runtime" in argv:
            runtime = argv[argv.index("--runtime") + 1]
        if runtime == "codex" or (not runtime and os.environ.get("CODEX_THREAD_ID")):
            # CODEX INJECTS ONLY JSON. Measured 2026-09-20 over five days of rollouts: every hook whose lines reach a
            # Codex turn emits {"hookSpecificOutput": {"additionalContext": ...}}; this hook printed plain text, ran,
            # and not one Codex session ever saw a registration or the digest. Capture the lines and wrap them.
            try:
                raw = sys.stdin.buffer.read()
            except Exception:
                raw = b""
            try:
                event = str(json.loads(raw.decode("utf-8", "replace")[raw.decode("utf-8", "replace").find("{"):]).get("hook_event_name") or "UserPromptSubmit")
            except Exception:
                event = "UserPromptSubmit"
            real_in, real_out = sys.stdin, sys.stdout
            buf = io.StringIO()
            rc = 0
            try:
                sys.stdin = io.TextIOWrapper(io.BytesIO(raw), encoding="utf-8", errors="replace")
                sys.stdout = buf
                rc = hook_main("codex")
            except Exception as exc:
                log("hook error: %r" % (exc,))
            finally:
                sys.stdin, sys.stdout = real_in, real_out
            text = buf.getvalue().strip()
            if text:
                sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}))
            return rc or 0
        try:
            return hook_main(runtime)
        except Exception as exc:
            log("hook error: %r" % (exc,))
            return 0
    # the CLI is a person or an agent at a keyboard: say what went wrong, loudly, and exit 1 - the first version
    # swallowed a KeyError on a purged id into hook.log and exited 0, which reads as success to a script
    try:
        return cli_main(argv)
    except KeyError as exc:
        sys.stderr.write("checklist: %s (run `%s list --all` to see the ids)\n" % (exc.args[0] if exc.args else exc, CLI))
        return 1
    except Exception as exc:
        sys.stderr.write("checklist: error: %r\n" % (exc,))
        log("cli error: %r" % (exc,))
        return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:
        log("fatal: %r" % (exc,))
        sys.exit(0)
