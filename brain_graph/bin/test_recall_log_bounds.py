"""Controls for the recall log's list bounds and graph_learn's replay with them.

    python test_recall_log_bounds.py [DIR_WITH_graph_lane.py_AND_graph_learn.py] [ROWS_JSONL]

The directory defaults to the live files; ROWS_JSONL defaults to the newest graph_mixture rows in the recall keys
quarantine (whole body and graph lists as recall used them). Nothing live is written: log_recall writes to a
temporary path.
"""
if "-h" in __import__("sys").argv[1:] or "--help" in __import__("sys").argv[1:]:
    print((__doc__ or "").strip() or "usage: python %s" % __import__("os").path.basename(__file__))
    raise SystemExit(0)
import glob
import importlib.util
import json
import os
import sys
import tempfile

HOME = os.path.expanduser("~")
BIN = os.path.join(HOME, ".claude", "brain_graph", "bin")
DR = os.path.join(HOME, ".claude", "dreaming")
FAILS = []


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (("  " + detail) if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


def main():
    d = sys.argv[1] if len(sys.argv) > 1 else None
    sys.path[:0] = [BIN, DR]
    lane = load(os.path.join(d or DR, "graph_lane.py"), "lane_under_test")
    learn = load(os.path.join(d or BIN, "graph_learn.py"), "learn_under_test")
    old_learn_fuse = None
    try:
        import subprocess
        repo = os.path.join(HOME, "brain-repo")
        blob = subprocess.run(["git", "-C", repo, "show", "65c5995973:12_cognitive_architecture/brain_graph/live/graph_learn.py"],
                              capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
        p = os.path.join(tempfile.mkdtemp(prefix="gl_old_"), "graph_learn_old.py")
        open(p, "w", encoding="utf-8").write(blob)
        old_learn_fuse = load(p, "learn_old").fuse
    except Exception as exc:
        print("  (old graph_learn not loaded: %s)" % exc)

    # 1. log_recall writes the whole lists' bounds, keeps up to 100 items of each (30 before 29 Sep) and the whole lists'
    # lengths, so a replay knows whether a list was cut (interactive audit G02)
    log = os.path.join(tempfile.mkdtemp(prefix="rl_"), "recall_log.jsonl")
    os.environ.update(BRAIN_RECALL_LOG="1", BRAIN_RECALL_LOG_PATH=log)
    os.environ.pop("BRAIN_RECALL_LOG_FULL", None)
    body = [("b%02d" % i, 10.0 - i * 0.1) for i in range(50)]
    graph = [("g%02d" % i, 0.9 - i * 0.01) for i in range(40)]
    lane.log_recall("a test query", 0.1, body, graph, ["b00", "b01"])
    rec = [json.loads(l) for l in open(log, encoding="utf-8") if l.strip()][-1]
    check("1 the record carries the whole lists' bounds", rec.get("bounds") == [round(5.1, 6), 10.0, round(0.51, 6), 0.9],
          str(rec.get("bounds")))
    check("2 and all 50 and 40 items, under the cap of 100, with the lengths", len(rec["body"]) == 50
          and len(rec["graph"]) == 40 and rec.get("n") == [50, 40], "%d %d %s" % (len(rec["body"]), len(rec["graph"]),
                                                                            rec.get("n")))

    # 2. graph_learn's replay: the first 30 with the bounds equal the whole lists' order
    # the newest file with a sample worth the name: a three-question smoke run (27 Sep 15:22) became the newest file,
    # and control 4, which needs rows whose cut lists differ, could not pass on three rows (30 Sep)
    rows_path = sys.argv[2] if len(sys.argv) > 2 else next((
        p for p in sorted(glob.glob(os.path.join(HOME, "bank_recall_keys_quarantine", "graph_mixture_rows_*.jsonl")),
                          key=os.path.getmtime, reverse=True)
        if sum(1 for l in open(p, encoding="utf-8") if l.strip()) >= 100), None)
    if rows_path is None:
        # controls 3-5 replay a recorded bank; a machine without one runs 1-2 only and says so
        print("SKIP 3-5: no graph_mixture_rows_*.jsonl with 100 rows under ~/bank_recall_keys_quarantine "
              "(pass one as the second argument)")
        print("%d of 2 controls pass" % (2 - len(FAILS)))
        return 1 if FAILS else 0
    print("rows: %s" % os.path.basename(rows_path))
    rows = [json.loads(l) for l in open(rows_path, encoding="utf-8") if l.strip()]
    rows = [r for r in rows if r.get("graph") is not None and len(r["body"]) > 30][:200]
    same_b = diff_nb = same_old = 0
    for r in rows:
        b = [(s, float(v)) for s, v in r["body"]]
        g = [(s, float(v)) for s, v in r["graph"]]
        bounds = [min(v for _, v in b), max(v for _, v in b), min(v for _, v in g), max(v for _, v in g)]
        full = learn.fuse(b, g, 0.2)[:10]
        same_b += learn.fuse(b[:30], g[:30], 0.2, bounds)[:10] == full
        diff_nb += learn.fuse(b[:30], g[:30], 0.2)[:10] != full
        if old_learn_fuse:
            # up to the order of equal scores: the previous learner ordered them by hash, recall by name (29 Sep, G03)
            new, old = learn.fuse(b[:30], g[:30], 0.2), old_learn_fuse(b[:30], g[:30], 0.2)
            sc = dict(learn._lane().fuse_scores(b[:30], g[:30], 0.2))
            same_old += sorted(new) == sorted(old) and all(sc[x] >= sc[y] for x, y in zip(old, old[1:]))
    n = len(rows)
    check("3 with the bounds, the cut lists give the whole lists' top 10 (%d rows)" % n, n > 0 and same_b == n,
          "%d of %d" % (same_b, n))
    check("4 without them the cut lists differ in some rows (the control can fail)", diff_nb > 0, "%d of %d" % (diff_nb, n))
    check("5 without bounds the replay is what the previous graph_learn gave, but for the order of equal scores",
          old_learn_fuse is not None and same_old == n,
          "%d of %d" % (same_old, n))
    print("%d of 5 controls pass" % (5 - len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
