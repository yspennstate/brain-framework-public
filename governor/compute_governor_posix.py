#!/usr/bin/env python3
"""compute_governor_posix.py - publish compute_state.json on macOS/Linux (the read side of compute_governor.ps1).

Every agent reads ~/.claude/compute/compute_state.json before sizing work (compute_sense hook, the wake ritual, the
dream's REM preflight). On Windows compute_governor.ps1 writes it; this writes the same core fields here:
ts, host, status (ok | warn | distress), mode (owner_away_open | owner_present_polite | throttle), reasons,
cpu_pct, load, mem_free_pct, swap_used_mb, disk_free_gb, proc_count, owner_idle_s, owner_away, top.

It only OBSERVES. It renices and kills nothing: the owner's 2026-09-18 law is that no agent sheds or throttles running
work on its own - it reports instead. Agents read `mode` and size their own work.

usage: compute_governor_posix.py --once            one cycle
       compute_governor_posix.py --every 60        loop (writes governor.pid; stop with ~/.claude/compute/STOP_GOVERNOR)
"""
import argparse, json, os, re, shutil, socket, subprocess, sys, time

HOME = os.path.expanduser("~")
STATE_DIR = os.path.join(HOME, ".claude", "compute")
STATE = os.path.join(STATE_DIR, "compute_state.json")
STOP = os.path.join(STATE_DIR, "STOP_GOVERNOR")
OWNER_AWAY_S = 600          # no keyboard/mouse input for 10 min = owner away
SUSTAIN = 3                 # CPU saturated for this many consecutive cycles before it counts
SWAP_WARN_MB = 6500         # swap used: hold new launches (16 GB machine)
SWAP_DISTRESS_MB = 12000    # swap used: shed load before macOS starts killing processes
# Swap TRAFFIC (vm_stat swapins + swapouts, pages/s since the last cycle). On 31 Oct 22:50 three jobs paged each other
# at ~22,000 pages/s (16 KB pages, ~350 MB/s) with swap used 2.4 GB and 35% free, so the size gates above read ok while
# a simulation ran at 3% of its speed. Lifetime average over 44 h was 3,300/s; thresholds provisional until swap_io_ps history.
SWAP_IO_WARN_PS = 5000
SWAP_IO_DISTRESS_PS = 15000
_sat_run = 0


def sh(argv, timeout=10):
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout).stdout
    except Exception:
        return ""


def cpu_pct():
    out = sh(["top", "-l", "2", "-n", "0", "-s", "1"], timeout=15)
    m = re.findall(r"CPU usage:\s*([\d.]+)% user,\s*([\d.]+)% sys", out)
    if m:
        u, s = m[-1]
        return round(float(u) + float(s), 1)
    try:
        return round(100.0 * os.getloadavg()[0] / (os.cpu_count() or 1), 1)
    except OSError:
        return None


def mem():
    free_pct, swap_mb = None, None
    m = re.search(r"free percentage:\s*(\d+)%", sh(["memory_pressure", "-Q"]))
    if m:
        free_pct = int(m.group(1))
    m = re.search(r"used = ([\d.]+)M", sh(["sysctl", "-n", "vm.swapusage"]))
    if m:
        swap_mb = float(m.group(1))
    return free_pct, swap_mb


def swap_io():
    """Cumulative swapins + swapouts from vm_stat, or None."""
    out = sh(["vm_stat"])
    i, o = re.search(r"Swapins:\s+(\d+)", out), re.search(r"Swapouts:\s+(\d+)", out)
    return int(i.group(1)) + int(o.group(1)) if i and o else None


def swap_io_rate(count, now):
    """Pages/s against the counter in the last published state (so --once works); None without a usable pair."""
    try:
        with open(STATE, encoding="utf-8") as f:
            prev = json.load(f)
        pc, pt = prev.get("swap_io_pages"), prev.get("t_epoch")
    except (OSError, ValueError, AttributeError):
        return None
    if count is None or pc is None or pt is None or now <= pt or count < pc:  # counter reset at boot
        return None
    return round((count - pc) / (now - pt), 1)


def idle_s():
    m = re.search(r'"HIDIdleTime"\s*=\s*(\d+)', sh(["ioreg", "-c", "IOHIDSystem", "-d", "4"]))
    return round(int(m.group(1)) / 1e9, 1) if m else None


def top_procs(n=8):
    rows = []
    for ln in sh(["ps", "-axo", "pid=,%cpu=,rss=,comm="]).splitlines():
        p = ln.split(None, 3)
        if len(p) == 4:
            try:
                rows.append({"pid": int(p[0]), "cpu_pct": float(p[1]), "rss_mb": int(p[2]) // 1024, "name": os.path.basename(p[3])})
            except ValueError:
                pass
    rows.sort(key=lambda r: r["cpu_pct"], reverse=True)
    return rows[:n], len(rows)


def cycle():
    global _sat_run
    t0 = time.time()
    cpu = cpu_pct()
    free_pct, swap_mb = mem()
    sio, now = swap_io(), time.time()
    sio_ps = swap_io_rate(sio, now)
    idle = idle_s()
    top, nproc = top_procs()
    try:
        disk_free = round(shutil.disk_usage(HOME).free / 1e9, 1)
    except OSError:
        disk_free = None
    reasons, status = [], "ok"
    if free_pct is not None and free_pct < 5:
        reasons.append("memory_physical_critical"); status = "distress"
    elif free_pct is not None and free_pct < 15:
        reasons.append("memory_physical_pressure"); status = "warn"
    # Swap USED, not free: macOS grows the swap total a file at a time, so free stays near zero while used climbs, and the
    # free percentage above read 35-45% with swap full (29 and 30 Oct). Thresholds from the 29 Oct launch-gate memory.
    if swap_mb is not None and swap_mb >= SWAP_DISTRESS_MB:
        reasons.append("swap_critical"); status = "distress"
    elif swap_mb is not None and swap_mb >= SWAP_WARN_MB:
        reasons.append("swap_high"); status = max(status, "warn", key=["ok", "warn", "distress"].index)
    if sio_ps is not None and sio_ps >= SWAP_IO_DISTRESS_PS:
        reasons.append("swap_thrash"); status = "distress"
    elif sio_ps is not None and sio_ps >= SWAP_IO_WARN_PS:
        reasons.append("swap_paging"); status = max(status, "warn", key=["ok", "warn", "distress"].index)
    if disk_free is not None and disk_free < 10:
        reasons.append("disk_low"); status = "distress" if disk_free < 3 else max(status, "warn", key=["ok", "warn", "distress"].index)
    _sat_run = _sat_run + 1 if (cpu is not None and cpu >= 95) else 0
    owner_away = idle is not None and idle >= OWNER_AWAY_S
    if _sat_run >= SUSTAIN:
        reasons.append("cpu_saturated" if owner_away else "cpu_high_owner_present")
        if status == "ok":
            status = "warn"
    throttle = status == "distress" or (_sat_run >= SUSTAIN and not owner_away)
    mode = "throttle" if throttle else ("owner_away_open" if owner_away else "owner_present_polite")
    state = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"), "host": socket.gethostname(), "status": status, "mode": mode,
        "reasons": reasons, "cpu_pct": cpu, "load": [round(x, 2) for x in os.getloadavg()], "cpus": os.cpu_count(),
        "mem_free_pct": free_pct, "swap_used_mb": swap_mb, "swap_io_ps": sio_ps, "swap_io_pages": sio, "t_epoch": round(now, 1), "disk_free_gb": disk_free, "proc_count": nproc,
        "owner_idle_s": idle, "owner_away": owner_away, "owner_active": not owner_away, "top": top,
        "governor": "compute_governor_posix.py (observe only)", "cycle_ms": int((time.time() - t0) * 1000),
    }
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=1)
    os.replace(tmp, STATE)
    return state


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true"); ap.add_argument("--every", type=int, default=0)
    a = ap.parse_args()
    if not a.every:
        s = cycle()
        print("%s status=%s mode=%s cpu=%s%% mem_free=%s%% reasons=%s" % (s["ts"], s["status"], s["mode"], s["cpu_pct"], s["mem_free_pct"], s["reasons"]))
        return 0
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(os.path.join(STATE_DIR, "governor.pid"), "w") as f:
        f.write(str(os.getpid()))
    while not os.path.exists(STOP):
        try:
            cycle()
        except Exception as e:
            sys.stderr.write("governor cycle failed: %s\n" % e)
        time.sleep(max(5, a.every))
    return 0


if __name__ == "__main__":
    sys.exit(main())
