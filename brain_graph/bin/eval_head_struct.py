"""eval_head_struct.py - recall's protected head by promotion against the old score line, measured on his 56 and the
held-out reworded bank (28 Sep 2026, claude-825b1305; scheduled audit account03/B01 auto63,
B01-R1-BOOSTED-BM25-CROSSES-PROTECTED-HEAD-BOUNDARY-20260928).

    python eval_head_struct.py OUT_JSON CANDIDATE_BRAIN.py [--workers 4] [--limit N]

Three recalls per question, back to back in one worker, on one pinned copy of the word index and of the link sidecar:
 live  the installed brain.py
 A     the candidate with BRAIN_RECALL_HEAD_STRUCT=0 (the score line; must show exactly what live shows - the control)
 B     the candidate with BRAIN_RECALL_HEAD_STRUCT=1 (the head is the set of rows promoted into it)
Each recall prints 20 lines; the rank of the first gold memory among them is the score (0 = not shown). The candidate's
recall log gives, per question, how many ordinary rows crossed the lexical peak after the boosts ('crossed') and how many
fresh attachments a body row reached or passed ('att_passed'). Per-question rows name gold memories and go to the key
quarantine; the output holds counts only. The run fails closed (exit 2, control_passed false) when any A differs from
live or any recall is missing.
"""
import concurrent.futures
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

if __name__ == "__main__" and ("-h" in sys.argv[1:] or "--help" in sys.argv[1:]):
    print(__doc__)
    sys.exit(0)

HOME = os.path.expanduser("~")
BIN = os.path.dirname(os.path.abspath(__file__))
DR = os.path.join(HOME, ".claude", "dreaming")
Q = os.path.join(HOME, "bank_recall_keys_quarantine")
NOWIN = 0x08000000 if os.name == "nt" else 0
SHOWN = 20
LINE = re.compile(r"^  \d+\.\d\d \[.*\] (\S+)\s*$")


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def items(limit=0):
    sys.path.insert(0, BIN)
    sys.path.insert(0, DR)
    import eval_graph_mixture as egm
    return egm.items(limit)


def shown_slugs(stdout):
    out = []
    for ln in stdout.splitlines():
        m = LINE.match(ln)
        if m:
            out.append(m.group(1))
    return out[:SHOWN]


def one_recall(script, q, env, log):
    env = dict(env, BRAIN_RECALL_LOG_PATH=log)
    p = subprocess.run([sys.executable, "-X", "utf8", script, "recall", q, "-n", str(SHOWN)], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=900, env=env, creationflags=NOWIN)
    rec = None
    if os.path.exists(log):
        rows = [json.loads(l) for l in open(log, encoding="utf-8") if l.strip()]
        rec = rows[-1] if rows else None
    return shown_slugs(p.stdout), rec


def rank(shown, gold):
    return next((i + 1 for i, s in enumerate(shown) if s.lower() in gold), 0)


def sign_p(a, b):
    n, k = a + b, min(a, b)
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n) if n else 1.0


def below_normal():
    """BelowNormal for this process and the recalls it starts (Windows children inherit the class)."""
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x00004000)
        except Exception:
            pass


def main():
    below_normal()
    a = sys.argv[1:]
    out_path, cand = a[0], os.path.abspath(a[1])
    workers = int(a[a.index("--workers") + 1]) if "--workers" in a else 4
    limit = int(a[a.index("--limit") + 1]) if "--limit" in a else 0
    sys.path.insert(0, DR)
    import bm25
    tmp = tempfile.mkdtemp(prefix="head_struct_")
    idx = os.path.join(tmp, "bm25_index_pinned.json.gz")
    shutil.copy2(bm25.INDEX, idx)
    if os.path.exists(bm25.INDEX + ".pkl"):
        shutil.copy2(bm25.INDEX + ".pkl", idx + ".pkl")
    side_live = os.path.join(HOME, ".claude", "brain_graph", "live", "sidecar.sqlite")
    side = os.path.join(tmp, "sidecar_pinned.sqlite")
    shutil.copy2(side_live, side)
    base_env = dict(os.environ, BRAIN_BM25_INDEX=idx, BRAIN_RECALL_REFRESH="0", BRAIN_BM25_POSTINGS="0",
                    BRAIN_GRAPH_SIDECAR=side, BRAIN_RECALL_LOG="1", RECALL_REWRITE_NO_LOG="1", BRAIN_RECALL_LOG_FULL="1")
    base_env.pop("BRAIN_RECALL_HEAD_STRUCT", None)
    its = items(limit)
    t0 = time.time()

    def run(it):
        kind, key, q, gold = it
        h = hashlib.sha1(key.encode()).hexdigest()[:10]
        live, _ = one_recall(os.path.join(DR, "brain.py"), q, base_env, os.path.join(tmp, h + "_live.jsonl"))
        sa, ra = one_recall(cand, q, dict(base_env, BRAIN_RECALL_HEAD_STRUCT="0"), os.path.join(tmp, h + "_a.jsonl"))
        sb, rb = one_recall(cand, q, dict(base_env, BRAIN_RECALL_HEAD_STRUCT="1"), os.path.join(tmp, h + "_b.jsonl"))
        return {"kind": kind, "key": key, "gold": sorted(gold), "live": live, "a": sa, "b": sb,
                "r_live": rank(live, gold), "r_a": rank(sa, gold), "r_b": rank(sb, gold),
                "crossed": (ra or {}).get("crossed"), "att_passed": (ra or {}).get("att_passed"),
                "head_n_a": (ra or {}).get("head_n"), "head_n_b": (rb or {}).get("head_n"),
                "a_equals_live": sa == live and bool(sa)}

    rows = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        for n, r in enumerate(ex.map(run, its), 1):
            rows.append(r)
            if n % 20 == 0:
                print("%d/%d (%.0f s)" % (n, len(its), time.time() - t0), flush=True)
    res = {"built": time.strftime("%Y-%m-%d %H:%M:%S"), "n_items": len(its), "shown": SHOWN,
           "index_sha256": sha(idx), "sidecar_sha256": sha(side),
           "code_sha256": {os.path.basename(p): sha(p) for p in (os.path.abspath(__file__), cand, os.path.join(DR, "brain.py"),
                                                                 os.path.join(DR, "bm25.py"), os.path.join(DR, "graph_lane.py"))},
           "graph_store": json.load(open(os.path.join(DR, "brain_graph_dial.json"), encoding="utf-8")).get("store"),
           "control": {"a_equals_live": sum(1 for r in rows if r["a_equals_live"]), "rows": len(rows),
                       "empty_recalls": sum(1 for r in rows if not (r["live"] and r["a"] and r["b"]))},
           "sets": {}}
    for kind in sorted({r["kind"] for r in rows}):
        rs = [r for r in rows if r["kind"] == kind]
        d = {"n": len(rs),
             "crossed_any": sum(1 for r in rs if (r["crossed"] or 0) > 0),
             "crossed_rows": sum(r["crossed"] or 0 for r in rs),
             "att_passed_any": sum(1 for r in rs if (r["att_passed"] or 0) > 0),
             "shown_differs": sum(1 for r in rs if r["a"] != r["b"])}
        for arm in ("a", "b"):
            rk = [r["r_" + arm] for r in rs]
            d[arm] = {"hit1": sum(1 for x in rk if x == 1), "hit5": sum(1 for x in rk if x and x <= 5),
                      "hit10": sum(1 for x in rk if x and x <= 10), "hit20": sum(1 for x in rk if x),
                      "mrr": round(sum(1.0 / x for x in rk if x) / max(1, len(rs)), 4)}
        up = sum(1 for r in rs if (r["r_b"] or 99) < (r["r_a"] or 99))
        down = sum(1 for r in rs if (r["r_b"] or 99) > (r["r_a"] or 99))
        in5 = sum(1 for r in rs if r["r_b"] and r["r_b"] <= 5 and not (r["r_a"] and r["r_a"] <= 5))
        out5 = sum(1 for r in rs if r["r_a"] and r["r_a"] <= 5 and not (r["r_b"] and r["r_b"] <= 5))
        d["b_vs_a"] = {"rank_up": up, "rank_down": down, "sign_p_rank": round(sign_p(up, down), 4),
                       "top5_in": in5, "top5_out": out5, "sign_p_top5": round(sign_p(in5, out5), 4)}
        # the rows where the change could act: a crossing or a passed attachment on the question
        act = [r for r in rs if (r["crossed"] or 0) > 0 or (r["att_passed"] or 0) > 0]
        d["where_it_can_act"] = {"n": len(act),
                                 "rank_up": sum(1 for r in act if (r["r_b"] or 99) < (r["r_a"] or 99)),
                                 "rank_down": sum(1 for r in act if (r["r_b"] or 99) > (r["r_a"] or 99))}
        d["moved_without_a_crossing"] = sum(1 for r in rs if r["a"] != r["b"] and not (r["crossed"] or r["att_passed"]))
        res["sets"][kind] = d
        print(kind, json.dumps(d), flush=True)
    res["control_passed"] = (res["control"]["a_equals_live"] == len(rows) == len(its)
                             and res["control"]["empty_recalls"] == 0)
    res["seconds"] = round(time.time() - t0)
    res["command"] = [os.path.basename(sys.argv[0])] + sys.argv[1:]
    qrows = os.path.join(Q, "head_struct_rows_%s.jsonl" % time.strftime("%Y%m%d_%H%M"))
    with open(qrows, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    res["rows_file"] = os.path.basename(qrows)
    json.dump(res, open(out_path, "w", encoding="utf-8"), indent=1)
    shutil.rmtree(tmp, ignore_errors=True)
    print("control_passed", res["control_passed"], res["control"])
    sys.exit(0 if res["control_passed"] else 2)


if __name__ == "__main__":
    main()
