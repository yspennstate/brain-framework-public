#!/usr/bin/env python3
"""eval_domain2.py - does admitting two-letter domain terms to the index earn default-on?

Same shape and the same discipline as eval_deliberate_spellfix.py, which decided the 07-24 flag:
derive queries from slugs, measure hit@10 over many targets, print an explicit PASS/FAIL gate.

What it compares: two shadow indexes built from ONE corpus at ONE moment, one with the live
[a-z0-9_]{3,} tokeniser and one with {mv,iv,dd,ib} additionally admitted. Each arm is scored with
its OWN tokeniser, because BM25.score keeps only tokens already in self.df - scoring a fixed index
with the current tokeniser would drop "mv" from the query and show no effect.

Note vs the spellfix harness: its words_of() keeps only len(w) > 3, which would throw away the exact
terms under test. Here the domain terms are kept and everything else follows the same rule.

GATE - the change earns default-on iff BOTH:
  (a) hit@10 rises on targets whose slug carries a two-letter domain term, and
  (b) hit@10 does not fall on control targets whose slug carries none.
"""
import os
import re
import sys

if any(a in ("-h", "--help") for a in sys.argv[1:]):
    # Discovery is not execution: this file does its work at module scope, so the
    # guard must come before the first statement that runs.
    print(__doc__ or os.path.basename(__file__))
    raise SystemExit(0)


sys.path.insert(0, os.path.join(os.path.expanduser("~"), ".claude", "dreaming"))
import bm25  # noqa: E402

import tempfile
SCRATCH = os.path.join(tempfile.gettempdir(), "brain_domain2_shadow")
os.path.isdir(SCRATCH) or os.makedirs(SCRATCH)
# Both arm filenames carry a fingerprint of (whitelist, corpus), so the harness can never compare a
# cached arm against a fresh one. Two failure modes this closes: changing DOMAIN2 and silently
# reusing a shadow built from the old set, and - the one that actually bit me while measuring this -
# comparing indexes built minutes apart on a corpus that grew in between, which mixes corpus drift
# into what is supposed to be a tokeniser-only difference. hashlib, not hash(): str hashing is
# randomised per process, so hash() would change the name every run and never reuse anything.
def _fingerprint():
    import hashlib
    h = hashlib.sha1(",".join(sorted(bm25.DOMAIN2)).encode())
    try:
        names = sorted(f for f in os.listdir(bm25.MEM) if f.endswith(".md"))
        h.update(("%d" % len(names)).encode())
        h.update(("%.0f" % max(os.path.getmtime(os.path.join(bm25.MEM, f)) for f in names)).encode())
    except OSError:
        pass
    return h.hexdigest()[:10]


_FP = _fingerprint()
A = os.path.join(SCRATCH, "shadow_current_%s.json.gz" % _FP)
B = os.path.join(SCRATCH, "shadow_fixed_%s.json.gz" % _FP)

# imported, never copied - the eval and the tokeniser must not drift apart
DOMAIN2 = bm25.DOMAIN2
TOKEN2 = re.compile(r"[a-z0-9_]{2,}")
_orig = bm25.tokens
SMALL = {"the", "a", "an", "and", "or", "of", "to", "in", "is", "for", "on", "2026", "2025", "2024"}


def tokens_fixed(text):
    return [t for t in TOKEN2.findall((text or "").lower())
            if t not in bm25.STOP and (len(t) > 2 or t in DOMAIN2)]


def words_of(slug):
    """Slug words a person might actually type. Keeps the domain terms the spellfix harness drops."""
    out = []
    for w in re.split(r"[_\-]", slug.lower()):
        if not w or w.isdigit() or w in SMALL:
            continue
        if len(w) > 3 or w in DOMAIN2:
            out.append(w)
    return out


def hit10(idx, query, target, fixed):
    bm25.tokens = tokens_fixed if fixed else _orig
    try:
        got = [s for s, _ in bm25.BM25(idx).score(query, 10)]
    finally:
        bm25.tokens = _orig
    return target in got


def ensure_shadows():
    """Build both arms from one corpus at one moment. Never touches the live index - build() writes
    only its out path and out+'.pkl'."""
    for path, fn, label in ((A, _orig, "current {3,}"), (B, tokens_fixed, "fixed +mv/iv/dd/ib")):
        if os.path.exists(path):
            continue
        bm25.tokens = fn
        try:
            n, v = bm25.build(out=path)
        finally:
            bm25.tokens = _orig
        print("  built %-20s %5d docs %7d terms" % (label, n, v))


def run():
    ensure_shadows()
    slugs = list(bm25.BM25(A).dl.keys())
    hits, ctrls = [], []
    for slug in slugs:
        ws = words_of(slug)
        if len(ws) < 3:
            continue
        d2 = [w for w in ws if w in DOMAIN2]
        # query = the domain term (if any) plus the first three other words: what someone would type
        rest = [w for w in ws if w not in DOMAIN2][:3]
        if len(rest) < 3:
            continue
        q = " ".join(d2 + rest)
        (hits if d2 else ctrls).append((slug, q))

    step = max(1, len(ctrls) // len(hits)) if hits else 1
    ctrls = ctrls[::step][:len(hits)]          # match control count to target count
    print("  targets (slug carries mv/iv/dd/ib): %d" % len(hits))
    print("  controls (slug carries none):       %d\n" % len(ctrls))

    res = {}
    for name, cases in (("TARGETS", hits), ("CONTROLS", ctrls)):
        off = sum(hit10(A, q, s, False) for s, q in cases)
        on = sum(hit10(B, q, s, True) for s, q in cases)
        n = len(cases) or 1
        res[name] = (off / n, on / n, off, on, n)
        print("  %-9s hit@10   off %5.1f%% (%d/%d)   on %5.1f%% (%d/%d)   delta %+.1fpp"
              % (name, 100.0 * off / n, off, n, 100.0 * on / n, on, n, 100.0 * (on - off) / n))

    t_off, t_on = res["TARGETS"][0], res["TARGETS"][1]
    c_off, c_on = res["CONTROLS"][0], res["CONTROLS"][1]
    a = t_on > t_off
    b = c_on >= c_off
    print("\n  (a) targets improve      %s" % ("PASS" if a else "FAIL"))
    print("  (b) controls do not fall %s" % ("PASS" if b else "FAIL"))
    print("\n  GATE: %s" % ("PASS - earns default-on" if (a and b) else "FAIL - do not enable"))
    return 0 if (a and b) else 1


if __name__ == "__main__":
    sys.exit(run())
