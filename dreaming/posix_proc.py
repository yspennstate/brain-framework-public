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
import os
import re
import subprocess


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
    return {pid: (ppid, name) for pid, (ppid, name, _age, _cmd) in table().items()}


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
