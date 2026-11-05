#!/usr/bin/env python3
"""proof_sense.py - the proof-audit instinct lane: the gut that fires on a mathematical argument.

Registered on UserPromptSubmit. When the prompt is a mathematical argument (math-dense text with numbered or
sentence steps: a proof under audit, a lemma being checked, a derivation), it injects the audit posture and up
to three CLASS CHECKS chosen by what the argument contains - named results (hypotheses, direction, rate),
inequality chains, paired operators (sup/inf, limsup/liminf, union/intersection, forall/exists), index and
constant arithmetic, load-bearing hypothesis words, limit targets - plus the machine-check route (numerics,
then Lean against the local Mathlib). It is general: written from the classes of one-token error a proof
can carry, never from any test item, so it applies to any argument in any paper.

Why a separate lane (2026-09-07): the general gut caps at two fires ranked by scar charge, so on a proof the
trading scars and the exact-clauses reflex won the slots and told the solver to copy steps verbatim; measured
on the probability bank the brain arm then called flawed proofs valid MORE often than a sterile solver. A
proof needs the opposite posture, and this lane gives it without competing with the scars.

Fail-safe: any error -> exit 0, no output. Silent on non-mathematical prompts. Once per session per 10 min.
Reads stdin (hook JSON), writes injection JSON to stdout. Pure ASCII.
    python proof_sense.py --selftest
"""
import io, json, os, re, sys, time

HOME = os.path.expanduser("~")
STATE = os.path.join(HOME, ".claude", "dreaming", "proof_sense_state.json")
REFIRE_S = 600
MAX_CHECKS = 3

MATH_TOKEN = re.compile(r"\$[^$]{1,400}\$|\\\[|\\begin\{|\\(?:frac|sum|int|mathbb|mathcal|leq|geq|le|ge|infty|lim|sup|inf|alpha|beta|lambda|sigma|varepsilon|epsilon|mu|nu|rho|theta|omega)\b")
STEP_LINE = re.compile(r"(?m)^\s*(?:\[step\s+)?\d{1,3}[.)\]]\s+\S")  # v7: three-digit step ids (a step 100 was silently invisible)
PROOF_WORD = re.compile(r"\b(proof|lemma|theorem|proposition|corollary|argument|derivation|inequality|almost surely|martingale|measure)\b", re.I)

CHECKS = [
    ("named_result_hypotheses_direction_rate",
     re.compile(r"\b(Chernoff|Hoeffding|Chebyshev|Markov'?s?\s+inequality|Jensen|Cauchy[- ]Schwarz|H[o\u00f6]lder|Doob|Azuma|McDiarmid|Borel[- ]Cantelli|BK\s+inequality|van den Berg|Harris|FKG|Fatou|dominated convergence|monotone convergence|Fubini|Tonelli|Portmanteau|Slutsky|Girsanov|It[o\u00f4]'?s?\s+(formula|lemma)|Kolmogorov|Pinsker|Gr[o\u00f6]nwall|Talagrand|Bernstein|Stein'?s?\s+(method|lemma|identity)|Lindeberg|Lyapunov|Cram[e\u00e9]r|optional stopping|Arzel[a\u00e0]|Prokhorov|Skorokhod|Burkholder|Minkowski|Young'?s?\s+inequality|Poincar[e\u00e9]|log-Sobolev|Lebesgue|Radon[- ]Nikodym|Kantorovich|Strassen|Le Cam|Poissonization|reflection principle|union bound)\b", re.I),
     "A named result is a claim about HYPOTHESES, a DIRECTION and a RATE. For each named theorem or inequality in the argument, say what it needs (nonnegativity, independence, a dominator, C^2, uniform integrability, p > 1, a finite exponential moment), which side it bounds, and the rate it gives (exponential against polynomial). The commonest wrong step names a neighbour with the wrong hypothesis or direction: Chebyshev where the rate is Chernoff's, Harris for an upper bound that is BK's, Markov for a norm-of-integral bound that is Jensen's, dominated convergence with no dominator where the sequence is monotone."),
    ("inequality_chain_direction",
     re.compile(r"\\(?:leq|geq|le|ge|lesssim|gtrsim|ll|gg)\b|<=|>=|\u2264|\u2265|\\neq|\\lneq"),
     "In every chain, check each relation symbol against the operation that produced it: a decreasing map, a negative factor, a reciprocal, or a complement REVERSES it; an '=' inside a chain of '<=' is a separate claim (a triangle split, a subinterval integral, an upper bound are not equalities); a bound is only as strong as its weakest link, so find the one link you cannot re-derive."),
    ("paired_operator_swap",
     re.compile(r"\\(?:limsup|liminf|sup|inf|max|min|cup|cap|bigcup|bigcap|subset|supset|subseteq|supseteq|forall|exists)\b|\b(limsup|liminf|supremum|infimum|union|intersection|for all|there exists)\b", re.I),
     "sup/inf, limsup/liminf, max/min, union/intersection, subset/superset, for-all/there-exists come in PAIRS; preserve each one's direction and scope. For each occurrence ask which member the inequality direction and the later use require: an upper bound on a sup needs uniformity, a lower bound on a sup needs one point or arbitrarily close approximations (a sup need not be attained); limsup a_n <= B means a_n <= B + eps eventually and does not need convergence; replacing a limsup by a liminf on the LARGER side of an inequality strengthens it and needs its own justification (convergence suffices but is not necessary); a 'for all' cannot be concluded from a 'there exists'; a function in C^2 union C is not in C^2 intersect C."),
    ("index_and_constant_arithmetic",
     re.compile(r"\b[nkmijlNKM]\s*[+-]\s*1\b|\^\{?\s*-?\d|\\frac\{\s*\d|\b\d+\s*/\s*\d+\b|\b(?:2|3|4)\s*\\?(?:pi|cdot)|\\sqrt", re.I),
     "Re-derive every n+1 / n-1 / k-1 from the definition that produced it ('largest m such that' forces the next one to fail, 'smallest k such that' forces the previous one to fail; a sum from 0 to k-1 has k terms), and carry every constant and exponent both to the line before and to the claim (an interval in a case split must cover the statement's; sizes that add to a total must be consistent with it; an integrability exponent cannot rise for free; a larger threshold proves a weaker statement). When a step IS wrong the fault is often a single digit or sign, but do not assume a fault exists: re-derive, then judge."),
    ("load_bearing_hypothesis_words",
     re.compile(r"\b(independent|independence|bounded|uniformly|continuous|continuity|compact|closed|finite|integrable|measurable|Lipschitz|monotone|increasing|decreasing|convex|concave|nonnegative|non-negative|positive|centered|centred|i\.i\.d\.|iid|martingale|supermartingale|exchangeable|stationary|ergodic|absolutely continuous)\b", re.I),
     "Each hypothesis word is either load-bearing or decoration; decide which, per step. For every step that uses one, ask what breaks if it were the weaker neighbour (measurable for continuous, closed for compact, bounded for uniformly bounded, uncorrelated for independent, C^1 for C^2) or were missing, and whether the claim or an EARLIER step already granted it (then the drop changes nothing). 'Independent' is what lets probabilities multiply; 'bounded' or 'dominated' is what lets a limit pass an expectation; 'finite' is what makes a sum finite; 'uniformly' is the gap between pointwise and uniform."),
    ("limit_target",
     re.compile(r"\\(?:to|rightarrow|longrightarrow|xrightarrow)\s*\{?\s*(?:0|\\infty|infty|\+\\infty|-\\infty)|\\lim(?:sup|inf)?_\{[^}]{1,30}\\to|\bas\s+[a-zA-Z]\s*(?:\\to|->|tends to|goes to)\s*(?:0|\\infty|infinity)", re.I),
     "Check every limit's TARGET against the regime of the statement and of the lines around it: an integer index runs to infinity, never to zero; t -> 0 reads off an initial value while a long-run moment needs t -> infinity; a subsequence must have indices going to infinity; 'sending n -> 0' next to 'lim_{n -> infinity}' is a contradiction. One wrong endpoint invalidates the step even when the displayed formula is right."),
]

POSTURE = (
    '[proof-sense] this is an argument under AUDIT, not an instruction list: do not copy its steps as clauses, '
    'test them. The argument may be correct, incomplete, or wrong in one or several steps: assume no error count '
    'and no mutation format. Be suspicious by default, but say CLEAN when every step re-derives; otherwise name '
    'the FIRST step you cannot re-derive from the claim and the steps before it, with the missing premise, the '
    'contradiction or the counterexample (an example refutes a universal claim only if it meets ALL its '
    'hypotheses; passing sampled cases do not establish untested cases, while exhaustive certified verification '
    'can establish every case in a rigorously finite domain). Distinguish an invalid inference from an unfamiliar'
    ' but valid one: a step is not wrong because it skips the theorem you expected. Use bounded, windowless '
    'machine checks only after fresh compute admission. Compiler acceptance alone is not a proof certificate. '
    'Require the exact named theorem and declaration kind, transitive dependencies limited to propext, '
    'Classical.choice and Quot.sound (reject sorryAx and other axioms), and evidence bound to exact source bytes '
    'and a trusted compiler/dependency environment. A review hash records the caller assertion; it does not '
    'independently establish review, and executable source/imports can spoof printed messages. A certificate '
    'concerns the formal target; separately check fidelity to the intended mathematics. If the checker or runtime'
    ' cannot establish these conditions, the formal target remains UNVERIFIED. An unsolved goal, syntax error, '
    'missing import, timeout or resource hold leaves the intended claim UNRESOLVED; a failed tactic is not a '
    'counterexample. A step is refuted by a concrete counterexample or by proving its negation, never by a proof '
    'attempt that did not close. A note from the math brain beside this is a claim to CHECK the step against for '
    'applicability, never evidence the step is right or wrong. (lane v8: certificate-aware guidance)'
)


# A numbered mathematical argument can be written without LaTeX. This path
# requires a formal setup, at least three distinct technical concepts, and
# at least three numbered steps. Generic words such as bounded, normal,
# compact, measure, and proof do not count as technical concepts by themselves.
# Administrative task lists stay out even when they name mathematical topics.
PROSE_MATH_CUES = tuple((name, re.compile(pattern, re.I)) for name, pattern in (
    ("linear_space", r"\b(?:Hilbert|Banach|normed vector|inner product)\s+spaces?\b"),
    ("linear_operator", r"\b(?:bounded\s+|compact\s+)?linear\s+operators?\b|\boperator\s+norms?\b"),
    ("norm_geometry", r"\b(?:unit\s+balls?|(?:finite\s+)?(?:epsilon|delta)[- ]nets?|norm\s+convergence)\b"),
    ("measure_space", r"\bmeasure\s+spaces?\b|\bsigma[- ]algebras?\b"),
    ("integrability", r"\b(?:nonnegative\s+|real[- ]valued\s+)?integrable\s+functions?\b|\bLebesgue\s+integrals?\b"),
    ("convergence", r"\b(?:dominated|monotone|pointwise|uniform)\s+convergence\b|\balmost\s+(?:everywhere|surely)\b"),
    ("probability_measure", r"\b(?:Borel\s+)?probability\s+measures?\b|\bBorel\s+measures?\b"),
    ("continuous_function", r"\b(?:bounded\s+)?continuous\s+(?:real\s+|test\s+)?functions?\b"),
    ("polynomial", r"\bpolynomials?\b"),
    ("compactness", r"\bcompact\s+(?:intervals?|sets?|spaces?|closures?)\b"),
    ("moments", r"\b(?:finite\s+moments?|moment\s+sequences?|moments?\s+of\s+every)\b"),
    ("matrix", r"\bmatri(?:x|ces)\b"),
    ("determinant", r"\bdeterminants?\b"),
    ("eigenstructure", r"\beigen(?:values?|vectors?)\b"),
    ("group", r"\b(?:finite|abelian|cyclic)\s+groups?\b"),
    ("homomorphism", r"\b(?:group\s+)?homomorphisms?\b"),
    ("coset", r"\bcosets?\b"),
))
PROSE_ARGUMENT_INTRO = re.compile(
    r"\b(?:claim|proof|argument|theorem|lemma|proposition|corollary)\s*:"
    r"|\b(?:let|suppose|assume)\s+(?:[A-Za-z](?:_[A-Za-z0-9]+)?|mu|nu)\s+(?:be|and|denote|have|satisfy)\b",
    re.I,
)
PROSE_TASK_LINE = re.compile(
    r"(?m)^\s*(?:\[step\s+)?\d{1,3}[.)\]]\s+"
    r"(?:(?:please|first|next|then|finally)[,:]?\s+)*"
    r"(?:(?:could|would|can)\s+you\s+(?:please\s+)?)?"
    r"(?:(?:do\s+not|don't)\s+)?"
    r"(?:read|open|save|edit|update|commit|push|run|upload|download|install|delete|move|rename|send|schedule|start|stop)\b",
    re.I,
)



# Gate-only extension: inspect numbered reasoning locally, rather than counting
# mathematical topic names anywhere in an administrative request. These are
# recognition cues, not correctness tests. The existing LaTeX branches precede
# this extension unchanged, including their 200-character floor.
ASCII_PROSE_DOMAIN = re.compile(
    r"\b(?:prime numbers?|primes|divisors?|integers?|remainders?|divisible|modulo|"
    r"vertices|vertex|undirected graphs?|odd[- ]degree|even[- ]degree|"
    r"probability spaces?|probability measures?|random variables?|"
    r"taking expectations|linearity of expectation|expectations? of|"
    r"indicators? of (?:the )?union|"
    r"polynomials?|coefficients?|finite fields?|over a field|"
    r"subsets?|bijections?|injective|surjective|rational numbers?|irrational|"
    r"triangles?|interior angles?|derivative of|differentiable|integrals?|"
    r"convergent sequences?|real sequences?|real numbers?|nonnegative|"
    r"operator norms?|vector norms?|normed spaces?|eigenvalues?|matrices|supremum|infimum|subspaces?|vector spaces?|vectors?|linear combinations?)\b", re.I)
ASCII_PROSE_INFERENCE = re.compile(
    r"\b(?:definition|monotonicity|since|because|therefore|hence|thus|consequently|implies?|"
    r"contradiction|induction|it follows|this proves|yields?|gives?|"
    r"preserves?|identifies?|by definition|by linearity|dividing|"
    r"multiplying|subtracting|adding|taking expectations|so)\b", re.I)
ASCII_PROSE_RELATION = re.compile(
    r"(?<![A-Za-z0-9_])(?:[A-Za-z](?:_[A-Za-z0-9]+|[0-9]+)?|[0-9]+)"
    r"(?:\([^()\n]{1,30}\))?\s*(?:<=|>=|!=|=(?!=)|[<>])\s*(?:-?[0-9]|[A-Za-z])")
ASCII_PROSE_DOCUMENT = re.compile(
    r"\b(?:files?|folders?|repositories|repository|chapters?|documents?|"
    r"slides?|filenames?|editors?|typograph\w*|deploy\w*|meetings?|"
    r"deadlines?|bibliograph\w*|uploads?|downloads?|schedules?|"
    r"illustrations?|formatting|spelling|workshops?|handouts?|editorial|lecturers?|room bookings?|refreshments|travel arrangements?|timetables?|agendas?|session (?:one|two|three|four|[0-9]+))\b", re.I)


def _prose_argument_bodies(text):
    """Exclude document/agenda entries from both prose admission paths."""
    marks = list(STEP_LINE.finditer(text))
    bodies = [text[m.start(): marks[i + 1].start() if i + 1 < len(marks) else len(text)]
              for i, m in enumerate(marks)]
    return [body for body in bodies if not PROSE_TASK_LINE.search(body)
            and not ASCII_PROSE_DOCUMENT.search(body)]


def _ascii_numbered_reasoning(text):
    # Two explicit administrative steps retain the existing conservative veto.
    if len(PROSE_TASK_LINE.findall(text)) >= 2:
        return False
    eligible = _prose_argument_bodies(text)
    if len(eligible) < 3:
        return False
    math_rows = sum(bool(ASCII_PROSE_DOMAIN.search(body)
                        or ASCII_PROSE_RELATION.search(body)) for body in eligible)
    inference_rows = sum(bool(ASCII_PROSE_INFERENCE.search(body)) for body in eligible)
    symbolic_rows = sum(bool(ASCII_PROSE_RELATION.search(body)) for body in eligible)
    return (math_rows >= 2 and inference_rows >= 2
            and (any(ASCII_PROSE_DOMAIN.search(body) for body in eligible)
                 or symbolic_rows >= 2))


# 2026-09-11: MATH_TOKEN counts LaTeX; a numbered argument written in plain ASCII mathematics (x^2, f(z), |arg z| <
# pi/4, E[X], sum_{n}) counted ZERO tokens and the lane stayed silent on it. Measured on the GPT-6 bank-100 rerun:
# the lane spoke on 50 of 100 items - exactly the 50 with LaTeX - and every one of the five items the brain's
# material flipped from wrong to right carried this block; the 41 items with math notes alone moved 37 -> 36. So the
# posture is the half that measurably helps, and half the arguments never got it. ASCII notation now counts too
# (>= 8 pieces, in a numbered argument of >= 3 steps that is not a task list): bank coverage 50 -> 89 of 100, the
# 100 mdlit prompts 99 -> 99, and on 25,860 archived prompts of 200+ chars it adds 375 fires (+1.45 pp), the sample
# read by hand all mathematical (past bank-serving prompts "You are auditing a numbered mathematical argument",
# paper-polish and math-audit briefs). Refire is still once per session per 10 min, so the cost is bounded.
ASCII_MATH_TOKEN = re.compile(
    r"\b[A-Za-z]\^\{?[-0-9nkpqdm]\}?|\b[A-Za-z]_\{?[0-9nkijmpt]\}?\b|\b[A-Za-z]\([A-Za-z0-9 ,+\-]{1,12}\)|"
    r"\|[^|\n]{1,30}\||<=|>=|\bE\[|\bP\(|\bsum_|\bint_|\blim_|\bsup_|\binf_|\bsqrt\(|\bexp\(|\blog\(|"
    r"\b(?:pi|infty|infinity|epsilon|eps|delta|lambda|sigma|omega|theta|alpha|beta|gamma)\b|->|\bmod\b")
ASCII_MATH_MIN = 8


def _ascii_math_argument(text):
    steps = len(STEP_LINE.findall(text))
    if steps < 3 or len(PROSE_TASK_LINE.findall(text)) >= 2:
        return False
    if len(_prose_argument_bodies(text)) < 3:
        return False
    return len(ASCII_MATH_TOKEN.findall(text)) >= ASCII_MATH_MIN


_FLEET_WORDS = re.compile(
    r"\b(fleet|box(?:es)? ?[0-9]|lane|queue|simulation|backtest|deploy|ssh|systemd|github|push|commit|render|movie)\b", re.I)


def _prose_named_objects_argument(text):
    """A numbered prose argument naming two distinct strong mathematical objects (the math gate's list), with no
    fleet vocabulary and no task lines. Measured 2026-09-11 (see is_math_argument)."""
    if _FLEET_WORDS.search(text) or len(PROSE_TASK_LINE.findall(text)) >= 2:
        return False
    if len(_prose_argument_bodies(text)) < 3:
        return False
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        if here not in sys.path:
            sys.path.insert(0, here)
        import math_sense as _ms
        rxs = (_ms.STRONG_ANCHORS, _ms.STRONG_ANCHORS_2)
    except Exception:
        return False
    return len({mt.group(0).lower() for rx in rxs for mt in rx.finditer(text)}) >= 2


def is_math_argument(text):
    if len(text) < 200:
        return False
    m = len(MATH_TOKEN.findall(text))
    steps = len(STEP_LINE.findall(text))
    words = len(PROOF_WORD.findall(text))
    if m >= 6 and steps >= 3:
        return True
    if m == 0 and steps >= 3 and _ascii_math_argument(text):
        return True
    # 2026-09-07: 35 of the 12,193 bank arguments are written mostly in words (4-5 math tokens, 3-6 numbered steps,
    # 4+ proof words) and the old gate stayed silent on them - under a fail-closed serve that is an infinite re-serve,
    # not a quiet lane. A numbered argument with some math and the vocabulary of a proof is an argument.
    if m >= 3 and steps >= 3 and words >= 2:
        return True
    if m >= 14 and words >= 2:
        return True
    # 2026-09-11 evening: eleven bank-100 arguments written in words (topology, algebra: 0-7 ASCII math tokens, no
    # proof words) stayed silent. A numbered prose argument that names two distinct objects from the math gate's
    # strong list, with no fleet/ops vocabulary, is an argument: +4 of the 11 on the bank, and on 25,855 archived long
    # prompts it opens 26 more, every sampled one a proof audit or a manuscript review. The wider form (any anchor plus
    # one proof word) opened 479, mostly numbered fleet orders - which is why the fleet words are excluded here.
    if steps >= 3 and _prose_named_objects_argument(text):
        return True
    if steps < 3 or not PROSE_ARGUMENT_INTRO.search(text):
        return False
    if len(PROSE_TASK_LINE.findall(text)) >= 2:
        return False
    if len(_prose_argument_bodies(text)) < 3:
        return False
    if sum(bool(rx.search(text)) for _, rx in PROSE_MATH_CUES) >= 3:
        return True
    return _ascii_numbered_reasoning(text)


WEIGHT = {"named_result_hypotheses_direction_rate": 6.0, "limit_target": 2.0, "paired_operator_swap": 1.5}


def pick_checks(text):
    """The checks whose cues the argument contains, most specific first: a named result is the sharpest cue
    (one token names the hypothesis set), a limit target next; inequality symbols and hypothesis words are in
    every proof, so they rank by count only."""
    scored = []
    for cid, rx, gut in CHECKS:
        n = len(rx.findall(text))
        if n:
            scored.append((n * WEIGHT.get(cid, 1.0), n, cid, gut))
    scored.sort(key=lambda x: -x[0])
    return [(n, cid, gut) for _, n, cid, gut in scored[:MAX_CHECKS]]


def _recently_fired(session):
    try:
        now = time.time()
        state = {}
        if os.path.exists(STATE):
            with io.open(STATE, encoding="utf-8") as f:
                state = json.load(f)
        if not isinstance(state, dict):
            state = {}
        state = {s: t for s, t in state.items() if now - float(t) < 6 * 3600}
        last = state.get(session)
        if last and now - float(last) < REFIRE_S:
            return True
        state[session] = now
        tmp = "%s.tmp.%d" % (STATE, os.getpid())
        with io.open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f)
        try:
            os.replace(tmp, STATE)
        except OSError:
            try:
                os.remove(tmp)
            except OSError:
                pass
        return False
    except Exception:
        return False


KNOWLEDGE = os.path.join(HOME, ".claude", "dreaming", "proof_audit_knowledge.json")
# a named result in the argument -> the knowledge node that states its hypotheses, direction and rate
NAMED = [
    (re.compile('\\b(Chernoff|Hoeffding|Chebyshev)\\b', re.I), 'Chernoff bound vs Chebyshev bound'),
    (re.compile("\\bMarkov'?s?\\s+inequality\\b", re.I), "Markov's inequality"),
    (re.compile('\\b(strong\\s+)?Markov\\s+property\\b|\\bstrong\\s+Markov\\b', re.I), 'Markov property vs strong Markov'),
    (re.compile('\\bJensen\\b', re.I), "Jensen's inequality"),
    (re.compile("\\b(Cauchy[-\\s\\u2010-\\u2015]+Schwar[tz]z?|H[o\\u00f6]lder(?!(?:['\\u2019]s)?\\s*(?:continu|regular|semi|space|norm|exponent)))\\b", re.I), 'Cauchy-Schwarz and Holder'),
    (re.compile("\\bYoung'?s?\\s+inequality\\b", re.I), "Young's inequality"),
    (re.compile("\\b(BK\\s+inequality|van den Berg|Harris(?!(?:['\\u2019]s)?\\s+(?:recurren|chain|ergodic|theorem))|FKG|disjoint(?:ly)? occur)", re.I), 'FKG / Harris inequality vs BK'),
    (re.compile("\\bunion bound\\b|\\bBoole'?s?\\b|\\bBonferroni\\b", re.I), 'Union bound'),
    (re.compile('\\bBorel[- ]Cantelli\\b', re.I), 'Borel-Cantelli'),
    (re.compile('\\b(Vitali convergence|uniformly integrable|uniform integrability|u\\.i\\.|equi-?integrab)', re.I), 'Vitali convergence theorem: uniform integrability plus tightness replaces a dominating function'),
    (re.compile('\\bmonotone convergence\\b', re.I), 'Monotone convergence theorem'),
    (re.compile('\\bFatou\\b', re.I), "Fatou's lemma"),
    (re.compile('\\b(Fubini|Tonelli)\\b', re.I), 'Fubini vs Tonelli'),
    (re.compile('\\buniform(ly)? integrab', re.I), 'Uniform integrability'),
    (re.compile('\\boptional (stopping|sampling)\\b', re.I), 'Optional stopping theorem'),
    (re.compile('\\bmartingale convergence\\b|\\bsupermartingale converge', re.I), 'Martingale convergence theorems'),
    (re.compile("\\bDoob'?s?\\s+(maximal|inequalit|L\\^?p)", re.I), "Doob's inequalities"),
    (re.compile('\\bVille\\b', re.I), "Ville's inequality"),
    (re.compile('\\b(Azuma|McDiarmid|bounded differences)\\b', re.I), 'Azuma-Hoeffding and McDiarmid'),
    (re.compile("\\bIt[o\\u00f4]'?s?\\s+(formula|lemma|isometry)\\b|\\bIt[o\\u00f4] formula\\b", re.I), "Ito's formula"),
    (re.compile('\\b(Girsanov|Novikov)\\b', re.I), "Girsanov's theorem and Novikov"),
    (re.compile('\\b(Burkholder|BDG)\\b', re.I), 'Burkholder-Davis-Gundy'),
    (re.compile("\\bDynkin'?s?\\s+formula\\b", re.I), "Dynkin's formula"),
    (re.compile("(\\bpi-lambda\\b|\\\\pi\\$?[- ]\\$?\\\\lambda|\\bmonotone class\\b|\\bDynkin'?s?\\s+(?:theorem|lemma|system)\\b)", re.I), "Monotone class and Dynkin's pi-lambda"),
    (re.compile('\\b(Portmanteau|weak(ly)? converge)', re.I), 'Portmanteau theorem'),
    (re.compile('\\bSlutsky\\b', re.I), "Slutsky's theorem"),
    (re.compile('\\bProkhorov\\b|\\btightness\\b', re.I), "Prokhorov's theorem"),
    (re.compile('\\bSkorokhod\\b', re.I), 'Skorokhod representation'),
    (re.compile("\\b(L[e\\u00e9]vy(?:['\\u2019]s)?\\s+continuity|continuity theorem|Cram[e\\u00e9]r[-\\s\\u2010-\\u2015]+Wold|characteristic functions?(?!\\s+of\\s+(?:(?:the|a|an|measurable|Borel|open|closed)\\s+)*(?:sets?|events?|indicators?)\\b))\\b", re.I), "Levy's continuity theorem"),
    (re.compile('\\\\limsup|\\\\liminf|\\blimsup\\b|\\bliminf\\b', re.I), 'limsup and liminf'),
    (re.compile("\\b(Stein'?s?\\s+(method|kernel|identity|equation)|Stein kernel)\\b", re.I), "Stein's method"),
    (re.compile('\\bBerry[- ]Esseen\\b', re.I), 'Berry-Esseen theorem'),
    (re.compile("\\b(Lindeberg|Lyapunov'?s?\\s+condition|central limit)\\b", re.I), 'Lyapunov and Lindeberg conditions'),
    (re.compile('\\bDonsker\\b|\\binvariance principle\\b', re.I), "Donsker's invariance principle"),
    (re.compile('\\b(Cram[e\\u00e9]r(?![-\\s\\u2010-\\u2015]+(?:Rao|Wold))|large deviation|rate function|Varadhan)\\b', re.I), "Cramer's theorem and large deviations"),
    (re.compile('\\bSanov\\b|\\brelative entropy\\b', re.I), "Sanov's theorem"),
    (re.compile("\\b(three-series|Kolmogorov'?s?\\s+(inequality|criterion|continuity)|Kolmogorov[- ]Chentsov|Etemadi)\\b", re.I), "Kolmogorov's inequalities and criteria"),
    (re.compile('\\b(total variation|Pinsker|Hellinger)\\b', re.I), "Pinsker's inequality"),
    (re.compile('\\b(Wasserstein|Kantorovich|Rubinstein|optimal transport|transport(ation)? (distance|cost|plan))\\b', re.I), 'Kantorovich-Rubinstein duality'),
    (re.compile('\\b(heat kernel|Ricci|Li[- ]Yau)\\b', re.I), 'Gradient and heat-kernel estimates'),
    (re.compile('\\b(Arzel[a\\u00e0]|equicontinu|Banach fixed|contraction mapping|Hahn[- ]Banach)', re.I), 'Arzela-Ascoli, Banach fixed point'),
    (re.compile('\\b(Poincar[e\\u00e9]|log-Sobolev|hypercontractiv|Sobolev|Bakry)', re.I), 'Sobolev, Poincare and log-Sobolev'),
    (re.compile('\\b(stochastic(ally)? dominat|convex order|Strassen)\\b', re.I), 'Convex ordering and stochastic domination'),
    (re.compile('\\b(Le Cam|Poissoniz|Poisson approximation)\\b', re.I), 'Poisson approximation and Le Cam'),
    (re.compile('\\bexchangeab|\\bde Finetti\\b|\\bHewitt[- ]Savage\\b|\\bzero[- ]one law\\b|\\b0-1 law\\b|\\btail (event|sigma)', re.I), 'Hewitt-Savage and Kolmogorov zero-one laws'),
    (re.compile('\\bStirling\\b', re.I), "Stirling's formula"),
    (re.compile("\\bTaylor('?s)?\\s+(theorem|expansion|formula)\\b|\\bTaylor expan", re.I), "Taylor's theorem"),
    (re.compile('\\bMinkowski\\b', re.I), "Minkowski's inequality"),
    (re.compile('\\bGr[o\\u00f6]nwall\\b', re.I), "Gronwall's inequality"),
    (re.compile('\\bLeibniz\\b|\\bdifferentiat\\w+ under the integral\\b', re.I), 'Leibniz integral rule'),
    (re.compile("\\bBernstein'?s?\\s+inequality\\b", re.I), "Bernstein's inequality"),
    (re.compile('\\bRiesz\\b', re.I), 'Riesz representation'),
    (re.compile('\\b(Karamata|Potter|regularly varying|slowly varying|regular variation)\\b', re.I), "Karamata's theorem and Potter's bounds"),
    (re.compile('\\bPaley[- ]Zygmund\\b|\\bsecond[- ]moment method\\b', re.I), 'Paley-Zygmund inequality'),
    (re.compile("\\bLaplace('?s)?\\s+method\\b|\\bsaddle[- ]point\\b", re.I), "Laplace's method"),
    (re.compile('\\bDini\\b', re.I), "Dini's theorem"),
    (re.compile('\\bDuhamel\\b|\\bvariation of constants\\b|\\bmild solution\\b', re.I), "Duhamel's formula"),
    (re.compile("\\b(Russo(?:['\\u2019]s)?\\s+formula|Margulis[-\\s\\u2010-\\u2015]+Russo|pivotal (?:edge|site|coordinate|bond)|is pivotal|pivotality of|total influence)\\b", re.I), "Russo's formula"),
    (re.compile("\\bWald'?s?\\s+(identit|equation|lemma)", re.I), "Wald's identities"),
    (re.compile('\\bRadon[- ]Nikodym\\b|\\bLebesgue decomposition\\b', re.I), 'Radon-Nikodym theorem'),
    (re.compile('\\b(Campbell|Mecke|Slivnyak|Palm\\s+(?:measure|calculus|distribution|version|expectation|theory))\\b', re.I), 'Campbell, Mecke and Slivnyak'),
    (re.compile('\\b(Kingman|subadditive|Fekete|superadditive)\\b', re.I), "Kingman's subadditive ergodic theorem"),
    (re.compile('\\bBirkhoff\\b|\\bergodic theorem\\b', re.I), "Birkhoff's ergodic theorem"),
    (re.compile('\\b(Abel|Tauberian|Hardy[- ]Littlewood(?!\\s+maximal)|summation by parts)\\b', re.I), 'Abel and Dirichlet tests'),
    (re.compile('\\bPeierls\\b|\\bcontour argument\\b', re.I), 'Peierls argument'),
    (re.compile('\\b(Egorov|Lusin|Scheff[e\\u00e9])\\b', re.I), "Egorov's, Lusin's and Scheffe's theorems"),
    (re.compile('\\b(Perron[- ]Frobenius|Feller property|Feller process|irreducib|aperiodic)', re.I), 'Feller property, Perron-Frobenius'),
    (re.compile('\\b(Talagrand|Efron[- ]Stein|convex distance)\\b', re.I), "Talagrand's and Efron-Stein"),
    (re.compile('\\b(Vitali(?:\\x27s)? (?:covering|cover)|Lebesgue differentiation|Hardy[- ]Littlewood maximal|maximal function)\\b', re.I), 'Vitali covering, Lebesgue differentiation'),
    (re.compile('\\b(Doob[- ]Meyer|compensator|Doob decomposition|predictable quadratic)\\b', re.I), 'Doob decomposition, Doob-Meyer'),
    (re.compile('\\buncorrelated\\b|\\bpairwise independen', re.I), 'Independence vs uncorrelated'),
    (re.compile('\\b(in probability|almost surely|in distribution|in law|in L\\^?\\s*[1p2])\\b', re.I), 'Convergence modes'),
    (re.compile('\\bFeynman[- ]Kac\\b', re.I), 'Feynman-Kac formula'),
    (re.compile('\\b(Foster|Lyapunov function|drift condition|drift inequality|geometric(ally)? ergodic|petite set|small set)\\b', re.I), 'Foster-Lyapunov drift conditions'),
    (re.compile('\\b(spectral gap|mixing time|relaxation time|Cheeger|conductance|bottleneck ratio)\\b', re.I), 'Spectral gap, mixing time and Cheeger'),
    (re.compile('\\b(Doeblin|minori[sz]ation|coupling time|coupling inequality)\\b', re.I), "Doeblin's condition, minorisation and coupling"),
    (re.compile("\\b(Kolmogorov('?s)? extension|Carath[e\\u00e9]odory|consistent family|finite[- ]dimensional distributions)\\b", re.I), 'Kolmogorov extension and Caratheodory'),
    (re.compile('\\b(Glivenko|Kolmogorov[- ]Smirnov|Dvoretzky|DKW|empirical (distribution|process))\\b', re.I), 'Kolmogorov-Smirnov, Glivenko-Cantelli and DKW'),
    (re.compile('\\b(Borell|Tsirelson|Slepian|Sudakov|Fernique|Gaussian concentration)\\b', re.I), 'Gaussian concentration (Borell-TIS'),
    (re.compile('\\b(Dudley|chaining|entropy integral|covering number|metric entropy)\\b', re.I), "Dudley's entropy bound and chaining"),
    (re.compile('\\b(Rosenthal|Marcinkiewicz|Khintchine[- ]Kahane|Kahane|von Bahr)\\b', re.I), 'Rosenthal, Marcinkiewicz-Zygmund and Khintchine-Kahane'),
    (re.compile('\\b(Bennett|Freedman|matrix Bernstein|Tropp)\\b', re.I), 'Bennett, Freedman and the matrix Bernstein'),
    (re.compile('\\b(L[e\\u00e9]vy[- ]Khintchine|L[e\\u00e9]vy measure|L[e\\u00e9]vy process|subordinator|infinitely divisible|characteristic exponent)\\b', re.I), 'Levy processes and the Levy-Khintchine formula'),
    (re.compile('\\b(iterated logarithm|LIL|strong law of large numbers|SLLN)\\b', re.I), "Khintchine's law of the iterated logarithm"),
    (re.compile('\\b(Harnack|Nash inequality|heat kernel bound|Gaussian (upper|lower) bound|volume doubling)\\b', re.I), 'Harnack inequalities and Nash'),
    (re.compile('\\b(Bochner|Plancherel|Parseval|Fourier inversion|inversion formula|positive definite)\\b', re.I), 'Bochner, Parseval-Plancherel and Fourier inversion'),
    (re.compile('\\b(Hille[- ]Yosida|Trotter|Kurtz|Lumer|core of the generator|a core for)\\b', re.I), 'Hille-Yosida, Trotter-Kurtz'),
    (re.compile('\\b(Aldous|Meyer[- ]Zheng|C-tight|J1 topology|Skorokhod (space|topology))\\b', re.I), "Aldous' tightness criterion and Meyer-Zheng"),
    (re.compile('\\b(coupling from the past|Propp[- ]Wilson|Dobrushin)\\b', re.I), 'Coupling from the past and Dobrushin'),
    (re.compile('\\b(Kesten|critical probability|p_c\\b|infinite cluster|Burton[- ]Keane|Menshikov)', re.I), "Kesten's theorem and the critical probability"),
    (re.compile('\\b(reflection principle|running maximum|hitting time of (a )?level|first passage)\\b', re.I), 'The reflection principle and the running maximum'),
    (re.compile('\\b(strong(ly)? mixing|weak(ly)? mixing|mixing coefficient|alpha-mixing|beta-mixing|phi-mixing|\\\\alpha-mixing|\\\\beta-mixing)\\b', re.I), 'Ergodicity, mixing and the invariant'),
    (re.compile('(\\binfinitely often\\b|\\bi\\.o\\.|\\bfinitely often\\b|\\beventually\\b)', re.I), 'Almost sure vs in probability vs eventually'),
    (re.compile('\\b(tower property|conditional expectation|conditional Jensen|conditional variance|taking out what is known)\\b', re.I), 'Conditional expectation: tower property'),
    (re.compile('\\b(stopping time|optional time|F_\\\\?tau|debut)\\b', re.I), 'Stopping times and the optional sigma-algebra'),
    (re.compile('\\b(covariance|\\\\mathrm\\{Cov\\}|\\\\operatorname\\{Cov\\}|\\\\mathrm\\{Var\\}|\\\\operatorname\\{Var\\}|sample mean|sample variance)\\b', re.I), 'Variance, covariance and correlation bookkeeping'),
    (re.compile('\\b(moment generating function|cumulant|Legendre|exponential tilt|tilted (law|measure)|log-moment)\\b', re.I), 'Exponential families, moment generating functions'),
    (re.compile('\\b(Poisson process|thinning|superposition|memoryless|inter-?arrival|order statistics property)\\b', re.I), 'Poisson processes: superposition, thinning'),
    (re.compile('\\b(P[o\\u00f3]lya|ballot theorem|arcsine|simple random walk|return probability|Chung[- ]Fuchs)\\b', re.I), 'Random walks: recurrence by dimension'),
    (re.compile('\\b(Galton[- ]Watson|branching process|extinction probability|Kesten[- ]Stigum|Yaglom|offspring)\\b', re.I), 'Branching processes: extinction'),
    (re.compile('\\b(Gibbs measure|DLR|phase transition|Griffiths|GKS|GHS|Onsager|pure phase)\\b', re.I), 'Gibbs measures, DLR equations'),
    (re.compile('\\b(Ricci|curvature|Bakry[- ][E\\u00c9]mery|CD\\(|Ollivier|reflection coupling|Wasserstein contraction)', re.I), 'Wasserstein contraction, Ricci curvature'),
    (re.compile('\\b(Yamada[-\\s\\u2010-\\u2015]+Watanabe|pathwise uniqueness|strong solution|weak solution|comparison theorem|linear growth|locally Lipschitz)\\b', re.I), 'Stochastic differential equations: existence'),
    (re.compile('\\b(Kullback|KL divergence|mutual information|data[- ]processing|f-divergence|entropy inequality)\\b', re.I), 'Kullback-Leibler divergence, entropy and mutual information'),
    (re.compile("\\b(Weyl(?:['\\u2019]s)?\\s+inequality|Courant[-\\s\\u2010-\\u2015]+Fischer|Hoffman[-\\s\\u2010-\\u2015]+Wielandt|(?:min[-\\s\\u2010-\\u2015]+max|max[-\\s\\u2010-\\u2015]+min)\\s+(?:principle|characteri[sz]ation|formula|theorem for eigenvalues)|Rayleigh quotient|Schatten|Frobenius norm|operator norm)\\b", re.I), 'Matrix and operator inequalities'),
    (re.compile('\\b(Wigner|semicircle|Marchenko|Marcenko|Pastur|Tracy[- ]Widom|empirical spectral)\\b', re.I), "Wigner's semicircle law"),
    (re.compile('\\b(symmetri[sz]ation|Rademacher (?:complexity|average|process|signs?|sequence)|contraction principle|Massart|VC dimension|Ledoux)\\b', re.I), 'Symmetrisation, contraction and the Rademacher'),
    (re.compile("\\b(Kac('?s)? (formula|lemma)|return time|renewal|Blackwell|residual life|inspection paradox|size-biased)\\b", re.I), "Kac's formula, return times and the renewal"),
    (re.compile('\\b(regenerat|Harris recurren|split chain|Nummelin|atom of the chain|positive recurren)', re.I), 'Regeneration, Harris recurrence and the split chain'),
    (re.compile('\\b(class\\s+DL|class\\s+D\\b|uniformly integrable (?:local )?(?:martingale|submartingale)|closed martingale)\\b', re.I), 'Class D and class DL: which uniform integrability upgrades a local martingale'),
    (re.compile("\\b(Burkholder|Burkholder[-\\s]Davis[-\\s]Gundy|BDG|Davis(?:'s|s')? inequality|square function)\\b", re.I), 'Burkholder-Davis-Gundy: the exponent is p/2 and the bracket is the optional one'),
    (re.compile("\\b(Dubins[-\\s]Schwarz|time[-\\s]chang(?:e|ed|ing)|Knight(?:'s|s')? theorem|random time change)\\b", re.I), 'Dubins-Schwarz: a continuous local martingale is Brownian motion on its own clock'),
    (re.compile('\\b(strict local martingale|(?:nonnegative|non-negative|positive) local martingale|localizing sequence|reducing sequence|only a supermartingale|local martingale (?:but|which is|that is) not a (?:true |uniformly integrable )?martingale)\\b', re.I), 'Strict local martingales: a nonnegative local martingale is only a supermartingale'),
    (re.compile('\\b(Novikov|Kazamaki|Girsanov|exponential (?:local )?martingale|change of measure)\\b', re.I), 'Novikov and Kazamaki: which bracket goes with which criterion, and the constant 1/2'),
    (re.compile('\\b(Lenglart|domination inequality|dominated by an increasing process|weak[-\\s]type inequality)\\b', re.I), "Lenglart's domination inequality: domination at stopping times, and a weak-type conclusion"),
    (re.compile('\\b(Kunita[-\\s]Watanabe|mutual (?:bracket|variation)|cross[-\\s]variation|bracket <M,\\s*N>)\\b', re.I), 'Kunita-Watanabe: a pathwise Cauchy-Schwarz with square roots on both brackets'),
    (re.compile("\\b(compensator|counting process|Doob[-\\s]Meyer|stochastic intensity|point process|Watanabe(?:'s|s')? characteri[sz]ation)\\b", re.I), 'Compensator of a counting process: predictability is what makes it unique, and <M> is not [M]'),
    (re.compile('\\b(optional projection|predictable projection|dual predictable projection|predictable time|totally inaccessible|announc(?:ing|eable))\\b', re.I), 'Optional versus predictable projection: F_T with stopping times, F_{T-} with predictable times'),
    (re.compile('\\b(maximal identity|last[-\\s]passage time|Az[eé]ma[-\\s]Yor|Az[eé]ma supermartingale|honest time|Skorokhod embedding)\\b', re.I), "Doob's maximal identity and last passage: the ratio is start over level, x/a"),
    (re.compile("\\b(stochastic integral|It[oô](?:'s|s')? isometry|predictable integrand|integral against a (?:local )?martingale)\\b", re.I), 'Stochastic integrals against local martingales: predictable integrands, and only a local martingale'),
    (re.compile('\\b(Dol[eé]ans[-\\s]Dade|stochastic exponential|exponential (?:local )?martingale|E\\(M\\)|Novikov)\\b', re.I), 'Doleans-Dade exponential: the bracket correction is subtracted, with coefficient one half'),
    (re.compile("\\b(It[o\\u00f4\\u014d](?:['\\u2019]s)?\\s+isometry|isometry of the stochastic integral|predictable integrand|progressively measurable|square[- ]integrable integrand)\\b", re.I), 'Ito isometry: equality holds only under the L^2 hypothesis, and against d<M>, not ds'),
    (re.compile("\\b(It[oôō]'?s? (formula|lemma)|change of variables? formula|second[- ]order term|semimartingale)\\b", re.I), "Ito's formula: the one half, the second derivative, and the bracket it is integrated against"),
    (re.compile('\\b(integration by parts for (?:a |the )?semimartingales?|semimartingale product rule|quadratic (?:co)?variation|covariation|polari[sz]ation identity|predictable bracket|X_\\{s-\\}|left limits?)\\b', re.I), 'Integration by parts for semimartingales: the covariation term is added and the integrands are left limits'),
    (re.compile('\\b(Tanaka|local time|occupation (density|times) formula|sgn\\(|It[oôō][- ]Tanaka|L_t\\^?\\{?0)\\b', re.I), "Tanaka's formula and local time: which version carries the one half and how the sgn convention is fixed"),
    (re.compile("\\b(L[eé]vy'?s? (characteri[sz]ation|theorem)|characteri[sz]ation of Brownian motion|continuous local martingale)\\b", re.I), "Levy's characterisation of Brownian motion: continuity plus bracket exactly t, for all t and all components"),
    (re.compile('\\b(Novikov|Kazamaki|Dol[eé]ans|stochastic exponential|exponential (local )?martingale|uniformly integrable martingale)\\b', re.I), 'Stochastic exponentials, Novikov and Kazamaki: the minus one half, and which criterion reads which process'),
    (re.compile('\\b(Girsanov|change of measure|equivalent (probability )?measure|Radon[- ]Nikod[yý]m derivative|remove the drift)\\b', re.I), "Girsanov's theorem: the drift is subtracted, and the density must be a true martingale"),
    (re.compile('\\b(Stratonovich|Fisk[- ]Stratonovich|Wong[- ]Zakai|conversion (term|formula)|circ ?d[XW])\\b', re.I), 'Stratonovich versus Ito: the conversion term is plus one half the covariation of integrand with integrator'),
    (re.compile('\\b(Dambis|Dubins[- ]Schwarz|DDS|time[- ]chang(e|ed)|random time change|inverse of the quadratic variation)\\b', re.I), 'Dambis-Dubins-Schwarz: the time change runs one way, and the bracket must exhaust infinity'),
    (re.compile('\\b(martingale representation|predictable representation|Brownian filtration|representation theorem|generated by the Brownian)\\b', re.I), 'Martingale representation: the filtration must be the Brownian one, and there is no drift term'),
    (re.compile("\\b(Burkholder|Davis[- ]Gundy|BDG|maximal inequality|Doob'?s (L\\^?p )?inequality|running maximum)\\b", re.I), 'Burkholder-Davis-Gundy: the bracket enters at exponent p/2, and both directions hold'),
    (re.compile('\\b(Malliavin|Clark[- ]Ocone|Skorokhod integral|divergence operator|integration by parts formula|D_t ?F)\\b', re.I), 'Malliavin integration by parts: which operator is the adjoint, and the conditional expectation in Clark-Ocone'),
    (re.compile('\\b(Hille[-\\s]?Yosida|Lumer[-\\s]?Phillips|dissipativ\\w*|resolvent|contraction semigroup|C_?0[-\\s]?semigroup|positive maximum principle)\\b', re.I), 'Hille-Yosida and the resolvent bound: which way the dissipativity inequality runs and that ||R_lambda|| <= 1/lambda'),
    (re.compile('\\b(core for (?:the|a) (?:generator|operator)|is a core|essential(?:ly)? self[-\\s]?adjoint|graph norm|domain of the generator|closure of the (?:generator|operator))\\b', re.I), 'Cores for a generator: dense in the graph norm, not merely dense in the Banach space'),
    (re.compile("\\b(Dynkin'?s? formula|martingale problem|well[- ]posed martingale problem|generator of the (?:process|diffusion|semigroup)|A ?f\\(X_s\\)|in D\\(A\\))\\b", re.I), "Dynkin's formula and the martingale problem: the compensator is subtracted, and the stopping time needs an integrability hypothesis"),
    (re.compile('\\b(detailed balance|reversib(?:le|ility)|self[-\\s]?adjoint (?:on|in|with respect to) L\\^?2|Peskun|electrical network|conductance c\\(x,y\\))\\b', re.I), 'Detailed balance and reversibility: it implies stationarity but not conversely, and both measures must be indexed correctly'),
    (re.compile('\\b(Dirichlet form|spectral gap|Rayleigh quotient|variational characteri[sz]ation|canonical paths|energy form)\\b', re.I), 'Dirichlet form and the variational spectral gap: an infimum over centred test functions, with the factor 1/2'),
    (re.compile('\\b(Poincar[eé] inequality|spectral gap|exponential ergodicity|chi[-\\s]?square distance|L\\^?2 (?:decay|contraction)|relaxation)\\b', re.I), 'Poincare inequality for a Markov semigroup: gap in the denominator, and the exponent 2 gap for the variance'),
    (re.compile('\\b(relaxation time|mixing time|t_?\\{?mix|t_?\\{?rel|absolute spectral gap|second[-\\s]?largest eigenvalue|hitting time of a set)\\b', re.I), 'Relaxation, mixing and hitting times: the absolute gap, and the two-sided bound with log(1/pi_min)'),
    (re.compile('\\b(ergodic theorem|aperiodic\\w*|period(?:ic|icity) of the chain|irreducib\\w+|positive recurrent|null recurrent|Harris recurren\\w+|Ces[aà]ro average)\\b', re.I), 'Ergodic theorem for Markov chains: the law of large numbers needs no aperiodicity, convergence of the n-step law does'),
    (re.compile('\\b(coupling (?:inequality|argument|time)|maximal coupling|coalesc\\w+|total variation distance|TV distance|in total variation|submultiplicativ\\w+)\\b', re.I), 'Coupling inequality and total variation: the 1/2 sits with the L^1 sum, and the coupling gives an upper bound only'),
    (re.compile('\\b(strong stationary time|separation distance|sep\\(|randomi[sz]ed stopping time|stationary time)\\b', re.I), 'Strong stationary times: independence of position and time, and that they bound separation rather than total variation'),
    (re.compile('\\b(cutoff(?: phenomenon| window)?|pre[-\\s]?cutoff|product condition|birth[-\\s]?and[-\\s]?death chain)\\b', re.I), 'Cutoff and the product condition: necessary for reversible chains, not sufficient'),
    (re.compile("\\b(harmonic function|Dirichlet problem|hitting probabilit\\w+|hitting time|escape probability|effective resistance|Kac'?s formula|return time|tau_?[A-Za-z]?\\^?\\+)\\b", re.I), "Harmonic functions, hitting probabilities and Kac's formula: uniqueness needs almost sure absorption, and the return time is 1/pi(x)"),
    (re.compile("\\b(sub[- ]?[Gg]aussian|Hoeffding'?s? (lemma|inequality)|variance proxy|psi[-_ ]?2 norm|Orlicz norm)\\b", re.I), "Sub-Gaussian variables and Hoeffding's inequality: which constant sits under the square"),
    (re.compile("\\b(sub[- ]?exponential|psi[-_ ]?1 norm|Bernstein'?s? (moment )?condition|Orlicz|sub[- ]?gamma)\\b", re.I), 'Sub-exponential and psi_1 norms: the moment generating function only exists near zero'),
    (re.compile("\\b(Bernstein'?s? inequality|Bennett'?s? inequality|Bernstein[- ]type (bound|inequality)|Bennett[- ]type)\\b", re.I), "Bernstein's and Bennett's inequalities: the variance, not the range, sets the Gaussian regime"),
    (re.compile('\\b(Efron[-\\s]?Stein|jackknife|tensoriz\\w+ of (the )?variance|independent copy of)\\b', re.I), 'Efron-Stein inequality: it bounds the variance from above, and the 1/2 belongs to the resampled form'),
    (re.compile('(\\bEnt\\b|\\bsubadditivity of (the )?entropy\\b|\\bentropy method\\b|\\bmodified log[-\\s]?Sobolev\\b|\\bexponential Efron[-\\s]?Stein\\b)', re.I), 'Subadditivity of entropy and the modified log-Sobolev bound: it is the entropy of the exponential'),
    (re.compile("\\b(Herbst'?s? (argument|method)|Herbst)\\b", re.I), 'Herbst argument: the boundary value as lambda goes to zero is E[Z], not zero'),
    (re.compile('\\b(log(arithmic)?[-\\s]?Sobolev|Borell[-\\s]?TIS|Borell[-\\s]?Tsirelson|Gaussian concentration|Lipschitz function of a Gaussian|Poincar[eé] inequality)\\b', re.I), 'Gaussian log-Sobolev and Lipschitz concentration: the constant is dimension free and sigma^2 is pointwise'),
    (re.compile('\\b(McDiarmid|bounded[-\\s]differences?|Azuma[-\\s]?Hoeffding)\\b', re.I), "McDiarmid's bounded differences inequality: sum of squared constants, with a 2 in the numerator"),
    (re.compile('\\b(self[-\\s]?bounding|configuration function|sub[-\\s]?Poissonian)\\b', re.I), 'Self-bounding functions: the t/3 correction belongs to the upper tail only'),
    (re.compile("\\b(Talagrand'?s? (convex distance|inequality)|convex distance|convex[-\\s]distance inequality)\\b", re.I), "Talagrand's convex distance inequality: the exponent carries a 4 and the sup sits outside the inf"),
    (re.compile('\\b(negativ\\w+ (associat\\w+|dependen\\w+)|without replacement|hypergeometric|Serfling|permutation distribution|balls[-\\s]in[-\\s]bins)\\b', re.I), 'Negative association and sampling without replacement: Chernoff bounds transfer, and never get worse'),
    (re.compile("\\b(transport(ation)?[-\\s]cost|Marton|Bobkov[-\\s]?G[oö]tze|Pinsker'?s? inequality|Wasserstein|T_2 inequality|Otto[-\\s]?Villani)\\b", re.I), 'Transportation-cost inequalities: entropy bounds transport, and T_2 is strictly stronger than T_1'),
    (re.compile("\\b(Lindeberg[-\\s]?Feller|Lindeberg(?:'s)? condition|triangular array|uniformly asymptotically negligible|asymptotic negligibility)\\b", re.I), 'Lindeberg-Feller CLT: the truncation runs over the large values, for every epsilon'),
    (re.compile("\\b(Lyapunov(?:'s)?\\s+(?:condition|criterion|CLT|ratio)|(?:two|2)\\s*\\+\\s*\\\\?delta(?:\\s+moment)?)\\b", re.I), 'Lyapunov condition: the moment exponent is 2+delta on top and s_n^{2+delta} below'),
    (re.compile('\\b(Lindeberg (?:replacement|swapping|exchange)|swapping (?:method|argument)|replac(?:e|ing) one (?:summand|variable|coordinate) at a time|telescop\\w+ sum of Gaussians)\\b', re.I), 'Lindeberg swapping method: two moments matched, third derivative, remainder constant 1/6'),
    (re.compile("\\b(Berry[-\\s]?Esseen|Esseen(?:'s)? (?:bound|inequality|estimate)|uniform (?:rate|bound) (?:of|in) (?:the )?(?:normal approximation|CLT))\\b", re.I), 'Berry-Esseen bound: rate n^{-1/2}, third absolute moment over sigma^3'),
    (re.compile('\\b(martingale (?:central limit theorem|CLT|difference array)|martingale differences?|Hall[-\\s]?Heyde|McLeish|predictable quadratic variation)\\b', re.I), 'Martingale CLT: conditional variances converge in probability plus conditional Lindeberg'),
    (re.compile('\\b(conditional Lindeberg|conditional Lyapunov|asymptotic(?:ally)? negligib\\w+|infinitesimal array|uniformly negligible summands)\\b', re.I), 'Conditional Lindeberg and negligibility: what may replace it and what it forces'),
    (re.compile('\\b((?:strong|alpha|phi|uniform)(?:ly)?[-\\s]?mixing (?:sequence|array|CLT|process|central limit)|Ibragimov|long[-\\s]?run variance|asymptotic variance series)\\b', re.I), 'CLT for stationary mixing sequences: the variance series is Var(X_0) + 2 sum_{k>=1} Cov(X_0,X_k)'),
    (re.compile("\\b(delta method|second[-\\s]?order delta method|Taylor expan\\w+ (?:of|about) (?:the estimator|\\\\?theta)|g'\\(\\\\?theta\\))", re.I), 'Delta method: differentiability at the point, and the derivative enters squared'),
    (re.compile('\\b(Anscombe|random(?:ly)? (?:index|indices|number of (?:summands|terms))|randomly stopped sum|random time change in the CLT)\\b', re.I), "Anscombe's theorem: random indices need N_n/n -> c and uniform continuity in probability"),
    (re.compile('\\b(Cram(?:e|é)r[-\\s]?Wold|linear combinations? of the components|one[-\\s]?dimensional projections|multivariate (?:CLT|central limit theorem))\\b', re.I), 'Cramer-Wold device: every linear combination, and only for convergence in distribution'),
    (re.compile('\\b(stable (law|laws|distribution|limit)|alpha[- ]stable|α-stable|(?<!max[- ])(?<!maximum )domain of attraction|non[- ]normal domain of attraction)\\b', re.I), 'Stable limits and domains of attraction: tail index alpha, scaling n^{1/alpha}, tail balance'),
    (re.compile("\\b(Edgeworth (?:expansion|correction)|Cram(?:e|é)r(?:'s)? condition|lattice (?:span|distribution) correction|skewness correction term|cumulant expansion of the distribution function)\\b", re.I), "Edgeworth expansion: Cramer's condition, the order of the remainder, and the lattice exception"),
    (re.compile('\\b(contraction principle|push(-|\\s)?forward of the (LDP|large deviation)|rate function of the image|inf\\s*\\{?\\s*I\\(x\\)\\s*:\\s*f\\(x\\)\\s*=)\\b', re.I), 'Contraction principle: the map must be continuous, and the rate infimum runs over the preimage'),
    (re.compile('\\b(G(ä|a)rtner[- ]Ellis|essentially smooth|steepness|Legendre[- ]Fenchel|limiting (log[- ])?(cumulant|moment) generating function|Lambda\\^?\\*)\\b', re.I), 'Gartner-Ellis: the limiting log-mgf must be essentially smooth and lower semicontinuous, and only then is the closed-set bound available'),
    (re.compile('\\b(exponential(ly)? tight(ness)?|weak (LDP|large deviation principle)|upper bound (on|over) compact|good rate function)\\b', re.I), 'Upper bound on compacts versus on closed sets: only exponential tightness closes the gap'),
    (re.compile("\\b(Varadhan('s)? (integral )?lemma|Laplace principle)\\b|sup_?\\{?x\\}?\\s*\\(?\\s*F\\(x\\)\\s*-\\s*I\\(x\\)", re.I), "Varadhan's integral lemma: the exponential moment condition and why it is a sup minus the rate"),
    (re.compile("\\b(Bryc('s)? (inverse )?lemma|inverse (of )?Varadhan|Laplace[- ]principle equivalence)\\b", re.I), "Bryc's inverse lemma: the limit must exist for all bounded continuous F, and tightness is not optional"),
    (re.compile('\\b(Freidlin[- ]Wentzell|small[- ]noise (diffusion|limit)|Schilder|quasi(-|\\s)?potential|action functional)\\b', re.I), 'Freidlin-Wentzell: the small-noise rate is the energy with a 1/2 and the inverse diffusion matrix, at speed epsilon^{-2}'),
    (re.compile('\\b(moderate deviations?|moderate[- ]deviation (principle|regime)|between the (CLT|central limit theorem) and (Cram(é|e)r|large deviations))\\b', re.I), 'Moderate deviations: the scaling window between the CLT and Cramer, and why the rate is always Gaussian'),
    (re.compile('\\b(liminf.{0,40}open|limsup.{0,40}closed|I[- ]continuity set|lower bound (over|on) open sets|upper bound (over|on) closed sets|both bounds of the (LDP|large deviation principle))\\b', re.I), 'Laplace-Varadhan matching: the LDP is two inequalities in opposite directions over different set classes'),
    (re.compile('\\b(exponential(ly)? (good )?(equivalen(t|ce)|approximations?)|extended contraction principle|uniformly on sublevel sets)\\b', re.I), 'Exponentially good approximations: the extended contraction principle needs a uniform-on-sublevel-sets limit'),
    (re.compile("\\b(Legendre transform|convex conjugate|Cram(é|e)r('s)? theorem|log[- ]moment generating function|rate function .{0,20}sup_)\\b", re.I), "Legendre transform of the log-mgf: convexity, the direction of the duality, and where Cramer's rate is finite"),
    (re.compile("\\b(exponential Chebyshev|exponential Markov inequality|Markov('s)? inequality.{0,40}exp|union bound.{0,60}(exponential|large deviation|rate function)|finite cover.{0,30}compact)\\b", re.I), 'Exponential Chebyshev and the union bound: the two inequalities every LDP upper bound is built from'),
    (re.compile('\\b(speed (of the )?(LDP|large deviation)|at speed \\\\?[a-z]_n|scale a_n|normali[sz]ation of the (rate|deviation))\\b', re.I), 'Speed and the change-of-speed trap: rate functions only compare at the same normalisation'),
    (re.compile("\\b(Harris(?:'s)?[- ]?FKG|FKG inequality|Harris inequality|positively correlated|positive(ly)? associat)", re.I), 'Harris-FKG inequality: both events increasing, and which way the correlation runs'),
    (re.compile('\\b(FKG lattice condition|strong FKG|random[- ]cluster|Fortuin[- ]Kasteleyn|distributive lattice|comparison inequalit)', re.I), 'FKG lattice condition: what positive association needs beyond a product measure'),
    (re.compile("\\b(van den Berg[- ]Kesten|BK inequality|BK[- ]Reimer|Reimer'?s|disjoint occurrence|disjointly (occur|open)|edge[- ]disjoint (open )?paths)", re.I), 'BK-Reimer inequality: disjoint occurrence, and the direction opposite to FKG'),
    (re.compile("\\b(Russo'?s formula|Margulis[- ]Russo|pivotal(ity)?|pivotal (edge|site|coordinate|bond)|total influence)", re.I), "Russo's formula: the derivative is a sum of pivotal probabilities, for increasing events"),
    (re.compile('\\b(Russo[- ]Seymour[- ]Welsh|\\bRSW\\b|box[- ]crossing|crossing probabilit|left[- ]right crossing|arm (event|exponent)s?)', re.I), 'Russo-Seymour-Welsh box crossings: bounded away from 0 AND from 1, uniformly in the scale'),
    (re.compile('\\b(sharp threshold|coarse threshold|threshold window|Friedgut[- ]Kalai|Kahn[- ]Kalai[- ]Linial|\\bKKL\\b|Bourgain[- ]Kalai|influence of (a |the )?(variable|coordinate))', re.I), 'Friedgut-Kalai and KKL: sharp thresholds need transitivity, and the width is absolute'),
    (re.compile('\\b(OSSS|revealment|randomized (decision tree|algorithm)|decision tree algorithm|Duminil[- ]Copin.{0,20}Raoufi.{0,20}Tassion)', re.I), 'OSSS inequality: variance bounded by revealment times influence, summed'),
    (re.compile('\\b(giant component|Erd[oő]s[-– ]R[eé]nyi|G\\(n, ?p\\)|branching process (approximation|comparison|coupling)|survival probability|exploration process)', re.I), 'Erdos-Renyi giant component: the survival equation and the c > 1 hypothesis'),
    (re.compile('(critical window|scaling window|barely (sub|super)critical|multiplicative coalescent|n\\^\\{?-?4/3\\}?|n\\^\\{?2/3\\}?)', re.I), 'The critical window of G(n,p): n^(-4/3) in p, n^(2/3) in component size'),
    (re.compile('\\b(copies of H|subgraph counts?|balanced (?:graph|subgraph)|densest subgraph|m\\(H\\)|second[- ]moment method.{0,40}(?:copies|subgraph|G\\(n)|threshold for containing)\\b', re.I), 'Second-moment threshold for subgraph containment: m(H) is a maximum over subgraphs'),
    (re.compile('\\b(connectivity threshold|connected with high probability|isolated vert(ex|ices)|coupon collector|minimum degree (reaches|is at least)|hitting time (for|of) (connectivity|Hamiltonicity))\\b', re.I), 'Connectivity threshold of G(n,p): (log n + c)/n and the double-exponential limit'),
    (re.compile('\\b(increasing event|decreasing event|monotone (event|property|graph property)|non-?monotone|upward[- ]closed|stochastic(ally)? dominat|monotone coupling)', re.I), 'Monotone versus non-monotone events: which percolation tool is licensed'),
    (re.compile('\\b(Laplace functional|probability generating functional|generating functional|characteristic functional|Poisson random measure)\\b', re.I), 'Laplace functional of a Poisson process: the exponent is -int (1 - e^{-f}) dLambda'),
    (re.compile('\\b(Mecke (equation|formula|identity)|multivariate Mecke|Slivnyak[- ]Mecke)\\b', re.I), 'Mecke equation: the right-hand side evaluates the functional at the configuration with the point added'),
    (re.compile('\\b(Papangelou|Georgii[- ]Nguyen[- ]Zessin|GNZ (equation|formula)|conditional intensity|Gibbs point process)\\b', re.I), 'GNZ equation and the Papangelou intensity: kappa is evaluated without the point, f with it'),
    (re.compile("\\b(Campbell(\\'s)? (theorem|formula)|refined Campbell|Campbell[- ]Mecke|Palm (distribution|measure|version|expectation)|reduced Palm|Slivnyak)\\b", re.I), 'Campbell formula, Palm distributions and Slivnyak: reduced versus unreduced Palm'),
    (re.compile("\\b(Palm inversion( formula)?|Neveu('s)? exchange formula|exchange formula|Palm (calculus|distribution|measure|version|expectation)|reduced Palm|typical (point|cell)|inspection paradox|length[- ]bias\\w*)\\b", re.I), 'Palm inversion formula: the prefactor is lambda and the typical cell has mean volume 1/lambda'),
    (re.compile('\\b(factorial moment (measure|density|expansion)|(second|k[- ]?th|kth)[- ]order (moment|product|correlation) (measure|density|function)|pair correlation|Ripley|correlation function(s)? rho)\\b', re.I), 'Factorial moment measures: the distinct-tuple sum and the missing diagonal term'),
    (re.compile('\\b(determinantal|Macchi|Soshnikov|projection kernel|det\\(K|correlation kernel)\\b', re.I), 'Determinantal point processes: spectrum in [0,1], and rho_2 subtracts |K(x,y)|^2'),
    (re.compile('\\b(permanental|permanent of the (matrix|kernel)|per\\(K|boson(ic)? (process|point process)|alpha[- ](determinantal|permanental))\\b', re.I), 'Permanental processes: every determinantal sign is reversed and the spectral bound is not needed'),
    (re.compile('\\b(Chen[- ]Stein|Stein[- ]Chen|Poisson approximation|dependency (graph|neighbou?rhood)|Arratia|total variation distance.{0,30}Poisson)\\b', re.I), 'Chen-Stein with a dependency graph: b1 keeps the diagonal, b2 drops it, and the Stein factor falls with lambda'),
    (re.compile('\\b(thinn(ing|ed|s)|p-thinning|retention probability|independently (retain|delete|keep)s? each point)\\b', re.I), 'Thinning theorem: retained and discarded parts of a Poisson process are independent Poisson'),
    (re.compile('\\b(marking theorem|displacement theorem|mapping theorem[^.\\n]{0,80}\\b(?:Poisson|point process)|(?:Poisson|point process)[^.\\n]{0,80}\\bmapping theorem|independently (?:marked|displaced)|marked point process|push[- ]?forward of the intensity)\\b', re.I), 'Marking, displacement and mapping theorems: the kernel is integrated against Lambda in the first variable'),
    (re.compile("\\b(void probabilit|avoidance function|Renyi(\\'s)? theorem|Rényi(\\'s)? theorem|complete(ly)? independen(t|ce)|simple point process|no fixed atoms)\\b", re.I), "Void probabilities and Renyi's theorem: the conclusion needs the process to be simple"),
    (re.compile('\\b(Stieltjes transform|Cauchy transform|Stieltjes inversion|inversion formula|upper half[- ]plane|resolvent trace)\\b', re.I), 'Stieltjes transform and its inversion: which sign convention, and the 1/pi in the density'),
    (re.compile('\\b(semi-?circle law|semi-?circular|Wigner (matrix|matrices|law|ensemble)|self-consistent equation)\\b', re.I), 'Semicircle law and its self-consistent equation: m^2 + z m + 1 = 0 and the branch at infinity'),
    (re.compile('\\b(Marchenko[- ]?Pastur|MP law|sample covariance matri|Wishart|aspect ratio|singular values of X)\\b', re.I), 'Marchenko-Pastur law: the edges are (1 +/- sqrt(y))^2 and the atom has mass 1 - 1/y'),
    (re.compile("\\b(local law|local semi-?circle law|isotropic (?:local )?law|resolvent (?:entries|of (?:H|the matrix|a Wigner|the random matrix))|Green(?:['\\u2019]s)?\\s+function (?:of|for) (?:H|the matrix|(?:a |the )?(?:Wigner|random) matrix)|spectral parameter)\\b", re.I), 'Local law for random matrices: eta >> 1/n, and the averaged rate is one power better than the entrywise one'),
    (re.compile('\\b(rigidity|classical location|classical eigenvalue location|quantile of the (semi-?circle|limiting)|eigenvalue location)\\b', re.I), 'Eigenvalue rigidity: the n^{-2/3} edge scale, n^{-1} in the bulk, and the min(i, n+1-i) factor'),
    (re.compile('\\b(Tracy[- ]?Widom|soft edge|Airy kernel|edge fluctuation|GOE|GUE|largest eigenvalue distribution)\\b', re.I), 'Tracy-Widom at the soft edge: the n^{-2/3} scale, and which tail has exponent 3/2'),
    (re.compile('\\b(BBP|Baik[- ]Ben ?Arous[- ]Pech|spiked (model|covariance|matrix)|outlier eigenvalue|deformed Wigner|rank[- ]one spike)\\b', re.I), 'BBP transition for spiked models: threshold sqrt(y) for covariance, 1 for additive, and the outlier formula'),
    (re.compile("\\b(Weyl'?s? inequalit|Lidskii|Hoffman[- ]?Wielandt|Ky Fan|eigenvalue perturbation|unitarily invariant norm)\\b", re.I), 'Weyl, Lidskii and Hoffman-Wielandt: eigenvalue perturbation needs Hermitian matrices and matched order'),
    (re.compile('\\b(interlac(e|es|ing)|Cauchy interlacing|principal submatrix|rank[- ]one (perturbation|update)|rank inequality|low[- ]rank perturbation)\\b', re.I), 'Cauchy interlacing and the rank inequality: the index shifts by exactly one per unit of rank'),
    (re.compile('\\b(Bai(?:[-\\s\\u2010-\\u2015]+)?Yin|Davidson[-\\s\\u2010-\\u2015]+Szarek|(?:operator|spectral) norm of (?:a|an|the) [^.\\n]{0,60}\\bGaussian matrix|(?:operator|spectral) norm of (?:a|the) (?:random|m x n) matrix|largest singular value|smallest singular value|sqrt\\(m\\) ?\\+ ?sqrt\\(n\\))\\b', re.I), 'Operator norm of a Gaussian matrix: sqrt(m) + sqrt(n), the 2 sqrt(n) symmetric edge, and dimension-free concentration'),
    (re.compile("\\b(Chevet|Gordon'?s? (inequality|minimax|comparison|theorem)|Slepian|Sudakov[- ]Fernique|Gaussian width|Gaussian comparison)\\b", re.I), 'Chevet, Slepian and Sudakov-Fernique: both cross terms, and which side of the comparison the maximum sits on'),
    (re.compile('\\b(universality of (?:the )?local|sine kernel|Airy kernel|four[- ]moment (?:theorem|matching)|local eigenvalue statistics|Dyson Brownian motion|bulk (?:universality|statistics))\\b', re.I), 'Universality of local eigenvalue statistics: sine kernel in the bulk, Airy at the edge, and what is not universal'),
    (re.compile("\\b(Birkhoff(?:'s)?(?:\\s+pointwise)?\\s+ergodic theorem|pointwise ergodic theorem|ergodic averages?|Birkhoff averages?|time averages? converge)\\b", re.I), 'Birkhoff pointwise ergodic theorem: the limit is E[f | invariant sigma-field], constant only under ergodicity'),
    (re.compile("\\b(von Neumann(?:'s)?(?:\\s+mean)? ergodic theorem|mean ergodic theorem|Koopman operator|projection onto the (?:space of )?invariant (?:functions|vectors))\\b", re.I), 'Von Neumann mean ergodic theorem: norm convergence to the projection on the fixed subspace, not pointwise'),
    (re.compile("\\b(maximal ergodic theorem|Hopf(?:'s)? maximal|ergodic maximal (?:inequality|function)|dominated ergodic theorem|weak[- ]type \\(1, ?1\\))\\b", re.I), 'Maximal ergodic theorem and the ergodic maximal inequality: which side is nonnegative, and where the constant lives'),
    (re.compile("\\b(Poincar[eé] recurrence|Kac(?:'s)? (?:lemma|formula)|first return time|return time to A|mean recurrence time)\\b", re.I), "Poincare recurrence and Kac's lemma: the mean return time is 1/mu(A), not mu(A)"),
    (re.compile('\\b(weak(?:ly)? mixing|mild(?:ly)? mixing|strong(?:ly)? mixing|mixing implies ergodic|ergodic but not mixing|T x T is ergodic)\\b', re.I), 'The mixing hierarchy: mixing implies mild implies weak implies ergodic, each strictly, and the absolute values matter'),
    (re.compile('\\b(stationary Gaussian (?:process|sequence|field)|Gaussian (?:process|shift) is (?:ergodic|mixing)|spectral measure (?:is continuous|has no atoms)|Maruyama|Rajchman|Kolmogorov[-–]Rozanov)\\b', re.I), 'Spectral criterion for a stationary Gaussian process: atomless gives ergodic, r(n) -> 0 gives mixing'),
    (re.compile("\\b(Kingman(?:'s)?|sub(?:-)?additive ergodic theorem|super(?:-)?additive ergodic theorem|Fekete(?:'s)? lemma|subadditive sequence)\\b", re.I), "Kingman's subadditive ergodic theorem: the constant is the infimum of E[X_{0,n}]/n, and subadditivity points one way"),
    (re.compile('\\b(Shannon[-–]McMillan[-–]Breiman|SMB theorem|entropy rate|asymptotic equipartition|typical set|-\\(1/n\\) ?\\\\?log p)\\b', re.I), 'Shannon-McMillan-Breiman: the a.s. limit is the entropy rate, which is an infimum and a conditional entropy'),
    (re.compile('\\b(Kolmogorov[-–]Sinai|measure[- ]theoretic entropy|entropy of the (?:shift|transformation|system)|generating partition|generator of the sigma[- ]field|Ornstein isomorphism)\\b', re.I), 'Kolmogorov-Sinai entropy: a sup over partitions, attained at any generator, with h(T^k) = |k| h(T)'),
    (re.compile('\\b(invariant sigma[- ]?(?:field|algebra)|ergodic decomposition|extreme points of the set of invariant measures|stationary but not ergodic|tail sigma[- ]?(?:field|algebra)|mixed normal)\\b', re.I), 'The invariant sigma-field and ergodic decomposition: what a non-ergodic stationary limit theorem actually says'),
    (re.compile('\\b(subshift|shift of finite type|specification property|topological(?:ly)? (?:mixing|entropy)|variational principle|equilibrium state|measure of maximal entropy|Perron root)\\b', re.I), 'Subshifts, specification and the variational principle: topological entropy is a supremum over invariant measures'),
    (re.compile('\\b(alpha[- ]mixing|strong(?:ly)? mixing coefficient|beta[- ]mixing|phi[- ]mixing|rho[- ]mixing|absolutely regular|maximal correlation|covariance inequality|Davydov|Ibragimov)\\b', re.I), 'Mixing coefficients for stationary sequences: alpha is the weakest, and the covariance bound carries the exponent 1/r'),
    (re.compile('\\b(Vitali convergence|uniformly integrable|uniform integrability|[Uu]\\.?[Ii]\\.?\\b|equi-?integrab)', re.I), 'Vitali convergence theorem: uniform integrability plus tightness replaces a dominating function'),
    (re.compile('\\b(dominated convergence|DCT|dominating function|bounded convergence theorem)\\b', re.I), 'Dominated convergence theorem: one fixed integrable dominating function, not an n-dependent bound'),
    (re.compile("\\b(Fatou'?s? lemma|Fatou|reverse Fatou)\\b", re.I), "Fatou's lemma: liminf on the inside is <= liminf on the outside, and only for nonnegative or bounded-below sequences"),
    (re.compile('\\b(layer[- ]cake|distribution function formula|E\\[X\\] *= *\\\\?int_?0\\^\\\\?infty|integrating the tail|tail integral)', re.I), 'Layer-cake formula: the tail is integrated in t from 0 to infinity, with the p t^{p-1} weight'),
    (re.compile("\\b(Markov'?s? inequality|Chebyshev'?s? inequality|Bienaym(e|é)\\w*)\\b", re.I), 'Markov and Chebyshev inequalities: which side of t, which power, and where the moment sits'),
    (re.compile('\\b(absolute(ly)? continu(ity|ous) of the integral|uniform(ly)? absolutely continuous|small(ness)? on sets of small measure|int_A \\|f\\|)', re.I), 'Absolute continuity of the integral: small sets carry small integral, uniformly only for a UI family'),
    (re.compile('\\b(Fubini|Tonelli|iterated integral|product measur(e|able|ability)|interchange the order of integration)\\b', re.I), 'Fubini and Tonelli: nonnegativity or integrability of the double integral, and joint measurability first'),
    (re.compile("\\b(Egoro[fv]|Egorov'?s theorem|Lusin|almost uniform convergence)\\b", re.I), "Egorov's theorem: finite measure, a.e. convergence, and uniform convergence off a small set (not off a null set)"),
    (re.compile('\\b(Riesz[- ]Fischer|L\\^?p is complete|completeness of L\\^?p|Banach space L\\^?p|essential sup(remum)?)\\b', re.I), 'Riesz-Fischer: L^p is complete for 1 <= p <= infinity, and convergence in L^p gives only a subsequence a.e.'),
    (re.compile('\\b(weak[- ]?\\*|weak-?star|weakly (convergen|compact)|Dunford[- ]Pettis|Banach[- ]Alaoglu|conjugate exponent|dual of L\\^?p)', re.I), 'Weak versus weak-star convergence in L^p: which duality holds, and what compactness it buys'),
    (re.compile("\\b(Minkowski('s)? inequality|conjugate exponents?|H(o|ö)lder'?s? inequality with exponents?|which exponent)\\b", re.I), 'Holder, Minkowski and Jensen: which exponent goes on which factor and which way the convexity runs'),
    (re.compile('\\b(completion of the sigma[- ]algebra|complete(d)? filtration|usual conditions|null sets? (are )?(adjoined|added)|indistinguishab\\w*|(measurable |continuous )?modification of (the|a) process|version of (the|a) (process|random variable|conditional expectation|density))\\b', re.I), "Completion of a sigma-algebra and a.e. statements: null sets, versions, and where 'for all omega' is not available"),
    (re.compile('\\b(regular(ly)? var(iation|ying)|slow(ly)? var(iation|ying)|uniform convergence theorem|index of regular variation)\\b', re.I), 'Regular variation: which index carries the minus sign, and how uniform the limit is'),
    (re.compile('\\b(Karamata|truncated (second )?moment|integrat(e|ing|ion of) (a )?regularly varying)\\b', re.I), "Karamata's theorem: the integral is x f(x) over the distance of the index from -1"),
    (re.compile("\\b(Potter'?s? (bound|bounds|inequality)|Karamata representation|representation theorem for slowly varying)\\b", re.I), 'Karamata representation and Potter bounds: a max of two powers, valid only past a threshold'),
    (re.compile("\\b(Tauberian|Abelian theorem|Laplace[- ]Stieltjes transform|Karamata'?s Tauberian)\\b", re.I), "Karamata's Tauberian theorem: infinity for the function, zero for the transform, and the gamma factor"),
    (re.compile('\\b(Fisher[- ]Tippett|Gnedenko|generali[sz]ed extreme value|extreme value (index|distribution|law)|GEV|Fr[eé]chet|Gumbel)\\b', re.I), 'Fisher-Tippett-Gnedenko: the GEV exponent is -1/xi and the sign of xi picks the type'),
    (re.compile('\\b(max[- ]domain of attraction|maximum domain of attraction|von Mises condition|norming constants|MDA)\\b', re.I), 'Max-domains of attraction: n times the tail converges, and the limit is exp(-tau)'),
    (re.compile("\\b(subexponential|one[- ]big[- ]jump|max[- ]sum equivalence|Kesten'?s bound|convolution tail)\\b", re.I), 'Subexponentiality: the two-fold convolution tail is twice the tail, and one big jump does it'),
    (re.compile('\\b(long[- ]tailed|regularly varying .{0,30}subexponential|subexponential .{0,30}(regularly varying|long[- ]tailed|heavy[- ]tailed)|tail[- ]equivalen\\w+|closed under convolution)\\b', re.I), 'Regularly varying versus subexponential: strict inclusions and which tail survives a sum'),
    (re.compile("\\bBreiman'?s (lemma|theorem)\\b", re.I), "Breiman's lemma: the constant is E[Y^alpha] and the moment must reach strictly past alpha"),
    (re.compile('\\b(stable (law|laws|distribution|limit)|alpha[- ]stable|α-stable|domain of attraction|non[- ]normal domain of attraction)\\b', re.I), 'Stable limits: norming is n^{1/alpha}, tail balance is a hypothesis, and alpha = 2 is not a power tail'),
    (re.compile('\\b(Pickands|Balkema|generali[sz]ed Pareto|GPD|excess distribution|mean excess|peaks over threshold)\\b', re.I), 'Pickands-Balkema-de Haan: the excess law is generalised Pareto with the same xi as the maximum'),
    (re.compile('\\b(Hill estimator|Hill plot|tail index estimat\\w+|second[- ]order (regular variation|condition))\\b', re.I), 'The Hill estimator: it estimates 1/alpha, and k must go to infinity with k/n going to zero'),
]
_KNOW = None


def _named_result_domain_matches(text, key):
    """Bare irreducibility/periodicity is shared by unrelated mathematical fields."""
    perron = key == "Feller property, Perron-Frobenius"
    ergodic = key.startswith("Ergodic theorem for Markov chains:")
    if not (perron or ergodic):
        return True
    stochastic = re.search(
        r"\b(?:Markov\s+(?:chain|process|semigroup)|transition\s+(?:matrix|kernel|probabilit)|"
        r"stochastic\s+matri|stationary\s+(?:law|distribution)|"
        r"(?:irreducible|aperiodic|recurrent)\s+(?:Markov\s+)?chain|"
        r"(?:positive|null)\s+recurren|Harris\s+recurren)", text, re.I)
    if stochastic:
        return True
    if perron:
        return bool(re.search(
            r"\b(?:Perron[- \u2013]Frobenius|Feller\s+(?:property|process|semigroup)|"
            r"(?:nonnegative|non-negative|positive)\s+matri)", text, re.I))
    return False


def knowledge_notes(text, cap=3, note_budget=6000):
    """The stored statement of each named result the argument cites: hypotheses, direction, rate."""
    global _KNOW
    try:
        if _KNOW is None:
            with io.open(KNOWLEDGE, encoding="utf-8") as f:
                _KNOW = json.load(f)
    except Exception:
        return []
    # v6: every cue that fires is counted; the notes served are the cap most-cited named results (a result named
    # three times in the argument outranks one named once); generic-mode cues rank after specific named results.
    GENERIC = ("limsup and liminf", "Convergence modes", "Independence vs uncorrelated", "Almost sure vs in probability vs eventually", "Conditional expectation: tower property", "Stopping times and the optional sigma-algebra", "Variance, covariance and correlation bookkeeping")
    scored = []
    for rx, key in NAMED:
        n = len(rx.findall(text))
        if n and _named_result_domain_matches(text, key):
            scored.append((0 if key in GENERIC else 1, n, key))
    scored.sort(key=lambda x: (-x[0], -x[1]))
    # v7 (2026-09-08, from the Codex overnight census): 256 of the 299 stored notes are longer than the old 560-char
    # prefix, and a cut note can lose its qualification mid-sentence (167 of 197 notes served on the 199-item bank were
    # cut). A note is now served WHOLE under a shared budget, or omitted whole with a line saying so - never clipped.
    out, seen, used, omitted = [], set(), 0, []
    for _, n, key in scored:
        if len(out) + len(omitted) >= cap:
            break
        if key in seen:
            continue
        for k in _KNOW:
            if k.get("title", "").startswith(key):
                seen.add(key)
                ml = k.get("mathlib") or []
                card = "  - [named result: %s] %s%s" % (k["title"], k["body"],
                       (" | Mathlib leads (declarations in the local checkout, not proof certificates): " + ", ".join(ml[:3])) if ml else "")
                if used + len(card) <= note_budget:
                    out.append(card)
                    used += len(card)
                else:
                    omitted.append(k["title"])
                break
    for title in omitted:
        out.append("  - [named result omitted whole for budget: %s]" % title)
    return out


# the per-step inventory: the tokens where a one-token fault lives, listed per step so the reader looks AT them.
# Every step is listed, never a chosen one - this is attention, not an answer.
INV = [
    ("limit", re.compile(r"(?:\\to|\\rightarrow|\\longrightarrow|->|\u2192)\s*\{?\s*(\+?\\infty|\\infty|infty|0|-\\infty|[0-9]+|[a-zA-Z](?![a-zA-Z]))")),
    ("hyp", re.compile(r"\b(independent|independence|bounded|uniformly|uniform|continuous|continuity|compact|closed|open|finite|infinite|integrable|measurable|Lipschitz|H[o\u00f6]lder|smooth|analytic|monotone|increasing|decreasing|nondecreasing|nonincreasing|convex|concave|nonnegative|non-negative|positive|negative|strictly|centered|centred|symmetric|invariant|irreducible|aperiodic|recurrent|transient|countable|dense|separable|complete|i\.i\.d\.|iid|martingale|supermartingale|submartingale|exchangeable|stationary|ergodic|mixing|absolutely continuous|almost surely|almost everywhere|a\.s\.|a\.e\.|in probability|in distribution|in law|pointwise|at most|at least|above|below|upper|lower|larger|smaller|greater|less|maximal|minimal|sufficiently (large|small)|arbitrarily (large|small))\b", re.I)),
    ("rel", re.compile(r"\\(?:leq|le|geq|ge|leqslant|geqslant|lneq|gneq|lesssim|gtrsim|ll|gg)(?![A-Za-z])|<=|>=|\u2264|\u2265|(?<![<>=\\])<(?![<=])|(?<![<>=\\])>(?![>=])")),
    ("idx", re.compile(r"(?<![A-Za-z0-9])[a-zA-Z]\s*[+-]\s*1(?![0-9])|\{\s*[a-zA-Z]\s*[+-]\s*1\s*\}")),
    ("quant", re.compile(r"\\forall(?![A-Za-z])|\\exists(?![A-Za-z])|\bfor (all|every|each|some|any)\b|\bthere (exists?|is a|are)\b", re.I)),
    ("op", re.compile(r"\\(limsup|liminf|sup|inf|max|min|cup|cap|bigcup|bigcap|subset|supset|subseteq|supseteq|subsetneq)(?![A-Za-z])|\b(supremum|infimum|maximum|minimum|union|intersection)\b", re.I)),
    ("cite", re.compile(r"\b(Lemma|Theorem|Proposition|Corollary|Claim|Equation|inequality)\b\s*\(?(?:ref|eqref|cref|Cref)?\s*[A-Za-z:._-]*\)?|\bby\s+(?:the\s+)?[A-Z][a-zA-Z\u00e9\u00f6\u00f4'-]+(?:[- ][A-Z][a-zA-Z]+)?(?:'s)?\s+(?:inequality|theorem|lemma|formula|bound|principle)", re.I)),
    ("const", re.compile(r"\^\{?\s*-?[0-9]+(?:/[0-9]+)?\}?|\\frac\{\s*-?[0-9]+\s*\}\{\s*-?[0-9]+\s*\}|(?<![a-zA-Z_\\{])\b[0-9]+(?:\.[0-9]+)?\b(?![a-zA-Z}])")),
]
STEP_SPLIT_LINE = re.compile(r"(?m)^\s*(?:\[step\s+)?(\d{1,3})[.)\]]\s+")


class _Steps(object):
    """Step bodies as BLOCKS: from a numbered line to the next numbered line (a step's display math spans lines; the
    first version read only the first line and missed a quarter of the altered steps - measured 2026-09-07 over the
    whole bank as a syntax audit)."""
    @staticmethod
    def findall(text):
        ms = list(STEP_SPLIT_LINE.finditer(text))
        out = []
        for i, m in enumerate(ms):
            end = ms[i + 1].start() if i + 1 < len(ms) else len(text)
            body = text[m.end():end]
            # stop the last block at the prompt's trailing instructions
            cut = re.search(r"\n\s*(Return ONLY|BRAIN NOTES|Audit the numbered|If every step is valid)", body)
            if cut:
                body = body[:cut.start()]
            out.append((m.group(1), " ".join(body.split())))
        return out


STEP_SPLIT = _Steps()


CLAIM_RE = re.compile(r"(?s)CLAIM:\s*(.+?)\n\s*(?:ARGUMENT|PROOF|STEPS)\s*:")
LIMIT_PAIR = re.compile(r"([a-zA-Z])\s*(?:\\to|\\rightarrow|\\longrightarrow|->|→)\s*\{?\s*(\+?\\infty|\\infty|infty|0|-\\infty)")


def claim_line(text):
    """The claim's own regime and hypothesis words, so a step can be read against them: which variable goes where,
    and which hypothesis words the claim grants (a step that drops or weakens one changes the theorem)."""
    m = CLAIM_RE.search(text)
    if not m:
        return "", {}
    c = m.group(1)
    regime = {}
    for var, tgt in LIMIT_PAIR.findall(c):
        regime[var] = "infty" if "infty" in tgt else tgt
    hyps = []
    for h in INV[1][1].findall(c):
        h = h if isinstance(h, str) else next((x for x in h if x), "")
        h = h.lower()
        if h not in hyps:
            hyps.append(h)
    parts = []
    if regime:
        parts.append("regime:" + ",".join("%s->%s" % (k, v) for k, v in regime.items()))
    if hyps:
        parts.append("hyp:" + ",".join(hyps[:10]))
    return ("    claim: " + ("; ".join(parts) if parts else "(no limit regime or hypothesis words)")), regime


# v6b (2026-09-07): weaker member -> stronger members of a hypothesis pair. A step that uses the weaker word after the
# claim or an earlier step granted the stronger one is flagged (measured: on the altered step in 38 pct of fires,
# 12 pct of clean items carry a flag - a pointer, not a verdict).
WEAKER = [
    ("uncorrelated", ("independent",)), ("pairwise independent", ("independent",)), ("measurable", ("continuous",)),
    ("continuous", ("lipschitz", "uniformly continuous", "smooth")), ("bounded", ("uniformly bounded",)),
    ("pointwise", ("uniformly", "uniform")), ("in probability", ("almost surely", "a.s.")),
    ("in distribution", ("in probability", "almost surely")), ("closed", ("compact",)), ("finite", ("bounded",)),
    ("integrable", ("square-integrable", "bounded")), ("nonnegative", ("positive",)), ("non-negative", ("positive",)),
    ("nondecreasing", ("increasing",)), ("nonincreasing", ("decreasing",)), ("monotone", ("increasing", "decreasing")),
    ("local martingale", ("martingale",)), ("supermartingale", ("martingale",)), ("submartingale", ("martingale",)),
    ("for some", ("for all", "for every", "for each")), ("there exists", ("for all", "for every")),
]
_WRX = {w: re.compile(r"(?<![a-z-])" + re.escape(w) + r"(?![a-z-])", re.I) for w in
        set(w for w, _ in WEAKER) | set(x for _, xs in WEAKER for x in xs)}


def _weaker_flags(claim_text, steps):
    """[(step_num, weak, strong)] - the weaker word used in a step after the stronger was granted earlier."""
    granted = set()
    for _, xs in WEAKER:
        for x in xs:
            if _WRX[x].search(claim_text or ""):
                granted.add(x)
    out = []
    for num, body in steps:
        for weak, strongs in WEAKER:
            if _WRX[weak].search(body) and not any(_WRX[x].search(body) for x in strongs):
                g = [x for x in strongs if x in granted]
                if g:
                    out.append((num, weak, g[0]))
        for _, xs in WEAKER:
            for x in xs:
                if _WRX[x].search(body):
                    granted.add(x)
    return out


def step_inventory(text, max_chars=6000):
    # 13:1x 09-07: the cap was 1,600 chars and an 18-step proof lost its later steps - the altered step 13 of one bank
    # item was never listed. Every step must appear; the per-class value lists are capped instead (5 each).
    steps = STEP_SPLIT.findall(text)
    if len(steps) < 2:
        return ""
    cline, regime = claim_line(text)
    out = [cline] if cline else []
    cm = CLAIM_RE.search(text)
    weak_by_step = {}
    for num, weak, strong in _weaker_flags(cm.group(1) if cm else "", steps):
        weak_by_step.setdefault(num, []).append("'%s' where '%s' was granted" % (weak, strong))
    for num, body in steps:
        parts = []
        for name, rx in INV:
            hits = rx.findall(body)
            if not hits:
                continue
            if name == "rel":
                le = sum(1 for h in hits if any(c in h for c in ("le", "<", "\u2264", "lesssim")))
                ge = len(hits) - le
                parts.append("rel:%d<=,%d>=" % (le, ge))
            elif name in ("limit", "hyp", "idx", "op", "quant", "cite"):
                vals = []
                for h in hits:
                    h = h if isinstance(h, str) else next((x for x in h if x), "")
                    h = re.sub(r"\s+", " ", h).strip()
                    if h and h not in vals:
                        vals.append(h)
                parts.append("%s:%s" % (name, ",".join(vals[:5])))
            else:
                vals = []
                for h in hits:
                    h = h if isinstance(h, str) else next((x for x in h if x), "")
                    if h and h not in vals:
                        vals.append(h)
                if vals:
                    parts.append("%s:%s" % (name, ",".join(vals[:8])))
        # a limit target in this step that contradicts the claim's regime for the same variable is flagged, not judged
        flags = []
        for var, tgt in LIMIT_PAIR.findall(body):
            t = "infty" if "infty" in tgt else tgt
            if var in regime and regime[var] != t:
                flags.append("%s->%s here but %s->%s in the claim" % (var, t, var, regime[var]))
        if flags:
            parts.append("REGIME MISMATCH: " + "; ".join(flags[:2]))
        if num in weak_by_step:
            parts.append("WEAKER: uses " + "; ".join(weak_by_step[num][:2]))
        out.append("    step %s: %s" % (num, "; ".join(parts) if parts else "(prose only)"))
    block = "\n".join(out)
    if len(block) > max_chars:
        # v7 (2026-09-08): the old cut dropped the tail steps silently while the header promised every step (an
        # 80-step proof lost step 80). Keep whole step lines under the budget, then SAY which steps are not shown.
        kept, used = [], 0
        for line in out:
            if used + len(line) + 1 > max_chars - 200:
                break
            kept.append(line)
            used += len(line) + 1
        shown = len([l for l in kept if l.startswith("    step ")])
        total = len([l for l in out if l.startswith("    step ")])
        block = "\n".join(kept) + ("\n    INVENTORY INCOMPLETE: %d of %d steps shown for budget; steps after the last one "
                                   "listed are NOT inventoried - read them yourself with the same care." % (shown, total))
    return ("  - STEP INVENTORY (every step; tokens to inspect without assuming any is wrong - limit targets, hypothesis words, "
            "relation directions, off-by-one indices, quantifiers, paired operators, citations, constants/exponents; "
            "read each one against the claim and the previous step, do not skim it):\n" + block)


# v9 (2026-09-24, from a brain-on/off study): the card leads with what is specific to THIS argument. The hook
# dispatcher cuts every hook's text from the end when the prompt's hooks outrun the 10,000 characters the harness hands
# the model, and in all nine proof audits of the study where this card fired, the cut left only POSTURE's first ~1,500
# characters - general rules and the Lean certificate paragraph - and not one of the checks picked for the argument
# reached the model. Same words, new order: the posture's opening sentences, then the checks, the knowledge notes and
# the step inventory, then the rest of the posture as general rules. POSTURE itself is unchanged for its importers.
POSTURE_HEAD = (
    '[proof-sense] this is an argument under AUDIT, not an instruction list: do not copy its steps as clauses, '
    'test them. The argument may be correct, incomplete, or wrong in one or several steps: assume no error count '
    'and no mutation format. Be suspicious by default, but say CLEAN when every step re-derives; otherwise name '
    'the FIRST step you cannot re-derive from the claim and the steps before it, with the missing premise, the '
    'contradiction or the counterexample. A note from the math brain beside this is a claim to CHECK the step '
    'against for applicability, never evidence the step is right or wrong. The checks for this argument come '
    'first, the general rules last. (lane v9: specific first)'
)
POSTURE_REST = (
    '  - general rules: an example refutes a universal claim only if it meets ALL its hypotheses; passing sampled '
    'cases do not establish untested cases, while exhaustive certified verification can establish every case in a '
    'rigorously finite domain. Distinguish an invalid inference from an unfamiliar but valid one: a step is not '
    'wrong because it skips the theorem you expected. Use bounded, windowless machine checks only after fresh '
    'compute admission. Compiler acceptance alone is not a proof certificate. Require the exact named theorem and '
    'declaration kind, transitive dependencies limited to propext, Classical.choice and Quot.sound (reject sorryAx '
    'and other axioms), and evidence bound to exact source bytes and a trusted compiler/dependency environment. A '
    'review hash records the caller assertion; it does not independently establish review, and executable '
    'source/imports can spoof printed messages. A certificate concerns the formal target; separately check fidelity '
    'to the intended mathematics. If the checker or runtime cannot establish these conditions, the formal target '
    'remains UNVERIFIED. An unsolved goal, syntax error, missing import, timeout or resource hold leaves the intended '
    'claim UNRESOLVED; a failed tactic is not a counterexample. A step is refuted by a concrete counterexample or by '
    'proving its negation, never by a proof attempt that did not close.'
)


def render(text):
    if not is_math_argument(text):
        return ""
    lines = [POSTURE_HEAD]
    for n, cid, gut in pick_checks(text):
        lines.append("  - (%s, %d cues) %s" % (cid, n, gut))
    lines.append("    (hint: general audit instincts written from the classes of one-token error a proof can carry; "
                 "they say WHERE to look, not what is wrong - weigh each against the step in front of you)")
    lines.extend(knowledge_notes(text))
    inv = step_inventory(text)
    if inv:
        lines.append(inv)
    lines.append(POSTURE_REST)
    return "\n".join(lines)


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    prompt = (data.get("prompt") or "")[:20000]
    block = render(prompt)
    if not block:
        return
    if _recently_fired(str(data.get("session_id") or "")):
        return
    sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": block}}))


def _selftest():
    proof = ("CLAIM: $\\P(X \\ge t) \\le e^{-t^2/2}$.\nARGUMENT:\n1. By Chebyshev's inequality, $\\P(|S_n - np| \\geq d np) \\le \\exp(-d^2 np/2)$.\n"
             "2. Let $m$ be the largest integer with $\\rho N^{-m} \\ge \\delta$; then $\\rho N^{-m+1} < \\delta$.\n"
             "3. Since the $X_i$ are independent, $\\limsup_n \\mu_n(F) \\le \\mu(F)$ for closed $F$ as $n \\to \\infty$.\n"
             "4. Hence $\\sup_x f(x) \\le \\int f \\, d\\mu$ and the claim follows.")
    owner = "please only use the desktop app, not the api, and make exactly 3 charts of the book's pnl; do not deploy anything."
    chat = "how long will this take? and why did the brain do worse in round 1?"
    r = render(proof)
    assert r.startswith("[proof-sense]"), "positive control did not fire"
    ids = [cid for _, cid, _ in pick_checks(proof)]
    assert "named_result_hypotheses_direction_rate" in ids and len(ids) == MAX_CHECKS, ids
    assert render(owner) == "" and render(chat) == "", "negative control fired"
    print("proof_sense selftest: PASS (positive fired with %s; two negatives silent)" % ids)
    return 0


if __name__ == "__main__":
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # help is inert: no stdin read, no state file touched
        print("usage: proof_sense.py             (hook: reads the UserPromptSubmit JSON on stdin)\n"
              "       proof_sense.py --selftest  (one positive and two negative controls)")
        raise SystemExit(0)
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    try:
        main()
    except Exception:
        pass
