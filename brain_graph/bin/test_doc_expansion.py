"""Controls for doc expansion in bm25.py. Run after any change to bm25.read_doc, the expansion
store or the index build. Counts only; no memory text is printed.

    python test_doc_expansion.py [--live-index]

1. switch off: read_doc is the memory's own text (the pre-expansion index text), for every memory
2. switch on: read_doc = the switch-off text + its questions' tokens (120-token budget), nothing else
3. a question for a memory that does not exist adds no document
4. fresh_memories: questions written after the last scan make exactly that memory fresh; switch off ignores the store
5. (--live-index) the published index carries the questions of a sample of expanded memories
"""
if "-h" in __import__("sys").argv[1:] or "--help" in __import__("sys").argv[1:]:
    print((__doc__ or "").strip() or "usage: python %s" % __import__("os").path.basename(__file__))
    raise SystemExit(0)
import importlib.util
import json
import os
import random
import shutil
import sys
import tempfile
import time

DR = os.path.join(os.path.expanduser("~"), ".claude", "dreaming")
sys.path.insert(0, DR)
results = []


def check(name, ok, detail=""):
    results.append(ok)
    print("%s %s %s" % ("PASS" if ok else "FAIL", name, detail), flush=True)


def load(name, env):
    for k, v in env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    spec = importlib.util.spec_from_file_location(name, os.path.join(DR, "bm25.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def main():
    t0 = time.time()
    off = load("bm25_off", {"BRAIN_DOC_EXPANSION": "0"})
    on = load("bm25_on", {"BRAIN_DOC_EXPANSION": "1"})
    store = on.EXPANSION_FILE
    exp = on.expansions()
    if not exp:
        print("SKIP - no doc-expansion store yet (a fresh install writes it on the first expansion pass): %s" % store)
        return 0
    check("the expansion store exists and parses", bool(exp), "%d memories with questions in %s" % (len(exp), store))
    d_off, d_on = off.read_docs(), on.read_docs()
    # date tokens (md0902, bm25 DATES, on since 27 Sep) sit after the head budget by design, so they are not counted
    md = __import__("re").compile(r"^md[01][0-9][0-3][0-9]$")
    head_ok = all(sum(1 for t in d_off[s] if not md.match(t)) <= off.MAX_DOC_TOKENS + off.MAX_ATTACHMENT_TOKENS
                  for s in d_off)
    check("switch off: every document is the memory's own text within its budgets", head_ok and len(d_off) > 1000,
          "%d documents" % len(d_off))
    nodates = load("bm25_nodates", {"BRAIN_DOC_EXPANSION": "0", "BRAIN_TOKENS_DATES": "0"})
    os.environ.pop("BRAIN_TOKENS_DATES", None)
    d_nd = nodates.read_docs()
    check("dates off: no date token in any document, and the rest of each document unchanged",
          all(not any(md.match(t) for t in d_nd[s]) for s in d_nd)
          and all([t for t in d_off[s] if not md.match(t)] == d_nd[s] for s in d_nd if s in d_off),
          "%d documents" % len(d_nd))
    bad = 0
    n_exp = 0
    for s, tks in d_off.items():
        q = exp.get(s)
        want = tks + (on.tokens(" ".join(q[0]))[:on.MAX_EXPANSION_TOKENS] if q else [])
        n_exp += bool(q)
        if d_on.get(s) != want:
            bad += 1
    check("switch on: document = own text + its questions' tokens, nothing else",
          bad == 0 and set(d_on) == set(d_off), "%d expanded of %d, %d differ" % (n_exp, len(d_off), bad))
    orphans = [s for s in exp if s not in d_off]
    check("a question for a missing memory adds no document", all(s not in d_on for s in orphans),
          "%d orphan entries" % len(orphans))
    tmp = tempfile.mkdtemp(prefix="docexp_")
    try:
        idx = os.path.join(tmp, "idx.json.gz")
        since0 = time.time() - 5
        on._publish_payload({"n": 1, "avgdl": 1.0, "df": {}, "dl": {}, "tf": {}}, idx, on._index_signature(idx),
                            since0, 0, 0)
        base_fresh = set(on.fresh_memories(on.MEM, idx)[0])
        picks = sorted(set(d_off) - base_fresh)[:2]
        st2 = os.path.join(tmp, "exp.jsonl")
        with open(st2, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"slug": picks[0], "q": ["an old question"], "t": since0 - 100}) + "\n")
            fh.write(json.dumps({"slug": picks[1], "q": ["a new question"], "t": time.time()}) + "\n")
            fh.write(json.dumps({"slug": "no_such_memory_xyz", "q": ["x"], "t": time.time()}) + "\n")
        on.EXPANSION_FILE, off.EXPANSION_FILE = st2, st2
        added_on = set(on.fresh_memories(on.MEM, idx)[0]) - base_fresh
        added_off = set(off.fresh_memories(off.MEM, idx)[0]) - base_fresh
        check("fresh_memories: only the memory whose questions came after the scan", added_on == {picks[1]},
              "added %d" % len(added_on))
        check("switch off: fresh_memories ignores the store", not added_off)
    finally:
        on.EXPANSION_FILE = off.EXPANSION_FILE = store
        shutil.rmtree(tmp, ignore_errors=True)
    if "--live-index" in sys.argv:
        z = on._load_payload(on.INDEX)
        sample = random.Random(26).sample(sorted(set(exp) & set(z["tf"])), 50)
        miss = sum(1 for s in sample if not all(t in z["tf"][s] for t in on.tokens(" ".join(exp[s][0]))[:5]))
        check("the published index carries the questions of 50 expanded memories", miss == 0, "%d of 50 missing" % miss)
    print("RESULT %d/%d (%.0f s)" % (sum(results), len(results), time.time() - t0))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
