"""served_vs_delivered_1to1.py - served_vs_delivered.py with a one-to-one join (27 Sep, claude-825b1305), after
account01/B08 R12 (DELIVERY-JOIN-011): the first matcher let one transcript occurrence of a line confirm every served
row of that line within 180 s, so a retry could make an earlier lost serve read as delivered.

Here each [subconscious] occurrence of a fragment in a session's transcript confirms at most one served line: served
lines are taken in time order and each takes the earliest unused occurrence in [t - 5 s, t + WINDOW]. A served row
followed within 5 s by a 'hook error' in logs/live.log is flagged (that evaluation aborted after writing its row). Both
matchers are run on the same rows, and the rows they disagree on are counted. Never prints the text of a line.

    python served_vs_delivered_1to1.py [--window 180] [--since EPOCH] [--until EPOCH]
"""
import bisect
import datetime
import glob
import json
import os
import sys

HOME = os.path.expanduser("~")
LIVE = os.path.join(HOME, ".claude", "brain_graph", "live")
LOG = os.path.join(HOME, ".claude", "brain_graph", "logs", "live.log")
PROJ = os.path.join(HOME, ".claude", "projects")
a = sys.argv[1:]
WINDOW = float(a[a.index("--window") + 1]) if "--window" in a else 180.0
SINCE = float(a[a.index("--since") + 1]) if "--since" in a else 0.0
UNTIL = float(a[a.index("--until") + 1]) if "--until" in a else 9e18


def short(nm, n=46):                       # live_membrane.short, verbatim
    kd, _, rest = nm.partition(":")
    rest = rest.replace(os.path.expanduser("~"), "~")
    if len(rest) > n:
        rest = "..." + rest[-(n - 3):]
    return "%s:%s" % (kd, rest)


def fragment(kind, key):
    if kind in ("mem", "word"):
        return "this reminds me of `%s`" % key
    if kind == "place":
        return "usually go next to `%s`" % short(key[len("place:"):] if key.startswith("place:") else key, 70)
    return None


def strings(o):
    if isinstance(o, str):
        yield o
    elif isinstance(o, dict):
        for v in o.values():
            yield from strings(v)
    elif isinstance(o, list):
        for v in o:
            yield from strings(v)


def ts(rec):
    t = rec.get("timestamp")
    if not t:
        return None
    try:
        return datetime.datetime.fromisoformat(t.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


_cache = {}


def transcript(sid):
    if sid in _cache:
        return _cache[sid]
    hits = glob.glob(os.path.join(PROJ, "*", sid + ".jsonl")) or glob.glob(os.path.join(PROJ, "*", "*", sid + ".jsonl"))
    recs = None
    if hits:
        recs = []
        with open(hits[0], encoding="utf-8", errors="replace") as fh:
            for ln in fh:
                if "[subconscious]" not in ln:
                    continue
                try:
                    r = json.loads(ln)
                except ValueError:
                    continue
                t = ts(r)
                if t is not None:
                    recs.append((t, r))
    _cache[sid] = recs
    return recs


def occurrences(recs, frag):
    """(time, record index, verdict) of every [subconscious] line holding frag, in time order."""
    out = []
    for i, (t, rec) in enumerate(recs):
        best = None
        for s in strings(rec):
            j = s.find(frag)
            while j >= 0:
                start = s.rfind("[subconscious]", 0, j)
                end = s.find("\n", j)
                line = s[start if start >= 0 else j: end if end >= 0 else len(s)].rstrip()
                v = "WHOLE" if ("hint:" in line and line.endswith(")")) else "CUT"
                if best != "WHOLE":
                    best = v
                j = s.find(frag, j + 1)
        if best:
            out.append((t, i, best))
    return out


errs = []
if os.path.exists(LOG):
    for ln in open(LOG, encoding="utf-8", errors="replace"):
        if " hook error: " in ln:
            try:
                errs.append(datetime.datetime.strptime(ln[:19], "%Y-%m-%d %H:%M:%S").timestamp())
            except ValueError:
                pass
errs.sort()


def near_error(t0):
    k = bisect.bisect_left(errs, t0 - 1)
    return k < len(errs) and errs[k] <= t0 + 5


rows = [json.loads(l) for l in open(os.path.join(LIVE, "served.jsonl"), encoding="utf-8") if l.strip()]
rows = sorted((r for r in rows if SINCE <= float(r["t"]) < UNTIL), key=lambda r: float(r["t"]))
lines = []
for r in rows:
    for kind, key, _by in r["served"]:
        if kind == "held":
            continue                       # held back: never shown, not a delivery question
        lines.append((float(r["t"]), r["sid"], kind, key, fragment(kind, key), near_error(float(r["t"]))))

old = {"WHOLE": 0, "CUT": 0, "ABSENT": 0, "NO_TRANSCRIPT": 0}
new = {"WHOLE": 0, "CUT": 0, "ABSENT": 0, "NO_TRANSCRIPT": 0}
used = {}                                  # (sid, frag) -> set of record indexes already taken
flips, err_rows, err_absent, notes = 0, 0, 0, []
occ_cache = {}
for t0, sid, kind, key, frag, err in lines:
    recs = transcript(sid)
    if recs is None or not frag:
        old["NO_TRANSCRIPT"] += 1
        new["NO_TRANSCRIPT"] += 1
        continue
    if (sid, frag) not in occ_cache:
        occ_cache[(sid, frag)] = occurrences(recs, frag)
    occ = [o for o in occ_cache[(sid, frag)] if t0 - 5 <= o[0] <= t0 + WINDOW]
    vo = "WHOLE" if any(o[2] == "WHOLE" for o in occ) else ("CUT" if occ else "ABSENT")
    taken = used.setdefault((sid, frag), set())
    free = [o for o in occ if o[1] not in taken]
    if free:
        pick = next((o for o in free if o[2] == "WHOLE"), free[0])
        taken.add(pick[1])
        vn = pick[2]
    else:
        vn = "ABSENT"
    old[vo] += 1
    new[vn] += 1
    err_rows += err
    err_absent += err and vn == "ABSENT"
    if vo != vn:
        flips += 1
    if vn != "WHOLE":
        notes.append((sid[:8], kind, vn, "hook error within 5 s" if err else ""))


def share(c):
    n = c["WHOLE"] + c["CUT"] + c["ABSENT"]
    return "%d of %d whole (%.1f%%)" % (c["WHOLE"], n, 100.0 * c["WHOLE"] / n) if n else "none"


print("served rows %d, shown lines %d (held lines left out), window %.0f s" % (len(rows), len(lines), WINDOW))
print("first matcher (any occurrence in the window):  %s  %s" % (share(old), json.dumps(old)))
print("one-to-one matcher (each occurrence once):     %s  %s" % (share(new), json.dumps(new)))
print("lines whose verdict the join changes: %d; served rows followed by a hook error within 5 s: %d, of them absent "
      "under the one-to-one join: %d" % (flips, err_rows, err_absent))
for n in notes[:40]:
    print("  not whole:", n)
