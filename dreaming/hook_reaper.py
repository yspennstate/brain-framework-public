#!/usr/bin/env python3
"""hook_reaper.py - kill hook invocations the runner cancelled but never terminated.

Found 2026-09-07: when a UserPromptSubmit / PreToolUse hook exceeds its timeout, the Claude Code runner stops waiting
but does not kill the process. On a loaded box each cancelled hook (its python, plus a conhost) stays alive for hours
- twelve of them from the previous day were found orphaned at 12:4x, beside 54 dead sleep/tail/grep/console-host
pipelines. Every leak adds to the process count that makes the NEXT hook take minutes to start: the brain's own
plumbing was starving the brain. This reaper runs every 15 minutes (windowless scheduled task) and stops any hook
invocation that (a) is orphaned - its parent no longer exists - and (b) is older than MAX_AGE_MIN, matching the hook
by its exact operand (the dreaming/ hook scripts, the error-graph inline hook, the project-memory hook), never by a
substring that could match a shell or a peer's worker. Owner order of the same hour: turn off what is not doing
anything. Log: ~/.claude/dreaming/hook_reaper.log. Fail-safe: any error -> exit 0.
    python hook_reaper.py [--dry-run] [--max-age-min 15]
"""
import argparse, io, os, re, subprocess, sys, time

HOME = os.path.expanduser("~")
LOG = os.path.join(HOME, ".claude", "dreaming", "hook_reaper.log")
HOOK_SCRIPTS = ("instinct_prime.py", "proof_sense.py", "math_sense.py", "spread_recall.py", "fok_probe.py", "compute_sense.py",
                "danger_mode.py", "brain_switch.py", "canonical_route.py", "work_timer.py", "prompt_archive.py", "mailbox_hook.py",
                "project_memory_hook.py", "ps1_guard.py", "report_guards_hook.py", "coactivation_end.py", "resume_checkpoint.py",
                "registry_refresh.py", "claim_check.py", "campaign_keep_working.py", "retraction_guard.py", "conscience.py",
                "pre_report_check.py", "reality_monitor.py", "push_reminder.py")
HOOK_RE = re.compile(r"(ERROR_GRAPH_MODE|" + "|".join(re.escape(s) for s in HOOK_SCRIPTS) + r")")
# Owner order 2026-09-07 12:3x: "fix the whole process for these so that this stuff gets automatically cleaned up every 20
# minutes by the brain and agents". Two victim classes, both ORPHANS ONLY (parent process gone - a hidden daemon launched by a
# VBS is an orphan by design, so the classes are narrow and named, never "any orphan"):
#   A. a hook invocation older than MAX_AGE_MIN (the runner cancelled it and never killed it);
#   B. a dead pipeline: sleep/tail/grep with no parent (older than 5 min), or a bash/cmd shell with no parent and no
#      children (older than 10 min). pythonw / python daemons and PowerShell scripts are NEVER in class B, and neither is
#      a console host (see the 12:51 scar below DEAD_PIPE: it dies with its owner and "no parent" says nothing).
# The healer rule (theta_probe_churn scar): every run logs candidates and kills, so a sweeper that kills a lot is itself the
# thing to inspect; it never restarts anything and never touches a process with a living parent.
# 12:51 on 09-07: the first live run stopped three "orphaned" console hosts aged 5-9 min - they were the consoles of a
# DETACHED test server's live claude sessions (start /b breaks the parent chain but the console is in use), and every
# in-flight item returned an empty envelope. A console host dies with its owning process; it is never a victim here.
# 2026-09-24 05:49: class B killed a LIVE grep, 12 minutes into a background `python ... | grep > file`
# run, and the python died with exit 120 when its pipe went; the same sweep took two other agents' 900 s and 1200 s
# sleeps. Git Bash is MSYS: bash forks, the child execs, and the forked Windows process exits, so EVERY program a bash
# script starts - each pipeline element, each sleep - has a dead Windows parent while its MSYS parent is alive. Measured
# the same hour on three live processes: all three had a dead Windows parent and a live MSYS parent. "No Windows parent"
# therefore said nothing about sleep/tail/grep/bash, and class B was killing any of them older than 5-10 minutes
# (214 sleeps, 210 greps, 154 tails, 66 bash shells since 09-07). The fix: an MSYS process is an orphan only when its
# MSYS parent is gone as well (Git's ps.exe gives PID, PPID and WINPID; an MSYS parent that died reparents its children
# to PID 1). No MSYS table, no class B for MSYS programs: without it the reaper cannot tell a live pipeline from a dead one.
DEAD_PIPE = ("sleep.exe", "tail.exe", "grep.exe")
SHELLS = ("bash.exe", "cmd.exe")
MSYS_PS = r"C:\Program Files\Git\usr\bin\ps.exe"


def msys_table():
    """{winpid: (msys_pid, msys_ppid)} for every MSYS process, from Git's ps.exe; None when it cannot be read."""
    try:
        r = subprocess.run([MSYS_PS, "-e", "-l"], capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=60, creationflags=(0x08000000 if os.name == "nt" else 0))
    except (OSError, subprocess.SubprocessError):
        return None
    out = {}
    for ln in (r.stdout or "").splitlines()[1:]:
        tok = ln.split()
        if tok and not tok[0].isdigit():
            tok = tok[1:]          # a leading status flag (S, I, O) on some rows
        if len(tok) >= 4 and all(t.isdigit() for t in tok[:4]):
            out[int(tok[3])] = (int(tok[0]), int(tok[1]))
    return out if (r.returncode == 0 and out) else None


def msys_parent_alive(winpid, table):
    """True when the process is an MSYS process whose MSYS parent is still running (so it belongs to live work)."""
    pid_ppid = table.get(winpid)
    if not pid_ppid:
        return False
    ppid = pid_ppid[1]
    return ppid != 1 and ppid in {p for p, _ in table.values()}
PS = r"""
$all = Get-CimInstance Win32_Process
$ids = @{}; $all | ForEach-Object { $ids[$_.ProcessId] = 1 }
$kids = @{}; $all | ForEach-Object { $kids[$_.ParentProcessId] = 1 }
foreach ($p in $all) {
  if ($p.Name -notin @('python.exe','pythonw.exe','powershell.exe','sleep.exe','tail.exe','grep.exe','conhost.exe','OpenConsole.exe','bash.exe','cmd.exe')) { continue }
  $orph = -not $ids.ContainsKey($p.ParentProcessId)
  $haskids = $kids.ContainsKey($p.ProcessId)
  $age = ((Get-Date) - $p.CreationDate).TotalMinutes
  $cmd = $p.CommandLine
  if ($cmd) { $cmd = $cmd.Replace("`n"," ").Replace("`r"," ") }
  Write-Output ("{0}`t{1}`t{2}`t{3}`t{4:F1}`t{5}" -f $p.ProcessId, $p.Name, $orph, $haskids, $age, $cmd)
}
"""


def census():
    if os.name != "nt":
        # Nothing to reap off Windows: an orphan here is re-parented to launchd/init and reaped when it exits, so the
        # stranded-console-host population this tool exists for does not form. An empty census reaps nothing.
        return []
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", PS], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=240, creationflags=(0x08000000 if os.name == "nt" else 0))
    rows = []
    for ln in (r.stdout or "").splitlines():
        parts = ln.split("\t", 5)
        if len(parts) == 6:
            pid, name, orph, haskids, age, cmd = parts
            try:
                rows.append((int(pid), name, orph.strip().lower() == "true", haskids.strip().lower() == "true", float(age), cmd or ""))
            except ValueError:
                pass
    return rows


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--dry-run", action="store_true"); ap.add_argument("--max-age-min", type=float, default=15.0)
    a = ap.parse_args()
    me = os.getpid()
    victims = []
    msys = None
    spared = 0
    rows = census()
    if any(r[2] and (r[1] in DEAD_PIPE or r[1] == "bash.exe") for r in rows):
        msys = msys_table()
    for pid, name, orph, haskids, age, cmd in rows:
        if not orph or pid == me:
            continue
        if name in ("python.exe", "pythonw.exe", "powershell.exe"):
            if age > a.max_age_min and HOOK_RE.search(cmd):
                victims.append((pid, name, age, "A:hook " + cmd))
            continue
        if name in DEAD_PIPE or name == "bash.exe":
            # MSYS programs: a dead Windows parent is normal (see the 09-24 note above DEAD_PIPE); judge by the MSYS parent,
            # and without the MSYS table judge nothing
            if msys is None or msys_parent_alive(pid, msys):
                spared += 1
                continue
        if name in DEAD_PIPE and age > 5:
            victims.append((pid, name, age, "B:pipe"))
        elif name in SHELLS and not haskids and age > 10:
            victims.append((pid, name, age, "B:shell"))
    killed = 0
    for pid, name, age, cmd in victims:
        if a.dry_run:
            continue
        r = subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, text=True, creationflags=(0x08000000 if os.name == "nt" else 0))
        if r.returncode == 0:
            killed += 1
    try:
        with io.open(LOG, "a", encoding="utf-8") as f:
            f.write("%s\tcandidates=%d\tkilled=%d\tdry=%s\t%s\tspared_msys=%d%s\n" % (
                time.strftime("%Y-%m-%d %H:%M:%S"), len(victims), killed, a.dry_run,
                "; ".join("%s:%d:%.0fm" % (n, p, ag) for p, n, ag, _ in victims[:12]), spared,
                "" if msys is not None else " (no MSYS table: class B skipped for MSYS programs)"))
    except OSError:
        pass
    print("hook_reaper: candidates %d, killed %d, spared (live MSYS parent or no MSYS table) %d%s" % (
        len(victims), killed, spared, " (dry run)" if a.dry_run else ""))
    if a.dry_run:
        for p, n, ag, why in victims:
            print("  would stop %s %d (%.0f min) %s" % (n, p, ag, why[:100]))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("hook_reaper: error %s" % e)
    sys.exit(0)
