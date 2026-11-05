#!/usr/bin/env python3
"""build_retrieval_trace.py - reconstruct the brain's retrieval history from the session transcripts.

The one thing the brain cannot currently measure about itself is whether its accessibility signals
actually predict USE: salience, the consciousness tier, and the feeling-of-knowing each estimate "how
reachable is this memory," but the only ground truth - which memory got read, and when - was thrown away.
coactivation_end.py folds a session's co-reads into an aggregate weight and DELETES the per-session log,
and coactivation.tsv carries no timestamps. So there is no way to run the predictive eval the method
demands ("build the stable held-out eval before you tune") - does a signal computed at time T predict a
re-read after T.

This recovers it, read-only and retroactively, from the source of truth that was there all along: the
session transcripts (`~/.claude/projects/{{BRAIN_SLUG}}/**/*.jsonl`), which record every Read tool call
with a timestamp. Nested Claude subagents retain their parent `sessionId`, so their reads belong to that
same episode. It streams the files (never loads one whole; prints only counts, never memory content),
deduplicates only events with the same stable transcript identity, and writes a compact `retrievals.tsv`
(slug, precise epoch, iso, session) - one row per memory Read, sorted by time. No hot-path hook touched,
no unbounded log, full history available now instead of only going forward.

What it unlocks (all downstream, all measured against real use): base-level activation (ACT-R:
recency+frequency of retrieval, Anderson-Schooler), the predictive accessibility eval (salience/tier/FOK
at T -> re-read after T, AUROC), a held-out recall gold set from real co-reads, and the reliability
denominator. Read-only. With no arguments it retains all non-future history. `--days N` takes a finite
positive number and is anchored to the invocation's wall clock, not to the newest transcript event.
Usage: python build_retrieval_trace.py [--days N]

THE SCAN CACHE (13 Oct 2026). Reading every transcript on every run stopped fitting: 7,160 files and
67 GB, and the dream's 45-minute cap killed this step in both night dreams of 12-13 Oct with nothing
written, so retrievals.tsv stood a day old. Transcripts only grow, so each file's memory Reads are kept
in retrieval_trace_cache.json with the byte offset just past its last complete line, and a run reads
only what was appended since; a file whose size and mtime have not moved is not even opened. The
filters, the deduplication and the window are applied afresh on every run, so --days, --keep-tests and
the test-session rule never go stale in the cache. The cache key is the source of the extraction code,
so an edit there rebuilds it; a file that shrank or whose first 4 KB changed is read again from byte 0;
a line still being written is read on every run and never cached. A run that is stopped keeps what it
read (the cache is saved every minute of scanning), so the next run continues. The cache is the only
other file written. Only the live transcript root is cached unless build_rows is given a cache path.
"""
import argparse
import glob
import hashlib
import inspect
import io
import json
import math
import os
import sys
import time
from collections import Counter
from datetime import datetime, timezone

HOME = os.path.expanduser("~")
TDIR = os.path.join(HOME, ".claude", "projects", "{{BRAIN_SLUG}}")
MEMORY_ROOT = os.path.normpath(os.path.join(TDIR, "memory"))
OUT = os.path.join(HOME, ".claude", "dreaming", "retrievals.tsv")
CACHE = os.path.join(HOME, ".claude", "dreaming", "retrieval_trace_cache.json")
# same special-pages skip the graph/salience use, so the trace covers the same slug space
SKIP = {"MEMORY", "INDEX_ARCHIVE", "PREDICTIONS", "STATE_OF_MIND", "OPEN_QUESTIONS", "AUTOBIOGRAPHY",
        "MODEL_OF_PARTNER", "FASCINATIONS", "THE_OTHER_MINDS", "CONSCIENCE", "MISTAKES", "SALIENCE",
        "GROWTH", "STATS", "CURIOSITY", "RECONSOLIDATION_QUEUE"}
_NEEDLE = b'"Read"'                 # the rarer of the two strings a memory-Read line must hold
_NEEDLE2 = b'"tool_use"'
_CHUNK = 16 << 20
_HEAD = 4096                        # bytes of a file's start kept as its signature
_HEAD_FINAL = 4 * 65536             # a file this long has its first 64K characters on disk for good
_SAVE_SECONDS = 60.0
_LIVE_ROOT = object()               # build_rows default: cache only the live transcript root


class TraceIntegrityError(RuntimeError):
    """The source transcript set cannot support a lossless deterministic trace."""


def _epoch(ts):
    """ISO-8601 (with optional trailing Z) -> epoch seconds; None if unparseable."""
    if not ts or not isinstance(ts, str):
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        # Claude writes zoned timestamps. Treat a legacy naive value as UTC so a machine timezone
        # change cannot move the same transcript event across a held-out cutoff.
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        epoch = dt.timestamp()
        return epoch if math.isfinite(epoch) else None
    except Exception:
        return None


def _memory_slug(file_path):
    """Return a direct child of the exact live memory root, never a substring lookalike."""
    if not isinstance(file_path, str) or not file_path:
        return None
    candidate = os.path.expandvars(os.path.expanduser(file_path))
    if not os.path.isabs(candidate):
        candidate = os.path.join(HOME, candidate)
    candidate = os.path.normpath(candidate)
    if os.path.normcase(os.path.dirname(candidate)) != os.path.normcase(MEMORY_ROOT):
        return None
    name = os.path.basename(candidate)
    return name[:-3] if name.lower().endswith(".md") else None


def _reads_in_line(o, path, line_no):
    """Yield (slug, stable identity) for memory Reads on one transcript line.

    Tool-use ids are the strongest identity. Message UUID + content position is the safe fallback.
    When both are absent, retain source location so two indistinguishable but possibly real calls are
    never collapsed merely because their visible row fields happen to tie.
    """
    msg = o.get("message")
    content = msg.get("content") if isinstance(msg, dict) else None
    if not isinstance(content, list):
        return
    for index, c in enumerate(content):
        if isinstance(c, dict) and c.get("type") == "tool_use" and c.get("name") == "Read":
            inputs = c.get("input")
            if not isinstance(inputs, dict):
                continue
            fp = inputs.get("file_path", "") or ""
            slug = _memory_slug(fp)
            if slug:
                if c.get("id"):
                    identity = ("tool", str(c.get("id")))
                elif o.get("uuid"):
                    identity = ("message", str(o.get("uuid")), str(index))
                else:
                    identity = ("source", os.path.abspath(path), str(line_no), str(index))
                yield slug, identity


def _fallback_session(path, root):
    """Infer a parent session from either root/session.jsonl or root/session/subagents/..."""
    rel = os.path.relpath(path, root)
    parts = rel.split(os.sep)
    if len(parts) > 1:
        return parts[0]
    return os.path.splitext(parts[0])[0]


def _discover(root):
    """Return every Claude transcript deterministically, including nested subagents."""
    pattern = os.path.join(root, "**", "*.jsonl")
    return sorted(glob.iglob(pattern, recursive=True))


TEST_PROMPT = "[headless-worker]"


def _is_test_head(head, _sk):
    """True when a transcript head (its first 64K characters) is a bench test worker's."""
    if _sk is not None:
        return _sk.kind_from_head(head) == "test"
    for line in head.split("\n"):
        if '"user"' not in line:
            continue
        try:
            o = json.loads(line)
        except Exception:
            continue
        msg = o.get("message") if isinstance(o, dict) else None
        if o.get("type") != "user" or not isinstance(msg, dict):
            continue
        c = msg.get("content")
        if isinstance(c, list):
            c = " ".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text")
        if c:
            return str(c).lstrip().startswith(TEST_PROMPT)
    return False


def _session_kinds():
    try:
        # the rule lives in session_kinds.py, shared by every consumer of use (27 Sep, audit account02/B13); the loop
        # in _is_test_head is the same rule, kept only for a machine where that module cannot be imported
        sys.path.insert(0, os.path.join(os.path.expanduser("~"), ".claude", "brain_graph", "bin"))
        import session_kinds as _sk
    except Exception:
        _sk = None
    return _sk


def _test_sessions(root, memo=None):
    """Sessions whose first prompt starts '[headless-worker]': the recall and brain benches' test workers. Their reads
    are a test answering questions, not use (27 Sep: 446 of the trace's 2,790 reads, 120 of the 141 on 26 Sep), and
    counted as use they lift whatever memories the benches ask about. A session is its top-level transcript; its nested
    subagents carry the same session id.

    memo ({name: [size, mtime_ns, is_test]}, kept in the scan cache) spares the reopen: a head read from a file of
    _HEAD_FINAL bytes or more can no longer change, and a shorter file is read again whenever its size or mtime moves."""
    tests = set()
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return tests
    _sk = _session_kinds()
    for name in names:
        if not name.endswith(".jsonl"):
            continue
        path = os.path.join(root, name)
        st = None
        if memo is not None:
            try:
                st = os.stat(path)
            except OSError:
                continue
            m = memo.get(name)
            if m and (m[0] >= _HEAD_FINAL or (m[0] == st.st_size and m[1] == st.st_mtime_ns)):
                if m[2]:
                    tests.add(name[:-6])
                continue
        try:
            with io.open(path, encoding="utf-8", errors="replace") as fh:
                head = fh.read(65536)
        except OSError:
            continue
        is_test = _is_test_head(head, _sk)
        if is_test:
            tests.add(name[:-6])
        if memo is not None:
            memo[name] = [st.st_size, st.st_mtime_ns, is_test]
    if memo is not None:
        for name in set(memo) - set(names):
            del memo[name]
    return tests


def _positive_days(value):
    try:
        days = float(value)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError("must be a finite positive number")
    if not math.isfinite(days) or days <= 0:
        raise argparse.ArgumentTypeError("must be a finite positive number")
    return days


def _args(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=_positive_days, default=None,
                        help="retain events in the last positive N wall-clock days (default: all history)")
    parser.add_argument("--keep-tests", action="store_true",
                        help="keep the reads of benches' test workers (first prompt '[headless-worker]'); left out by default")
    return parser.parse_args(argv)


def _line_reads(raw, path, line_no, fallback_session, reads, bad):
    """One transcript line that holds _NEEDLE: append its memory Reads as [iso, session, slug, identity]."""
    if _NEEDLE2 not in raw:
        return
    try:
        obj = json.loads(raw.decode("utf-8", "replace"))
    except Exception:
        bad[0] += 1
        return
    if not isinstance(obj, dict):
        return
    iso = obj.get("timestamp") or ""
    session = str(obj.get("sessionId") or fallback_session)
    for slug, identity in _reads_in_line(obj, path, line_no):
        reads.append([iso, session, slug, list(identity)])


def _scan_file(path, e, fallback_session):
    """Read one transcript from where its cache entry stopped. Returns (entry, tail_reads, tail_bad): the
    entry covers every complete line; the tail is a last line still being written, read but never cached."""
    with io.open(path, "rb") as fh:
        size = os.fstat(fh.fileno()).st_size
        if e is not None and (e["done"] > size or e["hlen"] > size
                              or hashlib.sha256(fh.read(e["hlen"])).hexdigest()[:16] != e["head"]):
            e = None
        if e is None:
            e = {"done": 0, "lines": 0, "hlen": 0, "head": hashlib.sha256(b"").hexdigest()[:16], "bad": 0, "reads": []}
        reads, bad = list(e["reads"]), [e["bad"]]
        done, lines = e["done"], e["lines"]
        fh.seek(done)
        carry = b""
        while True:
            chunk = fh.read(_CHUNK)
            if not chunk:
                break
            buf = carry + chunk
            last = buf.rfind(b"\n")
            if last < 0:
                carry = buf
                continue
            pos = counted = 0
            seen = lines
            while True:
                i = buf.find(_NEEDLE, pos, last)
                if i < 0:
                    break
                ls = buf.rfind(b"\n", 0, i) + 1
                le = buf.find(b"\n", i)
                seen += buf.count(b"\n", counted, ls)
                counted = ls
                _line_reads(buf[ls:le], path, seen + 1, fallback_session, reads, bad)
                pos = le + 1
            lines = seen + buf.count(b"\n", counted, last + 1)
            done += last + 1
            carry = buf[last + 1:]
        hlen = min(done, _HEAD)
        if hlen != e["hlen"]:
            fh.seek(0)
            head = hashlib.sha256(fh.read(hlen)).hexdigest()[:16]
        else:
            head = e["head"]
    tail_reads, tail_bad = [], [0]
    if carry and _NEEDLE in carry:
        _line_reads(carry, path, lines + 1, fallback_session, tail_reads, tail_bad)
    return ({"done": done, "lines": lines, "hlen": hlen, "head": head, "bad": bad[0], "reads": reads},
            tail_reads, tail_bad[0])


def _cache_key(root):
    """Valid while the code that turns a line into cached reads is unchanged, for this root."""
    h = hashlib.sha256()
    for fn in (_memory_slug, _reads_in_line, _fallback_session, _line_reads, _scan_file):
        h.update(inspect.getsource(fn).encode("utf-8"))   # raises if the source cannot be read: no cache then
    h.update(repr((os.path.normcase(os.path.abspath(root)), MEMORY_ROOT, HOME, _NEEDLE, _NEEDLE2)).encode("utf-8"))
    return h.hexdigest()[:16]


def _kinds_key():
    """Valid while the test-session rule is unchanged."""
    h = hashlib.sha256()
    for fn in (_is_test_head, _test_sessions):
        h.update(inspect.getsource(fn).encode("utf-8"))
    _sk = _session_kinds()
    if _sk is not None:
        try:
            with io.open(_sk.__file__, "rb") as fh:
                h.update(fh.read())
        except Exception:
            h.update(b"session_kinds unreadable")
    h.update(repr((TEST_PROMPT, _HEAD_FINAL)).encode("utf-8"))
    return h.hexdigest()[:16]


def _load_cache(path):
    try:
        with io.open(path, encoding="utf-8") as fh:
            blob = json.load(fh)
        return blob if isinstance(blob, dict) else {}
    except Exception:
        return {}


def _save_cache(path, blob):
    """Atomic; a cache that cannot be written only costs the next run a longer read."""
    tmp = "%s.tmp.%d" % (path, os.getpid())
    try:
        with io.open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(blob, fh, separators=(",", ":"))
        os.replace(tmp, path)
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass


def _file_reads(root, stats, cache, state):
    """Yield (nested, fallback_session, reads) per transcript in _discover order, reads in file order as
    [iso, session, slug, identity]; counts transcript_files, nested_transcript_files, open_errors and bad_json.
    With a cache `state` a file is read only past where the last run stopped, and the state is saved every
    _SAVE_SECONDS of scanning and at the end."""
    old = dict(state["files"]) if state is not None else {}
    last_save = time.time()
    paths = _discover(root)
    for path in paths:
        stats["transcript_files"] += 1
        nested = os.path.dirname(os.path.abspath(path)) != os.path.abspath(root)
        if nested:
            stats["nested_transcript_files"] += 1
        fallback_session = _fallback_session(path, root)
        e = old.get(path)
        try:
            st = os.stat(path)
            if e and e["size"] == st.st_size and e["mtime"] == st.st_mtime_ns and e["done"] == st.st_size:
                tail_reads, tail_bad = [], 0
            else:
                e, tail_reads, tail_bad = _scan_file(path, e, fallback_session)
                e["size"], e["mtime"] = st.st_size, st.st_mtime_ns
                if state is not None:
                    state["files"][path] = e
                    if time.time() - last_save > _SAVE_SECONDS:
                        _save_cache(cache, state)
                        last_save = time.time()
        except OSError:
            stats["open_errors"] += 1
            continue
        stats["bad_json"] += e["bad"] + tail_bad
        yield nested, fallback_session, e["reads"] + tail_reads
    if state is not None:
        keep = set(paths)
        state["files"] = {p: x for p, x in state["files"].items() if p in keep}
        _save_cache(cache, state)


def build_rows(root=TDIR, days=None, as_of=None, keep_tests=False, cache=_LIVE_ROOT):
    """Build rows without writing them; `as_of` is an injectable wall-clock seam for evaluation.
    `cache`: a scan-cache path, None for a full read; by default only the live transcript root is cached."""
    if not os.path.isdir(root):
        raise FileNotFoundError("required transcript root missing: %s" % os.path.abspath(root))
    as_of = time.time() if as_of is None else float(as_of)
    if not math.isfinite(as_of):
        raise ValueError("as_of must be finite")
    if days is not None and (not math.isfinite(float(days)) or float(days) <= 0):
        raise ValueError("days must be finite and positive")
    cutoff = as_of - float(days) * 86400.0 if days is not None else None
    if cache is _LIVE_ROOT:
        cache = CACHE if os.path.normcase(os.path.abspath(root)) == os.path.normcase(os.path.abspath(TDIR)) else None

    state = None
    if cache:
        try:
            key, kinds_key = _cache_key(root), _kinds_key()
        except Exception:
            key = None
        if key is not None:
            blob = _load_cache(cache)
            state = {"key": key,
                     "files": dict(blob.get("files") or {}) if blob.get("key") == key else {},
                     "kinds_key": kinds_key,
                     "kinds": dict(blob.get("kinds") or {}) if blob.get("kinds_key") == kinds_key else {}}

    stats = Counter()
    events = {}                       # (session, identity) -> event tuple
    tests = set() if keep_tests else _test_sessions(root, state["kinds"] if state is not None else None)
    stats["test_sessions"] = len(tests)
    for nested, fallback_session, reads in _file_reads(root, stats, cache, state):
        for iso, session, slug, identity in reads:
            identity = tuple(identity)
            epoch = _epoch(iso)
            if slug in SKIP or slug.startswith("dream_"):
                stats["filtered_special"] += 1
                continue
            if session in tests or fallback_session in tests:
                stats["test_worker_reads"] += 1
                continue
            stats["scanned_reads"] += 1
            if epoch is None:
                stats["bad_timestamp_reads"] += 1
                continue
            if epoch > as_of:
                stats["future_reads"] += 1
                continue
            if cutoff is not None and epoch < cutoff:
                stats["too_old_reads"] += 1
                continue
            # A tool-use/message identity denotes one call even if a copied transcript record's
            # timestamp drifts slightly. Distinct identities at the same microsecond remain distinct.
            event_key = (session, identity)
            prior = events.get(event_key)
            event = (epoch, slug, iso, session, identity, nested)
            if prior is not None:
                if prior[1] != slug:
                    raise TraceIntegrityError(
                        "one transcript event identity maps to conflicting memory reads")
                stats["duplicate_reads"] += 1
                if prior[:3] != event[:3]:
                    stats["metadata_drift_reads"] += 1
                    if (event[0], event[2]) < (prior[0], prior[2]):
                        events[event_key] = event
                continue
            events[event_key] = event

    ordered = sorted(events.values(), key=lambda row: (row[0], row[1], row[2], row[3], row[4]))
    for row in ordered:
        stats["nested_rows" if row[5] else "root_rows"] += 1
    return [row[:4] for row in ordered], stats


def _write_trace(rows, path=OUT):
    """Atomically promote a microsecond-resolved trace; never leave a truncated ground-truth file."""
    tmp = "%s.tmp.%d" % (path, os.getpid())
    try:
        with io.open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write("slug\tepoch\tiso\tsession\n")
            for ep, slug, iso, session in rows:
                # Whole-second rounding fabricated alphabetical order for reads in the same second.
                f.write(f"{slug}\t{ep:.6f}\t{iso}\t{session}\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass


def main(argv=None, transcript_dir=None, output=None, as_of=None):
    args = _args(sys.argv[1:] if argv is None else argv)
    root = TDIR if transcript_dir is None else transcript_dir
    out = OUT if output is None else output
    if not os.path.isdir(root):
        print("build_retrieval_trace: required transcript root missing: %s" % os.path.abspath(root))
        return 2
    try:
        rows, stats = build_rows(root=root, days=args.days, as_of=as_of, keep_tests=args.keep_tests)
    except TraceIntegrityError as exc:
        print("build_retrieval_trace: source integrity failure; prior trace preserved:", exc)
        return 2
    if not stats["transcript_files"]:
        print("build_retrieval_trace: no transcript files found; prior trace preserved")
        return 2
    if stats["open_errors"]:
        print("build_retrieval_trace: %d transcript file(s) unreadable; prior trace preserved" %
              stats["open_errors"])
        return 2
    try:
        _write_trace(rows, out)
    except Exception as e:
        print("build_retrieval_trace: write failed:", e)
        return 1

    # stats only (no memory content in output)
    slugs = set(r[1] for r in rows)
    sessions = set(r[3] for r in rows)
    span = (rows[-1][0] - rows[0][0]) / 86400 if len(rows) > 1 else 0.0
    top = Counter(r[1] for r in rows).most_common(5)
    print(f"build_retrieval_trace: {len(rows)} memory-read events, {len(slugs)} distinct memories, "
          f"{len(sessions)} sessions, {span:.1f}-day span -> {out}")
    print("  scanned: %d transcript files (%d nested); rows root=%d nested=%d; "
          "deduplicated=%d; metadata drift=%d; future=%d; bad timestamps=%d" % (
              stats["transcript_files"], stats["nested_transcript_files"], stats["root_rows"],
              stats["nested_rows"], stats["duplicate_reads"], stats["metadata_drift_reads"],
              stats["future_reads"], stats["bad_timestamp_reads"],
    ))
    print("  most-retrieved:", ", ".join(f"{s}({n})" for s, n in top))
    print("  left out: %d reads by %d test-worker sessions (--keep-tests keeps them)" % (
        stats["test_worker_reads"], stats["test_sessions"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
