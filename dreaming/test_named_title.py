"""Controls for the math hook's named-result sub-queries: a cited name keeps the card it names.

 N1 _named_title_match: accents fold, inequality/estimate/bound are one kind, a one-word name is a whole word, and a
    different kind or a different name does not match
 N2 MATHBRAIN_NAMED_ACCENT_FOLD=0 leaves accents as written
 N3 _title_names_only: a title whose head names the citation and nothing more passes; a namesake with more words does not
 N4 _title_exact_first(parts=True): a hyphenated name counts by its parts, a kind synonym counts as the kind, a lone
    eponym before "theorem" is looked for with the kind word; parts=False keeps the old rule
 N5 the possessive form and the plain name: the fallback query carries both
 N6 MATHBRAIN_NAMED_TITLEMATCH=0 switches the filter off

    python test_named_title.py      (under a second; ALL PASS at the end)
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
    def __init__(self, nid, title):
        self.id, self.title, self.body, self.kind, self.metadata = nid, title, "", "claim", {}


class Hit:
    def __init__(self, nid, title):
        self.node = Node(nid, title)


def fresh(env):
    for k in [k for k in os.environ if k.startswith("MATHBRAIN_")]:
        del os.environ[k]
    os.environ.update(env)
    sys.modules.pop("math_sense", None)
    return importlib.import_module("math_sense")


def ids(hits):
    return [h.node.id for h in hits]


def main():
    m = fresh({})
    ntm = m._named_title_match
    check(ntm("Hölder's inequality", "Holder inequality for sums")
          and ntm("Caccioppoli estimate", "Caccioppoli Inequality")
          and ntm("Fatou", "Fatou's lemma")
          and not ntm("Fatou", "Fatoux")
          and not ntm("Weyl inequality", "Weyl's law for the Laplacian")
          and not ntm("Lax-Milgram theorem", "Babuska-Brezzi theorem"),
          "N1 named title match")
    m0 = fresh({"MATHBRAIN_NAMED_ACCENT_FOLD": "0"})
    check(not m0._named_title_match("Hölder inequality", "Holder inequality") and m0.NAMED_ACCENT_FOLD is False,
          "N2 accent fold off")
    m = fresh({})
    check(m._title_names_only("Aubin–Lions lemma", "Aubin-Lions lemma: compactness")
          and not m._title_names_only("F. Riesz's theorem", "Riesz representation theorem for Hilbert spaces")
          and not m._title_names_only("", "Anything"),
          "N3 title names only")
    pool = [Hit("a", "Compactness in Bochner spaces"), Hit("b", "Aubin-Lions compactness lemma")]
    kind = [Hit("c", "Energy methods"), Hit("d", "Caccioppoli Inequality")]
    ep = [Hit("e", "Fubini and Tonelli hypotheses"), Hit("f", "Tonelli theorem")]
    got = (ids(m._title_exact_first("Aubin-Lions lemma", pool, parts=True)),
           ids(m._title_exact_first("Aubin-Lions lemma", pool)),
           ids(m._title_exact_first("Caccioppoli estimate", kind, parts=True)),
           ids(m._title_exact_first("Tonelli's theorem", ep, parts=True)))
    check(got == (["b"], ["a", "b"], ["d"], ["f"]), "N4 exact first %r" % (got,))
    q = "Plancherel's theorem"
    plain = m._POSSESSIVE_RE.sub(lambda x: x.group(1) + x.group(2) + "s " + x.group(1), q)
    check(m._POSSESSIVE_RE.search(q) is not None and "Plancherel theorem" in plain and "Plancherel's" in plain,
          "N5 possessive fallback %r" % plain)
    off = fresh({"MATHBRAIN_NAMED_TITLEMATCH": "0"})
    check(off.NAMED_TITLEMATCH is False and m.NAMED_TITLEMATCH is True, "N6 switch")
    print("ALL PASS" if all(results) else "SOME FAILED")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
