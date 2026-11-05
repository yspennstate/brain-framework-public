"""Controls for the math hook's review reads and claim rows (read for the shown cards only, each row judged by its digest).

 R1 a sound human read of a shown card comes back as when / passed / human, and only those three keys
 R2 a payload edited without its digest, a "passed" that is the string "false", and a payload that is a JSON list are
    skipped - none raises
 R3 a card that is not asked for is not read
 C1 _claim_parts returns a sound claim row's statement and assumptions and skips a row whose payload no longer matches
 C2 _claim_view leaves a node with no claim row unchanged

    python test_review_reads.py      (under a second; ALL PASS at the end)
"""
import dataclasses
import importlib
import json
import os
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
results = []


def check(ok, what):
    results.append(ok)
    print(("PASS  " if ok else "FAIL  ") + what)


def fresh(env):
    for k in [k for k in os.environ if k.startswith("MATHBRAIN_")]:
        del os.environ[k]
    os.environ.update(env)
    sys.modules.pop("math_sense", None)
    return importlib.import_module("math_sense")


def row(obj):
    return json.dumps(dataclasses.asdict(obj)), obj.digest()


def main():
    tmp = tempfile.mkdtemp()
    store = os.path.join(tmp, "brain.sqlite3")
    m = fresh({"MATHBRAIN_STORE": store})
    if os.path.isdir(m.BUILD_SRC) and m.BUILD_SRC not in sys.path:
        sys.path.insert(0, m.BUILD_SRC)  # the vendored package, as the hook itself loads it
    from math_brain.models import Claim, ClaimKind, Evidence, EvidenceKind
    con = sqlite3.connect(store)
    con.executescript("""
        create table claims (id text primary key, payload_json text, digest text, created_at text);
        create table evidence (id text primary key, claim_id text, payload_json text, digest text, created_at text);
    """)
    c1 = Claim(statement="Every bounded coercive bilinear form has a Lax-Milgram solution.", kind=ClaimKind("theorem"),
               assumptions=("H is a real Hilbert space", "B is bounded"))
    c2 = Claim(statement="A second claim.", kind=ClaimKind("theorem"))
    c3 = Claim(statement="A third claim.", kind=ClaimKind("theorem"))
    for c in (c1, c2, c3):
        p, d = row(c)
        if c is c2:
            p = p.replace("A second claim.", "A changed claim.")
        con.execute("insert into claims values (?, ?, ?, ?)", (c.id, p, d, c.created_at))

    def ev(cid, kind, passed):
        return Evidence(claim_id=cid, kind=EvidenceKind(kind), summary="read", scope="whole proof", verifier="r",
                        passed=passed)
    good = ev(c1.id, "human_proof_review", True)
    edited = ev(c2.id, "discovery_report", False)
    strfalse = ev(c3.id, "discovery_report", False)
    for e in (good, edited, strfalse):
        p, d = row(e)
        if e is edited:
            p = p.replace('"passed": false', '"passed": true')
        if e is strfalse:
            p = p.replace('"passed": false', '"passed": "false"')
        con.execute("insert into evidence values (?, ?, ?, ?, ?)", (e.id, e.claim_id, p, d, e.created_at))
    con.execute("insert into evidence values ('evidence_list', ?, '[1, 2]', 'x', '2026-01-01')", (c2.id,))
    con.commit()
    con.close()

    r = m._review_reads([c1.id, c2.id, c3.id])
    check(set(r) == {c1.id} and r[c1.id]["passed"] is True and r[c1.id]["human"] is True
          and set(r[c1.id]) == {"when", "passed", "human"}, "R1 sound human read: %r" % r.get(c1.id))
    check(c2.id not in r and c3.id not in r, "R2 edited, string-false and list payloads skipped")
    check(m._review_reads([c2.id]) == {} and m._review_reads([]) == {}, "R3 only the asked cards are read")
    parts = m._claim_parts([c1.id, c2.id])
    check(parts.get(c1.id) == (c1.statement, c1.assumptions) and c2.id not in parts,
          "C1 claim rows: %r" % {k[:12]: v for k, v in parts.items()})

    class Node:
        id, title, body, metadata = "n", "t", "b", {}
    node = Node()
    check(m._claim_view(node, None) is node, "C2 no claim row: node unchanged")
    print("ALL PASS" if all(results) else "SOME FAILED")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
