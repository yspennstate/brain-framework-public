#!/usr/bin/env python3
"""Rebuild the BM25 lexical index ONLY when it is actually stale. A safety net, not a second writer.

WHY CONDITIONAL, measured 2026-08-17. The index has two writers now:
  1. the dream, which rebuilds it as part of a normal cycle, and
  2. the 3-hourly BrainEmbeddingRefresh launcher, which I added earlier tonight.
Writer 2 exists because the dream stopped for ~31 hours (last run 2026-08-15 19:31, next
2026-08-17 02:52) and the index froze 25.8h with 136 memories invisible to lexical recall. That
gap was real and the safety net is worth keeping.

But an UNCONDITIONAL second writer is harmful once the dream is running again, for a reason that is
not about cost: every rebuild changes the index hash, and the peer campaign's dashboard fails closed
whenever the observed BM25 generation moves. Three of its publication blockers on 2026-08-17 were
caused by index churn I created. Rebuilding an already-fresh index buys nothing and spends a
generation.

So: rebuild only if the index is older than MAX_AGE_HOURS, or if it is missing eligible memories
beyond STRANDED_LIMIT. Otherwise print the decision and exit without touching a byte. When the dream
is healthy this never fires; during a dream gap it holds the floor.

Eligibility mirrors the producer (bm25.read_docs skips SKIP_FILES and any slug starting "dream_"),
because counting raw *.md reports ~31 permanent phantom gaps and would make this rebuild forever.

Read-only unless it decides to rebuild. Exit 0 either way - a skip is a success.
"""
import glob, gzip, io, json, os, subprocess, sys, time

if any(a in ("-h", "--help") for a in sys.argv[1:]):
    # Discovery is not execution: this file does its work at module scope, so the
    # guard must come before the first statement that runs.
    print(__doc__ or os.path.basename(__file__))
    raise SystemExit(0)


HERE = os.path.dirname(os.path.abspath(__file__))
INDEX = os.path.join(HERE, "bm25_index.json.gz")
BUILDER = os.path.join(HERE, "bm25.py")
MEMDIR = os.path.expanduser("~/.claude/projects/{{BRAIN_SLUG}}/memory")
LOG = os.path.join(HERE, "bm25_refresh.log")

MAX_AGE_HOURS = 4.0     # the launcher fires every 3h; 4h means "a cycle was actually missed"
STRANDED_LIMIT = 60     # a normal working day writes ~100 memories between nightly rebuilds


def say(msg):
    line = "%s %s" % (time.strftime("%Y-%m-%dT%H:%M:%S"), msg)
    print(line)
    try:
        with io.open(LOG, "a", encoding="utf-8", newline="\n") as f:
            f.write(line + "\n")
    except Exception:
        pass


def stranded():
    """(indexed, eligible, stranded) or None when it cannot be determined."""
    try:
        sys.path.insert(0, HERE)
        from bm25 import SKIP_FILES
        # Read the bytes and let go of the index before parsing them. Parsing as it streams from the open file
        # held the index 9-13 s on this laptop under load (13 Oct 2026), and on Windows no publish can replace it
        # while anyone holds it.
        with open(INDEX, "rb") as fh:
            blob = fh.read()
        with gzip.GzipFile(fileobj=io.BytesIO(blob), mode="rb") as gz:
            with io.TextIOWrapper(gz, encoding="utf-8") as f:
                dl = json.load(f).get("dl") or {}
        del blob
        eligible = [s for s in (os.path.basename(p)[:-3] for p in glob.glob(os.path.join(MEMDIR, "*.md")))
                    if s not in SKIP_FILES and not s.startswith("dream_")]
        if not eligible:
            return None
        return len(dl), len(eligible), sum(1 for s in eligible if s not in dl)
    except Exception:
        return None


def main():
    if not os.path.exists(INDEX):
        say("REBUILD: no index present at %s" % INDEX)
    else:
        age_h = (time.time() - os.path.getmtime(INDEX)) / 3600.0
        cov = stranded()
        if cov is None:
            # Missing evidence is not permission to act, and not permission to skip either.
            # An unreadable index is itself a reason to rebuild.
            say("REBUILD: index age %.2fh but coverage UNKNOWN (index unreadable)" % age_h)
        else:
            indexed, eligible, miss = cov
            if age_h <= MAX_AGE_HOURS and miss <= STRANDED_LIMIT:
                say("skip: index age %.2fh <= %.1fh and %d stranded <= %d (%d/%d eligible) - "
                    "another writer is keeping it fresh, not spending a generation"
                    % (age_h, MAX_AGE_HOURS, miss, STRANDED_LIMIT, indexed, eligible))
                return 0
            why = []
            if age_h > MAX_AGE_HOURS:
                why.append("age %.2fh > %.1fh" % (age_h, MAX_AGE_HOURS))
            if miss > STRANDED_LIMIT:
                why.append("%d stranded > %d" % (miss, STRANDED_LIMIT))
            say("REBUILD: %s (%d/%d eligible indexed)" % ("; ".join(why), indexed, eligible))

    # DRY_RUN exists because testing the rebuild path would itself spend a generation - the exact
    # harm this script was written to avoid. A check you cannot exercise without side effects is a
    # check nobody runs, so the decision is verifiable independently of the action.
    if os.environ.get("BM25_REFRESH_DRY_RUN") == "1":
        say("DRY RUN: would rebuild now; no bytes touched")
        return 0

    p = subprocess.run([sys.executable, BUILDER, "build"], capture_output=True, timeout=1800)
    out = (p.stdout or b"").decode("utf-8", errors="replace").strip()
    err = (p.stderr or b"").decode("utf-8", errors="replace").strip()
    say("builder exit %s: %s" % (p.returncode, out or err or "(no output)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
