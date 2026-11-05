"""Requests as nodes, the request prompt hook, and the nightly review queue."""
import json
from pathlib import Path

import pytest

from math_brain import nightly, request_hook
from math_brain.models import Claim, ClaimKind
from math_brain.requests import LedgerError, RequestLedger, redact, request_id
from math_brain.store import ResearchStore

ASK = "Is every group of prime order cyclic?"


def test_two_typings_of_one_ask_are_one_request(tmp_path: Path) -> None:
    led = RequestLedger(tmp_path / "requests.jsonl")
    a = led.ask(ASK, at="2026-10-03T10:00:00+00:00")
    b = led.ask("is EVERY group of prime order cyclic", at="2026-10-05T10:00:00+00:00")
    assert a.id == b.id == request_id(ASK)
    req = led.get(a.id)
    assert req.times_asked == 2
    assert (req.first_asked, req.last_asked) == ("2026-10-03T10:00:00+00:00", "2026-10-05T10:00:00+00:00")


def test_state_folds_back_from_the_ledger_file(tmp_path: Path) -> None:
    path = tmp_path / "requests.jsonl"
    led = RequestLedger(path)
    rid = led.ask(ASK).id
    led.link(rid, "claim_ab12", "produced")
    led.link(rid, "claim_cd34", "answered_by")
    again = RequestLedger(path).get(rid)
    assert again.status == "answered"
    assert again.linked("produced") == ["claim_ab12"] and again.linked("answered_by") == ["claim_cd34"]


def test_asking_again_after_an_answer_reopens_it(tmp_path: Path) -> None:
    led = RequestLedger(tmp_path / "requests.jsonl")
    rid = led.ask(ASK).id
    led.link(rid, "claim_cd34", "answered_by")
    assert led.ask(ASK).status == "open"
    assert led.get(rid).status_note == "asked again after an answer"


def test_bad_relations_and_follows_are_refused(tmp_path: Path) -> None:
    led = RequestLedger(tmp_path / "requests.jsonl")
    rid = led.ask(ASK).id
    with pytest.raises(LedgerError):
        led.link(rid, "claim_x", "cites")
    with pytest.raises(LedgerError):
        led.link(rid, "req_000000000000", "follows")
    with pytest.raises(LedgerError):
        led.link("req_missing", "claim_x", "produced")
    with pytest.raises(LedgerError):
        led.ask("  ?! ")


def test_redact_removes_email_and_home_paths() -> None:
    # paths assembled here so the repository's own home-path guard does not flag the fixture
    linux_home, mac_home = "/" + "home/alice", "/" + "Users/bob"
    text = redact("mail a@b.org about %s/notes.tex and %s/x" % (linux_home, mac_home))
    assert "a@b.org" not in text and "alice" not in text and "bob" not in text
    assert "<email>" in text and "<home>" in text


def test_a_torn_last_line_is_skipped_but_earlier_damage_is_not(tmp_path: Path) -> None:
    path = tmp_path / "requests.jsonl"
    RequestLedger(path).ask(ASK)
    good = path.read_text(encoding="utf-8")
    path.write_text(good + '{"op": "ask", "id": "req_', encoding="utf-8")
    assert RequestLedger(path).get(request_id(ASK)) is not None
    path.write_text('{"op": broken\n' + good, encoding="utf-8")
    with pytest.raises(LedgerError):
        RequestLedger(path)


def test_importing_a_prompt_archive_twice_adds_nothing(tmp_path: Path) -> None:
    rows = [
        {"text": ASK, "ts": "2026-10-03T14:00:00"},
        {"text": "ok do it", "ts": "2026-10-03T14:01:00"},
        {"text": "keep working on the cyclic group proof", "ts": "2026-10-03T14:05:00", "origin": "automation"},
    ]
    led = RequestLedger(tmp_path / "requests.jsonl")
    first = led.import_prompts(rows)
    assert (first["added"], first["short"], first["automation"]) == (1, 1, 1)
    second = RequestLedger(tmp_path / "requests.jsonl").import_prompts(rows)
    assert (second["added"], second["already"]) == (0, 1)


def test_similar_finds_the_earlier_ask_and_not_an_unrelated_one(tmp_path: Path) -> None:
    led = RequestLedger(tmp_path / "requests.jsonl")
    led.ask(ASK)
    led.ask("What is the mixing time of the lazy random walk on the hypercube?")
    hits = led.similar("is a group of prime order always cyclic")
    assert [r.id for _, r in hits] == [request_id(ASK)]


def test_request_hook_shows_earlier_work_and_never_matches_itself(tmp_path: Path) -> None:
    path = tmp_path / "requests.jsonl"
    led = RequestLedger(path)
    rid = led.ask(ASK, at="2026-10-03T10:00:00+00:00").id
    led.link(rid, "claim_ab12", "produced")
    env = {"MATH_BRAIN_REQUESTS": str(path)}
    out = request_hook.run({"prompt": "is every group of prime order cyclic, again?"}, env)
    assert rid in out and "claim_ab12" in out and "since 2026-10-03" in out
    assert request_hook.run({"prompt": "ok go on"}, env) == ""
    assert request_hook.run({"prompt": ASK}, {}) == ""
    fresh = "Does the Collatz map have a cycle other than 1 4 2?"
    record = dict(env, MATH_BRAIN_REQUESTS_RECORD="1")
    assert request_hook.run({"prompt": fresh}, record) == ""
    assert RequestLedger(path).get(request_id(fresh)) is not None


def test_request_hook_main_fails_open_and_wraps_for_codex(tmp_path: Path, monkeypatch, capsys) -> None:
    import io
    import sys

    path = tmp_path / "requests.jsonl"
    RequestLedger(path).ask(ASK)
    monkeypatch.setenv("MATH_BRAIN_REQUESTS", str(path))
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b"not json")))
    assert request_hook.main([]) == 0 and capsys.readouterr().out == ""
    payload = json.dumps({"prompt": "every group of prime order is cyclic, right?"}).encode()
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b"\xef\xbb\xbf" + payload)))
    assert request_hook.main(["--codex"]) == 0
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    assert request_id(ASK) in envelope["hookSpecificOutput"]["additionalContext"]


def _store_with_claims(path: Path, *texts: str) -> None:
    with ResearchStore(path) as store:
        for text in texts:
            store.add_claim(Claim(text, ClaimKind.THEOREM))


def test_nightly_queue_remembers_across_nights_and_skips_an_unchanged_store(tmp_path: Path) -> None:
    db, queue = tmp_path / "brain.sqlite3", tmp_path / "queue.jsonl"
    _store_with_claims(db, "For every integer n, n + 0 = n.")
    first = nightly.run(db, queue, at="2026-11-01T02:00:00+00:00")
    assert first["changed"] and first["proposals"] >= 1 and len(first["new"]) == first["proposals"]
    size = queue.stat().st_size
    second = nightly.run(db, queue, at="2026-11-02T02:00:00+00:00")
    assert not second["changed"] and second["new"] == [] and queue.stat().st_size == size
    with ResearchStore(db) as store:
        store.add_claim(Claim("For every integer n, n * 1 = n.", ClaimKind.THEOREM))
    third = nightly.run(db, queue, at="2026-11-03T02:00:00+00:00")
    assert third["changed"]
    q = nightly.Queue.load(queue)
    assert q.nights == 2
    carried = [i for i in q.items.values() if i.id in first["new"] and i.present]
    assert carried and all(i.nights_seen == 2 for i in carried)


def test_a_reviewed_proposal_leaves_the_open_list(tmp_path: Path) -> None:
    db, queue = tmp_path / "brain.sqlite3", tmp_path / "queue.jsonl"
    _store_with_claims(db, "For every integer n, n + 0 = n.")
    report = nightly.run(db, queue, at="2026-11-01T02:00:00+00:00")
    q = nightly.Queue.load(queue)
    target = q.open_items()[0].id
    q.review(target, "done", "edge added")
    reloaded = nightly.Queue.load(queue)
    assert target not in {i.id for i in reloaded.open_items()}
    assert len(reloaded.open_items()) == report["open"] - 1
    with pytest.raises(ValueError):
        reloaded.review(target, "maybe")
    with pytest.raises(KeyError):
        reloaded.review("proposal_missing", "done")
