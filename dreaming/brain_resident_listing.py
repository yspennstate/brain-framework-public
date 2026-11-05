"""Lever 3 (recall-10x lead1): a resident listing of the memory folder kept current by ReadDirectoryChangesW.

brain._fast_listing(MEM) re-reads ~50k directory entries (~1.5 s) every time the recall server drops its call cache.
This keeps {name: _FastEntry} resident, patches it from change events (ctypes only; py314 has no pywin32), and
serves the same list in NTFS order (sorted by str.upper, 0 mismatches over 49,766 names - lever3_order_probe).
Changed names are re-read with FindFirstFileExW on the exact name, through the same field unpacking as
_fast_listing, so an entry is byte-for-byte what a full listing would return. Overflow or any error marks the
listing dirty and the next call does one full listing. Development copy: not wired into the server yet.

    python resident_listing.py parity [rounds] [gap_s]    # resident vs _fast_listing while agents write
"""
import ctypes, importlib.util, json, os, sys, threading, time
from ctypes import wintypes

D = os.path.expanduser("~/.claude/dreaming")
B = None   # brain.py's names: a module loaded by _load_brain(), or bind(globals_dict) inside the recall server


def _load_brain():
    global B
    if B is None:
        spec = importlib.util.spec_from_file_location("brainmod_rl", os.path.join(D, "brain.py"))
        B = importlib.util.module_from_spec(spec); spec.loader.exec_module(B)
    return B


class _Bound(object):
    """brain.py's live globals as attributes; _fast_listing is the unwrapped original (the server memoizes and
    later replaces the global, and the resident listing must never call its own replacement)."""
    def __init__(self, g, fast_listing):
        self.__dict__["_g"], self.__dict__["_fast_listing"] = g, fast_listing

    def __getattr__(self, name):
        return self._g[name]


def bind(g, fast_listing):
    global B
    B = _Bound(g, fast_listing)
    return B

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
CreateFileW = k32.CreateFileW
CreateFileW.restype = wintypes.HANDLE
CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                        wintypes.DWORD, wintypes.HANDLE]
RDCW = k32.ReadDirectoryChangesW
RDCW.restype = wintypes.BOOL
RDCW.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, wintypes.BOOL, wintypes.DWORD,
                 ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p, ctypes.c_void_p]
CancelIoEx = k32.CancelIoEx
CancelIoEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
CloseHandle = k32.CloseHandle
CloseHandle.argtypes = [wintypes.HANDLE]
INVALID = wintypes.HANDLE(-1).value
FILTER = 0x1 | 0x2 | 0x4 | 0x8 | 0x10 | 0x40   # file name, dir name, attributes, size, last write, creation


def _entry_for(folder, name):
    """The _FastEntry a full listing would give for folder/name, or None if it is gone."""
    if not B._FL_API:
        B._fast_listing(folder)   # builds the shared ctypes API
    _WFD, first, nxt, close, invalid, unpack = B._FL_API[0]
    d = _WFD()
    h = first(os.path.join(folder, name), 1, ctypes.byref(d), 0, None, 0)
    if h is None or h == invalid:
        return None
    try:
        attr, c, a, m, shi, slo = unpack(ctypes.string_at(ctypes.addressof(d), 36))
        if d.name != name:   # case-only rename or short-name match: let a full listing settle it
            raise LookupError(d.name)
        E = B._FL_EPOCH
        st = B._FastStat()
        st.st_size = (shi << 32) | slo
        st.st_mtime_ns = (m - E) * 100
        st.st_ctime_ns = (c - E) * 100
        st.st_birthtime_ns = st.st_ctime_ns
        st.st_atime_ns = (a - E) * 100
        st.st_file_attributes = attr
        isdir = bool(attr & 0x10)
        st.st_mode = (0o40111 if isdir else 0o100000) | (0o444 if attr & 0x1 else 0o666)
        e = B._FastEntry()
        e.name, e.path, e._st, e._dir = name, os.path.join(folder, name), st, isdir
        return e
    finally:
        close(h)


class ResidentListing(object):
    def __init__(self, folder):
        self.folder = os.fspath(folder)
        self.lock = threading.Lock()
        self.d, self.sorted_cache, self.dirty = {}, None, True
        self.events = self.relists = self.patched = 0
        self.h = CreateFileW(self.folder, 0x1, 0x7, None, 3, 0x02000000, None)   # LIST_DIRECTORY, share rwd, BACKUP_SEMANTICS
        if self.h == INVALID:
            raise OSError(ctypes.get_last_error(), "CreateFileW")
        self.stop = False
        self._relist()
        self.t = threading.Thread(target=self._watch, daemon=True)
        self.t.start()

    def _relist(self):
        lst = B._fast_listing(self.folder)
        if lst is None:
            lst = list(os.scandir(self.folder))
        with self.lock:
            self.d = {e.name: e for e in lst}
            self.sorted_cache = lst   # already in the folder's order
            self.dirty = False
            self.relists += 1

    def _watch(self):
        buf = ctypes.create_string_buffer(int(os.environ.get("RL_BUF_KB", "256")) * 1024)   # small only in the overflow test
        got = wintypes.DWORD()
        while not self.stop:
            ok = RDCW(self.h, buf, len(buf), False, FILTER, ctypes.byref(got), None, None)
            if self.stop:
                return
            if not ok or got.value == 0:   # error or overflow: changes were lost
                with self.lock:
                    self.dirty = True
                if not ok:
                    time.sleep(0.5)
                continue
            names, off, raw = set(), 0, buf.raw[:got.value]
            while True:
                nxt_off, action, ln = int.from_bytes(raw[off:off + 4], "little"), int.from_bytes(raw[off + 4:off + 8], "little"), int.from_bytes(raw[off + 8:off + 12], "little")
                names.add(raw[off + 12:off + 12 + ln].decode("utf-16-le"))
                if not nxt_off:
                    break
                off += nxt_off
            self.events += len(names)
            fresh = {}
            for n in names:
                try:
                    fresh[n] = _entry_for(self.folder, n)
                except Exception:
                    with self.lock:
                        self.dirty = True
                    break
            with self.lock:
                for n, e in fresh.items():
                    if e is None:
                        self.d.pop(n, None)
                    else:
                        self.d[n] = e
                self.sorted_cache = None
                self.patched += len(fresh)

    def listing(self):
        if self.dirty:
            self._relist()
        with self.lock:
            if self.sorted_cache is None:
                self.sorted_cache = [self.d[n] for n in sorted(self.d, key=str.upper)]
            return list(self.sorted_cache)

    def close(self):
        self.stop = True
        CancelIoEx(self.h, None)
        CloseHandle(self.h)


def install(srv):
    """Serve brain._fast_listing(MEM) from a resident listing inside a brain_recall_server.Brain; any other
    folder still goes through the server's memo. Returns the ResidentListing (its counters are the receipt)."""
    g = srv.g
    memo = g["_fast_listing"]
    bind(g, getattr(memo, "__wrapped__", memo))
    rl = ResidentListing(g["MEM"])
    mem_norm = os.path.normcase(os.path.abspath(g["MEM"]))

    def fast_listing(folder):
        try:
            if os.path.normcase(os.path.abspath(os.fspath(folder))) == mem_norm:
                return rl.listing()
        except Exception:
            pass
        return memo(folder)
    fast_listing.__wrapped__ = memo
    g["_fast_listing"] = fast_listing
    return rl


def burst(n=2000, folder=None):
    """Overflow/dirty path on a scratch folder (never the memory folder): n files written at once, then edited,
    then half deleted; after each wave the resident listing must equal a full listing."""
    _load_brain()
    import tempfile
    folder = folder or tempfile.mkdtemp(prefix="rl_burst_", dir=os.environ.get("RL_SCRATCH") or None)
    r = ResidentListing(folder)
    waves = []

    def check(label):
        time.sleep(1.0)
        a, b = [_key(e) for e in r.listing()], [_key(e) for e in B._fast_listing(folder)]
        waves.append({"wave": label, "n": len(b), "equal": a == b, "events": r.events, "relists": r.relists})
        print(json.dumps(waves[-1]), flush=True)
    for i in range(n):
        open(os.path.join(folder, "f%05d.md" % i), "w").write("x" * (i % 97))
    check("create")
    for i in range(0, n, 3):
        open(os.path.join(folder, "f%05d.md" % i), "a").write("more")
    check("append")
    for i in range(0, n, 2):
        os.remove(os.path.join(folder, "f%05d.md" % i))
    check("delete")
    os.rename(os.path.join(folder, "f00001.md"), os.path.join(folder, "F00001_renamed.md"))
    check("rename")
    r.close()
    for x in os.listdir(folder):
        os.remove(os.path.join(folder, x))
    os.rmdir(folder)
    summ = {"t": time.strftime("%F %T"), "test": "burst", "n": n, "all_equal": all(w["equal"] for w in waves),
            "waves": waves}
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "resident_listing_parity.jsonl"), "a",
              encoding="utf-8") as f:
        f.write(json.dumps(summ) + "\n")
    print(json.dumps({k: v for k, v in summ.items() if k != "waves"}))


def _key(e):
    s = e.stat()
    return (e.name, s.st_size, s.st_mtime_ns, s.st_file_attributes)


def parity(rounds=20, gap=3.0):
    _load_brain()
    t0 = time.perf_counter(); r = ResidentListing(B.MEM); t_start = time.perf_counter() - t0
    rows = []
    for i in range(rounds):
        time.sleep(gap)
        t = time.perf_counter(); a = r.listing(); ta = time.perf_counter() - t
        t = time.perf_counter(); b = B._fast_listing(B.MEM); tb = time.perf_counter() - t
        ka, kb = [_key(e) for e in a], [_key(e) for e in b]
        mism = sum(1 for x, y in zip(ka, kb) if x != y) + abs(len(ka) - len(kb))
        retry = None
        if mism:   # a write between the two reads: read both again, resident second so it has seen the event
            time.sleep(0.3)
            kb2 = [_key(e) for e in B._fast_listing(B.MEM)]; time.sleep(0.2)
            ka2 = [_key(e) for e in r.listing()]
            retry = sum(1 for x, y in zip(ka2, kb2) if x != y) + abs(len(ka2) - len(kb2))
        rows.append({"round": i + 1, "n": len(kb), "resident_s": round(ta, 4), "full_s": round(tb, 3),
                     "mismatch": mism, "mismatch_after_reread": retry, "events": r.events, "relists": r.relists})
        print(json.dumps(rows[-1]), flush=True)
    r.close()
    clean = sum(1 for x in rows if not x["mismatch"] or x["mismatch_after_reread"] == 0)
    summ = {"t": time.strftime("%F %T"), "rounds": rounds, "gap_s": gap, "start_s": round(t_start, 3),
            "clean_rounds": clean, "median_resident_s": sorted(x["resident_s"] for x in rows)[rounds // 2],
            "median_full_s": sorted(x["full_s"] for x in rows)[rounds // 2], "events": r.events,
            "patched": r.patched, "relists": r.relists, "rows": rows}
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "resident_listing_parity.jsonl"), "a",
              encoding="utf-8") as f:
        f.write(json.dumps(summ) + "\n")
    print(json.dumps({k: v for k, v in summ.items() if k != "rows"}))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "parity":
        parity(int(sys.argv[2]) if len(sys.argv) > 2 else 20, float(sys.argv[3]) if len(sys.argv) > 3 else 3.0)
    elif len(sys.argv) > 1 and sys.argv[1] == "burst":
        burst(int(sys.argv[2]) if len(sys.argv) > 2 else 2000)
