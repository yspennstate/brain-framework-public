#!/usr/bin/env python3
"""spread_recall.py - spreading-activation recall: the unprompted "this reminds me...".

The lexical hooks (fok_probe, instinct_prime) answer "do you hold a memory whose WORDS match this?"
A person's recall does more: a task lights up a memory, and that memory lights up a third one that
shares no vocabulary with the task at all - the distant, structural association you'd never grep for.
That is spreading activation (Collins & Loftus 1975; Anderson ACT-R): activation flows from what's
touched, along the association graph, and pools somewhere non-obvious.

This runs on UserPromptSubmit and on reading a memory. It:
  1. SEEDS the graph - the memories the prompt's words touch (or the memory just read).
  2. SPREADS activation two hops along spread_edges.tsv (Hebbian co-reads + wiki-links, built by
     build_spread_edges.py), normalized by fan-out so a hub doesn't flood the walk.
  3. Surfaces the top pooled node ONLY IF it is non-obvious - not a seed, and NOT itself a lexical
     match of the prompt (else it's "you already see that," not "this reminds me"), and worth the
     interruption (salience floor, clear margin over noise).

That non-obvious filter is the whole point and the whole restraint: it speaks exactly when it has
the association the other hooks structurally cannot reach, and is silent otherwise. Deduped per
session so a train of prompts on one topic surfaces the bridge once, not every turn.

Fail-safe silent on any error. Reads stdin (hook JSON), writes injection JSON to stdout.
"""
import sys, os, io, json, re, time, bisect, threading

# 5 Nov 2026 (claude-95df24fa): a resident spread_server.py keeps this hook's loaded state warm (median 415 -> 50 ms
# in the memo harness, output byte-identical 8/8). Ask it first, before the heavy imports below; on None (no server,
# busy, declined, any error) the hook runs cold exactly as before. SPREAD_SERVER=0 turns it off.
if __name__ == "__main__" and os.environ.get("SPREAD_SERVER", "1") != "0" and not os.environ.get("SPREAD_SERVER_CHILD"):
    _raw = sys.stdin.read()
    sys.stdin = io.StringIO(_raw)
    _served = None
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import spread_client as _spread_client
        _served = _spread_client.serve(_raw)
    except Exception:
        _served = None
    if _served is not None:
        sys.stdout.write(_served)
        raise SystemExit(0)

from collections import defaultdict

import keyword_artifacts
import brain_node_documents as node_docs
from supersession import active_supersessions


def _brain_slug_resolve():
    """The memory corpus this install belongs to.

    Order matters. $BRAIN_SLUG wins so a caller can point one script at another corpus.
    Then BRAIN_SLUG beside this file, which bootstrap/install.py writes with the slug of the
    --project-dir it was given; without it an install whose project directory is not the home
    directory builds a corpus nothing ever reads. The home-derived value stays last so a tree
    that works today keeps working untouched.
    """
    env = os.environ.get("BRAIN_SLUG", "").strip()
    if env:
        return env
    try:
        marker = os.path.join(os.path.dirname(os.path.abspath(__file__)), "BRAIN_SLUG")
        with open(marker, encoding="utf-8") as fh:
            named = fh.read().strip()
        if named:
            return named
    except OSError:
        pass
    home = os.path.expanduser("~")
    return home.replace(":", "-").replace(chr(92), "-").replace("/", "-")


try:
    import embedding_artifact
except Exception:
    embedding_artifact = None

try:
    import _usersite  # noqa: F401 - re-adds Roaming user-site under `python -s` (numpy lives there;
    #                   without this the semantic re-rank would degrade SILENTLY under the fast flag)
except Exception:
    pass

HOME = os.path.expanduser("~")
# Claude Code names a project directory after the home path, with ':' and the
# separators replaced by '-'. Deriving it keeps this tree free of one machine's
# username and correct on any other - and unlike a template token it is a real
# value at import time.
_BRAIN_SLUG = _brain_slug_resolve()
MEM = os.path.join(HOME, ".claude", "projects", _BRAIN_SLUG, "memory")
DR = os.path.dirname(os.path.abspath(__file__))
EDGES = os.path.join(DR, "spread_edges.tsv")
# Links of memories written since the nightly build (build_spread_edges.py --fresh); merged while newer.
EDGES_FRESH = os.path.join(DR, "spread_edges_fresh.tsv")
GRAPH_CACHE = os.path.join(DR, ".spread_edges.parsed.pkl")   # _load_graph's parse, keyed on EDGES mtime+size
HOOK_GRAPH_CACHE = os.path.join(DR, ".spread_edges.norec.pkl")  # _hook_graph's filtered parse, same key + pattern
SAL = os.path.join(DR, "salience.tsv")
STATE = os.path.join(DR, ".spread_fired.json")
EMB = os.path.join(DR, "memory_embeddings.npz")   # optional semantic sidecar (build_memory_embeddings.py, box)
EMB_AUTHORITY = os.path.join(DR, ".embedding_current.json")
EXPECTED_EMB_MODEL = "BAAI/bge-small-en-v1.5"
MW = os.path.join(DR, "mistake_weights.tsv")      # slug -> severity (build_mistake_weights.py)
KW = os.path.join(DR, "keyword_nodes.tsv")        # owner topic nodes (build_keyword_nodes.py)
KWG = os.path.join(DR, "keyword_graph_edges.tsv") # the full topic graph (offline/analysis view)
KWREL = os.path.join(DR, "keyword_related.tsv")   # subject-to-subject only; the live path reads this

STOP = set(("the a an and or of to in for on with is are was were be this that it its as at by from into "
            "over per not no new one two use used using via etc when then than but our we you your i me my "
            "he she they them their what which who how why can do does did has have had will would could "
            "should more most some any each all only just get got make made see saw look need want know "
            "now today about into onto out up down let me is it do keep working").split())
DECAY = 0.5          # activation halves each hop out from the seeds
HOPS = 2
# what makes an association worth interrupting for is that ONE node clearly stands out, not that its
# absolute activation clears some level (that level differs between a one-seed Read and a many-seed
# prompt). So gate on margin over the runner-up + a low signal floor + a salience floor.
ACT_FLOOR = 0.16     # the winner must carry real graph signal (not a lone whisper off one weak edge)
MARGIN = 1.175       # ...and beat the runner-up. Was 1.35 since 2026-07-08; halved-excess on
                     # 2026-07-25 by owner directive ("strengthen connections by 2x so that
                     # things trigger faster"): (1.35-1)/2 = 0.175 over parity. This knowingly
                     # supersedes the 07-09 shadow-eval verdict against a margin retune - that
                     # verdict optimized precision under the old objective; the owner set a new
                     # one. Same-inputs receipt at the CONNECTION_GAIN note below.
SAL_FLOOR = 45.0     # ...and be worth the look
CAND_FLOOR = 0.10    # keep anything with a pulse as a candidate; the gate above decides what speaks
REDUNDANCY_MAX = 0.34  # drop a candidate whose description overlaps a lexical hit's this much (redundant)
# PRECISION TUNING - what's tried (2026-07-08), and the meta-lesson. Two blind judges scored the fires
# 2/4 then 3/7 (~45%) and named the dominant miss mode: attractor-pull toward high-connectivity "gist"
# hubs and same-session co-read siblings. Two graph-signal fixes were attempted:
#   - degree/specificity penalty on hubs: REJECTED, measurement made precision WORSE (43%->33%) - a
#     substantive hub and a tangential one are identical by degree; it's a content call, not topology.
#   - single-edge fragility gate (a candidate whose activation rests on one edge from one seed - likely
#     a co-read artifact): a sound corroboration idea, but NOT SHIPPED because it can't be validated.
# The meta-lesson (the real reason to stop here): the association graph is NON-STATIONARY - every
# session's co-reads and every dream rebuild change it, so the same prompt surfaces different candidates
# week to week. A tune "verified" against a 7-case snapshot is verified against a graph that no longer
# exists (caught live: the dream ran mid-test and the fires shifted). So the honest read stands: ~45%
# precise on a young graph, useful when it lands, silent on ~55% of prompts - and lifting the ceiling
# needs a STABLE held-out eval plus body/embedding relevance, not another cheap graph signal fit to a
# moving target. Left here so the next session neither re-runs the dead ends nor trusts a snapshot tune.
DEDUP_SECS = 43200   # was 600; 141 of 198 Mac spread lines were repeats and none earned a use (30 Oct)
# PER-SESSION RATE CAP (2026-09-22, F37 of the recall study). The per-slug dedup above stops one memory
# repeating and does nothing about the total: over 30 days 4.7 pct of sessions took 65 pct of every
# association delivered (one session took 195). Measured on the layer's own log (surfaced.tsv, 6,535 spread
# rows / 1,169 sessions in 30 days): the flood sessions run a median 27 h and fire every ~12 min. Rule: the
# first SESSION_FREE_FIRES associations fire on the per-slug dedup alone; after that the layer speaks again
# only once SESSION_GAPS seconds have passed since its last association - 10 min up to the 12th, 30 min up
# to the 20th, 2 h beyond. Replayed on that log the rule delivers 55 pct of the rows, leaves 91 pct of the
# sessions untouched and turns every >50 session into a trickle. A held-back fire is written to
# spread_capped.tsv, never to surfaced.tsv (which must stay the record of what was DELIVERED, or the next
# measurement reads the instrument). The premise-check path (superseded memory just read) is NOT capped.
SESSION_FREE_FIRES = 6
SESSION_GAPS = ((12, 600), (20, 1800), (None, 7200))
HOOK_ATTACHMENT_JSON_CHARS = 7000  # leave room under Claude's measured 10,000-char hook cap
SKIP = {"MEMORY", "INDEX_ARCHIVE", "PREDICTIONS", "STATE_OF_MIND", "OPEN_QUESTIONS",
        "AUTOBIOGRAPHY", "MODEL_OF_PARTNER", "FASCINATIONS", "THE_OTHER_MINDS", "MISTAKES"}
# SESSION RECORDS (2026-10-13). compact_nodes.py files one node per compaction, a summary of what a session
# was doing, into the memory folder: 1,451 by 13 Oct, 299 of them written on 12 Oct and 363 on 13 Oct. They
# are records, not lessons, and they had taken over every channel of this hook. On 148 replayed prompts they
# held 485 of the 740 BM25 seed slots; every one carries the same template link to the compaction hub (1,404
# of the hub's 1,411 edges) and a link to its session's previous node, so a seeded record passed its charge
# to the hub and the hub won 26/60, 40/60 and 17/29 fires on three prompt sets; a fresh record's salience
# (median 63, top tenth 99) also outranked the memories under a subject for the topic slots. The regression
# gate's mistake-class rate, 8-20% from 3 to 11 Oct, read 5% on 12 Oct and 0% twice on 13 Oct. So the hook
# does not see them: no seed, no edge, no topic slot, no fire. brain.py and the eval tools still do
# (_load_graph and the default _seeds_from_prompt are unchanged).
SESSION_RECORD = re.compile(r"^(?:manual_)?compaction_(?:claude|codex)_")
BM25_RECORD_POOL = 40   # BM25 depth the hook reads so that five non-record hits remain after the filter
# MISTAKE-FIRST SURFACING (2026-07-25, owner ask: nodes connected to mistakes - especially big
# ones - must surface FASTER; they were near-silent). Measured before the change: the July-24
# audit wave = 145 graph-leaf nodes (~1.2 spread_edges each), zero compiled reflexes, 2 lifetime
# spread surfacings, salience 99.5+ - so the walk and gates below, not salience, were the
# bottleneck. Severity comes from mistake_weights.tsv (offline builder; fail-open: absent file
# -> all zeros -> byte-identical prior behaviour). Three graded levers, none touching
# non-mistake nodes:
#   inflow boost   flow into a mistake node x(1 + MW_BOOST*sev) - a scar-class leaf can clear
#                  ACT_FLOOR from its single inbound edge instead of never
#   margin relief  a sev>=MW_SEV_BIG standout needs MW_MARGIN_BIG not MARGIN - the exact 1.15
#                  band the shadow-fires counterfactual log has tracked since 07-09
#   habit floor    cross-session habituation never damps a big scar below MW_HABIT_FLOOR
#                  (damping, never deletion - the reflex-ranking house rule)
MW_BOOST = 0.40
MW_SEV_BIG = 0.8
MW_MARGIN_BIG = 1.075   # big-scar standout margin; excess halved with MARGIN on 2026-07-25
MW_HABIT_FLOOR = 0.9
MW_SCORE_TILT = 0.10   # score x(1 + tilt*sev): a big scar wins ties, never floods
# CONNECTION STRENGTH x2 (25 Jul 2026: connections strengthened 2x so that
# things trigger faster). Two candidate mechanisms, both MEASURED on the 177-prompt replay:
#   (1) walk-conductance x2 (flow *= 2 per hop): REJECTED - it densifies the candidate pool
#       (more runners-up clear CAND_FLOOR), the standout margin tightens, and baseline fires
#       DROPPED 74 -> 65. Stronger connections, slower triggering - the opposite of the ask.
#       (Doubling stored edge weights would cancel entirely: the walk is degree-normalized.)
#   (2) trigger-threshold /2 (a fire needs half the arriving activation): measured a same-
#       inputs DELTA OF ZERO (120 vs 120 fires over 177 prompts) - ACT_FLOOR does not bind
#       at this corpus. Kept as the owner's 2x knob for the day floors bind again.
#   (3) standout-margin excess halved (MARGIN 1.35 -> 1.175, MW_MARGIN_BIG 1.15 -> 1.075):
#       the margin IS the binding gate. Same-inputs receipt over 177 real prompts:
#       120 -> 138 fires (+18, 11 of them mistake-class, 0 lost, 9 swaps that eyeball
#       neutral-or-better). Combined with mistake-first surfacing, the session's total vs the
#       morning baseline on the same prompts: 68 -> 138 fires = 2.03x triggering. SHIPPED.
CONNECTION_GAIN = 2.0   # divides ACT_FLOOR at the speak gate; 1.0 restores pre-07-25 behaviour
# OWNER TOPIC NODES x3 (owner, 2026-07-25: the words he uses - "microsoft volume", "aws",
# "levrage" - are nodes, and when he is on one of them the mistakes and information under it
# should surface ~3x more). keyword_nodes.tsv maps his vocabulary to member memories and maps
# his misspellings to the corpus spelling (levrage -> leverage), so the boost survives his
# typing. The gain is differential, never global: only members of a topic the CURRENT text
# names are lifted, which is the shape that worked for mistake severity and the opposite of
# the uniform walk gain that measured WORSE. A broad topic (grid: 372 members) is capped to
# its most salient TOPIC_TOP_K, so naming a big subject cannot light the whole corpus flat.
KEYWORD_GAIN = 3.0        # a MISTAKE inside a named topic
KEYWORD_INFO_GAIN = 1.7   # ordinary information inside a named topic
TOPIC_TOP_K = 40
TOPIC_MAX_UNION = 120   # ceiling on the seed union across every subject a text names
TOPIC_MAX_HITS = 6      # a prompt can name many ordinary words that are also topics
TOPIC_MAX_RELATED = 4   # neighbouring subjects are context, never an unbounded second query
TOPIC_OVERLAY_MAX_EDGES = 120  # topic->member edges; adjacency entries are at most 2x this
# Why the topic lift is not flat, measured: a flat 3x over all 40 topic nodes lifted the topic's
# ORDINARY bulk alongside its scars, and the scars lost the competition - mistake-class fires
# fell 118 -> 104 on 193 real prompts while total fires barely moved. His ask was that mistakes
# AND information surface more, so the gain is graded: full 3x for the topic's mistakes, a real
# but smaller lift for its ordinary members. Scars keep their edge inside the topic they belong to.
# TUNING RECEIPT (eval_mistake_surfacing.py, 176 real prompts, 2026-07-25). Journey: at
# 0.75/0.25 single-winner, the change over-rotated - fire rate x1.53, ordinary fires 24->7,
# ~10 good fires SILENCED (a boosted scar seized top-1 then died at a gate; one silenced case
# was the owner-law memory an_owner_law_memory). 0.40/0.10 + the two-arm
# arbitration below (baseline is a FLOOR; boost may add or swap, never silence; additions
# must be sidecar-inspectable and at least corpus-typically similar) landed at: fires 62->97
# (all 35 changes pure additions, 0 silenced, 0 swapped), sev>=0.8 mistake fires 34->65,
# additions ~2/3 clearly relevant on eyeball with the rest tangential-cheap. Live subprocess
# smoke: fires end-to-end, 123ms. Do NOT stack more per-case rules on this snapshot - the
# graph is non-stationary (the 07-08 meta-lesson above). POST-SIDECAR RE-RUN (same day, after
# coverage went 2722/2722): the vector guard opened for the July-24 wave and additions grew -
# fires OFF 74 -> ON 132 of 177, sev>=0.8 mistake fires 53 -> 108, still 0 silenced / 1 swap.
# That is a 75% fire rate; whether it over-serves is now a LIVE question for the judged
# instruments (surfaced.tsv carries the severity column for exactly this), not for another
# snapshot tune. Next retune happens on judged real-session n, per the house rule.


def _words(s):
    return set(w for w in re.findall(r"[a-zA-Z][a-zA-Z_]{2,}", (s or "").lower()) if w not in STOP)


def _distinctive_words(words, limit=12):
    """Longest cue words with a process-stable lexical tie-break."""
    return set(sorted(set(words), key=lambda w: (-len(w), w))[:limit])


def _load_superseded():
    """slug -> its correction. A superseded memory must never be surfaced when its correction is also
    in play (the Supersede/MemConflict stale-fact fix; eval_stale_fact shows relevance is stale-blind).
    Built by schema_supersede.py; absent file -> empty map -> behaviour unchanged.
    In the hook the names come from the one shared folder read (see _mem_names), not from supersession's own
    scandir (0.94 s on 53k files): this call takes no frontmatter, so only the set of .md file names is used, and
    the shared read's names equal scandir's (53,098, same set). SPREAD_SHARED_LIST=0 lists as before."""
    known = None
    if _SHARE_LIST and os.environ.get("SPREAD_SHARED_LIST", "1") != "0":
        if _MEM_STATS is None:
            try:
                _mem_names()
            except Exception:
                pass
        if _MEM_STATS is not None:
            known = {name[:-3] for name in _MEM_STATS}
    return active_supersessions(MEM, index_path=os.path.join(DR, "superseded.tsv"), known_slugs=known)


def _collapse_activations(act, parent, superseded):
    """Route historical nodes to current endpoints without double-counting graph echoes."""
    collapsed = {}
    collapsed_parent = {}
    for slug, activation in act.items():
        current = superseded.get(slug, slug)
        if activation > collapsed.get(current, float("-inf")):
            collapsed[current] = activation
        bridge, flow = parent.get(slug, (None, 0.0))
        if bridge:
            bridge = superseded.get(bridge, bridge)
        if flow > collapsed_parent.get(current, (None, 0.0))[1]:
            collapsed_parent[current] = (bridge, flow)
    return collapsed, collapsed_parent


def _load_habituation():
    """Cross-session habituation. spread_recall dedups within a session, but has no memory that it has
    already surfaced the SAME tangential association to the fleet many times - so one structurally-near
    but rarely-useful memory can dominate (feedback_codex_findings was 34% of all surfacings, never read;
    attribution.py, 2026-07-10). Like neural habituation to a repeated non-salient stimulus, an association
    surfaced >= HABIT_START times recently and NEVER read gets a graded score damp (floored, never zero, so
    it can still fire if it ever truly stands out). A memory that WAS read after surfacing is protected
    (factor 1) - someone found it worth opening. Fail-open: no ledger -> {} -> behaviour unchanged."""
    counts = defaultdict(int)
    read = set()
    now = time.time()
    win = 14 * 86400
    try:
        with io.open(os.path.join(DR, "surfaced.tsv"), encoding="utf-8", errors="replace") as f:
            for ln in f:
                p = ln.rstrip("\n").split("\t")
                if len(p) >= 3 and p[0] != "ts":
                    # count only SPREAD over-surfacing - fok (lexical naming) and premise (correction
                    # pointer) are different mechanisms with different fire paths; habituating a spread
                    # fire on fok counts would conflate them (legacy rows lack a 4th field -> spread).
                    if len(p) >= 4 and p[3] != "spread":
                        continue
                    try:
                        if now - float(p[0]) <= win:
                            counts[p[2]] += 1
                    except Exception:
                        pass
    except Exception:
        return {}
    try:
        with io.open(os.path.join(DR, "retrievals.tsv"), encoding="utf-8", errors="replace") as f:
            next(f, None)
            for ln in f:
                p = ln.rstrip("\n").split("\t")
                if len(p) >= 2:
                    try:
                        if now - float(p[1]) <= win:
                            read.add(p[0])
                    except Exception:
                        pass
    except Exception:
        pass
    HABIT_START = 6
    return {slug: max(0.6, HABIT_START / float(n))
            for slug, n in counts.items() if n >= HABIT_START and slug not in read}


def _edge_paths():
    """spread_edges.tsv, plus spread_edges_fresh.tsv while it is newer than the nightly build."""
    paths = [EDGES]
    try:
        if os.path.getmtime(EDGES_FRESH) > os.path.getmtime(EDGES):
            paths.append(EDGES_FRESH)
    except OSError:
        pass
    return paths


def _edges_key():
    """(mtime_ns, size) of each edge file _load_graph reads; the graph pickles key on it, so a new fresh file
    or a new nightly build both invalidate them."""
    key = ()
    for p in _edge_paths():
        st = os.stat(p)
        key += (st.st_mtime_ns, st.st_size)
    return key


def _load_graph(with_keywords=False, include_base=False):
    """Load the memory graph.

    The long-standing public helper returned ``(adj, deg)`` and brain.py plus five evaluation
    tools still consume that contract. The topic arm needs a third memory-only degree view, so
    internal callers opt into ``include_base=True``. Changing the default tuple shape broke the
    owner's ``brain.py recall`` command outright.
    """
    # Parsing spread_edges.tsv (95k lines) cost 0.74 s of a 4.4 s hook run on 30 Oct; a pickle of the parse loads in
    # about a third of that. Keyed on the file's mtime and size, written atomically, any failure falls back to the parse.
    try:
        key = _edges_key()
    except Exception:
        key = None
    if key is not None:
        try:
            import pickle
            with io.open(GRAPH_CACHE, "rb") as f:
                ck, adj, deg, deg_base = pickle.load(f)
            if ck == key:
                return (adj, deg, deg_base) if include_base else (adj, deg)
        except Exception:
            pass
    adj = defaultdict(list)      # slug -> [(neighbor, weight)]
    deg = defaultdict(float)     # slug -> total incident weight (fan-out normalizer)
    deg_base = defaultdict(float)  # ...the same, counting memory-to-memory edges only
    # keyword_graph_edges.tsv carries his vocabulary as `kw:<term>` NODES: term-to-memory and
    # term-to-term by shared membership, which would make a keyword an object the walk travels
    # THROUGH rather than a lookup beside it. MEASURED AND NOT ENABLED (2026-07-25): merging it
    # gained 11 fires on 196 real prompts but SILENCED 10 good ones - including the owner-law
    # memory an_owner_law_memory - while mistake fires stayed flat (128 -> 130).
    # 87k keyword edges against an 8.2k memory-memory graph is a 10x rewiring, and kw nodes act
    # as high-degree shortcuts that reroute the walk rather than extend it. The artifact is
    # built and regrows nightly; enabling it needs the same no-silencing floor the mistake lane
    # got (baseline choice preserved unless the richer graph only ADDS), which costs a second
    # spread per event and deserves its own measured pass, not a late-night constant tweak.
    # Create graph_fire/KEYWORD_GRAPH_ENABLED to switch it on.
    for path in _edge_paths():
        try:
            with io.open(path, encoding="utf-8", errors="replace") as f:
                next(f, None)
                for ln in f:
                    p = ln.rstrip("\n").split("\t")
                    if len(p) < 3 or p[0] == "a":
                        continue
                    try:
                        w = float(p[2])
                    except Exception:
                        continue
                    a, b = p[0], p[1]
                    adj[a].append((b, w)); adj[b].append((a, w))
                    deg[a] += w; deg[b] += w
                    if not (a.startswith("kw:") or b.startswith("kw:")):
                        deg_base[a] += w; deg_base[b] += w
        except Exception:
            continue
    if key is not None and adj:
        try:
            import pickle
            tmp = "%s.%d.tmp" % (GRAPH_CACHE, os.getpid())
            with io.open(tmp, "wb") as f:
                pickle.dump((key, adj, deg, deg_base), f, protocol=pickle.HIGHEST_PROTOCOL)
            os.replace(tmp, GRAPH_CACHE)
        except Exception:
            try:
                os.remove(tmp)
            except Exception:
                pass
    return (adj, deg, deg_base) if include_base else (adj, deg)


def _without_session_records(adj, deg, deg_base):
    """The hook's graph with every edge that touches a session record removed. New containers for the
    nodes it touches and the inputs' own lists for the rest (callers may cache the parsed graph); a graph
    with no session record in it comes back as the same three objects."""
    records = [node for node in adj if SESSION_RECORD.match(node)]
    if not records:
        return adj, deg, deg_base
    adj2 = defaultdict(list, adj)
    deg2 = defaultdict(float, deg)
    deg_base2 = defaultdict(float, deg_base)

    def _reset(node, kept):
        adj2[node] = kept
        deg2[node] = sum(w for _nb, w in kept)
        deg_base2[node] = 0.0 if node.startswith("kw:") else sum(w for nb, w in kept if not nb.startswith("kw:"))

    touched = set()
    for node in records:
        touched.update(nb for nb, _w in adj[node])
        _reset(node, [])
    for node in touched:
        if not SESSION_RECORD.match(node):
            _reset(node, [(nb, w) for nb, w in adj[node] if not SESSION_RECORD.match(nb)])
    return adj2, deg2, deg_base2


def _hook_graph():
    """_without_session_records(*_load_graph(include_base=True)), with the filtered graph kept in its own pickle.
    The filter cost 0.2-0.27 s a prompt on 1 Nov (5.9k records in a 29k-node graph), more than loading the parse.
    Keyed on EDGES mtime+size and the record pattern; used only while _load_graph is this module's own (a caller
    that swaps it in gets its graph filtered as before); any failure falls back to filtering."""
    if _load_graph is not _LOAD_GRAPH_OWN:
        return _without_session_records(*_load_graph(include_base=True))
    try:
        key = _edges_key() + (SESSION_RECORD.pattern,)
    except Exception:
        key = None
    import pickle
    if key is not None:
        try:
            with io.open(HOOK_GRAPH_CACHE, "rb") as f:
                ck, adj, deg, deg_base = pickle.load(f)
            if ck == key:
                return adj, deg, deg_base
        except Exception:
            pass
    adj, deg, deg_base = _without_session_records(*_load_graph(include_base=True))
    if key is not None and adj:
        tmp = "%s.%d.tmp" % (HOOK_GRAPH_CACHE, os.getpid())
        try:
            with io.open(tmp, "wb") as f:
                pickle.dump((key, adj, deg, deg_base), f, protocol=pickle.HIGHEST_PROTOCOL)
            os.replace(tmp, HOOK_GRAPH_CACHE)
        except Exception:
            try:
                os.remove(tmp)
            except Exception:
                pass
    return adj, deg, deg_base


_LOAD_GRAPH_OWN = _load_graph


class _MembersWithoutRecords(object):
    """The topic snapshot's members with the session records left out, filtered per subject on demand
    (the hook reads a handful of subjects; filtering all of them would cost more than the walk)."""
    __slots__ = ("_members", "_groups")

    def __init__(self, members):
        self._members = members
        self._groups = {}

    def __contains__(self, term):
        return term in self._members

    def __bool__(self):
        return bool(self._members)

    def get(self, term, default=None):
        if term not in self._groups:
            raw = self._members.get(term)
            if not raw:
                return default if raw is None else raw
            group = raw if isinstance(raw, set) else set(raw.split(","))
            self._groups[term] = {slug for slug in group if not SESSION_RECORD.match(slug)}
        return self._groups[term]


def _load_embeddings():
    """The offline-computed semantic sidecar (build_memory_embeddings.py, run on the box). Returns
    (index {slug: row}, matrix, bias) or None if absent/unreadable - in which case spread_recall runs
    EXACTLY as before (graph-walk only, no regression). This is the online half of lifting the ~45%
    ceiling: with it, candidates get re-ranked by cosine similarity to the seed cluster, so a tangential
    hub that the graph reaches but that means something different gets damped - the content call the
    judges said a fast hook couldn't make, made cheap by precomputing the vectors offline.

    bias = the sidecar's own random-pair cosine baseline (for unit vectors, ||mean vec||^2 - verified
    against measured random-pair means 2026-07-09). Models differ wildly here: LSA sits near 0.0,
    bge-small near 0.68 - so a raw-cosine damp collapses to a ~4x narrower range on a high-baseline
    model. Recentring by the sidecar's OWN baseline makes the re-rank self-calibrating across models;
    for LSA (bias~0.004) behaviour is unchanged."""
    if embedding_artifact is None:
        return None
    # A command hook starts a fresh interpreter on every prompt.  The schema-v3 artifact is a
    # simple, immutable NPZ, so importing NumPy only to read it cost more than the graph walk
    # itself (~190 ms on the live path).  The lightweight reader verifies the same authority
    # pointer, complete sidecar hash, schema, source-stat mask, and participant content hashes.
    # If this module predates the hot reader, retain its canonical loader for source-tree
    # compatibility.  Once the hot reader exists, however, its rejection is authoritative:
    # retrying malformed current-schema bytes through a more permissive parser would weaken
    # the fail-closed boundary and would unexpectedly import NumPy on a damaged artifact.
    hot_loader = getattr(embedding_artifact, "load_verified_embeddings_lightweight", None)
    if hot_loader is not None:
        global _MEM_STATS
        loaded = hot_loader(EMB, EMB_AUTHORITY, MEM, EXPECTED_EMB_MODEL)
        _MEM_STATS = getattr(loaded, "memory_stats", None)
        return loaded
    return embedding_artifact.load_verified_embeddings(
        EMB, EMB_AUTHORITY, MEM, EXPECTED_EMB_MODEL
    )


def _load_mistake_weights():
    """slug -> severity 0..1 for mistake-class nodes (audit_* dispositions, compiled-reflex
    source scars, their curated wiki-link neighbours), built offline by build_mistake_weights.py.
    Fail-open: absent/corrupt file -> {} -> exact prior behaviour."""
    out = {}
    try:
        with io.open(MW, encoding="utf-8", errors="replace") as f:
            next(f, None)
            for ln in f:
                p = ln.rstrip("\n").split("\t")
                if len(p) >= 2 and p[0] != "slug":
                    try:
                        out[p[0]] = max(0.0, min(1.0, float(p[1])))
                    except Exception:
                        pass
    except Exception:
        return {}
    return out


def _load_keyword_snapshot():
    """Bind nodes + relations to one verified generation.

    A pointer or hash failure disables the topic lane for this call. It never combines a new
    nodes file with an old related-subject file.
    """
    members, aliases, weights, related = {}, {}, {}, defaultdict(list)
    try:
        selected = keyword_artifacts.load_generation(
            DR,
            required=("keyword_nodes.tsv", "keyword_related.tsv"),
            allow_legacy=True,
        )
        with io.open(selected["paths"]["keyword_nodes.tsv"],
                     encoding="utf-8", errors="strict") as f:
            next(f, None)
            for ln in f:
                p = ln.rstrip("\n").split("\t")
                if len(p) < 4 or p[0] == "keyword":
                    continue
                try:
                    weight = max(0.0, float(p[2]))
                except Exception:
                    continue
                if p[1] == "member":
                    members[p[0]] = p[3]
                    weights[p[0]] = weight
                elif p[1] == "alias":
                    aliases[p[0]] = p[3]
        aliases = {raw: target for raw, target in aliases.items() if target in members}
        with io.open(selected["paths"]["keyword_related.tsv"],
                     encoding="utf-8", errors="strict") as f:
            next(f, None)
            for ln in f:
                p = ln.rstrip("\n").split("\t")
                if len(p) < 3 or not p[0].startswith("kw:") or not p[1].startswith("kw:"):
                    continue
                try:
                    weight = float(p[2])
                except Exception:
                    continue
                if not (weight > 0.0):
                    continue
                a, b = p[0][3:], p[1][3:]
                if a in members and b in members and a != b:
                    related[a].append((b, weight))
                    related[b].append((a, weight))
        for term in related:
            related[term].sort(key=lambda pair: (-pair[1], pair[0]))
        return members, aliases, weights, related, selected["generation_id"]
    except Exception:
        return {}, {}, {}, defaultdict(list), ""


def _load_keyword_nodes():
    """Backward-compatible two-value view for evaluation tools."""
    members, aliases, _weights, _related, _generation = _load_keyword_snapshot()
    return members, aliases


def _members_of(members, term):
    """The member set for one named subject (split on demand)."""
    raw = members.get(term)
    if not raw:
        return set()
    if isinstance(raw, set):
        return raw
    return set(raw.split(","))


def _topic_overlay(words, sal, members, aliases, hit, weights=None, related=None):
    """Edges for the subjects THIS text names, attached on demand: kw:<term> to its members and
    to its related subjects. Wiring all 87k keyword edges into the walk instead cost ~680ms per
    recall (vs 123ms) because a 90-member topic explodes the second hop across its whole
    subject - and the subjects he is NOT talking about contribute nothing but that cost. The
    overlay is a few hundred edges at most, so the keyword layer is consulted where it is
    relevant rather than carried everywhere."""
    overlay = defaultdict(list)
    if not hit:
        return overlay
    weights = weights or {}
    related = related or {}
    hit_order = sorted(hit, key=lambda term: (-weights.get(term, 0.0), term))

    # Choose a few strongest neighbouring subjects globally. Six neighbours PER hit made a
    # normal multi-topic prompt fan out across more than a thousand memories.
    neighbour_weight = {}
    for term in hit_order:
        for other, weight in related.get(term, ()):
            if other not in hit and other in members:
                neighbour_weight[other] = max(neighbour_weight.get(other, 0.0), weight)
    neighbours = sorted(
        neighbour_weight,
        key=lambda term: (-neighbour_weight[term], -weights.get(term, 0.0), term),
    )[:TOPIC_MAX_RELATED]
    reach_order = hit_order + neighbours

    # Round-robin the most salient members so every named subject gets a lane before any one
    # broad subject consumes the global edge budget. Stable slug tie-breaks make the result
    # identical under every PYTHONHASHSEED.
    ranked = {}
    for term in reach_order:
        ranked[term] = sorted(
            _members_of(members, term),
            key=lambda slug: (-sal.get(slug, 40.0), slug),
        )[:TOPIC_TOP_K]
    topic_edges = []
    for rank in range(TOPIC_TOP_K):
        for term in reach_order:
            group = ranked.get(term, ())
            if rank < len(group):
                topic_edges.append((term, group[rank]))
                if len(topic_edges) >= TOPIC_OVERLAY_MAX_EDGES:
                    break
        if len(topic_edges) >= TOPIC_OVERLAY_MAX_EDGES:
            break
    group_sizes = defaultdict(int)
    for term, _slug in topic_edges:
        group_sizes[term] += 1
    for term, slug in topic_edges:
        node = "kw:" + term
        weight = 1.0 / (group_sizes[term] ** 0.5)
        overlay[node].append((slug, weight))
        overlay[slug].append((node, weight))

    # Relations are symmetric and deduplicated; their count is bounded by the selected reach.
    relation_edges = {}
    reach = set(reach_order)
    for term in hit_order:
        for other, weight in related.get(term, ()):
            if other not in reach or other == term:
                continue
            pair = tuple(sorted((term, other)))
            relation_edges[pair] = max(relation_edges.get(pair, 0.0), weight)
    for (a, b), weight in sorted(relation_edges.items()):
        overlay["kw:" + a].append(("kw:" + b, weight))
        overlay["kw:" + b].append(("kw:" + a, weight))
    return overlay


def _topic_slugs(words, sal):
    """The slugs under the topics this text names, capped per topic by salience.

    Matching accepts his spelling through the alias map, so 'levrage' activates the leverage
    topic. Returns a set; membership in it is what earns the KEYWORD_GAIN in the walk."""
    members, aliases, hit, _weights, _related, _generation = _topic_context(words)
    if not members:
        return set()
    return _topic_expand(hit, members, sal)


def _topic_context(words):
    """One generation-bound topic context for this text."""
    members, aliases, weights, related, generation = _load_keyword_snapshot()
    if not members:
        return {}, {}, set(), {}, defaultdict(list), ""
    hit = set()
    for w in sorted(words):
        if w in members:
            hit.add(w)
        canonical = aliases.get(w)
        if canonical and canonical in members:
            hit.add(canonical)
    hit = set(sorted(hit, key=lambda term: (-weights.get(term, 0.0), term))[:TOPIC_MAX_HITS])
    return members, aliases, hit, weights, related, generation


def _topic_hits(words):
    """Backward-compatible three-value view for evaluation tools."""
    members, aliases, hit, _weights, _related, _generation = _topic_context(words)
    return members, aliases, hit


def _topic_expand(hit, members, sal):
    """Seeds from the subjects this text names, capped per subject AND across all of them.

    TOPIC_TOP_K alone bounds one subject; it does not bound their union, so a text naming many
    subjects still lit hundreds of seeds (100 topic words measured at an 843-slug union). Past a
    point every extra seed only flattens the walk - everything is lit, so nothing stands out."""
    out = set()
    for keyword in sorted(hit):
        group = _members_of(members, keyword)
        group = sorted(group, key=lambda slug: (-sal.get(slug, 40.0), slug))[:TOPIC_TOP_K]
        out.update(group)
    if len(out) > TOPIC_MAX_UNION:
        out = set(sorted(out, key=lambda slug: (-sal.get(slug, 40.0), slug))[:TOPIC_MAX_UNION])
    return out


# _load_salience is kept per (mtime, size) of salience.tsv (3 Nov 2026, claude-738954b6, RECALL-10X rank 3): the
# resident recall server called it twice a warm recall and parsed the 3.8 MB file each time, 0.49 s a call in the
# profile. The file is rewritten about once a day; a changed stat re-reads it. Each caller gets its own copy.
# BRAIN_SAL_CACHE=0 turns the cache off.
_SAL_CACHE = [None, None]


def _load_salience():
    if os.environ.get("BRAIN_SAL_CACHE", "1") != "0":
        try:
            st = os.stat(SAL)
            key = (st.st_mtime_ns, st.st_size)
        except Exception:
            key = None
        if key is not None and _SAL_CACHE[0] == key:
            return dict(_SAL_CACHE[1])
        sal = _load_salience_file()
        if key is not None:
            _SAL_CACHE[0], _SAL_CACHE[1] = key, sal
        return dict(sal)
    return _load_salience_file()


def _load_salience_file():
    sal = {}
    try:
        with io.open(SAL, encoding="utf-8", errors="replace") as f:
            next(f, None)
            for ln in f:
                p = ln.split("\t")
                if len(p) >= 2:
                    slug = p[0][:-3] if p[0].endswith(".md") else p[0]
                    try:
                        sal[slug] = float(p[1])
                    except Exception:
                        pass
    except Exception:
        pass
    return sal


def _index_lines():
    """slug -> its MEMORY.md index line (for a legible one-liner + lexical-match test)."""
    out = {}
    try:
        with io.open(os.path.join(MEM, "MEMORY.md"), encoding="utf-8", errors="replace") as f:
            for ln in f:
                m = re.match(r"- \[([^\]]+?)\.md\]", ln)
                if m:
                    out[m.group(1)] = ln.strip()
    except Exception:
        pass
    return out


# The filename arm's folder names (3 Nov 2026, claude-738954b6, RECALL-10X rank 3): brain.py's recall sets MEM_NAMES
# for the length of a call to a reader of the call's own listing (_memlist, which the recall server answers from its
# resident listing), so the arm no longer lists the 50k-entry folder a second time - 2.2 s of a 6-8 s warm recall in
# the server profile. Same names in the same order; None (the hook, any other caller, a failed listing) lists as before.
MEM_NAMES = None
# In the per-prompt hook (main() sets this) the name scan takes the embedding loader's bulk stat read instead of
# os.listdir and hands it on, so the folder is read once a prompt, not twice. Same .md names in the same order as
# listdir (53,077, checked); the scan only reads names ending in .md. SPREAD_SHARED_LIST=0 lists as before.
_SHARE_LIST = False
# {name.md: (size, mtime_ns)} from the last shared folder read in this process, or None.
_MEM_STATS = None


def _mem_names():
    global _MEM_STATS
    if MEM_NAMES is not None:
        try:
            names = MEM_NAMES()
            if names is not None:
                return names
        except Exception:
            pass
    if (_SHARE_LIST and embedding_artifact is not None and os.environ.get("SPREAD_SHARED_LIST", "1") != "0"
            and hasattr(embedding_artifact, "_PREFETCH")):
        try:
            st = embedding_artifact._bulk_md_stats(MEM)
        except Exception:
            st = None
        if st is not None:
            _MEM_STATS = st
            embedding_artifact._PREFETCH = (MEM, time.monotonic(), st)
            return list(st)
    return os.listdir(MEM)


# --- RunIndex: the seed arms' overlap counts without the per-line scan (3 Nov 2026, claude-738954b6, RECALL-10X rank 3) ---
# The two lexical arms of _seeds_from_prompt count, for each index line (and each memory file name), how many query
# words occur in it as substrings: 0.6-0.8 s of a warm served recall on 50k lines plus 50k names. Every query word is
# [a-z_]+ (_words lowers the prompt and keeps [a-zA-Z][a-zA-Z_]{2,}), so `w in text.lower()` holds exactly when w sits
# inside one maximal [a-z_]+ run of the lowered text. A resident process keeps run -> keys postings and the run
# vocabulary joined for one bytes.find pass per word; a word's keys are the union of the postings of the runs that
# contain it. Same counts, same seeds, same order (62/62 word sets, 22 real questions + 40 random run fragments).
# It is built from the SECOND call in a process (a one-shot CLI or the per-prompt hook never pays the ~1.7 s build),
# kept while the lists are unchanged (two list compares in C), resynced key by key when they change, and off with
# BRAIN_SEED_RUNINDEX=0. Any word outside [a-z_]+ or any failure falls back to the scan.
_RUN_RE = re.compile(r"[a-z_]+")
_RUN_WORD = re.compile(r"[a-z_]+\Z")


class _RunIndex:
    def __init__(self):
        self.keys = None
        self.texts = None
        self.text = {}
        self.pos = {}
        self.post = {}
        self.vocab = []
        self.vblob = b""
        self.vstarts = [0]
        self.vdirty = False

    def _add(self, key, text):
        for r in set(_RUN_RE.findall(text.lower())):
            s = self.post.get(r)
            if s is None:
                s = self.post[r] = set()
                self.vocab.append(r)
                self.vdirty = True
            s.add(key)

    def _drop(self, key, text):
        for r in set(_RUN_RE.findall(text.lower())):
            s = self.post.get(r)
            if s is not None:
                s.discard(key)

    def sync(self, keys, texts):
        # the same list objects as last call: skip the element-wise compare (about 1.2 ms a call at 50,000 keys)
        if keys is self.keys and texts is self.texts:
            return
        if keys == self.keys and texts == self.texts:
            return
        old = self.text
        live = set(keys)
        for k, t in zip(keys, texts):
            o = old.get(k)
            if o is t or o == t:
                continue
            if o is not None:
                self._drop(k, o)
            self._add(k, t)
            old[k] = t
        for k in [k for k in old if k not in live]:
            self._drop(k, old.pop(k))
        self.keys, self.texts = keys, texts
        self.pos = {k: i for i, k in enumerate(keys)}

    def hits(self, words, need=2):
        """[(key, count)] with count >= need, in key order: count = number of words that occur in the key's text."""
        if self.vdirty:
            self.vblob = "\x00".join(self.vocab).encode("ascii")
            st = [0]
            for r in self.vocab:
                st.append(st[-1] + len(r) + 1)
            self.vstarts = st
            self.vdirty = False
        blob, st, vocab, post = self.vblob, self.vstarts, self.vocab, self.post
        cnt = {}
        for w in words:
            b = w.encode("ascii")
            got = set()
            p = blob.find(b)
            while p != -1:
                i = bisect.bisect_right(st, p) - 1
                got |= post[vocab[i]]
                p = blob.find(b, st[i + 1])
            for k in got:
                cnt[k] = cnt.get(k, 0) + 1
        pos = self.pos
        return sorted(((k, c) for k, c in cnt.items() if c >= need), key=lambda kc: pos[kc[0]])


_RUNIDX = {"calls": 0}
_SLUG_REUSE = {}
_RUNIDX_LOCK = threading.Lock()


def _runindex_ok(tag, words):
    """True when the RunIndex may answer: switched on, past the first call in the process (or already built), and every
    word [a-z_]+."""
    if os.environ.get("BRAIN_SEED_RUNINDEX", "1") == "0":
        return False
    if tag not in _RUNIDX and _RUNIDX["calls"] < 1:
        return False
    return all(_RUN_WORD.match(w) for w in words)


def _runindex_hits(tag, keys, texts, words):
    """[(key, count >= 2)] in key order from the RunIndex for `tag` synced to keys/texts, or None (another thread holds
    it, or a failure, which also drops the index) - the caller then scans."""
    if not _RUNIDX_LOCK.acquire(blocking=False):
        return None
    try:
        ri = _RUNIDX.get(tag)
        if ri is None:
            ri = _RUNIDX[tag] = _RunIndex()
        ri.sync(keys, texts)
        return ri.hits(words)
    except Exception:
        _RUNIDX.pop(tag, None)
        return None
    finally:
        _RUNIDX_LOCK.release()


def _seeds_from_prompt(words, idx, skip_records=False):
    """Lexical hits = seeds. Returns {slug: strength} and the lexical-match set (the 'obvious' set).
    skip_records=True (the hook) leaves the session records out of every arm; the BM25 arm then reads
    BM25_RECORD_POOL hits so that its five seeds are the five best non-record hits."""
    if len(words) < 2:
        return {}, set()
    # Stable tie-break: `words` is a set, so length-only sorting made equal-length cutoff terms
    # depend on PYTHONHASHSEED. On the real replay that jitter was larger than the measured spell
    # delta. Lexical order is only a deterministic tie-break; length remains the rarity proxy.
    words = _distinctive_words(words)   # distinctive words only
    seeds, lex = {}, set()
    pairs = _runindex_hits("idx", list(idx), list(idx.values()), words) if _runindex_ok("idx", words) else None
    if pairs is not None:
        for slug, overlap in pairs:
            if slug in SKIP or (skip_records and SESSION_RECORD.match(slug)):
                continue
            seeds[slug] = float(overlap)
            lex.add(slug)
    else:
        for slug, line in idx.items():
            if slug in SKIP or (skip_records and SESSION_RECORD.match(slug)):
                continue
            low = line.lower()
            overlap = sum(1 for w in words if w in low)
            if overlap >= 2:
                seeds[slug] = float(overlap)
                lex.add(slug)
    # also filenames across the whole store (memories not in the live index)
    # (3 Nov 2026, RECALL-10X: the words of four letters or more are picked once and each name lowered once, not once
    # per word: 1.9 s of a 4 s warm recall on the 50k-entry folder. Fewer than two such words can never reach an
    # overlap of 2, so the folder is not listed at all then. Same seeds, same order.)
    long_words = [w for w in words if len(w) >= 4]
    try:
        names = _mem_names() if len(long_words) >= 2 else ()
        pairs = None
        if names and _runindex_ok("names", long_words):
            # The resident server hands back the SAME held list object while the folder is unchanged; new slug
            # strings every call made RunIndex.sync compare ~53k strings by content against the held ones, paging
            # them in (sync was 22% of slow-call samples). The slug list is reused while `names` is the same object
            # (held here, so its id cannot be recycled); any other list, e.g. a fresh listing, builds as before.
            # Off: BRAIN_SEED_SLUG_REUSE=0.
            c = _SLUG_REUSE.get("v")
            if c is not None and c[0] is names and os.environ.get("BRAIN_SEED_SLUG_REUSE", "1") != "0":
                slugs = c[1]
            else:
                slugs = [fn[:-3] for fn in names if fn.endswith(".md")]
                _SLUG_REUSE["v"] = (names, slugs)
            pairs = _runindex_hits("names", slugs, slugs, long_words)
        if pairs is not None:
            for slug, overlap in pairs:
                if slug in SKIP or slug in seeds or (skip_records and SESSION_RECORD.match(slug)):
                    continue
                seeds[slug] = float(overlap)
                lex.add(slug)
        else:
            for fn in names:
                if not fn.endswith(".md"):
                    continue
                slug = fn[:-3]
                if slug in SKIP or slug in seeds or (skip_records and SESSION_RECORD.match(slug)):
                    continue
                slow = slug.lower()
                overlap = sum(1 for w in long_words if w in slow)
                if overlap >= 2:
                    seeds[slug] = float(overlap)
                    lex.add(slug)
    except Exception:
        pass
    _RUNIDX["calls"] += 1
    # BM25 full-text arm (2026-07-09): the index-line/filename match above cannot see memory CONTENT.
    # Gated before wiring on eval_prompt_recall (real first prompts -> the session's actual reads,
    # whole corpus as negatives): bm25 hit@10 0.53 vs 0.22 for this lexical seeder, MRR 2.4x. Top
    # BM25 hits join the seed set at lexical-comparable strength; fail-open (no index -> unchanged).
    try:
        from bm25 import BM25, QueryNormalizer
        bm = BM25()
        # spell-normalize OOV query tokens before scoring: the owner types heavy deliberate typos
        # ('memeory','mathmatically'), and exact-term BM25 surfaces nothing for them (the workflow-recall
        # gap). Correction touches only out-of-vocab tokens, so a clean prompt is unchanged (measured
        # 2026-07-09: prompt-recall hit@10 0.56->0.61, hit@20 0.75->0.81, hit@5=; MRR -0.006 noise, and
        # the seed set here is a POOL not a ranked list, so top-1 rank doesn't matter). edit1-only, ~0.7ms.
        q = QueryNormalizer(bm.df).normalize(" ".join(words))
        if skip_records:
            hits = [hit for hit in bm.score(q, BM25_RECORD_POOL) if not SESSION_RECORD.match(hit[0])][:5]
        else:
            hits = bm.score(q, 5)
        if hits:
            smax = hits[0][1]
            for slug, s in hits:
                if s >= 6.0 and slug not in SKIP:
                    seeds[slug] = max(seeds.get(slug, 0.0), 1.5 + 1.5 * s / smax)
                    lex.add(slug)
    except Exception:
        pass
    return seeds, lex


def _spread(seeds, adj, deg, mw=None, kw=None, use_keywords=True, overlay=None):
    """Two-hop spreading activation, fan-out normalized. Track each node's strongest bridge
    (parent). mw grades a conductance boost INTO mistake-class nodes (the MW_BOOST note above);
    the default None keeps external replay harnesses that call _spread positionally byte-compatible."""
    mw = mw or {}
    kw = kw or set()
    # the named subjects' edges, attached for this recall only (see _topic_overlay)
    overlay = overlay if (overlay and use_keywords) else {}
    deg_ov = defaultdict(float)
    for node, pairs in overlay.items():
        for _nb, w in pairs:
            deg_ov[node] += w
    act = defaultdict(float)
    parent = {}
    for s, a0 in seeds.items():
        act[s] += a0
    frontier = dict(seeds)
    for _hop in range(HOPS):
        nxt = defaultdict(float)
        for src, a in frontier.items():
            d = deg.get(src, 0.0) + deg_ov.get(src, 0.0)
            if d <= 0:
                continue
            neighbours = adj.get(src, ())
            if overlay:
                extra = overlay.get(src)
                if extra:
                    neighbours = list(neighbours) + extra
            for nb, w in neighbours:
                if not use_keywords and nb.startswith("kw:"):
                    continue          # memory-only arm: the floor the keyword arm must clear
                sev_nb = mw.get(nb, 0.0)
                gain = 1.0 + MW_BOOST * sev_nb
                if nb in kw:                  # he named this topic: its nodes diffuse faster
                    gain *= KEYWORD_GAIN if sev_nb >= MW_SEV_BIG else KEYWORD_INFO_GAIN
                flow = a * (w / d) * DECAY * gain
                if flow <= 0:
                    continue
                nxt[nb] += flow
                act[nb] += flow
                # remember the neighbour's single largest inflow -> the bridge that explains it
                if flow > parent.get(nb, (None, 0.0))[1]:
                    parent[nb] = (src, flow)
        frontier = nxt
    return act, parent


def _log_capped(sess, slug, n, gap, need):
    """One row per held-back association (real sessions only), so the cap's effect stays measurable
    without touching surfaced.tsv."""
    if not re.match(r"^[0-9a-f]{8}-", sess):
        return
    row = "%.0f\t%s\t%s\t%d\t%.0f\t%d\n" % (time.time(), sess[:12], slug, n, gap, need)
    if _fresh_async():
        _FW.put(("cap", row))
        return
    _append_capped(row)


def _append_capped(row):
    try:
        with io.open(os.path.join(DR, "spread_capped.tsv"), "a", encoding="utf-8") as f:
            f.write(row)
    except Exception:
        pass


# Under the resident server the dedup state's write went on the
# reply path, and on a contended disk the os.replace of this 9 KB file took 1-4 s (13 of 498 slow served calls,
# 5-6 Nov, faults.jsonl). The server holds the state in process and one writer thread flushes it; see _fresh.
import threading as _threading
import queue as _queue
_FW = _queue.Queue()
_FS = {"st": None, "sig": None, "mine": {}, "thread": None}
_FS_LOCK = _threading.Lock()


def _fresh_async():
    if os.environ.get("SPREAD_SERVER_CHILD") != "1" or os.environ.get("SPREAD_FRESH_ASYNC", "1") == "0":
        return False
    if _FS["thread"] is None or not _FS["thread"].is_alive():
        if _FS["thread"] is None:
            import atexit
            atexit.register(_fresh_flush)     # the server exits between calls on a code change: land queued stamps
        t = _threading.Thread(target=_fresh_writer, name="fresh-writer", daemon=True)
        _FS["thread"] = t
        t.start()
    return True


def _state_sig():
    try:
        x = os.stat(STATE)
        return (x.st_mtime_ns, x.st_size)
    except OSError:
        return None


def _write_state(st):
    _tmp = STATE + ".%d.tmp" % os.getpid()
    with io.open(_tmp, "w", encoding="utf-8") as _f:
        json.dump(st, _f)
    os.replace(_tmp, STATE)


def _fresh_writer():
    while True:
        kind, val = _FW.get()
        try:
            if kind == "cap":
                _append_capped(val)
                continue
            with _FS_LOCK:
                if val is not _FS["st"]:
                    continue          # a newer state is queued behind this one
                snap = dict(val)
            _write_state(snap)
            with _FS_LOCK:
                _FS["sig"] = _state_sig()
                for k, v in snap.items():
                    if _FS["mine"].get(k) == v:
                        del _FS["mine"][k]
        except Exception:
            pass
        finally:
            _FW.task_done()


def _fresh_flush(timeout=5.0):
    """Tests and shutdown: wait until every queued write landed."""
    import time as _t
    end = _t.time() + timeout
    while _FW.unfinished_tasks and _t.time() < end:
        _t.sleep(0.01)
    return not _FW.unfinished_tasks


def _fresh_load_async():
    with _FS_LOCK:
        sig = _state_sig()
        if _FS["st"] is None or sig != _FS["sig"]:
            try:
                st = json.load(io.open(STATE, encoding="utf-8")) if sig is not None else {}
            except Exception:
                st = {}
            for k, v in _FS["mine"].items():
                if v > st.get(k, 0):
                    st[k] = v
            _FS["st"], _FS["sig"] = st, sig
        return dict(_FS["st"])


def _fresh(sess, slug, capped=False):
    """Per-slug dedup (DEDUP_SECS), and with capped=True the per-session rate cap on top of it. State
    entries are 'session|slug' -> last fire time; the premise-check path keys as 'session|stale|slug' and
    those never count as association fires. Fail-safe on any state error: fire."""
    if not sess:
        return True
    now = time.time()
    _async = _fresh_async()
    if _async:
        st = _fresh_load_async()
    else:
        try:
            st = json.load(io.open(STATE, encoding="utf-8")) if os.path.exists(STATE) else {}
        except Exception:
            st = {}
    st = {k: v for k, v in st.items() if now - v < 86400}
    key = sess + "|" + slug
    if now - st.get(key, 0) < DEDUP_SECS:
        return False
    if capped:
        pre = sess + "|"
        fires = [v for k, v in st.items() if k.startswith(pre) and not k[len(pre):].startswith("stale|")]
        n = len(fires)
        if n >= SESSION_FREE_FIRES:
            need = next(g for lim, g in SESSION_GAPS if lim is None or n < lim)
            gap = now - max(fires)
            if gap < need:
                _log_capped(sess, slug, n, gap, need)
                return False
    st[key] = now
    # only real (uuid-shaped) sessions persist: test/eval fixtures were leaking keys into this
    # live state file (the ledgers-polluted-by-own-gate class, caught by the 07-09 graph audit)
    if re.match(r"^[0-9a-f]{8}-", sess):
        if _async:
            with _FS_LOCK:
                _FS["mine"][key] = now
                _FS["st"] = st
            _FW.put(("state", st))
            return True
        try:
            # atomic replace (2026-09-02): a bare truncate-and-dump let a concurrent reader see a half-written
            # file, fall back to {} and write back only its own key - the race that wiped the reflex hook's
            # backoff state across 21 sessions; same shape here
            _write_state(st)
        except Exception:
            pass
    return True


def _desc(slug, idx):
    line = idx.get(slug, "")
    m = re.match(r"- \[[^\]]+\]\([^)]+\)\s*[-\u2014]*\s*(.*)$", line)  # strip hyphen or em-dash sep
    txt = (m.group(1) if m else "").strip()
    if not txt:
        # fall back to the memory's description frontmatter / first prose line
        txt = _file_desc(slug)
    return txt[:200]


# MEMORY.md keeps only the newest ~100 index lines, so _desc falls back to the file for nearly every candidate: ~310
# reads a prompt, 0.1-0.33 s by disk state (1 Nov). DESC_CACHE keeps slug -> (size, mtime_ns, description[:200]),
# answered while the file keeps that stat (the embedding loader's rule) and saved when a prompt added entries.
DESC_CACHE = os.path.join(DR, ".spread_desc_cache.marshal")
_FDESC = {}


def _fdesc_cache():
    got = _FDESC.get("c")
    if got is None:
        import marshal
        try:
            with open(DESC_CACHE, "rb") as fh:
                got = marshal.load(fh)
            if not isinstance(got, dict):
                got = {}
        except Exception:
            got = {}
        _FDESC["c"] = got
        import atexit
        atexit.register(_fdesc_save)     # hook_dispatch runs hooks in-process and ends in sys.exit, so this runs
    return got


def _fdesc_save():
    """Merge this process's new entries into the file another hook may have rewritten since it was read, then swap
    it in: hooks run side by side, and a plain dump of the copy read at start would drop what the others added."""
    if not _FDESC.get("dirty"):
        return
    import marshal
    out = _FDESC["c"]
    new = _FDESC.get("new")
    if new:
        try:
            with open(DESC_CACHE, "rb") as fh:
                cur = marshal.load(fh)
            if isinstance(cur, dict):
                cur.update(new)
                out = cur
        except Exception:
            pass
    tmp = "%s.%d.tmp" % (DESC_CACHE, os.getpid())
    try:
        with open(tmp, "wb") as fh:
            marshal.dump(out, fh)
        os.replace(tmp, DESC_CACHE)
        _FDESC["dirty"] = False
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass


def _file_desc(slug):
    path = os.path.join(MEM, slug + ".md")
    # The stat comes from the prompt's one shared folder read when it ran (see _mem_names), else os.stat: on a large
    # memory folder the per-file stat of 45-280 candidates was most of this lookup's cost. SPREAD_DESC_STATS=0 stats
    # every file.
    st = None
    if _MEM_STATS is not None and os.environ.get("SPREAD_DESC_STATS", "1") != "0":
        try:
            got = _MEM_STATS.get(slug + ".md")
            if got is not None:
                st = (int(got[0]), int(got[1]))
        except Exception:
            st = None
    if st is None:
        try:
            s = os.stat(path)
        except OSError:
            return ""
        st = (s.st_size, s.st_mtime_ns)
    cache = _fdesc_cache()
    hit = cache.get(slug)
    if hit is not None and hit[0] == st[0] and hit[1] == st[1]:
        return hit[2]
    txt = ""
    try:
        with io.open(path, encoding="utf-8", errors="replace") as handle:
            body = handle.read()
        dm = re.search(r'description:\s*"?(.+?)"?\s*\n', body)
        if dm:
            txt = dm.group(1).strip()
    except Exception:
        return txt
    # the stat was taken before the read, so a file written in between leaves an entry no later stat matches
    cache[slug] = (st[0], st[1], txt[:200])
    _FDESC.setdefault("new", {})[slug] = cache[slug]
    _FDESC["dirty"] = True
    return txt


def _status_hint(slug, desc):
    """One clause on how far to trust a surfaced memory, from its own frontmatter (status, superseded_by)
    with the index line's leading tag as the fallback - so the reader hears 'provisional' or 'superseded'
    in the same breath as the reminder. Owner order 2026-09-02: a note can mean something good or bad,
    not necessarily good; the brain should give a hint, not an instruction. Fail-safe: any error -> the
    generic clause."""
    status, superseded, mtype, parts, front = "", "", "", [], ""
    part_pointer = None
    try:
        with io.open(os.path.join(MEM, slug + ".md"), encoding="utf-8", errors="replace") as handle:
            head = handle.read(8000)
        # only the closed frontmatter block: a value severed by the read bound must not read as a short one
        fm = re.match(r"\s*---\s*\n(.*?)\n---", head, re.S)
        head = fm.group(1) if fm else ""
        front = head
        m = re.search(r"^\s*superseded_by:[ \t]*(.*)$", head, re.M)
        if m and m.group(1).strip():
            # one classification for every reader (supersession.pointer_scope, 4 Oct 2026, dot 125873D4): a pointer
            # its author narrowed - "slug # only its third section", "slug (the half-real part only ...)" - is a
            # part supersession, never "read that one instead"
            try:
                from supersession import pointer_scope
                kind, slugs, qualifier = pointer_scope(m.group(1))
            except Exception:
                kind, slugs, qualifier = "whole", [m.group(1).split()[0].strip("'\",")], ""
            if kind == "whole":
                superseded = slugs[0]
            elif kind == "part":
                part_pointer = (("\"%s\"" % qualifier) if qualifier else "part of it", slugs[0])   # the author's words
        m = re.search(r"^\s*status:\s*(\S+)", head, re.M)   # lives under metadata:, indented
        if m:
            status = m.group(1).strip().strip("'\"").lower()
        m = re.search(r"^\s*type:\s*(\S+)", head, re.M)
        if m:
            mtype = m.group(1).strip().strip("'\"").lower()
        parts = _part_items(head)
        if part_pointer and part_pointer not in parts:
            parts = [part_pointer] + parts
    except Exception:
        pass
    if not status:
        m = re.match(r"\s*\[([A-Z][A-Z -]{2,24})[\],]", desc or "")   # "[VERIFIED]" or "[MEASURED, ...]"
        if m:
            status = m.group(1).strip().lower()
    if superseded:
        return ("hint: SUPERSEDED by `%s` - read that one instead; this memory records what was true before it"
                % superseded)
    base = _status_hint_rest(slug, desc, status, mtype)
    # PART SUPERSESSION (2026-09-22, round 5 W4): the memory declares one clause of itself overtaken and the rest
    # current (superseded_in_part). Say which clause moved and where, then the ordinary hint for the rest. A memory
    # its author retracted keeps the retraction hint alone - "the rest stands" would contradict it.
    if parts and not base.startswith("hint: RETRACTED"):
        moved = []
        for clause, successor in parts[:2]:
            clause = clause if len(clause) <= 90 else clause[:87].rstrip() + "..."
            moved.append(("%s moved to `%s`" % (clause, successor)) if successor
                         else ("%s was corrected in its own body" % clause))
        if len(parts) > 2:
            moved.append("%d more" % (len(parts) - 2))
        return _with_moved("hint: PARTLY SUPERSEDED: %s; the rest stands. For the rest: %s" % (
            "; ".join(moved), base[len("hint: "):] if base.startswith("hint: ") else base),
            slug, front, [s for _, s in parts])
    return _with_moved(base, slug, front, ())


def _with_moved(hint, slug, front, skip):
    """The hint plus what the memory names that has changed since it was filed and where the current word lives
    (moved_facts.py: deleted boxes and storage, retired law generations, the laptop GPU stop, the voice...). Added
    2026-09-23 after T4: 31 recall hits two judges called misleading were almost all old memories naming something
    since retired; the registry catches 19 of them. A retracted memory keeps its retraction hint alone. Fail-safe."""
    if hint.startswith("hint: RETRACTED"):
        return hint
    try:
        import moved_facts
        found = moved_facts.notes(slug, front, skip=tuple(s for s in skip if s))
    except Exception:
        return hint
    if not found:
        return hint
    return hint + "; moved since it was filed: " + " / ".join("%s -> `%s`" % (w, s) for w, s in found)


def _part_items(front):
    """[(clause, successor)] declared by a frontmatter block's superseded_in_part (or the older
    partially_superseded_by); [] when none, when BRAIN_PART_SUPERSESSION=0, or on any error."""
    if os.environ.get("BRAIN_PART_SUPERSESSION", "1") == "0" or not re.search(
            r"^[ \t]*(?:superseded_in_part|partially_superseded_by)[ \t]*:", front or "", re.M):
        return []
    try:
        from supersession import part_supersessions
        return part_supersessions(front, MEM)
    except Exception:
        return []


def _status_hint_rest(slug, desc, status, mtype):
    """_status_hint for a memory that is not superseded as a whole (split out 2026-09-22 so a part-superseded
    memory can carry the same clause for the part that stands). Unchanged text, unchanged order."""
    # a memory its author took back (2026-09-22, F50): 32 memories open with a RETRACTED / WITHDRAWN banner and
    # name no successor, and their status field, when it says so, says it in five spellings; before this branch
    # every one of them fell through to the type hint and read as a live project fact
    if status.startswith(("retracted", "withdrawn", "headline-retracted")) or re.match(
            r"\s*(?:\[[A-Z][A-Z ,-]*\]\s*){0,3}\[(?:RETRACTED|WITHDRAWN)", desc or ""):
        return ("hint: RETRACTED by its author - the claim was taken back; read it for what was wrong and why, "
                "not for what to do")
    if re.match(r"\s*\[OWNER", desc or "") or status in ("owner-stated", "owner law", "owner-law"):
        return ("hint: an owner-stated rule - it binds where it applies; check that it applies here and that no "
                "later ruling replaced it")
    if status == "scar" or re.match(r"\s*(?:\[[A-Z][A-Z ,-]*\]\s*){0,3}\[SCAR", desc or "") or slug.startswith("scar_"):
        return "hint: a scar - a mistake and its rule; check whether the same shape is in play before acting on it"
    if status in ("verified", "measured"):
        return "hint: a %s memory - it may bear on this or it may not; a reminder is a lead, not a verdict" % status
    if status in ("provisional", "hypothesis", "draft", "pre-fix con a", "unverified"):
        return "hint: a %s memory - unverified; it can point the wrong way as easily as the right one" % status
    # older memories carry no status tag; the memory's own type says what kind of thing it is
    if mtype == "feedback":
        return "hint: a feedback memory (a correction or a confirmed way of working) - it binds if this is the same situation"
    if mtype == "project":
        return "hint: a project memory - a fact about ongoing work as of its date; check that it still stands"
    if mtype == "reference":
        return "hint: a reference memory - a pointer to where something lives, not a verdict about it"
    if mtype == "user":
        return "hint: a memory about the owner - stable, but read its date"
    return "hint: status untagged - weigh it against what you see; a reminder is a lead, not a verdict"


def _attachment_context(slug, include_auto=False):
    """Verified attachment context for a surfaced/read node, bounded for the host hook.

    A raw Read of ``slug.md`` is incomplete when the node carries documents.  Small auto-read
    bundles ride directly in the hook context.  A larger bundle gets an exact, lossless part
    command instead of a silently clipped prefix.  Any integrity failure is itself an owner-alert
    condition: the agent must not rely on a parent whose full notes could not be verified.
    """
    try:
        docs = node_docs.summaries(MEM, slug)
        if not docs:
            return ""
        auto_count = sum(1 for doc in docs if doc["auto_read"])
        note = (
            "[brain-node documents] `%s` carries %d verified attached document(s), "
            "%d marked auto-read. A raw parent-file read is not the complete node."
            % (slug, len(docs), auto_count)
        )
        if not include_auto:
            return note + " Read the complete node with `python %s node %s`." % (
                os.path.join(DR, "brain.py"), slug)
        rendered = node_docs.render_auto_documents(MEM, slug)
        if not rendered:
            return note + " Read on-demand documents with `python %s node %s`." % (
                os.path.join(DR, "brain.py"), slug)
        candidate = note + "\n" + rendered
        if len(json.dumps(candidate)) <= HOOK_ATTACHMENT_JSON_CHARS:
            return candidate
        parts = node_docs.split_rendered(rendered, part_chars=6000)
        return (
            note + " The complete auto-read text is too large for one safe hook context; "
            "nothing was partially injected. Immediately read all %d lossless parts, starting "
            "with `python %s node %s --attachments-only --auto-only --part 1 --part-chars 6000`."
            % (len(parts), os.path.join(DR, "brain.py"), slug)
        )
    except Exception as exc:
        return (
            "[BRAIN SELF-CARE ALERT] Attached documents for `%s` could not be verified (%s: %s). "
            "Do not rely on this node as complete. Tell the owner in the first user-visible update."
            % (slug, type(exc).__name__, str(exc)[:300])
        )


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # Discovery is not execution: this script writes, so a help request must
        # exit before any work. See script_help_flag_can_execute_work.
        print(__doc__ or os.path.basename(__file__))
        return
    global _SHARE_LIST
    _SHARE_LIST = True
    try:
        # lstrip the UTF-8 BOM: a PowerShell 5.1 pipe (and any BOM-writing feeder) prepends
        # U+FEFF, json.loads refuses it, and the fail-safe would swallow the whole event -
        # the same class as the cascade hook's BOM mis-parse found 07-15.
        data = json.loads((sys.stdin.read() or "{}").lstrip("﻿"))
    except Exception:
        return
    try:
        ev = data.get("hook_event_name", "")
        idx = _index_lines()
        lex = set()
        topic_words = set()   # the words that decide which of his topics this turn names
        if ev == "UserPromptSubmit":
            prompt = data.get("prompt", "") or ""
            # 2026-09-07: a mathematical argument (a proof under audit) is not the territory of work memories -
            # on the probability bank this lane served trading and audit memories beside proofs, pure noise
            # that cost the solver attention. The proof-audit lane (proof_sense.py) speaks there instead.
            try:
                import exact_clauses as _ec
                if _ec._is_math_argument(prompt):
                    return
            except Exception:
                pass
            # harness noise is not a thought - don't free-associate on it (the n=104 judged replay
            # showed fires on diagnostic pings and malformed-tool echoes; all were junk)
            if prompt.lstrip().startswith(("Diagnostic ping", "Your tool call was malformed",
                                           "[Request interrupted", "API Error")):
                return
            words = _words(prompt)
            topic_words = words
            seeds, lex = _seeds_from_prompt(words, idx, skip_records=True)
        elif ev == "PostToolUse" and data.get("tool_name") == "Read":
            fp = ((data.get("tool_input", {}) or {}).get("file_path", "") or "")
            if not fp.replace("/", os.sep).lower().startswith(MEM.lower()):
                return
            slug = os.path.basename(fp)[:-3] if fp.endswith(".md") else ""
            if not slug or slug in SKIP:
                return
            # a deliberate Read is a strong, single, focused activation - give it enough charge that
            # its strongest associate can clear the bar (a lone seed otherwise disperses over its hub
            # neighbours and nothing surfaces). The margin + salience filters still gate what speaks.
            seeds = {slug: 2.2}
            # an agent reading a memory is working ON that topic - same 3x lane as his prompts
            topic_words = _words(idx.get(slug, "")) | _words(slug.replace("_", " "))
            # PREMISE-CHECK, read-path only (2026-07-09): the session just READ a superseded memory -
            # it is consuming the stale content right now, so the flag is unambiguous by construction.
            # (The prompt-path cousin was replay-evaled the same day and HELD: 8/10 of its fires were
            # weak-topical BM25 noise - eval_premise_check.py carries the verdict. Trace base rate for
            # this path: 6/459 memory reads, 4 of them the pre-correction levered-grid memory.) Emits
            # the correction pointer instead of a generic associate for this event - one output per hook.
            corr = _load_superseded().get(slug)
            if corr and _fresh(data.get("session_id") or "", "stale|" + slug):
                cdesc = _desc(corr, idx)
                msg = (f"[premise-check] `{slug}` (just read) was SUPERSEDED by `{corr}`"
                       + (f": {cdesc}" if cdesc else "")
                       + ". Read the correction before acting on the old version.")
                sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                                                    "additionalContext": msg}}))
                _sess = data.get("session_id") or ""
                if re.match(r"^[0-9a-f]{8}-", _sess):
                    try:
                        with io.open(os.path.join(DR, "surfaced.tsv"), "a", encoding="utf-8") as _sf:
                            _sf.write("%.0f\t%s\t%s\tpremise\n" % (time.time(), _sess[:12], corr))
                    except Exception:
                        pass
                    try:  # conscious->subconscious: prime the gut on the correction just read (fail-safe)
                        sys.path.insert(0, os.path.join(DR, "graph_fire"))
                        import activation_buffer as _ab
                        _ab.deposit(_sess, {corr: 1.0})
                    except Exception:
                        pass
                return
            attachment_context = _attachment_context(slug, include_auto=True)
            if attachment_context:
                sys.stdout.write(json.dumps({"hookSpecificOutput": {
                    "hookEventName": "PostToolUse", "additionalContext": attachment_context}}))
                return
        else:
            return
        if not seeds:
            return
        superseded = _load_superseded()
        current_seeds = {}
        for slug, activation in seeds.items():
            current = superseded.get(slug, slug)
            current_seeds[current] = max(current_seeds.get(current, 0.0), activation)
        seeds = current_seeds
        lex = {superseded.get(slug, slug) for slug in lex}
        adj, deg, deg_base = _hook_graph()
        if not adj:
            return
        mw = _load_mistake_weights()
        sal = _load_salience()
        # the topics THIS text names (his spelling or the corpus's), capped per topic by salience
        (kw_members, kw_aliases, kw_hit, kw_weights,
         kw_related, _kw_generation) = _topic_context(topic_words)
        if kw_members:
            kw_members = _MembersWithoutRecords(kw_members)
        topic = _topic_expand(kw_hit, kw_members, sal) if kw_members else set()
        overlay = _topic_overlay(
            topic_words, sal, kw_members, kw_aliases, kw_hit,
            weights=kw_weights, related=kw_related,
        )
        # SEMANTIC re-rank (only if the box has computed the embedding sidecar; else this whole block is
        # a no-op and behaviour is unchanged). Build the seed cluster's centroid; a candidate's cosine
        # to it measures whether the graph reached something that MEANS the same, not just co-occurs.
        emb = _load_embeddings()
        idx_e = mat = seed_centroid = None
        emb_bias = 0.0
        vector_fresh = {}
        if emb:
            try:
                idx_e, mat, emb_bias = emb

                def _fresh_vector(slug):
                    if slug not in vector_fresh:
                        vector_fresh[slug] = embedding_artifact.embedding_row_current(emb, MEM, slug)
                    return vector_fresh[slug]

                rows = [idx_e[s] for s in seeds if s in idx_e and _fresh_vector(s)]
                if rows:
                    if hasattr(emb, "normalized_centroid"):
                        seed_centroid = emb.normalized_centroid(rows)
                    else:
                        import numpy as np
                        c = mat[rows].mean(axis=0)
                        seed_centroid = c / (float(np.linalg.norm(c)) + 1e-9)
            except Exception:
                seed_centroid = None
        # word-sets of what keyword search ALREADY surfaced (the lexical hits' descriptions). A blind
        # judge caught the real failure mode: the walk can re-surface a memory whose on-prompt content
        # duplicates a lexical hit word-for-word (redundant, not "this reminds me"). So suppress a
        # candidate whose description overlaps an already-surfaced lexical hit's too much.
        lex_wordsets = [w for w in (_words(idx.get(s, "")) for s in lex) if w]
        # stale-fact demotion (Supersede/MemConflict): a memory that carries superseded_by must not be
        # surfaced when its correction is anywhere in play - the session would act on the outdated value.
        # eval_stale_fact verifies the deployed route map against authoritative memory frontmatter.
        habit = _load_habituation()   # cross-session over-surfacing damp (fail-open {})
        description_cache = {}
        candidate_words_cache = {}
        semantic_cosine_cache = {}

        def _cached_desc(slug):
            if slug not in description_cache:
                description_cache[slug] = _desc(slug, idx)
            return description_cache[slug]

        def _candidate_words(slug):
            if slug not in candidate_words_cache:
                candidate_words_cache[slug] = _words(idx.get(slug, "") or _cached_desc(slug))
            return candidate_words_cache[slug]

        def _semantic_cosine(slug):
            if seed_centroid is None or slug not in idx_e:
                return None
            if slug not in semantic_cosine_cache:
                if not _fresh_vector(slug):
                    semantic_cosine_cache[slug] = None
                elif hasattr(emb, "row_dot"):
                    semantic_cosine_cache[slug] = emb.row_dot(idx_e[slug], seed_centroid)
                else:
                    semantic_cosine_cache[slug] = float(mat[idx_e[slug]] @ seed_centroid)
            return semantic_cosine_cache[slug]

        def _select(mw_arm, log_shadow, use_keywords=False):
            """One full walk -> score -> standout-gate pass under a given mistake-weight arm.
            Returns (slug-or-None, parent). Dedup deliberately stays OUTSIDE (applied once to the
            final choice) so running two arms cannot double-stamp state. With mw_arm == {} this is
            exactly the pre-07-25 selection."""
            act, parent = _spread(seeds, adj, deg_base, mw_arm, topic,
                                  use_keywords, overlay)
            act, parent = _collapse_activations(act, parent, superseded)
            cands = []
            for slug, a in act.items():
                # a kw: node is a ROUTER, not a destination - it relays activation between the
                # memories under a subject, but "this reminds me of kw:volume" is not a memory
                if slug.startswith("kw:"):
                    continue
                if slug in seeds or slug in lex or slug in SKIP or a < CAND_FLOOR or SESSION_RECORD.match(slug):
                    continue
                cw = _candidate_words(slug)
                if cw and any(len(cw & lw) / len(cw | lw) > REDUNDANCY_MAX for lw in lex_wordsets):
                    continue   # its content duplicates something the session already sees
                score = a * (0.6 + sal.get(slug, 40.0) / 250.0)
                cos = _semantic_cosine(slug)
                if cos is not None:
                    cos = (cos - emb_bias) / (1.0 - emb_bias)       # self-calibrated: 0 = corpus-typical pair
                    score *= (0.4 + 0.6 * max(0.0, cos))            # damp what's semantically far from the seeds
                sev = mw_arm.get(slug, 0.0)
                score *= (1.0 + MW_SCORE_TILT * sev)                 # a big scar wins ties, never floods
                h = habit.get(slug, 1.0)
                if sev >= MW_SEV_BIG:
                    h = max(h, MW_HABIT_FLOOR)                       # habituation never silences a big scar
                score *= h                                           # cross-session habituation of over-surfacers
                cands.append((score, a, slug))
            if not cands:
                return None, parent
            cands.sort(reverse=True)
            # standout-or-nothing, with one mistake-lane relaxation: a BOOSTED scar that seized a
            # rank and fails a gate yields the floor to the next candidate instead of silencing the
            # pass. With mw_arm empty no candidate is skippable and only cands[0] is ever
            # considered - the old contract, byte for byte.
            for k in range(min(len(cands), 4)):
                score_k, act_k, slug_k = cands[k]
                sev_k = mw_arm.get(slug_k, 0.0)
                second = cands[k + 1][0] if len(cands) > k + 1 else 0.0
                # fire only for a clear, salient standout that carries real graph signal - else silent
                # (CONNECTION_GAIN halves the absolute signal a trigger needs - see the note above)
                if act_k < ACT_FLOOR / CONNECTION_GAIN or sal.get(slug_k, 40.0) < SAL_FLOOR:
                    if sev_k > 0.0:
                        continue
                    return None, parent
                margin_req = MW_MARGIN_BIG if sev_k >= MW_SEV_BIG else MARGIN
                if second > 0 and score_k < margin_req * second:
                    # counterfactual shadow log (2026-07-09): bge's calibrated damp band compresses
                    # top/second margins, so the LSA-era MARGIN=1.35 fires less - but n=7 judged fires
                    # is far too thin to retune on. Log every would-have-fired-at-1.15 case this gate
                    # suppresses - baseline arm, top candidate only, so the ledger keeps its meaning.
                    try:
                        if log_shadow and k == 0 and score_k >= 1.15 * second and re.match(
                                r"^[0-9a-f]{8}-", data.get("session_id") or ""):
                            with io.open(os.path.join(DR, "shadow_fires.tsv"), "a", encoding="utf-8") as _sf:
                                _sf.write("%s\t%s\t%s\t%.3f\t%.3f\t%.3f\n"
                                          % (time.strftime("%Y-%m-%dT%H:%M:%S"),
                                             (data.get("session_id") or "")[:12],
                                             slug_k, score_k, second, score_k / max(second, 1e-9)))
                    except Exception:
                        pass
                    if sev_k > 0.0:
                        continue
                    return None, parent
                return slug_k, parent
            return None, parent

        # Arbitration (2026-07-25, the mistake-first contract, measured in eval_mistake_surfacing.py):
        # the boost may ADD a fire and may SWAP the winner, but must never SILENCE what the unboosted
        # brain would have said - and a swap to a scar the semantic backstop cannot inspect (no fresh
        # sidecar row; 343 missing on 07-25, incl. the newest audit wave) falls back to the baseline
        # choice rather than winning on boost alone. First A/B without these two rules: fire rate
        # x1.53, ordinary fires 24->7, ~12 good fires silenced (one an owner-law memory). With them
        # the baseline is a floor: ON-fires are a superset of OFF-fires up to deliberate swaps.
        sel_b, parent_b = _select({}, True)
        top_slug, parent_used = sel_b, parent_b
        if mw:
            sel_n, parent_n = _select(mw, False)
            # KEYWORD-GRAPH ARM under the same floor: his vocabulary as kw: nodes lets the walk
            # cross from a subject he named to the memories and neighbouring subjects under it.
            # Measured alone it SILENCED 10 good fires per 196 prompts (one an owner law), so it
            # never gets to be the last word - it may only speak where the memory-only graph was
            # already going to speak or was silent. Fires become a superset; losses are zero by
            # construction rather than by luck. No subject named -> empty overlay -> this arm
            # cannot change the answer, so skip the walk instead of paying for it.
            if overlay:
                try:
                    sel_k, parent_k = _select(mw, False, use_keywords=True)
                    if sel_k is not None:
                        sel_n, parent_n = sel_k, parent_k
                except Exception:
                    pass
            if sel_n is not None and sel_n != sel_b:
                if seed_centroid is not None and not (sel_n in idx_e and _fresh_vector(sel_n)):
                    sel_n = None   # uninspectable boost-made winner: keep the baseline choice
                elif sel_b is None and seed_centroid is not None:
                    # a PURE ADDITION must at least MEAN something related: recentred cosine >= 0
                    # is "no less similar than a random corpus pair" - the 07-25 replay's few noise
                    # additions (a trading scar on a novel-illustration prompt) all sat below it
                    try:
                        _cos = _semantic_cosine(sel_n)
                        if (_cos - emb_bias) / (1.0 - emb_bias) < 0.0:
                            sel_n = None
                    except Exception:
                        pass
            if sel_n is not None:
                top_slug, parent_used = sel_n, parent_n
        if top_slug is None:
            return
        if not _fresh(data.get("session_id") or "", top_slug, capped=True):
            # a recently-surfaced boosted winner must not mute a still-fresh baseline fire
            if not (mw and sel_b and top_slug != sel_b and _fresh(data.get("session_id") or "", sel_b, capped=True)):
                return
            top_slug, parent_used = sel_b, parent_b
        bridge = parent_used.get(top_slug, (None, 0))[0]
        desc = _cached_desc(top_slug)
        # name the bridge only when it's a genuine intermediate - a two-hop link the session couldn't
        # see. When the association is one hop off a seed the bridge IS the seed, and naming it is noise.
        via = f" (via `{bridge}`)" if (bridge and bridge not in seeds) else ""
        msg = (f"[association] this reminds me of `{top_slug}`{via} - a memory the keyword match "
               f"wouldn't surface; the association graph puts it near this, which can cut either way")
        if desc:
            msg += f": {desc}"
        msg += ". (" + _status_hint(top_slug, desc) + ")"
        attachment_context = _attachment_context(top_slug, include_auto=False)
        if attachment_context:
            msg += " " + attachment_context
        out_ev = "UserPromptSubmit" if ev == "UserPromptSubmit" else "PostToolUse"
        sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": out_ev,
                                                            "additionalContext": msg}}))
        # durable surface-log for attribution.py (utilization: did this surfacing get used?). Real
        # sessions only (uuid-shaped) so test fixtures don't pollute the ledger - the same guard the
        # dedup state uses (the ledgers-polluted-by-own-gate scar).
        _sess = data.get("session_id") or ""
        if re.match(r"^[0-9a-f]{8}-", _sess):
            try:
                with io.open(os.path.join(DR, "surfaced.tsv"), "a", encoding="utf-8") as _sf:
                    # 5th col (07-25): mistake severity of the fired slug, 0.00 for non-mistake -
                    # readers are positional over cols 0-3 and tolerate the extra column
                    _sf.write("%.0f\t%s\t%s\tspread\t%.2f\n"
                              % (time.time(), _sess[:12], top_slug, mw.get(top_slug, 0.0)))
            except Exception:
                pass
            # conscious -> subconscious bridge (2026-07-11): deposit the consciously-surfaced scar into the
            # shared activation buffer, so the gut (graph_fire/gfcore) is primed to FIRE the reflex anchored
            # on it on the next action. Closes the loop the other way from gfcore.note_fires. Fail-safe.
            try:
                sys.path.insert(0, os.path.join(DR, "graph_fire"))
                import activation_buffer as _ab
                _ab.deposit(_sess, {top_slug: 1.0})
            except Exception:
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
    # The cyclic collector found nothing to free in a ~1 s hook, but once the graph is loaded each pass walked it:
    # 0.737 -> 0.661 s CPU per prompt with it paused (1 Nov 2026, 5 of 5 pairs). Re-enabled after, since the
    # dispatcher may run other hooks in this same process.
    import gc as _gc
    _gc.disable()
    try:
        main()
    finally:
        _gc.enable()
