"""study_scar_fire_before_repeat.py - when he had to restate one of his standing orders, did a reflex for that order
exist, and did it fire before he had to? (28 Sep 08:5x, claude-825b1305; registered in study/THREE_DAY_RUN_2026-09-27.md
before any code.)

Each labelled correction (results/STANDING_ORDERS_LABELS_2026-09-27.json) names the orders it restated as memory slugs.
A reflex is linked to an order when its source names the slug (strict) or any of its fields does (loose), and counts only
if it existed before the correction (a learned reflex by its "added" stamp; a seed reflex is taken to exist). Each case
gets one class, first match wins:
  no linked reflex
  fired between his ask and his correction          (instinct_fires logs, the correction's session)
  matched there but held back                        (instinct_suppressed logs: the cap or the backoff)
  fired earlier in the session only
  never matched in the session
The registered classes never looked at holds BEFORE his ask, so a reflex matched and held earlier in the session fell
to "never matched in the session" (case 38: two holds before the ask; scheduled audit account02/B13 auto11, R38,
B13-R38-REGISTERED-NEVER-MATCHED-IGNORES-PREASK-SUPPRESSION-024). Since 28 Sep 14:1x each row also carries
held_before_ask and class_with_preask_holds, where such a case reads "matched earlier in the session, held back each
time"; the registered "class" and "classes" are kept as registered.

    python study_scar_fire_before_repeat.py LABELS_JSON OUT_JSON
"""

import sys
if __name__ == "__main__" and any(arg in ("-h", "--help") for arg in sys.argv[1:]):
    print(__doc__ or "No command-line options.")
    raise SystemExit(0)

import collections
import glob
import io
import json
import os
import re
import sys
from datetime import datetime

DR = os.path.join(os.path.expanduser("~"), ".claude", "dreaming")


def load_reflexes():
    tsv = os.path.join(DR, "instincts.tsv")
    out = {}
    with io.open(tsv, encoding="utf-8") as fh:
        next(fh, None)
        for ln in fh:
            p = ln.rstrip("\n").split("\t")
            if len(p) >= 7:
                out[p[0]] = {"id": p[0], "gut": p[4], "sting": p[5], "source": p[6], "added": None}
    for e in json.load(io.open(os.path.join(DR, "instincts_learned.json"), encoding="utf-8")):
        if e.get("id") in out:
            out[e["id"]]["added"] = e.get("added")
    return out


def added_epoch(s):
    """'2026-09-27 14:2x (...)' -> the start of that ten-minute window, local time; None when absent or unreadable"""
    m = re.match(r"(\d{4}-\d\d-\d\d)(?:[ T](\d\d):(\d)[\dx])?", s or "")
    if not m:
        return None
    hh, tens = (int(m.group(2)), int(m.group(3))) if m.group(2) else (0, 0)
    return datetime.strptime(m.group(1), "%Y-%m-%d").replace(hour=hh, minute=tens * 10).timestamp()


def read_log(pattern):
    rows = []
    for p in sorted(glob.glob(os.path.join(DR, pattern))):
        with io.open(p, encoding="utf-8", errors="replace") as fh:
            for ln in fh:
                f = ln.rstrip("\n").split("\t")
                if len(f) >= 6 and f[0].isdigit():          # a 5-column row (early July) carries no session
                    rows.append(f)
    return rows


def main():
    lab = json.load(io.open(sys.argv[1], encoding="utf-8"))["preventable"]
    refl = load_reflexes()
    fires = read_log("instinct_fires*.tsv*")        # epoch, event, kind, reflex, input, session, method
    supp = read_log("instinct_suppressed*.tsv*")    # epoch, event, kind, reflex, reason, session
    rows, tally, tally2 = [], collections.Counter(), collections.Counter()
    for c in lab:
        orders = c["orders"] if isinstance(c["orders"], list) else json.loads(c["orders"].replace("'", '"'))
        t, ask = float(c["t"]), float(c.get("ask_t") or c["t"])
        sid = str(c["sid"])[:8]
        strict, loose = set(), set()
        for r in refl.values():
            a = added_epoch(r["added"]) if r["added"] else None
            if a is not None and a >= t:
                continue
            for o in orders:
                if o in r["source"]:
                    strict.add(r["id"])
                if o in (r["gut"] + r["sting"] + r["source"] + r["id"]):
                    loose.add(r["id"])
        linked = strict or loose
        fired_win = sorted({f[3] for f in fires if f[3] in linked and f[5].startswith(sid) and ask - 5 <= int(f[0]) <= t})
        held_win = sorted({f[3] + ":" + f[4] for f in supp if f[3] in linked and f[5].startswith(sid) and ask - 5 <= int(f[0]) <= t})
        fired_before = sorted({f[3] for f in fires if f[3] in linked and f[5].startswith(sid) and int(f[0]) < ask - 5})
        held_before = sorted({f[3] for f in supp if f[3] in linked and f[5].startswith(sid) and int(f[0]) < ask - 5})
        if not linked:
            cls = "no linked reflex"
        elif fired_win:
            cls = "fired between his ask and his correction"
        elif held_win:
            cls = "matched there but held back"
        elif fired_before:
            cls = "fired earlier in the session only"
        else:
            cls = "never matched in the session"
        tally[cls] += 1
        cls2 = ("matched earlier in the session, held back each time"
                if cls == "never matched in the session" and held_before else cls)
        tally2[cls2] += 1
        rows.append({"n": c["n"], "theme": c["theme"], "sid": sid, "orders": orders, "linked_strict": sorted(strict),
                     "linked_loose_only": sorted(loose - strict), "fired_in_window": fired_win, "held_in_window": held_win,
                     "fired_before_ask": fired_before, "held_before_ask": held_before, "class": cls,
                     "class_with_preask_holds": cls2, "minutes_ask_to_correction": round((t - ask) / 60, 1)})
    out = {"cases": len(rows), "classes": dict(tally), "classes_with_preask_holds": dict(tally2), "rows": rows,
           "fire_log_rows": len(fires), "suppression_log_rows": len(supp)}
    json.dump(out, io.open(sys.argv[2], "w", encoding="utf-8"), indent=1)
    print(json.dumps({"cases": len(rows), "classes": dict(tally)}, indent=1))


if __name__ == "__main__":
    main()
