"""quarantine_rows.py - a run never destroys an earlier run's rows (27 Sep, claude-825b1305).

The evaluators write their per-item rows (bank ids and ranks) under fixed names in the recall-keys quarantine and the
brain-graph study tree, and each rerun rewrote the file: on 27 Sep the 05:11 canonw/stem run replaced the rows behind
both published tokenizer results, which no longer exist. Those trees are brain data, never deleted (owner law, 26 Sep),
and an overwrite is a deletion. So a writer passes its fixed path through keep_old() first: a file already there is
copied beside itself under its own modification time, and only then written over. The fixed name stays the newest run,
so readers of it keep working. If the copy fails, the run stops before it writes.

    from quarantine_rows import keep_old
    with open(keep_old(path), "w", encoding="utf-8") as fh: ...
"""
import os
import shutil
import time


def _same_bytes(a, b, chunk=1 << 20):
    # not filecmp: its cache is keyed on (size, mtime), and a rewritten file with the same size in the same second
    # reads as already kept - the one case this module exists for (caught by control 4 on 27 Sep)
    if os.path.getsize(a) != os.path.getsize(b):
        return False
    with open(a, "rb") as fa, open(b, "rb") as fb:
        while True:
            x, y = fa.read(chunk), fb.read(chunk)
            if x != y:
                return False
            if not x:
                return True


def keep_old(path):
    """Copy an existing `path` to `<stem>_<its mtime><ext>` (or `..._2`, `..._3` when that name holds other bytes) and
    return `path`. A missing file is left alone. Raises if the copy cannot be made."""
    try:
        st = os.stat(path)
    except OSError:
        return path
    stem, ext = os.path.splitext(path)
    base = "%s_%s" % (stem, time.strftime("%Y%m%d_%H%M%S", time.localtime(st.st_mtime)))
    n = 1
    while True:
        dst = "%s%s" % (base if n == 1 else "%s_%d" % (base, n), ext)
        if not os.path.exists(dst):
            shutil.copy2(path, dst)
            if not _same_bytes(path, dst):
                raise OSError("copy of %s did not match; not overwriting it" % path)
            return path
        if _same_bytes(path, dst):
            return path                  # already kept
        n += 1
