"""run_graph.py - keeps the brain graph growing by itself (26 Sep, claude-9209cf81).

His order of 26 Sep 04:28: build the whole edge and diffusion part of the brain now, from the data we have. The capture
(transcripts -> touch events) and the graph store (the learned edges) had run by hand from a session scratchpad; this
driver runs them from their durable home, ~/.claude/brain_graph/, on a schedule:

  capture   every hour: the new bytes of every Claude transcript touched in the last 40 days become touch events,
            appended to events_touch.jsonl (byte offsets in capture_state.json), then the learner and the rest of
            the hourly live lane
  fast      every five minutes (27 Sep, his 17:1x: "the data should be live, i don't understand why we are stuck in
            the past"): the same capture, the memories written since the last pass, and the fast layer
            (live_delta.py) rebuilt on every event the store has not seen yet, which the membrane reads beside it
  build     every six hours: store/ rebuilt from all the events (written to store.new, checked, then swapped in);
            GraphStore(~/.claude/brain_graph/store) is what the membrane, graph_recall.py and the diffusion read.
            The hour either side of any touch of the recall banks is left out (graph_store.BANK_RX), so a ranker tuned
            on a bank against this store is not scored on answers the grading itself taught it

Each run takes a lock the operating system holds (released when the run ends, however it ends), runs BelowNormal and exempt from
Windows power throttling, refuses to build with less than 3 GB of commit headroom, and appends one line to
logs/run_graph.log and the latest state to STATUS.json. Launched windowless by run_graph.vbs (wscript //B, pythonw).
A build or an hourly capture that finds a run in progress waits up to three minutes for it; a fast run skips.
Usage: run_graph.py capture|build|fast|status"""

import sys
if __name__ == "__main__" and any(arg in ("-h", "--help") for arg in sys.argv[1:]):
    print(__doc__ or "No command-line options.")
    raise SystemExit(0)

import ctypes
import ctypes.wintypes as wt
import io
import json
import os
import shutil
import sys
import time
import traceback

HOME = os.path.join(os.path.expanduser("~"), ".claude", "brain_graph")
BIN = os.path.join(HOME, "bin")
EVENTS = os.path.join(HOME, "events_touch.jsonl")
STATE = os.path.join(HOME, "capture_state.json")
STORE = os.path.join(HOME, "store")
RECENT_STORE = os.path.join(HOME, "store_recent")   # the live capture alone; his corrections' edges (build_config)
VERDICTS = os.path.join(HOME, "events_verdicts.jsonl")  # his corrections, repeats, praise (backfill_history.py verdicts)
LOGS = os.path.join(HOME, "logs")
STATUS = os.path.join(HOME, "STATUS.json")
sys.path.insert(0, BIN)


class MEMSTAT(ctypes.Structure):
    _fields_ = [("dwLength", wt.DWORD), ("dwMemoryLoad", wt.DWORD), ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong), ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong), ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


def commit_free_gb():
    m = MEMSTAT()
    m.dwLength = ctypes.sizeof(MEMSTAT)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return m.ullAvailPageFile / 1e9


def polite():
    """BelowNormal, and opted out of power throttling (a throttled windowless job ran 4-10x slower, 25 Sep)."""
    try:
        # the types must be declared: left to guess, ctypes passes GetCurrentProcess()'s pseudo-handle as a 32-bit int
        # and both calls silently fail (measured 26 Sep: the class stayed Normal)
        k32 = ctypes.WinDLL("kernel32")
        k32.GetCurrentProcess.restype = wt.HANDLE
        k32.SetPriorityClass.argtypes = [wt.HANDLE, wt.DWORD]
        k32.SetProcessInformation.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD]
        h = k32.GetCurrentProcess()
        k32.SetPriorityClass(h, 0x00004000)

        class PPTS(ctypes.Structure):
            _fields_ = [("Version", wt.ULONG), ("ControlMask", wt.ULONG), ("StateMask", wt.ULONG)]
        st = PPTS(1, 0x1, 0x0)
        k32.SetProcessInformation(h, 4, ctypes.byref(st), ctypes.sizeof(st))
    except Exception:
        pass


def priorities():
    """This process's CPU class, I/O priority and memory priority. A task at Task Scheduler's default priority 7 runs at
    I/O priority low and memory priority 2 (measured 26 Sep on every such task here), which starved the first hourly
    capture; the tasks are registered at 6 and this line records what a run actually got."""
    try:
        k32, nt = ctypes.windll.kernel32, ctypes.windll.ntdll
        k32.OpenProcess.restype = wt.HANDLE
        k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
        k32.GetPriorityClass.argtypes = [wt.HANDLE]
        k32.GetProcessInformation.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD]
        k32.CloseHandle.argtypes = [wt.HANDLE]
        nt.NtQueryInformationProcess.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.ULONG, ctypes.c_void_p]
        h = k32.OpenProcess(0x1000 | 0x0400, False, os.getpid())
        io = ctypes.c_ulong(99)
        nt.NtQueryInformationProcess(h, 33, ctypes.byref(io), 4, None)

        class MPI(ctypes.Structure):
            _fields_ = [("MemoryPriority", wt.ULONG)]
        mp = MPI(99)
        k32.GetProcessInformation(h, 0, ctypes.byref(mp), ctypes.sizeof(mp))
        cls = {0x40: "Idle", 0x4000: "BelowNormal", 0x20: "Normal"}.get(k32.GetPriorityClass(h), "other")
        k32.CloseHandle(h)
        return "cpu %s, io %s, memory %d" % (cls, {0: "very low", 1: "low", 2: "normal", 3: "high"}.get(io.value, "?"),
                                            mp.MemoryPriority)
    except Exception as e:
        return "priorities unknown (%s)" % type(e).__name__


def log(line):
    os.makedirs(LOGS, exist_ok=True)
    with open(os.path.join(LOGS, "run_graph.log"), "a", encoding="utf-8") as f:
        f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), line))


def status_update(**kw):
    try:
        st = json.load(open(STATUS, encoding="utf-8"))
    except Exception:
        st = {}
    st.update(kw)
    tmp = STATUS + ".tmp"
    json.dump(st, open(tmp, "w", encoding="utf-8"), indent=1)
    os.replace(tmp, STATUS)


def _os_lock(fd, take):
    """Take (non-blocking) or release the OS lock on the first byte of an open file; raises OSError when it is held.
    The membrane's primitive (live_membrane._os_lock): msvcrt on Windows, flock elsewhere."""
    if os.name == "nt":
        import msvcrt
        os.lseek(fd, 0, 0)
        msvcrt.locking(fd, msvcrt.LK_NBLCK if take else msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(fd, (fcntl.LOCK_EX | fcntl.LOCK_NB) if take else fcntl.LOCK_UN)


class Lock:
    """One run at a time, held by the operating system on an open file for as long as the run holds it and released by
    the system when the run ends, however it ends (29 Sep, scheduled audits account02/B18 r76 and account01/B18,
    B18-R63-PID-REUSE-FALSE-LIVENESS-001 and B18-R63-RUNGRAPH-POSIX-CONTENTION-CRASH-20260929).

    Until then the lock was a file created exclusively with the holder's pid in it, broken when that pid was not running
    or the file was older than stale_s. A pid says only that some process with that number is running: a crashed run's
    pid taken by an unrelated process read as a live run and held graph work off for up to two hours. The probe was also
    Windows-only, so on POSIX a second run crashed instead of skipping. A crashed run's lock is now released by the
    system at once, and a live run's lock is never taken, whatever its age: a run held past stale_s is logged by every run
    it keeps waiting, for someone to stop by hand. The holder's pid, start time and token go in a note beside the lock
    (graph.lock.who), for diagnosis only; the lock file itself carries nothing."""

    def __init__(self, name, stale_s=7200):
        self.path = os.path.join(HOME, name + ".lock")
        self.who = self.path + ".who"
        self.stale_s = stale_s
        self.held = False
        self.error = None                           # set when the lock file could not be made or opened at all
        self.token = None
        self._fd = None

    def _note(self):
        try:
            with open(self.who, encoding="utf-8") as fh:
                return fh.read()
        except OSError:
            return None

    def __enter__(self):
        # A lock that cannot be opened is a failure, not a run in progress: until 29 Sep both came back as held = False,
        # and a lock path that could never be opened read as healthy contention on every run while nothing ran
        # (account04/B15 r9, B15-R63-LOCK-OPEN-FAILURE-MASQUERADES-AS-CONTENTION-20260929-01).
        stage = "making the lock folder"
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            stage = "opening the lock file"
            fd = os.open(self.path, os.O_RDWR | os.O_CREAT)
        except OSError as exc:
            self.error = "%s while %s (errno %s)" % (type(exc).__name__, stage, exc.errno)
            return self
        try:
            _os_lock(fd, True)
        except OSError:
            os.close(fd)
            try:
                age = time.time() - os.path.getmtime(self.who)
            except OSError:
                age = 0.0
            if age > self.stale_s:
                log("the graph lock has been held for %.1f h (%s); a live run is never broken - stop it by hand"
                    % (age / 3600.0, (self._note() or "no note").split(" ")[0]))
            return self
        self._fd, self.held = fd, True
        self.token = "%d %f %s" % (os.getpid(), time.time(), os.urandom(6).hex())
        try:
            with open(self.who, "w", encoding="utf-8") as fh:
                fh.write(self.token)
        except OSError:
            pass
        return self

    def __exit__(self, *exc):
        if not self.held:
            return
        if self._note() == self.token:              # removed while still held, so it cannot be a successor's note
            try:
                os.remove(self.who)
            except OSError:
                pass
        try:
            _os_lock(self._fd, False)
        except OSError:
            pass
        try:
            os.close(self._fd)
        except OSError:
            pass
        self._fd, self.held = None, False


def capture(after=True):
    import extract_touch_events as ext
    before = os.path.getsize(EVENTS) if os.path.exists(EVENTS) else 0
    buf = io.StringIO()
    old = sys.stdout, sys.stderr, sys.argv
    sys.stdout = sys.stderr = buf
    sys.argv = ["extract_touch_events.py", "--days", "40", "--out", EVENTS, "--state", STATE]
    if os.path.exists(VERDICTS):             # made whole by backfill_history.py verdicts; the hourly capture adds to it
        sys.argv += ["--verdicts-out", VERDICTS]
    t0 = time.time()
    try:
        ext.main()
    finally:
        sys.stdout, sys.stderr, sys.argv = old
    grew = os.path.getsize(EVENTS) - before
    summary = (buf.getvalue().strip().splitlines() or ["(no output)"])[-1][:400]
    log("capture: +%.1f MB in %.0f s; %s" % (grew / 2**20, time.time() - t0, summary))
    status_update(capture_at=time.strftime("%Y-%m-%d %H:%M:%S"), capture_added_mb=round(grew / 2**20, 2),
                  events_mb=round(os.path.getsize(EVENTS) / 2**20, 1))
    if after:
        live_after_capture()
    _fast_layer()


def fast():
    """The five-minute pass: capture, the memories written since the last pass into the sidecar, the fast layer."""
    capture(after=False)
    t0 = time.time()
    try:
        log("live memories: %s in %.0f s" % (_memories(), time.time() - t0))
    except Exception:
        log("live memories FAILED: %s" % traceback.format_exc().strip().splitlines()[-1][:300])


def _delta():
    """The fast layer (live_delta.py) rebuilt on every event the store has not seen: after each capture and each build."""
    t0 = time.time()
    try:
        import live_delta
        p = live_delta.update()
        if p.get("skipped"):
            log("live delta skipped: %s" % p["skipped"][:300])
            status_update(delta_skipped_at=time.strftime("%Y-%m-%d %H:%M:%S"), delta_skipped=p["skipped"][:300])
            return
        status_update(delta_at=time.strftime("%Y-%m-%d %H:%M:%S"), delta_events=p.get("events_new"),
                      delta_edges=p.get("edges"), delta_newest_event=p.get("newest_event"))
        log("live delta: %d new events, %d edges, %d nodes in %.1f s" % (p.get("events_new", 0), p.get("edges", 0),
                                                                        p.get("nodes", 0), time.time() - t0))
    except Exception:
        log("live delta FAILED: %s" % traceback.format_exc().strip().splitlines()[-1][:300])


def _sidecar_behind():
    """Why the sidecar is not the store's own generation, or None when it is (or when that cannot be read: the delta's
    own read then says what went wrong)."""
    try:
        import live_delta
        live_delta.base()
    except Exception as e:
        if type(e).__name__ == "SidecarBehind":
            return str(e)
    return None


def _fast_layer(retry=True):
    """The fast layer after a capture or a build. A sidecar left behind the store (its publish failed after a build
    swapped the store in) is republished first, since a delta is built only over the sidecar's own store; if it is still
    behind, no delta is built and the previous pair keeps serving (29 Sep, account02/B11 r83). retry=False is the build's
    call, right after its own sidecar step failed; the next capture retries."""
    why = _sidecar_behind()
    if why and retry:
        t0 = time.time()
        try:
            log("live sidecar republished (it was behind the store: %s): %s in %.0f s" % (why[:200], _sidecar(),
                                                                                       time.time() - t0))
        except Exception:
            log("live sidecar republish FAILED: %s" % traceback.format_exc().strip().splitlines()[-1][:300])
        why = _sidecar_behind()
    if why:
        log("live delta skipped: the sidecar is behind the store (%s); the previous delta keeps serving with its own "
            "sidecar" % why[:200])
        status_update(delta_skipped_at=time.strftime("%Y-%m-%d %H:%M:%S"), delta_skipped=why[:300])
        return
    _delta()


def live_after_capture():
    """The live lane's hourly half (26 Sep, claude-825b1305): judge the membrane's fires against what the sessions did
    (graph_learn.py: edge factors, the serving bar, the recall weight), let memories written this hour into the
    sidecar's link and content layers so the hook can reach them before the next store build, rebuild the math brain's
    use layer from its serve log (math_use.py), and append the Codex rollouts' new bytes to events_codex.jsonl."""
    for name, fn in (("learn", _learn), ("memories", _memories), ("math use", _math_use), ("codex capture", _codex)):
        t0 = time.time()
        try:
            msg = fn()
            log("live %s: %s in %.0f s" % (name, msg, time.time() - t0))
        except Exception:
            log("live %s FAILED: %s" % (name, traceback.format_exc().strip().splitlines()[-1][:300]))


def _learn():
    import graph_learn
    buf = io.StringIO()
    old = sys.stdout, sys.argv
    sys.stdout, sys.argv = buf, ["graph_learn.py"]
    try:
        graph_learn.main()
    finally:
        sys.stdout, sys.argv = old
    try:
        st = json.loads(buf.getvalue())
        return "judged %s, learned %s edges; %s; %s" % (st.get("judged_now"), st.get("edges_learned_now"), st.get("bar"),
                                                         st.get("recall"))
    except Exception:
        return buf.getvalue().strip()[-200:]


def _memories():
    import live_sidecar
    n = live_sidecar.refresh_memories()
    return "%s memories/links/content" % (n,)


def _math_use():
    import math_use
    return json.dumps(math_use.build())


def _codex():
    """The 40-day Codex capture, kept current: its own incremental state (codex_state.json) means only new bytes."""
    import extract_codex_events as cx
    buf = io.StringIO()
    old = sys.stdout, sys.stderr, sys.argv
    sys.stdout = sys.stderr = buf
    sys.argv = ["extract_codex_events.py", "--days", "40", "--out", os.path.join(HOME, "events_codex.jsonl"),
                "--state", os.path.join(HOME, "codex_state.json")]
    try:
        cx.main()
    finally:
        sys.stdout, sys.stderr, sys.argv = old
    return (buf.getvalue().strip().splitlines() or ["(no output)"])[-1][:300]


def event_files():
    """What the store is built from: the live Claude capture, plus whatever build_config.json adds (the six-month history
    backfill, the Codex capture) - each only after a walk-forward comparison said it does not make the graph worse.

    A Codex events file is left out, and the leaving logged, until the Codex capture keys sessions by a canonical id:
    extract_codex_events.py writes x + the last seven characters of the rollout id, which session_kinds cannot resolve
    (all 2,143 sessions of events_codex.jsonl read 'unknown' on 27 Sep), so the builder could not drop a script's session
    and the store would stop being known-test-excluded (scheduled audit account01/B15, finding r52, 27 Sep). A study that
    means to build with those events sets BRAIN_GRAPH_ALLOW_CODEX_EVENTS=1."""
    files = [EVENTS]
    try:
        cfg = json.load(open(os.path.join(HOME, "build_config.json"), encoding="utf-8"))
        for f in cfg.get("extra_event_files", []):
            p = f if os.path.isabs(f) else os.path.join(HOME, f)
            if os.path.basename(p).startswith("events_codex") and os.environ.get("BRAIN_GRAPH_ALLOW_CODEX_EVENTS") != "1":
                log("event file %s left out: Codex session ids are x+last7 aliases session_kinds cannot resolve" % f)
                continue
            if os.path.exists(p):
                files.append(p)
    except Exception:
        pass
    return files


def live_after_build():
    """The live lane's six-hourly half: the sidecar rebuilt from the new store (live_sidecar.py), and recall's walk
    (graph_lane.py) moved onto the new store, so the edges both read are the ones that grew since the last build."""
    for name, fn in (("sidecar", _sidecar), ("recall walk", _recall_walk)):
        t0 = time.time()
        try:
            msg = fn()
            log("live %s: %s in %.0f s" % (name, msg, time.time() - t0))
        except Exception:
            log("live %s FAILED: %s" % (name, traceback.format_exc().strip().splitlines()[-1][:300]))


def _sidecar():
    import live_sidecar
    vs = RECENT_STORE if _config().get("corrections_from_live_capture") else None
    m = live_sidecar.build(STORE, verdict_store=vs)
    return "%d edges, %d memories, %d links, %d content, %d attractors; correction edges %s" % (
        m["edges"], m["memories"], m["links"], m["content"], m["attractors"],
        "from %s: %d" % (os.path.basename(vs), m["verdict_edges"]) if m.get("verdict_edges") is not None
        else "from the main store")


def _config():
    try:
        return json.load(open(os.path.join(HOME, "build_config.json"), encoding="utf-8"))
    except Exception:
        return {}


def _verdict_file():
    """His corrections, repeats and praise from the re-derived file (build_config verdict_events_file), or None: then the
    event files' own verdict events are used, which before 26 Sep 17:13 were mostly hook feedback."""
    f = _config().get("verdict_events_file")
    if not f:
        return None
    p = f if os.path.isabs(f) else os.path.join(HOME, f)
    return p if os.path.exists(p) else None


def _not_a_test_worker():
    """skip_session for every store build: the benches' headless test workers (session_kinds.py 'test') stay out
    (27 Sep: over 14 days they were 26, 35 and 34 percent of the store's memory reads, recalled and served events; their
    questions and reads are the benches', and the walk fed them back into recall). 'unknown' sessions are kept."""
    import session_kinds
    return lambda s: session_kinds.kind(s) == "test"


def _save_kinds():
    try:
        import session_kinds
        session_kinds.save()
    except Exception:
        pass


def _recent_store():
    """His corrections read their edges from a store of the live capture alone: no history, no fading (26 Sep
    walk-forward, results/LIVE_MEMBRANE_STUDY_2026-09-26_1702_H_hybrid.json: the June-August store with 14-day fading
    warned before his corrections 4.3% right at the 2% bar, the same store with the live capture's correction edges
    16.7%, every other channel unchanged). Built after the main store; on failure the previous one stays."""
    import graph_store
    new, old = RECENT_STORE + ".new", RECENT_STORE + ".old"
    shutil.rmtree(new, ignore_errors=True)
    t0 = time.time()
    meta = graph_store.build(EVENTS, new, exclude_rx=graph_store.BANK_RX, verdict_file=_verdict_file(),
                             skip_session=_not_a_test_worker())
    _save_kinds()
    if not meta.get("pairs") or not os.path.exists(os.path.join(new, "edges.npz")):
        shutil.rmtree(new, ignore_errors=True)
        return "empty (%s); the previous one stays" % meta
    shutil.rmtree(old, ignore_errors=True)
    if os.path.exists(RECENT_STORE):
        os.replace(RECENT_STORE, old)
    os.replace(new, RECENT_STORE)
    shutil.rmtree(old, ignore_errors=True)
    return "%d events, %d sessions, %d pairs in %.0f s" % (meta.get("events", 0), meta.get("sessions", 0),
                                                           meta.get("pairs", 0), time.time() - t0)


def _recall_walk():
    """Copy the store to dreaming/graph_lane_store.new, build its walk there, then swap it in under the name the dial
    reads and give the walk that name's cache key (diffusion.cached keys a walk by its store path)."""
    import hashlib
    dreaming = os.path.join(os.path.expanduser("~"), ".claude", "dreaming")
    sys.path.insert(0, dreaming)
    import diffusion
    import graph_lane
    d = graph_lane.dial()
    target = os.path.abspath(d.get("store") or os.path.join(dreaming, "graph_lane_store"))
    if os.path.normcase(target) == os.path.normcase(os.path.abspath(STORE)):
        return "dial reads the live store itself; nothing to move"
    new, old = target + ".new", target + ".old"
    shutil.rmtree(new, ignore_errors=True)
    os.makedirs(new)
    for f in ("edges.npz", "nodes.json", "uses.json"):
        shutil.copy2(os.path.join(STORE, f), os.path.join(new, f))
    cache_dir = os.path.join(dreaming, "graph_lane_cache")
    g = diffusion.Diffusion.cached(new, co_w=d["co_w"], cap=int(d["cap"]), alpha=d["alpha"], cache_dir=cache_dir,
                                   build=True)
    tag = "diffusion_cache_c%g_k%d_a%g_i%d" % (d["co_w"], int(d["cap"]), d["alpha"], 20)

    def key(p):
        return hashlib.sha1(os.path.abspath(p).lower().encode("utf-8")).hexdigest()[:12]
    src_cache = os.path.join(cache_dir, "%s_%s.npz" % (key(new), tag))
    if not os.path.exists(src_cache):
        shutil.rmtree(new, ignore_errors=True)
        return "walk cache was not written; the old walk stays"
    shutil.rmtree(old, ignore_errors=True)
    if os.path.exists(target):
        os.replace(target, old)
    os.replace(new, target)
    dst_cache = os.path.join(cache_dir, "%s_%s.npz" % (key(target), tag))
    os.replace(src_cache, dst_cache)
    os.utime(dst_cache, None)                  # newer than the moved edges.npz, so cached() takes it
    shutil.rmtree(old, ignore_errors=True)
    return "walk of %d nodes, %d edges now under %s" % (g.N, g.nnz, os.path.basename(target))


def build():
    free = commit_free_gb()
    if free < 3.0:
        log("build refused: %.1f GB of commit headroom, need 3" % free)
        status_update(build_refused_at=time.strftime("%Y-%m-%d %H:%M:%S"), build_refused_free_gb=round(free, 1))
        return
    import graph_store
    new, old = STORE + ".new", STORE + ".old"
    shutil.rmtree(new, ignore_errors=True)
    t0 = time.time()
    files = event_files()
    nbytes = os.path.getsize(EVENTS)         # where this build's events end: the fast layer starts here (27 Sep)
    try:                                     # and what those bytes were, so the fast layer can tell a rewritten log
        import live_delta                    # (account04/B06 R12, 27 Sep)
        marks = live_delta.prefix_marks(EVENTS, nbytes) or (None, None)
        full_sha = live_delta.prefix_sha(EVENTS, nbytes)   # the whole prefix (account03/B12 R12, 28 Sep)
    except Exception:
        marks, full_sha = (None, None), None
    try:
        hl = json.load(open(os.path.join(HOME, "build_config.json"), encoding="utf-8")).get("half_life_days")
    except Exception:
        hl = None
    meta = graph_store.build(files if len(files) > 1 else EVENTS, new, exclude_rx=graph_store.BANK_RX,  # bank out
                             half_life_days=hl, verdict_file=_verdict_file(), skip_session=_not_a_test_worker())
    _save_kinds()
    if not meta.get("pairs") or not meta.get("sessions"):
        log("build produced an empty store (%s); the old store stays" % meta)
        return
    for f in ("edges.npz", "nodes.json", "uses.json"):
        if not os.path.exists(os.path.join(new, f)):
            log("build is missing %s; the old store stays" % f)
            return
    shutil.rmtree(old, ignore_errors=True)
    if os.path.exists(STORE):
        os.replace(STORE, old)
    os.replace(new, STORE)
    shutil.rmtree(old, ignore_errors=True)
    # the benches' test workers left out (skip_session) are counted beside the bank work, so a reader can see what
    # population the store was grown from (27 Sep: graph_store.build counted them, this line never said so)
    log("build: %d events, %d sessions, %d pairs, %d co-activation pairs in %.0f s; bank work left out: %d events in "
        "%d sessions; test workers left out: %d events in %d sessions" % (
            meta.get("events", 0), meta.get("sessions", 0), meta.get("pairs", 0), meta.get("co_pairs", 0),
            time.time() - t0, meta.get("excluded_events", 0), meta.get("excluded_sessions", 0),
            meta.get("skipped_events", 0), meta.get("skipped_sessions", 0)))
    status_update(build_at=time.strftime("%Y-%m-%d %H:%M:%S"), build_events=meta.get("events"),
                  build_sessions=meta.get("sessions"), build_pairs=meta.get("pairs"), build_until=meta.get("until"),
                  build_bank_events_left_out=meta.get("excluded_events"),
                  build_test_workers_left_out={"events": meta.get("skipped_events"),
                                               "sessions": meta.get("skipped_sessions")},
                  build_events_bytes=nbytes, build_store_built=meta.get("built"),
                  build_events_head_sha=marks[0], build_events_tail_sha=marks[1], build_events_sha=full_sha,
                  build_repeats_collapsed=meta.get("repeats_collapsed"))
    if _config().get("corrections_from_live_capture"):
        t1 = time.time()
        try:
            log("recent store (his corrections' edges): %s" % _recent_store())
        except Exception:
            log("recent store FAILED after %.0f s: %s; the previous one stays" % (
                time.time() - t1, traceback.format_exc().strip().splitlines()[-1][:300]))
    live_after_build()
    _fast_layer(retry=False)                  # the fast layer starts over on the new store at once (unless the sidecar
                                              # failed to publish: then the next capture republishes it first)


def main():
    polite()
    mode = sys.argv[1] if len(sys.argv) > 1 else "status"
    if mode == "status":
        print(open(STATUS, encoding="utf-8").read() if os.path.exists(STATUS) else "no status yet")
        return
    if mode not in ("capture", "build", "fast"):
        log("unknown mode %r; nothing ran" % mode)
        sys.exit(2)
    if mode != "fast":                   # twelve fast runs an hour would bury the hourly and six-hourly lines
        log("%s started (%s)" % (mode, priorities()))     # a starved run is otherwise indistinguishable from a missed fire
    deadline = time.time() + (0.0 if mode == "fast" else 180.0)
    while True:
        with Lock("graph") as lk:        # one lock for all: a build never reads a file the capture is appending to
            if lk.held:
                try:
                    {"capture": capture, "build": build, "fast": fast}[mode]()
                except Exception:
                    log("%s FAILED: %s" % (mode, traceback.format_exc().strip().splitlines()[-1][:300]))
                    status_update(**{mode + "_failed_at": time.strftime("%Y-%m-%d %H:%M:%S")})
                    raise
                return
            error = lk.error
        if time.time() >= deadline:      # a fast run skips at once; the hourly and six-hourly runs wait for it
            if error:
                log("%s FAILED: the graph lock could not be taken (%s); nothing ran" % (mode, error))
                status_update(**{mode + "_lock_failed_at": time.strftime("%Y-%m-%d %H:%M:%S"), "lock_error": error})
                sys.exit(3)
            log("%s skipped: a run is in progress" % mode)
            return
        time.sleep(5.0)


if __name__ == "__main__":
    main()
