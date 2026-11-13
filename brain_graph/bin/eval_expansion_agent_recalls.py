"""eval_expansion_agent_recalls.py - doc expansion on the searches agents actually ran.

Every `brain.py recall` since the live log began (live/recall_log.jsonl), judged by the live learner's own rule,
graph_learn.judge_searches: the session the record names (the shown-slug overlap within 90 s only for records logged
before 27 Sep 02:40), a window from the search to ten minutes later or the same session's next search, and the memories
opened in it, in order. The FIRST memory opened is the target; the best rank of any memory opened in the window is kept
beside it as a descriptive count only. Each judged search is ranked by the word index alone under the frozen shadow
indexes of eval_expansion_endtoend.py (base = the memory's own text, exp = plus its questions, expn = plus only the
question words it lacks). The result records the sha256 of the judge's source and of this file, and how every logged
search was joined or dropped.

Before 27 Sep 07:1x the command line took its own path: the shown-slug join for every record, an uncut ten-minute window
and the best-ranked of any opened memory per arm, while judged_recalls() below already used the shared judge. Results built before then (results/DOC_EXPANSION_AGENT_RECALLS_2026-09-27.json, and the separate
eval_agent_recalls_fixed.py's) are from those rules.

    python eval_expansion_agent_recalls.py OUT_JSON [--kinds his,script]
"""
import collections
import hashlib
import json
import math
import os
import sys
import time

HOME = os.path.expanduser("~")
sys.path.insert(0, os.path.join(HOME, ".claude", "dreaming"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_PREV_EXPANSION = os.environ.get("BRAIN_DOC_EXPANSION")
os.environ["BRAIN_DOC_EXPANSION"] = "0"
import bm25  # noqa: E402
# bm25 reads the switch once, at import; put the environment back, so the processes an importer starts run with the
# live default (27 Sep: the usage-order run imported this module and every brain.py child it started had expansion off)
if _PREV_EXPANSION is None:
    os.environ.pop("BRAIN_DOC_EXPANSION", None)
else:
    os.environ["BRAIN_DOC_EXPANSION"] = _PREV_EXPANSION

BG =os.path.join(HOME, ".claude", "brain_graph")
RECALL_LOG = os.path.join(BG, "live", "recall_log.jsonl")
EVENTS = os.path.join(BG, "events_touch.jsonl")
SHADOW = os.path.join(BG, "study", "doc_expansion", "shadow")
ARMS = {"base": os.path.join(SHADOW, "bm25_base.json.gz"), "exp": os.path.join(SHADOW, "bm25_exp.json.gz"),
        "expn": os.path.join(SHADOW, "bm25_expn.json.gz")}
TARGET = "first memory the search's session opened before min(10 min, that session's next search)"


import os as _ko_os, sys as _ko_sys  # noqa: E402
_ko_sys.path.insert(0, _ko_os.path.join(_ko_os.path.expanduser("~"), ".claude", "brain_graph", "bin"))
from quarantine_rows import keep_old  # noqa: E402  a rerun keeps the previous output (R13 trap 1, 27 Sep): brain data is never deleted


def tail_events(t_min):
    """(ts, session, kind, id) for 'recalled' and 'mem' events at or after t_min, from the end of the events file."""
    size = os.path.getsize(EVENTS)
    lo, hi = 0, size
    with open(EVENTS, "rb") as fh:
        while hi - lo > 1 << 20:                  # bisect on byte offset to the first line at t_min
            mid = (lo + hi) // 2
            fh.seek(mid)
            fh.readline()
            line = fh.readline()
            try:
                ts = json.loads(line)["ts"]
            except (ValueError, KeyError):
                lo = mid + 1
                continue
            if ts < t_min:
                lo = mid
            else:
                hi = mid
        fh.seek(lo)
        if lo:
            fh.readline()
        out = []
        for raw in fh:
            if b'"kind": "recalled"' not in raw and b'"kind": "mem"' not in raw:
                continue
            try:
                o = json.loads(raw)
            except ValueError:
                continue
            if o["ts"] >= t_min:
                out.append((float(o["ts"]), o["session"], o["kind"], str(o["id"]).lower()))
    return out


def judged_recalls(until=None, kinds=None, stats=None):
    """[(recall record, [memories opened])] for every logged search a session can be matched to (graph_learn's rule).
    Each record gains '_kind' - his / test / script / unknown, graph_learn.session_kind - and `kinds` keeps only those
    (27 Sep: 198 of the first 201 judged searches were test workers of the recall benches, not agents at work).
    `stats`, a dict, receives how each logged search was joined or dropped; the returned list does not depend on it."""
    recs = [json.loads(l) for l in open(RECALL_LOG, encoding="utf-8") if l.strip()]
    recs = [r for r in recs if r.get("q") and r["t"] + 600 < time.time() and (until is None or r["t"] < until)]
    if stats is not None:
        stats.update(logged=len(recs), no_session=0, no_open=0, joined_by_logged_session=0, joined_by_overlap=0,
                     window_cut_by_next_search=0, kinds_judged={}, kept=0)
    if not recs:
        return []
    evs = tail_events(min(r["t"] for r in recs) - 120)
    by_s = collections.defaultdict(list)
    for ts, s, kd, nid in evs:
        by_s[s].append((ts, kd, nid))
    # the live learner's own rule (graph_learn.judge_searches, 27 Sep): the session the record names, a window that ends
    # at the same session's next search, and the opens in order - opened[0] is the fixed target; the whole list is for
    # descriptive any-open counts only
    import graph_learn
    judged = []
    trio = graph_learn.judge_searches(recs, by_s)
    if stats is not None:
        times = collections.defaultdict(list)
        for r, best, _ in trio:
            if best:
                times[best].append(r["t"])
        nxt = {}
        for s, ts in times.items():
            ts.sort()
            for a, b in zip(ts, ts[1:]):
                nxt[(s, a)] = b
    kinds_seen = collections.Counter()
    for r, best, opened in trio:
        if stats is not None:
            if not best:
                stats["no_session"] += 1
            elif not opened:
                stats["no_open"] += 1
        if not best or not opened:
            continue
        r["_kind"] = "his" if r.get("entry") == "cli" else _kind(best)
        if stats is not None:
            own = str(r.get("session") or "")[:8].lower()
            stats["joined_by_logged_session" if own == best else "joined_by_overlap"] += 1
            if nxt.get((best, r["t"]), r["t"] + 600) < r["t"] + 600:
                stats["window_cut_by_next_search"] += 1
            kinds_seen[r["_kind"]] += 1
        if kinds is None or r["_kind"] in kinds:
            judged.append((r, opened))
    if stats is not None:
        stats["kinds_judged"] = dict(kinds_seen)
        stats["kept"] = len(judged)
    return judged


_KIND_CACHE, _KIND_IDX = {}, []


def _kind(session):
    import graph_learn
    if not _KIND_IDX:
        _KIND_IDX.append(graph_learn.transcript_index(max_age_days=30))
    return graph_learn.session_kind(session, _KIND_IDX[0], _KIND_CACHE)


def sign_p(a, b):
    n, k = a + b, min(a, b)
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n) if n else 1.0


def _sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def load_engines():
    return {arm: bm25.BM25.from_payload(bm25._load_payload(p)) for arm, p in ARMS.items()}


def run(out_path, kinds=None, engines=None):
    """Judge, rank and write the result; returns it. `engines` {arm: object with .score(q, topk)} replaces the shadow
    indexes (the parity test passes stubs, so it runs this same path)."""
    import graph_learn
    t0 = time.time()
    st = {}
    judged = judged_recalls(kinds=kinds, stats=st)
    if engines is None:
        engines = load_engines()
    rows = []
    for r, opened in judged:
        row = {"t": r["t"], "kind": r.get("_kind"), "n_open": len(opened)}
        for arm, eng in engines.items():
            top = [s.lower() for s, _ in eng.score(r["q"], topk=60)]
            row[arm] = top.index(opened[0]) + 1 if opened[0] in top else 0
            rr = [top.index(o) + 1 for o in opened if o in top]
            row[arm + "_any"] = min(rr) if rr else 0
        rows.append(row)
    receipt = {}
    try:
        receipt = json.load(open(os.path.join(SHADOW, "BUILD_RECEIPT.json"), encoding="utf-8"))
    except (OSError, ValueError):
        pass
    res = {"built": time.strftime("%Y-%m-%d %H:%M:%S"), "schema": 2, "target": TARGET,
           "judge": "graph_learn.judge_searches", "judge_source_sha256": _sha(graph_learn.__file__),
           "evaluator_sha256": _sha(os.path.abspath(__file__)), "kinds_filter": sorted(kinds) if kinds else None,
           "shadow_built": receipt.get("built"), "coverage": receipt.get("coverage"),
           "recalls_logged": st.get("logged", 0), "judged": len(rows), "joins": st, "arms": {}}
    for arm in engines:
        d = {}
        for tgt, key in (("first", arm), ("any", arm + "_any")):
            rk = [x[key] for x in rows]
            d[tgt] = {"hit1": sum(1 for k in rk if k == 1), "hit5": sum(1 for k in rk if 0 < k <= 5),
                      "hit10": sum(1 for k in rk if 0 < k <= 10), "hit60": sum(1 for k in rk if k > 0),
                      "mrr": round(sum(1.0 / k for k in rk if k) / max(1, len(rk)), 4)}
        res["arms"][arm] = d
    for arm in [a for a in engines if a != "base"]:
        if "base" not in engines:
            break
        for tgt, sfx in (("first", ""), ("any", "_any")):
            a, b = [x["base" + sfx] for x in rows], [x[arm + sfx] for x in rows]
            gi = sum(1 for x, y in zip(a, b) if (0 < y <= 5) and not (0 < x <= 5))
            go = sum(1 for x, y in zip(a, b) if (0 < x <= 5) and not (0 < y <= 5))
            res["%s_%s_top5" % (arm, tgt)] = {"in": gi, "out": go, "sign_p": round(sign_p(gi, go), 4),
                                              "better": sum(1 for x, y in zip(a, b) if (y or 99) < (x or 99)),
                                              "worse": sum(1 for x, y in zip(a, b) if (y or 99) > (x or 99))}
    res["rows"] = rows                               # ranks only: no query text, no memory names
    res["seconds"] = round(time.time() - t0)
    with open(keep_old(out_path), "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=1)
    return res


def main(argv=None):
    a = list(sys.argv[1:] if argv is None else argv)
    if not a or a[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    kinds = set(a[a.index("--kinds") + 1].split(",")) if "--kinds" in a else None
    res = run(a[0], kinds=kinds)
    print(json.dumps({k: v for k, v in res.items() if k != "rows"}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
