"""fastrecall.py - the question-independent half of a math_recall.py search, prepared once and kept on disk.

A cold math_recall.py call spent about four fifths of its time (12 Oct 2026, R01 profile) redoing work that does not
depend on the question: reading every card and link from the store, computing each card's index terms, title tokens
and phrase bigrams, building the tool's title and link maps, and creating the ONNX query encoder. This module keeps that
work in a marshal file per (store state, code, MATHBRAIN_*/MATH_BRAIN_* environment) and runs the search from it. The
scores are the ones retrieve() computes: the same helpers, term by term, in the same order, so the printout is the same
to the byte (controls in improve_1012/fronts/R01/REPORT.md).

The fast path runs only on the retrieve() it was written against (PIN_RETRIEVAL). Any other retrieval.py, a lexical
document-frequency override, an expansion map, an expansion weight, a support or margin floor, a guard, a store read
that warned, or a prepared file that cannot be read sends the call down the unchanged live path. marshal loads plain
values only, never code.

    MATH_RECALL_FAST=0           the live path, always
    MATH_RECALL_FAST_ENCODER=0   keep the fast search, build the query encoder the usual way
    MATH_RECALL_CACHE_DIR=DIR    where the prepared files live (default ~/.claude/dreaming/cache/math_recall)
    MATH_RECALL_FAST_DEBUG=1     one line on stderr: fast or live, built or loaded, and why
"""
import hashlib
import json
import marshal
import math
import os
import re
import sys
import threading
import time
from array import array
from collections import Counter, defaultdict
from collections.abc import Mapping

FORMAT = 1
#: the retrieve() / _channel_scores() this module mirrors (math-brain-build src/math_brain/retrieval.py, 12 Oct 2026)
PIN_RETRIEVAL = "350904078336b15b89ae71cf3e57678c360fdb85949402dd42ea592e883d0a79"
#: the query encoder whose ONNX session the Encoder thread builds ahead of time
PIN_QUERY_EMBED = "855b962e6c462ef151ce9c9417208214bd13750af26ee35bb5c5d6dd3cae57d8"
KEEP_FILES = 4   # prepared states kept at most; an evaluation arm with other dials is one more
#: and at most this many bytes of them (main + link file), the newest always kept. A state was about 60 MB when the count
#: was set; on 28 Oct it was 370 MB, so four held 1.6 GB on a disk with 1.2 GB free (env MATH_RECALL_FAST_KEEP_MB).
KEEP_MB = 800
DREAMING = os.path.join(os.path.expanduser("~"), ".claude", "dreaming")
STATUS = {}      # what the last open_prepared()/search() did (read by the controls and MATH_RECALL_FAST_DEBUG)
_MEMO = {}       # key -> Prepared, for callers that run main() many times in one process


def enabled():
    return os.environ.get("MATH_RECALL_FAST", "1").strip().lower() not in ("0", "off", "false", "no")


def cache_dir():
    return os.environ.get("MATH_RECALL_CACHE_DIR") or os.path.join(DREAMING, "cache", "math_recall")


def _debug(msg):
    if os.environ.get("MATH_RECALL_FAST_DEBUG", "").strip() not in ("", "0"):
        try:
            sys.stderr.write("[fastrecall] %s\n" % msg)
        except Exception:
            pass


def _read(path):
    with open(path, "rb") as fh:
        return fh.read()


def _code_key(R, caller):
    """sha256 over every .py of the math_brain package in use, this file and the calling tool."""
    h = hashlib.sha256()
    pkg = os.path.dirname(os.path.abspath(R.__file__))
    for name in sorted(f for f in os.listdir(pkg) if f.endswith(".py")):
        h.update(name.encode("utf-8") + b"\0")
        h.update(_read(os.path.join(pkg, name)))
    for p in (os.path.abspath(__file__), os.path.abspath(caller)):
        h.update(_read(p))
    return h.hexdigest()


def _env_key():
    return "\n".join("%s=%s" % (k, v) for k, v in sorted(os.environ.items())
                     if k.upper().startswith(("MATHBRAIN_", "MATH_BRAIN_")))


def _store_key(store_path):
    """The store and its signal sidecar (whose rows the build reads), each with its write-ahead log; never the -shm
    files, which readers write. An absent log and an empty one are the same state (no pages beyond the main file):
    a read-only open creates an empty log and cannot remove it, a read-write close removes it, so the file comes and
    goes with other tools' reads while the content stays put. Keyed apart, the first call after each flip rebuilt, and
    when the flip came from the build's own sidecar read, the call refused and paid the build and the live path
    (16.5 s against 6 s on a quiet copy of the store, R01 NOTES 12 Oct)."""
    sp = os.path.abspath(store_path)
    parts = []
    for suffix in ("", "-wal", ".signals.sqlite", ".signals.sqlite-wal"):
        try:
            st = os.stat(sp + suffix)
        except OSError:
            st = None
        if suffix.endswith("-wal") and (st is None or st.st_size == 0):
            parts.append("%s:empty" % suffix)
        elif st is None:
            parts.append("%s:none" % suffix)
        else:
            parts.append("%s:%d:%d" % (suffix, st.st_size, st.st_mtime_ns))
    return "|".join(parts)


def _ints(values):
    return array("i", values).tobytes()


def _unints(blob):
    a = array("i")
    a.frombytes(blob)
    return a


def _node(row, GraphNode, NodeKind):
    return GraphNode(id=row[0], kind=NodeKind(row[1]), title=row[2], body=row[3], metadata=json.loads(row[4]),
                     schema_version=row[5], created_at=row[6])


def _edge(row, GraphEdge, EdgeKind):
    return GraphEdge(source_id=row[0], target_id=row[1], kind=EdgeKind(row[2]), rationale=row[3], weight=row[4],
                     metadata=json.loads(row[5]), id=row[6], schema_version=row[7], created_at=row[8])


def _plain(*values):
    return all(type(v) is str for v in values)


# ----------------------------------------------------------------------------------------------------------- build
def _read_store(store_path):
    import warnings
    from math_brain.store import ResearchStore
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with ResearchStore.open_read_only(store_path) as store:
            nodes = list(store.iter_nodes())
            edges = list(store.iter_edges(skip_unknown=True))
    return nodes, edges, caught


def _heavy_from(nodes, edges, title):
    rows = tuple((e.source_id, e.target_id, e.kind.value, e.rationale, e.weight, json.dumps(e.metadata), e.id,
                  e.schema_version, e.created_at) for e in edges)
    corpus_low = "\n".join((title.get(n.id, "") + " " + (n.body or "")).lower() for n in nodes)
    return {"format": FORMAT, "edges": rows, "corpus_low": corpus_low}


def build(store_path, R, derive):
    """Read the store once through the live readers and prepare everything a search needs. Returns (state, heavy), or
    (None, why) when the fast path must not be used for this store."""
    from math_brain.models import EdgeKind, GraphEdge, GraphNode, NodeKind
    nodes, edges, caught = _read_store(store_path)
    if caught:
        return None, "the store read warned"
    if R.EXPANSION or R.GLOBAL_LEXICAL_DF is not None:
        return None, "expansion map or lexical df override in use"
    prof = R.recall_profile()
    if not prof or float(prof.get("expansion_weight") or 0) > 0:
        return None, "recall profile off or expansion weight on"
    title_weight = prof["title_weight"]
    if not math.isfinite(title_weight) or title_weight < 0 or title_weight != int(title_weight):
        return None, "title weight not a whole number"
    whole = int(title_weight)

    node_rows = {}
    order = []
    for n in nodes:
        row = (n.id, n.kind.value, n.title, n.body, json.dumps(n.metadata), n.schema_version, n.created_at)
        if not _plain(n.id, n.title, n.body, n.created_at) or _node(row, GraphNode, NodeKind) != n:
            return None, "a card does not survive the round trip"
        node_rows[n.id] = row
        order.append(n.id)
    if len(node_rows) != len(nodes):
        return None, "card ids repeat"
    for e in edges:
        row = (e.source_id, e.target_id, e.kind.value, e.rationale, e.weight, json.dumps(e.metadata), e.id,
               e.schema_version, e.created_at)
        if _edge(row, GraphEdge, EdgeKind) != e:
            return None, "a link does not survive the round trip"

    accepted, exclusions = R.guard_nodes(tuple(nodes), None)
    accepted, revision_exclusions = R.current_revision_nodes(accepted, tuple(edges), known_ids=(n.id for n in nodes))
    exclusions = (*exclusions, *revision_exclusions)
    if len({n.id for n in accepted}) != len(accepted):
        return None, "accepted ids repeat"
    if not all(_plain(x.node_id, x.reason) for x in exclusions):
        return None, "an exclusion is not plain text"
    if R._SIGNAL_DB is None:
        R.load_signal_db(R.signal_db_path(store_path))
    if R._SIGNAL_DB is not None:
        R.prefetch_signals([n.title + " " + n.body for n in accepted])

    lex_post, sym_post, frm_post = defaultdict(list), defaultdict(list), defaultdict(list)
    lex_len, sym_len, symn_len, frm_len, title_tokens, degenerate = [], [], [], [], [], []
    bigrams, fold_bigrams = defaultdict(list), defaultdict(list)
    for i, n in enumerate(accepted):
        sig = R.index_signals(n.title + " " + n.body)
        lex_terms, sym_terms = R._card_terms(sig)
        tt = R._title_tokens(n.title)
        if title_weight > 0:
            title_terms = R._content_terms(tt)
            if title_terms:
                lex_terms = lex_terms + title_terms * whole
        for post, terms in ((lex_post, lex_terms), (sym_post, sym_terms), (frm_post, sig.formula_fingerprints)):
            for term, tf in Counter(terms).items():
                post[term] += (i, tf)
        lex_len.append(len(lex_terms))
        sym_len.append(len(sym_terms))
        symn_len.append(sum(1 for t in sym_terms if not t.startswith("punct:") and t != "op:subscript"))
        frm_len.append(len(sig.formula_fingerprints))
        tt = tuple(tt)
        if not all(type(t) is str for t in tt):
            return None, "a title token is not plain text"
        title_tokens.append(tt)
        degenerate.append(1 if R._is_degenerate(n) else 0)
        norm = tuple(R._strip_possessive(t) for t in tt)
        if len(norm) >= 2:
            for bg in set(zip(norm, norm[1:])):
                bigrams[bg].append(i)
            if R.PHRASE_NORM:
                fold = tuple(R._phrase_fold(t) for t in norm)
                for bg in set(zip(fold, fold[1:])):
                    fold_bigrams[bg].append(i)
    for post in (lex_post, sym_post, frm_post):
        if not all(type(t) is str for t in post):
            return None, "an index term is not plain text"
    tool = derive(nodes, edges)
    state = {
        "format": FORMAT,
        "ids": tuple(n.id for n in accepted),
        "order": tuple(order),
        "rows": node_rows,
        "exclusions": tuple((x.node_id, x.reason) for x in exclusions),
        "degenerate": bytes(degenerate),
        "lex": {t: _ints(v) for t, v in lex_post.items()}, "lex_len": _ints(lex_len), "lex_total": sum(lex_len),
        "sym": {t: _ints(v) for t, v in sym_post.items()}, "sym_len": _ints(sym_len), "symn_len": _ints(symn_len),
        "frm": {t: _ints(v) for t, v in frm_post.items()}, "frm_len": _ints(frm_len),
        "title_tokens": tuple(title_tokens),
        "bigrams": {k: _ints(v) for k, v in bigrams.items()},
        "fold_bigrams": {k: _ints(v) for k, v in fold_bigrams.items()},
        "phrase_norm": bool(R.PHRASE_NORM),
        "title_weight": title_weight,
        "tool": tool,
    }
    return state, _heavy_from(nodes, edges, tool["title"])


# ------------------------------------------------------------------------------------------------------- the cache
def _first_unmarshallable(obj, path="", depth=0):
    """Key path and type of the first value marshal refuses, or None."""
    try:
        marshal.dumps(obj)
        return None
    except ValueError:
        pass
    if depth > 6:
        return path, type(obj).__name__, repr(obj)[:120]
    if isinstance(obj, dict):
        for k, v in obj.items():
            hit = _first_unmarshallable(k, "%s{key %r}" % (path, k), depth + 1) or \
                _first_unmarshallable(v, "%s[%r]" % (path, k), depth + 1)
            if hit:
                return hit
    elif isinstance(obj, (list, tuple, set, frozenset)):
        for i, v in enumerate(obj):
            hit = _first_unmarshallable(v, "%s[%d]" % (path, i), depth + 1)
            if hit:
                return hit
    return path, type(obj).__name__, repr(obj)[:120]


def _write(path, obj):
    tmp = "%s.tmp%d" % (path, os.getpid())
    try:
        with open(tmp, "wb") as fh:
            fh.write(marshal.dumps(obj))
        os.replace(tmp, path)
    except ValueError:
        # a value marshal cannot write; log where it is, keep no half file
        try:
            os.remove(tmp)
        except OSError:
            pass
        try:
            hit = _first_unmarshallable(obj)
            with open(os.path.join(os.path.dirname(path), "unmarshal.log"), "a", encoding="utf-8") as fh:
                fh.write("%s %s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), os.path.basename(path), hit))
        except Exception:
            pass
        raise
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass


def _prune(folder):
    """Keep the newest prepared states, at most KEEP_FILES of them and KEEP_MB in all; the newest is always kept
    (a state is a main file and its .heavy file)."""
    try:
        budget = float(os.environ.get("MATH_RECALL_FAST_KEEP_MB", KEEP_MB)) * 1e6
    except ValueError:
        budget = KEEP_MB * 1e6
    try:
        mains = [os.path.join(folder, f) for f in os.listdir(folder)
                 if f.startswith("recall_") and f.endswith(".m") and not f.endswith(".heavy.m")]
        mains.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        keep, used = 0, 0
        for p in mains:
            size = sum(os.path.getsize(q) for q in (p, p[:-2] + ".heavy.m") if os.path.exists(q))
            if keep == 0 or (keep < KEEP_FILES and used + size <= budget):
                keep, used = keep + 1, used + size
            else:
                break
        for p in mains[keep:]:
            for q in (p, p[:-2] + ".heavy.m"):
                try:
                    os.remove(q)
                except OSError:
                    pass
    except OSError:
        pass


class _LazyEdges(Mapping):
    """edge id -> GraphEdge, read from the prepared link file the first time anything asks."""

    def __init__(self, prepared):
        self._p = prepared
        self._d = None

    def _map(self):
        if self._d is None:
            self._d = {getattr(e, "id", None): e for e in self._p.all_edges()}
        return self._d

    def __getitem__(self, key):
        return self._map()[key]

    def __iter__(self):
        return iter(self._map())

    def __len__(self):
        return len(self._map())


class _LazyNodes(Mapping):
    """card id -> GraphNode for every card, each built the first time it is asked for (a walk reads one or two)."""

    def __init__(self, prepared):
        self._p = prepared

    def __getitem__(self, key):
        if key not in self._p.s["rows"]:
            raise KeyError(key)
        return self._p.node(key)

    def __iter__(self):
        return iter(self._p.s["order"])

    def __len__(self):
        return len(self._p.s["order"])


class Prepared:
    def __init__(self, state, key, heavy_path, store_path, R):
        from math_brain.models import EdgeKind, GraphEdge, GraphNode, NodeKind
        self.s = state
        self.key = key
        self.heavy_key = key   # the key the link file on disk carries; self.key moves when extend() runs
        self.heavy_path = heavy_path
        self.store_path = store_path
        self.R = R
        self.ids = state["ids"]
        self.tool = state["tool"]
        self._heavy = None
        self._nodes = {}
        self._posmap = None
        self._GN, self._NK, self._GE, self._EK = GraphNode, NodeKind, GraphEdge, EdgeKind

    # -- what the tool reads besides the search
    def node(self, node_id):
        n = self._nodes.get(node_id)
        if n is None:
            n = self._nodes[node_id] = _node(self.s["rows"][node_id], self._GN, self._NK)
        return n

    def all_nodes(self):
        """Every card in store order, as iter_nodes() gives them."""
        return [self.node(i) for i in self.s["order"]]

    def node_map(self):
        return _LazyNodes(self)

    def heavy(self):
        if self._heavy is None:
            h = None
            try:
                h = marshal.loads(_read(self.heavy_path))
                if not isinstance(h, dict) or h.get("key") != self.heavy_key:
                    h = None
            except Exception:
                h = None
            if h is None:   # the link file is missing or belongs to another store state: take it from the store
                nodes, edges, _caught = _read_store(self.store_path)
                h = _heavy_from(nodes, edges, self.tool["title"])
            self._heavy = h
        return self._heavy

    def all_edges(self):
        """Every link in store order, as iter_edges(skip_unknown=True) gives them."""
        return [_edge(r, self._GE, self._EK) for r in self.heavy()["edges"]]

    def edge_map(self):
        return _LazyEdges(self)

    def corpus_low(self):
        return self.heavy()["corpus_low"]

    def _pos(self):
        if self._posmap is None:
            self._posmap = {i: k for k, i in enumerate(self.ids)}
        return self._posmap

    # -- the search
    def usable_now(self, kw):
        R = self.R
        return (not R.EXPANSION and R.GLOBAL_LEXICAL_DF is None and bool(R.PHRASE_NORM) == self.s["phrase_norm"]
                and float(kw.get("expansion_weight") or 0) == 0
                and kw.get("title_weight") == self.s["title_weight"]
                and kw.get("min_support_score") == 0.0 and kw.get("min_margin") == 0.0
                and kw.get("guard") is None and kw.get("background_hubs") is None
                and kw.get("current_only", True) is True)

    def search(self, query, kwargs, encoder_ready=None):
        """retrieve_for_recall(query, nodes=<every card>, **kwargs) from the prepared state, or None when it cannot
        answer exactly; the caller then runs the live search."""
        try:
            kw = dict(kwargs)
            for key, value in self.R.recall_profile().items():
                kw.setdefault(key, value)
            if not self.usable_now(kw):
                STATUS["search"] = "live: a dial the prepared state does not cover"
                return None
            res = self.retrieve(query, encoder_ready=encoder_ready, **kw)
            STATUS["search"] = "fast"
            return res
        except Exception as exc:
            STATUS["search"] = "live: %s: %s" % (type(exc).__name__, str(exc)[:200])
            _debug(STATUS["search"])
            return None

    def _channel(self, query_terms, post, lengths, total, *, channel, weight, notation_mode=False, scheme="base",
                 bm25_k1=None, bm25_b=None):
        """_channel_scores() over the prepared postings: every card of the pool counts in N and in the mean length, and
        a card's sum runs over the query terms in sorted order, as there."""
        R = self.R
        bm25 = channel is R.ScoreChannel.LEXICAL and scheme == "bm25"
        k1 = R.BM25_K1 if bm25_k1 is None else bm25_k1
        b = R.BM25_B if bm25_b is None else bm25_b
        if channel is R.ScoreChannel.SYMBOL and notation_mode:
            query_terms = tuple(t for t in query_terms if not t.startswith("punct:") and t != "op:subscript")
        query_counts = Counter(query_terms)
        if not query_counts:
            return {}, {}
        total_documents = max(1, len(self.ids))
        scores = {}
        traces = defaultdict(list)
        average_length = 1.0
        if bm25:
            average_length = max(1e-9, total / total_documents)
        lens = _unints(lengths)
        for term, query_weight in sorted(query_counts.items()):
            blob = post.get(term)
            if blob is None:
                continue
            pairs = _unints(blob)
            frequency = len(pairs) // 2
            if channel is R.ScoreChannel.SYMBOL and notation_mode:
                inverse_frequency = math.log((total_documents + 1) / (frequency + 0.5))
            elif bm25:
                inverse_frequency = R._bm25_idf(total_documents, frequency)
            else:
                inverse_frequency = math.log1p(total_documents / (1 + frequency))
            detail = f"matched normalized term {term!r}"
            for j in range(0, len(pairs), 2):
                d, tf = pairs[j], pairs[j + 1]
                length = max(1, lens[d])
                if bm25:
                    amount = (
                        weight
                        * R._query_term_weight(query_weight)
                        * inverse_frequency
                        * tf * (k1 + 1)
                        / (tf + k1 * (1 - b + b * length / average_length))
                    )
                else:
                    amount = (
                        weight
                        * R._query_term_weight(query_weight)
                        * inverse_frequency
                        * (1 + math.log1p(tf))
                        / math.sqrt(length)
                    )
                node_id = self.ids[d]
                scores[node_id] = scores.get(node_id, 0.0) + amount
                traces[node_id].append(R.ScoreTrace(channel, amount, detail))
        return scores, traces

    def retrieve(self, query, *, edges=None, limit=10, seed_limit=8, graph_depth=2, decay=0.55,
                 min_support_score=0.15, min_margin=0.0, guard=None, mode=None, current_only=True,
                 background_hubs=None, background_limit=5, lexical_scheme=None, title_weight=0.0,
                 semantic_weight=None, bm25_k1=None, bm25_b=None, expansion_weight=0.0, encoder_ready=None):
        """retrieve(query, nodes=<every card>, ...) for the tool's call; `edges` is read only when graph_depth > 0."""
        R = self.R
        S = R.ScoreChannel
        if guard is not None or background_hubs is not None or not current_only or expansion_weight:
            raise RuntimeError("a dial the prepared state does not cover")
        mode = R.RetrievalMode(mode if mode is not None else R.RetrievalMode.DISCOVERY)
        scheme = (lexical_scheme or R.LEXICAL_SCHEME or "base").strip().lower()
        if scheme not in R.LEXICAL_SCHEMES:
            raise ValueError("lexical_scheme must be one of " + ", ".join(R.LEXICAL_SCHEMES))
        if not math.isfinite(title_weight) or title_weight < 0 or title_weight != int(title_weight):
            raise ValueError("title_weight must be a whole number, zero or more")
        if semantic_weight is not None and (not math.isfinite(semantic_weight) or semantic_weight < 0):
            raise ValueError("semantic_weight must be finite and nonnegative")
        if limit <= 0 or seed_limit <= 0:
            raise ValueError("limit and seed_limit must be positive")
        if graph_depth < 0:
            raise ValueError("graph_depth must be nonnegative")
        if not 0 <= decay <= 1:
            raise ValueError("decay must be in [0, 1]")
        if (not math.isfinite(min_support_score) or not math.isfinite(min_margin) or min_support_score < 0
                or min_margin < 0):
            raise ValueError("abstention thresholds must be nonnegative")
        if graph_depth > 0 and edges is None:
            raise RuntimeError("the spreading needs the links")

        signals = R.index_signals(query)
        exclusions = tuple(R.GuardExclusion(i, r) for i, r in self.s["exclusions"])
        if signals.empty:
            return R.RetrievalResult(signals, (), True, R.AbstentionReason.NO_INDEXABLE_SIGNALS, exclusions)
        lexical, lexical_traces = self._channel(
            R._content_terms(signals.lexical_tokens), self.s["lex"], self.s["lex_len"], self.s["lex_total"],
            channel=S.LEXICAL, weight=1.0, scheme=scheme, bm25_k1=bm25_k1, bm25_b=bm25_b)
        notation = R._notation_idf_mode(signals)
        symbols, symbol_traces = self._channel(
            R._symbol_terms(signals.math_tokens), self.s["sym"], self.s["symn_len"] if notation else self.s["sym_len"],
            0, channel=S.SYMBOL, weight=1.35, notation_mode=notation)
        formulas, formula_traces = self._channel(
            signals.formula_fingerprints, self.s["frm"], self.s["frm_len"], 0, channel=S.FORMULA, weight=2.0)
        # the meaning channel, as in retrieve()
        semantic = {}
        semantic_traces = defaultdict(list)
        _semantic = R._semantic
        _max_numbers = int(os.environ.get("MATH_BRAIN_SEMANTIC_MAX_NUMBERS", "1") or 1)
        _semantic_on = _semantic.enabled() if semantic_weight is None else semantic_weight > 0
        if _semantic_on and len(re.findall(r"\d+", query or "")) <= _max_numbers:
            if encoder_ready is not None:
                encoder_ready()
            try:
                if _semantic.available():
                    details = _semantic.standout_details(query, self.ids)
                    if semantic_weight is not None:
                        _per_sd = float(_semantic.WEIGHT) if _semantic.WEIGHT else 0.0
                        details = {
                            node_id: (amount / _per_sd * semantic_weight, cosine)
                            for node_id, (amount, cosine) in details.items()
                            if _per_sd > 0 and amount > 0
                        }
                    semantic = {node_id: amount for node_id, (amount, _cos) in details.items()}
                    for node_id, (amount, cosine) in details.items():
                        _sd = amount if semantic_weight is None else amount / semantic_weight
                        semantic_traces[node_id].append(
                            R.ScoreTrace(
                                S.SEMANTIC,
                                amount,
                                f"{_sd:.2f} sd above the corpus mean similarity "
                                f"(cosine {cosine:.3f})",
                            )
                        )
            except Exception:
                semantic, semantic_traces = {}, defaultdict(list)
        pos = self._pos()
        base_scores = {}
        for node_id in sorted(set(lexical) | set(symbols) | set(formulas) | set(semantic), key=pos.__getitem__):
            base_scores[node_id] = (lexical.get(node_id, 0.0) + symbols.get(node_id, 0.0)
                                    + formulas.get(node_id, 0.0) + semantic.get(node_id, 0.0))
        base_scores = {node_id: score for node_id, score in base_scores.items() if score > 0}
        seeds = sorted(base_scores, key=lambda item: (-base_scores[item], item))[:seed_limit]
        graph, warnings, graph_traces = R._graph_spread(
            seeds, base_scores, tuple(edges) if graph_depth > 0 else (), set(self.ids),
            graph_depth=graph_depth, decay=decay, mode=mode)
        phrase_scores, phrase_traces = R._title_phrase_scores(
            signals.lexical_tokens, self._phrase_candidates(signals.lexical_tokens), weight=R.TITLE_PHRASE_WEIGHT)
        for node_id, amount in phrase_scores.items():
            lexical[node_id] = lexical.get(node_id, 0.0) + amount
            lexical_traces.setdefault(node_id, []).extend(phrase_traces[node_id])

        deg = self.s["degenerate"]
        light = []
        for node_id in set(lexical) | set(symbols) | set(formulas) | set(semantic) | set(graph) | set(warnings):
            components = (
                lexical.get(node_id, 0.0),
                symbols.get(node_id, 0.0),
                formulas.get(node_id, 0.0),
                semantic.get(node_id, 0.0),
                graph.get(node_id, 0.0),
                warnings.get(node_id, 0.0),
            )
            if not any(components):
                continue
            light.append((node_id, R.SearchHit(None, *components)))
        light.sort(key=lambda x: (deg[pos[x[0]]] == 1, -x[1].total_score, x[0]))
        ranked = []
        for node_id, h in light[:limit]:
            trace = tuple(
                sorted(
                    list(lexical_traces.get(node_id, [])) + list(symbol_traces.get(node_id, []))
                    + list(formula_traces.get(node_id, [])) + list(semantic_traces.get(node_id, []))
                    + list(graph_traces.get(node_id, [])),
                    key=lambda item: (
                        item.channel.value,
                        item.detail,
                        item.source_node_id or "",
                        item.edge_id or "",
                        item.path,
                    ),
                )
            )
            ranked.append(R.SearchHit(self.node(node_id), h.lexical_score, h.symbol_score, h.formula_score,
                                      h.semantic_score, h.graph_score, h.warning_score, trace))
        ranked = tuple(ranked)
        if not ranked:
            return R.RetrievalResult(signals, (), True, R.AbstentionReason.NO_CANDIDATE_MATCH, exclusions)
        # retrieve()'s two gates, on the servable part of the whole pool
        servable = [x for x in light if deg[pos[x[0]]] != 1] or light
        by_support = sorted(servable, key=lambda x: (-x[1].support_score, x[0]))
        if by_support[0][1].support_score < min_support_score:
            return R.RetrievalResult(signals, ranked, True, R.AbstentionReason.BELOW_SUPPORT_THRESHOLD, exclusions)
        if len(by_support) > 1 and by_support[0][1].support_score - by_support[1][1].support_score < min_margin:
            raise RuntimeError("a near tie under a margin floor is left to the live search")
        return R.RetrievalResult(signals, ranked, False, None, exclusions, ())

    def _phrase_candidates(self, query_tokens):
        """{id: title tokens} for every card whose title shares a bigram (verbatim, or folded under PHRASE_NORM) with
        the query, in pool order: a superset of the cards _title_phrase_scores() scores, and it scores each alone."""
        R = self.R
        norm = tuple(R._strip_possessive(t) for t in query_tokens)
        if len(norm) < 2:
            return {}
        docs = set()
        for bg in set(zip(norm, norm[1:])):
            blob = self.s["bigrams"].get(bg)
            if blob is not None:
                docs.update(_unints(blob))
        if R.PHRASE_NORM:
            fold = tuple(R._phrase_fold(t) for t in norm)
            for bg in set(zip(fold, fold[1:])):
                blob = self.s["fold_bigrams"].get(bg)
                if blob is not None:
                    docs.update(_unints(blob))
        tt = self.s["title_tokens"]
        return {self.ids[d]: tt[d] for d in sorted(docs)}


# ---------------------------------------------------------------------------------------------------------- opening
STALE_S = 2700.0   # a caller may answer from a state this many seconds behind the store (MATH_RECALL_FAST_STALE_S)


def _stale_window(stale_s):
    if stale_s is not None:
        return float(stale_s)
    try:
        v = os.environ.get("MATH_RECALL_FAST_STALE_S", "")
        return float(v) if v.strip() else STALE_S
    except ValueError:
        return STALE_S


DELTA = os.environ.get("MATH_RECALL_DELTA", "1").strip().lower() not in ("0", "off", "false")
DELTA_CALL_MAX = int(os.environ.get("MATH_RECALL_DELTA_CALL_MAX", "2000") or 2000)   # cards indexed in one call
DELTA_MAX_AGE_S = float(os.environ.get("MATH_RECALL_DELTA_MAX_AGE_S", "21600") or 21600) if DELTA else 0.0


def _bring_up_to_date(prepared, store_path, R, derive, path, heavy_path, folder, key, window):
    """A state behind the store: index the cards it has not seen (fastrecall_delta.extend, in memory only - the
    prebuild stays the one writer of states on disk). Refused or off: within the window serve it as it is, past the
    window build as before."""
    why = "off"
    if DELTA:
        try:
            import fastrecall_delta as FD
            FD.MAX_CARDS = min(FD.MAX_CARDS, DELTA_CALL_MAX)
            short_ids = getattr(sys.modules.get(getattr(derive, "__module__", "")), "short_ids", None)
            t0 = time.time()
            n, why = FD.extend(prepared, store_path, short_ids)
            if n is not None:
                STATUS.update(extended=n, extend_s=round(time.time() - t0, 3), stale_s=0)
                # the extended state is this store state: caches keyed by P.key (the literal lane) must not serve the old one
                prepared.key = key
                _debug("fast: extended by %d card(s) in %.2f s" % (n, time.time() - t0))
                return prepared
        except Exception as exc:
            why = "%s: %s" % (type(exc).__name__, str(exc)[:200])
            # extend() may have failed half way: never serve a half-extended state
            prepared = None
    STATUS["extend_refused"] = why
    if not STATUS.get("past_window") and prepared is not None:
        return prepared
    if prepared is None and not STATUS.get("past_window"):
        state = marshal.loads(_read(path))
        return Prepared(state, state["key"], heavy_path, store_path, R)
    t_read = time.time()
    built, heavy = build(store_path, R, derive)
    if built is None:
        STATUS["why"] = heavy
        return None
    if _store_key(store_path) != key.split("|", 1)[1]:
        STATUS["moved"] = True
    built["key"] = heavy["key"] = key
    built["read_at"] = t_read
    os.makedirs(folder, exist_ok=True)
    _write(heavy_path, heavy)
    _write(path, built)
    _prune(folder)
    STATUS.update(built=True, stale_s=0)
    return Prepared(built, key, heavy_path, store_path, R)


def open_prepared(store_path, R, derive, caller, stale_s=None):
    """The prepared state for this store, code and environment: loaded, or built and written. None when the fast path
    is off or must not be used (STATUS["why"] says why).

    A state up to stale_s seconds behind the store (default STALE_S) is served as it is: on 27-28 Oct the store moved
    every minute or two while one read of it took 3 to 115 minutes on the starved disk, so an exact match never landed
    and every call read the whole store live (8 of 8 prebuild runs 00:02-00:24 28 Oct ended "the store changed while it
    was read"; recall calls timed out at 200-240 s). Cards written in the last stale_s seconds can be missing from such
    an answer (STATUS["stale_s"] says how far behind). A build that saw the store move is kept for the same reason; the
    live path reads a moving store the same way. stale_s=0 is the old exact behaviour."""
    STATUS.clear()
    STATUS.update(fast=False, built=False, path=None, why="", stale_s=0)
    window = _stale_window(stale_s)
    if not enabled():
        STATUS["why"] = "MATH_RECALL_FAST=0"
        return None
    try:
        if hashlib.sha256(_read(os.path.abspath(R.__file__))).hexdigest() != PIN_RETRIEVAL:
            STATUS["why"] = "retrieval.py is not the pinned one"
            _debug("live: " + STATUS["why"])
            return None
        name = hashlib.sha256(("%d|%s|%s|%s|%s" % (FORMAT, sys.version, _code_key(R, caller), _env_key(),
                                                   os.path.normcase(os.path.abspath(store_path)))).encode("utf-8")
                              ).hexdigest()[:24]
        key = "%s|%s" % (name, _store_key(store_path))
        memo = _MEMO.get(key)
        if memo is not None:
            STATUS.update(fast=True, path=memo.heavy_path)
            return memo
        folder = cache_dir()
        path = os.path.join(folder, "recall_%s.m" % name)
        heavy_path = os.path.join(folder, "recall_%s.heavy.m" % name)
        STATUS["path"] = path
        state = None
        state_key = key
        try:
            got = marshal.loads(_read(path))
            if isinstance(got, dict) and got.get("format") == FORMAT and isinstance(got.get("key"), str):
                if got["key"] == key:
                    state = got
                elif window > 0 and got["key"].split("|", 1)[0] == name:
                    age = time.time() - float(got.get("read_at") or os.path.getmtime(path))
                    # RECALL-10X lead 2: a state past the window is still used when extend() can bring it up to date
                    if 0 <= age <= max(window, DELTA_MAX_AGE_S):
                        state, state_key = got, got["key"]
                        STATUS["stale_s"] = int(age)
                        STATUS["past_window"] = age > window
        except Exception:
            state, state_key = None, key
        if state is None:
            t_read = time.time()
            built, heavy = build(store_path, R, derive)
            if built is None:
                STATUS["why"] = heavy
                _debug("live: " + heavy)
                return None
            if _store_key(store_path) != key.split("|", 1)[1]:
                if window <= 0:
                    STATUS["why"] = "the store changed while it was read"
                    _debug("live: " + STATUS["why"])
                    return None
                STATUS["moved"] = True
            built["key"] = heavy["key"] = key
            built["read_at"] = t_read
            os.makedirs(folder, exist_ok=True)
            _write(heavy_path, heavy)
            _write(path, built)
            _prune(folder)
            state = built
            STATUS["built"] = True
        prepared = Prepared(state, state_key, heavy_path, store_path, R)
        if state_key != key and not STATUS["built"]:
            prepared = _bring_up_to_date(prepared, store_path, R, derive, path, heavy_path, folder, key, window)
            if prepared is None:
                return None
        _MEMO.clear()
        _MEMO[key] = prepared
        STATUS["fast"] = True
        _debug("fast: %s %s%s" % ("built" if STATUS["built"] else "loaded", path,
                                  " (%d s behind the store)" % STATUS["stale_s"] if STATUS["stale_s"] else ""))
        return prepared
    except Exception as exc:  # the live path is always there
        STATUS["why"] = "%s: %s" % (type(exc).__name__, str(exc)[:200])
        _debug("live: " + STATUS["why"])
        return None


# ------------------------------------------------------------------------------------------- the query encoder
def wants_encoder(queries, R):
    """True when the search will embed at least one of these wordings (the meaning channel's own test in retrieve())."""
    try:
        if not enabled() or os.environ.get("MATH_RECALL_FAST_ENCODER", "1").strip().lower() in ("0", "off"):
            return False
        weight = R.recall_profile().get("semantic_weight")
        on = R._semantic.enabled() if weight is None else weight > 0
        most = int(os.environ.get("MATH_BRAIN_SEMANTIC_MAX_NUMBERS", "1") or 1)
        return bool(on) and any(len(re.findall(r"\d+", q or "")) <= most for q in queries)
    except Exception:
        return False


class Encoder:
    """Builds query_embed's ONNX session in a thread, from a copy of the same model saved after ONNX Runtime's graph
    optimisation (so the session skips that step), while the main thread prepares the search. Same tokenizer, same
    thread count and options; the vectors were bitwise identical on every dev question (R01 NOTES). Anything unexpected
    leaves query_embed alone, and it builds its own session as before. The caller joins it before embedding and
    before exit."""

    def __init__(self):
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self._run, name="recall-encoder", daemon=True)
        self.thread.start()
        return self

    def wait(self):
        if self.thread is not None:
            self.thread.join()
            self.thread = None

    @staticmethod
    def _run():
        try:
            qe_path = os.path.join(DREAMING, "query_embed.py")
            if hashlib.sha256(_read(qe_path)).hexdigest() != PIN_QUERY_EMBED:
                return
            qe = sys.modules.get("query_embed")
            if qe is None:
                import importlib.util
                spec = importlib.util.spec_from_file_location("query_embed", qe_path)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                qe = sys.modules.setdefault("query_embed", mod)
            if os.path.normcase(os.path.abspath(getattr(qe, "__file__", "") or "")) != os.path.normcase(qe_path):
                return
            if "sess" in qe._STATE or not qe.available():
                return
            import onnxruntime as ort
            from tokenizers import Tokenizer
            src = os.path.join(qe.MODEL_DIR, "model.onnx")
            st = os.stat(src)
            # never the platform module here: on this Python it asks WMI for the processor
            tag = hashlib.sha256(("%d|%d|%s|%s|%s" % (
                st.st_size, st.st_mtime_ns, ort.__version__, os.environ.get("PROCESSOR_IDENTIFIER", ""),
                os.environ.get("PROCESSOR_ARCHITECTURE", ""))).encode("utf-8")).hexdigest()[:20]
            folder = cache_dir()
            opt = os.path.join(folder, "bge_small_opt_%s.onnx" % tag)
            if not os.path.exists(opt):
                os.makedirs(folder, exist_ok=True)
                tmp = "%s.tmp%d" % (opt, os.getpid())
                so = ort.SessionOptions()
                so.intra_op_num_threads = 2
                so.log_severity_level = 3
                so.optimized_model_filepath = tmp
                ort.InferenceSession(src, so, providers=["CPUExecutionProvider"])
                try:
                    os.replace(tmp, opt)
                except OSError:
                    try:
                        os.remove(tmp)
                    except OSError:
                        pass
                    return
            so = ort.SessionOptions()
            so.intra_op_num_threads = 2
            so.log_severity_level = 3
            so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_DISABLE_ALL
            sess = ort.InferenceSession(opt, so, providers=["CPUExecutionProvider"])
            tok = Tokenizer.from_file(os.path.join(qe.MODEL_DIR, "tokenizer.json"))
            tok.enable_truncation(512)
            tok.enable_padding()
            if "sess" not in qe._STATE:
                qe._STATE["tok"] = tok
                qe._STATE["sess"] = sess
        except Exception:
            return
