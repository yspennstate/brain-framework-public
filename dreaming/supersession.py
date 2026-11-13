#!/usr/bin/env python3
"""Shared, chain-safe supersession routing for active brain consumers.

Historical memory files and graph nodes remain intact.  Active readers call this
module to resolve an old slug to the terminal correction only when that endpoint
exists.  Broken targets and cycles fail open to the original slug.
"""
import argparse
import io
import os
import re
import sys


HERE = os.path.dirname(os.path.abspath(__file__))
SLUG_RX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
FRONTMATTER_RX = re.compile(r"\A(?:\ufeff)?---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|$)", re.S)
FM_SUPERSEDED_RX = re.compile(r"^[ \t]*superseded_by:[ \t]*([A-Za-z0-9][A-Za-z0-9_-]*)[ \t]*$", re.M)
# A pointer field is machine-read and the pattern above demands the value be ONE slug and nothing else, so a
# declaration written as prose is dropped in silence and the stale memory keeps being served as current.
# Measured on the live corpus 2026-09-22: of 425 declarations, 12 do not route - 7 of them prose, and 3 of
# those 7 open with a real slug followed by a comment ("some_slug_... # only its THIRD section is wrong")
# or a second correction ("exit8_peak_..., beta_window_..."). This pattern recovers that leading slug. A
# route from it is admitted ONLY when the named file exists (see _lenient_target), so it can recover a link
# to a real memory and can never invent one: "VOID same evening - see correction block" yields the
# slug-shaped token VOID, which names no file and is refused.
FM_SUPERSEDED_LEAD_RX = re.compile(
    r"^[ \t]*superseded_by:[ \t]*[\"']?([A-Za-z0-9][A-Za-z0-9_-]*)[\"']?[ \t]*(?:[,#]|[ \t]|$)", re.M)
INDEX_ENTRY_RX = re.compile(r"^- \[([^\]]+?)\.md\]")
FM_DESC_RX = re.compile(r'^description:[ \t]*["\']?(.+?)["\']?[ \t]*$', re.M)
FM_STATUS_RX = re.compile(r"^[ \t]*status:[ \t]*([A-Za-z-]+)[ \t]*$", re.M)


def _read(path, limit=None):
    try:
        with io.open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read() if limit is None else fh.read(limit)
    except Exception:
        return ""


def _frontmatter_target(body):
    match = FRONTMATTER_RX.search(body or "")
    if not match:
        return ""
    target = FM_SUPERSEDED_RX.search(match.group(1))
    return target.group(1) if target else ""


def _lenient_target(body, memory_dir):
    """The leading slug of a prose pointer - returned only when the memory it names exists on disk.

    The existence test is what makes this safe to read at all: the lenient pattern happily matches the first
    word of a sentence, so the gate, not the pattern, decides.  A stat per pointer on ~420 pointers is
    immaterial beside the scandir this loop already pays for.
    """
    slug = _lenient_candidate(body)
    return slug if slug and os.path.isfile(os.path.join(memory_dir, slug + ".md")) else ""


def _lenient_candidate(body):
    """The prose pointer's leading slug before the existence test: what the frontmatter cache keeps, so the test is
    made again on every call against the current directory (28 Sep, interactive audit IA-20260928-04: the cache kept
    the tested result, and a pointer whose target appeared after the predecessor was cached stayed unrouted until the
    predecessor itself changed)."""
    match = FRONTMATTER_RX.search(body or "")
    if not match:
        return ""
    lead = FM_SUPERSEDED_LEAD_RX.search(match.group(1))
    if not lead or not SLUG_RX.fullmatch(lead.group(1)):
        return ""
    # A '#' COMMENT ON A POINTER NARROWS IT, so such a value is not a plain route. Measured on the live
    # corpus 2026-09-22, the split is clean: both commented pointers are PARTIAL supersessions - "only its
    # THIRD section (the cloud-hash claim) is wrong; the sidecar-backlog findings stand", and "one feed-name part
    # only - it is a DOCUMENTED hub alias, not a dead feed" - while both comma-separated values are lists of
    # several full corrections. Admitting a partial as a full route would rewrite the whole memory to its
    # correction and throw away the sections its own author said still stand, which is worse than the
    # fail-open it replaces: it overrides the author instead of merely ignoring them. Caught by reading the
    # seven values rather than the count, the same way the arm-H extractor's four bugs were caught.
    if "#" in (lead.string[lead.start():] or ""):
        line_tail = lead.string[lead.end(1):].split("\n", 1)[0]
        if "#" in line_tail:
            return ""
    # ANY QUALIFIER NARROWS IT, not only a '#' comment (4 Oct 2026): "slug (the half-real part only, ...; the
    # 75-percent picture count stands)" routed the whole memory to its successor. See pointer_scope.
    line = lead.string[lead.start():].split("\n", 1)[0]
    kind, slugs, _qualifier = pointer_scope(line.split(":", 1)[1] if ":" in line else "")
    return slugs[0] if kind == "whole" and slugs and slugs[0] == lead.group(1) else ""


def pointer_scope(value):
    """What one superseded_by VALUE declares - the classification every reader of the field shares.

    ('whole', [slug, ...], '')  one slug, quoted or not, or a comma list of slugs (several full corrections);
    ('part', [slug], qualifier) a slug followed by anything else - a '#' comment, a parenthesis, prose. The author
                                narrowed the pointer to part of the memory, so no reader may treat it as a replacement
                                of the whole: the route map does not route it, the recall hint says PARTLY SUPERSEDED
                                and names the qualifier, the live sidecar records no whole successor;
    ('none', [], '')            the value opens with no slug.
    Added 4 Oct 2026 on dot 125873D4's static finding (spread_recall's hint cut the first token of a qualified
    pointer and told the reader to read the successor instead). Three readers had each cut the first token their own
    way - the lenient route here (which caught only '#'), the hint and live_sidecar - and one live memory read
    'read that one instead' while its own frontmatter said one of its two rules still stands."""
    v = " ".join((value or "").split())
    if len(v) >= 2 and v[0] in "\"'" and v[-1] == v[0]:
        v = v[1:-1].strip()
    m = re.match(r"[\"']?([A-Za-z0-9][A-Za-z0-9_-]*?)(?:\.md)?[\"']?(?=$|[\s,#(])", v)
    if not m:
        return ("none", [], "")
    first, rest = m.group(1), v[m.end():].strip()
    if not rest:
        return ("whole", [first], "")
    if rest.startswith(","):
        slugs = [first]
        for token in rest[1:].split(","):
            token = token.strip().strip("\"'")
            token = token[:-3] if token.endswith(".md") else token
            if not SLUG_RX.fullmatch(token):
                return ("part", [first], rest[1:].strip())
            slugs.append(token)
        return ("whole", slugs, "")
    qualifier = rest[1:].strip() if rest.startswith("#") else rest
    if qualifier.startswith("(") and qualifier.endswith(")"):
        qualifier = qualifier[1:-1].strip()
    return ("part", [first], qualifier)


def _read_frontmatter(path, max_chars=1024 * 1024):
    """Read only the leading YAML block, including long/nested metadata."""
    try:
        with io.open(path, "r", encoding="utf-8", errors="replace") as fh:
            first = fh.readline()
            if first.lstrip("\ufeff").strip() != "---":
                return ""
            lines = ["---\n"]
            size = len(first)
            for line in fh:
                size += len(line)
                if size > max_chars:
                    return ""
                lines.append(line)
                if line.strip() == "---":
                    return "".join(lines)
    except Exception:
        pass
    return ""


def frontmatter_block(path):
    """The inside of a memory's closed leading YAML block, read to its closing line - '' when absent/unclosed.

    Readers that take a fixed prefix (read(4096), read(8000)) see a severed block as a short one; this one
    stops at the closing '---' however long the description runs (bounded at 1 MB by _read_frontmatter)."""
    match = FRONTMATTER_RX.search(_read_frontmatter(path) or "")
    return match.group(1) if match else ""


# FILING STAMPS (2026-09-22, round 5 W4; F54). `created:` carries a DATE, so two orders filed the same day cannot
# be put in order: all nine inverted verdicts of the 200-pair owner-order judge had this one cause (three 09-02
# scheduling laws collided that way), and fifteen pairs were one order filed two to four times in a day.
# `remember` now writes `created_at:` (local ISO time with its offset, to the second) beside `created:`; a reader
# that ORDERS memories uses created_at when both sides carry it and the date otherwise, and says so when the date
# cannot decide. `modified:` (the native memory tool's field) is deliberately NOT a filing stamp: it is the last
# EDIT, in UTC - a memory filed on one day can read modified two days later.
FM_CREATED_AT_RX = re.compile(
    r"^[ \t]*created_at:[ \t]*[\"']?(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?"
    r"(?:Z|[+-]\d{2}:?\d{2})?)[\"']?[ \t]*$", re.M)
FM_CREATED_RX = re.compile(r"^[ \t]*created:[ \t]*[\"']?(\d{4}-\d{2}-\d{2})(?!\d)", re.M)


def _iso_epoch(text):
    """Epoch seconds for an ISO time; a missing offset reads as local time. None when it does not parse."""
    import datetime as _dt
    try:
        value = text.strip()
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        stamp = _dt.datetime.fromisoformat(value)
        if stamp.tzinfo is None:
            stamp = stamp.astimezone()          # naive = this machine's local time
        return stamp.timestamp()
    except Exception:
        return None


def memory_stamp(front):
    """{'day': 'YYYY-MM-DD' or '', 'at': epoch or None, 'text': what to show} from a frontmatter block.

    created_at wins when it parses; its day is its own local date (the writer derives both from one clock
    reading, so they agree). A memory with neither field has day '' and at None - it cannot be ordered."""
    front = front or ""
    at, day, text = None, "", ""
    m = FM_CREATED_AT_RX.search(front)
    if m:
        at = _iso_epoch(m.group(1))
        if at is not None:
            text = m.group(1)
            day = m.group(1)[:10]
    if not day:
        m = FM_CREATED_RX.search(front)
        if m:
            day = text = m.group(1)
    return {"day": day, "at": at, "text": text}


def order_stamps(a, b):
    """-1 when stamp a was filed before b, 1 when after, 0 when the stamps cannot say (no stamp, or the same
    day with a time missing on either side). Two exact times are compared exactly; otherwise only days are."""
    if a.get("at") is not None and b.get("at") is not None:
        return (a["at"] > b["at"]) - (a["at"] < b["at"])
    if a.get("day") and b.get("day") and a["day"] != b["day"]:
        return -1 if a["day"] < b["day"] else 1
    return 0


# PART SUPERSESSION (2026-09-22, round 5 W4; F56 item 3). `superseded_by` is per NODE: a two-part
# law can be overtaken for one part and still be the law for the other, and a reader who follows the node stamp is
# routed to the new law and loses the half that still stands. The recall collapse is right to substitute a wholly overtaken memory; the
# 08-22 measurement (partial_supersession_makes_the_valid_half_unreachable_2026_08_22) showed what it costs when
# the overtaking was partial - predecessor at lexical rank 1 in 16/16, returned by full recall in 1/22 - and
# concluded that the repair belongs in the DATA, one memory at a time, not in a prose-reading collapse.
# `superseded_in_part:` is that data: a flow list of items `<clause in a few words> -> <successor slug>`. A memory
# carrying it is NOT routed anywhere (the resolver and the ledger never read it); readers FLAG it instead - recall
# prints `partly superseded: <clause> -> <slug>`, the association hint says which clause moved and that the rest
# stands, and a reflex compiled from it gets a hint line rather than a halved charge. One physical line, so every
# line-anchored reader in this codebase (superseded_by / status / created regexes) is untouched by it; valid YAML.
#     superseded_in_part: ["the weekly review day -> review_moves_to_thursdays_2026_01_01"]
# The corpus already holds the idea in two earlier spellings, both read here so they stop being invisible:
# `superseded_in_part: "<prose>"` (3 memories, an in-place correction with no successor slug) and
# `partially_superseded_by: <slug>  # <which part>` (2 memories). Neither is routed, as before.
FM_PART_RX = re.compile(
    r"^([ \t]*)(superseded_in_part|partially_superseded_by)[ \t]*:[ \t]*(.*?)[ \t]*$", re.M)
PART_ARROW = "->"
PART_CLAUSE_MAX = 160


def _flow_items(value):
    """Items of a one-line YAML flow sequence of scalars ('[\"a\", 'b', c]'); None when it is not one."""
    value = (value or "").strip()
    if not (value.startswith("[") and value.endswith("]")):
        return None
    body, items, i, n = value[1:-1], [], 0, len(value) - 2
    while i < n:
        ch = body[i]
        if ch in " \t,":
            i += 1
            continue
        if ch == '"':
            j, out = i + 1, []
            while j < n and body[j] != '"':
                if body[j] == "\\" and j + 1 < n:
                    out.append(body[j + 1])
                    j += 2
                    continue
                out.append(body[j])
                j += 1
            items.append("".join(out))
            i = j + 1
        elif ch == "'":
            j, out = i + 1, []
            while j < n:
                if body[j] == "'":
                    if j + 1 < n and body[j + 1] == "'":
                        out.append("'")
                        j += 2
                        continue
                    break
                out.append(body[j])
                j += 1
            items.append("".join(out))
            i = j + 1
        else:
            j = body.find(",", i)
            j = n if j < 0 else j
            items.append(body[i:j].strip())
            i = j + 1
    return [it for it in items if it.strip()]


def _scalar(value):
    """A one-line YAML scalar without its quotes or a trailing ' # comment' (plain scalars only)."""
    value = (value or "").strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        inner = value[1:-1]
        return inner.replace("''", "'") if value[0] == "'" else inner.replace('\\"', '"').replace("\\\\", "\\")
    return re.split(r"[ \t]+#", value, 1)[0].strip()


def _split_item(item, memory_dir=None):
    """'clause -> slug' -> (clause, slug); an item whose tail is not an existing slug stays whole, successor ''."""
    item = " ".join((item or "").split())
    if PART_ARROW in item:
        clause, target = item.rsplit(PART_ARROW, 1)
        target = target.strip().strip("\"'`[]().,;")
        if target.endswith(".md"):
            target = target[:-3]
        if SLUG_RX.fullmatch(target) and (
                memory_dir is None or os.path.isfile(os.path.join(memory_dir, target + ".md"))):
            return clause.strip(), target
    return item, ""


def part_supersessions(front, memory_dir=None):
    """[(clause, successor), ...] declared in a frontmatter block; successor '' = corrected in its own body.

    memory_dir, when given, admits a successor only if its file exists - a typo'd slug then reads as prose
    rather than as a pointer at nothing (the dangling-pointer class, write-time refused by brain.py)."""
    out = []
    lines = (front or "").splitlines()
    for k, line in enumerate(lines):
        m = FM_PART_RX.match(line)
        if not m:
            continue
        indent, field, value = m.group(1), m.group(2), m.group(3)
        if field == "partially_superseded_by":
            # `<slug>  # <which part>` - the comment is the clause; the slug routes nothing
            lead = re.match(r"[\"']?([A-Za-z0-9][A-Za-z0-9_-]*)[\"']?[ \t]*(?:#[ \t]*(.*))?$", value.strip())
            if lead:
                slug = lead.group(1)
                if memory_dir is None or os.path.isfile(os.path.join(memory_dir, slug + ".md")):
                    out.append((" ".join((lead.group(2) or "part of it").split()), slug))
                    continue
            if value.strip():
                out.append((_scalar(value), ""))
            continue
        items = _flow_items(value)
        if items is None:
            if value.strip():
                items = [_scalar(value)]
            else:
                # a hand-written block sequence under the key: the more-indented '- item' lines below it
                items = []
                for nxt in lines[k + 1:]:
                    bm = re.match(r"^([ \t]*)-[ \t]+(.*?)[ \t]*$", nxt)
                    if not bm or len(bm.group(1)) < len(indent):
                        break
                    items.append(_scalar(bm.group(2)))
        for it in items:
            if it.strip():
                out.append(_split_item(it, memory_dir))
    return out


def read_part_supersessions(memory_dir, slug):
    """part_supersessions of one memory, successors existence-checked; [] on any error (fail-open)."""
    try:
        return part_supersessions(frontmatter_block(os.path.join(memory_dir, slug + ".md")), memory_dir)
    except Exception:
        return []


def clean_part_clause(clause):
    """One line, no double quotes or backslashes (the item is written as a double-quoted YAML scalar)."""
    clause = " ".join((clause or "").replace("\\", "/").replace('"', "'").split())
    return clause[:PART_CLAUSE_MAX].rstrip()


def _render_part_items(items):
    """A one-line flow list of double-quoted scalars; an earlier hand-written item is escaped, not rewritten."""
    def q(text):
        return '"%s"' % text.replace("\\", "\\\\").replace('"', '\\"')
    return "[" + ", ".join(q(("%s %s %s" % (c, PART_ARROW, s)) if s else c) for c, s in items) + "]"


def stamp_part(body, clause, successor):
    """Return the memory text with `<clause> -> <successor>` added to its `superseded_in_part:` list.

    Idempotent (the same clause and successor twice is one item); an existing list - or an earlier one-item prose
    value - is extended IN PLACE, keeping its indent; otherwise the field goes at the end of the frontmatter at the
    top level, where `_stamp_superseded` puts `superseded_by`. Raises ValueError when there is no frontmatter or the
    clause is empty. Never touches `superseded_by`, the ledger, or anything outside the frontmatter."""
    clause = clean_part_clause(clause)
    if not clause:
        raise ValueError("the clause that moved must be named (a few words)")
    if not SLUG_RX.fullmatch(successor or ""):
        raise ValueError("successor must be ONE slug, not %r" % (successor,))
    fm = re.match(r"^(\ufeff?---[ \t]*\r?\n)(.*?\r?\n)(---[ \t]*(?:\r?\n|$))", body or "", re.S)
    if not fm:
        raise ValueError("the memory has no frontmatter block")
    eol = "\r\n" if "\r\n" in fm.group(0) else "\n"
    front = fm.group(2)
    lines = front.splitlines(True)
    for k, line in enumerate(lines):
        m = FM_PART_RX.match(line.rstrip("\r\n"))
        if not m or m.group(2) != "superseded_in_part":
            continue
        indent, value = m.group(1), m.group(3)
        existing = _flow_items(value)
        span = 1
        if existing is None:
            if value.strip():
                existing = [_scalar(value)]
            else:
                existing = []
                for nxt in lines[k + 1:]:
                    bm = re.match(r"^([ \t]*)-[ \t]+(.*?)[ \t]*$", nxt.rstrip("\r\n"))
                    if not bm or len(bm.group(1)) < len(indent):
                        break
                    existing.append(_scalar(bm.group(2)))
                    span += 1
        pairs = [_split_item(it) for it in existing if it.strip()]
        key = (clause.lower(), successor)
        if any((c.lower(), s) == key for c, s in pairs):
            return body
        pairs.append((clause, successor))
        lines[k:k + span] = [indent + "superseded_in_part: " + _render_part_items(pairs) + eol]
        return fm.group(1) + "".join(lines) + fm.group(3) + body[fm.end():]
    new_line = "superseded_in_part: " + _render_part_items([(clause, successor)]) + eol
    return fm.group(1) + front + new_line + fm.group(3) + body[fm.end():]


def partial_successors(front, memory_dir=None):
    """The successor slugs a frontmatter block declares as PARTIAL (for writers that must not make them whole)."""
    return {s for _c, s in part_supersessions(front, memory_dir) if s}


def _default_index():
    return os.environ.get("BRAIN_SUPERSEDED_INDEX") or os.path.join(HERE, "superseded.tsv")


def load_map(memory_dir, index_path=None, include_frontmatter=False):
    """Load old->correction routes.  Frontmatter, when requested, is authoritative."""
    routes = {}
    path = _default_index() if index_path is None else index_path
    try:
        with io.open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                parts = line.rstrip("\n").split("\t")
                if len(parts) < 2:
                    continue
                old, correction = parts[0].strip(), parts[1].strip()
                if old == "old" or not SLUG_RX.fullmatch(old) or not SLUG_RX.fullmatch(correction):
                    continue
                routes[old] = correction
    except Exception:
        pass

    if include_frontmatter:
        for name, target in _frontmatter_targets(memory_dir).items():
            routes[name] = target
    return routes


# 2026-09-11: every deliberate recall (and every consumer asking for frontmatter authority) re-read the
# leading YAML of all 15,875 memory files - 2.5 s per call on this machine, the largest fixed cost in
# brain.py recall after the embedding load. The targets change only when a file changes, so they are
# cached beside the index keyed by (size, mtime_ns); a run re-reads only new or modified files (a
# scandir over the directory costs 0.15 s). The cache is a convenience, never an authority: a missing,
# unreadable or malformed cache is simply rebuilt from the files, and the returned map is identical to
# the uncached one by construction (verified on the live corpus at landing: 389 routes, dict-equal).
_FM_CACHE_NAME = "supersession_frontmatter_cache.json"
# Bump this whenever the PARSE changes, or the fix does not reach the corpus: a cache entry is keyed by
# (size, mtime_ns), so every unchanged file would keep serving the empty target the old parse recorded.
# 3 (28 Sep): an entry holds the strict target and the prose pointer's candidate apart, and the candidate's target is
# looked for in this call's own directory listing, never taken from the cache (IA-20260928-04).
# 4 (29 Sep): an entry is keyed by size, mtime, ctime and birth time,
# so a file replaced by rename with its old size and mtime is read again (Windows gives the renamed file a new creation
# time; POSIX changes ctime on any write or utime). The one case still read from the cache is an in-place write of the
# same size whose mtime is then set back to the same 100 ns on Windows; no writer here does that. Targets resolve
# through the folder's own file names, so a directory named *.md is never a memory, and a target renamed only in case
# keeps its route where the file system ignores case (normcase); where it does not, the spellings stay distinct.
_FM_CACHE_VERSION = 5   # 5 (4 Oct 2026): a qualified pointer no longer routes - every cached candidate is re-read


def _md_files(memory_dir):
    """The folder's memory files: .md names that are FILES (a directory named *.md is not a memory; a file symlink is),
    as DirEntry objects. One enumeration for every reader here, so the targets, the known slugs and the audit agree.
    [] when the folder cannot be read."""
    try:
        with os.scandir(memory_dir) as it:
            return [e for e in it if e.name.endswith(".md") and e.is_file()]
    except Exception:
        return []


def _canonical_names(entries):
    """normcase(slug) -> the slug as the file is spelled. On Windows normcase folds case, so a pointer that names
    Target_Correction reaches target_correction.md; on POSIX it is the identity."""
    return {os.path.normcase(e.name[:-3]): e.name[:-3] for e in entries}


def _stat_key(st):
    return [st.st_size, st.st_mtime_ns, st.st_ctime_ns, getattr(st, "st_birthtime_ns", 0) or 0]


def _fm_cache_path(memory_dir):
    """One cache file per memory folder (29 Oct 2026). The single shared file held one folder at a time, so a recall
    against the repo mirror's memory folder made the next live recall re-read all 28k files: 70-282 s on the loaded
    Mac, and other agents' recalls queued 5-13 min behind it."""
    import hashlib
    tag = hashlib.sha256(os.path.normcase(os.path.abspath(memory_dir)).encode("utf-8")).hexdigest()[:12]
    # BRAIN_SUPERSESSION_CACHE_DIR moves the cache out of the code folder (tests point it at a temp dir)
    return os.path.join(os.environ.get("BRAIN_SUPERSESSION_CACHE_DIR") or HERE,
                        "supersession_frontmatter_cache.%s.json" % tag)


# What the last _frontmatter_targets call read: memory_dir and pointers, file name -> (strict, candidate) for every file
# that has either. brain.py's recall keeps it beside a cached map to tell whether a later folder state can change it.
LAST_POINTERS = {}


def _frontmatter_targets(memory_dir):
    import json
    cache_path = _fm_cache_path(memory_dir)
    cached = {}
    try:
        try:
            fh = io.open(cache_path, encoding="utf-8")
        except FileNotFoundError:   # first use: seed from the old shared file when it holds this folder
            fh = io.open(os.path.join(HERE, _FM_CACHE_NAME), encoding="utf-8")
        with fh:
            data = json.load(fh)
        if isinstance(data, dict) and data.get("memory_dir") == os.path.abspath(memory_dir) \
                and data.get("version") == _FM_CACHE_VERSION \
                and isinstance(data.get("files"), dict):
            cached = data["files"]
        elif isinstance(data, dict) and data.get("memory_dir") == os.path.abspath(memory_dir) \
                and data.get("version") == 4 and isinstance(data.get("files"), dict):
            # 4 -> 5 changed only the lenient candidate, so only entries that cached one are re-read (1 of 18,047 on
            # 4 Oct): a whole re-read is 18k cold opens, minutes inside a hook's timeout on a loaded laptop
            cached = {k: v for k, v in data["files"].items()
                      if not (isinstance(v, list) and len(v) == 6 and v[5])}
    except Exception:
        cached = {}
    files, out, dirty, stale = {}, {}, False, 0
    pointers = {}
    LAST_POINTERS.clear()
    import time as _time
    now_ns = _time.time_ns()
    entries = _md_files(memory_dir)
    canon = _canonical_names(entries)
    for entry in entries:
        name = entry.name
        try:
            key = _stat_key(entry.stat())
        except Exception:
            continue
        prev = cached.get(name)
        # a file written in the last few seconds is always re-read: a rewrite of the same size inside one
        # mtime tick (tests do this; a fast agent could) must not inherit the previous content's target
        recent = (now_ns - key[1]) < 5_000_000_000
        if not recent and isinstance(prev, list) and len(prev) == 6 and prev[:4] == key:
            strict, candidate = prev[4], prev[5]
        else:
            head = _read_frontmatter(entry.path)
            strict = _frontmatter_target(head)
            candidate = "" if strict else _lenient_candidate(head)
            # A re-read that parses to what the cache already holds changed only its stamp. CONSCIENCE.md is
            # rewritten by a Stop hook on every turn, so counting that as dirty rewrote this 3.4 MB cache on
            # every recall (30 Oct). Such a file is re-read next call instead; the map returned is the same.
            # A new file waits the same way: agents file a memory every few minutes, so writing on each one
            # still rewrote the cache on most recalls (2.6 s of a 15 s recall at load 48).
            if prev is None or (isinstance(prev, list) and len(prev) == 6 and prev[4:] == [strict, candidate]):
                stale += 1
            else:
                dirty = True
        files[name] = key + [strict, candidate]
        if strict or candidate:
            pointers[name] = (strict, candidate)
        # a prose pointer routes only while its target exists, judged against this listing on every call; a target is
        # spelled as its file is, so the resolver's exact-name check finds it
        if strict:
            target = canon.get(os.path.normcase(strict), strict)
        else:
            target = canon.get(os.path.normcase(candidate), "") if candidate else ""
        if target and SLUG_RX.fullmatch(target):
            out[name[:-3]] = target
    # a deleted file's leftover entry is never looked up, so removals alone do not force a write
    if dirty or stale > 64 or (files and not cached):
        try:
            tmp = cache_path + ".tmp.%d" % os.getpid()
            with io.open(tmp, "w", encoding="utf-8") as fh:
                # dumps, not dump: json.dump streams through the pure-Python encoder; dumps takes the
                # C encoder (about 4x faster on a 5 MB cache) and writes the same bytes
                fh.write(json.dumps({"memory_dir": os.path.abspath(memory_dir),
                                     "version": _FM_CACHE_VERSION, "files": files}))
            os.replace(tmp, cache_path)
        except Exception:
            pass
    LAST_POINTERS.update(memory_dir=os.path.abspath(memory_dir), pointers=pointers)
    return out


def resolve(slug, routes, known_slugs=None, max_hops=32):
    """Resolve one slug to a terminal correction; broken/cyclic routes fail open."""
    original = slug
    current = slug
    seen = set()
    for _ in range(max_hops):
        if current not in routes:
            if current != original and (known_slugs is None or current in known_slugs):
                return current
            return original
        if current in seen:
            return original
        seen.add(current)
        nxt = routes.get(current, "")
        if not nxt or nxt in seen:
            return original
        current = nxt
    return original


# 3 Nov 2026: fok_probe and spread_recall both call active_supersessions on every prompt, in the one
# dispatcher process since the hooks went in-process, and each listed the whole memory folder: 0.45 s of a 0.72 s call
# with 45k files (S/fokprof). The slug set is kept per process and reused while the folder's inode, mtime and ctime are
# unchanged. Creating, removing or renaming an entry moves the folder's mtime and ctime, so the set is right by
# construction; the stat is read before the listing, so a change during the listing makes the next call list again.
# Not covered: a *.md symlink whose target changes between file and directory, which the listing alone would notice.
_KNOWN_SLUGS = {}


def _known_slugs(memory_dir):
    path = os.path.abspath(memory_dir)
    try:
        st = os.stat(path)
        key = (st.st_ino, st.st_mtime_ns, st.st_ctime_ns)
    except Exception:
        key = None
    hit = _KNOWN_SLUGS.get(path)
    if key is not None and hit is not None and hit[0] == key:
        return hit[1]
    names = frozenset(entry.name[:-3] for entry in _md_files(memory_dir))
    if key is not None:
        _KNOWN_SLUGS[path] = (key, names)
    return names


# 5 Nov 2026: the per-process set above still costs every fresh process the whole listing (26 ms cold on a
# 45,000-file memory folder), and fok_probe's call is the first in each prompt's dispatcher. active_supersessions only asks
# about slugs that appear in a route (as source or target): it intersects the listing with the route keys and resolve()
# tests membership of route targets only. So across processes it is enough to keep the known slugs among those route
# names, under the same folder key (inode, mtime, ctime) taken before the listing. A hit needs the key unchanged and
# every current route name inside the saved universe; anything else lists the folder as before. Same result by
# construction; BRAIN_SUP_ROUTE_CACHE=0 turns it off.
# One file for every folder, the newest ROUTE_KNOWN_MAX kept: a file per folder left one behind for each test's temp
# memory folder (eight from one test run).
ROUTE_KNOWN_MAX = 4


def _route_known_path(memory_dir=None):
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), ".sup_route_known.json")


def _route_known_all():
    import json
    try:
        with io.open(_route_known_path(), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _dir_key(memory_dir):
    try:
        st = os.stat(os.path.abspath(memory_dir))
        return [st.st_ino, st.st_mtime_ns, st.st_ctime_ns]
    except Exception:
        return None


def _route_known(memory_dir, routes):
    if os.environ.get("BRAIN_SUP_ROUTE_CACHE", "1") == "0":
        return None
    try:
        import json
        key = _dir_key(memory_dir)
        hit = _KNOWN_SLUGS.get(os.path.abspath(memory_dir))
        if key is None or (hit is not None and list(hit[0]) == key):
            return None              # no key, or this process already holds the full set: use it
        saved = _route_known_all().get(os.path.abspath(memory_dir)) or {}
        if saved.get("key") != key:
            return None
        universe = set(saved.get("universe") or ())
        if not (set(routes) <= universe and set(routes.values()) <= universe):
            return None
        return frozenset(saved.get("known") or ())
    except Exception:
        return None


def _route_known_save(memory_dir, routes, known):
    if os.environ.get("BRAIN_SUP_ROUTE_CACHE", "1") == "0":
        return
    try:
        import json
        hit = _KNOWN_SLUGS.get(os.path.abspath(memory_dir))
        if hit is None or hit[1] is not known:
            return                   # only a set _known_slugs keyed this call; its key was read before its listing
        import tempfile
        tmp_root = os.path.join(os.path.realpath(tempfile.gettempdir()), "")
        if os.path.realpath(memory_dir).startswith(tmp_root):
            return                   # test folders would push the live folder out of the cap
        universe = set(routes) | set(routes.values())
        data = _route_known_all()
        data.pop(os.path.abspath(memory_dir), None)
        data[os.path.abspath(memory_dir)] = {"key": list(hit[0]), "universe": sorted(universe),
                                             "known": sorted(universe & known)}
        while len(data) > ROUTE_KNOWN_MAX:
            data.pop(next(iter(data)))          # oldest written first: json keeps insertion order
        path = _route_known_path()
        tmp = "%s.%d.tmp" % (path, os.getpid())
        with io.open(tmp, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(data))
        os.replace(tmp, path)
    except Exception:
        pass


def active_supersessions(memory_dir, index_path=None, known_slugs=None,
                         include_frontmatter=False):
    """Return known historical slug -> existing terminal correction."""
    routes = load_map(memory_dir, index_path=index_path,
                      include_frontmatter=include_frontmatter)
    if known_slugs is None:
        # The prompt hook calls this in a fresh process on every turn.  ``listdir`` followed
        # by ``isfile`` paid one new path lookup for every memory (2,700+ on the live corpus,
        # roughly 100-180 ms on Windows).  ``scandir`` carries the directory-entry metadata
        # through the enumeration, while retaining the old contract: only real ``.md`` files
        # (including file symlinks) are eligible; a directory named ``*.md`` is not.
        known_slugs = _route_known(memory_dir, routes)
        if known_slugs is None:
            known_slugs = _known_slugs(memory_dir)
            _route_known_save(memory_dir, routes, known_slugs)
    else:
        known_slugs = set(known_slugs)
    active = {}
    for slug in known_slugs.intersection(routes):   # resolve() returns a slug with no route unchanged
        endpoint = resolve(slug, routes, known_slugs=known_slugs)
        if endpoint != slug:
            active[slug] = endpoint
    return active


def _synth_index_line(memory_dir, slug):
    body = _read(os.path.join(memory_dir, slug + ".md"), 4096)
    desc_match = FM_DESC_RX.search(body)
    status_match = FM_STATUS_RX.search(body)
    desc = desc_match.group(1).strip().strip('"\'') if desc_match else "current correction"
    status = status_match.group(1).upper() if status_match else "CURRENT"
    return "- [%s.md](%s.md) - [%s] %s" % (slug, slug, status, desc)


def _known_memory_slugs(memory_dir):
    return {entry.name[:-3] for entry in _md_files(memory_dir)}


def current_index_lines(memory_dir, index_path=None, limit=None,
                        include_frontmatter=True):
    """Return current-only MEMORY.md entries, preserving order and deduplicating endpoints."""
    raw_lines = _read(os.path.join(memory_dir, "MEMORY.md")).splitlines()
    entries = []
    by_slug = {}
    for line in raw_lines:
        match = INDEX_ENTRY_RX.match(line)
        if match:
            slug = match.group(1)
            entries.append((slug, line))
            by_slug.setdefault(slug, line)
    routes = active_supersessions(
        memory_dir, index_path=index_path,
        known_slugs=_known_memory_slugs(memory_dir),
        include_frontmatter=include_frontmatter)
    out = []
    seen = set()
    for slug, line in entries:
        current = routes.get(slug, slug)
        if current in seen:
            continue
        chosen = by_slug.get(current)
        if not chosen and os.path.isfile(os.path.join(memory_dir, current + ".md")):
            chosen = _synth_index_line(memory_dir, current)
        if not chosen:
            chosen = line
            current = slug
        out.append(chosen)
        seen.add(current)
        if limit is not None and len(out) >= limit:
            break
    return out


def audit_pointers(memory_dir):
    """Classify every correction pointer the way the machine reads it.

    Three outcomes, and only the first is protection: ROUTES (a slug naming a file that exists), DANGLING (a
    well-formed slug naming nothing - served as current because resolve fails open), PROSE (a sentence in a
    field that is read as one slug - the author believed they declared a correction and no route was built).
    `corrected_by` is reported beside `superseded_by` because authors use both, while only the latter routes.
    """
    # NON-ROUTING DECLARATION FIELDS. Three states exist that a routing pointer cannot express, and all three
    # used to be written into `superseded_by` as prose, where the resolver ignored them while the field still
    # implied a correction had been declared:
    #   partially_superseded_by  only PART of the memory was corrected - routing it would discard the rest
    #   corrected_in_place       the correction is in this memory's OWN body; there is no successor slug
    #   withdrawn_in_place       the memory withdrew its own claim; again no successor
    # They are collected in their own bucket so they stay VISIBLE in the report rather than vanishing from
    # every view, and the bucket is one list so adding another such field never changes this function's arity
    # again (changing it once already broke both callers).
    # `superseded_in_part` (2026-09-22) is the structured form of the first of these: one row per list item, the
    # successor recovered only when its file exists, so a part-link to nothing shows here as an empty recovery.
    _NON_ROUTING = ("partially_superseded_by", "corrected_in_place", "withdrawn_in_place")
    field_rx = re.compile(
        r"^[ \t]*(superseded_by|corrected_by|partially_superseded_by|corrected_in_place|withdrawn_in_place)"
        r"[ \t]*:[ \t]*(.*?)[ \t]*$", re.M)
    files = sorted(e.name for e in _md_files(memory_dir))
    names = {n[:-3] for n in files}
    routes, dangling, prose, partial = [], [], [], []
    for name in files:
        head = _read_frontmatter(os.path.join(memory_dir, name))
        block = FRONTMATTER_RX.search(head or "")
        if not block:
            continue
        if re.search(r"^[ \t]*superseded_in_part[ \t]*:", block.group(1), re.M):
            for clause, successor in part_supersessions(block.group(1), memory_dir):
                partial.append((name[:-3], "superseded_in_part",
                                ("%s %s %s" % (clause, PART_ARROW, successor)) if successor else clause, successor))
        for field, raw in field_rx.findall(block.group(1)):
            value = raw.strip().strip('"').strip("'")
            if not value or value.lower() in ("null", "none", "no"):
                continue
            slug = value[:-3] if value.endswith(".md") else value
            if field in _NON_ROUTING:
                lead = FM_SUPERSEDED_LEAD_RX.search("superseded_by: " + value)
                partial.append((name[:-3], field, value,
                                lead.group(1) if lead and lead.group(1) in names else ""))
            elif SLUG_RX.fullmatch(slug):
                (routes if slug in names else dangling).append((name[:-3], field, slug))
            else:
                lead = FM_SUPERSEDED_LEAD_RX.search("superseded_by: " + value)
                recovered = lead.group(1) if lead and lead.group(1) in names else ""
                prose.append((name[:-3], field, value, recovered))
    # Returned as a 4-tuple; callers that want only the first three still unpack correctly if they slice,
    # and hook_liveness reads the first three by name.
    return routes, dangling, prose, partial


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    current = sub.add_parser("current-index")
    current.add_argument("--memory-dir", required=True)
    current.add_argument("--index")
    current.add_argument("--limit", type=int)
    audit = sub.add_parser("audit", help="classify every correction pointer; non-zero if any is unroutable")
    audit.add_argument("--memory-dir", required=True)
    args = parser.parse_args(argv)
    if args.command == "audit":
        routes, dangling, prose, partial = audit_pointers(args.memory_dir)
        print("routes            %4d  slug -> an existing memory" % len(routes))
        print("DANGLING          %4d  slug -> nothing; served as CURRENT (resolve fails open)" % len(dangling))
        print("PROSE             %4d  not one slug; declared nothing" % len(prose))
        print("non-routing       %4d  partial / in-place declarations; deliberately not routed" % len(partial))
        for src, field, value, recovered in partial:
            print("  %-24s %-50s %.44s" % (field, src[:50], value))
        for src, field, slug in dangling:
            print("  DANGLING  %-58s %s -> %s" % (src[:58], field, slug))
        for src, field, value, recovered in prose:
            print("  PROSE     %-58s %s -> %.60s%s" % (src[:58], field, value,
                                                       "   [lead slug recovers: %s]" % recovered
                                                       if recovered else ""))
        return 1 if (dangling or prose) else 0
    if args.command == "current-index":
        memory_index = os.path.join(args.memory_dir, "MEMORY.md")
        if not os.path.isdir(args.memory_dir) or not os.path.isfile(memory_index):
            print("current index unavailable: %s" % memory_index, file=sys.stderr)
            return 1
        lines = current_index_lines(args.memory_dir, index_path=args.index, limit=args.limit)
        if not lines:
            print("current index has no readable entries: %s" % memory_index, file=sys.stderr)
            return 1
        for line in lines:
            print(line)
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
