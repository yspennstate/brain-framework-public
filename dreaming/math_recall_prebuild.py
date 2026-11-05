"""math_recall_prebuild.py - keep math_recall.py's prepared search ready for the math store as it is now.

The first math_recall.py call after the math store changes builds the prepared state (fastrecall.py) itself: 10 s on
the 13 Oct machine and 14 s under the A/B load, longer than a Codex tool call waits (10 s; a slower call comes back
empty). The store moves in batches, several times on a busy day. This job, run every 5 minutes by the hidden task
MathRecallPrebuild5m, opens the prepared state exactly as math_recall.py does - the same interpreter, the same store
path, the same caller file and the same MATHBRAIN_* environment, so the same cache file - and the build happens here
instead of inside somebody's question. Callers then load it in about 2 s.

A run whose store, code and environment match the last good run stops after four stat calls and a hash of the engine
sources (about half a second, below normal priority). Anything unexpected is logged and left for the next run; the
recall tool itself never depends on this job.

    python math_recall_prebuild.py           check, and open (load or build) when something moved
    python math_recall_prebuild.py --force   open the prepared state now
    python math_recall_prebuild.py --status  the last runs

Files, in the cache folder (MATH_RECALL_CACHE_DIR or ~/.claude/dreaming/cache/math_recall):
    prebuild_state.json   the last good signature and the last run (rewritten every run)
    prebuild.log          one line per run that opened the state or failed; quiet runs add nothing
"""
import sys
if __name__ == "__main__" and ("-h" in sys.argv[1:] or "--help" in sys.argv[1:]):
    print(__doc__)
    sys.exit(0)
import hashlib
import importlib.util
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "math_recall.py")


def below_normal():
    """Below-normal priority, but out of Windows power throttling: a windowless below-normal job is throttled by default
    and ran 4-10 times slower (memory the_power_throttle_optout_never_ran_ctypes_cut_the_handle_2026_10_11, whose
    c_void_p handle this uses). Returns what the process reads back, for the state file."""
    got = {}
    try:
        import psutil
        # Normal by default since 29 Oct: at below-normal on a busy machine the build took 7,397 s (02:25-04:28) while
        # a recall call at normal priority built the same state in about 3.5 min (04:28:45-04:32), and the prebuild's
        # state, read two hours earlier, was already outside the callers' 2,700 s window, so it was never used. At most
        # six builds an hour still bound its share. MATH_RECALL_PREBUILD_PRIORITY=below_normal restores the old class.
        low = os.environ.get("MATH_RECALL_PREBUILD_PRIORITY", "").strip().lower() in ("below_normal", "low")
        psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS if low else psutil.NORMAL_PRIORITY_CLASS)
        got["priority"] = hex(int(psutil.Process().nice()))
    except Exception as exc:
        got["priority"] = "error %s" % type(exc).__name__
    try:
        import ctypes

        class Throttle(ctypes.Structure):
            _fields_ = [("Version", ctypes.c_ulong), ("ControlMask", ctypes.c_ulong), ("StateMask", ctypes.c_ulong)]

        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.GetCurrentProcess.restype = ctypes.c_void_p
        k.SetProcessInformation.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong]
        k.GetProcessInformation.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong]
        hp = ctypes.c_void_p(k.GetCurrentProcess())
        want = Throttle(1, 1, 0)   # version 1, execution speed controlled, throttling off
        ok = k.SetProcessInformation(hp, 4, ctypes.byref(want), ctypes.sizeof(want))
        back = Throttle(1, 0, 0)
        k.GetProcessInformation(hp, 4, ctypes.byref(back), ctypes.sizeof(back))
        got["throttle"] = "set %s, control 0x%x state 0x%x" % (bool(ok), back.ControlMask, back.StateMask)
    except Exception as exc:
        got["throttle"] = "error %s" % type(exc).__name__
    return got


def load_tool():
    spec = importlib.util.spec_from_file_location("math_recall", TOOL)
    mr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mr)
    return mr


def stamp():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def read_state(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            got = json.load(fh)
        return got if isinstance(got, dict) else {}
    except Exception:
        return {}


def write_state(path, state):
    tmp = "%s.tmp%d" % (path, os.getpid())
    try:
        with open(tmp, "w", encoding="utf-8", newline="") as fh:
            json.dump(state, fh, indent=1, sort_keys=True)
        os.replace(tmp, path)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass


def log(folder, line):
    try:
        with open(os.path.join(folder, "prebuild.log"), "a", encoding="utf-8", newline="") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


_RUN_LOCK = []


def hold_run_lock(folder):
    """True when this process now holds prebuild.lock (kept until exit); False when another live run holds it."""
    try:
        os.makedirs(folder, exist_ok=True)
        fh = open(os.path.join(folder, "prebuild.lock"), "a+b")
    except OSError:
        return True  # the lock is a courtesy; a folder we cannot write is the old behaviour
    try:
        if os.name == "nt":
            import msvcrt
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return False
    _RUN_LOCK.append(fh)
    return True


WRITE_AGE_S = float(os.environ.get("MATH_RECALL_PREBUILD_WRITE_AGE_S", "900") or 900)
SIDECAR_WAL_MAX = float(os.environ.get("MATH_RECALL_SIDECAR_WAL_MB", "64") or 64) * 1024 * 1024


def trim_sidecar_wal(store, limit=None, timeout=120):
    """Fold the signal sidecar's write-ahead log into its database and cut the log to zero bytes when it is past
    `limit` (SQLite's TRUNCATE checkpoint; no row changes). Returns a log fragment, or "" when there was nothing to do.

    Why: on 31 Oct the sidecar's log stood at 809 MB from one full rewrite and nothing ever shrank it. On Windows,
    when no process holds the database open, the next opener truncates the -shm index and rebuilds it by reading every
    log frame, 4 KB at a time - 809 MB at 0.25-0.5 MB/s on this disk. fastrecall_delta.extend's first signal query
    paid that: one-card extends of 1,773, 2,543 and 3,664 s, while a four-card extend with the sidecar already held
    open took 1.7 s. Readers waiting behind the rebuild got "database is locked"."""
    limit = SIDECAR_WAL_MAX if limit is None else limit
    db = str(store) + ".signals.sqlite"
    wal = db + "-wal"
    try:
        size = os.path.getsize(wal)
    except OSError:
        return ""
    if size <= limit:
        return ""
    import sqlite3
    t0 = time.time()
    try:
        con = sqlite3.connect(db, timeout=timeout)
        try:
            busy, frames, done = con.execute("pragma wal_checkpoint(TRUNCATE)").fetchone()
        finally:
            con.close()
    except Exception as exc:
        return "sidecar log %d MB, trim failed %s: %s" % (size >> 20, type(exc).__name__, str(exc)[:120])
    after = os.path.getsize(wal) if os.path.exists(wal) else 0
    return "sidecar log %d MB -> %d MB in %.1f s (busy %s, frames %s/%s)" % (size >> 20, after >> 20,
                                                                         time.time() - t0, busy, done, frames)


def name_now(fr, R, mr, store):
    """The prepared state's name as the code on disk computes it NOW (fastrecall's formula). A run that loaded old code
    keeps its old name in memory; on 31 Oct a prebuild started 05:23 wrote recall_6d83d047 back at 06:53, after a landing
    at 06:51 had renamed the live state to 6a6cc269 - a dead 462 MB pair that _prune (newest first, 800 MB) would have
    kept over the live one."""
    return hashlib.sha256(("%d|%s|%s|%s|%s" % (fr.FORMAT, sys.version, fr._code_key(R, mr.__file__, mr.derive_tool),
                                               fr._env_key(), os.path.normcase(os.path.abspath(store)))
                           ).encode("utf-8")).hexdigest()[:24]


def write_back(fr, P, st, t_read, current=None):
    """Write an extended state over the one on disk, so callers load it inside their window instead of extending from
    an ever older base (29 Oct: the 10:53 build was past the 2,700 s window an hour later, every call extended from it,
    one extend took 59 s, and six hours on the base fell off DELTA_MAX_AGE_S into a full build of 900-1,600 s). Tested
    on a copy first: the written and reloaded state answered 40 of 40 searches with the same cards, order and scores,
    with the same links, corpus, ids and tool maps, and the next open loaded it with no build. Only a state more than
    WRITE_AGE_S old is rewritten: the write is 460 MB (about 20 s on this disk)."""
    try:
        age = time.time() - float(P.s.get("read_at") or 0)
        # extended_links: a run that added only links between known cards (fastrecall_delta._read_links, 4 Nov)
        if not (st.get("extended") or st.get("extended_links")) or age < WRITE_AGE_S or not st.get("path"):
            return " (not written, base %d s old)" % age
        if current is not None:
            now = current()
            if not os.path.basename(st["path"]).startswith("recall_%s." % now):
                return " (not written: the code changed during the run, the state is now recall_%s)" % now
        t0 = time.time()
        heavy = dict(P.heavy())
        heavy.pop("_delta", None)
        P.s["key"] = heavy["key"] = P.key
        P.s["read_at"] = t_read
        path = st["path"]
        fr._write(path[:-2] + ".heavy.m", heavy)
        fr._write(path, P.s)
        return " (written in %.1f s, base was %d s old)" % (time.time() - t0, age)
    except Exception as exc:
        return " (write failed %s: %s)" % (type(exc).__name__, str(exc)[:120])


def main(argv):
    if "--status" in argv:
        mr_folder = os.environ.get("MATH_RECALL_CACHE_DIR") or os.path.join(HERE, "cache", "math_recall")
        print(json.dumps(read_state(os.path.join(mr_folder, "prebuild_state.json")), indent=1, sort_keys=True))
        try:
            with open(os.path.join(mr_folder, "prebuild.log"), "r", encoding="utf-8") as fh:
                for line in fh.readlines()[-10:]:
                    print(line.rstrip())
        except OSError:
            pass
        return 0
    # One run at a time. The task's 10-minute limit kills the wscript wrapper but not this child, so the task's
    # IgnoreNew policy never sees a long build and starts another every 5 minutes: on 27 Oct 22:47-23:58 five to seven
    # builds ran at once on a starved disk, each 1.5-2 h, and their "opens" (written only at the end) never reached
    # the stand-aside check below. The OS drops the lock when the holder exits, crash or kill included.
    if not hold_run_lock(os.environ.get("MATH_RECALL_CACHE_DIR") or os.path.join(HERE, "cache", "math_recall")):
        return 0
    proc = below_normal()
    t0 = time.time()
    mr = load_tool()
    if mr.BUILD_SRC not in sys.path:
        sys.path.insert(0, mr.BUILD_SRC)
    fr = mr._fast_module()
    if fr is None:
        return 1
    folder = fr.cache_dir()
    os.makedirs(folder, exist_ok=True)
    state_path = os.path.join(folder, "prebuild_state.json")
    state = read_state(state_path)
    import math_brain.retrieval as R
    store = mr.STORE_PATH
    trimmed = trim_sidecar_wal(store)
    if trimmed:
        log(folder, "%s %s" % (stamp(), trimmed))
    sig =hashlib.sha256(("%s|%s|%s|%s|%s" % (
        sys.version, fr._code_key(R, mr.__file__, mr.derive_tool), fr._env_key(),
        os.path.normcase(os.path.abspath(store)), fr._store_key(store))).encode("utf-8")).hexdigest()[:24]
    state["last_check"] = stamp()
    state["process"] = proc
    if "--force" not in argv and sig == state.get("good_sig") and os.path.exists(state.get("path") or ""):
        state["last_result"] = "current"
        write_state(state_path, state)
        return 0
    # first do no harm: a store that moves every few minutes would have this job building all the time. Past six
    # opens in an hour it stands aside and callers build for themselves, as they did before it existed.
    recent = [t for t in state.get("opens", []) if isinstance(t, (int, float)) and time.time() - t < 3600]
    if "--force" not in argv and len(recent) >= 6:
        state["last_result"] = "standing aside: %d opens in the last hour" % len(recent)
        write_state(state_path, state)
        return 0
    # only a build counts toward the six an hour; a load of a state under 15 min behind the store is cheap
    t1 = time.time()
    P = fr.open_prepared(store, R, mr.derive_tool, mr.__file__, stale_s=900)
    if fr.STATUS.get("built"):
        state["opens"] = recent + [round(t1, 1)]
    st = dict(fr.STATUS)
    secs = time.time() - t1
    if P is not None and st.get("fast"):
        after = hashlib.sha256(("%s|%s|%s|%s|%s" % (
            sys.version, fr._code_key(R, mr.__file__, mr.derive_tool), fr._env_key(),
            os.path.normcase(os.path.abspath(store)), fr._store_key(store))).encode("utf-8")).hexdigest()[:24]
        result = ("built" if st.get("built") else "loaded") + (", %d s behind the store" % st["stale_s"] if st.get("stale_s") else "")
        # lead 3: say whether the in-memory extend ran or why it was refused, so a full build is never unexplained
        if st.get("extended") is not None:
            result += ", extended by %s card(s) in %s s" % (st["extended"], st.get("extend_s")) + write_back(
                fr, P, st, t1, current=lambda: name_now(fr, R, mr, store))
        elif st.get("extend_refused"):
            result += ", extend refused: %s" % str(st["extend_refused"])[:160]
        if after == sig:
            state["good_sig"], state["path"] = sig, st.get("path")
        else:
            result += ", store moved during the run"
    else:
        result = "live path: " + str(st.get("why") or "unknown")
    state["last_result"] = result
    state["last_open_s"] = round(secs, 2)
    write_state(state_path, state)
    log(folder, "%s %s in %.1f s (whole run %.1f s)" % (stamp(), result, secs, time.time() - t0))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as exc:
        try:
            folder = os.environ.get("MATH_RECALL_CACHE_DIR") or os.path.join(HERE, "cache", "math_recall")
            log(folder, "%s error %s: %s" % (stamp(), type(exc).__name__, str(exc)[:300]))
        except Exception:
            pass
        sys.exit(1)
