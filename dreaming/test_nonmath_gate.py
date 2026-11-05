"""Controls for the long non-mathematical text gate and the store-wide df table reader.

 N1 a long prose text with few anchor words and a vocabulary the store does not know is silenced, the note names it
 N2 the same text, its words known to three or more cards each: the vocabulary share keeps it open
 N3 a long text dense in anchor words stays open whatever the vocabulary
 N4 a text under 150 words, MATHBRAIN_NONMATH_GATE=0 and a store with no <store>.lexdf.pkl are never judged
 N5 _lexdf: a failed read is not kept, so a table written later is read on the next call; a good read is kept
 N6 the wiring in _main_body: the gate runs after the library import and before retrieve, and returns on a silence

    python test_nonmath_gate.py      (a second or two; ALL PASS at the end)
"""
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


WORDS = ("the meeting moved to the afternoon because the train arrived late and everyone wanted coffee before "
         "starting so the agenda slipped while people chatted about weekend plans holidays gardens and recipes").split()
PROSE = " ".join(WORDS[i % len(WORDS)] for i in range(220))


def fresh(env, df=None):
    d = tempfile.mkdtemp()
    store = os.path.join(d, "store.sqlite3")
    if df is not None:
        with open(store + ".lexdf.pkl", "wb") as fh:
            pickle.dump({"n": 10, "df": df}, fh)
    for k in [k for k in os.environ if k.startswith("MATHBRAIN_")]:
        del os.environ[k]
    os.environ.update(env)
    os.environ["MATHBRAIN_STORE"] = store
    sys.modules.pop("math_sense", None)
    m = importlib.import_module("math_sense")
    m.STORE_PATH = store
    notes = []
    m._silence_note = lambda data, reason, harness_only=False: notes.append(reason)
    return m, notes, store


def density(m, text):
    words = len(m._NONMATH_WORD.findall(m._NONMATH_LATEX.sub(" ", text)))
    fams = [m.STRONG_ANCHORS, m.STRONG_ANCHORS_2, m.MATH_ANCHORS, m.EXTRA_ANCHORS, m.GREEK_ANCHORS, m.WEAK_ANCHORS_2]
    if m.ANCHORS_3_ON:
        fams.append(m.STRONG_ANCHORS_3)
    return sum(len(rx.findall(text)) for rx in fams) / float(words)


def main():
    other = {"hilbert": 9, "banach": 9, "eigenvalue": 9}
    m, notes, _ = fresh({}, other)
    d0 = density(m, PROSE)
    out = m._nonmath_text(PROSE, {})
    check(d0 < m.NONMATH_DENSITY and out is True and len(notes) == 1 and notes[0].startswith("nonmath_text:w="),
          "N1 prose density %.4f silenced %r note %r" % (d0, out, notes))

    import math_brain.retrieval as R
    known = dict(other)
    known.update({t: 5 for t in R._content_terms(R.index_signals(PROSE).lexical_tokens)})
    m, notes, _ = fresh({}, known)
    check(m._nonmath_text(PROSE, {}) is False and notes == [], "N2 known vocabulary stays open, notes %r" % notes)

    m, notes, _ = fresh({}, other)
    dense = PROSE + " " + " ".join(["theorem lemma proof corollary eigenvalue integral"] * 6)
    dd = density(m, dense)
    check(dd >= m.NONMATH_DENSITY and m._nonmath_text(dense, {}) is False and notes == [],
          "N3 anchor density %.4f stays open, notes %r" % (dd, notes))

    m, n1, _ = fresh({}, other)
    short = m._nonmath_text(" ".join(WORDS[:40] * 3), {})
    m0, n2, _ = fresh({"MATHBRAIN_NONMATH_GATE": "0"}, other)
    off = m0._nonmath_text(PROSE, {})
    mt, n3, _ = fresh({}, None)
    notable = mt._nonmath_text(PROSE, {})
    check(short is False and off is False and notable is False and n1 == n2 == n3 == [],
          "N4 short %r off %r no table %r" % (short, off, notable))

    m, notes, store = fresh({}, None)
    first = m._lexdf()
    with open(store + ".lexdf.pkl", "wb") as fh:
        pickle.dump({"n": 7, "df": {"zeta": 3}}, fh)
    second = m._lexdf()
    os.remove(store + ".lexdf.pkl")
    third = (m._store_df(), m._store_n())
    check(first == ({}, 0) and second == ({"zeta": 3}, 7) and third == ({"zeta": 3}, 7),
          "N5 lexdf before %r after %r kept %r" % (first, second, third))

    src = open(os.path.join(HERE, "math_sense.py"), encoding="utf-8").read()
    body = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "_main_body")
    seg = lambda n: ast.get_source_segment(src, n) or ""
    ifs = [n for n in ast.walk(body) if isinstance(n, ast.If) and seg(n.test).strip() == "_nonmath_text(prompt, data)"]
    imp = [n for n in ast.walk(body) if isinstance(n, ast.ImportFrom) and n.module == "math_brain.store"]
    ret = [n for n in ast.walk(body) if isinstance(n, ast.Call) and seg(n.func) == "retrieve"]
    ok = (len(ifs) == 1 and imp and ret and imp[0].lineno < ifs[0].lineno < min(r.lineno for r in ret)
          and any(isinstance(x, ast.Return) for x in ifs[0].body))
    check(ok, "N6 wiring gate %d after import %r before retrieve %r"
          % (len(ifs), bool(imp and ifs and imp[0].lineno < ifs[0].lineno),
             bool(ifs and ret and ifs[0].lineno < min(r.lineno for r in ret))))
    print("ALL PASS" if all(results) else "SOME FAILED")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
