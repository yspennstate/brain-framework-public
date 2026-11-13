"""keyword_corpus_cache.py - incremental inputs for build_keyword_nodes.py.

Why (2026-11-03): the dream's keyword step re-read and re-tokenized all ~49k memory files
(first 4,000 chars each) and re-parsed the whole 200 MB PROMPTS.jsonl every run. Warm and unstarved that is
~15 s of CPU (11-02 12:02 run), but the dream runs BelowNormal while the owner is present and a BelowNormal
job on a full box gets almost no CPU: the step hit its 2700 s cap on 10-31 and 11-03 and was killed, leaving
the keyword layer a generation behind. A day adds ~5,750 memory files and edits ~5 (measured 11-03), so
almost all of that work repeats yesterday's.

What this keeps, under ~/.claude/dreaming/cache/keyword_nodes/:
  corpus.sqlite   one row per memory file: path, mtime_ns, size, its search text, its token counts
                  (so an edited or vanished file can be SUBTRACTED from the running vocabulary)
  agg.pickle      the running vocabulary Counter and the prompt-term Counter with the prompt file's
                  byte offset and a hash of the 64 KB before it (PROMPTS.jsonl is append-only; if the
                  tail hash does not match, the whole file is re-read)

Results are bit-identical to the full read: `python build_keyword_nodes.py --verify` builds both ways and
compares the digests that go into the generation's source manifest. Any doubt (version change, a corrupt
cache, a failed check) falls back to the full read, which is the old code path unchanged.
"""
import hashlib
import io
import json
import os
import pickle
import re
import sqlite3
from collections import Counter

VERSION = 1
CACHE = os.path.join(os.path.expanduser("~"), ".claude", "dreaming", "cache", "keyword_nodes")
TAIL = 65536


def _counts_blob(counter):
    return pickle.dumps(dict(counter), protocol=4)


def _sub(total, counts):
    for t, n in counts.items():
        left = total.get(t, 0) - n
        if left > 0:
            total[t] = left
        else:
            total.pop(t, None)


def _read_entry(path, slug, token_re):
    """Exactly what build_keyword_nodes._corpus reads for one file: (text, Counter) or None."""
    try:
        body = io.open(path, encoding="utf-8", errors="replace").read(4000)
    except OSError:
        return None
    desc = ""
    m = re.search(r'description:\s*"?(.+?)"?\s*\n', body)
    if m:
        desc = m.group(1)
    text = (slug + " " + desc).lower()
    return text, Counter(t.lower() for t in token_re.findall(body))


def _load_agg():
    try:
        with open(os.path.join(CACHE, "agg.pickle"), "rb") as f:
            agg = pickle.load(f)
        if agg.get("version") == VERSION:
            return agg
    except Exception:
        pass
    return None


def _save_agg(agg):
    os.makedirs(CACHE, exist_ok=True)
    tmp = os.path.join(CACHE, "agg.pickle.tmp")
    with open(tmp, "wb") as f:
        pickle.dump(agg, f, protocol=4)
    os.replace(tmp, os.path.join(CACHE, "agg.pickle"))


def corpus(bases, skip, token_re):
    """Incremental twin of build_keyword_nodes._corpus(): returns (texts, vocab, stats)."""
    os.makedirs(CACHE, exist_ok=True)
    agg = _load_agg() or {"version": VERSION}
    vocab = agg.get("vocab")
    db = sqlite3.connect(os.path.join(CACHE, "corpus.sqlite"))
    db.execute("CREATE TABLE IF NOT EXISTS f (path TEXT PRIMARY KEY, mtime_ns INTEGER, size INTEGER,"
               " text TEXT, counts BLOB)")
    db.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)")
    row = db.execute("SELECT v FROM meta WHERE k='nonce'").fetchone()
    if vocab is None or row is None or row[0] != agg.get("nonce"):
        # no trustworthy running total (first run, version change, or a crash between the two saves):
        # start the table over so the counts can never drift from the files
        db.execute("DELETE FROM f")
        vocab = Counter()
    known = {p: (m, s) for p, m, s in db.execute("SELECT path, mtime_ns, size FROM f")}
    seen, order = set(), []
    added = changed = removed = 0
    for base in bases:
        try:
            ents = list(os.scandir(base))
        except OSError:
            continue
        for e in ents:
            name = e.name
            if not name.endswith(".md") or name[:-3] in skip:
                continue
            try:
                st = e.stat()
            except OSError:
                continue
            path = e.path
            fp = (st.st_mtime_ns, st.st_size)
            old = known.get(path)
            if old == fp:
                seen.add(path)
                order.append(path)
                continue
            if old is not None:            # edited: subtract what it used to contribute
                row = db.execute("SELECT counts FROM f WHERE path=?", (path,)).fetchone()
                _sub(vocab, pickle.loads(row[0]))
                db.execute("DELETE FROM f WHERE path=?", (path,))
                changed += 1
            got = _read_entry(path, name[:-3], token_re)
            if got is None:                # unreadable now: absent, exactly as the full read treats it
                continue
            text, counts = got
            vocab.update(counts)
            db.execute("INSERT OR REPLACE INTO f VALUES (?,?,?,?,?)",
                       (path, fp[0], fp[1], text, _counts_blob(counts)))
            if old is None:
                added += 1
            seen.add(path)
            order.append(path)
    for path in set(known) - seen:         # vanished or now unreadable: take it out
        row = db.execute("SELECT counts FROM f WHERE path=?", (path,)).fetchone()
        if row:
            _sub(vocab, pickle.loads(row[0]))
        db.execute("DELETE FROM f WHERE path=?", (path,))
        removed += 1
    text_of = dict(db.execute("SELECT path, text FROM f"))
    nonce = os.urandom(8).hex()
    agg["vocab"] = vocab
    agg["nonce"] = nonce
    _save_agg(agg)                          # pickle first, then the table commit carries the same nonce:
    db.execute("INSERT OR REPLACE INTO meta VALUES ('nonce', ?)", (nonce,))
    db.commit()                             # a crash between them leaves the nonces unequal -> full reset
    db.close()
    texts = {}
    for path in order:                     # same order as the full read, so a slug in both dirs resolves the same way
        texts[os.path.basename(path)[:-3]] = text_of[path]
    return texts, Counter(vocab), {"added": added, "changed": changed, "removed": removed, "files": len(order)}


def prompt_terms(path, token_re):
    """Incremental twin of the PROMPTS.jsonl half of build_keyword_nodes._prompt_terms().

    Returns (Counter, read_any, stats). read_any mirrors the original: True once any prompt text was seen."""
    agg = _load_agg() or {"version": VERSION}
    st = agg.get("prompts") or {}
    try:
        size = os.path.getsize(path)
    except OSError:
        return Counter(), False, {"mode": "missing"}
    counts, offset, read_any = Counter(st.get("counts") or {}), int(st.get("offset") or 0), bool(st.get("read_any"))
    mode = "append"
    if offset == 0 or offset > size or not st.get("tail_sha"):
        mode, counts, offset, read_any = "full", Counter(), 0, False
    else:
        with open(path, "rb") as f:
            f.seek(max(0, offset - TAIL))
            if hashlib.sha256(f.read(offset - max(0, offset - TAIL))).hexdigest() != st["tail_sha"]:
                mode, counts, offset, read_any = "full", Counter(), 0, False
    with open(path, "rb") as f:
        f.seek(offset)
        data = f.read()
    # whole lines go into the stored total; a last line with no newline yet counts for THIS run only
    cut = data.rfind(b"\n") + 1

    def _eat(chunk, into):
        any_ = False
        for raw in chunk.split(b"\n"):
            if not raw.strip():
                continue
            try:
                row = json.loads(raw.decode("utf-8", errors="replace"))
            except Exception:
                continue
            if not isinstance(row, dict):
                continue
            text = row.get("text") or row.get("prompt") or ""
            if isinstance(text, str) and text:
                any_ = True
                into.update(t.lower() for t in token_re.findall(text))
        return any_
    read_any = _eat(data[:cut], counts) or read_any
    out, out_any = counts, read_any
    if data[cut:].strip():
        out = Counter(counts)
        out_any = _eat(data[cut:], out) or read_any
    new_off = offset + cut
    with open(path, "rb") as f:
        f.seek(max(0, new_off - TAIL))
        tail_sha = hashlib.sha256(f.read(new_off - max(0, new_off - TAIL))).hexdigest()
    agg = _load_agg() or {"version": VERSION}
    agg["prompts"] = {"counts": dict(counts), "offset": new_off, "tail_sha": tail_sha, "read_any": read_any}
    _save_agg(agg)
    return out, out_any, {"mode": mode, "bytes": cut}
