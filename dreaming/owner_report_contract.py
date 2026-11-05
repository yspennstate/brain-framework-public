"""Outgoing-prose contract: the partner-facing invariants a report must not break.

The mechanism, not the content. The private deployment this framework came from carries the
partner's own hard-won invariants here (the things a report to him must never assert); each
deployment fills in its own. What ships is the machinery and two invariants that hold anywhere:

1. ``settled_number_without_provenance`` - a money figure handed over as a settled result with
   no estimand and no source. A number that reaches the partner must say WHAT it measures and
   WHERE it comes from ("gross, from the replay ledger", "an upper bound", "measured on the
   2026-09 tape"); a bare "$123k per year" is an oracle a later reader cannot check.
2. ``financial_scope_incomplete`` - a total quoted over a set the same text says is missing
   members. If three of the legs are absent, the sum is understated by an unknown amount and
   stays understated until the missing members are reconciled; this code is PERSISTENT, so the
   guard keeps blocking until the report either drops the total or says the gap is closed.

Callers (``codex_stop_guard.py``, ``graph_fire/pre_report_check.py``) load this module by path
and use two names: ``check(text) -> {"code", "reason"} | {}`` and ``is_persistent_code(code)``.
Add a deployment's own invariants to ``INVARIANTS`` as (code, regex, reason) triples, or replace
the list from a sidecar file named by ``OWNER_REPORT_CONTRACT_FILE`` (one JSON object per line:
{"code": ..., "pattern": ..., "reason": ..., "persistent": false}). A regex that cannot compile
is skipped with a note on stderr rather than taking the guard down.

Run ``python owner_report_contract.py --selftest`` after any change; the controls below cover
both invariants in both directions.
"""
from __future__ import annotations

import io
import json
import os
import re
import sys

MAX_TEXT = 32 * 1024

_FENCED = re.compile(r"```.*?```", re.S)
_INLINE = re.compile(r"`[^`\n]{0,1000}`")
_BLOCKQUOTE = re.compile(r"(?m)^\s*>.*$")

# a money figure: $123k, $1.2M, 45,000 dollars, -$4,120
_MONEY = re.compile(
    r"(?:(?<![\w.])[+\-−]?\s*\$\s*\d[\d,]*(?:\.\d+)?\s*(?:[kKmMbB]\b)?"
    r"|\b\d[\d,]*(?:\.\d+)?\s*(?:k|m|million|thousand)?\s*dollars\b)")
# a settled framing around it: earns / makes / returns / P&L is / profit of ... per year
_SETTLED = re.compile(
    r"\b(?:earns?|makes?|returns?|yields?|nets?|profits?\s+of|p\s*&\s*l\s+(?:is|was|of)|pnl\s+(?:is|was|of)"
    r"|loses?|lost|gained?|made)\b", re.I)
# provenance or estimand markers that make a number checkable
_PROVENANCE = re.compile(
    r"\b(?:gross|net of|before fees|after fees|upper bound|lower bound|estimate[sd]?|approximat\w+|about|"
    r"roughly|measured|from the|per the|according to|source[sd]?:?|ledger|tape|replay|backtest|simulat\w+|"
    r"in[- ]sample|out[- ]of[- ]sample|window|as of \d|unverified|provisional|hypothetical|if\b|would\b)", re.I)
# a scope the text itself calls incomplete
_INCOMPLETE = re.compile(
    r"\b(?:incomplete|missing|absent|not (?:yet )?reconciled|unreconciled|omits?|omitted|excludes?|"
    r"not included|understated|overstated|partial(?:ly)?)\b", re.I)
_TOTAL_WORDS = re.compile(r"\b(?:total|sum|p\s*&\s*l|pnl|net|overall|combined|in all|altogether)\b", re.I)

INVARIANTS = [
    # (code, compiled regex or None for a coded check, reason, persistent)
]
PERSISTENT_CODES = frozenset({"financial_scope_incomplete"})


def _sidecar_invariants():
    path = os.environ.get("OWNER_REPORT_CONTRACT_FILE", "")
    if not path or not os.path.isfile(path):
        return []
    out = []
    try:
        with io.open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                row = json.loads(line)
                try:
                    pat = re.compile(str(row["pattern"]), re.I)
                except re.error as exc:
                    sys.stderr.write("owner_report_contract: skipped %r (%s)\n" % (row.get("code"), exc))
                    continue
                out.append((str(row["code"]), pat, str(row.get("reason") or row["code"]),
                            bool(row.get("persistent"))))
    except Exception as exc:
        sys.stderr.write("owner_report_contract: sidecar unreadable (%s)\n" % exc)
    return out


def _prose(text: str) -> str:
    text = text[:MAX_TEXT]
    text = _FENCED.sub(" ", text)
    text = _INLINE.sub(" ", text)
    text = _BLOCKQUOTE.sub(" ", text)
    return text


def _sentences(text: str):
    for s in re.split(r"(?<=[.!?])\s+|\n{2,}", text):
        s = s.strip()
        if s:
            yield s


def check(text: str) -> dict:
    """Return {"code", "reason"} for the first invariant the outgoing text breaks, else {}."""
    if not isinstance(text, str) or not text.strip():
        return {}
    prose = _prose(text)
    persistent_extra = {}
    for code, pat, reason, persistent in _sidecar_invariants() + [
            (c, p, r, c in PERSISTENT_CODES) for c, p, r in INVARIANTS if p is not None]:
        if pat.search(prose):
            if persistent:
                persistent_extra[code] = True
            return {"code": code, "reason": reason}
    for sent in _sentences(prose):
        if _INCOMPLETE.search(sent) and _MONEY.search(sent) and _TOTAL_WORDS.search(sent):
            return {"code": "financial_scope_incomplete",
                    "reason": ("The report quotes a total over a set it says is incomplete; the figure is "
                               "understated by an unknown amount until the missing members are reconciled. "
                               "Drop the total or state that the gap is closed. "
                               "[owner_report_contract: financial_scope_incomplete]")}
    for sent in _sentences(prose):
        if _MONEY.search(sent) and _SETTLED.search(sent) and not _PROVENANCE.search(sent):
            return {"code": "settled_number_without_provenance",
                    "reason": ("A money figure is handed over as a settled result with no estimand and no "
                               "source. Say what it measures (gross or net, bound or estimate) and where it "
                               "comes from before it reaches the partner. "
                               "[owner_report_contract: settled_number_without_provenance]")}
    return {}


def is_persistent_code(code: str) -> bool:
    if code in PERSISTENT_CODES:
        return True
    return any(c == code and persistent for c, _p, _r, persistent in _sidecar_invariants())


def selftest() -> int:
    cases = [
        ("The pipeline earns roughly $123k per year on the replay ledger.", ""),
        ("The pipeline earns $123k per year.", "settled_number_without_provenance"),
        ("The strategy made $40,000 last month.", "settled_number_without_provenance"),
        ("Gross of fees the strategy made about $40,000 last month, measured on the September tape.", ""),
        ("Account 7 P&L is incomplete; 3 legs are missing, so -$4,120 is understated.",
         "financial_scope_incomplete"),
        ("The total is $9,100 and every leg is reconciled.", ""),
        ("No numbers here, only a plan.", ""),
        ("```\nearns $5k per year\n```", ""),
    ]
    bad = 0
    for text, want in cases:
        got = check(text).get("code", "")
        ok = got == want
        bad += 0 if ok else 1
        print("%s  %-36s %s" % ("ok  " if ok else "FAIL", want or "(silent)", text[:70]))
    print("persistent:", is_persistent_code("financial_scope_incomplete"),
          is_persistent_code("settled_number_without_provenance"))
    print("selftest %s" % ("PASS" if not bad else "FAIL (%d)" % bad))
    return 0 if not bad else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv[1:]:
        sys.exit(selftest())
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        print(__doc__)
        sys.exit(0)
    data = sys.stdin.read()
    print(json.dumps(check(data)))
