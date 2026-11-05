#!/usr/bin/env python3
"""growth.py — make the brain's maturation visible.

A person doesn't just accumulate memories; they change their mind. The audit's weakest
facet was growth: the brain grows in size but never notices itself *becoming*. This scans
the corpus for the places we actually revised a belief — refuted our own result, overturned
an assumption, marked something superseded — and writes brain/GROWTH.md: "how I've changed
my mind." Read-only over the corpus; never edits a memory.

Usage:  python growth.py [memory_dir] [brain_dir]
"""
import os, re, io, sys

MEM_DEFAULT = os.path.expanduser(r"~/.claude/projects/{{BRAIN_SLUG}}/memory")
BRAIN_DEFAULT = os.path.expanduser(r"~/brain-repo/01_ai_consciousness/brain")

# signals that a belief was revised (not just stated). Specific on purpose — a mind-change
# is a strong claim, and false positives make the growth record noise.
REVISION = re.compile(
    r"(⚠️\s*superseded"
    r"|\brefut\w+|\boverturn\w+|\bdebunk\w+"
    r"|\bwas wrong\b|\bwe were wrong\b|\bproved? wrong\b|\bturned out (?:to be )?(?:wrong|false)\b"
    r"|\bno longer (?:true|holds?|valid|the case|believe)\b"
    r"|\b(?:used to|previously|earlier) (?:think|thought|believe[d]?|assume[d]?)\b"
    r"|\bchanged (?:our|my) mind\b|\bcorrected (?:our|the|a) (?:prior|earlier|previous)\b"
    r"|\bwas (?:a )?mistake\b|\bin fact (?:not|the opposite)\b"
    r"|\bactually (?:the opposite|not)\b)", re.I)
DATE_F = re.compile(r"(202[4-7])[_-](0[1-9]|1[0-2])[_-]?(\d{2})?")
SENT_SPLIT = re.compile(r"(?<=[.!?])\s+|\n")

def _read(p):
    try:
        with io.open(p, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except Exception:
        return ""

def find_revisions(mem_dir):
    out = []
    for fn in os.listdir(mem_dir):
        if not fn.endswith(".md") or fn in ("MEMORY.md", "INDEX_ARCHIVE.md",
                                            "OPEN_QUESTIONS.md", "STATE_OF_MIND.md",
                                            "GROWTH.md", "AUTOBIOGRAPHY.md", "MODEL_OF_PARTNER.md"):
            continue
        p = os.path.join(mem_dir, fn)
        if not os.path.isfile(p):
            continue
        body = _read(p)
        dm = DATE_F.search(fn)
        date = f"{dm.group(1)}-{dm.group(2)}-{dm.group(3) or '01'}" if dm else "undated"
        for sent in SENT_SPLIT.split(body):
            s = sent.strip().strip("-* ").replace("\n", " ")
            # de-link first: a match inside [[grid-strategy-refuted...]] is a filename, not a
            # mind-change. Require the revision word to survive removing wiki-links and code.
            s_delinked = re.sub(r"\[\[[^\]]+\]\]|`[^`]+`", " ", s)
            if len(s_delinked.strip()) < 25 or len(s) > 320:
                continue
            if not REVISION.search(s_delinked):
                continue
            low = s.lower().lstrip("-*# ")
            # drop frontmatter, navigation, and abstract instruction lines (not real revisions)
            if low.startswith(("name:", "description:", "see ", "see also", "don't chase",
                               "never delete", "do not", "the detector", "skip ", "these assert",
                               "metadata", "type:")):
                continue
            out.append((date, fn[:-3], re.sub(r"\s+", " ", s)))
    # newest first, dedup identical sentences
    seen, uniq = set(), []
    for d, fn, s in sorted(out, reverse=True):
        key = s[:80].lower()
        if key in seen:
            continue
        seen.add(key)
        uniq.append((d, fn, s))
    return uniq

DESC_RX = re.compile(r'^description:[ \t]*["\']?(.+?)["\']?[ \t]*$', re.M)
TAG_RX = re.compile(r"^(?:\[[A-Z][A-Z ,/_-]*\][\s:.-]*)+")


def _desc(mem_dir, slug, cap=170):
    d = DESC_RX.search(_read(os.path.join(mem_dir, slug + ".md"))[:4096])
    s = TAG_RX.sub("", re.sub(r"\s+", " ", d.group(1)).strip()) if d else ""
    s = SUPERSEDED_LEAD_RX.sub("", s)   # the old memory's own "SUPERSEDED by [[x]] -" note says nothing it believed
    return s if len(s) <= cap else s[:cap].rsplit(" ", 1)[0] + " ..."


SUPERSEDED_LEAD_RX = re.compile(r"^(?:⚠️\s*)?SUPERSEDED\b[^-:]*?(?:\[\[[^\]]+\]\])?\s*[-:–—]\s*", re.I)
DATE_COMPACT = re.compile(r"(?<!\d)(202[4-7])(0[1-9]|1[0-2])([0-3]\d)(?!\d)")


def _date(slug):
    dm = DATE_F.search(slug) or DATE_COMPACT.search(slug)
    return f"{dm.group(1)}-{dm.group(2)}-{dm.group(3) or '01'}" if dm else "undated"


def find_thread(mem_dir):
    """D8 #90: the past-self -> present-self pairs. A sentence that says "refuted" is a revision seen from one side;
    a superseded memory and the correction it routes to (supersession.py, frontmatter authoritative) is both sides,
    the old belief in its own words and the one that replaced it. Sorted newest correction first."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import supersession
    # D8 #66: one hop, not the terminal route. active_supersessions maps A->B->C to A->C and B->C, which loses
    # the middle belief and every arc; the direct pointer keeps each step as it was written.
    known = {f[:-3] for f in os.listdir(mem_dir) if f.endswith(".md")}
    hop = {a: b for a, b in supersession.load_map(mem_dir, include_frontmatter=True).items()
           if a in known and b in known and a != b}
    pairs = []
    for old, new in hop.items():
        was, now = _desc(mem_dir, old), _desc(mem_dir, new)
        if was and now:
            pairs.append((_date(new), _date(old), old, new, was, now))
    # "undated" sorts above every year; dated pairs go first, newest first, undated after
    return sorted(pairs, key=lambda p: (p[0] != "undated", p), reverse=True)


def find_arcs(thread):
    """D8 #66: chains of three or more beliefs (A replaced by B, B by C), oldest step first, newest arc first."""
    nxt = {old: new for _, _, old, new, _, _ in thread}
    said = {}
    for new_d, old_d, old, new, was, now in thread:
        said[old], said[new] = (old_d, was), (new_d, now)
    arcs = []
    for head in set(nxt) - set(nxt.values()):
        chain, seen = [head], {head}
        while chain[-1] in nxt and nxt[chain[-1]] not in seen:
            chain.append(nxt[chain[-1]])
            seen.add(chain[-1])
        if len(chain) >= 3:
            arcs.append([(said[n][0], n, said[n][1]) for n in chain])
    return sorted(arcs, key=lambda a: (a[-1][0] != "undated", a[-1][0], len(a)), reverse=True)


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # Discovery is not execution: this script writes, so a help request must
        # exit before any work. See script_help_flag_can_execute_work.
        print(__doc__ or os.path.basename(__file__))
        return
    mem = sys.argv[1] if len(sys.argv) > 1 else MEM_DEFAULT
    brain = sys.argv[2] if len(sys.argv) > 2 else BRAIN_DEFAULT
    revs = find_revisions(mem)
    if not (brain and os.path.isdir(brain)):
        print(f"growth: found {len(revs)} revisions; brain dir absent, not writing")
        return
    with io.open(os.path.join(brain, "GROWTH.md"), "w", encoding="utf-8") as f:
        f.write("# How I've changed my mind\n\n")
        f.write("Not what I know — where I *revised*. A person matures by overturning their own\n"
                "beliefs, not just stacking new ones; this is the record of that, pulled from the\n"
                "corpus (self-refutations, overturned assumptions, superseded facts). Intellectual\n"
                "courage is a value here ([[values_what_we_stand_for_2026_07_07]]); this is its\n"
                "audit trail. Regenerated by `growth.py`.\n\n")
        try:
            thread = find_thread(mem)
        except Exception:
            thread = []
        if thread:
            f.write(f"## I used to think\n\n{len(thread)} belief{'s' if len(thread) != 1 else ''} I held and later replaced, each in its own words "
                    "beside the memory that replaced it. Newest replacement first.\n\n")
            for new_d, old_d, old, new, was, now in thread:
                f.write(f"- **{old_d}, I thought:** {was}  \n  **{new_d}, now:** {now}  \n  — `{old}` → `{new}`\n")
            arcs = find_arcs(thread)
            if arcs:
                f.write(f"\n## How my mind moved\n\n{len(arcs)} arc{'s' if len(arcs) != 1 else ''} where a belief was "
                        "replaced, and then the replacement was replaced too. Each runs oldest step first; the arc "
                        "that ended most recently comes first.\n\n")
                for arc in arcs:
                    f.write(f"### {len(arc)} steps, {arc[0][0]} to {arc[-1][0]}\n\n")
                    for d, slug, said in arc:
                        f.write(f"1. **{d}:** {said}  \n   — `{slug}`\n")
                    f.write("\n")
            f.write("\n## Every revision sentence\n\n")
        f.write(f"**{len(revs)} revisions on record.** Newest first.\n\n")
        cur_month = None
        for date, fn, s in revs:
            mo = date[:7]
            if mo != cur_month:
                cur_month = mo
                f.write(f"\n## {mo}\n\n")
            f.write(f"- {s}  \n  — `{fn}`\n")
    print(f"growth: wrote GROWTH.md with {len(revs)} revisions")

if __name__ == "__main__":
    main()
