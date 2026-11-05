#!/usr/bin/env python3
"""build_programs_view.py - map the brain's major RESEARCH PROGRAMS (coherent multi-memory work bodies).

TOPICS.md groups by topic, TIMELINE.md by date - but neither shows the brain's coherent PROGRAMS: the
43-memory arnold_diffusion proof, the builder/rmt/cast/options research runs, the W80 directive set. A
program is a series of memos sharing a stem (the prefix before a trailing cycle/round/W-number). This
writes a concise PROGRAMS.md nav view: each substantive program with its size, date span, and an anchor
summary - so the owner and a waking session can see the big work bodies at a glance and jump into one.

Reuses build_series_edges' detection (same stem/noise/substance rules) so the map matches what the
navigability edges connect. Read-only over the memory dir; writes one nav file. Wired into the nightly.
Usage: python build_programs_view.py [--quiet]
"""
import io, os, re, sys
from collections import defaultdict

HOME = os.path.expanduser("~")
DR = os.path.join(HOME, ".claude", "dreaming")
MEM = os.path.join(HOME, ".claude", "projects", "{{BRAIN_SLUG}}", "memory")
BRAIN = os.path.join(HOME, "brain-repo", "01_ai_consciousness", "brain")
OUT = os.path.join(BRAIN, "PROGRAMS.md")
sys.path.insert(0, DR)
try:
    import build_series_edges as bse
except Exception:
    bse = None

DATE = re.compile(r"_(\d{4})_(\d{2})_(\d{2})")
ANCHOR = re.compile(r"(master|final|paper|complete|journal|report|summary|solved|breakthrough|gist)", re.I)


def desc(slug):
    try:
        t = io.open(os.path.join(MEM, slug + ".md"), encoding="utf-8", errors="replace").read()
        m = re.search(r'description:\s*"?(.+?)"?\s*\n', t)
        return (m.group(1) if m else "").strip()[:130]
    except Exception:
        return ""


def span(members):
    ds = sorted(DATE.search(m).group(0)[1:] for m in members if DATE.search(m))
    return (ds[0], ds[-1]) if ds else (None, None)


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # Discovery is not execution: this script writes, so a help request must
        # exit before any work. See script_help_flag_can_execute_work.
        print(__doc__ or os.path.basename(__file__))
        return
    quiet = "--quiet" in sys.argv[1:]
    if bse is None:
        if not quiet:
            print("build_programs_view: build_series_edges import failed")
        return
    _edges, series = bse.series_edges(1, 0.0)  # k/weight irrelevant; we want the detected series map
    if not series:
        if not quiet:
            print("build_programs_view: no series detected")
        return
    real = {bse.norm(fn): fn[:-3] for fn in os.listdir(MEM) if fn.endswith(".md")}
    progs = []
    for stem, canon_members in series.items():
        members = [real.get(m, m) for m in canon_members]
        if len(members) < bse.MIN_SERIES:
            continue
        lo, hi = span(members)
        # anchor = a master/final/paper member, else the one with the longest description
        anchor = next((m for m in members if ANCHOR.search(m)), None)
        if not anchor:
            anchor = max(members, key=lambda m: len(desc(m)))
        progs.append((len(members), stem, lo, hi, anchor))
    progs.sort(reverse=True)
    lines = ["# The brain's work programs (coherent multi-memory bodies)", "",
             f"{len(progs)} programs, largest first - a run of memos sharing a stem. Some are sustained "
             "research (arnold_diffusion, the rmt/cast runs); many are operational agent cycles from big "
             "fleet days (the W80 builder swarm, the inspector/audit loops). Complements TOPICS (by "
             "subject) and TIMELINE (by date); each is now internally navigable (build_series_edges). "
             "Regenerated nightly by build_programs_view.py.", ""]
    for n, stem, lo, hi, anchor in progs:
        sp = f"{lo} -> {hi}" if lo else "undated"
        d = desc(anchor)
        lines.append(f"## {stem}  ({n} memories, {sp})")
        lines.append(f"- anchor: [{anchor}](../claude/memory/{anchor}.md)" + (f" - {d}" if d else ""))
        lines.append("")
    try:
        os.makedirs(BRAIN, exist_ok=True)
        tmp = OUT + ".tmp"
        with io.open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(lines) + "\n")
        os.replace(tmp, OUT)
    except Exception as e:
        if not quiet:
            print("build_programs_view: write failed:", e)
        return
    if not quiet:
        print(f"build_programs_view: {len(progs)} programs -> {OUT}")
        for n, stem, lo, hi, _ in progs[:6]:
            print(f"  {stem}  ({n} memories)")


if __name__ == "__main__":
    main()
