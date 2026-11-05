#!/usr/bin/env python3
"""Keep MEMORY.md under its read limit forever, losing nothing.
- trims descriptions at clause/word boundaries, preserving complete links
- if the file exceeds CAP bytes, moves the OLDEST entries (bottom, since newest is
  prepended) into INDEX_ARCHIVE.md until under cap.
Nothing is deleted: every entry stays in MEMORY.md or INDEX_ARCHIVE.md, and every
memory is fully in TOPICS/TIMELINE regardless. Idempotent. Dry-run default; 'apply' writes.

Run nightly by the dream (low contention). Reads/writes fresh to minimize races."""
import io, os, re, sys
from index_text import frontmatter_description, shorten_index_text, index_row_text

if any(a in ("-h", "--help") for a in sys.argv[1:]):
    # Discovery is not execution: this file does its work at module scope, so the
    # guard must come before the first statement that runs.
    print(__doc__ or os.path.basename(__file__))
    raise SystemExit(0)


MEM = os.path.expanduser(r"~/.claude/projects/{{BRAIN_SLUG}}/memory")
IDX = os.path.join(MEM, "MEMORY.md")
ARC = os.path.join(MEM, "INDEX_ARCHIVE.md")
APPLY = len(sys.argv) > 1 and sys.argv[1] == "apply"
CAP = 16000      # bytes; well under the ~24KB read limit, leaves headroom for the day's prepends
MAXLINE = 280
ENTRY = re.compile(r"^- \[")

def trim(line):
    row = re.match(r"^(- \[([^\]]+\.md)\]\(\2\) - )(.*)$", line)
    if not row:
        return line  # Never cut an unrecognized pointer or non-entry line.
    prefix, filename, old = row.groups()
    tag = re.match(r"^(\[[^\]]+\]\s*)", old)
    if tag:
        prefix += tag.group(1)
        old = old[tag.end():]
    # The memory owns the summary. Preserve the existing per-row budget so
    # refreshing a previously cut row cannot bloat the auto-loaded index.
    description = frontmatter_description(os.path.join(MEM, filename))
    if description:
        return prefix + index_row_text(description, max(0, min(MAXLINE, len(line)) - len(prefix)))
    if len(line) <= MAXLINE:
        return line
    return prefix + shorten_index_text(old, max(0, MAXLINE - len(prefix)))


def _memory_write_lock(timeout=20.0):
    """Hold the sidecar lock brain.py remember holds while it rewrites MEMORY.md (31 Oct, q1534): unlocked, a
    row filed between the read below and the write at the end was lost. Returns the open handle, or None on
    timeout (then nothing is written)."""
    import time
    h = open(os.environ.get("BRAIN_WRITE_LOCK", os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                               "memory_write.lock")), "a+b")
    h.seek(0, os.SEEK_END)
    if h.tell() == 0:
        h.write(b"\0")
        h.flush()
    deadline = time.monotonic() + timeout
    while True:
        try:
            if os.name == "nt":
                import msvcrt
                h.seek(0)
                msvcrt.locking(h.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(h.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return h
        except OSError:
            if time.monotonic() >= deadline:
                h.close()
                return None
            time.sleep(0.05)


_lock = _memory_write_lock() if APPLY else None
if APPLY and _lock is None:
    print("SKIPPED: the shared memory write lock stayed busy for 20 s; nothing written")
    raise SystemExit(0)

s = io.open(IDX, encoding="utf-8").read()
lines = s.split("\n")
title = lines[0] if lines and lines[0].startswith("#") else "# Memory Index"
body = lines[1:] if lines and lines[0].startswith("#") else lines

entries = [trim(l) for l in body if ENTRY.match(l)]
other = [l for l in body if l.strip() and not ENTRY.match(l)]  # stray non-entry lines, preserved

# grow the kept set from the top (newest) until the byte cap
kept, overflow, size = [], [], len((title + "\n").encode("utf-8"))
for e in entries:
    b = len((e + "\n").encode("utf-8"))
    if size + b <= CAP or not kept:
        kept.append(e); size += b
    else:
        overflow.append(e)

new_idx = title + "\n" + "\n".join(kept) + ("\n" if kept else "")
before = len(s.encode("utf-8"))
after = len(new_idx.encode("utf-8"))

archived = 0
if overflow:
    arc_old = io.open(ARC, encoding="utf-8").read() if os.path.exists(ARC) else "# Index archive (older memory-index lines; nothing lost — full detail in TOPICS/TIMELINE)\n"
    a_lines = arc_old.split("\n")
    a_title = a_lines[0]
    a_rest = "\n".join(a_lines[1:]).strip("\n")
    new_arc = a_title + "\n\n" + "\n".join(overflow) + ("\n" + a_rest if a_rest else "") + "\n"
    archived = len(overflow)

print(("APPLIED" if APPLY else "DRY-RUN"),
      f"entries={len(entries)} kept={len(kept)} archived={archived} "
      f"size {before}B -> {after}B (cap {CAP})")
def _atomic_write(path, text):
    # tmp + fsync + replace, as remember does: readers that skip the lock (session load, recall) never see a
    # half-written file, and a crash leaves the old file whole
    tmp = "%s.tmp.%d" % (path, os.getpid())
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


if APPLY and (new_idx != s or archived):
    # archive first: a crash between the two writes then leaves a row in both files, never in neither
    if overflow:
        _atomic_write(ARC, new_arc)
    _atomic_write(IDX, new_idx)
    print("  wrote MEMORY.md" + (" + INDEX_ARCHIVE.md" if overflow else ""))
