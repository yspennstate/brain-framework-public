"""apply_policy.py - set the live membrane's bars from one walk-forward study result, naming it (26 Sep, claude-825b1305).

A bar wired into the live hook carries the measurement that produced it (the brain's reflex: a deployed number names its
estimator). Each bar is the score at which that channel's top line fired on a chosen share of the study's moments:
places at 5 percent, memory by node size at 5, memory by edge at 2, his corrections at 2; errors stay in shadow (the
learner turns them on from its own judgments). Only the bars move; the rest of policy.json is kept.

    python apply_policy.py <results json> [--dry]
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import live_membrane as LM  # noqa: E402


def at(curve, rate):
    for c in curve:
        if abs(c["fire_rate"] - rate) < 1e-9:
            return c
    return None


def main():
    path = sys.argv[1]
    dry = "--dry" in sys.argv
    d = json.load(open(path, encoding="utf-8"))
    pick = {"place_hebb_min": at(d["place"]["curve"], 0.05), "mem_size_min": at(d["size"]["curve_top1"], 0.05),
            "mem_hebb_min": at(d["hebb"]["curve_top1"], 0.02), "err_hebb_min": at(d["err"]["curve"], 0.02)}
    if "his_correction" in d:
        pick["verdict_hebb_min"] = at(d["his_correction"]["curve"], 0.02)
    try:
        pol = json.load(open(LM.POLICY, encoding="utf-8"))
    except Exception:
        pol = {}
    note = []
    for k, c in pick.items():
        if c:
            pol[k] = round(float(c["threshold"]), 3)
            note.append("%s %.3f (right %.1f%% of %d lines at a %.0f%% fire rate)" % (k, c["threshold"], 100 * c["precision"],
                                                                                   c["fired"], 100 * c["fire_rate"]))
    pol["version"] = int(pol.get("version", 1)) + 1
    pol["_set_by"] = "apply_policy.py %s from %s: %s" % (time.strftime("%Y-%m-%d %H:%M"), os.path.basename(path),
                                                          "; ".join(note))
    print(pol["_set_by"])
    if not dry:
        tmp = LM.POLICY + ".tmp"
        json.dump(pol, open(tmp, "w", encoding="utf-8"), indent=1)
        os.replace(tmp, LM.POLICY)


if __name__ == "__main__":
    main()
