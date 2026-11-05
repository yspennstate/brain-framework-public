#!/usr/bin/env python3
"""pre_report.py - fire the gut on the OUTGOING message, before a wrong number/claim reaches the owner.

The hooks see tool I/O, never the assistant's prose - so the single worst class (a flattering/undershoot
number reported to the owner, an over-claimed 'done') had no firing surface. The correction this was
built from was exactly that shape: a replay reported at a yearly rate roughly an order of magnitude
under what the lived ledger showed, with sim_flatters AND risk_vs_lived_record both silent because the
number lived in prose. reality_monitor.py already proved a Stop hook can read the last assistant message and
block; this adds the number-vs-lived-book check that reality_monitor doesn't do.

Two signals, combined:
  1. GRAPH FIRE (gfcore): the outgoing text is graph-near a sim/risk scar (fires sim_sign_vs_lived_nav /
     sim_flatters / risk_vs_measured_record, plus the deployment's own rule reflexes).
  2. LIVED-ANCHOR MISMATCH: a reported $ figure (with a period) contradicts one of the stated anchor numbers
     by >2x in EITHER direction, when the text names that anchor's subject. Undershoot is as damning as
     flatter - 'a sim that disagrees with the measured record in either direction is lying' (MISTAKES.md 07-11).

check() returns the fires + a should_block flag. Used by the Stop hook and the shadow eval. Fail-safe.
"""
import os, io, json, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ANCHORS = os.path.join(HERE, "lived_anchors.json")

# a money figure: either $N[suffix] (suffix optional when $ is present), OR N+REQUIRED-scale-suffix with no
# $ ('180k', '180 thousand', '1.8M') - agents write dollar amounts without the sign and those must not
# evade the lived-book check. A BARE number with neither $ nor a scale suffix ('180', '85%') never matches.
_MONEY = re.compile(r"\$\s*([\d][\d,]*(?:\.\d+)?)\s*(k|thousand|m|mn|mm|million|b|bn|billion|grand)?"
                    r"|(?<![\w.$])([\d][\d,]*(?:\.\d+)?)\s*(k|thousand|m|mn|mm|million|bn|billion|grand)\b", re.I)
_MULT = {"k": 1e3, "thousand": 1e3, "grand": 1e3, "m": 1e6, "mn": 1e6, "mm": 1e6, "million": 1e6,
         "b": 1e9, "bn": 1e9, "billion": 1e9}
_PERIODS = [
    ("year", re.compile(r"(per year|/ ?yr|/ ?year|a year|annual|per annum|yearly)", re.I)),
    ("month", re.compile(r"(per month|/ ?mo\b|a month|monthly|/ ?month)", re.I)),
    ("day", re.compile(r"(per day|/ ?day|a day|daily)", re.I)),
]
# the number must be ASSERTED as a P&L figure near the reported value (real analysis prose MENTIONS
# ledger figures and data sizes constantly; only a headline assertion 'the pipeline EARNS $X/yr' should block).
_ASSERT = re.compile(r"\b(earns?|earned|makes?|made|nets?|netted|generat\w+|profit|p\s?&\s?l|pnl|yields?|"
                     r"brings? in|rakes? in|pulls? in|took? in|(?:comes?|came) to|total(?:s|ed|led)?|adds? up to|"
                     r"book (makes|earns))\b", re.I)
_HEDGE = re.compile(r"\b(should|might|may|probably|likely|i think|i believe|appears?|seems?|roughly|about|"
                    r"approximately|estimate|ballpark|order of|not sure|unverified|assuming|if .* correct|"
                    r"need to (check|verify|reconcile)|haven'?t (verified|checked|reconciled))\b", re.I)
# a SOURCE marker: the figure names where it was read from (a ledger/observer/file/command), or is flagged
# plainly as unverified. Generous by design - any plausible provenance phrase passes; the unsourced-assert
# branch must only catch a bare asserted number with no provenance at all (fail open on ambiguity).
_SRC = re.compile(r"\b(sourced?[d]?\s*(:|from)|per (the|his|its|that)\b|from (the|that|its) (\w+[ -]){0,2}"
                  r"(ledger|log|tape|file|run|db|feed|journal|account|statement|observer|engine|export|card|cache|output)|"
                  r"as measured|measured (on|from|by|in|over)|verified against|reconciled? (against|with|to)|"
                  r"read (from|off|out of)|according to|reported by|logged (in|by)|the (output|result) (of|shows?)|"
                  r"data incomplete|\bledger says|\btape shows|\bshows?\b[^.]{0,40}\$)", re.I)
# a concrete artifact reference doubles as provenance: a windows/posix path, a *.py/md/json/tsv/jsonl/log
# name, or a `backticked` artifact near the prose.
_ARTIFACT = re.compile(r"[A-Za-z]:\\[\w\\.\-]+|/[\w./\-]+\.(?:py|md|json|tsv|jsonl|log|csv|npz)\b|"
                       r"\b[\w.\-]+\.(?:py|md|json|tsv|jsonl|log|csv|npz)\b|`[^`]{3,60}`")
# a strong COMPLETION claim about an artifact the reader will open - the he_will_look class. reality_monitor.py
# owns 'it works/deployed/tests pass'; this covers the sibling vocabulary it doesn't (finalized, populated,
# removed, shipped). Deliberately NOT bare 'done' (too common); needs a completion verb.
_DONE = re.compile(r"\b(finali[sz]ed|populated|(is|it'?s|we'?re|are|all) done|removed|deleted|completed|"
                   r"shipped|ready to ship|good to go|all set|fully (working|implemented|populated)|"
                   r"exhibits? (are )?(in|populated|filled)|everything is (in|there|populated))\b", re.I)

_ANCHORS_CACHE = None


def _s(t):
    """Coerce any input to a safe string - the Stop path must never throw on odd input."""
    return t if isinstance(t, str) else ("" if t is None else str(t))


def _load_anchors():
    global _ANCHORS_CACHE
    if _ANCHORS_CACHE is None:
        try:
            d = json.load(io.open(ANCHORS, encoding="utf-8"))
            out = []
            for a in d.get("anchors", []):
                # A remembered observation is not a present-tense oracle. Remembered magnitudes from older
                # runs get retired as their frames change, and some turn out contaminated. Hard blocking is opt-in only, so a copied or newly added number cannot become
                # live merely by existing in this file.
                if a.get("blocking_oracle") is not True:
                    continue
                for pk, period in (("value_usd_per_year", "year"), ("value_usd_per_month", "month"),
                                   ("value_usd_per_day", "day")):
                    if pk in a:
                        v = a[pk]
                        lo, hi = (v if isinstance(v, list) else [v, v])
                        out.append({"keys": [k.lower() for k in a.get("match", [])],
                                    "period": period, "lo": float(lo), "hi": float(hi),
                                    "src": a.get("source", ""), "key": a.get("key", "")})
                        break
            _ANCHORS_CACHE = out
        except Exception:
            _ANCHORS_CACHE = []
    return _ANCHORS_CACHE


def _to_usd(num, unit):
    try:
        v = float(num.replace(",", ""))
        return v * _MULT.get((unit or "").lower(), 1.0)
    except Exception:
        return None


def _find_money(text):
    """List of (usd, period) reported figures. period is 'year'/'month'/'day' or None."""
    out = []
    try:
        t = _s(text)
        for m in _MONEY.finditer(t):
            num = m.group(1) or m.group(3)   # $-branch or suffix-branch
            unit = m.group(2) or m.group(4)
            usd = _to_usd(num, unit)
            if usd is None or usd < 1000:  # ignore tiny/plainly-non-book numbers
                continue
            # the period must be ATTACHED to the number - an asserted RATE ('205k/year', '180k per year',
            # '18k a day') - not a loose 'year' 40 chars away, which is a mention in analysis prose (the
            # 0.75%-false-block source). The recorded undershoot was an attached rate, '~$N-Nk/year'.
            fwd = t[m.end():m.end() + 10]
            period = None
            for pname, pre in _PERIODS:
                if pre.search(fwd):
                    period = pname
                    break
            out.append((usd, period, m.start()))
    except Exception:
        pass
    return out


def anchor_mismatches(text):
    """Reported figures contradicting an explicitly promoted blocking oracle. Fail-safe -> []."""
    flags = []
    try:
        tl = _s(text).lower()
        money = _find_money(text)
        if not money:
            return []
        for a in _load_anchors():
            for usd, period, pos in money:
                if period != a["period"]:
                    continue
                # the anchor SUBJECT (its match words) must be NEAR the number, not just somewhere in a long
                # message - that separates 'the pipeline ... $Nk/year' (a real undershoot) from analysis prose
                # that names the subject once and a different number far away (the false-block source).
                if not any(k in tl[max(0, pos - 80):pos + 45] for k in a["keys"]):
                    continue
                # >4x gap, not 2x: real analysis prose discusses measured numbers at 2-3x sub-quantities all the
                # time (a 7yr total, a sub-period); only an EGREGIOUS miss (a 10x undershoot) should
                # block. The attached-rate period detection (above) already gates out loose mentions.
                if usd < 0.25 * a["lo"] or usd > 4.0 * a["hi"]:
                    direction = "UNDER" if usd < a["lo"] else "OVER"
                    flags.append({
                        "reported": usd, "anchor_lo": a["lo"], "anchor_hi": a["hi"],
                        "period": period, "direction": direction, "anchor": a["key"] or a["src"],
                    })
    except Exception:
        pass
    # dedup identical
    seen, out = set(), []
    for f in flags:
        k = (round(f["reported"]), f["anchor"], f["direction"])
        if k not in seen:
            seen.add(k)
            out.append(f)
    return out


# result context: a statistic only contradicts the measured record if it is about the system being measured.
# Set BRAIN_RESULT_CONTEXT to a regex alternation naming your own system; the default is generic.
_RESULT_WORDS = os.environ.get("BRAIN_RESULT_CONTEXT") or (
    r"book|strateg(?:y|ies)|portfolio|leverag|backtest|pipeline|the (system|model|account|book|strategy)")
_BOOKCTX = re.compile(r"\b(" + _RESULT_WORDS + r")\b", re.I)
# the worst drawdown the measured record has actually seen, in percent (BRAIN_LIVED_WORST_DD)
try:
    _LIVED_WORST_DD = float(os.environ.get("BRAIN_LIVED_WORST_DD") or 25)
except ValueError:
    _LIVED_WORST_DD = 25.0
_LOSS_SIGN = re.compile(r"\b(los(e|es|ing|s)\b|negative return|unprofitable|loses? money|in the red|"
                        r"bleeding money|down every (year|month)|lost money (every|each))\b", re.I)
_DD_CTX = re.compile(r"draw.?down|drop|loss|lose|decline|crash|ruin|wipe|blow.?up|down\b", re.I)


def stat_sanity(text):
    """Structural sanity of reported STATISTICS against the measured record - the classes beyond raw $ that
    get corrected most: a drawdown far worse than the worst ever measured, a P&L SIGN contradicting a
    profitable record, an implausible Sharpe. Gated on result context so a generic stats discussion
    doesn't fire. Fail-safe."""
    flags = []
    try:
        t = _s(text)
        if not _BOOKCTX.search(t):
            return []
        tl = t.lower()
        # drawdown: a STRONG drawdown phrase within ~28 chars of a figure at least ~3.6x the worst measured
        # drawdown (several times the configured worst): the naive worst-case-scaling error. The tight
        # phrase+proximity+magnitude keeps ordinary '30% of X' analysis prose from tripping it.
        _ddp = r"draw.?down|max.?dd|\bmaxdd\b|drew down|draws? down|drawn down|blow.?up|wiped? out|near.?ruin"
        for m in re.finditer(r"(\d{2,3})(?:\s*(?:to|-)\s*(\d{2,3}))?\s*%", t):
            val = max(int(m.group(1)), int(m.group(2)) if m.group(2) else int(m.group(1)))
            near = tl[max(0, m.start() - 28):m.end() + 8]
            if val >= max(40, round(_LIVED_WORST_DD * 40 / 11)) and re.search(_ddp, near):
                flags.append({"kind": "drawdown", "reported_pct": val, "lived_worst_pct": _LIVED_WORST_DD,
                              "reflex": "risk_vs_measured_record"})
                break
        # sign contradiction: the measured, profitable system reported LOSING. Tight (real analysis prose mentions
        # backtests + losses constantly): needs an explicit money-loss AND a recurring temporal AND
        # attribution to the measured system ('the/current/our' + a result word) - all three, or it
        # false-fires on ordinary discussion.
        if (re.search(r"los(e|es|ing)\s+money|unprofitable|negative (return|pnl|p&l)|loses? (money )?(every|each)|"
                      r"lost money (every|each)", tl)
                and re.search(r"every (single )?year|each year|annually|every month", tl)
                and re.search(r"\b(the|current|our)\s+(" + _RESULT_WORDS + r")\b", tl)):
            flags.append({"kind": "sign", "reflex": "sim_sign_vs_lived_nav"})
        # implausible Sharpe
        m = re.search(r"sharpe\D{0,10}(\d+(?:\.\d+)?)", tl)
        if m:
            try:
                if float(m.group(1)) >= 5:
                    flags.append({"kind": "sharpe", "reported": float(m.group(1)), "reflex": "sim_flatters"})
            except Exception:
                pass
    except Exception:
        return []
    return flags


# Deployment rules: recommendations this deployment has learned are always wrong for it. Each rule is a
# regex for the recommended action, an optional negation regex that clears it ('never skip the tests'), an
# optional 'near' regex that must appear close to the match, a gate ('recommend' = a recommendation verb
# must sit right before the match) and a block policy ('always' blocks even when hedged, 'unhedged' blocks
# only an unhedged claim, 'never' only surfaces the reflex). Rules live in strategy_rules.json beside this
# module, or at the path in BRAIN_STRATEGY_RULES; with no file there are no rules.
# strategy_rules.example.json shows the format with one generic rule.
_REC = re.compile(r"\b(should|recommend|consider|could|suggest|propos|better to|worth|the (play|move|idea|"
                  r"answer) is|i'?d|we could|you could|instead\b)", re.I)
RULES_PATH = os.environ.get("BRAIN_STRATEGY_RULES") or os.path.join(HERE, "strategy_rules.json")
RULES_EXAMPLE = os.path.join(HERE, "strategy_rules.example.json")


def _load_rules(path):
    try:
        raw = json.load(io.open(path, encoding="utf-8"))
    except Exception:
        return []
    rules = []
    for r in raw if isinstance(raw, list) else []:
        try:
            rules.append({
                "kind": str(r["kind"]), "reflex": str(r.get("reflex") or r["kind"]),
                "pattern": re.compile(r["pattern"], re.I),
                "negation": re.compile(r["negation"], re.I) if r.get("negation") else None,
                "near": re.compile(r["near"], re.I) if r.get("near") else None,
                "before": int(r.get("near_before", 35)), "after": int(r.get("near_after", 55)),
                "gate": r.get("gate", "recommend"), "block": r.get("block", "never")})
        except Exception:
            continue
    return rules


_RULES = _load_rules(RULES_PATH)


def strategy_sanity(text):
    """Fire each deployment rule whose action appears in the text: behind a recommendation verb when
    the rule is gated, not cleared by its negation, with its 'near' words close by when it has them.
    Fail-safe -> []."""
    try:
        t = _s(text)
        flags = []
        for r in _RULES:
            m = r["pattern"].search(t)
            if not m:
                continue
            if r["negation"] and r["negation"].search(t):
                continue
            # the recommendation verb must sit RIGHT BEFORE the match ('you should ...'), not anywhere in a
            # long analysis message that happens to contain both words.
            if r["gate"] == "recommend" and not _REC.search(t[max(0, m.start() - 42):m.start() + 3]):
                continue
            if r["near"] and not r["near"].search(t[max(0, m.start() - r["before"]):m.end() + r["after"]]):
                continue
            flags.append({"kind": r["kind"], "reflex": r["reflex"], "block": r["block"],
                          "span": m.group(0)[:40]})
        return flags
    except Exception:
        return []


# mention-vs-act gate (REFEREE_discuss_gate_proposal.md): prose that DISCUSSES / attributes / retracts a wrong
# number ("we should NOT report X", "earlier I wrongly said X", "the old memory claimed X - retracted",
# "the correction: X is wrong") is NOT a fresh wrong report and must not block the number/drawdown/sign
# branches. Mistake-detection and correction turns are full of exactly this prose - blocking it is the
# ERN's own law (a guard that fires on correct discussion trains the reader to click past it). Errs toward
# gating: a blocker should fail OPEN on ambiguity. Deployment rules keep their own negation regex, so they are not gated.
_DISCUSS = re.compile(
    r"\b(do|does|should|would|will|could|must)\s+not\s+(report|say|claim|present|tell|state)\b"
    r"|\b(don'?t|doesn'?t|shouldn'?t|wouldn'?t|won'?t|can'?t|couldn'?t|never)\s+(report|say|claim|present|tell|state)\b"
    r"|\b(wrongly|incorrectly|mistakenly|falsely)\s+(said|reported|claimed|stated|told)\b"
    r"|\b(the old memory|old anchor|earlier i|previously i|i wrongly|i earlier|i had (said|claimed|reported))\b"
    r"|\bretracted\b|\bwithdrawn\b|\bignore that\b|\bundershot\b|\bundershoot"
    r"|\bverified and cleared\b|\b(the )?(independent )?verifier (reproduced|confirms?|matched|checked)\b|"
    r"\breproduced every number\b"  # SPECIFIC verification-outcome phrasing (the number is being CHECKED) - narrow, not broad 'reconciled'/'reproduced', which the real undershoot's re-audit context also uses
    r"|\bthat('?s| was)?\s+(the|a|my|an)\s+[\w ]{0,20}(error|mistake)\b"
    r"|\b(his|the|a|my|your)\s+[\w'\- ]{0,20}correction\b|\bcorrection:\b"
    r"|\b(was|were|is|are)\s+wrong\b|\bnow (retracted|withdrawn|corrected|superseded)\b|\bflattering[- ]sim\b",
    re.I)


def check(text, session=None):
    """Combine graph-fire + lived-anchor mismatch + statistic/strategy sanity on outgoing prose. Fail-safe."""
    text = _s(text)
    try:
        sys.path.insert(0, HERE)
        import gfcore
        # Stop/prose surface fires once a turn on high-stakes output -> more sensitive than PreToolUse.
        gf = gfcore.graph_fire(text, session, thr_mult=0.55)
    except Exception:
        gf = []
    try:
        mism = anchor_mismatches(text)
    except Exception:
        mism = []
    SIMRISK = {"sim_flatters", "risk_vs_measured_record", "sim_sign_vs_lived_nav"} | {r["reflex"] for r in _RULES}
    strong = [f for f in gf if f.get("id") in SIMRISK]
    hedged = bool(_HEDGE.search(text or ""))
    money = _find_money(text)
    has_num = bool(money)
    # unsourced ASSERTED figure (2026-07-28, the owner's 'check the live feed before you report' class):
    # a money figure with an assertion verb attached ('the pipeline MAKES $274k') and NO provenance anywhere in
    # the message - no source phrase, no artifact/path reference, not hedged. The Stop hook combines this
    # with its observed-this-turn flag; here we only compute the text-side condition. Fail open: any source
    # marker or artifact mention anywhere in the message clears the whole flag.
    unsourced_assert = False
    try:
        if money and not hedged and not _SRC.search(text) and not _ARTIFACT.search(text):
            for _usd, _period, _pos in money:
                if _ASSERT.search(text[max(0, _pos - 60):_pos + 30]):
                    unsourced_assert = True
                    break
    except Exception:
        unsourced_assert = False
    # completion-claim path (the he_will_look class the graph tokens are too weak for): fire the reflex so
    # the Stop hook can nudge 'open the artifact the reader will see before signing off'. Grounding/blocking is the
    # Stop hook's job (it has the transcript); here we only surface the reflex.
    if _DONE.search(text) and not hedged and not any(f.get("id") == "he_will_look" for f in gf):
        gf = list(gf) + [{"id": "he_will_look", "score": None, "charge": 1.0, "src": "MISTAKES",
                          "validity": "high", "mode": "fire", "via": "completion_claim"}]
    # block when the number provably contradicts the measured record, OR a strong sim/risk reflex fired on an
    # unhedged reported number. Hedged ('roughly', 'need to reconcile') never blocks - it's honest.
    try:
        stat = (stat_sanity(text) or []) + (strategy_sanity(text) or [])
    except Exception:
        stat = []
    for sflag in stat:
        rid = sflag.get("reflex")
        if rid and not any(f.get("id") == rid for f in gf):
            gf = list(gf) + [{"id": rid, "score": None, "charge": 1.0, "src": "MISTAKES",
                              "validity": "low", "mode": "fire", "via": "stat_sanity:" + sflag["kind"]}]
    # mention-vs-act suppression DISABLED (REFEREE_discuss_gate_proposal.md) - it broke the guard's
    # INVARIANT (a wrong number never reaches the reader unblocked). This vocabulary gate opened a CONFIRMED
    # 3/5 false-negative hole: a FRESH wrong report carrying 'withdrawn / old memory / retracted / was wrong'
    # passed UNBLOCKED (referee_mention_fix_proto.py ADVERSARIAL dropped 5/5 -> 2/5). You cannot vocabulary-
    # discriminate mention-from-act - the two share surface. A missed wrong number defeats the guard's whole
    # purpose; the mention false-block it prevented is rare (0.03% on 13k real msgs) and recoverable. So
    # block-on-mism is the safe default. `discuss` stays computed for the returned dict/logging; it does NOT
    # suppress. Regression gate: referee_mention_fix_proto.py must show ADVERSARIAL 5/5 + ACT 5/5.
    # (Was: `if discuss: mism=[]; stat=[rule flags only]`.)
    discuss = bool(_DISCUSS.search(text))
    # a rule with block 'always' is a directional recommendation error (wrong even when hedged - 'you
    # might skip the tests' is still the wrong move); drawdown/sign and 'unhedged' rules respect the hedge.
    strat_block = [s for s in stat if s.get("block") == "always"]
    stat_block = [s for s in stat if s["kind"] in ("drawdown", "sign") or s.get("block") == "unhedged"]
    # BLOCK only on PROVABLE issues (a number contradicting the measured record, a bad drawdown/sign, a deployment-rule
    # claim) - NOT on 'a sim reflex fired near a number', which blocks CORRECT in-range numbers too. The sim
    # reflex still surfaces via graph_fires; it just doesn't force a block without a provable contradiction.
    should_block = bool(mism) or bool(strat_block) or (bool(stat_block) and not hedged)
    return {"graph_fires": gf, "mismatches": mism, "stat": stat, "strong": [f["id"] for f in strong],
            "hedged": hedged, "has_number": has_num, "discuss": discuss, "should_block": should_block,
            "unsourced_assert": unsourced_assert}


def _selftest():
    global _RULES
    if not _RULES:
        _RULES = _load_rules(RULES_EXAMPLE)
    cases = [
        ("Running the pipeline backtest on the sample tape it earns roughly $180k per year.", False, "stale magnitude is not an oracle"),
        ("The pipeline backtest makes about $1.9M per year, in line with the record.", False, "recent magnitude is not an oracle"),
        ("The current strategy loses money every year.", True, "lived-sign contradiction"),
        ("You should skip the tests and ship it tonight.", True, "deployment rule (example file)"),
        ("We never skip the tests before a release.", False, "rule negation clears it"),
        ("I read the module; it defines three helper functions.", False, "benign observation"),
        ("", False, "empty"),
    ]
    for txt, expect_block, note in cases:
        r = check(txt)
        print("  block=%-5s (want %s) [%-16s] fires=%s mism=%d :: %s"
              % (r["should_block"], expect_block, note, [f["id"] for f in r["graph_fires"][:3]],
                 len(r["mismatches"]), (txt[:40] + "...") if txt else "(empty)"))
    # unsourced-assert branch (2026-07-28): text-side condition only; the Stop hook adds observed-this-turn
    ua_cases = [
        ("The pipeline made $274k this month.", True, "bare asserted figure, no provenance"),
        ("The pipeline made $274k this month, from the sales ledger.", False, "sourced"),
        ("The pipeline made roughly $274k this month.", False, "hedged is honest"),
        ("revenue_report.py shows the pipeline made $274k this month.", False, "artifact named"),
        ("The backtest CAGR file has 3,000 rows and the fee is $12k per row of overhead.", False, "no assert verb near figure"),
        ("Costs came to $84k for the quarter.", True, "asserted total, no provenance"),
    ]
    bad = 0
    for txt, want, note in ua_cases:
        r = check(txt)
        got = bool(r.get("unsourced_assert"))
        flag = "ok" if got == want else "WRONG"
        bad += (got != want)
        print("  unsourced=%-5s (want %-5s) %-6s [%s] :: %s" % (got, want, flag, note, txt[:52]))
    if bad:
        print("unsourced-assert selftest FAILED (%d)" % bad)
        return 1
    for junk in [None, 123, {"x": 1}]:
        try:
            check(junk)
        except Exception as e:
            print("  FAIL-SAFE BREACH:", e)
            return 1
    print("pre_report selftest OK (fail-safe held)")
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
