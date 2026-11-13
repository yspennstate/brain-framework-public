"""test_codex_capture_exactly_once.py - the Codex capture adds no event twice when a run loses its state replace.

    python test_codex_capture_exactly_once.py [DIR_WITH_THE_CAPTURE] [OLD_CAPTURE_DIR]

Three real Codex rollouts are copied into a temp ROOT/yyyy/mm/dd (read only; the live logs and state are not written).
Finished rollouts compressed to .jsonl.zst are used when too few plain ones exist; they are decompressed into the temp
folder (needs the zstandard module).
 R  one uninterrupted capture of all three is the reference
 P1 a committed capture of two, the third arrives, the run that reads it loses its state replace, the next run reads it
    again: the events equal the reference
 P2 the same, and the FIRST run's final state replace lost too (left with the state it saved before scanning):
    equal again
 N1 the capture before this change (OLD_CAPTURE_DIR, which must also hold its extract_touch_events.py) in P1's steps:
    extra events (the check can fail)
"""
if "-h" in __import__("sys").argv[1:] or "--help" in __import__("sys").argv[1:]:
    print((__doc__ or "").strip() or "usage: python %s" % __import__("os").path.basename(__file__))
    raise SystemExit(0)
import collections
import glob
import os
import shutil
import subprocess
import sys
import tempfile
import time

H = os.path.expanduser("~")
BIN = os.path.join(H, ".claude", "brain_graph", "bin")
NEW = sys.argv[1] if len(sys.argv) > 1 else BIN
OLD = sys.argv[2] if len(sys.argv) > 2 else None
NO_WINDOW = 0x08000000 if os.name == "nt" else 0
fails, ran = [], []

CODE = r"""
import sys
sys.path.insert(0, sys.argv[1])
import extract_codex_events as cx
cx.ROOT = sys.argv[2]
sys.argv = ["extract_codex_events.py", "--days", "4000", "--out", sys.argv[3], "--state", sys.argv[4]]
cx.main()
"""


def check(ok, what):
    print(("PASS  " if ok else "FAIL  ") + what)
    ran.append(what)
    if not ok:
        fails.append(what)


def run(impl, root, d):
    os.makedirs(d, exist_ok=True)
    r = subprocess.run([sys.executable, "-c", CODE, impl, root, os.path.join(d, "events.jsonl"),
                        os.path.join(d, "state.json")], capture_output=True, text=True, creationflags=NO_WINDOW)
    return r.returncode, (r.stdout + r.stderr)[-400:]


def lines(p):
    c = collections.Counter()
    if os.path.exists(p):
        with open(p, "rb") as fh:
            for ln in fh:
                ln = ln.rstrip(b"\r\n")
                if ln:
                    c[ln] += 1
    return c


def pick(n=3):
    out = []
    for f in sorted(glob.glob(os.path.join(H, ".codex", "sessions", "*", "*", "*", "*.jsonl")), key=os.path.getmtime):
        if 200000 < os.path.getsize(f) < 3000000 and os.path.getmtime(f) < time.time() - 86400:
            out.append(f)
        if len(out) == n:
            return out
    try:
        import zstandard  # noqa: F401
    except ImportError:
        return out
    for f in sorted(glob.glob(os.path.join(H, ".codex", "sessions", "*", "*", "*", "*.jsonl.zst")), key=os.path.getmtime):
        if 30000 < os.path.getsize(f) < 600000 and os.path.getmtime(f) < time.time() - 86400:
            out.append(f)
        if len(out) == n:
            break
    return out


def place(f, d):
    """copy a rollout into d as plain .jsonl (a .zst is decompressed)"""
    if not f.endswith(".zst"):
        shutil.copy2(f, d)
        return
    import zstandard
    dst = os.path.join(d, os.path.basename(f)[:-4])
    with open(f, "rb") as a, open(dst, "wb") as b:
        zstandard.ZstdDecompressor().copy_stream(a, b)
    st = os.stat(f)
    os.utime(dst, (st.st_atime, st.st_mtime))


def main():
    src = pick()
    if len(src) < 3:
        print("SKIP - fewer than three suitable rollouts (a fresh install has none yet)")
        sys.exit(0)
    T = tempfile.mkdtemp(prefix="codex_once_")
    full = os.path.join(T, "full", "2026", "09", "01")
    os.makedirs(full)
    for f in src:
        place(f, full)
    rc, out = run(NEW, os.path.join(T, "full"), os.path.join(T, "ref"))
    ref = lines(os.path.join(T, "ref", "events.jsonl"))
    check(rc == 0 and sum(ref.values()) > 0, "R reference: %d events from 3 rollouts (rc %s)" % (sum(ref.values()), rc))

    def steady(impl, tag, lose_first=False):
        d = os.path.join(T, tag)
        root = os.path.join(d, "root")
        day = os.path.join(root, "2026", "09", "01")
        os.makedirs(day)
        fs = sorted(glob.glob(os.path.join(full, "*.jsonl")), key=os.path.getmtime)
        for f in fs[:2]:
            shutil.copy2(f, day)
        rcs = [run(impl, root, d)[0]]
        if lose_first:
            # the first run's final state replace never landed: what is left is the state it saved before scanning,
            # the committed length of an empty log and no rollout offsets
            import json
            key = os.path.normcase(os.path.abspath(os.path.join(d, "events.jsonl")))
            json.dump({"_committed_bytes": {key: 0}}, open(os.path.join(d, "state.json"), "w", encoding="utf-8"))
            rcs.append(run(impl, root, d)[0])
        saved = open(os.path.join(d, "state.json"), "rb").read()
        shutil.copy2(fs[2], day)
        rcs.append(run(impl, root, d)[0])
        open(os.path.join(d, "state.json"), "wb").write(saved)
        rc, out = run(impl, root, d)
        rcs.append(rc)
        return rcs, lines(os.path.join(d, "events.jsonl")), out

    rcs, got, out = steady(NEW, "p1")
    check(all(r == 0 for r in rcs), "P1 runs rc %s" % rcs)
    check(got == ref, "P1 events %d, reference %d; extra %d, missing %d" % (
        sum(got.values()), sum(ref.values()), sum((got - ref).values()), sum((ref - got).values())))
    rcs, got, out = steady(NEW, "p2", lose_first=True)
    check(got == ref, "P2 (the first run's state lost too) events %d, reference %d; extra %d" % (
        sum(got.values()), sum(ref.values()), sum((got - ref).values())))
    if OLD:
        rcs, got, out = steady(OLD, "n1")
        extra = sum((got - ref).values())
        check(extra > 0, "N1 the capture before this change, same steps: %d extra events (the check can fail)" % extra)
    shutil.rmtree(T, ignore_errors=True)
    print("FAIL" if fails else "PASS", "- %d controls, %d failed" % (len(ran), len(fails)))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
