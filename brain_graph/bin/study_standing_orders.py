"""study_standing_orders.py - could the brain have handed the agent the order he then had to say again?
(27 Sep 18:0x, claude-825b1305; his 17:4x "think about who it is not helping, and make it work ... the other index
method does help, is there a way to combine them")

Of his 73 corrections and repeats since 1 Sep, 22 restated an order of his that the brain already held (labelled by
hand in standing_orders_labels.json: the GPU nine times, pictures and 3Blue1Brown motion six, the voice four, public
writing, a retired film, Codex pictures once each). For each, the session is replayed from his ask to his correction,
and at every moment the membrane would evaluate (his prompt, and every tool call) each method names his stored orders
(owner_* memories and memories marked owner-stated) that existed at that moment:

  ask      the word index (the recall stack's BM25, the "other index method") on his ask's words: today's word line,
           restricted to his orders
  context  the word index on his ask plus the names of what the agent touched in the ten minutes before and the
           agent's own words there
  graph    the membrane on the week's store (learned edges, content edges, links, node size), his orders only
  both     reciprocal-rank fusion of context and graph (k 60)

An order counts as existing from the earlier of its first commit to the brain repo and the end of its creation day; a
superseded order hands over to its successor once the successor exists. Scored per correction: the best rank of an
order he restated, at his ask, at the agent's last tool moment before the correction, and at any moment between; and
the number of distinct orders each method put in its top two over the stretch (naming everything is naming nothing).

    python study_standing_orders.py LABELS_JSON OUT_JSON
"""
import collections
import datetime
import glob
import json
import os
import re
import sys
import time

HERE = os.path.join(os.path.expanduser("~"), ".claude", "brain_graph", "bin")
sys.path.insert(0, HERE)
sys.path.insert(1, os.path.join(os.path.expanduser("~"), ".claude", "dreaming"))
import bm25  # noqa: E402
import live_membrane as LM  # noqa: E402
import study_live_membrane as SLM  # noqa: E402

MEM = os.path.join(os.path.expanduser("~"), ".claude", "projects", (os.environ.get("BRAIN_SLUG") or os.path.expanduser("~").replace(":", "-").replace(chr(92), "-").replace("/", "-")), "memory")
REPO_MEM = os.path.join(os.path.expanduser("~"), "ai-memories-and-functionality", "01_ai_consciousness", "claude", "memory")
FIELD = re.compile(r"^\s*(created_at|created|status|superseded_by)\s*:\s*(.+)$", re.M)
SLUG_DAY = re.compile(r"_(20\d\d)_(\d\d)_(\d\d)")
TOKEN = re.compile(r"[a-z][a-z0-9]{2,}")
NOISE = {"users", "owner", "appdata", "local", "temp", "claude", "scratchpad", "projects", "com", "www", "http", "https",
         "exe", "bin", "tmp", "the", "and", "for", "with", "src", "lib", "home", "jsonl", "json", "txt", "html", "memory",
         MEM.split(os.sep)[-2].lower(), "python", "bash", "powershell", "tool", "cmd", "file", "dir", "proj", "said", "kw", "hook"}
WEEKS = [("2026-09-01 00:00", "X_drift0901"), ("2026-09-08 00:00", "X_drift0908"), ("2026-09-15 00:00", "X_drift0915"),
         ("2026-09-22 00:00", "X_drift0922")]
ARMS = ("ask", "context", "graph", "both")


def first_adds():
    """{slug: first commit time} of every memory mirrored in the brain repo, in one git pass."""
    import subprocess
    out = subprocess.run(["git", "log", "--diff-filter=A", "--format=C %ct", "--name-only", "--", "."], cwd=REPO_MEM,
                         capture_output=True, text=True, encoding="utf-8", errors="replace",
                         creationflags=0x08000000 if os.name == "nt" else 0).stdout
    first, t = {}, None
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("C "):
            t = int(line[2:])
        elif line.endswith(".md") and t:
            s = line.rsplit("/", 1)[-1][:-3].lower()
            first[s] = min(first.get(s, t), t)
    return first


def orders():
    """{slug: {avail, superseded_by, desc}} for his stored orders."""
    fa = first_adds()
    out = {}
    for p in glob.glob(os.path.join(MEM, "*.md")):
        slug = os.path.basename(p)[:-3].lower()
        try:
            head = open(p, encoding="utf-8", errors="replace").read(4000)
        except OSError:
            continue
        if not head.startswith("---"):
            continue
        head = head.split("\n---", 1)[0]
        f = {k: v.strip().strip('"') for k, v in FIELD.findall(head)}
        if not (slug.startswith("owner_") or "owner-stated" in f.get("status", "").lower()):
            continue
        ends = []
        day = (f.get("created_at") or f.get("created") or "")[:10]
        m = SLUG_DAY.search(slug)
        if not day and m:
            day = "%s-%s-%s" % m.groups()
        try:
            ends.append(datetime.datetime.strptime(day, "%Y-%m-%d").timestamp() + 86399.0)
        except ValueError:
            pass
        if slug in fa:
            ends.append(float(fa[slug]))
        if not ends:
            ends.append(os.path.getmtime(p))
        sup = f.get("superseded_by", "")
        sup = sup.split()[0].strip("[]'\",").lower() if sup else ""
        out[slug] = {"avail": min(ends), "superseded_by": sup}
    return out


def resolve(slug, t, ords):
    """The order in force at t: a superseded order hands over to its successor once the successor exists."""
    seen = set()
    while slug in ords and ords[slug]["superseded_by"] and slug not in seen:
        seen.add(slug)
        nxt = ords[slug]["superseded_by"]
        if nxt in ords and ords[nxt]["avail"] <= t:
            slug = nxt
        else:
            break
    return slug


def rank_words(query, t, ords, conn, n=10):
    if not query.strip():
        return []
    hits = bm25.score_postings(query, topk=800, conn=conn) or []
    out = []
    for sl, _sc in hits:
        sl = str(sl).lower()
        if sl in ords and ords[sl]["avail"] <= t:
            r = resolve(sl, t, ords)
            if r not in out:
                out.append(r)
        if len(out) >= n:
            break
    return out


def rank_graph(lv, items, t, ords, pol, n=10):
    ev = lv.evaluate(items, t, seen_slugs=(), pol=pol, mem_asof=t)
    out = []
    for c in sorted(ev.get("mem_all", []), key=lambda c: -c.get("fused", 0.0)):
        sl = str(c["slug"]).lower()
        if sl in ords and ords[sl]["avail"] <= t:
            r = resolve(sl, t, ords)
            if r not in out:
                out.append(r)
        if len(out) >= n:
            break
    return out


def rrf(*lists, k=60.0, n=10):
    sc = collections.defaultdict(float)
    for lst in lists:
        for r, sl in enumerate(lst):
            sc[sl] += 1.0 / (k + r)
    return [sl for sl, _v in sorted(sc.items(), key=lambda kv: -kv[1])][:n]


def words_of(name):
    kd, _, nid = name.partition(":")
    return [w for w in TOKEN.findall(nid.lower()) if w not in NOISE]


def main():
    labels_path, out_path = sys.argv[1], sys.argv[2]
    lab = json.load(open(labels_path, encoding="utf-8"))["preventable"]
    t0 = time.time()
    ords = orders()
    print("stored orders of his: %d (%.0f s)" % (len(ords), time.time() - t0), flush=True)
    for c in lab:
        c["avail_orders"] = [o for o in c["orders"] if o in ords and ords[o]["avail"] <= (c["ask_t"] or c["t"])]
    usable = [c for c in lab if c["avail_orders"]]
    print("preventable corrections: %d labelled, %d whose restated order existed before the ask" % (len(lab), len(usable)),
          flush=True)
    only = {c["sid"] for c in usable}
    t_from = min(c["ask_t"] or c["t"] for c in usable) - 3600.0
    by = SLM.load_recaptured(t_from, only=only)
    print("sessions loaded: %d (%.0f s)" % (len(by), time.time() - t0), flush=True)
    conn = bm25._postings_conn(bm25.INDEX)
    pol = LM.policy()
    lvs = {}
    res = []
    for c in usable:
        t_a, t_c = c["ask_t"] or c["t"], c["t"]
        tag = [tg for d, tg in WEEKS if time.mktime(time.strptime(d, "%Y-%m-%d %H:%M")) <= t_a][-1]
        if tag not in lvs:
            lvs[tag] = LM.Live(sidecar=os.path.join(SLM.STUDY, "sidecar_wf%s.sqlite" % tag),
                               learned=os.path.join(SLM.STUDY, "no_learned.sqlite"), delta=None)
        lv = lvs[tag]
        evs = by.get(c["sid"], [])
        moments = []
        for i, (ts, kd, nid) in enumerate(evs):
            if t_a - 5.0 <= ts < t_c - 1.0 and kd == "tool":
                moments.append(i)
        if len(moments) > 150:
            step = len(moments) / 150.0
            moments = [moments[int(k * step)] for k in range(150)]
        ask_words = " ".join(TOKEN.findall((c["ask"] or "").lower()))
        target = set(c["avail_orders"])
        per = {a: {"ranks": [], "top2": set()} for a in ARMS}
        # the ask moment itself (his prompt, before the agent has touched anything for it)
        for j, i in enumerate([None] + moments):
            if i is None:
                t = t_a + 1.0
                items = [("kw:" + w, 0.0) for w in ask_words.split()[:40]]
                ctx_words = []
            else:
                t = evs[i][0]
                items, got = [], set()
                for ts2, kd2, nid2 in reversed(evs[:i + 1]):
                    if t - ts2 > pol["window_s"] or len(items) >= pol["k_items"]:
                        break
                    nm = "%s:%s" % (kd2, nid2)
                    if nm not in got:
                        got.add(nm)
                        items.append((nm, t - ts2))
                ctx_words = []
                for nm, _age in items:
                    ctx_words += words_of(nm)
            lists = {"ask": rank_words(ask_words, t, ords, conn),
                     "context": rank_words(ask_words + " " + " ".join(ctx_words[:80]), t, ords, conn),
                     "graph": rank_graph(lv, items, t, ords, pol) if items else []}
            lists["both"] = rrf(lists["context"], lists["graph"])
            for a in ARMS:
                lst = lists[a]
                best = min([lst.index(o) + 1 for o in target if o in lst] or [99])
                per[a]["ranks"].append(best)
                per[a]["top2"].update(lst[:2])
        row = {"n": c["n"], "theme": c["theme"], "sid": c["sid"], "moments": len(moments) + 1,
               "minutes": round((t_c - t_a) / 60.0, 1)}
        for a in ARMS:
            rk = per[a]["ranks"]
            row[a] = {"at_ask": rk[0], "at_case": rk[-1] if len(rk) > 1 else rk[0], "best": min(rk),
                      "share_top2": round(sum(1 for x in rk if x <= 2) / float(len(rk)), 3),
                      "distinct_top2": len(per[a]["top2"])}
        res.append(row)
        print("#%d %-14s %3d moments  " % (c["n"], c["theme"], row["moments"]) + "  ".join(
            "%s ask %s case %s best %s spread %d" % (a, row[a]["at_ask"], row[a]["at_case"], row[a]["best"],
                                                       row[a]["distinct_top2"]) for a in ARMS), flush=True)
    summ = {}
    for a in ARMS:
        summ[a] = {"hit2_at_ask": sum(1 for r in res if r[a]["at_ask"] <= 2),
                   "hit2_at_case": sum(1 for r in res if r[a]["at_case"] <= 2),
                   "hit2_any": sum(1 for r in res if r[a]["best"] <= 2),
                   "mean_share_top2": round(sum(r[a]["share_top2"] for r in res) / max(1, len(res)), 3),
                   "median_distinct_top2": sorted(r[a]["distinct_top2"] for r in res)[len(res) // 2] if res else None}
    out = {"built": time.strftime("%Y-%m-%d %H:%M:%S"), "corrections_labelled": len(lab), "usable": len(res),
           "summary": summ, "rows": res, "seconds": round(time.time() - t0)}
    json.dump(out, open(out_path, "w", encoding="utf-8"), indent=1)
    print(json.dumps(summ, indent=1))


if __name__ == "__main__":
    main()
