#!/usr/bin/env python3
"""Stage a memory layer for the full brain graph (TOP10 rank 8, claude-1ca628ae, 3 Nov 2026).

The installed full graph (graph_lane_store) is built from hook events, so it holds only the memories a hooked session
wrote or recalled (7,795 of 45,087 on 3 Nov). This writes, beside it and never into it:

  graph_lane_stage_memlayer/nodes_add.jsonl   one row per memory missing from the graph: {"id": "mem:<slug>", ...}
  graph_lane_stage_memlayer/edges_add.jsonl   typed edges with their evidence:
      links_to   memory -> memory, from a [[slug]] written in the memory's body (resolved to an existing file only)
      cites      memory -> file:/dir: node already in the graph, from a path in the memory's evidence line or body
      supersedes memory -> memory, from superseded_by / supersedes in the frontmatter
  graph_lane_stage_memlayer/report.json       counts, and how many edges land on nodes the event graph already has

Nothing is inferred: every edge is a link, a path or a frontmatter field the memory's author wrote. Idempotent (rewrites
the stage dir each run). Adoption into live recall is a separate, tested step.
"""
import json, os, re, sys, time

HOME = os.path.expanduser("~")
MEM = os.path.join(HOME, ".claude/projects/{{BRAIN_SLUG}}/memory")
STORE = os.path.join(HOME, ".claude/dreaming/graph_lane_store")
OUT = os.path.join(HOME, ".claude/dreaming/graph_lane_stage_memlayer")
LINK = re.compile(r"\[\[([^\]|#]+)")
PATH = re.compile(r"(~/[^\s;,'\"`)\]]+|~/[^\s;,'\"`)\]]+)")


def main():
    names = json.load(open(os.path.join(STORE, "nodes.json")))["names"]
    have = set(names)
    in_graph = {n.split(":", 1)[1] for n in names if n.startswith(("mem:", "memw:"))}
    files = sorted(f for f in os.listdir(MEM) if f.endswith(".md") and f != "MEMORY.md")
    slugs = {f[:-3] for f in files}
    os.makedirs(OUT, exist_ok=True)
    nodes = open(os.path.join(OUT, "nodes_add.jsonl.tmp"), "w")
    edges = open(os.path.join(OUT, "edges_add.jsonl.tmp"), "w")
    cnt = {"memories": len(slugs), "already_in_graph": len(slugs & in_graph), "nodes_added": 0,
           "links_to": 0, "cites": 0, "cites_existing_node": 0, "supersedes": 0, "unresolved_links": 0}
    for f in files:
        slug = f[:-3]
        t = open(os.path.join(MEM, f), errors="ignore").read()
        head, _, body = t.partition("\n---\n") if t.startswith("---") else ("", "", t)
        desc = (re.search(r"^description:\s*(.+)$", head, re.M) or [None, ""])[1].strip().strip('"')[:300]
        status = (re.search(r"^\s*status:\s*(\S+)", head, re.M) or [None, ""])[1]
        if slug not in in_graph:
            nodes.write(json.dumps({"id": "mem:" + slug, "desc": desc, "status": status}) + "\n")
            cnt["nodes_added"] += 1
        seen = set()
        for m in LINK.findall(t):
            m = m.strip().split(" (")[0]
            if m in slugs and m != slug and m not in seen:
                seen.add(m)
                edges.write(json.dumps({"src": "mem:" + slug, "dst": "mem:" + m, "type": "links_to"}) + "\n")
                cnt["links_to"] += 1
            elif m not in slugs:
                cnt["unresolved_links"] += 1
        for key in ("superseded_by", "supersedes"):
            for m in re.findall(r"^\s*%s:\s*\"?([A-Za-z0-9_\-]+)" % key, head, re.M):
                if m in slugs:
                    a, b = (slug, m) if key == "supersedes" else (m, slug)
                    edges.write(json.dumps({"src": "mem:" + a, "dst": "mem:" + b, "type": "supersedes"}) + "\n")
                    cnt["supersedes"] += 1
        cited = set()
        for p in PATH.findall(t):
            p = p.rstrip(".:?!*>")
            full = p.replace("~", HOME, 1) if p.startswith("~") else p
            for cand in ("file:" + full, "dir:" + full, "file:" + p, "dir:" + p):
                if cand in have and cand not in cited:
                    cited.add(cand)
                    cnt["cites_existing_node"] += 1
                    break
            else:
                cand = "file:" + full
                if cand in cited:
                    continue
                cited.add(cand)
            edges.write(json.dumps({"src": "mem:" + slug, "dst": cand, "type": "cites"}) + "\n")
            cnt["cites"] += 1
    nodes.close(); edges.close()
    for n in ("nodes_add.jsonl", "edges_add.jsonl"):
        os.replace(os.path.join(OUT, n + ".tmp"), os.path.join(OUT, n))
    cnt["built"] = time.strftime("%F %T %z")
    cnt["store_release"] = json.load(open(os.path.join(STORE, "INSTALLED_RELEASE.json"))).get("tag", "?")
    json.dump(cnt, open(os.path.join(OUT, "report.json"), "w"), indent=1)
    print(json.dumps(cnt, indent=1))


if __name__ == "__main__":
    main()
