#!/usr/bin/env python3
"""Walk the math store's links: a compact map of what lies around a card, and which way to step (claude-4a88bc43-f04,
12 Oct 2026, his 11 Oct 21:5x question: if the agent can walk along edges, with diffusion, and knows how diffusion and the
brain work, can it find the information faster and cheaper?).

    python ~/.claude/dreaming/math_walk.py <card id> [<card id> ...] [--toward "<what you are looking for>"] [--chars 4000]
    python ~/.claude/dreaming/math_walk.py --words "<your question, in a mathematician's words>" [--chars 4000]

A card id may be given by its first characters when they fit one card (the map prints such short ids).

What it prints, one line per card, never whole cards (read a card whole with math_recall.py --id <id>):
  - the card's links grouped by what they mean, from this card's point of view (proved by, special case of, uses,
    used by, refuted by, ...), with the linked cards' titles and ids; CAUTION marks a negative link (refutes,
    fails_under: a counterexample, a refuted route, a pitfall the result needs to avoid);
  - with --toward: the cards within two links that are closest to what you are looking for, ranked by the meaning of
    their text (the store's bge-small claim vectors) plus a small nearness term, each with the path that reaches it;
  - two links away, the strongest diffusion paths: the spreading retrieve() uses (decay 0.55 per link, the same kind
    weights), with a card that has many links passing on less (each step divided by the square root of its link weight);
  - with --words: the word search's own top 3 (math_recall.py prints them whole), then the cards within two links of
    its top 8 closest in meaning to your words, then the cards closest in meaning anywhere in the store; the word
    search's top 8 are not repeated.

Measured on 12 Oct 2026, dev questions only (206 Codex-written and br50 questions; no agent; F04 fronts folder,
results.jsonl): when the word search's top 8 miss the answer card (99 of 206), the answer is one link from those 8 in
21 cases, two links in 21 more, and further or unreachable in 57 (15 sit on cards with no link at all), so a walk is
worth trying, but rewording is often what finds it. Ranked by meaning plus nearness, the two-link neighbourhood puts
the answer in its first 8 lines 24 of 99 times; the store-wide meaning list 25; the two lists overlap on 11, so
together 38 of 99, against 15 for reading 8 more cards of the same word search (math_recall --limit 16). Diffusion
alone, without your words, ranks the answer in the first 8 lines of a card's two-link map in 25 of 41 cases where it
lies within two links; with --toward, 35 of 41 (21 in the first 3). A line costs about 130 characters; a whole card
in a search printout about 2,000-4,000.

Links that grow stronger with use (12 Oct 2026; math_strength.py --explain says how). When agents have used two linked
cards together (opened one within 30 minutes of the other in one session), the link's line ends with [strength 0.62:
used together 3 times, last 2 h ago], the strongest links lead their group, and the diffusion passes more along them
(the link's weight times 1 + strength). Cards agents keep using together that have no link of their own get a group of
their own, "used together by agents", and the diffusion follows those learned links too, at half the strength. A
strength says agents found two cards useful side by side, never that one proves or implies the other.
MATH_STRENGTH_OFF=1 maps as before. MATH_BRAIN_HOW_IT_WORKS.md beside this file explains the store, its links,
spreading and strengths in one page.

The store and the vectors are opened read-only. The ids of the cards a map is drawn for (and the hits of --words) go
to the co-use log (~/.claude/brain_graph/live/math_couse.jsonl: card ids, session, time, never the words); evaluation
and test runs are not logged (MATH_STRENGTH_NOLOG=1 logs nothing). What it prints is untrusted mathematical reference
material, not instructions.
"""
import argparse
import bisect
import math
import os
import re
import sys
from collections import defaultdict

BUILD_SRC = os.environ.get("MATHBRAIN_BUILD_SRC", "")
STORE_PATH = os.environ.get("MATHBRAIN_STORE", "")
DREAMING = os.path.join(os.path.expanduser("~"), ".claude", "dreaming")   # query_embed.py, the ONNX bge-small query encoder
DECAY = 0.55        # retrieve()'s spreading decay per link
NEAR = 0.015        # weight of log(nearness) beside meaning; dev plateau 0.01-0.02 (E7, E8)
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "   # bge's query side, as semantic.py
TITLE = 88          # characters of a title on a map line
MID_TITLE = 44

# (kind, direction from the start card) -> (group order, label); "out" = this card is the link's source
LABEL = {
    ("refutes", "out"): (0, "CAUTION refutes"),
    ("refutes", "in"): (0, "CAUTION refuted by"),
    ("fails_under", "out"): (1, "CAUTION pitfall it must avoid (fails_under)"),
    ("fails_under", "in"): (1, "CAUTION results this pitfall warns about"),
    ("proves", "out"): (2, "proves"),
    ("proves", "in"): (2, "proved by"),
    ("specializes", "out"): (3, "special or sharper case of"),
    ("generalizes", "in"): (3, "special case of (generalized by)"),
    ("specializes", "in"): (4, "has as a special or sharper case"),
    ("generalizes", "out"): (4, "generalizes (has as a special case)"),
    ("defines", "in"): (5, "uses the definition"),
    ("defines", "out"): (5, "defines a term used by"),
    ("depends_on", "out"): (6, "uses (depends on)"),
    ("depends_on", "in"): (7, "used by"),
    ("analogous_to", "out"): (8, "companion (analogous_to; neither implies the other)"),
    ("analogous_to", "in"): (8, "companion (analogous_to; neither implies the other)"),
    ("bridges_to", "out"): (9, "literature bridge to"),
    ("bridges_to", "in"): (9, "literature bridged from"),
    ("verified_by", "out"): (10, "verified by"),
    ("verified_by", "in"): (10, "verifies"),
    ("triggers", "out"): (10, "triggers"),
    ("triggers", "in"): (10, "triggered by"),
    ("semantic_association", "out"): (11, "shares vocabulary (loose; not a proof relation)"),
    ("semantic_association", "in"): (11, "shares vocabulary (loose; not a proof relation)"),
    ("cites", "out"): (12, "cites"),
    ("cites", "in"): (13, "cited by"),
    ("supersedes", "out"): (14, "replaces (older version)"),
    ("supersedes", "in"): (14, "SUPERSEDED by"),
    ("used_together", "out"): (11, "used together by agents (learned from use; not a mathematical relation)"),
}
SHORT_KIND = {"depends_on": "uses", "semantic_association": "vocab", "analogous_to": "companion", "specializes": "specializes",
              "generalizes": "generalizes", "defines": "defines", "proves": "proves", "refutes": "refutes",
              "fails_under": "fails_under", "cites": "cites", "bridges_to": "bridge", "verified_by": "verified_by",
              "triggers": "triggers", "supersedes": "supersedes", "used_together": "used together"}


def _strength_module():
    """math_strength.py beside this file (the co-use strengths and the use log), or None."""
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        if here not in sys.path:
            sys.path.append(here)
        import math_strength
        return math_strength
    except Exception:
        return None


def log_use(tool, use=(), seen=(), store_path=None, lead=()):
    """Record the ids a call mapped (use) or listed as search hits (seen), and the learned partners it printed (lead).
    Never raises; never records the words."""
    ms = _strength_module()
    if ms is not None:
        ms.log_use(tool, use, seen, store_path, lead=lead)


def printed_leads(M, cards, lines):
    """(card, partner) for each learned partner of these cards that the printed lines name: following one of those
    later is the printout's suggestion, not new evidence, so math_strength gives the pair nothing for it."""
    if not M.learned:
        return []
    text = "\n".join(lines)
    return [(c, o) for c in cards for o, _i in M.learned.get(c, ()) if M.short(o) in text or o in text]


def _kind(x):
    return str(getattr(x, "value", x))


def _one(s):
    return " ".join((s or "").split())


class Map:
    def __init__(self, store_path):
        sys.path.insert(0, BUILD_SRC)
        from math_brain.retrieval import EDGE_MULTIPLIER
        from math_brain.store import ResearchStore
        with ResearchStore.open_read_only(store_path) as st:
            self.nodes = list(st.iter_nodes())
            self.edges = list(st.iter_edges(skip_unknown=True))
        self.mult = {_kind(k): v for k, v in EDGE_MULTIPLIER.items()}
        self.by_id = {n.id: n for n in self.nodes}
        self.sorted_ids = sorted(self.by_id)
        self.superseded_by = {e.target_id: e.source_id for e in self.edges if _kind(e.kind) == "supersedes"}
        self.title = {n.id: _one(n.title) for n in self.nodes}
        self.links = defaultdict(list)   # every link, both ends: (other, kind, direction, negative, rationale)
        self.nb = defaultdict(list)      # walkable: current cards only, no supersedes
        for e in self.edges:
            k = _kind(e.kind)
            neg = bool(getattr(e, "is_warning", False))
            why = _one(getattr(e, "rationale", ""))
            self.links[e.source_id].append((e.target_id, k, "out", neg, why))
            self.links[e.target_id].append((e.source_id, k, "in", neg, why))
            if k == "supersedes" or e.source_id in self.superseded_by or e.target_id in self.superseded_by:
                continue
            w = abs(getattr(e, "signed_weight", 1.0) or 1.0) * self.mult.get(k, 0.5)
            self.nb[e.source_id].append((e.target_id, k, "out", neg, w))
            self.nb[e.target_id].append((e.source_id, k, "in", neg, w))
        self.nb0 = self.nb
        self.fan0 = {n: sum(x[4] for x in lst) for n, lst in self.nb0.items()}
        typed = {(e.source_id, e.target_id) for e in self.edges}
        self.typed = lambda x, y: (x, y) in typed or (y, x) in typed  # noqa: E731
        ms = _strength_module()
        self.set_strengths(ms.for_tools(self.typed) if ms is not None else None)
        self._vec = None

    def set_strengths(self, S):
        """Walk with co-use strengths S (a math_strength.Strengths, or None for none). A link agents used together
        passes on weight x (1 + strength); a learned link (no link of its own, used together in two or more separate
        sessions) is walkable at a vocabulary link's weight x 0.5 x strength. Rebuilt from the stored links."""
        self.S, self.learned, self.nb, self.fan = S, {}, self.nb0, self.fan0
        if S is None:
            return
        ms = _strength_module()
        sm = S.strength_map()
        self.nb = defaultdict(list, self.nb0)        # the untouched cards keep their lists (never appended to below)
        touched = set()
        for n, part in sm.items():
            if n in self.nb0:
                self.nb[n] = [(o, k, d, neg, w * (1.0 + part.get(o, 0.0))) for (o, k, d, neg, w) in self.nb0[n]]
                touched.add(n)
        self.learned = S.learned_all(self.typed)
        lw = ms.LEARNED_WEIGHT * self.mult.get("semantic_association", 0.55)
        for n, lst in self.learned.items():
            if n in self.superseded_by or n not in self.by_id:
                continue
            add = [(o, "used_together", "out", False, lw * info["strength"]) for o, info in lst
                   if o not in self.superseded_by and o in self.by_id]
            if add:
                self.nb[n] = list(self.nb.get(n, ())) + add
                touched.add(n)
        self.fan = dict(self.fan0)
        for n in touched:
            self.fan[n] = sum(x[4] for x in self.nb[n])

    def strength(self, a, b):
        return self.S.strength(a, b) if self.S is not None else 0.0

    def tag(self, a, b):
        """'[strength 0.62: used together 3 times, last 2 h ago]' for two cards agents used together, else ''."""
        info = self.S.pair(a, b) if self.S is not None else None
        return _strength_module().tag(info) if info else ""

    # ---- ids ----
    def short(self, nid):
        """the shortest prefix (at least 12 characters) that names one card"""
        for k in range(12, len(nid) + 1):
            p = nid[:k]
            i = bisect.bisect_left(self.sorted_ids, p)
            n = 0
            while i < len(self.sorted_ids) and self.sorted_ids[i].startswith(p) and n < 2:
                n += 1
                i += 1
            if n == 1:
                return p if k < len(nid) else nid
        return nid

    def resolve(self, want):
        want = want.strip().strip(",;.()[]")
        if want in self.by_id:
            return want, None
        hits = [i for i in self.sorted_ids[bisect.bisect_left(self.sorted_ids, want):][:3] if i.startswith(want)] if len(want) >= 6 else []
        if len(hits) == 1:
            return hits[0], None
        return None, ("no card with id %s%s" % (want, " (%d+ cards start with it)" % len(hits) if hits else ""))

    # ---- nearness: the spreading retrieve() does, each step divided by sqrt of the card's link weight ----
    def near(self, seed):
        """{card: (nearness, path)} for every current card within two links of seed; path = [(kind, dir, mid)]"""
        out = {}
        fs = math.sqrt(max(1.0, self.fan.get(seed, 1.0)))
        for (v1, k1, d1, n1, w1) in self.nb.get(seed, ()):
            a = DECAY * w1 / fs
            if a > out.get(v1, (0,))[0]:
                out[v1] = (a, ((k1, d1, None, n1),))
            f1 = math.sqrt(max(1.0, self.fan.get(v1, 1.0)))
            for (v2, k2, d2, n2, w2) in self.nb.get(v1, ()):
                if v2 == seed or v2 == v1:
                    continue
                b = a * DECAY * w2 / f1
                if b > out.get(v2, (0,))[0]:
                    out[v2] = (b, ((k1, d1, v1, n1), (k2, d2, None, n2)))
        out.pop(seed, None)
        return out

    # ---- meaning ----
    def meaning(self, words):
        """{card id: cosine} for every card with a claim vector, or None when the encoder or vectors are missing"""
        try:
            import numpy as np
            if DREAMING not in sys.path:
                sys.path.insert(0, DREAMING)
            import query_embed
            q = query_embed.embed(QUERY_PREFIX + words)
            if q is None:
                return None
            if self._vec is None:
                path = os.environ.get("MATH_BRAIN_CLAIM_VECS") or os.path.join(os.path.dirname(STORE_PATH_USED[0]), "claim_embeddings.npz")
                with np.load(path, allow_pickle=False) as f:
                    v = np.asarray(f["vecs"], dtype=np.float32)
                    ids = [str(x) for x in f["slugs"]]
                v /= np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-9)
                self._vec = (v, ids)
            v, ids = self._vec
            cos = v @ np.asarray(q, dtype=np.float32)
            return {i: float(c) for i, c in zip(ids, cos)}
        except Exception:
            return None

    @staticmethod
    def word_overlap(words, text):
        """fallback when no vectors: share of the question's content words found in the card"""
        toks = {t for t in re.findall(r"[a-z0-9]+", words.lower()) if len(t) > 3}
        if not toks:
            return 0.0
        have = set(re.findall(r"[a-z0-9]+", (text or "").lower()))
        return len(toks & have) / len(toks)

    # ---- printing ----
    def line(self, nid, how="", superseded_note=True):
        t = self.title.get(nid) or nid
        t = t[:TITLE] + ("..." if len(t) > TITLE else "")
        note = ""
        if superseded_note and nid in self.superseded_by:
            note = " SUPERSEDED by %s" % self.short(self.superseded_by[nid])
        return "    %s  (id %s)%s%s" % (t, self.short(nid), note, ("  " + how) if how else "")

    def path_text(self, path):
        parts = []
        for (k, d, mid, neg) in path:
            arrow = "->" if d == "out" else "<-"
            parts.append("%s%s%s" % ("CAUTION " if neg else "", arrow, SHORT_KIND.get(k, k)))
            if mid is not None:
                mt = self.title.get(mid) or mid
                parts.append('"%s" (id %s)' % (mt[:MID_TITLE] + ("..." if len(mt) > MID_TITLE else ""), self.short(mid)))
        return "[%s]" % " ".join(parts)


def ranked_toward(M, cands, words, cos):
    """cands: {card: (nearness, path)} -> [(score, card)] best first; meaning + NEAR * log(nearness)"""
    out = []
    for n, (nr, path) in cands.items():
        if cos is not None:
            c = cos.get(n)
            s = (c if c is not None else -1.0) + NEAR * math.log(nr + 1e-12)
        else:
            node = M.by_id.get(n)
            s = M.word_overlap(words, (node.title or "") + " " + (node.body or "") if node else "") + NEAR * math.log(nr + 1e-12)
        out.append((s, n))
    out.sort(key=lambda x: (-x[0], x[1]))
    return out


class Budget:
    def __init__(self, chars):
        self.left = chars
        self.lines = []

    def add(self, s, force=False):
        if not force and len(s) + 1 > self.left:
            return False
        self.lines.append(s)
        self.left -= len(s) + 1
        return True


def map_card(M, nid, B, toward=None, cos=None, n_toward=10, n_diff=8):
    node = M.by_id[nid]
    mine = M.links.get(nid, [])
    nout = sum(1 for x in mine if x[2] == "out")
    sup = (" SUPERSEDED by %s - its links may be dark; map the current version instead" % M.short(M.superseded_by[nid])
           ) if nid in M.superseded_by else ""
    B.add("[map] %s  (id %s; %s; %d links: %d out, %d in)%s" % (M.title.get(nid, ""), nid, _kind(node.kind), len(mine), nout,
                                                               len(mine) - nout, sup), force=True)
    near = M.near(nid)
    rank = ranked_toward(M, near, toward, cos) if toward else None
    if toward:
        B.add('  closest to "%s" within two links (%s, then nearness):' % (toward[:80], "meaning of the card's text" if cos is not None else "shared words"), force=True)
        shown = 0
        for s, n in rank:
            if shown >= n_toward:
                break
            if not B.add(M.line(n, M.path_text(near[n][1]))):
                break
            shown += 1
        if shown < len(rank):
            B.add("    (%d more within two links)" % (len(rank) - shown))
    # one-link groups
    groups = defaultdict(list)
    for (other, k, d, neg, why) in mine:
        order, label = LABEL.get((k, d), (15, "%s %s" % (k, "->" if d == "out" else "<-")))
        groups[(order, label)].append((other, k, d, neg))
    for other, info in M.learned.get(nid, ()):
        if other in M.by_id:
            groups[LABEL[("used_together", "out")]].append((other, "used_together", "out", False))
    # within a group: toward your words first when --toward is given, then the links agents used most together
    if toward:
        pos = {n: i for i, (s, n) in enumerate(rank)}
        for g in groups.values():
            g.sort(key=lambda x: (pos.get(x[0], 10 ** 6), -M.strength(nid, x[0]), M.title.get(x[0]) or x[0]))
    else:
        for g in groups.values():
            g.sort(key=lambda x: (x[0] in M.superseded_by, -M.strength(nid, x[0]), M.title.get(x[0]) or x[0]))
    keys = sorted(groups)
    if not keys:
        B.add("  no links: nothing can be reached from this card by walking; search again in other words "
              "(math_recall.py, or math_walk.py --words)", force=True)
        return
    B.add("  links, by what they mean from this card:", force=True)
    shown = {k: 0 for k in keys}
    heads = {}
    # round robin: every group gets its header and first line before any group gets a second line
    for k in keys:
        heads[k] = len(B.lines)
        B.add("   %s (%d):" % (k[1], len(groups[k])), force=True)
    body = {k: [] for k in keys}
    progress = True
    while progress:
        progress = False
        for k in keys:
            if shown[k] < len(groups[k]):
                other = groups[k][shown[k]][0]
                ln = M.line(other, M.tag(nid, other))
                if len(ln) + 1 <= B.left - 160:
                    body[k].append(ln)
                    B.left -= len(ln) + 1
                    shown[k] += 1
                    progress = True
    # splice the bodies under their headers
    out, hk = [], {v: k for k, v in heads.items()}
    for i, s in enumerate(B.lines):
        out.append(s)
        if i in hk:
            k = hk[i]
            out.extend(body[k])
            if shown[k] < len(groups[k]):
                more = "    ... %d more%s" % (len(groups[k]) - shown[k], "" if toward else " (add --toward \"...\" to rank them)")
                out.append(more)
                B.left -= len(more) + 1
    B.lines = out
    # strongest diffusion paths two links away
    one = {o for o, _ in M.learned.get(nid, ())}     # one learned link away: listed in its group above
    two = sorted(((v[0], n) for n, v in near.items() if len(v[1]) == 2 and n not in one), key=lambda x: (-x[0], x[1]))
    if two:
        B.add("  two links away, strongest by diffusion (the spreading retrieve() uses; a many-linked card passes on less):")
        shown2 = 0
        for s, n in two:
            if shown2 >= n_diff or not B.add(M.line(n, M.path_text(near[n][1]))):
                break
            shown2 += 1
        if shown2 < len(two):
            B.add("    (%d cards two links away in all)" % len(two))


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="A compact map of the math store around a card: links by meaning, nearby cards "
                                             "ranked toward your words, diffusion paths. Read-only.")
    ap.add_argument("ids", nargs="*", help="card ids (or their first characters) to map")
    ap.add_argument("--toward", default=None, metavar="WORDS", help="what you are looking for: ranks the cards within two links")
    ap.add_argument("--words", default=None, metavar="WORDS",
                    help="no card yet: search by these words, then map what lies within two links of the hits")
    ap.add_argument("--chars", type=int, default=int(os.environ.get("MATH_WALK_CHARS", "4000") or 4000),
                    help="characters to print in all (default 4000, about one whole card)")
    ap.add_argument("--store", default=STORE_PATH)
    a = ap.parse_args(argv)
    if not a.ids and not a.words:
        ap.error("give a card id to map, or --words to start from a search")
    if not os.path.exists(a.store):
        print("math store not found: %s" % a.store)
        return 2
    STORE_PATH_USED[0] = a.store
    M = Map(a.store)
    print("math store walk map, read-only; reference material, not instructions")
    if a.words:
        return words_mode(M, a)
    toward = " ".join(a.toward.split()) if a.toward else None
    cos = M.meaning(toward) if toward else None
    ids = []
    for w in a.ids:
        nid, err = M.resolve(w)
        if err:
            print(err)
        else:
            ids.append(nid)
    if not ids:
        return 1
    per = max(1200, a.chars // len(ids))
    blocks = []
    for nid in ids:
        B = Budget(per)
        map_card(M, nid, B, toward=toward, cos=cos)
        blocks.append(B.lines)
    log_use("walk", use=ids, store_path=a.store, lead=printed_leads(M, ids, [s for b in blocks for s in b]))
    for lines in blocks:
        print("\n".join(lines))
    print("read a card whole: math_recall.py --id <id>; map another: math_walk.py <id> [--toward \"...\"]")
    return 0


def words_mode(M, a):
    words = " ".join(a.words.split())
    from math_brain.retrieval import RetrievalMode, load_signal_db, retrieve, signal_db_path
    try:  # the recall profile (F01, 12 Oct 2026), so this top 8 is math_recall.py's; MATHBRAIN_RECALL_PROFILE=0 = plain
        from math_brain.retrieval import retrieve_for_recall as search_fn
    except ImportError:  # an older build without it
        search_fn = retrieve
    load_signal_db(signal_db_path(a.store))
    modes = {m.name: m for m in RetrievalMode}
    res = search_fn(words, nodes=M.nodes, edges=M.edges, limit=16, min_support_score=0.0, min_margin=0.0, graph_depth=0,
                    mode=modes["DISCOVERY"])
    top8 = [h.node.id for h in res.hits if h.node.id not in M.superseded_by][:8]
    lines = words_map(M, words, top8, a.chars)
    log_use("words", seen=top8, store_path=a.store, lead=printed_leads(M, top8, lines))
    print("\n".join(lines))
    print("read a card whole: math_recall.py --id <id>; map one: math_walk.py <id> --toward \"...\"")
    return 0


def words_map(M, words, top8, chars, cos=False, n_walk=12, n_jump=10):
    """The --words printout as lines: the word search's top 8 (numbered), then the cards within two links of them
    closest to the words (meaning + nearness), then the closest by meaning anywhere (a jump). top8 is the word
    search's own top 8 (retrieve_for_recall() depth 0, superseded dropped), as math_recall.py prints them."""
    B = Budget(chars)
    if cos is False:
        cos = M.meaning(words)
    if not top8:
        B.add("the word search found nothing; the closest cards by meaning anywhere:", force=True)
    else:
        B.add("the word search's top %d (math_recall.py prints them whole; not repeated below):" % len(top8), force=True)
        for i, n in enumerate(top8, 1):
            t = M.title.get(n) or n
            B.add("    [%d] %s  (id %s)" % (i, t[:70] + ("..." if len(t) > 70 else ""), M.short(n)), force=True)
    hit_no = {n: i for i, n in enumerate(top8, 1)}
    cands = {}
    for s in top8:
        for n, v in M.near(s).items():
            if n in hit_no:
                continue
            if v[0] > cands.get(n, (0,))[0]:
                cands[n] = (v[0], v[1], s)
    near = {n: (v[0], v[1]) for n, v in cands.items()}
    rank = ranked_toward(M, near, words, cos)
    walk_budget = B.left // 2 if cos is not None else B.left
    used0 = B.left
    if top8:
        B.add("within two links of those 8, closest to your words (%s, then nearness; [k] is the hit the path starts at):"
              % ("meaning of the card's text" if cos is not None else "shared words"), force=True)
    shown = 0
    for s, n in rank:
        nr, path, seed = cands[n]
        if shown >= n_walk or used0 - B.left > walk_budget or not B.add(M.line(n, "from [%d] %s" % (hit_no[seed], M.path_text(path)))):
            break
        shown += 1
    if shown < len(rank):
        B.add("    (%d cards within two links in all)" % len(rank))
    if cos is not None:
        B.add("closest by meaning anywhere in the store (a jump, not a walk; no link followed):")
        listed = {x[1] for x in rank[:shown]}
        jump = sorted(((c, n) for n, c in cos.items() if n in M.by_id and n not in M.superseded_by and n not in hit_no
                       and n not in listed), key=lambda x: (-x[0], x[1]))
        for c, n in jump[:n_jump]:
            if not B.add(M.line(n)):
                break
    return B.lines


STORE_PATH_USED = [STORE_PATH]

if __name__ == "__main__":
    sys.exit(main())
