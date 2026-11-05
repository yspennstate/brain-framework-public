#!/usr/bin/env python3
"""roster_A.py - generate the canonical "AgentsA" roster from LIVE truth, and (opt-in) archive
stale dead registry entries.

AgentsA = exactly the agents running when this is generated - the owner's clean, current fleet,
replacing the ~40 stale dead registry entries that accumulated. A canonical member is
PROCESS-BACKED: a live claude/codex process actually carries its session id (state WORKING or
IDLE). It KEEPS each session's existing A-name/C-name (the registry titles already carry A1..A8)
and keeps a real title (<user>, fleetwatch) for the rest; only a nameless work agent gets the
next free A-number.

Plain-started ALIVE* sessions (transcript hot but NO process carries the sid) are deliberately
NOT auto-added: they may be subagents, forks, or an owner terminal, and blindly resuming a
plain-started session is the exact move that duplicated the fleet on 07-12. They are listed
under "unclassified_live" for the owner to confirm.

Writes AGENTS_A.json, consumed by bringup_A.ps1 to relaunch exactly the canonical set via the
existing launch_one.ps1. Never deletes: --archive MOVES dead entries into registry/_archive.

  python roster_A.py                     # print the current AgentsA roster (no writes)
  python roster_A.py --write             # write AGENTS_A.json
  python roster_A.py --write --archive   # also move DEAD, enabled, >2h-quiet entries to _archive
"""
import os, sys, json, glob, re, time, datetime, shutil
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nudge

FLEET = nudge.FLEET
REG = nudge.REG
ARCHIVE = os.path.join(REG, "_archive")
OUT = os.path.join(FLEET, "AGENTS_A.json")
PROJ_ROOT = os.path.join(os.path.expanduser("~"), ".claude", "projects")
UUID8 = re.compile(r"^[0-9a-f]{8}$")


def transcript_age(sid):
    c = glob.glob(os.path.join(PROJ_ROOT, "*", sid + ".jsonl"))
    return (time.time() - max(os.path.getmtime(x) for x in c)) if c else None


def live_snapshot():
    out = nudge._ps("Get-CimInstance Win32_Process -Filter \"name='claude.exe' OR name='codex.exe'\" | "
                    "Select-Object ProcessId,ParentProcessId,CommandLine | ConvertTo-Json -Compress").stdout
    procs = json.loads(out) if out and out.strip() else []
    if isinstance(procs, dict):
        procs = [procs]
    ao = nudge._ps("Get-CimInstance Win32_Process | Select-Object ParentProcessId | "
                   "ConvertTo-Json -Compress").stdout
    allp = json.loads(ao) if ao and ao.strip() else []
    if isinstance(allp, dict):
        allp = [allp]
    ppids = {p.get("ParentProcessId") for p in allp}
    return procs, ppids


def state_of(sid, procs, ppids):
    mine = [p for p in procs if sid in (p.get("CommandLine") or "")]
    if not mine:
        a = transcript_age(sid)
        return "ALIVE*" if (a is not None and a < 300) else "DEAD"
    return "WORKING" if any(p.get("ProcessId") in ppids for p in mine) else "IDLE"


def load_reg():
    d = {}
    for f in glob.glob(os.path.join(REG, "*.json")):
        try:
            e = json.load(open(f, encoding="utf-8-sig"))
        except Exception:
            continue
        d[e.get("id") or os.path.basename(f)[:-5]] = (f, e)
    return d


def aname(e):
    tok = ((e.get("title") or "").strip().split() or [""])[0]
    return tok if (tok[:1] in ("A", "C") and tok[1:].isdigit()) else ""


def display_name(e, used, counter):
    a = aname(e)
    if a:
        return a
    first = ((e.get("title") or "").strip().split() or [""])[0]
    if first and not UUID8.match(first):
        return first[:12]                       # real name: <user>, fleetwatch, ...
    while ("A%d" % counter["n"]) in used:        # nameless work agent -> next free A-number
        counter["n"] += 1
    nm = "A%d" % counter["n"]
    used.add(nm)
    return nm


def build():
    reg = load_reg()
    procs, ppids = live_snapshot()
    backed, unclassified = [], []
    for sid, (f, e) in reg.items():
        st = state_of(sid, procs, ppids)
        if st in ("WORKING", "IDLE"):
            backed.append((sid, e, st))
        elif st == "ALIVE*":
            unclassified.append({"sid": sid, "title": e.get("title", ""),
                                 "note": "plain-started (no process carries the sid); may be a "
                                         "subagent/fork/owner terminal - confirm before adding"})
    used = {aname(e) for _, e, _ in backed if aname(e)}
    counter = {"n": 1}
    agents = []
    for sid, e, st in sorted(backed, key=lambda x: (aname(x[1]) or "ZZ", x[0])):
        nproc = len([p for p in procs if sid in (p.get("CommandLine") or "")])
        agents.append({
            "name": display_name(e, used, counter), "sid": sid, "runtime": e.get("runtime", "claude"),
            "state": st, "live_procs": nproc, "title": e.get("title", ""), "task": e.get("task", ""),
            "checkpoint": e.get("checkpoint", ""), "cwd": e.get("cwd", ""),
            "mode": e.get("mode", "gentle"), "disabled": bool(e.get("disabled"))})
    return reg, agents, unclassified


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # Discovery is not execution: this script writes, so a help request must
        # exit before any work. See script_help_flag_can_execute_work.
        print(__doc__ or os.path.basename(__file__))
        return
    write = "--write" in sys.argv
    archive = "--archive" in sys.argv
    reg, agents, unclassified = build()
    print("AgentsA canonical roster (%d process-backed agents):" % len(agents))
    for a in agents:
        print("  %-11s %-8s %-7s %-7s %s" % (a["name"], a["sid"][:8], a["runtime"], a["state"], a["title"][:44]))
    dups = [a for a in agents if a.get("live_procs", 1) > 1]
    if dups:
        print("\n  DUPLICATE-RESUME (flagged, never killed here - resolve via the DUP protocol,")
        print("  health.py verdict + self-ID mail; earlier pid continues, later yields):")
        for a in dups:
            print("    %-11s %-8s %d live processes" % (a["name"], a["sid"][:8], a["live_procs"]))
    if unclassified:
        print("\nunclassified live sessions (%d; NOT added - confirm each):" % len(unclassified))
        for u in unclassified:
            print("  %-8s %s" % (u["sid"][:8], u["title"][:44]))

    if write:
        force = "--force" in sys.argv
        prev = None
        if os.path.exists(OUT):
            try:
                prev = json.load(open(OUT, encoding="utf-8"))
            except Exception:
                prev = None
        prev_count = (prev or {}).get("count")
        # NEVER overwrite the recovery roster with an empty/partial snapshot. Post-crash the live set is
        # empty, so 'regenerate then bringup' would replace the roster with count=0 and lose it (audit 2026-07-12).
        if not force and len(agents) == 0:
            print("REFUSE --write: 0 process-backed agents right now (a crash, or the fleet is down). The "
                  "existing AGENTS_A.json IS the recovery roster - not overwriting it with an empty set. "
                  "Run bringup_A.ps1 -Go to restore first, or --force to clear deliberately.")
            return 1
        if not force and prev_count and len(agents) * 2 < prev_count:
            print("REFUSE --write: new count %d is under half the previous %d - looks transient/partial. "
                  "Re-run when the fleet is up, or --force." % (len(agents), prev_count))
            return 1
        if os.path.exists(OUT):
            try:
                shutil.copy2(OUT, OUT + ".prev")     # keep the last good roster for recovery
            except Exception:
                pass
        manifest = {
            "generated": datetime.datetime.now().isoformat(timespec="seconds"),
            "generated_by": "roster_A.py",
            "note": ("Canonical AgentsA roster = process-backed agents running when generated. "
                     "bringup_A.ps1 relaunches exactly these via launch_one.ps1. Regenerate to refresh."),
            "count": len(agents), "agents": agents, "unclassified_live": unclassified,
            "duplicates": [{"name": a["name"], "sid": a["sid"], "live_procs": a["live_procs"]} for a in dups]}
        json.dump(manifest, open(OUT, "w", encoding="utf-8"), indent=2)
        print("\nwrote %s (previous rotated to AGENTS_A.json.prev)" % OUT)

    if archive:
        dry = "--dry-run" in sys.argv
        live_sids = {a["sid"] for a in agents}
        procs, ppids = live_snapshot()
        cands = []
        for sid, (f, e) in reg.items():
            if sid in live_sids or e.get("disabled"):
                continue                                 # never a live/AgentsA member or a disabled entry
            if state_of(sid, procs, ppids) != "DEAD":
                continue                                 # never touch anything live (incl ALIVE*)
            age = transcript_age(sid)
            if age is not None and age < 7200:
                continue                                 # touched <2h ago: leave it for recovery
            cands.append((sid, f, e))
        print("\narchive candidates (%d): DEAD, enabled, >2h quiet, not in AgentsA:" % len(cands))
        for sid, f, e in cands:
            print("  %-8s %s" % (sid[:8], (e.get("title") or "")[:46]))
        if dry:
            print("[dry-run] nothing moved. Re-run with --archive (no --dry-run) to move them.")
        else:
            os.makedirs(ARCHIVE, exist_ok=True)
            for sid, f, e in cands:
                shutil.move(f, os.path.join(ARCHIVE, os.path.basename(f)))
            print("archived %d entries -> %s (reversible: move the file back)" % (len(cands), ARCHIVE))
    return 0


if __name__ == "__main__":
    sys.exit(main())
