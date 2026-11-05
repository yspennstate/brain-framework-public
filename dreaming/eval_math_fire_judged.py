"""Split eval_math_hookpath.py's false fires by how much harm they do.

eval_math_hookpath counts any block served on an "unanswerable" item as a false fire, and its strict count only
leaves out blocks that carry the target's stem. On 31 Oct the 17 strict fires at the live margin judged 8 on-topic
neighbours, 6 weak, 3 junk, so a gate tuned on the bare count pays for silencing useful neighbours. This re-scores any
hookpath output against hand judgments kept in state/math_fire_judgments.tsv.

A judgment belongs to what was served, not to the question: labels are keyed by (item key, the served claim ids in
order). A fire whose cards nobody has judged is printed as unjudged with its titles, never given an old label.

    python eval_math_fire_judged.py state/hookpath_<tag>.jsonl [more.jsonl ...] [--labels FILE] [--show-unjudged]
    python eval_math_fire_judged.py --seed-from OLD.tsv RUN.jsonl [RUN2.jsonl ...]   # turn a key-level TSV into labels

Judgments: target_held (the store now holds the theorem asked for), on_topic, weak, junk.
"""
from __future__ import annotations

import argparse
import collections
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LABELS = os.path.join(HERE, "state", "math_fire_judgments.tsv")
KINDS = ("target_held", "on_topic", "weak", "junk")


def fires(path):
    for line in io.open(path, encoding="utf-8"):
        r = json.loads(line)
        if r.get("kind") == "unanswerable" and r.get("ids"):
            yield r


def load_labels(path):
    out = {}
    if os.path.exists(path):
        for line in io.open(path, encoding="utf-8"):
            p = line.rstrip("\n").split("\t")
            if len(p) >= 3 and p[0] != "key":
                out[(p[0], p[1])] = p[2]
    return out


def seed(old_tsv, runs, path):
    """Old TSV: key, margin, judgment, titles. A key's judgment goes to the card set it fired with in the FIRST of
    RUNS (in the order given) where it fired - pass the runs in the order the judgments were read. The same key with
    a different card set in a later run stays unjudged."""
    old = {}
    for line in io.open(old_tsv, encoding="utf-8"):
        p = line.rstrip("\n").split("\t")
        if len(p) >= 3 and p[0] != "key":
            old[p[0]] = p[2]
    have = load_labels(path)
    seeded = {k for k, _ in have}
    new = []
    for run in runs:
        for r in fires(run):
            k, ids = r["key"], ",".join(r["ids"])
            if k in seeded or k not in old:
                continue
            seeded.add(k)
            have[(k, ids)] = old[k]
            new.append((k, ids, old[k], " | ".join(t[:50] for t in r["titles"])))
    exists = os.path.exists(path)
    with io.open(path, "a", encoding="utf-8") as fh:
        if not exists:
            fh.write("key\tids\tjudgment\ttitles\n")
        for row in new:
            fh.write("\t".join(row) + "\n")
    print("labels added: %d (file %s)" % (len(new), path))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="+")
    ap.add_argument("--labels", default=LABELS)
    ap.add_argument("--show-unjudged", action="store_true")
    ap.add_argument("--seed-from", default=None)
    a = ap.parse_args()
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    if a.seed_from:
        seed(a.seed_from, a.runs, a.labels)
        return
    labels = load_labels(a.labels)
    for run in a.runs:
        c = collections.Counter()
        unj = []
        for r in fires(run):
            j = labels.get((r["key"], ",".join(r["ids"])), "unjudged")
            c[j] += 1
            if j == "unjudged":
                unj.append(r)
        n = sum(c.values())
        print("%s: fires %d  %s  harmful (weak+junk) %d  junk %d"
              % (os.path.basename(run), n, "  ".join("%s %d" % (k, c[k]) for k in KINDS + ("unjudged",)),
                 c["weak"] + c["junk"], c["junk"]))
        if a.show_unjudged:
            for r in unj:
                print("   unjudged %-20s %s" % (r["key"], " | ".join(t[:50] for t in r["titles"])))


if __name__ == "__main__":
    main()
