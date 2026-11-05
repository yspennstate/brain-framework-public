#!/usr/bin/env python3
"""Read-only gate for memory birth dates and their downstream age behavior.

The live corpus currently has three incompatible ideas of age: file mtime, the
permissive ``memory_date`` resolver (slug -> created -> description -> mtime), and
a proposed provenance-preserving resolver (created -> FULL filename date ->
unknown).  This evaluator does not alter any of them.  It asks four narrow
questions:

1. Does the strict resolver obey synthetic precedence, validity, future-date,
   partial-date, and mtime-invariance contracts?
2. How often does each source agree with an exact transcript record whose tool
   result says ``type=create`` for the live memory path?
3. How much of the corpus can strict immutable evidence date without guessing?
4. Which dream ``recent`` (<=8d) and ``old`` (>30d) classifications change?

The exact-create transcript record is adjudication gold.  Session start,
``originSessionId``, descriptions, git dates, and mtimes are deliberately not
treated as birth evidence: they can respectively mean a long/later session, an
event being summarized, repository assembly, or a later copy/edit.

Ship gate (fixed before changing the live resolver): >=100 gold memories, >=75%
strict gold coverage, >=90% exact-day agreement, >=98% within two days, no error
over seven days, >=75% corpus coverage, and every synthetic contract passing.

Usage:
  python eval_memory_age.py
  python eval_memory_age.py PATH_TO_MEMORY --as-of 2026-07-09

It writes one file, its own scan cache (memory_age_scan_cache.json beside it: for each
transcript the byte offset read so far, a signature of its head, and the create records
found), so a run reads only what the transcripts gained since the last one. No memory
files, sidecars, mtimes, or module sources are written.
"""
from __future__ import print_function

import argparse
import hashlib
import inspect
import io
import json
import math
import os
import re
import sys
import time
from collections import Counter
from datetime import date, datetime, time as daytime
from pathlib import Path

sys.dont_write_bytecode = True
try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - the live Python is 3.9+
    ZoneInfo = None

import memory_date as current_memory_date


HOME = Path.home()
DEFAULT_MEM = HOME / ".claude" / "projects" / "{{BRAIN_SLUG}}" / "memory"
DEFAULT_PROJECT = HOME / ".claude" / "projects" / "{{BRAIN_SLUG}}"
DEFAULT_ARCHIVE = (HOME / "brain-repo" / "11_raw_session_transcripts" /
                   "claude_projects" / "{{BRAIN_SLUG}}")
CREATED = re.compile(r"^\s*created:\s*[\"']?(20\d\d-\d\d-\d\d)", re.M)
DESCRIPTION = re.compile(r"^description:\s*(.+)$", re.M)
FULL_SLUG_DATE = re.compile(
    r"(20\d{2})[_-](0[1-9]|1[0-2])[_-]?(0[1-9]|[12]\d|3[01])(?!\d)")
FRONTMATTER = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.S)
TRANSIENT = re.compile(r"^(transient_|codex_round\d|.*round\d+.*cycle\d+.*agent|.*_loop\d+)", re.I)
DREAM_SKIP = {"MEMORY.md", "INDEX_ARCHIVE.md"}
SCAN_CACHE = HOME / ".claude" / "dreaming" / "memory_age_scan_cache.json"
_NEEDLE = b'"type":"create"'
_CHUNK = 16 << 20
_HEAD = 4096
_SAVE_SECONDS = 60.0
_DEFAULT_CACHE = object()


class Birth(object):
    __slots__ = ("day", "source", "epoch")

    def __init__(self, day, source, epoch):
        self.day = day
        self.source = source
        self.epoch = epoch


def _frontmatter(body):
    match = FRONTMATTER.search(body or "")
    return match.group(1) if match else ""


def _valid_day(value, as_of):
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed <= as_of else None


def _noon_epoch(day, tz):
    return datetime.combine(day, daytime(12, 0), tzinfo=tz).timestamp()


def strict_birth(slug, head, as_of, tz, _mtime=None):
    """Exercise the actual production trusted resolver; mtime is intentionally ignored."""
    epoch, source = current_memory_date.trusted_birth(
        slug, head, as_of=as_of, timezone=tz)
    if epoch is None:
        return Birth(None, source, None)
    return Birth(datetime.fromtimestamp(epoch, tz).date(), source, float(epoch))


def raw_evidence(row, as_of, tz):
    """Independent candidates, before any resolver precedence hides a weak source."""
    evidence = {}
    match = CREATED.search(row["frontmatter"])
    parsed = _valid_day(match.group(1), as_of) if match else None
    if parsed:
        evidence["created"] = Birth(parsed, "created", _noon_epoch(parsed, tz))
    match = FULL_SLUG_DATE.search(row["stem"])
    parsed = _valid_day("%s-%s-%s" % match.groups(), as_of) if match else None
    if parsed:
        evidence["filename"] = Birth(parsed, "filename", _noon_epoch(parsed, tz))
    description = DESCRIPTION.search(row["frontmatter"])
    match = FULL_SLUG_DATE.search(description.group(1)) if description else None
    parsed = _valid_day("%s-%s-%s" % match.groups(), as_of) if match else None
    if parsed:
        evidence["description"] = Birth(parsed, "description", _noon_epoch(parsed, tz))
    evidence["mtime"] = mtime_birth(row, tz)
    return evidence


def mtime_birth(row, tz):
    epoch = row["mtime"]
    return Birth(datetime.fromtimestamp(epoch, tz).date(), "mtime", epoch)


def permissive_birth(row, tz):
    """Call the actual sibling memory_date.py, rather than a friendly reimplementation."""
    epoch, source = current_memory_date.memory_birth(
        row["stem"], row["body"][:1600], str(row["path"]))
    return Birth(datetime.fromtimestamp(epoch, tz).date(), source, float(epoch))


def load_corpus(mem_dir):
    rows = []
    for path in sorted(mem_dir.glob("*.md"), key=lambda p: p.name.lower()):
        try:
            body = path.read_text(encoding="utf-8", errors="replace")
            mtime = path.stat().st_mtime
        except OSError:
            continue
        rows.append({
            "path": path,
            "name": path.name,
            "stem": path.stem,
            "body": body,
            "frontmatter": _frontmatter(body),
            "mtime": mtime,
            "transient": bool(TRANSIENT.search(path.name) or
                              "type: transient" in body[:400].lower()),
        })
    return rows


def transcript_paths(project_dir, archive_dir):
    """All available transcript JSONL files, in stable order, without duplicate paths."""
    candidates = list(project_dir.glob("*.jsonl")) if project_dir.is_dir() else []
    if archive_dir.is_dir():
        candidates.extend(archive_dir.rglob("*.jsonl"))
    seen = set()
    paths = []
    for path in sorted(candidates, key=lambda p: str(p).lower()):
        key = os.path.normcase(os.path.abspath(str(path)))
        if key not in seen:
            seen.add(key)
            paths.append(path)
    return paths


def _line_creates(raw, recs):
    """The per-line test of the scan up to the parts that depend on the corpus: a line whose lower-cased
    text holds all three needles and parses to a create tool result appends [file_path, stamp]."""
    low = raw.lower()
    if b"tooluseresult" not in low or _NEEDLE not in low or b"memory" not in low:
        return
    try:
        obj = json.loads(raw.decode("utf-8", "replace"))
    except (TypeError, ValueError):
        return
    if not isinstance(obj, dict):
        return
    result = obj.get("toolUseResult")
    if not isinstance(result, dict) or str(result.get("type", "")).lower() != "create":
        return
    recs.append([str(result.get("filePath") or result.get("file_path") or ""), obj.get("timestamp")])


def _scan_creates(path, e):
    """Read one transcript from where its cache entry stopped. Returns (entry, tail): the entry covers every
    complete line; a last line still being written is read on every run and never cached."""
    with io.open(path, "rb") as fh:
        size = os.fstat(fh.fileno()).st_size
        if e is not None and (e["done"] > size or e["hlen"] > size
                              or hashlib.sha256(fh.read(e["hlen"])).hexdigest()[:16] != e["head"]):
            e = None
        if e is None:
            e = {"done": 0, "hlen": 0, "head": hashlib.sha256(b"").hexdigest()[:16], "recs": []}
        recs, done = list(e["recs"]), e["done"]
        fh.seek(done)
        carry = b""
        while True:
            chunk = fh.read(_CHUNK)
            if not chunk:
                break
            buf = carry + chunk
            last = buf.rfind(b"\n")
            if last < 0:
                carry = buf
                continue
            low = buf.lower()
            pos = 0
            while True:
                i = low.find(_NEEDLE, pos, last)
                if i < 0:
                    break
                start = buf.rfind(b"\n", 0, i) + 1
                end = buf.find(b"\n", i)
                _line_creates(buf[start:end], recs)
                pos = end + 1
            done += last + 1
            carry = buf[last + 1:]
        tail = []
        if carry:
            _line_creates(carry, tail)
        hlen = min(done, _HEAD)
        if hlen != e["hlen"]:
            fh.seek(0)
            head = hashlib.sha256(fh.read(hlen)).hexdigest()[:16]
        else:
            head = e["head"]
    return {"done": done, "hlen": hlen, "head": head, "recs": recs}, tail


def _scan_key():
    h = hashlib.sha256()
    for fn in (_line_creates, _scan_creates):
        h.update(inspect.getsource(fn).encode("utf-8"))
    h.update(repr((_NEEDLE, _HEAD)).encode("utf-8"))
    return h.hexdigest()[:16]


def _load_scan_cache(path):
    try:
        with io.open(str(path), encoding="utf-8") as fh:
            blob = json.load(fh)
        return blob if isinstance(blob, dict) else {}
    except Exception:
        return {}


def _save_scan_cache(path, blob):
    """Atomic; a cache that cannot be written only costs the next run a longer read."""
    tmp = "%s.tmp.%d" % (path, os.getpid())
    try:
        with io.open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(blob, fh, separators=(",", ":"))
        os.replace(tmp, str(path))
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass


def _is_default_dirs(project_dir, archive_dir):
    def norm(p):
        return os.path.normcase(os.path.abspath(str(Path(p).expanduser().resolve())))
    return norm(project_dir) == norm(DEFAULT_PROJECT) and norm(archive_dir) == norm(DEFAULT_ARCHIVE)


def _create_records(paths, cache_path):
    """Yield the [file_path, stamp] records of each readable transcript, in path order. With a cache each
    file is read only past where the last run stopped; the cache is saved every _SAVE_SECONDS and at the end."""
    state = None
    if cache_path is not None:
        try:
            key = _scan_key()
        except (OSError, TypeError):  # no source to key on: read everything, write nothing
            key = None
        if key:
            blob = _load_scan_cache(cache_path)
            files = blob.get("files") if blob.get("key") == key else None
            state = {"key": key, "files": files if isinstance(files, dict) else {}}
    old = dict(state["files"]) if state is not None else {}
    last_save = time.time()
    keep = set()
    for path in paths:
        name = str(path)
        keep.add(name)
        e = old.get(name)
        try:
            st = os.stat(name)
            if (isinstance(e, dict) and e.get("size") == st.st_size and e.get("mtime") == st.st_mtime_ns
                    and e.get("done") == st.st_size and isinstance(e.get("recs"), list)):
                tail = []
            else:
                try:
                    e, tail = _scan_creates(name, e if isinstance(e, dict) else None)
                except (KeyError, TypeError, ValueError):  # a malformed cache entry: read the file afresh
                    e, tail = _scan_creates(name, None)
                e["size"], e["mtime"] = st.st_size, st.st_mtime_ns
                if state is not None:
                    state["files"][name] = e
                    if time.time() - last_save > _SAVE_SECONDS:
                        _save_scan_cache(cache_path, state)
                        last_save = time.time()
        except OSError:
            continue
        yield e["recs"] + tail
    if state is not None:
        state["files"] = {p: x for p, x in state["files"].items() if p in keep}
        _save_scan_cache(cache_path, state)


def exact_create_gold(rows, project_dir, archive_dir, tz, cache=_DEFAULT_CACHE):
    """Earliest exact tool-result create date for each current live-memory filename. With the default
    directories each transcript is read only past where the last run stopped (SCAN_CACHE)."""
    by_name = {row["name"]: row for row in rows}
    mem_prefix = str(rows[0]["path"].parent.resolve()).replace("\\", "/").lower().rstrip("/") \
        if rows else ""
    gold = {}
    records = 0
    paths = transcript_paths(project_dir, archive_dir)
    if cache is _DEFAULT_CACHE:
        cache = SCAN_CACHE if _is_default_dirs(project_dir, archive_dir) else None
    for recs in _create_records(paths, cache):
        for file_path, stamp in recs:
            normalized = file_path.replace("\\", "/").lower()
            if not mem_prefix or not normalized.startswith(mem_prefix + "/"):
                continue
            name = re.split(r"[\\/]", file_path)[-1]
            if name not in by_name:
                continue
            try:
                made = datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).astimezone(tz)
            except (TypeError, ValueError):
                continue
            records += 1
            made_day = made.date()
            if name not in gold or made_day < gold[name]:
                gold[name] = made_day
    return gold, records, len(paths)


def synthetic_contract(as_of, tz):
    old = 1.0
    new = 4102444800.0
    fm = lambda text: "---\n%s\n---\nbody" % text
    reference_epoch, reference_day = current_memory_date.trusted_reference(
        as_of=as_of, timezone=tz)
    reference_local = datetime.fromtimestamp(reference_epoch, tz)
    fixtures = [
        ("created beats conflicting filename",
         strict_birth("x_2026_05_01", fm("created: 2026-06-01"), as_of, tz),
         (date(2026, 6, 1), "created")),
        ("invalid created falls through to full filename",
         strict_birth("x_2026_05_01", fm("created: 2026-02-31"), as_of, tz),
         (date(2026, 5, 1), "filename")),
        ("future created falls through to full filename",
         strict_birth("x_2026_05_01", fm("created: 2099-01-01"), as_of, tz),
         (date(2026, 5, 1), "filename")),
        ("valid full filename is accepted",
         strict_birth("x_2026_05_01", "", as_of, tz),
         (date(2026, 5, 1), "filename")),
        ("full filename remains valid beyond the old 2027 ceiling",
         strict_birth("x_2028_01_02", "", date(2028, 1, 3), tz),
         (date(2028, 1, 2), "filename")),
        ("impossible full filename is unknown",
         strict_birth("x_2026_02_31", "", as_of, tz),
         (None, "unknown")),
        ("partial filename month is not silently day one",
         strict_birth("x_2026_05", "", as_of, tz),
         (None, "unknown")),
        ("description event date is not birth evidence",
         strict_birth("x", fm("description: event on 2026-05-01"), as_of, tz),
         (None, "unknown")),
        ("body created line without YAML delimiters is ignored",
         strict_birth("x", "created: 2026-05-01", as_of, tz),
         (None, "unknown")),
        ("dated memory is invariant to mtime",
         strict_birth("x_2026_05_01", "", as_of, tz, old),
         (strict_birth("x_2026_05_01", "", as_of, tz, new).day,
          strict_birth("x_2026_05_01", "", as_of, tz, new).source)),
        ("undated memory stays unknown across mtimes",
         strict_birth("x", "", as_of, tz, old),
         (strict_birth("x", "", as_of, tz, new).day,
          strict_birth("x", "", as_of, tz, new).source)),
        ("as_of seam yields one local-noon reference",
         Birth(reference_day, "reference@%02d" % reference_local.hour, reference_epoch),
         (as_of, "reference@12")),
    ]
    results = []
    for label, got, expected in fixtures:
        actual = (got.day, got.source)
        results.append((label, actual == expected, actual, expected))
    # Same-day cohorts feed recent[:30] and concern[:20]. Their order must not change when a
    # repo sync or bulk copy changes mtimes, so exercise the actual production key with opposite
    # synthetic mtime orderings (the third tuple field is deliberately never consulted).
    cohort_epoch = _noon_epoch(date(2026, 6, 1), tz)
    cohort_orders = []
    for mtimes in ((1.0, 2.0), (2.0, 1.0)):
        cohort = [("zeta.md", cohort_epoch, mtimes[0]),
                  ("alpha.md", cohort_epoch, mtimes[1])]
        cohort_orders.append(tuple(row[0] for row in sorted(
            cohort, key=lambda row: current_memory_date.trusted_recency_key(row[0], row[1]))))
    expected_orders = [("alpha.md", "zeta.md"), ("alpha.md", "zeta.md")]
    results.append(("same-day recent ordering is mtime-invariant",
                    cohort_orders == expected_orders, cohort_orders, expected_orders))
    return results


def error_stats(pairs):
    errors = sorted(abs((pred.day - truth).days) for pred, truth in pairs if pred.day is not None)
    count = len(errors)
    if not count:
        return {"n": 0, "exact": 0, "within1": 0, "within2": 0,
                "over7": 0, "p95": None, "max": None}
    p95 = errors[max(0, int(math.ceil(0.95 * count)) - 1)]
    return {
        "n": count,
        "exact": sum(err == 0 for err in errors),
        "within1": sum(err <= 1 for err in errors),
        "within2": sum(err <= 2 for err in errors),
        "over7": sum(err > 7 for err in errors),
        "p95": p95,
        "max": errors[-1],
    }


def age_flags(birth, as_of_epoch):
    if birth.epoch is None:
        return None, False, False
    age = max(0.0, (as_of_epoch - birth.epoch) / 86400.0)
    return age, age <= 8.0, age > 30.0


def pct(numerator, denominator):
    return 100.0 * numerator / denominator if denominator else 0.0


def source_text(births):
    counts = Counter(birth.source for birth in births.values())
    return ", ".join("%s=%d" % item for item in sorted(counts.items()))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("memory_dir", nargs="?", default=str(DEFAULT_MEM))
    parser.add_argument("--project-dir", default=str(DEFAULT_PROJECT))
    parser.add_argument("--archive-dir", default=str(DEFAULT_ARCHIVE))
    parser.add_argument("--as-of", help="local YYYY-MM-DD; default is today's local date")
    parser.add_argument("--timezone", default="UTC")
    args = parser.parse_args(argv)

    if ZoneInfo is None:
        print("eval_memory_age: DATA-GATED - Python zoneinfo is unavailable.")
        return 2
    try:
        tz = ZoneInfo(args.timezone)
        as_of = date.fromisoformat(args.as_of) if args.as_of else datetime.now(tz).date()
    except (KeyError, ValueError) as exc:
        print("eval_memory_age: invalid date/timezone: %s" % exc)
        return 2

    mem_dir = Path(args.memory_dir).expanduser().resolve()
    project_dir = Path(args.project_dir).expanduser().resolve()
    archive_dir = Path(args.archive_dir).expanduser().resolve()
    rows = load_corpus(mem_dir)
    if not rows:
        print("eval_memory_age: DATA-GATED - no memory markdown files in %s" % mem_dir)
        return 2

    # Keep the actual current resolver deterministic under --as-of.
    if hasattr(current_memory_date, "_TODAY"):
        current_memory_date._TODAY = as_of.isoformat()
    as_of_epoch = _noon_epoch(as_of, tz)

    policies = {"mtime-only": {}, "permissive": {}, "strict": {}}
    evidence = {}
    for row in rows:
        policies["mtime-only"][row["name"]] = mtime_birth(row, tz)
        policies["permissive"][row["name"]] = permissive_birth(row, tz)
        policies["strict"][row["name"]] = strict_birth(
            row["stem"], row["body"][:1600], as_of, tz, row["mtime"])
        evidence[row["name"]] = raw_evidence(row, as_of, tz)

    tests = synthetic_contract(as_of, tz)
    passed = sum(ok for _label, ok, _actual, _expected in tests)
    print("eval_memory_age: as_of=%s timezone=%s corpus=%d" %
          (as_of.isoformat(), args.timezone, len(rows)))
    print("\nSYNTHETIC STRICT CONTRACT: %d/%d PASS" % (passed, len(tests)))
    for label, ok, actual, expected in tests:
        print("  %s %-52s got=%s" % ("PASS" if ok else "FAIL", label, actual))
        if not ok:
            print("       expected=%s" % (expected,))

    gold, gold_records, transcript_count = exact_create_gold(
        rows, project_dir, archive_dir, tz)
    print("\nSOURCE COVERAGE")
    for name in ("mtime-only", "permissive", "strict"):
        births = policies[name]
        known = sum(b.day is not None for b in births.values())
        print("  %-12s %4d/%d known (%5.1f%%)  %s" %
              (name, known, len(rows), pct(known, len(rows)), source_text(births)))

    print("\nEXACT-CREATE TRANSCRIPT GOLD: %d memories, %d create records, %d transcript files scanned" %
          (len(gold), gold_records, transcript_count))
    policy_gold_stats = {}
    for name in ("mtime-only", "permissive", "strict"):
        pairs = [(policies[name][memory], truth) for memory, truth in sorted(gold.items())
                 if memory in policies[name] and policies[name][memory].day is not None]
        stats = error_stats(pairs)
        policy_gold_stats[name] = stats
        print("  %-12s known=%3d/%d exact=%3d (%5.1f%%) <=2d=%3d (%5.1f%%) "
              ">7d=%2d p95=%s max=%s" %
              (name, stats["n"], len(gold), stats["exact"], pct(stats["exact"], stats["n"]),
               stats["within2"], pct(stats["within2"], stats["n"]), stats["over7"],
               str(stats["p95"]), str(stats["max"])))

    print("\nRAW EVIDENCE AGAINST EXACT CREATE (independent of resolver precedence)")
    for source in ("created", "filename", "description", "mtime"):
        corpus_n = sum(source in candidates for candidates in evidence.values())
        pairs = [(evidence[memory][source], truth) for memory, truth in sorted(gold.items())
                 if memory in evidence and source in evidence[memory]]
        stats = error_stats(pairs)
        print("  %-11s corpus=%4d gold=%3d exact=%3d (%5.1f%%) <=2d=%3d "
              ">7d=%2d p95=%s max=%s" %
              (source, corpus_n, stats["n"], stats["exact"],
               pct(stats["exact"], stats["n"]), stats["within2"], stats["over7"],
               str(stats["p95"]), str(stats["max"])))

    print("\nGOLD BY DECISIVE SOURCE")
    for policy_name, wanted in (("strict", ("created", "filename")),
                                ("permissive", ("slug", "created", "descr", "mtime"))):
        for source in wanted:
            pairs = [(policies[policy_name][memory], truth)
                     for memory, truth in sorted(gold.items())
                     if memory in policies[policy_name] and
                     policies[policy_name][memory].source == source and
                     policies[policy_name][memory].day is not None]
            stats = error_stats(pairs)
            if not stats["n"]:
                continue
            print("  %-10s/%-8s n=%3d exact=%3d (%5.1f%%) <=2d=%3d >7d=%2d max=%s" %
                  (policy_name, source, stats["n"], stats["exact"],
                   pct(stats["exact"], stats["n"]), stats["within2"],
                   stats["over7"], str(stats["max"])))

    dream_rows = [row for row in rows if row["name"] not in DREAM_SKIP]
    flags = {}
    print("\nDREAM AGE COUNTERFACTUALS (recent <=8d; old >30d; unknown is neither)")
    print("  %-12s %7s %7s %11s %8s" %
          ("policy", "known", "recent", "real-recent", "old"))
    for name in ("mtime-only", "permissive", "strict"):
        per_memory = {}
        for row in dream_rows:
            per_memory[row["name"]] = age_flags(policies[name][row["name"]], as_of_epoch)
        flags[name] = per_memory
        known = sum(age is not None for age, _recent, _old in per_memory.values())
        recent = sum(recent for _age, recent, _old in per_memory.values())
        real_recent = sum(per_memory[row["name"]][1] and not row["transient"] for row in dream_rows)
        old = sum(old for _age, _recent, old in per_memory.values())
        print("  %-12s %4d/%-2d %7d %11d %8d" %
              (name, known, len(dream_rows), recent, real_recent, old))

    strict_known = {memory for memory, (age, _recent, _old) in flags["strict"].items()
                    if age is not None}
    strict_unknown = set(flags["strict"]) - strict_known
    print("\nCOUNTERFACTUAL ERRORS RELATIVE TO STRICT-KNOWN MEMORIES")
    print("  %-12s %11s %9s %14s %12s %16s %19s" %
          ("policy", "missed-old", "extra-old", "extra-recent", "missed-recent",
           "old-from-unknown", "recent-from-unknown"))
    for name in ("mtime-only", "permissive", "strict"):
        missed_old = sum(flags["strict"][m][2] and not flags[name][m][2] for m in strict_known)
        extra_old = sum(flags[name][m][2] and not flags["strict"][m][2] for m in strict_known)
        extra_recent = sum(flags[name][m][1] and not flags["strict"][m][1] for m in strict_known)
        missed_recent = sum(flags["strict"][m][1] and not flags[name][m][1] for m in strict_known)
        old_unknown = sum(flags[name][m][2] for m in strict_unknown)
        recent_unknown = sum(flags[name][m][1] for m in strict_unknown)
        print("  %-12s %11d %9d %14d %12d %16d %19d" %
              (name, missed_old, extra_old, extra_recent, missed_recent,
               old_unknown, recent_unknown))

    strict_stats = policy_gold_stats["strict"]
    strict_known_corpus = sum(b.day is not None for b in policies["strict"].values())
    gates = [
        ("all synthetic contracts pass", passed == len(tests), "%d/%d" % (passed, len(tests))),
        (">=100 exact-create gold memories", len(gold) >= 100, str(len(gold))),
        (">=75% strict gold coverage",
         strict_stats["n"] >= 0.75 * len(gold) if gold else False,
         "%.1f%%" % pct(strict_stats["n"], len(gold))),
        (">=90% strict exact-day agreement",
         strict_stats["exact"] >= 0.90 * strict_stats["n"] if strict_stats["n"] else False,
         "%.1f%%" % pct(strict_stats["exact"], strict_stats["n"])),
        (">=98% strict agreement within two days",
         strict_stats["within2"] >= 0.98 * strict_stats["n"] if strict_stats["n"] else False,
         "%.1f%%" % pct(strict_stats["within2"], strict_stats["n"])),
        ("zero strict errors over seven days", strict_stats["over7"] == 0,
         str(strict_stats["over7"])),
        (">=75% strict corpus coverage", strict_known_corpus >= 0.75 * len(rows),
         "%.1f%%" % pct(strict_known_corpus, len(rows))),
    ]
    print("\nSHIP GATES")
    for label, ok, observed in gates:
        print("  %s %-45s observed=%s" % ("PASS" if ok else "FAIL", label, observed))

    if all(ok for _label, ok, _observed in gates):
        print("\nVERDICT: SHIP-CANDIDATE (strict resolver only) - created > FULL filename date > "
              "unknown is accurate, mtime-invariant, and sufficiently covered. HOLD the existing "
              "permissive resolver: description and mtime remain untrusted birth sources.")
        return 0
    if len(gold) < 100 or strict_stats["n"] < 0.75 * len(gold):
        print("\nVERDICT: DATA-GATED - exact-create evidence or strict coverage is insufficient; "
              "do not change live age behavior.")
    else:
        print("\nVERDICT: HOLD - the strict resolver failed an accuracy or contract gate; "
              "do not change live age behavior.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
