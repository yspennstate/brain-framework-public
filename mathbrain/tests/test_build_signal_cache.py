"""The signal cache builder: what it writes is what retrieve() looks up, and a publish leaves one whole file."""
import pickle

import pytest

from math_brain import build_signal_cache as bsc
from math_brain import retrieval as R
from math_brain.models import Claim, ClaimKind
from math_brain.store import ResearchStore


def _build(tmp_path, monkeypatch):
    store = tmp_path / "brain.sqlite3"
    with ResearchStore(store) as s:
        s.add_claim(Claim("For every integer n, n + 0 = n.", ClaimKind.THEOREM))
        s.add_claim(Claim("The sum of the first n odd numbers is n^2.", ClaimKind.THEOREM))
        nodes = list(s.iter_nodes())
    monkeypatch.setattr(bsc, "STORE", store)
    monkeypatch.setattr(bsc, "CACHE", tmp_path / "brain_signal_cache.pkl")
    assert bsc.main() == 0
    return nodes, tmp_path / "brain_signal_cache.pkl"


def test_the_cache_holds_exactly_the_text_retrieve_indexes(tmp_path, monkeypatch) -> None:
    nodes, cache = _build(tmp_path, monkeypatch)
    data = pickle.loads(cache.read_bytes())
    assert set(data) == {R._signal_key(n.title + " " + n.body) for n in nodes if (n.title + n.body).strip()}


def test_retrieve_hits_the_built_cache_without_recomputing(tmp_path, monkeypatch) -> None:
    nodes, cache = _build(tmp_path, monkeypatch)
    monkeypatch.setattr(R, "_SIGNAL_MEMO", {})
    monkeypatch.setattr(R, "_DISK_SIGNALS", {})
    assert R.load_signal_cache(cache) == len(pickle.loads(cache.read_bytes()))

    def recompute(_text):
        raise AssertionError("cache miss: the builder's key is not the one retrieve() looks up")

    monkeypatch.setattr(R, "_index_signals_uncached", recompute)
    for n in nodes:
        R.index_signals(n.title + " " + n.body)


def test_publish_leaves_one_whole_file_and_no_temporaries(tmp_path) -> None:
    dest = tmp_path / "brain_signal_cache.pkl"
    dest.write_bytes(b"old")
    bsc.publish({"k1": 1, "k2": 2}, dest)
    assert pickle.loads(dest.read_bytes()) == {"k1": 1, "k2": 2}
    assert [p.name for p in tmp_path.iterdir()] == [dest.name]


def test_a_failed_replace_keeps_the_old_cache_and_removes_its_temporary(tmp_path, monkeypatch) -> None:
    import os

    dest = tmp_path / "brain_signal_cache.pkl"
    dest.write_bytes(b"old")

    def held_open(src, dst):
        raise PermissionError("a reader holds the destination open")

    monkeypatch.setattr(os, "replace", held_open)
    with pytest.raises(PermissionError):
        bsc.publish({"k1": 1}, dest, tries=1)
    assert dest.read_bytes() == b"old"
    assert [p.name for p in tmp_path.iterdir()] == [dest.name]


def test_a_bad_cache_file_loads_nothing_instead_of_failing(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(R, "_DISK_SIGNALS", {})
    bad = tmp_path / "c.pkl"
    bad.write_bytes(b"not a pickle")
    assert R.load_signal_cache(bad) == 0
    bad.write_bytes(pickle.dumps(["a", "list"]))
    assert R.load_signal_cache(bad) == 0
    assert R.load_signal_cache(tmp_path / "absent.pkl") == 0
