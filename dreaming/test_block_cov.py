"""Controls for the block coverage floor and its yield to channel agreement.

 B1 _block_best_coverage: the best card's idf share of the prompt's store-known tokens (1.0 for a card carrying them all)
 B2 a plain question whose block covers little of it is silenced; digits, a formula token, a pinned title, an argument
    shape, a prompt over 1,500 characters or MATHBRAIN_BLOCK_COV=0 are never judged
 B3 the exemptions: a card titled with a name the prompt uses; a rare term the lead card's title shares with the prompt
    (each switched off by its dial, the block is silenced)
 B4 the house floor: a block led by one of the project's records is judged at 0.25, not 0.35
 B5 the agreement yield: under the floor, a block holding the meaning channel's first claim passes, the claim moved to
    the front and recorded; MATHBRAIN_COV_AGREE_WIDE=0 needs it to be the lead; MATHBRAIN_COV_AGREE=0 silences
 B6 the wiring in _main_body: the verdict runs before the integrity read and returns on a silence, not on a yield

    python test_block_cov.py      (under a second; ALL PASS at the end; stubs stand in for the prefilter and the store)
"""
import ast
import importlib
import os
import re
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
results = []


def check(ok, what):
    results.append(bool(ok))
    print(("PASS  " if ok else "FAIL  ") + what)


DF = {"nontrivial": 2, "zeros": 4, "zeta": 3, "function": 50, "critical": 6, "line": 30, "relative": 40}
POST = {t: list(range(n)) for t, n in DF.items()}


def install_stubs():
    pf = types.ModuleType("math_prefilter")
    pf.load = lambda path: {"n": 100, "post": POST}
    pf.tokens = lambda text: re.findall(r"[a-z]{3,}", (text or "").lower())
    sys.modules["math_prefilter"] = pf
    R = types.ModuleType("math_brain.retrieval")
    R.index_signals = lambda text: types.SimpleNamespace(math_tokens=tuple(re.findall(r"[=^_\\]", text or "")))
    pkg = sys.modules.get("math_brain") or types.ModuleType("math_brain")
    pkg.retrieval = R
    sys.modules["math_brain"] = pkg
    sys.modules["math_brain.retrieval"] = R


def fresh(env):
    for k in [k for k in os.environ if k.startswith("MATHBRAIN_")]:
        del os.environ[k]
    os.environ.update(env)
    sys.modules.pop("math_sense", None)
    m = importlib.import_module("math_sense")
    install_stubs()
    m._is_house = lambda node, meta=None: False
    m._semantic_top = lambda prompt: None
    return m


class Node:
    def __init__(self, nid, title, body=""):
        self.id, self.title, self.body, self.kind, self.metadata = nid, title, body, "claim", {}


class Hit:
    def __init__(self, nid, title, body=""):
        self.node = Node(nid, title, body)


Q = "where do the nontrivial zeros of the zeta function sit relative to the critical line"
QN = "where do the nontrivial zeros of the Riemann zeta function sit relative to the critical line"


def low():
    return [Hit("aaaa", "Function spaces"), Hit("cccc", "Line integrals")]


def main():
    m = fresh({})
    full = m._block_best_coverage(Q, [Hit("x", "zeros", "nontrivial zeta function critical line relative")])
    part = m._block_best_coverage(Q, low())
    check(abs(full - 1.0) < 1e-9 and 0 < part < 0.35, "B1 coverage full %.3f low block %.3f" % (full, part))

    v = m._block_cov_verdict(Q, low(), [])
    skips = [m._block_cov_verdict(Q + " 2", low(), []), m._block_cov_verdict(Q + " x^2", low(), []),
             m._block_cov_verdict(Q, low(), ["aaaa"]), m._block_cov_verdict("Q" * 1501, low(), []),
             fresh({"MATHBRAIN_BLOCK_COV": "0"})._block_cov_verdict(Q, low(), [])]
    m = fresh({})
    arg = m.ARGUMENT_SHAPE.search("Proof. Suppose " + Q + ". Then by the lemma it follows that the claim holds.")
    arg_v = m._block_cov_verdict("Proof. Suppose " + Q + ". Then by the lemma it follows that the claim holds.", low(), [])
    check(v is not None and v.startswith("block_coverage:") and skips == [None] * 5 and (not arg or arg_v is None),
          "B2 silenced %r, skipped %r, argument %r" % (v, skips, arg_v if arg else "n/a"))

    named = low() + [Hit("rrrr", "Riemann hypothesis")]
    nv = m._block_cov_verdict(QN, named, [])
    nv0 = fresh({"MATHBRAIN_BLOCK_COV_NAME_EXEMPT": "0"})._block_cov_verdict(QN, named, [])
    m = fresh({})
    rare = [Hit("zzzz", "Zeta regularization"), Hit("aaaa", "Function spaces")]
    rv = m._block_cov_verdict(Q, rare, [])
    rv0 = fresh({"MATHBRAIN_COV_RARE_EXEMPT": "0"})._block_cov_verdict(Q, rare, [])
    check(nv is None and (nv0 or "").startswith("block_coverage") and rv is None and (rv0 or "").startswith("block_coverage"),
          "B3 named %r/%r rare %r/%r" % (nv, nv0, rv, rv0))

    m = fresh({})
    mid = [Hit("hhhh", "Critical line relative")]
    cov = m._block_best_coverage(Q, mid)
    out = m._block_cov_verdict(Q, mid, [])
    m._is_house = lambda node, meta=None: True
    home = m._block_cov_verdict(Q, mid, [])
    nh = fresh({"MATHBRAIN_BLOCK_COV_HOUSE": "0"})
    nh._is_house = lambda node, meta=None: True
    nhv = nh._block_cov_verdict(Q, mid, [])
    check(0.25 < cov < 0.35 and (out or "").startswith("block_coverage") and home is None
          and (nhv or "").startswith("block_coverage"), "B4 cov %.3f other %r house %r house-off %r" % (cov, out, home, nhv))

    m = fresh({})
    m._semantic_top = lambda prompt: "cccc"
    blk = low()
    av = m._block_cov_verdict(Q, blk, [])
    order = [h.node.id for h in blk]
    agreed = dict(m._COV_AGREED)
    w0 = fresh({"MATHBRAIN_COV_AGREE_WIDE": "0"})
    w0._semantic_top = lambda prompt: "cccc"
    wv = w0._block_cov_verdict(Q, low(), [])
    na = fresh({"MATHBRAIN_COV_AGREE": "0"})
    na._semantic_top = lambda prompt: "aaaa"
    nav = na._block_cov_verdict(Q, low(), [])
    lead = fresh({"MATHBRAIN_COV_AGREE_WIDE": "0"})
    lead._semantic_top = lambda prompt: "aaaa"
    lv = lead._block_cov_verdict(Q, low(), [])
    check((av or "").startswith("coverage_agree_block") and order == ["cccc", "aaaa"] and agreed == {"block": "cccc"}
          and (wv or "").startswith("block_coverage") and (nav or "").startswith("block_coverage")
          and (lv or "").startswith("coverage_agree_block"),
          "B5 yield %r order %r agreed %r wide0 %r off %r lead %r" % (av, order, agreed, wv, nav, lv))

    src = open(os.path.join(HERE, "math_sense.py"), encoding="utf-8").read()
    body = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "_main_body")
    seg = lambda n: ast.get_source_segment(src, n) or ""
    calls = [n for n in ast.walk(body) if isinstance(n, ast.Call) and seg(n.func) == "_block_cov_verdict"]
    integ = [n for n in ast.walk(body) if isinstance(n, ast.Call) and seg(n.func) == "consistent_hits"]
    ifs = [n for n in ast.walk(body) if isinstance(n, ast.If) and seg(n.test).strip() == "_bcv and _bcv.startswith(\"coverage_agree_block\")"]
    ok = (len(calls) == 1 and integ and calls[0].lineno < integ[0].lineno and len(ifs) == 1
          and not any(isinstance(x, ast.Return) for x in ifs[0].body)
          and ifs[0].orelse and any(isinstance(x, ast.Return) for x in ifs[0].orelse[0].body))
    check(ok, "B6 wiring calls %d before integrity %r" % (len(calls), bool(integ and calls and calls[0].lineno < integ[0].lineno)))
    print("ALL PASS" if all(results) else "SOME FAILED")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
