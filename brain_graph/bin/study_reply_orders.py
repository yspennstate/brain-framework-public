"""study_reply_orders.py - could his stored orders, looked up once at the agent's reply, have handed back the order he then
restated, without serving a line to most replies? (28 Sep, claude-825b1305; registered THREE_DAY_RUN 28 Sep 00:3x.)

His corrections follow the agent's report (a median 39 s after its last action; his_corrections_follow_quick_on_task_
answers_not_drift_or_struggle_2026_09_27), and the orders channel that looked at every prompt and tool call was too chatty
(his_orders_channel_stays_in_shadow_too_chatty_and_late_2026_09_27). This looks once per reply.

For the 22 corrections that restated a stored order (results/STANDING_ORDERS_LABELS_2026-09-27.json): the agent's last
text block before the correction is the reply; his orders available then (study_standing_orders.orders / resolve) are
ranked by the word index on (a) the reply's words and (b) his ask's words plus the reply's. Chattiness: every reply in
those sessions (the last assistant text before each of his prompts) gets the same lookup; a line is served when the
first order's score clears the bar. Active session-hours are the distinct (session, clock hour) pairs with a message.

    python study_reply_orders.py LABELS_JSON OUT_JSON
"""

import sys
if __name__ == "__main__" and any(arg in ("-h", "--help") for arg in sys.argv[1:]):
    print(__doc__ or "No command-line options.")
    raise SystemExit(0)

import collections
import glob
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(1, os.path.join(os.path.expanduser("~"), ".claude", "dreaming"))
import bm25  # noqa: E402
import study_standing_orders as SO  # noqa: E402

PROJ = os.path.join(os.path.expanduser("~"), ".claude", "projects")


def epoch(ts):
    """Transcript stamps are UTC ('...Z'). calendar.timegm reads them as UTC; mktime minus time.timezone does not, because
    time.timezone is the standard-time offset and September here is summer time (the first run sat every event an hour
    early against the labels and found no reply for 12 of 22 corrections)."""
    import calendar
    return float(calendar.timegm(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S"))) + (float("0" + ts[19:23]) if ts[19:20] == "." else 0.0) if ts else None


def text_blocks(content):
    if isinstance(content, str):
        return [content]
    return [c.get("text", "") for c in content or [] if isinstance(c, dict) and c.get("type") == "text"]


def is_his_prompt(o):
    if o.get("type") != "user" or o.get("isMeta") or o.get("isSidechain"):
        return False
    c = (o.get("message") or {}).get("content")
    if isinstance(c, list) and any(isinstance(x, dict) and x.get("type") == "tool_result" for x in c):
        return False
    t = " ".join(text_blocks(c)).strip()
    return bool(t) and not t.startswith("<") and not t.startswith("[Request interrupted")


def load_session(sid):
    """[(t, 'prompt'|'reply', text)] in order; a reply is an assistant text block."""
    paths = glob.glob(os.path.join(PROJ, "*", sid + "*.jsonl"))
    out = []
    for p in paths:
        for line in open(p, encoding="utf-8", errors="replace"):
            try:
                o = json.loads(line)
            except ValueError:
                continue
            t = epoch(o.get("timestamp"))
            if t is None:
                continue
            if is_his_prompt(o):
                out.append((t, "prompt", " ".join(text_blocks(o["message"].get("content")))))
            elif o.get("type") == "assistant" and not o.get("isSidechain"):
                for tx in text_blocks((o.get("message") or {}).get("content")):
                    if tx.strip():
                        out.append((t, "text", tx))
    out.sort(key=lambda x: x[0])
    return out


def replies(evs):
    """[(t, text)]: the last assistant text before each of his prompts (and at the session's end)."""
    out, last = [], None
    for t, kd, tx in evs:
        if kd == "text":
            last = (t, tx)
        elif kd == "prompt" and last:
            out.append(last)
            last = None
    if last:
        out.append(last)
    return out


def rank_scored(query, t, ords, conn, n=10):
    if not query.strip():
        return []
    out, seen = [], set()
    for sl, sc in bm25.score_postings(query, topk=800, conn=conn) or []:
        sl = str(sl).lower()
        if sl in ords and ords[sl]["avail"] <= t:
            r = SO.resolve(sl, t, ords)
            if r not in seen:
                seen.add(r)
                out.append((r, float(sc)))
        if len(out) >= n:
            break
    return out


def words(tx):
    return " ".join(SO.TOKEN.findall((tx or "").lower()))


def main():
    labels_path, out_path = sys.argv[1], sys.argv[2]
    lab = json.load(open(labels_path, encoding="utf-8"))["preventable"]
    t0 = time.time()
    ords = SO.orders()
    conn = bm25._postings_conn(bm25.INDEX)
    sess = {}
    rows = []
    for c in lab:
        t_c, t_a = float(c["t"]), float(c.get("ask_t") or c["t"])
        target = [o for o in (eval(c["orders"]) if isinstance(c["orders"], str) else c["orders"])]
        target = {SO.resolve(o, t_c, ords) for o in target if o in ords and ords[o]["avail"] <= t_a}
        if not target:
            continue
        if c["sid"] not in sess:
            sess[c["sid"]] = load_session(c["sid"])
        before = [(t, tx) for t, kd, tx in sess[c["sid"]] if kd == "text" and t < t_c - 1.0 and t >= t_a - 5.0]
        if not before:
            rows.append({"n": c["n"], "theme": c["theme"], "sid": c["sid"], "reply": None})
            continue
        t_r, rep = before[-1]
        row = {"n": c["n"], "theme": c["theme"], "sid": c["sid"], "reply_s_before": round(t_c - t_r, 1),
               "reply_words": len(words(rep).split())}
        for arm, q in (("reply", words(rep)), ("ask_reply", words(c.get("ask")) + " " + words(rep)),
                       ("ask", words(c.get("ask")))):
            lst = rank_scored(q, t_r, ords, conn)
            names = [s for s, _ in lst]
            row[arm] = {"rank": min([names.index(o) + 1 for o in target if o in names] or [99]),
                        "top_score": lst[0][1] if lst else 0.0, "top": names[0] if names else None}
        rows.append(row)
        print("#%-3s %-10s reply %4.0f s before, %4d words | reply rank %2s | ask+reply rank %2s | ask rank %2s" % (
            c["n"], c["theme"], row["reply_s_before"], row["reply_words"], row["reply"]["rank"],
            row["ask_reply"]["rank"], row["ask"]["rank"]), flush=True)
    # chattiness over every reply in these sessions
    every = collections.defaultdict(list)
    hours = set()
    asks = {}
    for sid, evs in sess.items():
        last_prompt = ""
        for t, kd, tx in evs:
            hours.add((sid, int(t // 3600)))
        cur_prompt = ""
        for t, kd, tx in evs:
            if kd == "prompt":
                cur_prompt = tx
            asks[(sid, t)] = cur_prompt
        for t, tx in replies(evs):
            ask_here = max(((tt, a) for (s2, tt), a in asks.items() if s2 == sid and tt <= t), default=(0, ""))[1]
            for arm, q in (("reply", words(tx)), ("ask_reply", words(ask_here) + " " + words(tx))):
                lst = rank_scored(q, t, ords, conn, n=1)
                every[arm].append(lst[0][1] if lst else 0.0)
    act_h = len(hours)
    usable = [r for r in rows if r.get("reply")]
    table = {}
    for arm in ("reply", "ask_reply"):
        scores = sorted(every[arm])
        table[arm] = []
        for q in (0.50, 0.75, 0.85, 0.90, 0.95, 0.98):
            b = scores[min(len(scores) - 1, int(q * len(scores)))] if scores else 0.0
            served = sum(1 for s in scores if s >= b)
            caught1 = sum(1 for r in usable if r[arm]["rank"] == 1 and r[arm]["top_score"] >= b)
            caught2 = sum(1 for r in usable if r[arm]["rank"] <= 2 and r[arm]["top_score"] >= b)
            table[arm].append({"bar_quantile": q, "bar": round(b, 3), "lines": served,
                               "lines_per_active_hour": round(served / max(1, act_h), 3),
                               "caught_first": caught1, "caught_top2": caught2})
    out = {"built": time.strftime("%Y-%m-%d %H:%M:%S"), "labelled": len(lab), "usable": len(usable),
           "sessions": len(sess), "active_session_hours": act_h, "replies": len(every["reply"]),
           "rank1_any_bar": {a: sum(1 for r in usable if r[a]["rank"] == 1) for a in ("reply", "ask_reply", "ask")},
           "table": table, "rows": rows, "seconds": round(time.time() - t0)}
    json.dump(out, open(out_path, "w", encoding="utf-8"), indent=1)
    print("\n%d usable of %d; %d sessions, %d active session-hours, %d replies" % (
        len(usable), len(lab), len(sess), act_h, len(every["reply"])))
    print("rank 1 with no bar:", out["rank1_any_bar"])
    for arm in table:
        print("\n", arm)
        for r in table[arm]:
            print("  bar q%.2f %7.2f | lines %4d (%.2f per active hour) | caught first %2d | top 2 %2d" % (
                r["bar_quantile"], r["bar"], r["lines"], r["lines_per_active_hour"], r["caught_first"], r["caught_top2"]))


if __name__ == "__main__":
    main()
