"""eval_act_errors.py - do error nodes that NAME THE ACT anticipate failures? (27 Sep, claude-825b1305; pre-registered
12:2x in study/TWO_DAY_RUN_2026-09-26.md)

The live membrane's error lines are classes like err:Bash:python:exit1 attached by co-occurrence with the context, and
they come true at about their base rate (the_membranes_error_lines_come_true_at_their_base_rate_2026_09_27). This asks
the act-level question on the capture (brain_graph/events_touch.jsonl). A tool call is the bundle of events one session
wrote at one timestamp: its tool id, command words, files and services. It FAILED if an err event of the same session
comes within WINDOW seconds after it and before that session's next call. Walking forward in time, each call is scored
by the failure rate of EARLIER calls with the same key (only calls before it, at least MIN_N of them):
    tool     the tool id (Bash:python, Read, Edit ...)
    words    tool id + the first two command words
    target   words + the first file or script the call names
Read-only; aggregates only (no command text is printed).

    python eval_act_errors.py [OUT_JSON] [--window 20] [--min-n 5] [--days N] [--exclude TOOL,TOOL]
"""
import collections
import json
import os
import sys
import time

HOME = os.path.expanduser("~")
EVENTS = os.path.join(HOME, ".claude", "brain_graph", "events_touch.jsonl")
a = sys.argv[1:]
OUT = a[0] if a and not a[0].startswith("--") else None
WINDOW = float(a[a.index("--window") + 1]) if "--window" in a else 20.0
MIN_N = int(a[a.index("--min-n") + 1]) if "--min-n" in a else 5
DAYS = float(a[a.index("--days") + 1]) if "--days" in a else 0.0
# tool ids left out entirely (calls and failures), e.g. Bash:sleep, which the harness refuses in the foreground
EXCLUDE = set(a[a.index("--exclude") + 1].split(",")) if "--exclude" in a else set()
t_start = time.time()
cutoff = (time.time() - DAYS * 86400) if DAYS else 0.0

bundles = []                 # (ts, session, tool, words tuple, target) in file order
errs = collections.defaultdict(list)     # session -> [err ts]
cur = None                   # (session, ts) of the bundle being filled
b_tool, b_words, b_files = None, [], []


import os as _ko_os, sys as _ko_sys  # noqa: E402
_ko_sys.path.insert(0, _ko_os.path.join(_ko_os.path.expanduser("~"), ".claude", "brain_graph", "bin"))
from quarantine_rows import keep_old  # noqa: E402  a rerun keeps the previous output (R13 trap 1, 27 Sep): brain data is never deleted


def flush():
    if cur is not None and b_tool is not None:
        bundles.append((cur[1], cur[0], b_tool, tuple(b_words[:2]), b_files[0] if b_files else ""))


with open(EVENTS, encoding="utf-8", errors="replace") as fh:
    for ln in fh:
        if '"kind": "err"' not in ln and '"kind": "tool"' not in ln and '"kind": "cmd"' not in ln \
                and '"kind": "file"' not in ln:
            continue
        try:
            e = json.loads(ln)
        except ValueError:
            continue
        try:
            ts = float(e["ts"])
        except (KeyError, TypeError, ValueError):
            continue
        if ts < cutoff:
            continue
        s = str(e.get("session") or "")[:8]
        k = e.get("kind")
        if k == "err":
            errs[s].append((ts, e.get("id") or "?"))
            continue
        key = (s, e["ts"])
        if key != cur:
            flush()
            cur, b_tool, b_words, b_files = key, None, [], []
        if k == "tool":
            b_tool = e.get("id")
        elif k == "cmd":
            b_words.append(e.get("id"))
        elif k == "file":
            b_files.append(e.get("id"))
    flush()
bundles = [b for b in bundles if b[2] not in EXCLUDE]
bundles.sort(key=lambda b: b[0])
by_sess = collections.defaultdict(list)
for i, b in enumerate(bundles):
    by_sess[b[1]].append(i)
failed = [False] * len(bundles)
err_class = [None] * len(bundles)
import bisect
for s, idx in by_sess.items():
    evs = sorted(errs.get(s, ()))
    if not evs:
        continue
    ev = [x[0] for x in evs]
    for j, i in enumerate(idx):
        t = bundles[i][0]
        nxt = bundles[idx[j + 1]][0] if j + 1 < len(idx) else float("inf")
        p = bisect.bisect_right(ev, t)
        if p < len(ev) and ev[p] <= min(t + WINDOW, nxt):
            failed[i] = True
            err_class[i] = evs[p][1]
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import session_kinds
    kind_of = {s: session_kinds.kind(s) for s in by_sess}
except Exception as ex:          # the split is optional; the main counts do not depend on it
    print("session_kinds unavailable: %s" % ex)
    kind_of = {}
n = len(bundles)
nf = sum(failed)
res = {"built": time.strftime("%Y-%m-%d %H:%M:%S"), "excluded_tools": sorted(EXCLUDE), "window_s": WINDOW, "min_n": MIN_N, "days": DAYS or "all",
       "calls": n, "failed": nf, "base_rate": round(nf / n, 4) if n else None,
       "span": [time.strftime("%Y-%m-%d %H:%M", time.localtime(bundles[0][0])),
                time.strftime("%Y-%m-%d %H:%M", time.localtime(bundles[-1][0]))] if n else None,
       "keys": {}}


def key_of(level, b):
    if level == "tool":
        return b[2]
    if level == "words":
        return (b[2],) + b[3]
    return (b[2],) + b[3] + (b[4],)


for level in ("tool", "words", "target"):
    seen = collections.defaultdict(lambda: [0, 0])     # key -> [calls, failures] before now
    scored = []                                          # (pred, failed, index)
    for i, b in enumerate(bundles):
        k = key_of(level, b)
        c = seen[k]
        if c[0] >= MIN_N:
            scored.append((c[1] / c[0], failed[i], i))
        c[0] += 1
        c[1] += int(failed[i])
    out = {"scored_calls": len(scored), "scored_failures": sum(f for _p, f, _i in scored), "distinct_keys": len(seen)}
    for thr in (0.2, 0.3, 0.5):
        flagged = [f for p, f, _i in scored if p >= thr]
        hits = sum(flagged)
        out["at_%g" % thr] = {"flagged": len(flagged), "share_of_calls": round(len(flagged) / n, 4) if n else None,
                             "precision": round(hits / len(flagged), 4) if flagged else None,
                             "recall_of_all_failures": round(hits / nf, 4) if nf else None}
    fl = [i for p, f, i in scored if p >= 0.3]
    out["at_0.3_by_session_kind"] = {}
    for kd in sorted({kind_of.get(bundles[i][1], "?") for i in range(n)}):
        allk = sum(1 for i in range(n) if kind_of.get(bundles[i][1], "?") == kd)
        failk = sum(1 for i in range(n) if failed[i] and kind_of.get(bundles[i][1], "?") == kd)
        flk = [i for i in fl if kind_of.get(bundles[i][1], "?") == kd]
        hk = sum(1 for i in flk if failed[i])
        out["at_0.3_by_session_kind"][kd] = {"calls": allk, "failed": failk, "flagged": len(flk),
                                             "precision": round(hk / len(flk), 4) if flk else None,
                                             "recall": round(hk / failk, 4) if failk else None}
    out["at_0.3_flagged_tools"] = collections.Counter(bundles[i][2] for i in fl).most_common(12)
    out["at_0.3_caught_error_classes"] = collections.Counter(err_class[i] for i in fl if failed[i]).most_common(12)
    res["keys"][level] = out
res["seconds"] = round(time.time() - t_start)
print(json.dumps(res, indent=1))
if OUT:
    json.dump(res, open(keep_old(OUT), "w", encoding="utf-8"), indent=1)
