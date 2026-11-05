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
        psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
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
    sig = hashlib.sha256(("%s|%s|%s|%s|%s" % (
        sys.version, fr._code_key(R, mr.__file__), fr._env_key(),
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
            sys.version, fr._code_key(R, mr.__file__), fr._env_key(),
            os.path.normcase(os.path.abspath(store)), fr._store_key(store))).encode("utf-8")).hexdigest()[:24]
        result = ("built" if st.get("built") else "loaded") + (", %d s behind the store" % st["stale_s"] if st.get("stale_s") else "")
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
