#!/usr/bin/env python3
"""health.py - read-only integrity report for the crash-recovery APPARATUS itself (distinct from
status.ps1, which reports agent working/idle/dead). Run anytime to catch apparatus degradation BEFORE a
crash exposes it: registry encoding, checkpoint freshness, coverage gaps, and RESUME consistency.

Consolidates the audits from the 2026-07-11 hardening pass into one command. Read-only: reports, never
writes/kills/relaunches. Usage: python health.py
"""
import os, sys, re, glob, json, time, subprocess

HOME = os.path.expanduser("~")
REG = os.path.join(HOME, ".claude", "fleet", "registry")
PROJ = os.path.join(HOME, ".claude", "projects", "{{BRAIN_SLUG}}")
CODEX = os.path.join(HOME, ".codex", "sessions")
RESUME = os.path.join(HOME, ".claude", "dreaming", "resume")


def _load(f):
    try:
        return json.load(open(f, encoding="utf-8-sig"))
    except Exception as e:
        return {"_error": str(e)}


def _group_dup_resumes(procs):
    """Pure, unit-testable: from a list of {ProcessId,ParentProcessId,Name,CommandLine} dicts, return the
    duplicate-resume groups (session ids with >1 live claude.exe), each {sid,pids,active,childless}. active =
    pids that are some process's parent (own a live tool shell = the real session); childless = the rest.
    Kept separate from process enumeration so the verdict logic can be tested without spawning anything."""
    ppids = {p.get("ParentProcessId") for p in procs}
    groups = {}
    for p in procs:
        if (p.get("Name") or "").lower() == "claude.exe" and "--resume" in (p.get("CommandLine") or ""):
            m = re.search(r"--resume\s+([0-9a-fA-F-]{36})", p.get("CommandLine") or "")
            if m:
                groups.setdefault(m.group(1).lower(), []).append(p.get("ProcessId"))
    dups = []
    for sid, pids in groups.items():
        if len(pids) > 1:
            active = sorted(pid for pid in pids if pid in ppids)       # has child shells = the real session
            childless = sorted(pid for pid in pids if pid not in ppids)
            dups.append({"sid": sid[:8], "pids": sorted(pids), "active": active, "childless": childless})
    return dups


def _dup_verdict(active, pids):
    """Pure, unit-testable: the safe-retire verdict for one duplicate group. NEVER retire by childlessness when
    zero are active - an idle session is momentarily childless too (the 2026-07-12 b918a939 case). Only when
    exactly one is active is the childless set the safe-to-retire zombie(s)."""
    n = len(active)
    if n == 1:
        childless = [p for p in pids if p != active[0]]
        return "CLEAR: pid %s is the live session (owns tool shells); retire the childless dup(s) %s" % (
            active[0], childless)
    if n == 0:
        return ("AMBIGUOUS: all %d procs are childless right now - an idle session is momentarily childless "
                "too, so childlessness does NOT identify the stale one. Do NOT retire by that alone; wait until "
                "one spawns a tool shell (that is the live one), or have the owning agent self-identify via its "
                "own tool-shell parent pid." % len(pids))
    return ("URGENT: %d procs have active shells = two live copies mutating one transcript; the owning agent / "
            "owner must resolve" % n)


def _duplicate_resumes():
    """Detect >1 live claude.exe resuming the SAME session id - a duplicate resume, which risks two copies
    appending to one transcript (pure logic in _group_dup_resumes / _dup_verdict). READ-ONLY: retiring one
    stays a manual, process-tree-verified action. A peer's "kill PID X" can be backwards - 2026-07-12:
    codex-night-watch flagged the LIVE session as the duplicate; following it verbatim would have killed the
    live session. Returns None if detection is unavailable (non-Windows / no powershell), else the dup groups."""
    ps = ("Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,Name,CommandLine | "
          "ConvertTo-Json -Compress")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                             capture_output=True, text=True, timeout=20,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        procs = json.loads(out) if out and out.strip() else []
    except Exception:
        return None
    if isinstance(procs, dict):
        procs = [procs]
    return _group_dup_resumes(procs)


def _selftest():
    """Prove the duplicate-resume logic fires correctly on synthetic process tables - the 'test that proves the
    guard fires' half of the 2026-07-12 lesson. Covers none / CLEAR / AMBIGUOUS / URGENT and that a
    non-duplicated session is not grouped. Run: python health.py --selftest"""
    SID = "afec84b0-bf13-40d7-abb2-35145cfa1cb2"
    SID2 = "b918a939-b0e3-4500-0000-000000000000"

    def cl(s):
        return '"C:\\claude.exe" --resume %s --dangerously-skip-permissions "go"' % s

    def claude(pid, ppid, sid):
        return {"ProcessId": pid, "ParentProcessId": ppid, "Name": "claude.exe", "CommandLine": cl(sid)}

    def shell(pid, ppid):
        return {"ProcessId": pid, "ParentProcessId": ppid, "Name": "bash.exe", "CommandLine": "x"}

    fails = []

    def check(name, procs, expect_sids, verdict_needs):
        dups = _group_dup_resumes(procs)
        got = sorted(d["sid"] for d in dups)
        if got != sorted(expect_sids):
            fails.append("%s: expected sids %s, got %s" % (name, sorted(expect_sids), got))
            return
        for d in dups:
            want = verdict_needs.get(d["sid"])
            if want:
                v = _dup_verdict(d["active"], d["pids"])
                if want not in v:
                    fails.append("%s/%s: verdict %r missing %r" % (name, d["sid"], v, want))

    check("none", [claude(100, 1, SID), shell(101, 100)], [], {})
    check("clear", [claude(100, 1, SID), shell(101, 100), claude(200, 2, SID)], [SID[:8]], {SID[:8]: "CLEAR"})
    check("ambiguous", [claude(100, 1, SID), claude(200, 2, SID)], [SID[:8]], {SID[:8]: "AMBIGUOUS"})
    check("urgent", [claude(100, 1, SID), shell(101, 100), claude(200, 2, SID), shell(201, 200)],
          [SID[:8]], {SID[:8]: "URGENT"})
    check("two-sessions", [claude(100, 1, SID), claude(200, 2, SID), claude(300, 3, SID2)], [SID[:8]], {})

    if fails:
        print("health.py duplicate-resume SELFTEST FAILED:\n  - " + "\n  - ".join(fails))
        return 1
    print("health.py duplicate-resume selftest: PASS (none / CLEAR / AMBIGUOUS / URGENT / two-sessions)")
    return 0


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # Discovery is not execution: this script writes, so a help request must
        # exit before any work. See script_help_flag_can_execute_work.
        print(__doc__ or os.path.basename(__file__))
        return
    now = time.time()
    entries = {}
    problems = []
    for f in glob.glob(os.path.join(REG, "*.json")):
        d = _load(f)
        sid = os.path.basename(f)[:-5]
        raw = open(f, "rb").read()
        d["_bom"] = raw[:3] == b"\xef\xbb\xbf"
        d["_mtime"] = os.path.getmtime(f)
        entries[sid] = d

    # 1) ENCODING: readable, BOM, no mojibake (the 2026-07-11 no-BOM -> PS ANSI -> mojibake class)
    unreadable = [s for s, d in entries.items() if "_error" in d]
    nobom = [s for s, d in entries.items() if not d.get("_bom") and "_error" not in d]
    moji = [s for s, d in entries.items()
            if any(m in ((d.get("task") or "") + (d.get("checkpoint") or "")) for m in ("Ã", "â€"))]
    if unreadable:
        problems.append("UNREADABLE registry entries: " + ", ".join(x[:8] for x in unreadable))
    if moji:
        problems.append("MOJIBAKE in registry (no-BOM utf8 read as ANSI): " + ", ".join(x[:8] for x in moji))

    # 2) FRESHNESS: registry_refresh should keep updated recent for a live session; flag empty checkpoints
    def alive(sid):
        tf = os.path.join(PROJ, sid + ".jsonl")
        if os.path.exists(tf) and now - os.path.getmtime(tf) < 180:
            return True
        try:
            for cf in glob.glob(os.path.join(CODEX, "**", "*" + sid + "*.jsonl"), recursive=True):
                if now - os.path.getmtime(cf) < 180:
                    return True
        except Exception:
            pass
        return False
    live = {s for s in entries if alive(s)}
    # FRESHNESS via the canary's ROBUST signal (checkpoint `updated` vs the last ASSISTANT TURN, zone-correct),
    # NOT file mtimes: an idle-but-tool-active session has a recent transcript yet a legitimately old checkpoint
    # and must NOT be flagged (the confound registry_freshness_canary already solved). Reuse it, don't re-derive.
    try:
        sys.path.insert(0, os.path.join(HOME, ".claude", "dreaming"))
        import registry_freshness_canary as _canary
        _rows, stale_flags = _canary.check(REG, PROJ)
    except Exception:
        stale_flags = []
    empty_ck_live = [s for s in live if not entries[s].get("checkpoint") and not entries[s].get("disabled")
                     and entries[s].get("runtime") != "codex"]

    # 3) COVERAGE: active claude transcripts (<30m) with no registry entry. EXCLUDE dreams - a dream's FIRST
    # message carries run_dream.ps1's "injected by run_dream.ps1" header (PRECISE: won't false-skip a brain-dev
    # agent that merely MENTIONS run_dream, the -Discover bug), and the auto-register correctly skips dreams via
    # DREAM_TYPE - so a dream appearing here is CORRECT behaviour, not a coverage gap.
    active_unreg = []
    for tf in glob.glob(os.path.join(PROJ, "*.jsonl")):
        if now - os.path.getmtime(tf) < 1800:
            sid = os.path.basename(tf)[:-6]
            if sid in entries:
                continue
            try:
                with open(tf, "rb") as fh:
                    if b"injected by run_dream.ps1" in fh.read(20000):
                        continue                       # a dream - correctly unregistered, not a gap
            except Exception:
                pass
            active_unreg.append(sid[:8])

    # 4) RESUME consistency: each live session should have its own resume/RESUME_<sid>.md (gap-2 fix)
    resume_missing = []
    if os.path.isdir(RESUME):
        have = {fn[len("RESUME_"):-3] for fn in os.listdir(RESUME) if fn.startswith("RESUME_")}
        # only CLAUDE sessions use resume/RESUME_<sid>.md; codex has its own `codex resume`, so don't flag it
        live_claude = {s for s in live if os.path.exists(os.path.join(PROJ, s + ".jsonl"))}
        resume_missing = [s[:8] for s in live_claude if s[:8] not in {h[:8] for h in have}]

    auto = [s for s, d in entries.items() if d.get("auto")]
    print("=== crash-recovery apparatus health ===")
    print("registry: %d entries | readable %d | BOM %d | mojibake %d | auto-registered %d"
          % (len(entries), len(entries) - len(unreadable), len(entries) - len(nobom), len(moji), len(auto)))
    print("live sessions (transcript <180s): %d | stale-checkpoint (canary, robust) %d | empty-checkpoint %d"
          % (len(live), len(stale_flags), len(empty_ck_live)))
    print("coverage: active-but-UNREGISTERED claude sessions <30m: %s"
          % (", ".join(active_unreg) if active_unreg else "none (full coverage)"))
    print("RESUME per-session breadcrumb missing for live: %s"
          % (", ".join(resume_missing) if resume_missing else "none"))
    problems.extend(stale_flags)   # genuine stale-checkpoint problems from the robust canary (not file mtimes)
    if active_unreg:
        problems.append("active claude sessions with NO registry entry (run relaunch.ps1 -Discover, or they "
                        "auto-register on next Stop if started post-wiring): " + ", ".join(active_unreg))

    # 5) ADJACENT recovery layers - the whole apparatus, not just the fleet registry:
    #    mesh = the crash-durable coordination state; CrashGuard = the crash DETECTION that informs recovery.
    try:
        mesh_py = os.path.join(HOME, "brain-repo", "12_cognitive_architecture",
                               "agent_mesh", "agent_mesh.py")
        mj = json.loads(subprocess.run([sys.executable, mesh_py, "doctor"],
                                       capture_output=True, text=True, timeout=15,
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout or "{}")
        print("mesh coordination: integrity=%s | agents=%s | claims=%s (expired %s) | offline=%s"
              % (mj.get("integrity"), mj.get("active_agents"), mj.get("active_claims"),
                 mj.get("expired_claims"), mj.get("offline_agents")))
        if mj.get("integrity") not in ("ok", None):
            problems.append("mesh DB integrity=%s (coordination state may be corrupt)" % mj.get("integrity"))
    except Exception as e:
        print("mesh coordination: (doctor unavailable: %s)" % type(e).__name__)
    try:
        pulse = os.path.join(HOME, "CrashGuard", "logs", "pulse.log")
        with open(pulse, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 2000))
            last = f.read().decode("utf-8", "replace").strip().splitlines()[-1]
        age = (now - os.path.getmtime(pulse)) / 60
        m = re.search(r"tier=(\w+).*?bsod_today=(\d+)", last)
        tier, bsod = (m.group(1), m.group(2)) if m else ("?", "?")
        print("CrashGuard detection: heartbeat %.0fmin old | tier=%s | bsod_today=%s" % (age, tier, bsod))
        if age > 10:
            problems.append("CrashGuard heartbeat STALE (%.0fmin) - crash detection may be dead" % age)
    except Exception as e:
        print("CrashGuard detection: (pulse.log unavailable: %s)" % type(e).__name__)

    # 6) DUPLICATE RESUMES - two claude.exe on one session id. Real incident 2026-07-12: a duplicate resume
    #    (33652 live @22:14 + 28204 childless zombie @22:22) AND a peer that named the WRONG one to kill.
    #    Auto-surface it so the next health check / wake catches it before anyone retires the live session.
    dups = _duplicate_resumes()
    if dups is None:
        print("duplicate resumes: (detector unavailable - non-Windows or no powershell)")
    elif not dups:
        print("duplicate resumes: none (no session id has >1 live claude.exe)")
    else:
        for d in dups:
            verdict = _dup_verdict(d["active"], d["pids"])
            print("DUPLICATE RESUME: session %s -> pids %s | %s" % (d["sid"], d["pids"], verdict))
            problems.append("duplicate resume of %s (pids %s) - %s | never trust a peer's PID call here, "
                            "confirm via the live process tree" % (d["sid"], d["pids"], verdict))

    print("\n" + ("HEALTHY: no apparatus integrity problems." if not problems
                  else "PROBLEMS:\n  - " + "\n  - ".join(problems)))
    return 0 if not problems else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    raise SystemExit(main())
