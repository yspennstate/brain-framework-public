"""Controls for lever 15 in brain_recall_server: a memory's token list is kept while its files are unchanged and
read again the moment one of them changes. Run: python test_recall_server_read_doc_memo.py (or pytest)."""
import os, sys, tempfile, time, types
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import brain_recall_server as S


def _setup():
    mem = tempfile.mkdtemp()
    calls = []
    bm25 = types.SimpleNamespace(ATTACHMENT_FAILURES=[], _DATES_ON=True, _EXPANSION_ON=False)

    def read_doc(mem_dir, slug):
        calls.append(slug)
        return open(os.path.join(mem_dir, slug + ".md"), encoding="utf-8").read().split()
    bm25.read_doc = read_doc
    bnd = types.SimpleNamespace(Path=Path, MANIFEST_NAME="manifest.json",
                                bundle_dir=lambda m, s: Path(m) / "bundles" / s,
                                _node_path=lambda m, s: Path(m) / "nodes" / (s + ".md"),
                                _slug=lambda s: s)
    me = types.SimpleNamespace(miss={})
    S.Brain._stat_memo_read_doc(me, bm25, bnd)
    return mem, bm25, bnd, calls


def _write(mem, slug, text):
    p = os.path.join(mem, slug + ".md")
    open(p, "w", encoding="utf-8").write(text)
    t = time.time() + len(text)          # a distinct mtime for each write
    os.utime(p, (t, t))


def test_unchanged_file_is_read_once():
    mem, bm25, _, calls = _setup()
    _write(mem, "a", "alpha beta")
    assert bm25.read_doc(mem, "a") == ["alpha", "beta"]
    assert bm25.read_doc(mem, "a") == ["alpha", "beta"]
    assert calls == ["a"]


def test_changed_file_is_read_again():
    mem, bm25, _, calls = _setup()
    _write(mem, "a", "alpha")
    bm25.read_doc(mem, "a")
    _write(mem, "a", "alpha gamma delta")
    assert bm25.read_doc(mem, "a") == ["alpha", "gamma", "delta"]
    assert calls == ["a", "a"]


def test_new_attachment_document_is_read_again():
    mem, bm25, bnd, calls = _setup()
    _write(mem, "a", "alpha")
    bm25.read_doc(mem, "a")
    d = bnd.bundle_dir(mem, "a") / "documents"
    d.mkdir(parents=True)
    (d / "x.txt").write_text("doc")
    bm25.read_doc(mem, "a")
    assert calls == ["a", "a"]


def test_failed_attachment_read_is_not_kept():
    mem, bm25, _, calls = _setup()
    _write(mem, "a", "alpha")
    real = bm25.read_doc.__wrapped__

    def failing(m, s):
        bm25.ATTACHMENT_FAILURES.append(s)
        return real(m, s)
    me = types.SimpleNamespace(miss={})
    bm25.read_doc = failing
    S.Brain._stat_memo_read_doc(me, bm25, types.SimpleNamespace(
        Path=Path, MANIFEST_NAME="manifest.json", bundle_dir=lambda m, s: Path(m) / "bundles" / s,
        _node_path=lambda m, s: Path(m) / "nodes" / (s + ".md"), _slug=lambda s: s))
    bm25.read_doc(mem, "a")
    bm25.read_doc(mem, "a")
    assert calls == ["a", "a"]


def test_callers_get_their_own_copy():
    mem, bm25, _, _ = _setup()
    _write(mem, "a", "alpha beta")
    first = bm25.read_doc(mem, "a")
    first.append("mutated")
    assert bm25.read_doc(mem, "a") == ["alpha", "beta"]


if __name__ == "__main__":
    if "-h" in __import__("sys").argv[1:] or "--help" in __import__("sys").argv[1:]:
        print((__doc__ or "").strip() or "usage: python %s" % __import__("os").path.basename(__file__))
        raise SystemExit(0)
    n = 0
    for k, f in sorted(globals().items()):
        if k.startswith("test_") and callable(f):
            f()
            n += 1
            print("ok", k)
    print("%d passed" % n)
