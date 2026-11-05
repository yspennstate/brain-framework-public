"""study_answer_orders.py - does the agent's own answer name the order he is about to have to say again? (28 Sep 07:1x,
claude-825b1305; registered in study/THREE_DAY_RUN_2026-09-27.md before any code.)

His 27 Sep 16:29 idea was that the brain should speak when a mistake is about to be made; the same week measured that
his corrections follow quick on-task answers, not drift or struggle. So the moment to check is the answer. For the 20
usable labelled corrections of STANDING_ORDERS_LABELS_2026-09-27.json (22 corrections and repeats that restated an order
the brain already held; 20 whose order existed before his ask), the word index restricted to his orders
(study_standing_orders.rank_words, the orders in force at that time) runs over:
  answer       the words of the agent's last message before his correction (at most 120)
  answer+ask   those words and his ask's
and the rank of the restated order is compared with the replay's arms at the same moment (ask 7 of 20, context 8).
The negative side: every other agent message in the same sessions with no correction or repeat of his within the next 10
minutes; the share of them whose first order scores at least the lowest score among the hits is the line's rate if it
were served at that score.

    python study_answer_orders.py LABELS_JSON OUT_JSON
"""

import sys
if __name__ == "__main__" and any(arg in ("-h", "--help") for arg in sys.argv[1:]):
    print(__doc__ or "No command-line options.")
    raise SystemExit(0)

import glob
import io
import json
import os
import sys
import time

HERE = os.path.join(os.path.expanduser("~"), ".claude", "brain_graph", "bin")
sys.path.insert(0, HERE)
sys.path.insert(1, os.path.join(os.path.expanduser("~"), ".claude", "dreaming"))
import bm25  # noqa: E402
import study_standing_orders as SSO  # noqa: E402

PROJ = os.path.join(os.path.expanduser("~"), ".claude", "projects", (os.environ.get("BRAIN_SLUG") or os.path.expanduser("~").replace(":", "-").replace(chr(92), "-").replace("/", "-")))
VERDICTS = os.path.join(os.path.expanduser("~"), ".claude", "brain_graph", "events_verdicts.jsonl")
MAX_WORDS = 120
NEG_PER_SESSION = 150


def epoch(ts):
    """the capture's own reading of a transcript timestamp (the labels' times came from it)"""
    import extract_touch_events as X
    return X.epoch(ts)


def agent_messages(sid8):
    """(epoch, text) of every assistant text message in the Claude session whose id starts with sid8"""
    out = []
    for path in glob.glob(os.path.join(PROJ, sid8 + "*.jsonl")):
        with io.open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if '"assistant"' not in line:
                    continue
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                if o.get("type") != "assistant":
                    continue
                msg = o.get("message") or {}
                parts = [b.get("text") or "" for b in (msg.get("content") or []) if isinstance(b, dict)
                         and b.get("type") == "text"]
                txt = " ".join(p for p in parts if p.strip())
                t = epoch(o.get("timestamp") or "")
                if txt.strip() and t:
                    out.append((t, txt))
    out.sort()
    return out


def rank_scored(query, t, ords, conn, n=10):
    """rank_words with the BM25 score of each order kept (the best score of any slug resolving to it)"""
    if not query.strip():
        return []
    hits = bm25.score_postings(query, topk=800, conn=conn) or []
    out, seen = [], set()
    for sl, sc in hits:
        sl = str(sl).lower()
        if sl in ords and ords[sl]["avail"] <= t:
            r = SSO.resolve(sl, t, ords)
            if r not in seen:
                seen.add(r)
                out.append((r, float(sc)))
        if len(out) >= n:
            break
    return out


def words(text):
    return " ".join(SSO.TOKEN.findall((text or "").lower())[:MAX_WORDS])


def main():
    labels_path, out_path = sys.argv[1], sys.argv[2]
    lab = json.load(open(labels_path, encoding="utf-8"))["preventable"]
    ords = SSO.orders()
    conn = bm25._postings_conn(bm25.INDEX)
    verdict_ts = {}
    for line in io.open(VERDICTS, encoding="utf-8"):
        try:
            v = json.loads(line)
        except Exception:
            continue
        if v.get("id") in ("correction", "repeat"):
            verdict_ts.setdefault(str(v.get("session"))[:8], []).append(float(v["ts"]))
    rows, neg = [], []
    for c in lab:
        t_c, t_a = float(c["t"]), float(c.get("ask_t") or c["t"])
        target = [o for o in c["orders"] if o in ords and ords[o]["avail"] <= t_a]
        if not target:
            continue
        msgs = agent_messages(c["sid"])
        before = [m for m in msgs if t_a - 5 <= m[0] < t_c - 1]
        if not before:
            rows.append({"n": c["n"], "theme": c["theme"], "sid": c["sid"], "answer": None})
            continue
        ans = before[-1][1]
        res = {}
        for arm, q in (("answer", words(ans)), ("answer+ask", words(ans) + " " + words(c.get("ask")))):
            lst = rank_scored(q, t_c - 1, ords, conn)
            names = [o for o, _ in lst]
            best = min([names.index(o) + 1 for o in target if o in names] or [99])
            hit_score = next((s for o, s in lst if o in target), None)
            res[arm] = {"rank": best, "hit_score": hit_score, "top_score": lst[0][1] if lst else None}
        rows.append({"n": c["n"], "theme": c["theme"], "sid": c["sid"], "answer_words": len(words(ans).split()),
                     "minutes_before": round((t_c - before[-1][0]) / 60.0, 1), **res})
    # negatives once per session: up to NEG_PER_SESSION evenly spaced agent messages with no correction or repeat of
    # his in the ten minutes after
    for sid in sorted({c["sid"] for c in lab}):
        vt = verdict_ts.get(sid, [])
        pool = [(t_m, txt) for t_m, txt in agent_messages(sid) if not any(0 <= x - t_m <= 600 for x in vt)]
        if len(pool) > NEG_PER_SESSION:
            step = len(pool) / float(NEG_PER_SESSION)
            pool = [pool[int(k * step)] for k in range(NEG_PER_SESSION)]
        for t_m, txt in pool:
            lst = rank_scored(words(txt), t_m, ords, conn, n=1)
            neg.append({"sid": sid, "top_score": lst[0][1] if lst else None})
        print("negatives: %s %d" % (sid, len(pool)), flush=True)
    summ = {}
    for arm in ("answer", "answer+ask"):
        got = [r for r in rows if r.get(arm)]
        hits = [r for r in got if r[arm]["rank"] <= 2]
        floor = min((r[arm]["hit_score"] for r in hits if r[arm]["hit_score"] is not None), default=None)
        summ[arm] = {"cases": len(got), "hit2": len(hits), "hit1": sum(1 for r in got if r[arm]["rank"] == 1),
                     "lowest_hit_score": floor}
    floor = summ["answer"]["lowest_hit_score"]
    uniq = {}
    for x in neg:
        uniq.setdefault(x["sid"], []).append(x["top_score"])
    n_neg = sum(len(v) for v in uniq.values())
    over = sum(1 for v in uniq.values() for s in v if s is not None and floor is not None and s >= floor)
    summ["negatives"] = {"messages": n_neg, "sessions": len(uniq), "first_order_at_or_above_answer_floor": over,
                         "share": round(over / float(n_neg), 3) if n_neg else None}
    out = {"built": time.strftime("%Y-%m-%d %H:%M:%S"), "summary": summ, "rows": rows,
           "replay_arms_at_case": {"ask": 7, "context": 8, "graph": 0, "of": 20}}
    json.dump(out, open(out_path, "w", encoding="utf-8"), indent=1)
    print(json.dumps(summ, indent=1))


if __name__ == "__main__":
    main()
