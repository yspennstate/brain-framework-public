#!/usr/bin/env python3
"""Backfill missing metadata.type in memory frontmatter, inferred from filename.
Conservative: only edits files that HAVE frontmatter and LACK a type line.
UTF-8 in/out, no BOM. Dry-run by default; pass 'apply' to write."""
import os, re, sys, io

if any(a in ("-h", "--help") for a in sys.argv[1:]):
    # Discovery is not execution: this file works at module scope, so the guard
    # must precede the first statement that runs.
    print(__doc__ or os.path.basename(__file__))
    raise SystemExit(0)

MEM = os.path.expanduser(r"~/.claude/projects/{{BRAIN_SLUG}}/memory")
APPLY = len(sys.argv) > 1 and sys.argv[1] == "apply"
SKIP = {"MEMORY.md", "INDEX_ARCHIVE.md", "OPEN_QUESTIONS.md"}

PREFIX = [
    ("feedback_", "feedback"), ("user_", "user"), ("reference_", "reference"),
    ("project_", "project"), ("audit_", "audit"), ("audit", "audit"),
    ("gist_", "project"), ("session_", "transient"), ("crash_", "project"),
    ("migration", "project"), ("theory_", "project"), ("open_problem", "project"),
    ("paper", "project"), ("grid", "project"), ("acct", "project"),
    ("account", "project"), ("aws_", "project"), ("azure_", "project"),
    ("proj_", "project"),
    ("codex_", "project"), ("agent", "project"), ("arnold_", "project"),
]

def infer(fn):
    low = fn.lower()
    for pre, ty in PREFIX:
        if low.startswith(pre):
            return ty
    return "project"  # default: these are all work records

stats = {"has_type": 0, "added": 0, "no_frontmatter": 0, "skipped": 0}
by_type = {}
for fn in sorted(os.listdir(MEM)):
    if not fn.endswith(".md") or fn in SKIP:
        continue
    p = os.path.join(MEM, fn)
    try:
        text = io.open(p, encoding="utf-8").read()
    except Exception:
        stats["skipped"] += 1
        continue
    has_fm = text.startswith("---")
    end = text.find("\n---", 3) if has_fm else -1
    if not has_fm or end < 0:
        # synthesize full frontmatter: name from filename, description from first content line
        stats["no_frontmatter"] += 1
        ty = infer(fn)
        body = text.lstrip("﻿")
        desc = ""
        for ln in body.split("\n"):
            t = ln.strip().lstrip("#").strip().strip("*_ ")
            if len(t) >= 15 and not t.startswith(("---", "|", "```")):
                desc = t
                break
        if not desc:
            desc = fn[:-3].replace("_", " ")
        desc = desc.replace('"', "'")
        if len(desc) > 220:
            desc = desc[:217] + "..."
        fm_block = f'---\nname: {fn[:-3]}\ndescription: "{desc}"\nmetadata:\n  type: {ty}\n---\n\n'
        by_type[ty] = by_type.get(ty, 0) + 1
        if APPLY:
            io.open(p, "w", encoding="utf-8", newline="\n").write(fm_block + body)
        stats["synthesized"] = stats.get("synthesized", 0) + 1
        if stats["synthesized"] <= 6 and not APPLY:
            print(f"  sample [{ty}] {fn}: {desc[:100]}")
        continue
    fm = text[:end]
    if re.search(r"^\s*type:\s*\w", fm, re.M):
        stats["has_type"] += 1
        continue
    ty = infer(fn)
    by_type[ty] = by_type.get(ty, 0) + 1
    if re.search(r"^metadata:\s*$", fm, re.M):
        new_fm = re.sub(r"^(metadata:\s*)$", r"\1\n  type: " + ty, fm, count=1, flags=re.M)
    else:
        new_fm = fm.rstrip("\n") + f"\nmetadata:\n  type: {ty}"
    new = new_fm + text[end:]
    if APPLY:
        io.open(p, "w", encoding="utf-8", newline="\n").write(new)
    stats["added"] += 1

print(("APPLIED" if APPLY else "DRY-RUN"), stats)
print("added by type:", dict(sorted(by_type.items(), key=lambda kv: -kv[1])))
