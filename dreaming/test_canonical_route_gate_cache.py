"""canonical_route keeps the gate's verdict per prompt: the same answer as scoring it, and never a stale one."""
import importlib
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
NODE = "canonical_brain_improvement_prompt_v2_2026_08_15"
SESSION = "00000000-aaaa-bbbb-cccc-000000000000"

FAKE_BRAIN = '''
import json, os
CALLS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "calls.txt")
def _load_recall_policy():
    with open(os.environ["BRAIN_RECALL_POLICY"]) as fh:
        return json.load(fh)["nodes"]
def _intent_gate_strength(prompt, gate):
    with open(CALLS, "a") as fh:
        fh.write("x")
    return %s if "brain" in prompt else 0.0
'''


@pytest.fixture
def route(tmp_path, monkeypatch):
    fake = tmp_path / "fake"
    fake.mkdir()
    (fake / "brain.py").write_text(FAKE_BRAIN % "4.0")
    policy = fake / "recall_policy.json"
    policy.write_text(json.dumps({"schema_version": 1, "nodes": {NODE: {"intent_gate": {"any": 1}}}}))
    monkeypatch.setenv("BRAIN_RECALL_POLICY", str(policy))
    monkeypatch.setenv("CANONICAL_ROUTE_GATE_CACHE", str(tmp_path / "gate_cache.json"))
    monkeypatch.syspath_prepend(HERE)
    monkeypatch.syspath_prepend(str(fake))  # first, so the fake brain.py wins over the real one beside this file
    for name in ("brain", "canonical_route"):
        sys.modules.pop(name, None)
    cr = importlib.import_module("canonical_route")
    calls = fake / "calls.txt"

    def scored():
        return len(calls.read_text()) if calls.exists() else 0

    return cr, fake, policy, scored


def test_a_repeat_is_served_from_the_cache_with_the_same_answer(route):
    cr, _, _, scored = route
    assert cr.gate_strength("improve the brain", SESSION) == 4.0
    assert cr.gate_strength("improve the brain", SESSION) == 4.0
    assert cr.gate_strength("what time is it", SESSION) == 0.0
    assert cr.gate_strength("what time is it", SESSION) == 0.0
    assert scored() == 2


def test_a_changed_brain_py_or_policy_empties_the_cache(route):
    cr, fake, policy, scored = route
    assert cr.gate_strength("improve the brain", SESSION) == 4.0
    (fake / "brain.py").write_text(FAKE_BRAIN % "2.5")
    sys.modules.pop("brain", None)
    assert cr.gate_strength("improve the brain", SESSION) == 2.5
    policy.write_text(json.dumps({"schema_version": 1, "nodes": {}}))
    assert cr.gate_strength("improve the brain", SESSION) == 0.0
    assert scored() == 2


def test_only_a_real_session_writes_and_the_switch_turns_it_off(route, monkeypatch):
    cr, _, _, scored = route
    cr.gate_strength("improve the brain", "")
    cr.gate_strength("improve the brain", "")
    assert scored() == 2
    monkeypatch.setattr(cr, "GATE_CACHE", "0")
    cr.gate_strength("improve the brain", SESSION)
    cr.gate_strength("improve the brain", SESSION)
    assert scored() == 4
