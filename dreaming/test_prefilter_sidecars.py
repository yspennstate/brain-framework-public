"""Controls for what math_prefilter.build writes beside the index.

 P1 <store>.lexdf.pkl: n is the number of current cards (a superseded revision is not counted) and each term's df comes
    from title + body + the card's expansion words
 P2 expansion words become postings of their card but never its length (dl)
 P3 the keyed signal sidecar is filled and the build reports no error for it
 P4 MATHBRAIN_EXPANSION=0: no expansion words in the postings or the df table
 P5 an expansion that raises: the index and the df table are still written, the failure is recorded
 P6 MATHBRAIN_EXPANSION_MEMORY is handed to the expansion as its memory directory; unset, the prefilter names this
    install's own memory folder (projects/<slug>/memory under the home's Claude folder, the slug from $BRAIN_SLUG,
    the installer's BRAIN_SLUG marker or the home path), as the library has had no default since 30 Sep

    python test_prefilter_sidecars.py      (a few seconds; ALL PASS at the end; a stub stands in for the expansion)
"""
import importlib
import os
import pickle
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
for cand in (os.path.join(HERE, "..", "mathbrain", "src"), os.path.join(HERE, "..", "..", "mathbrain", "src")):
    if os.path.isdir(os.path.join(cand, "math_brain")):
        sys.path.insert(0, os.path.abspath(cand))
        break
results = []


def check(ok, what):
    results.append(bool(ok))
    print(("PASS  " if ok else "FAIL  ") + what)


SEEN = {}


def stub_expansion(raises=False):
    X = types.ModuleType("math_brain.expansion")
    X.LAST_BUILD = {}

    def build(store_path, current_nodes, memory_dir=None, prompts=None):
        SEEN["memory_dir"] = memory_dir
        SEEN["ids"] = sorted(n.id for n in current_nodes)
        if raises:
            raise RuntimeError("expansion broke")
        X.LAST_BUILD.update(status="success", sidecar=None, reason=None)
        return {"card-a": "frobeniusword"}

    X.build = build
    import math_brain
    math_brain.expansion = X
    sys.modules["math_brain.expansion"] = X


def make_store(path):
    from math_brain.models import EdgeKind, GraphEdge, GraphNode, NodeKind
    from math_brain.store import ResearchStore
    with ResearchStore(path) as store:
        store.add_node(GraphNode("card-a", NodeKind.CLAIM, "Hall marriage theorem", "a bipartite graph has a perfect matching"))
        store.add_node(GraphNode("card-b", NodeKind.CLAIM, "Konig theorem", "maximum matching equals minimum vertex cover"))
        store.add_node(GraphNode("card-old", NodeKind.CLAIM, "Konig theorem draft", "obsoleteword matching"))
        store.add_edge(GraphEdge("card-b", "card-old", EdgeKind.SUPERSEDES, "revision"))


def fresh(env):
    for k in [k for k in os.environ if k.startswith("MATHBRAIN_")]:
        del os.environ[k]
    os.environ.update(env)
    sys.modules.pop("math_prefilter", None)
    return importlib.import_module("math_prefilter")


def build_in(env, raises=False):
    d = tempfile.mkdtemp()
    store = os.path.join(d, "store.sqlite3")
    make_store(store)
    stub_expansion(raises)
    SEEN.clear()
    mp = fresh(env)
    res = mp.build(store)
    data = pickle.load(open(mp.index_path(store), "rb"))
    lex = pickle.load(open(store + ".lexdf.pkl", "rb")) if os.path.exists(store + ".lexdf.pkl") else None
    return store, res, data, lex, mp


def main():
    store, res, data, lex, mp = build_in({})
    df = (lex or {}).get("df", {})
    check(lex is not None and lex["n"] == 2 and df.get("frobeniusword") == 1 and "obsoleteword" not in df
          and df.get("matching") == 2, "P1 lexdf n %r frobenius %r obsolete %r matching %r"
          % ((lex or {}).get("n"), df.get("frobeniusword"), df.get("obsoleteword"), df.get("matching")))

    ia = data["ids"].index("card-a")
    own = len(mp.tokens("Hall marriage theorem a bipartite graph has a perfect matching"))
    post = data["post"].get("frobeniusword", [])
    check([i for i, _ in post] == [ia] and data["dl"][ia] == own and data.get("expansion", {}).get("cards") == 1,
          "P2 postings %r dl %r own %r meta %r" % (post, data["dl"][ia], own, data.get("expansion")))

    from math_brain import retrieval as R
    sig = res.get("signals") or {}
    check(os.path.exists(R.signal_db_path(store)) and "error" not in sig and "skipped" not in sig,
          "P3 signal sidecar %r" % (sig,))

    _, _, data0, lex0, _ = build_in({"MATHBRAIN_EXPANSION": "0"})
    check("frobeniusword" not in data0["post"] and "frobeniusword" not in lex0["df"] and lex0["n"] == 2,
          "P4 expansion off: postings %r df %r" % ("frobeniusword" in data0["post"], "frobeniusword" in lex0["df"]))

    _, resx, datax, lexx, _ = build_in({}, raises=True)
    check(lexx is not None and lexx["n"] == 2 and "error" in (resx.get("expansion") or {})
          and "frobeniusword" not in datax["post"], "P5 failing expansion: lexdf %r meta %r"
          % (lexx is not None, resx.get("expansion")))

    build_in({"MATHBRAIN_EXPANSION_MEMORY": os.path.join(tempfile.gettempdir(), "mem")})
    named = SEEN.get("memory_dir")
    build_in({})
    home = os.path.expanduser("~")
    marker = os.path.join(HERE, "BRAIN_SLUG")
    marked = open(marker, encoding="utf-8").read().strip() if os.path.isfile(marker) else ""
    own = os.path.join(home, ".claude", "projects", os.environ.get("BRAIN_SLUG") or marked
                       or home.replace(":", "-").replace(chr(92), "-").replace("/", "-"), "memory")
    check(named and named.endswith("mem") and SEEN.get("memory_dir") == own and SEEN.get("ids") == ["card-a", "card-b"],
          "P6 memory dir %r then %r, cards %r" % (named, SEEN.get("memory_dir"), SEEN.get("ids")))
    print("ALL PASS" if all(results) else "SOME FAILED")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
