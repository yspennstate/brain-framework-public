#!/usr/bin/env python3
"""math_prefilter.py - a lexical candidate filter so the math lane (math_sense.py) scales past a few thousand nodes.

retrieve() scores every node on every query (about 0.4 ms per node with the signal cache): fine at 5,000 nodes,
18 s at 46,000 - the literature layer built 2026-09-07 (every math.PR statement harvested for the probability
bank) is ten times the production store. This module keeps a small inverted index beside a store
(<store>.prefilter.pkl: token -> postings with term frequency, document lengths) and returns the K best node ids
for a prompt by BM25; math_sense then loads ONLY those nodes from sqlite and runs its unchanged ranking, gates
and rendering over them. The full-store path is untouched when no index file exists, and every error here falls
back to it (the hook must never break a session).

    python math_prefilter.py build <store.sqlite3>      write <store>.prefilter.pkl
    python math_prefilter.py query <store.sqlite3> "<text>" [K]

Tokens: the same casefold + NFKC as retrieval.tokenize, alphabetic runs of 3+ chars and LaTeX macros, minus the
lane's stopwords; a token that occurs in more than 20 percent of nodes carries no weight. Pure Python, no deps.
"""
import io, json, math, os, pickle, re, sys, time, unicodedata, collections, hashlib

# 2026-09-11: digit runs are tokens too. The store holds enumerated families (2,018 self-avoiding-walk counts that
# differ only in their parameters); without numbers every member of a family scored the same and the top-K cut was
# a coin toss over the family, so the row the question named never reached retrieve(). The df cap (build()) moved
# from 0.2 to 0.5 of the store the same day, because that family is 28 pct of the store and every one of its words
# ("self-avoiding", "lattice", "strip", "sites", ...) had been capped out of the index - the question had NO token
# left. Both changes need a rebuild (python math_prefilter.py build <store>); the index records which rules built it.
# 2026-09-11 (evening): a number keeps its SIGN. On the locate-the-fact bank every one of the 11 enumerated-count
# misses was a question naming a site like (-2,2) answered with rows for (2,2): "\d+" read both as "2". "-?\d+" makes
# "-2" its own token, so the row that shares the signed coordinate outscores its mirror image. Rebuild required.
TOKEN_RE = re.compile(r"\\[a-zA-Z]+|[a-z][a-z\-']{2,}|-?\d+")
# every dash a mathematician might type between two names, folded to the ASCII hyphen TOKEN_RE understands
_DASHES = {ord(c): "-" for c in "\u2010\u2011\u2012\u2013\u2014\u2015\u2212\uff0d"}
_DASHES[ord("\u2019")] = "'"
DF_CAP = float(os.environ.get("MATHBRAIN_PREFILTER_DFCAP", "0.5"))   # a token in more than this share of nodes carries no weight
STOP = frozenset("""the and for all that with then this there where have has are let such from which not only one two any
every some also into over can will its our their his her they them was were been being than when what who whom whose
does did done each much many more most very via per may might must shall should would could here where while whether
proof lemma theorem proposition corollary claim show shows shown suppose assume assumed obtain obtained holds hold given
case first second finally moreover therefore consequently note recall define denote set fix write consider follows
following since because hence thus implies imply proved prove using used use see also above below where
label ref eqref cite citep citet begin end left right frac sqrt mathbb mathcal mathrm mathbf mathfrak text quad qquad
cdot cdots ldots dots leq geq neq infty sum int prod lim sup inf max min log exp sin cos tan alpha beta gamma delta
epsilon varepsilon zeta eta theta iota kappa lambda mu nu xi pi rho sigma tau upsilon phi varphi chi psi omega
""".split())

def tokens(text):
    # Accents are FOLDED, not preserved (2026-09-12). TOKEN_RE matches ASCII letter runs, so a composed
    # accent split a name in two: "carathéodory" tokenized as "carath" + "odory" while the ASCII spelling
    # "caratheodory" was one token, and the two never matched. Measured before the fix: the prompt "State the
    # Vitali-Caratheodory theorem" served the right node, and the SAME prompt written the way a mathematician
    # writes it, "Vitali-Caratheodory" with the accents, served the Vitali COVERING LEMMA - a different
    # theorem, silently. Every accented name in mathematics was affected: Holder, Poincare, Garding, Levy,
    # Cesaro, Erdos, Caratheodory, Fejer. Decomposing (NFKD) and dropping the combining marks maps both
    # spellings to one token. The index stores tokens, so this needs a rebuild: python math_prefilter.py
    # build <store>.
    t = unicodedata.normalize("NFKD", text or "").casefold()
    t = "".join(c for c in t if not unicodedata.combining(c))
    # Dashes are folded to the ASCII hyphen for the same reason (2026-09-12, found in the same probe). A
    # two-author name is written with an EN-DASH - "Vitali-Caratheodory", "Banach-Steinhaus" - and TOKEN_RE
    # admits only the ASCII hyphen inside a word, so the en-dash form split into two tokens while the hyphen
    # form stayed one, and the two spellings retrieved disjoint candidate sets (12 against 1, zero overlap).
    t = t.translate(_DASHES)
    return [m.group(0) for m in TOKEN_RE.finditer(t) if m.group(0) not in STOP]

def index_path(store_path):
    return str(store_path) + ".prefilter.pkl"


def _current_index_nodes(nodes, edges):
    """Current-only projection; damaged or ambiguous revision families stay out."""
    from math_brain.models import EdgeKind
    from math_brain.retrieval import current_revision_nodes
    known = {node.id for node in nodes}
    revisions = [edge for edge in edges if edge.kind is EdgeKind.SUPERSEDES]
    adjacent, outgoing = collections.defaultdict(set), collections.defaultdict(set)
    for edge in revisions:
        adjacent[edge.source_id].add(edge.target_id)
        adjacent[edge.target_id].add(edge.source_id)
        outgoing[edge.source_id].add(edge.target_id)
    blocked, seen, bad_families = set(), set(), 0
    for seed in adjacent:
        if seed in seen:
            continue
        family, todo = set(), [seed]
        while todo:
            nid = todo.pop()
            if nid not in family:
                family.add(nid)
                todo.extend(adjacent[nid] - family)
        seen.update(family)
        invalid = bool(family - known)
        degree = {nid: 0 for nid in family}
        for nid in family:
            for older in outgoing[nid]:
                degree[older] += 1
        current = [nid for nid, count in degree.items() if count == 0]
        todo, count = list(current), 0
        while todo:
            nid = todo.pop()
            count += 1
            for older in outgoing[nid]:
                degree[older] -= 1
                if degree[older] == 0:
                    todo.append(older)
        invalid = invalid or count != len(family) or len(current) != 1
        if invalid:
            blocked.update(family)
            bad_families += 1
    eligible, exclusions = current_revision_nodes(nodes, revisions)
    accepted = [node for node in eligible if node.id not in blocked]
    return accepted, {"source_nodes": len(nodes), "indexed_current_nodes": len(accepted),
                      "superseded_nodes": len(exclusions),
                      "quarantined_present_nodes": len(known & blocked),
                      "quarantined_revision_families": bad_families}


def _source_revision_seal(nodes, edges):
    # Full reconstructed records, not the historical body-only digest column.
    digest = hashlib.sha256()
    for node in sorted(nodes, key=lambda item: item.id):
        digest.update(("node\0" + node.id + "\0" + node.digest() + "\n").encode("utf-8"))
    for edge in sorted(edges, key=lambda item: item.id):
        digest.update(("edge\0" + edge.id + "\0" + edge.digest() + "\n").encode("utf-8"))
    return digest.hexdigest()


def _index_file_signature(path):
    stat = os.stat(path)
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)


def build(store_path):
    sys.path.insert(0, os.path.expanduser(r"~/math-brain-build/src"))
    from math_brain.store import ResearchStore
    from math_brain.models import EdgeKind
    t0 = time.time()
    with ResearchStore.open_read_only(str(store_path)) as store:
        store.connection.execute("BEGIN")
        source_nodes = list(store.iter_nodes())
        revision_edges = list(store.iter_edges(kind=EdgeKind.SUPERSEDES))
    nodes, revision_projection = _current_index_nodes(source_nodes, revision_edges)
    # INDEX WHAT YOU CAN SERVE (2026-09-12, goal 30h item 4). The hook's selection_integrity re-reads every chosen card and
    # DROPS one whose stored digest no longer matches what the reader recomputes - and never refills the slot. 34 nodes in
    # the store fail that read (25 'b2 replacement' orphans written outside any bundle on 08-10, each a duplicate of an
    # original that already has a released QA-corrected successor, plus 9 campaign lemmas). They cannot be superseded
    # through the integrator (no logical id, no release - the framework refuses by design), yet they RANK: on the fresh
    # bank a refused node sat in the top 3 on 17 of 218 prompts, and two unnamed misses (Ramsey, Kuratowski) were an
    # orphan at rank 0 whose block came out empty. So the index applies the reader's own cheap test at build time -
    # the stored digest must equal the full node digest or the legacy body-only sha - and leaves the rest out, so the
    # real revision fills the slot. The claim-statement family (8 nodes, different check) is not covered here.
    # Dial MATHBRAIN_PREFILTER_INTEGRITY: 1 excludes (the default since 2026-09-13 02:3x), 0 indexes everything as before.
    # MEASURED before the flip, two indexes built from the same 7,803-node store, sequential, jobs 1: unnamed 71 -> 72 of 89
    # (Ramsey served), rank-1 53 -> 54, silent 10 -> 9; named 84/89 unchanged; absent-target fires 28 -> 27 of 40; long
    # prompts 74/100 with notes 170 -> 173 (six blocks gained a third card where the refused orphan had held the slot);
    # no item lost a hit or a rank. Kuratowski stays a miss - its real revision does not clear the floor on its own.
    refused = []
    if os.environ.get("MATHBRAIN_PREFILTER_INTEGRITY", "1").strip() not in ("0", "", "off", "false"):
        import hashlib as _hl
        with ResearchStore.open_read_only(str(store_path)) as store2:
            stored = dict(store2.connection.execute("select id, digest from nodes"))
        keep = []
        for n in nodes:
            d = stored.get(n.id)
            body_sha = "sha256:" + _hl.sha256((n.body or "").encode("utf-8")).hexdigest()
            if d == n.digest() or d == body_sha:
                keep.append(n)
            else:
                refused.append(n.id)
        nodes = keep
    revision_projection["reader_refused_excluded"] = len(refused)
    revision_projection["reader_refused_ids"] = refused
    source_revision_sha256 = _source_revision_seal(source_nodes, revision_edges)
    ids, dl, post, labels = [], [], collections.defaultdict(list), collections.defaultdict(list)
    for i, n in enumerate(nodes):
        ids.append(n.id)
        tf = collections.Counter(tokens((n.title or "") + " " + (n.body or "")))
        dl.append(sum(tf.values()))
        for tok, f in tf.items():
            post[tok].append((i, f))
        # LaTeX labels of literature statements (metadata "labels": a list or its repr) -> node index, so a step that
        # cites "(ref lem:hitting_prob)" can be answered with THAT statement instead of a lexical neighbour
        try:
            meta = json.loads(getattr(n, "metadata_json", None) or "{}") if isinstance(getattr(n, "metadata_json", None), str) else (getattr(n, "metadata", None) or {})
            labs = meta.get("labels") if isinstance(meta, dict) else None
            if isinstance(labs, str):
                labs = re.findall(r"'([^']+)'", labs)
            for lab in labs or []:
                labels[str(lab).strip().lower()].append(i)
        except Exception:
            pass
    N = len(ids)
    cap = DF_CAP * N
    post = {t: p for t, p in post.items() if len(p) <= cap}
    data = {"ids": ids, "dl": dl, "avgdl": (sum(dl) / N) if N else 1.0, "post": dict(post), "n": N,
            "labels": dict(labels), "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "store": str(store_path),
            "schema": "math_prefilter.current_revisions.v1", "revision_projection": revision_projection,
            "source_revision_sha256": source_revision_sha256,
            "df_cap": DF_CAP, "token_re": TOKEN_RE.pattern}
    p = index_path(store_path)
    tmp = p + ".tmp"
    with open(tmp, "wb") as fh:
        pickle.dump(data, fh, protocol=pickle.HIGHEST_PROTOCOL)
    os.replace(tmp, p)
    return {"index": p, "nodes": N, "tokens": len(post), "seconds": round(time.time() - t0, 1),
            "bytes": os.path.getsize(p)}

_CACHE = {}
LAST_SCORES = {}   # node id -> BM25 score of the last select() call
LAST_QSELF = 0.0   # the last query's self-score: sum of idf over its distinct indexed tokens - what a document carrying
                   # every query token once at average length would score. math_sense scales its on-topic floor by it
                   # (2026-09-11), because a raw BM25 floor grows with the question and silenced one-line questions.

def load(store_path):
    p = index_path(store_path)
    try:
        signature = _index_file_signature(p)
    except FileNotFoundError:
        _CACHE.pop(p, None)
        return None
    cached = _CACHE.get(p)
    if cached is not None and cached[0] == signature:
        return cached[1]
    with open(p, "rb") as fh:
        data = pickle.load(fh)
    if _index_file_signature(p) != signature:
        raise ValueError("prefilter index changed during load")
    if not isinstance(data, dict) or not isinstance(data.get("ids"), list):
        raise ValueError("malformed prefilter index")
    _CACHE[p] = (signature, data)
    return data

def _scores(data, text):
    """BM25 score per node index for the text (only nodes sharing a token appear)."""
    q = collections.Counter(tokens(text))
    N, dl, avgdl, post = data["n"], data["dl"], data["avgdl"], data["post"]
    k1, b = 1.2, 0.75
    score = collections.defaultdict(float)
    for tok in q:
        pl = post.get(tok)
        if not pl:
            continue
        idf = math.log(1.0 + (N - len(pl) + 0.5) / (len(pl) + 0.5))
        for i, f in pl:
            score[i] += idf * f * (k1 + 1) / (f + k1 * (1 - b + b * dl[i] / avgdl))
    return score


def select(store_path, text, k=300):
    """Top-k node ids for the text by BM25 over the prefilter index, or None when there is no index."""
    global LAST_SCORES, LAST_QSELF
    LAST_SCORES = {}
    LAST_QSELF = 0.0
    data = load(store_path)
    if not data:
        return None
    try:
        N, post = data["n"], data["post"]
        LAST_QSELF = sum(math.log(1.0 + (N - len(post[t]) + 0.5) / (len(post[t]) + 0.5))
                         for t in set(tokens(text)) if t in post)
    except Exception:
        LAST_QSELF = 0.0
    score = _scores(data, text)
    if not score:
        return []
    best = sorted(score.items(), key=lambda x: -x[1])[:k]
    LAST_SCORES = {data["ids"][i]: float(sc) for i, sc in best}   # read by math_sense's on-topic gate (2026-09-07)
    return [data["ids"][i] for i, _ in best]


REF_RE = re.compile(r"\((?:ref|Cref|cref|autoref|eqref)\s+([A-Za-z0-9:._'-]+)\)|\\(?:ref|Cref|cref|autoref)\{([^}]{1,60})\}")


def pinned(store_path, text, cap=2):
    """Node ids of literature statements the text CITES by LaTeX label ("(ref lem:hitting_prob)"). A label can
    recur across papers, so among the nodes carrying it the one with the best BM25 score against the text wins
    (the same paper shares the notation); a label with no lexical overlap at all is not served. Equation labels
    (eq:...) are never statements and are skipped."""
    data = load(store_path)
    if not data or not data.get("labels"):
        return []
    labs = []
    for a, b in REF_RE.findall(text):
        lab = (a or b).strip().lower()
        if lab and not lab.startswith(("eq", "fig", "tab", "sec", "app", "rem", "ex")) and lab not in labs:
            labs.append(lab)
    if not labs:
        return []
    score = _scores(data, text)
    out = []
    for lab in labs:
        cands = data["labels"].get(lab) or []
        if not cands:
            continue
        scored = sorted(((score.get(i, 0.0), i) for i in cands), reverse=True)
        if scored and scored[0][0] > 0:
            nid = data["ids"][scored[0][1]]
            if nid not in out:
                out.append(nid)
        if len(out) >= cap:
            break
    return out

if __name__ == "__main__":
    if len(sys.argv) < 2 or any(a in ("-h", "--help") for a in sys.argv[1:]):
        # help is inert: nothing is built or written
        print("usage: math_prefilter.py build STORE            (write STORE.prefilter.pkl)\n"
              "       math_prefilter.py query STORE TEXT [K]   (print the top-K candidate count and time)")
        raise SystemExit(0)
    if len(sys.argv) >= 3 and sys.argv[1] == "build":
        print(build(sys.argv[2]))
    elif len(sys.argv) >= 4 and sys.argv[1] == "query":
        t0 = time.time()
        ids = select(sys.argv[2], sys.argv[3], int(sys.argv[4]) if len(sys.argv) > 4 else 10)
        print("%d ids in %.2fs" % (len(ids or []), time.time() - t0))
        for i in (ids or [])[:10]:
            print(" ", i)
    else:
        print(__doc__)
