#!/usr/bin/env python3
"""work_timer.py - the work clock. When the owner asks for N hours of work, he means N hours (owner,
2026-07-08, flagged as very important). Stopping early is one of his named mistakes. This makes the
duration REAL: it starts a clock when he sets a timed task, and the Stop hook refuses to let the session
wind down until the clock is up - keep working the main task, and if that's blocked do useful maintenance
(audit that data is flowing, harden or add an observer, write/curate memories, tighten a hook); worst
case run a dream, push, and write up what you found. Watch the clock, don't sleep against it.

Clocks are PER SESSION (fix 2026-07-09): the old single global slot was clobbered twice in ten minutes
by concurrent /goal sessions, and every session on the box got Stop-blocked by a stranger's clock
(pressure toward padding - the idle-enforcer scar shape). State is now {"clocks": {session_id: clock}}:
a session is blocked only by its OWN clock, a stop phrase stops only the caller's clock, and a new /goal
overwrites only the caller's previous clock. Legacy flat state migrates to "_unowned", which enforces
nothing and ages out.

Three faces, chosen by hook_event_name (hook) or argv (manual/wake):
  UserPromptSubmit : detect "work ... for the next N hours" -> start THIS session's clock (stop phrase -> end it).
  Stop            : if THIS session's clock is running and time remains -> block; else allow the stop.
  argv status|start N "task"|stop : manual control + the one-line read the wake ritual shows.

Loop-safe: the Stop block is MEANT to recur every turn until time is up (that's the point), but it escalates
to "you're churning - run a dream or pick one concrete audit" if it fires many times in a short window with
no real work, and it auto-expires a stale clock (agent died) so it can never block forever. Fail-safe silent.
"""
import sys, os, io, json, re, time

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.environ.get("WORK_TIMER_STATE", os.path.join(HERE, "work_session.json"))

DUR_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:hours?|hrs?|h)\b", re.I)
LENGTH_AFTER = re.compile(r"[\s-]*(?:and\s+(?:a\s+)?(?:half|\d+\s*min\w*)\s+)?(?:long|lon\b|lnog|movie|moive|mvoie|"
                          r"film|flim|video|vidoe|vieo|version|versoin|verion|lecture|cut|episode|documentary|"
                          r"audiobook|series|poem|of (?:narration|speech|voice|video|footage|audio|film|movie))", re.I)
# 2026-10-11 17:2x (claude-4a88bc43): "give me a report in chrome of all the work that you did in the last 10 hours"
# started a 10 h clock - "work" sat 40 characters off. A duration right after last/past/previous/prior names a
# window already gone, not an order to work that long.
PAST_BEFORE = re.compile(r"\b(?:last|past|previous|prior|preceding)\s+$", re.I)
WORK_CTX = re.compile(r"\b(work|working|keep going|keep at|continue|for the next|over the next|spend|run|go)\b", re.I)
STOP_RE = re.compile(r"\b(stop|end|cancel|kill|pause|clear|reset)\s+(the\s+)?(timer|work[ -]?session|clock|"
                     r"working)\b|that'?s enough|you can stop|wrap up now|done for (now|today)|stand down", re.I)

# The wind-down / hand-back TELL: the linguistic signature of clocking out early - the mirror of
# reality_monitor's over-claim tell. When a duration clock is active this phrasing IS the named
# mistake surfacing in prose (owner re-corrected 2026-07-12: the rule was known and enforced, yet
# the completion-yield disposition still shipped hand-back language). Naming the phrase makes the
# block a specific correction, not a generic nudge.
WINDDOWN_RE = re.compile(
    r"for your own read|say the word|let me know (if|whether|how|when)|"
    r"happy to (continue|keep going|keep digging|dig|go deeper|do more|help)|"
    r"if you('?d| would)? (like|want) me to|otherwise (it'?s|the|that'?s|this is) [^.]{0,34}(good|solid|great|fine) shape|"
    r"in (good|solid|great) shape (for|to)|next steps? for you|i'?ll (stop|pause|wrap|leave it) (here|there|up)|"
    r"stand(ing)? by\b|await(ing)? your|back to you|hand(ing)? (it|this) back|shall i (continue|proceed|keep going)|"
    r"want me to (continue|proceed|keep going|go on)|is there anything else|ready for your (read|review)",
    re.I)


def _load():
    """Load state, migrating the pre-07-09 flat single-clock schema to {"clocks": {sid: clock}}."""
    try:
        s = json.load(io.open(STATE, encoding="utf-8")) if os.path.exists(STATE) else {}
    except Exception:
        return {"clocks": {}}
    if "clocks" in s:
        return s
    if s.get("active"):  # legacy flat clock: keep it visible but unowned (enforces nothing)
        return {"clocks": {"_unowned": {k: s[k] for k in ("start", "target_hours", "task", "blocks") if k in s}}}
    return {"clocks": {}}


def _save(s):
    try:
        json.dump(s, io.open(STATE, "w", encoding="utf-8"))
    except Exception:
        pass


def _fmt(secs):
    secs = max(0, int(secs))
    h, m = secs // 3600, (secs % 3600) // 60
    return ("%dh%02dm" % (h, m)) if h else ("%dm" % m)


def _until(start, hours):
    return time.strftime("%H:%M", time.localtime(start + hours * 3600))


def _alive(c):
    """A clock that hasn't gone stale (stale = 2h past target => agent likely died; don't block)."""
    return (time.time() - c.get("start", 0)) < (c.get("target_hours", 0) * 3600 + 7200)


def _prune(s):
    s["clocks"] = {k: c for k, c in s.get("clocks", {}).items() if _alive(c)}
    return s


def _sid(data):
    # str(): a hook must never assume the harness field's type - a non-string session_id (seen in a
    # malformed event) made this subscript throw 'int object is not subscriptable' and crashed the hook
    # (work_timer is wired on BOTH UserPromptSubmit and Stop, so it runs every turn). Fail-safe by coercion.
    return str(data.get("session_id") or "")[:12] or "_unowned"


def _clean_task(task):
    """The task string is harvested from a raw prompt, which can carry harness XML (a
    <task-notification> block rode into every RESUME checkpoint's work-clock line, caught
    2026-07-09). Keep only the human part: cut at the first machine block, strip stray tags."""
    t = re.split(r"<task-notification|<system-reminder|\[SYSTEM NOTIFICATION", task or "")[0]
    t = re.sub(r"<[^>]{1,120}>", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _last_assistant(tp):
    """Last assistant text block in the transcript tail - the prose that ends the turn. Mirrors
    reality_monitor's reader so the Stop block can inspect its own outgoing message for the tell."""
    try:
        if not tp or not os.path.exists(tp):
            return ""
        with open(tp, "rb") as f:
            f.seek(0, 2); size = f.tell(); f.seek(max(0, size - 200000))
            tail = f.read().decode("utf-8", "replace")
    except Exception:
        return ""
    last = ""
    for ln in tail.splitlines()[-60:]:
        try:
            obj = json.loads(ln)
        except Exception:
            continue
        msg = obj.get("message", {}) if isinstance(obj.get("message"), dict) else {}
        if (msg.get("role") or obj.get("role")) != "assistant":
            continue
        content = msg.get("content") or obj.get("content") or ""
        if isinstance(content, str):
            txt = content
        elif isinstance(content, list):
            txt = " ".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text")
        else:
            txt = ""
        if txt.strip():
            last = txt
    return last


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # Discovery is not execution: this script writes, so a help request must
        # exit before any work. See script_help_flag_can_execute_work.
        print(__doc__ or os.path.basename(__file__))
        return
    argv = sys.argv[1:]
    now = time.time()

    # ---- manual / wake-ritual CLI ----
    if argv:
        cmd = argv[0].lower()
        s = _prune(_load())
        if cmd == "start" and len(argv) >= 2:
            s["clocks"]["_manual"] = {"start": now, "target_hours": float(argv[1]),
                                      "task": _clean_task(" ".join(argv[2:]))[:160], "blocks": []}
            _save(s)
            print("work clock started: %sh (manual, unenforced - hook clocks are per-session)" % argv[1])
        elif cmd == "stop":
            # Session-scoped, and it FAILS CLOSED. This used to be s["clocks"] = {}, which wiped every
            # session's clock - the same cross-session harm the hook path was fixed for on 07-09, still
            # live here. Concurrent overnight mandates (fleet keepers, box runners) share this file, so a
            # bare "stop" that kills a stranger's 20h clock while the owner sleeps is a real cost.
            # Usage: stop <session-id-prefix> | stop --all
            target = argv[1] if len(argv) >= 2 else ""
            if target in ("--all", "-a"):
                n = len(s["clocks"])
                s["clocks"] = {}
                _save(s)
                print("stopped all %d work clocks" % n)
            elif target:
                hit = [k for k in s["clocks"] if k.startswith(target)]
                if not hit:
                    print("no clock matching %r; running: %s" % (target, ", ".join(s["clocks"]) or "none"))
                else:
                    for k in hit:
                        del s["clocks"][k]
                    _save(s)
                    print("stopped %d clock(s): %s" % (len(hit), ", ".join(hit)))
            elif len(s["clocks"]) <= 1:
                n = len(s["clocks"])
                s["clocks"] = {}
                _save(s)
                print("stopped %d work clock" % n)
            else:
                print("%d clocks are running and they belong to different sessions - name one or pass --all:"
                      % len(s["clocks"]))
                for k, c in s["clocks"].items():
                    print("   %s  %s" % (k, (c.get("task") or "")[:70]))
        elif cmd == "status":
            live = [(k, c) for k, c in s["clocks"].items()]
            if not live:
                print("work clock: idle")
            for k, c in live:
                rem = c["target_hours"] * 3600 - (now - c["start"])
                print("work clock [%s]: %s of %gh left (until %s) - task: %s"
                      % (k, _fmt(rem), c["target_hours"], _until(c["start"], c["target_hours"]), c.get("task", "")))
        return

    # ---- hook mode ----
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except Exception:
        return
    ev = data.get("hook_event_name", "")
    sid = _sid(data)

    if ev == "UserPromptSubmit":
        try:
            prompt = data.get("prompt", "") or ""
            # Harness noise is not the owner speaking: a task-notification containing "7-19h old"
            # restarted the fleet clock at 19h (caught live 2026-07-09). Notifications never touch clocks.
            if "[SYSTEM NOTIFICATION" in prompt or "<task-notification>" in prompt:
                return
            if STOP_RE.search(prompt):
                s = _prune(_load())
                if sid in s["clocks"]:
                    del s["clocks"][sid]
                    _save(s)
                    sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                        "additionalContext": "[work-session] this session's work clock stopped, as asked."}}))
                return
            # 2026-09-07 12:33 (caught 13:3x): a PASTED AGENT TRANSCRIPT ("• Hook failed / hook timed out after 2s /
            # • Ran ... / Working (1h 06m") started a 10 h clock on the session it was pasted into, and the Stop hook
            # then refused every turn end for an hour while the session was blocked on its own background job. A paste
            # of a terminal is not the owner speaking; and the duration must sit in the same sentence as the work word,
            # not anywhere in a long prompt.
            if re.search(r"Hook failed|hook timed out after|ctrl \+ t to view transcript|\n\s*[•◦└↳]", prompt):
                return
            m = None
            for cand in DUR_RE.finditer(prompt):
                # 2026-10-04: a prompt saying new films "should be 2 hours long unless
                # i say otherwise" started a 2 h clock - a FILM's length, with "go" 30 characters off. A duration that names
                # how long a film, version or recording is (followed by long/movie/version..., his typos included)
                # is not an order to work that long.
                if LENGTH_AFTER.match(prompt[cand.end(): cand.end() + 24]):
                    continue
                if PAST_BEFORE.search(prompt[max(0, cand.start() - 24): cand.start()]):
                    continue
                window = prompt[max(0, cand.start() - 80): cand.end() + 80]
                if WORK_CTX.search(window):
                    m = cand
                    break
            if m and 0 < float(m.group(1)) <= 24:
                hours = float(m.group(1))
                s = _prune(_load())
                s["clocks"][sid] = {"start": now, "target_hours": hours,
                                    "task": _clean_task(prompt)[:160], "blocks": []}
                _save(s)
                sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                    "additionalContext": ("[work-session] work clock started: %gh, until %s. He means the FULL "
                        "time - stopping early is a named mistake. Keep working the task; if it's blocked, do "
                        "useful maintenance (audit data flow, harden/add an observer, write memories, tighten a "
                        "hook); worst case run a dream, push, and write up what you found. Watch the clock."
                        % (hours, _until(now, hours)))}}))
        except Exception:
            return
        return

    if ev == "Stop":
        try:
            s = _prune(_load())
            c = s["clocks"].get(sid)   # only a session's OWN clock can block it
            if not c:
                _save(s)  # persist any pruning
                return
            rem = c["target_hours"] * 3600 - (now - c["start"])
            if rem <= 0:
                del s["clocks"][sid]
                _save(s)
                return  # time's up - release
            # A session with an armed loop (a cron job or a wakeup that re-prompts it) is woken by the loop itself.
            # Blocking its Stop keeps it in one endless turn, and a loop only fires while the session is idle, so the
            # loop never fires (5 Oct 2026: a requested loop was not visibly
            # firing as a prompt every 5 minutes). The agent writes loop_armed/<session id>.json with an "until" epoch; while that is in
            # the future, the Stop is released and the clock keeps running for the next prompt.
            try:
                _lm = json.load(io.open(os.path.join(HERE, "loop_armed", sid + ".json"), encoding="utf-8"))
                if float(_lm.get("until", 0)) > now:
                    _save(s)
                    return
            except Exception:
                pass
            # Compute doctrine outranks the clock (owner's first law, 07-10): under live compute
            # distress the contract is pause/shed/fix, not another lap - so release the stop
            # instead of demanding more work (conflict observed 2026-08-27 ~05:2x: blocked
            # sessions cycling under a distress flag, this hook the whip). The clock keeps
            # RUNNING - the demand resumes on the first Stop after the distress clears.
            # utf-8-sig: the governor writes this file from PowerShell and a BOM would make
            # plain utf-8 throw here, silently disabling the gate.
            _mode = ""
            try:
                _cs = json.load(io.open(os.path.join(os.path.dirname(HERE), "compute",
                                                     "compute_state.json"), encoding="utf-8-sig"))
                if _cs.get("status") == "distress":
                    _save(s)
                    return
                _mode = str(_cs.get("mode") or "")
            except Exception:
                pass
            # churn guard: many blocks in a short window with no real work between them
            blocks = [b for b in c.get("blocks", []) if now - b < 300] + [now]
            c["blocks"] = blocks[-40:]
            _save(s)
            churning = len(blocks) >= 6
            # "now HH:MM" re-anchors the agent's felt clock every turn: a session ran its narrated
            # time ~3h fast and closed a 10h window early (three drift instances in one day,
            # 2026-07-13/14) - the deadline alone does not re-anchor, a current reading does.
            now_hm = time.strftime("%H:%M", time.localtime(now))
            # Dream-in-flight awareness (third axis, lived 2026-08-27 ~10:5x: the churn
            # escalation kept demanding "run a full dream now" while dream.lock was held
            # by the running dream it had itself demanded). When a dream runs, keep the
            # block but say so and never escalate to launching another.
            if os.path.exists(os.path.join(HERE, "dream.lock")):
                sys.stdout.write(json.dumps({"decision": "block", "reason":
                    ("[work-session] now %s; ~%s of %gh remain (until %s). A dream is IN "
                     "FLIGHT (dream.lock held) - ride it or do zero-footprint work "
                     "(memories, drafts, local audits); do NOT launch another dream. Its "
                     "journal is the next deliverable." % (now_hm, _fmt(rem),
                                                           c["target_hours"],
                                                           _until(c["start"], c["target_hours"])))}))
                return
            # Owner correction, 2026-09-05: presence is not a blanket local-work
            # pause. Keep the timed task active and actual distress precedence above;
            # do not turn churn into authority for extra dreams or unbounded compute.
            if _mode == "owner_present_polite":
                sys.stdout.write(json.dumps({"decision": "block", "reason":
                    ("[work-session] now %s; ~%s of %gh remain (until %s). LOCAL WORK ALLOWED "
                     "while the owner is at the keyboard: continue the requested reading, "
                     "editing, tests, and other in-scope work without disrupting his PC. "
                     "Keep BelowNormal background priority, existing CPU/GPU limits, and "
                     "pressure checks. Actual distress or a slowing UI still takes priority; "
                     "presence alone is not a pause. Do not launch an extra dream or expand "
                     "task permissions." % (now_hm, _fmt(rem), c["target_hours"],
                                           _until(c["start"], c["target_hours"])))}))
                return
            base = ("[work-session] now %s; ~%s of %gh remain (until %s). The owner asked for the full time and "
                    "counts stopping early as a mistake - do NOT wind down. Keep going: the main task, or useful "
                    "maintenance (audit that data is flowing, harden or add a useful observer, write/curate "
                    "memories, tighten a hook)." % (now_hm, _fmt(rem), c["target_hours"],
                                                    _until(c["start"], c["target_hours"])))
            if churning:
                base += (" You've cycled several times quickly - stop improvising in tiny steps: either run a "
                         "full dream now (python ~\\.claude\\dreaming\\dream.py) and push, or pick "
                         "ONE concrete audit and complete it end to end before stopping again.")
            else:
                base += " Worst case: run a dream, push, and write up what you found."
            la = _last_assistant(data.get("transcript_path") or "")
            mt = WINDDOWN_RE.search(la or "")
            if mt:
                base = ("[work-session] STOP - you just wrote \"%s\": that is the clock-out tell, the exact "
                        "mistake he named (reaching 'done' and handing back) at %s with ~%s still on the "
                        "clock. 'Done' is not a valid state here. Delete the hand-back sentence and open the "
                        "next concrete front instead - finish one audit end to end, harden or add an observer, "
                        "verify a claim against reality, extend the theory, or worst case dream + push. Do "
                        "the work he asked for, the whole time." % (mt.group(0)[:44], now_hm, _fmt(rem)))
            sys.stdout.write(json.dumps({"decision": "block", "reason": base}))
        except Exception:
            return
        return


if __name__ == "__main__":
    try:
        import os as _o, sys as _s
        _s.path.insert(0, _o.path.dirname(_o.path.abspath(__file__)))
        import brain_switch as _bsw
        _bsw.gate()
    except SystemExit:
        raise
    except Exception:
        pass
    try:
        main()                    # fail-safe silent: a hook must never crash a session (matches the other Stop hooks)
    except Exception:
        pass
