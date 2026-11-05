"""owner_prompts_brains.py - his IMPORTANT prompts, served by brain (main / movie / math): one budgeted part per brain
once per session context, then only his new important prompts, hourly and capped.

Mode "important" of owner_prompts_hook.py, built by worker be1 on 7 Oct 2026 for his 22:4x and 22:56 orders ("cut this
down to the last 1k important prompts (every prompt the agent should decide if it is important or just something like
'keep working')", "i want much more of my text preserved", "distribute this over many hooks that fire at different
times, for the movie brain, the math brain and the brain, so agents still get relevant prompts while fewer go to them").

IMPORTANCE  each prompt of his gets one verdict, cached by id = sha1(session[:8] | first 160 normalized chars)[:16].
            Rules decide the clear cases: a continuation or ping with nothing else in it ("keep working", "ok", "yes do
            it", "continue", "speed things up", "try again", "you still working on this?") is DROP, and so is the same
            prompt again within 30 minutes; an order, a correction, a question with content, a pasted document, more
            than 150 chars of his own words, a continuation that names a duration, is KEEP. The rest is judged once in
            batches and cached in the verdict ledger; the agent that receives a prompt may record its own verdict
            (--verdict). Precedence: an agent's verdict > the batch judge > the rules; the latest wins within a class.
            Unsure = KEEP.
THE BLOCK   drawn from his last POOL_N KEEP prompts, each in his own words up to OWN_CUT chars, a pasted document cut to
            PASTE_CUT with his words around it kept, ASCII. Each brain serves the prompts tagged for it: main (every
            session; everything that is not film or math work, and anything said to every agent), movie (films,
            novels), math (papers, open problems). Organised as the hourly block was: what he keeps saying, the threads
            this session touched, then the brain's other threads, newest first. ONE PART of at most FIRST_MAX chars
            (8,800; under the 10,000-char inline ceiling of both runtimes with the notice in front): his words in full
            for his last FULL_HOURS (48) hours, one BRIEF line for older prompts, re-asks folded, and a count of what
            did not fit with the one command that shows all of it (--show).
DELIVERY    a brain's block once per context, on his prompt: main on the session's first prompt, movie or math when the
            session first does that work (his prompt, the session's project folder, or a tool step touching that
            territory, recorded and served at his next prompt); again after a compaction. One part per prompt. A tool
            step prints nothing. After that each brain sends only his new KEEP prompts (not the session's own), at most
            DELTA_MAX chars (3,000), in its own third of the hour (main :00-:20, movie :20-:40, math :40-:60). The block
            never goes twice to a context that holds it.

Switch on:  python ~/.claude/dreaming/owner_prompts_hook.py --mode important   (back: --mode simple)
            The hook imports this file in-process: no extra process per tool call. The split wiring, one registration
            per brain (--hook --brain main|movie|math), also works and keeps one state file per brain.
CLI:        --seed [--judge-dir DIR]   --verdict ID KEEP|DROP --by AGENT [--gist TEXT] [--brains main,movie]
            --show [main|movie|math|all]   --status   --selftest   --rules-report   --handcheck N SEED OUT.tsv
            --measure TIMELINE.json ...   --hook [--brain main|movie|math|all] [--runtime claude|codex]
"""
import sys
if __name__ == "__main__" and ("-h" in sys.argv[1:] or "--help" in sys.argv[1:]):
    print(__doc__)
    sys.exit(0)
import glob
import hashlib
import io
import json
import os
import random
import re
import sys
import time
import unicodedata
from collections import Counter, defaultdict
from difflib import SequenceMatcher

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

VERDICT_LEDGER = os.environ.get("OWNER_PROMPT_VERDICTS") or \
    os.path.expanduser("~/.claude/projects/{{BRAIN_SLUG}}/memory/prompts/OWNER_PROMPT_VERDICTS.jsonl")
POOL_N = int(os.environ.get("OWNER_PROMPTS_POOL", "1000"))
OWN_CUT = 600        # his own words in full up to the 600-char cut the hourly block used
PASTE_CUT = 300      # a pasted document: its first 300 chars
LINE_MAX = 2400      # one prompt's line, all pastes included
REASK_S = 30 * 60
BRAINS = ("main", "movie", "math")
WINDOW = {"main": (0, 20), "movie": (20, 40), "math": (40, 60)}   # each brain's third of the hour for its delta
DELTA_MIN_GAP_S = 40 * 60
DELTA_MAX_GAP_S = 80 * 60
ACTIVE_KEEP_S = 3 * 3600    # a film or math brain nothing pointed at for this long before a compaction is not re-sent
TOUCH_EVERY_S = 600         # a brain's last touch is written at most this often (no state write per tool step)
SELF = os.path.expanduser("~/.claude/dreaming/owner_prompts_brains.py")
# the budget (v2, 8 Oct): a brain's block is ONE part, header included; a delta is capped; a tool step sends nothing
FIRST_MAX = int(os.environ.get("OWNER_PROMPTS_FIRST_MAX", "8800"))
DELTA_MAX = int(os.environ.get("OWNER_PROMPTS_DELTA_MAX", "3000"))
FULL_HOURS = 48       # his words in full for his last 48 hours (from other sessions); one line for older prompts
BRIEF = 160           # that one line
STANDING_MAX = 1500   # "what he keeps saying", at most
REL_SHARE = 0.65      # of the room left for threads, the share of the threads this session touched (if others exist)
CHECKLIST_OPEN = 3    # open checklist items named under a touched thread
ALSO_MAX = 300        # the line naming the threads that did not fit; its room is always held back
FLOOR_SHARE = 0.5     # when the threads do not all fit: the share of their room that may go to one line per thread

# ----------------------------------------------------------------------------- text shape
PASTE_RX = re.compile(r"<pasted_content\b[^>]*>(.*?)(?:</pasted_content\b[^>]*>|\Z)", re.S)
PASTE_TAG_RX = re.compile(r"</?pasted_content\b[^>]*>")


def segments(text):
    """[(kind, text)] in order; kind 'own' (his words) or 'paste' (a pasted document)."""
    out, pos = [], 0
    for m in PASTE_RX.finditer(text or ""):
        if m.start() > pos:
            out.append(("own", text[pos:m.start()]))
        out.append(("paste", m.group(1)))
        pos = m.end()
    if pos < len(text or ""):
        out.append(("own", text[pos:]))
    return out


def own_words(text):
    return " ".join(s.strip() for k, s in segments(text) if k == "own").strip()


def norm(text):
    """Lowercase alphanumerics, paste tags removed (their content kept)."""
    return re.sub(r"[^a-z0-9]", "", PASTE_TAG_RX.sub(" ", text or "").lower())


def prompt_id(sess, text):
    """sha1(session[:8] | first 160 normalized chars)[:16]: the hook computes it from the payload at the prompt, the
    builder from the archive row."""
    return hashlib.sha1((str(sess or "")[:8] + "|" + norm(text)[:160]).encode("utf-8")).hexdigest()[:16]


_PUNCT = {"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u2013": "-", "\u2014": "-", "\u2026": "...",
          "\u00a0": " ", "\u2022": "-"}


def ascii_text(s):
    s = "".join(_PUNCT.get(c, c) for c in (s or ""))
    s = unicodedata.normalize("NFKD", s)
    return "".join(c if ord(c) < 128 else ("" if unicodedata.combining(c) else "?") for c in s)


def render_text(text):
    """His words in full up to OWN_CUT chars (cut as the hourly block cut them), each pasted document cut to PASTE_CUT
    with his words before and after it kept; whitespace folded; ASCII."""
    out, budget, cut = [], OWN_CUT, False
    for kind, s in segments(text or ""):
        s = re.sub(r"\s+", " ", s).strip()
        if not s:
            continue
        if kind == "paste":
            out.append("[pasted %d chars: %s%s]" % (len(s), s[:PASTE_CUT].rstrip(), " ..." if len(s) > PASTE_CUT else ""))
        elif budget > 0:
            if len(s) > budget:
                s = s[:max(0, budget - 3)].rstrip() + "..."
            budget -= len(s)
            out.append(s)
        elif not cut:
            out.append("...")
            cut = True
    return ascii_text(" ".join(out))[:LINE_MAX]


# ----------------------------------------------------------------------------- importance
# DROP only when EVERY word of the prompt is a continuation/ping word (his "keep working", "ok", "yes do it",
# "continue", "speed things up", and their typos). Anything else is KEEP by rule, or judged.
PING_WORDS = set("""
keep keeep kepp leep working workign wroking woking wokring workin wroking work going goin continue contiue contine
cont go ahead on ok okay okk k kk yes yess yeah yep yup ya yea sure do it that this please pls plz now proceed carry
resume faster speed things up hurry and so then alright allright fine thanks thank you thx cool nice perfect great
again more next loop lets let s us keepworking
""".split())
HARNESS_RX = re.compile(r"^\s*(?:\[SYSTEM NOTIFICATION[^\]]*\]\s*)?(?:<task-notification>|<agent-notification\b"
                        r"|<cross-session-message\b|<agent-message\b|<system-reminder\b|<local-command-stdout\b"
                        r"|<command-name>|<command-message>|Another Claude session sent a message\b"
                        r"|Your claude\.ai usage limit has reset\b|Caveat: The messages below)", re.I)
# words that make a short prompt an order, a correction, a decision or a real question (rule KEEP)
MARKER_RX = re.compile(
    r"\b(stop|don'?t|do not|dont|never|always|no\b|not\b|wrong|why|fix|make sure|i want|i need|i don'?t|remove|delete|delte"
    r"|add|use|change|switch|move|kill|pause|restart|start|launch|push|commit|write|rewrite|redo|render|publish|upload"
    r"|send|tell|ask|check|audit|test|run|build|create|make|set|turn|put|show|open|close|cancel|undo|revert|instead"
    r"|should|must|need|only|every|all agents|from now on|rule|law|priority|first|then do|before|after|until"
    r"|how (many|much|long|far|is|are|do|did|does)|what (is|are|was|were|do|does|did|happened|about the)|where (is|are)"
    r"|which|when|who|can you|could you|is it|are you|did you|have you|do we|is there|are there)\b", re.I)
LONG_OWN = 150
# the continuation classes the 7 Oct batch judged DROP (pings about the connection, "try again", "finish this up",
# "keep working on this full time"); they apply only where no KEEP rule spoke, and never to a prompt naming a duration
CONT_EXTRA = set("""
try done finish finishing up get to did i looks fine still you connected connect conncted able lost internet froze got
disconnected auditing studying stuff running solving fully much way too slow optimize even things process sped speded
speed your what about been doing we goo the is
""".split())
TYPO_BASES = ("keep", "working", "continue", "work")
FILLER_RX = re.compile(r"\b(without stop\w*|non ?stop|full ?time|as soon as possible|asap|on (this|that|it)"
                       r"|on what (i|you) (have |ve )?been \w+( you)?( to do)?)\b")
DURATION_RX = re.compile(r"\b(for|next|another)\b.{0,24}?\b(\d+|one|two|three|four|five|six|seven|eight|nine|ten|twelve"
                         r"|twenty|few|couple)\b.{0,12}?\b(hours?|hrs?|days?|minutes?|mins?|weeks?|nights?)\b"
                         r"|\b\d+ ?(h|hrs?|hours?|days?)\b")
PROBE_RX = re.compile(r"^\s*reply with (the word )?ok\b.{0,40}$", re.I | re.S)
SLASH_UI = set("""chrome seen clear compact model fast config status usage cost resume exit login logout mcp help hooks
context doctor ide permissions agents bashes todos vim theme export rewind plugin desk checklist""".split())
SLASH_RX = re.compile(r"^\s*/([a-z;:,.\-]+)\s*$", re.I)


def _pingish(words):
    for w in words:
        if w in PING_WORDS or w in CONT_EXTRA:
            continue
        if len(w) >= 3 and any(abs(len(w) - len(b)) <= 2 and SequenceMatcher(None, w, b).ratio() >= 0.8
                               for b in TYPO_BASES):
            continue
        return False
    return True


def rule_verdict(text):
    """(verdict, why) for the clear cases: ('DROP', ...), ('KEEP', ...), or ('', 'unclear')."""
    t = (text or "").strip()
    if not t:
        return "DROP", "rule:empty"
    if HARNESS_RX.match(t):
        return "DROP", "rule:harness"
    segs = segments(t)
    own = own_words(t)
    if any(k == "paste" for k, _ in segs):
        return "KEEP", "rule:paste"
    low = own.lower()
    words = re.findall(r"[a-z0-9']+", low.replace("'", ""))
    if not words and not re.search(r"[a-z0-9]", low):
        return "DROP", "rule:no-words"
    if words and len(words) <= 12 and all(w in PING_WORDS for w in words):
        return "DROP", "rule:ping"
    if len(own) > LONG_OWN:
        return "KEEP", "rule:long"
    if MARKER_RX.search(own):
        return "KEEP", "rule:marker"
    if "?" in own and len(words) >= 6:
        return "KEEP", "rule:question"
    if PROBE_RX.match(own):
        return "DROP", "rule:probe"
    m = SLASH_RX.match(own)
    if m and (m.group(1).lower() in SLASH_UI or not re.search(r"[a-z]{4}", m.group(1).lower())):
        return "DROP", "rule:slash"
    if not DURATION_RX.search(low):
        w2 = re.findall(r"[a-z0-9']+", FILLER_RX.sub(" ", low).replace("'", ""))
        if w2 and len(w2) <= 14 and _pingish(w2):
            return "DROP", "rule:continue"
    return "", "unclear"


# ----------------------------------------------------------------------------- brains
MOVIE_TOPICS = {"films and videos", "novels and writing"}
MATH_TOPICS = {"math papers", "open problems repo"}
MOVIE_RX = re.compile(
    r"\b(films?|movies?|videos?|youtube|renders?|rendering|rendered|blender|manim|narrat\w*|narrator|thumbnails?|scenes?"
    r"|episodes?|trailers?|clips?|animat\w*|voice ?overs?|tts|subtitles?|captions?|comfyui|ltx|storyboards?|cinemat\w*"
    r"|shots?|novels?|chapters?|audiobooks?|kaggle|movie[ _-]?brain|lectures?|documentar\w*|music|soundtrack)\b", re.I)
MATH_RX = re.compile(
    r"\b(math|maths|mathemat\w*|theorems?|lemmas?|proofs?|prove|proven|conjectures?|papers?|arxiv|lean|lakefile|latex"
    r"|tex|erd[oö]s|hadamard|kernels?|open problems?|referees?|journals?|preprints?|equations?|inequalit\w*|eigen\w*"
    r"|matri(x|ces)|polynomials?|primes?|combinatori\w*|math[ _-]?brain|math-research|submatri\w*|singular values?"
    r"|counterexamples?|bounds?)\b", re.I)
# a prompt that speaks to every agent goes to the main brain whatever its thread
MAIN_RX = re.compile(r"\b(all (the |of the )?agents|every agent|other agents|agents? should|from now on|\blaws?\b|\brules?\b"
                     r"|the brain|brain\b|hooks?|memor(y|ies)|checklist|mesh|codex|claude|subagents?)\b", re.I)
# a tool step's input (paths, commands) names the territory less loosely than his words do
MOVIE_PATH_RX = re.compile(r"movie_projects|movie[-_ ]brain|films?[-_/\\]|manim|blender|\.mp4\b|youtube|render_one"
                           r"|comfyui|thumbnail|novels?[-_/\\]|voiceover|narration", re.I)
MATH_PATH_RX = re.compile(r"math[-_ ]brain|math[-_]open[-_]problems|open[-_]problems|math-research|\.tex\b|lakefile"
                          r"|\.lean\b|arxiv|theorem|lemma", re.I)
# the session's project folder (Claude: the transcript's folder name, its cwd with every separator a dash)
PROJECT_MOVIE_RX = re.compile(r"movie[-_]projects|movie[-_]?brain|films?[-_]|[-_]movies?[-_]|manim|novels?[-_]|youtube"
                              r"|script[-_]factory", re.I)
PROJECT_MATH_RX = re.compile(r"math[-_]?brain|math[-_]open[-_]problems|open[-_]problems?|math[-_]research|erdos"
                             r"|[-_]lean[-_]|[-_]paper$", re.I)


def project_brains(transcript):
    """The brains a session works in by its project folder alone (empty for Codex: its rollout path names no project)."""
    name = os.path.basename(os.path.dirname(str(transcript or "")))
    if not name.startswith("C--"):
        return set()
    out = set()
    if PROJECT_MOVIE_RX.search(name):
        out.add("movie")
    if PROJECT_MATH_RX.search(name):
        out.add("math")
    return out


def brains_of(text, topic):
    """The brains a KEEP prompt is served by: movie (films, novels), math (papers, open problems), main (everything
    else, and anything that speaks to every agent)."""
    own = own_words(text) or text or ""
    b = set()
    if topic in MOVIE_TOPICS:
        b.add("movie")
    if topic in MATH_TOPICS:
        b.add("math")
    if topic not in MOVIE_TOPICS and topic not in MATH_TOPICS:
        b.add("main")
    if MOVIE_RX.search(own) and topic not in MATH_TOPICS:
        b.add("movie")
    if MATH_RX.search(own) and topic not in MOVIE_TOPICS:
        b.add("math")
    if MAIN_RX.search(own):
        b.add("main")
    return sorted(b)


# ----------------------------------------------------------------------------- the verdict ledger
_LED = {}


def read_ledger(path=None):
    """{id: record}: an agent's verdict outranks the batch judge, the judge outranks the rules (rule records are only
    the record of what the rules said; the rules are recomputed live); the latest wins within a class."""
    path = path or VERDICT_LEDGER
    try:
        s = os.stat(path)
    except OSError:
        return {}
    key = (path, s.st_mtime, s.st_size)
    if _LED.get("key") == key:
        return _LED["val"]
    best = {}
    with io.open(path, encoding="utf-8", errors="replace") as f:
        for ln in f:
            try:
                r = json.loads(ln)
            except Exception:
                continue
            if not isinstance(r, dict):
                continue
            pid, v, by = r.get("id"), str(r.get("verdict") or "").upper(), str(r.get("by") or "")
            if not pid or v not in ("KEEP", "DROP") or by == "rule":
                continue
            cls = 1 if (by == "be1-batch" or by.startswith("judge")) else 2
            if pid not in best or cls >= best[pid][0]:
                best[pid] = (cls, r)
    val = {k: v[1] for k, v in best.items()}
    _LED.clear()
    _LED.update({"key": key, "val": val})
    return val


def append_records(recs, path=None):
    """Append-only, under a lock file; the ledger is never rewritten."""
    path = path or VERDICT_LEDGER
    if not recs:
        return
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    lk, t0 = path + ".lock", time.time()
    while True:
        try:
            os.close(os.open(lk, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            break
        except FileExistsError:
            try:
                if time.time() - os.path.getmtime(lk) > 30:
                    os.remove(lk)   # a lock left by a dead writer
                    continue
            except OSError:
                pass
            if time.time() - t0 > 10:
                raise TimeoutError("verdict ledger busy: " + lk)
            time.sleep(0.05)
    try:
        with io.open(path, "a", encoding="utf-8") as f:
            for r in recs:
                f.write(json.dumps(r, ensure_ascii=True, sort_keys=True) + "\n")
    finally:
        try:
            os.remove(lk)
        except OSError:
            pass
    _LED.clear()


def _epoch(ts):
    try:
        return time.mktime(time.strptime(str(ts)[:19], "%Y-%m-%dT%H:%M:%S"))
    except Exception:
        return 0.0


def classify(prompts, led=None):
    """[{p, id, verdict KEEP|DROP, why, by, brains}] in the prompts' order."""
    led = read_ledger() if led is None else led
    out, seen = [], {}
    for p in prompts:
        text = p.get("text") or ""
        pid = prompt_id(p.get("session"), text)
        v, why = rule_verdict(text)
        key = norm(text)
        if v != "DROP" and key:
            e = _epoch(p.get("ts"))
            prev = seen.get(key)
            if prev is not None and e - prev <= REASK_S:
                v, why = "DROP", "rule:reask30"
            else:
                seen[key] = e
        by, r = "rule", led.get(pid)
        if r:
            v, why, by = str(r["verdict"]).upper(), str(r.get("why") or "ledger"), str(r.get("by") or "?")
        if v != "DROP":
            v = "KEEP"
        brains = [b for b in (r or {}).get("brains") or [] if b in BRAINS] or brains_of(text, p.get("topic"))
        out.append({"p": p, "id": pid, "verdict": v, "why": why, "by": by, "brains": brains})
    return out


# ----------------------------------------------------------------------------- the block
_BM = {}


def brain_metas(meta, now=None, led=None):
    """({brain: meta whose prompts are the brain's share of his last POOL_N KEEP prompts, rendered, plus their
    30-minute re-asks so the (asked xN) counts survive}, classification, the KEEP pool)."""
    prompts = meta.get("prompts") or []
    if now is not None:
        cut = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now))
        rows = [p for p in prompts if str(p.get("ts") or "")[:19] <= cut]
    else:
        rows = prompts
    led = read_ledger() if led is None else led
    key = (id(meta), len(prompts), len(rows), rows[-1].get("ts") if rows else "", id(led), POOL_N)
    if _BM.get("key") == key:
        return _BM["val"]
    cl = classify(rows, led)
    keep = [c for c in cl if c["verdict"] == "KEEP"][-POOL_N:] if POOL_N > 0 else []
    brains_by_key = {norm(c["p"].get("text")): c["brains"] for c in keep}
    first = keep[0]["p"].get("ts", "") if keep else "~"
    dup = [c for c in cl if c["why"] == "rule:reask30" and c["p"].get("ts", "") >= first
           and norm(c["p"].get("text")) in brains_by_key]
    by = {b: [] for b in BRAINS}
    for c in sorted(keep + dup, key=lambda c: c["p"].get("ts", "")):
        q = dict(c["p"])
        q["text"] = render_text(q.get("text"))
        q["topic"] = q.get("topic") or "other"
        q["refires"] = q.get("refires") or 0
        q["session"] = str(q.get("session") or "?")[:8]
        q["_id"] = c["id"]
        if c["verdict"] == "KEEP":
            bs = c["brains"]
        else:
            bs, q["_dup"] = brains_by_key.get(norm(c["p"].get("text")), []), True
        for b in bs:
            by[b].append(q)
    out = {}
    for b in BRAINS:
        m = dict(meta)
        m["prompts"] = by[b]
        m["_n"] = sum(1 for c in keep if b in c["brains"])
        out[b] = m
    val = (out, cl, keep)
    _BM.clear()
    _BM.update({"key": key, "val": val})
    return val


def _cands(host, items, sess8, now, full=True):
    """One thread's candidate lines, newest first, re-asks folded: (his words in full or None, one BRIEF line) per
    prompt. In full (render_text's cut) for a prompt of his last FULL_HOURS hours from another session; the session
    holds its own, and an older prompt gets one line."""
    f = host.fold(items)
    f.reverse()
    cutoff, out = now - FULL_HOURS * 3600, []
    for x in f:
        fl = None
        if full and x["session"] != sess8 and float(x.get("_t") or _epoch(x["ts"])) >= cutoff:
            fl = host.line(x, LINE_MAX + 10)
        out.append((fl, host.line(x, BRIEF)))
    return out


def _take(cands, room):
    """The lines that fit in `room` chars, newest first: the full line while it fits, else the brief one; the first
    brief line that does not fit ends the thread. (lines, chars used)."""
    out, used = [], 0
    for fl, br in cands:
        ln = fl if fl is not None and used + len(fl) + 1 <= room else br
        if used + len(ln) + 1 > room:
            break
        out.append(ln)
        used += len(ln) + 1
    return out, used


def _lines(host, items, room, sess8, now, full=True):
    """One thread's lines in at most `room` chars (see _cands / _take). (lines, lines shown, lines folded)."""
    c = _cands(host, items, sess8, now, full)
    ls = _take(c, room)[0]
    return ls, len(ls), len(c)


def _level(needs, room):
    """The water level: the share at which every need under it is met whole and the others split what is left evenly
    (inf when every need fits)."""
    left, m = float(room), len(needs)
    for r in sorted(needs):
        if r * m <= left:
            left -= r
            m -= 1
        else:
            return left / m
    return float("inf")


def _plan(host, bm, topics, by, sess8, now, opens=False):
    """Per thread: its heading lines, its candidate lines, what it needs to show whole, and its floor (heading + the
    newest line, brief)."""
    pl = {}
    for t in topics:
        items = by[t]
        block = ["  ## %s (%d asks, %s .. %s)" % (t, len(items), items[0]["ts"][5:10], items[-1]["ts"][5:16])]
        if opens:
            e = next((x for x in bm.get("threads") or [] if x.get("thread") == t), {})
            op = (e.get("checklist") or {}).get("open_items") or []
            if op:
                block.append(ascii_text("  open on the task checklist: " + "; ".join(
                    "%s %s" % (o.get("id"), (o.get("text") or "")[:70]) for o in op[:CHECKLIST_OPEN])))
        hs = sum(len(s) + 1 for s in block)
        c = _cands(host, items, sess8, now)
        pl[t] = {"head": block, "hs": hs, "cands": c,
                 "need": hs + sum(len(fl if fl is not None else br) + 1 for fl, br in c),
                 "floor": hs + (len(c[0][1]) + 1 if c else 0)}
    return pl


def _fill(host, bm, topics, by, room, sess8, now, opens=False, plan=None):
    """The threads in order of priority (the first first) in at most `room` chars. If they all fit whole, all are shown.
    Otherwise each thread in order gets its heading and newest line while those floors fit in FLOOR_SHARE of the room
    (the first always may); the threads after the first that does not fit are only named, so the oldest go first. The
    rest of the room is water-filled over the shown threads in order: one that needs less than an even share takes its
    need, the others split what is left, and what a thread leaves unused passes to the next. (lines, shown, total,
    named)."""
    pl = plan or _plan(host, bm, topics, by, sess8, now, opens)
    room = int(room)
    if sum(pl[t]["need"] for t in topics) <= room:
        lit = list(topics)
    else:
        lit, used = [], 0
        for t in topics:
            f = pl[t]["floor"]
            if used + f > room or (lit and used + f > room * FLOOR_SHARE):
                break
            lit.append(t)
            used += f
    out, shown, total, named = [], 0, 0, []
    left = room
    for i, t in enumerate(lit):
        p = pl[t]
        # the water-fill is over what each needs above its floor, so every shown thread keeps its newest line
        lvl = _level([pl[u]["need"] - pl[u]["floor"] for u in lit[i:]], left - sum(pl[u]["floor"] for u in lit[i:]))
        share = p["floor"] + min(p["need"] - p["floor"], lvl)
        ls, used = _take(p["cands"], int(share) - p["hs"])
        if not ls:
            named.append("%s (%d)" % (t, len(by[t])))
            continue
        out.extend(p["head"] + ls)
        shown += len(ls)
        left -= p["hs"] + used
    for t in topics:
        total += len(pl[t]["cands"])
        if t not in lit:
            named.append("%s (%d)" % (t, len(by[t])))
    return out, shown, total, named


def brain_block(host, bm, b, touched, now, limit=None, sess8=""):
    """One brain's block as ONE part of at most `limit` chars (FIRST_MAX): what he keeps saying, then the threads this
    session touched (newest touch first), then the brain's other threads (newest first). His words in full for his
    last FULL_HOURS hours, one line for older prompts; what does not fit is counted, and one command shows it all."""
    ps = bm["prompts"]
    if not ps:
        return ""
    limit = int(limit or FIRST_MAX)
    n_all = bm.get("_n", len(ps))
    head = ("[owner-prompts] HIS IMPORTANT PROMPTS - the %s brain: %d of his last %d important prompts (%s .. %s). His "
            "own words in full for his last %d hours (to %d chars, a pasted document to %d), one line for older ones, "
            "newest first. Sent once per context (again after a compaction), then only his new important prompts, "
            "hourly. His later words win over an earlier task." % (b, n_all, POOL_N, ps[0]["ts"][:16],
                                                                   ps[-1]["ts"][:16], FULL_HOURS, OWN_CUT, PASTE_CUT))
    foot_fmt = ("[owner-prompts] shown %d of the %d lines (re-asks folded); all of them: python %s --show %s. Never "
                "mark the checklist seen yourself.")
    room = limit - len(head) - 1 - len(foot_fmt % (99999, 99999, SELF, b)) - 1 - (ALSO_MAX + 1) - 8
    sb = host.standing_block(host.standing(bm, None, now, min_n=2)[:6], min(STANDING_MAX, max(0, room // 4)),
                             "WHAT HE KEEPS SAYING (%s brain):" % b, quote=100)
    sb = ascii_text(sb.rstrip("\n")) if sb else ""
    if sb:
        room -= len(sb) + 1
    newest, by = {}, defaultdict(list)
    for p in ps:
        newest[p["topic"]] = max(newest.get(p["topic"], 0.0), _epoch(p["ts"]))
        by[p["topic"]].append(p)
    rel = [t for t in reversed(list(touched or [])) if t in newest]
    rest = sorted((t for t in newest if t not in rel), key=lambda t: -newest[t])
    h_rel = "RELEVANT TO THIS SESSION (the threads your prompts touched, newest touch first):"
    h_rest = "HIS OTHER THREADS IN THE %s BRAIN (newest first):" % b.upper()
    out, shown, total, named = [head] + ([sb] if sb else []), 0, 0, []
    pr = _plan(host, bm, rel, by, sess8, now, opens=True)
    po = _plan(host, bm, rest, by, sess8, now)
    if rel:
        # REL_SHARE of the room at least, and all of what the other threads would leave unused
        need_rest = (len(h_rest) + 1 + sum(po[t]["need"] for t in rest)) if rest else 0
        r = int(max(room * REL_SHARE, room - need_rest)) - len(h_rel) - 1
        ls, k, n, nm = _fill(host, bm, rel, by, r, sess8, now, opens=True, plan=pr)
        if ls:
            out.append(h_rel)
            out.extend(ls)
            room -= len(h_rel) + 1 + sum(len(s) + 1 for s in ls)
        shown, total, named = shown + k, total + n, named + nm
    if rest:
        r = room - len(h_rest) - 1
        ls, k, n, nm = _fill(host, bm, rest, by, r, sess8, now, plan=po)
        if ls:
            out.append(h_rest)
            out.extend(ls)
        shown, total, named = shown + k, total + n, named + nm
    if named:
        also = "  also in this brain, not shown: " + ", ".join(named)
        out.append(also if len(also) <= ALSO_MAX else also[:ALSO_MAX - 4].rstrip(", ") + " ...")
    out.append(foot_fmt % (shown, total, SELF, b))
    return _clip(ascii_text("\n".join(out) + "\n"), limit)


def _clip(text, limit):
    """Never over the budget: whole lines dropped from the end of the body, the last line (the footer) kept. The room
    is counted before ascii_text, which can lengthen a line by a few chars; this is the guard, not the plan."""
    if len(text) <= limit:
        return text
    ls = text.rstrip("\n").split("\n")
    foot = [ls.pop()] if len(ls) > 1 else []
    room = limit - sum(len(s) + 1 for s in foot)
    keep, used = [], 0
    for s in ls:
        if used + len(s) + 1 > room:
            break
        keep.append(s)
        used += len(s) + 1
    return "\n".join(keep + foot) + "\n" if keep else ""


def brain_delta(host, bm, b, since, now, sess8, touched=(), limit=None):
    """His new KEEP prompts for one brain since the last delivery, from other sessions (the session holds its own), in
    at most `limit` chars (DELTA_MAX): the threads this session touched first, his words in full while they fit, then
    one line each; what does not fit is counted, and one command shows it."""
    new = [p for p in bm["prompts"] if not p.get("_dup") and _epoch(p["ts"]) > since and p["session"] != sess8]
    if not new:
        return ""
    limit = int(limit or DELTA_MAX)
    by = defaultdict(list)
    for p in new:
        by[p["topic"]].append(p)
    order = sorted(by, key=lambda t: (t not in (touched or ()), -max(_epoch(p["ts"]) for p in by[t])))
    head = ("[owner-prompts] HIS NEW IMPORTANT PROMPTS for the %s brain since %s (%d; this context already holds the "
            "block sent at its start):" % (b, time.strftime("%H:%M", time.localtime(since)), len(new)))
    foot_fmt = "  and %d more: python %s --show %s"
    room = limit - len(head) - 1 - len(foot_fmt % (99999, SELF, b)) - 1 - 8
    ls, k, n, named = _fill(host, bm, order, by, room, sess8, now)
    out = [head] + ls
    if k < n:
        out.append(foot_fmt % (n - k, SELF, b))
    return _clip(ascii_text("\n".join(out) + "\n"), limit)


# ----------------------------------------------------------------------------- delivery
def _imp_state(st):
    st = dict(st or {})
    if st.get("mode") != "important" or not isinstance(st.get("imp"), dict):
        # a context that held the simple block's queue starts over: its old parts are not this mode's
        keep = {k: st[k] for k in ("headless", "ask_at") if k in st}
        st = dict(keep, mode="important", pending=[], imp={"brains": {}, "touched": []})
    st["imp"].setdefault("brains", {})
    st["imp"].setdefault("touched", [])
    return st


def _drain(host, st, room=None):
    q = list(st.get("pending") or [])
    if not q:
        return []
    first, rest = host.refit(q.pop(0), int(room or host.CHUNK_MAX))
    if rest:
        q.insert(0, rest)
    st["pending"] = q
    return [first]


def _topic(host, text):
    f = getattr(host, "topic_of", None) or getattr(getattr(host, "H", None), "topic_of", None)
    try:
        return f(text) if f else "other"
    except Exception:
        return "other"


def nudge_line(sess, prompt):
    pid = prompt_id(str(sess)[:8], prompt)
    return ("[owner-prompts] no rule could judge his prompt just now (id %s). If it only continues or pings (\"keep "
            "working\", \"ok\"), record: python %s --verdict %s DROP --by <your agent id>   (unsure = KEEP, which needs "
            "nothing)\n" % (pid, SELF, pid))


def decide_core(host, event, sess, prompt, st, now, comp_at, hint, loader, only=None, proj=()):
    """One fire: (parts, state). comp_at = epoch of the newest compaction of this session (0 if none); hint = the tool
    step's input; loader() -> the hook's meta; only = one brain (split wiring) or None (all three); proj = the brains
    the session's project folder names. A tool step prints nothing: it only records a brain its input points at and a
    compaction. His prompt (or a session start) takes at most ONE part: a remainder left by the last fire, else one
    brain's block (once per context; the brain this prompt points at first, then main), else one brain's capped delta
    (his prompt only, in the brain's third of the hour)."""
    st = _imp_state(st)
    im, brs = st["imp"], st["imp"]["brains"]
    serve = BRAINS if only in (None, "all") else tuple(b for b in BRAINS if b == only)
    tool = event == "PreToolUse"
    parts = []
    if event == "SessionStart" and "main" in serve:
        try:
            host.prune_state(now)
        except Exception:
            pass
    rx = getattr(host, "HARNESS_EVENT_RX", None)
    if prompt and rx is not None and rx.match(prompt):
        prompt = ""
    ask = getattr(host, "ASK_RX", None)
    asked = bool(prompt and not tool and "main" in serve and ask is not None and ask.search(prompt))
    topic = _topic(host, prompt) if prompt else ""
    if topic and topic not in im["touched"]:
        im["touched"].append(topic)
        st["_save"] = 1
    fresh = set()       # the brains this prompt or tool step points at
    if topic in MOVIE_TOPICS or (prompt and MOVIE_RX.search(prompt)) or (hint and MOVIE_PATH_RX.search(hint)):
        fresh.add("movie")
    if topic in MATH_TOPICS or (prompt and MATH_RX.search(prompt)) or (hint and MATH_PATH_RX.search(hint)):
        fresh.add("math")
    for b in serve:
        x = brs.get(b) or {}
        if b in ({"main"} | fresh | set(proj or ())) and not x.get("active"):
            x["active"], x["touched_at"] = True, now
            brs[b] = x
            st["_save"] = 1
        elif b in fresh and now - float(x.get("touched_at") or 0) > TOUCH_EVERY_S:
            x["touched_at"] = now
            brs[b] = x
            st["_save"] = 1
    # a compaction newer than a delivered block: that context is gone, and so is anything queued for it. A film or math
    # brain nothing pointed at in the ACTIVE_KEEP_S before it goes quiet until his prompt or the work points at it again
    held = [float(x["full_at"]) for x in brs.values() if x.get("full_at")]
    if comp_at and held and comp_at > min(held) and comp_at > float(im.get("comp_seen") or 0):
        im["comp_seen"] = comp_at
        st["pending"] = []
        for b, x in brs.items():
            if x.get("full_at") and float(x["full_at"]) < comp_at:
                x["full_at"] = None
            if b != "main" and b not in (proj or ()) and comp_at - float(x.get("touched_at") or 0) > ACTIVE_KEEP_S:
                x["active"] = False
        st["why"] = "compaction"
        st["_save"] = 1
    if tool:
        return [], st
    # the agent that gets the prompt decides the prompts no rule could
    if event == "UserPromptSubmit" and prompt and "main" in serve and rule_verdict(prompt)[0] == "":
        parts.append(nudge_line(sess, prompt))
    room = int(host.CHUNK_MAX) - sum(len(p) for p in parts)
    # he asks what he asked: main's block is the answer, so it goes first while this context lacks it; a context that
    # holds it gets the host's ask block (his recent prompts word for word and the threads) as one part, every 30 min
    if asked and (brs.get("main") or {}).get("full_at") and \
            now - float(st.get("ask_at") or 0) >= float(getattr(host, "ASK_EVERY_S", 1800)):
        st["ask_at"] = now
        st["why"] = "ask block"
        st["_save"] = 1
        return parts + [_clip(ascii_text(host.ask_block()), room)], st
    if st.get("pending"):
        return parts + _drain(host, st, room), st
    order = [b for b in ("movie", "math") if b in fresh] + ["main"] + [b for b in ("movie", "math") if b not in fresh]
    if asked:
        order = ["main"] + [b for b in order if b != "main"]
    order = [b for b in order if b in serve]
    for b in order:
        x = brs.get(b) or {}
        if x.get("active") and not x.get("full_at"):
            text = brain_block(host, brain_metas(loader(), now)[0][b], b, im["touched"], now, min(FIRST_MAX, room),
                               str(sess)[:8])
            x["full_at"] = x["served_at"] = now
            brs[b] = x
            st["_save"] = 1
            if text:
                st["why"] = "%s block" % b
                return parts + [text], st
    if event != "UserPromptSubmit":
        return parts, st
    minute = time.localtime(now).tm_min
    for b in order:
        x = brs.get(b) or {}
        if not x.get("full_at"):
            continue
        since = float(x.get("served_at") or x["full_at"])
        lo, hi = WINDOW[b]
        if (lo <= minute < hi and now - since >= DELTA_MIN_GAP_S) or now - since >= DELTA_MAX_GAP_S:
            x["served_at"] = now
            st["_save"] = 1
            text = brain_delta(host, brain_metas(loader(), now)[0][b], b, since, now, str(sess)[:8], im["touched"],
                               min(DELTA_MAX, room))
            if text:
                st["why"] = "%s delta" % b
                return parts + [text], st
    return parts, st


def decide(host, event, sess, prompt, st, now=None, transcript="", runtime="claude", only=None):
    """The hook's decide() in mode "important" (owner_prompts_hook.decide delegates here)."""
    now = time.time() if now is None else now
    st = _imp_state(st)
    comp_at = 0.0
    if transcript:
        try:
            got = host.newest_compaction(st["imp"].get("scan") or {}, transcript, runtime, sess)
            comp_at = float(got[0] or 0)
            st["imp"]["scan"] = got[1]
            if got[2]:
                st["_save"] = 1
        except Exception as e:
            host._log_error("owner_prompts_brains.compaction", e)
    hint = getattr(host, "TOOL_HINT", "") if event == "PreToolUse" else ""
    try:
        return decide_core(host, event, sess, prompt, st, now, comp_at, hint, host.load, only,
                           project_brains(transcript))
    except Exception as e:
        host._log_error("owner_prompts_brains.decide", e)
        return [], st


class BrainEngine(object):
    """The split wiring: one hook registration per brain (--hook --brain main|movie|math). emit() sees this object as
    its engine; the state file and decide() are the brain's own, everything else is the host hook's."""

    def __init__(self, host, brain):
        object.__setattr__(self, "_h", host)
        object.__setattr__(self, "_b", brain)

    def __getattr__(self, k):
        return getattr(self._h, k)

    def __setattr__(self, k, v):
        setattr(self._h, k, v)      # emit() narrows CHUNK_MAX on its engine; the host's chunking must see it

    def state_path(self, sess):
        base = self._h.state_path(sess)
        tag = self._b or "all"
        return base[:-5] + ".%s.json" % tag if base.endswith(".json") else base + "." + tag

    def load_state(self, sess):
        try:
            with io.open(self.state_path(sess), encoding="utf-8") as f:
                st = json.load(f)
            return st if isinstance(st, dict) else {}
        except Exception:
            return {}

    def decide(self, event, sess, prompt, st, now=None, transcript="", runtime="claude"):
        return decide(self._h, event, sess, prompt, st, now, transcript, runtime, only=self._b)


# ----------------------------------------------------------------------------- controls (both directions)
def controls(host):
    """Importance, the block and the delivery, each both ways, on a synthetic corpus and a temporary ledger, state dir
    and mode file; the live ledger, state and mode are never touched. Returns [(name, ok)]."""
    global VERDICT_LEDGER, POOL_N
    import tempfile
    res = []

    def check(name, ok):
        res.append((name, bool(ok)))

    tmp = tempfile.mkdtemp(prefix="opb_controls_")
    saved = {"ledger": VERDICT_LEDGER, "pool": POOL_N, "load": host.load, "chunk": host.CHUNK_MAX,
             "mode": getattr(host, "MODE_FILE", None), "state": getattr(host, "STATE_DIR", None),
             "sched": getattr(host, "SCHEDULE_FILE", None), "shadow": getattr(host, "SHADOW_LOG", None)}
    VERDICT_LEDGER = os.path.join(tmp, "verdicts.jsonl")
    host.STATE_DIR = os.path.join(tmp, "state")
    os.makedirs(host.STATE_DIR, exist_ok=True)
    if saved["sched"] is not None:
        host.SCHEDULE_FILE = os.path.join(tmp, "schedule")
    if saved["shadow"] is not None:
        host.SHADOW_LOG = os.path.join(tmp, "shadow.log")
    try:
        now = time.time()

        def ts(ago, base=None):
            return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime((now if base is None else base) - ago))

        # ---- 1. importance
        pings = ["keep working", "ok", "yes do it", "continue", "speed things up", "keep wroking", "kee working",
                 "you still working on this?", "try again", "/chrome", "reply with the word OK and nothing else."]
        check("1 continuations and pings are DROP (%d)" % len(pings), all(rule_verdict(t)[0] == "DROP" for t in pings))
        keeps = ["one movie at a time", "if a moive already has a codex thuamnial DON'T touch it",
                 "i also prefer the flims go longer, like 1.25 hours, that is my prefrence record that as well",
                 "ER happen loalcly they shoudl all go through kaggle", "keep working on this for the next 10 hours",
                 "speed up the movie making", "what about the second index", "/dream", "why is the box idle",
                 "<pasted_content id=1>a long review</pasted_content id=1> ok"]
        check("1 orders, questions, durations and pastes are never DROP by rule (%d)" % len(keeps),
              all(rule_verdict(t)[0] != "DROP" for t in keeps))
        r3 = [{"ts": ts(3000), "session": "s1", "text": "fix the subtitles on the rome film", "topic": "films and videos"},
              {"ts": ts(2400), "session": "s2", "text": "fix the subtitles on the rome film", "topic": "films and videos"},
              {"ts": ts(0), "session": "s2", "text": "fix the subtitles on the rome film", "topic": "films and videos"}]
        check("1 the same prompt again within 30 minutes is DROP, after 30 minutes KEEP",
              [c["verdict"] for c in classify(r3, {})] == ["KEEP", "DROP", "KEEP"])
        u = {"ts": ts(10), "session": "s1", "text": "one movie at a time", "topic": "films and videos"}
        k = {"ts": ts(9), "session": "s1", "text": "keep working", "topic": "other"}
        uid, kid = prompt_id("s1", u["text"]), prompt_id("s1", k["text"])
        append_records([{"id": uid, "verdict": "DROP", "by": "be1-batch"}, {"id": uid, "verdict": "KEEP", "by": "agent-x"},
                        {"id": uid, "verdict": "DROP", "by": "be1-batch"}, {"id": kid, "verdict": "KEEP", "by": "agent-y"}])
        c2 = classify([u, k])
        check("1 the receiving agent's verdict outranks the batch judge and the rules",
              c2[0]["verdict"] == "KEEP" and c2[0]["by"] == "agent-x" and c2[1]["verdict"] == "KEEP")
        append_records([{"id": uid, "verdict": "DROP", "by": "agent-z"}])
        check("1 a later agent verdict wins within its class", classify([u])[0]["verdict"] == "DROP")
        VERDICT_LEDGER = os.path.join(tmp, "verdicts_empty.jsonl")

        # ---- 2. the block
        P = []

        def add(ago, text, topic, sess="sa000000"):
            P.append({"ts": ts(ago), "session": sess, "text": text, "topic": topic, "refires": 0})

        long_order = "render the rome film again with the narrator slower and the map bigger; " * 14
        add(90000, "keep working", "films and videos")
        add(89000, long_order, "films and videos")
        add(88000, "here is the review <pasted_content id=7>" + "X" * 2000 + "</pasted_content id=7> fix every point "
            "it makes in the genesis film", "films and videos")
        add(87000, "check the proof of the hadamard lemma 3 before the paper goes out", "math papers")
        add(86000, "all agents must log their claims on the mesh", "brain, hooks and agents")
        add(85000, "queue the runs for the new rule on the box", "trading simulations")
        add(84000, "don\u2019t touch the thumbnail \u2014 ever, on any film", "films and videos")
        for i in range(30):
            add(80000 - i * 600, "main order %02d: move the backup of the store to the big disk and check its hashes "
                "twice before anything else touches it, then report the sizes" % i, "this computer")
        for i in range(6):
            add(60000 - i * 10, "make film number %d longer, 75 minutes" % i, "films and videos")
        meta = {"prompts": sorted(P, key=lambda p: p["ts"]), "threads": [], "checklist_dir": "", "built": "t",
                "built_epoch": int(now), "sessions": 1}
        bms = brain_metas(meta, now)[0]
        mv = brain_block(host, bms["movie"], "movie", [], now)
        ma = brain_block(host, bms["math"], "math", [], now)
        mn = brain_block(host, bms["main"], "main", ["trading simulations"], now)
        allb = mv + ma + mn
        check("2 a ping is in no block", not re.search(r"\] keep working\s*$", allb, re.M))
        check("2 his own words in full to 600 chars, cut after", long_order[:560] in mv and long_order[:640] not in mv)
        check("2 a pasted document is cut to 300 chars and his words around it are kept",
              "X" * 300 in mv and "X" * 301 not in mv and "[pasted 2000 chars" in mv and "here is the review" in mv
              and "fix every point it makes in the genesis film" in mv)
        check("2 each brain serves its own prompts (math / movie / main)",
              "hadamard lemma 3" in ma and "make film number" not in ma and "make film number 5" in mv
              and "hadamard" not in mv and "log their claims on the mesh" in mn and "make film number" not in mn)
        i_rel, i_th, i_oth = mn.find("RELEVANT TO THIS SESSION"), mn.find("## trading simulations"), mn.find("HIS OTHER THREADS")
        check("2 organised: the threads this session touched first, then his other threads",
              -1 < i_rel < i_th < i_oth < mn.find("## this computer"))
        check("2 ASCII", all(ord(ch) < 128 for ch in allb) and "don't touch the thumbnail - ever" in mv)
        POOL_N = 3
        bm3 = brain_metas(meta, now)[0]
        mv3 = brain_block(host, bm3["movie"], "movie", [], now)
        check("2 the pool is his last POOL_N important prompts",
              "make film number 5" in mv3 and "make film number 2" not in mv3 and not bm3["math"]["prompts"])
        POOL_N = saved["pool"]

        # ---- 3. the delivery (v2): one part per brain per context, on his prompt; a tool step prints nothing
        host.load = lambda: meta
        loader = host.load
        host.CHUNK_MAX = 9391          # what emit leaves a Claude part: 9,500 less the history notice
        sizes = []

        def fire(ev, sess, prompt, st, t, comp=0.0, hint=""):
            parts, st = decide_core(host, ev, sess, prompt, st, t, comp, hint, loader)
            sizes.extend(len(p) for p in parts)
            return parts, st

        def kind(parts):
            out = []
            for p in parts:
                m = re.match(r"\[owner-prompts\] HIS (NEW )?IMPORTANT PROMPTS (?:- the |for the )(\w+) brain", p)
                out.append((("delta:" if m.group(1) else "block:") + m.group(2)) if m else
                           ("nudge" if "no rule could judge" in p else "other"))
            return out

        def addp(t_at, sess, text, topic="this computer"):
            meta["prompts"] = sorted(meta["prompts"] + [{"ts": ts(0, t_at), "session": sess, "text": text, "topic": topic,
                                                         "refires": 0}], key=lambda p: p["ts"])

        st, t = {}, now
        p1, st = fire("UserPromptSubmit", "sessA0001", "queue the lanes for the new law", st, t)
        tch = list(st["imp"]["touched"])
        want = brain_block(host, brain_metas(meta, now)[0]["main"], "main", tch, now, min(FIRST_MAX, 9391), "sessA000")
        check("3 his first prompt gets the main block as ONE part, the whole block",
              kind(p1) == ["block:main"] and p1[0] == want and len(p1[0]) <= FIRST_MAX)
        t += 60
        p2, st = fire("UserPromptSubmit", "sessA0001", "queue the lanes for the new law on box 3", st, t)
        check("3 a context that holds the block is not sent it again", p2 == [])
        t += 60
        p3, st = fire("PreToolUse", "sessA0001", "", st, t, 0.0,
                      '{"command": "python ~/movie_projects/render_one.py rome"}')
        act = bool(st["imp"]["brains"].get("movie", {}).get("active"))
        t += 60
        p4, st = fire("UserPromptSubmit", "sessA0001", "queue the lanes for the new law on box 3 now", st, t)
        t += 60
        p5, st = fire("UserPromptSubmit", "sessA0001", "render the genesis film again", st, t)
        check("3 a tool step prints nothing; the film work it shows brings the movie block at his next prompt, once",
              p3 == [] and act and kind(p4) == ["block:movie"] and p5 == [])
        s2, t2, kinds2 = {}, now, []
        for i in range(40):
            ev = "UserPromptSubmit" if i % 10 == 0 else "PreToolUse"
            parts, s2 = fire(ev, "sessB0002", "queue the lanes for the new law" if ev != "PreToolUse" else "", s2, t2, 0.0,
                             '{"command": "git status"}' if ev == "PreToolUse" else "")
            kinds2 += kind(parts)
            t2 += 30
        check("3 a session with no film work gets the main block once and never the movie block", kinds2 == ["block:main"])
        comp = t + 30
        t = comp + 30
        p6, st = fire("PreToolUse", "sessA0001", "", st, t, comp)
        reset = not st["imp"]["brains"]["main"].get("full_at") and not st["imp"]["brains"]["movie"].get("full_at")
        seq = []
        for i in range(4):
            t += 30
            parts, st = fire("UserPromptSubmit", "sessA0001", "queue the lanes for the new law, round %d" % i, st, t, comp)
            seq.append(kind(parts))
        check("3 after a compaction the tool step stays silent, then his prompts bring main, then movie, then nothing",
              p6 == [] and reset and seq == [["block:main"], ["block:movie"], [], []])
        lt = time.localtime(t + 2 * 3600)
        h0 = t + 2 * 3600 - lt.tm_min * 60 - lt.tm_sec          # the start of a local hour, at least an hour on
        for b in ("main", "movie"):
            st["imp"]["brains"][b]["served_at"] = h0 - 50 * 60  # both served at minute 10 of the hour before
        addp(h0 - 40 * 60, "sB000002", "turn the upload queue back on tonight")
        addp(h0 - 39 * 60, "sB000002", "keep working")
        addp(h0 - 38 * 60, "sessA000", "stop the old render queue on this box")
        p7, st = fire("UserPromptSubmit", "sessA0001", "queue the lanes for the new law, check", st, h0 - 5 * 60)
        p8, st = fire("PreToolUse", "sessA0001", "", st, h0 + 4 * 60)
        p9, st = fire("UserPromptSubmit", "sessA0001", "queue the lanes for the new law, again", st, h0 + 5 * 60)
        d = "".join(p9)
        check("3 no delta outside the brain's third of the hour before 80 minutes, and none on a tool step",
              p7 == [] and p8 == [])
        check("3 a delta in main's third of the hour: his new important prompts from other sessions only, no 'more' line",
              kind(p9) == ["delta:main"] and "turn the upload queue back on tonight" in d and "] keep working" not in d
              and "stop the old render queue" not in d and "more: python" not in d and len(d) <= DELTA_MAX)
        p10, st = fire("UserPromptSubmit", "sessA0001", "queue the lanes for the new law, again 2", st, h0 + 6 * 60)
        check("3 a delta is not repeated", p10 == [])
        st["imp"]["brains"]["main"]["served_at"] = h0 + 21 * 60
        addp(h0 + 30 * 60, "sB000002", "switch the archive job to the new disk")
        p11, st = fire("UserPromptSubmit", "sessA0001", "queue the lanes for the new law, late", st, h0 + 103 * 60)
        check("3 after 80 minutes the delta goes out outside its third of the hour",
              kind(p11) == ["delta:main"] and "switch the archive job" in "".join(p11))
        for i in range(40):
            addp(h0 + 110 * 60 + i * 30, "sC00000%d" % (i % 3), "order %02d: move the backup of the store to the big disk "
                 "and check its hashes twice before anything else touches it, then report the sizes" % i)
        p12, st = fire("UserPromptSubmit", "sessA0001", "queue the lanes for the new law, next hour", st, h0 + 185 * 60)
        d = "".join(p12)
        check("3 a delta too big for its cap is cut to DELTA_MAX with an 'and N more' line and the command",
              kind(p12) == ["delta:main"] and len(d) <= DELTA_MAX and re.search(r"  and \d+ more: python \S+ --show main", d)
              and "order 39" in d)
        old = {"mode": "simple", "served_at": now - 100,
               "pending": ["[owner-prompts] HIS PROMPTS - part 2 of 71 (continuing the delivery started 10:00)\nold block text"]}
        pt, st_old = fire("PreToolUse", "sessC0003", "", dict(old), now)
        pu, st_old = fire("UserPromptSubmit", "sessC0003", "queue the lanes for the new law", st_old, now + 5)
        check("3 from the simple block: its 71-part queue is dropped, the tool step is silent, his prompt gets main",
              pt == [] and "old block text" not in "".join(pu) and kind(pu) == ["block:main"] and not st_old.get("pending"))
        pn, _ = fire("UserPromptSubmit", "sessN0008", "for the codex as well", {}, now)
        pk, _ = fire("UserPromptSubmit", "sessN0009", "queue the lanes for the new law", {}, now)
        check("3 a prompt no rule can judge brings the verdict nudge with its id beside the block, inside the cap; a judged one "
              "does not", kind(pn) == ["nudge", "block:main"] and prompt_id("sessN000", "for the codex as well") in pn[0]
              and sum(len(p) for p in pn) <= host.CHUNK_MAX and kind(pk) == ["block:main"])
        check("3 every part so far is inside the part cap and ends with a newline",
              sizes and max(sizes) <= host.CHUNK_MAX)
        tr = os.path.join(tmp, "transcript.jsonl")
        with io.open(tr, "w", encoding="utf-8") as f:
            f.write(json.dumps({"type": "user", "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(now - 7200)),
                                "message": {"role": "user", "content": "hi"}}) + "\n")
            f.write(json.dumps({"type": "system", "subtype": "compact_boundary",
                                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(now - 60))}) + "\n")

        def held(full):
            return {"mode": "important", "pending": [],
                    "imp": {"brains": {"main": {"active": True, "full_at": full, "served_at": full}}, "touched": []}}
        pc0, stc = decide(host, "PreToolUse", "sessE0005", "", held(now - 3600), now, tr, "claude")
        fa0 = stc["imp"]["brains"]["main"].get("full_at")     # read now: the next call writes into the same dict
        pc, _ = decide(host, "UserPromptSubmit", "sessE0005", "queue the lanes for the new law", stc, now + 5, tr, "claude")
        check("3 a compaction in the transcript: the tool step is silent, the block comes back at his next prompt",
              pc0 == [] and not fa0 and kind(pc) == ["block:main"])
        pn2, _ = decide(host, "UserPromptSubmit", "sessE0006", "queue the lanes for the new law", held(now - 30), now, tr,
                        "claude")
        check("3 ... and a block sent after that compaction is not sent again", pn2 == [])
        pm = os.path.join(tmp, "C--Users-me-movie-projects", "sessH0007.jsonl")
        check("3 the project folder: a film project names the movie brain, a math project the math brain, home and a Codex "
              "rollout none", project_brains(pm) == {"movie"}
              and project_brains(os.path.join(tmp, "C--Users-me-open-problems", "x.jsonl")) == {"math"}
              and project_brains(os.path.join(tmp, "C--Users-me", "x.jsonl")) == set()
              and project_brains(os.path.expanduser("~/.codex/sessions/2026/10/08/rollout-x.jsonl")) == set())
        os.makedirs(os.path.dirname(pm), exist_ok=True)
        with io.open(pm, "w", encoding="utf-8") as f:
            f.write(json.dumps({"type": "user", "message": {"role": "user", "content": "hi"}}) + "\n")
        q1, sh = decide(host, "UserPromptSubmit", "sessH0007", "queue the lanes for the new law", {}, now, pm, "claude")
        q2, sh = decide(host, "UserPromptSubmit", "sessH0007", "queue the lanes for the new law 2", sh, now + 5, pm, "claude")
        q3, sh = decide(host, "UserPromptSubmit", "sessH0007", "queue the lanes for the new law 3", sh, now + 10, pm, "claude")
        check("3 a session in a film project: main at its first prompt, the movie block at the next, then nothing",
              kind(q1) == ["block:main"] and kind(q2) == ["block:movie"] and q3 == [])
        mf = os.path.join(tmp, "mode")
        if saved["mode"] is not None:
            host.MODE_FILE = mf
            with io.open(mf, "w", encoding="utf-8") as f:
                f.write("important\n")
            parts, _ = host.decide("UserPromptSubmit", "sessD0004", "queue the lanes for the new law", {}, now)
            ok_imp = host.mode() == "important" and kind(parts) == ["block:main"]
            with io.open(mf, "w", encoding="utf-8") as f:
                f.write("simple\n")
            parts, _ = host.decide("UserPromptSubmit", "sessD0005", "queue the lanes for the new law", {}, now)
            ok_simple = host.mode() == "simple" and not any("HIS IMPORTANT PROMPTS" in p for p in parts)
            check("3 the hook delegates in mode important and not in mode simple", ok_imp and ok_simple)
        eng = BrainEngine(host, "movie")
        p1 = eng.state_path("sessF0006")
        check("3 split wiring: each brain's hook keeps its own state file",
              p1.endswith(".movie.json") and p1 != host.state_path("sessF0006"))
        parts, stm = eng.decide("UserPromptSubmit", "sessF0006", "queue the lanes for the new law", {}, now)
        parts2, stm = eng.decide("UserPromptSubmit", "sessF0006", "render the genesis film again", stm, now + 5)
        parts3, stm = eng.decide("PreToolUse", "sessF0006", "", stm, now + 10)
        check("3 split wiring: the movie hook is silent until film work, then sends the movie block, then nothing",
              parts == [] and kind(parts2) == ["block:movie"] and parts3 == [])

        # ---- 3b. he asks what he asked; a quiet film brain; SessionStart (no test here prunes the live state)
        def fire2(ev, sess, prompt, st, t, comp=0.0):
            return decide_core(host, ev, sess, prompt, st, t, comp, "", loader)

        def kd(ps):
            return [k for k in kind(ps) if k != "nudge"]

        saved_ab, saved_pr = getattr(host, "ask_block", None), getattr(host, "prune_state", None)
        host.ask_block = lambda: "[owner-prompts] WHAT HE ASKED (test)\n" + "his words, line by line\n" * 900 + "end\n"
        host.prune_state = lambda now=None: None
        try:
            ta = now + 50000
            a1, sta = fire2("UserPromptSubmit", "sessK0011", "what did i ask the agents today", {}, ta)
            a2, sta = fire2("UserPromptSubmit", "sessK0011", "what did i ask the agents today", sta, ta + 60)
            a3, sta = fire2("UserPromptSubmit", "sessK0011", "what did i ask the agents today", sta, ta + 120)
            a4, sta = fire2("UserPromptSubmit", "sessK0011", "what did i ask the agents today", sta, ta + 1900)
            td = now + 90000
            r0, stl = fire2("UserPromptSubmit", "sessL0012", "render the genesis film again tonight", {}, td)
            r0b, stl = fire2("UserPromptSubmit", "sessL0012", "queue the lanes for the new law", stl, td + 60)
            c1 = td + 120
            r1, stl = fire2("SessionStart", "sessL0012", "", stl, c1 + 1, c1)
            r2, stl = fire2("UserPromptSubmit", "sessL0012", "queue the lanes for the new law on box 3", stl, c1 + 60, c1)
            c2 = c1 + ACTIVE_KEEP_S + 3600
            r3, stl = fire2("SessionStart", "sessL0012", "", stl, c2 + 1, c2)
            r4, stl = fire2("UserPromptSubmit", "sessL0012", "queue the lanes for the new law on box 3", stl, c2 + 60, c2)
            r5, stl = fire2("UserPromptSubmit", "sessL0012", "render the genesis film again tonight", stl, c2 + 120, c2)
            tss = now + 130000
            s1, stm2 = fire2("SessionStart", "sessM0013", "", {}, tss)
            s2, stm2 = fire2("SessionStart", "sessM0013", "", stm2, tss + 60)
            cm = tss + 120
            s3, stm2 = fire2("SessionStart", "sessM0013", "", stm2, cm + 1, cm)
        finally:
            if saved_ab is not None:
                host.ask_block = saved_ab
            if saved_pr is not None:
                host.prune_state = saved_pr
        a2o = [p for p in a2 if kind([p]) == ["other"]] or [""]
        check("3 an ask in a context without main's block gets main's block, not the ask block", kd(a1) == ["block:main"])
        check("3 an ask in a context that holds main's block gets the ask block as ONE part inside the cap, none queued",
              kd(a2) == ["other"] and "WHAT HE ASKED (test)" in a2o[0] and sum(len(p) for p in a2) <= host.CHUNK_MAX
              and not sta.get("pending"))
        check("3 the ask block is not re-sent within ASK_EVERY_S, and is after it", kd(a3) == [] and kd(a4) == ["other"])
        check("3 a compaction: main first at SessionStart, then the film brain touched within ACTIVE_KEEP_S",
              kd(r0) == ["block:movie"] and kd(r0b) == ["block:main"] and kd(r1) == ["block:main"]
              and kd(r2) == ["block:movie"])
        check("3 a compaction: a film brain untouched for ACTIVE_KEEP_S stays quiet until film work points at it again",
              kd(r3) == ["block:main"] and kd(r4) == [] and kd(r5) == ["block:movie"])
        check("3 SessionStart: the block at start, nothing on a resume of the same context, the block after a compaction",
              kd(s1) == ["block:main"] and s2 == [] and kd(s3) == ["block:main"])

        # ---- 4. the budget: a corpus far bigger than one part, at every cap
        B = []
        for i in range(240):
            tp = ("this computer", "trading simulations", "brain, hooks and agents")[i % 3]
            B.append({"ts": ts(30 + i * 600), "session": "sx%06d" % (i % 7), "topic": tp, "refires": 0,
                      "text": "order %03d for %s: %s" % (i, tp, "move the store to the big disk and check its hashes "
                                                             "twice " * 8)})
        for i in range(120):
            B.append({"ts": ts(40 + i * 900), "session": "sy%06d" % (i % 5), "topic": "films and videos", "refires": 0,
                      "text": "film order %03d: %s" % (i, "make the narrator slower and the map larger in the rome film "
                                                           * 8)})
        for i in range(30):
            B.append({"ts": ts(200000 + i * 7200), "session": "sz000000", "topic": "side thread %02d" % i, "refires": 0,
                      "text": "side order %02d: fix the old cron that writes the index twice" % i})
        for i in range(60):
            B.append({"ts": ts(50 + i * 1500), "session": "sw%06d" % (i % 4), "topic": "math papers", "refires": 0,
                      "text": "math order %03d: %s" % (i, "check the proof of the second lemma line by line and cite the "
                                                          "theorem " * 6)})
        G = [{"ts": ts(20), "session": "sessG000", "topic": "this computer", "refires": 0,
              "text": "OWN-RECENT " + "keep the archive on the big disk and check the hashes, " * 10 + "TAIL-OWN"},
             {"ts": ts(25), "session": "sx999999", "topic": "this computer", "refires": 0,
              "text": "OTHER-RECENT " + "keep the archive on the big disk and check the hashes, " * 10 + "TAIL-OTHER"},
             {"ts": ts(60 * 3600), "session": "sx999999", "topic": "this computer", "refires": 0,
              "text": "OTHER-OLD " + "keep the archive on the big disk and check the hashes, " * 10 + "TAIL-OLD"}]
        B.extend(G)

        def metas(ps):
            return brain_metas({"prompts": sorted(ps, key=lambda p: p["ts"]), "threads": [], "checklist_dir": "",
                                "built": "t", "built_epoch": int(now), "sessions": 9}, now)[0]
        bmb = metas(B)
        okb, cut_main, newest_main = True, True, True
        for cap in (9391, 8891, 3000):
            for b in ("main", "movie", "math"):
                x = brain_block(host, bmb[b], b, [], now, min(FIRST_MAX, cap), "sessG000")
                m = re.search(r"shown (\d+) of the (\d+) lines", x)
                okb = okb and bool(x) and len(x) <= min(FIRST_MAX, cap) and x.endswith("\n") and bool(m) \
                    and all(ord(ch) < 128 for ch in x) and ("--show %s. Never mark the checklist seen yourself." % b) in x
                if b == "main":
                    cut_main = cut_main and int(m.group(1)) < int(m.group(2)) and "also in this brain, not shown:" in x
                    newest_main = newest_main and "OTHER-RECENT" in x
        check("4 at caps 9,391 / 8,891 / 3,000 every brain's block is one part inside the cap, ASCII, with its footer",
              okb)
        check("4 a block too big for the cap says how much it shows, names the threads it left out, and keeps the newest",
              cut_main and newest_main)
        sm = brain_block(host, bms["main"], "main", [], now)
        m = re.search(r"shown (\d+) of the (\d+) lines", sm)
        check("4 ... and a block that fits shows every line and names nothing left out",
              bool(m) and m.group(1) == m.group(2) and "also in this brain, not shown" not in sm)
        xs = brain_block(host, metas(G + B[:6])["main"], "main", ["this computer"], now, FIRST_MAX, "sessG000")
        check("4 his words in full from other sessions in the last 48 hours; one line for the session's own and for older",
              "TAIL-OTHER" in xs and "OWN-RECENT" in xs and "TAIL-OWN" not in xs and "OTHER-OLD" in xs
              and "TAIL-OLD" not in xs)
        x = brain_block(host, bmb["main"], "main", ["this computer"], now, FIRST_MAX, "sessG000")
        mvb = brain_block(host, bmb["movie"], "movie", [], now, FIRST_MAX, "sessG000")
        mtb = brain_block(host, bmb["math"], "math", [], now, FIRST_MAX, "sessG000")
        check("4 the movie block holds film work only, the math block math only, and the main block neither",
              "film order 000" in mvb and "order 000 for" not in mvb and "math order" not in mvb
              and "math order 000" in mtb and "film order" not in mtb and "order 000 for" not in mtb
              and "film order" not in x and "math order" not in x and "order 000 for this computer" in x)
        rt = re.findall(r"^  ## (.+?) \(", x, re.M)
        check("4 the threads this session touched come first and get most of the room; the others newest first, the "
              "oldest only named", rt[:1] == ["this computer"] and "side thread 29" not in rt
              and "also in this brain, not shown: " in x and x.find("TAIL-OTHER") > 0)
        dl = brain_delta(host, bmb["main"], "main", now - 48 * 3600, now, "sessG000", (), DELTA_MAX)
        check("4 a delta over two days of prompts stays inside DELTA_MAX and counts the rest",
              len(dl) <= DELTA_MAX and re.search(r"  and \d+ more: python", dl))
    finally:
        VERDICT_LEDGER, POOL_N, host.load, host.CHUNK_MAX = saved["ledger"], saved["pool"], saved["load"], saved["chunk"]
        for k, name in (("mode", "MODE_FILE"), ("state", "STATE_DIR"), ("sched", "SCHEDULE_FILE"), ("shadow", "SHADOW_LOG")):
            if saved[k] is not None:
                setattr(host, name, saved[k])
        _LED.clear()
        _BM.clear()
    return res


# ----------------------------------------------------------------------------- tools
def _rec(c, verdict, by, why):
    return {"id": c["id"], "ts": c["p"].get("ts"), "sess": str(c["p"].get("session") or "")[:8],
            "text": ascii_text(own_words(c["p"].get("text") or ""))[:160], "verdict": verdict, "brains": c["brains"],
            "gist": "", "by": by, "why": why}


def seed(host, judge_dir=None):
    """One rule record per prompt not yet in the ledger, then the batch judgments (judge_in: every id KEEP;
    judge_out/judged_round*.tsv: id, verdict, why - later files win)."""
    cl = classify(host.load()["prompts"], {})
    have, last = set(), {}
    try:
        with io.open(VERDICT_LEDGER, encoding="utf-8", errors="replace") as f:
            for ln in f:
                try:
                    r = json.loads(ln)
                except Exception:
                    continue
                have.add(r.get("id"))
                last[(r.get("id"), r.get("by"))] = str(r.get("verdict")).upper()
    except OSError:
        pass
    recs = []
    for c in cl:
        if c["id"] not in have:
            have.add(c["id"])
            recs.append(_rec(c, "auto" if c["why"] == "unclear" else c["verdict"], "rule", c["why"]))
    judged = {}
    if judge_dir:
        for pth in sorted(glob.glob(os.path.join(judge_dir, "judge_in", "batch_*.txt"))):
            for ln in io.open(pth, encoding="utf-8"):
                f = ln.split("\t")
                if len(f) >= 4:
                    judged[f[0]] = ("KEEP", "judge:keep")
        for pth in sorted(glob.glob(os.path.join(judge_dir, "judge_out", "judged_round*.tsv"))):
            for ln in io.open(pth, encoding="utf-8"):
                if ln.startswith("#") or not ln.strip():
                    continue
                f = ln.rstrip("\n").split("\t")
                judged[f[0]] = (f[1].upper(), "judge:" + (f[2] if len(f) > 2 else ""))
    byid = {}
    for c in cl:
        byid.setdefault(c["id"], c)
    nj = 0
    for pid, (v, why) in judged.items():
        if last.get((pid, "be1-batch")) == v or pid not in byid:
            continue
        recs.append(_rec(byid[pid], v, "be1-batch", why))
        nj += 1
    append_records(recs)
    return "seeded %d records (%d batch judgments) into %s\n" % (len(recs), nj, VERDICT_LEDGER)


def status(host):
    t0 = time.time()
    meta = host.load()
    t1 = time.time()
    bms, cl, keep = brain_metas(meta, None)
    t2 = time.time()
    v = Counter(c["verdict"] for c in cl)
    why = Counter(c["why"] if c["by"] == "rule" else "by " + c["by"] for c in cl)
    span = (_epoch(keep[-1]["p"]["ts"]) - _epoch(keep[0]["p"]["ts"])) / 86400.0 if keep else 0.0
    out = ["owner_prompts_brains status",
           "  corpus %d prompts (%s .. %s); load %.2f s, classify + pool %.2f s" % (
               len(cl), cl[0]["p"]["ts"][:16] if cl else "?", cl[-1]["p"]["ts"][:16] if cl else "?", t1 - t0, t2 - t1),
           "  KEEP %d  DROP %d   %s" % (v["KEEP"], v["DROP"], ", ".join("%s %d" % kv for kv in why.most_common())),
           "  pool: the last %d KEEP, %s .. %s = %.2f days" % (
               len(keep), keep[0]["p"]["ts"][:16] if keep else "?", keep[-1]["p"]["ts"][:16] if keep else "?", span)]
    for b in BRAINS:
        t3 = time.time()
        blk = brain_block(host, bms[b], b, [], time.time())
        out.append("  %-5s %4d prompts, block %8d chars = %d parts of %d, render %.2f s" % (
            b, bms[b]["_n"], len(blk), -(-len(blk) // max(1, host.part_limit())), host.CHUNK_MAX, time.time() - t3))
    out.append("  ledger %s: %d agent/judge verdicts in force" % (VERDICT_LEDGER, len(read_ledger())))
    return "\n".join(out) + "\n"


def rules_report(host):
    """The rules now against the batch judgments: no judged KEEP may be DROP by a rule."""
    led = read_ledger()
    tab, bad = Counter(), []
    for p in host.load()["prompts"]:
        r = led.get(prompt_id(p.get("session"), p.get("text")))
        if not r or r.get("by") != "be1-batch":
            continue
        v = rule_verdict(p.get("text"))[0] or "unclear"
        tab["judged %s -> rule %s" % (r["verdict"], v)] += 1
        if r["verdict"] == "KEEP" and v == "DROP":
            bad.append(own_words(p.get("text"))[:100])
    out = ["rules vs the batch judgments: " + ", ".join("%s %d" % kv for kv in sorted(tab.items())),
           "judged KEEP that a rule now DROPs: %d" % len(bad)] + ["   " + b for b in bad]
    return "\n".join(out) + "\n"


def handcheck(host, n, seed_, path):
    cl = classify(host.load()["prompts"])
    sample = sorted(random.Random(seed_).sample(cl, n), key=lambda c: c["p"]["ts"])
    with io.open(path, "w", encoding="utf-8") as f:
        f.write("#id\tts\tverdict\twhy\tby\town_len\ttext (his own words; [+paste] = a pasted document followed)\n")
        for c in sample:
            raw = c["p"].get("text") or ""
            t = re.sub(r"\s+", " ", own_words(raw)).strip()
            f.write("%s\t%s\t%s\t%s\t%s\t%d\t%s%s\n" % (c["id"], c["p"]["ts"][5:16], c["verdict"], c["why"][:28], c["by"],
                                                       len(t), "[+paste] " if "<pasted_content" in raw else "",
                                                       ascii_text(t[:180])))
    return "handcheck: %d rows to %s (%s)\n" % (len(sample), path, dict(Counter(c["verdict"] for c in sample)))


def measure(host, paths):
    """Replay real sessions through this mode, his prompts as they stood at each moment (the pool is cut at the fire's
    time); a compaction starts a new context. Three wirings: the v2 row (one hook fired on every prompt and every tool
    step) and the two proposed ones (fired on his prompt and on SessionStart - at the session's first event and after
    each compaction, the boundary already written): one hook serving all three brains, or three hooks (--brain
    main|movie|math), each its own state. The replay never prunes the live state directory."""
    meta = host.load()
    out = []
    pat = re.compile(r"\[owner-prompts\] HIS (NEW )?IMPORTANT PROMPTS (?:- the |for the )(\w+) brain")
    saved_prune = getattr(host, "prune_state", None)
    host.prune_state = lambda now=None: None
    try:
        for path in paths:
            tl = json.load(io.open(path, encoding="utf-8"))
            sess = str(tl.get("session") or os.path.basename(path))
            evs = tl["events"]
            for wiring, hooks, tools, starts in (("one hook, prompt + tool steps", [None], True, False),
                                                 ("one hook, prompt + SessionStart", [None], False, True),
                                                 ("three hooks, prompt + SessionStart", list(BRAINS), False, True)):
                sts = {h: {} for h in hooks}
                per_ctx, per_hour, per_day, per_brain, n = Counter(), Counter(), Counter(), Counter(), Counter()
                t0 = time.time()

                def fire(ev, t, text, comp_at, ctx):
                    for h in hooks:
                        st = sts[h]
                        if ev == "PreToolUse" and not st:
                            continue
                        parts, st = decide_core(host, ev, sess, text if ev == "UserPromptSubmit" else "", st, t,
                                                comp_at, text if ev == "PreToolUse" else "", lambda: meta, only=h)
                        st.pop("_save", None)
                        sts[h] = st
                        txt = "".join(parts)
                        if not txt:
                            continue
                        first, rest = host.refit(txt, host.CHUNK_MAX)
                        if rest:
                            st["pending"] = [rest] + list(st.get("pending") or [])
                        m = pat.search(first)
                        n["parts"] += 1
                        n["deltas" if m and m.group(1) else "blocks" if m else "other"] += 1
                        n["biggest"] = max(n["biggest"], len(first))
                        per_brain[m.group(2) if m else "other"] += len(first)
                        per_ctx[ctx] += len(first)
                        per_hour[int(t // 3600)] += len(first)
                        per_day[time.strftime("%m-%d", time.localtime(t))] += len(first)

                comp_at, ctx = 0.0, 0
                active = set()
                if starts and evs:
                    fire("SessionStart", float(evs[0][0]), "", comp_at, ctx)
                for e in evs:
                    t, kind, text = float(e[0]), e[1], (e[2] if len(e) > 2 else "") or ""
                    active.add(int(t // 3600))
                    if kind == "compact":
                        comp_at, ctx = t, ctx + 1
                        if starts:
                            fire("SessionStart", t, "", comp_at, ctx)
                    elif kind == "prompt" or tools:
                        fire("UserPromptSubmit" if kind == "prompt" else "PreToolUse", t, text, comp_at, ctx)
                vals = sorted(per_ctx.values())
                tot = sum(vals)
                out.append("%s %s: %d chars in %d parts; %d of %d contexts got something (median %d, max %d per "
                           "context that did); %d per active hour (%d hours); largest part %d; by brain %s; by day %s; "
                           "%d blocks, %d deltas, %d other; replay %.1f s"
                           % (sess[:8], wiring, tot, n["parts"], len(vals), ctx + 1,
                              vals[len(vals) // 2] if vals else 0, vals[-1] if vals else 0,
                              tot // max(1, len(active)), len(active), n["biggest"], dict(per_brain),
                              dict(sorted(per_day.items())), n["blocks"], n["deltas"], n["other"], time.time() - t0))
    finally:
        if saved_prune is not None:
            host.prune_state = saved_prune
    return "\n".join(out) + "\n"


def main(argv=None):
    sys.modules.setdefault("owner_prompts_brains", sys.modules[__name__])
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if "--hook" in argv:
        brain = argv[argv.index("--brain") + 1] if "--brain" in argv and len(argv) > argv.index("--brain") + 1 else "all"
        runtime = "codex" if ("codex" in argv or os.environ.get("CODEX_THREAD_ID")) else "claude"
        host = None
        try:
            import owner_prompts_hook as host
            import owner_context_delivery
            if runtime == "codex" and "OWNER_PROMPTS_CHUNK_MAX" not in os.environ:
                host.CHUNK_MAX = min(host.CHUNK_MAX, host.CODEX_INLINE_MAX - 1000)
            payload = host.read_payload()
            if not isinstance(payload, dict) or not payload:
                return 0
            event = str(payload.get("hook_event_name") or payload.get("event") or "UserPromptSubmit")
            host.TOOL_HINT = (json.dumps(payload.get("tool_input") or {}, ensure_ascii=True)[:800]
                              if event == "PreToolUse" else "")
            owner_context_delivery.emit(BrainEngine(host, brain if brain in BRAINS else None), payload, runtime,
                                        sys.stdout, budget=host.CODEX_INLINE_MAX - 1000 if runtime == "codex"
                                        else host.HARNESS_INLINE_MAX - 500)
        except Exception as e:
            try:
                host._log_error("owner_prompts_brains.hook", e)
            except Exception:
                pass
        return 0
    import owner_prompts_hook as host
    if "--selftest" in argv:
        res = controls(host)
        for name, ok in res:
            print(("PASS " if ok else "FAIL ") + name)
        bad = [n for n, ok in res if not ok]
        print("owner_prompts_brains selftest: %d checks, %d failed" % (len(res), len(bad)))
        return 1 if bad else 0
    if "--seed" in argv:
        sys.stdout.write(seed(host, argv[argv.index("--judge-dir") + 1] if "--judge-dir" in argv else None))
        return 0
    if "--verdict" in argv:
        i = argv.index("--verdict")
        pid, v = (argv[i + 1:i + 3] + ["", ""])[:2]
        by = argv[argv.index("--by") + 1] if "--by" in argv and len(argv) > argv.index("--by") + 1 else ""
        v = v.upper()
        if not re.match(r"^[0-9a-f]{16}$", pid) or v not in ("KEEP", "DROP") or not by or by == "rule":
            print("usage: --verdict ID KEEP|DROP --by AGENT [--gist TEXT] [--brains main,movie,math]")
            return 2
        gist = argv[argv.index("--gist") + 1] if "--gist" in argv and len(argv) > argv.index("--gist") + 1 else ""
        brains = [b for b in (argv[argv.index("--brains") + 1].split(",") if "--brains" in argv else []) if b in BRAINS]
        rec = {"id": pid, "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "sess": "", "text": "", "verdict": v,
               "brains": brains, "gist": ascii_text(gist)[:200], "by": by, "why": "agent"}
        append_records([rec])
        print("recorded %s %s by %s" % (pid, v, by))
        return 0
    if "--status" in argv:
        sys.stdout.write(status(host))
        return 0
    if "--rules-report" in argv:
        sys.stdout.write(rules_report(host))
        return 0
    if "--handcheck" in argv:
        i = argv.index("--handcheck")
        sys.stdout.write(handcheck(host, int(argv[i + 1]), int(argv[i + 2]), argv[i + 3]))
        return 0
    if "--measure" in argv:
        sys.stdout.write(measure(host, [a for a in argv[argv.index("--measure") + 1:] if not a.startswith("--")]))
        return 0
    if "--show" in argv:
        i = argv.index("--show")
        want = argv[i + 1] if len(argv) > i + 1 and argv[i + 1] in BRAINS else "all"
        bms = brain_metas(host.load(), None)[0]
        for b in (BRAINS if want == "all" else (want,)):
            sys.stdout.write(brain_block(host, bms[b], b, [], time.time()))
        return 0
    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
