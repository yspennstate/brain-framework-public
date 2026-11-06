"""foresight keeps the parsed correction ledger on disk while MISTAKES.md and instincts.tsv are unchanged: the same
rows as parsing, a reparse after either file changes, and no cache use when it is off or pointed elsewhere."""
import importlib
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))


@pytest.fixture
def fs(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(HERE)
    sys.modules.pop("foresight", None)
    f = importlib.import_module("foresight")
    mem, dr = tmp_path / "memory", tmp_path / "dreaming"
    mem.mkdir()
    dr.mkdir()
    (mem / "MISTAKES.md").write_text("- 2026-11-01 one\n")
    (dr / "instincts.tsv").write_text("id\tgut\n")
    monkeypatch.setattr(f, "MEM", str(mem))
    monkeypatch.setattr(f, "DR", str(dr))
    monkeypatch.setattr(f, "_MIS_HOME", (str(mem), str(dr)))
    monkeypatch.setattr(f, "_MIS_CACHE", str(tmp_path / "cache.json"))
    parses = []

    def parse():                      # stands in for the ledger parse: one row per line, both files read
        parses.append(1)
        with open(os.path.join(f.MEM, "MISTAKES.md")) as fh:
            lines = fh.read().splitlines()
        with open(os.path.join(f.DR, "instincts.tsv")) as fh:
            tag = fh.read().count("\n")
        return [(ln.split()[1], "r%d" % tag, ln) for ln in lines]

    monkeypatch.setattr(f, "_mistakes_parse", parse)
    return f, mem, dr, parses, tmp_path / "cache.json"


def test_a_repeat_is_served_from_the_cache_with_the_same_rows(fs):
    f, _, _, parses, cache = fs
    a = f._mistakes()
    b = f._mistakes()
    assert a == b == [("2026-11-01", "r1", "- 2026-11-01 one")]
    assert all(isinstance(r, tuple) for r in b)
    assert len(parses) == 1 and cache.exists()


def test_a_change_to_either_file_reparses(fs):
    f, mem, dr, parses, _ = fs
    f._mistakes()
    with open(mem / "MISTAKES.md", "a") as fh:
        fh.write("- 2026-11-02 two\n")
    assert len(f._mistakes()) == 2
    with open(dr / "instincts.tsv", "a") as fh:
        fh.write("x\ty\n")
    assert f._mistakes()[0][1] == "r2"
    assert len(parses) == 3


def test_off_or_pointed_elsewhere_never_touches_the_cache(fs, monkeypatch, tmp_path):
    f, _, _, parses, cache = fs
    monkeypatch.setattr(f, "_MIS_CACHE", "0")
    f._mistakes()
    f._mistakes()
    assert len(parses) == 2 and not cache.exists() and not os.path.exists("0")
    monkeypatch.setattr(f, "_MIS_CACHE", str(cache))
    other = tmp_path / "other"
    other.mkdir()
    (other / "MISTAKES.md").write_text("- 2026-11-03 three\n")
    monkeypatch.setattr(f, "MEM", str(other))
    assert f._mistakes()[0][0] == "2026-11-03"
    f._mistakes()
    assert len(parses) == 4 and not cache.exists()
