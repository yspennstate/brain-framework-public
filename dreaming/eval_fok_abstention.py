#!/usr/bin/env python3
"""eval_fok_abstention.py - does the feeling-of-knowing correctly ABSTAIN on what the brain doesn't know?

LongMemEval (2024-25) names abstention - recognising the answer is NOT in memory - as one of five core
memory abilities, and builds unanswerable questions by construction to measure false recall. This brain's
fok_probe ships a feeling-of-knowing (fire when >=3 index/filename pointers light up) and a feeling-of-
NOT-knowing (abstain when 0 pointers + a definite-answer question), but its FALSE-RECALL rate - firing
"I know this" when it holds nothing - was never measured. A false FoK is the confabulation risk: the
session trusts a signal that the knowledge is there when scattered word-matches only made it look there.

Two probe sets, both from the corpus so the test is fair:
  ANSWERABLE   - a real memory's own distinctive words as the query. fok SHOULD light (>=MIN_CLUSTER).
  UNANSWERABLE - ONE distinctive word each from 5 DIFFERENT memories, so no single memory shares >=2
                 words with the query (nothing is ABOUT this bag of familiar words). fok SHOULD abstain.
                 This is the true hard negative - individually-familiar words, no memory they land on
                 together - exactly what a count-the-matches signal can confabulate on. (An earlier
                 version drew 3 words from one memory, which made that memory a genuine 3-overlap hit -
                 not unanswerable; fixed 2026-07-09.)

Replicates fok_probe's exact pointer count (index-line overlap>=2, filename overlap>=2) so it measures
the live signal, not a proxy. Reports false-recall rate, miss rate, and the separation. Read-only.
Usage: python eval_fok_abstention.py
"""
import os, io, re, random

DR = os.path.dirname(os.path.abspath(__file__))
MEM = os.path.expanduser(r"~/.claude/projects/{{BRAIN_SLUG}}/memory")
MIN_CLUSTER = 3   # must match fok_probe.MIN_CLUSTER
STOP = set(("the a an and or of to in for on with is are was were be this that it its as at by from into "
            "over per not no new one two use used using via etc when then than but our we you your i me my "
            "he she they them their what which who how why can do does did has have had will would could "
            "should more most some any each all only just get got make made see saw look need want know "
            "now today about into onto out up down let").split())
RNG = random.Random(1729)


def words(s):
    return [w for w in re.findall(r"[a-zA-Z][a-zA-Z_]{2,}", (s or "").lower()) if w not in STOP]


def load_index():
    lines = {}
    try:
        for ln in io.open(os.path.join(MEM, "MEMORY.md"), encoding="utf-8", errors="replace"):
            m = re.match(r"- \[([^\]]+?)\.md\]", ln)
            if m:
                lines[m.group(1)] = ln.lower()
    except Exception:
        pass
    return lines


def fok_scores(query, index_lines, all_slugs):
    """fok_probe's exact overlap map: slug -> overlap (index-line overlap>=2 or filename overlap>=2)."""
    qw = set(words(query))
    best = {}
    for slug, ln in index_lines.items():
        ov = sum(1 for w in qw if w in ln)
        if ov >= 2:
            best[slug] = ov
    for slug in all_slugs:
        ov = sum(1 for w in qw if len(w) >= 4 and w in slug.lower())
        if ov >= 2:
            best[slug] = max(best.get(slug, 0), ov)
    return best


def fires_flat(scores):
    """Live fok: >=MIN_CLUSTER memories clear the overlap>=2 bar."""
    return len(scores) >= MIN_CLUSTER


def fires_concentrated(scores):
    """Tightened: a real cluster needs CONCENTRATION - the top hit must carry >=3 overlap (the query
    lands ON a memory, not scattered across many at the minimum bar). Still needs >=MIN_CLUSTER pointers."""
    return len(scores) >= MIN_CLUSTER and (max(scores.values()) if scores else 0) >= 3


def build_probes(pool, mem_words, rng, n=40):
    sample = rng.sample(pool, min(n, len(pool)))
    ans_q, unans_q = [], []
    for slug in sample:
        ans_q.append(" ".join(rng.sample(mem_words[slug], min(5, len(mem_words[slug])))))
        # hard negative: 1 word from each of 5 distinct memories -> no memory shares >=2 with the query
        donors = rng.sample(pool, 5)
        unans_q.append(" ".join(rng.choice(mem_words[d]) for d in donors))
    return ans_q, unans_q


# --- the NOT-knowing (abstention nudge) side: when should fok_probe say "you hold nothing on this"?
# Ground truth: an ANSWERABLE query = the brain does hold it (FoNK firing there is a false abstention);
# a CHIMERA = nothing is about the bag (FoNK should fire). The old rule (n==0, absolute void) is
# brittle by construction: as the corpus grows, chance 2-word overlaps on generic words make a true
# void register as n>=1 and the nudge goes silent (OPEN_QUESTIONS 2026-07-08; humility fonk fixture).
def fonk_void_absolute(scores):
    return len(scores) == 0                                    # old, replaced 2026-07-09

def fonk_complement(scores):
    return not fires_concentrated(scores)                      # everything below the FoK bar

def fonk_scatter(scores):
    """Nothing lands SQUARELY: no memory carries >=3 of the query's words. Fires on true voids and
    scattered familiar-word bags alike; stays silent when a top hit is concentrated (the brain knows,
    even if too few companions lit for the FoK announcement)."""
    return (max(scores.values()) if scores else 0) <= 2


def main():
    idx = load_index()
    slugs = [fn[:-3] for fn in os.listdir(MEM) if fn.endswith(".md")]
    # distinctive words per memory (from its index line if present, else its slug)
    mem_words = {}
    for slug in slugs:
        src = idx.get(slug, slug.replace("_", " "))
        w = [x for x in words(src) if len(x) >= 4]
        if len(w) >= 4:
            mem_words[slug] = w
    pool = list(mem_words)
    if len(pool) < 20:
        print("eval_fok_abstention: too few richly-described memories to build probes.")
        return

    ans_q, unans_q = build_probes(pool, mem_words, RNG)

    def measure(rule, aq=None, uq=None):
        aq, uq = aq or ans_q, uq or unans_q
        knows = sum(1 for q in aq if rule(fok_scores(q, idx, slugs)))
        false = sum(1 for q in uq if rule(fok_scores(q, idx, slugs)))
        return knows / len(aq), false / len(uq)

    k_flat, f_flat = measure(fires_flat)
    k_conc, f_conc = measure(fires_concentrated)
    print("eval_fok_abstention: %d answerable + %d unanswerable (chimera) probes" % (len(ans_q), len(unans_q)))
    print("  %-28s %8s %13s %11s" % ("fok criterion", "knows", "false-recall", "separation"))
    print("  %-28s %7.2f %12.2f %11.2f  (old, pre-2026-07-09)" % ("flat count >=3", k_flat, f_flat, k_flat - f_flat))
    print("  %-28s %7.2f %12.2f %11.2f  (LIVE since 2026-07-09)" % ("concentrated (top overlap>=3)",
          k_conc, f_conc, k_conc - f_conc))
    print("  read: 'knows' should stay high while 'false-recall' drops. If the concentrated rule widens "
          "separation, fok_probe's fire criterion should require the top hit to carry real overlap, not "
          "just >=3 memories clearing the minimum bar (the confabulation-on-familiar-words fix).")

    # --- abstention (FoNK) section, multi-seed: false-abstention on answerable should be LOW,
    # abstention on chimeras HIGH. Note the score here is INTEGER overlap - fok_probe must test its
    # thresholds on the same integer (the 0.01*salience rank-bump reaches 1.0 and must not leak in).
    rules = [("absolute void n==0 (old)", fonk_void_absolute),
             ("complement of FoK", fonk_complement),
             ("scatter <=2 (LIVE)", fonk_scatter)]
    agg = {name: [0.0, 0.0] for name, _ in rules}
    seeds = [1729, 7, 42, 137, 271, 999]
    for sd in seeds:
        aq, uq = build_probes(pool, mem_words, random.Random(sd))
        for name, rule in rules:
            fa, ab = measure(rule, aq, uq)
            agg[name][0] += fa / len(seeds)   # false-abstention rate (on answerable)
            agg[name][1] += ab / len(seeds)   # correct-abstention rate (on chimeras)
    print()
    print("  FoNK (abstention nudge) - mean over %d seeds:" % len(seeds))
    print("  %-28s %16s %14s %11s" % ("void criterion", "false-abstention", "catches-void", "separation"))
    for name, _ in rules:
        fa, ab = agg[name]
        print("  %-28s %15.2f %13.2f %11.2f" % (name, fa, ab, ab - fa))
    print("  read: the old n==0 rule goes silent as the corpus grows (chance 2-overlaps mask true "
          "voids); the live scatter rule fires when no memory carries the query squarely. Keep it only "
          "while its false-abstention stays near the FoK miss floor.")

    # --- TYPO arm (2026-07-09): the owner types heavy deliberate typos ('memeory'), and fok matches
    # exact words - a typo'd claim-check can FALSE-VOID (FoNK says "you hold nothing" about something
    # held). bm25.QueryNormalizer fixes OOV tokens (edit1, df>=2) and is already live in the seeder;
    # this measures whether wiring it into fok_probe (a) recovers typo'd knows-rate, (b) does NOT raise
    # chimera false-recall (chimera words are in-vocab, so the normalizer must leave them), and (c) does
    # NOT repair genuinely-novel OOV words into vocabulary (manufacturing false-knowing on true voids).
    try:
        import sys as _sys
        _sys.path.insert(0, DR)
        from bm25 import BM25, QueryNormalizer
        qn = QueryNormalizer(BM25().df)

        def typo(w, rng):
            i = rng.randrange(len(w))
            op = rng.choice(["del", "dup", "swap", "sub"])
            if op == "del" and len(w) > 4:
                return w[:i] + w[i + 1:]
            if op == "dup":
                return w[:i] + w[i] + w[i:]
            if op == "swap" and i < len(w) - 1:
                return w[:i] + w[i + 1] + w[i] + w[i + 2:]
            return w[:i] + rng.choice("abcdefghijklmnopqrstuvwxyz") + w[i + 1:]

        def corrupt(q, rng):
            ws = q.split()
            for j in sorted(range(len(ws)), key=lambda k: -len(ws[k]))[:2]:  # hit the 2 most distinctive
                ws[j] = typo(ws[j], rng)
            return " ".join(ws)

        def nonsense(rng, n=12):
            # letter-shuffled real-length strings; re-roll any that lands in vocab (a true OOV void)
            out = []
            while len(out) < n:
                w = "".join(rng.choice("bcdfghjklmnpqrstvwxz") if k % 2 == 0 else rng.choice("aeiou")
                            for k in range(rng.randrange(7, 11)))
                if w not in qn.df:
                    out.append(w)
            return out

        agg2 = {k: 0.0 for k in ("clean", "typo", "typo_norm", "chim", "chim_norm",
                                 "void_catch", "void_catch_norm", "repaired")}
        for sd in seeds:
            rng = random.Random(sd)
            aq, uq = build_probes(pool, mem_words, random.Random(sd))
            t_aq = [corrupt(q, rng) for q in aq]
            # per-token _correct on the word set - the EXACT transform fok_probe now wires (not the
            # whole-query normalize(), whose bm25 tokenization/stopwords differ slightly from fok's)
            pt = lambda q: " ".join(qn._correct(w) for w in q.split())
            n_aq = [pt(q) for q in t_aq]
            c_norm = [pt(q) for q in uq]
            voids = [" ".join(rng.sample(nonsense(rng), 5)) for _ in range(20)]
            v_norm = [pt(q) for q in voids]
            repaired = sum(1 for q, p in zip(voids, v_norm)
                           for a, b in zip(q.split(), p.split()) if a != b)
            k = lambda qs: sum(1 for q in qs if fires_concentrated(fok_scores(q, idx, slugs))) / len(qs)
            v = lambda qs: sum(1 for q in qs if fonk_scatter(fok_scores(q, idx, slugs))) / len(qs)
            ns = len(seeds)
            agg2["clean"] += k(aq) / ns
            agg2["typo"] += k(t_aq) / ns
            agg2["typo_norm"] += k(n_aq) / ns
            agg2["chim"] += k(uq) / ns
            agg2["chim_norm"] += k(c_norm) / ns
            agg2["void_catch"] += v(voids) / ns
            agg2["void_catch_norm"] += v(v_norm) / ns
            agg2["repaired"] += repaired / (20.0 * 5.0) / ns
        print()
        print("  TYPO arm (QueryNormalizer into fok?) - mean over %d seeds:" % len(seeds))
        print("    knows (concentrated): clean %.2f | typo'd %.2f | typo'd+normalize %.2f"
              % (agg2["clean"], agg2["typo"], agg2["typo_norm"]))
        print("    chimera false-recall: raw %.2f | normalized %.2f  (must not rise)"
              % (agg2["chim"], agg2["chim_norm"]))
        print("    true-void catch (scatter): raw %.2f | normalized %.2f ; nonsense words 'repaired' "
              "into vocab: %.3f  (must stay ~0)" % (agg2["void_catch"], agg2["void_catch_norm"], agg2["repaired"]))
        print("    read: wire the normalizer into fok_probe only if the typo recovery is real AND both "
              "risk rows hold flat.")
    except Exception as e:
        print("  TYPO arm unavailable: %r" % e)


if __name__ == "__main__":
    main()
