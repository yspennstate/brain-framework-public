"""study_support_gate.py - GRA-03 from his 27 Sep audit, replayed offline: does a support-aware graph weight do better than
the min-max blend recall uses?

The finding: graph_lane.fuse min-max scales the graph list before the convex mix, so a graph preference built on almost
nothing moves the ranking as much as a strong one. The rows of the R7C run (every question recalled once with the lane's
body and graph list logged whole) let any fusion be replayed exactly as recall would show it: head + fuse(body, graph, w).

Registered before computing:
  S0  today's blend at weight w
  S1  peak: w x (1 - g5), g5 the graph list's fifth score after min-max (a list with one clear leader gets the weight)
  S3  ambiguity: w where the body's top two scores are within 5 percent (the words cannot decide), 0.2 w elsewhere
  weights 0.1, 0.2, 0.3, 0.4, the deployed mixture (mix 0.5); the weight for S1 and S3 is chosen on the reworded bank's
  TUNE half; the verdict is read on its TEST half and on his 56: adopt a gate only if it beats weight 0 by at least +3 net
  hit@5 on TEST (paired, sign test) and does not lose on his 56.

    python study_support_gate.py [ROWS.jsonl]
"""
import collections
import json
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.expanduser("~"), ".claude", "dreaming"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import graph_lane                                   # noqa: E402
from eval_graph_mixture import half, sign_p          # noqa: E402

ROWS = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser("~/bank_recall_keys_quarantine/graph_mixture_rows_20260927_1217.jsonl")
WEIGHTS = (0.1, 0.2, 0.3, 0.4)
SHOWN = 20


def head_of(row, in_body, in_graph):
    head = []
    for s in row["shown"]:
        if s in in_body or s in in_graph:
            break
        head.append(s)
    return head


def rank(order, gold):
    return next((i + 1 for i, s in enumerate(order[:SHOWN]) if s.lower() in gold), 0)


def support(variant, body, graph):
    if variant == "S1":
        g = sorted((v for _, v in graph), reverse=True)
        return 1.0 - (g[4] if len(g) > 4 else 0.0)
    if variant == "S3":
        b = sorted((v for _, v in body), reverse=True)
        if len(b) < 2 or b[0] <= 0:
            return 1.0
        return 1.0 if (b[0] - b[1]) / b[0] <= 0.05 else 0.2
    return 1.0


def main():
    rows = []
    with open(ROWS, encoding="utf-8") as fh:
        for ln in fh:
            r = json.loads(ln)
            if float(r.get("mix", -1)) == 0.5 and r.get("replayable"):
                rows.append(r)
    res = collections.defaultdict(dict)       # (variant, weight) -> {qid: rank}
    groups = {}
    for r in rows:
        body = [(s, float(v)) for s, v in r["body"]]
        graph = [(s, float(v)) for s, v in r["graph"]]
        gold = {g.lower() for g in r["gold"]}
        head = head_of(r, {s for s, _ in body}, {s for s, _ in graph})
        q = r["key"]
        groups[q] = "his56" if r["kind"] == "his56" else "reworded_" + half(q)
        res[("W0", 0.0)][q] = rank(head + [s for s, _ in body], gold)
        for w in WEIGHTS:
            for var in ("S0", "S1", "S3"):
                we = w * support(var, body, graph)
                order = head + [s for s, _ in graph_lane.fuse(body, graph, we, "convex")]
                res[(var, w)][q] = rank(order, gold)
    base = res[("W0", 0.0)]

    def score(key, grp):
        qs = [q for q in base if groups[q] == grp]
        r = res[key]
        h5 = sum(1 for q in qs if 0 < r[q] <= 5)
        mrr = sum(1.0 / r[q] for q in qs if r[q]) / max(1, len(qs))
        up = sum(1 for q in qs if (0 < r[q] <= 5) and not (0 < base[q] <= 5))
        down = sum(1 for q in qs if not (0 < r[q] <= 5) and (0 < base[q] <= 5))
        return len(qs), h5, mrr, up, down

    print("rows replayed: %d (mix 0.5, replayable); groups: %s" % (len(rows), dict(collections.Counter(groups.values()))))
    for grp in ("reworded_tune", "reworded_test", "his56"):
        n, h5, mrr, _, _ = score(("W0", 0.0), grp)
        print("\n%s  n=%d   weight 0: hit@5 %d  MRR %.3f" % (grp, n, h5, mrr))
        for w in WEIGHTS:
            line = "  w %.1f" % w
            for var in ("S0", "S1", "S3"):
                n, h5, mrr, up, down = score((var, w), grp)
                line += "   %s hit@5 %3d (+%d -%d, p %.2f) MRR %.3f" % (var, h5, up, down, sign_p(up, down), mrr)
            print(line)
    # the registered choice: the weight each gate does best at on TUNE (net hit@5), read on TEST and his56
    print("\nregistered choice (weight picked on reworded_tune by net hit@5):")
    for var in ("S0", "S1", "S3"):
        best = max(WEIGHTS, key=lambda w: (score((var, w), "reworded_tune")[3] - score((var, w), "reworded_tune")[4], -w))
        _, _, _, up, down = score((var, best), "reworded_test")
        _, _, _, up56, down56 = score((var, best), "his56")
        ok = (up - down) >= 3 and sign_p(up, down) < 0.1 and (up56 - down56) >= 0
        print("  %s at w %.1f: TEST net %+d (+%d -%d, p %.2f); his56 net %+d (+%d -%d) -> %s" % (
            var, best, up - down, up, down, sign_p(up, down), up56 - down56, up56, down56, "ADOPT" if ok else "no"))


if __name__ == "__main__":
    sys.exit(main())
