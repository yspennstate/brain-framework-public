"""Dream-cycle growth step: rebuild the live event stream from the deployment's
current records and fold everything new into the network. The dream cycle calls
this once per night so the graph keeps pace with what agents actually used, wrote,
and recalled since the last dream.

Standing task (from the owner directive): the network must keep growing from real
use - new tools invoked, new prompts written, new keywords and memories created -
not stay frozen at its first build. This is the mechanism that honors that. It
adds only NEW events (past the store's water mark), so it is cheap and idempotent.

  python dream_grow.py            # uses the default live paths
  python dream_grow.py --db <db> --home <home>

Runs at low priority; safe to call every dream.
"""
import argparse, json, os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import adapt_live
import grow

RAN_STAMP = os.path.join(HERE, ".grow_ran.json")   # value-birth marker for hook_liveness:
# net.sqlite3's own mtime is re-stamped by mere READERS (any NetStore connect - measured
# 2026-07-15 when a read-only eval re-stamped it), so the db mtime is freshness theater.
# This stamp is written only when grow actually RAN.


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--home", default=os.path.expanduser("~"))
    ap.add_argument("--db", default=os.path.join(HERE, "net.sqlite3"))
    ap.add_argument("--slug", default=None,
                    help="memory-corpus project slug (default: infer C--Users-<user>)")
    a = ap.parse_args(argv)

    try:
        import ctypes
        ctypes.windll.kernel32.SetPriorityClass(
            ctypes.windll.kernel32.GetCurrentProcess(), 0x00004000)  # BelowNormal
    except Exception:
        pass

    # the memory-corpus project slug is the FULL project dir with ':' and separators
    # replaced by '-' (the transcript-dir convention: ~ -> {{BRAIN_SLUG}}),
    # NOT just the home basename - getting this wrong silently skips prompts and memory.
    if a.slug:
        slug = a.slug
    else:
        slug = a.home.replace(":", "-").replace("\\", "-").replace("/", "-")
    mem = os.path.join(a.home, ".claude", "projects", slug, "memory")
    prompts = os.path.join(mem, "prompts")
    if not os.path.isdir(mem):
        # fall back: find the one project dir that actually has a memory/ tree
        proj = os.path.join(a.home, ".claude", "projects")
        if os.path.isdir(proj):
            for cand in sorted(os.listdir(proj)):
                if os.path.isdir(os.path.join(proj, cand, "memory")):
                    slug = cand
                    mem = os.path.join(proj, slug, "memory")
                    prompts = os.path.join(mem, "prompts")
                    break
    outcomes = os.path.join(a.home, ".claude", "dreaming", "outcomes.jsonl")
    retr = os.path.join(a.home, ".claude", "dreaming", "retrievals.tsv")
    ev = os.path.join(os.path.dirname(a.db), "events_full.jsonl")

    args = ["--out", ev]
    if os.path.isdir(prompts):
        args = ["--prompts-dir", prompts] + args
    if os.path.exists(outcomes):
        args = ["--outcomes", outcomes] + args
    if os.path.isdir(mem):
        args = ["--memory-dir", mem] + args
    if os.path.exists(retr):
        args = ["--retrievals", retr] + args
    adapt_live.main(args)

    if not os.path.exists(a.db):
        # first ever run: full build
        import build_graph, episodes
        build_graph.main(["--db", a.db, "--events", ev])
        episodes.main(["--db", a.db, "--events", ev])
        print("dream_grow: first full build complete")
    else:
        grow.main(["--db", a.db, "--events", ev])
    try:
        with open(RAN_STAMP, "w", encoding="utf-8") as f:
            json.dump({"ran": time.strftime("%Y-%m-%dT%H:%M:%S"), "db": a.db}, f)
    except Exception:
        pass   # the stamp is telemetry; growth itself already succeeded
    return 0


if __name__ == "__main__":
    sys.exit(main())
