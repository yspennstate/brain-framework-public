"""wrong_at_creation_control.py - the control account03/B12 asked for (B12-R34-FIRST-RECORD-IMMUTABILITY-NOT-
AUTHENTICITY-20260928), registered 28 Sep 10:2x in THREE_DAY_RUN_2026-09-27.md; claude-825b1305.

A 10 Aug concept row is made wrong FROM ITS FIRST RECORD: in Lagrange's theorem "|H| is a divisor of |G|" becomes
"|G| is a divisor of |H|", and the claim's payload, the ingest's own digest over it and the node body are changed
together in a copy of the live store, in a copy of every backup the first-record builder reads, and in a fixture
repository holding the 15 Aug export with the same change. Then:
  - the first-record re-seal must ACCEPT the row: a record made minutes after a wrong write is a record of the wrong
    write (this is the finding; if the rule refused it, the fixture would not be the attack);
  - the source check (source_check_0810.py, then source_check_0810_formulas.py, Wikipedia leads from the cache,
    nothing refetched) must FLAG it: the two formulas it added no longer sit where the lead has them;
  - every other row must come out of the re-seal and of the source check exactly as it does on the clean copy.
The live store, its backups, the object folder and the math brain repository are only read.

    python wrong_at_creation_control.py POPULATION_DIR SRC_DIR WORK_DIR STUDY_DIR OUT_JSON
STUDY_DIR holds concepts_formulas.json of the clean run and wiki_leads_cache.json.
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
VICTIM = "claim_e80022f127cfaffb090173b1470e8c08573a8eb6a95e0032811284113cbb9a47"      # Lagrange's theorem
GOOD, BAD = "then |H| is a divisor of |G|", "then |G| is a divisor of |H|"
NAMED = ("brain.sqlite3.bak_refw_ingest_20260812", "brain.sqlite3.bak_sandwichref_20260812",
         "brain.sqlite3.bak_before_bundle001_20260829")


LEMMA = ("claim_c9d0bb8354ebd968a84d4a76d6a7cca77c1a4a6c227fdc38065b4f1527e45d6c",   # exchangeable-pair identity
         "= 2 Var(X) - 2 Cov(X, X^L)", "= 2 Var(X) + 2 Cov(X, X^L)")
# --lemma (account01/B07's second fixture, B07-R60-R40, 28 Sep 11:3x): the victim is a campaign lemma made wrong from its
# first record (the sign of the covariance flipped), and the check is lemma_fidelity_check.py against the campaign log,
# which still says 2Var - 2Cov.


def main():
    global VICTIM, GOOD, BAD
    pop, src, work, study, out = sys.argv[1:6]
    lemma_mode = "--lemma" in sys.argv
    if lemma_mode:
        VICTIM, GOOD, BAD = LEMMA
    sys.path.insert(0, src)
    from math_brain.models import canonical_json, content_hash
    py = [sys.executable, "-X", "utf8"]
    tp = os.path.join(work, "pop_wrong_at_creation")
    if os.path.isdir(tp):
        shutil.rmtree(tp)
    os.makedirs(tp)
    copies = [p for p in glob.glob(os.path.join(pop, "brain.sqlite3.bak_iter*")) if not p.endswith(("-shm", "-wal"))]
    copies += [os.path.join(pop, n) for n in NAMED if os.path.exists(os.path.join(pop, n))]
    for p in copies:
        shutil.copy2(p, tp)
    s = sqlite3.connect("file:%s?mode=ro" % os.path.join(pop, "brain.sqlite3").replace("\\", "/"), uri=True)
    d = sqlite3.connect(os.path.join(tp, "brain.sqlite3"))
    s.backup(d)
    s.close()
    d.close()

    bad_payload = None
    touched = []
    for name in sorted(os.listdir(tp)):
        if not name.startswith("brain.sqlite3") or name.endswith(("-shm", "-wal", "-journal")):
            continue
        fp = os.path.join(tp, name)
        mt = os.path.getmtime(fp)
        db = sqlite3.connect(fp)
        row = db.execute("SELECT payload_json FROM claims WHERE id = ?", (VICTIM,)).fetchone()
        if row:
            p = json.loads(row[0])
            assert p["statement"].count(GOOD) == 1, name
            p["statement"] = p["statement"].replace(GOOD, BAD)
            bad_payload = p
            db.execute("UPDATE claims SET payload_json = ?, digest = ? WHERE id = ?",
                       (canonical_json(p), content_hash(p), VICTIM))
            db.execute("UPDATE nodes SET body = ? WHERE id = ?", (p["statement"], VICTIM))
            db.commit()
            touched.append(name)
        db.close()
        os.utime(fp, (mt, mt))                       # the builder dates the 12 and 22 Aug copies by their file time

    # the 15 Aug export, rebuilt in a fixture repository with the same change
    xr = os.path.join(tp, "export_repo")
    os.makedirs(os.path.join(xr, "export", "population"))
    g = ["git", "-C", xr, "-c", "user.name=fixture", "-c", "user.email=fixture@localhost"]
    subprocess.run(["git", "init", "-q", xr], check=True)
    for table in ("claims", "nodes", "edges"):
        raw = subprocess.run(["git", "-C", os.path.expanduser("~/math-brain-main"), "show",
                              "a2e0b08ed9:export/population/%s.jsonl" % table], capture_output=True, check=True).stdout
        lines = raw.decode("utf-8").splitlines()
        for i, ln in enumerate(lines):
            if VICTIM not in ln:
                continue
            r = json.loads(ln)
            if r.get("id") != VICTIM:
                continue
            if table == "claims":
                r["payload_json"], r["digest"] = canonical_json(bad_payload), content_hash(bad_payload)
            elif table == "nodes":
                r["body"] = bad_payload["statement"]
            lines[i] = json.dumps(r, ensure_ascii=False, sort_keys=True)
        io.open(os.path.join(xr, "export", "population", table + ".jsonl"), "w", encoding="utf-8",
                newline="\n").write("\n".join(lines) + "\n")
    subprocess.run(g + ["add", "-A"], check=True, capture_output=True)
    subprocess.run(g + ["commit", "-q", "-m", "fixture export"], check=True, capture_output=True)
    xc = subprocess.run(["git", "-C", xr, "rev-parse", "HEAD"], capture_output=True, check=True).stdout.decode().strip()

    fr = os.path.join(tp, "first.json")
    subprocess.run(py + [os.path.join(BIN, "batch_rows_first_record.py"), tp, xr, fr, "--export-commit", xc],
                   check=True, capture_output=True)
    first = json.load(io.open(fr, encoding="utf-8"))
    vfirst = {t: next((p for p in first["tables"][t]["rows_detail"] if p["id"] == VICTIM), None)
              for t in ("claims", "nodes")}

    def reseal(store, frj, bak, o):
        subprocess.run(py + [os.path.join(BIN, "reseal_math_rows.py"), store, src, bak, o, "--first-record", frj,
                             "--max-gap-min", "60"], check=True, capture_output=True)
        r = json.load(io.open(o, encoding="utf-8"))
        return {(c["table"], c["id"]) for c in r["changed"]}, {(x["table"], x["id"]) for x in r["refused"]}

    ch_bad, ref_bad = reseal(os.path.join(tp, "brain.sqlite3"), fr, os.path.join(tp, NAMED[2]),
                             os.path.join(tp, "reseal_fixture.json"))
    clean_fr = os.path.join(BIN, "..", "results", "BATCH_0810_FIRST_RECORD_2026-09-28.json")
    cw = os.path.join(work, "reseal_clean")
    os.makedirs(cw, exist_ok=True)
    ch_ok, ref_ok = reseal(os.path.join(pop, "brain.sqlite3"), clean_fr, os.path.join(pop, NAMED[2]),
                           os.path.join(cw, "reseal_clean.json"))

    if lemma_mode:
        lf = {}
        for arm, store in (("fixture", os.path.join(tp, "brain.sqlite3")), ("clean", os.path.join(pop, "brain.sqlite3"))):
            o = os.path.join(tp, "lemma_fidelity_%s.json" % arm)
            subprocess.run(py + [os.path.join(BIN, "lemma_fidelity_check.py"), store, o], check=True, capture_output=True)
            lf[arm] = {r["id"]: (r["equations"], r["found_in_source"]) for r in
                       json.load(io.open(o, encoding="utf-8"))["rows"]}
        others_same = all(lf["fixture"][k] == lf["clean"][k] for k in lf["clean"] if k != VICTIM)
        flagged = lf["fixture"][VICTIM][1] < lf["clean"][VICTIM][1]
        res = {"victim": VICTIM, "change": [GOOD, BAD], "sqlite_copies_changed": touched, "fixture_export_commit": xc,
               "victim_first_record": vfirst,
               "first_record_reseal": {"victim_claim_resealed": ("claims", VICTIM) in ch_bad,
                                       "victim_node_resealed": ("nodes", VICTIM) in ch_bad,
                                       "rows_resealed_fixture": len(ch_bad), "rows_resealed_clean": len(ch_ok),
                                       "same_rows_as_clean": ch_bad == ch_ok},
               "lemma_fidelity": {"victim_clean": lf["clean"][VICTIM], "victim_fixture": lf["fixture"][VICTIM],
                                  "victim_flagged": flagged, "every_other_lemma_as_on_the_clean_copy": others_same}}
        ok = (res["first_record_reseal"]["victim_claim_resealed"] and res["first_record_reseal"]["same_rows_as_clean"]
              and flagged and others_same)
        res["verdict"] = ("PASS: the first-record re-seal accepts a lemma wrong from its first record, the fidelity check "
                          "against the campaign log flags it, and every other lemma reads as on the clean copy") if ok \
            else "FAIL"
        json.dump(res, io.open(out, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
        print(json.dumps(res, indent=1, ensure_ascii=False)[:3000])
        return
    objroot = os.path.join(pop, "objects")
    cache = os.path.join(study, "wiki_leads_cache.json")
    subprocess.run(py + [os.path.join(BIN, "source_check_0810.py"), os.path.join(tp, "brain.sqlite3"), objroot,
                         os.path.join(tp, "concepts.json"), "--wiki-cache", cache, "--offline"],
                   check=True, capture_output=True)
    subprocess.run(py + [os.path.join(BIN, "source_check_0810_formulas.py"), os.path.join(tp, "concepts.json"), cache,
                         objroot, os.path.join(tp, "formulas.json"), os.path.join(tp, "side.md")],
                   check=True, capture_output=True)
    fx = {r["id"]: r for r in json.load(io.open(os.path.join(tp, "formulas.json"), encoding="utf-8"))["rows"]}
    cl = {r["id"]: r for r in json.load(io.open(os.path.join(study, "concepts_formulas.json"), encoding="utf-8"))["rows"]}
    sig = lambda r: (r["A_replaced_statement_is_the_artifacts"], r["B_harvest_words_all_kept_in_order"],
                     tuple(r["additions_needing_a_reader"]))
    others_same = all(sig(fx[k]) == sig(cl[k]) for k in cl if k != VICTIM)
    res = {"victim": VICTIM, "change": [GOOD, BAD], "sqlite_copies_changed": touched, "fixture_export_commit": xc,
           "victim_first_record": vfirst,
           "first_record_reseal": {"victim_claim_resealed": ("claims", VICTIM) in ch_bad,
                                   "victim_node_resealed": ("nodes", VICTIM) in ch_bad,
                                   "rows_resealed_fixture": len(ch_bad), "rows_resealed_clean": len(ch_ok),
                                   "same_rows_as_clean": ch_bad == ch_ok},
           "source_check": {"victim_clean": sig(cl[VICTIM]), "victim_fixture": sig(fx[VICTIM]),
                            "victim_flagged": bool(fx[VICTIM]["additions_needing_a_reader"])
                            and not cl[VICTIM]["additions_needing_a_reader"],
                            "every_other_row_as_on_the_clean_copy": others_same}}
    ok = (res["first_record_reseal"]["victim_claim_resealed"] and res["first_record_reseal"]["same_rows_as_clean"]
          and res["source_check"]["victim_flagged"] and others_same)
    res["verdict"] = ("PASS: the first-record re-seal accepts a row wrong from its first record, the source check flags "
                      "it, and every other row comes out as on the clean copy") if ok else "FAIL"
    json.dump(res, io.open(out, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    print(json.dumps(res, indent=1, ensure_ascii=False)[:3000])


if __name__ == "__main__":
    main()
