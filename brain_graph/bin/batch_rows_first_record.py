"""batch_rows_first_record.py - for each claim, node and edge the math store created on 10 Aug: the first backup that
holds it, how long after its created_at that backup was copied, and whether the row is identical in every record from
that one to the live store.

The batch's night left iteration backups beside the store (brain.sqlite3.bak_iterNNN_HHMMSS). A backup's file time is
the store's last write before the copy (the copy keeps it), so the copy time is read from the name: HHMMSS local
(UTC+3) on 10 Aug. Later records: the 12 Aug backups (copy time from the file time, an upper bound), the math brain
repository's export committed 15 Aug 17:01 UTC (a2e0b08ed9), the 22 Aug backup, and the live store.

    python batch_rows_first_record.py POPULATION_DIR REPO OUT_JSON [--export-commit HASH]
"""
if "-h" in __import__("sys").argv[1:] or "--help" in __import__("sys").argv[1:]:
    print((__doc__ or "").strip() or "usage: python %s" % __import__("os").path.basename(__file__))
    raise SystemExit(0)
import datetime
import io
import json
import os
import re
import sqlite3
import subprocess
import sys

COLS = {"claims": ("id", "payload_json", "digest", "created_at"),
        "nodes": ("id", "kind", "title", "body", "metadata_json", "digest", "created_at"),
        "edges": ("id", "source_id", "target_id", "kind", "rationale", "weight", "metadata_json", "digest", "created_at")}
UTC3 = datetime.timezone(datetime.timedelta(hours=3))


def db_rows(path, table):
    db = sqlite3.connect("file:%s?mode=ro" % path.replace("\\", "/"), uri=True)
    try:
        cols = COLS[table]
        return {r[0]: dict(zip(cols, r)) for r in db.execute("select %s from %s" % (", ".join(cols), table))}
    except sqlite3.Error:
        return None
    finally:
        db.close()


def export_rows(repo, commit, table):
    raw = subprocess.run(["git", "-C", repo, "show", "%s:export/population/%s.jsonl" % (commit, table)],
                         capture_output=True, check=True).stdout.decode("utf-8")
    return {r["id"]: r for r in (json.loads(l) for l in raw.splitlines() if l.strip())}


def main():
    pop, repo, out = sys.argv[1:4]
    # --export-commit lets a fixture supply its own export (the wrong-at-creation control, 28 Sep 10:4x); the real
    # record is the math brain repository's export of 15 Aug 17:01 UTC.
    xc = sys.argv[sys.argv.index("--export-commit") + 1] if "--export-commit" in sys.argv else "a2e0b08ed9"
    records = []                                        # (copy time UTC, label, loader)
    for name in os.listdir(pop):
        m = re.match(r"brain\.sqlite3\.bak_iter\d+_(\d\d)(\d\d)(\d\d)$", name)
        if m:
            t = datetime.datetime(2026, 8, 10, int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=UTC3)
            records.append((t.astimezone(datetime.timezone.utc), name, os.path.join(pop, name)))
    for name in ("brain.sqlite3.bak_refw_ingest_20260812", "brain.sqlite3.bak_sandwichref_20260812",
                 "brain.sqlite3.bak_before_bundle001_20260829"):
        p = os.path.join(pop, name)
        if os.path.exists(p):
            t = datetime.datetime.fromtimestamp(os.path.getmtime(p), tz=datetime.timezone.utc)
            records.append((t, name + " (file time)", p))
    records.append((datetime.datetime(2026, 8, 15, 17, 1, 25, tzinfo=datetime.timezone.utc), "export " + xc, None))
    records.sort(key=lambda r: r[0])
    live_path = os.path.join(pop, "brain.sqlite3")
    res = {"records": [(r[0].isoformat(), r[1]) for r in records], "tables": {}}
    for table in COLS:
        live = db_rows(live_path, table)
        batch = {k: v for k, v in live.items() if str(v["created_at"]).startswith("2026-08-10")}
        loaded = [(t, label, export_rows(repo, xc, table) if p is None else db_rows(p, table))
                  for t, label, p in records]
        per = []
        for rid, row in sorted(batch.items()):
            created = datetime.datetime.fromisoformat(str(row["created_at"]).replace("Z", "+00:00"))
            first, changed = None, []
            for t, label, rows in loaded:
                if rows is None or rid not in rows:
                    continue
                if first is None:
                    first = (t, label)
                if any(str(rows[rid].get(c)) != str(row[c]) for c in COLS[table]):
                    changed.append(label)
            per.append({"id": rid, "created_at": row["created_at"],
                        "first_record": first[1] if first else None,
                        "minutes_created_to_first_record": round((first[0] - created).total_seconds() / 60, 1)
                        if first else None,
                        "differs_from_live_in": changed})
        gaps = [p["minutes_created_to_first_record"] for p in per if p["minutes_created_to_first_record"] is not None]
        res["tables"][table] = {"rows": len(per), "never_changed": sum(1 for p in per if not p["differs_from_live_in"]),
                                "first_record_within_60_min": sum(1 for g in gaps if g <= 60),
                                "max_minutes_to_first_record": max(gaps) if gaps else None, "rows_detail": per}
        print(table, "rows", len(per), "| identical from first record to live:",
              res["tables"][table]["never_changed"], "| first record within 60 min:",
              res["tables"][table]["first_record_within_60_min"], "| longest gap (min):",
              res["tables"][table]["max_minutes_to_first_record"])
    json.dump(res, io.open(out, "w", encoding="utf-8"), indent=1)


if __name__ == "__main__":
    main()
