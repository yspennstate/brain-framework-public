#!/usr/bin/env python3
"""compute_sentinel.py - compute health sentinel for <box-host>.

Publishes /var/local/compute_health.json once a minute (systemd timer). The home governor
(<hostname> compute_governor.ps1) pulls that file into the fleet-wide compute state.

Contract (COMPUTE_DOCTRINE.md, compute_governor apparatus 2026-07-10):
  - the protected production stack is OBSERVED, never touched: unit states are reported; nothing in
    system.slice is ever reniced, restarted, or signalled.
  - protected-unit health is a separate alert channel. A stopped unit is not evidence of
    compute pressure and never, by itself, orders research agents to shed load.
  - The only medicine is renice (research processes in user.slice, never the owner's
    user-1001.slice RDP session), rate-limited and ledgered - the healer scar.
  - stack_squeezed fires when system.slice itself waits on CPU: that is the one signal that
    research load is hurting the stack, and it makes status=distress fleet-wide.
"""
import json, os, subprocess, time
import sys

OUT = "/var/local/compute_health.json"
LEDGER = "/var/local/compute_sentinel_interventions.tsv"
UNITS = ["example-prod-1", "example-prod-2",  # populate: your protected systemd units
         "example-feed.service", "example-workers.target"]
OWNER_UID_SLICE = "user-1001.slice"   # the owner's RDP session - exempt from everything
MAX_PER_HOUR = 20
NICE_TARGET = 10
RENICE_CORE_PCT = 50.0
SAMPLE_SEC = 1.5


def classify_pressure(load1, psi_cpu, psi_mem, sys_psi):
    """Return resource-pressure status without importing protected-unit health."""
    missing = [
        name
        for name, value in (
            ("psi_cpu", psi_cpu),
            ("psi_mem", psi_mem),
            ("sys_slice_psi", sys_psi),
        )
        if value is None
    ]
    pressure_reasons = []
    if psi_cpu is not None and (
        psi_cpu >= 40 or (load1 >= 10.0 and psi_cpu >= 10)
    ):
        pressure_reasons.append("cpu_pressure")
    if psi_mem is not None and psi_mem >= 15:
        pressure_reasons.append("memory_pressure")
    if sys_psi is not None and sys_psi >= 20:
        pressure_reasons.append("stack_squeezed")

    reasons = list(pressure_reasons)
    if missing:
        reasons.append("telemetry_degraded:" + ",".join(missing))
    elevated = (
        (psi_cpu is not None and psi_cpu >= 25)
        or load1 >= 7.0
        or (psi_mem is not None and psi_mem >= 8)
    )
    if pressure_reasons:
        status = "distress"
    elif elevated:
        status = "warn"
        reasons.insert(0, "elevated_pressure")
    elif missing:
        status = "warn"
    else:
        status = "ok"

    # PSI avg60 is already sustained. Bare load warns but does not throttle a
    # low-priority research slice when neither the CPU nor system.slice is waiting.
    throttle = (
        status == "distress"
        or bool(missing)
        or (psi_cpu is not None and psi_cpu >= 35)
    )
    if throttle and status != "distress" and not missing:
        reasons.append("saturation_throttle")
    mode = "throttle" if throttle else "open"
    return status, mode, reasons


def classify_protected_health(units):
    """Report non-active protected-units without treating them as compute pressure."""
    not_active = sorted(u for u, state in units.items() if state != "active")
    alerts = []
    if not_active:
        alerts.append("protected_unit_not_active:" + ",".join(not_active))
    return ("alert" if alerts else "ok"), alerts


def psi(path):
    try:
        with open(path) as f:
            for ln in f:
                if ln.startswith("some"):
                    for tok in ln.split():
                        if tok.startswith("avg60="):
                            return float(tok[6:])
    except Exception:
        pass
    return None


def unit_state(u):
    try:
        r = subprocess.run(["systemctl", "is-active", u],
                           capture_output=True, text=True, timeout=5)
        return r.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def proc_sample():
    out = {}
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open("/proc/%s/stat" % pid) as f:
                parts = f.read().rsplit(")", 1)[1].split()
            cpu_ticks = int(parts[11]) + int(parts[12])   # utime + stime
            nice = int(parts[16])
            with open("/proc/%s/cgroup" % pid) as f:
                cg = f.read()
            out[int(pid)] = (cpu_ticks, nice, cg)
        except Exception:
            pass
    return out


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # Discovery is not execution: this script writes, so a help request must
        # exit before any work. See script_help_flag_can_execute_work.
        print(__doc__ or os.path.basename(__file__))
        return
    load1 = float(open("/proc/loadavg").read().split()[0])
    psi_cpu = psi("/proc/pressure/cpu")
    psi_mem = psi("/proc/pressure/memory")
    psi_io = psi("/proc/pressure/io")
    sys_psi = psi("/sys/fs/cgroup/system.slice/cpu.pressure")
    mem_avail_mb = 0
    for ln in open("/proc/meminfo"):
        if ln.startswith("MemAvailable"):
            mem_avail_mb = int(ln.split()[1]) // 1024
            break

    units = {u: unit_state(u) for u in UNITS}
    owner_active = False
    try:
        r = subprocess.run(["loginctl", "list-sessions", "--no-legend"],
                           capture_output=True, text=True, timeout=5)
        owner_active = any(" owner" in ln for ln in r.stdout.splitlines())
    except Exception:
        pass

    hz = os.sysconf("SC_CLK_TCK")
    s1 = proc_sample()
    time.sleep(SAMPLE_SEC)
    s2 = proc_sample()
    heavy = []
    for pid, (t2, nice, cg) in s2.items():
        if pid not in s1:
            continue
        core_pct = (t2 - s1[pid][0]) / hz / SAMPLE_SEC * 100.0
        if core_pct >= 5.0 and "/user.slice/" in cg and OWNER_UID_SLICE not in cg:
            try:
                with open("/proc/%d/comm" % pid) as f:
                    comm = f.read().strip()
            except Exception:
                comm = "?"
            heavy.append({"pid": pid, "comm": comm, "core_pct": round(core_pct, 1), "nice": nice})
    heavy.sort(key=lambda h: -h["core_pct"])

    now = time.time()
    recent = 0
    try:
        with open(LEDGER) as f:
            for ln in f:
                try:
                    if float(ln.split("\t")[0]) > now - 3600:
                        recent += 1
                except Exception:
                    pass
    except Exception:
        pass

    reniced = []
    for h in heavy:
        if recent + len(reniced) >= MAX_PER_HOUR:
            break
        if h["core_pct"] >= RENICE_CORE_PCT and h["nice"] < NICE_TARGET and h["pid"] != os.getpid():
            try:
                os.setpriority(os.PRIO_PROCESS, h["pid"], NICE_TARGET)
                reniced.append(h)
            except Exception:
                pass
    if reniced:
        try:
            with open(LEDGER, "a") as f:
                for h in reniced:
                    f.write("%.0f\trenice\t%d\t%s\t%d\t%d\t%.1f\n" %
                            (now, h["pid"], h["comm"], h["nice"], NICE_TARGET, h["core_pct"]))
        except Exception:
            pass

    # Compute pressure and protected health are intentionally orthogonal. Only measured
    # resource pressure can issue the fleet-wide pause contract. Unit state is observed
    # and published so an operator can investigate it without the sentinel touching it.
    status, mode, reasons = classify_pressure(load1, psi_cpu, psi_mem, sys_psi)
    protected_health, protected_alerts = classify_protected_health(units)

    doc = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "host": "<box-host>",
           "status": status, "mode": mode, "reasons": reasons, "load1": load1,
           "psi_cpu_some_avg60": psi_cpu, "psi_mem_some_avg60": psi_mem,
           "psi_io_some_avg60": psi_io, "sys_slice_psi_avg60": sys_psi,
           "mem_avail_mb": mem_avail_mb, "owner_rdp_active": owner_active,
           "protected_health": protected_health, "protected_alerts": protected_alerts,
           "protected_units": units, "user_slice_top": heavy[:5],
           "reniced_now": len(reniced), "interventions_last_hour": recent + len(reniced)}
    tmp = OUT + ".tmp"
    with open(tmp, "w") as f:
        json.dump(doc, f)
    os.replace(tmp, OUT)


if __name__ == "__main__":
    main()
