#!/usr/bin/env python3
"""shadow_eval.py - replay today's mistakes (+ benign controls) through the graph-firing pipeline and
measure catch-rate vs false-fire, BEFORE anything goes live. This is the adversarial gate: a caution-
biased mechanism is only worth wiring in if it catches the real misses without drowning the session.

Positives = the 07-11 mistakes (grid undershoot in prose, backslash-path bug, leveraged-ETF, VXN sub,
over-claimed done, ...). Negatives = benign read-only actions and plain observations. Each case is routed
to the same components the live hooks will use, by its surface:
  assistant_text -> pre_report.check   |   bash -> gfcore + forward_feed   |   edit/prompt -> gfcore

Reports strict catch (the exact expected reflex fired) and mechanism catch (that OR a block/anchor flag),
plus false-fire on the controls. Stdlib only, forward-slash paths.
"""
import os, io, json, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gfcore, forward_feed, pre_report, eval_real

CASES = os.path.join(HERE, "heldout_cases.json")


def fire_all(surface, text):
    """Mirror the LIVE firing paths per surface. Command surfaces (bash/edit/prompt) run instinct_prime =
    regex UNION calibrated-graph (+ forward-feed on bash). The assistant_text surface runs pre_report
    (graph at higher sensitivity + anchor-mismatch + completion-claim); regex does NOT run on prose live."""
    ids, block = set(), False
    try:
        if surface == "assistant_text":
            r = pre_report.check(text)
            ids |= set(f["id"] for f in r["graph_fires"])
            if r["mismatches"]:
                ids.add("lived_anchor_mismatch")
            block = r["should_block"]
        elif surface == "bash":
            ids |= eval_real.fire_regex(text)
            ids |= set(f["id"] for f in gfcore.graph_fire(text))
            ids |= set(f["id"] for f in forward_feed.forward_feed(text, "Bash"))
        else:  # edit / prompt
            ids |= eval_real.fire_regex(text)
            ids |= set(f["id"] for f in gfcore.graph_fire(text))
    except Exception as e:
        print("  (error firing:", e, ")")
    return ids, block


def main():
    data = json.load(io.open(CASES, encoding="utf-8"))
    pos, neg = data.get("positives", []), data.get("negatives", [])
    strict_hits = mech_hits = 0
    print("=== POSITIVES (should fire the right caution) ===")
    for c in pos:
        ids, block = fire_all(c["surface"], c["text"])
        exp = set(c.get("expect", []))
        strict = bool(exp & ids)
        mech = strict or block
        strict_hits += strict
        mech_hits += mech
        tag = "HIT " if mech else "MISS"
        print("  [%s] %-16s surface=%-13s block=%-5s fired=%s"
              % (tag, c["id"], c["surface"], block, sorted(ids)[:5]))
        if not mech:
            print("        expected one of %s" % sorted(exp))
    ff = 0
    print("=== NEGATIVES (should stay quiet) ===")
    for c in neg:
        ids, block = fire_all(c["surface"], c["text"])
        fired = bool(ids) or block
        ff += fired
        tag = "FALSE" if fired else "quiet"
        print("  [%s] %-16s surface=%-13s fired=%s" % (tag, c["id"], c["surface"], sorted(ids)[:5]))
    print("\n=== SUMMARY ===")
    print("positives: %d/%d mechanism-caught (%d/%d exact-reflex)"
          % (mech_hits, len(pos), strict_hits, len(pos)))
    print("negatives: %d/%d false-fired  (%d quiet)" % (ff, len(neg), len(neg) - ff))
    # spotlight the two named misses
    for cid in ("grid_undershoot", "backslash_path"):
        c = next((x for x in pos if x["id"] == cid), None)
        if c:
            ids, block = fire_all(c["surface"], c["text"])
            ok = bool(set(c.get("expect", [])) & ids) or block
            print("  %-16s -> %s" % (cid, "CAUGHT" if ok else "MISSED"))


if __name__ == "__main__":
    main()
