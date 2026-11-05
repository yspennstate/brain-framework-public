#!/usr/bin/env python3
"""eval_shadow_fires.py - run the proactive MARGIN gate's OWN intended retune (spread_recall logs to
shadow_fires.tsv every fire it suppressed that would have fired at margin 1.15 instead of the live 1.35;
the comment there says 'future co-reads score the challenger offline, and the retune happens on real n').

VERDICT (2026-07-11): MARGIN=1.35 CONFIRMED - keep. Ran the retune on n=28 accumulated shadow-fires: only
4/28 (14%) of the MARGIN-suppressed challengers were read in-session, so 86% of suppressions correctly
filtered noise. The useful ones cluster in the 1.25-1.35 ratio bucket (4/13 = 31%) vs 0/15 in 1.15-1.25, so
IF ever loosened it should go to ~1.25, not lower - but 69% of that bucket is still noise, so for a
protect-the-owner's-attention brain the high-precision 1.35 gate is the right call. n=28 is thin (directional
only; the gate's own comment flagged n=7 as too thin), and the shadow log keeps growing for a firmer future
retune. With eval_proactive_coeffs (cosine-damp floor) this completes the proactive-hook audit: ranking AND
firing gate both confirmed.

For each shadow-fire (session S, suppressed challenger C, time T): was C actually READ in session S (and did
that read come AFTER T)? If a suppressed challenger gets read in-session, the tighter MARGIN=1.35 gate cost a
USEFUL surfacing; if it's rarely read, the gate is correctly filtering marginal ties. Read-only; MEASURE +
recommend a direction, do not auto-retune (the gate fires every session). Usage: python eval_shadow_fires.py
"""

import sys
if __name__ == "__main__" and any(arg in ("-h", "--help") for arg in sys.argv[1:]):
    print(__doc__ or "No command-line options.")
    raise SystemExit(0)

import io, os, re, time
from collections import defaultdict
DR = os.path.expanduser(r"~/.claude/dreaming")
SHADOW = os.path.join(DR, "shadow_fires.tsv")
TRACE = os.path.join(DR, "retrievals.tsv")
# rows judged by the last run: work_finders W71 asks for a re-run only once the log has grown past this
STAMP = os.path.join(DR, "state", "shadow_fires_eval.json")


def norm_slug(s):
    return s.strip()


def main():
    if not os.path.exists(SHADOW):
        print("no shadow_fires.tsv - the MARGIN gate has never shadow-logged a suppression."); return
    shadows = []
    for ln in io.open(SHADOW, encoding="utf-8", errors="replace"):
        p = ln.rstrip("\n").split("\t")
        if len(p) >= 6:
            try:
                ts = time.mktime(time.strptime(p[0], "%Y-%m-%dT%H:%M:%S"))
            except Exception:
                ts = None
            shadows.append({"ts": ts, "iso": p[0], "sess": p[1], "slug": p[2],
                            "top": float(p[3]), "second": float(p[4]), "ratio": float(p[5])})
    # trace: which slugs were read in which session, at what time
    reads_by_sess = defaultdict(list)   # session-prefix -> [(epoch, slug)]
    for i, ln in enumerate(io.open(TRACE, encoding="utf-8", errors="replace")):
        if i == 0:
            continue
        p = ln.rstrip("\n").split("\t")
        if len(p) >= 4:
            try:
                reads_by_sess[p[3][:12]].append((float(p[1]), p[0]))
            except Exception:
                pass

    n = len(shadows)
    read_insess = read_after = 0
    by_ratio = defaultdict(lambda: [0, 0])   # ratio bucket -> [useful, total]
    examples = []
    for s in shadows:
        reads = reads_by_sess.get(s["sess"][:12], [])
        slugs = {sl for _, sl in reads}
        was_read = s["slug"] in slugs
        after = any(sl == s["slug"] and (s["ts"] is None or ep >= s["ts"] - 1) for ep, sl in reads)
        if was_read:
            read_insess += 1
        if after:
            read_after += 1
        b = "1.15-1.25" if s["ratio"] < 1.25 else "1.25-1.35"
        by_ratio[b][1] += 1
        by_ratio[b][0] += 1 if was_read else 0
        if len(examples) < 6:
            examples.append((s["slug"][:42], s["ratio"], "READ" if was_read else "not read"))

    print(f"shadow-fires (MARGIN=1.35 suppressed, would-fire-at-1.15): {n}")
    print(f"  suppressed challenger later READ in its session:  {read_insess}/{n} ({100*read_insess//max(1,n)}%)")
    print(f"  ...and read AFTER the suppressed-fire moment:      {read_after}/{n}")
    print("  by margin bucket (useful/total):")
    for b, (u, t) in sorted(by_ratio.items()):
        print(f"    ratio {b}: {u}/{t}")
    print("  sample:")
    for slug, r, verdict in examples:
        print(f"    {r:.2f}  {verdict:8s}  {slug}")
    frac = read_insess / max(1, n)
    print()
    if n < 20:
        print(f"  n={n} is thin - directional only (the gate's own comment flagged n=7 as too thin).")
    if frac >= 0.35:
        print(f"  -> {100*frac:.0f}% of MARGIN-suppressed challengers were read in-session: the 1.35 gate is "
              f"costing useful surfacings. RECOMMEND testing a lower MARGIN (~1.2) - but MEASURE-only here; "
              f"deploy deliberately (fires every session), and re-check as n grows.")
    else:
        print(f"  -> only {100*frac:.0f}% of suppressed challengers were read in-session: MARGIN=1.35 is mostly "
              f"filtering marginal ties correctly. Directionally CONFIRMED; keep, and let the shadow log grow.")
    try:
        import json
        os.makedirs(os.path.dirname(STAMP), exist_ok=True)
        with open(STAMP, "w", encoding="utf-8") as f:
            json.dump({"rows": n, "read_in_session": read_insess, "at": time.strftime("%Y-%m-%dT%H:%M:%S")}, f)
    except OSError:
        pass


if __name__ == "__main__":
    main()
