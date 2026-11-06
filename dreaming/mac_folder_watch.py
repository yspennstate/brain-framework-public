"""mac_folder_watch.py - an exact "has anything under this folder changed?" signal on macOS, from FSEvents.

The brain recall server drops its folder memos when the memory folder's stat signature changes, and also on a 120 s
timer, because a file edited in place changes no folder stat. On 6 Nov 2026 about 67 of 165 folder drops in a day had
no change behind them. This watcher counts FSEvents file events under a folder (creates, edits in place, renames,
deletes, attribute changes, at any depth), so a caller can drop exactly when something changed.

barrier() makes the count current: it writes a sentinel file in a private folder watched by the same stream and waits
until that event comes back. Events are delivered in event-id order, so once the sentinel's event is in, every event
for a change made before barrier() was called is in too. If the sentinel does not come back within BARRIER_WAIT, or
the stream reports dropped events or a root change, the watcher marks itself broken and the caller must fall back to
its old rule. Only ctypes and the system frameworks are used. Read-only on the watched folder.
"""
import ctypes, os, sys, threading, time

BARRIER_WAIT = float(os.environ.get("MFW_BARRIER_WAIT", "0.5"))
# kFSEventStreamCreateFlag: NoDefer 0x2, WatchRoot 0x4, FileEvents 0x10
_CREATE_FLAGS = 0x2 | 0x4 | 0x10
# kFSEventStreamEventFlag: MustScanSubDirs 0x1, UserDropped 0x2, KernelDropped 0x4, EventIdsWrapped 0x8,
# RootChanged 0x20, Mount 0x40, Unmount 0x80
_BAD_FLAGS = 0x1 | 0x2 | 0x4 | 0x8 | 0x20 | 0x40 | 0x80
_SINCE_NOW = 0xFFFFFFFFFFFFFFFF
_UTF8 = 0x08000100

_CB = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_char_p),
                       ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint64))


class _Ctx(ctypes.Structure):
    _fields_ = [("version", ctypes.c_long), ("info", ctypes.c_void_p), ("retain", ctypes.c_void_p),
                ("release", ctypes.c_void_p), ("desc", ctypes.c_void_p)]


def _libs():
    cs = ctypes.CDLL("/System/Library/Frameworks/CoreServices.framework/CoreServices")
    cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    sysl = ctypes.CDLL("/usr/lib/libSystem.dylib")
    cf.CFStringCreateWithCString.restype = ctypes.c_void_p
    cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
    cf.CFArrayCreate.restype = ctypes.c_void_p
    cf.CFArrayCreate.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.c_long, ctypes.c_void_p]
    cf.CFRelease.argtypes = [ctypes.c_void_p]
    cs.FSEventStreamCreate.restype = ctypes.c_void_p
    cs.FSEventStreamCreate.argtypes = [ctypes.c_void_p, _CB, ctypes.POINTER(_Ctx), ctypes.c_void_p, ctypes.c_uint64,
                                       ctypes.c_double, ctypes.c_uint32]
    cs.FSEventStreamSetDispatchQueue.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    cs.FSEventStreamStart.restype = ctypes.c_ubyte
    cs.FSEventStreamStart.argtypes = [ctypes.c_void_p]
    for n in ("FSEventStreamStop", "FSEventStreamInvalidate", "FSEventStreamRelease", "FSEventStreamFlushSync"):
        getattr(cs, n).argtypes = [ctypes.c_void_p]
    sysl.dispatch_queue_create.restype = ctypes.c_void_p
    sysl.dispatch_queue_create.argtypes = [ctypes.c_char_p, ctypes.c_void_p]
    return cs, cf, sysl


class FolderWatch(object):
    """gen() is a number that changes whenever anything under `folder` changed, and is current once barrier()
    returned True. broken is set (and stays set) when the watcher can no longer vouch for that."""

    def __init__(self, folder, sentinel_dir):
        if sys.platform != "darwin":
            raise OSError("FSEvents is macOS only")
        self.folder = os.path.realpath(folder)
        self.sentinel_dir = os.path.realpath(sentinel_dir)
        if self.sentinel_dir == self.folder or self.sentinel_dir.startswith(self.folder + os.sep):
            raise ValueError("the sentinel folder must lie outside the watched folder")
        os.makedirs(self.sentinel_dir, exist_ok=True)
        self._sent_pre = os.path.join(self.sentinel_dir, "barrier.%d." % os.getpid()).encode()
        self._want = None
        self._got = False
        self._root_b = (self.folder + os.sep).encode()
        self._root_exact = self.folder.encode()
        self._n = 0
        self._changed = set()
        self._sentinel_writes = 0
        self.broken = None
        self._cv = threading.Condition()
        self.cs, self.cf, self.sysl = _libs()
        self._cb = _CB(self._on_events)
        self._ctx = _Ctx(0, None, None, None, None)
        cf = self.cf
        strs = [cf.CFStringCreateWithCString(None, p.encode(), _UTF8) for p in (self.folder, self.sentinel_dir)]
        arr = (ctypes.c_void_p * 2)(*strs)
        paths = cf.CFArrayCreate(None, arr, 2, None)
        self.stream = self.cs.FSEventStreamCreate(None, self._cb, ctypes.byref(self._ctx), paths, _SINCE_NOW, 0.0,
                                                  _CREATE_FLAGS)
        if not self.stream:
            raise OSError("FSEventStreamCreate failed")
        self.queue = self.sysl.dispatch_queue_create(b"brain.folder_watch", None)
        self.cs.FSEventStreamSetDispatchQueue(self.stream, self.queue)
        if not self.cs.FSEventStreamStart(self.stream):
            raise OSError("FSEventStreamStart failed")
        # the stream starts "since now": prove it is live before vouching for anything
        if not self.barrier():
            raise OSError("first barrier did not return")

    def _on_events(self, stream, info, n, paths, flags, ids):
        try:
            hit = 0
            got = False
            bad = None
            names = []
            cut = len(self._root_b)
            for i in range(n):
                p = paths[i] or b""
                f = flags[i]
                if f & _BAD_FLAGS:
                    bad = "event flags 0x%x on %s" % (f, p.decode("utf-8", "replace"))
                if p.startswith(self._sent_pre):
                    if p == self._want:
                        got = True
                elif p == self._root_exact:
                    hit += 1
                elif p.startswith(self._root_b):
                    hit += 1
                    names.append(p[cut:].split(b"/", 1)[0])
            with self._cv:
                self._n += hit
                self._changed.update(names)
                if got:
                    self._got = True
                if bad and not self.broken:
                    self.broken = bad
                self._cv.notify_all()
        except Exception as e:     # never raise into the dispatch queue
            self.broken = "callback error: %s: %s" % (type(e).__name__, e)

    def gen(self):
        return self._n

    def take_changed(self):
        """The names (bytes) directly under the folder that had an event since the last take, cleared."""
        with self._cv:
            out, self._changed = self._changed, set()
        return out

    def barrier(self):
        """True once every change made under the folder before this call has been counted; False (and broken set)
        when that cannot be shown."""
        if self.broken:
            return False
        self._sentinel_writes += 1
        path = self._sent_pre + str(self._sentinel_writes).encode()
        with self._cv:
            self._want = path
            self._got = False
        try:
            with open(path, "w") as fh:
                fh.write("x")
        except OSError as e:
            self.broken = "sentinel write: %s" % e
            return False
        end = time.monotonic() + BARRIER_WAIT
        try:
            with self._cv:
                while not self._got and not self.broken:
                    left = end - time.monotonic()
                    if left <= 0:
                        self.broken = "barrier timeout after %.2f s" % BARRIER_WAIT
                        return False
                    self._cv.wait(left)
            return not self.broken
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    def close(self):
        try:
            self.cs.FSEventStreamStop(self.stream)
            self.cs.FSEventStreamInvalidate(self.stream)
            self.cs.FSEventStreamRelease(self.stream)
        except Exception:
            pass
