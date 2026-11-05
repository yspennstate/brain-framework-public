#!/usr/bin/env python3
"""build_mistake_weights.py - the offline severity map behind mistake-first surfacing.

The owner's 07-25 ask: nodes connected to mistakes - especially the big ones - must surface
FASTER; they have not been firing fast enough. The 07-25 inventory measured why: the July-24
audit wave (145 nodes) has zero compiled reflexes, ~1.2 spread_edges per node (graph leaves),
and exactly 2 lifetime spread surfacings - while its salience sits at 99.5+. So the binding
constraint is not salience: it is that the diffusion walk treats a catastrophic scar exactly
like trivia (flat DECAY, one weak inbound edge, full MARGIN), and cross-session habituation
can then damp what little signal survives.

This builder emits mistake_weights.tsv: slug -> severity in [0,1] -> class, consumed by
spread_recall.py (inflow conductance boost, margin relief for sev>=0.8, habituation floor).
Severity is derived from signals the brain already maintains - nothing is hand-scored:

  audit    slug matches ^audit_ (an audit/mistake disposition node):
           sev = 0.35 + 0.65 * salience-charge  (the July wave carries charge 1.0 -> 1.0;
           the legacy May audit_cycle rows carry 0.0 -> 0.35, a mild tilt only)
  scar     the source memory of a compiled reflex in instincts.tsv (the gut's own ancestry):
           sev = max(prev, reflex charge) - a reflex's charge IS the graded burn size
  mistake  slug matches ^(mistake|scar)_ : same salience-charge ramp as audit
  adjacent one [[wiki-link]] hop out from any sev>=0.8 node: sev = 0.6 * parent (the ask is
           nodes CONNECTED to mistakes, so a big scar's curated neighbours ride along)

Rows below 0.20 are dropped (a zero-charge tilt is noise, not signal). Output is written
atomically (tmp + os.replace) so a concurrent hook read never sees a torn file; the consumer
is fail-open (absent/corrupt file -> {} -> behaviour byte-identical to before this change).

Run:     python build_mistake_weights.py            (seconds, stdlib only, laptop-safe)
Refresh: after an audit wave lands or reflexes compile; same cadence as build_spread_edges.
"""
import io
import os
import re
import sys
import time

if any(a in ("-h", "--help") for a in sys.argv[1:]):
    # Discovery is not execution: this file does its work at module scope, so the
    # guard must come before the first statement that runs.
    print(__doc__ or os.path.basename(__file__))
    raise SystemExit(0)


HERE = os.path.dirname(os.path.abspath(__file__))
MEM = os.path.join(os.path.expanduser("~"), ".claude", "projects", "{{BRAIN_SLUG}}", "memory")
SAL = os.path.join(HERE, "salience.tsv")
TSV = os.path.join(HERE, "instincts.tsv")
OUT = os.path.join(HERE, "mistake_weights.tsv")

FLOOR = 0.20          # below this a weight is noise - drop the row entirely
ADJ_FACTOR = 0.6      # a big scar's direct wiki-link neighbour inherits this fraction
ADJ_PARENT_MIN = 0.8  # only genuinely big mistakes propagate to neighbours
LINK = re.compile(r"\[\[([^\]|#]+?)(?:\|[^\]]*)?\]\]")
# the special pages are ledgers, not memories - spread_recall SKIPs them and so do we (a reflex
# whose source column says MISTAKES points at the ledger, not at a surfaceable node)
SPECIAL = {"MEMORY", "INDEX_ARCHIVE", "PREDICTIONS", "STATE_OF_MIND", "OPEN_QUESTIONS",
           "AUTOBIOGRAPHY", "MODEL_OF_PARTNER", "FASCINATIONS", "THE_OTHER_MINDS", "MISTAKES",
           "CONSCIENCE", "CURIOSITY", "SALIENCE", "GROWTH", "RECENT_BRAIN", "STATS",
           "RECONSOLIDATION_QUEUE"}


def _slugs():
    out = set()
    try:
        for fn in os.listdir(MEM):
            if fn.endswith(".md"):
                out.add(fn[:-3])
        dreams = os.path.join(MEM, "dreams")
        if os.path.isdir(dreams):
            for fn in os.listdir(dreams):
                if fn.endswith(".md"):
                    out.add(fn[:-3])
    except Exception:
        pass
    return out


def _salience_charge():
    """slug -> the salience builder's charge column (graded emotional weight, 0..1)."""
    out = {}
    try:
        with io.open(SAL, encoding="utf-8", errors="replace") as f:
            next(f, None)
            for ln in f:
                p = ln.rstrip("\n").split("\t")
                if len(p) >= 4 and p[0] != "fname":
                    slug = p[0][:-3] if p[0].endswith(".md") else p[0]
                    try:
                        out[slug] = max(0.0, min(1.0, float(p[3])))
                    except Exception:
                        pass
    except Exception:
        pass
    return out


def _reflex_sources():
    """source slug -> max charge over the compiled reflexes it produced."""
    out = {}
    try:
        with io.open(TSV, encoding="utf-8", errors="replace") as f:
            next(f, None)
            for ln in f:
                p = ln.rstrip("\n").split("\t")
                if len(p) >= 7 and p[1] in ("fire", "guide", "carry"):
                    try:
                        charge = float(p[7]) if len(p) >= 8 else 0.6
                    except Exception:
                        charge = 0.6
                    src = p[6].strip()
                    if src:
                        out[src] = max(out.get(src, 0.0), charge)
    except Exception:
        pass
    return out


def _links_of(slug):
    for base in (MEM, os.path.join(MEM, "dreams")):
        path = os.path.join(base, slug + ".md")
        try:
            body = io.open(path, encoding="utf-8", errors="replace").read()
        except Exception:
            continue
        return set(m.group(1).strip() for m in LINK.finditer(body))
    return set()


def build():
    slugs = _slugs()
    if not slugs:
        print("no memory corpus found; refusing to write an empty artifact", file=sys.stderr)
        return 1
    charge = _salience_charge()
    sources = _reflex_sources()

    sev, cls = {}, {}

    def _raise(slug, s, c):
        if slug in SPECIAL:
            return
        if slug in slugs and s > sev.get(slug, 0.0):
            sev[slug] = min(1.0, s)
            cls[slug] = c

    for slug in slugs:
        if slug.startswith("audit_"):
            _raise(slug, 0.35 + 0.65 * charge.get(slug, 0.0), "audit")
        elif slug.startswith(("mistake_", "scar_")):
            _raise(slug, 0.35 + 0.65 * charge.get(slug, 0.0), "mistake")
    for src, c in sources.items():
        _raise(src, c, "scar")

    # one curated hop out: the neighbours a big scar's own author linked by hand
    for slug in [s for s, v in sev.items() if v >= ADJ_PARENT_MIN]:
        for nb in _links_of(slug):
            if nb not in sev:
                _raise(nb, ADJ_FACTOR * sev[slug], "adjacent")

    rows = sorted(((s, v, cls[s]) for s, v in sev.items() if v >= FLOOR),
                  key=lambda r: (-r[1], r[0]))
    tmp = OUT + ".tmp-%d" % os.getpid()
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write("slug\tseverity\tclass\n")
        for slug, v, c in rows:
            f.write("%s\t%.3f\t%s\n" % (slug, v, c))
    os.replace(tmp, OUT)

    by_class = {}
    for _, _, c in rows:
        by_class[c] = by_class.get(c, 0) + 1
    big = sum(1 for _, v, _ in rows if v >= ADJ_PARENT_MIN)
    print("mistake_weights.tsv: %d rows (%s), %d at sev>=%.1f, built %s"
          % (len(rows), ", ".join("%s=%d" % kv for kv in sorted(by_class.items())),
             big, ADJ_PARENT_MIN, time.strftime("%Y-%m-%dT%H:%M:%S")))
    for probe in ("audit_sncc_001_strong_node_unfinished_integration_2026_07_25",
                  "audit_j24_111_non_sparse_full_repository_worktree_che_172866bef3_2026_07_24"):
        if probe in sev:
            print("  probe %s -> %.3f (%s)" % (probe, sev[probe], cls[probe]))
    return 0


if __name__ == "__main__":
    raise SystemExit(build())
