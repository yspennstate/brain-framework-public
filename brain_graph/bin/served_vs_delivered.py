"""served_vs_delivered.py - did the membrane's served lines reach the session whole? (27 Sep, claude-825b1305; scheduled
audit account02/B08 auto11, R12: 'served' is logged before the dispatcher fits every hook's text into its shared budget).

For every line in live/served.jsonl, the session's transcript records stamped from 5 s before to WINDOW s after the serve
are searched for the line's rendered fragment: "this reminds me of `<slug>`" (memory and word lines) or "usually go next
to `<place>`" (place lines, the name shortened as live_membrane.short renders it). WHOLE = found and the [subconscious]
line that holds it runs to its closing "(hint: ...)" or "hint: ...)"; CUT = found without its close; ABSENT = not found;
NO_TRANSCRIPT = the session has no Claude transcript. Prints counts and, for the lines not WHOLE, the session prefix,
kind and the reason; never the text of the line.

    python served_vs_delivered.py [--window 180] [--since EPOCH]
"""
import datetime
import glob
import json
import os
import sys

HOME = os.path.expanduser("~")
LIVE = os.path.join(HOME, ".claude", "brain_graph", "live")
PROJ = os.path.join(HOME, ".claude", "projects")
a = sys.argv[1:]
WINDOW = float(a[a.index("--window") + 1]) if "--window" in a else 180.0
SINCE = float(a[a.index("--since") + 1]) if "--since" in a else 0.0


def short(nm, n=46):                       # live_membrane.short, verbatim
    kd, _, rest = nm.partition(":")
    rest = rest.replace(os.path.expanduser("~"), "~")
    if len(rest) > n:
        rest = "..." + rest[-(n - 3):]
    return "%s:%s" % (kd, rest)


def fragment(kind, key):
    if kind in ("mem", "word"):
        return "this reminds me of `%s`" % key
    if kind == "place":
        return "usually go next to `%s`" % short(key[len("place:"):] if key.startswith("place:") else key, 70)
    return None


def strings(o):
    if isinstance(o, str):
        yield o
    elif isinstance(o, dict):
        for v in o.values():
            yield from strings(v)
    elif isinstance(o, list):
        for v in o:
            yield from strings(v)


def ts(rec):
    t = rec.get("timestamp")
    if not t:
        return None
    try:
        return datetime.datetime.fromisoformat(t.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


_cache = {}


def transcript(sid):
    if sid in _cache:
        return _cache[sid]
    hits = glob.glob(os.path.join(PROJ, "*", sid + ".jsonl")) or glob.glob(os.path.join(PROJ, "*", "*", sid + ".jsonl"))
    recs = None
    if hits:
        recs = []
        with open(hits[0], encoding="utf-8", errors="replace") as fh:
            for ln in fh:
                if "[subconscious]" not in ln:
                    continue
                try:
                    r = json.loads(ln)
                except ValueError:
                    continue
                t = ts(r)
                if t is not None:
                    recs.append((t, r))
    _cache[sid] = recs
    return recs


rows = [json.loads(l) for l in open(os.path.join(LIVE, "served.jsonl"), encoding="utf-8") if l.strip()]
rows = [r for r in rows if float(r["t"]) >= SINCE]
# a line held back by design was never meant to reach the transcript: its own state, outside the delivery counts
# (28 Sep, account03/B08 R12, B08-R12-HOLD-MEASUREMENT-011; until then it read as ABSENT)
out = {"WHOLE": 0, "CUT": 0, "ABSENT": 0, "NO_TRANSCRIPT": 0, "HELD_BY_DESIGN": 0}
by_kind = {}
notes = []
for r in rows:
    t0 = float(r["t"])
    recs = transcript(r["sid"])
    for kind, key, _by in r["served"]:
        frag = fragment(kind, key)
        verdict = "ABSENT"
        if kind == "held":
            verdict = "HELD_BY_DESIGN"
        elif recs is None:
            verdict = "NO_TRANSCRIPT"
        else:
            for t, rec in recs:
                if not (t0 - 5 <= t <= t0 + WINDOW):
                    continue
                for s in strings(rec):
                    i = s.find(frag) if frag else -1
                    if i < 0:
                        continue
                    start = s.rfind("[subconscious]", 0, i)
                    end = s.find("\n", i)
                    line = s[start if start >= 0 else i: end if end >= 0 else len(s)].rstrip()
                    verdict = "WHOLE" if ("hint:" in line and line.endswith(")")) else "CUT"
                    if verdict == "WHOLE":
                        break
                if verdict == "WHOLE":
                    break
        out[verdict] += 1
        by_kind.setdefault(kind, {}).setdefault(verdict, 0)
        by_kind[kind][verdict] += 1
        if verdict not in ("WHOLE", "HELD_BY_DESIGN"):
            notes.append((r["sid"][:8], kind, r.get("ep", ""), verdict))
with_t = out["WHOLE"] + out["CUT"] + out["ABSENT"]
print(json.dumps({"rows": len(rows), "lines": sum(v for k, v in out.items() if k != "HELD_BY_DESIGN"),
                  "held_by_design": out["HELD_BY_DESIGN"], "counts": out, "by_kind": by_kind,
                  "whole_share_of_lines_with_a_transcript": round(out["WHOLE"] / with_t, 3) if with_t else None}, indent=1))
for n in notes:
    print("  not whole:", n)
