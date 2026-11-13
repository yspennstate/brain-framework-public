"""eval_usage_order.py - does the usage tilt help once it is allowed to change the order?

A scheduled audit showed that brain.py's usage tilt - ACT-R base-level activation of how often
and how lately each memory was read, max +15 percent - multiplies the display score after the order is fixed, so on the
default path it has never moved a rank. This measures the version that re-sorts the body of the bm25 block by the tilted
score (brain_usage_study.py, a copy of brain.py with two study switches; the live file is untouched):
  BRAIN_RECALL_USAGE_ORDER=1   re-sort the body (block score <= the bm25 peak; policy seeds and attachments stay first)
  BRAIN_USAGE_ASOF=<epoch>     usage computed only from reads strictly before that time, so an answer read while the
                               question was being tested cannot raise its own score
Paired arms on the same questions, same as-of time, order off against order on:
  bank   his 56 owner-voice questions, usage as of 3 Sep 19:00 (the bank's creation)
  ep     his real first prompts (eval_prompt_recall_fullstack.episodes), usage as of each prompt's own time
  agent  agents' judged searches (eval_expansion_agent_recalls.judged_recalls), usage as of each search
  keys   the reworded bank's held-out half, both forms, usage as of 22 Sep 01:00 (its creation) - rows quarantined
Counts only in the result.

    python eval_usage_order.py OUT_JSON [--sets bank,ep,agent,keys] [--workers 4]
"""
if "-h" in __import__("sys").argv[1:] or "--help" in __import__("sys").argv[1:]:
    print((__doc__ or "").strip() or "usage: python %s" % __import__("os").path.basename(__file__))
    raise SystemExit(0)
import concurrent.futures
import hashlib
import io
import json
import math
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from quarantine_rows import keep_old  # a rerun never destroys an earlier run's rows (brain data, 26 Sep law)

HOME = os.path.expanduser("~")
DR = os.path.join(HOME, ".claude", "dreaming")
BIN = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DR)
sys.path.insert(0, BIN)
STUDY = os.path.join(BIN, "brain_usage_study.py")
Q = os.path.join(HOME, "bank_recall_keys_quarantine")
ROWS_OPEN = os.path.join(HOME, ".claude", "brain_graph", "study", "usage_order_rows.jsonl")
ROWS_Q = os.path.join(Q, "usage_order_key_rows.jsonl")
NOWIN = 0x08000000 if os.name == "nt" else 0
PLAIN = re.compile(r"^\s*\d\.\d\d\s+\[[^\]]*\]\s+(\S+)", re.M)
T_BANK = datetime(2026, 9, 3, 16, 0, tzinfo=timezone.utc).timestamp()     # 19:00 his clock
T_KEYS = datetime(2026, 9, 21, 22, 0, tzinfo=timezone.utc).timestamp()    # 22 Sep 01:00 his clock


PIN_ENV = {}                  # --pin: one frozen word index and graph weight for every call of the run
ALTERNATE = False             # --alternate: half the pairs run the treatment first
AA = False                    # --aa: a second control call per item, to measure what moves with no treatment at all


def recall(q, asof, on):
    env = dict(os.environ, BRAIN_RECALL_LOG="0", RECALL_REWRITE_NO_LOG="1", BRAIN_USAGE_ASOF="%.3f" % asof,
               BRAIN_RECALL_USAGE_ORDER="1" if on else "0", **PIN_ENV)
    try:
        out = subprocess.run([sys.executable, "-X", "utf8", STUDY, "recall", q, "-n", "20"], capture_output=True,
                             text=True, encoding="utf-8", errors="replace", timeout=900, env=env,
                             creationflags=NOWIN).stdout or ""
    except subprocess.TimeoutExpired:
        return None
    return PLAIN.findall(out)[:20]


def rank(top, gold):
    if top is None:
        return None
    return next((i + 1 for i, s in enumerate(top) if s.lower() in gold), 0)


def one(item):
    kind, key, q, gold, asof = item
    # salted: half() picks the held-out set by the parity of the same unsalted hash, so the unsalted rule sent every
    # held-out item treatment-first (27 Sep frozen rerun: 488 of 488)
    on_first = ALTERNATE and int(hashlib.sha1(("order|" + key).encode()).hexdigest(), 16) % 2 == 1
    if on_first:
        b = recall(q, asof, True)
        a = recall(q, asof, False)
    else:
        a = recall(q, asof, False)
        b = recall(q, asof, True)
    row = {"kind": kind, "key": key, "off": rank(a, gold), "on": rank(b, gold), "on_first": on_first,
           "moved": None if a is None or b is None else a[:8] != b[:8]}
    if AA:
        a2 = recall(q, asof, False)
        row["off2"] = rank(a2, gold)
        row["aa_moved"] = None if a is None or a2 is None else a[:20] != a2[:20]
    return row


STATE_GLOBS = (os.path.join(DR, "*.json"), os.path.join(DR, "*.gz"), os.path.join(DR, "*.pkl"), os.path.join(DR, "*.npz"),
               os.path.join(DR, "*.sqlite*"), os.path.join(DR, "*.tsv"), os.path.join(DR, "graph_lane_store", "*"),
               os.path.join(DR, "graph_lane_cache", "*"), os.path.join(HOME, ".claude", "brain_graph", "live", "*.sqlite"),
               os.path.join(HOME, ".claude", "brain_graph", "live", "*.json"))


def state_files():
    """{path: (size, mtime)} of the files a recall can read besides the memories: the word index and its caches, the
    graph lane's dial, store and walk cache, the sidecars, policies and tables. Compared at the start and the end of a
    run, it says whether the retrieval state held still while the pairs ran."""
    import glob as _g
    out = {}
    for pat in STATE_GLOBS:
        for p in _g.glob(pat):
            try:
                st = os.stat(p)
                out[p] = (st.st_size, round(st.st_mtime, 3))
            except OSError:
                pass
    return out


def pin_index(to_dir):
    """Copy the live word index (and its parsed cache) once, and point every call at the copy with refresh, publish
    and the postings sidecar off, the graph lane at the dial's current weight and store. Returns what was pinned."""
    import shutil
    import bm25
    import graph_lane
    os.makedirs(to_dir, exist_ok=True)
    dst = os.path.join(to_dir, "bm25_index_pinned.json.gz")
    shutil.copy2(bm25.INDEX, dst)
    if os.path.exists(bm25.INDEX + ".pkl"):
        shutil.copy2(bm25.INDEX + ".pkl", dst + ".pkl")
    d = graph_lane.dial()
    PIN_ENV.update(BRAIN_BM25_INDEX=dst, BRAIN_RECALL_REFRESH="0", BRAIN_BM25_POSTINGS="0",
                   BRAIN_GRAPH_LAMBDA=str(d.get("lambda", 0.0)), BRAIN_GRAPH_STORE=str(d.get("store", "")))
    return {"index_copy": dst, "index_sha256_16": hashlib.sha256(open(dst, "rb").read()).hexdigest()[:16],
            "graph_lambda": d.get("lambda"), "graph_store": d.get("store")}


def half(qid):
    return "tune" if int(hashlib.sha1(qid.encode()).hexdigest(), 16) % 2 == 0 else "test"


def episode_items():
    import eval_prompt_recall_fullstack as fs
    first = {}
    for ln in io.open(fs.LEDGER, encoding="utf-8", errors="replace"):
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if r.get("origin", "owner") == "owner" and r.get("session") and r.get("text"):
            if len(fs.tokens(r["text"])) >= 6:
                s = r["session"]
                ts = r.get("ts") or ""
                if s not in first or ts < first[s][0]:
                    first[s] = (ts, r["text"])
    out = []
    for s, prompt, reads in fs.episodes():
        ts = first.get(s, ("", ""))[0]
        try:
            t = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
        except ValueError:
            continue
        out.append(("ep", s, prompt, {x.lower() for x in reads}, t))
    return out


def sign_p(a, b):
    n, k = a + b, min(a, b)
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n) if n else 1.0


def main():
    a = sys.argv[1:]
    out_path = a[0]
    sets = (a[a.index("--sets") + 1] if "--sets" in a else "bank,ep,agent,keys").split(",")
    workers = int(a[a.index("--workers") + 1]) if "--workers" in a else 4
    global ALTERNATE, AA
    ALTERNATE, AA = "--alternate" in a, "--aa" in a
    pinned = pin_index(a[a.index("--pin") + 1]) if "--pin" in a else None
    forms = ["q_" + f for f in (a[a.index("--forms") + 1] if "--forms" in a else "paraphrase,verbatim").split(",")]
    limit = int(a[a.index("--limit") + 1]) if "--limit" in a else 0
    state0 = state_files()
    items = []
    if "bank" in sets:
        import eval_brain_recall_fresh as fresh
        for i, (q, golds) in enumerate(fresh.BANK + fresh.BANK_V2_EXTRA):
            golds = {g.lower() for g in golds if os.path.exists(os.path.join(fresh.MEM, g + ".md"))}
            if golds:
                items.append(("bank", "b%03d" % i, q, golds, T_BANK))
    if "ep" in sets:
        items += episode_items()
    if "agent" in sets:
        import eval_expansion_agent_recalls as ear
        for r, opened in ear.judged_recalls():            # the first memory opened is the target (27 Sep)
            items.append(("agent_" + r.get("_kind", "unknown"), "%.1f" % r["t"], r["q"], set(opened[:1]),
                          float(r["t"]) - 1.0))
    if "keys" in sets:
        solved = set(json.load(open(os.path.join(Q, "headless_solver_sessions_20260926.json"), encoding="utf-8")).values())
        for ln in open(os.path.join(Q, "recall_keys.jsonl"), encoding="utf-8"):
            if not ln.strip():
                continue
            k = json.loads(ln)
            if k["id"] in solved or half(k["id"]) != "test":
                continue
            for form in forms:
                items.append(("keys_" + form[2:], k["id"], k[form], {k["source_slug"].lower()}, T_KEYS))
    if limit:
        items = items[:limit]
    t0 = time.time()
    rows = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        for n, r in enumerate(ex.map(one, items), 1):
            rows.append(r)
            if n % 20 == 0:
                print("%d/%d (%.0f s)" % (n, len(items), time.time() - t0), flush=True)
    tag = os.path.splitext(os.path.basename(out_path))[0]      # a run never overwrites an earlier run's rows
    with open(keep_old(ROWS_OPEN.replace(".jsonl", "_%s.jsonl" % tag)), "w", encoding="utf-8") as fo, \
            open(keep_old(ROWS_Q.replace(".jsonl", "_%s.jsonl" % tag)), "w", encoding="utf-8") as fq:
        for r in rows:
            (fq if r["kind"].startswith("keys") else fo).write(json.dumps(r) + "\n")
    res = {"built": time.strftime("%Y-%m-%d %H:%M:%S"), "seconds": round(time.time() - t0),
           "study_copy_sha256_16": hashlib.sha256(open(STUDY, "rb").read()).hexdigest()[:16],
           "live_brain_py_sha256_16": hashlib.sha256(open(os.path.join(DR, "brain.py"), "rb").read()).hexdigest()[:16],
           "bm25_py_sha256_16": hashlib.sha256(open(os.path.join(DR, "bm25.py"), "rb").read()).hexdigest()[:16],
           "doc_expansion": os.environ.get("BRAIN_DOC_EXPANSION", "1") != "0", "sets": {},
           "pinned": pinned, "alternate": ALTERNATE, "aa": AA}
    state1 = state_files()
    res["state_changed_during_run"] = sorted(os.path.relpath(p, HOME) for p in set(state0) | set(state1)
                                             if state0.get(p) != state1.get(p))
    for kind in sorted({r["kind"] for r in rows}):
        rs = [r for r in rows if r["kind"] == kind and r["off"] is not None and r["on"] is not None]
        if not rs:
            continue
        d = {"n": len(rs), "order_moved_top8": sum(1 for r in rs if r["moved"])}
        for k in (1, 5, 8, 20):
            off = [bool(r["off"] and r["off"] <= k) for r in rs]
            on = [bool(r["on"] and r["on"] <= k) for r in rs]
            gin = sum(1 for x, y in zip(off, on) if y and not x)
            gout = sum(1 for x, y in zip(off, on) if x and not y)
            d["top%d" % k] = {"off": sum(off), "on": sum(on), "in": gin, "out": gout, "sign_p": round(sign_p(gin, gout), 4)}
        d["mrr_off"] = round(sum(1.0 / r["off"] for r in rs if r["off"]) / len(rs), 4)
        d["mrr_on"] = round(sum(1.0 / r["on"] for r in rs if r["on"]) / len(rs), 4)
        d["better"] = sum(1 for r in rs if (r["on"] or 99) < (r["off"] or 99))
        d["worse"] = sum(1 for r in rs if (r["on"] or 99) > (r["off"] or 99))
        if ALTERNATE:
            for first in (False, True):
                sub = [r for r in rs if r.get("on_first") == first]
                d["on_first" if first else "off_first"] = {
                    "n": len(sub), "better": sum(1 for r in sub if (r["on"] or 99) < (r["off"] or 99)),
                    "worse": sum(1 for r in sub if (r["on"] or 99) > (r["off"] or 99))}
        if AA:
            aa = [r for r in rs if r.get("off2") is not None]
            d["aa"] = {"n": len(aa), "list_moved_top20": sum(1 for r in aa if r.get("aa_moved")),
                       "gold_rank_changed": sum(1 for r in aa if (r["off2"] or 99) != (r["off"] or 99))}
        res["sets"][kind] = d
        print(kind, json.dumps(d), flush=True)
    json.dump(res, open(keep_old(out_path), "w", encoding="utf-8"), indent=1)


if __name__ == "__main__":
    main()
