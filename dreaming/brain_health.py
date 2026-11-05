#!/usr/bin/env python3
"""brain_health.py - one repeatable measurement of the brain, so "better" is a number.

Built 2026-08-20 from a five-lens audit of the live corpus. The hard-won method
(method_improving_the_brain_well_2026_07_08) is blunt that on a mature brain the obvious
improvement usually does not survive ground truth - roughly 13 of 15 hypotheses were refuted the
last time this was tried - and that the right first move is a STABLE eval, not a tune, because the
association graph is non-stationary and a snapshot tune is verified against a graph that no longer
exists. So this measures; it changes nothing.

Everything here is read-only. No memory file is written, moved or deleted, and nothing is pushed.

    python brain_health.py                 # structure only, fast, no model calls
    python brain_health.py --recall 40     # also run 40 live recall queries (slow: ~7s each)
    python brain_health.py --json out.json # machine-readable, for trend tracking

WHAT IT MEASURES, and why each one earned its place (2026-08-20 baseline in brackets):

  structure
    nodes, ledgers                      ledgers are NOT memories; keeping them in the size stats is
                                        what made an earlier read call a 2.7 MB "memory node" a
                                        crisis. It is a ledger. [10,263 nodes / 12 ledgers]
    size percentiles                    the node band is healthy and needs no surgery
                                        [p1 260 B, p50 4.3 KB, p99 15.8 KB]
    strata                              share of the corpus that is per-job checkpoint rather than
                                        durable knowledge [job_ = 31.9%]
  topology
    degree distribution + gini          heavy-tailed, alpha about 3.2-3.6 [gini 0.587]
    isolated nodes                      degree 0 in the wiki-link graph [2,909 = 28.3%]
    isolated-by-stratum                 90.6% of isolated nodes are job_ checkpoints - the orphan
                                        problem is one mechanism, not a corpus-wide rot
    components                          giant component and the rest [giant 7,137; next largest 27]
  index
    MEMORY.md coverage                  the index auto-loaded into every session [85 lines / 10,277]
  retrieval  (--recall)
    recall@1/5/20, MRR                  [0.433 / 0.667 / 0.800, MRR 0.545]
    LEAK-SPLIT recall                   the load-bearing one. Queries are split by term overlap with
                                        the target. High overlap retrieves; low overlap misses. If a
                                        change does not lift the LOW-LEAK half it has not made the
                                        brain smarter, only better at echoing vocabulary.
                                        [low-leak half: recall@1 0.30, MRR 0.415]
    latency                             [median 6.45 s, p90 10.8 s]

The low-leak number is the one to beat. Everything else is context.
"""

import argparse
import collections
import json
import os
import random
import re
import subprocess
import sys
import time

# Corpus location: --mem wins, then BRAIN_MEMORY_DIR, then the usual local layout. Kept configurable
# so this runs against any brain, not only the one it was written on.
_DEFAULT_MEM = os.path.join(os.path.expanduser("~"), ".claude", "projects", "{{BRAIN_SLUG}}", "memory")
MEM = os.environ.get("BRAIN_MEMORY_DIR", _DEFAULT_MEM)
BRAIN = os.environ.get(
    "BRAIN_CLI", os.path.join(os.path.dirname(os.path.abspath(__file__)), "brain.py"))
# Ledgers are append-only registers, not memory nodes. Keeping them in the size stats is what made an
# earlier read call INDEX_ARCHIVE.md (2.7 MB) a "monster memory": it is the archived index, moved out
# of MEMORY.md on 2026-07-03 to keep the loaded index small. The index is TIERED, not broken - but the
# archive is far too big to load, so a session effectively sees only MEMORY.md's ~80 lines.
LEDGER_HINT = re.compile(r"^(MEMORY|MISTAKES|OPEN_QUESTIONS|CONSCIENCE|PREDICTIONS|SALIENCE|"
                         r"AUTOBIOGRAPHY|MODEL_OF_|CURIOSITY|GROWTH|MEMORY_ARCHIVE|INDEX_ARCHIVE|"
                         r"STATE_OF_MIND|CONVERSATIONS|TIMELINE|TOPICS)")


def pct(sorted_vals, q):
    if not sorted_vals:
        return 0
    i = min(len(sorted_vals) - 1, max(0, int(round(q * (len(sorted_vals) - 1)))))
    return sorted_vals[i]


def gini(vals):
    v = sorted(vals)
    n = len(v)
    if n == 0 or sum(v) == 0:
        return 0.0
    cum = 0
    for i, x in enumerate(v, 1):
        cum += i * x
    return (2 * cum) / (n * sum(v)) - (n + 1) / n


def scan():
    """read the corpus once; return nodes, ledgers, sizes, link graph"""
    files = [f for f in os.listdir(MEM) if f.endswith(".md")]
    names = {f[:-3] for f in files}
    nodes, ledgers = [], []
    sizes, desc = {}, {}
    indeg, outdeg = collections.Counter(), collections.Counter()
    for f in files:
        slug = f[:-3]
        path = os.path.join(MEM, f)
        try:
            raw = open(path, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        (ledgers if LEDGER_HINT.match(slug) else nodes).append(slug)
        sizes[slug] = len(raw.encode("utf-8", "replace"))
        m = re.search(r"^description:\s*(.+?)\s*$", raw, re.M)
        if m:
            desc[slug] = m.group(1).strip().strip('"').strip("'")
        outdeg[slug] = 0
        for t in set(re.findall(r"\[\[([^\]|#]+)", raw)):
            t = t.strip()
            if t in names and t != slug:
                indeg[t] += 1
                outdeg[slug] += 1
    return names, nodes, ledgers, sizes, desc, indeg, outdeg


def components(names, indeg, outdeg):
    """undirected components over the wiki-link graph"""
    adj = collections.defaultdict(set)
    for f in os.listdir(MEM):
        if not f.endswith(".md"):
            continue
        slug = f[:-3]
        try:
            raw = open(os.path.join(MEM, f), encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        for t in set(re.findall(r"\[\[([^\]|#]+)", raw)):
            t = t.strip()
            if t in names and t != slug:
                adj[slug].add(t)
                adj[t].add(slug)
    seen, sizes = set(), []
    for n in names:
        if n in seen:
            continue
        stack, comp = [n], 0
        seen.add(n)
        while stack:
            u = stack.pop()
            comp += 1
            for w in adj.get(u, ()):
                if w not in seen:
                    seen.add(w)
                    stack.append(w)
        sizes.append(comp)
    sizes.sort(reverse=True)
    return sizes


def make_query(slug, d):
    """a query from the DESCRIPTION only, with the slug's own tokens removed.

    Without the strip a query names its own answer and every score is inflated - that is exactly how
    an earlier hand-rolled version of this test scored 0.81-1.00 and had to be thrown away."""
    d = re.sub(r"^\[[A-Z0-9 ,+&/-]+\]\s*", "", d)
    stop = {t for t in re.split(r"[^A-Za-z0-9]+", slug.lower()) if len(t) > 3}
    words = [w for w in re.split(r"\s+", d) if w]
    kept = [w for w in words if re.sub(r"[^A-Za-z0-9]", "", w).lower() not in stop]
    return re.sub(r"\s+", " ", " ".join(kept)).strip()[:180]


def leak(query, slug):
    """term overlap between query and target slug: the confound that must be reported, not hidden"""
    q = {t for t in re.split(r"[^A-Za-z0-9]+", query.lower()) if len(t) > 3}
    s = {t for t in re.split(r"[^A-Za-z0-9]+", slug.lower()) if len(t) > 3}
    return len(q & s) / max(1, len(s))


def recall_once(q, timeout=180):
    """returns (hits, seconds) or (None, seconds) when the TOOL failed.

    None and [] must stay distinguishable: a crashed reader that returns nothing looks exactly like a
    search that found nothing, and scoring the first as a miss once reversed this very comparison."""
    t0 = time.time()
    try:
        p = subprocess.run([sys.executable, BRAIN, "recall", q],
                           capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
    except Exception:
        return None, time.time() - t0
    hits = []
    for line in (p.stdout or "").splitlines():
        m = re.match(r"\s*[0-9.]+\s+\[[^\]]*\]\s+(\S+)", line)
        if m:
            hits.append(m.group(1))
    return hits, time.time() - t0


def run_recall(desc, sizes, n, seed=20260820):
    rng = random.Random(seed)
    pool = [s for s, d in desc.items() if len(d) > 70 and sizes.get(s, 0) > 800]
    rng.shuffle(pool)
    rows, dropped = [], 0
    for slug in pool:
        if len(rows) >= n:
            break
        q = make_query(slug, desc[slug])
        if len(q) < 30:
            continue
        hits, secs = recall_once(q)
        if hits is None:
            dropped += 1
            continue
        rank = hits.index(slug) + 1 if slug in hits else 0
        rows.append({"slug": slug, "q": q, "rank": rank,
                     "leak": round(leak(q, slug), 3), "secs": round(secs, 2)})
        print("    %-4s rank=%-4s leak=%.2f  %s" % ("HIT" if rank else "miss", rank or "-",
                                                    rows[-1]["leak"], slug[:52]))
        sys.stdout.flush()
    return rows, dropped


def score(rows):
    if not rows:
        return {}
    n = len(rows)
    at = lambda k: sum(1 for r in rows if r["rank"] and r["rank"] <= k) / n
    mrr = sum(1.0 / r["rank"] for r in rows if r["rank"]) / n
    secs = sorted(r["secs"] for r in rows)
    return {"n": n, "recall@1": round(at(1), 3), "recall@5": round(at(5), 3),
            "recall@20": round(at(20), 3), "MRR": round(mrr, 3),
            "median_secs": round(pct(secs, 0.5), 2), "p90_secs": round(pct(secs, 0.9), 2)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--recall", type=int, default=0, help="run N live recall queries (slow)")
    ap.add_argument("--json", help="write the report here")
    ap.add_argument("--mem", help="memory corpus directory (default: $BRAIN_MEMORY_DIR or the local brain)")
    a = ap.parse_args()

    global MEM
    if a.mem:
        MEM = a.mem
    if not os.path.isdir(MEM):
        sys.stderr.write("no corpus at %s - pass --mem or set BRAIN_MEMORY_DIR\n" % MEM)
        return 2

    names, nodes, ledgers, sizes, desc, indeg, outdeg = scan()
    node_sizes = sorted(sizes[s] for s in nodes)
    deg = {s: indeg.get(s, 0) + outdeg.get(s, 0) for s in names}
    iso = [s for s in names if deg[s] == 0]
    strata = collections.Counter()
    for s in names:
        strata[s.split("_")[0] + "_" if "_" in s else "other"] += 1
    job = [s for s in names if s.startswith("job_")]
    job_iso = [s for s in iso if s.startswith("job_")]
    comps = components(names, indeg, outdeg)

    idx = os.path.join(MEM, "MEMORY.md")
    idx_lines = sum(1 for _ in open(idx, encoding="utf-8", errors="replace")) if os.path.exists(idx) else 0

    rep = {
        "when": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "structure": {
            "nodes": len(nodes), "ledgers": len(ledgers),
            "size_p1": pct(node_sizes, 0.01), "size_p50": pct(node_sizes, 0.5),
            "size_p95": pct(node_sizes, 0.95), "size_p99": pct(node_sizes, 0.99),
            "size_max": node_sizes[-1] if node_sizes else 0,
        },
        "topology": {
            "edges": sum(outdeg.values()),
            "mean_degree": round(sum(deg.values()) / max(1, len(names)), 2),
            "gini_degree": round(gini(list(deg.values())), 3),
            "max_degree": max(deg.values()) if deg else 0,
            "isolated": len(iso),
            "isolated_pct": round(100 * len(iso) / max(1, len(names)), 1),
            "isolated_that_are_job": len(job_iso),
            "isolated_job_share_pct": round(100 * len(job_iso) / max(1, len(iso)), 1),
            "components": len(comps),
            "giant": comps[0] if comps else 0,
            "second": comps[1] if len(comps) > 1 else 0,
        },
        "strata": {
            "job_nodes": len(job),
            "job_pct": round(100 * len(job) / max(1, len(names)), 1),
            "job_with_inlink_pct": round(100 * sum(1 for s in job if indeg.get(s, 0)) / max(1, len(job)), 1),
        },
        "index": {"memory_md_lines": idx_lines, "corpus": len(names),
                  "coverage_pct": round(100 * idx_lines / max(1, len(names)), 2)},
    }

    print("BRAIN HEALTH  %s" % rep["when"])
    print()
    print("structure   nodes %d  ledgers %d" % (rep["structure"]["nodes"], rep["structure"]["ledgers"]))
    print("            size p1 %d  p50 %d  p95 %d  p99 %d  max %d bytes"
          % (rep["structure"]["size_p1"], rep["structure"]["size_p50"],
             rep["structure"]["size_p95"], rep["structure"]["size_p99"], rep["structure"]["size_max"]))
    t = rep["topology"]
    print("topology    edges %d  mean deg %.2f  gini %.3f  max %d"
          % (t["edges"], t["mean_degree"], t["gini_degree"], t["max_degree"]))
    print("            isolated %d (%.1f%%), of which job_ %d (%.1f%% of isolated)"
          % (t["isolated"], t["isolated_pct"], t["isolated_that_are_job"], t["isolated_job_share_pct"]))
    print("            components %d  giant %d  second %d" % (t["components"], t["giant"], t["second"]))
    s = rep["strata"]
    print("strata      job_ %d (%.1f%% of corpus), %.1f%% of them have any inlink"
          % (s["job_nodes"], s["job_pct"], s["job_with_inlink_pct"]))
    i = rep["index"]
    print("index       MEMORY.md %d lines for %d memories (%.2f%% coverage)"
          % (i["memory_md_lines"], i["corpus"], i["coverage_pct"]))

    if a.recall:
        print()
        print("retrieval   running %d live queries (about 7s each)" % a.recall)
        rows, dropped = run_recall(desc, sizes, a.recall)
        lk = sorted(r["leak"] for r in rows)
        med = pct(lk, 0.5) if lk else 0
        rep["retrieval"] = {
            "all": score(rows),
            "low_leak_half": score([r for r in rows if r["leak"] <= med]),
            "high_leak_half": score([r for r in rows if r["leak"] > med]),
            "dropped_tool_errors": dropped,
        }
        print()
        print("            %-16s %-6s %-8s %-8s %-8s %s" % ("group", "n", "r@1", "r@5", "r@20", "MRR"))
        for k in ("all", "low_leak_half", "high_leak_half"):
            v = rep["retrieval"][k]
            if v:
                print("            %-16s %-6d %-8.3f %-8.3f %-8.3f %.3f"
                      % (k, v["n"], v["recall@1"], v["recall@5"], v["recall@20"], v["MRR"]))
        print()
        print("            LOW-LEAK is the number that matters: high-leak queries share vocabulary")
        print("            with their answer, so they measure lexical matching, not understanding.")
        if dropped:
            print("            (%d samples dropped for TOOL errors - not counted as misses)" % dropped)

    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(rep, f, indent=1)
        print()
        print("wrote %s" % a.json)


if __name__ == "__main__":
    main()
