"""pre_report_check finds the current turn from the newest user text record: tool results are not a boundary, an
older turn's observation does not count, and the last assistant text comes from the current turn."""
import importlib.util
import json
import os

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))


@pytest.fixture(scope="module")
def prc():
    spec = importlib.util.spec_from_file_location("prc_under_test", os.path.join(HERE, "pre_report_check.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _rec(role, content, compact=True):
    sep = (",", ":") if compact else (", ", ": ")
    return json.dumps({"type": role, "message": {"role": role, "content": content}}, separators=sep)


def _tool(name):
    return _rec("assistant", [{"type": "tool_use", "name": name, "input": {}}])


def _result():
    return _rec("user", [{"type": "tool_result", "content": "ok"}])


def _write(tmp_path, lines):
    p = tmp_path / "t.jsonl"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(p)


def test_an_older_turns_observation_does_not_count(prc, tmp_path):
    lines = [_rec("user", "first ask"), _tool("Bash"), _result(), _rec("assistant", [{"type": "text", "text": "old"}]),
             _rec("user", "second ask"), _rec("assistant", [{"type": "text", "text": "done, all green"}])]
    assert prc._last_assistant(_write(tmp_path, lines)) == ("done, all green", False)


def test_a_tool_result_is_not_a_boundary(prc, tmp_path):
    lines = [_rec("user", "ask"), _tool("Read"), _result(), _rec("assistant", [{"type": "text", "text": "read it"}])]
    assert prc._last_assistant(_write(tmp_path, lines)) == ("read it", True)


def test_spaced_json_and_string_content_are_read_the_same(prc, tmp_path):
    lines = [_rec("user", "ask", compact=False), _tool("Grep"), _rec("user", "next", compact=False),
             _rec("assistant", "plain string reply", compact=False)]
    assert prc._last_assistant(_write(tmp_path, lines)) == ("plain string reply", False)


def test_no_user_text_in_the_tail_borrows_nothing(prc, tmp_path):
    lines = [_tool("Bash"), _result(), _rec("assistant", [{"type": "text", "text": "x"}])]
    assert prc._last_assistant(_write(tmp_path, lines)) == ("x", False)
