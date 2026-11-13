"""Query-side synonyms for the BM25 body of brain.py recall (31 Oct 2026, RECALL-10X).

The memories name a failure by its cause ("stale", "shows nothing"); a question names it by what was seen ("too old",
"expired", "nothing in it", "no visual"). BM25 matches exact words, so those questions miss. This appends the memory's
word to the query when a seen-as phrase is present; the original words are kept, so a question that already matches
loses nothing. The table is small and reviewed by hand: add a row only with a held-out before/after.
BRAIN_RECALL_SYN=0 turns it off."""
import os
import re

# (phrases a question uses, words the memories use)
TABLE = [
    (("too old", "expired", "outdated", "out of date"), "stale"),
    (("nothing in it", "no visual", "blank", "empty"), "shows nothing"),
    # the memories name the file by its suffix; 'temp folder' is left alone (those memories say 'temp')
    (("temp file", "temp files", "temporary file", "temporary files", "temp name"), "tmp"),
    # an API's daily allowance is what the memories call its quota; 'commit allowance' and 'premium allowance' stay out
    (("daily allowance", "allowance ran out", "ran out of allowance", "out of allowance", "api allowance",
      "upload allowance", "allowance was used up", "allowance used up", "allowance was spent", "spent the allowance"),
     "quota"),
    # probe 10 (1 Nov, content_gap_68ce/RESULT_probe10_1nov.md): four wordings questions use and memories do not
    (("documentary", "documentaries"), "docs film"),
    (("spelling hint", "spelling hints", "phonetic spelling"), "respelling"),
    (("backup job", "backup jobs"), "keeper"),
    (("dies partway", "died partway", "dies part way", "dies halfway", "dies midway"), "killed"),
    # probe 11 (1 Nov, content_gap_68ce/RESULT_probe11_1nov.md): render memories say chunk where questions say segment
    (("segment", "segments"), "chunk"),
    # probe 20 (2 Nov, content_gap_68ce/RESULT_probe20_2nov.md): his disk questions say space / storage ("are we out of
    # space agsin?", "2 T of stroage on this computer"), the laptop-disk memories say disk. Phrases only: a bare
    # 'space' / 'storage' row lost 39 stored questions (box storage, how much space a film uses).
    (("out of space", "running out of space", "run out of space", "more space", "free space", "free up space",
      "get back space", "back more space", "space on this computer", "storage on this computer",
      "stroage on this computer", "out of storage", "more storage", "more stroage", "storage is full",
      "space is full", "2 t of storage", "2 t of stroage", "space agsin", "space again", "space left",
      "low on space", "no space"), "disk"),
    (("swap file", "swap files", "swapfile", "page file", "page files", "paging file", "paging files"), "pagefile"),
    # q63 (4 Nov, recall_loop_68ceaf54_2026_10_27/RESULT_q63_4nov.md): the bond-leg memories name the fund tlt; a
    # question says long bond. 1,087 live memories say tlt and never these words, none says them without tlt. Held 21:
    # top-8 21 -> 21 (ceiling), MRR 0.863 -> 0.952, 4 better 0 worse; an IEI question naming the long bond as its
    # comparator moved 1 -> 2.
    (("long bond", "long bonds", "long-bond", "long treasury", "long treasuries", "long-dated treasury",
      "long dated treasury", "20 year treasury", "20-year treasury", "twenty year treasury"), "tlt"),
]
_RX = [(re.compile(r"\b(?:%s)\b" % "|".join(re.escape(k) for k in keys), re.I), add) for keys, add in TABLE]
# Rows a fixed phrase list cannot hold (a year follows the phrase).
# q78 (5 Nov, recall_loop_68ceaf54_2026_10_27/RESULT_q78_5nov.md): a question asks why a book "did badly in 2019" or "a
# bad year", and the memory calls it a weak year. Dev 29 -> 1, held [1,3,2,1] -> [1,1,1,1]. Five negatives are
# unchanged, except one 2022 question outside the top 8 either way (58 -> 122). No stored question fires it. A bare
# 'badly' row was rejected: it moved a views question 2 -> 6.
# BRAIN_RECALL_SYN_YEAR=0 leaves it out.
if os.environ.get("BRAIN_RECALL_SYN_YEAR", "1") != "0":
    _RX.append((re.compile(r"\b(?:badly|poorly)\s+in\s+(?:19|20)\d\d\b|\b(?:bad|poor)\s+years?\b", re.I), "weak"))
# q83 (5 Nov, recall_loop_68ceaf54_2026_10_27/RESULT_q83_5nov.md): a question names the abbreviation by example ("cut
# in two at St. or Dr.") and the normalizer drops "St"/"Dr", so the splitter memories, which say "abbreviation", lose
# their only clue (dev 111 -> 1 BM25-alone with the word added). Fires only when a capitalised title abbreviation with
# its period AND a split/break word are both in the question, so "Dr. Owhadi" questions are untouched.
# BRAIN_RECALL_SYN_ABBR=0 leaves it out.
if os.environ.get("BRAIN_RECALL_SYN_ABBR", "1") != "0":
    _RX.append((re.compile(
        r"^(?=.*\b(?:St|Dr|Mr|Mrs|Ms|Sgt|Gen|Col|Capt|Lt|Jr|Sr|Rev|Prof|Ft|Mt|Gov|Sen|Pres|Adm|Maj|Cpl|Pvt|Fr)\.(?=\s|$))"
        r"(?=.*(?i:\b(?:split|splits|splitting|cut|cuts|break|breaks|broke|broken|chop|chops|chopped|stops|stopped"
        r"|half|halves|in two|new line|new sentence)\b))", re.S), "abbreviation"))
# q84 (5 Nov, recall_loop_68ceaf54_2026_10_27/RESULT_q84_5nov.md): a question asks how big the hedge's f can be ("the
# most f can be", "how high is f allowed") and the normalizer drops the lone "f", so the f-cap memories (dynamic f,
# 0 <= f <= 0.8) have no clue left. Adds "dynamic" and "0_8" (the normalised 0.8) when a lowercase stand-alone f AND a
# size or limit word are both in the question; f( f- f' and f-strings do not count. The 0.8 is today's cap: if the
# law moves it, change this row with it. BRAIN_RECALL_SYN_FCAP=0 leaves it out.
if os.environ.get("BRAIN_RECALL_SYN_FCAP", "1") != "0":
    # a maths question about a function f is not about the hedge: on "if f is convex what is the max on the interval"
    # the unguarded row put three f-cap notes on top (live eval 5 Nov), so maths words switch it off
    _FCAP = (r"^(?!.*(?i:\b(?:convex|concave|interval|function|functions|derivative|integral|integrable|continuous"
             r"|differentiable|lemma|theorem|proof|prove|polynomial|domain|density|densities|probability|random"
             r"|matrix|eigenvalue|norm|measure|series|sequence|bounded|maximi[sz]e|minimi[sz]e|real-valued|analytic"
             r"|lipschitz|harmonic|holomorphic|of x|in x|f\s*=)\b))"
             r"(?=.*(?<![\w.'\-/])f(?![\w'(\-/]|\.\w))"
             r"(?=.*(?i:\b(?:most|max|maximum|maxed|ceiling|cap|capped|limit|limits|limited|high|higher|highest|big"
             r"|bigger|biggest|large|larger|largest|above|exceed|exceeds|bound|upper|allowed|allow|allows|range)\b))")
    _RX.append((re.compile(_FCAP, re.S), "dynamic"))
    _RX.append((re.compile(_FCAP, re.S), "0_8"))
# whole-word test for "the memory word is already in the question" ('tar' is not in 'starts')
_HAS = {add: re.compile(r"\b%s\b" % re.escape(add), re.I) for _rx, add in _RX}
if "0_8" in _HAS:
    _HAS["0_8"] = re.compile(r"(?<![\d.])0?[._]8(?![\d])")

# probe 15 (1 Nov, content_gap_68ce/RESULT_probe15_1nov.md): the memories split British and US spellings (390 files say
# neighbours, 64 neighbors; 427 catalogue, 1050 catalog), and bm25's typo snap only corrects a word seen in at most 8
# memories, so a question in one spelling never reached memories written in the other. Each split word in the question
# gets its other spelling scored at weight 0.3 by brain.py (BM25 is a sum over terms, so this is exact). brain.py
# reads BRAIN_RECALL_SPELL (1 on, 0 off) and BRAIN_RECALL_SPELL_W.
SPELL_STEMS = [("neighbour", "neighbor"), ("colour", "color"), ("behaviour", "behavior"), ("favour", "favor"),
               ("analys", "analyz"), ("organis", "organiz"), ("normalis", "normaliz"), ("summaris", "summariz"),
               ("recognis", "recogniz"), ("optimis", "optimiz"), ("prioritis", "prioritiz"), ("unrecognis", "unrecogniz")]
SPELL_WORDS = [("centre", "center"), ("centres", "centers"), ("centred", "centered"), ("centring", "centering"),
               ("licence", "license"), ("licences", "licenses"), ("catalogue", "catalog"), ("catalogues", "catalogs"),
               ("catalogued", "cataloged"), ("cataloguing", "cataloging"), ("grey", "gray"), ("modelling", "modeling"),
               ("modelled", "modeled"), ("travelled", "traveled"), ("travelling", "traveling"), ("labelled", "labeled"),
               ("labelling", "labeling"), ("unlabelled", "unlabeled"), ("mislabelled", "mislabeled"),
               ("relabelled", "relabeled"), ("cancelled", "canceled"), ("cancelling", "canceling")]
# mined 1 Nov 2026 (probe16_mine.py, both forms in >= 5 memories, checked by hand; probe16_eval.py measured them).
# BRAIN_RECALL_SPELL_MINED=0 leaves them out.
SPELL_MINED = [("acknowledgement", "acknowledgment"), ("ageing", "aging"), ("analogue", "analog"), ("analogues", "analogs"),
               ("annualisation", "annualization"), ("annualise", "annualize"), ("annualised", "annualized"),
               ("annualises", "annualizes"), ("artefact", "artifact"), ("artefacts", "artifacts"),
               ("authorisation", "authorization"), ("authorise", "authorize"), ("authorised", "authorized"),
               ("authorises", "authorizes"), ("authorising", "authorizing"), ("capitalised", "capitalized"),
               ("characterisation", "characterization"), ("characterise", "characterize"),
               ("characterised", "characterized"), ("defence", "defense"), ("dialled", "dialed"),
               ("discretisation", "discretization"), ("emphasised", "emphasized"), ("factorisation", "factorization"),
               ("finalising", "finalizing"), ("flavoured", "flavored"), ("formalisation", "formalization"),
               ("formalised", "formalized"), ("generalisation", "generalization"), ("generalise", "generalize"),
               ("generalised", "generalized"), ("generalises", "generalizes"), ("generalising", "generalizing"),
               ("honour", "honor"), ("honoured", "honored"), ("honouring", "honoring"), ("honours", "honors"),
               ("hypothesised", "hypothesized"), ("initialisation", "initialization"), ("initialised", "initialized"),
               ("judgement", "judgment"), ("judgements", "judgments"), ("labour", "labor"),
               ("localisation", "localization"), ("localised", "localized"), ("localises", "localizes"),
               ("materialise", "materialize"), ("maximise", "maximize"), ("maximiser", "maximizer"),
               ("maximises", "maximizes"), ("maximising", "maximizing"), ("metre", "meter"), ("metres", "meters"),
               ("minimisation", "minimization"), ("minimised", "minimized"), ("minimises", "minimizes"),
               ("minimising", "minimizing"), ("mislabelling", "mislabeling"), ("monetise", "monetize"),
               ("monetises", "monetizes"), ("neutralisation", "neutralization"), ("neutralise", "neutralize"),
               ("neutralised", "neutralized"), ("neutralises", "neutralizes"), ("neutralising", "neutralizing"),
               ("parameterisation", "parameterization"), ("parameterised", "parameterized"),
               ("personalised", "personalized"), ("programme", "program"), ("quantisation", "quantization"),
               ("quantised", "quantized"), ("randomisation", "randomization"), ("randomised", "randomized"),
               ("realisable", "realizable"), ("realisation", "realization"), ("realise", "realize"),
               ("realised", "realized"), ("realises", "realizes"), ("realising", "realizing"),
               ("regularisation", "regularization"), ("regularised", "regularized"), ("regulariser", "regularizer"),
               ("relabelling", "relabeling"), ("renormalised", "renormalized"), ("rigour", "rigor"),
               ("serialisation", "serialization"), ("serialised", "serialized"), ("serialises", "serializes"),
               ("specialised", "specialized"), ("standardised", "standardized"), ("synthesise", "synthesize"),
               ("synthesised", "synthesized"), ("synthesises", "synthesizes"), ("theatre", "theater"),
               ("theorising", "theorizing"), ("tokeniser", "tokenizer"), ("totalling", "totaling"),
               ("unauthorised", "unauthorized"), ("unmodelled", "unmodeled"), ("unnormalised", "unnormalized"),
               ("unrealised", "unrealized"), ("utilisation", "utilization"), ("vectorised", "vectorized")]
_SPELL_W = {}
for _a, _b in SPELL_WORDS + (SPELL_MINED if os.environ.get("BRAIN_RECALL_SPELL_MINED", "1") != "0" else []):
    _SPELL_W[_a], _SPELL_W[_b] = _b, _a
_WORD = re.compile(r"[a-z]+")


def _other_spelling(w):
    if w in _SPELL_W:
        return _SPELL_W[w]
    for a, b in SPELL_STEMS:
        if w.startswith(a):
            return b + w[len(a):]
        if w.startswith(b):
            return a + w[len(b):]
    return None


def spelling_fold(raw):
    """The other spelling of each split word in `raw`, in order, none already present."""
    words = _WORD.findall(raw.lower())
    have, add = set(words), []
    for w in words:
        o = _other_spelling(w)
        if o and o not in have and o not in add:
            add.append(o)
    return add


# NUMBERS WITH UNITS (5 Nov 2026, from the laptop line). "why did a NEXT file grow to 36 MB" found nothing: the
# normaliser drops "36" and "mb" when they stand apart, while the memory (and ~1,150 others) index the joined form
# ("36mb", "8gb", "14_8gb", "50ms"). Rejoin a number and a size or time unit written apart ("36 MB", "1.5 GB",
# "450 ms", "36-MB") into the indexed form; an unknown joined token scores nothing. BRAIN_RECALL_SYN_UNIT=0 leaves it out.
_UNIT_ON = os.environ.get("BRAIN_RECALL_SYN_UNIT", "1") != "0"
_UNIT = re.compile(r"(?<![\w.])(\d+(?:[.,]\d+)?)(?:\s+|\s*-\s*)(kb|mb|gb|tb|ms)\b", re.I)

# The same loss for percents, minutes, basis points and hours: "a 40% drop" reaches the scorer as "drop" (the number and
# the sign both gone), "12 pct" as "pct", while ~5,000 memories index "40pct", ~100 "29min", ~240 "25bp", ~400 "30h".
# Rejoin them into the indexed spelling. A bare "h" is left out (a variable or a typo as often as an hour).
# BRAIN_RECALL_SYN_UNIT2=0 leaves this row out.
_UNIT2_ON = os.environ.get("BRAIN_RECALL_SYN_UNIT2", "1") != "0"
_UNIT2_MAP = {"%": "pct", "pct": "pct", "percent": "pct", "min": "min", "mins": "min", "minutes": "min", "minute": "min",
              "bp": "bp", "bps": "bp", "hours": "h", "hour": "h", "hrs": "h", "hr": "h"}
_UNIT2 = re.compile(r"(?<![\w.])(\d+(?:[.,]\d+)?)(?:\s*(%)|(?:\s+|\s*-\s*)(pct|percent|minutes|minute|mins|min|bps|bp"
                    r"|hours|hour|hrs|hr)\b)", re.I)


def expand(query, raw=None):
    """Match the phrases on `raw` (the question as asked) and append to `query` (what BM25 scores). They differ in
    brain.py: the spell normaliser drops short words, so 'too old' reaches the scorer as 'old' and would never match."""
    raw = query if raw is None else raw
    if os.environ.get("BRAIN_RECALL_SYN", "1") == "0" or not query or not raw:
        return query
    add = [w for rx, w in _RX if rx.search(raw) and not _HAS[w].search(raw)]
    add = list(dict.fromkeys(add))  # two rows can add the same word; once is enough
    if _UNIT_ON:
        for n, u in _UNIT.findall(raw):
            j = n.replace(".", "_").replace(",", "_") + u.lower()
            if j not in add and j not in query.split():
                add.append(j)
    if _UNIT2_ON:
        for n, a, b in _UNIT2.findall(raw):
            j = n.replace(".", "_").replace(",", "_") + _UNIT2_MAP[(a or b).lower()]
            if j not in add and j not in query.split():
                add.append(j)
    return query + " " + " ".join(add) if add else query
