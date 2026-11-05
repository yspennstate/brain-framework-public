"""compare_dates_placebo.py - per-question check of the date placebo (27 Sep, claude-825b1305). Pre-registered in TWO_DAY_RUN_2026-09-26.md:
every question WITHOUT a date ranks exactly as in the dates arm (same document lengths, no date token in the query), so
any undated difference between placebo and dates falsifies the reading "the undated gain is length normalization".
Reads the rows file the study wrote (quarantine; ids, flags and counts only are printed)."""
import json
import os
import sys

HOME = os.path.expanduser("~")
DR = os.path.join(HOME, ".claude", "dreaming")
BIN = os.path.join(HOME, ".claude", "brain_graph", "bin")
sys.path.insert(0, DR)
sys.path.insert(0, BIN)
os.environ["BRAIN_TOKENS_DATES"] = "0"
import bm25  # noqa: E402

Q = os.path.join(HOME, "bank_recall_keys_quarantine")
rows = json.load(open(sys.argv[1] if len(sys.argv) > 1 else os.path.join(Q, "tokenizer_variant_rows.json"),
                      encoding="utf-8"))
assert {"base", "dates", "placebo"} <= set(rows), sorted(rows)

import eval_brain_recall_fresh as fresh  # noqa: E402
from eval_prompt_recall_fullstack import episodes  # noqa: E402
import eval_expansion_agent_recalls as ear  # noqa: E402

texts = {"bank": [q for q, _g in fresh.BANK + fresh.BANK_V2_EXTRA],
         "ep": [p for _s, p, _r in episodes()],
         "agent": [r["q"] for r, _o in ear.judged_recalls()]}
solved = set(json.load(open(os.path.join(Q, "headless_solver_sessions_20260926.json"), encoding="utf-8")).values())
keys = [k for k in (json.loads(l) for l in open(os.path.join(Q, "recall_keys.jsonl"), encoding="utf-8") if l.strip())
        if k["id"] not in solved]


def top5(r):
    return 0 < r <= 5


def report(tag, b, d, p, dated):
    n = len(b)
    und = [i for i in range(n) if not dated[i]]
    dat = [i for i in range(n) if dated[i]]
    diff_und = [i for i in und if p[i] != d[i]]
    diff_dat = [i for i in dat if p[i] != d[i]]
    print("%-13s n %4d  dated %3d | placebo != dates: undated %d, dated %d | top5 base %d placebo %d dates %d"
          % (tag, n, len(dat), len(diff_und), len(diff_dat), sum(map(top5, b)), sum(map(top5, p)), sum(map(top5, d))))
    for name, x in (("placebo", p), ("dates", d)):
        ins = [i for i in range(n) if top5(x[i]) and not top5(b[i])]
        outs = [i for i in range(n) if top5(b[i]) and not top5(x[i])]
        print("   %-8s vs base: top5 in %s out %s  (in: dated %d / undated %d)"
              % (name, len(ins), len(outs), sum(1 for i in ins if dated[i]), sum(1 for i in ins if not dated[i])))
    if tag == "bank":   # his 56 are not quarantined: show the moved questions by index and rank
        for i in range(n):
            if b[i] != d[i] or b[i] != p[i]:
                print("     q%02d dated=%s ranks base %s placebo %s dates %s" % (i, dated[i], b[i], p[i], d[i]))
    return len(diff_und)


bad = 0
for tag in ("bank", "ep", "agent"):
    b, d, p = rows["base"][tag], rows["dates"][tag], rows["placebo"][tag]
    if len(texts[tag]) != len(b):
        print("%s: %d texts now vs %d rows - source moved since the run; comparing ranks without the dated split"
              % (tag, len(texts[tag]), len(b)))
        print("   placebo != dates on %d of %d" % (sum(1 for x, y in zip(p, d) if x != y), len(b)))
        continue
    bad += report(tag, b, d, p, [bool(bm25.date_tokens(t)) for t in texts[tag]])
for form in ("q_paraphrase", "q_verbatim"):
    if form not in rows["base"]:
        continue
    ids = [k["id"] for k in keys if k["id"] in rows["base"][form]]
    kt = {k["id"]: k[form] for k in keys}
    b = [rows["base"][form][i] for i in ids]
    d = [rows["dates"][form][i] for i in ids]
    p = [rows["placebo"][form][i] for i in ids]
    bad += report(form, b, d, p, [bool(bm25.date_tokens(kt[i])) for i in ids])
print("FALSIFIED: %d undated questions rank differently between placebo and dates" % bad if bad else
      "prediction holds: every undated question ranks exactly as in the dates arm")
