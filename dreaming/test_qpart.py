"""Controls for the question-part floor (MATHBRAIN_GATE_QPART) and the lone-rescue margin (MATHBRAIN_QPART_SOLO).

 Q1 the dials: GATE_QPART on by default, off for 0/off; QPART_SOLO 1.5 by default, 0 turns the margin off
 Q2 the closing paragraph is what the part is scored on, and it is shorter than the prompt it came from
 Q3 the gate block scores the part on the prefilter, rescues only above the ratio, and drops a lone weak rescue

    python test_qpart.py      (a few seconds; ALL PASS at the end)
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
results = []


def check(ok, what):
    results.append(bool(ok))
    print(("PASS  " if ok else "FAIL  ") + what)


def dials(**env_over):
    env = dict(os.environ)
    for k in ("MATHBRAIN_GATE_QPART", "MATHBRAIN_QPART_SOLO"):
        env.pop(k, None)
    env.update(env_over)
    env["PYTHONPATH"] = os.pathsep.join([HERE] + [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p])
    r = subprocess.run([sys.executable, "-c", "import math_sense as m; print(int(m.GATE_QPART), m.QPART_SOLO)"],
                       cwd=HERE, env=env, capture_output=True, text=True, timeout=120)
    return ((r.stdout + r.stderr).strip().splitlines() or [""])[-1]


def main():
    d0, d1, d2 = dials(), dials(MATHBRAIN_GATE_QPART="0"), dials(MATHBRAIN_QPART_SOLO="0")
    check(d0 == "1 1.5" and d1.startswith("0 ") and d2 == "1 0.0", "Q1 dials: default=%r off=%r solo0=%r" % (d0, d1, d2))
    sys.path.insert(0, HERE)
    import math_sense as m
    prompt = ("Some background about the setting and earlier work.\n\n"
              "What is the exact constant in the second moment bound?")
    part = m._closing_paragraph(prompt) or m._prompt_tail(prompt, m.COH_TAIL or 250)
    check(bool(part) and len(part) < len(prompt) and "second moment" in part, "Q2 closing paragraph %r" % part)
    src = open(os.path.join(HERE, "math_sense.py"), encoding="utf-8").read()
    need = ["if GATE_QPART and GATE_FRAC > 0:", "_qp_floor = min(GATE_FLOOR, GATE_FRAC * _qself)",
            "_raw = math_prefilter._scores(_qd, _qpart)", "sc >= GATE_RATIO * top",
            "if (QPART_SOLO > 0 and len(keep) == 1 and keep[0].node.id in _qp_resc",
            "< QPART_SOLO * (_qp_floor or 0.0)):"]
    missing = [n for n in need if n not in src]
    check(not missing, "Q3 gate block present (missing %s)" % missing)
    print("ALL PASS" if all(results) else "SOME FAILED")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
