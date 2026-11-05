"""Lever 10 of the resident recall server (5 Nov 2026): three exact numpy forms of the hottest loops of a served recall,
installed in brain_recall_server's process only. A one-shot brain.py never imports this file.

Each one gives the same answer as the code it stands in for, float for float and in the same order, and falls back to
that code on anything it does not cover or on any error:

1. bm25.score_postings - postings decoded with np.frombuffer, each contribution the same float64 expression in the
   same order, ((idf*f)*(K1+1))/(f+norm(d)), added in posting order with np.add.at (0.0+v is v, so each document sees
   the same IEEE additions); top-k by (-score, position). 306 of 306 real prompts equal, 98 -> 14 ms median; a
   reassociated product (negative control) drops it to 225 of 306.
2. embedding_artifact.load_verified_embeddings (with a listing passed, as brain.py's recall passes it) - the parsed
   sidecar is kept, keyed on the file's stat plus the authority's sha256 and size; the common row case (row_safe, stat
   equal to the build's) is one numpy compare; every other row goes through the original per-row path in the original
   order; the bias mean is reused when the active rows are the same. 21 of 21 equal field by field, 111 -> 37 ms; a
   listing one memory short is caught.
3. spread_recall._spread for brain.py's call shape (no mw, kw or overlay) - the edge graph laid out once as CSR under
   brain.py's own graph cache key (the identity of every edge file); each hop's edges flat in the original visit order,
   np.add.at for the sums, np.maximum.at / np.minimum.at for the strongest bridge with first-wins ties, dict insertion
   order rebuilt. 60 of 60 calls equal (act and parent as ordered item lists), 86 -> 27 ms; the same edges summed in
   reverse order (negative control) differ on 20 of 20.
Together in one served Brain: 40 of 40 recall outputs equal, median 1264 -> 851 ms. Harnesses and the record are in
~/brain/porting/spread_server (ab_np.py, ab_emb.py, ab_spread_np.py, ab_recall_e2e.py, NOTES.txt).

Switches: BRS_EXACT_NP=0 skips the install; BRS_NP_BM25=0, BRS_NP_EMB=0 and BRS_NP_SPREAD=0 skip one lever."""
import io
import math
import os
import sys
from collections import defaultdict

STATS = {"bm25": [0, 0], "emb": [0, 0], "spread": [0, 0]}   # [served, fell back]

# Each lever replaces one function with a numpy form proven equal to ONE version of it. The brain-framework copies
# of bm25.score_postings (heapq top-k) and load_verified_embeddings (lever 8 sidecar, BRAIN_EMB_LAZY, bodies=) are
# other versions, so a lever goes in only when the function's source hashes to a version it was checked against.
# sha256(inspect.getsource(f))[:16]; add a hash only after that version's A/B passes.
PROVEN = {
    "bm25": {"b54f7a2d2b4c450c",      # Mac lineage, 306/306 (5 Nov)
             "53202072716ee9a9"},     # brain-framework (heapq top-k), 306/306 on two seeds, control 75/306,
                                      # 60.6 -> 12.1 ms (ab_bm25_lineage.py, 5 Nov 10:20 on the Mac's postings)
    "emb": {"467b923b88d89822",       # Mac light-loader lineage, 21/21 (5 Nov)
            "012f6f954ea04578"},      # brain-framework (lever-8 sidecar): 11/11 equal on every attribute, control
                                      # caught, 94 -> 25 ms (ab_emb_lineage.py, 5 Nov); BRAIN_EMB_LAZY=1 falls back
    "spread": {"fa0f3a9249918eb6"},   # same source on the Mac and in brain-framework, 60/60 (5 Nov)
}


def _proven(name, f):
    try:
        import hashlib, inspect
        f = getattr(f, "__wrapped__", f)
        return hashlib.sha256(inspect.getsource(f).encode()).hexdigest()[:16] in PROVEN[name]
    except Exception:
        return False


# ---- 1. bm25 ------------------------------------------------------------------------------------------------------
def _bm25_np(bm25, np, query, topk, conn):
    con, n, avgdl = conn
    acc = touched = None
    for t in sorted(set(bm25.tokens(query))):
        row = con.execute("select df,blob from p where term=?", (t,)).fetchone()
        if not row:
            continue
        df_t, blob = row
        idf = math.log(1.0 + (n - df_t + 0.5) / (df_t + 0.5)) * bm25.QUERY_TERM_WEIGHTS.get(t, 1.0)
        import zlib
        b = zlib.decompress(blob)
        if len(b) % 12:
            return None
        rec = np.frombuffer(b, dtype=np.dtype([("p", "<u4"), ("f", "<i4"), ("d", "<u4")]))
        f = rec["f"].astype(np.float64)
        d = rec["d"].astype(np.float64)
        norm = bm25.K1 * (1 - bm25.B + bm25.B * d / avgdl)
        v = ((idf * f) * (bm25.K1 + 1)) / (f + norm)
        p = rec["p"].astype(np.int64)
        if acc is None:
            size = max(int(p.max()) + 1 if len(p) else 1, int(n) + 1)
            acc = np.zeros(size, dtype=np.float64)
            touched = np.zeros(size, dtype=bool)
        if len(p) and p.max() >= acc.shape[0]:
            grow = int(p.max()) + 1
            acc = np.concatenate([acc, np.zeros(grow - acc.shape[0])])
            touched = np.concatenate([touched, np.zeros(grow - touched.shape[0], dtype=bool)])
        np.add.at(acc, p, v)
        touched[p] = True
    if acc is None or not touched.any():
        return []
    pos = np.nonzero(touched & (acc > 0))[0]
    s = acc[pos]
    order = np.lexsort((pos, -s))[:topk]
    out = []
    for i in order:
        r = con.execute("select slug from d where pos=?", (int(pos[i]),)).fetchone()
        if r:
            out.append((r[0], float(s[i])))
    return out


def _install_bm25(bm25, np):
    real = bm25.score_postings

    def score_postings(query, topk=20, index_path=bm25.INDEX, conn=None):
        if conn is None:
            STATS["bm25"][1] += 1
            return real(query, topk, index_path, conn=conn)
        try:
            out = _bm25_np(bm25, np, query, topk, conn)
            STATS["bm25"][0] += 1
            return out
        except Exception:
            STATS["bm25"][1] += 1
            return real(query, topk, index_path, conn=conn)
    score_postings.__wrapped__ = real
    bm25.score_postings = score_postings


# ---- 2. embeddings loader -----------------------------------------------------------------------------------------
_RES = {}


def _parsed(ea, np, actual, expected_sha, expected_size):
    st = os.stat(actual)
    key = (os.path.abspath(actual), expected_sha, expected_size, st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)
    hit = _RES.get("sidecar")
    if hit is not None and hit[0] == key:
        return hit[1]
    blob, sha, size, _ = ea.read_known_bytes(actual, expected_sha)
    if sha != expected_sha or size != expected_size:
        return None
    with np.load(io.BytesIO(blob), allow_pickle=False) as z:
        req = {"slugs", "vecs", "model", "built_at", "schema_version", "text_sha256", "source_size",
               "source_mtime_ns", "row_safe"}
        if req - set(z.files):
            return None
        sa, ha = z["slugs"], z["text_sha256"]
        slugs = sa.tolist() if sa.dtype.kind == "U" else [str(s) for s in sa]
        p = dict(slugs=slugs, vecs=np.asarray(z["vecs"]), model=str(z["model"].item()),
                 built_at=str(z["built_at"].item()), schema_version=int(z["schema_version"].item()),
                 text_hashes=ha.tolist() if ha.dtype.kind == "U" else [str(h) for h in ha],
                 source_size=np.asarray(z["source_size"]), source_mtime=np.asarray(z["source_mtime_ns"]),
                 row_safe=np.asarray(z["row_safe"]))
    p["names"] = [s + ".md" for s in p["slugs"]]
    p["hashes_by_slug"] = dict(zip(p["slugs"], p["text_hashes"]))
    p["probe_index"] = {s: i for i, s in enumerate(p["slugs"])}
    p["finite"] = bool(np.isfinite(p["vecs"]).all())
    p["unique"] = len(p["slugs"]) == len(set(p["slugs"]))
    for k in ("vecs", "source_size", "source_mtime", "row_safe"):
        p[k].setflags(write=False)
    now = os.stat(actual)
    if (now.st_ino, now.st_size, now.st_mtime_ns) == (st.st_ino, size, st.st_mtime_ns):
        _RES.clear()
        _RES["sidecar"] = (key, p)
    return p


def _load_fast(ea, np, sidecar, authority_path, mem_dir, expected_model, memory_entries):
    actual, status = ea.resolve_authority_sidecar(sidecar, authority_path)
    sha, size = status.get("sidecar_sha256"), status.get("sidecar_size")
    if not isinstance(sha, str) or not ea._SHA256_RE.fullmatch(sha):
        return None
    if isinstance(size, bool) or not isinstance(size, int) or size < 1:
        return None
    ea._utc(status.get("committed_at"))
    if expected_model and status.get("model") != expected_model:
        return None
    p = _parsed(ea, np, actual, sha, size)
    if p is None:
        return None
    slugs, vecs, th = p["slugs"], p["vecs"], p["text_hashes"]
    if (p["schema_version"] != ea.RUNTIME_SCHEMA_VERSION or status.get("model") != p["model"]
            or status.get("built_at") != p["built_at"]):
        return None
    if (vecs.dtype != np.float32 or vecs.ndim != 2 or vecs.shape[0] != len(slugs) or vecs.shape[1] < 1
            or len(th) != len(slugs) or not p["unique"] or not p["finite"]):
        return None
    probe = ea.VerifiedEmbeddings(p["probe_index"], vecs, 0.0, p["hashes_by_slug"])
    memory_stats = {e.name: e.stat() for e in memory_entries}
    sts = [memory_stats.get(n) for n in p["names"]]
    sz = np.fromiter((s.st_size if s is not None else -1 for s in sts), dtype=np.int64, count=len(sts))
    mt = np.fromiter((s.st_mtime_ns if s is not None else -1 for s in sts), dtype=np.int64, count=len(sts))
    fast = p["row_safe"] & (sz == p["source_size"]) & (mt == p["source_mtime"])
    ok = fast.copy()
    vcache, vnew = ea._read_verify_cache(mem_dir, sha), 0
    for i in np.nonzero(~fast)[0].tolist():
        st, slug = sts[i], slugs[i]
        if st is None:
            continue
        known = ea._verify_cache_answer(vcache, slug, st.st_size, st.st_mtime_ns, th[i])
        if known:
            ok[i] = True
        elif known is None:
            good = ea.embedding_row_current(probe, mem_dir, slug)
            if good:
                ok[i] = True
            try:
                now = os.stat(os.path.join(mem_dir, slug + ".md"))
                if (now.st_size, now.st_mtime_ns) == (st.st_size, st.st_mtime_ns):
                    vcache[slug] = [st.st_size, st.st_mtime_ns, th[i], bool(good), now.st_ctime_ns]
                    vnew += 1
            except OSError:
                pass
    if vnew:
        ea._write_verify_cache(mem_dir, sha, vcache)
    last = p.get("last")
    if last is not None and np.array_equal(last[0], ok):
        active, bias = dict(last[1]), last[2]
    else:
        idx = np.nonzero(ok)[0].tolist()
        active = dict(zip([slugs[i] for i in idx], idx))
        bias = 0.0
        if active:
            mean = vecs[list(active.values())].mean(axis=0)
            v = float(mean @ mean)
            bias = v if math.isfinite(v) and 0.0 <= v < 0.95 else 0.0
        p["last"] = (ok, dict(active), bias)
    loaded = ea.VerifiedEmbeddings(active, vecs, bias, p["hashes_by_slug"])
    loaded.row_of, loaded.row_safe = probe.index, p["row_safe"]
    loaded.source_size, loaded.source_mtime = p["source_size"], p["source_mtime"]
    loaded.verify_cache = vcache
    loaded.verify_key, loaded.built_ns = (mem_dir, sha), ea._built_ns(p["built_at"])
    return loaded


def _install_emb(ea, np):
    real = ea.load_verified_embeddings

    def load_verified_embeddings(sidecar, authority_path, mem_dir=None, expected_model=None,
                                 verify_all_rows=False, memory_entries=None, **kw):
        # BRAIN_EMB_LAZY=1 is the framework loader's other answer (rows judged later by the caller): not this lever's
        if kw or verify_all_rows or memory_entries is None or not mem_dir or os.environ.get("BRAIN_EMB_LAZY") == "1":
            STATS["emb"][1] += 1
            return real(sidecar, authority_path, mem_dir, expected_model, verify_all_rows=verify_all_rows,
                        memory_entries=memory_entries, **kw)
        try:
            out = _load_fast(ea, np, sidecar, authority_path, mem_dir, expected_model, memory_entries)
            STATS["emb"][0] += 1
            return out
        except Exception:
            STATS["emb"][1] += 1
            return real(sidecar, authority_path, mem_dir, expected_model, memory_entries=memory_entries)
    load_verified_embeddings.__wrapped__ = real
    ea.load_verified_embeddings = load_verified_embeddings


# ---- 3. spread ----------------------------------------------------------------------------------------------------
_CSR = {}


def _csr(np, adj, deg, key_fn):
    key = key_fn()
    hit = _CSR.get("g")
    if hit is not None and hit[0] == key and hit[1]["nsrc"] == len(adj):
        return hit[1]
    names, ids = [], {}
    srcs = list(adj.keys())
    for s in srcs:
        ids[s] = len(names)
        names.append(s)
    indptr = np.zeros(len(srcs) + 1, dtype=np.int64)
    nb_l, w_l = [], []
    for k, s in enumerate(srcs):
        for nb, w in adj.get(s, ()):
            i = ids.get(nb)
            if i is None:
                i = ids[nb] = len(names)
                names.append(nb)
            nb_l.append(i)
            w_l.append(w)
        indptr[k + 1] = len(nb_l)
    g = dict(names_arr=np.array(names, dtype=object), ids=ids, indptr=indptr, nb=np.asarray(nb_l, dtype=np.int64),
             w=np.asarray(w_l, dtype=np.float64), nsrc=len(srcs), lens=np.diff(indptr),
             deg=np.fromiter((deg.get(x, 0.0) for x in names), dtype=np.float64, count=len(names)))
    if key_fn() == key:          # keep only a layout read under the key it is filed by
        _CSR.clear()
        _CSR["g"] = (key, g)
    return g


def _spread_np(sr, np, seeds, adj, deg, key_fn):
    g = _csr(np, adj, deg, key_fn)
    ids, indptr, NB, W, nsrc = g["ids"], g["indptr"], g["nb"], g["w"], g["nsrc"]
    n0 = len(g["names_arr"])
    extra_names, seed_ids = [], []
    for s in seeds:
        i = ids.get(s)
        if i is None:
            if adj.get(s, ()):
                return None                   # a node with edges the layout does not have: not this generation
            i = n0 + len(extra_names)
            extra_names.append(s)
        seed_ids.append(i)
    N = n0 + len(extra_names)
    degall = g["deg"]
    if extra_names:
        degall = np.concatenate([degall, np.fromiter((deg.get(x, 0.0) for x in extra_names), dtype=np.float64)])
    lensall = np.concatenate([g["lens"], np.zeros(N - nsrc, dtype=np.int64)])
    startall = np.concatenate([indptr[:-1], np.zeros(N - nsrc, dtype=np.int64)])
    act = np.zeros(N, dtype=np.float64)
    seen = np.zeros(N, dtype=bool)
    sid = np.asarray(seed_ids, dtype=np.int64)
    sa = np.fromiter(seeds.values(), dtype=np.float64, count=len(seeds))
    np.add.at(act, sid, sa)
    orders = [sid]
    seen[sid] = True
    pflow = np.zeros(N, dtype=np.float64)
    psrc = np.full(N, -1, dtype=np.int64)
    pseen = np.zeros(N, dtype=bool)
    porders = []
    f_ids, f_a = sid, sa
    gain = 1.0 + sr.MW_BOOST * 0.0
    big = np.iinfo(np.int64).max
    for _hop in range(sr.HOPS):
        if not len(f_ids):
            break
        d = degall[f_ids] + 0.0
        if np.isnan(d).any() or np.isnan(f_a).any():
            return None
        m = ~(d <= 0) & (lensall[f_ids] > 0)
        fi, fa, fd = f_ids[m], f_a[m], d[m]
        if not len(fi):
            break
        lens_a = lensall[fi]
        tot = int(lens_a.sum())
        rep = np.repeat(np.arange(len(fi)), lens_a)
        off = np.arange(tot) - np.repeat(np.cumsum(lens_a) - lens_a, lens_a)
        e = startall[fi][rep] + off
        nb, w = NB[e], W[e]
        flow = ((fa[rep] * (w / fd[rep])) * sr.DECAY) * gain
        if np.isnan(flow).any():
            return None
        keep = ~(flow <= 0)
        nb, flow, src_e = nb[keep], flow[keep], fi[rep][keep]
        if not len(nb):
            break
        nxt = np.zeros(N, dtype=np.float64)
        np.add.at(nxt, nb, flow)
        np.add.at(act, nb, flow)
        pos = np.arange(len(nb), dtype=np.int64)
        firstpos = np.full(N, big, dtype=np.int64)
        np.minimum.at(firstpos, nb, pos)
        tn = np.nonzero(firstpos != big)[0]
        tf = firstpos[tn]
        touch = tn[np.argsort(tf)]
        orders.append(touch[~seen[touch]])
        seen[touch] = True
        hopmax = np.full(N, -np.inf)
        np.maximum.at(hopmax, nb, flow)
        eq = flow == hopmax[nb]
        argfirst = np.full(N, big, dtype=np.int64)
        np.minimum.at(argfirst, nb[eq], pos[eq])
        hf, hs = hopmax[tn], src_e[argfirst[tn]]
        win = hf > pflow[tn]
        newm = win & ~pseen[tn]
        if newm.any():
            porders.append(tn[newm][np.argsort(tf[newm])])
            pseen[tn[newm]] = True
        pflow[tn[win]] = hf[win]
        psrc[tn[win]] = hs[win]
        f_ids, f_a = touch, nxt[touch]
    na = g["names_arr"]
    if extra_names:
        na = np.concatenate([na, np.array(extra_names, dtype=object)])
    order = np.concatenate(orders)
    out = defaultdict(float)
    out.update(zip(na[order].tolist(), act[order].tolist()))
    par = {}
    if porders:
        po = np.concatenate(porders)
        par = dict(zip(na[po].tolist(), zip(na[psrc[po]].tolist(), pflow[po].tolist())))
    return out, par


def _install_spread(sr, np, g):
    real = sr._spread
    ident = g["_fc_ident"]
    paths = sr._edge_paths if hasattr(sr, "_edge_paths") else (lambda: [sr.EDGES])

    def key_fn():
        return tuple(ident(p) for p in paths()), ident(sr.__file__)

    def _spread(seeds, adj, deg, mw=None, kw=None, use_keywords=True, overlay=None):
        if mw or kw or overlay or not use_keywords or sr.HOPS < 1 or not seeds:
            STATS["spread"][1] += 1
            return real(seeds, adj, deg, mw, kw, use_keywords, overlay)
        try:
            out = _spread_np(sr, np, seeds, adj, deg, key_fn)
        except Exception:
            out = None
        if out is None:
            STATS["spread"][1] += 1
            return real(seeds, adj, deg, mw, kw, use_keywords, overlay)
        STATS["spread"][0] += 1
        return out
    _spread.__wrapped__ = real
    sr._spread = _spread


# ---- 4. status tag of an index line -------------------------------------------------------------------------------
# brain._status_from_index(slug, idx) is a regex over idx.get(slug, "") alone, called ~34k times a recall (31 ms).
# Memoized on the line text, so the answer is the function's own answer for that exact string.
def _install_status(g):
    real = g["_status_from_index"]
    memo = {}

    def _status_from_index(slug, idx):
        line = idx.get(slug, "")
        v = memo.get(line)
        if v is None:
            v = real(slug, {slug: line})
            if len(memo) > 200000:
                memo.clear()
            memo[line] = v
        return v
    _status_from_index.__wrapped__ = real
    g["_status_from_index"] = _status_from_index


def install(brain):
    """Called once by brain_recall_server.Brain after brain.py is loaded. Returns the names of the levers put in."""
    if os.environ.get("BRS_EXACT_NP", "1") == "0":
        return []
    import numpy as np
    done = []
    g = brain.g
    if os.environ.get("BRS_NP_BM25", "1") != "0":
        import bm25
        if (getattr(bm25, "score_postings", None) is not None and not hasattr(bm25.score_postings, "__wrapped__")
                and _proven("bm25", bm25.score_postings)):
            _install_bm25(bm25, np)
            done.append("bm25")
    if os.environ.get("BRS_NP_EMB", "1") != "0":
        import embedding_artifact as ea
        need = ("resolve_authority_sidecar", "read_known_bytes", "_read_verify_cache", "_verify_cache_answer",
                "_write_verify_cache", "embedding_row_current", "_built_ns", "_utc", "_SHA256_RE",
                "RUNTIME_SCHEMA_VERSION", "VerifiedEmbeddings")
        if (all(hasattr(ea, n) for n in need) and not hasattr(ea.load_verified_embeddings, "__wrapped__")
                and _proven("emb", ea.load_verified_embeddings)):
            _install_emb(ea, np)
            done.append("emb")
    if os.environ.get("BRS_NP_STATUS", "1") != "0":
        f = g.get("_status_from_index")
        try:   # only the version read and checked on 5 Nov (it reads the index line and nothing else)
            import hashlib, inspect
            src_ok = hashlib.sha256(inspect.getsource(f).encode()).hexdigest()[:16] == "281001d65d05ba2f"
        except Exception:
            src_ok = False
        if f is not None and src_ok and not hasattr(f, "__wrapped__"):
            _install_status(g)
            done.append("status")
    if os.environ.get("BRS_NP_SPREAD", "1") != "0":
        import spread_recall as sr
        if ("_fc_ident" in g and hasattr(sr, "_spread") and not hasattr(sr._spread, "__wrapped__")
                and _proven("spread", sr._spread)):
            _install_spread(sr, np, g)
            done.append("spread")
            # Servers live a median of 2 calls (38 on 5 Nov), so lay the graph out now, while the new server waits
            # for its first call, instead of in that call (~0.3 s). Same layout either way: any loader of this edge
            # generation gives the same keys and lists. BRS_NP_PREBUILD=0 leaves it to the first call.
            if os.environ.get("BRS_NP_PREBUILD", "1") != "0":
                import threading
                ident = g["_fc_ident"]
                paths = sr._edge_paths if hasattr(sr, "_edge_paths") else (lambda: [sr.EDGES])

                def prebuild():
                    try:
                        adj, deg = sr._load_graph()[:2]
                        if adj:
                            _csr(np, adj, deg, lambda: (tuple(ident(p) for p in paths()), ident(sr.__file__)))
                    except Exception:
                        pass
                threading.Thread(target=prebuild, name="recall-levers-prebuild", daemon=True).start()
                done.append("prebuild")
    return done
