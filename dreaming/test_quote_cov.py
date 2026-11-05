"""Controls for the quoted-name coverage rule.

 Q1 a short prompt quoting a name no served card carries is silenced with its coverage and word count
 Q2 a card that carries the quoted name keeps the block
 Q3 an all-caps quote of three or more letters is covered by a card whose word initials spell it; a made-up one is not
 Q4 never judged: a prompt with more than QUOTE_MAX_WORDS words outside its quotes, a store with no df table, a prompt
    with no quote; MATHBRAIN_QUOTE_COV=0 switches the rule off
 Q5 the wiring in _main_body: after the coherence cut, before the empty-block exit, skipped for a pinned title, returns

    python test_quote_cov.py      (a second or two; ALL PASS at the end)
"""

import sys
if __name__ == "__main__" and any(arg in ("-h", "--help") for arg in sys.argv[1:]):
    print(__doc__ or "No command-line options.")
    raise SystemExit(0)

import ast
import importlib
import os
import pickle
import sys
import tempfile

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


class Node:
    def __init__(self, nid, title, body=""):
        self.id, self.title, self.body, self.kind, self.metadata = nid, title, body, "claim", {}


class Hit:
    def __init__(self, nid, title, body=""):
        self.node = Node(nid, title, body)


DF = {"szemeredi": 2, "regularity": 4, "lemma": 40, "graph": 60, "burkholder": 2, "davis": 3, "gundy": 2,
      "inequality": 50, "martingale": 8, "hall": 5, "marriage": 3, "theorem": 70}


def fresh(env, table=True):
    d = tempfile.mkdtemp()
    store = os.path.join(d, "store.sqlite3")
    if table:
        with open(store + ".lexdf.pkl", "wb") as fh:
            pickle.dump({"n": 100, "df": DF}, fh)
    for k in [k for k in os.environ if k.startswith("MATHBRAIN_")]:
        del os.environ[k]
    os.environ.update(env)
    os.environ["MATHBRAIN_STORE"] = store
    sys.modules.pop("math_sense", None)
    m = importlib.import_module("math_sense")
    m.STORE_PATH = store
    return m


OTHER = [Hit("a", "Hall marriage theorem", "a bipartite graph has a perfect matching")]
SZ = [Hit("s", "Szemeredi regularity lemma", "every graph splits into regular pairs")]
BDG = [Hit("b", "Burkholder Davis Gundy inequality", "moments of a continuous martingale and its quadratic variation")]


def main():
    m = fresh({})
    q = 'what does "Szemeredi regularity lemma" say'
    v = m._quote_uncovered(q, OTHER)
    check(isinstance(v, tuple) and v[0] < m.QUOTE_COV and v[1] == 3, "Q1 uncovered %r" % (v,))
    check(m._quote_uncovered(q, SZ) is None and m._quote_uncovered(q, OTHER + SZ) is None, "Q2 covered keeps the block")

    acro = m._quote_uncovered('state "BDG" for continuous martingales', BDG)
    made = m._quote_uncovered('state "QZX" for continuous martingales', BDG)
    check(acro is None and isinstance(made, tuple), "Q3 BDG %r made-up %r" % (acro, made))

    long_q = q + " " + " ".join(["please explain carefully with every detail"] * 4)
    skips = [m._quote_uncovered(long_q, OTHER), fresh({}, table=False)._quote_uncovered(q, OTHER),
             fresh({})._quote_uncovered("what does the Szemeredi regularity lemma say", OTHER)]
    off = fresh({"MATHBRAIN_QUOTE_COV": "0"})
    check(skips == [None, None, None] and off.QUOTE_COV is None, "Q4 skips %r off %r" % (skips, off.QUOTE_COV))

    src = open(os.path.join(HERE, "math_sense.py"), encoding="utf-8").read()
    body = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "_main_body")
    seg = lambda n: ast.get_source_segment(src, n) or ""
    gate = [n for n in ast.walk(body) if isinstance(n, ast.If) and "QUOTE_COV is not None" in seg(n.test)]
    coh = [n for n in ast.walk(body) if isinstance(n, ast.Call) and seg(n.func) == "_incoherent_weak_block"]
    empty = [n for n in ast.walk(body) if isinstance(n, ast.If) and seg(n.test).strip() == "not block"]
    ok = (len(gate) == 1 and "not pinned_ids" in seg(gate[0].test) and coh and empty
          and coh[0].lineno < gate[0].lineno < max(e.lineno for e in empty)
          and any(isinstance(x, ast.Return) for x in ast.walk(gate[0])))
    check(ok, "Q5 wiring gate %d after coherence %r" % (len(gate), bool(gate and coh and coh[0].lineno < gate[0].lineno)))
    print("ALL PASS" if all(results) else "SOME FAILED")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
