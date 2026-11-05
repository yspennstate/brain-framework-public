#!/usr/bin/env python3
"""bm25_lane_selftest.py - regression controls for the write-time bm25 lane (bm25.py refresh / freeze).

WHY THIS FILE EXISTS. On 2026-09-02 the incremental refresh replaced "a new memory is dark to recall until the
nightly rebuild" with "recallable within seconds". A lane that maintains an index incrementally can drift from
the exact build in ways no single query shows (a df off by one, an avgdl that no longer matches, a race that
overwrites a newer build). These controls are the proof, and they are the ones that caught my own wrong
reading of a race simulation the night the lane shipped (memory
brain_write_time_recall_lane_bm25_refresh_2026_09_02). Run them after ANY edit to bm25.py.

    A  EQUIVALENCE  refreshed live index == clean build() over the same corpus (document set, per-doc tf and dl,
                    df over every term, n, avgdl, top-10 scores on a query battery). ~20 s: a full build to a
                    scratch path. Skipped with --quick.
    B  RACE GUARD   a build() landing inside refresh()'s guarded window must win: refresh writes nothing.
    C  DELETED      a file deleted between scan and read: no exception, doc dropped.
    D  FREEZE       refresh skips while a freeze stands; a freeze past its deadline is ignored and removed.

WRITES: every section calls refresh() on the LIVE index (that is what is under test), and B/C/D create and
remove one control memory file (zz_..._delete_me). Hold the mesh claim memory:brain/bm25_index while running.
Aborts (exit 2) if the generation is frozen by someone else. Exit 0 = all pass, 1 = a control failed.

Usage:  python bm25_lane_selftest.py [--quick]      (--help prints this and exits before any work)
"""
import gzip, io, json, os, sys, tempfile, time

if any(a in ("-h", "--help") for a in sys.argv[1:]):
    print(__doc__)
    sys.exit(0)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bm25  # noqa: E402

MEM = bm25.MEM
QUICK = "--quick" in sys.argv
SLUG = "zz_bm25_lane_selftest_control_delete_me"
CONTROL = os.path.join(MEM, SLUG + ".md")
QUERIES = ["restart box 3", "index level offset two above", "entry cost at most 1x the budget",
           "flatten the grid at the close", "google upload is not free per object",
           "feeder reads the old queue after sed -i"]
fails = 0


def report(name, ok, detail=""):
    global fails
    fails += (not ok)
    print("%-48s %s  %s" % (name, "PASS" if ok else "FAIL", detail))


def write_control(token):
    io.open(CONTROL, "w", encoding="utf-8").write(
        '---\nname: %s\ndescription: "[CONTROL, delete me] token %s"\nmetadata:\n  type: reference\n---\n\n%s\n'
        % (SLUG, token, token))


def in_index(token):
    return any(s == SLUG for s, _ in bm25.BM25(bm25.INDEX).score(token, 3))


def remove_control():
    try:
        os.remove(CONTROL)
    except OSError:
        pass


frozen, why = bm25.freeze_state()
if frozen:
    print("index generation is FROZEN (%s) - not testing against a held generation" % why)
    sys.exit(2)
remove_control()
added, gone = bm25.refresh(quiet=True)
print("pre-test refresh: +%d -%d" % (added, gone))

# ---- A. equivalence ---------------------------------------------------------------------------------------
if not QUICK:
    marker = json.load(io.open(bm25.INDEX + ".scan.json", encoding="utf-8"))
    scan_t0 = float(marker["scanned_at"])
    live = bm25._load_payload(bm25.INDEX)
    tmpdir = tempfile.mkdtemp(prefix="bm25eq_")
    tmp_index = os.path.join(tmpdir, "bm25_index.json.gz")
    t = time.time()
    n_built, v_built = bm25.build(MEM, tmp_index)
    print("clean build to scratch: %d docs, %d terms, %.1f s" % (n_built, v_built, time.time() - t))
    with gzip.open(tmp_index, "rt", encoding="utf-8") as f:
        clean = json.load(f)
    late = set()   # files changed after the refresh's scan started may differ legitimately
    with os.scandir(MEM) as it:
        for e in it:
            slug = bm25._indexable(e.name)
            if slug is None:
                continue
            try:
                if e.stat().st_mtime >= scan_t0 - 1.0:
                    late.add(slug)
            except OSError:
                pass
    live_docs = set(live["tf"]) - late
    clean_docs = set(clean["tf"]) - late
    report("A1 same document set", live_docs == clean_docs,
           "live %d clean %d late-excluded %d; only-live %s only-clean %s"
           % (len(live_docs), len(clean_docs), len(late), sorted(live_docs - clean_docs)[:3], sorted(clean_docs - live_docs)[:3]))
    common = live_docs & clean_docs
    bad_tf = [s for s in common if live["tf"][s] != clean["tf"][s]]
    bad_dl = [s for s in common if live["dl"][s] != clean["dl"][s]]
    report("A2 per-doc tf identical", not bad_tf, "%d differ: %s" % (len(bad_tf), bad_tf[:3]))
    report("A3 per-doc dl identical", not bad_dl, "%d differ: %s" % (len(bad_dl), bad_dl[:3]))

    def df_without(payload, drop):
        df = dict(payload["df"])
        for s in drop:
            tf = payload["tf"].get(s)
            if not tf:
                continue
            for term in tf:
                df[term] = df.get(term, 0) - 1
                if df[term] <= 0:
                    df.pop(term, None)
        return df
    df_live = df_without(live, late & set(live["tf"]))
    df_clean = df_without(clean, late & set(clean["tf"]))
    diff_terms = [k for k in set(df_live) | set(df_clean) if df_live.get(k) != df_clean.get(k)]
    report("A4 df identical over all terms", not diff_terms,
           "%d terms differ: %s" % (len(diff_terms), [(k, df_live.get(k), df_clean.get(k)) for k in diff_terms[:4]]))
    if not late:
        report("A5 n and avgdl identical", live["n"] == clean["n"] and abs(live["avgdl"] - clean["avgdl"]) < 1e-9,
               "n %d/%d avgdl %.6f/%.6f" % (live["n"], clean["n"], live["avgdl"], clean["avgdl"]))
    else:
        a = (sum(live["dl"][s] for s in live_docs) / len(live_docs)) if live_docs else 0.0
        b = (sum(clean["dl"][s] for s in clean_docs) / len(clean_docs)) if clean_docs else 0.0
        report("A5 avgdl over the common set", abs(a - b) < 1e-9, "%.6f vs %.6f (late docs excluded)" % (a, b))
    bl = bm25.BM25(bm25.INDEX)
    bc = bm25.BM25(tmp_index)
    mism = []
    for q in QUERIES:
        ra = [(s, round(v, 6)) for s, v in bl.score(q, 15) if s not in late][:10]
        rb = [(s, round(v, 6)) for s, v in bc.score(q, 15) if s not in late][:10]
        if ra != rb:
            mism.append((q, ra[:2], rb[:2]))
    report("A6 top-10 scores identical (%d queries)" % len(QUERIES), not mism, str(mism[:1])[:200])
    for fn in os.listdir(tmpdir):
        os.remove(os.path.join(tmpdir, fn))
    os.rmdir(tmpdir)
else:
    print("A  equivalence skipped (--quick)")

# ---- B. race guard ----------------------------------------------------------------------------------------
# The guard's window is between loaded_mt (taken inside the lock) and the final os.replace. A build landing
# BEFORE that point is legitimately built upon (refresh reloads the new payload) - a simulation there reads the
# correct behaviour as a failure. So the simulated build lands on the first read_doc() call.
write_control("ocelotgasket")
orig_read = bm25.read_doc
state = {"bumped": False}


def bumping_read(mem_dir, slug):
    if not state["bumped"]:
        state["bumped"] = True
        now = time.time() + 2
        os.utime(bm25.INDEX, (now, now))
        try:
            os.utime(bm25.INDEX + ".pkl", (now + 1, now + 1))
        except OSError:
            pass
    return orig_read(mem_dir, slug)


size_before = os.path.getsize(bm25.INDEX)
bm25.read_doc = bumping_read
try:
    r = bm25.refresh(quiet=True)
finally:
    bm25.read_doc = orig_read
size_after = os.path.getsize(bm25.INDEX)
present = in_index("ocelotgasket")
report("B1 refresh yields to a concurrent build", r == (0, 0) and not present and size_before == size_after,
       "returned %s; control in index: %s; bytes %d -> %d; bump: %s" % (r, present, size_before, size_after, state["bumped"]))
report("B1b no stale refresh lock left behind", not os.path.exists(bm25.INDEX + ".refresh.lock"))
r2 = bm25.refresh(quiet=True)
report("B2 next refresh picks the file up anyway", in_index("ocelotgasket"), "returned %s" % (r2,))

# ---- C. deleted during refresh ---------------------------------------------------------------------------
os.utime(CONTROL, None)


def deleting_read(mem_dir, slug):
    if slug == SLUG:
        remove_control()
    return orig_read(mem_dir, slug)


bm25.read_doc = deleting_read
try:
    r3 = bm25.refresh(quiet=True)
    ok = True
except Exception as exc:
    ok = False
    r3 = repr(exc)
finally:
    bm25.read_doc = orig_read
report("C1 file deleted mid-refresh: no raise, doc dropped", ok and not in_index("ocelotgasket"), "returned %s" % (r3,))
remove_control()
bm25.refresh(quiet=True)

# ---- D. freeze --------------------------------------------------------------------------------------------
bm25.freeze(5, "bm25_lane_selftest D")
write_control("narwhalspanner")
r4 = bm25.refresh(quiet=True)
report("D1 refresh skips while frozen", r4 == (0, 0) and not in_index("narwhalspanner"), "returned %s" % (r4,))
bm25.unfreeze()
r5 = bm25.refresh(quiet=True)
report("D2 refresh proceeds after unfreeze", in_index("narwhalspanner"), "returned %s" % (r5,))
io.open(bm25.INDEX + ".freeze", "w", encoding="utf-8").write(json.dumps({"until": time.time() - 10, "why": "stale", "holder": 1}))
remove_control()
r6 = bm25.refresh(quiet=True)
report("D3 stale freeze ignored and removed", not os.path.exists(bm25.INDEX + ".freeze") and not in_index("narwhalspanner"),
       "returned %s" % (r6,))

remove_control()
bm25.refresh(quiet=True)
report("Z  no control doc left in the index", not in_index("ocelotgasket") and not in_index("narwhalspanner"))
print("RESULT:", "ALL PASS" if fails == 0 else "%d FAILED" % fails)
sys.exit(1 if fails else 0)
