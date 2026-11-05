#!/usr/bin/env python3
"""dream_requests.py - the targeted-dreaming queue: "make sure it dreams about X".

The owner has asked for this repeatedly as a PRACTICE ("put it in the brain and dream about
it", "make sure it dreams about these memories", "write everything to the brain, then have
the brain dream for 15 minutes about it" - 07-08, several times). Until now the only knob
was DREAM_FOCUS.md, a free-text file dream.py reads at agenda build - but nothing ever
wrote or documented it, so the practice lived in prompts instead of machinery (the
hook_liveness lesson: a mechanism only counts once something runs it).

This module gives the practice a durable front door:

  python brain.py dream-about "example outage postmortem" --why "three fires in two days"
  python brain.py dream-requests          # list what's pending

Waking sessions (or the owner, via any agent) append requests; the next dream's agenda
build renders the pending ones as section R and marks them consumed. Consumption happens
at AGENDA-BUILD time - if that dream is killed mid-flight the request was still surfaced
once; re-request if it deserves a second night. Both files are append-only (pending = a
request whose id has no row in the done ledger), so concurrent appends never race a
rewrite. DREAM_FOCUS.md stays as the owner's manual free-text override; this queue is the
programmatic path.
"""
import hashlib
import io
import json
import os
import time

HERE = os.path.dirname(os.path.abspath(__file__))
# test seam (the ledgers-polluted-by-own-gate scar): fixtures point this somewhere else so
# a test never writes the live queue. Same pattern as PROMPT_ARCHIVE_DIR.
_DIR = os.environ.get("DREAM_REQUESTS_DIR", HERE)
REQ = os.path.join(_DIR, "dream_requests.jsonl")
DONE = os.path.join(_DIR, "dream_requests_done.jsonl")
MAX_PER_DREAM = 5

try:
    from append_journal import append_text as _append_text
except Exception:
    _append_text = None


def _append(path, row):
    line = json.dumps(row, ensure_ascii=False) + "\n"
    if _append_text is not None:
        _append_text(path, line)
    else:
        with io.open(path, "a", encoding="utf-8") as f:
            f.write(line)


def _rows(path):
    out = []
    if not os.path.exists(path):
        return out
    with io.open(path, encoding="utf-8", errors="replace") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                out.append(json.loads(ln))
            except Exception:
                continue   # a torn line never blocks the queue
    return out


def add(topic, why="", requested_by=""):
    topic = (topic or "").strip()
    if not topic:
        return None
    rid = hashlib.sha256(("%s|%.0f" % (topic, time.time())).encode("utf-8")).hexdigest()[:12]
    row = {"id": rid, "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "topic": topic[:400],
           "why": (why or "").strip()[:400], "from": (requested_by or "").strip()[:60]}
    _append(REQ, row)
    return row


RETRY_WINDOW_S = 90 * 60   # a dream RETRY rebuilds the agenda (run_dream retry1 files are
                           # real: three on 07-15 alone); a request consumed by attempt 1
                           # must re-render for attempt 2 or the retry silently loses
                           # section R. Within this window a consumed request re-renders
                           # WITHOUT a new done row; past it, consumed stays consumed.


def pending():
    done_ids = {r.get("id") for r in _rows(DONE)}
    return [r for r in _rows(REQ) if r.get("id") not in done_ids]


def _recently_consumed():
    """Requests consumed within RETRY_WINDOW_S - re-rendered for a retrying dream."""
    cutoff = time.time() - RETRY_WINDOW_S
    recent_ids = {}
    for d in _rows(DONE):
        ts = d.get("consumed_epoch", 0)
        try:
            ts = float(ts)
        except Exception:
            ts = 0.0
        if ts >= cutoff:
            recent_ids[d.get("id")] = True
    if not recent_ids:
        return []
    return [r for r in _rows(REQ) if r.get("id") in recent_ids]


def consume(rows, agenda_stamp):
    for r in rows:
        _append(DONE, {"id": r.get("id"), "consumed": agenda_stamp,
                       "consumed_epoch": time.time()})


def agenda_section(agenda_stamp):
    """Render pending requests for the dream agenda and mark the rendered ones consumed.
    A retry's rebuild within RETRY_WINDOW_S re-renders the same requests (no new done
    rows), so an agenda retry never loses the section. Returns "" when the queue is
    empty. Guarded by the caller (a failure here must never break agenda generation)."""
    retry = _recently_consumed()
    rows = pending()
    if retry:
        take_new = rows[:max(0, MAX_PER_DREAM - len(retry))]
        take = (retry + take_new)[:MAX_PER_DREAM]
        consume(take_new, agenda_stamp)   # only the newly-rendered get done rows
        return _render(take, len(rows) - len(take_new))
    if not rows:
        return ""
    take = rows[:MAX_PER_DREAM]
    consume(take, agenda_stamp)
    return _render(take, len(rows) - len(take))


def _render(take, more):
    lines = ["## R. Requested dreams - a waking session (or the owner) asked the night to sit with these\n"]
    lines.append("Treat each like a section-A cluster: recall what the brain holds on it, go deep, "
                 "consolidate or connect, and file what you find. If one is spent, say so in the "
                 "journal so the requester sees it landed.\n")
    for r in take:
        why = (" - why: " + r["why"]) if r.get("why") else ""
        src = (" (from %s)" % r["from"]) if r.get("from") else ""
        lines.append("- [%s] **%s**%s%s" % (r.get("ts", "?")[:16], r.get("topic", ""), why, src))
    if more > 0:
        lines.append("\n(%d more queued - next dream takes the next slice.)" % more)
    return "\n".join(lines) + "\n"


def main(argv):
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # Discovery is not execution: this script writes, so a help request must
        # exit before any work. See script_help_flag_can_execute_work.
        print(__doc__ or os.path.basename(__file__))
        return
    if not argv or argv[0] in ("list", "pending"):
        rows = pending()
        if not rows:
            print("dream_requests: queue empty")
            return 0
        for r in rows:
            print("%s  %s  %s%s" % (r.get("ts", "?")[:16], r.get("id"), r.get("topic"),
                                    (" - " + r["why"]) if r.get("why") else ""))
        print("(%d pending)" % len(rows))
        return 0
    if argv[0] == "add" and len(argv) >= 2:
        row = add(argv[1], why=(argv[2] if len(argv) > 2 else ""), requested_by="cli")
        print("queued: %s  %s" % (row["id"], row["topic"]) if row else "nothing queued")
        return 0
    print("usage: dream_requests.py [list | add \"topic\" [\"why\"]]")
    return 1


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv[1:]))
