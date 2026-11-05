"""Controls for the printout's walk pointer and matched-terms hint (_matched_terms, _finish_block).

 M1 _matched_terms reads lexical trace lines only, strongest first, capped at k; junk gives []
 M2 _finish_block swaps the note pointer for the card-walk pointer and appends "matched: ..." to each card's hint line
 M3 MATHBRAIN_MATCHED_TERMS=0 and MATHBRAIN_RECALL_POINTER=0 leave the block byte for byte
 M4 the hint is applied in _main_body after the empty-block check and before the block is emitted

    python test_matched_terms.py      (under a second; ALL PASS at the end)
"""
import ast
import importlib
import os
import sys
from types import SimpleNamespace as NS

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


def fresh(env):
    for k in [k for k in os.environ if k.startswith("MATHBRAIN_")]:
        del os.environ[k]
    os.environ.update(env)
    sys.modules.pop("math_sense", None)
    return importlib.import_module("math_sense")


def tr(channel, detail, amount):
    return NS(channel=NS(value=channel), detail=detail, amount=amount)


def main():
    m = fresh({})
    hit = NS(trace=[tr("lexical", "matched normalized term 'martingale'", 2.0),
                    tr("lexical", "matched title phrase 'optional stopping'", 3.5),
                    tr("lexical", "matched normalized term 'martingale'", 1.0),
                    tr("semantic", "matched normalized term 'ignored'", 9.0),
                    tr("lexical", "something else", 5.0),
                    tr("lexical", "matched normalized term 'time'", 0.5)])
    got = m._matched_terms(hit, 2)
    junk = m._matched_terms(NS(trace=None), 3) == [] and m._matched_terms(object(), 3) == [] \
        and m._matched_terms(hit, 0) == []
    check(got == ["optional stopping", "martingale"] and junk, "M1 lexical only, strongest first, capped: %r" % got)

    block = ("- A title [theorem]: text\n    (card_a | verified)\n"
             "- B title [concept]: text\n    (card_b | draft)\n"
             "Full note: python ~/.claude/dreaming/math_note.py SOURCE_ID")
    hits = {"card_a": hit, "card_b": NS(trace=[])}
    out = m._finish_block(block, ["card_a", "card_b"], hits)
    ok2 = ("(card_a | verified | matched: optional stopping, martingale, time)\n" in out
           and "(card_b | draft)\n" in out
           and "math_recall.py --id ID" in out and "math_note.py SOURCE_ID" not in out)
    check(ok2, "M2 walk pointer and matched line")

    m = fresh({"MATHBRAIN_MATCHED_TERMS": "0", "MATHBRAIN_RECALL_POINTER": "0"})
    check(m._finish_block(block, ["card_a", "card_b"], hits) == block, "M3 both switched off leaves the block unchanged")

    src = open(os.path.join(HERE, "math_sense.py"), encoding="utf-8").read()
    body = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "_main_body")
    seg = lambda n: ast.get_source_segment(src, n) or ""
    fin = [n.lineno for n in ast.walk(body) if isinstance(n, ast.Call) and seg(n.func) == "_finish_block"]
    emit = [n.lineno for n in ast.walk(body) if isinstance(n, ast.Call) and seg(n.func) == "_emit"]
    empty = [n.lineno for n in ast.walk(body) if isinstance(n, ast.Constant) and n.value == "empty_block:%d_pieces"]
    ok4 = len(fin) == 1 and empty and emit and max(empty) < fin[0] < max(emit)
    check(ok4, "M4 applied after the empty-block check, before the emit")
    print("ALL PASS" if all(results) else "SOME FAILED")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
