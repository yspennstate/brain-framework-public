"""The recall server's call row carries the page faults taken during the call, and None where getrusage is missing."""
import builtins
import importlib.util
import os

HERE = os.path.dirname(os.path.abspath(__file__))


def _server():
    spec = importlib.util.spec_from_file_location("brs_pf_under_test", os.path.join(HERE, "brain_recall_server.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_faults_count_up():
    brs = _server()
    a = brs._faults()
    if a is None:   # Windows
        return
    junk = bytearray(8 << 20)
    for i in range(0, len(junk), 4096):
        junk[i] = 1
    b = brs._faults()
    assert b[0] >= a[0] and b[1] > a[1]


def test_no_resource_module_gives_none(monkeypatch):
    brs = _server()
    real = builtins.__import__

    def fake(name, *args, **kw):
        if name == "resource":
            raise ImportError("no resource here")
        return real(name, *args, **kw)

    monkeypatch.setattr(builtins, "__import__", fake)
    assert brs._faults() is None


def test_the_call_row_has_pf():
    src = open(os.path.join(HERE, "brain_recall_server.py"), encoding="utf-8").read()
    assert '"pf": [pf1[0] - pf0[0], pf1[1] - pf0[1]] if pf0 and pf1 else None' in src
    assert '"pf": reply.get("pf")' in src
