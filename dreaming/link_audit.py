#!/usr/bin/env python3
"""link_audit.py - integrity of the association graph the brain recalls through. Read-only.

The brain is meant to be a graph, not a pile - spread_recall walks [[wiki-links]] to surface "this reminds
me...". Two kinds of rot degrade that silently: a [[link]] pointing at a memory that doesn't exist (recall
chases a ghost, and the SWS dream's section D is supposed to either write the missing memory or fix the
typo), and an ORPHAN - a memory with no inbound link, unreachable by association, effectively invisible no
matter how good it is. This measures both, so the dream has a real worklist instead of guessing.

A broken link referenced MANY times is a real gap the corpus keeps reaching for (write it); referenced
once is more likely a typo (fix it). An orphan that is recent or curated isn't a problem (new, or reachable
via the index); an orphan that is old and uncurated is the one genuinely lost.

  python link_audit.py [top_n]
"""
import os, io, re, sys, time
from collections import Counter, defaultdict

HOME = os.path.expanduser("~")
MEM = os.path.join(HOME, ".claude", "projects", "{{BRAIN_SLUG}}", "memory")
LINK = re.compile(r"\[\[([^\]|]+?)\]\]")
# index/ledger docs aren't memories - they don't need inbound links to be "reachable"
NOT_A_MEMORY = {"MEMORY", "INDEX_ARCHIVE", "OPEN_QUESTIONS", "RECONSOLIDATION_QUEUE", "PREDICTIONS",
                "AUTOBIOGRAPHY", "MODEL_OF_PARTNER", "STATE_OF_MIND", "THE_OTHER_MINDS", "FASCINATIONS",
                "RESUME", "INTENTIONS", "MISTAKES", "GROWTH", "SALIENCE"}


def _slug(s):
    s = s.strip()
    return s[:-3] if s.endswith(".md") else s


def _norm(s):
    # same resolution dream.py/build_brain_architecture.py use (fixed 2026-07-07): a link written
    # without the date (or with -) must still find the dated on-disk file, else we report phantoms
    s = s.strip().lower().replace("-", "_")
    return re.sub(r"_20\d\d_\d\d_\d\d$", "", s)


def _age_days(slug):
    m = re.search(r"_(\d{4})_(\d{2})_(\d{2})", slug)
    if not m:
        return 9999
    try:
        t = time.mktime((int(m.group(1)), int(m.group(2)), int(m.group(3)), 12, 0, 0, 0, 0, -1))
        return (time.time() - t) / 86400.0
    except Exception:
        return 9999


def main():
    top = int(sys.argv[1]) if len(sys.argv) > 1 else 15
    try:
        files = [f for f in os.listdir(MEM) if f.endswith(".md")]
    except Exception:
        print("no memory dir")
        return
    real = set(_slug(f) for f in files)
    # dream journals live in dreams/ - real link targets (fix 2026-07-15: two dream links read as
    # broken for months because only the top-level dir was listed)
    dreams_dir = os.path.join(MEM, "dreams")
    dream_slugs = set()
    if os.path.isdir(dreams_dir):
        dream_slugs = set(_slug(f) for f in os.listdir(dreams_dir) if f.endswith(".md"))
        real |= dream_slugs
    real_lower = set(s.lower() for s in real)   # Windows is case-insensitive: feedback_W80 == feedback_w80
    resolve_map = {}                            # date-stripped/normalized form -> real slug (lower)
    for s in real:
        resolve_map.setdefault(_norm(s), s.lower())
    curated = set()
    try:
        for ln in io.open(os.path.join(MEM, "MEMORY.md"), encoding="utf-8", errors="replace"):
            m = re.search(r"\((\S+?\.md)\)", ln)
            if m:
                curated.add(_slug(m.group(1)))
    except Exception:
        pass

    broken = Counter()             # missing target -> how many times it's linked
    broken_src = defaultdict(set)  # missing target -> who links it
    inbound = Counter()            # real target -> inbound link count
    total_links = 0
    for f in files:
        src = _slug(f)
        try:
            body = io.open(os.path.join(MEM, f), encoding="utf-8", errors="replace").read()
        except Exception:
            continue
        for raw in LINK.findall(body):
            tgt = _slug(raw)
            # a real memory slug is snake_case with an underscore, and usually a date; brackets round bare
            # prose ("[[links]]") or a number ("[[288]]") are not memory references - don't count them
            if not re.match(r"[A-Za-z0-9_]+$", tgt) or "_" not in tgt:
                continue
            if not (re.search(r"_20\d\d_\d\d_\d\d", tgt) or len(tgt) >= 10):
                continue
            # template placeholders quoted from instruction text into memory bodies
            # (dangling_link_count_includes_placeholder_noise_2026_08_14) - they pass the
            # underscore rule but are not references; counting them buries real gaps
            if tgt.lower() in ("their_slug", "memo_name", "their_name", "slug_name"):
                continue
            total_links += 1
            if tgt.lower() in real_lower:              # case-insensitive: the file exists on disk
                inbound[tgt.lower()] += 1
            elif _norm(tgt) in resolve_map:            # undated/dash link -> the dated on-disk file
                inbound[resolve_map[_norm(tgt)]] += 1
            else:
                broken[tgt] += 1
                broken_src[tgt].add(src)

    print("=" * 68)
    print(" LINK AUDIT  -  %d memories, %d slug-links" % (len(real), total_links))
    print("=" * 68)

    nb = sum(broken.values())
    print("\nBROKEN LINKS: %d references to %d non-existent memories (%.1f%% of links)"
          % (nb, len(broken), 100.0 * nb / max(total_links, 1)))
    if broken:
        print("  most-reached-for missing memories (linked N times = a gap the corpus wants filled):")
        for tgt, n in broken.most_common(top):
            kind = "WRITE (reached for repeatedly)" if n >= 3 else ("check" if n == 2 else "likely typo")
            print("    %2dx  %s   [%s]" % (n, tgt, kind))
            if n <= 2:
                print("         linked from: %s" % ", ".join(sorted(broken_src[tgt])[:3]))

    # orphans: DATED memories nothing links to, uncurated, not brand-new. Undated slugs are the legacy
    # reference/config docs that predate the date convention and are reached by NAME via the index, not by
    # association - counting them as "lost" would be the inflated read; restrict to the dated corpus.
    orphans = []
    for s in real:
        if s.upper() in NOT_A_MEMORY or s in dream_slugs:   # journals are reached via the index, not links
            continue
        age = _age_days(s)
        if age == 9999:                                # undated legacy - reached by name, not an orphan
            continue
        if inbound.get(s.lower(), 0) == 0 and s not in curated and age > 7:
            orphans.append((age, s))
    orphans.sort(reverse=True)
    print("\nORPHANS: %d DATED memories with no inbound link, uncurated, older than a week" % len(orphans))
    print("  (unreachable by association - the dream can link the worthwhile ones into a live neighbour)")
    for age, s in orphans[:top]:
        print("    %4dd  %s" % (int(age), s))
    if len(orphans) > top:
        print("    ... +%d more" % (len(orphans) - top))
    print()


if __name__ == "__main__":
    main()
