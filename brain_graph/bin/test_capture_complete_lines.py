"""The capture reads a transcript only up to its last complete line (complete_end, 27 Sep): five controls."""

import sys
if __name__ == "__main__" and any(arg in ("-h", "--help") for arg in sys.argv[1:]):
    print(__doc__ or "No command-line options.")
    raise SystemExit(0)

import importlib.util, json, os, sys, tempfile
sp = importlib.util.spec_from_file_location("X", os.path.expanduser(r"~/.claude/brain_graph/bin/extract_touch_events.py"))
X = importlib.util.module_from_spec(sp); sys.path.insert(0, os.path.expanduser(r"~/.claude/brain_graph/bin")); sp.loader.exec_module(X)
d = tempfile.mkdtemp()
p = os.path.join(d, "t.jsonl")
line = lambda i: json.dumps({"type": "user", "sessionId": "abcdef12-0000", "timestamp": "2026-09-27T10:00:%02dZ" % i, "message": {"role": "user", "content": "hello %d" % i}}) + "\n"
open(p, "w", newline="").write(line(1) + line(2) + line(3)[:40])
e1 = X.complete_end(p, 0)
ok1 = e1 == len(line(1) + line(2))
open(p, "a", newline="").write(line(3)[40:])
e2 = X.complete_end(p, e1)
ok2 = e2 == len(line(1) + line(2) + line(3))
ok3 = X.complete_end(p, e2) == e2
big = "x" * (3 << 20)
open(p, "a", newline="").write('{"big": "' + big)
ok4 = X.complete_end(p, e2) == e2        # a 3 MB line still being written: no progress, nothing lost
ev = []
st = {"bad_json": 0}
import collections; st = collections.Counter()
X.scan_file(p, 0, lambda *a: ev.append(a), st, end=e2)
ok5 = st["bad_json"] == 0
print("partial last line left:", ok1, "| completed next time:", ok2, "| nothing new:", ok3, "| long partial line waits:", ok4, "| no bad json within end:", ok5)
print("PASS" if all((ok1, ok2, ok3, ok4, ok5)) else "FAIL")
