"""live_delta.py - the brain graph's fast layer: what the hours since the last store build taught it (27 Sep).

Design goal: the graph is live (no stale snapshot between rebuilds) and temporal (every edge carries when it was seen). The store (graph_store.py) is rebuilt every six hours from a capture that ran hourly, so the membrane
read edges up to seven hours old and knew nothing of a file, folder or memory first touched since the last build (at
17:2x, 43,518 events in the log were newer than the store). This layer holds exactly what the store does not yet have:
the touch events appended to events_touch.jsonl after the store was built, turned into context -> target edges by the
store's own rule. A target is something a session needed (a memory read, a failed call, a sampled tool, the first touch
of a file, folder, project, repository or host); every distinct item of the same session in the 600 s before it adds to
item -> target, an error also to item -> err:ANY, and the store's hubs add nothing. Each edge and node carries the time
it was first and last seen, and its weight fades with the store's half-life from the moment it happened, so the
membrane can put both layers on one clock: the store's counts faded to the moment asked, plus these.

Rebuilt from scratch on every run from the log's tail (a few MB), so nothing drifts or counts twice: the six-hourly
build folds these events into the store, and the next run starts over on the new base. His corrections are not here
(the store takes them from events_verdicts.jsonl only, and they run in shadow). Co-activation pairs are not here either
(only the attractors and the recall walk read them, and those follow the store).

Written to live/delta_<stamp>.sqlite and named by live/delta.json, which is replaced atomically, so a hook holding the
previous file open keeps reading it. Only the newest three files are kept: they are rebuilt from the events and are
never the only copy of anything.

    python live_delta.py update      # after every capture (run_graph.py fast, every five minutes; and hourly)
    python live_delta.py stats
    python live_delta.py selftest
"""
import sys as _sys
if __name__ == "__main__" and any(_a in ("-h", "--help") for _a in _sys.argv[1:]):
    # Asking for help must not run the script's real work.
    print((__doc__ or "").strip() or "usage: python " + _sys.argv[0])
    raise SystemExit(0)
import collections
import glob
import json
import math
import os
import re
import sqlite3
import sys
import time
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
HOME = os.path.join(os.path.expanduser("~"), ".claude", "brain_graph")
LIVE = os.path.join(HOME, "live")
EVENTS = os.path.join(HOME, "events_touch.jsonl")
STATUS = os.path.join(HOME, "STATUS.json")
SIDECAR = os.path.join(LIVE, "sidecar.sqlite")
POINTER = os.path.join(LIVE, "delta.json")
KINDS_CACHE = os.path.join(LIVE, "session_kinds.json")
CONFIG = os.path.join(HOME, "build_config.json")
VERSION = 1

# the store's rule (graph_store.build), repeated here so a reader of one file sees the whole of it
TARGET_KINDS = ("mem", "err", "tool", "file", "dir", "proj", "repo", "host")   # verdicts: events_verdicts.jsonl only
FIRST_TOUCH = ("file", "dir", "proj", "repo", "host")
JUNK = re.compile(r"[$%{}*?<>|`\\]")
ERR_ANY = "err:ANY"
WINDOW = 600.0
K_CTX = 40
TOOL_RATE = 0.1
BANK_RX = re.compile(r"(?i)bank_recall_keys_quarantine|recall4|bank100|bank_rep300|rep300|bank_h2|bank_h1|recall_four_arms|"
                     r"mq_bench|rewrite_probe|cloud_credit_2026|memory_questions|recall_bank|bank_recall")
EXCLUDE_KINDS = ("file", "dir", "cmd", "store", "proj", "repo")
EXCLUDE_WINDOW = 3600.0
COVER = WINDOW + EXCLUDE_WINDOW  # how far back of a new event its count can depend on: a context event within the
                                 # window, and a bank touch within an hour of that one
LOOKBACK_MAX = 256 << 20      # the most context read before the delta's start; past it the read withholds (read_events)
KEEP_FILES = 3
# targets kept per item and kind: twice the sidecar's caps (live_sidecar.KIND_CAP), ranked by PMI x ln(1 + count) over
# the fast layer's own totals - fresh counts are thin, so the margin is wider; the membrane rescores on both layers
KIND_CAP = {"mem": 160, "err": 50, "dir": 50, "proj": 30, "tool": 30, "file": 30, "repo": 10, "host": 10, "verdict": 6}


def log(msg):
    os.makedirs(os.path.join(HOME, "logs"), exist_ok=True)
    with open(os.path.join(HOME, "logs", "live.log"), "a", encoding="utf-8") as f:
        f.write("%s delta: %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))


def _json(path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default


class SidecarBehind(Exception):
    """The store directory holds another generation than the one the sidecar was built from."""


def base():
    """The store the sidecar was built from: its last event time, build time, hubs, and the half-life of its edges.

    The generation comes from the sidecar and the hubs, half-life and first touches from the store directory, so the two
    must be the same build. They differ when a build swapped a new store in and the sidecar then failed to publish: a
    delta built on that pair would declare the old generation while reading the new store's hubs, and open_delta would
    accept it over the old sidecar (29 Sep).
    SidecarBehind is raised instead, and update() builds no delta until the sidecar is republished."""
    db = sqlite3.connect("file:%s?mode=ro" % SIDECAR.replace("\\", "/"), uri=True, timeout=10)
    try:
        meta = {k: json.loads(v) for k, v in db.execute("SELECT k, v FROM meta")}
    finally:
        db.close()
    store = meta.get("store") or os.path.join(HOME, "store")
    nm = _json(os.path.join(store, "nodes.json"), {}).get("meta", {})
    for mine, theirs in (("store_built", "built"), ("store_until", "until")):
        if not _same(meta.get(mine), nm.get(theirs)):
            raise SidecarBehind("the sidecar was built from the store of %s %r, the store directory holds %r" % (
                theirs, meta.get(mine), nm.get(theirs)))
    hl = nm.get("half_life_days")
    if hl is None:
        hl = _json(CONFIG, {}).get("half_life_days")
    return {"until": float(meta.get("store_until") or nm.get("until") or 0.0),
            "store_built": float(meta.get("store_built") or nm.get("built") or 0.0),
            "sidecar_built": float(meta.get("built") or 0.0), "hubs": set(nm.get("hubs") or []),
            "half_life_s": float(hl) * 86400.0 if hl else 0.0, "kinds": meta.get("kinds") or
            ["dir", "err", "file", "host", "mem", "proj", "repo", "tool", "verdict"], "store": store}


PREFIX_MARK = 1 << 20           # bytes hashed at each end of the store's prefix of the event log


def prefix_marks(path, nbytes):
    """sha256 of the first and of the last megabyte of the event log's first nbytes, or None (the log is shorter than
    nbytes, or unreadable). run_graph's build records them beside the byte count it consumed; start_offset trusts that
    count only while both still match (27 Sep)."""
    import hashlib
    try:
        nbytes = int(nbytes)
        if nbytes <= 0 or os.path.getsize(path) < nbytes:
            return None
        with open(path, "rb") as f:
            head = hashlib.sha256(f.read(min(PREFIX_MARK, nbytes))).hexdigest()
            at = max(0, nbytes - PREFIX_MARK)
            f.seek(at)
            tail = hashlib.sha256(f.read(nbytes - at)).hexdigest()
        return head, tail
    except (OSError, ValueError):
        return None


def prefix_sha(path, nbytes):
    """sha256 of the event log's whole first nbytes, or None (shorter or unreadable). The two end marks miss a line
    changed in the middle at the same length (28 Sep); hashing all 569 MB takes about 0.6 s, so the
    build records this and start_offset checks it on every run."""
    import hashlib
    try:
        nbytes = int(nbytes)
        if nbytes <= 0 or os.path.getsize(path) < nbytes:
            return None
        h = hashlib.sha256()
        with open(path, "rb") as f:
            left = nbytes
            while left > 0:
                blk = f.read(min(1 << 24, left))
                if not blk:
                    return None
                h.update(blk)
                left -= len(blk)
        return h.hexdigest()
    except (OSError, ValueError):
        return None


def start_offset(b, path=EVENTS):
    """Where the store's events end in the log. run_graph.build records the log's size as it starts reading
    (STATUS build_events_bytes, with the store's own build time beside it); before that record existed, the start is
    found by reading back from the end until a whole block is an hour older than the store's last event, and the delta
    is then the events after that last event."""
    st = _json(STATUS, {})
    nb, bt = st.get("build_events_bytes"), st.get("build_store_built")
    size = os.path.getsize(path)
    if nb and bt and abs(float(bt) - b["store_built"]) < 5.0 and 0 < int(nb) <= size:
        full = st.get("build_events_sha")
        if full:                                  # the whole prefix, since 28 Sep
            same = prefix_sha(path, int(nb)) == full
        else:                                     # a STATUS from before: its two end marks, or nothing to check
            want = (st.get("build_events_head_sha"), st.get("build_events_tail_sha"))
            same = not all(want) or prefix_marks(path, int(nb)) == want
        if same:
            return int(nb), "bytes"
        log("delta: the event log's first %d bytes no longer match the store's record (rewritten or re-derived since "
            "the build); the start is found by time" % int(nb))
    off = size
    with open(path, "rb") as f:
        while off > 0:
            off = max(0, off - (4 << 20))
            f.seek(off)
            blk = f.read(4 << 20).split(b"\n")[1:-1]
            ts = []
            for ln in blk[:: max(1, len(blk) // 300)]:
                try:
                    ts.append(float(json.loads(ln)["ts"]))
                except Exception:
                    pass
            if ts and max(ts) < b["until"] - 3600.0:
                break
    return off, "ts"


def kind_resolver(kinds_cache=KINDS_CACHE):
    """session -> kind, by the store's own rule. The touch events key a session by its first 8 characters and the
    settled cache keys whole ids, so looking the event's key up in the cache never matched and every test worker
    reached the fast layer (27 Sep: 23,098 events, 9.6 percent of the log since
    26 Sep 17:29, from 131 test sessions). With the default cache this is session_kinds.kind, which resolves an
    8-character key only when every session under it has one kind, exactly as graph_store.build calls it; a test that
    points kinds_cache elsewhere gets the same resolution over that file alone. Memoised per session."""
    memo = {}
    if kinds_cache == KINDS_CACHE:
        try:
            import session_kinds as sk

            def kind_of(s):
                s = str(s).strip().lower()
                if s not in memo:
                    memo[s] = sk.kind(s)
                return memo[s]
            kind_of.save = sk.save
            return kind_of
        except Exception:
            pass
    table = {str(k).lower(): v for k, v in _json(kinds_cache, {}).items()}
    by8 = collections.defaultdict(set)
    for k, v in table.items():
        by8[k[:8]].add(v)

    def kind_of(s):
        s = str(s).strip().lower()
        if s in table:
            return table[s]
        ks = by8.get(s) if len(s) == 8 else None
        return next(iter(ks)) if ks and len(ks) == 1 else "unknown"
    kind_of.save = lambda: None
    return kind_of


MARK_STEP = 4 << 20            # one running-maximum mark per 4 MiB of the event log
MARKS = os.path.join(LIVE, "ts_marks.json")
TS_RX = re.compile(rb'\{"ts": (-?[0-9]+(?:\.[0-9]+)?)[,}]')


def _block_max(body, ceiling):
    """The largest ts among the complete lines of body, leaving out any above ceiling: no line is written before its
    own time, so a ts in the future is a bad record, and letting one set the maximum would hide the log behind it.
    Lines the fast pattern does not read are parsed one by one."""
    got = TS_RX.findall(body)
    if len(got) == body.count(b"\n"):
        vals = [float(x) for x in got]
    else:
        vals = []
        for ln in body.split(b"\n"):
            if ln:
                try:
                    vals.append(float(json.loads(ln)["ts"]))
                except Exception:
                    pass                          # unreadable: every reader of the log skips it
    vals = [v for v in vals if v <= ceiling]
    return max(vals) if vals else None


def ts_marks(path, upto, sha=None, cache=None):
    """[(offset, maximum)]: line boundaries in the log's first `upto` bytes, about one per 4 MiB, each with the largest
    ts of every line before it. A line is written after its own time and the log only grows, so no line with ts >= T
    lies before the last boundary whose maximum is below T, in whatever order the capture appended.

    cache: a file keeping the marks with the prefix they describe (its length and sha256). They are reused when that
    prefix still hashes the same, extended over what was appended since, and computed afresh otherwise. sha: the
    sha256 of the first `upto` bytes when the caller has verified it already (start_offset has, in bytes mode)."""
    got = _json(cache, {}) if cache else {}
    marks, rm = [(0, float("-inf"))], float("-inf")
    if got.get("version") == 1 and got.get("step") == MARK_STEP and got.get("marks"):
        u0, s0 = int(got.get("upto") or 0), got.get("sha")
        old = [(int(o), float(m)) for o, m in got["marks"]]
        if u0 == upto and sha and s0 == sha:
            return old
        if s0 and u0 > 0 and prefix_sha(path, u0) == s0:
            marks = [x for x in old if x[0] <= upto] or marks
            rm = marks[-1][1]
    ceiling = time.time() + 86400.0
    off = marks[-1][0]
    with open(path, "rb") as f:
        f.seek(off)
        carry, left = b"", upto - off
        while left > 0:
            chunk = f.read(min(MARK_STEP, left))
            if not chunk:
                break
            left -= len(chunk)
            buf = carry + chunk
            cut = buf.rfind(b"\n") + 1
            body, carry = buf[:cut], buf[cut:]
            if body:
                m = _block_max(body, ceiling)
                if m is not None and m > rm:
                    rm = m
                off += len(body)
                marks.append((off, rm))
    if cache:
        rec = {"version": 1, "step": MARK_STEP, "upto": upto, "sha": sha or prefix_sha(path, upto),
               "built": time.time(), "marks": marks}
        tmp = cache + ".%d.tmp" % os.getpid()
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(rec, fh)
            if not _replace(tmp, cache):
                os.remove(tmp)
        except OSError:
            pass
    return marks


def context_start(marks, start, need, most=None):
    """(offset, floor): where the context read begins, and the largest ts of any line before it. The offset is the
    latest mark at or before start whose maximum is below need, when that lies within `most` bytes; otherwise the
    earliest mark within reach, whose floor is then at or above need and a caller must withhold what it cannot see."""
    most = LOOKBACK_MAX if most is None else most
    cand = [x for x in marks if x[0] <= start] or [(0, float("-inf"))]
    ok = [x for x in cand if x[1] < need]
    if ok and ok[-1][0] >= start - most:
        return ok[-1]
    reach = [x for x in cand if x[0] >= start - most]
    return reach[0] if reach else cand[-1]


def new_region(path, start, kind_of):
    """(earliest ts, sessions) of the countable lines from start on - the lines a bytes-mode read calls new. A line
    that began before start is not one of them."""
    first, ss = None, set()
    with open(path, "rb") as f:
        if start > 0:
            f.seek(start - 1)
            if f.read(1) != b"\n":
                f.readline()                      # the rest of a line that began before start
        for raw in f:
            if not raw.endswith(b"\n"):
                break
            try:
                e = json.loads(raw)
                kd, nid, s = e.get("kind"), e.get("id"), e.get("session")
                if not kd or nid is None or not s or kd == "verdict" or JUNK.search(str(nid)) or kind_of(s) == "test":
                    continue
                ts = float(e["ts"])
            except Exception:
                continue
            ss.add(s)
            first = ts if first is None else min(first, ts)
    return first, ss


def read_events(b, path=EVENTS, kinds_cache=KINDS_CACHE, start=None, mode=None, most=None):
    """[(session, ts, node, new)] from the log's tail: new=True for events the store has not seen; the rest are context.
    A last line still being written is left for the next run. Test-worker sessions and every event within an hour of a
    touch of the recall banks (same session) are left out, as the store leaves them out.

    Context: a new event's count depends on its session's events up to COVER seconds before it. Until 29 Sep the read
    began a fixed 8 MiB before the start, which a burst of other sessions, or a session captured days late, pushes
    that context out of (interactive audit IA-20260928-05; the log is far from time-ordered - 70 percent of its lines
    were appended over an hour after a later-stamped line, one run five days late - and 82 of 100 past store cuts
    needed more than 8 MiB, median 24 MB). Now the read starts at the latest mark of the running maximum (ts_marks)
    below the earliest new event minus COVER, which provably holds everything those windows need, and parses only the
    sessions that have new events. At most `most` bytes (256 MiB) are read back; a new event that the read cannot
    prove covered is kept as context and not counted - the next store build counts it - and the read record says how
    many (withheld_new, context_floor)."""
    live = start is None and path == EVENTS
    if start is None:
        start, mode = start_offset(b, path)
    kind_of = kind_resolver(kinds_cache)
    ctx_start, floor, sessions = 0, float("-inf"), None
    if start:
        if mode == "bytes":
            first, sessions = new_region(path, start, kind_of)
            need, upto, sha = (first if first is not None else float("inf")) - COVER, start, None
            if live:
                st = _json(STATUS, {})
                if int(st.get("build_events_bytes") or -1) == start:
                    sha = st.get("build_events_sha")     # start_offset verified these bytes against it just now
        else:                                    # new is by time here, so a new line may sit anywhere in the log
            need, upto, sha = b["until"] - COVER, os.path.getsize(path), None
        marks = ts_marks(path, upto, sha=sha, cache=MARKS if live else None)
        ctx_start, floor = context_start(marks, upto, need, most)
    sb = {s.encode("utf-8") for s in sessions} if sessions is not None else None
    out, marks = [], collections.defaultdict(list)
    n_bad = n_rep = n_skip = 0
    seen_ev = set()
    with open(path, "rb") as f:
        pos = ctx_start
        if ctx_start:
            f.seek(ctx_start - 1)
            if f.read(1) != b"\n":
                pos += len(f.readline())          # a partial line
        for raw in f:
            if not raw.endswith(b"\n"):
                break                             # still being written: read on the next run (and not in "end")
            at = pos
            pos += len(raw)
            if sb is not None and at < start:     # context of a session with no new event changes nothing
                i = raw.find(b'"session": "')
                j = raw.find(b'"', i + 12) if i >= 0 else -1
                if j > i + 12 and b"\\" not in raw[i + 12:j] and raw[i + 12:j] not in sb:
                    n_skip += 1
                    continue
            try:
                e = json.loads(raw)
            except Exception:
                n_bad += 1
                continue
            kd, nid, s = e.get("kind"), e.get("id"), e.get("session")
            if not kd or nid is None or not s or kd == "verdict" or JUNK.search(str(nid)):
                continue
            if kind_of(s) == "test":
                continue
            ts = float(e["ts"])
            key = (s, ts, kd + ":" + str(nid))
            if key in seen_ev:                    # an exact repeat counts once, as in graph_store.build (28 Sep)
                n_rep += 1
                continue
            seen_ev.add(key)
            new = at >= start if mode == "bytes" else ts > b["until"]
            if kd in EXCLUDE_KINDS and BANK_RX.search(str(nid)):
                marks[s].append(ts)
            out.append((s, ts, kd + ":" + str(nid), new))
    if marks:
        for s in marks:
            marks[s].sort()

        def near(s, ts):
            v = marks.get(s)
            if not v:
                return False
            import bisect
            k = bisect.bisect_left(v, ts)
            return (k < len(v) and v[k] - ts <= EXCLUDE_WINDOW) or (k > 0 and ts - v[k - 1] <= EXCLUDE_WINDOW)
        out = [x for x in out if not near(x[0], x[1])]
    withheld = 0
    if floor + COVER >= min((x[1] for x in out if x[3]), default=float("inf")):
        # the read could not reach back far enough for these: a line before ctx_start may carry a ts up to floor, so a
        # new event within COVER of it may have context the read never saw; it stays context, and the store counts it
        kept = []
        for x in out:
            if x[3] and x[1] <= floor + COVER:
                x = (x[0], x[1], x[2], False)
                withheld += 1
            kept.append(x)
        out = kept
        if live:
            log("delta: %d new events withheld - their context lies more than %d MiB before the start (a line up to %s "
                "sits before byte %d)" % (withheld, (most or LOOKBACK_MAX) >> 20,
                                          time.strftime("%m-%d %H:%M", time.localtime(floor)), ctx_start))
    try:
        kind_of.save()                        # kinds settled here are kept (session_kinds.save merges under a lock)
    except Exception:
        pass
    return out, {"start": start, "mode": mode, "ctx_start": ctx_start, "end": pos, "bad_json": n_bad, "repeats": n_rep,
                 "context_floor": floor if floor > float("-inf") else None, "context_sessions": len(sessions)
                 if sessions is not None else None, "context_skipped": n_skip, "withheld_new": withheld}


def keep_tool(s, ts, x, rate=None):
    """A tenth of tool targets, by a hash of session, time and name: graph_store.keep_tool, the store's own rule since 27
    Sep, so the tools kept here are the tools the next store keeps (before, the store drew from a seeded generator)."""
    rate = TOOL_RATE if rate is None else rate
    return (zlib.crc32(("%s|%.3f|%s" % (s, ts, x)).encode("utf-8")) % 10000) < rate * 10000


def pairs_of(events, hubs, now, half_life_s, first_seen=None):
    """The store's rule on the new events. Returns (edges, uses, out_w, in_w, total_w): edges[(src, dst)] =
    [weight, first time, last time]; a weight is 0.5 ** ((now - t) / half-life) (1 without a half-life). first_seen:
    {session: [nodes]} the store wrote in first_touch.json - what each session touched before the store's last event,
    so a target touched long before the context read is a repeat here as it is in the store."""
    by = collections.defaultdict(list)
    for s, ts, x, new in events:
        by[s].append((ts, x, new))
    edges, uses = {}, collections.defaultdict(list)
    out_w, in_w = collections.defaultdict(float), collections.defaultdict(float)
    total = 0.0
    for s, evs in by.items():
        evs.sort(key=lambda r: r[0])
        seen = set(first_seen.get(s) or ()) if first_seen else set()
        for j, (t, x, new) in enumerate(evs):
            kd = x.split(":", 1)[0]
            is_t = kd in TARGET_KINDS and (kd not in FIRST_TOUCH or x not in seen)
            seen.add(x)
            if not is_t or not new:
                continue
            uses[x].append(t)
            if kd == "tool" and not keep_tool(s, t, x):
                continue
            ctx, got = [], set()
            i = j - 1
            while i >= 0 and t - evs[i][0] <= WINDOW and len(ctx) < K_CTX:
                y = evs[i][1]
                if evs[i][0] < t and y not in hubs and y not in got:
                    got.add(y)
                    ctx.append(y)
                i -= 1
            if not ctx:
                continue
            w = 0.5 ** (max(0.0, now - t) / half_life_s) if half_life_s else 1.0
            dsts = (x, ERR_ANY) if kd == "err" else (x,)
            for c in ctx:
                for d in dsts:
                    e = edges.get((c, d))
                    if e is None:
                        edges[(c, d)] = [w, t, t]
                    else:
                        e[0] += w
                        e[1] = min(e[1], t)
                        e[2] = max(e[2], t)
                    out_w[c] += w
                    in_w[d] += w
                    total += w
    return edges, uses, out_w, in_w, total


def write_db(path, edges, uses, out_w, in_w, total, kinds, meta):
    if os.path.exists(path):
        os.remove(path)
    db = sqlite3.connect(path)
    db.executescript("""
    PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;
    CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT);
    CREATE TABLE dnode(id INTEGER PRIMARY KEY, name TEXT UNIQUE, kind TEXT, out_w REAL, in_w REAL, uses TEXT,
                       t_first REAL, t_last REAL);
    CREATE TABLE dedge(src INTEGER, dst INTEGER, k INTEGER, cnt REAL, t_first REAL, t_last REAL);
    """)
    names = set(out_w) | set(in_w) | set(uses)
    ids = {n: i for i, n in enumerate(sorted(names))}
    k_of = {k: i for i, k in enumerate(kinds)}
    first = collections.defaultdict(lambda: float("inf"))
    last = collections.defaultdict(lambda: float("-inf"))
    groups = collections.defaultdict(list)
    for (c, d), (w, t0, t1) in edges.items():
        kd = d.split(":", 1)[0]
        k = k_of.get(kd)
        if k is None or out_w.get(c, 0.0) <= 0 or in_w.get(d, 0.0) <= 0 or total <= 0:
            continue
        pmi = math.log((w / out_w[c]) / (in_w[d] / total))
        groups[(c, kd)].append((pmi * math.log1p(w), c, d, k, w, t0, t1))
    rows, n_cut = [], 0
    for (c, kd), xs in groups.items():
        xs.sort(key=lambda r: -r[0])
        cap = KIND_CAP.get(kd, 30)
        n_cut += max(0, len(xs) - cap)
        for _sc, c_, d, k, w, t0, t1 in xs[:cap]:
            rows.append((ids[c_], ids[d], k, round(w, 6), round(t0, 3), round(t1, 3)))
            for n in (c_, d):
                first[n] = min(first[n], t0)
                last[n] = max(last[n], t1)
    for n, ts in uses.items():
        if ts:
            first[n] = min(first[n], min(ts))
            last[n] = max(last[n], max(ts))
    db.executemany("INSERT INTO dnode VALUES (?,?,?,?,?,?,?,?)",
                   [(i, n, n.split(":", 1)[0], round(out_w.get(n, 0.0), 6), round(in_w.get(n, 0.0), 6),
                     json.dumps([round(t, 3) for t in sorted(uses[n])[-50:]]) if uses.get(n) else None,
                     first[n] if first[n] != float("inf") else None, last[n] if last[n] != float("-inf") else None)
                    for n, i in ids.items()])
    db.executemany("INSERT INTO dedge VALUES (?,?,?,?,?,?)", rows)
    db.executescript("CREATE INDEX dedge_src ON dedge(src);")
    meta = dict(meta, total_w=total, nodes=len(ids), edges=len(rows), edges_over_cap=n_cut)
    db.executemany("INSERT INTO meta VALUES (?,?)", [(k, json.dumps(v)) for k, v in meta.items()])
    db.commit()
    db.close()
    return meta


def _replace(src, dst, tries=20):
    for _ in range(tries):                  # a reader holding the old pointer for a moment makes Windows refuse
        try:
            os.replace(src, dst)
            return True
        except PermissionError:
            time.sleep(0.1)
    return False


def update(now=None):
    """Rebuild the fast layer and point live/delta.json at it."""
    t0 = time.time()
    now = float(now or time.time())
    if not os.path.exists(SIDECAR):       # a new install: no build has published a sidecar yet, so there is no base
        return {"skipped": "no sidecar yet (the first build publishes one)", "events_new": 0, "edges": 0, "nodes": 0,
                "newest_event": None}
    try:
        b = base()
    except SidecarBehind as e:            # the previous pointer stays, and open_delta keeps pairing it with its sidecar
        log("delta not rebuilt: %s" % e)
        return {"skipped": str(e), "events_new": 0, "edges": 0, "nodes": 0, "newest_event": None}
    ev, rd = read_events(b)
    n_new = sum(1 for e in ev if e[3])
    ft = _json(os.path.join(b["store"], "first_touch.json"), {})
    edges, uses, out_w, in_w, total = pairs_of(ev, b["hubs"], now, b["half_life_s"],
                                               first_seen=ft.get("sessions") if isinstance(ft, dict) else None)
    stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime(now))
    name = "delta_%s.sqlite" % stamp
    path = os.path.join(LIVE, name)
    meta = {"version": VERSION, "built": time.time(), "ref_time": now, "half_life_s": b["half_life_s"],
            "base_store_until": b["until"], "base_store_built": b["store_built"], "base_sidecar_built": b["sidecar_built"],
            "events_new": n_new, "events_read": len(ev), "sessions": len({e[0] for e in ev if e[3]}),
            "newest_event": max((e[1] for e in ev if e[3]), default=None), "read": rd}
    meta = write_db(path + ".new", edges, uses, out_w, in_w, total, b["kinds"], meta)
    os.replace(path + ".new", path)
    ptr = {"file": name, "built": meta["built"], "ref_time": now, "base_store_until": b["until"],
           "base_store_built": b["store_built"], "base_sidecar_built": b["sidecar_built"],
           "edges": meta["edges"], "nodes": meta["nodes"],
           "events_new": n_new, "newest_event": meta["newest_event"]}
    tmp = POINTER + ".%d.tmp" % os.getpid()
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(ptr, fh)
    if not _replace(tmp, POINTER):
        log("pointer not replaced (a reader held it); %s stays unnamed until the next run" % name)
        try:
            os.remove(tmp)
        except OSError:
            pass
    olds = sorted(glob.glob(os.path.join(LIVE, "delta_*.sqlite")))
    for p in olds[:-KEEP_FILES]:
        try:
            os.remove(p)                    # a rebuilt cache; a reader holding it open keeps it until it closes
        except OSError:
            pass
    newest = meta["newest_event"]
    log("%s: %d new events (%d sessions, newest %s), %d edges, %d nodes, from byte %d (%s) in %.1f s" % (
        name, n_new, meta["sessions"], time.strftime("%H:%M:%S", time.localtime(newest)) if newest else "-",
        meta["edges"], meta["nodes"], rd["start"], rd["mode"], time.time() - t0))
    return ptr


_BASE_KEYS = (("base_store_until", "store_until"), ("base_store_built", "store_built"), ("base_sidecar_built", "built"))


def _same(a, b):
    """The same generation: the recorded values are equal. The pointer and the delta file copy the sidecar's own floats,
    which survive JSON unchanged (29 Sep: all three equal to the last digit in the live pointer, file and sidecar), so
    equality is exact. Until 29 Sep this allowed one second, and a sidecar or store rebuilt within that second took the
    old delta (the 00:3x reply had said the fields must be equal)."""
    try:
        return float(a) == float(b)
    except (TypeError, ValueError):
        return False


def open_delta(sidecar_meta=None):
    """For the membrane: (connection, meta) of the current fast layer, or (None, None) when there is none or it was
    built on another base than the sidecar's (a build finished and the next run has not caught up); the membrane then
    reads the base alone. open_delta.refused says why, for a diagnosis.

    The base is identified by all it records - its last event, the store's build and the sidecar's build - in the pointer,
    in the file the pointer names and against the sidecar (28 Sep, interactive audit IA-20260928-02). Until then only the
    last event time was compared, so a store rebuilt with other filtering or config up to the same event took a delta
    counted on the old one. A pointer written before the sidecar's build time was added to it is judged on what it has."""
    open_delta.refused = None
    ptr = _json(POINTER, None)
    if not ptr:
        return None, None
    if sidecar_meta is not None:
        su = sidecar_meta.get("store_until")
        if su is None or not _same(ptr.get("base_store_until") or 0, su):
            open_delta.refused = "the pointer's base ends at another event than the sidecar's"
            return None, None
        for pk, sk in _BASE_KEYS[1:]:
            if ptr.get(pk) is not None and sidecar_meta.get(sk) is not None \
                    and not _same(ptr[pk], sidecar_meta[sk]):
                open_delta.refused = "the pointer's %s is not the sidecar's %s" % (pk, sk)
                return None, None
    p = os.path.join(LIVE, ptr["file"])
    if not os.path.exists(p):
        return None, None
    try:
        db = sqlite3.connect("file:%s?mode=ro" % p.replace("\\", "/"), uri=True, timeout=2)
        meta = {k: json.loads(v) for k, v in db.execute("SELECT k, v FROM meta")}
    except Exception:
        return None, None
    for pk, sk in _BASE_KEYS:
        # the file must be the generation the pointer describes, and built on the sidecar being read
        bad = (ptr.get(pk) is not None and meta.get(pk) is not None and not _same(ptr[pk], meta[pk])) or (
            sidecar_meta is not None and meta.get(pk) is not None and sidecar_meta.get(sk) is not None
            and not _same(meta[pk], sidecar_meta[sk]))
        if bad:
            open_delta.refused = "the delta file's %s does not match its pointer or the sidecar" % pk
            try:
                db.close()
            except Exception:
                pass
            return None, None
    # Every part of the base must be on record, in the pointer or the file, and in the sidecar. The comparisons above
    # skip a value that is missing, so a pointer and a file that both omit one - or a sidecar without it - could stand
    # on any generation of it and would still be taken (the dual legacy omission). A pointer that
    # omits a value its file records is still judged by the file (the migration case the checks above were written
    # for). The writer has recorded all three in both places since 28 Sep, so only a legacy delta is turned away, and
    # the next run replaces it.
    if sidecar_meta is not None:
        for pk, sk in _BASE_KEYS:
            if (ptr.get(pk) is None and meta.get(pk) is None) or sidecar_meta.get(sk) is None:
                open_delta.refused = ("the base's %s is recorded by neither the pointer nor the delta file"
                                      % pk if sidecar_meta.get(sk) is not None
                                      else "the sidecar records no %s to hold the delta's %s against" % (sk, pk))
                try:
                    db.close()
                except Exception:
                    pass
                return None, None
    return db, meta


open_delta.refused = None


def stats():
    ptr = _json(POINTER, None)
    print(json.dumps(ptr, indent=1))
    db, meta = open_delta()
    if db is not None:
        for t in ("dnode", "dedge"):
            print("%-6s %d rows" % (t, db.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]))
        print(json.dumps({k: meta[k] for k in ("events_new", "sessions", "total_w", "read")}, indent=1))


def selftest():
    """The fast layer's pairs equal the store's own on the same events (graph_store.build with every tool kept, no
    half-life, no hubs), counted from a synthetic log: context window, distinct items, first touch, errors to err:ANY,
    a hub left out, a bank touch leaving its hour out, the partial last line left for the next run."""
    import tempfile
    import numpy as np
    import graph_store
    tmp = tempfile.mkdtemp(prefix="live_delta_selftest_")
    p = os.path.join(tmp, "events.jsonl")
    T = 1790000000.0
    ev = []
    rng = np.random.default_rng(3)
    items = ["file:/a/%d.py" % i for i in range(12)] + ["dir:/a", "dir:/b", "mem:m1", "mem:m2", "err:Bash:x", "cmd:ls",
                                                         "tool:Bash:grep", "said:hello", "host:h1"]
    for s in ("s1", "s2", "s3"):
        t = T
        for _ in range(400):
            t += float(rng.integers(1, 90))
            ev.append({"ts": t, "session": s, "kind": "", "id": items[int(rng.integers(0, len(items)))]})
    for e in ev:
        e["kind"], e["id"] = e["id"].split(":", 1)
    ev.sort(key=lambda e: e["ts"])
    with open(p, "w", encoding="utf-8") as fh:
        for e in ev:
            fh.write(json.dumps(e) + "\n")
    ok = True
    # 1. equal to the store on the same events (tool_rate 1 on both sides, no hubs)
    meta = graph_store.build(p, os.path.join(tmp, "store"), window=WINDOW, k=K_CTX, hub=1.01, tool_rate=1.0)
    st = graph_store.GraphStore(os.path.join(tmp, "store"))
    want = {}
    for key, c in zip(st.keys.tolist(), st.cnt.tolist()):
        want[(st.names[key >> 32], st.names[key & 0xFFFFFFFF])] = c
    b = {"until": T - 1.0, "hubs": set()}
    evs, _rd = read_events(b, path=p, kinds_cache=os.path.join(tmp, "none.json"), start=0, mode="bytes")
    global TOOL_RATE
    old = TOOL_RATE
    TOOL_RATE = 1.0
    try:
        got_e, _u, _o, _i, _t = pairs_of(evs, set(), T + 1e6, 0.0)
    finally:
        TOOL_RATE = old
    got = {k: v[0] for k, v in got_e.items()}
    same = got == want
    print("1 same pairs as graph_store.build on %d events: %s (%d pairs, %d there)" % (len(ev), same, len(got), len(want)))
    ok &= same
    # 2. a hub adds nothing
    hub = "dir:/a"
    g2, *_ = pairs_of(evs, {hub}, T + 1e6, 0.0)
    no_hub = all(c != hub for c, _d in g2)
    print("2 a hub carries nothing: %s" % no_hub)
    ok &= no_hub
    # 3. only new events are targets, older ones still context
    half = ev[len(ev) // 2]["ts"]
    b3 = {"until": half, "hubs": set()}
    evs3, _ = read_events(b3, path=p, kinds_cache=os.path.join(tmp, "none.json"), start=0, mode="ts")
    g3, u3, *_ = pairs_of(evs3, set(), T + 1e6, 0.0)
    only_new = all(min(ts) > half for ts in u3.values()) and len(g3) > 0
    ctx_old = any(t0 < half + WINDOW for (_c, _d), (_w, t0, _t1) in g3.items())
    print("3 targets only after the store's last event, context reaching back: %s, %s" % (only_new, ctx_old))
    ok &= only_new and ctx_old
    # 4. a bank touch leaves its session's hour out; a partial last line waits
    with open(p, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": T + 5000.0, "session": "s1", "kind": "file", "id": "/q/recall_bank/x.json"}) + "\n")
        fh.write('{"ts": %f, "session": "s1", "kind": "file", "id": "/a/partial' % (T + 9e5))
    evs4, rd4 = read_events({"until": T - 1.0, "hubs": set()}, path=p, kinds_cache=os.path.join(tmp, "none.json"),
                            start=0, mode="bytes")
    near_bank = [e for e in evs4 if e[0] == "s1" and abs(e[1] - (T + 5000.0)) <= EXCLUDE_WINDOW]
    partial = any("partial" in e[2] for e in evs4)
    print("4 bank hour left out: %s; partial line left: %s" % (not near_bank, not partial))
    ok &= (not near_bank) and not partial
    # 5. fading: a pair seen an hour ago weighs 0.5 ** (1 h / half-life)
    g5, *_ = pairs_of([("z", 100.0, "file:/c", True), ("z", 110.0, "mem:m9", True)], set(), 3700.0, 7200.0)
    w5 = g5[("file:/c", "mem:m9")][0]
    print("5 weight after 3,590 s at a 2 h half-life: %.4f (want %.4f)" % (w5, 0.5 ** (3590 / 7200)))
    ok &= abs(w5 - 0.5 ** (3590 / 7200)) < 1e-9
    print("SELFTEST", "PASS" if ok else "FAIL")
    return ok


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "stats"
    if mode == "update":
        print(json.dumps(update(), indent=1))
    elif mode == "selftest":
        sys.exit(0 if selftest() else 1)
    else:
        stats()


if __name__ == "__main__":
    main()
