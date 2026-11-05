"""eval_doc_expansion.py - does writing the questions a memory answers into the index find it more often? (26 Sep)

The live index's own text for every memory (bm25.read_docs), scored by the live scorer (bm25.BM25.from_docs), with and
without the expansions doc_expand.py wrote, on the 22 Sep bank's reworded questions no solver saw, split into tune and
test halves by the parity of sha1(id) as every earlier recall study split them. Reports hit@1/5/20 and MRR per half, how
often an expanded memory that is not the answer enters the top five ahead of the answer, and the same for the verbatim
form of each question, which should not get worse. Per-question ranks stay in the key quarantine; counts only in the
result file.

    python eval_doc_expansion.py OUT_JSON [--repeat 1,2]
"""
import argparse
import hashlib
import json
import os
import sys
import time

HOME = os.path.expanduser("~")
sys.path.insert(0, os.path.join(HOME, ".claude", "dreaming"))
os.environ["BRAIN_DOC_EXPANSION"] = "0"   # the base arm is the memory's own text, whatever the live default is
import bm25  # noqa: E402
from quarantine_rows import keep_old  # a rerun never destroys an earlier run's rows (brain data, 26 Sep law)

Q = os.path.join(HOME, "bank_recall_keys_quarantine")
EXP = os.path.join(HOME, ".claude", "brain_graph", "study", "doc_expansion", "expansions.jsonl")


def half(qid):
    return "tune" if int(hashlib.sha1(qid.encode()).hexdigest(), 16) % 2 == 0 else "test"


def metrics(ranks):
    n = len(ranks)
    if not n:
        return {}
    f = lambda k: round(sum(1 for r in ranks if r is not None and r <= k) / n, 4)  # noqa: E731
    return {"n": n, "hit1": f(1), "hit5": f(5), "hit20": f(20),
            "mrr": round(sum(1.0 / r for r in ranks if r is not None) / n, 4)}


def load_expansions():
    exp = {}
    for line in open(EXP, encoding="utf-8"):
        try:
            o = json.loads(line)
        except ValueError:
            continue
        exp[o["slug"]] = o["q"]
    return exp


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--repeat", default="1,2", help="how many times the expansion text is counted, per variant")
    a = ap.parse_args()
    t0 = time.time()
    keys = [json.loads(l) for l in open(os.path.join(Q, "recall_keys.jsonl"), encoding="utf-8") if l.strip()]
    solved = set(json.load(open(os.path.join(Q, "headless_solver_sessions_20260926.json"))).values())
    items = [k for k in keys if k["id"] not in solved]
    docs = bm25.read_docs()
    exp = load_expansions()
    expanded = set(exp) & set(docs)
    variants = {"base": docs}
    for r in [int(x) for x in a.repeat.split(",") if x.strip()]:
        d2 = dict(docs)
        for slug in expanded:
            d2[slug] = docs[slug] + bm25.tokens(" ".join(exp[slug])) * r
        variants["exp_x%d" % r] = d2
    d3 = dict(docs)
    for slug in expanded:                  # only the question words the memory does not already contain
        own = set(docs[slug])
        d3[slug] = docs[slug] + [t for t in bm25.tokens(" ".join(exp[slug])) if t not in own][:120]
    variants["exp_new"] = d3
    engines = {name: bm25.BM25.from_docs(d) for name, d in variants.items()}
    print("docs %d, expanded %d, variants %s (%.0f s)" % (len(docs), len(expanded), list(engines), time.time() - t0),
          flush=True)
    res = {"built": time.strftime("%Y-%m-%d %H:%M:%S"), "docs": len(docs), "expanded_memories": len(expanded),
           "questions": len(items), "sources_expanded": sum(1 for k in items if k["source_slug"] in expanded)}
    rows = []
    per = {}
    intrude = {}
    for form in ("q_paraphrase", "q_verbatim"):
        for name, eng in engines.items():
            ranks = {"tune": [], "test": [], "all": []}
            n_intr = 0
            for k in items:
                top = [s for s, _ in eng.score(k[form], topk=50)]
                src = k["source_slug"]
                r = top.index(src) + 1 if src in top else None
                h = half(k["id"])
                ranks[h].append(r)
                ranks["all"].append(r)
                ahead = top[:min(5, (r or 51) - 1)]
                n_intr += sum(1 for s in ahead if s in expanded and s != src)
                rows.append({"id": k["id"], "form": form, "variant": name, "rank": r})
            per["%s/%s" % (form, name)] = {h: metrics(v) for h, v in ranks.items()}
            intrude["%s/%s" % (form, name)] = n_intr
            print("%-12s %-7s tune %s | test %s | expanded non-answers ahead in top 5: %d (%.0f s)" % (
                form, name, per["%s/%s" % (form, name)]["tune"], per["%s/%s" % (form, name)]["test"], n_intr,
                time.time() - t0), flush=True)
    res["metrics"] = per
    res["expanded_non_answers_ahead_in_top5"] = intrude
    res["seconds"] = round(time.time() - t0)
    json.dump(res, open(keep_old(a.out), "w", encoding="utf-8"), indent=1)
    with open(keep_old(os.path.join(Q, "doc_expansion_rows.jsonl")), "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    print("wrote", a.out)


if __name__ == "__main__":
    main()
