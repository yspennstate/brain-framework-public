#!/usr/bin/env python3
"""schema_extract.py - find the atemporal invariant under many episodes (Lewis & Durrant 2011: overlapping
replay across memories extracts a schema; Tse et al. 2007: a schema then accelerates assimilation of new
memories; Wagner 2004 / Yordanova 2008: sleep restructures a cluster under a newly-found rule).

The weekly gist tells the story of THIS week; a SCHEMA is the rule that holds across weeks. This scans the
memory index for a distinctive theme that recurs across many memories spanning a long time and is NOT yet
captured by a gist_/schema_ memory, and surfaces the strongest candidate. A different, slower kind of
dreaming (monthly): the dream reads the candidate and writes schema_<name>.md - an atemporal rule with a
'priors' line - so scattered episodes become one reusable prior instead of N separate scars.

  python schema_extract.py [top_n]
"""
import os, io, re, sys
from collections import defaultdict

HOME = os.path.expanduser("~")
MEM = os.path.join(HOME, ".claude", "projects", "{{BRAIN_SLUG}}", "memory")
# words too common in THIS brain to be a theme (they'd cluster everything), plus generic stopwords
STOP = set(("about above across after again against because before being below between both cannot could "
            "doing during each else ever every from have here into just like made make many more most much "
            "must never only other over same should since some such than that their them then there these "
            "they this those through under until very were what when where which while with would your "
            "memory brain session sessions owner agent agents work working this that from with into "
            "verified built build note gist scar method the and for not but you our its was are has had "
            "will can now new one two his him her they them then than what how why who all any "
            # structural / high-frequency words that are not a THEME (they'd cluster the whole corpus)
            "cycle project audit claude status final state start resume clean stage phase step done "
            "watchdog observer dashboard briefing foundation definition group update fixed bugfix docs "
            "problem solved proof lemma theorem result today night overnight goal restart running "
            "finding findings still codex general really actually thing things stuff been being").split())


def _date(slug):
    m = re.search(r"_(\d{4})_(\d{2})_(\d{2})", slug)
    if not m:
        return None
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    return y * 372 + mo * 31 + d   # a monotone day-ish index; only differences matter, not calendar exactness


SKIP = {"MEMORY.md", "INDEX_ARCHIVE.md", "RECONSOLIDATION_QUEUE.md", "PREDICTIONS.md", "OPEN_QUESTIONS.md",
        "AUTOBIOGRAPHY.md", "MODEL_OF_PARTNER.md", "STATE_OF_MIND.md", "THE_OTHER_MINDS.md",
        "FASCINATIONS.md", "RESUME.md", "INTENTIONS.md", "MISTAKES.md", "GROWTH.md"}


def _read_desc(path):
    """The frontmatter description (a curated summary) if present, else the first body line."""
    try:
        head = io.open(path, encoding="utf-8", errors="replace").read(1400)
    except Exception:
        return ""
    m = re.search(r'\ndescription:\s*["\']?(.+?)["\']?\s*\n[A-Za-z]', head, re.S)
    if m:
        return re.sub(r"\s+", " ", m.group(1))[:400]
    body = head.split("---", 2)[-1] if head.count("---") >= 2 else head
    return re.sub(r"\s+", " ", body.strip())[:200]


def main():
    top = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    # scan the actual FILES (the live index is recency-biased; the full corpus spans months)
    try:
        files = [f for f in os.listdir(MEM) if f.endswith(".md") and f not in SKIP and f[0].islower()]
    except Exception:
        return
    entries = []
    for f in files:
        slug = f[:-3]
        desc = _read_desc(os.path.join(MEM, f))
        entries.append((slug, slug.replace("_", " ") + " " + desc, _date(slug)))
    if len(entries) < 20:
        print("corpus too small to extract schemas")
        return
    covered = " ".join(s for s, _, _ in entries if s.startswith(("gist_", "schema_"))).lower()
    tok2mem = defaultdict(set)
    for slug, desc, date in entries:
        toks = set(w for w in re.findall(r"[a-zA-Z][a-zA-Z_]{4,}", desc.lower()) if w not in STOP)
        for t in toks:
            tok2mem[t].add((slug, date))
    cap = max(40, int(0.06 * len(entries)))   # a schema is a coherent cluster, not a corpus-wide word
    cands = []
    for t, mems in tok2mem.items():
        if not (5 <= len(mems) <= cap) or t in covered:
            continue
        dates = [d for _, d in mems if d]
        span = (max(dates) - min(dates)) if len(dates) >= 2 else 0
        if span < 20:   # must recur across time, not just a busy week
            continue
        cands.append((t, len(mems), span, sorted(m for m, _ in mems)))
    cands.sort(key=lambda c: -(c[1] * (c[2] ** 0.5)))
    if not cands:
        print("no cross-time schema candidate above threshold")
        return
    print("Schema candidates (a recurring cross-time theme not yet in a gist/schema):\n")
    for t, n, span, slugs in cands[:top]:
        print("* theme '%s'  - %d memories across ~%d days" % (t, n, span))
        for s in slugs[:8]:
            print("    - %s" % s)
        if len(slugs) > 8:
            print("    ... +%d more" % (len(slugs) - 8))
        print("    -> if these share ONE atemporal rule, write schema_%s_<date>.md (the rule + a 'priors:' line)\n" % t)


if __name__ == "__main__":
    main()
