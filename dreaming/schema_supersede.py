#!/usr/bin/env python3
"""schema_supersede.py - stamp a machine-readable `superseded_by:` field on memories that a later
memory corrects, so the stale-fact/conflict eval (and recall-time demotion, later) have a real basis.

The 2025-26 agent-memory literature (Zep bi-temporal knowledge graphs; the Supersede paper; MemConflict)
isolates UPDATING - use the current value of a fact, discard the superseded value - as a failure distinct
from retrieval quality: more memory budget does NOT fix stale-fact use. The prerequisite is knowing which
memory supersedes which. This brain has 160+ memories with prose correction language ("now superseded by
W#24", "the $586k correction", "turned out wrong") but almost none machine-readable, so nothing can
measure or act on supersession. This closes the write side.

Two tiers, honest about what a fast pattern can and can't do:
  RULE tier (safe to auto-apply): an EXPLICIT link - "superseded by [[slug]]", "supersedes [[slug]]",
    "correction to [[slug]]", or a dream's "WARNING superseded: [[slug]]" note. The target is named, so
    no judgment call - stamp `superseded_by: <slug>` on the OLD memory (idempotent, append-only field).
  DREAM tier (needs model judgment): a memory whose PROSE says something was corrected but names no
    target slug ("the earlier number was wrong", "actually it's X"). A regex cannot pick the right
    earlier memory (measured ~50% false-positive on content classification), so these are only LISTED
    for the nightly dream to resolve - written to supersede_candidates.tsv, never auto-stamped.

Safety: default DRY-RUN (prints what it would stamp). --apply writes, but ONLY the frontmatter field,
ONLY on files unchanged for >120s (never races a live edit), and never on a dream-owned view file.
Idempotent: re-running stamps nothing new. Usage:
  python schema_supersede.py            # dry-run: show rule-tier stamps + count dream-tier candidates
  python schema_supersede.py --apply    # stamp the rule-tier links
"""
import os, io, re, sys, time

from supersession import load_map
from brain import _memory_write_lock

MEM = os.environ.get("BRAIN_MEM_DIR",
                     os.path.expanduser(r"~/.claude/projects/{{BRAIN_SLUG}}/memory"))
DR = os.path.dirname(os.path.abspath(__file__))
CANDIDATES = os.environ.get("BRAIN_SUPERSEDE_CANDIDATES", os.path.join(DR, "supersede_candidates.tsv"))
JUDGED = os.environ.get("BRAIN_SUPERSEDE_JUDGED", os.path.join(DR, "supersede_judged.tsv"))
INDEX = os.environ.get("BRAIN_SUPERSEDED_INDEX", os.path.join(DR, "superseded.tsv"))
SKIP = {"MEMORY", "INDEX_ARCHIVE", "MISTAKES", "CURIOSITY", "OPEN_QUESTIONS", "STATE_OF_MIND",
        "PREDICTIONS", "AUTOBIOGRAPHY", "MODEL_OF_PARTNER", "FASCINATIONS", "THE_OTHER_MINDS",
        "CONSCIENCE", "RECONSOLIDATION_QUEUE"}

# explicit link, split by DIRECTION (the two phrasings point opposite ways):
#   "THIS superseded by [[X]]"  -> THIS is old, X is the correction  (stamp THIS.superseded_by = X)
#   "THIS supersedes/corrects [[X]]" -> X is old, THIS is the correction (stamp X.superseded_by = THIS)
SUPERSEDED_BY = re.compile(r"(?:superseded\s+by|replaced\s+by)\W{0,20}?\[\[([A-Za-z0-9_-]+)\]\]", re.I)
SUPERSEDES = re.compile(r"(?:supersedes|correction\s+to|corrects)\W{0,20}?\[\[([A-Za-z0-9_-]+)\]\]", re.I)
# prose correction WITHOUT a target slug - dream-tier only
PROSE = re.compile(r"\b(?:turned out (?:to be )?wrong|was wrong|the earlier .{0,20}? was|"
                   r"actually (?:it'?s|the |we)|\$\d[\d,.]*k? correction|stale[- ]mark)\b", re.I)


def slug_of(fn):
    return fn[:-3]


def canon_slugs():
    return {slug_of(fn) for fn in os.listdir(MEM) if fn.endswith(".md")}


def frontmatter(body):
    """The frontmatter block itself - a fixed char window silently missed the field behind a long
    description (found 2026-07-09: a stamp landed past char 800 and every consumer read 'no field')."""
    m = re.match(r"^---\s*\n.*?\n---\s*\n", body, re.S)
    return m.group(0) if m else body[:800]


def has_field(body, field):
    return re.search(r"^\s*%s:" % field, frontmatter(body), re.M) is not None


def read_head(path, n=800):
    try:
        return io.open(path, encoding="utf-8", errors="replace").read(n)
    except Exception:
        return ""


def declared_partial(old_slug, new_slug, old_text=None):
    """True when the OLD memory's own frontmatter names new_slug as a PARTIAL successor (superseded_in_part, or
    the older partially_superseded_by). 2026-09-22: the rule tier below turns prose such as
    "supersedes [[old]]" into a whole-node route - into the ledger on every run, into the old memory's frontmatter
    with --apply - and a later order that overtakes one clause of an earlier one is exactly where that prose gets
    written. The whole-node route would send every recall of the old memory to the new one and lose the clauses
    that still stand (the 08-22 partial-supersession measurement: 1 of 22 predecessors returned). The author's
    structured declaration outranks a regex over prose. Fail-open to False (the old behaviour)."""
    try:
        from supersession import partial_successors
        text = old_text if old_text is not None else read_head(os.path.join(MEM, old_slug + ".md"), 1024 * 1024)
        fm = frontmatter(text)
        # the memory's own WHOLE stamp for the same successor is the more explicit statement - it keeps its route
        own_whole = re.search(r"^\s*superseded_by:\s*%s\s*$" % re.escape(new_slug), fm, re.M)
        return new_slug in partial_successors(fm) and not own_whole
    except Exception:
        return False


def stamp(path, body, target):
    """Insert `superseded_by: <target>` into the frontmatter block (append-only field)."""
    m = re.match(r"^(---\s*\n.*?\n)(---\s*\n)", body, re.S)
    if not m:
        return None  # no frontmatter block - don't invent one
    if has_field(body, "superseded_by"):
        return None  # idempotent
    new = m.group(1) + "superseded_by: " + target + "\n" + m.group(2) + body[m.end():]
    return new


def load_judged():
    """Slugs a model pass already judged (waking session or dream) - never re-queued for the dream.
    The 2026-07-09 pass judged all 42 then-candidates: 5 stamped, the rest are correction RECORDS or
    inline self-corrections (healthy memories the prose regex fires on), recorded here so no future
    pass re-walks them. Delete a row to re-open a judgment."""
    judged = {}
    try:
        for i, ln in enumerate(io.open(JUDGED, encoding="utf-8", errors="replace")):
            if i == 0:
                continue
            p = ln.rstrip("\n").split("\t")
            if len(p) >= 2 and p[0]:
                judged[p[0]] = p[1]
    except Exception:
        pass
    return judged


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # Discovery is not execution: this script writes, so a help request must
        # exit before any work. See script_help_flag_can_execute_work.
        print(__doc__ or os.path.basename(__file__))
        return
    apply = "--apply" in sys.argv
    slugs = canon_slugs()
    judged = load_judged()
    now = time.time()
    rule_hits, dream_cands, held_partial = [], [], []
    for fn in sorted(os.listdir(MEM)):
        if not fn.endswith(".md") or slug_of(fn) in SKIP or fn.startswith("dream_"):
            continue
        p = os.path.join(MEM, fn)
        try:
            body = io.open(p, encoding="utf-8", errors="replace").read()
        except Exception:
            continue
        me = slug_of(fn)

        def _subject(m):
            """A [[link]] preceding the phrase in the same sentence means the sentence is a THIRD-PARTY
            report - '[[B]] ... superseded by [[X]]' is a memory REPORTING B->X, not declaring itself
            superseded. The unguarded rule stamped the REPORTER (found 2026-07-09: an agent44 corpus-audit
            memory got stamped superseded_by example rules because it reported another pair's supersession -
            writing-about read as being). Third-party pairs go to the JUDGMENT queue, never auto-stamp:
            the judged ledger already holds a case where the right verdict for an explicitly-reported
            subject was 'keep' (a self-contained feedback rule). Deliberately does NOT split on ';' -
            over-routing to judgment is safe, over-stamping is the bug."""
            back = body[max(0, m.start() - 200):m.start()]
            tail = re.split(r"[.!?]\s|\n- |\n\n", back)[-1]
            subj = re.findall(r"\[\[([A-Za-z0-9_-]+)\]\]", tail)
            return subj[-1] if subj else None

        # direction-aware: (old_slug, correction_slug) - stamp old_slug.superseded_by = correction_slug
        pair = None
        for m in SUPERSEDED_BY.finditer(body):
            t = m.group(1)
            if t not in slugs or t == me:
                continue
            s = _subject(m)
            if s and s != me:
                # queue the reported subject for judgment - unless already judged or already stamped
                # (a report about a resolved pair adds nothing)
                if s in slugs and s not in judged and not has_field(
                            read_head(os.path.join(MEM, s + ".md"), 1024 * 1024), "superseded_by"):
                    dream_cands.append((s, "third-party: %s reports superseded by %s" % (me, t)))
                continue
            pair = (me, t)  # THIS is old, t corrects it
            break
        if not pair:
            for m in SUPERSEDES.finditer(body):
                t = m.group(1)
                if t not in slugs or t == me:
                    continue
                s = _subject(m)
                if s and s != me:
                    if t not in judged and not has_field(
                            read_head(os.path.join(MEM, t + ".md"), 1024 * 1024), "superseded_by"):
                        dream_cands.append((t, "third-party: %s reports %s supersedes it" % (me, s)))
                    continue
                pair = (t, me)  # t is old, THIS corrects it
                break
        if pair and declared_partial(pair[0], pair[1], body if pair[0] == me else None):
            held_partial.append((pair[0], pair[1], fn))
        elif pair:
            rule_hits.append((pair[0], pair[1], fn))
        elif PROSE.search(body) and not has_field(body, "superseded_by") and me not in judged:
            snippet = PROSE.search(body).group(0)
            dream_cands.append((me, snippet))

    # Merge, do not reconstruct from frontmatter alone. The official writer publishes the
    # ledger before stamping old frontmatter; a crash in that window leaves a valid route that
    # nightly schema repair must preserve.
    index_tmp = "%s.tmp.%d.%d" % (INDEX, os.getpid(), time.time_ns())
    # Read OUTSIDE the lock (one frontmatter per route, ~450 files): which existing routes does the old memory itself
    # declare partial? A route added between this read and the rebuild is checked on the next run. Fail-open: {}.
    try:
        partial_pairs = {(o, n) for o, n in load_map(MEM, index_path=INDEX, include_frontmatter=True).items()
                         if declared_partial(o, n)}
    except Exception:
        partial_pairs = set()
    try:
        with _memory_write_lock():
            routes = load_map(MEM, index_path=INDEX, include_frontmatter=True)
            for old, new, _ in rule_hits:
                # Structural state is authoritative. Stale prose may discover a missing
                # route, but must never overwrite a ledger/frontmatter correction.
                routes.setdefault(old, new)
            # ...except where the old memory's OWN frontmatter says that very successor overtook only part of it
            # (2026-09-23). Two such rows were live when measured - two notes each declared
            # `partially_superseded_by: <X>  # <the part>` and the ledger still routed them wholly to X, so recall collapsed them and the sections their authors said
            # stand could not be reached. This rebuild carried the rows forward every night; it now drops them.
            dropped_partial = []
            with io.open(index_tmp, "w", encoding="utf-8", newline="\n") as f:
                f.write("old\tcorrection\n")
                for old in sorted(routes):
                    new = routes[old]
                    if old in slugs and new in slugs and old != new:
                        if (old, new) in partial_pairs:
                            dropped_partial.append((old, new))
                            continue
                        f.write("%s\t%s\n" % (old, new))
                f.flush()
                os.fsync(f.fileno())
            os.replace(index_tmp, INDEX)
            for old, new in dropped_partial:
                print("  dropped whole route %s -> %s: the old memory declares that successor PARTIAL"
                      % (old[:60], new[:60]))
    except Exception as err:
        try:
            os.remove(index_tmp)
        except OSError:
            pass
        print("schema_supersede: ledger refresh FAILED: %s" % err, file=sys.stderr)
        return 2

    print("schema_supersede: %d explicit rule-tier links, %d prose candidates for the dream"
          " (%d previously judged, skipped)" % (len(rule_hits), len(dream_cands), len(judged)))
    for older, newer, fn in rule_hits[:20]:
        print("  RULE  %s  <-superseded_by-  %s" % (older[:48], newer[:48]))
    if held_partial:
        print("  %d prose link(s) held back: the old memory declares that successor PARTIAL (superseded_in_part)"
              % len(held_partial))
        for older, newer, fn in held_partial[:10]:
            print("  PART  %s  <-in part-  %s" % (older[:48], newer[:48]))

    # dream-tier: write the candidate list for the nightly model pass (never auto-stamp)
    try:
        with io.open(CANDIDATES, "w", encoding="utf-8") as f:
            f.write("slug\tcorrection_phrase\n")
            for me, snip in dream_cands:
                f.write("%s\t%s\n" % (me, snip.replace("\t", " ")[:80]))
    except Exception:
        pass

    if not apply:
        print("  (dry-run - re-run with --apply to stamp the %d rule-tier links; "
              "%d prose candidates -> supersede_candidates.tsv for the dream)"
              % (len(rule_hits), len(dream_cands)))
        return

    stamped = 0
    for older, newer, fn in rule_hits:
        # stamp the OLD memory as superseded_by the CORRECTION
        op = os.path.join(MEM, older + ".md")
        try:
            if not os.path.exists(op) or now - os.path.getmtime(op) < 120:
                continue  # missing, or too fresh (avoid racing a live edit)
            body = io.open(op, encoding="utf-8", errors="replace").read()
            if has_field(body, "superseded_by"):
                continue
            new = stamp(op, body, newer)
            if new and new != body:
                tmp = op + ".tmp"
                io.open(tmp, "w", encoding="utf-8").write(new)
                os.replace(tmp, op)
                stamped += 1
        except Exception:
            continue
    print("  stamped %d `superseded_by:` fields (older memory points at its correction)" % stamped)


if __name__ == "__main__":
    raise SystemExit(main() or 0)
