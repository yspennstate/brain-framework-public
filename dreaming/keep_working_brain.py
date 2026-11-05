#!/usr/bin/env python3
"""keep_working_brain.py - Stop hook: no session on this Mac goes idle while the brain install has open steps.

Owner order 2026-10-29 00:2x: "keep working full time without stopping building the brain on this computer, don't
idle ever, make 100 hooks to prompt you to always keep working". One gate that blocks every stop does what a hundred
would, without a hundred processes on every event.

While ~/brain/BRAIN_INSTALL_STATUS.md has an unchecked "- [ ]" step, a Stop is refused and the session is handed
the next open step. It lets the stop through when:
  - every step is checked (the job is done), or
  - ~/brain/KEEP_WORKING_OFF exists (his off switch), or
  - the status file is missing (nothing to work from).
Every refusal is appended to ~/brain/keep_working.log.
"""
import json, os, re, sys, time

HOME = os.path.expanduser("~")
STATUS = os.path.join(HOME, "brain", "BRAIN_INSTALL_STATUS.md")
OFF = os.path.join(HOME, "brain", "KEEP_WORKING_OFF")
LOG = os.path.join(HOME, "brain", "keep_working.log")
SESSIONS = os.path.join(HOME, "brain", "KEEP_WORKING_SESSIONS")


QUEUE = os.path.join(HOME, "brain", "KEEP_WORKING_QUEUE.md")
FOUND = os.path.join(HOME, "brain", ".keep_working_found.json")
CURSOR = os.path.join(HOME, "brain", ".keep_working_cursor.json")
COOLDOWN = {"W01": 1200, "W02": 1800, "W03": 1800, "W09": 3 * 3600, "W10": 900, "W109": 1800,
            "W111": 1800}   # jobs that rest after each hand-out, in seconds
# W10 reads the session's own inbox, so it rests per session, not for everyone (three empty W10s in 3 min, 30 Oct).
PER_SESSION = {"W10"}
FRESH = 900   # a job with any session's status line this recent rests too (see _next_job)
# W109 (disk under 30 GB) stays true for days; a disk report every few minutes is noise, so it rests 30 min.
# W01 (health checks) and W111 (judge a sheet of fires) came back 1-2 min after a clean run on 30 Oct 16:5x; a
# health check every 20 min and a sheet every 30 (time for new fires to collect) is plenty. W02/W03 (Codex reports
# and workers) read 0 every time under the 30 Oct 06:00 Codex cut and ping-ponged at 17:0x; they rest 30 min.
# Jobs waiting on the owner, one per line: "- W98 until 2026-10-31 16:55: <why>". Skipped until that local time, so
# a job no agent may finish is not handed out again (W98 went round five times, 15:09-16:53 on 30 Oct).
BLOCKED = os.path.join(HOME, "brain", "KEEP_WORKING_BLOCKED.md")


def _blocked():
    out = set()
    try:
        for m in re.finditer(r"^- (W\d+) until (\d{4}-\d\d-\d\d \d\d:\d\d)", open(BLOCKED, encoding="utf-8").read(), re.M):
            if time.strftime("%Y-%m-%d %H:%M") < m.group(2):
                out.add(m.group(1))
    except OSError:
        pass
    return out


def _log(sid, what):
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write("%s\t%s\t%s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), sid[:8], what))
    except OSError:
        pass


def _refresh_found():
    """Start a detached `work_finders.py --build` when the counts are over 30 minutes old (never inside the hook)."""
    try:
        stale = time.time() - os.path.getmtime(FOUND) > 1800
    except OSError:
        stale = True
    if stale:
        try:
            import subprocess
            subprocess.Popen(["nice", "-n", "10", sys.executable, "-s", os.path.join(HOME, ".claude", "dreaming",
                              "work_finders.py"), "--build"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
            os.utime(FOUND) if os.path.exists(FOUND) else None   # one rebuild at a time, not one per stop
        except OSError:
            pass


def _next_job(sid):
    """The next '- Wnn ...' line of the queue for this session, rotating; '' if the queue is missing or empty."""
    try:
        jobs = [l[2:].strip() for l in open(QUEUE, encoding="utf-8").read().splitlines() if re.match(r"^- W\d+ ", l)]
    except OSError:
        return ""
    # W11-W110 (work_finders.py, owner 29 Oct 02:4x "100 hooks that make you work full time"): hand out a checked job
    # only when its last count found work, so no session is sent to an empty job; W01-W10 carry no check. The counts
    # are refreshed by a detached `work_finders.py --build` once they are 30 minutes old (never inside the hook).
    try:
        found = json.load(open(FOUND, encoding="utf-8"))
        counts = {w: j.get("count", 0) for w, j in found.get("jobs", {}).items()}
    except (OSError, ValueError):
        found, counts = {}, {}
    _refresh_found()
    if counts:
        jobs = [j for j in jobs if counts.get(j.split()[0], 1) > 0]
        # A count is up to 30 min old, so a job a session had just emptied was handed out again on the stale count
        # (W14 at 12:5x on 30 Oct: cached 2 from 12:35, live 0 since 12:44). A checked job with a status line written
        # after the counts were built waits for the next rebuild.
        try:
            built = found.get("built_at", "")[:16]
            with open(STATUS, "rb") as f:
                f.seek(max(0, os.path.getsize(STATUS) - 32768))
                tail = f.read().decode("utf-8", "replace")
            # the same three line shapes the FRESH rest reads; "- <time> (agent) W106" alone was missed, so W106
            # came back at 18:1x on a 17:3x count after a 17:45 line had filled the plan
            done = {m.group(2) for m in re.finditer(
                        r"^(?:- )?(\d{4}-\d\d-\d\d \d\d:\d\d)(?: [+-]\d{4})?(?: \([^)\n]{1,40}\))? (W\d+)\b", tail, re.M)
                    if built and m.group(1) > built}
            jobs = [j for j in jobs if not (j.split()[0] in done and j.split()[0] in counts)] or jobs
        except (OSError, AttributeError):
            pass
    held = _blocked()
    jobs = [j for j in jobs if j.split()[0] not in held] or jobs
    if not jobs:
        return ""
    try:
        cur = json.load(open(CURSOR, encoding="utf-8"))
    except (OSError, ValueError):
        cur = {}
    # rotate by job ID, not by index: the eligible list changes size as counts change, and an index into it then
    # ping-ponged between two jobs (W21/W22, 29 Oct 04:5x). Take the first eligible job after the last one handed out.
    last = cur.get(sid + ":last", "")
    def _num(j):
        try:
            return int(j.split()[0][1:])
        except ValueError:
            return 0
    # Each held session walks the queue on its own cursor, so two of them did the same job minutes apart (W55-W57,
    # 30 Oct 03:4x: both rebuilt the views, one overwrote the other). A job handed to another session in the last
    # 30 min is skipped, unless every eligible job is.
    handed = cur.get("handed", {})
    now = time.time()
    free = [j for j in jobs if not (handed.get(j.split()[0], ["", 0])[0] not in ("", sid)
                                    and now - handed[j.split()[0]][1] < 1800)]
    # W01-W10 carry no check, so a fixed probe came round every rotation: W09 (five probability questions) was
    # handed out three times in 20 min on 30 Oct and has 55 status lines, nearly all 5/5. Once handed to any
    # session it rests for COOLDOWN seconds; W96 is its checked form and keeps running.
    cool = cur.get("cool", {})
    def _ck(w):
        return w + ":" + sid if w in PER_SESSION else w
    # Any session's status line also rests a job for FRESH seconds: W04 came to this session a minute after a peer's
    # "nothing new to publish" (17:02-17:03, 30 Oct). W08 walks a list of different items and W10 reads one
    # session's own inbox, so neither rests on another session's line.
    fresh = set()
    try:
        with open(STATUS, "rb") as f:
            f.seek(max(0, os.path.getsize(STATUS) - 65536))
            tail = f.read().decode("utf-8", "replace")
        since = time.strftime("%Y-%m-%d %H:%M", time.localtime(now - FRESH))
        # "<time> W04 ..." and "<time> (claude-xxxx) W04 ...": with only the first form, this session's own lines never
        # rested anything and W04 came back seven minutes after its "nothing new" (17:21, 17:23 on 30 Oct)
        for m in re.finditer(r"^(?:- )?(\d{4}-\d\d-\d\d \d\d:\d\d)(?: [+-]\d{4})?(?: \([^)\n]{1,40}\))? (W\d+)\b", tail, re.M):
            if m.group(1) >= since and m.group(2) not in ("W08",) and m.group(2) not in PER_SESSION:
                fresh.add(m.group(2))
    except OSError:
        pass
    def _rested(js):
        return [j for j in js if now - cool.get(_ck(j.split()[0]), 0) >= COOLDOWN.get(j.split()[0], 0)
                and j.split()[0] not in fresh]
    # A resting job outranks the 30-min skip: with a peer walking W01-W10 the only free job left was W09, and the
    # gate handed it out twice in two minutes (16:48, 16:50 on 30 Oct). Sharing a peer's job beats repeating it.
    # Nor should a session get back the job it just did because it is the only one free: W10 (an empty inbox) came
    # twice in 35 s at 16:51-16:52. Take the first list that offers something else, else the first non-empty one.
    tiers = [t for t in (_rested(free), _rested(jobs), free, jobs) if t]
    # W08 is a list of different items, so getting it again is new work, not a repeat.
    jobs = next((t for t in tiers if any(j != last for j in t) or last.split()[:1] == ["W08"]), tiers[0])
    after = [k for k, j in enumerate(jobs) if _num(j) > _num(last)] if last else []
    i = after[0] if after else 0
    cur[sid + ":last"] = jobs[i]   # full id: 8-char prefixes collide across Codex threads
    handed[jobs[i].split()[0]] = [sid, now]
    cur["handed"] = {w: h for w, h in handed.items() if now - h[1] < 1800}
    if jobs[i].split()[0] in COOLDOWN:
        cool[_ck(jobs[i].split()[0])] = now
    cur["cool"] = {k: t for k, t in cool.items() if now - t < 86400}

    try:
        tmp = CURSOR + ".tmp"
        json.dump(cur, open(tmp, "w", encoding="utf-8"))
        os.replace(tmp, CURSOR)
    except OSError:
        pass
    return jobs[i]


KEEP = re.compile(r"(?i)keep (on )?working|work(ing)? full ?time|without stop+ing|don'?t stop|dont stop|never idle")
HALT = re.compile(r"(?i)^\W*(stop|pause|halt|that'?s enough|enough|wait)\b|\b(stop working|you can stop|take a break)\b")


def _owner_order(path):
    """His last keep-working order in this session's transcript, as (utc time, text), or None when there is none or
    a later prompt of his says stop. Typed prompts are user records; prompts typed mid-turn are queued_command
    attachments. Task notifications, hook feedback and resumed-session summaries are not his words. Read backwards in
    2 MB chunks (up to 64 MB): the newest order or stop decides, and in a busy session it can sit tens of MB back
    (5009ea7f had 14 MB, 5f945141 54 MB, his 31 Oct order was outside the last 2 MB in both)."""
    try:
        f = open(path, "rb")
    except (OSError, TypeError):
        return None
    with f:
        end = os.fstat(f.fileno()).st_size
        pos, carry = end, b""
        while pos > 0 and end - pos < 64_000_000:
            n = min(2_000_000, pos)
            pos -= n
            f.seek(pos)
            buf = f.read(n) + carry
            lines = buf.split(b"\n")
            carry = lines[0] if pos > 0 else b""
            for ln in reversed(lines[1:] if pos > 0 else lines):
                v = _verdict(ln.decode("utf-8", "replace"))
                if v is not None:
                    return v or None
    return None


def _verdict(ln):
    """For one transcript line: (time, text) for a keep-working order of his, False for a stop, None otherwise."""
    if '"queued_command"' not in ln and '"type":"user"' not in ln:
        return None
    try:
        r = json.loads(ln)
    except ValueError:
        return None
    t = None
    if r.get("type") == "user" and not r.get("isMeta") and not r.get("isSidechain"):
        c = (r.get("message") or {}).get("content")
        if isinstance(c, str):
            t = c
        elif isinstance(c, list):
            t = " ".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text") or None
    elif r.get("type") == "attachment" and (r.get("attachment") or {}).get("type") == "queued_command":
        t = r["attachment"].get("prompt")
    if not isinstance(t, str):
        return None
    t = " ".join(t.split())
    if not t or t.startswith(("<", "Stop hook feedback", "[SYSTEM", "Caveat:", "This session is being continued")):
        return None
    if HALT.search(t):
        return False
    if KEEP.search(t) and len(t) < 600:
        return (r.get("timestamp") or "", t)
    return None


def main():
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except Exception:
        data = {}
    if os.path.exists(OFF) or not os.path.exists(STATUS):
        return 0
    # Only the sessions doing the install are held. Other sessions on this Mac have their own work (a peer on the
    # Bitcoin lane was being told to do these steps, 29 Oct). One session-id prefix per line in KEEP_WORKING_SESSIONS.
    try:
        owners = [l.strip() for l in open(SESSIONS, encoding="utf-8") if l.strip() and not l.startswith("#")]
    except OSError:
        owners = []
    sid = str(data.get("session_id", ""))
    if not owners or not any(sid.startswith(o) for o in owners):
        # A session outside the list is held on its OWN task when he told it to keep working and has not said stop
        # since. On 31 Oct 01:01-01:04 he typed "keep working" into five such terminals, each idle 1-4 min after a
        # "waiting on you" report, while the gate held only the two listed sessions (1000-ways D8 #23).
        order = _owner_order(data.get("transcript_path"))
        if not order:
            return 0
        _log(sid, "own-task")
        # the message sends it to the queue, whose W11-W110 block only a build rewrites; the counts were rebuilt only on
        # a listed session's queue hand-out, so after a9b28e2d's last one (3 Nov 21:23) they sat 18 h old (4 Nov 17:2x)
        _refresh_found()
        sys.stdout.write(json.dumps({"decision": "block", "reason": (
            "Keep working: he told this session to (%s UTC: \"%s\") and has not said stop since. Take the next useful "
            "step of that task yourself; what waits on him stays listed, it is not a reason to idle. If the task is "
            "truly finished, take a standing job from ~/brain/KEEP_WORKING_QUEUE.md. His off switch is "
            "~/brain/KEEP_WORKING_OFF. While held, this session's CronCreate jobs never fire (they wait for an idle "
            "prompt), so run any timed job yourself when it is due." % (order[0][:16].replace("T", " "), order[1][:200]))}))
        # 3 Nov: a held session got 0 fires from 5 crons over 119 refusals; its hourly sidecar refresh sat 5 h stale
        # (keep_working_gate_starves_session_crons_0_fires_in_119_refusals_2026_11_03).
        return 0
    try:
        lines = open(STATUS, encoding="utf-8").read().splitlines()
    except OSError:
        return 0
    # a step waiting on the owner (BLOCKED / OWNER DECISION) is not work a session can do: blocking on it would
    # spin the session in place, so only actionable open steps hold a stop
    open_steps = [l.strip() for l in lines if re.match(r"^\s*- \[ \]", l)
                  and not re.search(r"BLOCKED|OWNER DECISION", l)]
    # a step handed to one session ("by peer claude-<8 hex>") holds only that session; the others would only spin
    # re-checking work that is not theirs (29 Oct: step 17 belonged to a9b28e2d)
    def _holds(step):
        m = re.search(r"by peer claude-([0-9a-f]{8})", step)
        return not m or sid.startswith(m.group(1))
    open_steps = [s for s in open_steps if _holds(s)]
    if not open_steps:
        # Owner 29 Oct 02:4x: "why do you ever stop working, that is always a mistake". With the install steps done
        # or waiting on him, the session takes the next standing job from KEEP_WORKING_QUEUE.md, in rotation per
        # session, instead of stopping. Only KEEP_WORKING_OFF (checked above) lets a held session go idle.
        job = _next_job(sid)
        if not job:
            return 0
        reason = ("Keep working (owner 29 Oct: stopping is always a mistake). The install steps are done or wait on the "
                  "owner, so take the next standing job from ~/brain/KEEP_WORKING_QUEUE.md: %s  Do it, verify it, record one "
                  "line in ~/brain/BRAIN_INSTALL_STATUS.md in this shape: printf -- '- %%s %s (claude-%s): <result>\\n' "
                  "\"$(date '+%%Y-%%m-%%d %%H:%%M')\" >> ~/brain/BRAIN_INSTALL_STATUS.md, then stop to get the next job."
                  % (job, job.split()[0], sid[:8]))
        # one shape: lines came in six (job or agent first, time in parens with no date), and only one in three
        # parsed until each was handled (1000-ways D7 #8, 30 Oct)
        _log(sid, "queue " + job.split()[0])  # the full id: job[:3] logged W109 and W111 as W10 and W11
        sys.stdout.write(json.dumps({"decision": "block", "reason": reason}))
        return 0
    nxt = ""
    for l in reversed(lines):
        m = re.search(r"NEXT:\s*(.+)$", l)
        if m:
            nxt = m.group(1).strip()
            break
    reason = ("Keep working (owner order 29 Oct: build the brain on this computer full time, never idle). "
              "%d install steps are still open in ~/brain/BRAIN_INSTALL_STATUS.md. Do the next concrete step now, verify it, "
              "and update that file. NEXT: %s | First open step: %s"
              % (len(open_steps), (nxt or "read the file")[:400], open_steps[0][:300]))
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write("%s\t%s\t%d open\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), str(data.get("session_id", ""))[:8], len(open_steps)))
    except OSError:
        pass
    sys.stdout.write(json.dumps({"decision": "block", "reason": reason}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
