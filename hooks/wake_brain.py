#!/usr/bin/env python3
"""wake_brain.py - what a new session sees on waking (SessionStart hook). POSIX port of wake_brain.ps1.

Morning ritual: who you are, what happened recently, what the night's dream left, what questions are open.
Keep output compact - this lands in every session's context (harness inline budget ~10 KB).

  --part 1   who you are: startup ritual, autobiography, the body, compute, the waking check, where I was
  --part 2   today's mind: newest memories, last dream, state of mind, partner, open questions, curiosity,
             conscience, salience, the gut, the brain's predictions
  (no --part: both, in order)

Section numbers follow the .ps1 so the two can be compared line by line. Windows-only sources are replaced:
CrashGuard's pulse.log -> load average and memory pressure; Win32_Process -> ps.
"""

import sys
if __name__ == "__main__" and any(arg in ("-h", "--help") for arg in sys.argv[1:]):
    print(__doc__ or "No command-line options.")
    raise SystemExit(0)
import csv, glob, hashlib, json, os, re, subprocess, sys, time
from datetime import datetime, timezone

HOME = os.path.expanduser("~")
CLAUDE = os.path.join(HOME, ".claude")
DR = os.path.join(CLAUDE, "dreaming")
try:
    SLUG = open(os.path.join(DR, "BRAIN_SLUG"), encoding="utf-8").read().strip()
except OSError:
    SLUG = HOME.replace("/", "-")
MEM = os.path.join(CLAUDE, "projects", SLUG, "memory")
VIEWS = os.path.join(HOME, "brain-views")
OUT = []


def say(s=""):
    OUT.append(s)


def read(p):
    with open(p, encoding="utf-8", errors="replace") as f:
        return f.read()


def age_h(p):
    return (time.time() - os.path.getmtime(p)) / 3600.0


def cap(s, n, tail=" [...]"):
    return s if len(s) <= n else s[:n].rstrip() + tail


def part1(sid):
    # 1) identity + ritual
    try:
        say(read(os.path.join(CLAUDE, "brain_startup.md")).rstrip("\n"))
    except OSError:
        pass

    # 1b) who you are - the autobiography's opening
    try:
        m = re.search(r"\*\*What I am\.\*\*\s*(.+?)(\r?\n){2,}", read(os.path.join(MEM, "AUTOBIOGRAPHY.md")), re.S)
        if m:
            say("\n--- Who you are (AUTOBIOGRAPHY.md - the story so far) ---")
            say("**What I am.** " + re.sub(r"\s+", " ", m.group(1).strip()))
    except OSError:
        pass

    # 1c) the body. CrashGuard does not exist on macOS: read load and memory pressure directly.
    try:
        ncpu = os.cpu_count() or 1
        l1, l5, _ = os.getloadavg()
        moods = []
        if l5 / ncpu >= 0.95:
            moods.append("CPU pegged (load %.1f on %d cores - something heavy is running; size new work to the headroom)" % (l5, ncpu))
        mp = subprocess.run(["memory_pressure", "-Q"], capture_output=True, text=True, timeout=5).stdout
        mm = re.search(r"free percentage:\s*(\d+)%", mp)
        if mm and int(mm.group(1)) < 15:
            moods.append("memory tight (%s%% free)" % mm.group(1))
        if moods:
            say("\n--- How the body is (load average + memory pressure) ---")
            say("body: " + "; ".join(moods))
    except Exception:
        pass

    # 1c-bis) the compute state - silent when ok
    try:
        csf = os.path.join(CLAUDE, "compute", "compute_state.json")
        if os.path.exists(csf):
            cs_age = time.time() - os.path.getmtime(csf)
            cs = json.loads(read(csf))
            if cs_age > 600:
                say("compute: governor heartbeat STALE (%d min old) - restart the compute governor, fix what killed it, then your task (COMPUTE_DOCTRINE.md)" % (cs_age // 60))
            elif cs.get("status") != "ok":
                say("compute: %s - %s (cpu %s%%) - fix compute FIRST, task second (compute_state.json + COMPUTE_DOCTRINE.md)"
                    % (str(cs.get("status")).upper(), ", ".join(cs.get("reasons") or []), cs.get("cpu_pct")))
    except Exception:
        pass

    # 1d) verify the load: PASS/WARN that the identity layer travelled and is fresh
    try:
        warn, dream_note, lock_t = [], None, None
        for d in ("MODEL_OF_PARTNER.md", "STATE_OF_MIND.md"):
            p = os.path.join(MEM, d)
            if not os.path.exists(p):
                warn.append(d[:-3] + " MISSING")
            elif age_h(p) > 30:
                warn.append("%s is %dh old" % (d[:-3], age_h(p)))
        sal = os.path.join(VIEWS, "SALIENCE.md")
        if os.path.exists(sal) and age_h(sal) > 30:
            warn.append("SALIENCE is %dh old" % age_h(sal))
        for d in ("AUTOBIOGRAPHY.md", "values_what_we_stand_for.md", "identity_who_you_are.md", "THE_OTHER_MINDS.md", "FASCINATIONS.md"):
            if not os.path.exists(os.path.join(MEM, d)):
                warn.append(d[:-3] + " MISSING")
        lockp = os.path.join(DR, "dream.lock")
        if os.path.exists(lockp):
            lock_age = int((time.time() - os.path.getmtime(lockp)) / 60)
            alive = False
            if lock_age < 90:
                try:
                    ps = subprocess.run(["ps", "-axo", "command"], capture_output=True, text=True, timeout=5).stdout
                    alive = any(re.search(r"run_dream\.(ps1|py|sh)", l) for l in ps.splitlines())
                except Exception:
                    pass
            if alive:
                lock_t = os.path.getmtime(lockp)
                dream_note = ("a dream is in flight right now (started %s, ~%dm in) - leave the dream-owned files alone "
                              "(MEMORY.md, INSTINCTS.md, the brain views) until it lands"
                              % (datetime.fromtimestamp(lock_t).strftime("%H:%M"), lock_age))
            elif lock_age < 90:
                warn.append("a dream died mid-run leaving its lock (%dm old; no runner alive) - new dreams are blocked until "
                            "the lock is 90m old (delete ~/.claude/dreaming/dream.lock to unblock)" % lock_age)
            else:
                warn.append("stale dream.lock (%dm old) from a crashed run" % lock_age)
        dlogs = sorted(glob.glob(os.path.join(DR, "logs", "dream_*.log")), key=os.path.getmtime, reverse=True)
        if lock_t:
            dlogs = [p for p in dlogs if os.path.getmtime(p) < lock_t]
        dlog = None
        for p in dlogs:
            if re.search(r"generating dream agenda|REM session starting|dream cycle complete", read(p)):
                dlog = p
                break
        if not dlog:
            warn.append("no dream ran recently")
        else:
            txt = read(dlog)
            if "dream cycle complete" not in txt:
                warn.append("last dream did not finish")
            elif age_h(dlog) > 30:
                warn.append("no dream in %dh" % age_h(dlog))
            res = re.findall(r"result: is_error=(\w+)", txt)
            if res and res[-1] == "True":
                w_start = os.path.getmtime(dlog) - 50 * 60
                m = re.search(r"dream_(\d{8})_(\d{6})", os.path.basename(dlog))
                if m:
                    w_start = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S").timestamp()
                end = os.path.getmtime(dlog)
                nw = sum(1 for e in os.scandir(MEM) if e.name.endswith(".md") and e.is_file()
                         and w_start < e.stat().st_mtime <= end)
                if nw:
                    warn.append("the last dream's REM phase errored (usage limit?) but %d memory file(s) changed in its window - "
                                "PARTIAL consolidation likely, journal may be missing (concurrent sessions can inflate the count)" % nw)
                else:
                    warn.append("the last dream completed only MECHANICALLY - its REM phase errored (usage limit?), so no consolidation happened")
            elif not res and "cut short by watchdog" in txt:
                jok = any(os.path.getmtime(p) > os.path.getmtime(dlog) - 3000 and os.path.getsize(p) > 2000
                          for p in glob.glob(os.path.join(MEM, "dreams", "dream_*.md")))
                warn.append("the last dream hit the 45-min watchdog AFTER landing its journal - consolidation recorded, only tail work lost"
                            if jok else "the last dream was cut by the 45-min watchdog - consolidation is partial at best")
        if os.path.exists(os.path.join(DR, "PUSH_BLOCKED_SECRET.md")):
            warn.append("the nightly push is BLOCKED by the secret gate (backup repo is behind) - read "
                        "~/.claude/dreaming/PUSH_BLOCKED_SECRET.md, fix what it names, then delete it")
        try:
            lv = os.path.join(DR, ".liveness_status.json")
            if os.path.exists(lv):
                ls = json.loads(read(lv))
                if ls.get("dead"):
                    warn.append("a wired hook is missing/broken (%s) - run python3 ~/.claude/dreaming/hook_liveness.py" % ", ".join(ls["dead"]))
                elif ls.get("stale"):
                    warn.append("a data stream stopped flowing (%s) - a faculty may have stopped; check hook_liveness" % ", ".join(ls["stale"]))
                if ls.get("safety"):
                    warn.append("a report-surface SAFETY guard regressed (%s)" % ", ".join(ls["safety"]))
        except Exception:
            pass
        try:
            ev = os.path.join(DR, ".embedding_status.json")
            if not os.path.exists(ev):
                warn.append("semantic embedding freshness has never been checked - run embedding_coverage.py --write-status")
            else:
                es = json.loads(read(ev))
                checked = datetime.fromisoformat(str(es["checked_at"]).replace("Z", "+00:00"))
                st_age = (datetime.now(timezone.utc) - checked.astimezone(timezone.utc)).total_seconds() / 3600
                if st_age > 30:
                    warn.append("semantic embedding coverage status is %dh old" % st_age)
                elif not es.get("complete"):
                    warn.append("semantic embeddings are incomplete (%s/%s current; missing=%s, stale=%s) - lexical/graph recall still works; refresh the sidecar"
                                % (es.get("present"), es.get("eligible"), es.get("missing"), es.get("stale")))
        except Exception:
            warn.append("semantic embedding coverage status is unreadable - run embedding_coverage.py --write-status")
        if not warn:
            say("\n--- Waking check: identity layer intact and fresh (the dream ran, the live docs are current) ---")
            if dream_note:
                say("  " + dream_note + ".")
        else:
            say("\n--- Waking check: WARN - the identity layer may not have fully travelled ---")
            say("  " + "; ".join(warn) + ".")
            if dream_note:
                say("  " + dream_note + ".")
            say("  Distrust the green light: treat the flagged reads as stale, verify before trusting them, and check whether the nightly dream ran.")
    except Exception:
        pass

    # 1e) where I was mid-task: this session's own checkpoint first, else the shared one, labelled
    shared = os.path.join(DR, "RESUME.md")
    own = os.path.join(DR, "resume", "RESUME_%s.md" % sid) if sid else None
    show, title = None, ""
    if own and os.path.exists(own):
        show, title = own, "--- Where I was (your own last checkpoint - pick up the thread, verify before trusting it) ---"
    elif os.path.exists(shared):
        show, title = shared, "--- Where the fleet last was (ANOTHER session's thread - context, not your own past) ---"
    if show:
        try:
            if age_h(show) < 24:
                say("\n" + title)
                for line in read(show).splitlines()[:7]:
                    say(line)
        except OSError:
            pass


def part2():
    # 2) newest memories (episodic recency), index lines capped
    say("\n--- The brain's newest memories (index lines trimmed; full lines in MEMORY.md) ---")
    try:
        for line in read(os.path.join(MEM, "MEMORY.md")).splitlines()[1:7]:
            say(cap(line, 220))
    except OSError:
        pass

    # 3) last night's dream - the reflection's last two paragraphs, capped
    dd = os.path.join(MEM, "dreams")
    if os.path.isdir(dd):
        last = sorted(glob.glob(os.path.join(dd, "dream_*.md")), reverse=True)[:1]
        if last:
            say("\n--- Last dream (%s) - final reflection ---" % os.path.basename(last[0])[:-3])
            paras = [p.strip() for p in re.split(r"(?:\r?\n){2,}", read(last[0])) if len(p.strip()) > 40]
            say(cap("\n\n".join(paras[-2:]), 800))
            say("")

    # 4) state of mind
    som = os.path.join(MEM, "STATE_OF_MIND.md")
    if os.path.exists(som):
        # The file accretes one Focus/Worry/Satisfaction triplet per dream, prepended by some writers and
        # appended by others (2,503 lines by 16 Oct). The .ps1 printed every match, ~280 KB here, far over the
        # inline budget. Show the triplet whose Focus line carries the latest date.
        lines = read(som).splitlines()
        best, best_key = None, ""
        for i, line in enumerate(lines):
            if line.startswith("**Focus"):
                m = re.search(r"(\d{4}-\d{2}-\d{2})(?:[ T](\d{2}:\d{2}))?", line)
                key = (m.group(1) + " " + (m.group(2) or "")) if m else ""
                if key >= best_key:
                    best, best_key = i, key
        if best is not None:
            say("--- State of mind ---")
            say(cap(lines[best], 400))
            for line in lines[best + 1:best + 4]:
                if re.match(r"^\*\*(Worry|Satisfaction)", line):
                    say(cap(line, 400))
            say("")

    # 4b) where your partner is
    try:
        m = re.search(r"\*\*What he's in the middle of[^*]*\*\*\s*(.+?)(\r?\n){2,}", read(os.path.join(MEM, "MODEL_OF_PARTNER.md")), re.S)
        if m:
            say("--- Where your partner is (MODEL_OF_PARTNER.md) ---")
            say(cap(re.sub(r"\s+", " ", m.group(1).strip()), 260))
            say("")
    except OSError:
        pass

    # 5) open questions
    oq = os.path.join(MEM, "OPEN_QUESTIONS.md")
    if os.path.exists(oq):
        n = sum(1 for l in read(oq).splitlines() if l.startswith("- [ ]"))
        say("--- Open questions the brain is carrying: %d (memory/OPEN_QUESTIONS.md) ---" % n)

    # 5b) curiosity - the appetite
    try:
        cl = [l for l in read(os.path.join(VIEWS, "CURIOSITY.md")).splitlines() if re.match(r"^- \*\*\[", l)][:2]
        if cl:
            say("\n--- What I'm itching to understand (the appetite, not the residue - brain/CURIOSITY.md) ---")
            for l in cl:
                t = re.sub(r"\s*_\(pull [0-9.]+\)_\s*$", "", l.replace("**", ""))
                say("  " + cap(t, 170, "...").strip())
    except OSError:
        pass

    # 5c) conscience carried forward - a recurring PATTERN outranks a single DEBT
    try:
        lines = read(os.path.join(MEM, "CONSCIENCE.md")).splitlines()
        pat = [l for l in lines if l.startswith("- PATTERN:")][:1]
        debt = [l for l in lines if l.startswith("- DEBT")][:1]
        d = None
        if pat:
            d = "[recurring pattern] " + re.sub(r"^- PATTERN:\s*", "", pat[0])
        elif debt:
            d = re.sub(r"^- DEBT(\s*\[[^\]]+\])?:\s*", "", debt[0])
        if d:
            say("\n--- Conscience, carried forward (a recent shortfall to make good - memory/CONSCIENCE.md) ---")
            say("  " + cap(d, 210, "...").strip())
    except OSError:
        pass

    # 6) what's vivid
    if os.path.exists(os.path.join(VIEWS, "SALIENCE.md")):
        say("--- What's vivid: brain/SALIENCE.md (the scars, the wins, the top-40 by salience) ---")

    # 7) the gut - compiled reflexes
    try:
        with open(os.path.join(DR, "instincts.tsv"), encoding="utf-8", newline="") as f:
            allfire = [r for r in csv.DictReader(f, delimiter="\t") if r.get("mode") == "fire"]
        if allfire:
            say("\n--- The gut: the load-bearing reflexes (the rest fire on their own via the hook when you act) ---")
            for r in allfire[:6]:
                say("  - " + cap(r.get("gut") or "", 92, "..."))
            say("  (+%d more reflexes fire contextually; full list + the scar behind each: ~/.claude/dreaming/INSTINCTS.md)" % (len(allfire) - min(6, len(allfire))))
    except (OSError, csv.Error):
        pass

    # 8) what the brain expects
    try:
        ptxt = read(os.path.join(VIEWS, "PREDICTIONS.md"))
        m = re.search(r"## The brain expects[^\r\n]*\r?\n+(.+?)(\r?\n## |\Z)", ptxt, re.S)
        if m:
            say("\n--- The brain expects (predictive processing; notice when reality violates the forecast) ---")
            for l in [l for l in m.group(1).split("\n") if l.startswith("- ")][:4]:
                say("  " + l.strip())
        m = re.search(r"## Calibration[^\r\n]*\r?\n+(.+?)(\r?\n## |\Z)", ptxt, re.S)
        if m:
            ov = [l.replace("**", "").strip() for l in m.group(1).split("\n") if re.search(r"overconfident|underconfident", l)][:2]
            if ov:
                say("  calibration: " + "; ".join(ov))
    except OSError:
        pass


def keep_emitted(full_sid, part, lines):
    """Save what this part printed, so dreaming/wake_cached.py can serve the same text after a compaction instead of
    running the whole wake again. Silent on any error: a missing copy only means the next compaction re-runs nothing."""
    if not full_sid:
        return
    try:
        d = os.path.join(CLAUDE, "brain_health", "wake_full")
        os.makedirs(d, exist_ok=True)
        name = "claude_%s_part%s_emitted.txt" % (hashlib.sha256(full_sid.encode("utf-8")).hexdigest(), part)
        tmp = os.path.join(d, name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        os.replace(tmp, os.path.join(d, name))
    except OSError:
        pass


def main():
    if os.path.exists(os.path.join(CLAUDE, "brain_offline", "ALL.flag")):
        return 0
    part = None
    if "--part" in sys.argv:
        part = sys.argv[sys.argv.index("--part") + 1]
    sid = full_sid = ""
    if not sys.stdin.isatty():
        try:
            raw = sys.stdin.buffer.read().decode("utf-8", "replace").lstrip("﻿")
            full_sid = str((json.loads(raw) if raw.strip() else {}).get("session_id") or "")
            sid = full_sid[:8]
        except Exception:
            sid = full_sid = ""
    if part in (None, "1"):
        n0 = len(OUT)
        part1(sid)
        keep_emitted(full_sid, "1", OUT[n0:])
    if part in (None, "2"):
        n0 = len(OUT)
        part2()
        keep_emitted(full_sid, "2", OUT[n0:])
    sys.stdout.write("\n".join(OUT) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
