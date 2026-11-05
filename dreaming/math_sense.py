#!/usr/bin/env python3
"""math_sense.py - mathematical memory injection from the populated math brain.

On each user prompt: a cheap lexical gate decides whether the prompt is mathematical
at all (two distinct math anchors required - the general brain's measured polysemy
lesson). Only then is the population store opened and the overlay retrieval run with
strict abstention thresholds. Confident hits are injected as a compact context block:
definitions, lemmas, theorems with their exact statements, so agents connected to the
brain receive the mathematics they are thinking about mid-thought.

Ranking is done here rather than taken from retrieve() because two of the scorer's
channels are wrong for this surface. The graph channel propagates a seed's score to
its neighbours, and one closed cluster of campaign deposits owns enough of the store's
edges to collect that mass on almost any formal query; the symbol channel is pure
notation (subscripts, parentheses, commas) and elects the most notation-dense node
rather than the relevant one. Both are downweighted below, and no more than one
campaign deposit may take a slot - a cap, not a topic filter, because a trigger that
has to guess the subject gets it wrong. Measured at this exact surface.

Trust honesty: population nodes are CURATED_SECONDARY/UNVERIFIED until QA upgrades
them; the block says so. Fail-safe silent on ANY error - this hook must never break
or slow a session that is not doing mathematics. Reads stdin (UserPromptSubmit JSON),
writes injection JSON to stdout. Pure ASCII.
"""
import hashlib
import io
import json
import os
import random
import re
import sys
import time


# 2026-09-22: the hook dispatcher has launched every hook as `python -s` (no user site) since 2026-09-15, and
# numpy on this machine lives ONLY in the user site (AppData\Roaming\Python\Python314\site-packages). Measured:
# every real-session fire of this hook from 2026-09-15 20:25 to 2026-09-22 19:35 died on
# `ModuleNotFoundError: No module named 'numpy'` inside math_brain.semantic - 710 rows in mathbrain_silence.tsv,
# not one card served in a live session for eight days - while every harness run (plain `python`) served
# normally, so every bank and regression number of that week measured a path production never took. A hook has
# to find its own dependencies under the flags it is launched with: when the user site was dropped and numpy is
# not importable, put the user site back on sys.path. With the user site present, or numpy on the system site,
# this is a no-op. Controls: `python -s math_sense.py` on the read-once FEI question serves the theorem card;
# a non-mathematical prompt under the same flags stays silent; hook_liveness now flags an import-dead hook.
def _ensure_user_site():
    if not getattr(sys.flags, "no_user_site", 0):
        return
    try:
        import numpy  # noqa: F401
        return
    except ModuleNotFoundError:
        pass
    try:
        import site
        usp = site.getusersitepackages()
        if usp and os.path.isdir(usp) and usp not in sys.path:
            sys.path.append(usp)
    except Exception:
        pass


_ensure_user_site()
import unicodedata   # 2026-09-13: two helpers here fold accents; without this they raised NameError into a
# swallowed except, which silently skipped the WHOLE on-topic gate - a behaviour change that reads as a result.

HOME = os.path.expanduser("~")
# Framework deployment: the mathbrain package is vendored beside this hook, and the
# store location is deployment-specific - point MATHBRAIN_STORE at your population db.
# MATHBRAIN_SRC (or MATHBRAIN_BUILD_SRC, the name math_recall reads) points at another build.
_HERE = os.path.dirname(os.path.abspath(__file__))
BUILD_SRC = (os.environ.get("MATHBRAIN_SRC") or os.environ.get("MATHBRAIN_BUILD_SRC")
             or os.path.join(_HERE, "..", "mathbrain", "src"))
STORE_PATH = os.environ.get("MATHBRAIN_STORE", "")
# 2026-09-11: 1400 held THREE clipped cards until 2026-09-08, when cards started going out whole (the 560-char clip
# was a Codex finding); a whole card is ~600 chars and the hint header ~560, so 1400 held ONE card - measured on the
# fresh bank through the hook: 201 of 208 served blocks carried a single note, and every theorem the ranker put at
# rank 2 or 3 was "omitted whole for the context budget". 2400 = the header plus three whole cards.
# 2026-09-11 (evening): on the locate-the-fact bank a campaign note that HELD the answer ("EXACTLY 1.000 at M = 8",
# 500 characters into its statement) was served as an 80-character excerpt plus its Assumes tail, and the solver
# said UNKNOWN. 320 statement characters minus a 220-character Assumes clause leaves 80; the window cannot reach a
# value the budget never reaches. Statement budget 320 -> 480 and block 2400 -> 3000 so three fuller cards still fit.
# SHADOW (MATHBRAIN_SHADOW, default off; 29 Sep 2026, job R70). The hook computes and prints its block as usual but
# records nothing that says a card was served: no session guard shard, no use log, no vouch serve row, no surfaced
# row, no session math context, and its silence notes go to the harness file. The subagent adapter runs its held arm
# this way, so a subagent that received nothing is not counted as exposed (scheduled audits account02/B05 and
# account01/B14 on serve logs written before any receiver). Every other run is unchanged.
SHADOW = os.environ.get("MATHBRAIN_SHADOW", "0").strip() in ("1", "on", "true")
MAX_BLOCK_CHARS = int(os.environ.get("MATHBRAIN_MAX_BLOCK_CHARS", "3000"))
MAX_STATEMENT_CHARS = int(os.environ.get("MATHBRAIN_MAX_STATEMENT_CHARS", "480"))
TIME_BUDGET_S = float(os.environ.get("MATHBRAIN_TIME_BUDGET_S", "30"))   # was 2.5 until 2026-09-07: under load the
                          # store load alone passed it and the lane abstained silently; the owner's order is that
                          # the brain always goes in, so the lane now waits (the hook's own timeout is the ceiling)
# A HEADLESS WORKER WAITS LONGER (mb10d campaign, 2026-09-24, from claude-e93602d2's ab30 measurement). Research sessions
# the Task Scheduler launches run BelowNormal; with 13 of 16 cores rendering film the 0.15 s prefilter-and-load stage
# took 42 s, past the 30 s budget, the 2 s top-card integrity grace could not finish either, and the hook served NOTHING
# in both brain arms of two items - mute exactly for the background research agents it is meant to serve. Their probe
# (budget 85 s): three cards in four of four runs, 9-30 s. Nobody is waiting at a keyboard on such a prompt, so when it
# opens with the worker marker the budget is HEADLESS_BUDGET_S and the integrity grace HEADLESS_GRACE_S. The pair is sized
# for the dispatcher's 90 s UserPromptSubmit ceiling: 1.5 x 50 s of pre-ranking grace, the ranking, then 8 s of integrity
# grace. An explicit MATHBRAIN_TIME_BUDGET_S / MATHBRAIN_INTEGRITY_GRACE_S wins; interactive prompts keep 30 s and 2 s.
HEADLESS_MARKER = "[headless-worker]"
HEADLESS_BUDGET_S = float(os.environ.get("MATHBRAIN_HEADLESS_BUDGET_S", "50") or 50)
HEADLESS_GRACE_S = float(os.environ.get("MATHBRAIN_HEADLESS_GRACE_S", "8") or 8)
_HEADLESS = False


def _headless_budget(prompt):
    global TIME_BUDGET_S, _HEADLESS
    if not (prompt or "").lstrip().startswith(HEADLESS_MARKER):
        return
    _HEADLESS = True
    if "MATHBRAIN_TIME_BUDGET_S" not in os.environ:
        TIME_BUDGET_S = max(TIME_BUDGET_S, HEADLESS_BUDGET_S)


PREFILTER_K = int(os.environ.get("MATHBRAIN_PREFILTER_K", "300"))
GATE_FLOOR = float(os.environ.get("MATHBRAIN_GATE_FLOOR", "25"))     # on-topic gate, 2026-09-07 (see the block after _select)
GATE_RATIO = float(os.environ.get("MATHBRAIN_GATE_RATIO", "0.45"))   # a note must reach this fraction of the best prefilter score
# 2026-09-11: the floor is a raw BM25 sum, and a BM25 sum grows with the QUESTION. A proof-audit prompt of 1,900
# characters carries a query self-score (sum of idf over its distinct indexed tokens) of about 295, so its best
# node clears 25 without trying (99 of 100 mdlit prompts); a mathematician's one-line question carries a self-score
# of 25-40, and the very node that answers it scores 14-22 - under the floor. Measured through the hook on the 09-03
# fresh bank: theorem served 84.3 pct -> 34.8 pct unnamed, 96.6 -> 38.2 named, silent 6.7 -> 58.4 pct, four days
# after the floor landed, with the health check reading 4 of 8 must-speak probes silent. The floor now scales with
# the question: min(GATE_FLOOR, GATE_FRAC x self-score). On long prompts GATE_FLOOR still binds (0.3 x 295 = 88 > 25),
# so the proof-bank behaviour the gate was tuned on is unchanged; on short questions the gold node passes 88/89
# unnamed and 89/89 named (offline, gate_analysis_20260911.json). Rollback: GATE_FRAC=0 restores the raw floor.
GATE_FRAC = float(os.environ.get("MATHBRAIN_GATE_FRAC", "0.3"))
GATE_QPART = os.environ.get("MATHBRAIN_GATE_QPART", "1") not in ("0", "", "off")   # 19 Oct: question-part floor (see the on-topic gate)
QPART_SOLO = float(os.environ.get("MATHBRAIN_QPART_SOLO", "1.5") or 0)   # 19 Oct: margin a lone rescued card must clear
# 2026-09-12 (his 09-02 "fire less, be more confident"): COVERAGE - the best prefilter score over the query's own
# self-score says how much of the question the store's best match covers. Measured in-process on the fresh bank
# (89 theorems x 2 forms + 40 absent targets): silencing below 0.45 cut absent-target fires 34 -> 30 of 40 with ZERO
# change on the 178 answerable forms (81 / 85 served, 48 / 58 hits); 0.50 cut them to 24 but cost 3 served and 2 hits.
# Curve in state/abstention_curve_rows_20260912.json. 0 disables.
# LANDED AT 0.45 AND REVERTED WITHIN THE HOUR (2026-09-12 02:1x): the fresh bank held (0.764 / 0.933, absent fires
# 0.775 -> 0.675) but the locate banks collapsed - walk counts 0/40, open problems 19/40, two-fact pairs 0/30 - and the
# health check failed: a long enumerated question covers little of itself with one row by construction. Default 0
# (off); the dial and the curve stay for a coverage signal that is measured on EVERY bank before it lands.
# SECOND FORM (02:3x, measured on EVERY bank in-process first - state/abstention_curve_allbanks_20260912.json): the same
# 0.45 applied only when the query's self-score is at most 60 keeps the fresh bank (81 / 85 served, 48 / 58 hits), the
# walk counts 40/40, the open problems 40/40 and the pairs 29/30, and still cuts absent-target fires 34 -> 30 of 40.
# Self-score medians: theorem questions 26-37, absent targets 42, locate questions 70-113 - the length of an enumerated
# question is what made the first form blind to it.
COVERAGE_MIN = float(os.environ.get("MATHBRAIN_COVERAGE_MIN", "0.45"))
COVERAGE_QSELF_MAX = float(os.environ.get("MATHBRAIN_COVERAGE_QSELF_MAX", "60"))

# Ranking constants, measured at this surface on 2026-08-22 over three populations
# (126 stratified benchmark probes, his own math-gated prompts, and campaign claims
# self-retrieved from their own bodies with the title held out).
GRAPH_WEIGHT = 1.0        # RE-MEASURED 2026-09-01 on the post-rs store: source-excluded
                          # depends_on eval (60 pairs, hook rank formula): gw=1.0 vs 0.0
                          # doubles top-1 (15% vs 8%), top-5 52% vs 35%, median rank 5 vs
                          # 7, top-50 misses 0 vs 14; famous-query negative control: 0 rs
                          # intrusions in 36 top-3 slots under BOTH weights. The 08-22
                          # zeroing answered the fpp closed-cluster pathology; the typed
                          # rs edge layer inverts the measurement. Rollback: set 0.0.
                          # (was 0.0 2026-08-22..2026-09-01; 1.0 implicitly before that)
SYMBOL_WEIGHT = 0.1       # scales the channel's own 1.35, giving an effective 0.135
# The two abstention thresholds. Both are now readable from the environment SO THEY CAN BE MEASURED, with the
# defaults byte-for-byte the standing values - unset, the hook behaves exactly as before (2026-09-13). The near-tie
# margin is on the owner's own do-not-land list, so this is a measurement door, not a change: it lets a paired run
# say what a different margin would cost and buy before he rules on it. Never ship a non-default value in a launcher.
MIN_SUPPORT = float(os.environ.get("MATHBRAIN_MIN_SUPPORT", "0.5"))   # re-applied to the re-ranked order
MIN_MARGIN = float(os.environ.get("MATHBRAIN_MIN_MARGIN", "0.1"))
CANDIDATE_POOL = 100000   # rank the whole store, then cut - retrieve()'s own limit
SLOT_WINDOW = 40          # would cut on the pre-rerank order and hide the answer
MAX_CAMPAIGN_SLOTS = 1
#: score margin by which a campaign deposit must beat the best non-campaign node to
#: keep its rank instead of being demoted. Swept 2026-08-30: at 1.0 the inversion rate
#: is zero on campaign-drawn AND famous-theorem queries, and the campaign share of
#: slots on famous queries is unchanged at 4.6 percent.
DEMOTION_MARGIN = 1.0

CAMPAIGN_PAT = re.compile(
    r"fpp|first-passage|first passage|percolation|rademacher[ _]singularity",
    re.IGNORECASE,
)  # 2026-09-01: rs cluster added to the cap alongside fpp - pure safety; the famous-query
   # negative control measured 0 rs intrusions even uncapped, and the cap costs rs nothing
   # on its own topical queries (a cap, not a topic filter, as the original note says)

MATH_ANCHORS = re.compile(
    r"\b(theorem|lemma|corollary|proposition|proof|prove|definition|axiom|conjecture|"
    r"integral|derivative|matrix|matrices|eigenvalue|eigenvector|group|subgroup|ring|field|"
    r"vector space|topolog\w*|manifold|measure|sigma.algebra|lebesgue|banach|hilbert|"
    r"probabilit\w*|martingale|markov|random variable|distribution|variance|convergen\w*|"
    r"polynomial|prime|integer|modul(?:o|ar)|congruen\w*|graph coloring|combinatori\w*|"
    r"permutation|isomorphi\w*|homomorphi\w*|continuous|compact|bounded|norm|metric space|"
    r"lipschitz|perceptron|neural network capacity|latin square|orthogonal array|"
    r"cardinalit\w*|countable|bijection|injective|surjective|supremum|infimum|"
    r"inequality|bound|entropy|isoperimetr\w*|geodesic|curvature|homology|fundamental group|"
    r"anticoncentration|antichain|hypercube|partition function|expander|vc.dimension|"
    r"sine|cosine|holomorphic|meromorphic|differentiable|fixed.point|corollary|"
    r"if and only if|counterexample|almost everywhere|measure zero|commutative|associative|"
    r"euler|gauss|riemann|hilbert|banach|cauchy|fourier|laplace|lebesgue|dirichlet|"
    r"noether|galois|erdos|kolmogorov|poincare|brouwer|frobenius|lagrange|legendre|"
    r"fermat|bernoulli|chebyshev|minkowski|weierstrass|bolzano|dedekind|cantor|godel|shannon)\b",
    re.IGNORECASE,
)
LATEX_RE = re.compile(r"\$[^$]{2,}\$|\\(?:frac|sum|int|forall|exists|mathbb|sqrt)\b")

# Mathematics written as plain prose still carries notation, and the gate used to see
# only LaTeX. Measured 2026-08-22 on 37 one-per-family probes and 400 of his own
# prompts: three families whose correct claim ranked FIRST were refused before
# retrieval ran (a dominated-convergence question saying "integral" but never
# "theorem"), while adding this signal fires on 5 more of his prompts in 400 - 1.2%,
# about a second per hundred - and those five are his own campaign questions.
ASCII_MATH_RE = re.compile(
    r"[A-Za-z]_\{?[0-9nkij]\}?|[A-Za-z0-9)]\^\{?[0-9nkpq]\}?|"
    r"\bE\[[^\]]{1,40}\]|\bP\([^)]{1,40}\)|<=|>=|!=|->|\bsum_|\bint_|\blim_"
)


STRONG_ANCHORS = re.compile(
    r"\b(theorem|lemma|corollary|axiom|conjecture|holomorphic|meromorphic|"
    r"euler|gauss|riemann|hilbert|banach|cauchy|fourier|laplace|lebesgue|dirichlet|"
    r"noether|galois|erdos|kolmogorov|poincare|brouwer|frobenius|lagrange|legendre|"
    r"fermat|bernoulli|chebyshev|minkowski|weierstrass|bolzano|dedekind|cantor|godel)\b",
    re.IGNORECASE,
)

# 2026-09-11: the gate refused 44 of the 54 fresh-bank questions the hook was silent on - "Sylow theorems" (the
# strong list has no plural), "Talagrand's convex distance inequality", "Marchenko-Pastur law", a graph-theory
# question with no anchor word at all. The strong list knew analysis and a few probabilists; combinatorics, algebra,
# geometry, statistical mechanics and optimisation had no names in it. Additions were drawn from the eponyms the
# store's own titles carry and measured on 33,780 archived prompts by the GREEK_ANCHORS discipline: the strong set
# adds 27 fires (+0.08 pp; 19 of them his own prompts about theorems in papers, the rest Marchenko-Pastur,
# sub-Gaussian, Riesz, Jensen, Rademacher, one "Cramer"), the weak set 9 more (+0.03 pp; "edges"/"vertex" in
# brain-graph and column-list prompts - the cost of the two-anchor rule). Rejected on the same measurement: "warning"
# (45 fires, none Chevalley-Warning), "abel" (his typo for able), "holder" (the English word). Fresh bank: the gate
# passes 62 -> 85 unnamed, 72 -> 88 named of 89; the six must-stay-silent probes stay silent.
STRONG_ANCHORS_2 = re.compile(
    r"\b(theorems|lemmas|corollaries|conjectures|axioms|"
    r"sylow|burnside|cayley|tur[aá]n|ramsey|menger|kuratowski|urysohn|tychonoff|tietze|zorn|dilworth|sperner|"
    r"k[oö]nig|erd[oő]s|ko-rado|szemer[eé]di|lov[aá]sz|kruskal|katona|"
    r"hoeffding|azuma|mcdiarmid|talagrand|bernstein|chernoff|hanson-wright|paley-zygmund|efron-stein|"
    r"borel-cantelli|khintchine|lindeberg|berry-esseen|slutsky|donsker|skorokhod|prokhorov|"
    r"girsanov|feynman-kac|fokker-planck|galton-watson|cram[eé]r|sanov|varadhan|"
    r"marchenko|pastur|tracy-widom|wigner|semicircle|"
    r"cheeger|alon-boppana|spielman|benjamini|schramm|kesten|fkg|reimer|peierls|griffiths|"
    r"sherrington|kirkpatrick|hopfield|"
    r"jensen|hölder|fatou|fubini|tonelli|egorov|lusin|radon-nikodym|riesz|plancherel|parseval|"
    r"hahn-banach|banach-alaoglu|banach-steinhaus|krein-milman|arzel[aà]|ascoli|stone-weierstrass|"
    r"picard|gronwall|lipschitz|sobolev|rellich|kondrachov|sard|morera|liouville|casorati|"
    r"heine-borel|bolzano|darboux|dini|"
    r"nakayama|wedderburn|nullstellensatz|schur|perron-frobenius|cayley-hamilton|gram-schmidt|cholesky|"
    r"hensel|chevalley|mordell|hasse|goldbach|collatz|"
    r"gauss-bonnet|hopf-rinow|bishop-gromov|cartan-hadamard|gromov|hodge|betti|borsuk|ulam|brouwer|kakutani|knaster|tarski|"
    r"hartman|grobman|poincare-bendixson|sharkovskii|oseledets|"
    r"johnson-lindenstrauss|sauer-shelah|glivenko|cantelli|rademacher|vapnik|"
    r"kkt|karush|kuhn-tucker|farkas|lagrangian|"
    r"neyman|rao-blackwell|lehmann|scheff[eé]|"
    r"l[oö]wenheim|skolem|goodstein|paris-harrington|cook-levin|savitch|courcelle|"
    r"brownian motion|ising model|riemannian|hypercontractiv\w*|percolation|boolean function\w*|algebraically closed|"
    r"abelian|complementary slackness|quadratic reciprocity|spontaneous magneti[sz]ation|first-passage|fpp|"
    r"sub-?gaussian|quadratic forms?|martingale\w*|stopping times?|arithmetic progressions?|"
    r"harmonic function\w*|elliptic equations?|hypoelliptic|sigma-algebra|pi-system|lambda-system|"
    r"large deviations?|rate function|convex bod(?:y|ies)|chromatic number|independence number|"
    r"projective curve|rational points|number field|perceptron|"
    # 2026-09-11 evening: the store carries 359 "Open problem:" ledger nodes and the gate had no word for them - a
    # locate bank asking where a ledger problem is posed was refused on 27 of 40 questions. Measured on 33,789
    # archived prompts: +119 fires (+0.35 pp), the sample entirely his own open-problem goals - the prompts where
    # this brain should speak.
    r"open[- ]problems?|unsolved problems?)\b", re.IGNORECASE)
# 2026-09-12 (goal 30h, item 2): a 30-question owner-voice bank about the probability campaign's OWN results was
# silent on 18; 16 of those were this gate refusing before retrieval, most with NO anchor at all - "is the Aldous-Janson
# ansatz 20 true", "Hasebe Ueda Wang problem 4.7 ...", "Bradley question 7 ...", "is tau star O(n log n) over
# arc-transitive graphs". Two shapes were missing: a NUMBERED CITATION (problem 4.7, question 17.18, ansatz 20,
# conjecture 4.2 - the way a mathematician names a problem in a source) and the probability vocabulary the strong list
# lacked ("brownian motion" was there, "brownian" alone was not; nothing for unimodal, arc-transitive, spectral gap,
# ergodic, mixing, additive functionals, normal approximation, beta-splitting). Measured on 33,845 archived prompts by
# the GREEK_ANCHORS discipline (lanes/m1_probstore/measure_anchors.py, ANCHOR_MEASUREMENT.txt): the whole set adds
# ONE fire to the 19,155 prompts the gate refuses (a random-matrix prompt), the six must-stay-silent probes stay silent,
# and it rescues 15 of the 16 refused owner-voice questions. Landed in two edits (the 2026-09-12 rule): default OFF
# first, then measured ON against OFF on a quiet machine, sequential, same store - fresh bank 71/89 + 84/89, absent
# fires 28/40, long prompts 74/100, notes 170, health 11/6: IDENTICAL on every aggregate and on every item's hit and
# rank (six items differed only in card order or block length, the hook's clock-dependent branches); owner-voice
# served 12 -> 24 and result nodes 10 -> 19 of 30, nothing lost. Default flipped ON 23:2x. MATHBRAIN_ANCHORS_3=0 restores.
ANCHORS_3_ON = os.environ.get("MATHBRAIN_ANCHORS_3", "1").strip() not in ("0", "", "off", "false")
DERIVED_ANCHORS = os.environ.get("MATHBRAIN_DERIVED_ANCHORS", "0").strip() in ("1", "on", "true")
_DERIVED_VOCAB = None


def _derived_anchor_vocab():
    """The corpus-derived anchor words, loaded once. Empty set when the file is missing or unreadable, which
    makes the dial inert rather than erroring - a cost filter must never be the reason a prompt crashes."""
    global _DERIVED_VOCAB
    if _DERIVED_VOCAB is None:
        try:
            with io.open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "math_anchor_vocab.json"),
                         encoding="utf-8") as fh:
                _DERIVED_VOCAB = frozenset(json.load(fh).get("terms") or ())
        except Exception:
            _DERIVED_VOCAB = frozenset()
    return _DERIVED_VOCAB
# 2026-09-22 (two-Opus study, campaign_2026-09-22_two_opus_with_without/tools/measure_gate_candidates.py): three of
# thirty natural questions about results the store holds were refused here - "read-once formulas over AND, OR and
# NOT ... H(f) <= C I(f)", "the exact read-once FEI constant conjectured to be", "Janson's Problems 18.41 and 18.42"
# - and the cli path served each one's source card first. Measured on 37,361 archived prompts (21,484 refused by the
# gate) with the three positive controls firing (warning 50, abel 2, edges 6): conjectur(ed|ing) +0 fires, read-once
# +0, the PLURAL numbered citation ("Problems 18.41") +1 (a peer's message about a math-brain memory), and the
# store's own eponyms +0 each; none breaks a must-stay-silent probe; together they rescue all three. REJECTED on the
# same measurement: prov(es|ed|en|ing) as a weak anchor - +57 fires, sampled as owner campaign orders and film
# surgery prompts, not mathematics. The rejected word stays out; MATHBRAIN_ANCHORS_3=0 still restores the 09-11 gate.
STRONG_ANCHORS_3 = re.compile(
    r"\b(?:(?:open\s+problems?|problems?|questions?|conjectures?|lemmas?|theorems?|propositions?|corollar(?:y|ies)|"
    r"ansatz|hypothes[ie]s)\s+\d+(?:\.\d+)*|"
    r"brownian|unimodal\w*|(?:arc|vertex|edge)-transitive|spectral gap|(?:relaxation|mixing|cover|hitting) times?|"
    r"(?:phi|alpha|beta|rho)-mixing|strong(?:ly)? mixing|ergodic\w*|stationary (?:measure|distribution|law|sequence)s?|"
    r"additive functionals?|normal approximation|beta[- ]splitting|"
    r"conjectur(?:ed|ing)|read-once|aldous|janson|khoshnevisan|leskel[aä]|hasebe|ueda|burdzy|scheutzow|thorisson|"
    r"kemeny|lyons-peres)\b", re.IGNORECASE)
# 2026-09-24 (campaign mb10d, T14): the programs the store is densest in had no words of their own at the gate. Five
# of ten questions that NAME the FEI program were refused before retrieval ran ("In the Gonzalez-MacManus-Pereyra
# restriction approach to FEI, what value must rho_0 reach", "What FEI constants are proved for read-k DNFs"), and the
# --query path answered all five. Measured on 17,403 distinct archived prompts of his (12,148 refused by the gate),
# tools/measure_program_anchors.py in the campaign folder, controls firing (warning 40, abel 2, edges 6): FEI +2 (both
# FEI work orders), Walsh / DNF / LTF / read-k / KKL / total influence / threshold gate / monotone function +0 each, no
# must-stay-silent probe broken; on the real-traffic shadow the FEI admissions stay silent downstream. REJECTED on the
# same measurement: ReLU (+71, nearly all about the ReLU film), CNF (+4, all the fund entity, never conjunctive normal
# form) and "singular value(s)" (+2, one of which then drew three junk cards - Thevenin, Large cardinal, a
# quasi-geodesics definition - on a ReLU-iteration research order). The DNF pattern is upper case and does not take CNF.
# MATHBRAIN_PROGRAM_ANCHORS=0 restores the gate without them.
PROGRAM_ANCHORS_ON = os.environ.get("MATHBRAIN_PROGRAM_ANCHORS", "1").strip() not in ("0", "", "off", "false")
PROGRAM_ANCHORS = re.compile(r"\bFEI\+?(?![\w-])|\bDNFs?\b|\bLTFs?\b|\bKKL\b")
PROGRAM_ANCHORS_WORDS = re.compile(
    r"\b(walsh|read-k|total influences?|threshold (?:gates?|functions?)|"
    r"monotone (?:boolean )?functions?)\b", re.IGNORECASE)
# weak: two of these (or one plus notation) open the gate, like MATH_ANCHORS; never alone
WEAK_ANCHORS_2 = re.compile(
    r"\b(vertex|vertices|edges|clique|planar|subgraph|bipartite|monochromatic|colou?rings?|labelled trees|"
    r"subsets|shatter\w*|orbits?|fixed points?|cyclic groups?|finitely generated|ideal|almost every|"
    r"locally integrable|invertible|normal space|closed sets|tangent space|exponential map|multipliers|"
    r"constrained optim\w*|change of measure|density process|couplings|spin correlations?|contours|passage time|"
    r"storage capacity|random patterns|influence|euclidean|pairwise distances|primes|integers|"
    r"empirical distribution|moment generating|gaussian process|direct sum|finite group|finite set|"
    r"hyperbolic|fundamental groups?|isometric|collinear|solvable)\b", re.IGNORECASE)
# 2026-09-24 22:2x (campaign mb10d, arm cand29): PLURALS AND THE STORE'S OWN NAMES. Every anchor above ends with \b, so a
# plural never matched its singular: "eigenvalue" opened the gate and "the real eigenvalues of the real Ginibre ensemble"
# did not, "latin square" did and "Latin squares of order 12" did not, "random variable" did and "exchangeable sequence
# of random variables" did not. Read off the refused questions of four banks (batch 1 of the coverage cards, the house
# bank, the memory-blind house bank, fresh2); a word is kept only if it is in at least one of the store's own node titles
# AND adds no fire among the 12,251 of his 17,532 distinct archived prompts the gate refuses today, controls firing
# (warning 40, abel 2, edges 6; tools/measure_gate_plurals.py, results/gate_plurals_v2.txt). As one gate the set adds 2
# prompts, both mathematics (his "concentration inequalities, probability theory" order and an agent's MOLS report).
# REJECTED on the same measurement: fields (+25, the audit-agent orders), groups (+7, ML pruning), bounds (+3),
# derivatives (+3, agent messages), distributions (+2, research-fleet orders). Not in any title, so left out:
# autocorrelation, not-all-equal, counterexamples, subgroups, orthogonal arrays. The weak words count as one more anchor
# (two anchors, or one plus notation, open the gate); the strong ones open it alone. Used only here, never in the
# long-text density of _nonmath_text. MATHBRAIN_ANCHORS_4=0 restores the gate without them.
ANCHORS_4_ON = os.environ.get("MATHBRAIN_ANCHORS_4", "1").strip() not in ("0", "", "off", "false")
WEAK_ANCHORS_4 = re.compile(
    r"\b(eigen(?:values|vectors|functions|spaces)|polynomials|permutations|integrals|manifolds|inequalities|geodesics|"
    r"martingales|bijections|variances|latin squares|norms|measures|random variables|simplex|polytopes?|"
    r"stochastically)\b", re.IGNORECASE)
STRONG_ANCHORS_4 = re.compile(
    r"\b(ginibre|gumbel|ces[aà]ro|volterra|pollaczek|khinchine?|rayleigh|total[- ]variation|commute[- ]times?|"
    r"spectral-gap|supermodular\w*|orthomorphisms?|interlacements?|subset sums?|radius of convergence)\b", re.IGNORECASE)

# SINGULAR VALUES (MATHBRAIN_ANCHORS_5, default on; campaign mb10d, 25 Sep, arm cand47). "the case where the fifth singular
# value is bigger then 1" - his words for our quadratic-form result - was refused here: no list carried the term. Measured over
# his 17,688 distinct archived prompts (campaign tools/measure_gate_linalg.py): it admits 2 of the 12,393 the gate refused, both
# his random-matrix research orders; no must-stay-silent probe breaks and no absent-target question is admitted. His spellings
# of it are corrected before the gate reads the text (the spelling map, arm cand49), not matched here: a gate that admits a misspelt
# prompt hands retrieval words it cannot match. RMT (+105 pruning-project work orders) and spectral/operator norm (admits an
# absent-target control) were rejected on the same run. MATHBRAIN_ANCHORS_5=0 restores the gate without it.
ANCHORS_5_ON = os.environ.get("MATHBRAIN_ANCHORS_5", "1").strip() not in ("0", "", "off", "false")
STRONG_ANCHORS_5 = re.compile(r"\bsingular[- ]values?\b", re.IGNORECASE)
# A POSSESSIVE NAME BEFORE AN INEQUALITY, IDENTITY, FORMULA OR CRITERION IS A MATH ANCHOR (MATHBRAIN_ANCHORS_POSSESSIVE, default on;
# campaign mb10d, 25 Sep, arm cand63). The eponym lists leave out "holder" and "young" as English words, so "What does Holder's
# inequality say about the product of two functions?" and "State Young's inequality for convolutions" were refused at the gate, and
# so was the hook-path bank's "Wald's identity for the expectation of a randomly stopped sum". Counted before it ran: it admits none
# of his 11,644 archived prompts and one bank question (Wald). "theorem" and "lemma" are strong anchors already.
ANCHORS_POSSESSIVE_ON = os.environ.get("MATHBRAIN_ANCHORS_POSSESSIVE", "1").strip() not in ("0", "", "off", "false")
STRONG_ANCHORS_POSSESSIVE = re.compile(r"\b[A-Z][a-zà-ÿ]+(?:-[A-Z][a-zà-ÿ]+)*['’]s?\s+(?:inequalit(?:y|ies)|identit(?:y|ies)|formula|criterion)\b")

# HIS SPELLING (MATHBRAIN_HIS_SPELLING, default on; campaign mb10d, 25 Sep, arm cand49). His orders about our results are typed
# fast ("the full proof of the 5 singualr value version", "all the poorfs"), and neither the gate nor retrieval can read a
# misspelt word. A speller against the store's vocabulary invents eponyms (benjamin -> Benjamini), so this is a reviewed list:
# each entry read by hand off campaign tools/mine_his_spelling.py (a word of his archive, used twice or more, neither a store
# term nor an English word) and kept only when it is unambiguously a misspelling of that word. Ambiguous ones are left out
# ("continuou" is his "continue", "experation" may be expiration, "interal" internal, "thery" there), and so is every
# COMMON word he misspells (mathematical, problem, theory, quantum, functions, estimate): measured on his traffic, a
# low-weight word corrected only reshuffles the ranking (two junk blocks, arm cand49). Only the text the gate and
# retrieval read is corrected; nothing he wrote is shown back. MATHBRAIN_HIS_SPELLING=0 reads his text as typed.
HIS_SPELLING_ON = os.environ.get("MATHBRAIN_HIS_SPELLING", "1").strip() not in ("0", "", "off", "false")
_HIS_SPELLING = {
    "singuler": "singular", "singualr": "singular", "sinuler": "singular", "singulr": "singular", "singurla": "singular",
    "singuel": "singular",
    "matricies": "matrices", "submatricies": "submatrices", "submatirices": "submatrices", "submatriices": "submatrices",
    "subamtrix": "submatrix", "submatirx": "submatrix", "submatix": "submatrix",
    "binomail": "binomial", "binimial": "binomial", "binomila": "binomial",
    "stocastic": "stochastic",
    "diffution": "diffusion", "difussion": "diffusion", "difustion": "diffusion",
    "convergecne": "convergence", "convergece": "convergence",
    "distirbution": "distribution",
    "gussain": "gaussian", "gussian": "gaussian",
    "hypothisis": "hypothesis",
    "kernal": "kernel", "kernsl": "kernel",
    "lipshitz": "lipschitz",
    "theorm": "theorem", "theorme": "theorem", "theormes": "theorems", "theorms": "theorems",
    "veribles": "variables",
    "differntial": "differential",
    "momentium": "momentum", "momenum": "momentum",
    "navir": "navier",
    "ranodm": "random", "rnadom": "random", "ranomness": "randomness", "ranomnes": "randomness",
    "spectram": "spectrum",
    "meausre": "measure", "meausres": "measures",
    "inequalites": "inequalities",
    "proability": "probability", "proabability": "probability",
    "poorfs": "proofs",
}
_HIS_SPELLING_RE = re.compile(r"\b(" + "|".join(sorted(map(re.escape, _HIS_SPELLING), key=len, reverse=True)) + r")\b",
                              re.IGNORECASE)


def _his_spelling(text):
    if not HIS_SPELLING_ON or not text:
        return text

    def rep(m):
        w = m.group(0)
        f = _HIS_SPELLING.get(w.lower())
        if not f:
            return w
        return f[:1].upper() + f[1:] if w[:1].isupper() else f
    return _HIS_SPELLING_RE.sub(rep, text)


# LATEX ACCENTS FOLD (MATHBRAIN_LATEX_ACCENTS, default on; campaign mb10d, 25 Sep, arm cand57). A proof pasted from its LaTeX
# source writes H\"older, Poincar\'e, Erd\H{o}s, \v{C}ech; the prefilter reads H\"older as "h" and "older" and the curated names
# never see it, so a proof citing "H\"older's inequality" got no Hölder card. The accent macro folds to its letter before the gate
# and retrieval: the symbol accents (\" \' \` \^) inside a word only - a JSON \" at a word start is left alone - and the letter
# accents (\H \v \c \u \k \r) with one braced letter anywhere.
LATEX_ACCENTS_ON = os.environ.get("MATHBRAIN_LATEX_ACCENTS", "1").strip() not in ("0", "off", "false", "")
_LATEX_ACC_BR = re.compile(r"\{\\[\"'`^]([A-Za-z])\}")
_LATEX_ACC_SYM = re.compile(r"(?<=[A-Za-z])\\[\"'`^](?:\{([A-Za-z])\}|([A-Za-z]))")
_LATEX_ACC_LET = re.compile(r"\\[Hvcukr]\{([A-Za-z])\}")


def _latex_accents(text):
    if not LATEX_ACCENTS_ON or not text or "\\" not in text:
        return text
    t = _LATEX_ACC_BR.sub(r"\1", text)
    t = _LATEX_ACC_SYM.sub(lambda m: m.group(1) or m.group(2), t)
    return _LATEX_ACC_LET.sub(r"\1", t)



# --- candidate additions (gate defect fix) ------------------------------------
# The \b before the subscript variable is load-bearing. Without it, `fb_in1k` inside a
# model name like DeiT-Base-Dist.patch16_224.fb_in1k reads as "b subscript i", and so do
# ft_in1k and mae_in1k. Measured on 19,093 of his own prompts, the unanchored version put
# 960 extra fires (+5.03pp) on traffic that was almost never mathematics. Anchored, the
# same fix costs +0.85pp and the new fires are mathematical. A mathematician writes
# single-letter variables (X_1, a_n, S_n); snake_case identifiers have multi-letter
# segments, and a word boundary separates the two cleanly. DO NOT REMOVE THE \b.
STRUCTURAL_MATH_RE = re.compile(
    r"\b[A-Za-z]_\{?[0-9nkijmp]\}?\b"
    r"|\b[A-Za-z]\^\{?[0-9nkpqd]\}?\b"
    r"|\bE\[[^\]]{1,40}\]"
    r"|\bP\([^)]{1,40}\)"
    r"|\bsum_|\bint_|\blim_|\binf_|\bsup_"
    r"|\|\|[^|]{1,20}\|\|"
    r"|<[A-Za-z][^>]{0,20},[^>]{0,20}>"
)

# `convergen\w*` never matched the base verb - "converge", "converges", "converged" all
# missed while only "convergence"/"convergent" hit. Plus the probability idioms the
# original list omitted ("almost surely" was absent while "almost everywhere" was there).
EXTRA_ANCHORS = re.compile(
    r"\b(almost surely|i\.i\.d|iid|in distribution|in probability|"
    r"inner product|expectation|subsequence|converg\w*|poisson|erlang(?=\s+(?:mixture|distribution)\b)|mmpp|"
    r"phase.type|convexity|convex(?=\s+(?:in|function|combination|hull|cone|set|optimization)\b)|concavity|concave(?=\s+(?:in|function)\b)|second moments?|random sums?)\b",
    re.IGNORECASE,
)

# Greek letters SPELLED OUT are a strong signal that prose is mathematical, and the anchor
# list had none - so "at which M is the engine's inequality tight for chi = 1/3, and what
# value must beta take there" carried ONE anchor and was refused before retrieval ran, while
# retrieval with the gate lifted returns a rank-1 hit at 8.75.
#
# WHICH LETTERS, decided by measurement on 28,116 archived prompts rather than by taste,
# because on THIS machine most of the alphabet is not mathematics:
#
#   all 21 names          +21.95pp, 6,171 new fires - alpha, beta, gamma, delta, theta, rho,
#                         sigma and vega are the options Greeks and saturate the trading
#                         traffic; the new fires were loop ticks and sleep instructions
#   minus options Greeks   +0.25pp, 70 new fires - still bad, and for a reason no
#                         hand-written negative would produce: "eta" matches ETA, so a
#                         customer-support assistant prompt lit the gate 21 times. "nu" and
#                         "xi" fired on non-ASCII table text rather than on mathematics.
#   this set              +0.078pp, 22 new fires, ALL of them mathematical - his own
#                         math-brain loop prompts, an RMT bibliography check, an RMT
#                         ensemble review, and the Ising perceptron capacity at kappa != 0
#
# That is an eighth of the +0.85pp the last widening cost. Same discipline as the \b lesson
# above: hand-written negatives passed there too, and only real traffic found the defect.
GREEK_ANCHORS = re.compile(
    r"\b(chi|kappa|mu|phi|psi|omega|epsilon|zeta|iota|upsilon)\b",
    re.IGNORECASE,
)

# Enumerative combinatorics had no anchor at all. The H1 test of 2026-09-05 (60 private
# self-avoiding-walk counts only the store holds) found the plain question form an agent
# would type - "How many self-avoiding walks of length 10 on the square lattice Z^2 start at
# (0,0), stay in the strip 0 <= y < 3, and avoid the sites (-4,2)?" - refused by this gate
# before retrieval ran (one structural notation, zero word anchors), while cli_query with the
# gate bypassed served the right block; the served H1 form passed only because it said
# "exact integer". Measured 2026-09-05 22:4x on 33,278 archived prompts (86.7 MB) by the same
# discipline as GREEK_ANCHORS: 538 prompts carry one of these words, 537 already fired, and
# each candidate set added ZERO new fires - a single weak anchor still needs notation or a
# second anchor, which is what keeps "enumerate the files" silent. The corpus did NOT contain
# a prompt with two of these words and no mathematics ("enumerate the files ... rename the
# lattice config"); the health check's negative case did, and it fired - so these words open
# only the anchor-plus-notation path in _math_gate (see there), never the two-anchor path.
# Never promote these to MATH_ANCHORS or STRONG_ANCHORS.
ENUMERATION_ANCHORS = re.compile(
    r"\b(self.avoiding|lattice|random walk\w*|enumerat\w*|generating function\w*)\b",
    re.IGNORECASE,
)

# A prompt that carries an argument the reader is checking, rather than a question to look up: three or more
# numbered steps, or the words that name the act of checking one. Used only by the kind preference below.
ARGUMENT_SHAPE = re.compile(
    r"(?:(?:^|\n)\s*\d+[.)]\s.*){3,}|\b(first invalid step|is the proof correct|audit the (?:numbered )?argument"
    r"|which step (?:is|fails)|find the error in the proof)\b",
    re.IGNORECASE | re.DOTALL,
)

# A PASTED PROOF IS AN ARGUMENT (MATHBRAIN_ARG_PROOFTEXT, default on; campaign mb10d, 25 Sep, arm cand44). ARGUMENT_SHAPE wants
# numbered steps or a phrase naming the act of checking, and no proof as a paper writes it has either: on the 35 proofs of the
# quadratic-form density papers (banks/qform_proofs_v1.json, the text the agents checking that paper paste) the hook reached 4
# of the 57 results the proofs cite by name, because the named-result sub-queries, the four-card cap and the argument
# exemptions never applied. A prompt also counts as an argument when it reads as a proof: 40 words or more, four or more LaTeX
# math commands from the list below (a fixed list, never any backslash word - a Windows path would pass that), and a citation
# made the way proofs make one: "by/from/using/applying/via <Name>'s [one or two words] theorem|lemma|...", or "by Lemma 3.2" /
# "Lemma~\ref". 0 switches it off.
ARG_PROOFTEXT = os.environ.get("MATHBRAIN_ARG_PROOFTEXT", "1").strip() not in ("0", "off", "false", "")
_PROOF_LATEX_RE = re.compile(
    r"\\(?:le|leq|ge|geq|lesssim|gtrsim|int|iint|sum|prod|frac|dfrac|tfrac|sqrt|mathbb|mathcal|mathbf|mathrm|operatorname"
    r"|lambda|sigma|mu|nu|epsilon|varepsilon|delta|alpha|beta|gamma|theta|omega|Omega|phi|varphi|psi|xi|eta|tau|rho|kappa"
    r"|infty|cdot|langle|rangle|det|tr|log|exp|sup|inf|max|min|lim|partial|nabla|to|mapsto|subset|subseteq|in|notin"
    r"|forall|exists|times|otimes|oplus|circ|quad|qquad|left|right|big|Big|norm|abs|mathsf|hat|tilde|bar|overline)(?![A-Za-z])")
_PROOF_CITE_RE = re.compile(
    r"\b(?:[Bb]y|[Ff]rom|[Uu]sing|[Aa]pplying|[Vv]ia)\s+(?:the\s+)?"
    r"[A-Z][\w'’]+(?:(?:--|[-‐-―])[A-Z][\w'’]+)*(?:'s|’s)?(?:\s+[a-z][a-z-]{2,}){0,2}\s+"
    r"(?:theorem|lemma|inequality|formula|identity|principle|decomposition|criterion|bound|estimate|test|complement)\b"
    r"|\b(?:[Bb]y|[Ff]rom|[Uu]sing)\s+(?:Lemma|Theorem|Proposition|Corollary|Remark)[~\s]*(?:\\ref\{|\d)"
    # the two standard citation forms anywhere in the text: a possessive name ("Tonelli's theorem", "Fatou's lemma gives")
    # and a double-barrelled name ("Cauchy--Schwarz inequality")
    r"|\b[A-Z][\w’]+(?:'s|’s)(?:\s+[a-z][a-z-]{2,}){0,2}\s+"
    r"(?:theorem|lemma|inequality|formula|identity|principle|decomposition|criterion|bound|estimate|test)\b"
    r"|\b[A-Z][a-z]+(?:--|[-‐-―])[A-Z][a-z]+(?:\s+[a-z][a-z-]{2,}){0,2}\s+"
    r"(?:theorem|lemma|inequality|formula|identity|principle|decomposition|criterion|bound|estimate|test)\b")


# A LATEX CROSS-REFERENCE AS A CITATION (MATHBRAIN_PROOF_REF_CUE, default OFF since arm cand58; on in cand56-cand57). A paper's
# proof cites its own lemmas as "Lemma~\ref{lem:schur}" with no "by" in front, and four proofs of our quadratic-form papers were
# not read as arguments for want of any other cue. OFF because on his traffic the bare form is mostly not a proof: a pasted draft
# carrying \cite{...} or a pasted session log carrying Corollary~\ref{...} became an "argument", its descriptive names ("matrix-level
# theorem", "right estimate") became sub-queries, and their cards took slots (four of his 486 prompts, one losing the card it was
# about; cand56's rehearsal). "By Lemma~\ref{...}" is still a citation under the older rule, and the curated names are one too.
PROOF_REF_CUE = os.environ.get("MATHBRAIN_PROOF_REF_CUE", "0").strip() not in ("0", "off", "false", "")
NAME_CUE = os.environ.get("MATHBRAIN_NAME_CUE", "1").strip() not in ("0", "off", "false", "")
_PROOF_REF_RE = re.compile(r"\b(?:Lemma|Lemmas|Theorem|Theorems|Proposition|Corollary|Claim)[~\s]*\\(?:ref|cref|eqref)\{|\\cite[a-z]*\{")


def _proof_text(prompt):
    if not ARG_PROOFTEXT or not prompt or len(prompt.split()) < 40:
        return False
    if len(_PROOF_LATEX_RE.findall(prompt)) < 4:
        return False
    if _PROOF_CITE_RE.search(prompt) is not None or (PROOF_REF_CUE and _PROOF_REF_RE.search(prompt) is not None):
        return True
    # A NAME IS A CITATION TOO (MATHBRAIN_NAME_CUE, default on; arm cand57): a name from the reviewed list below ("the change of
    # variables", "Use Tonelli", "its Schur complement") or a double-barrelled name after a citation verb ("apply
    # Cauchy--Schwarz"). Ten proofs of our quadratic-form papers cite only that way and were never read as arguments.
    if NAME_CUE:
        if SUBQ_CURATED and any(_rx.search(prompt) for _rx, _ in _CURATED_NAMED):
            return True
        if SUBQ_BARE_DOUBLE and BARE_DOUBLE_CUE and _BARE_DOUBLE_RE.search(prompt):
            return True
    return False


def _argument_shape(prompt):
    """ARGUMENT_SHAPE, or a pasted proof (see ARG_PROOFTEXT). Returns a truthy value or None."""
    m = ARGUMENT_SHAPE.search(prompt or "")
    if m:
        return m
    return True if _proof_text(prompt) else None


# PROSE GATE (2026-09-14, staged OFF behind MATHBRAIN_PROSE_GATE=1, default off).
# Re-derived as a PATCH from the candidate the 09-09 retrieval diagnosis left pending review
# (audits/evidence/2026/09/09/math-retrieval-diagnosis). That candidate is a whole-file fork of the
# hook as it stood at 982 lines; this file is now 2,489 across 45 commits, so deploying its FILE
# would revert the identifier exemption, the per-session repetition guard, the name-token coverage
# rule and the definition-below-result rule. The idea survives, the artifact does not.
# WHAT IT IS FOR: the diagnosis found 11 real mathematical questions that never reach retrieval
# because their phrasing carries no recognised anchor combination - martingale06/08/11/12 and
# strictness03/07/08/09/10/11/12. The gate is a cost filter, and it was refusing mathematics
# silently. [[the_math_gate_refuses_real_mathematics_and_never_says_so_2026_09_14]]
# WHY IT IS OFF: a widening measured EARLIER TODAY (the derived-anchor list) looked good in
# aggregate - silent 9 -> 5 - while the per-question diff showed one gain against three confident
# wrong cards. Admission is not the outcome; the transition matrix is. This dial stays off until it
# is measured the same way, per question, on both the answerable and the unanswerable arms.
PROSE_GATE = os.environ.get("MATHBRAIN_PROSE_GATE", "0").strip() not in ("0", "off", "false", "")
TECHNICAL_PROSE_FAMILIES = (
    re.compile(r"\bgaussian\b", re.IGNORECASE),
    re.compile(r"\bnormal law\b", re.IGNORECASE),
    re.compile(r"\bcharacteristic functions?\b", re.IGNORECASE),
    re.compile(r"\bconditional mean\b", re.IGNORECASE),
    re.compile(r"\blog[- ]convex(?:ity)?\b", re.IGNORECASE),
    re.compile(r"\bmonotonicity\b", re.IGNORECASE),
)
TECHNICAL_PROSE_REASONING = re.compile(
    r"\b(prove|derive|compute|calculate|construct|determine|establish|show|"
    r"factor\w*|multipl\w*|imply|implies|satisf\w*|converg\w*|convex\w*|"
    r"monotonic\w*|independen\w*|insufficient|equal\w*)\b", re.IGNORECASE,
)
TECHNICAL_PROSE_ACTION = re.compile(
    r"\b(rename|copy|move|delete|remove|open|download|upload|render|export|"
    r"resize|recolor|format|retitle|enlarge|translate|spellcheck|pronounc\w*|"
    r"capitaliz\w*|summarize|find|locate|bigger|smaller|larger|bold|italic)\b",
    re.IGNORECASE,
)
# `paths?` and `strings?` are DELIBERATELY ABSENT from this list, though the 09-09 candidate carried both.
# Measured on that candidate's own corpus: `paths?` matches ZERO of the 60 admin negatives and one real
# mathematical prompt - it buys nothing and costs "the sample paths of the walk", which is core vocabulary in
# his domain. `strings?` is the same shape (a string in a language is mathematics). The nouns that do the work
# are unambiguous: captions 12 admin / 0 maths, movies 8/0, files 7/0, reports 7/0, headings 6/0.
TECHNICAL_PROSE_ARTIFACT = re.compile(
    r"\b(file(?:name)?s?|folders?|directories|reports?|documents?|pdfs?|videos?|movies?|"
    r"clips?|captions?|titles?|fonts?|slides?|labels?|thumbnails?|screenshots?|"
    r"assets?|narration|headings?)\b", re.IGNORECASE,
)
# ADMIN SUPPRESSION ON THE LEGACY PATH (2026-09-14, staged OFF behind MATHBRAIN_ADMIN_SUPPRESS=1).
# The 09-09 candidate scoped its action-plus-artifact suppression to its NEW route only, because the gate of
# that day kept all 60 admin negatives silent. It no longer does: replaying that corpus today, the LEGACY gate
# fires on nine of them - rename/copy/render/export/translate/find the "Gaussian martingale independence"
# movie, PDF, video, captions, title, heading, file. Five days of anchor growth walked the gate into candidate
# v1's exact defect from the other direction, and it lands on his own work, because his films are mathematical.
# MEASURED with this dial on and `paths?` removed: admin negatives firing 9 -> 1 (the survivor, "I like the
# Gaussian martingale movie", carries no action word - a comment, not a request); the 8 development positives
# lose NOTHING; ten real mathematical probes including two "sample paths" sentences lose nothing; and the two
# prompts it removes from the gate-positive set are themselves admin ("make the Cauchy Schwarz theorem title
# larger in the video", "copy the martingale CLT PDF to my Downloads folder") - corrections, not losses.
# It stays off until the regression gate has judged it, because it is the first change tonight that makes the
# gate QUIETER, and a suppression that is wrong is silent about being wrong.
ADMIN_SUPPRESS = os.environ.get("MATHBRAIN_ADMIN_SUPPRESS", "0").strip() not in ("0", "off", "false", "")


# LONG NON-MATHEMATICAL TEXT (2026-09-24, campaign mb10d). Of the 177 blocks the hook served on his own past math
# prompts, 103 were junk (hand-labelled, banks/realtraffic_labels_v1.json in the campaign folder), most of them on long
# texts that are not mathematics - code diffs and JSON, logs, trading reports, novel chapters in LaTeX, agent loop
# instructions - which pass the entry gate on a few words ("theorem", "proof", "bound") among hundreds. A prompt of 150
# or more words whose entry-gate anchor matches are under 1.5 per 100 words (LaTeX command names are not words) AND
# under 70 pct of whose content words occur in at least three cards of the store is a long text that is not
# mathematics, and gets no block. Either condition alone costs real mathematics: density alone silenced three of the
# thirty arXiv proof audits of the ab30 study (proofs written in notation carry few anchor words), and the vocabulary
# share alone misses nothing it should keep but removes less. Together, on the labelled blocks: 45 of 103 junk removed,
# 1 of 34 useful lost (an agent-loop instruction that drew a failed-route card), none of the 32 ab30 prompts; no bank
# prompt reaches 150 words. The table is the store's own <store>.lexdf.pkl (written by math_prefilter.build); without
# it the gate stays open. MATHBRAIN_NONMATH_GATE=0 switches it off.
NONMATH_GATE = os.environ.get("MATHBRAIN_NONMATH_GATE", "1").strip() not in ("0", "off", "false", "")
NONMATH_MIN_WORDS = int(os.environ.get("MATHBRAIN_NONMATH_MIN_WORDS", "150") or 150)
NONMATH_DENSITY = float(os.environ.get("MATHBRAIN_NONMATH_DENSITY", "0.015") or 0.015)
NONMATH_SHARE = float(os.environ.get("MATHBRAIN_NONMATH_SHARE", "0.70") or 0.70)
_NONMATH_WORD = re.compile(r"[A-Za-z]{2,}")
_NONMATH_LATEX = re.compile(r"\\[A-Za-z]+")
_STORE_DF = None
_STORE_N = None


def _lexdf():
    """(df, n) from <store>.lexdf.pkl, read from one snapshot and kept only when the read succeeds; ({}, 0) for this
    call when the file is absent or unreadable, and the next call reads it again (cand81: a failed first read used to
    be kept for the life of the process, leaving the non-math gate and the lead filter open for every later prompt
    of a caller that imports the hook once)."""
    global _STORE_DF, _STORE_N
    if _STORE_DF is not None and _STORE_N is not None:
        return _STORE_DF, _STORE_N
    try:
        import pickle
        with open(str(STORE_PATH) + ".lexdf.pkl", "rb") as fh:
            d = pickle.load(fh)
        df, n = d.get("df") or {}, int(d.get("n") or 0)
    except Exception:
        return {}, 0
    _STORE_DF, _STORE_N = df, n
    return df, n


def _store_df():
    """term -> number of store cards carrying it, from <store>.lexdf.pkl; {} when absent or unreadable."""
    return _lexdf()[0]


def _nonmath_text(prompt, data):
    """True (after writing the silence note) when the prompt is a long text that is not mathematics. Never raises."""
    if not NONMATH_GATE:
        return False
    try:
        words = len(_NONMATH_WORD.findall(_NONMATH_LATEX.sub(" ", prompt)))
        if words < NONMATH_MIN_WORDS:
            return False
        fams = [STRONG_ANCHORS, STRONG_ANCHORS_2, MATH_ANCHORS, EXTRA_ANCHORS, GREEK_ANCHORS, WEAK_ANCHORS_2]
        if ANCHORS_3_ON:
            fams.append(STRONG_ANCHORS_3)
        density = sum(len(rx.findall(prompt)) for rx in fams) / float(words)
        if density >= NONMATH_DENSITY:
            return False
        df = _store_df()
        if not df:
            return False
        import math_brain.retrieval as _R
        toks = _R._content_terms(_R.index_signals(prompt).lexical_tokens)
        if not toks:
            return False
        share = sum(1 for t in toks if df.get(t, 0) >= 3) / float(len(toks))
        if share >= NONMATH_SHARE:
            return False
        _silence_note(data, "nonmath_text:w=%d,d=%.4f,s=%.3f" % (words, density, share))
        return True
    except Exception:
        return False


# OFF-DOMAIN TEXT (2026-09-24, campaign mb10d). A deployment can keep a whole subject away from the math store: a math
# card served on a prompt about it is junk by construction, and such prompts pass the entry gate on a stray "theorem"
# or "stopping". On the first deployment's 480 past prompts, hand-labelled, a prompt carrying a word of its list drew
# 10 junk blocks, 4 unclear and no useful one; none of 450 bank prompts carried one. Keep the list to unambiguous
# words: everyday or combinatorial ones ("put", "call", "sim", "trade", "legs") misfire. The words are a deployment
# setting: MATHBRAIN_OFFDOMAIN_WORDS (a regular-expression alternation) or math_offdomain_words.txt beside this file
# (one alternative per line, # starts a comment). With neither the gate is off; MATHBRAIN_TRADING_GATE=0 also turns it
# off.
def _offdomain_words():
    words = os.environ.get("MATHBRAIN_OFFDOMAIN_WORDS", "").strip()
    if not words:
        try:
            path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "math_offdomain_words.txt")
            with open(path, encoding="utf-8") as fh:
                alts = [ln.split("#", 1)[0].strip() for ln in fh]
            words = "|".join(a for a in alts if a)
        except OSError:
            words = ""
    if not words:
        return None
    try:
        return re.compile(r"\b(%s)\b" % words, re.I)
    except re.error:
        return None


TRADING_WORDS = _offdomain_words()
TRADING_GATE = (TRADING_WORDS is not None
                and os.environ.get("MATHBRAIN_TRADING_GATE", "1").strip() not in ("0", "off", "false", ""))


# PASTED VERDICTS (MATHBRAIN_VERDICT_GATE, campaign mb10d, 25 Sep, arm cand33). His prompts often paste a reviewer's
# verdict on one of our papers ("Verdict: potentially publishable, but not as-is ...", "Executive verdict", "My vote is NO
# - do not resubmit") and ask an agent to act on it; the entry gate passes them on the mathematics the verdict discusses,
# and the cards that come back are about that vocabulary, not about what the prompt asks for. Hand labels of the landing
# arm's blocks on his 480 past prompts (banks/realtraffic_labels_v1.json): a verdict word in the first 400 characters
# marked 15 junk blocks, 12 undecidable and 2 useful (two proof audits of the RMT pruning paper); the words were read off
# those labels, so the out-of-sample check is his archive and his traffic after 24 Sep 16:00. MATHBRAIN_VERDICT_GATE=0
# switches it off.
VERDICT_GATE = os.environ.get("MATHBRAIN_VERDICT_GATE", "1").strip() not in ("0", "off", "false", "")
VERDICT_WORDS = re.compile(
    r"\b(publishable|publishability|referee[- ]style|referee report|peer[- ]review|accept(?:ed)? for publication|"
    r"minor revision|major revision|substantial revision|not ready for submission|resubmit)\b"
    # "verdict" only as a reviewer's heading or with its owner - a solver's "UNSAT verdict" is mathematics
    r"|\b(?:executive|my|current|overall|final) verdict\b|(?:^|[\n#*]\s*)verdict\b", re.I)


def _verdict_text(prompt, data):
    """True (after writing the silence note) when the prompt opens with a publishability or referee verdict."""
    if not VERDICT_GATE:
        return False
    try:
        ws = sorted(set(m.group(0).lower() for m in VERDICT_WORDS.finditer(prompt[:400])))
        if not ws:
            return False
        _silence_note(data, "verdict_text:%s" % ",".join(ws[:4]))
        return True
    except Exception:
        return False


# A PROHIBITION IS NOT THE SUBJECT (30 Sep 2026, claude-825b1305, the owner's 14:09 order on getting knowledge to agents).
# Research-subagent tasks carry safety lines such as "never start any <book word> or bot process", and the word inside the
# prohibition silenced four quadratic-form research tasks at this gate while the one task without the line got three good
# cards. A match is skipped when the text before it in the same clause ends in a prohibition (never, don't, do not, must
# not, avoid) and at most three words. Measured on his last 3,000 prompts: all 117 that carry the vocabulary still fire;
# bare "no" and "not" are left out because "is there no access from the <book word> box" is a question about the book.
_PROHIBITION = re.compile(r"(?i)\b(never|don'?t|do not|must not|mustn'?t|avoid)\b(?:\s+\S+){0,3}\s*$")
_CLAUSE_BREAK = re.compile(r"[.;,!?\n]")


def _prohibited_mention(prompt, m):
    """True when the match sits inside a prohibition: 'never start any X', 'do not touch the X', 'avoid X'."""
    window = prompt[max(0, m.start() - 60):m.start()]
    cut = [b.end() for b in _CLAUSE_BREAK.finditer(window)]
    if cut:
        window = window[cut[-1]:]
    return bool(_PROHIBITION.search(window))


def _trading_text(prompt, data):
    """True (after writing the silence note) when the prompt carries the deployment's off-domain words outside a
    prohibition.
    Never raises."""
    if not TRADING_GATE:
        return False
    try:
        ws = sorted(set(m.group(0).lower() for m in TRADING_WORDS.finditer(prompt) if not _prohibited_mention(prompt, m)))
        if not ws:
            return False
        _silence_note(data, "offdomain_text:%s" % ",".join(ws[:4]))
        return True
    except Exception:
        return False


# GENERIC-ONLY MATCH (2026-09-24, campaign mb10d). "how close are we to solving the open problem?" and "ok i want that
# open problem full solved" pass the gate on "open problem" and drew three arbitrary "Open problem: ..." ledger cards
# each: the prompt names no object, and every served card met it only on research words (open, problem, solve,
# conjecture, theorem, proof, result ...). A block whose BEST card shares no prompt term outside those words is
# assembled from coincidence. Measured on the landing arm: it silences exactly those three junk blocks of his 126, and
# no useful block, no right card on the fresh or held-out banks, no false fire changes (every bank block shares a
# subject word). MATHBRAIN_GENERIC_ONLY=0 switches it off.
GENERIC_ONLY = os.environ.get("MATHBRAIN_GENERIC_ONLY", "1").strip() not in ("0", "off", "false", "")
GENERIC_RESEARCH_WORDS = ("open problem problems conjecture conjectures theorem theorems proof proofs prove proved "
                          "proving solve solved solving solution solutions result results question questions lemma "
                          "lemmas paper papers true false known famous literature full fully general case cases work "
                          "working method methods close closer "
                          # the words of his work orders about the research (cand17): with them the rule silences three
                          # more junk blocks ("ok can we use these method to solve a known open problem in the area",
                          # "tell the schduled taks working on this conjecture to converge on this problem", "please push
                          # all the reuslts to the open problem folder") and still nothing on any bank. Chosen by reading
                          # those blocks, so in-sample; his later traffic is the held-out check.
                          "area areas push pushed folder folders task tasks scheduled schedule converge better strong "
                          "stronger strongest strengthen fix fixed publishable publish published post posted github repo "
                          "email report list agents agent subagents box boxes run running test tests brain memory hook "
                          "hooks")
# FUNCTION WORDS COUNT AS GENERIC TOO (mb10d arm cand28, 24 Sep 21:2x). The retrieval tokenizer keeps "but", "now",
# "them", "still" ... as content terms, so "do that, but are the theorems strong now?" had two "subject" words left after
# the list above, and any card whose body says "but" defeated the silence: nine typed cards shifted the IDF enough for
# three textbook cards to be selected on it. English function words and his instruction verbs (his most frequent
# content terms that name no mathematics) are generic here - only here, never in the ranker. On the 68 hand-labelled
# useful and junk blocks: one more junk block silenced ("is this publishable? make sure that all the theorems are
# correct ..."), no useful block lost. "mean" and "even" are left out on purpose: both name mathematics.
GENERIC_FUNCTION_WORDS = (
    "but now them only like will again still right well before both once until after never already through "
    "actually though off too while really else because rather own enough less per yes no also just very much more "
    "most such each other into from with about than then there here where when which what who whom whose why how "
    "all any some many few can could would should might must shall may does did doing been being were has have had "
    "having this that these those its they their our your his her you make sure keep want get put see look find need "
    "take give try show tell think know say let please don't dont thing things stuff anything something "
    "everything way going done")
_GENERIC_TERMS = None



# DOCUMENT EXPANSION (mb10d campaign, 2026-09-24; math_brain.retrieval.EXPANSION). <store>.expansion.pkl gives a house card
# the mathematical words of its source memory that the card itself lacks; the prefilter indexes them and the ranker
# scores them. Every place this hook compares a prompt with a card's words - the block coverage floor, the top-card
# coverage gate, the generic-only silence - reads the same text through _card_index_text, or a card found by its
# expansion words would be silenced for not carrying them. MATHBRAIN_EXPANSION=0 switches it off (no load, plain text).
EXPANSION_ON = os.environ.get("MATHBRAIN_EXPANSION", "1").strip() not in ("0", "", "off", "false")
_EXPANSION_LOADED = False


def _load_expansion_once():
    """Marked loaded only when the sidecar gave cards: load_expansion returns 0 on an absent or unreadable file
    rather than raising, and a failed attempt is tried again by the next prompt (cand81)."""
    global _EXPANSION_LOADED
    if _EXPANSION_LOADED or not EXPANSION_ON:
        return
    try:
        from math_brain.retrieval import load_expansion
        if load_expansion(str(STORE_PATH) + ".expansion.pkl") > 0:
            _EXPANSION_LOADED = True
    except Exception:
        pass


def _card_index_text(node):
    try:
        import math_brain.retrieval as _R
        if getattr(_R, "EXPANSION", None) and node.id in _R.EXPANSION:
            return _R.index_text(node)
    except Exception:
        pass
    return (node.title or "") + " " + (node.body or "")

def _generic_only(prompt, chosen):
    """True when no chosen card shares a prompt term outside the generic research words. False on any doubt."""
    global _GENERIC_TERMS
    if not GENERIC_ONLY or not chosen:
        return False
    try:
        import math_brain.retrieval as _R
        terms = lambda t: set(_R._content_terms(_R.index_signals(t or "").lexical_tokens))
        if _GENERIC_TERMS is None:
            _GENERIC_TERMS = terms(GENERIC_RESEARCH_WORDS) | terms(GENERIC_FUNCTION_WORDS)
        pt = terms(prompt) - _GENERIC_TERMS
        if not pt:
            return True
        for h in chosen:
            if pt & terms(_card_index_text(h.node)):
                return False
        return True
    except Exception:
        return False


# LEAD CARDS ON LONG PROMPTS MUST BE NAMED (2026-09-24, campaign mb10d). Three store populations are leads, not results:
# the Wikipedia-harvested concept cards ("Model theory", "NP-completeness", "Bernstein inequalities"), the Codex ledger's
# "Attempt on ..." cards and its "Open problem: ..." cards. On his 480 past prompts the landing arm served 30 junk-block
# cards from the harvest against 7 in useful blocks, 13 against 0 from the attempts, 19 against 7 from the problem cards:
# on a long prompt - a pasted referee report, an operations order, a proof audit - some incidental word always matches
# one of them. On a prompt of 80 or more words (LaTeX command names not counted) such a card is served only when the
# prompt NAMES it: at least half of its title's IDF weight is carried by the prompt (house acronyms expanded, so "FEI"
# names "Fourier entropy versus total influence"). Simulated on the landing arm's own blocks
# (tools/population_rule_sim.py): 10 junk blocks silenced and 47 junk cards dropped; no useful block silenced, 7
# lead cards dropped from useful blocks that keep their on-subject cards; no bank question is that long, so every bank
# is untouched. Pinned citations are never dropped. MATHBRAIN_LEAD_NAMED=0 switches it off.
LEAD_NAMED = os.environ.get("MATHBRAIN_LEAD_NAMED", "1").strip() not in ("0", "off", "false", "")
# Which populations count as leads: MATHBRAIN_LEAD_POPULATIONS (bundle id prefixes, comma separated).
LEAD_POPULATIONS = tuple(p.strip() for p in os.environ.get(
    "MATHBRAIN_LEAD_POPULATIONS", "population.wikipedia-harvest,population.probability-codex-ledger-attempts,"
    "population.probability-codex-ledger-problems").split(",") if p.strip())
LEAD_MIN_WORDS = int(os.environ.get("MATHBRAIN_LEAD_MIN_WORDS", "80") or 80)
LEAD_MIN_COV = float(os.environ.get("MATHBRAIN_LEAD_MIN_COV", "0.5") or 0.5)
def _store_n():
    return _lexdf()[1]


# SLUG TITLES ARE LEADS TOO (MATHBRAIN_LEAD_SLUG, campaign mb10d, 25 Sep, arm cand34). A card whose title is its bundle
# entry key spelled out ("audit verdict thm1 thm2 pass" for rs-audit-verdict-thm1-thm2-pass) tells a reader nothing, and on
# his long prompts - a token table, a filesystem-audit role prompt, scheduled-task instructions - its process words
# ("audit", "verification", "report", "transfer") are what matched: on the landed hook's 73 blocks over his 483 past
# prompts such cards sat in 15, none of them about their own program. Today only the Rademacher-singularity bundles have
# such titles (275 of 325 current cards). They join the lead-named rule below; the rule reads the title's form, not the
# family, so a later bundle titled the same way is covered. MATHBRAIN_LEAD_KEY_TITLE=0 (or the older MATHBRAIN_LEAD_SLUG=0)
# switches it off.
LEAD_KEY_TITLE = os.environ.get("MATHBRAIN_LEAD_KEY_TITLE", os.environ.get("MATHBRAIN_LEAD_SLUG", "1")).strip() not in ("0", "off", "false", "")
LEAD_SLUG = LEAD_KEY_TITLE


def _key_titled(node):
    """True when the card's title is its bundle entry key in words (lower case, the key's family prefix optional)."""
    try:
        meta = getattr(node, "metadata", None) or {}
        key = str(meta.get("bundle_entry_key") or "")
        title = str(getattr(node, "title", "") or "")
        if not key or not title or title != title.lower():
            return False
        kw = re.sub(r"[^a-z0-9]+", " ", key.lower()).split()
        tw = re.sub(r"[^a-z0-9]+", " ", title.lower()).split()
        return bool(tw) and (kw == tw or kw[1:] == tw)
    except Exception:
        return False


_slug_titled = _key_titled


def _lead_filter(prompt, chosen, pinned=()):
    """chosen without the lead cards a long prompt does not name. Returns chosen unchanged on any doubt."""
    if not LEAD_NAMED or not chosen:
        return chosen
    try:
        if len(_NONMATH_WORD.findall(_NONMATH_LATEX.sub(" ", prompt))) < LEAD_MIN_WORDS:
            return chosen
        df, n = _store_df(), _store_n()
        if not df or not n:
            return chosen
        import math as _m
        import math_brain.retrieval as _R
        terms = lambda t: set(_R._content_terms(_R.index_signals(t or "").lexical_tokens))
        pt = terms(_expand_aliases(prompt))
        idf = lambda t: _m.log1p(n / (1.0 + df.get(t, 0)))
        keep = []
        for h in chosen:
            src = str((getattr(h.node, "metadata", None) or {}).get("bundle_id") or "")
            if h.node.id in pinned or not (any(src.startswith(p) for p in LEAD_POPULATIONS)
                                           or (LEAD_KEY_TITLE and _key_titled(h.node))):
                keep.append(h)
                continue
            tt = terms(h.node.title or "")
            w = sum(idf(t) for t in tt)
            if w > 0 and sum(idf(t) for t in tt if t in pt) / w >= LEAD_MIN_COV:
                keep.append(h)
        return keep
    except Exception:
        return chosen


# A NAMED RESULT OF A WIDER KIND OPENS THE GATE WHEN THE STORE HOLDS A CARD ABOUT IT (MATHBRAIN_GATE_NAMED_KIND, default on;
# arm cand71). "What does the Fredholm alternative say ..." was refused here: "alternative" is no anchor. A capitalised name
# followed by one of the kinds the named-result extractor learned in cand51 (alternative, series, property, test, iteration,
# method, bound, transform, calculus, expansion, duality, conjecture, trick) opens the gate only when a card's title opens with
# that name and kind and its head holds at most three more words. Over his 17,944 archived prompts it admits none the gate
# refused (a looser cut, name and kind anywhere in a title, admitted 72, nearly all junk); no probe breaks.
GATE_NAMED_KIND = os.environ.get("MATHBRAIN_GATE_NAMED_KIND", "1").strip() not in ("0", "off", "false", "")
_GATE_NEW_KINDS = re.compile(r"^(?:alternatives?|series|propert(?:y|ies)|(?:[A-Z]-)?tests?|iterations?|methods?|bounds?|"
                             r"transforms?|calculus|expansions?|duality|conjectures?|tricks?)$", re.I)
_GATE_TITLE_HEADS = None


def _gate_fold(text):
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower().replace("'s", "").replace("’s", "")).strip()


def _named_kind_card(prompt):
    """True when the prompt names a result of a wider kind and the store holds a card about exactly that result."""
    global _GATE_TITLE_HEADS
    try:
        found = [(m.group(1), m.group(2)) for m in _NAMED_RESULT_RE.finditer(prompt or "")
                 if m.group(1) and m.group(2) and _GATE_NEW_KINDS.match(m.group(2))]
        if not found:
            return False
        if _GATE_TITLE_HEADS is None:
            import sqlite3
            con = sqlite3.connect("file:%s?mode=ro" % str(STORE_PATH).replace("\\", "/"), uri=True)
            try:
                _GATE_TITLE_HEADS = [_gate_fold(re.split(r"[:(\[]", t, maxsplit=1)[0])
                                     for (t,) in con.execute("select title from nodes where title is not null")]
            finally:
                con.close()
        for name, kind in found:
            if name.isupper():
                continue
            nm, stem = _gate_fold(name), _gate_fold(kind)[:5]
            if len(nm) < 3:
                continue
            for h in _GATE_TITLE_HEADS:
                hh = h[4:] if h.startswith("the ") else h
                if hh.startswith(nm + " " + stem) and len(hh.split()) <= len(nm.split()) + 4:
                    return True
        return False
    except Exception:
        return False


def _math_gate(prompt):
    """The legacy gate, then - only when MATHBRAIN_PROSE_GATE=1 - a bounded prose route.

    Every decision the old gate made is preserved: a legacy positive returns True before the new
    code runs, so this can only ADD fires, never remove one. The new route needs a weak technical
    family AND a reasoning cue AND two distinct signals in total, and it suppresses the pair
    (action word + artifact noun) that made candidate v1 admit a report-renaming request.
    """
    if _legacy_math_gate(prompt):
        if (ADMIN_SUPPRESS and TECHNICAL_PROSE_ACTION.search(prompt)
                and TECHNICAL_PROSE_ARTIFACT.search(prompt)):
            return False
        return True
    if GATE_NAMED_KIND and _named_kind_card(prompt):
        return True
    if not PROSE_GATE:
        return False
    extra = sum(bool(pattern.search(prompt)) for pattern in TECHNICAL_PROSE_FAMILIES)
    if not extra or not TECHNICAL_PROSE_REASONING.search(prompt):
        return False
    if TECHNICAL_PROSE_ACTION.search(prompt) and TECHNICAL_PROSE_ARTIFACT.search(prompt):
        return False
    existing = set(m.group(0).lower() for m in MATH_ANCHORS.finditer(prompt))
    existing |= set(m.group(0).lower() for m in EXTRA_ANCHORS.finditer(prompt))
    existing |= set(m.group(0).lower() for m in GREEK_ANCHORS.finditer(prompt))
    return len(existing) + extra >= 2


# LETTER EXPONENTS (MATHBRAIN_EXP_LETTER, default on; campaign mb10d, 25 Sep, arm cand36). ASCII_MATH_RE takes an exponent
# only from [0-9nkpq] and STRUCTURAL_MATH_RE from [0-9nkpqd], so "3^m + (-1)^m" carried no notation this gate could see and
# the house question on the NAE cycle count was refused. Any single letter now counts, as notation beside a word anchor and
# as a structural notation ("x^y" alone stays refused: one notation, no anchor). Measured over his 17,630 distinct archived
# prompts (campaign tools/measure_gate_unicode.py, class re_exp_letter): 1,765 carry one, none newly admitted; no probe
# breaks; on every bank one question changes - that one, silent to its own theorem card first. 0 switches it off.
EXP_LETTER = os.environ.get("MATHBRAIN_EXP_LETTER", "1").strip() not in ("0", "off", "false", "")
EXP_LETTER_RE = re.compile(r"(?:(?<![A-Za-z0-9_])[A-Za-z0-9]|\))\^\{?[a-z]\}?(?![A-Za-z0-9_])")


def _legacy_math_gate(prompt):
    """One STRONG anchor (names, theorem-class words), two weak anchors, one weak
    anchor plus notation, or - new - two distinct STRUCTURAL notations with no word
    anchor at all. That last path exists because mathematics written in symbols was
    invisible to this gate: a Cauchy-Schwarz question that never says "Cauchy", and the
    bench's two silent probes, all carried zero word anchors. It deliberately does NOT
    use ASCII_MATH_RE, which contains <=, >=, != and -> and would light up on code.
    The gate is a cost filter; retrieval's abstention thresholds remain the correctness
    gate."""
    if STRONG_ANCHORS.search(prompt) or STRONG_ANCHORS_2.search(prompt):
        return True
    if ANCHORS_3_ON and STRONG_ANCHORS_3.search(prompt):
        return True
    if PROGRAM_ANCHORS_ON and (PROGRAM_ANCHORS.search(prompt) or PROGRAM_ANCHORS_WORDS.search(prompt)):
        return True
    if ANCHORS_5_ON and STRONG_ANCHORS_5.search(prompt):
        return True
    if ANCHORS_POSSESSIVE_ON and STRONG_ANCHORS_POSSESSIVE.search(prompt):
        return True
    if ANCHORS_4_ON and STRONG_ANCHORS_4.search(prompt):
        return True
    anchors = set(m.group(0).lower() for m in MATH_ANCHORS.finditer(prompt))
    anchors |= set(m.group(0).lower() for m in EXTRA_ANCHORS.finditer(prompt))
    anchors |= set(m.group(0).lower() for m in GREEK_ANCHORS.finditer(prompt))
    anchors |= set(m.group(0).lower() for m in WEAK_ANCHORS_2.finditer(prompt))
    if ANCHORS_4_ON:
        anchors |= set(m.group(0).lower() for m in WEAK_ANCHORS_4.finditer(prompt))
    if len(anchors) >= 2:
        return True
    # DERIVED ANCHORS (MATHBRAIN_DERIVED_ANCHORS, default 0 = OFF; 2026-09-14). The anchor lists above are
    # hand-written, so a question can be plainly mathematical and carry none of their words: measured over
    # the 178-question answerable bank, this gate refuses "Which finite graphs are planar, in terms of
    # forbidden subdivisions?", "Asymptotics of the number of primes up to x.", Jensen's inequality written
    # in symbols, and Menger's theorem stated in words - all four of which the store holds cards for.
    # `math_anchor_vocab.json` is a 301-word list DERIVED from the corpus and never from the bank: words of
    # four or more letters in at least 8 of the store's own node titles and in at most 0.2 pct of the
    # memory documents (the bm25 stopword list subtracted by hand, because the negative corpus strips
    # stopwords and therefore cannot see them - the first cut of this list "proved" that "that", "there"
    # and "with" were mathematical vocabulary).
    # MEASURED, AND REFUSED - keep it off. One such word admits 4 of the 5 refused answerable questions and
    # ZERO of the 40 absent-target ones, and the AGGREGATE on the hook-path bank looks like a clear win:
    #     q_content served 72/89 -> 73/89, rank-1 53 -> 54, SILENT 9 -> 5, false fire 27/40 unchanged.
    # The per-question diff says the opposite. Of the four newly admitted, ONE becomes a correct answer
    # (jensen, served "Jensen's Inequality" first) and THREE become confident wrong cards: Menger answered
    # with "Ore's Theorem on Hamiltonian Cycles", Kuratowski with "Robertson-Seymour", the prime number
    # theorem with "Infinitude of Primes". Those are the dangerous kind of wrong - a plausible neighbour
    # that reads as the answer. Trading four silences for one answer and three of those runs straight
    # against his 2026-09-02 order to make the brain fire LESS and be surer when it fires, so the silence
    # is the better behaviour and this stays off.
    # THE COST IN PRODUCTION, separately: over the 33,970 real prompts in the archive, 43.1 pct pass this
    # gate today and 912 more would - a 6 pct rise in admissions, some plainly not questions ("add that
    # code can be found here github..." carries "hamiltonian" from a paper title).
    # If someone revisits this: the aggregate is what fooled me for ten minutes. Diff the questions.
    if DERIVED_ANCHORS and _derived_anchor_vocab() and any(
            w in _derived_anchor_vocab() for w in re.findall(r"[a-z]{4,}", prompt.lower())):
        return True
    # An enumeration word opens only the anchor-plus-NOTATION path, never the two-anchor one:
    # "enumerate the files ... rename the lattice config" carries two of them, and "the lattice
    # config is missing a field" carries one plus the polysemous "field" - both must stay silent
    # (caught by hand-written negatives at 23:0x, not by the corpus, which held no such prompt).
    # The H1 question carries "self-avoiding"/"lattice" AND "<=", so it passes here.
    if len(anchors) >= 1 or ENUMERATION_ANCHORS.search(prompt):
        return (LATEX_RE.search(prompt) is not None
                or ASCII_MATH_RE.search(prompt) is not None
                or (EXP_LETTER and EXP_LETTER_RE.search(prompt) is not None))
    _struct = set(m.group(0) for m in STRUCTURAL_MATH_RE.finditer(prompt))
    if EXP_LETTER:
        _struct |= set(m.group(0) for m in EXP_LETTER_RE.finditer(prompt))
    return len(_struct) >= 2


def _install_global_idf():
    """Give the ranker store-wide document frequencies (see math_brain.retrieval.GLOBAL_LEXICAL_DF).

    Reads <store>.lexdf.pkl, written by math_prefilter.build beside the prefilter index. Missing, unreadable or switched off
    (MATHBRAIN_GLOBAL_IDF=0) leaves the ranker on pool statistics exactly as before. Never raises."""
    try:
        import pickle
        import math_brain.retrieval as _R
        # Default 0 since the real-traffic shadow of 24 Sep: store-wide IDF (2) gained +3 rank-1 of 64 held-out
        # questions, all of it from choosing different cards, and the same mechanism served junk on 21 of his own
        # prompts - words the whole pool shares ("theorems", "problems") got weight and cleared the support floor,
        # and English words rare among short statements ("neither", "nor") lifted the shortest card holding them.
        mode = int(os.environ.get("MATHBRAIN_GLOBAL_IDF", "0") or 0)
        if mode not in (1, 2):
            _R.GLOBAL_LEXICAL_DF = None
            return
        path = str(STORE_PATH) + ".lexdf.pkl"
        if not os.path.exists(path):
            _R.GLOBAL_LEXICAL_DF = None
            return
        with open(path, "rb") as fh:
            d = pickle.load(fh)
        _R.GLOBAL_LEXICAL_DF = d["df"]
        _R.GLOBAL_LEXICAL_N = int(d["n"])
        _R.GLOBAL_LEXICAL_MODE = mode
    except Exception:
        try:
            import math_brain.retrieval as _R
            _R.GLOBAL_LEXICAL_DF = None
        except Exception:
            pass


def _node_meta(node):
    return node.metadata if isinstance(node.metadata, dict) else {}


_REVIEW_KINDS = ("discovery_report", "human_proof_review")


def _review_reads(claim_ids, store_path=None):
    """claim id -> the latest sound recorded read of it (tools/record_review.py), for the cards about to be shown.

    Read after the selection, for those cards only, through the evidence table's claim index (29 Sep, account04/B10 r27,
    B10-R45-REVIEW-SCAN-COMPLEXITY-OMISSION-20260929: the read used to decode every evidence row before ranking, to
    annotate at most four cards). Each row is judged on its own, as the store's evidence_for judges it - a JSON object
    whose id, claim and time are the row's and whose digest is the row's digest - and its `passed` must be a real
    boolean (account02/B19 auto57-58, B19-R69-TRUST-HINT-EVIDENCE-INTEGRITY-BYPASS-20260929 and
    B19-R69-NONOBJECT-EVIDENCE-SILENCES-MATH-HOOK-20260929): until then a payload edited without its digest was shown
    as a human's pass, the string "false" read as a pass, and a payload that was a JSON list or null raised past the
    row's guard and silenced the whole hook. A row that fails is skipped, so its card reads unverified; nothing here
    changes which cards are shown, or silences the block. Only the three values the tag uses are kept (account04/B17
    r15: the reviewer's name and the free-text summary were copied for every row). The record is shown beside the hint
    and does not change the tag's verdict: an agent's read is a discovery report, not an authority (the store's own
    promotion rules say so), and a human's single read makes a claim provisional at most."""
    ids = sorted({str(i) for i in claim_ids if i})
    if not ids:
        return {}
    try:
        import sqlite3
        from math_brain.models import Evidence, EvidenceKind
        conn = sqlite3.connect("file:%s?mode=ro" % str(store_path or STORE_PATH).replace("\\", "/"), uri=True,
                               timeout=2)
    except Exception:
        return {}
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, claim_id, digest, created_at, payload_json FROM evidence WHERE claim_id IN (%s) "
            "ORDER BY created_at, id" % ",".join("?" * len(ids)), ids).fetchall()
    except Exception:
        rows = []
    finally:
        try:
            conn.close()
        except Exception:
            pass
    out = {}
    for row in rows:
        try:
            payload = json.loads(row["payload_json"])
            if not isinstance(payload, dict) or payload.get("kind") not in _REVIEW_KINDS \
                    or type(payload.get("passed")) is not bool:
                continue
            typed = dict(payload)
            typed["kind"] = EvidenceKind(typed["kind"])
            typed["limitations"] = tuple(typed["limitations"])
            ev = Evidence(**typed)
            if (ev.id, ev.claim_id, ev.created_at, ev.digest()) != (row["id"], row["claim_id"], row["created_at"],
                                                                      row["digest"]):
                continue
        except Exception:
            continue
        out[row["claim_id"]] = {"when": str(row["created_at"] or "")[:10], "passed": ev.passed,
                                "human": payload["kind"] == "human_proof_review"}
    return out


def _claim_parts(claim_ids, store_path=None):
    """claim id -> (statement, assumptions) from the claims table, for the cards about to be shown.

    An overlay claim's node lists its assumptions as bare lines after the statement, so the renderer took them for the
    statement's tail and cut them first: on the gate's 218 bank prompts 90 of 466 served cards lost a hypothesis whose
    words appear nowhere else on the card, and 29 through the view (1000-page report D10-T01, 30 Sep 2026; report
    audits/reports/2026/09/30/r1000_d_tests). The claim row keeps the two apart, and _claim_view renders the card
    from it. A row is used only when it is sound, as _review_reads judges evidence: a
    JSON object that builds a Claim whose id and digest are the row's. Anything else is skipped and that card renders
    as it did before; nothing here changes which cards are shown."""
    ids = sorted({str(i) for i in claim_ids if i})
    if not ids:
        return {}
    try:
        import sqlite3
        from math_brain.models import Claim, ClaimKind, RecordStatus
        conn = sqlite3.connect("file:%s?mode=ro" % str(store_path or STORE_PATH).replace("\\", "/"), uri=True,
                               timeout=2)
    except Exception:
        return {}
    try:
        rows = conn.execute("SELECT id, digest, payload_json FROM claims WHERE id IN (%s)" % ",".join("?" * len(ids)),
                            ids).fetchall()
    except Exception:
        rows = []
    finally:
        try:
            conn.close()
        except Exception:
            pass
    out = {}
    for cid, digest, raw in rows:
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                continue
            typed = dict(payload)
            typed["kind"] = ClaimKind(typed["kind"])
            typed["status"] = RecordStatus(typed["status"])
            typed["assumptions"] = tuple(typed["assumptions"])
            claim = Claim(**typed)
            if (claim.id, claim.digest()) != (cid, digest):
                continue
        except Exception:
            continue
        out[cid] = (claim.statement, claim.assumptions)
    return out


def _claim_view(node, parts):
    """The node to render: an overlay claim through its own statement and assumptions (math_brain.rendering.claim_view),
    anything else as it is. Without the build, or on any error, the node itself."""
    if not parts:
        return node
    try:
        from math_brain.rendering import claim_view
        return claim_view(node, parts[0], parts[1])
    except Exception:
        return node


def _trust_tag(meta, read=None):
    """How far to trust one served node, read from its own metadata. Nothing in the store carries an
    independent verification event (verification_events was empty on 2026-09-02, and every harvested
    draft has statement_fidelity_reviewed=0), so the tag says what KIND of thing this is and where it
    came from - never that it is right. Owner order 2026-09-02: a note can mean something good or bad,
    not necessarily good; the brain gives a hint. Fail-safe: any error -> 'unverified'.

    `read`, when given, is the claim's latest recorded read (see _review_reads): it is appended as a
    dated fact - who read it and what they found - and the closing word stays 'unverified' unless the
    read was a human's, in which case it reads 'provisional'; the store promotes to verified only on a
    second, independent route.

    TERSE SINCE 2026-09-13, and deliberately back to his own words. The owner's 09-02 order describes
    this line as saying "what kind of thing it is (theorem with a proof text, concept lead, harvested
    draft, unreviewed)" - a kind-tag. It had grown into a caution paragraph averaging 160 characters a
    card, and the caution it carried ("read the full source and assumptions before applying") already
    stands in the block header, once, where it is read once. Measured on the 1,000 captured blocks of
    the 09-12 A/B: hints were 14.2 pct of every block against 49.2 pct for the mathematics itself. The
    meanings are all still here - kind, proof on file or not, draft or curated, statement reviewed or
    not, verified or not, and any recorded read - in about a third of the characters."""
    try:
        ck = str(meta.get("claim_kind") or meta.get("kind") or "").lower()
        parts = []
        if ck in ("theorem", "lemma", "proposition", "corollary"):
            parts.append(ck + (" +proof" if meta.get("proof_text") else ", no proof"))
        elif ck:
            parts.append(ck + " lead")
        parts.append("draft" if (meta.get("draft") or meta.get("bundle_id")) else "curated")
        if not meta.get("statement_fidelity_reviewed"):
            parts.append("unreviewed")
        # a revision that declared its own status (retracted, refuted, gap found, verified ...) says so here, apart
        # from the closing word, which stays the authenticated one: a declared retraction must not reach a session as
        # a plain 'unverified', and a declared 'verified' is not a verification (30 Sep, account02/B11-R79)
        declared = meta.get("declared_status")
        if isinstance(declared, str) and declared.strip() and declared.strip().lower() != "unverified":
            parts.append("declared " + declared.strip().lower().replace("_", " "))
        if read:
            verdict = "no gap" if read.get("passed") else "GAP FOUND"
            who = "human" if read.get("human") else "agent"
            parts.append("%s read %s: %s" % (who, read.get("when") or "?", verdict))
            parts.append("provisional" if (read.get("human") and read.get("passed")) else "unverified")
        else:
            parts.append("unverified")
        return "; ".join(parts)
    except Exception:
        return "unverified"


# ORIGIN IN THE HINT (2026-09-24, campaign mb10d). T16 (claude-34f0942b, 24 Sep) handed research agents the store's
# program frontier: a house claim later found false was printed "[theorem]" in the same format as a published theorem,
# and the plans built on it - a list of our own results carries their errors with the list's authority. So a card that
# is this project's own record says so in its hint: "ours, unrefereed". Read from provenance, never from the subject:
# the transported memory cards and the scar layer, the typed refinements, the QFORM record cards, research.* deposits,
# ledger cards carrying "(our id ...)", and the Codex ledger's "Attempt on ..." cards. Literature, textbook and harvested
# cards are untouched. It says where the card came from, not whether the mathematics is right (a Lean reproduction of a
# published theorem is still our record of it). MATHBRAIN_ORIGIN_HINT=0 switches it off.
ORIGIN_HINT = os.environ.get("MATHBRAIN_ORIGIN_HINT", "1").strip() not in ("0", "off", "false", "")
# "owner result; ..." is how the campaign ledger marks his own settled problems (Aldous-Fill 4.48, Bradley Q5, the
# maximal Kemeny constant); "T6 correction ..." and "round-5 correction of claim_..." are our corrections of our own
# cards. An agent session named in a provenance is NOT the test: 96 Wikipedia cards carry "formula repair 2026-09-23
# (claude-96cda5f6)" and are literature an agent tidied. Audited over every current card of the store on 24 Sep 20:4x
# (live0924Q): every harvest, Wikipedia, ProofWiki and literature card untagged, the textbook "growth-b1" cards
# untagged, the open problem statements "as read from the source" untagged.
# MATHBRAIN_HOUSE_PROVENANCE adds a deployment's own provenance prefixes (a regular expression, matched at the start).
_HOUSE_PROV_EXTRA = os.environ.get("MATHBRAIN_HOUSE_PROVENANCE", "").strip()
_HOUSE_PROV_RE = re.compile(r"^(transported from the main brain memory|scar layer|typed (from|refinement)|owner result|"
                            r"T\d+ correction|round-\d+ correction"
                            + ("|" + _HOUSE_PROV_EXTRA if _HOUSE_PROV_EXTRA else "") + ")", re.I)


def _is_house(node, meta=None):
    """True for this project's own records (see ORIGIN_HINT). False on any doubt."""
    try:
        meta = meta if meta is not None else _node_meta(node)
        # a harvested statement (math:harvest.arxiv.*, harvest.wikipedia:...) is literature, whatever else it carries:
        # _is_campaign counts the arXiv harvest as a campaign deposit when MATHBRAIN_HARVEST_CAP is on (for slot
        # capping), so it is not a provenance test by itself - the first cut of this rule tagged three arXiv cards ours
        lid = str(meta.get("logical_id") or "")
        if "harvest." in lid:
            return False
        # a typed card whose statement is someone else's published result (add_cards.py "origin": "literature" - the
        # Erdos-Sos card: proved by GPT-6 Astra with Adamczewski, verified by Bloom; our memory reproduced it in Lean)
        if str(meta.get("origin") or "").strip().lower() == "literature":
            return False
        # the FPP lemmas rescued from orphan status on 09-13 are the FPP campaign's own mathematics
        # (memory eleven_of_the_orphans_i_called_duplicates_are_original_mathematics)
        if "orphan-rescue" in lid or "orphan-rescue" in str(meta.get("bundle_id") or ""):
            return True
        title = str(node.title or "")
        if "(our id " in title or title.startswith("Attempt on "):
            return True
        if meta.get("campaign") or _HOUSE_PROV_RE.search(str(meta.get("provenance") or "")):
            return True
        return _is_campaign(node)
    except Exception:
        return False


def _origin_prefix(node, meta):
    return "ours, unrefereed; " if ORIGIN_HINT and _is_house(node, meta) else ""


def _is_campaign(node):
    """A private research campaign's working deposits, by their own provenance.

    Read from metadata rather than matched against titles: three successive title
    patterns each missed a tier of deposits that carried no campaign word at all.
    """
    meta = _node_meta(node)
    # 2026-09-08: the subject-word match on domain / logical_id / title called 717 nodes a campaign deposit while only
    # 344 live in a research.* namespace - the rest were ordinary curated mathematics about percolation and FPP, so
    # with MAX_CAMPAIGN_SLOTS = 1 a percolation question could be answered with at most one percolation node (the
    # Harris-Kesten p_c = 1/2 node was suppressed on the 09-07 recall bank). Campaign membership is PROVENANCE:
    # a research.* logical id, a campaign source or bundle, or an UN-NAMESPACED working deposit whose domain is the
    # campaign's subject (the FPP lemma tier carries no logical id at all - measured 2026-09-08: 373 such nodes).
    # A curated node with its own logical id (math:probability.percolation:..., harvest.wikipedia:...) is never a
    # campaign deposit just because its subject is percolation.
    lid = str(meta.get("logical_id") or "")
    if re.search(r"(?:^|[:.])research\.", lid):
        return True
    # 2026-09-12 (sandbox measurement, default OFF): the arXiv harvest lanes (math:harvest.arxiv.*) helped the fresh bank
    # at 476 nodes (0.764 -> 0.787 unnamed served) and crowded it at 5,666 (0.742). Under MATHBRAIN_HARVEST_CAP=1 a
    # harvest.arxiv node is capped like a campaign deposit: one slot unless the ranker's own top hit is one.
    if HARVEST_CAP and "harvest.arxiv" in lid:
        return True
    if re.search(r"campaign", str(meta.get("source") or ""), re.IGNORECASE):
        return True
    if not lid and CAMPAIGN_PAT.search(str(meta.get("domain") or "")):
        return True
    return False


#: an encyclopedic opening - "In mathematics, X is ...", "Hoeffding's inequality is ..."
#: - as opposed to a node whose body states the theorem.
LEDE_RE = re.compile(
    r"^(in mathematics|in probability|in the mathematical|in statistics|"
    r"in functional analysis|in measure theory|in combinatorics|in geometry|"
    # 2026-09-03: the harvest opens 184 of its 704 Wikipedia ledes with a field the list above
    # never named ("In topology,", "In number theory,", "In computational complexity theory,",
    # "In the mathematics of evolving systems,"), so the same-title swap left those openings in
    # the block above their statement siblings. An "In <field>, ..." opening whose field names a
    # discipline is a lede; "In any inner product space, ..." is a statement and stays one (the
    # first, unrestricted form flagged 16 authored statements - measured before landing).
    r"in (?:the )?[a-z' -]{0,40}?(?:theory|analysis|algebra|geometry|topology|logic|calculus|"
    r"mathematics|probability|statistics|combinatorics|computability|complexity|physics|"
    r"mechanics|science|optimi[sz]ation|learning)(?: of [a-z' -]{1,40})?, |"
    r"[A-Z][\w' -]{2,40} (is|are|was|refers to) )",
    re.IGNORECASE,
)


def _is_lede(node):
    """True when the body reads like an encyclopedia opening rather than a statement.

    Used ONLY to choose between nodes that share a normalised title, so the subject is
    identical either way and the choice cannot change WHICH theorem is served - only
    whether the block carries its statement or its opening paragraph.
    """
    return bool(LEDE_RE.match(" ".join((node.body or "").split())))


# PRECISION CUE - TRIED AND REJECTED ON MEASUREMENT 2026-09-13 (goal 30h item 3). Kept here, inert at
# MATHBRAIN_PRECISION_BOOST=0, so the next reader gets the numbers instead of re-deriving the idea.
# WHAT IT BOUGHT, at boost 2 on the value-in-block bank (100 keyed items, the instrument built for "did the stored
# value reach the served block"): campaign vib 16 -> 17 and ALL 96 -> 97, served unchanged at 97 - one item gained,
# none lost. WHAT IT COST, the same boost on the fresh bank, matched pair, diffed per item: the unnamed Ramsey item
# went from served and rank-1 to a MISS (unnamed served 72 -> 71, rank-1 54 -> 53, silent 9 -> 10; named, absent
# fires and the 100 long prompts unchanged). It trades a famous-theorem retrieval for a campaign value, so it does
# not land. The diagnosis it came from still stands and is still open: on l1_c10 both cards carrying 1.2579 DO clear
# the prefilter floor (21.9 and 21.7 against 17.5, ranks 5 and 7 of 300), so the drop is in RANKING - but several
# same-campaign siblings also carry decimals, the boost lifts them equally, and the value never reaches the block at
# boost 1, 2 or 4. The finer signal that question needs is the phrase it names ("absolute constant C"), which the
# symbol channel scores at an effective 0.135. Controls for the cue itself: lanes/m7_brain/precision_cue_controls.py
# (22/22). Do not flip this dial without a pair that gains on BOTH banks.
#
# The design, for the record (behind MATHBRAIN_PRECISION_BOOST - default 0).
# A question that asks for a VALUE at a stated precision can only be answered by a card that CARRIES a numeral
# at that precision. Traced on l1_c10 ("the rooted NAE cycle at n = 13 forces the absolute constant C to be at
# least what value (to three decimals)?"): the two cards carrying 1.2579 sat at prefilter ranks 6 and 7 while the
# three slots went to same-campaign siblings scoring higher on words alone - and NONE of those siblings contains a
# decimal numeral at all, so no reader could have answered from the block. This is causal: it reads the question's
# own request and the card's own text, never the answer. It is a WEIGHT, not a gate - it reorders the candidates
# retrieval already admitted and can never introduce a card or open a silent prompt.
_ASKS_VALUE = re.compile(
    r"(?:to\s+(?:one|two|three|four|five|six|\d+)\s+decimals?"
    r"|\bdecimal places?\b"
    r"|\b(?:what|which)\s+(?:is|are|was|were)?\s*(?:the\s+)?"
    r"(?:value|number|constant|bound|exponent|ratio|probability|threshold|limit|mean|variance|count)\b"
    r"|\bat\s+least\s+what\b|\bat\s+most\s+what\b"
    r"|\bhow\s+(?:many|much|large|small)\b"
    r"|\b(?:compute|evaluate|give|state)\b[^?\n]{0,40}\b(?:value|constant|number|bound)\b)", re.IGNORECASE)
_WORD_DIGITS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}
_DEC_RE = re.compile(r"\d+\.(\d+)")


def _asked_precision(prompt):
    """Decimal places the question asks for, or 0 when it asks for a value without naming a precision,
    or None when it is not asking for a value at all."""
    if not _ASKS_VALUE.search(prompt or ""):
        return None
    m = re.search(r"to\s+(one|two|three|four|five|six|\d+)\s+decimals?", prompt or "", re.IGNORECASE)
    if m:
        tok = m.group(1).lower()
        return _WORD_DIGITS.get(tok, int(tok) if tok.isdigit() else 0)
    return 0


def _precision_boost():
    try:
        return float(os.environ.get("MATHBRAIN_PRECISION_BOOST", "0") or 0.0)
    except Exception:
        return 0.0


def _carries_precision(node, places):
    """True when the card's own text holds a numeral at the requested precision (any decimal when places is 0)."""
    text = (getattr(node, "title", "") or "") + " " + (getattr(node, "body", "") or "")
    for frac in _DEC_RE.findall(text[:6000]):
        if len(frac) >= max(places, 1):
            return True
    return False


# USE-GROWN LAYER (26 Sep 2026, claude-825b1305; his 15:5x order: the brain live, "the edges growing and learning,
# including for math brain"). ~/.claude/brain_graph/bin/math_use.py keeps each claim's use history (every serve, every
# explicit fetch) and which claims were served into the same session; _rank_score tilts a candidate by
# (1 + MATHBRAIN_USE_WEIGHT x its use score in 0..1), half its node size (ACT-R level of its uses), half its strongest
# co-activation edge to a claim this session already has. A tilt among close candidates, never an override: at the
# default 0.15 a candidate gains at most 15 per cent of its own score. MATHBRAIN_USE_WEIGHT=0 switches it off. Every
# serve is appended to ~/.claude/brain_graph/live/math_serves.jsonl, so the layer grows hourly (run_graph.py capture).
MATH_USE_WEIGHT = float(os.environ.get("MATHBRAIN_USE_WEIGHT", "0.15") or 0.0)
_USE_CTX = {"sid": "", "served": set(), "cache": {}}


def _use_module():
    p = os.path.join(os.path.expanduser("~"), ".claude", "brain_graph", "bin")
    if p not in sys.path:
        sys.path.insert(0, p)
    import math_use
    return math_use


def _use_begin(session_id):
    _USE_CTX.update(sid=session_id or "", served=set(), cache={})
    if not session_id or MATH_USE_WEIGHT <= 0:
        return
    try:
        with io.open(_session_shard(session_id), encoding="utf-8") as f:
            _USE_CTX["served"] = set(json.load(f).get("ids") or [])
    except Exception:
        pass


def _use_score(cid):
    if MATH_USE_WEIGHT <= 0 or not cid:
        return 0.0
    c = _USE_CTX["cache"]
    if cid not in c:
        try:
            c[cid] = _use_module().scores(_USE_CTX["sid"], [cid], _USE_CTX["served"]).get(cid, 0.0)
        except Exception:
            c[cid] = 0.0
    return c[cid]


def _rank_score(hit, precision=None, boost=0.0):
    base = (
        hit.lexical_score
        + SYMBOL_WEIGHT * hit.symbol_score
        + hit.formula_score
        + GRAPH_WEIGHT * hit.graph_score
    )
    if boost and precision is not None and _carries_precision(hit.node, precision):
        base += boost
    return base


# WHERE THE USE LAYER ACTS (26 Sep 19:32, claude-825b1305). Until then the tilt above sat inside _rank_score, so it
# decided which cards entered the block. Measured with MATHBRAIN_USE_LOG=0 on the three locate banks (130 questions,
# eval_value_in_block.py): value in block 97 with the weight off, 96 at 0.15 - one question (l1_c99e1a03e8b5) lost all
# three of its cards, the right one among them, to three other cards - and on the 40 reworded L2 questions
# (eval_vib_paraphrase.py) 14 both ways. So the lexical ranking alone now decides the block, and the use layer only
# orders the cards inside it: after the budget has fitted the block, a card moves up past its neighbour when it has
# been used more and its tilted score, score x (1 + MATHBRAIN_USE_WEIGHT x use), passes the neighbour's. The neighbour
# must have outranked it on score, so the campaign demotion and the named lifts keep their order. If the new order
# would fit a different set of cards under the budget, the block keeps the old order.
def _use_order_pieces(pieces, emitted_ids, hits_by_id):
    """Reorder the emitted cards by use, one neighbour at a time; the card set never changes."""
    if MATH_USE_WEIGHT <= 0 or len(emitted_ids) < 2:
        return pieces
    emitted = set(emitted_ids)
    shown = [p for p in pieces if p[0] in emitted]
    rest = [p for p in pieces if p[0] not in emitted]
    moved = False
    for i in range(len(shown) - 1):
        a, b = shown[i][0], shown[i + 1][0]
        ha, hb = hits_by_id.get(a), hits_by_id.get(b)
        if ha is None or hb is None:
            continue
        ua, ub = _use_score(a), _use_score(b)
        if ub <= ua:
            continue
        ra, rb = _rank_score(ha), _rank_score(hb)
        if rb > ra:
            continue
        if rb + MATH_USE_WEIGHT * ub * abs(rb) > ra + MATH_USE_WEIGHT * ua * abs(ra):
            shown[i], shown[i + 1] = shown[i + 1], shown[i]
            moved = True
    return shown + rest if moved else pieces


# ANCHOR RULE (2026-09-22, brain study F55). Measured on the 101 cards the hook served across the 35 harder proof
# audits, each judged one at a time by an Opus 5 judge: 34 were off the argument's domain (Johnson-Lindenstrauss on
# an extremal-graph item, pulled by "eps"; maximum likelihood on a dispersive-PDE item, pulled by "estimate";
# quantifier elimination pulled by the single word "elimination"), 4 licensed the planted false step. Every
# on-domain card shared at least THREE content words with the argument (median 8); the off-domain ones had a
# first quartile of 1. Requiring three shared content words - or a title the prompt names - drops 13 of the 34
# off-domain cards and 1 of the 4 false friends while keeping every one of the 52 bearing and 25 helpful cards
# (runs/judge2/CARD_RULES_OFFLINE.json in campaign_2026-09-22_recall). The rule scales with the prompt: a long
# argument (40+ content words) needs 3, a medium prompt 2, a short question is a name lookup and is exempt, so
# the abstention gates and the title match carry it as before. MATHBRAIN_ANCHOR_SHARED=0 switches it off.
_ANCHOR_STOP = frozenset("""a an the and or but if then than that this those these of in on at to for from by with
without into over under about as is are was were be been being it its we our you your what which who when where why
how not no so such very more most much many few some any all each every both one two first second next let there
here per also only just after before then thus hence since where while any every for each given suppose assume
follows holds implies proof step claim lemma theorem definition concept show shows""".split())


# LaTeX command names are notation, not content (2026-09-24, ab30 study). On the arXiv proofs of the ab30 bank the
# shared "words" that anchored Bernstein inequalities to a Lipschitz-free-space item were frac, leq and varepsilon,
# and mathcal and mathfrak anchored a tensor-triangulated definition to a topological-monoid item. Removing \name
# before the word scan drops those two cards and changes nothing on the 101 judged cards of 09-22, whose arguments are
# prose (campaign_2026-09-23_ab30_tokens/tools/math_card_anchor_rules.py, MATH_CARD_ANCHOR_RULES.json).
_LATEX_CMD = re.compile(r"\\[A-Za-z]+")


# DASHES FOLD IN THE WORD-SET TESTS (MATHBRAIN_DASH_FOLD, default on; campaign mb10d, 25 Sep, arm cand55). An argument writes
# "Cauchy–Schwarz" with an en dash, the card "Cauchy-Schwarz" with a hyphen; the prefilter and ranker fold dashes, these tests did
# not, and the anchor rule dropped the Cauchy-Schwarz cards from a proof citing Cauchy–Schwarz. The em dash (punctuation) stays.
DASH_FOLD_ON = os.environ.get("MATHBRAIN_DASH_FOLD", "1").strip() not in ("0", "off", "false", "")
_MS_DASH_FOLD = {ord(c): "-" for c in "\u2010\u2011\u2012\u2013\u2212"}


def _fold_dashes(text):
    return (text or "").translate(_MS_DASH_FOLD) if DASH_FOLD_ON else (text or "")


def _content_words(text):
    return {w for w in re.findall(r"[a-z][a-z-]{2,}", _LATEX_CMD.sub(" ", _fold_dashes(text)).casefold())
            if w not in _ANCHOR_STOP}


def _anchor_min_shared(prompt):
    """How many content words a card must share with THIS prompt, or 0 when the rule does not apply."""
    if prompt is None:
        return 0
    try:
        k = int(os.environ.get("MATHBRAIN_ANCHOR_SHARED", "3") or 0)
    except ValueError:
        k = 3
    if k <= 0:
        return 0
    n = len(_content_words(prompt))
    if n >= 40:
        return k
    # THE MEDIUM TIER, MEASURED (MATHBRAIN_ANCHOR_MEDIUM, default on; campaign mb10d, 25 Sep, arm cand35). It was extrapolated
    # from the long-audit calibration above and never measured on its own; every house and fresh-bank question sits in it.
    # Switched off: the held-out banks (memory-blind, FEI, fresh2, the hook-path bank) are identical; the house bank gains
    # three first answers (the MMPP question whose card says "MMPP" where the question says "Markov-modulated") - but on his
    # traffic the Erdos 548 verification prompt loses its Erdos-Sos card to Lean cards for other Erdos problems, a useful block
    # turned junk. Kept on. MATHBRAIN_ANCHOR_MEDIUM=0 switches it off and leaves long prompts at k.
    if n >= 12 and os.environ.get("MATHBRAIN_ANCHOR_MEDIUM", "1").strip() not in ("0", "off", "false", ""):
        return max(1, k - 1)
    return 0


_ANCHOR_SUFFIXES = ("ations", "ation", "ities", "ity", "ions", "ion", "ings", "ing", "ies", "es", "ed", "s")


def _anchor_stem(w):
    for suf in _ANCHOR_SUFFIXES:
        if w.endswith(suf) and len(w) - len(suf) >= 4:
            return w[: -len(suf)]
    return w


# NORMALISED ANCHOR WORDS (MATHBRAIN_ANCHOR_NORM, default off; campaign mb10d, 25 Sep, arm cand35): a hyphenated word also
# counts by its parts and every word by a light stem, so "event-stationary" meets "event". On the 101 judged cards of 22 Sep
# it changes no decision; on the banks the held-out ones are identical and two house questions turn right (in-sample); on
# his traffic it adds one more card to three blocks that were junk already and changes no useful block. A change whose only
# gain is in-sample and whose traffic cost is more junk text is not taken; the switch stays for a denser test.
def _anchor_words(text):
    """The anchor rule's word set: _content_words, or under MATHBRAIN_ANCHOR_NORM=1 their light stems plus the parts of
    hyphenated words."""
    words = _content_words(text)
    if os.environ.get("MATHBRAIN_ANCHOR_NORM", "0").strip() in ("0", "off", "false", ""):
        return words
    out = set()
    for w in words:
        out.add(_anchor_stem(w))
        if "-" in w:
            out |= {_anchor_stem(p) for p in w.split("-") if len(p) >= 3 and p not in _ANCHOR_STOP}
    return out


def _anchored(node, prompt_words, k):
    title_words = _anchor_words(getattr(node, "title", "") or "")
    if title_words and (title_words <= prompt_words or len(title_words & prompt_words) >= 2):
        return True
    card_words = _anchor_words((getattr(node, "title", "") or "") + " " + (getattr(node, "body", "") or "")[:1500])
    return len(card_words & prompt_words) >= k


def _title_is_the_query(query, title):
    """True when the sub-query names the node itself: same words, order and punctuation aside.

    "Aubin-Lions lemma" against "Aubin-Lions compactness lemma" is a match (the query's words are all there);
    "pointwise identity" against "Pitfall: Poisson Summation Needs Regularity" is not.
    """
    def words(s):
        s = unicodedata.normalize("NFKD", s or "").casefold()
        s = "".join(c for c in s if not unicodedata.combining(c))
        return {w for w in re.findall(r"[a-z][a-z-]{2,}", s) if w not in ("the", "and", "for", "with", "its")}
    q, t = words(query), words(title)
    if len(q) < 2 or not t:
        return False
    return q <= t or (len(q & t) >= 2 and len(q & t) >= len(q) - 1)


# IDENTIFIER CUE - a candidate, dial default OFF (MATHBRAIN_IDENT_BOOST=0), 2026-09-13.
# A question that names a problem the way a mathematician does ("Hasebe Ueda Wang problem 4.7") carries almost
# no weight in retrieval - the surrounding words decide. Change one digit and leave every other word alone and
# the hook serves the neighbour's result card 8 times in 10, at every near-tie threshold including today's.
# The card names its own problem in its title, so that is a near miss offered as a lead, not a false claim, and
# on 39 questions about REAL neighbouring problems the hook served zero wrong result cards. So this cue does
# not demote. It PROMOTES: on ov04 and ov08 the right card's title carries the identifier the question names,
# the card sits at prefilter rank 2 and 3 of 300, and it still does not reach the block. Measured on generation
# 4e2fca96: owner-voice own-result-card 20 -> 22 of 30 at boost 2.0, nothing lost, serve count unchanged, zero
# wrong cards on the near-miss probe, the 89-theorem fresh bank byte-identical card for card (0 of its 89
# questions name an identifier, so the cue cannot fire there), timing a wash on interleaved pairs.
# The pattern carries NO backslash on purpose: an escape written through a generator arrived once as the 0x08
# byte and the regex silently matched nothing. Character classes say the same thing and cannot be mangled.
_IDENT_RE = re.compile(
    '(?:problem|question|ansatz|conjecture|section|exercise)[ ]+([0-9]{1,3}(?:[.][0-9]{1,3})?)'
    '|(?<![0-9.])([0-9]{1,2}[.][0-9]{1,2})(?![0-9.])',
    re.IGNORECASE)


def _idents(text):
    """The problem identifiers a piece of text names. A bare decimal counts - "problem 7.5" and "7.5" are the
    same reference in his voice - but a bare integer does not, because integers are everywhere in mathematics,
    and a four-digit decimal does not either, because that is an arXiv number (the negative control caught it)."""
    return {m.group(1) or m.group(2) for m in _IDENT_RE.finditer(text or "")}


def _coverage_ident_exempt(prompt, scores, top=20):
    """Let a question past the coverage abstention when it names a problem identifier that one of its own top
    candidates carries IN ITS TITLE. Behind MATHBRAIN_COVERAGE_IDENT_EXEMPT, default off.

    Same species as the ENUMERATION exemption already in that gate: a question shape the coverage ratio judges
    badly. ov30 is the measured case - best candidate 0.384 of the question's self-score against a 0.45 floor,
    while its target card sits at prefilter rank 2 of 300 with the very number the question names in its title.
    Lowering the dial instead buys that one answer and pays two extra absent-target fires, and the curve is a
    step, so there is no cheaper setting; this is the targeted version of the same purchase.

    The store read costs nothing on a prompt that names no identifier, which is the common case (0 of the 40
    unanswerable probes name one, 17 of the 30 owner-voice questions do): the wanted set is computed first and
    an empty one returns before any query."""
    # DEFAULT ON since 2026-09-14 00:4x. Re-measured on the generation it landed on (b18409b9 / patched from
    # 297122a7): inert with both dials unset (0 of 30 blocks differ), exemption alone owner-voice served 25 -> 26,
    # own result card 22 -> 23 of 30, unanswerable fires 27/40 unchanged, adversarial neighbour-card 9/10
    # unchanged; the boost (MATHBRAIN_IDENT_BOOST 2.0) added NOTHING on top on this generation (23/30 either way),
    # so it stays 0 - exactly the "one of the two edits is redundant now" outcome the 09-13 measurement warned of.
    if str(os.environ.get("MATHBRAIN_COVERAGE_IDENT_EXEMPT", "1")).strip().lower() in ("0", "", "off", "false"):
        return False
    wanted = _idents(prompt)
    if not wanted:
        return False
    try:
        import sqlite3
        ids = [i for i, _s in sorted(scores.items(), key=lambda kv: -kv[1])[:top]]
        if not ids:
            return False
        con = sqlite3.connect("file:%s?mode=ro" % str(STORE_PATH).replace(chr(92), "/"), uri=True)
        try:
            q = "select title from nodes where id in (%s)" % ",".join("?" * len(ids))
            for (title,) in con.execute(q, ids):
                if wanted & _idents(title or ""):
                    return True
        finally:
            con.close()
    except Exception:
        return False
    return False


def _ident_boost():
    try:
        return float(os.environ.get("MATHBRAIN_IDENT_BOOST", "0") or 0.0)
    except ValueError:
        return 0.0


def _ident_kw_boost():
    try:
        return float(os.environ.get("MATHBRAIN_IDENT_KW_BOOST", "1.0") or 0.0)
    except ValueError:
        return 0.0


# capitalised words a title or a prompt carries that are not the name of a source ("Open Problem", "Section 4.6")
_KW_NAME_STOP = frozenset(("open", "section", "conjecture", "conjectures", "ansatz", "exercise", "problems",
                           "questions", "part", "chapter", "remark", "solved", "answered", "proved", "refuted",
                           "candidate", "resolved", "fully", "our", "id", "yes", "no", "not", "known", "false",
                           "true", "result", "results", "attempt", "failed", "route", "note", "case"))


def _kw_idents(text):
    """Identifiers the text names WITH a keyword ("Problem 4.45", "Conjecture 18.4", "Section 4.6") - the first
    alternative of _IDENT_RE only; a bare decimal is not a problem name in a work order."""
    return {m.group(1) for m in _IDENT_RE.finditer(text or "") if m.group(1)}


def _carries_ident(node, wanted):
    """Only the TITLE is read. A body mentions neighbouring problem numbers in passing ("unlike Problem 7.6");
    the title is where a card states which problem it is about, and all 13 result cards do."""
    if not wanted:
        return False
    return bool(wanted & _idents(getattr(node, "title", "") or ""))


# BLOCK COVERAGE (2026-09-24, mb10d campaign). The paper's decision rule is to speak only when the chance the block is
# right clears the cost ratio of a wrong fire to a right one. The best single predictor of that chance on the fresh bank
# is how much of the question the block's best card actually carries: the idf share of the question's indexed tokens that
# occur in the card's title and body (AUC 0.95 between a right first card and a confident card on an absent-target
# question; blocks judged to be the ones that pass every other gate). Measured on the cand1 run: blocks whose best card
# covers less than 0.40 of the question were right 6 times and false fires 11 times (P about 0.35), blocks from 0.40 to
# 0.45 about half and half. So below the floor the hook stays silent. Only on plain questions: a pinned title, a clause
# split (each clause's card covers only its clause) or an argument-shaped prompt (the anchor rule's territory, where one
# card covers a small share of a long proof by design) is never judged by it. MATHBRAIN_BLOCK_COV sets the floor; 0 is
# off.
def _block_cov_floor():
    try:
        return float(os.environ.get("MATHBRAIN_BLOCK_COV", "0.35") or 0)
    except Exception:
        return 0.0


# A HOUSE FLOOR (MATHBRAIN_BLOCK_COV_HOUSE, default 0.25; MATHBRAIN_BLOCK_COV_HOUSE_MODE lead|any, default lead; campaign
# mb10d, 25 Sep, arm cand36). A block that LEADS with one of our own records (_is_house) is judged at 0.25 instead of the
# floor above. Why: the floor study (0.35 / 0.30 / 0.25 for every block) moved the memory-blind house bank from 31 to 41
# first at 0.25, ten of eleven moved blocks right and every one led by our own card, while its costs - three wrong answers
# on fresh2's textbook questions, two absent-target false fires on the hook-path bank - sat in blocks carrying none of ours.
# A strong model already knows a textbook theorem; it cannot know our results, so this is where a lower floor pays. Measured
# as a house floor against the landed hook: 20 held-out questions leave silence (memory-blind, carry46, the v4 blind cards),
# 16 of them to the answer card first; carry46's absent controls, fresh2, the FEI held-out bank and the hook-path bank
# unchanged; his traffic untouched (the floor judges only plain short questions). Mode "any" (the block merely carries one of
# ours) also turned one FEI question right and was not taken, by the rule written before the results. 0 or empty switches it
# off.
def _block_cov_floor_for(chosen, floor):
    """The floor a block is judged at: the house floor when it is lower and the block leads with (mode any: carries) one of
    our own records; otherwise the floor itself."""
    try:
        raw = os.environ.get("MATHBRAIN_BLOCK_COV_HOUSE", "0.25").strip()
        if raw in ("", "0", "off", "false") or not chosen:
            return floor
        house = float(raw)
        if house >= floor:
            return floor
        if os.environ.get("MATHBRAIN_BLOCK_COV_HOUSE_MODE", "lead").strip() == "lead":
            return house if _is_house(chosen[0].node) else floor
        return house if any(_is_house(h.node) for h in chosen) else floor
    except Exception:
        return floor


def _cov_agree_wide(chosen, st):
    """COV_AGREE's test (claude-5693aca3, 19 Oct; default 2). MATHBRAIN_COV_AGREE_WIDE 0 = the old rule (the semantic
    top is the lead). 1 = also when the semantic top is a same-title copy of the lead (titles equal after case and
    punctuation folding). 2 = also when the semantic top is any card of the block; it is moved to the front.
    Bank 19 Oct (math_probe_fresh_20260903, hook path): 0 -> 70/57/8 served/rank1/silent, 2 -> 72/59/6, the two rows
    gained and nothing else changed; unanswerable 28/40, strict 19/31 both arms, no id list changed.
    Study: ~/.claude/brain_graph/studies/math_hook_cov_agree_wide_20261019."""
    if not chosen or not st:
        return False
    if _same_card(chosen[0].node.id, st):
        return True
    try:
        wide = int(os.environ.get("MATHBRAIN_COV_AGREE_WIDE", "2") or 0)
    except ValueError:
        wide = 0
    if wide <= 0:
        return False
    fold = lambda t: " ".join(re.sub(r"[^a-z0-9]+", " ", (t or "").casefold()).split())
    try:
        from math_brain.store import ResearchStore
        with ResearchStore.open_read_only(STORE_PATH) as store:
            n = store.get_node(st) if len(st) >= 64 else None
            if n is None:
                row = store.connection.execute("SELECT id FROM nodes WHERE id LIKE ? LIMIT 2", (st + "%",)).fetchall()
                n = store.get_node(row[0][0]) if len(row) == 1 else None
    except Exception as _e:
        n = None
        if os.environ.get("MATHBRAIN_TRACE_ANSWERS"): print("[agree-wide] lookup failed %r" % (_e,), file=sys.stderr)
    if os.environ.get("MATHBRAIN_TRACE_ANSWERS"): print("[agree-wide] st %s lead %r sem %r" % (st[:16], chosen[0].node.title, getattr(n, "title", None)), file=sys.stderr)
    if n is not None and fold(n.title) and fold(n.title) == fold(chosen[0].node.title):
        return True
    if wide >= 2:
        for i, h in enumerate(chosen[1:], 1):
            if _same_card(h.node.id, st):
                chosen.insert(0, chosen.pop(i))
                return True
    return False


def _block_best_coverage(prompt, hits):
    """The largest idf share of the prompt's store-known tokens carried by one card of the block (None: cannot tell)."""
    import math
    import math_prefilter
    data = math_prefilter.load(STORE_PATH)
    if not data:
        return None
    n_docs, post = data["n"], data["post"]
    qt = {t for t in math_prefilter.tokens(prompt) if t in post}
    if not qt:
        return None

    def _idf(t):
        df = len(post[t])
        return math.log(1.0 + (n_docs - df + 0.5) / (df + 0.5))

    wq = sum(_idf(t) for t in qt) or 1.0
    best = 0.0
    for h in hits:
        ct = set(math_prefilter.tokens(_card_index_text(h.node)))
        best = max(best, sum(_idf(t) for t in qt & ct) / wq)
    return best


# LINKED RIVALS (2026-09-24, mb10d campaign). The margin test asks whether there is a clear winner among DISTINCT
# candidates; the family keys above say a copy, an enumerated sibling, a campaign sibling or a card on the named thing
# is not a rival. A card the store itself links to the leader by an edge is not one either: a pitfall note and the
# result it warns about (FAILS_UNDER), a lemma and the theorem resting on it (DEPENDS_ON), a statement and a
# refutation of it, a claim and the notion it names. A near-tie inside one linked neighbourhood is concentrated
# evidence about one topic, and the block carries both cards. Found when store-wide IDF packed the Hurwitz pitfall,
# Weierstrass convergence, Montel and Normal Family within 0.1 of each other (all four linked) and the hook went
# silent on six pitfall questions it had answered. The adjacency is the pool's own edges (SUPERSEDES excluded: a
# superseded card is not current). MATHBRAIN_LINKED_RIVAL=0 restores the old rule.
_POOL_ADJ = {}


def _set_pool_adjacency(edges):
    """Record which pool cards the store links directly, for the margin test's rival choice."""
    global _POOL_ADJ
    adj = {}
    try:
        for e in edges or ():
            k = getattr(getattr(e, "kind", None), "value", str(getattr(e, "kind", "")))
            if k == "supersedes":
                continue
            a, b = e.source_id, e.target_id
            if a == b:
                continue
            adj.setdefault(a, set()).add(b)
            adj.setdefault(b, set()).add(a)
    except Exception:
        adj = {}
    _POOL_ADJ = adj


def _linked_to(a_id, b_id):
    """1 (default): a direct edge. 2: a direct edge or a shared neighbour of small degree - two cards about the same
    result (a pitfall note and a convergence theorem that both touch Hurwitz's theorem) without a hub between them;
    MATHBRAIN_LINK_HUB_MAX caps the shared neighbour's degree (default 8), so a concept card that half the pool
    mentions never makes two unrelated cards siblings."""
    mode = os.environ.get("MATHBRAIN_LINKED_RIVAL", "2")
    if mode == "0":
        return False
    na = _POOL_ADJ.get(a_id, ())
    if b_id in na:
        return True
    if mode != "2":
        return False
    try:
        hub_max = int(os.environ.get("MATHBRAIN_LINK_HUB_MAX", "8") or 8)
    except Exception:
        hub_max = 8
    nb = _POOL_ADJ.get(b_id, ())
    return any(len(_POOL_ADJ.get(m, ())) <= hub_max for m in set(na) & set(nb))


# UNSERVED KINDS (campaign mb10d, 25 Sep, arm cand37). An evidence node is a recorded read about a claim; a source node is
# a record about the store itself - the only one, note_dedup_refw_fpp02_2026, marks two FPP claims as duplicates ("DEDUP
# POINTER: ..."). Neither is mathematics to serve. On his 483 past prompts the note had been served in four blocks: the only
# card of two junk blocks (loop prompts asking agents to keep working on the brain), one more junk card in a third, and beside
# the FPP campaign card in a useful one. MATHBRAIN_SERVE_SOURCE=1 serves source nodes again.
_UNSERVED_KINDS = ("evidence",) + (() if os.environ.get("MATHBRAIN_SERVE_SOURCE", "0").strip() not in ("0", "off", "false", "")
                                   else ("source",))


# ONE KEY FOR "THE SAME TITLE" (campaign mb10d, 25 Sep, arm cand59): case and punctuation folded, and accents when fold is on, so
# "Hölder's inequality" and "Holder's Inequality" are one title. The sub-query merge always folds (MATHBRAIN_MERGE_TITLE_DEDUP,
# default on - after every block has passed its own floor); _select folds only under MATHBRAIN_TITLE_DEDUP_FOLD (default off: a
# fold before the floor can drop the copy that would have survived it).
TITLE_DEDUP_FOLD = os.environ.get("MATHBRAIN_TITLE_DEDUP_FOLD", "0").strip() not in ("0", "off", "false", "")
MERGE_TITLE_DEDUP = os.environ.get("MATHBRAIN_MERGE_TITLE_DEDUP", "1").strip() not in ("0", "off", "false", "")
# A DROPPED DUPLICATE KEEPS ITS PLACE (MATHBRAIN_MERGE_DUP_NO_REFILL, default on; arm cand60): the card behind it does not move up
# into the slot, so the block carries one card fewer instead of a card from further down the merge.
MERGE_DUP_NO_REFILL = os.environ.get("MATHBRAIN_MERGE_DUP_NO_REFILL", "1").strip() not in ("0", "off", "false", "")


def _title_key(title, fold=None):
    t = title or ""
    if (TITLE_DEDUP_FOLD if fold is None else fold):
        t = "".join(c for c in unicodedata.normalize("NFKD", t) if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", t.casefold()).strip()


NAMED_FAMILY_FOLD = os.environ.get("MATHBRAIN_NAMED_FAMILY_FOLD", "1").strip() not in ("0", "", "off", "false")


def _nf_fold(title):
    t = (title or "").casefold()
    if NAMED_FAMILY_FOLD:
        t = "".join(c for c in unicodedata.normalize("NFKD", t) if not unicodedata.combining(c))
    return t


def _select(hits, apply_gates=True, cap=3, title_query=None, prompt=None, anchor=True):
    """Re-rank, gate, deduplicate by title, cap campaign deposits, take `cap` (three by default).

    Returns [] when the store should stay silent.
    """
    # the precision cue (see _asked_precision): a question asking for a value at a stated precision
    # prefers cards that carry a numeral at that precision. Zero boost = this is inert.
    _pb = _precision_boost()
    _prec = _asked_precision(prompt if prompt is not None else (title_query or "")) if _pb else None

    _ib = _ident_boost()
    _want = _idents(prompt if prompt is not None else (title_query or "")) if _ib else set()
    # A PROBLEM THE PROMPT NAMES (mb10d campaign, 2026-09-24; MATHBRAIN_IDENT_KW_BOOST, relative, 0 = off). The fixed
    # +MATHBRAIN_IDENT_BOOST above is sized for a one-line question; on a 300-400-word research prompt the rank scores
    # run into the hundreds and +2 moves nothing (the ab30 study measured boost 2.0 on its 22 research prompts: 0 blocks
    # changed). Two of them name their problem outright ("Open Problem 4.45", "Open Problem 7.27") and the card whose
    # title carries that number sat 11th and 3rd. So a card is lifted by a SHARE of the best card's own score when (a) the
    # prompt names the identifier with a keyword - problem, question, conjecture, ansatz, section, exercise - never a
    # bare decimal, which in his work orders is a price, a dial or a size (0.65, 3.5 GB), and (b) the card's title
    # shares one of the prompt's names (Aldous, Fill, Janson ...) or carries none, so Lyons-Peres Problem 4.45 is not
    # lifted by a question about Aldous-Fill's.
    _kwb = _ident_kw_boost()
    _want_kw = _kw_idents(prompt if prompt is not None else (title_query or "")) if _kwb else set()
    _names_q = (_name_tokens(prompt or title_query or "") - _KW_NAME_STOP) if _want_kw else set()
    _kw_top = [0.0]

    def _kw_lift(hit):
        if not _want_kw:
            return False
        title = getattr(hit.node, "title", "") or ""
        if not (_want_kw & _idents(title)):
            return False
        tn = _name_tokens(title) - _KW_NAME_STOP
        return (not tn) or bool(tn & _names_q)

    def _rs(hit):
        s = _rank_score(hit, _prec, _pb)
        if _ib and _carries_ident(hit.node, _want):
            s += _ib
        if _kwb and _kw_top[0] > 0 and _kw_lift(hit):
            s += _kwb * _kw_top[0]
        return s

    if _want_kw:
        try:
            _kw_top[0] = max(_rank_score(h, _prec, _pb) for h in hits)
        except ValueError:
            _kw_top[0] = 0.0

    # An evidence node (a recorded read, a certificate, a citation) is ABOUT a claim; it is not
    # mathematics to serve. The store links it to its claim and indexes its summary like any node,
    # so without this it could take a slot on a query that happens to share its words
    # (2026-09-03, the day the evidence tables stopped being empty).
    ranked = sorted(
        (h for h in hits if str(getattr(h.node, "kind", "") or "").lower() not in _UNSERVED_KINDS),
        key=lambda hit: (-_rs(hit), hit.node.id),
    )
    if not ranked:
        return []
    # SANDBOX (claude-5693aca3, 19 Oct): coverage tie-break among near-tied leaders, MATHBRAIN_COVER_EPS (0 = off).
    try:
        _ceps = float(os.environ.get("MATHBRAIN_COVER_EPS", "0") or 0)
    except ValueError:
        _ceps = 0.0
    if _ceps > 0:
        _ctop = _rs(ranked[0])
        if _ctop > 0:
            def _ccov(h):
                return len({str(t.detail) for t in (getattr(h, "trace", None) or ()) if t.channel.value == "lexical"})
            _near = [h for h in ranked if _rs(h) >= (1 - _ceps) * _ctop]
            if len(_near) > 1:
                _near.sort(key=lambda h: (-_ccov(h), -_rs(h), h.node.id))
                ranked = _near + ranked[len(_near):]
    # the anchor rule (see _anchor_min_shared) is applied to the CHOSEN cards at the end, dropping without
    # refilling. It first ran here, before the gates, and refilled every freed slot from the ranked tail: on the
    # 35 audits the 12 cards it dropped (11 off-domain, 2 false friends) were replaced by 12 the judge found 11
    # off-domain and 0 helpful (Agent H, 2026-09-22) - less harmful, not better. A slot the argument's own words
    # cannot fill stays empty; the gates keep judging the ranking they always judged.
    _k_anchor = _anchor_min_shared(prompt) if anchor else 0
    _pw = _anchor_words(prompt) if _k_anchor else set()
    if apply_gates:
        top = _rs(ranked[0])
        if top < MIN_SUPPORT:
            return []
        # The margin test asks whether there is a CLEAR WINNER among distinct candidates.
        # Two copies of the same theorem are not two candidates, and comparing against a
        # near-duplicate makes the hook abstain on questions it answers perfectly well.
        #
        # Found 2026-08-30 by the health check after a bulk ingest of two thousand nodes:
        # the dominated convergence query went silent because its statement node and its
        # encyclopedia lede scored 10.033 and 9.935, a margin of 0.0979 against a threshold
        # of 0.1. Nothing about that query got worse - the ingest shifted every score through
        # corpus statistics and pushed an existing near-tie over the edge. Adding material
        # made the brain answer FEWER questions, which is the kind of regression only an
        # end-to-end check catches.
        #
        # So the runner-up is the best node whose normalised title DIFFERS from the leader's.
        # If no such node exists the leader is unopposed and the margin test has nothing to
        # say, so it does not fire.
        #
        # Measured 2026-09-05 on the H1 test (60 questions about the store's own 2,000 enumerated
        # self-avoiding-walk counts): 34 of 60 went SILENT here. The leader and its runner-up were
        # the same sentence with different parameters ("... length 10 ... strip 0 <= y < 3 ...
        # avoid the sites (4,2)" against "(-4,2), (4,1)"), scored a hair apart, and the margin
        # test read that as "no clear winner". But two members of one enumerated family are not
        # two competing answers: the block that goes out carries the parameters, and the reader
        # picks the row that matches (the Opus solver did exactly that in the H1 pilot - it
        # abstained when the sites differed and copied when they matched). So the runner-up must
        # differ from the leader in WORDS, not only in digits: titles that agree once every digit
        # run is replaced by a placeholder are one family, and the margin test does not fire on
        # them. The more precisely a family is enumerated, the more often the old rule silenced
        # it - the same "adding material made the brain answer fewer questions" shape as above.
        def _family_key(title):
            return re.sub(r"\d+", "#", re.sub(r"[^a-z0-9]+", " ", (title or "").casefold())).strip()
        # 2026-09-13: a CAMPAIGN is a family too. One research campaign deposits many notes that differ in words,
        # not only in digits, so the digit-placeholder rule above never covered them and the margin test read two
        # notes from the same campaign as two competing answers. Measured on the 09-11 locate bank: of the three
        # campaign questions the brain served nothing for, l1_c12 died exactly here with "three arm NAE star
        # absorption failure" - the right note - on top, and its rival a sibling from the same campaign. Two
        # deposits of one campaign are not rival answers: the block carries both and the reader picks, which is
        # the argument the enumerated families won on 2026-09-05. MATHBRAIN_CAMPAIGN_FAMILY=0 restores the old rule.
        _CAMPAIGN_FAMILY = os.environ.get("MATHBRAIN_CAMPAIGN_FAMILY", "1") != "0"
        def _campaign_key(node):
            if not _is_campaign(node):
                return None
            meta = _node_meta(node)
            lid = str(meta.get("logical_id") or "")
            m = re.search(r"research\.([a-z0-9_-]+)", lid)
            if m:
                return "research." + m.group(1)
            src = str(meta.get("source") or "")
            if re.search(r"campaign", src, re.IGNORECASE):
                return "source:" + re.sub(r"\s+", " ", src.casefold()).strip()[:60]
            return "domain:" + re.sub(r"\s+", " ", str(meta.get("domain") or "").casefold()).strip()[:60]
        _top_campaign = _campaign_key(ranked[0].node) if _CAMPAIGN_FAMILY else None
        top_key = _title_key(ranked[0].node.title)
        top_family = _family_key(ranked[0].node.title)
        # 2026-09-23: a NAMED family is a family too. "prove the FEI conjecture" went silent here with the FEI
        # step-constant card on top (1.520) and the FEI-for-threshold-functions card 0.096 below it - two results
        # about the one thing the prompt names, read as rival answers. When the prompt names something (an acronym,
        # a proper name - _name_tokens) and the leader's title carries it, a runner-up whose title carries the same
        # name is a sibling, not a competitor: the block carries both and the reader picks. MATHBRAIN_NAMED_FAMILY=0
        # restores the old rule. MEASURED before it went on (eval_math_hookpath, same bank and store, off vs on):
        # unnamed questions served 72 -> 73 of 89, named 83 -> 86 (rank-1 80 -> 83, silent 4 -> 1), absent-target
        # false fires 28/40 -> 28/40, strict 21/33 unchanged, median 1.12 -> 1.13 s; exactly four items moved, all
        # silent -> the right theorem (Kolmogorov 0-1, log-Sobolev, Nullstellensatz, KKL); campaign_2026-09-23_round6.
        _named = set()
        if os.environ.get("MATHBRAIN_NAMED_FAMILY", "1") != "0" and prompt:
            try:
                # the title side folds accents too (MATHBRAIN_NAMED_FAMILY_FOLD, default on; arm cand63): _name_tokens folds the
                # prompt's "Grönwall" to "gronwall", but the card "Grönwall's inequality" split into "gr" + "nwall", never shared
                # the name, and so stood as a rival to its own twin "Gronwall's Inequality (Integral Form)": "State Grönwall's
                # inequality in integral form" went silent on the margin test with both cards on top.
                _named = _name_tokens(prompt) & set(re.findall(r"[a-z0-9]+", _nf_fold(ranked[0].node.title)))
            except Exception:
                _named = set()
        # 2026-10-19 (claude-5693aca3): two more kinds of sibling, each behind its own switch, ON since 10:4x 19 Oct.
        # Measured on the 218-item bank (state/hookpath_rival_{base,poss,vocab,both}_20261019.jsonl): each switch
        # answers exactly its own item and changes nothing else, unanswerable ids identical; both together q_content
        # served 68 -> 70, rank-1 55 -> 57, silent 10 -> 8, false fire 28/40 and strict 19/31 unchanged. Set "0" to turn off.
        # POSSESSIVE family (MATHBRAIN_POSSESSIVE_FAMILY): the leader and the runner-up carry the same "Name's" in their
        # titles ("Bernstein's Inequality for Sums of Independent Bounded ..." against "Bernstein's Inequality for
        # Sub-Exponential Summands") - two versions of one named result, not rival answers, even when the question never
        # says the name (the named-family rule above needs the prompt to say it). VOCAB not a rival
        # (MATHBRAIN_VOCAB_NOT_RIVAL): a vocabulary card (logical_id vocab.*) defines a word and does not compete with a
        # theorem card for the answer; the unnamed Lebesgue differentiation question went silent on "Ergodicity", 0.087
        # under the right card.
        _POSS = os.environ.get("MATHBRAIN_POSSESSIVE_FAMILY", "1") != "0"
        _VOCAB_NR = os.environ.get("MATHBRAIN_VOCAB_NOT_RIVAL", "1") != "0"
        def _possessives(title):
            return {m.casefold() for m in re.findall(r"([A-Z][^\s'’]*)['’]s\b", title or "")}
        def _is_vocab(node):
            return str(_node_meta(node).get("logical_id") or "").startswith("vocab.")
        _top_poss = _possessives(ranked[0].node.title) if _POSS else set()
        _top_vocab = _is_vocab(ranked[0].node) if _VOCAB_NR else True
        rival = next(
            (h for h in ranked[1:]
             if _title_key(h.node.title) != top_key
             and not (_top_poss and _top_poss & _possessives(h.node.title))
             and not (not _top_vocab and _is_vocab(h.node))
             and _family_key(h.node.title) != top_family
             and not (_top_campaign is not None and _campaign_key(h.node) == _top_campaign)
             and not (_named and _named & set(re.findall(r"[a-z0-9]+", _nf_fold(h.node.title))))
             and not _linked_to(ranked[0].node.id, h.node.id)),
            None,
        )
        if rival is not None and (top - _rs(rival)) < MIN_MARGIN:
            # MEASUREMENT WITHDRAWN 2026-09-13: the numbers below were taken while `unicodedata` was not imported
            # in this module, so _title_is_the_query raised NameError, the exception left _select through the main
            # try, and the hook served NOTHING for those items - the arm measured "silent", not "exempted". The
            # import is fixed; this exemption is now UNMEASURED, not rejected. Re-run both arms before believing
            # either. The diagnosis it rests on stands: 18 of 53 named
            # sub-queries die here while the RIGHT node sits on top (Vitali-Caratheodory top 8.6 against a
            # floor of 2.6, the shadowing lemma 14.7, Aubin-Lions 8.2), because the store answers a name with
            # that node plus its near-scoring siblings and the margin test reads them as "no clear winner".
            # But exempting a sub-query that matches the leader's title measures WORSE on the pivot bank:
            # 55 items / 69 titled pivots against 59 / 74, both at MATHBRAIN_TIME_BUDGET_S=90 on the same
            # store - the freed sub-query cards displace base cards that were themselves carrying a used
            # result. (The first comparison that killed this read 55 against 59 with the two arms at DIFFERENT
            # machine loads, which is not a comparison at all - see the time-budget note below.)
            return []
    chosen = []
    seen_titles = {}   # normalised title -> index in `chosen`
    campaign_taken = 0
    # 2026-09-11: the cap exists to keep a closed campaign cluster off famous-theorem queries. When the query
    # is ABOUT the campaign - the ranker's own top hit is a campaign node - the cap starved the answer: on the
    # locate-the-fact bank the harness-count node ranked second behind its method note and the rank-2/3 slots
    # went to Inclusion-Exclusion and the CLT instead (5 of 20 campaign questions). A campaign-led ranking may
    # fill all three slots from the campaign; a textbook-led ranking keeps the one-slot cap unchanged.
    # a harvest node at the top never opens the campaign-led expansion (under MATHBRAIN_HARVEST_CAP): a harvest edge
    # over the textbook node on a famous-theorem question would otherwise turn the whole block into harvest siblings
    _top_lid = str(_node_meta(ranked[0].node).get("logical_id") or "")
    campaign_cap = cap if (_is_campaign(ranked[0].node) and not (HARVEST_CAP and "harvest.arxiv" in _top_lid)) else MAX_CAMPAIGN_SLOTS
    for hit in ranked[:SLOT_WINDOW]:
        if len(chosen) >= cap:
            break
        node = hit.node
        campaign = _is_campaign(node)
        if campaign and campaign_taken >= campaign_cap:
            continue
        # harvest and authored waves can hold the same theorem under
        # near-identical titles; injecting it twice burns a slot
        title_key = _title_key(node.title)
        if title_key in seen_titles:
            # ... and when they do, the higher-ranked one is often the harvested
            # encyclopedia OPENING while the lower-ranked one actually states the
            # theorem. Keeping the first hit served "In mathematics, X is a result
            # about..." in place of the statement on 23 theorems, measured
            # 2026-08-30 over the whole store: Rao-Blackwell at lede 10.54 against
            # statement 0.37, Cartan-Hadamard 7.90 against 0.36, AM-GM 5.53 against
            # 1.25. Both nodes carry the same normalised title, so swapping cannot
            # change WHICH theorem is injected - only whether its statement or its
            # opening paragraph goes out.
            prev_i = seen_titles[title_key]
            if _is_lede(chosen[prev_i].node) and not _is_lede(node):
                chosen[prev_i] = hit
            continue
        seen_titles[title_key] = len(chosen)
        chosen.append(hit)
        if campaign:
            campaign_taken += 1
    # A statement sibling can sit BELOW the slot window while its opening paragraph
    # sits above it - five of the twenty-three cases above were exactly that, and the
    # swap in the loop could not reach them. So make one pass over the full ranked
    # list for the ledes that survived. This looks past SLOT_WINDOW deliberately and
    # it is safe for the same reason the swap is: a candidate is only considered when
    # it carries the SAME normalised title as a node already chosen, so it cannot
    # introduce a subject the window had excluded - it can only replace an opening
    # with the statement of a theorem that is already going out.
    if len(ranked) > SLOT_WINDOW:
        chosen_keys = {
            _title_key(h.node.title): i
            for i, h in enumerate(chosen) if _is_lede(h.node)
        }
        if chosen_keys:
            for hit in ranked[SLOT_WINDOW:]:
                if not chosen_keys:
                    break
                key = _title_key(hit.node.title)
                i = chosen_keys.get(key)
                if i is not None and not _is_lede(hit.node):
                    chosen[i] = hit
                    del chosen_keys[key]
    # A campaign log does not outrank real mathematics in the block - UNLESS it clearly
    # outranks it, which the first version of this rule could not express.
    #
    # The unconditional form put a node scoring 9.56 behind a heat-equation maximum
    # principle at 2.94 and a triangle removal lemma at 2.74, on a question about
    # first-passage percolation. Measured 2026-08-30 over the bank queries: on
    # campaign-drawn questions 90 percent of blocks had a better node pushed below the
    # first slot, every one of them by more than three score points and the worst by
    # 64.7; on famous-theorem questions the same figure is 6 percent. So the penalty was
    # nearly harmless where it was designed to help and severe where it was not.
    #
    # The repair is the one this brain's own note asked for in August and did not get: a
    # category penalty that cannot be outvoted by a rank-1 match is a bug. A campaign
    # node keeps its place when it beats the best non-campaign node by DEMOTION_MARGIN;
    # otherwise it is demoted exactly as before. The cap and the ordering are untouched.
    #
    # Margin chosen by sweep, not by taste. At 1.0 the inversion rate goes to zero on
    # BOTH populations and the campaign share of slots on famous-theorem queries is
    # unchanged at 4.6 percent - the crowd-out that justified leaving this alone does not
    # occur, because nothing new is admitted to the block, only reordered within it.
    campaign_hits = [h for h in chosen if _is_campaign(h.node)]
    plain = [h for h in chosen if not _is_campaign(h.node)]
    best_plain = max((_rs(h) for h in plain), default=float("-inf"))
    earned = [h for h in campaign_hits if _rs(h) > best_plain + DEMOTION_MARGIN]
    demoted = [h for h in campaign_hits if h not in earned]
    _out = earned + plain + demoted
    if _k_anchor:
        _out = [h for h in _out if _anchored(h.node, _pw, _k_anchor)]
    return _out


#: a claim node whose title runs this long carries the old projection
LEGACY_CLAIM_TITLE_CHARS = 200
MAX_ASSUMES_CHARS = 220
MAX_LABEL_CHARS = 120


def _render_parts(node, query="", max_text_chars=None):
    """label, text for one hit - reuniting statements with their assumptions.

    `max_text_chars` is accepted for signature parity with the package renderer (which
    honours it); this fallback uses MAX_STATEMENT_CHARS.

    `query` is accepted and IGNORED here on purpose. The package copy
    (math_brain.rendering.render_parts) uses it to pick the passage of an
    over-long statement that matches the question instead of cutting from the
    head, which is worth 9 of 40 items against 27 of 40 on the retrieval-
    dependent banks. This is the fallback for a missing or broken build, and
    duplicating that scorer here is how a fallback silently drifts a version
    behind the thing it stands in for. Missing the build degrades to head
    truncation; it does not break.

    Two claim-node shapes exist in the store. Legacy (appended before
    2026-08-29): the whole statement in the TITLE and only the assumptions in
    the BODY, so rendering body-as-content served a conditional claim without
    its conditions - the transfer-principle rows went out as unconditional.
    New (store.py 437f805): short title, body = statement + 'Assumes:' clause.
    Both shapes render the same way here: label from the statement head, text =
    statement plus a capped Assumes tail that truncation can never drop.
    """
    title = " ".join((node.title or "").split())
    body = " ".join((node.body or "").split())
    is_claim = bool(_node_meta(node).get("claim_kind"))
    if is_claim and len(title) >= LEGACY_CLAIM_TITLE_CHARS:
        # legacy body = "\n".join(assumptions); keep the boundaries visible
        statement = title
        assumes = "; ".join(
            part.strip() for part in (node.body or "").splitlines() if part.strip()
        )
        label = statement[:MAX_LABEL_CHARS]
        if " " in label:
            label = label[: label.rfind(" ")]
        label += "..."
    else:
        label = title
        statement, assumes = body, ""
        if is_claim and " Assumes: " in body:
            statement, assumes = body.rsplit(" Assumes: ", 1)
    text = statement
    if assumes:
        clause = "Assumes: " + assumes
        if len(clause) > MAX_ASSUMES_CHARS:
            clause = clause[: MAX_ASSUMES_CHARS - 3] + "..."
        budget = max(MAX_STATEMENT_CHARS - len(clause) - 4, 80)
        if len(text) > budget:
            text = text[: budget - 3] + "..."
        text = text + " || " + clause
    elif len(text) > MAX_STATEMENT_CHARS:
        text = text[: MAX_STATEMENT_CHARS - 3] + "..."
    return label, text


SESSION_STATE = os.path.join(HOME, ".claude", "dreaming", ".math_sense_session_state.json")
# One shard per session since 2026-09-14; the monolith above is no longer read or written (see
# _session_shard for the measurement that retired it).
SESSION_DIR = os.path.join(HOME, ".claude", "dreaming", ".math_sense_sessions")
SESSION_STATE_TTL_S = 48 * 3600


def _session_shard(session_id):
    """This session's own state file. Sharded 2026-09-14: the guard used ONE json object holding every
    session, and every hook call read, TTL-filtered and RE-SERIALISED all of it to record a few ids for
    one session. Measured that day: 5.2 MB, 19,846 sessions, and `_already_injected` was 0.68 s of a
    1.69 s math-hook call - 0.58 s of it a single json.dump over 300,177 encode calls - a cost that grew
    with every session the fleet had ever run and was paid by every prompt of every agent. One small file
    per session makes it O(1). A session id is hashed, never used as a filename: it is caller-supplied."""
    h = hashlib.sha1((session_id or "").encode("utf-8", "replace")).hexdigest()[:24]
    return os.path.join(SESSION_DIR, h + ".json")


SERVED_ARCHIVE = os.environ.get("MATHBRAIN_SERVED_ARCHIVE") or os.path.join(
    os.path.expanduser("~"), ".claude", "brain_graph", "live", "math_served_archive.jsonl")


def _prune_session_shards(now, sample=1):
    """Retire shards past the TTL. Runs on roughly one call in `1/sample`, never on the read path's
    critical section, and never raises: a directory scan is cheap but it is not free, and the guard
    must not become the thing it replaced.
    BRAIN DATA IS NEVER DELETED (his law of 26 Sep 2026): a shard is the record of which claims reached which session,
    so its content is appended to the permanent archive (SERVED_ARCHIVE) first, and the file is removed only when that
    append succeeded. Before 26 Sep these records were deleted after 48 hours."""
    try:
        if random.random() > sample:
            return
        for n in os.listdir(SESSION_DIR):
            if not n.endswith(".json"):
                continue
            p = os.path.join(SESSION_DIR, n)
            try:
                if now - os.path.getmtime(p) > SESSION_STATE_TTL_S:
                    with io.open(p, encoding="utf-8") as f:
                        rec = f.read()
                    try:
                        body = json.loads(rec)
                    except ValueError:
                        body = {"raw": rec[:20000]}
                    line = json.dumps({"shard": n[:-5], "archived": round(now, 1), "rec": body},
                                      separators=(",", ":"))
                    os.makedirs(os.path.dirname(SERVED_ARCHIVE), exist_ok=True)
                    with io.open(SERVED_ARCHIVE, "a", encoding="utf-8") as out:
                        out.write(line + "\n")
                        out.flush()
                        os.fsync(out.fileno())
                    os.remove(p)
            except OSError:
                pass
    except Exception:
        pass


def _already_injected(session_id, node_ids, *, record=True):
    """Return the subset of node_ids already injected for this session, and record
    the rest. Repetition guard (2026-09-01): the same loop prompt re-fired one
    identical block four times in a session - pure context waste. Fail-safe: any
    error returns an empty set (inject as before, never suppress on a broken read)."""
    try:
        if not session_id:
            return set()
        now = time.time()
        path = _session_shard(session_id)
        rec = {}
        try:
            with io.open(path, encoding="utf-8") as f:
                rec = json.load(f)
        except FileNotFoundError:
            rec = {}
        except ValueError:
            # A half-written shard belongs to this session alone: drop it and start clean rather than
            # leaving the guard dead the way the shared file's corruption did on 2026-09-01.
            if not record:
                return set()
            try:
                os.remove(path)
            except OSError:
                pass
            rec = {}
        if not isinstance(rec, dict) or now - float(rec.get("ts", 0) or 0) >= SESSION_STATE_TTL_S:
            rec = {}
        seen = set(rec.get("ids") or [])
        dup = set(node_ids) & seen
        if not record:
            return dup
        rec = {"ids": sorted(seen | set(node_ids))[:400], "ts": now}
        try:
            os.makedirs(SESSION_DIR, exist_ok=True)
        except OSError:
            pass
        # Per-process temp name: a shared ".tmp" was the 2026-09-01 race.
        tmp = "%s.tmp.%d.%d" % (path, os.getpid(), int(now * 1000) % 1000000)
        with io.open(tmp, "w", encoding="utf-8") as f:
            json.dump(rec, f)
        try:
            os.replace(tmp, path)
        except OSError:
            # Lost the replace race (Windows sharing violation): the other writer's
            # state wins this round; drop our temp and never leave it behind.
            try:
                os.remove(tmp)
            except OSError:
                pass
        _prune_session_shards(now, sample=0.01)
        return dup
    except Exception:
        return set()



def _prefilter_revision_nodes(store, prefilter_ids, max_revision_nodes=4096):
    """Load candidate revisions without letting the lexical cut revive history.

    SUPERSEDES points from newer to older. Its incoming closure must be present
    before retrieve() decides which immutable revision is current. A missing
    endpoint quarantines its connected revision family; it never enables an old
    record as a fallback. This does not transfer lexical scores to new wording.

    Read cost (2026-10-12): the closure needs only the kind and the two ends of the SUPERSEDES, PROVES and REFUTES
    edges, and the return keeps only the edges with both ends in the pool, so _revision_edge_reader reads those three
    kinds through the kind index and builds the pool's own edges from their rows, in the same created_at, id order.
    Building all 17,500 edges as objects to keep a few hundred cost 0.43-0.50 s of every math prompt; this costs about
    0.02 s. The whole-edge read stays as the fallback whenever the reader cannot match it exactly.
    """
    from math_brain.models import EdgeKind

    fast = _revision_edge_reader(store, EdgeKind)
    if fast is None:
        edges = list(store.iter_edges())
        rel, pool_edges = edges, None
    else:
        edges = None
        rel, pool_edges = fast
    revisions = [e for e in rel if e.kind is EdgeKind.SUPERSEDES]
    newer, connected = {}, {}
    for edge in revisions:
        newer.setdefault(edge.target_id, set()).add(edge.source_id)
        connected.setdefault(edge.source_id, set()).add(edge.target_id)
        connected.setdefault(edge.target_id, set()).add(edge.source_id)
    wanted = set(prefilter_ids)
    frontier = list(wanted)
    expanded = 0
    while frontier:
        old_id = frontier.pop()
        for new_id in newer.get(old_id, ()):
            if new_id not in wanted:
                expanded += 1
                if expanded > max_revision_nodes:
                    raise ValueError("revision closure exceeds its bounded budget")
                wanted.add(new_id)
                frontier.append(new_id)
    # ANSWER CLOSURE (2026-09-20): a candidate that is the TARGET of a proves/refutes edge brings the edge's source
    # into the pool. The store held six of his results as claims with typed edges to the open-problem nodes and
    # the hook served the question alone, because the prefilter's lexical cut had the question and the ranker
    # never saw the answer. Bounded: at most ANSWER_CLOSURE_MAX sources per candidate, from the whole edge list
    # (already in memory above). Does not transfer scores; the ranker still judges the loaded node.
    try:
        _amax = int(os.environ.get("MATHBRAIN_ANSWER_CLOSURE_MAX", "3"))
    except Exception:
        _amax = 3
    if _amax > 0:
        answers = {}
        for edge in rel:
            if edge.kind is EdgeKind.PROVES or edge.kind is EdgeKind.REFUTES:
                answers.setdefault(edge.target_id, []).append(edge.source_id)
        for qid in list(wanted):
            # A superseded source is never loaded as an answer (2026-09-27, account03/B07). This closure runs after the
            # revision walk above, so an old revision added here arrives without its newer revision and without the
            # SUPERSEDES edge that marks it historical (the return keeps only edges with both ends loaded), and
            # retrieve() would rank it as current. A relation is bound to the revision it was written against: the
            # head is loaded through its own proves/refutes edge when it has one, and when it has none the question is
            # served without an answer card. Filtering before the cap also keeps an old revision from taking a slot
            # its current answer needed. The 24 Sep export held 7 answer edges from superseded sources, 5 with no
            # current relation (four of them REFUTED cards whose revisions say CANDIDATE REFUTATION, written on 22 Sep
            # against revisions already superseded on 13 Sep); the live store had rebound all 7 by 27 Sep, and on it
            # the loaded pools differ only by old revisions that retrieve() already hid (checked on every answer target).
            for src in [s for s in answers.get(qid, ()) if s not in newer][:_amax]:
                if src not in wanted:
                    expanded += 1
                    if expanded > max_revision_nodes:
                        break
                    wanted.add(src)
    loaded = store.get_nodes(sorted(wanted))
    blocked = wanted - set(loaded)
    frontier = list(blocked)
    while frontier:
        absent = frontier.pop()
        for neighbor in connected.get(absent, ()):
            if neighbor in wanted and neighbor not in blocked:
                blocked.add(neighbor)
                frontier.append(neighbor)
    # Original candidates keep their order; deterministic revisions follow.
    order = list(dict.fromkeys(prefilter_ids))
    order.extend(sorted(wanted - set(order)))
    nodes = [loaded[nid] for nid in order if nid in loaded and nid not in blocked]
    keep = {node.id for node in nodes}
    if pool_edges is not None:
        return nodes, pool_edges(keep)
    return nodes, [e for e in edges if e.source_id in keep and e.target_id in keep]


def _revision_edge_reader(store, EdgeKind):
    """(closure records, pool-edge loader) for _prefilter_revision_nodes, or None for its whole-edge read.

    The records are the SUPERSEDES, PROVES and REFUTES edges, not retracted, in created_at, id order, carrying only
    kind, source_id and target_id - everything the two closures read. The loader returns the non-retracted edges with
    both ends in `keep`, built by the store's own row reader in created_at, id order: the list the whole-edge read
    filtered down to. None (the whole-edge read) when the store holds an edge kind that is not exactly one of
    EdgeKind's values (that read raises UnknownEdgeKindError as before), when the store has no row reader, or when a
    query fails. Not matched: the whole-edge read parses every edge, so one malformed edge row anywhere raised; here
    only the pool's edges are parsed."""
    import sqlite3
    from collections import namedtuple

    con = store.connection
    if not callable(getattr(store, "_edge_from_row", None)):
        return None
    try:
        values = {k.value for k in EdgeKind}
        for row in con.execute("SELECT DISTINCT kind FROM edges").fetchall():
            if row[0] not in values:
                return None
        retracted = store.retracted_edge_ids()
        three = (EdgeKind.SUPERSEDES.value, EdgeKind.PROVES.value, EdgeKind.REFUTES.value)
        rows = con.execute("SELECT id, kind, source_id, target_id FROM edges WHERE kind IN (?, ?, ?) "
                           "ORDER BY created_at, id", three).fetchall()
    except sqlite3.Error:
        return None
    rec = namedtuple("RevisionEdge", "kind source_id target_id")
    rel = [rec(EdgeKind(r[1]), r[2], r[3]) for r in rows if r[0] not in retracted]

    def pool_edges(keep):
        if not keep:
            return []
        ids = sorted(keep)
        rows = None
        if all(isinstance(i, str) and i.isascii() for i in ids):
            blob = json.dumps(ids)
            try:
                # the index serves source_id; +target_id keeps the planner from probing every (source, target) pair
                rows = con.execute("SELECT * FROM edges WHERE source_id IN (SELECT value FROM json_each(?)) "
                                   "AND +target_id IN (SELECT value FROM json_each(?)) ORDER BY created_at, id",
                                   (blob, blob)).fetchall()
            except sqlite3.Error:
                rows = None
        if rows is None:
            return [e for e in store.iter_edges() if e.source_id in keep and e.target_id in keep]
        warned = set()
        out = []
        for row in rows:
            if row["id"] in retracted or row["source_id"] not in keep or row["target_id"] not in keep:
                continue
            edge = store._edge_from_row(row, skip_unknown=False, warned=warned)
            if edge is not None:
                out.append(edge)
        return out

    return rel, pool_edges



_FLAT_MATH_SYMBOLS = frozenset(u"≤≥∥∑∫√∈")


def _structure_flattened(rendered_text):
    """True when the harvest dropped this statement's fractions, exponents and subscripts.

    Measured 2026-09-15 over the whole store: 560 claims carry real mathematical symbols while
    containing no '/', no '^', no '_' and no 'frac' - 7.1 pct of all claims, and 53.9 pct of the
    harvested-arXiv tier. Eight drawn at random were all genuinely damaged, and the worst of them
    change the claim rather than merely uglifying it: `C_d (1 + LD/eta)^d` is served as
    "Cd 1 + LD eta d", and `N^{R(s1)+R(s2)}` as "N R(s1)+R(s2)", where an exponent has silently
    become a product. An agent reading that card cannot tell an exponent was ever there.

    Deliberately ONE WORD on the hint line, and only on the cards it fires for. The tag was cut to a
    third of its length on 09-13 because hints were displacing mathematics; this does not reopen
    that - a clean card is untouched. The check is cheap and conservative: it misses partial damage
    (a statement keeping one '/' elsewhere passes), so it under-reports rather than over-warns.
    """
    try:
        if not any(ch in _FLAT_MATH_SYMBOLS for ch in (rendered_text or "")):
            return False
        return not any(tok in rendered_text for tok in ("/", "^", "_", "frac"))
    except Exception:
        return False


def _source_is_excerpt(node, rendered_text):
    """Compare the rendered content with the complete projected source text."""
    title = " ".join((node.title or "").split())
    body = " ".join((node.body or "").split())
    if _node_meta(node).get("claim_kind") and len(title) >= LEGACY_CLAIM_TITLE_CHARS:
        assumes = "; ".join(part.strip() for part in (node.body or "").splitlines() if part.strip())
        full = title + (" || Assumes: " + assumes if assumes else "")
    elif _node_meta(node).get("claim_kind") and " Assumes: " in body:
        statement, assumes = body.rsplit(" Assumes: ", 1)
        full = statement + " || Assumes: " + assumes
    else:
        full = body
    return rendered_text != full


# PITFALL LINES (MATHBRAIN_PITFALL_LINES, campaign 2026-09-24). A served result that the store ties to a Pitfall note
# by a FAILS_UNDER edge carries that pitfall's title as one extra line on its card - the title IS the lesson ("Lax-
# Milgram Needs Coercivity on the Whole Space, Not Mere Positivity or Injectivity"), and the id lets the reader open
# the counterexample. The measured harm of a served statement is that it makes a wrong use of the result look
# legitimate (B12: citation, scope and sharpness faults lost ground under notes); the pitfall is the half of the
# result that says where it stops. Rendering only: the chosen cards, their order and every gate are untouched, a
# line is added only while the block keeps every card it had, at most one per card and two per block.
PITFALL_LINES = os.environ.get("MATHBRAIN_PITFALL_LINES", "1").strip() not in ("0", "off", "false", "")
PITFALL_MAX_BLOCK = 2
# the plain-question coverage floor's rare-term exemption (arm cand68; see the floor in main)
COV_RARE_EXEMPT = os.environ.get("MATHBRAIN_COV_RARE_EXEMPT", "1").strip() not in ("0", "off", "false", "")
try:
    COV_RARE_DF = int(os.environ.get("MATHBRAIN_COV_RARE_DF", "5") or 5)
except ValueError:
    COV_RARE_DF = 5
# ONE LINE PER RECORD PER BLOCK (MATHBRAIN_PITFALL_LINE_DEDUP, default on; arm cand67): two cards tied to the same pitfall showed
# its line twice; the second card now takes its next record, or none.
PITFALL_LINE_DEDUP = os.environ.get("MATHBRAIN_PITFALL_LINE_DEDUP", "1").strip() not in ("0", "off", "false", "")


# CORRECTION LINES (MATHBRAIN_CORRECTION_LINES, campaign 2026-09-24). A served card that rests (DEPENDS_ON) on a card
# whose statement was mathematically corrected since - the edge carried forward by the supersession with
# premise_corrected - says so: "corrected since: <title> (<id>)". An attempt on an open problem that the store has
# since answered (a REFUTES edge into the problem, or the problem superseded by its resolution) says "answered since".
# Scoped on purpose: REFUTES carries three meanings in this store (a conjecture answered NO, a route that fails, an
# inconsistency with a stronger axiom), so a refutation is reported only when the premise is an open problem, never
# for a theorem a scar "refutes" (Kingman's theorem rests on Fekete's lemma, which is true). Rendering only, under the
# same rule as the pitfall lines: at most one line per card, two per block, and only while every card still fits.
CORRECTION_LINES = os.environ.get("MATHBRAIN_CORRECTION_LINES", "1").strip() not in ("0", "off", "false", "")

# SAME RESULT (MATHBRAIN_SAME_RESULT, campaign mb10d 2026-09-24 23:0x, arm cand31). The store holds our memory-born card
# and the research ledger's card for ten named problems (Janson 18.4 and 18.10, Aldous-Fill 4.30 and 7.27, Lyons-Peres
# 17.18, ...), tied since this campaign by a semantic_association edge whose metadata says relation same_result
# (tools/link_twins.py). Without the tie two of the three slots of a block went to one result in about half the blocks
# that served either, and the two disagree on standing (ours "answered NO", the ledger "CANDIDATE REFUTATION" since its
# 13 Sep re-ingest). With it the finished block keeps the first card of a pair and drops the later one (on the hook path
# after every swap and reorder, which can put a twin back; on --query after the integrity check), and the kept card
# names the other as a record line, "same result on record: <title> (<id>)", so the standing the reader does not see in
# full still reaches them. A ledger revision after the tie is still recognised: the edge carries the ledger's logical
# id, and the record line names the ledger's current card for it.
SAME_RESULT = os.environ.get("MATHBRAIN_SAME_RESULT", "1").strip() not in ("0", "off", "false", "")


def _retraction_skip(con, col):
    """SQL that drops edges retracted as wrong (store.retract_edge); '' while the store has never retracted one."""
    if con.execute("select 1 from sqlite_master where type = 'table' and name = 'edge_retractions'").fetchone():
        return " and %s not in (select edge_id from edge_retractions)" % col
    return ""


def _same_result_twins(ids, heads=False, sources=None):
    """node id -> ids of its same-result twins: the edge's two ends plus the cards among `ids` that carry the edge's
    ledger logical id, and with heads=True also the store's current cards that carry it (a ledger revision written
    after the tie). A set passed as `sources` receives the edges' source ids (our memory-born cards). Never raises."""
    import sqlite3
    out = {}
    if not ids:
        return out
    try:
        con = sqlite3.connect("file:%s?mode=ro" % str(STORE_PATH).replace(chr(92), "/"), uri=True)
        try:
            rows = con.execute("select source_id, target_id, metadata_json from edges where kind = 'semantic_association' "
                               "and metadata_json like '%same_result%'" + _retraction_skip(con, "id")).fetchall()
            if not rows:
                return out
            marks = ",".join("?" * len(ids))
            by_lid = {}
            for nid, m in con.execute("select id, metadata_json from nodes where id in (%s)" % marks, list(ids)):
                try:
                    lid = json.loads(m or "{}").get("logical_id") or ""
                except Exception:
                    lid = ""
                if lid:
                    by_lid.setdefault(lid, set()).add(nid)
            if heads:
                # Heads are looked up only for the groups that touch `ids` (2026-10-12): a group reaches a card of
                # `ids` through an end of its edge or through a card of `ids` carrying its ledger id (by_lid holds
                # exactly those here), and the one heads=True caller, _pitfalls_for, reads tw[c] for c in ids alone.
                # The scan below reads the metadata of every node (0.12-0.18 s) and is now skipped when no group
                # touches the shown cards, which is nearly every prompt.
                want = set()
                idset = set(ids)
                for _s, _t, m in rows:
                    try:
                        _lid = json.loads(m or "{}").get("ledger_logical_id") or ""
                        if _s in idset or _t in idset or _lid in by_lid:
                            want.add(_lid)
                    except Exception:
                        pass
                want.discard("")
                if want:
                    q = ("select id, metadata_json from nodes where metadata_json like '%logical_id%' "
                         "and id not in (select target_id from edges where kind = 'supersedes')")
                    for nid, m in con.execute(q):
                        try:
                            lid = json.loads(m or "{}").get("logical_id") or ""
                        except Exception:
                            lid = ""
                        if lid in want:
                            by_lid.setdefault(lid, set()).add(nid)
        finally:
            con.close()
        for s, t, m in rows:
            if sources is not None:
                sources.add(s)
            try:
                lid = json.loads(m or "{}").get("ledger_logical_id") or ""
            except Exception:
                lid = ""
            group = {s, t} | by_lid.get(lid, set())
            for a in group:
                out.setdefault(a, set()).update(group - {a})
    except Exception:
        return {}
    return out


FINAL_TITLE_DEDUP = os.environ.get("MATHBRAIN_FINAL_TITLE_DEDUP", "1").strip() not in ("0", "", "off", "false")


def _drop_title_twins(chosen):
    """One card per accent-folded title on the finished block (MATHBRAIN_FINAL_TITLE_DEDUP, arm cand64): after every gate, so a
    fold can never drop the copy a later floor would have kept; a statement replaces an encyclopedia opening; no refill."""
    if not FINAL_TITLE_DEDUP or len(chosen) < 2:
        return chosen
    kept, pos = [], {}
    for h in chosen:
        k = _title_key(getattr(h.node, "title", ""), fold=True)
        if k and k in pos:
            i = pos[k]
            if _is_lede(kept[i].node) and not _is_lede(h.node):
                kept[i] = h
            continue
        if k:
            pos[k] = len(kept)
        kept.append(h)
    return kept


def _drop_later_twins(chosen):
    """One card per same-result pair (see SAME_RESULT): the pair keeps the place of its first card in block order, and
    the card shown there is our memory-born one (the same_result edge's source) - it names the construction and how it
    was checked, where the ledger's card carries the problem text and its 13 Sep "CANDIDATE" wording, which the kept
    card's record line still shows."""
    if not SAME_RESULT or len(chosen) < 2:
        return chosen
    src = set()
    tw = _same_result_twins([h.node.id for h in chosen], sources=src)
    if not tw:
        return chosen
    kept, placed = [], set()
    for h in chosen:
        if h.node.id in placed:
            continue
        group = [x for x in chosen if x.node.id == h.node.id or x.node.id in tw.get(h.node.id, ())]
        pick = next((x for x in group if x.node.id in src), h)
        kept.append(pick)
        placed.update(x.node.id for x in group)
    return kept


def _pitfalls_for(card_ids):
    """card id -> [(label, id, title)]: corrections and answered problems first, then FAILS_UNDER pitfalls/scars."""
    import sqlite3
    out = {}
    if not card_ids:
        return out
    con = sqlite3.connect("file:%s?mode=ro" % str(STORE_PATH).replace(chr(92), "/"), uri=True)
    marks = ",".join("?" * len(card_ids))
    try:
        skip_e, skip_f = _retraction_skip(con, "e.id"), _retraction_skip(con, "f.id")
        if CORRECTION_LINES:
            q = ("select e.source_id, n.id, n.title from edges e join nodes n on n.id = e.target_id "
                 "where e.kind = 'depends_on' and e.source_id in (%s) and e.metadata_json like '%%premise_corrected%%' "
                 "and n.id not in (select target_id from edges where kind = 'supersedes')") % marks + skip_e
            for src, nid, title in con.execute(q, list(card_ids)):
                out.setdefault(src, []).append(("corrected since", nid, " ".join((title or "").split())))
            q = ("select e.source_id, r.id, r.title from edges e join nodes p on p.id = e.target_id "
                 "join edges f on f.target_id = p.id and f.kind = 'refutes' join nodes r on r.id = f.source_id "
                 "where e.kind = 'depends_on' and e.source_id in (%s) and lower(p.title) like 'open problem%%' "
                 "and r.id not in (select target_id from edges where kind = 'supersedes')") % marks + skip_e + skip_f
            for src, rid, title in con.execute(q, list(card_ids)):
                out.setdefault(src, []).append(("answered since", rid, " ".join((title or "").split())))
        q = ("select e.source_id, n.id, n.title from edges e join nodes n on n.id = e.target_id "
             "where e.kind = 'fails_under' and e.source_id in (%s) "
             "and n.id not in (select target_id from edges where kind = 'supersedes')") % marks + skip_e
        for src, pid, title in con.execute(q, list(card_ids)):
            out.setdefault(src, []).append(("on record", pid, " ".join((title or "").split())))
        if SAME_RESULT:
            tw = _same_result_twins(list(card_ids), heads=True)
            want = sorted(set(x for c in card_ids for x in tw.get(c, ())) - set(card_ids))
            if want:
                q = ("select id, title from nodes where id in (%s) "
                     "and id not in (select target_id from edges where kind = 'supersedes')") % ",".join("?" * len(want))
                current = dict(con.execute(q, want).fetchall())
                for cid in card_ids:
                    for x in sorted(tw.get(cid, ())):
                        if x in current:
                            out.setdefault(cid, []).append(("same result on record", x, " ".join((current[x] or "").split())))
    finally:
        con.close()
    return out


def _with_pitfall_lines(header, pieces, emitted_ids, budget, prompt):
    """Re-assemble the block with record lines added greedily, never at the cost of a card."""
    found = _pitfalls_for(emitted_ids)
    if not found:
        return _assemble_note_block(header, pieces, budget)
    pw = _content_words(prompt or "")
    emitted = set(emitted_ids)
    lines = {}
    rank = {"corrected since": 0, "answered since": 0, "same result on record": 0.5, "on record": 1}
    for cid in emitted_ids:
        cands = [(lab, pid, t) for lab, pid, t in found.get(cid, []) if pid not in emitted]
        if not cands:
            continue
        cands.sort(key=lambda lpt: (rank.get(lpt[0], 2), -len(_content_words(lpt[2]) & pw), len(lpt[2])))
        lines[cid] = cands if PITFALL_LINE_DEDUP else cands[:1]
    base_block, base_ids = _assemble_note_block(header, pieces, budget)
    added = 0
    shown = set()
    cur = dict(pieces)
    for cid in emitted_ids:
        if added >= PITFALL_MAX_BLOCK or cid not in lines:
            continue
        # a record already shown on another card of this block is not shown again (MATHBRAIN_PITFALL_LINE_DEDUP)
        pick = next((c for c in lines[cid] if not (PITFALL_LINE_DEDUP and c[1] in shown)), None)
        if pick is None:
            continue
        lab, pid, title = pick
        trial = dict(cur)
        trial[cid] = cur[cid] + "\n    %s: %s (%s)" % (lab, title, pid)
        tb, tids = _assemble_note_block(header, [(i, trial.get(i, p)) for i, p in pieces], budget)
        if tids == base_ids:
            cur, base_block = trial, tb
            added += 1
            shown.add(pid)
    return base_block, base_ids


def _assemble_note_block(header, pieces, budget):
    """Fit whole rendered cards, including their hints, and return emitted IDs.

    A rendered card can itself contain an explicitly identified source excerpt.
    Outer budgeting must not remove that disclosure or its trust/assumption hint.
    """
    if budget <= 0 or len(header) > budget:
        return "", []
    chosen, used, omitted = [], len(header), 0
    for nid, piece in pieces:
        if used + 1 + len(piece) <= budget:
            chosen.append((nid, piece))
            used += 1 + len(piece)
        else:
            omitted += 1
    if omitted:
        while True:
            notice = "[math-brain: %d note(s) omitted whole for the context budget.]" % omitted
            if used + 1 + len(notice) <= budget:
                break
            if not chosen:
                return (notice if len(notice) <= budget else ""), []
            _, removed = chosen.pop()
            used -= 1 + len(removed)
            omitted += 1
    else:
        notice = ""
    block = header + "".join("\n" + piece for _, piece in chosen)
    if notice:
        block += "\n" + notice
    return block, [nid for nid, _ in chosen]


# CLAUSE SPLIT (2026-09-12 night). A question that names two parameter sets in two clauses ("N_A avoids (1,2) and
# (3,0), and N_B avoids (-2,1)") is a query with two lexical centres: sibling rows that partially match BOTH sets outscore
# either row that matches one fully, and the three-slot block fills with near-misses. Measured on the L3 two-fact bank
# (30 questions, both rows needed): whole question serves both rows on 4 / one on 15 / none on 11; asking each
# tuple-carrying clause on its own (the prompt minus the other tuple clauses) and merging the blocks round-robin serves
# both on 11 / 15 / 4. Scope is deliberately narrow: two or three clauses that carry numeric tuples (a lone (0,0) does
# not count), prompt under 1,500 characters - one archived prompt in 34,070 qualifies, so this is a block-composition
# rule for multi-fact questions, not a new way to fire. MATHBRAIN_CLAUSE_SPLIT=0 opts out.
CLAUSE_SPLIT = os.environ.get("MATHBRAIN_CLAUSE_SPLIT", "1") != "0"
# default ON since 2026-09-12 03:xx: on the LIVE store (1,565 harvest.arxiv nodes) the cap read unnamed served 0.764 -> 0.775,
# rank-1 0.551 -> 0.584, silent 0.146 -> 0.124, named and absent-target fires unchanged, locate reach unchanged
# (campaign constants served 17 -> 16 of 20). MATHBRAIN_HARVEST_CAP=0 opts out.
HARVEST_CAP = os.environ.get("MATHBRAIN_HARVEST_CAP", "1") == "1"   # see _is_campaign; measured on a sandbox and live 2026-09-12
# PRE-FLOOR (2026-09-12): apply the on-topic floor to the candidate list BEFORE the three slots are chosen, not only
# after. Traced on the locate bank: the ranker's top three all sat under the floor while floor-passing cards sat at
# ranks 4-13 (a rooted-NAE question: three concept notes at prefilter 15-19 against a floor of 19.2, the two notes
# carrying the value at 21.6 ranked 11th and 13th), so the block went out empty; on another the prefilter's favourite
# at 72.8 ranked 4th and lost its slot to two cards the gate then removed. Flag until measured on every bank.
# DEFAULT 3 since 2026-09-12 03:4x (his clock). Measured the same hour on every bank against the mode-0 base (unnamed
# 69/89 served, absent 27/40, campaign value-in-block 12/20): mode 1 -> 72 / 28 / 14 plus a third-slot fill on 24 of 40
# open-problem questions; mode 2 -> 73 / 31 / 17 (the empty-block rescue fires where the abstention lives); mode 3 ->
# 71 / 27 / 15, named path and reach unchanged, health 11/6. Modes 1 and 2 stay available for his decision.
PREFLOOR_MODE = int(os.environ.get("MATHBRAIN_PREFLOOR", "3") or 0)
PREFLOOR = PREFLOOR_MODE == 1          # mode 1: the whole candidate list is floored before the three slots are chosen
# mode 2 (RESCUE): the block is chosen as before; then (a) an EMPTY gated block is re-chosen among the floor-passing
# candidates, and (b) the prefilter's favourite, when it passes the floor and is not in the block, takes a free slot.
# Mode 1 measured 2026-09-12 03:0x: campaign served 16 -> 18 of 20 and value-in-block 12 -> 14, but it filled a third
# slot with an off-target hub on 24 of the 40 open-problem questions (cards 87 -> 111); mode 2 is the same gain
# without the fill - only an empty block or a missing lexical favourite changes anything.
_TUPLE_RE = re.compile(r"\(\s*-?\d+\s*,\s*-?\d+\s*\)")
_CLAUSE_RE = re.compile(r"(?<=[.;?!])\s+|,\s+and\s+|;\s+")


def _clause_subqueries(prompt):
    if len(prompt) > 1500:
        return []
    parts = [p for p in _CLAUSE_RE.split(prompt) if p and p.strip()]
    tup = [i for i, p in enumerate(parts)
           if [t for t in _TUPLE_RE.findall(p) if t.replace(" ", "") != "(0,0)"]]
    if len(tup) == 1:
        # ... unless ONE clause carries two named parameter sets: "Let S = {(2,1), (3,0)} and T = {(2,1)}". _CLAUSE_RE
        # cuts on ", and" but not on a bare " and ", so both sets stayed in one clause, the split bailed, and the whole
        # question went to the ranker as one long query. Measured 2026-09-13: 5 of the 1,000-item test's items - all
        # saw_growthdiff - were the only ones the brain served NOTHING for, with three right-family cards selected and
        # a whole-prompt score of 23-29 against the on-topic floor of 25. Split that clause on " and " when each half
        # assigns its own set, and each half earns its own sub-query.
        _i = tup[0]
        _halves = [h for h in re.split(r"\s+and\s+(?=[A-Za-z]\w*\s*=\s*\{)", parts[_i]) if h.strip()]
        if len(_halves) >= 2 and all(_TUPLE_RE.search(h) for h in _halves):
            parts = parts[:_i] + _halves + parts[_i + 1:]
            tup = [j for j, p in enumerate(parts)
                   if [t for t in _TUPLE_RE.findall(p) if t.replace(" ", "") != "(0,0)"]]
    if not 2 <= len(tup) <= 3:
        return []
    # the clauses AFTER the last tuple clause ("What is N_A - N_B? Give the exact ...") name both quantities and pull
    # the sub-query back toward the whole question; dropping them read 11 -> 13 of 30 both-served offline
    last = max(tup)
    return [" ".join(p for j, p in enumerate(parts) if j == i or (j not in tup and j < last)) for i in tup]


# A result the argument NAMES: a run of capitalised words (hyphens and accents allowed) in front of the word that
# says what kind of thing it is. "Vitali-Caratheodory theorem", "Lax-Milgram theorem", "Rellich-Kondrachov
# compactness", "Holder's inequality", and the "theorem of X" form.
_KIND_WORD = (r"theorem|lemma|inequality|principle|criterion|identity|formula|decomposition|representation|"
              r"compactness|embedding|convergence|rule|estimate|construction")
# WIDE form (MATHBRAIN_SUBQ_WIDE=1, default OFF): accepts the curly apostrophe ("Egorov’s theorem" as an author
# actually types it), a lowercase particle inside the name ("von Neumann mean ergodic theorem"), and a short
# lowercase tail. It finds strictly MORE real names - 45 pct of the pivots a prompt names, against far fewer -
# and it still measures WORSE at the default three cards, because every extra sub-query spends a card slot that
# an item-specific card was using: 57 items / 79-83 results against 60 / 85, in every sub-query budget from 1 to
# 4 and in both prompt and proper-name order. It PAYS only when the block is widened with it:
#   5 cards + 6,000 chars, wide extractor:  60 items / 96 results   (against 60 / 85 at the default)
# So the scarce resource is the slot, not the query. Default narrow; switch both dials together or neither.
_NAMED_RESULT_RE_WIDE = re.compile(
    r"(?:\b((?:(?:von|van|de|del|della|der|den|du|la|le|ten|ter|of)\s+)?"
    r"[A-Z][\w'’À-ſ]+(?:[-‐-―][A-Z][\w'’À-ſ]+)*"
    r"(?:\s+(?:(?:von|van|de|del|della|der|den|du|la|le|ten|ter)\s+)?[A-Z][\w'’À-ſ]+){0,2}"
    r"(?:\s+[a-z][a-z-]{2,}){0,2})\s*['’]?s?\s+(" + _KIND_WORD + r")\b)"
    r"|(?:\b(" + _KIND_WORD + r")\s+of\s+([A-Z][\w'’À-ſ]+(?:[-‐-―][A-Z][\w'’À-ſ]+)*)\b)"
    r"|(?:\b((?:[a-z][a-z-]{2,}\s+){1,3})(" + _KIND_WORD + r")\b)"
)
_NAMED_RESULT_RE = re.compile(
    # a proper name in front of the kind word: "Lax-Milgram theorem", "Holder's inequality"
    r"(?:\b([A-Z][\w'’À-ſ]+(?:(?:--|[-‐-―])[A-Z][\w'’À-ſ]+)*"
    r"(?:\s+[A-Z][\w'’À-ſ]+){0,2})\s*'?s?\s+(" + _KIND_WORD + r")\b)"
    # "theorem of X"
    r"|(?:\b(" + _KIND_WORD + r")\s+of\s+([A-Z][\w'’À-ſ]+(?:[-‐-―][A-Z][\w'’À-ſ]+)*)\b)"
    # a DESCRIPTIVE name in running prose, which is how most results are actually cited mid-argument:
    # "dominated convergence theorem", "the monotone convergence theorem", "weak maximum principle".
    # One to three lowercase words, none of them a filler word, in front of the kind word.
    r"|(?:\b((?:[a-z][a-z-]{2,}\s+){1,3})(" + _KIND_WORD + r")\b)"
)
# PLURAL KINDS AND MORE KINDS AFTER A PROPER NAME (MATHBRAIN_SUBQ_KINDS2, default on; campaign mb10d, 25 Sep, arm cand51). On
# the proof-audit bank 33 of the 42 named results the store holds and the block missed with a slot to spare were never extracted:
# "interior Schauder estimates" (a plural), "Fredholm alternative", "Neumann series", "Lusin's property (N)", "Weierstrass
# M-test", "Moser iteration". Every kind now matches its plural; after a capitalised name only, alternative, series, property,
# test, iteration, method, bound, transform, calculus, expansion, duality, conjecture and trick count as kinds too. The descriptive
# (lowercase) pattern keeps the old kinds, so "upper bound" and "this method" never become names. MATHBRAIN_SUBQ_KINDS2=0 restores
# the old kind list.
SUBQ_KINDS2 = os.environ.get("MATHBRAIN_SUBQ_KINDS2", "1").strip() not in ("0", "off", "false", "")
_KIND_WORD_PL = (r"(?:theorem|lemma|principle|decomposition|representation|embedding|rule|estimate|construction|formula)s?|"
                 r"inequalit(?:y|ies)|identit(?:y|ies)|criteri(?:on|a)|formulae|compactness|convergence")
_KIND_WORD_NAMED = (_KIND_WORD_PL + r"|alternatives?|series|propert(?:y|ies)|(?:[A-Z]-)?tests?|iterations?|methods?|bounds?|"
                    r"transforms?|calculus|expansions?|duality|conjectures?|tricks?")
if SUBQ_KINDS2:
    _NAMED_RESULT_RE = re.compile(
        r"(?:\b([A-Z][\w'’À-ſ]+(?:(?:--|[-‐-―])[A-Z][\w'’À-ſ]+)*"
        r"(?:\s+[A-Z][\w'’À-ſ]+){0,2})\s*'?s?\s+(" + _KIND_WORD_NAMED + r")\b)"
        r"|(?:\b(" + _KIND_WORD_PL + r")\s+of\s+([A-Z][\w'’À-ſ]+(?:[-‐-―][A-Z][\w'’À-ſ]+)*)\b)"
        r"|(?:\b((?:[a-z][a-z-]{2,}\s+){1,3})(" + _KIND_WORD_PL + r")\b)"
    )
# Filler that must never become the name of a result: "the following theorem", "this lemma", "our estimate".
_NAMED_FILLER = {"the", "this", "that", "these", "those", "a", "an", "our", "its", "his", "her", "their",
                 "following", "above", "below", "next", "last", "first", "second", "third", "same", "such",
                 "any", "each", "every", "some", "one", "two", "another", "previous", "preceding", "said",
                 "stated", "standard", "classical", "usual", "well-known", "known", "general", "main",
                 "key", "basic", "simple", "obvious", "trivial", "elementary", "similar", "corresponding",
                 "and", "or", "but", "with", "without", "by", "from", "for", "the-same", "apply", "applying",
                 "using", "use", "used", "invoke", "invoking", "cite", "citing", "see", "recall", "note",
                 "hence", "thus", "so", "since", "because", "then", "now", "here", "where", "which", "whose",
                 "step", "claim", "argument", "proof", "part", "case", "above-mentioned", "aforementioned",
                 # verbs that lead into a result word and would otherwise become its "name"
                 "has", "have", "had", "gives", "give", "given", "giving", "yields", "yield", "implies",
                 "imply", "shows", "show", "states", "state", "holds", "hold", "follows", "follow",
                 "satisfies", "satisfy", "produces", "produce", "provides", "provide", "becomes", "become",
                 "makes", "make", "equals", "equal", "obtain", "obtains", "obtained", "get", "gets",
                 "is", "are", "be", "being", "been", "was", "were", "does", "do", "did", "can", "may"}
_NAMED_SKIP = {"the", "this", "it", "we", "if", "then", "hence", "so", "thus", "by", "since", "let", "for",
               "and", "but", "define", "fix", "here", "now", "step", "claim", "argument", "suppose", "apply",
               "applying", "adding", "note", "every", "each", "both", "first", "second", "third", "finally"}


def _named_result_subqueries(prompt, limit=None):
    """The results a long argument NAMES, as their own queries (2026-09-12 15:4x).

    A proof audit hands the hook two thousand words; the prefilter scores all of them, so the pivot theorem's name
    is one token among hundreds and its node never reaches the ranked list. Measured on the 77 authored proof-audit
    items: the store HOLDS Holder (4 titles), the open mapping theorem (4), Lax-Milgram (5), Rellich (4), Fekete (3),
    and 16 maximum-principle nodes, yet the served block reached only 30 pct of the results those arguments use.
    Asking for each named result on its own is the query the whole-prompt score cannot express.
    """
    if limit is None:
        # How many named results one argument may ask for. Each sub-query spends a card slot, so this trades
        # against the item-specific cards already chosen; measured 2026-09-13 on bank db640d76f2ff at a budget
        # that never trips (items / named results reached, 77 items, 310 results):
        #   3 cards, limit 4: 57 / 83      3 cards, limit 2: see below      5 cards + 6,000 chars, limit 4: 60 / 96
        # against 60 / 85 for the narrower extractor at 3 cards.
        try:
            # 8 since arm cand54 (was 4): with the fifth-slot rule below a fifth distinct named result can be served, and
            # with four slots and no fifth-slot rule a higher limit measured neutral (107-108 of 310 at limits 6 and 8).
            limit = int(os.environ.get("MATHBRAIN_SUBQ_LIMIT", "8") or 8)
        except Exception:
            limit = 4
    seen, out = set(), []
    _re = _NAMED_RESULT_RE_WIDE if os.environ.get("MATHBRAIN_SUBQ_WIDE", "0") == "1" else _NAMED_RESULT_RE
    _leadstrip = os.environ.get("MATHBRAIN_SUBQ_LEADSTRIP", "1").strip() not in ("0", "off", "false", "")
    for m in _re.finditer(prompt):
        if m.group(1):
            name, kind = m.group(1), m.group(2)
            # A SENTENCE WORD IS NOT PART OF THE NAME (MATHBRAIN_SUBQ_LEADSTRIP, default on; campaign mb10d, 25 Sep, arm
            # cand42). An argument cites a result as "By Fatou's lemma", "By Fubini's theorem", "Then Holder's inequality";
            # the capitalised run takes the sentence word in ("By Fatou's" + lemma), and the skip test below used to drop
            # the whole match - matches do not overlap, so the name itself was never tried. On the 77 proof-audit items
            # the store held a card for 58 results named in the argument that the block did not serve. Strip the
            # leading skip and filler words and keep the name.
            if _leadstrip:
                _ws = name.split()
                while _ws and (_ws[0].lower() in _NAMED_SKIP or _ws[0].lower() in _NAMED_FILLER):
                    _ws = _ws[1:]
                name = " ".join(_ws)
        elif m.group(4):
            name, kind = m.group(4), m.group(3)
        else:
            name, kind = (m.group(5) or "").strip(), m.group(6)
            # a descriptive name is only a name once the filler words are stripped off the front
            words = [w for w in name.split() if w.lower() not in _NAMED_FILLER]
            name = " ".join(words)
        if not name:
            continue
        if os.environ.get("MATHBRAIN_SUBQ_DASH", "1").strip() not in ("0", "off", "false", ""):
            name = name.replace("--", "-")
        head = name.split()[0].lower()
        if head in _NAMED_SKIP or head in _NAMED_FILLER or len(name) < 4:
            continue
        q = ("%s %s" % (name, kind)).strip()
        k = q.lower()
        if k in seen:
            continue
        seen.add(k)
        # A PROPER NAME OUTRANKS A DESCRIPTION (2026-09-13). The cut used to take the first `limit` matches in
        # document order, so on a Navier-Stokes argument "satisfying energy inequality", "energy identity" and
        # "energy inequality" filled the budget while Banach-Alaoglu and Gagliardo-Nirenberg - both named in the
        # prompt, both in the store - were never asked for. Rank by how much of a NAME the match carries: a
        # capitalised or hyphenated proper name first, a bare lowercase description last; ties keep prompt order.
        # MEASURED 2026-09-13, bank db640d76f2ff, 3 cards, budget 600: ranking proper names ahead of descriptions
        # READ WORSE than plain prompt order - 57 items / 83 results against 60 / 85, and 51 / 57 when only the
        # single most name-like result was asked for. The first result an argument names is usually the one it
        # hinges on; a proper name mentioned later is often incidental. So prompt order it is, and the ranking is
        # kept only behind MATHBRAIN_SUBQ_RANK=1 for anyone who wants to re-test it against a wider card budget.
        if os.environ.get("MATHBRAIN_SUBQ_RANK", "0") == "1":
            _proper = 2 if (m.group(1) or m.group(4)) else 0
            _cap = min(sum(1 for w in name.split() if w[:1].isupper()), 2)
            out.append((-(_proper + _cap), m.start(), q))
        else:
            out.append((0, m.start(), q))
    # NAMES THE PATTERN CANNOT SEE (MATHBRAIN_SUBQ_CURATED, default on; campaign mb10d, 25 Sep, arm cand56): a reviewed list, each
    # entry a name a paper's proof uses without a kind word and a store card carries. It sits where it first appears in the text;
    # a result the pattern already extracted ("Fubini's theorem") is not asked for twice.
    # A DOUBLE-BARRELLED NAME AFTER A CITATION VERB (MATHBRAIN_SUBQ_BARE_DOUBLE, default on; arm cand57): "apply Cauchy--Schwarz in
    # the law of X_I", "by Borel--Cantelli" - a proof's name for the result, with no kind word for the pattern above to see.
    if SUBQ_BARE_DOUBLE:
        for _m2 in _BARE_DOUBLE_RE.finditer(prompt):
            _nm = _fold_dashes(_m2.group(1)).replace("--", "-")
            if _nm.lower() in seen or any(_named_title_match(_nm, _q) for _, _, _q in out):
                continue
            seen.add(_nm.lower())
            out.append((0, _m2.start(1), _nm))
    if SUBQ_CURATED:
        # a curated name in a LaTeX reference key counts where the reference sits (MATHBRAIN_CURATED_LABELS, arm cand57):
        # \eqref{eq:plancherel} is the paper's own name for the step
        _labels = [(m.start(), re.sub(r"[:_./-]+", " ", m.group(1))) for m in _LABEL_KEY_RE.finditer(prompt)] if CURATED_LABELS else []
        for _i, (_rx, _canon) in enumerate(_CURATED_NAMED):
            _mm = _rx.search(prompt)
            _pos = _mm.start() if _mm else None
            if _pos is None:
                for _lp, _lk in _labels:
                    if _CURATED_LABEL[_i].search(_lk):
                        _pos = _lp
                        break
            if _pos is None or _canon.lower() in seen:
                continue
            # an extracted name that is PART of the curated one ("value decomposition", cut from "Singular-value
            # decomposition") gives way to it in place (MATHBRAIN_CURATED_SUPERSEDE, arm cand57)
            if CURATED_SUPERSEDE:
                _sub = [j for j, (_, _, _q) in enumerate(out)
                        if _named_title_match(_q, _canon) and not _named_title_match(_canon, _q)]
                if _sub:
                    _a, _b, _ = out[_sub[0]]
                    out[_sub[0]] = (_a, _b, _canon)
                    seen.add(_canon.lower())
                    continue
            if any(_named_title_match(_canon, _q) or _named_title_match(_q, _canon) for _, _, _q in out):
                continue
            seen.add(_canon.lower())
            out.append((0, _pos, _canon))
        # an AMBIGUOUS name in a reference key, resolved by the proof's own notation (MATHBRAIN_LABEL_CONTEXT, arm cand66)
        if LABEL_CONTEXT:
            for _krx, _ctx, _canon in _LABEL_CONTEXT:
                if _canon.lower() in seen or not _ctx.search(prompt):
                    continue
                _pos = next((_lp for _lp, _lk in _labels if _krx.search(_lk)), None)
                if _pos is None:
                    continue
                if any(_named_title_match(_canon, _q) or _named_title_match(_q, _canon) for _, _, _q in out):
                    continue
                seen.add(_canon.lower())
                out.append((0, _pos, _canon))
    out.sort()
    return [q for _, _, q in out[:limit]]


SUBQ_CURATED = os.environ.get("MATHBRAIN_SUBQ_CURATED", "1").strip() not in ("0", "off", "false", "")
# (pattern, the name asked for). Technique names whose store card carries them, and surnames that name one result in a proof.
# Not here: Cauchy, Schur, Weyl, Young, Picard, Markov, Riesz (several results each), Delta, Gamma (Greek letters), integration by
# parts (the store's only card is Gaussian integration by parts, a different result).
_CURATED_NAMED = [
    (re.compile(r"\bSchur\s+complements?\b", re.IGNORECASE), "Schur complement lemma"),
    (re.compile(r"\bchanges?\s+of\s+variables?\b", re.IGNORECASE), "change of variables"),
    (re.compile(r"\bsingular[- ]value\s+decompositions?\b", re.IGNORECASE), "singular value decomposition"),
    (re.compile(r"\bFubini\b"), "Fubini's theorem"),
    (re.compile(r"\bTonelli\b"), "Tonelli's theorem"),
    (re.compile(r"\bParseval\b"), "Parseval's identity"),
    (re.compile(r"\bH(?:ö|o|oe)lder\b"), "Hölder's inequality"),
    (re.compile(r"\bArzel(?:à|a)\b"), "Arzelà-Ascoli theorem"),
    # arm cand57: every Plancherel card in the store is the L2 theorem; Cauchy-Schwarz is cited bare as often as not
    (re.compile(r"\bPlancherel\b"), "Plancherel theorem"),
    (re.compile(r"\bCauchy(?:--|[-‐-―])Schwarz\b"), "Cauchy-Schwarz inequality"),
]
_CURATED_LABEL = [re.compile(_rx.pattern, re.IGNORECASE) for _rx, _ in _CURATED_NAMED]
# AN AMBIGUOUS NAME IN A REFERENCE KEY (MATHBRAIN_LABEL_CONTEXT, default on; arm cand66). "Weyl" names seven results in the store and
# stays off the list above; in a reference key ("lem:weyl") of a proof that writes singular values or eigenvalues it is Weyl's
# inequality - a literal reading would take Weyl's LEMMA, the elliptic regularity result. (key pattern, context the proof must
# carry, the name asked for); reference keys only, never running text.
LABEL_CONTEXT = os.environ.get("MATHBRAIN_LABEL_CONTEXT", "1").strip() not in ("0", "off", "false", "")
_LABEL_CONTEXT = [
    (re.compile(r"\bweyl\b", re.IGNORECASE),
     re.compile(r"\bs_\{?[a-z0-9]|\\sigma_|\\lambda_|\bsingular values?\b|\beigenvalues?\b", re.IGNORECASE),
     "Weyl's inequality"),
]
CURATED_LABELS = os.environ.get("MATHBRAIN_CURATED_LABELS", "1").strip() not in ("0", "off", "false", "")
CURATED_SUPERSEDE = os.environ.get("MATHBRAIN_CURATED_SUPERSEDE", "1").strip() not in ("0", "off", "false", "")
_LABEL_KEY_RE = re.compile(r"\\(?:ref|cref|Cref|eqref|autoref)\{([^{}]{2,80})\}")
# OFF by default (arm cand57's count, 25 Sep): in his archive the form is mostly an AUTHOR PAIR - "from Higson--Prodan", "by
# Hammond--Miller", "use the Kumjian--Renault" - in pasted referee reports (16 prompts), and "via Quasi-Gaussianity" is not a name;
# its one bank case, "apply Cauchy--Schwarz", is covered by the curated list.
SUBQ_BARE_DOUBLE = os.environ.get("MATHBRAIN_SUBQ_BARE_DOUBLE", "1").strip() not in ("0", "off", "false", "")
# ON since arm cand65 as an EXTRACTOR only - it acts on prompts already read as arguments; as a CITATION CUE it stays off
# (MATHBRAIN_BARE_DOUBLE_CUE): as a cue it made three of his pasted referee reports arguments (arm cand57's count).
BARE_DOUBLE_CUE = os.environ.get("MATHBRAIN_BARE_DOUBLE_CUE", "0").strip() not in ("0", "off", "false", "")
_BARE_DOUBLE_RE = re.compile(
    r"\b(?:[Bb]y|[Ff]rom|[Uu]sing|[Uu]se|[Aa]pply|[Aa]pplying|[Aa]pplied|[Vv]ia|[Ii]nvoke|[Ii]nvoking)\s+(?:the\s+)?"
    r"([A-Z][a-zà-ÿ]+(?:(?:--|[-‐-―])[A-Z][a-zà-ÿ]+)+)(?![\w'’‐-―-])"
    r"(?!\s+(?:" + _KIND_WORD_NAMED + r")\b)")

_QUOTED_RE = re.compile(r"[\"“”]([^\"“”]{8,160})[\"“”]")


def _quoted_subqueries(prompt, limit=4):
    """Each quoted title in the prompt as its own query (2026-09-13 02:4x).

    A question that names several stored entries by title - 'the problems "A", "B" and "C"' - ranks two of them a
    hair apart, and the near-tie margin test reads that as no clear winner and silences the whole answer: 23 of the
    28 items the brain served nothing for on the 1,000-item test died exactly there, with the right family on top.
    The named-result extractor does not see them (a ledger title is not 'X theorem'); the clause split does not
    either (no tuples). Asking for each quoted title on its own is the query whose winner is not ambiguous.
    """
    seen, out = set(), []
    for m in _QUOTED_RE.finditer(prompt):
        q = " ".join(m.group(1).split())
        if len(q.split()) < 2:
            continue
        k = q.casefold()
        if k in seen:
            continue
        seen.add(k)
        out.append(q)
        if len(out) >= limit:
            break
    return out


TITLE_EXEMPT = os.environ.get("MATHBRAIN_TITLE_EXEMPT", "0") == "1"
_TITLE_STOP = frozenset(("the", "and", "for", "with", "its", "from", "that", "this", "campaign", "theorem",
                         "lemma", "proof", "case", "note", "result", "value", "which", "what", "how", "give",
                         "integer", "unknown", "decimal", "exact", "number", "does", "was", "were", "are"))


# HOUSE ACRONYMS (2026-09-23, MATHBRAIN_ALIASES, default 0 until measured). The store writes a result the way the
# campaign that found it talks - "The best known FEI lower bound is 6.5218 (Numaro 2026)" - and a reader asks the way
# a paper does - "the best known lower bound on the Fourier entropy-influence constant". Neither text carries the
# other's words, so the record card lost to a 3D-Ising card on "lower bound" (4.851 against 4.878) and the margin
# test went silent. Each pair below is expanded in BOTH directions, on the RETRIEVAL query only (the gate and every
# test of the prompt's own words still read what was typed), and only when one side is present. Keep the table to
# acronyms whose meaning in this house is unambiguous.
_ALIASES = (
    (r"\bFEI\b", "Fourier entropy influence", r"\bFourier[- ]entropy[- ](?:versus[- ](?:total[- ])?)?influence\b", "FEI"),
    (r"\bKKL\b", "Kahn Kalai Linial", r"\bKahn[- ]Kalai[- ]Linial\b", "KKL"),
    (r"\bLTFs?\b", "linear threshold function", r"\blinear threshold functions?\b", "LTF"),
    (r"\bMMPP\b", "Markov modulated Poisson process", r"\bMarkov[- ]modulated Poisson\b", "MMPP"),
    (r"\bFPP\b", "first passage percolation", r"\bfirst[- ]passage percolation\b", "FPP"),
    (r"\bSAWs?\b", "self avoiding walk", r"\bself[- ]avoiding walks?\b", "SAW"),
    (r"\bLYM\b", "Lubell Yamamoto Meshalkin", r"\bLubell[- ]Yamamoto[- ]Meshalkin\b", "LYM"),
    # 5 Nov (claude-1e0a23c2): "MP law" asks for Marchenko-Pastur; bare "MP" stays unexpanded (MP polynomials in the store),
    # and the reverse never fires (the store's titles already spell the name out).
    (r"\bMP[- ](?:law|distribution|density|limit)\b", "Marchenko-Pastur law; Marchenko Pastur law", r"(?!x)x", ""),
)
_ALIAS_RX = [(re.compile(a), exp_a, re.compile(b, re.I), exp_b) for a, exp_a, b, exp_b in _ALIASES]


def _expand_aliases(prompt):
    """The retrieval query: the prompt plus the other spelling of every house acronym it uses. Unchanged when the
    prompt names none, or when MATHBRAIN_ALIASES=0."""
    # ON since 2026-09-23 06:2x: the hook-path bank is byte-identical on vs off (73/89, 86/89, false fires 28/40,
    # median 1.05 vs 1.07 s); the FEI record question moves from a percolation card to the Numaro record first.
    if os.environ.get("MATHBRAIN_ALIASES", "1") == "0" or not prompt:
        return prompt
    add = []
    for rx_a, exp_a, rx_b, exp_b in _ALIAS_RX:
        if rx_a.search(prompt) and not rx_b.search(prompt):
            add.append(exp_a)
        elif rx_b.search(prompt) and not rx_a.search(prompt):
            add.append(exp_b)
    if os.environ.get("MATHBRAIN_NUMWORD", "1") != "0":
        add.extend(o for o in _numword_others(prompt) if o not in add)
    return prompt if not add else "%s (%s)" % (prompt, "; ".join(add))


# A hyphen term written with a number word or a digit ("zero-one", "0-1", "Three-Lines", "rank-1") shares no token
# with its other form under either tokenizer: math_prefilter keeps "zero-one" whole and splits "0-1" into "0" and
# "-1". So the other form rides along, like an alias, when its tokens are all in the prefilter index (31 Oct, offline
# through math_prefilter.select, porting/analysis_20261031/prefilter_numword_ab.py: 49 titles with such a term, the
# swapped wording better 4 / worse 0, Hadamard Three-Lines 37 -> 2; the exact titles 0 moved). ON since 31 Oct 22:29:
# the hook-path bank is byte-identical on vs off (218 jobs, 0 changed, though it holds zero-one, one-dimensional and
# two-dimensional prompts), and of 8 swapped-form probes "Hadamard 3-lines theorem: ..." goes from silent to the
# Three-Lines card at rank 1, the other 7 unchanged. "Hewitt-Savage zero-one" stays unserved: "0" and "1" have no
# postings, so a 0-1 card cannot be reached through the prefilter by either spelling. MATHBRAIN_NUMWORD=0 turns it off.
_NUMW = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten")
_NUMW_HY = re.compile(r"\b[A-Za-z0-9]+(?:-[A-Za-z0-9]+)+\b")
_NUMW_WORD = re.compile(r"(?<![a-z])(%s)(?![a-z])" % "|".join(_NUMW), re.I)
_NUMW_DIG = re.compile(r"(?<![0-9])(10|[0-9])(?![0-9])")


def _numword_others(prompt):
    if not _NUMW_WORD.search(prompt) and not _NUMW_DIG.search(prompt):
        return []
    try:
        import math_prefilter
        post = (math_prefilter.load(STORE_PATH) or {}).get("post") or {}
    except Exception:
        return []
    out = []
    for m in _NUMW_HY.finditer(prompt):
        t = m.group(0)
        o = _NUMW_WORD.sub(lambda w: str(_NUMW.index(w.group(1).lower())), t)
        if o == t:
            o = _NUMW_DIG.sub(lambda d: _NUMW[int(d.group(1))], t)
        toks = math_prefilter.tokens(o) if o != t else []
        if toks and all(x in post for x in toks) and o not in out:
            out.append(o)
    return out


_NAME_STOP = frozenset("""what which how why when where the for in if give state under does do is are let suppose
prove show explain describe a an and or of on to with without that this these those it its be been being can could
should would may might must there here they them their we you your i as at by from into over about than then so
such but not no yes one two both each any all some most more less many much other another same different new old
first second third theorem lemma proposition corollary inequality bound law rule method setting space function
transform process variable measure convergence distribution problem question result statement form version case
example define definition concept pdf""".split())


def _name_tokens(prompt):
    """The prompt's own identity tokens - hyphenated proper names (Tracy-Widom, Hanson-Wright), plain capitalised
    words, acronyms (KKL, LYM) - minus grammar capitals and mathematical furniture. Read from the prompt alone, so
    the signal is causal; the bank's stems are never consulted. Measured 2026-09-13 (memory
    covering_the_questions_names_is_the_best_ranking_signal_measured_2026_09_13). 'pdf' is stopped because the
    locate-bank boilerplate capitalises it on every question."""
    txt = unicodedata.normalize("NFKD", prompt or "")
    txt = "".join(c for c in txt if not unicodedata.combining(c))
    out = set()
    for m in re.finditer(r"\b([A-Z][a-zA-Z]+(?:-[A-Z][a-zA-Z]+)+)\b", txt):
        for part in m.group(1).split("-"):
            if part.lower() not in _NAME_STOP and len(part) > 2:
                out.add(part.lower())
    for m in re.finditer(r"\b([A-Z][a-z]{2,})\b", txt):
        w = m.group(1).lower()
        if w not in _NAME_STOP:
            out.add(w)
    for m in re.finditer(r"\b([A-Z]{2,6})\b", txt):
        w = m.group(1).lower()
        if w not in _NAME_STOP:
            out.add(w)
    return out


DEFN_BELOW_RESULT = os.environ.get("MATHBRAIN_DEFN_BELOW_RESULT", "1").strip() not in ("0", "off", "false")


def _defn_below_result_rerank(chosen):
    """A card the store records as a DEFINITION never sits above one it records as a theorem or lemma.

    The narrow rule out of the 2026-09-13 decisions memo, item 4, which measured it at rank-1 129 -> 131 of 178
    and said plainly that it needed nobody's permission - it is a change to the ranking code. It was never landed.
    Re-measured on the 09-14 gate rows before building it: of the 23 near-misses (the right card served but not
    first) the card that beats it is another THEOREM 9 times, a DEFINITION 7, a concept 5, a lemma 1. So seven is
    the ceiling here and the memo's two is the floor; the nine theorem-beats-theorem cases no kind field can touch.

    MEASURED AND LANDED 2026-09-14, both arms on the hook-path bank, the control arm reproducing the morning's
    clean numbers exactly. (An earlier pair was thrown away: it ran while the box was starving the hook, and
    q_hinted silence had tripled. That - the hook's own baseline - is the validity check, not the governor, which
    read `ok` throughout.)

        q_content rank-1   53/89 -> 57/89          q_hinted rank-1   80/89 -> 81/89
        served 72/89 and 84/89, silent 9 and 3, false fire 27/40 and strict 20/33: ALL UNCHANGED

    Five questions changed and every one is a GAIN, none lost - checked question by question, because class
    totals hid a real loss in a different experiment the same morning. Banach fixed point and Baire category were
    both being beaten by the card "Complete metric space"; the implicit function theorem and Kahn-Kalai-Linial,
    both forms, by their own definitions. The memo estimated +2 against a three-card block; the block carries four
    cards now, and the re-measured near-miss population held seven definition-blocked cases, of which five moved.

    REORDER ONLY, and stable: the same cards go out in the same set, equal-class cards keep the ranker's own
    order, so hit and silence cannot move and only the first card can change. That is deliberate - the memo's own
    predecessor work showed that FILTERING this pool is not neutral (removing candidates moves the margin test's
    leader and the gates computed relative to it, measured 155/310 against 160/310), so a kind signal has to act
    on the order and never on the set. The kind is read from the store's recorded field, which exists on 7,819 of
    7,865 nodes; a card with no recorded kind is treated as a result, never demoted on a missing value."""
    try:
        if not DEFN_BELOW_RESULT or not chosen or len(chosen) < 2:
            return chosen
        def _kind(h):
            m = _node_meta(h.node)
            return str(m.get("claim_kind") or m.get("kind") or getattr(h.node, "kind", "") or "").lower()
        RESULT = ("theorem", "lemma", "proposition", "corollary")
        if not any(_kind(h) in RESULT for h in chosen):
            return chosen                      # nothing to rank a definition below
        return [h for _, _, h in sorted(
            ((1 if _kind(h) == "definition" else 0, i, h) for i, h in enumerate(chosen)),
            key=lambda t: (t[0], t[1]))]
    except Exception:
        return chosen


TITLE_COVER = os.environ.get("MATHBRAIN_TITLE_COVER", "0").strip() in ("1", "on", "true")
_TITLE_COVER_STOP = frozenset("""the for and that with this from under over into what which how why when where
does do is are let suppose prove show state give explain describe about than then them they their its it be
been being can could should would may might must there here we you your as at by of on to in if or a an one two
both each any all some most more less many much other another same different new old first second third such but
not no yes""".split())


def _title_cover_rerank(prompt, chosen):
    """Order the cards a block already carries by how much of the QUESTION each title accounts for.

    The rule above it asks whether a title carries every NAME the question uses, which is the right question when
    the asker names the theorem and inert when they describe it - and describing it is exactly what the hard half
    of the bank does (rank-1 is 89.9 pct on questions that name the result and 59.6 pct on questions that do not).
    This asks the other direction and over ordinary words, not only capitalised ones: of the question's content
    words, what share does this title contain? Tokens match on a five-character prefix, so "converge" reaches
    "convergence" and "planar" reaches "planarity".

    REORDER ONLY, and that is the point: the same cards go out, so a block that was served is still served, a
    silence is still a silence, and no gate that judged the block is disturbed. Only the FIRST card can change,
    which is the only thing rank-1 measures. Stable: equal coverage keeps the ranker's own order.

    The same signal, question-side and smooth rather than thresholded, took the memory brain's deliberate recall
    from hit@1 28 to 32 of 56 the same morning (recall_ranks_by_how_much_of_the_question_the_name_covers).
    Behind MATHBRAIN_TITLE_COVER, default OFF until measured here."""
    try:
        if not TITLE_COVER or not chosen or len(chosen) < 2:
            return chosen
        q = unicodedata.normalize("NFKD", prompt or "")
        q = "".join(c for c in q if not unicodedata.combining(c)).lower()
        want = [w for w in re.findall(r"[a-z]{3,}", q) if w not in _TITLE_COVER_STOP]
        if not want:
            return chosen

        def cover(h):
            t = unicodedata.normalize("NFKD", str(getattr(h.node, "title", "") or ""))
            t = "".join(c for c in t if not unicodedata.combining(c)).lower()
            tt = set(re.findall(r"[a-z]{3,}", t))
            if not tt:
                return 0.0
            hit = 0
            for w in want:
                if w in tt or (len(w) >= 5 and any(len(u) >= 5 and u[:5] == w[:5] for u in tt)):
                    hit += 1
            return hit / float(len(want))
        return [h for _, _, h in sorted(
            ((-cover(h), i, h) for i, h in enumerate(chosen)), key=lambda t: (t[0], t[1]))]
    except Exception:
        return chosen


def _name_cover_rerank(prompt, chosen):
    """Within the cards a block already carries, a card may pass another ONLY if its title covers EVERY name token
    of the question. Stable: full-coverage cards keep their own order ahead of the rest; with no name token, or no
    card covering all of them, nothing moves. Re-orders only, never adds, drops or rescores, so every gate that
    judged the block still holds.

    Why this variant (2026-09-13, 89-theorem bank, 178 questions, in-sample): plain 'more coverage wins' read +6
    rank-1 but split into +7 on questions that NAME the theorem and -1 on questions that describe it; this
    every-token form reads +5 on the named half and 0 on the unnamed half (129 -> 134 of 178). On the independent
    locate bank (110 reorderable rows, scored by node id) it changed no row - inert, not harmful. Behind
    MATHBRAIN_NAME_COVER (default 1 once the hook-path A/B and the regression gate confirmed it)."""
    try:
        if not chosen or len(chosen) < 2:
            return chosen
        want = _name_tokens(prompt)
        if not want:
            return chosen

        def _covers_all(h):
            t = unicodedata.normalize("NFKD", str(getattr(h.node, "title", "") or ""))
            t = "".join(c for c in t if not unicodedata.combining(c)).lower()
            tt = set(w for w in re.findall(r"[a-z0-9]+", t) if len(w) > 2)
            return want <= tt
        full = [h for h in chosen if _covers_all(h)]
        if not full or len(full) == len(chosen):
            return chosen
        return full + [h for h in chosen if not _covers_all(h)]
    except Exception:
        return chosen


def _prompt_names_card(prompt, title):
    """True when the prompt carries two or more distinctive words of this card's title.

    Not a similarity score - a presence test on the title's own vocabulary, so it can only fire when the asker
    has written the words the card is filed under.
    """
    low = unicodedata.normalize("NFKD", (prompt or "")).casefold()
    low = "".join(c for c in low if not unicodedata.combining(c))
    t = unicodedata.normalize("NFKD", (title or "")).casefold()
    t = "".join(c for c in t if not unicodedata.combining(c))
    words = {w for w in re.findall(r"[a-z][a-z0-9-]{3,}", t) if w not in _TITLE_STOP}
    if len(words) < 2:
        return False
    return sum(1 for w in words if re.search(r"\b%s" % re.escape(w[:max(4, len(w) - 2)]), low)) >= 2


def _trace_answers(stage, chosen):
    """stderr trace of the served ids at a stage, only under MATHBRAIN_TRACE_ANSWERS=1 (a harness aid, silent in production)."""
    if os.environ.get("MATHBRAIN_TRACE_ANSWERS", "0") == "1":
        try:
            sys.stderr.write("[trace-answers] %s: %s\n" % (stage, [h.node.id[:16] for h in (chosen or [])]))
        except Exception:
            pass


def _attach_answers(chosen, pool_hits, edges, cap_extra=2):
    """Serve the answer with the question.

    A served node that is the TARGET of a proves/refutes edge from a claim in the ranked pool brings that claim in
    ahead of it. Measured 2026-09-20 on his six results of the week (Aldous-Fill 4.48(a) and 4.49(ii), Bradley Q5,
    the maximal Kemeny constant, Ding-Lee-Peres 5.2, Ross's second conjecture): the store held every answer as a
    claim with a typed edge to the open-problem node, and four of the six questions were served the question node
    alone - the ranker prefers the shorter "Open problem: ..." card to a 700-1,100-character theorem statement.
    A question-shaped prompt asked of a store that holds the answer must serve the answer.

    RESTRICTED on purpose: only proves/refutes edges (never analogous_to, cites or semantic_association), only
    sources already in the pool (the closure in _prefilter_revision_nodes loads them), never a source that is
    itself superseded, at most `cap_extra` companions per block, and nothing is rescored or reordered among the
    cards the ranker chose. A served node with no such edge leaves the block byte-identical. Off with
    MATHBRAIN_ANSWER_COMPANION=0.
    """
    try:
        if not chosen or not edges or os.environ.get("MATHBRAIN_ANSWER_COMPANION", "1") == "0":
            return chosen
        from math_brain.models import EdgeKind
        by_target = {}
        superseded = set()
        for e in edges:
            if e.kind is EdgeKind.PROVES or e.kind is EdgeKind.REFUTES:
                by_target.setdefault(e.target_id, []).append(e.source_id)
            elif e.kind is EdgeKind.SUPERSEDES:
                superseded.add(e.target_id)
        if not by_target:
            return chosen
        pool = {h.node.id: h for h in (pool_hits or [])}
        served = {h.node.id for h in chosen}
        out, added = [], 0
        _after = os.environ.get("MATHBRAIN_ANSWER_AFTER_RESULT", "1").strip() not in ("0", "off", "false", "")
        _reft = {e.target_id for e in edges if e.kind is EdgeKind.REFUTES}
        for h in chosen:
            # SANDBOX sb25 (claude-1e0a23c2, 5 Nov 01:5x): the answer leads unless the card is a PROVED stated result (no refutes edge in, no question word in the title). A served
            # card that is itself a stated result keeps the lead and its proof ingredient follows it.
            _q = True
            if _after:
                _tt = str(getattr(h.node, "title", "") or "").strip().lower()
                _tk = str(getattr(h.node, "kind", "") or "").lower()
                _q = (_tk == "open_question" or h.node.id in _reft
                      or any(_w in _tt for _w in ("problem", "question", "conjecture", "?")))
            _comp = []
            for src in by_target.get(h.node.id, ()):
                if added >= cap_extra or src in served or src in superseded or src not in pool:
                    continue
                if str(getattr(pool[src].node, "kind", "") or "").lower() in _UNSERVED_KINDS:
                    continue
                _comp.append(pool[src])
                served.add(src)
                added += 1
            if _q:
                out.extend(_comp)
                out.append(h)
            else:
                out.append(h)
                out.extend(_comp)
        return out
    except Exception:
        return chosen


# A NAMED SUB-QUERY KEEPS ONLY THE RESULT IT NAMED (MATHBRAIN_NAMED_TITLEMATCH, default on; campaign mb10d, 25 Sep, arm cand46).
# The rule is the proof-audit bank's (eval_pivot_reach.named_in) on one title: the first two content words of the name, each by a
# stem of four or more letters, or a one-word name as a whole word.
NAMED_TITLEMATCH = os.environ.get("MATHBRAIN_NAMED_TITLEMATCH", "1").strip() not in ("0", "off", "false", "")
_NTM_STOP = {"the", "a", "an", "of", "for", "and", "in", "on", "to", "by", "with", "its", "form", "version", "s"}


NAMED_ACCENT_FOLD = os.environ.get("MATHBRAIN_NAMED_ACCENT_FOLD", "1").strip() not in ("0", "off", "false", "")


def _fold_accents(text):
    if not NAMED_ACCENT_FOLD:
        return text or ""
    t = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in t if not unicodedata.combining(c))


# INEQUALITY, ESTIMATE AND BOUND ARE ONE KIND for a named result (MATHBRAIN_NAMED_KIND_SYNONYMS, default on; arm cand65): "the
# Caccioppoli estimate" is the store's "Caccioppoli Inequality". Theorem and lemma stay distinct.
NAMED_KIND_SYNONYMS = os.environ.get("MATHBRAIN_NAMED_KIND_SYNONYMS", "1").strip() not in ("0", "off", "false", "")
_KIND_SYN = frozenset(("inequality", "inequalities", "estimate", "estimates", "bound", "bounds"))
_KIND_SYN_RE = r"\b(?:inequalit|estimat|bound)"


def _named_title_match(name, title):
    ws = [w for w in re.findall(r"[a-z][a-z-]{1,}", _fold_accents(name).lower()) if w not in _NTM_STOP]
    if not ws:
        return False
    low = _fold_accents(title).lower()
    if len(ws) == 1:
        return re.search(r"\b%s\b" % re.escape(ws[0]), low) is not None
    return all((re.search(_KIND_SYN_RE, low) is not None) if (NAMED_KIND_SYNONYMS and w in _KIND_SYN)
               else (re.search(r"\b%s" % re.escape(w[:max(4, len(w) - 2)]), low) is not None) for w in ws[:2])


# A NAMED SUB-QUERY WHOSE CANDIDATES ARE TITLED WITH THE NAME PASSES THE GATES (MATHBRAIN_SUBQ_EXACT_PASS, default on; campaign
# mb10d, 25 Sep, arm cand55). "Aubin–Lions lemma" had one candidate, the card titled "Aubin-Lions compactness lemma", and served
# nothing: 0.29 lexical on a long body against MIN_SUPPORT 0.5. When every surviving candidate carries every distinctive word of the
# name in its title, the choice is among them with the support and margin gates off; the prefilter floor and the anchor rule stay.
SUBQ_EXACT_PASS = os.environ.get("MATHBRAIN_SUBQ_EXACT_PASS", "1").strip() not in ("0", "off", "false", "")
# ... and passes the anchor rule too (MATHBRAIN_SUBQ_EXACT_ANCHOR, default on; arm cand67): the cited name is its anchor. The
# rule held "Weyl's inequality" to three shared words of a long proof that cites it by label and dropped it (qf_nine_23).
SUBQ_EXACT_ANCHOR = os.environ.get("MATHBRAIN_SUBQ_EXACT_ANCHOR", "1").strip() not in ("0", "off", "false", "")
# ... but only for the card the citation NAMES (MATHBRAIN_SUBQ_EXACT_TIGHT, default on; arm cand69). "Exact" above is containment,
# so a title may carry the cited words among others, and a surname names many results: "F. Riesz's theorem" matched "Riesz
# representation theorem for Hilbert spaces", a reverse-Hoelder citation matched the Carbery-Wright card, and past the anchor rule
# each took a named slot and pushed a keyed card out (pivot bank, arm cand67). A title whose head - the text before a colon or
# bracket - holds no distinctive word the citation lacks passes on the name; any other exact candidate meets the anchor rule.
SUBQ_EXACT_TIGHT = os.environ.get("MATHBRAIN_SUBQ_EXACT_TIGHT", "1").strip() not in ("0", "off", "false", "")
_TIGHT_NEUTRAL = frozenset(set(_TITLE_STOP) | set(_KIND_SYN) | {"principle", "formula", "identity", "conjecture", "law",
                                                                "criterion", "corollary", "proposition", "theorems", "lemmas"})
SUBQ_EPONYM_EXACT = os.environ.get("MATHBRAIN_SUBQ_EPONYM_EXACT", "1").strip() not in ("0", "off", "false", "")
# A POSSESSIVE NAME ASKS FOR BOTH FORMS (MATHBRAIN_SUBQ_POSSESSIVE, default on; campaign mb10d, 25 Sep, arm cand61). The prefilter
# keeps an apostrophe inside a token and "theorem" is a stop word, so the named sub-query "Plancherel's theorem" searched for the one
# token "plancherel's" and found no candidate at all, while the cards are titled "Plancherel theorem" (13 candidates). A named
# sub-query whose possessive form finds no candidate asks again with the plain name beside it (a fallback since arm cand62);
# nothing else reads the changed text.
SUBQ_POSSESSIVE = os.environ.get("MATHBRAIN_SUBQ_POSSESSIVE", "1").strip() not in ("0", "off", "false", "")
_POSSESSIVE_RE = re.compile(r"\b([A-Za-zÀ-ſ][A-Za-zÀ-ſ-]{2,})(['’])s\b")


def _name_parts(text):
    """The distinctive words of a name, hyphenated names by their parts, accents and dashes folded."""
    t = _fold_dashes(str(text or ""))
    if NAMED_ACCENT_FOLD:
        t = _fold_accents(t)
    out = set()
    for w in re.findall(r"[a-z][a-z-]{2,}", t.lower()):
        if w in _TIGHT_NEUTRAL:
            continue
        for part in w.split("-"):
            if len(part) >= 2 and part not in _TIGHT_NEUTRAL:
                out.add(part)
    return out


def _title_names_only(query, title):
    """True when a title's head names the cited result and nothing more: its distinctive words are the citation's."""
    try:
        qw = _name_parts(query)
        return bool(qw) and _name_parts(re.split(r"[:(\[]", str(title or ""), 1)[0]) == qw
    except Exception:
        return False


def _subquery_block(query, prompt=None, exact_pass=False):
    """The block one sub-query earns on its own: prefilter -> ranker -> _select -> the on-topic floor. Hits, not ids.

    `prompt` is the whole prompt the sub-query was cut from, so the anchor rule in _select can hold a card to the
    argument's own words (2026-09-22: the sub-query path is where a long proof's cards come from, and without the
    prompt the rule was inert there - a live capture served the same 101 cards with it on and off)."""
    import math_prefilter
    from math_brain.retrieval import retrieve
    from math_brain.store import ResearchStore
    _xp = bool(exact_pass and SUBQ_EXACT_PASS)

    def _pool(pq):
        ids = math_prefilter.select(STORE_PATH, pq, k=PREFILTER_K)
        if not ids:
            return None
        ps = dict(getattr(math_prefilter, "LAST_SCORES", {}) or {})
        qself = float(getattr(math_prefilter, "LAST_QSELF", 0.0) or 0.0)
        floor = min(GATE_FLOOR, GATE_FRAC * qself) if (GATE_FRAC > 0 and qself > 0) else GATE_FLOOR
        with ResearchStore.open_read_only(STORE_PATH) as store:
            store.connection.execute("BEGIN")
            nodes, edges = _prefilter_revision_nodes(store, ids)
        _set_pool_adjacency(edges)
        result = retrieve(query, nodes=nodes, edges=edges, limit=CANDIDATE_POOL, min_support_score=0.0, min_margin=0.0)
        hits = result.hits
        if PREFLOOR:
            hits = [h for h in hits if float(ps.get(h.node.id, 0.0)) >= floor]
        hits = _tuple_exact_first(query, hits)
        return ps, floor, edges, result, hits, _title_exact_first(query, hits, parts=_xp)

    got = _pool(query)
    # THE PLAIN NAME WHEN NO CANDIDATE IS TITLED WITH THE POSSESSIVE ONE (arm cand63): "Plancherel's theorem" found no candidate,
    # "Brouwer's theorem" found only cards that write "Brouwer's" - neither pool held a card titled with the result, and asking
    # again with the plain name beside the possessive finds it. Asked every time, the plain name widened every possessive sub-query
    # and cost the pivot bank two keyed results (arm cand61); asked on an empty pool only, it lost Brouwer (arm cand62).
    if (exact_pass and SUBQ_POSSESSIVE and _POSSESSIVE_RE.search(query or "")
            and (got is None or got[5] is got[4])):
        again = _pool(_POSSESSIVE_RE.sub(lambda m: m.group(1) + m.group(2) + "s " + m.group(1), query))
        if again is not None and (got is None or again[5] is not again[4]):
            got = again
    if got is None:
        return []
    ps, floor, edges, result, hits, _ex = got
    if _xp and _ex is not hits:
        # every candidate here is titled with the name the argument cited: the name is the anchor (MATHBRAIN_SUBQ_EXACT_ANCHOR)
        chosen = [h for h in (_select(_ex, apply_gates=False, title_query=query, prompt=prompt,
                                      anchor=not SUBQ_EXACT_ANCHOR) or [])
                  if float(ps.get(h.node.id, 0.0)) >= floor]
        if SUBQ_EXACT_ANCHOR and SUBQ_EXACT_TIGHT and prompt:
            # the name anchors only the card it names; a looser namesake shares the argument's words or goes, exactly as the
            # anchor rule's last step in _select would have judged it (no refill)
            _k = _anchor_min_shared(prompt)
            if _k:
                _pw = _anchor_words(prompt)
                chosen = [h for h in chosen
                          if _title_names_only(query, getattr(h.node, "title", "")) or _anchored(h.node, _pw, _k)]
    else:
        hits = _ex
        chosen = [h for h in (_select(hits, title_query=query, prompt=prompt) or []) if float(ps.get(h.node.id, 0.0)) >= floor]
    return _attach_answers(chosen, result.hits, edges)


def _title_exact_first(query, hits, parts=False):
    """When the sub-query IS a stored entry's title, that entry goes first.

    Measured 2026-09-13 on the L6 bank: of the cards missing from wide enumeration blocks, 73 of 73 were IN THE
    CANDIDATE POOL and simply not served - zero retrieval failures. Each named entry already gets its own
    sub-query and the merge takes the top hit of each, so a missing card means that sub-query's own top hit was
    something other than the entry the question named. Ranking by score is right for a question; it is wrong for
    a sub-query that is a verbatim title, where the answer is not "what scores highest near these words" but
    "the thing called this".

    The test is containment on distinctive words - the card's title must carry every one of the query's - which
    is the same rule the enumeration cap uses, so the two cannot disagree about whether the store has an entry.

    RESTRICTS, does not reorder, and that distinction is the whole function. `_select` re-sorts its input by
    rank score, so handing it a better order changes nothing - the lesson `_tuple_exact_first` below already
    records in its own docstring, and which I re-learned by writing the reordering version first and reading
    the neighbouring function afterwards. When some candidate is titled with the query, the choice is made
    AMONG those candidates; when none is, nothing changes and the ranker decides as before. Nothing is
    rescored and every gate downstream still judges whatever comes out.
    """
    try:
        _qs = _fold_dashes(str(query or ""))
        if parts and NAMED_ACCENT_FOLD:
            _qs = _fold_accents(_qs)
        qw = {w for w in re.findall(r"[a-z][a-z-]{2,}", _qs.lower()) if w not in _TITLE_STOP}
        # parts=True (a named result, arm cand55): a hyphenated name counts by its parts, so "Aubin-Lions lemma" is looked for
        _n = sum(len(w.split("-")) for w in qw) if parts else len(qw)
        # ONE PROPER NAME BEFORE "theorem" OR "lemma" (MATHBRAIN_SUBQ_EPONYM_EXACT, default on; arm cand57). Both words are stop
        # words here, so "Tonelli's theorem" had one distinctive word and was never looked for: the pool held Tonelli's theorem
        # and Fubini-Tonelli, and the block served "Fubini and Tonelli: Which Hypothesis Fails". A named sub-query of one
        # capitalised name and the kind word is looked for as a title holding both.
        _ep = None
        if _n == 1 and parts and SUBQ_EPONYM_EXACT and hits:
            _w = next(iter(qw))
            _kinds = set(re.findall(r"\b(theorem|lemma)s?\b", _qs.lower()))
            if _kinds and re.search(r"\b" + re.escape(_w[:1].upper() + _w[1:]) + r"\b", _qs):
                _ep = (_w, _kinds)
        if (_n < 2 and _ep is None) or not hits:
            return hits
        exact = []
        for h in hits:
            _ts = _fold_dashes(str(getattr(h.node, "title", "") or ""))
            if parts and NAMED_ACCENT_FOLD:
                _ts = _fold_accents(_ts)
            if _ep is not None:
                _tws = set(re.findall(r"[a-z]{2,}", _ts.lower()))
                if _ep[0] in _tws and _ep[1] <= _tws:
                    exact.append(h)
                continue
            tw = {w for w in re.findall(r"[a-z][a-z-]{2,}", _ts.lower()) if w not in _TITLE_STOP}
            if parts and NAMED_KIND_SYNONYMS:
                tw = {("<kind>" if w in _KIND_SYN else w) for w in tw}
                if tw and {("<kind>" if w in _KIND_SYN else w) for w in qw} <= tw:
                    exact.append(h)
                continue
            if tw and qw <= tw:
                exact.append(h)
        return exact if exact else hits
    except Exception:
        return hits


def _norm_tuples(text):
    """The parameter tuples in a text, normalised ("( -2, 1 )" -> "(-2,1)"), the origin excluded."""
    out = set()
    for m in _TUPLE_RE.finditer(text or ""):
        t = re.sub(r"\s+", "", m.group(0))
        if t != "(0,0)":
            out.add(t)
    return out


def _tuple_exact_first(query, hits):
    """Within a tuple-carrying sub-query, prefer the rows whose tuple SET is the query's.

    2026-09-12: on the two-fact bank the block for "avoid the sites (5,1), (6,0)" served the row for "(5,1)" alone - a
    proper subset with a shorter title - because the digits 6 and 0 weigh nothing in either scorer (17 of 30 pairs missed
    a row this way). The lexical scorers cannot see a set; this can. When some candidate carries every query tuple and
    no other, the block is chosen among those; else among the candidates carrying every query tuple; else unchanged.
    _select re-sorts by rank, so the choice is made by RESTRICTING the candidates, not by reordering them. Only the
    clause-split path calls this; a prompt without parameter tuples never reaches it.
    """
    try:
        qt = _norm_tuples(query)
        if not qt or not hits:
            return hits
        exact, superset = [], []
        for h in hits:
            nt = _norm_tuples((h.node.title or "") + " " + (h.node.body or ""))
            if qt <= nt:
                (exact if nt == qt else superset).append(h)
        return exact or superset or hits
    except Exception:
        return hits


# RELAYS AND PICTURE BATCHES ARE NOT A READER'S QUESTION (2026-09-23). Codex runs this hook directly from
# ~/.codex/hooks.json, so the dispatcher's task-notification filter (2026-09-23 round 5) never reaches it, and the
# world-history picture workers start one `codex exec` per batch of five pictures. Measured from surfaced.tsv after the
# numpy fix of 09-22 19:38: 91 of 102 live serves went to those batches, 76 of them one of two cards - "Finitely
# generated abelian group" (the prompt says "image generation tool" and "the generated file") and a Rademacher open gap.
# The gate lets them in on "field" + "measure" ("an open field notebook", "a tape measure"). A relayed agent message
# (<agent-message>, a cross-session message, "Another Claude session sent a message") is a peer's report, not a
# question put to the reader, and got the same treatment. The skip is anchored at the START of the prompt and names
# the picture-worker head only: "You are auditing a numbered mathematical argument" and every other agent-launched
# MATHS prompt keep their cards (prompt_origin.is_agent_authored matches the grader prompts too, so it is NOT used).
# MATHBRAIN_RELAY_SKIP=0 restores the old behaviour.
# 2026-09-24: the picture head is matched as "You are a/the picture maker", not one exact sentence. Workers headed "You
# are the picture maker for a cinematic history documentary" and "You are a picture maker for three history documentaries
# (the history of mathematics, ...)" slipped past the exact wording and got algebra cards on 09-23 at 16:51, 18:33 and
# 20:38 (surfaced.tsv), the history-of-mathematics batches looking most like mathematics of all.
_RELAY_OR_PICTURE_HEAD = re.compile(
    r"^\s*(?:﻿)?(?:<agent-message\b|Another Claude session sent a message\b|<cross-session-message\b"
    r"|<task-notification\b|<agent-notification\b|<hook_prompt\b|<system-reminder\b|<local-command-stdout\b"
    r"|<recommended_plugins\b|You are (?:a|the) picture maker\b)", re.I)
RELAY_SKIP = os.environ.get("MATHBRAIN_RELAY_SKIP", "1").strip() not in ("0", "off", "false")

# SESSION CONTEXT FOR CONTINUATIONS (MATHBRAIN_SESSION_CONTEXT, default 0 = OFF; 2026-09-23, round 6).
# T6 judged what this hook serves on 60 of his own math-thread prompts: every math question it missed while the store
# held the right card was a continuation ("keep working until you have a full proof", "use creative ideas to solve the
# full problem") - the prompt names no topic because the session already has one, so the gate refuses it and even an
# ungated retrieval finds nothing in its words. With the dial on, a prompt that passes the gate is remembered for its
# session (one small file per session, hashed id, three-hour life), and a prompt the gate refuses is served on itself
# plus that remembered prompt - but only when it carries a proof-or-problem cue and no admin cue (push, email, repo,
# render, thumbnail ...). Everything after the gate - prefilter, coverage, margins, dedup - runs unchanged on the
# combined text, so the route can only be as sure as the remembered prompt made it. The header says where the notes
# came from.
# MEASURED, AND REFUSED - keep it off (2026-09-23 13:46, t7/route_yield.py). Over his last 30 days (9,422 prompts) the
# route would fire 23 times - two a day on 12 days - and replayed as live sessions only 4 of the 23 get a card the
# session was not already shown: the prompt that set the context had been served, and the repeat guard drops those
# cards (9 of the route's 14 were in the context's own block). Widening the cue does not rescue it: the eligible prompts
# it leaves quiet are film, voice, cost and hook work, and their "context" is often a stray gate pass itself. The T6
# judges read each prompt alone, so they counted as missed the cards a live session already had.
SESSION_CONTEXT = os.environ.get("MATHBRAIN_SESSION_CONTEXT", "0").strip() not in ("0", "off", "false", "")
SESSION_CTX_DIR = os.environ.get("MATHBRAIN_SESSION_CTX_DIR") or os.path.join(HOME, ".claude", "dreaming",
                                                                              ".math_session_ctx")
SESSION_CTX_TTL_S = 3 * 3600
SESSION_CTX_CHARS = 2000
CONTINUATION_CUE = re.compile(
    r"\b(proofs?|prove[ns]?|proving|solve[ds]?|solving|solutions?|conjectures?|theorems?|lemmas?|bounds?|limits?|"
    r"infinit[ey]|estimates?|inequalit(?:y|ies)|constants?|converge\w*|counterexamples?|close are we|full problem)\b",
    re.I)
CONTINUATION_ADMIN = re.compile(
    r"\b(push\w*|commit\w*|github|repos?|repository|e-?mail\w*|upload\w*|download\w*|overleaf|zip|acknowledg\w*|"
    r"funding|affiliat\w*|cite[sd]?|citations?|abstract|titles?|format\w*|folders?|website|youtube|movies?|films?|"
    r"videos?|images?|pictures?|render\w*|narrat\w*|thumbnails?|slides?|episodes?|usage|how long|timeline)\b", re.I)


def _ctx_file(session_id):
    h = hashlib.sha1((session_id or "").encode("utf-8", "replace")).hexdigest()[:24]
    return os.path.join(SESSION_CTX_DIR, h + ".json")


def _remember_math_context(session_id, prompt):
    """Keep the session's latest gate-passing prompt. Never raises; a lost write only costs one continuation."""
    if not session_id:
        return
    try:
        os.makedirs(SESSION_CTX_DIR, exist_ok=True)
        path = _ctx_file(session_id)
        tmp = "%s.%d.tmp" % (path, os.getpid())
        with io.open(tmp, "w", encoding="utf-8") as f:
            json.dump({"ts": time.time(), "text": prompt[:SESSION_CTX_CHARS]}, f, ensure_ascii=True)
        os.replace(tmp, path)
    except Exception:
        pass


def _session_math_context(session_id, prompt):
    """The remembered prompt for a gate-refused continuation, or None."""
    if not session_id or not CONTINUATION_CUE.search(prompt) or CONTINUATION_ADMIN.search(prompt):
        return None
    try:
        with io.open(_ctx_file(session_id), encoding="utf-8") as f:
            rec = json.load(f)
        if time.time() - float(rec.get("ts", 0)) > SESSION_CTX_TTL_S:
            return None
        text = str(rec.get("text") or "").strip()
        return text or None
    except Exception:
        return None


# THE VOUCH ROUTE (MATHBRAIN_GATE_VOUCH, default on; arm cand72, 26 Sep 04:0x). A plainly worded question carries no anchor
# word, so the entry gate refuses it before retrieval runs: on cand70, 32 of 40 paraphrased ledger questions per plain wrapper,
# and all 18 house-result questions of the house banks that it refuses. Two stages keep what the gate protects. The first is
# cheap: the prompt holds at least two words of math_vouch_vocab.json (five or more letters, in at least two store nodes, in at
# most three of his archive prompts the gate refuses; tools/measure_gate_vouch.py). The second checks what retrieval serves: the
# top card must also be the prompt's first claim by the semantic channel, else silence (vouch_disagree). Forced open, the gate
# served plausible wrong neighbours as well as right cards; where the two channels agreed on the top card, 11 of 13 blocks held
# the answer and the other 2 were attempt cards for the problem asked. Only a prompt the gate refused and the session context
# did not admit takes this route; nothing the gate admits changes.
GATE_VOUCH = os.environ.get("MATHBRAIN_GATE_VOUCH", "1").strip() not in ("0", "off", "false", "")
_VOUCH = {"route": False, "vocab": None, "card": None}
_VOUCH_WORD = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")
_VOUCH_STOP = frozenset("""about above after again against because before being below between both could doing during each
further having itself might myself other ought ourselves should their theirs them themselves there these those through under
until where which while would yours yourself yourselves what when with from into once only same some such than that then this
very just does have were been your they""".split())


# MATHBRAIN_VOUCH_VOCAB names the vocabulary file (default math_vouch_vocab.json beside this file); no file, no route.
VOUCH_VOCAB_PATH = os.environ.get("MATHBRAIN_VOUCH_VOCAB", "") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "math_vouch_vocab.json")


def _vouch_vocab():
    if _VOUCH["vocab"] is None:
        try:
            with io.open(VOUCH_VOCAB_PATH, encoding="utf-8") as fh:
                _VOUCH["vocab"] = frozenset(json.load(fh).get("terms") or ())
        except Exception:
            return frozenset()          # a failed read is not kept: the next prompt in this process reads again
    return _VOUCH["vocab"]


def _vouch_words(prompt):
    """The words of a prompt the vouch route counts: five letters or more, not a stop word (build_vouch_vocab reads it)."""
    return set(w for w in _VOUCH_WORD.findall((prompt or "").lower()) if len(w) >= 5 and w not in _VOUCH_STOP)


def _vouch_stage1(prompt):
    """At least two distinct store-language words (math_vouch_vocab.json) in a prompt the gate refused.

    A word and its plural count once (1 Nov 2026): 'compact compacts' alone vouched for the 5-minute loop prompt, so
    every tick paid the whole retrieval (~0.45 s) to say nothing. Folded, 1 of 165 vouched bank questions drops and the
    hook served it a wrong card; 3 of 31 of his vouched Mac prompts drop, none of them about mathematics."""
    try:
        v = _vouch_vocab()
        if not v:
            return False
        return len(set(w[:-1] if len(w) > 5 and w.endswith("s") and not w.endswith("ss") else w
                       for w in _vouch_words(prompt) & v)) >= 2
    except Exception:
        return False


def _vouch_agrees(prompt, chosen):
    """True when the served top card is also the prompt's first claim by the semantic channel; fails closed. The first
    claim is _semantic_top's, so a flat or tied similarity vector names no claim and nothing agrees with it."""
    try:
        return _same_card(str(chosen[0].node.id), _semantic_top(prompt))
    except Exception:
        return False


# COVERAGE YIELDS TO AGREEMENT (MATHBRAIN_COV_AGREE, default on; arm cand73, 26 Sep 09:5x). A plainly worded question covers
# few of its card's words, so both coverage abstentions silence it: on the blind plain bank they silence 18 of the 33
# questions cand72 leaves silent. When the lexical channel's first card is also the prompt's first claim by the semantic
# channel, the prefilter gate passes (its top candidate) and the block floor passes (the served lead card); fails closed
# without the claim vectors.
COV_AGREE = os.environ.get("MATHBRAIN_COV_AGREE", "1").strip() not in ("0", "off", "false", "")
# THE AGREED CARD IS SEALED (MATHBRAIN_COV_SEAL, default on; arm cand76, 28 Sep; scheduled audit account03/B07 auto11,
# B07-R22-COVAGREE-USE-ORDER-001). A coverage floor yields because the word and the meaning channels agree on one card,
# and the stages after it (lead filter, integrity reread, property order, session dedup, twin collapse, the math-use
# reorder) could still put another card first. When a yield was used, the use reorder is skipped and the block is
# emitted only if its first card is still the meaning channel's first claim.
COV_SEAL = os.environ.get("MATHBRAIN_COV_SEAL", "1").strip() not in ("0", "off", "false", "")
# A WEAK LEAD IN A SCATTERED BLOCK IS SILENCED (MATHBRAIN_COH_CUT, default on since 19 Oct 14:4x; 19 Oct, claude-5693aca3, study
# brain_graph/studies/math_hook_cov_agree_wide_20261019/NOTES_precision_screen.md). When the store lacks the theorem a
# prompt asks about, the hook still tends to serve a block: its first card sits further from the prompt than a right
# lead does, and its cards have little to do with one another. A block of two or more cards is withheld when the lead
# card's claim vector is below COH_CUT_LEAD in cosine to the prompt AND the cards' mean pairwise cosine is below
# COH_CUT_PAIR. Both readings exist at serve time. Measured at 0.70 / 0.70: fresh bank 2 of 19 strict false fires
# removed, 0 of 143 rank-1 hits lost; held-out probes 19 of 145 unanswerable fires removed, 0 of 92 gold hits lost.
# LEAD RAISED TO 0.80 on 2 Nov 22:1x (claude-1e0a23c2, op/cohname in brain_math_loop_1e0a: coh_held.py, coh_held2.py,
# coh_lead_fresh.py, coh_paironly.py). The pair line is the fragile one: every right serve on both banks has pair >= 0.697
# and pair 0.71-0.72 already loses 3 held-out gold hits. No right serve on either bank has pair < 0.70 and no name
# exemption, so the lead line can rise with the pair line kept: at 0.80 / 0.70, held-out 39 more unanswerable fires and
# 16 more gold misses withheld, 0 of 92 gold hits lost; fresh bank 3 more wrong serves withheld, 0 of 141 right lost.
# MATHBRAIN_COH_CUT_LEAD=0.70 restores the old line.
COH_CUT = os.environ.get("MATHBRAIN_COH_CUT", "1").strip() not in ("0", "", "off", "false")
COH_CUT_LEAD = float(os.environ.get("MATHBRAIN_COH_CUT_LEAD", "0.80") or 0.80)
COH_CUT_PAIR = float(os.environ.get("MATHBRAIN_COH_CUT_PAIR", "0.70") or 0.70)
# The lead cosine is also read against the prompt's last COH_TAIL chars (cut at a paragraph or sentence start found in
# the last 1.5x), and the higher of the two counts: a question asked at the end of a long lead-in otherwise reads as a
# weak match (19 Oct, coh_tail_screen.py: embedded set 4 -> 1 of 19 hits cut; held-out cuts unchanged, 19 fires +
# 3 wrong). MATHBRAIN_COH_TAIL, default 250; 0/off reads the whole prompt only.
_CT = os.environ.get("MATHBRAIN_COH_TAIL", "250").strip().lower()
COH_TAIL = 0 if _CT in ("", "0", "off", "false") else int(_CT)


def _prompt_tail(prompt, n):
    """The last n chars of the prompt, started at a paragraph or sentence break when one lies in the last 1.5n; None
    when the prompt is no longer than n."""
    t = (prompt or "").strip()
    if n <= 0 or len(t) <= n:
        return None
    win = t[-int(n * 1.5):]
    m = re.search(r"\n\s*\n|(?<=[.?!])\s+(?=[A-Z])", win[: len(win) - n // 3])
    return win[m.end():] if m else t[-n:]


def _closing_paragraph(prompt):
    """The prompt's last paragraph (after its last blank line) when it is 40-600 chars, else None. A question asked
    after a short lead-in sits there; the char tail above still carries part of the lead-in (19 Oct,
    coh_tail_screen2.py: real lead-ins 6 -> 4 of 33 hits cut, held-out cuts unchanged)."""
    parts = re.split(r"\n\s*\n", (prompt or "").strip())
    if len(parts) > 1 and 40 <= len(parts[-1].strip()) <= 600:
        return parts[-1].strip()
    return None


# THE CUT SPARES A BLOCK WHOSE LEAD CARD IS THE THING THE PROMPT NAMES (MATHBRAIN_COH_NAME_EXEMPT, default on; 2 Nov
# 2026, claude-1e0a23c2). The healthcheck row "Sylow theorems." was silenced at lead 0.583 / pair 0.666 although the lead
# card is titled "Sylow Theorems": a two-word prompt sits far from any card in cosine. Exempt only when the prompt has
# name tokens (_name_tokens) and every one of them is a word of the lead card's title (prefix match for plurals).
# Screened on the cut's own 19 Oct A/B rows: 0 of 3 fresh and 0 of 22 held-out silenced rows would come back.
COH_NAME_EXEMPT = os.environ.get("MATHBRAIN_COH_NAME_EXEMPT", "1").strip() not in ("0", "", "off", "false")


# A sentence-initial capital is not a name: _name_tokens reads "Total" in "Total variation ..." as one, and the Pinsker
# unanswerable row came back off-target on it. Tokens in more than 1 pct of the brain's memory documents (BM25 df,
# frozen 2 Nov 2026 to math_coh_common_words.txt, 2,575 words: total 8 pct, among 2 pct; Sylow, Pinsker, Girsanov
# under 0.1 pct) are dropped before the test, and the exemption needs at least one name left. File missing = no drop.
_COH_COMMON = None


def _coh_common_words():
    global _COH_COMMON
    if _COH_COMMON is None:
        try:
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "math_coh_common_words.txt"),
                      encoding="utf-8") as f:
                _COH_COMMON = frozenset(w.strip() for w in f if w.strip())
        except Exception:
            _COH_COMMON = frozenset()
    return _COH_COMMON


_RESULT_WORDS = frozenset(("theorem", "theorems", "inequality", "inequalities", "lemma", "conjecture", "principle",
                           "formula", "law", "identity", "criterion", "bound", "hypothesis", "estimate", "estimates"))


# A quoted name needs one SPECIFIC word (sb26, claude-1e0a23c2, 5 Nov): "main theorem", "key lemma", "limit theorem",
# "uniqueness theorem" name a kind of result, not one result, and passed off-topic lead cards (gen26 probe).
_QUOTE_GENERIC = frozenset(os.environ.get("MATHBRAIN_QUOTE_GENERIC_EXTRA", "").lower().split()) | frozenset((
    "main", "key", "first", "second", "third", "fourth", "fifth", "basic", "general", "fundamental", "principal",
    "important", "crucial", "technical", "auxiliary", "preliminary", "new", "simple", "useful", "standard", "classical",
    "final", "last", "next", "following", "above", "previous", "our", "this", "that", "famous", "celebrated", "well",
    "known", "deep", "easy", "big", "little", "one", "two", "three", "limit", "uniqueness", "existence", "structure",
    "comparison", "convergence", "extension", "decomposition", "representation", "classification", "approximation",
    "embedding", "global", "weak", "strong"))


def _lead_title_names_prompt(prompt, title):
    try:
        cw = _coh_common_words()
        nt = [w for w in _name_tokens(prompt) if w.lower() not in cw]
        if not title or (not nt and not _QUOTE_RE.search(prompt or "")):
            return False
        t = unicodedata.normalize("NFKD", title)
        t = "".join(c for c in t if not unicodedata.combining(c))
        tw = set(w for w in re.findall(r"[a-z0-9]+", t.lower()) if len(w) > 2)
        # A RESULT, NOT A CONCEPT (3 Nov, claude-1e0a23c2, op/exres): the exemption kept an unanswerable Kac-Rice block
        # because its lead card was the concept "Gaussian process", named in the prompt. The exemption is for a prompt
        # that names the theorem the lead card states, so the title must carry a result word. Fresh bank: the three
        # exempt hits (Sylow, isoperimetric, implicit function) keep it, the Kac-Rice fire loses it.
        if not (tw & _RESULT_WORDS):
            return False
        if nt and all(any(w == x or w.startswith(x) or x.startswith(w) for x in tw) for w in nt):
            return True
        # SANDBOX sb25 (claude-1e0a23c2, 5 Nov): a quote whose content words (two or more) all sit in the title.
        if os.environ.get("MATHBRAIN_QUOTE_NAME_EXEMPT", "1").strip() in ("0", "off", "false", ""):
            return False
        tw2 = set(re.findall(r"[a-z0-9]+", t.lower()))
        for _q in _QUOTE_RE.findall(prompt or ""):
            _qt = {w for w in re.findall(r"[a-z0-9]+", _nf_fold(_q)) if w not in _NTM_STOP and len(w) > 1}
            if len(_qt) >= 2 and _qt <= tw2 and (_qt - _RESULT_WORDS - _QUOTE_GENERIC):
                return True
        return False
    except Exception:
        return False


_COH_POS = None


def _incoherent_weak_block(prompt, ids):
    """(lead cosine, mean pairwise cosine) when the block should be withheld, else None. Cards without a vector are
    skipped; fewer than two with vectors never cut. Any failure keeps the block."""
    try:
        from math_brain import semantic as _S
        import numpy as _np
        _S._load_artifact()
        # Pair first (2 Nov, claude-1e0a23c2): a block is cut only when pair < COH_CUT_PAIR, so a coherent block skips
        # the prompt embed (355 of 446 held served blocks). Same verdicts, 24 of 24 held blocks; the id->row map is
        # cached per vector artifact instead of rebuilt each call.
        global _COH_POS
        if _COH_POS is None or _COH_POS[0] is not _S._ids:
            _COH_POS = (_S._ids, {i: n for n, i in enumerate(_S._ids)})
        pos = _COH_POS[1]
        rows = [pos[str(i)] for i in ids if str(i) in pos]
        if len(rows) < 2:
            return None
        vs = _S._vecs[rows]
        g = vs @ vs.T
        n = len(rows)
        pair = float((g.sum() - _np.trace(g)) / (n * (n - 1)))
        if pair >= COH_CUT_PAIR:
            return None
        q = _np.asarray(_S._query_encoder().embed(_S.QUERY_PREFIX + (prompt or "")), dtype=_np.float32)
        lead = float(vs[0] @ q)
        if lead < COH_CUT_LEAD and COH_TAIL:
            for tail in (_prompt_tail(prompt, COH_TAIL), _closing_paragraph(prompt)):
                if tail and lead < COH_CUT_LEAD:
                    lead = max(lead, float(vs[0] @ _np.asarray(_S._query_encoder().embed(_S.QUERY_PREFIX + tail),
                                                               dtype=_np.float32)))
        return (lead, pair) if lead < COH_CUT_LEAD else None
    except Exception:
        return None


# A BLOCK FAR FROM THE PROMPT ON THE STORE'S OWN SCALE IS SILENCED (MATHBRAIN_MEANQ_Z, default 1.25 since 19 Oct 15:5x;
# 19 Oct, claude-5693aca3, study math_hook_cov_agree_wide_20261019/calibrated_screen.py, z_sweep.py, NOTES). The served
# cards' mean cosine to the prompt moves with prompt length, so it is read against the prompt's mean cosine to a fixed
# random sample of 4000 store cards: z = (meanq - base) / sd. Held-out long prompts: 192 of 427 served blocks withheld
# (146 gold misses, 46 unanswerable), 0 of 92 gold hits lost; fresh bank: 2 of 160 hits lost. 0/off disables.
# DEFAULT OFF since 19 Oct 17:3x: with a fresh question placed after a real long lead-in (build_embedded.py, 178 rows)
# z silences 14 of 20 hits, because the lead-in dominates the prompt's embedding. Scoring the tail too
# (tail_z_screen.py, silence only when both z < 1.25) saves 7 of the 14 at 150-250 chars, 5 at 400, none at 800.
_MZ = os.environ.get("MATHBRAIN_MEANQ_Z", "off").strip().lower()
MEANQ_Z = None if _MZ in ("", "0", "off", "false") else float(_MZ)
_Z_BASE = []


def _far_block_z(prompt, ids):
    """(z, meanq) when z is below MEANQ_Z, else None. Any failure keeps the block."""
    try:
        from math_brain import semantic as _S
        import numpy as _np
        _S._load_artifact()
        if not _Z_BASE:
            rng = _np.random.default_rng(20261019)
            _Z_BASE.append(_S._vecs[rng.choice(len(_S._vecs), size=min(4000, len(_S._vecs)), replace=False)])
        pos = {i: n for n, i in enumerate(_S._ids)}
        rows = [pos[str(i)] for i in ids if str(i) in pos]
        if not rows:
            return None
        q = _np.asarray(_S._query_encoder().embed(_S.QUERY_PREFIX + (prompt or "")), dtype=_np.float32)
        m = float((_S._vecs[rows] @ q).mean())
        b = _Z_BASE[0] @ q
        z = (m - float(b.mean())) / (float(b.std()) or 1e-6)
        return (z, m) if z < MEANQ_Z else None
    except Exception:
        return None

# A SHORT PROMPT THAT QUOTES A NAME NO SERVED CARD CARRIES IS SILENCED (MATHBRAIN_QUOTE_COV, sandbox sb20, 3 Nov).
# A prompt of at most QUOTE_MAX_WORDS words outside its quotes is asking about the quoted thing; a block whose
# every card carries less than QUOTE_COV of each quote's IDF weight answers some other question.
# Default 0.75 (landed 3 Nov 22:4x, claude-1e0a23c2, sandbox sb20). enum_ctrl, 80 valid rows: absent_13's lone
# Erdos-Sos card (coverage 0.658, 7 words outside quotes) silenced, the rule fired on nothing else, present items
# served and right 15/15 = sb19. Held enum_subset: 0 of 104 quoted items have <= 20 words outside quotes, so untouched;
# his 11 short quoted prompts of the last 3000 all stop at math_gate_rejected before this. 0/off disables.
# ACRONYM QUOTES (sb21, 3 Nov 23:1x): an all-caps quote of 3+ letters is covered when a served card's consecutive word
# initials spell it. Without it, '"BDG" ... continuous martingales' was silenced at cov 0.000 although its cards name
# Burkholder-Davis-Gundy. Probe: BDG served on sb21, silenced on the old rule; made-up QZX and HJW silenced on both;
# exact-name controls unchanged. enum_ctrl has 0 of 80 acronym quotes and his last 3000 prompts 0, so both are untouched.
_QC = os.environ.get("MATHBRAIN_QUOTE_COV", "0.75").strip().lower()
QUOTE_COV = None if _QC in ("", "0", "off", "false") else float(_QC)
QUOTE_MAX_WORDS = int(os.environ.get("MATHBRAIN_QUOTE_MAX_WORDS", "20") or 20)
_QUOTE_RE = re.compile(r'"([^"\n]{3,120})"')
_ACRO_RE = re.compile(r"^[A-Z][A-Z0-9]*(?:-[A-Z][A-Z0-9]*)*$")


def _quote_uncovered(prompt, hits):
    """(best coverage, words outside quotes) when the rule silences, else None. Any failure keeps the block."""
    try:
        qs = _QUOTE_RE.findall(prompt or "")
        if not qs or not hits:
            return None
        nw = len(_NONMATH_WORD.findall(_QUOTE_RE.sub(" ", prompt)))
        if nw > QUOTE_MAX_WORDS:
            return None
        df, n = _store_df(), _store_n()
        if not df or not n:
            return None
        import math as _m
        import math_brain.retrieval as _R
        terms = lambda t: set(_R._content_terms(_R.index_signals(t or "").lexical_tokens))
        idf = lambda t: _m.log1p(n / (1.0 + df.get(t, 0)))
        raws = [_card_index_text(h.node) or "" for h in hits]
        cards = [terms(t) for t in raws]
        best, weighed = 0.0, False
        for q in qs:
            # An acronym quote ("BDG", "AM-GM") is covered by a card whose consecutive words start with its letters
            # (Burkholder-Davis-Gundy). Three letters or more: two-letter runs match by chance in most cards.
            if _ACRO_RE.match(q.strip()):
                ac = re.sub(r"[^a-z]", "", q.lower())
                if len(ac) >= 3 and any(ac in "".join(w[0] for w in re.findall(r"[a-z]+", t.lower())) for t in raws):
                    return None
            qt = terms(q)
            w = sum(idf(t) for t in qt)
            if w <= 0:
                continue
            weighed = True
            for ct in cards:
                best = max(best, sum(idf(t) for t in qt & ct) / w)
        return (best, nw) if weighed and best < QUOTE_COV else None
    except Exception:
        return None


_COV_AGREED = {}
_SEMTOP = {}
# A FIRST CLAIM NEEDS A WINNER (MATHBRAIN_SEMTOP_MARGIN, default 1e-6; 29 Sep, scheduled audits account01/B10 r24,
# B10-R68-SEMANTIC-ARGMAX-WITHOUT-EVIDENCE-02, and account04/B11 r5, B11-R68-RAW-ARGMAX-UNBOUND-AGREEMENT-20260929-01).
# np.argmax always names a claim: over a flat or tied similarity vector it names the first index, so "the meaning
# channel's first claim" existed with no evidence behind it, and it agreed with the lexical top whenever that happened to
# be the same index. The channel now names a claim only when the cosines are finite, spread, and the top one beats the
# second by more than this margin. Measured on his last 2,899 prompts: the top two tied exactly on 2 and never came
# within 1e-6 otherwise; the spread was never below 0.023. So 2 prompts lose their first claim, and with it any
# agreement. A negative margin turns the rule off.
try:
    SEMTOP_MARGIN = float(os.environ.get("MATHBRAIN_SEMTOP_MARGIN", "1e-6"))
except ValueError:
    SEMTOP_MARGIN = 1e-6


def _semantic_winner(cos, ids, np_):
    """The index-identifier the similarity vector puts first, or None when it puts nobody first."""
    if SEMTOP_MARGIN < 0:
        return str(ids[int(np_.argmax(cos))])
    if cos.shape[0] == 0 or not bool(np_.isfinite(cos).all()):
        return None
    if cos.shape[0] == 1:
        return str(ids[0])
    if float(cos.std()) <= 1e-9:
        return None
    first, second = np_.argpartition(-cos, 1)[:2]
    if cos[second] > cos[first]:
        first, second = second, first
    if float(cos[first] - cos[second]) <= SEMTOP_MARGIN:
        return None
    return str(ids[int(first)])


# THE VECTORS MUST COVER THE STORE (29 Sep, scheduled audit account01/B09 r31, the store-binding caveat of R68). The
# claim vectors are a file beside the store, and nothing refreshed them after an ingest, so a claim filed today had no
# vector: the meaning channel picked its first claim from the claims the file held. When the new claim is the right
# answer and an older one leads on the words and wins on meaning among the rest, the two channels agree on the older
# card and the gate yields to it. The channel now names nobody while a current claim has no vector or a vector names no
# node of the store. The math transport extends the file on every run (math_claim_vecs.py), so the gap lasts one run at
# most; math_claim_vecs.coverage holds the same rule for the transport's status line.
# THE TEXT IS BOUND TOO (29 Sep, scheduled audit account03/B12 r56). Ids alone let a file made from one store pass
# against another store that holds the same ids with other text. The file records each row's store digest
# (node_digests), math_brain.semantic reads them into _digests, and a row whose digest is not the store's counts as a
# claim without a vector. Loaded vectors that carry no digests (an older file, or a loader that does not read them)
# name nobody.
# A VECTOR OF UNKNOWN TEXT IS NOT BOUND (29 Sep, scheduled audits account04/B06 r76 and account04/B14 r8). The migration
# that bound the file wrote each row's current digest, and marked the rows no text of their claim reproduces as legacy in
# row_source; this check read the digest and not the mark, so 6,229 vectors of unknown text passed as bound. A legacy row
# now counts as a claim without a vector of its current text, and the channel names nobody while the file holds one.
_SEMTOP_WHY = {}


# A SELECTED CARD THE INTEGRITY READ REFUSES REFUSES THE BLOCK (29 Sep, scheduled audit account04/B04 r69). The read used to
# drop a refused card and serve the rest, so a refused first card moved the runner-up first under decisions made for the
# first card - its margin, and the coverage exception granted when both channels agreed on it - and a refused later card
# changed the set that the block-coverage floor and the generic-only test had judged. The block is now refused whenever
# any selected card is (math_brain's on_refusal="all"); a checkout older than 29 Sep has no such mode and drops the card
# as before. Measured the day it landed, on his 489 gate-passing prompts: 68 served, one of them with a refused card (a
# third card, the claim whose body is empty), none with a refused first card; 142 of the store's 8,243 servable cards
# failed the read (100 missing artifact bindings, 34 stored-digest mismatches, 8 claim/statement mismatches).
# THE KEYWORD FOLLOWS THE PACKAGE'S OWN FORM (29 Sep, scheduled audit account01/B02 auto11,
# B02-R78-LEGACY-CHECKOUT-REFUSAL-DOWNGRADE-20260929). math_brain has said "refuse the block" two ways: on_refusal="all"
# (from 6f2742b41f) and strict=True (7282e5a080, the same morning's first form). The hook asked only for the first and passed
# nothing otherwise, so a hook running over the 7282 package, or any package without either, dropped the refused card as
# before 29 Sep. Now each known form gets its own keyword, and a package offering neither returns None: the caller serves
# nothing and notes integrity_api_incompatible, since the block's gates were decided on the whole selection.
def _refusal_kwargs():
    try:
        from math_brain import selection_integrity as _si
        if "all" in getattr(_si, "ON_REFUSAL", ()):
            return {"on_refusal": "all"}
        import inspect
        if "strict" in inspect.signature(_si.consistent_hits).parameters:
            return {"strict": True}
    except Exception:
        pass
    return None


def _claim_vectors_cover_store(ids, store=None, digests=None, sources=None):
    """(missing, phantom): current claims of the store with no vector (a superseded claim needs none), and vector ids
    that name no node of it. With digests (one per id: the store digest of the text each vector was made from), a row
    whose digest is not the store's counts as a claim without a vector; with sources (one per id, the file's
    row_source), so does a row of unknown text (legacy), whatever digest it carries; and so does an id that names more
    than one row, superseded or not (account04/B06 r75: the winner is a row, and a second row of the same claim is a
    second voice no check has seen). Read in one snapshot, every call: the store runs in WAL mode, so its file's time
    says nothing about what it holds."""
    import sqlite3
    con = sqlite3.connect("file:%s?mode=ro" % os.path.abspath(store or STORE_PATH).replace(os.sep, "/"),
                          uri=True, timeout=10)
    try:
        con.execute("BEGIN")
        claims = {r[0] for r in con.execute("select id from claims")}
        superseded = {r[0] for r in con.execute("select target_id from edges where kind = 'supersedes'")}
        nodes = {r[0]: str(r[1]) for r in con.execute("select id, digest from nodes")}
    finally:
        con.close()
    have = [str(i) for i in ids]
    seen, dup = set(), set()
    for i in have:
        (dup if i in seen else seen).add(i)
    missing = (claims - set(have) - superseded) | dup
    if digests is not None:
        if len(digests) != len(have):
            raise ValueError("%d store digests for %d vectors" % (len(digests), len(have)))
        missing |= {i for i, d in zip(have, digests) if i in nodes and i not in superseded and nodes[i] != str(d)}
    if sources is not None:
        if len(sources) != len(have):
            raise ValueError("%d row sources for %d vectors" % (len(sources), len(have)))
        missing |= {i for i, s in zip(have, sources) if i in nodes and str(s) == "legacy"}
    return sorted(missing), sorted(set(have) - set(nodes))


# THE MEANING CHANNEL NAMES A CURRENT REVISION (MATHBRAIN_SEMTOP_CURRENT; 4 Oct 2026, the live-path variant of scheduled audit
# account04/B03). The argmax ran over every claim vector, and the 361 claims a newer immutable revision supersedes all keep
# theirs: asked "What is <title>?" for 327 revised claims, the winner was the OLD revision 181 times and the current one 79
# times (current revisions only: 167). retrieve() ranks current revisions only, so the agreement COV_AGREE looks for could not
# happen on exactly the cards that were revised. With the dial on, the superseded revisions are masked out before the winner.
# Measured before it went live (campaign_2026-10-03_mathbrain_scale, results/semtopcur_*; one candidate file, dial 0 against
# 1, side by side, clock lifted): on the 327 revised-claim questions fired 246 -> 256, hit 193 -> 202, first-card hit
# 184 -> 192 (9 silences became the right card, 1 became a related one, no answer lost); on 588 general jobs (the fresh
# bank's 178 answerable and 40 absent-target questions, 120 reworded ledger questions, 250 of his admitted prompts) not one
# outcome changed. Default on since 4 Oct; 0 restores the argmax over every vector.
SEMTOP_CURRENT = os.environ.get("MATHBRAIN_SEMTOP_CURRENT", "1").strip() not in ("0", "off", "false", "")
_CURRENT_MASK = {}


def _current_revision_mask(ids, np_):
    """Boolean mask over the claim vectors: False for a claim a newer revision supersedes (the target of a 'supersedes'
    edge in the store), read once per process and per vector file; None when the store cannot be read (no masking)."""
    key = (len(ids), STORE_PATH)
    if key not in _CURRENT_MASK:
        try:
            import sqlite3
            con = sqlite3.connect("file:%s?mode=ro" % STORE_PATH, uri=True)
            try:
                old = frozenset(t for (t,) in con.execute("SELECT target_id FROM edges WHERE kind='supersedes'"))
            finally:
                con.close()
            _CURRENT_MASK[key] = np_.asarray([str(i) not in old for i in ids], dtype=bool) if old else None
        except Exception:
            _CURRENT_MASK[key] = None
    return _CURRENT_MASK[key]


def _semantic_top(prompt):
    """The prompt's first claim by the semantic channel, cached per prompt; None when the channel is unavailable, its
    vectors do not cover the store (_claim_vectors_cover_store), or it names no winner (_semantic_winner). Why is in
    _SEMTOP_WHY."""
    key = prompt or ""
    if key not in _SEMTOP:
        top = None
        try:
            from math_brain import semantic as _S
            import numpy as _np
            _S._load_artifact()
            digests = getattr(_S, "_digests", None)
            missing, phantom = ([], []) if digests is None else _claim_vectors_cover_store(
                _S._ids, digests=digests, sources=getattr(_S, "_sources", None))
            if digests is None:
                _SEMTOP_WHY[key] = ("vectors do not cover the store: the loaded vectors carry no store digests (the "
                                    "file records none, or math_brain.semantic does not read them)")
            elif missing or phantom:
                _SEMTOP_WHY[key] = ("vectors do not cover the store: %d current claim(s) without a vector of their "
                                    "current text, %d vector(s) naming no node" % (len(missing), len(phantom)))
            else:
                vec = _S._query_encoder().embed(_S.QUERY_PREFIX + key)
                cos = _S._vecs @ _np.asarray(vec, dtype=_np.float32)
                ids = _S._ids
                if SEMTOP_CURRENT:
                    keep = _current_revision_mask(ids, _np)
                    if keep is not None:
                        cos, ids = cos[keep], [i for i, k in zip(ids, keep) if k]
                top = _semantic_winner(cos, ids, _np)
                _SEMTOP_WHY[key] = "winner" if top else "no winner"
        except Exception as exc:
            top = None
            _SEMTOP_WHY[key] = "unavailable: %s" % type(exc).__name__
        _SEMTOP[key] = top
    return _SEMTOP[key]


def _same_card(a, b):
    a, b = str(a or ""), str(b or "")
    return bool(a and b) and (a == b or a.startswith(b[:16]) or b.startswith(a[:16]))


def _switch_on(name):
    return os.environ.get(name, "1").strip() not in ("0", "", "off", "false")


def _merge_subquery_blocks(blocks, chosen, lead=False):
    """Round-robin the sub-query blocks three deep, then the whole-question cards; one card per node and, under
    MERGE_TITLE_DEDUP, one per accent-folded title (a statement replaces an encyclopedia opening).

    ONE CARD PER TITLE ACROSS THE MERGE (MATHBRAIN_MERGE_TITLE_DEDUP, default on; arm cand59). Two named sub-queries can
    each bring a copy of one result ("Holder's Inequality" and "Hölder's inequality"); the merge joined blocks by node id
    only, so both went out and a slot was spent twice. A dropped duplicate leaves None in its place under
    MERGE_DUP_NO_REFILL. lead=True (a named argument) puts the first MATHBRAIN_SUBQ_LEAD_KEEP whole-question cards
    (default 1) ahead of the blocks."""
    merged, seen, keys = [], set(), {}

    def _merge_in(h):
        if h.node.id in seen:
            return
        seen.add(h.node.id)
        if MERGE_TITLE_DEDUP:
            k = _title_key(getattr(h.node, "title", ""), fold=True)
            if k and k in keys:
                i = keys[k]
                if merged[i] is not None and _is_lede(merged[i].node) and not _is_lede(h.node):
                    merged[i] = h
                if MERGE_DUP_NO_REFILL:
                    merged.append(None)
                return
            if k:
                keys[k] = len(merged)
        merged.append(h)

    try:
        lead_keep = int(os.environ.get("MATHBRAIN_SUBQ_LEAD_KEEP", "1") or 0)
    except Exception:
        lead_keep = 0
    if lead and lead_keep > 0:
        for h in (chosen or [])[:lead_keep]:
            _merge_in(h)
    for depth in range(3):
        for b in blocks:
            if len(b) > depth:
                _merge_in(b[depth])
    for h in (chosen or []):
        _merge_in(h)
    return merged


def _named_slots_full(blocks, merged, cap, base, named):
    """The cards a merged sub-query list serves, whether the block budget widens for them, and the merged cut before
    the append (the coherence guard's fallback).

    A FIFTH CARD FOR A FIFTH NAMED RESULT (MATHBRAIN_NAMED_EXTRA_SLOT, default on; campaign mb10d, 25 Sep, arm cand54).
    merged opens with each named block's first card, in order; when more distinct results than the cap came back
    title-matched, the next of them gets one more slot - never a whole-question card - and the block budget widens.

    FIX D (MATHBRAIN_SUBQ_APPEND=N, default 4): on a named argument the whole-question block (base, the cards chosen
    before the sub-queries) is kept whole and up to N named cards with new titles are appended after it, so a named
    card never pushes the whole-question gold out of the last slot."""
    extra = False
    d0 = []
    if named and NAMED_TITLEMATCH and _switch_on("MATHBRAIN_NAMED_EXTRA_SLOT"):
        d0k = set()
        for b in blocks:
            if b and b[0].node.id not in d0:
                k0 = _title_key(b[0].node.title, fold=True) if MERGE_TITLE_DEDUP else b[0].node.id
                if k0 in d0k:
                    continue
                d0k.add(k0)
                d0.append(b[0].node.id)
    if len(d0) > cap and len(merged) > cap and merged[cap] is not None and merged[cap].node.id == d0[cap]:
        chosen = [h for h in merged[:cap + 1] if h is not None]
        extra = True
    else:
        chosen = [h for h in merged[:cap] if h is not None]
    cprime = list(chosen)
    try:
        n_app = int(os.environ.get("MATHBRAIN_SUBQ_APPEND", "4") or 0)
    except Exception:
        n_app = 0
    if named and n_app > 0 and base:
        keep = list(base)[:cap]
        ids = {h.node.id for h in keep}
        tks = {_title_key(getattr(h.node, "title", ""), fold=True) for h in keep}
        add = []
        for h in merged:
            if h is None or h.node.id in ids:
                continue
            k = _title_key(getattr(h.node, "title", ""), fold=True)
            if k and k in tks:
                continue
            add.append(h)
            ids.add(h.node.id)
            tks.add(k)
            if len(add) >= n_app:
                break
        chosen = keep + add
        extra = bool(add)
    return chosen, extra, cprime


def _named_slots(blocks, merged, cap, base, named):
    """_named_slots_full without the pre-append list."""
    chosen, extra, _ = _named_slots_full(blocks, merged, cap, base, named)
    return chosen, extra


def _cut_unless_named(prompt, hits):
    """_incoherent_weak_block on these cards, None when the lead card's title is the result the prompt names."""
    c = _incoherent_weak_block(prompt, [str(h.node.id) for h in hits])
    if c and COH_NAME_EXEMPT and _lead_title_names_prompt(prompt, hits[0].node.title):
        return None
    return c


def _subq_coh_guard(prompt, chosen, extra, pre, cprime, named):
    """The cards a named or quoted-title sub-query block serves once the coherence cut has read it
    (MATHBRAIN_SUBQ_COH_GUARD, default on). The cards a sub-query pulls are chosen by the names in the prompt, not by the
    question, so a merged block can read as weak and scattered where the whole-question block (pre) did not. When the
    merged block would be cut: on a named argument the merge before the append (cprime) is served if it passes
    (MATHBRAIN_SUBQ_APPEND_FALLBACK_MERGED, default on); else the whole-question block if it passes; else nothing
    changes and the serve path's own cut decides. Any failure keeps the block."""
    if not (COH_CUT and pre and len(chosen) > 1 and _switch_on("MATHBRAIN_SUBQ_COH_GUARD")):
        return chosen, extra
    try:
        if not _cut_unless_named(prompt, chosen):
            return chosen, extra
        cp = _cut_unless_named(prompt, pre) if len(pre) > 1 else None
        try:
            n_app = int(os.environ.get("MATHBRAIN_SUBQ_APPEND", "4") or 0)
        except Exception:
            n_app = 0
        if (named and n_app > 0 and len(cprime) > 1 and _switch_on("MATHBRAIN_SUBQ_APPEND_FALLBACK_MERGED")
                and not _cut_unless_named(prompt, cprime)):
            return list(cprime), True
        if not cp:
            return list(pre), False
    except Exception:
        pass
    return chosen, extra


def _block_cov_verdict(prompt, chosen, pinned_ids, split_used=False):
    """None when the block is not judged by the coverage floor or passes it; "block_coverage:<cov>" when it is silenced;
    "coverage_agree_block:<cov>" when it is under the floor but the word and meaning channels agree (COV_AGREE): the
    agreed card is recorded in _COV_AGREED and the block passes. A block the sub-queries built (split_used) is not
    judged."""
    floor = _block_cov_floor()
    if not (floor > 0 and chosen and not pinned_ids and not split_used and len(prompt) <= 1500
            and not _argument_shape(prompt)):
        return None
    # PLAIN QUESTIONS ONLY (cand7, 2026-09-24): at 0.40 on every prompt the floor silenced all 40 enumerated
    # self-avoiding-walk lookups, four locate questions, Hoeffding (0.384, a health case) and two held-out research
    # steps whose notation no card repeats word for word - word coverage undercounts a prompt that carries digits or
    # formulas. It judges only a prompt with neither, at 0.35.
    plain = not re.search(r"\d", prompt)
    if plain and os.environ.get("MATHBRAIN_BLOCK_COV_PLAIN_ONLY", "1") != "0":
        try:
            from math_brain.retrieval import index_signals as _isg
            plain = not _isg(prompt).math_tokens
        except Exception:
            plain = False
    elif os.environ.get("MATHBRAIN_BLOCK_COV_PLAIN_ONLY", "1") == "0":
        plain = True
    if not plain:
        return None
    # NAMED SUBJECT (cand7): a served card whose title carries a name the prompt uses (an acronym, a proper name -
    # _name_tokens) is on the prompt's subject however few of its other words the card repeats. A capitalised word that
    # opens a sentence is grammar, not a name ("Existence and uniqueness of a translation-invariant measure ..." matched
    # the title word "Existence"); acronyms and hyphenated names keep counting there.
    if os.environ.get("MATHBRAIN_BLOCK_COV_NAME_EXEMPT", "1") != "0":
        try:
            nm = _name_tokens(re.sub(r"(^|[.!?:;]\s+)[A-Z][a-z]+\b", r"\1", prompt.strip()))
            if nm and any(nm & set(re.findall(r"[a-z0-9]+", (h.node.title or "").casefold())) for h in chosen):
                return None
        except Exception:
            pass
        # A HOUSE ALIAS NAMES THE SUBJECT TOO (5 Nov, MATHBRAIN_BLOCK_COV_ALIAS_EXEMPT, default on): "MP law" is a name
        # the alias table spells out ("Marchenko-Pastur law"); a served card whose title carries every name token of
        # that expansion is on the prompt's subject. The raw-prompt check above cannot see it: no title says "MP".
        if os.environ.get("MATHBRAIN_BLOCK_COV_ALIAS_EXEMPT", "1") != "0":
            try:
                xp = _expand_aliases(prompt)
                if xp != prompt:
                    at = {w for w in re.findall(r"[a-z0-9]+", xp[len(prompt):].casefold())
                          if len(w) >= 4 and w not in _NAME_STOP}
                    if at and any(at <= set(re.findall(r"[a-z0-9]+", (h.node.title or "").casefold())) for h in chosen):
                        return None
            except Exception:
                pass
    # A RARE TERM THE LEAD CARD'S TITLE SHARES WITH THE PROMPT (MATHBRAIN_COV_RARE_EXEMPT, default on; arm cand68): a
    # question in other words covers few of the card's words, but it keeps the rare technical term ("fork-join",
    # "Sinkhorn", "hyperuniformity") the name exemption cannot see when it is not capitalised. A term at most
    # MATHBRAIN_COV_RARE_DF store cards carry, shared by the prompt and the lead card's title, lifts the floor.
    if COV_RARE_EXEMPT:
        try:
            import math_prefilter as _mpf
            pdata = _mpf.load(STORE_PATH)
            if pdata:
                post = pdata["post"]
                shared = set(_mpf.tokens(prompt)) & set(_mpf.tokens(chosen[0].node.title or ""))
                if any(len(w) >= 4 and w in post and len(post[w]) <= COV_RARE_DF for w in shared):
                    return None
        except Exception:
            pass
    try:
        cov = _block_best_coverage(prompt, chosen)
    except Exception:
        cov = None
    if cov is None or cov >= _block_cov_floor_for(chosen, floor):
        return None
    if COV_AGREE and _cov_agree_wide(chosen, _semantic_top(prompt)):
        _COV_AGREED["block"] = chosen[0].node.id
        return "coverage_agree_block:%.3f" % cov
    return "block_coverage:%.3f" % cov


def _reset_cov_agree_state():
    """A caller that runs several prompts in one process must not carry one prompt's agreed card or first claim into the
    next (the live hook runs one process per prompt; an in-process measurement does not)."""
    _COV_AGREED.clear()
    _SEMTOP.clear()
    _SEMTOP_WHY.clear()


def _reset_per_prompt_state():
    """A caller that runs several prompts in one process must not carry one prompt's vouch route, agreed card or pool
    adjacency into the next (the live hook runs one process per prompt; found 28 Sep in an in-process measurement). The
    adjacency joined on 28 Sep (account02/B11 auto37, B11-R31-POOL-ADJ-FALLBACK-STATE-LEAK-01): the full-store path never
    set it, so a prompt that fell back after a prefiltered one ranked with the earlier pool's links."""
    _VOUCH["route"] = False
    _VOUCH["card"] = None
    _COV_AGREED.clear()
    _SEMTOP.clear()
    _SEMTOP_WHY.clear()
    _set_pool_adjacency(())


# THE PRINTOUT POINTS AT THE WALK AND SAYS WHAT MATCHED (F05, 12 Oct 2026). Both are applied to the FINISHED block, after
# every stage that chooses, orders or fits cards, so neither can change which cards go out or in what order.
# (1) MATHBRAIN_RECALL_POINTER (default 1): the header names math_recall.py --id, which prints the card whole with every
#     link (kind, direction, the other card's id and title, the link's stated reason), instead of math_note.py, which
#     prints the note alone. Measured 11 Oct on 100 recall questions with Codex 6.1 agents: walking cards by id reached
#     the same answers as whole-card search (82 against 83) on 24.6M against 186M input tokens
#     (math_brain_recall_what_works_and_how_to_use_it_2026_10_11). 0 restores the math_note.py line.
# (2) MATHBRAIN_MATCHED_TERMS (default 3; 0 = off): each card's hint line ends "matched: a, b, c" - the prompt terms the
#     ranker matched on that card, strongest first, read from retrieve()'s own score trace. A lead that came in on
#     "field, rows" reads as the coincidence it is; one that came in on "martingale, stopping, optional" reads as on
#     subject. It is the hint the 2 Sep order asks every note to carry ("a note can mean something good or bad"), about
#     the match rather than the card. A card with no word match in its trace (a pinned citation) gets no such line.
RECALL_POINTER = os.environ.get("MATHBRAIN_RECALL_POINTER", "1").strip() not in ("0", "off", "false", "")
_NOTE_POINTER = "Full note: python ~/.claude/dreaming/math_note.py SOURCE_ID"
_RECALL_POINTER = "Card + links: python ~/.claude/dreaming/math_recall.py --id ID"
_TRACE_TERM = re.compile(r"""matched (?:normalized term|title phrase) ['"](.+?)['"]\s*$""")


def _matched_terms(hit, k):
    """Up to k prompt terms retrieve() matched on this hit, strongest first; [] on any doubt."""
    try:
        best = {}
        for t in getattr(hit, "trace", None) or ():
            if getattr(getattr(t, "channel", None), "value", "") != "lexical":
                continue
            m = _TRACE_TERM.search(str(getattr(t, "detail", "") or ""))
            if not m:
                continue
            term = m.group(1).strip()
            if not term or len(term) > 40:
                continue
            best[term] = max(best.get(term, 0.0), float(getattr(t, "amount", 0.0) or 0.0))
        return [w for w, _ in sorted(best.items(), key=lambda kv: (-kv[1], kv[0]))[:max(0, k)]]
    except Exception:
        return []


def _finish_block(block, emitted_ids, hits_by_id):
    """The finished block with the walk pointer and the matched-terms hint; the block unchanged on any doubt."""
    try:
        out = block
        if RECALL_POINTER and _NOTE_POINTER in out:
            out = out.replace(_NOTE_POINTER, _RECALL_POINTER, 1)
        try:
            k = int(os.environ.get("MATHBRAIN_MATCHED_TERMS", "3") or 0)
        except ValueError:
            k = 3
        if k > 0:
            for cid in emitted_ids or ():
                hit = hits_by_id.get(cid)
                terms = _matched_terms(hit, k) if hit is not None else []
                if not terms:
                    continue
                words = ", ".join(re.sub(r"[()|\r\n]+", " ", w).strip() for w in terms)
                marker = "    (%s | " % cid
                i = out.find(marker)
                if i < 0:
                    continue
                j = out.find(")\n", i)
                if j < 0:
                    j = len(out) - 1 if out.endswith(")") else -1
                if j < 0:
                    continue
                out = out[:j] + " | matched: " + words + out[j:]
        return out
    except Exception:
        return block


# A HARD WALL-CLOCK CEILING (F05, 12 Oct 2026; MATHBRAIN_HARD_DEADLINE_S, default 60 s; headless prompts
# MATHBRAIN_HEADLESS_HARD_DEADLINE_S, default 85 s; 0 = off, the old unbounded path, byte for byte).
# The budget above is checked at two points only (before ranking, and the integrity read's deadline), so a stage
# between them is unbounded - and since 8 Oct the dispatcher runs this hook IN ITS OWN PROCESS (hook_dispatch_allow.json),
# where the 90 s per-hook timeout of a subprocess never applies. Measured in production, 1-12 Oct (mathbrain_silence.tsv,
# real sessions only): 17 prompts spent 47-175 s loading before the hook gave up and served nothing
# (budget_exhausted_before_ranking), and hook_dispatch.log holds math_sense runs of 83, 88, 92 and 190 s - the prompt
# waits that long for a block that mostly never comes. Now the body runs on a daemon thread and the hook waits at most
# the ceiling: on time, the block goes out exactly as before; past it, the hook returns silently (silence note
# hard_deadline) and the late body can write nothing - its one write goes through _emit, which refuses once the hook
# has given up, so a late block can never reach the dispatcher's real stdout or be recorded as served. Headless
# workers keep the 85 s the 24 Sep sizing wanted under the dispatcher's 90 s; an interactive prompt gets 60 s, twice
# the 30 s budget the stages are sized for.
HARD_DEADLINE_S_DEFAULT = "60"
HEADLESS_HARD_DEADLINE_S_DEFAULT = "85"
_EMIT = {"lock": None, "abandoned": False, "exc": None}


def _hard_deadline_s(prompt):
    """The ceiling for this prompt in seconds; 0 or less = no ceiling (the body runs on the calling thread)."""
    try:
        if (prompt or "").lstrip().startswith(HEADLESS_MARKER):
            return float(os.environ.get("MATHBRAIN_HEADLESS_HARD_DEADLINE_S", HEADLESS_HARD_DEADLINE_S_DEFAULT) or 0)
        return float(os.environ.get("MATHBRAIN_HARD_DEADLINE_S", HARD_DEADLINE_S_DEFAULT) or 0)
    except Exception:
        return 0.0


def _emit(out, text):
    """Write the hook's one output unless the ceiling has passed. True when written."""
    lock = _EMIT["lock"]
    if lock is None:
        out.write(text)
        out.flush()
        return True
    with lock:
        if _EMIT["abandoned"]:
            return False
        out.write(text)
        out.flush()
        return True


def _main_body_guarded(data, out):
    try:
        _main_body(data, out)
    except BaseException as exc:          # the body's own try covers almost everything; this is the rest
        _EMIT["exc"] = exc
        try:
            _silence_note(data, "body_%s:%s" % (type(exc).__name__, str(exc)[:80]))
        except Exception:
            pass


def main():
    """Read the UserPromptSubmit JSON; run the body under the hard ceiling. Returns "abandoned" when it gave up."""
    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    if not isinstance(data, dict):
        return                              # a malformed payload is not a prompt: silent, fast, exit 0
    out = sys.stdout
    _p = data.get("prompt")
    hard = _hard_deadline_s(_p if isinstance(_p, str) else "")
    if hard <= 0:
        _EMIT["lock"] = None
        return _main_body(data, out)
    import threading
    _EMIT["lock"] = threading.Lock()
    _EMIT["abandoned"] = False
    _EMIT["exc"] = None
    t0 = time.time()
    worker = threading.Thread(target=_main_body_guarded, args=(data, out), name="math_sense_body", daemon=True)
    worker.start()
    worker.join(hard)
    if worker.is_alive():
        with _EMIT["lock"]:
            _EMIT["abandoned"] = True
        _silence_note(data, "hard_deadline:%.0fs" % (time.time() - t0))
        return "abandoned"
    if _EMIT["exc"] is not None and os.environ.get("MATHBRAIN_DEBUG"):
        raise _EMIT["exc"]
    return None


def _main_body(data, out):
    started = time.time()
    _reset_per_prompt_state()
    _reset_cov_agree_state()
    prompt = data.get("prompt")
    prompt = (prompt if isinstance(prompt, str) else "")[:8000]
    if RELAY_SKIP and _RELAY_OR_PICTURE_HEAD.match(prompt):
        _silence_note(data, "skip_relay_or_picture_batch")
        return
    _use_begin(str(data.get("session_id") or data.get("thread_id") or ""))
    prompt = _his_spelling(prompt)
    prompt = _latex_accents(prompt)
    _headless_budget(prompt)
    ctx_used = None
    gate_ok = not (len(prompt) < 12 or not _math_gate(prompt))
    if SESSION_CONTEXT:
        _ctx_sid = str(data.get("session_id") or data.get("thread_id") or "")
        if gate_ok:
            if not SHADOW:
                _remember_math_context(_ctx_sid, prompt)
        else:
            ctx_used = _session_math_context(_ctx_sid, prompt)
            if ctx_used is not None:
                _silence_note(data, "session_context_route", harness_only=True)
                prompt = prompt + "\n\n" + ctx_used
    if not gate_ok and ctx_used is None and GATE_VOUCH and len(prompt) >= 12 and _vouch_stage1(prompt):
        gate_ok = True
        _VOUCH["route"] = True
        _silence_note(data, "vouch_route", harness_only=True)
    if not gate_ok and ctx_used is None:
        # HARNESS RUNS ONLY (2026-09-14). This gate fires on every non-mathematical prompt in every
        # session, so logging it in production would bury the file - which is why it logged nothing at
        # all, and why it has been invisible. The cost of that invisibility, measured today: of the 12
        # answerable bank questions the hook silences, FIVE die right here and never reach the log, so
        # the silence file accounted for seven and read as though the margin test were the whole story.
        # The five are not marginal cases - "Which finite graphs are planar, in terms of forbidden
        # subdivisions?", "Asymptotics of the number of primes up to x.", Wald's identity, Jensen's
        # inequality written in symbols, Menger's theorem stated in words. A harness session (no uuid)
        # now gets the row; a real session still pays nothing.
        # SIZING, so nobody reads this as a call to loosen the gate: it refuses 5 of 178 answerable
        # bank questions (2.8 pct) and 4 of 40 absent-target ones (10 pct), so it is three times more
        # likely to stop a question the store cannot answer than one it can. Widening it trades those
        # five against a false-fire rate already at 60-67 pct. Measure both arms before touching it.
        _silence_note(data, "math_gate_rejected", harness_only=True)
        return
    if not os.path.exists(STORE_PATH):
        # A math prompt arrived and there is no store to answer it from. Logged since 2026-09-13: this is the
        # one silent exit that means the brain is BROKEN rather than merely quiet, and it was indistinguishable
        # from an ordinary abstention. The gate above it stays silent on purpose - it fires on every non-math
        # prompt and would bury the log.
        _silence_note(data, "store_missing")
        return
    try:
        sys.path.insert(0, BUILD_SRC)
        from math_brain.retrieval import retrieve
        from math_brain.store import ResearchStore
        _install_global_idf()
        if _nonmath_text(prompt, data):
            return
        if _trading_text(prompt, data):
            return
        if _verdict_text(prompt, data):
            return

        # The signal cache IS loaded here, and the 2026-08-30 note that stood in this place
        # ("3.26 s median WITH the cache against 2.49 s WITHOUT") measured a cache that never
        # hit: its builder hashed the title and the body separately while retrieve() hashes
        # `title + " " + body`, so every lookup missed and the hook paid the load with none of
        # the saving. Found 2026-09-03 by asking the loaded cache for the combined text. With
        # the key matched, three interleaved pairs in fresh processes over the 4,856-node
        # store: retrieve 0.30 s with the cache against 1.05 s without, whole call 0.71 s
        # against 1.29 s. The cache is a read-only sidecar beside the store, rebuilt by the
        # integrator after every ingest; a stale entry misses and recomputes, never lies.
        # 2026-09-07: a store may carry a lexical PREFILTER index beside it (<store>.prefilter.pkl, built by
        # math_prefilter.py). When it does, only the top PREFILTER_K candidates by BM25 are loaded and ranked -
        # the full-store path costs 0.4 ms per node and the literature layer (46,763 nodes) would take 18 s
        # against this hook's 2.5 s budget. Without an index the path below is byte-for-byte the old one; any
        # failure in the prefilter falls back to it. The signal cache is skipped on the prefilter path (300
        # nodes index on the fly in under a second; the 97 MB cache costs more to load than it saves).
        prefilter_ids = None
        pinned_ids = []
        try:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import math_prefilter
            if os.path.exists(math_prefilter.index_path(STORE_PATH)):
                prefilter_ids = math_prefilter.select(STORE_PATH, _expand_aliases(prompt), k=PREFILTER_K)
                # a result the prompt CITES by LaTeX label is served ahead of the ranked neighbours (2026-09-07:
                # "(ref lem:hitting_prob)" in a proof step -> that lemma's statement, from the literature layer)
                try:
                    pinned_ids = math_prefilter.pinned(STORE_PATH, prompt)
                except Exception:
                    pinned_ids = []
                if prefilter_ids is not None and pinned_ids:
                    prefilter_ids = pinned_ids + [i for i in prefilter_ids if i not in pinned_ids]
        except Exception:
            prefilter_ids = None
        if prefilter_ids is None:
            try:
                from math_brain.retrieval import load_signal_cache
                load_signal_cache(os.path.join(os.path.dirname(STORE_PATH), "brain_signal_cache.pkl"))
            except Exception:
                pass
        else:
            # the pooled candidates' signals from the keyed sidecar (math_brain.retrieval.load_signal_db, mb10d 24 Sep):
            # one query instead of ~300 recomputations; absent or stale, retrieve() computes them as before
            try:
                from math_brain.retrieval import load_signal_db, signal_db_path
                load_signal_db(signal_db_path(STORE_PATH))
            except Exception:
                pass

        # the package copy is the maintained one; this file keeps a fallback
        global _render_parts
        try:
            from math_brain.rendering import render_parts as _render_parts
        except Exception:
            pass

        # coverage abstention (see COVERAGE_MIN): a pinned citation is served regardless, and an ENUMERATION question
        # (self-avoiding / lattice / enumerate ...) is exempt - the H1 health probe is short AND covers little of itself,
        # and the 09-05 margin rule exists to keep that shape speaking (measured 02:5x on every bank + the 11 speak
        # probes: the exemption keeps them all at 11/11 while absent-target fires still fall 34 -> 30 of 40)
        if prefilter_ids and not pinned_ids and COVERAGE_MIN > 0 and not ENUMERATION_ANCHORS.search(prompt):
            try:
                _ps0 = getattr(math_prefilter, "LAST_SCORES", {}) or {}
                _qs0 = float(getattr(math_prefilter, "LAST_QSELF", 0.0) or 0.0)
                if _ps0 and 0 < _qs0 <= COVERAGE_QSELF_MAX and (max(_ps0.values()) / _qs0) < COVERAGE_MIN:
                    # a named result of a wider kind whose card the store holds is not abstained either (arm cand71): the
                    # gate route admitted "How does Stampacchia's iteration give an L-infinity bound?" and this check then
                    # silenced it at 0.406, the store's own "Stampacchia's iteration" card unserved
                    if not _coverage_ident_exempt(prompt, _ps0) and not (GATE_NAMED_KIND and _named_kind_card(prompt)):
                        if COV_AGREE and _same_card(max(_ps0, key=_ps0.get), _semantic_top(prompt)):
                            _silence_note(data, "coverage_agree_prefilter", harness_only=True)
                            _COV_AGREED["prefilter"] = max(_ps0, key=_ps0.get)
                        else:
                            _silence_note(data, "coverage_gate_prefilter:%.3f" % (max(_ps0.values()) / _qs0))
                            return
            except Exception:
                pass
        with ResearchStore.open_read_only(STORE_PATH) as store:
            # Keep node and revision-edge reads in one short snapshot; the review reads come after the selection, for
            # the shown cards only (_review_reads).
            store.connection.execute("BEGIN")
            if prefilter_ids is not None:
                if not prefilter_ids:
                    _silence_note(data, "prefilter_empty")
                    return
                nodes, edges = _prefilter_revision_nodes(store, prefilter_ids)
                _set_pool_adjacency(edges)
            else:
                nodes = list(store.iter_nodes())
                edges = list(store.iter_edges())
                _set_pool_adjacency(())        # the full store's links are not built here (cost); none carry over
        if not nodes:
            _silence_note(data, "no_nodes")
            return
        _elapsed = time.time() - started
        if _elapsed > TIME_BUDGET_S:
            # The budget was spent before a single card was ranked - only ever true on a loaded box, where the
            # 0.15 s prefilter-and-load stage measured on a quiet machine runs 50x slower. Until 2026-09-12 this
            # returned silently and unlogged, and it is the exit the silence log could not see. Now: log it, and
            # proceed anyway while the overrun is bounded (MATHBRAIN_BUDGET_GRACE, default 1.5 x the budget) -
            # the expensive loading is already done, and ranking plus one integrity read is the cheap tail. Past
            # the grace it still refuses, so the prompt is never held indefinitely.
            try:
                _gf = float(os.environ.get("MATHBRAIN_BUDGET_GRACE", "1.5") or 1.5)
            except Exception:
                _gf = 1.5
            if _elapsed > _gf * TIME_BUDGET_S:
                _silence_note(data, "budget_exhausted_before_ranking:%.1fs" % _elapsed)
                return
            _silence_note(data, "budget_exceeded_before_ranking_proceeding:%.1fs" % _elapsed)
        _load_expansion_once()
        result = retrieve(
            _expand_aliases(prompt),
            nodes=nodes,
            edges=edges,
            limit=CANDIDATE_POOL,
            min_support_score=0.0,
            min_margin=0.0,
        )
        if not result.hits:
            _silence_note(data, "no_hits")
            return
        _hits_for_select = result.hits
        if PREFLOOR and prefilter_ids is not None and not pinned_ids:
            try:
                _psf = getattr(math_prefilter, "LAST_SCORES", {}) or {}
                _qsf = float(getattr(math_prefilter, "LAST_QSELF", 0.0) or 0.0)
                if _psf:
                    _flr = min(GATE_FLOOR, GATE_FRAC * _qsf) if (GATE_FRAC > 0 and _qsf > 0) else GATE_FLOOR
                    _hits_for_select = [h for h in result.hits if float(_psf.get(h.node.id, 0.0)) >= _flr]
            except Exception:
                _hits_for_select = result.hits
        # ARGUMENT CARD BUDGET (MATHBRAIN_ARG_CARDS, default 4 since 2026-09-13). A prompt carrying a numbered argument
        # asks about several results at once - the 77 hard proof-audit items name 4.0 results each - while a lookup
        # question asks about one. Only argument-shaped prompts widen; every other prompt keeps three, so this dial
        # cannot add a card to a lookup and cannot move the absent-target fire rate.
        #
        # WHY 4, measured 2026-09-13 on those 77 items (same instrument, same bank, one run per cell):
        #     hook        cap   cards landed   block chars   named results reached
        #     verbose      3        2.86           2470          142/310   <- what shipped until today
        #     verbose      4        3.03           2613          145/310
        #     compressed   3        2.87           1965          142/310
        #     compressed   4        3.68           2484          160/310   <- this
        #     compressed   5        3.74           2526          151/310
        # The cap was never the binding constraint - the BUDGET was. A verbose card cost ~845 characters, so three of
        # them plus the old 407-character header filled the 3,000-char block and the assembler (whole cards only) threw
        # the fourth away: asking for four landed 3.03. Cutting the scaffolding (see the header and _trust_tag) made a
        # fourth card affordable, and it is worth 18 more named results than the shipped setting for 14 more characters.
        # Five is worse than four: the block barely grows (3.74 cards) but the extra slot changes WHICH cards are
        # selected and displaces a better one - more candidates, less reach.
        try:
            _argc = int(os.environ.get("MATHBRAIN_ARG_CARDS", "4") or 4)
        except Exception:
            _argc = 4
        _cap = _argc if (_argc > 3 and _argument_shape(prompt)) else 3
        # A QUESTION THAT NAMES N ENTRIES NEEDS N CARDS (MATHBRAIN_ENUM_WIDTH, default 10 = the most it will
        # widen to; 0 disables). Until 2026-09-13 every lookup prompt got exactly three cards, because the card
        # budget above only widens on ARGUMENT_SHAPE. That is fine when a question asks about one thing and a
        # coincidence when it asks about three - and a silent, total failure when it asks about four.
        #
        # Measured on a bank built to find this (L6, 240 items asking for the sum of N ledger pages, N = 3..8;
        # every page lives in one card's body, so the item is answerable exactly when the block carries all N):
        #     N = 3   37/40 answerable   2.92 cards served
        #     N = 4    0/40              2.90
        #     N = 5    0/40              2.75
        #     N = 6    0/40              2.90
        #     N = 7    0/40              2.85
        #     N = 8    0/40              2.95
        # A cliff, not a slope. The block serves ~2.9 cards however many the question needs, so a question
        # naming four or more stored entries cannot be answered from it at all. The 1,000-item A/B scored the
        # page families 100 pct because those items name exactly THREE problems and the cap is exactly three;
        # the perfect score was the bank agreeing with the dial, not the brain having no ceiling.
        #
        # The quoted-title extractor already counts what the prompt names, so the cap can simply follow it. Only
        # prompts that explicitly name several stored entries widen - which is exactly when the reader wants them
        # all - and the block budget below widens with it, or the whole-cards-only assembler would throw away
        # everything past the third card and nothing would change.
        try:
            _enum_max = int(os.environ.get("MATHBRAIN_ENUM_WIDTH", "10") or 10)
        except Exception:
            _enum_max = 10
        # WIDEN FOR ENTRIES THE STORE HAS, NOT FOR QUOTATION MARKS. Counting quoted titles was the first
        # version and it spent the owner's context on questions the brain cannot answer: measured on a control
        # bank of 80 enumeration questions whose every quoted title is ABSENT from the store (built by
        # recombining halves of real ledger titles, each checked absent on its full text), the hook fired on
        # 80 of 80 and the first version scaled the damage with the question - 3.0 cards and 2,408 characters
        # at three names, 8.0 cards and 6,008 at eight. Being wrong at eight cards is not better than being
        # wrong at three. So the cap follows the number of named entries the CANDIDATE POOL can actually cover:
        # on the L6 bank every name matches a card and the block widens; on the absent-title control nothing
        # matches and it stays at three, which is where it was before any of this.
        _named_entries = 0
        _named_extra = False   # a fifth distinct named result earned a fifth card (MATHBRAIN_NAMED_EXTRA_SLOT)
        _quoted_n = 0
        _covered_titles = []   # the quoted names the pool can actually cover, IN ORDER - see the sub-query stage
        if _enum_max > 0 and not pinned_ids:
            try:
                _quoted = _quoted_subqueries(prompt, limit=_enum_max)
                _quoted_n = len(_quoted)
                # THREE, not four. The count is wanted for two different decisions - widen the cap above three,
                # and abstain at zero - and computing it only when the cap would widen is a trap: at exactly
                # three quoted titles it would stay 0 and the abstention below would silence every three-title
                # question, which is the 473 items of the 1,000-item bank the brain answers perfectly.
                if _quoted_n >= 3:
                    _pool_titles = [({w for w in re.findall(r"[a-z][a-z-]{2,}",
                                                            str(getattr(h.node, "title", "") or "").lower())
                                      if w not in _TITLE_STOP}) for h in _hits_for_select]
                    for _q in _quoted:
                        _qw = {w for w in re.findall(r"[a-z][a-z-]{2,}", _q.lower()) if w not in _TITLE_STOP}
                        if not _qw:
                            continue
                        # CONTAINMENT, not overlap. "Shares two distinctive words" looked reasonable and was
                        # useless here: the control's absent titles are built by recombining halves of two real
                        # titles, so they share plenty of words with real cards and every one of them passed.
                        # The store HAS an entry only when some card's title carries every distinctive word of
                        # the name asked for - which is exactly how the control bank defines absence, so the two
                        # cannot disagree by construction.
                        if any(_qw <= _tw for _tw in _pool_titles):
                            _named_entries += 1
                            _covered_titles.append(_q)
                if _named_entries > _cap:
                    _cap = min(_named_entries, _enum_max)
            except Exception as _exc:
                _silence_note(data, "enum_width_error:%s" % type(_exc).__name__)
        # NOTHING IT ASKED FOR IS HERE (MATHBRAIN_ENUM_ABSTAIN, default 1). A question that quotes three or more
        # stored-entry titles and whose every quoted title is missing from the candidate pool is asking about
        # things this store does not hold, and serving three vocabulary neighbours answers nobody. Measured on a
        # control bank of 80 such questions - titles built by recombining halves of real ledger entries, each
        # verified absent on its full text, by a detector itself controlled against known-present titles - the
        # hook served a block on 80 of 80. His 2026-09-02 order is that the brain fire less and be surer when it
        # does, and this is the clearest case of firing on nothing that any bank here has produced.
        #
        # Narrow by construction, and the scoping was checked before the rule was written: of the 1,000-item
        # bank, only op_oddsource, op_pagemax and op_pagesum quote three or more long titles at all (473 items,
        # every one of which the brain answers), and no proof-audit item, no saw_* item, and nothing in either
        # half of the fresh bank does. A question with even ONE of its quoted titles in the pool still serves.
        # THE QUOTES MUST BE WHAT THE PROMPT IS ABOUT (MATHBRAIN_ENUM_MIN_SHARE, default 0.15; 0 = the old rule;
        # 2026-11-03, claude-1e0a23c2). The held bank wraps every one of its 788 long prompts in the same three
        # quoted instruction phrases, and this rule read them as three absent titles: 104 of the 121 held_1 items
        # that went silent with their gold card in the store died here. An enumeration question is mostly its
        # quoted names (share well above 0.3); in those prompts the quotes are at most 5.8 pct of the words.
        try:
            _enum_share_min = float(os.environ.get("MATHBRAIN_ENUM_MIN_SHARE", "0.15") or 0)
        except Exception:
            _enum_share_min = 0.15
        _enum_share = 1.0
        if _enum_share_min > 0 and _quoted_n >= 3:
            try:
                _qwords = sum(len(" ".join(m.group(1).split()).split()) for m in _QUOTED_RE.finditer(prompt)
                              if len(m.group(1).split()) >= 2)
                _enum_share = _qwords / max(1, len(prompt.split()))
            except Exception:
                _enum_share = 1.0
        if (os.environ.get("MATHBRAIN_ENUM_ABSTAIN", "1") != "0"
                and _quoted_n >= 3 and _named_entries == 0 and not pinned_ids
                and _enum_share >= _enum_share_min):
            _silence_note(data, "enum_none_of_%d_titles_in_store" % _quoted_n)
            return
        # DEFINITIONS DO NOT DECIDE STEPS - AND DEMOTING THEM STILL LOSES (MATHBRAIN_DEFN_DEMOTE, default 0 = OFF;
        # 2026-09-13). Kept as a measured negative so the next person to notice the 8 pct hit rate does not spend
        # the afternoon I spent. The diagnosis below is solid; the fix does not work. Measured across the 77
        # proof-audit items' 280 served slots: a definition card took 38 of them and named a result the argument
        # actually uses in 3 - an 8 pct hit rate against 53 for theorem, 49 for concept, 30 for lemma. That is
        # 12.5 pct of the block's capacity spent on cards that match the proof's VOCABULARY and say nothing about
        # whether a step follows. It happens because a proof-audit prompt is ~10,000 characters of mathematics in
        # which the four named results are a tiny share of the token mass, so BM25 over the whole prompt favours
        # definitions of the words it repeats.
        #
        # The existing MATHBRAIN_KIND_PREF reorders the CHOSEN cards and cannot help: measured at 121 -> 122 of
        # 280, because reordering never changes the set. `_select` re-sorts its input by rank score, so a
        # definition also cannot be demoted by handing it over later - it has to leave the candidate pool.
        # It leaves only when something else can fill the slot: if there are not enough non-definition candidates
        # to fill the cap, the definitions stay and nothing is lost. Argument-shaped prompts only - on a lookup
        # ("what is a tight family of measures?") the definition IS the answer.
        #
        # AND IT MEASURES WORSE: 155/310 with the demotion against 160/310 with it off, same instrument and bank,
        # back to back. The arithmetic that predicted +17 assumed the freed slots would be filled at the theorem
        # rate; they are not. The mechanism is almost certainly that this pool feeds gates computed RELATIVE TO
        # THE TOP CARD - the margin test's leader and runner-up, the campaign-family exemption, the scaled
        # on-topic floor - so removing candidates moves the leader and changes which rivals exist, and a block
        # that was served can go silent or lose a good card for reasons that have nothing to do with definitions.
        # The lesson for the next attempt: in this hook a candidate is not just a card, it is part of the
        # comparison that decides whether ANY card goes out. Filtering the pool is not a neutral operation, and a
        # kind-aware selector has to work inside `_select` alongside those gates rather than upstream of them.
        if (os.environ.get("MATHBRAIN_DEFN_DEMOTE", "0") != "0"
                and _cap > 3 and _argument_shape(prompt) and not pinned_ids):
            try:
                _nondef = [h for h in _hits_for_select
                           if str((_node_meta(h.node).get("claim_kind")
                                   or _node_meta(h.node).get("kind")
                                   or getattr(h.node, "kind", "") or "")).lower() != "definition"]
                if len(_nondef) >= _cap:
                    _hits_for_select = _nondef
            except Exception:
                pass
        chosen = _select(_hits_for_select, cap=_cap, prompt=prompt)
        if pinned_ids:
            pin = [h for h in result.hits if h.node.id in pinned_ids][:2]
            rest = [h for h in (chosen or []) if h.node.id not in pinned_ids]
            chosen = (pin + rest)[:_cap] if pin else chosen
        elif os.environ.get("MATHBRAIN_NAME_COVER", "1") != "0":
            # NAME COVERAGE (2026-09-13): the sibling-theorem miss - Matrix Bernstein asked, scalar Bernstein
            # served first with the named card at rank 2. A card that carries EVERY name token of the question
            # moves ahead of cards that do not; nothing else changes (see _name_cover_rerank).
            chosen = _name_cover_rerank(prompt, chosen)
            chosen = _title_cover_rerank(prompt, chosen)
            chosen = _defn_below_result_rerank(chosen)
        # COVERAGE SWAP (MATHBRAIN_COVER_SWAP, default 1; 2026-09-13). An argument that names four results wants
        # one card per result, and `_select` is not built to deliver that: it ranks by score, so a prompt whose
        # strongest signal is one theorem can spend three of four slots on that theorem's neighbourhood while the
        # other three named results go unserved.
        #
        # Measured on the 77 proof-audit items, splitting all 310 named results by WHERE the miss happens:
        #     served in the block  160  52%
        #     in pool, not served   94  30%   <- this: the ranker HAD the card and chose something else
        #     never retrieved       56  18%   <- the prefilter's problem, not the selector's
        # So the pool already holds cards for 94 results the block never shows, and perfect selection would read
        # 254/310 rather than 160.
        #
        # This is a SWAP, deliberately, not a filter. Filtering definitions out of the candidate pool measured
        # WORSE (155 against 160) because the pool also supplies the comparisons the margin test, the campaign
        # exemption and the scaled on-topic floor run on - remove a candidate and you move the leader and change
        # whether anything is served at all. So the pool is left untouched, `_select` decides as before, and only
        # then do slots that cover NO named target give way to pool cards that cover one. Every swapped card still
        # faces the on-topic gate, the integrity read and the dedup below, exactly as a chosen card would.
        # MEASURED AND OFF (default 0 since the same day it was written). The swap fires - 2 to 3 swaps on a
        # typical argument item - and moves the metric not at all: 160/310 with it and 160 without at the shipped
        # budget, 162 and 162 at 3,400 characters, card titles bit-identical. The reason is upstream of the idea:
        # the swap can only cover targets the EXTRACTOR names, and the extractor finds one to three "X theorem"
        # shaped names per item while these arguments lean on about four results, many of them descriptive
        # ("upper semicontinuity of an infimum of continuous functions") that no name-shaped regex will ever
        # catch. Coverage selection is not wrong; it is starved of targets. Left in, defaulted off, because the
        # day the target list gets better this is the machinery that spends it - see COVERAGE_CEILING in the
        # campaign record for what it is worth with perfect targets.
        # ON for enumeration prompts, whatever the dial says. The swap was a wash on argument prompts because
        # four slots were already full of cards that earn, so covering one target meant dropping another. An
        # enumeration prompt is the opposite case and the one where this machinery finally pays: the cap has just
        # been widened to the number of entries the question NAMES, so the seats exist, and the targets are not
        # guessed from prose - the question quotes them. Widening alone is not enough; measured on two L6 items
        # it seated 6 and 8 cards and still carried only 4 of the needed pages, because the ranker fills the new
        # seats by score. This puts the named entries in them.
        _enum_cover = _named_entries > 3
        if (chosen and not pinned_ids
                and (_enum_cover
                     or (os.environ.get("MATHBRAIN_COVER_SWAP", "0") != "0"
                         and _cap > 3 and _argument_shape(prompt)))):
            try:
                if _enum_cover:
                    # the question quotes exactly what it wants; nothing else belongs in the target list
                    _targets = list(_quoted_subqueries(prompt, limit=_enum_max))
                else:
                    _targets = list(_named_result_subqueries(prompt)) + list(_quoted_subqueries(prompt))
                # INVERTED TARGETS (MATHBRAIN_COVER_INVERT, default 1 when the swap runs). Extracting names from
                # 10,000 characters of prose is open-ended and the regex only catches "X theorem" shapes, so it
                # finds one to three targets where the argument leans on four and misses every descriptive result.
                # Turn the question round: the pool is ~300 cards with known titles, so instead of guessing what
                # the prompt names, ask of each card whether the prompt SAYS what that card is about. A membership
                # test over a known vocabulary, no pattern needed, and descriptive titles - the ones the regex
                # cannot see - carry the most distinctive words and match the most reliably. Measured on the title
                # channel across the 77 items: 45 as it ships, 67 inverted, 135 with oracle targets.
                # Not on an enumeration prompt: there the question quotes its own targets exactly, so guessing
                # more of them can only displace one the reader actually asked for.
                if not _enum_cover and os.environ.get("MATHBRAIN_COVER_INVERT", "1") != "0":
                    _pw = {w for w in re.findall(r"[a-z][a-z-]{2,}", prompt.lower()) if w not in _TITLE_STOP}
                    _cands = []
                    for _h in _hits_for_select:
                        _tw = {w for w in re.findall(r"[a-z][a-z-]{2,}",
                                                     str(getattr(_h.node, "title", "") or "").lower())
                               if w not in _TITLE_STOP}
                        if len(_tw) < 2:
                            continue
                        # every distinctive word of the title is present in the prompt
                        if len(_tw & _pw) >= 2 and len(_tw & _pw) == len(_tw):
                            _cands.append((len(_tw), str(getattr(_h.node, "title", "") or "")))
                    _cands.sort(key=lambda x: -x[0])
                    _targets = [t for _, t in _cands[:6]] + _targets
                if _targets:
                    def _content(s):
                        return {w for w in re.findall(r"[a-z][a-z-]{2,}", str(s or "").lower())
                                if w not in _TITLE_STOP}

                    def _covers(node, target):
                        """Does this card's TITLE name that result? Deliberately NOT the grader's rule.

                        The instrument matches a name's first two content words; mirroring that here would be
                        tuning the hook to its own scoreboard. This asks a plainer question - do the target's
                        distinctive words appear in the title - and lets the instrument judge it at arm's length.
                        """
                        tw, nw = _content(getattr(node, "title", "")), _content(target)
                        if not nw or not tw:
                            return False
                        shared = tw & nw
                        return len(shared) >= 2 or (len(nw) == 1 and bool(shared))

                    _covered = lambda h: any(_covers(h.node, t) for t in _targets)
                    _ids = {h.node.id for h in chosen}
                    _swaps = 0
                    if os.environ.get("MATHBRAIN_SWAP_DEBUG"):
                        sys.stderr.write("[cover-swap] targets=%d chosen=%d pool=%d uncovered=%d victims=%d\n"
                                         % (len(_targets), len(chosen), len(_hits_for_select),
                                            sum(1 for t in _targets if not any(_covers(h.node, t) for h in chosen)),
                                            sum(1 for h in chosen if not _covered(h))))
                    for _t in _targets:
                        if any(_covers(h.node, _t) for h in chosen):
                            continue
                        _cand = next((h for h in _hits_for_select
                                      if h.node.id not in _ids and _covers(h.node, _t)), None)
                        if _cand is None:
                            continue
                        _victims = [h for h in chosen if not _covered(h)]
                        if not _victims:
                            break
                        _victim = _victims[-1]
                        chosen = [_cand if h is _victim else h for h in chosen]
                        _ids.discard(_victim.node.id)
                        _ids.add(_cand.node.id)
                        _swaps += 1
                    if os.environ.get("MATHBRAIN_SWAP_DEBUG"):
                        sys.stderr.write("[cover-swap] swaps made=%d\n" % _swaps)
            except Exception as _exc:
                # Never silently. A swallowed NameError in this file once turned the whole on-topic gate into a
                # no-op and invalidated three measurements before anyone noticed; a stage that fails must say so.
                _silence_note(data, "cover_swap_error:%s: %s" % (type(_exc).__name__, _exc))
        # 2026-09-07 ON-TOPIC GATE (brain-100x goal): a non-pinned note must score at least max(GATE_FLOOR,
        # GATE_RATIO x top) on the prefilter, else it is not served - a weak best match is an off-topic note, and
        # off-topic notes are what cost the morning brain 6.7 pp against sterile on the proof bank. A Mathlib
        # declaration takes at most one slot and only at 0.6 x top. Pinned citations are exempt.
        if chosen and prefilter_ids is not None:
            try:
                ps = getattr(math_prefilter, "LAST_SCORES", {}) or {}
                if ps:
                    # 2026-09-11 (evening): the ratio is measured against the best prefilter score AMONG THE CHOSEN
                    # cards, not the prefilter's global best. The ranker (retrieve) is the primary judge; when its
                    # top card was not the prefilter's favourite, the old rule let the prefilter overrule it and
                    # dropped the card that carried the answer (locate bank: a wordy 99-token question put a long
                    # survey node at prefilter 72.8, the answer node at 18.4, and 0.45 x 72.8 removed the answer).
                    # The floor still applies to every card; the ratio now removes only cards much weaker than the
                    # best card the ranker itself chose.
                    _chosen_ps = [float(ps.get(h.node.id, 0.0)) for h in chosen if h.node.id not in pinned_ids]
                    top = max(_chosen_ps) if _chosen_ps else max(ps.values())
                    # the floor scales with the question (2026-09-11, see GATE_FRAC): a one-line question cannot
                    # reach a sum a 2,000-character proof reaches by length alone
                    qself = float(getattr(math_prefilter, "LAST_QSELF", 0.0) or 0.0)
                    floor = min(GATE_FLOOR, GATE_FRAC * qself) if (GATE_FRAC > 0 and qself > 0) else GATE_FLOOR
                    # THE QUESTION PART (MATHBRAIN_GATE_QPART, 19 Oct, claude-5693aca3): a question asked
                    # after a long lead-in carries a self-score the lead-in inflates, so the floor sits at its cap
                    # (25) while the cards the ranker chose score 12-25 on the whole prompt (embedded v2 trace: 15
                    # of 16 silent rows lost every chosen card here). A card that fails the whole-prompt floor is
                    # kept when, scored on the prompt's closing paragraph (else its tail) alone, it clears that
                    # part's own scaled floor - both numbers on one scale.
                    _qp_sc, _qp_floor = {}, None
                    if GATE_QPART and GATE_FRAC > 0:
                        try:
                            _qpart = _closing_paragraph(prompt) or _prompt_tail(prompt, COH_TAIL or 250)
                            if _qpart and len(_qpart) < len(prompt):
                                _qd = math_prefilter.load(STORE_PATH)
                                _qtok = set(math_prefilter.tokens(_qpart))
                                _qN, _qpost = _qd["n"], _qd["post"]
                                import math as _qm
                                _qself = sum(_qm.log(1.0 + (_qN - len(_qpost[t]) + 0.5) / (len(_qpost[t]) + 0.5))
                                             for t in _qtok if t in _qpost)
                                if _qself > 0:
                                    _qp_floor = min(GATE_FLOOR, GATE_FRAC * _qself)
                                    _ix = {nid: i for i, nid in enumerate(_qd["ids"])}
                                    _raw = math_prefilter._scores(_qd, _qpart)
                                    _qp_sc = {h.node.id: float(_raw.get(_ix.get(h.node.id, -1), 0.0)) for h in chosen}
                        except Exception:
                            _qp_sc, _qp_floor = {}, None
                    _qp_resc = set()
                    keep, n_ml = [], 0
                    for h in chosen:
                        nid = h.node.id
                        if nid in pinned_ids:
                            keep.append(h)
                            continue
                        sc = float(ps.get(nid, 0.0))
                        if sc < max(floor, GATE_RATIO * top):
                            # ... unless THE PROMPT NAMES THIS CARD (MATHBRAIN_TITLE_EXEMPT, default 0 under
                            # measurement 2026-09-13). The floor exists because "a weak best match is an off-topic
                            # note", but a long question naming one narrow note scores low by LENGTH, not by being
                            # off topic: locate item l1_c02 names the "FPP chi=1/3 campaign" and its own campaign
                            # note tops the ranker at 22.8 against a scaled floor of 23.4 - lost by 0.6. When two or
                            # more distinctive words of the card's TITLE appear in the prompt, the match is not weak.
                            # MEASURED 2026-09-13 and left OFF: campaign locate 18 -> 19 of 20, but unnamed served
                            # 72 -> 71 of 89, rank-1 54 -> 49, absent-target fires 27 -> 28 of 40. It does not even
                            # rescue the item it was built for: l1_c02 asks about off-U increments while the card it
                            # needs is titled for the block-increment exponent, so the prompt never carries its words.
                            _qp_ok = (_qp_floor is not None and sc >= GATE_RATIO * top
                                      and _qp_sc.get(nid, 0.0) >= _qp_floor)
                            if not (_qp_ok or (TITLE_EXEMPT and _prompt_names_card(prompt, h.node.title))):
                                continue
                            if _qp_ok:
                                _qp_resc.add(nid)
                        if str(_node_meta(h.node).get("claim_kind") or "") == "lean_declaration":
                            if n_ml >= 1 or sc < 0.6 * top:
                                continue
                            n_ml += 1
                        keep.append(h)
                    # A LONE RESCUE (MATHBRAIN_QPART_SOLO, 19 Oct, claude-5693aca3): a block whose only card is one the
                    # question part rescued has nothing beside it to corroborate it; it stays only when its closing-
                    # paragraph score clears that part's floor by the factor SOLO (0 = off). Real bank, rows under 25 s in
                    # every arm: question part alone 74 hits / 13 wrong, with SOLO 1.5 73 / 9 (SOLO 2: 71 / 9). The 1.5 is
                    # fitted on the 5 lone rescues it moves; it can only remove cards the question part added.
                    if (QPART_SOLO > 0 and len(keep) == 1 and keep[0].node.id in _qp_resc
                            and _qp_sc.get(keep[0].node.id, 0.0) < QPART_SOLO * (_qp_floor or 0.0)):
                        keep = []
                    chosen = keep
            except Exception:
                pass
        # PREFLOOR mode 2, the RESCUE (see PREFLOOR_MODE): nothing above changes; only an empty block or a missing
        # lexical favourite does. (a) The gated block is empty although floor-passing candidates exist: re-choose among
        # them and gate again. (b) The prefilter's favourite passes the floor, is not in the block, and a slot is free:
        # it takes the slot (never an evidence node, a Mathlib declaration, a harvest deposit under the cap, or a title
        # already in the block). Traced 2026-09-12 on the locate bank: an FPP question whose answer node was the
        # lexical favourite at 72.8 lost its slot to two cards the gate then removed; two others went out empty while
        # the notes carrying the value sat above the floor at ranks 4-13.
        # mode 3 is (b) alone and only into a NON-EMPTY block: an empty block stays empty, so the abstention behaviour is
        # untouched by construction (mode 2 measured 2026-09-12 03:1x: absent-target fires 27 -> 31 of 40, the rescue of
        # empty blocks fires where the abstention lives, and the lexical coverage ratio does not separate the two).
        if PREFLOOR_MODE in (2, 3) and prefilter_ids is not None and not pinned_ids:
            try:
                _ps2 = getattr(math_prefilter, "LAST_SCORES", {}) or {}
                _qs2 = float(getattr(math_prefilter, "LAST_QSELF", 0.0) or 0.0)
                if _ps2 and (PREFLOOR_MODE == 2 or chosen):
                    _flr2 = min(GATE_FLOOR, GATE_FRAC * _qs2) if (GATE_FRAC > 0 and _qs2 > 0) else GATE_FLOOR
                    if not chosen and PREFLOOR_MODE == 2:
                        _fh = [h for h in result.hits if float(_ps2.get(h.node.id, 0.0)) >= _flr2]
                        _re = _select(_fh) if _fh else []
                        if _re:
                            _best = max(float(_ps2.get(h.node.id, 0.0)) for h in _re)
                            chosen = [h for h in _re if float(_ps2.get(h.node.id, 0.0)) >= max(_flr2, GATE_RATIO * _best)]
                    _fav = max(_ps2.items(), key=lambda kv: kv[1])[0]
                    if (float(_ps2.get(_fav, 0.0)) >= _flr2 and len(chosen or []) < 3
                            and all(h.node.id != _fav for h in (chosen or []))):
                        _fh = next((h for h in result.hits if h.node.id == _fav), None)
                        if _fh is not None:
                            _fm = _node_meta(_fh.node)
                            _ftitle = re.sub(r"[^a-z0-9]+", " ", (_fh.node.title or "").casefold()).strip()
                            _titles = {re.sub(r"[^a-z0-9]+", " ", (h.node.title or "").casefold()).strip() for h in (chosen or [])}
                            if (str(getattr(_fh.node, "kind", "") or "").lower() not in _UNSERVED_KINDS
                                    and str(_fm.get("claim_kind") or "") != "lean_declaration"
                                    and not (HARVEST_CAP and "harvest.arxiv" in str(_fm.get("logical_id") or ""))
                                    and _ftitle not in _titles):
                                chosen = list(chosen or []) + [_fh]
            except Exception:
                pass
        # the answer rides with its question (see _attach_answers) - placed AFTER the on-topic gate and the rescue on
        # purpose: the gate keeps a card only above GATE_RATIO x the prefilter's top score, and a theorem statement
        # scores below the question it settles on the question's own words (traced 2026-09-20 on the Ross question:
        # _select ranked the counterexample first and the gate removed it). The edge is the on-topic evidence.
        chosen = _attach_answers(chosen, result.hits, edges)
        _trace_answers('after_attach', chosen)
        # the clause split (see _clause_subqueries): each tuple-carrying clause earns its own block; merged round-robin
        # ahead of the whole-question cards, three slots, pins untouched (a pinned prompt skips the split)
        _split_used = False
        # AN EMPTIED BLOCK IS REFILLED ONLY BY A SUB-QUERY THAT NAMES SOMETHING (MATHBRAIN_SUBQ_REFILL, default src; campaign
        # mb10d, 25 Sep, arms cand38-cand41). The sub-queries below run after the on-topic gate and the answer attachment;
        # when those have emptied the whole-question block, the merged block is made of sub-query cards alone (and a used
        # split also skips the coverage floor). On his 483 past prompts that is how two junk blocks were served, both from
        # QUOTED PHRASES the store holds no title for, one sub-query each: a pasted order-10 MOLS search status quoting
        # 'near triple,' got "Steiner Triple System" and a prompt of submission-formatting advice quoting 'see Theorem 1 in
        # Supplementary Material' got a Bradley card. The other sources are designed rescues of an empty block: the clause
        # split serves each fact of a two-fact question (the gate's locate bank, l3_f9682d4074c1 - silenced under cand40's
        # "arg"), the named-result sub-queries serve the pivots of an argument (3 of 310 titled pivots on the proof-audit
        # bank without them), and quoted STORE titles serve the entries a question names. "src" refuses only the quoted
        # phrases the store lacks; "arg" (cand40) refills only on argument-shaped prompts; 1 everywhere; 0 never.
        _subq_mode = os.environ.get("MATHBRAIN_SUBQ_REFILL", "src").strip().lower()
        _subq_refill = (_subq_mode not in ("0", "off", "false", "", "arg")
                        or (_subq_mode == "arg" and _argument_shape(prompt) is not None))
        _subq_src = ""
        _pre_subq_chosen = list(chosen or [])
        if CLAUSE_SPLIT and prefilter_ids is not None and not pinned_ids and (chosen or _subq_refill):
            try:
                subs = _clause_subqueries(prompt)
                _subq_src = "clause" if subs else ""
                # On an argument-shaped prompt the clause split is dead (it bails above 1,500 chars), so the named
                # results are the only sub-queries available. ON by default since 2026-09-12 16:1x, measured on the
                # 77 authored proof-audit items: a served card is TITLED with a result the argument actually uses on
                # 34 items against 24 before (44 pct against 31), titled hits 38 against 24 of 311. Every other bank
                # is unmoved to the digit (health 11/6, fresh bank 71 and 84 of 89, absent fires 21/33 strict, locate
                # reach, value-in-block) - the gate is narrow and those banks carry no argument-shaped prompt.
                # MATHBRAIN_NAMED_SUBQ=0 turns it off.
                # MATHBRAIN_NAMED_SUBQ_ANY=1 (default 0, under measurement 2026-09-12 23:xx) extends the named
                # sub-queries to EVERY prompt that names a result, not only argument-shaped ones: a plain question
                # such as "does the Vitali-Caratheodory theorem need regularity" names its pivot just as a proof does.
                # The guard against it is the absent-target bank: a named result the store lacks would pull its
                # nearest neighbour and fire falsely.
                if not subs and os.environ.get("MATHBRAIN_NAMED_SUBQ", "1") == "1" and (
                        _argument_shape(prompt) or os.environ.get("MATHBRAIN_NAMED_SUBQ_ANY", "0") == "1"):
                    subs = _named_result_subqueries(prompt)
                    _subq_src = "named" if subs else _subq_src
                # Quoted titles (MATHBRAIN_QUOTED_SUBQ, ON by default since 2026-09-13 02:4x): a prompt naming stored
                # entries by their quoted titles gets one sub-query per title - see _quoted_subqueries. Measured: 23 of
                # the 28 items the 1,000-item test found mute now serve (56 of 66 titles named); fresh bank unnamed
                # 71 -> 72 of 89, named 84 unchanged; absent-target fires 28 -> 27 of 40 (strict 21 -> 20 of 33);
                # health 11 speak / 6 silent either way. =0 turns it off.
                if not subs and os.environ.get("MATHBRAIN_QUOTED_SUBQ", "1") == "1":
                    # One sub-query per title the question quotes, not a fixed four. The default limit of 4 was
                    # invisible while every bank item named three entries; on the L6 bank it is the whole ceiling.
                    # A question naming eight problems ran four sub-queries, so four of its eight cards could
                    # never enter the pool and the block carried 4 of 8 pages however many slots it was given -
                    # widening the cap alone moved N=4 from 0/40 to answerable and left N=6 and N=8 stuck at 4.
                    #
                    # ASK FOR THE TITLES THE STORE HAS, NOT THE FIRST N OF THEM. Passing a COUNT was wrong
                    # whenever the quoted names are a mix: `_quoted_subqueries` returns the first N titles
                    # POSITIONALLY, so a question naming two problems the store lacks and three it holds - the
                    # ordinary shape when somebody misremembers a name - spent its sub-queries on the dead ones
                    # and never asked for the live ones. Measured on a bank built for it (L9, absent names
                    # deliberately first): the block served every time, correctly, and carried the pages of
                    # only 22 of 59 present titles at a flat 3.0 cards. The covered titles are collected above
                    # in the order they appear, so here they are simply used.
                    subs = _covered_titles or _quoted_subqueries(prompt, limit=4)
                    _subq_src = ("quoted_title" if _covered_titles else "quoted_phrase") if subs else _subq_src
                if subs and not chosen and _subq_mode == "src" and _subq_src == "quoted_phrase":
                    subs = []
                if os.environ.get("MATHBRAIN_SUBQ_OFF", "0") == "1":
                    subs = []
                if subs and (time.time() - started) < 0.5 * TIME_BUDGET_S:
                    # A TIME GUARD INSIDE THE LOOP (MATHBRAIN_SUBQ_TIME_FRAC, default 0.6; arm cand55). The stage used to check
                    # the budget once, before its first sub-query, and then run them all: with the named limit at 8 a long
                    # argument on a loaded machine ran past the budget, and past it the integrity read keeps the TOP card only
                    # (the graceful degradation below) - four cards turned into one. Past the fraction no new sub-query starts;
                    # the earlier names, which the argument usually hinges on, keep theirs. Rehearsals run at a 300 s budget,
                    # so this acts on the 30 s interactive budget.
                    try:
                        _sq_frac = float(os.environ.get("MATHBRAIN_SUBQ_TIME_FRAC", "0.6") or 0.6)
                    except Exception:
                        _sq_frac = 0.6
                    blocks = []
                    for _sq in subs:
                        if blocks and (time.time() - started) > _sq_frac * TIME_BUDGET_S:
                            blocks.append([])
                            continue
                        blocks.append(_subquery_block(_sq, prompt=prompt, exact_pass=(_subq_src == "named")))
                    if _subq_src == "named" and NAMED_TITLEMATCH:
                        blocks = [[h for h in b if _named_title_match(q, h.node.title)] for q, b in zip(subs, blocks)]
                    merged = _merge_subquery_blocks(blocks, chosen, lead=(_subq_src == "named"))
                    chosen, _named_extra, _cprime_chosen = _named_slots_full(blocks, merged, _cap, _pre_subq_chosen,
                                                                             _subq_src == "named")
                    if _subq_src in ("named", "quoted_title"):
                        chosen, _named_extra = _subq_coh_guard(prompt, chosen, _named_extra, _pre_subq_chosen,
                                                               _cprime_chosen, _subq_src == "named")
                    _split_used = True
            except Exception:
                pass
        _bcv = _block_cov_verdict(prompt, chosen, pinned_ids, split_used=_split_used)
        if _bcv and _bcv.startswith("coverage_agree_block"):
            _silence_note(data, _bcv, harness_only=True)
        elif _bcv:
            _silence_note(data, _bcv)
            return
        if chosen:
            _kept = _lead_filter(prompt, chosen, pinned=set(pinned_ids or ()))
            if not _kept:
                _silence_note(data, "lead_unnamed:%d" % len(chosen))
                return
            chosen = _kept
        if chosen and not pinned_ids and _generic_only(prompt, chosen):
            _silence_note(data, "generic_only_match")
            return
        if not chosen:
            _silence_note(data, "no_chosen_after_select")
            return
        # Check only the final selected cards, including pinned citations.
        # Never refill rejected slots or alter ranking/margin thresholds.
        from math_brain.selection_integrity import consistent_hits, SelectionUnavailable
        remaining = TIME_BUDGET_S - (time.time() - started)
        _rkw = _refusal_kwargs()
        if _rkw is None:
            _silence_note(data, "integrity_api_incompatible")
            return
        try:
            chosen = consistent_hits(chosen, STORE_PATH,
                                     deadline=time.monotonic() + max(0.0, remaining), **_rkw)
        except SelectionUnavailable as exc:
            # GRACEFUL DEGRADATION (2026-09-12 22:4x). The integrity read RAISES when its clock runs out, and the
            # hook's outer except turned that into SILENCE: under ten max-effort solvers the whole hook slowed
            # ~50x, the budget was spent before this line, the deadline was already in the past, and the brain
            # served NOTHING - 0.0 cards on 77 argument prompts at a 0.6 s budget, 0.5 cards at 1.0 s, against
            # 2.8 cards and 59 of 77 pivots with time. A brain that goes mute exactly when the machine is busy,
            # with no trace, is not working fully. So: when the failure is the clock and not the store, spend a
            # small bounded grace on the TOP card alone and serve that. Integrity is intact - nothing is served
            # that the read did not pass - and the overrun is capped by MATHBRAIN_INTEGRITY_GRACE_S. Any other
            # SelectionUnavailable (a card changed under us, an incomplete note) still refuses, as before.
            if str(exc) != "integrity_budget_exhausted":
                _silence_note(data, "integrity:%s" % exc)
                raise
            try:
                _grace = float(os.environ.get("MATHBRAIN_INTEGRITY_GRACE_S",
                                              str(HEADLESS_GRACE_S) if _HEADLESS else "2.0") or 2.0)
            except Exception:
                _grace = 2.0
            _silence_note(data, "integrity_budget_exhausted:grace_top_card")
            chosen = consistent_hits(chosen[:1], STORE_PATH, deadline=time.monotonic() + _grace, **_rkw)
        from property_order import order_selected
        _trace_answers('after_consistent', chosen)
        chosen = order_selected(prompt, chosen, pinned_ids)
        _trace_answers('after_order', chosen)
        if not chosen:
            _silence_note(data, "no_chosen_after_order")
            return
        if _VOUCH["route"]:
            if not _vouch_agrees(prompt, chosen):
                _silence_note(data, "vouch_disagree")
                return
            _VOUCH["card"] = str(chosen[0].node.id)       # sealed against the block that goes out (below)
        # KIND PREFERENCE on an argument the reader is checking (MATHBRAIN_KIND_PREF, default 0 = off; 2026-09-12 15:0x).
        # Reordering only - the same cards, never a different set, never a refill. On a prompt that carries a numbered
        # argument, a theorem or lemma is what decides a step; a definition matches the vocabulary and says nothing about
        # whether the step follows. The block budget truncates, so order is what the reader actually gets. Seen on
        # pa_an_3_07 (a Vitali-Caratheodory argument): the two served cards were "Lebesgue Measure" and "Functions of
        # Bounded Variation", both definitions, while the pivot theorem sat lower or absent.
        if os.environ.get("MATHBRAIN_KIND_PREF", "0") == "1" and len(chosen) > 1 and _argument_shape(prompt):
            try:
                _pins = set(pinned_ids or ())
                _rank = {"theorem": 0, "lemma": 0, "proposition": 0, "corollary": 1, "claim": 1}
                def _kind_rank(h):
                    if h.node.id in _pins:
                        return -1
                    m = _node_meta(h.node)
                    k = str(m.get("claim_kind") or m.get("kind") or h.node.kind or "").lower()
                    return _rank.get(k, 2)
                chosen = [h for _, _, h in sorted(
                    ((_kind_rank(h), i, h) for i, h in enumerate(chosen)), key=lambda t: (t[0], t[1]))]
            except Exception:
                pass
        # CARD-COVERAGE gate (MATHBRAIN_CARDCOV_MIN, default 0 = off; 2026-09-12 08:3x). The block goes out only when the
        # first card carries at least this share of the prompt's rare-word (idf) weight. Measured on the fresh bank's
        # served first cards: AUC 0.913 for true hits (154) against absent-target fires (23); at 0.35 it silences 11 of
        # the 23 fires and 4 of 10 wrong serves and loses 9 of 154 true hits (all unnamed questions). A trade, not a free
        # cut - left OFF for the owner's decision. Pinned citations and enumeration questions are exempt.
        try:
            _ccm = float(os.environ.get("MATHBRAIN_CARDCOV_MIN", "0") or 0)
        except Exception:
            _ccm = 0.0
        # Argument-shaped prompts are exempt too (2026-09-12 23:xx): a 2,000-word proof's idf coverage by ONE card is
        # tiny by construction, so the gate silences the proof audits it should help - measured at CARDCOV_MIN=0.2,
        # pivot reach 59 -> 13 of 77 items and 2.8 -> 0.6 cards, while absent-target fires did not move (28/40 both
        # ways). The gate is a lookup-question instrument; on lookups 0.3 cuts absent fires 28 -> 22 of 40 for 7 of 89
        # unnamed theorems - still a trade for the owner, but no longer one that breaks proofs.
        if (_ccm > 0 and prefilter_ids is not None and not pinned_ids and not ENUMERATION_ANCHORS.search(prompt)
                and not _argument_shape(prompt)):
            try:
                import math as _m
                _d = math_prefilter.load(STORE_PATH)
                _N, _post = _d["n"], _d["post"]
                _qt = {t for t in math_prefilter.tokens(prompt) if t in _post}
                if _qt:
                    def _idf(t):
                        return _m.log(1.0 + (_N - len(_post[t]) + 0.5) / (len(_post[t]) + 0.5))
                    _top = chosen[0].node
                    _ct = set(math_prefilter.tokens(_card_index_text(_top)))
                    _w = sum(_idf(t) for t in _qt)
                    if _w > 0 and sum(_idf(t) for t in _qt & _ct) / _w < _ccm:
                        _silence_note(data, "cardcov_gate:%.3f" % (sum(_idf(t) for t in _qt & _ct) / _w))
                        return
            except Exception:
                pass
        sess_for_guard = data.get("session_id") or ""
        dup_ids = _already_injected(sess_for_guard, [h.node.id for h in chosen], record=False)
        # AN EXPLICITLY NAMED ENTRY IS NEVER A DUPLICATE (2026-09-13). The guard stops the hook re-injecting a
        # card the session has already seen, which is right for a session that keeps brushing the same topic by
        # accident. It is wrong when the question NAMES what it wants: measured on the L6 bank, asking the same
        # enumeration question twice down one session returned 5 cards and 5 of 5 pages the first time and ZERO
        # the second - total starvation, on a question whose cards ARE its answer. A different enumeration
        # question in the same session was unaffected (3 of 5 both ways), so the guard only bites on a repeat,
        # and a repeat is exactly when the reader is asking again because they no longer have the first block -
        # after a compaction, or to check a figure. Suppressing it serves nobody. Narrow on purpose: only
        # prompts that quote several stored entries, and the ids are still RECORDED below, so an ordinary lookup
        # in the same session is deduplicated exactly as before.
        if _named_entries > 3:
            dup_ids = set()
        chosen = [h for h in chosen if h.node.id not in dup_ids]
        if not chosen:
            _silence_note(data, "all_dedup_suppressed:%d" % len(dup_ids))
            return
        # SAME RESULT on the finished block (see SAME_RESULT): the cover swap and the named-problem lift can put a twin
        # back after _select, so the check runs here, and the first card of the final order is the one kept
        chosen = _drop_title_twins(_drop_later_twins(chosen))
        reads = _review_reads([h.node.id for h in chosen])
        parts = _claim_parts([h.node.id for h in chosen])
        pieces = []
        for hit in chosen:
            node = _claim_view(hit.node, parts.get(hit.node.id))
            meta = _node_meta(node)
            kind = str(meta.get("claim_kind") or meta.get("kind") or node.kind)
            label, text = _render_parts(node, query=prompt, max_text_chars=MAX_STATEMENT_CHARS)
            hint = _origin_prefix(node, meta) + _trust_tag(meta, reads.get(node.id))
            if _structure_flattened(text):
                hint = "notation flattened; " + hint
            if _source_is_excerpt(node, text):
                hint = "EXCERPT, incomplete; " + hint
            pieces.append((node.id,
                "- %s [%s]: %s\n    (%s | %s)"
                % (label, kind, text, node.id, hint)
            ))
        # Owner order 2026-09-02 ("a note can mean something good or bad, not necessarily good ... the
        # brain can give a hint"): the block frames every note as a lead that can cut either way. The
        # B12 bank measured why this matters - a served statement about the cited result made a wrong
        # citation or an over-extended scope look legitimate (citation/scope/sharpness faults lost ground
        # under notes while dropped-hypothesis faults gained).
        # 2026-09-13: said in 248 characters instead of 407, every clause kept - hint-not-verdict, cuts both
        # ways, a note is not evidence the step uses the result correctly, nothing verified, how to read the
        # full note. Measured on the 1,000 captured blocks of the 09-12 A/B: the header cost 407 chars of a
        # 3,000-char budget on EVERY block, and 487 of 999 blocks sat at the cap - so the boilerplate was
        # displacing mathematics on half the questions. See _trust_tag for the per-card half of the same cut.
        header = (
            "[math-brain] leads, not verdicts - a match can expose a step as easily as support it, and none "
            "of this is independently verified: check each result's hypotheses against the step that uses it. "
            "Full note: python ~/.claude/dreaming/math_note.py SOURCE_ID"
        )
        if ctx_used is not None:
            header += (" This prompt names no topic, so these follow the session's last mathematical prompt - "
                       "check they are about what you are working on.")
        # Whole rendered cards only. Source excerpts remain explicitly labeled.
        # The budget follows the cap. `_assemble_note_block` emits whole cards only, so a block sized for three
        # cards silently discards the fourth onward and widening the cap alone would change nothing (measured:
        # asking the old verbose hook for four cards landed 3.03). A compressed card runs about 720 characters,
        # so the room a widened prompt needs is the header plus that per card. Only prompts that named several
        # stored entries are widened; every ordinary prompt keeps MAX_BLOCK_CHARS exactly as before.
        _budget = MAX_BLOCK_CHARS
        if _named_entries > 3 and len(pieces) > 3:
            _budget = max(MAX_BLOCK_CHARS, len(header) + 760 * len(pieces))
        if _named_extra and len(pieces) > 4:
            _budget = max(_budget, len(header) + 760 * len(pieces))
        block, emitted_ids = _assemble_note_block(header, pieces, _budget)
        if (block and len(emitted_ids) > 1 and MATH_USE_WEIGHT > 0 and not _VOUCH["route"]
                and not (COV_SEAL and _COV_AGREED)):
            try:
                _pieces_u = _use_order_pieces(pieces, emitted_ids, {h.node.id: h for h in chosen})
                if _pieces_u is not pieces:
                    _bu, _idu = _assemble_note_block(header, _pieces_u, _budget)
                    if _bu and set(_idu) == set(emitted_ids):
                        pieces, block, emitted_ids = _pieces_u, _bu, _idu
            except Exception as _exc:
                _silence_note(data, "use_order_error:%s" % type(_exc).__name__)
        if block and emitted_ids and PITFALL_LINES:
            try:
                block, emitted_ids = _with_pitfall_lines(header, pieces, emitted_ids, _budget, prompt)
            except Exception as _exc:
                _silence_note(data, "pitfall_lines_error:%s" % type(_exc).__name__)
        if block and emitted_ids and COV_SEAL and _COV_AGREED:
            try:
                _sealed = _same_card(emitted_ids[0], _semantic_top(prompt))
                if not _sealed and os.environ.get("MATHBRAIN_COV_AGREE_WIDE", "2") not in ("", "0"):
                    _sealed = _same_card(emitted_ids[0], _COV_AGREED.get("block"))
            except Exception:
                _sealed = False
            if not _sealed:
                _silence_note(data, "coverage_agree_identity_lost")
                return
        # THE VOUCHED CARD IS SEALED TOO (29 Sep; scheduled audit account01/B08 auto11,
        # B08-R19-VOUCH-FINAL-IDENTITY-NOT-SEALED-079). The vouch route opens a prompt the gate refused because the
        # served first card is also the meaning channel's first claim, and the stages after that check (session dedup,
        # twin collapse, the block budget, which drops a card too long to fit and keeps going) can put another card
        # first. A vouched block goes out only if it still leads with the card the two channels agreed on.
        if block and emitted_ids and _VOUCH["route"]:
            if not (_VOUCH.get("card") and _same_card(emitted_ids[0], _VOUCH["card"])):
                _silence_note(data, "vouch_identity_lost")
                return
        if block and COH_CUT and len(emitted_ids) > 1:
            _cut = _incoherent_weak_block(prompt, emitted_ids)
            if _cut and COH_NAME_EXEMPT:
                _lt = next((h.node.title for h in (chosen or []) if str(h.node.id) == str(emitted_ids[0])), "")
                if _lead_title_names_prompt(prompt, _lt):
                    _cut = None
            if _cut:
                _silence_note(data, "incoherent_weak_block:lead%.3f_pair%.3f" % _cut)
                return
        if block and MEANQ_Z is not None and emitted_ids:
            _zm = _far_block_z(prompt, emitted_ids)
            if _zm:
                _silence_note(data, "far_block_z:z%.2f_meanq%.3f" % _zm)
                return
        if block and QUOTE_COV is not None and emitted_ids and not pinned_ids:
            _qc = _quote_uncovered(prompt, [h for h in chosen if h.node.id in set(emitted_ids)])
            if _qc is not None:
                _silence_note(data, "quote_uncovered:cov%.3f_w%d" % _qc)
                return
        if not block:
            _silence_note(data, "empty_block:%d_pieces" % len(pieces))
            return
        block = _finish_block(block, emitted_ids, {h.node.id: h for h in chosen})
        if not _emit(out, json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "UserPromptSubmit",
                        "additionalContext": block,
                    }
                }
        )):
            return                          # the hard ceiling passed while this body ran: nothing was served
        if emitted_ids and not SHADOW:
            _already_injected(sess_for_guard, emitted_ids)
            try:
                if os.environ.get("MATHBRAIN_USE_LOG", "1") != "0":   # evaluations set 0: test serves are not uses
                    _use_module().log_serve(sess_for_guard, emitted_ids)
                    if _VOUCH["route"]:
                        with io.open(os.path.join(os.path.expanduser("~"), ".claude", "brain_graph", "live",
                                                  "math_vouch_serves.jsonl"), "a", encoding="utf-8") as _vfh:
                            _vfh.write(json.dumps({"t": round(time.time(), 1), "sid": str(sess_for_guard or "")[:64],
                                                   "ids": list(emitted_ids)[:12]}, separators=(",", ":")) + "\n")
            except Exception:
                pass
        sess = data.get("session_id") or ""
        if emitted_ids and not SHADOW and re.match(r"^[0-9a-f]{8}-", sess):
            try:
                with io.open(
                    os.path.join(HOME, ".claude", "dreaming", "surfaced.tsv"),
                    "a",
                    encoding="utf-8",
                ) as handle:
                    handle.write(
                        "%.0f\t%s\t%s\tmathbrain\n"
                        % (time.time(), sess[:12], emitted_ids[0][:40])
                    )
            except Exception:
                pass
    except Exception as exc:
        # A swallowed exception here is the brain going silent with no trace. Record the class and reason so
        # silence under load is COUNTABLE (2026-09-12): mathbrain_silence.tsv beside surfaced.tsv.
        try:
            _silence_note(data, "%s:%s" % (type(exc).__name__, str(exc)[:80]))
        except Exception:
            pass
        if os.environ.get("MATHBRAIN_DEBUG"):
            raise
        return


def _silence_note(data, reason, harness_only=False):
    """One line per silent or degraded exit, so 'how often does the brain go mute under load' has an answer.

    Format: epoch  session-prefix  reason. Never raises; never blocks the prompt.

    HARNESS RUNS GO TO A SEPARATE FILE (2026-09-13). A real session carries a uuid session id, which is the test
    `surfaced.tsv` beside this one has always applied; this log had no such test, so every measurement run landed
    in it. Counted live that day: 256 rows, of which ONE was a real session - the other 255 were the campaign's
    own probes, mine among them, several hundred in an afternoon, and the serial load pushed the hook past its
    time budget so the log read as if the brain were constantly timing out in production. A log that answers
    "how often does this happen to real users" must not be written by the instrument measuring it. Harness rows
    are kept rather than dropped - they are how a failing stage gets debugged - but in their own file."""
    try:
        sess = str((data or {}).get("session_id") or "")
        real = bool(re.match(r"^[0-9a-f]{8}-", sess)) and not SHADOW
        if harness_only and real:
            return          # a stage that fires on ordinary traffic: diagnosable in a harness, silent in production
        name = "mathbrain_silence.tsv" if real else "mathbrain_silence_harness.tsv"
        # A FOURTH COLUMN: which QUESTION went quiet (2026-09-14). Without it this log gives the
        # population and not the attribution, and the difference is a wrong diagnosis: on 09-14 five
        # coverage-gate rows in one window led me to blame that gate for four silences it had nothing
        # to do with - measured directly, all four passed it comfortably and the two it really silenced
        # were absent-target questions it was right to silence. A harness can now map a row back to its
        # bank item, and a real session can be traced without this file ever holding what was typed.
        # What the fingerprint is and is not (29 Sep, scheduled audit account04/B10 r26, B10-R45): the
        # first 40 bits of an unkeyed SHA-1, so anyone holding a prompt can confirm it is in the file -
        # which is how the harness matches rows to its bank - and it is not anonymous. The file stays on
        # this machine and in no repository. At 2,373 distinct prompts (29 Sep) the chance that any two
        # share a fingerprint is about 3 in a million (n^2 / 2^41); at a million it would be about 1 in 3.
        _fp = ""
        try:
            _p = str((data or {}).get("prompt") or "")
            if _p:
                _fp = hashlib.sha1(_p.encode("utf-8", "replace")).hexdigest()[:10]
        except Exception:
            _fp = ""
        with io.open(os.path.join(HOME, ".claude", "dreaming", name), "a", encoding="utf-8") as h:
            h.write("%.0f\t%s\t%s\t%s\n" % (time.time(), sess[:12], reason, _fp))
    except Exception:
        pass


def cli_query(query):
    """Runtime-agnostic recall: print the plain injection block for a query.

    Usage from ANY agent runtime (codex, scripts, humans):
        python math_sense.py --query "your mathematical question"
    Prints nothing when the store abstains. Bypasses the two-anchor prompt
    gate (an explicit query IS intent) but keeps the abstention thresholds.
    """
    try:
        sys.path.insert(0, BUILD_SRC)
        from math_brain.retrieval import retrieve
        from math_brain.store import ResearchStore

        global _render_parts
        try:
            from math_brain.rendering import render_parts as _render_parts
        except Exception:
            pass

        if not os.path.exists(STORE_PATH):
            return
        try:
            from math_brain.retrieval import load_signal_cache
            load_signal_cache(os.path.join(os.path.dirname(STORE_PATH), "brain_signal_cache.pkl"))
        except Exception:
            pass
        with ResearchStore.open_read_only(STORE_PATH) as store:
            # Keep node and revision-edge reads in one short snapshot; the review reads come after the selection.
            store.connection.execute("BEGIN")
            nodes = list(store.iter_nodes())
            edges = list(store.iter_edges())
        _load_expansion_once()
        # the house acronyms travel with the query here too (the hook path has expanded them since 2026-09-23): an agent
        # asking "FEI for read-once formulas" deliberately reaches the cards that spell the program out
        result = retrieve(
            _expand_aliases(query), nodes=nodes, edges=edges, limit=CANDIDATE_POOL,
            min_support_score=0.0, min_margin=0.0,
        )
        if not result.hits:
            return
        from math_brain.selection_integrity import consistent_hits
        _rkw = _refusal_kwargs()
        if _rkw is None:
            return                       # a package whose refusal form is unknown serves nothing here either
        chosen = consistent_hits(_select(result.hits), STORE_PATH,
                                 deadline=time.monotonic() + TIME_BUDGET_S, **_rkw)
        if not chosen:
            return
        chosen = _drop_title_twins(_drop_later_twins(chosen))
        twin_line = {}
        if SAME_RESULT:
            try:
                ids = [h.node.id for h in chosen]
                for cid, found in _pitfalls_for(ids).items():
                    for lab, pid, title in found:
                        if lab == "same result on record" and pid not in ids and cid not in twin_line:
                            twin_line[cid] = "  same result on record: %s (%s)" % (title, pid)
            except Exception:
                twin_line = {}
        print("Full current note: python ~/.claude/dreaming/math_note.py SOURCE_ID")
        reads = _review_reads([h.node.id for h in chosen])
        parts = _claim_parts([h.node.id for h in chosen])
        for hit in chosen:
            node = _claim_view(hit.node, parts.get(hit.node.id))
            meta = _node_meta(node)
            kind = str(meta.get("claim_kind") or node.kind)
            label, text = _render_parts(node, query=query, max_text_chars=MAX_STATEMENT_CHARS)
            print("[math-brain] %s [%s]: %s" % (label, kind, text))
            print("  (source-id: %s)" % node.id)
            hint = _origin_prefix(node, meta) + _trust_tag(meta, reads.get(node.id))
            if _structure_flattened(text):
                hint = "notation flattened (fractions/exponents lost in the harvest); " + hint
            if _source_is_excerpt(node, text):
                hint = "SOURCE EXCERPT: read the full source and assumptions before applying; " + hint
            print("  (hint: %s)" % hint)
            proof = str(meta.get("proof_text") or "").strip()
            if proof:
                print("  proof sketch: %s" % (proof[:400] + ("..." if len(proof) > 400 else "")))
            if node.id in twin_line:
                print(twin_line[node.id])
    except Exception:
        return


if __name__ == "__main__":
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # help is inert: no stdin read, no store open, no state file touched
        print("usage: math_sense.py                (hook: reads the UserPromptSubmit JSON on stdin)\n"
              "       math_sense.py --query TEXT   (print the note block for TEXT, or nothing)")
        raise SystemExit(0)
    if len(sys.argv) >= 3 and sys.argv[1] == "--query":
        cli_query(" ".join(sys.argv[2:]))
        raise SystemExit(0)
    try:
        _dir = os.path.dirname(os.path.abspath(__file__))
        sys.path.insert(0, _dir)
        import brain_switch as _bsw

        _bsw.gate()
    except SystemExit:
        raise
    except Exception:
        pass
    _rc = main()
    # A hook run as its own process (Codex runs it so) that gave up at the hard ceiling leaves its body on a daemon thread,
    # possibly inside numpy or onnxruntime; ending the process here, with nothing written, keeps interpreter shutdown from
    # waiting on or tearing down that thread. Never in the dispatcher's process, where stdout is the dispatcher's capture
    # (io.StringIO) and os._exit would end every other hook of the prompt: only when this file is the process's own script
    # and stdout is the process's own.
    _own = False
    try:
        _own = (sys.stdout is sys.__stdout__
                and os.path.abspath(sys.argv[0] or "") == os.path.abspath(__file__))
    except Exception:
        _own = False
    if _rc == "abandoned" and _own:
        try:
            sys.stdout.flush()
        except Exception:
            pass
        os._exit(0)
