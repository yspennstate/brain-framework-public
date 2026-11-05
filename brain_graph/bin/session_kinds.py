"""session_kinds.py - whose session was this? One answer for every part of the brain that learns from use (27 Sep).

Since the recall benches started (24 Sep) most of what the brain logs as use has been tests, and each consumer found it
separately: the recall learner and the membrane (04:07), the usage record (04:51), the math brain's use layer (05:2x),
the use-grown graph store (not yet). A scheduled audit (account02/B13, 27 Sep) put it plainly: a rule that repaired one
consumer was not made a rule for its neighbours. This module is that rule; the consumers import it.

  his      an interactive session: a Claude transcript whose entrypoint is cli, or a Codex thread from cli or vscode
  test     a headless Claude session whose first prompt starts '[headless-worker]', the prefix our own tools give the
           workers they start: the recall and brain benches and their graders, the question writer (doc_expand), the
           query rewriter, the brain framework's project-memory reviewers. Their reads answer a tool's question, not a
           task of his (27 Sep, last 3 days: 2,365 such sessions against 33 of his and 431 other scripts)
  script   any other headless run: a Claude sdk-cli session (the dream, film workers, campaign solvers) or a Codex exec
           or subagent thread
  unknown  no record here - an evaluation harness's own id ('vib-<uuid>'), a Codex session this machine keeps no thread
           for, a transcript too new to show its entrypoint, or an 8-character key two sessions of different kinds share

Use is 'his' or 'script'. Keys are a whole session id or its first 8 characters (the touch events and the recall log key
sessions by 8); a Codex id begins with the clock, so threads started in the same minute share their first 8 characters,
and such a key resolves only when every session under it has the same kind. Settled kinds are cached in
live/session_kinds.json; 'unknown' never is.

    python session_kinds.py ID [ID ...]      the kind of each
    python session_kinds.py --stats [DAYS]   sessions by kind among the transcripts touched in the last DAYS (default 7)
"""

import sys
if __name__ == "__main__" and any(arg in ("-h", "--help") for arg in sys.argv[1:]):
    print(__doc__ or "No command-line options.")
    raise SystemExit(0)
import collections
import json
import os
import re
import sys
import time

HOME = os.path.expanduser("~")
PROJECTS = os.path.join(HOME, ".claude", "projects")
CODEX = os.path.join(HOME, ".codex")
CACHE = os.path.join(HOME, ".claude", "brain_graph", "live", "session_kinds.json")
TEST_PROMPT = "[headless-worker]"
WORK = ("his", "script")
_STATE = {}


_ENTRY_RAW = re.compile(r'"entrypoint"\s*:\s*"([^"]+)"')
_USER_RAW = re.compile(r'"type"\s*:\s*"user"')
_TEXT_RAW = re.compile(r'"(?:content|text)"\s*:\s*"((?:[^"\\]|\\.){0,80})')


PENDING_S = 600.0      # a headless transcript with no first prompt yet, quiet this long, never got one


def kind_from_head(head, settled=True):
    """his / test / script / unknown from the first lines of a Claude transcript (its first 64 KB).

    A headless session is test or script by its first prompt, so a head that shows the entrypoint but no prompt yet
    cannot say which. settled=False says the transcript may still be written: such a head is 'unknown', which is never
    cached, and the session is read again once its prompt is there. Until 29 Sep it was 'script' at once and cached for
    good, so a test worker read in that moment stayed 'script' (interactive audit S01, scheduled audit account02/B18
    r77, B18-R9-SESSION-KIND-PREMATURE-SCRIPT-CACHE-001; none of the 851 cached 'script' rows was one)."""
    entry, first = _head_parts(head)
    if entry == "cli":
        return "his"
    if first is not None and first.lstrip().startswith(TEST_PROMPT):
        return "test"
    if entry:
        return "script" if (first is not None or settled) else "unknown"
    return "unknown"


def _head_parts(head):
    """(entrypoint, first prompt text) from a transcript's head; either may be None."""
    entry, first = None, None
    for line in head.split("\n"):
        try:
            d = json.loads(line)
        except Exception:
            continue
        entry = entry or d.get("entrypoint")
        if first is None and d.get("type") == "user" and isinstance(d.get("message"), dict):
            c = d["message"].get("content")
            if isinstance(c, list):
                c = " ".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text")
            if c:
                first = str(c)
        if entry and first is not None:
            break
    if entry is None or first is None:
        # a first prompt longer than the head is cut mid-record and does not parse: the graders' packets run to 35 KB
        # after two short title records, and all 24 sessions this called 'unknown' on 27 Sep were such graders
        if entry is None:
            m = _ENTRY_RAW.search(head)
            entry = m.group(1) if m else None
        if first is None:
            for line in head.split("\n"):
                if _USER_RAW.search(line):
                    m = _TEXT_RAW.search(line)
                    if m:
                        first = m.group(1).replace('\\"', '"')
                    break
    return entry, first


def kind_from_transcript(path):
    """The kind of one Claude transcript. A headless session whose first prompt is not in the first 64 KB is read on to
    1 MB (a dream's queue and attachment records run past 64 KB before its prompt: 6 of the 851 cached 'script' rows);
    one still without a prompt is 'script' once the file has been quiet PENDING_S, and 'unknown' (read again) before."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            head = fh.read(65536)
            entry, first = _head_parts(head)
            if entry and entry != "cli" and first is None and len(head) == 65536:
                head += fh.read(1 << 20)
                entry, first = _head_parts(head)
        if entry and entry != "cli" and first is None:
            try:
                settled = time.time() - os.path.getmtime(path) > PENDING_S
            except OSError:
                settled = False
            return kind_from_head(head, settled=settled)
        return kind_from_head(head)
    except OSError:
        return "unknown"


def _known():
    """{whole session id: ('claude', transcript) or ('codex', kind)}, built once per process."""
    if "known" in _STATE:
        return _STATE["known"]
    known = {}
    try:
        for d in os.listdir(PROJECTS):
            dp = os.path.join(PROJECTS, d)
            if not os.path.isdir(dp):
                continue
            try:
                for f in os.listdir(dp):
                    if f.endswith(".jsonl") and len(f) == 42:
                        known.setdefault(f[:-6].lower(), ("claude", os.path.join(dp, f)))
            except OSError:
                continue
    except OSError:
        pass
    for _root, _dirs, files in os.walk(os.path.join(CODEX, "sessions")):
        for f in files:
            if f.startswith("rollout-") and f.endswith(".jsonl") and len(f) > 42:
                known.setdefault(f[-42:-6].lower(), ("codex", "script"))
    try:
        import sqlite3
        db = sqlite3.connect("file:%s?mode=ro" % os.path.join(CODEX, "state_5.sqlite").replace("\\", "/"),
                             uri=True, timeout=5)
        for tid, src in db.execute("select id, source from threads"):
            known[str(tid).lower()] = ("codex", "his" if src in ("cli", "vscode") else "script")
        db.close()
    except Exception:
        pass
    by8 = collections.defaultdict(list)
    for sid in known:
        by8[sid[:8]].append(sid)
    _STATE["known"], _STATE["by8"] = known, by8
    return known


def _cache():
    if "cache" not in _STATE:
        try:
            _STATE["cache"] = json.load(open(CACHE, encoding="utf-8"))
        except Exception:
            _STATE["cache"] = {}
        _STATE["dirty"] = False
    return _STATE["cache"]


def _kind_full(sid):
    known, cache = _known(), _cache()
    rec = known.get(sid)
    if rec is None:
        return "unknown"
    if rec[0] == "codex":
        return rec[1]
    k = cache.get(sid)
    if k is None:
        k = kind_from_transcript(rec[1])
        if k != "unknown":                 # a transcript too new to show its entrypoint is read again next time
            cache[sid] = k
            _STATE["dirty"] = True
    return k


def kind(sid):
    """The kind of a whole session id or of its first 8 characters."""
    s = str(sid or "").strip().lower()
    if not s:
        return "unknown"
    known = _known()
    if s in known:
        return _kind_full(s)
    if len(s) == 8:
        ks = {_kind_full(x) for x in _STATE["by8"].get(s, ())}
        return ks.pop() if len(ks) == 1 else "unknown"
    return "unknown"


def is_work(sid):
    return kind(sid) in WORK


def known_ids():
    """Every whole session id this machine keeps a record of (lower case)."""
    return list(_known())


def save():
    """Write the settled kinds back (call once at the end of a run; a failure only costs a re-read next time).
    Several processes settle kinds (run_graph, graph_store, live_delta, math_use), and each used to write its whole
    in-memory copy over the file, so one lost another's (scheduled audit account02/B15, 27 Sep). Now: a lock file
    serialises writers, the file on disk is re-read and merged (a settled kind never changes, so the merge is a union
    and the disk's value wins a disagreement), and the rename is retried while a reader has the file open."""
    if not _STATE.get("dirty"):
        return
    lock, fd = CACHE + ".lock", None
    try:
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        for _i in range(100):
            try:
                fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                break
            except FileExistsError:
                try:
                    if time.time() - os.path.getmtime(lock) > 30:      # a writer died holding it
                        os.remove(lock)
                        continue
                except OSError:
                    pass
                time.sleep(0.05)
        if fd is None:
            return
        try:
            with open(CACHE, encoding="utf-8") as fh:
                disk = json.load(fh)
        except Exception:
            disk = {}
        merged = dict(disk)
        for k, v in _STATE["cache"].items():
            merged.setdefault(k, v)
        tmp = CACHE + ".%d.tmp" % os.getpid()
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(merged, fh)
        for i in range(6):
            try:
                os.replace(tmp, CACHE)
                break
            except PermissionError:
                time.sleep(0.02 * (i + 1))
        else:
            os.remove(tmp)
            return
        _STATE["cache"] = merged
        _STATE["dirty"] = False
    except Exception:
        pass
    finally:
        if fd is not None:
            os.close(fd)
            try:
                os.remove(lock)
            except OSError:
                pass


def reset():
    """Forget what this process has read (tests point PROJECTS, CODEX and CACHE elsewhere, then call this)."""
    _STATE.clear()


def main(argv):
    if argv[:1] == ["--stats"]:
        days = float(argv[1]) if len(argv) > 1 else 7.0
        cut = time.time() - days * 86400
        c = collections.Counter()
        for sid, rec in _known().items():
            if rec[0] == "claude":
                try:
                    if os.path.getmtime(rec[1]) < cut:
                        continue
                except OSError:
                    continue
            c[(rec[0], _kind_full(sid))] += 1
        save()
        for (src, k), n in sorted(c.items()):
            print("%-7s %-8s %d" % (src, k, n))
        return 0
    for sid in argv:
        print(sid, kind(sid))
    save()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
