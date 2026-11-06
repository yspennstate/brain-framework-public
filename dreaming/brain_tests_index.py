"""Build the index of brain tests: every folder under ai-memories 12_cognitive_architecture/brain_improvement/, newest
first, with the memories that name it (status, slug, the result line from the description). Writes
~/brain/online/BRAIN_TESTS_INDEX.md; with --publish (the download job, online_memory_pull.py, passes it) also puts it on ai-memories main as
brain_improvement/TESTS_INDEX.md through the contents API (one writer: this Mac)."""

import sys
if __name__ == "__main__" and any(arg in ("-h", "--help") for arg in sys.argv[1:]):
    print(__doc__ or "No command-line options.")
    raise SystemExit(0)

import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import time

H = os.path.expanduser("~")
REPO_DIR = os.path.join(H, "brain", "ai-memories-and-functionality")
MEM = os.path.join(H, ".claude", "projects", "{{BRAIN_SLUG}}", "memory")
BASE = "12_cognitive_architecture/brain_improvement"
OUT = os.path.join(H, "brain", "online", "BRAIN_TESTS_INDEX.md")
DATE = re.compile(r"(20\d\d)[-_](\d\d)[-_](\d\d)")
CACHE = os.path.join(H, "brain", "online", ".tests_index_cache.json")


# 3 Nov: the strongest brain-vs-no-brain result (SC2, 12 of 14 vs 4 of 14) and the 29 Oct recall A/Bs were pushed
# under audits/, not brain_improvement/, so this page never listed them. Test-named folders there count too.
AUDITS = "12_cognitive_architecture/audits"
AUDIT_TESTS = re.compile(r"recall|scar_avoid|_ab\b|_ab_|hop\d|xaware|^tests_")
# compaction node anchors (compaction_claude_<id>_<time>, compaction_codex_<id>_<time>_<hash>), 16k of them. 3 Nov: a
# bare "compaction_" prefix also dropped 14 real memories, among them the one naming compaction_coverage_2026_11_03
ANCHOR = re.compile(r"compaction_(claude|codex)_[0-9a-f]{8}_([0-9a-f]+_)?20\d{6}T\d{6}")
PARENT = {}   # folder name -> the base it sits under on origin/main
# 3 Nov 15:0x: the hop2/hop3 graph tests (hop3: graph puts gold in top 8 on 27 of 150 vs 14, p 0.001) went to
# brain_testing/, which this page never read; every folder there is a test.
TESTING = "12_cognitive_architecture/brain_testing"
PREFIX = {BASE: "", AUDITS: "audits/", TESTING: "brain_testing/"}
# 6 Nov: brain_improvement/ALL_TESTS holds the manifest of every test (README, MANIFEST.tsv, LATEST_TEN.md, GAPS.md)
# and links this index; it is not a test run
MANIFEST_DIRS = {"ALL_TESTS"}


def folders():
    for base in (AUDITS, TESTING, BASE):   # brain_improvement/ wins a name both hold
        r = subprocess.run(["git", "-C", REPO_DIR, "ls-tree", "-d", "--name-only", "origin/main", base + "/"],
                           capture_output=True, text=True, check=True)
        for l in r.stdout.split():
            n = l.rsplit("/", 1)[1]
            if base == BASE and n in MANIFEST_DIRS:
                continue
            if base != AUDITS or AUDIT_TESTS.search(n):
                PARENT[n] = base
    return list(PARENT)


def under(path):
    """(folder, rest) for a path inside an indexed folder, else None."""
    for base in (BASE, TESTING, AUDITS):
        if path.startswith(base + "/"):
            parts = path[len(base) + 1:].split("/", 1)
            if PARENT.get(parts[0]) == base:
                return parts[0], (parts[1] if len(parts) > 1 else "")
    return None


def front(text):
    m = re.search(r"^description:\s*(.*)$", text, re.M)
    return (m.group(1).strip().strip("'\"") if m else "")


MATH_DIR = os.path.join(H, "brain", "math-research-brain")
MATH_SHARED = "scheduled_tasks/work/shared/"


def online_blobs():
    """{blob sha: folder} for every file under brain_improvement/ on origin/main, plus the run folders under the math
    repo's scheduled_tasks/work/shared/ (as "math-research-brain/<folder>"); tree listings only, no blob fetch."""
    r = subprocess.run(["git", "-C", REPO_DIR, "ls-tree", "-r", "origin/main", BASE + "/", AUDITS + "/",
                        TESTING + "/"],
                       capture_output=True, text=True)
    out = {}
    for line in r.stdout.splitlines():
        meta, path = line.split("\t", 1)
        u = under(path)
        if u and u[1]:
            out[meta.split()[2]] = u[0]
    # 3 Nov: the 29 Oct Bitcoin wallet runs (~/codex_fleet/bigrun) went to math-research-brain, not here, and the
    # page still listed them as missing online. Listing shared/ takes ~0.7 s (59k files).
    r = subprocess.run(["git", "-C", MATH_DIR, "ls-tree", "-r", "origin/main", MATH_SHARED], capture_output=True,
                       text=True)
    for line in r.stdout.splitlines():
        meta, path = line.split("\t", 1)
        out.setdefault(meta.split()[2], "math-research-brain/" + path.rsplit("/", 2)[0])
    return out


def headlines(names):
    """{folder: (title, label + result line)} from the top-level write-ups of each folder on origin/main: the title of
    its README (or of the first other write-up), and the first scored sentence of a results file, of a README section
    headed Result, or failing those of any write-up. One ls-tree and one cat-file call for all folders."""
    # 3 Nov: 37 of 88 folders had a README or RESULTS.md; others carry RESULT.md, REPORT.md, MEMORY_BODY.md and such
    r = subprocess.run(["git", "-C", REPO_DIR, "ls-tree", "-r", "--name-only", "origin/main", BASE + "/", AUDITS + "/",
                        TESTING + "/"],
                       capture_output=True, text=True)
    docs = {}
    # 3 Nov: 8 of the 60 folders with no line keep their write-up one level down (results/REPORT.md); those count as
    # a fallback, the first six of a folder, report- and result-named first
    rank = lambda f: (f.count("/"), f != "README.md", not f.rsplit("/", 1)[-1].upper().startswith(("RESULT", "REPORT")), f)
    for path in r.stdout.splitlines():
        u = under(path)
        parts = ([u[0]] + u[1].split("/")) if u else []
        if len(parts) in (2, 3) and re.match(r"(?i)(readme|results?|report|summary|findings|memory_body)\w*\.md$",
                                              parts[-1]):
            docs.setdefault(parts[0], []).append("/".join(parts[1:]))
    for n in docs:
        docs[n] = sorted(docs[n], key=rank)
        docs[n] = [f for f in docs[n] if "/" not in f] + [f for f in docs[n] if "/" in f][:6]
    want = [(n, f) for n in names for f in docs.get(n, [])]
    r = subprocess.run(["git", "-C", REPO_DIR, "cat-file", "--batch"], input="".join("origin/main:%s/%s/%s\n" % (PARENT[n], n, f) for n, f in want).encode(),
                       capture_output=True)
    texts, buf, i = {}, r.stdout, 0
    for n, f in want:
        nl = buf.index(b"\n", i)
        head = buf[i:nl].split()
        i = nl + 1
        if len(head) == 3 and head[1] == b"blob":
            size = int(head[2])
            texts.setdefault(n, {})[f] = buf[i:i + size].decode("utf-8", "replace")
            i += size + 1
    # the first sentence that reports a score; a README's first sentence is nearly always the setup
    scored = re.compile(r"\b\d+\s*/\s*\d+\b|\b\d+ (of|vs\.?|against) \d+\b|\bp\s*[=<]?\s*0?\.\d|hit@\d|\b\d+(\.\d+)?\s*%"
                        r"|\d\s*->\s*\d")
    def first_sentence(body):
        for para in re.split(r"\n\s*\n", body):
            rows = [l.strip() for l in para.splitlines() if l.strip().startswith("|")]
            if len(rows) >= 3:  # a table: its total or overall row, cell by cell under the header's names
                cells = [[c.strip(" *") for c in r.strip("|").split("|")] for r in rows]
                for r in cells[2:]:
                    if re.match(r"(?i)(total|overall|all\b|pooled)", r[0]) and scored.search(" ".join(r[1:])):
                        s = "%s: %s" % (r[0], ", ".join("%s %s" % (h, c) if h else c
                                                        for h, c in zip(cells[0][1:], r[1:]) if c))
                        return s[:300] + ("..." if len(s) > 300 else "")
                continue
            para = " ".join(l.strip() for l in para.splitlines()).strip()
            if not para or para.startswith(("#", "|", "```")):
                continue
            for s in re.split(r"(?<=[.!?])\s+(?=[A-Z`*(])", para.lstrip("- ")):
                if scored.search(s) and not s.startswith(("Why", "See ", "Question", "Bank")):
                    return s[:300] + ("..." if len(s) > 300 else "")
        return ""
    out = {}
    for n in names:
        t = texts.get(n, {})
        readme = t.get("README.md", "")
        order = sorted(t, key=rank)
        title = ""
        for f in [f for f in order if "/" not in f]:  # a subfolder's title names a part, not the test
            m = re.match(r"\s*#\s+(.+)", t[f])
            if m:
                title = m.group(1).strip()
                break
        m = re.search(r"^#+\s*(results?|headline|findings?|verdict|outcome)\b[^\n]*\n(.*?)(?=^#|\Z)", readme,
                      re.M | re.S | re.I)
        result = ""
        for f in [f for f in order if f.upper().startswith("RESULT") and "/" not in f]:
            result = result or first_sentence(t[f])
        result = result or (m and first_sentence(m.group(2))) or ""
        result = ("Result: " + result) if result else ""
        for f in order:  # a prereg, report or note: a scored line, but maybe not this run's headline result
            if not result and first_sentence(t[f]):
                result = "From %s: %s" % (f, first_sentence(t[f]))
        if title or result:
            out[n] = (title, result)
    return out


def carried_online(p, blobs, limit=60):
    """The online folder holding p's files byte for byte, or None: two matches among the files of p and its direct
    subfolders (one, if only one file was read), so one shared engine source copied into many runs does not count.

    3 Nov: names alone missed runs pushed under another name (fullbrain_r4 is online as fullbrain_mac_2026_11_02)."""
    files = []
    for e in sorted(os.scandir(p), key=lambda e: e.name):
        try:
            if e.is_dir(follow_symlinks=False) and not e.name.startswith((".", "_")):
                files += [s for s in sorted(os.scandir(e.path), key=lambda s: s.name)]
            else:
                files.append(e)
        except OSError:
            continue
    seen, hits = 0, []
    for e in files:
        try:
            if not e.is_file(follow_symlinks=False) or not 200 <= e.stat().st_size <= 2 << 20:
                continue
            data = open(e.path, "rb").read()
        except OSError:
            continue
        seen += 1
        sha = hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()
        if sha in blobs:
            hits.append(blobs[sha])
            if len(hits) >= 2:
                break
        if seen >= limit:
            break
    return hits[0] if len(hits) >= min(2, seen) and hits else None


def local_runs(online):
    """Test run folders on this Mac that no folder under brain_improvement/ carries: {name: (path, entries, last touched)}.

    3 Nov: the owner found that big tests run here never reached the online brain, so agents elsewhere and the
    grant writers could not use them. Seven Mac runs (recall_join, recall_speed, bigrun and others) had memories but
    no rows or scripts online; this lists such runs on the index page until a folder for them lands."""
    out, blobs = {}, online_blobs()
    roots = [os.path.join(H, "codex_fleet", d) for d in sorted(os.listdir(os.path.join(H, "codex_fleet")))
             ] if os.path.isdir(os.path.join(H, "codex_fleet")) else []
    roots += [os.path.join(H, d) for d in sorted(os.listdir(H)) if d.startswith("recall_ab")]
    for p in roots:
        b = os.path.basename(p)
        if (not os.path.isdir(p) or b.startswith(("_", ".")) or b in LOCAL_SKIP or re.fullmatch(r"\d{8}", b)
                or is_clone(p)
                or any(n == b or n.startswith(b + "_") for n in online)):
            continue
        where = carried_online(p, blobs)
        if where:
            if where.startswith("math-research-brain/"):
                ELSEWHERE[b] = where
            continue
        # top level only: a walk of recall_mass alone is 6 GB of files, and the pull runs this every 15 minutes
        newest, n = 0, 0
        for e in os.scandir(p):
            n += 1
            try:
                newest = max(newest, e.stat(follow_symlinks=False).st_mtime)
            except OSError:
                pass
        out[b] = ("~" + p[len(H):], n, time.strftime("%d %b", time.localtime(newest)) if newest else "-")
    return out


ELSEWHERE = {}   # Mac run -> the math-repo folder that carries it, filled by local_runs
LOCAL_SKIP = {"keys_private", "movies", "pub"}   # keys, film work, the publishing clone: not test runs
QUICK, PARTIAL = True, False   # first pass of a run may be quick (see main); PARTIAL says it was


def is_clone(p):
    """A git clone, or a folder holding only clones: its content is already on GitHub (pub_recall is an ai-memories
    clone, openprob holds math-open-problems), so it is not a run missing online."""
    if os.path.exists(os.path.join(p, ".git")):
        return True
    subs = [e for e in os.scandir(p) if e.name not in ("__pycache__", ".DS_Store")]
    return bool(subs) and all(e.is_dir() and os.path.exists(os.path.join(e.path, ".git")) for e in subs)


def main():
    t0 = time.time()
    names = folders()
    local = local_runs(names)
    # 3 Nov: a run pushed as <run>_<date> (recall_warm_2026_11_03) had 50 memories naming ~/codex_fleet/recall_warm
    # and none naming the folder, so the page called it unrecallable. Such a folder falls back on its run's name.
    bases = {}
    for n in names:
        b = re.sub(r"_20\d\d[-_]\d\d[-_]\d\d$", "", n)
        if b != n and len(b) >= 6 and b not in names:
            bases[n] = b
    terms = set(names) | set(local) | set(bases.values())
    pat = re.compile("|".join(re.escape(n) for n in sorted(terms, key=len, reverse=True)))
    hits = {n: [] for n in terms}
    # 3 Nov: reading all ~44k memory files took 221 s a pass on the loaded Mac (a grep over them took 209 s, so the
    # cost is the reads). A file is read again only when its mtime or size moved. A new folder name, or a week gone,
    # reads them all again (~16 s): the memory files' mtimes are all recent, so a by-date cut would not save reads.
    try:
        cache = json.load(open(CACHE))
    except (OSError, ValueError):
        cache = {}
    full = time.time() - cache.get("full_at", 0) > 7 * 86400
    files = {} if full else cache.get("files", {})
    added = set(terms)
    added -= set(cache.get("names", []))
    # 3 Nov: a new folder name made the scan reread all ~27k memory files, 608 s under swap, so a pushed test sat
    # off the page ten minutes. The quick pass looks for new names only in files touched in the last two days,
    # publishes, and leaves the names out of the cache so the full pass right after reads everything.
    global PARTIAL
    PARTIAL = bool(QUICK and added and not full)
    cut = time.time() - 2 * 86400
    read = 0
    seen = {}
    for e in os.scandir(MEM):
        fn = e.name
        if not fn.endswith(".md") or ANCHOR.match(fn) or fn in ("MEMORY.md", "INDEX_ARCHIVE.md"):
            continue
        try:
            st = e.stat()
        except OSError:
            continue
        old = files.get(fn)
        if old and old[0] == st.st_mtime_ns and old[1] == st.st_size and (
                not added or (PARTIAL and st.st_mtime < cut)):
            seen[fn] = old
            continue
        try:
            text = open(e.path, errors="replace").read()
        except OSError:
            continue
        read += 1
        found = sorted(set(pat.findall(text)))
        seen[fn] = [st.st_mtime_ns, st.st_size, front(text) if found else "", found]
    for fn, (_, _, d, found) in seen.items():
        for n in found:
            if n in hits:
                hits[n].append((fn[:-3], d))
    try:
        tmp = CACHE + ".tmp"
        json.dump({"full_at": time.time() if full else cache["full_at"],
                   "names": sorted(terms - added if PARTIAL else terms), "files": seen},
                  open(tmp, "w"))
        os.replace(tmp, CACHE)
    except (OSError, KeyError):
        pass

    for n in names:  # a memory on <test>_results covers <test>
        if not hits[n]:
            hits[n] = [h for m in names if m.startswith(n + "_") for h in hits[m]]
        if not hits[n] and n in bases:
            hits[n] = list(hits[bases[n]])

    def key(n):
        m = DATE.search(n)
        return m.groups() if m else ("0", "0", "0")

    names.sort(key=key, reverse=True)
    bare = [n for n in names if not hits[n]]
    lines = ["# Brain tests: what ran and what it found", "",
             "Generated on the Mac mini %s from the test folders under `%s/` (and the test-named ones under "
             "`audits/`, and every folder under `brain_testing/`) on ai-memories main and the memories "
             "that name them. Newest first. A folder with no memory is a test the brain cannot recall, so agents on "
             "other computers and grant writers will not find it: if it is yours, file a memory that names the folder "
             "and the graph the test used. %d folders, %d with no memory." % (time.strftime("%d %b %Y %H:%M %z"),
                                                                             BASE, len(names), len(bare)), ""]
    # 3 Nov: the page listed memory slugs only, so a reader had to open each memory to learn what a test found
    heads = headlines(names)
    for n in names:
        lines.append("## %s" % (PREFIX[PARENT[n]] + n))
        title, result = heads.get(n, ("", ""))
        if title:
            lines.append("*%s*" % title.replace(H, "~"))
        if result:
            lines.append("")
            lines.append(result.replace(H, "~"))
            lines.append("")
        if not hits[n]:
            lines.append("- no memory names this folder yet")
        for slug, d in sorted(hits[n], key=lambda x: (-("VERIFIED" in x[1]), x[0]))[:4]:
            d = d.replace(H, "~")
            lines.append("- `%s`: %s" % (slug, d[:300] + ("..." if len(d) > 300 else "")))
        if len(hits[n]) > 4:
            lines.append("- and %d more memories" % (len(hits[n]) - 4))
        lines.append("")
    if local:
        lines += ["# Test runs on the Mac mini with no folder above", "",
                  "These ran here and have no rows or scripts under `%s/` yet, so other computers see at most the "
                  "memories that name them. The owner of each run should push its questions, scoring rule, graded rows "
                  "and README (the graph used and its counts) into a folder named for it." % BASE, ""]
        for b, (p, n, newest) in sorted(local.items(), key=lambda x: -len(hits[x[0]])):
            lines.append("- `%s` (%s, %d entries, last touched %s): %d memories name it%s" % (
                b, p, n, newest, len(hits[b]), (", e.g. `%s`" % sorted(hits[b])[0][0]) if hits[b] else ""))
        lines.append("")
    unscored = [n for n in names if not heads.get(n, ("", ""))[1]]
    if unscored:
        lines += ["# Folders with no result line", "",
                  "No top-level README, RESULT, REPORT, SUMMARY or FINDINGS file in these states a score, so a reader "
                  "has to open the memories to learn what the test found. If one is yours, add a README.md whose "
                  "result section says what was measured, on which graph, and the paired numbers. %d folders." %
                  len(unscored), ""]
        lines += ["- `%s`" % n for n in unscored]
        lines.append("")
    if ELSEWHERE:
        lines += ["# Mac mini runs carried in math-research-brain", ""]
        lines += ["- `%s`: `%s/`" % (b, ELSEWHERE[b]) for b in sorted(ELSEWHERE)]
        lines.append("")
    page = "\n".join(lines)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    open(OUT, "w").write(page)
    print("index: %d folders, %d without memory, %d files read%s, %.1f s" % (
        len(names), len(bare), read, " (full)" if full else "", time.time() - t0))
    # publish only when something besides the clock changed; the pull calls this every 15 minutes
    stamp = os.path.join(os.path.dirname(OUT), ".tests_index.sha")
    digest = hashlib.sha256(re.sub(r"Generated on the Mac mini .*? from the test", "", page).encode()).hexdigest()
    try:
        last = open(stamp).read().strip()
    except OSError:
        last = ""
    if "--publish" in sys.argv and digest == last:
        print("tests index unchanged, not published")
        return
    if "--publish" in sys.argv:
        if re.search(r"ghp_|github_pat_|sk-[A-Za-z0-9]{20}|AKIA|BEGIN .*PRIVATE|/Users/", page):
            sys.exit("refused: the page holds a secret-shaped string or a home path")
        spec = importlib.util.spec_from_file_location("fleet_board", os.path.join(H, ".claude", "dreaming",
                                                                                   "fleet_board.py"))
        fb = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fb)
        repo, dst = "yspennstate/ai-memories-and-functionality", BASE + "/TESTS_INDEX.md"
        ok = fb.put_file(repo, dst, lambda old: page, "brain tests index (auto) [skip ci]")
        remote, _ = fb.get_file(repo, dst)
        print("published %s, remote matches %s" % (ok, remote == page))
        if ok and remote == page:
            open(stamp, "w").write(digest)
        sys.exit(0 if ok and remote == page else 1)


if __name__ == "__main__":
    # 3 Nov: the pull starts this twice (right after the memory fetch and again at its end). A run that finds the
    # other still going leaves a note and exits; the running one sees the note when it finishes and scans once
    # more, so the end-of-pull run (the one that sees memories the pull brought in) is never lost under swap.
    import fcntl
    again = os.path.join(os.path.dirname(OUT), ".tests_index.again")
    lock = open(os.path.join(os.path.dirname(OUT), ".tests_index.lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        open(again, "w").close()
        sys.exit("tests index already running; it will scan again when done")
    code = 0
    for _ in range(3):
        if os.path.exists(again):
            os.remove(again)
        try:            # main exits after a publish; the rescan below must still run
            main()
            code = 0
        except SystemExit as e:
            code = e.code
        if PARTIAL:
            QUICK = False
            print("quick pass published; full pass for the new names")
        elif not os.path.exists(again):
            break
        else:
            print("another run asked while this one ran; scanning again")
    sys.exit(code)
