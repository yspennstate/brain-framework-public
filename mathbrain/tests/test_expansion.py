"""Document expansion for memory-born cards: build status, word choice, the prompt-cluster counts and the sidecar."""
import json
import pickle
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from math_brain import expansion


def _node(nid, title, body="", kind="claim", created="2026-11-01T00:00:00+00:00"):
    return SimpleNamespace(id=nid, title=title, body=body, kind=kind, created_at=created)


def _fixture(tmp_path: Path):
    """One house card born from a memory, two ordinary cards that name the memory's objects, and filler so the
    words stay under the 2% document-frequency cap."""
    store = tmp_path / "brain.sqlite3"
    db = sqlite3.connect(store)
    db.execute("create table claim_provenance (claim_id text, source_ref text)")
    db.execute("insert into claim_provenance values ('house', 'memory:janson_note; extra')")
    db.commit()
    db.close()
    memory = tmp_path / "memory"
    memory.mkdir()
    (memory / "janson_note.md").write_text(
        "---\nname: janson_note\n---\nThe width of a galton watson tree, its moments; audited and proved. "
        "See [[ergodic_coupling]].\n", encoding="utf-8")
    # "audited" (a stop word) and the link's words are title vocabulary too, so only their own filters keep them out
    nodes = [_node("house", "Janson conjecture settled for every real r"),
             _node("c1", "Galton watson tree width moments, audited ergodic coupling"),
             _node("c2", "Moments of galton watson tree width, audited ergodic coupling")]
    nodes += [_node("f%03d" % i, "Filler statement about lattices", "nothing here") for i in range(100)]
    return store, memory, nodes


def test_no_memory_directory_says_unconfigured(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(expansion, "MEMORY_DIR", None)
    assert expansion.build(tmp_path / "s.sqlite3", []) == {}
    assert expansion.LAST_BUILD["status"] == "unconfigured"
    assert expansion.build(tmp_path / "s.sqlite3", [], memory_dir=tmp_path / "absent") == {}
    assert expansion.LAST_BUILD["status"] == "source_missing"


def test_house_card_gets_the_objects_its_memory_names_and_nothing_else(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(expansion, "PROMPTS", None)
    store, memory, nodes = _fixture(tmp_path)
    out = expansion.build(store, nodes, memory_dir=memory, prompts=tmp_path / "no_prompts.jsonl")
    words = set(out["house"].split())
    assert {"galton", "watson", "tree", "width", "moments"} <= words
    assert not words & {"audited", "proved", "janson", "ergodic", "coupling"}
    assert set(out) == {"house"}
    assert expansion.LAST_BUILD["status"] == "success" and expansion.LAST_BUILD["cards"] == 1
    sidecar = pickle.loads(Path(expansion.sidecar_path(store)).read_bytes())
    assert sidecar["expansion"] == out and sidecar["generation"] == "2026-11-01T00:00:00+00:00"


def test_a_word_the_owner_keeps_using_is_left_out(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(expansion, "PROMPTS", None)
    store, memory, nodes = _fixture(tmp_path)
    prompts = tmp_path / "PROMPTS.jsonl"
    rows = [{"origin": "owner", "text": "please look at the tree question number %d again" % i} for i in range(5)]
    prompts.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    words = set(expansion.build(store, nodes, memory_dir=memory, prompts=prompts)["house"].split())
    assert "tree" not in words and "galton" in words


def test_prompt_clusters_count_each_term_once_and_refuse_bad_records(tmp_path: Path) -> None:
    prompts = tmp_path / "PROMPTS.jsonl"
    head = "x" * 300
    lines = [json.dumps({"origin": "owner", "text": head + " martingale"}),
             json.dumps({"origin": "owner", "text": head + " martingale coupling"}),
             json.dumps({"origin": "owner", "text": "short"}),
             json.dumps({"origin": "agent", "text": "an agent talking about coupling at length"}),
             json.dumps({"origin": "owner", "text": 7}),
             json.dumps({"origin": "owner"}),
             json.dumps([1, 2]),
             "{not json"]
    prompts.write_text("\n".join(lines) + "\n", encoding="utf-8")
    df, n, rejected = expansion._his_counts(tmp_path / "cache.pkl", strict=False, prompts=prompts)
    assert n == 1 and df["martingale"] == 1 and df["coupling"] == 1
    assert rejected == {"text_not_a_string": 1, "no_text": 1, "not_an_object": 1, "not_json": 1}
    with pytest.raises(ValueError):
        expansion._his_counts(tmp_path / "cache.pkl", strict=True, prompts=prompts)


def test_prompt_cache_follows_the_bytes_not_the_size(tmp_path: Path) -> None:
    prompts = tmp_path / "PROMPTS.jsonl"
    cache = tmp_path / "cache.pkl"
    prompts.write_text(json.dumps({"origin": "owner", "text": "the martingale question"}) + "\n", encoding="utf-8")
    assert "martingale" in expansion._his_counts(cache, strict=False, prompts=prompts)[0]
    prompts.write_text(json.dumps({"origin": "owner", "text": "the ergodicity question"}) + "\n", encoding="utf-8")
    df = expansion._his_counts(cache, strict=False, prompts=prompts)[0]
    assert "martingale" not in df and "ergodicity" in df


def test_an_older_build_never_replaces_a_newer_sidecar(tmp_path: Path) -> None:
    path = str(tmp_path / "s.sqlite3.expansion.pkl")
    assert expansion._publish(path, {"generation": "2026-11-02", "expansion": {"a": "new"}})
    assert not expansion._publish(path, {"generation": "2026-11-01", "expansion": {"a": "old"}})
    assert pickle.loads(Path(path).read_bytes())["expansion"] == {"a": "new"}
    assert expansion._publish(path, {"generation": "2026-11-03", "expansion": {"a": "newer"}})
