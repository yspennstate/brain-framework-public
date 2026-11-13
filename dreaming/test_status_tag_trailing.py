"""brain.py reads a status tag that carries words after it, and reads REFUTED / WITHDRAWN as retracted.

    python3 test_status_tag_trailing.py   (exit 0 = pass)

Index lines in the shape MEMORY.md writes ("- [slug](slug.md) - [TAG ...] text"): the plain tags read as before;
"[VERIFIED data node]", "[PROVISIONAL, one rep]" and "[OWNER-STATED 2026-08-04]" read as their tag; "[REFUTED 5 Nov
...]" and "[WITHDRAWN]" read as retracted; a bracket later in the text, an unknown word ("[VERIFIEDX]") and no tag
read as legacy. A memory whose frontmatter status is refuted counts as retracted.
"""
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import brain  # noqa: E402

CASES = [
    ("[VERIFIED] plain", "verified"),
    ("[PROVISIONAL] plain", "provisional"),
    ("[OWNER-STATED] plain", "owner-stated"),
    ("[RETRACTED] plain", "retracted"),
    ("[VERIFIED data node] words", "verified"),
    ("[PROVISIONAL, one rep] words", "provisional"),
    ("[OWNER-STATED 2026-08-04] words", "owner-stated"),
    ("[REFUTED 5 Nov 21:3x - see the newer card] words", "retracted"),
    ("[WITHDRAWN] words", "retracted"),
    ("[VERIFIEDX] words", "legacy"),
    ("no tag here [VERIFIED] later", "legacy"),
    ("no tag at all", "legacy"),
]


def main():
    fails = []
    for i, (rest, want) in enumerate(CASES):
        slug = "case_%02d" % i
        line = "- [%s](%s.md) - %s" % (slug, slug, rest)
        brain._STATUS_MEMO.clear()
        got = brain._status_from_index(slug, {slug: line})
        if got is None:
            m = brain._STATUS_RX.search(line)
            got = m.group(1).lower() if m else "legacy"
            got = brain._STATUS_ALIAS.get(got, got)
        if got != want:
            fails.append("%r: %s, want %s" % (rest, got, want))
    mem = tempfile.mkdtemp(prefix="stattag_")
    saved = brain.MEM
    try:
        brain.MEM = mem
        with open(os.path.join(mem, "gone.md"), "w", encoding="utf-8") as f:
            f.write("---\nname: gone\ndescription: x\nstatus: refuted\nmetadata:\n  type: project\n---\n\nbody\n")
        if not brain._frontmatter_retracted("gone"):
            fails.append("frontmatter status: refuted should read as retracted")
    finally:
        brain.MEM = saved
        shutil.rmtree(mem, ignore_errors=True)
    for f in fails:
        print("FAIL " + f)
    print("RESULT %d/%d" % (len(CASES) + 1 - len(fails), len(CASES) + 1))
    return 1 if fails else 0


if __name__ == "__main__":
    # Discovery is not execution: a help request exits before any work.
    if "-h" in sys.argv[1:] or "--help" in sys.argv[1:]:
        print(__doc__ or os.path.basename(__file__))
        raise SystemExit(0)
    sys.exit(main())
