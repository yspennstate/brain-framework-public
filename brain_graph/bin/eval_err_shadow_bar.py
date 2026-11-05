"""The error lines at the membrane's bars: the top 2, 5 and 10 percent of evaluations by the top candidate's graph
weight. For each selected evaluation, hit = its class occurs in the session within WINDOW; expected = the base rate
of that class (its occurrence within WINDOW after any evaluation of the same receiver kind). Lift = hits / expected.
Reuses err_shadow.py's data loading. Read-only; aggregates only."""
import sys, os
sys.argv = [sys.argv[0]] + (sys.argv[1:] or ["1800"])
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import io, contextlib
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    import eval_err_shadow as E            # loads evals and errs, prints its own tables (swallowed)

for kind in ("all", "his"):
    ev = [e for e in E.evals if kind == "all" or e[2] == kind]
    base = {}
    for cls in {e[3] for e in ev}:
        base[cls] = sum(1 for e in ev if E.occurs(e[1], cls, e[0])) / float(len(ev))
    ranked = sorted(ev, key=lambda e: -e[4])
    print("== %s: %d evaluations" % (kind, len(ev)))
    for frac in (0.02, 0.05, 0.10, 1.0):
        top = ranked[:max(1, int(round(frac * len(ranked))))]
        hits = sum(1 for e in top if E.occurs(e[1], e[3], e[0]))
        exp = sum(base[e[3]] for e in top)
        print("   top %3d%% (weight >= %5.1f): n %4d  hits %3d (%.1f%%)  expected from base %.1f  lift %s" % (
            int(frac * 100), top[-1][4], len(top), hits, 100.0 * hits / len(top), exp,
            ("%.2f" % (hits / exp)) if exp else "-"))
