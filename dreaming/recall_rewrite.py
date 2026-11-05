"""recall_rewrite.py - brain.py recall with a cheap rewrite first, for a question worded the way he asks.

The brain's search is lexical, so a question in his words misses the note about half the time. Measured 25-26 Sep
(memory a_cheap_rewrite_before_recall_finds_the_note_far_more_often_2026_09_25): three Haiku 4.5 rewrites, each in the
words a note answering the question would use, plus the question itself put the source memory in the top five of one
of the four searches for 132 of 300 reworded questions, against 104 for the question alone (+9 points; top twenty +14),
under a cent a question. This runs the same four searches and interleaves them - the first result of each, then the
second of each, and so on - so its first twenty lines are the four top-fives the measurement counted when the lists
share no memory; a memory two lists share is shown once, and the twenty lines then reach below a list's top five
(each list is searched ten deep at -n 20; scheduled audit account02/B08 auto11, 27 Sep).
Read at the same number of lines, though (27 Sep, brain_graph/results/REWRITE_FUSED_EQUAL_LINES_2026-09-27.json), the
interleaved list is a wash against plain recall - 109 against 105 at five lines, 150 against 149 at twenty - so it is a
second look, not a better first search: after a plain recall missed, its twenty lines held the note 25 times in 151.

Cost: one headless Haiku call on the subscription (--safe-mode, no tools, 5-10 s) and four recalls. Use it when a plain
recall misses or the question is in his everyday words; brain.py recall itself is unchanged. Every use is logged to
~/.claude/brain_graph/live/recall_rewrite_log.jsonl.

    python recall_rewrite.py "question" [-n 20] [--rewrites "a|b|c"] [--no-model]
"""
import argparse
import concurrent.futures
import json
import os
import re
import subprocess
import sys
import time

HOME = os.path.expanduser("~")
BRAIN = os.path.join(HOME, ".claude", "dreaming", "brain.py")
# Windows ships claude.exe; the Mac has plain claude (the .exe path made every rewrite fail there until 3 Nov)
CLAUDE = os.path.join(HOME, ".local", "bin", "claude.exe" if os.name == "nt" else "claude")
LOG = os.path.join(HOME, ".claude", "brain_graph", "live", "recall_rewrite_log.jsonl")
WORK = (r"{{USERPROFILE}}\recall4_rewrite_work" if os.name == "nt"
        else os.path.join(HOME, ".claude", "brain_graph", "live", "recall_rewrite_work"))
ROW = re.compile(r"^\s+([0-9]*\.?[0-9]+)\s+\[([^\]]*)\]\s+(\S+)\s*$")
NOWIN = 0x08000000 if os.name == "nt" else 0          # CREATE_NO_WINDOW: never a console flash
PROMPT = """[headless-worker] Someone keeps a large private collection of short technical notes about their own projects
(software, experiments, simulations, films, measurements, incidents). They asked the question below in everyday words.
Write THREE different search queries that would find the note answering it, each worded the way such a note would
likely be worded: concrete technical terms, names of tools, files, settings or processes, and numbers or dates if the
question implies them. One query per line, no numbering, no quotes, nothing else.

QUESTION: {q}"""


def rewrite(q, timeout=120):
    """Three rewrites from Haiku 4.5, exactly as the measurement made them; ([], error) when the call fails."""
    os.makedirs(WORK, exist_ok=True)
    cmd = [CLAUDE, "-p", "--safe-mode", "--model", "claude-haiku-4-5-20251001", "--output-format", "json",
           "--tools", "", "--strict-mcp-config", "--max-budget-usd", "0.2"]
    try:
        p = subprocess.run(cmd, input=PROMPT.format(q=q), cwd=WORK, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout, creationflags=NOWIN)
        text = json.loads(p.stdout).get("result") or ""
    except Exception as e:
        return [], "%s: %s" % (type(e).__name__, str(e)[:120])
    lines = [re.sub(r"^\s*(?:[-*\d.)]+\s*)?", "", ln).strip().strip('"') for ln in text.splitlines()]
    return [ln for ln in lines if len(ln) > 3][:3], None


def search(q, n):
    """brain.py recall's ranked rows for one query: [(slug, score, lanes, description)]."""
    try:
        p = subprocess.run([sys.executable, "-X", "utf8", BRAIN, "recall", q, "-n", str(n)], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=240, creationflags=NOWIN)
        out = p.stdout or ""
    except subprocess.TimeoutExpired:
        return []
    rows, last = [], None
    for line in out.splitlines():
        m = ROW.match(line)
        if m:
            last = [m.group(3), float(m.group(1)), m.group(2), ""]
            rows.append(last)
        elif last is not None and line.startswith("        ") and not last[3]:
            last[3] = line.strip()
    return rows


def interleave(lists, n):
    """First of each list, then the second of each, ... - the union of the top-k's in rank order."""
    out, seen = [], set()
    for i in range(max((len(x) for x in lists), default=0)):
        for j, lst in enumerate(lists):
            if i < len(lst) and lst[i][0] not in seen:
                seen.add(lst[i][0])
                out.append((lst[i], j, i + 1))
                if len(out) >= n:
                    return out
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("question")
    ap.add_argument("-n", type=int, default=20)
    ap.add_argument("--rewrites", default=None, help="'a|b|c' instead of asking the model (tests, or your own words)")
    ap.add_argument("--no-model", action="store_true", help="the question's own search only")
    a = ap.parse_args()
    t0 = time.time()
    err = None
    if a.rewrites is not None:
        rw = [x.strip() for x in a.rewrites.split("|") if x.strip()][:3]
    elif a.no_model:
        rw = []
    else:
        rw, err = rewrite(a.question)
    queries = [a.question] + rw
    per = max(5, -(-a.n // max(1, len(queries))) + 5)
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(queries)) as ex:
        lists = list(ex.map(lambda q: search(q, per), queries))
    fused = interleave(lists, a.n)
    print("recall+rewrite: %r" % a.question)
    for j, q in enumerate(rw, 1):
        print("  rewrite %d: %s" % (j, q))
    if err:
        print("  (no rewrite - %s; this is the plain recall)" % err)
    for k, ((slug, score, lanes, desc), j, r) in enumerate(fused, 1):
        src = "question" if j == 0 else "rewrite %d" % j
        print("  %2d. [%s #%d; %s] %s" % (k, src, r, lanes, slug))
        if desc:
            print("        " + desc)
    if os.environ.get("RECALL_REWRITE_NO_LOG") == "1":     # evaluations: bank questions stay out of the live log
        return 0
    try:
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"t": round(time.time(), 1), "q": a.question[:300], "rewrites": rw, "error": err,
                                 "shown": [[s[0], j, r] for s, j, r in fused], "seconds": round(time.time() - t0, 1)},
                                separators=(",", ":")) + "\n")
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
