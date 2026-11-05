"""test_attachment_isolation.py - one attachment bundle that fails verification no longer stops every deliberate recall
(28 Sep 2026, claude-825b1305; scheduled audit account03/B02 r68, B02-R1-ATTACHMENT-CORRUPTION-GLOBAL-RECALL-STOP-001).

    python test_attachment_isolation.py [--brain BRAIN.py] [--docs NODE_DOCUMENTS.py] [--bm25 BM25.py] [--old OLD_BRAIN.py]

Runs in throwaway memory folders with a shadow index (BRAIN_MEM_DIR, BRAIN_BM25_INDEX); the live store is never read or
written. A bundle is broken by changing one byte of its document after it was attached (its sha256 no longer matches).
 I1 two bundles, A broken and B healthy, B matching the query: search_nodes with a failures list returns B and names A;
    without the list it still raises (other callers keep the stop)
 I2 recall with A broken and a query only an ordinary memory matches: that memory is shown, the output carries the
    INCOMPLETE_ATTACHMENT_CORPUS line naming A, exit 0
 I3 recall on words only A's broken document holds: A's memory is not shown on them
 I4 two broken bundles: both named
 I5 no broken bundle: the displayed rows are identical to the brain before this change
 I6 the top node's own bundle broken: no partial attached block is printed, and the recall exits 2
With --old, the brain before this change must fail I2 (it exits 2 and shows nothing).
"""
import contextlib
import importlib.machinery
import importlib.util
import io
import os
import re
import shutil
import stat
import sys
import tempfile
import time

if __name__ == "__main__" and ("-h" in sys.argv[1:] or "--help" in sys.argv[1:]):
    print(__doc__)
    sys.exit(0)

HERE = os.path.dirname(os.path.abspath(__file__))
# bm25 fixes its memory dir and index path at import. Point both at a placeholder that is never created before any
# module is loaded, so nothing in this test can ever capture the live store; each Store re-loads bm25 under its own paths.
_PLACEHOLDER = os.path.join(tempfile.gettempdir(), "attach_iso_placeholder_never_created")
os.environ["BRAIN_MEM_DIR"] = os.path.join(_PLACEHOLDER, "memory")
os.environ["BRAIN_BM25_INDEX"] = os.path.join(_PLACEHOLDER, "bm25_index.json.gz")
ARGS = sys.argv[1:]
BRAIN = os.path.abspath(ARGS[ARGS.index("--brain") + 1]) if "--brain" in ARGS else os.path.join(HERE, "brain.py")
DOCS = os.path.abspath(ARGS[ARGS.index("--docs") + 1]) if "--docs" in ARGS else os.path.join(HERE, "brain_node_documents.py")
OLD = os.path.abspath(ARGS[ARGS.index("--old") + 1]) if "--old" in ARGS else None
BM25 = os.path.abspath(ARGS[ARGS.index("--bm25") + 1]) if "--bm25" in ARGS else os.path.join(HERE, "bm25.py")
PLAIN = re.compile(r"^\s*\d\.\d\d\s+\[[^\]]*\]\s+(\S+)", re.M)
fails, ran = [], []


def check(ok, what):
    print(("PASS  " if ok else "FAIL  ") + what)
    ran.append(what)
    if not ok:
        fails.append(what)


def rmtree(path):
    def clear(func, name, _exc):
        os.chmod(name, stat.S_IWRITE)
        func(name)
    shutil.rmtree(path, onexc=clear)


def load(path, name):
    loader = importlib.machinery.SourceFileLoader(name, path)
    spec = importlib.util.spec_from_file_location(name, path, loader=loader)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m               # a dataclass looks its module up here while the module executes
    loader.exec_module(m)
    return m


def write_memory(mem, slug, body):
    with open(os.path.join(mem, slug + ".md"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("---\nname: %s\ndescription: %s\nmetadata:\n  type: project\n---\n\n%s\n" % (slug, body[:60], body))


class Store:
    """A throwaway memory folder with a shadow BM25 index and attached documents."""

    def __init__(self, tag):
        self.td = tempfile.mkdtemp(prefix="attach_iso_%s_" % tag)
        self.mem = os.path.join(self.td, "memory")
        os.makedirs(self.mem)
        os.environ["BRAIN_MEM_DIR"] = self.mem
        os.environ["BRAIN_BM25_INDEX"] = os.path.join(self.td, "bm25_index.json.gz")
        os.environ["BRAIN_DOC_EXPANSION"] = "0"
        os.environ["BRAIN_RECALL_LOG"] = "0"
        os.environ["BRAIN_RECALL_REFRESH"] = "0"      # recall never spawns an index refresh from a test
        os.environ["RECALL_REWRITE_NO_LOG"] = "1"
        if HERE not in sys.path:
            sys.path.insert(0, HERE)
        self.nd = load(DOCS, "nd_%s" % tag)
        sys.modules["brain_node_documents"] = self.nd
        self.bm25 = load(BM25, "bm25")                 # the index code under test, with this store's paths
        assert os.path.abspath(self.bm25.MEM) == os.path.abspath(self.mem), self.bm25.MEM
        assert os.path.abspath(self.bm25.INDEX).startswith(os.path.abspath(self.td)), self.bm25.INDEX

    def memory(self, slug, body):
        write_memory(self.mem, slug, body)
        old = time.time() - 7200
        os.utime(os.path.join(self.mem, slug + ".md"), (old, old))
        # recall seeds its first stage from the index lines, as the live MEMORY.md gives them
        idx = os.path.join(self.mem, "MEMORY.md")
        lines = open(idx, encoding="utf-8").read().splitlines() if os.path.exists(idx) else ["# Memory Index"]
        lines = [l for l in lines if "(%s.md)" % slug not in l] + ["- [%s.md](%s.md) - %s" % (slug, slug, body[:120])]
        with open(idx, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("\n".join(lines) + "\n")

    def attach(self, slug, text):
        src = os.path.join(self.td, slug + "_notes.md")
        with open(src, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        doc = self.nd.attach(self.mem, slug, src, name="notes.md", description="full notes")
        return doc.path

    def build(self):
        import bm25
        bm25.build(self.mem, os.environ["BRAIN_BM25_INDEX"])

    def close(self):
        rmtree(self.td)


def corrupt(path):
    os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
    b = bytearray(open(path, "rb").read())
    b[-2] = ord("X") if b[-2] != ord("X") else ord("Y")
    open(path, "wb").write(bytes(b))


def recall(brain_path, tag, q, n=8):
    b = load(brain_path, "brain_iso_%s" % tag)
    live = os.path.join(os.path.expanduser("~"), ".claude")
    for where in (b.MEM, sys.modules["bm25"].MEM, sys.modules["bm25"].INDEX):
        if os.path.abspath(where).lower().startswith(os.path.abspath(live).lower()):
            raise SystemExit("REFUSED: the recall under test would read the live store (%s)" % where)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            rc = b.recall(q, n=n)
        except SystemExit as e:
            rc = e.code
    out = buf.getvalue()
    return rc, PLAIN.findall(out), out


def fixture(tag, broken=("a",)):
    st = Store(tag)
    st.memory("safe_fixture_memory_2026_09_28", "A plain note about quokkazanzibar fjordwhistle and nothing attached.")
    for s in ("a", "b", "c"):
        st.memory("%s_parent_memory_2026_09_28" % s, "A parent note number %s with an attachment." % s)
    paths = {"a": st.attach("a_parent_memory_2026_09_28", "Full notes: brokenwordlarch appears only here.\n"),
             "b": st.attach("b_parent_memory_2026_09_28", "Full notes: xylophonic zephyr appear only here.\n"),
             "c": st.attach("c_parent_memory_2026_09_28", "Full notes: marmosetglade appears only here.\n")}
    st.build()
    for k in broken:
        corrupt(paths[k])
    return st


def main():
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    load(DOCS, "brain_node_documents")
    st = fixture("i1")
    try:
        fl = []
        hits = st.nd.search_nodes(st.mem, "xylophonic zephyr", failures=fl)
        raised = False
        try:
            st.nd.search_nodes(st.mem, "xylophonic zephyr")
        except Exception:
            raised = True
        check([h[0] for h in hits] == ["b_parent_memory_2026_09_28"] and fl == [("a_parent_memory_2026_09_28",
                                                                                   "AttachmentError")] and raised,
              "I1 A broken, B healthy: hits %s, failures %s, raises without the list %s" % (
                  [h[0] for h in hits], fl, raised))
        rc, rows, out = recall(BRAIN, "i2", "quokkazanzibar fjordwhistle")
        check(rc == 0 and "safe_fixture_memory_2026_09_28" in rows and "INCOMPLETE_ATTACHMENT_CORPUS" in out
              and "a_parent_memory_2026_09_28" in out.split("INCOMPLETE_ATTACHMENT_CORPUS", 1)[1].splitlines()[0],
              "I2 an ordinary memory's query with A broken: exit %s, rows %s, warning names A %s" % (
                  rc, rows[:3], "INCOMPLETE_ATTACHMENT_CORPUS" in out))
        if OLD:
            orc, orows, _oout = recall(OLD, "i2old", "quokkazanzibar fjordwhistle")
            check(orc == 2 and not orows, "N1 the brain before this change exits %s and shows %d rows on I2 (the check can "
                                          "fail)" % (orc, len(orows)))
        rc, rows, out = recall(BRAIN, "i3", "brokenwordlarch")
        check("a_parent_memory_2026_09_28" not in rows and "INCOMPLETE_ATTACHMENT_CORPUS" in out,
              "I3 words only A's broken document holds: A shown %s (exit %s)" % ("a_parent_memory_2026_09_28" in rows, rc))
    finally:
        st.close()
    st = fixture("i4", broken=("a", "c"))
    try:
        rc, rows, out = recall(BRAIN, "i4", "quokkazanzibar fjordwhistle")
        line = out.split("INCOMPLETE_ATTACHMENT_CORPUS", 1)[1].splitlines()[0] if "INCOMPLETE_ATTACHMENT_CORPUS" in out else ""
        check("a_parent_memory_2026_09_28" in line and "c_parent_memory_2026_09_28" in line,
              "I4 two broken bundles, both named: %s" % line[:160])
    finally:
        st.close()
    st = fixture("i5", broken=())
    try:
        if OLD:
            same = all(recall(BRAIN, "i5n%d" % i, q)[1] == recall(OLD, "i5o%d" % i, q)[1]
                       for i, q in enumerate(("xylophonic zephyr", "quokkazanzibar fjordwhistle", "parent note attachment")))
            check(same, "I5 no broken bundle: the displayed rows equal the brain before this change on three queries")
        rc, rows, out = recall(BRAIN, "i5", "xylophonic zephyr")
        check(rc == 0 and "INCOMPLETE_ATTACHMENT_CORPUS" not in out and "b_parent_memory_2026_09_28" in rows,
              "I5b no broken bundle: no warning, B found (exit %s)" % rc)
    finally:
        st.close()
    st = fixture("i6", broken=("b",))
    try:
        st.memory("b_parent_memory_2026_09_28", "A parent note number b about xylophonic zephyr, attachment broken.")
        import bm25
        bm25.build(st.mem, os.environ["BRAIN_BM25_INDEX"])
        rc, rows, out = recall(BRAIN, "i6", "xylophonic zephyr")
        check(rows[:1] == ["b_parent_memory_2026_09_28"] and "AUTO-READ DOCUMENTS ATTACHED TO TOP NODE" not in out
              and rc == 2, "I6 the top node's own bundle broken: top %s, no partial block %s, exit %s" % (
                  rows[:1], "AUTO-READ DOCUMENTS ATTACHED TO TOP NODE" not in out, rc))
    finally:
        st.close()
    print("FAIL" if fails else "PASS", "- %d controls, %d failed" % (len(ran), len(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
