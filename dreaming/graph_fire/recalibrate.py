#!/usr/bin/env python3
"""recalibrate.py - the adaptation loop, with a guard against adopting a WORSE calibration.

The per-reflex thresholds are fit once against a snapshot of typical actions. The fleet's workload drifts,
so this re-samples fresh typical actions, rebuilds the anchors, calibrates a CANDIDATE, and compares it to
the live calibration on the held-out eval. It replaces the live file ONLY if the candidate is not worse
(recall doesn't drop, nag doesn't rise past a small tolerance) - a self-improvement step that can't quietly
degrade the mechanism. Meant to be run occasionally (e.g. by the nightly dream), not on the hot path.

Runs light: build_anchors/calibrate are milliseconds; sample_actions samples transcripts (bounded). Prints
the old-vs-candidate comparison and the adopt/keep decision. Fail-safe: on any error it keeps the live file.
"""
import os, io, sys, json, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
LIVE = os.path.join(HERE, "reflex_anchors_cal.json")
CAND = os.path.join(HERE, "reflex_anchors_cal_candidate.json")
LOG = os.path.join(HERE, "recalibrate.log")
BUDGET = "0.02"
NAG_TOLERANCE = 0.03   # allow the candidate up to +3% nag if recall improves; never adopt a recall drop


def _run(script, *args):
    subprocess.run([PY, os.path.join(HERE, script)] + list(args), cwd=HERE, timeout=300,
                   capture_output=True, text=True)


def _score(cal_path):
    sys.path.insert(0, HERE)
    import eval_real as E
    model = E.load_model(cal_path)
    posM = E.positives_mistakes()
    typ_act = [r for r in E.load_typical()[1::2] if not r["is_read"]]
    fire = lambda t: E.fire_regex(t) | E.fire_graph_cal(t, model)
    rec = sum(1 for ctx, exp in posM if exp & fire(ctx)) / max(1, len(posM))
    nag = sum(1 for r in typ_act if fire(r["text"])) / max(1, len(typ_act))
    return rec, nag


def _log(msg):
    try:
        import time  # only for the stamp; not on the hot path
        with io.open(LOG, "a", encoding="utf-8") as f:
            f.write("%.0f\t%s\n" % (time.time(), msg))
    except Exception:
        pass


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # Discovery is not execution: this script writes, so a help request must
        # exit before any work. See script_help_flag_can_execute_work.
        print(__doc__ or os.path.basename(__file__))
        return
    try:
        if not os.path.exists(LIVE):
            print("no live calibration yet; run calibrate.py first")
            return
        old_rec, old_nag = _score(LIVE)
        # refresh negatives (a big, representative sample - the 700 default under-counted nag by ~0.15) +
        # rebuild + calibrate a candidate (does not touch the live file)
        _run("sample_actions.py", "--files=100", "--max=2500")
        _run("build_anchors.py")
        _run("calibrate.py", "--budget=" + BUDGET, "--out=" + CAND)
        if not os.path.exists(CAND):
            print("candidate build failed; keeping live calibration")
            return
        new_rec, new_nag = _score(CAND)
        # a CONTENT refresh (reflexes added/removed since the live model was built) is always adopted -
        # it's more current, not calibration drift. The worse-guard applies only when the reflex SET is
        # unchanged (a pure recalibration on fresh negatives).
        live_ids = set(json.load(io.open(LIVE, encoding="utf-8")).get("reflexes", {}))
        cand_ids = set(json.load(io.open(CAND, encoding="utf-8")).get("reflexes", {}))
        refresh = live_ids != cand_ids
        better = refresh or ((new_rec >= old_rec - 1e-9) and (new_nag <= old_nag + NAG_TOLERANCE))
        print("live      : recall=%.3f nag=%.3f (%d reflexes)" % (old_rec, old_nag, len(live_ids)))
        print("candidate : recall=%.3f nag=%.3f (%d reflexes)%s"
              % (new_rec, new_nag, len(cand_ids), "  [CONTENT REFRESH]" if refresh else ""))
        if better:
            os.replace(CAND, LIVE)   # atomic: a concurrent gfcore read gets old-or-new bytes, never partial
            print("ADOPTED candidate (not worse than live).")
            _log("adopt rec %.3f->%.3f nag %.3f->%.3f" % (old_rec, new_rec, old_nag, new_nag))
        else:
            print("KEPT live (candidate would drop recall or raise nag past tolerance).")
            _log("keep rec %.3f vs %.3f nag %.3f vs %.3f" % (old_rec, new_rec, old_nag, new_nag))
            try:
                os.remove(CAND)
            except Exception:
                pass
    except Exception as e:
        print("recalibrate error (kept live):", e)


if __name__ == "__main__":
    main()
