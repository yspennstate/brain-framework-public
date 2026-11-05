"""test_coactivation_partition.py - an unreinforced coactivation link decays by elapsed time alone, however that time is
cut into folds (found by a scheduled audit, 28 Sep).

    python test_coactivation_partition.py [OLD_COACTIVATION_END.py]

Every table lives in a temporary folder; the live table is never read or written. Folds are driven the way the hook
meets them: before each fold the table's mtime is set that many seconds before the real clock, and the module's own
fold_targets rewrites it (a fresh two-memory log each time, so every fold is a decaying rewrite).
 P1 decay partition: edges at 1.0 and 2.0; one fold after 14 days against 2,016 folds ten minutes apart. The 2.0
    edge ends near 1.0 in both arms and the arms agree within 1e-4 relative; the 1.0 edge is pruned in both.
 P2 decay grid: edges at 0.6, 1.1, 2.0, 5.0 and 9.0 (none lands on the 0.5 floor at a span's end); folds every minute for a day, every ten minutes for three
    days and every two hours for fourteen, each against one fold over the same span: the same edges survive, every
    survivor agrees within 1e-4 relative, and the one-fold arm is within 1e-4 of 0.5 ** (span / half-life).
 P3 rounding: 10,000 random weights written by _atomic_write_weights read back as the same floats.
 P4 after a fold the table's mtime is the instant the fold decayed for, so with the clock pinned two folds of 100 s and
    600 s decay a weight by exactly 0.5 ** (700 s / half-life) (skipped for a module without a pinned clock).
 P5 prune-then-reinforce partition (29 Sep): a 0.51 link co-read once in a focused
    session 14 days later ends at 1.0 whether or not an unrelated fold on day 1 rewrote the table in between (it fell
    below the 0.5 floor on day 0.4, so the co-read starts from nothing either way).
 P6 five links over 30 days - co-read after they crossed the floor, before it, in a 20-memory session (a 0.3 increment,
    below the floor), and never - with only their own folds against the same folds plus an unrelated fold every three
    hours: both arms equal a continuous-time model of the rule (decay by elapsed time, gone from the moment a weight is
    below the floor, a co-read adds its focus) to 1e-9, survivors included.
With OLD_COACTIVATION_END.py (the file before 28 Sep), P1 must fail; with --old-prune PRE_29SEP.py (the file before the
floor applied at read), P5 and P6 must fail: the checks can fail.
"""
import importlib.machinery
import importlib.util
import inspect
import io
import os
import random
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ARGS = sys.argv[1:]
OLD_PRUNE = None
if "--old-prune" in ARGS:
    i = ARGS.index("--old-prune")
    OLD_PRUNE = ARGS[i + 1]
    del ARGS[i:i + 2]
NEW = os.path.join(HERE, "coactivation_end.py")
OLD = ARGS[0] if ARGS else None
DAY = 86400.0
fails, ran = [], []
P1_SEEN = {}     # what P1 saw, so the negative control asserts the defect itself


def check(ok, what):
    print(("PASS  " if ok else "FAIL  ") + what)
    ran.append(what)
    if not ok:
        fails.append(what)


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path, loader=importlib.machinery.SourceFileLoader(name, path))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def write_table(tsv, weights):
    with io.open(tsv, "w", encoding="utf-8", newline="\n") as f:
        f.write("a\tb\tweight\n")
        for (a, b), w in sorted(weights.items()):
            f.write("%s\t%s\t%r\n" % (a, b, float(w)))


def read_weights(mod, tsv):
    out = {}
    with io.open(tsv, encoding="utf-8") as f:
        next(f, None)
        for line in f:
            p = line.rstrip("\n").split("\t")
            if len(p) == 3 and p[0] != mod.APPLIED_TAG:
                out[(p[0], p[1])] = float(p[2])
    return out


def run_arm(mod, root, weights0, gaps):
    """a table holding weights0, then one fold per gap (seconds since the table's last rewrite); the final weights"""
    os.makedirs(root)
    tsv, audit = os.path.join(root, "coactivation.tsv"), os.path.join(root, "folds.log")
    write_table(tsv, weights0)
    for i, gap in enumerate(gaps):
        log = os.path.join(root, "f%05d.log" % i)
        with io.open(log, "w", encoding="utf-8", newline="\n") as f:
            f.write("fold_a\nfold_b\n")
        t = time.time() - gap
        os.utime(tsv, (t, t))
        r = mod.fold_targets([log], tsv=tsv, foldlog=audit, stamp="2099-01-01T00:00:00")
        if r.get("result") != "decay":
            raise RuntimeError("fold %d did not rewrite the table: %r" % (i, r))
    w = read_weights(mod, tsv)
    w.pop(("fold_a", "fold_b"), None)          # the pair folded every time is not under test
    return w


def rel(a, b):
    return abs(a - b) / max(abs(b), 1e-300)


def compare(many, one, weights0, tol):
    """(same survivors, largest relative gap between the arms over the survivors)"""
    same = set(many) == set(one)
    gap = max([rel(many[k], one[k]) for k in set(many) & set(one)] or [0.0])
    return same, gap, {k: (round(many.get(k, -1), 6), round(one.get(k, -1), 6)) for k in weights0}


def p1(mod, td, tag):
    w0 = {("p", "one"): 1.0, ("p", "two"): 2.0}
    many = run_arm(mod, os.path.join(td, tag + "_p1_many"), w0, [600.0] * 2016)
    one = run_arm(mod, os.path.join(td, tag + "_p1_one"), w0, [14 * DAY])
    same, gap, shown = compare(many, one, w0, 1e-4)
    P1_SEEN[tag] = {"one_kept_many": ("p", "one") in many, "two_many": many.get(("p", "two"))}
    two_ok = ("p", "two") in many and ("p", "two") in one and rel(many[("p", "two")], one[("p", "two")]) < 1e-4 \
        and abs(one[("p", "two")] - 1.0) < 1e-3
    pruned = ("p", "one") not in many and ("p", "one") not in one
    return same and two_ok and pruned, "%s P1 one 14-day fold against 2,016 ten-minute folds: (many, one) %s" % (tag, shown)


def p2(mod, td, tag):
    w0 = {("g", "0.6"): 0.6, ("g", "1.1"): 1.1, ("g", "2.0"): 2.0, ("g", "5.0"): 5.0, ("g", "9.0"): 9.0}
    half = mod.HALF_LIFE_DAYS * DAY
    worst, ok_all, notes = 0.0, True, []
    for step, span, name in ((60.0, DAY, "1min x 1 day"), (600.0, 3 * DAY, "10min x 3 days"),
                             (7200.0, 14 * DAY, "2h x 14 days")):
        n = int(round(span / step))
        many = run_arm(mod, os.path.join(td, "%s_p2_%d_many" % (tag, n)), w0, [step] * n)
        one = run_arm(mod, os.path.join(td, "%s_p2_%d_one" % (tag, n)), w0, [span])
        same, gap, _ = compare(many, one, w0, 1e-4)
        expect = {k: v * 0.5 ** (span / half) for k, v in w0.items() if v * 0.5 ** (span / half) >= mod.MIN_KEEP}
        exact = set(one) == set(expect) and all(rel(one[k], expect[k]) < 1e-4 for k in expect)
        ok_all = ok_all and same and gap < 1e-4 and exact
        worst = max(worst, gap)
        notes.append("%s: same survivors %s, largest gap %.2e, one-fold arm on the formula %s" % (name, same, gap, exact))
    return ok_all, "%s P2 grid, largest relative gap %.2e; %s" % (tag, worst, "; ".join(notes))


def p3(mod, td, tag):
    rng = random.Random(20260928)
    ws = {("w%05d" % i, "x"): rng.uniform(0.5, 50.0) for i in range(10000)}
    tsv = os.path.join(td, tag + "_p3.tsv")
    if "mtime" in inspect.signature(mod._atomic_write_weights).parameters:
        mod._atomic_write_weights(tsv, ws, set(), mtime=time.time())
    else:
        mod._atomic_write_weights(tsv, ws, set())
    back, _ = mod._load_decayed_weights(tsv, os.path.getmtime(tsv))     # read at the table's own instant: no decay
    same = sum(1 for k, v in ws.items() if back.get(k) == v)
    return same == len(ws), "%s P3 %d of %d random weights read back as the same float" % (tag, same, len(ws))


def p4(mod, td, tag):
    if "now" not in inspect.signature(mod.fold_targets).parameters:
        return None, "%s P4 skipped: this module has no pinned clock" % tag
    root = os.path.join(td, tag + "_p4")
    os.makedirs(root)
    tsv, audit = os.path.join(root, "coactivation.tsv"), os.path.join(root, "folds.log")
    write_table(tsv, {("p", "q"): 2.0})
    t0 = 1_900_000_000.0
    os.utime(tsv, (t0, t0))
    marks = []
    for i, t in enumerate((t0 + 100.0, t0 + 700.0)):
        log = os.path.join(root, "g%d.log" % i)
        with io.open(log, "w", encoding="utf-8", newline="\n") as f:
            f.write("fold_a\nfold_b\n")
        mod.fold_targets([log], tsv=tsv, foldlog=audit, stamp="2099-01-01T00:00:00", now=t)
        marks.append(os.path.getmtime(tsv) - t)
    got = read_weights(mod, tsv)[("p", "q")]
    want = 2.0 * 0.5 ** (700.0 / (mod.HALF_LIFE_DAYS * DAY))
    ok = rel(got, want) < 1e-12 and all(abs(m) < 1e-5 for m in marks)
    return ok, "%s P4 two pinned folds (100 s, then 600 s): weight %.15f against %.15f; mtime minus the fold's instant %s" % (
        tag, got, want, ["%.1e" % m for m in marks])


def fold_at(mod, root, tsv, t, sessions, k):
    """one fold at the pinned instant t over one log per session (each a list of memories)"""
    logs = []
    for j, mems in enumerate(sessions):
        log = os.path.join(root, "s%05d_%d.log" % (k, j))
        with io.open(log, "w", encoding="utf-8", newline="\n") as f:
            f.write("".join(m + "\n" for m in mems))
        logs.append(log)
    r = mod.fold_targets(logs, tsv=tsv, foldlog=os.path.join(root, "folds.log"), stamp="2099-01-01T00:00:00", now=t)
    if r.get("result") != "decay":
        raise RuntimeError("fold %d did not rewrite the table: %r" % (k, r))


def run_pinned(mod, root, weights0, t0, folds):
    """a table holding weights0 at t0, then folds [(t, sessions)] in time order; the final table, the helper pair left out"""
    os.makedirs(root)
    tsv = os.path.join(root, "coactivation.tsv")
    write_table(tsv, weights0)
    os.utime(tsv, (t0, t0))
    for k, (t, sessions) in enumerate(sorted(folds, key=lambda f: f[0])):
        fold_at(mod, root, tsv, t, sessions, k)
    w = read_weights(mod, tsv)
    w.pop(("fold_a", "fold_b"), None)
    return w


def p5(mod, td, tag):
    if "now" not in inspect.signature(mod.fold_targets).parameters:
        return None, "%s P5 skipped: this module has no pinned clock" % tag
    t0 = 1_900_000_000.0
    target = (t0 + 14 * DAY, [["t", "u"]])
    a = run_pinned(mod, os.path.join(td, tag + "_p5_a"), {("t", "u"): 0.51}, t0, [target])
    b = run_pinned(mod, os.path.join(td, tag + "_p5_b"), {("t", "u"): 0.51}, t0,
                   [(t0 + DAY, [["fold_a", "fold_b"]]), target])
    wa, wb = a.get(("t", "u")), b.get(("t", "u"))
    P5_SEEN[tag] = (wa, wb)
    ok = wa is not None and wb is not None and abs(wa - 1.0) < 1e-12 and abs(wb - 1.0) < 1e-12
    return ok, "%s P5 a 0.51 link co-read on day 14: %s with no fold between, %s after an unrelated fold on day 1" % (
        tag, wa, wb)


def model(weights0, t0, reads, t_end, half, floor):
    """the rule in continuous time: decay by elapsed time, gone from the moment a weight is below the floor, a co-read adds
    its focus (reads: [(t, pair, focus)])"""
    out = {}
    pairs = set(weights0) | {p for _t, p, _f in reads}
    for p in pairs:
        w, t_prev = weights0.get(p, 0.0), t0
        for t, q, f in sorted(r for r in reads if r[1] == p):
            w *= 0.5 ** ((t - t_prev) / half)
            w = w if w >= floor else 0.0
            w += f
            w = w if w >= floor else 0.0
            t_prev = t
        w *= 0.5 ** ((t_end - t_prev) / half)
        if w >= floor:
            out[p] = w
    return out


def p6(mod, td, tag):
    if "now" not in inspect.signature(mod.fold_targets).parameters:
        return None, "%s P6 skipped: this module has no pinned clock" % tag
    t0, end = 1_900_000_000.0, 1_900_000_000.0 + 30 * DAY
    w0 = {("a", "b"): 1.0, ("c", "d"): 0.51, ("e", "f"): 1.0, ("i", "j"): 3.0}
    survey = ["g%02d" % i for i in range(20)]                  # 20 memories: every pair gets min(1, 6/20) = 0.3
    own = [(t0 + 20 * DAY, [["a", "b"]]),                      # 0.37 by then: below the floor since day 14
           (t0 + 14 * DAY, [["c", "d"]]),                      # the auditor's link
           (t0 + 10 * DAY, [["e", "f"]]),                      # 0.61 by then: still alive, adds to it
           (t0 + 5 * DAY, [survey]),                           # 0.3 each: never above the floor
           (t0 + 25 * DAY, [["g", "h"]]),                      # a new link
           (end, [["fold_a", "fold_b"]])]                      # the read at day 30
    busy = own + [(t0 + k * 3 * 3600.0, [["fold_a", "fold_b"]]) for k in range(1, 240) if k % 8]
    reads = []
    for t, sessions in own:
        for mems in sessions:
            focus = min(1.0, mod.FOCUS_K / float(len(set(mems))))
            ms = sorted(set(mems))
            reads += [(t, (x, y), focus) for i, x in enumerate(ms) for y in ms[i + 1:] if (x, y) != ("fold_a", "fold_b")]
    want = model(w0, t0, reads, end, mod.HALF_LIFE_DAYS * DAY, mod.MIN_KEEP)
    got_own = run_pinned(mod, os.path.join(td, tag + "_p6_own"), w0, t0, own)
    got_busy = run_pinned(mod, os.path.join(td, tag + "_p6_busy"), w0, t0, busy)

    def same(got):
        return set(got) == set(want) and all(rel(got[k], want[k]) < 1e-9 for k in want)
    P6_SEEN[tag] = (same(got_own), same(got_busy))
    shown = {"%s%s" % k: (round(got_own.get(k, 0), 4), round(got_busy.get(k, 0), 4), round(want.get(k, 0), 4))
             for k in sorted(set(want) | set(got_own) | set(got_busy))}
    return same(got_own) and same(got_busy), "%s P6 (own folds, busy, model) at day 30: %s; %d unrelated folds" % (
        tag, shown, len(busy) - len(own))


P5_SEEN, P6_SEEN = {}, {}


def main():
    t_start = time.time()
    with tempfile.TemporaryDirectory(prefix="coactivation-partition-") as td:
        new = load(NEW, "coact_new")
        for fn in (p1, p2, p3, p4, p5, p6):
            ok, what = fn(new, td, "live")
            if ok is None:
                print("SKIP  " + what)
            else:
                check(ok, what)
        if OLD:
            old = load(OLD, "coact_old")
            ok, what = p1(old, td, "before 28 Sep")
            seen = P1_SEEN["before 28 Sep"]
            # the defect itself: ten-minute folds never prune the unreinforced 1.0 edge and leave the 2.0 edge well above 1
            check(seen["one_kept_many"] and (seen["two_many"] or 0) > 1.3,
                  "N1 the hook before this change keeps unreinforced links under frequent folds (the check can fail): "
                  + what.split(": ", 1)[1])
        if OLD_PRUNE:
            prev = load(OLD_PRUNE, "coact_prev")
            p5(prev, td, "before 29 Sep")
            wa, wb = P5_SEEN["before 29 Sep"]
            check(wa is not None and abs(wa - 1.255) < 1e-9 and wb is not None and abs(wb - 1.0) < 1e-12,
                  "N2 the hook before the floor applied at read gives the auditor's 1.255 and 1.0 (the check can fail): "
                  "%s, %s" % (wa, wb))
            p6(prev, td, "before 29 Sep")
            check(P6_SEEN["before 29 Sep"] != (True, True),
                  "N3 the hook before it departs from the model in at least one arm (own, busy match: %s)" % (
                      P6_SEEN["before 29 Sep"],))
    print("FAIL" if fails else "PASS", "- %d checks, %d failed, %.0f s" % (len(ran), len(fails), time.time() - t_start))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
