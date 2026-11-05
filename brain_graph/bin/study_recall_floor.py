"""Does recall's 0.02 display floor hide rows above it? (28 Sep 2026, claude-825b1305; scheduled audit account01/B02 R1)

On the bm25-first path the list recall displays runs in segment order - the BM25 block, then the lexical, graph and
semantic tails - not sorted by display score, and the display loop ended at the first row scoring 0.02 or less. A
candidate brain.py skips such a row instead. Because the rows before the first low row are the same under both rules and
the skip only adds later rows above the floor, the skip can add a displayed row but never remove or reorder one. This
study counts how often it adds rows and whether an added row is a gold.

One run per question of the candidate with BRAIN_RECALL_DUMP_SCORED (the scored list, in the order the display reads
it), both rules applied offline, at 8 rows (recall's default) and at 40 (the his56 ranker's depth):
  A  his 56 owner-voice questions with their golds (eval_brain_recall_fresh BANK + BANK_V2_EXTRA)
  B  up to 60 distinct recent searches from the live recall log, from his sessions and scripts (session_kinds), never
     test workers; their text is not written out, only a sha256

    python study_recall_floor.py CANDIDATE.py OUT.json
"""
import hashlib
import io
import json
import os
import random
import subprocess
import sys
import tempfile

HOME = os.path.expanduser("~")
DR = os.path.join(HOME, ".claude", "dreaming")
BG = os.path.join(HOME, ".claude", "brain_graph")
sys.path.insert(0, DR)
sys.path.insert(0, os.path.join(BG, "bin"))
import eval_brain_recall_fresh as fresh  # noqa: E402
import session_kinds as SK  # noqa: E402

NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def lists(scored, n):
    brk, skp = [], []
    for sc, slug in scored:
        if len(brk) >= n or sc <= 0.02:
            break
        brk.append(slug)
    for sc, slug in scored:
        if len(skp) >= n:
            break
        if sc > 0.02:
            skp.append(slug)
    return brk, skp


def run(cand, q, dump):
    env = dict(os.environ, BRAIN_RECALL_LOG="0", BRAIN_RECALL_DUMP_SCORED=dump)
    subprocess.run([sys.executable, "-X", "utf8", cand, "recall", q, "-n", "40"], capture_output=True, text=True,
                   encoding="utf-8", errors="replace", timeout=600, env=env, creationflags=NOWIN)
    rows = [json.loads(l) for l in io.open(dump, encoding="utf-8") if l.strip()]
    os.remove(dump)
    return rows[-1]["scored"] if rows else None


def main():
    cand, out = sys.argv[1], sys.argv[2]
    tmp = tempfile.mkdtemp(prefix="floor_")
    # A: his 56
    a_rows = []
    for i, (q, golds) in enumerate(fresh.BANK + fresh.BANK_V2_EXTRA):
        sc = run(cand, q, os.path.join(tmp, "a%d.jsonl" % i))
        if sc is None:
            a_rows.append({"i": i, "error": "no dump"})
            continue
        r = {"i": i}
        for n in (8, 40):
            b, s = lists(sc, n)
            rb = next((k + 1 for k, x in enumerate(b) if x in golds), 0)
            rs = next((k + 1 for k, x in enumerate(s) if x in golds), 0)
            r["n%d" % n] = {"shown_break": len(b), "shown_skip": len(s), "added": len(s) - len(b),
                            "gold_rank_break": rb, "gold_rank_skip": rs, "prefix_equal": s[:len(b)] == b}
        a_rows.append(r)
        print("A %2d: n8 %d->%d rows, gold %d->%d" % (i, r["n8"]["shown_break"], r["n8"]["shown_skip"],
                                                       r["n8"]["gold_rank_break"], r["n8"]["gold_rank_skip"]), flush=True)
    # B: recent logged searches from his sessions and scripts
    log = [json.loads(l) for l in io.open(os.path.join(BG, "live", "recall_log.jsonl"), encoding="utf-8") if l.strip()]
    seen, pool = set(), []
    for r in log:
        q = (r.get("q") or "").strip()
        sid = r.get("session") or ""
        if not q or q in seen or not sid:
            continue
        if SK.kind(sid) not in ("his", "script"):
            continue
        seen.add(q)
        pool.append(q)
    SK.save()
    random.Random(20260928).shuffle(pool)
    b_rows = []
    for j, q in enumerate(pool[:60]):
        sc = run(cand, q, os.path.join(tmp, "b%d.jsonl" % j))
        if sc is None:
            b_rows.append({"q_sha256": hashlib.sha256(q.encode("utf-8")).hexdigest()[:16], "error": "no dump"})
            continue
        r = {"q_sha256": hashlib.sha256(q.encode("utf-8")).hexdigest()[:16]}
        for n in (8, 40):
            b, s = lists(sc, n)
            r["n%d" % n] = {"shown_break": len(b), "shown_skip": len(s), "added": len(s) - len(b),
                            "prefix_equal": s[:len(b)] == b}
        b_rows.append(r)
        print("B %2d: n8 %d->%d rows" % (j, r["n8"]["shown_break"], r["n8"]["shown_skip"]), flush=True)

    def summ(rows, key):
        ok = [r for r in rows if key in r]
        return {"queries": len(ok), "lists_changed": sum(1 for r in ok if r[key]["added"] > 0),
                "rows_added": sum(r[key]["added"] for r in ok),
                "prefix_equal_everywhere": all(r[key]["prefix_equal"] for r in ok),
                "empty_under_break_not_under_skip": sum(1 for r in ok if r[key]["shown_break"] == 0 and r[key]["shown_skip"] > 0)}
    res = {"what": __doc__.split("\n")[0], "candidate_sha256": hashlib.sha256(open(cand, "rb").read()).hexdigest(),
           "A_his56": {"n8": summ(a_rows, "n8"), "n40": summ(a_rows, "n40"),
                       "gold_in_top8_break": sum(1 for r in a_rows if r.get("n8", {}).get("gold_rank_break", 0) > 0),
                       "gold_in_top8_skip": sum(1 for r in a_rows if r.get("n8", {}).get("gold_rank_skip", 0) > 0),
                       "gold_rank_changed": [r["i"] for r in a_rows if "n8" in r and
                                             r["n8"]["gold_rank_break"] != r["n8"]["gold_rank_skip"]],
                       "rows": a_rows},
           "B_logged": {"pool": len(pool), "n8": summ(b_rows, "n8"), "n40": summ(b_rows, "n40"), "rows": b_rows}}
    with io.open(out + ".tmp", "w", encoding="utf-8") as f:
        json.dump(res, f, indent=1)
    os.replace(out + ".tmp", out)
    print(json.dumps({k: v for k, v in res["A_his56"].items() if k != "rows"}))
    print(json.dumps({k: v for k, v in res["B_logged"].items() if k != "rows"}))


if __name__ == "__main__":
    main()
