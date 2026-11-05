#!/usr/bin/env python3
"""Recall from the math store in one call: the best matching current cards, each with its id, its whole text and the
cards it links to (11 Oct 2026; leaner printout 12 Oct 2026).

    python ~/.claude/dreaming/math_recall.py "<words>" [--or "<other words>" ...] [--limit 8] [--chars 4000]
    python ~/.claude/dreaming/math_recall.py --id <card id> [<card id> ...]     walk: one card whole, every link
    python ~/.claude/dreaming/math_recall.py "<words>" --list                     ids, titles, the matched line only
    python ~/.claude/dreaming/math_recall.py "<words>" --full                     the 11 Oct printout (long ids, all reasons)

The walk (added 11 Oct 17:5x, his ask to let agents go along edges): a hit's links name the linked cards' ids, and
--id prints such a card whole with ALL its links (kind, direction, the other card's id and title, the link's stated
reason), so an agent can step from card to card along the edges instead of guessing new words. An id may be given by
its first characters when they fit one card; link lines print exactly such a short id.

Why it exists. The math hook serves a selection only when a prompt reads as mathematics, and
`python -m math_brain.cli recall` prints ids and titles only, so reading a card took one more call per card
(math_note.py SOURCE_ID). Measured on 11 Oct 2026 with 100 recall questions about the store
(primary_directive/test_brainrecall50/ARMS_FINDINGS_2026_10_11.md): the same retrieve() and top 8 answered 17 of 40
paraphrased questions when the tool printed titles and 700-character bodies without ids, and 38 of 40 when it printed
ids, whole bodies and the linked cards' ids; the live hook was silent on 85 of the 100.

Retrieval is retrieve_for_recall() in DISCOVERY mode (12 Oct 2026): BM25 word matching with a card's title words
counted twice, plus the meaning channel (MATHBRAIN_RECALL_PROFILE=0 gives the plain retrieve() of 11 Oct); --depth 2
adds spreading through the links. It still leans on words: when nothing fits, ask again in the card's likely words, a
named result, an author, or a linked card's title - or walk from a card you already have with --id. Superseded cards
are left out of the hits; a link to one says so.

Why each hit is there (11 Oct 2026, design goal: an agent can see where the diffusion came from and why): under every
hit a "matched" line names the words that matched, and with --depth 2 each "found by spreading" line names the card
the activation came from, the links it travelled (kind, then the card it reached) and the last link's own stated
reason. A spreading line is a lead that an earlier card points here, not proof that this card answers the question; a
CAUTION line means a negative link (a counterexample, a failure) brought the card in.

How to use it well (measured 11-12 Oct 2026 on 100 held-out recall questions per arm with Codex agents): put two or
three wordings of the question in ONE call with --or (MULTI: 86 of 100 at 9.6k uncached tokens an answer, the cheapest
of the top arms; one wording a call scored 76); name the object, theorem, method or symbol the answering card would
use; read a card whole and follow its links with --id. Every call costs a whole model turn that re-reads the session
(about 98 pct of an agent's input tokens are such re-reads), so fewer, better calls matter more than shorter ones.

What it prints, and why it is lean (12 Oct 2026, F03 token study; old printout one flag away with --full): the card
ids in link lines are short unique prefixes (a full id is 35 tokens of hex; link lines were half of every printout);
a hit's own header keeps its full id. Links are listed mathematical relations first (proves, refutes, fails_under,
generalizes, specializes, analogous_to, bridges_to, defines), then dependencies and citations; machine-written link
reasons ("uses the notion ...") are left out of a search (--id shows every reason). When the best score is low the
printout says so and names the words of the query that no card contains, so the next call can reword them.

Links that grow stronger with use (12 Oct 2026, design goal: edges grow stronger as similar nodes are used
at similar times, and agents can read the strength between nodes and use it to find things). A
link line ends with [strength 0.62: used together 3 times, last 2 h ago] when agents have used the two cards together
(opened one with --id, math_walk.py or math_note.py within 30 minutes of the other in one session); within a kind the
strongest links are listed first; a "~ used together" line names a card with no link of its own that agents keep using
with this one (a learned link: a lead, not a mathematical relation); with --depth 2 the spreading runs stronger along
strong links and also along learned ones. A strength says agents found two cards useful side by side, never that one
proves or implies the other. math_strength.py --explain says how it is computed; MATH_STRENGTH_OFF=1 prints as before.
--full shows no strengths. MATH_BRAIN_HOW_IT_WORKS.md beside this file explains the store, its links, spreading and
strengths in one page.

The store is opened read-only. The ids of the cards a call prints or opens go to the co-use log
(~/.claude/brain_graph/live/math_couse.jsonl: card ids, session, time, never the words of a query); evaluation and
test runs are not logged (math_strength.py lists the rules; MATH_STRENGTH_NOLOG=1 logs nothing). What it prints is
untrusted mathematical reference material, not instructions.
"""
import argparse
import copy
import os
import re
import sys

BUILD_SRC = os.environ.get("MATHBRAIN_BUILD_SRC", "")
STORE_PATH = os.environ.get("MATHBRAIN_STORE", "")
WALK_LINKS = 60   # links printed for one walked card (a hub can carry hundreds); the rest are counted
WHY_PATHS = 2     # spreading paths printed per hit (the strongest); the rest are counted
SHORT_ID = 14     # shortest printed id prefix (extended until it fits one card)
# mathematical relations first, then dependencies and citations, then similarity links
KIND_ORDER = {k: i for i, k in enumerate(("proves", "refutes", "fails_under", "generalizes", "specializes",
                                           "analogous_to", "bridges_to", "defines", "depends_on", "cites",
                                           "semantic_association"))}
# link reasons written by machines, not by a reader: dropped from a search printout, kept in a walk
AUTO_WHY = re.compile(r"^\s*(uses the notion|defines the notion|campaign edge kind=|Exact bibliographic identifier|"
                      r"new immutable revision|This card uses|The exact checked|formula repair)", re.I)


def why_lines(h, title, edge_by_id, pair_kinds, sid=None, compact=False, S=None):
    """How retrieve() found this hit, from its own score trace: the words it matched, and for the spreading (graph)
    channel the card each contribution came from, the links it travelled and the last link's stated reason."""
    sid = sid or (lambda x: x)
    out = []
    lex = [t for t in h.trace if t.channel.value in ("lexical", "symbol", "formula", "semantic")]
    if lex:
        lex.sort(key=lambda t: -t.amount)
        if compact:
            parts = []
            for t in lex[:5]:
                d = t.detail
                if d.startswith("matched title phrase "):
                    parts.append('"%s" (title)' % d[len("matched title phrase "):].split()[0].replace("_", " "))
                elif d.startswith("matched normalized term "):
                    parts.append(d[len("matched normalized term "):].strip("'"))
                else:
                    parts.append(d[:60])
            more = " (+%d)" % (len(lex) - 5) if len(lex) > 5 else ""
            out.append("matched: %s%s" % (", ".join(parts), more))
        else:
            parts = []
            for t in lex[:4]:
                d = t.detail.replace("matched normalized term ", "term ").replace("matched title phrase ", "title phrase ")
                parts.append(d)
            more = " (+%d more)" % (len(lex) - 4) if len(lex) > 4 else ""
            words = sum(t.amount for t in lex)
            out.append("found by words %.2f: %s%s" % (words, "; ".join(parts), more))
    for chan, label in (("graph", "found by spreading"), ("warning", "CAUTION, reached through a negative link")):
        tr = sorted((t for t in h.trace if t.channel.value == chan and len(t.path) >= 2), key=lambda t: -t.amount)
        if not tr:
            continue
        total = sum(t.amount for t in tr)
        for i, t in enumerate(tr[:WHY_PATHS]):
            path = list(t.path) or [t.source_node_id]
            hops = []
            for u, v in zip(path, path[1:]):
                ks = pair_kinds.get((u, v)) or pair_kinds.get((v, u)) or ["link"]
                kt = "/".join(sorted(set(ks)))
                st = S.strength(u, v) if S is not None else 0.0
                if st > 0:      # the link carried more because agents used its two cards together
                    kt = ("%s (strength %.2f)" % (kt, st)) if kt == "used together" else (
                        "%s (used together, strength %.2f)" % (kt, st))
                hops.append("%s -> %s" % (kt, "this card" if v == h.node.id else
                                           '"%s" (%s)' % ((title.get(v) or v)[:60], sid(v))))
            e = edge_by_id.get(t.edge_id)
            why = " ".join((getattr(e, "rationale", "") or "").split()) if e is not None else ""
            seed = path[0] if path else t.source_node_id
            out.append('%s %.2f%s: from "%s" (%s) %s%s' % (
                label, t.amount, (" of %.2f" % total) if i == 0 and len(tr) > 1 else "", (title.get(seed) or seed)[:70],
                sid(seed), ", ".join(hops), ('; the link says: "%s"' % why[:160]) if why else ""))
        if len(tr) > WHY_PATHS:
            out.append("%s: %d more weaker paths" % (label, len(tr) - WHY_PATHS))
    return out


def _kind(x):
    return str(getattr(x, "value", x))


def short_ids(all_ids, floor=SHORT_ID):
    """Shortest prefix of each id that fits that id alone (at least `floor` characters), from the sorted id list."""
    ids = sorted(all_ids)
    out = {}
    for k, i in enumerate(ids):
        m = 0
        for j in (k - 1, k + 1):
            if 0 <= j < len(ids):
                o = ids[j]
                n = 0
                for x, y in zip(i, o):
                    if x != y:
                        break
                    n += 1
                m = max(m, n)
        out[i] = i[:max(floor, m + 1)]
    return out


def walk(ids, nodes, title, links, superseded_by, chars, sid=None, S=None, typed=None, opened=None, by_id=None):
    """--id: each named card whole, then every link it carries (both directions), grouped by kind; within a kind the
    links agents used most come first, and cards agents used with it that it has no link to follow (learned links).
    by_id: card id -> card, in place of `nodes` (the prepared state's, which builds only the cards asked for)."""
    sid = sid or (lambda x: x)
    by_id = by_id if by_id is not None else {n.id: n for n in nodes}
    for want in ids:
        want = want.strip().strip(",;.()[]")
        n = by_id.get(want)
        if n is None:
            hits = [i for i in by_id if i.startswith(want)] if len(want) >= 6 else []
            if len(hits) == 1:
                n = by_id[hits[0]]
            else:
                print("no card with id %s%s" % (want, " (%d cards start with it)" % len(hits) if hits else ""))
                continue
        if opened is not None:
            opened.append(n.id)
        note = "; SUPERSEDED by %s" % superseded_by[n.id] if n.id in superseded_by else ""
        print("[card] %s  (id: %s; %s%s)" % (title.get(n.id, ""), n.id, _kind(n.kind), note))
        body = " ".join((n.body or "").split())
        if body:
            print("    " + body[:chars] + (" [...]" if len(body) > chars else ""))
        mine = sorted(links.get(n.id, []), key=lambda x: (x[1] == "supersedes", KIND_ORDER.get(x[1], 99), x[1],
                                                          -(S.strength(n.id, x[2]) if S is not None else 0.0), x[0],
                                                          title.get(x[2]) or x[2]))
        print("    links: %d" % len(mine))
        for d, k, other, why in mine[:WALK_LINKS]:
            mark = " (superseded by %s)" % sid(superseded_by[other]) if other in superseded_by else ""
            print("    %s %s: %s (id: %s)%s%s%s" % (d, k, (title.get(other) or other)[:160], sid(other), mark,
                                                   strength_tag(S, n.id, other), ("  (" + why[:200] + ")") if why else ""))
        if len(mine) > WALK_LINKS:
            print("    ... %d more links not shown" % (len(mine) - WALK_LINKS))
        learned = S.learned(n.id, typed) if (S is not None and typed is not None) else []
        if learned:
            print("    used together with it, no link of their own (learned from use, not a mathematical relation): %d"
                  % len(learned))
            for other, info in learned[:WALK_LINKS]:
                mark = " (superseded by %s)" % sid(superseded_by[other]) if other in superseded_by else ""
                print("    ~ %s (id: %s)%s %s" % ((title.get(other) or other)[:160], sid(other), mark,
                                                 _strength_module().tag(info)))
    return 0


def walk_full(ids, nodes, title, links, superseded_by, chars, opened=None, by_id=None):
    """The 11 Oct walk printout (--full): links in stored kind order, full ids."""
    by_id = by_id if by_id is not None else {n.id: n for n in nodes}
    for want in ids:
        want = want.strip().strip(",;.()[]")
        n = by_id.get(want)
        if n is None:
            hits = [i for i in by_id if i.startswith(want)] if len(want) >= 6 else []
            if len(hits) == 1:
                n = by_id[hits[0]]
            else:
                print("no card with id %s%s" % (want, " (%d cards start with it)" % len(hits) if hits else ""))
                continue
        if opened is not None:
            opened.append(n.id)
        note = "; SUPERSEDED by %s" % superseded_by[n.id] if n.id in superseded_by else ""
        print("[card] %s  (id: %s; %s%s)" % (title.get(n.id, ""), n.id, _kind(n.kind), note))
        body = " ".join((n.body or "").split())
        if body:
            print("    " + body[:chars] + (" [...]" if len(body) > chars else ""))
        mine = sorted(links.get(n.id, []), key=lambda x: (x[1] == "supersedes", x[1], x[0], title.get(x[2]) or x[2]))
        print("    links: %d" % len(mine))
        for d, k, other, why in mine[:WALK_LINKS]:
            mark = " (superseded by %s)" % superseded_by[other] if other in superseded_by else ""
            print("    %s %s: %s (id: %s)%s%s" % (d, k, (title.get(other) or other)[:160], other, mark,
                                                 ("  (" + why[:200] + ")") if why else ""))
        if len(mine) > WALK_LINKS:
            print("    ... %d more links not shown" % (len(mine) - WALK_LINKS))
    return 0


def matched_line(body, terms, width=220):
    """The stretch of a card's text around its densest run of the query's matched words (for --list and weak hits)."""
    if not body:
        return ""
    low = body.lower()
    pos = [low.find(t) for t in terms if t and low.find(t) >= 0]
    if not pos:
        return body[:width] + (" [...]" if len(body) > width else "")
    best, start = -1, 0
    for p in pos:   # the window starting near p that holds most matched words
        s = max(0, p - 40)
        c = sum(1 for t in terms if t and low.find(t, s, s + width) >= 0)
        if c > best:
            best, start = c, s
    seg = body[start:start + width]
    return ("[...] " if start else "") + seg + (" [...]" if start + width < len(body) else "")


def unmatched_words(query_terms, corpus_low):
    """Content words of the query that occur in no card's title or text (worth rewording)."""
    return [t for t in query_terms if len(t) >= 4 and corpus_low.find(t) < 0]


def derive_tool(nodes, edges):
    """What a printout reads besides the search, from every card and link: titles, which card supersedes which, the
    link kinds of each pair, each card's links in store order, and the short ids. Plain values only, so fastrecall.py
    keeps them with the prepared state."""
    src = lambda e: getattr(e, "source_id", None) or getattr(e, "source", None)  # noqa: E731
    dst = lambda e: getattr(e, "target_id", None) or getattr(e, "target", None)  # noqa: E731
    superseded_by = {dst(e): src(e) for e in edges if _kind(e.kind) == "supersedes"}
    title = {n.id: " ".join((n.title or "").split()) for n in nodes}
    pair_kinds = {}
    for e in edges:
        pair_kinds.setdefault((src(e), dst(e)), []).append(_kind(e.kind))
    links = {}
    for e in edges:
        why = " ".join((getattr(e, "rationale", "") or "").split())
        links.setdefault(src(e), []).append(("->", _kind(e.kind), dst(e), why))
        links.setdefault(dst(e), []).append(("<-", _kind(e.kind), src(e), why))
    return {"title": title, "superseded_by": superseded_by, "pair_kinds": pair_kinds, "links": links,
            "short": short_ids(title.keys())}


def _fast_module():
    """fastrecall.py beside this file (the question-independent half of a search, prepared once), or None."""
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        if here not in sys.path:
            sys.path.append(here)
        import fastrecall
        return fastrecall
    except Exception:
        return None


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
    """Record the ids a call opened (use) and printed as hits (seen), and the learned partners it printed (lead).
    Never raises; never records the words."""
    ms = _strength_module()
    if ms is not None:
        ms.log_use(tool, use, seen, store_path, lead=lead)


def order_hit_links(nid, mine, S):
    """A hit's link lines in print order: mathematical relations first (KIND_ORDER), then within a kind the links agents
    used most (strongest co-use first), then the stored order."""
    if S is None:
        return sorted(mine, key=lambda x: KIND_ORDER.get(x[1], 99))
    return sorted(mine, key=lambda x: (KIND_ORDER.get(x[1], 99), -S.strength(nid, x[2])))


def strength_tag(S, a, b):
    """' [strength 0.62: used together 3 times, last 2 h ago]' for two cards agents used together, else ''."""
    info = S.pair(a, b) if S is not None else None
    return (" " + _strength_module().tag(info)) if info else ""


def strength_edges(edges, S, typed):
    """The edges the spreading follows, with strengths in: a link agents used together carries weight x (1 + strength)
    (a copy; the store is untouched), and each learned link joins as a loose association of weight 0.5 x strength
    whose stated reason says it was learned from use. Returns (edges, {(a, b): ["used together"]})."""
    from math_brain.models import EdgeKind, GraphEdge
    ms = _strength_module()
    out, kinds = [], {}
    sm = S.strength_map()
    for e in edges:
        s = sm.get(e.source_id)
        f = (1.0 + s.get(e.target_id, 0.0)) if s else 1.0
        if f != 1.0:
            e = copy.copy(e)
            object.__setattr__(e, "weight", e.weight * f)
        out.append(e)
    for a, lst in sorted(S.learned_all(typed).items()):
        for b, info in lst:
            if (b, a) in kinds:
                continue
            out.append(GraphEdge(a, b, EdgeKind.SEMANTIC_ASSOCIATION,
                                 "used together on %d session-days (learned from use; not a mathematical relation)"
                                 % info["n"], weight=max(0.01, min(1.0, ms.LEARNED_WEIGHT * info["strength"])),
                                 id="learned_%s_%s" % (a[:30], b[:30])))
            kinds[(a, b)] = ["used together"]
    return out, kinds


# Possessive names (28 Oct 2026). The tokenizer keeps an apostrophe inside a word, so the card
# "Janson's Inequality" indexes the term janson's, and the question "Janson inequality" (term janson) never meets it:
# the card was not in the top 8 for that wording, while three cards about a different Janson result were. Of the
# lexical index's 98,255 terms, 1,138 are possessives (talagrand's 64 cards, hall's 54, jensen's 116, janson's 19).
# Each wording gets the other form of a name added: base -> base's when base's is at least 0.5 pct of base's own
# document count and in 2 or more cards (that keeps group's, graph's, function's out), and base's -> base always.
# Meta words are skipped. OFF by default since the 28 Oct A/B (mixed result); MATH_RECALL_POSSESSIVE=1 turns it on. Changing the two tokenizers instead would need the
# prefilter index and the signal cache rebuilt.
_POSS_SKIP = {"paper", "author", "agent", "page", "image", "wikipedia", "claim", "source", "model", "campaign",
              "worker", "collector", "card", "lemma", "theorem"}


def possessive_variants(queries, lex, ratio=0.005, min_df=2, swap=False):
    from math_brain.retrieval import tokenize, _content_terms

    def df(t):
        b = lex.get(t)
        if b is None:
            return 0
        try:
            import fastrecall
            return len(fastrecall._unints(b)) // 2
        except Exception:
            return 1
    out = []
    for q in queries:
        terms = set(_content_terms(tokenize(q)))
        extra = []
        for t in sorted(terms):
            if t.endswith("'s"):
                base = t[:-2]
                if len(base) >= 3 and base not in terms and base in lex:
                    extra.append(base)
            elif t not in _POSS_SKIP and (t + "'s") not in terms:
                p = df(t + "'s")
                if p >= min_df and p >= ratio * max(1, df(t)):
                    extra.append(t + "'s")
        if swap:   # one wording with each name in its other form, in place
            if extra:
                rep = {}
                for e in extra:
                    rep[e[:-2] if e.endswith("'s") else e + "'s"] = e
                words = re.split(r"(\s+)", q)
                sw = "".join(rep.get(w.lower(), w) if w.strip() else w for w in words)
                if sw != q:   # nothing swapped in place (a name glued to punctuation): no wording rather than a sum
                    out.append(sw)
            continue
        out.append(q + " " + " ".join(extra) if extra else q)
    return out


_TUP = re.compile(r"\(\s*-?\d+(?:\s*,\s*-?\d+)+\s*\)")
_NUM = re.compile(r"(?<![\w.])-?\d+(?![\w.])")
LIT_MAX = int(os.environ.get("MATH_RECALL_LITERAL", "3") or 0)   # cards the literal lane may put first; 0 turns it off
LIT_POOL = 40   # a tuple set held by more cards than this says too little to pick one


class _LitHit(object):
    """A card the words did not rank but whose coordinate tuples hold every tuple of the wording."""
    def __init__(self, node):
        self.node, self.total_score, self.trace = node, 0.0, []


def _lit_parts(text):
    tups = [re.sub(r"\s+", "", t) for t in _TUP.findall(text or "")]
    nums = set(_NUM.findall(_TUP.sub(" ", text or "")))
    return tups, nums


def _lit_index(P, nodes, store_path):
    """card id -> (its tuples, the numbers outside them), for every current card with two or more tuples; cached per
    store state beside the prepared state (about 2,000 cards of the 2026 store, most of them walk-on-a-strip counts)."""
    import hashlib
    import json
    fr = _fast_module()
    try:
        # keyed to the prepared state when there is one: a state served behind the store (fastrecall STALE_S) is one
        # card set, and the live stamp would rebuild this index on every call while the store moves
        key = (repr(P.key) if getattr(P, "key", None) else
               repr(fr._store_key(store_path)) if fr is not None else repr(os.stat(store_path)[6:9]))
    except Exception:
        key = repr(os.stat(store_path)[6:9])
    cdir = (fr.cache_dir() if fr is not None else os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache"))
    path = os.path.join(cdir, "literal_%s.json" % hashlib.sha1((key + "|v1").encode()).hexdigest()[:16])
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        pass
    if nodes is None:
        nodes = P.all_nodes()
    idx = {}
    for n in nodes:
        tups, nums = _lit_parts(n.body)
        if len(tups) >= 2:
            idx[n.id] = [sorted(set(tups)), sorted(nums)]
    try:
        os.makedirs(cdir, exist_ok=True)
        tmp = path + ".%d.tmp" % os.getpid()
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(idx, f)
        os.replace(tmp, path)
        # one file per store state: keep the newest four (29 had piled up by 27 Oct, about 0.47 MB each)
        old = sorted((os.path.join(cdir, f) for f in os.listdir(cdir)
                      if f.startswith("literal_") and f.endswith(".json")), key=os.path.getmtime)[:-4]
        for f in old:
            try:
                os.remove(f)
            except OSError:
                pass
    except OSError:
        pass
    return idx


def literal_lane(queries, P, nodes, title, superseded_by, store_path, found_by=None):
    """[(card id, wording number)]: for a wording with two or more coordinate tuples, the current cards whose tuples
    hold every one of them in any order (the word search matches tuples a pair of numbers at a time, so a card listing
    the same sites in another order was missed; 27 Oct). Best first: more of the wording's other numbers matched, fewer
    tuples beyond the wording's; at most LIT_MAX cards in all, none when more than LIT_POOL cards qualify."""
    if LIT_MAX <= 0:
        return []
    want = []
    for qi, q in enumerate(queries, 1):
        tups, nums = _lit_parts(q)
        qt = set(tups) - {"(0,0)"}
        if len(qt) >= 2:
            want.append((qi, qt, nums))
    if not want:
        return []
    idx = _lit_index(P, nodes, store_path)
    out, seen = [], set()
    for qi, qt, nums in want:
        pool = []
        for cid, (ct, cn) in idx.items():
            if cid in superseded_by:
                continue
            cts = set(ct)
            if qt <= cts:
                nm = len(nums & set(cn))
                extra = len(cts - qt - {"(0,0)"})
                wr = min([r for q, r in (found_by or {}).get(cid, []) if q == qi] or [99])
                # a card the words already put first stays first: the literal match agrees with it
                pool.append((0 if wr == 1 else 1, -(nm - extra), -nm, wr, cid))
        if not pool or len(pool) > LIT_POOL:
            continue
        for *_k, cid in sorted(pool):
            if cid not in seen and len(out) < LIT_MAX:
                seen.add(cid)
                out.append((cid, qi))
    return out


TIP_MIN = int(os.environ.get("MATH_RECALL_TUPLE_TIP", "3") or 0)   # 0 turns the tip off


def tuple_tip(queries, hits, P, nodes, store_path):
    """One hint line, or "": no wording names a site tuple other than (0,0), yet TIP_MIN or more of the printed cards list them.
    Such cards (most of them walk-on-a-strip counts) differ only by their site lists, so words alone cannot pick one;
    the tuple lane can, once the sites are in a wording (27 Oct: an agent asked for 'strip width 3, length 13, avoid
    sites' without naming the sites and got a neighbour's card)."""
    if TIP_MIN <= 0 or not hits:
        return ""
    if any(set(_lit_parts(q)[0]) - {"(0,0)"} for q in queries):   # any named site: the agent knows its sites
        return ""
    try:
        idx = _lit_index(P, nodes, store_path)
    except Exception:
        return ""
    n = sum(1 for h in hits if h.node.id in idx)
    if n < TIP_MIN:
        return ""
    return ("TUPLE TIP (a hint): %d of these cards list coordinate tuples and differ by them. If you know the sites, "
            "write them as (x,y) tuples in a wording, e.g. (1,0), (2,1): the tuple lane then puts first the card that "
            "lists exactly those, in any order." % n)


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(
        description="One-call recall from the math store: ids, whole cards, linked ids.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="how to ask (measured 11-12 Oct 2026, Codex agents, 100 questions an arm):\n"
               "  two or three wordings in ONE call, each after its own --or: 86 of 100, against 76 with one\n"
               "  wording a call; word them the way the answering card is written (the object, the theorem or\n"
               "  method by name, the symbol, the author), not in the question's own phrasing\n"
               "  read the ids, then walk with --id (up to three ids a call): the same answers as searching\n"
               "  again (82 against 83) on about half the uncached tokens\n"
               "  WEAK MATCH under a printout means reword; no WEAK line does not mean the answer is printed;\n"
               "  stop when a printed card answers. Leave --depth at 0 (--or scored as well and cost less).")
    ap.add_argument("words", nargs="*")
    ap.add_argument("--id", nargs="+", default=None, metavar="CARD_ID",
                    help="walk: print these cards whole with every link they carry (ids from a hit's links; a short "
                         "prefix that fits one card is enough)")
    ap.add_argument("--limit", type=int, default=int(os.environ.get("MATH_RECALL_LIMIT", "8") or 8))
    ap.add_argument("--chars", type=int, default=None,
                    help="characters of each card's text to print (default: MATH_RECALL_CHARS for a search, "
                         "MATH_RECALL_WALK_CHARS for a walk, else 4000)")
    ap.add_argument("--depth", type=int, default=int(os.environ.get("MATH_RECALL_DEPTH", "0") or 0),
                    help="graph depth of the spreading (default from MATH_RECALL_DEPTH, else 0 = words only)")
    ap.add_argument("--or", dest="alts", action="append", default=[], metavar="WORDS",
                    help="another wording of the same question, repeatable (--or A --or B); every wording is searched in "
                         "this one call and the hits merged, best rank first (saves a search, which costs the agent a "
                         "whole turn)")
    ap.add_argument("--list", action="store_true",
                    help="compact listing: each hit's title, id and the line of its text that matched (read one with --id)")
    ap.add_argument("--full", action="store_true", default=os.environ.get("MATH_RECALL_FULL", "") not in ("", "0"),
                    help="the 11 Oct printout: full ids in every link line, every link reason, stored link order")
    ap.add_argument("--store", default=STORE_PATH)
    a = ap.parse_args(argv)
    walk_chars = a.chars or int(os.environ.get("MATH_RECALL_WALK_CHARS", "4000") or 4000)
    hit_links = int(os.environ.get("MATH_RECALL_LINKS", "8") or 0)
    link_why = int(os.environ.get("MATH_RECALL_LINK_WHY", "200" if a.full else "160") or 0)
    a.chars = a.chars or int(os.environ.get("MATH_RECALL_CHARS", "4000") or 4000)
    # below 5 the top 8 missed the answering card on 16 of 21 dev questions (12 Oct, 160 DEVQ questions, question words
    # only); at 5 or above, on 76 of 139: a weak signal, so the line fires rarely and says it is a hint
    weak_below = float(os.environ.get("MATH_RECALL_WEAK", "5.0") or 0)
    if not a.words and not a.id:
        ap.error("give words to search, or --id <card id> to walk from a card")
    query = " ".join(a.words)
    if not os.path.exists(a.store):
        print("math store not found: %s" % a.store)
        return 2
    sys.path.insert(0, BUILD_SRC)
    from math_brain.retrieval import RetrievalMode, load_signal_db, retrieve, signal_db_path
    try:  # the recall profile (F01, 12 Oct 2026); MATHBRAIN_RECALL_PROFILE=0 turns it off inside the function
        from math_brain.retrieval import recall_profile, retrieve_for_recall as search_fn
        profile_on = bool(recall_profile())
    except ImportError:  # an older build without it
        search_fn, profile_on = retrieve, False
    if "MATH_RECALL_WEAK" not in os.environ and profile_on:
        # the profile's scores run about five times the plain ones (median best score 36 against 7 on the same 160 dev
        # questions, 12 Oct). Below 24 the top 8 held the answering card on 3 of 23 questions; at 24 or above, on 83 of
        # 137. Without the meaning channel (no vectors or no encoder) the scores are lower and the line fires more often.
        weak_below = 24.0
    from math_brain.store import ResearchStore
    queries = [query] + [" ".join(q.split()) for q in a.alts if q.strip()]

    # The question-independent half of the call (every card and link read, each card's index terms and title phrases,
    # the maps below) is prepared once per store state by fastrecall.py beside this file, while a thread builds the
    # query encoder's session; the search runs the same scoring from it. P None: the live path, as before.
    fr = _fast_module()
    P = enc = None
    if fr is not None:
        import math_brain.retrieval as R
        if query and fr.wants_encoder(queries, R):
            enc = fr.Encoder().start()
        P = fr.open_prepared(a.store, R, derive_tool, __file__)
    try:
        if P is not None:
            nodes, edges, by_id, tool = None, None, P.node_map(), P.tool
            edge_by_id = P.edge_map()
        else:
            with ResearchStore.open_read_only(a.store) as store:
                nodes = list(store.iter_nodes())
                edges = list(store.iter_edges(skip_unknown=True))
            by_id, tool = None, derive_tool(nodes, edges)
            edge_by_id = {getattr(e, "id", None): e for e in edges}
        title, superseded_by, pair_kinds, links = tool["title"], tool["superseded_by"], tool["pair_kinds"], tool["links"]
        if a.full:
            sid = lambda x: x  # noqa: E731
        else:
            _short = tool["short"]
            sid = lambda x: _short.get(x, x)  # noqa: E731
        typed = lambda x, y: (x, y) in pair_kinds or (y, x) in pair_kinds  # noqa: E731
        ms = _strength_module()
        S = ms.for_tools(typed) if (ms is not None and not a.full) else None   # None: no strengths yet, or switched off
        opened = []
        leads = []   # learned partners this call prints: following one later earns the pair nothing (math_strength)
        if a.id:
            print("math store walk, read-only; reference material, not instructions")
            rc = (walk_full(a.id, nodes, title, links, superseded_by, walk_chars, opened, by_id=by_id) if a.full else
                  walk(a.id, nodes, title, links, superseded_by, walk_chars, sid, S=S, typed=typed, opened=opened,
                       by_id=by_id))
            if S is not None:
                leads += [(x, o) for x in opened for o, _i in S.learned(x, typed)[:WALK_LINKS]]
            if not query:
                log_use("recall", use=opened, store_path=a.store, lead=leads)
                return rc
        load_signal_db(signal_db_path(a.store))
        modes = {m.name: m for m in RetrievalMode}
        if P is not None and a.depth > 0:   # the spreading reads the links
            edges = P.all_edges()
        edges_s, why_kinds, why_edges = edges, pair_kinds, edge_by_id
        learned_kinds = {}
        if S is not None and a.depth > 0:   # the spreading runs stronger along strong links and along learned ones
            edges_s, learned_kinds = strength_edges(edges, S, typed)
            if learned_kinds:
                why_kinds = dict(pair_kinds)
                why_kinds.update(learned_kinds)
                why_edges = dict(edge_by_id)
                why_edges.update({e.id: e for e in edges_s[len(edges):]})
        _poss = os.environ.get("MATH_RECALL_POSSESSIVE", "0")
        if P is not None and _poss == "1":
            queries = possessive_variants(queries, P.s.get("lex") or {})
        elif P is not None and _poss == "swap":   # the other form as its own wording: fused by rank, never summed
            queries = queries + [v for v in possessive_variants(queries, P.s.get("lex") or {}, swap=True)
                                 if v not in queries]
        found_by, best, results = {}, {}, []
        for qi, q in enumerate(queries, 1):
            kw = dict(edges=edges_s, limit=a.limit * 2, min_support_score=0.0, min_margin=0.0, graph_depth=a.depth,
                      mode=modes["DISCOVERY"])
            res = P.search(q, kw, encoder_ready=enc.wait if enc is not None else None) if P is not None else None
            if res is None:   # the live search: no prepared state, or a dial it does not cover
                if enc is not None:
                    enc.wait()
                if nodes is None:
                    nodes = P.all_nodes()
                if kw["edges"] is None:
                    edges = edges_s = kw["edges"] = P.all_edges()
                res = search_fn(q, nodes=nodes, **kw)
            results.append(res)
            for rank, h in enumerate([h for h in res.hits if h.node.id not in superseded_by][:a.limit], 1):
                found_by.setdefault(h.node.id, []).append((qi, rank))
                best.setdefault(h.node.id, h)
        def order(h):   # best rank, then found by more wordings, then the earlier wording; the id breaks exact ties
            fb = found_by[h.node.id]
            if h.node.id in lit_pos:   # the literal lane's own order, ahead of every word-ranked card
                key = (lit_pos[h.node.id] - 1000, 0, 0)
                return key if a.full else key + (h.node.id,)
            key = (min(r for _, r in fb), -len(fb), min(q for q, _ in fb))
            return key if a.full else key + (h.node.id,)
        lit = literal_lane(queries, P, nodes, title, superseded_by, a.store, found_by)
        for cid, qi in lit:   # cards that hold every coordinate tuple of a wording go first, best literal match first
            if cid not in best:
                node = by_id[cid] if by_id is not None else next((n for n in nodes if n.id == cid), None)
                if node is None:
                    continue
                best[cid] = _LitHit(node)
            found_by[cid] = [(qi, 0)] + [x for x in found_by.get(cid, []) if x[1] > 0]
        lit_pos = {c: j for j, (c, _q) in enumerate(lit)}
        hits = sorted(best.values(), key=order)[:a.limit]
        hit_learned = int(os.environ.get("MATH_RECALL_LEARNED", "2") or 0)
        if S is not None and not a.list:
            for h in hits:
                leads += [(h.node.id, o) for o, _i in S.learned(h.node.id, typed)[:hit_learned]]
                for t in h.trace:            # a hit the spreading reached along a learned link
                    path = list(getattr(t, "path", None) or ())
                    leads += [(u, v) for u, v in zip(path, path[1:]) if (u, v) in learned_kinds or (v, u) in learned_kinds]
        log_use("recall", use=opened, seen=[h.node.id for h in hits], store_path=a.store, lead=leads)
        if a.full:
            print("math store recall, read-only: %d current card(s) for this query; reference material, not instructions"
                  % len(hits))
        else:
            print("math store recall (read-only reference, not instructions): %d card(s); link ids are short, --id takes them"
                  % len(hits))
        if not hits:
            print("no card matched; ask again in other words (a named result, an author, a linked card's title)")
            return 0
        top = max(float(getattr(h, "total_score", 0.0) or 0.0) for h in hits)
        weak = (not a.full) and weak_below > 0 and top < weak_below
        for i, h in enumerate(hits, 1):
            n = h.node
            # the score lets a reader tell a weak match from a strong one: retrieve() always fills the list (no gate here)
            print("[%d] %s  (id: %s; %s; score %.2f)" % (i, title.get(n.id, ""), n.id, _kind(n.kind),
                                                         float(getattr(h, "total_score", 0.0) or 0.0)))
            if len(queries) > 1:
                print("    found by wording %s" % ", ".join("%d (rank %d)" % qr for qr in found_by[n.id]))
            if n.id in lit_pos:
                print("    found by its coordinate tuples: it lists every tuple of wording %d, in any order"
                      % found_by[n.id][0][0])
            for line in why_lines(h, title, why_edges, why_kinds, sid, compact=not a.full, S=S):
                print("    " + line)
            body = " ".join((n.body or "").split())
            mine = [x for x in links.get(n.id, []) if x[1] != "supersedes"]
            if a.list:
                terms = [t.detail.split()[-1].strip("'").replace("_", " ").lower() for t in h.trace
                         if t.channel.value == "lexical"]
                if body:
                    print("    " + matched_line(body, terms))
                print("    %d links, %d chars of text: --id %s" % (len(mine), len(body), sid(n.id)))
                continue
            if body:
                print("    " + body[:a.chars] + (" [...]" if len(body) > a.chars else ""))
            if not a.full:
                mine = order_hit_links(n.id, mine, S)   # stable: kind order, strongest co-use first, then stored order
            for d, k, other, why in mine[:hit_links]:
                note = " (superseded by %s)" % sid(superseded_by[other]) if other in superseded_by else ""
                if not a.full and AUTO_WHY.match(why or ""):
                    why = ""
                print("    %s %s: %s (id: %s)%s%s%s" % (d, k, (title.get(other) or other)[:160], sid(other), note,
                                                       strength_tag(S, n.id, other),
                                                       ("  (" + why[:link_why] + ")") if why and link_why else ""))
            if len(mine) > hit_links:
                if a.full:
                    print("    ... %d more links: math_recall.py --id %s shows them all" % (len(mine) - hit_links, n.id))
                else:
                    print("    ... %d more links (--id %s)" % (len(mine) - hit_links, sid(n.id)))
            for other, info in (S.learned(n.id, typed)[:hit_learned] if S is not None else []):
                note = " (superseded by %s)" % sid(superseded_by[other]) if other in superseded_by else ""
                print("    ~ used together (no link of its own; learned from use): %s (id: %s)%s %s"
                      % ((title.get(other) or other)[:160], sid(other), note, ms.tag(info)))
        tip = tuple_tip(queries, hits, P, nodes, a.store)
        if tip:
            print(tip)
        if weak:
            from math_brain.retrieval import tokenize, _content_terms
            corpus_low = P.corpus_low() if P is not None else "\n".join(
                (title.get(n.id, "") + " " + (n.body or "")).lower() for n in nodes)
            missing = []
            for q in queries:
                for t in unmatched_words(_content_terms(tokenize(q)), corpus_low):
                    if t not in missing:
                        missing.append(t)
            print("WEAK MATCH (a hint): the best score is %.2f, and an answering card usually scores above %.0f. Reword: "
                  "name the object, theorem, method or author the card would use; put two or three wordings in one call "
                  "with --or." % (top, weak_below))
            if missing:
                print("No card contains: %s" % ", ".join(missing[:12]))
        return 0
    finally:
        if enc is not None:
            enc.wait()   # the encoder thread ends before the process does


if __name__ == "__main__":
    sys.exit(main())
