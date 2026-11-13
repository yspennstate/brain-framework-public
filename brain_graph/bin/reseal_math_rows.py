"""Re-seal the math store rows whose stored digest does not match their payload, and give the claims whose status the
library does not know the status of the claims they replaced (unverified).
Nothing is deleted and no row's text changes.

A row is re-sealed only with evidence that its payload is the one it was written with:
  node   its claim's stored digest verifies over the claim's raw payload, the node body equals the claim statement, and
         the node row is byte-identical in the EARLIEST backup given
  edge   the edge row is byte-identical in the earliest backup, and both endpoints exist
  claim  (status off the RecordStatus list) its stored digest verifies over its raw payload, and the claim row is
         byte-identical in the earliest backup; the status becomes "unverified", the digest is recomputed
A row that fails its evidence is listed and left as it is.

With --first-record FIRST.json (bin/batch_rows_first_record.py) every row must also have been recorded within
--max-gap-min minutes (default 60) of its created_at and be unchanged from that record to now. A backup made twelve days
later proves only that nothing changed the row after it; a record made minutes after the write proves it is what was written.

    python reseal_math_rows.py STORE SRC_DIR EARLIEST_BACKUP OUT_LIST.json [--first-record FIRST.json
                               [--max-gap-min 60]] [--decisions DECISIONS.json --decisions-sha256 HEX] [--apply]
Without --apply the store is not touched: the tool works on a copy it makes beside OUT_LIST.json and reports what the
repair would do, the census and the get_claim sweep after it.

--apply also needs the row-level source decisions: the structural checks above prove a row is what was written,
not that what was written is right, and until this change the apply never read the source check. Now every row the
structural plan would write must have a decision in DECISIONS.json (bind_source_decisions.py output) whose file sha256 is
--decisions-sha256, whose row_sha256 equals the row as stored now, and which passed: reason "source check passes",
source_faithful "yes", mathematically_correct "yes", no unsupported addition (a node also needs its claim's decision to
pass). A row that fails its decision is withheld and listed. A planned row with no decision, a decision made on other
content, or a decisions file other than the one named refuses the whole apply: nothing is written, exit 3.
A decisions file holding more than one decision for a row refuses the apply too, whichever way they point: until then the file was read into a dict and the last
decision silently won, so a failing decision followed by a passing one for the same row wrote it.
A node's claim decision is required, not only read when present: a planned node whose claim has no decision, or
whose claim decision was made on other content than the claim as stored now, refuses the apply like a planned row with
no decision; until then a node whose claim was not itself in the plan was written on its own decision alone.
An edge needs its endpoints' decisions to pass too: an edge whose source or target claim or node has a decision that does not pass is withheld with it; an endpoint
with no decision is outside the batch and is not judged here. Until then an edge was re-sealed on its own decision while
its endpoint stayed withheld.
The writes are made in one transaction against the rows the validation read: the row_sha256 of every planned row is taken when it is planned, and the
write, under BEGIN IMMEDIATE, re-reads each row before its update. If any planned row changed in between, every write
is rolled back and the apply refused (exit 3). Until then the rows were validated on one connection and written by id
on another, so a row changed in between was overwritten.
The write is also held to the schema it was validated under, and to changing nothing but the planned rows: row hashes certify content, not what an UPDATE
does, and a trigger can make the tool's own UPDATE change a row it depends on after that row's last check. The canonical
math store has no triggers, so --apply on a store with any trigger is refused before planning (exit 3). A dry run on such
a store reports the census and the plan, runs nothing on its copy, and says an apply would be refused. The schema is hashed at the
validation and again first thing under BEGIN IMMEDIATE, which also closes the window for a trigger installed in between;
after the UPDATEs, the connection's total changes must equal the planned rows, every dependency is read again, and every
planned row must hold exactly its validated columns with only the planned ones replaced. Any miss rolls back every write.
RESEAL_TEST_BARRIER=PATH (tests only) makes the tool wait, after the validation and before the write transaction, until
PATH exists; it writes PATH.waiting when it starts waiting.
The output records the SQLite library version and the store's journal and locking modes.

A dry run reads a copy the SQLite backup API takes from one read transaction on the store, so the copy holds what the store
holds, pages still in its write-ahead log included; the copy of the main file it used to take left those out, and a
trigger committed to the log but not yet checkpointed was missing from the dry run while --apply, reading the store,
refused on it. The modes in the output are the
store's, not the copy's.
"""
if "-h" in __import__("sys").argv[1:] or "--help" in __import__("sys").argv[1:]:
    print((__doc__ or "").strip() or "usage: python %s" % __import__("os").path.basename(__file__))
    raise SystemExit(0)
import hashlib
import json
import os
import sqlite3
import sys
import time

store, src, backup, out = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
apply = "--apply" in sys.argv
FIRST, GAP = None, 60.0
if "--first-record" in sys.argv:
    _fr = json.load(open(sys.argv[sys.argv.index("--first-record") + 1], encoding="utf-8"))
    FIRST = {(t, p["id"]): p for t, v in _fr["tables"].items() for p in v["rows_detail"]}
    if "--max-gap-min" in sys.argv:
        GAP = float(sys.argv[sys.argv.index("--max-gap-min") + 1])
DECISIONS, DEC_FILE, DEC_SHA, DEC_WANT, DEC_DUPS = None, None, None, None, set()
if "--decisions" in sys.argv:
    DEC_FILE = sys.argv[sys.argv.index("--decisions") + 1]
    _raw = open(DEC_FILE, "rb").read()
    DEC_SHA = hashlib.sha256(_raw).hexdigest()
    DECISIONS = {}
    for _x in json.loads(_raw.decode("utf-8"))["decisions"]:
        _k = (_x["table"], _x["id"])
        if _k in DECISIONS:
            DEC_DUPS.add(_k)                    # two decisions for one row: neither binds
        DECISIONS.setdefault(_k, _x)
if "--decisions-sha256" in sys.argv:
    DEC_WANT = sys.argv[sys.argv.index("--decisions-sha256") + 1].lower()


def refuse(why, extra=None):
    """Nothing is written; the reason goes to OUT_LIST.json and the exit code is 3."""
    res = {"store": store, "applied": False, "refused": why, "decisions_file": DEC_FILE, "decisions_sha256": DEC_SHA,
           "sqlite_version": sqlite3.sqlite_version}
    res.update(extra or {})
    json.dump(res, open(out, "w", encoding="utf-8"), indent=1)
    print("REFUSED:", why)
    sys.exit(3)


def row_sha256(conn, table, rid):
    """The same hash bind_source_decisions.py records: every column of the row as stored, in column order."""
    r = conn.execute("SELECT * FROM %s WHERE id = ?" % table, (rid,)).fetchone()
    if r is None:
        return None
    return hashlib.sha256(json.dumps([[k, r[k]] for k in r.keys()], ensure_ascii=False,
                                     default=str).encode("utf-8")).hexdigest()


def schema_sha256(conn):
    """Every schema object - tables, indexes, views, triggers - with its SQL. A change here changes what an UPDATE does."""
    rows = conn.execute("SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name, tbl_name").fetchall()
    return hashlib.sha256(json.dumps([list(r) for r in rows], ensure_ascii=False).encode("utf-8")).hexdigest()


def triggers(conn):
    return [{"name": r[0], "table": r[1]} for r in
            conn.execute("SELECT name, tbl_name FROM sqlite_master WHERE type = 'trigger' ORDER BY name").fetchall()]


def sqlite_modes(conn):
    """The SQLite library and the store's journal and locking modes, for the output."""
    return {"version": sqlite3.sqlite_version, "journal_mode": conn.execute("PRAGMA journal_mode").fetchone()[0],
            "locking_mode": conn.execute("PRAGMA locking_mode").fetchone()[0]}


def planned_sets(table, args):
    """The columns a planned UPDATE writes, with their values (the SQL in the plan is one of three shapes)."""
    if table == "claims":
        return {"payload_json": args[0], "digest": args[1]}
    return {"digest": args[0]}


def passed(d):
    if d.get("reason") != "source check passes":
        return False, "source check: %s" % d.get("reason")
    if d.get("source_faithful") != "yes":
        return False, "source_faithful %s" % d.get("source_faithful")
    if d.get("mathematically_correct") != "yes":
        return False, "mathematically_correct %s" % d.get("mathematically_correct")
    if d.get("unsupported_addition"):
        return False, "unsupported addition"
    return True, ""


def near_write(table, rid):
    """recorded within GAP minutes of being written and unchanged from that record to now (always true without --first-record)"""
    if FIRST is None:
        return True
    p = FIRST.get((table, rid))
    return bool(p) and p["minutes_created_to_first_record"] is not None and \
        p["minutes_created_to_first_record"] <= GAP and not p["differs_from_live_in"]
sys.path.insert(0, src)
from math_brain.models import (Claim, ClaimKind, EdgeKind, GraphEdge, GraphNode, NodeKind, RecordStatus,  # noqa: E402
                               canonical_json, content_hash)

VALID = {s.value for s in RecordStatus}


def ro(p):
    c = sqlite3.connect("file:%s?mode=ro" % p.replace("\\", "/"), uri=True)
    c.row_factory = sqlite3.Row
    return c


def snapshot(src_path, dst_path):
    """A copy of the store's committed state for a dry run: SQLite's backup API, all pages in one step, reads them in one
    read transaction on the store, write-ahead log included; the copy is written as a rollback-journal file."""
    for p in (dst_path, dst_path + "-wal", dst_path + "-shm", dst_path + "-journal"):
        if os.path.exists(p):
            os.remove(p)
    src = sqlite3.connect("file:%s?mode=ro" % src_path.replace("\\", "/"), uri=True)
    dst = sqlite3.connect(dst_path)
    try:
        src.backup(dst)
        dst.execute("PRAGMA journal_mode=DELETE")
    finally:
        dst.close()
        src.close()


def node_of(r):
    return GraphNode(id=r["id"], kind=NodeKind(r["kind"]), title=r["title"], body=r["body"],
                     metadata=json.loads(r["metadata_json"]), created_at=r["created_at"])


def edge_of(r):
    return GraphEdge(id=r["id"], source_id=r["source_id"], target_id=r["target_id"], kind=EdgeKind(r["kind"]),
                     rationale=r["rationale"], weight=r["weight"], metadata=json.loads(r["metadata_json"]),
                     created_at=r["created_at"])


def same_row(b, table, r):
    o = b.execute("SELECT * FROM %s WHERE id = ?" % table, (r["id"],)).fetchone()
    return o is not None and all(o[k] == r[k] for k in r.keys())


def census(c):
    n = sum(1 for r in c.execute("SELECT * FROM nodes") if node_of(r).digest() != r["digest"])
    e = sum(1 for r in c.execute("SELECT * FROM edges") if edge_of(r).digest() != r["digest"])
    bad = 0
    for r in c.execute("SELECT id, payload_json, digest, created_at FROM claims"):
        p = json.loads(r["payload_json"])
        try:
            p["kind"], p["status"], p["assumptions"] = ClaimKind(p["kind"]), RecordStatus(p["status"]), tuple(p["assumptions"])
            cl = Claim(**p)
            if cl.id != r["id"] or cl.created_at != r["created_at"] or cl.digest() != r["digest"]:
                bad += 1
        except Exception:
            bad += 1
    return {"node_mismatch": n, "edge_mismatch": e, "claims_get_claim_would_fail": bad}


def main():
    if apply and (DECISIONS is None or DEC_WANT is None):
        refuse("--apply needs --decisions FILE and --decisions-sha256 HEX: a structural re-seal is written only for rows "
               "whose source decision passed")
    if DECISIONS is not None and DEC_WANT is not None and DEC_SHA != DEC_WANT:
        refuse("the decisions file's sha256 is %s, not the %s named" % (DEC_SHA, DEC_WANT))
    if apply and DEC_DUPS:
        refuse("the decisions file holds more than one decision for %d row(s): which one binds is ambiguous; "
               "nothing written" % len(DEC_DUPS), {"duplicates": [{"table": t, "id": i} for t, i in sorted(DEC_DUPS)]})
    target = store
    if not apply:
        target = os.path.join(os.path.dirname(os.path.abspath(out)), "reseal_dryrun.sqlite3")
        snapshot(store, target)
    live, old = ro(target), ro(backup)
    if apply:
        modes = sqlite_modes(live)
    else:
        st = ro(store)
        modes = sqlite_modes(st)
        st.close()
    trig = triggers(live)
    if trig and apply:
        live.close()
        old.close()
        refuse("the store has %d trigger(s); the math store's schema has none, and a trigger can make a planned UPDATE "
               "change rows the plan did not name; nothing written" % len(trig), {"triggers": trig})
    before = census(live)
    plan, refused = [], []
    for r in live.execute("SELECT id, payload_json, digest, created_at FROM claims"):
        p = json.loads(r["payload_json"])
        if p.get("status") in VALID:
            continue
        ok = content_hash(p) == r["digest"] and same_row(old, "claims", r)
        if not ok or not near_write("claims", r["id"]):
            refused.append({"table": "claims", "id": r["id"], "why": "digest or backup check failed" if not ok else
                            "no unchanged record within %g min of the write" % GAP})
            continue
        q = dict(p, status="unverified")
        typed = dict(q, kind=ClaimKind(q["kind"]), status=RecordStatus(q["status"]), assumptions=tuple(q["assumptions"]))
        cl = Claim(**typed)
        assert cl.digest() == content_hash(q), "the typed and raw digests disagree"
        plan.append(("claims", r["id"], {"status": [p["status"], "unverified"], "digest": [r["digest"], cl.digest()]},
                     ("UPDATE claims SET payload_json = ?, digest = ? WHERE id = ?", (canonical_json(q), cl.digest(), r["id"]))))
    for r in live.execute("SELECT * FROM nodes"):
        d = node_of(r).digest()
        if d == r["digest"]:
            continue
        c = live.execute("SELECT payload_json, digest FROM claims WHERE id = ?", (r["id"],)).fetchone()
        p = json.loads(c["payload_json"]) if c else {}
        ok = (c is not None and content_hash(p) == c["digest"] and (r["body"] or "").strip() == (p.get("statement") or "").strip()
              and same_row(old, "nodes", r))
        if not ok or not near_write("nodes", r["id"]):
            refused.append({"table": "nodes", "id": r["id"], "why": "claim digest, statement or backup check failed"
                            if not ok else "no unchanged record within %g min of the write" % GAP})
            continue
        plan.append(("nodes", r["id"], {"digest": [r["digest"], d]}, ("UPDATE nodes SET digest = ? WHERE id = ?", (d, r["id"]))))
    for r in live.execute("SELECT * FROM edges"):
        d = edge_of(r).digest()
        if d == r["digest"]:
            continue
        ends = all(live.execute("SELECT 1 FROM nodes WHERE id = ?", (x,)).fetchone() for x in (r["source_id"], r["target_id"]))
        if not (ends and same_row(old, "edges", r)) or not near_write("edges", r["id"]):
            refused.append({"table": "edges", "id": r["id"], "why": "endpoint or backup check failed"
                            if not (ends and same_row(old, "edges", r)) else
                            "no unchanged record within %g min of the write" % GAP})
            continue
        plan.append(("edges", r["id"], {"digest": [r["digest"], d]}, ("UPDATE edges SET digest = ? WHERE id = ?", (d, r["id"]))))
    withheld, unmatched = [], []
    if DECISIONS is not None:
        keep = []
        for t, i, c, sq in plan:
            dx = DECISIONS.get((t, i))
            if (t, i) in DEC_DUPS or (t == "nodes" and ("claims", i) in DEC_DUPS):
                unmatched.append({"table": t, "id": i, "why": "more than one decision for this row or its claim"})
                continue
            if dx is None:
                unmatched.append({"table": t, "id": i, "why": "no decision for this row"})
                continue
            if not dx.get("row_sha256") or dx["row_sha256"] != row_sha256(live, t, i):
                unmatched.append({"table": t, "id": i, "why": "the decision was made on other content (row_sha256 %s)"
                                  % ("missing" if not dx.get("row_sha256") else "differs")})
                continue
            ok, why = passed(dx)
            if t == "nodes":
                cx = DECISIONS.get(("claims", i))
                if cx is None:
                    unmatched.append({"table": t, "id": i, "why": "no decision for its claim"})
                    continue
                if not cx.get("row_sha256") or cx["row_sha256"] != row_sha256(live, "claims", i):
                    unmatched.append({"table": t, "id": i, "why": "its claim's decision was made on other content "
                                      "(row_sha256 %s)" % ("missing" if not cx.get("row_sha256") else "differs")})
                    continue
                if ok:
                    ok, why2 = passed(cx)
                    why = "its claim's decision: " + why2 if not ok else ""
            if t == "edges" and ok:
                er = live.execute("SELECT source_id, target_id FROM edges WHERE id = ?", (i,)).fetchone()
                for end in (er["source_id"], er["target_id"]) if er else ():
                    for et in ("claims", "nodes"):
                        ex = DECISIONS.get((et, end))
                        if ex is None:
                            continue        # outside the review: its current row is bound below and read again at the write
                        if not passed(ex)[0]:
                            ok, why = False, "its endpoint's %s decision: %s" % (et[:-1], passed(ex)[1])
                            break
                        if not ex.get("row_sha256") or ex["row_sha256"] != row_sha256(live, et, end):
                            # a passing verdict counts only for the content it was made on; until then an endpoint's verdict was read
                            # and its content was not
                            ok, why = False, "its endpoint's %s decision was made on other content (row_sha256 %s)" % (
                                et[:-1], "missing" if not ex.get("row_sha256") else "differs")
                            break
                    if not ok:
                        break
            if not ok:
                withheld.append({"table": t, "id": i, "why": why})
                continue
            keep.append((t, i, c, sq))
        if apply and unmatched:
            live.close()
            old.close()
            refuse("%d row(s) the structural plan would write have no matching source decision; nothing written"
                   % len(unmatched), {"unmatched": unmatched, "withheld": withheld})
        plan = keep
    if trig:
        # a dry run on a store with triggers: the census and the plan are what a dry run is for, and
        # a plan that is not run cannot fire a trigger; nothing runs on the copy, and an apply stays refused
        live.close()
        old.close()
        res = {"store": store, "applied_to": None, "applied": False, "before": before, "after": None, "sqlite": modes,
               "triggers": trig, "triggers_present": True, "canonical_schema": False, "apply_blocked_by_triggers": True,
               "nothing_to_apply": not plan,
               "first_record_rule": {"max_gap_min": GAP, "rows_known": len(FIRST)} if FIRST is not None else None,
               "changed": [{"table": t, "id": i, "change": c} for t, i, c, _ in plan], "refused": refused,
               "decisions": None if DECISIONS is None else {"file": DEC_FILE, "sha256": DEC_SHA, "named_sha256": DEC_WANT,
                                                            "withheld": withheld, "unmatched": unmatched,
                                                            "duplicates": [{"table": t, "id": i} for t, i in sorted(DEC_DUPS)]}}
        json.dump(res, open(out, "w", encoding="utf-8"), indent=1)
        print("dry run on a store with %d trigger(s): before" % len(trig), before, "| rows planned", len(plan),
              "| refused", len(refused), "| nothing was run on the copy, and --apply on this store is refused")
        return
    pre = {(t, i): row_sha256(live, t, i) for t, i, _c, _sq in plan}     # the rows as validated
    # ...and every row a planned row depends on: a node's claim, and an edge's two endpoints as claims and as nodes. None
    # of them is written, but each one's content is what made the repair right, so each is read again inside the write
    # transaction and a change refuses the whole write.
    deps = {}
    for t, i, _c, _sq in plan:
        keys = [("claims", i)] if t == "nodes" else []
        if t == "edges":
            er = live.execute("SELECT source_id, target_id FROM edges WHERE id = ?", (i,)).fetchone()
            keys = [(et, end) for end in ((er["source_id"], er["target_id"]) if er else ()) for et in ("claims", "nodes")]
        for k in keys:
            if k not in pre and k not in deps:
                deps[k] = row_sha256(live, *k)
    rows0 = {}
    for t, i, _c, _sq in plan:
        r = live.execute("SELECT * FROM %s WHERE id = ?" % t, (i,)).fetchone()
        rows0[(t, i)] = {k: r[k] for k in r.keys()} if r is not None else None
    schema0 = schema_sha256(live)
    live.close()
    old.close()
    barrier = os.environ.get("RESEAL_TEST_BARRIER")
    if barrier:
        open(barrier + ".waiting", "w").close()
        for _ in range(600):
            if os.path.exists(barrier):
                break
            time.sleep(0.1)
    rw = sqlite3.connect(target, isolation_level=None, timeout=30)
    rw.row_factory = sqlite3.Row
    moved = []
    rw.execute("BEGIN IMMEDIATE")
    try:
        # the write lock is held from here, so nothing can change the schema or a row until COMMIT or ROLLBACK
        if schema_sha256(rw) != schema0:
            moved.append({"schema": True, "triggers": triggers(rw)})
        for (dt, di), h in ([] if moved else sorted(deps.items())):
            if row_sha256(rw, dt, di) != h:
                moved.append({"table": dt, "id": di, "dependency": True})
                break
        changes0 = rw.total_changes
        for t, i, _c, (sql, args) in ([] if moved else plan):
            if row_sha256(rw, t, i) != pre[(t, i)] or rw.execute(sql, args).rowcount != 1:
                moved.append({"table": t, "id": i})
                break
        if not moved and rw.total_changes - changes0 != len(plan):
            # total_changes counts rows a trigger or a foreign-key action wrote; rowcount does not
            moved.append({"side_effects": rw.total_changes - changes0 - len(plan)})
        for (dt, di), h in ([] if moved else sorted(deps.items())):
            if row_sha256(rw, dt, di) != h:
                moved.append({"table": dt, "id": di, "dependency": True, "after_write": True})
                break
        for t, i, _c, (_sql, args) in ([] if moved else plan):
            want = dict(rows0[(t, i)] or {}, **planned_sets(t, args))
            r = rw.execute("SELECT * FROM %s WHERE id = ?" % t, (i,)).fetchone()
            if r is None or {k: r[k] for k in r.keys()} != want:
                moved.append({"table": t, "id": i, "after_write": True})
                break
        rw.execute("ROLLBACK" if moved else "COMMIT")
    except BaseException:
        rw.execute("ROLLBACK")
        raise
    finally:
        rw.close()
    if moved:
        refuse("the schema, a planned row or a row it depends on changed between the validation and the write, or the write "
               "changed more than the planned rows; every write was rolled back", {"moved": moved,
                                                                                   "dependencies_checked": len(deps)})
    after = census(ro(target))
    res = {"store": store, "applied_to": target, "applied": apply, "before": before, "after": after, "sqlite": modes,
           "dependencies_checked": len(deps), "schema_sha256": schema0, "post_write_checked": len(plan),
           "first_record_rule": {"max_gap_min": GAP, "rows_known": len(FIRST)} if FIRST is not None else None,
           "changed": [{"table": t, "id": i, "change": c} for t, i, c, _ in plan], "refused": refused,
           "decisions": None if DECISIONS is None else {"file": DEC_FILE, "sha256": DEC_SHA, "named_sha256": DEC_WANT,
                                                        "withheld": withheld, "unmatched": unmatched,
                                                        "duplicates": [{"table": t, "id": i} for t, i in sorted(DEC_DUPS)]}}
    json.dump(res, open(out, "w", encoding="utf-8"), indent=1)
    print("before", before, "| after", after, "| rows changed", len(plan), "| refused", len(refused),
          "| withheld by decision", len(withheld), "| unmatched", len(unmatched),
          "| applied to", "the store" if apply else "a copy")


if __name__ == "__main__":
    main()
