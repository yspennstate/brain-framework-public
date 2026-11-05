#!/usr/bin/env python3
"""job_proc.py - structural identity for a Claude Code session: which process, which terminal.

Owner's ask, 2026-07-26: "can you make it that this does not work with timing, it works with how the
terminal are built?" He is right, and the previous verification is the argument for him. The handover
ladder ended in two temporal rungs - `recency` (newest readme anyone filed) and the machine-wide
LAST_JOB.md - and when the clear was finally tested for real, the nearest rival pointer landed in the SAME
MINUTE as ours. It matched on the process id, so it was correct, but had it fallen through one rung the
answer would have been a coin flip. A tie-break on time is not an identity.

So identity comes from the process tree instead, measured here 2026-07-26 with six live claude.exe:

    explorer.exe 14644
      WindowsTerminal.exe 23616          <- SHARED by every tab. Never match on this.
        powershell.exe 24620             <- one per tab. THIS is the lane.
          claude.exe 23692               <- one per tab. A /clear does not restart it.
            bash.exe -> python.exe       <- hooks, three levels down

    claude 4256 <-host 23200   claude 23320 <-host 34180   claude 23692 <-host 24620
    claude 25284 <-host 13564  claude 33872 <-host 35032   claude 36544 <-host 25092

Every claude.exe had its own distinct powershell.exe. That is the discriminator; the terminal application
above it is not, and matching there would have unified all six tabs into one lane.

Two things make this an identity rather than a hint:

  Creation time. A pid alone is not unique over time - Windows recycles them, and the pointer that a pid
  match reads could have been left by a claude.exe that exited hours ago while an unrelated process
  inherited its number. (pid, creation FILETIME) is unique for the life of the machine. This is what the
  old six-hour POINTER_TTL was really guarding against, badly: a timer cannot tell a recycled pid from a
  live one, and six hours is both too long to be safe and too short to be correct.

  Liveness. A clear happens INSIDE a running claude.exe, so a pointer whose process is gone can never be
  legitimately claimed by anybody. That retires the TTL completely: pointers are collected when their
  process dies, not when a clock runs out, which is both tighter and unbounded in time.

Pure ctypes on purpose - no subprocess, so no console window is ever allocated (the owner's standing rule),
and no psutil dependency in a hook that must never fail. TABLE is the test seam: set it to a fake process
table and the entire ladder, including pid reuse, is unit-testable without spawning anything.
"""
import json
import os

# Test seam. None -> ask Windows. Otherwise {pid: (ppid, name, created)} and no Win32 call is made.
# JOB_PROC_TABLE carries the same thing as JSON so a hook running in a SUBPROCESS can be handed a fake
# process tree. Without it the end-to-end tests could only exercise this machine's real pids, and the
# cases that matter most - a recycled pid, a dead process, a rival terminal tab - are precisely the ones
# you cannot arrange for real.
TABLE = None


def _env_table():
    raw = (os.environ.get("JOB_PROC_TABLE") or "").strip()
    if not raw:
        return None
    try:
        d = json.loads(raw)
        return {int(k): (int(v[0]), str(v[1]), int(v[2])) for k, v in d.items()}
    except Exception:
        return None

CLAUDE_NAMES = ("claude.exe", "claude")


def _win_table():
    """{pid: (ppid, name, 0)} from one Toolhelp32 pass. Creation times are filled in lazily."""
    import ctypes
    import ctypes.wintypes as w

    class PROCESSENTRY32(ctypes.Structure):
        _fields_ = [("dwSize", w.DWORD), ("cntUsage", w.DWORD), ("th32ProcessID", w.DWORD),
                    ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)), ("th32ModuleID", w.DWORD),
                    ("cntThreads", w.DWORD), ("th32ParentProcessID", w.DWORD),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", w.DWORD),
                    ("szExeFile", ctypes.c_char * 260)]

    k32 = ctypes.windll.kernel32
    k32.CreateToolhelp32Snapshot.restype = w.HANDLE
    k32.CreateToolhelp32Snapshot.argtypes = [w.DWORD, w.DWORD]
    k32.CloseHandle.argtypes = [w.HANDLE]

    out = {}
    h = k32.CreateToolhelp32Snapshot(0x00000002, 0)  # TH32CS_SNAPPROCESS
    if not h or h == w.HANDLE(-1).value:
        return out
    try:
        e = PROCESSENTRY32()
        e.dwSize = ctypes.sizeof(PROCESSENTRY32)
        ok = k32.Process32First(h, ctypes.byref(e))
        while ok:
            out[int(e.th32ProcessID)] = (int(e.th32ParentProcessID),
                                        e.szExeFile.decode("mbcs", "replace"), 0)
            ok = k32.Process32Next(h, ctypes.byref(e))
    finally:
        k32.CloseHandle(h)
    return out


def _win_created(pid):
    """Creation time of pid as an integer FILETIME (100ns ticks), 0 if it cannot be read.

    PROCESS_QUERY_LIMITED_INFORMATION is enough for same-user processes and does not need elevation.
    """
    import ctypes
    import ctypes.wintypes as w
    k32 = ctypes.windll.kernel32
    k32.OpenProcess.restype = w.HANDLE
    k32.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    k32.CloseHandle.argtypes = [w.HANDLE]
    h = k32.OpenProcess(0x1000, False, int(pid))
    if not h:
        return 0
    try:
        c, x, kt, u = w.FILETIME(), w.FILETIME(), w.FILETIME(), w.FILETIME()
        if not k32.GetProcessTimes(h, ctypes.byref(c), ctypes.byref(x),
                                   ctypes.byref(kt), ctypes.byref(u)):
            return 0
        return (int(c.dwHighDateTime) << 32) | int(c.dwLowDateTime)
    finally:
        k32.CloseHandle(h)


def faked():
    """The fixture table in force, or None when we are talking to the real machine."""
    if TABLE is not None:
        return TABLE
    return _env_table()


def table():
    """{pid: (ppid, name, created)}. Fail-safe: {} if the OS will not answer."""
    t = faked()
    if t is not None:
        return t
    if os.name != "nt":
        # created = start epoch seconds from ps lstart: stable to the second, so (pid, created) stays an identity
        try:
            import sys
            sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            import posix_proc
            starts = posix_proc.start_times()
            return {pid: (ppid, name, starts.get(pid, 0)) for pid, (ppid, name) in posix_proc.parent_map().items()}
        except Exception:
            return {}
    try:
        return _win_table()
    except Exception:
        return {}


def created(pid, tbl=None):
    tbl = table() if tbl is None else tbl
    row = tbl.get(int(pid or 0))
    if row and row[2]:
        return int(row[2])
    if faked() is not None:        # a fixture said 0; do not fall through to the real machine
        return 0
    try:
        return _win_created(pid)
    except Exception:
        return 0


def name_of(pid, tbl=None):
    tbl = table() if tbl is None else tbl
    row = tbl.get(int(pid or 0))
    return row[1] if row else ""


def is_claude(nm):
    return (nm or "").strip().lower() in CLAUDE_NAMES


def ancestry(pid=None, tbl=None, limit=16):
    """[(pid, name)] walking up from pid (default: this process) to the top of the tree."""
    tbl = table() if tbl is None else tbl
    cur = int(pid or os.getpid())
    out, seen = [], set()
    while cur and cur in tbl and cur not in seen and len(out) < limit:
        seen.add(cur)
        ppid, nm, _c = tbl[cur]
        out.append((cur, nm))
        cur = int(ppid or 0)
    return out


def resolve_claude(tbl=None):
    """(pid, created, how) for the claude.exe that owns this process. (0, 0, "none") if not found.

    Two independent routes, both structural, neither trusting the other:
      env       - CLAUDE_PID, CONFIRMED against the process table to actually be a claude.exe. An
                  unconfirmed env var is just a number; a recycled pid would sail straight through.
      ancestry  - walk our own parents until a claude.exe turns up. Works with no env var at all, which
                  matters because nothing in the documented hook payload promises CLAUDE_PID exists.
    """
    tbl = table() if tbl is None else tbl
    v = (os.environ.get("CLAUDE_PID") or "").strip()
    if v.isdigit():
        p = int(v)
        if is_claude(name_of(p, tbl)):
            return p, created(p, tbl), "env"
    for p, nm in ancestry(None, tbl):
        if is_claude(nm):
            return p, created(p, tbl), "ancestry"
    if v.isdigit() and not tbl:
        # The OS refused a process table. Trust the env var, but say the route was unverified so the
        # caller can label the match instead of overstating it.
        return int(v), 0, "env-unverified"
    return 0, 0, "none"


def identity(tbl=None):
    """The structural fingerprint of this session's lane. No clocks in it.

    claude_pid/claude_started - the process a /clear resets in place. The exact predecessor key.
    host_*                    - the shell that launched it: one powershell.exe per terminal tab, so this
                                still identifies the lane when claude itself has been relaunched. NOT the
                                terminal application, which every tab shares.
    """
    tbl = table() if tbl is None else tbl
    cp, cstart, how = resolve_claude(tbl)
    hp, hstart, hname = 0, 0, ""
    if cp and cp in tbl:
        hp = int(tbl[cp][0] or 0)
        if hp:
            hstart, hname = created(hp, tbl), name_of(hp, tbl)
    return {"claude_pid": cp, "claude_started": cstart, "resolved_by": how,
            "host_pid": hp, "host_started": hstart, "host_name": hname}


def same_process(ident, rec):
    """Is rec's claude.exe the very process described by ident? Exact, or False.

    Both the pid AND the creation time must agree. pid alone is what makes a recycled number look like a
    predecessor, which is the failure this whole module exists to remove.
    """
    try:
        a, b = int(ident.get("claude_pid") or 0), int(rec.get("claude_pid") or 0)
        if not a or a != b:
            return False
        sa, sb = int(ident.get("claude_started") or 0), int(rec.get("claude_started") or 0)
        return bool(sa) and sa == sb
    except Exception:
        return False


def same_terminal(ident, rec):
    """Same host shell - one powershell.exe per tab - so the same terminal lane."""
    try:
        a, b = int(ident.get("host_pid") or 0), int(rec.get("host_pid") or 0)
        if not a or a != b:
            return False
        sa, sb = int(ident.get("host_started") or 0), int(rec.get("host_started") or 0)
        return bool(sa) and sa == sb
    except Exception:
        return False


def process_alive(rec, tbl=None):
    """Is the claude.exe that wrote rec still running, and still the same process?

    This replaces the six-hour POINTER_TTL. A clear happens inside a live claude.exe, so a pointer whose
    process is gone is unclaimable by anyone - collect it. Unbounded in time and tighter than a timer.

    Returns True when it cannot be determined (no process table at all), because deleting a pointer on the
    strength of a failed OS call would throw away the handover the feature exists to deliver.
    """
    tbl = table() if tbl is None else tbl
    if not tbl:
        return True
    try:
        pid = int(rec.get("claude_pid") or 0)
    except Exception:
        return False
    if not pid or pid not in tbl or not is_claude(name_of(pid, tbl)):
        return False
    want = int(rec.get("claude_started") or 0)
    if not want:
        return True          # written before creation times were recorded; liveness is all we can check
    return created(pid, tbl) == want
