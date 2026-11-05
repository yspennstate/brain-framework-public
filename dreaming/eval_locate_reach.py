#!/usr/bin/env python3
"""eval_locate_reach.py - does the math hook REACH the node a private fact lives in?

The locate banks of 2026-09-11 (L1: 40 self-avoiding-walk counts + 20 campaign constants; L2: 40 "Open problem"
ledger entries) found three retrieval defects in one afternoon - dropped minus signs, a campaign cap starving the
answer node, a cost gate with no word for "open problem" - and each showed up first as the SOURCE NODE NOT BEING
SERVED. That half of the bank needs no solver: run the question through the real hook and ask whether the node id
the answer lives in is among the emitted source ids. About a minute for 100 questions; no keys, no answers - the
bank file carries the question and the node id only.

    python eval_locate_reach.py                 run the bank, print one summary line per family, write state/locate_reach_<stamp>.json
    python eval_locate_reach.py --bank FILE     another bank of {id, family, question, source_node}
    python eval_locate_reach.py --jobs 3

Summary line (the regression gate parses it):
    REACH saw 40/40 campaign 15/20 openproblem 40/40 | served saw 40 campaign 18 openproblem 40 | median 1.2 s
"""
from __future__ import annotations

import io
import json
import os
import re
import statistics
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

if any(a in ("-h", "--help") for a in sys.argv[1:]):
    # Discovery is not execution: this file does its work at module scope, so the
    # guard must come before the first statement that runs.
    print(__doc__ or os.path.basename(__file__))
    raise SystemExit(0)


HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "state")
DEFAULT_BANK = os.path.join(STATE, "locate_reach_bank_20260911.json")
SRC_RE = re.compile('^[ ]*[(](?:source-id:[ ]*)?([A-Za-z0-9_]{16,})[ ]*(?:[|]|[)])', re.M)  # id, not label prose: the label lost "source-id:" on 2026-09-13


def run_hook(question, sess):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    t0 = time.time()
    try:
        out = subprocess.run([sys.executable, os.path.join(HERE, "math_sense.py")],
                             input=json.dumps({"prompt": question, "session_id": sess}),
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180, env=env)
        raw = (out.stdout or "").strip()
    except subprocess.TimeoutExpired:
        raw = ""
    ctx = ""
    if raw:
        try:
            ctx = json.loads(raw)["hookSpecificOutput"]["additionalContext"]
        except Exception:  # noqa: BLE001
            ctx = ""
    return ctx, round(time.time() - t0, 2)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    bank = DEFAULT_BANK
    jobs = 3
    if "--bank" in argv:
        bank = argv[argv.index("--bank") + 1]
    if "--jobs" in argv:
        jobs = int(argv[argv.index("--jobs") + 1])
    items = json.load(open(bank, encoding="utf-8"))["items"]
    stamp = time.strftime("%Y%m%d_%H%M%S")

    def one(it):
        # a fresh session id per question per run: the hook's per-session dedup would otherwise silence repeats
        ctx, dt = run_hook(it["question"], "reach-%s-%s" % (stamp, it["id"]))
        ids = SRC_RE.findall(ctx)
        # an item may name a second source node (the L3 two-fact pairs); it is reached only when BOTH are served
        srcs = [s for s in (it.get("source_node"), it.get("source_node_b")) if s]
        return {"id": it["id"], "family": it["family"], "served": bool(ctx), "emitted": ids, "has_src": bool(srcs),
                "reach": bool(srcs) and all(s in ids for s in srcs), "seconds": dt,
                "rank": (ids.index(srcs[0]) + 1) if srcs and srcs[0] in ids else None}

    with ThreadPoolExecutor(max_workers=jobs) as ex:
        rows = list(ex.map(one, items))
    fams = []
    _ORDER = ["saw", "campaign", "openproblem", "saw_pair"]
    for f in sorted({r["family"] for r in rows}, key=lambda f: _ORDER.index(f) if f in _ORDER else 9):
        fams.append(f)
    summary = {"as_of": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "bank": bank, "n": len(rows), "families": {}}
    for f in fams:
        fr = [r for r in rows if r["family"] == f]
        # items whose key recorded no source node (the L1 campaign constants) count for "served" only, never for reach
        summary["families"][f] = {"n": len(fr), "n_with_source": sum(1 for r in fr if r["has_src"]), "reach": sum(1 for r in fr if r["reach"]),
                                  "served": sum(1 for r in fr if r["served"]), "rank1": sum(1 for r in fr if r["rank"] == 1)}
    summary["median_seconds"] = round(statistics.median(r["seconds"] for r in rows), 2) if rows else None
    os.makedirs(STATE, exist_ok=True)
    outp = os.path.join(STATE, "locate_reach_%s.json" % stamp)
    with io.open(outp, "w", encoding="utf-8") as fh:
        json.dump({"summary": summary, "rows": rows}, fh, ensure_ascii=False, indent=1)
    line = "REACH " + " ".join("%s %d/%d" % (f, summary["families"][f]["reach"], summary["families"][f]["n_with_source"]) for f in fams)
    line += " | served " + " ".join("%s %d/%d" % (f, summary["families"][f]["served"], summary["families"][f]["n"]) for f in fams)
    line += " | rank1 " + " ".join("%s %d" % (f, summary["families"][f]["rank1"]) for f in fams)
    line += " | median %s s" % summary["median_seconds"]
    print(line)
    misses = [r for r in rows if r["has_src"] and not r["reach"]]
    for r in misses[:20]:
        print("  MISS %s %s served=%s emitted=%d" % (r["family"], r["id"], r["served"], len(r["emitted"])))
    print("rows:", outp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
