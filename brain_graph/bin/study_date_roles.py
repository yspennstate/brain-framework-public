"""study_date_roles.py - does a memory's own creation date outweigh the dates it talks about?

A scheduled audit found that the date tokens live since 27 Sep 03:11 count a memory's creation
date several times: the slug, which carries it, is read three times, and the frontmatter's created lines are read too,
while a date the memory talks about is usually written once. At average length BM25 then weighs the creation date about
57 per cent above a once-mentioned event date. Five arms on the word index, built in memory (no index file is written):
  merged    the live documents (dates on, as indexed since 03:11)
  dedup     the creation date once, the dates of the text as written
  typed     the creation date as its own token (cmd0902, once) beside the text's dates (md0902); a question's date emits both
  body      the text's dates only, no creation date
  none      no date tokens
Scored on his 56 questions, his 82 first prompts, the logged searches (first memory opened; mostly test workers) and the 968 reworded bank
questions in both forms (quarantined keys), and reported separately for the questions that carry a date, the only ones a
date arm can move directly. Counts only in the result; per-question ranks go to the quarantine.

    python study_date_roles.py OUT_JSON [--skip-keys]
"""
if "-h" in __import__("sys").argv[1:] or "--help" in __import__("sys").argv[1:]:
    print((__doc__ or "").strip() or "usage: python %s" % __import__("os").path.basename(__file__))
    raise SystemExit(0)
import hashlib
import io
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
import bm25  # noqa: E402
from quarantine_rows import keep_old  # a rerun never destroys an earlier run's rows (brain data, 26 Sep law)

Q = os.path.join(HOME, "bank_recall_keys_quarantine")
CREATED = re.compile(r"^\s*created(_at)?\s*:.*$", re.M)


def half(qid):
    return "tune" if int(hashlib.sha1(qid.encode()).hexdigest(), 16) % 2 == 0 else "test"


def rank_of(top, gold):
    return next((i + 1 for i, s in enumerate(top) if s.lower() in gold), 0)


def sign_p(a, b):
    n, k = a + b, min(a, b)
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n) if n else 1.0


def parts(slug):
    """(creation-date tokens, the text's own date tokens) for one memory, from the head the live index reads."""
    with io.open(os.path.join(bm25.MEM, slug + ".md"), encoding="utf-8", errors="replace") as fh:
        raw = fh.read()
    body = re.sub(r"^---.*?---", lambda m: re.sub(r"^(name|metadata|node_type|type|originSessionId).*$", "",
                                                  m.group(0), flags=re.M), raw, count=1, flags=re.S)
    sw = slug.replace("_", " ")
    created = set(bm25.date_tokens(sw))
    for m in CREATED.finditer(body):
        created.update(bm25.date_tokens(m.group(0)))
    budget = max(0, 12000 - 3 * (len(sw) + 1))
    text_dates = bm25.date_tokens(CREATED.sub("", body[:budget]))
    return sorted(created), text_dates


def main():
    a = sys.argv[1:]
    out_path = a[0]
    t0 = time.time()
    merged = bm25.read_docs()                                   # the live documents, dates on
    nodates = {}
    creat, textd = {}, {}
    for slug, tks in merged.items():
        c, t = parts(slug)
        creat[slug], textd[slug] = c, t
    # the live document minus its date tokens = the no-dates document (tokens are appended after the head budget,
    # so removing every md token restores the head, the attachments and the questions exactly)
    md = re.compile(r"^md[01][0-9][0-3][0-9]$")
    for slug, tks in merged.items():
        nodates[slug] = [x for x in tks if not md.match(x)]
    arms = {
        "merged": (merged, False, True),
        "dedup": ({s: nodates[s] + creat[s] + textd[s] for s in merged}, False, True),
        "typed": ({s: nodates[s] + ["c" + x for x in creat[s]] + textd[s] for s in merged}, True, True),
        "body": ({s: nodates[s] + textd[s] for s in merged}, False, True),
        "none": (nodates, False, False),
    }
    import eval_brain_recall_fresh as fresh
    from eval_prompt_recall_fullstack import episodes
    import eval_expansion_agent_recalls as ear
    sets = {"bank": [(q, {g.lower() for g in golds}) for q, golds in fresh.BANK + fresh.BANK_V2_EXTRA],
            "ep": [(p, {r.lower() for r in reads}) for _s, p, reads in episodes()],
            "logged": [(r["q"], {opened[0].lower()}) for r, opened in ear.judged_recalls()]}
    keys = []
    if "--skip-keys" not in a:
        solved = set(json.load(open(os.path.join(Q, "headless_solver_sessions_20260926.json"), encoding="utf-8")).values())
        keys = [k for k in (json.loads(l) for l in open(os.path.join(Q, "recall_keys.jsonl"), encoding="utf-8")
                            if l.strip()) if k["id"] not in solved]
    base_tok = bm25._base_tokens

    def qtok(typed, dates):
        def f(text):
            b = base_tok(text)
            if not dates:
                return b
            d = bm25.date_tokens(text)
            return b + d + (["c" + x for x in d] if typed else [])
        return f

    dated = {tag: [bool(bm25.date_tokens(q)) for q, _g in items] for tag, items in sets.items()}
    res = {"built": time.strftime("%Y-%m-%d %H:%M:%S"), "docs": len(merged),
           "n": {k: len(v) for k, v in sets.items()}, "n_dated": {k: sum(v) for k, v in dated.items()},
           "n_keys": len(keys), "creation_tf_mean": round(sum(sum(1 for x in merged[s] if md.match(x) and x in creat[s])
                                                              for s in merged) / len(merged), 2), "arms": {}}
    if keys:
        res["n_keys_dated"] = {f: sum(1 for k in keys if bm25.date_tokens(k[f])) for f in ("q_paraphrase", "q_verbatim")}
    ranks = {}
    real_tokens = bm25.tokens
    for name, (docs, typed, dates) in arms.items():
        bm25.tokens = qtok(typed, dates)
        eng = bm25.BM25.from_docs(docs)
        rk = {tag: [rank_of([x for x, _ in eng.score(q, topk=60)], g) for q, g in items] for tag, items in sets.items()}
        for form in (("q_paraphrase", "q_verbatim") if keys else ()):
            rk[form] = {k["id"]: rank_of([x for x, _ in eng.score(k[form], topk=50)], {k["source_slug"].lower()})
                        for k in keys}
        bm25.tokens = real_tokens
        ranks[name] = rk
        summ = {}
        for tag in sets:
            r = rk[tag]
            rd = [x for x, d in zip(r, dated[tag]) if d]
            summ[tag] = {"hit1": sum(1 for x in r if x == 1), "hit5": sum(1 for x in r if 0 < x <= 5),
                         "mrr": round(sum(1.0 / x for x in r if x) / max(1, len(r)), 4),
                         "dated_hit5": sum(1 for x in rd if 0 < x <= 5)}
        for form in (("q_paraphrase", "q_verbatim") if keys else ()):
            for h in ("tune", "test"):
                r = [v for qid, v in rk[form].items() if half(qid) == h]
                summ["%s_%s" % (form, h)] = {"hit5": sum(1 for x in r if 0 < x <= 5), "n": len(r),
                                             "mrr": round(sum(1.0 / x for x in r if x) / max(1, len(r)), 4)}
        res["arms"][name] = summ
        print(name, json.dumps(summ), "(%.0f s)" % (time.time() - t0), flush=True)
        del eng
    ref = ranks["merged"]
    res["vs_merged"] = {}
    for name in ranks:
        if name == "merged":
            continue
        f = {}
        for tag in sets:
            x, y = ref[tag], ranks[name][tag]
            gi = sum(1 for p, q in zip(x, y) if 0 < q <= 5 and not 0 < p <= 5)
            go = sum(1 for p, q in zip(x, y) if 0 < p <= 5 and not 0 < q <= 5)
            h1i = sum(1 for p, q in zip(x, y) if q == 1 and p != 1)
            h1o = sum(1 for p, q in zip(x, y) if p == 1 and q != 1)
            f[tag] = {"in": gi, "out": go, "sign_p": round(sign_p(gi, go), 4), "top1_in": h1i, "top1_out": h1o}
        for form in (("q_paraphrase", "q_verbatim") if keys else ()):
            ids = list(ref[form])
            gi = sum(1 for qid in ids if 0 < ranks[name][form][qid] <= 5 and not 0 < ref[form][qid] <= 5)
            go = sum(1 for qid in ids if 0 < ref[form][qid] <= 5 and not 0 < ranks[name][form][qid] <= 5)
            f[form] = {"in": gi, "out": go, "sign_p": round(sign_p(gi, go), 4)}
        res["vs_merged"][name] = f
    print(json.dumps(res["vs_merged"]), flush=True)
    res["seconds"] = round(time.time() - t0)
    json.dump(res, open(keep_old(out_path), "w", encoding="utf-8"), indent=1)
    if keys:
        with open(keep_old(os.path.join(Q, "date_roles_rows.json")), "w", encoding="utf-8") as fh:
            json.dump({n: {f: r[f] for f in ("q_paraphrase", "q_verbatim")} for n, r in ranks.items()}, fh)
    print("wrote", out_path)


if __name__ == "__main__":
    main()
