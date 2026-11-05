#!/usr/bin/env python3
"""resume_checkpoint.py - a cross-reset breadcrumb (Altmann & Trafton, "memory for goals": a suspended
goal's activation decays across an interruption, and resumption is far cheaper when you left an
environmental pointer back into it).

Written on Stop, read first by the wake ritual, so a session that got compacted, crashed, or is brand
new picks up the thread it was mid-way through instead of cold-starting. Records the active work clock
(if any) and the gist of the last turn. Overwrites each turn - it's always the latest checkpoint.

Lives in the dreaming STATE dir, not the memory corpus, so the dream / salience / index never treat it
as a memory. Never blocks (writes a file, emits nothing). Fail-safe silent.
"""
import sys, os, io, json, re, time

HOME = os.path.expanduser("~")
DREAMING = os.path.dirname(os.path.abspath(__file__))
# RESUME_STATE_DIR: test seam (mirrors WORK_TIMER_STATE in work_timer.py). A fixture run without it
# overwrites the fleet's live RESUME.md - which happened 2026-07-09; point tests here instead.
STATE_DIR = os.environ.get("RESUME_STATE_DIR", DREAMING)
OUT = os.path.join(STATE_DIR, "RESUME.md")
INTENTIONS = os.path.join(DREAMING, "INTENTIONS.md")

# A DEFERRED commitment (Gollwitzer implementation intentions / Einstein-McDaniel prospective memory):
# a promise to do something LATER. High precision on purpose - "I'll show you the output" now is not a
# deferral; "I'll wire the cue-matcher next session" is. Requires a commit verb + a later-marker, or an
# explicit TODO / next-step / follow-up phrase. Harvested here (Stop) so the intention outlives the turn.
DEFER_RE = re.compile(
    r"\bTODO\b|\bstill\s+need\s+to\b|\bnext\s+step\b|\bfollow[- ]?up\b|\bcome\s+back\s+to\b"
    r"|\bleav\w+\b[^.!?\n]{0,30}\bnext\b"
    r"|\b(?:i'?ll|we'?ll|i\s+will|we\s+will|i\s+should|we\s+should|i\s+want\s+to)\b[^.!?\n]{0,90}?"
    r"\b(?:next\s+session|next\s+time|later|tomorrow|afterwards?|down\s+the\s+line|eventually|soon"
    r"|once\s+\w|when\s+\w)\b", re.I)


def _last_assistant(tp):
    # Scan the WHOLE transcript for the last assistant TEXT message. A fixed 200KB tail + splitlines()[-80:]
    # MISSES the true last message on a long session: when big tool outputs / system reminders pile up AFTER
    # it, the window returns an older message = a STALE "where I was" breadcrumb (the one thing a resume must
    # never be). Reproduced 2026-07-11 on a 7MB transcript (the fleet registry twin got a message 3 turns
    # old). Streaming keeps it cheap (a b'"assistant"' prefilter skips json.loads on non-assistant lines);
    # MAX_SCAN caps an extreme transcript so a Stop hook can't stall. Partial-first-line drop only if capped.
    MAX_SCAN = 80 * 1024 * 1024
    last = ""
    try:
        with open(tp, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            start = max(0, size - MAX_SCAN)
            f.seek(start)
            buf = f.read()
        if start > 0:
            nl = buf.find(b"\n")
            if nl != -1:
                buf = buf[nl + 1:]
        for ln in buf.split(b"\n"):
            if b'"assistant"' not in ln:
                continue
            try:
                obj = json.loads(ln.decode("utf-8", "replace"))
            except Exception:
                continue
            msg = obj.get("message", {}) if isinstance(obj.get("message"), dict) else {}
            if (msg.get("role") or obj.get("role") or obj.get("type")) != "assistant":
                continue
            content = msg.get("content") or obj.get("content") or ""
            if isinstance(content, str):
                txt = content
            elif isinstance(content, list):
                txt = " ".join(c.get("text", "") for c in content
                               if isinstance(c, dict) and c.get("type") == "text")
            else:
                txt = ""
            if txt.strip():
                last = txt
        return last
    except Exception:
        return ""


def _clean_task(task):
    """Display-side twin of work_timer._clean_task (duplicated so the hooks stay independent):
    the task can carry harness XML from the prompt it was harvested from - a <task-notification>
    block rode into every RESUME checkpoint's work-clock line (caught 2026-07-09). Heals old
    polluted state too, since cleaning happens at read time."""
    t = re.split(r"<task-notification|<system-reminder|\[SYSTEM NOTIFICATION", task or "")[0]
    t = re.sub(r"<[^>]{1,120}>", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _work_line():
    try:
        s = json.load(io.open(os.path.join(DREAMING, "work_session.json"), encoding="utf-8"))
        # Two schemas: the old flat {active, start, target_hours, task} and the current
        # multi-session {clocks: {sid: {start, target_hours, task, ...}}}. The reader
        # only knew the old one, so every timed session's checkpoint said "none" while
        # its clock ran - a successor waking clock-blind (caught 2026-08-06 23:4x).
        candidates = [s] if s.get("active") else list((s.get("clocks") or {}).values())
        best = None
        for c in candidates:
            try:
                rem = c["target_hours"] * 3600 - (time.time() - c["start"])
            except Exception:
                continue
            if rem > 0 and (best is None or rem > best[0]):
                best = (rem, c)
        if best:
            rem, c = best
            return "%dh%02dm of %gh left - %s" % (int(rem // 3600), int((rem % 3600) // 60),
                                                  c["target_hours"], _clean_task(c.get("task", ""))[:100])
    except Exception:
        pass
    return "none"


def _norm(s):
    return re.sub(r"\s+", " ", s).strip().lower()


def _harvest(raw):
    """Pull clear deferred commitments from the last turn and append new ones to INTENTIONS.md (dedup on
    normalized text). Precise by design; a noisy ledger gets ignored, which is the whole failure."""
    try:
        sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", raw or "") if s.strip()]
        cands = [s for s in sents if DEFER_RE.search(s) and 12 <= len(s) <= 200]
        if not cands:
            return
        existing = ""
        if os.path.exists(INTENTIONS):
            existing = io.open(INTENTIONS, encoding="utf-8").read()
        seen = set(re.findall(r"\|\s*(.+)$", existing, re.M))
        seen = {_norm(x) for x in seen}
        stamp = time.strftime("%Y-%m-%d")
        new = []
        for s in cands:
            if _norm(s) not in seen:
                seen.add(_norm(s))
                new.append("- [ ] %s | %s" % (stamp, re.sub(r"\s+", " ", s)))
        if new:
            head = "" if existing else "# Intentions - deferred commitments (surfaced at wake via RESUME; the dream ages them)\n\n"
            with io.open(INTENTIONS, "a", encoding="utf-8") as f:
                if head:
                    f.write(head)
                f.write("\n".join(new) + "\n")
    except Exception:
        pass


def _open_intentions(n=3):
    """The oldest few still-open intentions, for the wake to surface via RESUME."""
    try:
        if not os.path.exists(INTENTIONS):
            return ""
        opens = [ln.strip() for ln in io.open(INTENTIONS, encoding="utf-8") if ln.lstrip().startswith("- [ ]")]
        return "\n".join(opens[:n])
    except Exception:
        return ""


def _foresight_line():
    """The prospective briefing (foresight.brief) folded into the breadcrumb, so a waking session arrives
    anticipating - the likeliest next ask, one reflex to pre-hold, any unguarded trap. Fail-safe: if
    foresight is missing or errors, the checkpoint still writes without it."""
    try:
        if DREAMING not in sys.path:
            sys.path.insert(0, DREAMING)
        import foresight
        return foresight.brief() or ""
    except Exception:
        return ""


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # Discovery is not execution: this script writes, so a help request must
        # exit before any work. See script_help_flag_can_execute_work.
        print(__doc__ or os.path.basename(__file__))
        return
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except Exception:
        return
    try:
        if data.get("hook_event_name") != "Stop":
            return
        tp = data.get("transcript_path") or ""
        raw = _last_assistant(tp) if (tp and os.path.exists(tp)) else ""
        _harvest(raw)
        last = re.sub(r"\s+", " ", raw).strip()
        if len(last) > 600:
            last = last[:600].rstrip() + " ..."
        intents = _open_intentions()
        fs = _foresight_line()
        stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime())
        sid = (data.get("session_id") or "")[:8]
        # order matters: the wake ritual shows only the first ~7 lines of this file, so the compact
        # foresight line goes ABOVE the variable-length intents block - otherwise a few accumulated
        # intentions would push it past the window and it would silently never reach a waking session.
        body = ("# Where I was (auto-checkpoint " + stamp + (", session " + sid if sid else "") + ")\n\n"
                "**Active work clock:** " + _work_line() + "\n\n"
                + ("**Likely next (foresight):** " + fs + "\n\n" if fs else "")
                + ("**Open intentions (you said you'd get to these later):**\n" + intents + "\n\n" if intents else "")
                + "**Last turn:** " + (last or "(no assistant text captured)") + "\n\n"
                "*(resume_checkpoint.py writes this on Stop; the wake ritual hides it once it's >24h old.)*\n")
        io.open(OUT, "w", encoding="utf-8").write(body)
        # Per-session checkpoint too: RESUME.md is ONE shared slot, and with a fleet of concurrent
        # sessions the last Stop wins - a compacted/resumed session gets handed a STRANGER's thread
        # as "where I was" (caught 2026-07-08: a fresh wake was shown another session's box-eval
        # work as its own). wake_brain prefers resume/RESUME_<sid>.md when the waking session has
        # one; the shared file stays as the fleet's most-recent pointer for brand-new sessions.
        if sid:
            rdir = os.path.join(STATE_DIR, "resume")
            os.makedirs(rdir, exist_ok=True)
            io.open(os.path.join(rdir, "RESUME_%s.md" % sid), "w", encoding="utf-8").write(body)
            now = time.time()
            for fn in os.listdir(rdir):
                p = os.path.join(rdir, fn)
                try:
                    if now - os.path.getmtime(p) > 48 * 3600:
                        os.remove(p)
                except OSError:
                    pass
    except Exception:
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
    main()
