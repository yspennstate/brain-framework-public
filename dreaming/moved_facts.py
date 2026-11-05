"""Facts that changed on a known date, read from moved_facts.json beside this file.

A memory filed BEFORE an entry's `since` whose description matches the entry's `pattern` names something that has
since changed: a deleted box or storage account, a retired law generation, the laptop GPU stop, the voice. notes()
returns (what, successor) pairs for such a memory, at most two, in registry order. A match is not guaranteed stale; notes carry checked precision
and say nothing about the rest of the memory. Used by spread_recall's association hint; brain.py recall carries
the same rule inline (_moved_flag). Built 2026-09-23 (round 6, T4). BRAIN_MOVED_FACTS=0 turns every note off.
Fail-safe: any error gives no notes."""
import datetime
import io
import json
import os
import re

_ENTRIES = None
_PRECISION = {}


def entries():
    """[(compiled pattern, since date, what, successor)], read once per process; [] when absent or unreadable."""
    global _ENTRIES
    if _ENTRIES is None:
        _ENTRIES = []
        _PRECISION.clear()
        try:
            with io.open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "moved_facts.json"),
                         encoding="utf-8") as handle:
                for e in json.load(handle).get("entries", []):
                    if e.get("retired"):
                        continue
                    audit = e.get("precision", {})
                    k, n = audit.get("true"), audit.get("sampled")
                    if type(k) is int and type(n) is int and n > 0 and 0 <= k <= n:
                        _PRECISION[(e["pattern"], e["since"], e["successor"])] = (k, n)
                    _ENTRIES.append((re.compile(e["pattern"], re.I), datetime.date.fromisoformat(e["since"]),
                                     e["what"].replace("[", "(").replace("]", ")"), e["successor"]))
        except Exception:
            _ENTRIES = []
    return _ENTRIES


def filed(slug, front):
    """The filing day: archive record created/first_upload, else slug suffix, else frontmatter created; None if absent."""
    # Archive-location records inherit the source folder's date in their slug.
    # Use the record's creation date, or its explicit first-upload date.
    if (slug or "").startswith("data_on_github_"):
        created = re.search(r"^\s*created:\s*['\"]?(20\d\d)-(\d\d)-(\d\d)", front or "", re.M)
        if not created:
            created = re.search(r"^\s*first_upload:\s*['\"]?(20\d\d)-(\d\d)-(\d\d)", front or "", re.M)
        if created:
            try:
                return datetime.date(*(int(created.group(i)) for i in (1, 2, 3)))
            except ValueError:
                pass
    m = re.search(r"(20\d\d)_(\d\d)_(\d\d)$", slug or "")
    if not m:
        m = re.search(r"^\s*created:\s*['\"]?(20\d\d)-(\d\d)-(\d\d)", front or "", re.M)
    if not m:
        return None
    try:
        return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def notes(slug, front, resolve=None, skip=()):
    """[(what, successor)] for a memory whose frontmatter block is `front`. `resolve` maps a successor slug to the end
    of its own supersession chain; a successor named in `skip` (already shown by a part link) is left out."""
    if os.environ.get("BRAIN_MOVED_FACTS", "1") == "0":
        return []
    try:
        day = filed(slug, front)
        d = re.search(r"^description:\s*(.*)$", front or "", re.M)
        desc = d.group(1) if d else ""
        if day is None or not desc:
            return []
        out = []
        for rx, since, what, successor in entries():
            if day >= since or not rx.search(desc):
                continue
            cur = resolve(successor) if resolve else successor
            if slug in (cur, successor) or cur in skip or any(cur == s for _, s in out):
                continue
            checked = _PRECISION.get((rx.pattern, since.isoformat(), successor))
            suffix = ("; right %d of %d times when checked" % checked
                      if checked else "; precision not checked")
            out.append((what + suffix, cur))
            if len(out) == 2:
                break
        return out
    except Exception:
        return []
