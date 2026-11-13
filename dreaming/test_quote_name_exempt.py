"""Controls for MATHBRAIN_QUOTE_NAME_EXEMPT in the math hook's lead-card check.

 Q1 a lower-case quote whose words all sit in a result title names the card
 Q2 with MATHBRAIN_QUOTE_NAME_EXEMPT=0 the same quote does not
 Q3 a generic quote ("main theorem") does not name a card
 Q4 a title without a result word is never named, quoted or not

    python test_quote_name_exempt.py      (a few seconds; ALL PASS at the end)
"""
import sys
if __name__ == "__main__" and any(arg in ("-h", "--help") for arg in sys.argv[1:]):
    print(__doc__ or "No command-line options.")
    raise SystemExit(0)
import os
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
results = []


def check(ok, what):
    results.append(bool(ok))
    print(("PASS  " if ok else "FAIL  ") + what)


def named(prompt, title, off=False):
    env = dict(os.environ)
    env.pop("MATHBRAIN_QUOTE_NAME_EXEMPT", None)
    if off:
        env["MATHBRAIN_QUOTE_NAME_EXEMPT"] = "0"
    env["PYTHONPATH"] = os.pathsep.join([HERE] + [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p])
    probe = "import sys, math_sense as m; print(bool(m._lead_title_names_prompt(sys.argv[1], sys.argv[2])))"
    r = subprocess.run([sys.executable, "-c", probe, prompt, title], cwd=HERE, env=env, capture_output=True,
                       text=True, timeout=120)
    return ((r.stdout + r.stderr).strip().splitlines() or [""])[-1]


def main():
    p = 'how is the "dominated convergence theorem" used to swap a limit and an integral'
    title = "Dominated convergence theorem"
    check(named(p, title) == "True", "Q1 quoted name counts")
    check(named(p, title, off=True) == "False", "Q2 dial off: quoted name ignored")
    g = 'what does the "main theorem" give in the bounded case'
    check(named(g, "Main theorem on bounded operators") == "False", "Q3 generic quote does not count")
    check(named('the "gaussian process" here', "Gaussian process") == "False", "Q4 no result word, not named")
    print("ALL PASS" if all(results) else "SOME FAILED")
    return 0 if all(results) else 1


if __name__ == "__main__":
    if "-h" in __import__("sys").argv[1:] or "--help" in __import__("sys").argv[1:]:
        print((__doc__ or "").strip() or "usage: python %s" % __import__("os").path.basename(__file__))
        raise SystemExit(0)
    sys.exit(main())
