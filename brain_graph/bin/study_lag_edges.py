"""study_lag_edges.py - does weighting a pair by how soon the target followed the source predict the next new thing better
than equal credit? (27 Sep, claude-825b1305; scheduled audit account03/B03 r51, B03-R7-SEQ-LAG-WEIGHT-MISMATCH-001.)

NODE_CLASSES.md describes a directed seq A -> B edge reinforced by exp(-lag/tau); the store gives every predecessor in
its 600 s window equal credit, and the 26 Sep order-of-use study (study_sequence_model.py) tested only equal-credit
counts. This runs that study's split, loader and O(n log n) scorer unchanged and adds models whose pair counts are
exp(-lag/tau), lag the shortest gap between a touch of the source and the target's first touch in the session (once
per session and pair, as the equal-credit counts are), tau 60 s and 300 s, for both the directed and the co-use form.

Registered before any number (brain_graph/study/THREE_DAY_RUN_2026-09-27.md, 23:1x): adopt the lag rule into graph_store
only if a lag model gets at least 25 percent more right lines as served at the 2 percent bar than equal-credit co-use in
the same run, with precision not lower, on at least as many sessions, and does not lose at every moment.

    python study_lag_edges.py OUT_JSON [UNTIL_EPOCH]

UNTIL_EPOCH drops every event stamped after it. The study reads the live event logs, and its 32 test sessions include
sessions still running, so an unbounded rerun scores more moments than the run it is compared with (27 Sep: 142,996
against the committed 124,344). Bounded at the committed run's load time, the equal-credit models must reproduce its
figures (sequence 15/462, co-use 36/497 at the 2 percent bar) before a lag number is read.
"""
import bisect
import collections
import json
import math
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import study_sequence_model as S  # noqa: E402
from quarantine_rows import keep_old  # noqa: E402

TAUS = (60.0, 300.0)


def train_lag(by, cut, taus=TAUS):
    """Equal-credit counts (as S.train) and, per tau, the same pairs weighted exp(-lag/tau)."""
    trans, co, pop = collections.defaultdict(collections.Counter), collections.defaultdict(collections.Counter), \
        collections.Counter()
    lag_t = {t: collections.defaultdict(collections.Counter) for t in taus}
    lag_c = {t: collections.defaultdict(collections.Counter) for t in taus}
    for evs in by.values():
        seq = S.distinct([e for e in evs if e[0] < cut])
        if len(seq) < 3:
            continue
        first = {}
        for ts, it in seq:
            first.setdefault(it, ts)
        news = sorted((ts, it) for it, ts in first.items() if it.split(":", 1)[0] in S.TARGET_KINDS)
        nts = [x[0] for x in news]
        gap = {}
        for ts, it in seq:
            j = bisect.bisect_right(nts, ts)
            while j < len(news) and news[j][0] <= ts + S.HORIZON:
                if news[j][1] != it:
                    k = (it, news[j][1])
                    g = news[j][0] - ts
                    if k not in gap or g < gap[k]:
                        gap[k] = g
                j += 1
        for (a, b), g in gap.items():
            trans[a][b] += 1
            co[a][b] += 1
            co[b][a] += 1
            for t in taus:
                w = math.exp(-g / t)
                lag_t[t][a][b] += w
                lag_c[t][a][b] += w
                lag_c[t][b][a] += w
        for it in first:
            if it.split(":", 1)[0] in S.TARGET_KINDS:
                pop[it] += 1
    return trans, co, pop, lag_t, lag_c


def main():
    out_path = sys.argv[1]
    t0 = time.time()
    cut = json.load(open(os.path.join(S.STUDY, "cut.json")))["D"]
    rows_file = os.path.join(S.STUDY, "rows_20260926_2021.jsonl")
    inter = sorted({r["sid"] for r in (json.loads(l) for l in open(rows_file, encoding="utf-8")) if r.get("ep") == "cli"})
    by = S.load()
    until = float(sys.argv[2]) if len(sys.argv) > 2 else None
    if until is not None:
        by = {s: [e for e in evs if e[0] <= until] for s, evs in by.items()}
        by = {s: evs for s, evs in by.items() if evs}
    print("loaded %d sessions (%.0f s)%s" % (len(by), time.time() - t0,
                                             "" if until is None else ", events up to %s" % time.strftime(
                                                 "%Y-%m-%d %H:%M:%S", time.localtime(until))), flush=True)
    trans, co, pop, lag_t, lag_c = train_lag(by, cut)
    models = {"sequence": trans, "cooccurrence": co}
    for t in TAUS:
        models["sequence_lag%d" % t] = lag_t[t]
        models["cooccurrence_lag%d" % t] = lag_c[t]
    totals = {name: {a: sum(v.values()) for a, v in m.items()} for name, m in models.items()}
    popular = [it for it, _ in pop.most_common(400)]
    print("trained %d source items (%.0f s)" % (len(trans), time.time() - t0), flush=True)
    engines = {name: S.NP(m, totals[name]) for name, m in models.items()}
    rows = {name: [] for name in list(models) + ["popularity"]}
    for sid in inter:
        s8 = sid[:8].lower()
        evs = by.get(s8) or by.get(sid) or []
        if evs:
            S.score_session_np(evs, cut, engines, pop, popular, s8, rows)
            print("  %s: %d moments (%.0f s)" % (s8, len(rows["sequence"]), time.time() - t0), flush=True)
    res = {"built": time.strftime("%Y-%m-%d %H:%M:%S"), "cut": time.strftime("%Y-%m-%d %H:%M", time.localtime(cut)),
           "until": until,
           "interactive_sessions": len(inter), "moments": len(rows["sequence"]), "taus_s": list(TAUS), "models": {}}
    for name, rs in rows.items():
        res["models"][name] = {"base_any_moment": round(sum(r["got"] for r in rs) / max(1, len(rs)), 4),
                               "as_served": {str(f): S.as_served(rs, f) for f in (0.02, 0.05, 0.1, 0.2, 1.0)},
                               "top_lines": {str(n): S.served_top(rs, n) for n in (24, 50, 100)}}
        a2, a1 = res["models"][name]["as_served"]["0.02"], res["models"][name]["as_served"]["1.0"]
        print("%-20s 2%%: %s/%s on %s sessions | every moment: %s/%s" % (
            name, a2.get("right"), a2.get("lines"), a2.get("sessions_right"), a1.get("right"), a1.get("lines")), flush=True)
    res["seconds"] = round(time.time() - t0)
    json.dump(res, open(keep_old(out_path), "w", encoding="utf-8"), indent=1)
    print("wrote", out_path)


if __name__ == "__main__":
    main()
