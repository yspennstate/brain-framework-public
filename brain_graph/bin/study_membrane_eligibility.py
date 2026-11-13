"""study_membrane_eligibility.py - what the membrane would put into a fresh worker's context on a bank question, and
whether that line names the answer.

R12's show-or-hold trial randomizes the delivery of a line the membrane has already
decided to show, on pairs of fresh benchmark workers. How many pairs it needs depends on two things that can be read
without running a single worker: how often a fresh worker's first prompt produces a line at all (eligibility), and how
often that line names the memory that answers the question (the most a shown line could add). This runs the membrane's
own evaluate() and decide() on each bank question exactly as run() builds a prompt's activation (the question's words as
kw: nodes at age 0, nothing else touched yet), in a fresh session state, on the live sidecar and policy, as a headless
receiver. Three times: with the six-hour cross-session quota as it stands; without it (a trial's own sessions would meet
the quota too, so both bound the rate); and without it with the question's own words counted as specific (kw: is in
GENERIC, so as built a line lit only by the question cannot be served; this variant prices that rule).

Counts only in the output; the per-question rows name gold memories and go to the key quarantine.

    python study_membrane_eligibility.py OUT_JSON [--limit N]
"""
if "-h" in __import__("sys").argv[1:] or "--help" in __import__("sys").argv[1:]:
    print((__doc__ or "").strip() or "usage: python %s" % __import__("os").path.basename(__file__))
    raise SystemExit(0)
import collections
import json
import os
import sys
import time

HOME = os.path.expanduser("~")
BIN = os.path.join(HOME, ".claude", "brain_graph", "bin")
sys.path.insert(0, os.path.join(HOME, ".claude", "dreaming"))
sys.path.insert(0, BIN)
Q = os.path.join(HOME, "bank_recall_keys_quarantine")

from quarantine_rows import keep_old  # noqa: E402  a rerun keeps the previous output (brain data is never deleted)
import live_membrane as LM  # noqa: E402
import extract_touch_events as X  # noqa: E402
import eval_graph_mixture as E  # noqa: E402


def one(lv, pol, base_p, q, now, quota, kw_specific=False):
    his = X.his_text({}, q) or q
    items = [("kw:" + w, 0.0) for w in X.words(his, cap=40)]
    ev = lv.evaluate(items, now, seen_slugs=set(), pol=pol, base_p=base_p)
    ev["_names"] = {}
    ev["ep"] = "sdk-cli"                              # a benchmark worker is headless (claude -p)
    if ev.get("err"):
        ev["_fix"] = lv.fix_memory(ev["err"][0][0], set())
    st = {"t0": now, "calls": 1, "seen": [], "served": {}, "fire_times": [], "sid": "eligibility-study"}
    real, real_generic = LM.served_elsewhere, LM.GENERIC
    if not quota:
        LM.served_elsewhere = lambda *a, **k: {}
    if kw_specific:                                   # the counterfactual: the question's own words count as specific
        LM.GENERIC = tuple(g for g in LM.GENERIC if g != "kw:")
    try:
        lines, served = LM.decide(ev, now, st, pol)
    finally:
        LM.served_elsewhere, LM.GENERIC = real, real_generic
    return ev, lines, served


def main():
    a = sys.argv[1:]
    out_path = a[0]
    limit = int(a[a.index("--limit") + 1]) if "--limit" in a else 0
    pol = LM.policy()
    try:
        base_p = json.load(open(LM.PRECISION, encoding="utf-8")).get("mem", {}).get("rate", 0.2)
    except Exception:
        base_p = 0.2
    lv = LM.Live()
    now = time.time()
    bank = E.items(limit)
    rows = []
    agg = collections.defaultdict(lambda: collections.Counter())
    ranks = collections.defaultdict(list)
    for bk, qid, q, golds in bank:
        row = {"bank": bk, "id": qid, "gold": sorted(golds)}
        for quota, kws in ((True, False), (False, False), (False, True)):
            ev, lines, served = one(lv, pol, base_p, q, now, quota, kws)
            tag = "kw_specific_noquota" if kws else ("quota" if quota else "noquota")
            mem = [k for kind, k, _b in served if kind == "mem"]
            place = [k for kind, k, _b in served if kind == "place"]
            c = agg[(bk, tag)]
            c["n"] += 1
            c["any_line"] += bool(lines)
            c["mem_line"] += bool(mem)
            c["place_line"] += bool(place)
            c["other_line"] += bool([s for s in served if s[0] not in ("mem", "place")])
            c["mem_line_names_gold"] += any(m.lower() in golds for m in mem)
            row[tag] = {"lines": len(lines), "mem": mem, "place": place,
                        "gold_served": any(m.lower() in golds for m in mem)}
            if quota:
                # the word channel (off in the live policy): the question's words through the word index, as run() would
                # compute it on a prompt of his; its top line, whether that is the answer, and whether it clears word_min
                wc = LM.word_candidates(X.words(X.his_text({}, q) or q, cap=40), set(), lv.db)
                wmin = pol.get("word_min") or 61.4
                wr = next((i + 1 for i, c in enumerate(wc) if c["slug"].lower() in golds), None)
                c3 = agg[(bk, "word_channel")]
                c3["n"] += 1
                c3["has_candidate"] += bool(wc)
                c3["top_is_gold"] += bool(wc) and wc[0]["slug"].lower() in golds
                c3["top_clears_bar"] += bool(wc) and wc[0]["score"] >= wmin
                c3["top_clears_bar_and_gold"] += bool(wc) and wc[0]["score"] >= wmin and wc[0]["slug"].lower() in golds
                c3["gold_top3"] += bool(wr and wr <= 3)
                row["word_top_gold"] = bool(wc) and wc[0]["slug"].lower() in golds
                row["word_top_score"] = wc[0]["score"] if wc else None
                row["word_gold_rank"] = wr
                allm = [x["slug"].lower() for x in ev.get("mem_all", [])]
                r = next((i + 1 for i, s in enumerate(allm) if s in golds), None)
                ranks[bk].append(r)
                row["gold_rank_in_candidates"] = r
                row["n_candidates"] = len(allm)
                row["n_known_items"] = ev.get("n_known")
                row["qb"] = ev.get("qb", [])
                c2 = agg[(bk, "candidates")]
                c2["n"] += 1
                c2["gold_top1"] += r == 1
                c2["gold_top3"] += bool(r and r <= 3)
                c2["gold_top10"] += bool(r and r <= 10)
                c2["gold_anywhere"] += r is not None
                c2["no_known_item"] += not ev.get("n_known")
                c2["quota_held_something"] += bool(ev.get("qb"))
        rows.append(row)
    res = {"built": time.strftime("%Y-%m-%d %H:%M:%S"), "study": "R12 step zero: membrane eligibility on a fresh worker's "
           "first prompt (the question's words only), with and without the cross-session quota",
           "policy_version": pol.get("version"), "sidecar_built": lv.meta.get("built"),
           "bars": {k: pol.get(k) for k in ("mem_size_min", "mem_hebb_min", "mem_reach_min", "place_hebb_min",
                                             "max_lines", "mem_max_sessions")},
           "counts": {"%s/%s" % k: dict(v) for k, v in sorted(agg.items())},
           "n_questions": len(bank)}
    qrows = keep_old(os.path.join(Q, "membrane_eligibility_rows_%s.jsonl" % time.strftime("%Y%m%d_%H%M")))
    with open(qrows, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    res["rows_file"] = os.path.basename(qrows)
    json.dump(res, open(keep_old(out_path), "w", encoding="utf-8"), indent=1)
    print(json.dumps(res["counts"], indent=1))


if __name__ == "__main__":
    main()
