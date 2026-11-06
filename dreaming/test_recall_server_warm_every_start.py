"""Every fresh recall server warms itself, not only a successor; a TTL-only drop is tagged in the call row."""
import importlib.util
import os

HERE = os.path.dirname(os.path.abspath(__file__))


def _server():
    spec = importlib.util.spec_from_file_location("brs_under_test", os.path.join(HERE, "brain_recall_server.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_a_client_started_server_warms():
    brs = _server()
    assert brs._should_warm({})
    assert brs._should_warm({"BRS_SUCCESSOR": "1"})


def test_the_off_switch_holds_for_every_server():
    brs = _server()
    assert not brs._should_warm({"BRS_WARMUP": "0"})
    assert not brs._should_warm({"BRS_WARMUP": "0", "BRS_SUCCESSOR": "1"})


class _Store(dict):
    pass


def test_a_ttl_only_drop_is_tagged(monkeypatch):
    brs = _server()
    b = brs.Brain.__new__(brs.Brain)
    st_a, st_b = _Store(x=1), _Store(y=1)
    b.group_paths = {"g_ttl": ["p1"], "g_sig": ["p2"]}
    b.stores = [("g_ttl", st_a), ("g_sig", st_b)]
    b.sigs = {"g_ttl": "same", "g_sig": "old"}
    b.filled = {"g_ttl": 0.0, "g_sig": 0.0}
    monkeypatch.setattr(brs, "EXACT_GROUPS", set(), raising=False)
    b._state_sig = lambda paths: "same" if paths == ["p1"] else "new"
    dropped = b.refresh_state()
    assert sorted(dropped) == ["g_sig", "g_ttl~ttl"]
    assert not st_a and not st_b
