"""Pull memories other computers filed onto this one. Insert-only.

TOP10 rank 2, 3 Nov 2026: a memory filed on the research box and on ai-memories main at 19:2x +0200 was still absent on
the other laptop 25 minutes later, and the other laptop found no task that pulls 01_ai_consciousness/claude/memory from main at all
(FM-20261103-125851-the other laptop). This closes that path.

This laptop already runs online_memory_pull.py (task BrainOnlineMemoryPull, every 15 min) and the Mac its launchd
twin com.brain.online-memory-pull; those stay the pulls there. memory_pull.py is the small portable version for a
computer that has neither (the other laptop on 3 Nov): no hard-coded paths, no clone, only fleet_board's GitHub auth.

What it does: lists the memory folder on ai-memories main with one tree call, compares it with this computer's
memory folder (the folder spread_recall.py names, or BRAIN_MEM_DIR), and writes each file that is MISSING here,
newest slug date first, at most --max per run (default 200). A file that already exists here is never touched, so a
local edit is never overwritten and nothing is ever deleted. Then it kicks the bm25 refresh so recall sees the new
files within seconds. Dry run by default; --apply writes. MEMORY.md index lines are not added: recall reads the files.

    python3 memory_pull.py                 what would arrive
    python3 memory_pull.py --apply         write up to 200 missing files, refresh recall
    python3 memory_pull.py --max 50 --apply
"""

import sys
if __name__ == "__main__" and any(arg in ("-h", "--help") for arg in sys.argv[1:]):
    print(__doc__ or "No command-line options.")
    raise SystemExit(0)

import base64, json, os, re, subprocess, sys, time
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fleet_board as F  # its api() carries this computer's GitHub auth

REPO = "yspennstate/ai-memories-and-functionality"
FOLDER = "01_ai_consciousness/claude/memory/"
NAME = re.compile(r"^[a-z0-9][a-z0-9_.\-]{2,200}\.md$")
SKIP = {"MEMORY.md", "INDEX_ARCHIVE.md", "MISTAKES.md"}   # indexes and shared logs are each computer's own
STATE = os.path.join(os.path.expanduser("~"), ".claude", "fleet_board", "memory_pull_state.json")


def mem_dir():
    if os.environ.get("BRAIN_MEM_DIR"):
        return os.environ["BRAIN_MEM_DIR"]
    try:
        import spread_recall
        return spread_recall.MEM
    except Exception:
        return None


def remote_names():
    """Every file in the memory folder on main, from ONE tree listing (the folder's tree sha comes from its parent)."""
    parent, leaf = FOLDER.rstrip("/").rsplit("/", 1)
    st, items = F.api("GET", "/repos/%s/contents/%s?ref=main" % (REPO, parent), timeout=30)
    if st != 200 or not isinstance(items, list):
        raise RuntimeError("parent listing failed: %s" % st)
    sha = next((i["sha"] for i in items if i.get("name") == leaf and i.get("type") == "dir"), None)
    if not sha:
        raise RuntimeError("memory folder not on main")
    st, tree = F.api("GET", "/repos/%s/git/trees/%s" % (REPO, sha), timeout=60)
    if st != 200 or "tree" not in tree:
        raise RuntimeError("tree listing failed: %s" % st)
    if tree.get("truncated"):
        print("warning: the tree listing was truncated; some files will wait for a later run")
    return {e["path"]: e["sha"] for e in tree["tree"] if e.get("type") == "blob"}


def main():
    a = sys.argv[1:]
    apply = "--apply" in a
    cap = int(a[a.index("--max") + 1]) if "--max" in a else 200
    mem = mem_dir()
    if not mem or not os.path.isdir(mem):
        sys.exit("memory folder not found (set BRAIN_MEM_DIR)")
    state = {}
    try:
        state = json.load(open(STATE, encoding="utf-8"))
    except Exception:
        pass
    now = datetime.now(timezone.utc)
    files = remote_names()
    local = set(os.listdir(mem))
    missing = sorted(n for n in files if NAME.match(n) and n not in SKIP and n not in local)
    print("memory folder:", mem)
    print("%d memory files on main, %d here, %d on main and missing here" % (len(files), len(local), len(missing)))
    # newest first by the date in the slug (most memories end _YYYY_MM_DD), so a capped run takes the fresh ones
    dated = lambda n: (re.search(r"(\d{4})_(\d\d)_(\d\d)\.md$", n) or re.search(r"()()()$", n)).groups()
    missing.sort(key=dated, reverse=True)
    if cap and len(missing) > cap:
        print("taking the newest %d this run (--max); the rest come on later runs" % cap)
        missing = missing[:cap]
    wrote = 0
    for n in missing:
        print(("  pull " if apply else "  would pull ") + n)
        if not apply:
            continue
        st, r = F.api("GET", "/repos/%s/contents/%s%s?ref=main" % (REPO, FOLDER, n), timeout=30)
        if st != 200 or "content" not in r:
            print("    fetch failed:", st)
            continue
        body = base64.b64decode(r["content"])
        if not body.startswith(b"---"):
            print("    skipped: no frontmatter")
            continue
        path = os.path.join(mem, n)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)   # never overwrite a local file
        except FileExistsError:
            continue
        with os.fdopen(fd, "wb") as fh:
            fh.write(body)
        wrote += 1
    if apply:
        os.makedirs(os.path.dirname(STATE), exist_ok=True)
        state.update(last=now.isoformat(), wrote=wrote, at=time.strftime("%Y-%m-%d %H:%M %z"))
        json.dump(state, open(STATE, "w", encoding="utf-8"))
        if wrote:
            bm = os.path.join(HERE, "bm25.py")
            if os.path.exists(bm):
                try:
                    subprocess.run([sys.executable, bm, "refresh"], capture_output=True, timeout=600,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                except Exception as e:
                    print("bm25 refresh failed: %r" % e)
        print("wrote %d" % wrote)


if __name__ == "__main__":
    main()
