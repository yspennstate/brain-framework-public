"""study_sequence_model.py - can the ORDER in which things were used predict the next new thing? (26 Sep, claude-825b1305)

His voice note: edges grow along the sequences things are used in. The live graph learns windowed co-occurrence (PMI
over a ten-minute context); this learns order literally. From every session before the cut, the sequence of distinct
specific items (folders, projects, files, memories read, errors, hosts, repos; the folders every agent visits and the
index files left out), and for each item the NEW items that came after it within 30 minutes: count(a -> b), once per
session. At a moment of a later session, the last three distinct items vote for what comes next, items the session
already touched are excluded (only a new item is worth naming), and the top candidate is judged right when the session
touches it within 30 minutes. Scored AS SERVED - one line per (session, item), the first moment it clears the bar - on
the interactive sessions of the same replay window as the membrane study, against a popularity baseline (the most
common next-new item overall) and against order-free co-occurrence (the same counts without direction).

    python study_sequence_model.py OUT_JSON [--fast]     (--fast: the same moments and verdicts in O(n log n))
"""
import collections
import glob
import json
import math
import os
import re
import sys
import time

HOME = os.path.join(os.path.expanduser("~"), ".claude", "brain_graph")
EVENT_FILES = [os.path.join(HOME, f) for f in ("events_history_claude.jsonl", "events_touch.jsonl")]
STUDY = os.path.join(HOME, "study")
PROJ = os.path.join(os.path.expanduser("~"), ".claude", "projects", (os.environ.get("BRAIN_SLUG") or os.path.expanduser("~").replace(":", "-").replace(chr(92), "-").replace("/", "-")))
KINDS = {b'"kind": "dir"': "dir", b'"kind": "proj"': "proj", b'"kind": "file"': "file", b'"kind": "mem"': "mem",
         b'"kind": "err"': "err", b'"kind": "host"': "host", b'"kind": "repo"': "repo"}
TARGET_KINDS = ("dir", "proj", "mem", "file")
TRIVIAL = re.compile(r"^(dir|proj):~/(\.claude/projects(/[^/]+(/memory)?)?|[^/]+|\.claude/[^/]+)$", re.I)
SKIP_MEM = {"memory", "mistakes", "index_archive", "predictions", "state_of_mind", "open_questions", "autobiography",
            "user_model", "curiosity", "conscience", "intentions", "salience"}
HORIZON, CONTEXT_S = 1800.0, 600.0


import os as _ko_os, sys as _ko_sys  # noqa: E402
_ko_sys.path.insert(0, _ko_os.path.join(_ko_os.path.expanduser("~"), ".claude", "brain_graph", "bin"))
from quarantine_rows import keep_old  # noqa: E402  a rerun keeps the previous output (R13 trap 1, 27 Sep): brain data is never deleted


def keep(item):
    if TRIVIAL.match(item):
        return False
    kd, _, i = item.partition(":")
    return not (kd == "mem" and i.lower() in SKIP_MEM)


def load(cut_after=None):
    """{session: [(ts, item)]} of specific items, sorted; only the byte-filtered kinds are parsed."""
    by = collections.defaultdict(list)
    for p in EVENT_FILES:
        if not os.path.exists(p):
            continue
        with open(p, "rb") as fh:
            for raw in fh:
                kind = next((k for b, k in KINDS.items() if b in raw), None)
                if not kind:
                    continue
                try:
                    o = json.loads(raw)
                except ValueError:
                    continue
                item = "%s:%s" % (kind, o["id"])
                if keep(item):
                    by[o["session"]].append((float(o["ts"]), item))
    for s in by:
        by[s].sort()
    return by


def distinct(evs):
    out = []
    for ts, it in evs:
        if not out or out[-1][1] != it:
            out.append((ts, it))
    return out


def train(by, cut):
    """count(a -> b) for b a NEW item of the session within HORIZON after a touch of a; once per session and pair."""
    import bisect
    trans, co, pop = collections.defaultdict(collections.Counter), collections.defaultdict(collections.Counter), \
        collections.Counter()
    for evs in by.values():
        seq = distinct([e for e in evs if e[0] < cut])
        if len(seq) < 3:
            continue
        first = {}
        for ts, it in seq:
            first.setdefault(it, ts)
        news = sorted((ts, it) for it, ts in first.items() if it.split(":", 1)[0] in TARGET_KINDS)
        nts = [x[0] for x in news]
        pairs = set()
        for ts, it in seq:
            j = bisect.bisect_right(nts, ts)
            while j < len(news) and news[j][0] <= ts + HORIZON:
                if news[j][1] != it:
                    pairs.add((it, news[j][1]))
                j += 1
        for a, b in pairs:
            trans[a][b] += 1
            co[a][b] += 1
            co[b][a] += 1
        for it in first:
            if it.split(":", 1)[0] in TARGET_KINDS:
                pop[it] += 1
    return trans, co, pop


def predict(model, totals, ctx, touched):
    sc = collections.Counter()
    for w, a in zip((1.0, 0.5, 0.33), ctx):
        nxt = model.get(a)
        if not nxt:
            continue
        tot = totals[a]
        for b, c in nxt.items():
            if b not in touched and b.split(":", 1)[0] in TARGET_KINDS:
                sc[b] += w * c / tot
    return sc.most_common(1)[0] if sc else (None, 0.0)


def as_served(rows, frac):
    """One line per (session, item): the first moment its score clears the bar that fires on `frac` of moments."""
    xs = sorted((r["score"] for r in rows if r["pred"]), reverse=True)
    if not xs:
        return {}
    bar = xs[max(0, min(len(xs) - 1, int(frac * len(rows)) - 1))]
    seen, lines = set(), []
    for r in sorted(rows, key=lambda r: (r["sid"], r["t"])):
        if not r["pred"] or r["score"] < bar or (r["sid"], r["pred"]) in seen:
            continue
        seen.add((r["sid"], r["pred"]))
        lines.append(r)
    right = [r for r in lines if r["got"]]
    by_kind = collections.Counter(r["pred"].split(":", 1)[0] for r in lines)
    right_kind = collections.Counter(r["pred"].split(":", 1)[0] for r in right)
    return {"bar": round(bar, 4), "lines": len(lines), "right": len(right),
            "distinct_items_right": len({r["pred"] for r in right}), "sessions_right": len({r["sid"] for r in right}),
            "by_kind": {k: "%d/%d" % (right_kind[k], n) for k, n in by_kind.items()}}


class NP:
    """predict() in numpy, exactly: the same sums in the same order, and among equal best scores the item met first
    (Counter.most_common(1) is max() over insertion order). Item ids are global and made on first use; the touched
    flags are the current session's, kept in step with its live touched set."""

    def __init__(self, model, totals):
        import numpy as np
        self.np, self.model, self.totals, self.arr = np, model, totals, {}
        self.ids, self.items, self.cap = {}, [], 1 << 16
        self.target, self.tflag = np.zeros(self.cap, bool), np.zeros(self.cap, bool)
        self.touched = set()

    def idx(self, it):
        k = self.ids.get(it)
        if k is None:
            k = self.ids[it] = len(self.items)
            self.items.append(it)
            if k >= self.cap:
                np = self.np
                self.cap *= 2
                self.target = np.concatenate([self.target, np.zeros(self.cap - len(self.target), bool)])
                self.tflag = np.concatenate([self.tflag, np.zeros(self.cap - len(self.tflag), bool)])
            self.target[k] = it.split(":", 1)[0] in TARGET_KINDS
            self.tflag[k] = it in self.touched
        return k

    def begin_session(self, touched):
        self.touched = touched
        self.tflag[:] = False
        for t in touched:
            k = self.ids.get(t)
            if k is not None:
                self.tflag[k] = True

    def touch(self, it):
        k = self.ids.get(it)
        if k is not None:
            self.tflag[k] = True

    def succ(self, a):
        if a not in self.arr:
            nxt = self.model.get(a)
            if not nxt:
                self.arr[a] = None
            else:
                np = self.np
                self.arr[a] = (np.fromiter((self.idx(b) for b in nxt), dtype=np.int64, count=len(nxt)),
                               np.fromiter(nxt.values(), dtype=np.float64, count=len(nxt)), self.totals[a])
        return self.arr[a]

    def predict(self, ctx):
        np = self.np
        parts = [(w, self.succ(a)) for w, a in zip((1.0, 0.5, 0.33), ctx)]
        parts = [(w, x) for w, x in parts if x is not None]
        if not parts:
            return None, 0.0
        ids = np.concatenate([x[0] for _, x in parts])
        sc = np.concatenate([(w * x[1]) / x[2] for w, x in parts])
        keep = self.target[ids] & ~self.tflag[ids]
        ids, sc = ids[keep], sc[keep]
        if not len(ids):
            return None, 0.0
        uniq, first, inv = np.unique(ids, return_index=True, return_inverse=True)
        sums = np.zeros(len(uniq))
        np.add.at(sums, inv, sc)
        best = sums.max()
        cand = np.nonzero(sums == best)[0]
        k = cand[np.argmin(first[cand])]
        return self.items[uniq[k]], float(best)


def score_session_np(evs, cut, engines, pop, popular, s8, rows):
    """score_session() with the numpy predictor; the same rows."""
    import bisect
    tss = [e[0] for e in evs]
    pos = collections.defaultdict(list)
    for k, (_, it) in enumerate(evs):
        pos[it].append(k)
    touched = {it for ts, it in evs if ts < cut}
    for eng in engines.values():
        eng.begin_session(touched)
    recent, cache = [], {}
    for i, (ts, it) in enumerate(evs):
        if ts < cut:
            continue
        touched.add(it)
        for eng in engines.values():
            eng.touch(it)
        if not recent or recent[-1][1] != it:
            recent.append((ts, it))
        ctx = [x[1] for x in reversed(recent) if ts - x[0] <= CONTEXT_S][:3]
        j = bisect.bisect_right(tss, ts + HORIZON)

        def got(pred):
            if not pred:
                return False
            ks = pos.get(pred, ())
            k = bisect.bisect_right(ks, i)
            return k < len(ks) and ks[k] < j

        key = (tuple(ctx), len(touched))
        if key not in cache:
            cache[key] = {name: eng.predict(ctx) for name, eng in engines.items()}
            cache[key]["popularity"] = next((p for p in popular if p not in touched), None)
        for name in engines:
            pred, score = cache[key][name]
            rows[name].append({"sid": s8, "t": ts, "pred": pred, "score": score, "got": got(pred)})
        pp = cache[key]["popularity"]
        rows["popularity"].append({"sid": s8, "t": ts, "pred": pp, "score": float(pop[pp]) if pp else 0.0,
                                   "got": got(pp)})


def score_session(evs, cut, trans, tot_t, co, tot_c, pop, popular, s8, rows):
    """The moments of one session after the cut, exactly as main() scored them, in O(n log n): the future window
    by bisect over the session's times, a prediction cached while the context and the touched set stay the same
    (touched only grows, so its size names it), and 'got' by bisect over the predicted item's own positions."""
    import bisect
    tss = [e[0] for e in evs]
    pos = collections.defaultdict(list)
    for k, (_, it) in enumerate(evs):
        pos[it].append(k)
    touched = {it for ts, it in evs if ts < cut}
    recent, cache = [], {}
    for i, (ts, it) in enumerate(evs):
        if ts < cut:
            continue
        touched.add(it)
        if not recent or recent[-1][1] != it:
            recent.append((ts, it))
        ctx = [x[1] for x in reversed(recent) if ts - x[0] <= CONTEXT_S][:3]
        j = bisect.bisect_right(tss, ts + HORIZON)

        def got(pred):
            if not pred:
                return False
            ks = pos.get(pred, ())
            k = bisect.bisect_right(ks, i)
            return k < len(ks) and ks[k] < j

        key = (tuple(ctx), len(touched))
        if key not in cache:
            cache[key] = {name: predict(model, tots, ctx, touched)
                          for name, (model, tots) in (("sequence", (trans, tot_t)), ("cooccurrence", (co, tot_c)))}
            cache[key]["popularity"] = next((p for p in popular if p not in touched), None)
        for name in ("sequence", "cooccurrence"):
            pred, score = cache[key][name]
            rows[name].append({"sid": s8, "t": ts, "pred": pred, "score": score, "got": got(pred)})
        pp = cache[key]["popularity"]
        rows["popularity"].append({"sid": s8, "t": ts, "pred": pp, "score": float(pop[pp]) if pp else 0.0,
                                   "got": got(pp)})


def served_top(rows, n):
    """The n lines a stricter bar would serve: the n (session, item) pairs with the highest peak score, each judged
    at the first moment its score reaches the bar (the n-th highest peak) - the as-served rule at a matched volume."""
    peak = {}
    for r in rows:
        if r["pred"]:
            k = (r["sid"], r["pred"])
            peak[k] = max(peak.get(k, 0.0), r["score"])
    if not peak:
        return {}
    top = sorted(peak.items(), key=lambda kv: -kv[1])[:n]
    bar = top[-1][1]
    keep = {k for k, _ in top}
    got = {}
    for r in sorted(rows, key=lambda r: (r["sid"], r["t"])):
        k = (r["sid"], r["pred"])
        if k in keep and k not in got and r["score"] >= bar:
            got[k] = r["got"]
    right = sum(1 for v in got.values() if v)
    kinds = collections.Counter(k[1].split(":", 1)[0] for k in keep)
    rk = collections.Counter(k[1].split(":", 1)[0] for k, v in got.items() if v)
    return {"lines": len(got), "right": right, "sessions": len({k[0] for k in keep}),
            "by_kind": {kd: "%d/%d" % (rk[kd], c) for kd, c in kinds.items()}}


def main():
    out_path = sys.argv[1]
    fast = "--fast" in sys.argv
    t0 = time.time()
    cut = json.load(open(os.path.join(STUDY, "cut.json")))["D"]
    rows_file = os.path.join(STUDY, "rows_20260926_2021.jsonl")
    inter = sorted({r["sid"] for r in (json.loads(l) for l in open(rows_file, encoding="utf-8")) if r.get("ep") == "cli"})
    by = load()
    print("loaded %d sessions of specific items (%.0f s)" % (len(by), time.time() - t0), flush=True)
    trans, co, pop = train(by, cut)
    tot_t = {a: sum(v.values()) for a, v in trans.items()}
    tot_c = {a: sum(v.values()) for a, v in co.items()}
    popular = [it for it, _ in pop.most_common(400)]
    print("trained: %d items with successors (%.0f s)" % (len(trans), time.time() - t0), flush=True)
    rows = {"sequence": [], "cooccurrence": [], "popularity": []}
    engines = {"sequence": NP(trans, tot_t), "cooccurrence": NP(co, tot_c)} if fast else None
    for sid in inter:
        s8 = sid[:8].lower()
        evs = by.get(s8) or by.get(sid) or []
        if not evs:
            continue
        if fast:
            score_session_np(evs, cut, engines, pop, popular, s8, rows)
            print("  %s: %d moments (%.0f s)" % (s8, len(rows["sequence"]), time.time() - t0), flush=True)
            continue
        touched = {it for ts, it in evs if ts < cut}
        recent = []
        for i, (ts, it) in enumerate(evs):
            if ts < cut:
                continue
            touched.add(it)
            if not recent or recent[-1][1] != it:
                recent.append((ts, it))
            ctx = [x[1] for x in reversed(recent) if ts - x[0] <= CONTEXT_S][:3]
            future = {x[1] for x in evs[i + 1:] if x[0] <= ts + HORIZON}
            for name, (model, tots) in (("sequence", (trans, tot_t)), ("cooccurrence", (co, tot_c))):
                pred, score = predict(model, tots, ctx, touched)
                rows[name].append({"sid": s8, "t": ts, "pred": pred, "score": score, "got": bool(pred and pred in future)})
            pp = next((p for p in popular if p not in touched), None)
            rows["popularity"].append({"sid": s8, "t": ts, "pred": pp, "score": float(pop[pp]) if pp else 0.0,
                                       "got": bool(pp and pp in future)})
    res = {"built": time.strftime("%Y-%m-%d %H:%M:%S"), "cut": time.strftime("%Y-%m-%d %H:%M", time.localtime(cut)),
           "interactive_sessions": len(inter), "moments": len(rows["sequence"]), "trained_items": len(trans),
           "seconds": None, "models": {}}
    for name, rs in rows.items():
        res["models"][name] = {"base_any_moment": round(sum(r["got"] for r in rs) / max(1, len(rs)), 4),
                               "as_served": {str(f): as_served(rs, f) for f in (0.02, 0.05, 0.1, 0.2, 1.0)},
                               "top_lines": {str(n): served_top(rs, n) for n in (24, 50, 100, 200)}}
        print(name, json.dumps(res["models"][name]), flush=True)
    res["seconds"] = round(time.time() - t0)
    json.dump(res, open(keep_old(out_path), "w", encoding="utf-8"), indent=1)
    print("wrote", out_path)


if __name__ == "__main__":
    main()
