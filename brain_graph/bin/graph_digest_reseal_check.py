"""Evidence for re-sealing the graph rows whose stored digest does not match their payload (B07-019, 28 Sep).
For every mismatching node: does its claim row verify the way ResearchStore.get_claim verifies it, and do the node's
title and body agree with that claim's statement fields? For every mismatching row: is it byte-identical in each older
copy given (earliest first), and do an edge's endpoints exist and verify? Read-only.
    python graph_digest_reseal_check.py LIVE SRC OLDER [OLDER ...]
"""
import collections
import json
import sqlite3
import sys
import types

live, src, olders = sys.argv[1], sys.argv[2], sys.argv[3:]
sys.path.insert(0, src)
from math_brain.models import (Claim, ClaimKind, EdgeKind, GraphEdge, GraphNode, NodeKind, RecordStatus,  # noqa: E402
                               content_hash)


def ro(p):
    c = sqlite3.connect("file:%s?mode=ro" % p.replace("\\", "/"), uri=True)
    c.row_factory = sqlite3.Row
    return c


def node(r):
    return GraphNode(id=r["id"], kind=NodeKind(r["kind"]), title=r["title"], body=r["body"],
                     metadata=json.loads(r["metadata_json"]), created_at=r["created_at"])


def edge(r):
    return GraphEdge(id=r["id"], source_id=r["source_id"], target_id=r["target_id"], kind=EdgeKind(r["kind"]),
                     rationale=r["rationale"], weight=r["weight"], metadata=json.loads(r["metadata_json"]),
                     created_at=r["created_at"])


def claim_ok(c, cid):
    row = c.execute("SELECT id, digest, created_at, payload_json FROM claims WHERE id = ?", (cid,)).fetchone()
    if row is None:
        return None, "no claims row"
    p = json.loads(row["payload_json"])
    if p.get("status") not in {s.value for s in RecordStatus}:
        # the typed reader refuses this status; judge the stored digest over the raw payload instead
        raw_ok = content_hash(p) == row["digest"] and p.get("id") == row["id"] and p.get("created_at") == row["created_at"]
        return types.SimpleNamespace(statement=p.get("statement")), \
            ("status %s not a RecordStatus; digest %s over the raw payload" % (p.get("status"), "verifies" if raw_ok else "does not verify"))
    p["kind"] = ClaimKind(p["kind"])
    p["status"] = RecordStatus(p["status"])
    p["assumptions"] = tuple(p["assumptions"])
    cl = Claim(**p)
    ok = cl.id == row["id"] and cl.created_at == row["created_at"] and cl.digest() == row["digest"]
    return cl, ("verifies" if ok else "claim digest mismatch")


a = ro(live)
olds = [ro(p) for p in olders]
out = collections.Counter()
rows = []
for r in a.execute("SELECT * FROM nodes"):
    if node(r).digest() == r["digest"]:
        continue
    cl, st = claim_ok(a, r["id"]) if r["kind"] == "claim" else (None, "not a claim node")
    agree = "-"
    if cl is not None:
        fields = {k: str(getattr(cl, k, "") or "") for k in ("statement", "title", "text", "summary")}
        body, title = r["body"] or "", r["title"] or ""
        agree = "body==statement" if fields.get("statement") and body.strip() == fields["statement"].strip() else (
            "body contains statement" if fields.get("statement") and fields["statement"].strip() in body else "differs")
    same = [bool(o.execute("SELECT 1 FROM nodes WHERE id=? AND title IS ? AND body IS ? AND metadata_json IS ? AND "
                           "digest IS ? AND created_at IS ?", (r["id"], r["title"], r["body"], r["metadata_json"],
                                                               r["digest"], r["created_at"])).fetchone()) for o in olds]
    out[("node", st, agree, tuple(same))] += 1
    rows.append({"table": "nodes", "id": r["id"], "claim": st, "text": agree, "same_in_older": same})
for r in a.execute("SELECT * FROM edges"):
    if edge(r).digest() == r["digest"]:
        continue
    ends = []
    for nid in (r["source_id"], r["target_id"]):
        n = a.execute("SELECT * FROM nodes WHERE id = ?", (nid,)).fetchone()
        ends.append("absent" if n is None else ("ok" if node(n).digest() == n["digest"] else "mismatch"))
    same = [bool(o.execute("SELECT 1 FROM edges WHERE id=? AND source_id IS ? AND target_id IS ? AND kind IS ? AND "
                           "rationale IS ? AND weight IS ? AND metadata_json IS ? AND digest IS ?",
                           (r["id"], r["source_id"], r["target_id"], r["kind"], r["rationale"], r["weight"],
                            r["metadata_json"], r["digest"])).fetchone()) for o in olds]
    out[("edge", "endpoints " + "/".join(ends), tuple(same))] += 1
    rows.append({"table": "edges", "id": r["id"], "endpoints": ends, "same_in_older": same})
for k, v in out.most_common():
    print(v, k)
json.dump(rows, open("census/reseal_evidence.json", "w", encoding="utf-8"), indent=1)
