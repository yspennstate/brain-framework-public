#!/usr/bin/env python3
"""eval_hook_fire_reasons.py - which gate keeps the proactive hook silent?

brain_regression_gate has reported "fire rate 37-47% of 60 (floor 60%)" and "mistake-class fire
rate 3-12% (floor 40%)" on every run of 2026-09-01/02. Rebuilding the stale mistake-weight table
moved the second number from 3% to 12% and the first not at all, so the weights were one dead
input, not the gap. This harness runs the SAME prompts through spread_recall.main() under one
ablation at a time and reports the fire rate and mistake-class rate for each, so the binding gate
is measured rather than guessed:

  baseline          the live constants
  margin_1.10       MARGIN 1.175 -> 1.10 (standout-or-nothing relaxed)
  act_floor_half    ACT_FLOOR 0.16 -> 0.08 (weaker graph signal may speak)
  sal_floor_30      SAL_FLOOR 45 -> 30 (less salient memories may speak)
  no_habituation    cross-session habituation table treated as empty
  cand_floor_half   CAND_FLOOR 0.10 -> 0.05 (more candidates enter)
  all_relaxed       all of the above together (the ceiling this machinery can reach)

Read-only: session ids are non-UUID ("reasons-...") so no dedup, habituation or surfacing ledger is
written by the hook, the same convention the regression gate uses. Usage:
  python eval_hook_fire_reasons.py [--n 60] [--out receipt.json]
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import time
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def run_prompt(sr, prompt, tag, index):
    old_in, old_out = sys.stdin, sys.stdout
    sys.stdin = io.StringIO(json.dumps({"hook_event_name": "UserPromptSubmit", "prompt": prompt,
                                        "session_id": "reasons-%s-%d" % (tag, index)}))
    sys.stdout = capture = io.StringIO()
    try:
        sr.main()
    except SystemExit:
        pass
    except Exception:
        return None
    finally:
        sys.stdin, sys.stdout = old_in, old_out
    out = capture.getvalue().strip()
    if not out:
        return None
    try:
        msg = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        m = re.search(r"this reminds me of `([^`]+)`", msg)
        return m.group(1) if m else None
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=60)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    import eval_mistake_surfacing as em
    import spread_recall as sr
    prompts = em.harvest(n_files=8)[:args.n]
    mw = sr._load_mistake_weights()
    base = {k: getattr(sr, k) for k in ("MARGIN", "ACT_FLOOR", "SAL_FLOOR", "CAND_FLOOR")}
    habit_orig = sr._load_habituation
    variants = {
        "baseline": {},
        "margin_1.10": {"MARGIN": 1.10},
        "act_floor_half": {"ACT_FLOOR": base["ACT_FLOOR"] / 2},
        "sal_floor_30": {"SAL_FLOOR": 30.0},
        "no_habituation": {"_habit": True},
        "cand_floor_half": {"CAND_FLOOR": base["CAND_FLOOR"] / 2},
        "all_relaxed": {"MARGIN": 1.10, "ACT_FLOOR": base["ACT_FLOOR"] / 2, "SAL_FLOOR": 30.0, "_habit": True,
                        "CAND_FLOOR": base["CAND_FLOOR"] / 2},
    }
    results = {}
    t0 = time.time()
    for name, over in variants.items():
        for k, v in base.items():
            setattr(sr, k, over.get(k, v))
        sr._load_habituation = (lambda: {}) if over.get("_habit") else habit_orig
        fired = []
        for i, p in enumerate(prompts):
            fired.append(run_prompt(sr, p, name, i))
        n_f = sum(1 for f in fired if f)
        n_m = sum(1 for f in fired if f and mw.get(f, 0) >= 0.5)
        results[name] = {"fire_rate": round(n_f / len(prompts), 3), "mistake_rate": round(n_m / len(prompts), 3),
                         "fired": n_f, "mistake_fires": n_m, "slugs": [f for f in fired if f]}
        print("%-16s fire %2d/%d (%.0f%%)  mistake-class %2d (%.0f%%)  distinct %d" % (
            name, n_f, len(prompts), 100 * n_f / len(prompts), n_m, 100 * n_m / len(prompts), len(set(f for f in fired if f))), flush=True)
    for k, v in base.items():
        setattr(sr, k, v)
    sr._load_habituation = habit_orig
    rec = {"when": time.strftime("%Y-%m-%d %H:%M"), "n_prompts": len(prompts), "base_constants": base, "results": results,
           "seconds": round(time.time() - t0, 1)}
    if args.out:
        with io.open(args.out, "w", encoding="utf-8") as f:
            json.dump(rec, f, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
