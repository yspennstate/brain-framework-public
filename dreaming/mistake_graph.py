#!/usr/bin/env python3
"""mistake_graph.py - the corrections corpus as a graph: mine the families, measure recurrence, find the gaps.

The dream hand-named FIVE recurring mistake shapes (MISTAKES.md). That hand-clustering is lossy: prediction
p8 ("the next correction fits one of the five") resolved MISS on 2026-07-09 - the eoc intent-misread did not
fit any of the five. So the hand-partition does not span the space, and "is this a recurring pattern?" should
be MEASURED on the graph, not eyeballed.

This reads the corrections corpus (MISTAKES.md, which schema_extract.py and the association graph both
deliberately EXCLUDE, so it has never been graph-mined) and builds a mistake graph:

  * nodes    = individual corrections (each time the owner told an agent it was wrong)
  * edges    = structural similarity: shared reflex id, shared cited scar ([[backlink]]), and
               salient-token overlap of the (what-went-wrong + lesson) text - "same failure shape"

Then, from the network-science canon:

  1. COMMUNITIES (Louvain): the families the data finds, vs the 5 the dream named by hand.
  2. RECURRENCE vs a degree-preserving NULL (configuration model, double-edge-swap): is the family
     structure above what random rewiring of the same degree sequence would produce? Small-n honest:
     also report each family's DISTINCT-DATE span (a family spanning many dates is genuinely recurring,
     robust to the null's noise on ~40 nodes).
  3. COVERAGE, a bipartite family<->reflex graph: a family with degree 0 (no member names a real reflex)
     is UNCOVERED - the structural version of "36% gut coverage of 161 failures".
  4. MINDWARE-OVERRIDE (Stanovich): a family that HAS a reflex but keeps accruing corrections dated AFTER
     the reflex existed = "the reflex fired yet the mistake landed" - the named-but-unbuilt override_audit.
  5. PRIORITY by centrality: rank the gaps by recurrence x blast-radius (cited fallout) x coverage-gap,
     so the highest-leverage missing/weak reflex surfaces first.

The output (mistake_families.json, only with --write) is reflex CONTENT: it names the uncovered families and
the missing mediator (the verify-step the lesson prescribes - "the omitted term is the edge"). The FIRING of
those reflexes is a separate layer (graph_fire/, another agent). This is upstream: what to fire and why.

Read-only by default (reports to stdout, writes nothing). Stdlib + networkx (guarded; degrades to
connected-components if networkx is absent). Forward-slash paths, ASCII only.

  python mistake_graph.py                 # report
  python mistake_graph.py --write         # also emit mistake_families.json
"""
import os, re, json, sys, math, hashlib, tempfile, time
from collections import defaultdict, Counter

from append_journal import exclusive_append_lock

HOME = os.path.expanduser("~")
DR = os.path.join(HOME, ".claude", "dreaming")
MEM = os.path.join(HOME, ".claude", "projects", "{{BRAIN_SLUG}}", "memory")
MISTAKES = os.path.join(MEM, "MISTAKES.md")
INSTINCTS = os.path.join(DR, "instincts.tsv")
OUT = os.path.join(DR, "mistake_families.json")
SNAP = os.path.join(DR, ".mistake_graph_snapshot.json")   # --watch diffs against this to fire on recurrence
STATUS = os.path.join(DR, ".mistake_graph_status.json")

SNAPSHOT_MAX_BYTES = 4 * 1024 * 1024
ARTIFACT_MAX_BYTES = 16 * 1024 * 1024
INSTINCTS_MAX_BYTES = 8 * 1024 * 1024
MISTAKES_MAX_BYTES = 64 * 1024 * 1024
SNAPSHOT_LOCK_SECONDS = 30.0
SNAPSHOT_RETRY_SECONDS = 0.5
_WINDOWS_TRANSIENT_CODES = frozenset((5, 32, 33))
SNAPSHOT_V2_SCHEMA = "mistake-graph-watch/v2"
SNAPSHOT_SCHEMA = "mistake-graph-watch/v3"
GENERATION_SCHEMA = "mistake-graph-generation/v1"
GENERATION_MAX_BYTES = 256
STATUS_SCHEMA = "mistake-graph-status/v1"
STATUS_MAX_BYTES = 1024
STATUS_SIGNAL_MAX = 1_000_000
_STATUS_STATES = frozenset(("baseline", "stale", "no_warning", "actionable"))
_STATUS_SIGNAL_FIELDS = (
    "became_recurring", "new_corrections", "new_overrides", "causal_removals",
)
_SNAPSHOT_CURRENT_FIELDS = ("all_ids", "singletons", "override_reflexes")
_SNAPSHOT_DATA_FIELDS = _SNAPSHOT_CURRENT_FIELDS + ("seen_ids",)
_SNAPSHOT_V2_FIELDS = ("schema", "observed_at_ns") + _SNAPSHOT_CURRENT_FIELDS
_SNAPSHOT_FIELDS = ("schema", "observed_at_ns") + _SNAPSHOT_DATA_FIELDS


class SnapshotStateError(RuntimeError):
    """A watch snapshot could not be read or advanced without losing state."""


class SnapshotConflictError(SnapshotStateError):
    """Two snapshots cannot be ordered safely by their correction ids."""


class SnapshotDeliveryError(SnapshotStateError):
    """The recurrence verdict did not cross stdout completely."""


class SnapshotPublicationError(SnapshotStateError):
    """The verdict was delivered but its retryable state could not advance."""


class SourceStateError(RuntimeError):
    """A load-bearing correction source was unreadable or exceeded its bound."""


def _status_path_for_snapshot(snapshot_path, explicit=None):
    return str(explicit) if explicit is not None else str(snapshot_path) + ".status.json"


def _paths_alias(left, right):
    """Fail closed when two publication paths may name the same filesystem object."""
    left = os.fspath(left)
    right = os.fspath(right)
    left_canonical = os.path.normcase(os.path.realpath(os.path.abspath(left)))
    right_canonical = os.path.normcase(os.path.realpath(os.path.abspath(right)))
    if left_canonical == right_canonical:
        return True
    def stat_if_present(path):
        try:
            return os.stat(path)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise SnapshotDeliveryError(
                "could not prove watch snapshot and status paths are distinct"
            ) from exc

    left_stat = stat_if_present(left)
    right_stat = stat_if_present(right)
    if left_stat is not None and right_stat is not None:
        try:
            return os.path.samefile(left, right)
        except OSError as exc:
            raise SnapshotDeliveryError(
                "could not prove watch snapshot and status paths are distinct"
            ) from exc
    return False


def _json_loads_no_duplicates(text):
    """Decode JSON while rejecting ambiguous duplicate object keys."""
    def object_from_pairs(pairs):
        out = {}
        for key, value in pairs:
            if key in out:
                raise ValueError("duplicate JSON object key: %s" % key)
            out[key] = value
        return out

    return json.loads(text, object_pairs_hook=object_from_pairs)

# tokens too generic to signal a shared failure SHAPE (they cluster the whole corpus)
STOP = set("""the a an and or of to in on for by is are was were be been being it its this that these those with
without into onto from as at we you he she they them his her their our your my me not no yes do did done if then
else than so but a i s w d id when where why how who all any more most less very really about around here there
will would could should can may might must one two three new old now day days before after check make sure look
take run use get keep need want first still just also see saw open said say tell point place change move find
wrong lesson reflex none yet candidate same class near also folds under must fire trigger refine both failed
owner agent agents his him her""".split())
# NOTE: domain nouns (grid, sim, book, data, hedge, card, anchor) are DELIBERATELY NOT stopped - they are the
# mistake-context signal that groups a real family, exactly as build_anchors.py argues (stopwords, not IDF).

WORD = re.compile(r"[a-zA-Z][a-zA-Z0-9_]{3,}")
DATE = re.compile(r"(20\d{2})[-_](\d{2})[-_](\d{2})")
MMDD = re.compile(r"\b(\d{2})[-/](\d{2})\b")
BACKLINK = re.compile(r"\[\[([^\]]+)\]\]")
# a correction bullet: - **HEADER** ... wrong: W <delim> lesson: L <delim> reflex: R. The delimiter is NOT
# fixed: older entries use "; lesson:", the verbose 07-11 ones use ". Lesson:". So split on the MARKER word
# (wrong:/lesson:/reflex: with a colon), never on the punctuation before it - the semicolon-only regex
# silently dropped every period-delimited 07-11 correction (the override cases). Match "reflex:" WITH a
# colon so the prose "the reflex must fire" inside a lesson is not mistaken for the field marker.
WRONG_MARK = re.compile(r"wrong:\s*", re.I)
LESSON_MARK = re.compile(r"lesson:\s*", re.I)
REFLEX_MARK = re.compile(r"reflex:\s*", re.I)
HEADER = re.compile(r"^\s*-\s*\*\*(?P<hdr>.+?)\*\*(?P<rest>.*)$", re.S)


def extract_fields(rest):
    """Return (wrong, lesson, reflex) by marker position, delimiter-agnostic. None if not a wrong/lesson bullet."""
    wm = WRONG_MARK.search(rest)
    if not wm:
        return None
    body = rest[wm.end():]
    lm = LESSON_MARK.search(body)
    if not lm:
        return None
    wrong = body[:lm.start()]
    after = body[lm.end():]
    rm = REFLEX_MARK.search(after)
    if rm:
        lesson, reflex = after[:rm.start()], after[rm.end():]
    else:
        lesson, reflex = after, ""
    clean = lambda s: re.sub(r"[;.\s]+$", "", re.sub(r"\s+", " ", s)).strip()
    return clean(wrong), clean(lesson), re.sub(r"\s+", " ", reflex).strip()
# the dream's hand-named recurring shapes: "N. **...**"
HANDNAMED = re.compile(r"^\d+\.\s*\*\*(?P<name>.+?)\*\*\s*(?P<body>.*)$", re.S)
# the corpus stating, in its own words, that a reflex EXISTED but did not fire (Stanovich mindware-override).
# ONLY past-tense failure markers - a prescriptive "must fire on X" is a future fix, not evidence of override.
FIRED_LANDED = re.compile(r"failed to fire|did ?n'?t fire|did not fire|both failed to fire|both silent|"
                          r"were both silent|both.{0,20}failed to fire|it did not fire|"
                          r"reflex[^.]{0,30}(both )?(silent|did not fire|failed to fire)", re.I)


def toks(s):
    return [w.lower() for w in WORD.findall(s or "") if w.lower() not in STOP and not w.isdigit()]


def _read_source_text(path, max_bytes, label):
    """Capture one source with a stat fast-path and an actual bounded byte read."""
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        raise SourceStateError("%s stat failed: %s" % (label, type(exc).__name__)) from exc
    if size > max_bytes:
        raise SourceStateError("%s exceeds %d bytes" % (label, max_bytes))
    try:
        with open(path, "rb") as source:
            before = os.fstat(source.fileno())
            if before.st_size > max_bytes:
                raise SourceStateError("%s exceeds %d bytes after open" % (label, max_bytes))
            raw = source.read(max_bytes + 1)
            after = os.fstat(source.fileno())
        if len(raw) > max_bytes:
            raise SourceStateError("%s grew beyond its read bound" % label)
        path_after = os.stat(path)

        def signature(stat_result):
            return (getattr(stat_result, "st_dev", None),
                    getattr(stat_result, "st_ino", None),
                    stat_result.st_size,
                    getattr(stat_result, "st_mtime_ns", None))

        if (signature(before) != signature(after)
                or signature(after) != signature(path_after)
                or len(raw) != after.st_size):
            raise SourceStateError("%s changed while it was captured" % label)
        text = raw.decode("utf-8-sig", "strict")
        if not text.strip():
            raise SourceStateError("%s is empty" % label)
        return text
    except SourceStateError:
        raise
    except Exception as exc:
        raise SourceStateError("%s read failed: %s" % (label, type(exc).__name__)) from exc


def _capture_source_pair(stateful, verify_pair=False, status_path=None):
    """Capture one coherent source pair and its causally ordered generation.

    Stateful scans briefly share the snapshot's stable lock for capture only.
    That makes generation order equal source-capture order without holding the
    lock through graph work. Reading the pair twice rejects a source transition
    that would otherwise combine two different corpus generations.
    """
    def capture_once():
        return (
            _read_source_text(INSTINCTS, INSTINCTS_MAX_BYTES, "instincts.tsv"),
            _read_source_text(MISTAKES, MISTAKES_MAX_BYTES, "MISTAKES.md"),
        )

    if not stateful:
        generation = time.time_ns()
        first = capture_once()
        if verify_pair:
            second = capture_once()
            if first != second:
                raise SourceStateError(
                    "correction sources changed during paired capture")
        return generation, first[0], first[1]
    try:
        with exclusive_append_lock(SNAP, timeout=SNAPSHOT_LOCK_SECONDS):
            generation = _reserve_generation_locked(
                _status_path_for_snapshot(SNAP, status_path))
            first = capture_once()
            second = capture_once()
            if first != second:
                raise SourceStateError("correction sources changed during paired capture")
            return generation, first[0], first[1]
    except SourceStateError:
        raise
    except Exception as exc:
        raise SourceStateError(
            "correction source capture lock failed: %s" % type(exc).__name__) from exc


def _reserve_generation_locked(status_path=None):
    """Reserve a strictly increasing attempt generation; caller owns SNAP's lock.

    This sidecar records attempts, not warning delivery. Crash gaps are safe and
    intentional: they make lock acquisition/source-capture order total across
    processes without pretending a failed scan was delivered.
    """
    counter_path = SNAP + ".generation.json"
    counter = 0
    try:
        raw = _read_file_bytes(counter_path, max_bytes=GENERATION_MAX_BYTES)
    except FileNotFoundError:
        pass
    except Exception as exc:
        raise SourceStateError("watch generation counter is unreadable") from exc
    else:
        try:
            value = _json_loads_no_duplicates(raw.decode("utf-8", "strict"))
        except Exception as exc:
            raise SourceStateError("watch generation counter is malformed") from exc
        if (not isinstance(value, dict)
                or set(value) != {"schema", "value"}
                or value.get("schema") != GENERATION_SCHEMA
                or type(value.get("value")) is not int
                or not 0 <= value["value"] < (1 << 63)):
            raise SourceStateError("watch generation counter schema is invalid")
        counter = value["value"]

    snapshot_generation = 0
    try:
        snapshot_raw = _read_file_bytes(SNAP, max_bytes=SNAPSHOT_MAX_BYTES)
    except FileNotFoundError:
        pass
    except Exception as exc:
        raise SourceStateError("watch snapshot is unreadable during generation reservation") from exc
    else:
        try:
            snapshot_generation = _decode_snapshot(snapshot_raw)["observed_at_ns"]
        except Exception as exc:
            raise SourceStateError("watch snapshot is invalid during generation reservation") from exc

    status_generation = 0
    target_status = _status_path_for_snapshot(SNAP, status_path)
    try:
        status_generation = load_watch_status(target_status)["generation"]
    except FileNotFoundError:
        pass
    except Exception as exc:
        raise SourceStateError(
            "watch status is invalid during generation reservation") from exc

    generation = max(counter, snapshot_generation, status_generation) + 1
    if generation >= (1 << 63):
        raise SourceStateError("watch generation counter is exhausted")
    encoded = (json.dumps(
        {"schema": GENERATION_SCHEMA, "value": generation},
        ensure_ascii=True, sort_keys=True,
    ) + "\n").encode("ascii")
    try:
        _atomic_replace_bytes(counter_path, encoded)
    except Exception as exc:
        raise SourceStateError("watch generation reservation failed") from exc
    return generation


def load_reflex_ids(raw=None):
    """The real reflex ids (instincts.tsv). A correction is COVERED iff its reflex field names one of these."""
    if raw is None:
        raw = _read_source_text(INSTINCTS, INSTINCTS_MAX_BYTES, "instincts.tsv")
    lines = raw.splitlines()
    expected_header = [
        "id", "mode", "validity", "trigger", "gut", "sting", "source", "charge",
    ]
    if not lines or lines[0].split("\t") != expected_header:
        raise SourceStateError("instincts.tsv header schema is invalid")
    ids = set()
    for row_number, line in enumerate(lines[1:], start=2):
        fields = line.split("\t")
        if len(fields) != len(expected_header) or not fields[0].strip():
            raise SourceStateError(
                "instincts.tsv row %d has an invalid schema" % row_number)
        reflex_id = fields[0].strip()
        if reflex_id in ids:
            raise SourceStateError(
                "instincts.tsv row %d duplicates reflex id" % row_number)
        ids.add(reflex_id)
    if not ids:
        raise SourceStateError("instincts.tsv contains no reflex ids")
    return ids


def norm_date(hdr, body):
    """A sortable YYYYMMDD-ish int from the header/body; 0 if none found. Only ordering matters."""
    m = DATE.search(hdr) or DATE.search(body[:200])
    if m:
        return int("%s%s%s" % (m.group(1), m.group(2), m.group(3)))
    m = MMDD.search(hdr)
    if m:  # bare MM-DD in a 2026 corpus
        return int("2026%s%s" % (m.group(1), m.group(2)))
    return 0


def parse_corrections(reflex_ids, raw=None):
    """Parse MISTAKES.md into correction records. Robust/fail-open per bullet."""
    if raw is None:
        raw = _read_source_text(MISTAKES, MISTAKES_MAX_BYTES, "MISTAKES.md")
    # split the two roles: correction bullets vs the dream's hand-named recurring list
    hand = []
    for ln in raw.splitlines():
        hm = HANDNAMED.match(ln.strip())
        if hm and "->" in hm.group("body"):
            hand.append(re.sub(r"\s+", " ", hm.group("name")).strip())

    # a bullet can span multiple physical lines; join a bullet until the next "- **" or blank-blank
    bullets = []
    cur = []
    for ln in raw.splitlines():
        if re.match(r"^\s*-\s*\*\*", ln):
            if cur:
                bullets.append("\n".join(cur))
            cur = [ln]
        elif cur is not None and ln.strip() and not ln.startswith(("#", "<!--", "**Coverage", "###", "1.", "2.",
                                                                   "3.", "4.", "5.")):
            cur.append(ln)
        else:
            if cur:
                bullets.append("\n".join(cur))
            cur = []
    if cur:
        bullets.append("\n".join(cur))

    recs = []
    for b in bullets:
        hm = HEADER.match(b)
        if not hm:
            continue
        hdr, rest = hm.group("hdr"), hm.group("rest")
        fields = extract_fields(rest)
        if not fields:
            continue  # not a wrong/lesson-structured correction (prose note); skip for the graph
        wrong, lesson, reflex_field = fields
        if not wrong and not lesson:
            continue
        # COVERED = a real reflex id appears anywhere in the correction, not only in the "reflex:" field: the
        # verbose 07-11 entries drop the field and name the reflex in prose ("must trigger his_call_is_final").
        # Scanning only the field silently under-counts coverage (marks a covered correction as a gap).
        scan = wrong + " " + lesson + " " + reflex_field
        named = sorted({rid for rid in reflex_ids if re.search(r"\b" + re.escape(rid) + r"\b", scan)})
        says_none = bool(re.search(r"none yet|none\b|no reflex", reflex_field, re.I))
        # a candidate id proposed but NOT yet a real reflex (e.g. "leveraged_etf_is_not_linear (none yet")
        cand = re.findall(r"\b([a-z][a-z0-9_]{4,})\b", reflex_field)
        candidates = sorted({c for c in cand if c not in reflex_ids and c not in STOP and "_" in c})
        backlinks = sorted(set(BACKLINK.findall(b)))
        date = norm_date(hdr, rest)
        body_toks = toks(wrong + " " + lesson)
        # EXPLICIT mindware-override: the corpus itself states when a reflex existed yet did not fire.
        # Ground-truth text ("both failed to fire", "both silent", "did NOT fire") is far more honest than
        # inferring override from dates (a reflex is often COMPILED in response to the later correction).
        fired_but_landed = bool(FIRED_LANDED.search(b))
        # content-based id, NOT index-based: the id must be stable across runs so --watch can diff snapshots
        # (an index shifts the moment a correction is inserted/reordered, which would break every diff).
        rid = "c_" + hashlib.sha1(b.encode("utf-8", "replace")).hexdigest()[:10]
        recs.append({
            "id": rid, "header": re.sub(r"\s+", " ", hdr)[:60], "date": date,
            "wrong": wrong[:400], "lesson": lesson[:400],
            "named_reflexes": named, "candidates": candidates, "says_none": says_none,
            "covered": bool(named), "backlinks": backlinks, "tokens": body_toks,
            "fired_but_landed": fired_but_landed,
        })
    return recs, hand


def similarity(a, b, use_reflex=True):
    """Structural similarity of two corrections in [0, inf).

    use_reflex=True: shared reflex id + shared candidate + shared scar + token overlap (the full view).
    use_reflex=False: the REFLEX-BLIND view - token + scar overlap ONLY. This is the non-circular signal:
    if families survive when the reflex labels are NOT fed in, the structure is real, not rediscovered."""
    w = 0.0
    detail = []
    if use_reflex:
        shared_reflex = set(a["named_reflexes"]) & set(b["named_reflexes"])
        if shared_reflex:
            w += 1.0 * len(shared_reflex)      # same reflex id = the strongest same-family signal
            detail.append("reflex:" + ",".join(sorted(shared_reflex)))
        shared_cand = set(a["candidates"]) & set(b["candidates"])
        if shared_cand:
            w += 0.8 * len(shared_cand)        # same proposed-but-unbuilt reflex = an uncovered family forming
            detail.append("cand:" + ",".join(sorted(shared_cand)))
    shared_link = set(a["backlinks"]) & set(b["backlinks"])
    if shared_link:
        w += 0.6 * len(shared_link)            # cite the same scar = touch the same context
        detail.append("scar:%d" % len(shared_link))
    # 5-char stem key + overlap coefficient: robust to short, lexically-varied lesson texts (verify/verified/
    # verifying -> 'verif'), and |A^B|/min(|A|,|B|) is forgiving where Jaccard is too harsh on short strings.
    # Require >=3 shared stems so a single common word can't link everything.
    ta = {t[:5] for t in a["tokens"]}
    tb = {t[:5] for t in b["tokens"]}
    inter = ta & tb
    if ta and tb and len(inter) >= 3:
        ov = len(inter) / float(min(len(ta), len(tb)))
        w += 2.5 * ov
        detail.append("tok:%.2f(%d)" % (ov, len(inter)))
    return w, detail


EDGE_THRESHOLD = 0.30   # min combined weight to draw an edge (tuned so token-only noise doesn't over-link)
CATALOG_RES = 1.4       # Louvain resolution for the actionable family catalog (finer than 1.0 so the domain
                        # corrections don't collapse into one coarse blob). Significance test stays at 1.0.
SIG_RES = 1.0           # resolution for the observed-vs-null significance test (apples-to-apples baseline)


def _ascii(s):
    """Down-map the corpus's curly quotes / dashes to ASCII for a clean console (no self-inflicted mojibake -
    the ERN flags the byte patterns this produces). Display only; the JSON on disk keeps ensure_ascii."""
    if not s:
        return ""
    repl = {"\u2019": "'", "\u2018": "'", "\u201c": '"', "\u201d": '"', "\u2014": "-",
            "\u2013": "-", "\u2026": "...", "\u2192": "->", "\u00a0": " "}
    for k, v in repl.items():
        s = s.replace(k, v)
    return s.encode("ascii", "replace").decode("ascii").replace("?", ".")


def build_graph(recs, use_reflex=True):
    edges = []
    for i in range(len(recs)):
        for j in range(i + 1, len(recs)):
            w, detail = similarity(recs[i], recs[j], use_reflex=use_reflex)
            if w >= EDGE_THRESHOLD:
                edges.append((recs[i]["id"], recs[j]["id"], w, detail))
    return edges


def communities(recs, edges, resolution=1.0):
    """Louvain communities if networkx is present; else connected components. Returns list of member-id sets.

    resolution>1 yields FINER communities: domain tokens over-connect same-domain corrections
    into one coarse blob at resolution 1.0, so the catalog uses a higher resolution for actionable sub-families.
    The significance test (null) is run at a fixed resolution so observed and null are apples-to-apples."""
    idset = [r["id"] for r in recs]
    try:
        import networkx as nx
    except ImportError:
        # stdlib fallback: connected components via union-find
        adj = defaultdict(set)
        for a, b, w, _ in edges:
            adj[a].add(b); adj[b].add(a)
        seen, comps = set(), []
        for n in idset:
            if n in seen:
                continue
            stack, mem = [n], []
            while stack:
                x = stack.pop()
                if x in seen:
                    continue
                seen.add(x); mem.append(x)
                stack.extend(adj[x] - seen)
            comps.append(set(mem))
        return comps, None, None
    G = nx.Graph()
    G.add_nodes_from(idset)
    for a, b, w, _ in edges:
        G.add_edge(a, b, weight=w)
    comms = nx.community.louvain_communities(
        G, weight="weight", seed=42, resolution=resolution)
    q = nx.community.modularity(
        G, comms, weight="weight") if G.number_of_edges() else 0.0
    return [set(c) for c in comms], q, G


def null_modularity(G, observed_q, rounds=200, resolution=1.0):
    """Degree-preserving configuration-model null: rewire the same degree sequence, recompute Q. Returns
    (mean, std, z). Honest on small n - reported as indicative, not a p-value. Uses the SAME resolution as
    the observed Q it is compared against, or the test is apples-to-oranges."""
    try:
        import networkx as nx
    except ImportError:
        return None
    if G is None or G.number_of_edges() < 8:
        return None
    qs = []
    for k in range(rounds):
        H = G.copy()
        try:
            nx.double_edge_swap(
                H, nswap=max(4, H.number_of_edges()),
                max_tries=H.number_of_edges() * 20, seed=1000 + k)
        except nx.NetworkXAlgorithmError:
            # Some small/rigid graphs cannot realize the requested number of
            # swaps within max_tries. That statistical sample is unusable, but
            # an unrelated runtime/programming failure must abort publication.
            continue
        c = nx.community.louvain_communities(
            H, weight="weight", seed=7, resolution=resolution)
        qs.append(nx.community.modularity(H, c, weight="weight"))
    if len(qs) < 20:
        return None
    mean = sum(qs) / len(qs)
    var = sum((x - mean) ** 2 for x in qs) / (len(qs) - 1)
    std = math.sqrt(var) if var > 0 else 1e-9
    return {"mean": mean, "std": std,
            "z": (observed_q - mean) / std, "rounds": len(qs)}


def dominant_lesson(members, recs_by_id):
    toks_c = Counter()
    for m in members:
        toks_c.update(recs_by_id[m]["tokens"])
    return [t for t, _ in toks_c.most_common(6)]


def missing_mediator(members, recs_by_id):
    """The verify-step the lessons prescribe - the 'omitted edge'. Heuristic: the imperative clause of the
    longest lesson, plus the shared salient verbs (verify/reconcile/check/simulate/confirm/isolate)."""
    verbs = ("verify", "reconcile", "check", "simulate", "confirm", "isolate", "retrieve", "restate",
             "re-derive", "rederive", "test", "read", "measure", "ground", "cross-check", "scope")
    hits = Counter()
    longest = ""
    for m in members:
        les = recs_by_id[m]["lesson"]
        if len(les) > len(longest):
            longest = les
        for v in verbs:
            if re.search(r"\b" + re.escape(v) + r"\b", les, re.I):
                hits[v] += 1
    verb = hits.most_common(1)[0][0] if hits else "verify"
    return verb, longest[:180]


def _cross_reflex_bridges(recs_by_id, edges_blind):
    """Blind edges linking two corrections with NON-EMPTY, DISJOINT reflex sets: token-shape says 'same
    failure' where the reflex tags say 'different families'. These are the links a tag-only view cannot see."""
    out = []
    for a, b, w, detail in edges_blind:
        ra, rb = set(recs_by_id[a]["named_reflexes"]), set(recs_by_id[b]["named_reflexes"])
        if ra and rb and not (ra & rb):
            out.append((a, b, w, sorted(ra), sorted(rb)))
    return out


def _retryable_windows_io(exc):
    """Only retry the Windows sharing/access failures observed around replace."""
    if os.name != "nt" or not isinstance(exc, OSError):
        return False
    code = getattr(exc, "winerror", None)
    # CPython sometimes exposes a bare PermissionError without winerror for an
    # access-denied/sharing collision.  It is still classified, not a blanket
    # retry of every OSError.
    return code in _WINDOWS_TRANSIENT_CODES or (code is None and isinstance(exc, PermissionError))


def _retry_windows_io(operation, retry_seconds=None):
    """Run one filesystem operation with a short, classified Windows retry."""
    budget = SNAPSHOT_RETRY_SECONDS if retry_seconds is None else max(0.0, float(retry_seconds))
    deadline = time.monotonic() + budget
    delay = 0.005
    while True:
        try:
            return operation()
        except OSError as exc:
            if not _retryable_windows_io(exc) or time.monotonic() >= deadline:
                raise
            time.sleep(delay)
            delay = min(0.05, delay * 2.0)


def _read_file_bytes_once(path, max_bytes=SNAPSHOT_MAX_BYTES):
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    fd = os.open(path, flags)
    try:
        size = os.fstat(fd).st_size
        if size > max_bytes:
            raise SnapshotStateError("watch snapshot exceeds %d bytes" % max_bytes)
        chunks = []
        total = 0
        while True:
            chunk = os.read(fd, min(65536, max_bytes + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > max_bytes:
                raise SnapshotStateError("watch snapshot exceeds %d bytes" % max_bytes)
        return b"".join(chunks)
    finally:
        os.close(fd)


def _read_file_bytes(path, max_bytes=SNAPSHOT_MAX_BYTES, retry_seconds=None):
    return _retry_windows_io(
        lambda: _read_file_bytes_once(path, max_bytes=max_bytes),
        retry_seconds=retry_seconds,
    )


def _canonical_snapshot(payload, source, allow_legacy=False,
                        require_fresh_history=False):
    """Strictly validate state so corruption cannot masquerade as a baseline."""
    if not isinstance(payload, dict):
        raise SnapshotStateError("%s snapshot has an invalid object schema" % source)
    keys = set(payload)
    if allow_legacy and keys == set(_SNAPSHOT_CURRENT_FIELDS):
        out = {"schema": SNAPSHOT_SCHEMA, "observed_at_ns": 0}
        legacy_history = True
    elif (allow_legacy and keys == set(_SNAPSHOT_V2_FIELDS)
          and payload.get("schema") == SNAPSHOT_V2_SCHEMA):
        out = {"schema": SNAPSHOT_SCHEMA}
        legacy_history = True
    elif keys == set(_SNAPSHOT_FIELDS):
        if payload.get("schema") != SNAPSHOT_SCHEMA:
            raise SnapshotStateError("%s snapshot schema is unsupported" % source)
        out = {"schema": SNAPSHOT_SCHEMA}
        legacy_history = False
    else:
        raise SnapshotStateError("%s snapshot has an invalid object schema" % source)

    if "observed_at_ns" not in out:
        generation = payload.get("observed_at_ns")
        if (type(generation) is not int or not 0 <= generation < (1 << 63)):
            raise SnapshotStateError("%s snapshot generation is invalid" % source)
        out["observed_at_ns"] = generation

    for field in _SNAPSHOT_CURRENT_FIELDS:
        values = payload.get(field)
        if not isinstance(values, list) or any(not isinstance(v, str) or not v for v in values):
            raise SnapshotStateError("%s snapshot field %s must be non-empty strings" % (source, field))
        if len(values) != len(set(values)):
            raise SnapshotStateError("%s snapshot field %s contains duplicates" % (source, field))
        out[field] = sorted(values)
    if legacy_history:
        out["seen_ids"] = list(out["all_ids"])
    else:
        values = payload.get("seen_ids")
        if not isinstance(values, list) or any(
                not isinstance(value, str) or not value for value in values):
            raise SnapshotStateError(
                "%s snapshot field seen_ids must be non-empty strings" % source)
        if len(values) != len(set(values)):
            raise SnapshotStateError(
                "%s snapshot field seen_ids contains duplicates" % source)
        out["seen_ids"] = sorted(values)
    if not set(out["singletons"]).issubset(set(out["all_ids"])):
        raise SnapshotStateError("%s snapshot singletons are not correction ids" % source)
    if not set(out["all_ids"]).issubset(set(out["seen_ids"])):
        raise SnapshotStateError("%s snapshot current ids are absent from seen history" % source)
    if require_fresh_history and out["seen_ids"] != out["all_ids"]:
        raise SnapshotStateError(
            "%s snapshot may not forge prior seen-ID history" % source)
    return out


def _decode_snapshot(raw):
    try:
        payload = _json_loads_no_duplicates(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise SnapshotStateError("existing watch snapshot is malformed") from exc
    return _canonical_snapshot(payload, "existing", allow_legacy=True)


def _encode_snapshot(payload, require_fresh_history=False):
    canonical = _canonical_snapshot(
        payload, "incoming", require_fresh_history=require_fresh_history)
    raw = (json.dumps(canonical, ensure_ascii=True, sort_keys=True) + "\n").encode("ascii")
    if len(raw) > SNAPSHOT_MAX_BYTES:
        raise SnapshotStateError("incoming watch snapshot exceeds %d bytes" % SNAPSHOT_MAX_BYTES)
    return canonical, raw


def _canonical_watch_status(payload, source="status"):
    """Validate the privacy-minimized watch status consumed at wake time."""
    if (not isinstance(payload, dict)
            or set(payload) != {
                "schema", "generation", "snapshot_sha256", "state", "signals",
                "carried",
            }
            or payload.get("schema") != STATUS_SCHEMA):
        raise SnapshotStateError("%s has an invalid watch-status schema" % source)
    generation = payload.get("generation")
    snapshot_sha256 = payload.get("snapshot_sha256")
    state = payload.get("state")
    signals = payload.get("signals")
    carried = payload.get("carried")
    if type(generation) is not int or not 0 <= generation < (1 << 63):
        raise SnapshotStateError("%s watch-status generation is invalid" % source)
    if (not isinstance(snapshot_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", snapshot_sha256) is None):
        raise SnapshotStateError("%s watch-status snapshot digest is invalid" % source)
    if not isinstance(state, str) or state not in _STATUS_STATES:
        raise SnapshotStateError("%s watch-status state is invalid" % source)
    if type(carried) is not bool:
        raise SnapshotStateError("%s watch-status carry marker is invalid" % source)
    if not isinstance(signals, dict) or set(signals) != set(_STATUS_SIGNAL_FIELDS):
        raise SnapshotStateError("%s watch-status signals are invalid" % source)
    clean_signals = {}
    for field in _STATUS_SIGNAL_FIELDS:
        value = signals.get(field)
        if type(value) is not int or not 0 <= value <= STATUS_SIGNAL_MAX:
            raise SnapshotStateError(
                "%s watch-status signal %s is invalid" % (source, field))
        if field == "causal_removals" and value not in (0, 1):
            raise SnapshotStateError(
                "%s watch-status causal-removal evidence must be 0 or 1" % source)
        clean_signals[field] = value
    actionable = any(clean_signals.values())
    if (state == "actionable") != actionable:
        raise SnapshotStateError(
            "%s watch-status actionability disagrees with its signals" % source)
    if carried and state != "actionable":
        raise SnapshotStateError(
            "%s carried watch status must be actionable" % source)
    return {
        "schema": STATUS_SCHEMA,
        "generation": generation,
        "snapshot_sha256": snapshot_sha256,
        "state": state,
        "signals": clean_signals,
        "carried": carried,
    }


def _decode_watch_status(raw, source="existing status"):
    try:
        payload = _json_loads_no_duplicates(raw.decode("utf-8", "strict"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise SnapshotStateError("%s is malformed" % source) from exc
    return _canonical_watch_status(payload, source)


def _encode_watch_status(payload):
    canonical = _canonical_watch_status(payload, "incoming status")
    raw = (json.dumps(
        canonical, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
    ) + "\n").encode("ascii")
    if len(raw) > STATUS_MAX_BYTES:
        raise SnapshotStateError(
            "incoming watch status exceeds %d bytes" % STATUS_MAX_BYTES)
    return canonical, raw


def load_watch_status(path=STATUS):
    """Strict bounded loader for wake/status consumers; missing remains distinct."""
    raw = _read_file_bytes(path, max_bytes=STATUS_MAX_BYTES)
    return _decode_watch_status(raw)


def _empty_status_signals():
    return {field: 0 for field in _STATUS_SIGNAL_FIELDS}


def _derive_watch_status(incoming, prior, transaction_status,
                         snapshot_sha256):
    """Derive count-only status from the exact locked observation pair."""
    signals = _empty_status_signals()
    generation = incoming["observed_at_ns"]
    preserve_equal = transaction_status in ("stale", "unchanged")
    merge_causal = False
    if transaction_status == "baseline":
        state = "baseline"
    elif transaction_status == "stale":
        unseen = set(incoming["all_ids"]) - set(prior["seen_ids"])
        if unseen:
            # The causally newer current snapshot omitted IDs that only a late
            # older observer has now revealed. Attribute one privacy-safe alarm
            # bit to the current generation and merge it idempotently.
            generation = prior["observed_at_ns"]
            signals["causal_removals"] = 1
            state = "actionable"
            merge_causal = True
        else:
            state = "stale"
    elif transaction_status == "unchanged":
        state = "no_warning"
    else:
        current_family = set(incoming["all_ids"]) - set(incoming["singletons"])
        signals["became_recurring"] = len(
            set(prior["singletons"]) & current_family)
        signals["new_corrections"] = len(
            set(incoming["all_ids"]) - set(prior["seen_ids"]))
        signals["new_overrides"] = len(
            set(incoming["override_reflexes"])
            - set(prior["override_reflexes"]))
        state = "actionable" if any(signals.values()) else "no_warning"
    payload = {
        "schema": STATUS_SCHEMA,
        "generation": generation,
        "snapshot_sha256": snapshot_sha256,
        "state": state,
        "signals": signals,
        "carried": False,
    }
    return payload, preserve_equal, merge_causal


def _publish_watch_status_locked(payload, path, preserve_equal=False,
                                 merge_causal=False,
                                 expected_prior_sha256=None,
                                 expected_prior_generation=None,
                                 target_will_publish=True):
    """Monotonically publish one compact status while SNAP's lock is held."""
    incoming, incoming_raw = _encode_watch_status(payload)
    try:
        prior_raw = _read_file_bytes(path, max_bytes=STATUS_MAX_BYTES)
    except FileNotFoundError:
        prior = None
    else:
        prior = _decode_watch_status(prior_raw)

    if prior is not None:
        prior_generation = prior["generation"]
        incoming_generation = incoming["generation"]
        prior_unresolved = (
            prior["state"] == "actionable"
            and (expected_prior_sha256 is None
                 or prior["snapshot_sha256"] != expected_prior_sha256
                 or prior_generation != expected_prior_generation)
        )
        if prior_unresolved and incoming_generation > prior_generation:
            if not target_will_publish:
                # Rebinding an unresolved warning to bytes that are already
                # durable would make digest/generation look resolved before
                # stdout succeeds. Preserve the older evidence until a later
                # transaction has a real snapshot-promotion boundary.
                return prior, False
            # The prior status crossed its atomic-file boundary but its target
            # snapshot never committed. Carry each warning category forward as
            # a conservative lower bound until one complete status -> stdout ->
            # snapshot cycle binds it to the durable snapshot. Max avoids
            # double-counting an event seen again by the newer observation.
            carried = {
                "schema": STATUS_SCHEMA,
                "generation": incoming_generation,
                "snapshot_sha256": incoming["snapshot_sha256"],
                "state": "actionable",
                "signals": {
                    field: max(
                        prior["signals"][field], incoming["signals"][field])
                    for field in _STATUS_SIGNAL_FIELDS
                },
                "carried": True,
            }
            incoming, incoming_raw = _encode_watch_status(carried)
        if prior_generation > incoming_generation:
            if incoming["state"] == "actionable":
                merged = {
                    "schema": STATUS_SCHEMA,
                    "generation": prior_generation,
                    "snapshot_sha256": prior["snapshot_sha256"],
                    "state": "actionable",
                    "signals": {
                        field: max(
                            prior["signals"][field], incoming["signals"][field])
                        for field in _STATUS_SIGNAL_FIELDS
                    },
                    "carried": True,
                }
                merged, merged_raw = _encode_watch_status(merged)
                if merged != prior:
                    _atomic_replace_bytes(path, merged_raw)
                    return merged, True
                return prior, False
            return prior, False
        if merge_causal and prior_generation == incoming_generation:
            # A late stale observation may reveal a removal only after the
            # newer status exists. Only join from the exact current snapshot or
            # an exact retry target; a third digest is an unresolved partial
            # commit and must fail closed.
            if prior["snapshot_sha256"] not in {
                    expected_prior_sha256, incoming["snapshot_sha256"]}:
                raise SnapshotConflictError(
                    "watch status targets a different same-generation snapshot")
            merged = {
                "schema": STATUS_SCHEMA,
                "generation": prior_generation,
                "snapshot_sha256": incoming["snapshot_sha256"],
                "state": "actionable",
                "signals": dict(prior["signals"]),
                "carried": prior["carried"] or incoming["carried"],
            }
            merged["signals"]["causal_removals"] = max(
                1, merged["signals"]["causal_removals"])
            merged, merged_raw = _encode_watch_status(merged)
            if merged == prior:
                return prior, False
            _atomic_replace_bytes(path, merged_raw)
            return merged, True
        if prior_generation == incoming_generation:
            if prior == incoming:
                return prior, False
            same_snapshot = (
                prior["snapshot_sha256"] == incoming["snapshot_sha256"])
            causal_superset = (same_snapshot
                and prior["carried"] == incoming["carried"]
                and all(prior["signals"][field] == incoming["signals"][field]
                        for field in _STATUS_SIGNAL_FIELDS
                        if field != "causal_removals")
                and prior["signals"]["causal_removals"] == 1
                and incoming["signals"]["causal_removals"] == 0)
            carried_superset = (same_snapshot
                and prior["carried"]
                and not incoming["carried"]
                and all(prior["signals"][field] >= incoming["signals"][field]
                        for field in _STATUS_SIGNAL_FIELDS))
            if (causal_superset or carried_superset
                    or (preserve_equal and same_snapshot)):
                return prior, False
            raise SnapshotConflictError(
                "equal watch-status generations disagree; prior preserved")

    _atomic_replace_bytes(path, incoming_raw)
    return incoming, True


def _render_watch_status_suffix(status, force_carried=False):
    """Render only aggregate counts; status never carries private correction text."""
    signals = status["signals"]
    parts = []
    labels = (
        ("new_corrections", "new correction(s)"),
        ("became_recurring", "became recurring"),
        ("new_overrides", "new override(s)"),
    )
    carried = status["carried"] or force_carried
    for field, label in labels:
        if signals[field]:
            parts.append("%s%d %s" % (
                "at least " if carried else "",
                signals[field], label))
    if signals["causal_removals"]:
        parts.append("stale-unseen omission evidence needs review")
    if not parts:
        return ""
    if carried:
        return ("  ** EARLIER UNRESOLVED WATCH STATUS CARRIED FORWARD - "
                "%s.\n" % "; ".join(parts))
    return "  ** LATEST WATCH STATUS ACTIONABLE - %s.\n" % "; ".join(parts)


def _write_all(fd, payload):
    view = memoryview(payload)
    written = 0
    while written < len(view):
        n = os.write(fd, view[written:])
        if n is None or n <= 0:
            raise OSError("snapshot stage write made no progress")
        written += n


def _read_fd_all(fd, max_bytes):
    os.lseek(fd, 0, os.SEEK_SET)
    chunks = []
    total = 0
    while True:
        chunk = os.read(fd, min(65536, max_bytes + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > max_bytes:
            raise SnapshotStateError("snapshot stage grew past its bound")
    return b"".join(chunks)


def _stage_bytes(path, payload):
    """Create one private, collision-proof, same-directory staged inode."""
    parent = os.path.dirname(os.path.abspath(path)) or os.curdir
    prefix = os.path.basename(path) + ".tmp."
    fd, tmp = tempfile.mkstemp(prefix=prefix, dir=parent)
    try:
        _write_all(fd, payload)
        os.fsync(fd)
        if os.fstat(fd).st_size != len(payload):
            raise SnapshotStateError("snapshot stage size verification failed")
        if _read_fd_all(fd, len(payload)) != payload:
            raise SnapshotStateError("snapshot stage readback verification failed")
    except Exception:
        try:
            os.close(fd)
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass
        raise
    os.close(fd)
    return tmp


def _replace_with_retry(tmp, path, retry_seconds=None):
    _retry_windows_io(lambda: os.replace(tmp, path), retry_seconds=retry_seconds)


def _remove_own_stage(tmp):
    try:
        _retry_windows_io(lambda: os.remove(tmp))
    except FileNotFoundError:
        pass
    except OSError:
        # The target is already preserved.  Cleanup remains best-effort so a
        # cleanup error cannot mask the publication error that caused it.
        pass


def _fsync_parent_best_effort(path):
    if os.name == "nt":
        return
    parent = os.path.dirname(os.path.abspath(path)) or os.curdir
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        fd = os.open(parent, flags)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def _atomic_replace_bytes(path, payload):
    tmp = _stage_bytes(path, payload)
    try:
        _replace_with_retry(tmp, path)
        tmp = None
        _fsync_parent_best_effort(path)
    finally:
        if tmp is not None:
            _remove_own_stage(tmp)


def _deliver_watch_block(stream, block):
    """Deliver one complete recurrence verdict before stamping it as observed."""
    if not isinstance(block, str) or not block.strip():
        raise SnapshotDeliveryError(
            "watch renderer did not return a non-empty verdict")
    try:
        written = stream.write(block)
        if type(written) is not int or written != len(block):
            raise SnapshotDeliveryError(
                "watch verdict short write (%r of %d characters)" % (written, len(block))
            )
        stream.flush()
    except SnapshotDeliveryError:
        raise
    except Exception as exc:
        raise SnapshotDeliveryError("watch verdict delivery failed") from exc


def _watch_snapshot_transaction(payload, path=SNAP, renderer=None, stream=None,
                                allow_removals=False, status_path=None):
    """Strictly load and monotonically publish one watch observation.

    The stable path lock covers the final prior read, the generation decision,
    complete verdict delivery, and promotion. Generations are captured before
    source reads, so a slow old scan cannot overwrite a newer scan regardless
    of whether correction IDs were added, removed, or reclassified. Removal
    opt-in guards differences from the last published current snapshot. If a
    stale arrival later reveals an ID omitted by a causally newer snapshot, the
    grow-only history retains it and the stale verdict reports that causal gap;
    fully preventing such a gap requires a writer-cooperative capture journal.

    ``renderer`` and ``stream`` are mandatory even for baseline publication:
    the primitive itself cannot advance delivery-bearing state through an
    output-free helper. A delivery failure leaves the old bytes retryable; a
    later promotion failure may duplicate a delivered warning, but cannot
    silence one.

    A compact Python-owned status is atomically delivered before stdout and
    snapshot promotion. It may therefore be ahead after a later failure, but
    the snapshot can never be ahead of both durable delivery boundaries.
    Status intentionally carries no correction identifiers. If an unresolved
    warning is carried through a divergent later snapshot, its category is
    delivered at least once but its exact seen-ID history cannot be recovered;
    a later restoration may conservatively warn again rather than be silenced.

    Returns ``(previous, status)`` where status is baseline, published,
    unchanged, or stale.  ``previous is None`` means the file was genuinely
    absent; every unreadable/malformed/oversize prior raises instead.
    """
    incoming, _incoming_raw = _encode_snapshot(
        payload, require_fresh_history=True)
    if renderer is None or stream is None:
        raise SnapshotDeliveryError(
            "watch snapshot publication requires an explicit renderer and stream")
    status_target = _status_path_for_snapshot(path, status_path)
    if _paths_alias(status_target, path):
        raise SnapshotDeliveryError(
            "watch status path must differ from the snapshot path")
    with exclusive_append_lock(path, timeout=SNAPSHOT_LOCK_SECONDS):
        prior_raw = None
        try:
            prior_raw = _read_file_bytes(path)
        except FileNotFoundError:
            prior = None
        else:
            prior = _decode_snapshot(prior_raw)

        next_state = incoming
        if prior is not None:
            prior_generation = prior["observed_at_ns"]
            incoming_generation = incoming["observed_at_ns"]
            if incoming_generation < prior_generation:
                status = "stale"
                next_state = dict(prior)
                next_state["seen_ids"] = sorted(
                    set(prior["seen_ids"]) | set(incoming["all_ids"]))
            elif incoming_generation == prior_generation:
                if any(incoming[field] != prior[field]
                       for field in _SNAPSHOT_CURRENT_FIELDS):
                    raise SnapshotConflictError(
                        "equal watch generations disagree; prior preserved"
                    )
                status = "unchanged"
                next_state = prior
            else:
                removed = set(prior["all_ids"]) - set(incoming["all_ids"])
                if removed and not allow_removals:
                    raise SnapshotStateError(
                        "watch observation removes %d correction id(s); "
                        "rerun explicitly with --allow-removals after verifying the source"
                        % len(removed))
                status = "published"
                next_state = dict(incoming)
                next_state["seen_ids"] = sorted(
                    set(prior["seen_ids"]) | set(incoming["all_ids"]))
        else:
            status = "baseline"

        # Derive and bound the exact bytes while still before delivery. A stale
        # observer may contribute grow-only seen-ID history, but can never
        # resurrect its older current classification.
        next_state, next_raw = _encode_snapshot(next_state)
        state_changed = prior is None or next_state != prior
        target_snapshot_raw = next_raw if state_changed else prior_raw
        target_snapshot_sha256 = hashlib.sha256(
            target_snapshot_raw).hexdigest()
        prior_snapshot_sha256 = (
            hashlib.sha256(prior_raw).hexdigest()
            if prior_raw is not None else None)

        try:
            block = renderer(prior, status)
        except SnapshotDeliveryError:
            raise
        except Exception as exc:
            raise SnapshotDeliveryError("watch verdict rendering failed") from exc
        status_payload, preserve_equal, merge_causal = _derive_watch_status(
            incoming, prior, status, target_snapshot_sha256)
        try:
            actual_status, _status_changed = _publish_watch_status_locked(
                status_payload, status_target,
                preserve_equal=preserve_equal, merge_causal=merge_causal,
                expected_prior_sha256=prior_snapshot_sha256,
                expected_prior_generation=(
                    prior["observed_at_ns"] if prior is not None else None),
                target_will_publish=state_changed)
        except Exception as exc:
            raise SnapshotDeliveryError(
                "watch status delivery failed; snapshot preserved") from exc
        if actual_status != _canonical_watch_status(
                status_payload, "derived status"):
            block += _render_watch_status_suffix(
                actual_status,
                force_carried=(
                    actual_status["generation"]
                        != status_payload["generation"]
                    or actual_status["snapshot_sha256"]
                        != status_payload["snapshot_sha256"]))
        _deliver_watch_block(stream, block)

        if not state_changed:
            return prior, status

        try:
            _atomic_replace_bytes(path, next_raw)
        except Exception as exc:
            raise SnapshotPublicationError(
                "verdict delivered but watch state publication failed; prior preserved"
            ) from exc
        return prior, status


def _save_watch_snapshot(payload, enabled, path=SNAP):
    """Report-mode compatibility gate; advancing snapshots require a delivered verdict."""
    if not enabled:
        return False
    raise SnapshotDeliveryError(
        "direct watch snapshot saves are disabled; use the delivery transaction")


def _write_json_artifact(path, payload, indent=1):
    """Publish an explicit --write artifact as one complete serialized file."""
    raw = (json.dumps(payload, ensure_ascii=True, indent=indent) + "\n").encode("ascii")
    if len(raw) > ARTIFACT_MAX_BYTES:
        raise SnapshotStateError("JSON artifact exceeds %d bytes" % ARTIFACT_MAX_BYTES)
    with exclusive_append_lock(path, timeout=SNAPSHOT_LOCK_SECONDS):
        _atomic_replace_bytes(path, raw)
    return True


def _render_watch_block(previous, status, all_ids, singleton_ids, in_family,
                        override_reflexes, recs_by_id):
    """Derive one complete recurrence verdict from the locked prior."""
    lines = ["", "=" * 74, " RECURRENCE WATCH  (change since last snapshot)", "=" * 74]
    if status == "baseline":
        lines.append(
            "  no prior snapshot - baseline observed; publication is pending after this notice."
        )
    elif status == "stale":
        lines.append(
            "  ** STALE OBSERVATION - a newer snapshot is already published; recurrence verdict suppressed."
        )
        unseen = sorted(set(all_ids) - set(previous.get("seen_ids", [])))
        if unseen:
            lines.append(
                "  ** REVIEW REQUIRED - %d stale correction id(s) will be retained in seen-ID history, "
                "but the newer current state excludes them; this reveals a causal removal that could not "
                "be authorized when the newer snapshot first arrived."
                % len(unseen)
            )
    else:
        previous_all = set(previous.get("all_ids", []))
        previous_seen = set(previous.get("seen_ids", previous_all))
        current_all = set(all_ids)
        newly_recurring = sorted(set(previous.get("singletons", [])) & in_family)
        brand_new = sorted(current_all - previous_seen)
        restored = sorted((current_all - previous_all) & previous_seen)
        removed = sorted(previous_all - current_all)
        new_ovr = sorted(
            set(override_reflexes) - set(previous.get("override_reflexes", []))
        )
        removed_ovr = sorted(
            set(previous.get("override_reflexes", [])) - set(override_reflexes)
        )
        became_singletons = sorted(
            (set(singleton_ids) & previous_all)
            - set(previous.get("singletons", []))
        )
        if not (newly_recurring or brand_new or new_ovr):
            lines.append(
                "  no new warning: no brand-new correction, no one-off became recurring, no new override."
            )
        if removed:
            lines.append(
                "  %d correction(s) removed from the current corpus; seen-ID history retained for restore detection."
                % len(removed)
            )
        if restored:
            lines.append(
                "  %d previously seen correction(s) restored from durable history; not counted as new."
                % len(restored)
            )
        if became_singletons:
            lines.append(
                "  %d prior correction(s) are now one-offs; recurrence classification removed."
                % len(became_singletons)
            )
        if removed_ovr:
            lines.append(
                "  %d prior override classification(s) are no longer present."
                % len(removed_ovr)
            )
        if newly_recurring:
            lines.append(
                "  ** A ONE-OFF BECAME RECURRING - author a reflex now (it is no longer a coincidence):"
            )
            for cid in newly_recurring:
                rec = recs_by_id.get(cid)
                if rec:
                    lines.append(
                        "     %-20s %s"
                        % (_ascii(rec["header"])[:20], _ascii(rec["lesson"] or rec["wrong"])[:78])
                    )
        if new_ovr:
            lines.append(
                "  ** NEW OVERRIDE - a reflex the corpus now says fired-yet-landed: %s"
                % ", ".join(new_ovr)
            )
        if brand_new:
            singleton_set = set(singleton_ids)
            novel = [cid for cid in brand_new if cid in singleton_set]
            lines.append(
                "  %d new correction(s) since last run: %d still novel (no sibling), %d fit an existing family."
                % (len(brand_new), len(novel), len(brand_new) - len(novel))
            )
    return "\n".join(lines) + "\n"


def _report_watch_failure(label, exc):
    """Best-effort loud diagnostic on stderr; never mutate watch state here."""
    detail = exc.__cause__ if exc.__cause__ is not None else exc
    block = "\n  ** %s: %s\n" % (label, detail)
    try:
        sys.stderr.write(block)
        sys.stderr.flush()
    except Exception:
        pass


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # Discovery is not execution: this script writes, so a help request must
        # exit before any work. See script_help_flag_can_execute_work.
        print(__doc__ or os.path.basename(__file__))
        return
    write = "--write" in sys.argv
    watch = "--watch" in sys.argv
    allow_removals = "--allow-removals" in sys.argv
    if allow_removals and not watch:
        _report_watch_failure(
            "ARGUMENT FAILED",
            ValueError("--allow-removals is valid only together with --watch"),
        )
        return 1
    try:
        # Generation is captured under the stateful source-pair lock before
        # either source. Completion time never participates in winner ordering.
        run_observed_at_ns, instincts_raw, mistakes_raw = _capture_source_pair(
            watch, verify_pair=write,
            status_path=(STATUS if watch else None))
        reflex_ids = load_reflex_ids(instincts_raw)
        recs, hand = parse_corrections(reflex_ids, mistakes_raw)
    except SourceStateError as exc:
        _report_watch_failure("CORRECTION SOURCE FAILED - no watch or artifact state advanced", exc)
        return 1
    if len(recs) < 10:
        _report_watch_failure(
            "CORRECTION SOURCE FAILED - too few structured corrections; no state advanced",
            SourceStateError("parsed %d corrections" % len(recs)),
        )
        return 1
    recs_by_id = {r["id"]: r for r in recs}

    # TWO views. FULL = reflex + token + scar (the actionable catalog). BLIND = token + scar ONLY (the
    # non-circular test: are the families real, or just the reflex labels fed back to me?).
    try:
        edges_full = build_graph(recs, use_reflex=True)
        edges_blind = build_graph(recs, use_reflex=False)
        comms_full, q_full, G_full = communities(
            recs, edges_full, resolution=CATALOG_RES)
        comms_blind, q_blind, G_blind = communities(
            recs, edges_blind, resolution=SIG_RES)
    except Exception as exc:
        _report_watch_failure(
            "CORRECTION ANALYSIS FAILED - no watch or artifact state advanced", exc)
        return 1
    comms_full = sorted(comms_full, key=len, reverse=True)
    comms_blind = sorted(comms_blind, key=len, reverse=True)

    print("=" * 74)
    print(" MISTAKE GRAPH  -  the corrections corpus as a network")
    print("=" * 74)
    n_cov = sum(1 for r in recs if r["covered"])
    n_unc = len(recs) - n_cov
    print("\nCORPUS  %d structured corrections; %d covered by a live reflex (%.0f%%), %d UNCOVERED"
          % (len(recs), n_cov, 100.0 * n_cov / len(recs), n_unc))
    print("GRAPH   full view %d edges (Q=%.3f) | reflex-blind view %d edges (Q=%s)"
          % (len(edges_full), q_full or 0.0, len(edges_blind),
             ("%.3f" % q_blind) if q_blind is not None else "n/a"))

    # 1. mined families vs the dream's hand-named five
    real_full = [c for c in comms_full if len(c) >= 2]
    real_blind = [c for c in comms_blind if len(c) >= 2]
    singles_full = [c for c in comms_full if len(c) == 1]
    print("\nFAMILIES  full view finds %d communities (size>=2) + %d singletons; the dream hand-named %d"
          % (len(real_full), len(singles_full), len(hand)))
    print("  reflex-blind (labels WITHHELD) still finds %d communities -> the family structure is not just"
          % len(real_blind))
    print("  the reflex tags fed back. Corpus has MORE shapes than the hand-named %d (why p8 missed 07-09)."
          % len(hand))
    bridges = _cross_reflex_bridges(recs_by_id, edges_blind)
    if bridges:
        print("  %d CROSS-REFLEX bridges (token-shape links reflexes the tag-view keeps apart), e.g.:" % len(bridges))
        for a, b, w, ra, rb in sorted(bridges, key=lambda x: -x[2])[:3]:
            print("    %s <-> %s  (w=%.2f)" % ("/".join(ra)[:24], "/".join(rb)[:24], w))

    # 2. RECURRENCE vs null - on the BLIND graph, so the test is not circular with the reflex labels
    try:
        nm = null_modularity(
            G_blind, q_blind, rounds=200, resolution=SIG_RES,
        ) if (G_blind is not None and q_blind is not None) else None
    except Exception as exc:
        _report_watch_failure(
            "CORRECTION ANALYSIS FAILED - no watch or artifact state advanced", exc)
        return 1
    print("\nRECURRENCE  (degree-preserving configuration-model null, on the reflex-BLIND graph)")
    if nm:
        verdict = ("above null - the shared-failure-shape structure is real, not label-induced" if nm["z"] >= 2
                   else "within null noise (small-n) - lean on distinct-date recurrence per family")
        print("  observed Q=%.3f   null Q=%.3f +/- %.3f   z=%.1f  -> %s"
              % (q_blind, nm["mean"], nm["std"], nm["z"], verdict))
    else:
        print("  blind graph too small for a stable null; using distinct-date recurrence per family below.")

    # 3+4+5. per-family (FULL view: reflex tags are the coverage/override unit): coverage, override, priority
    fam_rows = []
    for c in real_full:
        members = sorted(c, key=lambda m: recs_by_id[m]["date"])
        dates = sorted({recs_by_id[m]["date"] for m in members if recs_by_id[m]["date"]})
        covering = sorted({rid for m in members for rid in recs_by_id[m]["named_reflexes"]})
        cands = sorted({cd for m in members for cd in recs_by_id[m]["candidates"]})
        covered = bool(covering)
        # mindware-override, TEXT-GROUNDED: a covered family that the corpus itself says had a reflex which
        # did not fire (not inferred from dates - a reflex is often compiled in RESPONSE to the correction).
        override = covered and any(recs_by_id[m]["fired_but_landed"] for m in members)
        blast = sum(len(recs_by_id[m]["backlinks"]) for m in members)
        n_dates = len(dates)
        gap = 1.0 if not covered else (0.6 if override else 0.15)
        # size enters sublinearly (sqrt) so a coarse Louvain merge can't dominate priority by node count alone;
        # recurrence over distinct DATES is the load-bearing "reoccurring pattern" signal the goal asks for.
        priority = round((n_dates + 1) * (len(members) ** 0.5) * gap, 1)
        verb, mediator = missing_mediator(members, recs_by_id)
        fam_rows.append({
            "members": members, "size": len(members), "dates": dates,
            "covering_reflexes": covering, "candidates": cands, "covered": covered, "override": override,
            "blast_backlinks": blast, "dominant_lesson": dominant_lesson(members, recs_by_id),
            "missing_mediator_verb": verb, "missing_mediator_hint": mediator, "priority": priority,
        })
    fam_rows.sort(key=lambda f: -f["priority"])
    unc_fams = [f for f in fam_rows if not f["covered"]]
    ovr_fams = [f for f in fam_rows if f["override"]]

    # split the uncovered corrections: genuinely novel (a singleton, no sibling) vs sitting inside a family
    # that ALREADY has a reflex (its shape is covered; it just does not itself name the reflex). The second
    # kind is not really a gap - so the actionable gap is smaller than the flat count.
    memb_size = {m: len(c) for c in comms_full for m in c}
    unc_singletons = sum(1 for r in recs if not r["covered"] and memb_size.get(r["id"], 1) == 1)
    unc_infam = n_unc - unc_singletons
    print("\nCOVERAGE  (the honest split: corrections vs families)")
    print("  corrections: %d/%d uncovered (%.0f%%) - but the ACTIONABLE gap is smaller:"
          % (n_unc, len(recs), 100.0 * n_unc / len(recs)))
    print("    %d are novel SINGLETONS (no sibling - the un-generalised tail a 5-shape hand-partition"
          % unc_singletons)
    print("       cannot name; watch each for a sibling, don't pre-reflex a one-off), and")
    print("    %d sit in a family that already has a reflex (shape covered, just individually unlabelled)."
          % unc_infam)
    print("  families (size>=2): %d covered, %d uncovered; %d show EXPLICIT mindware-override"
          % (len(fam_rows) - len(unc_fams), len(unc_fams), len(ovr_fams)))
    print("             (a named reflex the corpus states did not fire): %s"
          % (", ".join(sorted({r for f in ovr_fams for r in f["covering_reflexes"]}))[:70] or "none"))

    print("\nTOP PRIORITY FAMILIES  (recurrence x size x coverage-gap)")
    for f in fam_rows[:5]:
        tag = "UNCOVERED" if not f["covered"] else ("OVERRIDE" if f["override"] else "covered")
        cov = ("cand:" + ",".join(f["candidates"][:2])) if not f["covered"] and f["candidates"] else \
              (",".join(f["covering_reflexes"][:2]) if f["covering_reflexes"] else "-")
        print("  [%-9s p=%5.1f] n=%d dates=%d  %s" % (tag, f["priority"], f["size"], len(f["dates"]), cov))
        print("      shape: %s" % ", ".join(f["dominant_lesson"]))
        print("      missing edge (%s): %s" % (f["missing_mediator_verb"], _ascii(f["missing_mediator_hint"])[:120]))

    # the real gap surface: uncovered CORRECTIONS (mostly singletons), highest-recurrence first
    unc_recs = [r for r in recs if not r["covered"]]
    unc_recs.sort(key=lambda r: -r["date"])
    if unc_recs:
        print("\nUNCOVERED CORRECTIONS  (no live reflex - the reflexes to author next; newest first)")
        for r in unc_recs[:8]:
            cand = (" cand:" + ",".join(r["candidates"][:1])) if r["candidates"] else ""
            print("  %-20s %s%s" % (_ascii(r["header"])[:20], _ascii(r["lesson"] or r["wrong"])[:74], cand))

    # --- RECURRENCE WATCH: the goal's "if it is a reoccurring pattern, fire a warning", as a diff over time.
    # The moment a novel one-off (singleton) gains a sibling, its shape has stopped being a coincidence and
    # become a pattern - that is exactly when a reflex should be authored. Also flags a brand-new override
    # (a reflex the corpus now says fired-yet-landed) and counts new corrections as novel vs fitting a family.
    all_ids = sorted(r["id"] for r in recs)
    singleton_ids = sorted(next(iter(c)) for c in singles_full)
    in_family = set(m for c in real_full for m in c)
    override_reflexes = sorted({r for f in ovr_fams for r in f["covering_reflexes"]})
    watch_state = None
    if watch:
        def render_watch(previous, status):
            return _render_watch_block(
                previous, status, all_ids, singleton_ids, in_family,
                override_reflexes, recs_by_id,
            )

        try:
            _previous_watch, watch_state = _watch_snapshot_transaction(
                {"schema": SNAPSHOT_SCHEMA, "observed_at_ns": run_observed_at_ns,
                 "all_ids": all_ids, "singletons": singleton_ids,
                 "override_reflexes": override_reflexes, "seen_ids": all_ids},
                path=SNAP,
                renderer=render_watch,
                stream=sys.stdout,
                allow_removals=allow_removals,
                status_path=STATUS,
            )
        except SnapshotPublicationError as exc:
            _report_watch_failure(
                "WATCH PUBLICATION FAILED AFTER VERDICT DELIVERY - prior preserved; recurrence remains retryable",
                exc,
            )
            return 1
        except SnapshotDeliveryError as exc:
            _report_watch_failure(
                "WATCH DELIVERY FAILED - prior preserved; recurrence remains retryable",
                exc,
            )
            return 1
        except Exception as exc:
            _report_watch_failure(
                "WATCH STATE FAILED - prior preserved; recurrence verdict suppressed",
                exc,
            )
            return 1

    if write and watch_state == "stale":
        print("\nartifact write skipped: this watch observation is stale; "
              "the newer durable artifact must remain authoritative")
    elif write:
        payload = {
            "_meta": {"n_corrections": len(recs), "n_covered": n_cov, "n_uncovered": n_unc,
                      "n_families": len(real_full), "n_singletons": len(singles_full),
                      "n_uncovered_families": len(unc_fams), "n_override_families": len(ovr_fams),
                      "hand_named": hand, "q_full": q_full, "q_blind": q_blind, "null_blind": nm,
                      "n_cross_reflex_bridges": len(bridges), "edge_threshold": EDGE_THRESHOLD,
                      "note": "read-only catalog; FIRING is graph_fire/ (separate agent). This is reflex CONTENT."},
            "families": fam_rows,
            "uncovered_corrections": [r["id"] for r in unc_recs],
            "corrections": recs,
        }
        try:
            _write_json_artifact(OUT, payload, indent=1)
            print("\nwrote %s (%d families, %d corrections, %d uncovered)" % (OUT, len(fam_rows), len(recs), n_unc))
        except Exception as e:
            _report_watch_failure("ARTIFACT PUBLICATION FAILED", e)
            return 1
    else:
        print("\n(report-only; run with --write to emit mistake_families.json as reflex content)")
    return 0


def _daydiff(a, b):
    """Approx day distance between two YYYYMMDD ints. Only used for relative family recurrence spans."""
    def d(x):
        y, m, dd = x // 10000, (x // 100) % 100, x % 100
        return y * 365 + m * 30 + dd
    return abs(d(b) - d(a))


if __name__ == "__main__":
    raise SystemExit(main())
