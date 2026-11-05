"""
GPU Watchdog - Monitors GPU temperature, utilization, and memory.
Warns and optionally kills processes that risk causing a TDR crash.

Uses nvidia-smi (subprocess) for reliability — NVML library is flaky
on some driver versions.

Runs as a background service, logs to gpu_watchdog.log.
"""

import time
import logging
import subprocess
import sys
import os
from datetime import datetime

# ── Configuration ──────────────────────────────────────────────────
CHECK_INTERVAL = 10          # seconds between checks
TEMP_WARN = 80               # °C - log warning
TEMP_CRITICAL = 90           # °C - kill GPU-heavy processes
GPU_UTIL_WARN = 80           # % sustained utilization warning (raised from 50 for RMT sweep, restore after)
GPU_UTIL_CRITICAL_SECONDS = 120  # seconds of >80% before killing (raised from 60)
VRAM_WARN_PCT = 50           # % VRAM usage warning
VRAM_CRITICAL_PCT = 50       # % VRAM usage - kill offending processes

# Processes to NEVER kill (protected background services)
PROTECTED_PROCESSES = {
    'python.exe', 'pythonw.exe',  # background services
    'java.exe', 'javaw.exe',      # IBC Gateway
    'explorer.exe', 'dwm.exe', 'csrss.exe', 'svchost.exe',
    'winlogon.exe', 'services.exe', 'lsass.exe', 'smss.exe',
    'System', 'wininit.exe', 'taskhostw.exe',
}

LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'gpu_watchdog.log')

# ── Logging setup ─────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ]
)
log = logging.getLogger('gpu_watchdog')


def show_toast(title, message):
    """Show a Windows toast notification."""
    try:
        ps_cmd = f'''
        [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
        [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
        $template = "<toast><visual><binding template='ToastGeneric'><text>{title}</text><text>{message}</text></binding></visual></toast>"
        $xml = New-Object Windows.Data.Xml.Dom.XmlDocument
        $xml.LoadXml($template)
        $toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
        [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("GPU Watchdog").Show($toast)
        '''
        subprocess.Popen(
            ['powershell', '-Command', ps_cmd],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
    except Exception:
        pass


def query_gpu():
    """Query GPU stats via nvidia-smi. Returns dict or None on failure."""
    try:
        result = subprocess.run(
            ['nvidia-smi',
             '--query-gpu=temperature.gpu,utilization.gpu,memory.used,memory.total,name',
             '--format=csv,noheader,nounits'],
            capture_output=True, text=True, timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        if result.returncode != 0:
            return None
        parts = [p.strip() for p in result.stdout.strip().split(',')]
        if len(parts) >= 5:
            return {
                'temp': int(parts[0]),
                'gpu_util': int(parts[1]),
                'mem_used_mb': int(parts[2]),
                'mem_total_mb': int(parts[3]),
                'name': parts[4],
                'vram_pct': (int(parts[2]) / int(parts[3])) * 100 if int(parts[3]) > 0 else 0,
            }
    except Exception as e:
        log.error(f"nvidia-smi query failed: {e}")
    return None


def get_gpu_processes():
    """Get list of processes using the NVIDIA GPU via nvidia-smi."""
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
    for p in procs:
        if p['name'].lower() not in {n.lower() for n in PROTECTED_PROCESSES}:
            try:
                log.warning(f"KILLING {p['name']} (PID {p['pid']}, {p['mem_mb']}MB VRAM) - {reason}")
                subprocess.run(
                    ['taskkill', '/PID', str(p['pid']), '/F'],
                    capture_output=True, timeout=10,
                    creationflags=subprocess.CREATE_NO_WINDOW
                )
                killed.append(p['name'])
            except Exception as e:
                log.error(f"Failed to kill PID {p['pid']}: {e}")
        else:
            log.info(f"PROTECTED: {p['name']} (PID {p['pid']}) - not killing")
    return killed


def main():
    # Initial GPU query to confirm nvidia-smi works
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # Discovery is not execution: this script writes, so a help request must
        # exit before any work. See script_help_flag_can_execute_work.
        print(__doc__ or os.path.basename(__file__))
        return
    gpu = query_gpu()
    while gpu is None:
        log.error("Cannot query GPU via nvidia-smi - retrying in 30s...")
        time.sleep(30)
        gpu = query_gpu()

    log.info(f"GPU Watchdog started - monitoring {gpu['name']} ({gpu['mem_total_mb']}MB VRAM)")
    log.info(f"Temp warn={TEMP_WARN}C, critical={TEMP_CRITICAL}C")
    log.info(f"Util warn={GPU_UTIL_WARN}%, critical after {GPU_UTIL_CRITICAL_SECONDS}s")
    log.info(f"VRAM warn={VRAM_WARN_PCT}%, critical={VRAM_CRITICAL_PCT}%")
    show_toast("GPU Watchdog", f"Monitoring {gpu['name']} - will warn at {TEMP_WARN}C")

    high_util_start = None
    last_warn_time = 0
    WARN_COOLDOWN = 300  # seconds between repeated warnings
    consecutive_failures = 0
    first_check = True

    while True:
        try:
            gpu = query_gpu()
            if gpu is None:
                consecutive_failures += 1
                if consecutive_failures >= 6:  # 1 minute of failures
                    log.critical("nvidia-smi unresponsive for 60s - GPU may be hung!")
                    show_toast("GPU CRITICAL", "nvidia-smi unresponsive! GPU may be hung!")
                time.sleep(CHECK_INTERVAL)
                continue

            consecutive_failures = 0
            if first_check:
                log.info(f"First check OK: {gpu['temp']}C | GPU {gpu['gpu_util']}% | VRAM {gpu['mem_used_mb']}/{gpu['mem_total_mb']}MB")
                first_check = False
            temp = gpu['temp']
            gpu_util = gpu['gpu_util']
            mem_used_mb = gpu['mem_used_mb']
            mem_total_mb = gpu['mem_total_mb']
            vram_pct = gpu['vram_pct']
            now = time.time()

            # ── Temperature checks ──
            if temp >= TEMP_CRITICAL:
                log.critical(f"GPU TEMP CRITICAL: {temp}C >= {TEMP_CRITICAL}C! Killing GPU processes!")
                show_toast("GPU CRITICAL", f"Temperature {temp}C! Killing GPU-heavy processes!")
                killed = kill_gpu_hogs(f"temperature critical {temp}C")
                if killed:
                    log.warning(f"Killed: {', '.join(killed)}")
                else:
                    log.warning("No killable GPU processes found")
            elif temp >= TEMP_WARN and (now - last_warn_time) > WARN_COOLDOWN:
                log.warning(f"GPU temp warning: {temp}C (threshold: {TEMP_WARN}C)")
                show_toast("GPU Warning", f"Temperature {temp}C approaching critical!")
                last_warn_time = now

            # ── Utilization checks ──
            if gpu_util >= GPU_UTIL_WARN:
                if high_util_start is None:
                    high_util_start = now
                    log.warning(f"GPU utilization high: {gpu_util}% - monitoring...")
                elif (now - high_util_start) >= GPU_UTIL_CRITICAL_SECONDS:
                    log.critical(f"GPU at {gpu_util}% for {int(now - high_util_start)}s! Killing GPU hogs!")
                    show_toast("GPU CRITICAL", f"GPU at {gpu_util}% for {GPU_UTIL_CRITICAL_SECONDS}s! Killing processes!")
                    killed = kill_gpu_hogs(f"sustained {gpu_util}% utilization for {int(now - high_util_start)}s")
                    if killed:
                        log.warning(f"Killed: {', '.join(killed)}")
                    high_util_start = None
            else:
                if high_util_start is not None:
                    log.info(f"GPU utilization normalized: {gpu_util}%")
                high_util_start = None

            # ── VRAM checks ──
            if vram_pct >= VRAM_CRITICAL_PCT:
                log.critical(f"VRAM CRITICAL: {mem_used_mb}/{mem_total_mb}MB ({vram_pct:.0f}%)! Killing GPU hogs!")
                show_toast("GPU VRAM CRITICAL", f"VRAM at {vram_pct:.0f}%! Killing processes!")
                killed = kill_gpu_hogs(f"VRAM critical {vram_pct:.0f}%")
                if killed:
                    log.warning(f"Killed: {', '.join(killed)}")
            elif vram_pct >= VRAM_WARN_PCT and (now - last_warn_time) > WARN_COOLDOWN:
                log.warning(f"VRAM warning: {mem_used_mb}/{mem_total_mb}MB ({vram_pct:.0f}%)")
                show_toast("GPU VRAM Warning", f"VRAM at {vram_pct:.0f}%!")
                last_warn_time = now

            # ── Periodic status (every 5 minutes) ──
            if int(now) % 300 < CHECK_INTERVAL:
                log.info(f"Status: {temp}C | GPU {gpu_util}% | VRAM {mem_used_mb}/{mem_total_mb}MB ({vram_pct:.0f}%)")

        except Exception as e:
            log.error(f"Watchdog error: {e}")

        time.sleep(CHECK_INTERVAL)


if __name__ == '__main__':
    main()
