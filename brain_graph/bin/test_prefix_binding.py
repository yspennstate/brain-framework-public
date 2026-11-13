"""test_prefix_binding.py - the fast layer uses the store's byte count only while the log's prefix is the one the store
consumed.

    python test_prefix_binding.py [PATH_TO_live_delta.py]

Controls (temp log and temp STATUS; the live ones are never touched):
 P1 the recorded count with matching marks: bytes mode, at the count
 P2 events appended after the build: still bytes mode (the prefix is unchanged)
 P3 the first line rewritten in place, same length: the marks differ, so the start is found by time and a line is logged
 P4 a STATUS written before the marks existed: bytes mode, as before (nothing to check against)
"""
if "-h" in __import__("sys").argv[1:] or "--help" in __import__("sys").argv[1:]:
    print((__doc__ or "").strip() or "usage: python %s" % __import__("os").path.basename(__file__))
    raise SystemExit(0)
import importlib.util
import json
import os
import sys
import tempfile

H = os.path.expanduser("~")
LDP = sys.argv[1] if len(sys.argv) > 1 else os.path.join(H, ".claude", "brain_graph", "bin", "live_delta.py")
sys.path.insert(0, os.path.join(H, ".claude", "brain_graph", "bin"))
spec = importlib.util.spec_from_file_location("ld_prefix", LDP)
LD = importlib.util.module_from_spec(spec)
spec.loader.exec_module(LD)

tmp = tempfile.mkdtemp(prefix="prefix_binding_")
LD.STATUS = os.path.join(tmp, "STATUS.json")
LD.HOME = tmp
os.makedirs(os.path.join(tmp, "logs"), exist_ok=True)
ev = os.path.join(tmp, "events.jsonl")
T = 1790000000.0
with open(ev, "w", encoding="utf-8") as fh:
    for i in range(30000):                       # about 2.5 MB, so the head and tail megabytes differ
        fh.write(json.dumps({"ts": T + i, "session": "s%d" % (i % 7), "kind": "file", "id": "/f/%06d" % i}) + "\n")
nb = os.path.getsize(ev)
built = T + 40000.0
b = {"store_built": built, "until": T + 29999}
fails = []


def check(ok, what):
    print(("PASS  " if ok else "FAIL  ") + what)
    if not ok:
        fails.append(what)


marks = LD.prefix_marks(ev, nb) if hasattr(LD, "prefix_marks") else (None, None)
json.dump({"build_events_bytes": nb, "build_store_built": built, "build_events_head_sha": marks[0] if marks else None,
           "build_events_tail_sha": marks[1] if marks else None}, open(LD.STATUS, "w"))
got = LD.start_offset(b, path=ev)
check(got == (nb, "bytes"), "P1 matching marks: %s" % (got,))

with open(ev, "a", encoding="utf-8") as fh:
    for i in range(30000, 30500):
        fh.write(json.dumps({"ts": T + i, "session": "s1", "kind": "file", "id": "/g/%06d" % i}) + "\n")
got = LD.start_offset(b, path=ev)
check(got == (nb, "bytes"), "P2 events appended after the build: %s" % (got,))

with open(ev, "r+b") as fh:
    first = fh.readline()
    fh.seek(0)
    fh.write(first.replace(b"/f/000000", b"/x/000000"))
log = os.path.join(tmp, "logs", "live.log")
before = open(log, encoding="utf-8").read().count("no longer match") if os.path.exists(log) else 0
got = LD.start_offset(b, path=ev)
after = open(log, encoding="utf-8").read().count("no longer match") if os.path.exists(log) else 0
check(got[1] == "ts" and after == before + 1, "P3 the first line rewritten in place: %s, logged %d" % (got, after - before))

json.dump({"build_events_bytes": nb, "build_store_built": built}, open(LD.STATUS, "w"))
got = LD.start_offset(b, path=ev)
check(got == (nb, "bytes"), "P4 a STATUS without marks: %s" % (got,))

# 28 Sep: the two end marks leave the middle of the prefix unchecked; the build now records a
# sha256 of the whole prefix. One byte changed at the head, the middle and the tail, each at the same length.
n_ctrl = 4
if hasattr(LD, "prefix_sha"):
    with open(ev, "r+b") as fh:                  # undo P3's edit, so the log is the one the store consumed
        fh.seek(0)
        fh.write(first)
    whole = LD.prefix_sha(ev, nb)
    marks = LD.prefix_marks(ev, nb)
    st = {"build_events_bytes": nb, "build_store_built": built, "build_events_head_sha": marks[0],
          "build_events_tail_sha": marks[1], "build_events_sha": whole}
    json.dump(st, open(LD.STATUS, "w"))
    got = LD.start_offset(b, path=ev)
    check(got == (nb, "bytes"), "P5 the whole prefix matching: %s" % (got,))
    n_ctrl += 1

    def flip(at):
        with open(ev, "r+b") as fh:
            fh.seek(at)
            c = fh.read(1)
            fh.seek(at)
            fh.write(b"7" if c != b"7" else b"8")
            return c

    def put_back(at, c):
        with open(ev, "r+b") as fh:
            fh.seek(at)
            fh.write(c)

    for name, at in (("head", 12), ("middle", nb // 2), ("tail", nb - 30)):
        c = flip(at)
        got = LD.start_offset(b, path=ev)
        check(got[1] == "ts", "P6 one byte changed at the %s (offset %d): %s" % (name, at, got))
        n_ctrl += 1
        put_back(at, c)
    json.dump({k: v for k, v in st.items() if k != "build_events_sha"}, open(LD.STATUS, "w"))
    c = flip(nb // 2)
    got = LD.start_offset(b, path=ev)
    check(got == (nb, "bytes"), "N1 the end marks alone miss the middle byte (the check can fail): %s" % (got,))
    n_ctrl += 1
    put_back(nb // 2, c)
    t0 = __import__("time").time()
    LD.prefix_sha(ev, nb)
    print("      whole-prefix hash of %.1f MB in %.3f s" % (nb / 2**20, __import__("time").time() - t0))

print("FAIL" if fails else "PASS", "- %d of %d controls" % (n_ctrl - len(fails), n_ctrl))
sys.exit(1 if fails else 0)
