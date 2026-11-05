"""calib_orders_log.py - the orders-channel calibration read from the capture log instead of the transcripts
(27 Sep 19:1x, claude-825b1305). calib_orders_fast.py re-parses every transcript since the start date, gigabytes, which
at BelowNormal behind the film renders did not finish loading in 25 minutes. The capture log (events_touch.jsonl) holds
the same events one JSON line each, time and session first, so a byte filter reads it in a minute.

Two parts, both with calib_orders_fast's replay and serving rule (stability 2, each order once per session in 2 h):
  - his interactive sessions from SINCE (default 26 Sep 17:13, when the capture stopped turning hook feedback into kw
    events - before it, a replay's "his last ask" could be a hook's words), every prompt and every fourth tool call;
  - the labelled corrections: each window from his ask to his correction, every tool call, with his ask's words taken
    from the label (the old capture's kw events in those windows came from hook feedback too, so they are skipped).

    python calib_orders_log.py OUT_JSON [--since "YYYY-MM-DD HH:MM"] [--membrane PATH] [--labels PATH] [--workers N]
"""

import sys
if __name__ == "__main__" and any(arg in ("-h", "--help") for arg in sys.argv[1:]):
    print(__doc__ or "No command-line options.")
    raise SystemExit(0)

import collections
import json
import multiprocessing as mp
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BIN = os.path.join(os.path.expanduser("~"), ".claude", "brain_graph", "bin")
for _d in (BIN, HERE):
    if _d not in sys.path:
        sys.path.insert(0, _d)
import calib_orders_fast as F  # noqa: E402

LOG = os.path.join(os.path.expanduser("~"), ".claude", "brain_graph", "events_touch.jsonl")


def _line_ts(raw):
    if not raw.startswith(b'{"ts": '):
        return None
    c = raw.find(b",", 7)
    try:
        return float(raw[7:c])
    except ValueError:
        return None


def seek_ts(f, target, size):
    """Byte offset of a line near the first one stamped >= target. The capture appends in batches, so the log is in time
    order only to within a batch; callers start an hour or more early."""
    lo, hi = 0, size
    while hi - lo > 1 << 16:
        mid = (lo + hi) // 2
        f.seek(mid)
        f.readline()
        ts = None
        for _ in range(50):
            raw = f.readline()
            if not raw:
                break
            ts = _line_ts(raw)
            if ts is not None:
                break
        if ts is None or ts >= target:
            hi = mid
        else:
            lo = mid
    return lo


def scan_log(want, spans):
    """want(session, ts) -> bool, over the byte ranges that cover spans [(t_from, t_to)] (the log is in time order to
    within a capture batch, so each range starts three hours early and runs three hours late); returns
    {session: [(ts, kind, id)]} sorted, each event once. Lines are filtered on the bytes of their time and session
    before any JSON is parsed."""
    size = os.path.getsize(LOG)
    ranges = []
    with open(LOG, "rb") as f:
        for a, b in spans:
            lo = seek_ts(f, a - 3 * 3600, size)
            hi = size if b >= 1e11 else min(size, seek_ts(f, b + 3 * 3600, size) + (1 << 16))
            ranges.append([lo, hi])
    if os.environ.get("CALIB_FULL_SCAN", "1") == "1":   # the log is in time order only to within a capture batch,
        ranges = [[0, size]]                            # and late batches carry old times: read all of it
    ranges.sort()
    merged = []
    for lo, hi in ranges:
        if merged and lo <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], hi)
        else:
            merged.append([lo, hi])
    by = collections.defaultdict(list)
    seen = set()
    with open(LOG, "rb") as f:
        for lo, hi in merged:
            f.seek(lo)
            if lo:
                f.readline()
            while f.tell() < hi:
                raw = f.readline()
                if not raw:
                    break
                ts = _line_ts(raw)
                if ts is None:
                    continue
                k = raw.find(b'"session": "')
                if k < 0:
                    continue
                s = raw[k + 12:k + 20].decode("ascii", "replace")
                if not want(s, ts):
                    continue
                if raw in seen:
                    continue
                seen.add(raw)
                try:
                    e = json.loads(raw)
                except Exception:
                    continue
                by[s].append((ts, e.get("kind"), e.get("id")))
    for s in by:
        by[s].sort()
    return by, merged


def orders_on_disk():
    """study_standing_orders.orders() without its git pass (a walk of the whole brain repo history that took more than
    ten minutes on the busy disk): an order counts from its own created_at time, which is when the file existed; else
    from the end of its creation day; else from its modification time."""
    import datetime
    import glob
    import re
    import study_standing_orders as SSO
    out = {}
    for p in glob.glob(os.path.join(SSO.MEM, "*.md")):
        slug = os.path.basename(p)[:-3].lower()
        try:
            head = open(p, encoding="utf-8", errors="replace").read(4000)
        except OSError:
            continue
        if not head.startswith("---"):
            continue
        head = head.split("\n---", 1)[0]
        f = {k: v.strip().strip('"') for k, v in SSO.FIELD.findall(head)}
        if not (slug.startswith("owner_") or "owner-stated" in f.get("status", "").lower()):
            continue
        avail = None
        ca = f.get("created_at", "")
        if re.match(r"\d{4}-\d\d-\d\dT\d\d:\d\d", ca):
            try:
                avail = datetime.datetime.fromisoformat(ca.strip("'")).timestamp()
            except ValueError:
                avail = None
        if avail is None:
            day = (f.get("created") or "")[:10]
            m = SSO.SLUG_DAY.search(slug)
            if not day and m:
                day = "%s-%s-%s" % m.groups()
            try:
                avail = datetime.datetime.strptime(day, "%Y-%m-%d").timestamp() + 86399.0
            except ValueError:
                avail = os.path.getmtime(p)
        sup = f.get("superseded_by", "")
        sup = sup.split()[0].strip("[]'\",").lower() if sup else ""
        out[slug] = {"avail": avail, "superseded_by": sup}
    return out


def replay_ask(evs, t_lo, t_hi, every, ask_words):
    """calib_orders_fast.replay with his ask fixed from the label and kw events skipped."""
    pol = F.G["POL"]
    W, K = pol["window_s"], pol["k_items"]
    out, run, n_tool = [], [None, 0], 0
    for i, (ts, kd, nid) in enumerate(evs):
        if not (t_lo <= ts < t_hi) or kd != "tool":
            continue
        n_tool += 1
        if n_tool % every:
            continue
        items, got, j = [], set(), i
        while j >= 0:
            ts2, kd2, nid2 = evs[j]
            if ts - ts2 > W or len(items) >= K:
                break
            if kd2 != "kw":
                nm = "%s:%s" % (kd2, nid2)
                if nm not in got:
                    got.add(nm)
                    items.append((nm, ts - ts2))
            j -= 1
        oc = F.cands(F.G["LM"].order_query(items, ask_words), ts)
        lead = oc[0][0] if oc else None
        run = [lead, run[1] + 1 if lead and run[0] == lead else (1 if lead else 0)]
        out.append((ts, lead, oc[0][1] if oc else 0.0, run[1]))
    return out


def work(job):
    key, evs, t_lo, t_hi, every, ask = job
    t0 = time.time()
    rows = replay_ask(evs, t_lo, t_hi, every, ask) if ask is not None else F.replay(evs, t_lo, t_hi, every)
    return key, rows, round(time.time() - t0, 1), len(F.G["CACHE"])


def main():
    out_path = sys.argv[1]
    since = F._arg("--since", "2026-09-26 17:13")
    memb = F._arg("--membrane", os.path.join(BIN, "live_membrane.py"))
    labels = F._arg("--labels", os.path.join(F.REPO_RESULTS, "STANDING_ORDERS_LABELS_2026-09-27.json"))
    workers = int(F._arg("--workers", "6"))
    F._polite()
    import session_kinds
    import study_standing_orders as SSO
    import extract_touch_events as X
    t0 = time.time()
    ords = SSO.orders() if "--git-dates" in sys.argv else orders_on_disk()
    T = time.mktime(time.strptime(since, "%Y-%m-%d %H:%M"))
    lab = json.load(open(labels, encoding="utf-8"))["preventable"]
    wins = collections.defaultdict(list)
    for c in lab:
        wins[c["sid"]].append(((c["ask_t"] or c["t"]) - 700.0, c["t"]))
    kinds = {}

    def want(s, ts):
        if s in wins and any(a <= ts <= b for a, b in wins[s]):
            return True
        if ts < T:
            return False
        if s not in kinds:
            kinds[s] = session_kinds.kind(s)
        return kinds[s] == "his"
    spans = [(T, 1e12)] + [w for ws in wins.values() for w in ws]
    by, merged = scan_log(want, spans)
    his = {s: v for s, v in by.items() if kinds.get(s) == "his" and sum(1 for e in v if e[0] >= T) >= 20}
    print("orders %d; read %.0f MB of the log in %d ranges; his sessions since %s: %d (%d events); correction sessions %d"
          " (%.0f s)" % (len(ords), sum(b - a for a, b in merged) / 1e6, len(merged), since, len(his),
                         sum(len(v) for v in his.values()), len({c["sid"] for c in lab} & set(by)), time.time() - t0),
          flush=True)
    jobs = [(("his", s), his[s], T, 1e12, 4, None) for s in sorted(his, key=lambda s: -len(his[s]))]
    for i, c in enumerate(lab):
        ask = [w.lower() for w in X.words(c.get("ask") or "", cap=40)]
        jobs.append((("prev", i), by.get(c["sid"], []), (c["ask_t"] or c["t"]) - 5.0, c["t"] - 1.0, 1, ask))
    all_rows, prev_rows, done = {}, {}, 0
    with mp.Pool(workers, initializer=F._init, initargs=(memb, ords)) as pool:
        for key, rows, secs, ncache in pool.imap_unordered(work, jobs):
            done += 1
            (all_rows if key[0] == "his" else prev_rows)[key[1]] = rows
            if key[0] == "his" or done == len(jobs):
                print("  %d/%d %s %s: %d evaluations in %.0f s [%.0f s]" % (
                    done, len(jobs), key[0], key[1], len(rows), secs, time.time() - t0), flush=True)
    span_h = sum((r[-1][0] - r[0][0]) / 3600.0 for r in all_rows.values() if r)
    act_h = float(len({(s, int(t // 3600)) for s, rows in all_rows.items() for t, *_ in rows}))
    n_eval = sum(len(r) for r in all_rows.values())
    res = {"source": "capture log", "since": since, "membrane": memb, "sessions": len(all_rows), "evaluations": n_eval,
           "span_hours": round(span_h, 1), "active_hours": act_h, "bars": {}}
    print("replayed %d sessions: %d evaluations, %.0f active session-hours (%.0f wall-clock)" % (
        len(all_rows), n_eval, act_h, span_h), flush=True)
    for bar in F.BARS:
        lines = [(s, t, sl, sc) for s, rows in all_rows.items() for t, sl, sc in F.served_at(rows, bar)]
        per_slug = collections.Counter(sl for _s, _t, sl, _sc in lines)
        res["bars"][str(bar)] = {"lines": len(lines), "per_active_hour": round(len(lines) / max(1.0, act_h), 3),
                                 "per_span_hour": round(len(lines) / max(1e-9, span_h), 3),
                                 "sessions_with_a_line": len({s for s, *_ in lines}),
                                 "top_orders": per_slug.most_common(15),
                                 "lines": [[s, round(t), sl, sc] for s, t, sl, sc in lines]}
        print("bar %4.0f: %4d lines, %.2f per active session-hour (%.2f per wall-clock hour), %d sessions; top: %s" % (
            bar, len(lines), len(lines) / max(1.0, act_h), len(lines) / max(1e-9, span_h), len({s for s, *_ in lines}),
            ", ".join("%s x%d" % (sl[:44], n) for sl, n in per_slug.most_common(5))), flush=True)
    prev = []
    for i, c in enumerate(lab):
        rows = prev_rows.get(i, [])
        hits = {}
        for bar in F.BARS:
            got = F.served_at(rows, bar)
            hits[str(bar)] = {"served": [sl for _t, sl, _sc in got],
                              "right": any(sl in c["orders"] for _t, sl, _sc in got)}
        prev.append({"n": c["n"], "theme": c["theme"], "sid": c["sid"], "moments": len(rows), "hits": hits,
                     "leaders": collections.Counter(r[1] for r in rows).most_common(4)})
    res["preventable"] = prev
    for bar in F.BARS:
        k = str(bar)
        print("bar %4.0f: the restated order would have been served before his correction in %d of %d (%d had moments)" % (
            bar, sum(1 for p in prev if p["hits"][k]["right"]), len(prev), sum(1 for p in prev if p["moments"])),
            flush=True)
    res["seconds"] = round(time.time() - t0)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=1)


if __name__ == "__main__":
    main()
