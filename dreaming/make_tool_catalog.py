"""Generate a catalog of ~/.claude/dreaming's tools from their own docstrings.

WHY: 248 .py tools live there, 49 of them eval harnesses, and no catalog exists - verified by three
independent searches (filename glob; content scan of the dreaming dir; content scan of
12_cognitive_architecture). The densest listing anywhere is the memory OPEN_QUESTIONS at 85 of 248,
which is a to-do list. So the tooling is discoverable only through recall, and recall on exactly
that question measured 1-of-9 on associative queries this session. Six near-rebuilds in one day by
an agent actively trying to check first is the cost.

DESIGN: derived, never hand-maintained. It reads each file's first docstring line, so it cannot
drift from the source the way a hand-written index would - regenerate and it is current. Groups by
prefix so an agent scanning for "is there already an X" sees the family, not an alphabet.

Read-only apart from the single output file.
"""
import os, glob, io, ast, datetime, sys

if any(a in ("-h", "--help") for a in sys.argv[1:]):
    # Discovery is not execution: this file does its work at module scope, so the
    # guard must come before the first statement that runs.
    print(__doc__ or os.path.basename(__file__))
    raise SystemExit(0)


DR = os.path.expanduser("~/.claude/dreaming")
OUT = os.path.join(DR, "TOOL_CATALOG.md")


def first_doc_line(path):
    try:
        src = io.open(path, encoding="utf-8", errors="replace").read()
    except Exception as e:
        return "(unreadable: %s)" % type(e).__name__
    try:
        doc = ast.get_docstring(ast.parse(src))
    except Exception:
        doc = None
    if not doc:
        for ln in src.splitlines():
            s = ln.strip()
            if s.startswith("#") and len(s) > 3 and not s.startswith("#!"):
                return s.lstrip("# ").strip()[:150]
        return "(no docstring)"
    line = doc.strip().splitlines()[0].strip()
    # docstrings here often start "name.py - the real description"
    # strip a leading "name.py - " or "name - " prefix. ORDER MATTERS: strip the .py extension
    # BEFORE the punctuation, or "bm25.py - x" leaves "py - x" (the lstrip eats the dot first,
    # then ".py" no longer matches). That bug shipped in the first generated catalog.
    base = os.path.basename(path)[:-3]
    low = line.lower()
    if low.startswith(base.lower() + ".py"):
        line = line[len(base) + 3:]
    elif low.startswith(base.lower()):
        line = line[len(base):]
    line = line.lstrip(". -")
    return line[:150] or "(empty docstring)"


def group_of(name, reldir):
    # Files in a SUBDIRECTORY are grouped by that directory - they are subsystems (graph_fire,
    # strong_nodes, network, error_graph...), not loose tools, and a prefix grouping would scatter
    # them. The first version of this catalog globbed only the top level and covered 249 of 416
    # .py files - 60% - while its header claimed "248 tools". That is a bounded read presented as
    # the population, which is the exact defect the catalog exists to help against.
    if reldir != ".":
        return reldir.split(os.sep)[0] + "/"
    for pre in ("eval_", "build_", "brain_", "dream_", "recall_", "reflex_", "instinct_",
                "heed_", "bm25", "compact_", "check_", "audit_", "job_", "prompt_", "spread_"):
        if name.startswith(pre):
            return pre.rstrip("_")
    return "other"


def main():
    paths = sorted(glob.glob(os.path.join(DR, "**", "*.py"), recursive=True))
    groups = {}
    for p in paths:
        n = os.path.basename(p)[:-3]
        reldir = os.path.relpath(os.path.dirname(p), DR)
        label = n if reldir == "." else "%s/%s" % (reldir.replace(os.sep, "/"), n)
        groups.setdefault(group_of(n, reldir), []).append((label, first_doc_line(p)))
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    L = []
    L.append("# Tool catalog - ~/.claude/dreaming")
    L.append("")
    L.append("GENERATED, not hand-maintained. Regenerate with the script that wrote this and it is")
    L.append("current; edit it by hand and it will drift. One line per tool, taken from the tool's own")
    L.append("first docstring line, so the description cannot disagree with the source.")
    L.append("")
    L.append("Written %s. %d tools in %d families." % (stamp, len(paths), len(groups)))
    L.append("")
    L.append("WHY THIS EXISTS: an agent asking \"is there already a tool for X\" had two routes -")
    L.append("guess a filename, or ask the brain. Recall on that class of question measured 1-of-9 on")
    L.append("associative queries (2026-08-22), and six near-rebuilds happened in one day to an agent")
    L.append("actively trying to check first: the eval harnesses, heed_audit, build_orphan_edges,")
    L.append("bm25_append, the speak-gate measurement, and the precision judge. All existed. None was")
    L.append("findable by asking.")
    L.append("")
    L.append("READ THIS BEFORE BUILDING ANYTHING IN THIS DIRECTORY.")
    L.append("")
    for g in sorted(groups, key=lambda x: (x == "other", x)):
        items = sorted(groups[g])
        L.append("## %s  (%d)" % (g, len(items)))
        L.append("")
        for n, d in items:
            L.append("- **%s** - %s" % (n, d))
        L.append("")
    io.open(OUT, "w", encoding="utf-8", newline="\n").write("\n".join(L))
    print("wrote %s" % OUT)
    print("%d tools, %d families" % (len(paths), len(groups)))
    for g in sorted(groups, key=lambda x: -len(groups[g]))[:8]:
        print("   %-12s %3d" % (g, len(groups[g])))


if __name__ == "__main__":
    main()
