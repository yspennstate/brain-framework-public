#!/usr/bin/env python3
"""Build a typed, provenance-preserving memory-access ledger.

``retrievals.tsv`` deliberately remains the narrow gold trace: explicit Claude ``Read`` tool calls.
This companion observes additional access routes without pretending that discovery or surfacing is a
read.  It streams Claude and Codex transcripts plus ``surfaced.tsv`` and emits only metadata:

* ``read_explicit``  - Claude Read tool on an exact memory file (confidence 1.00);
* ``read_command``   - an unambiguous shell file reader such as Get-Content on an exact memory path;
* ``search_command`` - rg/grep/Select-String over the memory corpus (never labelled a read);
* ``recall_query``   - a ``brain.py recall`` invocation, with no query text retained;
* ``recall_surface`` - a slug parsed from that recall command's ranked output;
* ``hook_surface``   - a FOK/spread entry already recorded in ``surfaced.tsv``.

No raw prompt, command, tool output, query, or memory content is written.  Evidence is represented by
a SHA-256 digest and a transcript basename/line number.  Default execution is report-only; ``--write``
atomically replaces this mechanism's own sidecar and never touches ``retrievals.tsv``.

The default seven-day window bounds a 5+ GB Codex transcript archive.  Use ``--days 0`` for a full
rebuild or a smaller window for a quick coverage report.
"""

from __future__ import print_function

import argparse
import csv
import datetime as _dt
import glob
import hashlib
import io
import json
import math
import os
import re
import sys
import time
from collections import Counter, defaultdict


HOME = os.path.expanduser("~")
DR = os.path.dirname(os.path.abspath(__file__))
CLAUDE_DIR = os.path.join(HOME, ".claude", "projects", "{{BRAIN_SLUG}}")
CODEX_DIR = os.path.join(HOME, ".codex", "sessions")
SURFACED = os.path.join(HOME, ".claude", "dreaming", "surfaced.tsv")
RETRIEVALS = os.path.join(HOME, ".claude", "dreaming", "retrievals.tsv")
OUT = os.path.join(HOME, ".claude", "dreaming", "memory_access.tsv")
DEFAULT_DAYS = 7.0

FIELDS = (
    "event_id", "epoch", "iso", "session", "actor", "source", "access_kind", "slug",
    "confidence", "artifact", "line", "evidence_sha256",
)

_MEMORY_MARKERS = (
    "/.claude/projects/{{BRAIN_SLUG}}/memory/",
    "/brain-repo/01_ai_consciousness/claude/memory/",
    "/.codex/memories/",
)
_CONTEXT_FILES = {"recent_brain.md": "RECENT_BRAIN"}
_SHELL_NAMES = {"bash", "powershell", "shell", "shell_command", "exec_command"}
_COMMAND_PREFIX = r"(?:^|\|\s*)(?:&\s*)?(?:(?:sudo|command)\s+)?"
_READ_OP = re.compile(
    _COMMAND_PREFIX + r"(?:Get-Content|cat|head|tail|less|more|type|sed\s+-n)\b", re.I
)
_SEARCH_OP = re.compile(
    _COMMAND_PREFIX + r"(?:rg|grep|egrep|fgrep|findstr|Select-String)\b", re.I
)
_BRAIN_RECALL = re.compile(
    _COMMAND_PREFIX
    + r"(?:(?:(?:python(?:3)?|py)(?:\.exe)?)(?:\s+-[A-Za-z]+)*\s+)?"
      r"(?:\"[^\"\r\n]*brain\.py\"|'[^'\r\n]*brain\.py'|[^\s|\"']*brain\.py)\s+recall\b",
    re.I,
)
_RANKED_RECALL = re.compile(
    r"^\s*(?:[-*]\s*)?(?:0|1)(?:\.\d+)?\s+\[[^\]]+\]\s+([A-Za-z0-9][A-Za-z0-9_.-]{2,})\s*$"
)
_QUOTED_MD = re.compile(r"[\"']([^\"'\r\n]*?\.md)[\"']", re.I)
_BARE_MD = re.compile(
    r"(?<![A-Za-z0-9_])((?:[A-Za-z]:|~|\$HOME|\$env:USERPROFILE|\.\.?)[^\s;|,\"']*?\.md)\b",
    re.I,
)
_COMMAND_DQ = re.compile(r"\bcommand\s*:\s*(\"(?:\\.|[^\"\\])*\")", re.I | re.S)
_COMMAND_SQ = re.compile(r"\bcommand\s*:\s*('(?:\\.|[^'\\])*')", re.I | re.S)
_COMMAND_BT = re.compile(r"\bcommand\s*:\s*`([^`]*)`", re.I | re.S)
_SAFE_SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,180}$")
_HOOK_SOURCES = {"fok", "premise", "spread"}
MAX_FUTURE_SKEW_SECONDS = 300.0
# Transcript lines are read as bytes in chunks and decoded only when they carry a needle. A single
# line above MAX_LINE_BYTES (a 34 MB tool result has been measured in a Codex rollout) is skipped
# whole and counted, never decoded: the 2026-09-13 dream died in a MemoryError inside this loop after
# holding dream.lock for twelve hours, with the machine under memory pressure from other work.
MAX_LINE_BYTES = 64 * 1024 * 1024
SCAN_CHUNK_BYTES = 8 * 1024 * 1024
_CLAUDE_NEEDLES = (b'"tool_use"', b'"tool_result"')
_CODEX_NEEDLES = (b'"response_item"', b'"session_meta"')
_DEADLINE = [None]


# Per-file scan cache. Transcripts are append-only, so a file whose size and mtime have not moved
# since the last --write yields exactly the rows it yielded then; reusing them is what turns a
# disk-bound 8 GB walk into a read of the few dozen files that changed today. The cache is keyed
# by the scanner's own source hash, so a code change invalidates it wholesale.
SCAN_CACHE = os.path.join(HOME, ".claude", "dreaming", "memory_access_scan_cache.json")
_CACHE = {"enabled": False, "old": {}, "new": {}}


def _scanner_sha():
    try:
        with io.open(os.path.abspath(__file__), "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()[:16]
    except Exception:
        return "unknown"


def _cache_load(path):
    _CACHE["enabled"] = True
    _CACHE["old"], _CACHE["new"] = {}, {}
    try:
        with io.open(path, encoding="utf-8") as fh:
            blob = json.load(fh)
        if blob.get("scanner_sha") == _scanner_sha() and isinstance(blob.get("files"), dict):
            _CACHE["old"] = blob["files"]
    except Exception:
        pass


def _cache_take(path, cutoff, end, stats, prefix):
    """Return the cached rows for an unchanged file (re-filtered to the window), else None."""
    if not _CACHE["enabled"]:
        return None
    entry = _CACHE["old"].get(path)
    if not entry:
        return None
    try:
        st = os.stat(path)
    except Exception:
        return None
    if entry.get("size") != st.st_size or entry.get("mtime") != st.st_mtime:
        return None
    rows = []
    for row in entry.get("rows", []):
        try:
            epoch = float(row["epoch"])
        except Exception:
            continue
        if cutoff is not None and epoch < cutoff:
            continue
        if end is not None and epoch > end:
            continue
        rows.append(dict(row))
    _CACHE["new"][path] = entry
    stats[prefix + "_files_cached"] += 1
    return rows


def _cache_put(path, rows):
    if not _CACHE["enabled"]:
        return
    try:
        st = os.stat(path)
    except Exception:
        return
    _CACHE["new"][path] = {"size": st.st_size, "mtime": st.st_mtime, "rows": [dict(r) for r in rows]}


def _cache_save(path):
    if not _CACHE["enabled"]:
        return
    blob = {"scanner_sha": _scanner_sha(), "written": _iso(time.time()), "files": _CACHE["new"]}
    tmp = "%s.tmp.%d" % (path, os.getpid())
    with io.open(tmp, "w", encoding="utf-8") as fh:
        json.dump(blob, fh)
    os.replace(tmp, path)


class ScanBudgetExceeded(Exception):
    """Raised when --max-seconds elapses mid-scan; the caller must not write a partial ledger."""


def _check_budget():
    deadline = _DEADLINE[0]
    if deadline is not None and time.time() > deadline:
        raise ScanBudgetExceeded("scan budget exhausted")


def _needle_lines(fh, needles, stats, prefix):
    """Yield (line_no, text) for the lines of a binary file handle that contain any needle.

    Lines are counted exactly as the old text reader counted them; only matching lines are decoded.
    A line longer than MAX_LINE_BYTES is dropped (stats[prefix + '_oversize_lines']) and the scan
    continues at its newline. The time budget is checked once per chunk.
    """
    line_no = 0
    buf = b""
    skipping = False
    while True:
        chunk = fh.read(SCAN_CHUNK_BYTES)
        if not chunk:
            break
        buf += chunk
        parts = buf.split(b"\n")
        buf = parts.pop()
        for part in parts:
            line_no += 1
            stats[prefix + "_lines"] += 1
            if skipping:
                skipping = False
                stats[prefix + "_oversize_lines"] += 1
                continue
            if any(needle in part for needle in needles):
                yield line_no, part.decode("utf-8", "replace")
        if len(buf) > MAX_LINE_BYTES:
            buf = b""
            skipping = True
        _check_budget()
    if buf:
        line_no += 1
        stats[prefix + "_lines"] += 1
        if skipping:
            stats[prefix + "_oversize_lines"] += 1
        elif any(needle in buf for needle in needles):
            yield line_no, buf.decode("utf-8", "replace")


def _sha(value):
    return hashlib.sha256(str(value).encode("utf-8", "replace")).hexdigest()


def _parse_time(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        return v if math.isfinite(v) else None
    try:
        # TSV ledgers store epochs as decimal strings; ISO parsing alone would reject them.
        if re.fullmatch(r"[+-]?\d+(?:\.\d+)?", str(value).strip()):
            v = float(str(value).strip())
            return v if math.isfinite(v) else None
    except Exception:
        return None
    try:
        return _dt.datetime.fromisoformat(str(value).strip().replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _iso(epoch):
    return _dt.datetime.fromtimestamp(float(epoch), _dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _safe(value):
    return str(value or "").replace("\t", " ").replace("\r", " ").replace("\n", " ")[:240]


def _text_content(value):
    """Flatten a tool-result shape without retaining it after parsing."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(_text_content(x) for x in value)
    if isinstance(value, dict):
        for key in ("text", "content", "output"):
            if key in value:
                return _text_content(value[key])
    return ""


def _resource_from_path(path):
    """Return a memory slug only for a known brain root; unresolved templates are rejected."""
    raw = str(path or "").strip().strip("\"'").replace("\\", "/")
    if not raw or any(x in raw for x in ("${", "*", "?", "<slug>")):
        return None
    low = raw.lower()
    base = low.rsplit("/", 1)[-1]
    if base in _CONTEXT_FILES and "/.codex/" in low:
        return _CONTEXT_FILES[base]
    if not any(marker in low for marker in _MEMORY_MARKERS):
        return None
    name = raw.rsplit("/", 1)[-1]
    if not name.lower().endswith(".md"):
        return None
    slug = name[:-3]
    return slug if _SAFE_SLUG.fullmatch(slug) else None


def _paths_in(text):
    seen = set()
    for rx in (_QUOTED_MD, _BARE_MD):
        for match in rx.finditer(text or ""):
            slug = _resource_from_path(match.group(1))
            if slug and slug not in seen:
                seen.add(slug)
                yield slug


def _has_memory_scope(command):
    low = str(command or "").replace("\\", "/").lower()
    return any(x.rstrip("/") in low for x in _MEMORY_MARKERS) or "/.codex/recent_brain.md" in low


def _split_statements(command):
    """Split shell chains outside quotes; keep pipelines intact for search-vs-read semantics."""
    text = str(command or "")
    out, buf = [], []
    quote = None
    i = 0

    def flush():
        value = "".join(buf).strip()
        if value:
            out.append(value)
        del buf[:]

    while i < len(text):
        ch = text[i]
        if quote is not None:
            buf.append(ch)
            if ch == "`" and i + 1 < len(text):
                i += 1
                buf.append(text[i])
            elif ch == "\\" and quote == '"' and i + 1 < len(text):
                i += 1
                buf.append(text[i])
            elif ch == quote:
                quote = None
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            buf.append(ch)
            i += 1
            continue
        if ch == "`" and i + 1 < len(text):
            buf.extend((ch, text[i + 1]))
            i += 2
            continue
        if ch == "#" and (not buf or str(buf[-1]).isspace()):
            while i < len(text) and text[i] not in "\r\n":
                i += 1
            flush()
            continue
        if ch in ";\r\n" or text[i:i + 2] in ("&&", "||"):
            flush()
            i += 2 if text[i:i + 2] in ("&&", "||") else 1
            continue
        buf.append(ch)
        i += 1
    flush()
    return out


def _has_brain_recall(command):
    return any(_BRAIN_RECALL.search(statement) for statement in _split_statements(command))


def _decode_single_quoted(literal):
    """Decode a JavaScript single-quoted literal without corrupting Windows paths.

    ``ast.literal_eval`` treats ``C:\\Users`` as a malformed Python unicode escape, while
    Codex exec wrappers are JavaScript. Preserve unknown escapes (notably path separators)
    and decode only the small, unambiguous escape set a generated wrapper can need.
    """
    body = literal[1:-1]
    out = []
    i = 0
    simple = {"n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f", "v": "\v"}
    while i < len(body):
        char = body[i]
        if char != "\\" or i + 1 >= len(body):
            out.append(char)
            i += 1
            continue
        nxt = body[i + 1]
        if nxt in simple:
            out.append(simple[nxt])
            i += 2
        elif nxt in ("'", '"', "\\", "/"):
            out.append(nxt)
            i += 2
        elif nxt == "x" and i + 3 < len(body) and re.fullmatch(r"[0-9A-Fa-f]{2}", body[i + 2:i + 4]):
            out.append(chr(int(body[i + 2:i + 4], 16)))
            i += 4
        elif nxt == "u" and i + 5 < len(body) and re.fullmatch(r"[0-9A-Fa-f]{4}", body[i + 2:i + 6]):
            out.append(chr(int(body[i + 2:i + 6], 16)))
            i += 6
        else:
            out.extend(("\\", nxt))
            i += 2
    return "".join(out)


def _extract_commands(tool_input):
    """Extract shell ``command`` string literals from a Codex exec wrapper or JSON arguments."""
    if isinstance(tool_input, dict):
        value = tool_input.get("command") or tool_input.get("cmd")
        return [value] if isinstance(value, str) else []
    text = str(tool_input or "")
    out = []
    for match in _COMMAND_DQ.finditer(text):
        try:
            value = json.loads(match.group(1))
        except Exception:
            continue
        if isinstance(value, str):
            out.append(value)
    for match in _COMMAND_SQ.finditer(text):
        try:
            value = _decode_single_quoted(match.group(1))
        except Exception:
            continue
        if isinstance(value, str):
            out.append(value)
    out.extend(match.group(1) for match in _COMMAND_BT.finditer(text))
    if not out:
        try:
            obj = json.loads(text)
            if isinstance(obj, dict) and isinstance(obj.get("command"), str):
                out.append(obj["command"])
        except Exception:
            pass
    return out


def _new_row(epoch, session, actor, source, kind, slug, confidence, artifact, line, evidence):
    evidence_hash = _sha(evidence)
    canonical = "|".join((
        source, kind, str(session or ""), "%.3f" % float(epoch), str(slug or ""), evidence_hash,
    ))
    return {
        "event_id": _sha(canonical)[:24],
        "epoch": "%.3f" % float(epoch),
        "iso": _iso(epoch),
        "session": _safe(session),
        "actor": actor,
        "source": source,
        "access_kind": kind,
        "slug": _safe(slug),
        "confidence": "%.2f" % float(confidence),
        "artifact": _safe(os.path.basename(artifact or "")),
        "line": str(int(line or 0)),
        "evidence_sha256": evidence_hash,
    }


def _command_rows(command, epoch, session, actor, source, artifact, line, call_ref):
    """Classify shell evidence. Search and recall are deliberately never returned as reads."""
    rows = []
    command = str(command or "")
    statements = _split_statements(command)
    for pos, statement in enumerate(statements):
        slugs = list(_paths_in(statement))
        is_search = bool(_SEARCH_OP.search(statement) and _has_memory_scope(statement))
        # A pipeline such as ``Get-Content file | Select-String pattern`` is observationally a
        # search, not evidence that the agent consumed the file.  The narrower class wins.
        if _READ_OP.search(statement) and not is_search:
            for slug in slugs:
                rows.append(_new_row(
                    epoch, session, actor, source, "read_command", slug, .95, artifact, line,
                    "%s|read|%s" % (call_ref, slug),
                ))
        if is_search:
            targets = slugs or [""]
            for slug in targets:
                rows.append(_new_row(
                    epoch, session, actor, source, "search_command", slug, .90, artifact, line,
                    "%s|search|%d|%s" % (call_ref, pos, slug or "corpus"),
                ))
    if _has_brain_recall(command):
        rows.append(_new_row(
            epoch, session, actor, source, "recall_query", "", 1.0, artifact, line,
            "%s|recall-query" % call_ref,
        ))
    return rows


def _recall_surface_rows(output, epoch, session, actor, source, artifact, line, call_ref):
    rows = []
    seen = set()
    for text_line in str(output or "").splitlines():
        match = _RANKED_RECALL.match(text_line)
        if not match:
            continue
        slug = match.group(1)
        if slug in seen:
            continue
        seen.add(slug)
        rows.append(_new_row(
            epoch, session, actor, source, "recall_surface", slug, .95, artifact, line,
            "%s|recall-surface|%s" % (call_ref, slug),
        ))
    return rows


def scan_claude(paths, cutoff=None, end=None):
    rows, stats = [], Counter()
    for path in sorted(paths):
        stats["claude_files"] += 1
        pending = {}
        cached = _cache_take(path, cutoff, end, stats, "claude")
        if cached is not None:
            rows.extend(cached)
            continue
        start = len(rows)
        try:
            fh = io.open(path, "rb")
        except Exception:
            stats["claude_open_error"] += 1
            continue
        with fh:
            for line_no, raw in _needle_lines(fh, _CLAUDE_NEEDLES, stats, "claude"):
                try:
                    obj = json.loads(raw)
                except Exception:
                    stats["claude_bad_json"] += 1
                    continue
                epoch = _parse_time(obj.get("timestamp"))
                if epoch is None:
                    stats["claude_missing_ts"] += 1
                    continue
                if cutoff is not None and epoch < cutoff:
                    continue
                if end is not None and epoch > end:
                    stats["claude_future_ts"] += 1
                    continue
                session = obj.get("sessionId") or os.path.basename(path)[:-6]
                message = obj.get("message") if isinstance(obj.get("message"), dict) else {}
                content = message.get("content")
                if not isinstance(content, list):
                    continue
                for item in content:
                    if not isinstance(item, dict):
                        continue
                    if item.get("type") == "tool_use":
                        tool_id = str(item.get("id") or "line-%d" % line_no)
                        name = str(item.get("name") or "")
                        inp = item.get("input") if isinstance(item.get("input"), dict) else {}
                        if name == "Read":
                            slug = _resource_from_path(inp.get("file_path"))
                            if slug:
                                rows.append(_new_row(
                                    epoch, session, "claude", "claude_read_tool", "read_explicit",
                                    slug, 1.0, path, line_no, "%s|explicit|%s" % (tool_id, slug),
                                ))
                        elif name.lower() in _SHELL_NAMES:
                            command = inp.get("command") or inp.get("cmd") or ""
                            rows.extend(_command_rows(
                                command, epoch, session, "claude", "claude_shell", path, line_no, tool_id,
                            ))
                            if _has_brain_recall(command):
                                pending[tool_id] = (epoch, session, line_no)
                    elif item.get("type") == "tool_result":
                        tool_id = str(item.get("tool_use_id") or "")
                        if tool_id in pending:
                            call_epoch, call_session, call_line = pending.pop(tool_id)
                            rows.extend(_recall_surface_rows(
                                _text_content(item.get("content")), call_epoch, call_session, "claude",
                                "brain_cli_claude", path, call_line, tool_id,
                            ))
        _cache_put(path, rows[start:])
    stats["claude_rows"] = len(rows)
    return rows, stats


def scan_codex(paths, cutoff=None, end=None):
    rows, stats = [], Counter()
    for path in sorted(paths):
        stats["codex_files"] += 1
        session = os.path.basename(path).rsplit("-", 5)[-1].replace(".jsonl", "")
        pending = {}
        cached = _cache_take(path, cutoff, end, stats, "codex")
        if cached is not None:
            rows.extend(cached)
            continue
        start = len(rows)
        try:
            fh = io.open(path, "rb")
        except Exception:
            stats["codex_open_error"] += 1
            continue
        with fh:
            for line_no, raw in _needle_lines(fh, _CODEX_NEEDLES, stats, "codex"):
                try:
                    obj = json.loads(raw)
                except Exception:
                    stats["codex_bad_json"] += 1
                    continue
                payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
                if obj.get("type") == "session_meta":
                    session = str(payload.get("id") or payload.get("session_id") or session)
                    continue
                if obj.get("type") != "response_item":
                    continue
                epoch = _parse_time(obj.get("timestamp"))
                if epoch is None:
                    stats["codex_missing_ts"] += 1
                    continue
                if cutoff is not None and epoch < cutoff:
                    continue
                if end is not None and epoch > end:
                    stats["codex_future_ts"] += 1
                    continue
                ptype = payload.get("type")
                if ptype == "custom_tool_call" and payload.get("name") == "exec":
                    call_id = str(payload.get("call_id") or payload.get("id") or "line-%d" % line_no)
                    commands = _extract_commands(payload.get("input"))
                    for command in commands:
                        rows.extend(_command_rows(
                            command, epoch, session, "codex", "codex_shell", path, line_no, call_id,
                        ))
                    if any(_has_brain_recall(c) for c in commands):
                        pending[call_id] = (epoch, session, line_no)
                elif ptype == "function_call" and str(payload.get("name") or "").lower() in _SHELL_NAMES:
                    call_id = str(payload.get("call_id") or payload.get("id") or "line-%d" % line_no)
                    commands = _extract_commands(payload.get("arguments"))
                    for command in commands:
                        rows.extend(_command_rows(
                            command, epoch, session, "codex", "codex_shell", path, line_no, call_id,
                        ))
                    if any(_has_brain_recall(c) for c in commands):
                        pending[call_id] = (epoch, session, line_no)
                elif ptype in ("custom_tool_call_output", "function_call_output"):
                    call_id = str(payload.get("call_id") or "")
                    if call_id in pending:
                        call_epoch, call_session, call_line = pending.pop(call_id)
                        rows.extend(_recall_surface_rows(
                            _text_content(payload.get("output")), call_epoch, call_session, "codex",
                            "brain_cli_codex", path, call_line, call_id,
                        ))
        _cache_put(path, rows[start:])
    stats["codex_rows"] = len(rows)
    return rows, stats


def scan_surfaced(path, cutoff=None, end=None):
    rows, stats = [], Counter()
    if not path or not os.path.exists(path):
        stats["surface_missing"] += 1
        return rows, stats
    try:
        fh = io.open(path, encoding="utf-8", errors="replace")
    except Exception:
        stats["surface_open_error"] += 1
        return rows, stats
    with fh:
        for line_no, raw in enumerate(fh, 1):
            if line_no == 1:
                continue
            parts = raw.rstrip("\n").split("\t")
            if len(parts) < 4:
                stats["surface_bad_row"] += 1
                continue
            epoch = _parse_time(parts[0])
            if epoch is None or (cutoff is not None and epoch < cutoff):
                continue
            if end is not None and epoch > end:
                stats["surface_future_ts"] += 1
                continue
            session, slug, src = parts[1], parts[2], parts[3].strip().lower()
            if not _SAFE_SLUG.fullmatch(slug):
                stats["surface_bad_slug"] += 1
                continue
            safe_src = src if src in _HOOK_SOURCES else "unknown"
            rows.append(_new_row(
                epoch, session, "hook", "hook_%s" % safe_src, "hook_surface", slug,
                1.0, path, line_no, "surface|%s|%s|%s" % (parts[0], session, slug),
            ))
    stats["surface_rows"] = len(rows)
    return rows, stats


def _dedupe(rows):
    by_id = {}
    for row in sorted(rows, key=lambda r: (
        float(r["epoch"]), r["source"], r["access_kind"], r["session"], r["slug"],
        r["artifact"], int(r["line"]),
    )):
        by_id.setdefault(row["event_id"], row)
    return sorted(by_id.values(), key=lambda r: (
        float(r["epoch"]), r["source"], r["access_kind"], r["session"], r["slug"], r["event_id"],
    ))


def build_records(claude_paths=(), codex_paths=(), surfaced_path=None, cutoff=None, end=None):
    crows, cs = scan_claude(claude_paths, cutoff=cutoff, end=end)
    xrows, xs = scan_codex(codex_paths, cutoff=cutoff, end=end)
    srows, ss = scan_surfaced(surfaced_path, cutoff=cutoff, end=end)
    raw = crows + xrows + srows
    rows = _dedupe(raw)
    stats = Counter()
    stats.update(cs); stats.update(xs); stats.update(ss)
    stats["rows_before_dedupe"] = len(raw)
    stats["duplicates"] = len(raw) - len(rows)
    stats["rows"] = len(rows)
    return rows, stats


def _atomic_write(path, rows):
    tmp = "%s.tmp.%d" % (path, os.getpid())
    try:
        with io.open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            writer = csv.DictWriter(fh, fieldnames=FIELDS, delimiter="\t", lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except Exception:
            pass
        raise


def _legacy_comparison(rows, path, cutoff=None, end=None):
    typed = set()
    for row in rows:
        if row["access_kind"] == "read_explicit":
            typed.add((row["slug"], int(round(float(row["epoch"]))), row["session"]))
    legacy = set()
    try:
        fh = io.open(path, encoding="utf-8", errors="replace")
    except Exception:
        return {"legacy_rows": 0, "matched": 0, "typed_only": len(typed), "legacy_only": 0}
    with fh:
        next(fh, None)
        for raw in fh:
            parts = raw.rstrip("\n").split("\t")
            if len(parts) < 4:
                continue
            try:
                epoch = float(parts[1])
            except Exception:
                continue
            if cutoff is not None and epoch < cutoff:
                continue
            if end is not None and epoch > end:
                continue
            legacy.add((parts[0], int(round(epoch)), parts[3]))
    return {
        "legacy_rows": len(legacy),
        "matched": len(legacy & typed),
        "typed_only": len(typed - legacy),
        "legacy_only": len(legacy - typed),
    }


def summarize(rows, stats=None, retrievals_path=None, cutoff=None, end=None):
    stats = Counter(stats or {})
    kinds, sources = Counter(), Counter()
    slugs_by_kind = defaultdict(set)
    sessions = set()
    for row in rows:
        kinds[row["access_kind"]] += 1
        sources[row["source"]] += 1
        sessions.add(row["session"])
        if row["slug"]:
            slugs_by_kind[row["access_kind"]].add(row["slug"])
    explicit_slugs = slugs_by_kind["read_explicit"]
    command_slugs = slugs_by_kind["read_command"]
    report = {
        "events": len(rows),
        "sessions": len(sessions),
        "distinct_slugs": len({r["slug"] for r in rows if r["slug"]}),
        "kinds": dict(sorted(kinds.items())),
        "sources": dict(sorted(sources.items())),
        "distinct_slugs_by_kind": {k: len(v) for k, v in sorted(slugs_by_kind.items())},
        "command_only_slugs_beyond_explicit": len(command_slugs - explicit_slugs),
        "scan": dict(sorted(stats.items())),
    }
    if retrievals_path:
        report["retrievals_compat"] = _legacy_comparison(
            rows, retrievals_path, cutoff=cutoff, end=end
        )
    return report


def _print_report(report, days, wrote=None):
    print("memory_access: %d typed events across %d sessions / %d slugs (window=%s)" % (
        report["events"], report["sessions"], report["distinct_slugs"],
        "all" if not days else "%gd" % days,
    ))
    print("  kinds: " + ", ".join("%s=%d" % x for x in sorted(report["kinds"].items())))
    print("  sources: " + ", ".join("%s=%d" % x for x in sorted(report["sources"].items())))
    print("  exact command-read slugs absent from explicit-Read ground truth: %d" %
          report["command_only_slugs_beyond_explicit"])
    compat = report.get("retrievals_compat")
    if compat:
        print("  retrievals.tsv compatibility: legacy=%d matched=%d typed-only=%d legacy-only=%d" % (
            compat["legacy_rows"], compat["matched"], compat["typed_only"], compat["legacy_only"],
        ))
    scan = report.get("scan", {})
    print("  scanned: claude files=%d (%d from cache) lines=%d; codex files=%d (%d from cache) lines=%d; "
          "deduped=%d; oversize lines skipped=%d; scan %.1f s" % (
        scan.get("claude_files", 0), scan.get("claude_files_cached", 0), scan.get("claude_lines", 0),
        scan.get("codex_files", 0), scan.get("codex_files_cached", 0), scan.get("codex_lines", 0),
        scan.get("duplicates", 0),
        scan.get("claude_oversize_lines", 0) + scan.get("codex_oversize_lines", 0),
        scan.get("scan_seconds", 0.0),
    ))
    print("  semantics: searches and surfaces remain typed NON-READ events; raw content is never emitted")
    if wrote:
        print("  wrote atomically: %s" % wrote)


def _size_or_zero(path):
    try:
        return os.path.getsize(path)
    except Exception:
        return 0


def _discover(root, recursive, cutoff):
    pattern = os.path.join(root, "**", "*.jsonl") if recursive else os.path.join(root, "*.jsonl")
    out = []
    for path in glob.iglob(pattern, recursive=recursive):
        try:
            if cutoff is None or os.path.getmtime(path) >= cutoff:
                out.append(path)
        except Exception:
            pass
    return sorted(out)


def _args(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=float, default=DEFAULT_DAYS,
                        help="event/file window; 0 scans all history (default: 7)")
    parser.add_argument("--claude-dir", default=CLAUDE_DIR)
    parser.add_argument("--codex-dir", default=CODEX_DIR)
    parser.add_argument("--surfaced", default=SURFACED)
    parser.add_argument("--retrievals", default=RETRIEVALS)
    parser.add_argument("--out", default=OUT)
    parser.add_argument("--write", action="store_true", help="atomically replace only --out")
    parser.add_argument("--json", action="store_true", help="print aggregate metadata as JSON")
    parser.add_argument("--max-seconds", type=float, default=0.0,
                        help="abort the scan (exit 3, nothing written) after this many seconds; 0 = unlimited")
    parser.add_argument("--cache", default=SCAN_CACHE,
                        help="per-file scan cache (unchanged transcripts reuse their rows); saved only with --write")
    parser.add_argument("--no-cache", dest="cache", action="store_const", const="",
                        help="read every file; never touch the cache")
    args = parser.parse_args(argv)
    if not math.isfinite(args.max_seconds) or args.max_seconds < 0:
        parser.error("--max-seconds must be a finite non-negative number")
    if not math.isfinite(args.days) or args.days < 0:
        parser.error("--days must be a finite non-negative number")
    return args


def main(argv=None):
    args = _args(sys.argv[1:] if argv is None else argv)
    missing_roots = [path for path in (args.claude_dir, args.codex_dir) if not os.path.isdir(path)]
    if missing_roots:
        print("build_memory_access: required transcript root missing: %s" %
              ", ".join(os.path.abspath(path) for path in missing_roots))
        return 2
    now = time.time()
    cutoff = now - args.days * 86400.0 if args.days and args.days > 0 else None
    end = now + MAX_FUTURE_SKEW_SECONDS
    # Claude subagents carry the parent sessionId in their own nested transcripts. Excluding them
    # erased most delegated Read/shell activity from the typed ledger while claiming actor coverage.
    claude_paths = _discover(args.claude_dir, True, cutoff)
    codex_paths = _discover(args.codex_dir, True, cutoff)
    _DEADLINE[0] = now + args.max_seconds if args.max_seconds > 0 else None
    if args.cache:
        _cache_load(args.cache)
    started = time.time()
    try:
        rows, stats = build_records(
            claude_paths, codex_paths, args.surfaced, cutoff=cutoff, end=end
        )
    except ScanBudgetExceeded:
        print("build_memory_access: scan budget of %.0f s exhausted after %d Claude + %d Codex files "
              "(%.1f GB in window); nothing written, prior sidecar preserved" % (
                  args.max_seconds, len(claude_paths), len(codex_paths),
                  sum(_size_or_zero(p) for p in claude_paths + codex_paths) / 1e9))
        return 3
    stats["scan_seconds"] = round(time.time() - started, 1)
    report = summarize(rows, stats, args.retrievals, cutoff=cutoff, end=end)
    wrote = None
    if args.write:
        _atomic_write(args.out, rows)
        wrote = args.out
        if args.cache:
            try:
                _cache_save(args.cache)
            except Exception as exc:
                print("build_memory_access: scan cache not saved (%s); next run reads every file" %
                      type(exc).__name__)
    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        _print_report(report, args.days, wrote=wrote)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
