"""Census for B07-R13-MATH-GRAPH-ROW-DIGEST-BYPASS-019: rebuild every node and edge of a math-brain store the way
ResearchStore.get_nodes / _edge_from_row do, recompute GraphNode.digest() / GraphEdge.digest(), and count rows whose
stored digest column differs. Read-only on the database named (open it on a copy).
    python graph_digest_census.py STORE.sqlite3 SRC_DIR OUT.json
"""
import collections
import json
import sqlite3
import sys

store, src, out = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, src)
from math_brain.models import EdgeKind, GraphEdge, GraphNode, NodeKind  # noqa: E402

con = sqlite3.connect("file:%s?mode=ro" % store.replace("\\", "/"), uri=True)
con.row_factory = sqlite3.Row
res = {"store": store, "nodes": 0, "node_mismatch": 0, "node_unbuildable": 0, "edges": 0, "edge_mismatch": 0,
       "edge_unknown_kind": 0, "edge_unbuildable": 0, "examples": [], "mismatch_by_kind": collections.Counter()}
for row in con.execute("SELECT * FROM nodes"):
    res["nodes"] += 1
    try:
        n = GraphNode(id=row["id"], kind=NodeKind(row["kind"]), title=row["title"], body=row["body"],
                      metadata=json.loads(row["metadata_json"]), created_at=row["created_at"])
    except Exception as exc:
        res["node_unbuildable"] += 1
        if len(res["examples"]) < 12:
            res["examples"].append({"table": "nodes", "id": row["id"], "error": type(exc).__name__})
        continue
    if n.digest() != row["digest"]:
        res["node_mismatch"] += 1
        res["mismatch_by_kind"]["node:" + row["kind"]] += 1
        if len(res["examples"]) < 12:
            res["examples"].append({"table": "nodes", "id": row["id"], "kind": row["kind"]})
for row in con.execute("SELECT * FROM edges"):
    res["edges"] += 1
    try:
        kind = EdgeKind(row["kind"])
    except ValueError:
        res["edge_unknown_kind"] += 1
        continue
    try:
        e = GraphEdge(id=row["id"], source_id=row["source_id"], target_id=row["target_id"], kind=kind,
                      rationale=row["rationale"], weight=row["weight"], metadata=json.loads(row["metadata_json"]),
                      created_at=row["created_at"])
    except Exception as exc:
        res["edge_unbuildable"] += 1
        if len(res["examples"]) < 12:
            res["examples"].append({"table": "edges", "id": row["id"], "error": type(exc).__name__})
        continue
    if e.digest() != row["digest"]:
        res["edge_mismatch"] += 1
        res["mismatch_by_kind"]["edge:" + row["kind"]] += 1
        if len(res["examples"]) < 12:
            res["examples"].append({"table": "edges", "id": row["id"], "kind": row["kind"]})
res["mismatch_by_kind"] = dict(res["mismatch_by_kind"])
json.dump(res, open(out, "w", encoding="utf-8"), indent=1)
print(json.dumps({k: v for k, v in res.items() if k != "examples"}, indent=1))
print("examples:", res["examples"][:6])
