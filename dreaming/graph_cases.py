"""Judge cases for GRAPH-PATH reflex fires (fire-log path column == graph) since a local time.

One case per (reflex, session, minute) with the tool input at the fire (from the session transcript, within
3 s) and the next two tool calls, in the judged-log shape (case_id, fired_at, path, proxy, instinct) so a
judge's rows land in judged_nightly.jsonl with the path attached. Skips fires already judged (same instinct
and fired_at). Writes judge_sheets/graph_<stamp>.json beside this file and prints the cases compactly.

    python graph_cases.py "2026-09-02 09:30" 40

Read-only. Used 2026-09-02 to judge forty graph fires by hand (2 on-domain) and to find that 39 of them
would not have fired unprimed - see the_graph_path_was_priming_itself memory and reflex_precision.py.
"""
import glob
import io
import json
import os
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.expanduser("~/.claude/projects")
OUT_DIR = os.path.join(HERE, "judge_sheets")


def _judged():
    seen = set()
    p = os.path.join(HERE, "judged_nightly.jsonl")
    if not os.path.exists(p):
        return seen
    for line in io.open(p, encoding="utf-8", errors="replace"):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue
        seen.add((r.get("instinct"), r.get("fired_at")))
    return seen


def _fires(cut, path_name="graph"):
    """(ts, reflex, session, proxy, reason) rows. path_name selects the fire log's path column (regex, graph,
    ff); 'suppressed' reads instinct_suppressed.tsv instead (ts, event, proxy, reflex, reason, session) - the
    fires a gate silenced, with the gate's reason - so the gates' cost can be judged from the same cases."""
    out = []
    if path_name == "suppressed":
        with io.open(os.path.join(HERE, "instinct_suppressed.tsv"), encoding="utf-8", errors="replace") as fh:
            for line in fh:
                p = line.rstrip("\n").split("\t")
                if len(p) < 6:
                    continue
                try:
                    ts = float(p[0])
                except ValueError:
                    continue
                if ts < cut or p[2] not in ("act", "write", "read", "prompt"):
                    continue
                out.append((ts, p[3], p[5], p[2], p[4]))
        out.sort()
        return out
    with io.open(os.path.join(HERE, "instinct_fires.tsv"), encoding="utf-8", errors="replace") as fh:
        for line in fh:
            p = line.rstrip("\n").split("\t")
            if len(p) < 7 or p[6].strip() != path_name:
                continue
            try:
                ts = float(p[0])
            except ValueError:
                continue
            if ts < cut or p[2] not in ("act", "write", "read", "prompt"):
                continue
            out.append((ts, p[3], p[5], p[2], ""))
    out.sort()
    return out


_cache = {}


def _tools(session):
    if session in _cache:
        return _cache[session]
    out = []
    for path in glob.glob(os.path.join(PROJ, "*", session + ".jsonl")):
        with io.open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if '"tool_use"' not in line:
                    continue
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                t = o.get("timestamp")
                if not t:
                    continue
                try:
                    tts = datetime.fromisoformat(t.replace("Z", "+00:00")).timestamp()
                except ValueError:
                    continue
                for c in (o.get("message", {}).get("content") or []):
                    if isinstance(c, dict) and c.get("type") == "tool_use":
                        out.append((tts, c.get("name"), json.dumps(c.get("input", {}), ensure_ascii=False)))
    out.sort()
    _cache[session] = out
    return out


def _guts():
    g = {}
    p = os.path.join(HERE, "instincts.tsv")
    if os.path.exists(p):
        for line in io.open(p, encoding="utf-8", errors="replace"):
            q = line.rstrip("\n").split("\t")
            if len(q) >= 5:
                g[q[0]] = q[4]
    return g


def build(since, limit=40, path_name="graph"):
    cut = time.mktime(time.strptime(since, "%Y-%m-%d %H:%M"))
    judged = _judged()
    guts = _guts()
    cases, seen = [], set()
    stamp = time.strftime("%m%d%H%M%S")
    fires = _fires(cut, path_name)
    for ts, rid, sid, proxy, reason in fires:
        fa = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
        if (rid, fa) in judged or (rid, sid, int(ts // 60)) in seen:
            continue
        tl = _tools(sid)
        at = [t for t in tl if abs(t[0] - ts) <= 3][:1]
        if not at:
            continue
        after = [t for t in tl if 3 < t[0] - ts <= 20 * 60][:2]
        seen.add((rid, sid, int(ts // 60)))
        case = {"case_id": "%s%s_%03d" % (path_name[0], stamp, len(cases)), "fired_at": fa,
                "stratum": "suppressed" if path_name == "suppressed" else "firehose",
                "proxy": proxy, "instinct": rid, "gut": guts.get(rid, "")[:240],
                "at_fire": ["[%s] %s" % (n, x[:600]) for _, n, x in at],
                "after": ["[%s] %s" % (n, x[:200]) for _, n, x in after]}
        if path_name == "suppressed":
            case["reason"] = reason
        else:
            case["path"] = path_name
        cases.append(case)
        if len(cases) >= limit:
            break
    return fires, cases


def main():
    since = sys.argv[1] if len(sys.argv) > 1 else time.strftime("%Y-%m-%d 00:00")
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 40
    path_name = sys.argv[3] if len(sys.argv) > 3 else "graph"
    fires, cases = build(since, limit, path_name)
    os.makedirs(OUT_DIR, exist_ok=True)
    out = os.path.join(OUT_DIR, "%s_%s.json" % (path_name, time.strftime("%Y%m%d_%H%M%S")))
    with io.open(out, "w", encoding="utf-8") as f:
        json.dump(cases, f, ensure_ascii=False, indent=1)
    print("%s-path fires since %s: %d; cases built: %d -> %s" % (path_name, since, len(fires), len(cases), out))
    for i, c in enumerate(cases):
        print("\n#%d %s %s [%s]\n  GUT: %s" % (i, c["instinct"], c["fired_at"], c["proxy"], c["gut"][:200]))
        print("  AT:", c["at_fire"][0][:420].replace("\n", " "))
        for a in c["after"]:
            print("  AFTER:", a[:150].replace("\n", " "))


if __name__ == "__main__":
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        print("usage: graph_cases.py   (help is inert; run without flags to do the work)")
        raise SystemExit(0)
    main()
