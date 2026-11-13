"""eval_place_shadow.py - do the membrane's place lines name a place the session then goes to, and one it was not
already in?

The live membrane (brain_graph/live/fires.jsonl) logs, at each evaluation, its place candidates [[name, weight], ...]
(dir:... ids). Joined to the capture (events_touch.jsonl: dir and file events, session = first 8 of the id):
    hit      the session touches the top place p (a dir event with p's path, or a file under it) within WINDOW s after
    base     how often p is touched within WINDOW s after ANY evaluation of the same receiver kind
    before   of the hits, the share where the session had touched p in the WINDOW s before the evaluation too
Read-only; aggregates only (place names are not printed, only counts).

    python eval_place_shadow.py [OUT_JSON] [--window 1800]
"""
if "-h" in __import__("sys").argv[1:] or "--help" in __import__("sys").argv[1:]:
    print((__doc__ or "").strip() or "usage: python %s" % __import__("os").path.basename(__file__))
    raise SystemExit(0)
import bisect
import collections
import json
import os
import sys
import time

HOME = os.path.expanduser("~")
FIRES = os.path.join(HOME, ".claude", "brain_graph", "live", "fires.jsonl")
EVENTS = os.path.join(HOME, ".claude", "brain_graph", "events_touch.jsonl")
a = sys.argv[1:]
OUT = a[0] if a and not a[0].startswith("--") else None
WINDOW = float(a[a.index("--window") + 1]) if "--window" in a else 1800.0

evals = []
with open(FIRES, encoding="utf-8", errors="replace") as f:
    for ln in f:
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if not r.get("place") or not r.get("sid"):
            continue
        top = r["place"][0]
        evals.append((float(r["t"]), str(r["sid"])[:8], r.get("kind") or "?", top[0], float(top[1])))
if not evals:
    sys.exit("no evaluation carries a place candidate")
t0 = min(e[0] for e in evals)
t1 = max(e[0] for e in evals)
wanted = {e[3] for e in evals}
dirs = {w[4:] for w in wanted if w.startswith("dir:")}
touch = collections.defaultdict(list)     # (session8, place name) -> times
with open(EVENTS, encoding="utf-8", errors="replace") as f:
    for ln in f:
        if '"kind": "dir"' not in ln and '"kind": "file"' not in ln:
            continue
        try:
            e = json.loads(ln)
        except ValueError:
            continue
        ts = float(e["ts"])
        if ts < t0 - WINDOW or ts > t1 + WINDOW:
            continue
        s = str(e["session"])[:8]
        pid = e.get("id") or ""
        if e["kind"] == "dir":
            if pid in dirs:
                touch[(s, "dir:" + pid)].append(ts)
        else:                       # a file under a candidate directory touches that directory
            d = os.path.dirname(pid)
            while d and d not in dirs and len(d) > 2:
                nd = os.path.dirname(d)
                if nd == d:
                    break
                d = nd
            if d in dirs:
                touch[(s, "dir:" + d)].append(ts)
for v in touch.values():
    v.sort()


import os as _ko_os, sys as _ko_sys  # noqa: E402
_ko_sys.path.insert(0, _ko_os.path.join(_ko_os.path.expanduser("~"), ".claude", "brain_graph", "bin"))
from quarantine_rows import keep_old  # noqa: E402  a rerun keeps the previous output (R13 trap 1, 27 Sep): brain data is never deleted


def after(s, p, t):
    v = touch.get((s, p))
    if not v:
        return False
    i = bisect.bisect_right(v, t)
    return i < len(v) and v[i] <= t + WINDOW


def before(s, p, t):
    v = touch.get((s, p))
    if not v:
        return False
    i = bisect.bisect_right(v, t)
    return i > 0 and v[i - 1] >= t - WINDOW


res = {"built": time.strftime("%Y-%m-%d %H:%M:%S"), "window_s": WINDOW, "evaluations": len(evals),
       "span_hours": round((t1 - t0) / 3600, 1), "kinds": {}}
for kind in ("his", "test", "all"):
    ev = [e for e in evals if kind == "all" or e[2] == kind]
    if not ev:
        continue
    places = collections.Counter(e[3] for e in ev)
    base = {p: sum(1 for e in ev if after(e[1], p, e[0])) / float(len(ev)) for p in places}
    hits = [e for e in ev if after(e[1], e[3], e[0])]
    was_there = sum(1 for e in hits if before(e[1], e[3], e[0]))
    new_hits = len(hits) - was_there
    exp = sum(base[e[3]] for e in ev)
    ranked = sorted(ev, key=lambda e: -e[4])
    bars = {}
    for frac in (0.02, 0.05, 0.10):
        top = ranked[:max(1, int(round(frac * len(ranked))))]
        h = [e for e in top if after(e[1], e[3], e[0])]
        bars["top_%d_pct" % int(frac * 100)] = {
            "n": len(top), "min_weight": round(top[-1][4], 2), "hit": round(len(h) / len(top), 4),
            "lift": round(len(h) / sum(base[e[3]] for e in top), 2) if sum(base[e[3]] for e in top) else None,
            "hits_already_there": round(sum(1 for e in h if before(e[1], e[3], e[0])) / len(h), 4) if h else None}
    res["kinds"][kind] = {
        "evaluations": len(ev), "distinct_places": len(places),
        "top_place_share_of_evaluations": round(places.most_common(1)[0][1] / len(ev), 4),
        "hit": round(len(hits) / len(ev), 4), "lift": round(len(hits) / exp, 2) if exp else None,
        "hits_already_there": round(was_there / len(hits), 4) if hits else None,
        "new_place_hits": new_hits, "new_place_hit_rate": round(new_hits / len(ev), 4),
        # the line worth serving names a place the session was NOT in: its precision among those evaluations alone
        "not_there_before": sum(1 for e in ev if not before(e[1], e[3], e[0])),
        "new_place_precision": round(new_hits / max(1, sum(1 for e in ev if not before(e[1], e[3], e[0]))), 4),
        "bars": bars}
print(json.dumps(res, indent=1))
if OUT:
    json.dump(res, open(keep_old(OUT), "w", encoding="utf-8"), indent=1)
