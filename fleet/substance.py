#!/usr/bin/env python3
"""substance.py - what is each agent actually SAYING right now? (2026-07-12)

Liveness metrics (process, cpu, transcript mtime) cannot see an agent that is alive but unable
to work - the 14:50 A4 incident ('Not logged in - Please run /login') was invisible to every
gauge and caught only by reading the latest assistant text. This prints one line per registered
live-ish session: the timestamp and first characters of its newest assistant message.

Red flags to scan for in the output: 'Not logged in', 'API Error', 'session limit', 'Rate
limited', a question addressed to the owner sitting unanswered, or the same text twice in a row
across runs (a stuck loop). Anything else is an agent thinking in its own words - leave it be.

Usage: python -X utf8 substance.py [sid-prefix ...]   (no args = all registered non-disabled)
"""
import json, os, glob, sys

HOME = os.path.expanduser("~")
REG = os.path.join(HOME, ".claude", "fleet", "registry")
PROJROOT = os.path.join(HOME, ".claude", "projects")


def last_text(sid):
    fs = glob.glob(os.path.join(PROJROOT, "*", sid + "*.jsonl"))
    if not fs:
        return None, None
    p = max(fs, key=os.path.getmtime)
    try:
        with open(p, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 300_000))
            lines = f.read().decode("utf-8", "replace").strip().splitlines()
    except OSError:
        return None, None
    for l in reversed(lines):
        try:
            d = json.loads(l)
        except Exception:
            continue
        if d.get("type") == "assistant":
            c = d.get("message", {}).get("content", [])
            t = " ".join(x.get("text", "") for x in c
                         if isinstance(x, dict) and x.get("type") == "text").strip()
            if t:
                return d.get("timestamp", "")[11:19] + "Z", t
    return None, None


def main():
    prefixes = [a for a in sys.argv[1:] if not a.startswith("-")]
    rows = []
    for f in sorted(glob.glob(os.path.join(REG, "*.json"))):
        try:
            e = json.load(open(f, encoding="utf-8-sig"))
        except Exception:
            continue
        if e.get("disabled"):
            continue
        sid = e.get("id") or os.path.basename(f)[:-5]
        if prefixes and not any(sid.startswith(p) for p in prefixes):
            continue
        ts, t = last_text(sid)
        if t is None:
            continue
        flag = ""
        low = t.lower()
        for bad in ("not logged in", "api error", "session limit", "rate limited"):
            if bad in low:
                flag = "  <<< FLAG"
                break
        rows.append((sid[:8], e.get("title", "")[:20], ts, t[:110].replace("\n", " "), flag))
    for sid, title, ts, t, flag in rows:
        print("%-9s %-21s %s :: %s%s" % (sid, title, ts, t, flag))
    if not rows:
        print("(no matching live sessions with assistant text)")


if __name__ == "__main__":
    main()
