"""For each graph row whose stored digest differs from its payload's digest in STORE, look the same id up in OLDER
(an earlier copy of the store) and say which fields differ and whether the older payload hashes to the stored digest.
    python graph_digest_why.py STORE OLDER SRC_DIR
"""
import collections
import json
import sqlite3
import sys

store, older, src = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, src)
from math_brain.models import EdgeKind, GraphEdge, GraphNode, NodeKind  # noqa: E402


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


a, b = ro(store), ro(older)
summary = collections.Counter()
for table, build, cols in (("nodes", node, ("kind", "title", "body", "metadata_json", "created_at")),
                           ("edges", edge, ("source_id", "target_id", "kind", "rationale", "weight", "metadata_json",
                                            "created_at"))):
    for r in a.execute("SELECT * FROM %s" % table):
        if build(r).digest() == r["digest"]:
            continue
        o = b.execute("SELECT * FROM %s WHERE id = ?" % table, (r["id"],)).fetchone()
        if o is None:
            summary[table + ": absent from the older copy"] += 1
            continue
        changed = [c for c in cols if r[c] != o[c]]
        old_ok = build(o).digest() == r["digest"]
        same_digest_col = o["digest"] == r["digest"]
        key = "%s: changed %s; older payload hashes to the stored digest: %s; digest column same as older: %s" % (
            table, "+".join(changed) or "nothing", old_ok, same_digest_col)
        summary[key] += 1
        if changed == ["metadata_json"] and summary[key] <= 2:
            ma, mb = json.loads(r["metadata_json"]), json.loads(o["metadata_json"])
            print("  %s metadata keys differing: %s" % (r["id"][:24], sorted(k for k in set(ma) | set(mb)
                                                                        if ma.get(k) != mb.get(k))[:8]))
for k, v in summary.most_common():
    print("%4d  %s" % (v, k))
