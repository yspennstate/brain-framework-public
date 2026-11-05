"""Build the vouch vocabulary math_sense reads (math_vouch_vocab.json).

A word enters the vocabulary when it has five or more letters, appears in at least --min-store-df current store cards,
and appears in at most --max-neg-df of your own prompts that the hook's entry gate refuses. The last condition is what
keeps everyday words out: without a prompt file every store word qualifies and the first stage of the route is weak.

    python build_vouch_vocab.py --store PATH [--prompts FILE] [--out math_vouch_vocab.json]

FILE holds one prompt per line, or JSON lines with a "prompt" or "text" field.
"""
import argparse
import collections
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def _prompts(path):
    with io.open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            if line.startswith("{"):
                try:
                    rec = json.loads(line)
                    line = str(rec.get("prompt") or rec.get("text") or "")
                except Exception:
                    pass
            if line:
                yield line


def build(store_path, prompts_path=None, min_store_df=2, max_neg_df=3, min_len=5):
    os.environ["MATHBRAIN_STORE"] = str(store_path)
    import math_sense as M
    from math_brain.store import ResearchStore
    sdf = collections.Counter()
    n_nodes = 0
    with ResearchStore.open_read_only(str(store_path)) as store:
        for node in store.iter_nodes():
            n_nodes += 1
            sdf.update(M._vouch_words((node.title or "") + " " + (node.body or "")))
    ndf = collections.Counter()
    refused = 0
    if prompts_path:
        for p in _prompts(prompts_path):
            if len(p) < 12 or not M._math_gate(p):
                refused += 1
                ndf.update(M._vouch_words(p))
    terms = sorted(w for w, c in sdf.items() if c >= min_store_df and len(w) >= min_len and ndf.get(w, 0) <= max_neg_df)
    return {"terms": terms, "built_from": {"store_nodes": n_nodes, "prompts_refused": refused, "min_store_df": min_store_df,
                                           "max_neg_df": max_neg_df, "min_len": min_len}}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--store", required=True)
    ap.add_argument("--prompts")
    ap.add_argument("--out", default=os.path.join(HERE, "math_vouch_vocab.json"))
    ap.add_argument("--min-store-df", type=int, default=2)
    ap.add_argument("--max-neg-df", type=int, default=3)
    ap.add_argument("--min-len", type=int, default=5)
    a = ap.parse_args(argv)
    d = build(a.store, a.prompts, a.min_store_df, a.max_neg_df, a.min_len)
    if not a.prompts:
        print("no --prompts file: every store word qualifies; the vocabulary will be broad", file=sys.stderr)
    tmp = a.out + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as fh:
        json.dump(d, fh, indent=0)
    os.replace(tmp, a.out)
    print("%d terms from %d cards, %d refused prompts -> %s" % (len(d["terms"]), d["built_from"]["store_nodes"],
                                                                 d["built_from"]["prompts_refused"], a.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
