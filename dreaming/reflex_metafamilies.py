#!/usr/bin/env python3
"""reflex_metafamilies.py - do the ~53 gut reflexes collapse into a few META-principles?

The heeding finding (this session): reflexes fire well but a typical turn surfaces several at once (53
reflexes, avg ~53 fires each, flat), so the specific critical one is diluted by the always-on generics.
If the reflexes have low-dimensional STRUCTURE - if they cluster into a handful of meta-principles - then
the firing layer can surface ONE meta-line when several of its members co-fire, instead of N diluting
lines. This tests for that structure directly, from behaviour, not by hand-labelling.

Method (network science): two reflexes CO-FIRE when they trigger on the same event (same timestamp in the
durable fire-log instinct_fires.tsv - one tool call / prompt matched both). Build the weighted co-firing
graph, run Louvain across several seeds for stability, and report the meta-families with an interpretable
label (dominant member + the fraction of fires it carries). A stable, modular partition means the gut has
a real low-rank structure the firing layer can exploit.

Read-only. Stdlib + networkx (guarded). Usage: python reflex_metafamilies.py
"""
import os, io, sys, itertools
from collections import defaultdict, Counter

HERE = os.path.dirname(os.path.abspath(__file__))
FIRES = os.path.join(HERE, "instinct_fires.tsv")
MIN_COFIRE = 3     # draw an edge only when two reflexes co-fired at least this many times (noise floor)

# The single strong meta-line each family should collapse to (hand-authored reflex CONTENT; the firing
# layer surfaces THIS once, plus the family's most-specific fired member, instead of N diluting sub-reflexes).
# Matched to a derived family by signature-member overlap, so the labels survive membership drift.
META_LINES = [
    (["sim_flatters", "risk_vs_measured_record", "sim_sign_vs_lived_nav", "leveraged_etf_is_not_linear", "stale_mark_banks"],
     "his-domain / production",
     "You're modeling a measured system. A sim that disagrees with the MEASURED record in either direction - sign OR "
     "magnitude - is the sim lying, not the book. Reconcile against the actual account/tape before you "
     "report a number; the owner's instinct on a system they run is a strong prior."),
    (["verify_adversarially", "steelman_opposite", "disconfirm", "outside_view", "reach_across_domains"],
     "epistemic discipline",
     "Before you believe this: write the strongest case you're WRONG and name the check that would confirm "
     "it; run the test that comes out different if the theory is false, not the one that confirms it. A "
     "passing confirmation isn't a test."),
    (["utf8_text_surgery", "ps1_hook_edit", "windowless", "premortem", "no_local_privileged_stack",
      "hasty_pattern_match", "his_call_is_final", "green_not_trusted"],
     "irreversible / side-effect action",
     "You're about to act with side effects (an edit, a process, a destructive move, a boundary he set). "
     "Read the real state before you write, confirm the exact target and that the change actually takes, "
     "honor his stated boundary, and prefer the reversible path."),
    (["voice_human", "heavy_compute_remote", "lookahead", "he_will_look", "compute_distress"],
     "producing output / compute",
     "He WILL open what you produce, and compute must stay polite. Verify the rendered artifact with your "
     "own eyes, keep it human-voiced, keep heavy compute off the laptop, and keep every feature causal."),
    (["secrets_in_git", "concurrent_races"],
     "shared repo state",
     "You're touching shared repo state. Scan the diff for secrets before every push and check what else is "
     "running before you force anything - rebase on reject, never clobber shared history."),
]


def _meta_line(members):
    """Match a derived family to its meta-line by signature-member overlap (best overlap wins)."""
    best, bestn = None, 0
    ms = set(members)
    for sig, label, line in META_LINES:
        k = len(ms & set(sig))
        if k > bestn:
            best, bestn = (label, line), k
    return best


def load_cofire():
    by_ts = defaultdict(set)
    fires = Counter()
    try:
        for ln in io.open(FIRES, encoding="utf-8", errors="replace"):
            p = ln.rstrip("\n").split("\t")
            if len(p) >= 4 and p[3]:
                by_ts[p[0]].add(p[3])
    except Exception:
        return {}, Counter(), Counter()
    edges = Counter()
    for refs in by_ts.values():
        for r in refs:
            fires[r] += 1
        for a, b in itertools.combinations(sorted(refs), 2):
            edges[(a, b)] += 1
    return by_ts, fires, edges


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # Discovery is not execution: this script writes, so a help request must
        # exit before any work. See script_help_flag_can_execute_work.
        print(__doc__ or os.path.basename(__file__))
        return
    ranking = "--ranking" in sys.argv
    _, fires, edges = load_cofire()
    if not fires:
        print("reflex_metafamilies: no fire-log data yet.")
        return
    try:
        import networkx as nx
    except Exception:
        print("reflex_metafamilies: networkx not available - skipping (co-firing counts still in the log).")
        return
    G = nx.Graph()
    for (a, b), w in edges.items():
        if w >= MIN_COFIRE:
            G.add_edge(a, b, weight=w)
    if G.number_of_edges() < 8:
        print("reflex_metafamilies: too few co-fires above the noise floor to cluster.")
        return

    # multi-seed Louvain: a stable Q means the meta-structure is real, not a seed artifact
    qs, best = [], None
    for seed in (1, 42, 100, 7, 2024):
        c = nx.community.louvain_communities(G, weight="weight", seed=seed, resolution=1.0)
        q = nx.community.modularity(G, c, weight="weight")
        qs.append(q)
        if best is None or q > best[0]:
            best = (q, c)
    qmean = sum(qs) / len(qs)
    qspread = max(qs) - min(qs)
    comms = [c for c in best[1] if len(c) >= 2]
    total_fires = sum(fires.values())

    print("=" * 74)
    print(" REFLEX META-FAMILIES  (do the reflexes collapse into a few principles?)")
    print("=" * 74)
    print("\n%d reflexes fire; %d are linked by co-firing (>=%d shared events); %d fire alone/rarely."
          % (len(fires), G.number_of_nodes(), MIN_COFIRE, len(fires) - G.number_of_nodes()))
    print("modularity Q = %.3f mean, spread %.3f across 5 seeds (%s); Q>0.3 = real meta-structure"
          % (qmean, qspread, "STABLE" if qspread < 0.05 else "seed-sensitive"))
    print("-> %d meta-families. When several members of ONE family co-fire, the firing layer can surface"
          % len(comms))
    print("   the family's meta-line ONCE instead of N diluting sub-reflexes (the heeding/volume fix).\n")
    for c in sorted(comms, key=lambda s: -sum(fires[r] for r in s)):
        mem = sorted(c, key=lambda r: -fires[r])
        fam_fires = sum(fires[r] for r in c)
        ml = _meta_line(c)
        print("  META-FAMILY: %s  (%d reflexes, %.0f%% of all fires)"
              % (ml[0] if ml else "(unlabeled)", len(c), 100.0 * fam_fires / total_fires))
        print("     lead: %s" % ", ".join("%s(%d)" % (r, fires[r]) for r in mem[:5]))
        if len(mem) > 5:
            print("     also: %s" % ", ".join(mem[5:]))
        if ml:
            print("     META-LINE (collapse the family to this one warning): %s" % ml[1])
    print("\n  read: a reflex that co-fires with a whole family is rarely the SPECIFIC signal for this act -")
    print("  it is the family's background. Surface the family's ONE most-specific member (by surface_weight,")
    print("  --ranking - charge is saturated so it can't rank), collapse the rest to the meta-line.")

    if ranking:
        import math, json
        total_fires = sum(fires.values())
        max_idf = max(math.log((total_fires + 1.0) / (f + 1.0)) for f in fires.values()) or 1.0
        weight = {r: round(math.log((total_fires + 1.0) / (fires[r] + 1.0)) / max_idf, 3) for r in fires}
        ordered = sorted(weight.items(), key=lambda kv: kv[1])
        print("\n" + "=" * 74)
        print(" SURFACE-RANKING  (inverse fire-frequency - the ranking signal charge lacks)")
        print("=" * 74)
        print("  89f84043: charge is saturated (37/45 = 1.00), so it can't rank co-firing reflexes. A reflex")
        print("  that fires RARELY carries more information when it fires than an always-on generic.")
        print("  surface_weight = normalized IDF (log(total/fires)): high = specific, surface; low = background.")
        print("  most GENERIC (demote/collapse): " + ", ".join("%s=%.2f" % (r, w) for r, w in ordered[:6]))
        print("  most SPECIFIC (surface first):  " + ", ".join("%s=%.2f" % (r, w) for r, w in ordered[-6:][::-1]))
        try:
            out = os.path.join(HERE, "reflex_ranking.json")
            io.open(out, "w", encoding="utf-8", newline="\n").write(json.dumps(
                {"_note": "surface_weight = normalized IDF over the fire-log; ranks co-firing reflexes since "
                          "charge is saturated (89f84043). high=surface first, low=demote/collapse to meta-line.",
                 "surface_weight": dict(sorted(weight.items(), key=lambda kv: -kv[1]))}, indent=1))
            print("  wrote %s (%d reflexes) - the firing layer can rank a turn's fires by this." % (out, len(weight)))
        except Exception as e:
            print("  (ranking write failed: %s)" % e)


if __name__ == "__main__":
    main()
