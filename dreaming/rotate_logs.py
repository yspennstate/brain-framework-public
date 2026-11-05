"""Rotate the hook's append-only logs without deleting a row (2026-09-02; made safe to apply 2026-09-03).

instinct_fires.tsv (58 MB), instinct_suppressed.tsv and graph_fire/gf_shadow_fires.tsv (142 MB) grow by
every hook call. Every reader (the nightly judge, the heed audit, graph_cases, replay_text_form,
fire_telemetry) looks back a few days at most, so rows older than KEEP_DAYS are moved - never dropped -
into monthly archive files beside the log (instinct_fires.2026-08.tsv, ...). The live file keeps its newest
KEEP_DAYS of rows; nothing is ever deleted (the simulation-data law and the data law both say preserve,
then rotate). A row whose timestamp cannot be read stays in the live file.

THE RACE, AND HOW IT IS CLOSED. Every session's hook appends to these files at any moment, so a naive
read-then-replace loses whatever was appended in between. The writers already serialise on a per-file
cross-process lock (append_journal.exclusive_append_lock: a named kernel mutex on Windows, flock on POSIX;
a writer that cannot get it within 0.25 s drops its telemetry row rather than delay the hook). So:

  1. plan OUTSIDE the lock: read the whole file once, split rows into kept / moved-by-month, remember
     the byte offset read to;
  2. append the moved rows to their monthly archives - still outside the lock (they came from the
     pre-read; nothing appended later is older than the cut);
  3. INSIDE the lock, briefly: read only the bytes appended since the pre-read (all newer than the cut,
     so all kept), write kept + delta to a temp file, fsync, os.replace. The hold is the time to write
     KEEP_DAYS of rows, a fraction of a second, so a writer that arrives mid-rotation waits and lands;
  4. verify: rows before (pre-read + delta) == rows after (live + newly archived), and the last row of
     the live file is the newest row seen. Any mismatch restores the pre-rotation copy, which is kept
     beside the log as <log>.pre_rotate_<stamp> until the next rotation verifies.

fire_telemetry reads the tail of the live file and takes its newest timestamp as now; the rewrite keeps
the newest rows in place, so it is unaffected.

    python rotate_logs.py            dry run: what would move where
    python rotate_logs.py --apply    do it (under the writers' lock)
    python rotate_logs.py --selftest exercise on a temporary copy, including a writer appending
                                     through the real lock DURING the rotation (positive and negative
                                     controls)
"""
import contextlib
import io
import os
import shutil
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    from append_journal import append_text, exclusive_append_lock
except Exception:  # pragma: no cover - the rotation refuses to run without the writers' lock
    append_text = None
    exclusive_append_lock = None

# The longest lookback among the live readers is build_reflex_ranking's default of 14 days (the judge
# reads a day, the heed audit four); 21 keeps every reader a week clear of the archive boundary.
KEEP_DAYS = 21
LOGS = [os.path.join(HERE, "instinct_fires.tsv"), os.path.join(HERE, "instinct_suppressed.tsv"),
        os.path.join(HERE, "graph_fire", "gf_shadow_fires.tsv")]
LOCK_TIMEOUT_S = 10.0


def _split_rows(text):
    """Split log text into rows that each end with a newline; a trailing partial row is kept whole."""
    rows = text.split("\n")
    out = [r + "\n" for r in rows[:-1]]
    if rows[-1]:
        out.append(rows[-1] + "\n")
    return out


def plan(path, keep_days=KEEP_DAYS, now=None):
    """-> (kept_rows, {archive_path: [rows]}, unreadable, bytes_read) without writing anything."""
    now = time.time() if now is None else now
    cut = now - keep_days * 86400
    kept, moved, unreadable = [], {}, 0
    if not os.path.exists(path):
        return kept, moved, unreadable, 0
    base, ext = os.path.splitext(path)
    with io.open(path, "rb") as f:
        raw = f.read()
    text = raw.decode("utf-8", errors="replace")
    for ln in _split_rows(text):
        head = ln.split("\t", 1)[0]
        try:
            ts = float(head)
        except ValueError:
            kept.append(ln)
            unreadable += 1
            continue
        if ts < cut:
            month = time.strftime("%Y-%m", time.localtime(ts))
            moved.setdefault("%s.%s%s" % (base, month, ext), []).append(ln)
        else:
            kept.append(ln)
    return kept, moved, unreadable, len(raw)


def _count_rows(path):
    with io.open(path, "rb") as f:
        return len(_split_rows(f.read().decode("utf-8", errors="replace")))


def apply(path, keep_days=KEEP_DAYS, now=None, stamp=None):
    """Rotate one log. Returns (moved_rows, unreadable). Raises on a verification failure after restoring."""
    if exclusive_append_lock is None:
        raise RuntimeError("append_journal is unavailable; refusing to rotate without the writers' lock")
    kept, moved, unreadable, offset = plan(path, keep_days, now)
    if not moved:
        return 0, unreadable
    stamp = stamp or time.strftime("%Y%m%dT%H%M%S")
    backup = "%s.pre_rotate_%s" % (path, stamp)
    shutil.copyfile(path, backup)          # the restore point; kept until the next rotation verifies
    archived_before = {arch: (_count_rows(arch) if os.path.exists(arch) else 0) for arch in moved}
    n_moved = 0
    for arch, rows in sorted(moved.items()):
        with io.open(arch, "ab") as f:
            f.write("".join(rows).encode("utf-8"))
            f.flush()
            os.fsync(f.fileno())
        n_moved += len(rows)
    tmp = path + ".rotate.%d.tmp" % os.getpid()
    with exclusive_append_lock(path, timeout=LOCK_TIMEOUT_S):
        with io.open(path, "rb") as f:
            f.seek(offset)
            delta_rows = _split_rows(f.read().decode("utf-8", errors="replace"))
        with io.open(tmp, "wb") as f:
            f.write("".join(kept).encode("utf-8"))
            f.write("".join(delta_rows).encode("utf-8"))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        # verify while the writers still wait: the count is exact only inside the lock (a row appended
        # the instant after the replace is legitimate and would read as "one too many" outside it)
        with io.open(path, "rb") as f:
            live_text = f.read().decode("utf-8", errors="replace")
    live_list = _split_rows(live_text)
    live_rows = len(live_list)
    last_live = live_list[-1] if live_list else None
    before = len(kept) + n_moved + len(delta_rows)
    archived_after = sum(_count_rows(arch) for arch in moved)
    archived_delta = archived_after - sum(archived_before.values())
    newest = (delta_rows or kept)[-1] if (delta_rows or kept) else None
    ok = (live_rows + archived_delta == before) and (newest is None or last_live == newest)
    if not ok:
        shutil.copyfile(backup, path)
        raise RuntimeError("rotation verification failed for %s: before %d, live %d, archived %d; restored from %s"
                           % (path, before, live_rows, archived_delta, backup))
    # The rotation verified, so the restore points of EARLIER rotations have served their purpose: keep only
    # the one just written. Without this a nightly run leaves a full copy of every log behind every night
    # (the 09-03 hand run left 201 MB of copies beside 66 MB of live logs) on a drive that is already near
    # full. A failed verification never reaches here, so on failure every copy stays.
    _drop_older_restore_points(path, keep=backup)
    return n_moved, unreadable


def _drop_older_restore_points(path, keep):
    """Remove <path>.pre_rotate_* copies other than `keep`. Returns the list removed. Best effort: a copy that
    cannot be removed (open in another process) is left for the next verified rotation."""
    removed = []
    d, base = os.path.split(path)
    prefix = base + ".pre_rotate_"
    for name in os.listdir(d or "."):
        if name.startswith(prefix):
            full = os.path.join(d, name)
            if os.path.abspath(full) == os.path.abspath(keep):
                continue
            try:
                os.remove(full)
                removed.append(full)
            except OSError:
                pass
    return removed


def _selftest():
    d = tempfile.mkdtemp()
    try:
        p = os.path.join(d, "instinct_fires.tsv")
        # an integer clock: the rows are written with %.0f, and a fractional `now` made the row that sits
        # exactly on the cut land on either side by the rounding of the second (the 09-02 self-test was
        # flaky for that reason). Row 14 sits ON the cut and is kept: `ts < cut` moves strictly older rows.
        now = float(int(time.time()))
        rows = ["%.0f\tPreToolUse\tact\tr%d\tsnip\tsess\tregex\n" % (now - i * 86400, i) for i in range(30)]
        rows.append("not-a-timestamp\tjunk row stays\n")
        io.open(p, "w", encoding="utf-8", newline="").write("".join(rows))
        stale = p + ".pre_rotate_20000101T000000"                                # an earlier rotation's restore point
        io.open(stale, "w", encoding="utf-8", newline="").write("old copy\n")
        kept, moved, unreadable, _ = plan(p, keep_days=14, now=now)
        assert len(kept) == 16 and unreadable == 1, (len(kept), unreadable)      # days 0..14 + the junk row
        assert sum(len(v) for v in moved.values()) == 15, moved                  # days 15..29
        n, _ = apply(p, keep_days=14, now=now, stamp="selftest")
        assert n == 15
        assert not os.path.exists(stale), "the earlier restore point survived a verified rotation"
        live = io.open(p, encoding="utf-8").read().splitlines()
        archived = sum(len(io.open(a, encoding="utf-8").read().splitlines()) for a in moved)
        assert len(live) == 16 and archived == 15 and len(live) + archived == 31, (len(live), archived)
        n2, _ = apply(p, keep_days=14, now=now, stamp="selftest2")                # idempotent
        assert n2 == 0
        assert os.path.exists(p + ".pre_rotate_selftest")
        print("selftest OK: 31 rows in -> 16 live + 15 archived, junk row kept, second run moves nothing")

        # concurrency control: a writer appends through the real lock while the rotation runs
        p2 = os.path.join(d, "gf_shadow_fires.tsv")
        big = ["%.0f\tPreToolUse\tact\tr%d\t%s\tsess\tregex\n" % (now - (i % 40) * 86400, i, "x" * 200) for i in range(60000)]
        io.open(p2, "w", encoding="utf-8", newline="").write("".join(big))
        stop = threading.Event()
        written = []

        def writer():
            k = 0
            while not stop.is_set():
                row = "%.0f\tPreToolUse\tact\tconcurrent%d\tsnip\tsess\tregex\n" % (now, k)
                try:
                    append_text(p2, row, timeout=2.0)
                    written.append(row)
                except Exception:
                    pass
                k += 1
                time.sleep(0.001)

        t = threading.Thread(target=writer, daemon=True)
        t.start()
        time.sleep(0.05)
        n3, _ = apply(p2, keep_days=14, now=now, stamp="concurrent")
        stop.set()
        t.join(timeout=5)
        live2 = io.open(p2, encoding="utf-8").read().splitlines()
        arch2 = sum(len(io.open(a, encoding="utf-8").read().splitlines())
                    for a in os.listdir(d) if a.startswith("gf_shadow_fires.2") for a in [os.path.join(d, a)])
        total = len(live2) + arch2
        expected = 60000 + len(written)
        assert total == expected, ("rows lost or duplicated", total, expected, len(written))
        assert all(r.rstrip("\n") in set(live2) for r in written[-5:]), "the writer's newest rows are not in the live file"
        assert not any(len(r.split("\t")) < 7 for r in live2), "a truncated fragment reached the live file"
        print("concurrency OK: 60000 + %d appended during rotation -> %d live + %d archived, none lost, no fragments"
              % (len(written), len(live2), arch2))
        # negative control: a verification failure restores the backup
        p3 = os.path.join(d, "instinct_suppressed.tsv")
        io.open(p3, "w", encoding="utf-8", newline="").write("".join(rows))
        stale3 = p3 + ".pre_rotate_20000101T000000"                             # must SURVIVE a failed rotation
        io.open(stale3, "w", encoding="utf-8", newline="").write("old copy\n")
        real_count = globals()["_count_rows"]
        globals()["_count_rows"] = lambda path: 0
        try:
            failed = False
            try:
                apply(p3, keep_days=14, now=now, stamp="negative")
            except RuntimeError:
                failed = True
            assert failed, "verification did not fail on a lying count"
        finally:
            globals()["_count_rows"] = real_count
        assert io.open(p3, encoding="utf-8").read() == "".join(rows), "the backup was not restored"
        assert os.path.exists(stale3), "a failed rotation removed an earlier restore point"
        print("negative control OK: a failed verification restores the pre-rotation file byte for byte")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def main():
    if "--selftest" in sys.argv:
        _selftest()
        return
    do = "--apply" in sys.argv
    for path in LOGS:
        kept, moved, unreadable, _ = plan(path)
        n = sum(len(v) for v in moved.values())
        size = os.path.getsize(path) / 1e6 if os.path.exists(path) else 0
        print("%-40s %6.1f MB: keep %d rows, move %d into %s%s" % (
            os.path.relpath(path, HERE), size, len(kept), n, ", ".join(os.path.basename(a) for a in sorted(moved)) or "-",
            " (%d unreadable rows stay)" % unreadable if unreadable else ""))
        if do and n:
            t0 = time.time()
            moved_n, _ = apply(path)
            print("   moved %d rows in %.1f s; live file now %.1f MB" % (moved_n, time.time() - t0, os.path.getsize(path) / 1e6))


if __name__ == "__main__":
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        print("usage: rotate_logs.py   (help is inert; run without flags to rotate)")
        raise SystemExit(0)
    main()
