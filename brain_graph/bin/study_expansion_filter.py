"""study_expansion_filter.py - does dropping weak generated questions help the word index? (27 Sep, claude-825b1305)

A scheduled audit (account04/B11, job R3) judged 207 stored questions: 94.7 percent answerable from their memory, 17.9
percent so generic they would match hundreds of memories ("anything new?"), 5.3 percent claiming more than the memory says
("right now" on a dated snapshot, "all" or "the system" where the memory speaks of one cycle). Doc2Query-- (Gospodinov,
MacAvaney and Macdonald, ECIR 2023) found that dropping weak generated queries helps. Three gates, per question:
  G1 generic   the mean inverse document frequency of its words (in the memory-only index) is below tau
  G2 now       it asks about the present ("now", "currently", "still", "today", ...) - every memory here is dated
  G3 universal it uses "all", "every", "always", "never", ... and the memory's own text never does
Arms, on the word index alone and built in memory (no index file is written): base (no questions), all (live since
27 Sep 01:13), G2+G3, and G1 at three tau values (the 10th, 20th and 30th percentile of the questions' mean IDF) with G2+G3.
tau is judged on the TUNE half of the reworded bank; the test half, his 56 questions, his real first prompts and the
agents' judged searches (first memory opened, a fixed target) are the checks. Counts only in the result; per-question
ranks of the reworded bank go to the quarantine.

    python study_expansion_filter.py OUT_JSON [--skip-keys]
"""

import sys
if __name__ == "__main__" and any(arg in ("-h", "--help") for arg in sys.argv[1:]):
    print(__doc__ or "No command-line options.")
    raise SystemExit(0)

import collections
import hashlib
import json
import math
import os
import re
import sys
import time

HOME = os.path.expanduser("~")
DR = os.path.join(HOME, ".claude", "dreaming")
sys.path.insert(0, DR)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["BRAIN_DOC_EXPANSION"] = "0"          # read_doc gives the memory's own text; questions are added here
import bm25  # noqa: E402
from quarantine_rows import keep_old  # a rerun never destroys an earlier run's rows (brain data, 26 Sep law)

Q = os.path.join(HOME, "bank_recall_keys_quarantine")
NOW = re.compile(r"\b(right now|currently|at the moment|these days|nowadays|still|today|now)\b")
UNIVERSAL = ("all", "any", "every", "everything", "anything", "everyone", "always", "never", "entire", "whole")


def half(qid):
    return "tune" if int(hashlib.sha1(qid.encode()).hexdigest(), 16) % 2 == 0 else "test"


def rank_of(top, gold):
    return next((i + 1 for i, s in enumerate(top) if s.lower() in gold), 0)


def sign_p(a, b):
    n, k = a + b, min(a, b)
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n) if n else 1.0


def main():
    a = sys.argv[1:]
    out_path = a[0]
    t0 = time.time()
    base = bm25.read_docs()
    n = len(base)
    df = collections.Counter()
    for tks in base.values():
        df.update(set(tks))
    idf = {t: math.log(n / c) for t, c in df.items()}
    texts = {}
    for slug in base:
        try:
            texts[slug] = open(os.path.join(bm25.MEM, slug + ".md"), encoding="utf-8", errors="replace").read().lower()
        except OSError:
            texts[slug] = ""
    exp = bm25.expansions()
    qinfo = {}                                     # slug -> [(question, mean idf, now?, universal-absent?)]
    all_means = []
    for slug, (qs, _t) in exp.items():
        if slug not in base:
            continue
        rows = []
        for q in qs:
            tk = bm25.tokens(q)
            m = sum(idf.get(t, math.log(n)) for t in tk) / len(tk) if tk else 0.0
            low = q.lower()
            uni = any(re.search(r"\b%s\b" % w, low) and not re.search(r"\b%s\b" % w, texts[slug]) for w in UNIVERSAL)
            rows.append((q, m, bool(NOW.search(low)), uni))
            all_means.append(m)
        qinfo[slug] = rows
    all_means.sort()
    taus = {p: all_means[int(len(all_means) * p / 100)] for p in (10, 20, 30)}

    def arm_docs(keep):
        docs = {}
        for slug, tks in base.items():
            rows = qinfo.get(slug)
            if not rows:
                docs[slug] = tks
                continue
            kept = [q for q, m, now, uni in rows if keep(m, now, uni)]
            docs[slug] = tks + (bm25.tokens(" ".join(kept))[:bm25.MAX_EXPANSION_TOKENS] if kept else [])
        return docs

    arms = [("base", None), ("all", lambda m, now, uni: True),
            ("g23", lambda m, now, uni: not now and not uni)]
    for p, tau in taus.items():
        arms.append(("g1p%d_g23" % p, (lambda tau: lambda m, now, uni: m >= tau and not now and not uni)(tau)))
    dropped = {}
    nq = sum(len(r) for r in qinfo.values())
    for name, keep in arms[1:]:
        dropped[name] = nq - sum(1 for rows in qinfo.values() for q, m, now, uni in rows if keep(m, now, uni))

    import eval_brain_recall_fresh as fresh
    from eval_prompt_recall_fullstack import episodes
    import eval_expansion_agent_recalls as ear
    sets = {"bank": [(q, {g.lower() for g in golds}) for q, golds in fresh.BANK + fresh.BANK_V2_EXTRA],
            "ep": [(p, {r.lower() for r in reads}) for _s, p, reads in episodes()],
            "agent": [(r["q"], {opened[0]}) for r, opened in ear.judged_recalls()]}
    keys = []
    if "--skip-keys" not in a:
        solved = set(json.load(open(os.path.join(Q, "headless_solver_sessions_20260926.json"), encoding="utf-8")).values())
        keys = [k for k in (json.loads(l) for l in open(os.path.join(Q, "recall_keys.jsonl"), encoding="utf-8")
                            if l.strip()) if k["id"] not in solved]
    res = {"built": time.strftime("%Y-%m-%d %H:%M:%S"), "docs": n, "questions": nq, "memories_with_questions": len(qinfo),
           "tau": {str(p): round(v, 3) for p, v in taus.items()}, "dropped": dropped,
           "n": {k: len(v) for k, v in sets.items()}, "n_keys": len(keys), "arms": {}}
    ranks = {}
    for name, keep in arms:
        docs = base if keep is None else arm_docs(keep)
        eng = bm25.BM25.from_docs(docs)
        rk = {tag: [rank_of([x for x, _ in eng.score(q, topk=60)], g) for q, g in items] for tag, items in sets.items()}
        for form in (("q_paraphrase", "q_verbatim") if keys else ()):
            rk[form] = {k["id"]: rank_of([x for x, _ in eng.score(k[form], topk=50)], {k["source_slug"].lower()})
                        for k in keys}
        ranks[name] = rk
        summ = {}
        for tag in sets:
            r = rk[tag]
            summ[tag] = {"hit1": sum(1 for x in r if x == 1), "hit5": sum(1 for x in r if 0 < x <= 5),
                         "mrr": round(sum(1.0 / x for x in r if x) / max(1, len(r)), 4)}
        for form in (("q_paraphrase", "q_verbatim") if keys else ()):
            for h in ("tune", "test"):
                r = [v for qid, v in rk[form].items() if half(qid) == h]
                summ["%s_%s" % (form, h)] = {"hit5": sum(1 for x in r if 0 < x <= 5), "n": len(r),
                                             "mrr": round(sum(1.0 / x for x in r if x) / max(1, len(r)), 4)}
        res["arms"][name] = summ
        print(name, json.dumps(summ), "(%.0f s)" % (time.time() - t0), flush=True)
        del eng, docs
    ref = ranks["all"]
    res["vs_all"] = {}
    for name in ranks:
        if name == "all":
            continue
        f = {}
        for tag in sets:
            x, y = ref[tag], ranks[name][tag]
            gi = sum(1 for p, q in zip(x, y) if 0 < q <= 5 and not 0 < p <= 5)
            go = sum(1 for p, q in zip(x, y) if 0 < p <= 5 and not 0 < q <= 5)
            f[tag] = {"in": gi, "out": go, "sign_p": round(sign_p(gi, go), 4)}
        for form in (("q_paraphrase", "q_verbatim") if keys else ()):
            for h in ("tune", "test"):
                ids = [qid for qid in ref[form] if half(qid) == h]
                gi = sum(1 for qid in ids if 0 < ranks[name][form][qid] <= 5 and not 0 < ref[form][qid] <= 5)
                go = sum(1 for qid in ids if 0 < ref[form][qid] <= 5 and not 0 < ranks[name][form][qid] <= 5)
                f["%s_%s" % (form, h)] = {"in": gi, "out": go, "sign_p": round(sign_p(gi, go), 4)}
        res["vs_all"][name] = f
    print(json.dumps(res["vs_all"]), flush=True)
    res["seconds"] = round(time.time() - t0)
    json.dump(res, open(keep_old(out_path), "w", encoding="utf-8"), indent=1)
    if keys:
        with open(keep_old(os.path.join(Q, "expansion_filter_rows.json")), "w", encoding="utf-8") as fh:
            json.dump({name: {f: r[f] for f in ("q_paraphrase", "q_verbatim")} for name, r in ranks.items()}, fh)
    print("wrote", out_path)


if __name__ == "__main__":
    main()
