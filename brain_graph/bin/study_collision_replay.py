"""study_collision_replay.py - how often would a collision check on shared brain files have spoken last week?
(registered 28 Sep 11:1x in THREE_DAY_RUN_2026-09-27.md; claude-825b1305)

Reads the 2,919 Edit/Write calls of 21-28 Sep from UNCLAIMED_WRITES_2026-09-28.json (session, minute, root, file) and
the mesh claims table. For W in 10, 30 and 60 minutes, a FIRE is the first edit of a (session, file) pair that falls
within W minutes after another session's edit of the same file, or inside a live claim of another agent covering the
file. Later edits of the same pair within W of the fire are not fires again (one line per session per file per window).
Every fire is listed with the other session, the gap and both sessions' edit counts on the file, for reading by hand.

    python study_collision_replay.py OUT_JSON [ROWS_JSON]
ROWS_JSON defaults to UNCLAIMED_WRITES_PATHS_2026-09-28.json (2,948 calls, each with its full path; committed beside
the result). The output names its input by sha256 and row count. Until 28 Sep 14:0x the default was
UNCLAIMED_WRITES_2026-09-28.json (file names only), so a run of the committed default matched every REPORT.md, PREREG.md
or LEDGER.md in any folder as one file and gave 20 fires at W = 30, not the 10 the full paths give (account03/B19 r61,
B19-R41-REPLAY-FULLPATH-INPUT-UNSEALED-20260928). The claim arm reads the local mesh database and is not reproducible
from committed files.
"""

import sys
if __name__ == "__main__" and any(arg in ("-h", "--help") for arg in sys.argv[1:]):
    print(__doc__ or "No command-line options.")
    raise SystemExit(0)

import collections
import io
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta

R = sys.argv[2] if len(sys.argv) > 2 else os.path.expanduser(r"~\.claude\brain_graph\results\UNCLAIMED_WRITES_PATHS_2026-09-28.json")
MESH = os.path.expanduser(r"~\.agents\coordination\agent_mesh.sqlite3")
YEAR = 2026


def t_of(s):
    return datetime.strptime("%d-%s" % (YEAR, s), "%Y-%m-%d %H:%M")


def main():
    import hashlib
    rows = json.load(io.open(R, encoding="utf-8"))["rows"]
    source = {"file": os.path.basename(R), "sha256": hashlib.sha256(open(R, "rb").read()).hexdigest(),
              "rows": len(rows), "rows_with_path": sum(1 for r in rows if r.get("path"))}
    print("input:", json.dumps(source))
    # a row with its full path is matched on it (the first run matched on the file name, and REPORT.md, PREREG.md and
    # LEDGER.md exist in many folders)
    edits = sorted(({"sid": r["sid"], "kind": r["kind"], "t": t_of(r["t"]),
                     "key": (r["root"], os.path.normcase(r.get("path") or r["file"]))}
                    for r in rows), key=lambda e: e["t"])
    by_file = collections.defaultdict(list)
    for e in edits:
        by_file[e["key"]].append(e)
    # claims of agents named after a session (claude-<8 hex>) are that session's; others belong to no edit session
    db = sqlite3.connect("file:%s?mode=ro" % MESH.replace("\\", "/"), uri=True)
    claims = [(a, res.lower(), datetime.fromisoformat(acq.replace("Z", "+00:00")).replace(tzinfo=None),
               datetime.fromisoformat(exp.replace("Z", "+00:00")).replace(tzinfo=None),
               datetime.fromisoformat(rel.replace("Z", "+00:00")).replace(tzinfo=None) if rel else None)
              for a, res, acq, exp, rel in db.execute(
                  "select agent_id, canonical_resource, acquired_at, expires_at, released_at from claims "
                  "where acquired_at >= '2026-09-20'")]
    out = {"input": source}
    for W in (10, 30, 60):
        fires, last_fire = [], {}
        for key, lst in by_file.items():
            for i, e in enumerate(lst):
                pair = (e["sid"], key)
                if pair in last_fire and e["t"] - last_fire[pair] <= timedelta(minutes=W):
                    continue
                prior = [p for p in lst[:i] if p["sid"] != e["sid"] and e["t"] - p["t"] <= timedelta(minutes=W)]
                if prior:
                    o = prior[-1]
                    fires.append({"t": e["t"].strftime("%m-%d %H:%M"), "sid": e["sid"], "kind": e["kind"],
                                  "root": key[0], "file": key[1], "other": o["sid"], "other_kind": o["kind"],
                                  "gap_min": int((e["t"] - o["t"]).total_seconds() // 60),
                                  "edits_this": sum(1 for x in lst if x["sid"] == e["sid"]),
                                  "edits_other": sum(1 for x in lst if x["sid"] == o["sid"])})
                    last_fire[pair] = e["t"]
        by_pair = collections.Counter((f["sid"], f["other"], f["file"]) for f in fires)
        out["W%d" % W] = {"fires": len(fires), "distinct_file_pairs": len(by_pair),
                          "his_sessions": sum(1 for f in fires if f["kind"] == "his"), "rows": fires}
        print("W=%d: %d fires over %d (session, other, file) pairs" % (W, len(fires), len(by_pair)))
    # the claim arm: edits by one session inside another agent's live claim on the same file name (UTC shift: the
    # rows are local UTC+3, the claims UTC)
    inside = 0
    for e in edits:
        tu = e["t"] - timedelta(hours=3)
        for a, res, acq, exp, rel in claims:
            if a.startswith("claude-" + e["sid"]):
                continue
            end = min(exp, rel) if rel else exp
            path = os.path.normcase(e["key"][1]).replace("/", "\\")
            if acq <= tu < end and (res == "path:" + path or (os.sep in path and path.startswith(res[5:].rstrip("\\") + "\\"))
                                    or (os.sep not in path and res.endswith("\\" + path))):
                inside += 1
                break
    out["edits_inside_another_agents_live_claim"] = inside
    print("edits inside another agent's live claim on that file:", inside)
    json.dump(out, io.open(sys.argv[1], "w", encoding="utf-8"), indent=1, ensure_ascii=False)


if __name__ == "__main__":
    main()
