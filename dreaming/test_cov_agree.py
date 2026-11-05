"""Controls for the semantic first claim and the coverage gate's yield to agreement.

 A1 _semantic_winner: a clear top is named; a tie, a flat vector, a non-finite cosine or an empty vector names nobody;
    a single row is named; MATHBRAIN_SEMTOP_MARGIN<0 is the plain argmax
 A2 _claim_vectors_cover_store: a claim with no vector, a vector naming no node, a stale digest, a legacy row and a
    duplicated id are each caught; a superseded claim needs no vector
 A3 _semantic_top: the winner when the vectors cover the store; nobody when the file carries no digests or misses a
    claim, with the reason in _SEMTOP_WHY; a superseded revision is masked out (MATHBRAIN_SEMTOP_CURRENT=0 keeps it)
 A4 _same_card matches an id and its short prefix, never an empty one; _reset_cov_agree_state clears the state
 A5 the wiring in _main_body: the prefilter gate yields only on agreement and returns otherwise, the use reorder is
    skipped while an agreed card stands, and the seal check runs before the coherence cut
 A6 the switches: MATHBRAIN_COV_AGREE=0, MATHBRAIN_COV_SEAL=0

    python test_cov_agree.py      (under a second; ALL PASS at the end; a stub stands in for the vector store)
"""
import ast
import importlib
import os
import sqlite3
import sys
import tempfile
import types

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
results = []


def check(ok, what):
    results.append(bool(ok))
    print(("PASS  " if ok else "FAIL  ") + what)


def unit(v):
    v = np.asarray(v, dtype=np.float32)
    return v / np.linalg.norm(v)


class Encoder:
    vec = unit([1, 0, 0])

    def embed(self, text):
        return Encoder.vec


def install_stub(ids, vecs, digests, sources=None):
    S = types.ModuleType("math_brain.semantic")
    S.QUERY_PREFIX = "q: "
    S._ids = np.array(ids, dtype=object)
    S._vecs = np.stack(vecs).astype(np.float32)
    S._digests = None if digests is None else np.array(digests, dtype=object)
    S._sources = None if sources is None else np.array(sources, dtype=object)
    S._load_artifact = lambda: None
    S._query_encoder = lambda: Encoder()
    pkg = sys.modules.get("math_brain") or types.ModuleType("math_brain")
    pkg.semantic = S
    sys.modules["math_brain"] = pkg
    sys.modules["math_brain.semantic"] = S


def make_store(path, claims, superseded=()):
    con = sqlite3.connect(path)
    con.executescript("create table claims(id text); create table edges(source_id text, target_id text, kind text);"
                      "create table nodes(id text, digest text);")
    for c in claims:
        con.execute("insert into claims values (?)", (c,))
        con.execute("insert into nodes values (?, ?)", (c, "d_" + c))
    for new, old in superseded:
        con.execute("insert into edges values (?, ?, 'supersedes')", (new, old))
    con.commit()
    con.close()


def fresh(env, store):
    for k in [k for k in os.environ if k.startswith("MATHBRAIN_")]:
        del os.environ[k]
    os.environ.update(env)
    sys.modules.pop("math_sense", None)
    m = importlib.import_module("math_sense")
    m.STORE_PATH = store
    return m


def main():
    tmp = tempfile.mkdtemp()
    store = os.path.join(tmp, "store.sqlite")
    make_store(store, ["aaaa", "bbbb", "cccc", "old1"], superseded=[("cccc", "old1")])
    m = fresh({}, store)

    w = m._semantic_winner
    clear = w(np.array([0.1, 0.9, 0.2]), ["x", "y", "z"], np)
    tie = w(np.array([0.9, 0.9, 0.2]), ["x", "y", "z"], np)
    flat = w(np.array([0.5, 0.5, 0.5]), ["x", "y", "z"], np)
    nan = w(np.array([0.1, np.nan, 0.2]), ["x", "y", "z"], np)
    empty = w(np.array([]), [], np)
    one = w(np.array([0.3]), ["x"], np)
    neg = fresh({"MATHBRAIN_SEMTOP_MARGIN": "-1"}, store)._semantic_winner(np.array([0.9, 0.9]), ["x", "y"], np)
    check(clear == "y" and tie is None and flat is None and nan is None and empty is None and one == "x" and neg == "x",
          "A1 winner %r tie %r flat %r nan %r empty %r one %r argmax %r" % (clear, tie, flat, nan, empty, one, neg))

    m = fresh({}, store)
    cv = m._claim_vectors_cover_store
    full = cv(["aaaa", "bbbb", "cccc"], digests=["d_aaaa", "d_bbbb", "d_cccc"])
    gap = cv(["aaaa", "bbbb"], digests=["d_aaaa", "d_bbbb"])
    phantom = cv(["aaaa", "bbbb", "cccc", "zzzz"], digests=["d_aaaa", "d_bbbb", "d_cccc", "x"])
    stale = cv(["aaaa", "bbbb", "cccc"], digests=["d_aaaa", "OLD", "d_cccc"])
    legacy = cv(["aaaa", "bbbb", "cccc"], digests=["d_aaaa", "d_bbbb", "d_cccc"], sources=["model", "legacy", "model"])
    dup = cv(["aaaa", "aaaa", "bbbb", "cccc"])
    check(full == ([], []) and gap == (["cccc"], []) and phantom == ([], ["zzzz"]) and stale == (["bbbb"], [])
          and legacy == (["bbbb"], []) and dup == (["aaaa"], []),
          "A2 full %r gap %r phantom %r stale %r legacy %r dup %r" % (full, gap, phantom, stale, legacy, dup))

    ids = ["aaaa", "bbbb", "cccc", "old1"]
    dig = ["d_" + i for i in ids]
    vecs = [unit([0, 1, 0]), unit([0.6, 0.8, 0]), unit([0.9, 0.1, 0]), unit([1, 0, 0])]
    install_stub(ids, vecs, dig)
    m = fresh({}, store)
    top = m._semantic_top("what is it")
    m0 = fresh({"MATHBRAIN_SEMTOP_CURRENT": "0"}, store)
    top0 = m0._semantic_top("what is it")
    install_stub(ids, vecs, None)
    m = fresh({}, store)
    nodig = m._semantic_top("what is it")
    why_nodig = m._SEMTOP_WHY.get("what is it", "")
    install_stub(ids[:2] + ids[3:], vecs[:2] + vecs[3:], dig[:2] + dig[3:])
    m = fresh({}, store)
    miss = m._semantic_top("what is it")
    why_miss = m._SEMTOP_WHY.get("what is it", "")
    check(top == "cccc" and top0 == "old1" and nodig is None and "no store digests" in why_nodig
          and miss is None and "1 current claim" in why_miss,
          "A3 top %r all-revisions %r no-digests %r missing %r (%s)" % (top, top0, nodig, miss, why_miss[:60]))

    m._COV_AGREED["prefilter"] = "x"
    m._SEMTOP["p"] = "x"
    m._SEMTOP_WHY["p"] = "winner"
    m._reset_cov_agree_state()
    sc = m._same_card
    check(sc("abcdef0123456789zz", "abcdef0123456789") and sc("aaaa", "aaaa") and not sc("aaaa", "bbbb")
          and not sc("", "aaaa") and not sc(None, None) and not m._COV_AGREED and not m._SEMTOP and not m._SEMTOP_WHY,
          "A4 same card and reset")

    src = open(os.path.join(HERE, "math_sense.py"), encoding="utf-8").read()
    body = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "_main_body")
    seg = lambda n: ast.get_source_segment(src, n) or ""
    ifs = [n for n in ast.walk(body) if isinstance(n, ast.If)]
    gate = [n for n in ifs if "COV_AGREE" in seg(n.test) and "_semantic_top" in seg(n.test)
            and "_COV_AGREED" in seg(n) and any(isinstance(x, ast.Return) for x in n.orelse)]
    use = [n for n in ifs if "MATH_USE_WEIGHT" in seg(n.test) and "not (COV_SEAL and _COV_AGREED)" in seg(n.test)]
    seal = [n for n in ifs if seg(n.test).strip() == "block and emitted_ids and COV_SEAL and _COV_AGREED"
            and "coverage_agree_identity_lost" in seg(n)]
    coh = [n for n in ifs if seg(n.test).strip() == "block and COH_CUT and len(emitted_ids) > 1"]
    reset = [n for n in ast.walk(body) if isinstance(n, ast.Call) and seg(n.func) == "_reset_cov_agree_state"]
    order = bool(seal and coh and seal[0].lineno < coh[0].lineno and use and use[0].lineno < seal[0].lineno)
    check(len(gate) == 1 and len(use) == 1 and len(seal) == 1 and order and reset and reset[0].lineno < gate[0].lineno,
          "A5 wiring gate %d use %d seal %d order %r reset %d" % (len(gate), len(use), len(seal), order, len(reset)))

    off = fresh({"MATHBRAIN_COV_AGREE": "0", "MATHBRAIN_COV_SEAL": "0"}, store)
    on = fresh({}, store)
    check(off.COV_AGREE is False and off.COV_SEAL is False and on.COV_AGREE is True and on.COV_SEAL is True
          and on.SEMTOP_CURRENT is True and on.SEMTOP_MARGIN == 1e-6, "A6 switches")
    print("ALL PASS" if all(results) else "SOME FAILED")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
