"""window_witness.py - certify that no console window appeared, by being TOLD instead of looking.

The rule (owner, 2026-06-07) is that no background process may flash a console, not even for ~100 ms,
and the stated verification is a poll of 50 ms or faster. The polling instrument can no longer hold that
bar on this machine - flash_hunt.ps1's own last runs report 50.6, 61.4 and 57.6 ms per sample and end
"treat a clean run as INCONCLUSIVE" - and an 18-minute run of it raised ui_freeze with the owner at the
keyboard. Polling faster is the wrong direction: it costs more of exactly the resource whose starvation
the rule exists to prevent, and a sampler can always miss something shorter than its interval.

Windows will tell us instead. SetWinEventHook(EVENT_OBJECT_SHOW) delivers a callback when a window is
shown, so a flash of ANY duration is observed - one millisecond or a hundred - and when nothing is
shown the watcher costs nothing at all. That converts "no flash seen at 57 ms sampling" into "the OS
reported every window shown in this interval, and none was a console".
"""
import ctypes
from ctypes import wintypes
import json
import sys
import time

user32 = ctypes.windll.user32
EVENT_OBJECT_DESTROY = 0x8001
EVENT_OBJECT_SHOW = 0x8002
EVENT_OBJECT_HIDE = 0x8003
WINEVENT_OUTOFCONTEXT = 0x0000
WINEVENT_SKIPOWNPROCESS = 0x0002
CONSOLE_CLASSES = {"ConsoleWindowClass", "PseudoConsoleWindow"}

WinEventProc = ctypes.WINFUNCTYPE(
    None, wintypes.HANDLE, wintypes.DWORD, wintypes.HWND,
    wintypes.LONG, wintypes.LONG, wintypes.DWORD, wintypes.DWORD)

sightings = []


def _class_of(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _pid_of(hwnd):
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


_live = {}


def _on_show(hook, event, hwnd, idobj, idchild, thread, when):
    # idobj 0 = OBJID_WINDOW; ignore child-object shows, they are not windows appearing.
    if not hwnd or idobj != 0:
        return
    # A window being destroyed no longer has a class to read - GetClassNameW returns empty - so the
    # close-out path must NOT ask what it was. `_live` only ever holds consoles we recorded on SHOW,
    # so membership is the identity check. (Asking first is what left every duration None.)
    if event != EVENT_OBJECT_SHOW:
        born = _live.pop(int(hwnd), None)
        if born is None:
            return
        ms = (time.time() - born) * 1000.0
        for s_ in reversed(sightings):
            if s_["hwnd"] == int(hwnd) and s_["visible_ms"] is None:
                s_["visible_ms"] = round(ms, 1)
                break
        return
    try:
        cls = _class_of(hwnd)
    except Exception:
        return
    if cls not in CONSOLE_CLASSES:
        return
    if True:
        _live[int(hwnd)] = time.time()
        sightings.append({"at": time.strftime("%Y-%m-%d %H:%M:%S"), "class": cls,
                          "pid": _pid_of(hwnd), "hwnd": int(hwnd), "visible_ms": None})
        return


def watch(seconds):
    cb = WinEventProc(_on_show)
    hook = user32.SetWinEventHook(EVENT_OBJECT_DESTROY, EVENT_OBJECT_HIDE, None, cb, 0, 0,
                                  WINEVENT_OUTOFCONTEXT | WINEVENT_SKIPOWNPROCESS)
    if not hook:
        return {"verdict": "UNKNOWN", "reason": "SetWinEventHook failed", "sightings": []}
    msg = wintypes.MSG()
    deadline = time.time() + seconds
    try:
        while time.time() < deadline:
            # PeekMessage keeps the loop responsive without blocking past the deadline.
            while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            time.sleep(0.005)
    finally:
        user32.UnhookWinEvent(hook)
    return {"verdict": "NO_CONSOLE_SHOWN" if not sightings else "CONSOLE_SHOWN",
            "reason": "the OS reported every window shown during the interval",
            "seconds": seconds, "sightings": sightings}


if __name__ == "__main__":
    secs = float(sys.argv[1]) if len(sys.argv) > 1 else 10.0
    print(json.dumps(watch(secs), indent=1))
