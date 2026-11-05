"""test_fusion_shared.py - recall and the learner's replay fuse with one function; a one-item or flat list keeps its weight;
a replay the logged lists cannot settle is not learned from (29 Sep 2026, claude-825b1305; interactive audit G02 and
G03, scheduled audit account01/B03 B03-R7-BODY-SINGLETON-LAMBDA-COLLAPSE-026).

    python test_fusion_shared.py [--lane GRAPH_LANE.py] [--learn GRAPH_LEARN.py]
                                 [--old-lane OLD_GRAPH_LANE.py] [--old-learn OLD_GRAPH_LEARN.py]

Pure functions and throwaway subprocesses; nothing live is read or written.
 F1 a one-item lexical body (A 10) against a graph (B 7, C 0) at weight 0.1: A stays first, and the scores stay apart
 F2 a body of equal scores (A 5, D 5): both above the graph-only items at weight 0.1
 F3 500 random lists with a real range in each: the new fuse returns exactly what the old one did, order and scores
 F4 500 random lists, ties and flat lists among them: the learner's replay orders exactly as recall does
 F5 a tie replayed in 16 processes with different hash seeds: one order, recall's
 F6 the audit's case (30 single-list leaders in each list, a shared candidate at 70, weights 0.4): recall ranks the
    shared one first, the logged lists cannot show it, and the replay is marked not exact
 F7 lists under 30 items: exact; both lists cut, the target clear of every range: exact; a graph-only neighbour
    within reach of the target: not exact
With --old-lane / --old-learn, the old versions must fail F1 (N1) and F5 (N2); each check can fail.
"""
import importlib.machinery
import importlib.util
import json
import os
import random
import subprocess
import sys

if __name__ == "__main__" and ("-h" in sys.argv[1:] or "--help" in sys.argv[1:]):
    print(__doc__)
    sys.exit(0)

HERE = os.path.dirname(os.path.abspath(__file__))
ARGS = sys.argv[1:]


def arg(name, default=None):
    return os.path.abspath(ARGS[ARGS.index(name) + 1]) if name in ARGS else default


LANE = arg("--lane", os.path.join(os.path.expanduser("~"), ".claude", "dreaming", "graph_lane.py"))
LEARN = arg("--learn", os.path.join(HERE, "graph_learn.py"))
OLD_LANE, OLD_LEARN = arg("--old-lane"), arg("--old-learn")
fails, ran = [], []


def check(ok, what):
    print(("PASS  " if ok else "FAIL  ") + what)
    ran.append(what)
    if not ok:
        fails.append(what)


def load(path, name):
    if os.path.dirname(path) not in sys.path:
        sys.path.insert(0, os.path.dirname(path))
    loader = importlib.machinery.SourceFileLoader(name, path)
    spec = importlib.util.spec_from_file_location(name, path, loader=loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


def rand_lists(rng, flat_ok=True):
    names = ["m%02d" % i for i in range(40)]
    body = [(s, round(rng.uniform(0, 30), 1 if rng.random() < 0.3 else 4)) for s in rng.sample(names, rng.randint(1, 25))]
    graph = [(s, round(rng.uniform(0, 5), 1 if rng.random() < 0.3 else 4)) for s in rng.sample(names, rng.randint(1, 25))]
    if flat_ok and rng.random() < 0.15:
        body = [(s, 7.0) for s, _ in body]
    if flat_ok and rng.random() < 0.15:
        graph = [(s, 2.0) for s, _ in graph]
    body.sort(key=lambda x: -x[1])
    graph.sort(key=lambda x: -x[1])
    return body, graph


def seed_orders(learn_path, lane_path, n=16):
    code = ("import importlib.machinery,importlib.util,json,sys\n"      # the loader named: a backup's suffix is not .py
            "ld=importlib.machinery.SourceFileLoader('gl', %r)\n"
            "spec=importlib.util.spec_from_file_location('gl', ld.path, loader=ld)\n"
            "m=importlib.util.module_from_spec(spec); sys.path.insert(0, %r); spec.loader.exec_module(m)\n"
            "if hasattr(m, 'LANE_PATH'): m.LANE_PATH=%r\n"
            "body=[('anchor',10.0),('bb',5.0),('aa',5.0),('cc',5.0)]; graph=[('bb',1.0),('aa',1.0),('cc',1.0),('zz',0.0)]\n"
            "print(json.dumps(m.fuse(body, graph, 0.2)))\n") % (learn_path, os.path.dirname(learn_path), lane_path)
    out = set()
    for seed in range(n):
        env = dict(os.environ, PYTHONHASHSEED=str(seed))
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
        out.add(r.stdout.strip() or ("error: " + r.stderr.strip()[-200:]))
    return out


def main():
    lane = load(LANE, "lane_new")
    gl = load(LEARN, "learn_new")
    gl.LANE_PATH = LANE
    f1 = lane.fuse([("A", 10.0)], [("B", 7.0), ("C", 0.0)], 0.1)
    check(f1[0][0] == "A" and f1[0][1] == 10.0 and len({v for _, v in f1}) == 3,
          "F1 a one-item body keeps its weight: %s" % f1)
    f2 = [s for s, _ in lane.fuse([("A", 5.0), ("D", 5.0)], [("E", 3.0), ("F", 1.0)], 0.1)]
    check(f2[:2] == ["A", "D"], "F2 a body of equal scores stays above the graph-only items: %s" % f2)
    rng = random.Random(29)
    if OLD_LANE:
        old_lane = load(OLD_LANE, "lane_old")
    else:
        old_lane = None
    same, tried = 0, 0
    for _ in range(500):
        body, graph = rand_lists(rng, flat_ok=False)
        if len({v for _, v in body}) < 2 or len({v for _, v in graph}) < 2:
            continue
        lam = rng.choice((0.05, 0.1, 0.2, 0.3, 0.4))
        tried += 1
        ref = old_lane.fuse(body, graph, lam) if old_lane else None
        new = lane.fuse(body, graph, lam)
        same += (ref is None) or (ref == new)
    if old_lane:
        check(same == tried, "F3 lists with a real range: the new fuse equals the old one on %d of %d" % (same, tried))
    parity, n = 0, 0
    for _ in range(500):
        body, graph = rand_lists(rng)
        lam = rng.choice((0.05, 0.1, 0.2, 0.3, 0.4))
        n += 1
        parity += gl.fuse(body, graph, lam) == [s for s, _ in lane.fuse(body, graph, lam)]
    check(parity == n, "F4 the learner's replay orders as recall does on %d of %d random lists" % (parity, n))
    orders = seed_orders(LEARN, LANE)
    recall_order = json.dumps([s for s, _ in lane.fuse([("anchor", 10.0), ("bb", 5.0), ("aa", 5.0), ("cc", 5.0)],
                                                      [("bb", 1.0), ("aa", 1.0), ("cc", 1.0), ("zz", 0.0)], 0.2)])
    check(orders == {recall_order}, "F5 a tie in 16 processes: %d order(s), recall's %s%s" % (
        len(orders), recall_order, "" if orders == {recall_order} else ": " + sorted(orders)[0][:160]))
    lead_b = [("L%02d" % i, 100.0 - i) for i in range(30)]
    lead_g = [("G%02d" % i, 100.0 - i) for i in range(30)]
    full_b, full_g = lead_b + [("S", 70.0), ("Zb", 0.0)], lead_g + [("S", 70.0), ("Zg", 0.0)]
    live_first = lane.fuse(full_b, full_g, 0.4)[0][0]
    logged_b, logged_g, bounds = full_b[:30], full_g[:30], [0.0, 100.0, 0.0, 100.0]
    replay = gl.fuse(logged_b, logged_g, 0.4, bounds)
    check(live_first == "S" and "S" not in replay and gl.replay_complete(logged_b, logged_g, 0.4, bounds, "S") is False,
          "F6 the audit's case: recall ranks %s first, the replay never sees it, marked not exact" % live_first)
    small_b, small_g = [("a", 9.0), ("b", 4.0), ("c", 1.0)], [("b", 3.0), ("d", 2.0), ("e", 0.0)]
    exact_small = all(gl.replay_complete(small_b, small_g, lam, None, t) for lam in (0.1, 0.4) for t in ("a", "b", "d"))
    cut_b = [("T", 100.0)] + [("b%02d" % i, 50.0 - i) for i in range(29)]
    cut_g = [("T", 10.0)] + [("g%02d" % i, 3.0 - i * 0.1) for i in range(29)]
    clear = gl.replay_complete(cut_b, cut_g, 0.2, [0.0, 100.0, 0.0, 10.0], "T")
    near_b = [("x%02d" % i, 100.0 - i) for i in range(10)] + [("T", 60.0)] + [("y%02d" % i, 59.0 - i) for i in range(19)]
    near_g = [("N", 10.0)] + [("g%02d" % i, 9.0 - i * 0.2) for i in range(29)]
    close = gl.replay_complete(near_b, near_g, 0.4, [0.0, 100.0, 0.0, 10.0], "T")
    check(exact_small and clear and close is False,
          "F7 short lists exact %s; both cut and clear exact %s; a neighbour within reach not exact %s" % (
              exact_small, clear, close is False))
    whole = gl.replay_complete(near_b, near_g, 0.4, [0.0, 100.0, 0.0, 10.0], "T", [30, 30])
    longer = gl.replay_complete(near_b, near_g, 0.4, [0.0, 100.0, 0.0, 10.0], "T", [30, 250])
    check(whole is True and longer is False,
          "F8 the recorded lengths decide: 30 of 30 kept is whole and exact (%s); 30 of 250 is cut, not exact (%s)" % (
              whole, longer))
    if OLD_LANE:
        o1 = old_lane.fuse([("A", 10.0)], [("B", 7.0), ("C", 0.0)], 0.1)
        check(o1[0][0] != "A", "N1 the old fuse gives a one-item body's place to the graph: %s (the check can fail)" % o1)
    if OLD_LEARN:
        oo = seed_orders(OLD_LEARN, LANE)
        errs = [o for o in oo if o.startswith("error")]
        check(len(oo) > 1 and not errs, "N2 the old replay orders the tie %d ways in 16 processes%s (the check can fail)" % (
            len(oo), (": " + errs[0][:160]) if errs else ""))
    print("FAIL" if fails else "PASS", "- %d controls, %d failed" % (len(ran), len(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
