#!/usr/bin/env python3
r"""
CrashGuard GPU keepalive -- prevents NVIDIA dGPU idle power-down (RTD3 / GC6).

ROOT-CAUSE DIAGNOSIS (2026-06-01)
---------------------------------
Recurring bugcheck 0x116 VIDEO_TDR_FAILURE with parameter-3
0xC000009A (STATUS_INSUFFICIENT_RESOURCES) on this machine fires while the GPU
is IDLE (0% util, avg power ~1.00 W -- the GC6/RTD3 deep-idle signature), NOT
under thermal/compute load. Two standard stopgaps were already in place and did
NOT help:
    * TdrDelay / TdrDdiDelay = 300 s (extreme)  -> a 5-minute timeout cannot
      help when the GPU is not "busy" but has fallen off the PCIe bus.
    * PCIe ASPM Link State Power Management = Off (High Performance plan).
The surviving explanation is DEVICE LOSS during the idle D3cold -> D0 restore on
NVIDIA driver 595.97 (RTX PRO 2000 Blackwell laptop GPU), corroborated by 34
corrected PCIe AER errors / 72 h and a link that trains to x8 of a max x16.

MITIGATION
----------
Hold one live CUDA context. While a context is resident the dGPU stays in D0 and
the driver never performs the failing power-down / restore transition. Clocks are
NOT pinned, so idle power stays modest (~D0 idle).

Two engines, chosen at startup for resilience:
  * PRIMARY  "torch"      -- a tiny resident tensor + periodic elementwise op
                             (cuBLAS/cuDNN never imported -> small footprint).
  * FALLBACK "cuda-driver"-- cuDevicePrimaryCtxRetain via nvcuda.dll, NO torch.
                             Used if torch/python is broken. nvcuda.dll ships with
                             the GPU driver, so this has effectively no deps.

This is a NON-admin, process-local mitigation. It does not touch the production
stack, fires no external notifications, and never opens a visible window
(launched via pythonw -WindowStyle Hidden). Fully reversible: stop the process.
"""
import os
import sys
import time
import json
import datetime

CG = os.path.join(os.path.expanduser("~"), "CrashGuard")
STATE = os.path.join(CG, "state")
LOGS = os.path.join(CG, "logs")
LOCK = os.path.join(STATE, "gpu-keepalive.lock")
HEARTBEAT = os.path.join(STATE, "gpu-keepalive.heartbeat")
LOG = os.path.join(LOGS, "gpu-keepalive.log")

INTERVAL_SEC = 8          # seconds between GPU touches
STILL_ACTIVE = 259


def _now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def log(level, msg):
    line = "%s [%-5s] %s\n" % (_now(), level, msg)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def pid_alive(pid):
    """True if a process with this PID is still running (Windows)."""
    try:
        import ctypes
        PROCESS_QUERY_LIMITED = 0x1000
        h = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED, False, int(pid))
        if not h:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(h)
        return code.value == STILL_ACTIVE
    except Exception:
        return False


def acquire_singleton():
    """Refuse to run as a duplicate (NO duplicate processes -- house rule)."""
    if os.path.exists(LOCK):
        try:
            old = int(open(LOCK).read().strip())
            if old != os.getpid() and pid_alive(old):
                log("WARN", "another gpu-keepalive is alive pid=%d -- exiting (singleton)" % old)
                sys.exit(4)
        except SystemExit:
            raise
        except Exception:
            pass  # stale/garbage lock -- overwrite
    try:
        with open(LOCK, "w") as f:
            f.write(str(os.getpid()))
    except Exception as e:
        log("ERROR", "could not write lock: %s" % e)


def write_heartbeat(payload):
    try:
        with open(HEARTBEAT, "w", encoding="utf-8") as f:
            json.dump(payload, f)
    except Exception:
        pass


def run_torch_engine():
    """PRIMARY engine: hold the context via a tiny resident torch tensor."""
    import torch
    dev = torch.device("cuda:0")
    name = torch.cuda.get_device_name(0)
    # Tiny resident tensor holds the CUDA context. Elementwise ops only so cuBLAS
    # / cuDNN are never loaded -> minimal VRAM footprint.
    anchor = torch.ones((256, 256), device=dev)
    try:
        total_mb = int(torch.cuda.get_device_properties(0).total_memory / (1024 * 1024))
    except Exception:
        total_mb = -1
    log("INFO", "CUDA context resident (torch) on %s (vram=%dMB); interval=%ds -- dGPU pinned to D0"
        % (name, total_mb, INTERVAL_SEC))

    ticks = 0
    consecutive_errors = 0
    while True:
        try:
            # Tiny op + sync forces a real GPU round-trip without pinning clocks.
            val = (anchor * 1.0001).sum().item()
            ticks += 1
            consecutive_errors = 0
            reserved_mb = int(torch.cuda.memory_reserved(0) / (1024 * 1024))
            write_heartbeat({
                "timestamp": _now(),   # key matches CrashGuard self-heal/watchdog convention
                "ts": _now(),
                "pid": os.getpid(),
                "service": "gpu-keepalive",
                "engine": "torch",
                "ticks": ticks,
                "device": name,
                "vram_reserved_mb": reserved_mb,
                "interval_sec": INTERVAL_SEC,
                "checksum": val,
            })
        except Exception as e:
            consecutive_errors += 1
            log("ERROR", "keepalive op failed (#%d) -- device may have dropped: %s"
                % (consecutive_errors, e))
            try:
                torch.cuda.synchronize()
            except Exception:
                pass
            # If the device is genuinely gone, a crash is already imminent; keep
            # trying so we re-anchor the instant the driver recovers.
            if consecutive_errors >= 3:
                try:
                    anchor = torch.ones((256, 256), device=dev)
                    log("INFO", "re-anchored CUDA context after %d errors" % consecutive_errors)
                except Exception as e2:
                    log("ERROR", "re-anchor failed: %s" % e2)
        time.sleep(INTERVAL_SEC)


def run_cuda_driver_engine():
    """FALLBACK engine: retain the device primary context via nvcuda.dll (no torch)."""
    import ctypes
    cuda = ctypes.WinDLL("nvcuda.dll")
    cuda.cuInit.argtypes = [ctypes.c_uint]; cuda.cuInit.restype = ctypes.c_int
    cuda.cuDeviceGet.argtypes = [ctypes.POINTER(ctypes.c_int), ctypes.c_int]; cuda.cuDeviceGet.restype = ctypes.c_int
    cuda.cuDeviceGetName.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_int]; cuda.cuDeviceGetName.restype = ctypes.c_int
    cuda.cuDevicePrimaryCtxRetain.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_int]; cuda.cuDevicePrimaryCtxRetain.restype = ctypes.c_int

    if cuda.cuInit(0) != 0:
        raise RuntimeError("cuInit failed")
    dev = ctypes.c_int(0)
    if cuda.cuDeviceGet(ctypes.byref(dev), 0) != 0:
        raise RuntimeError("cuDeviceGet failed")
    nm = ctypes.create_string_buffer(128)
    cuda.cuDeviceGetName(nm, 128, dev)
    name = nm.value.decode(errors="replace")
    ctx = ctypes.c_void_p()
    if cuda.cuDevicePrimaryCtxRetain(ctypes.byref(ctx), dev) != 0 or not ctx.value:
        raise RuntimeError("cuDevicePrimaryCtxRetain failed")
    log("INFO", "CUDA driver-API primary context retained on %s (no torch) -- dGPU pinned to D0" % name)

    ticks = 0
    while True:
        ticks += 1
        write_heartbeat({
            "timestamp": _now(),
            "ts": _now(),
            "pid": os.getpid(),
            "service": "gpu-keepalive",
            "engine": "cuda-driver",
            "ticks": ticks,
            "device": name,
            "interval_sec": INTERVAL_SEC,
        })
        time.sleep(INTERVAL_SEC)


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # Discovery is not execution: this script writes, so a help request must
        # exit before any work. See script_help_flag_can_execute_work.
        print(__doc__ or os.path.basename(__file__))
        return
    acquire_singleton()
    log("INFO", "gpu-keepalive starting pid=%d python=%s" % (os.getpid(), sys.version.split()[0]))

    # Primary engine: torch (validated). Fallback: raw CUDA driver API, so protection
    # survives a broken torch/python package set (nvcuda.dll ships with the driver).
    use_torch = True
    reason = ""
    try:
        import torch
        if not torch.cuda.is_available():
            use_torch = False
            reason = "torch.cuda.is_available() == False"
    except Exception as e:
        use_torch = False
        reason = "torch import failed: %s" % e

    if use_torch:
        try:
            run_torch_engine()  # infinite loop
            return
        except Exception as e:
            log("ERROR", "torch engine crashed (%s) -- trying CUDA driver-API fallback" % e)
    else:
        log("WARN", "%s -- using CUDA driver-API fallback (no torch)" % reason)

    try:
        run_cuda_driver_engine()  # infinite loop
    except Exception as e:
        log("ERROR", "CUDA driver-API fallback failed: %s -- keepalive cannot hold GPU" % e)
        sys.exit(3)


if __name__ == "__main__":
    main()
