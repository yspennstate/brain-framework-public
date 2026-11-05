"""Cards added to the math store since a prepared recall state was built, indexed into that state in memory.

RECALL-10X lead 2 (claude-68ceaf54, 29 Oct 2026). fastrecall.open_prepared serves a state up to STALE_S seconds
behind the store, so a card ingested a minute ago is not findable until the next prebuild (hours on a starved disk);
asking for it with STALE_S=0 costs a live read of the whole store (30-97 s a call on 29 Oct, one over 900 s).

The nodes table is append-only (max rowid == count), and a prepared state's "order" holds every card the build read,
so the cards at rowid > len(order) are exactly the ones the state has not seen. extend() reads only those rows and
the links that touch them, indexes each card exactly as fastrecall.build() does, and appends it to the in-memory
postings, lengths, title tokens, bigrams, ids and rows, so idf and the mean length are taken over the merged pool.

Differences from a full rebuild, by design (each makes extend() refuse rather than answer differently):
  - a SUPERSEDES link among the new links: the revision rules need the whole pool, so refuse;
  - a new link between two old cards is not seen (graph spread only); new links on new cards are;
  - the meaning channel has no vector for a new card (it scores on words, symbols and formulas only);
  - more than MAX_CARDS new cards: refuse (a rebuild is the right tool then).
"""
import json
import os
import sqlite3
from collections import Counter

MAX_CARDS = int(os.environ.get("MATH_RECALL_DELTA_MAX", "5000") or 5000)


def _connect(store_path):
    uri = "file:%s?mode=ro" % os.path.abspath(store_path).replace("\\", "/")
    return sqlite3.connect(uri, uri=True, timeout=30)


def read_delta(store_path, n_known, known_rows, upto=None):
    """(nodes, edges) added at rowid > n_known (and <= upto when given), as GraphNode/GraphEdge, or (None, why)."""
    from math_brain.models import EdgeKind, GraphEdge, GraphNode, NodeKind
    con = _connect(store_path)
    try:
        top = con.execute("SELECT max(rowid), count(*) FROM nodes").fetchone()
        if top[0] != top[1]:
            return None, "the nodes table is not append-only (max rowid %s, count %s)" % top
        if upto is not None:
            top = (min(upto, top[0]), min(upto, top[0]))
        if top[0] == n_known:
            return ([], []), ""
        if top[0] < n_known:
            return None, "the store has fewer cards than the state"
        if top[0] - n_known > MAX_CARDS:
            return None, "%d new cards, over MATH_RECALL_DELTA_MAX" % (top[0] - n_known)
        edge_row = con.execute("SELECT id FROM nodes WHERE rowid = ?", (n_known,)).fetchone()
        if n_known and (edge_row is None or edge_row[0] not in known_rows):
            return None, "the card at the state's last rowid is not in the state"
        rows = con.execute("SELECT id, kind, title, body, metadata_json, created_at FROM nodes WHERE rowid > ? AND rowid <= ? "
                           "ORDER BY created_at, id", (n_known, top[0])).fetchall()
        nodes = []
        for r in rows:
            if r[0] in known_rows:
                return None, "a new rowid carries a known card id"
            nodes.append(GraphNode(id=r[0], kind=NodeKind(r[1]), title=r[2], body=r[3],
                                   metadata=json.loads(r[4]), created_at=r[5]))
        ids = [n.id for n in nodes]
        retracted = set()
        try:
            retracted = {x[0] for x in con.execute("SELECT edge_id FROM edge_retractions")}
        except sqlite3.Error:
            pass
        edges = []
        known_kinds = {k.value for k in EdgeKind}
        for k in range(0, len(ids), 500):
            chunk = ids[k:k + 500]
            q = ",".join("?" * len(chunk))
            for r in con.execute("SELECT id, source_id, target_id, kind, rationale, weight, metadata_json, created_at "
                                 "FROM edges WHERE source_id IN (%s) OR target_id IN (%s)" % (q, q), chunk + chunk):
                if r[0] in retracted or r[3] not in known_kinds:
                    continue
                edges.append(GraphEdge(id=r[0], source_id=r[1], target_id=r[2], kind=EdgeKind(r[3]), rationale=r[4],
                                       weight=r[5], metadata=json.loads(r[6]), created_at=r[7]))
        uniq = {e.id: e for e in edges}
        edges = sorted(uniq.values(), key=lambda e: (e.created_at, e.id))
        return (nodes, edges), ""
    finally:
        con.close()


def extend(prepared, store_path=None, short_ids=None, upto=None):
    """Index the cards the prepared state has not seen into it, in place. Returns (n_added, why); n_added None means
    the state was left untouched and the caller should use the old path (a rebuild or the live read)."""
    from fastrecall import _ints, _unints, _node, _edge, _heavy_from  # noqa: F401  (the same packing as build())
    R = prepared.R
    s = prepared.s
    store_path = store_path or prepared.store_path
    got, why = read_delta(store_path, len(s["order"]), s["rows"], upto)
    if got is None:
        return None, why
    nodes, edges = got
    if not nodes:
        return 0, "nothing new"
    accepted, exclusions = R.guard_nodes(tuple(nodes), None)
    if exclusions:
        return None, "the guard excluded a new card"
    # the revision rules, rerun over the whole guard-accepted pool as build() runs them (retrieval.current_revision_nodes)
    rev = _revisions(R, s, accepted, nodes, store_path)
    if rev[0] is None:
        return None, rev[1]
    hidden, rev_ex, rev_edges, superseded_by = rev
    tool0 = s["tool"]
    have = {e.id for e in edges}
    for e in rev_edges:      # a supersedes link between two old cards, newer than the state
        if e.id not in have and "supersedes" not in tool0["pair_kinds"].get((e.source_id, e.target_id), ()):
            edges.append(e)
    edges.sort(key=lambda e: (e.created_at, e.id))
    drop = [d for d, i in enumerate(s["ids"]) if i in hidden]
    accepted = tuple(n for n in accepted if n.id not in hidden)
    s["exclusions"] = tuple(x for x in s["exclusions"] if x[1] not in REVISION_REASONS) + \
        tuple((x.node_id, x.reason) for x in rev_ex)
    tool0["superseded_by"] = superseded_by
    if R._SIGNAL_DB is None:      # as build() does
        R.load_signal_db(R.signal_db_path(store_path))
    if R._SIGNAL_DB is not None:
        R.prefetch_signals([n.title + " " + n.body for n in accepted])
    whole = int(s["title_weight"])
    base = len(s["ids"])
    lex_add, sym_add, frm_add = {}, {}, {}
    lex_len, sym_len, symn_len, frm_len, title_tokens, degenerate = [], [], [], [], [], []
    big_add, fold_add = {}, {}
    new_rows = {}
    for k, n in enumerate(accepted):
        i = base + k
        row = (n.id, n.kind.value, n.title, n.body, json.dumps(n.metadata), n.schema_version, n.created_at)
        new_rows[n.id] = row
        sig = R.index_signals(n.title + " " + n.body)
        lex_terms, sym_terms = R._card_terms(sig)
        tt = R._title_tokens(n.title)
        if s["title_weight"] > 0:
            title_terms = R._content_terms(tt)
            if title_terms:
                lex_terms = lex_terms + title_terms * whole
        for post, terms in ((lex_add, lex_terms), (sym_add, sym_terms), (frm_add, sig.formula_fingerprints)):
            for term, tf in Counter(terms).items():
                post.setdefault(term, []).extend((i, tf))
        lex_len.append(len(lex_terms))
        sym_len.append(len(sym_terms))
        symn_len.append(sum(1 for t in sym_terms if not t.startswith("punct:") and t != "op:subscript"))
        frm_len.append(len(sig.formula_fingerprints))
        title_tokens.append(tuple(tt))
        degenerate.append(1 if R._is_degenerate(n) else 0)
        norm = tuple(R._strip_possessive(t) for t in tt)
        if len(norm) >= 2:
            for bg in set(zip(norm, norm[1:])):
                big_add.setdefault(bg, []).append(i)
            if s["phrase_norm"]:
                fold = tuple(R._phrase_fold(t) for t in norm)
                for bg in set(zip(fold, fold[1:])):
                    fold_add.setdefault(bg, []).append(i)
    # appended doc numbers are all larger than any already there, so each posting stays in doc order
    for name, add in (("lex", lex_add), ("sym", sym_add), ("frm", frm_add), ("bigrams", big_add),
                      ("fold_bigrams", fold_add)):
        post = s[name]
        for term, vals in add.items():
            post[term] = post.get(term, b"") + _ints(vals)
    s["lex_len"] += _ints(lex_len)
    s["lex_total"] += sum(lex_len)
    s["sym_len"] += _ints(sym_len)
    s["symn_len"] += _ints(symn_len)
    s["frm_len"] += _ints(frm_len)
    s["title_tokens"] = tuple(s["title_tokens"]) + tuple(title_tokens)
    s["degenerate"] = bytes(s["degenerate"]) + bytes(degenerate)
    s["ids"] = tuple(s["ids"]) + tuple(n.id for n in accepted)
    s["order"] = tuple(s["order"]) + tuple(n.id for n in nodes)
    s["rows"].update(new_rows)
    # what the printout reads: titles, links, pair kinds, short ids (derive_tool's maps, extended)
    tool = s["tool"]
    for n in nodes:
        tool["title"][n.id] = " ".join((n.title or "").split())
    for e in edges:
        tool["pair_kinds"].setdefault((e.source_id, e.target_id), []).append(e.kind.value)
        why_ = " ".join((e.rationale or "").split())
        tool["links"].setdefault(e.source_id, []).append(("->", e.kind.value, e.target_id, why_))
        tool["links"].setdefault(e.target_id, []).append(("<-", e.kind.value, e.source_id, why_))
    if short_ids is not None:
        tool["short"] = short_ids(tool["title"].keys())
    if drop:
        _drop_docs(s, drop)
    prepared.ids = s["ids"]
    prepared.tool = tool
    prepared._posmap = None
    prepared._delta_edges = tuple((e.source_id, e.target_id, e.kind.value, e.rationale, e.weight,
                                   json.dumps(e.metadata), e.id, e.schema_version, e.created_at) for e in edges)
    prepared._delta_corpus = "\n".join((tool["title"].get(n.id, "") + " " + (n.body or "")).lower() for n in nodes)
    _patch_heavy(prepared)
    return len(accepted), ""


REVISION_REASONS = ("superseded_revision", "superseded_by_unavailable_revision", "competing_revision_heads")


class _Id:
    """current_revision_nodes reads only .id from an old card."""
    __slots__ = ("id",)

    def __init__(self, i):
        self.id = i


def _supersedes_edges(store_path):
    from math_brain.models import EdgeKind, GraphEdge
    con = _connect(store_path)
    try:
        retracted = set()
        try:
            retracted = {x[0] for x in con.execute("SELECT edge_id FROM edge_retractions")}
        except sqlite3.Error:
            pass
        out = []
        for r in con.execute("SELECT id, source_id, target_id, rationale, weight, metadata_json, created_at FROM edges "
                             "WHERE kind = 'supersedes' ORDER BY created_at, id"):
            if r[0] not in retracted:
                out.append(GraphEdge(id=r[0], source_id=r[1], target_id=r[2], kind=EdgeKind.SUPERSEDES,
                                     rationale=r[3], weight=r[4], metadata=json.loads(r[5]), created_at=r[6]))
        return out
    finally:
        con.close()


def _revisions(R, s, accepted, nodes, store_path):
    """(hidden ids, revision exclusions, every supersedes link, superseded_by) or (None, why).

    The old guard-accepted pool is the served ids plus the cards the old revision step hid; the new cards come after
    them, as in the store's read order. A card the old state hid that is current again would have to be inserted in
    the middle of the doc numbering: refuse it (and a supersedes link retracted since), a rebuild handles both."""
    rev_edges = _supersedes_edges(store_path)
    old_rev = {x[0] for x in s["exclusions"] if x[1] in REVISION_REASONS}
    pool = set(s["ids"]) | old_rev
    mat = [_Id(i) for i in s["order"] if i in pool] + list(accepted)
    known = tuple(s["order"]) + tuple(n.id for n in nodes)
    _, rev_ex = R.current_revision_nodes(mat, rev_edges, known_ids=known)
    hidden = {x.node_id for x in rev_ex}
    if old_rev - hidden:
        return None, "%d card(s) the state hid as old revisions are current again" % len(old_rev - hidden)
    superseded_by = {e.target_id: e.source_id for e in rev_edges}
    old_sb = s["tool"].get("superseded_by", {})
    if any(superseded_by.get(k) != v for k, v in old_sb.items()):
        return None, "a supersedes link in the state is gone from the store (retracted)"
    return hidden, rev_ex, rev_edges, superseded_by


def _drop_docs(s, drop):
    """Remove doc positions `drop` from every posting and per-doc array, renumbering the docs after them, so the
    state is what build() writes for the pool without them. One numpy pass per posting family."""
    import numpy as np
    D = np.asarray(sorted(drop), dtype=np.int32)
    for name, stride in (("lex", 2), ("sym", 2), ("frm", 2), ("bigrams", 1), ("fold_bigrams", 1)):
        post = s[name]
        terms = list(post)
        if not terms:
            continue
        blobs = [post[t] for t in terms]
        step = 4 * stride
        lens = np.fromiter((len(b) // step for b in blobs), dtype=np.int64, count=len(blobs))
        a = np.frombuffer(b"".join(blobs), dtype=np.int32).reshape(-1, stride).copy()
        docs = a[:, 0]
        keep = ~np.isin(docs, D)
        a[:, 0] = docs - np.searchsorted(D, docs).astype(np.int32)
        owner = np.repeat(np.arange(len(terms)), lens)[keep]
        buf = a[keep].tobytes()
        counts = np.bincount(owner, minlength=len(terms)).tolist()
        post.clear()
        off = 0
        for t, c in zip(terms, counts):
            if c:
                post[t] = buf[off:off + c * step]
                off += c * step
    gone = set(drop)
    for name in ("lex_len", "sym_len", "symn_len", "frm_len"):
        a = np.frombuffer(s[name], dtype=np.int32)
        s[name] = np.delete(a, D).astype(np.int32).tobytes()
    s["lex_total"] = int(np.frombuffer(s["lex_len"], dtype=np.int32).sum())
    s["degenerate"] = bytes(b for d, b in enumerate(s["degenerate"]) if d not in gone)
    s["title_tokens"] = tuple(t for d, t in enumerate(s["title_tokens"]) if d not in gone)
    s["ids"] = tuple(i for d, i in enumerate(s["ids"]) if d not in gone)


def _patch_heavy(prepared):
    """The link file plus the new links (graph spread) and the new cards' text (the corpus scan)."""
    if getattr(prepared, "_heavy_patched", False):
        return
    orig = prepared.heavy

    def heavy():
        h = orig()
        if not h.get("_delta"):
            h = dict(h)
            have = {r[6] for r in h["edges"]}
            h["edges"] = tuple(h["edges"]) + tuple(r for r in prepared._delta_edges if r[6] not in have)
            h["corpus_low"] = h["corpus_low"] + "\n" + prepared._delta_corpus
            h["_delta"] = True
            prepared._heavy = h
        return h

    prepared.heavy = heavy
    prepared._heavy_patched = True
