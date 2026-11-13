"""split_big_commit.py <full-commit-sha> [--budget MB] [--repo PATH] [--dry-run]

Split ONE local, never-pushed commit whose new blobs exceed the secret scanner's bounded total (scan_secrets.py
MAX_TOTAL_OBJECT_BYTES, 256 MB) into parts that each fit, so the dream's per-commit scan can certify it. The scanner
is NOT loosened: every byte is still scanned, just in commits small enough for one bounded scan.

Why it exists: one nightly commit changed 19,819 paths, 366.8 MB of blobs. The whole-range
scan and the per-commit fallback both refused it ("Git objects exceeded the bounded total scan size"), so the nightly
push stayed blocked for days while the brain views alone grow toward ~175 MB a commit.

How: the changed paths are packed largest-first into bins of at most --budget MB (default 180). Part k's tree is the
parent's tree plus bins 1..k; the last part is the original commit itself (same tree, author, dates, message). Later
local commits are re-made on top with their own trees and metadata. Checks before any ref moves: the commit is not on
origin/main, no merges or signed commits in range, every re-made commit's tree equals the old one. Backup ref
refs/backup/main_pre_split_<UTC stamp>; the main ref moves by compare-and-swap. Hold the mesh claim
git:<repo>/refs/heads/main while running it.
"""
import argparse, datetime, os, subprocess, sys, tempfile

ap = argparse.ArgumentParser()
ap.add_argument("target")
ap.add_argument("--budget", type=float, default=180.0, help="MB of new blobs per part")
ap.add_argument("--repo", default=".")
ap.add_argument("--dry-run", action="store_true")
ap.add_argument("--base", default="origin/main",
                help="the pushed main (the dream passes the advertised remote OID); the target must not be in it")
A = ap.parse_args()
R, TARGET, BUDGET, DRY = A.repo, A.target, int(A.budget * 1e6), A.dry_run


def git(*a, env=None, inp=None):
    p = subprocess.run(["git", "-C", R, *a], capture_output=True, env=env, input=inp)
    if p.returncode:
        sys.exit("git %s failed: %s" % (" ".join(a[:3]), p.stderr.decode(errors="replace")))
    return p.stdout


def meta(c):
    head, msg = git("cat-file", "commit", c).split(b"\n\n", 1)
    hdr = {}
    for line in head.split(b"\n"):
        k, v = line.split(b" ", 1)
        hdr.setdefault(k, v)
    if b"gpgsig" in hdr or b"encoding" in hdr:
        sys.exit("signed/encoded commit %s: stop" % c)

    def who(v):
        name, rest = v.decode().rsplit(" <", 1)
        mail, when = rest.split("> ", 1)
        return name, mail, when
    an, am, ad = who(hdr[b"author"]); cn, cm, cd = who(hdr[b"committer"])
    env = dict(os.environ, GIT_AUTHOR_NAME=an, GIT_AUTHOR_EMAIL=am, GIT_AUTHOR_DATE=ad,
               GIT_COMMITTER_NAME=cn, GIT_COMMITTER_EMAIL=cm, GIT_COMMITTER_DATE=cd)
    return hdr[b"tree"].decode(), msg, env


if len(TARGET) != 40:
    sys.exit("give the full 40-character commit id")
old_tip = git("rev-parse", "main").decode().strip()
# 7 Nov: the base must resolve. An unresolved origin/main made --is-ancestor return 128, which read as "not pushed".
base_oid = subprocess.run(["git", "-C", R, "rev-parse", "--verify", "--quiet", A.base + "^{commit}"],
                          capture_output=True).stdout.decode().strip()
if len(base_oid) != 40:
    sys.exit("base %s does not resolve to a commit: stop" % A.base)
if subprocess.run(["git", "-C", R, "merge-base", "--is-ancestor", TARGET, base_oid]).returncode == 0:
    sys.exit("target already in the pushed main %s - never rewrite pushed history: stop" % base_oid[:12])
if subprocess.run(["git", "-C", R, "merge-base", "--is-ancestor", TARGET, old_tip]).returncode:
    sys.exit("target not on main: stop")
chain = git("rev-list", "--reverse", "--topo-order", TARGET + "^.." + old_tip).decode().split()
if chain[0] != TARGET or git("rev-list", "--merges", TARGET + "^.." + old_tip).strip():
    sys.exit("range is not a plain line from the target to main: stop")
parent = git("rev-parse", TARGET + "^").decode().strip()

rows = []
parts = git("diff-tree", "-r", "--no-renames", "-z", parent, TARGET).split(b"\0")
i = 0
while i < len(parts) - 1:
    head = parts[i].decode()
    if not head.startswith(":"):
        i += 1
        continue
    _, nmode, _, noid, st = head[1:].split(" ")
    rows.append((nmode, noid, st, parts[i + 1].decode("utf-8", "surrogateescape")))
    i += 2
oids = sorted({r[1] for r in rows if r[2] != "D"})
sizes = dict(zip(oids, (int(x) for x in git("cat-file", "--batch-check=%(objectsize)",
                                             inp=("\n".join(oids) + "\n").encode()).decode().split())))
total = sum(sizes.values())
print("target %s: %d changed paths, %.1f MB of new blobs" % (TARGET[:12], len(rows), total / 1e6))
if total <= BUDGET:
    sys.exit("already within one part (%.1f MB <= %.1f MB): nothing to do" % (total / 1e6, BUDGET / 1e6))

# first-fit decreasing; a blob counted once per bin it first lands in
bins, seen = [], {}
for r in sorted((r for r in rows if r[2] != "D"), key=lambda r: -sizes[r[1]]):
    if r[1] in seen:
        bins[seen[r[1]]][0].append(r)
        continue
    s = sizes[r[1]]
    if s > BUDGET:
        sys.exit("one blob is %.1f MB, over the per-part budget: fix the file, not the budget" % (s / 1e6))
    for k, (rs, used) in enumerate(bins):
        if used + s <= BUDGET:
            rs.append(r); bins[k] = (rs, used + s); seen[r[1]] = k
            break
    else:
        bins.append(([r], s)); seen[r[1]] = len(bins) - 1
for k, (rs, used) in enumerate(bins, 1):
    print("  part %d: %d paths, %.1f MB" % (k, len(rs), used / 1e6))
print("  deletions ride in the last part: %d" % sum(1 for r in rows if r[2] == "D"))
if DRY:
    sys.exit(0)

tmpdir = tempfile.mkdtemp(prefix="splitcommit_")
env_i = dict(os.environ, GIT_INDEX_FILE=os.path.join(tmpdir, "index"))
git("read-tree", parent, env=env_i)
tree, msg, env_c = meta(TARGET)
subj, _, body = msg.partition(b"\n")
n = len(bins)
cur = parent
for k, (rs, _) in enumerate(bins[:-1], 1):
    info = "".join("%s %s\t%s\n" % (m, o, p) for m, o, _, p in rs).encode("utf-8", "surrogateescape")
    git("update-index", "--index-info", env=env_i, inp=info)
    tk = git("write-tree", env=env_i).decode().strip()
    mk = subj + b" (part %d of %d)\n" % (k, n) + (b"\n" + body.lstrip(b"\n") if body.strip() else b"")
    cur = git("commit-tree", tk, "-p", cur, env=env_c, inp=mk).decode().strip()
first_part = cur
new = git("commit-tree", tree, "-p", cur, env=env_c, inp=msg).decode().strip()
for c in chain[1:]:
    tr, m, e = meta(c)
    new = git("commit-tree", tr, "-p", new, env=e, inp=m).decode().strip()
news = git("rev-list", "--reverse", first_part + ".." + new).decode().split()
if len(news) != len(chain):
    sys.exit("re-made chain has %d commits, old has %d: stop (refs untouched)" % (len(news), len(chain)))
for o, nw in zip(chain, news):
    if git("rev-parse", o + "^{tree}") != git("rev-parse", nw + "^{tree}"):
        sys.exit("tree mismatch %s vs %s: stop (refs untouched)" % (o[:12], nw[:12]))
stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
backup = "refs/backup/main_pre_split_" + stamp
git("update-ref", backup, old_tip)
git("update-ref", "-m", "split an oversized local commit into %d parts" % n, "refs/heads/main", new, old_tip)
print("main %s -> %s (backup %s)" % (old_tip[:12], new[:12], backup))
for o, nw in zip(chain, news):  # machine lines: each old commit and the commit that replaced it
    print("MAP %s %s" % (o, nw))
print("NEWMAIN %s" % new)
print("scan each: for c in $(git rev-list --reverse %s..main); do scan_secrets.py --range $c^..$c; done" % parent[:12])
