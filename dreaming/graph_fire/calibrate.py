#!/usr/bin/env python3
"""calibrate.py - give each reflex its OWN firing threshold, calibrated to a false-alarm budget on REAL
typical actions. A uniform threshold is wrong: 'leveraged_etf_is_not_linear' (tokens etf/carry/reset) is
specific and safe to fire readily, while 'confirm_the_target' (grid/fleet/machine/control) is so broad it
fires on almost any command. Per-reflex calibration bounds each reflex's individual nuisance rate - an
FDR-style control - so the broad ones get a high bar and the specific ones a low one.

For each reflex we find the smallest threshold t such that its fire-rate on the 700-action typical sample
is <= BUDGET, and write it back as m['thr']. eval_real reads per-reflex thresholds via --cal. This is the
principled replacement for the single hand-set 1.05 that the honest eval showed over-fires at 0.79.

Stdlib only, forward-slash paths, no heavy compute.
"""
import os, io, json, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL = os.path.join(HERE, "reflex_anchors.json")
TYPICAL = os.path.join(HERE, "typical_actions.jsonl")
OUT = os.path.join(HERE, "reflex_anchors_cal.json")

_WORD = re.compile(r"[a-zA-Z][a-zA-Z0-9]{2,}")
_STOP = set("""the a an and or of to in on for by is are was were be been being it its this that these those
with without into onto from as at we you he she they them his her their our your my me not no yes do did done
if then else than so but scar gist method feedback project reference dream daydream new before after check make
sure look take run use get keep need want first still just also see saw open said say tell point place change move
find what which when where why how who out off down over under about around here there will would could should can
may might must more most less very really thing things one two three""".split())


def toks(s):
    return [w.lower() for w in _WORD.findall(s or "") if w.lower() not in _STOP and not w.isdigit()]


def score(ctoks, tokmap):
    s = 0.0
    n = 0
    for t in ctoks:
        w = tokmap.get(t)
        if w:
            s += w
            n += 1
    return s, n


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # Discovery is not execution: this script writes, so a help request must
        # exit before any work. See script_help_flag_can_execute_work.
        print(__doc__ or os.path.basename(__file__))
        return
    budget = 0.015
    model_path, out_path, typical = MODEL, OUT, TYPICAL
    for a in sys.argv[1:]:
        if a.startswith("--budget="):
            budget = float(a.split("=", 1)[1])
        if a.startswith("--model="):
            model_path = a.split("=", 1)[1]
        if a.startswith("--out="):
            out_path = a.split("=", 1)[1]
        if a.startswith("--typical="):
            typical = a.split("=", 1)[1]
            if not os.path.isabs(typical):
                typical = os.path.join(HERE, typical)
    d = json.load(io.open(model_path, encoding="utf-8"))
    model = d.get("reflexes", {})
    allrows = [json.loads(l) for l in io.open(typical, encoding="utf-8") if l.strip()]
    typ = allrows[::2]   # calibrate on the EVEN half; eval measures nag on the ODD half (no peeking)
    typ_tok = [set(toks(r["text"])) for r in typ]
    N = len(typ)
    grid = [round(0.6 + 0.1 * i, 2) for i in range(45)]   # 0.6 .. 5.0
    n_specific = n_broad = 0
    for rid, m in model.items():
        if m.get("mode") == "carry":
            m["thr"] = 99.0
            continue
        tk = m.get("tokens", {})
        # fire-rate on typical actions as a function of threshold
        best = grid[-1]
        for t in grid:
            ff = 0
            for ct in typ_tok:
                s, n = score(ct, tk)
                if n >= 2 and s >= t:
                    ff += 1
            if ff / N <= budget:
                best = t
                break
        m["thr"] = best
        if best <= 1.3:
            n_specific += 1
        elif best >= 3.0:
            n_broad += 1
    d["_meta"]["calibrated_budget"] = budget
    json.dump(d, io.open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    print("wrote %s  (per-reflex FP budget=%.1f%% of typical actions)" % (os.path.basename(out_path), 100 * budget))
    print("  reflexes: %d specific (thr<=1.3, fire readily) | %d broad (thr>=3.0, high bar)"
          % (n_specific, n_broad))
    # show a few calibrated thresholds
    ex = ["leveraged_etf_is_not_linear", "sim_flatters", "risk_vs_measured_record", "confirm_the_target",
          "he_will_look", "lookahead", "secrets_in_git", "vxn_long_tenor_state"]
    for rid in ex:
        if rid in model:
            print("    %-28s thr=%.2f" % (rid, model[rid]["thr"]))


if __name__ == "__main__":
    main()
