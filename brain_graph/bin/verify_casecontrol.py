"""verify_casecontrol.py - recompute a correction case-control result from its per-moment rows, and refuse a rows file
that is missing or is not the one the result was built from (28 Sep 2026, claude-825b1305; scheduled audit account01/B01
r66, B01-R13-CORRECTION-CASECONTROL-EVIDENCE-GENERATION-UNBOUND-20260928).

    python verify_casecontrol.py RESULT.json [--rows ROWS.jsonl] [--binding BINDING.json]

The rows file is read from --rows, else from the study folder (~/.claude/brain_graph/study) under the name the result
gives. Its sha256 must equal the result's evidence_generation.rows.sha256; a result built before the study bound its
evidence (27 Sep) is checked against its entry in --binding instead. Then the matched AUC, the per-session AUCs, the AUC
without the largest session and the placebo block are recomputed from the rows and compared with the result. The
bootstrap interval is not recomputed: its draws follow the control draws in one random stream the rows do not carry.
Exit 0 MATCH, 1 MISMATCH, 2 rows file missing, 3 rows digest differs, 4 nothing to check the digest against.
"""
import hashlib
import json
import os
import sys

STUDY = os.path.join(os.path.expanduser("~"), ".claude", "brain_graph", "study")


def sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def auc(cs):
    per = [(sum(1.0 for x in c["controls"] if x < c["case"]) + 0.5 * sum(1.0 for x in c["controls"] if x == c["case"]))
           / len(c["controls"]) for c in cs]
    return sum(per) / len(per) if per else None


def cases_from_rows(rows):
    """The study writes, per case: its placebo row (when there is one), the case row, then its control rows."""
    cases, pending = [], None
    for r in rows:
        if r.get("placebo"):
            pending = r["score"]
        elif r.get("case"):
            c = {"case": r["score"], "controls": [], "sid": r["sid"]}
            if pending is not None:
                c["placebo"] = pending
            pending = None
            cases.append(c)
        elif cases:
            cases[-1]["controls"].append(r["score"])
    return cases


def recompute(rows, with_placebo):
    matched = [c for c in cases_from_rows(rows) if c["controls"]]
    out = {"cases_with_controls": len(matched), "matched_auc": round(auc(matched), 3) if matched else None}
    by_s = {}
    for c in matched:
        by_s.setdefault(c["sid"], []).append(c)
    out["by_session"] = {s: {"cases": len(cs), "auc": round(auc(cs), 3)} for s, cs in
                         sorted(by_s.items(), key=lambda kv: -len(kv[1]))}
    if by_s:
        big = max(by_s, key=lambda s: len(by_s[s]))
        rest = [c for c in matched if c["sid"] != big]
        out["auc_without_largest_session"] = {"left_out": big, "cases": len(rest),
                                              "auc": round(auc(rest), 3) if rest else None}
    if with_placebo:
        both = [c for c in matched if "placebo" in c]
        out["placebo"] = {"cases_with_a_placebo_moment": len(both),
                          "case_auc_same_cases": round(auc(both), 3) if both else None,
                          "placebo_auc": round(auc([dict(c, case=c["placebo"]) for c in both]), 3) if both else None,
                          "case_above_placebo": sum(1 for c in both if c["case"] > c["placebo"]),
                          "case_below_placebo": sum(1 for c in both if c["case"] < c["placebo"])}
    return out


def main():
    args = sys.argv[1:]
    res_path = args[0]
    res = json.load(open(res_path, encoding="utf-8"))
    rows_path = args[args.index("--rows") + 1] if "--rows" in args else os.path.join(STUDY, res.get("rows_file", ""))
    want = ((res.get("evidence_generation") or {}).get("rows") or {}).get("sha256")
    source = "evidence_generation"
    if not want and "--binding" in args:
        b = json.load(open(args[args.index("--binding") + 1], encoding="utf-8"))
        want = ((b.get("results") or {}).get(os.path.basename(res_path)) or {}).get("rows_sha256")
        source = "binding"
    if not os.path.isfile(rows_path):
        print("REFUSED: the rows file %s is missing" % os.path.basename(rows_path))
        return 2
    if not want:
        print("REFUSED: nothing to check the rows file against (no evidence_generation and no binding entry)")
        return 4
    got = sha(rows_path)
    if got != want:
        print("REFUSED: the rows file's sha256 %s is not the %s the %s names" % (got[:16], want[:16], source))
        return 3
    rows = [json.loads(l) for l in open(rows_path, encoding="utf-8") if l.strip()]
    new = recompute(rows, bool(res.get("placebo")))
    diffs = []
    for k in ("cases_with_controls", "matched_auc", "by_session", "auc_without_largest_session"):
        if k in res and res[k] != new.get(k):
            diffs.append(k)
    if res.get("placebo"):
        old_p = {k: v for k, v in res["placebo"].items() if k != "lag_s"}
        if old_p != new.get("placebo"):
            diffs.append("placebo")
    print("%s: rows %s (%d rows, sha256 %s, checked against the %s); matched AUC %s on %d cases%s" % (
        "MATCH" if not diffs else "MISMATCH", os.path.basename(rows_path), len(rows), got[:16], source,
        new["matched_auc"], new["cases_with_controls"], ("; differs: " + ", ".join(diffs)) if diffs else ""))
    return 1 if diffs else 0


if __name__ == "__main__":
    sys.exit(main())
