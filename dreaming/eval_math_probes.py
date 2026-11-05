"""Score the math brain on the tiered probe bank THROUGH the hook's own selection path.

WHY THIS EXISTS. The 2026-08-22 loop measured retrieval on 733 audit-brief probes (470 tier-1
that constants were tuned on, 263 tier-2 held out) but its harness died with that session's
scratchpad; only the labelled probes survived in state/. This rebuilds the instrument on the
path that matters - retrieve() followed by math_sense._select() with the live gates - so a
number here is what an agent connected to the brain would actually have been served.

THE SET (state/, hand-labelled 2026-08-22):
  math_probe_gold.jsonl          470 probes, 9 claims, TIER 1 (constants were chosen on it)
  math_probe_gold_tier2.jsonl    263 probes, 12 claims, TIER 2 (held out - read this one)
  math_probe_unanswerable.jsonl  318 probes whose theorem is ABSENT from the store: every block
                                 served on one of these is a false fire

REPORTS
  per tier: block hit (gold in the served block), rank-1 hit, gold rank among the re-ranked
  candidates (median, and how many miss entirely), silence rate; per gold_key the same; on
  the unanswerable set the false-fire rate. Compare two runs item by item with --diff.

Read-only over the store. Writes one JSONL of per-item rows (--out) and prints the tables.
Run with PYTHONPATH pointing at math-brain-build/src, the way the hook does.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import io
import json
import os
import statistics
import sys
import time
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.environ.get("MATHBRAIN_PROBES", os.path.join(HERE, "state"))
BUILD_SRC = os.environ.get("MATHBRAIN_SRC", os.path.join(HERE, "..", "mathbrain", "src"))
STORE = os.environ.get("MATHBRAIN_STORE", "")
SETS = {
    "tier1": "math_probe_gold.jsonl",
    "tier2": "math_probe_gold_tier2.jsonl",
    "unanswerable": "math_probe_unanswerable.jsonl",
}


def _rows(path):
    out = []
    with io.open(path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return out


def _gold_ids(row):
    raw = row.get("gold_ids")
    if isinstance(raw, list):
        return set(raw)
    if isinstance(raw, str):
        # stored as the repr of a python list
        strip_chars = " '[]"
        return set(t.strip(strip_chars) for t in raw.split(",") if t.strip(strip_chars))
    return set()


def load_hook():
    sys.path.insert(0, HERE)
    sys.path.insert(0, BUILD_SRC)
    import math_sense  # noqa: E402  (the live hook; its constants are the ones measured)
    from math_brain.retrieval import retrieve  # noqa: E402
    from math_brain.store import ResearchStore  # noqa: E402

    return math_sense, retrieve, ResearchStore


def _title_key(text):
    """Accent-folded, case-folded, punctuation-free title key.

    The harvest waves spell the same theorem both ways ("Lovász local lemma" beside "Lovasz
    local lemma"); a key that kept the accent counted the ASCII copy as a miss on every
    probe the theorem was actually served for (63 of 63 on the first baseline).
    """
    folded = unicodedata.normalize("NFKD", text or "")
    out = []
    for ch in folded.casefold():
        if unicodedata.combining(ch):
            continue
        out.append(ch if ch.isalnum() else " ")
    return " ".join("".join(out).split())


def _load_stems():
    """gold_key -> stems (state/math_probe_gold_stems.json): a served title containing one of a
    key's stems counts as serving the theorem, whichever copy the store chose. {} when absent."""
    path = os.path.join(STATE, "math_probe_gold_stems.json")
    try:
        with io.open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        return {k: [_title_key(s) for s in v] for k, v in data.items() if not k.startswith("_") and isinstance(v, list)}
    except (OSError, ValueError):
        return {}


def score_set(name, rows, ms, retrieve, nodes, edges, limit, stems=None):
    """One row per probe. Three notions of a hit are kept apart on purpose:

    gold id   the labelled node itself was served (the 2026-08-22 labels name specific ids);
    title     any served node shares the gold theorem's normalised title - the store now holds
              several copies of most of these theorems (a harvested lede beside an authored
              statement), and the labels predate the copies;
    theorem   any served title contains one of the key's stems (state/math_probe_gold_stems.json):
              the theorem was served through ANY copy or sibling statement of it. Measured
              2026-09-03 ten points above the labelled hit on the held-out tier (39.9 vs 29.3).
    The id hit is the strictest, oldest bar; the theorem hit is what a reader would call served.
    """
    by_id = {n.id: n for n in nodes}
    stems = stems or {}
    results = []
    for row in rows:
        query = (row.get("query") or "")[:8000]
        gold = _gold_ids(row)
        gold_titles = {_title_key(by_id[g].title) for g in gold if g in by_id}
        if row.get("gold_title"):
            gold_titles.add(_title_key(row.get("gold_title")))
        key_stems = stems.get(row.get("gold_key") or row.get("key") or "", [])
        t0 = time.time()
        res = retrieve(query, nodes=nodes, edges=edges, limit=limit,
                       min_support_score=0.0, min_margin=0.0)
        hits = list(res.hits)
        ranked = sorted(hits, key=lambda h: (-ms._rank_score(h), h.node.id))
        chosen = ms._select(hits) if hits else []
        chosen_ids = [h.node.id for h in chosen]
        rank = None
        title_rank = None
        for i, h in enumerate(ranked):
            if rank is None and h.node.id in gold:
                rank = i + 1
            if title_rank is None and _title_key(h.node.title) in gold_titles:
                title_rank = i + 1
            if rank is not None and title_rank is not None:
                break
        results.append({
            "set": name,
            # the audit briefs share their first hundred characters by template, so a diff keyed on a
            # prefix compares different probes; the key is the whole query's digest
            "qid": hashlib.sha1(query.encode("utf-8", "replace")).hexdigest()[:16],
            "gold_key": row.get("gold_key") or row.get("key"),
            "gold_title": row.get("gold_title"),
            "n_gold": len(gold),
            "served": len(chosen_ids),
            "block_hit": bool(gold and any(c in gold for c in chosen_ids)),
            "title_hit": bool(gold_titles and any(_title_key(h.node.title) in gold_titles for h in chosen)),
            "theorem_hit": bool(key_stems and any(any(s in _title_key(h.node.title) for s in key_stems) for h in chosen)),
            "rank1_hit": bool(gold and ranked and ranked[0].node.id in gold),
            "gold_rank": rank,
            "title_rank": title_rank,
            "top_title": ranked[0].node.title[:80] if ranked else "",
            "top_score": round(ms._rank_score(ranked[0]), 3) if ranked else None,
            "top_lex": round(ranked[0].lexical_score, 3) if ranked else None,
            "top_sym": round(ranked[0].symbol_score, 3) if ranked else None,
            "top_graph": round(ranked[0].graph_score, 3) if ranked else None,
            "second_score": round(ms._rank_score(ranked[1]), 3) if len(ranked) > 1 else None,
            "served_titles": [h.node.title[:60] for h in chosen],
            "secs": round(time.time() - t0, 3),
            "query_head": query[:120].replace("\n", " "),
        })
    return results


def summarize(results):
    by_set = collections.defaultdict(list)
    for r in results:
        by_set[r["set"]].append(r)
    lines = []
    for name in ("tier1", "tier2", "unanswerable"):
        rs = by_set.get(name)
        if not rs:
            continue
        n = len(rs)
        if name == "unanswerable":
            fired = sum(1 for r in rs if r["served"])
            lines.append(f"{name:12s} n={n:4d}  false fires (a block served) {fired}/{n} = {100.0*fired/n:.1f}%")
            continue
        bh = sum(1 for r in rs if r["block_hit"])
        th = sum(1 for r in rs if r.get("title_hit"))
        xh = sum(1 for r in rs if r.get("theorem_hit"))
        r1 = sum(1 for r in rs if r["rank1_hit"])
        silent = sum(1 for r in rs if not r["served"])
        ranks = [r["gold_rank"] for r in rs if r["gold_rank"]]
        tranks = [r["title_rank"] for r in rs if r.get("title_rank")]
        miss = sum(1 for r in rs if r["gold_rank"] is None)
        med = statistics.median(ranks) if ranks else None
        tmed = statistics.median(tranks) if tranks else None
        lines.append(
            f"{name:12s} n={n:4d}  block hit by id {bh}/{n} = {100.0*bh/n:.1f}%  by title {th}/{n} = {100.0*th/n:.1f}%  "
            f"theorem served {xh}/{n} = {100.0*xh/n:.1f}%  "
            f"rank-1 {100.0*r1/n:.1f}%  silent {100.0*silent/n:.1f}%  gold median rank {med} (title {tmed})  gold unranked {miss}"
        )
        keys = collections.defaultdict(list)
        for r in rs:
            keys[r["gold_key"]].append(r)
        for key, krs in sorted(keys.items(), key=lambda kv: -len(kv[1])):
            kn = len(krs)
            kb = sum(1 for r in krs if r["block_hit"])
            kt = sum(1 for r in krs if r.get("title_hit"))
            kx = sum(1 for r in krs if r.get("theorem_hit"))
            kranks = [r["gold_rank"] for r in krs if r["gold_rank"]]
            ktr = [r["title_rank"] for r in krs if r.get("title_rank")]
            kmed = statistics.median(kranks) if kranks else None
            ktmed = statistics.median(ktr) if ktr else None
            ksil = sum(1 for r in krs if not r["served"])
            lines.append(
                f"    {str(key):14s} n={kn:3d}  block by id {100.0*kb/kn:5.1f}%  by title {100.0*kt/kn:5.1f}%  theorem {100.0*kx/kn:5.1f}%  "
                f"silent {100.0*ksil/kn:5.1f}%  gold median rank {kmed} (title {ktmed})"
            )
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", default=STORE)
    ap.add_argument("--sets", default="tier1,tier2,unanswerable")
    ap.add_argument("--sample", type=int, default=0, help="per set; 0 = all")
    ap.add_argument("--out", default=os.path.join(STATE, "math_probes_last.jsonl"))
    ap.add_argument("--diff", default="", help="an earlier --out file to compare item by item")
    args = ap.parse_args()

    ms, retrieve, ResearchStore = load_hook()
    with ResearchStore(args.store) as store:
        nodes = list(store.iter_nodes())
        edges = list(store.iter_edges())
    print(f"store {args.store}: {len(nodes)} nodes, {len(edges)} edges")
    results = []
    t0 = time.time()
    stems = _load_stems()
    for name in args.sets.split(","):
        rows = _rows(os.path.join(STATE, SETS[name]))
        if args.sample:
            rows = rows[: args.sample]
        results.extend(score_set(name, rows, ms, retrieve, nodes, edges, ms.CANDIDATE_POOL, stems=stems))
        print(f"  {name}: {len(rows)} probes done at {time.time()-t0:.0f}s", flush=True)
    with io.open(args.out, "w", encoding="utf-8") as handle:
        for r in results:
            handle.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(summarize(results))
    if args.diff and os.path.exists(args.diff):
        before = {(r["set"], r.get("qid") or r["query_head"]): r for r in _rows(args.diff)}
        gained = lost = 0
        for r in results:
            b = before.get((r["set"], r.get("qid") or r["query_head"]))
            if not b or r["set"] == "unanswerable":
                continue
            if r["block_hit"] and not b["block_hit"]:
                gained += 1
            elif b["block_hit"] and not r["block_hit"]:
                lost += 1
        print(f"vs {os.path.basename(args.diff)}: block hits gained {gained}, lost {lost}")


if __name__ == "__main__":
    main()
