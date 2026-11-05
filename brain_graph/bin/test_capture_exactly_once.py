"""test_capture_exactly_once.py - an interrupted capture adds no event twice (28 Sep, claude-825b1305; account03/B12
R12, B12-R12-CAPTURE-STATE-APPEND-NONATOMIC-DUPLICATION).

    python test_capture_exactly_once.py [DIR_WITH_THE_CAPTURE] [OLD_CAPTURE_DIR]

Four real transcripts are copied into a temp folder (read only; the live log, state and transcripts are never written).
The reference is one uninterrupted capture. Each arm kills the capture at one point (os._exit, so nothing is flushed or
saved after it), runs it again, and compares with the reference: the multiset of events, a graph_store build (edges,
uses, names) and the fast layer's pairs.
 P1 killed after the first transcript's events were written, before any state
 P2 killed in the middle of writing the second transcript's events (a torn line)
 P3 killed after every transcript was written, before the state was replaced
 P4 steady state: a committed capture of two transcripts, two more arrive, the run that reads them loses its state
    replace (the state is put back as it was), the next run reads them again
 N1 negative control, the capture before this change (OLD_CAPTURE_DIR) in P4's steps: its events are NOT the reference
    (the check can fail)
"""
import collections
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile

H = os.path.expanduser("~")
BIN = os.path.join(H, ".claude", "brain_graph", "bin")
NEW = sys.argv[1] if len(sys.argv) > 1 else BIN
OLD = sys.argv[2] if len(sys.argv) > 2 else None
PROJ_LIVE = os.path.join(H, ".claude", "projects", (os.environ.get("BRAIN_SLUG") or os.path.expanduser("~").replace(":", "-").replace(chr(92), "-").replace("/", "-")))
NO_WINDOW = 0x08000000 if os.name == "nt" else 0
fails, ran = [], []


def check(ok, what):
    print(("PASS  " if ok else "FAIL  ") + what)
    ran.append(what)
    if not ok:
        fails.append(what)


def pick_transcripts(n=4):
    """Four finished transcripts of 0.3-0.9 MB with tool use, oldest first (a live one could grow during the test)."""
    out = []
    for f in sorted(glob.glob(os.path.join(PROJ_LIVE, "*.jsonl")), key=os.path.getmtime):
        s = os.path.getsize(f)
        if 300000 < s < 900000 and os.path.getmtime(f) < __import__("time").time() - 86400:
            with open(f, "rb") as fh:
                if fh.read().count(b'"tool_use"') > 30:
                    out.append(f)
        if len(out) == n:
            break
    return out


CODE = r"""
import sys
sys.path.insert(0, sys.argv[1])
import extract_touch_events as e
e.PROJ = sys.argv[2]
sys.argv = ["extract_touch_events.py", "--days", "4000", "--out", sys.argv[3], "--state", sys.argv[4],
            "--verdicts-out", sys.argv[5]] + sys.argv[6:]
e.main()
"""


def run(impl, proj, d, *extra):
    os.makedirs(d, exist_ok=True)
    r = subprocess.run([sys.executable, "-c", CODE, impl, proj, os.path.join(d, "events.jsonl"),
                        os.path.join(d, "state.json"), os.path.join(d, "verdicts.jsonl")] + list(extra),
                       capture_output=True, text=True, creationflags=NO_WINDOW)
    return r.returncode, (r.stdout + r.stderr)[-600:]


def lines(path):
    good, bad = collections.Counter(), 0
    if not os.path.exists(path):
        return good, bad
    with open(path, "rb") as fh:
        for ln in fh:
            ln = ln.rstrip(b"\r\n")
            if not ln:
                continue
            try:
                json.loads(ln)
                good[ln] += 1
            except Exception:
                bad += 1
    return good, bad


def build(ev, out):
    sys.path.insert(0, NEW)
    import graph_store
    meta = graph_store.build(ev, out, window=600, k=40, hub=0.9, tool_rate=1.0)
    import numpy as np
    z = np.load(os.path.join(out, "edges.npz"))
    edges = {k: z[k].tolist() for k in sorted(z.files)}
    uses = json.load(open(os.path.join(out, "uses.json"), encoding="utf-8"))
    names = json.load(open(os.path.join(out, "nodes.json"), encoding="utf-8"))["names"]
    return meta, edges, uses, names


def delta_pairs(ev):
    sys.path.insert(0, NEW)
    import importlib
    LD = importlib.import_module("live_delta")

    def kinds(_cache):                               # every session counts; the live kinds cache is never written
        f = lambda s: "his"
        f.save = lambda: None
        return f
    LD.kind_resolver = kinds
    evs, info = LD.read_events({"until": 0.0, "store_built": 0.0}, path=ev, start=0, mode="bytes")
    got = LD.pairs_of(evs, set(), 1.0e10, 0.0)
    return got[0], info


def same_as_ref(tag, d, ref):
    good, bad = lines(os.path.join(d, "events.jsonl"))
    vgood, _ = lines(os.path.join(d, "verdicts.jsonl"))
    check(good == ref["events"], "%s events: %d lines, reference %d; extra %d, missing %d" % (
        tag, sum(good.values()), sum(ref["events"].values()), sum((good - ref["events"]).values()),
        sum((ref["events"] - good).values())))
    check(vgood == ref["verdicts"], "%s verdicts: %d, reference %d" % (tag, sum(vgood.values()), sum(ref["verdicts"].values())))
    meta, edges, uses, names = build(os.path.join(d, "events.jsonl"), os.path.join(d, "store"))
    check(edges == ref["edges"] and uses == ref["uses"] and names == ref["names"],
          "%s store: edges, uses and names %s the reference's (%d events, %d pairs)" % (
              tag, "equal" if (edges == ref["edges"] and uses == ref["uses"] and names == ref["names"]) else "DIFFER from",
              meta.get("events", 0), meta.get("pairs", 0)))
    pairs, info = delta_pairs(os.path.join(d, "events.jsonl"))
    check(pairs == ref["pairs"], "%s fast layer: %d pairs, reference %d (bad lines skipped %d)" % (
        tag, len(pairs), len(ref["pairs"]), info.get("bad_json", 0)))
    return bad


def main():
    src = pick_transcripts()
    if len(src) < 4:
        print("FAIL - fewer than four suitable transcripts")
        sys.exit(1)
    T = tempfile.mkdtemp(prefix="capture_once_")
    proj = os.path.join(T, "proj")
    os.makedirs(proj)
    for i, f in enumerate(src):
        shutil.copy2(f, os.path.join(proj, os.path.basename(f)))
    print("transcripts: %s" % ", ".join("%s (%d KB)" % (os.path.basename(f)[:8], os.path.getsize(f) // 1024) for f in src))

    rc, out = run(NEW, proj, os.path.join(T, "ref"))
    good, bad = lines(os.path.join(T, "ref", "events.jsonl"))
    vgood, _ = lines(os.path.join(T, "ref", "verdicts.jsonl"))
    if rc != 0 or not good:
        print("FAIL - reference capture rc=%s: %s" % (rc, out))
        sys.exit(1)
    meta, edges, uses, names = build(os.path.join(T, "ref", "events.jsonl"), os.path.join(T, "ref", "store"))
    pairs, _ = delta_pairs(os.path.join(T, "ref", "events.jsonl"))
    ref = {"events": good, "verdicts": vgood, "edges": edges, "uses": uses, "names": names, "pairs": pairs}
    print("reference: %d events (%d distinct), %d verdicts, %d store pairs, %d fast-layer pairs" % (
        sum(good.values()), len(good), sum(vgood.values()), meta.get("pairs", 0), len(pairs)))

    d = os.path.join(T, "p1")
    rc, out = run(NEW, proj, d, "--fail-after", "1")
    first = sum(lines(os.path.join(d, "events.jsonl"))[0].values())
    check(rc == 9 and first > 0, "P1 killed after the first transcript (rc %s, %d events on disk)" % (rc, first))
    rc, out = run(NEW, proj, d)
    check(rc == 0 and "not written again" in out, "P1 rerun rc %s: %s" % (rc, out.strip().splitlines()[-1][-160:]))
    bad = same_as_ref("P1", d, ref)
    check(bad == 0, "P1 no torn line (%d)" % bad)

    d = os.path.join(T, "p2")
    rc, out = run(NEW, proj, d, "--fail-after", "2", "--fail-torn")
    check(rc == 9, "P2 killed mid-write of the second transcript (rc %s)" % rc)
    rc, out = run(NEW, proj, d)
    check(rc == 0, "P2 rerun rc %s: %s" % (rc, out.strip().splitlines()[-1][-160:]))
    bad = same_as_ref("P2", d, ref)
    check(bad == 1, "P2 exactly one torn line left in place, unparsed (%d)" % bad)

    d = os.path.join(T, "p3")
    rc, out = run(NEW, proj, d, "--fail-after", "4")
    check(rc == 9, "P3 killed after every transcript, before the state (rc %s)" % rc)
    rc, out = run(NEW, proj, d)
    check(rc == 0, "P3 rerun rc %s" % rc)
    same_as_ref("P3", d, ref)

    def steady(impl, d):
        half = os.path.join(d, "proj")
        os.makedirs(half)
        for f in sorted(glob.glob(os.path.join(proj, "*.jsonl")), key=os.path.getmtime)[:2]:
            shutil.copy2(f, half)
        rc1, _ = run(impl, half, d)                               # a committed capture of two transcripts
        saved = open(os.path.join(d, "state.json"), "rb").read()
        for f in sorted(glob.glob(os.path.join(proj, "*.jsonl")), key=os.path.getmtime)[2:]:
            shutil.copy2(f, half)                                  # two more arrive
        rc2, _ = run(impl, half, d)                                # read them ...
        open(os.path.join(d, "state.json"), "wb").write(saved)     # ... and lose the state replace
        rc3, out = run(impl, half, d)                              # the next run reads them again
        return rc1, rc2, rc3, out

    d = os.path.join(T, "p4")
    rc1, rc2, rc3, out = steady(NEW, d)
    check((rc1, rc2, rc3) == (0, 0, 0), "P4 three runs rc %s" % [rc1, rc2, rc3])
    same_as_ref("P4", d, ref)

    if OLD:
        d = os.path.join(T, "n1")
        rc1, rc2, rc3, out = steady(OLD, d)
        good, _ = lines(os.path.join(d, "events.jsonl"))
        extra = sum((good - ref["events"]).values())
        check(extra > 0, "N1 the capture before this change, same steps: %d extra events (the check can fail)" % extra)

    shutil.rmtree(T, ignore_errors=True)
    print("FAIL" if fails else "PASS", "- %d controls, %d failed" % (len(ran), len(fails)))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
