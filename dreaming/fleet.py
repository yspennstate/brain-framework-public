"""fleet.py - who's awake right now, with evidence. For the owner, not for agents.

Born from the a recovery incident,
the owner read it as a crashed Claude, and spawned a second session to redo work the
first was still doing. The mesh board says what agents CLAIM; this joins those claims
to what the machine can PROVE - live processes and transcript write times - so a
glance answers "alive, quiet, or gone?" before any window gets closed.

    fleet            (via %USERPROFILE%\fleet.cmd)
    python ~/.claude/dreaming/fleet.py

Read-only, stdlib-only, exit 0 always.
"""

import io
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

HOME = Path(os.environ.get("USERPROFILE") or str(Path.home()))
MESH = (HOME / "brain-repo" / "12_cognitive_architecture"
        / "agent_mesh" / "agent_mesh.py")
TRANSCRIPTS = HOME / ".claude" / "projects" / "{{BRAIN_SLUG}}"


def run(args, timeout=10):
    try:
        r = subprocess.run(args, capture_output=True, timeout=timeout)
        return r.stdout.decode("utf-8", errors="replace")
    except Exception:
        return ""


def ago(seconds):
    if seconds < 120:
        return "%ds ago" % int(seconds)
    if seconds < 7200:
        return "%dm ago" % int(seconds / 60)
    return "%.1fh ago" % (seconds / 3600.0)


def iso_age(stamp):
    try:
        t = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - t).total_seconds()
    except Exception:
        return None


def transcript_age(session_key):
    if not session_key or "\\" in session_key or "/" in session_key:
        return None
    p = TRANSCRIPTS / (session_key + ".jsonl")
    try:
        return (datetime.now().timestamp() - p.stat().st_mtime)
    except OSError:
        return None


def verdict(t_age, seen_age, lease_age):
    # transcript evidence outranks mesh self-report; mesh outranks silence
    if t_age is not None:
        if t_age < 180:
            return "ALIVE - writing %s. Do NOT close its window." % ago(t_age)
        if t_age < 1800:
            return "alive, quiet (last wrote %s)" % ago(t_age)
        return "no writes for %s - may be idle or gone" % ago(t_age)
    if lease_age is not None and lease_age > 0:
        return "mesh lease EXPIRED %s - likely gone" % ago(lease_age)
    if seen_age is not None:
        return "self-reported %s (no transcript to verify)" % ago(seen_age)
    return "unknown"


def window_report(pid_names):
    """Visible top-level windows of terminal hosts, with Windows' own hung verdict
    (IsHungAppWindow = the 'Not Responding' test). A hung terminal window with ALIVE
    agents under it is exactly the shape that once read as a crash."""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    hosts = {"windowsterminal.exe", "wezterm-gui.exe", "openconsole.exe", "conhost.exe"}
    rows = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            name = pid_names.get(pid.value, "").lower()
            hung = bool(user32.IsHungAppWindow(hwnd))
            if hung or name in hosts:
                n = user32.GetWindowTextLengthW(hwnd)
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                rows.append((pid_names.get(pid.value, "pid %d" % pid.value),
                             pid.value, hung, buf.value[:60]))
        return True

    user32.EnumWindows(cb, 0)
    return rows


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # Discovery is not execution: this script writes, so a help request must
        # exit before any work. See script_help_flag_can_execute_work.
        print(__doc__ or os.path.basename(__file__))
        return
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    print("FLEET - %s local. A frozen window is NOT a dead agent; check here first." % now)

    # what the machine can prove: live agent processes
    pid_names = {}
    out = run(["tasklist", "/FO", "CSV", "/NH"], timeout=8)
    for line in out.splitlines():
        parts = line.split('","')
        if len(parts) > 2 and parts[0].startswith('"'):
            try:
                pid_names[int(parts[1])] = parts[0].lstrip('"')
            except ValueError:
                pass
    counts = {}
    for exe in ("claude.exe", "codex.exe", "node.exe"):
        n = sum(1 for v in pid_names.values() if v.lower() == exe)
        if n:
            counts[exe] = n
    if counts:
        print("Processes: " + ", ".join("%s x%d" % (k, v) for k, v in sorted(counts.items())))

    # terminal windows: responsive or hung (agents underneath can outlive a hung UI)
    try:
        rows = window_report(pid_names)
        if rows:
            print("Terminal windows:")
            for name, pid, hung, title in rows:
                state = "NOT RESPONDING (UI hung - agents under it may still be ALIVE; see below before closing)" if hung else "responsive"
                print("  %s (pid %d) %s%s" % (name, pid, state,
                                              (' - "%s"' % title) if title else ""))
    except Exception:
        pass

    # what the mesh claims, joined to transcript evidence
    board = run([sys.executable, str(MESH), "--json", "status"], timeout=15)
    agents = []
    try:
        agents = json.loads(board).get("agents", [])
    except Exception:
        print("(mesh board unreadable - showing transcript evidence only)")
    seen_keys = set()
    if agents:
        print()
        for a in sorted(agents, key=lambda x: x.get("last_seen", ""), reverse=True):
            aid = a.get("agent_id", "?")
            key = a.get("session_key", "")
            seen_keys.add(key)
            t_age = transcript_age(key)
            # iso_age is positive for past stamps: a positive lease_until age = expired lease
            v = verdict(t_age, iso_age(a.get("last_seen", "")),
                        iso_age(a.get("lease_until", "")) if a.get("lease_until") else None)
            task = (a.get("task", "") or "").replace("\n", " ")
            if len(task) > 90:
                task = task[:90].rstrip() + "..."
            print("  %-34s %s" % (aid, v))
            print("  %-34s   %s" % ("", task))

    # recent transcripts with no mesh identity (sessions that never said hello)
    try:
        stray = []
        for p in TRANSCRIPTS.glob("*.jsonl"):
            if p.name.startswith("agent-"):
                continue
            age = datetime.now().timestamp() - p.stat().st_mtime
            if age < 6 * 3600 and p.stem not in seen_keys:
                stray.append((age, p.stem[:8]))
        if stray:
            print()
            print("Sessions writing recently but NOT on the mesh (no hello):")
            for age, sid in sorted(stray)[:8]:
                print("  %s... last wrote %s" % (sid, ago(age)))
            if len(stray) > 8:
                print("  (+%d older)" % (len(stray) - 8))
    except OSError:
        pass

    print()
    print("Boards: mesh status --markdown | workspace.py stream | .agents\\coordination\\BOARD.md")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("FLEET VIEW DEGRADED: %s: %s" % (type(exc).__name__, exc))
    sys.stdout.flush()
