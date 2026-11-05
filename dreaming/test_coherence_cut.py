"""Controls for the coherence cut: a weak lead in a scattered block is withheld, a named lead card is spared.

 C1 _incoherent_weak_block: a scattered block with a far lead is cut; a coherent block is kept without embedding the
    prompt; a near lead is kept; fewer than two cards with vectors is never cut; a failing vector store keeps the block
 C2 the tail reading: a long lead-in followed by a near question is kept; MATHBRAIN_COH_TAIL=0 reads the whole prompt
 C3 _prompt_tail / _closing_paragraph cut at a sentence or paragraph start
 C4 _lead_title_names_prompt: "Sylow theorems." against "Sylow Theorems" is exempt; a concept title is not; a prompt
    with no name left after the common words is not
 C5 the switches: MATHBRAIN_COH_CUT=0, MATHBRAIN_COH_CUT_LEAD, MATHBRAIN_COH_NAME_EXEMPT=0

    python test_coherence_cut.py      (under a second; ALL PASS at the end; a stub stands in for the vector store)
"""
import importlib
import os
import sys
import types

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
results = []


def check(ok, what):
    results.append(ok)
    print(("PASS  " if ok else "FAIL  ") + what)


def unit(v):
    v = np.asarray(v, dtype=np.float32)
    return v / np.linalg.norm(v)


class Encoder:
    calls = 0
    table = {}

    def embed(self, text):
        Encoder.calls += 1
        for key, vec in Encoder.table.items():
            if key in text and len(text) < 400:
                return vec
        return unit([0, 0, 0, 1])


def install_stub(vectors):
    S = types.ModuleType("math_brain.semantic")
    S.QUERY_PREFIX = "q: "
    S._ids = np.array(list(vectors), dtype=object)
    S._vecs = np.stack([vectors[k] for k in vectors])
    S._load_artifact = lambda: None
    S._query_encoder = lambda: Encoder()
    pkg = sys.modules.get("math_brain") or types.ModuleType("math_brain")
    pkg.semantic = S
    sys.modules["math_brain"] = pkg
    sys.modules["math_brain.semantic"] = S


def fresh(env):
    for k in [k for k in os.environ if k.startswith("MATHBRAIN_")]:
        del os.environ[k]
    os.environ.update(env)
    sys.modules.pop("math_sense", None)
    return importlib.import_module("math_sense")


VEC = {
    "a": unit([1, 0, 0, 0]), "b": unit([0, 1, 0, 0]), "c": unit([0, 0, 1, 0]),
    "d": unit([1, 0.1, 0, 0]), "e": unit([1, 0, 0.1, 0]),
}


def main():
    install_stub(VEC)
    Encoder.table = {"NEAR": unit([1, 0, 0, 0])}
    m = fresh({})
    far = m._incoherent_weak_block("an unrelated question", ["a", "b", "c"])
    Encoder.calls = 0
    coherent = m._incoherent_weak_block("an unrelated question", ["a", "d", "e"])
    calls = Encoder.calls
    near = m._incoherent_weak_block("NEAR question", ["a", "b", "c"])
    single = m._incoherent_weak_block("an unrelated question", ["a", "zzz"])
    check(far is not None and coherent is None and calls == 0 and near is None and single is None,
          "C1 cut %r coherent %r (embeds %d) near %r single %r" % (far, coherent, calls, near, single))
    sys.modules["math_brain.semantic"]._load_artifact = lambda: 1 / 0
    broken = m._incoherent_weak_block("an unrelated question", ["a", "b", "c"])
    check(broken is None, "C1b a failing vector store keeps the block")
    install_stub(VEC)

    lead_in = "Some background that goes on. " * 30 + "\n\nNEAR: what does the lemma say about this bound?"
    kept = m._incoherent_weak_block(lead_in, ["a", "b", "c"])
    m0 = fresh({"MATHBRAIN_COH_TAIL": "0"})
    whole = m0._incoherent_weak_block(lead_in, ["a", "b", "c"])
    check(kept is None and whole is not None and m0.COH_TAIL == 0, "C2 tail kept %r, whole-prompt %r" % (kept, whole))

    m = fresh({})
    long = "First sentence here. " * 20 + "The question comes last. What is it?"
    tail = m._prompt_tail(long, 60)
    para = m._closing_paragraph("Lead-in text.\n\nWhat is the value of the constant in the Sylow count?")
    check(tail is not None and tail[0].isupper() and len(tail) <= 90 and m._prompt_tail("short", 60) is None
          and para is not None and para.startswith("What") and m._closing_paragraph("one paragraph only") is None,
          "C3 tail %r para %r" % (tail, para))

    ok = m._lead_title_names_prompt("Sylow theorems.", "Sylow Theorems")
    concept = m._lead_title_names_prompt("Gaussian process zeros", "Gaussian process")
    other = m._lead_title_names_prompt("Sylow theorems.", "Cauchy's theorem on groups")
    check(ok and not concept and not other, "C4 name exemption %r %r %r" % (ok, concept, other))

    off = fresh({"MATHBRAIN_COH_CUT": "0"})
    low = fresh({"MATHBRAIN_COH_CUT_LEAD": "0.70"})
    nx = fresh({"MATHBRAIN_COH_NAME_EXEMPT": "0"})
    on = fresh({})
    check(off.COH_CUT is False and low.COH_CUT_LEAD == 0.70 and on.COH_CUT_LEAD == 0.80 and on.COH_CUT_PAIR == 0.70
          and nx.COH_NAME_EXEMPT is False and on.COH_NAME_EXEMPT is True, "C5 switches")
    print("ALL PASS" if all(results) else "SOME FAILED")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
