#!/usr/bin/env python3
"""recall_or.py - ask brain.py recall several wordings of one question and fuse the rankings into one list.

    python3 recall_or.py "first wording" --or "second wording" [--or "third"] [-n 8]

Each wording runs through brain.py recall unchanged; the ranked lists are combined by reciprocal-rank fusion
(score = sum over wordings of 1/(60 + rank)). Measured on the Mac mini on 29 Oct over codex worker T1's 120-question
bank: hit@5 96/120 for one wording, 106 for the other, 111 reading both top-5 lists, 112 for the fused top 5
(memory two_wordings_fused_by_rank_beat_one_recall_query_112_vs_96_of_120_2026_10_29). It changes nothing in
brain.py; each call costs one recall per wording.
"""
import argparse
import os
import re
import subprocess
import sys

BRAIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "brain.py")
RANKED = re.compile(r"^\s+\d\.\d\d \[[^\]]*\] (\S+)")


def ranked(question, n):
    out = subprocess.run([sys.executable, "-s", BRAIN, "recall", question, "-n", str(n)],
                         capture_output=True, text=True, timeout=900).stdout
    lines, slugs = {}, []
    for ln in out.splitlines():
        m = RANKED.match(ln)
        if m and m.group(1) not in lines:
            lines[m.group(1)] = ln.strip()
            slugs.append(m.group(1))
    return slugs, lines


def main():
    ap = argparse.ArgumentParser(description="Fuse brain.py recall over several wordings (RRF, k=60).")
    ap.add_argument("question")
    ap.add_argument("--or", dest="more", action="append", default=[], help="another wording of the same question")
    ap.add_argument("-n", type=int, default=8, help="results to print (each wording is asked for 2n)")
    ap.add_argument("-k", type=int, default=60, help="RRF constant")
    a = ap.parse_args()
    score, shown = {}, {}
    for q in [a.question] + a.more:
        slugs, lines = ranked(q, max(10, 2 * a.n))
        for rank, s in enumerate(slugs):
            score[s] = score.get(s, 0.0) + 1.0 / (a.k + rank + 1)
            shown.setdefault(s, lines[s])
    fused = sorted(score, key=lambda s: -score[s])[:a.n]
    print("fused recall over %d wording(s), RRF k=%d" % (1 + len(a.more), a.k))
    for i, s in enumerate(fused, 1):
        print("%2d. %.4f  %s" % (i, score[s], shown[s][:160]))


if __name__ == "__main__":
    main()
