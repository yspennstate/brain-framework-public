"""instinct_prime keeps exact_clauses' rendered line per prompt: the same line as rendering it, never a stale one."""
import importlib
import json
import os
import sys
import types

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
SESSION = "00000000-aaaa-bbbb-cccc-000000000000"


@pytest.fixture
def ec(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(HERE)
    sys.modules.pop("instinct_prime", None)
    ip = importlib.import_module("instinct_prime")
    ip._SLOW_EVENT["done"] = True                     # no slow-call line from the test process
    src = tmp_path / "exact_clauses.py"
    src.write_text("# stand-in\n")
    monkeypatch.setattr(ip, "__file__", str(tmp_path / "instinct_prime.py"))   # _ec_ident stats the file beside it
    monkeypatch.setattr(ip, "_EC_CACHE", str(tmp_path / "ec_cache.json"))
    calls = []
    fake = types.ModuleType("exact_clauses")
    fake.version = "v1"
    fake.render = lambda text: calls.append(text) or ("[%s] %s" % (fake.version, text) if "not" in text else "")
    monkeypatch.setitem(sys.modules, "exact_clauses", fake)
    monkeypatch.setitem(ip._SLOW_EVENT, "session", SESSION)
    return ip, src, fake, calls, tmp_path / "ec_cache.json"


def test_a_repeat_is_served_from_the_cache_with_the_same_line(ec):
    ip, _, _, calls, cache = ec
    for _ in range(2):
        assert ip._exact_clause_context("UserPromptSubmit", "use the app, not the api") == "[v1] use the app, not the api"
        assert ip._exact_clause_context("UserPromptSubmit", "what time is it") == ""
    assert len(calls) == 2
    assert len(json.loads(cache.read_text())["v"]) == 2
    assert ip._exact_clause_context("PreToolUse", "use the app, not the api") == ""


def test_an_edit_to_exact_clauses_empties_the_cache(ec):
    ip, src, fake, calls, _ = ec
    ip._exact_clause_context("UserPromptSubmit", "do not deploy")
    fake.version = "v2"
    src.write_text("# stand-in, edited\n")
    assert ip._exact_clause_context("UserPromptSubmit", "do not deploy") == "[v2] do not deploy"
    assert len(calls) == 2


def test_only_a_real_session_writes_and_the_switch_turns_it_off(ec, monkeypatch):
    ip, _, _, calls, cache = ec
    monkeypatch.setitem(ip._SLOW_EVENT, "session", "")
    ip._exact_clause_context("UserPromptSubmit", "do not deploy")
    ip._exact_clause_context("UserPromptSubmit", "do not deploy")
    assert len(calls) == 2 and not cache.exists()
    monkeypatch.setitem(ip._SLOW_EVENT, "session", SESSION)
    monkeypatch.setattr(ip, "_EC_CACHE", "0")
    ip._exact_clause_context("UserPromptSubmit", "do not deploy")
    ip._exact_clause_context("UserPromptSubmit", "do not deploy")
    assert len(calls) == 4
    assert not os.path.exists("0")
