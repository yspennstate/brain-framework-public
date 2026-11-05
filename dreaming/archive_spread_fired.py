#!/usr/bin/env python3
"""archive_spread_fired.py - keep the associative push history before it is destroyed.

`.spread_fired.json` records which memories the associative pusher actually handed to which
session. spread_recall._fresh() prunes it to a HARD 24-HOUR WINDOW on every write:

    st = {k: v for k, v in st.items() if now - v < 86400}

so there is no archive and no rotation - push history older than a day is gone permanently.
(`.spread_fired_replay.json`, 2026-07-09, is a replay artifact, not a rotated generation.)

That destroyed history is the only record of what the brain PUSHED, as opposed to what a session
went and fetched. It matters because a read-trace ground truth inherits the pusher's selection: a
session cannot read a memo the pusher never surfaced, so that memo can never become gold, and the
resulting eval is blind to exactly the associative retrieval it is supposed to measure. On
2026-08-22 the live window held 111 pushes over 14 sessions and 102 distinct memories - and a
live, correctly-registered, hash-bound contract node appeared zero times among them.

Retrospective analysis is therefore impossible by construction rather than by anyone's search
failing. Prospective analysis is possible, but ONLY if something captures the log faster than the
24h prune. This is that something.

Append-only. First-seen timestamp wins, so re-observing a key never rewrites its origin time.
Read-only with respect to the live log - it never writes `.spread_fired.json`.

Usage: python archive_spread_fired.py [--quiet]
"""
import os, io, json, time, sys

DR = os.path.dirname(os.path.abspath(__file__))
LIVE = os.path.join(DR, ".spread_fired.json")
ARCH = os.path.join(DR, "spread_fired_archive.jsonl")
QUIET = "--quiet" in sys.argv


def main():
    try:
        live = json.load(io.open(LIVE, encoding="utf-8"))
    except Exception as e:
        if not QUIET:
            print("live log unreadable: %s" % e)
        return 1

    seen = set()
    if os.path.exists(ARCH):
        for ln in io.open(ARCH, encoding="utf-8", errors="replace"):
            try:
                r = json.loads(ln)
            except Exception:
                continue
            seen.add((r.get("session"), r.get("slug")))

    new = []
    for k, v in live.items():
        if "|" not in k or not isinstance(v, (int, float)):
            continue
        sess, slug = k.split("|", 1)
        if (sess, slug) in seen:
            continue
        new.append({"session": sess, "slug": slug, "fired_at": v,
                    "fired_iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(v)),
                    "archived_at": time.strftime("%Y-%m-%dT%H:%M:%S")})

    if new:
        # append: a truncating open on a history file is the one mistake that cannot be undone
        with io.open(ARCH, "a", encoding="utf-8", newline="\n") as f:
            for r in sorted(new, key=lambda r: r["fired_at"]):
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    if not QUIET:
        total = len(seen) + len(new)
        print("live window %d keys | new %d | archive now %d records -> %s"
              % (len(live), len(new), total, ARCH))
    return 0


if __name__ == "__main__":
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        print(__doc__)
        raise SystemExit(0)
    raise SystemExit(main())
