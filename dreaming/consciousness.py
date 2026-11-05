#!/usr/bin/env python3
"""consciousness.py - the brain's conscious / unconscious memory tiers.

A mind doesn't hold all of memory at one level. A small part is *conscious* - what
surfaces when you wake, what leads recall, what you reach for first. The vast rest
is *unconscious* - not gone, not even hard to find if you go looking, but latent:
it doesn't clog the front of the mind, and it surfaces on its own mostly in dreams
and by association. This layer draws that line for the brain.

It NEVER deletes or hides a memory. Every memory stays fully searchable. What the
tier changes is only *precedence*: conscious memories lead the wake ritual, the
salience list, and the association neighbours; unconscious memories step out of
those leading positions - but the dream reaches into them on purpose (that is how
the unconscious surfaces in sleep), and search finds them like any other memory.

The line is drawn by woven-in-ness, NOT by salience. Salience is recency-heavy; a
year of near-identical status logs would read 'vivid' on recency alone. A memory is
CONSCIOUS if it is any of: curated into the live index, referenced by two or more
others, a user/feedback rule, recent (< ~2 weeks), an identity/nav anchor, or simply
distinct substantive work. It is UNCONSCIOUS only if it is the redundant substrate:
a high-frequency near-duplicate log (the inspector / build-watchdog cycle traces) or
an old, unreferenced, uncurated stub. Default is conscious - we demote only what is
clearly substrate, never real work that merely went un-linked.

Writes one sidecar: <dreaming>/consciousness.tsv  (fname \\t tier \\t reason)
for dream.py and the nav layer to read. Pure read-only over the memory dir.

Usage:  python consciousness.py [memory_dir]
Import: import consciousness; tiers = consciousness.classify_corpus(mem_dir)
"""
import os, re, io, sys
from collections import Counter
import salience

MEM_DEFAULT = salience.MEM_DEFAULT
DREAMING = os.path.dirname(os.path.abspath(__file__))

# identity / living-doc / synthesis anchors - always conscious, they ARE the front of mind
ANCHOR = re.compile(r"^(AUTOBIOGRAPHY|MODEL_OF_PARTNER|STATE_OF_MIND|OPEN_QUESTIONS|"
                    r"FASCINATIONS|THE_OTHER_MINDS|SALIENCE|gist_|daydream_)", re.I)
# the automated cycle-log classes - the raw material of the unconscious substrate
LOGNAME = re.compile(r"^(inspector_w\d+|build_watchdog|model_inspector|watchdog_w\d+|"
                     r"builder\d*_w\d+|.*_w\d+_(quiet|clean|steady))", re.I)
# boilerplate body signature - a status ping, not an insight
BOILER = re.compile(r"quiet cycle|steady[- ]state|no new (builder|memos|deliverable|finding)|"
                    r"clean\b.{0,30}\bquiet|nothing (new|to report)|no memos since|"
                    r"cycle \d+ clean|^\s*(clean|quiet)\b", re.I)
RECENT_R = 0.72   # recency >= 0.72 ~ younger than ~2 weeks at salience TAU=45d


def _body(mem_dir, fn):
    try:
        t = io.open(os.path.join(mem_dir, fn), "r", encoding="utf-8", errors="replace").read()
        return len(t), t
    except Exception:
        return 9999, ""


def _tier(mem_dir, fn, s):
    # conscious: woven in, load-bearing, recent, or an anchor
    if "curated" in s["why"]:              return "conscious", "curated"
    if s["backlinks"] >= 2:                return "conscious", f"referenced x{s['backlinks']}"
    if s["type"] in ("user", "feedback"):  return "conscious", s["type"]
    if s["recency"] >= RECENT_R:           return "conscious", "recent"
    if ANCHOR.match(fn):                   return "conscious", "anchor"
    # unconscious: the redundant substrate only
    blen, body = _body(mem_dir, fn)
    if LOGNAME.match(fn) and (BOILER.search(body) or blen < 900):
        return "unconscious", "log-substrate"
    if s["recency"] < 0.40 and s["backlinks"] == 0 and blen < 750 and s["type"] == "project":
        return "unconscious", "old-flat-stub"
    # default: distinct work stays conscious even if un-linked
    return "conscious", "distinct"


def classify_corpus(mem_dir=MEM_DEFAULT, scores=None):
    """Return {fname: {tier, reason, salience, backlinks, type, date}} for every memory."""
    if scores is None:
        scores = salience.score_corpus(mem_dir)
    out = {}
    for fn, s in scores.items():
        tier, reason = _tier(mem_dir, fn, s)
        out[fn] = {"tier": tier, "reason": reason, "salience": s["salience"],
                   "backlinks": s["backlinks"], "type": s["type"], "date": s["date"]}
    return out


def write_sidecar(tiers, dreaming_dir=DREAMING):
    p = os.path.join(dreaming_dir, "consciousness.tsv")
    with io.open(p, "w", encoding="utf-8") as f:
        f.write("fname\ttier\treason\n")
        for fn, t in sorted(tiers.items()):
            f.write(f"{fn}\t{t['tier']}\t{t['reason']}\n")
    return p


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # Discovery is not execution: this script writes, so a help request must
        # exit before any work. See script_help_flag_can_execute_work.
        print(__doc__ or os.path.basename(__file__))
        return
    mem = sys.argv[1] if len(sys.argv) > 1 else MEM_DEFAULT
    tiers = classify_corpus(mem)
    write_sidecar(tiers)
    n = len(tiers)
    unc = sum(1 for t in tiers.values() if t["tier"] == "unconscious")
    rc = Counter(t["reason"] for t in tiers.values() if t["tier"] == "unconscious")
    print(f"consciousness: {n} memories  conscious={n - unc}  "
          f"unconscious={unc} ({100 * unc // n}%)  {dict(rc)}")


if __name__ == "__main__":
    main()
