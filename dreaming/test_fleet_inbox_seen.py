"""Controls for fleet_inbox_hook.py marking: only the messages it showed are marked seen.

 S1 eight unseen messages: the first prompt shows five, the next shows the other three, the third shows none
 S2 a brand-new session (no seen file) shows the newest five of the last two days and marks the whole inbox seen,
    so neither the rest nor the older history follows it
 S3 a control: a message posted after the first prompt is shown on the next one

The hook runs as a child with HOME/USERPROFILE in a temp folder and a fresh inbox cache, so it never reaches the
network or the real state folder.

    python test_fleet_inbox_seen.py      (a few seconds; ALL PASS at the end)
"""
if __name__ == "__main__" and __import__("sys").argv[1:2] in (["-h"], ["--help"]):
    print((__doc__ or "").strip() or "run with no arguments")
    raise SystemExit(0)

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(HERE, "fleet_inbox_hook.py")
results = []
N_CHECKS = 6


def check(ok, what):
    results.append(bool(ok))
    print(("PASS  " if ok else "FAIL  ") + what)


def msg_id(t, k):
    return "FM-%s-test%02d" % (time.strftime("%Y%m%d-%H%M%S", time.localtime(t)), k)


def inbox(ids):
    return "# Fleet inbox\n\n" + "".join("## %s | when | someone\nmessage body %s\n\n" % (i, i) for i in ids)


def write_cache(home, ids):
    st = os.path.join(home, ".claude", "fleet_board")
    os.makedirs(st, exist_ok=True)
    with open(os.path.join(st, "inbox_cache.json"), "w", encoding="utf-8") as f:
        json.dump({"t": time.time(), "text": inbox(ids), "etag": None, "rest_off_until": 0}, f)
    return st


def run(home, sid):
    env = dict(os.environ, HOME=home, USERPROFILE=home)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    r = subprocess.run([sys.executable, "-s", HOOK], input=json.dumps({"session_id": sid}).encode(), env=env,
                       capture_output=True, timeout=60, creationflags=flags)
    return re.findall(r"(?m)^(FM-\S+)  ", r.stdout.decode("utf-8", "replace"))


tmp = tempfile.mkdtemp(prefix="fleet_seen_")
try:
    now = time.time()
    ids = [msg_id(now - 60 * k, k) for k in range(8)]          # newest first, all inside two days
    old = msg_id(now - 5 * 86400, 99)                           # older than the first window

    home = os.path.join(tmp, "a")
    st = write_cache(home, ids)
    with open(os.path.join(st, "seen_claude-sessaaaa.json"), "w", encoding="utf-8") as f:
        json.dump([], f)                                        # an existing session that has seen nothing
    a1, a2, a3 = run(home, "sessaaaa-1"), run(home, "sessaaaa-1"), run(home, "sessaaaa-1")
    check(a1 == ids[:5] and a2 == ids[5:] and a3 == [], "S1 five, then three, then none: %d %d %d" % (len(a1), len(a2), len(a3)))
    seen = json.load(open(os.path.join(st, "seen_claude-sessaaaa.json"), encoding="utf-8"))
    check(sorted(seen) == sorted(ids), "S1 every message ends up seen exactly once")

    home = os.path.join(tmp, "b")
    st = write_cache(home, ids + [old])
    b1, b2 = run(home, "sessbbbb-1"), run(home, "sessbbbb-1")
    check(b1 == ids[:5], "S2 a new session shows the newest five")
    check(b2 == [], "S2 nothing follows it on the next prompt (got %s)" % b2)
    seen = json.load(open(os.path.join(st, "seen_claude-sessbbbb.json"), encoding="utf-8"))
    check(sorted(seen) == sorted(ids + [old]), "S2 the whole inbox is marked, the old message too")

    newer = msg_id(now + 30, 50)
    write_cache(home, [newer] + ids + [old])
    check(run(home, "sessbbbb-1") == [newer], "S3 control: a message posted later is shown on the next prompt")
except Exception as e:
    check(False, "raised %s: %s" % (type(e).__name__, e))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

ok = all(results) and len(results) == N_CHECKS
print("ALL PASS" if ok else "FAILED %d of %d" % (N_CHECKS - results.count(True), N_CHECKS))
sys.exit(0 if ok else 1)
