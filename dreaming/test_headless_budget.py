"""Controls for the math hook's headless-worker budget: a prompt opening with the worker marker waits longer.

 H1 an ordinary prompt keeps the 30 s budget and the 2 s integrity grace
 H2 a prompt opening with [headless-worker] gets the 50 s budget and the 8 s grace
 H3 an explicit MATHBRAIN_TIME_BUDGET_S wins over the headless budget
 H4 the marker counts only at the start of the prompt (after leading spaces), not inside it

    python test_headless_budget.py      (under a second; ALL PASS at the end)
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


def fresh(env):
    for k in [k for k in os.environ if k.startswith("MATHBRAIN_")]:
        del os.environ[k]
    os.environ.update(env)
    sys.modules.pop("math_sense", None)
    return importlib.import_module("math_sense")


def main():
    m = fresh({})
    m._headless_budget("What is the Lovasz local lemma?")
    check(m.TIME_BUDGET_S == 30.0 and not m._HEADLESS, "H1 ordinary prompt: budget %.0f s, headless %s"
          % (m.TIME_BUDGET_S, m._HEADLESS))

    m = fresh({})
    m._headless_budget("  [headless-worker] prove the bound")
    check(m.TIME_BUDGET_S == 50.0 and m._HEADLESS and m.HEADLESS_GRACE_S == 8.0,
          "H2 headless prompt: budget %.0f s, grace %.0f s" % (m.TIME_BUDGET_S, m.HEADLESS_GRACE_S))

    m = fresh({"MATHBRAIN_TIME_BUDGET_S": "300"})
    m._headless_budget("[headless-worker] prove the bound")
    check(m.TIME_BUDGET_S == 300.0, "H3 explicit budget wins: %.0f s" % m.TIME_BUDGET_S)

    m = fresh({})
    m._headless_budget("please run this as a [headless-worker] task")
    check(m.TIME_BUDGET_S == 30.0 and not m._HEADLESS, "H4 marker inside the prompt does not count: %.0f s"
          % m.TIME_BUDGET_S)

    print("ALL PASS" if all(results) else "SOME FAILED")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
