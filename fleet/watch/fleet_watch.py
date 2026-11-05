#!/usr/bin/env python3
"""fleet_watch.py - per-minute OBSERVE-ONLY fleet monitor (2026-07-12, session efc1169a).

Samples every --interval seconds: claude/codex process census (cpu deltas, child shells),
transcript pulses, duplicate-resume groups (reuses health.py's tested logic), relaunch waves,
compute mode. Appends one JSONL row per tick to watch/fleet_watch.jsonl and prints an EVENT
line to stdout ONLY when something actionable changes - stdout is consumed by a Monitor, so
every line wakes the supervising session.

Healer doctrine (theta_probe_churn scar): this script NEVER kills, nudges, or relaunches.
It observes and reports; medicine stays a manual, tree-verified, rate-limited action by the
supervisor. First, do no harm.

Stop cleanly: create watch/STOP. Selftest: python fleet_watch.py --selftest.
"""
import os, sys, json, time, re, subprocess, argparse

HOME = os.path.expanduser("~")
FLEET = os.path.join(HOME, ".claude", "fleet")
WATCH = os.path.join(FLEET, "watch")
PROJ = os.path.join(HOME, ".claude", "projects", "{{BRAIN_SLUG}}")
CODEX = os.path.join(HOME, ".codex", "sessions")
COMPUTE = os.path.join(HOME, ".claude", "compute", "compute_state.json")
LOG = os.path.join(WATCH, "fleet_watch.jsonl")
STOPFILE = os.path.join(WATCH, "STOP")

sys.path.insert(0, FLEET)
import health  # _group_dup_resumes / _dup_verdict: pure, selftested (2026-07-12)

CPU_QUIET_SEC = 1.0       # < this many cpu-seconds per tick (self+descendants) = a quiet minute.
                          # Calibrated on live fleet 2026-07-12 12:55: generating agents burn 3-8 s/min
                          # even childless; idle TUIs 0.05-0.3 with rare 1.6 spikes. 1.0 splits the bands
                          # (an idle spike >1.0 just delays flagging - miss-side, never churn-side).
STALL_TICKS = 12          # consecutive quiet minutes before STALL-SUSPECT
EVENT_COOLDOWN_S = 1200   # same (kind,key) event at most every 20 min
WAVE_NEW_RESUMES = 3      # >= this many new --resume claude procs in one tick = a relaunch wave
HEARTBEAT_TICKS = 30

def _now():
    return time.time()

def set_below_normal():
    try:
        import ctypes
        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)
    except Exception:
        pass

def get_procs():
    """All processes: pid, ppid, name, cmdline (agents only get full), cpu (seconds)."""
    ps = ("Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,Name,"
          "CommandLine,KernelModeTime,UserModeTime | ConvertTo-Json -Compress")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                         capture_output=True, text=True, timeout=45,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    procs = json.loads(out) if out and out.strip() else []
    if isinstance(procs, dict):
        procs = [procs]
    return procs


def _normalize(procs):
    """cpu seconds from WMI 100ns tick fields; safe on synthetic tables too."""
    for p in procs:
        if "cpu" not in p:
            p["cpu"] = ((p.get("KernelModeTime") or 0) + (p.get("UserModeTime") or 0)) / 1e7
    return procs

def descendants_cpu(pid, by_ppid, depth=0):
    total, kids = 0.0, 0
    if depth > 6:
        return total, kids
    for c in by_ppid.get(pid, []):
        total += c["cpu"]
        kids += 1
        t, k = descendants_cpu(c["ProcessId"], by_ppid, depth + 1)
        total += t
        kids += k
    return total, kids

def sid_of(cmd):
    m = re.search(r"--resume\s+([0-9a-fA-F-]{36})", cmd or "")
    if m:
        return m.group(1).lower()
    m = re.search(r"\bresume\s+([0-9a-fA-F-]{36})", cmd or "")   # codex resume <id>
    return m.group(1).lower() if m else None

def agent_rows(procs):
    """One row per claude.exe/codex.exe: pid, sid, mode, cpu incl descendants, child count."""
    by_ppid = {}
    for p in procs:
        by_ppid.setdefault(p.get("ParentProcessId"), []).append(p)
    rows = []
    for p in procs:
        name = (p.get("Name") or "").lower()
        if name not in ("claude.exe", "codex.exe"):
            continue
        cmd = p.get("CommandLine") or ""
        # same-image HELPERS (v4, adversarial-watcher catch): the harness spawns e.g. its ripgrep
        # under the claude.exe image with `rg ...` argv. Admit by ARGV0 BASENAME, not substring -
        # a substring rule wrongly admits `rg --glob !claude/ ...` (contains "claude") and wrongly
        # relies on paths. argv0 is the quoted or first-whitespace token; its basename must start
        # with the image stem (claude/codex) for this row to be an agent.
        c = cmd.lstrip()
        if c.startswith('"'):
            argv0 = c[1:c.find('"', 1)] if c.find('"', 1) > 0 else c
        else:
            argv0 = c.split(None, 1)[0] if c.split(None, 1) else c
        base = argv0.replace("\\", "/").rsplit("/", 1)[-1].lower()
        stem = name.split(".")[0]
        if not base.startswith(stem):
            continue
        if name == "codex.exe" and "codex-win32" in cmd and " resume" not in cmd and " exec" not in cmd:
            # inner codex.exe spawned by the node wrapper carries no args; keep it (it IS the agent)
            pass
        dcpu, kids = descendants_cpu(p["ProcessId"], by_ppid)
        rows.append({
            "pid": p["ProcessId"], "ppid": p.get("ParentProcessId"), "name": name,
            "sid": sid_of(cmd), "gentle": (" -p " in (cmd + " ") or cmd.rstrip().endswith(" -p")
                                           or " exec " in cmd),   # codex exec = one-shot, exit is DONE not DEAD
            "cpu": round(p["cpu"] + dcpu, 2), "kids": kids,
            "cmd_head": re.sub(r"\s+", " ", cmd)[:110],
        })
    return rows

def transcript_pulse(now, fresh_s=180):
    claude = []
    try:
        for fn in os.listdir(PROJ):
            if fn.endswith(".jsonl"):
                fp = os.path.join(PROJ, fn)
                try:
                    if now - os.path.getmtime(fp) < fresh_s:
                        claude.append(fn[:8])
                except OSError:
                    pass
    except OSError:
        pass
    codex = 0
    try:
        import glob as _g
        for fp in _g.glob(os.path.join(CODEX, "**", "*.jsonl"), recursive=True):
            try:
                if now - os.path.getmtime(fp) < fresh_s:
                    codex += 1
            except OSError:
                pass
    except Exception:
        pass
    return sorted(set(claude)), codex

def compute_mode():
    try:
        d = json.load(open(COMPUTE, encoding="utf-8-sig"))
        return "%s/%s" % (d.get("status"), d.get("mode"))
    except Exception:
        return "unknown"

class Watch:
    def __init__(self, exclude_pids):
        self.exclude = set(exclude_pids)
        self.prev_cpu = {}       # pid -> cpu seconds (self+descendants)
        self.quiet = {}          # pid -> consecutive quiet ticks
        self.known = {}          # pid -> row (last seen)
        self.last_event = {}     # (kind,key) -> ts
        self.prev_dups = {}      # sid -> verdict-kind
        self.tick_n = 0

    def emit(self, kind, key, msg, force=False):
        now = _now()
        k = (kind, str(key))
        if not force and now - self.last_event.get(k, 0) < EVENT_COOLDOWN_S:
            return None
        self.last_event[k] = now
        line = "EVENT %s %s" % (kind, msg)
        print(line, flush=True)
        return line

    def tick(self, procs, now=None):
        now = now or _now()
        self.tick_n += 1
        rows = agent_rows(_normalize(procs))
        events = []
        cur_pids = {r["pid"] for r in rows}

        # deaths / completions
        for pid, old in list(self.known.items()):
            if pid not in cur_pids:
                kind = "DONE" if old.get("gentle") else "DEAD"
                e = self.emit(kind, pid, "pid=%s sid=%s cmd=%r" %
                              (pid, (old.get("sid") or "?")[:8], old.get("cmd_head", "")[:80]),
                              force=(kind == "DEAD"))
                if e:
                    events.append(e)
                self.known.pop(pid, None)
                self.prev_cpu.pop(pid, None)
                self.quiet.pop(pid, None)

        # waves: many NEW --resume claude procs at once (tick 1 sees everything as new - skip it)
        new_resumes = [r for r in rows if r["pid"] not in self.known
                       and r["name"] == "claude.exe" and r["sid"]]
        if self.tick_n > 1 and len(new_resumes) >= WAVE_NEW_RESUMES:
            e = self.emit("WAVE", "wave", "%d new --resume claude procs in one tick: %s" %
                          (len(new_resumes), [(r["pid"], (r["sid"] or "")[:8]) for r in new_resumes]),
                          force=True)
            if e:
                events.append(e)

        # stalls: cpu-quiet + childless streaks (skip self/excluded, skip gentle one-shots' natural end)
        for r in rows:
            pid = r["pid"]
            # pid REUSE between ticks (v4, 2026-07-12 auditor catch): a known pid reappearing with a
            # DIFFERENT name/cmd is a new process wearing an old number - close out the old record
            # first, never blend their histories (the rg.exe-labeled DEAD came from this).
            old = self.known.get(pid)
            if old and (old.get("name") != r["name"] or
                        (old.get("sid") or "") != (r.get("sid") or "")):
                kind = "DONE" if old.get("gentle") else "DEAD"
                e = self.emit(kind, "%s-reused" % pid, "pid=%s sid=%s cmd=%r (pid since reused)" %
                              (pid, (old.get("sid") or "?")[:8], old.get("cmd_head", "")[:80]),
                              force=(kind == "DEAD"))
                if e:
                    events.append(e)
                self.prev_cpu.pop(pid, None)
                self.quiet.pop(pid, None)
            if pid in self.exclude:
                self.known[pid] = r
                self.prev_cpu[pid] = r["cpu"]
                continue
            prev = self.prev_cpu.get(pid)
            if prev is not None:
                delta = max(0.0, r["cpu"] - prev)
                if delta < CPU_QUIET_SEC and r["kids"] == 0:
                    self.quiet[pid] = self.quiet.get(pid, 0) + 1
                else:
                    self.quiet[pid] = 0
                if self.quiet.get(pid, 0) == STALL_TICKS:
                    e = self.emit("STALL-SUSPECT", pid,
                                  "pid=%s sid=%s quiet_min=%d cpu=%.1f cmd=%r" %
                                  (pid, (r["sid"] or "?")[:8], STALL_TICKS, r["cpu"],
                                   r["cmd_head"][:80]))
                    if e:
                        events.append(e)
            self.prev_cpu[pid] = r["cpu"]
            self.known[pid] = r

        # duplicate resumes (health.py's tested logic)
        dups = health._group_dup_resumes(procs)
        cur = {}
        for d in dups:
            verdict = health._dup_verdict(d["active"], d["pids"])
            kind = verdict.split(":")[0]
            cur[d["sid"]] = kind
            if self.prev_dups.get(d["sid"]) != kind:
                e = self.emit("DUP", d["sid"], "sid=%s pids=%s verdict=%s" %
                              (d["sid"], d["pids"], verdict[:160]), force=(kind != "AMBIGUOUS"))
                if e:
                    events.append(e)
        for sid in set(self.prev_dups) - set(cur):
            e = self.emit("DUP-RESOLVED", sid, "sid=%s no longer duplicated" % sid, force=True)
            if e:
                events.append(e)
        self.prev_dups = cur
        return rows, dups, events


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=int, default=60)
    ap.add_argument("--exclude", default="", help="comma-separated pids never stall-flagged (self)")
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()
    set_below_normal()
    os.makedirs(WATCH, exist_ok=True)
    exclude = [int(x) for x in args.exclude.split(",") if x.strip().isdigit()]
    w = Watch(exclude)
    prev_compute = None
    print("EVENT WATCH-UP interval=%ss exclude=%s pid=%s" % (args.interval, exclude, os.getpid()),
          flush=True)
    while True:
        if os.path.exists(STOPFILE):
            print("EVENT WATCH-STOP stopfile seen", flush=True)
            return 0
        t0 = _now()
        try:
            procs = get_procs()
            rows, dups, events = w.tick(procs, t0)
            claude_active, codex_active = transcript_pulse(t0)
            cm = compute_mode()
            if prev_compute is not None and cm != prev_compute and ("distress" in cm or "throttle" in cm):
                w.emit("COMPUTE", "mode", "compute mode changed %s -> %s" % (prev_compute, cm), force=True)
            prev_compute = cm
            if w.tick_n % HEARTBEAT_TICKS == 1 and w.tick_n > 1:
                print("EVENT HEARTBEAT tick=%d claude=%d codex=%d fresh_claude_transcripts=%d "
                      "fresh_codex=%d dups=%d compute=%s" %
                      (w.tick_n, sum(1 for r in rows if r["name"] == "claude.exe"),
                       sum(1 for r in rows if r["name"] == "codex.exe"),
                       len(claude_active), codex_active, len(dups), cm), flush=True)
            with open(LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "ts": round(t0, 1), "tick": w.tick_n,
                    "agents": [{k: r[k] for k in ("pid", "name", "sid", "gentle", "cpu", "kids")}
                               for r in rows],
                    "quiet": {str(p): q for p, q in w.quiet.items() if q >= 3},
                    "fresh_claude": claude_active, "fresh_codex": codex_active,
                    "dups": dups, "compute": cm, "events": events,
                }, default=str) + "\n")
        except Exception as e:
            print("EVENT WATCH-ERROR %s: %s" % (type(e).__name__, str(e)[:200]), flush=True)
        if args.once:
            return 0
        time.sleep(max(5, args.interval - (_now() - t0)))


def _selftest():
    def proc(pid, ppid, name, cmd, cpu):
        return {"ProcessId": pid, "ParentProcessId": ppid, "Name": name, "CommandLine": cmd,
                "KernelModeTime": int(cpu * 1e7 / 2), "UserModeTime": int(cpu * 1e7 / 2)}
    SID = "d26d6c0b-d8cd-4dbf-83af-f7e23a0b56d8"
    SID2 = "4e59ca13-17f5-436b-8934-23cafa3d3f0d"
    fails = []
    w = Watch(exclude_pids=[999])

    # tick 1: two agents, one busy (child shell), one idle-but-new; no events expected beyond none
    t = [proc(100, 1, "claude.exe", "claude.exe --resume %s go" % SID, 10),
         proc(101, 100, "bash.exe", "x", 5),
         proc(200, 2, "claude.exe", "claude.exe --resume %s go" % SID2, 10),
         proc(999, 3, "claude.exe", "claude.exe", 10)]
    rows, dups, ev = w.tick(t, now=1000.0)
    if dups:
        fails.append("tick1: unexpected dups %s" % dups)
    if ev:
        fails.append("tick1: unexpected events %s" % ev)
    if not any(r["pid"] == 100 and r["kids"] == 1 for r in rows):
        fails.append("tick1: pid 100 should have 1 child")

    # ticks 2..N: pid 200 cpu frozen + childless -> STALL-SUSPECT at exactly STALL_TICKS quiet ticks
    got_stall = []
    for i in range(STALL_TICKS + 2):
        t2 = [proc(100, 1, "claude.exe", "claude.exe --resume %s go" % SID, 10 + i),
              proc(101, 100, "bash.exe", "x", 5),
              proc(200, 2, "claude.exe", "claude.exe --resume %s go" % SID2, 10),
              proc(999, 3, "claude.exe", "claude.exe", 10)]
        _, _, ev = w.tick(t2, now=1000.0 + 60 * (i + 1))
        got_stall += [e for e in ev if "STALL-SUSPECT" in e]
    if len(got_stall) != 1 or "pid=200" not in got_stall[0]:
        fails.append("stall: expected exactly one STALL-SUSPECT for pid 200, got %s" % got_stall)
    if any("999" in e for e in got_stall):
        fails.append("stall: excluded pid 999 must never be flagged")

    # duplicate appears -> DUP event; disappears -> DUP-RESOLVED
    t3 = [proc(100, 1, "claude.exe", "claude.exe --resume %s go" % SID, 40),
          proc(101, 100, "bash.exe", "x", 5),
          proc(300, 4, "claude.exe", "claude.exe --resume %s go" % SID, 1)]
    _, dups, ev = w.tick(t3, now=99999.0)
    if not any("DUP" in e and "CLEAR" in e for e in ev):
        fails.append("dup: expected CLEAR DUP event, got %s" % ev)
    t4 = [proc(100, 1, "claude.exe", "claude.exe --resume %s go" % SID, 41),
          proc(101, 100, "bash.exe", "x", 5)]
    _, _, ev = w.tick(t4, now=99999.0 + 60)
    if not any("DUP-RESOLVED" in e for e in ev):
        fails.append("dup: expected DUP-RESOLVED, got %s" % ev)
    # pid 300 vanished but was NOT gentle -> DEAD event expected in same tick
    if not any("DEAD" in e and "pid=300" in e for e in ev):
        fails.append("dead: expected DEAD for pid 300, got %s" % ev)

    # wave: 3 new resumes in one tick
    w2 = Watch([])
    base = [proc(100, 1, "claude.exe", "claude.exe --resume %s go" % SID, 1)]
    w2.tick(base, now=5000.0)
    wave = base + [proc(401, 1, "claude.exe", "claude.exe --resume %s x" % SID2, 0),
                   proc(402, 1, "claude.exe", "claude.exe --resume 89f84043-38e8-4e2c-b4bb-3d66964f7e4d x", 0),
                   proc(403, 1, "claude.exe", "claude.exe --resume afec84b0-bf13-40d7-abb2-35145cfa1cb2 x", 0)]
    _, _, ev = w2.tick(wave, now=5060.0)
    if not any("WAVE" in e for e in ev):
        fails.append("wave: expected WAVE event, got %s" % ev)

    # gentle one-shot exit -> DONE (not DEAD)
    w3 = Watch([])
    g = [proc(500, 1, "claude.exe", 'claude.exe -p --model claude-fable-5 "edit"', 1)]
    w3.tick(g, now=7000.0)
    _, _, ev = w3.tick([], now=7060.0)
    if not any("DONE" in e and "pid=500" in e for e in ev):
        fails.append("gentle: expected DONE for pid 500, got %s" % ev)
    if any("DEAD" in e for e in ev):
        fails.append("gentle: one-shot exit must not be DEAD")

    # helper admission (v4): argv0-basename rule - quoted agent path admitted; bare rg excluded
    # even when its ARGS contain the stem; helper argv under the agent image excluded
    wh = Watch([])
    rows_h, _, _ = wh.tick([
        proc(700, 1, "claude.exe", r'"C:\x\claude-code\bin\claude.exe" --resume ' + SID, 1),
        proc(701, 1, "claude.exe", 'rg --no-config --files --glob !claude/', 1),
        proc(702, 1, "codex.exe", 'rg --files', 1),
    ], now=9000.0)
    got = sorted(r["pid"] for r in rows_h)
    if got != [700]:
        fails.append("helper-admission: expected only pid 700 admitted, got %s" % got)

    # pid reuse (v4): same pid returns as a DIFFERENT session -> old record closed out with ITS
    # OWN cmd, histories never blended (the rg.exe mislabel class)
    w4 = Watch([])
    w4.tick([proc(600, 1, "claude.exe", "claude.exe --resume %s go" % SID, 5)], now=8000.0)
    _, _, ev = w4.tick([proc(600, 1, "claude.exe", "claude.exe --resume %s go" % SID2, 1)],
                       now=8060.0)
    reuse = [e for e in ev if "reused" in e or "DEAD" in e]
    if not any("DEAD" in e and SID[:8] in e for e in reuse):
        fails.append("pid-reuse: expected DEAD closing the OLD session record, got %s" % ev)
    if w4.quiet.get(600, 0) != 0:
        fails.append("pid-reuse: trackers must reset for the new occupant")

    print("fleet_watch selftest: %s" % ("PASS (stall/dup/dead/done/wave/exclude)" if not fails
                                        else "FAIL:\n  - " + "\n  - ".join(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    raise SystemExit(main())
