"""score_corrections_per_session.py - the membrane's correction line scored per session, so one session cannot carry
the result (27 Sep, claude-825b1305; pre-registered 13:1x in study/TWO_DAY_RUN_2026-09-26.md).

Reads a replay's rows file (study_live_membrane.py writes study/rows_<stamp>.jsonl; a row per moment with sid,
verdict_score and verdict_got = a correction or repeat of his followed within 30 min). The bar is the top FRAC of all
moments that carry a correction line. Over his interactive sessions (session_kinds 'his'):
    CAUGHT        a session with a correction in which the line fired at the bar and a correction followed within 30 min
    FALSE ALARM   a session with no correction in which the line fired at the bar at all
Prints counts only.

    python score_corrections_per_session.py ROWS_JSONL [--frac 0.05]
"""
import collections
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import session_kinds  # noqa: E402

a = sys.argv[1:]
path = a[0]
FRAC = float(a[a.index("--frac") + 1]) if "--frac" in a else 0.05
rows = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
vr = [r for r in rows if "verdict_score" in r]
scores = sorted((r["verdict_score"] for r in vr), reverse=True)
thr = scores[max(0, int(round(FRAC * len(scores))) - 1)] if scores else float("inf")
by = collections.defaultdict(list)
for r in vr:
    by[str(r.get("sid"))[:8]].append(r)
kind = {s: session_kinds.kind(s) for s in by}
out = {"rows": os.path.basename(path), "frac": FRAC, "threshold": round(thr, 3), "moments": len(vr), "kinds": {}}
for kd in sorted(set(kind.values())) + ["all"]:
    ss = [s for s in by if kd == "all" or kind[s] == kd]
    with_corr = [s for s in ss if any(r.get("verdict_got") for r in by[s])]
    without = [s for s in ss if s not in with_corr]
    caught = [s for s in with_corr if any(r.get("verdict_got") and r["verdict_score"] >= thr for r in by[s])]
    alarms = [s for s in without if any(r["verdict_score"] >= thr for r in by[s])]
    cs = len(caught) / len(with_corr) if with_corr else None
    fa = len(alarms) / len(without) if without else None
    out["kinds"][kd] = {"sessions": len(ss), "with_correction": len(with_corr), "caught": len(caught),
                        "caught_share": round(cs, 3) if cs is not None else None,
                        "without_correction": len(without), "false_alarms": len(alarms),
                        "false_alarm_share": round(fa, 3) if fa is not None else None,
                        "ratio": round(cs / fa, 2) if cs is not None and fa else None}
print(json.dumps(out, indent=1))
