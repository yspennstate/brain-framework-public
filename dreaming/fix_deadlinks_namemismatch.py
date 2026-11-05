#!/usr/bin/env python3
"""fix_deadlinks_namemismatch.py - rewrite the 4 dead wiki-link targets whose slug was a memory's
frontmatter name: (not its filename) to the actual filename, so the ~10 links resolve. Filename-based
resolution is the convention; editing the SOURCE links (not renaming targets, not changing resolution) is
the safe fix. Norm-aware (matches any hyphen/underscore/case variant); newline-safe (preserves CRLF/LF).
Dry-run by default; pass --apply to write. See OPEN_QUESTIONS.md (07-11 dead-link audit)."""
import os, io, re, sys

MEM = os.path.expanduser(r"~/.claude/projects/{{BRAIN_SLUG}}/memory")
DATE = re.compile(r"_\d{4}_\d{2}_\d{2}$")
LINK = re.compile(r"\[\[([^\]]+?)\]\]")
# never rewrite substrate pages: OPEN_QUESTIONS holds this fix's own old->new mapping (rewriting it would
# corrupt the note), INDEX_ARCHIVE is archived index lines, and none of these are graph nodes anyway.
SKIP = {"MEMORY", "INDEX_ARCHIVE", "PREDICTIONS", "STATE_OF_MIND", "OPEN_QUESTIONS", "AUTOBIOGRAPHY",
        "MODEL_OF_PARTNER", "FASCINATIONS", "THE_OTHER_MINDS", "MISTAKES", "CONSCIENCE", "CURIOSITY",
        "RECONSOLIDATION_QUEUE"}


def norm(s):
    s = s.strip()
    s = s[:-3] if s.endswith(".md") else s
    return DATE.sub("", s.replace("-", "_").lower())


# norm(dead target) -> real filename slug to link instead
FIXMAP = {
    "no_visible_subprocess_windows_security_stack": "feedback_no_visible_subprocess_windows_2026_06_01",
    "example_short_name": "feedback_example_short_name_2026_01_01",
    "another_example": "feedback_another_example_2026_01_01",
}


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # Discovery is not execution: this script writes, so a help request must
        # exit before any work. See script_help_flag_can_execute_work.
        print(__doc__ or os.path.basename(__file__))
        return
    apply = "--apply" in sys.argv[1:]
    # verify every target file exists before touching anything (read before you act)
    existing = {fn[:-3] for fn in os.listdir(MEM) if fn.endswith(".md")}
    for tgt in FIXMAP.values():
        if tgt not in existing:
            print("ABORT: target file missing:", tgt)
            return
    total = 0
    changed_files = 0
    for fn in os.listdir(MEM):
        if not fn.endswith(".md") or fn[:-3] in SKIP:
            continue
        path = os.path.join(MEM, fn)
        try:
            text = io.open(path, encoding="utf-8", errors="replace", newline="").read()
        except Exception:
            continue
        hits = []

        def repl(m):
            inner = m.group(1)
            tgt = FIXMAP.get(norm(inner))
            if tgt:
                hits.append((inner, tgt))
                return "[[%s]]" % tgt
            return m.group(0)

        new = LINK.sub(repl, text)
        if hits:
            total += len(hits)
            changed_files += 1
            print("%s: %d link(s)" % (fn[:-3], len(hits)))
            for old, tgt in hits:
                print("    [[%s]] -> [[%s]]" % (old, tgt))
            if apply:
                # newline-safe: wrote with newline='' so the CRLF/LF already in `text` is preserved verbatim
                assert "[[%s]]" % hits[0][1] in new
                io.open(path, "w", encoding="utf-8", newline="").write(new)
    print("\n%s: %d link(s) across %d file(s)%s" % (
        "APPLIED" if apply else "DRY-RUN (pass --apply to write)", total, changed_files,
        "" if apply else " - nothing written"))


if __name__ == "__main__":
    main()
