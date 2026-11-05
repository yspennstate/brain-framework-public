"""test_ctail_mem_scope.py - a recall on another memory folder never shows the live folder's compaction anchors
(5 Nov 2026).

    python test_ctail_mem_scope.py [--module BRAIN.py] [--old OLD_BRAIN.py]

The compaction tail lane reads its tails from the live compactions folder whatever BRAIN_MEM_DIR says, and a tail
stands for its anchor. In a sandbox that anchor is not a memory, so it entered as a foreign row: a throwaway folder of
three memories ranked the live folder's compaction anchors first, and test_attachment_isolation I7-I10 failed on
every Mac-line brain.py because of it.
 S1 a sandbox recall on words the live compaction tails hold shows only the sandbox's own memories
 S2 the sandbox's own memories are still found (the check is not passed by an empty answer)
 S3 on the live folder the scope tag is empty, so the new check never runs there
 S4 the sandbox recall writes no per-folder tail file into the compactions folder (each one was 3.4 MB, never read
    again once its temp folder was gone: 220 of them, 478 MB, on a Mac by 5 Nov)
 S5 after a compaction lands (a day folder's mtime moves), a sandbox recall leaves the shared tail cache as it was;
    before, it rebuilt that cache from the sandbox, which holds no anchors, and the live folder's next recall read the
    empty index under a current key and lost every compaction tail until the next compaction
With --old, the brain before this change must show a row from outside the sandbox (N1), leave a tail file (N2) and
empty the shared cache (N3). Each run copies the compactions folder's day folders and its shared tail cache under a
throwaway HOME, so nothing live is read or written. Without compaction summaries the run says SKIP.
Old modules are run beside their own folder's siblings, so --old should point into a whole checkout.
"""
import glob
import importlib.machinery
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile

if __name__ == "__main__" and ("-h" in sys.argv[1:] or "--help" in sys.argv[1:]):
    print(__doc__)
    sys.exit(0)

HERE = os.path.dirname(os.path.abspath(__file__))
ARGS = sys.argv[1:]
MOD = os.path.abspath(ARGS[ARGS.index("--module") + 1]) if "--module" in ARGS else os.path.join(HERE, "brain.py")
OLD = os.path.abspath(ARGS[ARGS.index("--old") + 1]) if "--old" in ARGS else None
ROW = re.compile(r"^\s+[0-9.]+ \[[^\]]*\] (\S+)", re.M)
QUERY = "compaction summary session agent claims mesh owner asked pushed commit"
OWN = {
    "mesh_claims_note_2026_11_05": "Mesh claims gate every write; an agent asked to commit pushes after it asserts.",
    "session_summary_note_2026_11_05": "A session summary records what the owner asked and which commit was pushed.",
    "unrelated_garden_note_2026_11_05": "The garden beds need compost before the spring planting.",
}
fails, ran = [], []


def check(ok, what):
    print(("PASS  " if ok else "FAIL  ") + what)
    ran.append(what)
    if not ok:
        fails.append(what)


def sandbox(td):
    mem = os.path.join(td, "memory")
    os.makedirs(mem)
    lines = ["# Memory Index"]
    for slug, body in OWN.items():
        with open(os.path.join(mem, slug + ".md"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write("---\nname: %s\ndescription: %s\nmetadata:\n  type: project\n---\n\n%s\n" % (slug, body[:60], body))
        lines.append("- [%s.md](%s.md) - %s" % (slug, slug, body))
    with open(os.path.join(mem, "MEMORY.md"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    return mem


def env_for(td, mem):
    return dict(os.environ, BRAIN_MEM_DIR=mem, BRAIN_BM25_INDEX=os.path.join(td, "bm25_index.json.gz"),
                BRAIN_RECALL_SERVER="0", BRAIN_RECALL_LOG="0", BRAIN_RECALL_REFRESH="0", BRAIN_DOC_EXPANSION="0",
                RECALL_REWRITE_NO_LOG="1", BRAIN_RECALL_PUBLISH="off")


LIVE_CDIR = os.path.join(os.path.expanduser("~"), ".claude", "brain_graph", "compactions")


def scoped_files(cdir):
    return sorted(f for f in os.listdir(cdir) if f.startswith((".tail_postings.marshal.", ".mtail_index.marshal.")))


def shared_tails(cdir):
    import marshal
    try:
        with open(os.path.join(cdir, ".ctail_index.marshal"), "rb") as fh:
            return len(marshal.load(fh)[1][0])
    except (OSError, ValueError, EOFError, IndexError, TypeError):
        return None


def recall_rows(brain, tag, landed=False):
    """(rows, note, per-folder tail files the recall left, tails in the shared cache after) for one sandbox recall
    under a throwaway HOME; landed moves a day folder's mtime first, as a new compaction does."""
    td = tempfile.mkdtemp(prefix="ctail_scope_%s_" % tag)
    try:
        mem = sandbox(td)
        env = env_for(td, mem)
        home = os.path.join(td, "home")
        cdir = os.path.join(home, ".claude", "brain_graph", "compactions")
        shutil.copytree(LIVE_CDIR, cdir, ignore=shutil.ignore_patterns(".*"))
        shutil.copy2(os.path.join(LIVE_CDIR, ".ctail_index.marshal"), cdir)
        if landed:
            day = sorted(d for d in os.listdir(cdir) if os.path.isdir(os.path.join(cdir, d)))[-1]
            os.utime(os.path.join(cdir, day), None)
        env.update(HOME=home, USERPROFILE=home)
        d = os.path.dirname(brain)
        b = subprocess.run([sys.executable, "-s", "-c", "import sys; sys.path.insert(0, sys.argv[1]); import bm25; "
                            "bm25.build(sys.argv[2], sys.argv[3])", d, mem, env["BRAIN_BM25_INDEX"]],
                           env=env, capture_output=True, text=True, timeout=300)
        if b.returncode:
            return None, "index build failed: " + (b.stderr or "").strip()[-200:], [], None
        p = subprocess.run([sys.executable, "-s", brain, "recall", QUERY, "-n", "8"], cwd=d, env=env,
                           capture_output=True, text=True, timeout=300)
        rows = [r.removesuffix(".md") for r in ROW.findall(p.stdout)]
        return rows, "rc %s" % p.returncode, scoped_files(cdir), shared_tails(cdir)
    finally:
        shutil.rmtree(td, ignore_errors=True)


def scope_tag(path):
    loader = importlib.machinery.SourceFileLoader("ctail_scope_mod", path)
    spec = importlib.util.spec_from_file_location("ctail_scope_mod", path, loader=loader)
    m = importlib.util.module_from_spec(spec)
    sys.path.insert(0, os.path.dirname(path))
    saved = os.environ.pop("BRAIN_MEM_DIR", None)
    try:
        loader.exec_module(m)
        import bm25 as bm     # brain.py imports it lazily; with BRAIN_MEM_DIR unset its MEM is the live folder
        f = getattr(m, "_mem_scoped", None)
        return None if (f is None or bm is None) else f("", bm)
    finally:
        if saved is not None:
            os.environ["BRAIN_MEM_DIR"] = saved


def main():
    cdir = LIVE_CDIR
    have = bool(glob.glob(os.path.join(cdir, "*", "*")))
    if not have or shared_tails(cdir) is None:
        print("SKIP - no compaction summaries under %s, so the lane has nothing to leak" % cdir)
        return 0
    rows, note, left, _sh = recall_rows(MOD, "new")
    if rows is None:
        check(False, "S1 the sandbox recall ran: %s" % note)
    else:
        foreign = [r for r in rows if r not in OWN]
        check(not foreign, "S1 rows only from the sandbox (%s): foreign %s" % (note, foreign[:3]))
        check(any(r in OWN for r in rows), "S2 the sandbox's own memories are found: %s" % rows[:4])
        check(not left, "S4 no per-folder tail file left in the compactions folder: %s" % left[:2])
    before = shared_tails(cdir)
    _r, _n, _l, after = recall_rows(MOD, "new_landed", landed=True)
    check(after == before, "S5 a compaction lands and a sandbox recall runs: shared cache tails %s -> %s"
          % (before, after))
    tag = scope_tag(MOD)
    check(tag == "", "S3 on the live folder the scope tag is empty (%r), so the check never runs there" % tag)
    if OLD:
        orow, onote, oleft, _osh = recall_rows(OLD, "old")
        ofor = [r for r in (orow or []) if r not in OWN]
        check(bool(ofor), "N1 the brain before this change shows rows from outside the sandbox (%s): %s"
              % (onote, ofor[:3]))
        check(bool(oleft), "N2 the brain before this change leaves a per-folder tail file: %s" % oleft[:2])
        _r, _n, _l, oafter = recall_rows(OLD, "old_landed", landed=True)
        check(oafter is not None and oafter < before, "N3 the brain before this change rebuilds the shared cache from "
              "the sandbox: tails %s -> %s" % (before, oafter))
    print("FAIL" if fails else "PASS", "- %d controls, %d failed" % (len(ran), len(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
