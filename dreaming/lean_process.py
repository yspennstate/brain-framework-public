"""Windows subprocess tree owned by a native Job Object before its first instruction.

The child starts suspended. A failed job assignment never launches it. Cleanup
uses native handles and cannot select an unrelated process by a reused PID.
"""
import ctypes as c
from ctypes import wintypes as w
import json
import os
import subprocess
import time

# The job's committed memory is capped (JOB_OBJECT_LIMIT_JOB_MEMORY), so the kernel refuses an allocation past the cap
# at once. Before 5 Oct 2026 the only memory guard was lean_check's admission poll, every 15 s, and one compile grew to
# 38 GB between two polls; commit reached 99.9 pct of the machine's limit before the poll stopped it. A one-line Mathlib
# compile commits about 4 GB (4,098 MB measured on 5 Oct, most of it the .olean files read in), and under a 2 GB cap
# Mathlib does not load at all. LEAN_JOB_MEMORY_MB overrides the cap; 0 removes it. Every run's peak goes to PEAK_LOG.
CAP_MB = 16384
PEAK_LOG = os.environ.get("LEAN_PEAK_LOG") or os.path.join(os.path.expanduser("~"), ".claude", "dreaming",
                                                             "lean_process_peaks.jsonl")


def cap_mb():
    try:
        return max(0, int(os.environ.get("LEAN_JOB_MEMORY_MB", CAP_MB)))
    except ValueError:
        return CAP_MB


def priority_class():
    """8 Oct 21:1x (claude-4a88bc43, pp1's question): on the saturated laptop a BelowNormal compile never finishes - 0 of 32
    on 8 Oct; a measured pair at 20:56-21:09, same compiler, Mathlib and imports: BelowNormal with Windows power throttling
    turned off timed out at 600 s with 21 s of CPU, Normal finished in 135 s at 4,097 MB. Throttling is not the cause;
    priority is. Normal while the compute governor says the owner is away (run_bounded.py's rule); BelowNormal while he is
    present (his rule: background work stays BelowNormal). LEAN_PRIORITY=normal|belownormal overrides."""
    ov = os.environ.get("LEAN_PRIORITY", "").strip().lower()
    if ov == "normal":
        return subprocess.NORMAL_PRIORITY_CLASS
    if ov == "belownormal":
        return subprocess.BELOW_NORMAL_PRIORITY_CLASS
    try:
        with open(os.path.join(os.path.expanduser("~"), ".claude", "compute", "compute_state.json"), encoding="utf-8") as f:
            st = json.load(f)
        if st.get("owner_away") is True or "away" in str(st.get("mode", "")):
            return subprocess.NORMAL_PRIORITY_CLASS
    except (OSError, ValueError):
        pass
    return subprocess.BELOW_NORMAL_PRIORITY_CLASS


def record(row):
    try:
        with open(PEAK_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
    except OSError:
        pass                                  # the peak log is a record, never a reason for a compile to fail


class BasicLimit(c.Structure):
    _fields_ = [("process_time", c.c_longlong), ("job_time", c.c_longlong),
                ("flags", w.DWORD), ("min_working_set", c.c_size_t),
                ("max_working_set", c.c_size_t), ("active_limit", w.DWORD),
                ("affinity", c.c_size_t), ("priority", w.DWORD), ("scheduling", w.DWORD)]


class IoCounters(c.Structure):
    _fields_ = [(name, c.c_ulonglong) for name in
                ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]


class ExtendedLimit(c.Structure):
    _fields_ = [("basic", BasicLimit), ("io", IoCounters),
                ("process_memory", c.c_size_t), ("job_memory", c.c_size_t),
                ("peak_process_memory", c.c_size_t), ("peak_job_memory", c.c_size_t)]


def run(argv, *, input=None, cwd=None, timeout=600, progress=None, env=None):
    if os.name != "nt":
        raise RuntimeError("This process owner is implemented and tested for Windows only")
    import psutil
    k = c.WinDLL("kernel32", use_last_error=True)
    k.CreateJobObjectW.argtypes = [c.c_void_p, w.LPCWSTR]
    k.CreateJobObjectW.restype = w.HANDLE
    k.SetInformationJobObject.argtypes = [w.HANDLE, c.c_int, c.c_void_p, w.DWORD]
    k.SetInformationJobObject.restype = w.BOOL
    k.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
    k.AssignProcessToJobObject.restype = w.BOOL
    k.TerminateJobObject.argtypes = [w.HANDLE, w.UINT]
    k.TerminateJobObject.restype = w.BOOL
    k.CloseHandle.argtypes = [w.HANDLE]
    k.CloseHandle.restype = w.BOOL
    k.OpenThread.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    k.OpenThread.restype = w.HANDLE
    k.GetProcessIdOfThread.argtypes = [w.HANDLE]
    k.GetProcessIdOfThread.restype = w.DWORD
    k.ResumeThread.argtypes = [w.HANDLE]
    k.ResumeThread.restype = w.DWORD
    k.QueryInformationJobObject.argtypes = [w.HANDLE, c.c_int, c.c_void_p, w.DWORD, c.POINTER(w.DWORD)]
    k.QueryInformationJobObject.restype = w.BOOL
    cap = cap_mb()
    started = time.time()

    def peak_mb():
        info = ExtendedLimit()
        if not k.QueryInformationJobObject(job, 9, c.byref(info), c.sizeof(info), None):
            return None
        return round(info.peak_job_memory / 2 ** 20)

    def at_cap(peak):
        # an allocation the cap refuses is never counted, so the peak stops short of the cap: within 5 pct is the cap
        return bool(cap and peak is not None and peak >= 0.95 * cap)

    noted = []

    def note(peak, code, timed_out=False, stopped=False):
        noted.append(1)
        record({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "argv0": os.path.basename(str(argv[0])), "cap_mb": cap,
                "peak_mb": peak, "at_cap": at_cap(peak), "exit": code, "timed_out": timed_out,
                "stopped_by_caller": stopped, "seconds": round(time.time() - started, 1)})
        return ("\nThe compile was stopped at its memory cap of %d MB (peak %d MB).\n" % (cap, peak)) if at_cap(peak) else ""

    def checked(value):
        if not value:
            raise c.WinError(c.get_last_error())
        return value

    job = checked(k.CreateJobObjectW(None, None))
    proc = None
    assigned = False
    try:
        limits = ExtendedLimit()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if cap:
            limits.basic.flags |= 0x200  # JOB_OBJECT_LIMIT_JOB_MEMORY: committed memory of the whole tree
            limits.job_memory = cap * 2 ** 20
        checked(k.SetInformationJobObject(job, 9, c.byref(limits), c.sizeof(limits)))
        flags = subprocess.CREATE_NO_WINDOW | priority_class() | 0x4
        proc = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, encoding="utf-8", errors="replace",
                                creationflags=flags)
        if progress:
            progress({"phase": "created_suspended", "pid": proc.pid})
        # Popen retains this native handle until process cleanup. The child is suspended.
        checked(k.AssignProcessToJobObject(job, w.HANDLE(int(proc._handle))))
        assigned = True
        threads = psutil.Process(proc.pid).threads()
        if len(threads) != 1:
            raise RuntimeError("Expected one suspended initial thread; refusing ambiguous launch")
        thread = checked(k.OpenThread(0x0802, False, threads[0].id))
        try:
            if k.GetProcessIdOfThread(thread) != proc.pid:
                raise RuntimeError("Initial thread ownership changed")
            suspend_count = k.ResumeThread(thread)
            if progress:
                progress({"phase": "resumed", "previous_suspend_count": suspend_count})
            if suspend_count == 0xFFFFFFFF:
                raise c.WinError(c.get_last_error())
            if suspend_count != 1:
                raise RuntimeError("Unexpected suspend count; refusing to override another process controller")
        finally:
            k.CloseHandle(thread)
        start = time.monotonic()
        pending_input = input
        while True:
            try:
                out, err = proc.communicate(pending_input, timeout=min(15, max(0.01, timeout - (time.monotonic() - start))))
                break
            except subprocess.TimeoutExpired as exc:
                pending_input = None
                elapsed = time.monotonic() - start
                if progress:
                    progress({"phase": "waiting", "seconds": round(elapsed, 3), "root_exit_code": proc.poll()})
                if elapsed >= timeout:
                    checked(k.TerminateJobObject(job, 124))
                    out, err = proc.communicate(timeout=15)
                    err = (err or "") + note(peak_mb(), proc.returncode, timed_out=True)
                    raise subprocess.TimeoutExpired(argv, timeout, output=out, stderr=err) from exc
        err = (err or "") + note(peak_mb(), proc.returncode)
        return subprocess.CompletedProcess(argv, proc.returncode, out, err)
    finally:
        if proc is not None and proc.poll() is None:
            if assigned:
                k.TerminateJobObject(job, 125)
            else:
                proc.kill()  # native handle; never resumed, so no descendants exist
        if not noted and assigned:           # stopped by the caller, such as lean_check's admission poll
            note(peak_mb(), proc.poll() if proc is not None else None, stopped=True)
        k.CloseHandle(job)  # also closes any remaining descendants owned by this call
        if proc is not None:
            try:
                proc.wait(timeout=15)
            finally:
                for pipe in (proc.stdin, proc.stdout, proc.stderr):
                    if pipe is not None:
                        pipe.close()
