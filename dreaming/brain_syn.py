"""Query-side synonyms for the BM25 body of brain.py recall (claude-68ceaf54, 31 Oct 2026, RECALL-10X).

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
    # Mac, 4 Nov 2026 (claude-52d17aea): bare "empty" is left out. Over 111 Mac bank wordings it fired on 61 and moved
    # 2 up and 8 down ("why is the fetch log session always empty" 6 -> 12); "blank" moved 1 up and 0 down.
    (("nothing in it", "no visual", "blank"), "shows nothing"),
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
]
_RX = [(re.compile(r"\b(?:%s)\b" % "|".join(re.escape(k) for k in keys), re.I), add) for keys, add in TABLE]
# whole-word test for "the memory word is already in the question" ('tar' is not in 'starts')
_HAS = {add: re.compile(r"\b%s\b" % re.escape(add), re.I) for _k, add in TABLE}

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


def expand(query, raw=None):
    """Match the phrases on `raw` (the question as asked) and append to `query` (what BM25 scores). They differ in
    brain.py: the spell normaliser drops short words, so 'too old' reaches the scorer as 'old' and would never match."""
    raw = query if raw is None else raw
    if os.environ.get("BRAIN_RECALL_SYN", "1") == "0" or not query or not raw:
        return query
    add = [w for rx, w in _RX if rx.search(raw) and not _HAS[w].search(raw)]
    return query + " " + " ".join(add) if add else query
