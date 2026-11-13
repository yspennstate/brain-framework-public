"""test_recall_attractor.py - eval_recall_attractor.py on a fixture store with one would-be attractor.

    python test_recall_attractor.py [--brain BRAIN.py]

A throwaway memory folder holds ten memories on unrelated topics and one parent memory whose attached document is
long and quotes every topic's description. The attachment is indexed (the index is built after it is attached), so
recall must rank it by BM25 like any memory and must not promote it above the memory a query is actually about.
 A1 each topic memory asked by its own description: the parent is not rank 1 on more than half of them
 A2 the ten (description -> slug) pairs: at least eight at rank 1 and all ten in the top 8
 A3 a nonsense query puts nothing on top with a strong score
 A4 the command line gives the same verdict and exits 0
 C1 control: the same measure over a recall that always answers the parent reports share 1.0 and fails
With --brain, a copy of brain.py that promotes an attachment the index has already read must fail A1 and A2.
Nothing outside the temporary folder is read or written: HOME points into it and the index is a shadow index.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

if "-h" in sys.argv[1:] or "--help" in sys.argv[1:]:
    print(__doc__)
    sys.exit(0)

HERE = os.path.dirname(os.path.abspath(__file__))
ARGS = sys.argv[1:]
BRAIN = os.path.abspath(ARGS[ARGS.index("--brain") + 1]) if "--brain" in ARGS else os.path.join(HERE, "brain.py")
TD = tempfile.mkdtemp(prefix="recall_attractor_test_")
MEM = os.path.join(TD, "memory")
INDEX = os.path.join(TD, "bm25_index.json.gz")
os.makedirs(MEM)
ENV = {"BRAIN_MEM_DIR": MEM, "BRAIN_BM25_INDEX": INDEX, "BRAIN_DOC_EXPANSION": "0", "BRAIN_RECALL_LOG": "0",
       "BRAIN_RECALL_REFRESH": "0", "RECALL_REWRITE_NO_LOG": "1", "BRAIN_RECALL_CACHE_DIR": os.path.join(TD, "fc"),
       "HOME": os.path.join(TD, "home"), "USERPROFILE": os.path.join(TD, "home")}
os.makedirs(ENV["HOME"])
os.environ.update(ENV)
fails, ran = [], []


def check(ok, what):
    print(("PASS  " if ok else "FAIL  ") + what)
    ran.append(what)
    if not ok:
        fails.append(what)


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


TOPICS = [
    ("glacier_melt_rates_2026_01_01", "glacier", "Glacier melt rates measured on the northern ice field each summer"),
    ("sourdough_starter_feeding_2026_01_02", "sourdough", "Sourdough starter feeding schedule and flour hydration"),
    ("violin_bow_rehair_2026_01_03", "violin", "Violin bow rehair with horsehair and rosin"),
    ("tidal_pool_ecology_2026_01_04", "tidal", "Tidal pool ecology of anemones and hermit crabs"),
    ("bicycle_chain_wear_2026_01_05", "bicycle", "Bicycle chain wear gauge and cassette replacement"),
    ("orchid_repotting_bark_2026_01_06", "orchid", "Orchid repotting in pine bark after flowering"),
    ("telescope_mirror_collimation_2026_01_07", "telescope", "Telescope mirror collimation with a laser tool"),
    ("beekeeping_winter_cluster_2026_01_08", "beekeeping", "Beekeeping winter cluster and hive insulation"),
    ("pottery_glaze_firing_2026_01_09", "pottery", "Pottery glaze firing at cone six in a kiln"),
    ("chess_endgame_rook_2026_01_10", "chess", "Chess endgame technique with rook against pawn"),
]
PARENT = "general_notes_hub_2026_01_11"


def memory(slug, desc, body):
    with open(os.path.join(MEM, slug + ".md"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("---\nname: %s\ndescription: %s\nmetadata:\n  type: project\n---\n\n%s\n" % (slug, desc, body))
    old = time.time() - 7200
    os.utime(os.path.join(MEM, slug + ".md"), (old, old))


try:
    lines = ["# Memory Index"]
    for slug, word, desc in TOPICS:
        memory(slug, desc, desc + ". " + ("Notes on %s practice and what was learned. " % word) * 3)
        lines.append("- [%s.md](%s.md) - %s" % (slug, slug, desc))
    memory(PARENT, "A hub of general notes", "A hub of general notes kept in one place.")
    lines.append("- [%s.md](%s.md) - A hub of general notes" % (PARENT, PARENT))
    with open(os.path.join(MEM, "MEMORY.md"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    nd = load(os.path.join(HERE, "brain_node_documents.py"), "brain_node_documents")
    # the would-be attractor: a long document quoting every topic's description, padded with filler
    doc = "\n".join("Item %d quotes: %s." % (i, d) for i, (_s, _w, d) in enumerate(TOPICS))
    doc += "\n" + ("Filler paragraph about nothing in particular. " * 40 + "\n") * 25
    src = os.path.join(TD, "hub_notes.md")
    with open(src, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(doc)
    nd.attach(MEM, PARENT, src, name="notes.md", description="full notes", auto_read=False)
    # attached two hours before the index is built, as the memories were written: an attachment newer than the
    # index's scan is one the index may not have read, and recall rightly promotes that one
    old = time.time() - 7200
    for root, _dirs, files in os.walk(str(nd.bundle_dir(MEM, PARENT))):
        for f in files:
            os.utime(os.path.join(root, f), (old, old))
    bm25 = load(os.path.join(HERE, "bm25.py"), "bm25")
    bm25.spawn_detached_postings = lambda *a, **k: None
    bm25.spawn_detached_refresh = lambda *a, **k: None
    assert os.path.abspath(bm25.MEM) == os.path.abspath(MEM), bm25.MEM
    bm25.build(MEM, INDEX)

    ev = load(os.path.join(HERE, "eval_recall_attractor.py"), "eval_recall_attractor")
    brain = load(BRAIN, "brain")
    assert os.path.abspath(brain.MEM) == os.path.abspath(MEM), brain.MEM
    qs = [d for _s, _w, d in TOPICS]
    pairs = [(d, s) for s, _w, d in TOPICS]
    rec = ev.measure(ev.brain_recall(brain), qs, pairs)
    share_parent = sum(1 for r in rec["per_query"] if r["top"][:1] == [PARENT]) / len(qs)
    check(share_parent <= 0.5 and rec["attractor_top1_share"] <= 0.5,
          "A1 the parent is rank 1 on %.0f%% of its topic queries; most common rank 1 holds %.0f%%"
          % (100 * share_parent, 100 * rec["attractor_top1_share"]))
    check(rec["positive_hit1"] >= 8 and rec["positive_hit8"] == 10,
          "A2 pairs hit@1 %d/10, hit@8 %d/10" % (rec["positive_hit1"], rec["positive_hit8"]))
    check(rec["neg_top_score"] < 1.0, "A3 nonsense queries top score %.2f" % rec["neg_top_score"])
    qf = os.path.join(TD, "queries.txt")
    with open(qf, "w", encoding="utf-8") as fh:
        fh.write("\n".join(qs) + "\n")
    out = os.path.join(TD, "receipt.json")
    r = subprocess.run([sys.executable, os.path.join(HERE, "eval_recall_attractor.py"), "--queries", qf, "--out", out],
                       capture_output=True, encoding="utf-8", env=dict(os.environ, **ENV), timeout=300)
    cli = json.load(open(out, encoding="utf-8")) if os.path.exists(out) else {}
    check(r.returncode == 0 and cli.get("ok") is True
          and cli.get("attractor_top1_share") == rec["attractor_top1_share"],
          "A4 the command line exits %s with share %s" % (r.returncode, cli.get("attractor_top1_share")))
    always = lambda q, n=8: [(9.0, "lex", PARENT)]
    bad = ev.measure(always, qs, pairs)
    check(bad["attractor_top1_share"] == 1.0 and bad["positive_hit1"] == 0,
          "C1 control: a recall that always answers the parent reads share %.1f, hit@1 %d"
          % (bad["attractor_top1_share"], bad["positive_hit1"]))
finally:
    shutil.rmtree(TD, ignore_errors=True)
print("%d of %d pass" % (len(ran) - len(fails), len(ran)))
sys.exit(1 if fails else 0)
