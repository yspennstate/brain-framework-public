# -*- coding: utf-8 -*-
"""hook_seen.py - is this exact block already in the receiver's context? (G06 hook token budget, 12 Oct 2026)

A hook that re-sends a long block on a timer pays for it twice: the new copy, and every later turn re-reading both copies
until the context is compacted. Measured 12 Oct over the last 48 h of 17 Claude transcripts (fronts/G06 in
primary_directive/improve_1012): 15.1 pct of every hook-context character was an EXACT repeat of a block the same session
had already received since its last compaction - push_reminder.py 1.07 M chars, owner_board_hook.py 0.59 M.

This module answers one question for a hook that has ALREADY decided to fire: has this receiver been given this exact
text (sha1) after its last compaction and within max_age_s? If so the hook sends a short line instead of the block. The
timer, the decision to fire and the text itself are unchanged; only a copy the context already holds is not sent again.

  receiver(payload)      the session id, or session~agent for a subagent's step (a subagent carries its PARENT's session
                         id and its own agent_id - instinct_prime.py _receiver, 30 Sep): each context is its own receiver
  already_given(...)     (True, when) if the same text went to this receiver after its last compaction and within max_age_s
  record_given(...)      note that the full text went to this receiver now

Fails OPEN: any error reads as "not given", so the hook sends the whole block exactly as it did before this module.
Compactions are read from the tail of ~/.claude/brain_graph/compactions/INDEX.jsonl (the PostCompact hook writes a row per
compaction, compact_nodes.py); a full text older than the oldest row in the tail is treated as possibly compacted away.
Test seams: HOOK_SEEN_STATE (state folder), HOOK_SEEN_INDEX (compaction index). Selftest: python hook_seen.py --selftest
"""
import sys
if __name__ == "__main__" and ("-h" in sys.argv[1:] or "--help" in sys.argv[1:]):
    print(__doc__)
    sys.exit(0)
import datetime as _dt
import hashlib
import io
import json
import os
import re
import time

HOME = os.path.expanduser("~")
STATE_DIR = os.environ.get("HOOK_SEEN_STATE") or os.path.join(HOME, ".claude", "dreaming", "hook_seen_state")
INDEX = os.environ.get("HOOK_SEEN_INDEX") or os.path.join(HOME, ".claude", "brain_graph", "compactions", "INDEX.jsonl")
TAIL_BYTES = 256 * 1024
_AGENT_TRANSCRIPT = re.compile(r"/subagents/(?:workflows/[^/]+/)?agent-([A-Za-z0-9]+)\.jsonl$")


def receiver(payload):
    """The session id, or session~agent when the step is a subagent's (agent_id, or a subagents/ transcript path)."""
    payload = payload if isinstance(payload, dict) else {}
    session = str(payload.get("session_id") or payload.get("thread_id") or "")
    agent = str(payload.get("agent_id") or "")
    if not agent:
        path = str(payload.get("agent_transcript_path") or payload.get("transcript_path") or "").replace("\\", "/")
        m = _AGENT_TRANSCRIPT.search(path)
        agent = m.group(1) if m else ""
    agent = re.sub(r"[^A-Za-z0-9]", "", agent)[:24]
    return session + "~" + agent if session and agent else session


def _digest(text):
    return hashlib.sha1(str(text).encode("utf-8", "replace")).hexdigest()[:16]


def _safe(name):
    return re.sub(r"[^A-Za-z0-9_.~-]", "_", str(name))[:120] or "none"


def _epoch(iso):
    try:
        return _dt.datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def compaction_view(session_id, index=None):
    """(last compaction epoch of this session or None, epoch of the oldest row read) from the index tail."""
    index = index or INDEX
    with open(index, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        fh.seek(max(0, size - TAIL_BYTES))
        data = fh.read()
    lines = data.split(b"\n")
    if size > TAIL_BYTES:
        lines = lines[1:]               # the first line of a tail read may be cut
    last, oldest = None, None
    sid = str(session_id or "")
    for ln in lines:
        if not ln.strip():
            continue
        try:
            r = json.loads(ln.decode("utf-8", "replace"))
        except Exception:
            continue
        t = _epoch(r.get("at"))
        if t is None:
            continue
        oldest = t if oldest is None else min(oldest, t)
        if sid and str(r.get("session_id") or "") == sid:
            last = t if last is None else max(last, t)
    return last, oldest, size <= TAIL_BYTES


def already_given(hook, payload, text, now=None, max_age_s=6 * 3600):
    """(True, epoch given) when this exact text went in full to this receiver after its last compaction and within
    max_age_s; (False, None) otherwise, and on any error."""
    try:
        now = time.time() if now is None else now
        rcv = receiver(payload)
        if not rcv:
            return False, None
        path = os.path.join(STATE_DIR, _safe(hook), _safe(rcv) + ".json")
        st = json.load(io.open(path, encoding="utf-8"))
        at = float(st.get("at") or 0)
        if st.get("h") != _digest(text) or now - at > max_age_s or at > now + 60:
            return False, None
        session = str((payload or {}).get("session_id") or (payload or {}).get("thread_id") or "")
        last, oldest, whole = compaction_view(session)
        if last is not None and last >= at - 5:
            return False, None          # compacted after (or as) the full text went out: the context lost it
        if not whole and (oldest is None or at < oldest):
            return False, None          # the full text is older than the index tail: a compaction could hide past it
        return True, at
    except Exception:
        return False, None


def record_given(hook, payload, text, now=None):
    """Note that the full text went to this receiver now. Never raises."""
    try:
        now = time.time() if now is None else now
        rcv = receiver(payload)
        if not rcv:
            return
        d = os.path.join(STATE_DIR, _safe(hook))
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, _safe(rcv) + ".json")
        tmp = path + ".%d.tmp" % os.getpid()
        with io.open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"h": _digest(text), "at": now, "receiver": rcv}, fh)
        for i in range(5):
            try:
                os.replace(tmp, path)
                break
            except OSError:
                if i == 4:
                    raise
                time.sleep(0.05)
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass


def hhmm(epoch):
    try:
        return time.strftime("%H:%M", time.localtime(float(epoch)))
    except Exception:
        return "earlier"


def selftest():
    import tempfile
    global STATE_DIR, INDEX
    keep = STATE_DIR, INDEX
    d = tempfile.mkdtemp(prefix="hook_seen_")
    STATE_DIR = os.path.join(d, "state")
    INDEX = os.path.join(d, "INDEX.jsonl")
    io.open(INDEX, "w", encoding="utf-8").write(json.dumps({"at": "2026-10-12T01:00:00+03:00", "session_id": "other"}) + "\n")
    bad = []
    try:
        t0 = time.time()
        p = {"session_id": "S1", "hook_event_name": "PreToolUse"}
        if already_given("h", p, "TEXT", now=t0)[0]:
            bad.append("a receiver never given the text reads as given")
        record_given("h", p, "TEXT", now=t0)
        if not already_given("h", p, "TEXT", now=t0 + 1800)[0]:
            bad.append("the same text to the same receiver 30 min later does not read as given")
        if already_given("h", p, "TEXT CHANGED", now=t0 + 1800)[0]:
            bad.append("a changed text reads as given")
        if already_given("h", dict(p, agent_id="a123"), "TEXT", now=t0 + 1800)[0]:
            bad.append("a subagent under the same session reads as given (its context never had it)")
        if already_given("h", {"session_id": "S1", "transcript_path": "C:/x/S1/subagents/workflows/wf_1/agent-a99.jsonl"},
                         "TEXT", now=t0 + 1800)[0]:
            bad.append("a subagent named only by its transcript path reads as given")
        if already_given("h", p, "TEXT", now=t0 + 7 * 3600)[0]:
            bad.append("a text older than max_age reads as given")
        with io.open(INDEX, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"at": _dt.datetime.fromtimestamp(t0 + 600).astimezone().isoformat(), "session_id": "S1"}) + "\n")
        if already_given("h", p, "TEXT", now=t0 + 1800)[0]:
            bad.append("a compaction after the full text does not reset it")
        record_given("h", p, "TEXT", now=t0 + 1900)
        if not already_given("h", p, "TEXT", now=t0 + 2500)[0]:
            bad.append("a full text given after the compaction does not count")
        INDEX = os.path.join(d, "missing.jsonl")
        if already_given("h", p, "TEXT", now=t0 + 2500)[0]:
            bad.append("an unreadable compaction index does not fail open")
        if already_given("h", {}, "TEXT", now=t0)[0]:
            bad.append("an empty payload reads as given")
    finally:
        STATE_DIR, INDEX = keep
    print("selftest %s%s" % ("ok" if not bad else "FAILED: ", "; ".join(bad)))
    return 0 if not bad else 1


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    print(__doc__)
