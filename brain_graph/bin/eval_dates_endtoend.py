"""eval_dates_endtoend.py - date tokens through the real recall command, A/B on one frozen snapshot (27 Sep).

study_tokenizer_variants.py scored the word index alone. This is the end-to-end check before anything goes live: two
shadow indexes from ONE read of the memory folder - base (exactly the live settings: doc expansion on, date tokens
off) and dates (the same plus BRAIN_TOKENS_DATES=1, staged in bm25.py) - both frozen, and every question sent through
`brain.py recall`, the command agents and hooks use, with its semantic and graph lanes, once against each index. The
dates arm also sets BRAIN_TOKENS_DATES=1 for the recall process, so the question gets its date tokens too. The two
arms of a question run back to back, in alternating order.

Sets: his 56 owner-voice questions (hit@1, hit@5 of the top 8), his real first prompts (what the session read; hit@8,
MRR), agents' judged searches (the first memory the agent opened; favours the incumbent index by construction).
Rows stay in the study folder; the result holds counts only.

    python eval_dates_endtoend.py build
    python eval_dates_endtoend.py run OUT_JSON [--workers 3] [--only bank|ep|agent]
"""
import concurrent.futures
import importlib.util
import json
import math
import os
import re
import subprocess
import sys
import time

HOME = os.path.expanduser("~")
DR = os.path.join(HOME, ".claude", "dreaming")
BIN = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DR)
sys.path.insert(0, BIN)
from quarantine_rows import keep_old  # noqa: E402  # a rerun keeps the previous run's rows (R13 trap 1, 27 Sep): brain data is never deleted, and an overwrite deletes
SHADOW = os.environ.get("DATES_SHADOW_DIR") or os.path.join(
    HOME, "AppData", "Local", "Temp", "claude", (os.environ.get("BRAIN_SLUG") or os.path.expanduser("~").replace(":", "-").replace(chr(92), "-").replace("/", "-")), "825b1305-b798-4b86-9592-6dee5d977809",
    "scratchpad", "shadow_dates")
ARMS = {"base": os.path.join(SHADOW, "bm25_base.json.gz"), "dates": os.path.join(SHADOW, "bm25_dates.json.gz")}
RECEIPT = os.path.join(SHADOW, "BUILD_RECEIPT.json")
ROWS = os.path.join(HOME, ".claude", "brain_graph", "study", "doc_expansion", "dates_endtoend_rows.jsonl")
NOWIN = 0x08000000 if os.name == "nt" else 0
SLUG_RE = re.compile(r"^\s*\d\.\d\d\s+\[[^\]]*\]\s+(\S+)", re.M)


def load_bm25(dates):
    if dates:
        os.environ["BRAIN_TOKENS_DATES"] = "1"
    else:
        os.environ.pop("BRAIN_TOKENS_DATES", None)
    spec = importlib.util.spec_from_file_location("bm25_%s" % ("dates" if dates else "base"),
                                                  os.path.join(DR, "bm25.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    os.environ.pop("BRAIN_TOKENS_DATES", None)
    return m


def payload_of(docs):
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
    b0, b1 = load_bm25(False), load_bm25(True)
    slugs = sorted(s for s in (b0._indexable(f) for f in os.listdir(b0.MEM)) if s)   # one listing for both arms
    docs0, docs1 = {}, {}
    for s in slugs:
        d0 = b0.read_doc(b0.MEM, s)
        if d0:
            docs0[s] = d0
            docs1[s] = b1.read_doc(b1.MEM, s) or d0
    for arm, d in (("base", docs0), ("dates", docs1)):
        path = ARMS[arm]
        b0.unfreeze(path)
        sig = b0._publish_payload(payload_of(d), path, b0._index_signature(path), t0, len(d), 0)
        if sig is None:
            raise SystemExit("could not publish %s" % path)
        b0.freeze(300, "date-token A/B (%s arm)" % arm, index_path=path)
    rec = {"built": time.strftime("%Y-%m-%d %H:%M:%S"), "scan_t0": t0, "docs": len(docs0),
           "avgdl_base": round(payload_of(docs0)["avgdl"], 2), "avgdl_dates": round(payload_of(docs1)["avgdl"], 2),
           "doc_expansion": b0._EXPANSION_ON, "seconds": round(time.time() - t0)}
    json.dump(rec, open(keep_old(RECEIPT), "w", encoding="utf-8"), indent=1)
    print(json.dumps(rec))


def recall(q, arm, k=8):
    env = dict(os.environ, BRAIN_BM25_INDEX=ARMS[arm], BRAIN_RECALL_REFRESH="0", RECALL_REWRITE_NO_LOG="1",
               BRAIN_RECALL_LOG="0")
    env.pop("BRAIN_TOKENS_DATES", None)
    if arm == "dates":
        env["BRAIN_TOKENS_DATES"] = "1"
    try:
        out = subprocess.run([sys.executable, "-X", "utf8", os.path.join(DR, "brain.py"), "recall", q],
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900, env=env,
                             creationflags=NOWIN).stdout or ""
    except subprocess.TimeoutExpired:
        return None
    return SLUG_RE.findall(out)[:k]


def one(item):
    kind, key, q, gold, flip = item
    res = {"kind": kind, "key": key}
    for arm in (("dates", "base") if flip else ("base", "dates")):
        top = recall(q, arm)
        res[arm] = None if top is None else next((i + 1 for i, s in enumerate(top) if s.lower() in gold), 0)
    return res


def sign_p(a, b):
    n, k = a + b, min(a, b)
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n) if n else 1.0


def run(out_path, workers, only):
    items = []
    if only in (None, "bank"):
        import eval_brain_recall_fresh as fresh
        for i, (q, golds) in enumerate(fresh.BANK + fresh.BANK_V2_EXTRA):
            golds = {g.lower() for g in golds if os.path.exists(os.path.join(fresh.MEM, g + ".md"))}
            if golds:
                items.append(("bank", "b%03d" % i, q, golds, i % 2 == 1))
    if only in (None, "ep"):
        from eval_prompt_recall_fullstack import episodes
        for j, (s, prompt, reads) in enumerate(episodes()):
            items.append(("ep", s, prompt, {x.lower() for x in reads}, j % 2 == 1))
    if only in (None, "agent"):
        import eval_expansion_agent_recalls as ear
        for j, (r, opened) in enumerate(ear.judged_recalls()):
            items.append(("agent", "%.1f" % r["t"], r["q"], set(opened), j % 2 == 1))
    t0 = time.time()
    rows = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        for n, r in enumerate(ex.map(one, items), 1):
            rows.append(r)
            if n % 20 == 0:
                print("%d/%d (%.0f s)" % (n, len(items), time.time() - t0), flush=True)
    with open(keep_old(ROWS), "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    res = {"built": time.strftime("%Y-%m-%d %H:%M:%S"), "seconds": round(time.time() - t0),
           "receipt": json.load(open(RECEIPT, encoding="utf-8")), "sets": {}}
    for kind in ("bank", "ep", "agent"):
        rs = [r for r in rows if r["kind"] == kind and r.get("base") is not None and r.get("dates") is not None]
        if not rs:
            continue
        d = {"n": len(rs)}
        for k in (1, 5, 8):
            a = [bool(r["base"] and r["base"] <= k) for r in rs]
            b = [bool(r["dates"] and r["dates"] <= k) for r in rs]
            gi = sum(1 for x, y in zip(a, b) if y and not x)
            go = sum(1 for x, y in zip(a, b) if x and not y)
            d["top%d" % k] = {"base": sum(a), "dates": sum(b), "in": gi, "out": go, "sign_p": round(sign_p(gi, go), 4)}
        d["mrr_base"] = round(sum(1.0 / r["base"] for r in rs if r["base"]) / len(rs), 4)
        d["mrr_dates"] = round(sum(1.0 / r["dates"] for r in rs if r["dates"]) / len(rs), 4)
        d["better"] = sum(1 for r in rs if (r["dates"] or 99) < (r["base"] or 99))
        d["worse"] = sum(1 for r in rs if (r["dates"] or 99) > (r["base"] or 99))
        res["sets"][kind] = d
        print(kind, json.dumps(d), flush=True)
    json.dump(res, open(keep_old(out_path), "w", encoding="utf-8"), indent=1)


if __name__ == "__main__":
    a = sys.argv[1:]
    if a and a[0] == "build":
        build()
    elif a and a[0] == "run":
        run(a[1], int(a[a.index("--workers") + 1]) if "--workers" in a else 3,
            a[a.index("--only") + 1] if "--only" in a else None)
    else:
        print(__doc__)
