"""Controls for three graph-walk speedups: seed-row ppr (BRAIN_PPR_SEEDROWS), 16 walk steps (BRAIN_PPR_ITERS) and the
resident lazy link walk (graph_lane.LAZY_RESIDENT).

 P1 the seed-row ppr equals the plain loop bit for bit on random graphs with dangling nodes and several seed columns
 P2 BRAIN_PPR_SEEDROWS=0 runs the plain loop; a walk one step short differs (the comparison can see a change)
 I1 graph_lane.ranked walks 16 steps by default
 I2 BRAIN_PPR_ITERS=20 walks 20; a value that is not a number falls back to 16
 L1 the resident lazy walk returns what the one-shot walk returns
 L2 a second resident call reuses the kept neighbour lists while the sidecar is unchanged
 L3 a commit to the sidecar makes a new key: the kept lists are dropped and the new link is seen
 L4 a sidecar in WAL mode is never kept (no safe key)

    python test_walk_speedups.py      (a few seconds; ALL PASS at the end)
Builds its graphs and sidecars in a temporary folder; nothing of the user's is read.
"""
if __name__ == "__main__" and __import__("sys").argv[1:2] in (["-h"], ["--help"]):
    print((__doc__ or "").strip() or "run with no arguments")
    raise SystemExit(0)

import os
import shutil
import sqlite3
import sys
import tempfile
import types

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import diffusion  # noqa: E402
import graph_lane  # noqa: E402

TMP = tempfile.mkdtemp(prefix="walk_speedups_")
results = []
N_CHECKS = 8


def check(ok, what):
    results.append(bool(ok))
    print(("PASS  " if ok else "FAIL  ") + what)


def fake_graph(rng, n):
    a = (rng.random((n, n)) < 0.03) * rng.random((n, n))
    dangling = a.sum(axis=1) == 0
    out = np.where(dangling, 1.0, a.sum(axis=1))
    P = a / out[:, None]
    return types.SimpleNamespace(alpha=0.3, iters=20, dtype=np.float32, PT=P.T.astype(np.float32), dangling=dangling)


def ppr(g, S, iters=None, plain=False):
    os.environ.pop("BRAIN_PPR_SEEDROWS", None)
    if plain:
        os.environ["BRAIN_PPR_SEEDROWS"] = "0"
    try:
        return diffusion.Diffusion.ppr(g, S, iters=iters)
    finally:
        os.environ.pop("BRAIN_PPR_SEEDROWS", None)


try:
    rng = np.random.default_rng(7)
    same = True
    for trial in range(5):
        g = fake_graph(rng, 300)
        S = np.zeros((300, 3), np.float32)
        for b in range(3):
            S[rng.choice(300, 4, replace=False), b] = rng.random(4) + 0.1
        same = same and np.array_equal(ppr(g, S), ppr(g, S, plain=True))
    check(same, "P1 seed-row ppr equals the plain loop bit for bit (5 graphs, 3 seed columns)")
    g = fake_graph(rng, 300)
    S = np.zeros((300, 1), np.float32)
    S[[3, 9], 0] = 1.0
    check(not np.array_equal(ppr(g, S, iters=19, plain=True), ppr(g, S)), "P2 a walk one step short differs")
except Exception as e:
    check(False, "ppr raised %s: %s" % (type(e).__name__, e))

try:
    store = os.path.join(TMP, "store")
    os.makedirs(store)
    open(os.path.join(store, "edges.npz"), "wb").close()
    seen = []

    class G:
        N = 1

        def seed_words(self, q):
            return {0: 1.0}

        def ppr(self, S, iters=None):
            seen.append(iters)
            return S

        def ranked_memories(self, R, gamma=None, allowed=None, depth=None, with_scores=None):
            return ([],)

    real_graph = graph_lane._graph
    graph_lane._graph = lambda d: G()
    d = {"store": store, "gamma": 1.0, "prf": 0}
    try:
        for v in (None, "20", "x"):
            os.environ.pop("BRAIN_PPR_ITERS", None)
            if v is not None:
                os.environ["BRAIN_PPR_ITERS"] = v
            graph_lane.ranked("a question", d=d)
    finally:
        graph_lane._graph = real_graph
        os.environ.pop("BRAIN_PPR_ITERS", None)
    check(seen[:1] == [16], "I1 ranked walks 16 steps by default: %r" % (seen[:1],))
    check(seen[1:] == [20, 16], "I2 BRAIN_PPR_ITERS=20 walks 20, a non-number falls back to 16: %r" % (seen[1:],))
except Exception as e:
    check(False, "ranked raised %s: %s" % (type(e).__name__, e))
    check(False, "I2 not reached")


def sidecar(name, wal=False):
    p = os.path.join(TMP, name)
    con = sqlite3.connect(p)
    if wal:
        con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE link (src TEXT, dst TEXT, w REAL)")
    con.executemany("INSERT INTO link VALUES (?,?,?)",
                    [("a", "b", 1.0), ("b", "c", 0.5), ("c", "d", 2.0), ("a", "e", 0.3), ("e", "f", 1.0)])
    con.commit()
    con.close()
    return p


def walk(resident):
    graph_lane.LAZY_RESIDENT = resident
    try:
        return graph_lane._link_ranked_lazy([("a", 1.0), ("c", 0.5)], 10, 0.5, 1e-6, 200)
    finally:
        graph_lane.LAZY_RESIDENT = False


try:
    p = sidecar("sidecar.sqlite")
    os.environ["BRAIN_GRAPH_SIDECAR"] = p
    getattr(graph_lane, "_LAZY_RES", {}).clear()
    one_shot = walk(False)
    first = walk(True)
    check(one_shot == first and len(graph_lane._LAZY_RES) == 1 and one_shot,
          "L1 resident walk equals the one-shot walk (%d memories)" % len(one_shot))
    nbr, out = next(iter(graph_lane._LAZY_RES.values()))
    nbr["a"] = nbr["a"] + [("zzz", 5.0)]       # a mark only the kept lists carry
    out["a"] = out["a"] + 5.0
    check("zzz" in dict(walk(True)), "L2 a second call reuses the kept lists while the file is unchanged")
    con = sqlite3.connect(p)
    con.execute("INSERT INTO link VALUES ('d', 'g', 1.0)")
    con.commit()
    con.close()
    after = dict(walk(True))
    check("zzz" not in after and "g" in after, "L3 a commit makes a new key: kept lists dropped, new link seen")
    getattr(graph_lane, "_LAZY_RES", {}).clear()
    os.environ["BRAIN_GRAPH_SIDECAR"] = sidecar("wal.sqlite", wal=True)
    w = walk(True)
    check(w == walk(False) and not graph_lane._LAZY_RES, "L4 a WAL sidecar is walked but never kept")
except Exception as e:
    check(False, "lazy walk raised %s: %s" % (type(e).__name__, e))
finally:
    os.environ.pop("BRAIN_GRAPH_SIDECAR", None)
    getattr(graph_lane, "_LAZY_RES", {}).clear()
    shutil.rmtree(TMP, ignore_errors=True)

ok = all(results) and len(results) == N_CHECKS
print("ALL PASS" if ok else "FAILED %d of %d" % (N_CHECKS - results.count(True), N_CHECKS))
sys.exit(0 if ok else 1)
