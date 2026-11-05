#!/usr/bin/env python3
"""What did we already try on this open problem? Prior work for a problem id or a problem statement, in one call
(claude-4a88bc43-f08, 12 Oct 2026, front F08 of the brain-improvement run).

    python ~/.claude/dreaming/problem_recall.py P0072              prior work for one problem id (any intake: P C D E F G H I)
    python ~/.claude/dreaming/problem_recall.py "<statement words>" find the problem by its statement, then its prior work
    python ~/.claude/dreaming/problem_recall.py P0072 --files 12    more work units; --chars N longer attempt text
    python ~/.claude/dreaming/problem_recall.py --refresh           rebuild the index now (about a minute)

What it prints, for each problem: the ledger record (title, source, status from the LATEST ledger row, earlier rows
counted), other ids that look like the same problem, every attempt row of the ledgers (method, result, obstruction,
failed route, verification, artifact), the 10 Oct Codex pool and the 11-12 Oct TRIO run on it (verdicts, claim
counts by status, remaining gaps), the work units (campaign lanes and folders) whose files are about it, ranked so that a
file mostly about this problem counts more than a list that names 200 problems, the math-store cards of the problem and
its attempts with the technique cards they link to (read any of them whole with math_recall.py --id <id>), and the
memories that name it.

Why it exists. OP15 (11 Oct, memory op15_brain_vs_no_brain_open_problems_2026_10_11) found that the brain helps on an open
problem as a MEMORY of our earlier work (it decided P0072), and that an id-pattern search of the store found the earlier
attempt for 2 of the 200 listed problems while the attempt's text found every one checked. Prior work is spread over
two repositories, eight intake ledgers, the store and the memories, under ids that differ between intakes; this tool
reads them all and keys them by problem.

Sources, all read-only: open-problems-repo at origin/master (never the working tree, which may sit on a peer's
branch; the tool does not fetch, so it sees what the last fetch brought), the brain repository's 06_math_research
(the C, D, E, F, G, H, I intake ledgers, the 10 Oct pool, the TRIO run, OP15), the memories, and the math store.
It writes only its own cache (problem_recall_cache/ beside it). It never reads the held-out test folders (see FIREWALL).
What it prints is our own earlier work, with the authors' own labels: "proved" there means the worker's claim,
not a referee's acceptance, unless the row says independent review.
"""
import argparse
import collections
import datetime as _dt
import gzip
import heapq
import json
import math
import os
import re
import sqlite3
import subprocess
import sys
import time

HOME = os.path.expanduser("~")
MOP = os.environ.get("PROBLEM_RECALL_MOP", os.path.expanduser(r"~/open-problems-repo"))
BRAIN = os.environ.get("PROBLEM_RECALL_BRAIN", os.path.expanduser(r"~/brain-repo"))
MATHDIR = "06_math_research"
MEMDIR = os.environ.get("PROBLEM_RECALL_MEMDIR", os.path.join(HOME, ".claude", "projects", "{{BRAIN_SLUG}}", "memory"))
STORE = os.environ.get("MATHBRAIN_STORE", "")
CACHE_DIR = os.environ.get("PROBLEM_RECALL_CACHE", os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                                   "problem_recall_cache"))
CACHE = os.path.join(CACHE_DIR, "index_v1.json.gz")
STALE_SECONDS = 6 * 3600          # rebuild when the cache is older than this (or --refresh)
INDEX_VERSION = 2

# Held-out test material is never opened, listed or indexed (the 12 Oct brief's firewall).
FIREWALL = ("brain_workers", "claude_pool", "pd_work", "test_v1", "test_v2", "test_recall300_brain",
            "test_brainrecall50", "gh_bank", "candidates.jsonl", "hooklive_blocks")
# Copies of open-problems-repo inside the brain repo duplicate origin/master; the pool and TRIO are read structurally.
BRAIN_SKIP_TOP = ("open-problems-repo", "open-problems-repo-p300", "open-problems-repo-agent-a",
                  "open-problems-repo-agent-b", "open-problems-repo-fleet")
# A problem id is a letter and four digits with no letter or digit beside it; an underscore IS a boundary here
# ("__P0072_admission.md"), which \b would miss. In paths the letter may be lower case ("i0120_admission.md").
ID_RE = re.compile(r"(?<![A-Za-z0-9])([CDEFGHIP])(\d{4})(?![0-9])")
PATH_ID_RE = re.compile(r"(?<![A-Za-z0-9])([CDEFGHIPcdefghip])(\d{4})(?![0-9])")
GREP_ID = r"(?<![A-Za-z0-9])[CDEFGHIP][0-9]{4}(?![0-9])"
TEXT_EXT = (".md", ".txt", ".json", ".jsonl", ".tex", ".csv", ".tsv", ".lean", ".py")
STATUS_LINE = r"^\W{0,6}(verdict|status|state|result|outcome|conclusion|closure)\W{0,4}[:\-]\s*\S"
INTAKE_LETTER = {"claude": "C", "D": "D", "E": "E", "F": "F", "G": "G", "H": "H", "I": "I"}
STOP = set("""a an the of on in to for and or is are be by with as at from that this it its their which what when whether
does do can there any every each some all no not if then than into over under between where who how why we our one two
let given show prove open problem conjecture question is are holds hold true false""".split())


def firewalled(path):
    p = path.replace("\\", "/").lower()
    return any(f in p for f in FIREWALL)


def run(args, cwd=None):
    r = subprocess.run(args, cwd=cwd, capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return r.stdout.decode("utf-8", "replace")


def git_mop(*args):
    return run(["git", "--no-optional-locks", "-C", MOP] + list(args))


def git_brain(*args):
    return run(["git", "--no-optional-locks", "-C", BRAIN] + list(args))


def jsonl(text):
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        if isinstance(d, dict):
            out.append(d)
    return out


def read_text(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


GREEK = {"α": "alpha", "β": "beta", "γ": "gamma", "δ": "delta", "ε": "epsilon", "ζ": "zeta", "η": "eta", "θ": "theta",
         "ι": "iota", "κ": "kappa", "λ": "lambda", "μ": "mu", "ν": "nu", "ξ": "xi", "π": "pi", "ρ": "rho", "σ": "sigma",
         "τ": "tau", "υ": "upsilon", "φ": "phi", "χ": "chi", "ψ": "psi", "ω": "omega", "ϕ": "phi", "ϵ": "epsilon"}


_GREEK_TABLE = None


def _ascii(s):
    """Greek letters to their names (the ledgers write 'kappa', a restatement may write the letter), accents and
    super/subscripts folded, so both spellings meet. Plain ASCII passes through; otherwise one str.translate over every
    code point whose lower() is a GREEK key (30 Oct: the per-character loop was 45 s of each rebuild; same output on
    all 85,815 claim cards)."""
    global _GREEK_TABLE
    if s.isascii():
        return s
    import unicodedata
    if _GREEK_TABLE is None:
        _GREEK_TABLE = {cp: " %s " % GREEK[chr(cp).lower()] for cp in range(0x110000) if chr(cp).lower() in GREEK}
    return unicodedata.normalize("NFKD", s.translate(_GREEK_TABLE)).encode("ascii", "ignore").decode("ascii")


def words(s):
    out = []
    for w in re.findall(r"[a-z0-9]+", _ascii(s or "").lower()):
        if len(w) < 2 or w in STOP:
            continue
        if len(w) > 4 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        out.append(w)
    return out


def short(s, n):
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[: n - 3].rstrip() + "..."


def date_of(s):
    m = re.search(r"(20\d\d)-?(\d\d)-?(\d\d)", s or "")
    return "%s-%s-%s" % m.groups() if m else ""


# ----------------------------------------------------------------------------------------------------------- build
def build_ledgers(mop_sha):
    probs = collections.defaultdict(list)   # id -> rows (ledger order)
    atts = collections.defaultdict(list)
    p_path = "probability_campaign_2026_09/codex_20h_20260906"
    for r in jsonl(git_mop("show", "%s:%s/problems.jsonl" % (mop_sha, p_path))):
        if r.get("problem_id"):
            r["_ledger"] = "open-problems-repo:%s/problems.jsonl" % p_path
            probs[r["problem_id"]].append(r)
    for r in jsonl(git_mop("show", "%s:%s/attempts.jsonl" % (mop_sha, p_path))):
        if r.get("problem_id"):
            r["_ledger"] = "open-problems-repo:%s/attempts.jsonl" % p_path
            atts[r["problem_id"]].append(r)
    base = os.path.join(BRAIN, MATHDIR)
    for d in sorted(os.listdir(base)):
        m = re.match(r"probability_1000_intake_([A-Za-z]+)_\d{8}$", d)
        if not m:
            continue
        for kind, sink in (("problems", probs), ("attempts", atts)):
            p = os.path.join(base, d, kind + ".jsonl")
            if os.path.exists(p):
                for r in jsonl(read_text(p)):
                    if r.get("problem_id"):
                        r["_ledger"] = "brain:%s/%s/%s.jsonl" % (MATHDIR, d, kind)
                        sink[r["problem_id"]].append(r)
    op200 = {}
    for r in jsonl(git_mop("show", "%s:OPEN_PROBLEMS_200/list.jsonl" % mop_sha)):
        if r.get("problem_id"):
            op200[r["problem_id"]] = r
    return probs, atts, op200


def build_pool():
    """10 Oct Codex pool: wave300/<id>/<worker>/CLAIMS.jsonl and team_O workers' problem lists (brain repo copy)."""
    base = os.path.join(BRAIN, MATHDIR, "codex_pool_2026_10_10")
    pool = collections.defaultdict(lambda: {"workers": [], "claims": []})
    wave = os.path.join(base, "wave300")
    if os.path.isdir(wave):
        for pid in sorted(os.listdir(wave)):
            pdir = os.path.join(wave, pid)
            if not ID_RE.fullmatch(pid) or not os.path.isdir(pdir):
                continue
            for w in sorted(os.listdir(pdir)):
                wdir = os.path.join(pdir, w)
                if not os.path.isdir(wdir) or firewalled(wdir):
                    continue
                st = read_text(os.path.join(wdir, "STATUS.md"))
                pool[pid]["workers"].append({"worker": w, "status": short(st.splitlines()[0] if st else "", 160),
                                             "path": "brain:%s/codex_pool_2026_10_10/wave300/%s/%s" % (MATHDIR, pid, w)})
                for r in jsonl(read_text(os.path.join(wdir, "CLAIMS.jsonl"))):
                    pool[pid]["claims"].append({"worker": w.split("_")[0], "status": r.get("status", ""),
                                                "type": r.get("record_type", ""),
                                                "statement": short(r.get("statement") or r.get("claim"), 400),
                                                "gap": short(r.get("remaining_gap"), 300),
                                                "reviewer": short(r.get("independent_reviewer"), 80),
                                                "proof": str(r.get("mathematical_proof", ""))})
    led = os.path.join(base, "PROBLEM_LEDGER.json")
    try:
        d = json.load(open(led, encoding="utf-8"))
    except Exception:
        d = {}
    for w in d.get("team_O", []) or []:
        for pid in w.get("problems", []) or []:
            pool[pid]["workers"].append({"worker": w.get("worker", ""), "status": "team O: " + short(w.get("state"), 40),
                                         "report": short(w.get("report"), 300),
                                         "path": "brain:%s/codex_pool_2026_10_10/team_O/%s" % (MATHDIR, w.get("worker", ""))})
    return dict(pool)


def build_trio():
    base = os.path.join(BRAIN, MATHDIR, "trio_2026_10_11")
    trio = {}
    opd = os.path.join(base, "op")
    if not os.path.isdir(opd):
        return trio
    for pid in sorted(os.listdir(opd)):
        if not ID_RE.fullmatch(pid):
            continue
        d = os.path.join(opd, pid)
        st = read_text(os.path.join(d, "STATUS.md"))
        get = lambda key: next((short(l.split(":", 1)[1], 300) for l in st.splitlines() if l.startswith(key + ":")), "")
        claims = [{"status": r.get("status", ""), "claim": short(r.get("claim") or r.get("statement"), 400),
                   "gap": short(r.get("remaining_gap"), 300)} for r in jsonl(read_text(os.path.join(d, "CLAIMS.jsonl")))]
        audits = []
        ad = os.path.join(base, "op_audit", pid)
        if os.path.isdir(ad):
            for a in sorted(os.listdir(ad)):
                t = read_text(os.path.join(ad, a, "AUDIT.md"))
                v = next((short(l, 200) for l in t.splitlines() if re.search(r"(?i)verdict|^status", l)), "")
                audits.append({"auditor": a, "verdict": v})
        last = [l for l in st.splitlines() if re.match(r"20\d\d-\d\d-\d\d:", l)]
        trio[pid] = {"state": get("STATE"), "verdict": get("VERDICT"), "next": get("NEXT"),
                     "last": short(last[-1], 400) if last else "", "claims": claims, "audits": audits,
                     "path": "brain:%s/trio_2026_10_11/op/%s" % (MATHDIR, pid),
                     "check": os.path.exists(os.path.join(d, "CLAUDE_CHECK.md"))}
    return trio


def build_files(mop_sha):
    """Every text file of open-problems-repo origin/master and of the brain repo's 06_math_research (tracked files)
    that names a problem id: per file, the ids it names with counts, and its title."""
    files = {}   # key -> {"src","path","ids":{id:n},"title"}

    def ingest(src, out, prefix_strip):
        for line in out.splitlines():
            parts = line.rsplit(":", 1)
            if len(parts) != 2:
                continue
            path, pid = parts
            if prefix_strip and path.startswith(prefix_strip):
                path = path[len(prefix_strip):]
            if firewalled(path):
                continue
            f = files.setdefault(src + ":" + path, {"src": src, "path": path, "ids": collections.Counter(), "title": ""})
            f["ids"][pid] += 1

    ingest("mop", git_mop("grep", "-I", "-o", "-P", GREP_ID, mop_sha), mop_sha + ":")
    tracked = [p for p in git_brain("ls-files", "--", MATHDIR).splitlines()
               if p.split("/")[1:2] and p.split("/")[1] not in BRAIN_SKIP_TOP and not firewalled(p)]
    tops = sorted(set(p.split("/")[1] for p in tracked if len(p.split("/")) > 2))
    if tops:
        out = git_brain("grep", "-I", "-o", "-P", GREP_ID, "--", *["%s/%s" % (MATHDIR, t) for t in tops])
        ingest("brain", out, "")
    # a file named for a problem is about it even when its text never spells the id the same way
    mop_paths = [l.split("\t", 1)[1] for l in git_mop("ls-tree", "-r", mop_sha).splitlines() if "\t" in l]
    for src, paths in (("mop", mop_paths), ("brain", tracked)):
        for path in paths:
            if firewalled(path) or not path.lower().endswith(TEXT_EXT):
                continue
            pids = set((a.upper() + b) for a, b in PATH_ID_RE.findall(path))
            if not pids:
                continue
            f = files.setdefault(src + ":" + path, {"src": src, "path": path, "ids": collections.Counter(), "title": ""})
            for pid in pids:
                if not f["ids"].get(pid):
                    f["ids"][pid] = 1
    # titles: first markdown heading (one pass each)
    for src, out, strip in (("mop", git_mop("grep", "-I", "-m1", "-E", r"^#{1,3} ", mop_sha, "--", "*.md"), mop_sha + ":"),
                            ("brain", git_brain("grep", "-I", "-m1", "-E", r"^#{1,3} ", "--",
                                                *["%s/%s/*.md" % (MATHDIR, t) for t in tops]) if tops else "", "")):
        for line in out.splitlines():
            m = re.match(r"(.+?\.md):(#{1,3} .*)$", line)
            if not m:
                continue
            path = m.group(1)[len(strip):] if strip and m.group(1).startswith(strip) else m.group(1)
            f = files.get(src + ":" + path)
            if f is not None:
                f["title"] = short(m.group(2).lstrip("# "), 140)
    # the file's own verdict or status line, when it has one ("VERDICT: PARTIAL", "**Status:** proved ...")
    for src, out, strip in (("mop", git_mop("grep", "-I", "-m1", "-i", "-P", STATUS_LINE, mop_sha, "--", "*.md"),
                             mop_sha + ":"),
                            ("brain", git_brain("grep", "-I", "-m1", "-i", "-P", STATUS_LINE, "--",
                                                *["%s/%s/*.md" % (MATHDIR, t) for t in tops]) if tops else "", "")):
        for line in out.splitlines():
            m = re.match(r"(.+?\.md):(.*)$", line)
            if not m:
                continue
            path = m.group(1)[len(strip):] if strip and m.group(1).startswith(strip) else m.group(1)
            f = files.get(src + ":" + path)
            if f is not None:
                f["status"] = short(re.sub(r"[*_`#>|]+", "", m.group(2)).strip(), 200)
    for f in files.values():
        f["ids"] = dict(f["ids"])
    return files


def build_memories():
    mems = collections.defaultdict(list)
    try:
        names = sorted(os.listdir(MEMDIR))
    except OSError:
        return {}
    for n in names:
        if not n.endswith(".md") or n == "MEMORY.md":
            continue
        t = read_text(os.path.join(MEMDIR, n))
        ids = collections.Counter(a + b for a, b in ID_RE.findall(t))
        if not ids:
            continue
        m = re.search(r"^description:\s*(.*)$", t, re.M)
        desc = short((m.group(1) if m else "").strip().strip('"'), 220)
        sup = re.search(r"^\s*superseded_by:\s*(\S+)", t, re.M)
        for pid, c in ids.items():
            mems[pid].append({"slug": n[:-3], "n": c, "nids": len(ids), "desc": desc,
                              "superseded_by": sup.group(1) if sup else ""})
    return dict(mems)


def build_store():
    """Store cards keyed to a problem (bundle_entry_key op-/at-/res-/st-/r-/context-/w###- <id>) and their links."""
    out = collections.defaultdict(list)
    if not os.path.exists(STORE):
        return {}
    con = sqlite3.connect("file:%s?mode=ro" % STORE.replace("\\", "/"), uri=True)
    cur = con.cursor()
    title, keyed = {}, {}
    for nid, kind, ttl, meta in cur.execute("select id, kind, title, metadata_json from nodes"):
        title[nid] = ttl
        try:
            k = json.loads(meta).get("bundle_entry_key", "") or ""
        except Exception:
            k = ""
        m = re.match(r"^(op|at|res|st|r|context)-([a-z])(\d{4})", k) or re.match(r"^(w\d{3})-([a-z])(\d{4})", k)
        if m:
            keyed[nid] = (m.group(1) if not m.group(1).startswith("w") else "pool", (m.group(2) + m.group(3)).upper(), k)
    retracted = set(r[0] for r in cur.execute("select edge_id from edge_retractions"))
    links = collections.defaultdict(list)
    for eid, s, t, kind in cur.execute("select id, source_id, target_id, kind from edges"):
        if eid in retracted:
            continue
        if s in keyed:
            links[s].append(("->", kind, t))
        if t in keyed:
            links[t].append(("<-", kind, s))
    sup = {}
    for eid, s, t, kind in cur.execute("select id, source_id, target_id, kind from edges where kind='supersedes'"):
        if eid not in retracted:
            sup[t] = s
    for nid, (pre, pid, k) in keyed.items():
        techs = []
        for d, kind, other in links.get(nid, []):
            if other in keyed or kind == "supersedes":
                continue
            techs.append({"dir": d, "kind": kind, "id": other, "title": short(title.get(other, ""), 120)})
        out[pid].append({"id": nid, "role": pre, "key": k, "title": short(title.get(nid, ""), 140),
                         "superseded_by": sup.get(nid, ""), "links": techs})
    con.close()
    return dict(out)


def store_text_matches(probs, op200, store, limit=5):
    """Store claim cards found by TEXT for each problem (title + statement against every current claim card's title
    and body), whatever their ids or bundle keys say. A card keyed to this problem is marked so; the rest are cards
    the id scan cannot see (another bundle's attempt, a literature card, a result card written later)."""
    if not os.path.exists(STORE):
        return {}, {}
    con = sqlite3.connect("file:%s?mode=ro" % STORE.replace("\\", "/"), uri=True)
    sup = set(r[0] for r in con.execute(
        "select target_id from edges where kind='supersedes' and id not in (select edge_id from edge_retractions)"))
    docs, title = {}, {}
    for nid, ttl, body in con.execute("select id, title, substr(body, 1, 1500) from nodes where kind='claim'"):
        if nid in sup:
            continue
        title[nid] = ttl
        docs[nid] = words(ttl + " " + ttl + " " + (body or ""))
    con.close()
    bm = Bm25(docs, 1.2, 0.75)
    keyed = {c["id"]: pid for pid, cs in store.items() for c in cs}
    out, calib = {}, collections.Counter()
    for pid in set(probs) | set(op200):
        r = latest(probs.get(pid, [])) or op200.get(pid, {})
        q = words("%s %s %s" % (r.get("title", ""), r.get("title", ""), short(r.get("statement", ""), 600)))
        if not q:
            continue
        # nlargest == sorted(..., reverse)[:limit], ties kept in insertion order (30 Oct: 112 -> 86 ms CPU a query;
        # 20,000 synthetic dicts, 5,778 with ties, all equal)
        sc = heapq.nlargest(limit, bm.scores(q).items(), key=lambda x: x[1])
        if not sc:
            continue
        top = sc[0][1]
        out[pid] = [{"id": i, "ratio": round(s / top, 3), "title": short(title.get(i, ""), 140),
                     "keyed": keyed.get(i, "")} for i, s in sc]
        own = [c["id"] for c in store.get(pid, []) if c["role"] == "op" and c["id"] in docs]
        if own:
            calib["with_own_op_card"] += 1
            calib["own_op_card_ranked_1"] += int(sc[0][0] in own)
            calib["own_op_card_in_top5"] += int(any(i in own for i, _ in sc))
    return out, dict(calib)


def build_index():
    t0 = time.time()
    mop_sha = git_mop("rev-parse", "origin/master").strip()
    if not re.fullmatch(r"[0-9a-f]{40}", mop_sha):
        raise SystemExit("problem_recall: cannot read origin/master of %s" % MOP)
    probs, atts, op200 = build_ledgers(mop_sha)
    store = build_store()
    by_text, calib = store_text_matches(probs, op200, store)
    idx = {"version": INDEX_VERSION, "built_at": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
           "mop_sha": mop_sha, "brain_head": git_brain("rev-parse", "HEAD").strip()[:12],
           "problems": probs, "attempts": atts, "op200": op200, "pool": build_pool(), "trio": build_trio(),
           "files": build_files(mop_sha), "memories": build_memories(), "store": store,
           "store_by_text": by_text, "store_text_calibration": calib}
    idx["build_seconds"] = round(time.time() - t0, 1)
    os.makedirs(CACHE_DIR, exist_ok=True)
    tmp = CACHE + ".tmp"
    with gzip.open(tmp, "wt", encoding="utf-8") as fh:
        json.dump(idx, fh)
    os.replace(tmp, CACHE)
    return idx


REBUILD_LOCK = os.path.join(CACHE_DIR, "rebuild.lock")
REBUILD_LOCK_SECONDS = 2 * 3600    # one background rebuild at a time; a lock older than this is treated as dead


def start_background_rebuild():
    """Rebuild the index in a detached, windowless, normal-priority process; at most one at a time."""
    try:
        if os.path.exists(REBUILD_LOCK) and time.time() - os.path.getmtime(REBUILD_LOCK) < REBUILD_LOCK_SECONDS:
            return False
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(REBUILD_LOCK, "w") as fh:
            fh.write("%d %s\n" % (os.getpid(), _dt.datetime.now().isoformat(timespec="seconds")))
        # NORMAL, named: with no class flag Windows gives the child its parent's class, and agent shells run
        # BelowNormal. A BelowNormal rebuild sat 30 min on 2 cpu-s in the store read on 30 Oct (busy box).
        flags = 0x08000000 | 0x00000200 | 0x00000020  # CREATE_NO_WINDOW | NEW_PROCESS_GROUP | NORMAL_PRIORITY_CLASS
        subprocess.Popen([sys.executable, "-s", os.path.abspath(__file__), "--rebuild-only"],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         close_fds=True, creationflags=flags if os.name == "nt" else 0,
                         start_new_session=os.name != "nt")  # off Windows: its own session, so a closing shell's hangup misses it
        return True
    except Exception:
        return False


def load_index(refresh=False):
    # A stale cache is served at once and rebuilt in the background (30 Oct: under load a rebuild ran past 600 s, so
    # every call after the 6 h mark rebuilt in the foreground, timed out, and the cache was never refreshed).
    # The header line prints when the index was built; --refresh still rebuilds in the foreground.
    if not refresh and os.path.exists(CACHE):
        try:
            age = time.time() - os.path.getmtime(CACHE)
            with gzip.open(CACHE, "rt", encoding="utf-8") as fh:
                idx = json.load(fh)
            if idx.get("version") == INDEX_VERSION:
                if age >= STALE_SECONDS and start_background_rebuild():
                    print("problem_recall: index is %.1f h old; rebuilding in the background" % (age / 3600),
                          file=sys.stderr)
                return idx
        except Exception:
            pass
    return build_index()


# ----------------------------------------------------------------------------------------------------------- search
# Tuned 12 Oct on the tune half of the F08 dev set (restatements of 68 problems by later workers; sha1(id) even):
# the attempts' own text at 0.6 of the statement channel, k1 1.6, b 0.6. See F08/REPORT.md for the check half.
ATTEMPT_WEIGHT = 0.6


class Bm25:
    """Okapi BM25 over an inverted index (each query word counted once)."""

    def __init__(self, docs, k1=1.6, b=0.6):
        self.ids = list(docs)
        self.post = collections.defaultdict(list)
        lens = []
        for i, d in enumerate(self.ids):
            c = collections.Counter(docs[d])
            lens.append(sum(c.values()))
            for w, f in c.items():
                self.post[w].append((i, f))
        self.len = lens
        self.avg = (sum(lens) / len(lens)) if lens else 1.0
        n = len(self.ids)
        self.idf = {w: math.log(1 + (n - len(p) + 0.5) / (len(p) + 0.5)) for w, p in self.post.items()}
        self.k1, self.b = k1, b

    def scores(self, q):
        acc = collections.defaultdict(float)
        k1, b, avg = self.k1, self.b, self.avg
        for w in set(q):
            p = self.post.get(w)
            if not p:
                continue
            idf = self.idf[w]
            for i, f in p:
                acc[i] += idf * f * (k1 + 1) / (f + k1 * (1 - b + b * self.len[i] / avg))
        return {self.ids[i]: s for i, s in acc.items()}


def named_titles(idx, exclude=()):
    """Headings of the files named for a problem (a lane's artifacts, an admission, a claim file): later workers'
    own words for it. Problems with work but no ledger row (I0120 and others admitted after the intake) get a
    document from these alone."""
    out = collections.defaultdict(list)
    for key, f in idx["files"].items():
        t = f.get("title")
        if not t or f["path"] in exclude:
            continue
        up = f["path"].upper()
        for pid in f["ids"]:
            if pid in up:
                out[pid].append(t)
    return out


def problem_docs(idx, titles=True, exclude=()):
    """One document per problem id: titles, statements (every ledger row and the 200-list), dedup key, and the
    headings of the files named for it."""
    docs = {}
    for pid, rows in idx["problems"].items():
        txt = []
        for r in rows:
            txt += [r.get("title", ""), r.get("statement", ""), str(r.get("dedup_key", "")).replace("_", " ")]
        o = idx["op200"].get(pid)
        if o:
            txt += [o.get("title", ""), o.get("statement", "")]
        docs[pid] = words(" ".join(txt))
    for pid, o in idx["op200"].items():
        docs.setdefault(pid, words(o.get("title", "") + " " + o.get("statement", "")))
    if titles:
        for pid, ts in named_titles(idx, exclude).items():
            extra = words(" ".join(ts[:12]))
            extra = [w for w in extra if not re.fullmatch(r"\d+|[a-z]\d{4}|admission|report|claim|run|handoff", w)]
            docs[pid] = docs.get(pid, []) + extra
    return docs


def attempt_docs(idx):
    docs = {}
    for pid, rows in idx["attempts"].items():
        docs[pid] = words(" ".join("%s %s %s" % (r.get("method", ""), r.get("result", ""), r.get("obstruction", ""))
                                   for r in rows))
    return docs


def find_problems(idx, query, limit):
    q = words(query)
    if not q:
        return []
    ps = Bm25(problem_docs(idx)).scores(q)
    at = Bm25(attempt_docs(idx)).scores(q)
    top_p = max(ps.values()) if ps else 1.0
    top_a = max(at.values()) if at else 1.0
    fused = collections.Counter()
    for pid, s in ps.items():
        fused[pid] += s / top_p
    for pid, s in at.items():
        fused[pid] += ATTEMPT_WEIGHT * s / top_a
    return fused.most_common(limit)


def same_problem(idx, pid, docs=None, bm=None):
    """Other ids whose title and statement read like this problem's (dedup_key, duplicate_of, or a close text match)."""
    out = []
    rows = idx["problems"].get(pid, [])
    keys = set(str(r.get("dedup_key")) for r in rows if r.get("dedup_key"))
    dup = set(str(r.get("duplicate_of")) for r in rows if r.get("duplicate_of") not in (None, "", "None", "null"))
    for other, orows in idx["problems"].items():
        if other == pid:
            continue
        why = []
        if keys and keys & set(str(r.get("dedup_key")) for r in orows if r.get("dedup_key")):
            why.append("same dedup key")
        if other in dup or any(str(r.get("duplicate_of")) == pid for r in orows):
            why.append("marked duplicate")
        if why:
            out.append((other, ", ".join(why), 1.0))
    if docs is not None and bm is not None and docs.get(pid):
        sc = bm.scores(docs[pid])
        self_s = sc.get(pid) or 1.0
        for other, s in sorted(sc.items(), key=lambda x: -x[1])[:6]:
            if other != pid and s / self_s >= 0.55 and other not in [o for o, _, _ in out]:
                out.append((other, "statement match %.2f" % (s / self_s), s / self_s))
    return out


# ----------------------------------------------------------------------------------------------------------- print
def file_weight(f, pid):
    """How much of a file is about this problem: the share of its problem-id mentions that are this id, times a slow
    count term, tripled when the file is named for it. A list naming 160 problems once each weighs about 0.006."""
    n = f["ids"].get(pid, 0)
    if not n:
        return 0.0
    share = n / float(sum(f["ids"].values()))
    w = share * (1.0 + math.log2(n))
    if pid in f["path"].upper():
        w *= 3.0
    return w


LANE_RE = re.compile(r"^(?:[A-Z][A-Z0-9]*-[A-Z0-9-]*\d|[CDEFGHIP]\d{4}|w\d{3}(?:_.*)?|R\d\d|P\d\d)$")


def unit_of(path):
    """The work unit of a file: its path up to the first lane-like folder (R7-02, HARD8-04, PROB20-16, NM13-10, a
    problem id, a worker w###), else its first two folders."""
    parts = path.split("/")
    if parts[0] == MATHDIR:
        parts = parts[1:]
    dirs = parts[:-1]
    for i, p in enumerate(dirs[:5]):
        if LANE_RE.match(p):
            return "/".join(dirs[: i + 1])
    return "/".join(dirs[:2]) if dirs else parts[0]


def work_units(idx, pid):
    units = collections.defaultdict(lambda: {"w": 0.0, "files": [], "src": set()})
    for key, f in idx["files"].items():
        if pid not in f["ids"]:
            continue
        p = f["path"]
        if "/codex_pool_2026_10_10/" in "/" + p or "/trio_2026_10_11/" in "/" + p or p.startswith("probability_campaigns/codex_pool_2026-10-10"):
            continue   # printed from their own structured sections
        if re.search(r"(^|/)(problems|attempts)\.jsonl$", p):
            continue   # printed from the ledgers
        name = unit_of(p)
        if name.startswith("probability_1000/"):
            name = name[len("probability_1000/"):]     # an older copy of the intake folders inside open-problems-repo
        u = units[name]
        w = file_weight(f, pid)
        u["src"].add(f["src"])
        leaf = p.rsplit("/", 1)[-1]
        if any(leaf == q.rsplit("/", 1)[-1] for _, q, _ in u["files"]):
            continue                                    # the same file in both repositories
        u["files"].append((w, p, f.get("title", "")))
    out = []
    for name, u in units.items():
        u["files"].sort(key=lambda x: -x[0])
        ws = [x[0] for x in u["files"]]
        u["w"] = ws[0] + 0.25 * sum(ws[1:]) if ws else 0.0
        out.append((u["w"], name, u))
    out.sort(key=lambda x: -x[0])
    return out


def latest(rows):
    def key(r):
        return (str(r.get("recorded_at") or r.get("updated_at") or ""),)
    return sorted(rows, key=key)[-1] if rows else {}


def run_name(ledger_path):
    p = ledger_path or ""
    for pat, name in (("codex_20h_20260906", "6 Sep codex_20h first pass"), ("intake_claude_20260907", "7 Sep intake C"),
                      ("intake_D_", "8 Sep intake D"), ("intake_E_", "8 Sep intake E"), ("intake_F_", "8 Sep intake F"),
                      ("intake_G_", "10 Sep intake G"), ("intake_H_", "14 Sep intake H"), ("intake_I_", "14 Sep intake I")):
        if pat in p:
            return name
    return p.rsplit("/", 2)[-2] if "/" in p else p


def dedicated(f, pid):
    """A file is dedicated work on the problem when it is named for it, or most of its id mentions are this id."""
    n = f["ids"].get(pid, 0)
    return pid in f["path"].upper() or (n >= 2 and n / float(sum(f["ids"].values())) >= 0.5)


def timeline(idx, pid):
    """Every earlier attempt on the problem, one entry per run: date, run, agent, verdict, audit, path."""
    out = []
    for a in idx["attempts"].get(pid, []):
        verdict = []
        if a.get("full_problem_solved") not in (None, ""):
            verdict.append("full_problem_solved=%s" % a.get("full_problem_solved"))
        if a.get("candidate_full_solution") not in (None, "", "False", False):
            verdict.append("candidate_full_solution=%s" % short(a.get("candidate_full_solution"), 40))
        if a.get("result_status"):
            verdict.append(short(a.get("result_status"), 60))
        verdict.append(short(a.get("result"), 140))
        rev = a.get("independent_review")
        out.append({"date": str(a.get("recorded_at", ""))[:10], "run": run_name(a.get("_ledger")),
                    "agent": str(a.get("author") or "codex_20h worker" if "codex_20h" in a.get("_ledger", "") else
                                 a.get("author") or "?"),
                    "verdict": "; ".join(v for v in verdict if v),
                    "audit": "%s%s" % (short(a.get("verification"), 60) or "unreviewed",
                                       ("; independent review: " + short(rev, 80)) if rev not in (None, "", "none") else ""),
                    "path": "%s (%s)%s" % (a.get("_ledger", ""), a.get("attempt_id", ""),
                                           (" artifact " + str(a.get("artifact") or a.get("proof_file") or a.get("proof_path")))
                                           if (a.get("artifact") or a.get("proof_file") or a.get("proof_path")) else ""),
                    "superseded_by": a.get("superseded_by", "")})
    rows = idx["problems"].get(pid, [])
    if len(rows) > 1:
        r = latest(rows)
        out.append({"date": str(r.get("recorded_at", ""))[:10], "run": "ledger update" +
                    ((" (" + short(r.get("update_source") or r.get("author") or r.get("updated_by"), 60) + ")")
                     if (r.get("update_source") or r.get("author") or r.get("updated_by")) else ""),
                    "agent": short(r.get("author") or r.get("updated_by") or "", 40),
                    "verdict": short(r.get("result_status"), 160), "audit": short(r.get("verification"), 160),
                    "path": r.get("_ledger", "")})
    pool = idx["pool"].get(pid)
    if pool:
        c = collections.Counter(x["status"] for x in pool["claims"])
        revs = sorted(set(x.get("reviewer", "") for x in pool["claims"] if x.get("reviewer")))
        out.append({"date": "2026-10-10", "run": "10 Oct Codex pool", "agent": "%d workers (%s)" % (
            len(pool["workers"]), ", ".join(sorted(set(w["worker"].split("_")[0] for w in pool["workers"])))[:80]),
            "verdict": ("%d claim rows: " % len(pool["claims"]) + ", ".join("%s %d" % kv for kv in c.most_common(4)))
            if pool["claims"] else short(next((w.get("report") for w in pool["workers"] if w.get("report")), ""), 160),
            "audit": ("reviewers named: " + "; ".join(revs[:3])) if revs else "pool-internal labels only",
            "path": "; ".join(sorted(set(w["path"].rsplit("/", 1)[0] for w in pool["workers"])))[:200]})
    tr = idx["trio"].get(pid)
    if tr:
        out.append({"date": "2026-10-11..12", "run": "TRIO 11-12 Oct", "agent": "Codex solver",
                    "verdict": "STATE %s, VERDICT %s" % (tr["state"] or "?", tr["verdict"] or "?"),
                    "audit": ("; ".join("%s %s" % (a["auditor"], short(a["verdict"], 60)) for a in tr["audits"]) or
                              "no audit folder") + ("; third check CLAUDE_CHECK.md" if tr.get("check") else ""),
                    "path": tr["path"]})
    lanes = collections.defaultdict(list)
    for key, f in idx["files"].items():
        if pid in f["ids"] and dedicated(f, pid):
            p = f["path"]
            if "codex_pool_2026_10_10" in p or "trio_2026_10_11" in p or "codex_pool_2026-10-10" in p:
                continue
            if re.search(r"(^|/)(problems|attempts)\.jsonl$", p):
                continue
            lanes[unit_of(p)].append(f)
    for name, fs in lanes.items():
        dates = sorted(set(d for d in (date_of(f["path"]) for f in fs) if d))
        def rank(f):   # a lane's verdict: its latest handoff, final report or summary first, then its latest file
            leaf = f["path"].rsplit("/", 1)[-1].lower()
            return (bool(re.search(r"handoff|final|terminal|summary|report|verdict|result|status", leaf)),
                    date_of(f["path"]), f["path"])
        ordered = sorted(fs, key=rank, reverse=True)
        st = next((f.get("status") for f in ordered if f.get("status")), "")
        first_read = ordered[0]["path"].rsplit("/", 1)[-1] if ordered else ""
        lane = next((p for p in name.split("/") if LANE_RE.match(p) and not ID_RE.fullmatch(p)), "")
        out.append({"date": (dates[0] if len(dates) == 1 else "%s..%s" % (dates[0], dates[-1])) if dates else "",
                    "run": name, "agent": lane or "-", "verdict": st or "(no verdict line; %d files)" % len(fs),
                    "audit": "audit file present" if any(re.search(r"(?i)audit|referee|review", f["path"]) for f in fs)
                    else "", "path": "%s:%s (%d files)" % (fs[0]["src"], name, len(fs))})
    out.sort(key=lambda e: e["date"] or "9999")
    return out


def summary_line(idx, pid, tl):
    if not tl and not idx["store"].get(pid):
        n_list = sum(1 for f in idx["files"].values() if pid in f["ids"])
        return ("NO RECORDED ATTEMPT found for %s (searched: the P ledger at open-problems-repo origin/master %s, the C-I "
                "intake ledgers, every text file of open-problems-repo and of 06_math_research, the 10 Oct pool, TRIO, the "
                "store by key and by text, the memories)%s" % (pid, idx["mop_sha"][:9],
                                                              "; it is named in %d files among other problems (lists, "
                                                              "rankings, multi-problem reports: see WORK UNITS)" % n_list
                                                              if n_list else ""))
    runs = [e["run"] for e in tl]
    first = [e for e in tl if "first pass" in e["run"] or "intake" in e["run"]]
    deeper = [e for e in tl if e not in first and e["run"] != "ledger update"]
    s = "PRIOR WORK on %s: %d run%s" % (pid, len(runs), "" if len(runs) == 1 else "s")
    if first:
        s += "; first pass %s (%s)" % (first[0]["date"], first[0]["run"])
    if deeper:
        s += "; later: " + "; ".join("%s %s" % (e["run"], e["date"]) for e in deeper[:5])
    else:
        s += "; nothing after the first pass"
    return s


def print_problem(idx, pid, args, docs=None, bm=None, header=True):
    rows = idx["problems"].get(pid, [])
    o = idx["op200"].get(pid)
    r = latest(rows) if rows else {}
    title = r.get("title") or (o or {}).get("title") or "(no ledger row)"
    led = r.get("_ledger", "")
    print("%s  %s" % (pid, title))
    tl = timeline(idx, pid)
    print("  " + summary_line(idx, pid, tl))
    if tl:
        print("  EARLIER WORK, oldest first (date | run | agent | verdict | audit | where):")
        for e in tl[: args.runs]:
            print("   - %s | %s | %s | %s | %s | %s%s" % (e["date"] or "undated", e["run"], short(e["agent"], 50),
                                                        short(e["verdict"], 200), short(e["audit"], 140) or "-",
                                                        short(e["path"], 200),
                                                        (" | SUPERSEDED by " + e["superseded_by"]) if e.get("superseded_by") else ""))
        if len(tl) > args.runs:
            print("   ... %d more (--runs N)" % (len(tl) - args.runs))
    if r:
        src = " ".join(str(x) for x in (r.get("source_id", ""), r.get("locator", "")) if x)
        print("  ledger: %s (%d row%s; the latest is %s)" % (led, len(rows), "" if len(rows) == 1 else "s",
                                                             r.get("recorded_at") or "undated"))
        if src:
            print("  source: %s" % short(src, 200))
        st = ["%s=%s" % (k, short(r.get(k), args.chars)) for k in ("historical_status", "current_status",
                                                                     "result_status", "verification")
              if r.get(k) not in (None, "")]
        if st:
            print("  status (latest row): " + "; ".join(st))
        older = [x for x in rows if x is not r and x.get("result_status") not in (None, "", r.get("result_status"))]
        if older:
            print("  earlier rows said: " + "; ".join("%s result_status=%s" % (str(x.get("recorded_at", ""))[:10],
                                                                              short(x.get("result_status"), 80))
                                                       for x in older[-3:]))
        for k in ("resolved_by", "resolution_summary", "status_note", "memo", "current_status_note", "proof_file",
                  "proof_path", "duplicate_of"):
            if r.get(k) not in (None, "", "None"):
                print("  %s: %s" % (k, short(r.get(k), 300)))
        if args.statement:
            print("  statement: " + short(r.get("statement"), args.chars))
    if o:
        print("  on the 200 list: rank %s (%s); where it stands: %s" % (o.get("rank"), o.get("list_version", ""),
                                                                       short(o.get("where_it_stands"), 240)))
    sp = same_problem(idx, pid, docs, bm)
    if sp:
        print("  possibly the same problem: " + "; ".join("%s (%s; %s)" % (x, short(latest(idx["problems"].get(x, [])).get("title", ""), 60), why)
                                                        for x, why, _ in sp[:4]))
    # attempts
    at = idx["attempts"].get(pid, [])
    if at:
        print("  ATTEMPTS in the ledgers (%d):" % len(at))
        for a in sorted(at, key=lambda a: str(a.get("recorded_at", "")))[: args.attempts]:
            flags = [str(a.get(k)) for k in ("verification",) if a.get(k)]
            if a.get("full_problem_solved") not in (None, ""):
                flags.append("full_problem_solved=%s" % a.get("full_problem_solved"))
            if a.get("superseded_by"):
                flags.append("SUPERSEDED by %s" % a.get("superseded_by"))
            print("   - %s %s [%s] %s" % (a.get("attempt_id", "?"), str(a.get("recorded_at", ""))[:10], "; ".join(flags),
                                          ("author " + str(a.get("author"))) if a.get("author") else ""))
            for k, lab in (("method", "method"), ("result", "result"), ("obstruction", "obstruction"),
                           ("failed_route_counterexample", "failed route"), ("key_identity", "key identity"),
                           ("independent_check", "check")):
                if a.get(k):
                    print("       %s: %s" % (lab, short(a.get(k), args.chars)))
            art = a.get("artifact") or a.get("proof_file") or a.get("proof_path")
            if art:
                print("       artifact: %s" % art)
        if len(at) > args.attempts:
            print("   ... %d more attempt rows" % (len(at) - args.attempts))
    # pool
    pool = idx["pool"].get(pid)
    if pool:
        c = collections.Counter(x["status"] for x in pool["claims"])
        print("  10 OCT CODEX POOL: %d worker%s, %d claim rows%s; no target was solved there, labels are the workers' own"
              % (len(pool["workers"]), "" if len(pool["workers"]) == 1 else "s", len(pool["claims"]),
                 (" (" + ", ".join("%s %d" % kv for kv in c.most_common()) + ")") if c else ""))
        best = [x for x in pool["claims"] if x["status"] in ("independently checked proof", "proved special case")
                and x["type"] not in ("source_status_guard",)]
        for x in best[: args.pool]:
            print("   - %s [%s] %s | gap: %s" % (x["worker"], x["status"], short(x["statement"], 260), short(x["gap"], 160)))
        for w in pool["workers"]:
            if w.get("report"):
                print("   - %s %s: %s" % (w["worker"], w["status"], short(w["report"], 260)))
        if pool["workers"]:
            print("     folders: %s" % "; ".join(sorted(set(w["path"].rsplit("/", 1)[0] for w in pool["workers"])))[:300])
    tr = idx["trio"].get(pid)
    if tr:
        c = collections.Counter(x["status"] for x in tr["claims"])
        print("  TRIO RUN 11-12 OCT: STATE %s, VERDICT %s; claims %s; audits %s%s" % (
            tr["state"] or "?", tr["verdict"] or "?", ", ".join("%s %d" % kv for kv in c.most_common()) or "none",
            "; ".join("%s: %s" % (a["auditor"], short(a["verdict"], 80)) for a in tr["audits"]) or "none",
            "; third check CLAUDE_CHECK.md" if tr.get("check") else ""))
        if tr["last"]:
            print("     last entry: " + short(tr["last"], 300))
        for x in tr["claims"][: args.pool]:
            print("   - [%s] %s | gap: %s" % (x["status"], short(x["claim"], 240), short(x["gap"], 140)))
        print("     folder: " + tr["path"])
    # work units
    units = work_units(idx, pid)
    if units:
        print("  WORK UNITS that name %s (%d; ranked by how much of each file is about it):" % (pid, len(units)))
        for w, name, u in units[: args.files]:
            dates = sorted(set(d for d in (date_of(p) for _, p, _ in u["files"]) if d))
            span = (dates[0] if dates[0] == dates[-1] else "%s..%s" % (dates[0], dates[-1])) if dates else ""
            src = "/".join(sorted(u["src"]))
            print("   - [%s] %s %s(%d file%s)" % (src, name, (span + " ") if span else "", len(u["files"]),
                                                   "" if len(u["files"]) == 1 else "s"))
            for fw, p, t in u["files"][:3]:
                leaf = p.rsplit("/", 1)[-1]
                print("       %s%s" % (leaf, (" - " + t) if t else ""))
        if len(units) > args.files:
            rest = units[args.files:]
            print("   ... %d more units (--files N shows them): %s" % (len(rest), ", ".join(n for _, n, _ in rest[:6])))
    # store
    st = idx["store"].get(pid, [])
    if st:
        print("  MATH STORE cards (read whole: python ~/.claude/dreaming/math_recall.py --id <id>):")
        for c in sorted(st, key=lambda c: ("op", "at", "res", "st", "r", "context", "pool").index(c["role"])
                        if c["role"] in ("op", "at", "res", "st", "r", "context", "pool") else 9):
            print("   - %s %s: %s%s" % (c["role"], c["id"][:22], c["title"],
                                       ("  (SUPERSEDED by %s)" % c["superseded_by"][:22]) if c["superseded_by"] else ""))
        techs = collections.OrderedDict()
        for c in st:
            for l in c["links"]:
                if l["kind"] in ("depends_on", "cites", "analogous_to", "specializes", "generalizes", "refutes"):
                    techs.setdefault(l["id"], (l["kind"], l["title"]))
        if techs:
            print("    techniques and results these cards link to (%d): %s" % (len(techs), "; ".join(
                "%s %s (%s)" % (k, t, i[:22]) for i, (k, t) in list(techs.items())[: args.techs])))
    bt = [c for c in idx.get("store_by_text", {}).get(pid, []) if c["keyed"] != pid and c["ratio"] >= 0.6]
    if bt:
        print("  STORE CARDS FOUND BY TEXT (its title and statement against every current card; not keyed to %s):" % pid)
        for c in bt[:4]:
            print("   - %s %.2f: %s%s" % (c["id"][:22], c["ratio"], c["title"],
                                         ("  (keyed to %s)" % c["keyed"]) if c["keyed"] else ""))
    mem = sorted(idx["memories"].get(pid, []), key=lambda m: (m["slug"].startswith("compaction_"),
                                                              -m["n"] / math.log2(1 + m["nids"])))
    nc = [m for m in mem if not m["slug"].startswith("compaction_")]
    mem = nc[:5] + [m for m in mem if m["slug"].startswith("compaction_")][: max(0, 2 - len(nc))]
    if mem:
        print("  MEMORIES that name it: " + "; ".join("%s%s" % (m["slug"], " (superseded by %s)" % m["superseded_by"]
                                                                if m["superseded_by"] else "") for m in mem[:5]))
    if not (rows or at or pool or tr or units or st or mem):
        print("  nothing recorded under this id")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Prior work on an open problem, by id or by statement.")
    ap.add_argument("query", nargs="*", help="a problem id (P0072) or statement words")
    ap.add_argument("--limit", type=int, default=3, help="problems shown for a statement query (default 3)")
    ap.add_argument("--files", type=int, default=6, help="work units shown per problem (default 6)")
    ap.add_argument("--attempts", type=int, default=6, help="attempt rows shown per problem (default 6)")
    ap.add_argument("--pool", type=int, default=3, help="pool and TRIO claim rows shown (default 3)")
    ap.add_argument("--techs", type=int, default=8, help="technique cards shown (default 8)")
    ap.add_argument("--runs", type=int, default=12, help="earlier-work timeline entries shown (default 12)")
    ap.add_argument("--chars", type=int, default=400, help="characters per attempt field (default 400)")
    ap.add_argument("--statement", action="store_true", help="also print the ledger statement")
    ap.add_argument("--refresh", action="store_true", help="rebuild the index first")
    ap.add_argument("--json", action="store_true", help="print the matched ids and scores as JSON (for measurement)")
    ap.add_argument("--rebuild-only", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    if args.rebuild_only:
        try:
            try:  # normal I/O and memory priority too (a task parent can hand down I/O 1 / memory 2)
                sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
                import bm25
                bm25._raise_io_mem_priority()
            except Exception:
                pass
            build_index()
        finally:
            try:
                os.remove(REBUILD_LOCK)
            except OSError:
                pass
        return 0
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    idx = load_index(args.refresh)
    q = " ".join(args.query).strip()
    if not q:
        print("problem_recall: index of open-problems-repo origin/master %s, brain %s, built %s (%ss): %d problems, %d with "
              "attempt rows, %d files naming an id" % (idx["mop_sha"][:9], idx["brain_head"], idx["built_at"],
                                                      idx.get("build_seconds"), len(idx["problems"]), len(idx["attempts"]),
                                                      len(idx["files"])))
        return 0
    ids = [a + b for a, b in ID_RE.findall(q.upper())] if ID_RE.fullmatch(q.upper().split()[0]) else []
    docs = problem_docs(idx)
    bm = Bm25(docs)
    if ids and all(ID_RE.fullmatch(x) for x in q.upper().replace(",", " ").split()):
        if args.json:
            print(json.dumps({"query": q, "ids": ids}))
            return 0
        print("problem_recall %s (open-problems-repo origin/master %s, built %s)" % (" ".join(ids), idx["mop_sha"][:9],
                                                                                   idx["built_at"][:16]))
        for pid in ids:
            print_problem(idx, pid, args, docs, bm)
        return 0
    hits = find_problems(idx, q, max(args.limit, 10))
    if args.json:
        print(json.dumps({"query": q[:80], "hits": [[p, round(s, 4)] for p, s in hits]}))
        return 0
    if not hits:
        print("problem_recall: no recorded problem matches these words; try the object, the named conjecture or the source")
        return 0
    print("problem_recall \"%s\" (open-problems-repo origin/master %s, built %s): best matches" % (
        short(q, 80), idx["mop_sha"][:9], idx["built_at"][:16]))
    for pid, s in hits[: max(args.limit, 5)]:
        r = latest(idx["problems"].get(pid, [])) or idx["op200"].get(pid, {})
        n_at = len(idx["attempts"].get(pid, []))
        n_u = len(work_units(idx, pid))
        print("  %s %.2f  %s  [%d attempt rows, %d work units%s%s]" % (
            pid, s, short(r.get("title", ""), 90), n_at, n_u, ", pool" if pid in idx["pool"] else "",
            ", TRIO" if pid in idx["trio"] else ""))
    print()
    for pid, s in hits[: args.limit]:
        print_problem(idx, pid, args, docs, bm)
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
