"""check_lag_train_equals_train.py - the positive control for study_lag_edges.py: its
equal-credit counts must be study_sequence_model.train's, exactly, on the same input.

The committed sequence run cannot be reproduced from today's event logs: the 27 Sep backfill added events stamped before
that run loaded (1,451 sessions carry events before 26 Sep 21:47 today, against the 1,437 it saw). So instead of matching
its printed figures, this runs both trainers on one input and compares every count.

    python check_lag_train_equals_train.py [UNTIL_EPOCH]
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import study_sequence_model as S  # noqa: E402
import study_lag_edges as L  # noqa: E402

t0 = time.time()
cut = json.load(open(os.path.join(S.STUDY, "cut.json")))["D"]
by = S.load()
if len(sys.argv) > 1:
    until = float(sys.argv[1])
    by = {s: [e for e in evs if e[0] <= until] for s, evs in by.items()}
    by = {s: evs for s, evs in by.items() if evs}
a_trans, a_co, a_pop = S.train(by, cut)
b_trans, b_co, b_pop, lag_t, lag_c = L.train_lag(by, cut)


def same(x, y):
    kx = {k for k, v in x.items() if v}
    ky = {k for k, v in y.items() if v}
    return kx == ky and all(dict(x[k]) == dict(y[k]) for k in kx)


ok = [("sequence", same(a_trans, b_trans)), ("cooccurrence", same(a_co, b_co)), ("popularity", dict(a_pop) == dict(b_pop))]
# the lag weights never exceed one and equal one only at lag zero, so each weighted count is at most the equal one
bound = all(lag_t[t][a][b] <= a_trans[a][b] + 1e-9 for t in lag_t for a in lag_t[t] for b in lag_t[t][a])
ok.append(("lag counts bounded by equal counts", bound))
pairs = sum(len(v) for v in a_trans.values())
print("sessions %d, sources %d, directed pairs %d (%.0f s)" % (len(by), len(a_trans), pairs, time.time() - t0))
for name, good in ok:
    print(("PASS  " if good else "FAIL  ") + name)
print("FAIL" if not all(g for _, g in ok) else "PASS", "- %d of %d" % (sum(g for _, g in ok), len(ok)))
sys.exit(0 if all(g for _, g in ok) else 1)
