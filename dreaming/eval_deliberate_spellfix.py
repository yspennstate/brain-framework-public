"""eval_deliberate_spellfix.py - does the deliberate-recall spell-fix earn its keep?

The deliberate brain.py recall bm25 block scores the RAW query, so a typo silently drops its term
(the proactive seeder normalizes; the deliberate path did not - a 2026-07-15 bm25-first-refactor
oversight, node recall_deliberate_bm25_no_spellfix_2026_07_24). The fix is env-gated:
  BRAIN_RECALL_SPELLFIX=1  ->  bm25 block scores QueryNormalizer.normalize(query).

This measures EXACTLY what the fix changes - BM25.score(query) [off] vs BM25.score(normalize(query))
[on] - over many slug-derived targets, clean vs typo'd, at two typo severities. Laptop-safe (ms).

GATE (prints PASS/FAIL, exits nonzero on FAIL): the fix earns default-on iff, at every severity,
  ON recovers typo recall (typo_on > typo_off)  AND  clean recall does not regress (clean_on >= clean_off).
Usage:  python eval_deliberate_spellfix.py
"""
import os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bm25 import BM25, QueryNormalizer

STOP = {"the","a","an","and","or","of","to","in","is","for","on","2026","2025","2024"}

def words_of(slug):
    return [w for w in re.split(r"[_\-]", slug)
            if w and not w.isdigit() and w.lower() not in STOP and len(w) > 3]

def typo(word):                      # deterministic edit-1: delete the middle char
    if len(word) < 5: return word
    m = len(word) // 2
    return word[:m] + word[m+1:]

def run():
    bm = BM25()
    qn = QueryNormalizer(bm.df)
    slugs = list(bm.dl.keys())
    step = max(1, len(slugs) // 120)
    sample = slugs[::step]

    cases = []
    for slug in sample:
        ws = words_of(slug)[:3]
        if len(ws) < 2: continue
        cases.append((slug, ws))
    if not cases:
        print("no usable targets"); return 1

    def hit10(query, target, normalize):
        q = qn.normalize(query) if normalize else query
        return any(s == target for s, _ in bm.score(q, 10))

    def measure(n_typo):
        from collections import Counter
        r = Counter(); tot = 0
        for target, ws in cases:
            clean = " ".join(ws)
            tw = [typo(w) if i < n_typo else w for i, w in enumerate(ws)]
            if tw == ws: continue
            tq = " ".join(tw)
            tot += 1
            r["clean_off"] += hit10(clean, target, False)
            r["clean_on"]  += hit10(clean, target, True)
            r["typo_off"]  += hit10(tq, target, False)
            r["typo_on"]   += hit10(tq, target, True)
        return r, tot

    print(f"eval_deliberate_spellfix: {len(cases)} slug-derived targets\n")
    ok = True
    for label, n in [("LIGHT (typo 1 word)", 1), ("HEAVY (typo all words)", 99)]:
        r, tot = measure(n)
        if not tot: continue
        p = lambda k: 100.0 * r[k] / tot
        clean_ok = r["clean_on"] >= r["clean_off"]
        typo_ok = r["typo_on"] > r["typo_off"]
        gate = clean_ok and typo_ok
        ok = ok and gate
        print(f"  {label}  (n={tot})")
        print(f"    CLEAN  off {p('clean_off'):5.1f}%   on {p('clean_on'):5.1f}%   {'ok' if clean_ok else 'REGRESSION'}")
        print(f"    TYPO   off {p('typo_off'):5.1f}%   on {p('typo_on'):5.1f}%   recovery {p('typo_on')-p('typo_off'):+.1f}pt   {'ok' if typo_ok else 'NO GAIN'}")
    print(f"\n  GATE: {'PASS - spellfix recovers typos with no clean regression (recommend default-on)' if ok else 'FAIL - check regression/gain'}")
    return 0 if ok else 1

if __name__ == "__main__":
    sys.exit(run())
