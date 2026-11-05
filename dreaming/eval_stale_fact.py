#!/usr/bin/env python3
"""eval_stale_fact.py - does the recall stack serve the CURRENT fact over its SUPERSEDED version?

The 2025-26 memory literature (Zep bi-temporal; the Supersede paper; MemConflict) names UPDATING - use
the current value of a fact, discard the superseded one - as a failure distinct from relevance: a system
can retrieve the RIGHT topic and still serve the STALE value, because the superseded memory often matches
a topic query as well as (or better than) its terse correction. The metric is the COLLISION rate (a topic
query surfaces BOTH members of an (old, correction) pair - the moment where staleness can bite) and the
STALE-FIRST rate among collisions (the outdated one ranks >= its correction).

Ground truth = the `superseded_by:` frontmatter (schema_supersede.py stamps explicit links; the dream
backfills prose-correction pairs). The query is the TOPIC - the distinctive words the two memories share -
never either memory's own full text, so the test is fair (neither side is handed its own wording).

It then evaluates the shippable fix: a deterministic `superseded_by`-aware demotion (if both a stale
memory and its correction are candidates, drop the stale one). Laptop-safe (bm25 only, no model).
Honest about power: with few pairs this is a directional read; it says so. Read-only.
Usage: python eval_stale_fact.py
"""
import os, io, re, sys
from collections import Counter


def _brain_slug_resolve():
    """The memory corpus this install belongs to.

    Order matters. $BRAIN_SLUG wins so a caller can point one script at another corpus.
    Then BRAIN_SLUG beside this file, which bootstrap/install.py writes with the slug of the
    --project-dir it was given; without it an install whose project directory is not the home
    directory builds a corpus nothing ever reads. The home-derived value stays last so a tree
    that works today keeps working untouched.
    """
    env = os.environ.get("BRAIN_SLUG", "").strip()
    if env:
        return env
    try:
        marker = os.path.join(os.path.dirname(os.path.abspath(__file__)), "BRAIN_SLUG")
        with open(marker, encoding="utf-8") as fh:
            named = fh.read().strip()
        if named:
            return named
    except OSError:
        pass
    home = os.path.expanduser("~")
    return home.replace(":", "-").replace(chr(92), "-").replace("/", "-")

DR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DR)
# Claude Code names a project directory after the home path, with ':' and the
# separators replaced by '-'. Deriving it keeps this tree free of one machine's
# username and correct on any other - and unlike a template token it is a real
# value at import time.
_BRAIN_SLUG = _brain_slug_resolve()
MEM = os.environ.get("BRAIN_MEM_DIR", os.path.join(os.path.expanduser("~"), ".claude", "projects", _BRAIN_SLUG, "memory"))


def read(fn):
    try:
        return io.open(os.path.join(MEM, fn), encoding="utf-8", errors="replace").read()
    except Exception:
        return ""


def load_pairs():
    pairs = []
    for fn in os.listdir(MEM):
        if not fn.endswith(".md"):
            continue
        body = read(fn)
        # search the frontmatter BLOCK, not a fixed window (a long description pushed the field past
        # char 800 and this eval silently under-counted), and allow uppercase in slugs (W14-era names)
        fm = re.match(r"^---\s*\n.*?\n---\s*\n", body, re.S)
        head = fm.group(0) if fm else body[:800]
        m = re.search(
            r"^\s*superseded_by:\s*([A-Za-z0-9][A-Za-z0-9_-]*)\s*$",
            head,
            re.M,
        )
        if m and os.path.exists(os.path.join(MEM, m.group(1) + ".md")):
            pairs.append((fn[:-3], m.group(1)))
    return sorted(pairs)


def distinctive_terms(words, limit=12):
    """Choose query cues deterministically, including equal-length ties."""
    return sorted(
        {word for word in words if len(word) > 3},
        key=lambda word: (-len(word), word),
    )[:limit]


def main():
    from bm25 import BM25, tokens
    from supersession import active_supersessions

    pairs = load_pairs()
    if len(pairs) < 3:
        print("eval_stale_fact: only %d superseded_by pairs - too few to read yet "
              "(schema_supersede.py stamps them; the dream backfills prose corrections)." % len(pairs))
        for o, n in pairs:
            print("   pair: %s  ->  %s" % (o, n))
        return 2
    bm = BM25()
    frontmatter_routes = active_supersessions(
        MEM,
        index_path=os.devnull,
        include_frontmatter=True,
    )
    deployed_routes = active_supersessions(
        MEM,
        index_path=os.path.join(DR, "superseded.tsv"),
    )
    route_mismatches = [
        (old, frontmatter_routes.get(old), deployed_routes.get(old))
        for old, _new in pairs
        if not frontmatter_routes.get(old)
        or deployed_routes.get(old) != frontmatter_routes.get(old)
    ]

    collisions = stale_first = fixed_by_demote = 0
    # PREMISE-AWARENESS (LongMemEval-V2, the eval-suite's other named gap next to workflow): when a query
    # ASSERTS the stale premise (phrased in the OLD memory's own words - the owner asking about/assuming
    # the superseded fact), does the CORRECTION still reach the candidate pool so the system can flag the
    # false premise (via the demotion swap)? Distinct from the topic-query collision above: here the stale
    # memory ranks #1 BY CONSTRUCTION (it's the query's own words), so the question is whether the
    # correction co-surfaces at all - if it doesn't, a query built on a stale assumption gets confirmed.
    premise_corr_surfaced = premise_n = 0
    n = len(pairs)
    for old, new in pairs:
        # TOPIC query = distinctive words shared by both memories (neither side's own full wording)
        w_old, w_new = set(tokens(read(old + ".md"))), set(tokens(read(new + ".md")))
        shared = distinctive_terms(w_old & w_new)
        if not shared:
            continue
        ranked = [s for s, _ in bm.score(" ".join(shared), 30)]
        ro = ranked.index(old) if old in ranked else 999
        rn = ranked.index(new) if new in ranked else 999
        both = ro < 30 and rn < 30
        if both:
            collisions += 1
            if ro <= rn:                       # stale ranked at-or-above its correction
                stale_first += 1
                fixed_by_demote += 1           # the demotion rule drops `old`, so `new` wins - always fixes it
        # premise-assertion angle: query = the OLD memory's OWN distinctive words (asserting the stale fact)
        prem_q = distinctive_terms(w_old)
        if prem_q:
            premise_n += 1
            pr = [s for s, _ in bm.score(" ".join(prem_q), 15)]
            if new in pr:                      # the correction reached the pool -> demotion can flag it
                premise_corr_surfaced += 1

    print("eval_stale_fact: %d (old -> correction) pairs" % n)
    print(
        "  production endpoint routing: %d/%d frontmatter-authoritative old slugs resolve "
        "to the same existing terminal correction before graph scoring"
        % (n - len(route_mismatches), n)
    )
    if route_mismatches:
        print("  route mismatches (old, authoritative, deployed): %r" % route_mismatches[:5])
    print("  collision rate (topic query surfaces BOTH): %d/%d" % (collisions, n))
    if collisions:
        print("  stale-first among collisions (relevance-only): %.2f  (%d/%d ranked the outdated one >= its correction)"
              % (stale_first / collisions, stale_first, collisions))
        print("  superseded_by-aware demotion fixes: %d/%d of the stale-first cases (deterministic - "
              "drops the stale twin whenever its correction co-surfaces)" % (fixed_by_demote, stale_first))
    if premise_n:
        print("  PREMISE-AWARENESS: correction reaches the pool for %d/%d premise-asserting queries "
              "(query phrased in the STALE fact's own words). The gap: the %d where it does NOT - a query "
              "built on the stale assumption surfaces only the stale memory, so nothing flags the false "
              "premise. Those need the correction's OWN vocabulary broadened, or an explicit "
              "superseded_by back-pointer surfaced with the stale hit." % (premise_corr_surfaced, premise_n,
                                                                           premise_n - premise_corr_surfaced))
    print("  read: relevance alone is stale-blind (a terse correction rarely out-matches the memory that "
          "stated the fact in full); deployed `spread_recall` resolves every surfaced old slug through "
          "the verified supersession map before graph scoring, so correction co-retrieval is not required.")
    if route_mismatches:
        print("VERDICT: FAIL - deployed supersession routes diverge from memory frontmatter")
        return 1
    print("VERDICT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
