"""reseal_laundering_control.py - the attack B07 named (B07-R34-MATH-RESEAL-PREBACKUP-LAUNDERING-021), run on copies:
a batch row altered before the 22 Aug backup was taken, consistently (claim statement, claim digest over the raw
payload, node body), so the re-seal's first rule sees nothing wrong. The old rule (identical in the 22 Aug backup) must
re-seal it - the finding reproduced; the rule with --first-record must refuse it, and must still re-seal every other row
it re-sealed before (28 Sep 09:4x, claude-825b1305). The live store and its backups are only read.

    python reseal_laundering_control.py POPULATION_DIR SRC_DIR WORK_DIR OUT_JSON
"""
import glob
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys

BIN = os.path.dirname(os.path.abspath(__file__))


def main():
    pop, src, work, out = sys.argv[1:5]
    sys.path.insert(0, src)
    from math_brain.models import canonical_json, content_hash
    tp = os.path.join(work, "pop_laundering")
    if os.path.isdir(tp):
        shutil.rmtree(tp)
    os.makedirs(tp)
    for p in glob.glob(os.path.join(pop, "brain.sqlite3.bak_iter*")) + [
            os.path.join(pop, n) for n in ("brain.sqlite3.bak_refw_ingest_20260812",
                                           "brain.sqlite3.bak_sandwichref_20260812",
                                           "brain.sqlite3.bak_before_bundle001_20260829")]:
        if not p.endswith(("-shm", "-wal")):
            shutil.copy2(p, tp)                      # keeps the file time the first-record tool reads
    src_db = sqlite3.connect("file:%s?mode=ro" % os.path.join(pop, "brain.sqlite3").replace("\\", "/"), uri=True)
    dst = sqlite3.connect(os.path.join(tp, "brain.sqlite3"))
    src_db.backup(dst)
    src_db.close()
    dst.close()
    # the victim: the first batch claim whose first record came within an hour of its write
    first = json.load(io.open(os.path.join(BIN, "..", "results", "BATCH_0810_FIRST_RECORD_2026-09-28.json"),
                              encoding="utf-8"))
    victim = next(p["id"] for p in first["tables"]["claims"]["rows_detail"]
                  if p["minutes_created_to_first_record"] is not None and p["minutes_created_to_first_record"] <= 60)
    for name in ("brain.sqlite3", "brain.sqlite3.bak_before_bundle001_20260829"):
        db = sqlite3.connect(os.path.join(tp, name))
        pj = db.execute("SELECT payload_json FROM claims WHERE id = ?", (victim,)).fetchone()[0]
        p = json.loads(pj)
        p["statement"] = p["statement"] + " (altered)"
        db.execute("UPDATE claims SET payload_json = ?, digest = ? WHERE id = ?", (canonical_json(p), content_hash(p), victim))
        db.execute("UPDATE nodes SET body = ? WHERE id = ?", (p["statement"], victim))
        db.commit()
        db.close()
    py = [sys.executable, "-X", "utf8"]
    fr = os.path.join(tp, "first.json")
    subprocess.run(py + [os.path.join(BIN, "batch_rows_first_record.py"), tp, os.path.expanduser("~/math-brain-main"), fr],
                   check=True, capture_output=True)
    store = os.path.join(tp, "brain.sqlite3")
    bak = os.path.join(tp, "brain.sqlite3.bak_before_bundle001_20260829")
    res = {"victim": victim}
    for arm, extra in (("old_rule", []), ("first_record_rule", ["--first-record", fr, "--max-gap-min", "60"])):
        o = os.path.join(tp, arm + ".json")
        subprocess.run(py + [os.path.join(BIN, "reseal_math_rows.py"), store, src, bak, o] + extra, check=True,
                       capture_output=True)
        r = json.load(io.open(o, encoding="utf-8"))
        changed = {(c["table"], c["id"]) for c in r["changed"]}
        res[arm] = {"victim_claim_resealed": ("claims", victim) in changed,
                    "victim_node_resealed": ("nodes", victim) in changed,
                    "rows_changed": len(r["changed"]), "refused": [(x["table"], x["id"][:24], x["why"]) for x in r["refused"]]}
    ok = (res["old_rule"]["victim_claim_resealed"] and res["old_rule"]["victim_node_resealed"]
          and not res["first_record_rule"]["victim_claim_resealed"] and not res["first_record_rule"]["victim_node_resealed"])
    res["verdict"] = "PASS: the old rule launders the altered row, the first-record rule refuses it" if ok else "FAIL"
    json.dump(res, io.open(out, "w", encoding="utf-8"), indent=1)
    print(json.dumps(res, indent=1)[:2500])


if __name__ == "__main__":
    main()
