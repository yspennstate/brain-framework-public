#!/usr/bin/env python3
"""reactivation.py - targeted memory reactivation (TMR): find the memories that MATTER but have gone
DORMANT, so the dream can rehearse them before they fade out of reach.

Grounded: Rasch et al. 2007; Rudoy et al. 2009 (TMR) - replaying a cue tied to a specific memory during
sleep selectively strengthens THAT memory; the brain rescues traces by reactivating them, not by leaving
them to luck. Tononi & Cirelli (SHY) - sleep down-scales synapses globally so the un-reactivated fade and
the reactivated survive with better signal-to-noise. Wixted (disuse) - forgetting is driven by lack of
retrieval, not just age; a still-important memory that simply stops being retrieved decays anyway.

Why this is additive, not a duplicate of salience.py: salience already fades the boring old middle and
keeps the vivid/load-bearing high from STATIC signals (charge, hubs, curation, type). What it can't see is
DYNAMIC reactivation - whether the running sessions actually pull a memory up. A memory salience rates
important that the sessions never co-read is exactly the disuse case salience is blind to. This reads the
reactivation trace (coactivation.tsv) that salience ignores and surfaces the important-but-dormant ones.

Honest about the signal: coactivation is young and sparse (only a few percent of the corpus has been
co-read at all), so "not co-read" ALONE means little - almost everything looks dormant. So dormancy is
only ever flagged for a memory that is ALSO important (curated / charged / a hub / a value) and not itself
recent - a small, hand-reviewable rescue list, never a forget list. Forgetting stays with salience.py and
tag_transient.py; this only ever RESCUES. Read-only; writes nothing.

  python reactivation.py [top_n]
"""
import os, io, re, sys
from collections import defaultdict

HOME = os.path.expanduser("~")
MEM = os.path.join(HOME, ".claude", "projects", "{{BRAIN_SLUG}}", "memory")
DR = os.path.join(HOME, ".claude", "dreaming")


def _coactivation_strength():
    """slug -> summed weight of every co-read edge touching it (0 if never co-read). The positive
    'this memory is alive' signal; absence is weak evidence given how young the trace is."""
    strength = defaultdict(float)
    try:
        for ln in io.open(os.path.join(DR, "coactivation.tsv"), encoding="utf-8", errors="replace"):
            p = ln.rstrip("\n").split("\t")
            if len(p) >= 3 and p[0] != "a":
                try:
                    w = float(p[2])
                except Exception:
                    continue
                strength[p[0]] += w
                strength[p[1]] += w
    except Exception:
        pass
    return strength


def _salience():
    """slug(.md stripped) -> (salience_float, why_tags_string) from salience.py's sidecar."""
    out = {}
    try:
        with io.open(os.path.join(DR, "salience.tsv"), encoding="utf-8", errors="replace") as f:
            next(f, None)
            for ln in f:
                p = ln.rstrip("\n").split("\t")
                if len(p) >= 5:
                    slug = p[0][:-3] if p[0].endswith(".md") else p[0]
                    try:
                        s = float(p[1])
                    except Exception:
                        s = 0.0
                    out[slug] = (s, p[4])
    except Exception:
        pass
    return out


def _age_days(slug):
    m = re.search(r"_(\d{4})_(\d{2})_(\d{2})", slug)
    if not m:
        return 0
    import time
    try:
        t = time.mktime((int(m.group(1)), int(m.group(2)), int(m.group(3)), 12, 0, 0, 0, 0, -1))
        return (time.time() - t) / 86400.0
    except Exception:
        return 0


def importance(why):
    """0..1 from salience.py's own 'why' tags - the brain's static verdict that a memory matters:
    curated into the index, emotionally charged, a link hub, or a value/feedback/user memory."""
    sc = 0.0
    if "curated" in why:
        sc += 0.5
    if "hub" in why:
        sc += 0.35
    if "charged+" in why or "charged-" in why or "charged" in why:
        sc += 0.2
    if "feedback" in why or "user" in why:
        sc += 0.35
    return min(1.0, sc)


def main():
    top = int(sys.argv[1]) if len(sys.argv) > 1 else 12
    coact = _coactivation_strength()
    sal = _salience()
    if not sal:
        print("no salience.tsv yet - run salience.py first")
        return

    live = sum(1 for s in coact if coact[s] > 0)
    print("=" * 64)
    print(" REACTIVATION (TMR)  -  important but dormant -> rehearse before they fade")
    print("=" * 64)
    print("\nreactivation coverage: %d / %d memories co-read at least once (%.0f%%)"
          % (live, len(sal), 100.0 * live / max(len(sal), 1)))
    print("(coactivation is young - dormancy is only flagged where the memory also MATTERS)\n")

    cands = []
    for slug, (s, why) in sal.items():
        imp = importance(why)
        if imp < 0.5:                       # only ever rescue what the brain itself rates important
            continue
        if coact.get(slug, 0.0) > 0:        # already being reactivated - alive, no rescue needed
            continue
        if "recent" in why:                 # freshly written - not dormant, just new
            continue
        age = _age_days(slug)
        if age < 21:                        # give it time before calling it dormant
            continue
        # dormancy score: important, old, and salience already starting to let go
        score = imp + min(0.5, age / 180.0) + (0.3 if s < 90 else 0.0)
        cands.append((score, imp, age, s, slug, why))

    if not cands:
        print("nothing important has gone dormant - the vivid set is being reactivated")
        return
    cands.sort(reverse=True)
    print("Rescue candidates (important, not recently co-read, not fresh - the dream should READ each")
    print("before acting; re-link it to a live neighbour or surface it at wake to rehearse the trace):\n")
    for score, imp, age, s, slug, why in cands[:top]:
        print("  * %s" % slug)
        print("      importance %.2f (%s), salience %.0f, age %dd, co-reads 0"
              % (imp, why.strip(","), s, int(age)))
    if len(cands) > top:
        print("\n  ... +%d more dormant-important memories" % (len(cands) - top))
    print()


if __name__ == "__main__":
    main()
