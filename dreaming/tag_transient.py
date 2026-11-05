#!/usr/bin/env python3
"""Demote agent-scratch checkpoint memories to type: transient (never delete).
Matches the same pattern dream.py hides from the agenda. Only rewrites the type:
line inside existing frontmatter; leaves body untouched. Dry-run default; 'apply' writes."""
import os, re, sys, io

if any(a in ("-h", "--help") for a in sys.argv[1:]):
    # Discovery is not execution: this file works at module scope, so the guard
    # must precede the first statement that runs.
    print(__doc__ or os.path.basename(__file__))
    raise SystemExit(0)

MEM = os.path.expanduser(r"~/.claude/projects/{{BRAIN_SLUG}}/memory")
APPLY = len(sys.argv) > 1 and sys.argv[1] == "apply"
# checkpoint scratch: round/cycle/agent/loop swarm files, codex_round dumps
PAT = re.compile(r"(^transient_|^codex_round\d|round\d+.*cycle\d+.*agent|_loop\d+|_agent\d+_report)", re.I)
SKIP = {"MEMORY.md", "INDEX_ARCHIVE.md", "OPEN_QUESTIONS.md", "STATE_OF_MIND.md"}

changed, already, samples = 0, 0, []
for fn in sorted(os.listdir(MEM)):
    if not fn.endswith(".md") or fn in SKIP or not PAT.search(fn):
        continue
    p = os.path.join(MEM, fn)
    try:
        t = io.open(p, encoding="utf-8").read()
    except Exception:
        continue
    if not t.startswith("---"):
        continue
    end = t.find("\n---", 3)
    if end < 0:
        continue
    fm, rest = t[:end], t[end:]
    m = re.search(r"^(\s*type:\s*)(\w+)", fm, re.M)
    if m and m.group(2) == "transient":
        already += 1
        continue
    if m:
        new_fm = fm[:m.start()] + m.group(1) + "transient" + fm[m.end():]
    elif re.search(r"^metadata:\s*$", fm, re.M):
        new_fm = re.sub(r"^(metadata:\s*)$", r"\1\n  type: transient", fm, count=1, flags=re.M)
    else:
        new_fm = fm.rstrip("\n") + "\nmetadata:\n  type: transient"
    if APPLY:
        io.open(p, "w", encoding="utf-8", newline="\n").write(new_fm + rest)
    changed += 1
    if len(samples) < 6:
        samples.append(fn)

print(("APPLIED" if APPLY else "DRY-RUN"), f"demoted={changed} already_transient={already}")
for s in samples:
    print("  ", s)
