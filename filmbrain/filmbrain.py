#!/usr/bin/env python
"""
filmbrain - the movie-making part of the brain.

  filmbrain recall "how do I stop a render looking computer generated"
  filmbrain node craft.motion_blur
  filmbrain neighbors craft.narration --depth 2
  filmbrain learn "grain at strength 4 is what stops a 640x352 upscale reading as plastic" \
        --node craft.grain --evidence "88 clips, Bastogne night 09-03" --confidence verified
  filmbrain record --kind tts --tool kokoro --params '{"voice":"bm_lewis","speed":0.95}' \
        --metrics '{"f0_median":94.1}' --outcome ok --artifact out.wav
  filmbrain checklist narration
  filmbrain stats
  filmbrain tools

The point of `recall` is that it answers with CRAFT first and API detail second.
A question about looking cinematic should not return four hundred ffmpeg options.
"""
import argparse, json, os, sys, textwrap

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fb_store import Store, slug

W = 100

# craft, principles and failures are what a person asks for; params are what a
# pipeline asks for. Rank accordingly unless the caller says otherwise.
KIND_WEIGHT = {
    "principle": 3.0, "failure": 2.6, "craft": 2.4, "metric": 1.8, "preset": 1.8,
    "engine": 1.6, "voice": 1.6, "tool": 1.5, "project": 1.4, "asset": 1.2,
    "work": 1.1, "person": 1.1, "run": 1.0, "reference": 1.0, "term": 1.0,
    "format": 0.55, "param": 0.4,
}


def wrap(s, indent=6, width=W):
    if not s:
        return ""
    return textwrap.fill(s, width=width, initial_indent=" " * indent,
                         subsequent_indent=" " * indent)


def cmd_recall(a):
    s = Store()
    # Conversational glue previously filled the 400-row candidate window with
    # incidental hits ("needs" fetched Windows path gotchas for a typesetting
    # question). Remove only common question words; preserve technical tokens,
    # numbers and negation. Keep the original query for the displayed context.
    import re as _re
    query_stop = {"a", "an", "the", "i", "we", "you", "my", "your", "me",
                  "how", "what", "which", "why", "should", "would", "could",
                  "can", "do", "does", "did", "is", "are", "was", "were",
                  "be", "been", "have", "has", "had", "this", "that", "it",
                  "and", "or", "for", "to", "of", "with", "in", "on",
                  "need", "needs", "want", "please", "proper", "looks"}
    query_terms = [t for t in _re.sub(r"[^\w\s]", " ", a.query.lower()).split()
                   if t not in query_stop]
    search_query = " ".join(query_terms) or a.query
    hits = s.search(search_query, limit=400, kind=a.kind)
    # bm25 alone rewards incidental word overlap: "my video looks cheap" scored
    # "looks and references" top because it contains the word "looks". Boost a
    # node whose NAME or TAGS carry the query's words, and boost the symptom
    # nodes hard, because a symptom is phrased the way a person actually asks.
    import re as _re
    terms = [t for t in _re.sub(r"[^\w\s]", " ", a.query.lower()).split() if len(t) > 2]
    stop = {"the", "and", "for", "how", "why", "what", "does", "did", "you", "your",
            "with", "that", "this", "are", "was", "should", "would", "can", "not",
            "have", "has", "get", "got", "but", "its", "it's", "use", "using", "make"}
    terms = [t for t in terms if t not in stop]

    scored = []
    for h in hits:
        w = KIND_WEIGHT.get(h["kind"], 1.0)
        if h.get("source") == "stub":
            w *= 0.2
        name = (h.get("name") or "").lower()
        tags = (h.get("tags") or "").lower()
        hit_name = sum(1 for t in terms if t in name)
        hit_tag = sum(1 for t in terms if t in tags)
        w *= 1.0 + 0.8 * hit_name + 0.3 * hit_tag
        # symptoms and gotchas are both phrased as the problem rather than the
        # concept, which is how a person asks. A gotcha gets slightly more when
        # the query actually matches its name, because it is the specific answer
        # where a symptom is the general one.
        if h["id"].startswith("symptom."):
            w *= 2.2
        elif h["id"].startswith("gotcha."):
            w *= 2.0 + 0.6 * hit_name
        # bm25 returns negative-is-better; flip it
        scored.append((-float(h.get("score") or 0.0) * w, h))
    scored.sort(key=lambda x: -x[0])

    top = scored[: a.limit]
    print("filmbrain recall: " + a.query)
    print("-" * W)
    for sc, h in top:
        print("  [{}] {}  ({})".format(h["kind"], h["name"], h["id"]))
        if h.get("body") and not a.brief:
            print(wrap(h["body"][:700]))
        print()

    les = s.search_lessons(a.query, limit=6)
    if les:
        print("  what we learned, in our own runs")
        print("  " + "-" * (W - 2))
        for l in les:
            tag = l.get("confidence") or "provisional"
            print(wrap("[" + tag + "] " + l["text"], indent=4))
            if l.get("evidence"):
                print(wrap("evidence: " + l["evidence"], indent=8))
            print()

    if not top and not les:
        print("  nothing yet. Add it:  filmbrain learn \"...\" --node <id>")


def cmd_node(a):
    s = Store()
    n = s.get(a.id)
    if not n:
        hits = s.search(a.id.replace(".", " "), limit=6)
        print("no node " + a.id + ("; did you mean:" if hits else ""))
        for h in hits:
            print("   ", h["id"], "-", h["name"])
        return
    print("=" * W)
    print(n["name"] + "   [" + n["kind"] + "]   " + n["id"])
    raw_status = n.get("status")
    status_label = "active (legacy NULL)" if raw_status is None else ("invalid blank " + repr(raw_status) if not str(raw_status).strip() else str(raw_status))
    print("status: " + status_label)
    print("source: " + (n.get("source") or "?") + "   tags: " + (n.get("tags") or ""))
    print("=" * W)
    print(wrap(n.get("body") or "(no body)", indent=2))
    print()
    edges = s.neighbors(a.id, depth=1)
    out = [e for e in edges if e["src"] == a.id]
    inn = [e for e in edges if e["dst"] == a.id]
    if out:
        print("  this ->")
        for e in sorted(out, key=lambda x: x["rel"])[:60]:
            t = s.get(e["dst"])
            print("     {:<16} {}".format(e["rel"], (t["name"] if t else e["dst"])))
    if inn:
        print("  -> this")
        for e in sorted(inn, key=lambda x: x["rel"])[:60]:
            t = s.get(e["src"])
            print("     {:<16} {}".format(e["rel"], (t["name"] if t else e["src"])))
    obs = list(s.db.execute("SELECT metric,num,unit,text,created,run_id FROM obs WHERE node_id=? "
                            "ORDER BY created DESC LIMIT 20", (a.id,)))
    if obs:
        print("  measured")
        for o in obs:
            v = o["num"] if o["num"] is not None else o["text"]
            print("     {:<28} {} {}   {}".format(o["metric"], v, o["unit"] or "", o["created"]))
    les = list(s.db.execute("SELECT text,evidence,confidence FROM lesson WHERE node_id=?", (a.id,)))
    if les:
        print("  learned")
        for l in les:
            print(wrap("[" + l["confidence"] + "] " + l["text"], indent=5))


def cmd_neighbors(a):
    s = Store()
    edges = s.neighbors(a.id, depth=a.depth)
    seen = set()
    print(a.id + "  (depth " + str(a.depth) + ", " + str(len(edges)) + " edges)")
    for e in edges:
        k = (e["src"], e["rel"], e["dst"])
        if k in seen:
            continue
        seen.add(k)
        print("   {} --{}--> {}".format(e["src"], e["rel"], e["dst"]))


def cmd_learn(a):
    s = Store()
    if a.node and not s.get(a.node):
        print("note: node " + a.node + " does not exist yet; creating a stub for it")
        s.add_node(a.node, "craft", a.node.split(".", 1)[-1].replace("_", " "),
                   body="(created by a lesson)", source="stub", tags=["stub"])
    lid = s.add_lesson(a.text, node_id=a.node, evidence=a.evidence,
                       confidence=a.confidence, session=a.session)
    print("learned " + lid)


def cmd_record(a):
    s = Store()
    params = json.loads(a.params) if a.params else {}
    metrics = json.loads(a.metrics) if a.metrics else {}
    rid = s.add_run(a.kind, a.tool, params=params, outcome=a.outcome, metrics=metrics,
                    artifact=a.artifact, notes=a.notes, seconds=a.seconds, session=a.session)
    for k, v in metrics.items():
        if isinstance(v, (int, float)):
            s.add_obs(a.node or ("tool." + slug(a.tool)), k, num=float(v), run_id=rid)
        else:
            s.add_obs(a.node or ("tool." + slug(a.tool)), k, text=str(v), run_id=rid)
    print(rid)


def cmd_stats(a):
    s = Store()
    d = s.stats()
    print("nodes {}   edges {}   nodes+edges {}".format(d["nodes"], d["edges"],
                                                        d["nodes"] + d["edges"]))
    print("runs {}   observations {}   lessons {}".format(d["runs"], d["obs"], d["lessons"]))
    print()
    print("by kind:")
    for k, v in d["by_kind"].items():
        print("   {:<12} {}".format(k, v))
    print("by source:")
    for k, v in d["by_source"].items():
        print("   {:<18} {}".format(k, v))
    print("top relations:")
    for k, v in list(d["by_rel"].items())[:12]:
        print("   {:<16} {}".format(k, v))


def cmd_tools(a):
    s = Store()
    rows = list(s.db.execute("SELECT * FROM node WHERE kind IN ('tool','engine') ORDER BY kind,name"))
    for r in rows:
        print("[{}] {}".format(r["kind"], r["name"]))
        print(wrap((r["body"] or "")[:520], indent=4))
        print()


def cmd_checklist(a):
    """The functional bit: give me everything that PREVENTS a failure in this area."""
    s = Store()
    hits = s.search(a.topic, limit=200)
    ids = {h["id"] for h in hits if h["kind"] in ("craft", "principle", "failure", "metric")}
    if not ids:
        print("no such area")
        return
    q = ",".join("?" * len(ids))
    rows = list(s.db.execute(
        "SELECT e.src, e.rel, e.dst FROM edge e WHERE e.rel IN ('prevents','fixes','requires') "
        "AND (e.src IN (" + q + ") OR e.dst IN (" + q + "))", list(ids) + list(ids)))
    print("checklist for: " + a.topic)
    print("-" * W)
    seen = set()
    for r in rows:
        src, dst = s.get(r["src"]), s.get(r["dst"])
        if not src or not dst:
            continue
        line = "{}  {}  {}".format(src["name"], r["rel"], dst["name"])
        if line in seen:
            continue
        seen.add(line)
        if r["rel"] in ("prevents", "fixes"):
            print("  [ ] " + src["name"] + "   (so you do not get: " + dst["name"] + ")")
            print(wrap((src["body"] or "")[:260], indent=8))
    fails = [s.get(i) for i in ids]
    fails = [f for f in fails if f and f["kind"] == "failure"]
    if fails:
        print()
        print("  ways this goes wrong:")
        for f in fails:
            print("   - " + f["name"])
            print(wrap((f["body"] or "")[:240], indent=8))


def main():
    p = argparse.ArgumentParser(prog="filmbrain")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("recall"); r.add_argument("query"); r.add_argument("--limit", type=int, default=10)
    r.add_argument("--kind"); r.add_argument("--brief", action="store_true"); r.set_defaults(fn=cmd_recall)

    n = sub.add_parser("node"); n.add_argument("id"); n.set_defaults(fn=cmd_node)

    g = sub.add_parser("neighbors"); g.add_argument("id"); g.add_argument("--depth", type=int, default=1)
    g.set_defaults(fn=cmd_neighbors)

    l = sub.add_parser("learn"); l.add_argument("text"); l.add_argument("--node")
    l.add_argument("--evidence"); l.add_argument("--confidence", default="provisional")
    l.add_argument("--session"); l.set_defaults(fn=cmd_learn)

    c = sub.add_parser("record"); c.add_argument("--kind", required=True); c.add_argument("--tool", required=True)
    c.add_argument("--params"); c.add_argument("--metrics"); c.add_argument("--outcome", default="ok")
    c.add_argument("--artifact"); c.add_argument("--notes"); c.add_argument("--seconds", type=float)
    c.add_argument("--node"); c.add_argument("--session"); c.set_defaults(fn=cmd_record)

    sub.add_parser("stats").set_defaults(fn=cmd_stats)
    sub.add_parser("tools").set_defaults(fn=cmd_tools)

    k = sub.add_parser("checklist"); k.add_argument("topic"); k.set_defaults(fn=cmd_checklist)

    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
