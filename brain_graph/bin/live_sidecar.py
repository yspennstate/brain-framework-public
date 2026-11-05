"""live_sidecar.py - the brain graph made fast enough to read on every prompt and tool call (26 Sep, claude-825b1305).

His voice note of 26 Sep 00:26: "The brain is a network in the background, which is subconscious. It's not context. It's a
subconscious graph that is feeding in information to the conscious, to the context window." The graph store
(graph_store.py) holds the learned edges as arrays that take seconds to load; a hook has milliseconds. This builds
live/sidecar.sqlite from the store, the memories' own links and the attractors, so the membrane hook
(live_membrane.py) can answer "what does this moment light up" with a few indexed reads:

  node       every node of the store: kind, in-weight, out-sum and its use history (node size = ACT-R base level)
  edge       for each context item, its strongest targets per kind (memories, errors, folders, projects, tools, files)
             with PMI and count; hebb(x) = sum over the moment's items of weight x PMI(item -> x) x ln(1 + count);
             and when the edge was first and last seen (27 Sep, from the store's p_first / p_last)
  link       memory <-> memory: [[links]], superseded_by, related, and the typed links of the E1 cloud lane
  content    a node that a memory's text names (file, folder, host, dial, service, store) -> that memory, IDF-weighted,
             so a memory nobody has read yet is reachable from what a session touches
  memory     slug, description, status, superseded_by, mtime: what a served line prints and what it must skip
  attractor  co-activation communities (Louvain on positive PMI, as attractors.py) and what each led to at more than
             twice its base rate: the errors, memories and tools that followed its members
Built to sidecar.sqlite.new and swapped in; learned.sqlite (the learner's reinforcement) is separate and survives.

    python live_sidecar.py build [--store DIR]      # after each store build (run_graph.py build calls it)
    python live_sidecar.py memories                 # hourly: memory, link and content tables only (fresh memories)
    python live_sidecar.py stats
"""
import argparse
import collections
import json
import math
import os
import re
import sqlite3
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
HOME = os.path.join(os.path.expanduser("~"), ".claude", "brain_graph")
STORE = os.path.join(HOME, "store")
LIVE = os.path.join(HOME, "live")
SIDECAR = os.path.join(LIVE, "sidecar.sqlite")
MEM = os.path.join(os.path.expanduser("~"), ".claude", "projects", (os.environ.get("BRAIN_SLUG") or os.path.expanduser("~").replace(":", "-").replace(chr(92), "-").replace("/", "-")), "memory")
E1_LINKS = os.path.join(os.path.expanduser("~"), "ai-memories-and-functionality", "12_cognitive_architecture",
                        "brain_improvement", "cloud_credit_2026-09-26", "results", "memory_links_e1_partial.jsonl")
VERSION = 1

# targets kept per context item and kind: memories most, because memories are what reach the context
KIND_CAP = {"mem": 80, "err": 25, "dir": 25, "proj": 15, "tool": 15, "file": 15, "repo": 5, "host": 5, "verdict": 3}
LINK_W = {"named": 0.7, "superseded_by": 1.0, "supersedes": 1.0, "same_incident": 1.0, "cause_of": 0.8,
          "caused_by": 0.8, "fixes": 0.8, "fixed_by": 0.8, "instance_of": 0.6, "generalizes": 0.6, "depends_on": 0.6,
          "contradicts": 0.5, "same_subject": 0.4}
LINK = re.compile(r"\[\[([A-Za-z0-9_.\-]+)\]\]")
NAMES = re.compile(r"^\s*(superseded_by|supersedes|related|see_also)\s*:\s*(.+)$", re.I | re.M)
SKIP = {"MEMORY", "INDEX_ARCHIVE", "PREDICTIONS", "STATE_OF_MIND", "OPEN_QUESTIONS", "AUTOBIOGRAPHY", "MODEL_OF_PARTNER",
        "FASCINATIONS", "THE_OTHER_MINDS", "CONSCIENCE", "MISTAKES", "CURIOSITY", "RECONSOLIDATION_QUEUE", "INTENTIONS"}
DESC_RX = re.compile(r"^description:\s*(.+)$", re.M)
FIELD_RX = re.compile(r"^\s*(status|superseded_by|type)\s*:\s*(.+)$", re.M)
BARE_FILE = re.compile(r"(?<![\w/\\.-])([A-Za-z0-9_][A-Za-z0-9_.-]{2,80}\.(?:py|ps1|sh|js|ts|vbs|bat|cmd|lean|tex|md|json|"
                       r"jsonl|csv|tsv|yaml|yml|toml|txt|sqlite3?|db|html|mp4|wav|png))\b")
DIAL_BARE = re.compile(r"\b([A-Z][A-Z0-9]*_[A-Z0-9_]{2,})\b")


def _whole_successor(value):
    """The successor a superseded_by value names when it replaces the WHOLE memory, else ''. One classification for every
    reader (~/.claude/dreaming/supersession.pointer_scope, 4 Oct 2026): a pointer its author narrowed - "slug # only its
    third section", "slug (the half-real part only ...; the count rule stands)" - is a part supersession, so the memory
    row records no successor and the membrane never says the memory was replaced. Cutting the first token here had made
    the live membrane announce a partly superseded rule as replaced. Falls back to the first token if the module is
    missing."""
    try:
        dreaming = os.path.join(os.path.expanduser("~"), ".claude", "dreaming")
        if dreaming not in sys.path:
            sys.path.append(dreaming)
        from supersession import pointer_scope
        kind, slugs, _qualifier = pointer_scope(value)
        return slugs[0].lower() if kind == "whole" and slugs else ""
    except Exception:
        return value.split()[0].strip("[]'\",").lower() if value.split() else ""


def log(msg):
    os.makedirs(os.path.join(HOME, "logs"), exist_ok=True)
    with open(os.path.join(HOME, "logs", "live.log"), "a", encoding="utf-8") as f:
        f.write("%s sidecar: %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))


def schema(db):
    db.executescript("""
    PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;
    CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
    CREATE TABLE IF NOT EXISTS node(id INTEGER PRIMARY KEY, name TEXT, kind TEXT, in_w REAL, out_sum REAL, uses TEXT);
    CREATE TABLE IF NOT EXISTS edge(src INTEGER, dst INTEGER, k INTEGER, pmi REAL, cnt REAL, s REAL, t_first REAL,
                                    t_last REAL);
    CREATE TABLE IF NOT EXISTS link(src TEXT, dst TEXT, etype TEXT, w REAL);
    CREATE TABLE IF NOT EXISTS content(node TEXT, slug TEXT, w REAL);
    CREATE TABLE IF NOT EXISTS memory(slug TEXT PRIMARY KEY, descr TEXT, status TEXT, superseded_by TEXT, mtype TEXT,
                                      mtime REAL);
    CREATE TABLE IF NOT EXISTS attractor(id INTEGER PRIMARY KEY, size INTEGER, cohesion REAL, members TEXT, leads TEXT,
                                         mistakes TEXT);
    CREATE TABLE IF NOT EXISTS member(node INTEGER, attractor INTEGER);
    """)


def build_edges(db, st):
    kinds = sorted(KIND_CAP)
    kind_ix = {k: i for i, k in enumerate(kinds)}
    kind_of = np.array([kind_ix.get(n.split(":", 1)[0], -1) for n in st.names], dtype=np.int64)
    cap_of = np.array([KIND_CAP[k] for k in kinds], dtype=np.int64)
    src = (st.keys >> 32).astype(np.int64)
    dst = (st.keys & 0xFFFFFFFF).astype(np.int64)
    cnt = st.cnt.astype(np.float64)
    pos_ix = np.arange(len(st.keys))
    kd = kind_of[dst]
    ok = (kd >= 0) & (st.out_sum[src] > 0) & (st.in_w[dst] > 0)
    src, dst, cnt, kd, pos_ix = src[ok], dst[ok], cnt[ok], kd[ok], pos_ix[ok]
    pmi = np.log((cnt / st.out_sum[src]) / (st.in_w[dst] / st.total_w))
    pos = pmi > 0
    src, dst, cnt, kd, pmi, pos_ix = src[pos], dst[pos], cnt[pos], kd[pos], pmi[pos], pos_ix[pos]
    s = pmi * np.log1p(cnt)
    order = np.lexsort((-s, kd, src))
    g = src[order] * 16 + kd[order]
    starts = np.r_[True, g[1:] != g[:-1]]
    first = np.maximum.accumulate(np.where(starts, np.arange(len(g)), 0))
    rank = np.arange(len(g)) - first
    sel = order[rank < cap_of[kd[order]]]
    tf, tl = st.times(pos_ix[sel]) if hasattr(st, "times") else (None, None)
    if tf is None:
        tf = tl = [None] * len(sel)
    else:
        tf, tl = np.round(tf, 1).tolist(), np.round(tl, 1).tolist()
    rows = zip(src[sel].tolist(), dst[sel].tolist(), kd[sel].tolist(), np.round(pmi[sel], 4).tolist(),
               cnt[sel].tolist(), np.round(s[sel], 4).tolist(), tf, tl)
    db.executemany("INSERT INTO edge VALUES (?,?,?,?,?,?,?,?)", rows)
    return int(len(sel)), int(len(order))


def replace_verdict_edges(db, st_main, st_recent):
    """His corrections read their edges from a store of the recent weeks alone (26 Sep, walk-forward: adding June-July
    and fading edges improves places and errors, but the warning before his corrections fell from 16.7% right to 4.3% at
    the 2% bar - four thousand older sessions move the hubs and weight totals those edges are measured against). Replace
    the main store's edges into verdict targets with the recent store's, mapped by node name."""
    kinds = sorted(KIND_CAP)
    kv = kinds.index("verdict")
    main_id = {n: i for i, n in enumerate(st_main.names)}
    src = (st_recent.keys >> 32).astype(np.int64)
    dst = (st_recent.keys & 0xFFFFFFFF).astype(np.int64)
    is_v = np.array([st_recent.names[d].startswith("verdict:") for d in dst.tolist()], dtype=bool) if len(dst) else \
        np.zeros(0, dtype=bool)
    src, dst, cnt = src[is_v], dst[is_v], st_recent.cnt[is_v].astype(np.float64)
    ok = (st_recent.out_sum[src] > 0) & (st_recent.in_w[dst] > 0)
    src, dst, cnt = src[ok], dst[ok], cnt[ok]
    pmi = np.log((cnt / st_recent.out_sum[src]) / (st_recent.in_w[dst] / st_recent.total_w))
    keep = pmi > 0
    rows = []
    for s_, d_, p_, c_ in zip(src[keep].tolist(), dst[keep].tolist(), pmi[keep].tolist(), cnt[keep].tolist()):
        a, b = main_id.get(st_recent.names[s_]), main_id.get(st_recent.names[d_])
        if a is not None and b is not None:
            rows.append((a, b, kv, round(p_, 4), c_, round(p_ * math.log1p(c_), 4), None, None))
    db.execute("DELETE FROM edge WHERE k=?", (kv,))
    db.executemany("INSERT INTO edge VALUES (?,?,?,?,?,?,?,?)", rows)
    return len(rows)


def build_nodes(db, st):
    rows = []
    for i, n in enumerate(st.names):
        u = st.uses.get(i)
        uj = json.dumps([u[0][-50:], u[1], u[2]]) if u else None
        rows.append((i, n, n.split(":", 1)[0], float(st.in_w[i]), float(st.out_sum[i]), uj))
    db.executemany("INSERT INTO node VALUES (?,?,?,?,?,?)", rows)
    return len(rows)


MEMPARSE_CACHE = os.path.join(LIVE, "memparse_cache.pkl")
CACHE_WRITE_ALWAYS = False      # tests only: write the cache on a partial pass too


def _code_key():
    """The parse depends on this file's patterns and extract_touch_events's; any edit to either starts a fresh cache."""
    import hashlib
    import extract_touch_events as X
    h = hashlib.sha256()
    for p in (os.path.abspath(__file__), os.path.abspath(X.__file__)):
        with open(p, "rb") as fh:
            h.update(fh.read())
    return h.hexdigest()[:16]


def _load_cache(key):
    import pickle
    try:
        with open(MEMPARSE_CACHE, "rb") as fh:
            c = pickle.load(fh)
        if c.get("key") == key:
            return c["files"]
    except Exception:
        pass
    return {}


def _save_cache(key, files):
    import pickle
    tmp = "%s.%d.tmp" % (MEMPARSE_CACHE, os.getpid())     # two writers (a build, a study) never share a temp file
    try:
        with open(tmp, "wb") as fh:
            pickle.dump({"key": key, "files": files}, fh, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(tmp, MEMPARSE_CACHE)
    except Exception as e:                     # a cache is never a precondition
        log("memparse cache not saved: %s: %s" % (type(e).__name__, e))


def _parse_file(text):
    """Everything memory_pass takes from one memory file that does not depend on the store or on the other memories:
    the frontmatter fields, the slugs it names, and its content candidates before they are matched against the store's
    nodes, dials and file basenames. Cached by (mtime, size) so a full build reads only the files that changed
    (1 Nov, claude-1e0a23c2: the full pass re-read ~40k files every build, 154 s quiet and 11,029 s on 31 Oct)."""
    import extract_touch_events as X
    head = text[:3000]
    m = DESC_RX.search(head)
    desc = (m.group(1).strip().strip('"') if m else "")[:300]
    fields = {k.lower(): v.strip().strip('"') for k, v in FIELD_RX.findall(head)}
    sup = fields.get("superseded_by", "")
    sup = _whole_successor(sup) if sup else ""
    refs = set(r.lower().removesuffix(".md") for r in LINK.findall(text))
    for mm in NAMES.finditer(head):
        refs.update(r.lower() for r in re.findall(r"[A-Za-z0-9_.\-]{8,}", mm.group(2)))
    rec = {"desc": desc, "status": fields.get("status", "")[:40], "sup": sup, "type": fields.get("type", "")[:20],
           "refs": sorted(refs), "fm": text.startswith("---")}
    if not rec["fm"]:
        return rec
    got = collections.Counter()
    for mt_ in X.PATH_RX.finditer(text):
        for kind, nid in X.path_nodes(mt_.group(0)):
            if kind != "mem":
                got["%s:%s" % (kind, nid)] += 1
    low = text.lower()
    for key, host in X.HOSTS.items():
        if re.search(r"(?<![\w.-])%s(?![\w-])" % re.escape(key.lower()), low):
            got["host:" + host] += 1
    dialm = collections.Counter(dm.group(1) for dm in DIAL_BARE.finditer(text))
    for rx, pre in ((X.GS_RX, "store:gs://"), (X.DB_RX, "store:"), (X.SRV_RX, "store:")):
        for sm in rx.finditer(text):
            got[pre + (sm.group(1).lower() if pre != "store:" or rx is X.DB_RX else sm.group(1))] += 1
    for keys, rx, name in X.SVC:
        if any(k in low for k in keys) and rx.search(text):
            got["svc:" + name] += 1
    rec["got"] = dict(got)
    rec["dial"] = dict(dialm)
    rec["bare"] = [bm.group(1).lower() for bm in BARE_FILE.finditer(text)]
    return rec


def memory_pass(db, node_names, max_mtime=None, only=None, all_slugs=None):
    """memory, link and content tables from the memory files themselves. node_names: every node name of the store, so
    content edges are kept only toward nodes a session can actually light. max_mtime (a walk-forward study's cut) leaves
    out memories last written after it. only: read just these file names (the incremental hourly pass), resolving
    their links against all_slugs; the typed E1 links are loaded on a full pass only. A file whose mtime and size match
    the parse cache is not read again (_parse_file)."""
    in_store = set(node_names)
    basenames = collections.defaultdict(list)
    dials = set()
    for n in node_names:
        if n.startswith("file:"):
            basenames[n.rsplit("/", 1)[-1]].append(n)
        elif n.startswith("dial:"):
            dials.add(n[5:])
    stats = {}
    if only is not None:
        files = list(only)
    else:
        files = []
        with os.scandir(MEM) as it:
            for de in it:
                if de.name.endswith(".md") and de.name[:-3] not in SKIP:
                    files.append(de.name)
                    try:
                        st = de.stat()
                        stats[de.name] = (st.st_mtime, st.st_mtime_ns, st.st_size)
                    except OSError:
                        pass
    key = _code_key()
    cache = _load_cache(key)
    n_read = 0
    lower = {s: s for s in all_slugs} if all_slugs else {f[:-3].lower(): f[:-3].lower() for f in files}
    mem_rows, links, content = [], [], collections.defaultdict(collections.Counter)
    for f in files:
        slug = f[:-3].lower()
        p = os.path.join(MEM, f)
        try:
            if f in stats:
                mt, mt_ns, size = stats[f]
            else:
                st = os.stat(p)
                mt, mt_ns, size = st.st_mtime, st.st_mtime_ns, st.st_size
            if max_mtime and mt > max_mtime:
                continue
            c = cache.get(f)
            if c is not None and c[0] == mt_ns and c[1] == size:
                rec = c[2]
            else:
                text = open(p, encoding="utf-8", errors="replace").read()
                rec = _parse_file(text)
                cache[f] = (mt_ns, size, rec)
                n_read += 1
        except OSError:
            continue
        sup = rec["sup"]
        mem_rows.append((slug, rec["desc"], rec["status"], sup, rec["type"], mt))
        for r in rec["refs"]:
            d = lower.get(r)
            if d and d != slug:
                links.append((slug, d, "named", LINK_W["named"]))
        if sup and sup in lower and sup != slug:
            links.append((slug, sup, "superseded_by", 1.0))
            links.append((sup, slug, "supersedes", 1.0))
        if not rec["fm"]:
            continue
        got = collections.Counter(rec["got"])
        for d_, k in rec["dial"].items():
            if d_ in dials:
                got["dial:" + d_] += k
        named = {n.rsplit("/", 1)[-1] for n in got if n.startswith("file:")}
        for b in rec["bare"]:
            hits = basenames.get(b, ())
            if b not in named and 0 < len(hits) <= 3:
                for h in hits:
                    got[h] += 1
        for n, c_ in got.items():
            if n in in_store:
                content[n][slug] += min(c_, 3)
    n_gone = 0
    if only is None and max_mtime is None:
        keep = set(files)
        n_gone = sum(1 for f in cache if f not in keep)
        if n_gone:
            cache = {f: v for f, v in cache.items() if f in keep}
    if (n_read or n_gone) and ((only is None and max_mtime is None) or CACHE_WRITE_ALWAYS):
        _save_cache(key, cache)
    memory_pass.last_read = n_read
    if only is None and os.path.exists(E1_LINKS):
        for line in open(E1_LINKS, encoding="utf-8"):
            try:
                e = json.loads(line)
            except ValueError:
                continue
            t = e.get("type")
            s, d = str(e.get("src", "")).lower(), str(e.get("dst", "")).lower()
            if t in LINK_W and s in lower and d in lower and s != d:
                links.append((s, d, t, LINK_W[t]))
    n_mem = max(1, len(mem_rows))
    crow = []
    for n, per in content.items():
        df = len(per)
        if df > 0.02 * n_mem:           # a node most memories name (a hub folder) links to nothing in particular
            continue
        idf = math.log(n_mem / df)
        for slug, c_ in per.items():
            crow.append((n, slug, round(idf * (1.0 + math.log(c_)), 4)))
    db.execute("DELETE FROM memory")
    db.execute("DELETE FROM link")
    db.execute("DELETE FROM content")
    db.executemany("INSERT OR REPLACE INTO memory VALUES (?,?,?,?,?,?)", mem_rows)
    db.executemany("INSERT INTO link VALUES (?,?,?,?)", links)
    db.executemany("INSERT INTO content VALUES (?,?,?)", crow)
    return len(mem_rows), len(links), len(crow)


def build_attractors(db, st, min_count=5.0, min_size=3, seed=7):
    """Louvain communities on the positive-PMI co-activation graph and what each one led to (attractors.py's method)."""
    import networkx as nx
    from networkx.algorithms.community import louvain_communities
    names = st.names
    ck, cc = st.co_keys, st.co_cnt
    j = (ck >> 32).astype(np.int64)
    i = (ck & 0xFFFFFFFF).astype(np.int64)
    deg = np.bincount(j, weights=cc, minlength=len(names))
    tot = float(cc.sum()) or 1.0
    keep = (cc >= min_count) & (j < i)
    pmi = np.log((cc[keep] * tot) / (deg[j[keep]] * deg[i[keep]]))
    pos = pmi > 0
    G = nx.Graph()
    for u, v, w in zip(j[keep][pos].tolist(), i[keep][pos].tolist(), pmi[pos].tolist()):
        G.add_edge(u, v, weight=w)
    if G.number_of_edges() == 0:
        return 0
    comms = [c for c in louvain_communities(G, weight="weight", seed=seed, resolution=1.0) if len(c) >= min_size]
    rows, mem_rows = [], []
    for a_id, c in enumerate(comms):
        members = sorted(c, key=lambda x: -deg[x])
        agg = collections.Counter()
        mass = 0.0
        for m in members:
            xs, w = st.rows(m, cap=100000)
            mass += float(st.out_sum[m])
            for x, ww in zip(xs.tolist(), w.tolist()):
                if names[x].split(":", 1)[0] in ("err", "mem", "tool", "verdict") and x not in c:
                    agg[x] += ww
        leads = []
        if mass > 0:
            for x, ww in agg.items():
                if ww < min_count:
                    continue
                lift = (ww / mass) / (st.in_w[x] / st.total_w)
                if lift > 2.0:
                    leads.append((lift * math.log1p(ww), round(float(lift), 2), int(ww), names[x]))
        leads.sort(reverse=True)
        weight = sum(G[u][v]["weight"] for u in c for v in G[u] if v in c) / 2.0
        rows.append((a_id, len(c), round(weight / max(1, len(c)), 3), json.dumps([names[m] for m in members[:12]]),
                     json.dumps([[nm, lf, n] for _s, lf, n, nm in leads[:12]]),
                     json.dumps([[nm, lf, n] for _s, lf, n, nm in leads if nm.startswith(("err:", "verdict:correction"))][:6])))
        mem_rows += [(int(m), a_id) for m in c]
    db.executemany("INSERT INTO attractor VALUES (?,?,?,?,?,?)", rows)
    db.executemany("INSERT INTO member VALUES (?,?)", mem_rows)
    return len(rows)


PUBLISH_TRIES, PUBLISH_WAIT = 60, 0.25


def publish(new, out, tries=None, wait=None):
    """Replace out with new, retrying while Windows refuses: SQLite opens its files without FILE_SHARE_DELETE, so a hook
    reading the sidecar at that moment makes the rename fail (29 Sep, account02/B11 r83). Returns the seconds waited;
    raises the last PermissionError when readers held it for the whole budget (15 s), and the caller keeps the old
    sidecar and builds no delta over the new store until a later publish lands."""
    tries = PUBLISH_TRIES if tries is None else tries
    wait = PUBLISH_WAIT if wait is None else wait
    t0 = time.time()
    for i in range(tries):
        try:
            os.replace(new, out)
            return 0.0 if i == 0 else time.time() - t0
        except PermissionError:
            if i == tries - 1:
                raise
            time.sleep(wait)


def build(store=STORE, out=SIDECAR, max_mtime=None, verdict_store=None):
    from graph_store import GraphStore
    t0 = time.time()
    os.makedirs(os.path.dirname(out), exist_ok=True)
    new = out + ".new"
    if os.path.exists(new):
        os.remove(new)
    st = GraphStore(store)
    db = sqlite3.connect(new)
    schema(db)
    n_nodes = build_nodes(db, st)
    n_edges, n_pairs = build_edges(db, st)
    n_verdict = None
    if verdict_store and os.path.exists(os.path.join(verdict_store, "edges.npz")):
        n_verdict = replace_verdict_edges(db, st, GraphStore(verdict_store))
    t1 = time.time()
    n_mem, n_link, n_content = memory_pass(db, st.names, max_mtime=max_mtime)
    t2 = time.time()
    try:
        n_attr = build_attractors(db, st)
    except Exception as e:                     # the attractors are a layer, not a precondition
        n_attr = -1
        log("attractors failed: %s: %s" % (type(e).__name__, e))
    db.executescript("""
    CREATE INDEX node_name ON node(name);
    CREATE INDEX edge_src ON edge(src);
    CREATE INDEX link_src ON link(src);
    CREATE INDEX content_node ON content(node);
    CREATE INDEX content_slug ON content(slug);
    CREATE INDEX link_dst ON link(dst);
    CREATE INDEX member_node ON member(node);
    """)
    meta = {"version": VERSION, "kinds": sorted(KIND_CAP), "built": time.time(), "store_until": st.meta.get("until"), "store_built": st.meta.get("built"),
            "total_w": st.total_w, "nodes": n_nodes, "edges": n_edges, "pairs_positive": n_pairs, "memories": n_mem,
            "links": n_link, "content": n_content, "attractors": n_attr, "store": store,
            "verdict_store": verdict_store, "verdict_edges": n_verdict}
    db.executemany("INSERT OR REPLACE INTO meta VALUES (?,?)", [(k, json.dumps(v)) for k, v in meta.items()])
    db.commit()
    db.close()
    waited = publish(new, out)
    if waited:
        log("publish waited %.1f s for readers of %s to close it" % (waited, os.path.basename(out)))
    log("built %s: %d nodes, %d edges (of %d positive pairs), %d memories, %d links, %d content edges, %d attractors; "
        "arrays %.0f s, memories %.0f s, total %.0f s" % (os.path.basename(out), n_nodes, n_edges, n_pairs, n_mem, n_link,
                                                          n_content, n_attr, t1 - t0, t2 - t1, time.time() - t0))
    return meta


def refresh_memories():
    """The hourly pass, incremental: only memory files written or changed since the sidecar last saw them are read
    (a directory listing carries the times, so the other 17,000 files cost nothing), and their rows in the memory, link
    and content tables are replaced. New memories become reachable by the hook before the next store build."""
    t0 = time.time()
    if not os.path.exists(SIDECAR):
        return None
    db = sqlite3.connect(SIDECAR, timeout=30)
    # the per-slug deletes below scan content and link whole without these (32 s -> 9 s for ~110 files, 1 Nov)
    db.execute("CREATE INDEX IF NOT EXISTS content_slug ON content(slug)")
    db.execute("CREATE INDEX IF NOT EXISTS link_dst ON link(dst)")
    # a links-only sidecar (memory_pass alone, no build) has no link_src either, and graph_lane's lazy walk reads
    # src IN (...) OR dst IN (...): without both indexes every batch scans the whole table (Mac, 5 Nov: 274 batches,
    # 2.9 s of a 3.2 s recall)
    db.execute("CREATE INDEX IF NOT EXISTS link_src ON link(src)")
    known = dict(db.execute("SELECT slug, mtime FROM memory"))
    changed = []
    with os.scandir(MEM) as it:
        for de in it:
            if not de.name.endswith(".md") or de.name[:-3] in SKIP:
                continue
            sl = de.name[:-3].lower()
            try:
                mt = de.stat().st_mtime
            except OSError:
                continue
            if sl not in known or mt > (known[sl] or 0) + 1:
                changed.append(de.name)
    if not changed:
        db.close()
        return (0, 0, 0)
    names = [r[0] for r in db.execute("SELECT name FROM node WHERE kind IN ('file','dial','host','svc','store','dir',"
                                      "'proj','repo')")]
    tmp = sqlite3.connect(":memory:")
    schema(tmp)
    n = memory_pass(tmp, names, only=changed, all_slugs=set(known) | {f[:-3].lower() for f in changed})
    slugs = [f[:-3].lower() for f in changed]
    n_mem = max(1, db.execute("SELECT COUNT(*) FROM memory").fetchone()[0])
    for sl in slugs:
        db.execute("DELETE FROM memory WHERE slug=?", (sl,))
        db.execute("DELETE FROM link WHERE src=? AND etype IN ('named','superseded_by')", (sl,))
        db.execute("DELETE FROM link WHERE dst=? AND etype='supersedes'", (sl,))
        db.execute("DELETE FROM content WHERE slug=?", (sl,))
    db.executemany("INSERT OR REPLACE INTO memory VALUES (?,?,?,?,?,?)", tmp.execute("SELECT * FROM memory").fetchall())
    db.executemany("INSERT INTO link VALUES (?,?,?,?)", tmp.execute("SELECT * FROM link").fetchall())
    crow = []
    for nd, sl, c in tmp.execute("SELECT node, slug, w FROM content"):
        df = db.execute("SELECT COUNT(*) FROM content WHERE node=?", (nd,)).fetchone()[0] + 1
        if df > 0.02 * n_mem:
            continue
        crow.append((nd, sl, round(math.log(n_mem / df) * (1.0 + math.log(max(1.0, c))), 4)))
    db.executemany("INSERT INTO content VALUES (?,?,?)", crow)
    db.execute("INSERT OR REPLACE INTO meta VALUES ('memories_refreshed', ?)", (json.dumps(time.time()),))
    db.commit()
    db.close()
    tmp.close()
    log("memories refreshed: %d changed files, %d links, %d content edges in %.0f s" % (
        len(changed), n[1], len(crow), time.time() - t0))
    return (len(changed), n[1], len(crow))


def stats():
    db = sqlite3.connect(SIDECAR)
    for k, v in db.execute("SELECT k, v FROM meta"):
        print("%-20s %s" % (k, v[:120]))
    for t in ("node", "edge", "link", "content", "memory", "attractor", "member"):
        print("%-10s %d rows" % (t, db.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=("build", "memories", "stats"))
    ap.add_argument("--store", default=STORE)
    ap.add_argument("--verdict-store", default=None, help="take the edges into his corrections from this store")
    a = ap.parse_args()
    if a.mode == "build":
        print(json.dumps(build(a.store, verdict_store=a.verdict_store), indent=1))
    elif a.mode == "memories":
        print(refresh_memories())
    else:
        stats()


if __name__ == "__main__":
    main()
