#!/usr/bin/env python3
"""bm25.py - a real ranked keyword arm for the recall stack (laptop-safe, milliseconds).

The lexical seeder in spread_recall sees only MEMORY.md index lines and filenames - memory CONTENT
is invisible to it unless the graph or the embedding happens to reach it. BM25 (IDF-weighted,
length-normalized term matching; Robertson-Sparck Jones) over the full memory text is the standard
keyword arm every 2025-26 production memory survey converged on (semantic + keyword + entity), and
at 1417 docs it costs milliseconds with no model, no GPU, no network.

Index: bm25_index.json.gz - {vocab df, per-doc sparse tf, doc lengths}. Rebuild is idempotent and
fast (~seconds); the dream persist refreshes it nightly alongside spread_edges.

Gate before any live wiring (build-behind-an-eval): eval_prompt_recall.py replays real owner
prompts (the new prompt archive) against what each session actually went on to read.

Usage:  python bm25.py build            rebuild the index from the memory dir
        python bm25.py refresh          index only the memory files newer than the index (seconds; safe to
                                        run from a 15-minute task, from recall() and from remember())
        python bm25.py freeze [MIN] [why]   hold the index generation (a receipt/probe/eval run); refresh skips
        python bm25.py unfreeze             release it (a freeze past its own deadline is ignored anyway)
        python bm25.py query "text"     top-10 sanity check

WRITE-TIME LANE (2026-09-02). A memory written to the corpus was invisible to the bm25 arm - and so to
deliberate recall, whose ranking is bm25-first - until the nightly rebuild. Measured that night: a box-icing
law filed at 01:2xZ did not surface for "can I restart <remote-box>" until the index was rebuilt by hand.
refresh() closes that gap incrementally: it adds or replaces the docs whose file mtime is newer than the
index, drops docs whose file is gone, and rewrites the same payload format atomically under a lock, so the
nightly build() stays the source of truth and every reader (hooks via the pickle sidecar, recall(), evals)
sees the fresh memory on its next load.
"""
import os, io, re, sys, json, gzip, hashlib, math, pickle, stat, tempfile
import time as _time
from contextlib import contextmanager

import brain_node_documents as node_docs


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


HOME = os.path.expanduser("~")
# Claude Code names a project directory after the home path, with ':' and the
# separators replaced by '-'. Deriving it keeps this tree free of one machine's
# username and correct on any other - and unlike a template token it is a real
# value at import time.
_BRAIN_SLUG = _brain_slug_resolve()
MEM = os.environ.get("BRAIN_MEM_DIR", os.path.join(HOME, ".claude", "projects", _BRAIN_SLUG, "memory"))
DR = os.path.dirname(os.path.abspath(__file__))
# BRAIN_BM25_INDEX points a whole process (build, refresh, recall, the evals) at a SHADOW index - the tokeniser
# A/B of 2026-09-14 scores the owner-voice bank end to end on a shadow built with V3 without touching the live one.
INDEX = os.environ.get("BRAIN_BM25_INDEX") or os.path.join(DR, "bm25_index.json.gz")
LIVE_MEM = os.path.join(HOME, ".claude", "projects", "{{BRAIN_SLUG}}", "memory")
LIVE_INDEX = os.path.join(DR, "bm25_index.json.gz")


def _same_path(a, b):
    return os.path.normcase(os.path.abspath(os.fspath(a))) == os.path.normcase(os.path.abspath(os.fspath(b)))


def publishes_live_from_elsewhere(mem_dir=None, out=None):
    """True when a write would publish the LIVE index from a notes directory that is not the live one: the
    BRAIN_MEM_DIR test seam used without BRAIN_BM25_INDEX. On 5 Oct 2026 at 09:16Z a recall run that way merged a
    snapshot six notes short and handed the publish to the detached refresh, which inherited the seam and wrote the
    live index from the snapshot. The 15-minute refresh then judged it current (it compares file times), so those
    six notes were dark to every agent's recall until a full build at 09:19Z. Such writes are refused; point
    BRAIN_BM25_INDEX at a shadow index to build one for another directory."""
    mem_dir = MEM if mem_dir is None else mem_dir
    out = INDEX if out is None else out
    return _same_path(out, LIVE_INDEX) and not _same_path(mem_dir, LIVE_MEM)
_PAYLOAD_CACHE = {}   # (path, file signature) -> parsed payload; one entry in practice
K1, B = 1.2, 0.75
# term -> weight multiplying that query term's idf in both scorers; empty means every weight is 1 and scores are bit
# for bit unchanged (x * 1.0 is exact). Set by a caller for one query, e.g. a trial that gives added word forms a
# fraction of a typed word's weight (1 Nov, mesh task 1; recall_misses_word_forms_closing_vs_close_2026_10_29).
QUERY_TERM_WEIGHTS = {}
MAX_DOC_TOKENS = 1500          # the load-bearing head of a memory; keeps the index lean
MAX_ATTACHMENT_TOKENS = 3000   # extra bounded space; never displace the parent node's head
TOKEN = re.compile(r"[a-z0-9_]{3,}")
# The {3,} floor silently deletes our two-letter measurement vocabulary from BOTH the index and the
# query, so those terms cannot be recalled at all: `recall "MV correction"` and `recall "the
# correction"` returned byte-identical results. IV is in 1,584 memories, DD 875, IB 609, MV 399.
# A whitelist, not a {2,} floor - {2,} would also admit md/py/sh/yr/am/et (file extensions, units).
# Measured 2026-08-12 on shadow indexes built from one corpus at one moment, so the difference is the
# tokeniser and not corpus drift. Two arms, each with its own pre-registered gate, both PASS:
#   mv/iv/dd/ib      310 targets hit@10 92.6 -> 98.7pct, 310 controls flat
#   + Nx/1s/1m/tenor 139 targets hit@10 93.5 -> 99.3pct, 139 controls flat
# The second set exists because the first scan used [A-Za-z]{2} and structurally could not see the
# leverage-multiple vocabulary (2x, 5x, 8x - 762-888 memories each) or bar sizes (1s, 1m).
# Cost, same corpus both arms: gains all 22, loses 11 df=1 tokens pushed out of the 73 docs at
# MAX_DOC_TOKENS. OFF by default because enabling only works with a matching index rebuild - see the
# memory two_letter_terms_are_invisible_to_recall_2026_08_12 for the enable procedure.
DOMAIN2 = ({"mv", "iv", "dd", "ib"}                       # measurement vocabulary
           | {"%dx" % i for i in range(10)}               # leverage multiples: 2x, 5x, 8x
           | {"1s", "1m", "1h", "1d"}                     # bar sizes - the data law is written in 1s
           | {"1y", "2y", "3y", "5y"})                    # tenors
TOKEN2 = re.compile(r"[a-z0-9_]{2,}")
_DOMAIN2_ON = os.environ.get("BRAIN_TOKENS_DOMAIN2") == "1"
# V3 (2026-09-14, staged behind BRAIN_TOKENS_V3=1 for a shadow-index measurement): the owner's short
# alphanumeric names vanish from BOTH index and query under the {3,} floor - "v2-B" -> nothing, "rev-8" ->
# "rev", "k=0.45" -> nothing, "box 4" -> "box" - four of fourteen owner-voice recall misses on 2026-09-11 could
# not match their memory at all. V3 keeps the DOMAIN2 whitelist, keeps any token that carries a digit (v2, p100,
# 65, 24, 1s), and keeps an alphanumeric compound joined by '-' or '.' as ONE token as well as its parts
# ("v2-b" -> v2_b, v2; "0.45" -> 0_45, 45; "<remote-box>" -> research_box_03, research, box, 03).
# Single characters are still dropped (the "4" of "box 4" stays invisible - a lone digit is noise everywhere).
# DEFAULT ON since 2026-09-14 01:1x (BRAIN_TOKENS_V3=0 opts out; the live index was rebuilt with it in the same
# minute). Shadow A/B on the 56-question owner-voice bank, one corpus, one moment: hit@1 28 -> 28, hit@5 42 -> 43
# (the "v2-B" question, rank none -> 2), zero losses, one question's rank moved at all; avgdl 455 -> 492.
_V3_ON = os.environ.get("BRAIN_TOKENS_V3", "1") != "0"
_V3_COMPOUND = re.compile(r"[a-z0-9]+(?:[-.][a-z0-9]+)+")
_DIGIT = re.compile(r"[0-9]")


_ALNUM_ID = re.compile(r"^(?=.*[0-9])(?=.*[a-z])[a-z0-9_]+$")      # v2, p100, 1s, rev_8, v2_b, research_box_03
_WORDNUM_ON = os.environ.get("BRAIN_TOKENS_WORDNUM") == "1"      # staged 2026-09-14 02:0x for a shadow measurement
_V3_WORDNUM = re.compile(r"\b([a-z]{3,}) ([0-9]{1,3})\b")
_DECIMAL = re.compile(r"^[0-9]+_[0-9]+$")                             # 0_45, 0_25 - a decimal number, not a date
# Decimals are 45 pct of V3's added vocabulary (36,675 of 81,038 terms measured 2026-09-14) and 62 pct of all the
# added terms appear in exactly one memory. A partner's rules are full of numbers they type back at the brain
# (k=0.45, ratio 0.25, STEP 0.0035), so the lane is not obviously waste - it is measured, not assumed, below.
_V3_DECIMALS = os.environ.get("BRAIN_TOKENS_V3_DECIMAL", "1") != "0"
# DATES (2026-09-27, claude-825b1305; staged OFF behind BRAIN_TOKENS_DATES=1 for a shadow end-to-end measurement): a
# month and day however written - "september 2", "2 Sep", "2026-09-02", a slug's 2026_09_02 - gains one shared token
# md0902 on both sides; nothing is removed, so an exact match keeps its weight. A memory's head gets the tokens of the
# dates in its first 12,000 characters (the slug's creation date among them), after the head budget, so they never
# displace its words. Word index alone (live/study_tokenizer_variants.py; results/TOKENIZER_VARIANTS_2026-09-27.json
# and TOKENIZER_VARIANTS_BANK_2026-09-27.json): his 56 top-5 42 -> 44 (2 in, 0 out); his 82 prompts, the agents' 191
# searches and the reworded bank's paraphrase form no top-5 change; its exact-wording form 6 in, 0 out. Through the full
# `brain.py recall` on frozen shadows (live/eval_dates_endtoend.py, results/DATES_ENDTOEND_2026-09-27.json): his 56 top-5
# 47 -> 49 (2 in, 0 out), his 82 prompts top-1 5 -> 6 (no loss), 199 agent searches unchanged at top 5.
# DEFAULT ON since 27 Sep 03:1x (live index rebuilt with it the same minute); BRAIN_TOKENS_DATES=0 opts out, and a change
# of the switch needs a full `bm25.py build` so the index and every reader tokenize alike.
_DATES_ON = os.environ.get("BRAIN_TOKENS_DATES", "1") != "0"
_MONTH = {"jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3, "apr": 4, "april": 4, "may": 5,
          "jun": 6, "june": 6, "jul": 7, "july": 7, "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
          "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12}
_MONTH_ALT = "|".join(sorted(_MONTH, key=len, reverse=True))
_DATE_NUM = re.compile(r"\b20[0-9]{2}[-_ ]([01][0-9])[-_ ]([0-3][0-9])\b")
_DATE_WORD = re.compile(r"\b(" + _MONTH_ALT + r")\.? ([0-3]?[0-9])(?:st|nd|rd|th)?\b")
_DATE_WORD2 = re.compile(r"\b([0-3]?[0-9])(?:st|nd|rd|th)? (?:of )?(" + _MONTH_ALT + r")\b")


def date_tokens(text):
    """md<MMDD> for every month-day in the text, in order of the three forms (numeric, month-day, day-month)."""
    low = (text or "").lower()
    out = []
    for m in _DATE_NUM.finditer(low):
        mo, d = int(m.group(1)), int(m.group(2))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            out.append("md%02d%02d" % (mo, d))
    for m in _DATE_WORD.finditer(low):
        d = int(m.group(2))
        if 1 <= d <= 31:
            out.append("md%02d%02d" % (_MONTH[m.group(1)], d))
    for m in _DATE_WORD2.finditer(low):
        d = int(m.group(1))
        if 1 <= d <= 31:
            out.append("md%02d%02d" % (_MONTH[m.group(2)], d))
    return out


def _tokens_v3(text):
    """A strict SUPERSET of the live tokeniser: every token it emits today, plus the two-letter DOMAIN2 terms,
    plus short ALPHANUMERIC identifiers (a letter and a digit together: 3c, 1s, p100), plus each identifier-like
    '-'/'.' compound as one extra token (v2_b, rev_8, research_box_03) and each decimal number (0_45).
    Deliberately NOT admitted (first shadow, 2026-09-14 00:5x): bare integers and years ('65', '24', '2026') and
    date compounds ('2026_09_13') - they sit in every memory, grew the average document 455 -> 543 tokens, moved
    the lexical seed set on unrelated questions, and cost two owner-voice hits for the one they bought."""
    low = (text or "").lower()
    out = [t for t in TOKEN2.findall(low)
           if t not in STOP and (len(t) > 2 or t in DOMAIN2 or _ALNUM_ID.match(t))]
    for m in _V3_COMPOUND.finditer(low):
        c = m.group(0).replace("-", "_").replace(".", "_")
        if _ALNUM_ID.match(c) or (_V3_DECIMALS and _DECIMAL.match(c)):
            out.append(c)
    if _WORDNUM_ON:
        # "box 4", "version 3", "gen 8", "phase 2" - a word followed by a short number is an identifier the
        # way he types it; the lone digit stays invisible, the pair becomes one extra token (box_4).
        for m in _V3_WORDNUM.finditer(low):
            if m.group(1) not in STOP:
                out.append(m.group(1) + "_" + m.group(2))
    return out
STOP = set("""the and for that this with are was but not you your from have has had can will would should
could been being they them their there here what when where which while about into over under after
before between during against more most some such only just than then also very each other same our
ours its his her she him who whom these those does did doing done how why all any both few too own
out off above below again further once because until unless a an is it in on at by to of as be or if
we me my i he no nor so""".split())
SKIP_FILES = {"MEMORY", "INDEX_ARCHIVE", "PREDICTIONS", "STATE_OF_MIND", "OPEN_QUESTIONS",
              "AUTOBIOGRAPHY", "MODEL_OF_PARTNER", "FASCINATIONS", "THE_OTHER_MINDS", "CONSCIENCE",
              "MISTAKES", "CURIOSITY", "RECONSOLIDATION_QUEUE"}


def _base_tokens(text):
    if _V3_ON:
        return _tokens_v3(text)
    if _DOMAIN2_ON:
        return [t for t in TOKEN2.findall((text or "").lower())
                if t not in STOP and (len(t) > 2 or t in DOMAIN2)]
    return [t for t in TOKEN.findall((text or "").lower()) if t not in STOP]


def tokens(text):
    if _DATES_ON:
        return _base_tokens(text) + date_tokens(text)
    return _base_tokens(text)


_ALPHA = "abcdefghijklmnopqrstuvwxyz"


def _edits1(w):
    sp = [(w[:i], w[i:]) for i in range(len(w) + 1)]
    return set([a + b[1:] for a, b in sp if b]
               + [a + b[1] + b[0] + b[2:] for a, b in sp if len(b) > 1]
               + [a + c + b[1:] for a, b in sp if b for c in _ALPHA]
               + [a + c + b for a, b in sp for c in _ALPHA])


class QueryNormalizer:
    """Spell-normalize OUT-OF-VOCAB query tokens to their nearest corpus term (edit distance, df-tie).

    The owner types with heavy deliberate typos ([[fix_owner_spelling_automatically]]); word-BM25
    matches exact terms, so 'memeory'!='memory' surfaces nothing (the workflow-recall gap). This
    corrects only tokens NOT SOLIDLY in the corpus vocab, to a term that IS - so a clean query is
    byte-identical (a clean prompt cannot be demoted at all), while a typo'd token snaps to the right
    term. Gated for safety+speed: len 4..20 alpha tokens only (no codes/ids; a long OOV token is a real
    term, not a typo), correction target must clear a df floor (never snap to a fluke rare word),
    EDIT-DISTANCE 1 ONLY (edit2 is a hook-latency bomb and the source of most mis-corrections - see
    _correct). Measured 2026-07-09 (eval_prompt_recall, n=36): every pool-inclusion metric up (hit@10
    0.56->0.61, hit@20 0.75->0.81); MRR dips are rank-order noise, irrelevant to spread_recall's seeder
    which takes the top-5 as a POOL, not a ranked list.

    QUOTED-TYPO IMMUNITY (found+fixed 2026-07-09 night): memories QUOTE the owner's typo'd prompts, so
    within a DAY of shipping, 'memeory' reached df=5 and a bare `tok in df` short-circuit made his
    highest-frequency typos permanently uncorrectable - the records of the typos were protecting the
    typos. Fix: TYPO_DF_MAX/SNAP_RATIO (a rare in-vocab token corrects only to a >=50x-more-common
    target). Canary-verified: memeory/sidcar/gride correct; ginoe/fonk/bge/kappa/forrester/
    minilm (real rare terms) untouched; pool-inclusion metrics identical old-vs-new rule (n=41).
    HONEST MISS kept deliberately: a df=1 token ('mathmatically', quoted once) stays uncorrected -
    measured across all 8611 df=1 vocab tokens, loosening the floor to catch it would wrongly snap 10.1%
    (866 tokens, mostly REAL words: vocal->local, tour->hour, mush->must). Recurring typos climb to
    df>=2 where the ratio rule catches them; one-off quotes are not worth 866 manglings.
    """
    DF_FLOOR = 2   # only correct TO a term in >= this many memories (blocks fluke single-memory snaps;
                   # floor 1 scores marginally higher on the 36-ep snapshot but a df=1 target is almost
                   # always a wrong correction - cheap insurance for unseen queries, MRR/hit@5 unchanged)
    TYPO_DF_MAX = 8   # a token this rare may itself BE a quoted typo: memories QUOTE the owner's typo'd
                      # prompts, so 'memeory' reached df=5 within a DAY of the spell fix shipping and a
                      # bare membership test made it immune (measured 2026-07-09 night - the records of
                      # his typos were protecting the typos). Rare in-vocab tokens stay correctable...
    SNAP_RATIO = 50   # ...but only to a target MASSIVELY more common than the token itself ('memeory'
                      # df=5 -> 'memory' df=1143 corrects at ratio 229; a genuinely-new rare term whose
                      # edit-1 neighbour is merely common stays itself). Keeps false-snap risk tiny.

    def __init__(self, df):
        self.df = df

    def _correct(self, tok):
        # EDIT-DISTANCE-1 ONLY (measured 2026-07-09). Rationale: (1) latency - this runs in the
        # UserPromptSubmit hook on every prompt; edit-distance-2 on a long token generates ~(54*len)^2
        # candidates (a 34-char OOV token took 1.5s), and even medium tokens cost ~30ms each. edit1 is
        # ~0.5ms. (2) safety - the mis-corrections (combain->domain, refeclt->reject) are almost all the
        # 2-edit jumps to an unrelated common word; edit1 fixes are high-confidence single-typo repairs
        # (downalod->download). (3) the eval agrees: edit1-only gives the best ALL hit@10 (0.61); the
        # procedural lift edit2 added was thin (n=8) and on a metric that overstates the gap anyway.
        # Length caps: skip <4 (too short to correct safely) and >20 (a long OOV token is a real term).
        if len(tok) < 4 or len(tok) > 20 or any(c.isdigit() for c in tok):
            return tok
        own = self.df.get(tok, 0)
        if own > self.TYPO_DF_MAX:
            return tok         # solidly in vocabulary - never touched
        floor = max(self.DF_FLOOR, self.SNAP_RATIO * own)
        e1 = _edits1(tok)
        prefetch = getattr(self.df, "prefetch", None)   # a df read term by term (brain's recall) takes them in one batch
        if prefetch is not None:
            prefetch(e1)
        c1 = [x for x in e1 if self.df.get(x, 0) >= floor]
        return max(c1, key=lambda x: self.df[x]) if c1 else tok

    def normalize(self, query):
        m = _typo_map()
        out = []
        for t in tokens(query):
            c = self._correct(t)
            out.append(m.get(t, t) if c == t else c)
        return " ".join(out)


# THE OWNER TYPO MAP, where the edit-1 fix gives up (1 Nov 2026). claude-a9b28e2d's map of his recurring typos
# (~/brain/porting/owner_typo_map.json, built from his prompts) reaches what one edit cannot: two-edit typos and
# misspelt words (brian -> brain, platue -> plateau). It is read from the guarded copy that
# codex_fleet/recall_warm/tools/typo_map_guard.py writes (dictionary-word keys such as noam, todd and howard stay as
# typed, and so does xhigh). A token takes its map entry only when _correct leaves it as typed. Judged blind on his
# questions, own-session compactions dropped (typo_blind_round.py): this narrow use won 28 and lost 13 of 79 changed
# top 5s over three disjoint slices (sign test p 0.028, one judge, the third slice added after a look at p 0.099);
# the map applied ahead of the edit-1 fix won 14 and lost 11 of 33 (chanles -> changes and common-word respellings).
# BRAIN_TYPO_MAP=0 turns it off.
TYPO_MAP_FILE = os.path.join(HOME, ".claude", "dreaming", "owner_typo_map_guarded.json")
_TYPO_MAP = {}


def _typo_map():
    """typo -> word from the guarded map, reread when the file changes; {} if absent or switched off."""
    if os.environ.get("BRAIN_TYPO_MAP", "1") == "0":
        return {}
    try:
        st = os.stat(TYPO_MAP_FILE)
    except OSError:
        return {}
    key = (st.st_size, st.st_mtime_ns)
    if _TYPO_MAP.get("key") != key:
        try:
            with io.open(TYPO_MAP_FILE, encoding="utf-8") as fh:
                m = json.load(fh).get("map") or {}
        except (OSError, ValueError):
            m = {}
        _TYPO_MAP.update(key=key, map=m)
    return _TYPO_MAP["map"]


# DOC EXPANSION (2026-09-26, claude-825b1305). The index sees a memory in its own words, and he asks in his: measured on
# 968 reworded questions, the answer memory is in a plain search's top five a third of the time. Each memory now also
# carries three questions a person might ask whose answer is in it, written once in ordinary words by Haiku 4.5 from the
# memory's own description and opening (doc_expand.py; it never sees a test question). They are appended after the
# memory's tokens and its attachments with their own small budget, so they never displace the memory's head.
# BRAIN_DOC_EXPANSION=0 turns it off; a change of the switch needs a full `bm25.py build` to take effect everywhere.
_EXPANSION_ON = os.environ.get("BRAIN_DOC_EXPANSION", "1") != "0"
EXPANSION_FILE = os.environ.get("BRAIN_DOC_EXPANSION_FILE") or os.path.join(
    HOME, ".claude", "brain_graph", "doc_expansion", "expansions.jsonl")
MAX_EXPANSION_TOKENS = 120
_EXP_CACHE = {}


def expansions():
    """slug -> (questions, written_at) from the expansion store, parsed once per file generation; {} if absent."""
    try:
        st = os.stat(EXPANSION_FILE)
    except OSError:
        return {}
    key = (EXPANSION_FILE, st.st_size, st.st_mtime_ns)
    got = _EXP_CACHE.get(key)
    if got is None:
        got = {}
        try:
            with io.open(EXPANSION_FILE, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    try:
                        o = json.loads(line)
                    except ValueError:
                        continue
                    if o.get("slug") and o.get("q"):
                        got[o["slug"]] = (list(o["q"]), float(o.get("t") or 0.0))
        except OSError:
            return {}
        _EXP_CACHE.clear()
        _EXP_CACHE[key] = got
    return got


# A dated dream journal (dream_2026_10_16) is an aggregate that matches everything; a lesson whose slug merely
# starts with dream_ (dream_semantic_association_step_...) is an ordinary memory and must be indexed.
_JOURNAL = re.compile(r"dream_\d{4}_\d{2}_\d{2}")


def is_journal(slug):
    return bool(_JOURNAL.fullmatch(slug))


def _indexable(fn):
    """The one rule for which memory files the index sees (shared by build, refresh and evals)."""
    if not fn.endswith(".md"):
        return None
    slug = fn[:-3]
    if slug in SKIP_FILES or is_journal(slug):
        return None
    return slug


ATTACHMENT_FAILURES = []     # (slug, error type) of every bundle read_doc could not verify in this process

# read_doc's tokeniser output kept by the text's own hash (3 Nov 2026, RECALL-10X lever 11, claude-fb4584c3). In the
# resident recall server every call merges the memories newer than the published index (refresh_in_memory), and each
# call re-read and re-tokenised the same twenty or thirty files: about 1 s of a 4 s warm recall went to _tokens_v3.
# The key is the exact text, so a changed file gives a new key; the tokens are the tokeniser's own. BRAIN_BM25_TOKMEMO=0
# turns it off. Bounded: cleared whole past _TOKMEMO_MAX entries.
_TOKMEMO = {}
_TOKMEMO_MAX = 4000


def _memo_tokens(kind, text, fn):
    if os.environ.get("BRAIN_BM25_TOKMEMO", "1") == "0":
        return fn(text)
    key = (kind, hashlib.sha1(text.encode("utf-8", "surrogatepass")).digest(), len(text))
    got = _TOKMEMO.get(key)
    if got is None:
        if len(_TOKMEMO) >= _TOKMEMO_MAX:
            _TOKMEMO.clear()
        got = _TOKMEMO[key] = tuple(fn(text))
    return list(got)


def read_doc(mem_dir, slug):
    """One memory -> token list, exactly as the live index sees it (or None if unreadable/empty)."""
    try:
        with io.open(os.path.join(mem_dir, slug + ".md"), encoding="utf-8", errors="replace") as handle:
            body = handle.read()
    except Exception:
        return None
    body = re.sub(r"^---.*?---", lambda m: re.sub(r"^(name|metadata|node_type|type|originSessionId).*$",
                                                  "", m.group(0), flags=re.M),
                  body, count=1, flags=re.S)   # keep description text, drop frontmatter plumbing
    # Preserve the historical parent-node budget exactly, then give verified attachments
    # their own bounded tail.  Appending text before the old slice would make a long parent
    # silently consume the entire budget and recreate the "attached but unreadable" failure.
    # 2026-09-11: the slug (the memory's name, the field that most often carries the owner's own words for a
    # thing) counts THREE times, not once. Measured offline on the 50-question owner-voice bank and the 68
    # real-prompt episodes with the 09-03 lab (recall_rerank_lab_v2.py, mode T = slug x3 + the live job_
    # demotion): G1 hit@1 18 -> 21, hit@5 33 -> 36 of 50 with 0 losses; G2 hit@10 +1 / -0. Nine of the
    # bank's fourteen misses had the question's words in the gold memory's NAME and lost to a longer body.
    slug_words = slug.replace("_", " ")
    head = slug_words + " " + slug_words + " " + slug_words + " " + body
    if _DATES_ON:     # the date tokens of the first 12,000 characters, after the budget (see DATES above)
        tks = _memo_tokens("b", head, _base_tokens)[:MAX_DOC_TOKENS] + date_tokens(head[:12000])
    else:
        tks = _memo_tokens("t", head, tokens)[:MAX_DOC_TOKENS]
    try:
        attachment_text = node_docs.search_text_for_node(mem_dir, slug)
    except Exception as exc:
        # A bundle that fails verification adds nothing to its parent's entry, and the rest of the index is read (28 Sep,
        # scheduled audit account03/B02 r68, B02-R1-ATTACHMENT-CORRUPTION-GLOBAL-RECALL-STOP-001). Until then one broken
        # bundle stopped the whole build and emptied recall's BM25 block: the live lane reads a fresh parent here, and a
        # damaged file is fresh by its mtime. The parent's own text is still indexed; the bundle is named on stderr.
        attachment_text = ""
        ATTACHMENT_FAILURES.append((slug, type(exc).__name__))
        print("[bm25] %s: its attachment bundle failed verification (%s) and adds nothing to the index" % (
            slug, type(exc).__name__), file=sys.stderr)
    if attachment_text:
        tks.extend(_memo_tokens("t", attachment_text, tokens)[:MAX_ATTACHMENT_TOKENS])
    if _EXPANSION_ON and tks:
        q = expansions().get(slug)
        if q:
            tks.extend(tokens(" ".join(q[0]))[:MAX_EXPANSION_TOKENS])
    return tks or None


def _read_threads():
    try:
        return max(1, int(os.environ.get("BRAIN_BM25_READ_THREADS", "8")))
    except ValueError:
        return 8


def read_many(mem_dir, slugs):
    """[read_doc(mem_dir, s) for s in slugs], in the same order, on a small thread pool.
    Why (4 Nov 2026, TOP10 rank 3, claude-738954b6): a refresh's merge is file reads, not arithmetic - on a shadow
    copy the merge loop for 1,000 notes took 0.4 s and their read_doc 4-31 s. Interleaved A/B on 4,000 older notes
    (100-note chunks, ABAB): serial 7.3 ms/note median, 8 threads 2.8, tokens identical. The open and read release
    the GIL; tokenising does not, so more threads buy little. BRAIN_BM25_READ_THREADS=1 restores the serial loop."""
    slugs = list(slugs)
    w = _read_threads()
    if w <= 1 or len(slugs) < 32:
        return [read_doc(mem_dir, s) for s in slugs]
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(min(w, len(slugs))) as ex:
        return list(ex.map(lambda s: read_doc(mem_dir, s), slugs))


def read_docs(mem_dir=MEM):
    """slug -> token list, exactly as the live index sees each memory (shared by build + evals)."""
    docs = {}
    slugs = [s for s in (_indexable(fn) for fn in sorted(os.listdir(mem_dir))) if s is not None]
    for slug, tks in zip(slugs, read_many(mem_dir, slugs)):
        if tks:
            docs[slug] = tks
    return docs


def _index_signature(path):
    """Identity of a published file, including replacements with a preserved mtime."""
    try:
        return _stat_signature(os.stat(path))
    except FileNotFoundError:
        return None


def _stat_signature(info):
    # On Windows/Python 3.14, stat().ctime can still mean creation time while
    # fstat().ctime means metadata-change time. The explicit birthtime agrees
    # between path and handle; mixing the two ctime meanings prevented every
    # fallback cache write on the live index. POSIX retains its change clock.
    identity_time = (getattr(info, "st_birthtime_ns", info.st_ctime_ns)
                     if os.name == "nt" else info.st_ctime_ns)
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, identity_time)


def _stable_signature(info):
    """The identity a rename keeps, where the platform has a birth time; None elsewhere.

    On macOS os.replace moves ctime (1.1 s after a 1.1 s wait, 30 Oct 2026, APFS; inode, size, mtime and
    birth time kept), so a sidecar staged against the temp's full signature never matched the published
    index: every publish fell back to writing the pickle after the replace, and a recall in those seconds
    parsed the gzip json and wrote its own (8-13 s, 4 of 20 one-shot recalls,
    ~/codex_fleet/recall_speed/outlier.txt). Tagged so it can never equal a full signature."""
    birth = getattr(info, "st_birthtime", None) if os.name != "nt" else None
    if birth is None:
        return None
    return ("stable", info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, birth)


def _stable_index_signature(path):
    try:
        return _stable_signature(os.stat(path))
    except FileNotFoundError:
        return None


def _stage_pickle(payload, pkl, source_signature=None, fresh=None):
    """Write the sidecar for `payload`, bound to `source_signature`, to a temp file beside `pkl` and return the
    temp's path; placing it is the caller's. Shares one string object per distinct term first: a freshly BUILT
    payload holds per-doc copies of every term string, which pickle's memoizer can't fold (measured 2026-07-09:
    4.1MB / ~100ms load vs 2.7MB / ~60ms compacted - json.load gets the sharing for free from its key cache;
    build() didn't). Costs ~43ms once per write, saves ~40ms on every hook load.

    fresh: None (build, and any caller that cannot say) folds every string as above. Otherwise the slugs whose
    term counts this process made from the documents; only their terms are folded, with the slugs, onto the
    strings the payload already holds. The rest of a payload that came out of a pickle or a json parse shares
    its strings already (the pickle memo, the json key memo), and the full pass over it had grown to 2.5 s a
    publish at 21,500 memories (13 Oct 2026, fronts/R20/time_pickle_r20.py: the same 45,883,938-byte sidecar
    with and without it). The folding order is the full pass's, so the sidecar comes out byte for byte the same
    (fronts/R20/test_r20_pkl.py)."""
    if fresh is None:
        seen = {}

        def s(x):
            return seen.setdefault(x, x)

        payload = {"n": payload["n"], "avgdl": payload["avgdl"],
                   "df": {s(k): v for k, v in payload["df"].items()},
                   "dl": {s(k): v for k, v in payload["dl"].items()},
                   "tf": {s(k): {s(t): c for t, c in tf.items()} for k, tf in payload["tf"].items()}}
    else:
        df, dl, tfs = payload["df"], payload["dl"], payload["tf"]
        fresh = {k for k in fresh if k in tfs}
        if fresh:
            seen = {k: k for k in df}
            s = seen.setdefault
            dl = {s(k, k): v for k, v in dl.items()}
            tfs = {s(k, k): ({s(t, t): c for t, c in tf.items()} if k in fresh else tf) for k, tf in tfs.items()}
        # A new top-level dict either way, keyed by this file's own strings as the full pass keys it: in a json
        # parse the key "df" is the same object as the term "df", and pickling that dict as it came would share
        # them where the full pass does not (a few bytes, the same payload; test_r20_pkl.py caught it).
        payload = {"n": payload["n"], "avgdl": payload["avgdl"], "df": df, "dl": dl, "tf": tfs}
    # A delayed writer (including a reader warming its cache) may finish after a
    # newer gzip generation. Its mtime is then newer but its contents are older.
    # Keep the exact source identity with the cached payload; unbound legacy
    # sidecars are deliberately rebuilt once by the reader.
    # Keep the existing payload keys at top level so already-running processes
    # using the prior reader remain compatible during the runtime cutover.
    cached = dict(payload, _cache_schema="bm25-pickle-generation-v1",
                  _source_signature=source_signature)
    fd, tmp = tempfile.mkstemp(prefix=os.path.basename(pkl) + ".tmp.",
                               dir=os.path.dirname(os.path.abspath(pkl)))
    try:
        with os.fdopen(fd, "wb") as f:
            pickle.dump(cached, f, protocol=pickle.HIGHEST_PROTOCOL)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    return tmp


def _write_pickle(payload, pkl, source_signature=None, fresh=None):
    """Atomic best-effort sidecar write; a failure just means the next load pays the json parse. fresh: as for
    _stage_pickle."""
    tmp = None
    try:
        tmp = _stage_pickle(payload, pkl, source_signature, fresh)
        # Existing readers do not understand the new signature field. Preserve
        # their fast path too: never replace the current cache with an older
        # generation, even when a delayed caller reaches this helper last.
        index_path = pkl[:-4] if pkl.endswith(".pkl") else None
        if index_path is None or source_signature is None:
            return
        with _publication_lock(index_path):
            if _index_signature(index_path) == source_signature:
                os.replace(tmp, pkl)
    except Exception:
        pass
    finally:
        try:
            if tmp is not None and os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass


def _load_payload(path=INDEX):
    """Parsed-index load with two fast paths. The gzip+json parse costs ~170ms and runs in EVERY hook
    process (spread_recall constructs BM25() per event - the dominant hook cost, measured 2026-07-09,
    filed in OPEN_QUESTIONS as the 130ms item). Fast path 1: an in-process memo keyed by file identity
    for evals that construct BM25() thousands of times. Fast path 2: a pickle sidecar of the SAME
    parsed payload - ~50ms, 3.3x (marshal measured SLOWER here, 340ms). The json.gz stays the source
    of truth: the sidecar is trusted only if bound to the same generation, regenerated after any json
    load, and any pickle problem falls back to json. Scoring is identical by construction - the
    pickle IS the parsed json (verified equal + identical query battery 2026-07-09)."""
    path = os.fspath(path)
    pkl = path + ".pkl"
    try:
        info = os.stat(path)
        signature, stable = _stat_signature(info), _stable_signature(info)
    except FileNotFoundError:
        signature = stable = None
    key = (path, signature)
    if signature is not None and key in _PAYLOAD_CACHE:
        return _PAYLOAD_CACHE[key]
    z = None
    try:
        # Read the bytes and let go of the file before unpickling, as for the gzip below: a load that streams
        # holds the sidecar for the whole unpickle (about 1.5 s for 45 MB on a loaded laptop, 13 Oct 2026), and
        # on Windows no publish can put the next generation's sidecar in place while anyone holds it.
        with open(pkl, "rb") as f:
            blob = f.read()
        cached = pickle.loads(blob)
        del blob
        if (signature is not None and isinstance(cached, dict)
                and cached.get("_cache_schema") == "bm25-pickle-generation-v1"
                and cached.get("_source_signature") in (signature, stable)
                and _index_signature(path) == signature):
            z = {name: cached[name] for name in ("n", "avgdl", "df", "dl", "tf")}
    except Exception:
        z = None
    if z is None:
        # Bind to the handle actually read, not a stat of a pathname that can
        # have been replaced between stat() and open(). A concurrent replacement
        # may make this a prior coherent snapshot, never a mislabelled new one.
        # Read the bytes and let go of the file before parsing them. A parse that streams from the open file
        # holds it for the whole decompress and json load (9-13 s on a loaded laptop, 13 Oct 2026), and on
        # Windows no publish can replace the index while anyone holds it; reading the bytes takes 0.1 s.
        with open(path, "rb") as raw:
            signature = _stat_signature(os.fstat(raw.fileno()))
            blob = raw.read()
        with gzip.GzipFile(fileobj=io.BytesIO(blob), mode="rb") as compressed:
            with io.TextIOWrapper(compressed, encoding="utf-8") as f:
                z = json.load(f)
        del blob
        _write_pickle(z, pkl, signature, ())      # a json parse shares its strings already
        key = (path, signature)
    if signature is not None:
        _PAYLOAD_CACHE.clear()   # hold one payload, not a history of rebuilds
        _PAYLOAD_CACHE[key] = z
    return z


@contextmanager
def _publication_lock(index_path, timeout_seconds=5.0):
    """Serialize only the compare-and-replace, with an OS-released crash lock.

    Never unlink this stable inode: unlinking a live lock allows a second
    writer to lock a different inode under the same pathname.
    """
    lock_path = index_path + ".publish.lock"
    def checked_lock_info():
        info = os.lstat(lock_path)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or getattr(info, "st_file_attributes", 0) & 0x400):
            raise OSError("index publication lock must be a regular non-reparse file with one link")
        return info

    if os.path.lexists(lock_path):
        checked_lock_info()
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(lock_path, flags, 0o600)
    locked = False
    try:
        info = os.fstat(fd)
        named = checked_lock_info()
        if (info.st_dev, info.st_ino) != (named.st_dev, named.st_ino):
            raise OSError("index publication lock changed while opening")
        if info.st_size == 0:
            os.write(fd, b"0")
        until = _time.monotonic() + timeout_seconds
        while True:
            os.lseek(fd, 0, os.SEEK_SET)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
                break
            except OSError:
                if _time.monotonic() >= until:
                    raise TimeoutError("index publication lock is busy")
                _time.sleep(0.02)
        named = checked_lock_info()
        if (info.st_dev, info.st_ino) != (named.st_dev, named.st_ino):
            raise RuntimeError("index publication lock changed while acquiring")
        yield
    finally:
        try:
            if locked:
                os.lseek(fd, 0, os.SEEK_SET)
                if os.name == "nt":
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


INDEX_TEMP_STALE_S = 3600     # a publish takes seconds; an hour-old temp belongs to a process that died


def _sweep_index_temps(out, older_than=INDEX_TEMP_STALE_S):
    """Delete the staging files of publishes that never finished.

    `_publish_payload` already removes its own temp in a `finally`, and that is not enough: a process KILLED
    outright - a usage limit, a reboot, a taskkill on a starved disk - never runs it. Measured 2026-09-14 in
    the live dreaming directory: 21 abandoned `bm25_index.json.gz.tmp.*` files holding 212 MB, the oldest
    11.8 hours, on a disk with 42 GB free. The same directory had 60 of them on 2026-09-13
    (the_bm25_index_leaves_orphaned_temp_files_2026_09_13); they were swept by hand and came straight back,
    because sweeping is not a fix unless something does it every time. At roughly 10 MB a temp and 40 deaths
    a day this leaks about 400 MB a day.

    An hour is deliberately generous: a publish writes and replaces in seconds, so nothing in flight is ever
    close, and a failed delete (someone still holds the handle) is simply next publish's work."""
    import glob
    now = _time.time()
    out = os.fspath(out)
    for pat in (out + ".tmp.*", out + ".pkl.tmp.*"):
        for junk in glob.glob(pat):
            try:
                if now - os.stat(junk).st_mtime > older_than:
                    os.remove(junk)
            except OSError:
                pass


PUBLISH_REPLACE_WAIT_S = 20.0   # how long a publish waits for a reader to let go of the index (Windows)


def _replace_waiting_out_readers(src, dst, wait_s=None):
    """os.replace that waits, for a bounded time, for a reader to let go of dst.

    On Windows a file opened with a plain open() is shared without FILE_SHARE_DELETE, and replacing it fails with
    WinError 5 for as long as the reader holds it. Measured 13 Oct 2026: a reader that parses the gzip as it
    streams holds the index 9-13 s on this laptop under load, and 10 of 114 publish attempts between 12 Oct 21:00Z
    and 13 Oct 03:40Z died that way, each one a whole refresh thrown away with its memories left out of the index.
    The caller holds the publication lock, so nothing else can publish while this waits, and the generation it
    checked is still the one it replaces. Any other error, or the wait running out, raises as before."""
    wait_s = PUBLISH_REPLACE_WAIT_S if wait_s is None else wait_s
    deadline = _time.monotonic() + wait_s
    delay = 0.05
    while True:
        try:
            os.replace(src, dst)
            return
        except PermissionError as exc:
            if os.name != "nt":
                raise
            if _time.monotonic() >= deadline:
                # Two publishes still died this way after the wait landed (13 Oct 04:41-04:44Z) and nothing said who
                # held the file, so the error now carries it. Asked only once the publish has already failed.
                try:
                    exc.add_note("held when the wait ran out - index: %s; staged copy: %s"
                                 % (_who_holds(dst), _who_holds(src)))
                except Exception:
                    pass
                raise
            _time.sleep(delay)
            delay = min(delay * 2, 0.5)


def _who_holds(path, limit=16):
    """The processes that have `path` open, as 'pid N name (command line)' joined by '; ', from the Windows Restart
    Manager: 'nobody' when it finds none, 'unknown (...)' when it cannot tell. Best effort, never raises."""
    if os.name != "nt":
        return "unknown (not Windows)"
    try:
        import ctypes
        from ctypes import wintypes

        class _UniqueProcess(ctypes.Structure):
            _fields_ = [("dwProcessId", wintypes.DWORD), ("ProcessStartTime", wintypes.FILETIME)]

        class _ProcessInfo(ctypes.Structure):
            _fields_ = [("Process", _UniqueProcess), ("strAppName", wintypes.WCHAR * 256),
                        ("strServiceShortName", wintypes.WCHAR * 64), ("ApplicationType", ctypes.c_int),
                        ("AppStatus", wintypes.ULONG), ("TSSessionId", wintypes.DWORD), ("bRestartable", wintypes.BOOL)]

        rm = ctypes.WinDLL("rstrtmgr")
        rm.RmStartSession.argtypes = [ctypes.POINTER(wintypes.DWORD), wintypes.DWORD, wintypes.LPWSTR]
        rm.RmRegisterResources.argtypes = [wintypes.DWORD, wintypes.UINT, ctypes.POINTER(wintypes.LPCWSTR),
                                           wintypes.UINT, ctypes.c_void_p, wintypes.UINT, ctypes.c_void_p]
        rm.RmGetList.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.UINT), ctypes.POINTER(wintypes.UINT),
                                 ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)]
        rm.RmEndSession.argtypes = [wintypes.DWORD]
        for fn in (rm.RmStartSession, rm.RmRegisterResources, rm.RmGetList, rm.RmEndSession):
            fn.restype = wintypes.DWORD
        session = wintypes.DWORD()
        key = ctypes.create_unicode_buffer(33)
        rc = rm.RmStartSession(ctypes.byref(session), 0, key)
        if rc:
            return "unknown (RmStartSession %d)" % rc
        try:
            files = (wintypes.LPCWSTR * 1)(os.path.abspath(path))
            rc = rm.RmRegisterResources(session, 1, files, 0, None, 0, None)
            if rc:
                return "unknown (RmRegisterResources %d)" % rc
            size = limit
            for _ in range(3):
                infos = (_ProcessInfo * size)()
                needed, count, reasons = wintypes.UINT(0), wintypes.UINT(size), wintypes.DWORD(0)
                rc = rm.RmGetList(session, ctypes.byref(needed), ctypes.byref(count), infos, ctypes.byref(reasons))
                if rc != 234:                       # ERROR_MORE_DATA: the list grew; ask again with room for it
                    break
                size = needed.value + 4
            if rc:
                return "unknown (RmGetList %d)" % rc
            found = [(int(i.Process.dwProcessId), i.strAppName or "?") for i in infos[:count.value]]
        finally:
            rm.RmEndSession(session)
    except Exception as exc:
        return "unknown (%s: %s)" % (type(exc).__name__, exc)
    if not found:
        return "nobody"
    out = []
    for pid, name in found:
        cmd = ""
        try:
            import psutil
            cmd = " ".join(psutil.Process(pid).cmdline())[:240]
        except Exception:
            pass
        out.append("pid %d %s%s" % (pid, name, (" (%s)" % cmd) if cmd else ""))
    return "; ".join(out)


JSON_WRITE_SLICE = 1 << 23       # characters of json text encoded and written at a time


def _write_json_text(f, obj):
    """Write obj as json to the binary file f: json.dumps once, then encode and write it in 8 MB slices.

    json.dump(obj, f) was three quarters of a publish. CPython uses its C encoder only for the one-shot
    json.dumps; json.dump runs the pure-Python encoder and pushes every small piece through the gzip text
    wrapper. Measured 13 Oct 2026 on the live payload (21,509 docs, 87 MB of text, compresslevel 2, loaded
    laptop): json.dump 17.0 and 17.6 s, json.dumps plus the writes 4.2 and 4.4 s, the decompressed text
    identical (fronts/R20/time_publish_r20.py). The slices keep the encoded copy at 8 MB instead of a second
    87 MB. BRAIN_BM25_JSON_ONESHOT=0 restores json.dump."""
    s = json.dumps(obj)
    for i in range(0, len(s), JSON_WRITE_SLICE):
        f.write(s[i:i + JSON_WRITE_SLICE].encode("utf-8"))


def _publish_payload(payload, out, expected_signature, scan_t0, added, removed,
                     compresslevel=9, manifests=None, fresh_docs=None):
    """Publish only if the generation we observed is still current.

    Compression happens before the lock; checking before compression left a
    real race in which refresh overwrote a full rebuild completed meanwhile.
    manifests: the attachment manifest digests taken at the scan's start, recorded in the marker (see
    attachment_manifest_digests). fresh_docs: the slugs this process re-read, for the sidecar (see _stage_pickle);
    None folds the whole payload.

    THE PICKLE SIDECAR GOES IN WITH THE INDEX, NOT SECONDS AFTER IT (2026-10-13). It used to be written after
    the replace: 11.9 s after one publish on the loaded laptop (16:26:50 -> 16:27:01), at about one publish a
    minute. A reader in that gap without the postings sidecar for the new generation - and that sidecar is
    built a few seconds after the publish - found the pickle bound to the old generation and parsed the gzip
    json and wrote its own pickle: 12-20 s, measured in 4 of 80 in-process recalls (R19 diag_d2_stacks), and the
    same path serves every prompt's spread_recall. Now the pickle is written BEFORE the lock, bound to the
    identity the staged gzip carries into the live path - os.replace keeps st_dev, st_ino, size, mtime and birth
    time (6 of 6 on this volume, 13 Oct 2026) - and put in place right after the index under the same lock. If
    the identity came out different, or a reader still held the old sidecar, it is placed after a bounded wait or
    written the old way: the binding is checked, never assumed. BRAIN_BM25_PKL_EARLY=0 restores the old order.
    """
    _sweep_index_temps(out)
    fd, tmp = tempfile.mkstemp(prefix=os.path.basename(out) + ".tmp.",
                               dir=os.path.dirname(os.path.abspath(out)))
    os.close(fd)
    pkl = out + ".pkl"
    pkl_tmp = staged = None
    try:
        if os.environ.get("BRAIN_BM25_JSON_ONESHOT", "1") != "0":
            with gzip.open(tmp, "wb", compresslevel=compresslevel) as f:
                _write_json_text(f, payload)
        else:
            with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=compresslevel) as f:
                json.dump(payload, f)
        if os.environ.get("BRAIN_BM25_PKL_EARLY", "1") != "0":
            try:
                staged = _stable_index_signature(tmp) or _index_signature(tmp)
                pkl_tmp = _stage_pickle(payload, pkl, staged, fresh_docs) if staged is not None else None
            except Exception:
                pkl_tmp = staged = None
        pre = _prebuild_postings(payload, out, expected_signature)
        placed = False
        with _publication_lock(out):
            if _index_signature(out) != expected_signature:
                return None
            _replace_waiting_out_readers(tmp, out)
            signature = _index_signature(out)
            bound = _stable_index_signature(out) or signature
            if pkl_tmp is not None and bound == staged:
                try:
                    os.replace(pkl_tmp, pkl)
                    placed = True
                except OSError:
                    pass            # a reader holds the old sidecar: wait for it below, outside this lock
            _write_scan_marker(out, scan_t0, added, removed, signature, manifests)
            if pre is not None:
                _place_prebuilt(pre, out, signature)
        if not placed and pkl_tmp is not None and bound == staged:
            try:
                with _publication_lock(out):
                    if _index_signature(out) == signature:
                        _replace_waiting_out_readers(pkl_tmp, pkl)
                        placed = True
            except Exception:
                pass
        if not placed:
            _write_pickle(payload, pkl, signature, fresh_docs)
        _PAYLOAD_CACHE.clear()
        return signature
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
        try:
            if pkl_tmp is not None and os.path.exists(pkl_tmp):
                os.remove(pkl_tmp)
        except OSError:
            pass
        try:
            if pre is not None and os.path.exists(pre):
                os.remove(pre)      # the publish lost its race, or the placing failed
        except (OSError, NameError):
            pass


def build(mem_dir=MEM, out=INDEX, _attempt=0):
    out = os.fspath(out)
    if publishes_live_from_elsewhere(mem_dir, out):
        print("bm25 build: REFUSED - would publish the live index from %s, which is not the live notes directory; "
              "set BRAIN_BM25_INDEX to a shadow path" % mem_dir)
        return 0, 0
    # HOLD THE GENERATION WHILE WE REBUILD, OR THE REFRESHES WIN EVERY TIME (2026-09-15).
    # _publish_payload refuses to publish if the index changed under it, and build() gives up after
    # three such losses with "index changed during three full builds". That is not a memory-write
    # race: it is a race with the INCREMENTAL REFRESH, which every remember() and every 15-minute
    # task kicks. A full rebuild over 16,000 memories takes ~100 s; with eight sessions writing
    # about 27 memories an hour, a refresh lands inside that window essentially always, so the
    # nightly full build could not finish - it failed in the 11:31 dream at 17:16 having burned
    # 5 minutes on three attempts. (The published index stayed current the whole time because the
    # refreshes themselves publish, which is why this was quiet rather than visible.)
    # The coordination piece already exists and refresh() already honours it: freeze the generation
    # for the build's duration. A freeze past its own deadline is ignored and removed on sight, so a
    # crashed builder cannot stall the lane. Only the OUTERMOST attempt takes it, and we never touch
    # a freeze somebody else is holding - an eval mid-run keeps its generation and we simply lose,
    # which is the correct outcome.
    _took_freeze = False
    if _attempt == 0:
        try:
            if not freeze_state(out)[0]:
                freeze(15, "bm25 full rebuild in progress (pid %d)" % os.getpid(), index_path=out, kind="build")
                _took_freeze = True
        except Exception:
            _took_freeze = False
    try:
        return _build_inner(mem_dir, out, _attempt)
    finally:
        if _took_freeze:
            try:
                unfreeze(out)
            except Exception:
                pass


def _build_inner(mem_dir, out, _attempt=0):
    expected_signature = _index_signature(out)
    scan_t0 = _time.time()          # recorded beside the index: the next refresh's reference time
    manifests = attachment_manifest_digests(mem_dir)
    docs = read_docs(mem_dir)
    df, tfs, dl = {}, {}, {}
    for slug, tks in docs.items():
        tf = {}
        for t in tks:
            tf[t] = tf.get(t, 0) + 1
        tfs[slug] = tf
        dl[slug] = len(tks)
        for t in tf:
            df[t] = df.get(t, 0) + 1
    n = len(docs)
    tfs, dl = _build_keep_order(out, tfs, dl)
    avgdl = (sum(dl.values()) / n) if n else 0.0
    payload = {"n": n, "avgdl": avgdl, "df": df, "dl": dl, "tf": tfs}
    if _publish_payload(payload, out, expected_signature, scan_t0, n, 0, manifests=manifests) is None:
        if _attempt >= 2:
            raise RuntimeError("index changed during three full builds; retry when writers settle")
        return build(mem_dir, out, _attempt=_attempt + 1)
    return n, len(df)


def _build_keep_order(out, tfs, dl):
    """(tfs, dl) reordered as an incremental refresh would have left them: the documents the newest postings sidecar of
    `out` holds with the same fingerprint first, in its position order, then the rest in the order build() read them.
    The sidecar's incremental build keeps only the payload prefix that matches its base, and build()'s sorted order
    broke that prefix within the first hundred documents, so every full build() used to cost a full sidecar build
    (4 Nov 2026). Scores do not depend on the order; it decides only which of two equal scores is listed first, as
    refresh()'s appends always have. No sidecar, any error, or BM25_BUILD_KEEP_ORDER=0: unchanged."""
    if os.environ.get("BM25_BUILD_KEEP_ORDER", "1") == "0":
        return tfs, dl
    try:
        import sqlite3
        base = _postings_base(out, "")
        if base is None:
            return tfs, dl
        con = sqlite3.connect("file:%s?mode=ro" % base.replace("?", "%3f"), uri=True)
        try:
            rows = con.execute("select pos, slug from d order by pos").fetchall()
            fps = dict(con.execute("select pos, h from fp"))
        finally:
            con.close()
        head = [s for p, s in rows if s in tfs and fps.get(p) == _doc_fp(tfs[s], dl[s])]
        if not head or len(set(head)) != len(head):
            return tfs, dl
        hs = set(head)
        order = head + [s for s in tfs if s not in hs]
        if len(order) != len(tfs):
            return tfs, dl
        return {s: tfs[s] for s in order}, {s: dl[s] for s in order}
    except Exception:
        return tfs, dl


REFRESH_LOCK_STALE_S = 180        # a lock older than this belongs to a dead process
REFRESH_FULL_REBUILD_AT = 400     # more fresh files than this: the READ path (refresh_in_memory) merges nothing
# refresh() itself merges up to REFRESH_MERGE_MAX fresh files and builds above it. Measured 4 Nov 2026 on a shadow of the
# live index (51k memories, laptop under load): merge + gzip-2 publish 98 s at 400 fresh, 209 s at 5000, while build()'s
# read of every memory alone took 506 s (recall_10x_2026_10_29/folder_drop_2026_11_04/refresh_vs_build_cost.py).
REFRESH_MERGE_MAX = int(os.environ.get("BM25_REFRESH_MERGE_MAX", "5000"))


def scan_reference(index_path=INDEX):
    """The time the index's content was read from disk; 0.0 when there is no index. A memory file or attachment
    changed at or after it may be missing from the index. THE REFERENCE TIME IS THE LAST SCAN'S START, NOT THE INDEX
    FILE'S MTIME. A refresh scans at T0 and writes at T1 (several seconds later); a memory written between T0 and T1
    carries an mtime older than the new index and would never look fresh again until the nightly build. refresh()
    records T0 in a marker beside the index; a marker that predates the current index generation (a build() replaced
    the index since) is ignored and the index mtime minus a one-minute margin is used instead. Re-indexing a memory
    that was already absorbed is harmless (replace), so the margin errs early."""
    try:
        built = os.path.getmtime(index_path)
    except OSError:
        return 0.0
    since = built - 60.0
    try:
        with io.open(index_path + ".scan.json", encoding="utf-8") as fh:
            marker = json.load(fh)
        t0 = float(marker.get("scanned_at", 0.0))
        bound = marker.get("source_signature")
        if (bound is None or tuple(bound) == _index_signature(index_path)) and built - 3600.0 <= t0 <= built + 1.0:
            since = t0 - 1.0
    except Exception:
        pass
    return since


def attachment_mtimes(mem_dir=MEM):
    """[(slug, newest mtime over every file in its attachment bundle)]: one scandir per bundle and no validation
    (about 1 ms for the live store's bundles; the validated route through node_docs takes about 200 ms). It only
    decides which parents to re-read; read_doc reads their attachments through the validated route."""
    out = []
    try:
        with os.scandir(os.path.join(mem_dir, node_docs.BUNDLE_DIRNAME)) as it:
            bundles = [e for e in it if e.is_dir(follow_symlinks=False)]
    except OSError:
        return out
    for b in bundles:
        newest, stack = 0.0, [b.path]
        while stack:
            try:
                with os.scandir(stack.pop()) as it:
                    for e in it:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        else:
                            try:
                                newest = max(newest, e.stat(follow_symlinks=False).st_mtime)
                            except OSError:
                                pass
            except OSError:
                pass
        out.append((b.name, newest))
    return out


def attachment_manifest_digests(mem_dir=MEM):
    """{slug: sha256 of its bundle's manifest bytes} for every attachment bundle. The manifest names each document by
    its content hash, so a bundle replaced by different documents has a different manifest even when every file keeps
    its old mtime (a restore with preserved times, rsync -a, cp -p). About 40 small reads on the live store."""
    out = {}
    try:
        with os.scandir(os.path.join(mem_dir, node_docs.BUNDLE_DIRNAME)) as it:
            bundles = [e for e in it if e.is_dir(follow_symlinks=False)]
    except OSError:
        return out
    for b in bundles:
        try:
            with open(os.path.join(b.path, node_docs.MANIFEST_NAME), "rb") as fh:
                out[b.name] = hashlib.sha256(fh.read()).hexdigest()
        except OSError:
            out[b.name] = None
    return out


def _marker_manifests(index_path=INDEX):
    """The manifest digests recorded with the current index generation, or None when the marker is missing, predates
    the field, or belongs to another generation (same binding rule as scan_reference)."""
    try:
        with io.open(index_path + ".scan.json", encoding="utf-8") as fh:
            marker = json.load(fh)
    except Exception:
        return None
    bound = marker.get("source_signature")
    recorded = marker.get("attachment_manifests")
    if bound is None or not isinstance(recorded, dict) or tuple(bound) != _index_signature(index_path):
        return None
    return recorded


_FM_IDC_ON = os.environ.get("BM25_FM_IDCACHE", "1") != "0"
_FM_IDC = {"since": None, "c": {}}


def fresh_memories(mem_dir=MEM, index_path=INDEX, entries=None):
    """(fresh, gone): slugs whose .md is newer than the index (new or edited) or whose attachments changed since it
    was read, and indexed slugs whose file no longer exists. One scandir + one stat per entry - ~30 ms on the live
    corpus, so recall() can afford it on every call. Returns ([], []) when the index does not exist (build() is the
    answer then). `entries` is a DirEntry listing of mem_dir the caller already took: brain.py's recall passes its
    one listing, whose stats the supersession reader has usually paid for already (30 Oct: this second scan's 28k
    stats were 1.1 s of a recall at load 48)."""
    if not os.path.exists(index_path):
        return [], []
    since = scan_reference(index_path)
    fresh = []
    present = set()
    if entries is not None and _FM_IDC_ON:
        # A caller's listing (the recall server's resident one) keeps an unchanged file's entry object and replaces it
        # when the folder watcher reports that name, so an entry object we have seen has the stat we already read:
        # name -> (entry, slug, fresh), reset when the index's scan reference moves. Same answer as the loop below on
        # 22 of 22 forced folder drops, 0.3 s off the median served call (4 Nov 2026, claude-738954b6, TOP10 rank 3,
        # recall_10x_2026_10_29/folder_drop_2026_11_04/incr_parity.py). BM25_FM_IDCACHE=0 turns it off.
        if _FM_IDC["since"] != since:
            _FM_IDC["since"], _FM_IDC["c"] = since, {}
        c = _FM_IDC["c"]
        get = c.get
        for e in entries:
            n = e.name
            v = get(n)
            if v is None or v[0] is not e:
                slug = _indexable(n)
                f = False
                if slug is not None:
                    try:
                        f = e.stat().st_mtime >= since
                    except OSError:
                        f = False
                v = c[n] = (e, slug, f)
            if v[1] is None:
                continue
            present.add(v[1])
            if v[2]:
                fresh.append(v[1])
        if len(c) > 2 * len(present) + 5000:
            _FM_IDC["c"] = {}
    else:
        import contextlib as _cl
        with (_cl.nullcontext(iter(entries)) if entries is not None else os.scandir(mem_dir)) as it:
            for e in it:
                slug = _indexable(e.name)
                if slug is None:
                    continue
                present.add(slug)
                try:
                    if e.stat().st_mtime >= since:
                        fresh.append(slug)
                except OSError:
                    continue
    # A memory whose attachments changed after the last scan is fresh too: read_doc indexes their text, so what the
    # index should hold for it changed (28 Sep, account02/B02 R1, B02-R1-ATTACHMENT-REFRESH-DROPS-LIVE-PROMOTION-001:
    # until then an incremental refresh never re-read an attachment-only change, and its newer index then switched off
    # brain.py's live promotion of that attachment while the index still lacked the text).
    have = set(fresh)
    for slug, t in attachment_mtimes(mem_dir):
        if t >= since and slug in present and slug not in have:
            fresh.append(slug)
    # ...and so is one whose bundle manifest differs from the one recorded when its text was read, whatever the mtimes
    # say (28 Sep, account03/B02 r71, B02-R23-ATTACHMENT-STALE-BM25-DIGEST-BYPASS-001: a bundle replaced with its old
    # mtimes kept was never re-read, so its old text went on ranking the parent). A parent with no recorded digest is
    # left to the mtime test above.
    recorded = _marker_manifests(index_path)
    if recorded:
        have = set(fresh)
        current = attachment_manifest_digests(mem_dir)
        for slug in sorted(set(recorded) | set(current)):
            # a changed manifest, or a bundle recorded then removed (its directory is gone, so no mtime can say so)
            if (slug in recorded and recorded[slug] != current.get(slug) and slug in present and slug not in have):
                fresh.append(slug)
    # A memory whose questions were written after the last scan is fresh too: its file did not change, but what the
    # index should hold for it did. One stat in the common case (the store is older than the scan).
    if _EXPANSION_ON:
        try:
            if os.path.getmtime(EXPANSION_FILE) >= since:
                have = set(fresh)
                for slug, (_q, t) in expansions().items():
                    if t >= since and slug in present and slug not in have:
                        fresh.append(slug)
        except OSError:
            pass
    # the gone-scan needs the payload (~0.4 s from the pickle sidecar); a deletion with no fresh file
    # must still be caught, or a deleted memory keeps ranking until the nightly build (measured 2026-09-02)
    try:
        gone = [s for s in _load_payload(index_path)["tf"] if s not in present]
    except Exception:
        gone = []
    return sorted(fresh), sorted(gone)


def _write_scan_marker(index_path, scan_t0, added, removed, source_signature=None, manifests=None):
    """Beside the index: when the writer's directory scan STARTED (the next refresh's reference time -
    see fresh_memories), plus a small receipt. Best-effort; absence falls back to the index mtime."""
    try:
        marker = {"scanned_at": float(scan_t0), "written_at": _time.time(),
                  "added": int(added), "removed": int(removed), "writer": os.getpid(),
                  "source_signature": source_signature}
        if manifests is not None:
            marker["attachment_manifests"] = manifests
        tmp = "%s.scan.json.tmp.%d" % (index_path, os.getpid())
        with io.open(tmp, "w", encoding="utf-8") as fh:
            json.dump(marker, fh)
        os.replace(tmp, index_path + ".scan.json")
    except Exception:
        pass


def _stamp():
    return _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime())


FREEZE_MAX_MINUTES = 240   # a freeze longer than this is a leak, not a run; ignored past its own deadline anyway


def freeze_state(index_path=INDEX):
    """(frozen: bool, why: str). A consumer that must observe ONE index generation (a recall receipt, a
    probe suite, an eval) writes <index>.freeze = {"until": epoch, "why": ..., "holder": pid}; refresh()
    then skips until that deadline or until unfreeze(). This is the coordination piece the 2026-08-17
    analysis (fresh_tail.py) said was missing before an incremental append could be trusted: generation
    churn is a scheduling problem, and a freeze is the schedule. A freeze past its own deadline is stale
    and ignored (removed on sight), so a crashed holder cannot stall the lane."""
    f = index_path + ".freeze"
    try:
        with io.open(f, encoding="utf-8") as fh:
            d = json.load(fh)
        until = float(d.get("until", 0))
        if until >= _time.time():
            return True, "%s (holder %s, %.0f s left)" % (d.get("why", "?"), d.get("holder", "?"), until - _time.time())
        try:
            os.remove(f)
        except OSError:
            pass
        return False, "stale freeze removed"
    except FileNotFoundError:
        return False, ""
    except Exception as exc:
        return True, "unreadable freeze file (%s) - treating as frozen, fix or remove %s" % (exc, f)


def freeze(minutes, why, index_path=INDEX, kind="generation"):
    """kind "generation" (default): a consumer needs ONE index generation - refresh() and the read path's
    in-memory merge both stand still. kind "build": the full rebuild only needs refresh() not to publish
    over it; the read path keeps merging fresh memories in memory (it publishes nothing)."""
    minutes = max(1.0, min(float(minutes), FREEZE_MAX_MINUTES))
    d = {"until": _time.time() + minutes * 60.0, "why": str(why)[:200], "holder": os.getpid(),
         "since": _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime()), "kind": str(kind)}
    tmp = "%s.freeze.tmp.%d" % (index_path, os.getpid())
    with io.open(tmp, "w", encoding="utf-8") as fh:
        json.dump(d, fh)
    os.replace(tmp, index_path + ".freeze")
    return d


def read_merge_frozen(index_path=INDEX):
    """True when the read path's in-memory merge of fresh memories must stand still: any live freeze except
    one the full rebuild took for itself (kind "build"). Until 4 Nov 2026 the rebuild's 15-minute freeze
    also stopped the merge, so recall silently dropped every memory filed since the last index for the
    whole build (memory bm25_rebuild_freeze_makes_new_memories_dark_to_recall_silently_2026_11_03)."""
    frozen, _why = freeze_state(index_path)
    if not frozen:
        return False
    try:
        with io.open(index_path + ".freeze", encoding="utf-8") as fh:
            return json.load(fh).get("kind") != "build"
    except Exception:
        return True


def unfreeze(index_path=INDEX):
    try:
        os.remove(index_path + ".freeze")
        return True
    except OSError:
        return False


def _pid_is_alive(pid):
    """True when that pid is a live process, False when it certainly is not, None when unknowable.
    Only a definite False may shorten a lock's life - an unknown must fall back to the age rule."""
    if not pid or pid <= 0:
        return None
    try:
        if os.name == "nt":
            import ctypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            k = ctypes.windll.kernel32
            h = k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
            if h:
                code = ctypes.c_ulong()
                ok = k.GetExitCodeProcess(h, ctypes.byref(code))
                k.CloseHandle(h)
                if ok:
                    return code.value == 259          # STILL_ACTIVE
                return True
            # ERROR_INVALID_PARAMETER (87) means no such process; anything else is a permission
            # question, and a process we may not open is a process that exists.
            return False if k.GetLastError() == 87 else None
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except Exception:
        return None


def _touch_lock(lock):
    try:
        os.utime(lock, None)
    except OSError:
        pass


def _refresh_lock(index_path):
    lock = index_path + ".refresh.lock"
    try:
        st = os.stat(lock)
        stale = _time.time() - st.st_mtime > REFRESH_LOCK_STALE_S
        if not stale:
            # A DEAD HOLDER'S LOCK BLOCKS FOR ITS FULL LIFETIME OTHERWISE (measured 2026-09-21: a
            # refresh killed mid-run left a lock whose pid no longer existed, and the next three
            # minutes of refreshes reported "index already current" over two unindexed memories).
            # Only a DEFINITE "that pid is gone" shortens it; unknown keeps the age rule.
            try:
                holder = int(io.open(lock, encoding="utf-8").read().strip() or 0)
            except Exception:
                holder = 0
            stale = _pid_is_alive(holder) is False
        if stale:
            os.remove(lock)
    except OSError:
        pass
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return lock
    except OSError:
        return None


def refresh(mem_dir=MEM, out=INDEX, quiet=False, build_if_absent=False):
    """Incremental index update: add/replace every memory whose file is newer than the index, drop the ones
    whose file is gone, rewrite the payload (same format as build) atomically. Returns (added, removed);
    (0, 0) when nothing changed - the common case, and the reason it is cheap enough for recall().
    Best-effort by design: a held lock, a missing index or a race with a concurrent build() means "skip
    this time"; the nightly build() remains the exact, canonical rebuild."""
    out = os.fspath(out)
    if publishes_live_from_elsewhere(mem_dir, out):
        if not quiet:
            print("bm25 refresh: REFUSED - would publish the live index from %s, which is not the live notes "
                  "directory; set BRAIN_BM25_INDEX to a shadow path" % mem_dir)
        return 0, 0
    frozen, why = freeze_state(out)
    if frozen:
        if not quiet:
            print("bm25 refresh: index generation is FROZEN - %s" % why)
        return 0, 0
    expected_signature = _index_signature(out)
    # With no index there is nothing to update incrementally (fresh_memories() returns no work), so a caller
    # that is writing - the detached CLI refresh, the child remember_index spawns for a just-saved memory - may
    # ask for the full build here. recall() never does: it calls refresh() on every query and a rebuild must
    # not run where somebody is waiting. Ported from vanilla-brain 1281b2a (22 Sep 2026).
    if expected_signature is None and build_if_absent and os.path.isdir(mem_dir):
        res = build(mem_dir, out)
        spawn_detached_postings()
        return (res[0] if isinstance(res, tuple) else int(res or 0)), 0
    scan_t0 = _time.time()          # the reference time the NEXT refresh compares file mtimes against
    current = attachment_manifest_digests(mem_dir)
    fresh, gone = fresh_memories(mem_dir, out)
    if not fresh and not gone:
        return 0, 0
    # The marker claims a digest only for what the index holds: the parents re-read now get the digest taken at this
    # scan's start, the rest keep the digest their content was read under, and a parent with none recorded stays
    # unknown until the next full build (it then falls back to the mtime test alone).
    manifests = {s: d for s, d in (_marker_manifests(out) or {}).items() if s in current and s not in gone}
    for s in fresh:
        if s in current:
            manifests[s] = current[s]
        else:
            manifests.pop(s, None)
    if len(fresh) > REFRESH_MERGE_MAX:
        n, _v = build(mem_dir, out)
        spawn_detached_postings()      # this path publishes too, and leaves the sidecar a generation behind
        return n, 0
    lock = _refresh_lock(out)
    if lock is None:
        return 0, 0
    try:
        if _index_signature(out) != expected_signature:
            return 0, 0
        z = _load_payload(out)
        df, dl, tfs = dict(z["df"]), dict(z["dl"]), dict(z["tf"])
        def _drop(slug):
            old = tfs.pop(slug, None)
            dl.pop(slug, None)
            if old:
                for t in old:
                    c = df.get(t, 0) - 1
                    if c <= 0:
                        df.pop(t, None)
                    else:
                        df[t] = c
        added = 0
        toks = []
        for c in range(0, len(fresh), 250):
            if c:
                _touch_lock(lock)     # a merge of thousands outlives REFRESH_LOCK_STALE_S otherwise (4 Nov 2026)
            toks.extend(read_many(mem_dir, fresh[c:c + 250]))
        for slug, tks in zip(fresh, toks):
            _drop(slug)
            if not tks:
                continue
            tf = {}
            for t in tks:
                tf[t] = tf.get(t, 0) + 1
            tfs[slug] = tf
            dl[slug] = len(tks)
            for t in tf:
                df[t] = df.get(t, 0) + 1
            added += 1
        for slug in gone:
            _drop(slug)
        n = len(tfs)
        payload = {"n": n, "avgdl": (sum(dl.values()) / n) if n else 0.0, "df": df, "dl": dl, "tf": tfs}
        _touch_lock(lock)
        # The compare and replace are serialized with full builds, AFTER the
        # staged gzip is complete. A pathname-mtime check before compression
        # cannot prevent overwriting a newer generation.
        try:
            if _publish_payload(payload, out, expected_signature, scan_t0,
                                added, len(gone), compresslevel=2, manifests=manifests, fresh_docs=fresh) is None:
                return 0, 0
        except TimeoutError:
            return 0, 0
        if not quiet:
            print("bm25 refresh: +%d fresh memor%s, -%d gone -> %d docs" % (added, "y" if added == 1 else "ies", len(gone), n))
        # A PUBLISH RETIRES THE SIDECAR, SO THE PUBLISHER IS WHO MUST REPLACE IT (2026-09-14, measured).
        # Only the `bm25.py refresh` CLI used to rebuild it, but refresh() is also called IN PROCESS - by
        # the recall self-heal, by remember(), by the health check - and every one of those publishes left
        # the cache dark until some later CLI run caught up. Polled liveness came out at 20 pct. The build
        # still never runs where anyone is waiting: it is handed to a detached windowless process. A sidecar prebuilt
        # with the publish (P1) needs none.
        if not PREBUILT_LAST.get("sidecar"):
            spawn_detached_postings()
        return added, len(gone)
    finally:
        try:
            os.remove(lock)
        except OSError:
            pass


def refresh_in_memory(mem_dir=MEM, out=INDEX):
    """The incremental merge of refresh() WITHOUT the publish: returns (payload, added, removed) where
    payload is exactly what refresh() would have written, or (None, 0, 0) when nothing is fresh, the
    generation is frozen, the index is missing, or the fresh set is large enough that refresh() would
    do a full build instead. Nothing is written and no lock is taken.

    Why (2026-09-13): a deliberate recall that found the index older than 15 minutes ran refresh(),
    and refresh() PUBLISHES - 99 s of one 147 s recall went to json.dump of the 20 MB index and its
    31 MB pickle, synchronously, while a person waited (memory
    a_deliberate_recall_republishes_the_whole_index_2026_09_13). The read path now merges the fresh
    memories in memory, scores this query on the merged payload (same ranking the published index
    would give), and hands the publish to a detached windowless `bm25.py refresh`."""
    out = os.fspath(out)
    if read_merge_frozen(out):
        return None, 0, 0
    fresh, gone = fresh_memories(mem_dir, out)
    if not fresh and not gone:
        return None, 0, 0
    if len(fresh) > REFRESH_FULL_REBUILD_AT:
        return None, 0, 0
    z = _load_payload(out)
    df, dl, tfs = dict(z["df"]), dict(z["dl"]), dict(z["tf"])

    def _drop(slug):
        old = tfs.pop(slug, None)
        dl.pop(slug, None)
        if old:
            for t in old:
                c = df.get(t, 0) - 1
                if c <= 0:
                    df.pop(t, None)
                else:
                    df[t] = c
    added = 0
    for slug, tks in zip(fresh, read_many(mem_dir, fresh)):
        _drop(slug)
        if not tks:
            continue
        tf = {}
        for t in tks:
            tf[t] = tf.get(t, 0) + 1
        tfs[slug] = tf
        dl[slug] = len(tks)
        for t in tf:
            df[t] = df.get(t, 0) + 1
        added += 1
    for slug in gone:
        _drop(slug)
    n = len(tfs)
    payload = {"n": n, "avgdl": (sum(dl.values()) / n) if n else 0.0, "df": df, "dl": dl, "tf": tfs}
    return payload, added, len(gone)


def scrub_in_memory(slugs, mem_dir=MEM, out=INDEX, payload=None):
    """Re-read the named memories on top of payload (or the published index) and return the merged payload; nothing is
    written. For a parent whose attachment bundle just failed verification: read_doc indexes its own text and nothing
    from the bundle, so this recall does not rank it on text the verifier rejected. The published index may still hold
    that text when the damage kept the bundle's mtime (the incremental refresh never sees it); the next full build
    re-reads every parent (28 Sep, account03/B02 r71, B02-R23-ATTACHMENT-STALE-BM25-DIGEST-BYPASS-001).
    Returns None when there is nothing to scrub or no index to scrub."""
    slugs = sorted(set(slugs or ()))
    if not slugs:
        return payload
    try:
        z = payload if payload is not None else _load_payload(os.fspath(out))
    except Exception:
        return payload
    df, dl, tfs = dict(z["df"]), dict(z["dl"]), dict(z["tf"])
    for slug in slugs:
        old = tfs.pop(slug, None)
        dl.pop(slug, None)
        for t in old or ():
            c = df.get(t, 0) - 1
            if c <= 0:
                df.pop(t, None)
            else:
                df[t] = c
        tks = read_doc(mem_dir, slug)
        if not tks:
            continue
        tf = {}
        for t in tks:
            tf[t] = tf.get(t, 0) + 1
        tfs[slug] = tf
        dl[slug] = len(tks)
        for t in tf:
            df[t] = df.get(t, 0) + 1
    n = len(tfs)
    return {"n": n, "avgdl": (sum(dl.values()) / n) if n else 0.0, "df": df, "dl": dl, "tf": tfs}


SPAWN_MARK_MAX_S = 600   # a detached refresh still running after ten minutes is stuck; the next caller may spawn again


def _is_that_refresh(pid, started_at):
    """True only for a live process that IS the refresh spawned at started_at, not a stranger handed the recycled
    pid. With psutil: started within a minute of the spawn, with `bm25` and `refresh` in its command line. Without
    psutil: liveness alone (the marker's ten-minute life bounds a recycled pid's damage to one skipped spawn, which
    the 15-minute task covers)."""
    if _pid_is_alive(pid) is not True:
        return False
    try:
        import psutil
    except ImportError:
        return True
    try:
        p = psutil.Process(pid)
        cmd = " ".join(p.cmdline()).lower()
        return abs(p.create_time() - started_at) <= 60 and "bm25" in cmd and "refresh" in cmd
    except Exception:
        return False


def _refresh_in_flight(index_path=None):
    """The pid of a refresh of this index running now, else None: the detached refresh spawned last (pid and start
    time in INDEX.spawned) while it is younger than SPAWN_MARK_MAX_S and still that process, or a live holder of the
    refresh lock under the lock's own age rule (the 15-minute task, remember()'s awaited child, a hand-run refresh).
    Anything unsure answers None, which means spawn - the behaviour before the guard."""
    index_path = os.fspath(index_path or INDEX)
    now = _time.time()
    try:
        with io.open(index_path + ".spawned", encoding="utf-8") as fh:
            pid_s, at_s = fh.read().split()[:2]
        pid, at = int(pid_s), float(at_s)
        if 0 <= now - at <= SPAWN_MARK_MAX_S and _is_that_refresh(pid, at):
            return pid
    except Exception:
        pass
    try:
        lock = index_path + ".refresh.lock"
        if now - os.stat(lock).st_mtime <= REFRESH_LOCK_STALE_S:
            with io.open(lock, encoding="utf-8") as fh:
                pid = int(fh.read().strip() or 0)
            if pid and pid != os.getpid() and _pid_is_alive(pid) is True:
                return pid
    except Exception:
        pass
    return None


def _mark_spawned(pid, index_path=None):
    """Record the refresh just spawned for _refresh_in_flight. Best-effort: with no marker the next caller spawns,
    as before."""
    index_path = os.fspath(index_path or INDEX)
    tmp = "%s.spawned.tmp.%d" % (index_path, os.getpid())
    try:
        with io.open(tmp, "w", encoding="utf-8") as fh:
            fh.write("%d %.3f\n" % (pid, _time.time()))
        os.replace(tmp, index_path + ".spawned")
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass


def spawn_detached_refresh(log_path=None):
    """Run `bm25.py refresh` in a separate windowless process and return at once. The caller keeps
    serving from its in-memory merge; the published generation catches up seconds later. Never
    flashes a console (CREATE_NO_WINDOW, DETACHED_PROCESS). Best-effort: returns the pid or None.

    SINGLE FLIGHT (13 Oct 2026). recall() calls this whenever a memory is newer than the index, so a caller
    that recalls in a loop spawned one refresh per call: 34 were alive at 05:24 that morning, children of one
    diagnostic script, about 11 GB between them on a box with 6.8 GB free. All but one of them loaded the
    payload, found the lock held (bm25_refresh.log: dozens of BLOCKED lines naming one holder) and exited.
    Now a refresh already running for this index - the one spawned last, or the lock's live holder - is
    returned instead of a new child. Nothing is lost: recall merges fresh memories in memory either way, and
    the first call after that refresh ends spawns again if anything is still unindexed."""
    import subprocess
    if publishes_live_from_elsewhere():               # the child inherits the seam and would publish the live index
        return None
    running = _refresh_in_flight()
    if running:
        return running
    log_path = log_path or os.path.join(os.path.dirname(os.path.abspath(__file__)), "bm25_refresh.log")
    try:
        flags = (getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
                 | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        logf = io.open(log_path, "a", encoding="utf-8")
        p = subprocess.Popen([sys.executable, "-B", os.path.abspath(__file__), "refresh"],
                             stdin=subprocess.DEVNULL, stdout=logf, stderr=subprocess.STDOUT,
                             creationflags=flags, close_fds=True)
        _mark_spawned(p.pid)
        return p.pid
    except Exception:
        return None


# ---------------------------------------------------------------------------------------------
# TERM-MAJOR POSTINGS SIDECAR (2026-09-14). The payload above is DOCUMENT-major: tf[slug][term].
# Scoring one query therefore costs a full load of every document's term map - 34.9 MB of pickle -
# plus a sweep over all ~16,000 documents, and a hook is a fresh process that pays it every time.
# This sidecar stores the same numbers TERM-major in sqlite, so a query reads only the rows for its
# own handful of terms, and keeps the whole term->df map as one 2.5 MB compressed row for the spell
# normaliser. Identical ranking, proven query by query (see the exactness note on score_postings).
#
# MEASURED on a QUIET box, ABBA-interleaved so run order cannot masquerade as a speedup, under a
# frozen index generation, with the sidecar's engagement verified before and after each battery:
#     component (fresh process: import + BM25() + df normalise + one score)   0.442 s -> 0.091 s
#     spread_recall end to end, the heaviest prompt-time hook, 20 runs a side  1.244 s -> 0.816 s
# so about 0.43 s off EVERY prompt of EVERY agent, and the hook's output was byte-identical on all
# 12 prompts compared. Earlier figures from this work (1.563 s a query, 7.66 s for spread_recall)
# were taken while the box carried six video encodes: they are load readings, not path readings, and
# the honest quiet-box baseline is the 1.244 s above.
#
# It is a CACHE, never a source of truth: it carries the index generation it was built from, refuses
# to answer for any other, and every caller falls back to the payload path when it is absent, stale
# or unreadable - measured, not assumed (a sidecar with a corrupted signature ran at the full payload
# time and returned the same notes). BRAIN_BM25_POSTINGS=0 turns it off.
def spawn_detached_postings(log_path=None):
    """Run `bm25.py postings` in a separate windowless process and return at once - the sidecar catches up
    to the generation this process just published without the publisher paying the 15 s. Never flashes a
    console. Best-effort: returns the pid or None, and the sidecar simply stays a generation behind.

    It gets its OWN log file. The publisher is still writing to the refresh log when this child starts,
    and two processes appending to one file tear each other's lines: within minutes of this being wired
    the refresh log carried spliced entries ("... -> 16026 2026-09-14T06:54:21Z bm25 postings: ..."),
    which is how a log stops being usable as evidence."""
    import subprocess
    log_path = log_path or os.path.join(os.path.dirname(os.path.abspath(__file__)), "bm25_postings.log")
    try:
        flags = (getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
                 | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        logf = io.open(log_path, "a", encoding="utf-8")
        p = subprocess.Popen([sys.executable, "-B", os.path.abspath(__file__), "postings"],
                             stdin=subprocess.DEVNULL, stdout=logf, stderr=subprocess.STDOUT,
                             creationflags=flags, close_fds=True)
        # RAISE IT TO NORMAL (2026-09-14, measured). A detached child inherits the caller's priority class, and
        # the callers here are hooks and schedulers that run BelowNormal - which on this box is starvation, not
        # politeness. Found two of these builds alive at once, one for 256 minutes on 24 SECONDS of CPU and the
        # other 189 minutes on 14 s, while the same build takes 13 s when it gets a scheduler share. A rebuild
        # that never finishes is a sidecar permanently a generation behind, which is the whole feature dark.
        # Set on the handle rather than as a creation flag: NORMAL_PRIORITY_CLASS in creationflags does NOT
        # raise a child of a BelowNormal parent here (tested both ways, same class out).
        try:
            import ctypes
            h = ctypes.windll.kernel32.OpenProcess(0x0200, False, int(p.pid))   # PROCESS_SET_INFORMATION
            if h:
                ctypes.windll.kernel32.SetPriorityClass(h, 0x00000020)          # NORMAL_PRIORITY_CLASS
                _raise_io_mem_priority(h)
                ctypes.windll.kernel32.CloseHandle(h)
        except Exception:
            pass                                                                # best effort; it still builds
        return p.pid
    except Exception:
        return None


def _raise_io_mem_priority(h=None):
    """Set I/O priority to normal (2) and memory priority to normal (5) on a process handle, or on this process.
    Best effort, Windows only, returns (io_status, mem_ok) or None.

    WHY (2026-10-30, measured): the CPU class above is not the whole inheritance. Live builders spawned from the
    hooks read I/O priority 1 (low) and memory priority 2 (low) while their class was Normal, and their builds
    logged 1137-6661 s against about 20 s; one lived 7.4 h for a generation that was already dead. Same build on
    a fixed copy, alternated low/normal three times each: 68.4/30.3/38.6 s at low against 17.2/21.2/22.3 s at
    normal, identical row digests. Under memory pressure a low-memory-priority process loses its pages first and
    rereads them. Normal needs no privilege (only High does)."""
    try:
        import ctypes
        k, nt = ctypes.windll.kernel32, ctypes.windll.ntdll
        if h is None:
            h = k.GetCurrentProcess()
        hv = ctypes.c_void_p(h)
        st = nt.NtSetInformationProcess(hv, 33, ctypes.byref(ctypes.c_ulong(2)), 4)        # ProcessIoPriority
        ok = k.SetProcessInformation(hv, 0, ctypes.byref(ctypes.c_ulong(5)), 4)            # ProcessMemoryPriority
        return (st & 0xFFFFFFFF, bool(ok))
    except Exception:
        return None


POSTINGS_SCHEMA = "bm25-postings/v1"
_POSTINGS_ON = os.environ.get("BRAIN_BM25_POSTINGS", "1") == "1"


# I1 (2026-10-13): a new generation's sidecar is built from the newest older one - copied, and only the postings of the
# documents the publish changed rewritten. Measured 12-13 Oct (improve_1012/fronts/R16/STATUS.md): 350 publishes, median
# gap 103 s; a full build took 36-50 s of wall time on the loaded laptop (13 s of CPU), 101 of the 350 generations were
# replaced before theirs existed, and the live generation had a sidecar 63% of the time - every reader without one
# loads the 45 MB payload. BRAIN_BM25_POSTINGS_INCR=0 turns it off (full builds only).
_POSTINGS_INCR_ON = os.environ.get("BRAIN_BM25_POSTINGS_INCR", "1") == "1"
POSTINGS_INCR_MAX = 2000          # more documents to place or drop than this and the full build is the cheaper one
POSTINGS_INCR_CHAIN = 100         # a full build after this many incremental ones in a row: it compacts the file and
                                  # bounds how long a fault no check saw could be carried forward
POSTINGS_LAST = {}                # what the last build_postings of this process did: mode, base, counts, why
# P1 (30 Oct 2026, Mac): the publisher builds the new generation's sidecar from the old one's BEFORE the publish and puts
# it in place under the publication lock, so no reader meets a generation without one. Until then a detached child built
# it after the publish: 51 builds in 2 h, median 5.9 s, and a recall in that gap unpickled the whole payload (1.8 s,
# about 0.1 s on the average recall). Only the detached `bm25.py refresh` sets this (nobody waits on it); remember()'s
# awaited refresh and the recall path leave it off. BRAIN_BM25_PREBUILD=0 turns it off there too.
_POSTINGS_PREBUILD = False
PREBUILT_LAST = {}                # what the last publish of this process did with a prebuilt sidecar


def _postings_tag(signature):
    """A short stable name for one index generation."""
    return hashlib.sha1(str(signature).encode("utf-8")).hexdigest()[:12]


def postings_path(index_path=INDEX, signature=None):
    """The sidecar file for ONE generation. The generation is in the NAME, not only in the header.

    WHY (measured 2026-09-14, Windows): a single file replaced in place cannot be rebuilt while any
    reader holds it. `os.replace` over an open sqlite raises PermissionError WinError 5 - and on this
    machine a hook opens this file on every prompt of every agent, so the busy hours are exactly the
    hours the rebuild would fail. The sidecar would freeze at one generation, every hook would fall
    back to the payload path, and the dial would still read "on" - the same silent-fallback failure
    that already fooled one A/B today. Naming each generation's file separately means a rebuild never
    has to overwrite a file somebody is reading: it writes a NEW name and the old one is swept once
    its readers let go."""
    index_path = os.fspath(index_path)
    if signature is None:
        signature = _index_signature(index_path)
    if signature is None:
        return index_path + ".postings.sqlite3"
    return "%s.postings.%s.sqlite3" % (index_path, _postings_tag(signature))


def _doc_fp(tf, dl):
    """Eight bytes that change when one document's postings would: its words in tf order, their counts and its length.
    Hashed from plain bytes, not marshal or pickle, whose output can depend on object identity."""
    import array
    import struct
    h = hashlib.blake2b(digest_size=8)
    h.update(struct.pack("<qq", len(tf), int(dl)))
    h.update("\x00".join(tf).encode("utf-8", "surrogatepass"))
    h.update(array.array("q", tf.values()).tobytes())
    return h.digest()


def _fwd_blob(tf):
    """One document's words, compressed: what an incremental build needs to take it out of the postings it is in."""
    import struct
    import zlib
    s = "\x00".join(tf)
    if s.count("\x00") != max(len(tf) - 1, 0):
        raise ValueError("a word with a NUL in it cannot go in the forward table")
    return zlib.compress(struct.pack("<I", len(tf)) + s.encode("utf-8", "surrogatepass"), 1)


def _fwd_terms(blob):
    """The words _fwd_blob stored, in the same order."""
    import struct
    import zlib
    raw = zlib.decompress(blob)
    k = struct.unpack_from("<I", raw, 0)[0]
    terms = raw[4:].decode("utf-8", "surrogatepass").split("\x00") if k else []
    if len(terms) != k:
        raise ValueError("a forward row holds %d words and says %d" % (len(terms), k))
    return terms


def _rec_find(blob, pos):
    """Index of document `pos` in one word's postings (12-byte records in increasing position order), or -1."""
    import struct
    lo, hi = 0, len(blob) // 12
    while lo < hi:
        mid = (lo + hi) // 2
        p = struct.unpack_from("<I", blob, mid * 12)[0]
        if p < pos:
            lo = mid + 1
        elif p > pos:
            hi = mid
        else:
            return mid
    return -1


def _postings_base(index_path, live):
    """The newest sidecar on disk that is not `live` (a path or a file name): what an incremental build starts from."""
    import glob
    best = None
    for f in glob.glob(os.fspath(index_path) + ".postings.*.sqlite3"):
        if os.path.basename(f) == os.path.basename(live):
            continue
        try:
            m = os.stat(f).st_mtime
        except OSError:
            continue
        if best is None or m > best[0]:
            best = (m, f)
    return best[1] if best else None


def _postings_note():
    """How the last build ran, for the end of its log line."""
    L = POSTINGS_LAST
    if L.get("mode") == "incremental":
        tag = L["base"].rsplit(".sqlite3", 1)[0].rsplit(".", 1)[-1]
        return "[incremental from %s: %d kept, %d dropped, %d placed, chain %d]" % (
            tag, L["kept"], L["dropped"], L["placed"], L["chain"])
    if L.get("why"):
        return "[full; incremental refused: %s]" % L["why"]
    return "[full]"


class PostingsSuperseded(Exception):
    """A newer generation's sidecar is already on disk, so the one being built can never be read."""


def _postings_superseded(index_path, signature):
    """True when the live generation is not `signature` AND its sidecar already exists.

    WHY (30 Oct 2026): a builder started for one generation kept going after the index had moved on. Pid 28340 built
    generation fb2ac05015df for 11 h (240 cpu-s at the old low I/O and memory priority) while ten newer sidecars were
    built and published beside it. Nothing reads a dead generation's sidecar, and because _postings_base picks the
    newest file by mtime, a dead build that finishes late becomes the base the next build copies.
    Stopping merely because the index moved would livelock a busy hour (a publish every minute, builds of 20-100 s);
    stopping only once the live generation's sidecar exists cannot, because something newer has already finished.
    Two stats; cheap enough to call every few megabytes."""
    live = _index_signature(index_path)
    if live is None or live == signature:
        return False
    return os.path.exists(postings_path(index_path, live))


def _copy_unless_superseded(base, tmp, index_path, signature, chunk=8 << 20):
    """shutil.copyfile, checking every `chunk` bytes whether the build is still worth finishing. Leaves no partial
    temp behind when it stops (the incremental build's own cleanup starts only after the copy)."""
    try:
        with io.open(base, "rb") as r, io.open(tmp, "wb") as w:
            while True:
                b = r.read(chunk)
                if not b:
                    break
                w.write(b)
                if _postings_superseded(index_path, signature):
                    raise PostingsSuperseded("superseded while copying the base")
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _build_postings_incremental(signature, out, z, base, index_path=INDEX):
    """Generation `signature`'s sidecar (payload `z`) from `base`, an older generation's sidecar: copy it, take out the
    documents that are gone or changed, put the new and changed ones in, rewrite only the postings they touch. Returns
    (terms, docs, counts), or raises and the caller does the full build.

    POSITIONS ARE ORDERED, NOT DENSE. The payload's tf order is part of what a sidecar reproduces: score() breaks ties by
    it. refresh() moves every document it re-reads to the end of that order and a gone document leaves a hole, so a
    0..n-1 position would change for every later document at every publish. Here the documents at the front of the new
    order that sit in the base in the same order and unchanged (same fingerprint) keep their positions; from the first
    one that does not, the rest of the new order goes past the base's largest position. Readers rank and break ties by
    position and look a slug up by its position (score_postings, and brain.py's overlay since I1), so an ordered set
    of positions serves them exactly as 0..n-1 does.

    Checked before the file is published: the documents are in payload order, every word's df is the payload's, each
    table has one row per document, and a few queries made of the touched words score here exactly as over the payload."""
    import shutil
    import sqlite3
    import struct
    import zlib
    slugs = list(z["tf"])
    tfm, dl, df = z["tf"], z["dl"], z["df"]
    tmp = "%s.tmp.%d" % (out, os.getpid())
    for junk in (tmp, tmp + "-journal"):
        if os.path.exists(junk):
            os.remove(junk)
    if _postings_superseded(index_path, signature):
        raise PostingsSuperseded("superseded before the copy")
    _copy_unless_superseded(base, tmp, index_path, signature)
    con = sqlite3.connect(tmp)
    try:
        con.execute("pragma journal_mode=off")
        con.execute("pragma synchronous=off")
        tables = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
        if not {"p", "d", "hdr", "vocab", "fp", "fwd"} <= tables:
            raise LookupError("the base has no forward tables")
        h = dict(con.execute("select k,v from hdr"))
        if h.get("schema") != POSTINGS_SCHEMA:
            raise LookupError("the base is schema %r" % h.get("schema"))
        chain = int(h.get("chain") or 0) + 1
        if chain > POSTINGS_INCR_CHAIN:
            raise LookupError("%d incremental builds since the last full one" % (chain - 1))
        rows = con.execute("select pos, slug from d order by pos").fetchall()
        bpos = {s: p for p, s in rows}
        bfp = dict(con.execute("select pos, h from fp"))
        nfwd = con.execute("select count(*) from fwd").fetchone()[0]
        if not rows or len(bpos) != len(rows) or len(bfp) != len(rows) or nfwd != len(rows):
            raise LookupError("the base's tables disagree")
        top = rows[-1][0]
        kept, last = 0, -1
        for s in slugs:
            p = bpos.get(s)
            if p is None or p <= last or bfp.get(p) != _doc_fp(tfm[s], dl[s]):
                break
            last = p
            kept += 1
        keep = {bpos[s] for s in slugs[:kept]}
        dropped = [p for p, _s in rows if p not in keep]
        tail = slugs[kept:]
        if len(tail) > POSTINGS_INCR_MAX or len(dropped) > POSTINGS_INCR_MAX:
            raise LookupError("%d documents to place and %d to drop" % (len(tail), len(dropped)))
        if top + len(tail) >= 2 ** 32:
            raise LookupError("positions would pass 2**32")
        rem = {}                                   # word -> the dropped positions that hold it
        for p in dropped:
            for t in _fwd_terms(con.execute("select terms from fwd where pos=?", (p,)).fetchone()[0]):
                rem.setdefault(t, []).append(p)
        npos = range(top + 1, top + 1 + len(tail))
        add = {}                                   # word -> its new records, in position order
        for q, s in zip(npos, tail):
            d = int(dl[s])
            for t, f in tfm[s].items():
                add.setdefault(t, []).append(struct.pack("<IiI", q, int(f), d))
        for i_t, t in enumerate(set(rem) | set(add)):
            if i_t % 2000 == 1999 and _postings_superseded(index_path, signature):
                raise PostingsSuperseded("superseded while rewriting postings")
            row = con.execute("select blob from p where term=?", (t,)).fetchone()
            b = zlib.decompress(row[0]) if row else b""
            if len(b) % 12:
                raise ValueError("a word's postings are not whole records")
            cut = sorted(_rec_find(b, p) for p in rem.get(t, ()))
            if cut:
                if cut[0] < 0 or len(set(cut)) != len(cut):
                    raise LookupError("a dropped document is missing from the postings of a word it held")
                parts, at = [], 0
                for i in cut:
                    parts.append(b[at:i * 12])
                    at = i * 12 + 12
                parts.append(b[at:])
                b = b"".join(parts)
            if t in add:
                b += b"".join(add[t])
            if b:
                con.execute("insert or replace into p values (?,?,?)", (t, len(b) // 12, zlib.compress(b, 1)))
            else:
                con.execute("delete from p where term=?", (t,))
        for table in ("d", "fp", "fwd"):
            con.executemany("delete from %s where pos=?" % table, ((p,) for p in dropped))
        con.executemany("insert into d values (?,?)", zip(npos, tail))
        con.executemany("insert into fp values (?,?)", ((q, _doc_fp(tfm[s], dl[s])) for q, s in zip(npos, tail)))
        con.executemany("insert into fwd values (?,?)", ((q, _fwd_blob(tfm[s])) for q, s in zip(npos, tail)))
        con.executemany("insert or replace into hdr values (?,?)", (
            ("signature", str(signature)), ("n", repr(z["n"])), ("avgdl", repr(z["avgdl"])),
            ("built_at", _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime())),
            ("chain", str(chain)), ("built_from", os.path.basename(base))))
        con.execute("insert or replace into vocab values (?,?)",
                    ("df", zlib.compress(pickle.dumps(df, protocol=5), 1)))
        if [s for (s,) in con.execute("select slug from d order by pos")] != slugs:
            raise ValueError("the documents are not in payload order")
        terms = 0
        for t, v in con.execute("select term, df from p"):
            if df.get(t) != v:
                raise ValueError("a word's df differs from the payload's")
            terms += 1
        if terms != sum(1 for v in df.values() if v > 0):
            raise ValueError("%d words here, %d in the payload" % (terms, len(df)))
        for table in ("fp", "fwd"):
            if con.execute("select count(*) from %s" % table).fetchone()[0] != len(slugs):
                raise ValueError("the %s table does not have one row per document" % table)
        qs = []
        for s in dict.fromkeys(tail[:1] + tail[-1:]):
            ws = sorted(tfm[s], key=lambda t: (df.get(t, 0), t))
            qs.append(" ".join(ws[:2] + ws[-1:]))
        if rem:
            qs.append(" ".join(sorted(rem, key=lambda t: (df.get(t, 0), t))[:3]))
        ref = BM25.from_payload(z)
        for q in qs:
            if score_postings(q, 50, conn=(con, float(z["n"]), float(z["avgdl"]))) != ref.score(q, 50):
                raise ValueError("a query of the touched words scores differently here and over the payload")
        con.commit()
    except BaseException:
        try:
            con.close()
        except Exception:
            pass
        for junk in (tmp, tmp + "-journal"):
            try:
                os.remove(junk)
            except OSError:
                pass
        raise
    finally:
        try:
            con.close()
        except Exception:
            pass
    if _postings_superseded(index_path, signature):
        for junk in (tmp, tmp + "-journal"):
            try:
                os.remove(junk)
            except OSError:
                pass
        raise PostingsSuperseded("superseded before publishing")
    os.replace(tmp, out)
    return terms, len(slugs), {"kept": kept, "dropped": len(dropped), "placed": len(tail), "chain": chain}


def _prebuild_postings(payload, out, expected_signature):
    """P1: the sidecar for the generation about to be published, built incrementally from `expected_signature`'s own
    sidecar into a temp, or None (off, no base, or the incremental build refused; the detached build then runs as before).
    Its header still names the base's generation; _place_prebuilt renames it once the publish has its signature. The
    staged payload is what the published json parses back to (the early pickle relies on the same), so the sidecar is the
    one a build after the publish would write; the incremental build's own query checks hold it to that."""
    PREBUILT_LAST.clear()
    if (not _POSTINGS_PREBUILD or not _POSTINGS_INCR_ON or expected_signature is None
            or os.environ.get("BRAIN_BM25_PREBUILD", "1") == "0"):
        return None
    base = postings_path(out, expected_signature)
    if not os.path.exists(base):
        PREBUILT_LAST.update(why="the old generation has no sidecar")
        return None
    pre = "%s.postings.pre.tmp.%d" % (os.fspath(out), os.getpid())
    t0 = _time.time()
    try:
        terms, docs, info = _build_postings_incremental(expected_signature, pre, payload, base, index_path=out)
    except Exception as e:
        for junk in (pre, "%s.tmp.%d" % (pre, os.getpid())):
            try:
                os.remove(junk)
            except OSError:
                pass
        PREBUILT_LAST.update(why=("%s: %s" % (type(e).__name__, e))[:200])
        return None
    PREBUILT_LAST.update(info, terms=terms, docs=docs, base=os.path.basename(base), seconds=_time.time() - t0)
    return pre


def _place_prebuilt(pre, out, signature):
    """Under the publication lock, right after the replace: stamp the prebuilt sidecar with the published signature and
    move it to that generation's name. Best effort; on any failure the detached build makes it as before."""
    import sqlite3
    try:
        con = sqlite3.connect(pre)
        try:
            con.execute("insert or replace into hdr values (?,?)", ("signature", str(signature)))
            con.commit()
        finally:
            con.close()
        final = postings_path(out, signature)
        if os.path.exists(final):
            return False
        os.replace(pre, final)
        PREBUILT_LAST.update(sidecar=os.path.basename(final))
        return True
    except Exception as e:
        PREBUILT_LAST.update(why=("placing: %s: %s" % (type(e).__name__, e))[:200])
        return False


def build_postings(index_path=INDEX, out=None, base=None):
    """Write the term-major sidecar for the CURRENT generation of `index_path`. Returns (terms, docs).

    With `base`, the sidecar of an older generation, it first tries _build_postings_incremental and does the full build
    only if that refuses (I1). POSTINGS_LAST says which ran, from what, and why."""
    import sqlite3
    import struct
    import zlib
    index_path = os.fspath(index_path)
    signature = _index_signature(index_path)
    if signature is None:
        raise OSError("no index at %s" % index_path)
    out = out or postings_path(index_path, signature)
    z = _load_payload(index_path)
    if _index_signature(index_path) != signature:
        # A sidecar's name says which generation it answers for, and every reader trusts the name. A publish that
        # landed while the payload was read would put the next generation's postings under this one's name.
        raise OSError("the index was republished while its payload was read")
    POSTINGS_LAST.clear()
    POSTINGS_LAST.update(mode="full", base=None, why="")
    if base is not None and os.path.basename(base) == os.path.basename(out):
        base = None
    if base is not None and _POSTINGS_INCR_ON:
        try:
            terms, docs, info = _build_postings_incremental(signature, out, z, base, index_path=index_path)
            POSTINGS_LAST.update(info, mode="incremental", base=os.path.basename(base))
            return terms, docs
        except PostingsSuperseded:
            raise
        except Exception as e:
            POSTINGS_LAST["why"] = ("%s: %s" % (type(e).__name__, e))[:200]
    slugs = list(z["tf"])                      # payload order: the tie-break order score() inherits
    dl = z["dl"]
    inv = {}
    for pos, slug in enumerate(slugs):
        d = int(dl[slug])
        for term, f in z["tf"][slug].items():
            inv.setdefault(term, []).append((pos, int(f), d))
    try:
        fwd = [(pos, _doc_fp(z["tf"][s], dl[s]), _fwd_blob(z["tf"][s])) for pos, s in enumerate(slugs)]
    except (ValueError, TypeError, OverflowError):
        fwd = None                             # it answers queries; it just cannot be the base of the next build
    if _postings_superseded(index_path, signature):
        raise PostingsSuperseded("superseded before the full build")

    def _rows():
        for i_t, (t, lst) in enumerate(inv.items()):
            if i_t % 20000 == 19999 and _postings_superseded(index_path, signature):
                raise PostingsSuperseded("superseded during the full build")
            yield t, len(lst), zlib.compress(b"".join(struct.pack("<IiI", p, f, d) for p, f, d in lst), 1)

    tmp = "%s.tmp.%d" % (out, os.getpid())
    if os.path.exists(tmp):
        os.remove(tmp)
    con = sqlite3.connect(tmp)
    try:
        con.execute("pragma journal_mode=off")
        con.execute("pragma synchronous=off")
        con.execute("create table p(term text primary key, df int, blob blob) without rowid")
        con.executemany("insert into p values (?,?,?)", _rows())
        con.execute("create table d(pos integer primary key, slug text)")
        con.executemany("insert into d values (?,?)", enumerate(slugs))
        if fwd is not None:
            # I1: per document, a fingerprint of its postings and its words, so that the next generation's sidecar can
            # be built from this one. No reader opens these two tables.
            con.execute("create table fp(pos integer primary key, h blob)")
            con.executemany("insert into fp values (?,?)", ((p, h) for p, h, _b in fwd))
            con.execute("create table fwd(pos integer primary key, terms blob)")
            con.executemany("insert into fwd values (?,?)", ((p, b) for p, _h, b in fwd))
        con.execute("create table hdr(k text primary key, v text)")
        con.executemany("insert into hdr values (?,?)", (
            ("schema", POSTINGS_SCHEMA), ("signature", str(signature)),
            ("n", repr(z["n"])), ("avgdl", repr(z["avgdl"])),
            ("built_at", _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime())), ("chain", "0")))
        # The whole term->df map as ONE compressed pickle row. Both hot callers (spread_recall and
        # deliberate recall) hand `.df` to QueryNormalizer for spell-normalisation, so a searcher that
        # could only score would still have to load the 35 MB payload for the vocabulary. Measured
        # 2026-09-14: 2.5 MB, decodes in 0.058 s against 0.66 s for the payload.
        con.execute("create table vocab(k text primary key, blob blob)")
        con.execute("insert into vocab values (?,?)",
                    ("df", zlib.compress(pickle.dumps(z["df"], protocol=5), 1)))
        con.commit()
    except BaseException:
        # A half-written 57 MB temp on a disk at 2 pct free is how the dreaming directory collected 60
        # orphaned bm25_index temps (the_bm25_index_leaves_orphaned_temp_files_2026_09_13). The sidecar
        # never leaves one: the only file that survives a failure is the previous good generation.
        try:
            con.close()
        except Exception:
            pass
        for junk in (tmp, tmp + "-journal"):
            try:
                os.remove(junk)
            except OSError:
                pass
        raise
    finally:
        try:
            con.close()
        except Exception:
            pass
    if _postings_superseded(index_path, signature):
        for junk in (tmp, tmp + "-journal"):
            try:
                os.remove(junk)
            except OSError:
                pass
        raise PostingsSuperseded("superseded before publishing the full build")
    os.replace(tmp, out)
    return len(inv), len(slugs)


def _postings_conn(index_path=INDEX):
    """An open read-only connection plus (n, avgdl) IF the sidecar matches the live generation, else None.
    Fail-closed on any mismatch and fail-open to the payload path on any error - a cache that answers for
    the wrong generation is worse than no cache."""
    import sqlite3
    index_path = os.fspath(index_path)
    try:
        signature = _index_signature(index_path)
        if signature is None:
            return None
        p = postings_path(index_path, signature)
        if not os.path.exists(p):
            return None
        con = sqlite3.connect("file:%s?mode=ro" % p.replace("?", "%3f"), uri=True)
        h = dict(con.execute("select k,v from hdr"))
        if h.get("schema") != POSTINGS_SCHEMA or h.get("signature") != str(signature):
            con.close()
            return None
        return con, float(h["n"]), float(h["avgdl"])
    except Exception:
        return None


# The per-posting loop in score_postings was the largest single cost of a membrane turn: about 210,000
# struct.unpack_from calls for one ~110-word order query (R32, 14 Oct 2026). The fast path does the same
# arithmetic in C - array views of the blob and map() over the operator functions, in the same expression
# tree and the same summation order - so every score is the same double. BRAIN_BM25_FASTLOOP=0 restores the
# plain loop.
_FASTLOOP = os.environ.get("BRAIN_BM25_FASTLOOP", "1") == "1"
_HEAPTOP = os.environ.get("BRAIN_BM25_HEAPTOP", "1") == "1"


class _LengthNorm(dict):
    """K1 * (1 - B + B * d / avgdl) for a document length d: the loop's own expression, computed once per
    length per query instead of once per posting. Same operands, same order, so the same double."""
    __slots__ = ("avgdl",)

    def __init__(self, avgdl):
        dict.__init__(self)
        self.avgdl = avgdl

    def __missing__(self, d):
        v = self[d] = K1 * (1 - B + B * d / self.avgdl)
        return v


def score_postings(query, topk=20, index_path=INDEX, conn=None):
    """BM25 over the term-major sidecar, or None when it cannot answer (caller falls back).

    EXACTNESS. Same formula, same constants, and deliberately the same two orderings that make a float
    result reproducible: terms are summed per document in `sorted(set(q))` order exactly as score() does,
    and ties are broken by the document's position in the payload - which is what score()'s stable sort
    over a dict built in tf order already does. Verified bit-for-bit against score() on a large query
    battery before this was ever enabled."""
    import struct
    import zlib
    own = conn is None
    got = conn if conn is not None else _postings_conn(index_path)
    if got is None:
        return None
    con, n, avgdl = got
    try:
        scores = {}
        fast = False
        if _FASTLOOP:
            from array import array
            from itertools import repeat
            from operator import add, mul, truediv
            fast = sys.byteorder == "little" and array("I").itemsize == 4 and array("i").itemsize == 4
            get, norm, k1p1 = scores.get, _LengthNorm(avgdl).__getitem__, K1 + 1
        for t in sorted(set(tokens(query))):
            row = con.execute("select df,blob from p where term=?", (t,)).fetchone()
            if not row:
                continue
            df_t, blob = row
            idf = math.log(1.0 + (n - df_t + 0.5) / (df_t + 0.5)) * QUERY_TERM_WEIGHTS.get(t, 1.0)
            b = zlib.decompress(blob)
            if fast:
                if len(b) % 12:
                    return None     # the plain loop raises struct.error on a torn tail: the same None
                w = array("I")
                w.frombytes(b)
                fs = array("i")
                fs.frombytes(b)
                fs, ps = fs[1::3], w[0::3]
                # scores[pos] = scores.get(pos, 0.0) + ((idf * f) * (K1 + 1)) / (f + norm(d)), posting by posting:
                # update() pulls one pair at a time, so each get() sees every earlier posting's write
                vs = map(truediv, map(mul, map(mul, repeat(idf), fs), repeat(k1p1)), map(add, fs, map(norm, w[2::3])))
                scores.update(zip(ps, map(add, map(get, ps, repeat(0.0)), vs)))
                continue
            for i in range(0, len(b), 12):
                pos, f, d = struct.unpack_from("<IiI", b, i)
                scores[pos] = scores.get(pos, 0.0) + idf * f * (K1 + 1) / (f + K1 * (1 - B + B * d / avgdl))
        if not scores:
            return []
        # heapq.nsmallest is documented equal to sorted(...)[:topk]; the key is unique (pos), so no tie can reorder.
        # A full sort of ~18,000 candidates cost about half of a seed-arm score call (28 of 53 ms median; the heap
        # 4.9 ms - claude-738954b6, 4 Nov 2026). BRAIN_BM25_HEAPTOP=0 restores the full sort.
        cand = ((p, s) for p, s in scores.items() if s > 0)
        if _HEAPTOP:
            import heapq
            top = heapq.nsmallest(topk, cand, key=lambda kv: (-kv[1], kv[0]))
        else:
            top = sorted(cand, key=lambda kv: (-kv[1], kv[0]))[:topk]
        out = []
        for pos, s in top:
            r = con.execute("select slug from d where pos=?", (pos,)).fetchone()
            if r:
                out.append((r[0], s))
        return out
    except Exception:
        return None
    finally:
        if own:
            try:
                con.close()
            except Exception:
                pass


# MEASURED 2026-09-14 from bm25_refresh.log: the index publishes a NEW generation 51 times a day, median
# gap 19 min, p25 12 min. The sidecar is bound to one generation, so a rate limit longer than that gap
# leaves the cache dark most of the day - a ten-minute limit was tried first and did exactly that (a peer
# published 90 s after a build and the rebuild was refused, so the dial read "on" while every hook silently
# fell back). 90 s only stops a thundering herd of concurrent detached refreshes; at 13 s and 57 MB a
# rebuild costs about 11 CPU-minutes and 2.9 GB of rewritten (not grown) bytes a day.
POSTINGS_MIN_REBUILD_S = 90       # per GENERATION: do not retry a generation this machine just failed on
POSTINGS_HERD_S = 20              # per CLOCK: only wide enough to stop two concurrent refreshes both building
POSTINGS_LOCK_STALE_S = 600       # a build lock older than this is taken over if its holder is not running
POSTINGS_LOCK_DEAD_S = 3 * 3600   # ... and taken over regardless past this (a recycled pid, or a build stuck for good)
POSTINGS_CATCHUP_ROUNDS = 3       # builds one holder makes for generations published while it was building
POSTINGS_TEMP_STALE_S = 1800      # a .tmp.<pid> nobody replaced within half an hour is an orphan
POSTINGS_RETIRE_S = 120           # a superseded generation's sidecar, once its readers have let go


def _sweep_postings_temps(p, index_path=INDEX, older_than=POSTINGS_TEMP_STALE_S):
    """Retire what the sidecar leaves behind: the `.tmp.<pid>` of an interrupted build, and the whole
    sidecar of every generation that is no longer the live one.

    Both halves are needed. Fixing build_postings' exception path is not enough on its own, because a
    killed process (a reboot, a usage limit, a taskkill) never runs it - one abandoned 53 MB temp per
    attempt is how the dreaming directory reached 60 of them on 2026-09-13. And now that each generation
    gets its own file, nothing would ever delete the old ones: at 57 MB and 51 generations a day that is
    2.9 GB a day on a disk with 44 GB free. A delete that fails because a reader still holds the file is
    not an error - it is next sweep's work."""
    import glob
    now = _time.time()
    keep = os.path.basename(postings_path(index_path))
    stem = os.fspath(index_path) + ".postings."
    # Glob the STEM, not this generation's name: an interrupted build leaves its temp under the name of
    # the generation it was building, which by the time anyone sweeps is usually not the live one.
    for junk in glob.glob(stem + "*.tmp.*"):
        try:
            if now - os.stat(junk).st_mtime > older_than:
                os.remove(junk)
        except OSError:
            pass
    # I1: the newest sidecar of another generation is what the next incremental build copies. It stays, whatever its
    # age, until a newer one takes its place.
    spare = _postings_base(index_path, keep) if _POSTINGS_INCR_ON else None
    spare = os.path.basename(spare) if spare else None
    for old in glob.glob(stem + "*.sqlite3") + glob.glob(stem + "*.sqlite3.attempt"):
        if os.path.basename(old) in (keep, keep + ".attempt", spare):
            continue
        try:
            if now - os.stat(old).st_mtime > POSTINGS_RETIRE_S:
                os.remove(old)
        except OSError:
            pass          # a hook still has it open; it will be gone next sweep


def _lock_holder_alive(lock):
    """True while the pid written in `lock` is a running process and the lock is younger than POSTINGS_LOCK_DEAD_S.
    On Windows there is no cheap signal-0 probe here, so it answers False and age alone decides, as before."""
    if os.name == "nt":
        return False
    try:
        if _time.time() - os.stat(lock).st_mtime > POSTINGS_LOCK_DEAD_S:
            return False
        with io.open(lock, encoding="utf-8") as fh:
            pid = int(fh.read().strip() or 0)
    except (OSError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True               # EPERM: alive, just not ours
    return True


def maybe_build_postings(index_path=INDEX, force=False, quiet=True):
    """Rebuild the sidecar IF it is absent or built from a different generation; see _build_postings_once.
    With one lock per machine, a generation published during a build is refused as "another-build", so the holder
    builds again for whatever is live when it finishes (up to POSTINGS_CATCHUP_ROUNDS more) rather than leaving the
    sidecar a generation behind until the next publish. Each catch-up is incremental from the build just made."""
    status = _build_postings_once(index_path, force, quiet)
    for _ in range(POSTINGS_CATCHUP_ROUNDS):
        if status not in ("built", "superseded"):
            break
        nxt = _build_postings_once(index_path, False, quiet, catchup=True)
        if nxt not in ("built", "superseded"):
            break                  # current, or refused: the answer is still what the last build did
        status = nxt
    return status


def _build_postings_once(index_path=INDEX, force=False, quiet=True, catchup=False):
    """Rebuild the sidecar IF it is absent or built from a different generation. Returns a short status word.

    Called only from the DETACHED `bm25.py refresh` / `build` process, never from a read path: rebuilding
    is exactly the kind of full rewrite of a growing structure that must not run where somebody is waiting
    (a_deliberate_recall_republishes_the_whole_index_2026_09_13). A stale sidecar costs nothing but the
    speedup - `_postings_conn` refuses any generation but the live one - so it is safe to be late, and the
    rate limit keeps a busy hour of memory writes from rewriting 57 MB onto a nearly-full disk every time."""
    index_path = os.fspath(index_path)
    try:
        signature = _index_signature(index_path)
        if signature is None:
            return "no-index"
        p = postings_path(index_path)
        _sweep_postings_temps(p, index_path)
        if not force:
            got = _postings_conn(index_path)
            if got is not None:
                try:
                    got[0].close()
                except Exception:
                    pass
                return "current"
            # RATE-LIMIT PER GENERATION, NOT PER CLOCK (corrected 2026-09-14, measured). A wall-clock
            # limit blocks exactly the case the sidecar exists for: a build finished at 09:05:30, a peer
            # published 34 s later, and the catch-up was refused as "rate-limited" while every hook fell
            # back to the payload path. The thing worth refusing is a SECOND build for a generation this
            # machine already tried and failed on; a NEW generation is always worth the 13 s. The short
            # wall-clock floor below remains only as a thundering-herd guard between concurrent refreshes.
            stamp = p + ".attempt"
            try:
                with io.open(stamp, encoding="utf-8") as fh:
                    tried = fh.read().strip()
            except OSError:
                tried = ""
            if tried == str(signature):
                try:
                    if _time.time() - os.stat(stamp).st_mtime < POSTINGS_MIN_REBUILD_S:
                        return "rate-limited"
                except OSError:
                    pass
            elif not catchup:             # a catch-up's own stamp is the one this would see
                try:
                    if _time.time() - os.stat(stamp).st_mtime < POSTINGS_HERD_S:
                        return "another-build"
                except OSError:
                    pass
        # ONE BUILDER PER MACHINE, NOT PER GENERATION (1 Nov, Mac, measured). The lock used to sit beside the
        # sidecar, whose name carries the generation, so a build for each new generation took a lock of its own:
        # at 03:52 three builds (~0.9 GB each) for three generations ran at once through swap, started 01:48 to
        # 03:42, and the log shows two ending "superseded". The 600 s staleness was also by age alone, which would
        # let a second build in beside one that swap had slowed past ten minutes. Now: one lock per index, stale
        # only once its holder is gone (or past POSTINGS_LOCK_DEAD_S), and the builder goes round again for any
        # generation published while it worked (maybe_build_postings).
        lock = index_path + ".postings.build.lock"
        try:
            if _time.time() - os.stat(lock).st_mtime > POSTINGS_LOCK_STALE_S and not _lock_holder_alive(lock):
                os.remove(lock)
        except OSError:
            pass
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
        except OSError:
            return "another-build"
        try:
            try:
                with io.open(p + ".attempt", "w", encoding="utf-8") as fh:
                    fh.write(str(signature))     # WHICH generation was attempted, not merely when
            except OSError:
                pass
            t0 = _time.time()
            # the base is picked here, under the build lock; the sweep above keeps it on disk (I1)
            base = _postings_base(index_path, p) if _POSTINGS_INCR_ON else None
            try:
                terms, docs = build_postings(index_path, base=base)
            except PostingsSuperseded as e:
                if not quiet:
                    print("%s bm25 postings: stopped, %s (generation %s; a newer sidecar is on disk)"
                          % (_stamp(), e, _postings_tag(signature)))
                return "superseded"
            if not quiet:
                print("%s bm25 postings: %d terms over %d docs in %.1f s -> %s %s"
                      % (_stamp(), terms, docs, _time.time() - t0, os.path.basename(p), _postings_note()))
            return "built"
        finally:
            try:
                with io.open(lock, encoding="utf-8") as fh:
                    mine = fh.read().strip() == str(os.getpid())
                if mine:                  # a lock taken over after a stale removal is somebody else's now
                    os.remove(lock)
            except OSError:
                pass
    except Exception as e:
        if not quiet:
            print("%s bm25 postings: build failed (%s) - the payload path still answers" % (_stamp(), e))
        return "failed"


class _SidecarDf:
    """The sidecar's df read term by term from table p (get, [], in, prefetch); anything that walks the whole map
    (len, iteration, keys/values/items) loads the vocabulary blob once and answers from it."""

    def __init__(self, con):
        self._con, self._c, self._full = con, {}, None

    def _all(self):
        if self._full is None:
            import zlib
            row = self._con.execute("select blob from vocab where k='df'").fetchone()
            self._full = pickle.loads(zlib.decompress(row[0])) if row else {}
        return self._full

    def prefetch(self, terms):
        if self._full is not None:
            return
        need = [t for t in set(terms) if t not in self._c]
        for i in range(0, len(need), 900):
            chunk = need[i:i + 900]
            got = dict(self._con.execute("select term, df from p where term in (%s)" % ",".join("?" * len(chunk)),
                                         chunk))
            for t in chunk:
                self._c[t] = got.get(t)

    def get(self, term, default=None):
        if self._full is not None:
            return self._full.get(term, default)
        if term not in self._c:
            self.prefetch([term])
        v = self._c.get(term)
        return default if v is None else v

    def __getitem__(self, term):
        v = self.get(term)
        if v is None:
            raise KeyError(term)
        return v

    def __contains__(self, term):
        return self.get(term) is not None

    def __len__(self):
        return len(self._all())

    def __iter__(self):
        return iter(self._all())

    def keys(self):
        return self._all().keys()

    def values(self):
        return self._all().values()

    def items(self):
        return self._all().items()


class BM25:
    def __init__(self, path=INDEX):
        # LAZY OVER THE SIDECAR (2026-09-14, behind BRAIN_BM25_POSTINGS). When a term-major sidecar exists
        # for THIS generation, construction reads a header and nothing else: `score` answers from the
        # sidecar and `df` comes from its 2.5 MB vocabulary blob, so a hook that only seeds pays ~0.09 s
        # instead of 0.71 s. Any other attribute (tf, dl) falls through __getattr__ and loads the payload
        # exactly as before, and every failure path falls back to the payload - the sidecar is a cache.
        self._path = os.fspath(path)
        self._sc = _postings_conn(self._path) if _POSTINGS_ON else None
        if self._sc is not None:
            _con, self.n, self.avgdl = self._sc
            return
        z = _load_payload(path)
        self.n, self.avgdl, self.df, self.dl, self.tf = z["n"], z["avgdl"], z["df"], z["dl"], z["tf"]

    def __getattr__(self, name):
        """Load what the sidecar does not carry, once, on first touch. Only reached for attributes that
        __init__ did not set, so a payload-backed instance never comes through here."""
        if name.startswith("_") or name not in ("df", "dl", "tf"):
            raise AttributeError(name)
        sc = self.__dict__.get("_sc")
        if name == "df" and sc is not None and os.environ.get("BRAIN_BM25_LAZY_DF", "1") != "0":
            # a hook that only spell-normalizes a prompt reads a few dozen terms; unpickling the whole 394k-term
            # blob cost it ~0.11-0.14 s per prompt (30 Oct). Single terms come from table p; iteration loads the blob.
            self.df = _SidecarDf(sc[0])
            return self.df
        if name == "df" and sc is not None:
            import zlib
            try:
                row = sc[0].execute("select blob from vocab where k='df'").fetchone()
                if row:
                    df = pickle.loads(zlib.decompress(row[0]))
                    self.df = df
                    return df
            except Exception:
                pass
        z = _load_payload(self.__dict__.get("_path", INDEX))
        self.df, self.dl, self.tf = z["df"], z["dl"], z["tf"]
        self.n, self.avgdl = z["n"], z["avgdl"]
        return self.__dict__[name]

    @classmethod
    def from_payload(cls, payload):
        """Score over a payload produced by refresh_in_memory() - the same shape _load_payload returns."""
        o = cls.__new__(cls)
        o._sc = None            # an in-memory merge is newer than any sidecar: never route this one there
        o._path = INDEX
        o.n, o.avgdl, o.df, o.dl, o.tf = payload["n"], payload["avgdl"], payload["df"], payload["dl"], payload["tf"]
        return o

    @classmethod
    def from_docs(cls, docs):
        """Score over an in-memory docs dict (evals compare candidate index texts with the live scorer)."""
        o = cls.__new__(cls)
        o._sc = None            # a candidate corpus is not the live index: never route this one at the sidecar
        o._path = INDEX
        df, tfs, dl = {}, {}, {}
        for slug, tks in docs.items():
            tf = {}
            for t in tks:
                tf[t] = tf.get(t, 0) + 1
            tfs[slug] = tf
            dl[slug] = len(tks)
            for t in tf:
                df[t] = df.get(t, 0) + 1
        o.n = len(docs)
        o.avgdl = (sum(dl.values()) / o.n) if o.n else 0.0
        o.df, o.dl, o.tf = df, dl, tfs
        return o

    def score(self, query, topk=20):
        sc = self.__dict__.get("_sc")
        if sc is not None:
            out = score_postings(query, topk, self.__dict__.get("_path", INDEX), conn=sc)
            if out is not None:
                return out      # proven identical to the loop below: eval_bm25_postings, 2,175 queries, 0 mismatches
        q = [t for t in tokens(query) if t in self.df]
        if not q:
            return []
        # sorted() so the summation order (and thus the last ulp of each score) is identical in every
        # process - set order is hash-randomized per process, which made eval runs differ bit-for-bit
        idf = {t: math.log(1.0 + (self.n - self.df[t] + 0.5) / (self.df[t] + 0.5)) * QUERY_TERM_WEIGHTS.get(t, 1.0)
               for t in sorted(set(q))}
        scores = {}
        for slug, tf in self.tf.items():
            s = 0.0
            for t in idf:
                f = tf.get(t)
                if f:
                    s += idf[t] * f * (K1 + 1) / (f + K1 * (1 - B + B * self.dl[slug] / self.avgdl))
            if s > 0:
                scores[slug] = s
        return sorted(scores.items(), key=lambda kv: -kv[1])[:topk]


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # Discovery is not execution: this script writes, so a help request must
        # exit before any work. See script_help_flag_can_execute_work.
        print(__doc__ or os.path.basename(__file__))
        return
    if sys.argv[1:2] == ["build"]:
        n, v = build()
        print("bm25 index: %d docs, %d terms -> %s" % (n, v, INDEX))
        maybe_build_postings(quiet=False)
    elif sys.argv[1:2] == ["postings"]:
        _raise_io_mem_priority()      # whoever launched it; see the measurement in the helper
        print("bm25 postings: %s" % maybe_build_postings(force="--force" in sys.argv, quiet=False))
    elif sys.argv[1:2] == ["freeze"]:
        mins = float(sys.argv[2]) if len(sys.argv) > 2 else 30.0
        why = " ".join(sys.argv[3:]) or "held by %s" % os.getpid()
        d = freeze(mins, why)
        print("%s bm25 freeze: generation held for %.0f min - %s" % (_stamp(), mins, d["why"]))
    elif sys.argv[1:2] == ["unfreeze"]:
        print("%s bm25 unfreeze: %s" % (_stamp(), "released" if unfreeze() else "nothing was frozen"))
    elif sys.argv[1:2] == ["refresh"]:
        frozen, why = freeze_state()
        if frozen:
            print("%s bm25 refresh: skipped, generation FROZEN - %s" % (_stamp(), why))
            return
        # No index at all is the one case refresh() cannot see: fresh_memories() reports no work when the index
        # is absent, so a refresh would exit 0 over a corpus nothing can recall. This detached process is where
        # a full build may run, so build here (vanilla-brain 1281b2a).
        if _index_signature(INDEX) is None:
            if not os.path.isdir(MEM):
                print("%s bm25 refresh: no memory corpus at %s (check BRAIN_MEM_DIR)" % (_stamp(), MEM))
                return
            res = build()
            n0 = res[0] if isinstance(res, tuple) else res
            print("%s bm25 refresh: no index yet - built %s docs -> %s" % (_stamp(), n0, INDEX))
            maybe_build_postings(quiet=True)
            return
        global _POSTINGS_PREBUILD
        _POSTINGS_PREBUILD = True
        added, removed = refresh(quiet=True)
        pb = PREBUILT_LAST
        if pb.get("sidecar"):
            print("%s bm25 postings: prebuilt %d terms over %d docs in %.1f s -> %s [from %s: %d kept, %d dropped, "
                  "%d placed, chain %d]" % (_stamp(), pb["terms"], pb["docs"], pb["seconds"], pb["sidecar"], pb["base"],
                                            pb["kept"], pb["dropped"], pb["placed"], pb["chain"]))
        elif pb.get("why"):
            print("%s bm25 postings: not prebuilt (%s)" % (_stamp(), pb["why"]))

        def _docs():
            # Loaded only for a line that prints it. A BLOCKED run used to load the whole payload first (about
            # 330 MB in memory) just for this count and never printed it; with 34 of them queued behind one lock
            # on 13 Oct, that load was most of the 11 GB they held.
            try:
                return _load_payload()["n"]
            except Exception:
                return -1
        if not added and not removed:
            # (0, 0) MEANS TWO DIFFERENT THINGS AND ONLY ONE OF THEM IS GOOD NEWS. refresh() also
            # returns it when it could not run at all - a lock held by another writer, a generation
            # that moved under it, a publish that timed out - and printing "already current" over
            # unindexed memories is the exact shape a guard dies of (2026-09-21: two memories sat
            # dark behind a dead holder's lock while three runs in a row reported the index current).
            still, still_gone = fresh_memories(MEM, INDEX)
            if still or still_gone:
                holder, live_holder = "", False
                try:
                    pid = int(io.open(INDEX + ".refresh.lock", encoding="utf-8").read().strip() or 0)
                    alive = _pid_is_alive(pid)
                    live_holder = alive is not False
                    holder = " - lock held by pid %d%s" % (pid, "" if live_holder else " (DEAD)")
                except Exception:
                    pass
                print("%s bm25 refresh: BLOCKED, %d memor%s and %d deletion(s) still unindexed%s"
                      % (_stamp(), len(still), "y" if len(still) == 1 else "ies", len(still_gone), holder))
                for slug in still[:5]:
                    print("    unindexed: %s" % slug)
                # A LIVE HOLDER IS NOT A FAILURE - another writer is doing this work and the next
                # run will find it done, so the scheduled task must not go red on a normal
                # collision. No live holder and work still undone IS a failure, and is the only
                # case that earns a non-zero exit: a gate that cries wolf is the gate nobody reads.
                return 0 if live_holder else 1
            print("%s bm25 refresh: index already current (%d docs)" % (_stamp(), _docs()))
        else:
            print("%s bm25 refresh: +%d fresh memor%s, -%d gone -> %d docs"
                  % (_stamp(), added, "y" if added == 1 else "ies", removed, _docs()))
        # The sidecar catches up HERE, in the detached process, whether or not this run published:
        # it is checked against the live generation, so a run that found the index already current
        # still rebuilds one that is absent or left behind by somebody else's publish.
        st = maybe_build_postings(quiet=True)
        if st not in ("current", "rate-limited"):
            print("%s bm25 postings: %s" % (_stamp(), st))
        # New memories get their wiki-link edges in the association graph now, not at the next nightly
        # spread_edges.tsv build (build_spread_edges.py --fresh, a few seconds).
        if added:
            try:
                import build_spread_edges
                build_spread_edges.fresh()
            except Exception as e:
                print("%s spread_edges_fresh: FAILED %s" % (_stamp(), str(e)[:200]))
    elif sys.argv[1:2] == ["query"] and len(sys.argv) > 2:
        for slug, s in BM25().score(" ".join(sys.argv[2:]), 10):
            print("%7.2f  %s" % (s, slug))
    else:
        print(__doc__)


if __name__ == "__main__":
    # main() returns non-zero only where it has something to report (a blocked refresh with no
    # live holder); every other path returns None, which `or 0` keeps at the historical exit 0.
    raise SystemExit(main() or 0)
