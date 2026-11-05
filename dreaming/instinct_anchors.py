#!/usr/bin/env python3
"""instinct_anchors.py - required-literal pre-filter for the reflex triggers.

WHY (measured 2026-09-14 02:2x, cProfile of one instinct_prime call): 0.78 of 0.85 s went to re.compile - every
hook process compiles all ~318 trigger regexes from scratch because a hook is a fresh interpreter and Python's
regex cache dies with it. Under render load that 0.8 s of CPU became the 7-18 s hook medians the slow-call log
recorded all afternoon. Most triggers cannot match most texts, and that is decidable WITHOUT compiling them:
a trigger's parse tree yields an "any-of" set of literal substrings such that every match of the pattern must
contain at least one of them. If none is in the text, the pattern is skipped; if the derivation finds no
requirement, the pattern is compiled exactly as before.

SOUNDNESS. The requirement is read from the top-level concatenation of the pattern: a run of LITERAL nodes is
a required substring; a BRANCH requires one of its branches' requirements (a branch with none means none);
a positive lookahead or lookbehind requires what its content requires, because that content matched a piece of the
text (since 2026-10-14: before, a trigger built as a conjunction of lookaheads had no anchor and ran on every text);
a repeat with min 0, a character class, a negative lookaround, an anchor, a group reference and '.' contribute nothing.
Of the requirements found on one concatenation the longest minimum literal is kept (any one of them is sound).
Literals are lowercased and the text is lowercased, because the hook matches with re.I. Two case-fold edges
(since 2026-10-14): a run of literals breaks at any non-ASCII character, because re.I pairs some non-ASCII letters
that str.lower() keeps apart (the two Greek sigmas, the micro sign and mu); and under re.I an ASCII letter also
matches three characters str.lower() does not lower to it - dotless i U+0131, long s U+017F, and capital dotted I
U+0130, which lowers to i plus the combining dot U+0307 - so could_match folds those (to i and s, the dot dropped)
before it looks. Before, a text spelled with them could be skipped where the pattern fired. Verified on the live
trigger table against a corpus of prompts and memory bodies before landing (see the memory named below);
the harness is `python instinct_anchors.py --verify`.

The sidecar `instinct_anchors.json` maps sha1(trigger) -> ["lit", ...] and is rebuilt by `build()` (called by
instincts.py after each compile, and safe to run by hand). A trigger absent from the sidecar is compiled as
before, so a stale sidecar can only cost speed, never a fire.

Memory: reflex_triggers_are_prefiltered_by_required_literals_2026_09_14
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import sys
import time

if any(a in ("-h", "--help") for a in sys.argv[1:]):
    # Discovery is not execution: this file does its work at module scope, so the
    # guard must come before the first statement that runs.
    print(__doc__ or os.path.basename(__file__))
    raise SystemExit(0)


try:
    from re import _parser as _sre_parser   # Python 3.11+
    from re import _constants as _C
except Exception:  # pragma: no cover - older interpreters
    import sre_parse as _sre_parser        # type: ignore
    import sre_constants as _C             # type: ignore

HERE = os.path.dirname(os.path.abspath(__file__))
TSV = os.path.join(HERE, "instincts.tsv")
SIDECAR = os.path.join(HERE, "instinct_anchors.json")
MAX_ANYOF = 64          # an alternation wider than this is not worth pre-checking; compile as before


def _key(trigger):
    return hashlib.sha1(trigger.encode("utf-8", "replace")).hexdigest()[:20]


def _better(a, b):
    """Keep the stronger requirement: larger minimum literal length, then fewer alternatives."""
    if a is None:
        return b
    if b is None:
        return a
    ka = (min(len(x) for x in a), -len(a))
    kb = (min(len(x) for x in b), -len(b))
    return a if ka >= kb else b


def _req_seq(seq):
    best = None
    cur = []
    for op, av in seq:
        if op is _C.LITERAL and av < 128:
            cur.append(chr(av).lower())
            continue
        if cur:
            best = _better(best, {"".join(cur)})
            cur = []
        r = _req_node(op, av)
        if r:
            best = _better(best, r)
    if cur:
        best = _better(best, {"".join(cur)})
    return best


def _req_node(op, av):
    if op is _C.SUBPATTERN:
        return _req_seq(av[3])
    if op is _C.BRANCH:
        out = set()
        for b in av[1]:
            r = _req_seq(b)
            if not r:
                return None
            out |= r
        return out if out and len(out) <= MAX_ANYOF else None
    if op in (_C.MAX_REPEAT, _C.MIN_REPEAT) or op is getattr(_C, "POSSESSIVE_REPEAT", object()):
        return _req_seq(av[2]) if av[0] >= 1 else None
    if op is getattr(_C, "ATOMIC_GROUP", object()):
        return _req_seq(av)
    if op is _C.ASSERT:
        # (?=X) or (?<=X): X matched somewhere in the text, so whatever X requires is in it (R29, 14 Oct 2026)
        return _req_seq(av[1])
    return None   # IN, ANY, AT, ASSERT_NOT, NOT_LITERAL, GROUPREF, CATEGORY, ...


def anchors_for(trigger):
    """Any-of literal set for one trigger, or None when no requirement can be read from it."""
    try:
        tree = _sre_parser.parse(trigger, re.I)
    except Exception:
        return None
    req = _req_seq(tree)
    if not req:
        return None
    req = {x for x in req if x}
    return sorted(req) if req and len(req) <= MAX_ANYOF else None


def build(tsv=TSV, out=SIDECAR):
    rows = []
    with io.open(tsv, encoding="utf-8", errors="replace", newline="") as f:
        rd = csv.DictReader(f, delimiter="\t", quoting=csv.QUOTE_NONE)
        for r in rd:
            t = (r.get("trigger") or "").strip()
            if t:
                rows.append(t)
    table, covered = {}, 0
    for t in rows:
        a = anchors_for(t)
        if a:
            table[_key(t)] = a
            covered += 1
    blob = {"schema": "instinct-anchors/v1", "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "triggers": len(rows), "with_anchor": covered, "anchors": table}
    tmp = "%s.tmp.%d" % (out, os.getpid())
    with io.open(tmp, "w", encoding="utf-8") as f:
        json.dump(blob, f)
    os.replace(tmp, out)
    return len(rows), covered


def load(path=SIDECAR):
    """{sha1(trigger): [lit, ...]} or {} - fail-open."""
    try:
        with io.open(path, encoding="utf-8") as f:
            blob = json.load(f)
        return blob.get("anchors") or {}
    except Exception:
        return {}


_FOLD_MAP = {0x131: "i", 0x17F: "s", 0x307: None}
_FOLD_CHARS = ("ı", "ſ", "̇")
_FOLD_LAST = [None, None]   # the last text folded and its fold: the hook asks about one text once per trigger


def _folded(low_text):
    """low_text with the three re.I partners of ASCII letters folded (see the docstring); the same object when it
    holds none of them. The map works character by character, so a literal found in low_text is found in the fold."""
    if low_text.isascii():
        return low_text
    if _FOLD_LAST[0] is low_text:
        return _FOLD_LAST[1]
    out = low_text
    for ch in _FOLD_CHARS:
        if ch in low_text:
            out = low_text.translate(_FOLD_MAP)
            break
    _FOLD_LAST[0], _FOLD_LAST[1] = low_text, out
    return out


def could_match(trigger, low_text, table):
    """False only when the table proves the trigger cannot match low_text (already lowercased)."""
    a = table.get(_key(trigger))
    if not a:
        return True
    low_text = _folded(low_text)
    for lit in a:
        if lit in low_text:
            return True
    return False


def _verify(sample=4000):
    """Soundness on the live table: whenever a trigger matches a text, the pre-check must have said 'could'."""
    import random
    random.seed(20260914)
    texts = []
    prompts = os.path.join(os.path.expanduser("~"), ".claude", "projects", "{{BRAIN_SLUG}}", "memory", "prompts", "PROMPTS.jsonl")
    try:
        with io.open(prompts, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        for ln in random.sample(lines, min(sample // 2, len(lines))):
            try:
                texts.append(str(json.loads(ln).get("prompt") or json.loads(ln).get("text") or "")[:8000])
            except Exception:
                texts.append(ln[:8000])
    except Exception:
        pass
    mem = os.path.join(os.path.expanduser("~"), ".claude", "projects", "{{BRAIN_SLUG}}", "memory")
    names = [n for n in os.listdir(mem) if n.endswith(".md")]
    for n in random.sample(names, min(sample // 2, len(names))):
        try:
            texts.append(io.open(os.path.join(mem, n), encoding="utf-8", errors="replace").read()[:8000])
        except Exception:
            pass
    texts = [t for t in texts if t]
    rows = []
    with io.open(TSV, encoding="utf-8", errors="replace", newline="") as f:
        for r in csv.DictReader(f, delimiter="\t", quoting=csv.QUOTE_NONE):
            t = (r.get("trigger") or "").strip()
            if t:
                rows.append(t)
    table = {_key(t): anchors_for(t) for t in rows if anchors_for(t)}
    unsound, matches, skipped, total = 0, 0, 0, 0
    t0 = time.time()
    for text in texts:
        low = text.lower()
        for t in rows:
            total += 1
            could = could_match(t, low, table)
            try:
                hit = bool(re.findall(t, text, re.I))
            except Exception:
                continue
            matches += hit
            skipped += (not could)
            if hit and not could:
                unsound += 1
                print("UNSOUND: trigger %r matched but pre-check skipped it (anchors %s)" % (t[:80], table.get(_key(t))))
    print("verify: %d texts x %d triggers = %d checks; matches %d; skipped by pre-check %d (%.1f%%); UNSOUND %d; %.0fs"
          % (len(texts), len(rows), total, matches, skipped, 100.0 * skipped / max(1, total), unsound, time.time() - t0))
    print("anchored triggers: %d of %d" % (len(table), len(rows)))
    return 1 if unsound else 0


if __name__ == "__main__":
    if "--verify" in sys.argv:
        sys.exit(_verify())
    n, c = build()
    print("instinct_anchors: %d triggers, %d with a required-literal anchor -> %s" % (n, c, SIDECAR))
