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
# explicit TODO / next-step phrase. Harvested here (Stop) so the intention outlives the turn.
# 30 Oct (1000-ways D4 #74): a bare "next step" or "follow-up" matched report prose ("Running: the T256 follow-up",
# "That's the next step, and it's recorded"); 7 of 28 harvested lines were real deferrals in each of two hand-labelled
# samples. The phrases now need a plan shape, and status lines (SKIP_RE) are left out. A version fitted on one sample
# scored precision 0.75, recall 3 of 7 on the other (was 0.25, 7 of 7); this one is fitted on both 56 labels.
# 31 Oct (q1526): "come back to" needs a first-person subject; the bare phrase took third-person prose ("He had come
# back to her", "the scores come back to the missing folder"). Same 56 labels, nothing lost; 2 open lines drop.
DEFER_RE = re.compile(
    r"(?:^|\s)TODO:|\bstill\s+need\s+to\b|\b(?:i|we)(?:'ll|\s+will|\s+can|\s+should|\s+need\s+to|\s+want\s+to)?\s+come\s+back\s+to\b"
    r"|\bmy\s+next\s+step\b|\bpossible\s+next\s+step\b"
    r"|\bnext\s+step\s+(?:is|would\s+be|will\s+be)\s+(?:to\s+\w+|\w+ing)\b|\bwhich\s+is\s+the\s+next\s+step\b"
    r"|\b(?:i'?ll|we'?ll|i\s+will|we\s+will|i\s+should|we\s+should|i\s+want\s+to)\b[^.!?\n]{0,90}?"
    r"\b(?:next\s+session|next\s+time|later|tomorrow|afterwards?|down\s+the\s+line|eventually|soon"
    r"|once\s+\w|when\s+\w|own\s+next\s+step|as\s+soon\s+as)\b", re.I)
SKIP_RE = re.compile(r"^(?:REPORT:|Running:|- Still running|- Waiting:|Finished)", re.I)


# A hook refusal names the shape it refuses: "REFUSED (python dash): ...", "BLOCKED (danger mode): ...".
REFUSAL_RE = re.compile(r"\b(?:REFUSED|BLOCKED) \(([^)]{2,40})\)")


def _last_assistant(tp):
    return _scan(tp)[0]


def _refusal_line(refusals):
    """Hook refusals this session already took, so the session does not walk into them again after a compaction
    (1000-ways D5 #70, 31 Oct: 106 of 115 python-dash refusals on the Mac came to a session already refused once, 88
    of them with a compaction between - the refusal had been summarised away). Most taken first, with the hook's own
    advice (the last sentence of its reason)."""
    if not refusals:
        return ""
    parts = []
    for tag, (n, advice) in sorted(refusals.items(), key=lambda kv: -kv[1][0])[:3]:
        parts.append("%s x%d%s" % (tag, n, (" - " + advice) if advice else ""))
    return "; ".join(parts)


def _scan(tp):
    """One pass over the transcript: (last assistant text, {refusal tag: [count, advice]})."""
    # Scan the WHOLE transcript for the last assistant TEXT message. A fixed 200KB tail + splitlines()[-80:]
    # MISSES the true last message on a long session: when big tool outputs / system reminders pile up AFTER
    # it, the window returns an older message = a STALE "where I was" breadcrumb (the one thing a resume must
    # never be). Reproduced 2026-07-11 on a 7MB transcript (the fleet registry twin got a message 3 turns
    # old). Streaming keeps it cheap (a b'"assistant"' prefilter skips json.loads on non-assistant lines);
    # MAX_SCAN caps an extreme transcript so a Stop hook can't stall. Partial-first-line drop only if capped.
    MAX_SCAN = 80 * 1024 * 1024
    last = ""
    refusals = {}
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
            if b'"is_error":true' in ln and (b"REFUSED (" in ln or b"BLOCKED (" in ln):
                try:
                    obj = json.loads(ln.decode("utf-8", "replace"))
                    # api-request-blob records repeat tool results; count each result once, from its user record
                    for c in (obj.get("message") or {}).get("content") or [] if obj.get("type") == "user" else []:
                        if isinstance(c, dict) and c.get("type") == "tool_result" and c.get("is_error"):
                            body = c.get("content")
                            body = body if isinstance(body, str) else " ".join(
                                x.get("text", "") for x in body or [] if isinstance(x, dict))
                            m = REFUSAL_RE.search(body)
                            if m:
                                tail = body[m.end():].strip().rstrip(".")
                                advice = re.split(r"(?<=[.!?])\s+", tail)[-1][:140] if tail else ""
                                r = refusals.setdefault(m.group(1), [0, advice])
                                r[0] += 1
                except Exception:
                    pass
                continue
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
        return last, refusals
    except Exception:
        return "", {}


def _clean_task(task):
    """Display-side twin of work_timer._clean_task (duplicated so the hooks stay independent):
    the task can carry harness XML from the prompt it was harvested from - a <task-notification>
    block rode into every RESUME checkpoint's work-clock line (caught 2026-07-09). Heals old
    polluted state too, since cleaning happens at read time."""
    t = re.split(r"<task-notification|<system-reminder|\[SYSTEM NOTIFICATION", task or "")[0]
    t = re.sub(r"<[^>]{1,120}>", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _work_line(sid=""):
    try:
        s = json.load(io.open(os.path.join(DREAMING, "work_session.json"), encoding="utf-8"))
        # Two schemas: the old flat {active, start, target_hours, task} and the current
        # multi-session {clocks: {sid: {start, target_hours, task, ...}}}. The reader
        # only knew the old one, so every timed session's checkpoint said "none" while
        # its clock ran - a successor waking clock-blind (caught 2026-08-06 23:4x).
        clocks = s.get("clocks") or {}
        if s.get("active"):
            candidates = [s]
        elif sid:
            # 2026-10-30 (1000-ways D5 #20): only this session's own clock. Taking the longest clock of any
            # session put one session's 10 h Bitcoin ask into all nine Mac sessions' checkpoints as their own.
            candidates = [c for k, c in clocks.items() if k.startswith(sid)]
            if not candidates:
                return "none in this session"
        else:
            candidates = list(clocks.values())
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


def _previously(last, cap=240):
    """The last turn's opening sentence (a final message leads with what was done), joined with the next one when
    the first is under 60 characters, up to cap characters."""
    s = re.sub(r"\s+", " ", re.sub(r"[*`]+|^[#>]+\s*", "", last or "", flags=re.M)).strip()
    if not s:
        return ""
    cut = s[:cap]
    ends = [m.end() for m in re.finditer(r"[.!?](?=\s|$)", cut) if m.end() >= 60]
    if ends:
        return cut[:ends[0]]
    return cut.rsplit(" ", 1)[0] + " ..." if len(s) > cap else cut


def _norm(s):
    return re.sub(r"\s+", " ", s).strip().lower()


def _harvest(raw, sid=""):
    """Pull clear deferred commitments from the last turn and append new ones to INTENTIONS.md (dedup on
    normalized text). Precise by design; a noisy ledger gets ignored, which is the whole failure.
    Each line carries the session that made the promise after its date, so a wake shows a session its own."""
    try:
        sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", raw or "") if s.strip()]
        cands = [s for s in sents if DEFER_RE.search(s) and not SKIP_RE.match(s) and 12 <= len(s) <= 200]
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
                new.append("- [ ] %s%s | %s" % (stamp, " " + sid if sid else "", re.sub(r"\s+", " ", s)))
        if new:
            head = "" if existing else "# Intentions - deferred commitments (surfaced at wake via RESUME; the dream ages them)\n\n"
            with io.open(INTENTIONS, "a", encoding="utf-8") as f:
                if head:
                    f.write(head)
                f.write("\n".join(new) + "\n")
    except Exception:
        pass


def _open_intentions(n=3, sid=""):
    """The newest few still-open intentions the current DEFER_RE still accepts, for the wake to surface via RESUME.
    It used to show the oldest: nothing ever closes a line on the Mac (140 open, 0 closed on 30 Oct), so the same three
    report sentences from 29 Oct headed every checkpoint. Lines the old pattern let in stay in the file, unshown.
    2026-10-30 (1000-ways D5 #30): with a session id, only that session's own lines. The file is one for the whole
    fleet, so 23 checkpoints showed the same three newest lines and at most 3 of the 27 shown to nine real sessions
    were their own promises. Untagged lines (before this change) are shown to no session."""
    try:
        if not os.path.exists(INTENTIONS):
            return ""
        opens = []
        for ln in io.open(INTENTIONS, encoding="utf-8"):
            ln = ln.strip()
            if ln.startswith("- [ ]"):
                if sid and not re.match(r"- \[ \] \d{4}-\d{2}-\d{2} %s \|" % re.escape(sid), ln):
                    continue
                text = ln.split("|", 1)[-1].strip()
                if DEFER_RE.search(text) and not SKIP_RE.match(text):
                    opens.append(ln)
        return "\n".join(opens[-n:][::-1])
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


# _scan re-reads up to 80 MB at every Stop (0.24-0.49 s idle, 1.3 s median under load on 1 Nov). The cache keeps, per
# transcript, every refusal record and the newest assistant text with their byte offsets, parses only appended lines,
# and applies _scan's own 80 MB window when it answers, so the counts slide out exactly as _scan's do.
SCAN_CACHE = os.path.join(STATE_DIR, ".resume_scan_cache")
_MAX_SCAN = 80 * 1024 * 1024


def _scan_line(ln, refs, off, last):
    """_scan's work on one line (bytes) at file offset off; appends refusal records, returns the newest text."""
    if b'"is_error":true' in ln and (b"REFUSED (" in ln or b"BLOCKED (" in ln):
        try:
            obj = json.loads(ln.decode("utf-8", "replace"))
            for c in (obj.get("message") or {}).get("content") or [] if obj.get("type") == "user" else []:
                if isinstance(c, dict) and c.get("type") == "tool_result" and c.get("is_error"):
                    body = c.get("content")
                    body = body if isinstance(body, str) else " ".join(
                        x.get("text", "") for x in body or [] if isinstance(x, dict))
                    m = REFUSAL_RE.search(body)
                    if m:
                        tail = body[m.end():].strip().rstrip(".")
                        advice = re.split(r"(?<=[.!?])\s+", tail)[-1][:140] if tail else ""
                        refs.append([off, m.group(1), advice])
        except Exception:
            pass
        return last
    if b'"assistant"' not in ln:
        return last
    try:
        obj = json.loads(ln.decode("utf-8", "replace"))
    except Exception:
        return last
    msg = obj.get("message", {}) if isinstance(obj.get("message"), dict) else {}
    if (msg.get("role") or obj.get("role") or obj.get("type")) != "assistant":
        return last
    content = msg.get("content") or obj.get("content") or ""
    if isinstance(content, str):
        txt = content
    elif isinstance(content, list):
        txt = " ".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text")
    else:
        txt = ""
    return [off, txt] if txt.strip() else last


def _marks(tp, end):
    import hashlib
    with open(tp, "rb") as f:
        head = f.read(4096)
        f.seek(max(0, end - 4096))
        before = f.read(min(end, 4096))
    return [hashlib.sha1(head).hexdigest()[:16], hashlib.sha1(before).hexdigest()[:16]]


def _scan_cached(tp):
    """_scan's (last assistant text, refusals), parsing only what the transcript gained since the last Stop."""
    import hashlib
    st = os.stat(tp)
    cpath = os.path.join(SCAN_CACHE, hashlib.sha1(os.path.abspath(tp).encode("utf-8")).hexdigest()[:20] + ".json")
    c = None
    try:
        with io.open(cpath, encoding="utf-8") as f:
            c = json.load(f)
        if (c.get("v") != 1 or c.get("ino") != st.st_ino or c.get("dev") != st.st_dev or st.st_size < c["end"]
                or _marks(tp, c["end"]) != c.get("marks")):
            c = None
    except Exception:
        c = None
    if c is None:
        try:                                          # a rebuild: drop caches no Stop has touched in a week
            for e in os.scandir(SCAN_CACHE):
                if e.name.endswith(".json") and e.stat().st_mtime < time.time() - 7 * 86400:
                    os.remove(e.path)
        except OSError:
            pass
        base = max(0, st.st_size - _MAX_SCAN)         # nothing before _scan's window can ever count again
        c = {"v": 1, "ino": st.st_ino, "dev": st.st_dev, "end": base, "skip_first": base > 0, "refs": [],
             "last": None, "marks": None}
    with open(tp, "rb") as f:
        f.seek(c["end"])
        buf = f.read(st.st_size - c["end"])
    parts = buf.split(b"\n")
    refs, last, off = c["refs"], c["last"], c["end"]
    for i, ln in enumerate(parts[:-1]):
        if not (i == 0 and c.get("skip_first")):
            last = _scan_line(ln, refs, off, last)
        off += len(ln) + 1
    if off != c["end"] or c.get("marks") is None:
        c.update(end=off, last=last, skip_first=c.get("skip_first") and off == c["end"], marks=_marks(tp, off))
        try:
            os.makedirs(SCAN_CACHE, exist_ok=True)
            tmp = "%s.%d.tmp" % (cpath, os.getpid())
            with io.open(tmp, "w", encoding="utf-8") as f:
                json.dump(c, f, ensure_ascii=False)
            os.replace(tmp, cpath)
        except Exception:
            pass
    if parts[-1]:                                     # a last line with no newline yet: read now, cached once complete
        refs = list(refs)
        last = _scan_line(parts[-1], refs, off, last)
    start = max(0, st.st_size - _MAX_SCAN)
    keep = (lambda o: True) if start == 0 else (lambda o: o > start)
    refusals = {}
    for o, tag, advice in refs:
        if keep(o):
            r = refusals.setdefault(tag, [0, advice])
            r[0] += 1
    return (last[1] if last and keep(last[0]) else ""), refusals


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
        raw, refusals = ("", {})
        if tp and os.path.exists(tp):
            try:
                raw, refusals = _scan_cached(tp)
            except Exception:
                raw, refusals = _scan(tp)
        # The Stop payload's own copy of the final message (1 Nov 2026). The hook runs before that message's line
        # is on disk, so the scan returns the turn's second-to-last text; the payload has the last one.
        final = data.get("last_assistant_message")
        if isinstance(final, str) and final.strip():
            raw = final
        refused = _refusal_line(refusals)
        sid = (data.get("session_id") or "")[:8]
        _harvest(raw, sid)
        last = re.sub(r"\s+", " ", raw).strip()
        if len(last) > 600:
            last = last[:600].rstrip() + " ..."
        intents = _open_intentions(sid=sid)
        fs = _foresight_line()
        stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime())
        # order matters: the wake ritual shows only the first ~7 lines of this file, so the compact
        # foresight line goes ABOVE the variable-length intents block - otherwise a few accumulated
        # intentions would push it past the window and it would silently never reach a waking session.
        # 2 Nov 2026 (1000-ways D8 #100): "Last turn" sits below that window and reached 21 of 318 wakes, so the
        # wake said where I was without saying what I had just done. "Previously" is its first sentence, on the
        # header's next line; the work clock line is dropped when it would only say "none" (314 of 318 wakes).
        work = _work_line(sid)
        prev = _previously(last)
        body = ("# Where I was (auto-checkpoint " + stamp + (", session " + sid if sid else "") + ")\n"
                + ("**Previously:** " + prev + "\n" if prev else "") + "\n"
                + ("**Active work clock:** " + work + "\n\n" if not work.startswith("none") else "")
                + ("**Refused already in this session (do not retry these shapes):** " + refused + "\n\n" if refused else "")
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
