"""Score the math brain on the FRESH bank (state/math_probe_fresh_<date>.json) through the hook's own path.

The owner's ask, 2026-09-03 18:5x: "i want another test to see how it is doing". The 22 August bank had been
the only instrument, its tier-1 was tuned on, and its labels name single nodes. This bank is new: 90 theorem
targets the store holds (checked by title stem before authoring, none of the 08-22 gold keys), each asked two
ways - q_content, the question a working mathematician asks mid-proof without naming the result, and q_hinted,
the same with the name - and 40 targets the store lacks (checked absent by stem, re-checked here at run time)
whose questions must NOT be answered.

Scored the way a reader would score it: theorem-level - a served title containing one of the target's stems.
Also: rank-1 (the top re-ranked title carries a stem), silence, and for the lacking targets the false-fire rate
plus what was served instead. Same path as eval_math_probes.py: retrieve() with gates off over the whole store,
then math_sense._select() with the live gates. Read-only over the store. One JSONL of per-item rows (--out);
a hand-judgement sample printed at the end (--judge N) so a human can read what the block actually said.
Run with PYTHONPATH at math-brain-build/src, the way the hook does.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import io
import json
import os
import random
import sys
import time
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "state")
# Framework deployment: the mathbrain package is vendored beside this hook and the store location is
# deployment-specific - point MATHBRAIN_SRC / MATHBRAIN_STORE at yours.
BUILD_SRC = os.environ.get("MATHBRAIN_SRC", os.path.join(HERE, "..", "mathbrain", "src"))
STORE = os.environ.get("MATHBRAIN_STORE", "")
BANK = os.path.join(STATE, "math_probe_fresh_20260903.json")


def _below_normal():
    try:
        k32 = ctypes.windll.kernel32
        k32.SetPriorityClass(k32.GetCurrentProcess(), 0x00004000)
    except Exception:  # noqa: BLE001
        try:  # no windll off Windows: nice 10 carries the same BelowNormal intent
            import os as _os
            if _os.name != "nt":
                _os.nice(10)
        except Exception:  # noqa: BLE001
            pass


def _title_key(text):
    folded = unicodedata.normalize("NFKD", text or "")
    out = []
    for ch in folded.casefold():
        if unicodedata.combining(ch):
            continue
        out.append(ch if ch.isalnum() else " ")
    return " ".join("".join(out).split())


def load_hook():
    sys.path.insert(0, HERE)
    sys.path.insert(0, BUILD_SRC)
    import math_sense  # noqa: E402
    from math_brain.retrieval import retrieve  # noqa: E402
    from math_brain.store import ResearchStore  # noqa: E402
    return math_sense, retrieve, ResearchStore


def stem_hit(titles, stems):
    return any(any(s in _title_key(t) for s in stems) for t in titles)


def run_one(query, ms, retrieve, nodes, edges):
    res = retrieve(query, nodes=nodes, edges=edges, limit=ms.CANDIDATE_POOL, min_support_score=0.0, min_margin=0.0)
    hits = list(res.hits)
    ranked = sorted(hits, key=lambda h: (-ms._rank_score(h), h.node.id))
    chosen = ms._select(hits) if hits else []
    return ranked, chosen


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bank", default=BANK)
    ap.add_argument("--store", default=STORE)
    ap.add_argument("--out", default=os.path.join(STATE, "math_fresh_last.jsonl"))
    ap.add_argument("--judge", type=int, default=20, help="hand-judgement sample size per set")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--sample", type=int, default=0, help="targets per set; 0 = all")
    args = ap.parse_args()
    _below_normal()
    ms, retrieve, ResearchStore = load_hook()
    bank = json.load(io.open(args.bank, encoding="utf-8"))
    with ResearchStore(args.store) as store:
        nodes = list(store.iter_nodes())
        edges = list(store.iter_edges())
    title_keys = [_title_key(n.title) for n in nodes if str(getattr(n, "kind", "") or "").lower() != "evidence"]
    print(f"store {args.store}: {len(nodes)} nodes, {len(edges)} edges; bank {os.path.basename(args.bank)}")

    # re-check the split against the live store: a target the store has gained moves out of 'unanswerable'
    ans = bank["answerable"][: args.sample] if args.sample else bank["answerable"]
    unans = bank["unanswerable"][: args.sample] if args.sample else bank["unanswerable"]
    present_now = {t["key"]: sum(1 for k in title_keys if any(s in k for s in t["stems"])) for t in ans + unans}
    moved = [t["key"] for t in unans if present_now[t["key"]] > 0]
    missing = [t["key"] for t in ans if present_now[t["key"]] == 0]
    if moved:
        print(f"  {len(moved)} 'unanswerable' targets now have a title in the store and are skipped: {moved}")
    if missing:
        print(f"  {len(missing)} 'answerable' targets have NO title in the store and are skipped: {missing}")
    unans = [t for t in unans if present_now[t["key"]] == 0]
    ans = [t for t in ans if present_now[t["key"]] > 0]

    rows = []
    t0 = time.time()
    for t in ans:
        for phr in ("content", "hinted"):
            q = t["q_" + phr]
            ranked, chosen = run_one(q, ms, retrieve, nodes, edges)
            served = [h.node.title for h in chosen]
            rows.append({
                "set": "answerable", "phrasing": phr, "key": t["key"], "area": t["area"],
                "qid": hashlib.sha1(q.encode("utf-8", "replace")).hexdigest()[:16], "query": q,
                "served": served, "served_ids": [h.node.id for h in chosen],
                "theorem_hit": stem_hit(served, t["stems"]),
                "rank1_hit": bool(ranked) and stem_hit([ranked[0].node.title], t["stems"]),
                "silent": not chosen,
                "n_copies_in_store": present_now[t["key"]],
                "gold_rank": next((i + 1 for i, h in enumerate(ranked) if stem_hit([h.node.title], t["stems"])), None),
                "top_title": ranked[0].node.title[:80] if ranked else "",
                "top_score": round(ms._rank_score(ranked[0]), 3) if ranked else None,
                "served_heads": [" ".join((h.node.body or "").split())[:220] for h in chosen],
            })
    print(f"  answerable: {len(ans)} targets x 2 phrasings at {time.time()-t0:.0f}s", flush=True)
    for t in unans:
        q = t["q"]
        ranked, chosen = run_one(q, ms, retrieve, nodes, edges)
        served = [h.node.title for h in chosen]
        rows.append({
            "set": "unanswerable", "phrasing": "content", "key": t["key"], "area": t["area"],
            "qid": hashlib.sha1(q.encode("utf-8", "replace")).hexdigest()[:16], "query": q,
            "served": served, "served_ids": [h.node.id for h in chosen],
            "false_fire": bool(chosen), "silent": not chosen,
            "top_title": ranked[0].node.title[:80] if ranked else "",
            "top_score": round(ms._rank_score(ranked[0]), 3) if ranked else None,
            "served_heads": [" ".join((h.node.body or "").split())[:220] for h in chosen],
        })
    print(f"  unanswerable: {len(unans)} targets at {time.time()-t0:.0f}s", flush=True)
    with io.open(args.out, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    # summary
    def pct(n, d):
        return f"{100.0*n/d:.1f}%" if d else "n/a"
    A = [r for r in rows if r["set"] == "answerable"]
    U = [r for r in rows if r["set"] == "unanswerable"]
    print("\n== FRESH BANK, live hook path ==")
    for phr in ("content", "hinted"):
        rs = [r for r in A if r["phrasing"] == phr]
        n = len(rs)
        th = sum(r["theorem_hit"] for r in rs); r1 = sum(r["rank1_hit"] for r in rs); si = sum(r["silent"] for r in rs)
        ranks = [r["gold_rank"] for r in rs if r["gold_rank"]]
        med = sorted(ranks)[len(ranks) // 2] if ranks else None
        print(f"  answerable/{phr:7s} n={n}: theorem served {pct(th, n)}  rank-1 {pct(r1, n)}  silent {pct(si, n)}  gold median rank {med}  gold unranked {n - len(ranks)}")
    both = sum(1 for t in ans if all(r["theorem_hit"] for r in A if r["key"] == t["key"]))
    either = sum(1 for t in ans if any(r["theorem_hit"] for r in A if r["key"] == t["key"]))
    print(f"  targets served under BOTH phrasings {both}/{len(ans)}; under at least one {either}/{len(ans)}")
    n = len(U); ff = sum(r["false_fire"] for r in U)
    print(f"  unanswerable n={n}: blocks served (false fires) {pct(ff, n)}")
    print("\n  by area (content / hinted theorem served):")
    for area in sorted(set(r["area"] for r in A)):
        c = [r for r in A if r["area"] == area and r["phrasing"] == "content"]
        h = [r for r in A if r["area"] == area and r["phrasing"] == "hinted"]
        print(f"    {area:18s} n={len(c):2d}  content {pct(sum(r['theorem_hit'] for r in c), len(c)):>6s}  hinted {pct(sum(r['theorem_hit'] for r in h), len(h)):>6s}")
    print("\n  content-only misses (key: top served title):")
    for r in A:
        if r["phrasing"] == "content" and not r["theorem_hit"]:
            print(f"    {r['key']:18s} gold rank {str(r['gold_rank']):>5s}  served: {' | '.join(s[:38] for s in r['served'])}")
    if args.judge:
        rnd = random.Random(args.seed)
        print(f"\n== HAND-JUDGEMENT SAMPLE ({args.judge} content-only answerable, {args.judge // 2} unanswerable) ==")
        for r in rnd.sample([r for r in A if r["phrasing"] == "content"], min(args.judge, len(A) // 2)):
            print(f"\n  [{r['key']}] hit={r['theorem_hit']}  Q: {r['query'][:150]}")
            for s, hd in zip(r["served"], r["served_heads"]):
                print(f"      - {s[:70]} :: {hd[:160]}")
        for r in rnd.sample(U, min(args.judge // 2, len(U))):
            print(f"\n  [absent:{r['key']}] served={len(r['served'])}  Q: {r['query'][:150]}")
            for s, hd in zip(r["served"], r["served_heads"]):
                print(f"      - {s[:70]} :: {hd[:160]}")


if __name__ == "__main__":
    main()
