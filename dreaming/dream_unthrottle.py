"""Keep one process tree out of Windows power throttling (EcoQoS) for as long as its root lives. Priority is not touched.

run_dream.ps1 starts this windowless (pythonw) with its own PID. Windows throttles a windowless BelowNormal process by
default, and the dream is one: on 25 Sep the view builder over 3,000 memories took 414 s and 146 s throttled against 36 s
and 42 s opted out (7.2x), the staged secret scan got 0.05 cpu-s per 30 s against 23.28 opted out, and the live view
build hit its 2400 s cap. Every few seconds this opts the root and each new descendant out; it exits when the root is
gone, or after --max-hours. Usage: dream_unthrottle.py <root_pid> [--every S] [--max-hours H] [--log PATH]"""
import argparse
import ctypes
import ctypes.wintypes as w
import os
import time

import psutil


class PPTS(ctypes.Structure):
    _fields_ = [("Version", w.ULONG), ("ControlMask", w.ULONG), ("StateMask", w.ULONG)]


k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.OpenProcess.restype = w.HANDLE
k32.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
k32.SetProcessInformation.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
k32.SetProcessInformation.restype = w.BOOL
k32.CloseHandle.argtypes = [w.HANDLE]
PROCESS_SET_INFORMATION = 0x0200
PROCESS_POWER_THROTTLING = 4          # ProcessPowerThrottling
EXECUTION_SPEED = 0x1                 # PROCESS_POWER_THROTTLING_EXECUTION_SPEED


def opt_out(pid: int) -> bool:
    h = k32.OpenProcess(PROCESS_SET_INFORMATION, False, pid)
    if not h:
        return False
    try:
        st = PPTS(1, EXECUTION_SPEED, 0)  # the speed policy is controlled, and its state is off: never throttle
        return bool(k32.SetProcessInformation(h, PROCESS_POWER_THROTTLING, ctypes.byref(st), ctypes.sizeof(st)))
    finally:
        k32.CloseHandle(h)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", type=int)
    ap.add_argument("--every", type=float, default=5.0)
    ap.add_argument("--max-hours", type=float, default=10.0)
    ap.add_argument("--log", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "dream_unthrottle.log"))
    a = ap.parse_args()
    try:
        root = psutil.Process(a.root)
        root_key = (root.pid, root.create_time())
    except psutil.Error:
        return 0
    seen, failed = set(), 0
    t_end = time.time() + 3600 * a.max_hours
    while time.time() < t_end:
        try:
            if not root.is_running() or (root.pid, root.create_time()) != root_key:
                break
            procs = [root] + root.children(recursive=True)
        except psutil.Error:
            break
        for p in procs:
            try:
                key = (p.pid, p.create_time())
            except psutil.Error:
                continue
            if key in seen:
                continue
            seen.add(key)
            if not opt_out(p.pid):
                failed += 1
        time.sleep(a.every)
    try:
        with open(a.log, "a", encoding="utf-8") as fh:
            fh.write("%s root %d: opted %d processes out of power throttling, %d refused\n"
                     % (time.strftime("%Y-%m-%d %H:%M:%S"), a.root, len(seen) - failed, failed))
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
