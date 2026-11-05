#!/usr/bin/env python3
"""report_guards.py - four ADDITIONAL provable-contradiction block classes for the report surface.

STAGED (improve_2026_07_12): consumed by pre_report_check.py via a patch-spec; does NOT modify
pre_report.py, so the referee_mention_fix_proto gate (ACT 5/5 + ADVERSARIAL 5/5) is untouched by
construction. All detectors are STRUCTURAL (assertion shape + anchor-subject adjacency + an
external ground bit), never discussion-vocabulary excuses - the mention-vs-act referee showed
vocabulary discrimination opens false-negative holes, so none is used here.

Classes (mined from MISTAKES.md recurrence, ranked):
  A stale_dead_assert   - PRESENT-TENSE negative-existence/brokenness claim about a NAMED artifact
                          (adjacent identifier), unhedged, in a turn with NO tool observation
                          (observed=False from the Stop hook). The uncaught 07-12 pattern (3x in one
                          session: 'the prevent-guard is unbuilt' etc.) + 06-16 'impossible after one
                          wall' + 07-11 black-screenshot 'impossible' + 06-09 stale-baseline assert.
                          The ground bit is STRUCTURAL: you may declare a thing dead in a turn where
                          you actually LOOKED; only a look-free declaration blocks.
  B settled_while_pending - a settled conclusion/number co-present with a 'verifier still pending'
                          marker in the SAME message: an internal contradiction, provable from the
                          text alone (07-11 HIGH carry: answered $57-77k/mo before the adversarial
                          worker returned; hedges do NOT excuse - answering-before-the-check IS the sin).
  C unmeasured_regime_timing - 'IV/VIX/vol is low/high' state assertion + act-now recommendation with
                          NO measurement token (percentile/z/number) adjacent (07-11 'long-term IV is
                          low, load the hedge now' when it was actually rich; 07-02 'you said it is high').
  D implausible_stat    - a book-context statistic outside any lived range asserted as a finding:
                          Sharpe >= 5 (lived 0.5-2.7) or std >= 300% (06-20/21 'this table is wrong':
                          Sharpe 7.67, 950.7% std). Upgrades the existing surface-only sharpe flag to
                          a block, still book-gated and hedge-respecting.

Fail-safe: every public function returns [] on ANY error. stdlib only. check_extra(text, observed)
is the single entry point for the Stop hook.
"""
import os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))

# reuse pre_report's hedge/money/book-context so semantics stay in sync; fall back local if absent
try:
    sys.path.insert(0, HERE)
    sys.path.insert(0, os.path.dirname(HERE))  # staged: pre_report lives one level up
    import pre_report as _pr
    _HEDGE = _pr._HEDGE
    _BOOKCTX = _pr._BOOKCTX
    _find_money = _pr._find_money
    _ASSERT = _pr._ASSERT
except Exception:
    _HEDGE = re.compile(r"\b(should|might|may|probably|likely|i think|appears?|seems?|roughly|about|"
                        r"approximately|estimate|not sure|unverified|assuming)\b", re.I)
    _BOOKCTX = re.compile(r"\b(book|strateg|grid|portfolio|leverag|backtest)\b", re.I)
    _ASSERT = re.compile(r"\b(earns?|makes?|nets?|comes? to|totals?)\b", re.I)

    def _find_money(_t):
        return []


def _s(t):
    return t if isinstance(t, str) else ("" if t is None else str(t))


# ---------------------------------------------------------------- A: stale_dead_assert
# present-tense negative-existence / brokenness predicate (assertive, not past narrative).
# Two tiers, measured on the 2500-message real-prose corpus: STRONG diagnosis predicates take any
# named artifact; WEAK ones (gone/missing/empty/absent - the repair-completion / file-observation
# false-hit source, 9+ corpus hits) additionally need a RUNNABLE-system subject.
_DEAD_STRONG = re.compile(
    r"\b(is|are)\s+(still\s+|completely\s+|totally\s+)?"
    r"(broken|dead|unreachable|orphaned|unbuilt|unimplemented|unwired|"
    r"not\s+(running|live|ingesting|firing|wired|implemented|built|installed|registered)|impossible)\b"
    r"|\b(is|are)\s+down\b(?!\s*[\d~$.+-])"  # 'the daemon is down', never 'ABC is down 3%'
    r"|\b(does\s+not|doesn'?t)\s+(exist|run|fire|ingest)\b"
    r"|\bnever\s+(ran|fired|ingested|existed)\b"
    r"|\bno\s+longer\s+(runs?|exists?|fires?|writes?)\b"
    r"|\bnothing\s+is\s+(running|listening|writing|ingesting)\b"
    r"|\bwas\s+never\s+(implemented|built|created|wired)\b"
    r"|\bcan(?:not|'?t)\s+be\s+(done|captured|reached|accessed)\b",
    re.I)
_DEAD_WEAK = re.compile(r"\b(is|are)\s+(still\s+)?(gone|missing|empty|absent)\b", re.I)
_SYSNOUN = re.compile(r"\b(hook|daemon|process|service|builder|pipeline|watcher|sentinel|listener|"
                      r"worker|cron|scheduled\s+task|unit|timer|queue|database|table|index|runtime|"
                      r"ingest\w*|registry|server|socket|port)\b", re.I)
# post-change completion shape ('are dead now', 'is gone now') is a fresh-change claim (the
# he_will_look completion class, already owned elsewhere), not a stale-read diagnosis; and an
# 'X doesn't exist yet' marks known-new (about to create), not stale absence.
_POSTCHANGE = re.compile(r"\b(now|anymore|no\s+more)\b", re.I)
_YET = re.compile(r"^\s*,?\s*yet\b", re.I)
# the claim must be about a CONCRETE NAMED artifact adjacent to the predicate (the anchor-subject
# rule): a file with an extension, a path, a backticked span, a snake_case identifier, or a system
# noun naming a runnable/checkable thing. Abstract talk ('that is impossible') never matches.
_ARTIFACT = re.compile(
    r"[\w>-]+\.(?:py|ps1|psm1|json|jsonl|md|tsv|csv|sqlite3?|log|txt|ya?ml|toml|vbs|ts|js|html|pdf|exe)\b"
    r"|[A-Za-z]:[/\\]|~/|`[^`]+`|\b\w+_\w+\b"
    r"|\b(hook|daemon|task|process|service|port|cron|watcher|sentinel|module|function|script|pid|"
    r"endpoint|route|branch|repo|database|queue|worker|listener|guard|reflex|builder|pipeline|"
    r"scheduled\s+task|unit|timer|table|index)\b", re.I)
# class-specific hedges beyond _HEDGE: an explicitly-stale or suspicion frame is honest
_STALE_HEDGE = re.compile(r"\b(looks?\s+like|last\s+(time|read|check)|when\s+i\s+last|as\s+of\b|stale|"
                          r"suspect|hypothes|guess|haven'?t\s+(re)?(checked|read)|unread|reconstruct|"
                          r"if\s+it|might\s+be|may\s+be|presumably)\b", re.I)
# conditional / interrogative frame in the short pre-window (the subject sits between the frame word
# and the predicate, so no end-anchor) is not an assertion
_COND = re.compile(r"\b(if|whether|when|unless|in\s+case|check(ing)?\s+(that|if|whether)|verify(ing)?\s+"
                   r"(that|if|whether)|assum\w+|suppose|would\s+be|could\s+be|until)\b", re.I)


def stale_dead_assert(text, observed=None):
    """Block-class A. Fires ONLY when observed is False (the Stop hook KNOWS no tool ran this turn).
    observed None/True -> []. Fail-safe -> []."""
    try:
        if observed is not False:
            return []
        t = _s(text)
        if not t or _HEDGE.search(t) or _STALE_HEDGE.search(t):
            return []
        cands = [(m, False) for m in _DEAD_STRONG.finditer(t)] + \
                [(m, True) for m in _DEAD_WEAK.finditer(t)]
        for m, weak in cands:
            # not a question: the sentence containing the predicate must not end with '?'
            tail = t[m.end():m.end() + 90]
            stopch = re.search(r"[.!?\n]", tail)
            if stopch and stopch.group(0) == "?":
                continue
            # post-change completion ('are dead now') / known-new ('doesn't exist yet') are other classes
            if _POSTCHANGE.search(t[max(0, m.start() - 16):m.end() + 16]) or _YET.match(t[m.end():m.end() + 8]):
                continue
            # not conditional: no if/whether/when/check-that frame immediately before the clause
            pre = t[max(0, m.start() - 30):m.start()]
            if _COND.search(pre):
                continue
            # anchor subject adjacent: a named artifact within the clause window around the predicate;
            # weak predicates (gone/missing/empty/absent) need a RUNNABLE-system subject specifically
            win = t[max(0, m.start() - 80):m.end() + 40]
            if not _ARTIFACT.search(win):
                continue
            if weak and not _SYSNOUN.search(t[max(0, m.start() - 60):m.start() + 10]):
                continue
            return [{
                "kind": "stale_dead_assert", "reflex": "first_wall_is_not_impossibility",
                "span": m.group(0)[:48],
                "reason": ("[pre-report] You're declaring something broken/dead/missing ('%s') in a turn "
                           "with NO verifying observation - this is the stale-read assert class (3x on "
                           "07-12 alone: 'the prevent-guard is unbuilt' when relaunch.ps1 already had it). "
                           "Re-check the CURRENT state (one Read/Bash on the actual target) before "
                           "asserting absence, or say plainly the read is stale." % m.group(0)[:48]),
            }]
        return []
    except Exception:
        return []


# ---------------------------------------------------------------- B: settled_while_pending
# B is intentionally conservative: a status word near a conclusion is not enough.  The verifier
# must have an active/future predicate, the conclusion must be affirmative, and both must bind in
# one hard prose scope.  This avoids replayed false hits from quotes, detector discussions,
# attribution, resolved checks, and unrelated audits.
_VERIFIER = (
    r"(?:verifier|verification|adversarial\s+(?:worker|check|audit)|"
    r"audit(?:or|\s+team)?|reconciliation|re-?check|cross-?check|"
    r"validator|validation(?:\s+run)?|checker|review)"
)
_ACTIVE_PENDING = re.compile(
    rf"\b(?:(?:the|an?|my|our|his|her|their)\s+)?"
    rf"(?:(?P<modifier>[a-z][\w-]{{2,}})\s+)?(?P<actor>{_VERIFIER})\b"
    r"(?P<target_before>\s+(?:of|for|on)\s+[^.;!?\n]{1,48}?)?\s*"
    r"(?P<state>"
    r"(?:is|are|remains?)\s+(?:still\s+)?(?:pending|running|outstanding|in\s+flight)"
    r"|(?:is|are)\s+not\s+yet\s+(?:back|done|finished|complete)"
    r"|(?:has|have)n['’]?t\s+(?:returned|finished|completed|come\s+back)"
    r"|(?:has|have)\s+not\s+yet\s+(?:returned|finished|completed|come\s+back)"
    r"|(?:is|are)\s+yet\s+to\s+(?:return|finish|land)"
    r"|(?:is|are)\s+(?:still\s+)?(?:checking|validating|auditing|reviewing)"
    r")"
    r"(?P<target_after>\s+(?:it|this|that|the\s+(?:answer|number|figure|result|verdict|"
    r"recommendation|decision|report|work|diagnosis)))?",
    re.I,
)
_FUTURE_PENDING = re.compile(
    r"\b(?P<actor>i|we)\s+(?:still\s+)?"
    r"(?P<state>will|must|need\s+to|have\s+to|plan\s+to|intend\s+to|have\s+yet\s+to)\s+"
    r"(?P<verb>verify|confirm|validate|audit|re-?check|cross-?check|check|reconcile)\b"
    r"(?P<target>[^.;!?\n]{0,72})",
    re.I,
)
_BEFORE_PENDING = re.compile(
    rf"\b(?P<state>before|while)\s+(?:the\s+)?(?P<actor>{_VERIFIER})\b"
    r"(?P<target>[^.;!?\n]{0,48}?)\s+"
    r"(?:returns?|finishes?|lands?|comes?\s+back|runs?|is\s+(?:done|complete|running))\b",
    re.I,
)
_FINAL_CLAIM = re.compile(
    r"\b(?:so\s+)?(?:the\s+)?(?:final\s+)?(?:answer|number|saving|cost|figure|result|"
    r"verdict|recommendation|decision)"
    r"(?:\s+(?:for|on)\s+[^:;.!?]{1,45})?\s*(?:is|:|comes\s+to)\s+\S|"
    r"\b(?:bottom\s+line|conclusion)\s*:\s*\S",
    re.I,
)
_ACTION_CLAIM = re.compile(
    r"\b(?:i|we)\s+recommend\b|\bgo\s+with\b|\bthe\s+(?:play|move|choice)\s+is\b",
    re.I,
)
_QUESTION_START = re.compile(
    r"^\s*(?:what|which|who|why|how|is|are|should|could|would|will|do|does|can)\b",
    re.I,
)
_HYPOTHETICAL_START = re.compile(r"^\s*(?:if|whether|suppose|supposing|assuming|unless)\b", re.I)
_NONFINAL_CLAIM = re.compile(
    r"\b(?:provisional|tentative|preliminary|unknown|tbd|to\s+be\s+determined|"
    r"not\s+(?:yet\s+)?final|no\s+final\s+answer|subject\s+to|conditional\s+on|"
    r"awaiting\s+(?:the\s+)?(?:audit|verification|check))\b",
    re.I,
)
_REFUSED_CLAIM = re.compile(
    r"\b(?:do\s+not|don['’]?t|will\s+not|won['’]?t|cannot|can['’]?t)\s+"
    r"(?:\w+\s+){0,3}?(?:say|state|claim|report|provide|give|hand\s+over|settle|"
    r"recommend|choose|call)\b",
    re.I,
)
_ATTRIBUTED_CLAIM = re.compile(
    r"\b(?:(?!(?:i|we)\b)[a-z][\w-]{1,24}|the\s+(?:model|agent|memo|report|paper)|"
    r"(?:his|her|their)\s+(?:memo|report))\s+"
    r"(?:claims?|claimed|says?|said|reported|states?|stated|argues?|argued)\b",
    re.I,
)
_HISTORICAL_CLAIM = re.compile(
    r"\b(?:earlier|previously|formerly|yesterday|last\s+(?:time|week|turn))\b"
    r"[^.;!?\n]{0,42}\b(?:i\s+)?(?:said|reported|stated|claimed|gave)\b|"
    r"\b(?:retract|retracted|withdraw|withdrew|was\s+wrong|incorrectly)\b",
    re.I,
)
_META_SCOPE = re.compile(
    r"\b(?:this|the)\s+(?:detector|guard|rule|fixture|test|example|definition|pattern)\b|"
    r"\b(?:detector|guard|test|fixture|example)\s+(?:should|would|must|did|does)\s+"
    r"(?:flag|match|catch|reject|accept|fire)\b|"
    r"\b(?:adversarial|regression)\s+(?:test|fixture|evaluation|case)\b|"
    r"\b(?:reported\s+speech|quoted\s+(?:example|trigger)|synthetic\s+trigger)\b",
    re.I,
)
_BINDS_PENDING = re.compile(
    r"\b(?:it|this|that)\b|"
    r"\b(?:this|the|my|our)\s+(?:answer|number|figure|verdict|conclusion|report|work|"
    r"result|diagnosis|recommendation|decision)\b|"
    r"\b(?:answer|number|figure|verdict|conclusion|report|result)\s+(?:above|below)\b",
    re.I,
)
_NON_PROSE = re.compile(
    r"(?s:```.*?```)|`[^`\n]{0,1000}`|(?m:^\s*>[^\n]*(?:\n|$))|"
    r"\"[^\"\n]{0,800}\"|“[^”\n]{0,800}”"
)
_HARD_BOUNDARY = re.compile(r"^\s*(?:#{1,6}\s+|[-*+]\s+|\d{1,3}[.)]\s+|\|)")
_CLAUSE_BREAK = re.compile(r"\s*;\s*|\s*,\s*(?=(?:but|while|although|however)\b)|"
                           r"\s+(?=(?:but|while|although|however)\b)", re.I)
_SCOPE_NOISE = frozenset(
    "a an and are as at audit auditing auditor back before bottom but check checker checking choice "
    "complete conclusion confirm confirmation cross decision done final figure finished flight for "
    "from go has have in into is it later line move my need number of on our outstanding pending plan "
    "play recheck reconcile reconciliation recommend recommendation report result return returned review "
    "running still that the their them then this tomorrow validate validating validation validator verdict "
    "verification verifier verify we while will with work worker workers yet adversarial".split()
)


def _hard_scopes(text):
    """Keep prose paragraphs together, but never bind across headings, bullets, or table rows."""
    out, current = [], []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            if current:
                out.append(" ".join(current))
                current = []
            continue
        if _HARD_BOUNDARY.match(raw):
            if current:
                out.append(" ".join(current))
                current = []
            out.append(_HARD_BOUNDARY.sub("", raw, count=1).strip())
        else:
            current.append(line)
    if current:
        out.append(" ".join(current))
    return [s for s in out if s]


def _sentences(scope):
    # A decimal point inside a reported figure is not a sentence boundary.  Splitting there used
    # to separate the assertion verb from its money field in the canonical pending-verifier miss.
    out, start = [], 0
    for m in re.finditer(r"(?<!\d)[.!?]+(?!\d)", scope):
        piece = scope[start:m.end()].strip()
        if piece:
            out.append(piece)
        start = m.end()
    tail = scope[start:].strip()
    if tail:
        out.append(tail)
    return out


def _claim_clause(fragment, sentence):
    if not fragment or sentence.rstrip().endswith("?") or _QUESTION_START.search(fragment):
        return False
    matches = list(_FINAL_CLAIM.finditer(fragment)) + list(_ACTION_CLAIM.finditer(fragment))
    try:
        for _usd, _period, pos in (_find_money(fragment) or []):
            if _ASSERT.search(fragment[max(0, pos - 60):pos + 18]):
                matches.append(type("_Pos", (), {"start": lambda self, p=pos: p})())
    except Exception:
        pass
    for match in matches:
        prefix = fragment[:match.start()]
        local = fragment[max(0, match.start() - 100):]
        if (_HYPOTHETICAL_START.search(prefix) or _NONFINAL_CLAIM.search(local)
                or _REFUSED_CLAIM.search(local) or _ATTRIBUTED_CLAIM.search(prefix)
                or _HISTORICAL_CLAIM.search(local)):
            continue
        return True
    return False


def _scope_tokens(text):
    return {w for w in re.findall(r"[a-z][a-z0-9_-]{2,}", text.lower()) if w not in _SCOPE_NOISE}


def _pending_records(sentence):
    records = []
    for m in _ACTIVE_PENDING.finditer(sentence):
        target = " ".join(x or "" for x in (
            m.group("modifier"), m.group("target_before"), m.group("target_after")
        ))
        records.append({"kind": "active", "actor": m.group("actor").lower(),
                        "target": target, "backref": bool(_BINDS_PENDING.search(target))})
    for pattern in (_FUTURE_PENDING, _BEFORE_PENDING):
        for m in pattern.finditer(sentence):
            target = m.groupdict().get("target") or ""
            records.append({"kind": "future" if pattern is _FUTURE_PENDING else "before",
                            "actor": m.group("actor").lower(), "target": target,
                            "backref": bool(_BINDS_PENDING.search(target))})
    return records


def _scope_bound(pending, claim):
    if pending["backref"]:
        return True
    pt = _scope_tokens(pending["target"])
    ct = _scope_tokens(claim)
    # An unqualified local verifier inherits the adjacent claim.  A named competing target must
    # overlap; e.g. a documentation/payroll audit does not bind a release/portfolio conclusion.
    return not pt or bool(pt & ct)


def settled_while_pending(text, observed=None):
    """Flag an affirmative settled answer while its verifier is active or promised for later.

    This remains fail-safe and deliberately abstains on quoted/meta/reported, hypothetical,
    provisional, resolved, refused, cross-paragraph, and disjoint-target cases.
    """
    try:
        t = _NON_PROSE.sub(" ", _s(text))
        if not t:
            return []
        global_pending, global_claims = [], []
        has_meta_scope = False
        for scope_i, scope in enumerate(_hard_scopes(t)):
            if _META_SCOPE.search(scope):
                has_meta_scope = True
                continue
            sentences = _sentences(scope)
            pending = [(i, rec) for i, sentence in enumerate(sentences)
                       for rec in _pending_records(sentence)]
            claims = []
            for i, sentence in enumerate(sentences):
                for fragment in _CLAUSE_BREAK.split(sentence):
                    fragment = fragment.strip()
                    if _claim_clause(fragment, sentence):
                        claims.append((i, fragment))
            global_pending.extend((scope_i, i, rec) for i, rec in pending)
            global_claims.extend((scope_i, i, claim) for i, claim in claims)
            for pi, prec in pending:
                for ci, claim in claims:
                    if abs(pi - ci) <= 1 and _scope_bound(prec, claim):
                        return [{
                            "kind": "settled_while_pending",
                            "reflex": "verification_binds_the_answer",
                            "span": "bound verifier still pending",
                            "reason": (
                                "[verification_binds_the_answer] This settles a claim while a verifier "
                                "bound to that same claim is active or promised for later. Wait for it, "
                                "or report only the open status."
                            ),
                        }]
        # A leading generic verifier/adversarial-worker status is a response-wide banner.  The
        # canonical miss put that banner first, then settled figures under later headings.  Do not
        # generalize this to a bare audit (which may concern another paragraph), a named target, a
        # trailing status, or any message containing detector/test meta-discussion.
        if not has_meta_scope:
            for ps, _pi, prec in global_pending:
                actor = prec.get("actor", "")
                global_actor = actor == "verifier" or actor.startswith("adversarial ")
                if (ps != 0 or prec.get("kind") != "active" or not global_actor
                        or _scope_tokens(prec.get("target", ""))):
                    continue
                if any(cs > ps for cs, _ci, _claim in global_claims):
                    return [{
                        "kind": "settled_while_pending",
                        "reflex": "verification_binds_the_answer",
                        "span": "bound verifier still pending",
                        "reason": (
                            "[verification_binds_the_answer] This settles a claim while a verifier "
                            "bound to that same claim is active or promised for later. Wait for it, "
                            "or report only the open status."
                        ),
                    }]
        return []
    except Exception:
        return []


# ---------------------------------------------------------------- C: unmeasured_regime_timing
_REGIME = re.compile(
    r"\b(vix|vxn|iv|implied\s+vol\w*|vol(?:atility)?|term\s+structure|skew|premium|carry)\b"
    r"[^.!?\n]{0,25}\b(is|are|looks?|sits?)\s+(really\s+|very\s+|quite\s+|extremely\s+|historically\s+)?"
    r"(low|high|cheap|rich|expensive|elevated|depressed)\b", re.I)
_ACT_NOW = re.compile(r"\b(load(\s+up)?|buy|enter|add|roll|lock\s+in|now\s+is\s+the\s+time|time\s+to|"
                      r"do\s+it\s+now|act\s+now)\b", re.I)
_MEASURED = re.compile(r"\bp\s?\d{1,3}\b|percentile|z-?score|\btrailing\b|full-?history|"
                       r"\d+(\.\d+)?\s*%|\bat\s+\d+(\.\d+)?\b|\bvs\.?\s+\d", re.I)


def unmeasured_regime_timing(text, observed=None):
    """Block-class C: a NOW-state vol-regime assertion driving an act-now recommendation with no
    measurement token adjacent. 07-11: 'long-term IV is low, load the cheap hedge' when long-dated IV was
    p66 full-history / p100 trailing (RICH) - the regime was inferred, never measured. Fail-safe -> []."""
    try:
        t = _s(text)
        if not t:
            return []
        m = _REGIME.search(t)
        if not m:
            return []
        # measured adjacent (percentile / z / an explicit number) -> honest, pass
        if _MEASURED.search(t[max(0, m.start() - 110):m.end() + 110]):
            return []
        # needs an act-now recommendation within the neighborhood (state chatter alone never blocks)
        if not _ACT_NOW.search(t[max(0, m.start() - 60):m.end() + 140]):
            return []
        if _HEDGE.search(t):
            return []
        return [{
            "kind": "unmeasured_regime_timing", "reflex": "verify_current_value_before_timing_advice",
            "span": m.group(0)[:48],
            "reason": ("[pre-report] You're asserting a vol regime ('%s') and recommending acting NOW "
                       "without a measured percentile adjacent. 07-11: 'long-term IV is low, load ~1y "
                       "puts' - the long-dated vol was actually rich on its own history, and the index "
                       "was the wrong one. Measure the RIGHT variable's current percentile first, then advise."
                       % m.group(0)[:48]),
        }]
        return []
    except Exception:
        return []


# ---------------------------------------------------------------- D: implausible_stat
# (?!...) range guard: 'Sharpe 5-9' / '300 to 900%' is a RANGE DESCRIPTION (e.g. prose describing a
# detector's thresholds - a real transcript false positive), not a reported statistic; a reported
# finding is a point value ('Sharpe 7.67').
_SHARPE = re.compile(r"sharpe\D{0,10}(\d+(?:\.\d+)?)(?!\s*(?:-|–|to\s)\s*\d)", re.I)
_BIGSTD = re.compile(r"\b(std|stdev|standard\s+deviation|annuali[sz]ed\s+vol\w*)\b\D{0,12}"
                     r"(\d{3,4})(?:\.\d+)?\s*%(?!\s*(?:-|–|to\s)\s*\d)"
                     r"|(?<![\d.-])()(\d{3,4})(?:\.\d+)?\s*%\s*(?:std|stdev|vol\b)", re.I)


def implausible_stat(text, observed=None):
    """Block-class D: a book-context statistic no measured range contains, asserted as a finding:
    Sharpe >= 5 (his configs live 0.5-2.7) or std >= 300% (the 06-21 'this table is wrong' shipped
    Sharpe 7.67 + 950.7% std). Book-gated + hedge-respecting. Fail-safe -> []."""
    try:
        t = _s(text)
        if not t or not _BOOKCTX.search(t) or _HEDGE.search(t):
            return []
        out = []
        m = _SHARPE.search(t)
        if m:
            try:
                v = float(m.group(1))
                if v >= 5:
                    out.append({
                        "kind": "implausible_stat", "reflex": "sim_flatters", "stat": "sharpe", "value": v,
                        "reason": ("[pre-report] You're reporting Sharpe %.2f on his book - outside any "
                                   "measured range (his configs live ~0.5-2.7; the 06-21 table he called "
                                   "wrong shipped Sharpe 7.67). A statistic no lived range contains is "
                                   "YOUR pipeline failing - recompute/reconcile before reporting." % v),
                    })
            except Exception:
                pass
        m = _BIGSTD.search(t)
        if m:
            try:
                v = float(m.group(2) or m.group(4))  # noun-first branch vs %-first branch
                if v >= 300:
                    out.append({
                        "kind": "implausible_stat", "reflex": "sim_flatters", "stat": "std", "value": v,
                        "reason": ("[pre-report] You're reporting a %.0f%% std/vol on his book - an "
                                   "outside-any-measured-range statistic (the 06-21 wrong table shipped "
                                   "950.7%% std). Sanity-check the units/annualization before reporting." % v),
                    })
            except Exception:
                pass
        return out
    except Exception:
        return []


# ---------------------------------------------------------------- entry point
def check_extra(text, observed=None, hedged=None):
    """All four classes; list of flags (possibly empty), most-provable first. Fail-safe -> []."""
    try:
        flags = []
        flags += settled_while_pending(text, observed)   # internal contradiction: most provable
        flags += stale_dead_assert(text, observed)       # observed-gated
        flags += implausible_stat(text, observed)
        flags += unmeasured_regime_timing(text, observed)
        return flags
    except Exception:
        return []


def _selftest():
    # recorded REAL claim texts (quoted in memories/MISTAKES.md), not authored: A must fire on the
    # 07-12 stale-read wording; B on the 07-11 pending-answer shape; C on the 07-11 IV-low wording.
    pos = [
        ("the duplicate-PREVENT guard is unbuilt - hand it to 0c44dd79", False, "stale_dead_assert"),
        ("The error_graph builder never fired; error_graph_runtime is empty.", False, "stale_dead_assert"),
        ("So the saving is $61k a month; the adversarial worker is still running.", None, "settled_while_pending"),
        ("long-term IV is low, load the cheap hedge now", None, "unmeasured_regime_timing"),
        ("The symmetric grid backtest comes out at Sharpe 7.67 with 950.7% std.", None, "implausible_stat"),
    ]
    neg = [
        ("I ran ls: the file is missing from the staging dir.", True, "observed turn -> no A"),
        ("Checking whether the daemon is dead before restarting it.", False, "conditional -> no A"),
        ("Is the watcher process dead?", False, "question -> no A"),
        ("It looks like the builder never fired - let me verify.", False, "hedged -> no A"),
        ("The audit is still pending; I will not hand over a number until it returns.", None, "no settled -> no B"),
        ("long-dated IV sits at p66 full-history - rich, not low; do not add the hedge yet.", None, "measured -> no C"),
        ("His validated config runs Sharpe 1.35 on the walk-forward.", None, "in-range -> no D"),
        ("", False, "empty"),
    ]
    b_pos = [
        "The final answer is option blue; the audit is still pending.",
        "Bottom line: choose the blue path. The adversarial check is still running.",
        "Conclusion: ship the release, but verification is not yet complete.",
        "The verdict is accept; the validator hasn't returned.",
        "Go with the blue plan; I still need to verify it.",
        "I recommend switching to the blue plan; I will confirm it tomorrow.",
        "The answer is option blue. The audit of option blue remains pending.",
        "So the cost is the lower quote; reconciliation is in flight.",
        "The play is use the blue configuration while the re-check is running.",
        "Final decision: deploy. We plan to audit this after handoff.",
        "The number is the ledger total; I will cross-check the number later.",
        "The final answer is proceed before the verifier returns.",
        "I recommend not shipping; the audit is still pending.",
        "Ignoring the old memo, final answer: ship; the verifier is still running.",
        "The lint check passed, but the adversarial audit is still pending; final answer: ship.",
        "Final answer: ship; the verifier is checking it.",
    ]
    b_neg = [
        "The audit is pending; I will not hand over an answer until it returns.",
        "Is the final answer option blue while the audit is pending?",
        "If the final answer is option blue while the audit is pending, that would be premature.",
        'He wrote "The final answer is option blue; the audit is pending."',
        "`The final answer is option blue; the audit is pending.`",
        "> The final answer is option blue; the audit is pending.\nA separate note follows.",
        "This detector should flag the following example: final answer: blue; audit is pending.",
        "The audit was still pending yesterday; the final answer is option blue now.",
        "The audit is no longer pending; the final answer is option blue.",
        "The audit has completed, and the final answer is option blue.",
        "The provisional answer is option blue; the audit is pending.",
        "The answer is unknown; the audit is pending.",
        "The final answer for release is go; the documentation audit is pending.",
        "The final answer is option blue.\n\nThe payroll audit is pending.",
        "- The final answer is option blue.\n- The audit is pending.",
        "# Release\nFinal answer: option blue.\n# Payroll\nThe audit is pending.",
        "Earlier I said the final answer is option blue while the audit was pending.",
        "Alice claims the final answer is option blue; her audit is pending.",
        "I retract the final answer; the audit is pending.",
        "The final answer is option blue; I verified it yesterday.",
        "The answer is option blue, subject to the audit result.",
        "The answer is not yet final; the audit is pending.",
        "The final answer is option blue; I will confirm the appointment tomorrow.",
        "Before the audit returns, do not say the final answer is option blue.",
        "The final answer is option blue; the payroll audit is pending.",
        "The final answer after the audit returns will be option blue.",
        "The final answer is option blue; a data download is still running.",
        "The verifier checks every release; final answer: option blue.",
        "Verification of an outstanding theorem is discussed; conclusion: the proof is sound.",
        "The adversarial fixture says final answer: option blue while an audit is pending.",
    ]
    bad = 0
    for txt, obs, want in pos:
        got = [f["kind"] for f in check_extra(txt, observed=obs)]
        ok = want in got
        bad += (not ok)
        print("  POS %-28s -> %-24s %s" % (want, ",".join(got) or "-", "" if ok else "<-MISS"))
    for txt, obs, note in neg:
        got = [f["kind"] for f in check_extra(txt, observed=obs)]
        bad += bool(got)
        print("  NEG %-28s -> %-24s %s" % (note[:28], ",".join(got) or "-", "" if not got else "<-FALSE"))
    for i, txt in enumerate(b_pos, 1):
        ok = bool(settled_while_pending(txt))
        bad += (not ok)
        print("  B+%02d -> %s" % (i, "PASS" if ok else "MISS"))
    for i, txt in enumerate(b_neg, 1):
        ok = not settled_while_pending(txt)
        bad += (not ok)
        print("  B-%02d -> %s" % (i, "PASS" if ok else "FALSE"))
    reason = (settled_while_pending(b_pos[0]) or [{}])[0].get("reason", "")
    numeric_reason = bool(re.search(r"\$\s*\d|\b\d+(?:\.\d+)?\b", reason))
    bad += numeric_reason
    print("  B reason has no numeric oracle -> %s" % ("PASS" if not numeric_reason else "FALSE"))
    for junk in [None, 123, {"x": 1}, ["y"]]:
        try:
            check_extra(junk, observed=False)
        except Exception as e:
            print("  FAIL-SAFE BREACH:", e)
            return 1
    print("report_guards selftest:", "OK" if bad == 0 else "%d misses" % bad)
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(_selftest())
