"""Controls for the per-prompt reset (_reset_per_prompt_state).

 R1 after one prompt's state is set (agreed cards, semantic winner, pool links), the reset clears all of it
 R2 _main_body calls the reset first, before it reads the prompt

    python test_reset_state.py      (under a second; ALL PASS at the end)
"""
import ast
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


def main():
    sys.modules.pop("math_sense", None)
    m = importlib.import_module("math_sense")
    m._COV_AGREED["q"] = "card_a"
    m._SEMTOP["q"] = "card_b"
    m._SEMTOP_WHY["q"] = "winner"
    m._set_pool_adjacency([NS(source_id="a", target_id="b", kind=NS(value="depends_on"))])
    before = bool(m._linked_to("a", "b") and m._COV_AGREED and m._SEMTOP)
    m._reset_per_prompt_state()
    check(before and not m._COV_AGREED and not m._SEMTOP and not m._SEMTOP_WHY and not m._linked_to("a", "b"),
          "R1 the reset clears agreed cards, the semantic winner and the pool links")

    src = open(os.path.join(HERE, "math_sense.py"), encoding="utf-8").read()
    body = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "_main_body")
    calls = [n for n in body.body if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
             and getattr(n.value.func, "id", "") == "_reset_per_prompt_state"]
    first_prompt = min(n.lineno for n in ast.walk(body) if isinstance(n, ast.Constant) and n.value == "prompt")
    check(len(calls) == 1 and calls[0].lineno < first_prompt, "R2 _main_body resets before it reads the prompt")
    print("ALL PASS" if all(results) else "SOME FAILED")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
