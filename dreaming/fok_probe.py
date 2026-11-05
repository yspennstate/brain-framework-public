#!/usr/bin/env python3
"""fok_probe.py - Feeling-of-Knowing (Koriat 1993): a fast pre-retrieval accessibility signal.

On each user prompt, BEFORE any deliberate recall, this probes whether the brain HAS relevant
knowledge - by counting how many memory pointers light up (index lines + filenames matching the
prompt's content words), weighted by salience. If a real cluster lights up it says so and names the
strongest, so the session reaches for what it knows instead of answering from nothing. Silent on a
blank (no relevant memory) - reasoning fresh is fine, and a nag on every prompt is noise.

Koriat's accessibility model: the feeling of knowing is INFERRED from the accessibility of partial
cues, not read off the target. Here the partial cues are matching filenames + index descriptions -
cheap to count without opening a single memory body. Kills two opposite failures: answering from
nothing when a memory existed, and scanning the whole index when nothing is there.

Fail-safe silent on any error. Reads stdin (UserPromptSubmit JSON), writes injection JSON to stdout.
"""
import sys, os, io, json, re, time
from supersession import active_supersessions

HOME = os.path.expanduser("~")
MEM = os.path.join(HOME, ".claude", "projects", "{{BRAIN_SLUG}}", "memory")
SAL = os.path.join(HOME, ".claude", "dreaming", "salience.tsv")
STOP = set(("the a an and or of to in for on with is are was were be this that it its as at by from into "
            "over per not no new one two use used using via etc when then than but our we you your i me my "
            "he she they them their what which who how why can do does did has have had will would could "
            "should more most some any each all only just get got make made see saw look need want know "
            "now today about into onto out up down let me a is it to do i").split())
MIN_CLUSTER = 3   # fire only when the brain genuinely holds a cluster on this; else stay quiet
# the negative half: only speak "you don't know this" when the prompt actually ASKS for a definite answer
# (a fact / number / decision to verify). Most of the user's prompts are directives, so this stays quiet in
# normal work and fires only when a claim-check lands on an empty brain.
CLAIM_RE = (r"\b(is it true|are you sure|what'?s the|what is the|how (much|many|long|big|far)|verify|"
            r"confirm|double.?check|calculat|comput|exact(ly)?|is this (right|correct)|which is|what value|"
            r"prove that|reconcile|is that (right|correct))\b")
FONK_STATE = os.path.join(HOME, ".claude", "dreaming", ".fonk_fired.json")


def _fresh_fonk(sess):
    """Dedup feeling-of-not-knowing: once per ~5 min per session, so a run of unknowns doesn't nag."""
    if not sess:
        return True
    now = time.time()
    try:
        st = json.load(io.open(FONK_STATE, encoding="utf-8")) if os.path.exists(FONK_STATE) else {}
    except Exception:
        st = {}
    st = {k: v for k, v in st.items() if now - v < 86400}
    if now - st.get(sess, 0) < 300:
        return False
    st[sess] = now
    try:
        json.dump(st, io.open(FONK_STATE, "w", encoding="utf-8"))
    except Exception:
        pass
    return True


def _words(s):
    return set(w for w in re.findall(r"[a-zA-Z][a-zA-Z_]{2,}", (s or "").lower()) if w not in STOP)


def _distinctive_words(words, limit=12):
    """Longest cue words with a process-stable lexical tie-break."""
    return set(sorted(set(words), key=lambda w: (-len(w), w))[:limit])


# ---- v2 (2026-09-24): the feeling of knowing read off the brain's own lexical retrieval ------------
# WHY. v1 (below, kept behind FOK_LEGACY=1) counted how many of the prompt's twelve LONGEST words appear in memory
# FILENAMES and the NEWEST index lines. On a research question the harness text crowded the mathematics out of those
# twelve words, most memories were never looked at, and "computations" in a test's rules tripped the definite-answer
# test. Measured on a brain-on/brain-off study's own streams: "feeling-of-NOT-knowing ... you'll be reasoning from scratch" reached
# the model in 26 of 30 round-1 brain-on runs and 40 of 40 in rounds 2-3 - on every house-result question, whose answer
# brain.py recall ranks first - and the "you know this" line never fired once. The brain told its research agents it
# knew nothing, and they consulted it in 2 of 30 runs.
# HOW. BM25 (the index recall uses) on the prompt with harness text removed; three readings of the answer:
#   s1   the top memory's score - short void questions never reach S1_MIN (voids max 30, house questions min 32)
#   conc s1 / s10 - a hub memory that shares vocabulary with any mathematics does not stand above the tenth
#   rare the share of the question's K rarest corpus words the top memory contains - author names, problem numbers,
#        notation; a named problem's memory carries them, a vocabulary-heavy neighbour does not
# knowing if s1 >= S1_MIN and (conc >= CONC_MIN or rare >= RARE_MIN); not-knowing only if s1 < VOID_MAX and the
# question itself (harness removed) asks for a definite answer; otherwise silent. Thresholds fitted on calibration sets
# only (T14 named/midwork/absent, round 1's house questions, 20 short voids, 20 long arXiv proofs no memory holds):
# 0 false "knowing" on the 50 voids, 0 false "not-knowing" on 90 house questions (v1: nearly all), knowing on 40/40
# named questions; held out, the 14 research questions of that study's fourth round: knowing 11, silent 3, not-knowing 0. On the user's last
# 400 prompts: knowing 78 (v1 81), not-knowing 8 (v1 20); the named memory relevant in about 20 of 25 sampled.
S1_MIN, CONC_MIN, RARE_MIN, VOID_MAX, RARE_K = 30.0, 1.9, 0.6, 25.0, 8
_HARNESS_CUT = re.compile(r"This is one item of a timed|Rules for this session:|Finish your reply with exactly", re.I)
_HARNESS_HEAD = re.compile(r"^\s*\[headless-worker\][^\n]*\n", re.I)
KNOW_STATE = os.path.join(HOME, ".claude", "dreaming", ".fok_knowing_fired.json")


def _strip_harness(prompt):
    p = _HARNESS_HEAD.sub("", prompt or "", count=1)
    m = _HARNESS_CUT.search(p)
    return p[:m.start()] if m else p


def _front(slug):
    """(description, status) of a memory, read from its own frontmatter."""
    try:
        t = io.open(os.path.join(MEM, slug + ".md"), encoding="utf-8", errors="replace").read(20000)
    except Exception:
        return "", "", ""
    m = re.match(r"---\n(.*?)\n---\n", t, re.S)
    fm = m.group(1) if m else ""
    desc = (re.search(r"^description:\s*(.*)$", fm, re.M) or [None, ""])[1].strip().strip('"')
    status = (re.search(r"^\s*status:\s*(\S+)", fm, re.M) or [None, ""])[1].strip()
    return desc, status, t


def _fresh_knowing(sess, slug, window=1800):
    """Name the same memory to the same session at most once per 30 minutes."""
    if not sess:
        return True
    now = time.time()
    try:
        st = json.load(io.open(KNOW_STATE, encoding="utf-8")) if os.path.exists(KNOW_STATE) else {}
    except Exception:
        st = {}
    st = {k: v for k, v in st.items() if now - v < 86400}
    key = sess + "|" + slug
    if now - st.get(key, 0) < window:
        return False
    st[key] = now
    try:
        json.dump(st, io.open(KNOW_STATE, "w", encoding="utf-8"))
    except Exception:
        pass
    return True


# v2.2 (2026-09-24): the IDENTIFIER channel. A question that names a published problem ("Aldous and Fill
# ... their Open Problem 7.27") can have its answer memory at rank 1 and still fall short of both tests, because sibling
# memories on the same source score almost as high (conc 1.59 < 1.9) and its rarest words are notation the memory does
# not repeat (rare share 0). What marks the answer memory is that it names the same problem: its slug or its first 4,000
# characters carry the same dotted or two-digit number AND a name from the 80 characters before the label. A number
# alone is not enough (every source has a Problem 4.2), a name alone is not enough (every Aldous-Fill memory says
# Aldous). Measured offline before landing: +1 held answer (the 7.27
# question), no change on the near-miss and void sets, and not one of the user's last 400 prompts reads differently.
_ID_LABEL = re.compile(r"\b(?:Open\s+Problem|Problem|Question|Conjecture|Remark|Exercise|Ansatz|Lemma|Theorem|"
                       r"Proposition|Hypothesis)s?\s+(\d{1,3}(?:\.\d{1,3}){0,2})\b")
_ID_STRONG = re.compile(r"^(?:\d{1,3}\.\d{1,3}(?:\.\d{1,3})?|\d{2,3})$")
_ID_NAME = re.compile(r"\b([A-Z][a-z]{2,}(?:-[A-Z][a-z]{2,})?)\b")
_ID_STOP = {"Open", "Problem", "Question", "Conjecture", "Remark", "Exercise", "Ansatz", "Lemma", "Theorem", "Proposition",
            "Hypothesis", "Chapter", "Section", "Part", "Their", "They", "The", "This", "For", "Let", "Use", "And",
            "Random", "Reversible", "Markov", "Chains", "Walks", "Graphs", "Probability", "Networks", "Trees", "Surveys",
            "Settle", "Answer", "Given", "Consider", "Suppose", "Show", "Prove", "Does", "What", "When", "How", "Which",
            "Why", "Stationary", "Simply", "Nonlinear", "Forward", "Brownian", "Quadratic", "Critical", "Discrepancy",
            "Balls", "Under", "After", "Before", "With", "From", "Into", "Over", "Then", "Thus", "Here", "There", "These",
            "Those", "Note", "Also", "Since", "While", "Where", "Every", "Each", "Some", "Many", "Most", "More", "Such",
            "Both", "Either", "Neither", "Only", "Even", "Still", "Just", "About", "Above", "Below", "Between", "Within",
            "Without", "Using", "Following", "Recall", "Assume", "Define", "Write", "Take", "Make", "Find", "Give",
            "State", "Check", "Corrections", "Inbox", "Skip", "Gmail", "None", "Selected", "Content"}


def _id_match(q, slug, text):
    """The top memory names the same labelled problem as the question: its number (dotted, or two or three digits) in
    the slug's tokens or as a whole token in the text, and a name from just before the label in the slug or text."""
    low = slug.lower()
    toks = low.split("_")
    body = (text or "")[:4000]
    for m in _ID_LABEL.finditer(q):
        num = m.group(1)
        if not _ID_STRONG.match(num):
            continue
        names = {n for part in _ID_NAME.findall(q[max(0, m.start() - 80):m.start()]) for n in part.split("-")
                 if n not in _ID_STOP and len(n) >= 4}
        if not names:
            continue
        parts = num.split(".")
        k = len(parts)
        in_slug = "".join(parts) in toks or any(toks[i:i + k] == parts for i in range(len(toks) - k + 1))
        in_text = re.search(r"(?<![\w.])%s(?![\w]|\.\d)" % re.escape(num), body) is not None
        if not (in_slug or in_text):
            continue
        if any(n.lower() in toks or re.search(r"\b%s\b" % re.escape(n), body) for n in names):
            return True
    return False


def _v2(data):
    import math
    from bm25 import BM25, tokens
    q = _strip_harness(data.get("prompt", ""))[:3000]
    if len(q.strip()) < 12:
        return
    b = BM25()
    superseded = active_supersessions(MEM)
    top, seen = [], set()
    for s, v in b.score(q, topk=12):
        s2 = superseded.get(s, s)
        if s2 not in seen:
            seen.add(s2)
            top.append((s2, v))
    s1 = top[0][1] if top else 0.0
    if top and s1 >= S1_MIN:
        s10 = top[min(9, len(top) - 1)][1]
        conc = s1 / s10 if s10 > 0 else 99.0
        df, n = b.df, b.n
        qt = sorted(set(t for t in tokens(q) if t in df))
        idf = {t: math.log(1.0 + (n - df[t] + 0.5) / (df[t] + 0.5)) for t in qt}
        rare = sorted(qt, key=lambda t: (-idf[t], t))[:RARE_K]
        desc, status, text = _front(top[0][0])
        toks = set(tokens(text))
        rare_share = sum(1 for t in rare if t in toks) / max(1, len(rare))
        if conc >= CONC_MIN or rare_share >= RARE_MIN or _id_match(q, top[0][0], text):
            slug = top[0][0]
            # a correction recorded only in the memory's own frontmatter (superseded_by:) is not in the supersession
            # index; follow it one hop so the session is handed the correction, not the withdrawn claim
            # top level or indented under metadata: (the memory tooling moves it there; 96 of 477 superseded memories)
            nxt = re.search(r"^[ \t]*superseded_by:\s*([a-z0-9_]+)\s*$", text[:4000], re.M)
            if nxt and os.path.exists(os.path.join(MEM, nxt.group(1) + ".md")):
                slug = nxt.group(1)
                desc, status, _ = _front(slug)
            if not _fresh_knowing(data.get("session_id") or "", slug):
                return
            hint = {"verified": "verified", "provisional": "provisional - unverified",
                    "withdrawn": "WITHDRAWN - read its correction"}.get(status, status or "status not recorded")
            near = ", ".join("`%s`" % s for s, _ in top[1:3]) or "none"
            d = desc[:320] + ("" if desc[:320].endswith((".", "?", ")")) else ".")
            ctx = ("[metamemory] feeling-of-knowing: a memory lands on this - `%s` (%s): %s Also near it: %s. Read "
                   "it (python ~/.claude/dreaming/brain.py recall \"<key terms>\") before reasoning from scratch: it may "
                   "answer the question, or record a route already tried; weigh it, it can be wrong." % (
                       slug, hint, d, near))
            sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                                                "additionalContext": ctx}}))
            _sess = data.get("session_id") or ""
            if re.match(r"^[0-9a-f]{8}-", _sess):
                try:
                    with io.open(os.path.join(HOME, ".claude", "dreaming", "surfaced.tsv"), "a",
                                 encoding="utf-8") as _sf:
                        _sf.write("%.0f\t%s\t%s\tfok\n" % (time.time(), _sess[:12], slug))
                except Exception:
                    pass
            return
    if s1 < VOID_MAX and len(set(tokens(q))) >= 3 and re.search(CLAIM_RE, q, re.I) and \
            _fresh_fonk(data.get("session_id") or ""):
        ctx = ("[metamemory] feeling-of-NOT-knowing: no stored memory lands on this and it asks for a definite "
               "answer - you'll be reasoning from scratch. Flag your uncertainty plainly, prefer to verify (run it, "
               "check a source) over asserting, and remember 'I'd need to check' is a valid answer.")
        sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                                            "additionalContext": ctx}}))


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # Discovery is not execution: this script writes, so a help request must
        # exit before any work. See script_help_flag_can_execute_work.
        print(__doc__ or os.path.basename(__file__))
        return
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except Exception:
        return
    try:
        if data.get("hook_event_name") != "UserPromptSubmit":
            return
        # 2026-09-07: silent on a mathematical argument - a feeling-of-knowing about work memories is noise
        # beside a proof under audit (measured on the probability bank; proof_sense.py speaks there)
        try:
            import exact_clauses as _ec
            if _ec._is_math_argument(data.get("prompt", "")):
                return
        except Exception:
            pass
        if os.environ.get("FOK_LEGACY") != "1":
            try:
                _v2(data)
                return
            except Exception:
                pass            # fail over to v1 rather than go dark
        words = _words(data.get("prompt", ""))
        if len(words) < 2:
            return
        # spell-normalize OUT-OF-VOCAB words against the corpus vocabulary (bm25.QueryNormalizer:
        # edit1-only, df>=2, in-vocab words untouched - a clean prompt is identical). Measured
        # 2026-07-09 in eval_fok_abstention's typo arm: the user's typos HALVE the knows-rate
        # (0.82 -> 0.31, a false-void on things the brain holds); per-token correction restores 0.82
        # with chimera false-recall and true-void catch UNCHANGED (0 nonsense repairs over 6 seeds).
        # Fail-open: no index -> words unchanged.
        try:
            from bm25 import BM25, QueryNormalizer
            _qn = QueryNormalizer(BM25().df)
            words = set(_qn._correct(w) for w in words)
        except Exception:
            pass
        # distinctive words only - a long/rambling prompt must not light up the whole corpus (it once
        # reported ~700). Keep the ~12 longest (a decent proxy for rarity) so the count means something.
        # Deterministic equal-length cutoff: set iteration is hash-randomized across processes.
        words = _distinctive_words(words)
        salmap = {}
        try:
            with io.open(SAL, encoding="utf-8") as f:
                next(f, None)
                for ln in f:
                    p = ln.split("\t")
                    if len(p) >= 2:
                        slug = p[0][:-3] if p[0].endswith(".md") else p[0]
                        try:
                            salmap[slug] = float(p[1])
                        except Exception:
                            pass
        except Exception:
            pass
        superseded = active_supersessions(MEM)
        best = {}     # slug -> rank score (overlap + salience tiebreak) - for NAMING the strongest
        best_ov = {}  # slug -> INTEGER overlap - all threshold tests run on this (the 0.01*salience
                      # bump reaches 1.0 at salience 100, enough to fake a 3-overlap; found 2026-07-09)
        # rich pointers: the live index lines (filename + description)
        try:
            with io.open(os.path.join(MEM, "MEMORY.md"), encoding="utf-8") as f:
                for ln in f:
                    m = re.match(r"- \[([^\]]+?)\.md\]", ln)
                    if not m:
                        continue
                    slug = m.group(1)
                    overlap = sum(1 for w in words if w in ln.lower())
                    if overlap >= 2:
                        current = superseded.get(slug, slug)
                        best[current] = max(best.get(current, 0),
                                            overlap + 0.01 * salmap.get(current, 0))
                        best_ov[current] = max(best_ov.get(current, 0), overlap)
        except Exception:
            pass
        # broad pointers: filenames across the whole store (catches memories not in the live index)
        try:
            for fn in os.listdir(MEM):
                if not fn.endswith(".md") or fn in ("MEMORY.md", "INDEX_ARCHIVE.md", "PREDICTIONS.md"):
                    continue
                slug = fn[:-3]
                overlap = sum(1 for w in words if len(w) >= 4 and w in slug.lower())
                if overlap >= 2:
                    current = superseded.get(slug, slug)
                    best[current] = max(best.get(current, 0),
                                        overlap + 0.01 * salmap.get(current, 0))
                    best_ov[current] = max(best_ov.get(current, 0), overlap)
        except Exception:
            pass
        hits = sorted(best.items(), key=lambda kv: kv[1], reverse=True)
        n = len(hits)
        # CONCENTRATION requirement (2026-07-09, eval_fok_abstention): a real feeling-of-knowing needs
        # the query to land ON a memory (a top hit with >=3 overlapping words), not just >=3 memories
        # each clearing the overlap>=2 floor - the flat count confabulated on familiar-word chimeras
        # (false-recall 0.57 vs 0.07-0.12 with concentration; separation 0.10->0.55+, over 6 seeds,
        # 'knows' rate unchanged at 0.68). A blank/thin/scattered signal takes the not-knowing path.
        # Tested on INTEGER overlap, never the salience-bumped rank score.
        top_overlap = max(best_ov.values()) if best_ov else 0
        if n < MIN_CLUSTER or top_overlap < 3:
            # feeling-of-NOT-knowing (abstention): NOTHING LANDS SQUARELY - no memory carries >=3 of
            # the prompt's words (scatter rule, eval_fok_abstention 2026-07-09: catches 0.90 of true
            # voids vs 0.15 for the old absolute n==0 rule, false-abstention 0.00 over 6 seeds; the
            # old rule went silent as the corpus grew - chance 2-overlaps on generic words masked
            # voids, the humility fonk fixture failure). Speak only on a definite-answer ask, once.
            raw = data.get("prompt", "") or ""
            if top_overlap <= 2 and len(words) >= 4 and re.search(CLAIM_RE, raw, re.I) and _fresh_fonk(data.get("session_id") or ""):
                ctx = ("[metamemory] feeling-of-NOT-knowing: no stored memory lands squarely on this "
                       "(at most scattered word-matches) and it asks for a definite answer - you'll be "
                       "reasoning from scratch. Flag your uncertainty plainly, prefer to verify (run it, "
                       "check a source) over asserting, and remember 'I'd need to check' is a valid "
                       "answer. Don't manufacture confidence to fill the gap.")
                sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                                                    "additionalContext": ctx}}))
            return  # blank or thin - stay quiet
        top = ", ".join(f"`{s}`" for s, _ in hits[:3])
        conf = "HIGH" if n >= 6 else "MEDIUM"
        ctx = (f"[metamemory] feeling-of-knowing {conf}: the brain holds ~{n} memories bearing on this "
               f"(strongest: {top}). You KNOW things here - run `python ~/.claude/dreaming/brain.py "
               f"recall \"<key terms>\"` before reasoning from scratch so explicit corrections stay in view.")
        sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                                            "additionalContext": ctx}}))
        # durable surface-log for attribution.py (real uuid sessions only - keep test fixtures out)
        _sess = data.get("session_id") or ""
        if re.match(r"^[0-9a-f]{8}-", _sess):
            try:
                with io.open(os.path.join(HOME, ".claude", "dreaming", "surfaced.tsv"), "a", encoding="utf-8") as _sf:
                    _sf.write("%.0f\t%s\t%s\tfok\n" % (time.time(), _sess[:12], hits[0][0]))
            except Exception:
                pass
    except Exception:
        return


if __name__ == "__main__":
    try:
        import os as _o, sys as _s
        _s.path.insert(0, _o.path.dirname(_o.path.abspath(__file__)))
        import brain_switch as _bsw
        _bsw.gate()
    except SystemExit:
        raise
    except Exception:
        pass
    main()
