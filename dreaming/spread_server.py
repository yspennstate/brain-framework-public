"""spread_server.py - a resident process that answers the spread_recall prompt hook with its loaded state kept warm.

Why: the hook is the slowest one on every prompt (median ~400 ms of ~870 ms on 4 Nov). Almost all of it is reloading
the same files each prompt: the embedding sidecar and its freshness check, the edge graph, the keyword generation,
salience, habituation, the memory listing and MEMORY.md. Kept in one process those loads are paid once; on 5 Nov
memoizing them cut the median from 415 ms to 50 ms with the output byte-identical on 8 of 8
prompts (scratch harness memo_spread.py).

The hook itself is unchanged: spread_client.py sends the hook's stdin here and prints the answer; any failure, a
busy server or a decline means the hook runs cold exactly as before.

Correctness rules (same shape as brain_recall_server.py):
- Each memoized loader is keyed on the stat (inode, size, mtime_ns) of the files it reads, and dropped at most
  STATE_TTL seconds after it was filled, so an in-place memory edit is seen within that window at worst.
- Between calls a refresher refills entries before they expire, so state stays warm while prompts are minutes apart.
- The process exits when spread_recall.py or any brain module it imported changed on disk, or past RSS_CAP, or
  after IDLE_EXIT seconds without a call. The next prompt runs cold and starts a fresh server.
- A call whose BRAIN_*/SPREAD_*/MATHBRAIN_* environment differs from the server's is declined.
- One call at a time. Writes only what the hook itself writes (surfaced.tsv, its fire state).
"""

import sys
if __name__ == "__main__" and any(arg in ("-h", "--help") for arg in sys.argv[1:]):
    print(__doc__ or "No command-line options.")
    raise SystemExit(0)

import bisect, collections, io, json, os, secrets, socketserver, struct, sys, threading, time

D = os.path.dirname(os.path.abspath(__file__))
if D not in sys.path:
    sys.path.insert(0, D)
STATE_DIR = os.path.join(D, "spread_server")
ENDPOINT = os.path.join(STATE_DIR, "endpoint.json")
LOG = os.path.join(STATE_DIR, "server.log")
WARM_KEYS = os.path.join(STATE_DIR, "warm_keys.json")   # the last server's loader calls, for a warm start
CALL_LOG = os.path.join(STATE_DIR, "calls.jsonl")   # one line a call: session prefix, served or why not, ms
# Laptop port (5 Nov): the laptop's loaders cost seconds, not the Mac's ~0.4 s, so a refill of every
# entry each 72 s would keep a core busy for nothing; here the TTL is 300 s (a dependency's stat change still refills
# at once). An in-place edit to one memory file, which moves no watched stat, is seen within 5 minutes.
STATE_TTL = float(os.environ.get("SPREAD_SERVER_TTL", "300" if os.name == "nt" else "120"))
IDLE_EXIT = float(os.environ.get("SPREAD_SERVER_IDLE_EXIT", "7200"))
RSS_CAP = int(float(os.environ.get("SPREAD_SERVER_RSS_CAP_GB", "1.5")) * 1024 ** 3)
NAMES_BG = os.environ.get("SPREAD_SERVER_NAMES_BG", "1") != "0"
SERVE_STALE = os.environ.get("SPREAD_SERVER_STALE", "1" if os.name == "nt" else "0") == "1"
RESTAT_AGE = float(os.environ.get("SPREAD_SERVER_RESTAT_AGE", "120"))
ENV_PREFIXES = ("BRAIN_", "SPREAD_", "MATHBRAIN_", "FOK_")
# Hooks this process may run. fok_probe, 5 Nov: the first A/B timed the served arm in-process against a cold process
# and read 68 vs ~85 ms; timed both as a hook runs (a fresh process each, ab_fok2.py) it is 86 vs 134 ms, 30 of 30
# byte-identical, so it is hosted. Its warm time is scoring long prompts (14 ms short, ~64 ms median on real ones).
HOSTED = ("spread_recall", "fok_probe")


# Which files a slow served call opened. A served call spends 80-94 pct of its
# wall time off the CPU; the BM25 arm's 2,000 sqlite page reads (7.1 MB) cost only ~140 ms of CPU, and single cold
# opens here take ~0.3 s under disk contention. An audit hook notes every open/listdir/scandir of the serving thread
# during a call; a call slower than OPEN_LOG_MS writes its opens, by directory, with the longest gaps that followed
# an open (a gap is the open plus whatever ran before the next one), to opens.jsonl.
OPEN_LOG = os.path.join(STATE_DIR, "opens.jsonl")
OPEN_LOG_MS = float(os.environ.get("SPREAD_SERVER_OPEN_LOG_MS", "2000" if os.name == "nt" else "0"))
_TRACE = {"thr": None, "ops": None}


def _audit(ev, args):
    if _TRACE["thr"] is not None and ev in ("open", "os.listdir", "os.scandir") \
            and threading.current_thread() is _TRACE["thr"]:
        p = args[0] if args else None
        if isinstance(p, (str, bytes)):
            _TRACE["ops"].append((time.perf_counter(), p if isinstance(p, str) else p.decode("utf-8", "replace"), ev))


if OPEN_LOG_MS > 0:
    sys.addaudithook(_audit)


def open_summary(ops, t_end):
    dirs = {}
    gaps = []
    for i, (t, p, ev) in enumerate(ops):
        d = os.path.dirname(p) if ev == "open" else p
        dirs[d] = dirs.get(d, 0) + 1
        nxt = ops[i + 1][0] if i + 1 < len(ops) else t_end
        gaps.append((round((nxt - t) * 1000), os.path.basename(p) or p, ev))
    gaps.sort(reverse=True)
    return {"n": len(ops), "dirs": dict(sorted(dirs.items(), key=lambda kv: -kv[1])[:8]), "gaps": gaps[:8]}


# The BM25 build window. After each index publish the new generation's postings sidecar takes tens of seconds to build,
# and in that window BM25() unpickles the whole payload under the GIL (measured: 14.9 s of a 15.1 s served call). The
# previous generation's sidecar is still on disk then (the sweep keeps the newest older one as the next build's base),
# and a sidecar file only ever appears by os.replace of a committed temp, so any *.postings.<tag>.sqlite3 is a finished
# file. Here the hosted hooks' BM25() answers from it while the live one builds, the same one-generation-stale trade
# SERVE_STALE makes for the graph; deliberate recall (outside this process) is untouched. Past STALE_BM25_MAX_S of age
# the old file is not used and the payload path runs as before. Off: SPREAD_SERVER_STALE_BM25=0.
STALE_BM25 = os.environ.get("SPREAD_SERVER_STALE_BM25", "1" if os.name == "nt" else "0") == "1"
STALE_BM25_MAX_S = float(os.environ.get("SPREAD_SERVER_STALE_BM25_MAX_S", "1800"))
STALE_BM25_USED = [0]


def _stale_postings_conn(bm25, path):
    import sqlite3
    path = os.fspath(path)
    sig = bm25._index_signature(path)
    if sig is None:
        return None
    base = bm25._postings_base(path, bm25.postings_path(path, sig))
    if base is None or time.time() - os.stat(base).st_mtime > STALE_BM25_MAX_S:
        return None
    con = sqlite3.connect("file:%s?mode=ro" % base.replace("?", "%3f"), uri=True)
    try:
        h = dict(con.execute("select k,v from hdr"))
        if h.get("schema") != bm25.POSTINGS_SCHEMA:
            con.close()
            return None
        return con, float(h["n"]), float(h["avgdl"])
    except Exception:
        con.close()
        raise


def install_stale_bm25():
    import bm25
    cls = bm25.BM25
    if getattr(cls.__init__, "_stale_wrapped", False):
        return
    orig = cls.__init__

    def __init__(self, path=bm25.INDEX):
        if getattr(bm25, "_POSTINGS_ON", True):
            try:
                sc = bm25._postings_conn(path)            # what the original __init__ does first
                if sc is None:                             # the live generation has no sidecar yet
                    sc = _stale_postings_conn(bm25, path)
                    if sc is not None:
                        STALE_BM25_USED[0] += 1
                if sc is not None:
                    self._path, self._sc = os.fspath(path), sc
                    self.n, self.avgdl = sc[1], sc[2]
                    return
            except Exception:
                pass
        orig(self, path)

    __init__._stale_wrapped = True
    cls.__init__ = __init__


# Spell-correct df memo. After the held name list, the largest frames in slow served calls were the sidecar df's
# prefetch: QueryNormalizer asks the sidecar's p(term, df) table for every edit-1 candidate of each rare prompt word
# (about 55 x length candidates a word), and each served call builds a new BM25 and a new df reader, so every answer
# was thrown away. The memo keeps them per sidecar FILE: the key is the database file the connection opened plus its
# stat identity, so a new generation (a new file, or the same file rewritten) changes the key and the answers stay
# exact. prefetch fills a local dict and publishes it in one update, so a concurrent caller never sees a half-filled
# chunk. Off: SPREAD_SERVER_DF_MEMO=0.
#
# Carry. The sidecar is rebuilt every few minutes (one new memory is a new generation), and an empty memo per
# generation left no live win. A new generation keeps every answer whose word is in no document that changed: the two
# generations' fp tables (an 8-byte fingerprint per position) say which documents went or came, their fwd rows give
# those documents' words, and only those words are dropped. Checked on a real rebuild (54 documents gone, 63 came,
# 7,410 words): 0 of 20,000 present words and 0 of 30,000 edit-1 candidates outside that set changed df; 1,129 of
# 5,000 inside it did. The retirer deletes a superseded sidecar within minutes, so the memo's generation is held open
# read-only (bm25's sweep treats a held file as next sweep's work) and let go right after the carry. Cost about 210 ms
# (the new fp table; the old one is kept) plus about 30 ms once per generation. No fp/fwd table, or any error: an
# empty memo. Off: SPREAD_SERVER_DF_CARRY=0.
DF_MEMO = {"key": None, "got": {}, "hold": None, "fp": None}
DF_MEMO_MAX = 400000
DF_CARRY = os.environ.get("SPREAD_SERVER_DF_CARRY", "1") != "0"
DF_LOCK = threading.Lock()
_bm25_mod = None
# Vocabulary filter. Nearly all of the edit-1 candidates prefetch asks the sidecar for are not words of the corpus (150
# memory descriptions: 9,973 asked, 50 present), and each absent probe still reads a leaf page of p, whose postings sit
# inline (about 0.7 ms a probe cold, about 310 ms a corrected word). The server holds, per generation, a sorted array of
# the in-process hash() of every term (the keys of vocab['df'] are exactly p's terms) and asks sqlite only for
# candidates whose hash is in it: an absent hash is an exact None, a collision only costs a query. Built off the call
# path in a thread (about 0.7 s, about 80 MB transient); a new generation unions the carry's changed words into the old
# array (a word in no changed document was in the old p), and a full rebuild every 6 h drops the stale words. While no
# array matches the current file, prefetch asks for everything, as before. Off: SPREAD_SERVER_DF_VOCAB=0.
DF_VOCAB = os.environ.get("SPREAD_SERVER_DF_VOCAB", "1") != "0"
VOCAB = {"key": None, "h": None, "built": 0.0, "bg": None}
VOCAB_REBUILD_S = 6 * 3600


def _vocab_hashes(path):
    import numpy as np, pickle, zlib
    con = _ro_con(path)
    try:
        d = pickle.loads(zlib.decompress(con.execute("select blob from vocab where k='df'").fetchone()[0]))
    finally:
        con.close()
    a = np.fromiter((hash(k) for k in d), dtype=np.int64, count=len(d))
    del d
    a.sort()
    return a


def _vocab_bg(path, key):
    def run():
        try:
            a = _vocab_hashes(path)
        except Exception:
            a = None
        with DF_LOCK:
            if a is not None and DF_MEMO["key"] == key:
                VOCAB["key"], VOCAB["h"], VOCAB["built"] = key, a, time.time()
            VOCAB["bg"] = None
    VOCAB["bg"] = key
    threading.Thread(target=run, name="df-vocab", daemon=True).start()


def _vocab_union(h, words):
    import numpy as np
    if not words:
        return h
    new = np.unique(np.fromiter((hash(w) for w in words), dtype=np.int64, count=len(words)))
    i = np.searchsorted(h, new)
    j = np.minimum(i, len(h) - 1)
    keep = h[j] != new
    return np.insert(h, i[keep], new[keep]) if keep.any() else h   # a sorted insert, no re-sort of the whole array


def _vocab_absent(h, words):
    """For each of `words`, True when its hash is not in the sorted array `h`: the word is exactly absent from p."""
    import numpy as np
    hs = np.fromiter((hash(x) for x in words), dtype=np.int64, count=len(words))
    i = np.searchsorted(h, hs)
    i[i >= len(h)] = 0
    return (h[i] != hs).tolist()


def _ro_con(path):
    import sqlite3
    return sqlite3.connect("file:%s?mode=ro" % path.replace("\\", "/"), uri=True, check_same_thread=False)


def _df_carry(old_con, old_fp, new_con, got):
    """`got` minus the words of every document that differs between the two generations, the new fp map, and the
    changed words."""
    fn = dict(new_con.execute("select pos, h from fp"))
    fo = old_fp if old_fp is not None else dict(old_con.execute("select pos, h from fp"))
    changed = set()
    for p, h in fo.items():
        if fn.get(p) != h:
            changed.update(_bm25_mod._fwd_terms(old_con.execute("select terms from fwd where pos=?", (p,)).fetchone()[0]))
    for p, h in fn.items():
        if fo.get(p) != h:
            changed.update(_bm25_mod._fwd_terms(new_con.execute("select terms from fwd where pos=?", (p,)).fetchone()[0]))
    snap = dict(got)      # a copy first: other callers may be publishing into `got` meanwhile
    return {k: v for k, v in snap.items() if k not in changed}, fn, changed


def _df_memo_for(con):
    """The shared answer dict for the sidecar `con` reads, swapped (and carried) when the generation changed."""
    return _df_memo_and_vocab(con)[0]


def _df_memo_and_vocab(con):
    """(the shared answer dict, the vocabulary hash array or None) for the sidecar `con` reads; the array is returned
    only when it was built for this exact file and stat identity."""
    f = [r[2] for r in con.execute("pragma database_list") if r[1] == "main"][0]
    s = os.stat(f)
    key = (os.path.normcase(f), s.st_size, s.st_mtime_ns, getattr(s, "st_birthtime_ns", s.st_ctime_ns), s.st_ino)
    if DF_MEMO["key"] != key or len(DF_MEMO["got"]) > DF_MEMO_MAX:
        with DF_LOCK:
            if DF_MEMO["key"] != key or len(DF_MEMO["got"]) > DF_MEMO_MAX:
                got, fp, hold, changed = {}, None, DF_MEMO["hold"], None
                old_key = DF_MEMO["key"]
                if (DF_CARRY and hold is not None and (DF_MEMO["got"] or (DF_VOCAB and VOCAB["h"] is not None))
                        and len(DF_MEMO["got"]) <= DF_MEMO_MAX
                        and DF_MEMO["key"] and DF_MEMO["key"][0] != key[0] and hasattr(_bm25_mod, "_fwd_terms")):
                    try:
                        got, fp, changed = _df_carry(hold, DF_MEMO["fp"], con, DF_MEMO["got"])
                    except Exception:
                        got, fp, changed = {}, None, None
                new_hold = None
                if DF_CARRY:
                    try:
                        new_hold = _ro_con(f)
                    except Exception:
                        new_hold = None
                DF_MEMO["key"], DF_MEMO["got"], DF_MEMO["fp"], DF_MEMO["hold"] = key, got, fp, new_hold
                if DF_VOCAB and VOCAB["key"] != key:
                    h = None
                    if (changed is not None and VOCAB["h"] is not None and VOCAB["key"] == old_key
                            and time.time() - VOCAB["built"] < VOCAB_REBUILD_S):
                        try:
                            h = _vocab_union(VOCAB["h"], changed)
                        except Exception:
                            h = None
                    VOCAB["key"], VOCAB["h"] = (key, h) if h is not None else (None, None)
                if hold is not None:
                    try:
                        hold.close()
                    except Exception:
                        pass
    got, vh = DF_MEMO["got"], None
    if DF_VOCAB:
        if VOCAB["key"] == key:
            vh = VOCAB["h"]
        elif VOCAB["bg"] is None:
            with DF_LOCK:
                if VOCAB["bg"] is None and VOCAB["key"] != key and DF_MEMO["key"] == key:
                    _vocab_bg(f, key)
    return got, vh


def install_df_memo(bm25):
    """Replace bm25's sidecar df reader with one whose answers live in DF_MEMO; returns True when installed."""
    global _bm25_mod
    base = getattr(bm25, "_SidecarDf", None)
    if base is None or getattr(base, "_df_memo", False) or os.environ.get("SPREAD_SERVER_DF_MEMO", "1") == "0":
        return False
    _bm25_mod = bm25

    class _MemoDf(base):
        _df_memo = True

        _vh = None

        def __init__(self, con):
            base.__init__(self, con)
            try:
                self._c, self._vh = _df_memo_and_vocab(con)
            except Exception:
                pass                      # keep the reader's own dict: no memo for this one

        def prefetch(self, terms):
            if self._full is not None:
                return
            got = self._c
            need = [t for t in set(terms) if t not in got]
            loc = {}
            if self._vh is not None and need:
                miss = _vocab_absent(self._vh, need)
                loc.update(dict.fromkeys([t for t, m in zip(need, miss) if m]))
                need = [t for t, m in zip(need, miss) if not m]
            for i in range(0, len(need), 900):
                part = need[i:i + 900]
                loc.update(dict.fromkeys(part))
                for term, df in self._con.execute("select term, df from p where term in (%s)" % ",".join("?" * len(part)),
                                                  part):
                    loc[term] = df
            got.update(loc)

    bm25._SidecarDf = _MemoDf
    return True


# Idle paging. Served calls of 2 s or more took a median 41,936 page faults
# against 399 for calls under 1.5 s, and the faults follow the idle time before the call (over 60 s idle: ~46,500, every
# call slow): with ~0.7 GB of RAM free Windows trims the idle server's ~390 MB working set and the next prompt pages its
# state back in. A SOFT minimum (both hard
# flags off) asks the memory manager to trim other working sets first; it can still trim this one under real pressure,
# so nothing is pinned. Off with SPREAD_SERVER_WS_MIN_MB=0; posix unchanged.
WS_MIN_MB = float(os.environ.get("SPREAD_SERVER_WS_MIN_MB", "0"))   # off: the ~46k-fault calls blamed on idle trimming were each the first call after a server restart; 320 and 560 MB soft floors changed nothing


def ws_floor(min_mb):
    if os.name != "nt":
        return "not windows"
    try:
        import ctypes
        from ctypes import wintypes
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.GetCurrentProcess.restype = wintypes.HANDLE
        k.SetProcessWorkingSetSizeEx.argtypes = [wintypes.HANDLE, ctypes.c_size_t, ctypes.c_size_t, wintypes.DWORD]
        lo = int(min_mb * 1048576)
        hi = max(lo * 2, int(RSS_CAP))
        ok = k.SetProcessWorkingSetSizeEx(k.GetCurrentProcess(), lo, hi, 0x2 | 0x8)   # MIN_DISABLE | MAX_DISABLE: soft
        return "soft min %d MB set" % min_mb if ok else "refused, error %d" % ctypes.get_last_error()
    except Exception as e:
        return "error %s: %s" % (type(e).__name__, e)


def log(msg):
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write("%s pid %d %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), os.getpid(), msg))
    except Exception:
        pass


def env_subset(env):
    return {k: v for k, v in env.items() if k.startswith(ENV_PREFIXES) and k != "SPREAD_SERVER_CHILD"}


def avail_mb():
    """Windows: physical RAM available to the machine, MB (GlobalMemoryStatusEx); None elsewhere or on error."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class MSX(ctypes.Structure):
            _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        m = MSX()
        m.dwLength = ctypes.sizeof(MSX)
        if ctypes.WinDLL("kernel32").GlobalMemoryStatusEx(ctypes.byref(m)):
            return int(m.ullAvailPhys // 1048576)
    except Exception:
        pass
    return None


def rss_bytes(_faults=False):
    if os.name == "nt":   # laptop port: the working set, from GetProcessMemoryInfo
        try:
            import ctypes
            from ctypes import wintypes

            class PMC(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                            ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
            c = PMC()
            c.cb = ctypes.sizeof(PMC)
            k = ctypes.WinDLL("kernel32")
            k.GetCurrentProcess.restype = wintypes.HANDLE
            psapi = ctypes.WinDLL("psapi")
            psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
            if psapi.GetProcessMemoryInfo(k.GetCurrentProcess(), ctypes.byref(c), c.cb):
                return (int(c.WorkingSetSize), int(c.PageFaultCount)) if _faults else int(c.WorkingSetSize)
        except Exception:
            pass
        return (0, 0) if _faults else 0
    try:
        import resource
        u = resource.getrusage(resource.RUSAGE_SELF)
        r = u.ru_maxrss if sys.platform == "darwin" else u.ru_maxrss * 1024
        return (r, int(u.ru_majflt)) if _faults else r
    except Exception:
        return (0, 0) if _faults else 0


def call_cost():
    """(process CPU s, page faults, working set MB) for one call's before/after: a
    served call of 8-55 s with no load inside it was not waiting on the refresher (1 of 4 overlapped one, as often
    as fast calls), so the log must say whether the time was CPU, paging, or waiting for a core. Windows counts soft
    and hard faults together; posix gives major faults."""
    ws, pf = rss_bytes(_faults=True)
    return time.process_time(), pf, ws / 1048576.0, io_counts()


# Where a call's page faults come from. About one served call in eight takes
# ~44k faults (~175 MB) and ~2.2 s, yet its working set grows only 4-20 MB and free RAM is no lower than on fast calls:
# something allocates and frees ~175 MB inside the call. A sampler thread reads the process fault count and private
# bytes every FAULT_SAMPLE_MS during a call, charges each interval's faults to the serving thread's stack at its end
# (the innermost frame from the dreaming folder, plus the innermost frame of all), and a call with at least
# FAULT_LOG_MIN faults writes the top charges and its peak private-byte rise to faults.jsonl. Off: =0.
FAULT_LOG = os.path.join(STATE_DIR, "faults.jsonl")
FAULT_SAMPLE_MS = float(os.environ.get("SPREAD_SERVER_FAULT_SAMPLE_MS", "20" if os.name == "nt" else "0"))
FAULT_LOG_MIN = int(os.environ.get("SPREAD_SERVER_FAULT_LOG_MIN", "20000"))
# 5 Nov 22:0x: after the directory-read fix the slowest served calls take 0.6-1.2 s with ordinary fault counts; a call
# at least this slow is logged too, and each top entry's sample count x FAULT_SAMPLE_MS is roughly its time.
FAULT_LOG_MS = float(os.environ.get("SPREAD_SERVER_FAULT_LOG_MS", "800"))


class FaultSampler(threading.Thread):
    def __init__(self, target):
        super().__init__(daemon=True)
        self.tid = target.ident
        self.done = threading.Event()
        self.by = {}
        self.n = 0
        self.peak_priv = 0.0
        import ctypes
        from ctypes import wintypes

        class PMC(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
        self.c = PMC()
        self.c.cb = ctypes.sizeof(PMC)
        k = ctypes.WinDLL("kernel32")
        k.GetCurrentProcess.restype = wintypes.HANDLE
        self.h = k.GetCurrentProcess()
        self.f = ctypes.WinDLL("psapi").GetProcessMemoryInfo
        self.f.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
        self.ref = ctypes.byref(self.c)
        self.pf0, self.priv0 = self.read()

    def read(self):
        self.f(self.h, self.ref, self.c.cb)
        return int(self.c.PageFaultCount), self.c.PagefileUsage / 1048576.0

    @staticmethod
    def key(fr):
        # 5 Nov 22:2x: the dreaming part is the innermost THREE frames from the dreaming folder, joined by " < "
        # (callee < caller), so a cost such as the memory-folder listing names the path that asked for it.
        inner = None
        ours = []
        while fr is not None and len(ours) < 3:
            co = fr.f_code
            if inner is None:
                inner = "%s:%s:%d" % (os.path.basename(co.co_filename), co.co_name, fr.f_lineno)
            if "dreaming" in co.co_filename.replace("\\", "/"):
                ours.append("%s:%s:%d" % (os.path.basename(co.co_filename), co.co_name, fr.f_lineno))
            fr = fr.f_back
        return "%s | %s" % (" < ".join(ours) or None, inner)

    def run(self):
        last = self.pf0
        while not self.done.wait(FAULT_SAMPLE_MS / 1000.0):
            try:
                pf, priv = self.read()
                fr = sys._current_frames().get(self.tid)
                k = self.key(fr) if fr is not None else "?"
                e = self.by.setdefault(k, [0, 0])
                e[0] += pf - last
                e[1] += 1
                last = pf
                self.n += 1
                self.peak_priv = max(self.peak_priv, priv - self.priv0)
            except Exception:
                pass

    def summary(self):
        top = sorted(self.by.items(), key=lambda kv: -kv[1][0])[:8]
        return {"samples": self.n, "peak_priv_mb": round(self.peak_priv, 1),
                "top": [[k, v[0], v[1]] for k, v in top]}



class DirWatch(threading.Thread):
    """Counts name changes (create, delete, rename) in one folder with ReadDirectoryChangesW; see Warm.held_names.
    ok is False when the watch could not be armed or has failed: the caller then lists every time."""

    def __init__(self, path):
        super().__init__(daemon=True)
        self.path = path
        self.gen = 0
        self.ok = False
        self.armed_at = float("inf")
        self.ready = threading.Event()
        # (gen, "add"|"del"|"ovf", name) per change, so a hold can be patched instead of re-listed
        self.log = collections.deque(maxlen=8192)
        self.lock = threading.Lock()

    def since(self, g0, g1):
        """The changes of generations g0+1..g1 in order, or None when any is missing (evicted) or an overflow."""
        with self.lock:
            ev = [e for e in self.log if g0 < e[0] <= g1]
            first = self.log[0][0] if self.log else None
        if g1 > g0 and (first is None or first > g0 + 1):
            return None
        if any(e[1] == "ovf" for e in ev):
            return None
        return ev

    def run(self):
        try:
            if os.name != "nt":
                return
            import ctypes
            from ctypes import wintypes
            k = ctypes.WinDLL("kernel32", use_last_error=True)
            k.CreateFileW.restype = wintypes.HANDLE
            k.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                      wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
            k.ReadDirectoryChangesW.restype = wintypes.BOOL
            k.ReadDirectoryChangesW.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, wintypes.BOOL,
                                                wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p,
                                                ctypes.c_void_p]
            # FILE_LIST_DIRECTORY, share read|write|delete, OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS
            h = k.CreateFileW(self.path, 0x0001, 0x7, None, 3, 0x02000000, None)
            if not h or h == ctypes.c_void_p(-1).value:
                return
            buf = ctypes.create_string_buffer(1 << 16)
            n = wintypes.DWORD()
            self.armed_at = time.time()
            self.ok = True
            self.ready.set()
            while True:
                # changes between two calls are buffered by the handle; an overflow returns success with 0 bytes
                r = k.ReadDirectoryChangesW(h, buf, len(buf), False, 0x1, ctypes.byref(n), None, None)
                g = self.gen + 1
                ev = []
                try:
                    if not r or not n.value:
                        ev.append((g, "ovf", None))
                    else:
                        raw, off = buf.raw[:n.value], 0
                        while True:
                            nxt, act, ln = struct.unpack_from("<III", raw, off)
                            name = raw[off + 12: off + 12 + ln].decode("utf-16-le")
                            # 1 added, 2 removed, 4 renamed-from, 5 renamed-to; 3 (modified) is not under this filter
                            kind = "add" if act in (1, 5) else "del" if act in (2, 4) else None
                            if kind:
                                ev.append((g, kind, name))
                            if not nxt:
                                break
                            off += nxt
                        if not ev:
                            ev.append((g, "nop", None))
                except Exception:
                    ev = [(g, "ovf", None)]
                with self.lock:
                    self.log.extend(ev)
                    self.gen = g
                if not r:
                    self.ok = False
                    return
        except Exception:
            self.ok = False
        finally:
            self.ready.set()


def io_counts():
    """(read ops, read MB, other ops) for this process on Windows, from GetProcessIoCounters; None elsewhere (5 Nov
    20:1x): the first 28 costed calls spent 80-94 pct of their wall time off the CPU with few page
    faults, so the wait is either file I/O (opens here cost ~0.3 s under contention) or a core run queue; read ops
    during a call tell the two apart."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class IOC(ctypes.Structure):
            _fields_ = [("ReadOperationCount", ctypes.c_ulonglong), ("WriteOperationCount", ctypes.c_ulonglong),
                        ("OtherOperationCount", ctypes.c_ulonglong), ("ReadTransferCount", ctypes.c_ulonglong),
                        ("WriteTransferCount", ctypes.c_ulonglong), ("OtherTransferCount", ctypes.c_ulonglong)]
        c = IOC()
        k = ctypes.WinDLL("kernel32")
        k.GetCurrentProcess.restype = wintypes.HANDLE
        k.GetProcessIoCounters.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
        if k.GetProcessIoCounters(k.GetCurrentProcess(), ctypes.byref(c)):
            return (int(c.ReadOperationCount), c.ReadTransferCount / 1048576.0, int(c.OtherOperationCount))
    except Exception:
        pass
    return None


def st3(p):
    try:
        s = os.stat(p)
        return (s.st_ino, s.st_size, s.st_mtime_ns)
    except OSError:
        return None


class Warm:
    def __init__(self):
        t = time.perf_counter()
        import spread_recall as sr
        self.sr = sr
        self.memo = {}
        self.code_files = self._code_files()
        self.code_sig = {p: st3(p) for p in self.code_files}
        self.bm25_gen = st3(os.path.join(D, "bm25_index.json.gz"))
        self.calls = 0
        self.misses = []
        self.mods = {}
        self.orig = {}
        self.saved_keys = None
        self._wrap()
        # the exact numpy bm25.score_postings from recall_levers (306/306 real prompts equal, 98 -> 14 ms): fok_probe's
        # BM25 loop was the 129 ms a served fok call still paid on long prompts. SPREAD_NP_BM25=0 off.
        if os.environ.get("SPREAD_NP_BM25", "1") != "0":
            try:
                import numpy as np, bm25, recall_levers
                if not hasattr(bm25.score_postings, "__wrapped__"):
                    recall_levers._install_bm25(bm25, np)
                    log("exact numpy bm25 on")
            except Exception as e:
                log("exact numpy bm25 off: %s: %s" % (type(e).__name__, e))
        if STALE_BM25:
            try:
                install_stale_bm25()
                log("stale bm25 sidecar in the build window on (max age %.0f s)" % STALE_BM25_MAX_S)
            except Exception as e:
                log("stale bm25 off: %s: %s" % (type(e).__name__, e))
        try:
            import bm25
            if install_df_memo(bm25):
                log("spell-correct df memo on (carry %s)" % ("on" if DF_CARRY else "off"))
        except Exception as e:
            log("df memo off: %s: %s" % (type(e).__name__, e))
        log("loaded in %.2f s, %d code files watched" % (time.perf_counter() - t, len(self.code_files)))

    def _code_files(self):
        out = set()
        for m in list(sys.modules.values()):
            f = getattr(m, "__file__", None) or ""
            if f.endswith(".py") and os.path.dirname(os.path.abspath(f)) == D:
                out.add(os.path.abspath(f))
        out.add(os.path.abspath(__file__))
        return sorted(out)

    def _deps(self, name):
        sr = self.sr
        mem, dr = sr.MEM, sr.DR
        j = os.path.join
        edges = [getattr(sr, "EDGES", ""), getattr(sr, "EDGES_FRESH", "")]
        kw = [getattr(sr, "KW", ""), getattr(sr, "KWG", "")]
        try:
            kw += [j(dr, n) for n in os.listdir(dr) if n.startswith("keyword") and not n.endswith(".log")]
        except OSError:
            pass
        table = {
            "_load_superseded": [mem, j(dr, "superseded.tsv")],
            "_load_habituation": [j(dr, "surfaced.tsv"), j(dr, "retrievals.tsv")],
            "_hook_graph": edges + [mem],
            "_load_graph": edges + [mem],
            "_load_embeddings": [sr.EMB, sr.EMB_AUTHORITY, mem],
            "_load_mistake_weights": [sr.MW],
            "_load_keyword_snapshot": kw,
            "_load_keyword_nodes": kw,
            "_load_salience": [sr.SAL],
            "_index_lines": [j(mem, "MEMORY.md")],
            "_mem_md_names": [mem],
        }
        return [p for p in table[name] if p]

    def _wrap(self):
        sr = self.sr
        for name in ("_load_embeddings", "_hook_graph", "_load_graph", "_load_keyword_snapshot", "_load_salience",
                     "_load_habituation", "_mem_md_names", "_index_lines", "_load_superseded",
                     "_load_mistake_weights", "_load_keyword_nodes"):
            f = getattr(sr, name, None)
            if f is None:
                continue
            deps = self._deps(name)

            def w(*a, _f=f, _name=name, _deps=deps, **k):
                key = (_name, a, tuple(sorted(k.items())))
                sig = tuple(st3(p) for p in _deps)
                hit = self.memo.get(key)
                now = time.time()
                if hit is not None and hit[0] == sig and now - hit[1] < STATE_TTL:
                    return hit[2]
                # Serve the held value while the refresher reloads it off the lock (laptop, 5 Nov 19:1x): a memory
                # written anywhere moves the memory folder's stat, and the caller that met it paid the graph reload
                # itself (5-15 s here; 55 of 170 s of caller time 18:49-19:02). Only the serving thread gets the held
                # value; the refresher (main thread) reloads, so _hook_graph's inner _load_graph is never stale to it.
                if (SERVE_STALE and hit is not None and now - hit[1] < STATE_TTL
                        and threading.current_thread() is not threading.main_thread()):
                    self.misses.append("%s:stale:0" % _name)
                    return hit[2]
                t0 = time.perf_counter()
                val = _f(*a, **k)
                self.memo[key] = (sig, now, val, _f, a, k, _deps)
                why = "new" if hit is None else ("stat" if hit[0] != sig else "ttl")
                self.misses.append("%s:%s:%.0f" % (_name, why, (time.perf_counter() - t0) * 1000))
                return val
            setattr(sr, name, w)
            self.orig[name] = (f, deps)

    # Warm start (5 Nov 2026): a new server's first prompt filled every loader cold (600-640 ms: code edits, idle
    # exits and index rebuilds all restart it). The loader calls of the last server are kept in WARM_KEYS (plain
    # json arguments only) and a new server fills them once before it serves, the same values a call would store.
    def save_keys(self):
        keys = []
        for (name, a, kv) in list(self.memo):
            vals = list(a) + [v for _k, v in kv]
            if all(v is None or isinstance(v, (str, int, float, bool)) for v in vals):
                keys.append([name, list(a), [[k2, v] for k2, v in kv]])
        keys.sort()
        if keys == self.saved_keys:
            return
        tmp = WARM_KEYS + ".%d.tmp" % os.getpid()
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(keys, f)
        os.replace(tmp, WARM_KEYS)
        self.saved_keys = keys

    def prefill(self):
        try:
            with open(WARM_KEYS, encoding="utf-8") as f:
                keys = json.load(f)
        except (OSError, ValueError):
            return 0
        n = 0
        for name, a, kv in keys:
            got = self.orig.get(name)
            if got is None:
                continue
            f, deps = got
            a, k = tuple(a), dict((k2, v) for k2, v in kv)
            key = (name, a, tuple(sorted(k.items())))
            if key in self.memo:
                continue
            try:
                sig = tuple(st3(p) for p in deps)
                val = f(*a, **k)
            except Exception:
                continue
            self.memo[key] = (sig, time.time(), val, f, a, k, deps)
            n += 1
        self.saved_keys = sorted(keys)
        return n

    def refresh(self):
        """Refill entries near the end of their STATE_TTL window with the original loader, so a prompt minutes
        after the last one still finds warm state (5 Nov: with prompts minutes apart every live call ran cold)."""
        n = 0
        now = time.time()
        for key, ent in list(self.memo.items()):
            sig, t, _val, f, a, k, deps = ent
            # also refill at once when a dependency changed (a memory written anywhere in the fleet moves the memory
            # folder's stat), so that reload is paid here and not by the next prompt
            if now - t < STATE_TTL * 0.6 and tuple(st3(p) for p in deps) == sig:
                continue
            # laptop, 5 Nov 19:1x: with SERVE_STALE the callers already get the held value, and the memory folder
            # moves about once a minute, so a stat refill waits until the entry is RESTAT_AGE old. A 12-15 s graph
            # reload holds the GIL (marshal/json in C) and stretched a concurrent call from ~1 s to 17 s.
            if SERVE_STALE and now - t < RESTAT_AGE:
                continue
            try:
                val = f(*a, **k)
            except Exception:
                self.memo.pop(key, None)
                continue
            self.memo[key] = (tuple(st3(p) for p in deps), time.time(), val, f, a, k, deps)
            n += 1
        return n

    def stale_reason(self):
        for p, s in self.code_sig.items():
            if st3(p) != s:
                return "code changed: " + os.path.basename(p)
        # A BM25 rebuild is no reason to exit: the hook builds BM25() afresh each call, the server holds no postings
        # file open between calls (lsof, 5 Nov), and memory writes rebuild the index every few minutes.
        if rss_bytes() > RSS_CAP:
            return "rss cap"
        return None

    # Held memory-folder listing. The sampler's caller frames put the
    # remaining served-call listing cost (~13 samples, 260+ ms, 1.7k faults in a 2.5 s call) in spread_recall._mem_names,
    # which lists the 53k-entry folder on every call with two long words; none of the server's memoized loaders covers
    # it. The NAME SET changes only on create, delete or rename. The folder's own stat is NOT a usable key for that on
    # one NTFS volume (measured: a create moved it, a delete never did, one rename did and the next did not), so
    # the hold is keyed on DirWatch's generation: ReadDirectoryChangesW (FILE_NOTIFY_CHANGE_FILE_NAME) bumps it on any
    # create, delete or rename and on a buffer overflow. The generation is read BEFORE the listing, so a change during it
    # re-lists next call; a hold made before the watcher was armed is not trusted; STATE_TTL is the backstop; no watcher
    # (another OS, an error) = list every call, as before. In-place edits rename nothing, so per-file stats are NOT
    # served from the held read: _MEM_STATS is a names-only dict whose .get returns None, and _desc_from_file falls
    # through to its own live os.stat. Iteration (the supersession arm's known-slug set) sees the held names.
    # Off: SPREAD_SERVER_HOLD_NAMES=0.
    class _NamesOnly(dict):
        def get(self, k, d=None):
            return None

    def _names_bg_start(self, w, ea, path):
        self.names_bg = ("run", None)

        def run():
            res = None
            try:
                with w.lock:
                    sig0 = w.gen
                t0 = time.time()
                st = ea._win_bulk_md_stats(path)
                if st is not None and w.ok:
                    res = (sig0, t0, list(st), self._NamesOnly.fromkeys(st))
            except Exception:
                res = None
            self.names_bg = ("done", res)

        threading.Thread(target=run, daemon=True, name="names_bg").start()

    def held_names(self):
        sr = self.sr
        ea = getattr(sr, "embedding_artifact", None)
        if ea is None or not hasattr(ea, "_bulk_md_stats"):
            return None
        w = getattr(self, "names_watch", None)
        if w is None or w.path != sr.MEM:
            w = self.names_watch = DirWatch(sr.MEM)
            w.start()
            w.ready.wait(2.0)
        sig = w.gen if w.ok else None
        h = hold_was = getattr(self, "names_hold", None)
        now = time.time()
        # Background re-list: the TTL re-list ran inside a served call every ~5 min, and
        # those calls were the slowest the server had (11 calls 00:41-01:29, median 1,316 ms, 9 over 1 s, against 438 ms
        # for calls on the held list). From half the TTL a thread lists the folder and the next call adopts its result,
        # patched from the watcher's events after the generation it read before listing (add and del are set operations,
        # so an event the listing already saw is a no-op). The TTL re-list in a call stays as the fallback.
        # Off: SPREAD_SERVER_NAMES_BG=0.
        bg = getattr(self, "names_bg", None)
        if bg is not None and bg[0] == "done":
            self.names_bg = None
            r = bg[1]
            if r is not None and (h is None or r[1] > h[1]) and r[1] >= w.armed_at + 0.5:
                h = self.names_hold = r
                self.misses.append("held_names:bg")
        if (NAMES_BG and os.name == "nt" and hasattr(ea, "_win_bulk_md_stats") and h is not None and sig is not None
                and getattr(self, "names_bg", None) is None and STATE_TTL / 2 <= now - h[1] < STATE_TTL
                and h[1] >= w.armed_at + 0.5):
            self._names_bg_start(w, ea, sr.MEM)
        # A generation change is patched from the watcher's own events: with ~1.7 new memories a minute,
        # 7 of the first 27 served calls re-listed on a generation change, and the listing was the largest frame in
        # each slow one (5-6 sampler hits, 0.7-1.2k faults). The folder lists in NTFS order, which for these names is
        # sorted(key=str.upper) (checked on all 53,563), so an added name is slotted in at its place; add and del are
        # applied as set operations, so an event the listing already saw is a no-op. An overflow, an evicted event, the
        # TTL or an unarmed hold still re-list.
        if (h is not None and sig is not None and h[0] != sig and now - h[1] < STATE_TTL
                and h[1] >= w.armed_at + 0.5):
            ev = w.since(h[0], sig)
            if ev is not None:
                names, have, n0 = list(h[2]), dict(h[3]), 0
                for _g, kind, nm in ev:
                    if not nm or not nm.endswith(".md"):
                        continue
                    if kind == "add" and nm not in have:
                        bisect.insort(names, nm, key=str.upper)
                        have[nm] = None
                        n0 += 1
                    elif kind == "del" and nm in have:
                        names.remove(nm)
                        del have[nm]
                        n0 += 1
                h = self.names_hold = (sig, h[1], names, self._NamesOnly(have))
                self.misses.append("held_names:patch:%d" % n0)
        if (h is None or sig is None or h[0] != sig or now - h[1] >= STATE_TTL
                or h[1] < w.armed_at + 0.5):
            # Clear the one-shot handoff first: _bulk_md_stats returns a _PREFETCH under 60 s old instead of reading,
            # and in this process the memoized loader seldom takes it, so before 5 Nov 22:4x each call's _mem_names got
            # the previous call's listing back and re-armed it with a fresh time - a frozen name list while calls kept
            # coming within 60 s of each other.
            if hasattr(ea, "_PREFETCH"):
                ea._PREFETCH = None
            st = ea._bulk_md_stats(sr.MEM)
            if st is None:
                return None
            h = self.names_hold = (sig, now, list(st), self._NamesOnly.fromkeys(st))
            if hasattr(ea, "_PREFETCH"):
                ea._PREFETCH = (sr.MEM, time.monotonic(), st)
            self.misses.append("held_names:list:%s" % (
                "new" if hold_was is None else "nowatch" if sig is None else "gen" if hold_was[0] != sig
                else "ttl" if now - hold_was[1] >= STATE_TTL else "arm"))
        sr._MEM_STATS = h[3]
        return h[2]

    def call(self, raw, hook="spread_recall"):
        if hook == "spread_recall":
            mod = self.sr
            if os.environ.get("SPREAD_SERVER_HOLD_NAMES", "1") != "0":
                mod.MEM_NAMES = self.held_names
                mod._MEM_STATS = None
        else:
            # other prompt hooks whose module caches are all stat-keyed (checked 5 Nov: fok_probe, supersession, bm25)
            mod = self.mods.get(hook)
            if mod is None:
                import importlib
                mod = self.mods[hook] = importlib.import_module(hook)
        real_in, real_out = sys.stdin, sys.stdout
        out = io.StringIO()
        self.misses = []
        try:
            sys.stdin = io.StringIO(raw)
            sys.stdout = out
            mod.main()
        finally:
            sys.stdin, sys.stdout = real_in, real_out
        self.calls += 1
        # the hook imports bm25, exact_clauses and others lazily inside main(): watch those too from now on
        for p in self._code_files():
            if p not in self.code_sig:
                self.code_sig[p] = st3(p)
        return out.getvalue()


def main():
    os.environ["SPREAD_SERVER_CHILD"] = "1"
    try:
        if hasattr(os, "nice"):
            os.nice(10)
        elif os.name == "nt":   # laptop port: Windows has no os.nice (AttributeError killed every spawn); BelowNormal
            import ctypes
            ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x00004000)
    except Exception:
        pass
    os.makedirs(STATE_DIR, exist_ok=True)
    warm = Warm()
    my_env = env_subset(os.environ)
    token = secrets.token_hex(16)
    last = [time.time()]
    lock = threading.Lock()
    stop = threading.Event()

    class H(socketserver.StreamRequestHandler):
        def handle(self):
            try:
                req = json.loads(self.rfile.readline().decode("utf-8"))
            except Exception:
                return
            if req.get("token") != token:
                return
            resp = {"ok": False}
            hook = req.get("hook") or "spread_recall"
            if hook not in HOSTED:
                resp["why"] = "not hosted: %s" % hook
            elif env_subset(req.get("env") or {}) != my_env:
                resp["why"] = "env differs"
            elif not lock.acquire(timeout=float(os.environ.get("SPREAD_SERVER_LOCK_WAIT", "8" if os.name == "nt" else "1.5"))):   # laptop 1.5 -> 8 s: 15 of 105 calls declined busy (5 Nov)
                resp["why"] = "busy"
            else:
                try:
                    why = warm.stale_reason()
                    if why:
                        resp["why"] = why
                        log("declined and exiting: " + why)
                        stop.set()
                    else:
                        t = time.perf_counter()
                        s0 = STALE_BM25_USED[0]
                        c0 = call_cost()
                        if OPEN_LOG_MS > 0:
                            _TRACE["ops"], _TRACE["thr"] = [], threading.current_thread()
                        fs = None
                        if FAULT_SAMPLE_MS > 0 and os.name == "nt":
                            try:
                                fs = FaultSampler(threading.current_thread())
                                fs.start()
                            except Exception:
                                fs = None
                        try:
                            resp = {"ok": True, "out": warm.call(req.get("raw") or "{}", hook),
                                    "ms": round((time.perf_counter() - t) * 1000, 1)}
                        finally:
                            _TRACE["thr"] = None
                            if fs is not None:
                                fs.done.set()
                        c1 = call_cost()
                        if fs is not None and (c1[1] - c0[1] >= FAULT_LOG_MIN or resp["ms"] >= FAULT_LOG_MS):
                            try:
                                fs.join(0.5)
                                row = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "hook": hook, "ms": resp["ms"],
                                       "pf": c1[1] - c0[1]}
                                row.update(fs.summary())
                                with open(FAULT_LOG, "a", encoding="utf-8") as f:
                                    f.write(json.dumps(row) + "\n")
                            except Exception:
                                pass
                        if OPEN_LOG_MS > 0 and resp["ms"] >= OPEN_LOG_MS:
                            try:
                                row = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "hook": hook, "ms": resp["ms"]}
                                row.update(open_summary(_TRACE["ops"] or [], time.perf_counter()))
                                with open(OPEN_LOG, "a", encoding="utf-8") as f:
                                    f.write(json.dumps(row) + "\n")
                            except Exception:
                                pass
                        # process CPU covers every thread, so a refresh running beside the call counts here too
                        resp["cost"] = {"cpu_ms": round((c1[0] - c0[0]) * 1000), "pf": c1[1] - c0[1],
                                        "ws_mb": round(c0[2]), "ws_in_mb": round(c1[2] - c0[2])}
                        if c0[3] and c1[3]:   # Windows only: file reads and other I/O (opens, stats) in the call
                            resp["cost"].update({"rd": c1[3][0] - c0[3][0], "rd_mb": round(c1[3][1] - c0[3][1], 1),
                                                 "oth": c1[3][2] - c0[3][2]})
                        if STALE_BM25_USED[0] != s0:   # the BM25 arm answered from the previous sidecar
                            resp["cost"]["stale_bm"] = STALE_BM25_USED[0] - s0
                        am = avail_mb()
                        if am is not None:   # machine RAM free at the end of the call: judge paging at equal pressure
                            resp["cost"]["avail_mb"] = am
                        try:
                            warm.save_keys()
                        except Exception:
                            pass
                        why = warm.stale_reason()
                        if why:
                            log("exit after answer: " + why)
                            stop.set()
                except Exception as e:
                    resp = {"ok": False, "why": "error %r" % (e,)}
                finally:
                    lock.release()
                    last[0] = time.time()
            try:
                self.wfile.write((json.dumps(resp) + "\n").encode("utf-8"))
            except Exception:
                pass
            try:
                sid = str((json.loads(req.get("raw") or "{}") or {}).get("session_id", ""))[:8]
                with open(CALL_LOG, "a", encoding="utf-8") as f:
                    f.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "sid": sid, "hook": hook,
                                    "ok": resp.get("ok"),
                                        "why": resp.get("why"), "ms": resp.get("ms"), "cost": resp.get("cost"),
                                        "miss": list(warm.misses) if resp.get("ok") else None}) + "\n")
            except Exception:
                pass

    srv = socketserver.TCPServer(("127.0.0.1", 0), H)
    port = srv.server_address[1]
    tmp = ENDPOINT + ".%d.tmp" % os.getpid()
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"port": port, "token": token, "pid": os.getpid(), "started": time.time()}, f)
    os.replace(tmp, ENDPOINT)
    log("serving on 127.0.0.1:%d" % port)
    th = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.5}, daemon=True)
    th.start()
    if os.environ.get("SPREAD_WARM_START", "1") != "0":
        def warm_start():
            if lock.acquire(timeout=5):
                try:
                    t = time.perf_counter()
                    n = warm.prefill()
                    log("warm start: %d loader calls filled in %.2f s" % (n, time.perf_counter() - t))
                    if WS_MIN_MB > 0:
                        log("working-set floor: %s" % ws_floor(WS_MIN_MB))
                except Exception as e:
                    log("warm start error %r" % (e,))
                finally:
                    lock.release()
        threading.Thread(target=warm_start, daemon=True).start()
    try:
        tick = 0
        while not stop.wait(5):
            if time.time() - last[0] > IDLE_EXIT:
                log("idle exit")
                break
            tick += 1
            if tick % 2 == 0 and SERVE_STALE:
                # the stale check under the lock, the reload off it: a caller meanwhile is served the held value
                if lock.acquire(blocking=False):
                    try:
                        why = warm.stale_reason()
                    finally:
                        lock.release()
                    if why:
                        log("exiting between calls: " + why)
                        break
                try:
                    t = time.perf_counter()
                    n = warm.refresh()
                    if n and time.perf_counter() - t > 1:
                        log("refreshed %d entries off the lock in %.1f s" % (n, time.perf_counter() - t))
                except Exception as e:
                    log("refresh error %r" % (e,))
            elif tick % 2 == 0 and lock.acquire(blocking=False):
                try:
                    why = warm.stale_reason()
                    if why:
                        log("exiting between calls: " + why)
                        break
                    warm.refresh()
                except Exception as e:
                    log("refresh error %r" % (e,))
                finally:
                    lock.release()
    finally:
        srv.shutdown()
        try:
            with open(ENDPOINT, encoding="utf-8") as f:
                if json.load(f).get("pid") == os.getpid():
                    os.remove(ENDPOINT)
        except Exception:
            pass
        log("stopped after %d calls" % warm.calls)


if __name__ == "__main__":
    main()
