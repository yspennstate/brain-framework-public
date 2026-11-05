# -*- coding: utf-8 -*-
"""diffusion.py - spreading activation over the use-grown brain graph, as a personalised PageRank, and its blend
with the brain's lexical recall behind one dial.

The graph is the store graph_store.py builds from the agents' touches (claude-9209cf81): context -> target counts
(whenever a memory was read, a file or folder first touched, a tool called or a call failed, every distinct item the
session touched in the ten minutes before it) and co-occurrence counts between non-word items.

The walk. From a node the walker steps along its positive associations. A context -> target edge weighs
    a(i -> j) = max(0, ln(P(j | i) / P(j))) * ln(1 + n_ij)
(the score GraphStore.rows already ranks by), a co-occurrence edge the same on the co-occurrence counts, times CO_W.
Each row keeps its CAP strongest edges and is normalised to a probability. With probability ALPHA the walker jumps
back to the seed - the words of the question and whatever the session is touching - so the stationary distribution
p_s is the seed's personalised PageRank. A node with no way out sends its mass back to the seed.

The score. p_s favours the nodes every walk ends in. A node scores p_s(m) / p_0(m)^GAMMA, where p_0 is the same walk
seeded by every context node in proportion to how often it was a context: GAMMA 0 keeps the raw mass, 1 is pure lift
(how much more this seed reaches m than an average seed does).

The blend. blend() fuses the lexical ranking with the graph's by weighted reciprocal rank,
    s(m) = (1 - LAMBDA) / (K + r_lex(m)) + LAMBDA / (K + r_graph(m)),
so LAMBDA 0 is today's recall order exactly and 1 is the graph alone.
"""
import collections
import json
import math
import os
import re
import sys

import numpy as np


def tail_digest(path, span=65536):
    """A hash of a file's size and its last span bytes. An npz ends in its zip directory, which carries every member's
    CRC-32, so any change to any array changes this tail; a same-size rewrite that keeps the write time changes it too.
    None when the file cannot be read."""
    import hashlib
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            fh.seek(max(0, size - span))
            tail = fh.read(span)
    except OSError:
        return None
    return "%d:%s" % (size, hashlib.sha1(tail).hexdigest()[:20])


def source_identity(store):
    """What a walk is built from: edges.npz and nodes.json, each by its tail digest."""
    return "|".join("%s=%s" % (name, tail_digest(os.path.join(store, name))) for name in ("edges.npz", "nodes.json"))

WORD = re.compile(r"[a-z][a-z0-9_]{3,}")
# the capture's own stop list (extract_touch_events.py), so a query word maps to the node the capture would have made
STOP = set("""the a an and or of to in on for with at by from is are was were be been it its this that these those i you
he she we they me my your our their his her them us do does did done not no yes if then else so as but can will would
should could have has had just also any all some more most less very into out up down over under again about after before
when where what which who whom why how than too only own same other such both each few here there please make sure need
want use using used get got let lets like know think see look go going ok okay thanks now one two new still next first
last time way work working file files line lines run running check""".split())
MEM_KINDS = ("mem", "memw", "recalled", "served", "reflex")
WORD_KINDS = ("kw", "said")


def _cap_rows(A, cap):
    """Keep each row's `cap` largest entries (ties at the threshold kept)."""
    A = A.tocsr()
    counts = np.diff(A.indptr)
    for r in np.flatnonzero(counts > cap):
        lo, hi = A.indptr[r], A.indptr[r + 1]
        seg = A.data[lo:hi]
        thr = np.partition(seg, len(seg) - cap)[len(seg) - cap]
        seg[seg < thr] = 0.0
    A.eliminate_zeros()
    return A


_KERNEL = []


def _csr_kernel():
    """scipy's compiled CSR routines (scipy.sparse._sparsetools), or None. When scipy.sparse is not imported yet the
    extension is loaded from its file on its own, in a few milliseconds; importing scipy.sparse took 0.8 s of every
    recall's 5 s on 13 Oct, for the one product a step of the walk needs."""
    if not _KERNEL:
        mod = sys.modules.get("scipy.sparse._sparsetools")
        if mod is None:
            try:
                import importlib.machinery
                import importlib.util
                folder = os.path.join(os.path.dirname(importlib.util.find_spec("scipy").origin), "sparse")
                for suffix in importlib.machinery.EXTENSION_SUFFIXES:
                    path = os.path.join(folder, "_sparsetools" + suffix)
                    if os.path.exists(path):
                        # the extension's init function is named for _sparsetools, so the spec carries that name
                        spec = importlib.util.spec_from_file_location("_sparsetools", path)
                        mod = importlib.util.module_from_spec(spec)
                        spec.loader.exec_module(mod)
                        break
            except Exception:
                mod = None
        _KERNEL.append(mod if hasattr(mod, "csr_matvec") else None)
    return _KERNEL[0]


class CSR:
    """A square CSR matrix whose product with a float32 vector or column is scipy's own: csr_matvec on the same arrays,
    the routine csr_matrix @ x calls for those shapes, so a walk comes out the same bit for bit. Anything else asked of
    it goes to a scipy.sparse.csr_matrix made the first time it is needed."""

    def __init__(self, data, indices, indptr, n):
        self.data, self.indices, self.indptr, self.shape = data, indices, indptr, (n, n)
        self._sp = None

    def _scipy(self):
        if self._sp is None:
            import scipy.sparse as sp
            self._sp = sp.csr_matrix((self.data, self.indices, self.indptr), shape=self.shape)
        return self._sp

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._scipy(), name)

    def __matmul__(self, x):
        n = self.shape[0]
        k = _csr_kernel()
        if (k is not None and type(x) is np.ndarray and x.shape in ((n,), (n, 1)) and x.dtype == np.float32
                and self.data.dtype == np.float32):
            y = np.zeros(n, np.float32)
            try:
                k.csr_matvec(n, n, self.indptr, self.indices, self.data, x.ravel(), y)
            except Exception:
                return self._scipy() @ x
            return y if x.ndim == 1 else y.reshape(n, 1)
        return self._scipy() @ x


class Diffusion:
    CACHE = "diffusion_cache.npz"

    @classmethod
    def cached(cls, store, co_w=0.5, cap=300, alpha=0.3, iters=20, cache_dir=None, build=True):
        """The walk built once per store and parameter set, then read back in a fraction of a second (the live
        recall path cannot pay the build - seven seconds idle, over two minutes on this laptop under load - on
        every call). Rebuilt when the store's edges are newer. cache_dir keeps the cache outside the store (a store
        another agent rebuilds is not ours to write into). build=False returns None instead of building."""
        import hashlib
        tag = "diffusion_cache_c%g_k%d_a%g_i%d" % (co_w, cap, alpha, iters)
        if cache_dir:
            os.makedirs(cache_dir, exist_ok=True)
            key = hashlib.sha1(os.path.abspath(store).lower().encode("utf-8")).hexdigest()[:12]
            path = os.path.join(cache_dir, "%s_%s.npz" % (key, tag))
        else:
            path = os.path.join(store, tag + ".npz")
        src = os.path.join(store, "edges.npz")
        src_id = source_identity(store)
        if os.path.exists(path) and cls._cache_matches(path, src, src_id):
            try:
                return cls._load(path, store, alpha, iters)
            except Exception:
                pass
        if not build:
            return None
        g = cls(store, co_w=co_w, cap=cap, alpha=alpha, iters=iters)
        try:
            tmp = path + ".tmp.npz"
            np.savez(tmp, indptr=g.PT.indptr, indices=g.PT.indices, data=g.PT.data, dangling=g.dangling, p0=g.p0,
                     out_sum=g.out_sum, mn=g._mn, ms=g._ms, nnz=np.array([g.nnz]), src_id=np.array(src_id))
            os.replace(tmp, path)
        except Exception:
            pass
        return g

    @staticmethod
    def _cache_matches(path, src, src_id):
        """Whether the walk cached at path was built from the store as it is now. A cache records the identity of the
        store it was built from (source_identity) and is used only while that identity holds. Until 30 Sep it was used
        whenever it was newer than edges.npz, so a store rolled back to an older edges.npz with its time kept (a restore
        with cp -p or robocopy) was walked with the newer store's matrix (1000-page report D05-T04). A cache written
        before the identity was recorded keeps the old rule until the next build replaces it."""
        try:
            with np.load(path) as z:
                if "src_id" in z.files:
                    return str(z["src_id"]) == src_id
        except Exception:
            return False
        try:
            return os.path.getmtime(path) >= os.path.getmtime(src)
        except OSError:
            return False

    @classmethod
    def _load(cls, path, store, alpha, iters):
        z = np.load(path)
        g = cls.__new__(cls)
        d = json.load(open(os.path.join(store, "nodes.json"), encoding="utf-8"))
        g.names, g.meta = d["names"], d["meta"]
        g.N = len(g.names)
        g.id = {n: i for i, n in enumerate(g.names)}
        g.alpha, g.iters, g.dtype = alpha, iters, np.float32
        g.PT = CSR(z["data"], z["indices"], z["indptr"], g.N)
        g.dangling, g.p0, g.out_sum = z["dangling"], z["p0"], z["out_sum"]
        g._mn, g._ms, g.nnz = z["mn"], z["ms"], int(z["nnz"][0])
        g.mem_nodes = collections.defaultdict(list)
        slugs = {}
        for i in g._mn.tolist():
            slugs.setdefault(g.names[i].partition(":")[2], []).append(i)
        g.mem_nodes.update(slugs)
        g.slug_list = sorted(g.mem_nodes)
        g.slug_ix = {s: k for k, s in enumerate(g.slug_list)}
        # _ms indexes the sorted slug list the cache was written with; rebuild it the same way to be safe
        ms = [g.slug_ix[g.names[i].partition(":")[2]] for i in g._mn.tolist()]
        g._ms = np.array(ms, np.int64)
        return g

    def __init__(self, store, co_w=0.5, cap=300, alpha=0.3, iters=20, dtype=np.float32):
        import scipy.sparse as sp
        z = np.load(os.path.join(store, "edges.npz"))
        d = json.load(open(os.path.join(store, "nodes.json"), encoding="utf-8"))
        self.names, self.meta = d["names"], d["meta"]
        self.N = N = len(self.names)
        self.id = {n: i for i, n in enumerate(self.names)}
        self.alpha, self.iters, self.dtype = alpha, iters, dtype
        keys, cnt = z["keys"], z["cnt"].astype(np.float64)
        out_sum, in_w = z["out_sum"].astype(np.float64), z["in_w"].astype(np.float64)
        total = float(cnt.sum())
        src = (keys >> 32).astype(np.int64)
        dst = (keys & 0xFFFFFFFF).astype(np.int64)
        with np.errstate(divide="ignore", invalid="ignore"):
            a = np.maximum(np.log((cnt / out_sum[src]) / (in_w[dst] / total)), 0.0) * np.log1p(cnt)
        ck, cc = z["co_keys"], z["co_cnt"].astype(np.float64)
        cs = (ck >> 32).astype(np.int64)
        cd = (ck & 0xFFFFFFFF).astype(np.int64)
        co_out = np.bincount(cs, weights=cc, minlength=N)
        ctot = float(cc.sum()) or 1.0
        with np.errstate(divide="ignore", invalid="ignore"):
            ca = np.maximum(np.log(cc * ctot / (co_out[cs] * co_out[cd])), 0.0) * np.log1p(cc) * co_w
        rows = np.concatenate((src, cs))
        cols = np.concatenate((dst, cd))
        vals = np.concatenate((a, ca))
        ok = np.isfinite(vals) & (vals > 0)
        A = sp.csr_matrix((vals[ok], (rows[ok], cols[ok])), shape=(N, N))
        A.sum_duplicates()
        A = _cap_rows(A, cap)
        rs = np.asarray(A.sum(axis=1)).ravel()
        self.dangling = rs <= 0
        P = sp.diags(np.where(rs > 0, 1.0 / np.maximum(rs, 1e-300), 0.0)) @ A
        self.PT = P.T.tocsr().astype(dtype)
        self.nnz = int(A.nnz)
        self.out_sum = out_sum
        # the average seed: every context node in proportion to how often it was one
        base = out_sum / out_sum.sum()
        self.p0 = self.ppr(base.reshape(-1, 1).astype(dtype))[:, 0].astype(np.float64)
        # memory slug -> its nodes (read, written, recalled, served, fired)
        self.mem_nodes = collections.defaultdict(list)
        for i, n in enumerate(self.names):
            k, _, x = n.partition(":")
            if k in MEM_KINDS:
                self.mem_nodes[x].append(i)
        self.slug_list = sorted(self.mem_nodes)
        self.slug_ix = {s: k for k, s in enumerate(self.slug_list)}
        mn, ms = [], []
        for k, s in enumerate(self.slug_list):
            for i in self.mem_nodes[s]:
                mn.append(i)
                ms.append(k)
        self._mn, self._ms = np.array(mn, np.int64), np.array(ms, np.int64)

    def memory_matrix(self, R, gamma=0.5):
        """slugs x B: each slug's best lift-scaled mass over its nodes, for every column of a PPR result."""
        lift = R[self._mn].astype(np.float64) / np.power(self.p0[self._mn] + 1e-12, gamma).reshape(-1, 1)
        out = np.zeros((len(self.slug_list), R.shape[1]))
        np.maximum.at(out, self._ms, lift)
        return out

    def ranked_memories(self, R, gamma=0.5, allowed=None, depth=200, with_scores=False):
        """Per column, the top `depth` memory slugs by score (only slugs in `allowed`, when given, and score > 0);
        with_scores gives (slug, score) pairs."""
        M = self.memory_matrix(R, gamma)
        if allowed is not None:
            mask = np.array([s in allowed for s in self.slug_list])
            M[~mask] = 0.0
        out = []
        for b in range(M.shape[1]):
            col = M[:, b]
            k = min(depth, int((col > 0).sum()))
            if k == 0:
                out.append([])
                continue
            # best first, equal scores in name order (slug_list is sorted), and the cut at depth made the same way.
            # Until 30 Sep argpartition and an unstable argsort put tied memories in an order of their own (three exact
            # ties came out c, a, b) and chose which of a tie crossing the cut survived; 35 of 60 recent recall queries
            # had exact ties, none across the cut (1000-page report D04-T05)
            pos = np.flatnonzero(col > 0)
            top = pos[np.lexsort((pos, -col[pos]))][:k]
            out.append([(self.slug_list[i], float(col[i])) for i in top] if with_scores
                       else [self.slug_list[i] for i in top])
        return out

    def ppr(self, S, alpha=None, iters=None):
        """Personalised PageRank for a batch of seed columns S (N x B, any non-negative weights)."""
        alpha = self.alpha if alpha is None else alpha
        iters = self.iters if iters is None else iters
        S = np.asarray(S, dtype=self.dtype)
        col = S.sum(axis=0, keepdims=True)
        S = S / np.where(col > 0, col, 1)
        r = S.copy()
        for _ in range(iters):
            dang = r[self.dangling].sum(axis=0, keepdims=True)
            r = alpha * S + (1 - alpha) * (self.PT @ r + S * dang)
        return r

    def seed_words(self, text, extra=()):
        """Seed weights from a text: each non-stop word's word nodes, rarer words heavier; plus any node names."""
        w = collections.Counter()
        for t in WORD.findall(text.lower()):
            if t in STOP:
                continue
            for k in WORD_KINDS:
                i = self.id.get(k + ":" + t)
                if i is not None and not self.dangling[i]:
                    w[i] += 1.0 / math.log(math.e + self.out_sum[i])
        for n in extra:
            i = self.id.get(n)
            if i is not None and not self.dangling[i]:
                w[i] += 1.0 / math.log(math.e + self.out_sum[i])
        return w

    def memory_scores(self, R, gamma=0.5, allowed=None):
        """For each column of a PPR result, {slug: score} over memories (max over a slug's nodes)."""
        lift = R / np.power(self.p0 + 1e-12, gamma).reshape(-1, 1)
        out = []
        slugs = [s for s in self.mem_nodes if allowed is None or s in allowed]
        idx = [self.mem_nodes[s] for s in slugs]
        for b in range(R.shape[1]):
            colv = lift[:, b]
            sc = {}
            for s, ii in zip(slugs, idx):
                v = max(float(colv[i]) for i in ii)
                if v > 0:
                    sc[s] = v
            out.append(sc)
        return out


def blend_scores(lex_scored, graph_scored, lam, depth=200):
    """Convex blend of min-max normalised scores, the way brain.py fuses its query-semantic lane: a memory absent
    from a list scores 0 there. lex_scored / graph_scored: [(slug, score)] best first."""
    if lam <= 0:
        return [s for s, _ in lex_scored[:depth]]

    def norm(lst):
        lst = lst[:depth]
        if not lst:
            return {}
        hi, lo = lst[0][1], lst[-1][1]
        span = (hi - lo) or 1.0
        return {s: (v - lo) / span for s, v in lst}
    a, b = norm(lex_scored), norm(graph_scored)
    f = {s: (1 - lam) * a.get(s, 0.0) + lam * b.get(s, 0.0) for s in set(a) | set(b)}
    return sorted(f, key=lambda s: -f[s])


def blend(lex_ranked, graph_ranked, lam, k=60, depth=200):
    """Weighted reciprocal-rank fusion. lam 0 returns the lexical order unchanged (to `depth`)."""
    if lam <= 0:
        return list(lex_ranked[:depth])
    s = collections.defaultdict(float)
    for r, m in enumerate(lex_ranked[:depth]):
        s[m] += (1 - lam) / (k + r + 1)
    for r, m in enumerate(graph_ranked[:depth]):
        s[m] += lam / (k + r + 1)
    return sorted(s, key=lambda m: -s[m])
