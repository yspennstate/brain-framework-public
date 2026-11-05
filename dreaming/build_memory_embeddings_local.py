#!/usr/bin/env python3
"""build_memory_embeddings_local.py - the LAPTOP-SAFE half of semantic recall (model-free LSA).

build_memory_embeddings.py is the good path: a real sentence model (all-MiniLM), but it needs
torch/sentence-transformers and the RAM headroom the crash-prone laptop doesn't have - so it only runs
on the box, and until someone runs it there the recall re-rank is a no-op and the ~45% keyword ceiling
stands. This is the fallback that needs NOTHING but numpy (already here): classic Latent Semantic
Analysis - TF-IDF over word uni/bigrams, then a randomized truncated SVD to a few hundred dense dims.
LSA is the pre-neural "semantic" embedding: the SVD folds co-occurrence structure in, so two memories
about the same object land near each other even when they don't share the exact words that pure token
overlap (spread_recall's lexical seeds) needs. It is weaker than a real sentence model - the honest
question is whether it's strong ENOUGH to separate related from unrelated, which eval_semantic_recall.py
decides against fixed ground-truth pairs. Build, then EVAL, then keep only if it earns it.

This is now a diagnostic baseline only. It writes the legacy three-array LSA format to
memory_embeddings.local.next.npz; it lacks the v2 text hashes/schema/build timestamp and therefore
cannot become production authority. Direct output to memory_embeddings.npz is refused. Compare it in
an explicit offline eval, never install it as the runtime sidecar.

Rows are L2-normalized because the online half treats a candidate vector as unit-norm (it takes a raw
dot product against the unit seed-centroid). Pure numpy, CPU, seconds - no model, no GPU, no download.

Usage:   python build_memory_embeddings_local.py [memory_dir] [--dims 256] [--out PATH] [--limit N]
Refresh: same cadence as the box builder - re-run when the corpus grows ~10% or monthly. A memory added
         since the last run just isn't in the sidecar and spread_recall keeps its graph score for it.
"""
import os, io, sys, re, math
from collections import defaultdict

import build_memory_embeddings as builder

MEM_DEFAULT = os.path.expanduser(r"~/.claude/projects/{{BRAIN_SLUG}}/memory")
DR = os.path.dirname(os.path.abspath(__file__))
LIVE_OUT = os.path.join(DR, "memory_embeddings.npz")
OUT_DEFAULT = os.path.join(DR, "memory_embeddings.local.next.npz")
# the special pages aren't memories - identical skip set to the box builder so the two sidecars cover
# exactly the same slugs and are drop-in interchangeable.
SKIP = {"MEMORY", "INDEX_ARCHIVE", "PREDICTIONS", "STATE_OF_MIND", "OPEN_QUESTIONS", "AUTOBIOGRAPHY",
        "MODEL_OF_PARTNER", "FASCINATIONS", "THE_OTHER_MINDS", "MISTAKES", "CONSCIENCE", "CURIOSITY",
        "SALIENCE", "GROWTH", "RECENT_BRAIN", "STATS", "RECONSOLIDATION_QUEUE"}
FM = re.compile(r"^---\n.*?\n---\n", re.S)
# a compact stoplist - the highest-frequency english + this brain's own filler; keeps the vocab on
# content words. (The IDF term already down-weights ubiquitous words; this just trims the vocab.)
STOP = set(("the a an and or of to in for on with is are was were be been being this that these those it "
            "its as at by from into over under per not no nor new one two three use used using via etc "
            "when then than but our we you your yours i me my mine he she they them their what which who "
            "whom how why can could do does did done has have had having will would shall should may might "
            "must more most some any each all only just get got make made see saw seen look need want know "
            "now today about onto out up down off out so if too very much many also here there while both "
            "because about above below between through during before after again once own same other").split())
TOKEN = re.compile(r"[a-zA-Z][a-zA-Z0-9_]{1,}")


def _text_for(body):
    """Identical signal to the box builder: description line + prose, frontmatter stripped, first 2000
    chars. Keeping this in lockstep means the two embedding methods embed the same text - so a later
    box run is a clean apples-to-apples upgrade over this LSA baseline, not a different experiment."""
    desc = ""
    m = re.search(r'description:\s*"?(.+?)"?\s*\n', body)
    if m:
        desc = m.group(1).strip() + ". "
    prose = FM.sub("", body).strip()
    return (desc + prose)[:2000]


def _tokens(text):
    """Content-word unigrams + adjacent bigrams. Bigrams give LSA a little phrase structure ('grid sim',
    'prediction error') that lone words lose - cheap and it sharpens the topic signal."""
    words = [w.lower() for w in TOKEN.findall(text)]
    words = [w for w in words if w not in STOP and len(w) > 1]
    toks = list(words)
    toks.extend(words[i] + "_" + words[i + 1] for i in range(len(words) - 1))
    return toks


def _randomized_svd(X, k, n_oversamples=12, n_iter=3, seed=0):
    """Halko-Martinsson-Tropp randomized truncated SVD - the light way to get the top-k singular
    directions of a tall-thin doc x term matrix without the full (expensive) decomposition. Pure numpy,
    a few small matmuls; power iterations sharpen the estimate of the dominant subspace."""
    import numpy as np
    rng = np.random.default_rng(seed)          # fixed seed -> deterministic sidecar (re-runnable eval)
    n, m = X.shape
    r = min(k + n_oversamples, m, n)
    Q = rng.standard_normal((m, r)).astype(np.float32)
    Y = X @ Q                                   # [n, r]
    for _ in range(n_iter):                     # power iterations: (X X^T)^q Y
        Y = X @ (X.T @ Y)
    Q, _ = np.linalg.qr(Y)                       # orthonormal basis for the doc-space range, [n, r]
    B = Q.T @ X                                  # [r, m], small
    Ub, S, Vt = np.linalg.svd(B, full_matrices=False)
    U = Q @ Ub
    kk = min(k, U.shape[1])
    return U[:, :kk], S[:kk]


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # Discovery is not execution: this script writes, so a help request must
        # exit before any work. See script_help_flag_can_execute_work.
        print(__doc__ or os.path.basename(__file__))
        return
    import numpy as np
    args = sys.argv[1:]
    dims, out, limit = 256, OUT_DEFAULT, None
    flag_values = set()          # indices consumed as a flag's value, so they aren't read as the positional
    for i, a in enumerate(args):
        if a == "--dims" and i + 1 < len(args):
            flag_values.add(i + 1)
            try: dims = int(args[i + 1])
            except Exception: pass
        if a == "--out" and i + 1 < len(args):
            flag_values.add(i + 1)
            out = args[i + 1]
        if a == "--limit" and i + 1 < len(args):
            flag_values.add(i + 1)
            try: limit = int(args[i + 1])
            except Exception: pass
    mem = next((a for i, a in enumerate(args) if not a.startswith("--") and i not in flag_values), MEM_DEFAULT)
    if builder.same_file_target(out, LIVE_OUT):
        print("build_memory_embeddings_local: refusing direct live output; this legacy LSA artifact "
              "is diagnostic-only and cannot become production authority.")
        return 2

    # 1. load the memory texts (same corpus the box builder covers)
    slugs, docs = [], []
    for fn in sorted(os.listdir(mem)):
        if not fn.endswith(".md") or fn[:-3] in SKIP:
            continue
        try:
            body = io.open(os.path.join(mem, fn), encoding="utf-8", errors="replace").read()
        except Exception:
            continue
        t = _text_for(body)
        if len(t) < 40:
            continue
        slugs.append(fn[:-3]); docs.append(_tokens(t))
        if limit and len(slugs) >= limit:
            break
    N = len(slugs)
    if N < 3:
        print("build_memory_embeddings_local: too few memories to embed."); return

    # 2. vocabulary with document-frequency pruning: drop hapax (df<2, noise) and near-ubiquitous
    #    (df>50% of corpus, no discriminative value), then cap to the most informative terms.
    df = defaultdict(int)
    for toks in docs:
        for w in set(toks):
            df[w] += 1
    max_df = max(2, int(0.5 * N))
    vocab_terms = [w for w, d in df.items() if 2 <= d <= max_df]
    # rank by idf-ish informativeness but keep enough terms for the SVD to find structure
    vocab_terms.sort(key=lambda w: df[w])          # rarer (more specific) first
    CAP = 12000
    if len(vocab_terms) > CAP:
        vocab_terms = vocab_terms[:CAP]
    vocab = {w: j for j, w in enumerate(vocab_terms)}
    V = len(vocab)
    if V < 10:
        print("build_memory_embeddings_local: vocabulary too small after pruning."); return

    # 3. TF-IDF matrix (sublinear tf, smoothed idf), L2-normalized rows
    idf = np.zeros(V, dtype=np.float32)
    for w, j in vocab.items():
        idf[j] = math.log((1.0 + N) / (1.0 + df[w])) + 1.0
    X = np.zeros((N, V), dtype=np.float32)
    for i, toks in enumerate(docs):
        tf = defaultdict(int)
        for w in toks:
            j = vocab.get(w)
            if j is not None:
                tf[j] += 1
        for j, c in tf.items():
            X[i, j] = (1.0 + math.log(c)) * idf[j]      # sublinear tf x idf
    norms = np.linalg.norm(X, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    X /= norms

    # 4. LSA: randomized truncated SVD -> dense doc coordinates (U*S), L2-normalized for cosine
    k = min(dims, V, N - 1)
    print(f"build_memory_embeddings_local: {N} memories x {V} terms -> LSA {k} dims (numpy, CPU)...")
    U, S = _randomized_svd(X, k)
    emb = (U * S).astype(np.float32)                    # document embeddings in latent-semantic space
    en = np.linalg.norm(emb, axis=1, keepdims=True)
    en[en == 0] = 1.0
    emb /= en

    np.savez_compressed(out, slugs=np.array(slugs), vecs=emb, model=f"tfidf-lsa-{k}-local")
    print(f"wrote {out}: {emb.shape[0]} vectors x {emb.shape[1]} dims (model-free LSA). "
          f"Now run eval_semantic_recall.py - keep this sidecar only if it separates the ground-truth pairs.")


if __name__ == "__main__":
    sys.exit(main() or 0)
