#!/usr/bin/env python3
"""eval_paraphrase_reach.py - does the math hook still find a stored result when the question names it in other words?
(nightly gate instrument, recorded and not floored; campaign mb10d, 25 Sep)

The 40 open-problem ledger questions of the locate bank L2, once in their stored wording and once with the problem's stored
title replaced by a description in other words (state/bank_locate_l2_paraphrase_v1.json, written before any retrieval ran on
it). For each: the hook as a subprocess with a fresh session id; "node" = the answer's own node is among the served cards (the
sharp measure), "value" = a page number in the block equals the key's (the value-in-block parser; it runs a little above node).
Keys are read from the quarantine only to score and are never printed. First reading, cand65 live on 25 Sep: stored node 40 of
40, paraphrase node 11 of 40 (24 with the plain-question coverage floor off) - memory
the_math_hook_is_lexical_and_paraphrases_lose_most_of_what_it_finds_2026_09_25.

TWO NATURAL FRAMINGS (4 Oct 2026, claude-4a88bc43). The bank's template wraps each description in a 50-word frame that
shares its words with the boilerplate of every ledger card, so the shortest boilerplate card wins: 10 of the 22 template
blocks the coverage floor silenced on 4 Oct were led by one card. The same 40 descriptions in a short natural question reach
their answer node 27 of 40 under the same gates (template 14), and in "What is known about ...?" only 12 - mostly refused
at the entry gate, the real weakness. Both natural forms run beside the template, which keeps its history since 25 Sep:
memory the_paraphrase_template_halves_reworded_research_recall_2026_10_04.

    python eval_paraphrase_reach.py [--out X.jsonl] [--hook PATH] [--env K=V ...] [--jobs 2]
prints   PARAPHRASE stored n=40 served S node N value V | paraphrase n=40 served S node N value V
           | natural_where n=40 served S node N value V | natural_known n=40 served S node N value V
"""
import argparse
import concurrent.futures as cf
import importlib.util
import io
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "state")
LB = os.path.join(os.path.expanduser("~"), "brain-repo", "12_cognitive_architecture", "brain_improvement",
                  "campaign_2026-09-11_20h_mathbrain", "locate_bank")
KD = os.path.join(os.path.expanduser("~"), "bank_l1_keys_quarantine")
BANK = os.path.join(STATE, "bank_locate_l2_paraphrase_v1.json")
NATURAL = (("natural_where", "Where is the open problem about {desc} posed, and on which page?"),
           ("natural_known", "What is known about {desc}?"))
FORMS = ("stored", "paraphrase") + tuple(name for name, _ in NATURAL)


import os as _ko_os, sys as _ko_sys  # noqa: E402
_ko_sys.path.insert(0, _ko_os.path.join(_ko_os.path.expanduser("~"), ".claude", "brain_graph", "bin"))
from quarantine_rows import keep_old  # noqa: E402  a rerun keeps the previous output (R13 trap 1, 27 Sep): brain data is never deleted


def main():
    spec = importlib.util.spec_from_file_location("vib", os.path.join(HERE, "eval_value_in_block.py"))
    vib = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(vib)
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="")
    ap.add_argument("--hook", default=vib.HOOK)
    ap.add_argument("--env", action="append", default=[])
    ap.add_argument("--jobs", type=int, default=2)
    a = ap.parse_args()
    vib.HOOK = a.hook
    if not os.path.isfile(vib.HOOK):
        raise SystemExit("eval_paraphrase_reach: no such hook: %s" % vib.HOOK)
    env = dict(os.environ)
    for kv in a.env:
        k, _, v = kv.partition("=")
        env[k] = v
    orig = {it["id"]: it["question"] for it in json.load(io.open(os.path.join(LB, "bank_l2_items.json"), encoding="utf-8"))}
    para = json.load(io.open(BANK, encoding="utf-8"))
    keys = {k["id"]: k for k in json.load(io.open(os.path.join(KD, "bank_l2_keys.json"), encoding="utf-8"))}
    work = []
    for it in para["items"]:
        work.append((it["id"], "stored", orig[it["id"]]))
        work.append((it["id"], "paraphrase", para["template"].format(desc=it["desc"])))
        for name, frame in NATURAL:
            work.append((it["id"], name, frame.format(desc=it["desc"])))

    def one(w):
        iid, form, q = w
        # _run returns (block, ids, seconds, invalid) since 30 Sep 03:32 (account03/B01): until this line followed it the
        # instrument raised on its first call and the gate's row read "paraphrase summary line not found"
        block, ids, secs, bad = vib._run(q, env, timeout=180)
        if bad:
            return {"id": iid, "form": form, "served": False, "vib": False, "node": False, "n_cards": 0,
                    "seconds": round(secs, 2), "invalid": bad}
        nums = vib._numbers(block, "openproblem_page")
        k = keys[iid]
        src = (k.get("source_node") or "")
        return {"id": iid, "form": form, "served": bool(block), "vib": bool(block) and vib._accept(k, nums),
                "node": bool(src) and any(i.startswith(src[:16]) or src.startswith(i[:16]) for i in ids),
                "n_cards": len(ids), "seconds": round(secs, 2)}

    with cf.ThreadPoolExecutor(max(1, a.jobs)) as ex:
        rows = list(ex.map(one, work))
    out = a.out or os.path.join(STATE, "paraphrase_reach_%s.jsonl" % time.strftime("%Y%m%d_%H%M%S"))
    io.open(keep_old(out), "w", encoding="utf-8").write("\n".join(json.dumps(r) for r in rows) + "\n")
    # an invalid call is in no count and is counted at the end of the line, as eval_value_in_block does
    bad = {}
    for r in rows:
        if r.get("invalid"):
            bad[r["invalid"]] = bad.get(r["invalid"], 0) + 1
    parts = []
    for form in FORMS:
        rs = [r for r in rows if r["form"] == form and not r.get("invalid")]
        parts.append("%s n=%d served %d node %d value %d" % (form, len(rs), sum(r["served"] for r in rs),
                                                            sum(r["node"] for r in rs), sum(r["vib"] for r in rs)))
    print("PARAPHRASE " + " | ".join(parts) + " | invalid %d%s" % (
        sum(bad.values()), (" (%s)" % "; ".join("%s: %d" % kv for kv in sorted(bad.items()))) if bad else ""))
    print("rows:", out)


if __name__ == "__main__":
    main()
