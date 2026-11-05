"""posix_proc.py - the macOS/Linux side of the Win32 process calls the hooks make.

The hooks were written on Windows and read the process table through Get-CimInstance (PowerShell), Toolhelp32
snapshots and OpenProcess. None of those exist here; each hook keeps its Windows path and calls into this module
when os.name != "nt". Everything here is one `ps` call or a syscall: no psutil (absent under `python -s`).

  table()        {pid: (ppid, name, age_minutes, command)}   one `ps` over the whole process table
  parent_map()   {pid: (ppid, name)}
  alive(pid)     True if the pid exists (os.kill(pid, 0); EPERM still means alive)
  idle_seconds() seconds since the last keyboard/mouse input (IOHIDSystem HIDIdleTime), 0.0 if unknown
"""

import sys
if __name__ == "__main__" and any(arg in ("-h", "--help") for arg in sys.argv[1:]):
    print(__doc__ or "No command-line options.")
    raise SystemExit(0)

import ctypes
import os
import re
import subprocess
import sys


def _etime_minutes(s):
    """ps etime is [[dd-]hh:]mm:ss."""
    days = 0
    if "-" in s:
        d, s = s.split("-", 1)
        days = int(d)
    parts = [int(p) for p in s.split(":")]
    while len(parts) < 3:
        parts.insert(0, 0)
    h, m, sec = parts
    return days * 1440 + h * 60 + m + sec / 60.0


def table(timeout=30, cmd_cap=160):
    """{pid: (ppid, name, age_minutes, command)}. On macOS read in-process through libproc (14 ms where `ps` took
    3.7 s at load 67 and 30-75 s at load 250-300, 29 Oct); `ps` elsewhere or on failure. Against `ps` on 547 pids:
    ppid, name and age identical; the command differs only for another user's process (setuid `login`, root
    daemons), where the kernel hides the arguments and the executable path stands in. Zombies are not listed."""
    if sys.platform == "darwin":
        try:
            rows = _libproc_table(cmd_cap)
            if rows:
                return rows
        except Exception:
            pass
    try:
        r = subprocess.run(["ps", "-axww", "-o", "pid=,ppid=,etime=,args="], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
    except (subprocess.TimeoutExpired, OSError):
        return {}
    rows = {}
    for ln in (r.stdout or "").splitlines():
        m = re.match(r"\s*(\d+)\s+(\d+)\s+(\S+)\s+(.*)$", ln)
        if not m:
            continue
        cmd = m.group(4).strip()
        argv0 = cmd.split(" ", 1)[0] if cmd else ""
        try:
            rows[int(m.group(1))] = (int(m.group(2)), os.path.basename(argv0), _etime_minutes(m.group(3)), cmd[:cmd_cap])
        except ValueError:
            continue
    return rows


def start_times(timeout=30):
    """{pid: start epoch seconds}, from ps lstart - stable to the second, so (pid, start) is a process identity."""
    import time
    if sys.platform == "darwin":
        try:
            lib = _libproc()
            res = _bsd_start_times(lib, _all_pids(lib))
            if res:
                return res
        except Exception:
            pass
    try:
        out = subprocess.run(["ps", "-axo", "pid=,lstart="], capture_output=True, text=True, timeout=timeout,
                             env=dict(os.environ, LC_ALL="C")).stdout
    except (subprocess.TimeoutExpired, OSError):
        return {}
    res = {}
    for ln in out.splitlines():
        m = re.match(r"\s*(\d+)\s+(.+?)\s*$", ln)
        if m:
            try:
                res[int(m.group(1))] = int(time.mktime(time.strptime(m.group(2), "%a %b %d %H:%M:%S %Y")))
            except ValueError:
                pass
    return res


def parent_map():
    """{pid: (ppid, name)}. On macOS read in-process through libproc, falling back to `ps` elsewhere or on failure.

    The `ps -axww` table took 30-75 s per call under load 250-300 (waste_sense's census timing log, 29 Oct), and
    waste_sense runs it on every prompt: 12 of its fires hit the dispatcher's 90 s kill that day. libproc answered
    the same table in 3-54 ms with identical parents for every pid both saw (732-753 of them) and the same names."""
    if sys.platform == "darwin":
        try:
            rows = _libproc_parent_map()
            if rows:
                return rows
        except Exception:
            pass
    return {pid: (ppid, name) for pid, (ppid, name, _age, _cmd) in table().items()}


class _BsdShortInfo(ctypes.Structure):
    _fields_ = [("pid", ctypes.c_uint32), ("ppid", ctypes.c_uint32), ("pgid", ctypes.c_uint32),
                ("status", ctypes.c_uint32), ("comm", ctypes.c_char * 16), ("flags", ctypes.c_uint32),
                ("uid", ctypes.c_uint32), ("gid", ctypes.c_uint32), ("ruid", ctypes.c_uint32),
                ("rgid", ctypes.c_uint32), ("svuid", ctypes.c_uint32), ("svgid", ctypes.c_uint32),
                ("rfu", ctypes.c_uint32)]


def _libproc():
    lib = ctypes.CDLL("/usr/lib/libproc.dylib")
    lib.proc_listallpids.argtypes = [ctypes.c_void_p, ctypes.c_int]
    lib.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int]
    lib.proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
    return lib


def _libc():
    libc = ctypes.CDLL(None)
    libc.sysctl.argtypes = [ctypes.POINTER(ctypes.c_int), ctypes.c_uint, ctypes.c_void_p,
                            ctypes.POINTER(ctypes.c_size_t), ctypes.c_void_p, ctypes.c_size_t]
    return libc


def _all_pids(lib):
    n = lib.proc_listallpids(None, 0)
    if n <= 0:
        return []
    pids = (ctypes.c_int * (n + 256))()
    n = lib.proc_listallpids(pids, ctypes.sizeof(pids))
    return [pids[i] for i in range(max(0, n)) if pids[i] > 0]


def _bsd_start_times(lib, pids):
    """{pid: start epoch seconds} - the field ps lstart prints - from PROC_PIDTBSDINFO (pbi_start_tvsec at byte 120
    of the 136-byte proc_bsdinfo). The kernel refuses it for another user's process, so root daemons are absent.
    Not one KERN_PROC_ALL sysctl, which is what ps reads: under load ~95 that call took 1-2 s in three of six fresh
    processes and once 22.8 s, while this per-pid read took 3 ms every time and agreed on 621 of 621 pids (29 Oct)."""
    buf = ctypes.create_string_buffer(136)
    res = {}
    for pid in pids:
        if lib.proc_pidinfo(pid, 3, 0, buf, 136) == 136:
            res[pid] = int.from_bytes(ctypes.string_at(buf, 136)[120:128], "little", signed=True)
    return res


def _procargs(libc, pid, abuf):
    """(argc, bytes after the exec path) from KERN_PROCARGS2, or (0, b'') when the kernel will not show them."""
    sz = ctypes.c_size_t(ctypes.sizeof(abuf))
    if libc.sysctl((ctypes.c_int * 3)(1, 49, pid), 3, abuf, ctypes.byref(sz), None, 0) != 0:
        return 0, b""
    raw = ctypes.string_at(abuf, sz.value)          # not abuf.raw: that copies the whole 1 MB buffer per pid
    i = raw.find(b"\0", 4)                          # int argc, the exec path, NUL padding, then argv
    if i < 0:
        return 0, b""
    while i < len(raw) and raw[i] == 0:
        i += 1
    return int.from_bytes(raw[:4], "little"), raw[i:]


def _vis(s):
    """control characters as ps prints them (a newline in `bash -c` arguments becomes \\012)."""
    return "".join(c if c >= " " and c != "\x7f" else "\\%03o" % ord(c) for c in s)


def _libproc_table(cmd_cap):
    import time
    lib, libc = _libproc(), _libc()
    pids = _all_pids(lib)
    starts = _bsd_start_times(lib, pids)
    now = time.time()
    abuf = ctypes.create_string_buffer(1 << 20)
    pbuf = ctypes.create_string_buffer(4096)
    info = _BsdShortInfo()
    rows = {}
    for pid in pids:
        if lib.proc_pidinfo(pid, 13, 0, ctypes.byref(info), ctypes.sizeof(info)) != ctypes.sizeof(info):
            continue                                # gone, or a zombie
        st = starts.get(pid)
        age = max(0.0, (now - st) / 60.0) if st else 0.0    # 0 = unknown, as waste_sense reads it
        argc, rest = _procargs(libc, pid, abuf)
        cmd = " ".join(a.decode("utf-8", "replace") for a in rest.split(b"\0")[:argc]) if argc else ""
        if not cmd:   # another user's process: the arguments are hidden, the executable path is not
            n = lib.proc_pidpath(pid, pbuf, ctypes.sizeof(pbuf))
            cmd = pbuf.value[:n].decode("utf-8", "replace") if n > 0 else info.comm.decode("utf-8", "replace")
        cmd = _vis(cmd)
        rows[pid] = (int(info.ppid), os.path.basename(cmd.split(" ", 1)[0]), age, cmd[:cmd_cap])
    return rows


def _libproc_parent_map():
    lib, libc = _libproc(), _libc()
    pids = _all_pids(lib)
    argmax = ctypes.c_int(0)
    sz = ctypes.c_size_t(ctypes.sizeof(argmax))
    libc.sysctl((ctypes.c_int * 2)(1, 8), 2, ctypes.byref(argmax), ctypes.byref(sz), None, 0)   # KERN_ARGMAX
    abuf = ctypes.create_string_buffer(max(argmax.value, 4096))
    info = _BsdShortInfo()
    rows = {}
    for pid in pids:
        # PROC_PIDT_SHORTBSDINFO (13) answers for every user's processes, unlike the full BSD info
        if lib.proc_pidinfo(pid, 13, 0, ctypes.byref(info), ctypes.sizeof(info)) == ctypes.sizeof(info):
            rows[pid] = (int(info.ppid), _argv0(libc, pid, abuf) or info.comm.decode("utf-8", "replace"))
    return rows


def _argv0(libc, pid, abuf):
    """basename of argv[0], which is what `ps -o comm` prints (the claude CLI's own comm is its version number);
    '' when the kernel will not show this pid's arguments."""
    argc, rest = _procargs(libc, pid, abuf)
    if not argc:
        return ""
    return os.path.basename(rest.split(b"\0", 1)[0].decode("utf-8", "replace"))


def alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, ValueError, TypeError):
        return False


def idle_seconds():
    try:
        out = subprocess.run(["ioreg", "-c", "IOHIDSystem", "-d", "4"], capture_output=True, text=True, timeout=5).stdout
        m = re.search(r'"HIDIdleTime"\s*=\s*(\d+)', out)
        return int(m.group(1)) / 1e9 if m else 0.0
    except Exception:
        return 0.0


if __name__ == "__main__":
    t = table()
    print("processes:", len(t), "| idle s: %.1f" % idle_seconds(), "| self alive:", alive(os.getpid()), "| pid 999999 alive:", alive(999999))
