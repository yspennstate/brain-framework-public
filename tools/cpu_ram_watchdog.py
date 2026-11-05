"""
CPU + RAM Watchdog - mirrors gpu_watchdog.py for system CPU and memory.

Protects the machine's protected workloads (the human's interactive programs,
production services) from being starved by heavy sims / batch sweeps /
multiple concurrent agent sessions. Enforces the 50%-of-box rule across
ALL non-protected processes combined.

Uses psutil. Logs to cpu_ram_watchdog.log. Run via Scheduled Task at logon.
"""

import time
import logging
import os
import sys
import subprocess
from datetime import datetime

import psutil

# ── Configuration ──────────────────────────────────────────────────
CHECK_INTERVAL = 10              # seconds between checks

# 50%-of-box rule. 16 logical cores, ~68 GB RAM.
CPU_WARN_PCT = 50                # system-wide CPU % (excluding protected procs)
CPU_CRITICAL_SECONDS = 60        # sustained seconds above CPU_WARN_PCT before killing
RAM_WARN_PCT = 50                # system RAM % used
RAM_CRITICAL_PCT = 75            # hard kill threshold (RAM exhaustion is fast death)

# Per-process kill thresholds (any single non-protected proc above these = kill)
PROC_CPU_KILL_PCT = 400          # ~4 full cores sustained
PROC_RAM_KILL_GB = 20            # ~30% of 68 GB
PROC_SUSTAIN_SEC = 60            # must hold for this long

# Protected: never kill these. Match by exact name OR cmdline substring.
PROTECTED_NAMES = {
    'explorer.exe', 'dwm.exe', 'csrss.exe', 'svchost.exe',
    'winlogon.exe', 'services.exe', 'lsass.exe', 'smss.exe',
    'System', 'wininit.exe', 'taskhostw.exe', 'fontdrvhost.exe',
    'java.exe', 'javaw.exe',                # example: a production JVM service
    'pythonw.exe',                          # windowless background services
    'gpu_watchdog.py', 'cpu_ram_watchdog.py',
    'claude.exe', 'node.exe',               # Claude Code itself
}
PROTECTED_CMDLINE_SUBSTRINGS = [
    'protected_workload',  # populate: markers of processes that must never be killed
    'gpu_watchdog',
    'cpu_ram_watchdog',
]

LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cpu_ram_watchdog.log')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ]
)
log = logging.getLogger('cpu_ram_watchdog')


def show_toast(title, message):
    try:
        ps_cmd = (
            "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null;"
            "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null;"
            f"$template = \"<toast><visual><binding template='ToastGeneric'><text>{title}</text><text>{message}</text></binding></visual></toast>\";"
            "$xml = New-Object Windows.Data.Xml.Dom.XmlDocument;"
            "$xml.LoadXml($template);"
            "$toast = [Windows.UI.Notifications.ToastNotification]::new($xml);"
            "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('CPU/RAM Watchdog').Show($toast)"
        )
        subprocess.Popen(
            ['powershell', '-Command', ps_cmd],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
        )
    except Exception:
        pass


def is_protected(proc):
    """True if this process must never be killed."""
    try:
        name = (proc.info.get('name') or '').lower()
        if name in {n.lower() for n in PROTECTED_NAMES}:
            # python.exe is NOT in PROTECTED_NAMES on purpose — heavy sims run as python.exe.
            # But pythonw.exe IS protected (background bots).
            return True
        cmdline = ' '.join(proc.info.get('cmdline') or []).lower()
        for sub in PROTECTED_CMDLINE_SUBSTRINGS:
            if sub.lower() in cmdline:
                return True
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return True  # be safe
    return False


def list_procs():
    """Return list of (proc, cpu_pct, ram_gb, protected)."""
    out = []
    for p in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            # Skip kernel pseudo-processes — PID 0 (System Idle Process)
            # reports ~1000% CPU which is just idle time, not real usage.
            # PID 4 (System) is also unkillable.
            if p.pid in (0, 4):
                continue
            cpu = p.cpu_percent(None)            # since last call
            mem = p.memory_info().rss / (1024 ** 3)
            out.append((p, cpu, mem, is_protected(p)))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return out


def kill_proc(p, reason):
    try:
        name = p.info.get('name', '?')
        pid = p.pid
        log.warning(f"KILLING {name} (PID {pid}) — {reason}")
        p.kill()
        return f"{name}({pid})"
    except Exception as e:
        log.error(f"Failed to kill PID {p.pid}: {e}")
        return None


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # Discovery is not execution: this script writes, so a help request must
        # exit before any work. See script_help_flag_can_execute_work.
        print(__doc__ or os.path.basename(__file__))
        return
    cores = psutil.cpu_count()
    total_ram_gb = psutil.virtual_memory().total / (1024 ** 3)
    log.info(f"CPU/RAM Watchdog started — {cores} cores, {total_ram_gb:.1f} GB RAM")
    log.info(f"CPU warn={CPU_WARN_PCT}% sustained {CPU_CRITICAL_SECONDS}s; RAM warn={RAM_WARN_PCT}% kill={RAM_CRITICAL_PCT}%")
    log.info(f"Per-proc kill: CPU>{PROC_CPU_KILL_PCT}% or RAM>{PROC_RAM_KILL_GB}GB sustained {PROC_SUSTAIN_SEC}s")
    show_toast("CPU/RAM Watchdog", f"Monitoring {cores}c / {total_ram_gb:.0f}GB — 50% cap")

    # Prime psutil's cpu_percent counters
    for p in psutil.process_iter():
        try:
            p.cpu_percent(None)
        except Exception:
            pass
    psutil.cpu_percent(None)
    time.sleep(1)

    high_cpu_start = None
    proc_breach_since = {}   # pid -> first-seen-breach timestamp
    last_status = 0

    while True:
        try:
            sys_cpu = psutil.cpu_percent(None)
            vm = psutil.virtual_memory()
            sys_ram_pct = vm.percent
            now = time.time()

            procs = list_procs()

            # ── System RAM hard kill ──
            if sys_ram_pct >= RAM_CRITICAL_PCT:
                log.critical(f"SYSTEM RAM CRITICAL: {sys_ram_pct:.0f}% — killing biggest non-protected procs")
                show_toast("RAM CRITICAL", f"RAM {sys_ram_pct:.0f}%! Killing hogs!")
                hogs = sorted([x for x in procs if not x[3]], key=lambda x: -x[2])[:3]
                for p, cpu, mem, _ in hogs:
                    kill_proc(p, f"system RAM {sys_ram_pct:.0f}% — proc holding {mem:.1f}GB")
            elif sys_ram_pct >= RAM_WARN_PCT:
                log.warning(f"System RAM at {sys_ram_pct:.0f}% (warn={RAM_WARN_PCT}%)")

            # ── System CPU sustained ──
            if sys_cpu >= CPU_WARN_PCT:
                if high_cpu_start is None:
                    high_cpu_start = now
                    log.warning(f"System CPU high: {sys_cpu:.0f}% — monitoring...")
                elif (now - high_cpu_start) >= CPU_CRITICAL_SECONDS:
                    log.critical(f"System CPU at {sys_cpu:.0f}% for {int(now-high_cpu_start)}s — killing top non-protected hog")
                    show_toast("CPU CRITICAL", f"CPU {sys_cpu:.0f}% sustained! Killing hog!")
                    hogs = sorted([x for x in procs if not x[3]], key=lambda x: -x[1])[:1]
                    for p, cpu, mem, _ in hogs:
                        kill_proc(p, f"system CPU {sys_cpu:.0f}% sustained {int(now-high_cpu_start)}s")
                    high_cpu_start = None
            else:
                high_cpu_start = None

            # ── Per-process sustained breach ──
            current_breachers = set()
            for p, cpu, mem, prot in procs:
                if prot:
                    continue
                if cpu >= PROC_CPU_KILL_PCT or mem >= PROC_RAM_KILL_GB:
                    current_breachers.add(p.pid)
                    first = proc_breach_since.setdefault(p.pid, now)
                    if (now - first) >= PROC_SUSTAIN_SEC:
                        kill_proc(p, f"per-proc breach: CPU={cpu:.0f}% RAM={mem:.1f}GB for {int(now-first)}s")
                        proc_breach_since.pop(p.pid, None)
            # cleanup stale entries
            for pid in list(proc_breach_since):
                if pid not in current_breachers:
                    proc_breach_since.pop(pid, None)

            # ── Periodic status every 5 min ──
            if now - last_status >= 300:
                used_gb = vm.used / (1024 ** 3)
                log.info(f"Status: CPU {sys_cpu:.0f}% | RAM {used_gb:.1f}/{total_ram_gb:.1f}GB ({sys_ram_pct:.0f}%)")
                last_status = now

        except Exception as e:
            log.error(f"Watchdog error: {e}")

        time.sleep(CHECK_INTERVAL)


if __name__ == '__main__':
    main()
