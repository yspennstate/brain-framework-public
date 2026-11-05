"""Controls for the math hook's origin hint ("ours, unrefereed") and a revision's declared status.

 O1 a house card (house provenance, "(our id ...)", "Attempt on ...", a research.* deposit) is tagged
 O2 literature is not: a harvested card, a typed card with origin literature even under a house provenance, an
    encyclopedia card an agent repaired
 O3 MATHBRAIN_HOUSE_PROVENANCE adds a prefix; MATHBRAIN_ORIGIN_HINT=0 switches the tag off
 D1 a declared status shows in the trust tag; a declared "unverified" adds nothing; the closing word stays "unverified"

    python test_origin_hint.py      (under a second; ALL PASS at the end)
"""
import importlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
results = []


def check(ok, what):
    results.append(ok)
    print(("PASS  " if ok else "FAIL  ") + what)


class Node:
    def __init__(self, title, **meta):
        self.id, self.title, self.body, self.kind, self.metadata = title, title, "", "claim", meta


def fresh(env):
    for k in [k for k in os.environ if k.startswith("MATHBRAIN_")]:
        del os.environ[k]
    os.environ.update(env)
    sys.modules.pop("math_sense", None)
    return importlib.import_module("math_sense")


def tag(m, node):
    return m._origin_prefix(node, m._node_meta(node))


def main():
    m = fresh({})
    house = [Node("A scar", provenance="scar layer: mixing time"),
             Node("Bound (our id T12)"),
             Node("Attempt on the capacity problem"),
             Node("Lemma 3", logical_id="research.fpp:lemma3"),
             Node("Fix", provenance="T6 correction of an earlier card")]
    check(all(tag(m, n) == "ours, unrefereed; " for n in house), "O1 house cards tagged: %r" % [tag(m, n) for n in house])
    lit = [Node("Hoeffding", logical_id="math:harvest.arxiv.math.PR:1", provenance="scar layer"),
           Node("Erdos-Sos", origin="literature", provenance="typed from a memory"),
           Node("Gamma function", logical_id="harvest.wikipedia:gamma", provenance="formula repair (agent-1)"),
           Node("Plain theorem", logical_id="math:analysis:plain")]
    check(all(tag(m, n) == "" for n in lit), "O2 literature untagged: %r" % [tag(m, n) for n in lit])
    m = fresh({"MATHBRAIN_HOUSE_PROVENANCE": "lab note"})
    on = tag(m, Node("Note", provenance="Lab note 4"))
    m = fresh({"MATHBRAIN_ORIGIN_HINT": "0"})
    off = tag(m, house[0])
    check(on == "ours, unrefereed; " and off == "", "O3 extra prefix %r, switch off %r" % (on, off))
    m = fresh({})
    a = m._trust_tag({"claim_kind": "theorem", "declared_status": "retracted"})
    b = m._trust_tag({"claim_kind": "theorem", "declared_status": "unverified"})
    check("declared retracted" in a and a.endswith("unverified") and "declared" not in b,
          "D1 declared status: %r / %r" % (a, b))
    print("ALL PASS" if all(results) else "SOME FAILED")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
