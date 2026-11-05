"""
Load the authored craft ontology into the store.

Craft files declare NODES as (id, name, body, rels, tags) and SUPPORT as
(id, kind, name[, body]). Anything a rel points at that has not been declared
gets a stub, so I can write "causes effect.unease" before effect.unease exists
and fill it in later. Stubs are reported at the end - an unfilled stub is a
to-do list, not an error.
"""
import importlib.util, os, sys, glob

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
from fb_store import Store, RELS, NODE_KINDS

CRAFT_DIR = os.environ.get("FILMBRAIN_CRAFT_DIR", os.path.join(_ROOT, "craft"))

# kind inferred from the id prefix when a node does not say
PREFIX_KIND = {
    "craft.": "craft", "effect.": "craft", "failure.": "failure", "principle.": "principle",
    "metric.": "metric", "asset.": "asset", "tool.": "tool", "engine.": "engine",
    "voice.": "voice", "work.": "work", "person.": "person", "format.": "format",
    "preset.": "preset", "project.": "project", "ref.": "reference", "term.": "term",
    "param.": "param",
}


def kind_for(node_id, declared=None):
    if declared and declared in NODE_KINDS:
        return declared
    for p, k in PREFIX_KIND.items():
        if node_id.startswith(p):
            return k
    return "craft"


def load_module(path):
    name = "craft_" + os.path.splitext(os.path.basename(path))[0]
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    s = Store()
    declared = set()
    pending_edges = []
    bad_rels = []
    n_nodes = 0

    files = sorted(glob.glob(os.path.join(CRAFT_DIR, "*.py")))
    for path in files:
        mod = load_module(path)
        dom = getattr(mod, "DOMAIN", None)
        if dom:
            did, dname, dbody = dom
            s.add_node(did, "craft", dname, title=dname, body=dbody, source="authored",
                       tags=["domain"], commit=False)
            declared.add(did)
            n_nodes += 1

        for row in getattr(mod, "NODES", []):
            nid, name, body = row[0], row[1], row[2]
            rels = row[3] if len(row) > 3 else []
            tags = row[4] if len(row) > 4 else []
            s.add_node(nid, kind_for(nid), name, title=name, body=body,
                       source="authored", tags=tags, commit=False)
            declared.add(nid)
            n_nodes += 1
            for rel, dst in rels:
                if rel not in RELS:
                    bad_rels.append((nid, rel, dst))
                    rel = "see_also"
                pending_edges.append((nid, rel, dst))

        for row in getattr(mod, "SUPPORT", []):
            sid, skind, sname = row[0], row[1], row[2]
            sbody = row[3] if len(row) > 3 else None
            # Files load alphabetically, and a SUPPORT entry is often a bare
            # placeholder for something another file defines properly. Never let
            # an empty body overwrite a written one - that is how four nodes
            # ended up blank after anchors.py had filled them.
            if not sbody:
                existing = s.get(sid)
                if existing and (existing.get("body") or "").strip():
                    declared.add(sid)
                    continue
            s.add_node(sid, kind_for(sid, skind if skind in NODE_KINDS else None),
                       sname, title=sname, body=sbody, source="authored",
                       tags=[skind], commit=False)
            declared.add(sid)
            n_nodes += 1
        s.commit()
        print("loaded", os.path.basename(path), flush=True)

    # stubs for anything pointed at but never declared
    stubs = set()
    for src, rel, dst in pending_edges:
        if dst not in declared and not s.get(dst):
            stubs.add(dst)
    for dst in sorted(stubs):
        s.add_node(dst, kind_for(dst), dst.split(".", 1)[-1].replace("_", " "),
                   title=dst.split(".", 1)[-1].replace("_", " "),
                   body="(stub - pointed at by the craft graph, not yet written)",
                   source="stub", tags=["stub"], commit=False)
        n_nodes += 1
    s.commit()

    n_edges = 0
    for src, rel, dst in pending_edges:
        s.add_edge(src, rel, dst, source="authored", commit=False)
        n_edges += 1
        # the inverse edge where it is meaningful, so neighbours() finds both ways
        inv = {"causes": "caused_by", "part_of": "has_part", "is_a": "has_instance"}
    s.commit()

    print("craft:", n_nodes, "nodes", n_edges, "edges,", len(stubs), "stubs")
    if bad_rels:
        print("UNKNOWN RELATIONS (mapped to see_also):")
        for r in bad_rels:
            print("  ", r)
    if stubs:
        print("stubs to fill:", ", ".join(sorted(stubs)[:40]))
    print(s.stats())


if __name__ == "__main__":
    main()
