#!/usr/bin/env python3
"""memory_date.py - birth-date resolvers for age-based memory consumers.

mtime LIES: any robocopy / git restore / repo assembly resets it to ~now, so an April memory can
look days old (measured 2026-07-09: 181 memories off by >30 days, max 95). Age-based ranking
(salience recency, reconsolidation resistance) must not trust it. Tiers, most reliable first:
  1) DATE in the filename slug  (the authoring convention: ..._2026_06_27.md)  - ground truth
  2) created: frontmatter        (brain.py stamps new ones; backfilled from transcripts 2026-07-09)
  3) DATE leading the description (older undated memories lead the description with the date)
  4) file mtime                  (last resort - the one that lies; flagged by returning is_mtime=True)

salience.py carries its own copy of tiers 1/2/3 (a live hook - not refactored to import this, to
avoid a hot-path dependency); this module is the canonical version for every OTHER consumer.

``memory_birth`` / ``age_days`` retain that permissive API unchanged for existing consumers.
``trusted_birth`` is the gated immutable path: valid created frontmatter, then a valid FULL
filename date, otherwise unknown. It never substitutes an event description or mutable mtime.
"""
import datetime as _dt
import os, io, re, time

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - live Python is 3.9+
    ZoneInfo = None

_SLUG_DATE = re.compile(r"(202[4-7])[_-](0[1-9]|1[0-2])[_-]?(\d{2})?")
_CREATED = re.compile(r"^\s*created:\s*[\"']?(\d{4})-(\d{2})-(\d{2})", re.M)
_TRUSTED_CREATED = re.compile(
    r"^\s*created:\s*[\"']?(\d{4})-(\d{2})-(\d{2})(?!\d)", re.M)
_DESC = re.compile(r"^description:\s*(.+)$", re.M)
_TRUSTED_FULL_SLUG_DATE = re.compile(
    r"(20\d{2})[_-](0[1-9]|1[0-2])[_-]?(0[1-9]|[12]\d|3[01])(?!\d)")
_FRONTMATTER = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.S)
_TODAY = None


def _today():
    global _TODAY
    if _TODAY is None:
        _TODAY = time.strftime("%Y-%m-%d")
    return _TODAY


def _epoch(y, mo, d):
    return time.mktime((int(y), int(mo), int(d or 1), 12, 0, 0, 0, 0, -1))


def _trusted_timezone(timezone):
    if timezone is None:
        return _dt.datetime.now().astimezone().tzinfo
    if isinstance(timezone, str):
        if ZoneInfo is None:
            raise ValueError("named timezones require Python zoneinfo")
        return ZoneInfo(timezone)
    return timezone


def _trusted_as_of(as_of, timezone):
    if as_of is None:
        return _dt.datetime.now(timezone).date()
    if isinstance(as_of, _dt.datetime):
        if as_of.tzinfo is None:
            return as_of.date()
        return as_of.astimezone(timezone).date()
    if isinstance(as_of, _dt.date):
        return as_of
    return _dt.date.fromisoformat(str(as_of))


def _trusted_frontmatter(head):
    text = head or ""
    match = _FRONTMATTER.search(text)
    return match.group(1) if match else ""


def trusted_reference(as_of=None, timezone=None):
    """Return one ``(epoch, local_day)`` clock for a deterministic age-selection pass."""
    tz = _trusted_timezone(timezone)
    if as_of is None:
        current = _dt.datetime.now(tz)
        return current.timestamp(), current.date()
    cutoff = _trusted_as_of(as_of, tz)
    reference = _dt.datetime.combine(cutoff, _dt.time(12, 0), tzinfo=tz)
    return reference.timestamp(), cutoff


def trusted_recency_key(slug, birth_epoch):
    """Newest trusted birth first, with a stable name tie-break and no mtime dependence."""
    return (birth_epoch is None, -float(birth_epoch or 0.0), (slug or "").casefold())


def trusted_birth(slug, head, as_of=None, timezone=None):
    """Return ``(epoch, source)`` from immutable evidence, or ``(None, 'unknown')``.

    ``head`` is the raw file head, including YAML delimiters. Precedence is a valid
    ``created:`` inside that frontmatter, then a valid FULL date in the
    filename slug. ``as_of`` accepts an ISO day/date/datetime and ``timezone`` accepts
    a ZoneInfo name or tzinfo, making fixtures and replays deterministic. Partial dates,
    impossible/future days, descriptions, and mtimes are never promoted to birth.
    """
    tz = _trusted_timezone(timezone)
    cutoff = _trusted_as_of(as_of, tz)
    frontmatter = _trusted_frontmatter(head)

    match = _TRUSTED_CREATED.search(frontmatter)
    if match:
        try:
            made = _dt.date(*(int(part) for part in match.groups()))
        except ValueError:
            made = None
        if made is not None and made <= cutoff:
            epoch = _dt.datetime.combine(made, _dt.time(12, 0), tzinfo=tz).timestamp()
            return epoch, "created"

    match = _TRUSTED_FULL_SLUG_DATE.search(slug or "")
    if match:
        try:
            made = _dt.date(*(int(part) for part in match.groups()))
        except ValueError:
            made = None
        if made is not None and made <= cutoff:
            epoch = _dt.datetime.combine(made, _dt.time(12, 0), tzinfo=tz).timestamp()
            return epoch, "filename"
    return None, "unknown"


def memory_birth(slug, head, path=None):
    """Return (epoch_seconds, source) for a memory's birth. `head` = the file's first ~1500 chars.
    source in {'slug','created','descr','mtime'}. Never returns a future date from tiers 1-3."""
    today = _today()
    # 1) slug date
    m = _SLUG_DATE.search(slug)
    if m and "%s-%s-%s" % (m.group(1), m.group(2), m.group(3) or "01") <= today:
        return _epoch(m.group(1), m.group(2), m.group(3)), "slug"
    # 2) created: frontmatter
    m = _CREATED.search(head or "")
    if m and "%s-%s-%s" % m.groups() <= today:
        return _epoch(*m.groups()), "created"
    # 3) date leading the description
    dm = _DESC.search(head or "")
    if dm:
        m = _SLUG_DATE.search(dm.group(1))
        if m and "%s-%s-%s" % (m.group(1), m.group(2), m.group(3) or "01") <= today:
            return _epoch(m.group(1), m.group(2), m.group(3)), "descr"
    # 4) mtime (lies)
    if path and os.path.exists(path):
        return os.path.getmtime(path), "mtime"
    return time.time(), "mtime"


def age_days(slug, head, path=None, now=None):
    e, src = memory_birth(slug, head, path)
    return max(0.0, ((now or time.time()) - e) / 86400.0), src
