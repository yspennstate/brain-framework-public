#!/usr/bin/env python3
"""eval_recall_signals.py - which recall signal actually predicts what the brain reaches for next?

spread_recall blends signals (Hebbian co-read graph, wiki-links, salience, and - when a sidecar exists -
embedding cosine) to decide what to surface. Nothing measured which of them predicts REAL future use, so
the weighting is a guess. This is the causal test that answers it, and the honest reason it's causal:
a recall system is validated the way a recommender is - train on behaviour BEFORE a split time T, predict
behaviour AFTER T. Past co-reads predicting future co-reads is not circular (train and test are disjoint
in time); it's exactly the prediction the graph is for.

Method: split the retrieval trace (build_retrieval_trace.py) at the 70th-pct event. From co-reads BEFORE
T, score each candidate memory-pair by (a) past co-read count (the Hebbian graph) and (b) content-embedding
cosine (the sidecar, if present). Ground truth = the pair is co-read AFTER T. Report AUROC per signal and
for a simple blend - which predictor, and does the embedding ADD anything over the graph causally (the
question the LSA revert left open: it was weak cross-sectionally, but does it help at the margin?).

Honest about power: the trace is young; with few future co-read pairs the AUROCs won't be distinguishable,
and it says so. It's re-runnable and the trace now rebuilds nightly, so this eval matures into a real
tuning instrument - build the stable held-out eval before you tune anything. Read-only.
Usage:  python eval_recall_signals.py
"""
import os, io, sys, itertools, random
from collections import defaultdict
import embedding_artifact

DR = os.path.dirname(os.path.abspath(__file__))
TRACE = os.path.join(DR, "retrievals.tsv")
EMB = os.path.join(DR, "memory_embeddings.npz")                       # live sidecar (box), if present
EMB_AUTHORITY = os.path.join(DR, ".embedding_current.json")
MEM = os.path.expanduser(r"~/.claude/projects/{{BRAIN_SLUG}}/memory")
MAXSESS = 25          # skip giant survey sessions - their C(n,2) pairs are co-read noise, not association


def _auroc(pos, neg, rng):
    if not pos or not neg:
        return None
    pp = pos if len(pos) <= 400 else rng.sample(pos, 400)
    nn = neg if len(neg) <= 400 else rng.sample(neg, 400)
    return sum((1.0 if a > b else 0.5 if a == b else 0.0) for a in pp for b in nn) / (len(pp) * len(nn))


def main():
    if not os.path.exists(TRACE):
        print("eval_recall_signals: no retrievals.tsv - run build_retrieval_trace.py first.")
        return
    events = []
    for i, ln in enumerate(io.open(TRACE, encoding="utf-8", errors="replace")):
        if i == 0:
            continue
        p = ln.rstrip("\r\n").split("\t")
        if len(p) >= 4:
            try:
                events.append((float(p[1]), p[0], p[3]))    # (epoch, slug, session)
            except Exception:
                pass
    if len(events) < 30:
        print(f"eval_recall_signals: trace too thin ({len(events)} events) to split causally yet.")
        return
    events.sort()
    T = events[int(0.70 * len(events))][0]

    def coreads(evs):
        by = defaultdict(set)
        for ep, slug, sess in evs:
            by[sess].add(slug)
        pairs = defaultdict(int)
        for slugs in by.values():
            if 2 <= len(slugs) <= MAXSESS:
                for a, b in itertools.combinations(sorted(slugs), 2):
                    pairs[(a, b)] += 1
        return pairs

    past = coreads([e for e in events if e[0] < T])          # train: co-reads before T
    future = set(coreads([e for e in events if e[0] >= T]))  # test: pairs co-read after T
    if len(future) < 10:
        print(f"eval_recall_signals: only {len(future)} future co-read pairs after the split - "
              f"too few to judge (the trace is young; re-run as it grows).")
        return

    # candidate universe: pairs among memories seen before T (what a recall system could have ranked at T)
    mems = sorted({e[1] for e in events if e[0] < T})    # memories seen before T = what recall could rank at T
    # This is the decisive production BGE gate. An explicit LSA diagnostic belongs in a separate
    # harness; silently substituting it here would certify a different arm.
    loaded = embedding_artifact.load_verified_embeddings(
        EMB, EMB_AUTHORITY, MEM, "BAAI/bge-small-en-v1.5", verify_all_rows=True
    )
    if loaded is None or not loaded[0]:
        raise SystemExit("eval_recall_signals: verified active BGE authority is required")
    emb_idx, emb_mat, _ = loaded
    _, receipt = embedding_artifact.resolve_authority_sidecar(EMB, EMB_AUTHORITY)
    emb_label = "authority:%s" % receipt["sidecar_sha256"][:12]
    print("embedding_generation=%s" % emb_label)

    def emb_cos(a, b):
        if emb_idx is not None and a in emb_idx and b in emb_idx:
            return float(emb_mat[emb_idx[a]] @ emb_mat[emb_idx[b]])
        return None

    # build labelled candidate pairs: positives = future co-reads whose members were both active before T
    active = set(mems)
    pos_pairs = [p for p in future if p[0] in active and p[1] in active]
    if len(pos_pairs) < 10:
        print(f"eval_recall_signals: only {len(pos_pairs)} future pairs have both members active pre-split - too few.")
        return
    rng = random.Random(11)
    # negatives: random active pairs not co-read in the future
    neg_pairs, tries = [], 0
    fset = set(future)
    ml = sorted(active)
    while len(neg_pairs) < len(pos_pairs) and tries < 40000:
        a, b = sorted(rng.sample(ml, 2))
        tries += 1
        if (a, b) not in fset:
            neg_pairs.append((a, b))

    g_pos = [past.get(p, 0) for p in pos_pairs]
    g_neg = [past.get(p, 0) for p in neg_pairs]
    auc_graph = _auroc(g_pos, g_neg, rng)

    print(f"eval_recall_signals (causal, split at 70th-pct event; {len(pos_pairs)} future co-read pairs "
          f"vs {len(neg_pairs)} random):")
    print(f"  AUROC  past-co-read graph (Hebbian) -> future co-read : "
          f"{auc_graph:.3f}" if auc_graph is not None else "  graph: n/a")
    if emb_idx:
        e_pos = [c for p in pos_pairs if (c := emb_cos(*p)) is not None]
        e_neg = [c for p in neg_pairs if (c := emb_cos(*p)) is not None]
        auc_emb = _auroc(e_pos, e_neg, rng)
        # simple blend: rank by graph, break ties / add with a small embedding term
        b_pos = [past.get(p, 0) + 0.5 * (emb_cos(*p) or 0) for p in pos_pairs]
        b_neg = [past.get(p, 0) + 0.5 * (emb_cos(*p) or 0) for p in neg_pairs]
        auc_blend = _auroc(b_pos, b_neg, rng)
        print(f"  AUROC  content embedding ({emb_label}) -> future co-read  : "
              f"{auc_emb:.3f}" if auc_emb is not None else "  embedding: n/a")
        print(f"  AUROC  graph + 0.5*embedding blend                   : "
              f"{auc_blend:.3f}" if auc_blend is not None else "  blend: n/a")
        if auc_graph is not None and auc_emb is not None and auc_blend is not None:
            adds = auc_blend > auc_graph + 0.02
            print(f"  -> does the embedding ADD over the graph causally? {'YES, marginally' if adds else 'NO'} "
                  f"(blend {auc_blend:.3f} vs graph {auc_graph:.3f}). "
                  f"{'thin - provisional' if len(pos_pairs) < 25 else ''}")
    print(f"  NOTE: {len(pos_pairs)} test pairs is {'THIN - treat as provisional; re-run as the nightly trace grows' if len(pos_pairs) < 40 else 'a usable sample'}.")


if __name__ == "__main__":
    main()
