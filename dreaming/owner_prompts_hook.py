#!/usr/bin/env python3
"""owner_prompts_hook.py - the hook that hands every agent the owner's recent prompts, in parts the harness will show.

Owner orders (his clock): 2026-09-18 12:33, every agent gets his last 1000 prompts every hour; 15:5x, "the last 2 days and
the last 1k prompts, without it being overwhelming"; 16:05, the same for the last 3000; 18:2x, one uniform block every hour
(SIMPLE mode, his choice); 2026-09-20 16:5x, "from the 3k prompts I want all relevant ones pushed to agents every hour".

THE CEILING: Claude Code hands the model at most 10,000 chars of one hook command's output and spills the rest to a file
with a 2 KB preview; Codex keeps about the same and cuts, and Codex injects JSON only. So every delivery goes out in PARTS
bounded to 9,500 UTF-8 context bytes for Claude and 9,000 for Codex. User prompts and active tool steps drain the
queue. The Claude pre-tool path is its own command; Codex shares the existing reflex bridge's measured budget.

  SIMPLE (his choice)  one block per session per hour, ordered for a reader who may see only part 1:
         what he keeps saying (verdicts given more than once, newest cluster first; asks repeated on several days);
         RELEVANT TO THIS SESSION: every ask of the 3000 on each thread this session touched, newest first, with the
         open checklist items, the threads in the order touched, newest touch first; then his last 48 hours on every
         other thread, newest first. A thread touched between blocks arrives at once. At the hour a delivery still in
         flight continues with his newest asks put in front; a fresh block starts once the queue is drained.
  LAYERED (--mode layered)  WAKE (the 3000's shape, the last three hours) / SLICE (a thread's 48 hours on first touch,
         with one line per earlier day) / DELTA (hourly, only what is new); queued parts flow here too.
  ASK    he asks what he asked / what is going on / checklist: the verbatim last-100 block + the digest, in parts.
  PULL   python owner_prompts_hook.py --show "<thread>" [--hours 48] | --show all --hours 6 | --show 1000 | --show 100

Sources: owner_prompts_hourly.json (rebuilt hourly by owner_prompts_hourly.py from the ledger's owner rows - harness
runs and injected turns are origin "automation" there) plus a tail read of the live ledger. The verbatim last-100 block
comes from owner_prompt_context.py when it sits beside this file. Never blocks, never raises out (any error -> silent
exit 0). OWNER_PROMPTS_NO_PEER=1 forces the fallback on the ASK path; OWNER_PROMPTS_CHUNK_MAX overrides the part size.
Wired for Claude as its own UserPromptSubmit/PreToolUse command in ~/.claude/settings.json (python -s ... --hook --runtime claude)
and for Codex through ~/.codex/hooks.json plus codex_reflex_hook's pre-tool path. Queue updates are serialized and
saved atomically after stdout flush; emission is not a receiver-side receipt. --status, --selftest (30).
"""
import io
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict

if any(a in ("-h", "--help") for a in sys.argv[1:]):
    # Discovery is not execution: this file does its work at module scope, so the
    # guard must come before the first statement that runs.
    print(__doc__ or os.path.basename(__file__))
    raise SystemExit(0)


HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.expanduser("~")
STATE_DIR = os.environ.get("OWNER_PROMPTS_STATE_DIR") or os.path.join(HERE, ".owner_prompts_hook_state")
ASK_EVERY_S = 30 * 60          # the same ask again within this window is treated as an ordinary prompt
STATE_KEEP_DAYS = 14
WAKE_MAX = 12000
SLICE_MAX = 15000
DELTA_MAX = 6000
ASK_MAX = 6500
SLICE_HOURS = 48.0
WAKE_HOURS = 3.0
DELTA_EVERY_S = 60 * 60
DELTA_CHECK_S = 15 * 60
TAIL_BYTES = 3 * 1024 * 1024
ASK_RX = re.compile(r"(what (did|have) i (ask|tell|give)|what (are|is) (the |my |all the |every)?agents? (working|doing|on)"
                    r"|what is (going on|everyone (working|doing))|last (\d+ )?prompts|my (last|recent) (prompts|asks)"
                    r"|\bchecklist\b|what did i want|forget what i asked|what am i waiting)", re.I)
# the harness delivers a background job's completion as a UserPromptSubmit whose text is the notice itself; a relayed
# agent or mesh message, a reminder block and the usage-limit continuation arrive the same way (prompt_origin keeps the
# fuller list for the ledger; loop re-fires stay out of this one because they carry the user's words about the topic)
# Match the whole harness tag name, allowing attributes or whitespace before its closing bracket.
# A similarly named tag (for example task-notification-example) remains ordinary prompt text.
HARNESS_EVENT_RX = re.compile(r"^\s*(?:\[SYSTEM NOTIFICATION[^\]]*\]\s*)?(?:<(?:task-notification|agent-notification"
                              r"|cross-session-message|agent-message|system-reminder|local-command-stdout)(?=[\s/>])"
                              r"|Another Claude session sent a message\b|Your claude\.ai usage limit has reset\b)", re.I)

try:
    sys.path.insert(0, HERE)
    import owner_prompts_hourly as H
    ARCHIVE, LEDGER = H.ARCHIVE, H.LEDGER
except Exception:  # discovery failed: env vars or the default project memory
    H = None
    _mem = os.environ.get("BRAIN_MEMORY_DIR") or os.path.join(HOME, ".claude", "memory")
    ARCHIVE = os.environ.get("PROMPT_ARCHIVE_DIR") or os.path.join(_mem, "prompts")
    LEDGER = os.path.join(ARCHIVE, "PROMPTS.jsonl")
JSON = os.path.join(ARCHIVE, "owner_prompts_hourly.json")
DIGEST = os.path.join(ARCHIVE, "OWNER_PROMPTS_DIGEST.md")
RECENT = os.path.join(ARCHIVE, "OWNER_RECENT_ASKS.md")
LAST = os.path.join(ARCHIVE, "LAST_3000_OWNER_PROMPTS.md")
LAST1000 = os.path.join(ARCHIVE, "LAST_1000_OWNER_PROMPTS.md")
LOG = os.path.join(HERE, "owner_prompts_hook.log")
BACK_MAX = 4000
SLICE_48H_MAX = 11000
THREADS = os.path.join(ARCHIVE, "OWNER_THREADS.md")
PULL = "python %s --show" % os.path.join(HERE, "owner_prompts_hook.py").replace("\\", "/")
VERDICTS_MAX = 2600
MODE_FILE = os.path.join(HERE, "owner_prompts_mode")   # 'layered' (default) or 'simple'
SIMPLE_HOURS = 48.0
SIMPLE_MAX = 160000
SIMPLE_EVERY_S = 60 * 60


def mode():
    try:
        m = io.open(MODE_FILE, encoding="utf-8").read().strip().lower()
        return m if m in ("simple", "layered", "important") else "layered"
    except Exception:
        return "layered"


# WHEN THE SIMPLE BLOCK IS SENT (2026-09-24, his 01:33 ask "feed less prompts, but more relevant ones ... make sure that
# the method works"). Measured over 09-20 22:41 .. 09-24 14:27 from this hook's own log: 732 hourly blocks, 173 MB, and
# 78 pct of those bytes were a block re-sent to a session that already held it; his new asks between two blocks are
# about 0.5 pct. A busy 1M-context session compacts about every two hours (preTokens ~968k), and a compaction is the
# only thing that takes the block out of its context. So the CONTEXT schedule sends the full block when a context
# begins - the session's first delivery, the first fire after a compaction, or when the last full block is K hours
# old - and between those only his new asks, hourly. Every context still holds the whole block, so coverage is the
# hourly block's by construction; the bytes fall 37 pct at K=2, 47 at K=3, 50 at K=4 (memory
# owner_prompt_feed_fewer_but_relevant_measured_2026_09_24). The schedule file:
#   absent or "hourly"      the hourly block, his 2026-09-18 choice (the default)
#   "hourly shadow K"       the hourly block, plus a log of what the context schedule would have sent (no output change)
#   "context K"             the context schedule with a K-hour refresh; "context" alone = no age refresh
SCHEDULE_FILE = os.path.join(HERE, "owner_prompts_schedule")
SHADOW_LOG = os.path.join(HERE, "owner_prompts_shadow.log")
AGE_SLACK_S = 300          # a K-hour refresh is due five minutes early, so the hourly tick does not miss it by a minute
# The compaction record is read from the transcript's tail, stateless: measured 2026-09-24 on 14 Claude and 13 Codex
# compactions, the record sits 492-558 KB (Claude) and 434-441 KB (Codex) before the next point a hook fires (the
# summary and the re-attached context come first), so 3 MB is five times the deepest seen.
TAIL_SCAN = 3 * 1024 * 1024
COMPACT_MARK = {"claude": b'"compact_boundary"', "codex": b'"compacted"'}


def schedule():
    """(kind, K_hours or None, shadow_K or None or 'off'). kind is 'hourly' or 'context'. Any doubt reads as hourly."""
    try:
        toks = io.open(SCHEDULE_FILE, encoding="utf-8").read().strip().lower().split()
    except Exception:
        return ("hourly", None, "off")
    num = lambda s: (None if s in ("none", "0", "") else float(s))  # noqa: E731
    try:
        if toks and toks[0] == "context":
            return ("context", num(toks[1]) if len(toks) > 1 else None, "off")
        if len(toks) >= 2 and toks[0] == "hourly" and toks[1] == "shadow":
            return ("hourly", None, num(toks[2]) if len(toks) > 2 else None)
    except Exception:
        pass
    return ("hourly", None, "off")


def _codex_rollout(sess):
    """The Codex rollout file for a session id: ~/.codex/sessions/YYYY/MM/DD/rollout-<stamp>-<id>.jsonl, this month and
    the last one only (a glob over the whole tree costs seconds on this box)."""
    import glob as _g
    if not re.match(r"^[0-9a-f-]{16,}$", sess or ""):
        return ""
    base = os.path.join(HOME, ".codex", "sessions")
    t = time.localtime()
    months = [(t.tm_year, t.tm_mon), (t.tm_year - (t.tm_mon == 1), 12 if t.tm_mon == 1 else t.tm_mon - 1)]
    for y, m in months:
        hits = _g.glob(os.path.join(base, "%04d" % y, "%02d" % m, "*", "rollout-*-%s.jsonl" % sess))
        if hits:
            return max(hits, key=os.path.getmtime)
    return ""


def _compaction_epoch(line, runtime):
    """The epoch of a compaction record (Claude: type system / subtype compact_boundary; Codex: type compacted), or 0.
    Only a whole line that parses counts, so a marker quoted inside a tool result or a half-written line never does."""
    try:
        d = json.loads(line)
    except Exception:
        return 0.0
    if not isinstance(d, dict):
        return 0.0
    ok = d.get("type") == "compacted" if runtime == "codex" else (d.get("type") == "system" and d.get("subtype") == "compact_boundary")
    if not ok:
        return 0.0
    ts = str(d.get("timestamp") or "")
    try:
        import calendar
        frac = float("0" + ts[19:].rstrip("Z")) if ts[19:20] == "." else 0.0
        return calendar.timegm(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S")) + frac
    except Exception:
        return 0.0


def newest_compaction(scan, transcript, runtime, sess):
    """(epoch of the newest compaction record in the transcript's last TAIL_SCAN bytes or 0.0, scan, changed). Stateless
    about the transcript itself - compared against the time of the last full block, so a record is acted on once and
    nothing has to be saved on the silent tool steps. scan only remembers a Codex rollout's path (found by session id)
    or a recent miss. Never raises."""
    sc = dict(scan or {})
    changed = False
    try:
        p = transcript or sc.get("path") or ""
        if not p and runtime == "codex":
            # a miss is remembered for ten minutes: the lookup lists a month of rollout folders
            if time.time() - float(sc.get("miss_at") or 0) < 600:
                return 0.0, sc, False
            p = _codex_rollout(sess)
            sc = {"path": p} if p else {"miss_at": time.time()}
            changed = True
        if not p or not os.path.exists(p):
            return 0.0, sc, changed
        size = os.path.getsize(p)
        start = max(0, size - TAIL_SCAN)
        with io.open(p, "rb") as f:
            f.seek(start)
            chunk = f.read(size - start)
        mark = COMPACT_MARK.get(runtime, COMPACT_MARK["claude"])
        newest = 0.0
        if mark in chunk:
            for ln in chunk.split(b"\n"):
                if mark in ln:
                    newest = max(newest, _compaction_epoch(ln, runtime))
        return newest, sc, changed
    except Exception as e:
        _log_error("newest_compaction", e)
        return 0.0, sc, changed


def context_due(full_at, compacted, K, now):
    """Why the context schedule sends the whole block now: 'first', 'compact', 'age', or '' (not due)."""
    if not full_at:
        return "first"
    if compacted:
        return "compact"
    if K is not None and now - float(full_at) >= K * 3600 - AGE_SLACK_S:
        return "age"
    return ""


def _shadow_log(msg):
    try:
        if os.path.exists(SHADOW_LOG) and os.path.getsize(SHADOW_LOG) > LOG_MAX_BYTES:
            os.replace(SHADOW_LOG, SHADOW_LOG + ".1")
        with io.open(SHADOW_LOG, "a", encoding="utf-8") as lf:
            lf.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass
SLICE_VERDICTS_MAX = 1600
# His negative verdicts. Strong words only: 'again' and 'wrong' are everywhere and would drown the signal.
VERDICT_RX = re.compile(r"\b(shit|garbage|crap|terrible|awful|bad|boring|horrible|useless|disaster|unmitigated|ugly|not good"
                        r"|nothing (interesting|substantial|substinital)|not (interesting|what i wanted|impressed)|still (bad|shit|not)"
                        r"|not fixed|no interesting|waste of)\b", re.I)


def load_objects():
    """Named things per thread from the private topics JSON ('objects': label -> regex); a phrase key is used without it."""
    try:
        with io.open(H.TOPICS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        out = {}
        for t in data.get("topics") or []:
            objs = t.get("objects") or {}
            if objs:
                out[t["topic"]] = [(label, re.compile(rx, re.I)) for label, rx in objs.items()]
        return out
    except Exception:
        return {}


OBJECTS = load_objects()


def is_verdict(text):
    return bool(VERDICT_RX.search(text))


def _object_of(topic, text):
    for label, rx in OBJECTS.get(topic, []):
        if rx.search(text):
            return label
    return None


# Machine-shaped text that rides in the owner stratum (a pasted plugin list, a worker launch prompt, a loop preamble)
# must never become "what he keeps saying": on 2026-09-20 the standing block's two ask clusters were
# "<recommended_plugins> Here is a list of plugins" x17 and "You are independent NMKC publication referee 10" x10.
_MACHINE_RX = re.compile(u"^[\\s﻿]*(<|you are (an? |the |waking)|here is a list|goal set:|your claude|\\[dream|consulting the brain|caveat:|cross-session|another claude session)", re.I)
STANDING_MAX_AGE_D = 14


def standing(meta, topic=None, now=None, min_n=1):
    """What he keeps saying, across the whole window: verdicts clustered by the object he named (or a phrase key), and
    asks repeated on more than one day. Verdicts come newest-cluster first (yesterday's x8 outranks last week's x14),
    asks by count; clusters whose newest line is older than STANDING_MAX_AGE_D days are left out."""
    now = time.time() if now is None else now
    verd, asks = {}, {}
    for p in meta["prompts"]:
        if topic and p["topic"] != topic:
            continue
        if p["topic"] == "other" and topic is None:
            continue
        t = p["text"]
        if _MACHINE_RX.match(t):
            continue
        words = re.sub(r"[^a-z0-9 ]", "", t.lower()).split()
        if is_verdict(t):
            obj = _object_of(p["topic"], t)
            key = (p["topic"], obj or " ".join(words[:5]))
            it = verd.setdefault(key, {"kind": "verdict", "topic": p["topic"], "label": obj or (" ".join(words[:5]) + "..."), "n": 0, "days": set(), "first": p["ts"], "last": p["ts"], "newest": t, "sessions": set()})
            it["n"] += 1; it["days"].add(p["ts"][:10]); it["last"] = p["ts"]; it["newest"] = t; it["sessions"].add(p["session"])
        elif len(words) >= 4 and not re.match(r"(/loop|loop |your claudeai usage limit|crosssessionmessage|goal set)", " ".join(words[:5])) and "continue the task you were working on" not in t.lower():
            key = (p["topic"], " ".join(words[:6]))
            it = asks.setdefault(key, {"kind": "ask", "topic": p["topic"], "label": " ".join(words[:6]) + "...", "n": 0, "days": set(), "first": p["ts"], "last": p["ts"], "newest": t, "sessions": set()})
            it["n"] += 1; it["days"].add(p["ts"][:10]); it["last"] = p["ts"]; it["newest"] = t; it["sessions"].add(p["session"])
    items = [v for v in verd.values() if v["n"] >= max(2, min_n) or (v["n"] >= min_n and v["label"] and not v["label"].endswith("..."))]
    items += [a for a in asks.values() if a["n"] >= 3 and len(a["days"]) >= 2 and len(a["sessions"]) >= 2]
    cutoff = now - STANDING_MAX_AGE_D * 86400
    items = [it for it in items if _epoch(it["last"]) >= cutoff]
    items.sort(key=lambda it: (0, -_epoch(it["last"]), -it["n"]) if it["kind"] == "verdict" else (1, -it["n"], 0))
    return items


def standing_block(items, cap, heading, quote=140):
    if not items:
        return ""
    out = [heading]
    for it in items:
        q = it["newest"]
        q = q[:quote].rstrip() + ("..." if len(q) > quote else "")
        if it["kind"] == "verdict":
            out.append("  !! %s / %s: called bad x%d on %d day%s (%s .. %s) - newest: \"%s\"" % (
                _short(it["topic"]), it["label"], it["n"], len(it["days"]), "" if len(it["days"]) == 1 else "s", it["first"][5:16], it["last"][5:16], q))
        else:
            out.append("  .. %s: asked x%d on %d days (%s .. %s) - \"%s\"" % (_short(it["topic"]), it["n"], len(it["days"]), it["first"][5:10], it["last"][5:16], q))
    out.append("  if your task touches one of these, address the standing verdict before anything else and say what changed.")
    return _cap("\n".join(out) + "\n", cap, "  (more in OWNER_THREADS.md)")


# ----------------------------------------------------------------------------- data
def _epoch(ts):
    try:
        return time.mktime(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S"))
    except Exception:
        return 0.0


def load():
    """His prompts (the hourly JSON plus the live tail), the thread elements, the checklist dir."""
    meta = {"prompts": [], "threads": [], "checklist_dir": "", "built": "", "built_epoch": 0, "sessions": 0}
    try:
        with io.open(JSON, encoding="utf-8") as f:
            d = json.load(f)
        meta.update({"prompts": list(d.get("prompts") or []), "threads": d.get("threads") or [],
                     "checklist_dir": d.get("checklist_dir") or "", "built": d.get("built") or "",
                     "built_epoch": int(d.get("built_epoch") or 0), "sessions": int(d.get("sessions") or 0)})
    except Exception as e:
        _log_error("load.json", e)
    # the live tail: rows newer than the build (a prompt typed a minute ago belongs in the delta)
    if H is not None:
        try:
            newest = meta["prompts"][-1]["ts"] if meta["prompts"] else ""
            rows = []
            with io.open(LEDGER, "rb") as f:
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size - TAIL_BYTES))
                chunk = f.read().decode("utf-8", errors="replace")
            lines = chunk.split("\n")[1:] if size > TAIL_BYTES else chunk.split("\n")
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if str(r.get("ts") or "")[:19] > newest:
                    rows.append(r)
            fresh = H.owner_prompts(rows)
            for r in fresh:
                r["topic"] = H.topic_of(r["text"])
            meta["prompts"].extend(fresh)
        except Exception as e:
            _log_error("load.tail", e)
    # Stored hourly views predate newer redaction rules. Filter their full text
    # before line clipping or delivery chunking; a missing filter fails closed.
    from prompt_archive import redact_tree
    return redact_tree(meta)


def topic_of(text):
    try:
        return H.topic_of(text) if H is not None else "other"
    except Exception:
        return "other"


def _norm_key(text):
    return re.sub(r"[^a-z0-9 ]", "", text.lower())[:60]


def fold(items, window_s=3 * 3600):
    """Collapse his re-asks: the same opening words within a window count once, annotated (asked xN)."""
    out, last = [], {}
    for x in sorted(items, key=lambda r: r["ts"]):
        k = _norm_key(x["text"])
        t = _epoch(x["ts"])
        prev = last.get(k)
        if prev is not None and t - prev["_t"] <= window_s:
            prev["asked"] = prev.get("asked", 1) + 1
            prev["_t"] = t
            continue
        y = dict(x)
        y["_t"] = t
        out.append(y)
        last[k] = y
    return out


def line(x, cut):
    t = x["text"]
    if len(t) > cut:
        t = t[:cut - 3].rstrip() + "..."
    # a re-ask and a loop's echo are different things: the file this block is cut from writes "(re-fired xN)" for an
    # echo, and adding the two into one "asked" count made a looping prompt read "asked x118" to every agent
    asked, refired = x.get("asked", 1), x.get("refires", 0)
    tag = (" (asked x%d)" % asked if asked > 1 else "") + (" (re-fired x%d)" % refired if refired else "")
    mark = "!! " if is_verdict(x["text"]) else ""
    return "  %s%s %s [%s] %s%s" % (mark, x["ts"][5:10], x["ts"][11:16], x["session"], t, tag)


def _short(name):
    words = {"films and videos": "films", "trading simulations": "sims", "this computer and compute": "computer",
             "brain, hooks and agents": "brain", "live trading and accounts": "live", "math papers": "math",
             "open problems repo": "open", "the pitch deck": "deck", "novels and writing": "novels"}
    return words.get(name) or re.sub(r"[^a-z0-9]", "", name.lower())[:8] or "other"


def _cap(text, cap, note):
    if len(text) <= cap:
        return text
    cut = text[:cap - len(note) - 2]
    cut = cut[:cut.rfind("\n")] if "\n" in cut else cut
    return cut + "\n" + note + "\n"


# ----------------------------------------------------------------------------- blocks
def shape_table(prompts):
    """Prompts per day and thread: the last 14 days one row each, earlier days folded into ISO weeks."""
    import datetime
    topics = [t for t, _ in Counter(p["topic"] for p in prompts).most_common()]
    cols = [_short(t) for t in topics]
    days = sorted({p["ts"][:10] for p in prompts})
    recent = set(days[-14:])
    grid = defaultdict(Counter)
    for p in prompts:
        d = p["ts"][:10]
        if d in recent:
            key = d[5:]
        else:
            y, m, dd = [int(v) for v in d.split("-")]
            key = "wk%02d " % datetime.date(y, m, dd).isocalendar()[1]
        grid[key][p["topic"]] += 1
    order = sorted(k for k in grid if k.startswith("wk")) + [d[5:] for d in days if d in recent]
    head = "  day    " + " ".join("%7s" % c[:7] for c in cols) + "   total"
    rows = [head]
    for k in order:
        rows.append("  %-5s " % k + " ".join("%7d" % grid[k][t] for t in topics) + "  %6d" % sum(grid[k].values()))
    return "\n".join(rows)


def wake_block(meta, now=None):
    now = time.time() if now is None else now
    P = meta["prompts"]
    if not P:
        return ""
    days = len({p["ts"][:10] for p in P})
    cl = os.path.join(meta["checklist_dir"], "CHECKLIST.md").replace("\\", "/") if meta["checklist_dir"] else ""
    out = ["[owner-prompts] WAKE PICTURE. His last %d prompts (%d days, %d sessions; built %s, live tail added). Files: %s (all of them, by day; the newest 1000 also in LAST_1000_OWNER_PROMPTS.md), "
           "%s (each thread's history and what we are working on)%s. Pull a slice any time: %s \"<thread>\" --hours 48 | all --hours 6 | 1000 | 100." % (
               len(P), days, meta["sessions"], meta["built"], LAST.replace("\\", "/"), THREADS.replace("\\", "/"),
               (", the task checklist " + cl) if cl else "", PULL)]
    out.append("[owner-prompts] the shape of the last %d (prompts per day and thread; earlier days folded into weeks):" % len(P))
    out.append(shape_table(P))
    sb = standing_block(standing(meta, None, now, min_n=2)[:8], VERDICTS_MAX, "[owner-prompts] WHAT HE KEEPS SAYING (repeated across the whole window; !! = a negative verdict he has given more than once):", quote=120)
    if sb:
        out.append(sb.rstrip("\n"))
    out.append("[owner-prompts] what each thread is about right now (its newest two asks; you get a thread's full 48 hours the first time you touch it):")
    cutoff7 = now - 7 * 86400
    for e in meta["threads"]:
        if e.get("thread") == "other" or not e.get("last_7d"):
            continue
        items = [p for p in P if p["topic"] == e["thread"] and _epoch(p["ts"]) >= cutoff7]
        if not items:
            continue
        opn = e.get("checklist", {}).get("open", 0)
        out.append("  ## %s: %d asks in 24 h, %d in 7 days%s" % (e["thread"], e.get("last_24h", 0), e.get("last_7d", 0), (", checklist open %d" % opn) if opn else ""))
        for x in fold(items)[-2:]:
            out.append("  " + line(x, 150))
    recent = [p for p in P if _epoch(p["ts"]) >= now - WAKE_HOURS * 3600]
    out.append("[owner-prompts] his last %d hours verbatim, every session, oldest first (%d asks):" % (int(WAKE_HOURS), len(recent)))
    for x in fold(recent)[-16:]:
        out.append(line(x, 220))
    out.append("[owner-prompts] his later words win over an earlier task; before you say what he wants, read the thread's history, and never mark the checklist seen yourself.")
    return _cap("\n".join(out) + "\n", WAKE_MAX, "[owner-prompts] (wake picture cut to size; the files hold the rest)")


def slice_block(meta, topic, hours=SLICE_HOURS, now=None, cap=SLICE_MAX):
    now = time.time() if now is None else now
    cutoff = now - hours * 3600
    all_items = [p for p in meta["prompts"] if p["topic"] == topic]
    items = [p for p in all_items if _epoch(p["ts"]) >= cutoff]
    older = [p for p in all_items if _epoch(p["ts"]) < cutoff]
    if not items and not older:
        return ""
    folded = fold(items)
    e = next((t for t in meta["threads"] if t.get("thread") == topic), {})
    days_older = sorted({p["ts"][:10] for p in older})
    out = ["[owner-prompts] THREAD %s: %d asks in the last %d hours (%d lines after folding re-asks, oldest first, %d sessions)%s." % (
        topic, len(items), int(hours), len(folded), len({p["session"] for p in items}),
        ("; before that %d asks over %d days, one line per day" % (len(older), len(days_older))) if older else "")]
    opens = e.get("checklist", {}).get("open_items") or []
    if opens:
        out.append("  open on the task checklist: " + "; ".join("%s %s" % (o["id"], (o.get("text") or "")[:80]) for o in opens[:6]))
    sb = standing_block(standing(meta, topic, now)[:8], SLICE_VERDICTS_MAX, "  what he keeps saying on this thread (whole window; !! = a repeated negative verdict):")
    if sb:
        out.append(sb.rstrip("\n"))
    back = []
    if older:
        by_day = defaultdict(list)
        for p in older:
            by_day[p["ts"][:10]].append(p)
        for d in days_older:
            ps = sorted(by_day[d], key=lambda r: r["ts"])
            newest = ps[-1]["text"]
            back.append("  %s (%d) %s" % (d[5:], len(ps), newest[:110].rstrip() + ("..." if len(newest) > 110 else "")))
        if sum(len(l) + 1 for l in back) > BACK_MAX:
            keep, total = [], 0
            for l in reversed(back):
                if total + len(l) + 1 > BACK_MAX:
                    break
                keep.append(l)
                total += len(l) + 1
            back = ["  (%d earlier days left out; pull them with --hours %d)" % (len(days_older) - len(keep), int(hours) + 24 * len(days_older))] + list(reversed(keep))
        back = ["  further back (day, asks that day, the day" + chr(39) + "s newest ask):"] + back
    body = [line(x, 150) for x in folded]
    if sum(len(l) + 1 for l in body) > SLICE_48H_MAX:
        # trim the oldest NEUTRAL lines first; a verdict line (!!) is never dropped for size
        verdict_lines = [l for l in body if l.startswith("  !! ")]
        budget = SLICE_48H_MAX - sum(len(l) + 1 for l in verdict_lines)
        keep, total = set(), 0
        for i in range(len(body) - 1, -1, -1):
            l = body[i]
            if l.startswith("  !! "):
                keep.add(i)
                continue
            if total + len(l) + 1 > budget:
                continue
            keep.add(i)
            total += len(l) + 1
        dropped = len(body) - len(keep)
        body = ["  (%d older lines of these %d hours left out, no verdict among them; they are in %s)" % (dropped, int(hours), THREADS.replace("\\", "/"))] + [l for i, l in enumerate(body) if i in keep]
    if body:
        body = ["  the last %d hours:" % int(hours)] + body
    return _cap("\n".join(out + back + body) + "\n", cap, "[owner-prompts] (thread slice cut to size; pull the thread for the rest)")


def delta_block(meta, since_epoch, now=None, cut=150, cap=DELTA_MAX):
    now = time.time() if now is None else now
    new = [p for p in meta["prompts"] if _epoch(p["ts"]) > since_epoch]
    if not new:
        return ""
    by = defaultdict(list)
    for p in new:
        by[p["topic"]].append(p)
    out = ["[owner-prompts] SINCE YOU LAST LOOKED (%s): %d new asks across %d threads, every session, oldest first." % (
        time.strftime("%H:%M", time.localtime(since_epoch)), len(new), len(by))]
    for topic, items in sorted(by.items(), key=lambda kv: -len(kv[1])):
        out.append("  ## %s (%d)" % (topic, len(items)))
        for x in fold(items):
            out.append("  " + line(x, cut))
    text = "\n".join(out) + "\n"
    return _cap(text, cap, "[owner-prompts] (delta cut to size; pull the thread for the rest)") if cap else text


def context_delta(meta, since_epoch, now=None):
    """His new asks since the last delivery, for the context schedule: every ask in full as the hourly block prints it
    (a pasted document cut at 4000 chars), newest first within each thread, the session's context already holding the
    rest. Uncapped: it goes out in parts like the block, so nothing he said is dropped for size."""
    now = time.time() if now is None else now
    new = [p for p in meta["prompts"] if _epoch(p["ts"]) > since_epoch]
    if not new:
        return ""
    by = defaultdict(list)
    for p in new:
        by[p["topic"]].append(p)
    out = ["[owner-prompts] HIS NEW ASKS since %s (%d across %d threads, every session; your context already holds the block "
           "sent at the start of this context, and a fresh one comes after a compaction):" % (
               time.strftime("%H:%M", time.localtime(since_epoch)), len(new), len(by))]
    for topic, items in sorted(by.items(), key=lambda kv: -max(_epoch(p["ts"]) for p in kv[1])):
        out.append("  ## %s (%d)" % (topic, len(items)))
        folded = fold(items)
        folded.reverse()
        out.extend(line(x, 4000) for x in folded)
    out.append("[owner-prompts] his later words win over an earlier task; never mark the checklist seen yourself.")
    return "\n".join(out) + "\n"


def _read(path, cap):
    try:
        with io.open(path, encoding="utf-8", errors="replace") as f:
            t = f.read()
    except Exception:
        return ""
    from prompt_archive import redact
    t = redact(t)[0]
    if len(t) > cap:
        t = t[:cap - 60] + "\n[owner-prompts] (truncated; read %s)\n" % os.path.basename(path)
    return t


def verbatim_block():
    """The last-100 block from owner_prompt_context.py; empty when unavailable or switched off."""
    if os.environ.get("OWNER_PROMPTS_NO_PEER"):
        return ""
    try:
        import owner_prompt_context as opc
        from prompt_archive import redact_tree
        view = opc.load_view(path=LEDGER)
        return opc.render(redact_tree(view.get("prompts") or [])) or ""
    except Exception as e:
        _log_error("verbatim_block (falling back to the 3-hour file)", e)
        return ""


def ask_block():
    parts = []
    vb = verbatim_block()
    parts.append(vb.rstrip("\n") if vb else _read(RECENT, 4000).rstrip("\n"))
    dg = _read(DIGEST, ASK_MAX)
    if dg:
        parts.append(dg.rstrip("\n"))
    return ("\n".join(p for p in parts if p) + "\n") if any(parts) else ""


RELEVANT_LINE_CHARS = 600   # each relevant prompt in full up to this (the LAST_3000 file's own cut); pasted documents beyond it are trimmed
# THE CEILING IS THE HARNESS'S, NOT OURS. Claude Code persists any ONE hook command's context above 10,000 chars to a
# file under the session's tool-results and shows the model a 2 KB preview with the path (its persist threshold,
# read out of the 2.0.x bundle on 2026-09-20; measured the same day: 16.1, 36.1 and 65.6 KB contexts all spilled,
# 5.7 KB reached the model). A spilled block is not delivered - the model would have to go and read the file. So a
# delivery goes out in PARTS of at most CHUNK_MAX chars, one part per prompt, and this hook is wired as its OWN hook
# command (settings.json), not inside the dispatcher, so the other hooks' lines do not eat its budget. A part is the
# body plus its header line; HEADER_RESERVE keeps the two together under the cap. OWNER_PROMPTS_CHUNK_MAX overrides
# the cap (a runtime with a different ceiling, a test); under MIN_PART the hook waits for the next prompt.
HARNESS_INLINE_MAX = 10000
# Codex keeps about the same (a 15,003-char block arrived as 10,036 chars under a "truncated output" warning, 2026-09-20)
# but it cuts instead of spilling and may count tokens, so a Codex part gets a wider margin.
CODEX_INLINE_MAX = 10000
CHUNK_MAX = int(os.environ.get("OWNER_PROMPTS_CHUNK_MAX", str(HARNESS_INLINE_MAX - 500)))
MIN_PART = 2500
HEADER_RESERVE = 220


def thread_condensed(meta, topic, now=None, hours=SIMPLE_HOURS, cap=None):
    """EVERY relevant prompt of one thread from the 3000 window - his 2026-09-20 16:5x order: "from the 3k prompts I want
    all relevant ones pushed to agents every hour" - preceded by how many and when, what is open on the checklist for
    the thread, and what he keeps saying on it. The prompts older than the verbatim window are listed in full (each up
    to RELEVANT_LINE_CHARS), oldest first, re-asks folded; the last SIMPLE_HOURS hours are in the shared block below.
    The earlier condensed form (one line per day, 5 KB per thread) is superseded; size is handled by chunked delivery,
    never by dropping lines."""
    now = time.time() if now is None else now
    cutoff = now - hours * 3600
    all_items = [p for p in meta["prompts"] if p["topic"] == topic]
    if not all_items:
        return ""
    n_recent = sum(1 for p in all_items if _epoch(p["ts"]) >= cutoff)
    e = next((t for t in meta["threads"] if t.get("thread") == topic), {})
    # NEWEST FIRST, on purpose: a part is at most CHUNK_MAX chars and a session that sends few prompts an hour
    # sees only the first parts, so the first part must carry his newest words on the thread, not the oldest.
    folded = fold(all_items)
    folded.reverse()
    out = ["  ## %s: %d asks in the window (%s .. %s), %d of them in the last %d hours; %d lines after folding re-asks, NEWEST FIRST" % (
        topic, len(all_items), all_items[0]["ts"][:10], all_items[-1]["ts"][:16], n_recent, int(hours), len(folded))]
    opens = e.get("checklist", {}).get("open_items") or []
    if opens:
        out.append("  open on the task checklist: " + "; ".join("%s %s" % (o["id"], (o.get("text") or "")[:80]) for o in opens[:6]))
    sb = standing_block(standing(meta, topic, now)[:5], SLICE_VERDICTS_MAX, "  what he keeps saying on this thread:")
    if sb:
        out.append(sb.rstrip("\n"))
    out.append("  every ask on this thread, newest first:")
    out.extend(line(x, RELEVANT_LINE_CHARS) for x in folded)
    text = "\n".join(out) + "\n"
    return _cap(text, cap, "  (thread cut to size; pull it for the rest)") if cap else text


def chunk_text(text, limit=None):
    """Split without losing characters; even a single long line must fit."""
    limit = CHUNK_MAX if limit is None else limit
    if limit < 1:
        raise ValueError("positive chunk limit required")
    parts = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit) + 1
        if not cut:
            cut = text.rfind(" ", 0, limit) + 1 or limit
        parts.append(text[:cut])
        text = text[cut:]
    if text or not parts:
        parts.append(text)
    return parts


def part_limit():
    """The body size of one part after reserving its header; admission checks MIN_PART."""
    return max(1, CHUNK_MAX - HEADER_RESERVE)


def deliver(text, st, label, now):
    from prompt_archive import redact
    text = redact(text)[0]
    """The first part now, the rest queued on the session state for its next prompts (one part per prompt). Every
    part, header included, fits under CHUNK_MAX so the harness hands it to the model inline."""
    parts = chunk_text(text, part_limit())
    n = len(parts)
    if n == 1:
        return [text]
    stamp = time.strftime("%H:%M", time.localtime(now))
    out = ["[owner-prompts] %s - part 1 of %d (remaining parts follow during prompts and active tool steps)\n%s" % (label, n, parts[0])]
    st["pending"] = ["[owner-prompts] %s - part %d of %d (continuing the delivery started %s)\n%s" % (label, i + 2, n, stamp, p) for i, p in enumerate(parts[1:])]
    return out


def refit(part, limit):
    """A queued part built under a larger budget is split at a line boundary to fit today's: (now, remainder or None)."""
    if len(part) <= limit:
        return part, None
    head, _, body = part.partition("\n")
    if not body or len(head) + 2 >= limit:
        raise ValueError("part header cannot fit the context budget")
    pieces = chunk_text(body, max(1, limit - len(head) - 1))
    if len(pieces) == 1:
        return part, None
    tag = " (split to fit this prompt)"
    rest_head = head if head.endswith(tag) else head + tag   # the tag once; a remainder refitted again must not grow its header
    return head + "\n" + pieces[0], rest_head + "\n" + "".join(pieces[1:])


def simple_block(meta, now=None, threads=()):
    """One block for every session, every hour: what he keeps saying, then the relevant 3000 condensed for the threads
    THIS session's prompts have touched (its own part, everything else is shared), then his last SIMPLE_HOURS (48)
    hours verbatim, every session, oldest first, then where the whole 3000 lives. Measured 2026-09-20: 74-118 KB per
    session per hour before the relevant section; a thread's condensed section is at most CONDENSED_MAX chars."""
    now = time.time() if now is None else now
    P = meta["prompts"]
    if not P:
        return ""
    cl = os.path.join(meta["checklist_dir"], "CHECKLIST.md").replace("\\", "/") if meta["checklist_dir"] else ""
    out = ["[owner-prompts] HIS LAST %d HOURS, every session, plus every ask of his last %d prompts (%d days) on the threads this session touched"
           " - the hourly block, delivered in parts, one per prompt, newest words first in each section. The whole window is in %s%s; pull any thread with %s \"<thread>\" --hours 480." % (
               int(SIMPLE_HOURS), len(P), len({p["ts"][:10] for p in P}), LAST.replace("\\", "/"), ("; the task checklist is " + cl) if cl else "", PULL)]
    sb = standing_block(standing(meta, None, now, min_n=2)[:8], VERDICTS_MAX, "[owner-prompts] WHAT HE KEEPS SAYING (!! = a negative verdict given more than once; newest cluster first):", quote=120)
    if sb:
        out.append(sb.rstrip("\n"))
    covered = set()
    rel = []
    for t in threads:
        if not t or t == "other":
            continue
        r = thread_condensed(meta, t, now)
        if r:
            rel.append(r)
            covered.add(t)
    if rel:
        out.append("[owner-prompts] RELEVANT TO THIS SESSION (the threads your prompts touched: every ask of the %d-prompt window on each, in full, newest first, with what is open on the checklist):" % len(P))
        out.extend(r.rstrip("\n") for r in rel)
    recent = [p for p in P if _epoch(p["ts"]) >= now - SIMPLE_HOURS * 3600 and p["topic"] not in covered]
    folded = fold(recent)
    folded.reverse()
    out.append("[owner-prompts] the last %d hours on every%s thread (%d asks, newest first; re-asks folded; only a pasted document longer than 4000 chars is cut):" % (
        int(SIMPLE_HOURS), " other" if covered else "", len(recent)))
    for x in folded:
        out.append(line(x, 4000))
    out.append("[owner-prompts] his later words win over an earlier task; never mark the checklist seen yourself.")
    return "\n".join(out) + "\n"


# ----------------------------------------------------------------------------- session state
def read_payload():
    try:
        if sys.stdin.isatty():
            return {}
        try:
            sys.stdin.reconfigure(encoding="utf-8", errors="replace")  # the payload is UTF-8; Windows defaults to cp1252
        except Exception:
            pass
        raw = sys.stdin.read()
    except Exception:
        raw = ""
    try:
        return json.loads(raw) if raw.strip() else {}
    except Exception:
        return {}


def session_id(payload, runtime):
    for k in ("session_id", "sessionId", "session", "thread_id"):
        v = payload.get(k)
        if v:
            return str(v)
    for k in ("CLAUDE_SESSION_ID", "CODEX_THREAD_ID", "CODEX_SESSION_ID"):
        v = os.environ.get(k)
        if v:
            return v
    return ""


LOG_MAX_BYTES = 2 * 1024 * 1024


def _log(msg):
    """Append one line; past 2 MB the log rolls to .1 (one generation kept), so it cannot grow without bound."""
    try:
        if os.path.exists(LOG) and os.path.getsize(LOG) > LOG_MAX_BYTES:
            os.replace(LOG, LOG + ".1")
        with io.open(LOG, "a", encoding="utf-8") as lf:
            lf.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


def _log_error(where, exc):
    _log("ERROR %s: %s: %s" % (where, type(exc).__name__, str(exc)[:200]))


def prune_state(now=None):
    """Drop per-session state older than STATE_KEEP_DAYS; at most once a day (a marker file carries the last run)."""
    now = time.time() if now is None else now
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        marker = os.path.join(STATE_DIR, ".pruned")
        if os.path.exists(marker) and now - os.path.getmtime(marker) < 86400:
            return 0
        n = 0
        for name in os.listdir(STATE_DIR):
            p = os.path.join(STATE_DIR, name)
            if name.endswith(".json") and now - os.path.getmtime(p) > STATE_KEEP_DAYS * 86400:
                os.remove(p)
                n += 1
        with io.open(marker, "w", encoding="utf-8") as f:
            f.write(str(int(now)))
        return n
    except Exception as e:
        _log_error("prune_state", e)
        return 0


def state_path(sess):
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", sess)[:80]
    return os.path.join(STATE_DIR, safe + ".json")


def load_state(sess):
    try:
        with io.open(state_path(sess), encoding="utf-8") as f:
            st = json.load(f)
        return st if isinstance(st, dict) else {}
    except Exception:
        return {}


def save_state(sess, st):
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        st["at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        with io.open(state_path(sess), "w", encoding="utf-8") as f:
            json.dump(st, f)
    except Exception:
        pass


def _drain(st):
    """The next queued part, refitted to today's budget; the remainder goes back in front."""
    pend = list(st.get("pending") or [])
    if not pend:
        return None
    now_part, rest = refit(pend.pop(0), CHUNK_MAX)
    if rest:
        pend.insert(0, rest)
    st["pending"] = pend
    return now_part


def _decide_context(st, served, touched, topic, new_thread, comp_at, K, now):
    """The context schedule (SCHEDULE_FILE): the whole simple block when a context begins (first delivery, a compaction
    recorded after the last full block, or the last full block K hours old), his new asks hourly in between, a newly
    touched thread's history at once. st['_save'] asks the caller to keep a state change that printed nothing."""
    parts = []
    served_at = float(st.get("served_at") or 0)
    # a session carried over from the hourly schedule counts its last hourly block as the block its context holds
    full_at = st.get("full_at") or (served_at if st.get("mode") == "simple" else None)
    why = context_due(full_at, bool(full_at) and comp_at > float(full_at), K, now)
    if why == "age" and st.get("pending"):
        # the refresh is due while the last block is still in flight: his newest asks go in front and the queue
        # continues, as the hourly schedule does at the hour
        d = context_delta(load(), served_at, now)
        if d:
            st["pending"] = ["[owner-prompts] NEW SINCE %s (the queued parts of your block continue after this)\n%s" % (
                time.strftime("%H:%M", time.localtime(served_at)), d.rstrip("\n") + "\n")] + list(st["pending"])
        p = _drain(st)
        if p:
            parts.append(p)
        st.update({"full_at": now, "served_at": now, "fires": int(st.get("fires") or 0) + 1, "why": "context-age-continue", "mode": "simple"})
    elif why:
        st["pending"] = []
        parts.extend(deliver(simple_block(load(), now, threads=list(reversed(touched))), st, "HIS PROMPTS", now))
        st.update({"full_at": now, "served_at": now, "fires": int(st.get("fires") or 0) + 1, "why": "context-" + why, "mode": "simple"})
    elif now - served_at >= SIMPLE_EVERY_S:
        d = context_delta(load(), served_at, now)
        st["served_at"] = now
        st["_save"] = 1          # the hour is spent even when nothing new came: kept, or every later fire reloads
        if d:
            carry = list(st.get("pending") or [])
            parts.extend(deliver(d, st, "HIS NEW ASKS", now))
            st["pending"] = list(st.get("pending") or []) + carry
            st.update({"fires": int(st.get("fires") or 0) + 1, "why": "context-delta"})
    elif new_thread:
        sec = thread_condensed(load(), topic, now)
        if sec:
            text = "[owner-prompts] RELEVANT TO THIS SESSION - a thread your prompt just touched: every relevant prompt of the window in full:\n" + sec.rstrip("\n") + "\n"
            carry = list(st.get("pending") or [])
            parts.extend(deliver(text, st, "THREAD " + topic, now))
            st["pending"] = list(st.get("pending") or []) + carry
            st.update({"fires": int(st.get("fires") or 0) + 1, "why": "context-thread:" + _short(topic)})
    elif st.get("pending"):
        p = _drain(st)
        if p:
            parts.append(p)
        st.update({"fires": int(st.get("fires") or 0) + 1, "why": "context-part"})
    st["threads"] = sorted(served)
    st["mode"] = "simple"
    return [p for p in parts if p], st


def _shadow(st, sess, runtime, comp_at, K, now):
    """What the context schedule would send at this fire, beside the hourly block - logged, never sent. Its own clock
    lives in st['shadow']; the bytes of a would-be full block are read afterwards from the main log (the session's
    own block of that hour), the bytes of a would-be delta are rendered here."""
    sh = dict(st.get("shadow") or {})
    if not sh and st.get("mode") == "simple" and st.get("served_at"):
        # a session already running when the shadow starts: its last hourly block is the block its context holds, as
        # it would be at a real switch (no burst)
        sh = {"full_at": float(st["served_at"]), "served_at": float(st["served_at"]), "carried": 1}
        st["_save"] = 1
    compacted = bool(sh.get("full_at")) and comp_at > float(sh.get("full_at"))
    why = context_due(sh.get("full_at"), compacted, K, now)
    ev = ""
    if why:
        sh.update({"full_at": now, "served_at": now})
        ev = "full:" + why
    elif now - float(sh.get("served_at") or 0) >= SIMPLE_EVERY_S:
        d = context_delta(load(), float(sh.get("served_at") or 0), now)
        sh["served_at"] = now
        ev = "delta:%dB" % len(d.encode("utf-8"))
    if compacted:
        ev = (ev + " " if ev else "") + "compaction-at=%s" % time.strftime("%H:%M:%S", time.localtime(comp_at))
    st["shadow"] = sh
    if ev:
        st["_save"] = 1
        _shadow_log("%s %s K=%s %s" % (runtime, (sess or "")[:8], K, ev))


def decide(event, sess, prompt, st, now=None, transcript="", runtime="claude"):
    """Mode "important": owner_prompts_brains.py decides (his important prompts, served by brain).
    Every other mode: _decide_simple below, unchanged."""
    if mode() == "important":
        import owner_prompts_brains
        return owner_prompts_brains.decide(sys.modules[__name__], event, sess, prompt, st, now, transcript=transcript, runtime=runtime)
    return _decide_simple(event, sess, prompt, st, now, transcript, runtime)


def _decide_simple(event, sess, prompt, st, now=None, transcript="", runtime="claude"):
    """Which layers this fire prints. Returns (parts, new_state). transcript (the payload's transcript_path; for Codex
    the rollout is found by session id) is read only by the context schedule and its shadow, for compactions."""
    now = time.time() if now is None else now
    st = dict(st or {})
    served = set(st.get("threads") or [])
    parts = []
    if event == "SessionStart":
        meta = load()
        selected_mode = mode()
        text = simple_block(meta, now) if selected_mode == "simple" else wake_block(meta, now)
        parts.extend(deliver(text, st, "HIS PROMPTS", now))
        st.update({"served_at": now, "threads": sorted(served), "fires": int(st.get("fires") or 0) + 1, "checked_at": now, "why": "simple" if selected_mode == "simple" else "wake", "mode": selected_mode})
        prune_state(now)
        return parts, st
    if prompt and HARNESS_EVENT_RX.match(prompt):
        # a background job's completion notice is the harness speaking, not the user: it carries the agent's own task
        # description, and a film-worded one read as the films thread and queued 25 parts of it into an unrelated
        # session. It opens no thread and asks nothing; the timed layers (the hourly block, a queued remainder) still flow
        prompt = ""
    if prompt and ASK_RX.search(prompt):
        key = _norm_key(prompt)
        if not (st.get("ask_key") == key and now - float(st.get("ask_at") or 0) < ASK_EVERY_S):
            ab = ask_block()
            if ab:
                # the verbatim last hundred can run to 15 KB: in parts like the hourly block, or the harness spills it
                st["pending"] = []
                parts.extend(deliver(ab, st, "WHAT HE ASKED", now))
            st.update({"served_at": now, "fires": int(st.get("fires") or 0) + 1, "checked_at": now, "threads": sorted(served),
                       "ask_at": now, "ask_key": key, "why": "ask"})
            return [p for p in parts if p], st
    if mode() == "simple":
        # the session's threads accumulate from its prompts; the hourly block carries their condensed history, and a
        # thread touched for the first time between blocks gets its condensed section at once (his 16:05 order: the
        # relevant 3000 reach the agent, not only the shared 48 hours)
        topic = topic_of(prompt) if prompt else "other"
        new_thread = topic != "other" and topic not in served
        if new_thread:
            served.add(topic)
        # the threads in the order the session touched them, newest touch first: the block's RELEVANT section lists them
        # in that order, so the first parts carry the thread the session is on now
        touched = [t for t in (st.get("touched") or sorted(served)) if t in served]
        if new_thread and topic not in touched:
            touched.append(topic)
        st["touched"] = touched
        kind, K, shadow_K = schedule()
        comp_at = 0.0
        if kind == "context" or shadow_K != "off":
            comp_at, st["scan"], changed = newest_compaction(st.get("scan"), transcript, runtime, sess)
            if changed:
                st["_save"] = 1
        if kind == "context":
            return _decide_context(st, served, touched, topic, new_thread, comp_at, K, now)
        if shadow_K != "off":
            try:
                _shadow(st, sess, runtime, comp_at, shadow_K, now)
            except Exception as e:
                _log_error("shadow", e)
        # a session that last received the layered shape gets its first simple block at its next prompt, not an hour later
        if st.get("mode") != "simple" or now - float(st.get("served_at") or 0) >= SIMPLE_EVERY_S:
            if st.get("mode") == "simple" and st.get("pending"):
                # a delivery still in flight at the hour: his newest asks go in front of it and the queue continues -
                # restarting it would show a session that sends a few prompts an hour the same first parts every hour
                d = delta_block(load(), float(st.get("served_at") or 0), now)
                pend = list(st["pending"])
                if d:
                    pend.insert(0, "[owner-prompts] NEW THIS HOUR (the queued parts of your hourly block continue after this)\n" + d.rstrip("\n") + "\n")
                now_part, rest = refit(pend.pop(0), CHUNK_MAX)
                parts.append(now_part)
                if rest:
                    pend.insert(0, rest)
                st["pending"] = pend
                st.update({"served_at": now, "fires": int(st.get("fires") or 0) + 1, "checked_at": now, "why": "simple-continue", "mode": "simple"})
            else:
                st["pending"] = []
                parts.extend(deliver(simple_block(load(), now, threads=list(reversed(touched))), st, "HIS PROMPTS", now))
                st.update({"served_at": now, "fires": int(st.get("fires") or 0) + 1, "checked_at": now, "why": "simple", "mode": "simple"})
        elif new_thread:
            sec = thread_condensed(load(), topic, now)
            if sec:
                text = "[owner-prompts] RELEVANT TO THIS SESSION - a thread your prompt just touched: every relevant prompt of the window in full (the shared block follows on the hour):\n" + sec.rstrip("\n") + "\n"
                # a queued remainder of the hourly block keeps flowing after this thread's own parts
                carry = list(st.get("pending") or [])
                parts.extend(deliver(text, st, "THREAD " + topic, now))
                st["pending"] = list(st.get("pending") or []) + carry
                st.update({"fires": int(st.get("fires") or 0) + 1, "why": "simple-thread:" + _short(topic)})
        elif st.get("pending"):
            pend = list(st["pending"])
            now_part, rest = refit(pend.pop(0), CHUNK_MAX)
            parts.append(now_part)
            if rest:
                pend.insert(0, rest)
            st["pending"] = pend
            st.update({"fires": int(st.get("fires") or 0) + 1, "why": "simple-part"})
        st["threads"] = sorted(served)
        return [p for p in parts if p], st
    if st.get("pending"):
        # layered mode: a queued remainder (an ask block in parts) flows one part per prompt here too
        pend = list(st["pending"])
        now_part, rest = refit(pend.pop(0), CHUNK_MAX)
        if rest:
            pend.insert(0, rest)
        st["pending"] = pend
        st.update({"fires": int(st.get("fires") or 0) + 1, "why": "layered-part", "threads": sorted(served)})
        return [now_part], st
    meta = None
    why = []
    if not st.get("served_at"):  # a session that never saw a wake (Codex has no SessionStart)
        meta = load()
        parts.append(wake_block(meta, now))
        st["served_at"] = now
        why.append("wake")
    topic = topic_of(prompt) if prompt else "other"
    if topic != "other" and topic not in served:
        meta = meta or load()
        s = slice_block(meta, topic, SLICE_HOURS, now)
        if s:
            parts.append(s)
            why.append("slice:" + _short(topic))
        served.add(topic)
    elif now - float(st.get("checked_at") or 0) >= DELTA_CHECK_S and now - float(st.get("served_at") or 0) >= DELTA_EVERY_S:
        meta = meta or load()
        d = delta_block(meta, float(st["served_at"]), now)
        st["checked_at"] = now
        if d:
            parts.append(d)
            st["served_at"] = now
            why.append("delta")
    st["threads"] = sorted(served)
    st["why"] = "+".join(why)
    st["mode"] = "layered"
    if parts:
        st["fires"] = int(st.get("fires") or 0) + 1
    return [p for p in parts if p], st


# ----------------------------------------------------------------------------- entry points
def show(args):
    what = args[0] if args else "all"
    hours = SLICE_HOURS
    if "--hours" in args:
        try:
            hours = float(args[args.index("--hours") + 1])
        except Exception:
            pass
    meta = load()
    if what == "100":
        return ask_block()
    if what == "1000":
        return "[owner-prompts] his last %d prompts: %s\n%s\n" % (len(meta["prompts"]), LAST.replace("\\", "/"), shape_table(meta["prompts"]))
    if what == "wake":
        return wake_block(meta)
    if what == "all":
        now = time.time()
        items = [p for p in meta["prompts"] if _epoch(p["ts"]) >= now - hours * 3600]
        return "[owner-prompts] every thread, last %g hours, %d asks, oldest first:\n%s\n" % (hours, len(items), "\n".join(line(x, 220) for x in fold(items)))
    names = [t["thread"] for t in meta["threads"]] or sorted({p["topic"] for p in meta["prompts"]})
    match = next((n for n in names if n == what), None) or next((n for n in names if what.lower() in n.lower()), None)
    if not match:
        return "[owner-prompts] no thread named %r; threads: %s\n" % (what, ", ".join(names))
    return slice_block(meta, match, hours, cap=10 ** 6) or "[owner-prompts] nothing on %s in the last %g hours\n" % (match, hours)


def status():
    """One screen for a human or an agent: is the hourly build fresh, is the hook wired once, what fired, what failed."""
    now = time.time()
    out = ["mode: %s" % mode()]
    try:
        import glob as _g
        recent = [json.load(io.open(f, encoding="utf-8")) for f in _g.glob(os.path.join(STATE_DIR, "*.json")) if now - os.path.getmtime(f) < 2 * 3600]
        out.append("sessions active in the last 2 h: %d, of which served under the current mode: %d" % (len(recent), sum(1 for d in recent if d.get("mode") == mode())))
    except Exception:
        pass
    now = time.time()
    try:
        age = (now - os.path.getmtime(JSON)) / 60.0
        with io.open(JSON, encoding="utf-8") as f:
            d = json.load(f)
        P = d.get("prompts") or []
        out.append("build: %s (%.0f min ago, %s) - %d prompts %s..%s, %d threads, read %s%s" % (
            d.get("built"), age, "FRESH" if age < 120 else "STALE - the hourly task did not land", len(P),
            P[0]["ts"][:10] if P else "?", P[-1]["ts"][:16] if P else "?", len(d.get("threads") or []), d.get("read", "?"),
            " TOPICS WARNING" if d.get("topics_warning") else ""))
        if len(P) < 2000:
            out.append("WARNING: the window holds %d prompts, expected about 3000 - the ledger may have been rotated or sharded; run owner_prompts_hourly.py --full" % len(P))
    except Exception as e:
        out.append("build: NO JSON (%s) - run owner_prompts_hourly.py" % e)
    try:
        import owner_prompt_context  # noqa: F401
        out.append("verbatim last-100 renderer: importable")
    except Exception as e:
        out.append("verbatim last-100 renderer: NOT importable (%s) - the ask path serves the 3-hour file" % type(e).__name__)
    # Claude: its own command in settings.json since 2026-09-20 (the dispatcher list carried it before; both are read)
    for label, paths in (("claude wiring", (os.path.join(HOME, ".claude", "settings.json"), os.path.join(HERE, "hook_dispatch_list.json"))),
                         ("codex wiring", (os.path.join(HOME, ".codex", "hooks.json"),))):
        try:
            t = "\n".join(io.open(p, encoding="utf-8").read() for p in paths if os.path.exists(p))
            mine, peer = t.count("owner_prompts_hook.py"), t.count("owner_prompt_context.py")
            out.append("%s: this hook x%d%s%s" % (label, mine, "" if mine else " - NOT WIRED", ("; owner_prompt_context.py ALSO wired x%d - that doubles the prompts" % peer) if peer else ""))
        except Exception:
            out.append("%s: file not readable" % label)
    try:
        files = [f for f in os.listdir(STATE_DIR) if f.endswith(".json")]
        live = [f for f in files if now - os.path.getmtime(os.path.join(STATE_DIR, f)) < 6 * 3600]
        out.append("sessions with state: %d (%d active in the last 6 h)" % (len(files), len(live)))
    except Exception:
        out.append("sessions with state: none")
    try:
        lines = io.open(LOG, encoding="utf-8", errors="replace").read().splitlines()
        fires = [l for l in lines if " ERROR " not in l]
        errs = [l for l in lines if " ERROR " in l]
        day = time.strftime("%Y-%m-%d")
        out.append("fires logged: %d total, %d today; errors: %d total, %d today" % (
            len(fires), sum(1 for l in fires if l.startswith(day)), len(errs), sum(1 for l in errs if l.startswith(day))))
        for l in fires[-5:]:
            out.append("  " + l)
        if errs:
            out.append("  last error: " + errs[-1])
    except Exception:
        out.append("fires logged: no log yet")
    return "\n".join(out) + "\n"


def selftest():
    """Both arms of every layer on a synthetic corpus, in a temporary state dir - live state is never touched."""
    import tempfile
    global STATE_DIR
    failures = []
    counted = [0]

    def check(name, cond):
        counted[0] += 1
        print(("PASS " if cond else "FAIL ") + name)
        if not cond:
            failures.append(name)

    now = time.time()
    day = 86400
    prompts = []
    for i in range(40):
        t = now - (39 - i) * 12 * 3600  # one prompt every 12 h over 20 days
        for topic, text in (("films and videos", "finish the film %d" % i), ("trading simulations", "check the lanes %d" % i)):
            prompts.append({"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t)), "session": "s%d" % (i % 3),
                            "text": text, "topic": topic, "refires": 0})
    prompts.append({"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now - 60)), "session": "s9", "text": "and the newest ask", "topic": "films and videos", "refires": 0})
    prompts.sort(key=lambda p: p["ts"])
    threads = [{"thread": "films and videos", "last_24h": 3, "last_7d": 14, "checklist": {"open": 1, "open_items": [{"id": "T-1", "text": "finish"}]}},
               {"thread": "trading simulations", "last_24h": 2, "last_7d": 14, "checklist": {"open": 0, "open_items": []}}]
    meta = {"prompts": prompts, "threads": threads, "checklist_dir": "", "built": "test", "built_epoch": int(now), "sessions": 3}
    global load
    real_load = load
    load = lambda: meta  # noqa: E731
    # the synthetic corpus names its own two threads; topic_of must see a lexicon that maps to them, whatever
    # lexicon file (private, and absent on a fresh installation) sits beside owner_prompts_hourly.py
    saved_topics = getattr(H, "TOPICS", None) if H is not None else None
    if H is not None:
        H.TOPICS = [("films and videos", ["film"]), ("trading simulations", ["lanes"])]
    tmp = tempfile.mkdtemp(prefix="owner_prompts_selftest_")
    old_state = STATE_DIR
    STATE_DIR = tmp
    # the schedule and its shadow log are the live files' too: the checks below are the hourly block's, so they read a
    # schedule file that does not exist, and nothing they do lands in the live shadow log (09-24: the first landing's
    # selftest wrote seven synthetic sessions into it)
    global SCHEDULE_FILE, SHADOW_LOG
    saved_sched, saved_shadow = SCHEDULE_FILE, SHADOW_LOG
    SCHEDULE_FILE, SHADOW_LOG = os.path.join(tmp, "no_schedule"), os.path.join(tmp, "shadow.log")
    global MODE_FILE
    saved_mode0 = MODE_FILE
    MODE_FILE = os.path.join(tmp, "mode0")  # the layered checks must not depend on the live mode
    io.open(MODE_FILE, "w", encoding="utf-8").write("layered\n")
    try:
        # the thread classifier's colliding keys (27 Sep): a lexicon with them, then the synthetic one back
        if H is not None and hasattr(H, "_BOUNDED_KEYS"):
            synthetic = H.TOPICS
            H.TOPICS = [("live", [" ask", "hub", "bots", "position", "quot", "margin", "server box"]),
                        ("brain", ["brain", "github", "push"])]
            check("'what i asked for' with a brain word goes to brain, not to the quote's ask",
                  topic_of("ok now keep improving the brain, based on what i asked for") == "brain")
            check("a real ask and the hub still count", topic_of("buy it at the ask and check the hub quotes") == "live")
            check("github is not the hub", topic_of("check the github repo") == "brain")
            check("robots, composition, quota and marginal are not live words",
                  topic_of("the robots in the composition, our usage quota, the marginal law") == "other")
            check("positions and a margin still are", topic_of("check the positions, the margin is fine") == "live")
            check("the other keys keep the substring rule", topic_of("the server boxes") == "live")
            H.TOPICS = synthetic
        w = wake_block(meta, now)
        check("wake has the table and the newest ask, under the cap", "shape of the last" in w and "the newest ask" in w and len(w) <= WAKE_MAX)
        parts, st = decide("SessionStart", "A", "", {}, now)
        check("session start serves the wake once", len(parts) == 1 and "WAKE PICTURE" in parts[0] and st.get("why") == "wake")
        parts, st = decide("UserPromptSubmit", "A", "is the film done", st, now + 1)
        check("first touch of a thread serves its slice with further-back days", len(parts) == 1 and "THREAD films" in parts[0] and "further back" in parts[0])
        parts, st = decide("UserPromptSubmit", "A", "render the film again", st, now + 2)
        check("second touch of the same thread is silent", parts == [])
        parts, st = decide("UserPromptSubmit", "A", "ok", st, now + 3)
        check("an unclassified prompt is silent", parts == [])
        parts, st = decide("UserPromptSubmit", "A", "what did i ask today", st, now + 4)
        check("the ask path serves the block", len(parts) == 1 and st.get("why") == "ask")
        # a long verbatim block goes out in parts too (a 15 KB ask block was cut by Codex and would be spilled by Claude)
        global DIGEST, CHUNK_MAX, MIN_PART
        saved_digest, saved_chunk_a, saved_min_a = DIGEST, CHUNK_MAX, MIN_PART
        DIGEST = os.path.join(tmp, "digest_big.md")
        io.open(DIGEST, "w", encoding="utf-8").write("".join("  09-20 %02d:%02d [s1] ask number %d about the film and the box\n" % (h, m, h * 60 + m) for h in range(3) for m in range(0, 60, 2)))
        os.environ["OWNER_PROMPTS_NO_PEER"] = "1"
        try:
            CHUNK_MAX, MIN_PART = 800, 200
            full_ask = ask_block()
            parts, st = decide("UserPromptSubmit", "W", "what did i ask today", {}, now + 10)
            got = list(parts)
            rounds = 0
            while st.get("pending") and rounds < 200:
                more, st = decide("UserPromptSubmit", "W", "ok", st, now + 11 + rounds)
                got.extend(more)
                rounds += 1
            body = "".join(re.sub(r"^\[owner-prompts\] WHAT HE ASKED - part \d+ of \d+ [^\n]*\n", "", g) for g in got)
            check("a long ask block is delivered in parts that fit and reassemble", len(full_ask) > 800 and len(got) > 1
                  and all(len(g) <= 800 for g in got) and body.split("\n") == full_ask.split("\n"))
        finally:
            DIGEST, CHUNK_MAX, MIN_PART = saved_digest, saved_chunk_a, saved_min_a
            os.environ.pop("OWNER_PROMPTS_NO_PEER", None)
        parts, st = decide("UserPromptSubmit", "A", "what did i ask today", st, now + 5)
        check("the same ask again within the window is not re-served", parts == [] or "the last" not in (parts[0] if parts else ""))
        st["served_at"] = now - 2 * 3600
        st["checked_at"] = 0
        parts, st = decide("UserPromptSubmit", "A", "carry on", st, now + 6)
        check("an hour later the delta carries only what is new", len(parts) == 1 and "SINCE YOU LAST LOOKED" in parts[0] and "and the newest ask" in parts[0] and "finish the film 0" not in parts[0])
        st["checked_at"] = 0
        parts, st = decide("UserPromptSubmit", "A", "carry on", st, now + 7)
        check("a delta with nothing new is silent", parts == [])
        parts, st = decide("UserPromptSubmit", "C", "check the lanes please", {}, now + 8)
        check("a session with no wake (Codex) gets wake plus its thread on the first prompt", len(parts) == 2 and "WAKE PICTURE" in parts[0] and "THREAD trading" in parts[1])
        old = os.path.join(tmp, "old.json")
        io.open(old, "w", encoding="utf-8").write("{}")
        os.utime(old, (now - 20 * day, now - 20 * day))
        marker = os.path.join(tmp, ".pruned")
        if os.path.exists(marker):
            os.remove(marker)  # the SessionStart step above pruned already; the daily marker would skip this run
        n = prune_state(now)
        check("pruning removes state older than %d days" % STATE_KEEP_DAYS, n == 1 and not os.path.exists(old))
        big = dict(meta)
        big["prompts"] = prompts * 40
        check("a huge thread stays under the slice cap", len(slice_block(big, "films and videos", SLICE_HOURS, now)) <= SLICE_MAX + 200)
        global OBJECTS
        saved_objects = OBJECTS
        OBJECTS = {"films and videos": [("galaxy film", re.compile("galaxy", re.I))]}
        v = dict(meta)
        v["prompts"] = prompts + [
            {"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now - 3 * day)), "session": "s1", "text": "the galaxy film is shit, nothing interesting in it", "topic": "films and videos", "refires": 0},
            {"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now - 30 * 3600)), "session": "s2", "text": "the galaxy film is still bad, fix it", "topic": "films and videos", "refires": 0}]
        w2 = wake_block(v, now)
        check("a verdict repeated on two days leads the wake picture", "WHAT HE KEEPS SAYING" in w2 and "called bad x2" in w2)
        s2 = slice_block(v, "films and videos", SLICE_HOURS, now)
        check("a verdict line inside the 48 hours is marked and kept", "!! " in s2 and "still bad" in s2)
        big2 = dict(v)
        big2["prompts"] = prompts * 40 + v["prompts"][-1:]
        check("a verdict line survives trimming for size", "still bad" in slice_block(big2, "films and videos", SLICE_HOURS, now))
        OBJECTS = saved_objects
        saved_mode = MODE_FILE
        MODE_FILE = os.path.join(tmp, "mode")
        io.open(MODE_FILE, "w", encoding="utf-8").write("simple\n")
        parts, st = decide("UserPromptSubmit", "S", "anything at all", {}, now + 20)
        parts2, st2 = decide("UserPromptSubmit", "S", "anything else", st, now + 21)
        check("simple mode sends one block on the first prompt and nothing a minute later", len(parts) == 1 and "HIS LAST 48 HOURS" in parts[0] and parts2 == [])
        # the relevant 3000: a session whose prompt touches a thread gets that thread condensed inside its block,
        # a thread touched between blocks arrives at once, the same thread again is silent
        parts, st = decide("UserPromptSubmit", "T", "is the film done", {}, now + 30)
        n_old = sum(1 for p in prompts if p["topic"] == "films and videos" and _epoch(p["ts"]) < now + 30 - SIMPLE_HOURS * 3600)
        check("simple mode's block carries EVERY earlier ask of the touched thread, in full, and its checklist items",
              len(parts) == 1 and "RELEVANT TO THIS SESSION" in parts[0] and "## films and videos" in parts[0] and "T-1" in parts[0]
              and sum(1 for l in parts[0].split("\n") if "finish the film" in l) >= n_old and "finish the film 0" in parts[0])
        parts, st = decide("UserPromptSubmit", "T", "check the lanes please", st, now + 31)
        check("a thread touched between blocks arrives at once, in full, without the shared block",
              len(parts) == 1 and "## trading simulations" in parts[0] and "HIS LAST" not in parts[0] and "check the lanes 0" in parts[0])
        parts, st = decide("UserPromptSubmit", "T", "check the lanes again", st, now + 32)
        check("the same thread again is silent", parts == [])
        # a background job's completion notice is the harness speaking: film-worded, it opens no thread; the same
        # words typed by the user still do
        notice = ('<task-notification>\n<task-id>b1</task-id>\n<status>completed</status>\n<summary>Background command '
                  '"is the film done" completed (exit code 0)</summary>\n</task-notification>')
        _p0, stn = decide("UserPromptSubmit", "N", "anything at all", {}, now + 40)
        pn, stn = decide("UserPromptSubmit", "N", notice, stn, now + 41)
        check("a harness task-notification opens no thread", pn == [] and "films and videos" not in (stn.get("touched") or []))
        pn2, stn = decide("UserPromptSubmit", "N", "[SYSTEM NOTIFICATION - NOT USER INPUT]\n" + notice, stn, now + 42)
        check("a notification behind the system banner opens no thread either", pn2 == [])
        pn2b, stn = decide("UserPromptSubmit", "N", '<cross-session-message from="peer">is the film done</cross-session-message>', stn, now + 42.5)
        check("a relayed peer message opens no thread", pn2b == [])
        pn3, stn = decide("UserPromptSubmit", "N", "is the film done", stn, now + 43)
        check("the same words typed as the user's prompt still open the thread", len(pn3) == 1 and "## films and videos" in pn3[0])
        # the block is session-relevant and newest first: the touched thread's asks appear once, in its own section,
        # newest at the top; the shared 48 hours carry the other threads only
        blk = simple_block(meta, now + 30, threads=["films and videos"])
        film_lines = [l for l in blk.split("\n") if "finish the film 0" in l]
        sec = blk.split("every ask on this thread, newest first:")[1].split("[owner-prompts]")[0].strip().split("\n")
        stamps = [l.strip()[3:14] if l.strip().startswith("!! ") else l.strip()[:11] for l in sec]
        check("a touched thread's asks appear once and newest first", len(film_lines) == 1 and stamps == sorted(stamps, reverse=True) and len(stamps) >= 3)
        shared = blk.split("on every other thread")[1]
        check("the shared 48 hours carry the other threads only", "finish the film" not in shared and "check the lanes" in shared)
        # a delivery larger than the chunk cap goes out in parts on consecutive prompts, nothing dropped, and every part
        # with its header line stays under the cap the harness hands to the model inline
        saved_chunk, saved_min = CHUNK_MAX, MIN_PART   # (declared global above, at the ask-path check)
        CHUNK_MAX, MIN_PART = 800, 200   # the synthetic block is about 2 KB; this forces several parts
        parts, st = decide("UserPromptSubmit", "U", "is the film done", {}, now + 40)
        full = simple_block(meta, now + 40, threads=["films and videos"])
        got = [parts[0]]
        rounds = 0
        while st.get("pending") and rounds < 200:
            more, st = decide("UserPromptSubmit", "U", "ok", st, now + 41 + rounds)
            got.extend(more)
            rounds += 1
        body = "".join(re.sub(r"^\[owner-prompts\] HIS PROMPTS - part \d+ of \d+ [^\n]*\n", "", g) for g in got)
        check("an over-cap block is delivered in parts on consecutive prompts and reassembles to the whole",
              len(got) > 1 and "part 1 of %d" % len(got) in got[0] and body.split("\n") == full.split("\n"))
        check("every part, header included, fits under the cap", all(len(g) <= CHUNK_MAX for g in got))
        parts, st = decide("UserPromptSubmit", "U", "ok", st, now + 300)
        check("after the last part the session is silent until the hour", parts == [])
        # a delivery still in flight at the hour continues (his newest asks in front) instead of starting over; a
        # drained queue lets the next hour start a fresh block
        parts, st = decide("UserPromptSubmit", "X", "is the film done", {}, now + 40)
        parts2, st = decide("UserPromptSubmit", "X", "ok", st, now + 40 + SIMPLE_EVERY_S + 5)
        check("at the hour a delivery in flight continues instead of restarting", len(parts2) == 1 and "part 2 of" in parts2[0] and st.get("why") == "simple-continue")
        rounds = 0
        while st.get("pending") and rounds < 200:
            more, st = decide("UserPromptSubmit", "X", "ok", st, now + 41 + SIMPLE_EVERY_S + rounds)
            rounds += 1
        parts3, st = decide("UserPromptSubmit", "X", "ok", st, now + 40 + 2 * SIMPLE_EVERY_S + 300)
        check("with the queue drained the next hour starts a fresh block", len(parts3) == 1 and "part 1 of" in parts3[0] and st.get("why") == "simple")
        # a part queued under a larger budget is split to fit a smaller one on the day it goes out, nothing dropped
        CHUNK_MAX = 1300
        parts, st = decide("UserPromptSubmit", "V", "is the film done", {}, now + 40)
        queued = list(st.get("pending") or [])
        small = max(400, len(queued[0]) // 2) if queued else 800   # smaller than the queued part by construction
        CHUNK_MAX = small
        got2 = [parts[0]]
        rounds = 0
        while st.get("pending") and rounds < 200:
            more, st = decide("UserPromptSubmit", "V", "ok", st, now + 41 + rounds)
            got2.extend(more)
            rounds += 1
        strip = lambda g: re.sub(r"^\[owner-prompts\] HIS PROMPTS - part \d+ of \d+ [^\n]*\n", "", g)
        if os.environ.get("OWNER_PROMPTS_SELFTEST_DEBUG"):
            sys.stderr.write("refit debug: queued=%s small=%d got2=%s full=%d rebuilt=%d\n" % (
                [len(q) for q in queued], small, [len(g) for g in got2], len(full), len("".join(strip(g) for g in got2))))
            for g in got2:
                sys.stderr.write("  part head: %r\n" % g[:110])
            a, b = "".join(strip(g) for g in got2).split("\n"), full.split("\n")
            for i in range(max(len(a), len(b))):
                if i >= len(a) or i >= len(b) or a[i] != b[i]:
                    sys.stderr.write("  first diff at line %d: rebuilt=%r full=%r\n" % (i, a[i] if i < len(a) else None, b[i] if i < len(b) else None))
                    break
        check("a queued part is refitted to a smaller budget without loss",
              bool(queued) and len(got2) > 1 + len(queued) and all(len(g) <= small for g in got2[1:])
              and "".join(strip(g) for g in got2).split("\n") == full.split("\n"))
        CHUNK_MAX, MIN_PART = saved_chunk, saved_min
        # machine-shaped text never becomes a standing ask, and a verdict cluster from last week ranks below yesterday's
        m = dict(meta)
        m["prompts"] = prompts + [{"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now - i * day - 60)), "session": "s%d" % i,
                                   "text": "<recommended_plugins> Here is a list of plugins that are available but not installed", "topic": "films and videos", "refires": 0} for i in range(4)]
        check("machine-shaped text never becomes a standing ask", "recommended_plugins" not in standing_block(standing(m, None, now, min_n=2), VERDICTS_MAX, "x"))
        OBJECTS = {"films and videos": [("old film", re.compile("old film", re.I)), ("new film", re.compile("new film", re.I))]}
        v4 = dict(meta)
        v4["prompts"] = prompts + [{"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now - d * day)), "session": "s1", "text": "the old film is shit", "topic": "films and videos", "refires": 0} for d in (8, 9, 10)] + \
                        [{"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now - d * day)), "session": "s2", "text": "the new film is still bad", "topic": "films and videos", "refires": 0} for d in (1, 2)]
        sb4 = standing_block(standing(v4, None, now, min_n=2), VERDICTS_MAX, "x")
        check("the newest verdict cluster comes first even when an older one has more lines", sb4.find("new film") < sb4.find("old film") and "old film" in sb4)
        OBJECTS = saved_objects
        MODE_FILE = saved_mode
    finally:
        STATE_DIR = old_state
        MODE_FILE = saved_mode0
        SCHEDULE_FILE, SHADOW_LOG = saved_sched, saved_shadow
        load = real_load
        if H is not None and saved_topics is not None:
            H.TOPICS = saved_topics
    try:
        import owner_prompts_brains
        for _name, _ok in owner_prompts_brains.controls(sys.modules[__name__]):
            check("important mode: " + _name, _ok)
    except Exception as e:
        check("important mode controls ran (%s: %s)" % (type(e).__name__, e), False)
    print("selftest: %d checks, %d failed" % (counted[0], len(failures)))
    return 1 if failures else 0


def main():
    argv = sys.argv[1:]
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if "--show" in argv:
        sys.stdout.write(show(argv[argv.index("--show") + 1:]))
        return 0
    if "--status" in argv:
        sys.stdout.write(status())
        return 0
    if "--mode" in argv:
        want = argv[argv.index("--mode") + 1] if len(argv) > argv.index("--mode") + 1 else ""
        if want in ("simple", "layered", "important"):
            with io.open(MODE_FILE, "w", encoding="utf-8") as f:
                f.write(want + "\n")
            _log("mode set to %s" % want)
        sys.stdout.write("mode: %s (simple = one fixed block, his last %d hours in full, to every session every hour; layered = wake + thread slices + deltas)\n" % (mode(), int(SIMPLE_HOURS)))
        return 0
    if "--selftest" in argv:
        return selftest()
    if "--print" in argv:
        sys.stdout.write(wake_block(load()) or "[owner-prompts] no build yet - run owner_prompts_hourly.py\n")
        return 0
    if "--hook" not in argv:
        print(__doc__)
        return 0
    runtime = "codex" if ("codex" in argv or os.environ.get("CODEX_THREAD_ID")) else "claude"
    global CHUNK_MAX
    if runtime == "codex" and "OWNER_PROMPTS_CHUNK_MAX" not in os.environ:
        CHUNK_MAX = min(CHUNK_MAX, CODEX_INLINE_MAX - 1000)
    try:
        payload = read_payload()
        sess = session_id(payload, runtime)
        if not payload and not sess:
            return 0  # no payload and no session: nothing to key the per-session clock on, so stay silent
        sess = sess or runtime + "-unknown"
        event = str(payload.get("hook_event_name") or payload.get("event") or os.environ.get("HOOK_EVENT") or "UserPromptSubmit")
        prompt = str(payload.get("prompt") or payload.get("user_prompt") or payload.get("text") or "")
        if CHUNK_MAX < MIN_PART and event != "SessionStart" and not (prompt and ASK_RX.search(prompt)):
            _log("%s %s %s skipped: budget %d under MIN_PART %d; the delivery waits for the next prompt" % (runtime, event, sess[:8], CHUNK_MAX, MIN_PART))
            return 0
        try:
            if isinstance(payload, dict):
                sys.modules[__name__].TOOL_HINT = json.dumps(payload.get("tool_input") or {}, ensure_ascii=True, default=str)[:800]
        except Exception:
            pass
        import owner_context_delivery
        owner_context_delivery.emit(sys.modules[__name__], payload, runtime, sys.stdout,
                                    budget=CODEX_INLINE_MAX - 1000 if runtime == "codex" else HARNESS_INLINE_MAX - 500)
    except Exception as e:
        _log_error("main", e)
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
