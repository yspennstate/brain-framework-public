"""brain_resident_listing_mac.py - the memory folder's listing kept resident in the brain recall server, on macOS.

The Mac counterpart of brain_resident_listing.py (the laptop's, on ReadDirectoryChangesW). Every time the recall
server drops its folder memos (a memory written anywhere, or its 120 s timer), brain._bulk_listing re-walks the
46k-entry memory folder with its stats: 86 ms on a quiet machine and 160 ms p50 inside the server (6 Nov 2026). This
module keeps {name: _BulkEntry} and the folder's order, and on each listing:

1. makes the FSEvents count current (mac_folder_watch.barrier: a sentinel write that must come back, about 11 ms);
2. re-reads with lstat only the names that had an event, building each entry the way _bulk_listing does;
3. when a name came or went, takes the order from os.listdir, which returns the same order as getattrlistbulk (both
   read the folder's B-tree; equal on all 46,233 names, and that order is ascending APFS name hash then name bytes,
   0 violations), and checks the names match the entries it holds, lstat-ing any extra and dropping any missing.
An unchanged file keeps its entry object, so brain._sup_rows' per-entry cache can hit (it was built for the laptop's
resident listing and never hit on the Mac).

Falls back to a full _bulk_listing when the watcher cannot vouch for its count (barrier timeout, dropped events, a
root change), when an entry is neither a file nor a folder (then the full walk returns None and the caller uses
os.scandir, as before), and at least every FULL_EVERY seconds. The last bounds one known blind spot: a write into a
file still held open raises no event until it is closed, so its size and mtime would otherwise stay old (none was
held open for writing in the memory folder when checked, 6 Nov). BRS_RESIDENT_LISTING=0 turns this off.
"""
import json, os, stat as _stat, sys, threading, time

FULL_EVERY = float(os.environ.get("BRL_FULL_EVERY", "600"))
D = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = "" if os.environ.get("BRL_LOG", "1") == "0" else os.path.join(D, "brain_recall_server", "resident.jsonl")


# Where a new name sits in the folder's order, by the APFS directory-entry hash (crc32c, no final xor, of the NFD
# case-folded name as UTF-32LE, low 22 bits) then name bytes. Only ASCII names reach it (a non-ASCII change walks).
# BRL_PREDICT_ORDER=0 lists the folder instead, as before (6 Nov 2026).
PREDICT_ORDER = os.environ.get("BRL_PREDICT_ORDER", "1") != "0"
KEEP_ON_FULL = os.environ.get("BRL_KEEP_ON_FULL", "1") != "0"
_CRC_T = []


def _order_key(name):
    import unicodedata
    if not _CRC_T:
        for i in range(256):
            c = i
            for _ in range(8):
                c = (c >> 1) ^ 0x82F63B78 if c & 1 else c >> 1
            _CRC_T.append(c)
    t, crc = _CRC_T, 0xFFFFFFFF
    for x in unicodedata.normalize("NFD", name.casefold()).encode("utf-32-le"):
        crc = t[(crc ^ x) & 0xFF] ^ (crc >> 8)
    return (crc & 0x3FFFFF, name.encode("utf-8", "surrogateescape"))


class ResidentBulk(object):
    def __init__(self, g, folder, bulk, sentinel_dir):
        import mac_folder_watch
        self.g = g
        self.folder = os.fspath(folder)
        self.bulk = bulk                 # the unwrapped brain._bulk_listing
        self.lock = threading.Lock()
        self.d = {}
        self.order = None                # [entry] in the folder's order, or None after a failed full walk
        self.full_t = 0.0
        self.counts = {"full": 0, "patched": 0, "reorders": 0, "same": 0, "fallback": 0}
        self.last_fallback = ""
        self.predict_off = ""
        self.watch = mac_folder_watch.FolderWatch(self.folder, sentinel_dir)
        self._full("start")

    def _predicted(self, found):
        """The new order without os.listdir after names came or went: the entries still held, in the old order, with
        each new name put where the folder keeps it, by (APFS name hash, name bytes) - the order os.listdir returned
        on all 46,316 names on 6 Nov 2026, 269 equal-hash neighbours included. A new name is first checked to be
        stored under that spelling (F_GETPATH on an open handle), which the listdir compare used to catch. None
        (the caller lists the folder) on any doubt."""
        import bisect
        d = self.d
        old = set(e.name for e in self.order)
        added = [n for n in found if n not in old]
        # every name lstat found, not only the new ones: after a case-only rename lstat still answers for the old
        # spelling, which must not stay listed beside the new one (the burst test's case-only wave, 6 Nov)
        for n in found:
            if not self._spelled(n):
                return None
        order = [d[e.name] for e in self.order if e.name in d]
        for n in sorted(added, key=_order_key):
            k = _order_key(n)
            lo, hi = 0, len(order)
            while lo < hi:
                mid = (lo + hi) // 2
                if _order_key(order[mid].name) < k:
                    lo = mid + 1
                else:
                    hi = mid
            order.insert(lo, d[n])
        if len(order) != len(d):
            return None
        return order

    def _spelled(self, name):
        try:
            import fcntl
            fd = os.open(os.path.join(self.folder, name), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                p = fcntl.fcntl(fd, fcntl.F_GETPATH, bytes(1024)).split(b"\0", 1)[0]
            finally:
                os.close(fd)
            return os.path.basename(os.fsdecode(p)) == name
        except Exception:
            return False

    def _full(self, why):
        self.watch.take_changed()        # everything up to here is in the walk below
        prev = self.order
        lst = self.bulk(self.folder)
        # audit of the predicted order: when the walk finds the same names, it must find them in the same order, or
        # prediction is switched off for this process and the reason kept in predict_off
        if (prev is not None and lst is not None and self.counts.get("predicted") and len(prev) == len(lst)):
            a, b = [e.name for e in prev], [e.name for e in lst]
            if a != b and set(a) == set(b):
                i = next(i for i in range(len(a)) if a[i] != b[i])
                self.predict_off = "order differs from the walk at %d: %r vs %r" % (i, a[i], b[i])
            elif a == b:
                self.counts["audited"] = self.counts.get("audited", 0) + 1
        self.full_t = time.monotonic()
        self.counts["full"] += 1
        if lst is None:
            self.d, self.order = {}, None
            return None
        # An entry whose name, kind and stat are unchanged keeps its object on every full walk, as on the patch path,
        # so per-entry caches keyed on the object (_sup_rows' _SUPROWC, 112 ms raw to rebuild, 0.62 s seen under load)
        # still hit after the walk. BRL_KEEP_ON_FULL=0 replaces every object, as before (6 Nov 2026).
        old = self.d
        if old and lst and KEEP_ON_FULL:
            slots = type(lst[0]._st).__slots__ if lst[0]._st is not None else ()
            kept = []
            for e in lst:
                o = old.get(e.name)
                if (o is not None and o._dir == e._dir and o._folder == e._folder
                        and all(getattr(o._st, k, None) == getattr(e._st, k, None) for k in slots)):
                    e = o
                kept.append(e)
            lst = kept
        self.d = {e.name: e for e in lst}
        self.order = lst
        return list(lst)

    def refresh_if_due(self, margin):
        """The periodic full walk, run by the server between calls once it is within `margin` s of due, so a recall
        does not pay it inline: 9 of 91 live listings on 6 Nov 2026 were these, p50 241 ms, and the walk is a Python
        loop that holds the GIL against the call's other threads. An entry whose name, kind and stat are unchanged
        keeps its object, as on the patch path, so per-entry caches still hit. True when it walked."""
        if not self.lock.acquire(blocking=False):
            return False
        try:
            if self.watch.broken or self.order is None or time.monotonic() - self.full_t < FULL_EVERY - margin:
                return False
            self._full("idle")
            self.counts["idle"] = self.counts.get("idle", 0) + 1
            return True
        finally:
            self.lock.release()

    def _entry(self, name):
        """The _BulkEntry _bulk_listing would give for name, None when it is gone, False when it is neither a regular
        file nor a folder (the full walk returns None for the whole listing then)."""
        try:
            st = os.lstat(os.path.join(self.folder, name))
        except FileNotFoundError:
            return None
        m = st.st_mode
        if _stat.S_ISREG(m):
            isdir = False
        elif _stat.S_ISDIR(m):
            isdir = True
        else:
            return False
        g = self.g
        e = g["_BulkEntry"]()
        e.name = name
        e._folder = self.folder
        e._dir = isdir
        s = g["_BulkStat"]()
        # getattrlistbulk with FSOPT_PACK_INVAL_ATTRS gives a folder a zero data length
        s.st_size = 0 if isdir else st.st_size
        mt, ct = st.st_mtime_ns, st.st_ctime_ns
        s.st_mtime_ns, s.st_ctime_ns = mt, ct
        ms, mn = divmod(mt, 1_000_000_000)
        cs, cn = divmod(ct, 1_000_000_000)
        s.st_mtime, s.st_ctime = ms + mn * 1e-9, cs + cn * 1e-9
        s.st_ino = st.st_ino
        e._st = s
        return e

    def patch_if_changed(self):
        """Between calls: when the watcher has seen names since the last listing, patch them in now, so the next
        recall's listing has nothing left but the barrier. Inside a call the patch competed with the call's own
        threads for the GIL, and the FSEvents callback needs the GIL to deliver the sentinel: 27 served folder drops
        on a Mac (6 Nov 2026) paid _memlist p50 220 ms, while the same patch path logged p50 33 ms. The listing a call
        gets is built by the same patch code either way; only the moment the events are taken moves. True when it
        ran. BRS_IDLE_PATCH=0 (in the server) turns it off."""
        if self.watch.broken or self.order is None or not self.watch._changed:
            return False
        if not self.lock.acquire(blocking=False):
            return False
        self.lock.release()          # _listing takes it; a call that comes in between just waits on it
        self.listing("idle_patch")
        return True

    def listing(self, tag=None):
        t0 = time.perf_counter()
        before = dict(self.counts)
        self._bar_ms = None
        try:
            if tag:
                self.counts[tag] = self.counts.get(tag, 0) + 1
            return self._listing()
        finally:
            if LOG_PATH:
                try:
                    kind = [k for k in self.counts if self.counts[k] != before.get(k)]
                    row = {"t": round(time.time(), 2), "ms": round((time.perf_counter() - t0) * 1000, 1),
                           "barrier_ms": self._bar_ms, "kind": kind, "n": len(self.d)}
                    with open(LOG_PATH, "a") as fh:
                        fh.write(json.dumps(row) + "\n")
                except Exception:
                    pass

    def _barrier(self):
        t = time.perf_counter()
        ok = self.watch.barrier()
        self._bar_ms = round((time.perf_counter() - t) * 1000, 1)
        return ok

    def _listing(self):
        with self.lock:
            if (self.watch.broken or self.order is None or time.monotonic() - self.full_t > FULL_EVERY
                    or not self._barrier()):
                if self.watch.broken:
                    self.last_fallback = self.watch.broken
                    self.counts["fallback"] += 1
                    return self.bulk(self.folder)
                return self._full("timer")
            changed = self.watch.take_changed()
            if not changed:
                self.counts["same"] += 1
                return list(self.order)
            if not all(b.isascii() for b in changed):
                # the volume folds case and Unicode form: a non-ASCII name may not be spelled as the folder stores it
                return self._full("non-ascii name")
            names_moved = False
            found = []
            for b in changed:
                name = os.fsdecode(b)
                e = self._entry(name)
                if e is False:
                    return self._full("special entry")
                if e is None:
                    if self.d.pop(name, None) is not None:
                        names_moved = True
                else:
                    if name not in self.d:
                        names_moved = True
                    self.d[name] = e
                    found.append(name)
            self.counts["patched"] += len(changed)
            if names_moved and PREDICT_ORDER and not self.predict_off:
                got = self._predicted(found)
                if got is not None:
                    self.order = got
                    self.counts["predicted"] = self.counts.get("predicted", 0) + 1
                    return list(got)
            if names_moved:
                names = os.listdir(self.folder)
                have = set(names)
                if any(n not in have for n in found):
                    # lstat found a name the folder does not list under that spelling (a case alias, or it went
                    # after the lstat): the stored entry may not have been re-read, so walk the folder
                    return self._full("alias")
                if len(names) != len(self.d) or any(n not in self.d for n in names):
                    # changed after the barrier: settle each difference the way a walk taken now would
                    for n in [n for n in self.d if n not in have]:
                        del self.d[n]
                    for n in names:
                        if n not in self.d:
                            e = self._entry(n)
                            if e is False:
                                return self._full("special entry")
                            if e is not None:
                                self.d[n] = e
                    names = [n for n in names if n in self.d]
                self.order = [self.d[n] for n in names]
                self.counts["reorders"] += 1
            else:
                d = self.d
                self.order = [d[e.name] for e in self.order]
            return list(self.order)

    def close(self):
        self.watch.close()


def install(srv):
    """Serve brain._bulk_listing(MEM) from a resident listing inside a brain_recall_server.Brain; any other folder
    goes to the original. Returns the ResidentBulk (its counts are the receipt)."""
    if sys.platform != "darwin":
        raise OSError("macOS only")
    g = srv.g
    real = g["_bulk_listing"]
    real = getattr(real, "__wrapped__", real)
    mem = g["MEM"]
    rb = ResidentBulk(g, mem, real, os.path.join(D, "brain_recall_server", "fsw"))
    mem_norm = os.path.normcase(os.path.abspath(mem))

    def bulk_listing(folder):
        try:
            if os.path.normcase(os.path.abspath(os.fspath(folder))) == mem_norm:
                return rb.listing()
        except Exception:
            pass
        return real(folder)
    bulk_listing.__wrapped__ = real
    g["_bulk_listing"] = bulk_listing
    return rb
