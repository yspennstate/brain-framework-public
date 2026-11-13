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
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import time

HOME =os.path.expanduser("~")
_BRAIN_TREE = os.path.join(HOME, "brain-repo", "12_cognitive_architecture", "task_checklist")
STORE = (os.environ.get("TASK_CHECKLIST_HOME")
         or (_BRAIN_TREE if os.path.isdir(os.path.dirname(_BRAIN_TREE)) else os.path.join(HOME, ".claude", "task_checklist")))
LEDGER = os.path.join(STORE, "ledger.jsonl")
STATE = os.path.join(STORE, "state.json")
VIEW = os.path.join(STORE, "CHECKLIST.md")
LOCK = os.path.join(STORE, ".lock")
LOG = os.path.join(STORE, "hook.log")
# 1 Nov: the owner's words past ASKED_CAP chars live whole in ASKED_FULL (append-only), not in state.json. Loop ticks stored
# their whole prompt on every entry: asked_words was 23.7 of 31.7 MB, and every hook rewrites state.json under the lock.
ASKED_FULL = os.path.join(STORE, "asked_full.jsonl")
ASKED_CAP = 1500
_LIVE_STORES = {os.path.normcase(os.path.abspath(p)) for p in (_BRAIN_TREE, os.path.join(HOME, ".claude", "task_checklist"))}
MESH_DB = os.path.join(HOME, ".agents", "coordination", "agent_mesh.sqlite3")

CLI = "python %s" % os.path.abspath(__file__).replace("\\", "/")
MIN_ASK_CHARS = 25
MAX_QUESTION_CHARS = 160      # longer than this and it is an ask that mentions the list, not a question about it
                              # (the owner's questions run 20-90 characters; the owner's asks 200-900)
DIGEST_MAX_OTHERS = 8
# Claude Code hands the model at most 10,000 chars of one hook command's output inline; above that it persists the
# text to a file and shows a 2 KB preview (measured 2026-09-20). The whole list is 290 KB, so the hook prints the
# top of it - open entries first - under this cap and names the file for the rest. The dispatcher merges this hook
# with the sense hooks on the same prompt, hence well under 10,000.
CONTEXT_CAP = 7000
# an automated delivery probe of a hook (this fleet's own test prompt) is never one of the owner's asks
PROBE_RX = re.compile(r"^\s*(\[probe\]|Automated delivery probe of a hook\b"
                      # a headless worker's batch prompt is the fleet's, not the owner's (2026-09-23: one entry per picture batch)
                      # (2026-09-24: any picture-maker brief - "for YouTube thumbnails" named no documentary and put
                      # seven Codex briefs on the owner's list as the owner's asks, T-20260924-080..083 and 090..092)
                      r"|\[headless-worker\]|You are (?:a|the) picture maker\b"
                      # the harness's continuation turn after a usage-limit reset (2026-09-23: one entry per session)
                      r"|Your claude\.ai usage limit has reset\b"
                      # a Codex liveness ping (2026-11-03: 440 open entries, one per codex-01a1... ping session,
                      # 2,787 open in all - they buried the owner's real asks on the board's OPEN_TASKS export)
                      r"|Reply with the single word OK\.?\s*$"
                      # the directive coordinator's round prompt to its Codex workers (313 open entries, 15-25 Oct)
                      r"|=== OPEN CALLS \(read this first\) ==="
                      # Codex worker stage and relaunch prompts written by runners (3 Nov: 628 FEI "Next stage", 357 series
                      # keeper "It is HH:MM local. Your last run ended", 25 "You are A033, one of 100 Codex agents", 13 resume)
                      r"|Next stage \(\d{4}-\d\d-\d\d \d\d:\d\d UTC\)\. Read "
                      r"|It is \d\d:\d\d local\. Your last run (?:ended|stopped)\b"
                      r"|You are A\d+, one of \d+ Codex agents\b"
                      r"|Continue from where you stopped: read your STATUS\.md)", re.I)

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
# <cross-session-message> is the form a peer's relay takes since the 2026-09-24 restart (T-20260924-042 and five more
# were filed from one as "the owner's" asks under the receiving agents)
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


def _pid_alive(pid):
    """Whether a process id names a running process. Never os.kill(pid, 0) on Windows: signal 0 there is CTRL_C_EVENT."""
    if pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except OSError:
            return True
    import ctypes
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x1000, False, pid)          # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ctypes.GetLastError() == 5            # access denied: it exists
    try:
        code = ctypes.c_ulong()
        return bool(k32.GetExitCodeProcess(h, ctypes.byref(code))) and code.value == 259   # STILL_ACTIVE
    finally:
        k32.CloseHandle(h)


def _lock_holder_alive():
    """The lock file holds its writer's pid. Unreadable or unparsable counts as alive: a wrong steal costs the list."""
    try:
        with io.open(LOCK, "rb") as fh:
            return _pid_alive(int(fh.read(32).strip() or b"0"))
    except (OSError, ValueError):
        return True


# HOLD THE LOCK AT FULL SPEED (11 Nov 2026). After the orjson save (00:17) every caller held the lock
# under 3 s except one: poem_carry_on.py's `note`, launched from a scheduled task, held it 41 s at 00:22 and 101.8 s at
# 00:47, and every hook that wanted the list in those minutes failed. Its entry was stamped 63 s after it took the lock:
# loading the 15 MB state, 0.3 s elsewhere, took a minute. A scheduled task starts at BelowNormal with low I/O and memory
# priority, and Windows power-throttles a hidden low-priority process on a busy box, so the holder barely ran while
# thirty sessions waited on it. While this process holds the lock it runs at Normal CPU, I/O and memory priority and is
# not throttled; on release it gets back exactly what it had. Nothing here needs a privilege; any failure is ignored.
_BOOST_SAVED = {}


def _boost_while_locked(on):
    if os.name != "nt":
        return
    try:
        import ctypes
        k, nt = ctypes.windll.kernel32, ctypes.windll.ntdll
        h = ctypes.c_void_p(k.GetCurrentProcess())

        class _Throttle(ctypes.Structure):
            _fields_ = [("Version", ctypes.c_ulong), ("ControlMask", ctypes.c_ulong), ("StateMask", ctypes.c_ulong)]

        if on:
            _BOOST_SAVED.clear()
            cls = k.GetPriorityClass(h)
            if cls in (0x40, 0x4000) and k.SetPriorityClass(h, 0x20):          # IDLE / BELOW_NORMAL -> NORMAL
                _BOOST_SAVED["cls"] = cls
            io_p = ctypes.c_ulong(0)
            if nt.NtQueryInformationProcess(h, 33, ctypes.byref(io_p), 4, None) == 0 and io_p.value < 2:
                if nt.NtSetInformationProcess(h, 33, ctypes.byref(ctypes.c_ulong(2)), 4) == 0:   # ProcessIoPriority
                    _BOOST_SAVED["io"] = io_p.value
            mem = ctypes.c_ulong(0)
            if k.GetProcessInformation(h, 0, ctypes.byref(mem), 4) and mem.value < 5:
                if k.SetProcessInformation(h, 0, ctypes.byref(ctypes.c_ulong(5)), 4):         # ProcessMemoryPriority
                    _BOOST_SAVED["mem"] = mem.value
            old = _Throttle(1, 0, 0)
            if k.GetProcessInformation(h, 4, ctypes.byref(old), ctypes.sizeof(old)):         # ProcessPowerThrottling
                new = _Throttle(1, 1, 0)                                                     # execution speed: not throttled
                if k.SetProcessInformation(h, 4, ctypes.byref(new), ctypes.sizeof(new)):
                    _BOOST_SAVED["throttle"] = (old.Version, old.ControlMask, old.StateMask)
        else:
            saved = dict(_BOOST_SAVED)
            _BOOST_SAVED.clear()
            if "throttle" in saved:
                old = _Throttle(*saved["throttle"])
                k.SetProcessInformation(h, 4, ctypes.byref(old), ctypes.sizeof(old))
            if "mem" in saved:
                k.SetProcessInformation(h, 0, ctypes.byref(ctypes.c_ulong(saved["mem"])), 4)
            if "io" in saved:
                nt.NtSetInformationProcess(h, 33, ctypes.byref(ctypes.c_ulong(saved["io"])), 4)
            if "cls" in saved:
                k.SetPriorityClass(h, saved["cls"])
    except Exception:
        pass


class _Lock(object):
    """A lock file with a short wait; a lock older than 60 s is treated as abandoned. A hook waits WAIT seconds; the
    CLI waits 30 (set in main): with state.json at 31 MB a save holds the lock for seconds on a loaded box, and the
    4 s wait failed about 2,000 times a day in October, by-hand done/note calls among them (30 Oct)."""
    WAIT = 4.0

    def __enter__(self):
        os.makedirs(STORE, exist_ok=True)
        deadline = time.time() + self.WAIT
        while True:
            try:
                fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode("ascii"))
                os.close(fd)
                _LOCK_DEPTH[0] += 1
                if _LOCK_DEPTH[0] == 1:
                    _LOCK_T0[0] = time.time()
                    _boost_while_locked(True)
                return self
            except FileExistsError:
                try:
                    # A lock older than 60 s was stolen whatever its holder was doing. On 1 Nov a 31 MB save on the loaded
                    # box ran past 60 s, a second writer took the lock and read state.json mid-replace, the read failed,
                    # and load_state's fallback saved a day-old copy over the list. Steal only from a dead holder, or
                    # from one that has held it 15 minutes.
                    age = time.time() - os.path.getmtime(LOCK)
                    if age > 900 or (age > 60 and not _lock_holder_alive()):
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
        _LOCK_DEPTH[0] = max(0, _LOCK_DEPTH[0] - 1)
        if _LOCK_DEPTH[0] == 0 and _BOOST_SAVED:
            _boost_while_locked(False)
        if _LOCK_DEPTH[0] == 0 and _LOCK_T0[0]:
            held = time.time() - _LOCK_T0[0]
            _LOCK_T0[0] = 0.0
            if held > 3.0:
                # 2 Nov 22:3x: refusals continued at ~1/4 min after the reader fix; name the long holders
                try:
                    log("lock held %.1f s by pid %d: %s" % (held, os.getpid(), " ".join(sys.argv[1:3])[:80]))
                except Exception:
                    pass
        if _LOCK_DEPTH[0] == 0 and _PENDING_VIEW:
            st, mt = _PENDING_VIEW.pop("st"), _PENDING_VIEW.pop("mtime")
            _PENDING_VIEW.clear()
            # 1 Nov: rendering CHECKLIST.md (5.7 MB) under the lock was a third of every hold. It is written after the
            # lock is released, and only if no later save has landed: the last writer of a burst always writes it.
            if _state_mtime() == mt:
                if not (_ASYNC_VIEW[0] and _spawn_view_render()):
                    _write_view_now(st)


# write_view() inside a held lock leaves its state here for _Lock.__exit__ to render once the lock is released
_LOCK_DEPTH = [0]
_LOCK_T0 = [0.0]
_PENDING_VIEW = {}

# 10 Nov: rendering and writing CHECKLIST.md (6.7 MB, 7,345 entries) cost 0.2-0.5 s of every saving prompt and every
# Stop, inside the hook the prompt waits on. A hook on the live store hands it to one hidden child instead
# (--render-view); the child renders from state.json, then renders again if a save landed meanwhile, so the last
# writer of a burst is always shown, seconds later. The CLI and the selftest still render in their own process.
# TASK_CHECKLIST_VIEW_SYNC=1 restores the old way.
_ASYNC_VIEW = [False]
VIEW_RENDER_LOCK = os.path.join(STORE, ".render.lock")


def _render_child_alive():
    try:
        if time.time() - os.path.getmtime(VIEW_RENDER_LOCK) > 300:
            return False
        with io.open(VIEW_RENDER_LOCK, "rb") as fh:
            return _pid_alive(int(fh.read(32).strip() or b"0"))
    except (OSError, ValueError):
        return False


def _spawn_view_render():
    """True if a render child is running or was started (hidden, own group, out of the hook's job when allowed)."""
    if _render_child_alive():
        return True
    cmd = [sys.executable, "-s", os.path.abspath(__file__), "--render-view"]
    env = dict(os.environ, TASK_CHECKLIST_HOME=STORE)
    kw = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True, env=env)
    if os.name == "nt":
        base = 0x08000000 | 0x00000200      # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
        for flags in (base | 0x01000000, base):     # + CREATE_BREAKAWAY_FROM_JOB when the job allows it
            try:
                subprocess.Popen(cmd, creationflags=flags, **kw)
                return True
            except Exception:
                continue
        return False
    try:
        subprocess.Popen(cmd, start_new_session=True, **kw)
        return True
    except Exception:
        return False


def _render_view_main():
    for _ in range(6):
        try:
            fd = os.open(VIEW_RENDER_LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode("ascii"))
            os.close(fd)
        except FileExistsError:
            if _render_child_alive():
                return 0
            try:
                os.remove(VIEW_RENDER_LOCK)
            except OSError:
                pass
            continue
        except OSError as exc:
            log("render child: no lock (%r)" % (exc,))
            return 0
        try:
            mt = _state_mtime()
            _write_view_now(load_state())
        except Exception as exc:
            log("render child failed: %r" % (exc,))
            mt = _state_mtime()
        finally:
            try:
                os.remove(VIEW_RENDER_LOCK)
            except OSError:
                pass
        # the lock is gone before this check: a save that saw the lock held is seen here, a later one starts a child
        if _state_mtime() == mt:
            return 0
    return 0


# BURSTS OF STOPS ARE WRITTEN ONCE (11 Nov 2026). A Stop on an open entry loaded and saved the whole
# state (16 MB: about 85 + 55 ms, and seconds while another holder had the lock) to change one entry's last words. In
# the ledger of 8-11 Nov, 44 pct of the Stops on an entry came under 30 s after the one before (a Stop hook sends the
# agent back to work) and 49 pct under 60 s. On the live store such a Stop now leaves its words in a small file of its
# own and returns; one hidden child applies every session's pending Stops in a single locked load and save once the
# window has passed. No Stop's words are lost; the list shows them up to about a minute later. A closing line, a named
# ask, a session with no full Stop in the window, or a prompt since that Stop still takes the full path.
# TASK_CHECKLIST_STOP_COALESCE=0 turns it off; another number is the window in seconds (default 60 on the live store,
# 0 elsewhere, so the selftest still sees every Stop whole).
def _stop_window():
    v = os.environ.get("TASK_CHECKLIST_STOP_COALESCE")
    if v is None:
        return 60.0 if _ASYNC_VIEW[0] else 0.0
    try:
        return max(0.0, float(v))
    except ValueError:
        return 0.0


def _stop_dir():
    return os.path.join(os.path.dirname(STATE), ".stop_pending")      # follows STATE, like the session sidecar


def _stop_flush_lock():
    return os.path.join(_stop_dir(), ".flush.lock")


def _stop_mark(session, tid):
    """After a full Stop, inside the lock: the entry this session's Stops touch and when they were last written whole.
    Pending words of this session are older than the ones just written, so they go."""
    if not session or _stop_window() <= 0:
        return
    try:
        d = _stop_dir()
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, session[:12] + ".mark")
        if tid:
            tmp = "%s.%d.tmp" % (p, os.getpid())
            with io.open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"ns": time.time_ns(), "id": tid}, fh)
            _replace_retrying(tmp, p, tries=20)
        else:
            _stop_unmark(session)
        for f in os.listdir(d):
            if f.startswith(session[:12] + ".") and f.endswith(".pend"):
                try:
                    os.remove(os.path.join(d, f))
                except OSError:
                    pass
    except Exception as exc:
        log("stop mark not written: %r" % (exc,))


def _stop_unmark(session):
    """A prompt may open a new entry, so the session's next Stop takes the full path."""
    if session:
        try:
            os.remove(os.path.join(_stop_dir(), session[:12] + ".mark"))
        except OSError:
            pass


def _stop_coalesce(session, agent, said_full, said):
    """True when this Stop's words were left for the flush child instead of a whole-state save."""
    w = _stop_window()
    if w <= 0 or not session or not said or closes_task(said_full) or _NAMED_ASK_RX.search(said_full or ""):
        return False
    try:
        d = _stop_dir()
        with _open_shared(os.path.join(d, session[:12] + ".mark"), binary=True) as fh:
            mark = json.loads(fh.read())
        ns = time.time_ns()
        if not mark.get("id") or (ns - int(mark["ns"])) / 1e9 >= w:
            return False
        p = os.path.join(d, "%s.%d.pend" % (session[:12], ns))
        with io.open(p + ".tmp", "w", encoding="utf-8") as fh:
            json.dump({"ns": ns, "session": session[:12], "id": mark["id"], "by": agent, "at": now_min(), "said": said},
                      fh, ensure_ascii=False)
        os.replace(p + ".tmp", p)
    except Exception:
        return False
    _spawn_stop_flush()
    return True


def _stop_flush_alive():
    lk = _stop_flush_lock()
    try:
        if time.time() - os.path.getmtime(lk) > 300:
            return False
        with io.open(lk, "rb") as fh:
            return _pid_alive(int(fh.read(32).strip() or b"0"))
    except (OSError, ValueError):
        return False


def _spawn_stop_flush():
    if _stop_flush_alive():
        return True
    cmd = [sys.executable, "-s", os.path.abspath(__file__), "--flush-stops"]
    env = dict(os.environ, TASK_CHECKLIST_HOME=STORE, TASK_CHECKLIST_STOP_COALESCE=str(_stop_window()))
    kw = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True, env=env)
    if os.name == "nt":
        base = 0x08000000 | 0x00000200      # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
        for flags in (base | 0x01000000, base):     # + CREATE_BREAKAWAY_FROM_JOB when the job allows it
            try:
                subprocess.Popen(cmd, creationflags=flags, **kw)
                return True
            except Exception:
                continue
        return False
    try:
        subprocess.Popen(cmd, start_new_session=True, **kw)
        return True
    except Exception:
        return False


def _pending_stops():
    d = _stop_dir()
    try:
        return sorted(f for f in os.listdir(d) if f.endswith(".pend"))
    except OSError:
        return []


# A LOOP TICK'S REOPEN IS WRITTEN WITH THE NEXT SAVE (11 Nov 2026). A loop session closes its entry
# every round, and the next tick of the same words reopened it with a whole-state load and save under the lock (15 MB:
# 73 such reopens in 47 minutes on 11 Nov; the checklist hook's median prompt was 35 ms, its mean 277 ms). When the
# sidecar shows the session has no open entry and its newest entry is this ask, the prompt now leaves the reopen in a
# small file of its own and returns. The same holds when the session alternates between two loop asks and this tick
# names its other open entry (only a note and the session's 'latest' change; 11 Nov 18:0x, 600-3,100 ms a tick). Every locked load_state folds pending reopens in before it changes anything, so an
# agent's `done`, a closing-line Stop or the flush child writes the reopen first and then its own change, in order;
# state.json keeps the newest reopen applied per session, so a load that is never saved cannot apply one twice. The
# list shows the reopen up to about a minute later. Off where Stop coalescing is off (TASK_CHECKLIST_STOP_COALESCE=0,
# the selftest's store), and with TASK_CHECKLIST_NO_FAST=1.
def _pending_reopens(session=None):
    try:
        fs = sorted(f for f in os.listdir(_stop_dir()) if f.endswith(".reopen"))
    except OSError:
        return []
    return [f for f in fs if f.startswith(session[:12] + ".")] if session else fs


_FOLDED = {"files": [], "events": []}      # what the last locked load folded in; save_state removes and records them


def _fold_reopens(st):
    _FOLDED["files"], _FOLDED["events"] = [], []
    files = _pending_reopens()
    if not files:
        return
    d = _stop_dir()
    recs = []
    for f in files:
        p = os.path.join(d, f)
        try:
            with _open_shared(p, binary=True) as fh:
                recs.append(json.loads(fh.read()))
            _FOLDED["files"].append(f)
        except Exception:
            try:
                if time.time() - os.path.getmtime(p) > 60:
                    _FOLDED["files"].append(f)        # unreadable for a minute: damaged, not being written
            except OSError:
                pass
    recs.sort(key=lambda r: int(r.get("ns") or 0))
    applied = st.setdefault("reopens_applied", {})
    for r in recs:
        s, ns = r.get("session") or "", int(r.get("ns") or 0)
        if not s or ns <= int(applied.get(s) or 0):
            continue
        applied[s] = ns
        e = st["entries"].get(r.get("id") or "")
        if not e:
            continue
        by, at, iso = r.get("by") or "", r.get("at") or now_min(), r.get("iso") or now_iso()
        if e.get("status") != "open":
            e["status"], e["done_at"], e["seen"], e["seen_at"] = "open", None, False, None
            _FOLDED["events"].append({"at": iso, "kind": "open", "id": e["id"], "by": by, "note": ""})
        txt = "asked again (time %d)" % int(r.get("n") or 2)
        e["updates"].append({"at": at, "by": by, "note": txt})
        e["updates"] = e["updates"][-40:]
        e["last_activity"] = at
        _FOLDED["events"].append({"at": iso, "kind": "note", "id": e["id"], "by": by, "note": txt})
        st["sessions"].setdefault(s, {})["latest"] = e["id"]
    cut = time.time_ns() - 3 * 86400 * 10 ** 9
    for s in [k for k, v in applied.items() if int(v or 0) < cut]:
        del applied[s]


def _saved_folded():
    """After a save has replaced state.json: the ledger rows of what it folded in, and its reopen files go."""
    files, events = _FOLDED["files"], _FOLDED["events"]
    _FOLDED["files"], _FOLDED["events"] = [], []
    for ev in events:
        try:
            append_ledger(ev)
        except Exception as exc:
            log("reopen ledger row not written: %r" % (exc,))
    for f in files:
        try:
            os.remove(os.path.join(_stop_dir(), f))
        except OSError:
            pass


def _defer_reopen(session, text, agent):
    """True when this prompt's reopen of the session's done entry was left for the next save (and printed)."""
    if _stop_window() <= 0 or not session or os.environ.get("TASK_CHECKLIST_NO_FAST") == "1":
        return False
    try:
        with _open_shared(_side_path(), binary=True) as fh:
            side = json.loads(fh.read())
        if side.get("stamp") != _state_stamp():
            return False
        s = session[:12]
        per = side.get("k", {}).get(s)
        if not per:
            return False
        words = " ".join(text.split())
        cands = [per[k] for k in {_ask_key(words), _ask_key(redact(words))} if k in per]
        if not cands:
            return False
        hit = max(cands, key=lambda c: c[0])          # repeated_ask: the newest entry with these words
        d = _stop_dir()
        mine = []
        for f in _pending_reopens(session):
            try:
                with _open_shared(os.path.join(d, f), binary=True) as fh:
                    mine.append(json.loads(fh.read()).get("id"))
            except Exception:
                return False
        if side.get("stamp") != _state_stamp():
            return False                 # a save folded the pending files meanwhile: the full path counts right
        latest = mine[-1] if mine else (side.get("s", {}).get(s) or [None])[0]
        if latest == hit[0]:
            return True                  # the session's open entry (pending or saved) is this ask: nothing to write
        n = int(hit[1]) + 2 + mine.count(hit[0])
        os.makedirs(d, exist_ok=True)
        ns = time.time_ns()
        p = os.path.join(d, "%s.%d.reopen" % (s, ns))
        with io.open(p + ".tmp", "w", encoding="utf-8") as fh:
            json.dump({"ns": ns, "session": s, "id": hit[0], "by": agent, "at": now_min(), "iso": now_iso(), "n": n}, fh)
        os.replace(p + ".tmp", p)
    except Exception:
        return False
    _spawn_stop_flush()
    print("[checklist] The owner's repeated ask is %s (time %d), reopened: \"%s\"" % (hit[0], n, short(text, 120)))
    print("  Close each round with: %s done %s \"<result>\" - the rounds stay as dated updates on the one entry." % (CLI, hit[0]))
    return True


def _apply_pending_stops():
    """Inside the lock: every pending Stop as its ledger event, and each session's newest words on its entry, in one
    load and save. Words older than the session's last full Stop are dropped (that Stop wrote newer ones)."""
    d = _stop_dir()
    files = _pending_stops()
    if not files and not _pending_reopens():
        return 0
    recs = []
    for f in files:
        try:
            with io.open(os.path.join(d, f), encoding="utf-8") as fh:
                recs.append(json.load(fh))
        except Exception:
            pass
    recs.sort(key=lambda r: r.get("ns", 0))
    st = load_state()
    applied, marks = 0, {}
    for r in recs:
        s = r.get("session") or ""
        if s not in marks:
            try:
                with _open_shared(os.path.join(d, s + ".mark"), binary=True) as fh:
                    marks[s] = json.loads(fh.read())
            except Exception:
                marks[s] = None
        m = marks[s]
        if m and int(m.get("ns", 0)) > int(r.get("ns", 0)):
            continue
        e = st["entries"].get(r.get("id") or "")
        if not e or e.get("status") != "open":
            continue
        e["last_said"] = {"at": r.get("at") or now_min(), "text": r.get("said") or ""}
        touch(st, e["id"], r.get("by") or "")
        _event(st, "stop", e["id"], r.get("by") or "", (r.get("said") or "")[:200])
        applied += 1
    folded = len(_FOLDED["files"])
    if applied or folded:
        save_state(st)
        write_view(st)
    applied += folded
    for f in files:
        try:
            os.remove(os.path.join(d, f))
        except OSError:
            pass
    return applied


def _flush_stops_main():
    """The hidden child: wait out the window, apply what is pending, again while more arrives, then go."""
    _ASYNC_VIEW[0] = (os.path.normcase(os.path.abspath(STORE)) in _LIVE_STORES
                      and os.environ.get("TASK_CHECKLIST_VIEW_SYNC") != "1")
    _Lock.WAIT = 30.0
    w = _stop_window() or 60.0
    lk = _stop_flush_lock()
    for _ in range(240):
        try:
            os.makedirs(_stop_dir(), exist_ok=True)
            fd = os.open(lk, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode("ascii"))
            os.close(fd)
        except FileExistsError:
            if _stop_flush_alive():
                return 0
            try:
                os.remove(lk)
            except OSError:
                pass
            continue
        except OSError as exc:
            log("stop flush: no lock (%r)" % (exc,))
            return 0
        try:
            time.sleep(w)
            with _Lock():
                n = _apply_pending_stops()
            log("stop flush: %d pending Stop(s) applied" % n) if n else None
        except Exception as exc:
            log("stop flush failed: %r" % (exc,))
        finally:
            try:
                os.remove(lk)
            except OSError:
                pass
        # the lock is gone before this check: a Stop that saw it held is seen here, a later one starts a child
        if not _pending_stops() and not _pending_reopens():
            break
    try:                                       # marks of sessions quiet for a day
        d = _stop_dir()
        for f in os.listdir(d):
            p = os.path.join(d, f)
            if f.endswith(".mark") and time.time() - os.path.getmtime(p) > 86400:
                os.remove(p)
    except OSError:
        pass
    return 0


def _state_mtime():
    try:
        return os.path.getmtime(STATE)
    except OSError:
        return None


def _backup_path():
    """The hourly copy forced to disk; see save_state. Read from STATE at call time: selftest() rebinds STATE to a temp
    store, and a path fixed at import let its 513-byte test state overwrite the live 15 MB copy (23 Oct 13:48)."""
    return STATE + ".hourly"


def _win32():
    """kernel32 with the two calls the shared read and the POSIX replace need, or None off Windows."""
    if os.name != "nt":
        return None
    k32 = globals().get("_K32")
    if k32 is None:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateFileW.restype = wintypes.HANDLE
        k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                                    wintypes.DWORD, wintypes.HANDLE]
        k32.SetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        globals()["_K32"] = k32
    return k32


def _open_shared(path, binary=False):
    """Open for reading with FILE_SHARE_DELETE, so a writer's POSIX replace never waits on this reader (2 Nov 21:3x:
    plain open() readers made os.replace fail for up to 28.6 s while the checklist lock was held; the reader keeps
    the old file's bytes, a whole snapshot). Falls back to a plain open off Windows or on any failure."""
    k32 = _win32()
    if k32 is not None:
        try:
            import ctypes, msvcrt
            h = k32.CreateFileW(os.path.abspath(path), 0x80000000, 7, None, 3, 0x80, None)   # GENERIC_READ, share RWD
            if h not in (None, ctypes.c_void_p(-1).value):
                fd = msvcrt.open_osfhandle(h, os.O_RDONLY | os.O_BINARY)
                raw = os.fdopen(fd, "rb")
                return raw if binary else io.TextIOWrapper(raw, encoding="utf-8")
            err = ctypes.get_last_error()
            if err in (2, 3):
                raise FileNotFoundError(2, "No such file", path)
        except FileNotFoundError:
            raise
        except Exception:
            pass
    return io.open(path, "rb") if binary else io.open(path, encoding="utf-8")


def _posix_replace(src, dst):
    """Rename src over dst with FILE_RENAME_FLAG_POSIX_SEMANTICS: succeeds while readers hold dst open with share-delete
    (they keep the old bytes). Returns False when the call is unavailable here; raises PermissionError while a reader
    without share-delete holds dst."""
    k32 = _win32()
    if k32 is None:
        return False
    import ctypes
    from ctypes import wintypes
    h = k32.CreateFileW(os.path.abspath(src), 0x00010000 | 0x80000000, 7, None, 3, 0x80, None)   # DELETE|GENERIC_READ
    if h in (None, ctypes.c_void_p(-1).value):
        err = ctypes.get_last_error()
        globals()["_REPL_WHY"] = "src %d" % err
        if err in (5, 32):     # the fresh temp is held (a scanner reading the new file): wait, do not fall back
            raise PermissionError(err, "temp held", src)
        return False
    try:
        name = os.path.abspath(dst)

        class _Info(ctypes.Structure):
            _fields_ = [("Flags", wintypes.DWORD), ("RootDirectory", wintypes.HANDLE),
                        ("FileNameLength", wintypes.DWORD), ("FileName", ctypes.c_wchar * (len(name) + 1))]
        info = _Info(0x1 | 0x2, None, len(name) * 2, name)    # REPLACE_IF_EXISTS | POSIX_SEMANTICS
        if k32.SetFileInformationByHandle(h, 22, ctypes.byref(info), ctypes.sizeof(info)):   # FileRenameInfoEx
            return True
        err = ctypes.get_last_error()
    finally:
        k32.CloseHandle(h)
    globals()["_REPL_WHY"] = "dst %d" % err
    if err in (5, 32):
        raise PermissionError(err, "replace blocked by a reader", dst)
    return False


try:
    import orjson as _orjson
except Exception:
    try:
        import _usersite  # noqa: F401 - hooks run `python -s`; orjson lives in the Roaming user-site
        import orjson as _orjson
    except Exception:
        _orjson = None


def _jloads(raw):
    """11 Nov: state.json is 15 MB; the stdlib parse took 0.22-0.36 s and the indented dump 0.29-0.36 s on every
    registering prompt and Stop, inside the lock. orjson does both in 0.09 s and 0.04 s and gives equal objects on the
    live state. Anything orjson refuses (a BOM, NaN) goes through the stdlib as before."""
    if _orjson is not None:
        try:
            return _orjson.loads(raw)
        except Exception:
            pass
    return json.loads(raw.decode("utf-8-sig") if isinstance(raw, bytes) else raw)


def _jdump_bytes(st, indent):
    if _orjson is not None:
        try:
            return _orjson.dumps(st, option=_orjson.OPT_SORT_KEYS | _orjson.OPT_NON_STR_KEYS
                                 | (_orjson.OPT_INDENT_2 if indent else 0))
        except Exception:
            pass
    return json.dumps(st, ensure_ascii=False, indent=1 if indent else None, sort_keys=True).encode("utf-8")


def _read_json(path):
    # read the bytes and close before parsing: the file is open for the read only, not the parse
    with _open_shared(path, binary=True) as fh:
        raw = fh.read()
    return _jloads(raw)


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


def _state_looks_whole(path):
    """True when the file has the shape of a complete JSON object: its first byte is '{' and its last non-blank byte '}'.
    A read that failed on such a file failed for a passing reason (another writer's replace, memory), not damage.
    A file that cannot even be opened counts as whole: falling back on it is the one move that loses the list."""
    try:
        n = os.path.getsize(path)
        with _open_shared(path, binary=True) as fh:
            head = fh.read(1)
            fh.seek(max(0, n - 64))
            tail = fh.read()
        return head == b"{" and tail.rstrip().endswith(b"}")
    except OSError:
        return True


def load_state():
    # 1 Nov: 'state.json unreadable' was logged 8 times in 4 hours with the file intact. Each time the hourly copy (or a
    # leftover temp a day old) was loaded and then SAVED over the list, dropping every change since that copy (19
    # entries at 03:01). A failed read of a whole file is retried, then refused; only a damaged file falls back.
    st, last = None, None
    for k in range(10):
        try:
            if not os.path.exists(STATE):
                st = {}
                break
            if os.path.getsize(STATE) == 0:
                st = {}
                break
            st = _read_json(STATE)
            break
        except Exception as exc:
            last = exc
            if not _state_looks_whole(STATE):
                log("state.json is damaged (%r); falling back" % (exc,))
                st = _fallback_state()
                break
            time.sleep(0.1 + 0.05 * k)
    if st is None:
        log("state.json would not load (%r) though it is whole; refusing, not loading an older copy" % (last,))
        raise RuntimeError("state.json would not load: %r" % (last,))
    st.setdefault("entries", {})
    st.setdefault("sessions", {})
    st.setdefault("seq", {})
    if _LOCK_DEPTH[0] > 0:
        try:
            _fold_reopens(st)
        except Exception as exc:
            _FOLDED["files"], _FOLDED["events"] = [], []
            log("pending reopens not folded: %r" % (exc,))
    return st


def _replace_retrying(tmp, dest, tries=60):
    """os.replace, retried: on Windows it fails with PermissionError while any other process has dest open, and with
    the 9 Oct wave's fifty workers reading state.json on every prompt that cost 33 lost writes in an evening (hook.log)."""
    t0 = time.time()
    for k in range(tries):
        try:
            if not _posix_replace(tmp, dest):
                os.replace(tmp, dest)
            if k:   # 2 Nov 21:2x: measure how long a writer waits on readers (lock holds of 20-28 s seen)
                log("replace waited %.1f s over %d tries (last block: %s)" % (time.time() - t0, k + 1,
                                                                          globals().get("_REPL_WHY", "?")))
            return
        except PermissionError:
            if k == tries - 1:
                log("replace gave up after %.1f s over %d tries (last block: %s)" % (time.time() - t0, tries,
                                                                           globals().get("_REPL_WHY", "?")))
                raise
            time.sleep(0.05 + 0.01 * k)


def _sweep_stale_tmps(keep=2, age=3600):
    """A writer killed mid-dump (a hook timeout on a busy disk) leaves its 17 MB state.json.<pid>.tmp behind: 33 in one
    hour on 24 Oct, about half a gigabyte an hour. After a good save, remove those older than an hour, keeping the newest
    `keep` for _fallback_state.
    11 Nov 2026: the view's CHECKLIST.md.<pid>.tmp is left the same way (66 at 16:21 that day, 70 in
    all, 6.8 MB each, about 470 MB); nothing reads them, so those older than an hour go too, none kept."""
    import glob
    try:
        olds = sorted(glob.glob(STATE + ".*.tmp"), key=os.path.getmtime, reverse=True)[keep:]
        olds += glob.glob(VIEW + ".*.tmp")
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
        data = _jdump_bytes(st, indent=True)
        with io.open(tmp, "wb") as fh:
            fh.write(data)
        _replace_retrying(tmp, STATE)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    _saved_folded()
    _write_session_side(st)
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
            with io.open(btmp, "wb") as fh:
                fh.write(_jdump_bytes(st, indent=False))
                fh.flush()
                os.fsync(fh.fileno())
            _replace_retrying(btmp, bak)
    except Exception as exc:
        log("hourly backup failed: %r" % (exc,))


# THE SAME-ASK FAST PATH (11 Nov 2026). About 60 pct of prompts change nothing here: a loop tick whose
# entry is still open (ledger 00:45-01:45: ~330 prompts, ~130 writes). Each one still parsed the whole 14.8 MB
# state.json under the lock (145 ms idle, 500+ ms on the loaded box) to learn that. save_state now also writes, per
# session, the entry latest_open_for_session would return and its ask key, stamped with state.json's size and mtime
# right after the replace. A prompt whose stamp still matches and whose words have that key returns at once. Any other
# writer of state.json changes the stamp, and the hook falls back to the full load.
def _side_path():
    return os.path.join(os.path.dirname(STATE), "session_latest.json")   # follows STATE, so a test store keeps its own


def _state_stamp():
    s = os.stat(STATE)
    return [s.st_size, s.st_mtime_ns]


def _write_session_side(st):
    try:
        side, bykey = {}, {}
        for e in st["entries"].values():
            s = e.get("session") or ""
            if not s:
                continue
            k = e.get("asked_key") or _ask_key(e["asked_words"])
            per = bykey.setdefault(s, {})
            if k not in per or e["id"] > per[k]["id"]:
                per[k] = e
            if e.get("status") == "open" and (s not in side or e["id"] > side[s][0]):
                side[s] = [e["id"], k]
        cut = time.strftime("%Y-%m-%d %H:%M", time.localtime(time.time() - 3 * 86400))
        recent = {}
        for s, per in bykey.items():
            for k, e in per.items():
                if (e.get("last_activity") or "") >= cut:
                    recent.setdefault(s, {})[k] = [e["id"], sum(1 for u in e["updates"]
                                                                if u["note"].startswith("asked again"))]
        for s, sess in st["sessions"].items():          # the session's own 'latest' wins when it is open
            e = st["entries"].get(sess.get("latest") or "")
            if e and e.get("status") == "open":
                side[s] = [e["id"], e.get("asked_key") or _ask_key(e["asked_words"])]
        side_path = _side_path()
        tmp = "%s.%d.tmp" % (side_path, os.getpid())
        with io.open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"stamp": _state_stamp(), "s": side, "k": recent}, fh, separators=(",", ":"))
        _replace_retrying(tmp, side_path)
    except Exception as exc:
        log("session sidecar not written: %r" % (exc,))


def _fast_same_ask(session, text):
    """True when the sidecar, still stamped with the current state.json, says this session's open entry is this ask."""
    try:
        with _open_shared(_side_path(), binary=True) as fh:
            side = json.loads(fh.read())
        if side.get("stamp") != _state_stamp():
            return False
        hit = side.get("s", {}).get((session or "")[:12])
        if not hit or _pending_reopens(session):
            return False
        words = " ".join(text.split())
        return hit[1] in (_ask_key(words), _ask_key(redact(words)))
    except Exception:
        return False


def _fast_no_open(session):
    """True when the sidecar, still stamped with the current state.json, says this session has no open entry."""
    if os.environ.get("TASK_CHECKLIST_NO_FAST") == "1" or not session:
        return False
    try:
        with _open_shared(_side_path(), binary=True) as fh:
            side = json.loads(fh.read())
        return (side.get("stamp") == _state_stamp() and session[:12] not in side.get("s", {})
                and not _pending_reopens(session))
    except Exception:
        return False


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
    # (T-20260919-011 and -116 were registered from such text; found in the hourly block 2026-09-20)
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
    r"sk-[A-Za-z0-9]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|github" r"_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16}|xox[baprs]-[A-Za-z0-9-]{10,}"
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


# THE MESH LOOKUP IS CACHED PER SESSION (11 Nov 2026). It ran on every prompt and every Stop of every session, and the
# mesh database (605 MB) keeps a rollback journal, so any agent's write locks readers out: the read waited out its
# timeout, 1.8 s, and that was the whole of the checklist's slow rows at the loop ticks, when every agent writes the
# mesh at once (slow-run trace, 19:22: mt 1,756 and 1,780 ms; load, save and lock a few ms). The answer changes when
# an agent registers, so it is kept MESH_CACHE_TTL_S per session, a miss included; a lookup that fails keeps the last
# answer, and with none the result is (None, None), as a timed-out read always was. TASK_CHECKLIST_MESH_CACHE=0: no cache.
MESH_CACHE_TTL_S = 600.0
MESH_TIMEOUT_S = 0.25


def _mesh_cache_path():
    return os.path.join(os.path.dirname(STATE), "mesh_lookup_cache.json")   # follows STATE, like the session sidecar


def _mesh_lookup(session, agent_hint):
    import sqlite3
    if not os.path.exists(MESH_DB):
        return None, None
    # No SQLite busy wait: on this Windows build a 0.25 s busy timeout cost about 0.75 s on a held lock (its retry sleeps
    # round up to the timer tick, 11 Nov). The read retries against a real MESH_TIMEOUT_S deadline instead.
    con = sqlite3.connect("file:%s?mode=ro" % MESH_DB.replace("\\", "/"), uri=True, timeout=0)
    deadline = time.monotonic() + MESH_TIMEOUT_S

    def q(sql, params):
        while True:
            try:
                return con.execute(sql, params).fetchall()
            except sqlite3.OperationalError as e:
                msg = str(e).lower()
                if ("locked" not in msg and "busy" not in msg) or time.monotonic() + 0.02 >= deadline:
                    raise
                time.sleep(0.02)
    try:
        sid = (session or "")[:8]
        rows = []
        if sid:
            rows = q("select agent_id, task from agents where session_key like ? order by last_seen desc limit 1",
                     ("%" + sid + "%",))
        if not rows and agent_hint:
            rows = q("select agent_id, task from agents where agent_id = ? limit 1", (agent_hint,))
        if rows:
            return rows[0][0], rows[0][1]
    finally:
        con.close()
    return None, None


def mesh_task_for(session, agent_hint=""):
    """Best-effort, read-only: the agent mesh row for this session (agent id, task). Never raises."""
    key = "%s|%s" % ((session or "")[:8], agent_hint or "")
    use_cache = os.environ.get("TASK_CHECKLIST_MESH_CACHE", "1") != "0"
    cache, now = {}, time.time()
    if use_cache:
        try:
            with io.open(_mesh_cache_path(), encoding="utf-8") as fh:
                cache = json.load(fh)
            if not isinstance(cache, dict):
                cache = {}
        except Exception:
            cache = {}
        hit = cache.get(key)
        if isinstance(hit, list) and len(hit) == 3 and 0 <= now - float(hit[2] or 0) < MESH_CACHE_TTL_S:
            return hit[0], hit[1]
    try:
        res = _mesh_lookup(session, agent_hint)
    except Exception:
        hit = cache.get(key)
        if isinstance(hit, list) and len(hit) == 3:
            return hit[0], hit[1]
        return None, None
    if use_cache:
        path = _mesh_cache_path()
        tmp = "%s.%d.tmp" % (path, os.getpid())
        try:
            keep = {k: v for k, v in cache.items()
                    if isinstance(v, list) and len(v) == 3 and 0 <= now - float(v[2] or 0) < 86400}
            keep[key] = [res[0], res[1], now]
            with io.open(tmp, "w", encoding="utf-8") as fh:
                json.dump(keep, fh, ensure_ascii=False)
            os.replace(tmp, path)
        except Exception:
            pass
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    return res


# ----------------------------------------------------------------------------- the model

def _entry(st, tid):
    e = st["entries"].get(tid)
    if not e:
        raise KeyError("no entry %s" % tid)
    return e


def _ask_key(words):
    return hashlib.sha1(words.encode("utf-8", "replace")).hexdigest()[:16]


def _cap_asked(tid, words):
    """The owner's words for state.json: whole up to ASKED_CAP chars; past it the whole text goes to ASKED_FULL first."""
    if len(words) <= ASKED_CAP:
        return words
    with io.open(ASKED_FULL, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"id": tid, "at": now_iso(), "asked_words": words}, ensure_ascii=False) + "\n")
    return words[:ASKED_CAP] + " [... %d more chars; whole ask: show %s]" % (len(words) - ASKED_CAP, tid)


def same_ask(e, text):
    """Is this entry the same ask as text? By key, so a capped entry still matches its loop's next tick."""
    words = " ".join(text.split())
    k = e.get("asked_key") or _ask_key(e["asked_words"])
    return k == _ask_key(words) or k == _ask_key(redact(words))


def full_asked(tid):
    """The whole ask from ASKED_FULL, or None."""
    try:
        with io.open(ASKED_FULL, encoding="utf-8") as fh:
            for line in fh:
                if ('"%s"' % tid) in line:
                    r = json.loads(line)
                    if r.get("id") == tid:
                        return r.get("asked_words")
    except (OSError, ValueError):
        pass
    return None


def cap_existing(st):
    """One-time move of long asks already in state: whole text to ASKED_FULL, the cap stays. Returns (n, chars)."""
    n = chars = 0
    for tid, e in sorted(st["entries"].items()):
        w = e.get("asked_words") or ""
        if "asked_key" not in e:
            e["asked_key"] = _ask_key(w)
        if len(w) > ASKED_CAP and not w.endswith("whole ask: show %s]" % tid):
            e["asked_words"] = _cap_asked(tid, w)
            n += 1
            chars += len(w) - len(e["asked_words"])
    return n, chars


def _event(st, kind, tid, by, note=""):
    if _FOLDED["events"]:
        folded, _FOLDED["events"] = _FOLDED["events"], []
        for x in folded:
            append_ledger(x)
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
    return ("  Still open on your list: %s - if any of those is finished, `%s done <id> \"<result>\"` now (they read the list)."
            % (", ".join(e["id"] for e in olds[-4:]), CLI))


def add(st, asked, by, runtime, session, working="", asked_at=None, source="hook"):
    tid = new_id(st)
    full = redact(" ".join(asked.split()))
    e = {
        "id": tid,
        "asked_at": asked_at or now_min(),
        "asked_words": _cap_asked(tid, full),
        "asked_key": _ask_key(full),
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
    cands = [x for x in st["entries"].values() if x["session"] == (session or "")[:12] and same_ask(x, words)]
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
    scheduled audit)."""
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
    out.append("Updated %s (the owner's clock). %d open, %d done and not yet seen, %d seen." % (now_min(), len(open_), len(done_unseen), len(seen)))
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
    if _LOCK_DEPTH[0] > 0:
        _PENDING_VIEW["st"], _PENDING_VIEW["mtime"] = st, _state_mtime()
        return
    _write_view_now(st)


def _write_view_now(st):
    try:
        tmp = "%s.%d.tmp" % (VIEW, os.getpid())
        with io.open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(render(st))
        _replace_retrying(tmp, VIEW)
    except Exception as exc:
        try:
            os.remove(tmp)
        except Exception:
            pass
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
    # a waking session's own open asks are the top of its goal stack; the fleet's newest eight used to bury them
    # (Mac, 30 Oct: 72 of 137 wakes had an open ask of their own that the digest never named; ported from 303219725)
    mine = [e for e in open_ if session and not exclude_session and e["session"] == session[:12]]
    if mine:
        lines.append("  Yours, open in this session (newest first - the top is what you are on):")
        for e in mine[:3]:
            lines.append("  * %s %s: asked \"%s\" -> %s" % (
                e["id"], e["asked_at"], short(e["asked_words"], 110),
                short(e["working_on"], 90) or "(working-on not stated)"))
        if len(mine) > 3:
            lines.append("  * ... and %d more of yours open." % (len(mine) - 3))
        lines.append("  Other agents:")
    others = [e for e in open_ if e not in mine and (not exclude_session or e["session"] != (exclude_session or "")[:12])]
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
    # code page and turns every em dash in the owner's prompt into mojibake. Read bytes, decode UTF-8.
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
    _TR["ev"], _TR["s"] = event[:4], session[:8]
    # A HARNESS IS NOT HIM (2026-09-23, round 5). Every real Claude and Codex session carries a uuid-shaped id; the
    # regression gate, the brain studies and the dispatcher controls send test prompts through this hook with ids like
    # "regression-gate", "brainstudy-a" and "r5-env-int-C". Nine such prompts sat in the owner's checklist as open asks from
    # agents that do not exist ("State the triangle removal lemma precisely."). On the live store, a session whose id is
    # not uuid-shaped registers nothing and prints nothing; the selftest's temp store is unaffected, and
    # TASK_CHECKLIST_ALLOW_HARNESS=1 lifts it.
    if (session and not re.match(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-", session)
            and os.path.normcase(os.path.abspath(STORE)) in _LIVE_STORES
            and os.environ.get("TASK_CHECKLIST_ALLOW_HARNESS") != "1"):
        return 0
    _ASYNC_VIEW[0] = (os.path.normcase(os.path.abspath(STORE)) in _LIVE_STORES
                      and os.environ.get("TASK_CHECKLIST_VIEW_SYNC") != "1")
    runtime = runtime_arg or ("codex" if payload.get("thread_id") else "claude")
    if "--runtime" not in sys.argv and os.environ.get("CODEX_THREAD_ID"):
        runtime = "codex"
    # the mesh lookup opens a 200 MB sqlite that is often write-locked; a session row that carries mesh_agent skips it.
    # 2 Nov 21:2x: nothing on the live store ever writes that row (remember_mesh has no caller; 0 of 1,383 sessions
    # carry it), so this pre-read was a whole 13 MB state.json load on every prompt and every stop of every session,
    # unlocked, with the file open - and an open reader makes the lock holder's os.replace fail and retry for up to
    # ~20 s (the lock was busy 58 pct of a 90 s sample; 101 'held too long' in the 20:00 hour). On the live store the
    # cache always missed, so skipping the read changes nothing there; a test store still reads it.
    sess0 = {}
    if session and os.path.normcase(os.path.abspath(STORE)) not in _LIVE_STORES:
        sess0 = load_state()["sessions"].get(session[:12], {})
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
        # no open entry for this session and no ask named in the closing line: the locked path below would find no
        # entry and write nothing, so skip its lock and its whole-state parse (about 190 of ~500 Stops an hour)
        if not _NAMED_ASK_RX.search(said_full or "") and _fast_no_open(session):
            return 0
        if _stop_coalesce(session, agent, said_full, said):
            return 0
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
            _stop_mark(session, e["id"] if e and e.get("status") == "open" else "")    # inside the lock: see _stop_mark
        return 0

    if event != "UserPromptSubmit":
        return 0
    _stop_unmark(session)
    text = redact(clean_prompt(str(payload.get("prompt") or payload.get("user_prompt") or payload.get("text") or "")))
    if not text:
        return 0
    # a /goal is an ask in the owner's words, whichever way the harness delivers it - the XML command form (handled in
    # clean_prompt) or plain text "/goal keep watching the movie ..." (the owner's 16:00 prompt, skipped as a slash command)
    m_goal = re.match(r"^/goal\b\s*(.*)$", text, re.S)
    if m_goal:
        text = m_goal.group(1).strip()
        if not text:
            return 0
    elif text.startswith("/") and not text.startswith("/checklist"):
        return 0
    # pasted output, JSON, a log or an email is context, not the ask: keep the owner's sentence, note the paste; a prompt
    # that is only a paste is attached to the session's open entry (T-20260918-054 was a subscription listing
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
    # list instead of registering the ask (caught on the owner's own 14:07 prompt, 2026-09-18).
    # ...and a SHORT imperative that mentions the list ("audit and make sure the agents are using the checklist") is
    # an ask too (caught on the owner's 15:35 prompt the same day). So the question path needs the FORM of a question: it
    # starts with an interrogative, or ends in "?", or is the /checklist command - and it is short.
    # "mark all seen" / "i've seen it" is HIS checkmark, whatever its form - tell the agent to run `seen`, register nothing
    if SEEN_RX.search(text) and len(text) <= MAX_QUESTION_CHARS:
        st = load_state()
        print("[checklist] The owner is marking entries SEEN - that checkmark is the owner's. Run `%s seen <id|all|today>` for the ones they "
              "names (all = every done entry not yet seen), then confirm which ids moved. The list:" % CLI)
        print(render_for_context(st))
        return 0
    mentions = QUESTION_RX.search(text) or text.startswith("/checklist")
    is_question = bool(INTERROGATIVE_RX.match(text)) or text.rstrip().endswith("?") or text.startswith("/checklist")
    if mentions and is_question and (len(text) <= MAX_QUESTION_CHARS or text.startswith("/checklist")):
        st = load_state()
        print("[checklist] The owner is asking about the tasks they gave agents. Answer FROM the checklist below, "
              "in the owner's words per entry, dated; then, if the owner says they have seen some, run `%s seen <id|all>`." % CLI)
        print(render_for_context(st))
        return 0
    print_list_too = bool(mentions)      # an ask ABOUT the checklist still gets the list, after it is registered
    if (session and len(text) >= MIN_ASK_CHARS and not FOLLOWUP_RX.match(text)
            and os.environ.get("TASK_CHECKLIST_NO_FAST") != "1" and _fast_same_ask(session, text)):
        return 0                         # the same ask as the session's open entry: nothing to write (see _side_path)
    # An ask that names the checklist is deferred too (11 Nov 18:4x): the list goes out only when a NEW entry is
    # registered, and a repeat prints the same two lines on either path. Excluding them sent the loops whose words say
    # "checklist" or "task list" through a 15 MB load and save every tick (600-1,000 ms, four sessions on 11 Nov).
    if (session and len(text) >= MIN_ASK_CHARS and not FOLLOWUP_RX.match(text)
            and _defer_reopen(session, text, agent)):
        return 0                         # a loop tick reopening its done entry: written by the next save (see _pending_reopens)

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
        if latest and same_ask(latest, text):
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
    old fallback joined the runtime to the Windows USERNAME, which on this machine is "owner", so every agent note,
    working line and done without --by was recorded as "cli-owner" (2,910 ledger rows by 28 Sep) and read as the owner's."""
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
    sub.add_parser("cap-asked", help="move asks past ASKED_CAP chars to asked_full.jsonl (one-time)")
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
            whole = full_asked(e["id"]) if "whole ask: show" in e["asked_words"] else None
            if whole:
                e = dict(e, asked_words=whole)
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
        elif args.cmd == "cap-asked":
            n, chars = cap_existing(st)
            print("capped %d asks, %d chars moved to %s" % (n, chars, ASKED_FULL))
        save_state(st)
        write_view(st)
    return 0


# ----------------------------------------------------------------------------- selftest

def selftest():
    import shutil
    import tempfile
    global STORE, LEDGER, STATE, VIEW, LOCK, LOG, ASKED_FULL
    tmp = tempfile.mkdtemp(prefix="checklist_selftest_")
    ASKED_FULL = os.path.join(tmp, "asked_full.jsonl")
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
    check("a /goal delivered as plain text registers too (the owner's 16:00 prompt was skipped as a slash command)",
          len(load_state()["entries"]) == 4 and "keep watching the movie" in out7b)
    # the 2026-09-18 14:07 defect: a long ask that mentions the checklist must REGISTER, not print the list
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
    # the owner's 15:35 prompt: a SHORT imperative that mentions the checklist is an ask, and it gets the list as well
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
    # a follow-up after the entry was ticked done reopens it (the owner's "keep working")
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
    planted = "gh" + "p_" + "abcdefghijklmnopqrstuvwxyz" + "0123456789"
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
    longask = "keep improving the recall of the brain and the math brain, step %d of the long loop order. " * 60
    run_hook({"hook_event_name": "UserPromptSubmit", "session_id": "sessL0003", "prompt": longask})
    eL = [x for x in load_state()["entries"].values() if x["session"] == "sessL0003"]
    whole = full_asked(eL[0]["id"]) if eL else None
    check("a long ask is capped in state and kept whole in asked_full.jsonl",
          len(eL) == 1 and len(eL[0]["asked_words"]) < ASKED_CAP + 80 and whole == " ".join(longask.split()))
    run_hook({"hook_event_name": "UserPromptSubmit", "session_id": "sessL0003", "prompt": longask})
    check("the same long ask again (a loop tick) matches its capped entry",
          len([x for x in load_state()["entries"].values() if x["session"] == "sessL0003"]) == 1)
    # the view is rendered after the lock is released, and not at all when a later save landed first
    with _Lock():
        stv = load_state()
        note(stv, sorted(stv["entries"])[0], "view probe", "selftest")
        save_state(stv)
        before = io.open(VIEW, encoding="utf-8").read()
        write_view(stv)
        held = io.open(VIEW, encoding="utf-8").read()
    after = io.open(VIEW, encoding="utf-8").read()
    check("write_view under the lock waits for the release, then writes", held == before and after != before
          and not os.path.exists(LOCK) and not _PENDING_VIEW)
    marker = "VIEW-STALE-PROBE"
    with _Lock():
        stv = load_state()
        save_state(stv)
        write_view(stv)
        time.sleep(0.05)
        io.open(VIEW, "w", encoding="utf-8").write(marker)
        os.utime(STATE, (time.time() + 5, time.time() + 5))     # a later writer's save
    check("a deferred view is skipped when a later save landed", io.open(VIEW, encoding="utf-8").read() == marker)
    with _Lock():
        write_view(load_state())
    check("the next write renders the view again", io.open(VIEW, encoding="utf-8").read() != marker and _LOCK_DEPTH[0] == 0)
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
    if "--render-view" in argv:
        return _render_view_main()
    if "--flush-stops" in argv:
        return _flush_stops_main()
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
    _Lock.WAIT = 30.0
    try:
        return cli_main(argv)
    except KeyError as exc:
        sys.stderr.write("checklist: %s (run `%s list --all` to see the ids)\n" % (exc.args[0] if exc.args else exc, CLI))
        return 1
    except Exception as exc:
        sys.stderr.write("checklist: error: %r\n" % (exc,))
        log("cli error: %r" % (exc,))
        return 1


# A TRACE OF THE SLOW HOOK RUNS (11 Nov 2026). The prompt path is 20-50 ms on its fast and deferred
# branches, yet about one prompt in six still took 1-2 s with nothing printed, and the profile row cannot say why. On
# the live store a hook run of 300 ms or more appends one line to ~/.claude/compute/checklist_slow.jsonl: event,
# session, total, lock wait, load, save, view, and what the two fast checks answered. Capped at 5 MB; never raises.
_TR = {}
_SLOW_LOG = os.path.join(HOME, ".claude", "compute", "checklist_slow.jsonl")


def _tr_wrap(name, key, keep_result=False):
    inner = globals()[name]

    def wrapped(*a, **k):
        t = time.perf_counter()
        r = None
        try:
            r = inner(*a, **k)
            return r
        finally:
            _TR[key] = round(_TR.get(key, 0.0) + (time.perf_counter() - t) * 1e3, 1)
            if keep_result:
                _TR[key + "_r"] = bool(r)
    wrapped.__name__ = inner.__name__
    wrapped.__doc__ = inner.__doc__
    globals()[name] = wrapped


for _n, _k, _r in (("load_state", "ld", False), ("save_state", "sv", False), ("write_view", "vw", False),
                   ("_fast_same_ask", "fa", True), ("_defer_reopen", "df", True),
                   ("mesh_task_for", "mt", False), ("last_said", "ls", False), ("_fast_no_open", "fn", True),
                   ("_stop_coalesce", "sc", True), ("_stop_unmark", "su", False), ("ask_and_paste", "ap", False),
                   ("clean_prompt", "cp", False), ("redact", "rd", False)):
    _tr_wrap(_n, _k, _r)

_Lock_enter = _Lock.__enter__


def _lock_enter_timed(self):
    t = time.perf_counter()
    try:
        return _Lock_enter(self)
    finally:
        _TR["lw"] = round(_TR.get("lw", 0.0) + (time.perf_counter() - t) * 1e3, 1)


_Lock.__enter__ = _lock_enter_timed
_hook_main_untimed = hook_main


def hook_main(runtime_arg):
    _TR.clear()
    t = time.perf_counter()
    try:
        return _hook_main_untimed(runtime_arg)
    finally:
        try:
            ms = (time.perf_counter() - t) * 1e3
            if ms >= 300 and os.path.normcase(os.path.abspath(STORE)) in _LIVE_STORES:
                if not os.path.exists(_SLOW_LOG) or os.path.getsize(_SLOW_LOG) < 5 * 1024 * 1024:
                    row = dict(_TR, at=now_iso(), ms=round(ms, 1))
                    with io.open(_SLOW_LOG, "a", encoding="utf-8") as fh:
                        fh.write(json.dumps(row) + "\n")
        except Exception:
            pass


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:
        log("fatal: %r" % (exc,))
        sys.exit(0)
