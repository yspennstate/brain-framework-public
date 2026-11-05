#!/usr/bin/env python3
"""registry_hygiene.py - disable auto-registered ghost entries so relaunch cannot resurrect them.

The 2026-07-12 landmine: registry_refresh auto-registers every session at its first Stop, and
nothing ever expires them. The registry grew 12 -> 45 entries, all enabled, ~35 of them dead
(one-shot workers, old codex roots). relaunch.ps1 relaunches every enabled entry that fails
Test-Alive, so ONE bare relaunch would have spawned ~35 ghost `-p` windows - a wave-generator
worse than the incident it recovers from.

Policy (deliberately narrow):
  - only entries with auto=true (explicit register.ps1 registrations are the owner's; never touch)
  - only entries not already disabled
  - only entries DEAD by both signals: no live claude/codex process carries the id in its
    command line, AND no transcript write (claude PROJ or codex sessions tree) for >24h
  - action is register-style DISABLE (reversible with register.ps1 -Enable), never delete
  - every action ledgered to watch/nudge_ledger.jsonl

Usage: python registry_hygiene.py [--apply]   (default is dry-run)
"""
import os, sys, json, glob, time, subprocess, datetime

HOME = os.path.expanduser("~")
FLEET = os.path.join(HOME, ".claude", "fleet")
REG = os.path.join(FLEET, "registry")
PROJ = os.path.join(HOME, ".claude", "projects", "{{BRAIN_SLUG}}")
CODEX = os.path.join(HOME, ".codex", "sessions")
LEDGER = os.path.join(FLEET, "watch", "nudge_ledger.jsonl")
STALE_S = 24 * 3600
NO_WIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def live_cmdlines():
    ps = ("Get-CimInstance Win32_Process -Filter \"name='claude.exe' OR name='codex.exe'\" | "
          "ForEach-Object { $_.CommandLine }")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                         capture_output=True, text=True, timeout=45, creationflags=NO_WIN).stdout
    return out or ""


def newest_transcript_age(sid, now):
    # ALL project dirs, not just {{BRAIN_SLUG}} - sessions started from another cwd write
    # elsewhere (caught 2026-07-12: 21 "absent" verdicts were really novel-project sessions)
    ages = []
    for tf in glob.glob(os.path.join(HOME, ".claude", "projects", "*", sid + "*.jsonl")):
        try:
            ages.append(now - os.path.getmtime(tf))
        except OSError:
            pass
    try:
        for cf in glob.glob(os.path.join(CODEX, "**", "*" + sid + "*.jsonl"), recursive=True):
            ages.append(now - os.path.getmtime(cf))
    except Exception:
        pass
    return min(ages) if ages else None


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # Discovery is not execution: this script writes, so a help request must
        # exit before any work. See script_help_flag_can_execute_work.
        print(__doc__ or os.path.basename(__file__))
        return
    apply = "--apply" in sys.argv
    now = time.time()
    cmdlines = live_cmdlines()
    keep, ghosts, skipped = [], [], []
    for f in sorted(glob.glob(os.path.join(REG, "*.json"))):
        try:
            d = json.load(open(f, encoding="utf-8-sig"))
        except Exception:
            skipped.append((os.path.basename(f)[:8], "unreadable"))
            continue
        sid = d.get("id") or os.path.basename(f)[:-5]
        if not d.get("auto"):
            keep.append((sid[:8], "explicit registration"))
            continue
        if d.get("disabled"):
            keep.append((sid[:8], "already disabled"))
            continue
        if sid in cmdlines:
            keep.append((sid[:8], "live process"))
            continue
        age = newest_transcript_age(sid, now)
        if age is not None and age < STALE_S:
            keep.append((sid[:8], "transcript %.1fh old (<24h)" % (age / 3600)))
            continue
        ghosts.append((f, d, sid, age))

    print("registry: %d entries | ghosts to disable: %d | untouched: %d"
          % (len(keep) + len(ghosts) + len(skipped), len(ghosts), len(keep)))
    for f, d, sid, age in ghosts:
        why = "no live proc, transcript %s" % ("%.1fh stale" % (age / 3600) if age is not None
                                               else "absent")
        print("  %s %-26s %s%s" % (sid[:8], (d.get("title") or "")[:26], why,
                                   "" if apply else "  [dry-run]"))
        if apply:
            d["disabled"] = True
            d["disabled_why"] = "registry_hygiene 2026-07-12: auto-registered ghost (%s)" % why
            data = json.dumps(d, indent=1, ensure_ascii=False).encode("utf-8")
            with open(f, "wb") as fh:
                fh.write(b"\xef\xbb\xbf" + data)   # keep the BOM convention (PS readers need it)
            with open(LEDGER, "a", encoding="utf-8") as lg:
                lg.write(json.dumps({"ts": now, "iso": datetime.datetime.now().isoformat(
                    timespec="seconds"), "action": "hygiene-disable", "sid": sid, "why": why}) + "\n")
    if not apply and ghosts:
        print("dry-run only. Re-run with --apply to disable the above (reversible: "
              "register.ps1 -Id <sid> -Enable).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
