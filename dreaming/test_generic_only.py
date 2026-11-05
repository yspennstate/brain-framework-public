"""Controls for the generic-only silence (_generic_only, MATHBRAIN_GENERIC_ONLY).

 G1 a prompt made only of research and function words is generic: True for any cards
 G2 a prompt with a subject word is not generic when a chosen card carries that word, and is when none does
 G3 MATHBRAIN_GENERIC_ONLY=0 and an empty choice never silence
 G4 _main_body calls it after the selection, and never on a pinned citation

    python test_generic_only.py      (a few seconds; ALL PASS at the end)
"""
import importlib
import os
import sys
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


def card(i, title, body):
    return NS(node=NS(id=i, title=title, body=body))


def main():
    m = fresh({})
    cards = [card("a", "Open problem: sunflower bound", "The sunflower conjecture asks for an exponential bound."),
             card("b", "Hadwiger-Nelson problem", "Chromatic number of the plane.")]
    check(m._generic_only("how close are we to solving the open problem?", cards) is True, "G1 generic prompt")
    check(m._generic_only("is the sunflower conjecture solved?", cards) is False, "G2 subject word on a card")
    check(m._generic_only("is the Collatz conjecture solved?", cards) is True, "G2 subject word on no card")
    check(m._generic_only("how close are we to solving the open problem?", []) is False, "G3 empty choice")
    m = fresh({"MATHBRAIN_GENERIC_ONLY": "0"})
    check(m._generic_only("how close are we to solving the open problem?", cards) is False, "G3 switch off")

    src = open(os.path.join(HERE, "math_sense.py"), encoding="utf-8").read()
    i = src.index('_silence_note(data, "generic_only_match")')
    check("if chosen and not pinned_ids and _generic_only(prompt, chosen):" in src[i - 200:i]
          and src.index("no_chosen_after_select") > i, "G4 wired after selection, pinned citations exempt")
    print("ALL PASS" if all(results) else "SOME FAILED")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
