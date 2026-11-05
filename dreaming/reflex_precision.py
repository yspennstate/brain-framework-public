#!/usr/bin/env python3
"""reflex_precision.py - one number per reflex for "how often was this fire on-domain when a judge looked",
from the nightly judged log, so the priming hook can fire less and say how confident each fire is.

Source: judged_nightly.jsonl - the five target trading reflexes since 07-16 and, since 2026-09-02, the
firehose stratum (six of the day's top firers a night, plus a 30-case hand-judged sample). A row's
domain_at_play is yes / no / unclear; precision counts yes over (yes + no), unclear abstains. Laplace-
smoothed so a reflex with two judged rows does not read as 0 or 1. Reflexes with fewer than MIN_N judged
rows are reported as unjudged and the hook treats them as before.

Writes reflex_precision.json: {reflex_id: {"n": .., "on_domain": .., "useful": .., "followed_when_on": ..,
"p": smoothed precision, "raw": on_domain/n}}. Nightly, after the dream's judge pass; safe to run any time
(read-only over the log). Owner order 2026-09-02 09:1x: make the brain fire less and be more confident
when it fires.
"""
import collections
import io
import json
import os
import sys
import time

if any(a in ("-h", "--help") for a in sys.argv[1:]):
    # Discovery is not execution: this file does its work at module scope, so the
    # guard must come before the first statement that runs.
    print(__doc__ or os.path.basename(__file__))
    raise SystemExit(0)


HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "judged_nightly.jsonl")
OUT = os.path.join(HERE, "reflex_precision.json")
# a trigger rewritten to its referent is a NEW trigger (2026-09-02: eleven were): the judged fires of the old
# form must not score it. {reflex_id: "YYYY-MM-DD HH:MM"} - trigger-path rows fired before that time, and rows
# with no fired_at at all (they predate the record), leave the overall table; the graph path is untouched.
REWRITES = os.path.join(HERE, "trigger_rewrites.json")


def _rewrites():
    try:
        d = json.load(io.open(REWRITES, encoding="utf-8"))
        return {k: str(v).replace("T", " ")[:16] for k, v in d.items() if not k.startswith("_") and v}
    except (OSError, ValueError):
        return {}
MIN_N = 4


def main():
    rows = []
    if os.path.exists(LOG):
        for ln in io.open(LOG, encoding="utf-8", errors="replace"):
            try:
                rows.append(json.loads(ln))
            except ValueError:
                # a concurrent append without a trailing newline glues two rows onto one line (seen 2026-09-02:
                # the nap's last row + a hand row); split at the object boundary rather than drop both silently
                parts, buf, depth = [], "", 0
                for ch in ln:
                    buf += ch
                    depth += (ch == "{") - (ch == "}")
                    if depth == 0 and buf.strip():
                        parts.append(buf.strip()); buf = ""
                for part in parts:
                    try:
                        rows.append(json.loads(part))
                    except ValueError:
                        continue
    # one verdict per case: the nightly judge re-judged some early cases on later nights under the same
    # case_id; keep the LAST verdict per (case_id, reflex). The reflex is part of the key because sheet
    # case_ids carried only minute resolution until 2026-09-02 and two sheets generated in the same minute
    # reused ids for different cases (keying on case_id alone dropped outside_view from 10 rows to 5).
    last = {}
    for r in rows:
        cid = r.get("case_id")
        last[(cid, r.get("instinct")) if cid else id(r)] = r
    rows = list(last.values())
    agg = collections.defaultdict(lambda: {"yes": 0, "no": 0, "unclear": 0, "useful": 0, "followed_when_on": 0})
    # Suppressed-stratum rows (fire_judge_nightly J3, since 2026-09-02) judge fires the hook did NOT show.
    # They must not enter a reflex's precision (that is "how often was a SHOWN fire on-domain"); they
    # measure the gates' cost instead, kept separately per reason.
    gates = collections.defaultdict(lambda: {"yes": 0, "no": 0, "unclear": 0, "useful": 0})
    # per firing path (regex = the reflex's own trigger, graph = an association near a scar, ff = forward
    # feed), from the row's `path` (judge sheets carry it since 2026-09-02; older rows were backfilled from the
    # fire log where the match was unambiguous). The hook gates a graph-path fire on ITS OWN judged precision.
    paths = collections.defaultdict(lambda: collections.defaultdict(lambda: {"yes": 0, "no": 0}))
    rewrites = _rewrites()
    dropped = collections.Counter()
    for r in rows:
        rid = r.get("instinct")
        if not rid:
            continue
        d = str(r.get("domain_at_play", "")).lower()
        pth = str(r.get("path") or "").strip().lower()
        if rid in rewrites and pth not in ("graph", "ff") and str(r.get("stratum", "")).lower() != "suppressed":
            fired = str(r.get("fired_at") or "").replace("T", " ")[:16]
            if not fired or fired < rewrites[rid]:
                dropped[rid] += 1
                continue
        if pth and str(r.get("stratum", "")).lower() != "suppressed" and d in ("yes", "true", "1", "no", "false", "0"):
            paths[rid][pth]["yes" if d in ("yes", "true", "1") else "no"] += 1
        if str(r.get("stratum", "")).lower() == "suppressed":
            g = gates[str(r.get("reason", "?"))]
            g["yes" if d in ("yes", "true", "1") else "no" if d in ("no", "false", "0") else "unclear"] += 1
            if str(r.get("useful", "")).lower() in ("yes", "true", "1"):
                g["useful"] += 1
            continue
        if pth in ("graph", "ff"):
            # an association's fire is judged on its own path above; it says nothing about the trigger, so it
            # stays out of the overall precision the hook ranks the trigger's fires by (2026-09-02 16:2x: the
            # graph-path rows, all off-domain from the priming bug, had pulled six scars' overall p under 0.40).
            # It comes AFTER the suppressed branch: a suppressed graph fire still counts toward its gate's cost.
            continue
        if d in ("yes", "true", "1"):
            agg[rid]["yes"] += 1
            if str(r.get("followed", "")).lower() in ("yes", "true", "1"):
                agg[rid]["followed_when_on"] += 1
        elif d in ("no", "false", "0"):
            agg[rid]["no"] += 1
        else:
            agg[rid]["unclear"] += 1
        if str(r.get("useful", "")).lower() in ("yes", "true", "1"):
            agg[rid]["useful"] += 1
    out = {"built": time.strftime("%Y-%m-%dT%H:%M:%S"), "min_n": MIN_N, "rows": len(rows), "reflexes": {},
           # the gates' measured cost: suppressed fires the judge found on-domain, per reason (J3 stratum)
           "gates": {k: dict(v, cost_rate=(v["yes"] / float(v["yes"] + v["no"])) if (v["yes"] + v["no"]) else None)
                     for k, v in sorted(gates.items())}}
    empty = {"yes": 0, "no": 0, "unclear": 0, "useful": 0, "followed_when_on": 0}
    for rid in sorted(set(agg) | set(paths)):
        # a reflex whose only judged fires came by the graph path still gets an entry (overall n = 0, its
        # path table filled) so the hook can gate that path on its own record
        a = agg.get(rid, empty)
        n = a["yes"] + a["no"]
        out["reflexes"][rid] = {"n": n, "on_domain": a["yes"], "unclear": a["unclear"], "useful": a["useful"],
                                "followed_when_on": a["followed_when_on"],
                                "p": (a["yes"] + 1.0) / (n + 2.0), "raw": (a["yes"] / n) if n else None,
                                "judged": n >= MIN_N,
                                "reset_at": rewrites.get(rid), "dropped_before_rewrite": dropped.get(rid, 0),
                                "paths": {k: {"n": v["yes"] + v["no"], "on_domain": v["yes"],
                                              "p": (v["yes"] + 1.0) / (v["yes"] + v["no"] + 2.0),
                                              "judged": (v["yes"] + v["no"]) >= MIN_N}
                                          for k, v in sorted(paths.get(rid, {}).items())}}
    tmp = OUT + ".tmp.%d" % os.getpid()
    with io.open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    os.replace(tmp, OUT)
    judged = {k: v for k, v in out["reflexes"].items() if v["judged"]}
    print("reflex precision: %d judged rows, %d reflexes with >= %d rows -> %s" % (len(rows), len(judged), MIN_N, OUT))
    for k, v in sorted(judged.items(), key=lambda kv: kv[1]["p"]):
        print("  %-40s n=%3d on-domain=%3d p=%.2f" % (k, v["n"], v["on_domain"], v["p"]))
    gp = [(k, v["paths"]["graph"]) for k, v in out["reflexes"].items() if v.get("paths", {}).get("graph", {}).get("judged")]
    if gp:
        print("graph-path precision (judged at n >= %d; the hook gates a graph fire below p %.2f):" % (MIN_N, 0.25))
        for k, v in sorted(gp, key=lambda kv: kv[1]["p"]):
            print("  %-40s n=%3d on-domain=%3d p=%.2f" % (k, v["n"], v["on_domain"], v["p"]))
    if out["gates"]:
        print("gate cost (suppressed fires the judge found on-domain, per reason):")
        for k, v in out["gates"].items():
            print("  %-12s judged=%3d on-domain=%3d unclear=%2d cost_rate=%s" % (
                k, v["yes"] + v["no"], v["yes"], v["unclear"], "n/a" if v["cost_rate"] is None else "%.2f" % v["cost_rate"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
