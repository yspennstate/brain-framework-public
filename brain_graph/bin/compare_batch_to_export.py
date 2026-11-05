"""compare_batch_to_export.py - are the math store's 10 Aug rows the same today as in the earliest record git holds of
them? (R34, B07-R34-MATH-RESEAL-PREBACKUP-LAUNDERING-021; 28 Sep 09:3x, claude-825b1305)

The re-seal's evidence rule compared each row with the 22 Aug backup, which proves only that nothing changed it after
22 Aug. The math brain repository committed an export of the store on 15 Aug 17:01 UTC (a2e0b08ed9, "Deploy the
population store as versionable JSONL"); git dates that record. This compares every claim, node and edge the live store
created on 10 Aug, field by field, with its row in that export, and reports which are identical, which differ (and in
which fields), and which the export lacks.

    python compare_batch_to_export.py STORE REPO COMMIT OUT_JSON
"""
import io
import json
import sqlite3
import subprocess
import sys

COLS = {"claims": ("id", "payload_json", "digest", "created_at"),
        "nodes": ("id", "kind", "title", "body", "metadata_json", "digest", "created_at"),
        "edges": ("id", "source_id", "target_id", "kind", "rationale", "weight", "metadata_json", "digest", "created_at")}


def export_rows(repo, commit, table):
    raw = subprocess.run(["git", "-C", repo, "show", "%s:export/population/%s.jsonl" % (commit, table)],
                         capture_output=True, check=True).stdout.decode("utf-8")
    return {r["id"]: r for r in (json.loads(l) for l in raw.splitlines() if l.strip())}


def main():
    store, repo, commit, out = sys.argv[1:5]
    db = sqlite3.connect("file:%s?mode=ro" % store, uri=True)
    res = {"store": store, "commit": commit, "tables": {}}
    for table, cols in COLS.items():
        ex = export_rows(repo, commit, table)
        live = db.execute("select %s from %s where created_at like '2026-08-10%%'" % (", ".join(cols), table)).fetchall()
        same, differ, missing = [], [], []
        for row in live:
            r = dict(zip(cols, row))
            e = ex.get(r["id"])
            if e is None:
                missing.append(r["id"])
                continue
            bad = [c for c in cols if str(e.get(c)) != str(r[c])]
            (differ if bad else same).append({"id": r["id"], "fields": bad} if bad else r["id"])
        res["tables"][table] = {"created_10_aug": len(live), "identical_in_export": len(same), "differ": differ,
                                "absent_from_export": missing}
        print(table, "created 10 Aug:", len(live), "| identical in the export:", len(same), "| differ:", len(differ),
              "| absent:", len(missing))
    json.dump(res, io.open(out, "w", encoding="utf-8"), indent=1)


if __name__ == "__main__":
    main()
