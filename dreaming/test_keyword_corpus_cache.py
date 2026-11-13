"""test_keyword_corpus_cache.py - the incremental keyword inputs equal a full read, run after run.

    python framework/dreaming/test_keyword_corpus_cache.py

Builds a small memory folder (with a dreams/ subfolder) and a PROMPTS.jsonl in a temporary directory, points
keyword_corpus_cache at a cache there, and after each change compares corpus() and prompt_terms() with a full
read written the way build_keyword_nodes reads them: first run; new, edited and deleted memories; a slug in both
folders; a prompt line with no newline yet, then completed; a rewritten prompt log (the tail hash must force a
full re-read); a corrupt cache file and a lost cache file (both must reset, never drift). Nothing outside the
temporary directory is read or written. Takes about a second.
"""
if "-h" in __import__("sys").argv[1:] or "--help" in __import__("sys").argv[1:]:
    print((__doc__ or "").strip() or "usage: python %s" % __import__("os").path.basename(__file__))
    raise SystemExit(0)
import io, json, os, re, shutil, sys, tempfile, time
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import keyword_corpus_cache as kcc

TOKEN = re.compile(r"[a-zA-Z][a-zA-Z0-9_]{2,}")   # the same pattern build_keyword_nodes uses
SKIP = {"MEMORY"}
WORK = tempfile.mkdtemp(prefix="kcc_test_")
MEM = os.path.join(WORK, "memory")
DREAMS = os.path.join(MEM, "dreams")
PROMPTS = os.path.join(WORK, "PROMPTS.jsonl")
kcc.CACHE = os.path.join(WORK, "cache")
os.makedirs(DREAMS)
res = []


def ok(name, cond, extra=""):
    res.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name + (" " + extra if extra else ""), flush=True)


def full_corpus():
    texts, vocab = {}, Counter()
    for base in (MEM, DREAMS):
        for name in os.listdir(base):
            if not name.endswith(".md") or name[:-3] in SKIP:
                continue
            body = io.open(os.path.join(base, name), encoding="utf-8", errors="replace").read(4000)
            m = re.search(r'description:\s*"?(.+?)"?\s*\n', body)
            texts[name[:-3]] = (name[:-3] + " " + (m.group(1) if m else "")).lower()
            vocab.update(t.lower() for t in TOKEN.findall(body))
    return texts, vocab


def full_prompts():
    counts, read_any = Counter(), False
    with io.open(PROMPTS, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                row = json.loads(line)
            except Exception:
                continue
            text = row.get("text") or row.get("prompt") or ""
            if isinstance(text, str) and text:
                read_any = True
                counts.update(t.lower() for t in TOKEN.findall(text))
    return counts, read_any


def memory(base, slug, desc, body):
    path = os.path.join(base, slug + ".md")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write('---\nname: %s\ndescription: "%s"\n---\n\n%s\n' % (slug, desc, body))
    st = os.stat(path)                     # a fresh mtime even on a coarse clock
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))


def prompts(rows, mode="a", newline=True):
    with open(PROMPTS, mode, encoding="utf-8", newline="\n") as f:
        f.write("\n".join(json.dumps(r) for r in rows) + ("\n" if newline else ""))


def check(name, want_mode=None):
    texts, vocab, cst = kcc.corpus((MEM, DREAMS), SKIP, TOKEN)
    ft, fv = full_corpus()
    ok(name + ": corpus texts", texts == ft, "%d vs %d" % (len(texts), len(ft)))
    ok(name + ": corpus vocabulary", vocab == fv)
    counts, read_any, pst = kcc.prompt_terms(PROMPTS, TOKEN)
    fc, fa = full_prompts()
    ok(name + ": prompt terms", counts == fc and read_any == fa, "mode=%s" % pst.get("mode"))
    if want_mode:
        ok(name + ": prompt read mode is " + want_mode, pst.get("mode") == want_mode, str(pst))
    return cst, pst


try:
    for i in range(12):
        memory(MEM, "note_%02d" % i, "recall speed note %d" % i, "The spreading walk visits node%d and edge%d." % (i, i))
    memory(MEM, "MEMORY", "index", "skipped by name")
    memory(DREAMS, "dream_01", "a dream journal", "consolidation replay salience")
    prompts([{"text": "make recall faster please"}, {"prompt": "check the salience weights"}, {"text": ""}])
    cst, _ = check("first run", "full")
    ok("first run reads every file", cst["added"] == 13, str(cst))

    cst, _ = check("no change", "append")
    ok("no change reads no file", cst["added"] == cst["changed"] == cst["removed"] == 0, str(cst))

    memory(MEM, "note_03", "recall speed note three, rewritten", "Now about the resident server and its endpoint.")
    os.remove(os.path.join(MEM, "note_07.md"))
    memory(MEM, "note_new", "a new card", "keyword aliases and their review")
    memory(DREAMS, "note_05", "the dream copy of note five", "a slug that lives in both folders")
    prompts([{"text": "where is the keyword layer"}, {"text": "half written line"}], newline=False)
    cst, _ = check("edit, delete, add, partial prompt line", "append")
    ok("only the changed files are read", (cst["added"], cst["changed"], cst["removed"]) == (2, 1, 1), str(cst))

    with open(PROMPTS, "a", encoding="utf-8", newline="\n") as f:
        f.write("\n" + json.dumps({"text": "the line after it"}) + "\n")
    check("partial line completed (counted once)", "append")

    prompts([{"text": "a different log entirely, longer than before " * 3}] * 4, mode="w")
    check("prompt log rewritten", "full")

    with open(os.path.join(kcc.CACHE, "agg.pickle"), "wb") as f:
        f.write(b"not a pickle")
    check("corrupt cache file", "full")

    os.remove(os.path.join(kcc.CACHE, "agg.pickle"))
    memory(MEM, "note_01", "edited while the cache was lost", "drift would show here")
    check("lost cache file", "full")
finally:
    shutil.rmtree(WORK, ignore_errors=True)

print("RESULT %d/%d" % (sum(res), len(res)))
sys.exit(0 if all(res) else 1)
