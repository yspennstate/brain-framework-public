"""study_live_membrane.py - the live membrane scored walk-forward on real sessions (26 Sep, claude-825b1305).

A cut D (default three days ago). The store and the sidecar are built only from what happened before D (bank work left
out as in every build, memories last written after D left out); then every session after D is replayed from the
capture's own events. At each prompt and every fourth tool call the membrane evaluates the moment exactly as the hook
does: the distinct items of the ten minutes before it, newest first, weighted 0.5 ** (age / 150 s). What counts as the
membrane being right, per channel:

  memory   the session reads that memory within 30 minutes (a read of a memory the same session wrote is not a recall)
  error    the session hits that error class within 30 minutes
  place    the session first touches that folder or project within 30 minutes
Channels compared on the top line: fused (what the hook serves), hebb alone (learned edges), reach alone (memory links
and the memories whose text names what is active), size alone (the most used memory not yet seen: popularity).

Writes results/LIVE_MEMBRANE_STUDY_<date>.json in the brain_graph folder of the repository and, with --write-policy,
the thresholds that reach the target precision into live/policy.json (only raising the bar the hook already has).

    python study_live_membrane.py [--days 3] [--sessions 120] [--per-session 60] [--write-policy]
"""
import argparse
import collections
import glob
import json
import math
import os
import random
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import live_membrane as LM  # noqa: E402

HOME = os.path.join(os.path.expanduser("~"), ".claude", "brain_graph")
EVENTS = os.path.join(HOME, "events_touch.jsonl")
STUDY = os.path.join(HOME, "study")
REPO_RESULTS = os.path.join(os.path.expanduser("~"), "brain-repo", "12_cognitive_architecture",
                            "brain_graph", "results")
HORIZON = 1800.0


import os as _ko_os, sys as _ko_sys  # noqa: E402
_ko_sys.path.insert(0, _ko_os.path.join(_ko_os.path.expanduser("~"), ".claude", "brain_graph", "bin"))
from quarantine_rows import keep_old  # noqa: E402  a rerun keeps the previous output (R13 trap 1, 27 Sep): brain data is never deleted


def load_after(t_from):
    """Events with ts >= t_from, grouped by session and sorted; a cheap ts read before any json parse."""
    by = collections.defaultdict(list)
    with open(EVENTS, "rb") as fh:
        for raw in fh:
            try:
                i = raw.index(b",")
                ts = float(raw[7:i])
            except (ValueError, IndexError):
                continue
            if ts < t_from:
                continue
            try:
                o = json.loads(raw)
            except ValueError:
                continue
            by[o["session"]].append((o["ts"], o["kind"], o["id"]))
    for s in by:
        by[s].sort()
    return by


def load_recaptured(t_from, only=None):
    """The replay window read again from the transcripts with the capture as it is now (26 Sep 17:13: only prompts he
    typed give kw and verdict events; hook feedback and notifications no longer do), so the replay sees what the live
    hook sees."""
    import glob
    import extract_touch_events as X
    by = collections.defaultdict(list)
    stats = collections.Counter()

    def emit(ts, s, kind, nid):
        if ts >= t_from and (only is None or s in only):
            by[s].append((ts, kind, nid))
    for p in glob.glob(os.path.join(os.path.expanduser("~"), ".claude", "projects", "**", "*.jsonl"), recursive=True):
        if os.sep + "memory" + os.sep in p or os.path.getmtime(p) < t_from:
            continue
        try:
            X.scan_file(p, 0, emit, stats)
        except Exception:
            pass
    for s in by:
        by[s].sort()
    return by


def replace_verdicts(by, path, t_from):
    """His reactions from the re-derived file instead of the capture's old verdict events."""
    for s in by:
        by[s] = [e for e in by[s] if e[1] != "verdict"]
    with open(path, encoding="utf-8") as f:
        for ln in f:
            e = json.loads(ln)
            if e["ts"] >= t_from and e["session"] in by:
                by[e["session"]].append((e["ts"], "verdict", e["id"]))
    for s in by:
        by[s] = sorted(set(by[s]))


def ensure_cut(D, rebuild=False, extra=(), tag="", half_life=None, verdict_file=None):
    import graph_store
    import live_sidecar
    store = os.path.join(STUDY, "store_wf" + tag)
    side = os.path.join(STUDY, "sidecar_wf%s.sqlite" % tag)
    stamp = os.path.join(STUDY, "cut%s.json" % tag)
    try:
        old = json.load(open(stamp, encoding="utf-8"))
    except Exception:
        old = {}
    if rebuild or abs(old.get("D", 0) - D) > 3600 or not os.path.exists(side):
        os.makedirs(STUDY, exist_ok=True)
        t0 = time.time()
        files = [EVENTS] + [f if os.path.isabs(f) else os.path.join(HOME, f) for f in extra]
        graph_store.build(files if len(files) > 1 else EVENTS, store, until=D, exclude_rx=graph_store.BANK_RX,
                          half_life_days=half_life, verdict_file=verdict_file)
        live_sidecar.build(store, out=side)         # every memory file; each moment sees only those written before it
        json.dump({"D": D, "built": time.time(), "seconds": round(time.time() - t0)}, open(stamp, "w"))
    else:
        D = old["D"]
    return D, side


def outcomes(evs, i, t):
    """What the session did in (t, t + HORIZON]: memories read (not its own), errors, first-touched places."""
    wrote, before_places = set(), set()
    for ts, kd, nid in evs[:i]:
        if kd == "memw":
            wrote.add(nid.lower())
        elif kd in ("dir", "proj"):
            before_places.add("%s:%s" % (kd, nid))
    read, errs, places = [], set(), set()
    for ts, kd, nid in evs[i:]:
        if ts <= t:
            continue
        if ts > t + HORIZON:
            break
        if kd == "verdict":
            errs.add("verdict:" + nid)                  # his correction or repeat: the mistake that matters
        if kd == "memw":
            wrote.add(nid.lower())
        elif kd == "mem" and nid.lower() not in wrote and nid.lower() not in read:
            read.append(nid.lower())
        elif kd == "err":
            errs.add("err:" + nid)
        elif kd in ("dir", "proj"):
            key = "%s:%s" % (kd, nid)
            if key not in before_places:
                places.add(key)
    return read, errs, places


def curve(rows, key, got_key, fracs=(0.02, 0.05, 0.1, 0.2, 0.3, 0.5, 1.0)):
    xs = sorted(((r[key], r[got_key]) for r in rows if r.get(key) is not None), key=lambda x: -x[0])
    out = []
    n = len(rows)
    for f in fracs:
        m = max(1, int(round(f * n)))
        top = xs[:m]
        if not top:
            continue
        out.append({"fire_rate": f, "threshold": round(top[-1][0], 3), "fired": len(top),
                    "precision": round(sum(1 for _v, g in top if g) / len(top), 4)})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=3.0)
    ap.add_argument("--sessions", type=int, default=120)
    ap.add_argument("--per-session", type=int, default=60)
    ap.add_argument("--every", type=int, default=4)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--cut", type=float, default=None, help="the cut as an epoch (a variant compared with another run)")
    ap.add_argument("--extra", action="append", default=[], help="another events file in the store (repeatable)")
    ap.add_argument("--tag", default="", help="suffix for this variant's store, sidecar and result names")
    ap.add_argument("--half-life", type=float, default=None, help="edge half-life in days (graph_store.build)")
    ap.add_argument("--sidecar", default=None, help="replay against this prebuilt sidecar (a hybrid) instead of building")
    ap.add_argument("--verdict-file", default=None, help="his reactions from this file, in the store and the replay")
    ap.add_argument("--recapture", action="store_true", help="read the replay window from the transcripts again")
    ap.add_argument("--sessions-from", default=None, help="replay the sessions of this rows file (a paired comparison)")
    ap.add_argument("--lexical", action="store_true", help="also score his prompts with BM25, alone and with the graph")
    ap.add_argument("--lex-boost", type=float, default=0.5, help="BM25 x (1 + boost x node size) in the combined channel")
    ap.add_argument("--hub", action="store_true",
                    help="also score node size discounted by breadth: size - lam x ln(1 + in-degree / median), several lam")
    ap.add_argument("--write-policy", action="store_true")
    ap.add_argument("--target-precision", type=float, default=0.25)
    a = ap.parse_args()
    t_start = time.time()
    D = time.time() - a.days * 86400
    if a.cut:
        D = a.cut
    if a.sidecar:
        side = a.sidecar
    else:
        D, side = ensure_cut(D, a.rebuild, extra=a.extra, tag=a.tag, half_life=a.half_life, verdict_file=a.verdict_file)
    print("cut %s; sidecar %s (%.0f s)" % (time.strftime("%Y-%m-%d %H:%M", time.localtime(D)), side,
                                         time.time() - t_start), flush=True)
    pinned = None
    if a.sessions_from:
        pinned = list(dict.fromkeys(json.loads(ln)["sid"] for ln in open(a.sessions_from, encoding="utf-8")))
    by = load_recaptured(D - 700, only=set(pinned) if pinned else None) if a.recapture else load_after(D - 700)
    if a.verdict_file:
        replace_verdicts(by, a.verdict_file, D - 700)
    if pinned:
        sess = [s for s in pinned if s in by]
    else:
        sess = [s for s, e in by.items() if sum(1 for x in e if x[0] >= D and x[1] == "tool") >= 8]
        random.Random(a.seed).shuffle(sess)
        sess = sess[:a.sessions]
    lv = LM.Live(sidecar=side, learned=os.path.join(STUDY, "no_learned.sqlite"))
    pol = LM.policy()
    HUB_LAMS = (0.25, 0.5, 1.0, 2.0)
    hubdeg, hub_med = {}, 1.0
    if a.hub:                                       # breadth from the cut's own sidecar: how many contexts point at a memory
        for nm, n in lv.db.execute("SELECT n.name, count(*) FROM edge e JOIN node n ON n.id = e.dst "
                                   "WHERE n.kind = 'mem' GROUP BY e.dst"):
            hubdeg[nm[4:].lower()] = n
        if hubdeg:
            hub_med = float(sorted(hubdeg.values())[len(hubdeg) // 2])
        print("hub: %d memories with incoming edges, median in-degree %.0f" % (len(hubdeg), hub_med), flush=True)
    ep_cache = {}

    def session_kind(s8):                           # cli = a session he types in; sdk-cli = headless (a script's prompt)
        if s8 not in ep_cache:
            ep = ""
            for f in glob.glob(os.path.join(os.path.expanduser("~"), ".claude", "projects", (os.environ.get("BRAIN_SLUG") or os.path.expanduser("~").replace(":", "-").replace(chr(92), "-").replace("/", "-")), s8 + "*.jsonl"))[:1]:
                with open(f, encoding="utf-8", errors="replace") as fh:
                    for k, ln in enumerate(fh):
                        if k > 80:
                            break
                        try:
                            ep = json.loads(ln).get("entrypoint") or ""
                        except ValueError:
                            continue
                        if ep:
                            break
            ep_cache[s8] = ep
        return ep_cache[s8]
    lex_ctx = None
    if a.lexical:                                   # the recall stack's own BM25 index, and when each memory appeared
        sys.path.insert(0, os.path.join(os.path.expanduser("~"), ".claude", "dreaming"))
        import bm25
        lex_ctx = {"bm25": bm25, "conn": bm25._postings_conn(bm25.INDEX),
                   "mtime": dict(lv.db.execute("SELECT slug, mtime FROM memory").fetchall())}
    rows = []
    t0 = time.time()
    for si, s in enumerate(sess):
        evs = by[s]
        n_tool, n_eval = 0, 0
        last_kw_ts = None
        seen = set()
        for i, (ts, kd, nid) in enumerate(evs):
            if ts < D:
                if kd in LM.MEM_KINDS:
                    seen.add(nid.lower())
                continue
            moment = False
            if kd == "tool":
                n_tool += 1
                moment = n_tool % a.every == 0
            elif kd == "kw" and ts != last_kw_ts:
                last_kw_ts = ts
                moment = True
            if moment and n_eval < a.per_session:
                # items: the distinct nodes of the ten minutes before this event, newest first
                items, got = [], set()
                for ts2, kd2, nid2 in reversed(evs[:i]):
                    if ts - ts2 > pol["window_s"] or len(items) >= pol["k_items"]:
                        break
                    nm = "%s:%s" % (kd2, nid2)
                    if nm not in got:
                        got.add(nm)
                        items.append((nm, ts - ts2))
                if kd == "kw":                          # the prompt's own words, as the hook adds them
                    for ts2, kd2, nid2 in evs[i:i + 40]:
                        if ts2 != ts or kd2 != "kw":
                            break
                        items.insert(0, ("kw:" + nid2, 0.0))
                ev = lv.evaluate(items, ts, seen_slugs=seen, pol=pol, mem_asof=ts)
                read, errs, places = outcomes(evs, i, ts)
                allc = ev.get("mem_all", [])
                r = {"sid": s, "t": ts, "ev": "prompt" if kd == "kw" else "post", "n_items": ev.get("n_items"),
                     "known": ev.get("n_known"), "read": read[:5], "n_read": len(read)}
                if read:                                # why the first read was or was not reachable
                    f0 = read[0]
                    mt = lv.db.execute("SELECT mtime FROM memory WHERE slug=?", (f0,)).fetchone()
                    in_cand = f0 in {c["slug"] for c in allc}
                    r["read0_cat"] = ("surfaced" if f0 in seen else "no_file" if not mt else
                                      "written_after_moment" if mt[0] > ts else
                                      "candidate" if in_cand else "not_reached")
                    r["read0_node"] = bool(lv.db.execute("SELECT 1 FROM node WHERE name=?", ("mem:" + f0,)).fetchone())
                for chan, keyf in (("fused", lambda c: c.get("fused", 0)), ("hebb", lambda c: c.get("hebb", 0)),
                                   ("reach", lambda c: c.get("reach", 0)),
                                   ("size", lambda c: c["size"] if c.get("size") is not None else -99)):
                    order = [c for c in sorted(allc, key=lambda c: -keyf(c)) if keyf(c) > (-98 if chan == "size" else 0)]
                    top = order[0] if order else None
                    r[chan + "_top"] = top["slug"] if top else None
                    r[chan + "_score"] = keyf(top) if top else None
                    r[chan + "_got"] = bool(top and top["slug"] in read)
                    slugs = [c["slug"] for c in order[:10]]
                    r[chan + "_any10"] = bool(set(slugs) & set(read))
                    r[chan + "_rank_first_read"] = (slugs.index(read[0]) + 1) if read and read[0] in slugs else None
                for H in (0, 5, 10, 20, 40):            # the size channel, admitting only edges at least H strong
                    pool = sorted((c for c in allc if c.get("size") is not None and c.get("hebb", 0) >= H),
                                  key=lambda c: -c["size"])
                    r["sizeH%d_score" % H] = pool[0]["size"] if pool else None
                    r["sizeH%d_got" % H] = bool(pool and pool[0]["slug"] in read)
                r["ep"] = session_kind(str(s)[:8])
                if a.hub:                               # node size discounted by breadth, as search discounts a common word
                    sized = [c for c in allc if c.get("size") is not None]
                    for lam in HUB_LAMS:
                        def hub_score(c, lam=lam):
                            return c["size"] - lam * math.log1p(hubdeg.get(c["slug"].lower(), 0) / hub_med)
                        top = max(sized, key=hub_score) if sized else None
                        r["hub%g_score" % lam] = hub_score(top) if top else None
                        r["hub%g_got" % lam] = bool(top and top["slug"] in read)
                if lex_ctx and kd == "kw":              # his prompt through the recall stack's BM25 arm, alone and with the graph
                    words = [nid2 for ts2, kd2, nid2 in evs[i:i + 40] if ts2 == ts and kd2 == "kw"]
                    lx = [(sl, sc) for sl, sc in (lex_ctx["bm25"].score_postings(" ".join(words), topk=40,
                                                                                  conn=lex_ctx["conn"]) or [])
                          if sl not in seen and 0 < lex_ctx["mtime"].get(sl, 9e18) <= ts]
                    graph = {c["slug"]: c for c in allc}
                    hook_served = {nid2.lower() for ts2, kd2, nid2 in evs[i:i + 400]
                                   if kd2 in ("served", "recalled") and ts <= ts2 <= ts + 120}
                    chans = {"lex": [(sl, sc) for sl, sc in lx],
                             "lexg": sorted(((sl, sc * (1.0 + a.lex_boost * min(3.0, max(0.0, (graph.get(sl) or {}).get("size") or 0.0))))
                                             for sl, sc in lx), key=lambda x: -x[1]),
                             "agree": [(sl, sc) for sl, sc in lx if sl in graph]}
                    for nm_, lst in chans.items():
                        if lst:
                            r[nm_ + "_top"], r[nm_ + "_score"] = lst[0][0], lst[0][1]
                            r[nm_ + "_got"] = lst[0][0] in read
                            r[nm_ + "_hook"] = lst[0][0] in hook_served
                top = ev["mem"][0] if ev.get("mem") else None
                r["top_hebb"] = top.get("hebb", 0) if top else None
                r["top_reach"] = top.get("reach", 0) if top else None
                if ev.get("err"):
                    r["err_top"], r["err_score"] = ev["err"][0][0], ev["err"][0][1]
                    r["err_got"] = ev["err"][0][0] in errs
                if ev.get("place"):
                    r["place_top"], r["place_score"] = ev["place"][0][0], ev["place"][0][1]
                    r["place_got"] = ev["place"][0][0] in places
                vd = [v for v in ev.get("verdict", []) if v[0] in ("verdict:correction", "verdict:repeat")]
                if vd:
                    r["verdict_top"], r["verdict_score"] = vd[0][0], vd[0][1]
                    r["verdict_got"] = bool({"verdict:correction", "verdict:repeat"} & errs)
                if ev.get("attr"):
                    a0 = ev["attr"][0]
                    r["attr"] = a0["id"]
                    mis = [m[0] for m in a0.get("mistakes", [])]
                    r["attr_mistake_got"] = bool(set(mis) & errs)
                    r["attr_has_mistake"] = bool(mis)
                rows.append(r)
                n_eval += 1
            if kd in LM.MEM_KINDS:
                seen.add(nid.lower())
        if (si + 1) % 10 == 0:
            print("%d/%d sessions, %d moments, %.0f s" % (si + 1, len(sess), len(rows), time.time() - t0), flush=True)
    n = len(rows)
    with_read = [r for r in rows if r["n_read"]]
    res = {"cut": time.strftime("%Y-%m-%d %H:%M", time.localtime(D)), "days": a.days, "sessions": len(sess),
           "moments": n, "moments_followed_by_a_read": len(with_read), "horizon_s": HORIZON,
           "seconds": round(time.time() - t_start)}
    for chan in ("fused", "hebb", "reach", "size"):
        res[chan] = {
            "top1_read_in_30min_all_moments": round(sum(r[chan + "_got"] for r in rows) / max(1, n), 4),
            "any_of_top10_read_all_moments": round(sum(r[chan + "_any10"] for r in rows) / max(1, n), 4),
            "first_read_in_top10_when_a_read_follows": round(
                sum(1 for r in with_read if r[chan + "_rank_first_read"]) / max(1, len(with_read)), 4),
            "first_read_top1_when_a_read_follows": round(
                sum(1 for r in with_read if r[chan + "_rank_first_read"] == 1) / max(1, len(with_read)), 4),
            "curve_top1": curve(rows, chan + "_score", chan + "_got")}
    cats = collections.Counter(r.get("read0_cat") for r in with_read)
    res["first_read_category"] = dict(cats)
    res["first_read_was_a_graph_node"] = round(sum(1 for r in with_read if r.get("read0_node")) / max(1, len(with_read)), 4)
    reachable = [r for r in with_read if r.get("read0_cat") in ("candidate", "not_reached")]
    res["reachable_reads"] = {"n": len(reachable)}
    for chan in ("fused", "hebb", "reach", "size"):
        res["reachable_reads"][chan + "_in_top10"] = round(
            sum(1 for r in reachable if r[chan + "_rank_first_read"]) / max(1, len(reachable)), 4)
        res["reachable_reads"][chan + "_top1"] = round(
            sum(1 for r in reachable if r[chan + "_rank_first_read"] == 1) / max(1, len(reachable)), 4)
    res["size_by_edge_floor"] = {str(H): curve(rows, "sizeH%d_score" % H, "sizeH%d_got" % H) for H in (0, 5, 10, 20, 40)}
    er = [r for r in rows if "err_score" in r]
    pl = [r for r in rows if "place_score" in r]
    at = [r for r in rows if r.get("attr_has_mistake")]
    res["err"] = {"moments": len(er), "base_rate": round(sum(r["err_got"] for r in er) / max(1, len(er)), 4),
                  "curve": curve(er, "err_score", "err_got")}
    res["place"] = {"moments": len(pl), "base_rate": round(sum(r["place_got"] for r in pl) / max(1, len(pl)), 4),
                    "curve": curve(pl, "place_score", "place_got")}
    vr = [r for r in rows if "verdict_score" in r]
    res["his_correction"] = {"moments": len(vr), "base_rate": round(sum(r["verdict_got"] for r in vr) / max(1, len(vr)), 4),
                             "curve": curve(vr, "verdict_score", "verdict_got")}
    if a.lexical:                                   # prompt moments only: word search, word search boosted by node size,
        pr = [r for r in rows if r["ev"] == "prompt"]   # and only the memories both word search and the graph reach
        res["lexical"] = {"prompt_moments": len(pr),
                          "graph_size_curve_prompt_moments": curve(pr, "size_score", "size_got")}
        for nm_ in ("lex", "lexg", "agree"):
            rr = [r for r in pr if nm_ + "_score" in r]
            res["lexical"][nm_] = {"moments": len(rr), "curve": curve(pr, nm_ + "_score", nm_ + "_got"),
                                   "top_served_by_a_hook_within_2min": round(sum(r[nm_ + "_hook"] for r in rr) / max(1, len(rr)), 4),
                                   "curve_not_served_by_a_hook": curve([r for r in pr if not r.get(nm_ + "_hook")],
                                                                       nm_ + "_score", nm_ + "_got")}
    # AS SERVED (26 Sep 20:23): consecutive moments of one session share their outcome, so a per-moment curve counts one
    # event many times (one session re-reading one hub memory was 13 of the size channel's 13 hits in his sessions), and
    # the live hook never names the same item to a session twice in two hours. So each channel is also scored with one
    # line per (session, item) - the first moment the item clears the live bar - on his interactive sessions and on the
    # headless ones, places with and without the folders the live filter never names.
    def as_served(rs, ch, bar, keep=lambda r: True):
        seen_k, out = set(), []
        for r in sorted(rs, key=lambda r: (r["sid"], r["t"])):
            if r.get(ch + "_score") is None or r[ch + "_score"] < bar or not keep(r):
                continue
            k = (r["sid"], r.get(ch + "_top"))
            if k not in seen_k:
                seen_k.add(k)
                out.append(r)
        right = [r for r in out if r.get(ch + "_got")]
        return {"lines": len(out), "right": len(right), "distinct_items_right": len({r.get(ch + "_top") for r in right}),
                "sessions_right": len({r["sid"] for r in right})}
    polv = LM.policy()
    res["as_served"] = {}
    for kind in ("cli", "sdk-cli"):
        rs_k = [r for r in rows if r.get("ep") == kind]
        res["as_served"][kind] = {
            "place_non_obvious": as_served(rs_k, "place", polv["place_hebb_min"],
                                           lambda r: not LM.TRIVIAL_PLACE.match(str(r.get("place_top")))),
            "place": as_served(rs_k, "place", polv["place_hebb_min"]),
            "size": as_served(rs_k, "size", polv["mem_size_min"]),
            "hebb": as_served(rs_k, "hebb", polv["mem_hebb_min"]),
            "err": as_served(rs_k, "err", polv["err_hebb_min"]),
            "verdict": as_served(rs_k, "verdict", polv.get("verdict_hebb_min", 76.0))}
    # every channel on his interactive sessions alone (26 Sep: 88 of 131 prompt moments were headless scripts' prompts)
    cli = [r for r in rows if r.get("ep") == "cli"]
    res["interactive"] = {"moments": len(cli), "sessions": len({r["sid"] for r in cli}),
                          "size": curve(cli, "size_score", "size_got"), "hebb": curve(cli, "hebb_score", "hebb_got"),
                          "place": curve([r for r in cli if "place_score" in r], "place_score", "place_got"),
                          "err": curve([r for r in cli if "err_score" in r], "err_score", "err_got")}
    if a.hub:
        res["hub"] = {"median_in_degree": hub_med, "memories_with_edges": len(hubdeg)}
        for lam in HUB_LAMS:
            res["hub"][str(lam)] = {"all": curve(rows, "hub%g_score" % lam, "hub%g_got" % lam),
                                    "interactive": curve(cli, "hub%g_score" % lam, "hub%g_got" % lam)}
    res["attractor"] = {"moments_lit_with_a_mistake": len(at),
                        "mistake_followed": round(sum(r["attr_mistake_got"] for r in at) / max(1, len(at)), 4)}
    # thresholds: the loosest bar whose top-line precision still reaches the target, never firing on more than 30%
    def pick(cv, target):
        ok = [c for c in cv if c["precision"] >= target and c["fire_rate"] <= 0.3]
        return max(ok, key=lambda c: c["fire_rate"]) if ok else None
    choice = {"mem": pick(res["hebb"]["curve_top1"], a.target_precision),
              "err": pick(res["err"]["curve"], a.target_precision),
              "place": pick(res["place"]["curve"], a.target_precision)}
    res["threshold_choice"] = choice
    os.makedirs(REPO_RESULTS, exist_ok=True)
    res["variant"] = {"tag": a.tag, "extra_event_files": a.extra, "half_life_days": a.half_life,
                      "verdict_file": a.verdict_file, "recaptured": a.recapture, "sessions_from": a.sessions_from,
                      "sidecar": a.sidecar, "hub": a.hub}
    out = os.path.join(REPO_RESULTS, "LIVE_MEMBRANE_STUDY_%s%s.json" % (time.strftime("%Y-%m-%d_%H%M"),
                                                                      ("_" + a.tag) if a.tag else ""))
    json.dump(res, open(keep_old(out), "w", encoding="utf-8"), indent=1)
    with open(keep_old(os.path.join(STUDY, "rows_%s.jsonl" % time.strftime("%Y%m%d_%H%M"))), "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    print(json.dumps({k: v for k, v in res.items() if k not in ("fused", "hebb", "reach", "size")}, indent=1))
    for chan in ("fused", "hebb", "reach", "size"):
        c = res[chan]
        print("%-6s top1 read in 30 min %.3f | any of top10 %.3f | when a read follows: in top10 %.3f, top1 %.3f" % (
            chan, c["top1_read_in_30min_all_moments"], c["any_of_top10_read_all_moments"],
            c["first_read_in_top10_when_a_read_follows"], c["first_read_top1_when_a_read_follows"]))
        print("       curve " + "; ".join("%.0f%%: p %.3f (>= %.1f)" % (x["fire_rate"] * 100, x["precision"], x["threshold"])
                                          for x in c["curve_top1"]))
    print("wrote", out)
    if a.write_policy:
        cur = LM.policy()
        new = {}
        if choice["mem"]:
            new["mem_hebb_min"] = max(cur["mem_hebb_min"], choice["mem"]["threshold"])
        if choice["err"]:
            new["err_hebb_min"] = max(cur["err_hebb_min"], choice["err"]["threshold"])
        if choice["place"]:
            new["place_hebb_min"] = max(cur["place_hebb_min"], choice["place"]["threshold"])
        if new:
            try:
                p = json.load(open(LM.POLICY, encoding="utf-8"))
            except Exception:
                p = {}
            p.update(new)
            p["version"] = int(p.get("version", 1)) + 1
            p["_set_by"] = "study_live_membrane.py %s: %s" % (time.strftime("%Y-%m-%d %H:%M"), os.path.basename(out))
            tmp = LM.POLICY + ".tmp"
            json.dump(p, open(tmp, "w", encoding="utf-8"), indent=1)
            os.replace(tmp, LM.POLICY)
            print("policy:", new)


if __name__ == "__main__":
    main()
