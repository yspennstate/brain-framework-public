"""eval_rewrite_his_words.py - does the rewrite before search help on HIS wording? (27 Sep, claude-825b1305)

recall_rewrite.py was measured on model-reworded bank questions (+9 points at top 5). This asks the same of his own 56
owner-voice questions (eval_brain_recall_fresh BANK + BANK_V2_EXTRA) and his 82 real first prompts
(eval_prompt_recall_fullstack.episodes), on the live index: plain `brain.py recall` against recall_rewrite.py (three
Haiku rewrites, four searches interleaved). Per question: the plain rank (top 8), the fused rank (first 20 lines), and
whether any of the four searches has the answer in its own top five. Neither log is written (BRAIN_RECALL_LOG=0,
RECALL_REWRITE_NO_LOG=1). Counts only in the result; rows stay in the study folder.

    python eval_rewrite_his_words.py OUT_JSON [--workers 3] [--only bank|ep]
"""

import sys
if __name__ == "__main__" and any(arg in ("-h", "--help") for arg in sys.argv[1:]):
    print(__doc__ or "No command-line options.")
    raise SystemExit(0)

import concurrent.futures
import json
import math
import os
import re
import subprocess
import sys
import time
from quarantine_rows import keep_old  # a rerun never destroys an earlier run's rows (brain data, 26 Sep law)

HOME = os.path.expanduser("~")
DR = os.path.join(HOME, ".claude", "dreaming")
sys.path.insert(0, DR)
ROWS = os.path.join(HOME, ".claude", "brain_graph", "study", "doc_expansion", "rewrite_his_words_rows.jsonl")
NOWIN = 0x08000000 if os.name == "nt" else 0
PLAIN = re.compile(r"^\s*\d\.\d\d\s+\[[^\]]*\]\s+(\S+)", re.M)
FUSED = re.compile(r"^\s+\d+\.\s+\[(question|rewrite \d) #(\d+); [^\]]*\]\s+(\S+)\s*$", re.M)
ENV = dict(os.environ, BRAIN_RECALL_LOG="0", RECALL_REWRITE_NO_LOG="1")


def run(args, timeout=900):
    try:
        return subprocess.run([sys.executable, "-X", "utf8"] + args, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout, env=ENV, creationflags=NOWIN).stdout or ""
    except subprocess.TimeoutExpired:
        return None


def one(item):
    kind, key, q, gold = item
    res = {"kind": kind, "key": key}
    out = run([os.path.join(DR, "brain.py"), "recall", q])
    slugs = PLAIN.findall(out or "")[:8]
    res["plain"] = None if out is None else next((i + 1 for i, s in enumerate(slugs) if s in gold), 0)
    out = run([os.path.join(DR, "recall_rewrite.py"), q, "-n", "20"])
    if out is None:
        res["fused"] = res["any5"] = None
        return res
    rows = FUSED.findall(out)
    fused = [s for _src, _r, s in rows]
    res["fused"] = next((i + 1 for i, s in enumerate(fused) if s in gold), 0)
    res["any5"] = any(s in gold and int(r) <= 5 for _src, r, s in rows)
    res["rewrote"] = "rewrite 1:" in out
    return res


def sign_p(a, b):
    n, k = a + b, min(a, b)
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n) if n else 1.0


def main():
    a = sys.argv[1:]
    out_path = a[0]
    workers = int(a[a.index("--workers") + 1]) if "--workers" in a else 3
    only = a[a.index("--only") + 1] if "--only" in a else None
    import eval_brain_recall_fresh as fresh
    from eval_prompt_recall_fullstack import episodes
    items = []
    if only in (None, "bank"):
        for i, (q, golds) in enumerate(fresh.BANK + fresh.BANK_V2_EXTRA):
            golds = [g for g in golds if os.path.exists(os.path.join(fresh.MEM, g + ".md"))]
            if golds:
                items.append(("bank", "b%03d" % i, q, set(golds)))
    if only in (None, "ep"):
        for sess, prompt, reads in episodes():
            items.append(("ep", sess, prompt, set(reads)))
    t0 = time.time()
    rows = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        for n, r in enumerate(ex.map(one, items), 1):
            rows.append(r)
            if n % 10 == 0:
                print("%d/%d (%.0f s)" % (n, len(items), time.time() - t0), flush=True)
    with open(keep_old(ROWS), "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    res = {"built": time.strftime("%Y-%m-%d %H:%M:%S"), "seconds": round(time.time() - t0)}
    for kind in ("bank", "ep"):
        rs = [r for r in rows if r["kind"] == kind and r.get("plain") is not None and r.get("fused") is not None]
        if not rs:
            continue
        p5 = [bool(r["plain"] and r["plain"] <= 5) for r in rs]
        f5 = [bool(r["fused"] and r["fused"] <= 5) for r in rs]
        a5 = [bool(r["any5"]) for r in rs]
        gain = sum(1 for x, y in zip(p5, a5) if y and not x)
        loss = sum(1 for x, y in zip(p5, a5) if x and not y)
        res[kind] = {"n": len(rs), "rewrites_made": sum(1 for r in rs if r.get("rewrote")),
                     "plain_top5": sum(p5), "plain_top1": sum(1 for r in rs if r["plain"] == 1),
                     "fused_top5": sum(f5), "fused_top20": sum(1 for r in rs if r["fused"]),
                     "any_search_top5": sum(a5), "any5_gained_vs_plain": gain, "any5_lost_vs_plain": loss,
                     "sign_p": round(sign_p(gain, loss), 4)}
        print(kind, json.dumps(res[kind]), flush=True)
    json.dump(res, open(keep_old(out_path), "w", encoding="utf-8"), indent=1)


if __name__ == "__main__":
    main()
