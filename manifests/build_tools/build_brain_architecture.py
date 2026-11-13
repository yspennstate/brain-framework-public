#!/usr/bin/env python3
"""Build the brain's navigation layer from the flat memory corpus.

Reads every memory file in the live brain (claude/memory) and generates, under
01_ai_consciousness/brain/:
  BRAIN_INDEX.md  - master catalog of every memory store in the repo
  TOPICS.md       - the live brain grouped by topic
  TIMELINE.md     - the live brain on an episodic (monthly) axis
  GRAPH.md        - wiki-link graph: hubs, backlinks, orphans, node size by usage
  STATS.md        - corpus statistics

Re-run any time:  python manifests/build_tools/build_brain_architecture.py <repo_root> [<live memory dir>]
Pure read-only over the memory files; only writes into brain/.
"""
import os, re, sys, io
from collections import defaultdict, Counter

if any(a in ("-h", "--help") for a in sys.argv[1:]):
    # Discovery is not execution: this file does its work at module scope, so the
    # guard must come before the first statement that runs.
    print(__doc__ or os.path.basename(__file__))
    raise SystemExit(0)


ROOT = sys.argv[1] if len(sys.argv) > 1 else "."
CONS = os.path.join(ROOT, "01_ai_consciousness")
# optional second argument: the live memory folder, when the repo's mirror of it lags (macOS has no
# mirror step; its views describe ~/.claude/projects/<slug>/memory, 30 Oct 2026)
LIVE = sys.argv[2] if len(sys.argv) > 2 else os.path.join(CONS, "claude", "memory")
OUT  = os.path.join(CONS, "brain")
os.makedirs(OUT, exist_ok=True)

import contextlib
@contextlib.contextmanager
def _out(name):
    """Write a view to name.tmp and rename it over the old one, so a build stopped by its cap (RELATED.md
    takes over an hour on a loaded macOS machine) leaves the previous generation instead of a cut-off file."""
    path = os.path.join(OUT, name)
    with io.open(path + ".tmp", "w", encoding="utf-8") as fh:
        yield fh
    os.replace(path + ".tmp", path)
DREAMING = os.path.join(ROOT, "12_cognitive_architecture", "dreaming")
sys.path.insert(0, DREAMING)
# A fresh install keeps the dreaming scripts only under ~/.claude/dreaming; the repo copy of
# that folder appears later, if at all. Look there second so supersession.py is found either way.
sys.path.insert(1, os.path.join(os.path.expanduser("~"), ".claude", "dreaming"))
from supersession import active_supersessions
SUPERSEDED = active_supersessions(
    LIVE, index_path=os.path.join(DREAMING, "superseded.tsv"), include_frontmatter=True)

# conscious/unconscious tiers (consciousness.py sidecar): demote the latent substrate in RELATED so
# the redundant logs stop leading conscious memories' neighbour lists. Home-anchored; if the sidecar
# is absent, UNCONSCIOUS stays empty and the graph behaves exactly as before.
UNCONSCIOUS = set()
try:
    _cs = os.path.expanduser(os.path.join("~", ".claude", "dreaming", "consciousness.tsv"))
    with io.open(_cs, "r", encoding="utf-8") as _cf:
        next(_cf, None)
        for _ln in _cf:
            _p = _ln.rstrip("\n").split("\t")
            if len(_p) >= 2 and _p[1] == "unconscious":
                UNCONSCIOUS.add(_p[0])
except Exception:
    UNCONSCIOUS = set()

# Hebbian co-activation weights (coactivation.tsv, written by the PostToolUse/Stop hooks): how often
# two memories were READ together across sessions. Blended into RELATED so association reflects what
# the brain reasons with together, not only shared vocabulary. Home-anchored; empty if absent.
COACT = {}
try:
    _cw = os.path.expanduser(os.path.join("~", ".claude", "dreaming", "coactivation.tsv"))
    with io.open(_cw, "r", encoding="utf-8") as _cf:
        next(_cf, None)
        for _ln in _cf:
            _p = _ln.rstrip("\n").split("\t")
            if len(_p) >= 3 and _p[0] != "a":
                try:
                    _a, _b, _wt = _p[0], _p[1], float(_p[2])
                except Exception:
                    continue
                COACT.setdefault(_a, {})[_b] = _wt
                COACT.setdefault(_b, {})[_a] = _wt
except Exception:
    COACT = {}

# USAGE (base_activation.tsv, written by base_activation.py from the retrieval trace): ACT-R
# base-level activation B = ln( sum_j (T - t_j)^-0.5 ) over past reads, so recency and frequency
# are one number. The brain has computed this since 2026-07-30 and used it only to tilt ranking
# inside recall; nothing ever drew it, so the corpus had no picture of what it actually uses.
# Home-anchored; empty if absent, and an empty signal draws a uniform map on purpose.
ACT, ACT_N = {}, {}
try:
    _ba = os.path.expanduser(os.path.join("~", ".claude", "dreaming", "base_activation.tsv"))
    with io.open(_ba, "r", encoding="utf-8") as _bf:
        next(_bf, None)
        for _ln in _bf:
            _p = _ln.rstrip("\n").split("\t")
            if len(_p) >= 2:
                try:
                    ACT[_p[0]] = float(_p[1])
                except ValueError:
                    continue
                if len(_p) >= 3:
                    try:
                        ACT_N[_p[0]] = int(_p[2])
                    except ValueError:
                        pass
except Exception:
    ACT, ACT_N = {}, {}

# Node size from usage, in vanilla-brain's mapping (brain/viz_data.py:645-657) so the two brains
# agree on what a size means:
#     size = MIN + sqrt(use / use_max) * (MAX - MIN)
# relative to the busiest node (differences show from the first read, not only after dozens),
# sqrt so mid-usage nodes stay distinguishable instead of bunching at the floor, and uniform when
# nothing has been read - a uniform map is the honest picture of no usage.
#
# One deliberate substitution. Vanilla's `use` is a monotone recall counter that never decays, so
# a memory hammered six months ago outranks one hot today, permanently. Here `use` is the
# min-max normalized ACT-R activation, which carries the decay - and it is deliberately the SAME
# transform the ranker applies (brain.py `_load_usage_norm`), so the picture shows exactly what
# recall's usage tilt sees rather than a second, differently-shaped usage signal.
#
# exp(B) - the literal age-discounted read count, and the closer analogue of vanilla's counter -
# was tried first and measured wrong for a map: ACT-R gives the most recent read a near-singular
# weight ((T-t)^-0.5 with t seconds old), so on 2026-08-22 it put 1,321 of 1,439 nodes into one
# bucket behind a single giant that had been read once. The log keeps the ordering and restores
# the spread (median size 11.6 -> 16.2).
NODE_SIZE_MIN, NODE_SIZE_MAX = 11.0, 30.0
_B_LO = min(ACT.values()) if ACT else 0.0
_B_SPAN = ((max(ACT.values()) - _B_LO) or 1.0) if ACT else 1.0
USE = {_s: (_b - _B_LO) / _B_SPAN for _s, _b in ACT.items()}
USE_MAX = max(USE.values()) if USE else 0.0


def node_size(slug):
    """Rendered size of one node, given how much the brain actually uses it."""
    if USE_MAX <= 0:
        return NODE_SIZE_MIN
    frac = (max(0.0, USE.get(slug, 0.0)) / USE_MAX) ** 0.5
    return round(NODE_SIZE_MIN + frac * (NODE_SIZE_MAX - NODE_SIZE_MIN), 3)


def size_bar(size, width=24):
    """The size as something an eye can compare at a glance."""
    span = NODE_SIZE_MAX - NODE_SIZE_MIN
    n = int(round(width * ((size - NODE_SIZE_MIN) / span if span else 0.0)))
    return "█" * max(0, min(width, n))

# transient agent-scratch (round/cycle/loop audit checkpoints) is archived — never
# deleted — into claude/memory/transient/, outside the flat recall corpus. Counted
# here for honesty; excluded from every view because the scan below is non-recursive.
TRANSIENT_DIR = os.path.join(LIVE, "transient")
n_archived_transient = (sum(1 for x in os.listdir(TRANSIENT_DIR) if x.startswith("transient_") and x.endswith(".md"))
                        if os.path.isdir(TRANSIENT_DIR) else 0)

FM_NAME = re.compile(r"^name:\s*(.+)$", re.M)
FM_DESC = re.compile(r"^description:\s*(.+)$", re.M)
FM_TYPE = re.compile(r"^\s*type:\s*(\w+)", re.M)
WIKI    = re.compile(r"\[\[([^\]\|#]+)\]\]")
DATE_F  = re.compile(r"(202[4-7])[_-](0[1-9]|1[0-2])(?:[_-](\d{2}))?")
_THIS_MONTH = __import__("time").strftime("%Y-%m")

# Topic rules: first match wins (ordered). These are DEPLOYMENT-SPECIFIC - a brain's subjects are
# whatever the person it serves works on. Point BRAIN_TOPICS_FILE at a JSON file to replace them:
#   [{"label": "Infrastructure", "pattern": "box|crash|gpu|ssh|docker|systemd"}, ...]
# The defaults below are the generic shape every brain has: the machinery it runs on, the work it
# does, the agents that do it, and how to work with the person it belongs to.
DEFAULT_TOPICS = [
 ("Infrastructure & machines (hosts, migration, crashes, agents)",
  r"migration|box|crash|gpu|laptop|ssh|vnc|tunnel|docker|systemd|scheduler|codex|claude|memory|session|windows|linux|shell|terminal|flash|backup|repo|github|git[_-]"),
 ("Research & analysis (papers, proofs, experiments, models)",
  r"paper|proof|theorem|manuscript|dissertation|experiment|dataset|model|training|benchmark|result|measurement|analysis|study|lemma|conjecture|operator|kernel"),
 ("Audit & agent programs (builders, audit cycles, swarms)",
  r"audit|builder|cycle\d|swarm|handoff|session_|review|referee|verification|verify|finding"),
 ("Partner & feedback (how to work with the person this brain serves)",
  r"user_|feedback_|owner|partner|spelling|voice|writing|preference|discipline|rule"),
]


def _load_topics():
    """Replace the defaults from BRAIN_TOPICS_FILE when a deployment supplies one."""
    path = os.environ.get("BRAIN_TOPICS_FILE", "")
    if not path or not os.path.isfile(path):
        return DEFAULT_TOPICS
    try:
        import json
        rows = json.load(io.open(path, encoding="utf-8"))
        out = [(str(r["label"]), str(r["pattern"])) for r in rows]
        for _label, pat in out:
            re.compile(pat)      # a bad pattern is a configuration error, not a silent skip
        return out or DEFAULT_TOPICS
    except Exception as exc:
        sys.stderr.write("brain topics file unusable (%s); using defaults%s" % (exc, os.linesep))
        return DEFAULT_TOPICS


TOPICS = _load_topics()

def topic_of(name, desc):
    hay = (name + " " + desc).lower()
    for label, rx in TOPICS:
        if re.search(rx, hay):
            return label
    return "Other"

def clip_desc(s, n=110):
    s = (s or "").replace("\n", " ").strip()
    return s if len(s) <= n else s[:n-1] + "…"

def read(p):
    try:
        with io.open(p, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except Exception:
        return ""

mems = []   # (fname, name, desc, mtype, date, links, words)
for fn in sorted(os.listdir(LIVE)):
    if not fn.endswith(".md") or fn in ("MEMORY.md", "INDEX_ARCHIVE.md"):
        continue
    text = read(os.path.join(LIVE, fn))
    name = (FM_NAME.search(text) or [None]) and (FM_NAME.search(text).group(1).strip() if FM_NAME.search(text) else fn[:-3])
    desc = FM_DESC.search(text).group(1).strip() if FM_DESC.search(text) else ""
    mtyp = FM_TYPE.search(text).group(1) if FM_TYPE.search(text) else None
    if not mtyp:  # infer from filename prefix (old memories predate the type: convention)
        low = fn.lower()
        for pre, ty in (("feedback_","feedback"),("project_","project"),("reference_","reference"),
                        ("user_","user"),("audit_","audit"),("audit","audit"),("crash_","project"),
                        ("session_","transient")):
            if low.startswith(pre): mtyp = ty; break
        mtyp = mtyp or "unfiled"
    # the LAST date in a file name is when the memory was written; an earlier one is its subject
    # (job_2025_03_31_..._2026_08_05 filed under 2025-03). A description date is only a fallback and
    # never one past this month (a future date in one description put a memory under 2027-12).
    fm = list(DATE_F.finditer(fn))
    m = fm[-1] if fm else DATE_F.search(desc)
    if m and not fm and m.group(1) + "-" + m.group(2) > _THIS_MONTH:
        m = None
    date = (m.group(1) + "-" + m.group(2)) if m else "undated"
    links = [l.strip() for l in WIKI.findall(text)]
    mems.append((fn, name, desc, mtyp, date, links, len(text.split())))

names = {m[1]: m[0] for m in mems}
fnames = {m[0] for m in mems}

# canonical link resolution — memories are dated files but [[links]] often drop the
# date or swap -/_ ; resolve before calling a link "dangling" (matches dream.py).
_DATE_TAIL = re.compile(r"[_-]?20\d\d[_-]\d\d(?:[_-]\d\d)?$")
def _norm(s):
    return _DATE_TAIL.sub("", s.strip().lower().rstrip(".md")).replace("-", "_")
_canon = {}
for m in mems:
    _canon.setdefault(_norm(m[0][:-3]), m[0])
def resolve_link(target):
    t = target.strip()
    if t in names:        return names[t]         # name: field match
    if t + ".md" in fnames: return t + ".md"      # exact filename
    return _canon.get(_norm(t))                    # date/sep-insensitive

# ---------- STATS ----------
by_type = Counter(m[3] for m in mems)
total_words = sum(m[6] for m in mems)
with _out("STATS.md") as f:
    f.write("# Brain statistics\n\n")
    f.write(f"- live memories: **{len(mems)}** ({total_words:,} words)\n")
    if n_archived_transient:
        f.write(f"- archived transient scratch: {n_archived_transient} (in `transient/`, excluded from views)\n")
    f.write(f"- by type: " + ", ".join(f"{k}={v}" for k, v in by_type.most_common()) + "\n")
    dated = [m for m in mems if m[4] != 'undated']
    if dated:
        f.write(f"- date span: {min(m[4] for m in dated)} .. {max(m[4] for m in dated)}\n")
    f.write(f"- wiki-links: {sum(len(m[5]) for m in mems)} across {sum(1 for m in mems if m[5])} linking memories\n")

# ---------- TOPICS ----------
# any stray transient still at top level is collapsed; the bulk lives in transient/.
durable = [m for m in mems if m[3] != "transient"]
n_transient = len(mems) - len(durable)
groups = defaultdict(list)
# Communities of the association graph (build_communities.py in the dreaming folder) group the memories when that
# map is under 3 days old: on 30 Oct they put a session's next read in the cluster of the one before 5.4x as often as
# chance, against 1.6x for the keyword buckets below. Memories not in the graph keep their keyword topic.
_comm = {}
_comm_path = os.environ.get("BRAIN_COMMUNITIES_TSV") or os.path.expanduser(
    os.path.join("~", ".claude", "dreaming", "communities.tsv"))
try:
    import time as _t
    if _t.time() - os.path.getmtime(_comm_path) < 3 * 86400:
        with io.open(_comm_path, encoding="utf-8", errors="replace") as _fh:
            next(_fh, None)
            for _ln in _fh:
                _p = _ln.rstrip("\n").split("\t")
                if len(_p) >= 3:
                    _comm[_p[0]] = (int(_p[1]), _p[2])
except (OSError, ValueError):
    _comm = {}
for m in durable:
    c = _comm.get(m[0][:-3])
    groups[c[1] if c else topic_of(m[0] + " " + m[1], m[2])].append(m)
with _out("TOPICS.md") as f:
    _arch = n_archived_transient + n_transient
    f.write(f"# The brain by topic\n\n{len(durable)} durable memories, grouped (newest "
            f"first). {_arch} transient agent-scratch checkpoints are archived in "
            f"`claude/memory/transient/` (never deleted) and excluded from this view.\n")
    if _comm:
        f.write("\nGroups are communities of the association graph (links and co-reads), named by their most "
                "typical words and best-connected memory; memories outside the graph keep a keyword topic.\n")
    # equal-sized groups are ordered by name: a set alone came out in hash order, so two builds differed (1 Nov 2026)
    _labels = sorted({v[1] for v in _comm.values()}, key=lambda l: (-len(groups.get(l, ())), l))
    order = [l for l in _labels if not l.startswith("small communities")] + \
            [l for l in _labels if l.startswith("small communities")] + [t[0] for t in TOPICS] + ["Other"]
    for g in order:
        if g not in groups: continue
        rows = sorted(groups[g], key=lambda m: m[4], reverse=True)
        f.write(f"\n## {g}  ({len(rows)})\n\n")
        for fn, name, desc, mtyp, date, links, w in rows:
            d = desc if len(desc) <= 160 else desc[:157] + "..."
            f.write(f"- `{date}` [{fn[:-3]}](../claude/memory/{fn}) — {d}\n")

# ---------- TIMELINE ----------
by_month = defaultdict(list)
for m in mems:
    by_month[m[4]].append(m)
with _out("TIMELINE.md") as f:
    f.write("# The brain as a timeline\n\nEpisodic axis: what was learned when (by filename/description date).\n")
    for month in sorted(by_month, reverse=True):
        rows = sorted(by_month[month], key=lambda m: m[0], reverse=True)
        f.write(f"\n## {month}  ({len(rows)})\n\n")
        for fn, name, desc, mtyp, date, links, w in rows:
            d = desc if len(desc) <= 140 else desc[:137] + "..."
            f.write(f"- [{fn[:-3]}](../claude/memory/{fn}) — {d}\n")

# ---------- GRAPH ----------
backlinks = defaultdict(list)
broken = []
for fn, name, desc, mtyp, date, links, w in mems:
    for l in links:
        tgt = resolve_link(l)
        if tgt:
            backlinks[tgt].append(fn)
        else:
            broken.append((fn, l))
linked = set(backlinks) | {m[0] for m in mems if m[5]}
orphans = [m for m in mems if m[0] not in linked]
hubs = Counter({k: len(v) for k, v in backlinks.items()})
with _out("GRAPH.md") as f:
    f.write("# The brain's link graph\n\n")
    f.write(f"{sum(len(v) for v in backlinks.values())} resolved links, {len(broken)} dangling (fine — they mark future memories), {len(orphans)} orphan memories.\n")
    f.write("\n## Hubs (most linked-to)\n\n")
    f.write("Degree, then the node's usage size — connectivity and use are different axes and a "
            "big hub is often a small node.\n\n")
    for tgt, n in hubs.most_common(30):
        f.write(f"- **{n}** ← [{tgt[:-3]}](../claude/memory/{tgt})  `size {node_size(tgt[:-3]):.1f}`\n")

    # ---------- node size by usage ----------
    f.write("\n## Node size (how much each memory is used)\n\n")
    if not USE:
        f.write(f"No usage signal on disk (`~/.claude/dreaming/base_activation.tsv` absent), so "
                f"every node is {NODE_SIZE_MIN:g} — a uniform map is the honest picture of no usage.\n")
    else:
        sized = sorted(((node_size(m[0][:-3]), m[0]) for m in mems), reverse=True)
        at_floor = sum(1 for s, _ in sized if s <= NODE_SIZE_MIN + 1e-9)
        f.write(f"{len(USE)} of {len(mems)} memories have been read at least once since the "
                f"retrieval trace begins; the other {len(mems)-len(USE)} sit at the floor. "
                f"Size runs {NODE_SIZE_MIN:g}–{NODE_SIZE_MAX:g} as "
                f"`{NODE_SIZE_MIN:g} + sqrt(use/use_max) * {NODE_SIZE_MAX-NODE_SIZE_MIN:g}`, "
                f"where `use` is ACT-R base-level activation normalized across the corpus — the "
                f"same number recall's usage tilt ranks with, so this is a picture of what the "
                f"ranker sees. Recency counts: a memory read hard last month has already shrunk. "
                f"One artefact worth knowing: activation weights a read by `(age)^-0.5`, so the "
                f"memory read *last* before the table was built can top this list on a single "
                f"read.\n\n")
        for size, fn in sized[:40]:
            slug = fn[:-3]
            f.write(f"- `{size:5.1f}` {size_bar(size)} [{slug}](../claude/memory/{fn}) — "
                    f"{ACT_N.get(slug, 0)} reads, B={ACT.get(slug, float('-inf')):.2f}\n")
        f.write(f"\n{at_floor} memories are at the floor ({NODE_SIZE_MIN:g}).\n")

    f.write("\n## Backlinks (who points at whom)\n\n")
    for tgt in sorted(backlinks):
        srcs = ", ".join(f"[{s[:-3]}](../claude/memory/{s})" for s in sorted(set(backlinks[tgt])))
        f.write(f"- [{tgt[:-3]}](../claude/memory/{tgt}) ← {srcs}\n")
    f.write("\n## Dangling links (target memory not written yet)\n\n")
    for src, l in sorted(set(broken)):
        f.write(f"- `[[{l}]]` wanted by [{src[:-3]}](../claude/memory/{src})\n")

# ---------- BRAIN_INDEX ----------
def count_md(p):
    try:
        return sum(1 for x in os.listdir(os.path.join(CONS, p)) if x.endswith(".md"))
    except Exception:
        return 0
with _out("BRAIN_INDEX.md") as f:
    f.write("""# BRAIN INDEX — every memory store in this repo

The brain has strata. Newest and authoritative first.

| Store | Where | What |
|---|---|---|
""")
    f.write(f"| **Live brain** ({len(mems)} memories) | `claude/memory/` | The current, authoritative memory corpus + `MEMORY.md` index |\n")
    if n_archived_transient:
        f.write(f"| Transient scratch ({n_archived_transient}) | `claude/memory/transient/` | Archived agent audit-loop checkpoints — real history, never deleted, kept out of the recall corpus |\n")
    proj = os.path.join(CONS, "claude", "project_memories")
    if os.path.isdir(proj):
        for d in sorted(os.listdir(proj)):
            n = sum(1 for x in os.listdir(os.path.join(proj, d)) if x.endswith(".md"))
            f.write(f"| Project brain ({n}) | `claude/project_memories/{d}/` | Per-project memories |\n")
    f.write(f"| Codex brain ({count_md('codex/memories')}) | `codex/memories/` | Codex agent's memories |\n")
    hist = os.path.join(CONS, "historical_memories")
    if os.path.isdir(hist):
        for d in sorted(os.listdir(hist)):
            p = os.path.join(hist, d)
            if os.path.isdir(p):
                n = sum(1 for x in os.listdir(p) if x.endswith(".md"))
                f.write(f"| Historical stratum ({n}) | `historical_memories/{d}/` | see historical_memories/README.md |\n")
    f.write("""
## Navigation layers (generated — rebuild with build_brain_architecture.py)
- [TOPICS.md](TOPICS.md) — the brain grouped by subject
- [TIMELINE.md](TIMELINE.md) — the brain month by month
- [GRAPH.md](GRAPH.md) — hand-written `[[link]]` graph: hubs, backlinks, dangling links
- [RELATED.md](RELATED.md) — associative graph (auto-linked by shared terms; recall by association)
- [SEARCH.md](SEARCH.md) — keyword → memories inverted index
- [STATS.md](STATS.md) — corpus statistics

## Codex brain extracts (recovered from binary sqlite)
- `../codex/extracted/codex_goals_log.md` — 87 agent objectives
- `../codex/extracted/codex_session_catalog.md` (+`.csv`) — 7077-session episodic index
- `../codex/extracted/codex_agent_spawn_graph.md` — the agent family tree

## Cognitive architecture (how the brain becomes a mind)
- `../../12_cognitive_architecture/COGNITIVE_ARCHITECTURE.md` — identity, relationship,
  voice, creativity, continuity, and the auto-consult startup loop
- Live memories: identity_who_you_are, relationship_our_work_together,
  method_write_like_a_human, method_think_creatively, startup_consult_the_brain

## Raw substrate
- `10_ai_interaction_history/` — every prompt ever typed (claude + codex)
- `11_raw_session_transcripts/` — every full conversation (the brain's episodic record)
""")

# ---------- SEARCH (inverted index over name + description) ----------
STOP = set("the a an and or of to in for on with is are was were be been being this that these those "
           "it its as at by from into over per not no yes new old one two use used using via off out up "
           "vs etc via when then than but our we you your my me i him her his their they them so if all any "
           "each both more most some such only same then once now day days week month year 2026 2027".split())
inv = defaultdict(set)
for fn, name, desc, mtyp, date, links, w in mems:
    toks = re.findall(r"[A-Za-z][A-Za-z0-9_]{2,}", (name + " " + desc).lower())
    for t in set(toks):
        if t in STOP or len(t) < 3: continue
        inv[t].add(fn)
# keep distinctive terms: appear in >=2 and <=12% of memories
cap = max(3, int(0.12 * len(mems)))
terms = {t: fs for t, fs in inv.items() if 2 <= len(fs) <= cap}
# each term shows its 20 most specific memories (fewest distinctive terms), not the first 20 by name, and a memory
# still on no list joins the list of its rarest term: the alphabetical cut left 3,089 of 28,710 memories unlisted
# (31 Oct 2026); this pick lists every memory that has a description
nterm = defaultdict(int)
for fs in terms.values():
    for x in fs: nterm[x] += 1
shown = {t: set(sorted(fs, key=lambda x: (nterm[x], x))[:20]) for t, fs in terms.items()}
listed = set().union(*shown.values()) if shown else set()
rarest = {}
for t, fs in terms.items():
    for x in fs:
        if x not in listed and (x not in rarest or (len(fs), t) < rarest[x]):
            rarest[x] = (len(fs), t)
for x, (_, t) in rarest.items():
    shown[t].add(x)
with _out("SEARCH.md") as f:
    f.write("# Keyword search index\n\n")
    f.write(f"Inverted index over memory names + descriptions ({len(terms)} distinctive terms). "
            "Ctrl-F a term to jump to the memories that mention it; a long term lists its most specific ones.\n\n")
    for t in sorted(terms):
        fs = terms[t]
        links_md = ", ".join(f"[{x[:-3]}](../claude/memory/{x})" for x in sorted(shown[t]))
        more = f" +{len(fs)-len(shown[t])}" if len(fs) > len(shown[t]) else ""
        f.write(f"- **{t}** ({len(fs)}): {links_md}{more}\n")

# ---------- RELATED (associative graph via shared distinctive terms) ----------
# term set per memory (distinctive terms only), then candidate scoring through the inverted index
memterms = defaultdict(set)
for t, fs in terms.items():
    for fn in fs:
        memterms[fn].add(t)
fn_meta = {m[0]: m for m in mems}

def current_fname(fn):
    slug = fn[:-3] if fn.endswith(".md") else fn
    current = SUPERSEDED.get(slug, slug) + ".md"
    return current if current in fn_meta else fn

# Each term's members under their current names, made once: related() used to rebuild this set for every memory
# that holds the term, which with ~29k memories was most of an 85-minute view build (1 Nov 2026). raw counts the
# terms a memory shares with fn under its own file name, so |mine | memterms[other]| = |mine| + |theirs| - raw[other]
# without building the union. Same counts and scores as before; nlargest returns sorted(..., reverse=True)[:k].
import heapq
cur_terms = {t: {current_fname(candidate) for candidate in fs} for t, fs in terms.items()}

def related(fn, k=6):
    if current_fname(fn) != fn:
        return []
    mine = memterms.get(fn, set())
    myslug = fn[:-3] if fn.endswith(".md") else fn
    coact = COACT.get(myslug, {})
    cand = Counter()
    raw = Counter()
    for t in mine:
        cand.update(cur_terms[t])
        raw.update(terms[t])
    cand.pop(fn, None)
    # Hebbian: memories read together with this one are neighbours even with no shared vocabulary
    for oslug in coact:
        ofn = current_fname(oslug + ".md")
        if ofn != fn and ofn in fn_meta:
            cand.setdefault(ofn, 0)
    if not cand:
        return []
    scored = []
    nmine = len(mine)
    for other, shared in cand.items():
        union = (nmine + len(memterms.get(other, ())) - raw.get(other, 0)) or 1
        _sc = shared / union
        _cw = coact.get(other[:-3] if other.endswith(".md") else other, 0)
        if _cw:
            _sc += 0.04 * min(_cw, 8)   # read together across sessions -> a real, use-shaped link
        if other in UNCONSCIOUS:
            _sc *= 0.25   # latent substrate: still a neighbour, never a leading one
        scored.append((_sc, shared, other))
    return heapq.nlargest(k, scored)
with _out("RELATED.md") as f:
    f.write("# Associative map (auto-linked)\n\n")
    f.write("For each memory, the most similar other memories by shared distinctive terms "
            "(Jaccard over the search vocabulary). This is the brain's *implicit* graph — "
            "recall by association, beyond the hand-written `[[links]]`.\n")
    nowired = 0
    for m in sorted(mems, key=lambda m: m[4], reverse=True):
        rel = related(m[0])
        if not rel:
            nowired += 1
            continue
        f.write(f"\n**[{m[0][:-3]}](../claude/memory/{m[0]})**\n")
        for score, shared, other in rel:
            om = fn_meta.get(other)
            od = clip_desc(om[2]) if om else ""
            f.write(f"- ({score:.2f}) [{other[:-3]}](../claude/memory/{other}) — {od}\n")
    f.write(f"\n---\n{len(mems)-nowired} memories wired into the associative graph; {nowired} isolated (no distinctive terms).\n")

print(f"brain built: {len(mems)} memories, {len(groups)} topics, {len(backlinks)} linked targets, {len(orphans)} orphans, {len(terms)} search terms, RELATED wired {len(mems)-nowired}")
