"""calib_orders_fast.py - calibrate the membrane's standing-orders channel on his real sessions. The replay of calib_orders.py - every prompt and every fourth tool call of his interactive sessions
from SINCE, his last ask kept, the leader counted across evaluations, each order served once per session in two hours -
with three changes:
  - sessions run in parallel worker processes (BelowNormal, off cores 0 and 1), and a query already scored in a worker
    is not scored again (the serial run needed about two minutes per session: his sessions stay open for days);
  - an order counts at a moment only as the order in force then: a hit hands over along its superseded_by chain only to
    successors that existed at that moment (study_standing_orders.resolve), and an order that did not exist yet is out;
  - volume is reported per ACTIVE session-hour (an hour in which the session was evaluated at least once) as well as
    per wall-clock span, since a session open overnight adds hours with nothing in them.
The preventable corrections (results/STANDING_ORDERS_LABELS_2026-09-27.json) are replayed at every tool call between
his ask and his correction.

    python calib_orders_fast.py SINCE_YYYY-MM-DD OUT_JSON [--membrane PATH] [--workers N] [--labels PATH]
"""
if "-h" in __import__("sys").argv[1:] or "--help" in __import__("sys").argv[1:]:
    print((__doc__ or "").strip() or "usage: python %s" % __import__("os").path.basename(__file__))
    raise SystemExit(0)
import collections
import importlib.util
import json
import multiprocessing as mp
import os
import pickle
import sys
import time

BIN = os.path.join(os.path.expanduser("~"), ".claude", "brain_graph", "bin")
HERE = os.path.dirname(os.path.abspath(__file__))
REPO_RESULTS = os.environ.get("BRAIN_RESULTS_DIR") or os.path.join(HERE, "..", "results")
BARS = (20.0, 25.0, 30.0, 35.0, 40.0, 50.0, 60.0)
G = {}


def _arg(name, default):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


def _polite():
    try:
        import psutil
        p = psutil.Process()
        # BelowNormal
        # under the Normal-priority load gets nothing - measured again 27 Sep 19:2x, 0 MB/s and 0 pct CPU for minutes
        p.nice(psutil.NORMAL_PRIORITY_CLASS if os.environ.get("CALIB_PRIORITY") == "normal"
               else psutil.BELOW_NORMAL_PRIORITY_CLASS)
        n = os.cpu_count() or 4
        if n > 4:
            p.cpu_affinity(list(range(2, n)))
    except Exception:
        pass


def _load_membrane(path):
    for d in (BIN, HERE):
        if d not in sys.path:
            sys.path.insert(0, d)
    spec = importlib.util.spec_from_file_location("live_membrane", path)
    m = importlib.util.module_from_spec(spec)
    sys.modules["live_membrane"] = m
    spec.loader.exec_module(m)
    return m


def _init(memb, ords):
    _polite()
    G["LM"] = _load_membrane(memb)
    G["LV"] = G["LM"].Live(delta=None)
    G["POL"] = G["LM"].policy()
    G["ORDS"] = ords
    G["CACHE"] = {}


def _resolve(slug, t, ords):
    seen = set()
    while slug in ords and ords[slug]["superseded_by"] and slug not in seen:
        seen.add(slug)
        nxt = ords[slug]["superseded_by"]
        if nxt in ords and ords[nxt]["avail"] <= t:
            slug = nxt
        else:
            break
    return slug


def cands(query, ts):
    oc = G["CACHE"].get(query)
    if oc is None:
        oc = G["LM"].order_candidates(query, set(), G["LV"].db, topn=12, topk=800)
        G["CACHE"][query] = oc
    ords, out, taken = G["ORDS"], [], set()
    for c in oc:
        orig = c.get("via") or c["slug"]
        if orig not in ords:
            continue
        sl = _resolve(orig, ts, ords)
        if sl not in ords or ords[sl]["avail"] > ts or sl in taken:
            continue
        taken.add(sl)
        out.append((sl, c["score"]))
    return out


def replay(evs, t_lo, t_hi, every):
    """[(t, leader, score, run length)] at the hook's moments of one session."""
    pol = G["POL"]
    W, K = pol["window_s"], pol["k_items"]
    out, st_ask, run, n_tool, last_kw = [], [], [None, 0], 0, None
    for i, (ts, kd, nid) in enumerate(evs):
        if not (t_lo <= ts < t_hi):
            continue
        moment = False
        if kd == "tool":
            n_tool += 1
            moment = n_tool % every == 0
        elif kd == "kw" and ts != last_kw:
            last_kw = ts
            st_ask = [n2.lower() for t2, k2, n2 in evs[i:i + 60] if t2 == ts and k2 == "kw"][:40]
            moment = True
        if not moment:
            continue
        items, got, j = [], set(), i
        while j >= 0:
            ts2, kd2, nid2 = evs[j]
            if ts - ts2 > W or len(items) >= K:
                break
            nm = "%s:%s" % (kd2, nid2)
            if nm not in got:
                got.add(nm)
                items.append((nm, ts - ts2))
            j -= 1
        oc = cands(G["LM"].order_query(items, st_ask), ts)
        lead = oc[0][0] if oc else None
        run = [lead, run[1] + 1 if lead and run[0] == lead else (1 if lead else 0)]
        out.append((ts, lead, oc[0][1] if oc else 0.0, run[1]))
    return out


def work(job):
    key, evs, t_lo, t_hi, every = job
    t0 = time.time()
    rows = replay(evs, t_lo, t_hi, every)
    return key, rows, round(time.time() - t0, 1), len(G["CACHE"])


def served_at(rows, bar, stable=2, refractory=7200.0):
    last, got = {}, []
    for t, lead, sc, rl in rows:
        if lead and sc >= bar and rl >= stable and t - last.get(lead, -1e18) > refractory:
            last[lead] = t
            got.append((t, lead, sc))
    return got


def load_sessions(T, only=None, tag="", windows=None):
    """His sessions' events from T (cached in a pickle beside this script for reruns)."""
    import session_kinds
    import study_live_membrane as SLM
    pk = os.path.join(HERE, "calib_sessions_%d%s.pkl" % (int(T), tag))
    if os.path.exists(pk):
        with open(pk, "rb") as fh:
            return pickle.load(fh)
    if only is None:
        by = SLM.load_recaptured(T)
    else:                                   # only these sessions' own transcript files (a scan of every file since
        import glob                         # 1 Sep reads thousands of transcripts to keep twenty)
        import extract_touch_events as X
        by = collections.defaultdict(list)
        stats = collections.Counter()

        def emit(ts, s, kind, nid):
            if ts >= T and s in only:
                by[s].append((ts, kind, nid))
        root = os.path.join(os.path.expanduser("~"), ".claude", "projects")
        for p in glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True):
            if os.sep + "memory" + os.sep in p or not any(s in p for s in only):
                continue
            if windows and not any(s in p and os.path.getmtime(p) >= a and os.path.getctime(p) <= b
                                   for s, a, b in windows):
                continue                    # written only before the window or created after it (ctime = creation)
            try:
                X.scan_file(p, 0, emit, stats)
            except Exception:
                pass
        for s in by:
            by[s].sort()
    if only is None:
        by = {s: v for s, v in by.items() if session_kinds.kind(s) == "his" and len(v) >= 20}
    else:
        by = dict(by)
    with open(pk, "wb") as fh:
        pickle.dump(by, fh, protocol=pickle.HIGHEST_PROTOCOL)
    return by


def main():
    since, out_path = sys.argv[1], sys.argv[2]
    memb = _arg("--membrane", os.path.join(BIN, "live_membrane.py"))
    workers = int(_arg("--workers", "6"))
    labels = _arg("--labels", os.path.join(REPO_RESULTS, "STANDING_ORDERS_LABELS_2026-09-27.json"))
    _polite()
    for d in (BIN, HERE):
        if d not in sys.path:
            sys.path.insert(0, d)
    import study_standing_orders as SSO
    t0 = time.time()
    ords = SSO.orders()
    T = time.mktime(time.strptime(since, "%Y-%m-%d"))
    his = load_sessions(T)
    lab = json.load(open(labels, encoding="utf-8"))["preventable"]
    T2 = min(c["ask_t"] or c["t"] for c in lab) - 3600.0
    prev_by = load_sessions(T2, only={c["sid"] for c in lab}, tag="_prev",
                            windows=[(c["sid"], (c["ask_t"] or c["t"]) - 700.0, c["t"]) for c in lab])
    print("orders %d; his sessions since %s: %d; preventable sessions %d (%.0f s)" % (
        len(ords), since, len(his), len(prev_by), time.time() - t0), flush=True)
    jobs = [(("his", s), his[s], T, 1e12, 4) for s in sorted(his, key=lambda s: -len(his[s]))]
    jobs += [(("prev", i), prev_by.get(c["sid"], []), (c["ask_t"] or c["t"]) - 5.0, c["t"] - 1.0, 1)
             for i, c in enumerate(lab)]
    all_rows, prev_rows, done = {}, {}, 0
    with mp.Pool(workers, initializer=_init, initargs=(memb, ords)) as pool:
        for key, rows, secs, ncache in pool.imap_unordered(work, jobs):
            done += 1
            if key[0] == "his":
                all_rows[key[1]] = rows
            else:
                prev_rows[key[1]] = rows
            if key[0] == "his" or done == len(jobs):
                print("  %d/%d %s %s: %d evaluations in %.0f s (cache %d) [%.0f s]" % (
                    done, len(jobs), key[0], key[1], len(rows), secs, ncache, time.time() - t0), flush=True)
    span_h = sum((r[-1][0] - r[0][0]) / 3600.0 for r in all_rows.values() if r)
    active = {(s, int(t // 3600)) for s, rows in all_rows.items() for t, *_ in rows}
    act_h = float(len(active))
    n_eval = sum(len(r) for r in all_rows.values())
    res = {"since": since, "membrane": memb, "sessions": len(all_rows), "evaluations": n_eval,
           "span_hours": round(span_h, 1), "active_hours": act_h, "bars": {}}
    print("replayed %d sessions: %d evaluations, %.0f active session-hours (%.0f wall-clock)" % (
        len(all_rows), n_eval, act_h, span_h), flush=True)
    for bar in BARS:
        lines = [(s, t, sl, sc) for s, rows in all_rows.items() for t, sl, sc in served_at(rows, bar)]
        per_slug = collections.Counter(sl for _s, _t, sl, _sc in lines)
        res["bars"][str(bar)] = {"lines": len(lines), "per_active_hour": round(len(lines) / max(1.0, act_h), 3),
                                 "per_span_hour": round(len(lines) / max(1e-9, span_h), 3),
                                 "sessions_with_a_line": len({s for s, *_ in lines}),
                                 "top_orders": per_slug.most_common(15),
                                 "lines_sample": [[s, round(t), sl, sc] for s, t, sl, sc in lines[:60]]}
        print("bar %4.0f: %4d lines, %.2f per active session-hour (%.2f per wall-clock hour), %d sessions; top: %s" % (
            bar, len(lines), len(lines) / max(1.0, act_h), len(lines) / max(1e-9, span_h), len({s for s, *_ in lines}),
            ", ".join("%s x%d" % (sl[:44], n) for sl, n in per_slug.most_common(5))), flush=True)
    prev = []
    for i, c in enumerate(lab):
        rows = prev_rows.get(i, [])
        hits = {}
        for bar in BARS:
            got = served_at(rows, bar)
            hits[str(bar)] = {"served": [sl for _t, sl, _sc in got],
                              "right": any(sl in c["orders"] for _t, sl, _sc in got)}
        prev.append({"n": c["n"], "theme": c["theme"], "moments": len(rows), "hits": hits,
                     "leaders": collections.Counter(r[1] for r in rows).most_common(4)})
    res["preventable"] = prev
    for bar in BARS:
        k = str(bar)
        print("bar %4.0f: the restated order would have been served before his correction in %d of %d" % (
            bar, sum(1 for p in prev if p["hits"][k]["right"]), len(prev)), flush=True)
    res["seconds"] = round(time.time() - t0)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=1)


if __name__ == "__main__":
    main()
