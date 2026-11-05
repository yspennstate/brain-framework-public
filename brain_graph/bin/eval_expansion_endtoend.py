"""eval_expansion_endtoend.py - doc expansion through the real recall command, A/B on one frozen snapshot (26 Sep).

The pilot (eval_doc_expansion.py) scored the word index alone, with only the answer memories and a random thousand
expanded. This is the fair test: every memory expanded, two shadow indexes built from ONE read of the memory folder
(base = exactly what the live index holds; exp = the same plus each memory's three plain-words questions), both
frozen, and every question sent through `brain.py recall` - the command agents and hooks use, with its semantic and
graph lanes - once against each index. The two arms of a question run back to back, in alternating order, so any
drift in the rest of the stack between arms is paired away.

Question sets: the 56-question owner-voice bank (eval_brain_recall_fresh.py: BANK + BANK_V2_EXTRA; hit@1, hit@5 of
the top 8) and the real-prompt episodes (eval_prompt_recall_fullstack.episodes: what he typed first in a session,
scored against what that session went on to read; hit@8 and MRR). Per-question rows stay in the study folder; the
result file holds counts only.

    python eval_expansion_endtoend.py build
    python eval_expansion_endtoend.py run OUT_JSON [--workers 3] [--k 8] [--only bank|ep] [--limit N]
"""
import concurrent.futures
import json
import os
import re
import subprocess
import sys
import time

HOME = os.path.expanduser("~")
DR = os.path.join(HOME, ".claude", "dreaming")
sys.path.insert(0, DR)
os.environ["BRAIN_DOC_EXPANSION"] = "0"   # the base arm is the memory's own text, whatever the live default is
import bm25  # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from quarantine_rows import keep_old  # noqa: E402  # a rerun keeps the previous run's rows (R13 trap 1, 27 Sep): brain data is never deleted, and an overwrite deletes

STUDY = os.path.join(HOME, ".claude", "brain_graph", "study", "doc_expansion")
SHADOW = os.path.join(STUDY, "shadow")
EXP = os.path.join(STUDY, "expansions.jsonl")
ARMS = {"base": os.path.join(SHADOW, "bm25_base.json.gz"), "exp": os.path.join(SHADOW, "bm25_exp.json.gz"),
        "expn": os.path.join(SHADOW, "bm25_expn.json.gz")}
RECEIPT = os.path.join(SHADOW, "BUILD_RECEIPT.json")
ROWS = os.path.join(STUDY, "endtoend_rows.jsonl")
MAX_EXP_TOKENS = 120
NOWIN = 0x08000000 if os.name == "nt" else 0
SLUG_RE = re.compile(r"^\s*\d\.\d\d\s+\[[^\]]*\]\s+(\S+)", re.M)


def load_expansions():
    exp = {}
    with open(EXP, encoding="utf-8") as fh:
        for line in fh:
            try:
                o = json.loads(line)
            except ValueError:
                continue
            if o.get("q"):
                exp[o["slug"]] = o["q"]
    return exp


def payload_of(docs):
    """The payload _build_inner writes, from a docs dict."""
    df, tfs, dl = {}, {}, {}
    for slug, tks in docs.items():
        tf = {}
        for t in tks:
            tf[t] = tf.get(t, 0) + 1
        tfs[slug] = tf
        dl[slug] = len(tks)
        for t in tf:
            df[t] = df.get(t, 0) + 1
    n = len(docs)
    return {"n": n, "avgdl": (sum(dl.values()) / n) if n else 0.0, "df": df, "dl": dl, "tf": tfs}


def build():
    os.makedirs(SHADOW, exist_ok=True)
    t0 = time.time()
    docs = bm25.read_docs()
    exp = load_expansions()
    ex_docs, exn_docs = {}, {}
    n_exp = 0
    for slug, tks in docs.items():
        q = exp.get(slug)
        if q:
            qt = bm25.tokens(" ".join(q))
            ex_docs[slug] = tks + qt[:MAX_EXP_TOKENS]
            own = set(tks)
            exn_docs[slug] = tks + [t for t in qt if t not in own][:MAX_EXP_TOKENS]
            n_exp += 1
        else:
            ex_docs[slug] = exn_docs[slug] = tks
    for arm, d in (("base", docs), ("exp", ex_docs), ("expn", exn_docs)):
        path = ARMS[arm]
        bm25.unfreeze(path)
        sig = bm25._publish_payload(payload_of(d), path, bm25._index_signature(path), t0, len(d), 0)
        if sig is None:
            raise SystemExit("could not publish %s" % path)
        bm25.freeze(240, "expansion A/B (%s arm)" % arm, index_path=path)
    rec = {"built": time.strftime("%Y-%m-%d %H:%M:%S"), "scan_t0": t0, "docs": len(docs), "expanded": n_exp,
           "coverage": round(n_exp / max(1, len(docs)), 4), "max_exp_tokens": MAX_EXP_TOKENS,
           "avgdl_base": round(payload_of(docs)["avgdl"], 2), "seconds": round(time.time() - t0)}
    json.dump(rec, open(keep_old(RECEIPT), "w", encoding="utf-8"), indent=1)
    print(json.dumps(rec))


def recall(q, arm, k):
    env = dict(os.environ, BRAIN_BM25_INDEX=ARMS[arm], BRAIN_RECALL_REFRESH="0", RECALL_REWRITE_NO_LOG="1",
               BRAIN_RECALL_LOG="0")                     # the live recall log holds agents' own searches only
    t0 = time.time()
    try:
        out = subprocess.run([sys.executable, os.path.join(DR, "brain.py"), "recall", q], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=600, env=env, creationflags=NOWIN).stdout or ""
    except subprocess.TimeoutExpired:
        return None, time.time() - t0
    return SLUG_RE.findall(out)[:k], time.time() - t0


def one(item, k):
    kind, key, q, gold, flip = item
    res = {"kind": kind, "key": key}
    for arm in (("expn", "exp", "base") if flip else ("base", "exp", "expn")):
        slugs, secs = recall(q, arm, k)
        res[arm] = None if slugs is None else next((i + 1 for i, s in enumerate(slugs) if s in gold), 0)
        res[arm + "_s"] = round(secs, 1)
    return res


def summarize(rows, kind, k):
    rs = [r for r in rows if r["kind"] == kind and all(r.get(a) is not None for a in ARMS)]
    out = {"n": len(rs), "timeouts": sum(1 for r in rows if r["kind"] == kind and any(r.get(a) is None for a in ARMS))}
    for arm in ARMS:
        ranks = [r[arm] for r in rs]
        out[arm] = {"hit1": sum(1 for x in ranks if x == 1), "hit5": sum(1 for x in ranks if 0 < x <= 5),
                    "hitk": sum(1 for x in ranks if 0 < x <= k),
                    "mrr": round(sum(1.0 / x for x in ranks if x) / max(1, len(ranks)), 4)}
    for arm in ("exp", "expn"):
        out[arm + "_better"] = sum(1 for r in rs if (r[arm] or 99) < (r["base"] or 99))
        out[arm + "_worse"] = sum(1 for r in rs if (r[arm] or 99) > (r["base"] or 99))
        out[arm + "_found_only"] = sum(1 for r in rs if r[arm] and not r["base"])
        out[arm + "_lost"] = sum(1 for r in rs if r["base"] and not r[arm])
    return out


def run(out_path, workers, k, only, limit=None):
    sys.path.insert(0, DR)
    import eval_brain_recall_fresh as fresh
    from eval_prompt_recall_fullstack import episodes
    mem = fresh.MEM
    items = []
    if only in (None, "bank"):
        for i, (q, golds) in enumerate(fresh.BANK + fresh.BANK_V2_EXTRA):
            golds = [g for g in golds if os.path.exists(os.path.join(mem, g + ".md"))]
            if golds:
                items.append(("bank", "b%03d" % i, q, set(golds), i % 2 == 1))
    if only in (None, "ep"):
        for j, (sess, prompt, reads) in enumerate(episodes()):
            items.append(("ep", sess, prompt, set(reads), j % 2 == 1))
    if limit:
        items = items[:limit]
    for arm, path in ARMS.items():
        frozen, why = bm25.freeze_state(path)
        if not frozen:
            bm25.freeze(240, "expansion A/B (%s arm)" % arm, index_path=path)
    t0 = time.time()
    rows = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(one, it, k) for it in items]
        for n, f in enumerate(concurrent.futures.as_completed(futs), 1):
            rows.append(f.result())
            if n % 10 == 0 or n == len(items):
                print("%d/%d done (%.0f s)" % (n, len(items), time.time() - t0), flush=True)
    with open(keep_old(ROWS), "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    res = {"built": time.strftime("%Y-%m-%d %H:%M:%S"), "k": k, "receipt": json.load(open(RECEIPT, encoding="utf-8")),
           "seconds": round(time.time() - t0)}
    for kind in ("bank", "ep"):
        if any(r["kind"] == kind for r in rows):
            res[kind] = summarize(rows, kind, k)
            print(kind, json.dumps(res[kind]), flush=True)
    json.dump(res, open(keep_old(out_path), "w", encoding="utf-8"), indent=1)
    print("wrote", out_path)


def main():
    a = sys.argv[1:]
    if not a or a[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    if a[0] == "build":
        build()
        return 0
    if a[0] == "run" and len(a) >= 2:
        workers = int(a[a.index("--workers") + 1]) if "--workers" in a else 3
        k = int(a[a.index("--k") + 1]) if "--k" in a else 8
        only = a[a.index("--only") + 1] if "--only" in a else None
        limit = int(a[a.index("--limit") + 1]) if "--limit" in a else None
        run(a[1], workers, k, only, limit)
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
