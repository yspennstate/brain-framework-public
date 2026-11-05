"""Controls for the served archive: an expired session shard is archived before it is removed.

 A1 an expired shard leaves the session folder and its record is one line of the archive
 A2 a fresh shard stays where it is and is not archived
 A3 when the archive cannot be written the expired shard is kept
 A4 a shard that is not valid JSON is archived raw, not lost

    python test_served_archive.py      (a few seconds; ALL PASS at the end)
"""

import sys
if __name__ == "__main__" and any(arg in ("-h", "--help") for arg in sys.argv[1:]):
    print(__doc__ or "No command-line options.")
    raise SystemExit(0)
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
results = []


def check(ok, what):
    results.append(bool(ok))
    print(("PASS  " if ok else "FAIL  ") + what)


PROBE = r"""
import os, sys, time
import math_sense as m
d, arc = sys.argv[1], sys.argv[2]
m.SESSION_DIR = d
m.SERVED_ARCHIVE = arc
m._prune_session_shards(time.time(), sample=1)
print(" ".join(sorted(os.listdir(d))) or "-")
"""


def run(d, arc):
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([HERE] + [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p])
    r = subprocess.run([sys.executable, "-c", PROBE, d, arc], cwd=HERE, env=env, capture_output=True, text=True,
                       timeout=120)
    return ((r.stdout + r.stderr).strip().splitlines() or [""])[-1]


def shard(d, name, body, age_h):
    p = os.path.join(d, name + ".json")
    with open(p, "w", encoding="utf-8") as f:
        f.write(body)
    t = os.path.getmtime(p) - age_h * 3600
    os.utime(p, (t, t))


def main():
    with tempfile.TemporaryDirectory() as tmp:
        d = os.path.join(tmp, "sessions")
        os.makedirs(d)
        arc = os.path.join(tmp, "live", "archive.jsonl")
        shard(d, "old1", json.dumps({"ts": 1, "ids": ["claim_a"]}), 72)
        shard(d, "new1", json.dumps({"ts": 2, "ids": ["claim_b"]}), 1)
        shard(d, "bad1", "{not json", 72)
        left = run(d, arc)
        rows = [json.loads(x) for x in open(arc, encoding="utf-8")] if os.path.exists(arc) else []
        by = {r["shard"]: r for r in rows}
        check("old1" not in left and by.get("old1", {}).get("rec", {}).get("ids") == ["claim_a"],
              "A1 expired shard archived and removed (left=%r rows=%d)" % (left, len(rows)))
        check("new1.json" in left and "new1" not in by, "A2 fresh shard kept, not archived (left=%r)" % left)
        check(by.get("bad1", {}).get("rec", {}).get("raw") == "{not json", "A4 invalid shard archived raw")
        # A3: the archive path is a directory, so the append fails
        shard(d, "old2", json.dumps({"ts": 3}), 72)
        blocked = os.path.join(tmp, "blocked")
        os.makedirs(blocked)
        left2 = run(d, blocked)
        check("old2.json" in left2, "A3 append failed, shard kept (left=%r)" % left2)
    print("ALL PASS" if all(results) else "SOME FAILED")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
