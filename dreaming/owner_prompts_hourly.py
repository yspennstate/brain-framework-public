#!/usr/bin/env python3
"""owner_prompts_hourly.py - the owner's last 3000 prompts, rebuilt every hour, and the threads they form.

Owner order, 2026-09-18 12:33 (his clock): a hook so that every agent gets his last 1000 prompts every hour,
because agents keep forgetting what he asked for; and a hook to a checkbox schedule so that they can see what
is going on there, with each element carrying a description of the history and what we are working on.

This is the hourly builder (Task Scheduler job BrainOwnerPromptsHourly through owner_prompts_launcher.vbs). It
reads the append-only prompt ledger (memory/prompts/PROMPTS.jsonl, written by prompt_archive.py on every
UserPromptSubmit), keeps the owner's own prompts, folds the re-fires a /loop puts back on the queue, and writes
five files beside the ledger:

  LAST_3000_OWNER_PROMPTS.md   his last 3000 prompts, newest first, grouped by day: time [session] text
  LAST_1000_OWNER_PROMPTS.md   the newest 1000 of them, same layout (his 12:33 order named a thousand; 16:05 widened it)
  OWNER_THREADS.md             the history layer of the checkbox schedule: one element per topic thread,
                               with first/last asked, how many times, which sessions, the dated history in
                               his words, and "what we are working on now"; the checkbox on each element
                               reflects the task checklist's done/seen marks (task_checklist_hook.py's
                               state.json) when that store exists - this file never writes the checklist
  OWNER_PROMPTS_DIGEST.md      the bounded block the hook injects (about 2 KB): where the full files are,
                               the task checklist, and the active threads with their counts
  OWNER_RECENT_ASKS.md         the last three hours of his asks across every session - the hook prints it
                               only when the verbatim last-100 block (owner_prompt_context.py) is unavailable
  owner_prompts_hourly.json    the same, machine-readable, for other tools

The delivery hook is owner_prompts_hook.py (Claude through hook_dispatch, Codex through hooks.json); it serves
the last-100 verbatim block of owner_prompt_context.py and the digest above in ONE injection, so no agent pays
for two prompt hooks. The topic lexicon lives in owner_prompt_topics.json beside this file (private vocabulary
stays out of the code); without that file a small generic lexicon is used.

Paths: BRAIN_MEMORY_DIR, PROMPT_ARCHIVE_DIR, TASK_CHECKLIST_HOME and OWNER_PROMPT_TOPICS override discovery.
Never raises out: a failure leaves the previous files in place (every write is temp + os.replace).
"""
import glob
import io
import json
import os
import re
import sys
import time
from collections import defaultdict

HOME = os.path.expanduser("~")
HERE = os.path.dirname(os.path.abspath(__file__))


def _discover_memory_dir():
    """The brain's memory directory: the env var, else the project memory dir that owns the live prompt ledger."""
    env = os.environ.get("BRAIN_MEMORY_DIR")
    if env:
        return env
    cands = glob.glob(os.path.join(HOME, ".claude", "projects", "*", "memory"))
    with_ledger = [p for p in cands if os.path.exists(os.path.join(p, "prompts", "PROMPTS.jsonl"))]
    if with_ledger:
        return max(with_ledger, key=lambda p: os.path.getmtime(os.path.join(p, "prompts", "PROMPTS.jsonl")))
    with_index = [p for p in cands if os.path.exists(os.path.join(p, "MEMORY.md"))]
    if with_index:
        return max(with_index, key=lambda p: os.path.getmtime(os.path.join(p, "MEMORY.md")))
    return os.path.join(HOME, ".claude", "memory")


def _checklist_dirs():
    out = [os.environ.get("TASK_CHECKLIST_HOME") or ""]
    out += sorted(glob.glob(os.path.join(HOME, "*", "12_cognitive_architecture", "task_checklist")))
    out.append(os.path.join(HOME, ".claude", "task_checklist"))
    return out


MEM = _discover_memory_dir()
ARCHIVE = os.environ.get("PROMPT_ARCHIVE_DIR") or os.path.join(MEM, "prompts")
LEDGER = os.path.join(ARCHIVE, "PROMPTS.jsonl")
OUT_LAST = os.path.join(ARCHIVE, "LAST_1000_OWNER_PROMPTS.md")
OUT_LAST3 = os.path.join(ARCHIVE, "LAST_3000_OWNER_PROMPTS.md")
OUT_THREADS = os.path.join(ARCHIVE, "OWNER_THREADS.md")
OUT_DIGEST = os.path.join(ARCHIVE, "OWNER_PROMPTS_DIGEST.md")
OUT_RECENT = os.path.join(ARCHIVE, "OWNER_RECENT_ASKS.md")
OUT_JSON = os.path.join(ARCHIVE, "owner_prompts_hourly.json")
CACHE = os.path.join(ARCHIVE, "owner_prompts_cache.json")   # incremental ledger read: offset, head sha, tail probe, owner rows
CACHE_MAX_ROWS = 8000          # raw owner rows kept; 3000 folded prompts need well under this
CACHE_TEXT_CAP = 4000
CHECKLIST_DIRS = _checklist_dirs()
TOPICS_FILE = os.environ.get("OWNER_PROMPT_TOPICS") or os.path.join(HERE, "owner_prompt_topics.json")
N_KEEP = int(os.environ.get("OWNER_PROMPTS_N", "3000"))
N_NEWEST = 1000
JSON_TEXT_CAP = 4000
DIGEST_HOURS = 3.0
DIGEST_MAX_PROMPTS = 14
HISTORY_PER_THREAD = 120

# Without a lexicon file: a small generic one, so the threads view still forms on a fresh installation.
DEFAULT_TOPICS = [
    ("code and infrastructure", ["code", "bug", "fix", "deploy", "server", "script", "test", "build", "install", "config"]),
    ("writing and papers", ["paper", "draft", "write", "essay", "chapter", "abstract", "proof", "theorem", "cite"]),
    ("media and video", ["video", "film", "render", "movie", "audio", "voice", "image", "animation"]),
    ("agents, memory and hooks", ["agent", "memory", "hook", "brain", "prompt", "session", "remember", "checklist"]),
    ("this computer", ["computer", "disk", "cpu", "ram", "slow", "crash", "restart", "window"]),
]


def load_topics():
    try:
        with io.open(TOPICS_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
        topics = [(str(t["topic"]), [str(k).lower() for k in t["keys"]]) for t in data["topics"]]
        return topics or DEFAULT_TOPICS
    except Exception:
        return DEFAULT_TOPICS


TOPICS = load_topics()
QUOTE_RX = re.compile(r"\s+")
# UTF-8 text that was once read as cp1252 and written back (U+00E2 U+20AC U+201D is an em dash). A hook that
# reads its stdin without declaring UTF-8 produces exactly this on Windows; repair it on the way out, never in
# the store it came from.
_MOJI_RX = re.compile(u"[ÂÃâ][-¿€‚ƒ„…†‡ˆ‰"
                      u"Š‹ŒŽ‘’“”•–—˜™š›"
                      u"œžŸ]")


def demojibake(s):
    if not s or not _MOJI_RX.search(s):
        return s
    try:
        fixed = s.encode("cp1252").decode("utf-8")
    except Exception:
        return s
    return fixed if len(_MOJI_RX.findall(fixed)) < len(_MOJI_RX.findall(s)) else s


def _cache_age_s(cache):
    """Seconds since the cache was written; a cache older than a day is not trusted (one full read a day)."""
    try:
        return time.time() - time.mktime(time.strptime(str(cache.get("written"))[:19], "%Y-%m-%dT%H:%M:%S"))
    except Exception:
        return 10 ** 9


def _head_sha(fh):
    import hashlib
    fh.seek(0)
    return hashlib.sha256(fh.read(4096)).hexdigest()


def _slim(r):
    from prompt_archive import redact
    return {"ts": r.get("ts"), "session": r.get("session"), "text": redact(str(r.get("text") or ""))[0][:CACHE_TEXT_CAP], "origin": r.get("origin"),
            "source": r.get("source"), "class_provisional": r.get("class_provisional")}


def read_owner_rows(full=False):
    """The ledger's owner rows, read incrementally: the cache holds the rows already seen and the byte offset they end at;
    a run parses only what was appended since. Any doubt - a shorter file, a different head, a tail that does not match
    the probe, a torn line - falls back to a full read. Returns (rows, how)."""
    import hashlib
    size = os.path.getsize(LEDGER) if os.path.exists(LEDGER) else 0
    if not size:
        return [], "empty", 0
    cache = None
    if not full:
        try:
            with io.open(CACHE, encoding="utf-8") as fh:
                cache = json.load(fh)
        except Exception:
            cache = None
    with io.open(LEDGER, "rb") as fh:
        head = _head_sha(fh)
        off = int(cache.get("offset") or 0) if cache else 0
        ok = bool(cache) and cache.get("schema") == 2 and cache.get("head_sha") == head and 0 < off <= size and _cache_age_s(cache) < 86400
        if ok:
            probe_size = min(200, off)
            fh.seek(off - probe_size)
            ok = (cache.get("tail_probe_bytes") == probe_size and
                  hashlib.sha256(fh.read(probe_size)).hexdigest() == cache.get("tail_probe_sha256"))
        if ok:
            rows = [_slim(r) for r in cache.get("rows") or [] if isinstance(r, dict)]
            total_seen = int(cache.get("total_seen") or len(rows))
            fh.seek(off)
            chunk = fh.read()
            how = "incremental"
        else:
            rows, off, how, total_seen = [], 0, "full", 0
            fh.seek(0)
            chunk = fh.read()
        last_nl = chunk.rfind(b"\n")
        if last_nl < 0:
            new_off = off
            body = b""
        else:
            body = chunk[:last_nl + 1]
            new_off = off + last_nl + 1
        added = 0
        for line in body.decode("utf-8", "replace").split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            if _is_owner(r) and len(str(r.get("text") or "")) >= 3:
                rows.append(_slim(r))
                added += 1
        total_seen += added
        rows = rows[-CACHE_MAX_ROWS:]
        fh.seek(max(0, new_off - 200))
        probe = fh.read(min(200, new_off))
    try:
        _write(CACHE, json.dumps({"schema": 2, "head_sha": head, "offset": new_off,
                                  "tail_probe_sha256": hashlib.sha256(probe).hexdigest(), "tail_probe_bytes": len(probe),
                                  "rows": rows, "total_seen": total_seen,
                                  "written": time.strftime("%Y-%m-%dT%H:%M:%S")}, ensure_ascii=False))
    except Exception:
        pass
    return rows, "%s (+%d rows)" % (how, added), total_seen


def _read_ledger():
    rows = []
    if not os.path.exists(LEDGER):
        return rows
    with io.open(LEDGER, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    return rows


def _is_owner(r):
    origin = (r.get("origin") or "").lower()
    if origin:
        return origin == "owner"
    src = str(r.get("source") or "")
    # hook = the Claude hook; hook-codex = the Codex hook (wired 2026-09-20; before it no Codex prompt reached
    # the ledger live, only the 07-06 backfill did)
    if not (src.startswith("hook") or src in ("backfill-claude", "backfill-codex", "codex-native-owner-repair")):
        return False
    return (r.get("class_provisional") or "") != "automation"


def _clean(text):
    # Reapply the current filter to old ledger rows and checklist text BEFORE
    # any summary cuts; redaction at original archive time was not sufficient.
    from prompt_archive import redact
    return redact(demojibake(QUOTE_RX.sub(" ", str(text or "")).strip()))[0]


def owner_prompts(rows):
    """Owner rows, chronological, re-fires folded: the same words from the same session within a day count once."""
    out = []
    last_by_key = {}
    for r in rows:
        if not _is_owner(r):
            continue
        text = _clean(r.get("text"))
        if len(text) < 3:
            continue
        ts = str(r.get("ts") or "")[:19]
        sess = str(r.get("session") or "?")[:8]
        key = (sess, text[:200])
        prev = last_by_key.get(key)
        if prev is not None and ts[:10] == prev["ts"][:10]:
            prev["refires"] += 1
            prev["last_ts"] = ts
            continue
        rec = {"ts": ts, "session": sess, "text": text, "refires": 0, "last_ts": ts,
               "cls": r.get("class_provisional") or "", "runtime": "codex" if "codex" in str(r.get("source")) else "claude"}
        out.append(rec)
        last_by_key[key] = rec
    return out


def topic_of(text):
    t = " " + text.lower() + " "
    best, best_n = "other", 0
    for name, keys in TOPICS:
        n = sum(1 for k in keys if k in t)
        if n > best_n:
            best, best_n = name, n
    return best


def load_checklist_state():
    for d in CHECKLIST_DIRS:
        if not d:
            continue
        p = os.path.join(d, "state.json")
        if os.path.exists(p):
            try:
                st = json.load(io.open(p, encoding="utf-8"))
                return d, st
            except Exception:
                return d, None
    return "", None


def checklist_entries(st):
    """Flatten the task checklist's entries whatever the container shape is."""
    if not st:
        return []
    ents = st.get("entries") if isinstance(st, dict) else None
    if isinstance(ents, dict):
        ents = list(ents.values())
    if not isinstance(ents, list):
        ents = [v for v in (st.values() if isinstance(st, dict) else []) if isinstance(v, dict) and ("asked" in v or "text" in v)]
    out = []
    for e in ents:
        if not isinstance(e, dict):
            continue
        text = _clean(e.get("asked_words") or e.get("asked") or e.get("text") or e.get("title") or "")
        seen = e.get("seen")
        seen = (str(seen).lower() == "true") if not isinstance(seen, bool) else seen
        out.append({"id": e.get("id") or e.get("tid") or "", "text": text, "status": str(e.get("status") or "open").lower(),
                    "seen": seen, "agent": e.get("agent") or e.get("by") or "",
                    "working": _clean(e.get("working_on") or e.get("working") or ""),
                    "asked_at": e.get("asked_at") or e.get("ts") or "", "topic": topic_of(text)})
    return out


def _write(path, body):
    tmp = path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(body)
    os.replace(tmp, path)


def _hhmm(ts):
    return ts[11:16] if len(ts) >= 16 else ts


def _p(path):
    return path.replace("\\", "/")


def _write_last(path, items, total_seen, now, cl_md):
    by_day = defaultdict(list)
    for r in reversed(items):
        by_day[r["ts"][:10]].append(r)
    lines = ["# His last %d prompts (owner rows of the prompt ledger, newest first, re-fires folded)" % len(items),
             "", "Rebuilt %s from %s (%d owner rows in the ledger so far). One line per prompt: time [session] text; "
             "`(re-fired xN)` is a /loop echo, not N asks. Threads with history: OWNER_THREADS.md. The hourly digest agents "
             "receive: OWNER_PROMPTS_DIGEST.md. The task checklist (done and seen marks): %s." % (
                 now, _p(LEDGER), total_seen, cl_md or "not found"),
             "", "Span: %s to %s across %d sessions." % (items[0]["ts"][:16] if items else "?", items[-1]["ts"][:16] if items else "?",
                                                       len({r["session"] for r in items})), ""]
    for day in sorted(by_day, reverse=True):
        lines.append("## %s  (%d)" % (day, len(by_day[day])))
        for r in by_day[day]:
            rf = " (re-fired x%d)" % (r["refires"] + 1) if r["refires"] else ""
            lines.append("- %s [%s] %s%s" % (_hhmm(r["ts"]), r["session"], r["text"][:600], rf))
        lines.append("")
    _write(path, "\n".join(lines) + "\n")


def build(full=False):
    t0 = time.time()
    rows, how, total_seen = read_owner_rows(full=full)
    owner = owner_prompts(rows)
    # The ledger is append-only and a backfill lands OLD rows at its end (the 2026-09-20 Codex backfill added
    # 1,620 July-September rows after the newest Claude rows), so "the last 3000" must be by time, not by
    # file position; the sort is stable, so same-second rows keep their file order.
    owner.sort(key=lambda r: r["ts"])
    last = owner[-N_KEEP:]
    for r in last:
        r["topic"] = topic_of(r["text"])
    now = time.strftime("%Y-%m-%d %H:%M")
    cl_dir, cl_state = load_checklist_state()
    cl = checklist_entries(cl_state)
    cl_md = _p(os.path.join(cl_dir, "CHECKLIST.md")) if cl_dir else ""

    # ---- LAST_3000 and LAST_1000 (newest first, grouped by day)
    sessions = len({r["session"] for r in last})
    _write_last(OUT_LAST3, last, total_seen, now, cl_md)
    _write_last(OUT_LAST, last[-N_NEWEST:], total_seen, now, cl_md)

    # ---- threads
    threads = defaultdict(list)
    for r in last:
        threads[r["topic"]].append(r)
    cutoff_24h = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 24 * 3600))
    cutoff_7d = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 7 * 24 * 3600))
    elements = []
    for name, items in threads.items():
        items_desc = sorted(items, key=lambda r: r["ts"], reverse=True)
        n24 = sum(1 for r in items if r["ts"] >= cutoff_24h)
        n7 = sum(1 for r in items if r["ts"] >= cutoff_7d)
        cl_items = [e for e in cl if e["topic"] == name]
        cl_open = [e for e in cl_items if e["status"] not in ("done", "dropped")]
        cl_done = [e for e in cl_items if e["status"] == "done"]
        cl_seen = [e for e in cl_items if e["seen"]]
        elements.append({
            "thread": name, "count": len(items), "last_24h": n24, "last_7d": n7,
            "first_asked": min(r["ts"] for r in items), "last_asked": items_desc[0]["ts"],
            "sessions": sorted({r["session"] for r in items}),
            "working_on": [{"ts": r["ts"], "session": r["session"], "text": r["text"][:300]} for r in items_desc[:3]],
            "history": [{"ts": r["ts"], "session": r["session"], "text": r["text"][:160], "refires": r["refires"]} for r in items_desc[:HISTORY_PER_THREAD]],
            "checklist": {"open": len(cl_open), "done": len(cl_done), "seen": len(cl_seen),
                          "open_items": [{"id": e["id"], "agent": e["agent"], "text": e["text"][:160], "working": e["working"][:160]} for e in cl_open[:8]]},
        })
    elements.sort(key=lambda e: (e["last_24h"], e["last_7d"], e["last_asked"]), reverse=True)

    th = ["# What he asked, by thread - the history layer of the checkbox schedule", "",
          "Rebuilt %s from his last %d prompts. One element per topic thread. The checkbox is [x] only when the task checklist "
          "records every entry of that thread as done; `seen` counts the entries he has marked as seen there. The task checklist "
          "itself (per-agent entries, done and seen marks, the CLI) is task_checklist_hook.py's %s; this file never writes it. "
          "Every time is his clock; `[session]` is the first eight characters of the session id." % (
              now, len(last), cl_md or "store (not found on this box)"), ""]
    for e in elements:
        box = "[x]" if (e["checklist"]["open"] == 0 and e["checklist"]["done"] > 0) else "[ ]"
        th.append("## %s %s  -  %d prompts, %d in the last 24 h, %d in 7 days; first %s, last %s; sessions %s" % (
            box, e["thread"], e["count"], e["last_24h"], e["last_7d"], e["first_asked"][:16], e["last_asked"][:16], ", ".join(e["sessions"][-8:])))
        th.append("")
        th.append("**What we are working on now (his newest words):**")
        for w in e["working_on"]:
            th.append("- %s [%s] %s" % (w["ts"][:16], w["session"], w["text"]))
        if e["checklist"]["open_items"]:
            th.append("")
            th.append("**Open on the task checklist (%d open, %d done, %d seen):**" % (e["checklist"]["open"], e["checklist"]["done"], e["checklist"]["seen"]))
            for it in e["checklist"]["open_items"]:
                th.append("- [ ] %s %s%s" % (it["id"], it["text"], (" - working: " + it["working"]) if it["working"] else ""))
        elif cl_dir:
            th.append("")
            th.append("Task checklist: %d done, %d seen, nothing open on this thread." % (e["checklist"]["done"], e["checklist"]["seen"]))
        th.append("")
        th.append("**History (newest first):**")
        for h in e["history"]:
            rf = " (re-fired x%d)" % (h["refires"] + 1) if h["refires"] else ""
            th.append("- %s [%s] %s%s" % (h["ts"][:16], h["session"], h["text"], rf))
        th.append("")
    _write(OUT_THREADS, "\n".join(th) + "\n")

    # ---- recent asks (the fallback when the verbatim last-100 block is unavailable)
    cutoff_d = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - DIGEST_HOURS * 3600))
    recent = [r for r in last if r["ts"] >= cutoff_d]
    recent_desc = sorted(recent, key=lambda r: r["ts"], reverse=True)[:DIGEST_MAX_PROMPTS]
    rc = ["[owner-prompts] what he asked in the last %d hours (%d prompts, newest first; every session, so you see the whole picture):" % (
        int(DIGEST_HOURS), len(recent))]
    for r in recent_desc:
        rc.append("  %s [%s] %s" % (_hhmm(r["ts"]), r["session"], r["text"][:220]))
    _write(OUT_RECENT, "\n".join(rc) + "\n")

    # ---- digest (bounded): pointers + checklist + threads. The verbatim prompts come from owner_prompt_context.py.
    dg = ["[owner-prompts] rebuilt %s. His last %d prompts across %d sessions: %s; the threads with their history and what we are "
          "working on: %s." % (now, len(last), sessions, _p(OUT_LAST), _p(OUT_THREADS))]
    if cl_md:
        dg.append("[owner-prompts] the task checklist (his asks per agent, done and seen marks): %s" % cl_md)
    dg.append("[owner-prompts] active threads (24 h / 7 d / all): " + "; ".join(
        "%s %d/%d/%d%s" % (e["thread"], e["last_24h"], e["last_7d"], e["count"],
                            (" (checklist open %d)" % e["checklist"]["open"]) if e["checklist"]["open"] else "")
        for e in elements if e["last_7d"]))
    if TOPICS is DEFAULT_TOPICS and os.path.exists(TOPICS_FILE):
        dg.append("[owner-prompts] WARNING: owner_prompt_topics.json exists but failed to load; the generic lexicon is in use - fix the JSON.")
    dg.append("[owner-prompts] before you say what he asked for, read the thread's history in OWNER_THREADS.md; a note is a pointer, not a verdict.")
    digest = "\n".join(dg) + "\n"
    if len(digest) > 4000:
        digest = digest[:3900] + "\n[owner-prompts] (digest truncated; read the files)\n"
    _write(OUT_DIGEST, digest)

    payload = {"built": now, "built_epoch": int(time.time()), "ledger_rows": total_seen, "read": how,
               "topics_warning": bool(TOPICS is DEFAULT_TOPICS and os.path.exists(TOPICS_FILE)), "owner_prompts_total": len(owner),
               "kept": len(last), "sessions": sessions, "checklist_dir": cl_dir, "checklist_entries": len(cl),
               "topics_file": TOPICS_FILE if os.path.exists(TOPICS_FILE) else "", "threads": elements,
               "prompts": [dict(r, text=r["text"][:JSON_TEXT_CAP]) for r in last],
               "seconds": round(time.time() - t0, 2)}
    _write(OUT_JSON, json.dumps(payload, ensure_ascii=False, indent=0))
    return payload


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        print(__doc__)
        return 0
    try:
        p = build(full="--full" in sys.argv[1:])
        print("owner_prompts_hourly: %d owner prompts kept; %d owner rows in the ledger (read %s), %d threads, checklist entries %d, %.1fs%s" % (
            p["kept"], p["ledger_rows"], p["read"], len(p["threads"]), p["checklist_entries"], p["seconds"],
            " TOPICS WARNING" if p["topics_warning"] else ""))
        return 0
    except Exception as e:  # never raise out of an hourly job
        sys.stderr.write("owner_prompts_hourly failed: %r\n" % (e,))
        return 0


if __name__ == "__main__":
    sys.exit(main())
