"""
Unified System Watchdog — GPU + CPU + RAM + commit charge + disk + event log.

Prevents crashes by:
  1. Monitoring GPU temp / utilization / VRAM (cumulative window, not just sustained)
  2. Monitoring system CPU / RAM / commit charge / disk I/O
  3. Rate-of-change detection — catches runaway allocations before they hit threshold
  4. Killing non-protected hogs before thresholds cause a crash
  5. Preemptive throttling — lowers priority + restricts affinity at warning level
  6. Windows Event Log monitoring — catches WHEA/driver errors that precede crashes
  7. nvidia-smi hang → preemptive GPU process kill (not just alert)
  8. Audible beep on warnings (not just toast notifications)
  9. Excluding PID 0/4 (System Idle / System kernel)
 10. GPU queries on a separate thread — nvidia-smi hangs don't block CPU/RAM monitoring
 11. Automatic log rotation — keeps last 3 logs, 5MB each
 12. Self-health heartbeat file — external checker can detect if we're hung

Protected processes (never killed):
  - pythonw.exe (background services)
  - java.exe / javaw.exe (IBC Gateway)
  - claude.exe / node.exe (Claude Code)
  - Anything with your_service / your_daemon / tick_recorder / system_watchdog in cmdline
  - Core Windows processes

NOTE: python.exe is intentionally NOT protected — heavy sims run as python.exe
and must be killable. Background bots run as pythonw.exe.

Run: pythonw.exe system_watchdog.py
Register: schtasks /Create /TN "System_Watchdog" /TR "pythonw.exe %USERPROFILE%\\system_watchdog.py" /SC ONLOGON /RL HIGHEST /F
"""

import time
import logging
import logging.handlers
import subprocess
import sys
import os
import threading
import winsound
import ctypes
from datetime import datetime

import psutil

# ═══════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════

CHECK_INTERVAL = 5            # seconds between checks (was 10 — faster catches spikes)
STATUS_INTERVAL = 300         # seconds between status log lines

# ── GPU thresholds ──
GPU_TEMP_WARN = 78            # °C — audible beep + log
GPU_TEMP_CRITICAL = 85        # °C — kill GPU processes
GPU_UTIL_WARN = 50            # % sustained utilization
GPU_UTIL_CRITICAL_SEC = 60    # seconds above warn before killing
GPU_UTIL_WINDOW_CHECKS = 24   # look at last 24 checks (~120s at 5s interval)
GPU_UTIL_CRITICAL_FRACTION = 0.5  # kill if >=50% of window checks are above warn (12/24)
VRAM_WARN_PCT = 50            # % VRAM usage warning
VRAM_CRITICAL_PCT = 50        # % VRAM usage — kill

# ── CPU thresholds ──
CPU_WARN_PCT = 50             # system-wide CPU %
CPU_WINDOW_CHECKS = 24        # look at last 24 checks (~120s at 5s interval)
CPU_CRITICAL_FRACTION = 0.7   # kill if >=70% of window checks are above warn

# ── RAM thresholds ──
RAM_WARN_PCT = 50             # system RAM % — warn
RAM_CRITICAL_PCT = 75         # system RAM % — kill biggest hogs

# ── Commit charge (RAM + page file) ──
COMMIT_CRITICAL_PCT = 85      # % of total commit limit — kill hogs

# ── RAM rate-of-change ──
RAM_SPIKE_PCT = 15            # if RAM jumps this many % between checks → immediate kill

# ── Disk I/O ──
DISK_BUSY_PCT = 95            # disk busy % threshold
DISK_BUSY_WINDOW = 12         # checks (~60s at 5s) of sustained disk saturation

# ── Per-process thresholds ──
PROC_CPU_KILL_PCT = 400       # ~4 full cores sustained
PROC_RAM_KILL_GB = 20         # ~30% of 64 GB
PROC_SUSTAIN_SEC = 60         # must hold for this long

# ── Beep settings ──
BEEP_FREQ_WARN = 1000         # Hz
BEEP_DUR_WARN = 300           # ms
BEEP_FREQ_CRITICAL = 2000     # Hz — higher pitch for critical
BEEP_DUR_CRITICAL = 800       # ms — longer for critical
BEEP_COOLDOWN = 30            # seconds between repeated beeps

# ── Preemptive throttling ──
THROTTLE_CPU_PCT = 40         # system CPU above this → throttle top non-protected procs
THROTTLE_RAM_PCT = 60         # system RAM above this → throttle top non-protected procs

# ── Windows Event Log ──
EVENTLOG_CHECK_INTERVAL = 30  # seconds between event log checks
EVENTLOG_LOOKBACK_SEC = 60    # look at events from last N seconds

# ── Handle / GDI leak detection ──
HANDLE_WARN = 200000          # system-wide open handles — warn
HANDLE_CRITICAL = 300000      # system-wide — start killing top handle holders
PROC_HANDLE_KILL = 20000      # per-process handle count — kill if sustained
PROC_HANDLE_SUSTAIN_SEC = 30  # seconds a process must hold excessive handles

# ── Non-paged pool (kernel memory) ──
NONPAGED_POOL_CRITICAL_MB = 1024  # most systems crash around 1.5-2GB; kill early

# ── GPU TDR prevention ──
# GPU temp rate-of-change: if temp jumps this much between checks, kill preemptively
# (TDR often follows rapid thermal spike even below absolute threshold)
GPU_TEMP_SPIKE_C = 10         # °C jump in one check interval → preemptive kill

# ── Protected processes ──
# Priority tiers (highest = last to kill):
#   Tier 1 (NEVER kill): the human's interactive programs, core Windows
#   Tier 2 (NEVER kill): production services
#   Tier 3 (NEVER kill): the agent session itself
#   Everything else: killable
PROTECTED_NAMES = {
    # Core Windows
    'explorer.exe', 'dwm.exe', 'csrss.exe', 'svchost.exe',
    'winlogon.exe', 'services.exe', 'lsass.exe', 'smss.exe',
    'system', 'wininit.exe', 'taskhostw.exe', 'fontdrvhost.exe',
    # The human's interactive programs (HIGHEST priority - populate for your machine)
    'examplegame.exe',             # e.g. a game the human plays
    # Production services (second highest priority)
    'java.exe', 'javaw.exe',       # example: a production JVM service
    'pythonw.exe',                  # windowless background services
    # Claude Code
    'claude.exe', 'node.exe',
}
PROTECTED_CMDLINE_SUBSTRINGS = [
    'protected_workload',  # populate: markers of processes that must never be killed
    'gpu_watchdog', 'cpu_ram_watchdog', 'system_watchdog',
]

# Kernel pseudo-processes — always skip
SKIP_PIDS = {0, 4}

# ── Log rotation ──
LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'system_watchdog.log')
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 3

# ── Heartbeat file ──
HEARTBEAT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'system_watchdog.heartbeat')

# ═══════════════════════════════════════════════════════════════════
# LOGGING (with rotation)
# ═══════════════════════════════════════════════════════════════════

log = logging.getLogger('system_watchdog')
log.setLevel(logging.INFO)

_rotating_handler = logging.handlers.RotatingFileHandler(
    LOG_FILE, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT
)
_rotating_handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
log.addHandler(_rotating_handler)

_console_handler = logging.StreamHandler(sys.stdout)
_console_handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
log.addHandler(_console_handler)

# ═══════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════

_last_beep = 0

def beep(critical=False):
    """Audible beep in a thread so it doesn't block the main loop."""
    global _last_beep
    now = time.time()
    if now - _last_beep < BEEP_COOLDOWN:
        return
    _last_beep = now

    def _do_beep():
        try:
            if critical:
                for _ in range(3):
                    winsound.Beep(BEEP_FREQ_CRITICAL, BEEP_DUR_CRITICAL)
                    time.sleep(0.1)
            else:
                winsound.Beep(BEEP_FREQ_WARN, BEEP_DUR_WARN)
        except Exception:
            pass

    threading.Thread(target=_do_beep, daemon=True).start()


def show_toast(title, message):
    """Show a Windows toast notification (best-effort)."""
    try:
        ps_cmd = (
            "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null;"
            "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null;"
            f"$template = \"<toast><visual><binding template='ToastGeneric'><text>{title}</text><text>{message}</text></binding></visual></toast>\";"
            "$xml = New-Object Windows.Data.Xml.Dom.XmlDocument;"
            "$xml.LoadXml($template);"
            "$toast = [Windows.UI.Notifications.ToastNotification]::new($xml);"
            "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('System Watchdog').Show($toast)"
        )
        subprocess.Popen(
            ['powershell', '-Command', ps_cmd],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
        )
    except Exception:
        pass


def alert(title, message, critical=False):
    """Log + beep + toast."""
    if critical:
        log.critical(f"{title}: {message}")
    else:
        log.warning(f"{title}: {message}")
    beep(critical=critical)
    show_toast(title, message)


def write_heartbeat():
    """Write current timestamp to heartbeat file."""
    try:
        with open(HEARTBEAT_FILE, 'w') as f:
            f.write(str(time.time()))
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════
# GPU MONITORING (nvidia-smi on a thread to avoid blocking)
# ═══════════════════════════════════════════════════════════════════

_gpu_lock = threading.Lock()
_gpu_latest = None
_gpu_failures = 0

def _gpu_query_worker():
    """Background thread: polls nvidia-smi every CHECK_INTERVAL seconds."""
    global _gpu_latest, _gpu_failures
    while True:
        try:
            result = subprocess.run(
                ['nvidia-smi',
                 '--query-gpu=temperature.gpu,utilization.gpu,memory.used,memory.total,name',
                 '--format=csv,noheader,nounits'],
                capture_output=True, text=True, timeout=15,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            if result.returncode == 0:
                parts = [p.strip() for p in result.stdout.strip().split(',')]
                if len(parts) >= 5:
                    mem_total = int(parts[3])
                    mem_used = int(parts[2])
                    with _gpu_lock:
                        _gpu_latest = {
                            'temp': int(parts[0]),
                            'gpu_util': int(parts[1]),
                            'mem_used_mb': mem_used,
                            'mem_total_mb': mem_total,
                            'name': parts[4],
                            'vram_pct': (mem_used / mem_total * 100) if mem_total > 0 else 0,
                            'query_time': time.time(),
                        }
                        _gpu_failures = 0
            else:
                with _gpu_lock:
                    _gpu_failures += 1
        except Exception:
            with _gpu_lock:
                _gpu_failures += 1
        time.sleep(CHECK_INTERVAL)


def get_gpu():
    """Get latest GPU data (non-blocking). Returns (dict_or_None, failure_count)."""
    with _gpu_lock:
        if _gpu_latest and (time.time() - _gpu_latest.get('query_time', 0)) > 30:
            return None, _gpu_failures
        return _gpu_latest, _gpu_failures


def get_gpu_processes():
    """Get list of processes using the GPU."""
    try:
        result = subprocess.run(
            ['nvidia-smi', '--query-compute-apps=pid,process_name,used_memory',
             '--format=csv,noheader,nounits'],
            capture_output=True, text=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        processes = []
        for line in result.stdout.strip().split('\n'):
            if line.strip():
                parts = [p.strip() for p in line.split(',')]
                if len(parts) >= 3:
                    processes.append({
                        'pid': int(parts[0]),
                        'name': os.path.basename(parts[1]),
                        'mem_mb': int(parts[2])
                    })
        return processes
    except Exception:
        return []


def kill_gpu_hogs(reason):
    """Kill non-protected processes using the GPU."""
    procs = get_gpu_processes()
    killed = []
    protected_lower = {n.lower() for n in PROTECTED_NAMES}
    for p in procs:
        if p['name'].lower() not in protected_lower:
            try:
                log.warning(f"KILLING {p['name']} (PID {p['pid']}, {p['mem_mb']}MB VRAM) — {reason}")
                subprocess.run(
                    ['taskkill', '/PID', str(p['pid']), '/F'],
                    capture_output=True, timeout=10,
                    creationflags=subprocess.CREATE_NO_WINDOW
                )
                killed.append(f"{p['name']}({p['pid']})")
            except Exception as e:
                log.error(f"Failed to kill PID {p['pid']}: {e}")
        else:
            log.info(f"PROTECTED: {p['name']} (PID {p['pid']}) — not killing")
    return killed


# ═══════════════════════════════════════════════════════════════════
# CPU / RAM MONITORING (psutil)
# ═══════════════════════════════════════════════════════════════════

def is_protected(proc):
    """True if this process must never be killed."""
    try:
        name = (proc.info.get('name') or '').lower()
        if name in PROTECTED_NAMES:
            return True
        cmdline = ' '.join(proc.info.get('cmdline') or []).lower()
        for sub in PROTECTED_CMDLINE_SUBSTRINGS:
            if sub.lower() in cmdline:
                return True
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return True
    return False


def list_procs():
    """Return list of (proc, cpu_pct, ram_gb, protected), excluding kernel PIDs."""
    out = []
    for p in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            if p.pid in SKIP_PIDS:
                continue
            cpu = p.cpu_percent(None)
            mem = p.memory_info().rss / (1024 ** 3)
            out.append((p, cpu, mem, is_protected(p)))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return out


def kill_proc(p, reason):
    """Kill a single process."""
    try:
        name = p.info.get('name', '?')
        pid = p.pid
        log.warning(f"KILLING {name} (PID {pid}) — {reason}")
        p.kill()
        return f"{name}({pid})"
    except Exception as e:
        log.error(f"Failed to kill PID {p.pid}: {e}")
        return None


def throttle_proc(p, reason):
    """Lower priority + restrict affinity of a process (preemptive, non-lethal)."""
    try:
        name = p.info.get('name', '?')
        pid = p.pid
        current_nice = p.nice()
        if current_nice <= psutil.BELOW_NORMAL_PRIORITY_CLASS:
            p.nice(psutil.IDLE_PRIORITY_CLASS)
            # Restrict to first 4 cores (mask 0x0F)
            try:
                p.cpu_affinity(list(range(4)))
            except Exception:
                pass
            log.warning(f"THROTTLED {name} (PID {pid}) → IDLE priority + 4 cores — {reason}")
            return True
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass
    return False


# ═══════════════════════════════════════════════════════════════════
# COMMIT CHARGE MONITORING (Windows-specific)
# ═══════════════════════════════════════════════════════════════════

def get_commit_charge():
    """Get commit charge (used, limit) in GB via psutil swap_memory.
    On Windows, swap_memory().total = commit limit, .used = commit charge."""
    try:
        swap = psutil.swap_memory()
        # total = commit limit (RAM + page file size)
        # used = current commit charge
        used_gb = swap.used / (1024 ** 3)
        total_gb = swap.total / (1024 ** 3)
        pct = swap.percent
        return used_gb, total_gb, pct
    except Exception:
        return None, None, None


# ═══════════════════════════════════════════════════════════════════
# HANDLE / GDI LEAK DETECTION
# ═══════════════════════════════════════════════════════════════════

def get_system_handle_count():
    """Get total system handle count via performance counter."""
    try:
        # Use WMI via PowerShell — more reliable than ctypes for this
        result = subprocess.run(
            ['powershell', '-NoProfile', '-Command',
             '(Get-Counter "\\Process(_Total)\\Handle Count" -ErrorAction SilentlyContinue).CounterSamples[0].CookedValue'],
            capture_output=True, text=True, timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        if result.returncode == 0 and result.stdout.strip():
            return int(float(result.stdout.strip()))
    except Exception:
        pass
    return None


def get_proc_handle_counts(procs):
    """Get handle counts for non-protected processes. Returns [(proc, handle_count)]."""
    results = []
    for p, cpu, mem, prot in procs:
        if prot:
            continue
        try:
            handles = p.num_handles()
            results.append((p, handles))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return sorted(results, key=lambda x: -x[1])


# ═══════════════════════════════════════════════════════════════════
# NON-PAGED POOL MONITORING
# ═══════════════════════════════════════════════════════════════════

def get_nonpaged_pool_mb():
    """Get non-paged pool size in MB via performance counter."""
    try:
        result = subprocess.run(
            ['powershell', '-NoProfile', '-Command',
             '(Get-Counter "\\Memory\\Pool Nonpaged Bytes" -ErrorAction SilentlyContinue).CounterSamples[0].CookedValue / 1MB'],
            capture_output=True, text=True, timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except Exception:
        pass
    return None


# ═══════════════════════════════════════════════════════════════════
# DISK I/O MONITORING
# ═══════════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════════
# WINDOWS EVENT LOG MONITORING
# ═══════════════════════════════════════════════════════════════════

def check_critical_events():
    """Check Windows Event Log for WHEA, driver, and kernel errors.
    Returns list of (source, message) tuples for concerning events."""
    try:
        # Query System log for critical/error events in the last EVENTLOG_LOOKBACK_SEC
        ps_cmd = (
            f"Get-WinEvent -FilterHashtable @{{"
            f"LogName='System';"
            f"Level=1,2;"  # 1=Critical, 2=Error
            f"StartTime=(Get-Date).AddSeconds(-{EVENTLOG_LOOKBACK_SEC})"
            f"}} -MaxEvents 10 -ErrorAction SilentlyContinue | "
            f"Select-Object -Property ProviderName,Message | "
            f"ForEach-Object {{ $_.ProviderName + '|||' + ($_.Message -replace '\\r?\\n',' ').Substring(0, [Math]::Min(200, $_.Message.Length)) }}"
        )
        result = subprocess.run(
            ['powershell', '-NoProfile', '-Command', ps_cmd],
            capture_output=True, text=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        events = []
        # Filter for crash-precursor sources
        danger_sources = {'whea-logger', 'kernel-power', 'nvlddmkm', 'display',
                         'volmgr', 'disk', 'ntfs', 'bugcheck', 'save dump'}
        for line in result.stdout.strip().split('\n'):
            if '|||' in line:
                source, msg = line.split('|||', 1)
                if any(d in source.lower() for d in danger_sources):
                    events.append((source.strip(), msg.strip()))
        return events
    except Exception:
        return []


# ═══════════════════════════════════════════════════════════════════
# MAIN LOOP
# ═══════════════════════════════════════════════════════════════════

def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # Discovery is not execution: this script writes, so a help request must
        # exit before any work. See script_help_flag_can_execute_work.
        print(__doc__ or os.path.basename(__file__))
        return
    cores = psutil.cpu_count()
    total_ram_gb = psutil.virtual_memory().total / (1024 ** 3)

    # Start GPU query thread
    gpu_thread = threading.Thread(target=_gpu_query_worker, daemon=True)
    gpu_thread.start()

    # Wait briefly for first GPU reading
    time.sleep(2)
    gpu, _ = get_gpu()
    gpu_available = gpu is not None
    gpu_name = gpu['name'] if gpu else 'N/A'
    gpu_vram = gpu['mem_total_mb'] if gpu else 0

    # Commit charge info
    commit_used, commit_total, commit_pct = get_commit_charge()
    commit_str = f"  Commit limit: {commit_total:.1f} GB" if commit_total else "  Commit: unavailable"

    log.info("=" * 60)
    log.info(f"System Watchdog v2 started")
    log.info(f"  CPU: {cores} cores | RAM: {total_ram_gb:.1f} GB")
    log.info(f"  GPU: {gpu_name} ({gpu_vram}MB VRAM)" if gpu_available else "  GPU: not detected")
    log.info(commit_str)
    log.info(f"  Check interval: {CHECK_INTERVAL}s")
    log.info(f"  GPU thresholds: temp warn={GPU_TEMP_WARN}C kill={GPU_TEMP_CRITICAL}C | util warn={GPU_UTIL_WARN}% window={GPU_UTIL_WINDOW_CHECKS} frac={GPU_UTIL_CRITICAL_FRACTION} | VRAM kill={VRAM_CRITICAL_PCT}%")
    log.info(f"  CPU thresholds: warn={CPU_WARN_PCT}% | kill when {CPU_CRITICAL_FRACTION*100:.0f}% of last {CPU_WINDOW_CHECKS} checks high")
    log.info(f"  RAM thresholds: warn={RAM_WARN_PCT}% kill={RAM_CRITICAL_PCT}% spike={RAM_SPIKE_PCT}%/check")
    log.info(f"  Commit charge: kill at {COMMIT_CRITICAL_PCT}%")
    log.info(f"  Disk: kill at {DISK_BUSY_PCT}% busy for {DISK_BUSY_WINDOW} checks")
    log.info(f"  Throttle at: CPU>{THROTTLE_CPU_PCT}% or RAM>{THROTTLE_RAM_PCT}%")
    log.info(f"  Event log: checking every {EVENTLOG_CHECK_INTERVAL}s for WHEA/driver/kernel errors")
    log.info(f"  Per-proc kill: CPU>{PROC_CPU_KILL_PCT}% or RAM>{PROC_RAM_KILL_GB}GB sustained {PROC_SUSTAIN_SEC}s")
    log.info(f"  Audible beeps: ON (warn={BEEP_FREQ_WARN}Hz, critical={BEEP_FREQ_CRITICAL}Hz)")
    log.info("=" * 60)
    show_toast("System Watchdog v2", f"Monitoring {cores}c / {total_ram_gb:.0f}GB / {gpu_name}")

    # Prime psutil cpu_percent counters
    for p in psutil.process_iter():
        try:
            p.cpu_percent(None)
        except Exception:
            pass
    psutil.cpu_percent(None)
    time.sleep(1)

    # State
    high_gpu_util_start = None
    gpu_util_history = []
    cpu_history = []
    proc_breach_since = {}
    last_status = 0
    last_gpu_warn_time = 0
    last_gpu_hung_alert = 0
    last_eventlog_check = 0
    prev_ram_pct = None           # for rate-of-change detection
    disk_busy_history = []        # sliding window of disk busy readings
    prev_disk_counters = None     # for disk busy % calculation
    prev_disk_time = None
    throttled_pids = set()        # track what we've already throttled this cycle
    handle_breach_since = {}      # pid -> first-seen-breach timestamp for handle leaks
    prev_gpu_temp = None          # for GPU temp rate-of-change
    last_kernel_check = 0         # throttle expensive perf counter queries
    KERNEL_CHECK_INTERVAL = 30    # seconds between handle/pool checks (PowerShell is slow)
    GPU_WARN_COOLDOWN = 60

    while True:
        try:
            now = time.time()
            write_heartbeat()

            # ════════════════════════════════════════════════════════
            # GPU CHECKS (non-blocking — data comes from thread)
            # ════════════════════════════════════════════════════════
            gpu, gpu_fails = get_gpu()

            if gpu_fails >= 4 and (now - last_gpu_hung_alert) > 120:
                # nvidia-smi hung — GPU driver is likely crashed/crashing
                # Don't just alert — preemptively kill GPU processes
                alert("GPU HUNG", f"nvidia-smi unresponsive ({gpu_fails} failures) — preemptive GPU kill!", critical=True)
                killed = kill_gpu_hogs("nvidia-smi hung — preemptive kill to prevent crash")
                if killed:
                    log.warning(f"Preemptive kill: {', '.join(killed)}")
                last_gpu_hung_alert = now
            elif gpu is not None:
                temp = gpu['temp']
                gpu_util = gpu['gpu_util']
                vram_pct = gpu['vram_pct']

                # Temperature — absolute threshold + rate-of-change (TDR prevention)
                if temp >= GPU_TEMP_CRITICAL:
                    alert("GPU TEMP CRITICAL", f"{temp}C >= {GPU_TEMP_CRITICAL}C — killing GPU processes!", critical=True)
                    killed = kill_gpu_hogs(f"temperature critical {temp}C")
                    if killed:
                        log.warning(f"Killed: {', '.join(killed)}")
                elif prev_gpu_temp is not None and (temp - prev_gpu_temp) >= GPU_TEMP_SPIKE_C:
                    alert("GPU TEMP SPIKE", f"Jumped {temp - prev_gpu_temp}C in {CHECK_INTERVAL}s ({prev_gpu_temp}C → {temp}C) — killing to prevent TDR!", critical=True)
                    killed = kill_gpu_hogs(f"temp spike {prev_gpu_temp}C → {temp}C in {CHECK_INTERVAL}s")
                    if killed:
                        log.warning(f"Killed: {', '.join(killed)}")
                elif temp >= GPU_TEMP_WARN and (now - last_gpu_warn_time) > GPU_WARN_COOLDOWN:
                    alert("GPU TEMP WARNING", f"{temp}C (threshold: {GPU_TEMP_WARN}C)")
                    last_gpu_warn_time = now
                prev_gpu_temp = temp

                # Utilization — dual detection: sustained burst OR cumulative window
                gpu_is_high = gpu_util >= GPU_UTIL_WARN
                gpu_util_history.append(gpu_is_high)
                if len(gpu_util_history) > GPU_UTIL_WINDOW_CHECKS:
                    gpu_util_history.pop(0)

                if gpu_is_high:
                    # Sustained burst detection
                    if high_gpu_util_start is None:
                        high_gpu_util_start = now
                        log.warning(f"GPU utilization high: {gpu_util}% — monitoring...")
                        beep()
                    elif (now - high_gpu_util_start) >= GPU_UTIL_CRITICAL_SEC:
                        alert("GPU UTIL CRITICAL", f"{gpu_util}% for {int(now - high_gpu_util_start)}s — killing!", critical=True)
                        killed = kill_gpu_hogs(f"sustained {gpu_util}% for {int(now - high_gpu_util_start)}s")
                        if killed:
                            log.warning(f"Killed: {', '.join(killed)}")
                        high_gpu_util_start = None
                        gpu_util_history.clear()

                    # Cumulative window detection (catches rapid on/off spiking)
                    if gpu_util_history:
                        gpu_high_count = sum(gpu_util_history)
                        gpu_high_frac = gpu_high_count / len(gpu_util_history)
                        if len(gpu_util_history) >= GPU_UTIL_WINDOW_CHECKS and gpu_high_frac >= GPU_UTIL_CRITICAL_FRACTION:
                            alert("GPU UTIL CRITICAL (cumulative)",
                                  f"{gpu_util}% now, {gpu_high_count}/{len(gpu_util_history)} checks above {GPU_UTIL_WARN}% — killing!",
                                  critical=True)
                            killed = kill_gpu_hogs(f"cumulative GPU: {gpu_high_count}/{len(gpu_util_history)} checks above {GPU_UTIL_WARN}%")
                            if killed:
                                log.warning(f"Killed: {', '.join(killed)}")
                            high_gpu_util_start = None
                            gpu_util_history.clear()
                else:
                    if high_gpu_util_start is not None:
                        log.info(f"GPU utilization normalized: {gpu_util}%")
                    high_gpu_util_start = None

                # VRAM
                if vram_pct >= VRAM_CRITICAL_PCT:
                    alert("VRAM CRITICAL", f"{gpu['mem_used_mb']}/{gpu['mem_total_mb']}MB ({vram_pct:.0f}%) — killing!", critical=True)
                    killed = kill_gpu_hogs(f"VRAM critical {vram_pct:.0f}%")
                    if killed:
                        log.warning(f"Killed: {', '.join(killed)}")
                elif vram_pct >= VRAM_WARN_PCT and (now - last_gpu_warn_time) > GPU_WARN_COOLDOWN:
                    alert("VRAM WARNING", f"{gpu['mem_used_mb']}/{gpu['mem_total_mb']}MB ({vram_pct:.0f}%)")
                    last_gpu_warn_time = now

            # ════════════════════════════════════════════════════════
            # CPU / RAM CHECKS
            # ════════════════════════════════════════════════════════
            sys_cpu = psutil.cpu_percent(None)
            vm = psutil.virtual_memory()
            sys_ram_pct = vm.percent

            procs = list_procs()

            # ── RAM rate-of-change detection (NEW) ──
            if prev_ram_pct is not None:
                ram_delta = sys_ram_pct - prev_ram_pct
                if ram_delta >= RAM_SPIKE_PCT:
                    alert("RAM SPIKE", f"RAM jumped {ram_delta:.1f}% in {CHECK_INTERVAL}s ({prev_ram_pct:.0f}% → {sys_ram_pct:.0f}%) — killing biggest non-protected!", critical=True)
                    hogs = sorted([x for x in procs if not x[3]], key=lambda x: -x[2])[:2]
                    for p, cpu, mem, _ in hogs:
                        kill_proc(p, f"RAM spike {ram_delta:.1f}% in {CHECK_INTERVAL}s — proc holding {mem:.1f}GB")
            prev_ram_pct = sys_ram_pct

            # ── System RAM ──
            if sys_ram_pct >= RAM_CRITICAL_PCT:
                alert("RAM CRITICAL", f"{sys_ram_pct:.0f}% — killing biggest non-protected procs!", critical=True)
                hogs = sorted([x for x in procs if not x[3]], key=lambda x: -x[2])[:3]
                for p, cpu, mem, _ in hogs:
                    kill_proc(p, f"system RAM {sys_ram_pct:.0f}% — proc holding {mem:.1f}GB")
            elif sys_ram_pct >= RAM_WARN_PCT:
                log.warning(f"System RAM at {sys_ram_pct:.0f}% (warn={RAM_WARN_PCT}%)")

            # ── Commit charge (NEW) ──
            commit_used, commit_total, commit_pct = get_commit_charge()
            if commit_pct is not None and commit_pct >= COMMIT_CRITICAL_PCT:
                alert("COMMIT CHARGE CRITICAL",
                      f"{commit_used:.1f}/{commit_total:.1f}GB ({commit_pct:.0f}%) — killing biggest procs!",
                      critical=True)
                hogs = sorted([x for x in procs if not x[3]], key=lambda x: -x[2])[:3]
                for p, cpu, mem, _ in hogs:
                    kill_proc(p, f"commit charge {commit_pct:.0f}% — proc holding {mem:.1f}GB")

            # ── System CPU — cumulative window tracking ──
            is_high = sys_cpu >= CPU_WARN_PCT
            cpu_history.append(is_high)
            if len(cpu_history) > CPU_WINDOW_CHECKS:
                cpu_history.pop(0)

            if is_high:
                high_count = sum(cpu_history)
                high_frac = high_count / len(cpu_history)

                if len(cpu_history) >= CPU_WINDOW_CHECKS and high_frac >= CPU_CRITICAL_FRACTION:
                    alert("CPU CRITICAL",
                          f"{sys_cpu:.0f}% now, {high_count}/{len(cpu_history)} checks above {CPU_WARN_PCT}% — killing top hog!",
                          critical=True)
                    hogs = sorted([x for x in procs if not x[3]], key=lambda x: -x[1])[:1]
                    for p, cpu, mem, _ in hogs:
                        kill_proc(p, f"system CPU sustained: {high_count}/{len(cpu_history)} checks above {CPU_WARN_PCT}%")
                    cpu_history.clear()
                elif high_count == 1:
                    log.warning(f"System CPU high: {sys_cpu:.0f}% — monitoring ({high_count}/{CPU_WINDOW_CHECKS})...")
                    beep()

            # ── Preemptive throttling (NEW) ──
            # If CPU or RAM is elevated but not critical, throttle hogs before they cause a crash
            should_throttle = sys_cpu >= THROTTLE_CPU_PCT or sys_ram_pct >= THROTTLE_RAM_PCT
            if should_throttle:
                # Sort non-protected by CPU usage, throttle top 3
                hogs = sorted([x for x in procs if not x[3] and x[1] > 20], key=lambda x: -x[1])[:3]
                for p, cpu, mem, _ in hogs:
                    if p.pid not in throttled_pids:
                        if throttle_proc(p, f"preemptive: sys CPU={sys_cpu:.0f}% RAM={sys_ram_pct:.0f}%"):
                            throttled_pids.add(p.pid)
            else:
                # Clear throttle tracking when system is calm
                throttled_pids.clear()

            # ── Per-process sustained breach ──
            current_breachers = set()
            for p, cpu, mem, prot in procs:
                if prot:
                    continue
                if cpu >= PROC_CPU_KILL_PCT or mem >= PROC_RAM_KILL_GB:
                    current_breachers.add(p.pid)
                    first = proc_breach_since.setdefault(p.pid, now)
                    if (now - first) >= PROC_SUSTAIN_SEC:
                        alert("PROC KILL",
                              f"{p.info.get('name','?')} PID {p.pid} — CPU={cpu:.0f}% RAM={mem:.1f}GB for {int(now - first)}s",
                              critical=True)
                        kill_proc(p, f"per-proc breach: CPU={cpu:.0f}% RAM={mem:.1f}GB for {int(now - first)}s")
                        proc_breach_since.pop(p.pid, None)
            for pid in list(proc_breach_since):
                if pid not in current_breachers:
                    proc_breach_since.pop(pid, None)

            # ════════════════════════════════════════════════════════
            # DISK I/O MONITORING (NEW)
            # ════════════════════════════════════════════════════════
            try:
                disk_counters = psutil.disk_io_counters(perdisk=False)
                if prev_disk_counters is not None and prev_disk_time is not None:
                    dt = now - prev_disk_time
                    if dt > 0:
                        # Approximate busy % from read+write time delta
                        read_time_delta = disk_counters.read_time - prev_disk_counters.read_time
                        write_time_delta = disk_counters.write_time - prev_disk_counters.write_time
                        # read_time and write_time are in ms, dt is in seconds
                        busy_pct = min(100, (read_time_delta + write_time_delta) / (dt * 1000) * 100)
                        disk_busy_history.append(busy_pct >= DISK_BUSY_PCT)
                        if len(disk_busy_history) > DISK_BUSY_WINDOW:
                            disk_busy_history.pop(0)

                        if len(disk_busy_history) >= DISK_BUSY_WINDOW and all(disk_busy_history):
                            alert("DISK SATURATED",
                                  f"Disk >{DISK_BUSY_PCT}% busy for {DISK_BUSY_WINDOW * CHECK_INTERVAL}s — killing biggest I/O hog!",
                                  critical=True)
                            # Kill biggest non-protected RAM consumer (often the swap thrashing culprit)
                            hogs = sorted([x for x in procs if not x[3]], key=lambda x: -x[2])[:1]
                            for p, cpu, mem, _ in hogs:
                                kill_proc(p, f"disk saturated — proc holding {mem:.1f}GB (likely swap thrashing)")
                            disk_busy_history.clear()
                prev_disk_counters = disk_counters
                prev_disk_time = now
            except Exception:
                pass

            # ════════════════════════════════════════════════════════
            # HANDLE LEAKS + NON-PAGED POOL + CPU TEMP (throttled)
            # ════════════════════════════════════════════════════════
            if now - last_kernel_check >= KERNEL_CHECK_INTERVAL:
                last_kernel_check = now

                # Handle leaks
                try:
                    sys_handles = get_system_handle_count()
                    if sys_handles is not None:
                        if sys_handles >= HANDLE_CRITICAL:
                            alert("HANDLE CRITICAL", f"{sys_handles} system handles — killing top handle holders!", critical=True)
                            handle_hogs = get_proc_handle_counts(procs)[:3]
                            for p, h in handle_hogs:
                                kill_proc(p, f"system handles {sys_handles} — proc has {h} handles")
                        elif sys_handles >= HANDLE_WARN:
                            log.warning(f"System handles elevated: {sys_handles} (warn={HANDLE_WARN})")

                    # Per-process handle leak detection
                    handle_hogs = get_proc_handle_counts(procs)
                    current_handle_breachers = set()
                    for p, h in handle_hogs:
                        if h >= PROC_HANDLE_KILL:
                            current_handle_breachers.add(p.pid)
                            first = handle_breach_since.setdefault(p.pid, now)
                            if (now - first) >= PROC_HANDLE_SUSTAIN_SEC:
                                alert("HANDLE LEAK KILL",
                                      f"{p.info.get('name','?')} PID {p.pid} — {h} handles for {int(now - first)}s",
                                      critical=True)
                                kill_proc(p, f"handle leak: {h} handles for {int(now - first)}s")
                                handle_breach_since.pop(p.pid, None)
                    for pid in list(handle_breach_since):
                        if pid not in current_handle_breachers:
                            handle_breach_since.pop(pid, None)
                except Exception as e:
                    log.error(f"Handle check error: {e}")

                # Non-paged pool
                try:
                    np_pool_mb = get_nonpaged_pool_mb()
                    if np_pool_mb is not None and np_pool_mb >= NONPAGED_POOL_CRITICAL_MB:
                        alert("NON-PAGED POOL CRITICAL",
                              f"{np_pool_mb:.0f}MB — approaching kernel crash! Killing biggest procs!",
                              critical=True)
                        hogs = sorted([x for x in procs if not x[3]], key=lambda x: -x[2])[:3]
                        for p, cpu, mem, _ in hogs:
                            kill_proc(p, f"non-paged pool {np_pool_mb:.0f}MB — proc holding {mem:.1f}GB")
                except Exception as e:
                    log.error(f"Non-paged pool check error: {e}")

                # CPU temperature (via WMI — not all systems support this)
                try:
                    result = subprocess.run(
                        ['powershell', '-NoProfile', '-Command',
                         '(Get-CimInstance MSAcpi_ThermalZoneTemperature -Namespace root/wmi -ErrorAction SilentlyContinue | '
                         'Select-Object -First 1).CurrentTemperature / 10 - 273.15'],
                        capture_output=True, text=True, timeout=5,
                        creationflags=subprocess.CREATE_NO_WINDOW
                    )
                    if result.returncode == 0 and result.stdout.strip():
                        cpu_temp = float(result.stdout.strip())
                        if cpu_temp > 0 and cpu_temp < 150:  # sanity check
                            if cpu_temp >= 95:
                                alert("CPU TEMP CRITICAL", f"{cpu_temp:.0f}C — killing top CPU hogs!", critical=True)
                                hogs = sorted([x for x in procs if not x[3]], key=lambda x: -x[1])[:2]
                                for p, cpu, mem, _ in hogs:
                                    kill_proc(p, f"CPU temp {cpu_temp:.0f}C")
                            elif cpu_temp >= 85:
                                log.warning(f"CPU temperature elevated: {cpu_temp:.0f}C")
                except Exception:
                    pass  # many systems don't expose WMI thermal zones — that's fine

            # ════════════════════════════════════════════════════════
            # WINDOWS EVENT LOG CHECK
            # ════════════════════════════════════════════════════════
            if now - last_eventlog_check >= EVENTLOG_CHECK_INTERVAL:
                events = check_critical_events()
                for source, msg in events:
                    # WHEA = hardware error, nvlddmkm = NVIDIA driver crash
                    is_gpu_driver = 'nvlddmkm' in source.lower()
                    alert(f"EVENT LOG: {source}",
                          f"{msg[:150]}",
                          critical=True)
                    if is_gpu_driver:
                        log.critical("NVIDIA driver error detected — preemptive GPU kill!")
                        killed = kill_gpu_hogs("NVIDIA driver error in event log")
                        if killed:
                            log.warning(f"Preemptive kill: {', '.join(killed)}")
                last_eventlog_check = now

            # ════════════════════════════════════════════════════════
            # PERIODIC STATUS
            # ════════════════════════════════════════════════════════
            if now - last_status >= STATUS_INTERVAL:
                used_gb = vm.used / (1024 ** 3)
                gpu_str = ""
                if gpu is not None:
                    gpu_str = f" | GPU {gpu['temp']}C {gpu['gpu_util']}% VRAM {gpu['mem_used_mb']}/{gpu['mem_total_mb']}MB"
                cpu_high_count = sum(cpu_history) if cpu_history else 0
                gpu_high_count = sum(gpu_util_history) if gpu_util_history else 0
                commit_str = f" | Commit {commit_pct:.0f}%" if commit_pct is not None else ""
                disk_busy_count = sum(disk_busy_history) if disk_busy_history else 0
                # Non-blocking extras for status (use cached values if available)
                np_pool_str = ""
                try:
                    np_mb = get_nonpaged_pool_mb()
                    if np_mb is not None:
                        np_pool_str = f" | NPPool {np_mb:.0f}MB"
                except Exception:
                    pass
                log.info(
                    f"Status: CPU {sys_cpu:.0f}% (win {cpu_high_count}/{len(cpu_history)}) | "
                    f"RAM {used_gb:.1f}/{total_ram_gb:.1f}GB ({sys_ram_pct:.0f}%){commit_str}"
                    f"{gpu_str} gpu_win={gpu_high_count}/{len(gpu_util_history)} | "
                    f"disk_busy={disk_busy_count}/{len(disk_busy_history)}{np_pool_str}"
                )
                last_status = now

        except Exception as e:
            log.error(f"Watchdog error: {e}")

        time.sleep(CHECK_INTERVAL)


if __name__ == '__main__':
    main()
