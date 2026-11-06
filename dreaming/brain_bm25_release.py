"""Let a warm recall server live through a BM25 rebuild instead of exiting on it.

Why: each rebuild unlinks the old postings sidecar (~160 MB), and the server exited on the first one so the disk space
came back (5 Nov 2026). Rebuilds come every 15-35 minutes, so 92 of 117 server starts in the 24 h to 6 Nov 09:10 were
these exits, and the first live call of a fresh server took p50 1.33 s: 51% of all live recall time.

The one long-lived holder of a postings file in a warm server is brain._FC_DF (a _FcLazyDf with its own connection),
also reachable from brain_node_documents._IDF_CACHE, which is filled once per process and was kept current only by
those exits. release() closes and drops every _FC_DF entry for another file and clears _IDF_CACHE, then reports any
regular file of 1 MB or more this process still holds open with no name left on disk. The caller exits as before
when that list is not empty, so the disk always comes back.

6 Nov 2026, claude-95df24fa, Mac mini."""
import os
import stat
import sys


def _fd_path(fd):
    """The path the kernel still has for fd (macOS F_GETPATH; Linux /proc), or ''."""
    try:
        import fcntl
        if hasattr(fcntl, "F_GETPATH"):
            return fcntl.fcntl(fd, fcntl.F_GETPATH, bytes(1024)).split(b"\0", 1)[0].decode("utf-8", "replace")
        return os.readlink("/proc/self/fd/%d" % fd)
    except Exception:
        return ""


def held_deleted(min_bytes=1 << 20):
    """[(fd, size, path)] for regular files this process holds open whose link count is 0."""
    out = []
    try:
        fds = os.listdir("/dev/fd")
    except OSError:
        return out
    for f in fds:
        try:
            fd = int(f)
            st = os.fstat(fd)
        except (ValueError, OSError):
            continue
        if stat.S_ISREG(st.st_mode) and st.st_nlink == 0 and st.st_size >= min_bytes:
            out.append((fd, st.st_size, _fd_path(fd)))
    return out


def release(g):
    """Drop the warm server's hold on old BM25 generations. g: brain.py's globals. Returns what it did."""
    did = {"df_closed": 0, "idf_cleared": False}
    # each recall leaves two postings connections in cyclic garbage (the per-call overlay and its BM25 objects), freed
    # only by a full collection: 9 open after 6 warm calls, 1 after gc.collect() (72 ms), 6 Nov 2026. Without this an
    # old generation's file stays held until the collector happens to run its oldest generation.
    import gc
    did["gc_freed"] = gc.collect()
    try:
        import bm25
        sig = bm25._index_signature(bm25.INDEX)
        cur = os.path.abspath(bm25.postings_path(bm25.INDEX, sig)) if sig is not None else None
    except Exception:
        cur = None
    fcdf = g.get("_FC_DF")
    if isinstance(fcdf, dict):
        for k in list(fcdf):
            path = k[0] if isinstance(k, tuple) and k else None
            if cur is not None and path == cur:
                continue
            v = fcdf.pop(k, None)
            con = getattr(v, "_con", None)
            if con is not None:
                try:
                    con.close()
                except Exception:
                    pass
                v._con = None
                did["df_closed"] += 1
    bnd = sys.modules.get("brain_node_documents")
    cache = getattr(bnd, "_IDF_CACHE", None)
    if isinstance(cache, dict):
        cache.clear()
        did["idf_cleared"] = True
    # brain._USAGE_CACHE (base_activation.tsv, rebuilt by the nightly dream) is also read once per process: reset it
    # here so it stays as fresh as it was when every rebuild restarted the server
    if "_USAGE_CACHE" in g:
        g["_USAGE_CACHE"] = None
        did["usage_reset"] = True
    # two speed-only caches are written to disk at process exit; the exits wrote them every rebuild, so do it here
    for mod, fn in (("embedding_artifact", "_vc_flush"), ("spread_recall", "_fdesc_save")):
        f = getattr(sys.modules.get(mod), fn, None)
        if callable(f):
            try:
                f()
            except Exception:
                pass
    did["held_deleted"] = held_deleted()
    if did["held_deleted"]:
        did["dead_closed"], did["dead_owners"] = close_dead_postings()
        did["held_deleted"] = held_deleted()
    return did


def close_dead_postings():
    """Close every sqlite connection in this process whose postings file is gone from disk. A memo keyed by the index
    signature can keep an old generation's object (and its connection) alive long after nothing will read it: an
    in-process A/B on 6 Nov held the file from two generations back after release() and a clear of every store. The
    owners' type names come back so the next look can name the holder. Run only when something is still held (a gc
    walk over a warm server is not free)."""
    import gc
    import sqlite3
    n, owners = 0, []
    for o in gc.get_objects():
        if type(o) is not sqlite3.Connection:
            continue
        try:
            rows = o.execute("PRAGMA database_list").fetchall()
        except Exception:
            continue                      # closed already
        if not any(p and ".postings." in os.path.basename(p) and not os.path.exists(p) for _s, _n, p in rows):
            continue
        try:
            for r in gc.get_referrers(o):
                t = type(r).__name__
                if t not in ("frame", "list") and len(owners) < 12:
                    owners.append(t + (":" + ",".join(str(k)[:40] for k, v in r.items() if v is o)[:80]
                                       if isinstance(r, dict) else ""))
        except Exception:
            pass
        try:
            o.close()
            n += 1
        except Exception:
            pass
    return n, owners
