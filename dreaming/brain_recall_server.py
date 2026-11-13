"""brain_recall_server.py - a resident process that answers `brain.py recall` with its built state kept warm.

RECALL-10X lead 1 for brain.py (claude-fb4584c3, 3 Nov 2026). A cold `brain.py recall` spends most of its CPU
reloading state from disk (file caches, the superseded map, listings, tiers, archived index lines, query
expansions, node documents, the fresh-memory scan). This process loads brain.py once, memoizes those reload
functions, and serves recall calls from brain_recall_client.py over localhost.

Correctness rules:
- Every memo is dropped when the state signature changes (memory folder, MEMORY.md, the BM25 index, the
  embedding sidecar, the superseded index, the graph sidecar) and at most STATE_TTL seconds after it was filled.
- The server exits (after answering) when any brain module it imported changed on disk, so a code change is
  never served by old code; the next client call runs cold and starts a fresh server.
- A call whose BRAIN_* environment differs from the server's is declined, and the client runs cold.
- One call at a time; a call that would wait more than LOCK_WAIT seconds is declined (client runs cold).
- Exits after IDLE_EXIT seconds without a call, or when its working set passes RSS_CAP, or when one call has held
  the lock past ORPHAN_S (its client stopped reading at 180 s, so nobody is waiting for the answer).
Launched windowless by the client (python.exe with CREATE_NO_WINDOW, so its own children inherit a hidden
console). Read-only on the brain except what recall itself writes (its usage logs). Never deletes anything.
"""

import sys
if __name__ == "__main__" and any(arg in ("-h", "--help") for arg in sys.argv[1:]):
    print(__doc__ or "No command-line options.")
    raise SystemExit(0)

import contextlib, functools, io, json, os, secrets, socket, socketserver, sys, threading, time

D = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(D, "brain_recall_server")
ENDPOINT = os.path.join(STATE_DIR, "endpoint.json")
LOG = os.path.join(STATE_DIR, "server.log")
CALL_LOG = os.path.join(STATE_DIR, "calls.jsonl")
STATE_TTL = float(os.environ.get("BRS_STATE_TTL", "120"))
IDLE_EXIT = float(os.environ.get("BRS_IDLE_EXIT", "7200"))
LOCK_WAIT = float(os.environ.get("BRS_LOCK_WAIT", "20"))
# A call still holding the lock this long has outlived its client's 180 s read timeout: nobody reads its answer,
# and every caller behind it is declined busy. 0 turns the exit off.
ORPHAN_S = float(os.environ.get("BRS_ORPHAN_S", "200"))
RSS_CAP = int(float(os.environ.get("BRS_RSS_CAP_GB", "2.5")) * 1024 ** 3)
# Mac, 5 Nov 2026 (claude-1ca628ae): every BM25 rebuild unlinks the old postings sidecar (~157 MB), but a reader this
# process opened keeps it on disk until we exit; after 19 h one server held 11 of them (2.3 GiB with other deleted
# files) while the owner's film ran short of disk. Exit after answering once the index has been replaced this many
# times; the next client call starts a fresh server (0.1 s load). 0 turns it off.
# 5 Nov 03:44 (claude-7df69752): cap 3 -> 1. Rebuilds come about every 35 min (3 in 1 h 44 min on 5 Nov) and a fresh
# server loads in 0.1 s, so exiting on the first one costs little and holds at most one dead generation (~157 MB).
BM25_GEN_CAP = int(os.environ.get("BRS_BM25_GEN_CAP", "1"))
BM25_STAY = int(os.environ.get("BRS_BM25_STAY", "12"))
BM25_INDEX = os.path.join(D, "bm25_index.json.gz")


def bm25_gen():
    try:
        st = os.stat(BM25_INDEX)
        return (st.st_ino, st.st_mtime_ns)
    except OSError:
        return None
GROUP = {"_tiers": "tiers", "_archived_index_lines": "archive", "expansions": "selfkeyed", "_fc_get": "selfkeyed"}
EXACT_GROUPS = frozenset() if os.environ.get("BRS_EXACT_NO_TTL", "1") == "0" else frozenset({"tiers", "archive"})
MEMO_NAMES = ("_fc_get", "_load_superseded", "_memlist", "_fast_listing", "_archived_index_lines", "_tiers",
              "_fc_fresh_memories", "_fc_overlay", "_sup_rows")


def _faults():
    """(major, minor) page faults of this process so far, or None where getrusage is missing (Windows).
    On a Mac under the film's memory pressure (6 Nov 2026) the idle server sat mostly compressed or swapped (762 MB
    footprint, 42 MB resident), and a call after a gap paged its state back in; pf in the call row shows that."""
    try:
        import resource
        ru = resource.getrusage(resource.RUSAGE_SELF)
        return (ru.ru_majflt, ru.ru_minflt)
    except Exception:
        return None


def _should_warm(env):
    """Every fresh server warms itself before its first caller unless BRS_WARMUP=0 (successor or not)."""
    return env.get("BRS_WARMUP", "1") != "0"


def log(msg):
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
    except Exception:
        pass


# BRAIN_ variables brain.py reads on each call (never at import), so the server applies the caller's value for the call
# instead of declining. 3 Nov 2026: 91 of 98 recall calls were declined "brain env differs", all from compact_nodes.py's
# linker, which sets BRAIN_RECALL_REFRESH=0. BRS_PER_CALL_ENV=0 restores the old decline.
# 5 Nov 2026 (claude-7df69752, Mac): BRAIN_RECALL_LOG too. graph_lane.log_recall reads it on each call, and every test
# bank sets BRAIN_RECALL_LOG=0, so all of them were declined and ran cold.
# 5 Nov 2026 (claude-83c711d4, the research box): BRAIN_HEADLESS_DIET too. Only hook_dispatch reads it, never recall, and it
# was the laptop's one remaining env decline (55 calls in 24 h).
PER_CALL_ENV = () if os.environ.get("BRS_PER_CALL_ENV", "1") == "0" else (
    "BRAIN_RECALL_REFRESH", "BRAIN_RECALL_LOG", "BRAIN_HEADLESS_DIET")
CLIENT_ONLY_ENV = ("BRAIN_RECALL_SERVER",)   # read by brain_recall_client, never by brain.py


def brain_env(env):
    return {k: v for k, v in env.items()
            if k.startswith("BRAIN_") and k not in PER_CALL_ENV and k not in CLIENT_ONLY_ENV}


def rss_bytes():
    try:
        import ctypes
        from ctypes import wintypes

        class PMC(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
        pmc = PMC(); pmc.cb = ctypes.sizeof(PMC)
        # The pseudo-handle as a pointer: with ctypes' default int restype it reached psapi as an invalid handle on
        # 64-bit, the call failed and this returned 0 every time, so RSS_CAP never fired (4 Nov: 0 against a 342 MB
        # psutil reading).
        h = ctypes.c_void_p(-1)
        if ctypes.windll.psapi.GetProcessMemoryInfo(h, ctypes.byref(pmc), pmc.cb):
            return int(pmc.WorkingSetSize)
    except Exception:
        pass
    try:  # macOS (4 Nov 2026): peak resident size in bytes; a cap on the peak is the cautious reading
        import resource
        if sys.platform == "darwin":
            return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    except Exception:
        pass
    return 0


class Brain:
    def __init__(self):
        sys.path.insert(0, D)
        os.chdir(D)
        import runpy
        self.g = runpy.run_path(os.path.join(D, "brain.py"), run_name="brain_recall_server_module")["main"].__globals__
        import bm25, brain_node_documents as bnd
        self.stores = []
        self.miss = {}
        stat_fc = os.environ.get("BRS_STAT_FCGET", "1") != "0" and "_fc_file" in self.g and "_fc_get" in self.g
        for name in MEMO_NAMES:
            if name in self.g and not (stat_fc and name == "_fc_get"):
                self._memo(self.g, name)
        if stat_fc:
            self._stat_memo_fc_get()
        # brain.py's two callers of _archived_index_lines .update() the dict with the live index (_recall_body, the
        # owner-line pass), so a memo that hands back its own dict accumulates earlier calls' live lines: a slug that
        # left MEMORY.md would stay in the archive view until the group dropped. Each call gets a copy (47k entries,
        # a few ms). Found by the lever-6 parity control on 3 Nov; present since lever 1, bounded then by the TTL.
        if "_archived_index_lines" in self.g:
            memo = self.g["_archived_index_lines"]
            real = getattr(memo, "__wrapped__", memo)

            def arch_copy(*a, **k):
                return dict(memo(*a, **k))
            arch_copy.__wrapped__ = real
            arch_copy.__name__ = "_archived_index_lines"
            self.g["_archived_index_lines"] = arch_copy
        self._memo(bm25, "expansions")
        if os.environ.get("BRS_STAT_BUNDLES", "1") != "0":
            self._stat_memo_bundles(bnd)
        else:
            self._memo(bnd, "load_documents")
        if "load_documents" in self.g:
            self.g["load_documents"] = bnd.load_documents
        if os.environ.get("BRS_STAT_READDOC", "1") != "0":
            try:
                self._stat_memo_read_doc(bm25, bnd)
            except Exception as e:
                log("read_doc stat memo off: %s: %s" % (type(e).__name__, e))
        mem = self.g.get("MEM") or ""
        folder = [mem, os.path.join(mem, "MEMORY.md")]
        for name in ("bm25_index.json.gz", "memory_embeddings.npz", "superseded_index.json"):
            folder.append(os.path.join(D, name))
        for env_name in ("BRAIN_GRAPH_SIDECAR", "BRAIN_SUPERSEDED_INDEX"):
            if os.environ.get(env_name):
                folder.append(os.environ[env_name])
        self.group_paths = {"folder": folder, "tiers": [os.path.join(D, "consciousness.tsv")],
                            "archive": [os.path.join(mem, "INDEX_ARCHIVE.md")], "selfkeyed": []}
        self.sigs = {}
        self.filled = {}
        # lever 3: the memory folder's listing kept resident by ReadDirectoryChangesW, so a folder drop no longer
        # re-reads ~50k entries (parity 20/20 live, 2000-file burst and forced overflow equal, 10/10 ranked lists
        # equal in resident_ab.jsonl). BRS_RESIDENT_LISTING=0 turns it off; any failure leaves the memo path.
        self.resident = None
        # macOS (6 Nov 2026): the same lever on FSEvents, for brain._bulk_listing (brain_resident_listing_mac.py has
        # the parity: 46,233 of 46,233 entries, 45 of 45 scratch waves, live rounds; listing 99 -> 13 ms).
        if os.environ.get("BRS_RESIDENT_LISTING", "1") != "0" and sys.platform == "darwin" and "_bulk_listing" in self.g:
            try:
                import brain_resident_listing_mac
                self.resident = brain_resident_listing_mac.install(self)
                log("resident listing (FSEvents) on")
            except Exception as e:
                log("resident listing (FSEvents) off: %s: %s" % (type(e).__name__, e))
        elif os.environ.get("BRS_RESIDENT_LISTING", "1") != "0" and "_fast_listing" in self.g:
            try:
                import brain_resident_listing
                self.resident = brain_resident_listing.install(self)
            except Exception as e:
                log("resident listing off: %s: %s" % (type(e).__name__, e))
        # Lever 8 (3 Nov 2026): keep the PARSED embeddings sidecar between calls (embedding_artifact.RESIDENT, keyed
        # by the file's stat plus the authority's sha256 and size); which rows are current is still judged on every
        # call. Loader 1.0-2.4 s -> 0.5-0.65 s on the loaded laptop, 16/16 recall outputs equal. BRS_EMB_RESIDENT=0 off.
        if os.environ.get("BRS_EMB_RESIDENT", "1") != "0":
            try:
                import embedding_artifact
                embedding_artifact.RESIDENT = True
            except Exception as e:
                log("resident embeddings off: %s: %s" % (type(e).__name__, e))
        # Lever 9 (3 Nov 2026): keep the link walk's neighbour lists per sidecar generation (graph_lane.LAZY_RESIDENT);
        # the capture rewrites the sidecar every 15 minutes, which starts a fresh set. BRS_LINK_RESIDENT=0 off.
        if os.environ.get("BRS_LINK_RESIDENT", "1") != "0":
            try:
                import graph_lane
                graph_lane.LAZY_RESIDENT = True
            except Exception as e:
                log("resident link lists off: %s: %s" % (type(e).__name__, e))
        # Lever 10 (5 Nov 2026): exact numpy forms of bm25.score_postings, the embeddings loader and _spread
        # (recall_levers.py has the proofs). Each is admitted only for a source version it was checked on and falls
        # back to the original otherwise. BRS_EXACT_NP=0 off.
        try:
            import recall_levers
            put = recall_levers.install(self)
            if put:
                log("exact numpy levers: %s" % ",".join(put))
        except Exception as e:
            log("exact numpy levers off: %s: %s" % (type(e).__name__, e))
        self.code = self._code_sig()
        self.calls = 0

    def _memo(self, holder, attr):
        f = holder[attr] if isinstance(holder, dict) else getattr(holder, attr)
        store = {}
        self.stores.append((GROUP.get(attr, "folder"), store))
        miss = self.miss

        @functools.wraps(f)
        def w(*a, **k):
            try:
                key = repr((a, sorted(k.items())))
            except Exception:
                return f(*a, **k)
            if key in store:
                return store[key]
            t = time.perf_counter()
            v = f(*a, **k)
            miss[attr] = miss.get(attr, 0.0) + time.perf_counter() - t
            # _fc_overlay's None means "take the payload merge this time" (no sidecar yet, generation moved, ...).
            # The postings sidecar lands 2 s to 5 min after its index and is not in the folder signature, so a kept
            # None sent every later call down refresh_in_memory (~23k page faults, ~0.5 s, Mac 6 Nov 2026) until the
            # next memory write. A None is not kept; each one is counted in the call row. BRS_OVL_NONE_MEMO=1 keeps it.
            if v is None and attr == "_fc_overlay" and os.environ.get("BRS_OVL_NONE_MEMO", "0") != "1":
                miss["_fc_overlay:none"] = miss.get("_fc_overlay:none", 0.0) + 1.0
                return v
            store[key] = v
            return v
        if isinstance(holder, dict):
            holder[attr] = w
        else:
            setattr(holder, attr, w)

    def _stat_memo_read_doc(self, bm25, bnd, cap=1024):
        """lever 15: keep each memory's BM25 token list across folder drops while its files are unchanged.

        A folder drop (any peer memory write) re-ran the fresh-memory overlay, which re-read EVERY memory newer than
        the current postings generation, not the one that changed: 69 fresh at 15:37 on 4 Nov cost the overlay
        244-502 ms a drop, nearly all of it bm25.read_doc. Here a token list is kept while the memory file's
        (mtime_ns, size), its attachment bundle's stat signature (node file, manifest, documents listing - the same
        signature lever 4 uses) and the slug's query-expansion entry are unchanged. A read that adds to
        bm25.ATTACHMENT_FAILURES is not kept. Each caller gets its own copy of the list. The store sits in no group
        and holds at most `cap` slugs, oldest use dropped first. BRS_STAT_READDOC=0 turns it off.
        """
        from collections import OrderedDict
        real = bm25.read_doc
        store = OrderedDict()
        lock = threading.Lock()
        miss = self.miss

        def sig(mem, slug):
            st = os.stat(os.path.join(mem, slug + ".md"))
            out = [(st.st_mtime_ns, st.st_size)]
            root = bnd.bundle_dir(mem, slug)
            for p in (bnd._node_path(bnd.Path(mem), bnd._slug(slug)), root / bnd.MANIFEST_NAME):
                try:
                    s2 = os.lstat(p)
                    out.append((s2.st_mtime_ns, s2.st_size))
                except OSError:
                    out.append(None)
            try:
                with os.scandir(root / "documents") as it:
                    out.append(tuple(sorted((e.name, e.stat(follow_symlinks=False).st_mtime_ns,
                                             e.stat(follow_symlinks=False).st_size) for e in it)))
            except OSError:
                out.append(None)
            out.append((getattr(bm25, "_DATES_ON", None), getattr(bm25, "_EXPANSION_ON", None)))
            if getattr(bm25, "_EXPANSION_ON", False):
                out.append(repr(bm25.expansions().get(slug)))
            return tuple(out)

        @functools.wraps(real)
        def w(mem_dir, slug):
            try:
                key = (str(mem_dir), slug)
                s = sig(str(mem_dir), slug)
            except Exception:
                return real(mem_dir, slug)
            # bm25.read_many calls this from a thread pool, so the store's reorder and eviction take a lock
            with lock:
                hit = store.get(key)
                if hit is not None and hit[0] == s:
                    store.move_to_end(key)
                    return None if hit[1] is None else list(hit[1])
            t = time.perf_counter()
            nfail = len(getattr(bm25, "ATTACHMENT_FAILURES", ()))
            v = real(mem_dir, slug)
            with lock:
                miss["read_doc"] = miss.get("read_doc", 0.0) + time.perf_counter() - t
                if len(getattr(bm25, "ATTACHMENT_FAILURES", ())) == nfail:
                    store[key] = (s, None if v is None else tuple(v))
                    store.move_to_end(key)
                    while len(store) > cap:
                        store.popitem(last=False)
            return v
        w.__wrapped__ = real
        bm25.read_doc = w

    def _stat_memo_bundles(self, bnd):
        """lever 4: keep each attachment bundle across folder drops while its own files are unchanged.

        Under the folder memo every memory write anywhere reloaded and re-hashed all 41 bundles (0.22 s, 1 MB);
        the stat signature of one bundle (node file, manifest, documents listing with mtime and size) costs 0.015 s
        for all 41. A writer publishes by replacing the manifest atomically, so any attach changes the signature.
        The store is in no group, so neither a folder drop nor the state TTL clears it; a code change restarts the
        server. BRS_STAT_BUNDLES=0 restores the folder memo.
        """
        f = bnd.load_documents
        store = {}
        miss = self.miss

        def sig(mem, slug):
            out = []
            try:
                st = os.lstat(bnd._node_path(bnd.Path(mem), bnd._slug(slug)))
                out.append((st.st_mtime_ns, st.st_size))
            except Exception:
                out.append(None)
            root = bnd.bundle_dir(mem, slug)
            try:
                st = os.lstat(root / bnd.MANIFEST_NAME)
                out.append((st.st_mtime_ns, st.st_size))
            except Exception:
                out.append(None)
            try:
                with os.scandir(root / "documents") as it:
                    rows = []
                    for e in it:
                        st = e.stat(follow_symlinks=False)
                        rows.append((e.name, st.st_mtime_ns, st.st_size))
                out.append(tuple(sorted(rows)))
            except Exception:
                out.append(None)
            return tuple(out)

        @functools.wraps(f)
        def w(mem, slug, *a, **k):
            try:
                key = repr((str(mem), slug, a, sorted(k.items())))
                s = sig(mem, slug)
            except Exception:
                return f(mem, slug, *a, **k)
            hit = store.get(key)
            if hit is not None and hit[0] == s:
                return hit[1]
            t = time.perf_counter()
            v = f(mem, slug, *a, **k)
            miss["load_documents"] = miss.get("load_documents", 0.0) + time.perf_counter() - t
            store[key] = (s, v)
            return v
        bnd.load_documents = w

    def _stat_memo_fc_get(self, cap=64):
        """lever 5: keep each recall fast-cache file's parsed value while that file is unchanged.

        Under the self-keyed memo every _fc_get value (walk, links, df, sgraph, superseded head and rows, tfkeys)
        was thrown away by the 120 s state TTL and re-unmarshalled, 0.3-0.7 s a call, and a miss (None) was
        memoized until the TTL even after the file was written. Here the key is the cache file's path (one file
        per (name, key) by construction) and the value is kept only while the file's (mtime, size, id) match a
        stat taken before and after the read; a missing file or a None is never kept. One os.stat per lookup.
        The store sits in no group and holds at most `cap` files, newest use last. BRS_STAT_FCGET=0 turns it off.
        """
        from collections import OrderedDict
        real = self.g["_fc_get"]
        path_of = self.g["_fc_file"]
        store = OrderedDict()
        miss = self.miss

        def st3(p):
            st = os.stat(p)
            return (st.st_mtime_ns, st.st_size, st.st_ino)

        @functools.wraps(real)
        def w(name, key):
            try:
                p = path_of(name, key)
                s = st3(p)
            except Exception:
                return real(name, key)
            hit = store.get(p)
            if hit is not None and hit[0] == s:
                store.move_to_end(p)
                return hit[1]
            t = time.perf_counter()
            v = real(name, key)
            dt = time.perf_counter() - t
            miss["_fc_get"] = miss.get("_fc_get", 0.0) + dt
            # which entry the miss read (6 Nov 2026): on a Mac _fc_get missed on 46 of 107 folder drops, p50 0.11 s,
            # and the total did not say which. Only the call row's miss_s carries it.
            kk = "_fc_get:" + str(name)[:40]
            miss[kk] = miss.get(kk, 0.0) + dt
            if v is not None:
                try:
                    if st3(p) == s:
                        store[p] = (s, v)
                        store.move_to_end(p)
                        while len(store) > cap:
                            store.popitem(last=False)
                except Exception:
                    pass
            return v
        self.g["_fc_get"] = w

    def _code_sig(self):
        sig = {}
        for m in list(sys.modules.values()):
            p = getattr(m, "__file__", None) or ""
            if p and os.path.abspath(p).lower().startswith(D.lower()) and p.endswith(".py"):
                try:
                    sig[p] = os.stat(p).st_mtime_ns
                except OSError:
                    sig[p] = None
        sig[os.path.join(D, "brain.py")] = os.stat(os.path.join(D, "brain.py")).st_mtime_ns
        return sig

    def code_changed(self):
        for p, m in self.code.items():
            try:
                now = os.stat(p).st_mtime_ns
            except OSError:
                now = None
            if now != m:
                return p
        # A module imported after start joins the watch list when first seen (6 Nov 2026): modules brain.py imports
        # inside a call were never in the start-time list, so an edit to one did not respawn the server. Its mtime is
        # taken here, one call after its import, so an edit landing inside that one call is seen only at the next
        # edit. BRS_LATE_IMPORT_WATCH=0 keeps the start-time list.
        if os.environ.get("BRS_LATE_IMPORT_WATCH", "1") != "0":
            for m in list(sys.modules.values()):
                p = getattr(m, "__file__", None) or ""
                if p and p not in self.code and p.endswith(".py") and os.path.abspath(p).lower().startswith(D.lower()):
                    try:
                        self.code[p] = os.stat(p).st_mtime_ns
                    except OSError:
                        self.code[p] = None
        return None

    def _state_sig(self, paths):
        out = []
        for p in paths:
            try:
                st = os.stat(p)
                out.append((p, st.st_mtime_ns, st.st_size, st.st_ino))
            except OSError:
                out.append((p, None, None, None))
        return tuple(out)

    def refresh_state(self):
        """lever 6: the tiers and archive groups each read exactly one file (consciousness.tsv, INDEX_ARCHIVE.md) and
        nothing else, so their stat signature (mtime_ns, size, inode) is the whole of their state: they drop only when
        that file changes, never on the TTL (which threw away 0.2-0.5 s of re-reads on every call made more than two
        minutes after the last). The folder group keeps the TTL: its signature does not cover everything its memos
        read. BRS_EXACT_NO_TTL=0 puts the TTL back on every group."""
        dropped = []
        now = time.time()
        for grp, paths in self.group_paths.items():
            sig = self._state_sig(paths)
            ttl_out = grp not in EXACT_GROUPS and now - self.filled.get(grp, 0) > STATE_TTL
            old_sig = self.sigs.get(grp)
            if sig != old_sig or ttl_out:
                for g, st in self.stores:
                    if g == grp:
                        st.clear()
                self.sigs[grp] = sig
                self.filled[grp] = now
                # "folder~ttl" in the call row (6 Nov 2026): dropped on the TTL alone, its signature unchanged
                dropped.append(grp if sig != old_sig else grp + "~ttl")
        return dropped

    def call(self, argv, env, cwd):
        pf0 = _faults()
        dropped = self.refresh_state()
        self.miss.clear()
        saved_env = {}
        for k, v in env.items():
            if k.startswith(("CLAUDE", "CODEX", "AGENT")):
                saved_env[k] = os.environ.get(k)
                os.environ[k] = v
        # 7 Nov 2026: a caller that sends no session id ran under the spawner's (its CLAUDE_CODE_SESSION_ID and
        # ENTRYPOINT stayed in the server's environ), so script and Codex searches were logged as a person's
        # (the research box: 179 of 2,607 served calls in 48 h). Unset every such name the caller did not send.
        # BRS_POP_SESSION_ENV=0 restores the old behaviour.
        if os.environ.get("BRS_POP_SESSION_ENV", "1") != "0":
            for k in [k for k in os.environ if k.startswith(("CLAUDE", "CODEX", "AGENT")) and k not in env]:
                saved_env[k] = os.environ.pop(k)
        for k in PER_CALL_ENV:   # the caller's value, or unset when the caller has none
            saved_env[k] = os.environ.get(k)
            if k in env:
                os.environ[k] = env[k]
            else:
                os.environ.pop(k, None)
        out, err = io.StringIO(), io.StringIO()
        rc = 0
        old_argv = sys.argv
        sys.argv = [os.path.join(D, "brain.py")] + list(argv)
        # slow-call stack samples (6 Nov 2026, Mac): a sampling thread, no tracing; rows only for calls of 500 ms+
        try:
            import brain_call_sampler
            smp = brain_call_sampler.begin()
        except Exception:
            smp = None
        t0 = time.time()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                try:
                    self.g["main"]()
                except SystemExit as e:
                    rc = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
        except Exception as e:
            rc = 1
            err.write("brain_recall_server: %s: %s\n" % (type(e).__name__, e))
        finally:
            sys.argv = old_argv
            for k, v in saved_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            try:
                os.chdir(D)
            except OSError:
                pass
        self.calls += 1
        pf1 = _faults()
        if smp is not None:
            try:
                smp.stop()
                smp.write(os.path.join(STATE_DIR, "slow_samples.jsonl"), int((time.time() - t0) * 1000),
                          {"op": (argv or ["?"])[0], "dropped": dropped,
                           "pf": [pf1[0] - pf0[0], pf1[1] - pf0[1]] if pf0 and pf1 else None,
                           "miss_s": {k: round(v, 2) for k, v in self.miss.items() if v >= 0.05}})
            except Exception:
                pass
        return {"rc": rc, "out": out.getvalue(), "err": err.getvalue(), "ms": int((time.time() - t0) * 1000),
                "dropped": dropped, "calls": self.calls,
                "pf": [pf1[0] - pf0[0], pf1[1] - pf0[1]] if pf0 and pf1 else None,
                "miss_s": {k: round(v, 2) for k, v in self.miss.items() if v >= 0.05}}


def main():
    os.makedirs(STATE_DIR, exist_ok=True)
    t0 = time.time()
    brain = Brain()
    base_env = brain_env(os.environ)
    lock = threading.Lock()
    token = secrets.token_hex(16)
    last = {"t": time.time(), "bm25": bm25_gen(), "gens": 0}
    cur = {"t0": None}   # when the call now holding the lock took it
    orphaned = {"s": None}
    stop = threading.Event()

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            try:
                req = json.loads(self.rfile.readline().decode("utf-8"))
            except Exception:
                return
            if req.get("token") != token:
                return
            reply = {"decline": None}
            if req.get("op") == "ping":
                reply = {"ok": True, "pid": os.getpid(), "calls": brain.calls}
            elif not lock.acquire(timeout=LOCK_WAIT):
                reply = {"decline": "busy"}
            else:
                cur["t0"] = time.time()
                try:
                    # code first (3 Nov 2026): with the env check ahead of it, a server whose callers were all declined
                    # on env never saw its own code change and kept running the old code.
                    changed = brain.code_changed()
                    if changed:
                        reply = {"decline": "code changed: " + os.path.basename(changed)}
                        log("code changed: %s" % changed)
                        stop.set()
                    elif brain_env(req.get("env") or {}) != base_env:
                        reply = {"decline": "brain env differs"}
                    else:
                        reply = brain.call(req.get("argv") or [], req.get("env") or {}, req.get("cwd") or D)
                        # one row per call (3 Nov 2026): which groups dropped and what each miss cost in real use,
                        # so the next lever is picked from live calls, not forced drops. BRS_CALL_LOG=0 turns it off.
                        if os.environ.get("BRS_CALL_LOG", "1") != "0":
                            try:
                                with open(CALL_LOG, "a", encoding="utf-8") as fh:
                                    fh.write(json.dumps({"t": round(time.time(), 1), "gap_s": round(time.time() - last["t"], 1),
                                                         "op": (req.get("argv") or ["?"])[0], "ms": reply.get("ms"),
                                                         "sess": ((req.get("env") or {}).get("CLAUDE_CODE_SESSION_ID")
                                                                  or (req.get("env") or {}).get("CODEX_THREAD_ID") or "")[:8],
                                                         "dropped": reply.get("dropped"), "miss_s": reply.get("miss_s"),
                                                         "pf": reply.get("pf")}) + "\n")
                            except Exception:
                                pass
                        if rss_bytes() > RSS_CAP:
                            log("rss over cap after a call; exiting")
                            stop.set()
                        g = bm25_gen()
                        if g != last["bm25"]:
                            last["bm25"], last["gens"] = g, last["gens"] + 1
                        if BM25_GEN_CAP and last["gens"] >= BM25_GEN_CAP:
                            # 6 Nov 2026 (claude-95df24fa, Mac): stay up through a rebuild when nothing deleted is
                            # still held. The exits below were 92 of 117 server starts in 24 h, and a fresh server's
                            # first live call was p50 1.33 s. brain_bm25_release closes the old postings connections,
                            # clears the once-per-process df/usage caches and lists any regular file of 1 MB+ still
                            # open with no name on disk; a non-empty list exits as before so the disk comes back.
                            # In-process A/B (ab_release v1-v3): a released warm server equalled a cold process on
                            # every query of every rebuild. At most BRS_BM25_STAY rebuilds in a row (default 12),
                            # then one exit, so a cache nobody has found cannot drift for long. BRS_BM25_RELEASE=0
                            # restores the plain exit.
                            rel = None
                            if os.environ.get("BRS_BM25_RELEASE", "1") != "0" and last.get("stayed", 0) < BM25_STAY:
                                try:
                                    import brain_bm25_release
                                    rel = brain_bm25_release.release(brain.g)
                                    if rel.get("held_deleted"):
                                        # a rebuild that landed mid-call leaves the old BM25 object (and its
                                        # postings connection) in the folder store until the next call drops it;
                                        # drop every store now (a full refill in a warm process, ~0.2 s next
                                        # call) and look again
                                        import gc
                                        for _grp, st in brain.stores:
                                            st.clear()
                                        brain.sigs.clear()
                                        brain.filled.clear()
                                        gc.collect()
                                        rel = brain_bm25_release.release(brain.g)
                                        rel["cleared"] = True
                                except Exception as e:
                                    log("bm25 release failed: %s: %s" % (type(e).__name__, e))
                                    rel = None
                            if rel is not None and not rel.get("held_deleted"):
                                last["stayed"] = last.get("stayed", 0) + 1
                                log("bm25 index replaced; released the old generation, staying up (%d in a row)%s"
                                    % (last["stayed"], (" (closed %d dead postings connections; held by %s%s)"
                                                         % (rel["dead_closed"], ", ".join(rel.get("dead_owners") or []),
                                                            "; stores cleared" if rel.get("cleared") else ""))
                                                        if rel.get("dead_closed") else
                                                        (" (stores cleared)" if rel.get("cleared") else "")))
                                last["gens"] = 0
                            else:
                                held = (rel or {}).get("held_deleted") or []
                                log("bm25 index replaced %d times; exiting so its deleted postings files are freed%s"
                                    % (last["gens"], (" (held: %s)" % ", ".join(os.path.basename(h[2]) or str(h[0])
                                                                             for h in held[:3])) if held else ""))
                                last["respawn"] = True
                                stop.set()
                finally:
                    cur["t0"] = None
                    last["t"] = time.time()
                    lock.release()
            try:
                self.wfile.write((json.dumps(reply) + "\n").encode("utf-8"))
            except Exception:
                pass

    class Server(socketserver.ThreadingTCPServer):
        daemon_threads = True
        allow_reuse_address = False

    srv = Server(("127.0.0.1", 0), Handler)
    port = srv.server_address[1]
    tmp = ENDPOINT + ".%d.tmp" % os.getpid()
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"port": port, "token": token, "pid": os.getpid(), "started": time.time(),
                   "load_s": round(time.time() - t0, 2)}, f)
    os.replace(tmp, ENDPOINT)
    log("serving pid %d port %d after %.1f s load" % (os.getpid(), port, time.time() - t0))
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    # 6 Nov 2026 (claude-95df24fa, Mac): a successor warms itself before its first caller. 98 of 115 server starts in
    # 24 h were bm25 replacements, and the first call a fresh server answered cost p50 1484 ms (70 calls, 117 of the
    # 170 s of live recall time). In-process, two warm-up recalls (1.4-2.3 s, log and refresh off) took the first real
    # call to 136-228 ms against 680-1090 ms cold; one warm-up left it at 577-826. A caller arriving mid warm-up waits on
    # the lock as usual. At first only a successor warmed, on the belief that a client-started server has its client
    # waiting; it does not (brain_recall_client starts the server and runs that call cold). On the Mac from 07:52 to
    # 09:28 on 6 Nov the first live call on 4 warmed servers took 247-687 ms and on 5 unwarmed ones 897-2370 ms, so
    # every fresh server warms now (_should_warm).
    # The per-call keys go in the env passed to call(): call() unsets any the env lacks. BRS_WARMUP=0 turns it off.
    if _should_warm(os.environ):
        def warm():
            if not lock.acquire(timeout=1):
                return
            try:
                tw = time.time()
                for q in ("brain recall warm up of the lanes", "second warm call for the seed index"):
                    brain.call(["recall", q], {"BRAIN_RECALL_LOG": "0", "BRAIN_RECALL_REFRESH": "0"}, D)
                log("warm-up: 2 recalls in %d ms" % ((time.time() - tw) * 1000))
            except Exception as e:
                log("warm-up failed: %s: %s" % (type(e).__name__, e))
            finally:
                lock.release()
        threading.Thread(target=warm, daemon=True).start()
    try:
        while not stop.wait(15):
            if time.time() - last["t"] > IDLE_EXIT:
                log("idle exit")
                break
            t0c = cur["t0"]
            if ORPHAN_S > 0 and t0c is not None and time.time() - t0c > ORPHAN_S:
                orphaned["s"] = round(time.time() - t0c, 1)
                log("a call has held the lock %.0f s, past its client's read timeout; exiting" % orphaned["s"])
                try:
                    with open(CALL_LOG, "a", encoding="utf-8") as fh:
                        fh.write(json.dumps({"t": round(time.time(), 1), "op": "orphaned", "ms": int(orphaned["s"] * 1000),
                                             "aborted": True}) + "\n")
                except Exception:
                    pass
                break
            # 6 Nov 2026: the resident listing's periodic full walk (FULL_EVERY, 600 s) ran inside
            # whichever recall came after it fell due. Here it runs between calls, a tick or so early; a call that
            # arrives meanwhile waits on the lock (~0.2 s). BRS_IDLE_FULL=0 turns it off.
            rb = brain.resident
            if (rb is not None and hasattr(rb, "refresh_if_due") and os.environ.get("BRS_IDLE_FULL", "1") != "0"
                    and time.time() - last["t"] > 2 and lock.acquire(blocking=False)):
                try:
                    t = time.time()
                    if rb.refresh_if_due(60):
                        log("resident listing: periodic full walk between calls, %d ms" % ((time.time() - t) * 1000))
                    # and the names other writers touched since the last call are patched in now, not inside the
                    # next recall (brain_resident_listing_mac.patch_if_changed). BRS_IDLE_PATCH=0 turns it off.
                    elif hasattr(rb, "patch_if_changed") and os.environ.get("BRS_IDLE_PATCH", "1") != "0":
                        rb.patch_if_changed()
                except Exception as e:
                    log("idle full walk failed: %s: %s" % (type(e).__name__, e))
                finally:
                    lock.release()
            # 6 Nov 2026 (Mac): the tail postings (brain._tail_postings: every compaction tail and long memory tail,
            # packed) were rebuilt inside the first recall after each compaction: 17 of 46 slow calls sampled carried
            # one, ~600 ms each. Here the rebuild runs between calls when the held index is no longer valid, with the
            # same builder and the same validity test the call applies, so a call finds it current.
            # BRS_IDLE_TPOST=0 turns it off.
            g = brain.g
            if (os.environ.get("BRS_IDLE_TPOST", "1") != "0" and os.environ.get("BRAIN_RECALL_CTAIL", "1") != "0"
                    and "_tail_postings" in g and "_tpost_valid" in g and time.time() - last["t"] > 2
                    and lock.acquire(blocking=False)):
                try:
                    import bm25 as _b
                    ix = g["_TPOST"].get("ix")
                    if ix is not None and not g["_tpost_valid"](ix, _b):
                        t = time.time()
                        g["_tail_postings"](_b)
                        log("tail postings rebuilt between calls, %d ms" % ((time.time() - t) * 1000))
                except Exception as e:
                    log("idle tail postings failed: %s: %s" % (type(e).__name__, e))
                finally:
                    lock.release()
    finally:
        try:
            with open(ENDPOINT, encoding="utf-8") as f:
                mine = json.load(f).get("pid") == os.getpid()
            if mine:  # replace only after the read handle is closed (Windows refuses otherwise)
                os.replace(ENDPOINT, ENDPOINT + ".stopped")
        except Exception:
            pass
        time.sleep(1)
        srv.shutdown()
        log("stopped pid %d after %d calls" % (os.getpid(), brain.calls))
        # 5 Nov 2026 (claude-7df69752): index rebuilds came every few minutes on 5 Nov, and after each cap exit the Codex
        # test arm (sandboxed, cannot spawn) and BRAIN_-carrying loops ran cold until another session started a server
        # (none from 04:35 to 04:4x). The exiting server starts its successor; it waits on server.lock until we are gone.
        if last.get("respawn") and os.environ.get("BRS_RESPAWN", "1") != "0":
            try:
                import subprocess
                subprocess.Popen([sys.executable, "-B", os.path.abspath(__file__)], cwd=D,
                                 env=dict({k: v for k, v in os.environ.items() if not k.startswith("BRAIN_")},
                                          BRS_SUCCESSOR="1"),
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 close_fds=True, start_new_session=(os.name == "posix"),
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                log("started a successor after the index change")
            except Exception as e:
                log("successor start failed: %s: %s" % (type(e).__name__, e))
        if orphaned["s"] is not None:
            # the orphaned call's thread is still running; leave without waiting on it, so the server lock and the
            # port are free for the next server at once
            os._exit(0)


if __name__ == "__main__":
    if os.name == "posix":
        try:
            os.nice(max(0, 10 - os.nice(0)))   # niced to 10 (compute doctrine); a successor does not stack it
        except OSError:
            pass
    lockf = None
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        lockf = open(os.path.join(STATE_DIR, "server.lock"), "a+b")
        try:
            import msvcrt

            def _try_lock():
                msvcrt.locking(lockf.fileno(), msvcrt.LK_NBLCK, 1)
        except ImportError:  # macOS / Linux (4 Nov 2026)
            import fcntl

            def _try_lock():
                fcntl.flock(lockf.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        for _ in range(40):  # a server that is stopping holds the lock for up to ~16 s
            try:
                _try_lock()
                break
            except OSError:
                time.sleep(1)
        else:
            sys.exit(0)  # another server is live
    except Exception:
        sys.exit(0)
    main()
