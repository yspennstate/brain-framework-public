#!/usr/bin/env python3
"""curiosity.py - turn the brain's appetite from a charter into a mechanism.

FASCINATIONS.md already names the abiding through-lines (kappa, forgetting kernels, RMT, the
transfer move) - but it's hand-written, extended only when the dream happens to notice something.
OPEN_QUESTIONS.md is the opposite: task residue, loose ends waiting to be closed. Between them
there was no mechanism that GENERATES fresh curiosity from what the brain is actually doing - the
information-gap a person feels when something doesn't fit (Loewenstein 1994), the pull of a
forecast that came out wrong (Berlyne 1960; curiosity as the drive to reduce prediction error).

This computes that. It reads the brain's own signals and surfaces the gaps worth wanting to close:

  A. SURPRISE - the biggest prediction errors (predictions.tsv). A confident miss is a standing
     question: my model of the world was wrong HERE. Understanding why updates the model.
  B. INCONGRUITY - memory pairs that co-activate strongly (Hebbian, coactivation.tsv) yet are NOT
     wiki-linked and share little vocabulary. The brain keeps reaching for both together without a
     model of why. That gap between "these co-occur" and "I can't say what links them" is exactly
     the felt itch of curiosity, and closing it is generative (it bridges two regions at once).
  C. LONELY-SALIENT - memories with high salience/charge but few graph connections. Something that
     MATTERS and stands nearly alone. What does it connect to that I haven't drawn yet?

Each gap is scored by a wonder heuristic: it should sit at the EDGE of what's known (the seeds are
salient/real, so the gap is felt, not vacuous), be GENERATIVE (bridges distant topics), and be LIVE
(recent, unresolved). Writes brain/CURIOSITY.md (the live computed appetite) and prints a one-line
digest the wake ritual surfaces, so a session with slack reaches for what it's itching to understand
instead of sitting idle. The dream can promote a durable one into FASCINATIONS or drop the rest.

Read-only over the brain except brain/CURIOSITY.md. Usage: python curiosity.py [memory_dir] [brain_dir]
"""
import os, re, io, sys, math
from collections import defaultdict

MEM_DEFAULT = os.path.expanduser(r"~/.claude/projects/{{BRAIN_SLUG}}/memory")
BRAIN_DEFAULT = os.path.expanduser(r"~/brain-repo/01_ai_consciousness/brain")
DR = os.path.dirname(os.path.abspath(__file__))
COACT = os.path.join(DR, "coactivation.tsv")
EDGES = os.path.join(DR, "spread_edges.tsv")
PREDS = os.path.join(DR, "predictions.tsv")
SAL = os.path.join(DR, "salience.tsv")

LINK_RE = re.compile(r"\[\[([^\]]+?)\]\]")
DATE_TAIL = re.compile(r"_\d{4}_\d{2}_\d{2}$")
STOP = set(("the a an and or of to in for on with is are was were be this that it its as at by from into "
            "over per not no new one two use used using via etc when then than but our we you your the "
            "he she they them their what which who how why can do does did has have had will would could a "
            "from with 2026 2027 owner brain memory dream night session").split())
SURPRISE_MIN = 1.0     # bits; below this a "miss" was well-hedged, no real gap
COACT_MIN = 1.2        # only pairs the brain reaches for together often enough to itch
VOCAB_MAX = 0.16       # incongruity: they must share LITTLE language (else the link is obvious)
LONELY_DEG = 3.0       # graph degree below which a salient memory counts as standing alone
TOPN = 8
# the special pages aren't memories - they're curated indices/logs deliberately kept OUT of the
# association graph, so they'd read as "lonely" (degree 0) though they're really hubs. Exclude them.
SKIP = {"MEMORY", "INDEX_ARCHIVE", "PREDICTIONS", "STATE_OF_MIND", "OPEN_QUESTIONS", "AUTOBIOGRAPHY",
        "MODEL_OF_PARTNER", "FASCINATIONS", "THE_OTHER_MINDS", "MISTAKES", "CURIOSITY", "SALIENCE",
        "GROWTH", "RECENT_BRAIN", "STATS"}


def norm(s):
    s = s.strip()
    if s.endswith(".md"):
        s = s[:-3]
    return DATE_TAIL.sub("", s.replace("-", "_").lower())


def load_salience():
    sal, charge = {}, {}
    try:
        with io.open(SAL, encoding="utf-8", errors="replace") as f:
            next(f, None)
            for ln in f:
                p = ln.split("\t")
                if len(p) >= 4:
                    slug = p[0][:-3] if p[0].endswith(".md") else p[0]
                    try:
                        sal[slug] = float(p[1]); charge[slug] = float(p[3])
                    except Exception:
                        pass
    except Exception:
        pass
    return sal, charge


def load_degree():
    deg = defaultdict(float)
    try:
        with io.open(EDGES, encoding="utf-8", errors="replace") as f:
            next(f, None)
            for ln in f:
                p = ln.rstrip("\n").split("\t")
                if len(p) >= 3 and p[0] != "a":
                    try:
                        w = float(p[2])
                    except Exception:
                        continue
                    deg[p[0]] += w; deg[p[1]] += w
    except Exception:
        pass
    return deg


def index_desc(mem):
    """slug -> its MEMORY.md one-liner (proxy text for vocab overlap + a legible label)."""
    out = {}
    try:
        with io.open(os.path.join(mem, "MEMORY.md"), encoding="utf-8", errors="replace") as f:
            for ln in f:
                m = re.match(r"- \[([^\]]+?)\.md\]", ln)
                if m:
                    out[m.group(1)] = ln.strip()
    except Exception:
        pass
    return out


def words(s):
    return set(w for w in re.findall(r"[a-zA-Z][a-zA-Z_]{2,}", (s or "").lower()) if w not in STOP)


def linked(mem, a, b):
    """Is there a wiki-link either way between memories a and b? (targeted read, only for candidates.)"""
    na, nb = norm(a), norm(b)
    for src, tgt in ((a, nb), (b, na)):
        try:
            text = io.open(os.path.join(mem, src + ".md"), encoding="utf-8", errors="replace").read()
        except Exception:
            continue
        for m in LINK_RE.findall(text):
            if norm(m) == tgt:
                return True
    return False


def surprise_gaps(sal):
    gaps = []
    try:
        with io.open(PREDS, encoding="utf-8", errors="replace") as f:
            next(f, None)
            for ln in f:
                p = ln.rstrip("\n").split("\t")
                if len(p) < 7 or p[0] == "id":
                    continue
                try:
                    outcome, bits = p[3], float(p[5])
                except Exception:
                    continue
                if outcome == "miss" and bits >= SURPRISE_MIN:
                    claim = p[6].strip()
                    q = (f"My forecast came out wrong here ({bits:.1f} bits of surprise): \"{claim}\". "
                         f"What did I misread about how this works?")
                    gaps.append((bits * 1.2, "surprise", q, [p[1]]))
    except Exception:
        pass
    return gaps


def incongruity_gaps(mem, sal, idx):
    # KNOWN LIMITATION (confirmed by the dream, 2026-07-08): some of these are recency artifacts, not
    # real incongruities - two freshly-written, heavily-linked memories co-fire because every recent
    # session read them together, not because of any hidden connection (e.g. an affiliation fact next
    # to a method memory). Low vocab overlap, which we read as "the felt gap", is then just "unrelated".
    # A cheap graph signal can't separate a surprising-real co-occurrence from a co-read-of-the-week; the
    # dream's / a session's judgment is the backstop (the dream sees these and lets them fade). Left as a
    # candidate pool to be judged, not asserted - deliberately not over-filtered on a moving co-read graph.
    gaps = []
    pairs = []
    try:
        with io.open(COACT, encoding="utf-8", errors="replace") as f:
            next(f, None)
            for ln in f:
                p = ln.rstrip("\n").split("\t")
                if len(p) >= 3 and p[0] != "a":
                    try:
                        pairs.append((float(p[2]), p[0], p[1]))
                    except Exception:
                        pass
    except Exception:
        pass
    pairs.sort(reverse=True)
    used = set()                            # each memory appears in at most one incongruity gap...
    for w, a, b in pairs[:60]:              # ...so we surface DISTINCT itches, not one cluster 3x
        if w < COACT_MIN:
            break
        if a in SKIP or b in SKIP or a in used or b in used:
            continue
        wa, wb = words(idx.get(a, a.replace("_", " "))), words(idx.get(b, b.replace("_", " ")))
        if wa and wb:
            jac = len(wa & wb) / len(wa | wb)
        else:
            jac = 0.0
        if jac > VOCAB_MAX:                 # they share language -> the link is already obvious
            continue
        if linked(mem, a, b):               # already connected -> no gap
            continue
        ms = (sal.get(a, 40) + sal.get(b, 40)) / 2.0
        score = w * (1.0 - jac) * (0.5 + ms / 200.0)
        q = (f"`{a}` and `{b}` keep surfacing together, but I've never connected them and they share "
             f"almost no language. What's the link I keep feeling but haven't drawn?")
        gaps.append((score, "incongruity", q, [a, b]))
        used.add(a); used.add(b)
    return gaps


def _recency(slug):
    """A crude live-ness weight from the date in the slug: a recent unlinked memory is a live thread;
    an old settled one is closed. No wall-clock needed - later date string sorts higher. Undated -> mid."""
    m = re.search(r"_(\d{4})_(\d{2})(?:_(\d{2}))?$", slug)
    if not m:
        return 0.6
    ym = int(m.group(1)) * 12 + int(m.group(2))
    # map 2026-01..2027-12 -> ~0.4..1.3; older feedback sinks, this week's findings rise
    return max(0.4, min(1.3, 0.4 + (ym - 24312) * 0.05))


def lonely_salient_gaps(sal, charge, deg):
    gaps = []
    for slug, s in sal.items():
        if slug in SKIP or s < 90 or charge.get(slug, 0) < 0.66:
            continue
        d = deg.get(slug, 0.0)
        if d > LONELY_DEG:
            continue
        # a settled preference (an old high-charge feedback rule) is understood, not a gap - recency
        # separates a live unlinked finding from a closed one, so those don't flood the appetite.
        score = charge.get(slug, 0) * (s / 100.0) * (2.0 / (1.0 + d)) * _recency(slug)
        q = (f"`{slug}` matters (high salience, charged) but stands almost alone in the association "
             f"graph. What does it connect to that I haven't drawn - which live thread is it really part of?")
        gaps.append((score, "lonely-salient", q, [slug]))
    return gaps


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # Discovery is not execution: this script writes, so a help request must
        # exit before any work. See script_help_flag_can_execute_work.
        print(__doc__ or os.path.basename(__file__))
        return
    mem = sys.argv[1] if len(sys.argv) > 1 else MEM_DEFAULT
    brain = sys.argv[2] if len(sys.argv) > 2 else BRAIN_DEFAULT
    sal, charge = load_salience()
    deg = load_degree()
    idx = index_desc(mem)

    gaps = surprise_gaps(sal) + incongruity_gaps(mem, sal, idx) + lonely_salient_gaps(sal, charge, deg)
    # Priority tier before score: a SURPRISE gap (a real forecast miss) is unambiguously a genuine gap,
    # while an incongruity can be a co-read-of-the-week artifact needing judgment (see incongruity_gaps).
    # So lead with the confirmed-genuine kind, then rank by pull within tier - not a threshold on the
    # moving co-read graph, just an ordering that puts the surest appetite first.
    TIER = {"surprise": 0}   # surprise leads (confirmed-genuine); the judgment-needed kinds rank by pull
    gaps.sort(key=lambda g: (TIER.get(g[1], 1), -g[0]))
    # Fill top-N but cap each wellspring, so a plentiful weak source (lonely-salient over old settled
    # feedback) can't drown the rare felt gaps. Real curiosity is diverse in kind, not one shape six times.
    CAP = {"lonely-salient": 2, "surprise": 4, "incongruity": 4}
    seen, per, top = set(), defaultdict(int), []
    for score, kind, q, seeds in gaps:
        key = q[:80]
        if key in seen or per[kind] >= CAP.get(kind, 3):
            continue
        seen.add(key); per[kind] += 1
        top.append((score, kind, q, seeds))
        if len(top) >= TOPN:
            break

    if brain and os.path.isdir(brain):
        with io.open(os.path.join(brain, "CURIOSITY.md"), "w", encoding="utf-8") as f:
            f.write("# What I'm itching to understand (computed)\n\n")
            f.write("The live appetite, detected from the brain's own signals - distinct from "
                    "OPEN_QUESTIONS.md (task residue) and FASCINATIONS.md (the hand-curated abiding "
                    "through-lines). These are freshly-felt information-gaps: a forecast that came out "
                    "wrong, two memories that keep firing together with no drawn link, something salient "
                    "standing alone. Regenerated by curiosity.py; the dream promotes a durable one into "
                    "FASCINATIONS or lets it fade. A session with slack should reach for the top of this list.\n\n")
            for score, kind, q, seeds in top:
                f.write(f"- **[{kind}]** {q}  _(pull {score:.2f})_\n")
            if not top:
                f.write("_No sharp gap right now - the model fits what it's seen. That's allowed._\n")

    if top:
        s0 = top[0]
        digest = f"curiosity: {len(top)} live gaps. Sharpest [{s0[1]}]: {s0[2][:130]}"
    else:
        digest = "curiosity: no sharp gap right now (the model fits what it's seen)."
    print(digest)


if __name__ == "__main__":
    main()
