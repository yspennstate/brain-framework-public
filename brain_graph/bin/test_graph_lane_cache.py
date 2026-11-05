"""graph_lane's caches follow the generation of the file they read (30 Sep 2026; interactive audit B100-03).

    python test_graph_lane_cache.py [GRAPH_LANE.py] [--old OLD_GRAPH_LANE.py]

The link cache (_link_graph) had one key for the whole process and the walk cache (_graph) was keyed by the store's
path, so a process that switched the pinned sidecar, or outlived a rebuild, kept the first graph it read.

L1 switches the sidecar pin A -> B -> A in one process and requires every warm read to equal a cold read (a fresh copy
of the module) of the file pinned at that moment; L2 replaces the pinned file by rename at the same path, as the capture
publishes a rebuild; L3 rewrites it in place with the same size inside one tick of the file clock, as its memory refresh
does; L4 an unchanged file is served from the cache (the same object); L5 a missing file reads as an empty graph and is
read once it appears; L6 a replacement that never lands (the publish gave up) keeps the old generation; L7 two arms in
one interpreter, each ranking through link_ranked from its own sidecar; L8 at most four generations are held; L9 a file
that changes while it is read answers that call and is not cached; W1-W3 the walk cache follows the store's edges.npz
the same way. N1-N3 (with --old) run L1, L2 and W1 on the code before the change and show the stale graph.

1000-page report D05-T04 (30 Sep): W4 rewrites edges.npz in place at the same size and puts its write time back, and
needs the new walk (the code before read the old one); D1-D4 hold the walk's disk cache to the store it was built from:
used while the identity matches, refused for a store rolled back to other edges with an older time (the time rule used
it), a same-size npz with a changed member reads as new, and a cache written before identities keeps the time rule.
"""
import importlib.util
import os
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FAILS = []


def check(name, ok, detail=""):
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, (" - " + detail) if detail else ""))
    if not ok:
        FAILS.append(name)


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def sidecar(path, target, via_rename=True):
    """A sidecar whose one link runs seed -> target, in the live schema; written beside and renamed over by default."""
    tmp = path + ".new" if via_rename else path
    if os.path.exists(tmp):
        os.remove(tmp)
    db = sqlite3.connect(tmp)
    db.execute("CREATE TABLE link(src TEXT, dst TEXT, etype TEXT, w REAL)")
    db.execute("INSERT INTO link VALUES ('seed', ?, 'named', 1.0)", (target,))
    db.commit()
    db.close()
    if via_rename:
        os.replace(tmp, path)


def rewrite_in_place(path, old, new):
    db = sqlite3.connect(path)
    db.execute("UPDATE link SET dst=? WHERE dst=?", (new, old))
    db.commit()
    db.close()


def targets(graph):
    return sorted(b for b, _w in graph[0].get("seed", []))


N_COLD = [0]


def cold(module_path, pin):
    """What a fresh process reads with pin set: a new copy of the module, its caches empty."""
    N_COLD[0] += 1
    os.environ["BRAIN_GRAPH_SIDECAR"] = pin
    return load(module_path, "graph_lane_cold_%d" % N_COLD[0])._link_graph()


def warm(g, pin):
    os.environ["BRAIN_GRAPH_SIDECAR"] = pin
    return g._link_graph()


class FakeDiffusion:
    """Stands in for diffusion.Diffusion: the walk it returns is the content of the store's edges.npz."""
    calls = 0

    @classmethod
    def cached(cls, store, **_kw):
        cls.calls += 1
        with open(os.path.join(store, "edges.npz"), encoding="ascii") as fh:
            return {"walk": fh.read()}


def write_edges(store, text):
    tmp = os.path.join(store, "edges.npz.tmp")
    with open(tmp, "w", encoding="ascii") as fh:
        fh.write(text)
    os.replace(tmp, os.path.join(store, "edges.npz"))


def walk_cases(g, td, tag):
    """W1: a store rebuilt at its path gives the new walk; W2: unchanged, the same object; W3: at most two held."""
    import types
    fake = types.ModuleType("diffusion")
    fake.Diffusion = FakeDiffusion
    saved = sys.modules.get("diffusion")
    sys.modules["diffusion"] = fake
    try:
        store = os.path.join(td, "store_" + tag)
        os.makedirs(store)
        d = {"store": store, "co_w": 0.5, "cap": 300, "alpha": 0.3}
        write_edges(store, "v1")
        first = g._graph(d)
        write_edges(store, "v2")
        second = g._graph(d)
        again = g._graph(d)
        others = []
        for k in range(3):
            s = os.path.join(td, "store_%s_%d" % (tag, k))
            os.makedirs(s)
            write_edges(s, "o%d" % k)
            others.append(g._graph(dict(d, store=s)))
        return first, second, again, len(g._G)
    finally:
        if saved is None:
            sys.modules.pop("diffusion", None)
        else:
            sys.modules["diffusion"] = saved


def walk_rewrite_case(g, td):
    """W4 (1000-page report D05-T04): the edges file rewritten in place, same size, write time put back."""
    import types
    fake = types.ModuleType("diffusion")
    fake.Diffusion = FakeDiffusion
    saved = sys.modules.get("diffusion")
    sys.modules["diffusion"] = fake
    try:
        store = os.path.join(td, "store_inplace")
        os.makedirs(store)
        d = {"store": store, "co_w": 0.5, "cap": 300, "alpha": 0.3}
        write_edges(store, "v1")
        p = os.path.join(store, "edges.npz")
        st = os.stat(p)
        first = g._graph(d)
        with open(p, "r+", encoding="ascii") as fh:
            fh.write("v2")
        os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))
        return first, g._graph(d)
    finally:
        if saved is None:
            sys.modules.pop("diffusion", None)
        else:
            sys.modules["diffusion"] = saved


def diffusion_cases(diffusion_path, td):
    """D1-D4 (1000-page report D05-T04): the walk's disk cache is used only for the store it was built from."""
    import numpy as np
    dm = load(diffusion_path, "diffusion_under_test")
    store = os.path.join(td, "dstore")
    os.makedirs(store)
    edges, nodes = os.path.join(store, "edges.npz"), os.path.join(store, "nodes.json")
    np.savez(edges, a=np.arange(64, dtype=np.int64))
    with open(nodes, "w", encoding="utf-8") as fh:
        fh.write('{"names": ["m:a", "m:b"], "meta": {}}')
    cache = os.path.join(td, "walk_cache.npz")
    np.savez(cache, indptr=np.zeros(3), src_id=np.array(dm.source_identity(store)))
    check("D1 a cache built from the store as it is now is used", dm.Diffusion._cache_matches(cache, edges,
                                                                                              dm.source_identity(store)))
    st, before = os.stat(edges), dm.tail_digest(edges)
    np.savez(edges, a=np.arange(64, dtype=np.int64)[::-1])       # same size, other contents
    os.utime(edges, ns=(st.st_atime_ns, st.st_mtime_ns - 10 ** 9))  # rolled back: older than the cache
    check("D2 a store rolled back to other edges with an older time does not use the newer walk",
          not dm.Diffusion._cache_matches(cache, edges, dm.source_identity(store)))
    check("D3 a same-size npz with a changed member has a new identity",
          os.path.getsize(edges) == st.st_size and dm.tail_digest(edges) not in (None, before))
    legacy = os.path.join(td, "walk_cache_legacy.npz")
    np.savez(legacy, indptr=np.zeros(3))
    t = os.stat(edges).st_mtime_ns
    os.utime(legacy, ns=(t + 5 * 10 ** 9, t + 5 * 10 ** 9))
    newer = dm.Diffusion._cache_matches(legacy, edges, dm.source_identity(store))
    os.utime(legacy, ns=(t - 5 * 10 ** 9, t - 5 * 10 ** 9))
    older = dm.Diffusion._cache_matches(legacy, edges, dm.source_identity(store))
    check("D4 a cache written before identities keeps the time rule until it is rebuilt", newer and not older)


def main():
    args = [a for a in sys.argv[1:] if a != "--old"]
    old = None
    if "--old" in sys.argv:
        old = sys.argv[sys.argv.index("--old") + 1]
        args.remove(old)
    path = args[0] if args else os.path.join(os.path.expanduser("~"), ".claude", "dreaming", "graph_lane.py")
    pin_before = os.environ.get("BRAIN_GRAPH_SIDECAR")
    g = load(path, "graph_lane_under_test")
    with tempfile.TemporaryDirectory() as td:
        a, b = os.path.join(td, "a.sqlite"), os.path.join(td, "b.sqlite")
        sidecar(a, "alpha")
        sidecar(b, "beta")

        ra1 = warm(g, a)
        rb = warm(g, b)
        ra2 = warm(g, a)
        check("L1 pin A -> B -> A: each warm read equals the cold read of the pinned file",
              ra1 == cold(path, a) and rb == cold(path, b) and ra2 == cold(path, a)
              and targets(rb) == ["beta"] and targets(ra2) == ["alpha"],
              "A %s, B %s, A again %s" % (targets(ra1), targets(rb), targets(ra2)))
        check("L4 an unchanged file is served from the cache", warm(g, a) is ra2)

        p = os.path.join(td, "pinned.sqlite")
        sidecar(p, "alpha")
        before = warm(g, p)
        sidecar(p, "beta")
        after = warm(g, p)
        check("L2 a file replaced by rename at the same path is read again",
              targets(before) == ["alpha"] and after == cold(path, p) and targets(after) == ["beta"],
              "%s then %s" % (targets(before), targets(after)))

        q = os.path.join(td, "inplace.sqlite")
        sidecar(q, "alpha")
        first = warm(g, q)
        size = os.path.getsize(q)
        rewrite_in_place(q, "alpha", "gamma")
        second = warm(g, q)
        check("L3 a same-size rewrite in place is read again",
              os.path.getsize(q) == size and targets(first) == ["alpha"] and targets(second) == ["gamma"]
              and second == cold(path, q), "%s then %s" % (targets(first), targets(second)))

        m = os.path.join(td, "later.sqlite")
        empty = warm(g, m)
        sidecar(m, "delta")
        present = warm(g, m)
        check("L5 a missing file reads as empty and is read once it appears",
              empty == ({}, {}) and targets(present) == ["delta"] and present == cold(path, m),
              "%s then %s" % (targets(empty), targets(present)))

        f = os.path.join(td, "failed.sqlite")
        sidecar(f, "alpha")
        kept = warm(g, f)
        sidecar(f + ".staged", "beta", via_rename=False)  # the rebuild is written but never swapped in
        still = warm(g, f)
        check("L6 a replacement that never lands keeps the old generation",
              still is kept and targets(still) == ["alpha"] and still == cold(path, f))

        os.environ["BRAIN_GRAPH_SIDECAR"] = a
        arm_a = [s for s, _v in g.link_ranked([("seed", 1.0)])]
        os.environ["BRAIN_GRAPH_SIDECAR"] = b
        arm_b = [s for s, _v in g.link_ranked([("seed", 1.0)])]
        check("L7 two arms in one interpreter rank from their own sidecars",
              "alpha" in arm_a and "beta" not in arm_a and "beta" in arm_b and "alpha" not in arm_b,
              "arm A %s, arm B %s" % (arm_a, arm_b))

        for k in range(6):
            s = os.path.join(td, "many_%d.sqlite" % k)
            sidecar(s, "t%d" % k)
            last = warm(g, s)
        check("L8 at most four generations are held, the newest among them",
              len(g._LINKS) <= 4 and any(v is last for v in g._LINKS.values()), "%d held" % len(g._LINKS))

        t = os.path.join(td, "torn.sqlite")
        sidecar(t, "alpha")
        real = getattr(g, "_generation", None)
        if real is None:
            check("L9 a file that changes while it is read answers the call and is not cached", False,
                  "this code has no generation identity")
        else:
            seen = []

            def moving(pth):
                seen.append(pth)
                gen = real(pth)
                return gen if len(seen) == 1 else gen[:-1] + (("moved",),)
            g._generation = moving
            try:
                os.environ["BRAIN_GRAPH_SIDECAR"] = t
                torn = g._link_graph()
            finally:
                g._generation = real
            check("L9 a file that changes while it is read answers the call and is not cached",
                  targets(torn) == ["alpha"] and real(t) not in g._LINKS)

        w1, w2, w3, held = walk_cases(g, td, "new")
        check("W1 a store rebuilt at its path gives the new walk", w1 == {"walk": "v1"} and w2 == {"walk": "v2"},
              "%s then %s" % (w1, w2))
        check("W2 an unchanged store is served from the cache", w3 is w2)
        check("W3 at most two walks are held", held <= 2, "%d held" % held)
        w4 = walk_rewrite_case(g, td)
        check("W4 an edges file rewritten in place at the same size with its time restored gives the new walk",
              w4 == ({"walk": "v1"}, {"walk": "v2"}), str(w4))
        diffusion_cases(os.path.join(os.path.dirname(os.path.abspath(path)), "diffusion.py"), td)

        if old:
            o = load(old, "graph_lane_before")
            oa = warm(o, a)
            ob = warm(o, b)
            check("N1 before the change the pin switch is ignored (the finding)",
                  targets(oa) == ["alpha"] and targets(ob) == ["alpha"], "B read as %s" % targets(ob))
            o2 = load(old, "graph_lane_before_2")
            r = os.path.join(td, "old_pinned.sqlite")
            sidecar(r, "alpha")
            ob1 = warm(o2, r)
            sidecar(r, "beta")
            ob2 = warm(o2, r)
            check("N2 before the change a rebuild at the same path is not read", targets(ob2) == ["alpha"],
                  "%s then %s" % (targets(ob1), targets(ob2)))
            o3 = load(old, "graph_lane_before_3")
            v1, v2, _v3, _h = walk_cases(o3, td, "old")
            check("N3 before the change the walk cache keeps the first walk for a rebuilt store",
                  v1 == {"walk": "v1"} and v2 == {"walk": "v1"}, "%s then %s" % (v1, v2))
    if pin_before is None:
        os.environ.pop("BRAIN_GRAPH_SIDECAR", None)
    else:
        os.environ["BRAIN_GRAPH_SIDECAR"] = pin_before
    print("%d failed" % len(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
