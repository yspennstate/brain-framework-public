#!/usr/bin/env python3
"""dream.py - generate tonight's dream agenda for the brain.

Mechanical half of the dreaming system. Scans the live memory corpus and emits
DREAM_AGENDA.md: what tonight's REM session (a headless Claude) should replay,
consolidate, reconcile, and check. No LLM here - just selection heuristics that
mimic sleep pressure: recency, similarity-without-connection, contradiction,
staleness, and a dash of random replay.

Usage: python dream.py [memory_dir] [out_path]
"""
import os, re, sys, io, random, time
from collections import defaultdict, Counter
from memory_date import trusted_birth, trusted_recency_key, trusted_reference
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


if any(a in ("-h", "--help") for a in sys.argv[1:]):
    # Discovery is not execution: this file does its work at module scope, so the
    # guard must come before the first statement that runs.
    print(__doc__ or os.path.basename(__file__))
    raise SystemExit(0)

try:
    import embedding_artifact
except Exception:
    embedding_artifact = None

# Claude Code names a project directory after the home path, with ':' and the
# separators replaced by '-'. Deriving it keeps this tree free of one machine's
# username and correct on any other - and unlike a template token it is a real
# value at import time.
_BRAIN_SLUG = _brain_slug_resolve()
MEM = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.expanduser("~"), ".claude", "projects", _BRAIN_SLUG, "memory")
OUT = sys.argv[2] if len(sys.argv) > 2 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "DREAM_AGENDA.md")
AGE_AS_OF = os.environ.get("BRAIN_AGE_AS_OF") or None
AGE_TIMEZONE = os.environ.get("BRAIN_TIMEZONE", "UTC")
AGE_REFERENCE_EPOCH, AGE_CUTOFF = trusted_reference(
    as_of=AGE_AS_OF, timezone=AGE_TIMEZONE)

# Different kinds of dreaming, grounded in sleep architecture (Diekelmann-Born 2010; Giuditta's
# sequential hypothesis 1995): consolidation (SWS) precedes and feeds creative recombination (REM).
# run_dream.ps1 sets DREAM_TYPE by hour; the agenda declares it so the REM session works the right pass.
TYPE = os.environ.get("DREAM_TYPE", "mixed").lower()
DREAM_KINDS = {
    "sws": "SWS / consolidation - 'the filer'. Digest the day into existing structure: sections A-D "
           "(consolidate recent, make the missed associations, reconcile stale beliefs, wire dangling "
           "links). NO creative play tonight - the bisociation is the REM pass's job. File cleanly.",
    "rem": "REM / creative - 'the connector'. The filing is done (the dawn SWS pass handled it). Work "
           "sections E-G: replay from the unconscious, sit with what carried feeling, and try the "
           "bisociation (favor the most distant pairs). This is where cross-domain transfer lives.",
    "nap": "Nap - light maintenance. A short pass: consolidate only the freshest (section A), resolve "
           "any due predictions, and stop. No deep reaching, no bisociation - keep it brief and cheap.",
    "mixed": "Mixed - consolidate first (A-D), then play (E-G). The full agenda (the default).",
}

FM_DESC = re.compile(r"^description:\s*(.+)$", re.M)
WIKI = re.compile(r"\[\[([^\]\|#]+)\]\]")
DATE_F = re.compile(r"(202[4-7])[_-](0[1-9]|1[0-2])[_-]?(\d{2})?")
STOP = set("the a an and or of to in for on with is are was were be this that it its as at by from into over per not no new one two use used using via etc when then than but our we you your all any each more most some such only same now day days week month year".split())

# Facts that are CURRENT as of 2026-07-07. Memories asserting the superseded
# version are staleness candidates. (entity, current_truth, since_date, stale_markers)
# since_date: memories dated on/after this ARE the current record - never flag them.
CURRENT_FACTS = [
    ("model default", "Fable 5 / Opus 4.8 era (2026-07)", "2026-07-01", [r"opus 4\.5", r"claude 3\.5"]),
    ("agentA size", "5 research agents (trimmed 2026-07-07)", "2026-07-07", [r"10 headless", r"ten sessions"]),
]

def read(p):
    try:
        with io.open(p, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except Exception:
        return ""

mems = {}
now = AGE_REFERENCE_EPOCH
for fn in os.listdir(MEM):
    if not fn.endswith(".md") or fn in ("MEMORY.md", "INDEX_ARCHIVE.md"):
        continue
    p = os.path.join(MEM, fn)
    text = read(p)
    desc = FM_DESC.search(text)
    desc = desc.group(1).strip() if desc else ""
    m = DATE_F.search(fn) or DATE_F.search(desc)
    event_date = f"{m.group(1)}-{m.group(2)}-{m.group(3) or '01'}" if m else None
    if event_date and event_date > time.strftime("%Y-%m-%d"):
        event_date = None  # future date = an expiry/deadline in the text, not when this happened
    birth_epoch, birth_source = trusted_birth(
        fn[:-3], text[:1600], as_of=AGE_CUTOFF, timezone=AGE_TIMEZONE)
    toks = set(t for t in re.findall(r"[a-z][a-z0-9_]{2,}", (fn + " " + desc).lower()) if t not in STOP)
    mems[fn] = {
        "desc": desc, "event_date": event_date,
        "birth_epoch": birth_epoch, "birth_source": birth_source,
        "links": set(WIKI.findall(text)), "toks": toks, "words": len(text.split()),
        "body": text,
    }

# canonical link resolution: memories are dated files (foo_2026_07_07.md) but
# wiki-links often drop the date or swap -/_. Normalize both sides before deciding
# a link dangles or two memories are "unconnected". (Bug the 07-07 dream caught.)
_DATE_TAIL = re.compile(r"[_-]?20\d\d[_-]\d\d(?:[_-]\d\d)?$")
def _norm(name):
    n = name.strip().lower().rstrip(".md")
    n = _DATE_TAIL.sub("", n)
    return n.replace("-", "_")

# Resolve explicit corrections before any selector runs. Historical files stay on
# disk, but active dream material contains only each chain's existing endpoint.
_all_names_noext = {fn[:-3] for fn in mems}
# dreams/-resident journals are real link targets even though they never enter the
# agenda's mems selection (the flat scan is deliberate for SELECTION; existence is a
# different question - the gap the 07-30 dreams kept re-flagging as dangling).
_subdir_names = set()
_dreams_dir = os.path.join(MEM, "dreams")
if os.path.isdir(_dreams_dir):
    _subdir_names = {fn[:-3] for fn in os.listdir(_dreams_dir) if fn.endswith(".md")}
_all_names_noext |= _subdir_names
_all_canon = {}
for fn in mems:
    _all_canon.setdefault(_norm(fn[:-3]), fn[:-3])
for _n in sorted(_subdir_names):
    _all_canon.setdefault(_norm(_n), _n)
superseded = active_supersessions(
    MEM, known_slugs=_all_names_noext, include_frontmatter=True)
superseded_hidden = len(superseded)
mems = {fn: m for fn, m in mems.items() if fn[:-3] not in superseded}
names_noext = {fn[:-3] for fn in mems}

_canon = {}
for fn in mems:
    _canon.setdefault(_norm(fn[:-3]), fn[:-3])
def resolve(target):
    """Return the real memory basename a [[link]] points at, or None if truly missing."""
    t = target.strip()
    candidate = t if t in _all_names_noext else _all_canon.get(_norm(t))
    if not candidate:
        return None
    candidate = superseded.get(candidate, candidate)
    return candidate if (candidate in names_noext or candidate in _subdir_names) else None

# transient agent-scratch (round/cycle/agent/loop checkpoints, session logs) is not
# durable memory — keep it in the corpus but never let it dominate the dream agenda.
_TRANSIENT = re.compile(r"^(transient_|codex_round\d|.*round\d+.*cycle\d+.*agent|.*_loop\d+)", re.I)
def is_transient(fn):
    if _TRANSIENT.search(fn):
        return True
    return "type: transient" in mems[fn].get("body", "").lower()[:400]
for fn in mems:  # cache
    mems[fn]["transient"] = is_transient(fn)

# ---- affective salience (optional) ----
# A human mind weights memories: what mattered stays vivid, the flat middle fades, the
# charged stuff (scars, wins) stays sharp. salience.py scores every memory + tags affect
# (valence good/bad, charge how intense). Used below to pick emotionally-weighted replay.
# Graceful: if salience.py is missing or throws, the dream runs exactly as before.
try:
    import salience as _sal
    SAL = _sal.score_corpus(MEM)   # {fname: {salience, valence, charge, why, ...}}
except Exception:
    SAL = {}

# ---- 1. recent memories -> consolidation fodder ----
# Encoding/birth time is not the event a description discusses. Only immutable created/full-filename
# evidence is allowed here; an unknown birth is neither recent nor old. This keeps a repo sync or a
# historical date in prose from manufacturing consolidation pressure.
def is_recent(m):
    return m["birth_epoch"] is not None and now - m["birth_epoch"] <= 8*86400
# Birth is the created day's noon, so a whole day ties and the name tiebreak picked section A's 30: on 1 Nov 2026
# the newest day held 788 memories and the 30 shown were all a_* slugs. Inside a tie, newest created_at goes first
# (birth and age stay as they are). Measured over 8 day-ends: a_* slugs in the 30 shown 184 -> 47 of 240, adjacent
# topic changes 44 -> 208 of 232 (1000-ways D3 #79, porting/w08_checks/d3_79_interleaving.py).
_CREATED_AT = re.compile(r"^\s*created_at:\s*[\"']?(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)", re.M)
def created_at_epoch(m):
    hit = _CREATED_AT.search(m["body"][:1600])
    if hit:
        try:
            import datetime as _dtm
            return _dtm.datetime.fromisoformat(hit.group(1).replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    return 0.0
def recent_key(fn):
    k = trusted_recency_key(fn, mems[fn]["birth_epoch"])
    return (k[0], k[1], -created_at_epoch(mems[fn]), k[2])
recent = sorted((fn for fn, m in mems.items() if is_recent(m) and not m["transient"]), key=recent_key)
transient_recent = sum(1 for fn, m in mems.items() if is_recent(m) and m["transient"])

# pairs co-cited by ONE connector note (daydream_*/gist_*): a daydream that links [[A]] and [[B]] in
# its own body IS the association - but A,B then sit 2 hops apart (both link FROM the note), so the
# generators below re-proposed such pairs as novel (filed by the 07-09 evening REM2 dream, which
# re-proposed a pairing a prior REM had already made). Measured 2026-07-09 before wiring: 24
# connector notes, 678 co-cited pairs, tonight's agenda 0/13 phantom - a recurrence guard, not a hot fix.
cocited = set()
for fn, m in mems.items():
    base = fn[:-3]
    if not base.startswith(("daydream_", "gist_", "dream_")):
        continue
    tg = sorted(t for t in {resolve(l) for l in m["links"]} if t and t != base)
    for i in range(len(tg)):
        for j in range(i + 1, len(tg)):
            cocited.add((tg[i], tg[j]))

# ---- 2. similar-but-unlinked pairs (association candidates) ----
inv = defaultdict(set)
for fn, m in mems.items():
    for t in m["toks"]:
        inv[t].add(fn)
cap = max(3, int(0.10 * len(mems)))
pairs = Counter()
for t, fs in inv.items():
    if 2 <= len(fs) <= 12:
        fs = sorted(fs)
        for i in range(len(fs)):
            for j in range(i+1, len(fs)):
                # 31 Oct: same-series pairs (a session's compaction chain, a catalogue's cards) took 395 of the
                # best 600 and all 175 candidates; skip them before the cut (first 12 characters agree, as in 2b)
                if len(fs[i]) >= 15 and len(fs[j]) >= 15 and fs[i][:12] == fs[j][:12]:
                    continue
                pairs[(fs[i], fs[j])] += 1
assoc = []
for (a, b), shared in pairs.most_common(600):
    if shared < 3:
        break
    if mems[a]["transient"] or mems[b]["transient"]:
        continue
    a_targets = {resolve(l) for l in mems[a]["links"]}
    b_targets = {resolve(l) for l in mems[b]["links"]}
    if b in a_targets or b[:-3] in a_targets or a in b_targets or a[:-3] in b_targets:
        continue  # already connected (via resolved links, date-insensitive)
    if tuple(sorted((a[:-3], b[:-3]))) in cocited:
        continue  # one daydream/gist already cites both - the pairing exists, just 2 hops apart
    jac = shared / (len(mems[a]["toks"] | mems[b]["toks"]) or 1)
    # 0.17 was loosened from 0.22 (too strict for the grown corpus). assoc=0 on 07-08 was PROBED,
    # not guessed at: at a 0.05 floor the best unlinked pair sat at 0.167 and 12 of the top 15 were
    # same-series May checkpoint files (arnold_diffusion_cycle*) whose similarity is boilerplate -
    # so an empty list was the honest answer, and the two real candidates below threshold got
    # hand-linked instead. Do NOT blind-lower this; re-probe (patch a copy's floor to 0.05 and read
    # the candidates) before believing the threshold is the problem.
    if jac >= 0.17:
        assoc.append((jac, a, b))
assoc.sort(reverse=True)

# ---- 2b. semantic association candidates (the sidecar sees below the token floor) ----
# The token-Jaccard detector above honestly bottomed out (assoc=0 on 07-08 was probed: the real
# associations sit BELOW the boilerplate floor, so tokens can't reach them). The embedding sidecar
# (box-built bge-small, 2026-07-09, cleared all three eval gates) ranks similar-but-unlinked pairs
# by MEANING instead. Probed before wiring: 45 top candidates read by hand, ~90% precision above
# 0.72 calibrated cosine once same-series slug-prefix pairs are dropped; the misses were exactly
# the boilerplate the prefix filter now catches. Fail-safe: no sidecar / no numpy -> empty list,
# agenda unchanged.
def _top_sem_pairs(V, cut, pid, codes, keep=4000, block=1024):
    """Pairs i < j with V[i].V[j] >= cut, best `keep` by dot, as arrays (I, J, D).

    Row blocks over columns j >= i, so no full Gram is built (over ~50k rows it was 9.9 GB a copy plus 19.7 GB of
    triu indices, and its MemoryError emptied the list every night). The filters run INSIDE the loop: pairs of one
    series (equal pid) and pairs whose code i*n+j is in `codes` (sorted, unique: already linked, co-cited or
    wiki-linked) are dropped before the top `keep` is taken. Keeping the top first and filtering after returned
    only near-duplicates and already-linked pairs, and so no association at all. Ties go row-major."""
    import numpy as _np
    n = len(V)
    I = _np.empty(0, _np.int64); J = _np.empty(0, _np.int64); D = _np.empty(0, V.dtype)
    for s0 in range(0, n, block):
        blk = V[s0:s0 + block] @ V[s0:].T
        r, cc = _np.nonzero(blk >= cut)
        m = cc > r
        r, cc = r[m], cc[m]
        gi, gj = s0 + r, s0 + cc
        m = pid[gi] != pid[gj]
        r, cc, gi, gj = r[m], cc[m], gi[m], gj[m]
        if len(codes) and len(r):
            # binary search is an exact membership test on a sorted unique array; np.isin re-sorts it per block
            q = gi * n + gj
            pos = _np.searchsorted(codes, q)
            pos[pos == len(codes)] = 0
            m = codes[pos] != q
            r, cc = r[m], cc[m]
        I = _np.concatenate([I, s0 + r]); J = _np.concatenate([J, s0 + cc])
        D = _np.concatenate([D, blk[r, cc]])
        if len(D) > 4 * keep:
            kth = _np.partition(D, len(D) - keep)[len(D) - keep]
            m = D >= kth
            I, J, D = I[m], J[m], D[m]
            cut = max(cut, kth)
        del blk
    return I, J, D


sem_assoc = []
try:
    import numpy as _np
    if embedding_artifact is None:
        raise ImportError("embedding artifact verifier unavailable")
    _dr = os.path.dirname(os.path.abspath(__file__))
    _loaded = embedding_artifact.load_verified_embeddings(
        os.path.join(_dr, "memory_embeddings.npz"),
        os.path.join(_dr, ".embedding_current.json"),
        MEM,
        "BAAI/bge-small-en-v1.5",
        verify_all_rows=True,
    )
    if _loaded is None:
        raise ValueError("no verified embedding authority")
    _emb_idx, _vecs, _bias = _loaded
    _slugs = [None] * len(_vecs)
    for _slug, _row in _emb_idx.items():
        _slugs[_row] = _slug + ".md"
    if 0.0 <= _bias < 0.95:
        _linked = set()               # graph edges (wiki-links + co-reads) = already associated
        with io.open(os.path.join(_dr, "spread_edges.tsv"), encoding="utf-8", errors="replace") as _f:
            next(_f, None)
            for _ln in _f:
                _p = _ln.rstrip("\n").split("\t")
                if len(_p) >= 2:
                    _linked.add(tuple(sorted((_p[0], _p[1]))))
        _tier = {}                    # unconscious tier = log-substrate stubs; keep them out
        try:
            with io.open(os.path.join(_dr, "consciousness.tsv"), encoding="utf-8", errors="replace") as _f:
                next(_f, None)
                for _ln in _f:
                    _p = _ln.rstrip("\n").split("\t")
                    if len(_p) >= 2:
                        _tier[_p[0]] = _p[1]
        except Exception:
            pass

        def _same_series(x, y):
            n = 0
            for cx, cy in zip(x, y):
                if cx != cy:
                    break
                n += 1
            return n >= 12

        _keep = [i for i, s in enumerate(_slugs)
                 if s in mems and not mems[s]["transient"] and _tier.get(s) != "unconscious"]
        if _keep:
            _V = _vecs[_keep]
            # Row blocks over columns j >= i, keeping only the best 4000 pairs above the cut. The full
            # Gram over ~50k rows was 9.9 GB a copy plus 19.7 GB of triu indices; its MemoryError fell
            # into the except below, so this list came back empty every night (found 3 Nov 2026).
            # Same pair set as the full matrix (float32 noise ~2e-6); ties go row-major.
            _cut = _np.float32(0.72 * (1.0 - _bias) + _bias)       # cal >= 0.72  <=>  dot >= _cut
            # same-series = the 12-char prefix id; linked / co-cited / wiki-linked = sorted pair codes i*n+j
            _n = len(_keep)
            _names = [_slugs[k][:-3] for k in _keep]
            _pos = {nm: q for q, nm in enumerate(_names)}
            _pfx = {}
            _pid = _np.array([_pfx.setdefault(nm[:12], len(_pfx)) if len(nm) >= 12 else -1 - q
                              for q, nm in enumerate(_names)], dtype=_np.int64)
            _codes = []

            def _addpair(x, y):
                qx, qy = _pos.get(x), _pos.get(y)
                if qx is not None and qy is not None and qx != qy:
                    _codes.append(min(qx, qy) * _n + max(qx, qy))
            for _x, _y in _linked:
                _addpair(_x, _y)
            for _x, _y in cocited:
                _addpair(_x, _y)
            for _q, _k in enumerate(_keep):
                for _l in mems[_slugs[_k]]["links"]:
                    _t = resolve(_l)
                    if _t:
                        _addpair(_names[_q], _t)
            _codes = _np.unique(_np.array(_codes, dtype=_np.int64))
            _I, _J, _D = _top_sem_pairs(_V, _cut, _pid, _codes)
            _C = (_D - _bias) / (1.0 - _bias)
            for _k in _np.lexsort((_J, _I, -_C))[:4000]:
                _i, _j = int(_I[_k]), int(_J[_k])
                _c = float(_C[_k])
                if _c < 0.72 or len(sem_assoc) >= 10:
                    break
                _a, _b = _slugs[_keep[_i]], _slugs[_keep[_j]]
                _an, _bn = _a[:-3], _b[:-3]
                if tuple(sorted((_an, _bn))) in _linked or _same_series(_an, _bn):
                    continue
                if tuple(sorted((_an, _bn))) in cocited:
                    continue      # co-cited by one daydream/gist connector = already associated
                _at = {resolve(l) for l in mems[_a]["links"]}
                _bt = {resolve(l) for l in mems[_b]["links"]}
                if _bn in _at or _an in _bt:
                    continue
                sem_assoc.append((_c, _a, _b))
except Exception:
    sem_assoc = []

# ---- 3. staleness candidates ----
stale = []


def _lit_gate(p):
    """A lowercase literal every match of marker p must contain ("" = no safe gate). Conservative: any
    alternation or character class gives no gate; group contents (lookaheads included) are ignored; a
    character made optional by ? * {..} is dropped. Measured 3 Nov: the sweep 43.0 s -> 5.4 s, same 52 hits,
    because a re.I search over 223 MB per marker loses the literal fast path."""
    q = re.sub(r"\((?:[^()\\]|\\.)*\)", "\x00", p)
    if "|" in q.replace("\\|", "") or "(" in q or "[" in q:
        return ""
    best, cur, i = "", [], 0
    while i <= len(q):
        ch = q[i] if i < len(q) else "\x00"
        if ch == "\\" and i + 1 < len(q) and not q[i + 1].isalnum():
            cur.append(q[i + 1]); i += 2; continue
        if ch == "\\" or ch in ".^$*+?{}\x00":
            if ch in "*?{" and cur:
                cur.pop()
            if len(cur) > len(best):
                best = "".join(cur)
            cur = []
            if ch == "{":
                j = q.find("}", i)
                i = len(q) if j < 0 else j + 1
            else:
                i += 2 if ch == "\\" else 1
            continue
        cur.append(ch); i += 1
    return best.lower()


_STALE_MK = {mk: (_lit_gate(mk), re.compile(mk, re.I)) for _e, _t, _s, _mks in CURRENT_FACTS for mk in _mks}
for fn, m in mems.items():
    hay = m["body"].lower()
    # already reconciled? a prior dream annotated it - don't re-flag (the 07-07 dream's find)
    if "superseded" in hay or "⚠️" in m["body"] or m["transient"]:
        continue
    for entity, truth, since, markers in CURRENT_FACTS:
        # a memory dated on/after the fact became current IS the current record
        if m["event_date"] and m["event_date"] >= since:
            continue
        if any(_STALE_MK[mk][0] in hay and _STALE_MK[mk][1].search(hay) for mk in markers):
            stale.append((fn, entity, truth))
            break

# ---- 4. dangling links (memories wanted but never written) ----
# Report only slug-shaped wants. Prose that merely contains double brackets -
# JSON fragments, [[X]]-style placeholders, wildcards, bare numbers, and
# meta-words like [[links]] - is tokenizer noise, not a memory the brain wants
# (the 08-06 19:30 agenda's top ten was nine parts this junk). Filtered at the
# REPORT only: extraction, association edges, and orphan logic are untouched.
# 31 Oct (q1676): every memory name holds an underscore or a dash (the seven that do not, MEMORY,
# MISTAKES and the like, always resolve), so a separator is required; that and the template names
# below dropped 16 of the 25 targets only the agenda counted (memory, target, wiki-link, memo_name...).
_LINK_NOISE = {"link", "links", "name", "names", "subject", "slug", "slugs",
               "their_slug", "todo", "tbd", "memo_name", "memo-name", "other-memory-name",
               "their_name", "slug_name", "wiki-link", "wiki-links"}
def _sluglike(t):
    t = t.strip()
    if t.lower() in _LINK_NOISE:
        return False
    return bool(re.match(r"^[A-Za-z][A-Za-z0-9_-]{3,}$", t)) and bool(re.search(r"[_-]", t))
dangling = Counter()
for fn, m in mems.items():
    for l in m["links"]:
        if resolve(l) is None and _sluglike(l):
            dangling[l] += 1

# ---- 5. random replay: the dream reaching into the UNCONSCIOUS ----
# In a mind, sleep is where latent, un-recalled material surfaces on its own. So replay
# draws mostly from the unconscious tier (consciousness.py) - the substrate the wake ritual
# and the salience list step over - plus a few true orphans. Reading it back is how the
# dream keeps the substrate alive: most gets let lie, but now and then one still matters and
# earns a link back up into conscious memory. Degrades to the old age-weighted sample if the
# tier sidecar isn't there yet.
random.seed()  # true randomness each night - dreams differ
old = [fn for fn, m in mems.items()
       if m["birth_epoch"] is not None and now - m["birth_epoch"] > 30*86400]
linked_to = set()
for m in mems.values():
    linked_to |= m["links"]
orphans = [fn for fn in mems if fn[:-3] not in linked_to and not mems[fn]["links"]]

unconscious = []
try:
    _cs = os.path.join(os.path.dirname(os.path.abspath(__file__)), "consciousness.tsv")
    with io.open(_cs, "r", encoding="utf-8") as _cf:
        next(_cf, None)  # header
        for _ln in _cf:
            _p = _ln.rstrip("\n").split("\t")
            if len(_p) >= 2 and _p[1] == "unconscious" and _p[0] in mems:
                unconscious.append(_p[0])
except Exception:
    unconscious = []

# prioritized replay (Mattar-Daw 2018 - replay is ordered by gain x NEED, not uniform): bias the draw
# toward the unconscious memories that overlap the day's live concerns, with jitter so it still varies
# night to night and keeps REM's breadth.
concern = set()
for fn in recent[:20]:
    concern |= mems[fn]["toks"]

def _cohort(fn):
    # worker-numbered families (inspector_W*, build_watchdog_W*) are ONE cohort
    # regardless of date: the unconscious tier is 200-of-204 those two families
    # (measured 2026-08-06), which is exactly the May-25 quiet-ping flood four
    # dream shifts complained about (07-19..07-23). Other names keep their stem.
    stem = fn[:-3] if fn.endswith(".md") else fn
    m = re.match(r"([A-Za-z_]+?)_W\d+", stem)
    if m:
        return m.group(1)
    return re.sub(r"_?\d{4}_\d{2}_\d{2}$", "", stem)[:24]

if unconscious:
    ranked = sorted(unconscious,
                    key=lambda fn: len(mems[fn]["toks"] & concern) * (0.7 + 0.6 * random.random()),
                    reverse=True)
    # prioritized order with a hard cap of 2 per family and NO backfill: on a
    # homogeneous tier, fewer diverse slots beat nine copies of the same
    # quiet-ping (the de-cluttering the shifts asked for). Section E length
    # varies with tier diversity by design.
    picks, _seen = [], Counter()
    for fn in ranked:
        c = _cohort(fn)
        if _seen[c] >= 2:
            continue
        _seen[c] += 1
        picks.append(fn)
        if len(picks) == 9:
            break
    # the orphan tail honors the same family counter - a capped family must not
    # re-enter section E through the orphan door.
    _orph = []
    for fn in random.sample(orphans, min(len(orphans), 12)):
        c = _cohort(fn)
        if _seen[c] >= 2:
            continue
        _seen[c] += 1
        _orph.append(fn)
        if len(_orph) == 4:
            break
    replay = picks + _orph
else:
    replay = random.sample(old, min(8, len(old))) + random.sample(orphans, min(5, len(orphans)))

# ---- 5b. transition-band age replay (OFF by default; set BRAIN_REPLAY_BAND=1 to A/B - the diffusion notes
# section 8.2). The draw above is age-agnostic (concern-overlap) or uniform-over-old; neither replays the
# episodic->semantic CONSOLIDATION BOUNDARY. This adds up to 3 mid-age picks weighted by a log-Gaussian
# band centred ~5d (center/width are the A/B knobs, illustrative), COMPLEMENTING not replacing the draw
# above. Shadow-demonstrated in replay_schedule_shadow.py. Zero effect unless the env var is set.
if os.environ.get("BRAIN_REPLAY_BAND") == "1":
    import math as _math
    import sys as _sys
    _band = []
    for _fn, _m in mems.items():
        _be = _m.get("birth_epoch")
        if _be is None:
            continue
        _age = (now - _be) / 86400.0
        if _age <= 0:
            continue
        _w = _math.exp(-((_math.log(_age + 1.0) - _math.log(5.0)) ** 2) / (2 * 0.9 ** 2))
        _band.append((_w, _fn))
    _band.sort(reverse=True)
    _added = 0
    for _w, _fn in _band:
        if _added >= 3:
            break
        if _fn not in replay:
            replay.append(_fn)
            _added += 1
    print("[replay-band] BRAIN_REPLAY_BAND on: added %d mid-age (~5d band) picks" % _added, file=_sys.stderr)

# affective replay: a scar and a win worth sitting with tonight. Human dreams replay the
# charged stuff, not a flat sample. Draw from high-salience, high-charge memories — one of
# each valence — with a little randomness among the top so dreams differ night to night.
scar = win = None
if SAL:
    charged = [(fn, SAL[fn]) for fn in mems if fn in SAL and SAL[fn]["charge"] >= 0.5]
    scars = sorted((c for c in charged if c[1]["valence"] <= -0.34), key=lambda c: c[1]["salience"], reverse=True)
    wins  = sorted((c for c in charged if c[1]["valence"] >=  0.34), key=lambda c: c[1]["salience"], reverse=True)
    if scars: scar = random.choice(scars[:12])[0]
    if wins:  win  = random.choice(wins[:12])[0]

# ---- 6. imaginative seed (bisociation: force a link between distant, salient things) ----
# Section B finds memories that BELONG together. This finds two that DON'T — both vivid,
# from different corners, almost nothing in common — on purpose. Human dreams make absurd
# juxtapositions; now and then one is a real idea (math<->trading transfer lives here).
# The REM decides if there's anything to it; most nights there isn't, and that's fine.
distant = []
if SAL:
    top_sal = sorted((fn for fn in mems if fn in SAL and len(mems[fn]["toks"]) >= 6),
                     key=lambda fn: SAL[fn]["salience"], reverse=True)[:55]
    pool = random.sample(top_sal, min(30, len(top_sal)))   # vary the pairs night to night
    cand = []
    for i in range(len(pool)):
        for j in range(i + 1, len(pool)):
            a, b = pool[i], pool[j]
            ta, tb = mems[a]["toks"], mems[b]["toks"]
            jac = len(ta & tb) / (len(ta | tb) or 1)
            cand.append((jac, a, b))
    cand.sort()   # most-distant first
    used = set()  # top 3 distant, non-overlapping pairs -> the deeper dream can try a second
    for jac, a, b in cand:
        if a in used or b in used:
            continue
        distant.append((a, b)); used.add(a); used.add(b)
        if len(distant) >= 3:
            break

# ---- his corrections on days the classifier never reached ----
# The M-section below reads class_model=="mistake". That is the careful label, and it is clean
# (measured 2026-08-07: 637 rows, zero machine-authored). But the classifying CHORE runs in the
# dream, so a night that defers it leaves whole days with no class_model at all - and an
# unclassified day is INVISIBLE to corrections consolidation. Measured that morning: coverage was
# 100% through 07-30, then 15-21% for 07-31..08-04, and 0 of 95 rows on 08-06 - so the newest
# corrections, including the one that reversed a shipped legal document, could not reach the dream
# that was supposed to learn from them. MISTAKES.md went stale for exactly those days.
# Fix: never let the loop go blind on a chore backlog. Lexically surface corrections from recent
# UNCLASSIFIED rows too, conservatively (his own correction vocabulary), labelled by provenance so
# the dream still knows which ones carry a real verdict.
_CORRECTION_TELLS = re.compile(
    r"^\s*no[,.\s]|\byou sure\b|\bcheck again\b|\bplease check\b|\bche?k the math\b"
    r"|\bi never (?:said|told|asked|specified)\b|\bi thought i (?:asked|told)\b"
    r"|\bi though?t? it (?:was|were)\b|\b(?:is|was|that'?s) wrong\b|\bnot what i (?:wanted|asked)\b"
    r"|\bwtf\b|\bcome on\b|\bwhy did(?:n'?t)? you\b|\bdon'?t do\b|\bovercomplicat"
    r"|\bbefore (?:it was|you said)\b|\bnow you are saying\b|\bkeep happening\b"
    # 31 Oct 2026 (open question 1947): none of his ~7 corrections on the Mac (29-30 Oct) matched the list above,
    # so the 4-day window fell back to 14-15 Oct. Added from them; on 8,132 older labelled heads the additions
    # fire 87 more times at about 70% correct (recall 22.4 -> 26.8%, precision 59.5 -> 61.0%).
    r"|\bwhy (?:are|aren'?t|do|don'?t|did|didn'?t|is|isn'?t|were|was) (?:you|u|they|it|we)\b[^?.\n]{0,60}"
    r"(?:not|never|stop|idle|still|again|wrong)"
    r"|\bwh?y? doi? you ever\b|\bi (?:said|told you|asked)(?: you)? (?:multiple|many|several|\d+) times?\b"
    r"|\bi already (?:said|told|asked)\b|\bdo it (?:correctly|right|properly)\b|\bthat is (?:always )?a mistake\b"
    r"|\bi (?:do ?n'?t|dont|didn'?t) see (?:any|the|terminal|it|them|that)\b|\bstop asking me\b"
    r"|\bwhy (?:is|are) (?:it|this|that|they) (?:not|still)\b|\byou (?:forgot|missed|ignored|broke)\b"
    r"|\bthat'?s not (?:right|true|it|correct)\b|\bit (?:did not|didn'?t|does not|doesn'?t) work\b", re.I)
_MACHINE_PROMPT = re.compile(
    r"^\s*\[FABLE-1|WATCHDOG, owner directive|^\s*/goal loop wake|Overnight watchdog wake"
    r"|^\s*\[work-session|^\s*\[SYSTEM|^\s*\[campaign|^\s*\[AGENT|10-min cycle\]", re.I)


from correction_ingestion_status import recorded_origin, explicit_correction_candidate


def _unclassified_corrections(path, days=4, limit=6):
    """Recorded-owner rows with an explicit correction flag, or unclassified correction tells,
    inside the last `days` calendar days present in the archive. A false positive
    costs the dream one line of reading, a false negative costs a lost correction."""
    import json as _j
    rows = []
    try:
        for ln in io.open(path, encoding="utf-8", errors="replace"):
            try:
                r = _j.loads(ln)
            except Exception:
                continue
            if recorded_origin(r) != "owner":
                continue
            if r.get("class_model") and not explicit_correction_candidate(r):
                continue
            t = r.get("text") or ""
            if not isinstance(t, str) or not t or _MACHINE_PROMPT.search(t) or t.lstrip().startswith("/"):
                continue
            if explicit_correction_candidate(r) or _CORRECTION_TELLS.search(t):
                rows.append(r)
    except Exception:
        return []
    if not rows:
        return []
    rows.sort(key=lambda r: r.get("ts") or "")
    recent = sorted({(r.get("ts") or "")[:10] for r in rows})[-days:]
    return [r for r in rows if (r.get("ts") or "")[:10] in recent][-limit:]


# ---- emit agenda ----
with io.open(OUT, "w", encoding="utf-8") as f:
    f.write(f"# Dream agenda - {time.strftime('%Y-%m-%d %H:%M')}  [{TYPE.upper()} dream]\n\n")
    f.write(f"**Tonight is a {TYPE.upper()} dream.** {DREAM_KINDS.get(TYPE, DREAM_KINDS['mixed'])}\n\n")
    _focus = read(os.path.join(os.path.dirname(os.path.abspath(__file__)), "DREAM_FOCUS.md")).strip()
    if _focus:
        f.write("## Tonight's focus (from the owner or a waking session - weight the dream toward this, "
                "without ignoring the rest)\n\n" + _focus + "\n\n")
    f.write(f"Corpus: {len(mems)} memories. Pick 3-5 items below and go genuinely deep - "
            f"reach across the whole brain, not just the recent pile; depth beats coverage.\n")

    # ---- Requested dreams (owner practice since 07-08: "put it in the brain and dream about it").
    # Waking sessions queue topics via `brain.py dream-about`; render pending ones and mark them
    # consumed. Guarded - a failure here must never break agenda generation.
    try:
        import dream_requests as _dreq
        _rsec = _dreq.agenda_section(time.strftime("%Y-%m-%d %H:%M"))
        if _rsec:
            f.write("\n" + _rsec)
    except Exception:
        pass

    # ---- Strong-node growth (owner 2026-07-24): exact mechanisms recurring
    # >10 times must enter a designated connection-agent workflow during
    # dreaming. The helper revalidates the signed production generation before
    # exposing candidates; any failure is rendered as a loud degraded section.
    def _strong_section_with_timeout(budget_s=300):
        """2026-10-19 REM: strong_node_agenda_section() ran unbounded INSIDE the open agenda file. On
        10-19 the corpus scan finished at 20:09 (OUT truncated to 0 bytes at open), this call then ran
        until the 1800 s cap killed the tree at 20:23, and the buffered header never reached disk: a
        0-byte agenda for the third pass running (03:30, 12:17, 20:23) on a disk reading at ~11 MB/s.
        Same shape as the journal-written-last scar (2026-09-14): the valuable output must not wait
        behind unbounded work. Built in a child under a timeout, like the judge section; a timeout or
        a child failure raises and renders as the loud DEGRADED section below."""
        import os as _os, subprocess as _sp, sys as _sys
        _here = _os.path.dirname(_os.path.abspath(__file__))
        code = ("import sys; sys.path.insert(0, %r); import brain as b; "
                "sys.stdout.reconfigure(encoding='utf-8'); "
                "sys.stdout.write(b.strong_node_agenda_section() or '')" % _here)
        proc = _sp.run([_sys.executable, "-c", code], capture_output=True, timeout=budget_s, cwd=_here,
                       creationflags=getattr(_sp, "CREATE_NO_WINDOW", 0))
        if proc.returncode != 0:
            raise RuntimeError("strong-node child exited %d: %s"
                               % (proc.returncode, proc.stderr.decode("utf-8", "replace")[-300:]))
        return proc.stdout.decode("utf-8", "replace")

    f.flush()   # the header is on disk before any bounded-but-slow step (10-19: a kill lost it all)
    try:
        _strong_section = _strong_section_with_timeout()
        if _strong_section:
            f.write("\n" + _strong_section)
    except Exception as _strong_exc:
        f.write(
            "\n## M1. STRONG NODE designation queue (DEGRADED)\n\n"
            "The mandatory >10 recurrence scan could not run: %s\n"
            % " ".join(str(_strong_exc).split())[:400]
        )

    f.flush()
    # ---- Mistakes: ~30% of every dream is this (owner 2026-07-08). The owner's corrections - every
    # time he said "that's a mistake" or "not what I wanted" - are the highest-signal learning data
    # the brain has. A rotating sample surfaces each dream so the whole ledger gets worked over time.
    _mist = read(os.path.join(MEM, "MISTAKES.md"))
    _mlines = [l for l in _mist.splitlines() if l.strip().startswith("- ")]
    if _mlines:
        random.shuffle(_mlines)
        f.write("\n## M. MISTAKES to sit with - about 30% of tonight is this (owner's standing instruction)\n\n")
        f.write("The corrections below are things the owner told an agent it got WRONG or didn't want. For "
                "each, ask: is the lesson actually learned or just filed? Is there a reflex for it "
                "(`instincts.tsv` / `INSTINCTS.md`)? Does it RECUR across dates - if so it's the priority. "
                "Re-derive the underlying pattern; a recurring mistake with no reflex should become one "
                "(append to `instincts_learned.json`).\n\n")
        for l in _mlines[:9]:
            f.write(l + "\n")
        f.write(f"\n(full ledger: memory\\MISTAKES.md - {len(_mlines)} corrections. Work a different slice each dream.)\n")

    # ---- Delegation review (owner 2026-07-11): learn which agent (Fable/Opus/GPT-5.6) is best
    # for which task from the delegation ledger; surface downgrade alerts + mistake candidates.
    # Fully guarded - a failure here must never break agenda generation.
    try:
        import subprocess as _sp
        _deldir = os.path.expanduser(r"~/brain-repo/12_cognitive_architecture/delegation")
        _sp.run(["python", os.path.join(_deldir, "dream_delegation_review.py")], capture_output=True, timeout=60)
        _dagenda = read(os.path.join(_deldir, "DELEGATION_AGENDA.md"))
        if _dagenda.strip():
            f.write("\n## M2. Delegation review - which agent for which task (owner 2026-07-11)\n\n")
            f.write(_dagenda.strip() + "\n")
    except Exception:
        pass

    # ---- Reflex telemetry (07-16): the heed/damp series finally has a READER - the dream
    # sees the trend and can act on a decaying lift or a flapping damp set. Guarded.
    try:
        import fire_telemetry as _ftel
        _tsec = _ftel.agenda_section()
        if _tsec:
            f.write("\n" + _tsec)
    except Exception:
        pass

    def _judge_section_with_timeout(budget_s=120):
        """Build the judge's agenda section in a child process under a hard timeout. 2026-09-02: a
        suppression reason the hook had gained that day was missing from the sheet builder's reason
        list and its placement loop never ended; built inline, that would have held the whole dream
        here with nothing red anywhere. On a timeout the agenda carries a visible skip line instead."""
        import os as _os, subprocess as _sp, sys as _sys
        _here = _os.path.dirname(_os.path.abspath(__file__))
        code = ("import sys; sys.path.insert(0, %r); import fire_judge_nightly as fj; "
                "sys.stdout.reconfigure(encoding='utf-8'); sys.stdout.write(fj.agenda_section())" % _here)
        try:
            proc = _sp.run([_sys.executable, "-c", code], capture_output=True, timeout=budget_s, cwd=_here,
                           creationflags=getattr(_sp, "CREATE_NO_WINDOW", 0))
        except _sp.TimeoutExpired:
            return ("\n## J. Nightly judge - SKIPPED tonight: the agenda builder did not finish in %d s "
                    "(a hang; see MISTAKES 215 and brain_regression_gate's judge check). Judge nothing from this "
                    "section; the rows resume when the builder is fixed.\n" % budget_s)
        if proc.returncode != 0:
            return ""
        return proc.stdout.decode("utf-8", "replace")

    # ---- Nightly judge pass (07-16): 4 fresh judgment-reflex fires for the dream to
    # judge inline (blind protocol; verdicts append to judged_nightly.jsonl) - the
    # zero-marginal-cost cadence for the model pass. Guarded, and since 09-02 built under a timeout.
    try:
        _jsec = _judge_section_with_timeout()
        if _jsec:
            f.write("\n" + _jsec)
    except Exception:
        pass

    _hidden = []
    if transient_recent:
        _hidden.append(f"{transient_recent} transient checkpoints hidden")
    if superseded_hidden:
        _hidden.append(f"{superseded_hidden} superseded records routed to corrections")
    f.write(f"\n## A. Recent experience to consolidate ({len(recent)} real memories"
            f"{'; ' + '; '.join(_hidden) if _hidden else ''})\n\n")
    for fn in recent[:30]:
        f.write(f"- `{fn}` - {mems[fn]['desc'][:120]}\n")

    f.write(f"\n## B. Associations sensed but never made ({len(assoc) + len(sem_assoc)} candidates)\n")
    f.write("Similar content, no [[link]] between them. Connect, merge, or dismiss.\n\n")
    for jac, a, b in assoc[:15]:
        f.write(f"- ({jac:.2f}) `{a}`  <->  `{b}`\n")
    if sem_assoc:
        f.write(f"\nSemantic candidates (embedding sidecar - meaning-similar below the token floor; "
                f"many are duplicate rules / same-arc records that predate the linking discipline):\n")
        for c, a, b in sem_assoc:
            f.write(f"- (cos {c:.2f}) `{a}`  <->  `{b}`\n")

    f.write(f"\n## C. Possibly stale beliefs ({len(stale)})\n")
    f.write("These assert something superseded. Verify and annotate (do not delete).\n\n")
    seen = set()
    for fn, entity, truth in stale[:20]:
        if fn in seen: continue
        seen.add(fn)
        f.write(f"- `{fn}` - may predate: {entity} -> now {truth}\n")

    f.write(f"\n## D. Memories the brain wants but never wrote ({len(dangling)} dangling links)\n\n")
    for l, n in dangling.most_common(10):
        f.write(f"- `[[{l}]]` wanted by {n} memories\n")

    f.write(f"\n## E. Random replay (dream randomness - find what still matters)\n\n")
    for fn in replay:
        f.write(f"- `{fn}` - {mems[fn]['desc'][:110]}\n")

    f.write(f"\n## F. Affective replay (sit with what carried feeling)\n")
    f.write("The charged memories tonight - a scar and a win. Human dreams work the\n"
            "emotional stuff, not just the filing. Sit with one; let it colour the journal.\n\n")
    if scar:
        f.write(f"- scar: `{scar}` - {mems[scar]['desc'][:100]}\n")
    if win:
        f.write(f"- win:  `{win}` - {mems[win]['desc'][:100]}\n")
    if not (scar or win):
        f.write("- (salience layer unavailable tonight - skip)\n")
    f.write("\nThe full vivid / scars / wins map: `brain/SALIENCE.md`.\n")

    f.write(f"\n## G. Imaginative seeds (bisociation - force a link, or dismiss it)\n")
    f.write("Pairs of vivid memories from different corners with almost nothing in common. For each,\n"
            "ask: is there a real analogy, a transfer, a shared shape? Most are false echoes - say so.\n"
            "But a deep dream tries more than one, and now and then a second one lands. Don't manufacture.\n\n")
    if distant:
        for k, (a, b) in enumerate(distant, 1):
            f.write(f"- seed {k}: `{a}`  <->  `{b}`\n")
            f.write(f"    {mems[a]['desc'][:80]}\n")
            f.write(f"    {mems[b]['desc'][:80]}\n")
    else:
        f.write("- (no seeds tonight - skip)\n")

    # J. stale-fact backfill (Supersede/MemConflict, 2026-07-09): schema_supersede lists memories whose
    # PROSE says a fact was corrected but names no target slug - a regex can't pick the right earlier
    # memory, so the dream (a model) does. Naming the pair stamps superseded_by, feeding the demotion.
    try:
        _sc = os.path.join(os.path.dirname(os.path.abspath(__file__)), "supersede_candidates.tsv")
        _cands = [ln.rstrip("\n").split("\t") for ln in io.open(_sc, encoding="utf-8", errors="replace")][1:] \
            if os.path.exists(_sc) else []
        if _cands:
            f.write(f"\n## J. Stale-fact pairs to resolve ({len(_cands)} memories claim a correction)\n")
            f.write("Each memory below says a fact was corrected but names no target. For the ones you can "
                    "place, find the EARLIER memory it corrects and stamp the OLDER one's frontmatter with "
                    "`superseded_by: <correction-slug>` (spread_recall then never serves the stale fact "
                    "beside its correction - eval_stale_fact showed relevance is stale-blind 4/4). Only "
                    "stamp when the pair is unambiguous; skip a vague one rather than guess.\n")
            for _row in _cands[:8]:
                if len(_row) >= 2:
                    f.write(f"- `{_row[0]}` - {_row[1][:80]}\n")
    except Exception:
        pass

    # I. due predictions (auto-resolver, 2026-07-09): predict.py scores only resolved bets; nothing
    # was listing the bets whose by: date has passed, so they piled up unresolved and the surprise
    # signal never flowed. Compute the due list mechanically; the dream (a model) judges each against
    # what the corpus now knows - hit, miss, or push the date with a reason. Never regex-resolve.
    try:
        import predict as _pred
        _today = time.strftime("%Y-%m-%d")
        _due = [b for b in _pred.parse(MEM) if not b["done"] and b.get("by") and b["by"] <= _today
                and b.get("outcome") not in ("hit", "miss")]
        if _due:
            f.write(f"\n## I. Due predictions ({len(_due)} past their by: date - resolve tonight)\n")
            f.write("For each, decide from what the brain now knows: outcome: hit | outcome: miss "
                    "(edit the ledger line in memory/PREDICTIONS.md, keep it append-friendly), or move "
                    "the by: date forward with a parenthetical reason. A confident miss is the most "
                    "valuable line in the ledger - score it, don't soften it.\n")
            for b in _due[:8]:
                f.write(f"- [{b['id']}] p={b['p']} by:{b['by']} scope:{b['scope']} - {b['claim'][:120]}\n")
    except Exception:
        pass

    # H. prompt-archive upkeep (owner directive 2026-07-09: every prompt saved AND classified well).
    # The live hook only keyword-tags new prompts (provisional); the dream owes them a real read.
    try:
        _pl = os.path.join(MEM, "prompts", "PROMPTS.jsonl")
        _unc = 0
        _bk = None
        try:
            # his rows apart from machine rows, by day (prompt_backlog.py): on 2026-09-23, 37 of the
            # newest 40 unclassified rows were headless picture-worker prompts, so 360 of his waited
            # three days behind text whose origin already decides its class
            import prompt_backlog as _pbk
            _bk = _pbk.owner_backlog(_pl)
            _unc = _bk["owner"]
        except Exception:
            _bk = None
            if os.path.exists(_pl):
                for _ln in io.open(_pl, encoding="utf-8", errors="replace"):
                    if '"class_model"' not in _ln:
                        _unc += 1
        if _unc:
            if _bk:
                _bkdays = ", ".join("%s: %d" % (_d[5:], _n) for _d, _n in _bk["by_day"])
                f.write(f"\n## H. Prompt archive: {_unc} of HIS prompts await real classification "
                        f"(newest days {_bkdays}; {_bk['machine']} machine-origin rows need no read)\n")
                f.write("Read HIS rows only (recorded origin \"owner\"), newest day first. A machine-origin "
                        "row is decided by its origin, not by a read: on 2026-09-23, 37 of the newest 40 "
                        "unclassified rows were headless picture-worker prompts, which is how 360 of his "
                        "prompts from 09-21..09-23 waited three days. A loop re-sends his words unchanged, "
                        "so one text can be the prompt he typed and hundreds of re-fires: a verdict with "
                        "\"scope\": \"heads\" labels only the copy he typed (apply_prompt_classes.py). Some rows stamped his "
                        "are machine text all the same - a loop tick, a relaunch or reboot notice, a worker brief, anything "
                        "that speaks of him in the third person (his clock, the owner): give those \"class_model\": "
                        "\"automation\" and \"origin\": \"automation\" in the verdict (on 09-23, 551 rows like that carried "
                        "his stamp).\n")
            else:
                f.write(f"\n## H. Prompt archive: {_unc} prompts await real classification\n")
            f.write("The owner wants every prompt he gives an agent saved and CLASSIFIED WELL "
                    "(mistake / security / prove / improve / think / run / remember / admin / other; "
                    "automation = machine-authored). Origin takes precedence over action class; missing origin "
                    "is unknown, never assumed owner. A class such as improve/run can still contain a "
                    "correction: review correction presence separately, and RECORD that judgement as "
                    "`correction_present`: true or false on every verdict line - it is a second, "
                    "independent answer about the same text, not a re-run of the class. Until "
                    "2026-09-15 this instruction asked for the judgement and the verdict schema had "
                    "nowhere to put it, so the field was true on 3 of 34,388 ledger rows and "
                    "correction detection had collapsed onto class_model == 'mistake' - which is "
                    "precisely the coupling the canonical contract (4.1) forbids, because a correction "
                    "inside a prompt classed run or improve was then invisible. The hook's keyword tags "
                    "are provisional.\n"
                    f"Read up to 60 of HIS entries lacking `class_model` from `{_pl}` (his inflow runs "
                    "about 115 a day, measured 09-09..09-23, so 40 a pass never caught up), judge each from its "
                    "content (what did the owner want done), then write verdicts as jsonl lines "
                    "{sha1?, class_model, confidence, correction_present} - sha1 optional if you "
                    "instead run:\n"
                    "  python ~/.claude/dreaming/apply_prompt_classes.py <your_sidecar.jsonl>\n"
                    "  python ~/.claude/dreaming/build_prompt_views.py\n"
                    "(the sidecar hash rule is in apply_prompt_classes.py.) ORDER MATTERS: classify "
                    "the NEWEST days first, then work backwards. Oldest-first was the old "
                    "instruction and it is exactly backwards - the M-section reads class_model, so "
                    "oldest-first keeps the newest corrections invisible the longest, which is how "
                    "MISTAKES.md went three days stale while reflexes compiled nightly "
                    "(measured 2026-08-07: 08-06 was 0 of 95 rows classified).\n"
                    "ALSO re-judge 10 random entries that already carry class_model, blind (decide "
                    "before looking at the stored label); where you disagree, overwrite via the same "
                    "sidecar. You are the standing second rater - first blind check ran 2026-07-09: "
                    "17/27 exact, disagreements were dual-intent prompts.\n")
        # the owner asked (April, repeated 07-09) that the dream DREAM ABOUT THE MISTAKES - his own
        # correction words are the rawest mistake signal the brain has. Feed the newest mistake-class
        # prompts into the agenda as consolidation material, not just classification chores.
        try:
            import json as _json
            _mist = []
            if os.path.exists(_pl):
                for _ln in io.open(_pl, encoding="utf-8", errors="replace"):
                    if '"mistake"' in _ln or '"correction_present"' in _ln:
                        try:
                            _r = _json.loads(_ln)
                        except Exception:
                            continue
                        if explicit_correction_candidate(_r):
                            _mist.append(_r)
            if _mist:
                _mist.sort(key=lambda r: r.get("ts") or "")
                f.write("\nRecorded-owner correction candidates, verbatim (origin labels still need native review; "
                        "class). Read them as one body: is a shape recurring that MISTAKES.md and the "
                        "gut do not yet carry? If yes, distill it (a MISTAKES.md line or a reflex "
                        "candidate with a measured would-have-fired check); if no, say so and move on.\n")
                for _r in _mist[-6:]:
                    f.write("- %s %s\n" % ((_r.get("ts") or "?")[:10], (_r.get("text") or "")[:150].replace("\n", " ")))
            # and the ones the classifier never reached (see _unclassified_corrections above):
            # without this, a deferred chore silently blinds the M-section to the newest days.
            _raw = _unclassified_corrections(_pl)
            if _raw:
                _seen_txt = {(r.get("text") or "")[:150] for r in _mist[-6:]}
                _raw = [r for r in _raw if (r.get("text") or "")[:150] not in _seen_txt]
            if _raw:
                f.write("\nAlso his, from days the classifier has NOT reached yet (lexical match on "
                        "his correction vocabulary, so read them with a grain of salt - some may be "
                        "questions, not corrections). Judge them as part of the same body, and "
                        "classify these rows first when you do the H chore:\n")
                for _r in _raw:
                    f.write("- %s %s\n" % ((_r.get("ts") or "?")[:10], (_r.get("text") or "")[:150].replace("\n", " ")))
        except Exception:
            pass
    except Exception:
        pass

print(f"dream agenda written: {OUT}")
print(f"  recent={len(recent)} assoc={len(assoc)} sem_assoc={len(sem_assoc)} stale={len(set(s[0] for s in stale))} dangling={len(dangling)} replay={len(replay)} salience={'on' if SAL else 'off'} scar={bool(scar)} win={bool(win)} distant={len(distant)}")
