"""crash_breadcrumb.py - SessionStart: say so when the Mac came back from a crash, and name the sessions it killed.

1000 ways D10 #36 (crash-detecting resume breadcrumb). After the 22:43 kernel panic on 3 Nov the sessions restarted
with nothing at wake saying the machine had crashed; which agents had died was found by hand an hour later (two
sessions and five owner asks, W14). Within a day of an unclean boot this prints one block: the boot time, and the
Claude and Codex sessions that were writing in the 3 h before it and have written nothing since.

Unclean means wtmp (`last`) ends the sessions open before this boot with "crash" instead of a shutdown record. Prints
nothing on a clean boot, more than a day after boot, or when `last` cannot be read.
"""
import glob
import json
import os
import re
import subprocess
import sys
import time

H = os.path.expanduser("~")
WINDOW = 24 * 3600     # how long after the boot a waking session is told
BEFORE = 3 * 3600      # a session that last wrote this long before the boot counts as killed by it


def boot_time():
    try:
        out = subprocess.run(["sysctl", "-n", "kern.boottime"], capture_output=True, text=True, timeout=10).stdout
        return int(re.search(r"sec = (\d+)", out).group(1))
    except Exception:
        return 0


def unclean(last_text):
    """True when the sessions open before the newest boot ended in 'crash', False on a shutdown, None if unknown."""
    seen_boot = False
    for line in last_text.splitlines():
        if line.startswith("reboot time"):
            if seen_boot:
                return None          # two boots with no session between them: nothing to judge by
            seen_boot = True
            continue
        if not seen_boot or not line.strip() or line.startswith("wtmp begins"):
            continue
        if line.startswith("shutdown time"):
            return False
        if re.search(r"- crash\b", line):
            return True
        if re.search(r"- shutdown\b", line):
            return False
        return None                  # the first older session ended some other way
    return None


def killed(boot, me):
    """(runtime, short id, last write) for transcripts silent since the boot that were live when the box went down.

    A Claude session counts if it wrote in the BEFORE window: an interactive one sits idle for an hour and still dies.
    A Codex rollout counts only if it never logged task_complete: `codex exec` workers start and end every few minutes
    (3 Nov: 79 rollouts in the 3 h before the panic, every one but the last ending in task_complete)."""
    out = []
    for path in glob.glob(f"{H}/.claude/projects/*/*.jsonl") + glob.glob(f"{H}/.codex/sessions/*/*/*/*.jsonl"):
        try:
            st = os.stat(path)
        except OSError:
            continue
        if not (boot - BEFORE <= st.st_mtime < boot) or st.st_size < 10_000:
            continue
        name = os.path.basename(path)[:-6]
        if ".codex" in path:
            try:
                with open(path, "rb") as f:
                    f.seek(max(0, st.st_size - 20_000))
                    if b'"task_complete"' in f.read():
                        continue
            except OSError:
                continue
            # rollout uuids are time-ordered (v7), so their heads collide; the tail tells them apart
            out.append(("codex", name[-8:], st.st_mtime))
        elif not name.startswith(me[:8] or "-"):
            out.append(("claude", name[:8], st.st_mtime))
    return sorted(out, key=lambda r: -r[2])


def main():
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", "replace") if hasattr(sys.stdin, "buffer") else sys.stdin.read()
        i = raw.find("{")
        me = (json.loads(raw[i:]) if i >= 0 else {}).get("session_id") or ""
    except Exception:
        me = ""
    boot = boot_time()
    if not boot or time.time() - boot > WINDOW:
        return
    try:
        text = subprocess.run(["last"], capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return
    if unclean(text) is not True:
        return
    dead = killed(boot, me)
    when = time.strftime("%H:%M on %-d %b", time.localtime(boot))
    lines = [f"[crash] This Mac went down without a clean shutdown and rebooted at {when} (wtmp ends the sessions "
             f"open before it with 'crash'). Anything that was running then died with it: loops, claims, renders, "
             f"half-written files. Check a job's own output, not its status file, before trusting it."]
    if dead:
        shown = ", ".join(f"{rt} {sid} (last write {time.strftime('%H:%M', time.localtime(t))})" for rt, sid, t in dead[:10])
        more = f" and {len(dead) - 10} more" if len(dead) > 10 else ""
        lines.append(f"Sessions that never finished and have been silent since (writing in the {BEFORE // 3600} h before "
                     f"the crash; some may have stopped earlier for other reasons): {shown}{more}. "
                     f"Their owner asks need a note; restarting them is the owner's call.")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
