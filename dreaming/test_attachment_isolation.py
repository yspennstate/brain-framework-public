"""test_attachment_isolation.py - one attachment bundle that fails verification no longer stops every deliberate recall
(28 Sep 2026, claude-825b1305; scheduled audit account03/B02 r68, B02-R1-ATTACHMENT-CORRUPTION-GLOBAL-RECALL-STOP-001).

    python test_attachment_isolation.py [--brain BRAIN.py] [--docs NODE_DOCUMENTS.py] [--bm25 BM25.py] [--old OLD_BRAIN.py]
                                        [--old-r71 OLD_BRAIN.py] [--old-bm25 OLD_BM25.py]

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

I7-I10 (28 Sep 2026, scheduled audit account03/B02 r71, B02-R23-ATTACHMENT-STALE-BM25-DIGEST-BYPASS-001): the index is
built while a's bundle is valid, and the query also holds words every parent's own index line has, so recall reaches
BM25 (I3's attachment-only query returns before it). a must score exactly as its twin in a store where the bundle
never existed:
 I7 the bundle damaged, its mtime kept, refresh on      I8 the same, refresh off
 I9 the bundle damaged, its mtime moved (the ordinary case)
 I10 the bundle replaced by another VALID bundle with every file's old times kept: every row scores as in the twin
     built with the new bundle (compared on scores: a stable sort breaks exact ties by index insertion order)
With --old-r71, the brain before this change must rank a on the rejected words (N2); with --old-bm25, the index before
this change must keep the replaced bundle's text (N3). A Store stubs bm25's detached spawns, so a test leaves no process.
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
OLD_BM25 = os.path.abspath(ARGS[ARGS.index("--old-bm25") + 1]) if "--old-bm25" in ARGS else None
OLD_R71 = os.path.abspath(ARGS[ARGS.index("--old-r71") + 1]) if "--old-r71" in ARGS else None
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

    def __init__(self, tag, bm25_path=None):
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
        self.bm25 = load(bm25_path or BM25, "bm25")    # the index code under test, with this store's paths
        # a test never leaves a detached process behind: one still writing the postings sidecar held a file open while
        # the store was being removed
        self.bm25.spawn_detached_postings = lambda *a, **k: None
        self.bm25.spawn_detached_refresh = lambda *a, **k: None
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


RARE = "brokenwordlarch quillfernmoss tarnwhistle vellumcrag osprinth"


def stale_store(tag, a_text, bm25_path=None, gap=True):
    """Safe memory + parents a, b, c; a's attachment holds a_text (None = no attachment). The index is built more than
    a second after the attachments, so the refresh sees no bundle as fresh by its mtime."""
    st = Store(tag, bm25_path)
    st.memory("safe_fixture_memory_2026_09_28", "A plain note about quokkazanzibar fjordwhistle and nothing attached.")
    for s in ("a", "b", "c"):
        st.memory("%s_parent_memory_2026_09_28" % s, "A parent note number %s with an attachment." % s)
    pa = st.attach("a_parent_memory_2026_09_28", a_text) if a_text is not None else None
    st.attach("b_parent_memory_2026_09_28", "Full notes: xylophonic zephyr appear only here.\n")
    st.attach("c_parent_memory_2026_09_28", "Full notes: marmosetglade appears only here.\n")
    if gap:
        time.sleep(2.2)
    st.build()
    return st, pa


def bundle_times(root):
    out = {}
    for dirpath, _dirs, files in os.walk(root):
        for n in [dirpath] + [os.path.join(dirpath, f) for f in files]:
            s = os.stat(n)
            out[os.path.relpath(n, root)] = (s.st_atime_ns, s.st_mtime_ns)
    return out


def logged(brain_path, tag, q, st, refresh="sync"):
    """recall with its log captured; refresh 'sync' publishes in process (no detached spawn from a test), 'off' none"""
    log = os.path.join(st.td, "recall_%s.jsonl" % tag)
    keys = ("BRAIN_RECALL_LOG", "BRAIN_RECALL_LOG_FULL", "BRAIN_RECALL_LOG_PATH", "BRAIN_RECALL_REFRESH",
            "BRAIN_RECALL_PUBLISH")
    saved = {k: os.environ.get(k) for k in keys}
    os.environ.update(BRAIN_RECALL_LOG="1", BRAIN_RECALL_LOG_FULL="1", BRAIN_RECALL_LOG_PATH=log,
                      BRAIN_RECALL_REFRESH="0" if refresh == "off" else "1", BRAIN_RECALL_PUBLISH="sync")
    try:
        rc, rows, out = recall(brain_path, tag, q)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    rec = {}
    if os.path.exists(log):
        with open(log, encoding="utf-8") as fh:
            lines = [l for l in fh if l.strip()]
        if lines:
            import json
            rec = json.loads(lines[-1])
    a = [v for s, v in rec.get("body") or [] if s == "a_parent_memory_2026_09_28"]
    logged.last_body = {s: round(v, 9) for s, v in rec.get("body") or []}
    return rc, rows, out, (a[0] if a else None)


def stale_cases(brain_path, tag, bm25_path=None):
    """I7-I10 on one brain: returns {case: (damaged score of a, twin score of a, rows, twin rows)}"""
    res = {}
    # "parent number attachment" are in every parent's own index line, so recall has a foothold and reaches BM25 in the
    # twin too; without it both stores return before scoring and agree vacuously
    q = RARE + " parent number attachment"
    text = "Full notes: %s appear only here.\n" % ((RARE + " ") * 3)
    twin, _p = stale_store(tag + "_twin", None, bm25_path)
    try:
        _rc, trows, _o, tscore = logged(brain_path, tag + "_tw", q, twin)
    finally:
        twin.close()
    for case, keep, refresh in (("I7", True, "sync"), ("I8", True, "off"), ("I9", False, "sync")):
        st, pa = stale_store("%s_%s" % (tag, case), text, bm25_path)
        try:
            m = os.stat(pa)
            corrupt(pa)
            if keep:
                os.utime(pa, ns=(m.st_atime_ns, m.st_mtime_ns))
            if case == "I7":
                import bm25 as _b
                top = (_b.BM25().score(RARE, 3) or [(None, 0.0)])[0]
                res["setup"] = top
            _rc, rows, _o, score = logged(brain_path, "%s_%s_r" % (tag, case), q, st, refresh)
            res[case] = (score, tscore, rows, trows)
        finally:
            st.close()
    # I10: a valid bundle replaced by another valid bundle, every file keeping its old times
    new_text = "Full notes: newleafword appears only here.\n"
    for qq, key in ((q, "I10_old_words"), ("newleafword parent number attachment", "I10_new_word")):
        twin, _p = stale_store(tag + "_twin10", new_text, bm25_path)
        try:
            _rc, trows1, _o, tscore = logged(brain_path, tag + "_tw10", qq, twin)
            _rc, trows, _o, _ts2 = logged(brain_path, tag + "_tw10b", qq, twin)     # recall twice, as the store below does
            tbody = logged.last_body
        finally:
            twin.close()
        # the donor is made and closed BEFORE the store under test: a Store points the environment and the loaded bm25
        # at itself, so a donor made second silently became the store every later recall read
        donor, _pd = stale_store("%s_%s_donor" % (tag, key), new_text, bm25_path, gap=False)
        saved = tempfile.mkdtemp(prefix="attach_iso_bundle_")
        shutil.rmtree(saved)
        shutil.copytree(str(donor.nd.bundle_dir(donor.mem, "a_parent_memory_2026_09_28")), saved)
        donor.close()
        st, pa = stale_store("%s_%s" % (tag, key), text, bm25_path)
        try:
            broot = str(st.nd.bundle_dir(st.mem, "a_parent_memory_2026_09_28"))
            times = bundle_times(broot)
            shutil.rmtree(broot)
            shutil.copytree(saved, broot)
            for rel, (at, mt) in sorted(times.items(), key=lambda kv: -len(kv[0])):
                p = os.path.join(broot, rel)
                if os.path.exists(p):
                    os.utime(p, ns=(at, mt))
            for dirpath, _dirs, files in os.walk(broot):       # anything new in the donor gets the oldest time seen
                for n in [dirpath] + [os.path.join(dirpath, f) for f in files]:
                    if os.path.relpath(n, broot) not in times:
                        t0 = min(v[1] for v in times.values())
                        os.utime(n, ns=(t0, t0))
            assert os.path.abspath(sys.modules["bm25"].MEM) == os.path.abspath(st.mem), "recall would read another store"
            _rc, rows, _o, score = logged(brain_path, "%s_%s_r" % (tag, key), qq, st)
            # the seed arm (spread_recall) reads the published index before this recall's refresh merges the new
            # bundle; the refresh publishes, so the recall after it must match the twin row for row
            _rc2, rows2, _o2, score2 = logged(brain_path, "%s_%s_r2" % (tag, key), qq, st)
            # every row's score, not the order of exact ties: an incremental refresh re-inserts the re-read parent at
            # the end of the index, and a stable sort then breaks an exact tie the other way (seen 28 Sep, a and c both
            # at 1.8266)
            res[key] = (score, tscore, logged.last_body, tbody, rows == trows1, score2)
        finally:
            st.close()
            shutil.rmtree(saved, ignore_errors=True)
    return res


def same_score(x, y):
    return x is not None and y is not None and abs(x - y) < 1e-9


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
    # I7-I10 (28 Sep, account03/B02 r71, B02-R23-ATTACHMENT-STALE-BM25-DIGEST-BYPASS-001): the index was built while
    # a's bundle was valid; the query reaches BM25 through "attachment", which every parent's own text holds. A parent
    # whose bundle is left out must score exactly as its twin in a store where that bundle never existed.
    r = stale_cases(BRAIN, "st")
    check(r["setup"][0] == "a_parent_memory_2026_09_28" and r["setup"][1] >= 6.0,
          "I7 setup: the stale index still ranks a first on the rejected words at %.2f (spread_recall's seed arm takes "
          "hits from 6.0)" % r["setup"][1])
    for case, what in (("I7", "damaged, mtime kept, refresh on"), ("I8", "damaged, mtime kept, refresh off"),
                       ("I9", "damaged, mtime moved, refresh on (the ordinary case)")):
        s, t, rows, trows = r[case]
        check(same_score(s, t) and rows == trows, "%s %s: a scores %s, its twin with no bundle %s; rows equal %s" % (
            case, what, s, t, rows == trows))
    for key, what in (("I10_old_words", "the replaced bundle's words"), ("I10_new_word", "the new bundle's word")):
        s, t, body2, tbody, first_rows_equal, s2 = r[key]
        check(same_score(s, t) and same_score(s2, t) and body2 == tbody and bool(tbody),
              "%s a valid bundle replaced with its old times kept, on %s: a scores %s, its twin built with the new "
              "bundle %s; every row's score equals the twin's %s (%d rows; first recall's order equal %s)" % (
                  key.split("_")[0], what, s, t, body2 == tbody, len(tbody), first_rows_equal))
    if OLD_R71:
        o = stale_cases(OLD_R71, "stold", OLD_BM25)
        s, t = o["I7"][0], o["I7"][1]
        check(s is not None and t is not None and s > t + 1e-6,
              "N2 the brain before this change ranks a on the rejected words in I7: %s against its twin's %s (the check "
              "can fail)" % (s, t))
        if OLD_BM25:
            s, t = o["I10_new_word"][0], o["I10_new_word"][1]
            check(not same_score(s, t), "N3 the index before this change keeps the replaced bundle's text in I10: a scores %s, "
                  "the twin %s (the check can fail)" % (s, t))
    print("FAIL" if fails else "PASS", "- %d controls, %d failed" % (len(ran), len(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
