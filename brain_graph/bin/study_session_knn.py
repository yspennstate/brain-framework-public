"""study_session_knn.py - do sessions that started like this one predict what it will read? (27 Sep, claude-825b1305)

His methodology: edges grow along the sequences things are used in, so a new ask should light what past asks like it
led to. The cleanest form of that to test: for a session's first prompt, find the earlier sessions whose first prompts
read most like it and vote for the memories those sessions read. Walk-forward: a session at time t sees only sessions
that started before t, and only their reads before t. Compared, on the same sessions, with the word index on the prompt
(what recall does), with usage alone (ACT-R base activation at t, the "nodes grow with use" signal), and with the word
index and the neighbours fused (reciprocal rank).

Sessions: his first prompt of at least six words (memory/prompts/PROMPTS.jsonl, origin owner) and the memories the
session read (dreaming/retrievals.tsv, test workers already left out), 2 to 25 reads, as the ep set of the recall
studies. Gold: every memory the session read. Counts only in the result.

    python study_session_knn.py OUT_JSON [--k 10] [--min-history 15]
"""

import sys
if __name__ == "__main__" and any(arg in ("-h", "--help") for arg in sys.argv[1:]):
    print(__doc__ or "No command-line options.")
    raise SystemExit(0)

import bisect
import collections
import json
import math
import os
import sys
import time

HOME = os.path.expanduser("~")
DR = os.path.join(HOME, ".claude", "dreaming")
sys.path.insert(0, DR)
import bm25  # noqa: E402

LEDGER = os.path.join(HOME, ".claude", "projects", (os.environ.get("BRAIN_SLUG") or os.path.expanduser("~").replace(":", "-").replace(chr(92), "-").replace("/", "-")), "memory", "prompts", "PROMPTS.jsonl")
TRACE = os.path.join(DR, "retrievals.tsv")
import re  # noqa: E402
CLAUSE = re.compile(r"[.?!;\n]+|\balso\b|\band then\b|\bthen\b|\bafter that\b|\bplus\b|\bbtw\b", re.I)


import os as _ko_os, sys as _ko_sys  # noqa: E402
_ko_sys.path.insert(0, _ko_os.path.join(_ko_os.path.expanduser("~"), ".claude", "brain_graph", "bin"))
from quarantine_rows import keep_old  # noqa: E402  a rerun keeps the previous output (R13 trap 1, 27 Sep): brain data is never deleted


def iso_epoch(s):
    from datetime import datetime, timezone
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return d.timestamp()          # a naive stamp is his local time (the ledger writes local), not UTC
    except Exception:
        return None


def sign_p(a, b):
    n, k = a + b, min(a, b)
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n) if n else 1.0


def main():
    a = sys.argv[1:]
    out = a[0]
    K = int(a[a.index("--k") + 1]) if "--k" in a else 10
    MINH = int(a[a.index("--min-history") + 1]) if "--min-history" in a else 15
    t0 = time.time()
    reads = collections.defaultdict(list)            # session12 -> [(epoch, slug)]
    trace = []                                       # (epoch, slug) every read, for usage
    for i, ln in enumerate(open(TRACE, encoding="utf-8", errors="replace")):
        if i == 0:
            continue
        p = ln.rstrip("\n").split("\t")
        if len(p) < 4:
            continue
        slug, ep, sess = p[0], float(p[1]), p[3][:12]
        if slug in bm25.SKIP_FILES or slug.startswith("dream_"):
            continue
        reads[sess].append((ep, slug))
        trace.append((ep, slug))
    trace.sort()
    first = {}                                       # session12 -> (start epoch, first prompt of six words or more)
    start = {}                                       # session12 -> epoch of his first prompt of any length
    for ln in open(LEDGER, encoding="utf-8", errors="replace"):
        try:
            r = json.loads(ln)
        except Exception:
            continue
        if r.get("origin", "owner") != "owner" or not r.get("session") or not r.get("text"):
            continue
        s = str(r["session"])[:12]
        t = iso_epoch(r.get("ts"))
        if t is None:
            continue
        if s not in start or t < start[s]:
            start[s] = t
        if len(bm25.tokens(r["text"])) < 6:
            continue
        if s not in first or t < first[s][0]:
            first[s] = (t, r["text"])
    # the clock of a session is its start: what came before it is all the prediction may see
    first = {s: (start[s], p) for s, (_t, p) in first.items()}
    early = sum(1 for s in first if s in reads and min(e for e, _m in reads[s]) < first[s][0])
    print("sessions whose first read precedes their first prompt:", early, "(left out)", flush=True)
    first = {s: v for s, v in first.items() if not (s in reads and min(e for e, _m in reads[s]) < v[0])}
    sessions = sorted((first[s][0], s) for s in first if s in reads)
    print("sessions with a first prompt and reads:", len(sessions), flush=True)
    # BM25 over first prompts (documents = sessions' first prompts)
    pdocs = {s: bm25.tokens(first[s][1]) for _t, s in sessions}
    peng = bm25.BM25.from_docs(pdocs)
    # the word index over memories, as live
    meng = bm25.BM25.from_docs(bm25.read_docs())
    print("indexes built (%.0f s)" % (time.time() - t0), flush=True)
    tr_rows = [(ep, slug) for ep, slug in trace]
    res_rows = []
    for idx, (t, s) in enumerate(sessions):
        gold = {slug for ep, slug in reads[s]}
        if not (2 <= len(gold) <= 25):
            continue
        hist = [x for tt, x in sessions[:idx]]
        if len(hist) < MINH:
            continue
        prompt = first[s][1]
        # neighbours: earlier sessions whose first prompts match this one
        hset = set(hist)
        sims = [(x, sc) for x, sc in peng.score(prompt, topk=200) if x in hset][:K]
        vote = collections.Counter()
        for x, sc in sims:
            for ep, slug in reads[x]:
                if ep < t:
                    vote[slug] += sc
        knn = [m for m, _v in vote.most_common(50)]
        word = [m for m, _v in meng.score(prompt, topk=50)]
        # usage alone: base activation from reads strictly before t
        past = [(ep, slug) for ep, slug in tr_rows if ep < t]
        use = {}
        for ep, slug in past:
            use[slug] = use.get(slug, 0.0) + (max(t - ep, 1.0)) ** -0.5
        usage = [m for m, _v in sorted(use.items(), key=lambda kv: -kv[1])[:50]]
        rrf = collections.Counter()
        for lst in (word, knn):
            for r_, m in enumerate(lst):
                rrf[m] += 1.0 / (60 + r_)
        fused = [m for m, _v in rrf.most_common(50)]
        # the prompt's separate asks, each searched, merged round-robin by rank (27 Sep 05:1x)
        parts = [c.strip() for c in CLAUSE.split(prompt) if len(bm25.tokens(c)) >= 3]
        if len(parts) > 1:
            lists = [[m for m, _v in meng.score(c, topk=20)] for c in parts]
            clauses, seen = [], set()
            for r_ in range(20):
                for lst in lists:
                    if r_ < len(lst) and lst[r_] not in seen:
                        seen.add(lst[r_])
                        clauses.append(lst[r_])
        else:
            clauses = word

        def hit(lst, n):
            return bool(gold & set(lst[:n]))

        def cover(lst, n):
            return len(gold & set(lst[:n])) / len(gold)
        res_rows.append({"t": t, "s": s, "n_gold": len(gold), "n_hist": len(hist), "n_nb": len(sims),
                         **{"%s_hit%d" % (nm, n): hit(l, n) for nm, l in (("word", word), ("knn", knn), ("usage", usage),
                                                                          ("fused", fused), ("clauses", clauses))
                            for n in (1, 5, 10)},
                         **{"%s_cov10" % nm: cover(l, 10) for nm, l in (("word", word), ("knn", knn), ("usage", usage),
                                                                        ("fused", fused), ("clauses", clauses))},
                         "n_clauses": len(parts)})
    n = len(res_rows)
    summ = {"n": n, "k": K, "min_history": MINH, "built": time.strftime("%Y-%m-%d %H:%M:%S")}
    summ["multi_ask_sessions"] = sum(1 for r in res_rows if r["n_clauses"] > 1)
    for nm in ("word", "knn", "usage", "fused", "clauses"):
        summ[nm] = {"hit1": sum(r["%s_hit1" % nm] for r in res_rows), "hit5": sum(r["%s_hit5" % nm] for r in res_rows),
                    "hit10": sum(r["%s_hit10" % nm] for r in res_rows),
                    "cov10": round(sum(r["%s_cov10" % nm] for r in res_rows) / max(1, n), 4)}
    for a_, b_ in (("knn", "word"), ("fused", "word"), ("knn", "usage"), ("clauses", "word")):
        gi = sum(1 for r in res_rows if r["%s_hit5" % a_] and not r["%s_hit5" % b_])
        go = sum(1 for r in res_rows if r["%s_hit5" % b_] and not r["%s_hit5" % a_])
        summ["%s_vs_%s_hit5" % (a_, b_)] = {"only_%s" % a_: gi, "only_%s" % b_: go, "sign_p": round(sign_p(gi, go), 4)}
    summ["seconds"] = round(time.time() - t0)
    json.dump(summ, open(keep_old(out), "w", encoding="utf-8"), indent=1)
    print(json.dumps(summ, indent=1))
    if "--rows" in a:
        # one row per session for outside re-derivation (account04/B19's R5 audit, 27 Sep): no prompt, no memory names,
        # the session id salted and hashed; the cutoff is the session's start, given as epoch and as his local time with
        # its offset, so nobody has to guess the clock the ledger wrote
        import hashlib
        from datetime import datetime
        salt = "session-knn-20260927"
        with open(keep_old(a[a.index("--rows") + 1]), "w", encoding="utf-8") as fh:
            for r in sorted(res_rows, key=lambda r: r["t"]):
                row = {k: v for k, v in r.items() if k not in ("s", "t")}
                row = {"session": hashlib.sha256((salt + r["s"]).encode()).hexdigest()[:12],
                       "cutoff_epoch": round(r["t"], 1),
                       "cutoff_local": datetime.fromtimestamp(r["t"]).astimezone().isoformat(timespec="seconds"),
                       **row}
                fh.write(json.dumps(row) + "\n")
        print("rows:", len(res_rows))


if __name__ == "__main__":
    main()
