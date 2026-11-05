"""test_reseal_semantic_interlock.py - the re-seal writes only rows whose source decision passed (28 Sep, claude-825b1305;
scheduled audit account03/B07 R40, B07-R40-SOURCE-VERDICT-NOT-ENFORCED-BY-APPLY-001).

Every arm runs reseal_math_rows.py --apply on byte copies of the math store and of its earliest backup, made in WORK_DIR;
the live store and its backups are only read. B07's acceptance test, and the cases it names:
 A1, A2  the two wrong-at-creation fixtures (the concept row: in Lagrange's theorem |H| and |G| swapped; the lemma row:
         the covariance's sign flipped), written into the store copy AND the backup copy with the digest recomputed, so
         the structural plan proposes the row: the apply is refused and nothing is written
 A3      a planned row with no decision: refused, nothing written
 A4      a decisions file whose sha256 is not the one named: refused
 A5      an extra structural row (an edge outside the batch given a stale digest in store and backup alike): refused
 A6      the rows whose decision did not pass (source-partial, partly or conditionally correct, a late first record): left
         as they are while the passing rows are re-sealed
 A7      --apply with no decisions: refused
 P1      a clean copy with the bound decisions: exactly the rows the decisions pass are re-sealed, read back from the copy
 N1      the tool before this change (OLD) re-seals A1's victim on --apply: the defect B07 found (the check can fail)
 A8      (28 Sep 16:xx, account02/B07 R48, B07-R48-MATH-RESEAL-DUPLICATE-DECISION-LAST-WINS-028) a row whose decision
         failed given a second, passing decision after it, and the same two in the other order: both refused, nothing
         written; the dry run lists the row as unmatched
 N2      with --old-dup OLD2, the tool before that change re-seals the row whose failing decision a later passing one
         overrode (the last decision won; the check can fail)
 A9-A12  (28 Sep 18:xx, account01/B07 r66, B07-R66-R48-NODE-CLAIM-DECISION-OPTIONAL-IF-CLAIM-NOT-PLANNED) a node-only
         plan: a planned node's claim given a listed status and its digest recomputed in store and backup, so the claim
         leaves the plan and the node stays in it. A9 its claim decision removed: refused, nothing written. A10 a failing
         claim decision made on the claim as it now stands: the node withheld. A11 a passing one: the node re-sealed.
         A12 a passing claim decision made on the claim's earlier content: refused.
 N3      with --old-claim OLD3, the tool before that change re-seals the node in A9 and in A12 (the check can fail)
 A13     (28 Sep 18:xx, account01/B07 r67, B07-R67-R48-EDGE-RESEAL-CROSSES-WITHHELD-ENDPOINT) on the clean copy every edge
         whose source or target has a decision that does not pass is withheld, and an edge whose endpoints pass is
         re-sealed; P1's expectation includes the rule
 A14     (28 Sep 18:xx, account02/B12 auto11, B12-R83-RESEAL-VALIDATE-WRITE-TOCTOU) the tool validates, waits at its test
         barrier, another process commits a change to one planned row, the tool resumes: refused (3), no planned row
         written, the other process's change kept
 N4      with --old-race OLD4, the tool before that change, given the same barrier, overwrites the other process's change
 A15     (28 Sep 19:xx, account02/B07 r69, B07-R24-MATH-RESEAL-UNPLANNED-ENDPOINT-TOCTOU-031) a re-sealed edge points at a
         claim no decision covers; another process changes that claim at the barrier: refused (3), the change kept, no
         planned digest moved, the dependency named
 N5      with --old-dep OLD5, the tool before that change re-seals the edge while its endpoint changed under it
 A16     a passing endpoint decision whose row_sha256 is not the endpoint's content withholds the edge (dry run)
 N6      with --old-dep OLD5, the tool before that change plans the edge on that stale decision
 A17     (28 Sep 22:xx, account02/B13 r71, B13-R53-SCHEMA-TRIGGER-TOCTOU-035) at the barrier another process installs a
         trigger that rewrites a re-sealed edge's endpoint claim whenever an edge digest is updated: refused (3) on the
         schema, the endpoint untouched, no planned digest moved
 N7      with --old-trig OLD6, the tool before that change commits under the same trigger and the endpoint is rewritten
 A18     (account01/B07 r71, B07-R71-R53-PREEXISTING-TRIGGER-SIDE-EFFECT-BYPASSES-DEPENDENCY-CLOSURE) the same trigger is
         in the store before the run: refused (3) before planning, the store copy byte-identical
 N8      with --old-trig OLD6, the tool before that change commits and the endpoint is rewritten
 A20     a WAL checkpoint at the barrier changes no row and no schema: the apply commits and re-seals what P1 re-seals
 A21     a copy of the tool with the trigger refusal and the schema check switched off still refuses the A18 store: the
         after-write checks (total changes, dependencies, planned rows' columns) catch the side effect by themselves
 P6      (29 Sep, account04/B13 r53, T-R53-INSTALLED-VALIDATE-WRITE-INTERLOCK) the tool killed at its barrier, after the
         validation and before it opens the write connection: it holds no write lock, nothing is written, the store copy
         is byte-identical
 P7      the tool killed inside the write transaction, every planned update made and COMMIT not run (the tool runs
         unchanged under PAUSE, which stops its write connection at COMMIT): meanwhile no other connection can begin a
         write and a reader sees none of the updates; afterwards the next connection finds integrity ok, no planned digest
         moved and every table as before. Four arms: WAL (the live store's mode) and a rollback journal, each with the
         default cache and with a 10-page cache, which puts the uncommitted pages on disk before the kill (into the WAL,
         or into the database file with the original pages in the journal)
 N9      the same kill just after COMMIT leaves every planned row re-sealed: the P7 checks can fail
 P9      with --audited-blob BLOB, the tool's git blob must be the one the audit read
 A22     (29 Sep, account04/B13 r58, B13-R58-NOOP-UNRELATED-TRIGGER-REFUSAL-20260929) a store with nothing to repair and
         a trigger no planned UPDATE can fire (AFTER INSERT ON audit_log), dry run: exit 0, the zero census, no change,
         the trigger named, the store byte-identical
 N10     with --old-noop OLD7, the tool before that change refuses the same dry run (the check can fail)
 A23     nothing to repair and an AFTER UPDATE ON claims trigger: the dry run reports with the apply blocked, and --apply
         is refused before planning
 A24     the live store's plan and the unrelated trigger: the dry run lists the plan, runs nothing on its copy and marks
         the apply blocked; --apply is refused before planning, the store byte-identical (A25 is A18, A26 is A17)
 A27     nothing to repair, --apply, a trigger installed at the barrier: refused on the schema
 A28     the output records the SQLite version and the store's journal and locking modes (account04/B13 r53)
 W1-W8   (29 Sep, account04/B13 r4, B13-R58-WAL-SIDECAR-OMITTED-BY-DRYRUN-COPY-20260929-01) a trigger or a row change
         committed to the write-ahead log and not yet checkpointed, with the writer's connection still open: the dry run
         names the trigger and marks the apply blocked as --apply refuses it (W1, W4), counts the row change as a direct
         read after a checkpoint does (W3), in a rollback-journal store too (W5), under checkpoints running beside it
         (W6), leaves the store's main file and log byte-identical (W7), and records the store's own modes (W8). With
         --old-copy OLD8, the tool that copied the main file alone misses the trigger and the row (W2)
B13's P1-P9 map onto these: P1 is P1, P2 A14, P3 A15, P4 A17, P5 A18 and A21 (the store has no trigger, and every foreign
key is NO ACTION with enforcement off on the tool's connection), P6 P6, P7 P7, P8 N4 (with --old-race), P9 P9.

    python test_reseal_semantic_interlock.py WORK_DIR [OLD_RESEAL.py] [--old-dup OLD2_RESEAL.py]
                                             [--old-claim OLD3_RESEAL.py] [--old-race OLD4_RESEAL.py]
                                             [--old-dep OLD5_RESEAL.py] [--old-trig OLD6_RESEAL.py]
                                             [--old-noop OLD7_RESEAL.py] [--old-copy OLD8_RESEAL.py]
                                             [--tool RESEAL.py] [--audited-blob BLOB]
                                             [--keep]
--tool tests another copy of the tool (default: the installed one).
"""
import hashlib
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import time

H = os.path.expanduser("~")
BG = os.path.join(H, ".claude", "brain_graph")
TOOL = sys.argv[sys.argv.index("--tool") + 1] if "--tool" in sys.argv else os.path.join(BG, "bin", "reseal_math_rows.py")
POP = os.path.join(H, "math-brain-build", "local", "population")
SRC = os.path.join(H, "math-brain-build", "src")
BAK = "brain.sqlite3.bak_before_bundle001_20260829"
FIRST = os.path.join(BG, "results", "BATCH_0810_FIRST_RECORD_2026-09-28.json")
DEC = os.path.join(BG, "results", "SOURCE_CHECK_0810_DECISIONS_BOUND_2026-09-28.json")
CONCEPT = ("claim_e80022f127cfaffb090173b1470e8c08573a8eb6a95e0032811284113cbb9a47",
           "then |H| is a divisor of |G|", "then |G| is a divisor of |H|")
LEMMA = ("claim_c9d0bb8354ebd968a84d4a76d6a7cca77c1a4a6c227fdc38065b4f1527e45d6c",
         "= 2 Var(X) - 2 Cov(X, X^L)", "= 2 Var(X) + 2 Cov(X, X^L)")
WORK = sys.argv[1]
OLD = sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith("--") else None
OLD_DUP = sys.argv[sys.argv.index("--old-dup") + 1] if "--old-dup" in sys.argv else None
OLD_CLAIM = sys.argv[sys.argv.index("--old-claim") + 1] if "--old-claim" in sys.argv else None
OLD_RACE = sys.argv[sys.argv.index("--old-race") + 1] if "--old-race" in sys.argv else None
OLD_DEP = sys.argv[sys.argv.index("--old-dep") + 1] if "--old-dep" in sys.argv else None
OLD_TRIG = sys.argv[sys.argv.index("--old-trig") + 1] if "--old-trig" in sys.argv else None
AUDITED = sys.argv[sys.argv.index("--audited-blob") + 1] if "--audited-blob" in sys.argv else None
OLD_NOOP = sys.argv[sys.argv.index("--old-noop") + 1] if "--old-noop" in sys.argv else None
OLD_COPY = sys.argv[sys.argv.index("--old-copy") + 1] if "--old-copy" in sys.argv else None
UNRELATED = ("CREATE TABLE audit_log (note TEXT)", "CREATE TABLE audit_sink (note TEXT)",
             "CREATE TRIGGER audit_insert_only AFTER INSERT ON audit_log BEGIN "
             "INSERT INTO audit_sink(note) VALUES (NEW.note); END")
ON_CLAIMS = ("CREATE TRIGGER claims_touch AFTER UPDATE ON claims BEGIN "
             "UPDATE claims SET created_at = created_at WHERE id = NEW.id; END",)
PAUSE_TOOL = os.path.join(WORK, "pause_at_commit.py")
PAUSE = '''"""The reseal tool, run unchanged. Its write connection - the one it opens with isolation_level=None - stops at
COMMIT, just before it (WHEN=before) or just after it (WHEN=after), writes what the connection holds to BARRIER.waiting
and waits for BARRIER, which the test never makes: the test kills the process there."""
import json
import os
import runpy
import sqlite3
import sys
import time

TOOL, BARRIER, PAGES, WHEN = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]
_connect = sqlite3.connect


def _stop(conn):
    def q(sql):
        return sqlite3.Connection.execute(conn, sql).fetchone()[0]
    info = {"in_transaction": conn.in_transaction, "total_changes": conn.total_changes,
            "journal_mode": q("PRAGMA journal_mode"), "locking_mode": q("PRAGMA locking_mode"),
            "cache_size": q("PRAGMA cache_size"), "synchronous": q("PRAGMA synchronous"),
            "foreign_keys": q("PRAGMA foreign_keys"), "sqlite_version": sqlite3.sqlite_version, "when": WHEN}
    with open(BARRIER + ".tmp", "w") as f:
        json.dump(info, f)
    os.replace(BARRIER + ".tmp", BARRIER + ".waiting")
    while not os.path.exists(BARRIER):
        time.sleep(0.05)


class Stopping(sqlite3.Connection):
    def execute(self, sql, *args):
        commit = isinstance(sql, str) and sql.strip().upper() == "COMMIT"
        if commit and WHEN == "before":
            _stop(self)
        cur = sqlite3.Connection.execute(self, sql, *args)
        if commit and WHEN == "after":
            _stop(self)
        return cur


def connect(database, *args, **kw):
    if kw.get("isolation_level", "") is None and "factory" not in kw:
        conn = _connect(database, *args, factory=Stopping, **kw)
        if PAGES:
            sqlite3.Connection.execute(conn, "PRAGMA cache_size = %d" % PAGES)
        return conn
    return _connect(database, *args, **kw)


sqlite3.connect = connect
sys.argv = [TOOL] + sys.argv[5:]
runpy.run_path(TOOL, run_name="__main__")
'''
sys.path.insert(0, SRC)
from math_brain.models import canonical_json, content_hash  # noqa: E402

fails, ran = [], []


def check(ok, what):
    print(("PASS  " if ok else "FAIL  ") + what)
    ran.append(what)
    if not ok:
        fails.append(what)


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def arm(name):
    """A fresh directory with byte copies of the live store and the earliest backup (sqlite's backup API, read-only)."""
    d = os.path.join(WORK, name)
    if os.path.isdir(d):
        shutil.rmtree(d)
    os.makedirs(d)
    for src_name in ("brain.sqlite3", BAK):
        s = sqlite3.connect("file:%s?mode=ro" % os.path.join(POP, src_name).replace("\\", "/"), uri=True)
        t = sqlite3.connect(os.path.join(d, src_name))
        s.backup(t)
        s.close()
        t.close()
    return d


def empty_arm(name, extra=()):
    """A store with the math store's schema and no rows, its backup the same: nothing for the re-seal to do. extra: SQL
    run on the store afterwards (not on the backup)."""
    d = os.path.join(WORK, name)
    if os.path.isdir(d):
        shutil.rmtree(d)
    os.makedirs(d)
    s = sqlite3.connect("file:%s?mode=ro" % os.path.join(POP, "brain.sqlite3").replace("\\", "/"), uri=True)
    ddl = [r[0] for r in s.execute("SELECT sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite%' "
                                   "ORDER BY CASE type WHEN 'table' THEN 0 ELSE 1 END, name")]
    s.close()
    for n, sqls in (("brain.sqlite3", ddl + list(extra)), (BAK, ddl)):
        t = sqlite3.connect(os.path.join(d, n))
        for q in sqls:
            t.execute(q)
        t.commit()
        t.close()
    return d


def add_sql(d, sqls):
    db = sqlite3.connect(os.path.join(d, "brain.sqlite3"))
    for q in sqls:
        db.execute(q)
    db.commit()
    db.close()


def plant(d, victim):
    """The wrong-at-creation fixture: the claim's statement changed, its digest recomputed and the node body changed, in
    the store copy and the backup copy alike, so every structural check still passes."""
    vid, good, bad = victim
    for n in ("brain.sqlite3", BAK):
        db = sqlite3.connect(os.path.join(d, n))
        row = db.execute("SELECT payload_json FROM claims WHERE id = ?", (vid,)).fetchone()
        if row:
            p = json.loads(row[0])
            assert p["statement"].count(good) == 1, (n, vid)
            p["statement"] = p["statement"].replace(good, bad)
            db.execute("UPDATE claims SET payload_json = ?, digest = ? WHERE id = ?", (canonical_json(p), content_hash(p), vid))
            db.execute("UPDATE nodes SET body = ? WHERE id = ?", (p["statement"], vid))
            db.commit()
        db.close()


def run(tool, d, decisions=None, named=None, first=True, apply=True):
    out = os.path.join(d, "out.json")
    cmd = [sys.executable, "-X", "utf8", tool, os.path.join(d, "brain.sqlite3"), SRC, os.path.join(d, BAK), out]
    if first:
        cmd += ["--first-record", FIRST, "--max-gap-min", "60"]
    if decisions:
        cmd += ["--decisions", decisions]
    if named:
        cmd += ["--decisions-sha256", named]
    if apply:
        cmd += ["--apply"]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    res = json.load(open(out, encoding="utf-8")) if os.path.exists(out) else {}
    return r.returncode, res, (r.stdout + r.stderr).strip()[-300:]


def claim_row(d, vid):
    db = sqlite3.connect(os.path.join(d, "brain.sqlite3"))
    r = db.execute("SELECT payload_json, digest FROM claims WHERE id = ?", (vid,)).fetchone()
    db.close()
    return r


def digests(d, keys):
    db = sqlite3.connect(os.path.join(d, "brain.sqlite3"))
    out = {k: (db.execute("SELECT digest FROM %s WHERE id = ?" % k[0], (k[1],)).fetchone() or [None])[0] for k in keys}
    db.close()
    return out


def row_hash(d, table, rid):
    """The tool's row_sha256: every column of the row as stored, in column order."""
    db = sqlite3.connect(os.path.join(d, "brain.sqlite3"))
    db.row_factory = sqlite3.Row
    r = db.execute("SELECT * FROM %s WHERE id = ?" % table, (rid,)).fetchone()
    db.close()
    return hashlib.sha256(json.dumps([[k, r[k]] for k in r.keys()], ensure_ascii=False, default=str).encode("utf-8")).hexdigest()


def node_only(d, cid):
    """Give the claim a listed status, its digest recomputed, in the store copy and the backup copy alike: the claim
    leaves the structural plan and its node stays in it."""
    for n in ("brain.sqlite3", BAK):
        db = sqlite3.connect(os.path.join(d, n))
        p = json.loads(db.execute("SELECT payload_json FROM claims WHERE id = ?", (cid,)).fetchone()[0])
        p["status"] = "unverified"
        db.execute("UPDATE claims SET payload_json = ?, digest = ? WHERE id = ?", (canonical_json(p), content_hash(p), cid))
        db.commit()
        db.close()


def edge_ends(ids):
    """edge id -> (source id, target id), read from the live store read-only"""
    db = sqlite3.connect("file:%s?mode=ro" % os.path.join(POP, "brain.sqlite3").replace("\\", "/"), uri=True)
    out = {i: tuple(db.execute("SELECT source_id, target_id FROM edges WHERE id = ?", (i,)).fetchone() or ())
           for i in ids}
    db.close()
    return out


def race_run(tool, d, decisions, named, planned, victim=None):
    """The tool with RESEAL_TEST_BARRIER set: once it waits, change one claim in the store copy and commit, then release
    it - the first planned claim, or the claim named by victim (a row outside the plan). Returns (rc, result, the changed
    claim kept, planned digests moved)."""
    barrier = os.path.join(d, "barrier")
    out = os.path.join(d, "out.json")
    cmd = [sys.executable, "-X", "utf8", tool, os.path.join(d, "brain.sqlite3"), SRC, os.path.join(d, BAK), out,
           "--first-record", FIRST, "--max-gap-min", "60", "--decisions", decisions, "--decisions-sha256", named, "--apply"]
    before = digests(d, planned)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=dict(os.environ,
                            RESEAL_TEST_BARRIER=barrier))
    for _ in range(1200):
        if os.path.exists(barrier + ".waiting") or proc.poll() is not None:
            break
        time.sleep(0.1)
    victim = victim or sorted(k for k in planned if k[0] == "claims")[0]
    db = sqlite3.connect(os.path.join(d, "brain.sqlite3"))
    p = json.loads(db.execute("SELECT payload_json FROM claims WHERE id = ?", (victim[1],)).fetchone()[0])
    p["note_by_another_process"] = "NEWER_AUTHORITATIVE"
    db.execute("UPDATE claims SET payload_json = ? WHERE id = ?", (canonical_json(p), victim[1]))
    db.commit()
    db.close()
    open(barrier, "w").close()
    proc.communicate(timeout=600)
    res = json.load(open(out, encoding="utf-8")) if os.path.exists(out) else {}
    db = sqlite3.connect(os.path.join(d, "brain.sqlite3"))
    kept = "NEWER_AUTHORITATIVE" in db.execute("SELECT payload_json FROM claims WHERE id = ?", (victim[1],)).fetchone()[0]
    db.close()
    after = digests(d, planned)
    return proc.returncode, res, kept, {k for k in planned if before[k] != after[k]}


def barrier_run(tool, d, decisions, named, planned, action):
    """The tool with RESEAL_TEST_BARRIER set; once it waits, action(db) runs on another connection and commits, then the
    tool is released. Returns (rc, result, planned digests moved)."""
    barrier = os.path.join(d, "barrier")
    out = os.path.join(d, "out.json")
    cmd = [sys.executable, "-X", "utf8", tool, os.path.join(d, "brain.sqlite3"), SRC, os.path.join(d, BAK), out,
           "--first-record", FIRST, "--max-gap-min", "60", "--decisions", decisions, "--decisions-sha256", named, "--apply"]
    before = digests(d, planned)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=dict(os.environ,
                            RESEAL_TEST_BARRIER=barrier))
    for _ in range(1200):
        if os.path.exists(barrier + ".waiting") or proc.poll() is not None:
            break
        time.sleep(0.1)
    db = sqlite3.connect(os.path.join(d, "brain.sqlite3"), isolation_level=None)
    action(db)
    db.close()
    open(barrier, "w").close()
    proc.communicate(timeout=600)
    res = json.load(open(out, encoding="utf-8")) if os.path.exists(out) else {}
    return proc.returncode, res, {k for k in planned if before[k] != digests(d, planned)[k]}


def trigger_sql(claim_id):
    """A trigger that rewrites one claim whenever any edge's digest is updated - the re-seal's own UPDATE fires it."""
    return ("CREATE TRIGGER reseal_side_effect AFTER UPDATE OF digest ON edges BEGIN "
            "UPDATE claims SET payload_json = json_set(payload_json, '$.touched_by_trigger', 1) WHERE id = '%s'; END"
            % claim_id.replace("'", "''"))


def touched(d, claim_id):
    db = sqlite3.connect(os.path.join(d, "brain.sqlite3"))
    p = db.execute("SELECT payload_json FROM claims WHERE id = ?", (claim_id,)).fetchone()[0]
    db.close()
    return "touched_by_trigger" in p


def passes(x):
    return (x.get("reason") == "source check passes" and x.get("source_faithful") == "yes"
            and x.get("mathematically_correct") == "yes" and not x.get("unsupported_addition"))


def git_blob(path):
    b = open(path, "rb").read()
    return hashlib.sha1(b"blob %d\0" % len(b) + b).hexdigest()


def header_mode(path):
    """The journal mode the file itself records: bytes 18 and 19 of the header are 2 in WAL mode, 1 otherwise."""
    return "wal" if open(path, "rb").read(20)[18] == 2 else "rollback"


def logical_sha(path):
    """The schema and every table's rows as SQLite reads them: what the store says, whatever its pages look like."""
    db = sqlite3.connect(path, timeout=30)
    h = hashlib.sha256()
    for name, sql in db.execute("SELECT name, sql FROM sqlite_master ORDER BY type, name").fetchall():
        h.update(("%s\n%s\n" % (name, sql)).encode("utf-8"))
    for (t,) in db.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name").fetchall():
        for r in db.execute('SELECT * FROM "%s"' % t):
            h.update(repr(r).encode("utf-8"))
    db.close()
    return h.hexdigest()


def sidecars(path):
    return {s: os.path.getsize(path + s) for s in ("-journal", "-wal") if os.path.exists(path + s)}


def wal_holder(d, sqls):
    """A connection that puts the store in WAL mode, checkpoints the base, commits sqls to the log only and stays open:
    the change is committed and sits in brain.sqlite3-wal, not in the main file (the auto-checkpoint is off, and the log
    is only folded back when the last connection closes)."""
    db = sqlite3.connect(os.path.join(d, "brain.sqlite3"), isolation_level=None)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA wal_autocheckpoint=0")
    db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    db.execute("BEGIN")
    for q in sqls:
        db.execute(q)
    db.execute("COMMIT")
    return db


def main_file_has(d, sql):
    """What a copy of the main file alone holds: the count sql returns on it."""
    raw = os.path.join(d, "raw_main_copy.sqlite3")
    shutil.copyfile(os.path.join(d, "brain.sqlite3"), raw)
    db = sqlite3.connect(raw)
    try:
        return db.execute(sql).fetchone()[0]
    finally:
        db.close()


def write_blocked(d):
    """True when another connection cannot begin a write transaction now."""
    db = sqlite3.connect(os.path.join(d, "brain.sqlite3"), timeout=0, isolation_level=None)
    try:
        db.execute("BEGIN IMMEDIATE")
        db.execute("ROLLBACK")
        return False
    except sqlite3.OperationalError as exc:
        return "locked" in str(exc) or "busy" in str(exc)
    finally:
        db.close()


def digests_now(d, keys):
    """The digests another connection reads now, or None when it cannot read at all."""
    try:
        db = sqlite3.connect(os.path.join(d, "brain.sqlite3"), timeout=0)
        try:
            return {k: (db.execute("SELECT digest FROM %s WHERE id = ?" % k[0], (k[1],)).fetchone() or [None])[0]
                    for k in keys}
        finally:
            db.close()
    except sqlite3.OperationalError:
        return None


def killed_run(d, decisions, named, planned, stop, pages=0):
    """The tool killed part way. stop="barrier": at its RESEAL_TEST_BARRIER, after the validation and before it opens the
    write connection; stop="before" / "after": under PAUSE, at COMMIT with every planned update made, the COMMIT not run /
    run. Returns what the tool's connection held when it stopped, what another connection could do then, what was on disk
    after the kill, and the store as the next connection finds it."""
    store = os.path.join(d, "brain.sqlite3")
    barrier = os.path.join(d, "stop")
    args = [store, SRC, os.path.join(d, BAK), os.path.join(d, "out.json"), "--first-record", FIRST, "--max-gap-min", "60",
            "--decisions", decisions, "--decisions-sha256", named, "--apply"]
    if stop == "barrier":
        cmd, env = [sys.executable, "-X", "utf8", TOOL] + args, dict(os.environ, RESEAL_TEST_BARRIER=barrier)
    else:
        cmd, env = [sys.executable, "-X", "utf8", PAUSE_TOOL, TOOL, barrier, str(pages), stop] + args, dict(os.environ)
    before, content0, bytes0, side0 = digests(d, planned), logical_sha(store), sha(store), sidecars(store)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
    for _ in range(1200):
        if os.path.exists(barrier + ".waiting") or proc.poll() is not None:
            break
        time.sleep(0.1)
    reached = os.path.exists(barrier + ".waiting") and proc.poll() is None
    held = json.load(open(barrier + ".waiting", encoding="utf-8")) if reached and stop != "barrier" else {}
    blocked = write_blocked(d) if reached else None
    seen = digests_now(d, planned) if reached else None
    proc.kill()
    tail = proc.communicate(timeout=60)[0].decode("utf-8", "replace").strip()[-300:]
    on_disk = {"file_changed": sha(store) != bytes0, "sidecars": sidecars(store)}
    db = sqlite3.connect(store, timeout=30)
    integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
    db.close()
    after = digests(d, planned)
    return {"reached": reached, "rc": proc.returncode, "held": held, "write_blocked": blocked,
            "seen": "locked out" if seen is None else "%d moved" % sum(seen[k] != before[k] for k in planned),
            "seen_moved": bool(seen) and any(seen[k] != before[k] for k in planned),
            "on_disk": on_disk, "sidecars_before": side0, "integrity": integrity,
            "moved": {k for k in planned if after[k] != before[k]}, "content_same": logical_sha(store) == content0,
            "bytes_same": sha(store) == bytes0, "tail": tail}


def main():
    os.makedirs(WORK, exist_ok=True)
    open(PAUSE_TOOL, "w", encoding="utf-8").write(PAUSE)
    live = sqlite3.connect("file:%s?mode=ro" % os.path.join(POP, "brain.sqlite3").replace("\\", "/"), uri=True)
    store_modes = {"journal_mode": live.execute("PRAGMA journal_mode").fetchone()[0],
                   "locking_mode": live.execute("PRAGMA locking_mode").fetchone()[0], "sqlite_version": sqlite3.sqlite_version}
    live.close()
    blob = git_blob(TOOL)
    print("tool %s: git blob %s, sha256 %s; live store: %s" % (TOOL, blob, sha(TOOL)[:16], store_modes))
    if AUDITED:
        check(blob == AUDITED, "P9 the tool's git blob %s is the audited one %s" % (blob[:12], AUDITED[:12]))
    dsha = sha(DEC)
    decs = json.load(open(DEC, encoding="utf-8"))["decisions"]
    by = {(x["table"], x["id"]): x for x in decs}
    expect = {k for k, x in by.items() if passes(x) and not (k[0] == "nodes" and ("claims", k[1]) in by
                                                              and not passes(by[("claims", k[1])]))}
    ends = edge_ends([k[1] for k in by if k[0] == "edges"])
    bad_end = {("edges", e) for e, st in ends.items()
               if any(not passes(by[(t, x)]) for x in st for t in ("claims", "nodes") if (t, x) in by)}
    good_end = {("edges", e) for e in ends if ("edges", e) not in bad_end}
    expect -= bad_end
    late = {k for k, x in by.items() if x.get("reason") != "source check passes"}
    print("decisions %d, pass %d, withheld by decision %d (late first record %d); decisions sha256 %s" % (
        len(by), len(expect), len(by) - len(expect), len(late), dsha[:16]))

    # P1 / A6: the clean copy
    d = arm("p1_clean")
    before = digests(d, list(by))
    rc, res, tail = run(TOOL, d, DEC, dsha)
    after = digests(d, list(by))
    changed = {(c["table"], c["id"]) for c in res.get("changed", [])}
    moved = {k for k in by if before[k] != after[k]}
    p1_moved = set(moved)
    planned_ok = expect - late
    check(rc == 0 and changed == planned_ok and moved == planned_ok,
          "P1 clean copy, bound decisions: rc %s, %d rows re-sealed, the %d the decisions pass (read back: %d digests moved)" % (
              rc, len(changed), len(planned_ok), len(moved)))
    sq = res.get("sqlite") or {}
    check(sq.get("version") == sqlite3.sqlite_version and sq.get("locking_mode") in ("normal", "exclusive")
          and (sq.get("journal_mode") == "wal") == (header_mode(os.path.join(d, "brain.sqlite3")) == "wal"),
          "A28 the output records the SQLite version and the store's journal and locking modes: %s" % sq)
    unpassed = set(by) - expect - late
    check(not (moved & unpassed) and len(res.get("decisions", {}).get("withheld", [])) == len(unpassed),
          "A6 the %d planned rows whose decision did not pass are withheld and left as they are (%d moved)" % (
              len(unpassed), len(moved & unpassed)))

    # A1 / A2: the wrong-at-creation fixtures
    for tag, victim in (("A1 concept", CONCEPT), ("A2 lemma", LEMMA)):
        d = arm(tag.split()[0].lower() + "_fixture")
        plant(d, victim)
        h0 = sha(os.path.join(d, "brain.sqlite3"))
        rc, res, tail = run(TOOL, d, DEC, dsha)
        unm = [(u["table"], u["id"]) for u in res.get("unmatched", [])]
        check(rc == 3 and ("claims", victim[0]) in unm and sha(os.path.join(d, "brain.sqlite3")) == h0,
              "%s fixture: the structural plan proposes the victim, the apply is refused (rc %s), store copy unchanged %s, "
              "unmatched %s" % (tag, rc, sha(os.path.join(d, "brain.sqlite3")) == h0, sorted(t for t, _ in unm)))
        if tag.startswith("A1") and OLD:
            d2 = arm("n1_old_tool")
            plant(d2, victim)
            v0 = claim_row(d2, victim[0])
            rc2, res2, tail2 = run(OLD, d2, first=True)
            v1 = claim_row(d2, victim[0])
            st1 = json.loads(v1[0]).get("status") if v1 else None
            check(rc2 == 0 and v1 != v0 and st1 == "unverified",
                  "N1 the tool before this change re-seals the wrong-at-creation victim on --apply (rc %s, status now %s): "
                  "the defect B07 found" % (rc2, st1))

    # A3: a planned row with no decision
    d = arm("a3_missing")
    drop = sorted(expect - late)[0]
    part = json.load(open(DEC, encoding="utf-8"))
    part["decisions"] = [x for x in part["decisions"] if (x["table"], x["id"]) != drop]
    p3 = os.path.join(d, "decisions_missing_one.json")
    json.dump(part, open(p3, "w", encoding="utf-8"))
    h0 = sha(os.path.join(d, "brain.sqlite3"))
    rc, res, tail = run(TOOL, d, p3, sha(p3))
    check(rc == 3 and sha(os.path.join(d, "brain.sqlite3")) == h0 and any(u["id"] == drop[1] for u in res.get("unmatched", [])),
          "A3 one planned row's decision removed: refused (rc %s), nothing written %s" % (rc, sha(os.path.join(d, "brain.sqlite3")) == h0))

    # A4: a decisions file that is not the one named
    d = arm("a4_stale_hash")
    h0 = sha(os.path.join(d, "brain.sqlite3"))
    rc, res, tail = run(TOOL, d, DEC, "0" * 64)
    check(rc == 3 and "not the" in (res.get("refused") or "") and sha(os.path.join(d, "brain.sqlite3")) == h0,
          "A4 the named sha256 is not the decisions file's: refused (rc %s)" % rc)

    # A5: an extra structural row
    d = arm("a5_extra_row")
    db = sqlite3.connect(os.path.join(d, "brain.sqlite3"))
    eid = db.execute("SELECT id FROM edges WHERE id NOT IN (%s) ORDER BY id LIMIT 1" % ",".join(
        "?" * len([k for k in by if k[0] == "edges"])), [k[1] for k in by if k[0] == "edges"]).fetchone()[0]
    db.close()
    for n in ("brain.sqlite3", BAK):
        db = sqlite3.connect(os.path.join(d, n))
        db.execute("UPDATE edges SET digest = 'sha256:' || substr('0000000000000000000000000000000000000000000000000000000000000000', 1, 64) WHERE id = ?", (eid,))
        db.commit()
        db.close()
    h0 = sha(os.path.join(d, "brain.sqlite3"))
    rc, res, tail = run(TOOL, d, DEC, dsha, first=False)
    check(rc == 3 and any(u["id"] == eid for u in res.get("unmatched", [])) and sha(os.path.join(d, "brain.sqlite3")) == h0,
          "A5 an edge outside the batch made to look stale in store and backup: refused (rc %s), nothing written %s" % (
              rc, sha(os.path.join(d, "brain.sqlite3")) == h0))

    # A7: --apply with no decisions
    d = arm("a7_no_decisions")
    h0 = sha(os.path.join(d, "brain.sqlite3"))
    rc, res, tail = run(TOOL, d)
    check(rc == 3 and sha(os.path.join(d, "brain.sqlite3")) == h0, "A7 --apply without decisions: refused (rc %s)" % rc)

    # A8 / N2: two decisions for one row
    x_key = sorted(unpassed)[0]
    fail_dec = by[x_key]
    pass_dec = dict(fail_dec, reason="source check passes", source_faithful="yes", mathematically_correct="yes",
                    unsupported_addition=False)
    for order, pair in (("failing then passing", [fail_dec, pass_dec]), ("passing then failing", [pass_dec, fail_dec])):
        d = arm("a8_dup_" + order.split()[0])
        dup = json.load(open(DEC, encoding="utf-8"))
        dup["decisions"] = [x for x in dup["decisions"] if (x["table"], x["id"]) != x_key] + pair
        p8 = os.path.join(d, "decisions_duplicate.json")
        json.dump(dup, open(p8, "w", encoding="utf-8"))
        h0 = sha(os.path.join(d, "brain.sqlite3"))
        rc, res, tail = run(TOOL, d, p8, sha(p8))
        check(rc == 3 and sha(os.path.join(d, "brain.sqlite3")) == h0 and
              {"table": x_key[0], "id": x_key[1]} in res.get("duplicates", []),
              "A8 %s %s given two decisions, %s: refused (rc %s), nothing written %s" % (
                  x_key[0], x_key[1][:18], order, rc, sha(os.path.join(d, "brain.sqlite3")) == h0))
        if order.startswith("failing"):
            rc_d, res_d, _t = run(TOOL, d, p8, sha(p8), apply=False)
            unm = [(u["table"], u["id"]) for u in (res_d.get("decisions") or {}).get("unmatched", [])]
            check(rc_d == 0 and x_key in unm and sha(os.path.join(d, "brain.sqlite3")) == h0,
                  "A8 dry run on the same file: the row is listed as unmatched (rc %s), the store untouched" % rc_d)
            if OLD_DUP:
                d2 = arm("n2_old_dup")
                p8b = os.path.join(d2, "decisions_duplicate.json")
                json.dump(dup, open(p8b, "w", encoding="utf-8"))
                before2 = digests(d2, [x_key])[x_key]
                rc2, res2, tail2 = run(OLD_DUP, d2, p8b, sha(p8b))
                after2 = digests(d2, [x_key])[x_key]
                check(rc2 == 0 and after2 != before2,
                      "N2 the tool before this change re-seals the row a later passing decision overrode (rc %s, digest "
                      "moved %s): the defect B07 found" % (rc2, after2 != before2))

    # A9-A12 / N3: a node-only plan (the node planned, its claim not)
    nid = sorted(k[1] for k in planned_ok if k[0] == "nodes" and ("claims", k[1]) in planned_ok)[0]
    node_key, claim_key = ("nodes", nid), ("claims", nid)

    def node_only_arm(name, claim_dec, tool=TOOL):
        d = arm(name)
        node_only(d, nid)
        dec = json.load(open(DEC, encoding="utf-8"))
        dec["decisions"] = [x for x in dec["decisions"] if (x["table"], x["id"]) != claim_key]
        if claim_dec is not None:
            dec["decisions"].append(claim_dec(d))
        p = os.path.join(d, "decisions_node_only.json")
        json.dump(dec, open(p, "w", encoding="utf-8"))
        h0 = sha(os.path.join(d, "brain.sqlite3"))
        before_n = digests(d, [node_key])[node_key]
        rc, res, tail = run(tool, d, p, sha(p))
        moved_n = digests(d, [node_key])[node_key] != before_n
        planned = {(c["table"], c["id"]) for c in res.get("changed", [])} | {
            (u["table"], u["id"]) for u in res.get("unmatched", []) + res.get("withheld", [])
            + (res.get("decisions") or {}).get("withheld", []) + (res.get("decisions") or {}).get("unmatched", [])}
        return rc, res, moved_n, sha(os.path.join(d, "brain.sqlite3")) == h0, planned

    now_pass = lambda d: dict(by[claim_key], row_sha256=row_hash(d, "claims", nid))
    now_fail = lambda d: dict(by[claim_key], row_sha256=row_hash(d, "claims", nid), mathematically_correct="partly")
    old_pass = lambda d: dict(by[claim_key])
    rc, res, moved_n, same, planned = node_only_arm("a9_node_only_no_claim", None)
    check(rc == 3 and same and not moved_n and any(u["id"] == nid and u["table"] == "nodes" for u in res.get("unmatched", []))
          and claim_key not in planned,
          "A9 node-only plan, no claim decision: refused (rc %s), nothing written %s, the node unmatched %s" % (
              rc, same, [u["why"] for u in res.get("unmatched", []) if u["id"] == nid]))
    rc, res, moved_n, same, planned = node_only_arm("a10_node_only_failing_claim", now_fail)
    wh = [w for w in (res.get("decisions") or {}).get("withheld", []) if w["id"] == nid]
    check(rc == 0 and not moved_n and wh and claim_key not in planned,
          "A10 node-only plan, a failing claim decision: the node withheld (rc %s, moved %s, %s)" % (
              rc, moved_n, [w["why"] for w in wh]))
    rc, res, moved_n, same, planned = node_only_arm("a11_node_only_passing_claim", now_pass)
    check(rc == 0 and moved_n and claim_key not in planned,
          "A11 node-only plan, a passing claim decision on the claim as it stands: the node re-sealed (rc %s, moved %s)" % (
              rc, moved_n))
    rc, res, moved_n, same, planned = node_only_arm("a12_node_only_stale_claim", old_pass)
    check(rc == 3 and same and not moved_n and any(u["id"] == nid and u["table"] == "nodes" for u in res.get("unmatched", [])),
          "A12 node-only plan, a passing claim decision made on the claim's earlier content: refused (rc %s), %s" % (
              rc, [u["why"] for u in res.get("unmatched", []) if u["id"] == nid]))
    if OLD_CLAIM:
        rc9, _r9, moved9, _s9, _p9 = node_only_arm("n3_old_no_claim", None, OLD_CLAIM)
        rc12, _r12, moved12, _s12, _p12 = node_only_arm("n3_old_stale_claim", old_pass, OLD_CLAIM)
        check(rc9 == 0 and moved9 and rc12 == 0 and moved12,
              "N3 the tool before this change re-seals the node with no claim decision (rc %s, moved %s) and with a claim "
              "decision made on other content (rc %s, moved %s): the defect B07 found" % (rc9, moved9, rc12, moved12))

    # A13: an edge whose endpoint does not pass
    check(bool(bad_end) and not (p1_moved & bad_end) and (good_end - late - bad_end) <= p1_moved,
          "A13 edges with an endpoint that does not pass: %d withheld on the clean copy (%d moved); edges whose endpoints "
          "pass re-sealed %d of %d" % (len(bad_end), len(p1_moved & bad_end), len((good_end - late) & p1_moved),
                                     len(good_end - late)))

    # A14 / N4: another process changes a planned row between the validation and the write
    d = arm("a14_race")
    rc, res, kept, moved_d = race_run(TOOL, d, DEC, dsha, sorted(planned_ok))
    check(rc == 3 and kept and not moved_d and (res.get("moved") or []),
          "A14 a planned claim changed by another process after the validation: refused (rc %s), the change kept %s, "
          "planned digests moved %d, moved %s" % (rc, kept, len(moved_d), [m["id"][:18] for m in res.get("moved") or []]))
    if OLD_RACE:
        d = arm("n4_old_race")
        src = open(OLD_RACE, encoding="utf-8").read()
        anchor = "    rw = sqlite3.connect(target)\n"
        assert src.count(anchor) == 1, "the old tool's write line was not found once"
        hook = ("    import time as _t\n    _b = os.environ.get('RESEAL_TEST_BARRIER')\n    if _b:\n"
                "        open(_b + '.waiting', 'w').close()\n        while not os.path.exists(_b):\n"
                "            _t.sleep(0.1)\n")
        old_b = os.path.join(d, "old_tool_with_barrier.py")
        open(old_b, "w", encoding="utf-8").write(src.replace(anchor, hook + anchor))
        rc4, res4, kept4, moved4 = race_run(old_b, d, DEC, dsha, sorted(planned_ok))
        check(rc4 == 0 and not kept4, "N4 the tool before this change, given the same barrier, overwrites the other "
              "process's change (rc %s, change kept %s): the defect B12 found" % (rc4, kept4))

    # A15 / N5 (account02/B07 r69, B07-R24-MATH-RESEAL-UNPLANNED-ENDPOINT-TOCTOU-031): an edge the tool re-seals points
    # at a claim no decision covers; another process changes that claim between the validation and the write.
    ext = sorted({(e, x) for (t, e) in planned_ok if t == "edges" for x in ends.get(e, ())
                  if ("claims", x) not in by and ("nodes", x) not in by})
    check(bool(ext), "A15 setup: %d re-sealed edge endpoint(s) outside the reviewed rows" % len(ext))
    if ext:
        edge_e, claim_x = ext[0]
        d = arm("a15_external_endpoint_race")
        rc, res, kept, moved_d = race_run(TOOL, d, DEC, dsha, sorted(planned_ok), victim=("claims", claim_x))
        dep_moved = [m for m in res.get("moved") or [] if m.get("dependency")]
        check(rc == 3 and kept and not moved_d and dep_moved,
              "A15 a claim outside the reviewed rows that a re-sealed edge points at, changed after the validation: "
              "refused (rc %s), the change kept %s, planned digests moved %d, dependency named %s" % (
                  rc, kept, len(moved_d), [m["id"][:18] for m in dep_moved]))
        if OLD_DEP:
            d = arm("n5_old_dep_race")
            rc5, res5, kept5, moved5 = race_run(OLD_DEP, d, DEC, dsha, sorted(planned_ok), victim=("claims", claim_x))
            check(rc5 == 0 and ("edges", edge_e) in moved5, "N5 the tool before this change re-seals the edge while its "
                  "endpoint changed under it (rc %s, edge re-sealed %s): the defect B07 found" % (
                      rc5, ("edges", edge_e) in moved5))

    # A16 / N6: a passing endpoint decision bound to other content than the endpoint's row now
    cand = sorted((e, x, t) for (tt, e) in planned_ok if tt == "edges" for x in ends.get(e, ())
                  for t in ("claims", "nodes") if (t, x) in by and passes(by[(t, x)]))
    check(bool(cand), "A16 setup: %d passing endpoint decision(s) of re-sealed edges" % len(cand))
    if cand:
        edge_e, end_x, end_t = cand[0]
        stale = json.load(open(DEC, encoding="utf-8"))
        for x in stale["decisions"]:
            if x["table"] == end_t and x["id"] == end_x:
                x["row_sha256"] = "0" * 64
        stale_path = os.path.join(WORK, "decisions_stale_endpoint.json")
        json.dump(stale, open(stale_path, "w", encoding="utf-8"), indent=1)
        d = arm("a16_stale_endpoint_decision")
        rc, res, _t = run(TOOL, d, stale_path, sha(stale_path), apply=False)
        wh = {(w["table"], w["id"]): w["why"] for w in (res.get("decisions") or {}).get("withheld", [])}
        why = wh.get(("edges", edge_e), "")
        check("made on other content" in why, "A16 a passing endpoint decision made on other content withholds the edge: "
              "%s" % (why or "not withheld (rc %s)" % rc))
        if OLD_DEP:
            d = arm("n6_old_stale_endpoint")
            rc6, res6, _t6 = run(OLD_DEP, d, stale_path, sha(stale_path), apply=False)
            planned6 = {(c["table"], c["id"]) for c in res6.get("changed", [])}
            check(("edges", edge_e) in planned6, "N6 the tool before this change plans the edge on the stale endpoint "
                  "decision (planned %s): the defect B07 found" % (("edges", edge_e) in planned6))

    # A17-A21 / N7-N8 (account02/B13 r71, account01/B07 r71): triggers, the schema, and what the write itself changes
    if ext:
        edge_e, claim_x = ext[0]

        def install(db):
            db.execute(trigger_sql(claim_x))

        d = arm("a17_trigger_at_barrier")
        rc, res, moved_d = barrier_run(TOOL, d, DEC, dsha, sorted(planned_ok), install)
        schema_moved = [m for m in res.get("moved") or [] if m.get("schema")]
        check(rc == 3 and schema_moved and not touched(d, claim_x) and not moved_d,
              "A17 a trigger installed after the validation: refused (rc %s) on the schema %s, the endpoint rewritten %s, "
              "planned digests moved %d" % (rc, bool(schema_moved), touched(d, claim_x), len(moved_d)))
        if OLD_TRIG:
            d = arm("n7_old_trigger_at_barrier")
            rc7, _res7, moved7 = barrier_run(OLD_TRIG, d, DEC, dsha, sorted(planned_ok), install)
            check(rc7 == 0 and touched(d, claim_x) and moved7,
                  "N7 the tool before this change commits under the same trigger (rc %s) and the endpoint is rewritten "
                  "%s: the defect B13 found" % (rc7, touched(d, claim_x)))

        def with_trigger(name):
            dd = arm(name)
            db = sqlite3.connect(os.path.join(dd, "brain.sqlite3"))
            db.execute(trigger_sql(claim_x))
            db.commit()
            db.close()
            return dd

        d = with_trigger("a18_trigger_present")
        h0 = sha(os.path.join(d, "brain.sqlite3"))
        rc, res, _t = run(TOOL, d, DEC, dsha)
        check(rc == 3 and res.get("triggers") and sha(os.path.join(d, "brain.sqlite3")) == h0,
              "A18 a trigger already in the store: refused before planning (rc %s, triggers named %s), store copy "
              "byte-identical %s" % (rc, [t["name"] for t in res.get("triggers") or []],
                                     sha(os.path.join(d, "brain.sqlite3")) == h0))
        if OLD_TRIG:
            d = with_trigger("n8_old_trigger_present")
            rc8, _res8, _t8 = run(OLD_TRIG, d, DEC, dsha)
            check(rc8 == 0 and touched(d, claim_x),
                  "N8 the tool before this change commits with the trigger present (rc %s) and the endpoint is rewritten "
                  "%s: the defect B07 found" % (rc8, touched(d, claim_x)))

        # A21: the after-write checks alone, on a copy with the trigger refusal and the schema check switched off
        src = open(TOOL, encoding="utf-8").read()
        anchor = "\ndef passed(d):\n"
        assert src.count(anchor) == 1, "the tool's passed() was not found once"
        blind = os.path.join(WORK, "tool_without_schema_checks.py")
        open(blind, "w", encoding="utf-8").write(src.replace(
            anchor, "\ntriggers = lambda conn: []\nschema_sha256 = lambda conn: 'same'\n" + anchor))
        d = with_trigger("a21_after_write_checks_alone")
        h0 = sha(os.path.join(d, "brain.sqlite3"))
        rc, res, _t = run(blind, d, DEC, dsha)
        after_w = [m for m in res.get("moved") or [] if m.get("side_effects") or m.get("after_write")]
        check(rc == 3 and after_w and not touched(d, claim_x) and sha(os.path.join(d, "brain.sqlite3")) == h0,
              "A21 with the up-front checks off, the after-write checks refuse the trigger's side effect (rc %s, caught by "
              "%s), rolled back: endpoint rewritten %s, store copy byte-identical %s" % (
                  rc, [sorted(k for k in m if m[k] is not None) for m in after_w], touched(d, claim_x),
                  sha(os.path.join(d, "brain.sqlite3")) == h0))

    def checkpoint(db):
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    d = arm("a20_wal_checkpoint")
    db = sqlite3.connect(os.path.join(d, "brain.sqlite3"))
    mode = db.execute("PRAGMA journal_mode=WAL").fetchone()[0]
    db.close()
    rc, res, moved_d = barrier_run(TOOL, d, DEC, dsha, sorted(planned_ok), checkpoint)
    check(rc == 0 and moved_d == p1_moved,
          "A20 a WAL checkpoint at the barrier (journal %s) changes nothing: committed (rc %s), %d re-sealed as in P1 (%d)"
          % (mode, rc, len(moved_d), len(p1_moved)))

    # A22-A24, A27 / N10 (29 Sep, account04/B13 r58, B13-R58-NOOP-UNRELATED-TRIGGER-REFUSAL-20260929): a dry run on a
    # store with a trigger reports the census and the plan and runs nothing; --apply on such a store stays refused
    d = empty_arm("a22_noop_unrelated_trigger", UNRELATED)
    h0 = sha(os.path.join(d, "brain.sqlite3"))
    rc, res, tail = run(TOOL, d, apply=False)
    zero = res.get("before") == {"node_mismatch": 0, "edge_mismatch": 0, "claims_get_claim_would_fail": 0}
    check(rc == 0 and zero and res.get("changed") == [] and res.get("nothing_to_apply") is True
          and res.get("triggers_present") is True and res.get("canonical_schema") is False
          and [t["name"] for t in res.get("triggers") or []] == ["audit_insert_only"]
          and sha(os.path.join(d, "brain.sqlite3")) == h0,
          "A22 nothing to repair and a trigger no planned UPDATE can fire (AFTER INSERT ON audit_log), dry run: rc %s, zero "
          "census %s, changed %s, triggers %s, store byte-identical %s" % (
              rc, zero, res.get("changed"), [t["name"] for t in res.get("triggers") or []],
              sha(os.path.join(d, "brain.sqlite3")) == h0))
    if OLD_NOOP:
        d2 = empty_arm("n10_old_noop", UNRELATED)
        rc2, res2, _t2 = run(OLD_NOOP, d2, apply=False)
        check(rc2 == 3 and bool(res2.get("triggers")) and "before" not in res2,
              "N10 the tool before this change refuses the same dry run (rc %s) and gives no census: the defect B13 found"
              % rc2)
    empty_dec = os.path.join(WORK, "decisions_empty.json")
    json.dump({"decisions": []}, open(empty_dec, "w", encoding="utf-8"))
    d = empty_arm("a23_noop_claims_trigger", ON_CLAIMS)
    h0 = sha(os.path.join(d, "brain.sqlite3"))
    rc, res, _t = run(TOOL, d, apply=False)
    rc_a, res_a, _ta = run(TOOL, d, empty_dec, sha(empty_dec))
    check(rc == 0 and res.get("nothing_to_apply") is True and res.get("apply_blocked_by_triggers") is True
          and rc_a == 3 and bool(res_a.get("triggers")) and sha(os.path.join(d, "brain.sqlite3")) == h0,
          "A23 nothing to repair and an AFTER UPDATE ON claims trigger: the dry run reports (rc %s, apply blocked %s); "
          "--apply refused before planning (rc %s, triggers named %s), store byte-identical %s" % (
              rc, res.get("apply_blocked_by_triggers"), rc_a, bool(res_a.get("triggers")),
              sha(os.path.join(d, "brain.sqlite3")) == h0))
    d = arm("a24_plan_unrelated_trigger")
    add_sql(d, UNRELATED)
    h0 = sha(os.path.join(d, "brain.sqlite3"))
    rc, res, _t = run(TOOL, d, apply=False)
    copy = os.path.join(d, "reseal_dryrun.sqlite3")
    n_plan = len(res.get("changed") or [])
    # the copy is a SQLite backup since R58, so its bytes are not the store's; what it holds is (29 Sep)
    untouched = not os.path.exists(copy) or logical_sha(copy) == logical_sha(os.path.join(d, "brain.sqlite3"))
    rc_a, res_a, _ta = run(TOOL, d, DEC, dsha)
    check(rc == 0 and n_plan > 0 and res.get("apply_blocked_by_triggers") is True and res.get("after") is None
          and untouched and rc_a == 3 and bool(res_a.get("triggers")) and sha(os.path.join(d, "brain.sqlite3")) == h0,
          "A24 the store's plan with the unrelated trigger: the dry run lists %d planned rows (rc %s), runs nothing on its "
          "copy (%s) and marks the apply blocked %s; --apply refused before planning (rc %s), store byte-identical %s" % (
              n_plan, rc, untouched, res.get("apply_blocked_by_triggers"), rc_a,
              sha(os.path.join(d, "brain.sqlite3")) == h0))

    # W1-W8 (29 Sep, account04/B13 r4, B13-R58-WAL-SIDECAR-OMITTED-BY-DRYRUN-COPY-20260929-01): the dry run reads the
    # store's committed state, write-ahead log included
    trig_sql = "SELECT count(*) FROM sqlite_master WHERE type = 'trigger'"
    d = empty_arm("w1_wal_trigger")
    hold = wal_holder(d, UNRELATED)
    main_db = os.path.join(d, "brain.sqlite3")
    in_main = main_file_has(d, trig_sql)
    h_main, h_wal = sha(main_db), sha(main_db + "-wal")
    rc, res, _t = run(TOOL, d, apply=False)
    names = [x["name"] for x in res.get("triggers") or []]
    same_files = sha(main_db) == h_main and sha(main_db + "-wal") == h_wal
    rc_a, res_a, _ta = run(TOOL, d, empty_dec, sha(empty_dec))
    modes_out = res.get("sqlite") or {}
    check(in_main == 0 and os.path.getsize(main_db + "-wal") > 0 and rc == 0 and names == ["audit_insert_only"]
          and res.get("apply_blocked_by_triggers") is True,
          "W1 a trigger committed to the log only (the main file holds %d triggers, the log %d bytes): the dry run names "
          "%s and marks the apply blocked %s" % (in_main, os.path.getsize(main_db + "-wal"), names,
                                                   res.get("apply_blocked_by_triggers")))
    check(rc_a == 3 and bool(res_a.get("triggers")) and res.get("apply_blocked_by_triggers") is True,
          "W4 the same generation: the dry run says blocked and --apply refuses (rc %s)" % rc_a)
    check(same_files, "W7 the dry run leaves the store's main file and its log byte-identical (%s)" % same_files)
    check(modes_out.get("journal_mode") == "wal" and modes_out.get("version") == sqlite3.sqlite_version
          and bool(modes_out.get("locking_mode")),
          "W8 the output records the store's modes, not the copy's: %s; tool blob %s" % (modes_out, git_blob(TOOL)[:12]))
    if OLD_COPY:
        rc2, res2, _t2 = run(OLD_COPY, d, apply=False)
        check(not res2.get("apply_blocked_by_triggers") and not res2.get("triggers"),
              "W2 the tool that copied the main file alone misses the trigger (rc %s, triggers %s): the defect B13 found"
              % (rc2, [x["name"] for x in res2.get("triggers") or []]))

    # W6: checkpoints running beside the dry run, then a full one: each run sees one whole generation
    stop = threading.Event()

    def checkpoints():
        c = sqlite3.connect(main_db, isolation_level=None, timeout=5)
        while not stop.is_set():
            try:
                c.execute("PRAGMA wal_checkpoint(PASSIVE)")
            except sqlite3.OperationalError:
                pass
            time.sleep(0.01)
        c.close()
    th = threading.Thread(target=checkpoints)
    th.start()
    during = [run(TOOL, d, apply=False)[1] for _ in range(3)]
    stop.set()
    th.join()
    hold.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    after = run(TOOL, d, apply=False)[1]
    seen = [[x["name"] for x in r.get("triggers") or []] for r in during + [after]]
    check(all(s == ["audit_insert_only"] for s in seen) and main_file_has(d, trig_sql) == 1,
          "W6 checkpoints during the dry runs and a full one after: every run names the trigger (%s)" % seen)
    hold.close()

    # W3: a row change committed to the log only is counted as a read after the checkpoint counts it
    d = arm("w3_wal_row")
    base = run(TOOL, d, apply=False)[1]
    c = sqlite3.connect(os.path.join(d, "brain.sqlite3"))
    nid = c.execute("SELECT id FROM nodes ORDER BY id LIMIT 1").fetchone()[0]
    c.close()               # an open handle keeps the fixture folder from being removed on Windows
    hold = wal_holder(d, ["UPDATE nodes SET body = body || ' (edited)' WHERE id = '%s'" % nid.replace("'", "''")])
    edited_in_main = main_file_has(d, "SELECT count(*) FROM nodes WHERE body LIKE '%(edited)'")
    pending = run(TOOL, d, apply=False)[1]
    old_pending = run(OLD_COPY, d, apply=False)[1] if OLD_COPY else None
    hold.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    folded = run(TOOL, d, apply=False)[1]
    hold.close()
    b0, b1, b2 = (x.get("before") or {} for x in (base, pending, folded))
    changed = lambda r: sorted((c["table"], c["id"]) for c in r.get("changed") or [])
    check(edited_in_main == 0 and b1.get("node_mismatch") == b0.get("node_mismatch", -9) + 1 and b1 == b2
          and changed(pending) == changed(folded),
          "W3 a node edited in the log only: the dry run's census %s equals the census after the checkpoint %s (the "
          "baseline %s), and the plans agree (%d rows)" % (b1, b2, b0, len(changed(pending))))
    if old_pending is not None:
        check((old_pending.get("before") or {}) == b0,
              "W2b the tool that copied the main file alone counts the baseline census %s, missing the edit"
              % old_pending.get("before"))

    # W5: a rollback-journal store, the trigger committed to the main file
    d = empty_arm("w5_rollback_trigger", UNRELATED)
    rc, res, _t = run(TOOL, d, apply=False)
    check(rc == 0 and [x["name"] for x in res.get("triggers") or []] == ["audit_insert_only"]
          and (res.get("sqlite") or {}).get("journal_mode") in ("delete", "truncate", "persist")
          and res.get("apply_blocked_by_triggers") is True,
          "W5 a rollback-journal store: the dry run names the trigger (journal %s)" % (res.get("sqlite") or {}).get(
              "journal_mode"))

    def install_unrelated(db):
        for q in UNRELATED:
            db.execute(q)

    d = empty_arm("a27_noop_schema_at_barrier")
    rc, res, _moved = barrier_run(TOOL, d, empty_dec, sha(empty_dec), [], install_unrelated)
    check(rc == 3 and any(m.get("schema") for m in res.get("moved") or []),
          "A27 nothing to repair, --apply, a trigger installed at the barrier: refused (rc %s) on the schema %s" % (
              rc, any(m.get("schema") for m in res.get("moved") or [])))

    # P6 / P7 / N9 (29 Sep, account04/B13 r53, T-R53-INSTALLED-VALIDATE-WRITE-INTERLOCK): the process killed before the
    # write transaction, inside it, and just after it
    kills = {}
    d = arm("p6_kill_at_barrier")
    jm = header_mode(os.path.join(d, "brain.sqlite3"))
    k = kills["P6"] = killed_run(d, DEC, dsha, sorted(planned_ok), "barrier")
    check(k["reached"] and k["write_blocked"] is False and not k["moved"] and k["content_same"] and k["bytes_same"]
          and not k["on_disk"]["file_changed"] and k["integrity"] == "ok" and "-journal" not in k["on_disk"]["sidecars"]
          and not k["on_disk"]["sidecars"].get("-wal"),
          "P6 (%s copy) killed at the barrier, after the validation and before the write connection opens: another "
          "connection could begin a write %s; nothing written - file byte-identical %s, every table as before %s, planned "
          "digests moved %d, integrity %s, journal files after the kill %s" % (
              jm, k["write_blocked"] is False, k["bytes_same"], k["content_same"], len(k["moved"]), k["integrity"],
              k["on_disk"]["sidecars"]))
    for jmode, pages in (("wal", 0), ("wal", 10), ("delete", 0), ("delete", 10)):
        d = arm("p7_kill_in_txn_%s_%d" % (jmode, pages))
        db = sqlite3.connect(os.path.join(d, "brain.sqlite3"))
        db.execute("PRAGMA journal_mode = %s" % jmode)
        db.close()
        k = kills["P7 %s %d" % (jmode, pages)] = killed_run(d, DEC, dsha, sorted(planned_ok), "before", pages)
        h = k["held"]
        on_disk = k["on_disk"]["sidecars"].get("-wal", 0) > 0 if jmode == "wal" else k["on_disk"]["file_changed"]
        check(k["reached"] and h.get("in_transaction") is True and h.get("total_changes") == len(planned_ok)
              and h.get("journal_mode") == jmode and k["write_blocked"] and not k["seen_moved"]
              and (on_disk or not pages) and k["integrity"] == "ok" and not k["moved"] and k["content_same"],
              "P7 %s journal, %s cache: killed inside the write transaction with %s of %d planned updates made and COMMIT "
              "not run (another writer blocked %s, a reader: %s); uncommitted pages on disk at the kill %s (%s, database "
              "file changed %s); the next connection finds integrity %s, planned digests moved %d, every table as before "
              "%s, file byte-identical %s" % (
                  jmode, "%d-page" % pages if pages else "default", h.get("total_changes"), len(planned_ok),
                  k["write_blocked"], k["seen"], on_disk, k["on_disk"]["sidecars"], k["on_disk"]["file_changed"],
                  k["integrity"], len(k["moved"]), k["content_same"], k["bytes_same"]))
    d = arm("n9_kill_after_commit")
    k = kills["N9"] = killed_run(d, DEC, dsha, sorted(planned_ok), "after")
    check(k["reached"] and k["moved"] == planned_ok and not k["content_same"] and k["integrity"] == "ok",
          "N9 the same kill just after COMMIT: %d of %d planned rows re-sealed, every table as before %s - the P7 checks "
          "can fail" % (len(k["moved"]), len(planned_ok), k["content_same"]))

    print("FAIL" if fails else "PASS", "- %d controls, %d failed" % (len(ran), len(fails)))
    json.dump({"controls": ran, "failed": fails, "decisions_sha256": dsha, "tool_sha256": sha(TOOL), "tool_git_blob": blob,
               "audited_blob": AUDITED, "live_store": store_modes,
               "tool_connection_at_commit": {n: k["held"] for n, k in kills.items() if k["held"]},
               "kills": {n: {"rc": k["rc"], "on_disk": k["on_disk"], "sidecars_before": k["sidecars_before"],
                             "integrity": k["integrity"], "bytes_same": k["bytes_same"], "content_same": k["content_same"],
                             "moved": len(k["moved"])} for n, k in kills.items()},
               "old_tool_sha256": sha(OLD) if OLD else None},
              open(os.path.join(WORK, "result.json"), "w", encoding="utf-8"), indent=1)
    if "--keep" not in sys.argv:
        for n in os.listdir(WORK):
            p = os.path.join(WORK, n)
            if os.path.isdir(p):
                shutil.rmtree(p)
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
