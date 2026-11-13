#!/usr/bin/env python3
"""waste_sense.py - the hook that keeps every session honest about what it leaves running.

Owner order 2026-09-07 12:5x: "tell all the agents to judge their idle workers, and make a hook dedicated for this so
that agents are always told to clean up their work." Registered on UserPromptSubmit and Stop. On each fire it lists
the LIVE PROCESSES DESCENDED FROM THIS SESSION (the claude.exe this hook runs under: workers, shells, servers, loops it
spawned), the box totals (processes, console hosts, node workers, the governor's mode), and injects the judgment
the owner asked for: for each descendant, is it producing for anyone? idle, finished-but-alive, duplicated, a poll
loop nobody reads, a shell left open = waste - but since 2026-09-18 the owner has banned shedding load to free resources
(shedding makes everyone start over), so REPORT it and leave it running; only your own finished shell
fragments that own no work may be reaped. At Stop
(end of turn) it says: before you leave, stop what you leave behind, or name why it must stay.

Quiet rule: on UserPromptSubmit it speaks once per session per 20 minutes, and always when the session has more than
8 descendants or the box is past 900 processes or the governor reads throttle/distress. Fail-safe: any error ->
silent exit 0. Reads stdin (hook JSON), writes injection JSON.
    python waste_sense.py --selftest

CHANGED 2026-09-07 13:1x his clock, on "i don't need to see this output, for the waste hook please, fix it so that i
don't see it, i prefer a report hook that had the agent give a report on what they did": the Stop event is now SILENT.
A Stop hook that injects text re-prompts the model, so the old Stop injection produced a fresh one-line process verdict
after every reply (fifteen in a row in the building session, 13:0x). The census is for the agent's own judgment and is
never to be recited to the owner; what the owner wants at the end of a task is a short report of what was done, what
was stopped, and what was left running and why. That instruction now rides on the UserPromptSubmit injection so it is
in context before the work starts. The Stop registration was removed from settings.json and ~/.codex/hooks.json
(backups *.bak_wastesense_stop_20260907); a stray Stop fire returns nothing.
"""
import io, json, os, subprocess, sys, time

HOME = os.path.expanduser("~")
STATE = os.path.join(HOME, ".claude", "dreaming", "waste_sense_state.json")
COMPUTE = os.path.join(HOME, ".claude", "compute", "compute_state.json")
REFIRE_S = 1200
MANY_DESC, MANY_BOX = 8, 900
PS = r"""
$all = Get-CimInstance Win32_Process | Select-Object ProcessId, ParentProcessId, Name, CreationDate, CommandLine
foreach ($p in $all) {
  $cmd = $p.CommandLine
  if ($cmd) { $cmd = $cmd.Replace("`n"," ").Replace("`r"," "); if ($cmd.Length -gt 160) { $cmd = $cmd.Substring(0,160) } }
  $age = if ($p.CreationDate) { ((Get-Date) - $p.CreationDate).TotalMinutes } else { -1 }
  Write-Output ("{0}`t{1}`t{2}`t{3:F0}`t{4}" -f $p.ProcessId, $p.ParentProcessId, $p.Name, $age, $cmd)
}
"""


CENSUS_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "compute", "waste_census.json")
TIMING_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "compute", "waste_census_timing.log")
CENSUS_MAX_AGE_S = 900
# A STALE CENSUS BEATS A HUNG ONE (2026-09-24 02:4x). From about 01:57 every PowerShell start on the box hung (Defender's
# AMSI scan stopped answering), the census went stale after its 15 minutes, and from then on every UserPromptSubmit in
# every session spent the dispatcher's whole 90 s budget in this rebuild ("waste_sense.py rc=1 90.0s timed out", seven
# fires in three minutes): one stuck system service became a 90 s wait on every prompt on the machine. The cached rows
# stay true for every process still alive (see below), so a stale cache is still used under the live overlay; a rebuild
# is tried at most once per CENSUS_RETRY_S whoever fires, and it gets CENSUS_REBUILD_TIMEOUT_S instead of 300 s.
CENSUS_STALE_OK_S = 7 * 24 * 3600
CENSUS_RETRY_S = 600
CENSUS_REBUILD_TIMEOUT_S = 45
CENSUS_ATTEMPT = CENSUS_CACHE + ".attempt"
# THE CACHE NOW HOLDS ONLY WHAT NEVER CHANGES (2026-09-23). A process's parent, image name, start time and command
# line are fixed for its whole life, so a census of any age still describes every process that is STILL ALIVE and
# was alive when it was taken. What goes stale is liveness and the processes born since - and those come on every
# fire from one Toolhelp32 snapshot (no process creation, no per-process handle). So a fire overlays the live
# snapshot on the cached rows and rebuilds the PowerShell census only when the cache is older than
# CENSUS_MAX_AGE_S. Why: the 180 s TTL below still rebuilt on 21 of 23 prompts after the ancestor walk landed
# (19:40-20:22, median 2.9 s, max 56 s), because fleet-wide prompts arrived every two to three minutes and each
# PowerShell start cost up to 10.6 s on the loaded box; and the same measurement found 89 of 954 cached rows dead
# 11 s after the census was taken - a cached row can name a pid that is gone or already reused, which matters
# because the orphan line tells agents to reap by pid. The overlay drops the dead and treats a pid whose parent or
# image changed as a new process.
# THE HISTORY OF THE OLD TTL, kept because its measurements still describe the traffic:
# 45 SECONDS WAS SHORTER THAN THE TRAFFIC IT CACHES (measured 2026-09-18 from the dispatcher log).
# The gap between consecutive UserPromptSubmit events FLEET-WIDE - which is what matters, because this
# cache is shared across every session - has median 84 s over 831 intervals: p25 29, p75 177, p90 316.
# So a 45 s TTL was fresh for only 33.3 pct of prompts and TWO THIRDS of them paid the full PowerShell
# enumeration this cache exists to avoid, which is why waste_sense measured a 4.72 s median and a
# 13.48 s p90 on the prompt path rather than the cost of a JSON read. 180 s is the observed p75 and
# lifts the hit rate to 75.2 pct; 300 s would give 88.9 pct and is the next step if this holds.
# WHY A STALER CENSUS IS STILL A CORRECT ONE: the report it feeds is throttled to REFIRE_S = 1200, so a
# three-minute-old process population cannot change what a twenty-minute cadence says. The docstring's
# own argument - the population does not move meaningfully, and no loud condition is second-by-second -
# holds at 180 s exactly as it did at 45. The exactness that MUST survive is the caller's own tree; it used to be
# guarded by refusing a cache without this process's parent in it, and since 2026-09-23 it comes from the live
# snapshot on every fire instead (census() and _overlay).


def _census_live(timeout=300):
    # DO NOT SWAP THIS FOR psutil. That change was made and REVERTED on 2026-09-18, and the reason is not
    # obvious from here: this box does not stall on SPAWNS, it stalls on the PROCESS TABLE. One
    # Get-CimInstance is a single process-table operation that returns CommandLine for every process;
    # psutil.process_iter needs a HANDLE PER PROCESS, measured at 29 ms each - 4,272 ms across 618.
    # Timed back to back in one quiet minute psutil looked seven times faster (0.78 s against 5.89 s) and
    # that number is what sold the change; production said 7.84 and 11.00 s against a 6.78 s before-median
    # and it was reverted byte-identical within two prompts. A 7x median gap inside a 60x variance (psutil
    # itself ran 0.78, 1.19, 0.35 then 50.82, 2.26, 1.44 the same minute) is not a finding.
    # If you want this hook cheaper, do not make the census cheaper - take it OFF the prompt path, so a
    # fire never rebuilds: one scheduled producer writing the cache, a TTL that covers its cadence. The
    # cost that matters is WHO PAYS FOR THE PROCESS TABLE AND WHEN, not which binary reads it.
    # Measured 2026-09-18: this hook is 2,516 s of 7,419 s of all logged slow-hook time on the box - the
    # single largest item on the prompt path, median slow fire 4.79 s against ~1.5 s for every other hook.
    # Memory: a_seven_fold_median_gap_inside_a_sixty_fold_variance_2026_09_18.
    if os.name != "nt":
        # one `ps` over the process table - the same single-table read, in the same row shape
        import posix_proc
        return posix_proc.table(timeout=timeout)
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", PS], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout, creationflags=(0x08000000 if os.name == "nt" else 0))
    except subprocess.TimeoutExpired:
        return {}
    rows = {}
    for ln in (r.stdout or "").splitlines():
        parts = ln.split("\t", 4)
        if len(parts) == 5:
            try:
                rows[int(parts[0])] = (int(parts[1]), parts[2], float(parts[3]), parts[4])
            except ValueError:
                pass
    return rows


def _parent_map():
    """{pid: (ppid, exe name)} from ONE Toolhelp32 snapshot - a single kernel call with no per-process handle and no
    import beyond ctypes, so it works under the dispatcher's `python -s`, where psutil (a user-site package) is absent.
    Used only to link a hook to an ancestor the cached census already holds; the census itself stays the CIM read."""
    if os.name != "nt":
        import posix_proc
        return posix_proc.parent_map()
    import ctypes
    from ctypes import wintypes

    class PE32(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    k32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PE32)]
    k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PE32)]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    tc = time.perf_counter()
    snap = k32.CreateToolhelp32Snapshot(0x2, 0)       # TH32CS_SNAPPROCESS
    _PHASE["snapc"] = time.perf_counter() - tc
    if not snap or snap == wintypes.HANDLE(-1).value:
        return {}
    out = {}
    try:
        e = PE32()
        e.dwSize = ctypes.sizeof(PE32)
        ok = k32.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            out[int(e.th32ProcessID)] = (int(e.th32ParentProcessID), e.szExeFile)
            ok = k32.Process32NextW(snap, ctypes.byref(e))
    finally:
        k32.CloseHandle(snap)
    return out


def _proc_details(pids):
    """{pid: (age_min, command line)} for a handful of live pids, one limited-query handle each (no process
    creation). Used only for the session's own descendants and orphan candidates that are younger than the cached
    census, so the report can still say what they are and how old."""
    if os.name != "nt":
        # libproc reads the whole table in ~20 ms on the Mac, so one read answers any handful of pids
        import posix_proc
        t = posix_proc.table()
        return {p: (t[p][2], t[p][3]) for p in pids if p in t}
    import ctypes
    from ctypes import wintypes

    class USTR(ctypes.Structure):
        _fields_ = [("Length", ctypes.c_ushort), ("MaximumLength", ctypes.c_ushort), ("Buffer", ctypes.c_void_p)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    nt = ctypes.WinDLL("ntdll")
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    nt.NtQueryInformationProcess.restype = ctypes.c_long
    nt.NtQueryInformationProcess.argtypes = [wintypes.HANDLE, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong,
                                             ctypes.POINTER(ctypes.c_ulong)]
    out = {}
    for pid in pids:
        h = k32.OpenProcess(0x1000, False, int(pid))          # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            continue
        try:
            ft = [wintypes.FILETIME() for _ in range(4)]
            age = 0.0
            if k32.GetProcessTimes(h, *[ctypes.byref(f) for f in ft]):
                start = ((ft[0].dwHighDateTime << 32) | ft[0].dwLowDateTime) / 1e7 - 11644473600.0
                age = max(0.0, (time.time() - start) / 60.0)
            cmd, size = "", 4096
            for _ in range(2):                                 # ProcessCommandLineInformation; retry once if too small
                buf, need = ctypes.create_string_buffer(size), ctypes.c_ulong(0)
                st = nt.NtQueryInformationProcess(h, 60, buf, size, ctypes.byref(need))
                if st == 0:
                    us = USTR.from_buffer(buf)
                    if us.Buffer and us.Length:
                        cmd = ctypes.wstring_at(us.Buffer, us.Length // 2)
                    break
                if need.value <= size:
                    break
                size = need.value + 64
            out[int(pid)] = (age, cmd.replace("\n", " ").replace("\r", " ")[:160])
        finally:
            k32.CloseHandle(h)
    return out


def _overlay(snap, blob):
    """Live rows from the snapshot; age and command line from the cached census when it saw the SAME process - same
    pid, same parent, same image. A pid the cache never saw, or saw as something else (pids are reused within
    minutes at this churn), reads as new: age 0, command line unknown."""
    el_min = max(0.0, (time.time() - float(blob.get("at", 0))) / 60.0)
    cached = blob.get("rows", {})
    rows = {}
    for pid, (ppid, name) in snap.items():
        c = cached.get(str(pid))
        if c and len(c) >= 4 and int(c[0]) == ppid and str(c[1]).lower() == name.lower():
            age = float(c[2])
            rows[pid] = (ppid, name, age + el_min if age >= 0 else age, c[3])
        else:
            rows[pid] = (ppid, name, 0.0, "")
    return rows


_LAST = ["", 0.0, 0]          # the census path this fire took, what it cost, how many rows
_PHASE = {"fill": 0.0, "desc": 0, "load": 0.0, "snap": 0.0, "snapc": 0.0, "io": 0.0}


def _priority():
    """This process's priority class (0x20 Normal, 0x4000 BelowNormal, 0x40 Idle); 0 if unreadable. A private
    WinDLL instance, so the argtypes set here never leak into another hook running in the same dispatcher."""
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        k32.GetPriorityClass.restype = wintypes.DWORD
        k32.GetPriorityClass.argtypes = [wintypes.HANDLE]
        return int(k32.GetPriorityClass(k32.GetCurrentProcess()))
    except Exception:
        return 0


def _timing(t0):
    """One line per fire: the census path and its cost, the lookups for processes the census never saw, and the
    whole fire - so the prompt-path price is measured, not inferred from the dispatcher's slow rows (which only see
    fires over 1 s and cannot say where the time went)."""
    try:
        if os.path.exists(TIMING_LOG) and os.path.getsize(TIMING_LOG) > 2_000_000:
            os.replace(TIMING_LOG, TIMING_LOG + ".1")
        with open(TIMING_LOG, "a", encoding="utf-8") as fh:
            fh.write("%s %s %.3fs rows=%d fill=%.3fs total=%.3fs desc=%d load=%.3fs snap=%.3fs snapc=%.3fs "
                     "io=%.3fs pri=%x pid=%d ppid=%d\n" % (
                time.strftime("%Y-%m-%d %H:%M:%S"), _LAST[0], _LAST[1], _LAST[2], _PHASE["fill"],
                time.perf_counter() - t0, _PHASE["desc"], _PHASE["load"], _PHASE["snap"], _PHASE["snapc"],
                _PHASE["io"], _priority(), os.getpid(), os.getppid()))
    except Exception:
        pass


def census():
    """The box's processes as {pid: (ppid, name, age_min, command line)}.

    This ran on EVERY UserPromptSubmit in EVERY session and cost 3.8-6.9 s (median 5.2 s over 555
    processes) - a PowerShell enumeration whose result the 20-minute throttle discards about
    ninety-five per cent of the time. It cannot be gated on the throttle instead, because `loud` -
    the condition that breaks THROUGH the throttle when the box is in trouble - is computed from
    this very census.

    So the PowerShell census is taken at most every CENSUS_MAX_AGE_S and shared across sessions, and every fire
    overlays a live Toolhelp32 snapshot on it (see the note at CENSUS_MAX_AGE_S): liveness, parents and the
    caller's own tree are exact on every fire, and only the age and command line of a process born since the
    census are missing until the next one (main() looks those up for the few that the report names).
    Before this, the cache was refused unless the hook's parent was in it; under the dispatcher every hook runs
    as `bash -c python -s hook_dispatch.py`, the parent is a bash spawned a moment before, and that refusal
    rebuilt the census on nearly every fire (446 slow fires on 09-23, up to 90 s each).
    """
    t0 = time.perf_counter()
    blob, age = None, None
    try:
        with open(CENSUS_CACHE, encoding="utf-8") as fh:
            blob = json.load(fh)
        _PHASE["load"] = time.perf_counter() - t0
        age = time.time() - float(blob.get("at", 0))
    except Exception:
        blob, age = None, None
    usable = age is not None and age < CENSUS_STALE_OK_S
    if usable and (age < CENSUS_MAX_AGE_S or not _rebuild_due()):
        rows = _overlay_live(blob, t0, "snapshot" if age < CENSUS_MAX_AGE_S else "stale")
        if rows:
            return rows
    # A failed snapshot or unreadable cache must still respect the shared retry
    # budget; otherwise every prompt starts another expensive census rebuild.
    if not _rebuild_due():
        _LAST[:] = ["rebuild-backoff", time.perf_counter() - t0, 0]
        return {}
    _mark_rebuild_attempt()
    rows = _census_live(CENSUS_REBUILD_TIMEOUT_S)
    if rows:
        try:
            os.makedirs(os.path.dirname(CENSUS_CACHE), exist_ok=True)
            tmp = CENSUS_CACHE + ".tmp%d" % os.getpid()
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"at": time.time(), "rows": {str(k): list(v) for k, v in rows.items()}}, fh)
            os.replace(tmp, CENSUS_CACHE)
        except Exception:
            pass
        _LAST[:] = ["rebuild", time.perf_counter() - t0, len(rows)]
        return rows
    if usable:
        rows = _overlay_live(blob, t0, "stale-after-failed-rebuild")
        if rows:
            return rows
    _LAST[:] = ["rebuild-failed", time.perf_counter() - t0, 0]
    return {}


def _overlay_live(blob, t0, path):
    """The live snapshot over the cached rows; {} when this process is not in the snapshot."""
    try:
        ts = time.perf_counter()
        snap = _parent_map()
        _PHASE["snap"] = time.perf_counter() - ts
        if os.getpid() in snap:
            rows = _overlay(snap, blob)
            _LAST[:] = [path, time.perf_counter() - t0, len(rows)]
            return rows
    except Exception:
        pass
    return {}


def _rebuild_due():
    try:
        return time.time() - os.path.getmtime(CENSUS_ATTEMPT) >= CENSUS_RETRY_S
    except OSError:
        return True


def _mark_rebuild_attempt():
    try:
        with open(CENSUS_ATTEMPT, "w", encoding="utf-8") as fh:
            fh.write("%.0f\n" % time.time())
    except Exception:
        pass


def _fill_unknown(rows, pids):
    """Give the named pids that the cached census never saw their real age and command line."""
    todo = [p for p in pids if p in rows and not rows[p][3]]
    if not todo:
        return
    try:
        for pid, (age, cmd) in _proc_details(todo[:40]).items():
            ppid, name, _, _ = rows[pid]
            rows[pid] = (ppid, name, age, cmd)
    except Exception:
        pass


def my_session_root(rows):
    """Walk up from this hook to the claude.exe (or node/codex host) that owns the session."""
    pid = os.getpid()
    chain = []
    for _ in range(12):
        if pid not in rows:
            break
        chain.append(pid)
        ppid, name, _, _ = rows[pid]
        n = name.lower()
        n = n[:-4] if n.endswith(".exe") else n   # the Mac names hosts claude / codex / node, with no .exe
        if n in ("claude", "codex") or (n == "node" and "claude" in rows[pid][3].lower()):
            return pid, chain
        pid = ppid
    return None, chain


REUSE_SLACK_MIN = 1.5      # census ages are whole minutes; a real child is never older than its parent by more


def _stale_link(rows, child, parent):
    """True when `parent` cannot have created `child` because the child is older: the child's real creator died and
    Windows gave its pid to a newer process. Seen 2026-09-23 22:59, a 74-minute film worker listed under another
    session whose 4-minute bash held its dead parent's pid - and the report asks each session to clean up its own. A
    parent of unknown age (0: born after the census) is younger than CENSUS_MAX_AGE_S. Unknown child age: kept."""
    try:
        ca, pa = float(rows[child][2]), float(rows[parent][2])
    except Exception:
        return False
    if ca <= 0:
        return False
    if pa <= 0:
        pa = CENSUS_MAX_AGE_S / 60.0
    return ca > pa + REUSE_SLACK_MIN


def _subtree(rows, root):
    """Every pid below root, unfiltered (a reused pid can close a parent loop, so each is visited once)."""
    kids = {}
    for pid, v in rows.items():
        kids.setdefault(v[0], []).append(pid)
    out, seen, stack = [], {root}, [root]
    while stack:
        for c in kids.get(stack.pop(), ()):
            if c not in seen:
                seen.add(c); out.append(c); stack.append(c)
    return out


HARNESS = ("shell-snapshots", "snapshot-bash", "snapshot-zsh", "snapshot-powershell",
           "caffeinate -i -t ")   # Claude Code on the Mac keeps one under every session
_PARSER_SEEN = {}


def _harness_parser(cmd):
    """Claude Code's PowerShell tool keeps one long-lived helper per session that parses each command before it runs:
    `powershell.exe -NoProfile -NonInteractive -NoLogo -EncodedCommand <b64>`, whose decoded script reads base64 from
    stdin and calls Language.Parser]::ParseInput. Its command line names no snapshot file, so until 31 Oct this hook
    listed it on every prompt as the session's oldest idle worker (pid 59772, 1168 min). Plumbing,
    not work: recognised by decoding the command, cached per command line."""
    if not cmd or "-EncodedCommand" not in cmd:
        return False
    hit = _PARSER_SEEN.get(cmd)
    if hit is not None:
        return hit
    hit = False
    try:
        import base64, re
        m = re.search(r"-EncodedCommand\s+(\S+)", cmd)
        if m:
            txt = base64.b64decode(m.group(1)).decode("utf-16-le", "ignore")
            hit = "Language.Parser]::ParseInput" in txt and "[Console]::In.ReadToEnd()" in txt
    except Exception:
        hit = False
    _PARSER_SEEN[cmd] = hit
    return hit


def descendants(rows, root, exclude):
    """Live descendants of the session host, minus the harness's own plumbing: the tool's persistent shells (their
    command line names a shell-snapshots file) and any console host whose siblings are all plumbing. Without this the
    Stop injection fired on every turn about the tool's own bash and conhost (measured in the building session 12:5x)."""
    out, stack, seen = [], [root], {root}
    while stack:
        p = stack.pop()
        for pid, (ppid, name, age, cmd) in rows.items():
            if ppid == p and pid not in exclude and pid not in seen:
                if _stale_link(rows, pid, p):
                    continue
                seen.add(pid); out.append((pid, name, age, cmd)); stack.append(pid)
    real = [d for d in out if not any(h in (d[3] or "") for h in HARNESS) and not _harness_parser(d[3])]
    if all(d[1].lower() in ("conhost.exe", "openconsole.exe") for d in real):
        real = []
    return real


# Processes whose parent PID is no longer alive belong to NO session: descendants() walks DOWN from a
# session root through LIVE parent links, so a leaked process leaves every census at the exact moment it
# starts leaking. Measured 2026-09-14: 31 orphaned shell fragments (head/sort/du/grep/find/sleep), the
# oldest 331 minutes old, were never once named by this hook while it faithfully listed conhosts; reaping
# 28 of them took the processor queue from 135 to 93 on 16 cores. These names are unambiguous pipeline
# fragments - never a daemon, never a service, never csrss - so they are safe to name and safe to reap.
FRAGMENTS = ("head.exe", "sort.exe", "sleep.exe", "du.exe", "grep.exe", "find.exe", "wc.exe",
             "tail.exe", "cat.exe", "uniq.exe", "sed.exe", "awk.exe", "xargs.exe", "cut.exe")
ORPHAN_MIN_AGE_MIN = 5


def orphan_fragments(rows):
    """Shell-pipeline fragments with no living parent, older than ORPHAN_MIN_AGE_MIN.

    Attributable to nobody, so no session's descendant walk can ever see them. Age-gated so a pipeline
    running right now is never flagged."""
    out = []
    for pid, (ppid, name, age, cmd) in rows.items():
        if name.lower() not in FRAGMENTS:
            continue
        if ppid in rows:
            continue
        try:
            if int(age) < ORPHAN_MIN_AGE_MIN:
                continue
        except Exception:
            continue
        out.append((pid, name, age, cmd))
    out.sort(key=lambda d: -d[2])
    return _spare_live_msys(out)


def _spare_live_msys(cands):
    """Drop candidates whose MSYS parent is alive (2026-09-24). Every program a Git Bash script starts has a Windows
    parent that is already gone while its MSYS parent runs on, so the Windows test named the sleep of a live
    background loop, and a grep reading a live test run, as orphans and told agents to reap them by PID. hook_reaper.py got the same fix first; its MSYS table is reused here, read
    only when there is a candidate, so an ordinary prompt pays nothing. If the table cannot be read, the old answer
    stands."""
    if not cands:
        return cands
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        if here not in sys.path:
            sys.path.insert(0, here)
        from hook_reaper import msys_table, msys_parent_alive
        table = msys_table()
    except Exception:
        return cands
    if not table:
        return cands
    return [c for c in cands if not msys_parent_alive(int(c[0]), table)]


def box_totals(rows):
    names = [v[1].lower() for v in rows.values()]
    mode = "?"
    try:
        d = json.load(io.open(COMPUTE, encoding="utf-8"))
        mode = "%s/%s" % (d.get("status"), d.get("mode"))
    except Exception:
        pass
    return len(rows), names.count("conhost.exe"), names.count("node.exe"), names.count("powershell.exe"), mode


def _throttled(session, event):
    try:
        now = time.time()
        st = {}
        if os.path.exists(STATE):
            st = json.load(io.open(STATE, encoding="utf-8"))
        if not isinstance(st, dict):
            st = {}
        st = {k: v for k, v in st.items() if now - float(v) < 6 * 3600}
        key = session + "|" + event
        if event == "UserPromptSubmit" and key in st and now - float(st[key]) < REFIRE_S:
            return True
        st[key] = now
        tmp = "%s.tmp.%d" % (STATE, os.getpid())
        io.open(tmp, "w", encoding="utf-8").write(json.dumps(st))
        try:
            os.replace(tmp, STATE)
        except OSError:
            try: os.remove(tmp)
            except OSError: pass
        return False
    except Exception:
        return False


def render(event, desc, totals, orphans=()):
    total, con, node, psh, mode = totals
    lines = ["[waste-sense] owner order 2026-09-07: judge your idle workers and clean up your work. This session runs %d live process(es) "
             "under it; the box has %d processes (%d console hosts, %d node, %d PowerShell; governor %s)." % (len(desc), total, con, node, psh, mode)]
    hooks = [d for d in desc if any(k in d[3] for k in ("dreaming\\", "dreaming/", "ERROR_GRAPH", "project_memory_hook", "mailbox_hook"))]
    work = [d for d in desc if d not in hooks]
    work.sort(key=lambda d: -d[2])
    for pid, name, age, cmd in work[:12]:
        lines.append("  - pid %d %s, %d min: %s" % (pid, name, age, (cmd or "")[:110]))
    if len(work) > 12:
        lines.append("  - ... %d more" % (len(work) - 12))
    lines.append("  Not shown: anything you launched DETACHED (start /b, Start-Process, a VBS launcher, a scheduled task, a background "
                 "server) - those are orphans by design and only you know their operands; judge them too.")
    if orphans:
        mins = max(d[2] for d in orphans)
        lines.append("  ORPHANED SHELL FRAGMENTS, owned by no session (%d, oldest %d min): %s. A dead parent removes a "
                     "process from every session's census at the moment it becomes waste, so nobody is asked about these "
                     "and they accumulate until the scheduler feels it. They are finished pipeline pieces blocked on a "
                     "pipe whose other end is gone: reap them by explicit PID now (never by a name pattern - it matches "
                     "your own shell)." % (len(orphans), mins,
                     ", ".join("%s(%d)" % (n, p) for p, n, a, c in orphans[:8])))
    lines.append("  For each process above ask: producing, for whom? Idle, finished-but-alive, duplicated, a poll loop nobody reads, a shell "
                 "left open = waste. DO NOT STOP IT TO FREE RESOURCES (standing rule since 2026-09-18: shedding load "
                 "makes everyone start over, so running work is reported, not stopped). Killing work means he starts over, which costs "
                 "him more than the RAM is worth. You may still reap YOUR OWN finished shell fragment that provably owns no work (a "
                 "grep or head blocked on a dead pipe); everything else stays up and we power through. Report it, do not shed it. "
                 "Hook invocations are swept every 20 min (BrainWasteSweeper20m).")
    lines.append("  This census is for YOUR judgment - never recite it to the owner (his order, 13:1x 09-07). When the task is done, end your "
                 "final message with a short REPORT: what you did, what you stopped, and what you left running and why - one line each, "
                 "no PID lists. If a turn did no new work, say so in one line and stop; do not re-judge the same processes every turn.")
    return "\n".join(lines)


def _once(session, block):
    """The standing paragraphs (about 1,500 of the 1,926 chars a fire) go to a context once - again after a
    compaction or three hours; the census lines go every time (4 Nov 2026; 95 pct of this
    hook's 24 h text was a repeat to the same session). Any failure sends the block whole."""
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from once_per_context import filter_lines
        lines = block.split("\n")
        live = lambda l: l.startswith(("[waste-sense]", "  - pid", "  - ...", "  ORPHANED"))
        out, whole, at = filter_lines(session, "waste_sense", lines, keep=live)
        if whole:
            return block
        out.append("  (the standing rules - judge, do not shed, report in one line at the end - went to this context "
                   "whole at %s and are unchanged)" % time.strftime("%H:%M", time.localtime(at)))
        return "\n".join(out)
    except Exception:
        return block


def main():
    t0 = time.perf_counter()
    _LAST[:] = ["", 0.0, 0]          # only a census taken by THIS fire is logged (the selftest calls main() after census())
    try:
        _main()
    finally:
        if _LAST[0]:
            _timing(t0)


def _main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    event = data.get("hook_event_name") or "UserPromptSubmit"
    session = str(data.get("session_id") or "")
    if event == "Stop":
        return  # silent by owner order 2026-09-07 13:1x - a Stop injection re-prompts the model every turn
    rows = census()
    if not rows:
        return
    root, chain = my_session_root(rows)
    tf = time.perf_counter()
    if root:
        _fill_unknown(rows, _subtree(rows, root))
    _fill_unknown(rows, [p for p, v in rows.items() if v[1].lower() in FRAGMENTS and v[0] not in rows])
    _PHASE["fill"] = time.perf_counter() - tf
    desc = descendants(rows, root, set(chain)) if root else []
    _PHASE["desc"] = len(desc)
    orphans = orphan_fragments(rows)
    ti = time.perf_counter()
    totals = box_totals(rows)
    _PHASE["io"] = time.perf_counter() - ti
    total, con, node, psh, mode = totals
    # Orphans belong to nobody, so a quiet session would never be told about them; a pile of them is loud.
    loud = (len(desc) > MANY_DESC or total > MANY_BOX or len(orphans) >= 10
            or any(w in mode for w in ("throttle", "distress")))
    ti = time.perf_counter()
    quiet = (not loud) and _throttled(session, event)
    if loud:
        _throttled(session, event)
    _PHASE["io"] += time.perf_counter() - ti
    if quiet:
        return
    if not desc and not orphans:
        return
    block = render(event, desc, totals, orphans)
    block = _once(session, block)
    sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": block}}))


def _selftest():
    # the overlay: a dead pid is dropped, a pid with another parent or image is a new process, a known one ages
    snap = {10: (1, "a.exe"), 11: (10, "b.exe"), 12: (99, "c.exe"), 14: (10, "d.exe")}
    blob = {"at": time.time() - 120, "rows": {"10": [1, "A.EXE", 5.0, "cmd a"], "11": [7, "b.exe", 3.0, "cmd b"],
                                              "13": [10, "gone.exe", 1.0, "dead"], "14": [10, "x.exe", 2.0, "cmd x"]}}
    ov = _overlay(snap, blob)
    assert set(ov) == {10, 11, 12, 14}, "a pid missing from the snapshot must be dropped"
    assert ov[10][3] == "cmd a" and 6.9 < ov[10][2] < 7.1, "a known pid keeps its command line and ages"
    assert ov[11] == (10, "b.exe", 0.0, ""), "a pid under another parent is a new process"
    assert ov[14][3] == "" and ov[12][3] == "", "a reused image or an unseen pid is a new process"
    loop = {1: (3, "r", 0, ""), 2: (1, "x", 0, ""), 3: (2, "y", 0, ""), 5: (6, "z", 0, ""), 6: (5, "w", 0, "")}
    assert sorted(_subtree(loop, 1)) == [2, 3], "a parent loop must terminate"
    # a reused parent pid: the 74-minute process is not the 4-minute bash's child; a real chain is kept whole
    reuse = {1: (0, "claude.exe", 100.0, "claude"), 2: (1, "bash.exe", 4.0, "bash -c x"), 3: (2, "cmd.exe", 74.0, "cmd /c w"),
             4: (3, "python.exe", 74.0, "python w.py"), 5: (2, "python.exe", 3.0, "python t.py")}
    assert sorted(d[0] for d in descendants(reuse, 1, {1})) == [2, 5], "a child older than its parent is a reused pid"
    chain = {1: (0, "claude.exe", 100.0, "claude"), 2: (1, "python.exe", 50.0, "python r.py"), 3: (2, "claude.exe", 10.0, "c"),
             4: (3, "bash.exe", 0.0, "")}
    assert sorted(d[0] for d in descendants(chain, 1, {1})) == [2, 3, 4], "a real chain is kept, unknown ages included"
    me = _proc_details([os.getpid()]).get(os.getpid())
    assert me and "python" in me[1].lower() and 0 <= me[0] < 60, "own age and command line must be readable"
    rows = census()
    assert rows, "census empty"
    assert os.getpid() in rows, "the caller must be in its own census"
    root, chain = my_session_root(rows)
    desc = descendants(rows, root, set(chain)) if root else []
    t = box_totals(rows)
    out = render("UserPromptSubmit", desc, t)
    assert out.startswith("[waste-sense]") and "judge" in out and "REPORT" in out
    # Stop must be silent: a Stop injection re-prompts the model (owner order 2026-09-07 13:1x)
    import io as _io
    buf = _io.StringIO(); old = sys.stdout, sys.stdin
    try:
        sys.stdin = _io.StringIO(json.dumps({"hook_event_name": "Stop", "session_id": "selftest"})); sys.stdout = buf
        main()
    finally:
        sys.stdout, sys.stdin = old
    assert buf.getvalue() == "", "Stop event must emit nothing"
    print("waste_sense selftest: PASS (root %s, %d descendants, box %d procs, mode %s; Stop silent)" % (root, len(desc), t[0], t[4]))
    return 0


if __name__ == "__main__":
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        print("usage: waste_sense.py [--selftest]   (hook: reads the event JSON on stdin; help is inert)")
        raise SystemExit(0)
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    try:
        main()
    except Exception:
        pass
