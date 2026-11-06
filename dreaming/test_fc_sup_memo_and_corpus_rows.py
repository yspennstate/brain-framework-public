"""The speed layer's supersession memo outlives one recall, and the attach-corpus cache takes either row shape."""
import importlib.util
import os
import sys
import types

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))


@pytest.fixture()
def brain(tmp_path, monkeypatch):
    monkeypatch.setenv("BRAIN_RECALL_CACHE_DIR", str(tmp_path / "fc"))
    monkeypatch.setenv("BRAIN_RECALL_LOG", "0")
    monkeypatch.syspath_prepend(HERE)
    spec = importlib.util.spec_from_file_location("brain_fc_under_test", os.path.join(HERE, "brain.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _closure_values(fn, seen=None):
    seen = set() if seen is None else seen
    if id(fn) in seen:
        return []
    seen.add(id(fn))
    out = []
    for c in getattr(fn, "__closure__", None) or ():
        try:
            v = c.cell_contents
        except ValueError:
            continue
        out.append(v)
        if callable(v):
            out += _closure_values(v, seen)
    return out


def test_the_supersession_memo_is_shared_by_every_wrap(brain):
    for _ in range(2):
        w = brain._fc_superseded_wrap(lambda: {})
        assert any(v is brain._FC_SUP_MEMO for v in _closure_values(w))


def test_corpus_rows_are_copied_in_either_shape(brain):
    tri = ("a_slug", 7, {"x": 2})
    pair = ("b_slug", ["x", "y"])
    t2, p2 = brain._fc_att_row(tri), brain._fc_att_row(pair)
    assert t2 == tri and p2 == pair
    assert t2[2] is not tri[2] and p2[1] is not pair[1]


def _fake_store(tmp_path):
    mem = tmp_path / "mem"
    (mem / "bundles").mkdir(parents=True)
    (mem / "bundles" / "one.json").write_text("{}")
    nd = types.SimpleNamespace(BUNDLE_DIRNAME="bundles", __file__=os.path.join(HERE, "brain.py"))
    return mem, nd


@pytest.mark.parametrize("rows", [[("a_slug", ["x", "y"]), ("b_slug", ["z"])],
                                  [("a_slug", 2, {"x": 1}), ("b_slug", 1, {"z": 1})]])
def test_a_second_search_is_served_from_the_cache(brain, tmp_path, rows):
    mem, nd = _fake_store(tmp_path)
    calls = []

    def real(m, failures=None):
        calls.append(m)
        return [tuple(r) for r in rows]

    w = brain._fc_attach_corpus_wrap(nd, real)
    first = w(mem)
    second = w(mem)
    assert first == second == [tuple(r) for r in rows]
    assert len(calls) == 1


def test_an_entry_under_the_old_name_is_never_served(brain, tmp_path):
    mem, nd = _fake_store(tmp_path)
    w = brain._fc_attach_corpus_wrap(nd, lambda m, failures=None: [("fresh", 1, {"a": 1})])
    root = os.path.join(os.fspath(mem), "bundles")
    st = os.lstat(os.path.join(root, "one.json"))
    key = (os.path.abspath(root), ((".", "one.json", st.st_size, st.st_mtime_ns, st.st_ino, st.st_ctime_ns),),
           brain._fc_ident(nd.__file__))
    brain._fc_put("attcorpus3", key, [("stale", 1, {"b": 1})])
    assert w(mem) == [("fresh", 1, {"a": 1})]
