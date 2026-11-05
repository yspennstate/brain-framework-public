"""Controls for the far-block z rule (MATHBRAIN_MEANQ_Z, off by default).

 Z1 unset, the rule is off (MEANQ_Z is None)
 Z2 at 1.25: a block whose cards point the prompt's way keeps the block; a block pointing elsewhere on the store's own
    scale is silenced with its z and mean cosine
 Z3 ids the semantic index does not hold, or an encoder that raises, keep the block
 Z4 the wiring in _main_body: after the coherence cut, before the quoted-name rule, returns on a silence

    python test_far_block_z.py      (under a second; ALL PASS at the end; a stub stands in for the semantic index)
"""
import ast
import importlib
import os
import sys
import types

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
results = []


def check(ok, what):
    results.append(bool(ok))
    print(("PASS  " if ok else "FAIL  ") + what)


def stub_semantic(raises=False):
    rng = np.random.default_rng(7)
    vecs = rng.normal(size=(500, 16)).astype(np.float32)
    vecs[0] = 0.0
    vecs[0, 0] = 1.0
    vecs[1] = 0.0
    vecs[1, 0] = 0.9
    vecs[1, 1] = 0.1
    vecs[2] = 0.0
    vecs[2, 0] = -1.0
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
    S = types.ModuleType("math_brain.semantic")
    S._vecs = vecs
    S._ids = np.array(["c%d" % i for i in range(len(vecs))])
    S.QUERY_PREFIX = ""
    S._load_artifact = lambda: None

    class Enc:
        def embed(self, text):
            if raises:
                raise RuntimeError("encoder down")
            v = np.zeros(16, dtype=np.float32)
            v[0] = 1.0
            return v
    S._query_encoder = lambda: Enc()
    import math_brain
    math_brain.semantic = S
    sys.modules["math_brain.semantic"] = S


def fresh(env):
    for k in [k for k in os.environ if k.startswith("MATHBRAIN_")]:
        del os.environ[k]
    os.environ.update(env)
    sys.modules.pop("math_sense", None)
    return importlib.import_module("math_sense")


def main():
    for cand in (os.path.join(HERE, "..", "mathbrain", "src"), os.path.join(HERE, "..", "..", "mathbrain", "src")):
        if os.path.isdir(os.path.join(cand, "math_brain")):
            sys.path.insert(0, os.path.abspath(cand))
            break
    check(fresh({}).MEANQ_Z is None, "Z1 off by default")

    stub_semantic()
    m = fresh({"MATHBRAIN_MEANQ_Z": "1.25"})
    near = m._far_block_z("a question", ["c0", "c1"])
    far = m._far_block_z("a question", ["c2"])
    check(near is None and isinstance(far, tuple) and far[0] < 1.25 and far[1] < 0, "Z2 near %r far %r" % (near, far))

    unknown = m._far_block_z("a question", ["nope"])
    stub_semantic(raises=True)
    m2 = fresh({"MATHBRAIN_MEANQ_Z": "1.25"})
    broken = m2._far_block_z("a question", ["c2"])
    check(unknown is None and broken is None, "Z3 unknown ids %r failing encoder %r" % (unknown, broken))

    src = open(os.path.join(HERE, "math_sense.py"), encoding="utf-8").read()
    body = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "_main_body")
    seg = lambda n: ast.get_source_segment(src, n) or ""
    gate = [n for n in ast.walk(body) if isinstance(n, ast.If) and "MEANQ_Z is not None" in seg(n.test)]
    coh = [n for n in ast.walk(body) if isinstance(n, ast.Call) and seg(n.func) == "_incoherent_weak_block"]
    quote = [n for n in ast.walk(body) if isinstance(n, ast.If) and "QUOTE_COV is not None" in seg(n.test)]
    ok = (len(gate) == 1 and coh and quote and coh[0].lineno < gate[0].lineno < quote[0].lineno
          and any(isinstance(x, ast.Return) for x in ast.walk(gate[0])))
    check(ok, "Z4 wiring gate %d between coherence and quote %r"
          % (len(gate), bool(gate and coh and quote and coh[0].lineno < gate[0].lineno < quote[0].lineno)))
    print("ALL PASS" if all(results) else "SOME FAILED")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
