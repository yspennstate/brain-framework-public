# -*- coding: utf-8 -*-
"""Every compaction becomes its own brain node, connected to the nodes it relates to (9 Oct 2026: every compaction is saved as
its own node with edges to the other compactions and to the nodes it relates to, and the method is checked to be
doing that).

Claude Code runs this on two hook events (settings.json "PreCompact" / "PostCompact"):
- PreCompact (input: trigger, custom_instructions): prints instructions that Claude Code hands to the summarizer, so
  the summary keeps what the work needs afterwards (open asks, ids, commits, paths, claims, the NEXT line).
- PostCompact (input: trigger, compact_summary): saves the summary as its own node.

Each compaction is written twice (9 Oct 19:1x):
- the full summary under ~/.claude/brain_graph/compactions/<date>/<name>.md (brain data, never deleted);
- an anchor memory <name>.md in the memory folder: a bounded excerpt (the ask, the pending tasks, the current work, the
  next step), a pointer to the full summary, and [[links]] to the compaction hub, the session's previous compaction,
  other sessions' compactions on the same checklist tasks, and the memories brain.py recall finds closest. The anchor is
  what makes the compaction a node of the brain: bm25 recall indexes the memory folder, and build_spread_edges.py turns
  its [[links]] into graph edges. Anchors get no MEMORY.md line (that index is loaded into every session).
Before 19:1x the nodes lived only under brain_graph, where neither recall nor the graph looks, and one compaction was
lost: the hook read its input in the console code page and a stray surrogate made the UTF-8 write fail.

`--sweep` backfills what a hook missed: compact_boundary records in Claude transcripts (the summary is the next user
record) and 'compacted' records in every Codex home's rollouts (~/.codex*/sessions/YYYY/MM/DD). Codex has no compaction
hook and the pool's workers run hookless, so the sweep is how their compactions become nodes. A byte cursor per file
(sweep_state.json) keeps repeat sweeps cheap; a compaction already saved is never saved twice (same text, or the same
session within ten minutes). compact_hooks.py starts the sweep in the background at most every 15 minutes.

A subagent's compaction reaches the hook with its parent's session and no agent_id (Claude Code 2.1.296), and the
agent's own compact record can land in its transcript after PostCompact fires; so the sweep also runs attribute(), which
finds each unattributed row's summary in the session's agent transcripts, files the row under that agent and re-chains
`previous` within each identity (12 Oct). `--attribute [--days N] [--dry]` runs that pass alone; `--refresh-gists
[--dry]` rewrites the anchors whose description ended in a bare <summary> tag (the gist before 12 Oct).

The compactions tree is brain data under the 26 Sep law: never deleted. A hook must never break a compaction, so every
error is swallowed and logged to compactions/errors.log. COMPACT_NODES_ROOT overrides the store and COMPACT_ANCHOR_DIR
the anchor folder (tests); COMPACT_NO_RECALL=1 skips the recall call."""
import sys
if __name__ == "__main__" and ("-h" in sys.argv[1:] or "--help" in sys.argv[1:]):
    print(__doc__)
    sys.exit(0)
import datetime as _dt
import glob
import hashlib
import io
import json
import math
import os
import re
import subprocess
import sys
import time

HOME = os.environ.get("COMPACT_HOME") or os.path.expanduser("~")
ROOT = os.environ.get("COMPACT_NODES_ROOT") or os.path.join(HOME, ".claude", "brain_graph", "compactions")
MEM = os.environ.get("COMPACT_ANCHOR_DIR") or (os.path.join(ROOT, "anchors") if os.environ.get("COMPACT_NODES_ROOT")
                                                else os.path.join(HOME, ".claude", "projects", "{{BRAIN_SLUG}}", "memory"))
INDEX = os.path.join(ROOT, "INDEX.jsonl")
STATE = os.path.join(ROOT, "sweep_state.json")
HUB = "compaction_hub_how_we_compact_and_where_it_is_kept_2026_10_09"
CHECKLIST = os.path.join(HOME, "brain-repo", "12_cognitive_architecture", "task_checklist", "state.json")
BRAIN = os.path.join(HOME, ".claude", "dreaming", "brain.py")
KEEP = ("Compacting: keep in the summary, word for word where they appear: the owner's open asks with their times and "
        "task ids (T-...); every id, commit hash, sha, file path and card or item id the work depends on; what is done and "
        "verified versus still open; the NEXT line if one was written; mesh claims held and peer messages owed an answer; "
        "the agent's own id. Drop finished subjects' tool output and anything already saved to a file, a memory or a "
        "commit, and name where it was saved instead. Never copy test questions, answer keys or item text into the summary. "
        "Owner restrictions keep their scope words (for the night, until he says, this session).")
# test material never reaches a memory file (a memory can be pushed; test items, keys and questions stay on this computer):
# a summary that mentions the test bank keeps its full text in the local node and gives its anchor no excerpt
TEST_RX = re.compile(r"test_v\d|[/\\]items[/\\]|keys?_quarantine|answer key|key\.quote|blind_solves|plit_keys|bank_\w+_keys",
                     re.I)
SECTION_RX = re.compile(r"^\s*(\d{1,2})\.\s+([A-Z][A-Za-z ,/&'-]{2,60}):?\s*$", re.M)
WANT_SECTIONS = ("primary request", "pending tasks", "current work", "optional next step", "next step")
EXCERPT_CHARS = 3600
AGENT_KEYS = ("agent_id", "agent_type", "agent_description", "agent_transcript")
# Compaction anchors are linked through the session and task rules, never as 'a related memory' (the same pattern as
# spread_recall.SESSION_RECORD, 13 Oct c0f75bd4).
SESSION_RECORD_RX = re.compile(r"^(?:manual_)?compaction_(?:claude|codex)_")
# What every summary carries and no memory is about: agent and session ids, claim ids, task ids, hashes.
_QUERY_NOISE = re.compile(r"\b(?:claude|codex)-[0-9a-f]{8}\b|\bclm-[0-9a-f]{6,}\b|\bT-\d{8}-\d{3}\b"
                          r"|\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b|\b(?=[0-9a-f]*\d)[0-9a-f]{7,64}\b",
                          re.I)
RELINK_LOG = INDEX + ".relinked"
# The query's own vocabulary (14 Oct): the words found in over a fifth of the node bodies and the quotes found in ten or
# more of them say nothing about one session. build_query_common() writes them here; the sweep rebuilds it once a day.
QUERY_COMMON = os.path.join(ROOT, "query_common.json")
# recall ranks the compaction records high (85 of the top 100 rows for a film session's summary, 14 Oct) and they are
# left out, so the related memories are read from a deep list
RECALL_DEPTH = int(os.environ.get("COMPACT_RECALL_DEPTH") or 150)
# The relevance gate (14 Oct): recall's own rank and score do not say whether a memory is about the session (on 85
# blind-judged picks they did not separate the on-topic ones from the rest), so a related memory must also share the
# summary's rare words: the share of the memory's distinctive words (held by at most GATE_CUT of the memories), weighted
# by rarity, that the summary holds must exceed GATE_MIN. MEM_DF holds the memory folder's word counts; the sweep
# rebuilds it once a day. Every link the gate took out of an anchor is in REGATE_LOG.
# lex2 (14 Oct 14:3x): the gate reads the words of the whole link source, not the excerpt, and needs 0.08. Under lex1
# (excerpt words, 0.03) 23 anchors kept no link: an excerpt of 36-132 words shares too few rare words with any memory.
# On 33 nodes judged blind (23 of those, 10 linked controls; 220 picks, judgments_1410.tsv in
# crash_recovery_20261014/work_aeda7a9b): controls 27 on topic, 8 weak, 2 off against lex1's 15, 9, 6; the 23 empty
# ones 19 linked, 22 on, 11 weak, 10 off. Below 0.08 the whole-text score stops separating (0.05-0.08: 13 on, 28 off).
MEM_DF = os.path.join(ROOT, "mem_df.json")
REGATE_LOG = INDEX + ".regated"
GATE_VERSION = "lex2"
GATE_MIN = float(os.environ.get("COMPACT_GATE_MIN") or 0.08)
GATE_CUT = 0.02
GATE_DEPTH = 15   # the first 15 memories of each recall list are scored
RELINK_FILL = 5   # relink fills an anchor regate thinned back to this many related links
_GATE_WRX = re.compile(r"[a-z][a-z0-9]{3,}")
_RELATED_LINE = re.compile(r"^- \[\[([^\]\n]+)\]\] \(a related memory\)\s*$")
_WORD_RX = re.compile(r"[A-Za-z][A-Za-z0-9_.'-]{2,}")
_PATH_RX = re.compile(r"(?:[A-Za-z]:)?(?:[\\/][\w.\-~$@]+){2,}[\\/]?|~[\\/][\w.\-\\/]+")
_QUOTE_RX = re.compile(r'"([^"\n]{20,600})"|\u201c([^\u201d\n]{20,600})\u201d')


def _now():
    return _dt.datetime.now().astimezone()


def _clean(s):
    """Text that can always be written as UTF-8: a lone surrogate becomes U+FFFD (the 18:16 node was lost to one)."""
    return (s or "").encode("utf-8", "surrogatepass").decode("utf-8", "replace")


def _append(path, row):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _index_rows():
    rows = []
    try:
        with io.open(INDEX, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    pass
    except OSError:
        pass
    # Manual checkpoints use a separate ledger so a live historical backfill
    # never competes with their writer. They participate in the same graph.
    try:
        with io.open(os.path.join(ROOT, "MANUAL_INDEX.jsonl"), encoding="utf-8") as fh:
            for line in fh:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    pass
    except OSError:
        pass
    return rows


def _open_tasks(session_id, runtime="claude", extra_agent=None):
    """Checklist entries still open under the native runtime's session identity."""
    agents = {runtime + "-" + (session_id or "")[:8]}
    if extra_agent:
        agents.add(extra_agent)
    try:
        with io.open(CHECKLIST, encoding="utf-8") as fh:
            st = json.load(fh)
    except (OSError, ValueError):
        return []
    items = st.get("entries") if isinstance(st, dict) else st
    vals = items.values() if isinstance(items, dict) else items if isinstance(items, list) else []
    out = []
    for t in vals:
        if isinstance(t, dict) and t.get("agent") in agents and str(t.get("status", "")).lower() not in ("done", "dropped"):
            out.append(t.get("id"))
    return list(dict.fromkeys(x for x in out if x))[-12:]


def _depath(text):
    """A path becomes its file name (when it has an extension) or nothing. A Windows path is a run of words - Users,
    owner, AppData, Local, Temp, claude - that every summary shares, and it led the query of every node that named its
    scratchpad."""
    def keep(m):
        base = re.split(r"[\\/]", m.group(0).rstrip("\\/"))[-1]
        return " %s " % base if "." in base.strip(".") else " "
    return _PATH_RX.sub(keep, text.replace("`", " "))


def _quotes(text):
    """The quoted passages of 20 to 600 characters: his words, the error a session chased, the order it was given."""
    return [next(g for g in m.groups() if g) for m in _QUOTE_RX.finditer(text)]


def _quote_key(quote):
    return hashlib.sha1(" ".join(quote.lower().split()).encode("utf-8")).hexdigest()[:16]


def _node_body(path):
    """A node file's summary: the text after its frontmatter, without the Connected: list."""
    with io.open(path, encoding="utf-8", errors="replace") as stream:
        return stream.read().split("\n---\n", 1)[-1].partition("\n\nConnected:\n")[0]


def build_query_common(share=0.20, quote_min=10, min_nodes=50):
    """Write QUERY_COMMON: the words found in over `share` of the node bodies, and the keys of the quotes found in at
    least `quote_min` of them (the harness reminders, commit trailers, the worker contract, his standing rules that
    every summary quotes). Returns the number of nodes read, or 0 (nothing written) when the store is too small to say
    what is common. About 18 s on 2,296 nodes (14 Oct)."""
    from collections import Counter
    df, qf, n = Counter(), Counter(), 0
    for r in _index_rows():
        if not r.get("node") or not os.path.exists(r["node"]):
            continue
        try:
            body = _node_body(r["node"])
        except OSError:
            continue
        n += 1
        df.update(set(w.lower() for w in _WORD_RX.findall(_depath(body))))
        qf.update(set(_quote_key(q) for q in _quotes(body)))
    if n < min_nodes:
        return 0
    data = {"at": _now().isoformat(timespec="seconds"), "nodes": n, "share": share, "quote_min": quote_min,
            "words": sorted(w for w, c in df.items() if c > share * n),
            "quotes": sorted(k for k, c in qf.items() if c >= quote_min)}
    tmp = QUERY_COMMON + ".tmp.%d" % os.getpid()
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh)
    os.replace(tmp, QUERY_COMMON)
    return n


_QC = {"key": None, "words": frozenset(), "quotes": frozenset()}


def _query_common():
    """(common words, common quote keys) from QUERY_COMMON, read again only when the file changes; empty sets when it
    is missing or unreadable, so a new store still gets a query."""
    try:
        st = os.stat(QUERY_COMMON)
        key = (st.st_mtime_ns, st.st_size)
    except OSError:
        return frozenset(), frozenset()
    if _QC["key"] != key:
        try:
            with io.open(QUERY_COMMON, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return frozenset(), frozenset()
        _QC.update(key=key, words=frozenset(data.get("words") or ()), quotes=frozenset(data.get("quotes") or ()))
    return _QC["words"], _QC["quotes"]


def _query_common_stale(max_age=86400):
    try:
        return time.time() - os.path.getmtime(QUERY_COMMON) > max_age
    except OSError:
        return True


def build_mem_df(min_memories=500):
    """Write MEM_DF: for each word of four or more letters, the number of memory files that hold it, over the memory
    folder without the compaction anchors (they would count every session's own words). Returns the number of memories
    read, or 0 (nothing written) when there are too few to say which words are rare. About 12 s on 20,094 memories."""
    df, n = {}, 0
    try:
        names = os.listdir(MEM)
    except OSError:
        return 0
    for fn in names:
        if not fn.endswith(".md") or SESSION_RECORD_RX.match(fn):
            continue
        try:
            with io.open(os.path.join(MEM, fn), encoding="utf-8", errors="replace") as fh:
                words = set(_GATE_WRX.findall(fh.read().lower()))
        except OSError:
            continue
        n += 1
        for w in words:
            df[w] = df.get(w, 0) + 1
    if n < min_memories:
        return 0
    tmp = MEM_DF + ".tmp.%d" % os.getpid()
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump({"at": _now().isoformat(timespec="seconds"), "memories": n, "df": df}, fh, separators=(",", ":"))
    os.replace(tmp, MEM_DF)
    return n


_MDF = {"key": None, "n": 0, "df": None}


def _mem_df():
    """(memories, word counts) from MEM_DF, read again only when the file changes; (0, None) when it is missing or
    unreadable, and then no related memory is chosen (the gate cannot judge one)."""
    try:
        st = os.stat(MEM_DF)
        key = (st.st_mtime_ns, st.st_size)
    except OSError:
        return 0, None
    if _MDF["key"] != key:
        try:
            with io.open(MEM_DF, encoding="utf-8") as fh:
                data = json.load(fh)
            n, df = int(data["memories"]), data["df"]
        except (OSError, ValueError, KeyError, TypeError):
            return 0, None
        if n <= 0 or not isinstance(df, dict):
            return 0, None
        _MDF.update(key=key, n=n, df=df)
    return _MDF["n"], _MDF["df"]


def _mem_df_stale(max_age=86400):
    try:
        return time.time() - os.path.getmtime(MEM_DF) > max_age
    except OSError:
        return True


def _gate_words(text):
    """The summary's words the gate compares against: its whole link source (_link_source; _link_text when the
    plumbing was nearly all the summary held) without paths, ids and the words common to the nodes."""
    common = _query_common()[0]
    src = _link_source(text)
    if len(_WORD_RX.findall(src)) < 40:
        src = _link_text(text)
    return set(w for w in _GATE_WRX.findall(_QUERY_NOISE.sub(" ", _depath(src)).lower()) if w not in common)


def _gate_score(slug, words, n, df):
    """The rarity-weighted share of the memory's distinctive words that `words` holds; None when the memory cannot be
    read. 14 Oct, 12 nodes judged blind (not the ones the gate was set on): above 0.03, 43 of 52 picks on topic and 6
    off, against about 33 of 60 and 10 to 17 off for recall's top 5 alone; every node kept an on-topic link."""
    try:
        with io.open(os.path.join(MEM, slug + ".md"), encoding="utf-8", errors="replace") as fh:
            mine = set(_GATE_WRX.findall(fh.read().lower()))
    except OSError:
        return None
    cut, tot, shared = GATE_CUT * n, 0.0, 0.0
    for w in mine:
        c = df.get(w, 0)
        if c <= cut:
            v = math.log((n + 1.0) / (c + 1.0))
            tot += v
            if w in words:
                shared += v
    return shared / tot if tot else 0.0


def _related_query(text, words=80):
    """The recall query for a summary: its quoted passages first (his words, the error chased, the order given), then
    the words of its ask, pending tasks, current work and next step (what _excerpt keeps, quotes taken out). Paths are
    reduced to file names; the preface, the agent-id line, ids, the quotes found in ten or more nodes and the words
    found in over a fifth of them (QUERY_COMMON) are left out.

    Until 14 Oct 03:22 the query was the summary's first 60 words - the preface, the agent id and the mesh claims every
    summary opens with - so 377 of the 777 nodes written 13-14 Oct got no related memory and many of the rest an
    unrelated one (his_standing_film_orders_2026_09_23 on 158 of them). The 03:22 query still led with the scratchpad
    path and the environment lines a custom summary opens with: 66 of the 227 nodes saved after it got nothing, and for
    8 recent such nodes every one of recall's top 25 rows was a compaction record. On 12 real nodes (14 Oct 07:00)
    quotes first, then the excerpt's words, brought on-topic memories for most of them.

    Since 14 Oct (LINK_VERSION x1) both the quotes and the excerpt come from _link_source / _link_text, so a quoted
    keeper prompt or contract inside a who-I-am block no longer leads the query."""
    common, common_quotes = _query_common()
    found = _quotes(_link_source(text))
    quoted = [q for q in found if _quote_key(q) not in common_quotes and not TEST_RX.search(q) and not _ID_LINE.search(q)]
    body = "\n".join(l for l in _link_text(text).splitlines()
                     if not _PREFACE.match(l.strip()) and not _ID_LINE.search(l) and not SECTION_RX.match(l)
                     and not TEST_RX.search(l))
    for q in found:   # a kept quote is already at the front, and a common one stays out
        body = body.replace(q, " ")
    out = []
    for chunk in (" ".join(quoted), body):
        out += [w for w in _WORD_RX.findall(_QUERY_NOISE.sub(" ", _depath(chunk))) if w.lower() not in common]
    return " ".join(out[:words])


def _related_query_words(text, words=80):
    """The first recall query: the excerpt's words in their own order (quotes stay where they stand), with the same
    lines, paths, ids, common quotes and common words left out as in _related_query. With the gate it brought the most
    on-topic memories of the queries tried on 14 Oct; _related_query (quotes first) fills what it leaves."""
    common, common_quotes = _query_common()
    body = "\n".join(l for l in _link_text(text).splitlines()
                     if not _PREFACE.match(l.strip()) and not _ID_LINE.search(l) and not SECTION_RX.match(l)
                     and not TEST_RX.search(l))
    for q in _quotes(_link_source(text)):
        if _quote_key(q) in common_quotes:
            body = body.replace(q, " ")
    return " ".join([w for w in _WORD_RX.findall(_QUERY_NOISE.sub(" ", _depath(body))) if w.lower() not in common][:words])


def _related_memories(text, k=5, timeout=25):
    """The brain memories most related to the summary, by the brain's own recall (best effort, bounded in time)."""
    return _recall_related(text, k, timeout) or []


def _recall_slugs(query, timeout):
    """The memories of recall's answer to `query` in its order, without the compaction records and the hub (recall ranks
    the records high, so RECALL_DEPTH rows are asked for); None when recall could not answer."""
    try:
        r = subprocess.run([sys.executable, "-s", BRAIN, "recall", query, "-n", str(RECALL_DEPTH)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                           env=dict(os.environ, BRAIN_RECALL_REFRESH="0"),   # a linker never rebuilds the index
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))  # never a console window
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    out = []
    for line in r.stdout.splitlines():
        m = re.match(r"^\s*\d+\.\d+\s+\[([^\]]*)\]\s+([A-Za-z0-9_.-]+)\s*$", line)  # "  1.00 [lex+graph+sem] slug"
        if not m:
            continue
        slug = m.group(2)
        # another compaction anchor is linked through the session and task rules, not as a 'related memory'; a dream
        # log (dream_2026_07_14_nap_afternoon) is the night's journal, not a memory about the node's work
        if slug == HUB or SESSION_RECORD_RX.match(slug) or _DREAM_LOG_RX.match(slug) or slug in out:
            continue
        out.append(slug)
    return out


def _recall_related(text, k=5, timeout=25):
    """As _related_memories, but None when it could not judge (recall timed out or failed, no word table, no candidate
    memory readable) and [] when nothing passed, so relink tries the first kind again. The first GATE_DEPTH memories of
    recall's answer to _related_query_words are scored by the gate in recall's order; when fewer than k pass, the
    answer to _related_query fills the rest the same way. Until 14 Oct the top k of one recall were taken as they came:
    on 12 blind-judged nodes about a third of those were off topic."""
    n, df = _mem_df()
    if df is None:
        return None   # the sweep builds the word table; relink links this node after it
    if not os.path.exists(BRAIN) or os.environ.get("COMPACT_NO_RECALL"):
        return None   # not asked: a later pass with recall may still link it
    words = _gate_words(text)
    picked, seen, read = [], 0, 0
    for make in (_related_query_words, _related_query):
        query = make(text)
        if not query:
            continue
        slugs = _recall_slugs(query, timeout)
        if slugs is None:
            return picked or None
        for slug in slugs[:GATE_DEPTH]:
            if slug in picked:
                continue
            seen += 1
            score = _gate_score(slug, words, n, df)
            if score is None:
                continue
            read += 1
            if score > GATE_MIN:
                picked.append(slug)
                if len(picked) >= k:
                    return picked
    if seen and not read:
        return None   # recall named memories and none could be read: a broken path, not an answer
    return picked


def _excerpt(summary):
    """The parts a later reader needs first: the ask, what is pending, the current work, the next step."""
    marks = [(m.start(), m.group(2).strip().lower()) for m in SECTION_RX.finditer(summary)]
    parts = []
    for i, (start, title) in enumerate(marks):
        if any(title.startswith(w) for w in WANT_SECTIONS):
            end = marks[i + 1][0] if i + 1 < len(marks) else len(summary)
            parts.append(summary[start:end].strip()[:1300])
    text = "\n\n".join(parts) if parts else summary.strip()
    return text[:EXCERPT_CHARS] + (" [...]" if len(text) > EXCERPT_CHARS else "")


# Lines a gist must skip. A custom-format summary (the KEEP note above lets a session lay out its own) opens with the bare
# <summary> or <analysis> tag, a heading, or "I am claude-xxxxxxxx, session ..." - and the gist used to be that first line:
# on 12 Oct 138 of 862 anchors were described by the literal "<summary>" (memory
# stream_compactions_filed_under_the_coordinator_2026_10_12).
_TAG_LINE = re.compile(r"^\s*</?[A-Za-z][\w-]*>\s*$")
_HEAD_LINE = re.compile(r"^\s*(?:#{1,6}\s+\S.*|\*\*[^*\n]{1,90}\*\*:?)\s*$")
_ID_LINE = re.compile(r"(?:\bI am\b|\bI'm\b|\bMe:|\bAgent\b:?|\bagent id\b|\bmesh id\b).{0,90}?"
                      r"(?:\b(?:claude|codex)-[0-9a-f]{8}\b|\bsession\W{0,4}[0-9a-f]{8}-)", re.I)
_PREFACE = re.compile(r"^(?:This session is being continued|The summary below covers|Summary:?$)", re.I)
_ASK_LINE = re.compile(r"^(?:#{1,6}\s*)?(?:\d{1,2}[.)]\s*)?[*_]*Primary Request and Intent[*_]*[:.]?[*_]*\s*(.*)$", re.I)
_SECTION_TITLES = frozenset(("primary request and intent", "key technical concepts", "files and code sections",
                             "errors and fixes", "problem solving", "all user messages", "pending tasks", "current work",
                             "optional next step", "summary"))

# The text the linker reads (14 Oct, LINK_VERSION x1). A summary opens its ask with session plumbing - who the agent
# is, the clock, the crash, the keeper and its resume prompt, the contract, the mesh claims, the standing rules - and
# the excerpt kept it, so the related-memory queries and the gate's word set were about the plumbing. Of the 958 nodes
# saved on 14 Oct, 55 got no related memory (the gate rightly refused the crash-recovery and loop memories recall
# offered) and others got crash-recovery memories for film or rho work. Summaries that open with an <analysis> block,
# or number their headings '## 1. Primary Request', also fell back to their first 3600 characters. On six of those
# empty nodes and three linked controls (cf_excerpt_1014), reading the summary without the plumbing gave all six
# links, about 19 of 22 on topic, and cost the controls nothing (memory
# compaction_link_text_drops_session_plumbing_2026_10_14).
LINK_VERSION = "x1"
# A heading names plumbing by a phrase only plumbing uses ...
_PLUMB_STRONG = re.compile(r"who i am|\bwho we are\b|subagent.?rules|reporting rules|how this run works|"
                           r"\bstanding\s+(?:[\w-]+\s+)?(?:constraints?|orders?|rules?|restrictions?|instructions?|asks?|"
                           r"bans?|laws?|security)\b|\bconstraints\b|\bcontract\W*$|\benvironment\b|^identity\b|"
                           r"\bwho and (?:where|when)\b", re.I)
# ... or by a word work can use too (a Hindenburg crash, a data recovery, a security review, a mesh in a proof, the
# picture keeper), which counts only near the front of the heading - 'the keeper' only as the keeper, not a qualified
# 'picture keeper' - and when the block's first lines carry a session marker (a mesh id, a claim id, a subagent, a
# restart, the local time, the lease, the crash-recovery folder).
_PLUMB_WEAK = re.compile(r"\bclocks?\b|\bcrash|\brecover|\bsecurity\b|\bmesh\b|\bresum(?:e|ed|es|ing)\b", re.I)
_PLUMB_KEEPER = re.compile(r"\bkeeper(?:'s)?\s+(?:\d{1,2}:\d{2}\s+)?(?:instructions?|prompts?|resume|messages?|orders?)\b",
                           re.I)   # the crash keeper's prompt; a 'Keeper deadlock' or the picture keeper is work
_TASK_ID_RX = re.compile(r"\bT-\d{8}-\d{3}\b")   # a heading that names a checklist task is the work
_MIXED_ASK_RX = re.compile(r"\b(?:the|his|my|open)\s+asks?\b", re.I)   # 'The ask, and who I am' holds the ask
_QUOTE_ID_RX = re.compile(r"\b(?:claude|codex)-[0-9a-f]{8}\b|\bmesh id\b", re.I)
_FENCE_RX = re.compile(r"^\s*(?:```|~~~)")
_PLUMB_MARK = re.compile(r"\b(?:claude|codex)-[0-9a-f]{8}\b|\bclm-[0-9a-f]{6,}|\bsubagent|\brestart|\blocal time\b|"
                         r"\bUTC\b|\bpid \d+|\bagent_mesh\b|\blease\b|\bcrash_recovery_\d{8}", re.I)
_PAREN_RX = re.compile(r"\([^()]*\)")
_ENUM_TOKEN = re.compile(r"^(?:\d{1,2}|[A-Za-z])[.):]$")
_NUM_HEAD_RX = re.compile(r"^(?:#{1,6}\s+|\*\*)(\d{1,2})\.\s+([A-Z][A-Za-z ,/&'-]{2,60}?)(:?)(?:\*\*)?:?\s*$", re.M)
_NUM_BOLD_RX = re.compile(r"^[ \t]*(\d{1,2})\.\s+\*\*([A-Z][A-Za-z ,/&'-]{2,60}?)(:?)\*\*:?[ \t]*$", re.M)  # 1. **Title**
_SUB_HEAD_RX = re.compile(r"^\s*(?:#{1,6}\s+(.+?)|\*\*([^*\n]{1,90})\*\*:?|\*([^*\n]{1,90})\*:?)\s*$")
_LEAD_BULLET_RX = re.compile(r"^([ \t]*)[-*+]\s+\*\*([^*\n]{2,90}?)\*\*[:.]?\s*(.*)$")   # - **Lead:** text
_DREAM_LOG_RX = re.compile(r"^dream_\d{4}_")


def _num_head(m):
    """'## 1. Primary Request and Intent' or '**1. Primary Request and Intent:**' as SECTION_RX reads it, for the
    standard section titles only (a bold '**1. Fix the gate**' inside Pending Tasks stays a list item)."""
    if m.group(2).strip().lower() in _SECTION_TITLES:
        return "%s. %s%s" % (m.group(1), m.group(2).strip(), m.group(3))
    return m.group(0)


def _is_plumbing(title, head):
    """A block heading names session plumbing, read without its parentheses ('NEXT (06:20 his clock)' is work): a
    phrase only plumbing uses, or a word work can use too among the heading's first two words (or in a heading of at
    most four) with a session marker in the block's first lines. A heading that names a checklist task (his standing
    order T-...) is never plumbing, and a path counts by its file name ('.../crash_recovery_20261014/work_x/NEXT.md')."""
    if _TASK_ID_RX.search(title) or _MIXED_ASK_RX.search(title):
        return False
    bare = re.sub(r"\s+", " ", _depath(title))
    core = re.sub(r"\s+", " ", _PAREN_RX.sub(" ", bare)).strip(" :.-*#") or bare.strip(" :.-*#")
    if _PLUMB_STRONG.search(core):
        return True
    toks = [t for t in core.split() if not _ENUM_TOKEN.match(t)]
    lead = " ".join(toks if len(toks) <= 4 else toks[:2])
    if not (_PLUMB_WEAK.search(lead) or _PLUMB_KEEPER.search(" ".join(toks))):
        return False
    return _PLUMB_MARK.search("\n".join(head)) is not None


def _drop_plumbing(lines, head_rx, stop_rx=None):
    """`lines` without the blocks whose heading names plumbing; a block runs to the next heading of either kind (a
    '# comment' inside a fenced code block is not a heading). A list item led by a bold plumbing title ('- **Who I am
    here:** terminal ...', '- **Resume order (10:00).** ...') goes too, with the deeper-indented lines under it."""
    out, skipping, code, item = [], False, False, None
    for i, line in enumerate(lines):
        if _FENCE_RX.match(line):
            code = not code
        if item is not None:
            if not line.strip() or len(line) - len(line.lstrip(" \t")) > item:
                continue
            item = None
        m = None if code else head_rx.match(line)
        if m:
            skipping = _is_plumbing(next((g for g in m.groups() if g), ""), lines[i + 1:i + 5])
        elif stop_rx is not None and not code and stop_rx.match(line):
            skipping = False
        elif not skipping and not code:
            b = _LEAD_BULLET_RX.match(line)
            if b and _is_plumbing(b.group(2), [b.group(3)] + lines[i + 1:i + 4]):
                item = len(b.group(1))
                continue
        if not skipping:
            out.append(line)
    return out


def _num_heads(text):
    """'## 1. Title', '**1. Title:**' and '1. **Title**' section headings as SECTION_RX reads them (standard titles)."""
    return _NUM_BOLD_RX.sub(_num_head, _NUM_HEAD_RX.sub(_num_head, text))


def _link_source(summary):
    """The summary as the linker reads it: the <summary> block only, '## 1. Title' and '**1. Title:**' section
    headings written the way SECTION_RX reads them, without the blocks whose heading names session plumbing and
    without agent-id lines."""
    text = _num_heads(_summary_part(summary))
    out = []
    for line in _drop_plumbing(text.split("\n"), _SUB_HEAD_RX, stop_rx=SECTION_RX):
        if _ID_LINE.search(line):
            # an agent-id line keeps only the quoted passages that name no agent: 'I am claude-1234abcd. His words:
            # "..."' keeps his words; a launcher prompt quoted with its mesh id ('"You are helperB of claude-...')
            # goes with the line
            line = " ".join(m.group(0) for m in _QUOTE_RX.finditer(line) if not _QUOTE_ID_RX.search(m.group(0)))
            if not line:
                continue
        out.append(line)
    return "\n".join(out)


def _link_text(summary):
    """The excerpt the related-memory queries and the gate read: _excerpt of _link_source, or the plain excerpt when
    the plumbing was nearly all the summary held (under 40 words left). The anchor still shows _excerpt."""
    text = _excerpt(_link_source(summary))
    if len(_WORD_RX.findall(text)) < 40:
        return _excerpt(summary)
    return text


def _core(line):
    """A line without its heading marks, list bullet and emphasis."""
    c = re.sub(r"^\s*(?:#{1,6}\s*)?(?:(?:[-*+]|\d{1,2}[.)])\s+)?", "", line)
    return re.sub(r"[*_`]", "", c).strip()


def _is_heading(line):
    c = _core(line)
    if not c or _HEAD_LINE.match(line) or c.rstrip(":").strip().lower() in _SECTION_TITLES:
        return True
    # "Where things stand:", "WHAT I WAS TOLD TO DO", "Who I am and where things are" - a short title, not a sentence
    if len(c) <= 70 and (c.endswith(":") or (c.upper() == c and re.search(r"[A-Z]", c) is not None)):
        return True
    if len(c.split()) <= 7 and not re.search(r"[.!?;)\d]$", c) and not re.search(r"\d", c):
        return True
    # "Transcript: C:\Users\...", "Mesh CLI: C:/Users/..." - a labelled path is session plumbing, not what it was doing
    if re.match(r"^\$?[A-Za-z][\w ./-]{0,30}\s*[:=]\s*(?:[A-Za-z]:[\\/]|/[a-z]/|~[\\/]|\$\w)", c):
        return True
    return re.match(r"^\W*(?:session|agent)\W{0,4}(?:claude-|codex-)?[0-9a-f]{8}", c, re.I) is not None


def _substance(line):
    return bool(line) and not _TAG_LINE.match(line) and not _is_heading(line) and not _ID_LINE.search(line) \
        and not _PREFACE.match(line)


def _summary_part(summary):
    """The <summary> block when the text carries one (some put the model's <analysis> scratch first); else all of it."""
    m = re.search(r"<summary>\s*(.*?)\s*(?:</summary>|\Z)", summary, re.S)
    return m.group(1) if m and m.group(1).strip() else summary


def _gist(summary):
    """One line that tells a compaction from its neighbours: the ask when the summary names it ("Primary Request and
    Intent", any markdown), else its first line of substance - never a bare tag, a heading, the transcript's preface or
    an identity line."""
    lines = [l.strip() for l in _summary_part(summary).splitlines() if l.strip() and not _TAG_LINE.match(l)]
    pick = ""
    for i, line in enumerate(lines):
        m = _ASK_LINE.match(line)
        if m:
            rest = m.group(1).strip()
            pick = next((c for c in ([rest] if len(rest) >= 20 else []) + lines[i + 1:i + 8] if _substance(c)), "")
            break
    if not pick:
        pick = next((l for l in lines if _substance(l)), "")
    if not pick:
        pick = next(iter(lines), "")
    g = re.sub(r"^(?:[-*+]|\d{1,2}[.)])\s+", "", pick)
    g = re.sub(r"\s+", " ", re.sub(r"[*_`\"\[\]<>]", "", g)).strip()
    return g[:150] or "summary text not parsed"


_AGENT_ID_RX = re.compile(r"^[A-Za-z0-9_-]{4,64}$")


def agent_identity(agent_id, agent_type="", session_id="", transcript_path=""):
    """Who compacted, when the hook fired inside a subagent (a workflow stream agent, an Agent-tool worker).

    A subagent's compaction reaches this hook with its PARENT's session_id and transcript_path. The hook-input schema
    has an agent_id field, but Claude Code 2.1.296 builds the compaction payload without the agent context, so
    PreCompact/PostCompact arrive with no agent_id (measured 12 Oct 15:18: recovery agents s03 and s04 compacted and
    both payloads lacked it). This function uses agent_id when a payload carries one; agent_from_transcripts() finds
    the agent when it does not. Before 12 Oct this file did neither, so twenty FEI stream agents' compactions were filed
    under their coordinator's session (2d8060eb). The agent's description and transcript sit beside the parent's
    transcript, in <session>/subagents/**/agent-<id>.meta.json. Returns {} for the main thread."""
    agent_id = str(agent_id or "").strip()
    if agent_id.startswith("agent-"):
        agent_id = agent_id[len("agent-"):]
    if not agent_id or not _AGENT_ID_RX.match(agent_id):
        return {}
    out = {"agent_id": agent_id, "agent_type": re.sub(r"\s+", " ", str(agent_type or "")).strip()[:80]}
    try:
        base = os.path.join(os.path.dirname(transcript_path), session_id) if transcript_path and session_id else ""
        if base and os.path.isdir(os.path.join(base, "subagents")):
            hits = glob.glob(os.path.join(base, "subagents", "**", "agent-%s.meta.json" % agent_id), recursive=True)
            if hits:
                with io.open(hits[0], encoding="utf-8", errors="replace") as fh:
                    meta = json.load(fh)
                desc = re.sub(r"\s+", " ", _clean(str(meta.get("description") or ""))).strip()
                if desc:
                    out["agent_description"] = desc[:120]
                if not out["agent_type"] and meta.get("agentType"):
                    out["agent_type"] = str(meta.get("agentType"))[:80]
                out["agent_transcript"] = (hits[0][:-len(".meta.json")] + ".jsonl").replace(os.sep, "/")
    except (OSError, ValueError, AttributeError):
        pass
    return out


_WS_RX = re.compile(r"\s+")


def _summary_probe_text(summary):
    """The part of a compaction summary a transcript keeps: the <summary> block when there is one (the hook payload can
    also carry the <analysis> block, which the transcript drops), whitespace-normalised."""
    t = summary or ""
    a, b = t.find("<summary>"), t.rfind("</summary>")
    if a >= 0:
        t = t[a + len("<summary>"): b if b > a else len(t)]
    return _WS_RX.sub(" ", t).strip()


def agent_from_transcripts(summary, session_id="", transcript_path="", window_s=600, tail_bytes=512 * 1024,
                           waits=(0.0,)):
    """The subagent whose own transcript holds this compaction's summary, for payloads that carry no agent_id.

    Reads the tail of every agent-*.jsonl under <session>/subagents modified in the last window_s, takes each file's
    newest compact-summary record (isCompactSummary), and keeps the agent whose record contains at least two of three
    probe windows from the summary. Exactly one such agent -> its identity; none or several -> {} (main thread, or
    not decidable). At hook time this finds nothing under Claude Code 2.1.296: the record's timestamp predates the
    hook, but the record is appended only after PostCompact returns (12 Oct 15:33: s06's record, stamped
    12:33:38.930Z, follows attachments stamped 12:33:55.205Z, the second the hook finished; all six stream compactions
    of 15:28-15:39 were missed this way). The late pass the hook starts (_spawn_late_attribution) and the sweep's
    attribute() file those rows; this stays as one cheap pass for a version that writes the record first."""
    if not (summary and session_id and transcript_path):
        return {}
    base = os.path.join(os.path.dirname(transcript_path), session_id, "subagents")
    if not os.path.isdir(base):
        return {}
    s = _summary_probe_text(summary)
    if len(s) < 300:
        return {}
    probes = [s[i:i + 120] for i in (len(s) // 5, len(s) // 2, (4 * len(s)) // 5)]
    for wait in waits:
        if wait:
            time.sleep(wait)
        now, hits = time.time(), set()
        for f in glob.glob(os.path.join(base, "**", "agent-*.jsonl"), recursive=True):
            try:
                st = os.stat(f)
                if now - st.st_mtime > window_s:
                    continue
                with open(f, "rb") as fh:
                    if st.st_size > tail_bytes:
                        fh.seek(st.st_size - tail_bytes)
                    chunk = fh.read()
            except OSError:
                continue
            for line in reversed(chunk.split(b"\n")):
                if b'"isCompactSummary":true' not in line and b'"isCompactSummary": true' not in line:
                    continue
                try:
                    rec = json.loads(line.decode("utf-8", "replace"))
                except ValueError:
                    break
                if not (isinstance(rec, dict) and rec.get("isCompactSummary") is True):
                    continue
                txt = _WS_RX.sub(" ", _text_of((rec.get("message") or {}).get("content")))
                if sum(1 for p in probes if p in txt) >= 2:
                    hits.add(os.path.basename(f)[len("agent-"):-len(".jsonl")])
                break   # only each agent's newest compaction can be this one
        if len(hits) == 1:
            return agent_identity(hits.pop(), "", session_id, transcript_path)
        if len(hits) > 1:
            return {}
    return {}


def _agent_label(runtime, session_id, agent=None):
    """'claude session 2d8060eb', or for a subagent 'claude workflow-subagent B04 ... (agent a19d5a05, session 2d8060eb)'."""
    sid8 = (session_id or "unknown")[:8]
    if not agent or not agent.get("agent_id"):
        return "%s session %s" % (runtime, sid8)
    kind = agent.get("agent_type") or "subagent"
    desc = (" " + agent["agent_description"]) if agent.get("agent_description") else ""
    return "%s %s%s (agent %s, session %s)" % (runtime, kind, desc, agent["agent_id"][:8], sid8)


def manual_privacy_hash(summary):
    """Match private checkpoint content despite transport whitespace changes."""
    return hashlib.sha256(" ".join(_clean(summary).split()).encode("utf-8")).hexdigest()


def matches_manual_checkpoint(summary, rows=None):
    digest = manual_privacy_hash(summary)
    for row in (_index_rows() if rows is None else rows):
        if row.get("capture_kind") != "manual_checkpoint":
            continue
        private = row.get("anchor_privacy_sha256")
        if private is None:
            # Older, local manual records did not carry the whitespace-stable
            # privacy hash. Read only a confined, bounded, hash-verified source.
            root = os.path.normcase(os.path.realpath(ROOT))
            node = os.path.normcase(os.path.realpath(row.get("node", "")))
            if os.path.commonpath([root, node]) != root:
                raise ValueError("manual privacy source is outside compaction root")
            with io.open(node, "rb") as stream:
                raw = stream.read(256 * 1024 + 1)
            if len(raw) > 256 * 1024 or hashlib.sha256(raw).hexdigest() != row.get("sha256"):
                raise ValueError("manual privacy source is unverified")
            private = manual_privacy_hash(raw.decode("utf-8"))
        if not isinstance(private, str) or not re.fullmatch(r"[0-9a-f]{64}", private):
            raise ValueError("invalid manual privacy digest")
        if private == digest:
            return True
    return False


def write_anchor(name, summary, when, runtime, session_id, node_path, links, tasks, metadata_only=False, capture_kind="",
                 agent=None):
    """The compaction's node in the memory folder (see the module note); returns its path."""
    os.makedirs(MEM, exist_ok=True)
    path = os.path.join(MEM, name + ".md")
    sid8 = (session_id or "unknown")[:8]
    boundary_only = capture_kind == "codex_boundary_metadata"
    metadata_only = metadata_only or boundary_only or matches_manual_checkpoint(summary)
    test_adjacent = metadata_only or bool(TEST_RX.search(summary))
    excerpt = ("(Excerpt withheld: this compaction works on test material, which stays on this computer. Read the full summary "
               "locally at the path above.)" if test_adjacent else _excerpt(summary))
    gist = "Local recovery checkpoint; content withheld" if metadata_only else _gist(summary)
    if boundary_only:
        gist = "Native compaction boundary; plaintext summary unavailable"
        excerpt = "(Metadata only: retained messages and opaque model state stay in the native transcript.)"
    who = _agent_label(runtime, session_id, agent)
    desc = "[PROVISIONAL] Compaction of %s at %s: %s" % (who, when.strftime("%Y-%m-%d %H:%M"), gist)
    fm = ["---", "name: %s" % name, "description: \"%s\"" % desc.replace("\"", "'"), "metadata:", "  type: project",
          "  created: %s" % when.strftime("%Y-%m-%d"), "  created_at: %s" % when.isoformat(timespec="seconds"),
          "  status: provisional", "  node_kind: %s" % ("compaction_boundary" if boundary_only else "compaction"),
          "  runtime: %s" % runtime,
          "  session_id: %s" % (session_id or "")]
    if agent and agent.get("agent_id"):
        fm += ["  agent_id: %s" % agent["agent_id"], "  agent_type: %s" % (agent.get("agent_type") or "")]
    fm += ["  full_summary: %s" % node_path.replace(os.sep, "/"),
           "  test_adjacent: %s" % ("true" if test_adjacent else "false"), "---", ""]
    explanation = ("This records an observed native Codex compaction boundary. No plaintext recovery summary was "
                   "captured. The local metadata node records the source hash, byte offset and validated session/task "
                   "links. Resume through the native session or an explicitly authored manual checkpoint." if boundary_only
                   else "This is a compaction: the session's own summary of its work when its context was compacted "
                   "(a machine summary, provisional; check a fact against the files and commits it names before relying "
                   "on it). The excerpt below is the ask, the pending work and the next step; the full summary "
                   "(%d characters) is at %s." % (len(summary), node_path.replace(os.sep, "/")))
    body = ["FIRE WHEN resuming or reviewing the work of %s, or asked what it was doing around %s%s." % (
                who, when.strftime("%Y-%m-%d %H:%M"), (" (tasks %s)" % ", ".join(tasks)) if tasks else ""),
            "",
            explanation,
            "", excerpt, "", "Connected:"] + ["- " + x for x in links]
    tmp = path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(_clean("\n".join(fm + body) + "\n"))
    os.replace(tmp, path)
    return path


def _window_frac(a, b, k=120, step=60):
    """The fraction of a's k-char windows, one every step chars, that occur in b."""
    w = [a[i:i + k] for i in range(0, max(1, len(a) - k + 1), step)]
    return sum(1 for s in w if s in b) / len(w)


def _span_of(s, flat, k=120):
    """flat cut to the stretch s covers: from where s's first k chars occur to where its last k chars end. The
    transcript wraps a summary in a preface and a tail the hook's copy never had; they are not the compaction's text,
    and in a short summary they alone would sink the reverse measure. Either end not found: that side is kept whole."""
    head, tail = s[:k], s[-k:]
    i = flat.find(head)
    a = i if i >= 0 else 0
    j = flat.rfind(tail)
    b = j + len(tail) if j >= 0 and j + len(tail) > a else len(flat)
    return flat[a:b]


def _compaction_overlap(row, flat):
    """How much the node an INDEX row points at and `flat`, a whitespace-normalised transcript text, share: the smaller
    of the fraction of the node summary's 120-char windows found in flat and the fraction of the windows of flat's
    stretch that summary spans (_span_of) found in the summary. 0.0 when the node cannot be read."""
    try:
        s = _summary_probe_text(_node_parts(row["node"])[1])
    except (OSError, KeyError, TypeError):
        return 0.0
    if not (s and flat):
        return 0.0
    return min(_window_frac(s, flat), _window_frac(_span_of(s, flat), s))


def _same_compaction(row, flat):
    """True when the node an INDEX row points at holds the summary in `flat` (_compaction_overlap at least 0.8).
    Measured on the 1,054 hook-saved main-thread rows of the 48 h to 14 Oct 08:47: a node against its own compaction
    scores at least 0.81 (median 1.00; the two below 0.99 hold text their transcript lacks), against the 3,070 other
    compactions of its session within 30 min at most 0.73 (p99 0.53, median 0.15). Headless sessions that compact every
    few minutes keep 20-70 pct of the previous summary, and a two-of-three probe test passes such a pair about half the
    time. A node that cannot be read is not the same compaction: saving the transcript's copy restores what it lost."""
    return _compaction_overlap(row, flat) >= 0.8


def save_node(summary, runtime, session_id, trigger, source_path="", when=None, extra=None, window_s=0,
              native_identity=None, agent=None):
    """Write one compaction summary as its own node and anchor; return the node's path, or None when it is already one."""
    summary = _clean(summary).strip()
    if not summary:
        return None
    sha = hashlib.sha256(summary.encode("utf-8")).hexdigest()
    rows = _index_rows()
    capture_kind = extra.get("capture_kind", "") if isinstance(extra, dict) else ""
    boundary_only = capture_kind == "codex_boundary_metadata"
    metadata_only = boundary_only or matches_manual_checkpoint(summary, rows)
    duplicate = next((r for r in rows if r.get("sha256") == sha
                      and r.get("capture_kind") != "manual_checkpoint"), None)
    if duplicate:
        _stage_math_draft(extra, duplicate.get("name", ""), runtime, session_id, source_path)
        return None
    when = when or _now()
    agent = agent if (agent and agent.get("agent_id")) else None
    agent_id = agent["agent_id"] if agent else ""
    if window_s and session_id:
        # Backfill only: the same compaction the hook saved, whose text the transcript wraps in a preface and a tail.
        # Until 14 Oct any row of the session and agent within window_s counted as this compaction, so a session that
        # compacts every few minutes lost the next one (the 24 h to 14 Oct 07:50: 112 of 1,154 main-thread compactions
        # had no node, all in headless sessions; e7253a64 lost 18 of 71). A row is now this compaction only when the
        # hook saved it (a backfilled one is matched by its sha above) and its summary is this text, measured both ways
        # (_same_compaction). Any agent: the hook files a subagent's compaction under its parent with no agent_id until
        # attribute() finds the agent. The sweep's window is 120 s from the compaction's boundary: a hook row lands 5 s
        # before to 47 s after it (1,067 rows, 48 h to 14 Oct 08:5x). Wider is not safer: two compactions of one
        # session can share most of their text (9643c560 at 07:34:46 and 07:41:10 on 14 Oct share 0.86-0.91).
        flat = _WS_RX.sub(" ", summary)
        for r in rows:
            try:
                t = _dt.datetime.fromisoformat(r.get("at", ""))
            except ValueError:
                continue
            if (r.get("capture_kind") != "manual_checkpoint" and r.get("session_id") == session_id
                    and not r.get("backfilled") and abs((t - when).total_seconds()) <= window_s
                    and _same_compaction(r, flat)):
                _stage_math_draft(extra, r.get("name", ""), runtime, session_id, source_path)
                return None
    related = [] if metadata_only else _related_memories(summary)
    pool_actor = None
    if native_identity is not None:
        if runtime != "codex" or native_identity.get("session_id") != session_id:
            raise ValueError("native compaction identity mismatch")
        # Recall can take time. Re-read the header and current pool binding
        # after recall, immediately before deriving tasks and writing files.
        fresh = _codex_identity(source_path)
        if fresh != native_identity:
            raise ValueError("native compaction header changed during capture")
        pool_actor = _verified_pool_actor(fresh)
    day = os.path.join(ROOT, when.strftime("%Y-%m-%d"))
    os.makedirs(day, exist_ok=True)
    sid = (session_id or "unknown")[:8]
    if agent:   # twenty stream agents under one session can compact in the same second
        name = "compaction_%s_%s_%s_%s" % (runtime, sid, re.sub(r"[^A-Za-z0-9]", "", agent_id)[:10],
                                           when.strftime("%Y%m%dT%H%M%S"))
    else:
        name = "compaction_%s_%s_%s" % (runtime, sid, when.strftime("%Y%m%dT%H%M%S"))
    if boundary_only:
        name += "_" + sha[:16]  # two observed boundaries in one second remain distinct
    path = os.path.join(day, name + ".md")
    prev = [r for r in rows if r.get("session_id") == session_id and r.get("node")
            and (r.get("agent_id") or "") == agent_id]
    prev = sorted(prev, key=lambda r: r.get("at", ""))
    prev = [r for r in prev if r.get("at", "") <= when.isoformat(timespec="seconds")]   # same second: saved earlier
    tasks = _open_tasks(session_id, runtime, extra_agent=pool_actor)
    same_task = [r for r in rows if set(r.get("tasks") or []) & set(tasks) and r.get("session_id") != session_id][-5:]
    links = ["[[%s]] (the compaction hub)" % HUB]
    if prev:
        links.append("[[%s]] (this %s's previous compaction)" % (prev[-1].get("name", ""),
                                                                  "agent" if agent else "session"))
    for r in same_task:
        links.append("[[%s]] (another session's compaction on the same task)" % r.get("name", ""))
    for slug in related:
        links.append("[[%s]] (a related memory)" % slug)
    head = ["---", "name: %s" % name,
            "node_type: %s" % ("compaction_boundary_metadata" if boundary_only else "compaction_summary"),
            "runtime: %s" % runtime,
            "session_id: %s" % (session_id or "")]
    if agent:
        head += ["agent_id: %s" % agent_id, "agent_type: %s" % (agent.get("agent_type") or ""),
                 "agent_description: %s" % (agent.get("agent_description") or ""),
                 "agent_transcript: %s" % (agent.get("agent_transcript") or "")]
    head += ["trigger: %s" % (trigger or ""),
            "created_at: %s" % when.isoformat(timespec="seconds"), "chars: %d" % len(summary), "sha256: %s" % sha,
            "tasks: %s" % ", ".join(tasks), "previous: %s" % (prev[-1].get("name", "") if prev else ""),
            "related: %s" % ", ".join(related), "anchor: %s" % os.path.join(MEM, name + ".md").replace(os.sep, "/")]
    if source_path:
        head.append("source: %s" % source_path.replace(os.sep, "/"))
    head.append("---")
    body = "\n".join(head) + "\n\n" + summary + "\n\nConnected:\n" + "\n".join("- " + x for x in links) + "\n"
    tmp = path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(_clean(body))
    os.replace(tmp, path)
    anchor = write_anchor(name, summary, when, runtime, session_id, path, links, tasks,
                          metadata_only=metadata_only, capture_kind=capture_kind, agent=agent)
    row = {"at": when.isoformat(timespec="seconds"), "name": name, "runtime": runtime, "session_id": session_id,
           "trigger": trigger, "chars": len(summary), "sha256": sha, "node": path.replace(os.sep, "/"), "tasks": tasks,
           "previous": prev[-1].get("name") if prev else None, "related": related,
           "anchor": anchor.replace(os.sep, "/")}
    if agent:
        row.update({k: v for k, v in agent.items() if k in AGENT_KEYS and v})
    if extra:
        # Math packets are transient inputs. Only the validated draft reference
        # may be copied to the permanent compaction ledger.
        row.update({key: value for key, value in extra.items() if key != "math_solver"})
    if native_identity is not None:
        row["native_header_sha256"] = native_identity["header_sha256"]
        row["agent"] = pool_actor or "codex-" + session_id[:8]
    if metadata_only:
        row["anchor_privacy"] = "metadata_only_codex_boundary" if boundary_only else "metadata_only_manual_digest"
    math_draft = _stage_math_draft(extra, name, runtime, session_id, source_path)
    if math_draft:
        row["math_draft"] = math_draft
    _append(INDEX, row)
    return path


def _stage_math_draft(extra, name, runtime, session_id, source_path):
    """Opt-in solver export; the full session summary is never the math input.

    A runner supplies extra.math_solver or sets COMPACT_MATH_STAGE_FILE to a
    math-only digest packet. Codex sweeps inherit the same explicit stage file.
    """
    packet = extra.get("math_solver") if isinstance(extra, dict) else None
    stage_file = os.environ.get("COMPACT_MATH_STAGE_FILE")
    if packet is None and not stage_file:
        return None
    try:
        if packet is None:
            with io.open(stage_file, encoding="utf-8") as fh:
                raw = fh.read(65537)
            if len(raw) > 65536:
                raise ValueError("math digest packet exceeds 64K characters")
            packet = json.loads(raw)
        from compact_math import stage_math_node
        directory = os.environ.get("COMPACT_MATH_DRAFT_DIR") or os.path.join(ROOT, "math_drafts")
        expected_session = session_id
        if runtime == "codex" and source_path:
            # Bind to the complete rollout UUID, independently of the legacy
            # sweep's session-id slice, which currently drops its first byte.
            match = re.search(r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\.jsonl$",
                              os.path.basename(source_path))
            if match:
                expected_session = match.group(1)
        return stage_math_node(packet, name, directory, expected_session)
    except Exception as exc:
        try:
            _append(os.path.join(ROOT, "math_errors.jsonl"),
                    {"compaction": name, "error_type": type(exc).__name__})
        except Exception:
            pass
        return None


def ensure_anchors():
    """An anchor for every node saved before anchors existed (the 17:40 node); returns how many were written."""
    n = 0
    for r in _index_rows():
        if r.get("capture_kind") == "manual_checkpoint":
            # The manual writer verifies its metadata-only anchor. Rebuilding it
            # here from raw summary text could expose an unlabelled test item.
            continue
        if r.get("anchor") and os.path.exists(r["anchor"]):
            continue
        name, node = r.get("name"), r.get("node")
        if not name or not node or os.path.exists(os.path.join(MEM, name + ".md")):
            continue
        try:
            with io.open(node, encoding="utf-8", errors="replace") as stream:
                text = stream.read()
        except OSError:
            continue
        parts = text.split("\n---\n", 1)
        body = parts[1] if len(parts) == 2 else text
        summary, _, conn = body.partition("\n\nConnected:\n")
        links = [x[2:] for x in conn.splitlines() if x.startswith("- ")]
        try:
            when = _dt.datetime.fromisoformat(r.get("at", ""))
        except ValueError:
            when = _now()
        write_anchor(name, summary.strip(), when, r.get("runtime", "claude"), r.get("session_id", ""), node, links,
                     r.get("tasks") or [], metadata_only=r.get("anchor_privacy") in
                     ("metadata_only_manual_digest", "metadata_only_codex_boundary"),
                     capture_kind=r.get("capture_kind", ""),
                     agent={k: r[k] for k in AGENT_KEYS if r.get(k)} or None)
        _append(INDEX + ".anchors", {"at": _now().isoformat(timespec="seconds"), "name": name, "anchor_written": True})
        n += 1
    return n


def _load_state():
    try:
        return json.load(io.open(STATE, encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(st):
    os.makedirs(ROOT, exist_ok=True)
    with io.open(STATE + ".tmp", "w", encoding="utf-8", newline="\n") as fh:
        json.dump(st, fh)
    os.replace(STATE + ".tmp", STATE)


def _text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(c.get("text", "")) if isinstance(c, dict) else str(c) for c in content)
    return ""


def _when(ts):
    try:
        return _dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone()
    except ValueError:
        return None


# <project>/<session>/subagents/**/agent-<id>.jsonl: the parent's session and the agent's id
_SUBAGENT_RX = re.compile(r"[/\\]([^/\\]+)[/\\]subagents[/\\](?:.*[/\\])?agent-([^/\\]+)\.jsonl$")


def _claude_files(cutoff):
    """Main-thread and subagent transcripts modified since cutoff. The subagents' files are read too since 14 Oct: the
    hook files a subagent's compaction under its parent (attribute() finds the agent later), but a compaction whose
    PostCompact never ran (a crash, a killed session) had no other road to a node."""
    out = []
    for pat in (os.path.join(HOME, ".claude", "projects", "*", "*.jsonl"),
                os.path.join(HOME, ".claude", "projects", "*", "*", "subagents", "**", "agent-*.jsonl")):
        for p in glob.glob(pat, recursive=True):
            try:
                if os.path.getmtime(p) >= cutoff:
                    out.append(p)
            except OSError:
                pass
    return out


def claude_sweep(days=3, paths=None, state=None, checkpoint=None):
    """Save the compactions in Claude transcripts (a compact_boundary record, then the summary as a user record).
    checkpoint(), when given, is called after each file's cursor moves (the sweep saves its state from it)."""
    st = state if state is not None else _load_state()
    cutoff = _now().timestamp() - days * 86400
    files = paths if paths is not None else _claude_files(cutoff)
    saved = 0
    for path in files:
        key = "claude|" + path.replace(os.sep, "/")
        try:
            size = os.path.getsize(path)
        except OSError:
            continue
        start = int(st.get(key, 0))
        if start > size:
            start = 0
        if start == size:
            continue
        sub = _SUBAGENT_RX.search(path)
        if sub:
            sid = sub.group(1)
            agent = agent_identity(sub.group(2), "", sid, os.path.join(path[:sub.start()], sid + ".jsonl")) or None
        else:
            sid, agent = os.path.basename(path)[:-6], None
        pending, resume_at = None, None
        with open(path, "rb") as fh:
            fh.seek(start)
            while True:
                pos = fh.tell()
                raw = fh.readline()
                if not raw:
                    break
                if b'"compact_boundary"' in raw:
                    try:
                        rec = json.loads(raw)
                    except ValueError:
                        continue
                    if rec.get("subtype") == "compact_boundary":
                        pending = (_when(rec.get("timestamp")), (rec.get("compactMetadata") or {}).get("trigger", ""))
                        resume_at = pos
                elif pending and (b'"isCompactSummary"' in raw or b"This session is being continued" in raw):
                    try:
                        rec = json.loads(raw)
                    except ValueError:
                        continue
                    text = _text_of((rec.get("message") or {}).get("content"))
                    if text:
                        if save_node(text, "claude", sid, pending[1], path, pending[0] or _when(rec.get("timestamp")),
                                     extra={"backfilled": True}, window_s=120, agent=agent):
                            saved += 1
                        pending, resume_at = None, None
            end = fh.tell()
        st[key] = resume_at if pending else end   # a boundary whose summary has not been written yet is read again
        if checkpoint:
            checkpoint()
    if state is None:
        _save_state(st)
    return saved


def _codex_files(days, lookback=30):
    """The rollouts of the last `days` days' folders, and every rollout of the `lookback` days before them that was
    written in the window. A rollout lives in the folder of the day its thread started and a long thread keeps writing
    there: until 14 Oct only the last `days` folders were opened, so the FEI100 agents (started 12 Oct) compacted 101
    times in the 24 h to 14 Oct 07:50 and none of it was read."""
    out = []
    now = _now()
    cutoff = now.timestamp() - days * 86400
    for home in glob.glob(os.path.join(HOME, ".codex*")):
        sess = os.path.join(home, "sessions")
        if not os.path.isdir(sess):
            continue
        for d in range(max(int(days), int(lookback)) + 1):
            day = now - _dt.timedelta(days=d)
            folder = os.path.join(sess, day.strftime("%Y"), day.strftime("%m"), day.strftime("%d"))
            try:
                entries = list(os.scandir(folder))   # the listing carries each file's mtime on Windows: no stat per file
            except OSError:
                continue
            for e in entries:
                try:
                    if e.name.endswith(".jsonl") and e.is_file() and (d <= days or e.stat().st_mtime >= cutoff):
                        out.append(e.path)
                except OSError:
                    pass
    return out


def _codex_identity(path):
    """Bind the full filename UUID to the native header, without reading prompts."""
    match = re.search(r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\.jsonl$",
                      os.path.basename(path))
    if not match:
        raise ValueError("rollout filename has no complete native UUID")
    with open(path, "rb") as stream:
        raw = stream.readline(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024 or not raw.endswith(b"\n"):
        raise ValueError("native session header is incomplete or oversized")
    record = json.loads(raw)
    if not isinstance(record, dict):
        raise ValueError("native session header must be an object")
    payload = record.get("payload")
    if (record.get("type") != "session_meta" or not isinstance(payload, dict)
            or payload.get("id") != match.group(1)):
        raise ValueError("native session header and rollout UUID disagree")
    return {"session_id": payload["id"], "cwd": payload.get("cwd"),
            "header_sha256": hashlib.sha256(raw).hexdigest()}


def _verified_pool_actor(identity):
    """A worker label is valid only for its exact cwd and current native thread."""
    cwd = identity.get("cwd")
    if not isinstance(cwd, str) or not os.path.isabs(cwd):
        return None
    norm = lambda p: os.path.normcase(os.path.realpath(p))
    pool = os.path.join(HOME, "brain_workers")
    actor = os.path.basename(os.path.normpath(cwd))
    if not re.fullmatch(r"w[0-9]+", actor) or norm(cwd) != norm(os.path.join(pool, actor)):
        return None
    try:
        with open(os.path.join(pool, "state.json"), "rb") as stream:
            raw = stream.read(8 * 1024 * 1024 + 1)
        if len(raw) > 8 * 1024 * 1024:
            return None
        state = json.loads(raw)
    except (OSError, ValueError):
        return None
    worker = state.get(actor) if isinstance(state, dict) else None
    return actor if isinstance(worker, dict) and worker.get("thread") == identity["session_id"] else None


def _campaign_agent(identity):
    """'<campaign>/<agent>' for an agent of a worker campaign: cwd brain_workers/<campaign>/agents/<agent>, and the
    campaign's own state.json binds that agent to this thread (agents[<agent>].thread; FEI100_20261011 runs a hundred
    Codex agents this way). Anything else -> None."""
    cwd = identity.get("cwd")
    if not isinstance(cwd, str) or not os.path.isabs(cwd):
        return None
    norm = lambda p: os.path.normcase(os.path.realpath(p))
    here = os.path.normpath(cwd)
    agent, agents_dir = os.path.basename(here), os.path.dirname(here)
    campaign = os.path.dirname(agents_dir)
    if (os.path.basename(agents_dir).lower() != "agents" or not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", agent)
            or norm(os.path.dirname(campaign)) != norm(os.path.join(HOME, "brain_workers"))):
        return None
    try:
        with open(os.path.join(campaign, "state.json"), "rb") as stream:
            raw = stream.read(8 * 1024 * 1024 + 1)
        if len(raw) > 8 * 1024 * 1024:
            return None
        state = json.loads(raw)
    except (OSError, ValueError):
        return None
    agents = state.get("agents") if isinstance(state, dict) else None
    entry = agents.get(agent) if isinstance(agents, dict) else None
    if isinstance(entry, dict) and entry.get("thread") == identity.get("session_id"):
        return "%s/%s" % (os.path.basename(campaign), agent)
    return None


def _codex_capture_scope(identity):
    """Exclude test harnesses before allowing a pool worker or named native agent."""
    cwd = identity.get("cwd")
    if not isinstance(cwd, str) or not os.path.isabs(cwd):
        return None
    parts = re.split(r"[/\\]+", cwd.lower())
    if any(p.startswith(("harness", "pair_study", "pair-study", "pd_work", "test_v1", "test_v2")) for p in parts):
        return None
    norm = lambda p: os.path.normcase(os.path.realpath(p))
    actor = os.path.basename(os.path.normpath(cwd))
    if re.fullmatch(r"w[0-9]+", actor) and norm(cwd) == norm(os.path.join(HOME, "brain_workers", actor)):
        return "pool_worker_cwd"
    if _campaign_agent(identity):
        return "campaign_agent_cwd"
    if _open_tasks(identity["session_id"], "codex"):
        return "named_native_checklist_actor"
    if any(r.get("capture_kind") == "manual_checkpoint" and r.get("runtime") == "codex"
           and r.get("session_id") == identity["session_id"]
           and r.get("native_header_sha256") == identity["header_sha256"] for r in _index_rows()):
        return "verified_manual_checkpoint_actor"
    return None


def _codex_capture(rec, identity, raw, offset):
    """Opaque summaries yield a metadata node; retained prompt text is never copied."""
    pay = rec.get("payload") if isinstance(rec.get("payload"), dict) else rec
    text = pay.get("message") or pay.get("summary") or ""
    if isinstance(text, list):
        text = _text_of(text)
    if isinstance(text, str) and text.strip():
        return text, None
    history = pay.get("replacement_history")
    encrypted = isinstance(history, list) and any(isinstance(item, dict) and item.get("type") == "compaction"
                  and isinstance(item.get("encrypted_content"), str) and bool(item["encrypted_content"]) for item in history)
    event = {"sha256": hashlib.sha256(raw).hexdigest(), "byte_offset": offset}
    window = pay.get("window_number")
    if type(window) is int and window >= 0:
        event["window_number"] = window
    usage = pay.get("latest_token_usage_record")
    if isinstance(usage, dict):
        fields = ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens", "total_tokens")
        for group in ("usage", "turn_token_usage", "thread_token_usage"):
            values = usage.get(group)
            if isinstance(values, dict):
                safe = {key: values[key] for key in fields if type(values.get(key)) is int and values[key] >= 0}
                if safe:
                    event[group] = safe
    text = ("Native Codex compaction boundary.\nNo plaintext recovery summary is available from this record.\n"
            "Retained user/developer messages and opaque model state stay in the native transcript.\n"
            "Session: %s\nSource record SHA-256: %s\nSource byte offset: %d\nWindow number: %s\n"
            "NEXT: resume the native session or its linked manual checkpoint; this boundary is metadata only."
            % (identity["session_id"], event["sha256"], offset, event.get("window_number", "unavailable")))
    return text, {"capture_kind": "codex_boundary_metadata", "summary_available": False,
                  "encrypted_summary_present": bool(encrypted), "retained_messages_copied": False,
                  "native_event": event}


def codex_sweep(days=3, paths=None, state=None, checkpoint=None):
    """Save the compaction summaries in Codex rollouts of every Codex home (a 'compacted' record carries the summary)."""
    st = state if state is not None else _load_state()
    saved = 0
    for path in (paths if paths is not None else _codex_files(days)):
        key = "codex|" + path.replace(os.sep, "/")
        try:
            size = os.path.getsize(path)
        except OSError:
            continue
        start = int(st.get(key, 0)) if (paths is None or state is not None) else 0
        if start > size:
            start = 0
        if start == size:
            continue
        with open(path, "rb") as fh:
            fh.seek(start)
            identity = None
            while True:
                pos = fh.tell()
                raw = fh.readline()
                if not raw:
                    break
                if not raw.endswith(b"\n"):
                    fh.seek(pos)  # a writer may still be appending this record
                    break
                if b'"compacted"' not in raw:
                    continue
                try:
                    rec = json.loads(raw)
                except ValueError:
                    continue
                pay = rec.get("payload") if isinstance(rec.get("payload"), dict) else rec
                if rec.get("type") != "compacted" and pay.get("type") != "compacted":
                    continue
                try:
                    identity = identity or _codex_identity(path)
                    sid = identity["session_id"]
                    scope = _codex_capture_scope(identity)
                    if scope is None:
                        _append(os.path.join(ROOT, "EVENTS.jsonl"),
                                {"at": _now().isoformat(timespec="seconds"), "event": "codex_capture_withheld",
                                 "source": path, "session_id": sid, "reason": "scope_not_allowed"})
                        continue
                    text, extra = _codex_capture(rec, identity, raw, pos)
                    node = save_node(str(text), "codex", sid, "auto", path, _when(rec.get("timestamp")),
                                     native_identity=identity, extra=extra)
                except (OSError, ValueError) as exc:
                    # Retain the failing record for repair/retry, and allow the
                    # other rollout files in this sweep to make progress.
                    fh.seek(pos)
                    _append(os.path.join(ROOT, "errors.log"),
                            {"at": _now().isoformat(timespec="seconds"), "event": "codex_capture_rejected",
                             "source": path, "error_type": type(exc).__name__})
                    break
                if node:
                    saved += 1
            st[key] = fh.tell()
        if checkpoint:
            checkpoint()
    if state is None:
        _save_state(st)
    return saved


def sweep_file(path):
    """Save the compaction summaries in one Codex rollout; return how many were new (compact_hooks.py, Codex Stop)."""
    return codex_sweep(paths=[path])


def _spawn_late_attribution(delay=45):
    """Start a detached, windowless, BelowNormal `--attribute --after <delay>` pass: the subagent's compact record lands
    in its transcript only after this hook returns (see agent_from_transcripts), so the row is filed a minute later
    instead of at the next sweep. COMPACT_NO_SPAWN=1 disables it; COMPACT_SPAWN_LOG=<file> records the command instead
    of running it (tests)."""
    if os.environ.get("COMPACT_NO_SPAWN"):
        return None
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    args = [pyw if os.path.exists(pyw) else sys.executable, "-X", "utf8", os.path.abspath(__file__), "--attribute",
            "--days", "0.1", "--after", str(int(delay))]
    if os.environ.get("COMPACT_SPAWN_LOG"):
        _append(os.environ["COMPACT_SPAWN_LOG"], {"args": args})
        return args
    base = 0x00000008 | 0x00004000 | 0x08000000   # DETACHED_PROCESS | BELOW_NORMAL | CREATE_NO_WINDOW
    for flags in (base | 0x01000000, base):       # break away from the hook's job when it is allowed
        try:
            subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             close_fds=True, creationflags=flags)
            return args
        except OSError:
            continue
    return None


def _lock():
    """One sweep at a time (the background backfill and the periodic sweep would race on INDEX.jsonl)."""
    path = os.path.join(ROOT, "sweep.lock")
    os.makedirs(ROOT, exist_ok=True)
    try:
        old = int(io.open(path).read().strip() or 0)
    except (OSError, ValueError):
        old = 0
    if old and old != os.getpid():
        try:
            import psutil
            if psutil.pid_exists(old) and "compact_nodes" in " ".join(psutil.Process(old).cmdline()):
                return None
        except Exception:  # noqa: BLE001
            return None
    with io.open(path, "w") as fh:
        fh.write(str(os.getpid()))
    return path


PREV_LINK_RX = re.compile(r"^\[\[([^\]]+)\]\] \(this (?:session|agent)'s previous compaction\)$")
BARE_DESC_RX = re.compile(r':\s*</?(?:summary|analysis)>\s*"?\s*$')


def _summary_probes(summary):
    s = _summary_probe_text(summary)
    return [s[i:i + 120] for i in (len(s) // 5, len(s) // 2, (4 * len(s)) // 5)] if len(s) >= 300 else []


def _compact_records(path, chunk=8 << 20):
    """Every compact-summary record in one agent transcript, as whitespace-normalised text. Read in chunks and searched
    with bytes.find: a stream agent's transcript runs to tens of MB with a handful of compact records in it."""
    out, carry = [], b""
    marks = (b'"isCompactSummary":true', b'"isCompactSummary": true')
    try:
        with open(path, "rb") as fh:
            while True:
                block = fh.read(chunk)
                buf = carry + block
                if not block:
                    whole, carry = buf, b""
                else:
                    cut = buf.rfind(b"\n") + 1
                    whole, carry = buf[:cut], buf[cut:]
                for mark in marks:
                    pos = whole.find(mark)
                    while pos >= 0:
                        a = whole.rfind(b"\n", 0, pos) + 1
                        b = whole.find(b"\n", pos)
                        b = len(whole) if b < 0 else b
                        try:
                            rec = json.loads(whole[a:b].decode("utf-8", "replace"))
                            if isinstance(rec, dict) and rec.get("isCompactSummary") is True:
                                out.append(_WS_RX.sub(" ", _text_of((rec.get("message") or {}).get("content"))))
                        except ValueError:
                            pass
                        pos = whole.find(mark, b)
                if not block:
                    break
    except OSError:
        pass
    return out


def _node_parts(node_path):
    with io.open(node_path, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    parts = text.split("\n---\n", 1)
    head, body = (parts[0], parts[1]) if len(parts) == 2 else ("", text)
    summary, sep, conn = body.rpartition("\n\nConnected:\n")   # the links close the file; a summary may quote one
    if not sep:
        summary, conn = body, ""
    return head, summary, [x[2:] for x in conn.splitlines() if x.startswith("- ")]


def _anchor_links(path):
    try:
        with io.open(path, encoding="utf-8", errors="replace") as fh:
            _, sep, conn = fh.read().rpartition("\nConnected:\n")
    except OSError:
        return []
    return [x[2:] for x in conn.splitlines() if x.startswith("- ")] if sep else []


def _row_agent(row):
    return {k: row[k] for k in AGENT_KEYS if row.get(k)} or None


def _rewrite_compaction(row, agent, prev, node_too=True):
    """Re-file one saved compaction with this agent and this previous compaction: the node's frontmatter and links
    (node_too), then its anchor through write_anchor, keeping any related links relink() added to the anchor."""
    name, node = row["name"], row["node"]
    head, summary, node_links = _node_parts(node)
    kind = "agent" if agent else "session"

    def chained(links):
        out = [x for x in links if not PREV_LINK_RX.match(x)]
        if prev:
            out.insert(1 if out and out[0].endswith("(the compaction hub)") else 0,
                       "[[%s]] (this %s's previous compaction)" % (prev, kind))
        return out

    if node_too:
        hl = []
        for ln in head.split("\n"):
            if ln.startswith("previous:"):
                hl.append("previous: %s" % (prev or ""))
                continue
            if ln.startswith("agent_"):
                continue
            hl.append(ln)
            if ln.startswith("session_id:") and agent:
                hl += ["agent_id: %s" % agent["agent_id"], "agent_type: %s" % (agent.get("agent_type") or ""),
                       "agent_description: %s" % (agent.get("agent_description") or ""),
                       "agent_transcript: %s" % (agent.get("agent_transcript") or "")]
        text = "\n".join(hl) + "\n---\n" + summary + "\n\nConnected:\n" + "\n".join(
            "- " + x for x in chained(node_links)) + "\n"
        with io.open(node + ".tmp", "w", encoding="utf-8", newline="\n") as fh:
            fh.write(_clean(text))
        os.replace(node + ".tmp", node)
    apath = os.path.join(MEM, name + ".md")
    links = chained(node_links)
    links += [x for x in _anchor_links(apath) if x not in links and not PREV_LINK_RX.match(x)]
    try:
        when = _dt.datetime.fromisoformat(row.get("at", ""))
    except ValueError:
        when = _now()
    write_anchor(name, summary.strip(), when, row.get("runtime", "claude"), row.get("session_id", ""), node, links,
                 row.get("tasks") or [], metadata_only=row.get("anchor_privacy") in
                 ("metadata_only_manual_digest", "metadata_only_codex_boundary"),
                 capture_kind=row.get("capture_kind", ""), agent=agent)


def _rewrite_index(updates, tries=20):
    """Set fields on INDEX rows by name, every other line kept byte for byte. A PostCompact hook may append while this
    runs: the file is re-read and the replace retried until its size held still. Returns True when written."""
    for _ in range(tries):
        try:
            with io.open(INDEX, encoding="utf-8", newline="") as fh:
                data = fh.read()
        except OSError:
            return False
        lines = data.split("\n")
        for k, ln in enumerate(lines):
            if not ln.strip():
                continue
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            up = updates.get(r.get("name"))
            if up:
                r.update(up)
                lines[k] = json.dumps(r, ensure_ascii=False)
        size = len(data.encode("utf-8"))
        with io.open(INDEX + ".rewrite.tmp", "w", encoding="utf-8", newline="") as fh:
            fh.write("\n".join(lines))
        try:
            if os.path.getsize(INDEX) != size:
                time.sleep(0.3)
                continue
            os.replace(INDEX + ".rewrite.tmp", INDEX)
            return True
        except OSError:
            time.sleep(0.3)
    return False


ATTRIB_LAST = {}   # the last attribute() pass: {"found": {name: agent}, "updates": {name: fields}} (dry runs, tests)


def attribute(days=1.0, state=None, settle_s=1800, dry=False):
    """Give compactions saved without their subagent their agent, and re-chain previous within each identity.

    The PostCompact payload carries no agent_id and the hook's own transcript match can run before the agent's
    compact-summary record reaches its file (12 Oct 15:25: s05's and s11's compactions were saved unattributed and
    matched minutes later). For every claude row of the last `days` without an agent, in a session with a subagents
    tree, look for its summary in every compact record of every agent transcript written since; a unique match files
    it under that agent. Then every row of each touched session gets its previous compaction re-derived within its
    own identity (agent or main thread), and the changed nodes, anchors and INDEX rows are rewritten. A row with no
    match is checked again until it is settle_s old. Returns how many rows were re-filed (dry: would be; nothing is
    written and the state is left alone)."""
    st = state if state is not None else _load_state()
    checked = set(st.get("attrib_checked") or [])
    rows = []
    try:
        with io.open(INDEX, encoding="utf-8", errors="replace") as fh:
            for ln in fh:
                try:
                    rows.append(json.loads(ln))
                except ValueError:
                    pass
    except OSError:
        return 0
    now = _now()
    cutoff = now.timestamp() - days * 86400
    cand = {}
    for r in rows:
        if r.get("runtime") != "claude" or r.get("agent_id") or not r.get("name") or r["name"] in checked:
            continue
        when = _when(r.get("at"))
        if when is None or when.timestamp() < cutoff:
            continue
        cand.setdefault(r.get("session_id") or "", []).append((r, when))
    found = {}
    for sid, rs in cand.items():
        tops = glob.glob(os.path.join(HOME, ".claude", "projects", "*", sid))
        sub = [os.path.join(t, "subagents") for t in tops if os.path.isdir(os.path.join(t, "subagents"))]
        if not sid or not sub:
            checked.update(r["name"] for r, _ in rs)
            continue
        earliest = min(w.timestamp() for _, w in rs) - 300
        recs = {}
        for f in glob.glob(os.path.join(sub[0], "**", "agent-*.jsonl"), recursive=True):
            try:
                if os.path.getmtime(f) < earliest:
                    continue
            except OSError:
                continue
            got = _compact_records(f)
            if got:
                recs[os.path.basename(f)[len("agent-"):-len(".jsonl")]] = got
        parent = os.path.join(os.path.dirname(sub[0]) + ".jsonl")
        for r, when in rs:
            try:
                probes = _summary_probes(_node_parts(r["node"])[1])
            except (OSError, KeyError, TypeError):
                probes = []
            hits = {aid for aid, texts in recs.items() if any(sum(1 for p in probes if p in t) >= 2 for t in texts)}
            if len(hits) == 1:
                found[r["name"]] = agent_identity(hits.pop(), "", sid, parent)
            elif now.timestamp() - when.timestamp() > settle_s or len(hits) > 1:
                checked.add(r["name"])
    n = 0
    if found:
        touched = {r.get("session_id") for r in rows if r.get("name") in found}
        # the chain as save_node draws it: every row with a node in the session (manual checkpoints included), in time
        # order, each chained to the identity's latest earlier row; manual checkpoints are links, never rewritten here
        chain = [r for r in _index_rows() if r.get("session_id") in touched and r.get("name") and r.get("node")]
        chain.sort(key=lambda r: (_when(r.get("at")) or now).timestamp())
        last, updates = {}, {}
        for r in chain:
            agent = found.get(r["name"]) or _row_agent(r)
            ident = (r.get("session_id"), (agent or {}).get("agent_id", ""))
            prev = last.get(ident)
            last[ident] = r["name"]
            if r.get("capture_kind") == "manual_checkpoint" or r.get("runtime") != "claude":
                continue
            if r["name"] in found or (prev or None) != (r.get("previous") or None):
                if not dry:
                    try:
                        _rewrite_compaction(r, agent, prev)
                    except (OSError, KeyError, TypeError):
                        continue
                up = {"previous": prev}
                if r["name"] in found:
                    up.update(found[r["name"]])
                updates[r["name"]] = up
        ATTRIB_LAST.clear()
        ATTRIB_LAST.update({"found": found, "updates": updates})
        if dry:
            return len(found)
        if updates and _rewrite_index(updates):
            n = sum(1 for k in updates if k in found)
            _append(INDEX + ".attributed", {"at": now.isoformat(timespec="seconds"), "attributed": sorted(found),
                                            "rechained": sorted(k for k in updates if k not in found)})
    if dry:
        ATTRIB_LAST.clear()
        ATTRIB_LAST.update({"found": {}, "updates": {}})
        return 0
    window = {r.get("name") for r in rows if (_when(r.get("at")) or now).timestamp() >= cutoff}
    st["attrib_checked"] = sorted(checked & window)
    return n


def refresh_gists(dry=False):
    """Rewrite every anchor whose description ended in a bare <summary>/<analysis> tag (the gist before 12 Oct)."""
    n = 0
    for r in _index_rows():
        if r.get("capture_kind") == "manual_checkpoint" or not r.get("name") or not r.get("node"):
            continue
        apath = os.path.join(MEM, r["name"] + ".md")
        try:
            with io.open(apath, encoding="utf-8", errors="replace") as fh:
                desc = next((ln for ln in fh if ln.startswith("description:")), "")
        except OSError:
            continue
        if BARE_DESC_RX.search(desc.rstrip("\n")):
            if dry:
                n += 1
                continue
            try:
                _rewrite_compaction(r, _row_agent(r), r.get("previous"), node_too=False)
                n += 1
            except (OSError, KeyError, TypeError):
                continue
    return n


def sweep(days=3, which=("claude", "codex"), throttle=0.0, relink_n=None):
    """One pass: the transcripts' compactions, the anchors, the subagent attribution, then up to relink_n anchors given
    their related memories (COMPACT_RELINK_PER_SWEEP, default 10). The state is saved at most every 20 s while the
    transcripts are read and after every phase, and a phase that fails is written to errors.log while the rest run. Until
    14 Oct the state was saved only after a whole phase: a sweep that died in the middle (the 9-14 Oct ones never even
    started, see compact_hooks._maybe_spawn_sweep) began again at byte 0 of every file, 3.7 GB for one day of
    transcripts."""
    events = os.path.join(ROOT, "EVENTS.jsonl")
    lock = _lock()
    if not lock:
        try:
            holder = io.open(os.path.join(ROOT, "sweep.lock")).read().strip()
        except OSError:
            holder = ""
        _append(events, {"at": _now().isoformat(timespec="seconds"), "event": "sweep_refused", "pid": os.getpid(),
                         "holder": holder})
        return None
    if relink_n is None:
        relink_n = int(os.environ.get("COMPACT_RELINK_PER_SWEEP") or 10)
    t0 = time.time()
    _append(events, {"at": _now().isoformat(timespec="seconds"), "event": "sweep_start", "pid": os.getpid(), "days": days,
                     "which": list(which)})
    c = x = a = t = r = q = 0
    failed = []
    try:
        st = _load_state()
        last = [time.time()]

        def checkpoint():
            if time.time() - last[0] >= 20:
                _save_state(st)
                last[0] = time.time()

        def phase(name, fn):
            try:
                return fn() or 0
            except Exception as exc:  # noqa: BLE001  - the sweep's other work must still be recorded
                failed.append(name)
                _append(os.path.join(ROOT, "errors.log"), {"at": _now().isoformat(timespec="seconds"),
                                                           "error": name + ": " + _clean(repr(exc))[:400]})
                return 0
            finally:
                _save_state(st)

        def codex_throttled():   # the first pass reads every rollout once; spread it out on a busy disk
            n = 0
            for f in _codex_files(days):
                n += codex_sweep(days, paths=[f], state=st)
                checkpoint()
                time.sleep(throttle)
            return n

        if "claude" in which:
            c = phase("claude_sweep", lambda: claude_sweep(days, state=st, checkpoint=checkpoint))
        if "codex" in which:
            x = phase("codex_sweep", codex_throttled if throttle else
                      (lambda: codex_sweep(days, state=st, checkpoint=checkpoint)))
        a = phase("ensure_anchors", ensure_anchors)
        if "claude" in which:
            t = phase("attribute", lambda: attribute(max(days, 1), state=st))
        if _query_common_stale():   # once a day: the words and quotes too common to say what one session was about
            q = phase("query_common", build_query_common)
        if _mem_df_stale():   # once a day: the memory folder's word counts the relevance gate reads
            _append(events, {"at": _now().isoformat(timespec="seconds"), "event": "mem_df",
                             "memories": phase("mem_df", build_mem_df)})
        if relink_n > 0:
            r = phase("relink", lambda: relink(relink_n))
    finally:
        try:
            os.remove(lock)
        except OSError:
            pass
    _append(events, {"at": _now().isoformat(timespec="seconds"), "event": "sweep", "claude_saved": c, "codex_saved": x,
                     "anchors_written": a, "attributed": t, "relinked": r, "query_common_nodes": q,
                     "seconds": round(time.time() - t0),
                     "failed": failed})
    return c, x, a


def list_compactions(paths):
    """Read-only: how many compaction records each file holds (for planning shards). Returns {path: count}."""
    out = {}
    for p in paths:
        n = 0
        try:
            with open(p, "rb") as fh:
                for raw in fh:
                    if b'"compact_boundary"' in raw or (b'"compacted"' in raw and b'"type"' in raw):
                        n += 1
        except OSError:
            continue
        out[p] = n
    return out


def shard(paths_file, state_file=None):
    """Process exactly the files listed (one path per line), with a cursor state of their own: shards run in parallel
    (team C, his 20:04 order) without the global lock; a compaction saved by another shard is not saved again (sha)."""
    paths = [l.strip() for l in io.open(paths_file, encoding="utf-8") if l.strip() and not l.startswith("#")]
    state_file = state_file or os.path.join(ROOT, "shard_states", os.path.basename(paths_file) + ".json")
    os.makedirs(os.path.dirname(state_file), exist_ok=True)
    try:
        st = json.load(io.open(state_file, encoding="utf-8"))
    except (OSError, ValueError):
        st = {}
    c = x = 0
    for p in paths:
        norm = p.replace(os.sep, "/")
        if "/.claude/projects/" in norm:
            c += claude_sweep(paths=[p], state=st)
        elif "/.codex" in norm:
            x += codex_sweep(paths=[p], state=st)
        with io.open(state_file + ".tmp", "w", encoding="utf-8", newline="\n") as fh:
            json.dump(st, fh)
        os.replace(state_file + ".tmp", state_file)
    return c, x


def _gate_logged(path):
    """The anchor names a log holds rows for under the current gate (GATE_VERSION)."""
    names = set()
    try:
        with io.open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict) and row.get("gate") == GATE_VERSION:
                    names.add(row.get("name"))
    except OSError:
        pass
    return names


def _relinked_names():
    """Anchors relink has already answered under the current gate: memories found, or nothing found from the current
    link text (a row carrying LINK_VERSION). A row from before the gate does not count: that anchor is asked again if
    regate left it no related link. Nor does an empty answer from an older link text: since 14 Oct (x1) the linker
    reads the summary without its session plumbing, and an anchor that text never asked about is asked once more."""
    names = set()
    try:
        with io.open(RELINK_LOG, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict) and row.get("gate") == GATE_VERSION and (
                        row.get("related") or row.get("added") or row.get("link") == LINK_VERSION):
                    names.add(row.get("name"))
    except OSError:
        pass
    return names


def _regate_thinned():
    """Anchors regate took at least one link out of under the current gate."""
    names = set()
    try:
        with io.open(REGATE_LOG, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict) and row.get("gate") == GATE_VERSION and row.get("removed"):
                    names.add(row.get("name"))
    except OSError:
        pass
    return names


def _anchor_related(text):
    """The memories an anchor links as '(a related memory)'."""
    return [m.group(1) for m in (_RELATED_LINE.match(l) for l in text.split("\n")) if m]


def regate(limit=None, dry=False):
    """Hold the related links already written to the gate. In each anchor, a '(a related memory)' line whose memory
    fails the gate is taken out; the hub, session and task links stay, and so does a link whose memory cannot be read.
    Each anchor read is logged to REGATE_LOG with the links removed and kept and their scores, so a removal can be put
    back by hand; an anchor left with no related link is linked again by relink. Anchors already logged under this gate
    are skipped. Returns (anchors read, anchors changed, links removed, links kept), or None without a word table."""
    n, df = _mem_df()
    if df is None:
        if not build_mem_df():
            return None
        n, df = _mem_df()
        if df is None:
            return None
    done = _gate_logged(REGATE_LOG)
    read = changed = removed_n = kept_n = 0
    for r in _index_rows():
        if limit is not None and read >= limit:
            break
        name, anchor = r.get("name"), r.get("anchor")
        if not anchor or name in done or not os.path.exists(anchor) or not r.get("node"):
            continue
        try:
            with io.open(anchor, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            if not _anchor_related(text):
                continue
            words = _gate_words(_node_body(r["node"]))
        except OSError:
            continue
        out, removed, kept = [], [], []
        for line in text.split("\n"):
            m = _RELATED_LINE.match(line)
            if m:
                score = _gate_score(m.group(1), words, n, df)
                if score is not None and score <= GATE_MIN:
                    removed.append([m.group(1), round(score, 4)])
                    continue
                kept.append([m.group(1), None if score is None else round(score, 4)])
            out.append(line)
        read += 1
        removed_n += len(removed)
        kept_n += len(kept)
        if dry:
            continue
        if removed:
            tmp = anchor + ".regate.%d" % os.getpid()
            with io.open(tmp, "w", encoding="utf-8", newline="\n") as fh:
                fh.write("\n".join(out))
            os.replace(tmp, anchor)
            changed += 1
        _append(REGATE_LOG, {"at": _now().isoformat(timespec="seconds"), "name": name, "anchor": anchor,
                             "gate": GATE_VERSION, "min": GATE_MIN, "removed": removed, "kept": kept})
        done.add(name)
    return read, changed, removed_n, kept_n


def relink(limit=50, timeout=60):
    """Give anchors saved without related memories (recall timed out, or --no-recall) their related links, newest first,
    at most `limit` recall calls. An anchor recall answered is logged to INDEX.jsonl.relinked and not asked again; one it
    could not answer (timed out) is tried again by a later pass. Until 14 Oct every pass began at the oldest row, asked
    again about every anchor it had already linked (INDEX keeps the save-time 'related'), and counted only the
    anchors it linked, so on a store where recall found nothing it never stopped. Since the gate (14 Oct) an anchor is
    asked when it has no answer under this gate and holds no related link (none at save time, or regate took them all
    out) or regate thinned it below RELINK_FILL; the new links fill it back to RELINK_FILL. Until then a save-time list
    in INDEX kept an anchor out even after its links were gone. (Regate kept about four in five of the links it left on
    topic and dropped about two in five of the good ones, measured 14 Oct on a blind set, hence the refill.)"""
    done = tried = 0
    if _mem_df()[1] is None:
        build_mem_df()
    answered = _relinked_names()
    thinned = _regate_thinned()
    for r in reversed(_index_rows()):
        if tried >= limit:
            break
        if r.get("capture_kind") == "manual_checkpoint" or r.get("anchor_privacy") in (
                "metadata_only_manual_digest", "metadata_only_codex_boundary"):
            continue
        if r.get("name") in answered or not r.get("anchor") or not os.path.exists(r["anchor"]):
            continue
        try:
            with io.open(r["anchor"], encoding="utf-8", errors="replace") as stream:
                have = _anchor_related(stream.read())
            if have and (len(have) >= RELINK_FILL or r.get("name") not in thinned):
                continue
            with io.open(r["node"], encoding="utf-8", errors="replace") as stream:
                text = stream.read()
        except OSError:
            continue
        body = text.split("\n---\n", 1)[-1].partition("\n\nConnected:\n")[0]
        if matches_manual_checkpoint(body):
            continue
        tried += 1
        rel = _recall_related(body, timeout=timeout)
        if rel is None:
            continue
        try:
            with io.open(r["anchor"], encoding="utf-8") as stream:
                a = stream.read().rstrip("\n")
        except OSError:
            continue
        add = [s for s in rel if "[[%s]]" % s not in a][:max(0, RELINK_FILL - len(_anchor_related(a)))]
        if add:
            a += "\n" + "\n".join("- [[%s]] (a related memory)" % s for s in add) + "\n"
            tmp = "%s.tmp%d" % (r["anchor"], os.getpid())   # a backfill and the sweep's relink may run at once
            with io.open(tmp, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(a)
            os.replace(tmp, r["anchor"])
            done += 1
        _append(RELINK_LOG, {"at": _now().isoformat(timespec="seconds"), "name": r.get("name"), "related": rel,
                             "added": add, "gate": GATE_VERSION, "link": LINK_VERSION})
        answered.add(r.get("name"))
    return done


def main():
    if "--no-recall" in sys.argv:
        os.environ["COMPACT_NO_RECALL"] = "1"
    if "--list" in sys.argv:
        files = [l.strip() for l in io.open(sys.argv[sys.argv.index("--list") + 1], encoding="utf-8") if l.strip()]
        counts = list_compactions(files)
        print("files %d, with compactions %d, compaction records %d" % (len(counts), sum(1 for v in counts.values() if v),
                                                                     sum(counts.values())))
        return
    if "--paths-file" in sys.argv:
        pf = sys.argv[sys.argv.index("--paths-file") + 1]
        sf = sys.argv[sys.argv.index("--state") + 1] if "--state" in sys.argv else None
        c, x = shard(pf, sf)
        a = ensure_anchors()
        print("compaction shard %s: claude nodes %d, codex nodes %d, anchors written %d" % (os.path.basename(pf), c, x, a))
        return
    if "--build-query-common" in sys.argv:
        print("query_common: nodes read", build_query_common())
        return
    if "--build-mem-df" in sys.argv:
        print("mem_df: memories read", build_mem_df())
        return
    if "--regate" in sys.argv:   # --regate [N] [--dry]: hold the related links already written to the gate
        i = sys.argv.index("--regate")
        lim = int(sys.argv[i + 1]) if len(sys.argv) > i + 1 and sys.argv[i + 1].isdigit() else None
        print("regate (anchors read, changed, links removed, links kept):", regate(lim, dry="--dry" in sys.argv))
        return
    if "--relink" in sys.argv:
        print("anchors relinked:", relink(int(sys.argv[sys.argv.index("--relink") + 1]) if len(sys.argv) > sys.argv.index("--relink") + 1 else 50))
        return
    if "--codex-sweep" in sys.argv:
        print("codex compaction nodes saved:", codex_sweep())
        return
    if "--attribute" in sys.argv or "--refresh-gists" in sys.argv:
        after = float(sys.argv[sys.argv.index("--after") + 1]) if "--after" in sys.argv else 0.0
        if after:
            time.sleep(after)
        lock = _lock()
        for _ in range(24 if after else 0):   # the late pass waits up to two minutes for a running sweep
            if lock:
                break
            time.sleep(5)
            lock = _lock()
        if not lock:
            print("compaction attribution: a sweep holds the lock")
            return
        dry = "--dry" in sys.argv
        try:
            if "--attribute" in sys.argv:
                days = float(sys.argv[sys.argv.index("--days") + 1]) if "--days" in sys.argv else 1.0
                st = _load_state()
                k = attribute(days, state=st, dry=dry)
                ups = ATTRIB_LAST.get("updates") or {}
                print("compactions %sfiled under their subagent: %d; rows re-chained without a new agent: %d" % (
                    "that would be " if dry else "", k, sum(1 for x in ups if x not in (ATTRIB_LAST.get("found") or {}))))
                for name, a in sorted((ATTRIB_LAST.get("found") or {}).items()):
                    print("  %s -> %s %s" % (name, a.get("agent_id", ""), a.get("agent_description", "")))
                if not dry:
                    _save_state(st)
            if "--refresh-gists" in sys.argv:
                print("anchors with a bare-tag gist %s:" % ("to rewrite" if dry else "rewritten"), refresh_gists(dry))
        finally:
            try:
                os.remove(lock)
            except OSError:
                pass
        return
    if "--sweep" in sys.argv:
        days = int(sys.argv[sys.argv.index("--days") + 1]) if "--days" in sys.argv else 3
        which = tuple(w for w in ("claude", "codex") if "--" + w + "-only" in sys.argv) or ("claude", "codex")
        throttle = float(sys.argv[sys.argv.index("--throttle") + 1]) if "--throttle" in sys.argv else 0.0
        res = sweep(days, which, throttle)
        if res is None:
            print("compaction sweep: another sweep holds the lock")
            return
        c, x, a = res
        print("compaction sweep: claude nodes %d, codex nodes %d, anchors written %d" % (c, x, a))
        return
    raw = sys.stdin.buffer.read().decode("utf-8", "replace")   # never the console code page (the 18:16 node's mojibake)
    try:
        data = json.loads(raw) if raw.strip() else {}
    except ValueError:
        data = {}
    event = data.get("hook_event_name") or ("PostCompact" if "--post" in sys.argv else "PreCompact")
    sid, trig = data.get("session_id", ""), data.get("trigger", "")
    agent = agent_identity(data.get("agent_id"), data.get("agent_type"), sid, data.get("transcript_path", "")) \
        if data.get("agent_id") else {}
    if not agent and event == "PostCompact":
        agent = agent_from_transcripts(data.get("compact_summary", ""), sid, data.get("transcript_path", ""))
    events = os.path.join(ROOT, "EVENTS.jsonl")
    if event == "PreCompact":
        _append(events, dict({"at": _now().isoformat(timespec="seconds"), "event": "PreCompact", "session_id": sid,
                              "trigger": trig, "custom_instructions": _clean(data.get("custom_instructions") or "")[:400]},
                             **({"agent_id": agent["agent_id"]} if agent else {})))
        print(KEEP)
        return
    path = save_node(data.get("compact_summary", ""), "claude", sid, trig, data.get("transcript_path", ""),
                     extra={"math_solver": data["math_solver"]} if "math_solver" in data else None, agent=agent or None)
    _append(events, dict({"at": _now().isoformat(timespec="seconds"), "event": "PostCompact", "session_id": sid,
                          "trigger": trig, "node": (path or "").replace(os.sep, "/")},
                         **({"agent_id": agent["agent_id"]} if agent else {})))
    if path:
        print("compaction summary saved as its own brain node: %s" % path.replace(os.sep, "/"))
    tp = data.get("transcript_path", "")
    if path and not agent and sid and tp and os.path.isdir(os.path.join(os.path.dirname(tp), sid, "subagents")):
        _spawn_late_attribution()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        try:
            _append(os.path.join(ROOT, "errors.log"), {"at": _now().isoformat(timespec="seconds"), "error": _clean(repr(exc))[:500]})
        except Exception:
            pass
