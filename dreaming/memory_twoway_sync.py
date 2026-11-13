#!/usr/bin/env python3
"""memory_twoway_sync.py - keep this computer's memories and the repository's memory mirror in step, both ways.

    python memory_twoway_sync.py --repo <clone> --memory-dir <live memories> [--mirror memory] [--days 3] [--apply]

The environment can give the same three settings: BRAIN_REPO, BRAIN_MEMORY_DIR, BRAIN_MEMORY_MIRROR.

Why: when several computers share one brain, drift runs both ways. A correction made on one computer never reaches
the mirror unless something pushes it, and an addendum or a Related line written on another computer never comes back,
because a pull that only copies missing files skips a file that already exists here.

Each run:
  1. fetches origin main of the clone into refs/twoway/main (its own ref, so peers fetching the same clone at once
     never fight over refs/remotes/origin/main);
  2. takes the authored memories changed here in the last --days days (generated nodes are left to their builders)
     and compares each with the mirror by git blob id;
  3. classifies each differing file by its non-blank lines:
       ORIGIN_AHEAD  the mirror holds every line here and more -> pulled here (lossless)
       LIVE_AHEAD    this computer holds every mirror line and more -> pushed
       BOTH          each side has lines the other lacks -> merged here, then pushed: the file here is kept and the
                     mirror's own body lines are appended; a mirror line that is the old form of an edit made here
                     (contained in, or difflib ratio >= 0.6 with, a line only here) or that sits in the frontmatter is
                     dropped, except superseded_by: and status: superseded, which are carried into the frontmatter
     MISSING authored memories are pushed;
  4. backs up every file it rewrites to ~/.claude/brain_graph/memory_sync_backups_<date>/ (memories are never deleted);
  5. pushes the named mirror paths with push_paths.sh beside this file (secret scan, lease, one commit).
Default is a dry run that prints the counts; --apply does it; --no-push stops before the push.
Journal hubs (OPEN_QUESTIONS, PREDICTIONS, STATE_OF_MIND, MISTAKES) are merged by item with
memory_hub_merge.merge_items and pushed, but only when every mirror line is kept; a lossy merge is listed and left.
CONSCIENCE is a rolling ledger that drops old lines by design, so it is pushed as it is here when it differs.
MEMORY.md (the index each computer rebuilds) is only listed.
"""
import argparse
import datetime
import difflib
import hashlib
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("BRAIN_REPO", "")
MEM = os.environ.get("BRAIN_MEMORY_DIR", "")
MIRROR = os.environ.get("BRAIN_MEMORY_MIRROR", "memory").strip("/")
REF = "refs/twoway/main"
NOWIN = 0x08000000 if os.name == "nt" else 0
HUB_MERGE = ("OPEN_QUESTIONS.md", "PREDICTIONS.md", "STATE_OF_MIND.md", "MISTAKES.md")
HUB_COPY = ("CONSCIENCE.md",)
GENERATED = ("checklist_task", "compaction_", "mistake_entry", "data_in_bucket", "data_on_github", "file_node",
             "audit_report_node", "term_")
LOG_DIR = os.path.join(os.path.expanduser("~"), ".claude", "online_pull")


def git(*a, inp=None, check=True):
    r = subprocess.run(["git", *a], cwd=REPO, input=inp, capture_output=True, creationflags=NOWIN)
    if check and r.returncode:
        raise SystemExit("git %s failed: %s" % (a[0], r.stderr.decode("utf-8", "replace")[:300]))
    return r.stdout


def blob_id(data):
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def lines(b):
    return b.decode("utf-8", "replace").replace("\r", "").splitlines()


def fm_end(L):
    if L and L[0].strip() == "---":
        for i in range(1, len(L)):
            if L[i].strip() == "---":
                return i
    return -1


def authored(n):
    return bool(re.fullmatch(r"[a-z0-9_]+\.md", n)) and not n.startswith(GENERATED) and not n.endswith("_hub.md")


def classify(o_lines, lv_lines):
    o = set(x for x in o_lines if x.strip())
    lv = set(x for x in lv_lines if x.strip())
    return "SAME" if o == lv else "LIVE_AHEAD" if o <= lv else "ORIGIN_AHEAD" if lv <= o else "BOTH"


def merge(o, lv):
    """Return (merged lines, n added) for a BOTH file; o = mirror lines, lv = live lines."""
    sl, so = set(lv), set(o)
    oe = fm_end(o)
    ofm = set(o[:oe + 1]) if oe >= 0 else set()
    live_only = [x for x in lv if x.strip() and x not in so]
    add, fm_add = [], []
    for x in o:
        if not x.strip() or x in sl or x in add or x in fm_add:
            continue
        if x in ofm:
            if x.strip().startswith("superseded_by:") or x.strip() == "status: superseded":
                fm_add.append(x)
        elif not any(x.strip() in y or difflib.SequenceMatcher(None, x, y).ratio() >= 0.6 for y in live_only):
            add.append(x)
    le = fm_end(lv)
    if fm_add and le >= 0 and any(y.strip().startswith("superseded_by:") for y in lv[:le]):
        fm_add = [y for y in fm_add if not y.strip().startswith("superseded_by:")]
    out = list(lv)
    if fm_add:
        if le < 0:
            add = fm_add + add
        else:
            head = lv[:le]
            if any(y.strip() == "status: superseded" for y in fm_add):
                head = [y for y in head if not y.strip().startswith("status:")]
            ins = len(head)
            if any(y.startswith("  ") for y in fm_add):   # an indented key goes inside its block, above top-level keys
                while ins > 1 and re.match(r"^[a-z_]+: .+", head[ins - 1]):
                    ins -= 1
            out = head[:ins] + fm_add + head[ins:] + lv[le:]
    if add:
        while out and not out[-1].strip():
            out.pop()
        out += [""] + add
    return out, len(add) + len(fm_add)


def backup_dir():
    return os.path.join(os.path.expanduser("~"), ".claude", "brain_graph",
                        "memory_sync_backups_" + datetime.date.today().strftime("%Y%m%d"))


def _read(p):
    with open(p, "rb") as fh:
        return fh.read()


def _write_text(p, t):
    with open(p + ".twowaytmp", "w", encoding="utf-8", newline="") as fh:
        fh.write(t)
    os.replace(p + ".twowaytmp", p)


def write_lines(p, L, crlf, bk):
    os.makedirs(bk, exist_ok=True)
    shutil.copy2(p, os.path.join(bk, os.path.basename(p)))
    t = "\n".join(L) + "\n"
    _write_text(p, t.replace("\n", "\r\n") if crlf else t)


def _bash():
    for b in (shutil.which("bash"), r"C:\Program Files\Git\bin\bash.exe"):
        if b and os.path.isfile(b):
            return b
    return "bash"


def main(argv=None):
    global REPO, MEM, MIRROR
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=REPO)
    ap.add_argument("--memory-dir", default=MEM)
    ap.add_argument("--mirror", default=MIRROR)
    ap.add_argument("--days", type=float, default=3.0)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--no-push", action="store_true")
    a = ap.parse_args(argv)
    REPO, MEM, MIRROR = a.repo, a.memory_dir, a.mirror.strip("/")
    if not REPO or not MEM or not os.path.isdir(REPO) or not os.path.isdir(MEM):
        raise SystemExit("set --repo and --memory-dir (or BRAIN_REPO and BRAIN_MEMORY_DIR) to existing folders")
    t0 = time.time()
    git("fetch", "-q", "--no-tags", "--refmap=", "origin", "+refs/heads/main:" + REF)
    tip = git("rev-parse", REF).decode().strip()
    t_fetch = time.time() - t0
    tree = {}
    for row in git("ls-tree", REF, MIRROR + "/").decode("utf-8", "replace").splitlines():
        meta, path = row.split("\t", 1)
        tree[path.rsplit("/", 1)[-1]] = meta.split()[2]
    cut = time.time() - a.days * 86400
    recent = [n for n in os.listdir(MEM) if n.endswith(".md") and os.path.getmtime(os.path.join(MEM, n)) >= cut]
    missing, diff, hubs = [], [], []
    for n in recent:
        data = _read(os.path.join(MEM, n))
        if not authored(n):
            if re.fullmatch(r"[A-Z_]+\.md", n) and n in tree and blob_id(data) != tree[n] \
                    and blob_id(data.replace(b"\r\n", b"\n")) != tree[n]:
                hubs.append(n)
            continue
        if n not in tree:
            missing.append(n)
        elif blob_id(data) != tree[n] and blob_id(data.replace(b"\r\n", b"\n")) != tree[n]:
            diff.append(n)
    want = diff + [h for h in hubs if h in HUB_MERGE]
    if want:   # one batched fetch of the mirror blobs: in a partial clone a lazy fetch per blob is far slower
        oids = [tree[n] for n in want]
        for i in range(0, len(oids), 400):
            git("-c", "fetch.negotiationAlgorithm=noop", "fetch", "-q", "--no-tags", "--no-write-fetch-head",
                "origin", *oids[i:i + 400], check=False)
    blobs = {}
    if want:
        out = git("cat-file", "--batch", inp="".join(tree[n] + "\n" for n in want).encode())
        pos = 0
        for n in want:
            nl = out.index(b"\n", pos)
            hdr = out[pos:nl].split()
            if len(hdr) < 3 or hdr[1] != b"blob":
                pos = nl + 1
                continue
            size = int(hdr[2])
            blobs[n] = out[nl + 1:nl + 1 + size]
            pos = nl + 1 + size + 1
    cls = {"SAME": [], "ORIGIN_AHEAD": [], "LIVE_AHEAD": [], "BOTH": []}
    for n in diff:
        if n in blobs:
            cls[classify(lines(blobs[n]), lines(_read(os.path.join(MEM, n))))].append(n)
    print("tip %s recent %d authored-diff %d missing %d | %s | hubs differing: %s" % (
        tip[:10], len(recent), len(diff), len(missing),
        " ".join("%s %d" % (k, len(v)) for k, v in cls.items()), ",".join(hubs) or "none"))
    print("stages: fetch %.0f s, compare+blobs %.0f s" % (t_fetch, time.time() - t0 - t_fetch))
    if not a.apply:
        return 0
    bk = backup_dir()
    pulled = merged = 0
    for n in cls["ORIGIN_AHEAD"]:
        p = os.path.join(MEM, n)
        raw = _read(p)
        if not set(x for x in lines(raw) if x.strip()) <= set(lines(blobs[n])):   # edited since the read
            continue
        write_lines(p, lines(blobs[n]), b"\r\n" in raw, bk)
        pulled += 1
    push = list(cls["LIVE_AHEAD"]) + list(missing)
    for n in cls["BOTH"]:
        p = os.path.join(MEM, n)
        raw = _read(p)
        L, k = merge(lines(blobs[n]), lines(raw))
        if k:
            write_lines(p, L, b"\r\n" in raw, bk)
            merged += 1
        push.append(n)
    hub_note = []
    for n in hubs:
        p = os.path.join(MEM, n)
        if n in HUB_COPY:
            push.append(n)
            hub_note.append(n + " as here")
        elif n in HUB_MERGE and n in blobs:
            sys.path.insert(0, HERE)
            from memory_hub_merge import merge_items
            raw = _read(p)
            lv = raw.decode("utf-8", "replace").replace("\r", "").split("\n")
            L, added, lost, _rep = merge_items(lv, blobs[n].decode("utf-8", "replace").replace("\r", "").split("\n"))
            if lost:
                hub_note.append("%s left (merge would drop %d mirror lines)" % (n, len(lost)))
                continue
            if added:
                os.makedirs(bk, exist_ok=True)
                shutil.copy2(p, os.path.join(bk, n + ".prehub"))
                t = "\n".join(L)
                _write_text(p, t.replace("\n", "\r\n") if b"\r\n" in raw else t)
            push.append(n)
            hub_note.append("%s +%d" % (n, len(added)))
    print("pulled %d, merged %d, to push %d; hubs: %s" % (pulled, merged, len(push), ", ".join(hub_note) or "none"))
    if not push or a.no_push:
        return 0
    paths = []
    for n in push:
        os.makedirs(os.path.join(REPO, MIRROR), exist_ok=True)
        shutil.copyfile(os.path.join(MEM, n), os.path.join(REPO, MIRROR, n))
        paths.append(MIRROR + "/" + n)
    env = dict(os.environ, ALLOW_REMOVALS="1", PUSH_PATHS_ATTEMPTS=os.environ.get("PUSH_PATHS_ATTEMPTS", "1"))
    t_push = time.time()
    r = None
    for attempt in (1, 2):
        r = subprocess.run([_bash(), os.path.join(HERE, "push_paths.sh"), ".",
                            "memories: two-way sync, %d up [skip ci]" % len(paths), *paths],
                           cwd=REPO, env=env, capture_output=True, creationflags=NOWIN)
        out = r.stdout.decode("utf-8", "replace")
        err = r.stderr.decode("utf-8", "replace")
        tail = out.strip().splitlines()[-2:]
        print("push rc %d: %s (%.0f s; this push %.0f s)" % (r.returncode, " | ".join(tail), time.time() - t0,
                                                             time.time() - t_push))
        t_push = time.time()
        if r.returncode:   # keep the whole push transcript: a crash in the API fallback prints only to stderr
            os.makedirs(LOG_DIR, exist_ok=True)
            log = os.path.join(LOG_DIR, "twoway_push_last.log")
            with open(log, "w", encoding="utf-8") as fh:
                fh.write(out + "\n--- stderr ---\n" + err)
            print("  stderr tail: %s (full push output in %s)" % (" | ".join(err.strip().splitlines()[-3:]), log))
        # a peer wrote some of these files mid-push: leave those for the next run, which re-reads them with the
        # peer's change, and push the rest now, so one moved file does not hold back the others
        moved = set(re.findall(re.escape(MIRROR) + r"/[a-z0-9_]+\.md", out.split("peer changed", 1)[-1])) \
            if r.returncode and "peer changed" in out else set()
        if attempt == 2 or not moved or not set(paths) - moved:
            break
        paths = [p for p in paths if p not in moved]
        print("held back %d file(s) a peer changed; retrying %d" % (len(moved), len(paths)))
    return r.returncode


if __name__ == "__main__":
    if sys.stdout is None:   # pythonw under a scheduled task: the run's lines go to a log
        os.makedirs(LOG_DIR, exist_ok=True)
        sys.stdout = sys.stderr = open(os.path.join(LOG_DIR, "twoway.log"), "a", encoding="utf-8", buffering=1)
        print(time.strftime("%Y-%m-%d %H:%M:%S"), "run", " ".join(sys.argv[1:]))
    try:
        rc = main()
    except SystemExit as e:
        print("stopped:", e)
        rc = 3
    sys.stdout.flush()
    sys.exit(rc)
