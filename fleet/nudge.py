#!/usr/bin/env python3
"""nudge.py - prompt another agent in a different terminal, safely (2026-07-12).

The three states of a fleet agent and the right medicine for each:
  WORKING (owns tool shells / recent cpu)  -> mail  (a mailbox file; a hook surfaces it in-turn)
  IDLE    (alive TUI, quiet, childless)    -> wake  (tree-verified retire + guarded relaunch w/ prompt)
  DEAD    (no process serves the session)  -> wake  (guarded relaunch via launch_one.ps1)

Healer doctrine (the theta-probe churn scar): every intervention is evidence-gated against the
target's own recent baseline (the fleet watch log), rate-limited (30 min per session, 6/hour
global), and ledgered (watch/nudge_ledger.jsonl). status/mail are read-only-ish; wake is the
only medicine and refuses without evidence. Never kills python; only claude.exe/codex.exe pids
whose command line still matches the session id AND are childless at kill time.

Usage:
  python nudge.py status
  python nudge.py mail <sid-prefix> "message" [--from NAME]
  python nudge.py wake <sid-prefix> [--prompt TEXT] [--dry-run] [--force]
  python nudge.py ledger [-n 20]
"""
import os, sys, json, time, glob, argparse, subprocess, datetime

HOME = os.path.expanduser("~")
FLEET = os.path.join(HOME, ".claude", "fleet")
REG = os.path.join(FLEET, "registry")
MAILBOX = os.path.join(FLEET, "mailbox")
WATCHLOG = os.path.join(FLEET, "watch", "fleet_watch.jsonl")
LEDGER = os.path.join(FLEET, "watch", "nudge_ledger.jsonl")
LAUNCH_ONE = os.path.join(FLEET, "launch_one.ps1")
PROJ = os.path.join(HOME, ".claude", "projects", "{{BRAIN_SLUG}}")

PER_SID_COOLDOWN_S = 1800
GLOBAL_PER_HOUR = 6
IDLE_QUIET_TICKS = 10          # watch-log quiet streak needed to call a live proc "idle"

NO_WIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _ps(cmd, timeout=45):
    return subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                          capture_output=True, text=True, timeout=timeout, creationflags=NO_WIN)


def procs_for(sid):
    out = _ps("Get-CimInstance Win32_Process -Filter \"name='claude.exe' OR name='codex.exe'\" | "
              "Select-Object ProcessId,ParentProcessId,Name,CommandLine | ConvertTo-Json -Compress").stdout
    procs = json.loads(out) if out and out.strip() else []
    if isinstance(procs, dict):
        procs = [procs]
    mine = [p for p in procs if sid in (p.get("CommandLine") or "")]
    all_out = _ps("Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId | "
                  "ConvertTo-Json -Compress").stdout
    allp = json.loads(all_out) if all_out and all_out.strip() else []
    if isinstance(allp, dict):
        allp = [allp]
    ppids = {p.get("ParentProcessId") for p in allp}
    for p in mine:
        p["has_kids"] = p["ProcessId"] in ppids
    return mine


def resolve_sid(prefix):
    """Full sid from a prefix, via registry then live transcripts."""
    if len(prefix) == 36:
        return prefix
    hits = {os.path.basename(f)[:-5] for f in glob.glob(os.path.join(REG, prefix + "*.json"))}
    hits |= {os.path.basename(f)[:-6] for f in glob.glob(
        os.path.join(HOME, ".claude", "projects", "*", prefix + "*.jsonl"))}
    hits = sorted(hits)
    if len(hits) == 1:
        return hits[0]
    raise SystemExit("sid prefix %r -> %s matches %s" % (prefix, len(hits), [h[:12] for h in hits]))


def registry_entry(sid):
    f = os.path.join(REG, sid + ".json")
    try:
        return json.load(open(f, encoding="utf-8-sig"))
    except Exception:
        return {}


def watch_quiet_streak(pids, ticks=3):
    """Max quiet-streak among pids per the fleet watch's own log (the target's OWN baseline).
    Returns (max_streak_seen, log_age_s) or (None, None) if the watch log is unusable."""
    try:
        with open(WATCHLOG, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 200_000))
            lines = f.read().decode("utf-8", "replace").strip().splitlines()
        rows = [json.loads(x) for x in lines[-ticks:]]
        age = time.time() - rows[-1]["ts"]
        streak = 0
        for r in rows:
            for pid in pids:
                streak = max(streak, int(r.get("quiet", {}).get(str(pid), 0)))
        return streak, age
    except Exception:
        return None, None


def ledger_append(rec):
    os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
    rec["ts"] = time.time()
    rec["iso"] = datetime.datetime.now().isoformat(timespec="seconds")
    # WHO ran this (2026-07-12: a foreign dry-run appeared in the ledger unattributable) - the
    # invoking session if the hook env is present, else user+pid; never empty.
    rec["by"] = (os.environ.get("CLAUDE_SESSION_ID") or "").strip()[:12] or \
                "%s@pid%s" % (os.environ.get("USERNAME", "?"), os.getppid())
    with open(LEDGER, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")


def rate_ok(sid):
    now = time.time()
    per_sid, per_hour = [], []
    try:
        for line in open(LEDGER, encoding="utf-8-sig"):
            r = json.loads(line)
            if r.get("action") not in ("wake-launch", "wake-retire+launch"):
                continue
            if r.get("dry_run"):
                continue           # previews are not medicine; only real launches consume budget
            if now - r["ts"] < 3600:
                per_hour.append(r)
            if r.get("sid") == sid and now - r["ts"] < PER_SID_COOLDOWN_S:
                per_sid.append(r)
    except FileNotFoundError:
        pass
    if per_sid:
        return False, "per-sid cooldown: nudged %s %dm ago" % (sid[:8], (now - per_sid[-1]["ts"]) / 60)
    if len(per_hour) >= GLOBAL_PER_HOUR:
        return False, "global cap: %d interventions in the last hour" % len(per_hour)
    return True, ""


def do_mail(sid, message, sender):
    d = os.path.join(MAILBOX, sid)
    os.makedirs(d, exist_ok=True)
    fn = os.path.join(d, "%s_%s.md" % (datetime.datetime.now().strftime("%Y%m%d-%H%M%S"), sender))
    with open(fn, "w", encoding="utf-8") as f:
        f.write("from: %s\nto: %s\nsent: %s\n\n%s\n"
                % (sender, sid, datetime.datetime.now().isoformat(timespec="seconds"), message))
    ledger_append({"action": "mail", "sid": sid, "from": sender, "file": os.path.basename(fn)})
    print("mail queued: %s (a mailbox hook or the next wake delivers it)" % fn)


def launch_one(sid, prompt, dry_run, mode="gentle"):
    if not os.path.exists(LAUNCH_ONE):
        print("launch_one.ps1 not present yet (build in progress) - cannot launch. Mail queued instead?")
        return 90
    args = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", LAUNCH_ONE,
            "-Id", sid]
    if prompt:
        args += ["-Prompt", prompt]
    if mode:
        # a NUDGE wants unattended work to resume; interactive would open a PRESS-ENTER banner
        # that nobody presses (the A8 session sat pre-filled for 15h). gentle -p auto-runs.
        args += ["-Mode", mode]
    if dry_run:
        args += ["-DryRun"]
    r = subprocess.run(args, capture_output=True, text=True, timeout=90, creationflags=NO_WIN)
    sys.stdout.write(r.stdout or "")
    sys.stderr.write(r.stderr or "")
    return r.returncode


def do_wake(sid, prompt, dry_run, force):
    mine = procs_for(sid)
    ent = registry_entry(sid)
    if ent.get("disabled") and not force:
        print("REFUSE: %s is disabled in the registry (off stays off). -Enable it first." % sid[:8])
        ledger_append({"action": "refuse-disabled", "sid": sid})
        return 5
    # "work ... for the next N hours" re-arms the work_timer clock in the waked session, so its
    # own Stop hook sustains the loop - without it a gentle -p wake runs exactly ONE turn and
    # stops again (observed 2026-07-12: three wakes all DONE within ~3 minutes).
    default_prompt = ("NUDGE from fleet supervisor: you appear stopped. Work on your task for the "
                      "next 2 hours - re-read your task and checkpoint, verify on-disk state, and "
                      "keep working turn after turn; end each turn with a concise status. Task: %s "
                      "| Checkpoint: %s"
                      % (ent.get("task", "(see your transcript)"),
                         ent.get("checkpoint", "(see your transcript)")))
    prompt = prompt or default_prompt

    if not mine:
        # a PLAIN-started session (no --resume in any cmdline) is invisible to the process match
        # but very much alive - its transcript is hot. Relaunching it would CREATE a duplicate
        # (the exact incident class this tool exists to prevent; caught live 2026-07-12).
        # Check ALL project dirs: another-cwd session writes outside {{BRAIN_SLUG}}.
        cands = glob.glob(os.path.join(HOME, ".claude", "projects", "*", sid + ".jsonl"))
        tf = max(cands, key=os.path.getmtime) if cands else os.path.join(PROJ, sid + ".jsonl")
        if os.path.exists(tf) and time.time() - os.path.getmtime(tf) < 300 and not force:
            print("REFUSE: no process carries %s in its cmdline, but its transcript was written "
                  "%ds ago - a plain-started LIVE session. Use `mail`." %
                  (sid[:8], time.time() - os.path.getmtime(tf)))
            ledger_append({"action": "refuse-plain-alive", "sid": sid})
            return 10
        ok, why = (True, "") if force else rate_ok(sid)
        if not ok:
            print("REFUSE (rate): " + why)
            ledger_append({"action": "refuse-rate", "sid": sid, "why": why})
            return 6
        print("state=DEAD (no live process serves %s, transcript stale) -> guarded launch" % sid[:8])
        rc = launch_one(sid, prompt, dry_run)
        ledger_append({"action": "wake-launch", "sid": sid, "dry_run": dry_run, "rc": rc,
                       "evidence": "no live claude/codex process with sid in cmdline"})
        return rc

    # a DUPLICATED sid never goes through wake - even --force. The retire loop would kill every
    # childless match (both twins of an AMBIGUOUS pair). Duplicates get the DUP protocol:
    # health.py verdict + self-ID mail (see PROMPTING.md). Auditor catch, 2026-07-12.
    if len(mine) > 1:
        print("REFUSE: %d live processes serve %s - a DUPLICATE group. wake cannot pick; use the "
              "DUP protocol (health.py verdict, self-ID mail)." % (len(mine), sid[:8]))
        ledger_append({"action": "refuse-duplicate-group", "sid": sid,
                       "pids": [p["ProcessId"] for p in mine]})
        return 11

    # alive: demand idle evidence from the target's own baseline before any medicine
    with_kids = [p for p in mine if p.get("has_kids")]
    if with_kids and not force:
        print("REFUSE: pid(s) %s own live child shells - the agent is WORKING. Use `mail`."
              % [p["ProcessId"] for p in with_kids])
        ledger_append({"action": "refuse-working", "sid": sid,
                       "pids": [p["ProcessId"] for p in mine]})
        return 7
    streak, age = watch_quiet_streak([p["ProcessId"] for p in mine])
    if not force:
        if streak is None or age is None or age > 300:
            print("REFUSE: fleet watch log unusable/stale (age=%s) - no baseline, no medicine." % age)
            ledger_append({"action": "refuse-no-baseline", "sid": sid})
            return 8
        if streak < IDLE_QUIET_TICKS:
            print("REFUSE: quiet streak %s < %s min per the watch's own log - not proven idle. "
                  "Wait or use `mail`." % (streak, IDLE_QUIET_TICKS))
            ledger_append({"action": "refuse-not-idle", "sid": sid, "streak": streak})
            return 9
    ok, why = (True, "") if force else rate_ok(sid)
    if not ok:
        print("REFUSE (rate): " + why)
        ledger_append({"action": "refuse-rate", "sid": sid, "why": why})
        return 6

    pids = [p["ProcessId"] for p in mine]
    print("state=IDLE-VERIFIED (childless, quiet %s ticks) pids=%s -> retire + guarded relaunch"
          % (streak, pids))
    if dry_run:
        print("[dry-run] would Stop-Process %s then launch_one -Id %s -Prompt <nudge>" % (pids, sid[:8]))
        ledger_append({"action": "wake-retire+launch", "sid": sid, "dry_run": True, "pids": pids})
        return 0
    for pid in pids:
        # atomic re-verify: same pid, still claude/codex, still this sid, still childless
        chk = _ps("$p = Get-CimInstance Win32_Process -Filter \"ProcessId=%d\"; "
                  "$kids = @(Get-CimInstance Win32_Process -Filter \"ParentProcessId=%d\"); "
                  "if ($p -and $p.Name -match 'claude|codex' -and $p.CommandLine -match '%s' "
                  "-and $kids.Count -eq 0) { Stop-Process -Id %d -Force; 'retired %d' } "
                  "else { 'SKIP %d (state changed)' }" % (pid, pid, sid[:8], pid, pid, pid))
        print((chk.stdout or chk.stderr).strip())
    rc = launch_one(sid, prompt, dry_run=False)
    ledger_append({"action": "wake-retire+launch", "sid": sid, "dry_run": False, "pids": pids,
                   "rc": rc, "evidence": "childless + quiet>=%s ticks per watch log" % streak})
    return rc


def do_status():
    reg = sorted(glob.glob(os.path.join(REG, "*.json")))
    out = _ps("Get-CimInstance Win32_Process -Filter \"name='claude.exe' OR name='codex.exe'\" | "
              "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress").stdout
    procs = json.loads(out) if out and out.strip() else []
    if isinstance(procs, dict):
        procs = [procs]
    now = time.time()
    print("%-10s %-6s %-8s %-6s %-5s %s" % ("sid", "state", "trans_age", "procs", "mail", "title"))
    for f in reg:
        e = json.load(open(f, encoding="utf-8-sig"))
        sid = e.get("id", os.path.basename(f)[:-5])
        mine = [p for p in procs if sid in (p.get("CommandLine") or "")]
        cands = glob.glob(os.path.join(HOME, ".claude", "projects", "*", sid + ".jsonl"))
        tf = max(cands, key=os.path.getmtime) if cands else None
        age_s = (now - os.path.getmtime(tf)) if tf else None
        age = "%dm" % (age_s / 60) if age_s is not None else "-"
        mail = len(glob.glob(os.path.join(MAILBOX, sid, "*.md")))
        state = ("DISABLED" if e.get("disabled") else
                 "alive" if mine else
                 "alive*" if age_s is not None and age_s < 300 else   # plain-started, hot transcript
                 "dead")
        print("%-10s %-6s %-8s %-6s %-5s %s" % (sid[:8], state, age, len(mine), mail or "",
                                                (e.get("title") or "")[:40]))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    m = sub.add_parser("mail")
    m.add_argument("sid")
    m.add_argument("message")
    m.add_argument("--from", dest="sender", default="fleet-supervisor")
    w = sub.add_parser("wake")
    w.add_argument("sid")
    w.add_argument("--prompt", default=None)
    w.add_argument("--mode", default="gentle", choices=["gentle", "interactive"])
    w.add_argument("--dry-run", action="store_true")
    w.add_argument("--force", action="store_true")
    lg = sub.add_parser("ledger")
    lg.add_argument("-n", type=int, default=20)
    a = ap.parse_args()
    if a.cmd == "status":
        return do_status()
    if a.cmd == "mail":
        return do_mail(resolve_sid(a.sid), a.message, a.sender)
    if a.cmd == "wake":
        return do_wake(resolve_sid(a.sid), a.prompt, a.dry_run, a.force)
    if a.cmd == "ledger":
        try:
            for line in open(LEDGER, encoding="utf-8-sig").readlines()[-a.n:]:
                r = json.loads(line)
                print("%s %-22s %-10s %s" % (r.get("iso", "?"), r.get("action"),
                                             (r.get("sid") or "")[:8],
                                             r.get("why") or r.get("evidence") or ""))
        except FileNotFoundError:
            print("(no interventions ledgered yet)")
        return 0


if __name__ == "__main__":
    sys.exit(main())
