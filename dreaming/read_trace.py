#!/usr/bin/env python3
"""read_trace.py - a durable, append-only record of which memory was READ, when, and by which session.

WHY (29 Oct 2026, Mac mini). build_retrieval_trace.py rebuilds retrievals.tsv from Read tool calls in the Claude
transcripts, and the usage tilt in recall is built from that trace. On the Mac almost no session writes a transcript
(one .jsonl while six or more sessions ran), and the one that does read memories through Bash (sed, cat) and
`brain.py node`, never the Read tool: 0 memory Reads in its 45 MB. The per-session co-read logs that coactivation_log.py
writes are folded and deleted at every Stop. So the trace came out header-only and the tilt switched itself off.

This file is the record that does not depend on transcripts. One line per read, appended with a single O_APPEND
write (atomic for a line this short on a local disk, so concurrent sessions cannot interleave):

    slug  epoch  session  src  ref  entrypoint

src is 'read' (Read tool on a memory file), 'bash' (a plain reader - cat, head, tail, less, more, bat, sed -n - whose
first command names a memory file by full path) or 'node' (brain.py node <slug>). ref is the tool_use_id when the hook
payload has one, so build_retrieval_trace.py drops the copy of a Read it also finds in a transcript. entrypoint is
CLAUDE_CODE_ENTRYPOINT (cli for an interactive session, sdk-* for a headless one), kept for audits. Test workers are
not filtered here: the trace builder applies the test-session rule, so this file stays raw history. Never raises.
"""
import os
import re
import time

HOME = os.path.expanduser("~")
READS = os.environ.get("BRAIN_READS_LOG") or os.path.join(HOME, ".claude", "dreaming", "memory_reads.tsv")
_PROJECT = "{{BRAIN_SLUG}}"
_ROOTS = "|".join(re.escape(r) for r in sorted({HOME, "~", "$HOME", "${HOME}"}, key=len, reverse=True))
_MEM_RE = re.compile(r"(?:%s)/\.claude/projects/%s/memory/([A-Za-z0-9][A-Za-z0-9_.-]*)\.md\b"
                     % (_ROOTS, re.escape(_PROJECT)))
_READER = re.compile(r"^\s*(?:sudo\s+|command\s+)?(?:cat|head|tail|less|more|bat|sed\s+-n)\b")
_REDIRECT = re.compile(r"\d*>>?\s*\S+")
_CLEAN = re.compile(r"[\t\r\n]")


def bash_reads(command):
    """Memory slugs a shell command plainly reads; [] for anything else (grep, writes, variables, relative paths).
    Only the first command of each statement counts, so `sed -n 1,9p FILE | grep x` is a read and
    `grep x FILE` or `printf .. >> FILE` is not."""
    cmd = str(command or "")
    if "/memory/" not in cmd or ".md" not in cmd:
        return []
    out = []
    for statement in re.split(r"&&|\|\||;|\n", cmd):
        first = statement.split("|", 1)[0]
        if not _READER.match(first):
            continue
        for m in _MEM_RE.finditer(_REDIRECT.sub(" ", first)):
            slug = m.group(1)
            if slug not in out:
                out.append(slug)
    return out


def append(slug, session, src, ref="", path=None):
    """Append one read. Never raises."""
    try:
        row = "\t".join(_CLEAN.sub(" ", str(x or "")) for x in (
            slug, "%.6f" % time.time(), session or "nosess", src, ref,
            os.environ.get("CLAUDE_CODE_ENTRYPOINT", ""))) + "\n"
        fd = os.open(path or READS, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, row.encode("utf-8"))
        finally:
            os.close(fd)
    except Exception:
        pass
