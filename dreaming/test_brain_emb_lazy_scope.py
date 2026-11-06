"""The CLI recall loads the vector sidecar lazily; the recall server, which keeps it resident, does not."""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CHILD = r'''
import json, os, runpy, sys
g = runpy.run_path(os.path.join(sys.argv[1], "brain.py"), run_name=sys.argv[2])["main"].__globals__
seen = []
g["recall"] = lambda q, n=8, *a, **k: seen.append(os.environ.get("BRAIN_EMB_LAZY")) or 0
g["recall_or"] = lambda qs, n=8, *a, **k: seen.append(os.environ.get("BRAIN_EMB_LAZY")) or 0
sys.argv = ["brain.py", "recall", "a question"]
try:
    g["main"]()
except SystemExit:
    pass
print("\nSEEN " + json.dumps(seen))
'''


def _lazy_under(run_name, extra_env=None):
    env = {k: v for k, v in os.environ.items() if k != "BRAIN_EMB_LAZY"}
    env.update(extra_env or {})
    r = subprocess.run([sys.executable, "-B", "-c", CHILD, HERE, run_name], capture_output=True, text=True,
                       env=env, timeout=120)
    line = [ln for ln in r.stdout.splitlines() if ln.startswith("SEEN ")]
    assert line, r.stderr[-800:]
    return json.loads(line[-1][5:])


def test_the_cli_recall_is_lazy():
    assert _lazy_under("brain_cli_under_test") == ["1"]


def test_the_recall_server_keeps_the_eager_load():
    assert _lazy_under("brain_recall_server_module") == [None]


def test_an_explicit_setting_wins_in_both():
    assert _lazy_under("brain_cli_under_test", {"BRAIN_EMB_LAZY": "0"}) == ["0"]
    assert _lazy_under("brain_recall_server_module", {"BRAIN_EMB_LAZY": "1"}) == ["1"]
