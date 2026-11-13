"""Controls for the spread server's held memory-folder name list (DirWatch + Warm.held_names).

 H1 the first call lists the folder: the regular .md names, in folder order
 H2 a second call with no change serves the held list without listing again
 H3 a new file is patched in from the watcher's events, at its place, without a re-list
 H4 a removed file is patched out the same way
 H5 a hold older than STATE_TTL is listed again
 H6 the held stats are names only: .get gives None, so per-file stats are always read live
 H7 DirWatch.since gives None when an event it needs was evicted or the buffer overflowed

Windows only (ReadDirectoryChangesW); elsewhere the server lists every call and H1-H6 are skipped.
    python test_spread_held_names.py      (a few seconds; ALL PASS at the end)
"""
if "-h" in __import__("sys").argv[1:] or "--help" in __import__("sys").argv[1:]:
    print((__doc__ or "").strip() or "usage: python %s" % __import__("os").path.basename(__file__))
    raise SystemExit(0)
import os
import sys
import tempfile
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import embedding_artifact as ea
import spread_server as ss

results = []


def check(ok, what):
    results.append(ok)
    print(("PASS  " if ok else "FAIL  ") + what)


class Stub:
    _NamesOnly = ss.Warm._NamesOnly
    _names_bg_start = ss.Warm._names_bg_start
    held_names = ss.Warm.held_names


def touch(d, name):
    with open(os.path.join(d, name), "w", encoding="utf-8") as fh:
        fh.write(name)


def settle(w, g0, timeout=3.0):
    end = time.time() + timeout
    while w.gen == g0 and time.time() < end:
        time.sleep(0.02)


if os.name == "nt":
    mem = tempfile.mkdtemp(prefix="held_names_")
    for n in ["b_note.md", "a_note.md", "MEMORY.md", "other.txt"]:
        touch(mem, n)
    w0 = Stub()
    w0.sr = types.SimpleNamespace(MEM=mem, embedding_artifact=ea)
    w0.misses = []
    time.sleep(0.6)
    names = w0.held_names()
    want = sorted((n for n in os.listdir(mem) if n.endswith(".md")), key=str.upper)
    check(names == want and any(m.startswith("held_names:list") for m in w0.misses), "H1 the first call lists the folder")

    time.sleep(0.6)   # past the arm margin, so the next hold is trusted
    w0.misses = []
    w0.held_names()
    w0.misses = []
    again = w0.held_names()
    check(again == want and not any("list" in m for m in w0.misses), "H2 no change: the held list is served")

    g0 = w0.names_watch.gen
    touch(mem, "c_note.md")
    settle(w0.names_watch, g0)
    w0.misses = []
    names = w0.held_names()
    check(names == sorted(want + ["c_note.md"], key=str.upper) and "held_names:patch:1" in w0.misses,
          "H3 a new file is patched in at its place")

    g0 = w0.names_watch.gen
    os.remove(os.path.join(mem, "a_note.md"))
    settle(w0.names_watch, g0)
    w0.misses = []
    names = w0.held_names()
    check("a_note.md" not in names and "held_names:patch:1" in w0.misses, "H4 a removed file is patched out")

    h = w0.names_hold
    w0.names_hold = (h[0], h[1] - ss.STATE_TTL - 1, h[2], h[3])
    w0.misses = []
    w0.held_names()
    check(any(m == "held_names:list:ttl" for m in w0.misses), "H5 a hold past STATE_TTL is listed again")

    check(w0.sr._MEM_STATS.get("b_note.md") is None and "b_note.md" in w0.sr._MEM_STATS, "H6 names only, no stats")
else:
    print("SKIP  H1-H6: no directory watcher on this system")

dw = ss.DirWatch(".")
dw.log.extend([(5, "add", "x.md"), (6, "del", "y.md")])
dw.gen = 6
ok_tail = dw.since(4, 6) == [(5, "add", "x.md"), (6, "del", "y.md")]
evicted = dw.since(2, 6) is None
dw.log.append((7, "ovf", None))
dw.gen = 7
check(ok_tail and evicted and dw.since(6, 7) is None, "H7 an evicted event or an overflow gives None")

print("ALL PASS" if all(results) else "FAILED %d of %d" % (results.count(False), len(results)))
sys.exit(0 if all(results) else 1)
