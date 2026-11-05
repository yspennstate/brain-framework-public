"""Does the use-grown graph anticipate mistakes? The live membrane's top error candidate at each evaluation
(brain_graph/live/fires.jsonl: t, sid, kind, err = [[name, weight], ...]) against the errors that actually came
(brain_graph/events_touch.jsonl: kind err, id = the class, session = first 8 of the id). Read-only; aggregates only.

hit  = the top candidate's class occurs in the same session within WINDOW seconds after the evaluation
base = for each evaluation, the chance that class occurs in the same session within WINDOW seconds, averaged over ALL
       evaluations of that receiver kind (so a class that is simply common everywhere has a high base)
lift = hit rate / base rate for the same class
"""
import bisect, collections, json, os, sys

HOME = os.path.expanduser("~")
FIRES = os.path.join(HOME, ".claude", "brain_graph", "live", "fires.jsonl")
EVENTS = os.path.join(HOME, ".claude", "brain_graph", "events_touch.jsonl")
WINDOW = float(sys.argv[1]) if len(sys.argv) > 1 else 1800.0

evals = []
with open(FIRES, encoding="utf-8", errors="replace") as f:
    for ln in f:
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if not r.get("err") or not r.get("sid"):
            continue
        evals.append((float(r["t"]), str(r["sid"])[:8], r.get("kind") or "?", r["err"][0][0], float(r["err"][0][1])))
t0 = min(e[0] for e in evals)
t1 = max(e[0] for e in evals)
errs = collections.defaultdict(list)          # (session8, class) -> sorted times
with open(EVENTS, encoding="utf-8", errors="replace") as f:
    # the capture is appended in time order; skip what ends before the first evaluation
    for ln in f:
        if '"kind": "err"' not in ln:
            continue
        try:
            e = json.loads(ln)
        except ValueError:
            continue
        ts = float(e["ts"])
        if ts < t0 or ts > t1 + WINDOW:
            continue
        errs[(str(e["session"])[:8], "err:" + e["id"])].append(ts)
for v in errs.values():
    v.sort()
by_class_sessions = collections.defaultdict(set)
for (s, c) in errs:
    by_class_sessions[c].add(s)


def occurs(sess, cls, t):
    v = errs.get((sess, cls))
    if not v:
        return False
    i = bisect.bisect_right(v, t)
    return i < len(v) and v[i] <= t + WINDOW


print("evaluations with an error candidate: %d, %.1f hours (%s to %s); error events in the window: %d" % (
    len(evals), (t1 - t0) / 3600, __import__("time").strftime("%m-%d %H:%M", __import__("time").localtime(t0)),
    __import__("time").strftime("%m-%d %H:%M", __import__("time").localtime(t1)), sum(len(v) for v in errs.values())))
for kind in ("his", "test", "all"):
    ev = [e for e in evals if kind == "all" or e[2] == kind]
    if not ev:
        continue
    top = collections.Counter(e[3] for e in ev)
    print("\n== receiver kind %s: %d evaluations" % (kind, len(ev)))
    print("   %-34s %6s %8s %8s %6s" % ("top candidate class", "n", "hit", "base", "lift"))
    for cls, n in top.most_common(8):
        hits = sum(1 for e in ev if e[3] == cls and occurs(e[1], cls, e[0]))
        base = sum(1 for e in ev if occurs(e[1], cls, e[0])) / float(len(ev))
        hr = hits / float(n)
        print("   %-34s %6d %7.1f%% %7.1f%% %6s" % (cls[:34], n, 100 * hr, 100 * base, ("%.1f" % (hr / base)) if base else "-"))
    anyhit = sum(1 for e in ev if occurs(e[1], e[3], e[0]))
    print("   all top candidates: hit %.1f%% (%d of %d)" % (100.0 * anyhit / len(ev), anyhit, len(ev)))
    for lo, hi in ((0, 20), (20, 40), (40, 80), (80, 1e9)):
        b = [e for e in ev if lo <= e[4] < hi]
        if b:
            h = sum(1 for e in b if occurs(e[1], e[3], e[0]))
            print("   weight %4g-%-4s n %5d hit %.1f%%" % (lo, ("%g" % hi) if hi < 1e9 else "up", len(b), 100.0 * h / len(b)))
