"""doc_expand.py - write down, for each memory, the questions it answers, in plain words (26 Sep, claude-825b1305).

The brain's search is lexical: a question in his everyday words misses the note written in technical words about half
the time, and a rewrite of the QUESTION closes part of that gap (+9 points at top 5, at a model call per search). This
closes it from the other side, once per memory: a small model reads a memory's own description and opening and writes
three questions a person might ask whose answer is in it, in ordinary words; those questions are added to the text the
word index sees. It never sees any test question. Known in retrieval work as document expansion (doc2query).

    python doc_expand.py --slugs FILE [--batch 40] [--workers 3]   # writes study/doc_expansion/expansions.jsonl
    python doc_expand.py --new --out STORE [--max 400]              # every memory the store lacks (the hourly job)

Resumable: a slug already in the output is skipped. Every call is headless Haiku 4.5 on the subscription (--safe-mode,
no tools, windowless).
"""
import argparse
import concurrent.futures
import datetime
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import threading
import time

HOME = os.path.expanduser("~")
MEM = os.path.join(HOME, ".claude", "projects", (os.environ.get("BRAIN_SLUG") or os.path.expanduser("~").replace(":", "-").replace(chr(92), "-").replace("/", "-")), "memory")
OUT_DIR = os.path.join(HOME, ".claude", "brain_graph", "study", "doc_expansion")
OUT = os.path.join(OUT_DIR, "expansions.jsonl")
CLAUDE = os.path.join(HOME, ".local", "bin", "claude.exe")
WORK = r"{{USERPROFILE}}\doc_expand_work"
NOWIN = 0x08000000 if os.name == "nt" else 0
LOCK = threading.Lock()
PROMPT = """[headless-worker] Below are short notes from a private collection about someone's own projects (software,
experiments, simulations, films, trading, incidents). For each note, write three different questions a person might ask
whose answer is in that note. Write them the way a busy person would type them: plain everyday words, not the note's own
technical terms where an ordinary word would do, no file names unless the question is about the file. Output exactly one
JSON object per line and nothing else, in the same order as the notes:
{{"id": "<id>", "q": ["<question 1>", "<question 2>", "<question 3>"]}}

{notes}"""


def note_text(slug, limit=700):
    try:
        raw = io.open(os.path.join(MEM, slug + ".md"), encoding="utf-8", errors="replace").read()
    except OSError:
        return None
    desc = ""
    m = re.match(r"^---(.*?)---", raw, re.S)
    if m:
        d = re.search(r"^description:\s*(.+)$", m.group(1), re.M)
        desc = d.group(1).strip().strip('"') if d else ""
        raw = raw[m.end():]
    body = re.sub(r"\s+", " ", re.sub(r"[`*#>\[\]|]", " ", raw)).strip()
    return (desc + " " + body)[:limit].strip() or None


# Explicit recovery order: no Claude agents before 15 Oct 23:45 local.
# Keep the schedule enabled and existing expansion data intact; resume after expiry.
OWNER_CLAUDE_HOLD_UNTIL = datetime.datetime.fromisoformat("2026-10-15T23:45:00+03:00").timestamp()


def owner_claude_hold(now=None):
    return (time.time() if now is None else now) < OWNER_CLAUDE_HOLD_UNTIL


def ask(batch):
    """One Haiku call for [(slug, text)]; returns {slug: [q1, q2, q3]} for the notes it answered."""
    if owner_claude_hold():
        return {}, None, "owner hold: no Claude calls before 2026-10-15T23:45:00+03:00"
    ids = {"n%03d" % i: slug for i, (slug, _t) in enumerate(batch, 1)}
    notes = "\n".join("[id=n%03d] %s" % (i, t) for i, (_s, t) in enumerate(batch, 1))
    cmd = [CLAUDE, "-p", "--safe-mode", "--model", "claude-haiku-4-5-20251001", "--output-format", "json",
           "--tools", "", "--strict-mcp-config", "--max-budget-usd", "0.5"]
    os.makedirs(WORK, exist_ok=True)
    try:
        p = subprocess.run(cmd, input=PROMPT.format(notes=notes), cwd=WORK, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=600, creationflags=NOWIN)
        res = json.loads(p.stdout)
        text, cost = res.get("result") or "", res.get("total_cost_usd")
    except Exception as e:
        return {}, None, "%s: %s" % (type(e).__name__, str(e)[:160])
    out = {}
    for line in text.splitlines():
        line = line.strip().strip(",")
        if not line.startswith("{"):
            continue
        try:
            o = json.loads(line)
        except ValueError:
            continue
        slug = ids.get(str(o.get("id")))
        qs = [str(q).strip() for q in (o.get("q") or []) if str(q).strip()][:3]
        if slug and qs:
            out[slug] = qs
    if not out:                          # 26 Sep 23:13: the usage window ran out and 23 batches came back "0 answered"
        return out, cost, "no answers: " + " ".join(text.split())[:140]
    return out, cost, None


def text_sha(text):
    """The digest of the text the question-writer saw (note_text), kept with each row since 27 Sep."""
    return hashlib.sha256((text or "").encode("utf-8", "replace")).hexdigest()[:16]


def new_slugs(done, settle_s=120):
    """(new, changed): memory files the word index sees (bm25's own rule) that the store has no questions for, and
    those whose text changed after their questions were written, each oldest first; a file written in the last two
    minutes is left for the next run (it may still be being written). `done` is {slug: (written_at, sha or None)},
    the store's newest row per slug, which is the row bm25 uses. Changed means the text the writer sees now has a
    different digest from the row's; a row from before 27 Sep has no digest, and then a file modified after the row
    counts as changed. Until 27 Sep a memory edited after its questions kept them (8 of 17,428 that morning, all
    edited within the day; account03/B02's R4 audit). Until 28 Sep 04:1x the time test still ran first for a row with
    a digest too, so an edit made while the questions were being written, or within a minute after the row, was never
    compared (account01/B02 auto11, B02-R4-SHA-INVALIDATION-MTIME-DEADZONE): the digest now decides every row that has
    one, which costs one read of each settled memory per run (15.6 s for 17,546 on 28 Sep, once an hour)."""
    sys.path.insert(0, os.path.join(HOME, ".claude", "dreaming"))
    import bm25
    now, new, changed = time.time(), [], []
    with os.scandir(MEM) as it:
        for e in it:
            slug = bm25._indexable(e.name)
            if slug is None:
                continue
            try:
                mt = e.stat().st_mtime
            except OSError:
                continue
            if now - mt < settle_s:
                continue
            if slug not in done:
                new.append((mt, slug))
                continue
            t, sha = done[slug]
            if sha is not None:                              # the digest decides
                if text_sha(note_text(slug)) != sha:
                    changed.append((mt, slug))
                continue
            if mt > t + 60:                                  # a row from before 27 Sep: time is all it has
                changed.append((mt, slug))
    return [s for _m, s in sorted(new)], [s for _m, s in sorted(changed)]


def take_lock(store, stale_s=3600):
    """One writer per store: O_EXCL lock file with the pid; a lock older than an hour belongs to a dead run."""
    lock = store + ".lock"
    try:
        if time.time() - os.path.getmtime(lock) > stale_s:
            os.remove(lock)
    except OSError:
        pass
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return lock
    except OSError:
        return None


def main():
    global OUT
    ap = argparse.ArgumentParser()
    ap.add_argument("--slugs", help="a file with one memory slug per line")
    ap.add_argument("--new", action="store_true", help="every memory the store has no questions for")
    ap.add_argument("--out", default=None, help="the store to append to (default: the study file)")
    ap.add_argument("--max", type=int, default=400, help="--new: at most this many memories per run")
    ap.add_argument("--batch", type=int, default=40)
    ap.add_argument("--workers", type=int, default=3)
    a = ap.parse_args()
    if bool(a.slugs) == bool(a.new):
        ap.error("give exactly one of --slugs FILE or --new")
    if owner_claude_hold():
        print("owner hold: no Claude calls before 2026-10-15T23:45:00+03:00; no output changed", flush=True)
        return
    if a.out:
        OUT = os.path.abspath(a.out)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    lock = take_lock(OUT)
    if lock is None:
        print("%s another run holds %s.lock - nothing to do" % (time.strftime("%Y-%m-%d %H:%M:%S"), OUT))
        return
    try:
        _run(a)
    finally:
        try:
            os.remove(lock)
        except OSError:
            pass


def _run(a):
    done = {}                             # slug -> (written_at, sha or None) of its newest row, the one bm25 uses
    if os.path.exists(OUT):
        for line in open(OUT, encoding="utf-8"):
            try:
                o = json.loads(line)
                done[o["slug"]] = (float(o.get("t") or 0.0), o.get("sha"))
            except (ValueError, KeyError, TypeError):
                continue
    if a.new:
        fresh, changed = new_slugs(done)
        want = (fresh + changed)[:a.max]
        print("%s --new: %d memories without questions, %d changed since theirs (store has %d)" % (
            time.strftime("%Y-%m-%d %H:%M:%S"), len(fresh), len(changed), len(done)), flush=True)
        if not want:
            return
    else:
        want = [s.strip() for s in open(a.slugs, encoding="utf-8") if s.strip() and s.strip() not in done]
    items = [(s, note_text(s)) for s in want]
    items = [(s, t) for s, t in items if t]
    batches = [items[i:i + a.batch] for i in range(0, len(items), a.batch)]
    print("to expand: %d memories in %d calls (%d already done)" % (len(items), len(batches), len(done)), flush=True)
    t0, n_ok, spent = time.time(), 0, 0.0

    def run(b):
        got, cost, err = ask(b)
        seen = dict(b)
        with LOCK:
            with open(OUT, "a", encoding="utf-8") as fh:
                for slug, qs in got.items():
                    fh.write(json.dumps({"slug": slug, "q": qs, "t": round(time.time(), 1),
                                         "sha": text_sha(seen.get(slug))}) + "\n")
        return len(got), len(b), cost, err
    empty = [0]

    def run_guarded(b):
        if empty[0] >= 3:                 # three empty batches in a row: the model is not answering, stop spending calls
            return 0, len(b), 0.0, "skipped after three empty batches in a row"
        res = run(b)
        empty[0] = empty[0] + 1 if res[0] == 0 else 0
        return res
    with concurrent.futures.ThreadPoolExecutor(max_workers=a.workers) as ex:
        for k, (ok, n, cost, err) in enumerate(ex.map(run_guarded, batches), 1):
            n_ok += ok
            spent += cost or 0.0
            print("call %d/%d: %d of %d answered%s (%.0f s, cost so far %.2f)" % (
                k, len(batches), ok, n, (" - " + err) if err else "", time.time() - t0, spent), flush=True)
    print("done: %d memories expanded, %.0f s, reported cost %.2f%s" % (
        n_ok, time.time() - t0, spent, " - STOPPED EARLY, rerun later (resumable)" if empty[0] >= 3 else ""))


if __name__ == "__main__":
    main()
