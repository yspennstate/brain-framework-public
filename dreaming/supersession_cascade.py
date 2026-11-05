#!/usr/bin/env python3
"""1000_WAYS item 63: retroactive-interference cascade.

When a memory is superseded, other memories may still cite it as if it held. This finds them.
A citing memory is one that names a superseded slug (as [[slug]] or the bare slug) and names
nothing on that slug's correction chain. Each one is sorted by when it was filed against the
terminal correction:

  stale       filed AFTER the correction: written when the new value already existed,
              still pointing at the old one. Split again into inline restatements (the
              residue the item measures) and Related-list pointers (the old node routes them).
  historical  filed BEFORE the correction: right when written; a reader is routed by the
              old memory's own superseded_by, so these are left alone.
  undated     the stamps cannot order the two.

Mechanical nodes (compaction anchors, digests, index-like hubs) are counted apart: they link
the nearest memories by construction and assert nothing.

  supersession_cascade.py [--memory-dir D] [--queue PATH] [--sample N]

Reads only. With --queue it writes the stale rows as TSV (citing slug, superseded slug,
terminal correction, citing day, correction day) for a dream pass or a human to stamp.
"""
import argparse, os, random, re, sys

DREAM = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DREAM)
import supersession as sp  # noqa: E402

TOKEN_RX = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{11,}")
# A citation inside a "Related: [[a]], [[b]]" list only points; the old node's own superseded_by
# routes whoever follows it. A citation in a sentence restates the old claim. Hand read of 12 stale
# rows on 29 Oct: 6 were list pointers, 6 inline restatements (5-6 of those truly stale).
# A held-out read of 10 more inline rows found 1.5 true: the rest were lists the first marker set
# missed ("Nodes:", "related [[...", comma runs of bare slugs) or citations that already say the
# node is superseded. Both are recognised below. A third read (10 fresh rows) came to about 3 of 10:
# most supersessions here are refinements or renames whose old value still stands, which a slug-level
# test cannot see. Treat the inline count as an upper bound and each row as a question, not a defect.
LIST_MARKER_RX = re.compile(r"(?i)\b(related|kin|see also|links?|family|siblings?|nodes?)\b[^:\n\[]{0,40}:?")
LIST_ONLY_RX = re.compile(r"^[\s\[\]\w.,;()/'-]*$")
AWARE_RX = re.compile(r"(?i)supersed|its correction|was corrected|overturned|overt[ao]o?k|retired|was wrong|no longer holds")


def _in_slug_run(text, i, slug):
    """The mention sits between commas in a run of bare or bracketed slugs."""
    before = text[max(0, i - 3):i].rstrip(" [\n")
    after = text[i + len(slug):i + len(slug) + 4].lstrip("]")
    return before.endswith(",") or after[:1] == ","


def citation_use(text, slug):
    """'aware' if some mention says the node is superseded, 'pointer' if every mention only
    links (a Related-style list or a comma run of slugs), else 'inline'."""
    start, pointer_only = 0, True
    while True:
        i = text.find(slug, start)
        if i < 0:
            return "pointer" if pointer_only else "inline"
        if AWARE_RX.search(text[max(0, i - 200):i + len(slug) + 200]):
            return "aware"
        line_start = text.rfind("\n", 0, i) + 1
        para_start = max(text.rfind("\n\n", 0, i) + 2, 0)
        head = text[max(para_start, line_start - 400):i]
        marker = None
        for marker in LIST_MARKER_RX.finditer(head):
            pass
        listed = bool(marker) and bool(LIST_ONLY_RX.match(head[marker.end():]))
        if not (listed or _in_slug_run(text, i, slug)):
            pointer_only = False
        start = i + len(slug)
MECHANICAL_PREFIXES = ("compaction_", "digest_", "gist_", "hub_", "index_", "dream_", "STATE_OF_MIND",
                       "MEMORY", "MISTAKES", "OPEN_QUESTIONS", "CONSCIENCE", "CURIOSITY", "SALIENCE")


def chains(active, routes):
    """old slug -> every slug on its route to the terminal correction (old excluded)."""
    out = {}
    for old, terminal in active.items():
        seen, cur = [], old
        for _ in range(32):
            nxt = routes.get(cur)
            if not nxt or nxt in seen or nxt == old:
                break
            seen.append(nxt)
            cur = nxt
        out[old] = set(seen) | {terminal}
    return out


def stamp_of(memory_dir, slug, cache):
    if slug not in cache:
        cache[slug] = sp.memory_stamp(sp.frontmatter_block(os.path.join(memory_dir, slug + ".md")))
    return cache[slug]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--memory-dir", default=None)
    ap.add_argument("--queue", default=None)
    ap.add_argument("--sample", type=int, default=0, help="print N random stale rows for a precision read")
    a = ap.parse_args(argv)
    if a.memory_dir:
        memory_dir = a.memory_dir
    else:
        import brain
        memory_dir = brain.MEM
    active = sp.active_supersessions(memory_dir, include_frontmatter=True)
    routes = sp.load_map(memory_dir, include_frontmatter=True)
    chain = chains(active, routes)
    stamps = {}
    counts = {"stale": 0, "historical": 0, "undated": 0, "mechanical": 0, "stale_inline": 0, "stale_pointer": 0, "stale_aware": 0}
    citing_memories = {"stale": set(), "historical": set(), "undated": set()}
    stale_rows = []
    files = sp._md_files(memory_dir)
    for entry in files:
        slug = entry.name[:-3]
        if slug in active:
            continue  # itself superseded: a reader is routed past it
        try:
            text = open(entry.path, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        tokens = set(TOKEN_RX.findall(text))
        cited = [t for t in tokens if t in active and t != slug]
        if not cited:
            continue
        mechanical = slug.startswith(MECHANICAL_PREFIXES)
        for old in cited:
            if tokens & chain[old] or slug in chain[old]:
                continue  # it also names the correction: it knows
            if mechanical:
                counts["mechanical"] += 1
                continue
            terminal = active[old]
            order = sp.order_stamps(stamp_of(memory_dir, slug, stamps), stamp_of(memory_dir, terminal, stamps))
            kind = "stale" if order > 0 else "historical" if order < 0 else "undated"
            counts[kind] += 1
            citing_memories[kind].add(slug)
            if kind == "stale":
                use = citation_use(text, old)
                counts["stale_" + use] += 1
                stale_rows.append((slug, old, terminal, stamps[slug]["day"], stamps[terminal]["day"], use))
    print("memories %d | superseded %d | citations of a superseded slug with no correction named:"
          % (len(files), len(active)))
    for k in ("stale", "historical", "undated"):
        print("  %-10s %6d citations in %5d memories" % (k, counts[k], len(citing_memories[k])))
    print("  %-10s %6d citations (mechanical nodes, not counted as residue)" % ("mechanical", counts["mechanical"]))
    print("stale-assertion residue: %d inline restatements (+%d list pointers the old node routes, "
          "+%d that already say it is superseded)"
          % (counts["stale_inline"], counts["stale_pointer"], counts["stale_aware"]))
    if a.queue:
        tmp = a.queue + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("citing\tsuperseded\tcorrection\tciting_day\tcorrection_day\tuse\n")
            for row in sorted(stale_rows):
                f.write("\t".join(row) + "\n")
        os.replace(tmp, a.queue)
        print("queue: %s (%d rows)" % (a.queue, len(stale_rows)))
    if a.sample and stale_rows:
        random.seed(63)
        for row in random.sample(stale_rows, min(a.sample, len(stale_rows))):
            print("SAMPLE\t" + "\t".join(row))
    return 0


if __name__ == "__main__":
    sys.exit(main())
