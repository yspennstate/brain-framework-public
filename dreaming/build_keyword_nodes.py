#!/usr/bin/env python3
"""build_keyword_nodes.py - the owner's working vocabulary as first-class topic nodes.

His 07-25 ask: the words he actually uses - "microsoft volume", "aws", "levrage" - should be
NODES, and when he is talking about one of them the mistakes and information under it should
surface about 3x more. Two things were missing for that. There were no keyword nodes at all
(keyword_edges.tsv holds 8 memory-to-memory edges mined from shared terms, not a topic layer),
and nothing connected HIS spelling to the corpus's spelling: he types "levrage", "microsfot",
"voluem", and the memories say leverage, microsoft, volume, so a literal match finds nothing.

This builds one transactional generation containing keyword_nodes.tsv, keyword_graph_edges.tsv,
and keyword_related.tsv. Alias rows are limited to spellings the owner explicitly supplied as
examples; a shape-only spell guess can silently rewrite an intentional word.

  keyword \t slug \t weight \t kind        kind = member | alias

Sources, all already in the brain - no new judgment, no hand-curation:
  - HIS prompts (memory/prompts/PROMPTS.jsonl, else the session transcripts) supply the
    candidate vocabulary. A term he never types is not his keyword, however common it is.
  - The memory index supplies membership: a memory whose index line or slug carries the term
    is a member of that keyword.
  - A small reviewed map supplies the misspelling bridge for the owner's named examples. The
    live path never invents a correction from spelling shape alone.

Bounds that keep a topic node meaningful: MIN_MEMBERS (a term matching one memory is that
memory, not a topic), MAX_MEMBERS (a term matching half the corpus is a stopword with a job),
and a specificity weight 1/sqrt(members) so a broad term does not out-shout a sharp one.

Run:     python build_keyword_nodes.py [--min-prompt-count N]
Refresh: with the nightly dream, or after a wave of new memories.
"""
import io
import hashlib
import json
import os
import re
import sys
from collections import Counter, defaultdict

import keyword_artifacts


def _brain_slug_resolve():
    """The memory corpus this install belongs to.

    Order matters. $BRAIN_SLUG wins so a caller can point one script at another corpus.
    Then BRAIN_SLUG beside this file, which bootstrap/install.py writes with the slug of the
    --project-dir it was given; without it an install whose project directory is not the home
    directory builds a corpus nothing ever reads. The home-derived value stays last so a tree
    that works today keeps working untouched.
    """
    env = os.environ.get("BRAIN_SLUG", "").strip()
    if env:
        return env
    try:
        marker = os.path.join(os.path.dirname(os.path.abspath(__file__)), "BRAIN_SLUG")
        with open(marker, encoding="utf-8") as fh:
            named = fh.read().strip()
        if named:
            return named
    except OSError:
        pass
    home = os.path.expanduser("~")
    return home.replace(":", "-").replace(chr(92), "-").replace("/", "-")


HERE = os.path.dirname(os.path.abspath(__file__))
# Claude Code names a project directory after the home path, with ':' and the
# separators replaced by '-'. Deriving it keeps this tree free of one machine's
# username and correct on any other - and unlike a template token it is a real
# value at import time.
_BRAIN_SLUG = _brain_slug_resolve()
MEM = os.path.join(os.path.expanduser("~"), ".claude", "projects", _BRAIN_SLUG, "memory")
PROMPTS = os.path.join(MEM, "prompts", "PROMPTS.jsonl")
GRAPH_MAX_MEMBERS = 120     # a broader term would shortcut the whole graph if wired in
GRAPH_MIN_SHARED = 3        # two subjects need real overlap to be called related
GRAPH_MIN_JACCARD = 0.06

MIN_MEMBERS = 2
# His biggest topics are the point of this layer, so the cap is generous: at a ~2,700 memory
# corpus, 'leverage' reaches 86 members and 'grid' 372, and a 60-cap silently excluded exactly
# the words he named. Breadth is handled at recall instead, where only the top members by
# salience are boosted, so a broad topic cannot flatten the walk by lighting everything at once.
MAX_MEMBERS = 400
MIN_PROMPT_COUNT = 3        # he has to have used the word more than once in passing
MIN_LEN = 3                 # 'aws' is one of his keywords; anything shorter is noise
MIN_CORPUS_COUNT = 3        # ...and the corpus has to know it, or it is not a shared topic

# Owner-observed examples from the request that created this layer (2026-07-25). This is a
# reviewed semantic assertion, unlike shape-only guesses such as bride->bridge or arise->raise.
# Adding an alias requires another explicit owner-observed spelling and a regression control.
REVIEWED_ALIASES = {
    "levrage": "leverage",
    "microsfot": "microsoft",
    "voluem": "volume",
}
TOKEN = re.compile(r"[a-zA-Z][a-zA-Z0-9_]{2,}")
SKIP = {"MEMORY", "INDEX_ARCHIVE", "PREDICTIONS", "STATE_OF_MIND", "OPEN_QUESTIONS",
        "AUTOBIOGRAPHY", "MODEL_OF_PARTNER", "FASCINATIONS", "THE_OTHER_MINDS", "MISTAKES",
        "CONSCIENCE", "CURIOSITY", "SALIENCE", "GROWTH", "RECENT_BRAIN", "STATS",
        "RECONSOLIDATION_QUEUE"}
STOP = set((
    "the a an and or of to in for on with is are was were be been being this that these those it "
    "its as at by from into over under per not no nor new one two three use used using via etc "
    "when then than but our we you your yours i me my mine he she they them their what which who "
    "how why can could do does did done has have had having will would shall should may might must "
    "more most some any each all only just get got make made see saw seen look need want know now "
    "today about onto out up down off so if too very much many also here there while both because "
    "above below between through during before after again once own same other please thanks ok "
    "okay yes yeah sure also like want lets let go going run running make made take taking give "
    "check checked look looking see seeing tell told say said think thought work working works "
    "keep keeping start started stop stopped good great nice bad wrong right thing things stuff "
    "what whatever anything something everything nothing everyone someone anyone").split())


def _mapping_digest(mapping):
    """Process-stable digest of the exact logical input a build consumed."""
    h = hashlib.sha256()
    for key in sorted(mapping):
        h.update(str(key).encode("utf-8"))
        h.update(b"\0")
        h.update(str(mapping[key]).encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()


def _prompt_terms():
    """Counter of the terms HE types. PROMPTS.jsonl first; transcripts as the fallback."""
    counts = Counter()
    read_any = False
    try:
        with io.open(PROMPTS, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                text = row.get("text") or row.get("prompt") or ""
                if isinstance(text, str) and text:
                    read_any = True
                    counts.update(t.lower() for t in TOKEN.findall(text))
    except OSError:
        pass
    if read_any:
        return counts
    import glob
    root = os.path.join(os.path.expanduser("~"), ".claude", "projects", "{{BRAIN_SLUG}}")
    for path in sorted(glob.glob(os.path.join(root, "*.jsonl")), key=os.path.getmtime)[-60:]:
        try:
            for line in io.open(path, encoding="utf-8", errors="replace"):
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if row.get("type") != "user":
                    continue
                content = (row.get("message", {}) or {}).get("content")
                if isinstance(content, list):
                    content = " ".join(c.get("text", "") for c in content
                                       if isinstance(c, dict) and c.get("type") == "text")
                if isinstance(content, str) and content and not content.startswith(("[", "<")):
                    counts.update(t.lower() for t in TOKEN.findall(content))
        except OSError:
            continue
    return counts


def _corpus():
    """slug -> searchable text, and the corpus vocabulary counter.

    Membership must be read from the memory FILES, not MEMORY.md: the index is compacted to
    the newest entries (~60 lines against a ~2,700 memory corpus), so matching against it made
    'volume', 'microsoft' and 'grid' look like one-memory terms - measured, and the reason this
    function exists. The vocabulary is what decides whether one of his spellings is a real word
    or a typo, so it too must come from the whole corpus.
    """
    texts, vocab = {}, Counter()
    for base in (MEM, os.path.join(MEM, "dreams")):
        try:
            names = os.listdir(base)
        except OSError:
            continue
        for name in names:
            if not name.endswith(".md"):
                continue
            slug = name[:-3]
            if slug in SKIP:
                continue
            try:
                body = io.open(os.path.join(base, name), encoding="utf-8", errors="replace").read(4000)
            except OSError:
                continue
            desc = ""
            m = re.search(r'description:\s*"?(.+?)"?\s*\n', body)
            if m:
                desc = m.group(1)
            text = (slug + " " + desc).lower()
            texts[slug] = text
            vocab.update(t.lower() for t in TOKEN.findall(body))
    return texts, vocab


def _normalizer():
    """bm25's spell normalizer + its corpus vocabulary, or (None, set()) if unavailable."""
    try:
        sys.path.insert(0, HERE)
        from bm25 import BM25, QueryNormalizer
        bm = BM25()
        return QueryNormalizer(bm.df), set(bm.df)
    except Exception:
        return None, set()


def _graph_artifacts(rows):
    """Render the topics as FIRST-CLASS graph nodes, `kw:<term>`.

    Membership alone makes a keyword a lookup table. Making it a node with edges makes it an
    object the walk can travel THROUGH: activation entering 'volume' can now reach the storage
    and cost memories under it, and cross to the neighbouring subjects it shares members with.
    Two edge kinds, both derived, neither hand-curated:

      kw:<term> <-> member memory     - the topic holds this memory
      kw:<a>    <-> kw:<b>            - two subjects that share members are related subjects,
                                        weighted by Jaccard overlap so 'aws' reaches 'azure'
                                        and 'volume' without a hand-written ontology

    Bounded on purpose: only topics under GRAPH_MAX_MEMBERS become nodes (a term matching a
    tenth of the corpus is a stopword with a job, and wiring it in would shortcut the whole
    graph), and a keyword-keyword edge needs real overlap, not one shared memory.
    """
    by_topic = defaultdict(set)
    weights = {}
    for keyword, slug, weight, _kind in rows:
        by_topic[keyword].add(slug)
        weights[keyword] = weight
    topics = {t: m for t, m in by_topic.items() if len(m) <= GRAPH_MAX_MEMBERS}

    edges = []
    for term, group in topics.items():
        node = "kw:" + term
        for slug in sorted(group):
            edges.append((node, slug, weights.get(term, 0.2)))

    # keyword <-> keyword by shared membership
    member_index = defaultdict(list)
    for term, group in topics.items():
        for slug in group:
            member_index[slug].append(term)
    pair_counts = defaultdict(int)
    for slug, terms in member_index.items():
        if len(terms) < 2 or len(terms) > 12:      # a memory under a dozen topics says nothing
            continue
        ordered = sorted(terms)
        for i, a in enumerate(ordered):
            for b in ordered[i + 1:]:
                pair_counts[(a, b)] += 1
    for (a, b), shared in pair_counts.items():
        union = len(topics[a] | topics[b])
        jaccard = shared / float(union) if union else 0.0
        if shared >= GRAPH_MIN_SHARED and jaccard >= GRAPH_MIN_JACCARD:
            edges.append(("kw:" + a, "kw:" + b, round(min(1.0, jaccard * 2.0), 4)))

    graph_lines = ["a\tb\tweight\n"]
    graph_lines.extend("%s\t%s\t%.4f\n" % (a, b, w) for a, b, w in edges)

    # The subject-to-subject edges get their OWN small file. The full graph is ~87k rows and
    # loading it into every recall cost ~680ms on the every-prompt path (vs 123ms) - the walk,
    # not the parse: a 90-member topic node explodes the second hop across its whole subject.
    # The live path instead injects a named topic's edges on demand, and for that it needs only
    # this handful of relations, not the whole file.
    related = [(a, b, w) for a, b, w in edges if b.startswith("kw:")]
    related_lines = ["a\tb\tweight\n"]
    related_lines.extend("%s\t%s\t%.4f\n" % (a, b, w) for a, b, w in related)
    kw_kw = len(related)
    return "".join(graph_lines), "".join(related_lines), len(edges), kw_kw, len(topics)


def build():
    prompt_counts = _prompt_terms()
    if not prompt_counts:
        print("no owner prompt corpus found; refusing to write an empty artifact", file=sys.stderr)
        return 1
    texts, vocab = _corpus()
    if not texts:
        print("no memory corpus found; refusing to write an empty artifact", file=sys.stderr)
        return 1

    # his terms that the corpus also knows: these are the topic candidates
    canonical_terms = set()
    for term, count in prompt_counts.items():
        if len(term) < MIN_LEN or count < MIN_PROMPT_COUNT or term in STOP:
            continue
        if vocab.get(term, 0) >= MIN_CORPUS_COUNT:
            canonical_terms.add(term)

    # Membership over the real corpus, on TOKEN boundaries. Substring matching (`term in text`)
    # put 86,394 of 150,713 memberships - 57% - on memories that never use the word: 'aws' matched
    # 'laws'/'flaws', 'grid' matched 'gridlock', and 3-letter topics like 'eat'/'pos'/'ear' reached
    # several hundred unrelated memories each. A topic that points at the whole corpus carries no
    # information, and this layer diffuses at 3x, so the noise was amplified rather than diluted.
    members = defaultdict(set)
    for slug, text in texts.items():
        toks = set(TOKEN.findall(text))
        for term in canonical_terms & toks:
            members[term].add(slug)

    # Aliases are semantic assertions, not edit-distance guesses. Shape-only inference still
    # rewrote real intended words (addrs->adds, bride->bridge, arise->raise) after the first
    # tightening pass. Only spellings the owner explicitly supplied may bridge a topic.
    candidates = {}
    strong = {
        term for term in canonical_terms
        if MIN_MEMBERS <= len(members.get(term, ())) <= MAX_MEMBERS
    }
    for term, canonical in REVIEWED_ALIASES.items():
        if canonical in strong:
            candidates[term] = canonical

    rows, aliases = [], []
    for term in sorted(canonical_terms):
        member_set = members.get(term) or set()
        if not (MIN_MEMBERS <= len(member_set) <= MAX_MEMBERS):
            continue
        weight = 1.0 / (len(member_set) ** 0.5)
        for slug in sorted(member_set):
            rows.append((term, slug, weight, "member"))
    kept = {r[0] for r in rows}
    for term, canonical in sorted(candidates.items()):
        if canonical in kept:
            aliases.append((term, canonical, 1.0 / (len(members[canonical]) ** 0.5), "alias"))

    if not rows:
        print("no keyword node cleared the membership bounds; artifact not written", file=sys.stderr)
        return 1
    # ONE ROW PER SUBJECT, not one per membership. The row-per-membership form was 150,713 lines
    # and the live hook parses this file on every prompt: measured ~540ms of a ~590ms recall.
    # Collapsed, it is ~5.4k lines, and only the handful of subjects a prompt actually names ever
    # get their member list split. Same information, ~30x less parsing on the hot path.
    grouped = defaultdict(list)
    weight_of = {}
    for keyword, slug, weight, _kind in rows:
        grouped[keyword].append(slug)
        weight_of[keyword] = weight
    node_lines = ["keyword\tkind\tweight\tmembers\n"]
    for keyword in sorted(grouped):
        node_lines.append("%s\tmember\t%.4f\t%s\n"
                          % (keyword, weight_of[keyword], ",".join(sorted(grouped[keyword]))))
    for term, canonical, weight, _kind in aliases:
        node_lines.append("%s\talias\t%.4f\t%s\n" % (term, weight, canonical))
    graph_text, related_text, edge_count, kw_kw, graph_topics = _graph_artifacts(rows)
    source = {
        "prompt_terms_sha256": _mapping_digest(prompt_counts),
        "corpus_search_sha256": _mapping_digest(texts),
        "corpus_vocab_sha256": _mapping_digest(vocab),
        "memory_count": len(texts),
        "prompt_term_count": len(prompt_counts),
        "reviewed_aliases": dict(sorted(REVIEWED_ALIASES.items())),
    }
    selected = keyword_artifacts.publish_generation(
        HERE,
        {
            "keyword_nodes.tsv": "".join(node_lines),
            "keyword_graph_edges.tsv": graph_text,
            "keyword_related.tsv": related_text,
        },
        source=source,
    )

    topics = len({r[0] for r in rows})
    print("keyword generation: %s (manifest %s)"
          % (selected["generation_id"], selected["manifest_sha256"]))
    print("keyword_graph_edges.tsv: %d edges (%d keyword-to-keyword) over %d topic nodes"
          % (edge_count, kw_kw, graph_topics))
    print("keyword_nodes.tsv: %d topics, %d memberships, %d misspelling aliases"
          % (topics, len(rows), len(aliases)))
    for probe in ("leverage", "aws", "volume", "microsoft", "grid"):
        if probe in members:
            print("  %s -> %d members" % (probe, len(members[probe])))
    if aliases:
        print("  aliases sample:", ", ".join("%s->%s" % (a[0], a[1]) for a in aliases[:8]))
    return 0


if __name__ == "__main__":
    raise SystemExit(build())
