"""study_unclaimed_writes.py - how often does a session edit a shared brain file with no live mesh claim of its own
covering it? (registered 28 Sep 09:5x in study/THREE_DAY_RUN_2026-09-27.md before any code; claude-825b1305)

Population: Claude transcripts touched since SINCE; their Edit, Write, MultiEdit and NotebookEdit calls whose path lies
under a shared root (below). A call is covered when one of the session's agent ids held a claim whose canonical path
covers the file at the call's time: acquired_at <= t < min(expires_at, released_at), by the mesh's own canonicalization
and containment rule (agent_mesh.canonical_resource, resource_covers). A session's agent ids are the hook's form
claude-<first 8 of the session id> and every id its own tool calls passed as --agent or MESH_AGENT= (the agents table
keeps no session key). Kinds by session_kinds.py.

    python study_unclaimed_writes.py SINCE_ISO_DATE OUT_JSON
"""
import collections
import glob
import io
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone

HOME = os.path.expanduser("~")
MESH_DIR = os.path.expanduser("~/ai-memories-and-functionality/12_cognitive_architecture/agent_mesh")
MESH_DB = os.path.join(HOME, ".agents", "coordination", "agent_mesh.sqlite3")
TRANSCRIPTS = os.path.join(HOME, ".claude", "projects", (os.environ.get("BRAIN_SLUG") or os.path.expanduser("~").replace(":", "-").replace(chr(92), "-").replace("/", "-")))
sys.path.insert(0, MESH_DIR)
sys.path.insert(0, os.path.join(HOME, ".claude", "brain_graph", "bin"))
import agent_mesh  # noqa: E402
import session_kinds  # noqa: E402

ROOTS = {
    "brain repo": os.path.expanduser("~/ai-memories-and-functionality"),
    "dreaming": os.path.join(HOME, ".claude", "dreaming"),
    "brain_graph": os.path.join(HOME, ".claude", "brain_graph"),
    "memory": os.path.join(HOME, ".claude", "projects", (os.environ.get("BRAIN_SLUG") or os.path.expanduser("~").replace(":", "-").replace(chr(92), "-").replace("/", "-")), "memory"),
    "math-brain-build": os.path.expanduser("~/math-brain-build"),
}
TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
AGENT_ARG = re.compile(r"(?:--agent[ =]+|MESH_AGENT=)[\"']?([A-Za-z0-9][A-Za-z0-9._:-]{2,80})")


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() if s else None


def canon(p):
    try:
        return agent_mesh.canonical_resource(p)[0]
    except Exception:
        return "path:" + os.path.abspath(p).lower()


def root_of(p):
    ap = os.path.abspath(p).lower()
    for name, r in ROOTS.items():
        if ap.startswith(os.path.abspath(r).lower() + os.sep):
            return name
    return None


def main():
    since, out = sys.argv[1], sys.argv[2]
    t_since = datetime.fromisoformat(since).replace(tzinfo=timezone.utc).timestamp()
    db = sqlite3.connect("file:%s?mode=ro" % MESH_DB.replace("\\", "/"), uri=True)
    claims = collections.defaultdict(list)
    for a, cr, acq, exp, rel in db.execute("SELECT agent_id, canonical_resource, acquired_at, expires_at, released_at "
                                           "FROM claims WHERE acquired_at >= ?", (since,)):
        end = min(x for x in (ts(exp), ts(rel)) if x is not None) if (exp or rel) else float("inf")
        claims[a].append((cr, ts(acq), end))
    calls, agent_ids = [], collections.defaultdict(set)
    for f in glob.glob(os.path.join(TRANSCRIPTS, "*.jsonl")):
        if os.path.getmtime(f) < t_since:
            continue
        sid = os.path.basename(f)[:-6]
        agent_ids[sid].add("claude-" + sid[:8])
        with io.open(f, encoding="utf-8", errors="replace") as fh:
            for ln in fh:
                if '"tool_use"' not in ln:
                    continue
                try:
                    o = json.loads(ln)
                except Exception:
                    continue
                t = ts(o.get("timestamp"))
                for b in (o.get("message") or {}).get("content") or []:
                    if not isinstance(b, dict) or b.get("type") != "tool_use":
                        continue
                    inp = b.get("input") or {}
                    if b.get("name") == "Bash" or b.get("name") == "PowerShell":
                        for m in AGENT_ARG.finditer(str(inp.get("command", ""))):
                            agent_ids[sid].add(m.group(1))
                    if b.get("name") in TOOLS and t and t >= t_since:
                        p = inp.get("file_path") or inp.get("notebook_path")
                        if p and root_of(p):
                            calls.append((sid, t, b["name"], p))
    # post-hoc (28 Sep 09:5x, labelled so): an uncovered edit into a path ANOTHER agent held under a live claim at that
    # moment - the subset where a check reading the mesh would name a real collision rather than a missing formality
    others = []
    for a, lst in claims.items():
        for cr, a0, a1 in lst:
            others.append((a, cr, a0, a1))
    purpose = {}
    for a, cr, acq, p in db.execute("SELECT agent_id, canonical_resource, acquired_at, purpose FROM claims WHERE "
                                     "acquired_at >= ?", (since,)):
        purpose[(a, cr, ts(acq))] = p
    rows, tally = [], collections.Counter()
    for sid, t, tool, p in calls:
        target = canon(p)
        mine = agent_ids[sid]
        cov = any(agent_mesh.resource_covers(cr, target) and a0 <= t < a1
                  for a in mine for cr, a0, a1 in claims.get(a, ()))
        clash = None
        if not cov:
            for a, cr, a0, a1 in others:
                if a not in mine and a0 <= t < a1 and agent_mesh.resource_covers(cr, target):
                    clash = {"holder": a, "holder_purpose": (purpose.get((a, cr, a0)) or "")[:120],
                             "held": cr[5:] if cr.startswith("path:") else cr}
                    break
        k = session_kinds.kind(sid)
        tally[(k, root_of(p), cov)] += 1
        rows.append({"sid": sid[:8], "kind": k, "t": datetime.fromtimestamp(t).strftime("%m-%d %H:%M"), "tool": tool,
                     "root": root_of(p), "covered": cov, "file": os.path.basename(p), "path": p, "clash": clash})
    by_kind = collections.defaultdict(lambda: [0, 0])
    by_root = collections.defaultdict(lambda: [0, 0])
    for (k, r, cov), n in tally.items():
        by_kind[k][0 if cov else 1] += n
        by_root[r][0 if cov else 1] += n
    sessions = collections.defaultdict(lambda: [0, 0])
    for r in rows:
        sessions[(r["sid"], r["kind"])][0 if r["covered"] else 1] += 1
    # post-hoc: the collision the mesh exists for - two different sessions editing the same file within 10 minutes,
    # claimed or not; the files and the pairs, each pair counted once per file per 10-minute window
    by_file = collections.defaultdict(list)
    for (sid, t, tool, p), r in zip(calls, rows):
        by_file[canon(p)].append((t, sid, r["covered"]))
    near = []
    for f, evs in by_file.items():
        evs.sort()
        for i, (t1, s1, c1) in enumerate(evs):
            for t2, s2, c2 in evs[i + 1:]:
                if t2 - t1 > 600:
                    break
                if s2 != s1:
                    near.append({"file": os.path.basename(f[5:] if f.startswith("path:") else f),
                                 "first": datetime.fromtimestamp(t1).strftime("%m-%d %H:%M"), "a": s1[:8], "b": s2[:8],
                                 "gap_s": round(t2 - t1), "claimed_a": c1, "claimed_b": c2})
                    break
    res = {"since": since, "calls": len(rows), "covered_uncovered_by_kind": dict(by_kind),
           "covered_uncovered_by_root": dict(by_root),
           "sessions_with_shared_edits": len(sessions),
           "sessions_all_uncovered": sum(1 for v in sessions.values() if v[0] == 0),
           "sessions_all_covered": sum(1 for v in sessions.values() if v[1] == 0),
           "posthoc_uncovered_into_another_agents_live_claim": sum(1 for r in rows if r["clash"]),
           "posthoc_clashes": [r for r in rows if r["clash"]][:200],
           "posthoc_two_sessions_same_file_within_10_min": len(near),
           "posthoc_near_pairs": near[:200],
           "per_session": {"%s %s" % k: v for k, v in sorted(sessions.items(), key=lambda kv: -sum(kv[1]))},
           "rows": rows}
    json.dump(res, io.open(out, "w", encoding="utf-8"), indent=1)
    print(json.dumps({k: v for k, v in res.items() if k not in ("rows", "per_session", "posthoc_clashes",
                                                                   "posthoc_near_pairs")}, indent=1))
    for r in res["posthoc_near_pairs"][:20]:
        print("   near", r["first"], r["file"], r["a"], r["b"], "%ds" % r["gap_s"], "claimed:", r["claimed_a"], r["claimed_b"])
    for r in res["posthoc_clashes"][:15]:
        print("   clash", r["sid"], r["kind"], r["t"], r["tool"], r["file"], "| held by", r["clash"]["holder"], "|",
              r["clash"]["holder_purpose"][:70])
    for k, v in list(res["per_session"].items())[:12]:
        print("  ", k, "covered %d, uncovered %d" % tuple(v))


if __name__ == "__main__":
    main()
