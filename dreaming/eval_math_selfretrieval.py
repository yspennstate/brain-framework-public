"""The weakest possible bar for the math ranker: can it find a claim from that claim's own words?

WHY THIS EXISTS. Measured 2026-08-22: the math brain answers 1,616 live consultations with 67 of
its 1,130 claims, because graph_score is ~57% of the total and accumulates once per matched seed,
while the query's own words are ~11%. That predicts something falsifiable and embarrassing - that
querying with a claim's EXACT text can still lose to whatever is best connected to the seeds. This
harness tests it.

The gold set is free and needs no labelling: every claim is its own answer. Self-retrieval is not a
sufficient condition for a good ranker, but it is a necessary one - a store that cannot return a
claim quoted verbatim cannot return it from a paraphrase either. Read it as a floor, never as a
score of retrieval quality.

REPORTS, per query variant:
  hit@1 / hit@6 / MRR over the full population, plus the rank distribution of the misses and WHICH
  claims eat the slots when the right answer loses. That last column is the diagnostic: if the same
  handful absorbs the misses, the defect is the graph channel, not the lexical one.

Read-only: opens the store, writes one JSONL of per-item results and prints a summary.
"""
import sys, os, io, json, re, time, collections, statistics, argparse

sys.path.insert(0, "~/math-brain-build/src")
from math_brain.retrieval import retrieve
from math_brain.store import ResearchStore

STORE = "~/math-brain-build/local/population/brain.sqlite3"
OUT = os.path.expanduser("~/.claude/dreaming/state/math_selfretrieval.jsonl")


def node_text(n):
    for a in ("body", "statement", "title"):
        v = getattr(n, a, None)
        if isinstance(v, str) and v.strip():
            return v
    return ""


def node_title(n):
    v = getattr(n, "title", None)
    return v if isinstance(v, str) and v.strip() else ""


def norm_title(t):
    """Normalise a title so duplicate claims of the same theorem collapse together."""
    t = (t or "").lower()
    t = re.sub(r"\(.*?\)", " ", t)          # drop parenthetical qualifiers
    t = re.sub(r"[^a-z0-9 ]", " ", t)
    t = re.sub(r"\b(the|of|a|an|for|and|in|on|to)\b", " ", t)
    return " ".join(t.split())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=6, help="slate size math_sense uses")
    ap.add_argument("--sample", type=int, default=0, help="0 = the whole population")
    ap.add_argument("--store", default=STORE)
    a = ap.parse_args()

    st = ResearchStore(a.store)
    nodes = list(st.iter_nodes())
    edges = list(st.iter_edges())
    title_group = collections.defaultdict(set)
    for n in nodes:
        title_group[norm_title(n.title)].add(n.id)

    deg = collections.Counter()
    for e in edges:
        deg[e.source_id] += 1
        deg[e.target_id] += 1

    items = [n for n in nodes if node_text(n)]
    if a.sample:
        import random
        items = random.Random(11).sample(items, min(a.sample, len(items)))
    print("store %s: %d nodes, %d edges; %d testable claims"
          % (os.path.basename(a.store), len(nodes), len(edges), len(items)))
    print("gold = the claim itself. Two query variants per claim: its full text, and its title.")
    print()

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    fh = io.open(OUT, "w", encoding="utf-8", newline="\n")
    res = {"text": [], "title": []}
    res_dup = {"text": [], "title": []}
    thieves = collections.Counter()
    t0 = time.time()
    for i, n in enumerate(items):
        for variant, q in (("text", node_text(n)), ("title", node_title(n))):
            if not q:
                continue
            r = retrieve(q, nodes=nodes, edges=edges, limit=a.limit,
                         min_support_score=0.5, min_margin=0.1)
            ids = [h.node.id for h in r.hits]
            rank = ids.index(n.id) + 1 if n.id in ids else 0
            # DUPLICATE-AWARE RANK. The store is 35.8% duplicated - 1,131 claims collapse to 901
            # distinct normalised titles, 230 redundant copies - so returning a DIFFERENT copy of the
            # same theorem is a correct answer that a single-id check scores as a miss. Measured
            # 2026-08-22 on the sibling harness: it understated hit@1 by 7 points and hit@6 by 13.
            # 405 of this harness's 1,131 items carry the same exposure. Both ranks are recorded;
            # normalised-title equality can merge two genuinely different results sharing a name
            # (Lagrange's theorem is the group-theory one AND the mean-value one), so the dup rank is
            # the optimistic bound and the truth is between them.
            gold = title_group.get(norm_title(n.title), {n.id})
            drank = next((k + 1 for k, i in enumerate(ids) if i in gold), 0)
            res[variant].append(rank)
            res_dup[variant].append(drank)
            if rank != 1 and ids:
                thieves[ids[0]] += 1
            fh.write(json.dumps({"node": n.id, "variant": variant, "rank": rank,
                                 "rank_dupaware": drank,
                                 "degree": deg.get(n.id, 0), "qlen": len(q),
                                 "abstained": bool(r.abstained),
                                 "top": ids[0] if ids else None}) + "\n")
        if (i + 1) % 100 == 0:
            print("   %d/%d  (%.0fs)" % (i + 1, len(items), time.time() - t0), flush=True)
    fh.close()

    print()
    for variant in ("text", "title"):
        rr = res[variant]
        dd = res_dup[variant]
        if not rr:
            continue
        hit1 = sum(1 for x in rr if x == 1)
        hitk = sum(1 for x in rr if x > 0)
        mrr = sum((1.0 / x) for x in rr if x > 0) / len(rr)
        print("QUERY = the claim's own %s   (n=%d)" % (variant.upper(), len(rr)))
        print("   hit@1  %5d = %5.1f%%" % (hit1, 100.0 * hit1 / len(rr)))
        print("   hit@%d  %5d = %5.1f%%" % (a.limit, hitk, 100.0 * hitk / len(rr)))
        print("   MRR    %.3f" % mrr)
        if dd:
            dh1 = sum(1 for x in dd if x == 1)
            dhk = sum(1 for x in dd if x > 0)
            print("   DUPLICATE-AWARE (any copy of the same theorem counts):")
            print("      hit@1  %5d = %5.1f%%   (+%.1f)" % (dh1, 100.0*dh1/len(dd), 100.0*(dh1-hit1)/len(dd)))
            print("      hit@%d  %5d = %5.1f%%   (+%.1f)" % (a.limit, dhk, 100.0*dhk/len(dd), 100.0*(dhk-hitk)/len(dd)))
        print("   MISSED ENTIRELY (not in top %d): %d = %.1f%%"
              % (a.limit, len(rr) - hitk, 100.0 * (len(rr) - hitk) / len(rr)))
        print()

    print("WHO EATS THE SLOT when the claim does not rank first (top-8 thieves):")
    tot = sum(thieves.values())
    for nid, c in thieves.most_common(8):
        print("   %-44s %5d = %4.1f%% of all misses  (degree %d)"
              % (str(nid)[:44], c, 100.0 * c / max(1, tot), deg.get(nid, 0)))
    print("   distinct claims taking rank 1 on a miss: %d" % len(thieves))

    zero = [r for n, r in zip(items, res["text"]) if deg.get(n.id, 0) == 0]
    some = [r for n, r in zip(items, res["text"]) if deg.get(n.id, 0) > 0]
    if zero and some:
        print()
        print("BY CONNECTIVITY (query = own text):")
        print("   edgeless claims (n=%d): hit@1 %.1f%%"
              % (len(zero), 100.0 * sum(1 for x in zero if x == 1) / len(zero)))
        print("   with edges      (n=%d): hit@1 %.1f%%"
              % (len(some), 100.0 * sum(1 for x in some if x == 1) / len(some)))
    print()
    print("wrote %s" % OUT)


if __name__ == "__main__":
    main()
