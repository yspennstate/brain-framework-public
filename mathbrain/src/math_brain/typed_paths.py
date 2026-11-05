"""Typed paths over the math store: a card's history, its source, its typed links, and a path between two cards
(w15 DESIGNS.md, mechanism 6). Read-only; no model call.

    history <id>   the card's revision family, oldest first, the current version marked, and for each retired version
                   what its successor changed: the status line, hypotheses added or removed, values changed, and the
                   sentences that differ; each retired version says it is retired, by which card, when and why
    source  <id>   where the current version comes from: its provenance row, the record fields, the papers it names
                   (arXiv ids, DOIs) and the cards it cites
    links   <id>   the current version's typed edges, in and out, grouped by kind; an edge whose other end was retired is
                   shown at that end's current version, "via" the retired id (38 such links went dark on 6 Oct)
    path <a> <b>   the shortest chain of typed edges from a to b (depends_on, specializes, generalizes, proves, refutes,
                   fails_under, cites, defines; --undirected to follow them both ways), each hop with its statement

Why. recall serves current cards only and current-node withholds every predecessor ("Only current node/claim text is
returned; predecessor texts are withheld"), so a question about what changed between two versions of a result, or
about the chain of results one depends on, has no answer through the brain's tools. Knowledge updates and revocation are
the weak spot of agent memory (LongMemEval 2410.10813; STALE 2605.06527; revocation study 2609.08258): the history here
shows a retired version only as retired, with its successor beside it.

The text shown is the claim record's. When math_brain is importable, each card the commands print in full (history's
current version, source's and links' card, every card on a path) is also put to the store's own reader
(current_note.read_current_note); a card it refuses, about 1 in 100 on the 6 Oct snapshot (claim_node_statement_mismatch:
the claim's statement and the card body disagree), gets a WARNING line naming the reason. Retired versions, competing
heads and the one-line neighbours in links are not put to the reader.

    python typed_paths.py <db> history|source|links <id>
    python typed_paths.py <db> path <a> <b> [--undirected] [--kinds depends_on,cites]
    python typed_paths.py --selftest
"""
from __future__ import annotations

import collections
import json
import re
import sqlite3
import sys
import unicodedata

PATH_KINDS = ("depends_on", "specializes", "generalizes", "proves", "refutes", "fails_under", "cites", "defines")
_RECORD_LINE = re.compile(r"^(?:Posed (?:in|as)|Refers to the|No independent review|Status as recorded"
                          r"|Current status|Resolution citation|Exclusion status|Supersedes the)\b")
_STATUS = re.compile(r"^\s*\[campaign status:\s*([^\]]+)\]\s*", re.I)
_VALUE = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?(?:/\d+)?(?![\w])")
_ARXIV = re.compile(r"arXiv\s?:?\s?((?:\d{4}\.\d{4,5}|[a-z\-]+(?:\.[A-Z]{2})?/\d{7}))(?:v\d+)?", re.I)
_DOI = re.compile(r"\b(10\.\d{4,9}/[^\s,;)\]]+)")
MAX_SENT = 2
SENT_CHARS = 220


def _norm(s):
    return " ".join((s or "").split())


def _short(s, n=SENT_CHARS):
    s = _norm(s)
    return s if len(s) <= n else s[: n - 3].rsplit(" ", 1)[0] + "..."


class Store:
    """The few reads the commands need, against a read-only connection. `refusal(nid)` is the store's own reader as a
    check ('' when it accepts the card, else its reason; see reader_refusal): the text here comes from the claim record,
    and the reader refuses a card whose claim and card text disagree (about 1 card in 100 on the 6 Oct snapshot)."""

    def __init__(self, con: sqlite3.Connection, refusal=None):
        self.con = con
        self.refusal = refusal
        self._succ = collections.defaultdict(list)    # old -> [(new, edge rationale, created_at)]
        self._pred = collections.defaultdict(list)    # new -> [old]
        for src, tgt, rat, created in con.execute(
                "SELECT source_id, target_id, rationale, created_at FROM edges WHERE kind = 'supersedes'"):
            self._succ[tgt].append((src, rat or "", created or ""))
            self._pred[src].append(tgt)
        # edges retracted as wrong (store.retract_edge), by (source, target, kind); every read below skips them
        self.retracted = frozenset()
        if con.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'edge_retractions'").fetchone():
            self.retracted = frozenset(
                (s, t, k) for s, t, k in con.execute(
                    "SELECT e.source_id, e.target_id, e.kind FROM edge_retractions r JOIN edges e ON e.id = r.edge_id"))

    def node(self, nid):
        r = self.con.execute("SELECT id, kind, title, body, metadata_json, created_at FROM nodes WHERE id = ?",
                             (nid,)).fetchone()
        if not r:
            return None
        meta = {}
        try:
            meta = json.loads(r[4] or "{}")
        except ValueError:
            pass
        claim = {}
        c = self.con.execute("SELECT payload_json FROM claims WHERE id = ?", (nid,)).fetchone()
        if c:
            try:
                claim = json.loads(c[0])
            except ValueError:
                claim = {}
        statement = _norm(claim.get("statement") or r[3])
        hyps = [_norm(a) for a in (claim.get("assumptions") or []) if a and a.strip() and not _RECORD_LINE.match(a.strip())]
        return {"id": r[0], "kind": r[1], "title": _norm(r[2]), "statement": statement, "hypotheses": hyps,
                "meta": meta, "created": (r[5] or "")[:10], "claim_kind": meta.get("claim_kind") or claim.get("kind"),
                "status": meta.get("truth_status") or claim.get("status") or "unknown",
                "trust": meta.get("trust_class") or ""}

    def family(self, nid):
        """Every revision connected to nid by supersedes edges."""
        seen, todo = {nid}, [nid]
        while todo:
            x = todo.pop()
            for y in [s for s, _, _ in self._succ.get(x, [])] + self._pred.get(x, []):
                if y not in seen:
                    seen.add(y)
                    todo.append(y)
        return seen

    def retired(self, nid):
        return bool(self._succ.get(nid))

    def current_of(self, nid, _depth=0):
        """The current version of nid: nid itself when nothing supersedes it, else the head its successors lead to;
        None when the family has competing heads (no reviewed merge: which is current is a review decision)."""
        fam = self.family(nid)
        heads = [x for x in fam if not self._succ.get(x)]
        if len(heads) != 1:
            return None
        return heads[0]

    def order(self, fam):
        """Revisions oldest first: by the supersedes chain where it decides, then by creation time."""
        depth = {}

        def d(x, seen=()):
            if x in depth:
                return depth[x]
            preds = [p for p in self._pred.get(x, []) if p in fam and p not in seen]
            depth[x] = 0 if not preds else 1 + max(d(p, seen + (x,)) for p in preds)
            return depth[x]

        for x in fam:
            d(x)
        return sorted(fam, key=lambda x: (depth[x], (self.node(x) or {}).get("created", ""), x))


def reader_refusal(db):
    """The store's reader (math_brain.current_note) as a check on a card, or None when math_brain cannot be imported."""
    try:
        from math_brain.current_note import read_current_note, NoteReadError
    except ImportError:
        return None

    def check(nid):
        try:
            read_current_note(db, nid)
            return ""
        except NoteReadError as exc:
            return str(exc) or "refused"
        except Exception as exc:        # an optional check must never break the command; it is still a warning
            return "reader_error_" + type(exc).__name__
    return check


def _refused(st, nid, indent="  "):
    """A warning line when the store's reader refuses the card, else []."""
    why = st.refusal(nid) if st.refusal else ""
    return ["%sWARNING: the store's own reader refuses %s (%s); the text shown comes from the claim record and was not "
            "checked against the card." % (indent, nid, why)] if why else []


def _sentences(text):
    return [s.strip() for s in re.split(r"(?<=[.;])\s+(?=[A-Z(\[])", text or "") if s.strip()]


_HWORD = re.compile(r"[a-z0-9]+")
_STOP = frozenset("a an the of in on at to for with and or is are be by as it its that this any each all".split())
_OPS = re.compile(r"\s*([<>=+*/^,;:|])\s*")


def _fold(s):
    """Lower case without accents (Ito = Itô, Erdos = Erdős)."""
    return "".join(c for c in unicodedata.normalize("NFKD", (s or "").lower()) if not unicodedata.combining(c))


def _htoks(s):
    """Content words of a hypothesis: lower case, hyphenated words joined (non-negative = nonnegative), no stop words."""
    return {t for t in _HWORD.findall(_fold(s).replace("-", "")) if t not in _STOP}


def _canon(s):
    s = _fold(s)
    for a, b in (("≥", ">="), ("≤", "<="), ("−", "-"), ("–", "-"), ("—", "-")):
        s = s.replace(a, b)
    s = re.sub(r"(?<=[a-z])-(?=[a-z])", "", s)
    s = re.sub(r"([(\[{])\s+", r"\1", re.sub(r"\s+([)\]}])", r"\1", _OPS.sub(r"\1", s)))
    return " ".join(s.split())


def _said_in(h, text):
    """Does `text` still say hypothesis `h`: its normalised form inside the text, or 90 per cent of its words of three
    or more letters (at least two of them) present."""
    c = _canon(h).rstrip(" .")
    if c and re.search(("(?<![a-z0-9])" if c[0].isalnum() else "") + re.escape(c)
                       + ("(?![a-z0-9])" if c[-1].isalnum() else ""), _canon(text)):
        return True
    w = {x for x in _htoks(h) if len(x) >= 3}
    return len(w) >= 2 and len(w & _htoks(text)) >= 0.9 * len(w)


def _overlap(a, b):
    ta, tb = _htoks(a), _htoks(b)
    return (len(ta & tb), len(ta & tb) / min(len(ta), len(tb))) if ta and tb else (0, 0.0)


def _diff_hypotheses(old, new, body_o, body_n):
    """Hypothesis lines. A dropped condition is "removed" only when the newer version no longer says it anywhere: one
    reworded in the newer list is shown old -> new, and one the newer statement now carries is listed as moved. On the
    dev families 50 of 56 plain "removed" lines were one of those two (dev/paths6/removed_labels_dev.tsv)."""
    ho, hn = set(old["hypotheses"]), set(new["hypotheses"])
    gone = [h for h in old["hypotheses"] if h not in hn]
    came = [g for g in new["hypotheses"] if g not in ho]
    pairs = sorted(((-r, -k, i, j) for i, h in enumerate(gone) for j, g in enumerate(came)
                    for k, r in [_overlap(h, g)] if k >= 2 and r >= 0.5))
    match, used = {}, set()
    for _, _, i, j in pairs:
        if i not in match and j not in used:
            match[i] = j
            used.add(j)
    new_list = " ; ".join(new["hypotheses"])
    new_all = body_n + " ; " + new["title"] + " ; " + new_list
    old_all = body_o + " ; " + old["title"] + " ; " + " ; ".join(old["hypotheses"])
    rewritten, removed, moved = [], [], []
    for i, h in enumerate(gone):
        if i in match:
            rewritten.append((h, came[match[i]]))
        elif new_list and _said_in(h, new_list) and not _said_in(h, body_n + " ; " + new["title"]):
            rewritten.append((h, max(new["hypotheses"], key=lambda g: _overlap(h, g)[1])))
        elif _said_in(h, new_all):
            moved.append(h)
        else:
            removed.append(h)
    added = [g for j, g in enumerate(came) if j not in used and not _said_in(g, old_all)]
    listed = [g for j, g in enumerate(came) if j not in used and _said_in(g, old_all)]
    out = ["hypothesis removed: " + _short(h, 200) for h in removed[:4]]
    out += ["hypothesis added: " + _short(g, 200) for g in added[:4]]
    out += ["hypothesis rewritten: %s -> %s" % (_short(h, 120), _short(g, 120)) for h, g in rewritten[:4]]
    if moved:
        out.append("no longer a separate hypothesis, the newer statement still says it: "
                   + "; ".join(_short(h, 120) for h in moved[:4]))
    if listed:
        out.append("now listed as a hypothesis, the older version already said it: "
                   + "; ".join(_short(g, 120) for g in listed[:4]))
    return out


def diff_versions(old, new):
    """What `new` changed relative to `old`: lines, plain text."""
    out = []
    so = _STATUS.match(old["statement"])
    sn = _STATUS.match(new["statement"])
    s_old, s_new = (so.group(1).strip() if so else ""), (sn.group(1).strip() if sn else "")
    if s_old != s_new:
        out.append("status line: %s -> %s" % (s_old or "(none)", s_new or "(none)"))
    if old["status"] != new["status"]:
        out.append("record status: %s -> %s" % (old["status"], new["status"]))
    body_o = _STATUS.sub("", old["statement"])
    body_n = _STATUS.sub("", new["statement"])
    out += _diff_hypotheses(old, new, body_o, body_n)
    # a value counts as removed only when the newer version says it nowhere (statement, hypotheses or title)
    vo = collections.Counter(_VALUE.findall(" ; ".join([body_o] + old["hypotheses"] + [old["title"]])))
    vn = collections.Counter(_VALUE.findall(" ; ".join([body_n] + new["hypotheses"] + [new["title"]])))
    gone = [v for v in collections.Counter(_VALUE.findall(body_o)) if v not in vn]
    came = [v for v in collections.Counter(_VALUE.findall(body_n)) if v not in vo]
    if gone or came:
        out.append("values: removed %s; added %s" % (", ".join(gone[:8]) or "none", ", ".join(came[:8]) or "none"))
    if _norm(body_o) != _norm(body_n):
        so_set, sn_set = set(_sentences(body_o)), set(_sentences(body_n))
        rem = [s for s in _sentences(body_o) if s not in sn_set][:MAX_SENT]
        add = [s for s in _sentences(body_n) if s not in so_set][:MAX_SENT]
        for s in rem:
            out.append("text removed: " + _short(s))
        for s in add:
            out.append("text added: " + _short(s))
    if old["title"] != new["title"]:
        out.append("title: %s -> %s" % (_short(old["title"], 120), _short(new["title"], 120)))
    return out or ["no change in statement, hypotheses or status (a merge of duplicates or a metadata revision)"]


def history(st: Store, nid: str) -> str:
    n = st.node(nid)
    if not n:
        return "no such node: %s" % nid
    fam = st.family(nid)
    cur = st.current_of(nid)
    if len(fam) == 1:
        return "\n".join(["%s has one version; it is current." % nid] + _refused(st, nid, "") + [_card_line(n, full=True)])
    order = st.order(fam)
    lines = ["%d versions of this result, oldest first. %s" % (
        len(order), ("Current: %s." % cur) if cur else "COMPETING HEADS: no version is current until a review merges them.")]
    by_id = {x: st.node(x) for x in order}
    for i, x in enumerate(order, 1):
        v = by_id[x]
        if x == cur:
            lines.append("v%d CURRENT %s (%s, %s; %s)" % (i, x, v["created"], v["claim_kind"], v["status"]))
            lines += _refused(st, x)
            lines.append("  " + _card_line(v, full=True))
        elif not st._succ.get(x):
            lines.append("v%d COMPETING HEAD %s (%s, %s; %s) - no reviewed merge; unsettled, not current." % (
                i, x, v["created"], v["claim_kind"], v["status"]))
            lines.append("  " + _card_line(v, full=True))
        else:
            succ = st._succ.get(x, [])
            by = ", ".join("%s on %s" % (s, (c or "")[:10]) for s, _, c in succ[:2])
            why = _short(succ[0][1], 200) if succ else ""
            lines.append("v%d RETIRED %s (%s) - superseded by %s. Reason recorded: %s. Do not use it as current." % (
                i, x, v["created"], by, why or "none"))
            lines.append("  title then: " + _short(v["title"], 160))
            newer = [s for s, _, _ in succ if s in by_id]
            if newer:
                lines.append("  what v%d changed:" % (order.index(newer[0]) + 1))
                for d in diff_versions(v, by_id[newer[0]]):
                    lines.append("    - " + d)
    return "\n".join(lines)


def _resolve(st: Store, nid):
    """(the id to show, a warning line or ""): never show a retired card as if it were current."""
    cur = st.current_of(nid)
    if cur is not None:
        return cur, ""
    heads = sorted(x for x in st.family(nid) if not st._succ.get(x))
    return nid, ("WARNING: %s%s belongs to a family with competing heads (%s); no version is current until a review "
                 "merges them." % (nid, " is RETIRED and" if st.retired(nid) else "", ", ".join(heads)))


def _card_line(v, full=False):
    text = v["statement"] if full else _short(v["statement"], 300)
    s = "%s [%s]: %s" % (_short(v["title"], 160), v["claim_kind"] or v["kind"], text)
    if v["hypotheses"] and full:
        s += " | Conditions: " + "; ".join("(%d) %s" % (i, _short(h, 200)) for i, h in enumerate(v["hypotheses"], 1))
    return s


def source(st: Store, nid: str) -> str:
    cur, warn = _resolve(st, nid)
    v = st.node(cur)
    if not v:
        return "no such node: %s" % nid
    lines = ["source of %s%s" % (cur, (" (current version of %s)" % nid) if cur != nid else "")]
    if warn:
        lines.append("  " + warn)
    lines += _refused(st, cur)
    lines.append("  " + _card_line(v))
    row = st.con.execute("SELECT * FROM claim_provenance WHERE claim_id = ?", (cur,)).fetchone()
    if row:
        cols = [d[0] for d in st.con.execute("SELECT * FROM claim_provenance LIMIT 1").description]
        rec = dict(zip(cols, row))
        keep = {k: rec[k] for k in ("evidence_class", "source_kind", "source_ref", "bundle_id", "status", "measurement")
                if rec.get(k)}
        lines.append("  provenance: " + json.dumps(keep, ensure_ascii=False))
    m = v["meta"]
    fields = {k: m.get(k) for k in ("logical_id", "provenance", "citation", "source", "source_id", "campaign",
                                    "trust_class", "truth_status", "statement_fidelity_reviewed", "bundle_id",
                                    "filed_by", "filed_on", "audit_status") if m.get(k) not in (None, "", [], {})}
    if fields:
        lines.append("  record: " + json.dumps(fields, ensure_ascii=False)[:600])
    refs = sorted(set(_ARXIV.findall(v["statement"] + " " + " ".join(v["hypotheses"]))))
    dois = sorted(set(_DOI.findall(v["statement"])))
    if refs or dois:
        lines.append("  papers named: " + ", ".join(["arXiv:" + r for r in refs] + ["doi:" + d for d in dois]))
    for kind in ("cites", "proves", "refutes"):
        outs = st.con.execute("SELECT target_id FROM edges WHERE kind = ? AND source_id = ?", (kind, cur)).fetchall()
        outs = [o for o in outs if (cur, o[0], kind) not in st.retracted]
        for (t,) in outs[:6]:
            tv = st.node(st.current_of(t) or t)
            if tv:
                lines.append("  %s: %s (%s)" % (kind, _short(tv["title"], 140), tv["id"]))
    return "\n".join(lines)


def _edges_of(st: Store, nid, direction):
    col, other = ("source_id", "target_id") if direction == "out" else ("target_id", "source_id")
    rows = st.con.execute("SELECT kind, %s, rationale FROM edges WHERE %s = ? AND kind != 'supersedes'" % (other, col),
                          (nid,)).fetchall()
    if direction == "out":
        return [r for r in rows if (nid, r[1], r[0]) not in st.retracted]
    return [r for r in rows if (r[1], nid, r[0]) not in st.retracted]


def links(st: Store, nid: str) -> str:
    cur, warn = _resolve(st, nid)
    v = st.node(cur)
    if not v:
        return "no such node: %s" % nid
    ids = [cur] + [x for x in st.family(cur) if x != cur]       # edges recorded on retired versions still count
    groups = collections.OrderedDict()
    for direction in ("out", "in"):
        for x in ids:
            for kind, other, _rat in _edges_of(st, x, direction):
                oc = st.current_of(other) or other
                key = (direction, kind)
                via = (" via retired %s" % other) if oc != other else (
                    " (competing heads, not current)" if st.current_of(other) is None else "")
                ov = st.node(oc)
                if not ov:
                    continue
                entry = "%s (%s)%s" % (_short(ov["title"], 120), oc, via)
                groups.setdefault(key, [])
                if entry not in groups[key]:
                    groups[key].append(entry)
    lines = ["typed links of %s: %s" % (cur, _short(v["title"], 140))] + (["  " + warn] if warn else []) + _refused(
        st, cur)
    for (direction, kind), entries in groups.items():
        arrow = "->" if direction == "out" else "<-"
        lines.append("  %s %s (%d)" % (arrow, kind, len(entries)))
        for e in entries[:8]:
            lines.append("      " + e)
        if len(entries) > 8:
            lines.append("      (+%d more)" % (len(entries) - 8))
    return "\n".join(lines)


def path(st: Store, a: str, b: str, kinds=PATH_KINDS, undirected=False, max_depth=5) -> str:
    a0, b0 = a, b
    a, b = st.current_of(a) or a, st.current_of(b) or b
    adj = collections.defaultdict(list)
    for kind, s, t in st.con.execute("SELECT kind, source_id, target_id FROM edges WHERE kind IN (%s)"
                                     % ",".join("?" * len(kinds)), list(kinds)):
        if (s, t, kind) in st.retracted:
            continue
        s, t = st.current_of(s) or s, st.current_of(t) or t
        adj[s].append((t, kind, "->"))
        if undirected:
            adj[t].append((s, kind, "<-"))
    prev = {a: None}
    frontier = [a]
    for _ in range(max_depth):
        nxt = []
        for x in frontier:
            for y, kind, arrow in adj.get(x, []):
                if y not in prev:
                    prev[y] = (x, kind, arrow)
                    nxt.append(y)
        frontier = nxt
        if b in prev:
            break
    if b not in prev:
        return "no path of at most %d typed edges (%s%s) from %s to %s" % (
            max_depth, ",".join(kinds), ", undirected" if undirected else "", a, b)
    hops, x = [], b
    while prev[x] is not None:
        p, kind, arrow = prev[x]
        hops.append((p, kind, arrow, x))
        x = p
    hops.reverse()
    lines = ["path of %d hop(s):" % len(hops)]
    for end in sorted({a0, b0}):
        w = _resolve(st, end)[1]
        if w:
            lines.append("  " + w)
    va = st.node(a)
    lines += _refused(st, a)
    lines.append("  " + _card_line(va))
    for p, kind, arrow, x in hops:
        lines.append("  %s %s" % (arrow, kind))
        lines += _refused(st, x)
        lines.append("  " + _card_line(st.node(x)))
    return "\n".join(lines)


# ------------------------------------------------------------------------------------------------ selftest
def _toy():
    con = sqlite3.connect(":memory:")
    con.executescript("""
        CREATE TABLE nodes (id TEXT PRIMARY KEY, kind TEXT, title TEXT, body TEXT, metadata_json TEXT, created_at TEXT);
        CREATE TABLE claims (id TEXT PRIMARY KEY, payload_json TEXT);
        CREATE TABLE edges (id TEXT, source_id TEXT, target_id TEXT, kind TEXT, rationale TEXT, created_at TEXT);
        CREATE TABLE claim_provenance (claim_id TEXT PRIMARY KEY, evidence_class TEXT, source_kind TEXT,
            source_ref TEXT, bundle_id TEXT, status TEXT, measurement TEXT);
    """)

    def add(i, title, st, hyps, created, status="unverified"):
        con.execute("INSERT INTO nodes VALUES (?,?,?,?,?,?)", (i, "claim", title, st, json.dumps(
            {"claim_kind": "theorem", "truth_status": status}), created))
        con.execute("INSERT INTO claims VALUES (?,?)", (i, json.dumps({"statement": st, "assumptions": hyps})))

    add("c_old", "Bound A", "The constant is 270 for s_5.", ["X_i independent"], "2026-09-01T00:00:00")
    add("c_new", "Bound A", "[campaign status: partial_result] The constant is 128 for s_5.",
        ["X_i independent", "densities bounded by K"], "2026-09-10T00:00:00")
    add("c_dep", "Lemma B", "A lemma used by Bound A.", [], "2026-09-02T00:00:00")
    add("c_far", "Lemma C", "A lemma used by Lemma B. See arXiv:2005.08930.", [], "2026-09-02T00:00:00")
    con.execute("INSERT INTO edges VALUES ('e1','c_new','c_old','supersedes','corrected constant','2026-09-10')")
    con.execute("INSERT INTO edges VALUES ('e2','c_old','c_dep','depends_on','uses','2026-09-02')")
    con.execute("INSERT INTO edges VALUES ('e3','c_dep','c_far','depends_on','uses','2026-09-02')")
    con.execute("INSERT INTO claim_provenance VALUES ('c_new','curated','paper','arXiv:2005.08930','b1','ok','')")
    return con


def selftest() -> int:
    ok = True

    def check(name, cond):
        nonlocal ok
        if not cond:
            print("FAIL", name)
            ok = False

    st = Store(_toy())
    h = history(st, "c_old")
    check("current marked", "v2 CURRENT c_new" in h and "v1 RETIRED c_old" in h)
    check("status diff", "status line: (none) -> partial_result" in h)
    check("hypothesis diff", "hypothesis added: densities bounded by K" in h)
    check("value diff", "values: removed 270; added 128" in h)
    check("retired says so", "Do not use it as current" in h)
    check("single version", "one version; it is current" in history(st, "c_dep"))
    lk = links(st, "c_new")
    check("dark link resolved", "Lemma B (c_dep)" in lk and "-> depends_on" in lk)
    p = path(st, "c_new", "c_far")
    check("path two hops", p.startswith("path of 2 hop(s)") and "Lemma C" in p)
    check("no path backwards", path(st, "c_far", "c_new").startswith("no path"))
    check("undirected path", path(st, "c_far", "c_new", undirected=True).startswith("path of 2 hop(s)"))
    s = source(st, "c_old")
    check("source of current", "source of c_new (current version of c_old)" in s and "arXiv:2005.08930" in s)
    con = _toy()
    con.execute("INSERT INTO nodes VALUES ('c_alt','claim','Bound A','The constant is 200 for s_5.','{}','2026-09-11')")
    con.execute("INSERT INTO claims VALUES ('c_alt', '{\"statement\": \"The constant is 200 for s_5.\"}')")
    con.execute("INSERT INTO edges VALUES ('e4','c_alt','c_old','supersedes','another correction','2026-09-11')")
    st2 = Store(con)
    h2 = history(st2, "c_old")
    check("competing heads named", "COMPETING HEADS" in h2 and "COMPETING HEAD c_alt" in h2
          and "COMPETING HEAD c_new" in h2 and "RETIRED c_new" not in h2)
    s2 = source(st2, "c_old")
    check("source warns on a retired card with competing heads", "c_old is RETIRED" in s2 and "WARNING" in s2)
    check("links warn", "WARNING" in links(st2, "c_old"))
    check("path warns", "WARNING" in path(st2, "c_old", "c_far"))
    check("one head still clean", "WARNING" not in source(st, "c_old") and "WARNING" not in links(st, "c_new"))

    def v(stmt, hyps, title="T"):
        return {"statement": stmt, "hypotheses": hyps, "title": title, "status": "unverified"}
    d = diff_versions(v("Then S_n converges.", ["t >= 0", "f nonnegative with f(j) = O(j^k)"]),
                      v("For t ≥ 0 and f non-negative with f(j) = O(j^k), S_n converges.", []))
    check("moved into the statement, not removed", not any(x.startswith("hypothesis removed") for x in d)
          and any(x.startswith("no longer a separate hypothesis") and "t >= 0" in x for x in d))
    d = diff_versions(v("Then the limit is integrable.", ["f_n measurable and non-negative"]),
                      v("Then the limit is integrable.", ["f_n measurable, nonnegative, pointwise nondecreasing"]))
    check("reworded shown old -> new", d == ["hypothesis rewritten: f_n measurable and non-negative -> f_n measurable, "
                                             "nonnegative, pointwise nondecreasing"])
    d = diff_versions(v("PA is consistent.", ["Goedel's Second Incompleteness Theorem", "X_i independent"]),
                      v("PA is consistent.", ["X_i independent"]))
    check("real removal kept", d == ["hypothesis removed: Goedel's Second Incompleteness Theorem"])
    d = diff_versions(v("The bound holds with constant 270.", []), v("The bound holds.", ["constant 270"]))
    check("value moved into a hypothesis is not removed", not any(x.startswith("values:") for x in d)
          and any(x.startswith("no longer a separate") is False and "now listed" in x for x in d))
    d = diff_versions(v("Let the kernel be fixed.", ["C >= 0"]), v("Let the kernel be fixed.", ["t > 0"]))
    check("short real change", d == ["hypothesis removed: C >= 0", "hypothesis added: t > 0"])
    st3 = Store(_toy(), refusal=lambda nid: "claim_node_statement_mismatch" if nid == "c_new" else "")
    w = "WARNING: the store's own reader refuses c_new (claim_node_statement_mismatch)"
    h3, s3, l3, p3 = history(st3, "c_old"), source(st3, "c_old"), links(st3, "c_old"), path(st3, "c_new", "c_far")
    check("reader refusal warned everywhere the card is shown", all(w in x for x in (h3, s3, l3, p3)))
    check("refusal adds lines only", [x for x in h3.splitlines() if "WARNING" not in x] == h.splitlines()
          and [x for x in s3.splitlines() if "WARNING" not in x] == s.splitlines())
    check("accepted cards carry no reader warning", "WARNING" not in path(st3, "c_dep", "c_far")
          and "WARNING" not in history(st3, "c_dep"))
    print("selftest", "passed" if ok else "FAILED")
    return 0 if ok else 1


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--selftest" in argv:
        return selftest()
    if len(argv) < 3:
        print(__doc__)
        return 2
    db, cmd = argv[0], argv[1]
    con = sqlite3.connect("file:%s?mode=ro" % db.replace("\\", "/"), uri=True, timeout=10)
    st = Store(con, refusal=reader_refusal(db))
    if cmd == "history":
        print(history(st, argv[2]))
    elif cmd == "source":
        print(source(st, argv[2]))
    elif cmd == "links":
        print(links(st, argv[2]))
    elif cmd == "path" and len(argv) >= 4:
        kinds = PATH_KINDS
        if "--kinds" in argv:
            kinds = tuple(argv[argv.index("--kinds") + 1].split(","))
        print(path(st, argv[2], argv[3], kinds=kinds, undirected="--undirected" in argv))
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
