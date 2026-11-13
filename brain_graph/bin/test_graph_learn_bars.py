"""test_graph_learn_bars.py - the memory bars move once per batch of new evidence (27 Sep; the acceptance
test the audit of 27 Sep asked for: replaying unchanged evidence must not move the threshold or the policy).

    python test_graph_learn_bars.py [PATH_TO_graph_learn.py]
"""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.join(HERE, "graph_learn.py")
sys.path.insert(0, os.path.dirname(PATH))   # its own imports
spec = importlib.util.spec_from_file_location("graph_learn_under_test", PATH)
G = importlib.util.module_from_spec(spec)
spec.loader.exec_module(G)
FAILS = []


def run(name, ok):
    print("%s  %s" % ("PASS" if ok else "FAIL", name))
    if not ok:
        FAILS.append(name)


def judged(n, taken, t0=1000.0):
    return [{"t": t0 + i, "lines": [["mem", "m%d" % i, i < taken, ["x"]]]} for i in range(n)]


# 1. twenty failed lines, replayed 40 times: one move, then none (the audit's run went 55 -> 5000 in 33)
pol = {"mem_hebb_min": 55.0, "mem_size_min": 1.89}
j = judged(20, 0)
notes = [G.update_mem_bars(pol, j, 0.05) for _ in range(40)]
run("1 unchanged evidence moves the bar once: %s after 40 runs" % pol["mem_hebb_min"],
    pol["mem_hebb_min"] == 63.25 and sum(1 for x in notes if x) == 1 and pol["mem_size_min"] == 1.91)
# 2. twenty NEW failed lines move it again, once
j2 = j + judged(20, 0, t0=5000.0)
notes = [G.update_mem_bars(pol, j2, 0.05) for _ in range(5)]
run("2 a new batch moves it once more: %s" % pol["mem_hebb_min"],
    pol["mem_hebb_min"] == round(63.25 * 1.15, 2) and sum(1 for x in notes if x) == 1)
# 3. fewer than twenty new lines: no move
j3 = j2 + judged(19, 0, t0=9000.0)
run("3 nineteen new lines move nothing", G.update_mem_bars(pol, j3, 0.05) == "")
# 4. the down branch consumes its evidence the same way
pol2 = {"mem_hebb_min": 100.0, "mem_size_min": 2.0}
jd = judged(20, 20)
notes = [G.update_mem_bars(pol2, jd, 0.05) for _ in range(10)]
run("4 good evidence lowers the bar once: %s" % pol2["mem_hebb_min"],
    pol2["mem_hebb_min"] == 90.0 and sum(1 for x in notes if x) == 1)
# 5. the neutral band moves nothing and consumes nothing
pol3 = {"mem_hebb_min": 100.0, "mem_size_min": 2.0}
jn = judged(20, 6)                                  # 30 percent taken up, between 25 and 40
run("5 the neutral band moves nothing", G.update_mem_bars(pol3, jn, 0.05) == "" and "mem_bar_evidence_until" not in pol3)
print("%d of 5 controls pass (%s)" % (5 - len(FAILS), PATH))
sys.exit(1 if FAILS else 0)
