#!/usr/bin/env python3
"""reconsolidate.py - how a contradicted belief should update, decided by surprise x strength rather than
by panic.

Grounded: Nader, Schafe & LeDoux 2000 (a recalled memory returns to a LABILE state and must reconsolidate
over time, it is not overwritten instantly); Sevenster, Beckers & Kindt 2013 (prediction error GATES the
update - no mismatch, no change; too big a mismatch forms a NEW memory rather than editing the old);
Wang, de Oliveira Alvares & Nader 2009 (older and stronger memories resist destabilization).

The failure this prevents runs both ways: a session that panics and OVERWRITES a hard-won belief on one
noisy contradiction, and a session that NOTICES a contradiction and silently drops it, leaving a stale
belief. The rule: a session that hits a belief contradicted by new evidence does NOT edit the memory
mid-session - it appends one line to RECONSOLIDATION_QUEUE.md. This tool scores each queued belief's
STRENGTH (salience + age + type) and, given the session's SURPRISE tag, recommends a route the dream then
executes in its reconsolidation window - preserving the loser, never deleting.

Queue line format:
  - [ ] <memory-slug> | contradicted-by: <one line> | surprise: <low|med|high> | <date> | <session>

Usage:  python reconsolidate.py          # routing recommendations for the open queue
"""
import os, io, re, time, sys

HOME = os.path.expanduser("~")
MEM = os.path.join(HOME, ".claude", "projects", "{{BRAIN_SLUG}}", "memory")
DREAMING = os.path.join(HOME, ".claude", "dreaming")
sys.path.insert(0, DREAMING)
from memory_date import age_days as md_age
QUEUE = os.environ.get("RECON_QUEUE", os.path.join(MEM, "RECONSOLIDATION_QUEUE.md"))
SAL = os.path.join(DREAMING, "salience.tsv")


def _salience():
    m = {}
    try:
        with io.open(SAL, encoding="utf-8", errors="replace") as f:
            next(f, None)
            for ln in f:
                p = ln.split("\t")
                if len(p) >= 2:
                    slug = p[0][:-3] if p[0].endswith(".md") else p[0]
                    try:
                        m[slug] = float(p[1])
                    except Exception:
                        pass
    except Exception:
        pass
    return m


def _meta(slug):
    """(type, age_days) for a memory - older + value/feedback-typed resist change more. Age uses the
    canonical birth-date (slug -> created: -> description-date -> mtime), NOT bare mtime, which the
    repo-sync keeps resetting to ~now - so an undated memory with a created: stamp now ages right."""
    path = os.path.join(MEM, slug + ".md")
    typ, age = "", 999.0
    try:
        head = io.open(path, encoding="utf-8", errors="replace").read(1500)
        age, _src = md_age(slug, head, path)
        # the real `type:` line, NOT the `node_type:` above it (a plain `type:\s*` grabs node_type first)
        m = re.search(r"(?:^|\n)[ \t]*type:[ \t]*(\w+)", head)
        if m:
            typ = m.group(1)
    except Exception:
        pass
    return typ, age


def strength(slug, sal):
    """0..1 - how hard the belief is to destabilize."""
    s = sal.get(slug, 0.0)
    typ, age = _meta(slug)
    sc = min(0.5, s / 100.0) + min(0.3, age / 120.0)
    if typ in ("user", "feedback"):   # values / preferences / scars - the highest bar
        sc += 0.3
    return min(1.0, sc), typ, age, s


def route(surprise, st):
    if surprise == "low":
        return "LEAVE", "below the prediction-error threshold - churning it is noise (no surprise, no update)"
    if surprise == "high":
        return "BRANCH", ("a big mismatch usually means the CONTEXT changed, not that the old belief was "
                          "simply false - write a new dated memory, mark the old superseded, keep both")
    if st >= 0.6:
        return "TAG-LABILE (need >=2 corroborating hits)", ("a hard-won belief - one contradiction should "
               "not flip it (old/strong resists); tag it labile and require corroboration before editing")
    return "UPDATE-IN-PLACE", ("weakly held and a real mismatch - reconsolidate: update the assertion, move "
                               "the old wording to a superseded block at the bottom, keep the links")


def main():
    if not os.path.exists(QUEUE):
        print("no reconsolidation queue (nothing contradicted yet)")
        return
    sal = _salience()
    rows = []
    for ln in io.open(QUEUE, encoding="utf-8"):
        if not ln.lstrip().startswith("- [ ]") or "<" in ln:
            continue  # skip placeholder/format-example lines
        sm = re.search(r"- \[ \]\s*([^\s|]+)", ln)
        if not sm:
            continue
        slug = sm.group(1)[:-3] if sm.group(1).endswith(".md") else sm.group(1)
        su = re.search(r"surprise:\s*(low|med|medium|high)", ln, re.I)
        surprise = (su.group(1).lower() if su else "med")
        surprise = "med" if surprise == "medium" else surprise
        st, typ, age, s = strength(slug, sal)
        r, why = route(surprise, st)
        rows.append((slug, surprise, st, typ, age, r, why))
    if not rows:
        print("reconsolidation queue empty (no open items)")
        return
    print("Reconsolidation routes (%d open):\n" % len(rows))
    for slug, surprise, st, typ, age, r, why in rows:
        tag = ("/" + typ) if typ else ""
        print("* %s  [strength %.2f%s, age %dd, surprise %s]" % (slug, st, tag, int(age), surprise))
        print("    -> %s: %s" % (r, why))


if __name__ == "__main__":
    main()
