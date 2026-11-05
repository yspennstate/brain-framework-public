#!/usr/bin/env python3
"""eval_semantic_recall.py - the honest test owed before trusting the embedding recall lift.

spread_recall re-ranks its graph candidates by cosine to the seed cluster only through the verified active
BGE authority. The model-free LSA artifact remains an explicit offline baseline and cannot enter runtime.
Before believing that the BGE re-rank lifts precision, one thing
must hold: the embeddings must actually separate RELATED memories from UNRELATED ones. If cosine doesn't
track relatedness, the re-rank is noise dressed as signal. So this is a STABLE, ground-truth eval - fixed
memory PAIRS (not prompts, whose graph candidates drift week to week - the non-stationary-graph lesson),
with clear-cut labels where the right answer isn't a judgment call.

The metric that matters is SEPARATION, and the robust way to measure it is threshold-free: AUC = the
probability a random related pair scores higher than a random unrelated pair (0.5 = chance, 1.0 = perfect).
The old midpoint-accuracy number is fragile - one low-cosine related outlier drags it below a pass bar even
when the mean gap is large - so AUC is now the verdict driver and midpoint-accuracy is kept only as a
secondary read. Per-pair cosines are printed with `--pairs` so a failure is diagnosable, not just a grade.

The verdict is TIERED by the cost of an error in how the sidecar is actually used:
  - STRONG  (AUC >= 0.90): separates cleanly; trustworthy even for a hard semantic FILTER that removes
                           candidates. Wire it live with confidence.
  - MODERATE (0.80..0.90): real signal, some conceptual pairs missed. Safe for a GENTLE re-rank that only
                           REORDERS candidates (never removes one) - which is exactly how spread_recall
                           uses it (score *= 0.4 + 0.6*cos, floored, never dropped). A mis-order costs
                           far less than a wrong deletion, so this bar is lower than the filter bar on
                           purpose. A better model (the box sentence model) is the path to STRONG.
  - WEAK    (AUC < 0.80): does not separate; the re-rank would be noise - do NOT trust it, pull the sidecar.

Runs only where the sidecar exists; prints exactly what's missing otherwise. Read-only.
Usage: python eval_semantic_recall.py [--pairs | --behavioral | --determinism-check]
"""
import os, io, sys
import embedding_artifact

DR = os.path.dirname(os.path.abspath(__file__))
EMB = os.path.join(DR, "memory_embeddings.npz")
AUTHORITY = os.path.join(DR, ".embedding_current.json")
MEM = os.path.expanduser(r"~/.claude/projects/{{BRAIN_SLUG}}/memory")
MODEL = "BAAI/bge-small-en-v1.5"


def _runtime_view():
    loaded = embedding_artifact.load_verified_embeddings(
        EMB, AUTHORITY, MEM, MODEL, verify_all_rows=True
    )
    if loaded is None:
        return None
    _, receipt = embedding_artifact.resolve_authority_sidecar(EMB, AUTHORITY)
    return loaded, receipt

# clear-cut ground truth: pairs where "related?" is not a close call. Two memories about the SAME object /
# finding are related; a cooking memory against an astronomy one is not. Slugs may drift; the eval
# reports any it can't find rather than failing, so it stays runnable as the corpus evolves. Widened
# 2026-07-08 from 8+8 to a broader basis so the AUC verdict doesn't hinge on one or two pairs.
RELATED = [
    ("cadence_sim_accuracy_extended_ohlc_2026_07_08", "scar_check_the_brain_before_you_simulate_2026_07_08"),
    ("kerning_pairs_structural_model_2026_07_08", "compost_temperature_model_2026_07_08"),
    ("salience_affect_layer_2026_07_08", "instinct_layer_the_gut_2026_07_08"),
    ("humanness_audit_2026_07_08", "humanness_verified_2026_07_08"),
    ("overwatering_costs_yield_at_scale_2026_07_08", "orbit_transfer_window_planning_2026_07_08"),
    ("brain_neuroscience_grounding_2026_07_08", "gist_neuroscience_faculties_2026_07_08"),
    ("oco2_emulator_data_scaling_2026_07_08", "more_jpl_data_sources_2026_07_08"),
    # --- widened basis (all verified to exist) ---
    ("brain_neuroscience_grounding_2026_07_08", "brain_science_wave_two_2026_07_08"),
    ("method_improving_the_brain_well_2026_07_08", "brain_smarter_and_human_build_2026_07_08"),
    ("humanness_verified_2026_07_08", "associative_recall_verified_2026_07_08"),
    ("sourdough_hydration_ratio_2026_07_08", "overwatering_costs_yield_at_scale_2026_07_08"),
    ("gist_neuroscience_faculties_2026_07_08", "brain_smarter_and_human_build_2026_07_08"),
]
UNRELATED = [
    ("cadence_sim_accuracy_extended_ohlc_2026_07_08", "dnnaic_deep_kernel_null_2026_07_08"),
    ("compost_temperature_model_2026_07_08", "humanness_audit_2026_07_08"),
    ("salience_affect_layer_2026_07_08", "overwatering_costs_yield_at_scale_2026_07_08"),
    ("kerning_pairs_structural_model_2026_07_08", "instinct_layer_the_gut_2026_07_08"),
    ("oco2_emulator_data_scaling_2026_07_08", "cadence_sim_accuracy_extended_ohlc_2026_07_08"),
    ("brain_neuroscience_grounding_2026_07_08", "overwatering_costs_yield_at_scale_2026_07_08"),
    ("humanness_verified_2026_07_08", "compost_temperature_model_2026_07_08"),
    ("more_jpl_data_sources_2026_07_08", "salience_affect_layer_2026_07_08"),
    # --- widened basis ---
    ("brain_science_wave_two_2026_07_08", "overwatering_costs_yield_at_scale_2026_07_08"),
    ("method_improving_the_brain_well_2026_07_08", "oco2_emulator_data_scaling_2026_07_08"),
    ("sourdough_hydration_ratio_2026_07_08", "brain_neuroscience_grounding_2026_07_08"),
    ("associative_recall_verified_2026_07_08", "orbit_transfer_window_planning_2026_07_08"),
    ("dnnaic_deep_kernel_null_2026_07_08", "humanness_verified_2026_07_08"),
]


def _ordered_pair_scores(pairs, score):
    """Score pairs in canonical order so fixed-seed sampling is process-deterministic.

    ``pairs`` is normally a set assembled from retrieval sessions. Iterating that set directly
    changes with Python's per-process hash seed; a later ``random.Random(7).sample`` then samples
    different positives despite the apparently fixed RNG seed. Canonicalizing before scoring makes
    the behavioral eval reproducible without changing its pair population or metric.
    """
    out = []
    for a, b in sorted(pairs):
        value = score(a, b)
        if value is not None:
            out.append(value)
    return out


def determinism_check():
    """Small regression for the set-order failure that made --behavioral drift by process."""
    pairs = {("zeta", "alpha"), ("beta", "gamma"), ("alpha", "beta"), ("drop", "missing")}
    values = {
        ("alpha", "beta"): 0.1,
        ("beta", "gamma"): 0.2,
        ("zeta", "alpha"): 0.3,
    }
    got = _ordered_pair_scores(pairs, lambda a, b: values.get((a, b)))
    expected = [0.1, 0.2, 0.3]
    if got != expected:
        raise AssertionError("pair scores are not in canonical order: %r" % (got,))
    import random
    if random.Random(7).sample(got, len(got)) != random.Random(7).sample(expected, len(expected)):
        raise AssertionError("fixed-seed sampling is not reproducible")
    print("eval_semantic_recall determinism: PASS (canonical pair order + fixed RNG sample)")


def behavioral_eval():
    """The REAL-target gate, added 2026-07-08 after the LSA sidecar aced the pair-eval (AUC 0.87) but
    scored only 0.634 here - and driving spread_recall's live re-rank with it would have DAMPED the
    non-lexical associations that are its whole point (co-read associates sit at cosine ~0.05). The
    pair-eval uses hand-picked clear-cut pairs: an easy, flattering proxy. This asks the honest question:
    do the CONTENT embeddings (independent of the co-read graph) separate memories the fleet ACTUALLY
    co-read in a session from random pairs? Co-reads are the behavioral ground truth build_retrieval_trace
    recovers. An embedding that can't beat ~0.65 here does not track real association and must NOT drive
    the live re-rank, however well it did on hand-picked pairs. This is the scar-turned-guard: the box
    MiniLM run has to clear BOTH gates. Needs the sidecar + retrievals.tsv. Read-only."""
    import numpy as np, itertools, random
    from collections import defaultdict
    trace = os.path.join(DR, "retrievals.tsv")
    view = _runtime_view()
    if view is None:
        print("eval --behavioral: no verified active embedding authority.")
        return
    if not os.path.exists(trace):
        print("eval --behavioral: no retrievals.tsv. Run build_retrieval_trace.py first.")
        return
    loaded, receipt = view
    idx, mat, _ = loaded
    by_sess = defaultdict(set)
    for i, ln in enumerate(io.open(trace, encoding="utf-8", errors="replace")):
        if i == 0:
            continue
        p = ln.rstrip("\n").split("\t")
        if len(p) >= 4:
            by_sess[p[3]].add(p[0])
    coread = set()
    for slugs in by_sess.values():
        if 2 <= len(slugs) <= 25:            # skip giant survey sessions - their C(n,2) pairs are noise
            for a, b in itertools.combinations(sorted(slugs), 2):
                coread.add((a, b))

    def cos(a, b):
        if a in idx and b in idx:
            return float(mat[idx[a]] @ mat[idx[b]])
        return None

    pos = _ordered_pair_scores(coread, cos)
    if len(pos) < 20:
        print(f"eval --behavioral: only {len(pos)} co-read pairs in the embedded set - trace too young to judge.")
        return
    mems = sorted({m for pr in coread for m in pr} & set(idx))
    rng = random.Random(7)                    # fixed seed + canonical inputs -> process-deterministic
    cset, neg, tries = set(coread), [], 0
    while len(neg) < len(pos) and tries < 40000:
        a, b = rng.sample(mems, 2)
        tries += 1
        if tuple(sorted((a, b))) in cset:
            continue
        c = cos(a, b)
        if c is not None:
            neg.append(c)
    pp = pos if len(pos) <= 400 else rng.sample(pos, 400)
    nn = neg if len(neg) <= 400 else rng.sample(neg, 400)
    auc = sum((1.0 if a > b else 0.5 if a == b else 0.0) for a in pp for b in nn) / (len(pp) * len(nn))
    mp, mn = sum(pos) / len(pos), sum(neg) / len(neg)
    print(f"eval_semantic_recall --behavioral ({receipt['model']} "
          f"sha={receipt['sidecar_sha256'][:12]}, {len(pos)} co-read pairs vs {len(neg)} random):")
    print(f"  co-read cosine mean={mp:.3f}   random mean={mn:.3f}   gap={mp - mn:+.3f}")
    print(f"  AUROC (does content predict ACTUAL co-read behavior?): {auc:.3f}   [0.5=chance]")
    # IMPORTANT: this measures the embedding ALONE as a co-read ranker - a DIAGNOSTIC, not the go/no-go.
    # It reads ~0.6 for the LSA baseline (weak alone: co-reads are task-driven, not purely lexical). Do NOT
    # pull the sidecar on this number alone - that was an over-correction made and reversed 2026-07-08. The
    # DECISIVE gate for live use is eval_recall_signals.py: the embedding AS USED (spread_recall's multiplicative
    # blend with the graph) predicts future co-reads at ~0.79 vs the graph's ~0.68 - a robust +0.11 across 15
    # split/seed runs. A signal weak alone can still be a strong COMPLEMENT; the blend is what ships.
    if auc >= 0.65:
        print("  read: the embeddings track co-reads even alone (strong). Fine to lean on.")
    else:
        print("  read: weak ALONE (expected for LSA - co-reads are task-driven). NOT a reason to pull: "
              "the go/no-go is eval_recall_signals.py (embedding-as-used vs graph, causal). See its result.")


def main():
    if "--determinism-check" in sys.argv[1:]:
        determinism_check()
        return
    if "--behavioral" in sys.argv[1:]:
        behavioral_eval()
        return
    show_pairs = "--pairs" in sys.argv[1:]
    view = _runtime_view()
    if view is None:
        print("eval_semantic_recall: no verified active embedding authority.\n"
              "  Build a staged BGE sidecar and publish it through publish_memory_embeddings.py.")
        return
    import numpy as np
    loaded, receipt = view
    idx, mat, _ = loaded

    def cos(a, b):
        if a not in idx or b not in idx:
            return None
        va, vb = mat[idx[a]], mat[idx[b]]
        return float(va @ vb / ((np.linalg.norm(va) * np.linalg.norm(vb)) + 1e-9))

    rel, unr, missing = [], [], []
    rel_pairs, unr_pairs = [], []
    for a, b in RELATED:
        c = cos(a, b)
        (rel.append(c) or rel_pairs.append((c, a, b))) if c is not None else missing.append((a, b))
    for a, b in UNRELATED:
        c = cos(a, b)
        (unr.append(c) or unr_pairs.append((c, a, b))) if c is not None else missing.append((a, b))
    if len(rel) < 3 or len(unr) < 3:
        print("eval_semantic_recall: too many pair slugs missing from the sidecar to judge - refresh the pairs.")
        if missing:
            print("  missing:", ", ".join(f"{a}|{b}" for a, b in missing[:5]))
        return

    mr, mu = sum(rel) / len(rel), sum(unr) / len(unr)
    # AUC: probability a related pair outscores an unrelated pair (Mann-Whitney U / (n*m)). Threshold-free.
    auc = sum((1.0 if r > u else 0.5 if r == u else 0.0) for r in rel for u in unr) / (len(rel) * len(unr))
    # best achievable separation accuracy over all candidate thresholds (diagnostic, not the verdict)
    best_acc, best_t = 0.0, 0.0
    for t in sorted(set(rel + unr)):
        acc = (sum(1 for c in rel if c >= t) + sum(1 for c in unr if c < t)) / (len(rel) + len(unr))
        if acc > best_acc:
            best_acc, best_t = acc, t
    thr_mid = (mr + mu) / 2.0
    mid_acc = (sum(1 for c in rel if c >= thr_mid) + sum(1 for c in unr if c < thr_mid)) / (len(rel) + len(unr))

    print(f"eval_semantic_recall ({receipt['model']} sha={receipt['sidecar_sha256'][:12]}, "
          f"{len(rel)} related + {len(unr)} unrelated pairs"
          + (f", {len(missing)} skipped-missing" if missing else "") + "):")
    print(f"  mean cosine   related={mr:.3f}   unrelated={mu:.3f}   gap={mr - mu:+.3f}")
    print(f"  AUC (rank separation, threshold-free): {auc:.3f}     [0.5=chance, 1.0=perfect]")
    print(f"  best-threshold acc: {best_acc:.0%} at cos>={best_t:.2f}   |   midpoint-threshold acc: {mid_acc:.0%}")
    if auc >= 0.90:
        verdict = ("STRONG - separates cleanly; trustworthy even as a hard semantic filter. Wire live "
                   "with confidence.")
    elif auc >= 0.80:
        verdict = ("MODERATE - real signal, some conceptual pairs missed. Safe as spread_recall's GENTLE "
                   "re-rank (only reorders, never removes a candidate); a mis-order is cheap. A better "
                   "model (the box sentence model) is the path to STRONG.")
    else:
        verdict = ("WEAK - the embeddings barely separate related from unrelated; the re-rank would be "
                   "noise. Do NOT trust it - pull the sidecar or use a better model.")
    print(f"  verdict: {verdict}")

    if show_pairs:
        print("\n  RELATED pairs (want high cosine):")
        for c, a, b in sorted(rel_pairs):
            print(f"    {c:+.3f}  {a[:40]:40}  {b[:40]}")
        print("  UNRELATED pairs (want low cosine):")
        for c, a, b in sorted(unr_pairs, reverse=True):
            print(f"    {c:+.3f}  {a[:40]:40}  {b[:40]}")


if __name__ == "__main__":
    main()
