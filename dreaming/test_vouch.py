"""Controls for the vouch route (_vouch_stage1, _vouch_agrees) and build_vouch_vocab.py.

 V1 without a vocabulary file the route is closed; with one, two distinct vocabulary words open stage one, one does not
 V2 short words and stop words never count; MATHBRAIN_VOUCH_VOCAB names the file
 V3 stage two agrees only when the top card is the semantic channel's first claim, and fails closed
 V4 _main_body: stage one sits before the gate's refusal, stage two after the property order, the seal before the
    coherence cut, the use reorder skips a vouched block, and the per-prompt reset clears the route
 V5 the builder keeps store words that are rare in refused prompts and drops the ones that are common there

    python test_vouch.py      (a few seconds; ALL PASS at the end)
"""
import importlib
import json
import os
import sys
import tempfile
import types
from types import SimpleNamespace as NS

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
results = []


def check(ok, what):
    results.append(bool(ok))
    print(("PASS  " if ok else "FAIL  ") + what)


def fresh(env):
    for k in [k for k in os.environ if k.startswith("MATHBRAIN_")]:
        del os.environ[k]
    os.environ.update(env)
    sys.modules.pop("math_sense", None)
    return importlib.import_module("math_sense")


def main():
    tmp = tempfile.mkdtemp()
    voc = os.path.join(tmp, "v.json")
    with open(voc, "w", encoding="utf-8") as fh:
        json.dump({"terms": ["sunflower", "lemma-free", "exponential", "these"]}, fh)

    m = fresh({"MATHBRAIN_VOUCH_VOCAB": os.path.join(tmp, "absent.json")})
    check(not m._vouch_stage1("why is the sunflower bound exponential here"), "V1 no vocabulary file: route closed")
    m = fresh({"MATHBRAIN_VOUCH_VOCAB": voc})
    check(m._vouch_stage1("why is the sunflower bound exponential here"), "V1 two vocabulary words open stage one")
    check(not m._vouch_stage1("why is the sunflower bound so large here"), "V1 one word does not")
    check(not m._vouch_stage1("these sunflower things"), "V2 a stop word never counts")
    check(m._vouch_stage1("a lemma-free sunflower argument"), "V2 hyphenated word counts; file from the setting")

    top = hit = NS(node=NS(id="abc123"))
    m._semantic_top = lambda p: "abc123"
    check(m._vouch_agrees("q", [hit]), "V3 agrees with the first claim")
    m._semantic_top = lambda p: "zzz999"
    check(not m._vouch_agrees("q", [hit]), "V3 another first claim disagrees")
    m._semantic_top = lambda p: None
    check(not m._vouch_agrees("q", [hit]), "V3 no first claim fails closed")
    def boom(p):
        raise RuntimeError
    m._semantic_top = boom
    check(not m._vouch_agrees("q", [top]), "V3 an error fails closed")

    src = open(os.path.join(HERE, "math_sense.py"), encoding="utf-8").read()
    s1 = src.index("and _vouch_stage1(prompt):")
    refuse = src.index("    if not gate_ok and ctx_used is None:\n")
    order = src.index('_silence_note(data, "no_chosen_after_order")')
    s2 = src.index('_silence_note(data, "vouch_disagree")')
    seal = src.index('_silence_note(data, "vouch_identity_lost")')
    coh = src.index("        if block and COH_CUT and len(emitted_ids) > 1:")
    check(s1 < refuse and order < s2 < seal < coh, "V4 stages in order")
    check('MATH_USE_WEIGHT > 0 and not _VOUCH["route"]' in src, "V4 use reorder skips a vouched block")
    m = fresh({"MATHBRAIN_VOUCH_VOCAB": voc})
    m._VOUCH["route"], m._VOUCH["card"] = True, "x"
    m._reset_per_prompt_state()
    check(m._VOUCH["route"] is False and m._VOUCH["card"] is None, "V4 reset clears the route")

    # V5: the builder over a stand-in store
    nodes = [NS(title="Sunflower lemma", body="an exponential bound"), NS(title="Sunflower bound", body="exponential growth"),
             NS(title="Growth", body="growth matters")]

    class Store:
        @classmethod
        def open_read_only(cls, p):
            return cls()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def iter_nodes(self):
            return iter(nodes)

    fake = types.ModuleType("math_brain.store")
    fake.ResearchStore = Store
    saved = sys.modules.get("math_brain.store")
    sys.modules["math_brain.store"] = fake
    try:
        pf = os.path.join(tmp, "p.txt")
        with open(pf, "w", encoding="utf-8") as fh:
            for i in range(5):
                fh.write("tell me about growth today %d\n" % i)
            fh.write(json.dumps({"prompt": "exponential holidays"}) + "\n")
        sys.modules.pop("build_vouch_vocab", None)
        bv = importlib.import_module("build_vouch_vocab")
        import math_sense as M
        M._math_gate = lambda p: False
        d = bv.build("store.sqlite", pf)
        t = set(d["terms"])
        check({"sunflower", "exponential"} <= t and "growth" not in t and "lemma" not in t,
              "V5 builder keeps rare-in-prompts store words, drops common ones and single-card words")
        check(d["built_from"]["prompts_refused"] == 6 and d["built_from"]["store_nodes"] == 3, "V5 counts recorded")
    finally:
        if saved is None:
            sys.modules.pop("math_brain.store", None)
        else:
            sys.modules["math_brain.store"] = saved
    print("ALL PASS" if all(results) else "SOME FAILED")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
