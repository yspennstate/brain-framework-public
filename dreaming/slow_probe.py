import os
"""slow_probe.py - measure the things that make this laptop FEEL slow while CPU/RAM/disk read green.

Writes ~/.claude/compute/slow_state.json every run (a 10-minute scheduled task, windowless), read by
slow_sense.py (the hook) in under 50 ms. Budget: ~5-10 s wall, one PowerShell start, no window.

Signals (each with the 2026-09-15 numbers that motivated it, memory
the_slow_laptop_was_the_on_access_scanner_2026_09_15):
  open_ms_scanned   cold file OPEN, no read, on files Defender still scans (WinSxS)   77 ms bad / 0.3 ms good
  open_ms_excluded  the same on the agent tree (should be excluded)                    tells you the exclusions are gone
  msmpeng_pct       Defender engine CPU share of the whole box                          17 pct bad / 5 pct good
  ps_start_cpu_s    CPU burned by one bare PowerShell start                             1.5 s = no native image / 0.3 s good
  ps_start_wall_s   wall time of that start                                             8-14 s bad / <1 s good
  cores_pinned      logical cores over 90 pct while the average is under 60             aggregate CPU hides these
  commit_pct, ram_free_gb, disk_free_pct, exited_pinned, orphan_fragments, comfy_listening, scratch_gb
"""
import ctypes, ctypes.wintypes as wt, json, os, random, socket, subprocess, sys, time

if any(a in ("-h", "--help") for a in sys.argv[1:]):
    # Discovery is not execution: this file does its work at module scope, so the
    # guard must come before the first statement that runs.
    print(__doc__ or os.path.basename(__file__))
    raise SystemExit(0)


OUT = os.path.expanduser(r"~/.claude/compute/slow_state.json")
STATE_DIR = os.path.dirname(OUT)
SCANNED_TREE = r"C:\Windows\WinSxS"
CONTRACT_CACHE = os.path.expanduser(r"~/.brain-framework/state/project_memory/hot-contract-cache.json")
EXCLUDED_TREE = os.path.expanduser(r"~/brain-repo")
SCRATCH = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser(r"~/AppData/Local")), "Temp", "claude")
CREATE_NO_WINDOW = (0x08000000 if os.name == "nt" else 0)


# MEASURED UNTHROTTLED (2026-09-25). This probe runs as a windowless scheduled task at BelowNormal, the profile Windows
# power-throttles (EcoQoS) by default, so it was measuring its own throttle as much as the laptop. Same process, same
# minute, condition order rotated over six rounds: cold opens read 2.82 ms (scanned) and 0.23 ms (excluded) at the
# median throttled, with single rounds of 156.5 and 91.2 ms, against 0.04 and 0.04 ms opted out and 0.10 and 0.07 ms at
# Normal (scratchpad probe_ab_rot.py). His own programs run unthrottled, so the probe opts itself out
# (priority unchanged) and the PowerShell it starts; history rows from here on carry "unthrottled": true, and a reading
# on either side of that line is not comparable with the other.
def _no_power_throttle(handle=None):
    """SetProcessInformation(ProcessPowerThrottling): the execution-speed policy set, and set to off. True on success."""
    class _Throttling(ctypes.Structure):
        _fields_ = [("Version", wt.ULONG), ("ControlMask", wt.ULONG), ("StateMask", wt.ULONG)]
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetCurrentProcess.restype = ctypes.c_void_p
        k32.SetProcessInformation.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, wt.DWORD]
        h = ctypes.c_void_p(handle) if handle is not None else k32.GetCurrentProcess()
        state = _Throttling(1, 1, 0)
        return bool(k32.SetProcessInformation(h, 4, ctypes.byref(state), ctypes.sizeof(state)))
    except Exception:
        return False


FRAGMENTS = {"sleep.exe", "grep.exe", "sed.exe", "head.exe", "tail.exe", "wc.exe", "find.exe", "cat.exe", "sort.exe", "uniq.exe"}

try:
    import psutil
except Exception:
    psutil = None


def _top_dirs(root):
    """The root's subdirectory list, cached for a day beside the state file (WinSxS has ~20k entries; listing it
    80 times per run cost 40 s on 2026-09-15)."""
    key = "".join(c if c.isalnum() else "_" for c in root)
    cache = os.path.join(STATE_DIR, "slow_probe_dirs_%s.json" % key)
    try:
        c = json.load(open(cache, encoding="utf-8"))
        if time.time() - c.get("at", 0) < 86400 and c.get("dirs"):
            return c["dirs"]
    except Exception:
        pass
    dirs = []
    try:
        for e in os.scandir(root):
            if e.is_dir(follow_symlinks=False):
                dirs.append(e.path)
    except Exception:
        pass
    try:
        json.dump({"at": time.time(), "dirs": dirs}, open(cache, "w", encoding="utf-8"))
    except Exception:
        pass
    return dirs


def random_files(root, n, seed):
    """Random files from random subdirectories - different files every run so they are cold; one scandir per pick."""
    rnd = random.Random(seed)
    tops = _top_dirs(root)
    if not tops:
        return []
    out = []
    tries = 0
    while len(out) < n and tries < n * 3:
        tries += 1
        d = rnd.choice(tops)
        try:
            entries = list(os.scandir(d))
        except Exception:
            continue
        files = [e.path for e in entries if e.is_file(follow_symlinks=False)]
        subs = [e.path for e in entries if e.is_dir(follow_symlinks=False)]
        if subs and (not files or rnd.random() < 0.5):
            try:
                files = [e.path for e in os.scandir(rnd.choice(subs)) if e.is_file(follow_symlinks=False)] or files
            except Exception:
                pass
        if files:
            out.append(rnd.choice(files))
    return out


def open_latency_ms(root, n=80, budget_s=20.0, enough=25, settle_s=3.0):
    """Mean cold-open time, bounded in time, and cheapest exactly when the box is worst.

    Three rules, each from something this function actually did:

    1. A TIME BUDGET. Eighty opens at twelve seconds each is over twenty minutes; the recorded maximum for this
       stage was 1022.8 s inside a probe that fires every ten minutes. A measurement that outlasts its own
       interval is stale before it lands.

    2. NO SILENCE AT THE BAD END. The old version returned None below twenty successes, and the caller reads
       None as "cannot tell", which raises nothing - so the worse the disk got, the quieter the alarm became.
       Whatever was measured IS the measurement: seven opens in twenty seconds is a very slow disk, not missing
       data.

    3. STOP EARLY WHEN THE ANSWER IS ALREADY IN. A mean over twenty-five cold opens is not meaningfully worse
       than one over eighty, and on a healthy box all eighty cost under a tenth of a second so nothing is saved
       by stopping. It is the SLOW case where the sample is expensive - measured 2026-09-16, a busy box spent
       the whole 20 s budget here, 22.1 s of a 35.6 s probe run - and the slow case is precisely when an
       instrument should stop adding to the queue it is measuring. So: once there are `enough` opens AND more
       than `settle_s` has passed, the answer is in; go home.

    Returns (mean_ms, count). None only when the tree could not be sampled at all.
    """
    files = random_files(root, n, seed=int(time.time()) // 60)
    if len(files) < 20:
        return None, len(files)
    t = time.perf_counter()
    ok = 0
    for f in files:
        try:
            fd = os.open(f, os.O_RDONLY | getattr(os, "O_BINARY", 0))
            os.close(fd)
            ok += 1
        except Exception:
            pass
        el = time.perf_counter() - t
        if el > budget_s:
            break                                  # hard stop: report what was measured
        if ok >= enough and el > settle_s:
            break                                  # the answer is already in; stop taxing a busy box
    if ok < 1:
        return None, ok
    return (time.perf_counter() - t) * 1000.0 / ok, ok


def process_times_s(handle):
    c, e, k, u = wt.FILETIME(), wt.FILETIME(), wt.FILETIME(), wt.FILETIME()
    if not ctypes.windll.kernel32.GetProcessTimes(handle, ctypes.byref(c), ctypes.byref(e), ctypes.byref(k), ctypes.byref(u)):
        return None
    tot = ((k.dwHighDateTime << 32) + k.dwLowDateTime + (u.dwHighDateTime << 32) + u.dwLowDateTime)
    return tot / 1e7


# THE CAP IS 35 s, NOT 120, AND A TIMEOUT NOW RETURNS A NUMBER (2026-09-18). Nothing anywhere reads this
# stage past two rungs - wall > 4, and the "extreme" wall > 30 in verdict() - so a floor recorded at 35
# answers both questions the probe actually asks, and the 85 s above it bought nothing. Measured over the 40
# runs to 2026-09-18 01:11: this stage errored out 20 times, cost a mean of 76.1 s, and was the second
# largest stage of a probe whose median run was 595 s against its own 600 s schedule - a monitor of
# process-creation cost that had become one of the larger process-creation costs on the box.
# THE ERROR PATH ALSO DROPPED THE RUN. An exception left no ps_start_wall_s at all, so slow_cycles' medians
# and slow_sense's distribution were built only from the runs where the start was fast enough to finish -
# survivorship that understates the very metric they exist to report. Recording the cap keeps those runs in
# the population, on the correct side of the > 30 s rung.
# p.wait() timing out does not end the child; it finishes on its own later (checked - no aged
# "-NonInteractive -Command 1" process was alive after 20 timeouts, so this is not a leak). Killing it is
# what makes the cap real rather than nominal: an unkilled child keeps holding a slot on the exact resource
# being measured.
PS_START_CAP_S = 35


def ps_start():
    cmd = [r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe", "-NoProfile", "-NonInteractive", "-Command", "1"]
    t = time.perf_counter()
    p = subprocess.Popen(cmd, creationflags=CREATE_NO_WINDOW, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if hasattr(p, "_handle"):
        _no_power_throttle(int(p._handle))     # the child inherits the priority, not the opt-out (2026-09-25)
    try:
        p.wait(timeout=PS_START_CAP_S)
    except subprocess.TimeoutExpired:
        try:
            p.kill()
            p.wait(timeout=5)
        except Exception:
            pass
        return float(PS_START_CAP_S), None, True
    wall = time.perf_counter() - t
    cpu = process_times_s(int(p._handle)) if hasattr(p, "_handle") else None
    return wall, cpu, False


def cmd_start_ms():
    t = time.perf_counter()
    subprocess.run([r"C:\Windows\System32\cmd.exe", "/c", "exit"], creationflags=CREATE_NO_WINDOW, capture_output=True, timeout=60)
    return (time.perf_counter() - t) * 1000.0


def commit():
    class MS(ctypes.Structure):
        _fields_ = [("dwLength", wt.DWORD), ("dwMemoryLoad", wt.DWORD), ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong), ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong), ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
    m = MS(); m.dwLength = ctypes.sizeof(MS)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    lim = m.ullTotalPageFile; used = lim - m.ullAvailPageFile
    return round(used / 2**30, 1), round(lim / 2**30, 1), round(100.0 * used / max(1, lim), 1), round(m.ullAvailPhys / 2**30, 1)


# THE COMMIT PERCENTAGE DIVIDES BY A MOVING NUMBER (measured 2026-09-18). Everything in this file and in
# the advisory treats the limit as the fixed 95.4 GiB of a pagefile pinned at initial 32768 MB. It is not
# fixed. Measured across one hour tonight the limit read 95.4, then 102.5, then 106.8, then 105.6, then
# 99.4 GiB - Windows grows the pagefile toward its 98304 MB maximum under demand and SHRINKS it again
# when demand falls. In one 90-second sample commit used fell 3.79 GB, the limit fell 2.94 GB and C: free
# ROSE 2.61 GB, which is the pagefile handing disk back.
# Two consequences, both of which bit me tonight: (1) 94.2 pct of 95.4 and 88.0 pct of 99.4 are not
# comparable readings, and the alarm can arm or clear purely because the denominator moved; (2) I read a
# falling C: free as runaway consumption when it was the pagefile growing, and the same measurement an
# hour later showed it reversing. What is physically meaningful is HEADROOM IN BYTES, and how much room
# the pagefile still has to grow - which is bounded by the configured maximum AND by free disk, so a
# nearly-full disk is what actually turns commit pressure into failed allocations.
# This function only REPORTS; the commit_pct verdict threshold is unchanged, because a high percentage
# still predicts the stall-while-growing that the advisory describes. Read the three together.
def commit_headroom():
    """(headroom_gib, pagefile_now_gib, pagefile_growth_room_gib) - all GiB, matching this file's other *_gb."""
    class MS(ctypes.Structure):
        _fields_ = [("dwLength", wt.DWORD), ("dwMemoryLoad", wt.DWORD), ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong), ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong), ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
    m = MS(); m.dwLength = ctypes.sizeof(MS)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    head = m.ullAvailPageFile
    pf_now = max(0, m.ullTotalPageFile - m.ullTotalPhys)      # the file on disk, near enough
    PF_MAX = 98304 * 2**20                                    # the configured maximum, 96 GiB
    try:
        import shutil
        disk_free = shutil.disk_usage("C:/").free
    except Exception:
        disk_free = 0
    room = max(0, min(PF_MAX - pf_now, disk_free))             # it cannot grow past the max OR past the disk
    return round(head / 2**30, 1), round(pf_now / 2**30, 1), round(room / 2**30, 1)


# RECORD THE APPRAISAL SERVICE'S OWN OP RATE (2026-09-18), so a hypothesis this probe cannot test
# directly answers itself from data it is already collecting. Measured tonight: InventorySvc has been
# up 102.3 hours and has issued 959,489,172 filesystem operations, read 332.8 GB, WRITTEN NOTHING, and
# spent 10.25 hours of CPU - and it is still at 1,628 ops/s while the physical disk shows about 96,
# because most of its work is served from the NTFS metadata cache it is churning. In the same hours
# every file walk on this box collapsed: 158,429 files in 3 seconds yesterday, a comparable 43,120-file
# tree in 3,336 seconds tonight. That is the signature of a cold metadata cache and this service is the
# only thing on the box with the op rate to keep one cold.
# I CANNOT PROVE THE LINK without stopping the service, which is the owner's call (decision 7 in his
# disk memo). What I can do is stop guessing: log the rate beside open_ms_scanned every run, and after
# a few hours slow_cycles can correlate them. A hypothesis that needs an experiment nobody will run is
# worth less than one that answers itself overnight.
# It does NOT appear under its own name - it lives in `svchost.exe -k InvSvcGroup` - so it must be
# found through the service table, never a process-name search. That false negative cost a wrong report
# on 2026-09-17.
# THE PREVIOUS SAMPLE COMES FROM prev, NOT A MODULE GLOBAL. The probe is a FRESH PROCESS every ten
# minutes, so a module-level cache is empty on every run and the rate would be None forever - a
# measurement that silently never measures, which is the failure this file has already shipped once
# tonight. prev is the last run's state dict, the same channel hung_producers and scratch_gb use.
def inventory_svc_rate(prev):
    """(ops_per_s_since_the_previous_probe, lifetime_ops, gb_read, pid) - any element None if unavailable."""
    try:
        import psutil
    except Exception:
        return None, None, None, None
    pid = None
    try:
        for s in psutil.win_service_iter():
            try:
                if s.name() == "InventorySvc":
                    pid = s.pid()
                    break
            except Exception:
                continue
    except Exception:
        return None, None, None, None
    if not pid:
        # STOPPED IS A MEASUREMENT, NOT A SILENCE. If he takes decision 7 and stops this service, the probe
        # must SHOW the difference - a vanished key reads as a broken instrument, and this is the one
        # reading that has to survive the change it exists to justify. Zeros, not None.
        return 0.0, 0, 0.0, 0
    try:
        io = psutil.Process(pid).io_counters()
        ops = io.read_count + io.write_count + io.other_count
        now = time.time()
    except Exception:
        return None, None, None, None
    rate = None
    try:
        p_ops = (prev or {}).get("invsvc_ops")
        p_at = (prev or {}).get("at_epoch")
        p_pid = (prev or {}).get("invsvc_pid")
        # THE PID MUST MATCH, not just the counter direction. A restart resets the counters, and
        # comparing across two processes invents a rate out of two unrelated lifetimes - the
        # ops >= p_ops test alone only catches the restarts that happen to go DOWN, which is the half
        # of the problem that is obvious. [[a_two_snapshot_delta_has_two_blind_spots_2026_09_16]]
        same = (p_pid is None) or (p_pid == pid)
        if same and p_ops is not None and p_at and now > p_at and ops >= p_ops:
            rate = round((ops - p_ops) / (now - p_at), 1)
    except Exception:
        rate = None
    return rate, ops, round(io.read_bytes / 1e9, 1), pid


def disk_free_pct():
    # UNITS: this and every other *_gb in this file divide by 2**30, so they are GiB wearing a
    # GB name - 223.6 here is 240.1 decimal GB, a 7.4 pct gap. The names are published dict keys
    # other code reads, so they are NOT renamed; read them as GiB. The thresholds are GiB too
    # (disk_low < 40 = 42.9 GB). On 2026-09-17 this gap cost three checks and a false alarm that
    # the compute governor had gone blind, before its source settled it in one line.
    free = ctypes.c_ulonglong(); tot = ctypes.c_ulonglong()
    ctypes.windll.kernel32.GetDiskFreeSpaceExW("C:\\", None, ctypes.byref(tot), ctypes.byref(free))
    return round(100.0 * free.value / max(1, tot.value), 1), round(free.value / 2**30, 1)


def comfy_listening():
    """Is ComfyUI's server up? Answered from the kernel's LISTEN table, not by dialling it.

    THIS COST A FULL SECOND OF EVERY PROBE RUN (measured 2026-09-18 04:4x). The old body was a
    `create_connection(..., timeout=1.0)`, and the timeout is paid IN FULL in the normal case - nothing
    listening - because the attempt is not refused, it hangs until the deadline. Three reps, median:

        connect, 1.0 s timeout, nothing listening    1008-1012 ms
        connect, 0.25 s timeout, nothing listening       264 ms
        psutil LISTEN table                                6 ms      <- same answer, 170x cheaper

    Against the probe's own stated budget of 5-10 s wall, one second was 10-20 per cent of it, spent
    every ten minutes to rediscover that a server which is usually down is down.

    BOTH ARMS CONTROLLED, because a check that can only return False is not evidence on the axis it
    claims. Bound a real listener on a spare port and re-ran all three: connect/1.0 s, connect/0.25 s and
    the kernel read ALL returned True (0.5, 0.9, 4.9 ms - the connect is fast when someone answers; the
    second is only ever paid on absence). So the cheap path is not blind, it is just not dialling.

    An exception from net_connections (AccessDenied is possible on Windows) means "could not tell", never
    False - so it falls through to a SHORT connect rather than reporting the server down.
    """
    try:
        import psutil as _ps
        for c in _ps.net_connections(kind="tcp"):
            if c.status == "LISTEN" and c.laddr and c.laddr.port == 8188:
                return True
        return False
    except Exception:
        pass
    try:
        s = socket.create_connection(("127.0.0.1", 8188), timeout=0.25); s.close(); return True
    except Exception:
        return False


def cpu_and_defender():
    """per-core over 2 s plus MsMpEng share of the box over the same window."""
    if psutil is None:
        return {}
    mp = None
    for p in psutil.process_iter(["name"]):
        if (p.info["name"] or "").lower() == "msmpeng.exe":
            mp = p; break
    t0 = None
    if mp is not None:
        try:
            ct = mp.cpu_times(); t0 = ct.user + ct.system
        except Exception:
            t0 = None
    per = psutil.cpu_percent(interval=2.0, percpu=True)
    out = {"cores": len(per), "core_max": max(per), "core_avg": round(sum(per) / len(per), 1),
           "cores_pinned": sum(1 for x in per if x > 90), "per_core": [round(x) for x in per]}
    if t0 is not None:
        try:
            ct = mp.cpu_times(); d = (ct.user + ct.system) - t0
            out["msmpeng_pct"] = round(100.0 * d / (2.0 * len(per)), 1)
        except Exception:
            pass
    return out


def _snapshot():
    """pid, ppid, exe name for every process from ONE Toolhelp snapshot.

    THE 45-SECOND process_iter WAS A STALL, NOT A PROPERTY (re-measured 2026-09-18 04:3x). This
    docstring used to read "psutil.process_iter took 45 s here", and four stages in this file were moved
    onto Toolhelp because of it. Measured five alternating reps on the live box, 609 processes:

        Toolhelp _snapshot     36, 34, 36, 34, 36 ms    median 36
        psutil.process_iter    66,  6, 10,  6,  6 ms    median  6      <- 6x FASTER, not 7500x slower

    So the enumeration choice does not matter: both are milliseconds on a healthy box, both stall when
    the box stalls, and switching everything back would save about 120 ms of a run that takes 973
    seconds. Do not spend a tick on it in either direction. What the 45 s really was is the same thing
    `cpu_defender` at 185 s and `hung` at 316.7 s are tonight - the box's stall applied to an operation
    whose own cost is milliseconds. A number measured inside a stall window and written down as a fact
    about the machine will misdirect whoever reads it next; it misdirected me for an hour.
    """
    class PE32(ctypes.Structure):
        _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ProcessID", wt.DWORD),
                    ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)), ("th32ModuleID", wt.DWORD),
                    ("cntThreads", wt.DWORD), ("th32ParentProcessID", wt.DWORD), ("pcPriClassBase", ctypes.c_long),
                    ("dwFlags", wt.DWORD), ("szExeFile", ctypes.c_wchar * 260)]
    k = ctypes.windll.kernel32
    snap = k.CreateToolhelp32Snapshot(0x2, 0)
    rows = []
    if snap == wt.HANDLE(-1).value:
        return rows
    pe = PE32(); pe.dwSize = ctypes.sizeof(PE32)
    if k.Process32FirstW(snap, ctypes.byref(pe)):
        while True:
            rows.append((pe.th32ProcessID, pe.th32ParentProcessID, pe.szExeFile.lower()))
            if not k.Process32NextW(snap, ctypes.byref(pe)):
                break
    k.CloseHandle(snap)
    return rows


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [("ReadOperationCount", ctypes.c_ulonglong), ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong), ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong), ("OtherTransferCount", ctypes.c_ulonglong)]


def _proc_sample(pid):
    """CPU seconds AND cumulative I/O operations of one process, from ONE OpenProcess. None if refused.

    WHY BOTH ON ONE HANDLE (2026-09-18). This function used to return CPU only, and the advisory this
    probe publishes tells every agent that below ~10 MB/s the ranking that matters is OPERATIONS, not
    bytes - a tree walk or hardlink pass can issue 26,000 ops/s while moving 40 KB/s and is invisible to
    any byte-ranked census. So every agent that read the advice went and did its own pass over the whole
    process table to get it, on the one box whose measured failure mode is that the PROCESS TABLE stalls
    (29 ms per handle, 4,272 ms across 618 - a_seven_fold_median_gap_inside_a_sixty_fold_variance).
    The handle was already open here, opened twice per process per probe, and GetProcessIoCounters needs
    exactly the PROCESS_QUERY_LIMITED_INFORMATION this call already asks for. So the ops ranking now
    costs ZERO extra handles and zero extra passes, and it is a RATE across the same window as the CPU
    figure rather than a lifetime total smeared over every idle minute before it.
    """
    k = ctypes.windll.kernel32
    h = k.OpenProcess(0x1000, False, pid)
    if not h:
        return None
    try:
        cpu = process_times_s(h)
        ops = None
        io = _IO_COUNTERS()
        if k.GetProcessIoCounters(ctypes.c_void_p(h), ctypes.byref(io)):
            ops = (io.ReadOperationCount + io.WriteOperationCount, io.OtherOperationCount)
        return (cpu, ops)
    finally:
        k.CloseHandle(h)


def top_consumers(window_s=2.0, n=10):
    """Per-process CPU RATE over a short window for EVERY process (the instrument the 09-14 memories ask for -
    never cumulative CPU, which just ranks whoever has lived longest). Two Toolhelp snapshots, one OpenProcess per pid."""
    rows = _snapshot()
    t0 = time.perf_counter()
    a = {pid: _proc_sample(pid) for pid, _, _ in rows if pid > 4}
    time.sleep(window_s)
    dt = time.perf_counter() - t0
    out = []
    out_ops = []
    names = {pid: name for pid, _, name in rows}
    for pid, s0 in a.items():
        if s0 is None:
            continue
        s1 = _proc_sample(pid)
        if s1 is None:
            continue
        c0, o0 = s0
        c1, o1 = s1
        if c0 is not None and c1 is not None:
            rate = (c1 - c0) / dt
            if rate > 0.02:
                out.append((round(rate, 2), pid, names.get(pid, "?")))
        if o0 is not None and o1 is not None:
            # SPLIT ON PURPOSE. OtherOperationCount on Windows counts non-read/write I/O, and that
            # INCLUDES SOCKETS - the first live ranking put yt_dlp on top at 20,905 ops/s while it was
            # fetching metadata over the network with --skip-download, which is not disk contention at
            # all. The advisory formula (read+write+other) is kept as ops_s so the two agree, but rw_s
            # is published beside it: rw_s is the disk-ish half, and a row with a huge ops_s and a small
            # rw_s is a NETWORK storm being misread as a metadata storm.
            rw = (o1[0] - o0[0]) / dt
            ops = rw + (o1[1] - o0[1]) / dt
            if ops > 50:                      # below this is noise on a 2 s window
                out_ops.append((int(ops), int(rw), pid, names.get(pid, "?")))
    out.sort(reverse=True)
    out_ops.sort(reverse=True)
    counts = {}
    for _, _, name in rows:
        counts[name] = counts.get(name, 0) + 1
    return ([{"cores": r, "pid": p, "name": nm} for r, p, nm in out[:n]],
            {"procs": len(rows), "conhost": counts.get("conhost.exe", 0), "powershell": counts.get("powershell.exe", 0),
             "python": counts.get("python.exe", 0) + counts.get("pythonw.exe", 0), "bash": counts.get("bash.exe", 0),
             "ffmpeg": counts.get("ffmpeg.exe", 0), "claude": counts.get("claude.exe", 0), "node": counts.get("node.exe", 0)},
            round(sum(r for r, _, _ in out), 2),
            [dict({"ops_s": o, "rw_s": w, "pid": p, "name": nm}, **_label(p))
             for o, w, p, nm in out_ops[:n]])


def _label(pid):
    """A usable name for ONE pid, for the ops-ranked rows only. Returns {} when it cannot be had.

    "python.exe" is not an answer. The first live ops ranking on this box put three python.exe rows on
    top at 4,233 / 1,558 / 792 ops per second against blender at 491, and by the time the pid was asked
    what it was, all three had EXITED - the storm is made of transient helpers, so the label has to be
    taken while the row is being built, not afterwards. Resolving it costs a handle and a PEB read, so it
    is done for the ten rows that made the list and never for the 550 that did not: the whole reason this
    ranking is affordable is that it reuses handles already open, and a cmdline for every process would
    put the cost straight back.
    """
    try:
        import psutil
        cl = psutil.Process(pid).cmdline()
    except Exception:
        return {}
    if not cl:
        return {}
    py = [os.path.basename(x) for x in cl if str(x).lower().endswith(".py")]
    return {"what": py[0] if py else " ".join(str(x) for x in cl)[:70]}


def cpu_set_flags():
    """Per logical processor: (efficiency class, flags) from GetSystemCpuSetInformation. flags bit0 Parked, bit1 Allocated,
    bit3 RealTime. On 2026-09-15 LPs 0-3,10-13 read Allocated|RealTime (10) since boot and the scheduler used only 4-9."""
    n = wt.ULONG(0)
    k = ctypes.windll.kernel32
    k.GetSystemCpuSetInformation(None, 0, ctypes.byref(n), None, 0)
    buf = ctypes.create_string_buffer(n.value)
    k.GetSystemCpuSetInformation(buf, n.value, ctypes.byref(n), None, 0)
    off, out = 0, {}
    while off < n.value:
        size = ctypes.c_ulong.from_buffer(buf, off).value
        if ctypes.c_int.from_buffer(buf, off + 4).value == 0:
            lp = ctypes.c_ubyte.from_buffer(buf, off + 14).value
            eff = ctypes.c_ubyte.from_buffer(buf, off + 18).value
            flags = ctypes.c_ubyte.from_buffer(buf, off + 19).value
            out[str(lp)] = "%s%d" % ("P" if eff else "E", flags)
        off += size
    return out


def dispatcher_health(here=None, settings=None):
    """Is the single-process hook dispatcher wired AND able to work? The failure this guards is silent: if the
    dispatcher cannot start or its list is gone, every hook of a wired event stops firing and nothing says so.
    Structural only - it never RUNS the hooks, because running them would burn once-per-session budgets and append
    fake prompts to the archive. Checks: the enable flag, the script compiles, the list file parses and still holds
    entries for each wired event, and settings.json really points those events at the dispatcher."""
    import py_compile
    here = here or os.path.dirname(os.path.abspath(__file__))          # overridable so the control can test a scratch copy
    settings = settings or os.path.expanduser("~/.claude/settings.json")     # rather than break the live wiring to prove a branch
    out = {"wired": [], "ok": True, "why": []}
    enabled = os.path.exists(os.path.join(here, "HOOK_DISPATCH_ENABLED"))
    disp = os.path.join(here, "hook_dispatch.py")
    lst = os.path.join(here, "hook_dispatch_list.json")
    try:
        s = json.load(open(settings, encoding="utf-8")).get("hooks", {})
    except Exception as e:
        return {"wired": [], "ok": False, "why": ["settings.json unreadable: %s" % str(e)[:60]]}
    for ev, blocks in s.items():
        for b in blocks:
            for h in b.get("hooks", []):
                if "hook_dispatch.py" in (h.get("command", "") + " ".join(h.get("args") or [])):
                    out["wired"].append(ev)
    out["enabled_flag"] = enabled
    if not out["wired"]:
        return out                                  # not wired: nothing to guard
    if not enabled:
        out["ok"] = False; out["why"].append("wired but HOOK_DISPATCH_ENABLED missing - every wired hook is silent")
    try:
        py_compile.compile(disp, cfile=os.path.join(here, "__pycache__", "hook_dispatch.probe.pyc"), doraise=True)
    except Exception as e:
        out["ok"] = False; out["why"].append("hook_dispatch.py does not compile: %s" % str(e)[:80])
    try:
        d = json.load(open(lst, encoding="utf-8"))
        for ev in out["wired"]:
            n = sum(len(b.get("hooks", [])) for b in d.get(ev, []))
            if n == 0:
                out["ok"] = False; out["why"].append("list file has no hooks for %s" % ev)
    except Exception as e:
        out["ok"] = False; out["why"].append("hook_dispatch_list.json unreadable: %s" % str(e)[:60])
    try:
        out["log_age_min"] = round((time.time() - os.path.getmtime(os.path.join(STATE_DIR, "hook_dispatch.log"))) / 60)
    except Exception:
        out["log_age_min"] = None
    return out


def hung_windows(rows):
    """Names of top-level visible windows that are not responding (IsHungAppWindow) - the governor's ui_freeze names none,
    and on 09-15 the owner's own Settings window sat hung 20+ min while agents were blamed."""
    u = ctypes.windll.user32
    names = {pid: name for pid, _, name in rows}
    out = []
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def cb(hwnd, _):
        try:
            if u.IsWindowVisible(hwnd) and u.IsHungAppWindow(hwnd):
                pid = wt.DWORD(0); u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                n = u.GetWindowTextLengthW(hwnd); buf = ctypes.create_unicode_buffer(n + 1); u.GetWindowTextW(hwnd, buf, n + 1)
                out.append("%s:%s" % (names.get(pid.value, str(pid.value)).replace(".exe", ""), buf.value[:40]))
        except Exception:
            pass
        return True
    u.EnumWindows(WNDENUMPROC(cb), 0)
    return out


def disk_rate(window_s=1.0):
    if psutil is None:
        return {}
    try:
        a = psutil.disk_io_counters(); time.sleep(window_s); b = psutil.disk_io_counters()
        return {"disk_read_mb_s": round((b.read_bytes - a.read_bytes) / window_s / 2**20, 1),
                "disk_write_mb_s": round((b.write_bytes - a.write_bytes) / window_s / 2**20, 1)}
    except Exception:
        return {}


def _spare_live_msys(pids):
    """Drop fragments whose Git Bash (MSYS) parent is alive (2026-09-25). bash forks, the child execs and the forked
    Windows process exits, so every sleep, grep or tail a live bash loop starts has a dead WINDOWS parent. Counted by
    the Windows test alone, this probe read 30 fragments at 22:3x on 25 Sep and told every session to reap them by
    PID; all 13 sleeps on the box that minute had a dead Windows parent, the sleep of a live dream-watch loop among
    them. hook_reaper.py (24 Sep) and waste_sense.py already judge by the MSYS parent; this reuses their table so the
    three agree. Read only when there is a candidate. Returns (kept, basis): basis "msys" when the table was read,
    "windows" when it could not be and the Windows-parent list is returned as it was - a count known to be inflated
    (26 Sep 00:16: 37 by that fallback in a minute when the MSYS count was 0), so the verdict never fires on it."""
    if not pids:
        return pids, "msys"
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        if here not in sys.path:
            sys.path.insert(0, here)
        from hook_reaper import msys_table, msys_parent_alive
        table = msys_table()
    except Exception:
        return pids, "windows"
    if not table:
        return pids, "windows"
    return [p for p in pids if not msys_parent_alive(int(p), table)], "msys"


def orphans_and_pinned():
    n = 0
    basis = "unknown"
    try:
        rows = _snapshot()
        alive = {pid for pid, _, _ in rows}
        kept, basis = _spare_live_msys([pid for pid, ppid, name in rows if name in FRAGMENTS and ppid not in alive])
        n = len(kept)
    except Exception:
        pass
    ep = None
    try:
        cs = json.load(open(os.path.join(STATE_DIR, "compute_state.json"), encoding="utf-8"))
        ep = cs.get("exited_pinned", {}).get("count")
    except Exception:
        pass
    return n, ep, basis


PRODUCERS ={"python.exe", "pythonw.exe", "ffmpeg.exe", "dvisvgm.exe", "blender.exe"}


def _produced_recently(pr, minutes=35):
    """Has this process written anything into its working directory lately?

    The advisory that reports hung producers tells a READER to check the newest file in the
    process's own output directory first. The detector never did - it judged on io counters alone,
    which cannot tell a starved worker from a dead one. This closes that gap for the common case
    where the worker's cwd IS where it writes (every narrate.py lane on this box).

    Bounded on purpose: one scandir, no recursion, top level only, and any error means "unknown",
    which the caller treats as no suppression. It must never be the reason a flag is RAISED.

    THE WINDOW MUST MATCH THE WINDOW THE VERDICT IS BASED ON (raised 12 -> 35 min, 2026-09-17).
    The caller flags a producer only at `still >= 2`, which is three samples of a ten-minute task -
    twenty to thirty minutes of observed silence. Asking "did it write in the last TWELVE minutes"
    is asking about half that span, so a job whose write cadence falls between the two is flagged by
    a test its own evidence window would have cleared. Lived case: library_push.py gzips a 27.8 MB
    catalog and writes only when the gzip finishes - 74 s when the box was quiet, then 1360, 1374 and
    1355 s as it contended - so it emits nothing for 23 minutes at a stretch. I sampled its cwd twice
    twenty minutes apart and got 27.8 min stale and then 3.6 min fresh, and called the first reading
    a true positive. It was the same starved-not-hung case this function exists to suppress.
    35 minutes covers the three-cycle window with a margin and still leaves a genuinely dead producer
    flagged on the next pass.
    """
    try:
        d = pr.cwd()
    except Exception:
        return False
    if not d:
        return False
    cutoff = time.time() - minutes * 60
    try:
        with os.scandir(d) as it:
            for e in it:
                try:
                    if e.is_file(follow_symlinks=False) and e.stat().st_mtime >= cutoff:
                        return True
                except OSError:
                    continue
    except OSError:
        return False
    return False


_WAIT_ARG_RE = (r"(?:^|\s)--(not-before|not_before|start|start-at|at|until|after|wait[\w-]*|sleep[\w-]*|gap[\w-]*|every|"
                r"interval|delay[\w-]*|days|hours|tries|retries|retry[\w-]*)(?=[\s=]|$)")
_DATE_RE = r"(20\d\d)[-_]?(\d\d)[-_]?(\d\d)"


def _waits_by_design(pr, psutil):
    """Is this orphan WAITING on purpose - a scheduler asleep until its clock, a retry loop between tries, a server
    holding a port for a client? (2026-09-25) Every producer this detector named at 22:36 that evening was: two
    publish retries (--not-before 16:20 --tries 24 --gap-min 60), the speeches publish chain (--start 10:30 --days 7),
    a one-shot job that sleeps until the YouTube quota resets on 26 Sep, and the channel-picture chooser serving
    127.0.0.1:4213 for his click. The advisory's DO line reads "produced nothing and nothing waits on it: kill it",
    and all five had produced nothing, by design, with no parent - an agent following it would have killed his
    publishing. Three narrow signals, each named in the note so a reader can judge it: a wait or schedule argument,
    a script named for a day not yet past, a listening socket. SUPPRESSOR ONLY, like _produced_recently: it moves a
    process from "hung" to "waiting" (both are recorded) and never raises a flag."""
    import datetime
    import re
    try:
        args = pr.cmdline()
    except Exception:
        return None
    m = re.search(_WAIT_ARG_RE, " ".join(args))
    if m:
        return "waits by its own arguments (--%s)" % m.group(1)
    for a in args[1:8]:
        base = os.path.basename(a)
        if base.lower().endswith((".py", ".pyw")):
            for y, mo, d in re.findall(_DATE_RE, base):
                try:
                    if datetime.date(int(y), int(mo), int(d)) >= datetime.date.today():
                        return "a one-shot job named for a day not yet past (%s)" % base
                except ValueError:
                    pass
            break
    try:
        for c in pr.net_connections(kind="inet"):
            if c.status == psutil.CONN_LISTEN:
                return "serves port %d (waits on a client)" % c.laddr.port
    except Exception:
        pass
    return None


def hung_producers(prev):
    """Orphaned render/convert processes that have produced NOTHING for two probe cycles.

    Reports; never kills. hook_reaper deliberately excludes python daemons after the 2026-09-07
    scar where broad orphan-killing stopped live sessions.

    THE FIRST VERSION OF THIS WAS WRONG AND ITS FIRST FIRING PROVED IT. It sampled CPU and I/O over
    two seconds and flagged anything idle in that window. Its first hit was core_steer.py - the
    core-steering daemon this very probe tells people to expect - orphaned BY DESIGN because a VBS
    launched it, 72.7 MB written over its life, merely asleep between ticks. A two-second window
    cannot tell sleeping from hung.

    So the test is now a COMPARISON ACROSS RUNS: cumulative I/O is recorded each pass, and a process
    counts as hung only when its byte total has not moved across two consecutive probes - about
    twenty minutes - with the same pid and the same start time. A daemon that ticks at any interval
    under that clears itself. Returns (count, note, watch) where watch is carried into the next run.

    IT WAS WRONG A SECOND TIME, 2026-09-17, AND THE FIX IS THE SAME SHAPE. It judged a process by
    its OWN byte total, so every supervisor-with-workers read as hung: a parent that waits on
    children moves no bytes by design. The victim was harvest_loop.py, the owner's YouTube harvest -
    74 minutes old, 50 bytes written, orphaned because its windowless launcher exited - while its
    four children had moved 256 MB and yt_dlp was mid-download. The totals now include descendants,
    so the test finally means what the name says: this SUBTREE has produced nothing.

    KNOWN BLIND SPOT, stated rather than hidden: a producer that legitimately does nothing for more
    than twenty minutes - waiting on a long remote call, say - will be named. The verdict says
    "look", not "kill", and the note carries the age so a reader can judge. Both false positives so
    far were "idle is not dead" in different costumes; suspect that reading first.
    """
    watch = {}
    try:
        import psutil
    except Exception:
        return 0, None, watch, 0, None
    try:
        rows = _snapshot()
    except Exception:
        return 0, None, watch, 0, None
    alive = {pid for pid, _, _ in rows}
    cand = [pid for pid, ppid, name in rows if name in PRODUCERS and ppid not in alive]
    if not cand:
        return 0, None, watch, 0, None
    old = (prev or {}).get("hung_watch") or {}
    now = time.time()
    hung, worst = 0, None
    waiting, wworst = 0, None
    for pid in cand[:40]:
        try:
            pr = psutil.Process(pid)
            ct = pr.create_time()
            age = (now - ct) / 60.0
            if age < 30.0:
                continue
            io = pr.io_counters()
            total = io.read_bytes + io.write_bytes
            # ROLL UP THE CHILDREN (2026-09-17). A SUPERVISOR'S OWN IO NEVER MOVES - it waits on
            # workers, so judging it alone means "this process is idle", not "this subtree is dead".
            # This named the owner's YouTube harvest_loop.py as hung: 74 min old, 50 BYTES written,
            # orphaned because a windowless launcher exited - while its four children had moved
            # 256 MB and yt_dlp was mid-download, with frames landing 54 s earlier. Killing on that
            # verdict would have stopped work he explicitly asked for.
            for _kid in pr.children(recursive=True):
                try:
                    _kio = _kid.io_counters()
                    total += _kio.read_bytes + _kio.write_bytes
                except Exception:
                    pass
        except Exception:
            continue
        was = old.get(str(pid))
        still = 0
        if was and abs(was[0] - ct) < 1.0 and was[1] == total:
            still = int(was[2]) + 1
        watch[str(pid)] = [ct, total, still]
        # STARVED IS NOT HUNG (2026-09-17). A BelowNormal worker on a saturated box looks identical
        # to a dead one under any io-delta test: both show no bytes for two probe cycles.
        # ai_fixedpoints pid 42444 was named here while its newest wav had been written 1.5 min
        # earlier and decoded clean - 180,800 frames at 24 kHz. Its own file mtimes put the whole
        # run at 90.7 files/hour and the last hour at 1.0, a ~90x collapse landing in the window the
        # box hit 12/16 cores pinned with a 129 MB/s bulk read. Killing that throws away three hours
        # of narration to fix nothing. _produced_recently is a SUPPRESSOR ONLY and never raises a
        # flag: a process whose cwd is not its output directory simply gets no suppression and the
        # old behaviour stands, so a wrong guess costs a flag we would have raised anyway.
        if still >= 2 and not _produced_recently(pr):
            why = _waits_by_design(pr, psutil)
            if why:
                waiting += 1
                if wworst is None or age > wworst[1]:
                    wworst = (pid, age, why)
                continue
            hung += 1
            if worst is None or age > worst[1]:
                try:
                    worst = (pid, age, pr.name())
                except Exception:
                    worst = (pid, age, "?")
    note = None
    if worst:
        note = ("pid %d %s: orphaned, %.0f min old, it AND ITS CHILDREN produced nothing across %d "
                "probe cycles" % (worst[0], worst[2], worst[1], 3))
    wnote = None
    if wworst:
        wnote = "pid %d, %.0f min old: %s" % (wworst[0], wworst[1], wworst[2])
    return hung, note, watch, waiting, wnote


def scratch_gb(prev):
    """walk Temp\\claude at most once every 6 hours and never for more than 8 s - the first run took 250 s on
    5.8 GB of session scratch. A partial walk is recorded as a lower bound (scratch_partial=True)."""
    if prev and time.time() - prev.get("scratch_at", 0) < 6 * 3600 and prev.get("scratch_gb") is not None:
        return prev.get("scratch_gb"), prev.get("scratch_at"), prev.get("scratch_partial", False)
    tot = 0
    t0 = time.perf_counter()
    partial = False
    for root, dirs, files in os.walk(SCRATCH):
        for f in files:
            try:
                tot += os.stat(os.path.join(root, f)).st_size
            except Exception:
                pass
        if time.perf_counter() - t0 > 8.0:
            partial = True
            break
    return round(tot / 2**30, 1), time.time(), partial


def starvation(st):
    """Is the box STARVED, or simply full?

    Both look identical in the three signals this verdict used to read - cores pinned, a core at 100 per cent,
    and a PowerShell start that waits many seconds while using almost no processor. The 2026-09-15 case was
    starvation: an eight-core reservation nothing was using, TEN CORES IDLE while six ran flat out. The
    2026-09-16 02:5x case looked identical on those three signals and was the opposite - every usable core at
    100 per cent with the owner away, which is what he asked for ("use the whole card and all the cores",
    2026-09-09) and what the governor itself calls owner_away_open.

    The difference is IDLE CAPACITY sitting beside the saturation, and it costs one line to measure. Cores 14
    and 15 are parked low-power cores that read zero always, so they never count as idle capacity.

    This matters because the two remedies are opposite: starvation says restore the missing cores, a full box
    says leave it alone. An alarm whose advice contradicts a standing owner order teaches agents to skip it.
    """
    per = st.get("per_core") or []
    usable = [x for i, x in enumerate(per) if i < 14]
    idle_usable = sum(1 for x in usable if x < 10)
    st["idle_usable_cores"] = idle_usable
    waiting = (st.get("ps_start_wall_s") or 0) > 4 and (st.get("ps_start_cpu_s") or 9) < 1.0
    return bool(st.get("cores_pinned", 0) >= 6 and st.get("core_max", 0) > 95 and waiting and idle_usable >= 4)


def _core_steer_pools():
    """The render pools as the daemon currently defines them, read from the daemon, with a literal fallback."""
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import core_steer
        return tuple({c for c in range(64) if (m >> c) & 1} for m in core_steer.RENDER_MASKS)
    except Exception:
        return ({2, 3}, {4, 5, 6, 7, 8, 9}, {14, 15})     # last known good, 2026-09-16


def _core_steer_render_names():
    """Executable names the daemon treats as renders on sight, read from the daemon rather than restated."""
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import core_steer
        return {n.lower() for n in core_steer.RENDER_NAMES}
    except Exception:
        return {"ffmpeg.exe", "ffprobe.exe", "blender.exe", "manim.exe"}


def _core_steer_interval(default=20):
    """The daemon's sweep interval, so 'younger than one sweep' means what it says."""
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import core_steer
        return float(core_steer.INTERVAL)
    except Exception:
        return float(default)


def renders_outside_batch():
    """How many film renders are sitting outside the batch pool, counting WORKERS and not shims.

    core_steer classifies a render by its command line and moves it to the batch cores; this is the standing
    check that the classification is still happening, because the failure is silent - a misrouted render still
    renders, just on the owner's cores, and nothing reports it.

    ASK FOR THE NAME FIRST. The first version of this function called process_iter(["name","cmdline",...]),
    which fetches a command line for EVERY process on the box; measured 2026-09-16 that cost 16.8 s of
    processor and 25.6 s of wall clock, inside a probe that runs every ten minutes - the single most expensive
    thing the probe did, added by the same hand that wrote the note about instruments not becoming part of the
    problem. Fetching names, filtering to python, and reading a command line only for those costs 0.02 s of
    processor: the same answer for eight hundredths of the price.

    THE COUNT DROPS CHILDREN OF OTHER MATCHES: the film venv's python.exe is a shim that relaunches the base
    interpreter with the SAME command line, so a naive command-line count returns about twice the real number
    [[a_venv_python_shim_doubles_every_process_count_2026_09_16]].

    A render younger than three sweeps is not counted: it is still on the mask it inherited from its launcher
    and that is normal. The threshold is read from the daemon's own INTERVAL rather than written here - the
    docstring used to say 20 s while the code said 30 and the daemon swept every 20, three numbers for one
    fact. The daemon moved to a 5 s sweep on 2026-09-16 and nothing here had to change.
    Returns (outside, total) and (None, None) when it cannot tell.
    """
    try:
        import psutil
    except Exception:
        return None, None
    # ASK core_steer WHAT ITS POOLS ARE - never restate them here. This line used to read
    # `BATCH = ({2, 3}, {4, 5, 6, 7, 8, 9})`, a copy of the daemon's pool list, and on 2026-09-16 the daemon
    # gained a third pool on logical 14 and 15. The copy did not, so every render CORRECTLY placed on the new
    # pool would have been reported here as a misrouted render - an alarm raised by the instrument against a
    # fix, which is the worst kind because it argues for undoing the thing that helped.
    BATCH = _core_steer_pools()
    now = time.time()
    found = {}
    by_name = _core_steer_render_names()
    for pr in psutil.process_iter(["name"]):
        try:
            name = (pr.info["name"] or "").lower()
            if name in by_name:
                # an encoder is identified by its name alone, and costs no command line to find. Until
                # 2026-09-16 this loop looked only at python-named processes, so a bare ffmpeg.exe was invisible
                # to the check that exists to catch renders running outside their pool - and one was measured at
                # 3.15 cores on the owner's interactive cores while this returned a clean zero.
                found[pr.pid] = (pr.ppid(), pr.create_time(), pr)
                continue
            if not name.startswith("python"):
                continue                              # the name alone is cheap; a command line is not
            cl = " ".join(pr.cmdline() or []).lower()
            if "manim" not in cl:
                continue
            found[pr.pid] = (pr.ppid(), pr.create_time(), pr)
        except Exception:
            continue
    outside = total = 0
    for pid, (ppid, born, pr) in found.items():
        if ppid in found:
            continue                       # a shim's child: the same worker counted a second time
        if now - born < max(3.0 * _core_steer_interval(), 15.0):
            continue                       # younger than a sweep; still on its inherited mask, which is fine
        try:
            aff = set(pr.cpu_affinity())
        except Exception:
            continue
        total += 1
        if aff not in BATCH:
            outside += 1
    return outside, total


def contract_cache_age_h():
    """Hours since the hourly project-memory reconciliation last refreshed its contract cache.

    WHY A DISK PROBE WATCHES THIS. The hourly pass is the largest sustained reader on this box -
    measured 2026-09-16 at 61 MB/s for 45 minutes of every hour - and it had not completed once in 57
    attempts over 56 hours (42 killed at its 2700 s cap, 14 crashed on RuntimeError:
    historical_authoritative_event_requires_reclassification at store.py:393). A job that consumes the
    machine and never produces is exactly what this probe exists to name, and the cache mtime is the
    cheapest honest proxy: a completed pass refreshes it, so an old one means no pass has landed.
    Returns None when the subsystem is not installed - never fire on a box that simply lacks it.
    """
    try:
        return (time.time() - os.path.getmtime(CONTRACT_CACHE)) / 3600.0
    except Exception:
        return None


def open_latency_verdicts(st, starved):
    """Which of the two causes of a slow cold open this is - the scanner, or the disk.

    THE DECIDING FACT IS THE EXCLUDED TREE. An excluded path is not scanned at all, so the scanner CANNOT be
    what makes it slow. That single line settles more than MsMpEng's CPU share does, and it settles the case
    seen live at 2026-09-16 03:16 which the first version of this function got wrong: excluded 62.96 ms while
    the SCANNED tree read 0.27 ms, with MsMpEng at 5.5 pct - just over the busy threshold, so it was blamed on
    the scanner and the reader was sent to re-check exclusions that were working perfectly.

        scanned slow, excluded fast   the signature of scanning cost      -> the scanner
        excluded slow (either way)    cannot be the scanner, by definition -> the disk or that tree's I/O

    MsMpEng's share still decides the scanned-only case, because a saturated scanner burns CPU and a busy disk
    does not; measured 2026-09-16 02:19, scanned 594 ms with MsMpEng at 2 pct was a ten-chapter film rebuild
    reading 309 MB every five seconds, and twenty minutes later both trees read under 0.3 ms unchanged.

    Kept as its own function so it can be controlled both ways without running the whole probe.
    """
    if starved:
        return []
    slow_scanned = (st.get("open_ms_scanned") or 0) > 10
    slow_excluded = (st.get("open_ms_excluded") or 0) > 5
    if not (slow_scanned or slow_excluded):
        return []
    if slow_excluded:
        return ["open_latency_disk_contention"]      # exclusion means unscanned; the scanner is ruled out
    # SCANNED SLOW, EXCLUDED FAST - and this branch used to answer "the disk" for it.
    # The table above calls this case "the signature of scanning cost", then the code contradicted the
    # table whenever MsMpEng's CPU share sat under 5 pct. That is how the 2026-09-18 12:36 sample -
    # scanned 34.27 ms, excluded 2.42 ms, disk_read_mb_s 1.6 - was published to every session on this box
    # as disk contention, sending each one to hunt bulk file work while the EXCLUDED tree was opening in
    # 2.42 ms and the disk was reading 1.6 MB/s. Nothing was contending.
    # CPU share was never the right discriminator. The one case this fallback was built from
    # (2026-09-16 02:19, scanned 594 ms at 2 pct MsMpEng) was a ten-chapter film rebuild reading 309 MB
    # every five seconds - about 62 MB/s - and what made THAT one the disk was the throughput, which this
    # function never read. So read it, on the same ~50 MB/s boundary the reader is already told to use:
    # a genuine bulk read slows every cold open; a scan slows only the tree that is scanned.
    # Below that line the honest answer is the scanner, whose advice text already opens "with the disk
    # idle" and already says the exclusions are not the cause - it is measured on WinSxS, which is
    # deliberately scanned and must never be excluded. MsMpEng over 5 pct still corroborates; it no
    # longer has to carry the call alone.
    if (st.get("disk_read_mb_s") or 0) >= 50:
        return ["open_latency_disk_contention"]
    return ["open_latency_scanned"]


def already_running():
    """True when another probe is mid-run, so this one should stand down.

    THE INSTRUMENT WAS DOUBLING UP DURING EXACTLY THE EPISODES IT MEASURES. SlowProbe10m fires every 10 minutes
    with ExecutionTimeLimit PT5M, and this probe takes longer than five minutes whenever the box is genuinely
    slow - measured from its own history, 6 runs of 332, 348, 305, 1344, 453 and 536 s. Task Scheduler then kills
    the LAUNCHER at the limit; the launcher is a wait-style VBS, so the python child is orphaned and keeps
    running, and because the task instance is gone IgnoreNew lets the next one start. Result, measured: 9 of 121
    runs overlapped another and two ran concurrently at 00:03-00:26 on 2026-09-16, inside the worst slow window
    in the record. Each probe does 80 cold file opens, so the measurement was adding disk load precisely when the
    disk was the thing going wrong, and reading its own contribution back as the symptom.

    A pid file rather than a mutex so a killed probe cannot wedge the next one: if the recorded pid is gone, or
    the file is older than an hour, the lock is stale and this run takes it.
    """
    lock = os.path.join(STATE_DIR, "slow_probe.lock")
    try:
        if os.path.exists(lock):
            age = time.time() - os.path.getmtime(lock)
            other = int(open(lock, encoding="utf-8").read().strip() or 0)
            if age < 3600 and other and other != os.getpid():
                try:
                    import psutil
                    if psutil.pid_exists(other):
                        p = psutil.Process(other)
                        if "slow_probe" in " ".join(p.cmdline() or []):
                            return True          # a real sibling is mid-run
                except Exception:
                    return True                  # cannot tell; the safe answer is to stand down
        with open(lock, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except Exception:
        pass                                     # a lock problem must never stop the probe from running
    return False


def main():
    if already_running():
        # SAY SO RATHER THAN EXIT SILENTLY. slow_sense tells every agent to run this by hand when the box
        # feels slow, and a silent exit 0 looks exactly like a broken probe at precisely that moment.
        print('slow_probe: another probe is mid-run; standing down so two do not measure each other.',
              file=sys.stderr)
        # ...AND SAY IT WHERE SOMEONE WILL READ IT (2026-09-18). stderr was the only witness, and the
        # scheduled launcher is a hidden VBS with no console and no redirect, so every stand-down
        # vanished: no history row, no state write, nothing. A stand-down was indistinguishable from a
        # fire that never happened.
        # WHY THAT IS WORSE THAN A MISSING LINE: slow_history.jsonl is the series every agent measures
        # this box from. Measured today - 374 rows, median gap 10.0 min against a 10-minute schedule, but
        # p90 20.0 and max 50.4, with 39 gaps over 19 minutes and about 41 samples missing from an
        # expected 415, near a tenth of the record. And the absences are BIASED, not random: a run stands
        # down because a sibling is still going, which happens when runs are slow, which happens when the
        # box is stalling. So the quietest-looking data is missing exactly its worst moments, and any
        # distribution taken from it - the fraction of time the disk is quiet, how long a busy streak
        # lasts - is optimistic by an unknown amount. A threshold chosen from that series inherits the bias.
        # The row carries no measurements on purpose: readers pull fields with .get() and type checks, so a
        # row without them is skipped by anything computing a distribution while still being COUNTABLE as a
        # fire that produced nothing.
        try:
            _row = {'at': time.strftime('%Y-%m-%d %H:%M:%S'), 'at_epoch': time.time(),
                    'stood_down': True, 'reason': 'another probe mid-run'}
            with open(os.path.join(STATE_DIR, 'slow_history.jsonl'), 'a', encoding='utf-8') as f:
                f.write(json.dumps(_row, ensure_ascii=True) + chr(10))
        except Exception as _exc:
            # THE SAME SILENT LOSS AS THE MAIN APPEND, in the sibling path (2026-09-18). This block exists
            # so a stand-down is COUNTABLE - the comment above says so - and then dropped its own row on any
            # failure with a bare pass, which puts the count back at zero. Measured today: the 14:46 fire
            # exited 0, wrote no history row, and left no loss row at the main append either, so this return
            # is the only exit-0-before-history path in main and its record was unconditionally discardable.
            try:
                with open(os.path.join(STATE_DIR, 'slow_history_lost.jsonl'), 'a', encoding='utf-8') as _lf:
                    _lf.write(json.dumps({'at': _row.get('at'), 'at_epoch': _row.get('at_epoch'),
                                          'path': 'stand_down',
                                          'exc': type(_exc).__name__,
                                          'detail': ' '.join(str(_exc).split())[:160]},
                                         ensure_ascii=True) + chr(10))
            except Exception:
                pass
        return
    t_all = time.perf_counter()
    prev = None
    try:
        prev = json.load(open(OUT, encoding="utf-8"))
    except Exception:
        pass
    st = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "at_epoch": time.time()}
    st["unthrottled"] = _no_power_throttle()      # before the first measurement; see _no_power_throttle
    stage = {}
    def mark(name, t0): stage[name] = round(time.perf_counter() - t0, 1)
    t0 = time.perf_counter(); st["open_ms_scanned"], st["open_n_scanned"] = open_latency_ms(SCANNED_TREE); mark("open_scanned", t0)
    t0 = time.perf_counter(); st["open_ms_excluded"], st["open_n_excluded"] = open_latency_ms(EXCLUDED_TREE); mark("open_excluded", t0)
    t0 = time.perf_counter(); st.update(cpu_and_defender()); mark("cpu_defender", t0)
    t0 = time.perf_counter()
    try:
        w, c, timed_out = ps_start(); st["ps_start_wall_s"] = round(w, 2); st["ps_start_cpu_s"] = None if c is None else round(c, 2)
        if timed_out: st["ps_start_timeout"] = True
    except Exception as e:
        st["ps_start_error"] = str(e)[:80]
    mark("ps_start", t0)
    t0 = time.perf_counter()
    try:
        st["cmd_start_ms"] = round(cmd_start_ms())
    except Exception:
        pass
    mark("cmd_start", t0)
    # INSTRUMENTED 2026-09-18 because 44 per cent of the probe was in no stage at all. The 03:12:20 run
    # took 1249.7 s and its nine stage_s entries summed to 696.8 - so 553 seconds, nine minutes, were
    # spent somewhere the breakdown does not name, in the one file whose job is to explain where this
    # box's time goes. These four calls and the block after top() were the whole unmarked remainder.
    t0 = time.perf_counter()
    st["commit_gb"], st["commit_limit_gb"], st["commit_pct"], st["ram_free_gb"] = commit()
    try:
        st["commit_headroom_gb"], st["pagefile_gb"], st["pagefile_growth_room_gb"] = commit_headroom()
    except Exception:
        pass
    try:
        st["invsvc_ops_s"], st["invsvc_ops"], st["invsvc_read_gb"], st["invsvc_pid"] = inventory_svc_rate(prev)
    except Exception:
        pass
    st["disk_free_pct"], st["disk_free_gb"] = disk_free_pct()
    st["comfy_listening"] = comfy_listening()          # a socket connect - it can block
    _cc = contract_cache_age_h()
    st["contract_cache_age_h"] = None if _cc is None else round(_cc, 2)
    mark("sysreads", t0)
    t0 = time.perf_counter(); st["orphan_fragments"], st["exited_pinned"], st["orphan_basis"] = orphans_and_pinned(); mark("orphans", t0)
    t0 = time.perf_counter(); st["hung_producers"], st["hung_producer_note"], st["hung_watch"], st["waiting_producers"], st["waiting_note"] = hung_producers(prev); mark("hung", t0)
    t0 = time.perf_counter()
    try:
        st["top"], st["counts"], st["cores_busy_sum"], st["top_ops"] = top_consumers()
    except Exception as e:
        st["top_error"] = str(e)[:80]
    mark("top", t0)
    t0 = time.perf_counter()
    st.update(disk_rate())
    try:
        st["cpu_set_flags"] = cpu_set_flags()
    except Exception:
        pass
    mark("diskrate", t0)
    t0 = time.perf_counter()
    try:
        st["hung_windows"] = hung_windows(_snapshot())   # a second full process snapshot
    except Exception:
        st["hung_windows"] = []
    mark("windows", t0)
    t0 = time.perf_counter()
    try:
        st["dispatcher"] = dispatcher_health()
    except Exception as e:
        st["dispatcher"] = {"ok": False, "why": ["probe error: " + str(e)[:60]], "wired": []}
    mark("dispatcher", t0)
    try:
        cs = json.load(open(os.path.join(STATE_DIR, "compute_state.json"), encoding="utf-8"))
        st["governor"] = {"status": cs.get("status"), "mode": cs.get("mode"), "hung_windows": cs.get("hung_windows"),
                          "owner_active": cs.get("owner_active")}
    except Exception:
        pass
    t0 = time.perf_counter(); st["scratch_gb"], st["scratch_at"], st["scratch_partial"] = scratch_gb(prev); mark("scratch", t0)
    st["stage_s"] = stage
    if st["open_ms_scanned"] is not None: st["open_ms_scanned"] = round(st["open_ms_scanned"], 2)
    if st["open_ms_excluded"] is not None: st["open_ms_excluded"] = round(st["open_ms_excluded"], 2)

    v = []
    # Thresholds widened 2026-09-15 16:5x after both fired on a busy-but-healthy box (excluded-tree open 2.19 ms,
    # MsMpEng 11.2 pct) while real film encodes and Chrome drove 2,468 read ops/s. Scanner cost is proportional to
    # honest work; the signal has to mean "pathological", not "busy", or agents learn to skip it.
    # STARVATION IS DIAGNOSED FIRST, because it inflates every other number this probe takes. On 2026-09-15 the box
    # ran on six usable cores - an eight-core reservation nothing was using - and the probe reported a 570 ms file
    # open and an 87 s PowerShell start, so the hook sent agents to check the scanner exclusions. The exclusions were
    # fine: measured in the same minute by hand, a cold open cost 0.26 ms. A latency taken behind a full run queue
    # measures the queue, not the thing being timed. The tell is a PowerShell start that is slow in WALL time while
    # its own CPU time stays small: waiting, not working.
    # A CPU RESERVATION IS NOW A STANDING ALARM, since 2026-09-15, so that the problem
    # cannot quietly come back. One was applied on 2026-09-05, became effective at the 09-13 restart, and cost
    # him half his machine for two days before anyone measured it - because nothing watched. Now something does: any
    # logical processor held out of the default scheduler is reported every ten minutes, whoever put it there.
    try:
        reserved = sorted(int(lp) for lp, f in (st.get("cpu_set_flags") or {}).items() if int(f[1:]) & 0x2)
    except Exception:
        reserved = []
    st["reserved_cores"] = reserved
    # THE REGISTRY AND THE RUNNING KERNEL DISAGREE FOR A WHILE, and the difference is the whole meaning of the signal.
    # A reserved set only takes effect at a restart, so after it is removed the live flags keep showing it until he
    # reboots. Firing the alarm on that state every ten minutes for days would train every agent to ignore it - the
    # cry-wolf failure this file already had once today. So read the registry as well: a value that is GONE while the
    # kernel still holds cores is a fix waiting for a restart, and says so quietly. A value that is PRESENT and
    # unexplained is the alarm.
    reg_cores = None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SYSTEM\CurrentControlSet\Control\Session Manager\Kernel") as kk:
            raw, _ = winreg.QueryValueEx(kk, "ReservedCpuSets")
        mask = int.from_bytes(bytes(raw), "little")
        reg_cores = [i for i in range(64) if mask >> i & 1]
    except FileNotFoundError:
        reg_cores = []
    except OSError:
        reg_cores = []
    except Exception:
        reg_cores = None
    st["reserved_in_registry"] = reg_cores
    if reg_cores:
        v.append("cores_reserved_from_scheduler")          # a live reservation somebody has to explain
    elif reserved and reg_cores == []:
        v.append("reservation_pending_restart")            # already reversed; the kernel catches up at the next boot
    starved = starvation(st)
    st["starved"] = bool(starved)
    if starved:
        v.append("cpu_starved")
    # A SLOW COLD OPEN HAS TWO CAUSES AND THEY NEED OPPOSITE RESPONSES.
    # A saturated on-access scanner is a scanner BURNING CPU; a busy disk is not. And an EXCLUDED path is not
    # scanned at all, so when both trees rise together the common cause is underneath both of them. Measured
    # 2026-09-16 02:19: scanned 594 ms and excluded 122 ms with MsMpEng at 2 pct, while a ten-chapter film
    # rebuild read 309 MB every five seconds; twenty minutes later, nothing changed, both read under 0.3 ms.
    # The old verdict sent the reader to re-run the exclusion script, which would have been a no-op recorded
    # as a fix.
    st["renders_outside_batch"], st["renders_total"] = renders_outside_batch()
    # the classifier is only useful while it is still classifying, and its failure is silent
    if (st.get("renders_total") or 0) >= 4 and (st.get("renders_outside_batch") or 0) * 2 > st["renders_total"]:
        v.append("renders_not_pooled")
    v.extend(open_latency_verdicts(st, starved))
    _cc = st.get("contract_cache_age_h")
    if _cc is not None and _cc > 3.0: v.append("reconciliation_not_completing")
    if not starved and st.get("msmpeng_pct", 0) > 20: v.append("scanner_hot")
    if (st.get("ps_start_cpu_s") or 0) > 0.8: v.append("powershell_no_native_image")
    # A SLOW POWERSHELL START HAS TWO CAUSES TOO, and the same discriminator separates them. With capacity
    # sitting idle it is a fault worth chasing - on 2026-09-15 21:26 a start waited 77.9 s at 0.31 s of CPU
    # while cores were stranded by a reservation nothing used. With NOTHING idle it is simply the run queue
    # of a box doing the work it was asked to do (2026-09-16 03:16, 16.6 s at 0.36 s CPU, every usable core
    # busy), and calling that "the laptop is slow" trains the reader to discount the message. The cost is
    # real either way - every hook and task launch pays it - so the finding is kept and only the meaning
    # changes.
    if not starved and (st.get("ps_start_wall_s") or 0) > 4:
        # MAGNITUDE OVERRIDES LOAD. Replaying the eight real fires in the history showed the flaw in a
        # load-only rule: 2026-09-15 21:26 waited 77.9 SECONDS with only the parked cores idle, so a pure
        # capacity test called it an ordinary queue and told the reader nothing needed fixing. No healthy
        # box takes over half a minute to start a process however busy it is, so past that it is a fault
        # whatever the cores say. Under it, with nothing idle, it really is the queue.
        extreme = (st.get("ps_start_wall_s") or 0) > 30
        v.append("powershell_start_slow" if (extreme or (st.get("idle_usable_cores") or 0) >= 4)
                 else "powershell_start_slow_queue")
    if st.get("cores_pinned", 0) >= 8 and st.get("core_avg", 100) < 75: v.append("cores_pinned")   # half the box pinned; 6 is a normal film render
    # COMMIT CHARGE IS A PROMISE; THE PAGEFILE IS WHAT WOULD ACTUALLY BE SPENT (2026-09-18).
    # The threshold is unchanged - what changes is that the verdict now travels with the number that
    # tells a reader whether it means strain. Measured at 87.2 pct committed: physical 42.4 GiB used of
    # 63.4 with 21.1 GiB free, across 544 processes private 67.4 GiB against resident 40.4 GiB - so 27.0
    # GiB, 40 pct of the charge, was reserved and never touched - and the pagefile itself was 1.80 GiB
    # used of 32.00 allocated, 5.6 pct. A reader seeing "88 pct committed" reasonably fears paging; a
    # reader seeing it beside "pagefile 5.6 pct used" knows it is reservation. exited_pinned was 1 of
    # 549, checked first, because dead-but-pinned processes carry stale PageFileUsage and inflate any
    # private-byte total.
    try:
        _sw = psutil.swap_memory()
        st["pagefile_used_gb"] = round(_sw.used / 2**30, 2)
        st["pagefile_used_pct"] = round(100.0 * _sw.used / max(_sw.total, 1), 1)
    except Exception:
        st["pagefile_used_gb"] = st["pagefile_used_pct"] = None
    if st["commit_pct"] > 85: v.append("commit_high")
    if st["ram_free_gb"] < 3: v.append("ram_low")
    if st["disk_free_gb"] < 40: v.append("disk_low")   # 1.9 TB drive: 32 GB free stalled the pagefile on 09-14; percent misleads here
    if (st.get("exited_pinned") or 0) > 50: v.append("exited_pinned_high")
    if (st.get("hung_producers") or 0) > 0: v.append("hung_producer")
    # BrainWasteSweeper20m reaps the usual 5-15; 30+ is a runaway - but only on a count judged by the MSYS parent
    if (st.get("orphan_fragments") or 0) > 30 and st.get("orphan_basis") == "msys": v.append("orphan_fragments")
    if (st.get("scratch_gb") or 0) > 10: v.append("scratch_big")
    if st["comfy_listening"]: v.append("comfy_listening")
    if not (st.get("dispatcher") or {}).get("ok", True): v.append("hook_dispatch_broken")
    st["verdict"] = v
    # watchdog for the core-steering daemon (an ONLOGON task needs admin here; the 10-minute probe relaunches it instead)
    # TIMED 2026-09-18: this block can SPAWN a process, and process creation is the operation that stalls
    # on this box - a start measured 82 ms healthy and 12,175 ms inside a stall window. So the cheapest
    # watchdog in the file is also the one most able to cost a minute, and nothing was counting it.
    # Worth watching for the healer pattern too: `fresh` is core_steer's state file being under 120 s old,
    # and on a stalled box a healthy daemon can miss that deadline, which would have this probe relaunch
    # it every ten minutes forever. The relaunch count is in the history as core_steer=="relaunched".
    t0 = time.perf_counter()
    try:
        cs_state = os.path.join(STATE_DIR, "core_steer_state.json")
        stop = os.path.join(os.path.dirname(os.path.abspath(__file__)), "core_steer.STOP")
        launcher = os.path.join(os.path.dirname(os.path.abspath(__file__)), "core_steer_launcher.vbs")
        fresh = os.path.exists(cs_state) and time.time() - os.path.getmtime(cs_state) < 120
        st["core_steer"] = "running" if fresh else ("stopped(STOP file)" if os.path.exists(stop) else "not running")
        if not fresh and not os.path.exists(stop) and os.path.exists(launcher):
            subprocess.Popen(["wscript.exe", "//B", launcher], creationflags=CREATE_NO_WINDOW)
            st["core_steer"] = "relaunched"
    except Exception as e:
        st["core_steer"] = "watchdog error: " + str(e)[:60]
    mark("core_steer", t0)
    st["probe_s"] = round(time.perf_counter() - t_all, 1)
    # HOW MANY FIRES THIS RUN COST (2026-09-18). The task fires every 600 s under IgnoreNew, so a run
    # that takes longer than that suppresses the next fire, and a 20-minute hole in the history is
    # usually not a fault - it is this. Measured over 376 rows spanning 66.4 h: probe_s median 37 s but
    # p90 612 and max 1444, with 40 runs (10.6 pct) over the schedule, and 267 of 375 gaps fall within
    # three minutes of what the previous run's duration predicts.
    # WHY RECORD IT RATHER THAN LET READERS DERIVE IT: a run overruns BECAUSE the box is slow, so the
    # sampler thins out exactly when the measurement matters, and every distribution taken from this
    # file is optimistic unless the reader weights by the interval each row stands for. Deriving that
    # needs the next row, which the row itself cannot see; stating it here makes each row
    # self-describing. Separately and NOT the same number: about 42 fires over the same span vanish
    # with no row at all, which is what the stood_down marker above began to account for.
    st["fires_suppressed"] = int(st["probe_s"] // 600)
    # what the nine stages never accounted for; if this stays large, another block needs a mark
    try:
        st["stage_unaccounted_s"] = round(st["probe_s"] - sum(
            v for v in stage.values() if isinstance(v, (int, float))), 1)
    except Exception:
        pass
    tmp = OUT + ".tmp%d" % os.getpid()
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, indent=1)
    # A LATE RUN MUST NOT STAMP OVER A FRESH ONE (2026-09-18). Two probes run at once here routinely and
    # it is not a race in the file - the write is already atomic via a per-pid tmp and os.replace. It is a
    # race in TIME: whichever run FINISHES last wins, and the one that finishes last is usually the one
    # that started earliest, because it is the slow run. Measured tonight: pid 18784 had been going 20.6
    # minutes with its launcher already dead (Task Scheduler's time limit kills the wscript and leaves the
    # python - [[a_task_cap_kills_the_launcher_and_leaves_the_tree_alive_2026_09_17]]) while a fresh run
    # started under the 03:26 launcher. The orphan finishing later would have replaced the new numbers
    # with numbers from 03:0x, and every consumer - the sense hook, slow_sense, the desk - reads this file
    # as "now". The hook was already saying "probe is STALE, the task may be down" while the task was
    # Running and a probe was 20 minutes into a measurement.
    # st["at_epoch"] is this run's START, so the comparison is start-versus-start: the run that began most
    # recently owns the file. The history append below is NOT guarded - every run's sample is wanted for
    # the distribution, it is only the "current state" claim that a late run must not make.
    try:
        _cur = json.load(open(OUT, encoding="utf-8"))
        _newer = (_cur.get("at_epoch") or 0) > (st.get("at_epoch") or 0)
    except Exception:
        _newer = False           # unreadable or absent state is not a reason to withhold a measurement
    if _newer:
        st["state_write_skipped"] = "a run that started later already wrote this file"
        try:
            os.remove(tmp)
        except OSError:
            pass
    else:
        os.replace(tmp, OUT)
    # the two-day cycle study (owner, 2026-09-15 14:3x): every run appended, one line each, for slow_cycles.py to read
    # COUNT THE LOSS (2026-09-18). This append is the probe's PRIMARY OUTPUT - the comment above calls it
    # "the series every agent measures" - and it was guarded by a bare except/pass, the one place in this
    # file where a failure leaves no trace at all. Measured today: the task fired at 14:46 and reported
    # Last Result 0 while the history gained no row, so the series jumped 14:34 -> nothing across the worst
    # stall of the afternoon. A history that silently drops its hardest samples under-reports precisely the
    # episodes it exists to record. The loss goes to a DIFFERENT file, because whatever stopped the write
    # to this one may still be in the way.
    try:
        with open(os.path.join(STATE_DIR, "slow_history.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(st, ensure_ascii=True) + "\n")
    except Exception as _exc:
        try:
            with open(os.path.join(STATE_DIR, "slow_history_lost.jsonl"), "a", encoding="utf-8") as _lf:
                _lf.write(json.dumps({"at": st.get("at"), "at_epoch": st.get("at_epoch"),
                                      "exc": type(_exc).__name__,
                                      "detail": " ".join(str(_exc).split())[:160]},
                                     ensure_ascii=True) + "\n")
        except Exception:
            pass
    if "--print" in sys.argv:
        print(json.dumps(st, indent=1))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        try:
            with open(os.path.join(STATE_DIR, "slow_probe.err"), "a", encoding="utf-8") as f:
                f.write(time.strftime("%Y-%m-%d %H:%M:%S") + "\n" + traceback.format_exc() + "\n")
        except Exception:
            pass
        raise
