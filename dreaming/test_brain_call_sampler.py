"""The slow-call sampler: off switch, rows only for slow calls, the busy function named, and the server's hook."""
import importlib.util
import json
import os
import time

HERE = os.path.dirname(os.path.abspath(__file__))


def _sampler(monkeypatch, **env):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    spec = importlib.util.spec_from_file_location("bcs_under_test", os.path.join(HERE, "brain_call_sampler.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _spin_here(sec):
    end = time.time() + sec
    x = 0
    while time.time() < end:
        x += 1
    return x


def test_off_switch(monkeypatch):
    assert _sampler(monkeypatch, BRS_SAMPLE="0").begin() is None


def test_a_slow_call_names_its_hot_function(monkeypatch, tmp_path):
    bcs = _sampler(monkeypatch, BRS_SAMPLE="1", BRS_SAMPLE_MS="2", BRS_SAMPLE_SLOW_MS="100")
    smp = bcs.begin()
    _spin_here(0.3)
    smp.stop()
    out = tmp_path / "slow.jsonl"
    smp.write(str(out), 300, {"op": "recall"})
    row = json.loads(out.read_text().splitlines()[0])
    assert row["op"] == "recall" and row["samples"] > 10
    assert row["self"][0][0].endswith(":_spin_here")


def test_a_fast_call_writes_nothing(monkeypatch, tmp_path):
    bcs = _sampler(monkeypatch, BRS_SAMPLE="1", BRS_SAMPLE_SLOW_MS="500")
    smp = bcs.begin()
    _spin_here(0.05)
    smp.stop()
    out = tmp_path / "slow.jsonl"
    smp.write(str(out), 50, {"op": "recall"})
    assert not out.exists()


def test_the_server_hooks_it_in():
    src = open(os.path.join(HERE, "brain_recall_server.py"), encoding="utf-8").read()
    assert "smp = brain_call_sampler.begin()" in src
    assert 'smp.write(os.path.join(STATE_DIR, "slow_samples.jsonl")' in src
