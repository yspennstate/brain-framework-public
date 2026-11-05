#!/usr/bin/env python3
"""Join blind judge verdicts back to the cases' mechanical surface classes.

The judges never saw the surface class or the damp hypothesis; agreement here could
have diverged. Reports: (a) domain_at_play rate by surface group - the model-pass
upgrade of the n=36 hand check behind the context damp; (b) followed rate where the
domain WAS at play - the judgment-reflex heeding number the 07-15 pilot could not
compute; (c) per-reflex splits (thin cells - direction, not rates).

Installed 07-16; expects judge_cases.jsonl + judged_*.json in the cwd.
"""
import io, json, os, sys, collections

if any(a in ("-h", "--help") for a in sys.argv[1:]):
    # Discovery is not execution: this file works at module scope, so the guard
    # must precede the first statement that runs.
    print(__doc__ or os.path.basename(__file__))
    raise SystemExit(0)

SP = os.getcwd()
cases = {c["case_id"]: c for c in
         (json.loads(l) for l in io.open(os.path.join(SP, "judge_cases.jsonl"), encoding="utf-8"))}

verdicts = {}
for fn in sys.argv[1:]:
    txt = io.open(fn, encoding="utf-8").read().strip()
    if txt.startswith("```"):
        txt = txt.strip("`")
        txt = txt[txt.find("["):txt.rfind("]") + 1]
    arr = json.loads(txt)
    for v in arr:
        verdicts[v["case_id"]] = v

print("judged %d / %d cases" % (len(verdicts), len(cases)))
TRIO = {"sim_flatters", "risk_vs_measured_record", "ib_marks"}

by = collections.defaultdict(lambda: {"n": 0, "dom_yes": 0, "dom_no": 0, "dom_unclear": 0,
                                      "fol_yes": 0, "fol_no": 0, "fol_noop": 0})
for cid, v in verdicts.items():
    c = cases.get(cid)
    if not c:
        continue
    fam = "trio" if c["rid"] in TRIO else "comparison"
    for key in ((fam, c["group"]), (c["rid"], c["group"])):
        s = by[key]
        s["n"] += 1
        s["dom_" + ("yes" if v["domain_at_play"] == "yes" else
                    "no" if v["domain_at_play"] == "no" else "unclear")] += 1
        if v["domain_at_play"] == "yes":
            s["fol_" + ("yes" if v["followed"] == "yes" else
                        "no" if v["followed"] == "no" else "noop")] += 1

def show(title, keys):
    print("\n== %s ==" % title)
    print("%-38s %3s  %s  %s" % ("group", "n", "domain-at-play y/n/?", "followed y/n/noop (of domain-yes)"))
    for k in keys:
        s = by[k]
        if not s["n"]:
            continue
        print("%-38s %3d  %d/%d/%d %14s %d/%d/%d" %
              ("%s x %s" % k, s["n"], s["dom_yes"], s["dom_no"], s["dom_unclear"], "",
               s["fol_yes"], s["fol_no"], s["fol_noop"]))

show("pooled families", [(f, g) for f in ("trio", "comparison")
                         for g in ("selfref", "trading", "other")])
rids = sorted({c["rid"] for c in cases.values()})
show("per reflex (thin cells - direction only)",
     [(r, g) for r in rids for g in ("selfref", "trading", "other")])

with io.open(os.path.join(SP, "judged_joined.jsonl"), "w", encoding="utf-8") as f:
    for cid, v in sorted(verdicts.items()):
        c = cases.get(cid, {})
        f.write(json.dumps({**v, "rid": c.get("rid"), "surface": c.get("surface"),
                            "group": c.get("group"), "session": c.get("session")},
                           ensure_ascii=False) + "\n")
print("\njoined verdicts -> judged_joined.jsonl")
